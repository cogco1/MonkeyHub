"""The health route answers without touching the project."""

from __future__ import annotations

from fastapi import APIRouter

from ..transport.health import StudioHealth

router = APIRouter(tags=["health"])


@router.get("/health", response_model=StudioHealth, response_model_by_alias=True)
def read_health() -> StudioHealth:
    return StudioHealth(status="ok", project_bound=False)
