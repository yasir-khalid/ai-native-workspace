from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .authz.fga import FGAError
from .errors import Problem, problem_handler
from .routes import access, admin, calls, connectors, identity, internal, threads, workspace
from .services import Services
from .settings import Settings, get_settings


class RedactingFilter(logging.Filter):
    """Belt-and-braces: drop log records that look like they carry content."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.msg)
        return "PRIVATE" not in msg and "body=" not in msg


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    svc = Services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await svc.open()
        n = await svc.runs.recover_orphans()
        if n:
            logging.getLogger("podos").warning("marked %d interrupted interactive runs as failed", n)
        yield
        await svc.close()

    app = FastAPI(title="PodOS Workspace API", version="0.1.0", lifespan=lifespan)
    app.state.services = svc
    for h in logging.getLogger().handlers:
        h.addFilter(RedactingFilter())

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.web_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Active-Pod", "Last-Event-ID", "Idempotency-Key", "X-Request-ID", "Authorization"],
    )

    @app.middleware("http")
    async def request_ids(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        tp = request.headers.get("traceparent", "")
        request.state.trace_id = tp.split("-")[1] if tp.count("-") >= 3 else uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    app.add_exception_handler(Problem, problem_handler)

    @app.exception_handler(FGAError)
    async def authz_unavailable(request: Request, exc: FGAError):
        # Fail closed: if we can't ask OpenFGA, nothing is allowed.
        logging.getLogger("podos.authz").error("authorization service error: %s", exc)
        return JSONResponse({"type": "https://podos.example/problems/authorization_unavailable",
                             "title": "authorization unavailable", "status": 503, "code": "authorization_unavailable"},
                            status_code=503, media_type="application/problem+json")

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()][:10]
        return JSONResponse({"type": "https://podos.example/problems/validation", "title": "validation error",
                             "status": 422, "code": "validation_error", "errors": errors}, status_code=422,
                            media_type="application/problem+json")

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    for r in (identity.router, threads.router, workspace.router, calls.router, internal.router, admin.router, access.router, connectors.router):
        app.include_router(r)
    return app
