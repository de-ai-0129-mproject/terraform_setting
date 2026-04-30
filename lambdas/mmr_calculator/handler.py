"""
MMR Calculator Lambda

SQS에서 매치 메시지 받아서:
1. 멱등성 체크 (processed_matches)
2. 참가자 현재 MMR 조회 (player_mmr)
3. ELO 기반 MMR 계산
4. player_mmr 갱신 + processed_matches INSERT (단일 트랜잭션)
5. S3에 raw payload + 결과 HTML PUT
"""

import json
import logging
import os
from datetime import datetime

import boto3
import psycopg
from champions import get_champion_name
from html_template import render_match_html
from mmr import calculate_match_mmr

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 환경변수
SECRET_ARN = os.environ['SECRET_ARN']
RDS_HOST = os.environ['RDS_HOST']
RDS_DB = os.environ['RDS_DB']
S3_BUCKET = os.environ['S3_BUCKET']

# AWS 클라이언트
secrets_client = boto3.client('secretsmanager')
s3_client = boto3.client('s3')

# DB 자격증명 캐시
_db_credentials = None


def get_db_credentials() -> dict:
  global _db_credentials
  if _db_credentials is None:
    resp = secrets_client.get_secret_value(SecretId=SECRET_ARN)
    _db_credentials = json.loads(resp['SecretString'])
  return _db_credentials


def get_db_connection():
  creds = get_db_credentials()
  return psycopg.connect(
    host=RDS_HOST,
    dbname=RDS_DB,
    user=creds['username'],
    password=creds['password'],
    connect_timeout=10,
  )


def is_already_processed(conn, custom_match_id: str) -> bool:
  """processed_matches에 있으면 중복 처리."""
  with conn.cursor() as cur:
    cur.execute(
      'SELECT 1 FROM processed_matches WHERE custom_match_id = %s',
      (custom_match_id,),
    )
    return cur.fetchone() is not None


