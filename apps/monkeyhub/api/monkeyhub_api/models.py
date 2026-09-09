"""The finite application lifecycle exposed by the Hub."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

AppId = Literal["monkeyarch", "monkeydiagram", "monkeymonitor", "monkeyboard"]


class HubError(BaseModel):
    code: str
    detail: str


class AppStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    appId: AppId
    title: str
    serviceId: Literal["studio", "monitor", "board"]
    available: bool = True
    state: Literal["stopped", "starting", "running", "stopping", "error", "unavailable"]
    url: str | None = None
    processId: int | None = None
    error: HubError | None = None


class HubHealth(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["monkeyhub-api"] = "monkeyhub-api"
    serverVersion: str = "0.1.0"
    processId: int
    parentProcessId: int
    managedInstanceId: str | None = None
    sourceRevision: str | None = None


class HubFailure(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.error = HubError(code=code, detail=detail)
