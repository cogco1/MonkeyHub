"""The finite application lifecycle exposed by the Hub."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

AppId = Literal["monkeyarch", "monkeydiagram", "monkeymonitor", "monkeyboard", "monkeyfab"]


class HubError(BaseModel):
    code: str
    detail: str


class AppStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    appId: AppId
    title: str
    serviceId: Literal["studio", "monitor", "board", "hub"]
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


class FabProfile(BaseModel):
    key: str
    label: str
    nominal_volume_mm: list[float]
    usable_origin_mm: list[float]
    usable_volume_mm: list[float]
    notes: str
    sources: list[str]


class _FabRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, hide_input_in_errors=True)

    source: str = Field(min_length=1)

    @field_validator("source")
    @classmethod
    def absolute_source(cls, value: str) -> str:
        if "\x00" in value or not Path(value).is_absolute():
            raise ValueError("source must be an absolute path")
        return value


class FabPrepareRequest(_FabRequest):
    outputDir: str = Field(min_length=1)
    printer: str = Field(min_length=1)
    inputUnit: Literal["mm", "cm", "m", "in"]
    scale: str = Field(default="1:1", min_length=1)
    xyMarginMm: float = Field(default=5.0, ge=0)
    zClearanceMm: float = Field(default=5.0, ge=0)

    @field_validator("outputDir")
    @classmethod
    def absolute_output(cls, value: str) -> str:
        if "\x00" in value or not Path(value).is_absolute():
            raise ValueError("outputDir must be an absolute path")
        return value


class FabPrepareResult(BaseModel):
    stdout: str
    outputDir: str


class FabSendRequest(_FabRequest):
    host: str = Field(min_length=1)
    accessCode: SecretStr | None = None
    remoteName: str | None = None
    timeout: float = Field(default=30.0, gt=0)
    dryRun: bool = False


class FabSendResult(BaseModel):
    file: str
    remote_path: str
    bytes: int
    plates: list[int]
    host: str
    status: Literal["validated", "uploaded"]
    print_started: Literal[False]
