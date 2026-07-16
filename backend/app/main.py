"""
FastAPI app entry point.

In the platform deployment, this single process serves BOTH:
  - the JSON API at /api/v1/*
  - the static React frontend (built by Dockerfile stage 1) at /
  - the deep healthcheck at /health (for the platform proxy / UptimeRobot)

There is no Caddy / nginx in front of this anymore — the platform proxy
handles TLS + auth and forwards directly to us on $PORT.
"""
import os
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.database import AsyncSessionLocal
from app.routers import (
    auth, leads, assessments, briefings, feedback, overrides, portfolio, health,
)

# Fails CLOSED, mirroring the ENV check in app.services.auth (SECURITY_AUDIT.md
# F1): docs/redoc are only exposed when ENV is explicitly "dev". An unset or
# misconfigured ENV var must never leave API docs exposed in production.
_is_dev = os.getenv("ENV", "prod").lower() == "dev"

app = FastAPI(
    title="Raed Ventures Deal Flow",
    version="2.0.0",
    docs_url="/api/docs" if _is_dev else None,
    redoc_url="/api/redoc" if _is_dev else None,
)

# CORS — the platform proxy and frontend are same-origin in production, so
# CORS is only a concern for local dev (Vite at :5173 → FastAPI at :3000).
_allowed_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:5174,http://localhost:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Standard response-hardening headers (SECURITY_AUDIT.md F8). The platform
    proxy terminates TLS in front of us, but these cost nothing to set here too
    and cap the blast radius of a future stored-XSS / clickjacking vector."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'"
    )
    return response

# ----- API routes -----
API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(leads.router, prefix=API_PREFIX)
app.include_router(assessments.router, prefix=API_PREFIX)
app.include_router(briefings.router, prefix=API_PREFIX)
app.include_router(feedback.router, prefix=API_PREFIX)
app.include_router(overrides.router, prefix=API_PREFIX)
app.include_router(portfolio.router, prefix=API_PREFIX)
app.include_router(health.router, prefix=API_PREFIX)


@app.get("/health")
async def health(response: Response):
    """Deep healthcheck — exercises the DB pool. Returns 200 if Postgres
    responds, 503 otherwise. Used by the platform proxy and external
    uptime monitors. Unauthenticated by design, so the failure body must
    never echo exception internals (SECURITY_AUDIT.md F7) — full detail
    goes to the server log only."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception as exc:
        print(f"[health] DB check failed: {exc!r}")
        response.status_code = 503
        return {"status": "degraded", "db": "error"}


# ----- Frontend serving -----
#
# Stage 1 of the Dockerfile produces /app/frontend-dist with the built React
# SPA. We mount /assets directly for cache-busted bundles, and use a catch-all
# route for SPA navigation (any non-API path falls back to index.html so
# React Router can take over).
#
# In dev (without the build artefact present) this section quietly no-ops
# and Vite at :5173 serves the frontend independently.

_FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", "/app/frontend-dist")).resolve()
_INDEX_HTML = _FRONTEND_DIST / "index.html"

if _FRONTEND_DIST.is_dir():
    # Static assets (JS/CSS chunks under /assets/, favicon, etc.)
    assets_dir = _FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa_fallback(spa_path: str, request: Request):
        """SPA fallback: any non-API, non-health, non-assets path returns
        index.html so React Router handles it client-side."""
        # Don't shadow API routes or the healthcheck.
        if spa_path.startswith(("api/", "health", "assets/")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        # Try to serve a real file from dist (e.g. /favicon.ico, /robots.txt).
        # Resolve and confirm the result is still inside _FRONTEND_DIST before
        # serving — `Path.__truediv__` alone does not collapse `..` segments,
        # which allowed reading arbitrary files outside the build directory
        # (SECURITY_AUDIT.md F4).
        candidate = (_FRONTEND_DIST / spa_path).resolve()
        if candidate.is_relative_to(_FRONTEND_DIST) and candidate.is_file():
            return FileResponse(candidate)

        # Otherwise — let React Router take over. index.html must NOT be cached:
        # it references hash-busted asset filenames that change every build, so a
        # stale cached index.html points at JS/CSS that no longer exist → blank
        # page after a redeploy. The /assets bundles themselves are content-hashed
        # and safe to cache forever (handled by the StaticFiles mount above).
        if _INDEX_HTML.is_file():
            return FileResponse(
                _INDEX_HTML,
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )
        return JSONResponse({"detail": "Frontend not built"}, status_code=503)
