from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    """HTTP error with a stable machine-readable `code` for the frontend."""

    def __init__(self, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(status_code, detail={"code": code, "message": message, **extra})


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def lease_lost(message: str = "You no longer hold the lease on this image") -> ApiError:
    return ApiError(409, "lease_lost", message)


def forbidden(code: str, message: str) -> ApiError:
    return ApiError(403, code, message)
