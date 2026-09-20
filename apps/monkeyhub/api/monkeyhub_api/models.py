"""The finite application lifecycle exposed by the Hub."""

from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

AppId = Literal["monkeyarch", "monkeymonitor", "monkeyboard", "monkeyfab"]


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
    apiUrl: str | None = None
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
    contextMode: Literal["continue", "project", "stage"] = "continue"
    confirmedStageRef: str | None = None
    confirmedStageLabel: str | None = None


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


class ProjectArchiveExportRequest(BaseModel):
    """Write one retained project as the portable archive file this names."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    projectDir: str = Field(min_length=1)
    archivePath: str = Field(min_length=1)


class ProjectArchiveRestoreRequest(BaseModel):
    """Restore one archive under this parent folder, or the Hub workspace."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    archivePath: str = Field(min_length=1)
    targetParent: str | None = Field(default=None, min_length=1)


class ProjectArchiveSummary(BaseModel):
    """What one archive holds and where it is, said without design content."""

    projectId: str
    formatVersion: int
    version: int
    stateSha256: str
    runCount: int
    fileCount: int
    retainedBytes: int
    categories: dict[str, int]
    omissions: list[str]
    externalDependencies: list[str]
    archivePath: str
    archiveBytes: int
    archiveSha256: str
    verified: bool
    projectDir: str


class ProjectArchiveRestoreResult(BaseModel):
    """The restored archive's summary beside the project it is now listed as."""

    summary: ProjectArchiveSummary
    project: ChatProject


class ChatModelRequest(BaseModel):
    """Which model this conversation's next turns use; null means the CLI default."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    model: str | None = Field(default=None, min_length=1, max_length=200)


class ChatArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    archived: bool


class ChatDesignContext(BaseModel):
    """Exact project state, with an optional object or multi-object read focus.

    No focus means the design as a whole. It does not select a recent candidate
    or grant permission to edit the dependencies included in its read context.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    sourceRunId: str | None = Field(default=None, min_length=1)
    stateDigest: str = Field(min_length=1)
    targetComponentId: str | None = Field(default=None, min_length=1)
    elementId: str | None = Field(default=None, min_length=1)
    elementIds: list[str] = Field(default_factory=list, max_length=64)
    sourceStageRef: str | None = Field(default=None, min_length=1)
    contextRefs: list[str] = Field(default_factory=list, max_length=16)
    contextOffset: int = Field(default=0, ge=0)

    @field_validator("elementIds", "contextRefs")
    @classmethod
    def exact_references(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Context references must name an exact nonempty object.")
        if len(set(values)) != len(values):
            raise ValueError("Context references must not repeat an object.")
        return values

    @model_validator(mode="after")
    def consistent_focus(self):
        if self.elementId is not None and self.elementIds and self.elementIds != [self.elementId]:
            raise ValueError("elementId and elementIds must name the same focus when both are supplied.")
        return self


class ChatPostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    content: str = ""
    projectId: str = Field(min_length=1)
    attachments: list[ChatAttachmentInput] = Field(default_factory=list, max_length=8)
    # The verified editing projection for this turn only; old callers may omit it.
    designContext: ChatDesignContext | None = None
    contextMode: Literal["continue", "project", "stage"] = "continue"

    @model_validator(mode="after")
    def project_context_requires_source(self):
        if self.contextMode in {"project", "stage"} and self.designContext is None:
            raise ValueError("Starting from project state requires an explicit designContext.")
        return self


class ChatPermissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    projectId: str = Field(min_length=1)
    optionId: str | None


class ComputerInspectRequest(BaseModel):
    """Read one window's element tree, without touching anything."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    application: str = Field(min_length=1, max_length=120)
    window: str | None = Field(default=None, max_length=300)
    depth: int = Field(default=6, ge=1, le=12)

    @field_validator("window")
    @classmethod
    def compilable_window(cls, value: str | None) -> str | None:
        """A window filter is a Python regex, and a bad one is the caller's mistake.

        It is compiled here rather than deep in the provider, where re.error is
        not a ValueError and would leave this route answering 500 to a typo.
        """

        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(
                    f"window must be a Python regular expression: {exc}"
                ) from exc
        return value


class ComputerActionRequest(BaseModel):
    """One ComputerAction@1 payload; monkeycontrol owns what is inside it."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    action: dict[str, Any]
    # Absent means the mode the policy file chose for this machine.
    mode: Literal["fast", "demo"] | None = None


class ComputerRecordingRequest(BaseModel):
    """Start or stop the one recording a runtime may have running."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    command: Literal["start", "stop"]
    name: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class ComputerPolicy(BaseModel):
    """What this machine's owner allows, read from a file the Hub only reads.

    It lives at diagnostics/monkeycontrol/policy.json under the Hub's runtime
    root, beside the trace the runtime writes. Nothing in the Hub creates it:
    absent, unreadable or disabled all mean the same thing, which is no.
    """

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    enabled: bool = False
    allowedProcesses: list[str] = Field(default_factory=list, max_length=40)
    mode: Literal["fast", "demo"] = "fast"
