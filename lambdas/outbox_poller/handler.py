"""
Outbox Poller Lambda

EventBridge 스케줄로 주기적 호출. RDS의 match_outbox에서 published=false 행을
읽어 SQS에 발행하고 published=true 마킹.

테스트 옵션 (event payload):
    dry_run: true   → SQS 발행/마킹 없이 어떤 메시지가 나갈지만 로그
    limit: N        → 한 번에 N건만 처리 (기본 100)
    match_id: "..." → 특정 매치만 강제 재발행 (published 무시)

로깅: JSON 한 줄 (logger.py). OpenSearch에서 필드별 검색 가능.
"""

import json
import os

import boto3
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from errors import AppError, ErrorType, Stage
from logger import get_logger, reset_context, set_context

logger = get_logger(__name__)

# 환경변수
SECRET_ARN = os.environ['SECRET_ARN']
RDS_HOST = os.environ['RDS_HOST']
RDS_DB = os.environ['RDS_DB']
SQS_URL = os.environ['SQS_URL']
DEFAULT_LIMIT = int(os.environ.get('DEFAULT_LIMIT', '100'))

# AWS 클라이언트
secrets_client = boto3.client('secretsmanager')
sqs_client = boto3.client('sqs')

# DB 자격증명 캐시
_db_credentials = None


def get_db_credentials() -> dict:
  global _db_credentials
  if _db_credentials is None:
    try:
      resp = secrets_client.get_secret_value(SecretId=SECRET_ARN)
      _db_credentials = json.loads(resp['SecretString'])
    except (ClientError, BotoCoreError) as e:
      raise AppError(
        f'Secrets Manager 조회 실패: {e}',
        error_type=ErrorType.SECRETS_MANAGER_ERROR,
        stage=Stage.DB_CONNECT,
        retryable=True,
      ) from e
  return _db_credentials


def get_db_connection():
  creds = get_db_credentials()
  try:
    return psycopg.connect(
      host=RDS_HOST,
      dbname=RDS_DB,
      user=creds['username'],
      password=creds['password'],
      connect_timeout=10,
    )
  except psycopg.OperationalError as e:
    raise AppError(
      f'DB 연결 실패: {e}',
      error_type=ErrorType.DB_CONNECTION_ERROR,
      stage=Stage.DB_CONNECT,
      retryable=True,
    ) from e


def fetch_unpublished(conn, limit: int, match_id: str = None) -> list[dict]:
  """미발행 outbox 행 조회. match_id 지정 시 published 무시하고 그 행만."""
  try:
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
        {'id': row[0], 'custom_match_id': row[1], 'payload': row[2]}
        for row in cur.fetchall()
      ]
  except psycopg.Error as e:
    raise AppError(
      f'Outbox 조회 실패: {e}',
      error_type=ErrorType.DB_QUERY_ERROR,
      stage=Stage.FETCH_OUTBOX,
      retryable=True,
    ) from e


def publish_to_sqs(message: dict) -> str:
  """SQS에 메시지 발행. 메시지 ID 반환. 실패 시 AppError."""
  try:
    resp = sqs_client.send_message(
      QueueUrl=SQS_URL,
      MessageBody=json.dumps(message, ensure_ascii=False, default=str),
    )
    return resp['MessageId']
  except (ClientError, BotoCoreError) as e:
    raise AppError(
      f'SQS 발행 실패: {e}',
      error_type=ErrorType.SQS_PUBLISH_ERROR,
      stage=Stage.SQS_PUBLISH,
      retryable=True,
    ) from e


def mark_published(conn, outbox_ids: list[int]) -> None:
  if not outbox_ids:
    return
  try:
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
  except psycopg.Error as e:
    raise AppError(
      f'published 마킹 실패: {e}',
      error_type=ErrorType.DB_UPDATE_ERROR,
      stage=Stage.MARK_PUBLISHED,
      retryable=True,
    ) from e


def lambda_handler(event, context):
  reset_context()
  request_id = context.aws_request_id if context else 'local-test'
  set_context(request_id=request_id)

  dry_run = bool(event.get('dry_run', False))
  limit = int(event.get('limit', DEFAULT_LIMIT))
  match_id = event.get('match_id')

  logger.info(
    f'Outbox Poller started. dry_run={dry_run} limit={limit} match_id={match_id}',
    extra={
      'event': 'INVOCATION_STARTED',
      'dry_run': dry_run,
      'limit': limit,
      'match_id_filter': match_id,
    },
  )

  conn = None
  try:
    conn = get_db_connection()

    rows = fetch_unpublished(conn, limit=limit, match_id=match_id)
    logger.info(
      f'Fetched {len(rows)} unpublished rows',
      extra={
        'event': 'OUTBOX_FETCHED',
        'stage': Stage.FETCH_OUTBOX.value,
        'fetched_count': len(rows),
      },
    )

    if not rows:
      logger.info(
        'No unpublished rows',
        extra={'event': 'INVOCATION_SUCCEEDED', 'processed': 0},
      )
      return {'processed': 0, 'dry_run': dry_run}

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
          f'[DRY RUN] would send outbox_id={row["id"]} match={row["custom_match_id"]}',
          extra={
            'event': 'SQS_PUBLISH_DRY_RUN',
            'outbox_id': row['id'],
            'custom_match_id': row['custom_match_id'],
          },
        )
        continue

      try:
        msg_id = publish_to_sqs(message)
        published_ids.append(row['id'])
        logger.info(
          f'Published outbox_id={row["id"]} match={row["custom_match_id"]} sqs_msg={msg_id}',
          extra={
            'event': 'SQS_PUBLISH_SUCCEEDED',
            'outbox_id': row['id'],
            'custom_match_id': row['custom_match_id'],
            'sqs_message_id': msg_id,
          },
        )
      except AppError as e:
        failed_ids.append(row['id'])
        logger.error(
          str(e),
          extra={
            'event': 'SQS_PUBLISH_FAILED',
            'outbox_id': row['id'],
            'custom_match_id': row['custom_match_id'],
            **e.to_log_extra(),
          },
          exc_info=True,
        )

    if not dry_run:
      mark_published(conn, published_ids)

    result = {
      'processed': len(rows),
      'published': len(published_ids),
      'failed': len(failed_ids),
      'dry_run': dry_run,
    }
    logger.info(
      f'Outbox Poller done. {result}',
      extra={'event': 'INVOCATION_SUCCEEDED', **result},
    )
    return result

  except AppError as e:
    logger.error(
      str(e),
      extra={'event': 'INVOCATION_FAILED', **e.to_log_extra()},
      exc_info=True,
    )
    raise
  except Exception as e:
    logger.error(
      f'예상치 못한 오류: {e}',
      extra={
        'event': 'INVOCATION_FAILED',
        'error_type': ErrorType.UNKNOWN_ERROR.value,
        'stage': Stage.UNKNOWN.value,
        'retryable': True,
      },
      exc_info=True,
    )
    raise

  finally:
    if conn is not None:
      conn.close()