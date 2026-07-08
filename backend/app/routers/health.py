"""
Lightweight health-check endpoint — GET /api/v1/health.

Unlike /health in main.py (which exercises the DB pool for the platform
proxy / uptime monitors), this endpoint requires no authentication and
touches no database. It exists as a fast wiring test for the deal-flow
build loop and any lightweight liveness probes.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}
