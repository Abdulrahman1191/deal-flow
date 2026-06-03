# syntax=docker/dockerfile:1.6
# Multi-stage build:
#   Stage 1 — build the Vite/React frontend → static dist/
#   Stage 2 — Python runtime serves FastAPI which also serves the static dist
#
# The platform proxy lives in front of this container, terminates TLS,
# handles Slack auth, and adds X-Auth-Email on every request.

# ===========================================================================
#   Stage 1 — Frontend build
# ===========================================================================
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
# `npm ci` is strict about lockfile sync; fall back to `npm install` if the
# lockfile drifted so the build doesn't hard-fail on a minor mismatch.
RUN npm ci --silent || npm install --silent
COPY frontend/ ./
# Go straight to the bundler. We deliberately do NOT run the package.json
# "build" script — it prefixes `tsc`, but this repo has no tsconfig.json so
# tsc would error. Vite transpiles TS via esbuild (no type-check, no tsconfig
# needed), which is exactly what we want for a production bundle.
RUN npx vite build

# ===========================================================================
#   Stage 2 — Python runtime
# ===========================================================================
FROM python:3.11-slim AS runtime
WORKDIR /app

# System deps:
#   libpq-dev/gcc — psycopg2 build
#   curl          — platform healthcheck
#   tesseract-ocr (+ -ara) — OCR fallback for scanned / broken-CMap Arabic decks
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq-dev gcc curl \
      tesseract-ocr tesseract-ocr-ara \
    && rm -rf /var/lib/apt/lists/*

# Python deps — install before code so cache survives code changes
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Backend code
COPY backend/ ./

# Frontend static build from stage 1, mounted at /app/frontend-dist
COPY --from=frontend-build /frontend/dist ./frontend-dist

# The platform proxy targets us on $PORT (defaults to 3000 per the platform
# convention; FastAPI binds to that at runtime).
ENV PORT=3000
EXPOSE 3000

# Platform expects a healthcheck — hit our DB-aware /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:${PORT:-3000}/health || exit 1

# Run alembic migrations on every boot (idempotent) before serving.
# Bind 0.0.0.0 so the platform proxy can reach us across the docker network.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-3000}"]
