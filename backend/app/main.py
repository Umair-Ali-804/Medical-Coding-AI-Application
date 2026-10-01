"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Match

from app.api.routes import auth, claims, documents, suggestions, system, users
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, request_id_var
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS

log = logging.getLogger("app")


def _bootstrap_admin() -> None:
    """Create the first admin from BOOTSTRAP_ADMIN_* env vars if no users exist."""
    s = get_settings()
    if not (s.bootstrap_admin_email and s.bootstrap_admin_password):
        return
    from sqlalchemy import func, select

    from app.core.security import hash_password
    from app.db.session import session_scope
    from app.models import User
    from app.services import audit
    from app.services.principal import SYSTEM

    try:
        with session_scope() as db:
            if db.execute(select(func.count()).select_from(User)).scalar_one() == 0:
                user = User(
                    email=s.bootstrap_admin_email.lower(),
                    full_name="Administrator",
                    role="admin",
                    hashed_password=hash_password(s.bootstrap_admin_password.get_secret_value()),
                )
                db.add(user)
                db.flush()
                audit.record(db, SYSTEM, "user.bootstrap_admin", entity_type="user", entity_id=user.id)
                log.info("bootstrap_admin_created")
    except Exception:  # noqa: BLE001 - DB may not be migrated yet
        log.exception("bootstrap_admin_failed")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    s = get_settings()
    log.info(
        "startup",
        extra={
            "environment": s.environment,
            "llm_provider": s.llm_provider,
            "embedding_provider": s.embedding_provider,
            "vector_store": s.vector_store,
        },
    )
    _bootstrap_admin()
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    app = FastAPI(
        title=s.app_name,
        version=s.pipeline_version,
        description="AI-assisted medical coding: clinical documents -> evidence-backed codes -> validation -> human review.",
        lifespan=lifespan,
        docs_url="/docs" if s.environment != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if s.environment != "production" else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # noqa: ANN001, ANN202
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid[:64])
        started = time.perf_counter()
        route = request.url.path
        for r in request.app.router.routes:
            if r.matches(request.scope)[0] == Match.FULL:
                route = getattr(r, "path", route)
                break
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            elapsed = time.perf_counter() - started
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(elapsed)
            if not route.startswith("/health") and route != "/metrics":
                log.info(
                    "request",
                    extra={
                        "method": request.method,
                        "route": route,
                        "status": status,
                        "ms": round(elapsed * 1000, 1),
                    },
                )
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"  # responses may contain PHI
        if s.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    install_error_handlers(app)
    for r in (auth.router, users.router, documents.router, suggestions.router, system.router, claims.router):
        app.include_router(r, prefix=s.api_prefix)
    # unauthenticated probes at the root too (for orchestrators)
    app.add_api_route("/health/live", system.live, include_in_schema=False)
    app.add_api_route("/health/ready", system.ready, include_in_schema=False)
    app.add_api_route("/metrics", system.metrics, include_in_schema=False)
    return app


app = create_app()
