"""Every HTTP route the Studio API serves, mounted under ``/api``."""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    artifacts,
    candidates,
    controls,
    events,
    health,
    intents,
    pick,
    project,
    projects,
    protocol,
    proposals,
    state,
    validation,
)

router = APIRouter(prefix="/api")
router.include_router(health.router)
router.include_router(protocol.router)
router.include_router(projects.router)
router.include_router(project.router)
router.include_router(state.router)
router.include_router(artifacts.router)
router.include_router(pick.router)
router.include_router(proposals.router)
router.include_router(intents.router)
router.include_router(controls.router)
router.include_router(candidates.router)
router.include_router(validation.router)
router.include_router(events.router)

__all__ = ["router"]
