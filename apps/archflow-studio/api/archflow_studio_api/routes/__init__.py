"""Every HTTP route the Studio API serves, mounted under ``/api``."""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    artifacts,
    boards,
    candidates,
    capabilities,
    drawings,
    episodes,
    events,
    health,
    intents,
    options,
    pick,
    program,
    project,
    projects,
    protocol,
    proposals,
    runtime,
    settings,
    state,
    study,
    synchronization,
    validation,
)

router = APIRouter(prefix="/api")
router.include_router(health.router)
router.include_router(protocol.router)
router.include_router(settings.router)
router.include_router(projects.router)
router.include_router(project.router)
router.include_router(runtime.router)
router.include_router(state.router)
router.include_router(program.router)
router.include_router(artifacts.router)
router.include_router(boards.router)
router.include_router(drawings.router)
router.include_router(study.router)
router.include_router(pick.router)
router.include_router(proposals.router)
router.include_router(intents.router)
router.include_router(options.router)
router.include_router(candidates.router)
router.include_router(capabilities.router)
router.include_router(episodes.router)
router.include_router(validation.router)
router.include_router(events.router)
router.include_router(synchronization.router)

__all__ = ["router"]
