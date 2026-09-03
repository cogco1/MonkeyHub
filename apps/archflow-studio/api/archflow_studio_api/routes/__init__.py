"""Every HTTP route the Studio API serves, mounted under ``/api``."""

from __future__ import annotations

from fastapi import APIRouter

from . import health, project, state

router = APIRouter(prefix="/api")
router.include_router(health.router)
router.include_router(project.router)
router.include_router(state.router)

__all__ = ["router"]
