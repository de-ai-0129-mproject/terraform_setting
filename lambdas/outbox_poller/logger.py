"""
구조화된 JSON 로거 — Lambda → CloudWatch Logs → OpenSearch 파이프라인용.

CloudWatch Logs에 한 줄짜리 JSON으로 찍으면, Subscription Filter나
OpenSearch Ingestion이 필드별로 자동 파싱해서 Discover/Dashboards에서
필터링/집계 가능.

사용:
    from logger import get_logger, set_context, reset_context

    logger = get_logger(__name__)

    def lambda_handler(event, context):
        reset_context()
        set_context(request_id=context.aws_request_id)

        logger.info("started", extra={"event": "INVOCATION_STARTED"})

        try:
            ...
        except RetryableError as e:
            logger.error(
                "Riot API request timed out",
                extra={
                    "event": "MMR_CALCULATION_FAILED",
                    "error_type": "RiotApiTimeout",
                    "retryable": True,
                    "stage": "fetch_match_history",
                },
                exc_info=True,
            )
            raise
"""

import json
import logging
import os
import sys
import threading
import traceback
from datetime import datetime, timezone

# ─── 서비스 메타 (모든 로그에 자동 첨부) ───
SERVICE_NAME = os.environ.get("SERVICE_NAME", "unknown-service")
FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "unknown-function")
REGION = os.environ.get("AWS_REGION", "unknown-region")

# ─── thread-local 컨텍스트 ───
# Lambda는 컨테이너 재사용 시 동일 프로세스에서 여러 invocation을 처리할 수 있으므로
# 매 invocation 시작 시 reset_context() 필수.
_ctx = threading.local()


def reset_context() -> None:
    """invocation 시작 시 호출. 이전 invocation 컨텍스트를 비운다."""
    _ctx.data = {}


def set_context(**kwargs) -> None:
    """현재 invocation의 공통 컨텍스트를 추가/갱신.

    예: set_context(request_id="...", sqs_message_id="...", custom_match_id="...")
    None인 값은 무시.
    """
    if not hasattr(_ctx, "data"):
        _ctx.data = {}
    _ctx.data.update({k: v for k, v in kwargs.items() if v is not None})


def get_context() -> dict:
    return dict(getattr(_ctx, "data", {}) or {})


# ─── LogRecord의 표준 속성들 (extra로 들어온 사용자 필드와 구분하기 위함) ───
_RESERVED_LOGRECORD_ATTRS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
}


class JsonFormatter(logging.Formatter):
    """LogRecord → JSON 한 줄로 변환."""

    def format(self, record: logging.LogRecord) -> str:
        # 기본 필드
        out = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "function": FUNCTION_NAME,
            "region": REGION,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 컨텍스트 (request_id, sqs_message_id, custom_match_id 등)
        out.update(get_context())

        # extra=로 던진 사용자 필드 (event, error_type, stage, retryable, ...)
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_ATTRS or key.startswith("_"):
                continue
            # 컨텍스트와 키 충돌 시 extra가 이김 (호출부 명시 우선)
            out[key] = value

        # 예외 정보
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            out["exception"] = {
                "type": exc_type.__name__ if exc_type else None,
                "message": str(exc_value) if exc_value else None,
                "stacktrace": "".join(
                    traceback.format_exception(exc_type, exc_value, exc_tb)
                ),
            }

        # JSON 직렬화 — datetime 등 비표준 타입은 str()로 fallback
        return json.dumps(out, ensure_ascii=False, default=str)


_initialized = False


def _init_root_logger() -> None:
    """루트 로거에 JSON 핸들러 1개만 부착. Lambda 기본 핸들러 제거."""
    global _initialized
    if _initialized:
        return

    root = logging.getLogger()
    # Lambda 런타임이 자동 부착하는 핸들러 제거 (그대로 두면 같은 메시지 2번 찍힘)
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

    _initialized = True


def get_logger(name: str = None) -> logging.Logger:
    """모듈에서 import해서 쓰는 로거. 첫 호출 시 루트 로거 초기화."""
    _init_root_logger()
    return logging.getLogger(name)
