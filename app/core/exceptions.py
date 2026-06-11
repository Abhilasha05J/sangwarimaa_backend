from fastapi import Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: dict = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class UnauthorizedException(AppException):
    def __init__(self, message: str = "Authentication required"):
        super().__init__("UNAUTHORIZED", message, 401)


class ForbiddenException(AppException):
    def __init__(self, message: str = "Access denied"):
        super().__init__("FORBIDDEN", message, 403)


class NotFoundException(AppException):
    def __init__(self, resource: str = "Resource"):
        super().__init__(f"{resource.upper()}_NOT_FOUND", f"{resource} not found", 404)


class ConflictException(AppException):
    def __init__(self, message: str):
        super().__init__("CONFLICT", message, 409)


class ValidationException(AppException):
    def __init__(self, message: str, details: dict = None):
        super().__init__("VALIDATION_ERROR", message, 422, details)


class OTPException(AppException):
    def __init__(self, message: str = "Invalid or expired OTP"):
        super().__init__("OTP_INVALID", message, 400)


class RateLimitException(AppException):
    def __init__(self):
        super().__init__("RATE_LIMIT_EXCEEDED", "Too many requests. Please try again later.", 429)


# ── Handlers ─────────────────────────────────────────────────────────────────

def error_envelope(code: str, message: str, details: dict = None) -> dict:
    return {
        "success": False,
        "data": None,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def success_envelope(data, meta: dict = None) -> dict:
    return {"success": True, "data": data, "meta": meta, "error": None}


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(exc.code, exc.message, exc.details),
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_envelope("INTERNAL_ERROR", "An unexpected error occurred."),
    )
