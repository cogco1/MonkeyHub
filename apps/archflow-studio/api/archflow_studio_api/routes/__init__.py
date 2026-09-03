"""Every HTTP route the Studio API serves, mounted under ``/api``."""

from __future__ import annotations

from fastapi import APIRouter

from . import health

router = APIRouter(prefix="/api")
router.include_router(health.router)

__all__ = ["router"]
