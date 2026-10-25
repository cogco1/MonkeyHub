"""The finite application lifecycle exposed by the Hub."""

from pathlib import Path
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

AppId = Literal["monkeyarch", "monkeymonitor", "monkeyboard", "monkeyrender", "monkeyfab"]


def plain_spelling(path: str) -> str:
    """``path`` without a Win32 extended-length prefix, otherwise as spelled: ``\\\\?\\C:\\p`` is ``C:\\p``.

    The desktop shell hands the Hub its runtime folder with the prefix, and the
    projects made under it inherited it, so one folder was filed and listed
    under two spellings (gap 26). A UNC path keeps its two slashes.
    """

    if path[:8].upper() == "\\\\?\\UNC\\":
        return "\\\\" + path[8:]
    if path[:4] == "\\\\?\\" and path[5:7] == ":\\":
        return path[4:]
    return path


class HubError(BaseModel):
    code: str
    detail: str
    category: Literal["transient", "conflict"] | None = Field(default=None, description=(
        "transient: it passes by itself; conflict: the project moved, read it again before deciding. "
        "Left out for a failure the caller or a person answers. The Hub's answer adds it from "
        "archflow.contracts.failures; this module stays importable without archflow (an update's trial load)."))
    resend: bool | None = Field(default=None, description=(
        "True when nothing was done, so the same request may be sent again unchanged."))


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
# What a conversation waits for from the architect (#300). One value for now:
# an unanswered permission request.
ChatAttention = Literal["permission"]


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


CredentialId = Literal["gemini", "coding-plan"]


class CredentialStatus(BaseModel):
    """Whether a provider key is in use and where it comes from (#334); never the key."""

    id: CredentialId
    configured: bool
    # saved: this account's credential store, written from Hub settings.
    # environment: a variable the Hub was launched with; it wins and is named.
    # claude-config: the Claude CLI's own endpoint configuration, used for
    # Coding Plan while the Hub holds no Coding Plan token of its own.
    source: Literal["saved", "environment", "claude-config"] | None = None
    variable: str | None = None
    saved: bool = False
    storeAvailable: bool = True


class CredentialWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: SecretStr = Field(description="Written once; no response ever carries it.")


class CredentialCheck(BaseModel):
    id: CredentialId
    result: Literal["accepted", "rejected", "unreachable", "missing"]
    detail: str


ProviderLinkId = Literal["gemini-keys", "zhipu-keys", "moonshot-keys", "deepseek-keys", "bailian-keys"]


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
    # The name of the step the project's working design stands on, the way the history page names it (what the
    # person or the AI called it, else the Stage accepted on it, else the words it was asked in); null when nothing
    # names it. The left column shows it under the project's name.
    step: str | None = None


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


class ChatDocumentRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    runId: str = Field(min_length=1)
    assetSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revisionRef: str | None = None
    pageIndex: int = Field(default=0, ge=0)


class ChatDocument(ChatDocumentRef):
    fileName: str
    mimeType: str
    # The part a user message's image plays in a render discussion (#253), as
    # the user chose it; absent on presented results and older records.
    role: Literal["source", "reference"] | None = None


class ChatRenderContext(BaseModel):
    """The images one message discusses, by exact registered page and the role the user chose (#253).

    One source, the image a render would start from, and up to three
    references it may borrow from. The source is never also a reference and no
    page repeats. It names pages only: never a file name, a newest image or a
    model state, and it needs no design context.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    source: ChatDocumentRef
    references: list[ChatDocumentRef] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def distinct_pages(self):
        pages = [(row.runId, row.assetSha256, row.revisionRef, row.pageIndex) for row in (self.source, *self.references)]
        if len(set(pages)) != len(pages):
            raise ValueError("The source and each reference must be distinct pages; the source is not also a reference.")
        return self


class ChatSuggestionEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    value: str | None = Field(default=None, max_length=80)
    basis: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def estimate_requires_basis(self):
        if self.value is not None and (not self.value.strip() or not self.basis or not self.basis.strip()):
            raise ValueError("An estimate needs a nonempty value and its evidence basis; otherwise leave it unknown.")
        return self


class ChatSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    title: str = Field(min_length=1, max_length=160)
    outcome: str = Field(min_length=1, max_length=600)
    capability: Literal["available", "needs-development"]
    rationale: str = Field(min_length=1, max_length=800)
    tools: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=8)
    deliverables: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(default_factory=list, max_length=6)
    timeEstimate: ChatSuggestionEstimate = Field(default_factory=ChatSuggestionEstimate)
    costEstimate: ChatSuggestionEstimate = Field(default_factory=ChatSuggestionEstimate)
    prompt: str = Field(min_length=1, max_length=2000)

    @field_validator("title", "outcome", "rationale", "prompt")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Suggestion text must not be blank.")
        return value


class ChatMarkupContext(BaseModel):
    """The marks one message is about: a saved revision of the ink on one registered page (#300 3-3).

    The design loop reads what each mark lies on from this exact revision
    (``design_read part=marks``), and the architect's words are this message's
    text with the page's own comment and text marks; never a newer revision.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    runId: str = Field(min_length=1)
    assetSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pageIndex: int = Field(default=0, ge=0)
    revisionSha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="The saved annotation revision of the page.")
    drawingRevisionRef: str | None = Field(default=None, description="A view drawing's revisionRef.")


