"""業務錯誤。入口層把 AppError 轉成 {code, message, details} 的 JSON（開發文件 9 節錯誤碼表）。"""
from typing import Any


class AppError(Exception):
    status = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, details: list[dict[str, Any]] | None = None, code: str | None = None,
                 status: int | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or []
        if code:
            self.code = code
        if status:
            self.status = status

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return body


class Unauthenticated(AppError):
    status, code = 401, "UNAUTHENTICATED"


class Forbidden(AppError):
    status, code = 403, "FORBIDDEN"


class NotFound(AppError):
    status, code = 404, "NOT_FOUND"


class VersionConflict(AppError):
    status, code = 409, "VERSION_CONFLICT"


class PeriodClosed(AppError):
    status, code = 409, "PERIOD_CLOSED"


class DuplicateInvoice(AppError):
    status, code = 409, "DUPLICATE_INVOICE"


class DuplicateSource(AppError):
    status, code = 409, "DUPLICATE_SOURCE"


class HasSettlements(AppError):
    status, code = 409, "HAS_SETTLEMENTS"


class IdempotencyConflict(AppError):
    status, code = 409, "IDEMPOTENCY_CONFLICT"


class ValidationFailed(AppError):
    status, code = 422, "VALIDATION_FAILED"


class AmountMismatch(AppError):
    status, code = 422, "AMOUNT_MISMATCH"


class ChecklistIncomplete(AppError):
    status, code = 422, "CHECKLIST_INCOMPLETE"


class ExceedsAdvance(AppError):
    status, code = 422, "EXCEEDS_ADVANCE"


class AssetLifeRequired(AppError):
    status, code = 422, "ASSET_LIFE_REQUIRED"


class HasErrors(AppError):
    status, code = 422, "HAS_ERRORS"


class UpstreamUnavailable(AppError):
    status, code = 503, "UPSTREAM_UNAVAILABLE"
