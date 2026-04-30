"""
Outbox Poller Lambda

EventBridge 스케줄로 10초마다 호출됨.
RDS의 match_outbox에서 published=false 행을 가져와 SQS에 발행하고
published=true로 마킹한다.

테스트 옵션 (event payload):
    dry_run: true   → SQS 발행/마킹 안 함, 어떤 메시지가 나갈지만 로그
    limit: N        → 한 번에 N건만 처리 (기본 100)
    match_id: "..." → 특정 매치만 강제 재발행 (published 무시)
"""

import json
import logging
import os

import boto3
import psycopg
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 환경변수
SECRET_ARN = os.environ['SECRET_ARN']
RDS_HOST = os.environ['RDS_HOST']
RDS_DB = os.environ['RDS_DB']
SQS_URL = os.environ['SQS_URL']
DEFAULT_LIMIT = int(os.environ.get('DEFAULT_LIMIT', '100'))

# AWS 클라이언트
secrets_client = boto3.client('secretsmanager')
sqs_client = boto3.client('sqs')

# DB 자격증명 캐시 (Lambda 컨테이너 재사용 시 중복 조회 방지)
_db_credentials = None


def get_db_credentials() -> dict:
  """Secrets Manager에서 DB 사용자명/비밀번호 조회 (캐시)."""
  global _db_credentials
  if _db_credentials is None:
    resp = secrets_client.get_secret_value(SecretId=SECRET_ARN)
    _db_credentials = json.loads(resp['SecretString'])
  return _db_credentials


def get_db_connection():
  """RDS PostgreSQL 연결."""
  creds = get_db_credentials()
  return psycopg.connect(
    host=RDS_HOST,
    dbname=RDS_DB,
    user=creds['username'],
    password=creds['password'],
    connect_timeout=10,
  )


def fetch_unpublished(conn, limit: int, match_id: str = None) -> list[dict]:
  """미발행 outbox 행 조회. match_id 지정 시 published 무시하고 그 행만."""
  with conn.cursor() as cur:
    if match_id:
      cur.execute(
        """
                SELECT id, custom_match_id, payload
                FROM match_outbox
                WHERE custom_match_id = %s
                """,
        (match_id,),
      )
    else:
      cur.execute(
        """
                SELECT id, custom_match_id, payload
                FROM match_outbox
                WHERE published = FALSE
                ORDER BY id
                LIMIT %s
                """,
        (limit,),
      )

    return [
      {
        'id': row[0],
        'custom_match_id': row[1],
        'payload': row[2],  # JSONB는 자동으로 dict로 디시리얼라이즈됨
      }
      for row in cur.fetchall()
    ]


def publish_to_sqs(message: dict) -> str:
  """SQS에 메시지 발행. 메시지 ID 반환."""
  resp = sqs_client.send_message(
    QueueUrl=SQS_URL,
    MessageBody=json.dumps(message, ensure_ascii=False, default=str),
  )
  return resp['MessageId']


def mark_published(conn, outbox_ids: list[int]) -> None:
  """published=true, published_date=NOW() 마킹."""
  if not outbox_ids:
    return
  with conn.cursor() as cur:
    cur.execute(
      """
            UPDATE match_outbox
            SET published = TRUE, published_date = NOW()
            WHERE id = ANY(%s)
            """,
      (outbox_ids,),
    )
  conn.commit()


def lambda_handler(event, context):
  """Lambda 진입점."""
  logger.info(f'Outbox Poller started. event={event}')

  # 옵션 파싱
  dry_run = bool(event.get('dry_run', False))
  limit = int(event.get('limit', DEFAULT_LIMIT))
  match_id = event.get('match_id')

  if dry_run:
    logger.info('DRY RUN mode — SQS 발행/마킹 없음')

  conn = None
  try:
    conn = get_db_connection()

    # 1. 미발행 행 조회
    rows = fetch_unpublished(conn, limit=limit, match_id=match_id)
    logger.info(f'Fetched {len(rows)} unpublished rows')

    if not rows:
      return {'processed': 0, 'dry_run': dry_run}

    # 2. SQS 발행
    published_ids = []
    failed_ids = []

    for row in rows:
      message = {
        'outbox_id': row['id'],
        'custom_match_id': row['custom_match_id'],
        'payload': row['payload'],
      }

      if dry_run:
        logger.info(
          f'[DRY RUN] would send: outbox_id={row["id"]} match={row["custom_match_id"]}'
        )
        continue

      try:
        msg_id = publish_to_sqs(message)
        published_ids.append(row['id'])
        logger.info(
          f'Published outbox_id={row["id"]} match={row["custom_match_id"]} '
          f'sqs_msg={msg_id}'
        )
      except ClientError as e:
        failed_ids.append(row['id'])
        logger.error(f'Failed to publish outbox_id={row["id"]}: {e}')

    # 3. 발행 성공한 것만 마킹 (dry_run이 아닐 때만)
    if not dry_run:
      mark_published(conn, published_ids)

    result = {
      'processed': len(rows),
      'published': len(published_ids),
      'failed': len(failed_ids),
      'dry_run': dry_run,
    }
    logger.info(f'Outbox Poller done. {result}')
    return result

  except Exception as e:
    logger.exception(f'Outbox Poller failed: {e}')
    raise

  finally:
    if conn is not None:
      conn.close()
