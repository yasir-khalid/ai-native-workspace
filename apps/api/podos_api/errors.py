"""RFC 9457 problem-details errors. Messages are generic: they never echo
resource contents or confirm existence of resources in another pod."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class Problem(Exception):
    def __init__(self, status: int, code: str, detail: str = "", **extra: Any) -> None:
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail
        self.extra = extra


def not_found(kind: str = "resource") -> Problem:
    # Identical for "does not exist" and "exists in another pod": no existence oracle.
    return Problem(404, "not_found", f"{kind} not found")


def forbidden(code: str, detail: str = "", **extra: Any) -> Problem:
    return Problem(403, code, detail or "not permitted", **extra)


def conflict(code: str, detail: str = "", **extra: Any) -> Problem:
    return Problem(409, code, detail, **extra)


async def problem_handler(request: Request, exc: Problem) -> JSONResponse:
    body = {
        "type": f"https://podos.example/problems/{exc.code}",
        "title": exc.code.replace("_", " "),
        "status": exc.status,
        "code": exc.code,
        "detail": exc.detail,
        "request_id": getattr(request.state, "request_id", None),
        **exc.extra,
    }
    return JSONResponse(body, status_code=exc.status, media_type="application/problem+json")
