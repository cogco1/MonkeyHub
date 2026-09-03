"""Every HTTP route the Studio API serves, mounted under ``/api``."""

from __future__ import annotations

from fastapi import APIRouter

from . import artifacts, health, pick, project, state

router = APIRouter(prefix="/api")
router.include_router(health.router)
router.include_router(project.router)
router.include_router(state.router)
router.include_router(artifacts.router)
router.include_router(pick.router)

__all__ = ["router"]