def fetch_current_mmrs(conn, puuids: list[str], guild_id: str) -> dict[str, int]:
  """길드 내 참가자들의 현재 MMR 조회."""
  if not puuids:
    return {}
  with conn.cursor() as cur:
    cur.execute(
      """
            SELECT puuid, mmr 
            FROM player_mmr 
            WHERE puuid = ANY(%s) AND guild_id = %s AND is_deleted = FALSE
            """,
      (puuids, guild_id),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def upsert_mmrs(
  conn,
  guild_id: str,
  custom_match_id: str,
  mmr_results: list[dict],
  win_puuids: set[str],
) -> None:
  """player_mmr UPSERT."""
  with conn.cursor() as cur:
    for r in mmr_results:
      is_win = r['puuid'] in win_puuids
      cur.execute(
        """
                INSERT INTO player_mmr (
                    puuid, guild_id, mmr, games_played, wins, losses, last_match_id
                ) VALUES (%s, %s, %s, 1, %s, %s, %s)
                ON CONFLICT (puuid, guild_id) DO UPDATE SET
                    mmr = EXCLUDED.mmr,
                    games_played = player_mmr.games_played + 1,
                    wins = player_mmr.wins + EXCLUDED.wins,
                    losses = player_mmr.losses + EXCLUDED.losses,
                    last_match_id = EXCLUDED.last_match_id,
                    update_date = NOW()
                """,
        (
          r['puuid'],
          guild_id,
          r['post_mmr'],
          1 if is_win else 0,
          0 if is_win else 1,
          custom_match_id,
        ),
      )


def insert_mmr_history(
  conn,
  custom_match_id: str,
  guild_id: str,
  mmr_results: list[dict],
  participants: list[dict],
) -> None:
  """매치별 MMR 변동 이력 INSERT."""
  # puuid → participant 매핑
  p_by_puuid = {p['puuid']: p for p in participants}

  with conn.cursor() as cur:
    for r in mmr_results:
      p = p_by_puuid.get(r['puuid'])
      if not p:
        continue
      cur.execute(
        """
                INSERT INTO mmr_history (
                    custom_match_id, puuid, guild_id,
                    pre_mmr, post_mmr, delta,
                    game_result, position
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (custom_match_id, puuid) DO NOTHING
                """,
        (
          custom_match_id,
          r['puuid'],
          guild_id,
          r['pre_mmr'],
          r['post_mmr'],
          r['delta'],
          p['game_result'],
          p['position'],
        ),
      )


def mark_processed(
  conn,
  custom_match_id: str,
  request_id: str,
  s3_key: str,
) -> None:
  """processed_matches INSERT."""
  with conn.cursor() as cur:
    cur.execute(
      """
            INSERT INTO processed_matches (custom_match_id, lambda_request_id, s3_result_key)
            VALUES (%s, %s, %s)
            ON CONFLICT (custom_match_id) DO NOTHING
            """,
      (custom_match_id, request_id, s3_key),
    )


def upload_to_s3(key: str, body: bytes, content_type: str) -> None:
  """S3 PUT."""
  s3_client.put_object(
    Bucket=S3_BUCKET,
    Key=key,
    Body=body,
    ContentType=content_type,
  )


def process_match(message_body: str, request_id: str) -> dict:
  """SQS 메시지 1건 처리."""
  msg = json.loads(message_body)
  payload = msg['payload']
  custom_match_id = payload['custom_match_id']
  guild_id = payload['guild_id']
  participants = payload['participants']

  logger.info(f'Processing match {custom_match_id} ({len(participants)} players)')

  # 날짜 파티션 생성
  match_date = payload.get('match_date', datetime.utcnow().isoformat())
  dt = datetime.fromisoformat(match_date.replace('Z', '+00:00'))
  date_prefix = f'year={dt.year:04d}/month={dt.month:02d}/day={dt.day:02d}'

  conn = None
  try:
    conn = get_db_connection()

    # 1. 멱등성 체크
    if is_already_processed(conn, custom_match_id):
      logger.info(f'Match {custom_match_id} already processed — skipping')
      return {'custom_match_id': custom_match_id, 'status': 'skipped'}

    # 2. 현재 MMR 조회
    puuids = [p['puuid'] for p in participants]
    current_mmrs = fetch_current_mmrs(conn, puuids, guild_id)
    logger.info(f'Found existing MMRs for {len(current_mmrs)}/{len(puuids)} players')

    # 3. MMR 계산
    mmr_results = calculate_match_mmr(participants, current_mmrs)

    # 4. S3에 raw + HTML PUT (DB 갱신 전에 함, 실패 시 롤백 가능)
    raw_key = f'matches/{date_prefix}/{custom_match_id}.json'
    upload_to_s3(
      raw_key,
      json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'),
      'application/json',
    )

    html = render_match_html(payload, mmr_results, get_champion_name)
    html_key = f'results/{date_prefix}/{custom_match_id}.html'
    upload_to_s3(html_key, html.encode('utf-8'), 'text/html; charset=utf-8')

    # 5. DB 갱신 (단일 트랜잭션)
    win_puuids = {p['puuid'] for p in participants if p['game_result'] == '승'}
    upsert_mmrs(conn, guild_id, custom_match_id, mmr_results, win_puuids)
    insert_mmr_history(conn, custom_match_id, guild_id, mmr_results, participants)
    mark_processed(conn, custom_match_id, request_id, html_key)
    conn.commit()

    logger.info(
      f'Match {custom_match_id} processed. '
      f'S3: {html_key}. Players updated: {len(mmr_results)}'
    )
    return {
      'custom_match_id': custom_match_id,
      'status': 'ok',
      'players_updated': len(mmr_results),
      's3_html': html_key,
    }

  except Exception:
    if conn is not None:
      conn.rollback()
    raise
  finally:
    if conn is not None:
      conn.close()


def lambda_handler(event, context):
  """SQS 트리거. BatchSize=1이라 Records는 항상 1개."""
  logger.info(f'MMR Calculator started. records={len(event.get("Records", []))}')

  request_id = context.aws_request_id if context else 'local-test'
  failures = []

  for record in event.get('Records', []):
    message_id = record['messageId']
    body = record['body']

    try:
      process_match(body, request_id)
    except Exception as e:
      logger.exception(f'Failed to process message {message_id}: {e}')
      # SQS partial batch failure 응답
      failures.append({'itemIdentifier': message_id})

  if failures:
    return {'batchItemFailures': failures}
  return {'status': 'ok'}
