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


ChatProviderId = Literal["codex", "claude", "coding-plan"]
ChatStatus = Literal["idle", "running", "failed", "interrupted"]


class ChatProvider(BaseModel):
    id: ChatProviderId
    label: str
    available: bool
    detail: str
    # What was actually found about this connection, kept apart so nothing has
    # to be inferred from one sentence. `models` holds exactly what the
    # installed CLI's own read-only catalogue answered; it is never a guess,
    # and a connection whose catalogue cannot be read says why instead.
    installed: bool = False
    signedIn: bool | None = None
    models: list[str] = Field(default_factory=list)
    modelCatalog: Literal["checking", "ready", "unavailable"] = "checking"
    modelDetail: str = ""


class ChatProject(BaseModel):
    projectId: str
    projectDir: str
    name: str
    chatCount: int
    # The project's own published position, read through its existing P036
    # interfaces so a conversation can say which version it is talking about.
    # A candidate is not a version and never appears here.
    version: int | None = None
    stage: str | None = None


class ChatPermissionOption(BaseModel):
    optionId: str
    name: str
    kind: str


class ChatPermission(BaseModel):
    id: str
    title: str
    options: list[ChatPermissionOption]


class ChatAttachment(BaseModel):
    id: str
    name: str
    mimeType: str
    size: int


class ChatAttachmentContent(ChatAttachment):
    format: Literal["text", "base64"]
    content: str
    offset: int
    total: int
    nextOffset: int | None
    page: int
    totalPages: int


class ChatAttachmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=255)
    mimeType: str = Field(default="application/octet-stream", min_length=1, max_length=100)
    data: str = Field(max_length=((20 * 1024 * 1024 + 2) // 3) * 4)

    @field_validator("name")
    @classmethod
    def filename_only(cls, value: str) -> str:
        if value in {".", ".."} or any(char in "/\\" or ord(char) < 32 for char in value):
            raise ValueError("Use a filename without a directory or control characters.")
        return value


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "assistant", "tool"]
    content: str
    createdAt: str
    status: Literal["complete", "streaming", "failed", "interrupted"] = "complete"
    # The finished candidate this activity reported, so the conversation can
    # open that exact run. Absent on older records and on every other message.
    candidateId: str | None = None
    permission: ChatPermission | None = None
    attachments: list[ChatAttachment] = Field(default_factory=list)


class ChatSummary(BaseModel):
    id: str
    projectId: str
    projectDir: str
    title: str
    provider: ChatProviderId
    model: str | None = None
    status: ChatStatus = "idle"
    archived: bool = False
    createdAt: str
    updatedAt: str
    error: HubError | None = None


class ChatDetail(ChatSummary):
    messages: list[ChatMessage] = Field(default_factory=list)


class ChatUsageSource(BaseModel):
    """Only the native conversation identity and its Hub project binding."""

    projectId: str
    sessionId: str


class ChatCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    projectDir: str = Field(min_length=1)
    provider: ChatProviderId
    model: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)


class ChatWorkspace(BaseModel):
    """Where new projects are created, and what is already in that folder."""

    workspaceDir: str
    configured: bool
    projects: list[str] = Field(default_factory=list)


class ChatProjectRequest(BaseModel):
    """Create one empty project in the workspace; the name becomes its id."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=80)
    workspaceDir: str | None = Field(default=None, min_length=1)


class ChatModelRequest(BaseModel):
    """Which model this conversation's next turns use; null means the CLI default."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    model: str | None = Field(default=None, min_length=1, max_length=200)


class ChatArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    archived: bool


class ChatDesignContext(BaseModel):
    """The exact source and focus this one message is about.

    A caller that already knows which object it is talking about says so here,
    and the turn is prepared against that. Every field but the Stage is
    required: a partial selection would have to be completed by guessing, and a
    guess about which object a change lands on is the one thing this must not
    do. It selects nothing and authorises nothing — the project the turn is
    bound to is still the conversation's own.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    sourceRunId: str = Field(min_length=1)
    stateDigest: str = Field(min_length=1)
    targetComponentId: str = Field(min_length=1)
    elementId: str = Field(min_length=1)
    sourceStageRef: str | None = Field(default=None, min_length=1)


class ChatPostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    content: str = ""
    projectId: str = Field(min_length=1)
    attachments: list[ChatAttachmentInput] = Field(default_factory=list, max_length=8)
    # Absent on every existing caller, and never carried over: a later message
    # with no context of its own is prepared exactly as it was before.
    designContext: ChatDesignContext | None = None


class ChatPermissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    projectId: str = Field(min_length=1)
    optionId: str | None
