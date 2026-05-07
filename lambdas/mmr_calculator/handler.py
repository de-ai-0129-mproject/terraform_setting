"""
MMR Calculator Lambda

SQS에서 매치 메시지 받아서:
1. 멱등성 체크 (processed_matches)
2. 참가자 현재 MMR 조회 (player_mmr)
3. ELO 기반 MMR 계산
4. player_mmr 갱신 + processed_matches INSERT (단일 트랜잭션)
5. S3에 raw payload + 결과 HTML PUT

로깅: 모든 로그는 JSON 한 줄 (logger.py). OpenSearch에서 필드별 검색 가능.
에러: error_type/stage/retryable 표준 어휘 (errors.py).
"""

import json
import os
from datetime import datetime

import boto3
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from champions import get_champion_name
from errors import AppError, ErrorType, Stage
from html_template import render_match_html
from logger import get_logger, reset_context, set_context
from mmr import calculate_match_mmr

logger = get_logger(__name__)

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


def is_already_processed(conn, custom_match_id: str) -> bool:
  """processed_matches에 있으면 중복 처리."""
  try:
    with conn.cursor() as cur:
      cur.execute(
        'SELECT 1 FROM processed_matches WHERE custom_match_id = %s',
        (custom_match_id,),
      )
      return cur.fetchone() is not None
  except psycopg.Error as e:
    raise AppError(
      f'멱등성 체크 쿼리 실패: {e}',
      error_type=ErrorType.DB_QUERY_ERROR,
      stage=Stage.IDEMPOTENCY_CHECK,
      retryable=True,
    ) from e


def fetch_current_mmrs(conn, puuids: list[str], guild_id: str) -> dict[str, int]:
  """길드 내 참가자들의 현재 MMR 조회."""
  if not puuids:
    return {}
  try:
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
  except psycopg.Error as e:
    raise AppError(
      f'현재 MMR 조회 실패: {e}',
      error_type=ErrorType.DB_QUERY_ERROR,
      stage=Stage.FETCH_MMRS,
      retryable=True,
    ) from e


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
  try:
    s3_client.put_object(
      Bucket=S3_BUCKET,
      Key=key,
      Body=body,
      ContentType=content_type,
    )
  except (ClientError, BotoCoreError) as e:
    raise AppError(
      f'S3 업로드 실패 (key={key}): {e}',
      error_type=ErrorType.S3_UPLOAD_ERROR,
      stage=Stage.S3_UPLOAD,
      retryable=True,
      s3_key=key,
    ) from e


def parse_payload(message_body: str) -> tuple[dict, str, str, list]:
  """SQS 메시지 → payload 분해. 형식 오류면 InvalidPayload (DLQ 직행)."""
  try:
    msg = json.loads(message_body)
    payload = msg['payload']
    custom_match_id = payload['custom_match_id']
    guild_id = payload['guild_id']
    participants = payload['participants']
    if not isinstance(participants, list) or not participants:
      raise ValueError('participants가 비어있거나 list가 아님')
    return payload, custom_match_id, guild_id, participants
  except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
    raise AppError(
      f'메시지 파싱 실패: {e}',
      error_type=ErrorType.INVALID_PAYLOAD,
      stage=Stage.PARSE_MESSAGE,
      retryable=False,
    ) from e


