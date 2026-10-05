"""Local Hub actions for invitations, replica progress and membership."""
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

class ShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectDir: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")
    role: Literal["viewer", "designer", "moderator"] = "designer"

class JoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invitation: str = Field(min_length=1, max_length=16384)
    projectDir: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")

class RoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["viewer", "designer", "moderator", "revoked"]


def team_router(teams):
    router = APIRouter(prefix="/api/team", tags=["team"])

    @router.post("/share")
    def share(payload: ShareRequest):
        return teams.share(payload.projectDir, payload.name, payload.role)

    @router.post("/join")
    def join(payload: JoinRequest):
        return teams.join(payload.invitation, payload.projectDir, payload.name)

    @router.get("/projects")
    def projects():
        return [teams.status(project_id) for project_id in teams.settings.read()["projects"]]

    @router.get("/projects/{project_id}")
    def status(project_id: str):
        return teams.status(project_id)

    @router.post("/projects/{project_id}/resume")
    def resume(project_id: str):
        return teams.resume(project_id)

    @router.put("/projects/{project_id}/members/{actor_id}")
    def role(project_id: str, actor_id: str, payload: RoleRequest):
        return teams.change_role(project_id, actor_id, payload.role)

    return router