class ChatApprovedChange(BaseModel):
    """One item of a change list the architect ran, as the card states it."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    n: int = Field(ge=1, le=30)
    understanding: str = Field(min_length=1, max_length=240)
    targets: list[str] = Field(min_length=1, max_length=100)
    protected: list[str] = Field(default_factory=list, max_length=100)


class ChatApprovalQuote(BaseModel):
    """The items taken from one passage of the architect's words, and the span a submission binds for them."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    items: list[int] = Field(min_length=1, max_length=30)
    feedbackQuote: str | None = Field(default=None, max_length=2000)
    clauses: list[str] = Field(default_factory=list, max_length=30)


class ChatChangeListApproval(BaseModel):
    """The ticked items of a change list card, sent by the Hub in the architect's name with their own words.

    The message's text is only what the architect wrote; what each item means
    travels here, so nothing the Agent wrote reads as the person's.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    cardId: str = Field(pattern=r"^att_[0-9a-f]{32}$")
    items: list[ChatApprovedChange] = Field(min_length=1, max_length=30)
    quotes: list[ChatApprovalQuote] = Field(min_length=1, max_length=30)


class ChatSuggestionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    messageId: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                           json_schema_extra={"format": "uuid"})
    revision: int = Field(ge=0)


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
    documents: list[ChatDocument] = Field(default_factory=list)
    sourceTurnId: str | None = None
    presentationRevision: int | None = None
    suggestion: ChatSuggestion | None = None
    suggestionSelection: ChatSuggestionSelection | None = None
    contextMode: Literal["continue", "project", "stage"] = "continue"
    confirmedStageRef: str | None = None
    confirmedStageLabel: str | None = None
    # A message the architect sent while a turn was running (#301), and how far
    # it got: waiting for the Agent's next step, read by it, sent after the
    # current step was stopped for it, or never read because the turn ended.
    interjection: Literal["pending", "delivered", "restarted", "undelivered"] | None = None
    # The marks this message is about, and the change list items it runs (#300 3-3).
    markup: ChatMarkupContext | None = None
    changeListApproval: ChatChangeListApproval | None = None


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
    # External conversations are displayed here; their provider runs in the source host.
    sourceSessionId: str | None = None
    # "permission" while a permission request in this conversation waits for a
    # decision: the same request the conversation shows with its options.
    # Derived on every read and never stored, so a record cannot go stale.
    attention: ChatAttention | None = None

    @field_validator("projectDir")
    @classmethod
    def plain_project_dir(cls, value: str) -> str:
        # A conversation an earlier Hub saved under the prefixed spelling of its
        # folder belongs to the same project as one saved without it (gap 26).
        return plain_spelling(value)


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


class ChatPresentationBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    projectDir: str = Field(min_length=1)
    sourceSessionId: str = Field(min_length=1, max_length=200)
    provider: ChatProviderId = "codex"
    chatId: str | None = None
    title: str = Field(default="External conversation", min_length=1, max_length=200)


class ChatPresentationBinding(BaseModel):
    chatId: str
    projectId: str
    sourceSessionId: str
    token: str
    url: str


class ChatPresentationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    projectId: str
    sourceSessionId: str
    turnId: str
    messageId: str
    revision: int = Field(default=0, ge=0)
    kind: Literal["user", "progress", "assistant"]
    content: str = Field(default="", max_length=200000)
    status: Literal["streaming", "complete", "failed", "interrupted"] = "complete"
    attachments: list[ChatAttachmentInput] = Field(default_factory=list, max_length=8)
    documents: list[ChatDocumentRef] = Field(default_factory=list, max_length=8)
    suggestion: ChatSuggestion | None = None

    @model_validator(mode="after")
    def assistant_suggestion_only(self):
        if self.suggestion is not None and self.kind != "assistant":
            raise ValueError("Only an assistant result can present a suggestion.")
        return self


class ChatWorkspace(BaseModel):
    """Where new projects are created, and what is already in that folder."""

    workspaceDir: str
    configured: bool
    projects: list[str] = Field(default_factory=list)


class ChatPickPathRequest(BaseModel):
    """Ask the person to choose a folder or a file in the system's own dialog."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    kind: Literal["folder", "file"]
    title: str = Field(min_length=1, max_length=200)
    filter: Literal["archive", "any"] = "any"


class ChatPickedPath(BaseModel):
    path: str | None = Field(description="the chosen path; null when the dialog was closed without choosing")


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


class ChatStudyEvidence(BaseModel):
    """An explicit Study reference forwarded to the project runtime for validation."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    studyId: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    ledgerRef: str = Field(min_length=1)


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
    studyEvidence: list[ChatStudyEvidence] = Field(default_factory=list, max_length=3)

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
        if len({(item.studyId, item.ledgerRef) for item in self.studyEvidence}) != len(self.studyEvidence):
            raise ValueError("studyEvidence must not repeat an exact revision.")
        return self


class ChatPostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    content: str = ""
    projectId: str = Field(min_length=1)
    attachments: list[ChatAttachmentInput] = Field(default_factory=list, max_length=8)
    # The verified editing projection for this turn only; old callers may omit it.
    designContext: ChatDesignContext | None = None
    contextMode: Literal["continue", "project", "stage"] = "continue"
    suggestionSelection: ChatSuggestionSelection | None = None
    # The registered images this message discusses (#253), bound for this turn only.
    renderContext: ChatRenderContext | None = None
    # The marks it is about, and the change list items a card runs in the architect's name (#300 3-3).
    markupContext: ChatMarkupContext | None = None
    changeListApproval: ChatChangeListApproval | None = None

    @model_validator(mode="after")
    def project_context_requires_source(self):
        if self.suggestionSelection is not None and self.model_fields_set - {"projectId", "suggestionSelection"}:
            raise ValueError("Select a saved suggestion using only projectId and suggestionSelection.")
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