def process_match(message_body: str, request_id: str) -> dict:
  """SQS 메시지 1건 처리."""
  # 1. 메시지 파싱 (DLQ 직행 가능)
  payload, custom_match_id, guild_id, participants = parse_payload(message_body)
  set_context(custom_match_id=custom_match_id, guild_id=guild_id)

  logger.info(
    f'Processing match {custom_match_id} ({len(participants)} players)',
    extra={
      'event': 'MMR_CALCULATION_STARTED',
      'stage': Stage.PARSE_MESSAGE.value,
      'player_count': len(participants),
    },
  )

  # 날짜 파티션
  match_date = payload.get('match_date', datetime.utcnow().isoformat())
  dt = datetime.fromisoformat(match_date.replace('Z', '+00:00'))
  date_prefix = f'year={dt.year:04d}/month={dt.month:02d}/day={dt.day:02d}'

  conn = None
  try:
    conn = get_db_connection()

    # 2. 멱등성 체크
    if is_already_processed(conn, custom_match_id):
      logger.info(
        f'Match {custom_match_id} already processed — skipping',
        extra={'event': 'MMR_CALCULATION_SKIPPED', 'reason': 'already_processed'},
      )
      return {'custom_match_id': custom_match_id, 'status': 'skipped'}

    # 3. 현재 MMR 조회
    puuids = [p['puuid'] for p in participants]
    current_mmrs = fetch_current_mmrs(conn, puuids, guild_id)
    logger.info(
      f'Found existing MMRs for {len(current_mmrs)}/{len(puuids)} players',
      extra={
        'event': 'MMR_FETCHED',
        'stage': Stage.FETCH_MMRS.value,
        'existing_mmr_count': len(current_mmrs),
        'total_players': len(puuids),
      },
    )

    # 4. MMR 계산
    try:
      mmr_results = calculate_match_mmr(participants, current_mmrs)
    except Exception as e:
      raise AppError(
        f'MMR 계산 실패: {e}',
        error_type=ErrorType.MMR_CALCULATION_ERROR,
        stage=Stage.MMR_CALCULATION,
        retryable=False,  # 계산 로직 문제일 가능성 → DLQ로 보내고 사람이 봐야 함
      ) from e

    # 5. S3에 raw + HTML PUT (DB 갱신 전)
    raw_key = f'matches/{date_prefix}/{custom_match_id}.json'
    upload_to_s3(
      raw_key,
      json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'),
      'application/json',
    )

    html = render_match_html(payload, mmr_results, get_champion_name)
    html_key = f'results/{date_prefix}/{custom_match_id}.html'
    upload_to_s3(html_key, html.encode('utf-8'), 'text/html; charset=utf-8')

    # 6. DB 갱신 (단일 트랜잭션)
    try:
      win_puuids = {p['puuid'] for p in participants if p['game_result'] == '승'}
      upsert_mmrs(conn, guild_id, custom_match_id, mmr_results, win_puuids)
      insert_mmr_history(conn, custom_match_id, guild_id, mmr_results, participants)
      mark_processed(conn, custom_match_id, request_id, html_key)
      conn.commit()
    except psycopg.IntegrityError as e:
      raise AppError(
        f'DB 제약 위반: {e}',
        error_type=ErrorType.DB_INTEGRITY_ERROR,
        stage=Stage.DB_UPDATE,
        retryable=False,
      ) from e
    except psycopg.Error as e:
      raise AppError(
        f'DB UPDATE 실패: {e}',
        error_type=ErrorType.DB_UPDATE_ERROR,
        stage=Stage.DB_UPDATE,
        retryable=True,
      ) from e

    logger.info(
      f'Match {custom_match_id} processed. S3: {html_key}. '
      f'Players updated: {len(mmr_results)}',
      extra={
        'event': 'MMR_CALCULATION_SUCCEEDED',
        'players_updated': len(mmr_results),
        's3_html_key': html_key,
      },
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
  """SQS 트리거. BatchSize=1 권장이지만 partial batch failure도 지원."""
  reset_context()
  request_id = context.aws_request_id if context else 'local-test'
  set_context(request_id=request_id)

  records = event.get('Records', [])
  logger.info(
    f'MMR Calculator started. records={len(records)}',
    extra={'event': 'INVOCATION_STARTED', 'record_count': len(records)},
  )

  failures = []

  for record in records:
    message_id = record['messageId']
    receive_count = int(
      record.get('attributes', {}).get('ApproximateReceiveCount', 0)
    )
    body = record['body']

    # invocation 컨텍스트에 SQS 메시지 정보 추가
    set_context(sqs_message_id=message_id, receive_count=receive_count)

    try:
      process_match(body, request_id)
    except AppError as e:
      logger.error(
        str(e),
        extra={
          'event': 'MMR_CALCULATION_FAILED',
          **e.to_log_extra(),
        },
        exc_info=True,
      )
      failures.append({'itemIdentifier': message_id})
    except Exception as e:
      logger.error(
        f'예상치 못한 오류: {e}',
        extra={
          'event': 'MMR_CALCULATION_FAILED',
          'error_type': ErrorType.UNKNOWN_ERROR.value,
          'stage': Stage.UNKNOWN.value,
          'retryable': True,
        },
        exc_info=True,
      )
      failures.append({'itemIdentifier': message_id})
    finally:
      # 다음 레코드를 위해 메시지별 컨텍스트 정리
      # request_id는 invocation 단위라 유지
      set_context(custom_match_id=None, guild_id=None)
      reset_context()
      set_context(request_id=request_id)

  if failures:
    logger.warning(
      f'{len(failures)}/{len(records)} messages failed',
      extra={
        'event': 'INVOCATION_PARTIAL_FAILURE',
        'failure_count': len(failures),
        'total_count': len(records),
      },
    )
    return {'batchItemFailures': failures}

  logger.info(
    'All records processed successfully',
    extra={'event': 'INVOCATION_SUCCEEDED', 'record_count': len(records)},
  )
  return {'status': 'ok'}