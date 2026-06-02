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
    auth, leads, assessments, briefings, feedback, overrides, portfolio,
)

_is_prod = os.getenv("ENV", "dev").lower() == "prod"

app = FastAPI(
    title="Raed Ventures Deal Flow",
    version="2.0.0",
    docs_url=None if _is_prod else "/api/docs",
    redoc_url=None if _is_prod else "/api/redoc",
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

# ----- API routes -----
API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(leads.router, prefix=API_PREFIX)
app.include_router(assessments.router, prefix=API_PREFIX)
app.include_router(briefings.router, prefix=API_PREFIX)
app.include_router(feedback.router, prefix=API_PREFIX)
app.include_router(overrides.router, prefix=API_PREFIX)
app.include_router(portfolio.router, prefix=API_PREFIX)


@app.get("/health")
async def health(response: Response):
    """Deep healthcheck — exercises the DB pool. Returns 200 if Postgres
    responds, 503 otherwise. Used by the platform proxy and external
    uptime monitors."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception as exc:
        response.status_code = 503
        return {"status": "degraded", "db": "error", "detail": repr(exc)[:200]}


# ----- Frontend serving -----
#
# Stage 1 of the Dockerfile produces /app/frontend-dist with the built React
# SPA. We mount /assets directly for cache-busted bundles, and use a catch-all
# route for SPA navigation (any non-API path falls back to index.html so
# React Router can take over).
#
# In dev (without the build artefact present) this section quietly no-ops
# and Vite at :5173 serves the frontend independently.

_FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", "/app/frontend-dist"))
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

        # Try to serve a real file from dist (e.g. /favicon.ico, /robots.txt)
        candidate = _FRONTEND_DIST / spa_path
        if candidate.is_file():
            return FileResponse(candidate)

        # Otherwise — let React Router take over
        if _INDEX_HTML.is_file():
            return FileResponse(_INDEX_HTML)
        return JSONResponse({"detail": "Frontend not built"}, status_code=503)
