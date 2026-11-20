"""FastAPI application: the three contract endpoints plus hardened error handling."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from anyio import to_thread
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .chat_service import ChatService
from .config import Settings
from .db import Database
from .llm.gateway import AllProvidersUnavailable, CompletionFn, LLMGateway
from .llm.key_pool import KeyPool
from .llm.simulation import NO_FAILURES, parse_failure_header
from .schemas import ChatRequest, ChatResponse, HistoryResponse

logger = logging.getLogger("lyftr")


def create_app(settings: Settings | None = None, completion_fn: CompletionFn | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if len(settings.api_keys) < 2:
        logger.warning("LLM_API_KEYS has %d key(s); failover needs at least 2", len(settings.api_keys))

    db = Database(settings.database_path)
    pool = KeyPool(
        settings.api_keys,
        settings.model_for_key,
        rate_limit_cooldown=settings.rate_limit_cooldown_seconds,
        auth_cooldown=settings.auth_error_cooldown_seconds,
    )
    gateway = LLMGateway(settings, pool, completion_fn=completion_fn)
    chat = ChatService(settings, db, gateway)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await to_thread.run_sync(db.init_schema)  # schema is created automatically on startup
        logger.info(
            "ready: keys=%d models=%s cache=%s(%s) simulation=%s db=%s",
            len(pool),
            pool.distinct_models(),
            gateway.cache_enabled,
            settings.cache_type,
            settings.enable_failure_simulation,
            settings.database_path,
        )
        yield

    app = FastAPI(title="Lyftr LLM Chat API", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.db, app.state.pool, app.state.gateway = settings, db, pool, gateway

    allow_all = settings.allowed_origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=not allow_all,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Simulate-Failure", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Server-Timing"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.started = time.perf_counter()
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        response = await call_next(request)
        elapsed = (time.perf_counter() - request.state.started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={elapsed:.1f}"
        logger.info(
            "%s %s -> %d in %.0fms [%s]", request.method, request.url.path, response.status_code, elapsed, request_id
        )
        return response

    # ---------------------------------------------------------------- errors
    # Every error is JSON with a `detail` field: no stack traces, no HTML pages.
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        errors = [{k: v for k, v in err.items() if k in ("type", "loc", "msg")} for err in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(AllProvidersUnavailable)
    async def providers_down(_: Request, exc: AllProvidersUnavailable):
        return JSONResponse(
            {"detail": "all providers unavailable", "attempts": [a.as_dict() for a in exc.attempts]},
            status_code=503,
        )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        logger.exception("unhandled error: %s", type(exc).__name__)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    # ------------------------------------------------------------- endpoints
    @app.get("/health")
    async def health() -> JSONResponse:
        db_ok = await to_thread.run_sync(db.ping)
        body: dict[str, Any] = {
            "status": "ok" if db_ok and len(pool) > 0 else "unavailable",
            "keys_configured": len(pool),
            "cache_enabled": gateway.cache_enabled,
            # extras (allowed by the contract)
            "cache_type": settings.cache_type if gateway.cache_enabled else None,
            "database": "ok" if db_ok else "unreachable",
            "failure_simulation_enabled": settings.enable_failure_simulation,
            "models": pool.distinct_models(),
            "keys": pool.snapshot(),
            "version": __version__,
        }
        if body["status"] != "ok":
            body["detail"] = "database unreachable" if not db_ok else "no API keys configured"
            return JSONResponse(body, status_code=503)
        return JSONResponse(body)

    @app.post("/chat", response_model=ChatResponse)
    async def post_chat(
        payload: ChatRequest,
        request: Request,
        x_simulate_failure: str | None = Header(default=None),
    ) -> dict[str, Any]:
        plan = NO_FAILURES
        if x_simulate_failure:
            if settings.enable_failure_simulation:
                plan = parse_failure_header(x_simulate_failure)
                if plan.ignored:
                    logger.warning("ignored malformed X-Simulate-Failure entries: %s", plan.ignored)
            else:
                logger.info("X-Simulate-Failure ignored (ENABLE_FAILURE_SIMULATION is off)")
        return await chat.handle(payload, plan, request.state.started)

    @app.get("/sessions/{session_id}/messages", response_model=HistoryResponse, response_model_exclude_none=True)
    async def get_messages(session_id: str) -> dict[str, Any]:
        session = await to_thread.run_sync(db.get_session, session_id)
        if session is None:
            raise StarletteHTTPException(status_code=404, detail="session not found")
        history = await to_thread.run_sync(db.get_history, session_id, None)
        return {"session_id": session_id, "messages": history}

    return app
