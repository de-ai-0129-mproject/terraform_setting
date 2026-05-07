"""
구조화된 에러 분류 — 로그의 error_type 필드 표준값.

CloudWatch / OpenSearch에서 error_type 필드로 그루핑/대시보드/알람을 걸기 위한
공통 어휘. 새 에러 타입을 추가할 때는 여기에 먼저 등록하고 사용한다.
"""

from enum import Enum


class ErrorType(str, Enum):
    """Enum이지만 str 상속이라 그대로 JSON 직렬화 가능."""

    # ── 입력 검증 ──
    INVALID_PAYLOAD = "InvalidPayload"            # SQS 메시지 형식 오류 (재시도 의미 없음)
    MISSING_FIELD = "MissingField"                # 필수 필드 누락

    # ── DB ──
    DB_CONNECTION_ERROR = "DBConnectionError"     # 연결 실패 (재시도 가능)
    DB_QUERY_ERROR = "DBQueryError"               # SELECT 실패
    DB_UPDATE_ERROR = "DBUpdateError"             # INSERT/UPDATE 실패
    DB_INTEGRITY_ERROR = "DBIntegrityError"       # 제약 위반 (재시도 의미 없음)

    # ── AWS 서비스 ──
    SECRETS_MANAGER_ERROR = "SecretsManagerError"
    S3_UPLOAD_ERROR = "S3UploadError"
    SQS_PUBLISH_ERROR = "SQSPublishError"

    # ── MMR 계산 ──
    MMR_CALCULATION_ERROR = "MMRCalculationError"

    # ── 기타 ──
    UNKNOWN_ERROR = "UnknownError"


class Stage(str, Enum):
    """처리 단계 — 로그의 stage 필드 표준값. 어디서 터졌는지 빠르게 식별."""

    # outbox_poller
    DB_CONNECT = "db_connect"
    FETCH_OUTBOX = "fetch_outbox"
    SQS_PUBLISH = "sqs_publish"
    MARK_PUBLISHED = "mark_published"

    # mmr_calculator
    PARSE_MESSAGE = "parse_message"
    IDEMPOTENCY_CHECK = "idempotency_check"
    FETCH_MMRS = "fetch_mmrs"
    MMR_CALCULATION = "mmr_calculation"
    S3_UPLOAD = "s3_upload"
    DB_UPDATE = "db_update"
    UNKNOWN = "unknown"


class AppError(Exception):
    """애플리케이션 에러 — error_type/stage/retryable 정보를 함께 운반.

    catch한 쪽에서 logger.error(extra=err.to_log_extra(), exc_info=True)로
    한 번에 구조화 로깅 가능.
    """

    def __init__(
        self,
        message: str,
        error_type: ErrorType,
        stage: Stage,
        retryable: bool = True,
        **extra,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.stage = stage
        self.retryable = retryable
        self.extra = extra

    def to_log_extra(self) -> dict:
        return {
            "error_type": self.error_type.value,
            "stage": self.stage.value,
            "retryable": self.retryable,
            **self.extra,
        }
