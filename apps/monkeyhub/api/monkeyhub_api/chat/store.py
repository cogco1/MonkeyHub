"""Project-bound conversations backed by the installed coding CLIs.

The CLI retains its native conversation. Hub retains the visible transcript and
that exact session id under its configured runtime root. Design writes stay on
the existing Studio interfaces, reached through the stdio tool server each CLI
starts (``mcp_server``).
"""

from __future__ import annotations

import base64
import binascii
from hashlib import sha256
from concurrent.futures import Future, InvalidStateError
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import shutil
import secrets
import subprocess
import sys
import threading
import time
from typing import Callable, Literal, Mapping
from urllib.parse import urlencode
from uuid import uuid4

from pydantic import Field

from .. import projects
from . import activity, guides, mcp_server, media, providers, skill_plugins, transport, turn_context
from ..settings.store import read_application_settings

from ..models import (
    ChatAttachment, ChatAttention, ChatCreateRequest, ChatDesignContext, ChatDetail, ChatMessage, ChatPostRequest, ChatProject,
    ChatDocument, ChatDocumentRef, ChatPresentationBindRequest, ChatPresentationBinding, ChatPresentationRequest,
    ChatPermission, ChatPermissionOption, ChatPermissionRequest, ChatSuggestion,
    ChatProjectRequest, ChatProvider, ChatSummary, ChatUsageSource, ChatWorkspace, HubError, HubFailure,
)
from .turn_trace import HubTurnObserver
from monkeymonitor.store import UsageLog


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _SavedChat(ChatDetail):
    nativeSessionId: str | None = None
    cliStartId: str | None = None
    priorProviderSessionIds: list[str] = []
    # Provider continuity only; the Stage and all design facts remain in P036.
    providerStageRef: str | None = None
    # Records written before ACP retain the exact native CLI continuation path.
    transport: Literal["cli", "acp"] = "cli"
    acpSessionId: str | None = None
    acpDefaultModel: str | None = None
    # Read from the transcript on every summary (#300); a record never stores it.
    attention: ChatAttention | None = Field(default=None, exclude=True)


# A summary is these fields of the record, plus its attention read fresh.
_SUMMARY_FIELDS = set(ChatSummary.model_fields) - {"attention"}


def _attention(session: _SavedChat) -> ChatAttention | None:
    """What this conversation waits for from the architect (#300).

    A permission request waits exactly while its message still carries it:
    the request the conversation shows with its options. Answering, stopping,
    a withdrawn step and a Hub restart all clear it. The record is already in
    memory, so asking reads no file.
    """
    return "permission" if any(message.permission is not None for message in session.messages) else None


def _turn_id(session: _SavedChat) -> str:
    """The last user message the Agent was given; see ChatStore._answering for a running turn."""
    return next((row.id for row in reversed(session.messages) if row.role == "user"
                 and row.interjection in {None, "delivered", "restarted"}), "turn")


def _asked(session: _SavedChat) -> ChatMessage | None:
    """The message that started this turn, never an interjection sent during it."""
    return next((row for row in reversed(session.messages) if row.role == "user" and row.interjection is None), None)


@dataclass
class _Running:
    stop: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    last_save: float = 0.0
    trace: HubTurnObserver | None = None
    # This turn's own selected source and focus, if the message named one. It
    # lives and dies with the turn: nothing reads it afterwards, and the next
    # message brings its own or none.
    design_context: ChatDesignContext | None = None
    context_mode: Literal["continue", "project", "stage"] = "continue"
    attachments: tuple[tuple[ChatAttachment, Path], ...] = ()
    # The registered images this turn's message discusses, already bound to
    # its project with the roles the user chose (#253); this turn only.
    render: tuple[ChatDocument, ...] = ()
    # #301. The user messages this turn has handed to the Agent, first to last:
    # a call opened under one of them keeps its row when it finishes later.
    turns: list[str] = field(default_factory=list)
    # Interjections not yet with the Agent, in the order they were sent. What
    # is still here when the turn ends becomes the next prompt.
    waiting: list[tuple[str, str]] = field(default_factory=list)
    # Claude reads further user messages from stdin until its turn's result;
    # None when this turn has no such channel or it has closed.
    stdin: queue.Queue | None = None
    # Interjections already written to that stdin and not yet read back.
    written: list[str] = field(default_factory=list)
    # This turn was cancelled to continue with an interjection, not stopped.
    redirected: bool = False
    # This Claude turn was started with the library's skills only (#463): its
    # init event says which other skills are still left to turn off.
    library_skills: bool = False
    # The ACP prompt is with the adapter; steering is offered only then.
    prompting: bool = False
    steering: threading.Lock = field(default_factory=threading.Lock)


class ChatStore:
    def __init__(self, runtime_root: Path, hub_url: str, *, applications=None, commands=None, acp_command=None, timeout_s: float = 900):
        self.root = runtime_root / "chats"
        self.runtime_root = runtime_root
        self.usage_log = UsageLog(runtime_root / "diagnostics" / "monkeymonitor")
        self.hub_url = hub_url
        self.applications = applications
        self.commands = commands
        self._use_acp = acp_command is not None or commands is None
        self._acp_command = tuple(acp_command) if acp_command is not None else (providers._codex_acp_command() if commands is None else None)
        self._acp_sessions = {}
        self._acp_tools: dict[str, dict[str, dict]] = {}
        self._permissions: dict[tuple[str, str], Future] = {}
        self.timeout_s = timeout_s
        self._lock = threading.RLock()
        self._sessions: dict[str, _SavedChat] = {}
        self._running: dict[str, _Running] = {}
        self._progress_rows: dict[str, dict[str, ChatMessage]] = {}
        self._presentation_tokens: dict[str, str] = {}
        self._loaded = False
        self._closing = False
        self.on_change = None
        # The project runtime answers which candidates a chat's requests made
        # since a time, from its admission and operation records (#404 F15).
        self.turn_results = None
        # One in-memory answer per Hub run: what the installed CLIs said when
        # they were last asked. Nothing is written to disk and no check runs on
        # the transcript's polling path.
        self._checked: dict[str, dict] = {}
        self._checked_at: float | None = None
        self._checking = False
        self._check_thread: threading.Thread | None = None

    def _load(self) -> None:
        if self._loaded:
            return
        for path in sorted(self.root.glob("*.json")):
            try:
                session = _SavedChat.model_validate_json(path.read_text(encoding="utf-8"))
                projects._identifier(session.id)
                if path.stem != session.id:
                    raise ValueError("chat filename does not match its id")
            except (ValueError, OSError, HubFailure) as exc:
                raise HubFailure(503, "CHAT_RECORD_INVALID", "A saved chat record could not be read.") from exc
            self._sessions[session.id] = session
            if session.status == "running":
                session.status = "interrupted"
                session.error = HubError(code="CHAT_INTERRUPTED", detail="Hub closed before this turn finished. You can continue this conversation.")
                for message in session.messages:
                    if message.status == "streaming" and not session.sourceSessionId:
                        message.status = "interrupted"
                    if message.interjection == "pending":
                        message.interjection = "undelivered"
                    message.permission = None
                self._save(session)
            if session.archived:
                # Archived before this Hub started: its scratch was temporary computation (#404 item 6).
                self._clear_scratch(session.id)
        self._loaded = True

    def _attachment_path(self, session_id: str, attachment: ChatAttachment) -> Path:
        suffix = Path(attachment.name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ".bin"
        return self.root / projects._identifier(session_id) / "attachments" / (projects._identifier(attachment.id) + suffix)

    def _scratch_path(self, session_id: str) -> Path:
        return self.root / projects._identifier(session_id) / "scratch"

    def _clear_scratch(self, session_id: str) -> None:
        """Remove a chat's scratch: temporary computation, never a project record or an attachment."""
        shutil.rmtree(self._scratch_path(session_id), ignore_errors=True)

    def attachment(self, session_id: str, attachment_id: str) -> tuple[ChatAttachment, Path]:
        with self._lock:
            session = self._session(session_id)
            attachment = next((item for message in session.messages for item in message.attachments
                               if item.id == attachment_id), None)
            if attachment is None:
                raise HubFailure(404, "CHAT_ATTACHMENT_NOT_FOUND", "This file is not attached to this conversation.")
            path = self._attachment_path(session_id, attachment)
            if not path.is_file():
                raise HubFailure(404, "CHAT_ATTACHMENT_NOT_FOUND", "The attached file is no longer available.")
            return attachment, path

    def read_attachment(self, session_id: str, attachment_id: str, *, offset: int = 0,
                        limit: int = 32768, page: int = 1) -> dict:
        media._attachment_read_paging(offset, limit, page)
        attachment, path = self.attachment(session_id, attachment_id)
        total_pages = 1
        if attachment.mimeType == "application/pdf" or path.suffix == ".pdf":
            from pypdf import PdfReader

            try:
                with path.open("rb") as source:
                    reader = PdfReader(source)
                    total_pages = len(reader.pages)
                    if page > total_pages:
                        raise HubFailure(422, "CHAT_ATTACHMENT_PAGE_INVALID", f"This PDF has {total_pages} pages.")
                    text = reader.pages[page - 1].extract_text() or ""
            except HubFailure:
                raise
            except Exception as exc:
                raise HubFailure(422, "CHAT_ATTACHMENT_PDF_INVALID", "Text could not be extracted from this PDF.") from exc
            data = None
        else:
            if page != 1:
                raise HubFailure(422, "CHAT_ATTACHMENT_PAGE_INVALID", "Only PDF attachments have numbered pages.")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise HubFailure(503, "CHAT_ATTACHMENT_READ_FAILED", "The attached file could not be read.") from exc
            try:
                text = data.decode("utf-8") if b"\0" not in data else None
            except UnicodeDecodeError:
                text = None
        if text is not None:
            format_name, total, content = "text", len(text), text[offset:offset + limit]
        else:
            format_name, total = "base64", len(data)
            content = base64.b64encode(data[offset:offset + limit]).decode("ascii")
        return {**attachment.model_dump(), "format": format_name, "content": content,
                "offset": offset, "total": total, "nextOffset": offset + limit if offset + limit < total else None,
                "page": page, "totalPages": total_pages}

    def _save(self, session: _SavedChat, attachments: tuple[tuple[ChatAttachment, bytes], ...] = ()) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if session.provider != "codex":
            self._scratch_path(session.id).mkdir(parents=True, exist_ok=True)
        path = self.root / f"{session.id}.json"
        temporary = path.with_suffix(".tmp")
        written = []
        saved = False
        try:
            for attachment, data in attachments:
                destination = self._attachment_path(session.id, attachment)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    written.append(destination)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(session.model_dump_json() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            saved = True
            if self.on_change is not None:
                self.on_change(session)
        finally:
            temporary.unlink(missing_ok=True)
            if not saved:
                for destination in written:
                    destination.unlink(missing_ok=True)

    def _check(self, refresh: bool) -> None:
        """Ask the installed CLIs once, off the request thread.

        A check runs at most one at a time and is reused until it ages out, so
        a polling page never starts a CLI. Failure leaves the previous answer
        and never blocks the Hub.
        """
        with self._lock:
            fresh = self._checked_at is not None and time.monotonic() - self._checked_at < providers._CHECK_TTL_S
            if self._checking or self._closing or (fresh and not refresh):
                return
            self._checking = True
            commands = self.commands if self.commands is not None else providers._cli_commands()
            environment = providers._claude_env()

        def run() -> None:
            try:
                found = providers._check_providers(commands, environment)
            except Exception:  # noqa: BLE001 - a check never takes the Hub down
                found = {}
            with self._lock:
                if found:
                    self._checked = found
                    self._checked_at = time.monotonic()
                elif self._checked_at is None:
                    self._checked_at = time.monotonic()
                self._checking = False

        thread = threading.Thread(target=run, daemon=True, name="hub-chat-connections")
        with self._lock:
            self._check_thread = thread
        thread.start()

    def open_login(self, provider: str) -> None:
        """Open the CLI's own sign-in in a console window; the provider check reads the result (#334)."""
        commands = self.commands if self.commands is not None else providers._cli_commands()
        name = "Codex" if provider == "codex" else "Claude Code"
        if provider not in commands:
            raise HubFailure(409, "CHAT_PROVIDER_MISSING", f"The {name} CLI is not installed on this computer.")
        if not providers._console_available():
            raise HubFailure(409, "CHAT_LOGIN_UNSUPPORTED", f"Sign in by running the {name} CLI's login in a terminal.")
        parts = (*commands[provider], *providers._LOGIN_ARGS[provider])
        if any('"' in part for part in parts):
            raise HubFailure(409, "CHAT_PROVIDER_MISSING", f"The {name} CLI path cannot be started from here.")
        # cmd /k keeps the window open, so the CLI's last words stay readable.
        line = " ".join(f'"{part}"' for part in parts)
        providers._open_console(f'cmd.exe /d /k "{line}"', str(Path.home()))

    def providers(self, refresh: bool = False) -> list[ChatProvider]:
        commands = self.commands if self.commands is not None else providers._cli_commands()
        env = providers._claude_env()
        saved_plan = bool(providers._coding_plan_env())
        configured_plan = saved_plan or providers.claude_plan_configured(env)
        self._check(refresh)
        with self._lock:
            checked, checking = dict(self._checked), self._checking and self._checked_at is None
        rows = [
            ChatProvider(id="codex", label="Codex CLI", available="codex" in commands, installed="codex" in commands,
                         detail="Uses the installed Codex CLI and its saved login." if "codex" in commands else "Codex CLI is not installed."),
            ChatProvider(id="claude", label="Claude Code", available="claude" in commands, installed="claude" in commands,
                         detail="Uses the installed Claude CLI and its saved login or configuration." if "claude" in commands else "Claude CLI is not installed."),
            ChatProvider(id="coding-plan", label="Coding Plan", available="claude" in commands and configured_plan,
                         installed="claude" in commands,
                         detail="Uses the Coding Plan endpoint and token saved in Hub settings." if saved_plan and "claude" in commands
                         else "Uses the Claude CLI's existing Anthropic-compatible endpoint and authentication." if configured_plan and "claude" in commands
                         else "No Coding Plan endpoint and token yet. Enter them in Hub settings, under Conversations."),
        ]
        if self._use_acp:
            codex = rows[0]
            codex.label = "Codex"
            if codex.installed and self._acp_command is None:
                codex.available = False
                codex.detail = "Install this Hub's locked ACP adapter with npm ci in apps/monkeyhub and its api/requirements.txt in the Hub Python environment."
            elif codex.installed:
                codex.detail = "Persistent ACP sessions through this Hub's adapter, using the installed Codex and its saved login."
        for row in rows:
            if not row.installed:
                row.modelCatalog, row.modelDetail = "unavailable", "Install this CLI to read the models it offers."
                continue
            answer = checked.get(row.id)
            if answer is None:
                row.modelCatalog = "checking" if checking else "unavailable"
                row.modelDetail = "" if checking else "This connection was not checked; try again."
                continue
            row.signedIn = answer.get("signedIn")
            row.models = list(answer.get("models") or [])
            row.modelCatalog = "ready" if row.models else "unavailable"
            row.modelDetail = str(answer.get("modelDetail") or "")
        return rows

    def set_model(self, session_id: str, model: str | None) -> ChatDetail:
        """Which model this conversation's next turn runs on.

        The conversation keeps its connection, its native CLI session and its
        project; only what the next start passes as the model changes. A turn
        already running keeps the model it started with.
        """
        with self._lock:
            session = self._session(session_id)
            if session_id in self._running:
                raise HubFailure(409, "CHAT_RUNNING", "Wait for this reply to finish before changing its model.")
            chosen = model.strip() if isinstance(model, str) else None
            if chosen == session.model or (not chosen and session.model is None):
                return self.get(session_id)
            session.model = chosen or None
            session.updatedAt = _now()
            self._save(session)
            return self.get(session_id)

    def projects(self) -> list[ChatProject]:
        # Only the conversations are read under the store's lock; the projects'
        # own files are read outside it, so the chat list polled beside this
        # never waits for them (#449).
        with self._lock:
            self._load()
            counts: dict[tuple[str, str], int] = {}
            for session in self._sessions.values():
                key = (session.projectId, session.projectDir)
                counts[key] = counts.get(key, 0) + 1
        listed: dict[tuple[str, str], ChatProject] = {}
        for key, count in counts.items():
            version, stage = projects._listed_project(key[1])[1]
            listed[key] = ChatProject(projectId=key[0], projectDir=key[1], name=key[0],
                                        chatCount=count, version=version, stage=stage)
        workspace = self.workspace()
        workspace_root = Path(workspace.workspaceDir).resolve()
        discovered = []
        for name in workspace.projects:
            try:
                candidate = (workspace_root / name).resolve()
                if candidate.is_relative_to(workspace_root):
                    discovered.append(str(candidate))
            except OSError:
                continue
        current = read_application_settings(self.runtime_root).project_dir
        for path in dict.fromkeys([*discovered, *([current] if current else [])]):
            try:
                (project_id, project_dir), (version, stage) = projects._listed_project(path, identify=True)
            except HubFailure:
                pass
            else:
                if (project_id, project_dir) not in listed:
                    listed[(project_id, project_dir)] = ChatProject(
                        projectId=project_id, projectDir=project_dir, name=project_id,
                        chatCount=0, version=version, stage=stage,
                    )
        return sorted(listed.values(), key=lambda row: (row.name, row.projectDir))

    def workspace(self) -> ChatWorkspace:
        """The folder new projects are created in, and the projects already there."""
        settings = read_application_settings(self.runtime_root)
        root = projects._workspace(self.runtime_root, settings)
        found: list[str] = []
        try:
            found = sorted(item.name for item in root.iterdir()
                           if item.is_dir() and (item / "project.json").is_file())
        except OSError:
            found = []
        return ChatWorkspace(workspaceDir=str(root), configured=bool(settings.workspace_dir), projects=found)

    def create_project(self, request: ChatProjectRequest) -> ChatProject:
        """Make an empty project in the workspace and list it as one."""
        with self._lock:
            self._load()
            settings = read_application_settings(self.runtime_root)
            workspace = Path(request.workspaceDir) if request.workspaceDir else projects._workspace(self.runtime_root, settings)
            root = projects._new_project(workspace, request.name)
            project_id, project_dir = projects._project(str(root))
            version, stage = projects._position(project_dir)
            return ChatProject(projectId=project_id, projectDir=project_dir, name=project_id,
                               chatCount=0, version=version, stage=stage)

    def list(self, project_id: str | None = None, *, archived: bool = False) -> list[ChatSummary]:
        # Polled by every open Hub view (#300): only the summary fields are
        # copied, never a whole transcript.
        with self._lock:
            self._load()
            return [ChatSummary.model_validate({**row.model_dump(include=_SUMMARY_FIELDS), "attention": _attention(row)})
                    for row in sorted(self._sessions.values(), key=lambda item: item.updatedAt, reverse=True)
                    if row.archived == archived and (project_id is None or row.projectId == project_id)]

    def usage_sources(self) -> list[ChatUsageSource]:
        """Archiving or starting fresh preserves every bound Codex usage source."""
        with self._lock:
            self._load()
            sources = []
            for row in self._sessions.values():
                if row.provider != "codex":
                    continue
                identifier = row.acpSessionId if row.transport == "acp" else row.nativeSessionId
                for identifier in dict.fromkeys([*row.priorProviderSessionIds, identifier]):
                    if identifier:
                        sources.append(ChatUsageSource(projectId=row.projectId, sessionId=identifier))
            return sources

    def _session(self, session_id: str) -> _SavedChat:
        self._load()
        projects._identifier(session_id)
        if session_id not in self._sessions:
            raise HubFailure(404, "CHAT_NOT_FOUND", "This chat does not exist.")
        return self._sessions[session_id]

    def get(self, session_id: str) -> ChatDetail:
        with self._lock:
            session = self._session(session_id)
            detail = ChatDetail.model_validate({**session.model_dump(), "attention": _attention(session)})
            extra = [row.model_copy(deep=True) for row in self._progress_rows.get(session_id, {}).values()]
            if detail.status != "running":
                ending = "interrupted" if detail.status == "interrupted" else "failed" if detail.status == "failed" else "complete"
                for row in extra:
                    if row.status == "streaming":
                        row.status = ending
            detail.messages = sorted([*detail.messages, *extra], key=lambda row: (row.createdAt, row.id))
            return detail

    def _progress(self, session, key: str, text: str, *, append: bool = False,
                  status: str = "complete") -> None:
        """One transient projection shared by native and external conversation events."""
        clean = providers._redact(text) if append else providers._redact(text).strip()
        if not clean:
            return
        rows = self._progress_rows.setdefault(session.id, {})
        identifier = f"{_turn_id(session)}:progress:{key}"
        row = rows.get(key)
        if row is None or row.id != identifier:
            row = ChatMessage(id=identifier, role="tool", content="", createdAt=_now(), status=status)
            rows[key] = row
        row.content = (row.content + clean if append else clean)[-2400:]
        row.status = status
        # This row is a live snapshot, not a retained transcript entry. Keep
        # its latest update beside the current activity as new tool rows arrive.
        row.createdAt = _now()
        if self.on_change is not None:
            self.on_change(session)

    def presentation_token(self, session_id: str) -> str:
        with self._lock:
            self._session(session_id)
            return self._presentation_tokens.setdefault(session_id, secrets.token_urlsafe(32))

    def bind_presentation(self, request: ChatPresentationBindRequest) -> ChatPresentationBinding:
        with self._lock:
            self._load()
            if self._closing:
                raise HubFailure(409, "CHAT_CLOSING", "Hub is closing.")
            project_id, project_dir = projects._project(request.projectDir)
            if request.chatId:
                session = self._session(request.chatId)
            else:
                session = next((row for row in self._sessions.values()
                                if row.projectDir == project_dir and row.sourceSessionId == request.sourceSessionId), None)
            if session is None:
                now = _now()
                session = _SavedChat(id=str(uuid4()), projectId=project_id, projectDir=project_dir,
                                     title=request.title, provider=request.provider, sourceSessionId=request.sourceSessionId,
                                     createdAt=now, updatedAt=now)
                self._save(session)
                self._sessions[session.id] = session
            if (session.projectId, session.projectDir, session.sourceSessionId, session.provider) != (
                    project_id, project_dir, request.sourceSessionId, request.provider):
                raise HubFailure(409, "CHAT_PRESENTATION_MISMATCH", "Bind the original source session and project; a native chat cannot be adopted.")
            if session.archived:
                raise HubFailure(409, "CHAT_ARCHIVED", "Restore this chat in Hub before binding it.")
            if session.status == "interrupted" and session.error and session.error.code == "CHAT_INTERRUPTED":
                # An external agent can outlive Hub. Explicit rebind resumes that same in-flight turn.
                session = session.model_copy(update={"status": "running", "error": None, "updatedAt": _now()}, deep=True)
                self._save(session)
                self._sessions[session.id] = session
            return ChatPresentationBinding(chatId=session.id, projectId=project_id, sourceSessionId=request.sourceSessionId,
                                           token=self.presentation_token(session.id),
                                           url=self.hub_url + "/?" + urlencode({"chatId": session.id}))

    def _document(self, session, ref: ChatDocumentRef):
        from project_runtime.application.artifacts import document_bytes

        binding = media._project_binding(session.projectId, session.projectDir)
        document = media._registered_page(binding, ref)
        if document is None:
            raise HubFailure(409, "CHAT_DOCUMENT_MISMATCH", "This exact document revision or page is unavailable.")
        if document.mime_type not in media._IMAGE_MIMES:
            # A drawing page is shown, not only named (#404 item 9): the exact page as the PNG
            # the Board export gives, which also verifies the source bytes against P036.
            from dataclasses import replace
            from project_runtime.application.boards import BoardExportPage, export_board_pages
            from project_runtime.errors import StudioError
            try:
                page = export_board_pages(binding, [BoardExportPage(ref.runId, ref.assetSha256, ref.revisionRef, ref.pageIndex)],
                                          "png", False, 2048)
            except StudioError as exc:
                raise HubFailure(409, "CHAT_DOCUMENT_MISMATCH", "This exact document revision or page is unavailable.") from exc
            stem = Path(document.file_name).stem or "drawing"
            return replace(document, file_name=f"{stem}-p{ref.pageIndex + 1}.png", mime_type=page.media_type,
                           size_bytes=len(page.content), asset_sha256=sha256(page.content).hexdigest()), page.content
        # The legacy byte reader treats null revision as a wildcard. Select exact metadata first;
        # bytes remain content-addressed and independently verified by their existing owner.
        _, data = document_bytes(binding, ref.runId, ref.assetSha256, ref.revisionRef)
        return document, data

    def presentation_document(self, session_id: str, message_id: str, index: int):
        with self._lock:
            session = self._session(session_id)
            message = next((row for row in session.messages if row.id == message_id), None)
            if message is None or not 0 <= index < len(message.documents):
                raise HubFailure(404, "CHAT_DOCUMENT_NOT_FOUND", "This document is not in this conversation.")
            return self._document(session, message.documents[index])

    def _external_results(self, session_id: str, request: ChatPresentationRequest) -> tuple[tuple[str, str, str], ...]:
        """The candidates an external turn made, read before the chat lock: the runtime may refresh."""
        if request.kind != "assistant" or request.status == "streaming" or self.turn_results is None:
            return ()
        with self._lock:
            session = self._sessions.get(session_id)
            asked = next((row for row in session.messages if row.role == "user" and row.sourceTurnId == request.turnId),
                         None) if session and session.sourceSessionId else None
            if asked is None:
                return ()
            project_dir = session.projectDir
        try:
            return tuple(self.turn_results(session_id, project_dir, asked.createdAt))
        except HubFailure:
            return ()  # A closed or unreadable runtime leaves the answer without a card, as before.

    def present(self, session_id: str, request: ChatPresentationRequest, token: str) -> ChatDetail:
        """Upsert a public result without starting a provider or writing project state."""
        results = self._external_results(session_id, request)
        with self._lock:
            session = self._session(session_id).model_copy(deep=True)
            expected = self._presentation_tokens.get(session_id)
            if not expected or not secrets.compare_digest(expected, token):
                raise HubFailure(403, "CHAT_PRESENTATION_BIND_REQUIRED", "Reconnect the bound presentation tool to this Hub.")
            if self._closing or session.archived:
                raise HubFailure(409, "CHAT_UNAVAILABLE", "Restore the conversation or reconnect after Hub restarts.")
            if (request.projectId != session.projectId or request.sourceSessionId != (session.sourceSessionId or f"hub:{session_id}")
                    or projects._project(session.projectDir) != (session.projectId, session.projectDir)):
                raise HubFailure(409, "CHAT_PRESENTATION_MISMATCH", "The presentation belongs to a different project or source session.")
            projects._identifier(request.messageId)
            projects._identifier(request.turnId)
            if request.kind == "user" and (not session.sourceSessionId or request.status != "complete"):
                raise HubFailure(422, "CHAT_PRESENTATION_INVALID", "Only an external source can start a turn with a complete user message.")
            if request.kind == "progress" and (request.attachments or request.documents):
                raise HubFailure(422, "CHAT_PRESENTATION_INVALID", "Progress is transient text; publish media as an assistant result.")
            if not request.content.strip() and not request.attachments and not request.documents and request.suggestion is None:
                raise HubFailure(422, "CHAT_MESSAGE_EMPTY", "Provide text or a result attachment/document.")
            progress_key = f"external:{request.messageId}"
            previous = (self._progress_rows.get(session_id, {}).get(progress_key) if request.kind == "progress" else
                        next((row for row in session.messages if row.id == request.messageId), None))
            content = providers._redact(request.content, providers._claude_env())
            # Every nested card string crosses the same public/persistent
            # boundary as message text, including the continuation prompt.
            def redact_card(value):
                if isinstance(value, str):
                    return providers._redact(value, providers._claude_env())
                if isinstance(value, list):
                    return [redact_card(item) for item in value]
                if isinstance(value, dict):
                    return {key: redact_card(item) for key, item in value.items()}
                return value
            suggestion = (ChatSuggestion.model_validate(redact_card(request.suggestion.model_dump()))
                          if request.suggestion is not None else None)
            if request.kind == "progress":
                content = content.strip()[-2400:]
            if previous:
                role = "tool" if request.kind == "progress" else request.kind
                if previous.sourceTurnId != request.turnId or previous.role != role or previous.presentationRevision is None:
                    raise HubFailure(409, "CHAT_PRESENTATION_CONFLICT", "This message id belongs to another item or turn.")
                if request.revision < previous.presentationRevision:
                    raise HubFailure(409, "CHAT_PRESENTATION_STALE", "An older presentation revision cannot replace this result.")
                if request.revision == previous.presentationRevision:
                    same_files = len(request.attachments) == len(previous.attachments) and all(
                        upload.name == stored.name and upload.mimeType == stored.mimeType and
                        upload.data == base64.b64encode(self._attachment_path(session_id, stored).read_bytes()).decode("ascii")
                        for upload, stored in zip(request.attachments, previous.attachments))
                    same_refs = [ref.model_dump() for ref in request.documents] == [
                        ChatDocumentRef.model_validate(ref.model_dump(include=set(ChatDocumentRef.model_fields))).model_dump()
                        for ref in previous.documents]
                    settled_stream = request.status == "streaming" and previous.status != "streaming" and session.status != "running"
                    if (previous.content == content and previous.suggestion == suggestion
                            and (previous.status == request.status or settled_stream) and same_files and same_refs):
                        return self.get(session_id)
                    raise HubFailure(409, "CHAT_PRESENTATION_CONFLICT", "A revision identifies one exact message snapshot.")
                if previous.status != "streaming" or request.kind == "user":
                    raise HubFailure(409, "CHAT_PRESENTATION_COMPLETE", "A completed message cannot be rewritten.")
            current_user = next((row for row in reversed(session.messages) if row.role == "user"), None)
            current_turn = current_user.sourceTurnId or current_user.id if current_user else None
            if request.kind == "user":
                if session.status == "running" or any(row.sourceTurnId == request.turnId for row in session.messages):
                    raise HubFailure(409, "CHAT_RUNNING", "Finish the active turn and use a new turn id for the next request.")
            elif session.status != "running" or current_turn != request.turnId:
                raise HubFailure(409, "CHAT_PRESENTATION_TURN_CLOSED", "Start a user turn or use the currently running turn id.")
            if request.kind == "progress":
                self._progress(session, progress_key, content, status=request.status)
                row = self._progress_rows[session_id][progress_key]
                row.sourceTurnId, row.presentationRevision = request.turnId, request.revision
                return self.get(session_id)
            attachments, writes = [], []
            total = 0
            for index, upload in enumerate(request.attachments):
                try:
                    data = base64.b64decode(upload.data, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise HubFailure(422, "CHAT_ATTACHMENT_INVALID", "The attached file could not be decoded.") from exc
                total += len(data)
                if len(data) > 20 * 1024 * 1024 or total > 40 * 1024 * 1024:
                    raise HubFailure(413, "CHAT_ATTACHMENT_TOO_LARGE", "Files are limited to 20 MiB each and 40 MiB per message.")
                if upload.mimeType in media._IMAGE_MIMES:
                    media._verify_image(data, upload.mimeType)
                old = previous.attachments[index] if previous and index < len(previous.attachments) else None
                if old:
                    if (old.name, old.mimeType) != (upload.name, upload.mimeType) or self._attachment_path(session_id, old).read_bytes() != data:
                        raise HubFailure(409, "CHAT_ATTACHMENT_CONFLICT", "Publish a changed image as a new message; existing attachments stay exact.")
                    attachment = old
                else:
                    attachment = ChatAttachment(id=str(uuid4()), name=upload.name, mimeType=upload.mimeType, size=len(data))
                    writes.append((attachment, data))
                attachments.append(attachment)
            if previous and len(attachments) < len(previous.attachments):
                raise HubFailure(409, "CHAT_ATTACHMENT_CONFLICT", "An update must retain existing attachments.")
            documents = []
            for ref in request.documents:
                document, _ = self._document(session, ref)
                documents.append(ChatDocument(**ref.model_dump(), fileName=document.file_name, mimeType=document.mime_type))
            message = ChatMessage(id=request.messageId, role=request.kind, content=content,
                                  createdAt=previous.createdAt if previous else _now(), status=request.status,
                                  sourceTurnId=request.turnId, presentationRevision=request.revision,
                                  suggestion=suggestion,
                                  attachments=attachments, documents=documents)
            if previous:
                session.messages[session.messages.index(previous)] = message
            else:
                session.messages.append(message)
            # #404 F15: the requests this external turn made through the Hub are its steps. Each
            # candidate one finished is a row the Study card reads, as a native turn's call is.
            shown = {row.candidateId for row in session.messages if row.sourceTurnId == request.turnId and row.candidateId}
            for candidate_id, kind, asked_at in results:
                if candidate_id not in shown:
                    shown.add(candidate_id)
                    session.messages.insert(session.messages.index(message), ChatMessage(
                        id=f"{request.turnId}:result:{candidate_id}", role="tool", createdAt=asked_at,
                        content=f"studio_request · {kind} · completed\ncandidateId: {candidate_id}",
                        sourceTurnId=request.turnId, candidateId=candidate_id))
            if request.kind == "user":
                session.status, session.error = "running", None
            elif session.sourceSessionId and request.status != "streaming":
                session.status = "idle" if request.status == "complete" else request.status
                for row in session.messages:
                    if row.sourceTurnId == request.turnId and row.status == "streaming":
                        row.status = request.status
            session.updatedAt = _now()
            self._save(session, tuple(writes))
            self._sessions[session_id] = session
            if request.kind == "user":
                self._progress_rows.pop(session_id, None)
            return self.get(session_id)

    def set_archived(self, session_id: str, archived: bool) -> ChatDetail:
        """Hide or restore a conversation without changing its native session."""
        with self._lock:
            session = self._session(session_id)
            if session_id in self._running or session.status == "running":
                raise HubFailure(409, "CHAT_RUNNING", "Wait for this reply to finish or stop it before archiving the chat.")
            if session.archived == archived:
                return self.get(session_id)
            updated = session.model_copy(update={"archived": archived, "updatedAt": _now()}, deep=True)
            if not archived:
                self._clear_scratch(session_id)  # A restored chat starts with an empty scratch.
            self._save(updated)
            self._sessions[session_id] = updated
            if archived:
                self._clear_scratch(session_id)
            return self.get(session_id)

    def create(self, request: ChatCreateRequest) -> ChatDetail:
        with self._lock:
            self._load()
            provider = next(row for row in self.providers() if row.id == request.provider)
            if not provider.available:
                raise HubFailure(503, "CHAT_PROVIDER_UNAVAILABLE", provider.detail)
            project_id, project_dir = projects._project(request.projectDir)
            now = _now()
            session = _SavedChat(
                id=str(uuid4()), projectId=project_id, projectDir=project_dir,
                title=request.title or "New chat", provider=request.provider, model=request.model,
                createdAt=now, updatedAt=now,
                transport="acp" if self._use_acp and request.provider == "codex" else "cli",
            )
            self._save(session)
            self._sessions[session.id] = session
            return self.get(session.id)

    @contextmanager
    def project_configuration(self, project_dir: str | None):
        """Hold the same lock across configuration changes and turn admission."""
        with self._lock:
            target = str(Path(project_dir).resolve()) if project_dir else None
            if any(self._sessions[key].projectDir != target for key in self._running):
                raise HubFailure(409, "CHAT_PROJECT_BUSY", "Stop the running chat before switching the active project.")
            yield

    @contextmanager
    def application_lifecycle(self, app_id: str, *, stopping: bool = False, project_dir: str | None = None):
        with self._lock:
            if app_id in {"monkeyarch", "monkeyboard"} and stopping:
                configured = project_dir if project_dir is not None else read_application_settings(self.runtime_root).project_dir
                target = str(Path(configured).resolve()) if configured else None
                if any(target is not None and os.path.normcase(self._sessions[key].projectDir) == os.path.normcase(target) for key in self._running):
                    raise HubFailure(409, "CHAT_RUNNING", "Stop the running chat before closing its design tools.")
            yield

    def _takes_messages(self, session) -> None:
        """Refuse a message this chat takes from no one; the lock is held."""
        if session.sourceSessionId:
            raise HubFailure(409, "CHAT_EXTERNAL_SOURCE", "Continue this conversation in its external source; Hub only displays its results.")
        if self._closing:
            raise HubFailure(409, "CHAT_CLOSING", "Hub is closing.")
        if session.archived:
            raise HubFailure(409, "CHAT_ARCHIVED", "Restore this archived chat before sending another message.")

    def post(self, session_id: str, request: ChatPostRequest) -> ChatDetail:
        images: list[ChatDocument] = []
        if request.renderContext is not None:
            with self._lock:
                bound = self._session(session_id)
                # A chat that takes no message says so before any image is read.
                self._takes_messages(bound)
                if request.projectId != bound.projectId:
                    raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "This message belongs to a different project.")
                project = bound.projectId, bound.projectDir
            # Outside the lock: checking the pages reads the project's documents,
            # and a refused page leaves nothing kept and nothing started.
            images = media._render_images(*project, request.renderContext)
        with self._lock:
            session = self._session(session_id).model_copy(deep=True)
            self._takes_messages(session)
            if request.projectId != session.projectId or projects._project(session.projectDir) != (session.projectId, session.projectDir):
                raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "This message belongs to a different project.")
            if request.suggestionSelection is not None:
                selection = request.suggestionSelection
                if any(row.role == "user" and row.suggestionSelection == selection for row in session.messages):
                    raise HubFailure(409, "CHAT_SUGGESTION_CONSUMED", "This suggestion has already been selected.")
                if session.status == "running" or session_id in self._running:
                    raise HubFailure(409, "CHAT_RUNNING", "Wait for this reply to finish before choosing a suggestion.")
                if session.status != "idle":
                    raise HubFailure(409, "CHAT_SUGGESTION_EXPIRED", "Suggestions can only continue an idle conversation.")
                card = next((row for row in session.messages if row.id == selection.messageId), None)
                user = next((row for row in reversed(session.messages) if row.role == "user"), None)
                turn = (user.sourceTurnId or user.id) if user else None
                latest = next((row for row in reversed(session.messages)
                               if row.role == "assistant" and row.suggestion is not None and row.sourceTurnId == turn), None)
                if (card is None or card.role != "assistant" or card.suggestion is None or card.status != "complete"
                        or card.presentationRevision != selection.revision or card.sourceTurnId != turn or card != latest):
                    raise HubFailure(409, "CHAT_SUGGESTION_EXPIRED", "Choose the latest completed suggestion for the current user turn.")
                # Only a retained prompt enters the ordinary post/start path.
                # Context remains native continuation, with no client source override.
                request = request.model_copy(update={"content": card.suggestion.prompt})
            if session_id in self._running:
                return self._interject(session_id, request)
            provider = next(row for row in self.providers() if row.id == session.provider)
            legacy_codex = session.provider == "codex" and session.transport == "cli" and provider.installed
            if not provider.available and not legacy_codex:
                raise HubFailure(503, "CHAT_PROVIDER_UNAVAILABLE", provider.detail)
            content = providers._redact(request.content.strip(), providers._claude_env())
            if not content and not request.attachments:
                raise HubFailure(422, "CHAT_MESSAGE_EMPTY", "Enter a message or attach a file.")
            attachments = []
            total = 0
            for upload in request.attachments:
                try:
                    data = base64.b64decode(upload.data, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise HubFailure(422, "CHAT_ATTACHMENT_INVALID", "The attached file could not be decoded.") from exc
                total += len(data)
                if len(data) > 20 * 1024 * 1024 or total > 40 * 1024 * 1024:
                    raise HubFailure(413, "CHAT_ATTACHMENT_TOO_LARGE", "Files are limited to 20 MiB each and 40 MiB per message.")
                attachments.append((ChatAttachment(id=str(uuid4()), name=upload.name, mimeType=upload.mimeType, size=len(data)), data))
            if not session.messages and session.title == "New chat":
                session.title = (content.splitlines()[0] if content else attachments[0][0].name)[:80]
            session.messages.append(ChatMessage(id=str(uuid4()), role="user", content=content, createdAt=_now(),
                                                contextMode="continue" if request.contextMode == "stage" else request.contextMode,
                                                suggestionSelection=request.suggestionSelection,
                                                attachments=[attachment for attachment, _ in attachments],
                                                documents=images))
            session.status, session.error, session.updatedAt = "running", None, _now()
            self._save(session, tuple(attachments))
            self._sessions[session_id] = session
            self._progress_rows.pop(session_id, None)
            running = _Running(design_context=request.designContext, context_mode=request.contextMode,
                               attachments=tuple((attachment, self._attachment_path(session.id, attachment))
                                                 for attachment, _ in attachments), render=tuple(images), trace=HubTurnObserver(
                self.usage_log, _turn_id(session), session.projectId, session.provider, session.model,
            ))
            self._start(session_id, running, content)
            return self.get(session_id)

    def _start(self, session_id: str, running: _Running, content: str, carried: tuple[str, ...] = (),
               earlier: tuple[str, ...] = ()) -> None:
        """Register one turn and start its thread; the caller holds the lock.

        A continuation also knows the turns before it, so a call they opened
        and that only reports its end now keeps its own row.
        """
        session = self._sessions[session_id]
        running.turns = [*earlier, *carried] or [_turn_id(session)]
        if session.transport == "cli" and session.provider != "codex":
            # Claude keeps reading stream-json user messages until its result.
            running.stdin = queue.Queue()
        self._running[session_id] = running
        running.thread = threading.Thread(target=self._run, args=(session_id, content, running, carried),
                                          daemon=True, name=f"hub-chat-{session_id[:8]}")
        running.thread.start()

    def _interject(self, session_id: str, request: ChatPostRequest) -> ChatDetail:
        """Take a message sent while this chat's turn runs (#301); the lock is held.

        It is kept like any other message and marked as an interjection. Claude
        reads it from the stdin of its running turn at its next step. Codex takes
        it through the adapter's steering when the adapter offers that, and
        otherwise the current step is cancelled and the turn continues with it
        as the next prompt. Whatever the running turn can no longer take becomes
        the next prompt when it ends; nothing is refused for being early.
        """
        running = self._running[session_id]
        if request.attachments:
            raise HubFailure(409, "CHAT_INTERJECTION_FILES", "Attach files after this reply finishes, or stop it first.")
        if request.renderContext is not None:
            # The running turn was bound to its own images; new ones start a turn of their own.
            raise HubFailure(409, "CHAT_INTERJECTION_IMAGES", "Send the selected images after this reply finishes, or stop it first.")
        content = providers._redact(request.content.strip(), providers._claude_env())
        if not content:
            raise HubFailure(422, "CHAT_MESSAGE_EMPTY", "Enter a message or attach a file.")
        # The running turn holds this same record and saves it as it goes, so
        # the message joins it in place rather than through a replacement copy.
        session = self._sessions[session_id]
        message = ChatMessage(id=str(uuid4()), role="user", content=content, createdAt=_now(), interjection="pending")
        session.messages.append(message)
        session.updatedAt = _now()
        try:
            self._save(session)
        except BaseException:
            session.messages.remove(message)
            raise
        cancel = None
        if running.stdin is not None:
            running.stdin.put(json.dumps({
                "type": "user", "session_id": session.nativeSessionId or session.cliStartId or session.id,
                "parent_tool_use_id": None, "uuid": message.id,
                "message": {"role": "user", "content": [{"type": "text", "text": content}]},
            }, ensure_ascii=False) + "\n")
            running.written.append(message.id)
        else:
            running.waiting.append((message.id, content))
            # A Codex CLI turn reads its prompt once and nothing after it.
            fixed = (session.transport == "cli" and session.provider == "codex"
                     and running.process is not None and running.process.poll() is None)
            if running.redirected:
                # The step is already being cancelled; this one follows it.
                message.interjection = "restarted"
                self._save(session)
            elif session.transport == "acp" and running.prompting:
                # Whether the adapter steers is known once its turn is under way;
                # the delivery thread waits for that and then steers or cancels.
                threading.Thread(target=self._steer, args=(session_id, running), daemon=True,
                                 name=f"hub-steer-{session_id[:8]}").start()
            elif fixed:
                cancel = self._redirect(session_id, running)
            # Otherwise the provider has not started yet, and its prompt takes
            # the message, or it has finished reading, and the next one does.
        detail = self.get(session_id)
        if cancel is not None:
            # Cancelling waits on the adapter's thread or on a process, which may
            # in turn wait for this store's lock, held here by the request.
            threading.Thread(target=cancel, daemon=True, name=f"hub-redirect-{session_id[:8]}").start()
        return detail

    def _redirect(self, session_id: str, running: _Running):
        """Stop the current step so the turn continues with its interjections.

        The lock is held. Work already reported stays in the conversation; only
        a permission still waiting is withdrawn, because the step it belonged to
        is ending. Returns what cancels the step, to call outside the lock.
        """
        running.redirected = True
        session = self._sessions[session_id]
        waiting = {identifier for identifier, _ in running.waiting}
        for message in session.messages:
            if message.id in waiting:
                message.interjection = "restarted"
        self._clear_permissions(session_id)
        self._save(session)
        client, process = self._acp_sessions.get(session_id), running.process
        if session.transport == "acp" and client is not None:
            return client.cancel
        if process is not None:
            return lambda: providers._stop_process(process)
        return None

    def _steer(self, session_id: str, running: _Running) -> None:
        """Offer this turn's waiting interjections to the adapter, one at a time and in order."""
        with running.steering:
            while True:
                with self._lock:
                    if (self._running.get(session_id) is not running or running.stop.is_set()
                            or running.redirected or not running.prompting or not running.waiting):
                        return
                    identifier, content = running.waiting[0]
                    client = self._acp_sessions.get(session_id)
                if client is None:
                    return
                outcome = client.steer(content, lambda identifier=identifier: self._handing(session_id, running, identifier))
                if outcome == "startedNewTurn":
                    # The turn ended as the message arrived and the adapter began
                    # a turn of its own for it, which no prompt of this Hub waits
                    # on. That turn is cancelled; the message goes as a prompt.
                    client.cancel_turn()
                cancel = None
                with self._lock:
                    session = self._sessions[session_id]
                    queued = any(waiting == identifier for waiting, _ in running.waiting)
                    running.waiting = [row for row in running.waiting if row[0] != identifier]
                    if outcome == "injected":
                        self._delivered(session, identifier)
                        continue
                    if not queued:
                        # The turn's end took it for the next prompt before it left.
                        return
                    if identifier in running.turns:
                        running.turns.remove(identifier)
                    message = next((row for row in session.messages if row.id == identifier), None)
                    if message is None or message.interjection == "undelivered":
                        # The turn already ended stopped or failed, and said so.
                        return
                    current = self._running.get(session_id)
                    message.interjection = "undelivered" if running.stop.is_set() or self._closing else "pending"
                    self._save(session)
                    if message.interjection == "undelivered":
                        return
                    if current is None:
                        # This turn has already ended: the message is the next prompt.
                        session.status, session.error = "running", None
                        self._save(session)
                        self._continue(session_id, [(identifier, content)], redirected=False,
                                       earlier=tuple(running.turns))
                        return
                    if current is not running:
                        # A later turn runs now; it takes the message as its own.
                        current.waiting.append((identifier, content))
                        if current.prompting:
                            threading.Thread(target=self._steer, args=(session_id, current), daemon=True,
                                             name=f"hub-steer-{session_id[:8]}").start()
                        return
                    # Still this turn: the message waits for its next prompt, or,
                    # without steering, the current step stops for it now.
                    running.waiting.insert(0, (identifier, content))
                    if outcome in {"failed", "unsupported"} and running.prompting and not running.redirected:
                        cancel = self._redirect(session_id, running)
                if cancel is not None:
                    cancel()
                return

    def _handing(self, session_id: str, running: _Running, identifier: str) -> bool:
        """Called on the adapter's thread as a steer is about to leave.

        From here what the Agent says answers the message. A turn that has
        already ended, or stopped, or taken the message for its next prompt,
        keeps it from leaving at all, so it can never arrive twice.
        """
        with self._lock:
            if (self._running.get(session_id) is not running or running.stop.is_set() or running.redirected
                    or not any(waiting == identifier for waiting, _ in running.waiting)):
                return False
            self._delivered(self._sessions[session_id], identifier)
            return True

    def _delivered(self, session: _SavedChat, identifier: str) -> None:
        """The Agent has this interjection; what it says next answers it. The lock is held."""
        message = next((row for row in session.messages if row.id == identifier and row.role == "user"), None)
        if message is None or message.interjection not in {"pending", "undelivered"}:
            return
        message.interjection = "delivered"
        running = self._running.get(session.id)
        if running is not None:
            if identifier in running.written:
                running.written.remove(identifier)
            if identifier not in running.turns:
                running.turns.append(identifier)
        session.updatedAt = _now()
        self._save(session)

    def _answering(self, session: _SavedChat) -> str:
        """The user message the running turn is answering: the last one the Agent has.

        An interjection counts from the moment it is handed over; until then,
        and for a step stopped on its account, what the Agent says still
        belongs to the message before it.
        """
        running = self._running.get(session.id)
        return running.turns[-1] if running is not None and running.turns else _turn_id(session)

    def _call_row(self, session: _SavedChat, call: str) -> str:
        """The row of one tool call: where it was opened, else under the current turn."""
        running = self._running.get(session.id)
        for turn in reversed(running.turns if running is not None else []):
            identifier = f"{turn}:{call}"
            if any(row.id == identifier for row in session.messages):
                return identifier
        return f"{self._answering(session)}:{call}"

    def _take_waiting(self, session_id: str, running: _Running, prompt: str) -> str:
        """Fold interjections sent before the provider started into its prompt. The lock is held."""
        if not running.waiting:
            return prompt
        session = self._sessions[session_id]
        for identifier, content in running.waiting:
            prompt += "\n\n" + content
            self._delivered(session, identifier)
        running.waiting.clear()
        return prompt

    def _tool_connection(self, session: _SavedChat) -> dict:
        return {"command": sys.executable, "args": [str(Path(mcp_server.__file__).resolve()), "--mcp",
                "--hub-url", self.hub_url, "--chat-id", session.id]}

    def _codex_mcp(self, session: _SavedChat, command, environment) -> dict:
        """Keep the same project-only MCP configuration for both native transports."""
        mcp = self._tool_connection(session)
        listing = subprocess.run(
            [*command, "-C", session.projectDir, "mcp", "list", "--json"],
            cwd=session.projectDir, env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            servers = json.loads(listing.stdout)
            if listing.returncode or not isinstance(servers, list):
                raise ValueError("invalid MCP listing")
            mcp_servers = {}
            for row in servers:
                kind_name = row["transport"]["type"]
                if kind_name == "stdio":
                    transport = mcp
                elif kind_name == "streamable_http":
                    transport = {"url": self.hub_url}
                else:
                    raise ValueError("unsupported MCP transport")
                mcp_servers[row["name"]] = {**transport, "enabled": False, "required": False}
        except (ValueError, KeyError, TypeError) as exc:
            raise HubFailure(503, "CHAT_CONFIG_INVALID", "The installed Codex MCP configuration could not be read.") from exc
        from .. import computer_tools

        tool_names = ("studio_schema", "studio_request", "visual_review", "fab_request", "attachment_read",
                      "chat_present", *computer_tools.TOOL_NAMES)
        mcp_servers["monkeyhub"] = {
            **mcp, "enabled": True, "required": True,
            "env_vars": ["MONKEYHUB_PRESENTATION_TOKEN"],
            "enabled_tools": list(tool_names),
            "tools": {name: {"approval_mode": "approve"} for name in tool_names},
        }
        return mcp_servers

    def _command(self, session: _SavedChat, attachments: tuple[tuple[ChatAttachment, Path], ...] = (),
                 library: Callable[[], skill_plugins.Library | None] | None = None) -> tuple[list[str], dict[str, str]]:
        commands = self.commands if self.commands is not None else providers._cli_commands()
        kind = "codex" if session.provider == "codex" else "claude"
        environment = providers._claude_env() if kind == "claude" else dict(os.environ)
        plan = providers._coding_plan_env() if session.provider == "coding-plan" else {}
        if plan:
            # The saved endpoint takes this conversation, with its own token only:
            # an Anthropic key of the architect's must not travel to a third party.
            environment.pop("ANTHROPIC_API_KEY", None)
            environment.update(plan)
        environment["MONKEYHUB_PRESENTATION_TOKEN"] = self.presentation_token(session.id)
        if kind == "claude":
            # Project memory has one owner, studio.memory (#252). Claude Code's
            # own auto-memory would keep agent-written notes of its own under
            # ~/.claude/projects/<folder>/memory/ and, in a source checkout,
            # hand the chat the developer's MEMORY.md; a Hub chat has neither.
            environment["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        mcp = self._tool_connection(session)
        model = session.model
        # The assistant works where the work is: this Hub's own source tree when
        # it is a checkout, with the bound project writable beside it, so a
        # candidate can be produced and the code that produced it can be fixed.
        source = providers._source_checkout()
        workdir = str(source) if source is not None else session.projectDir
        if kind == "codex":
            # Let Codex read its native profile, provider and credentials.
            # Its table overrides merge, so disable the effective MCP names
            # explicitly. Never copy credential-bearing config into argv.
            mcp_servers = self._codex_mcp(session, commands[kind], environment)
            command = [*commands[kind], "exec", "--json", "--skip-git-repo-check",
                       "-C", workdir, "-s", "workspace-write", "--color", "never",
                       *(("--add-dir", session.projectDir) if workdir != session.projectDir else ()),
                       "-c", f"mcp_servers={providers._toml_value(mcp_servers)}"]
            if model:
                command += ["-m", model]
            if session.nativeSessionId:
                command += ["resume", session.nativeSessionId]
            for attachment, path in attachments:
                if attachment.mimeType in media._IMAGE_MIMES:
                    command += ["--image", str(path)]
            command.append("-")
        else:
            scratch = self._scratch_path(session.id)
            # The library project's skills, as a plugin Claude loads natively
            # (#252), and no other skill (#463). Under dontAsk the CLI (2.1.283)
            # does not gate its Skill tool, so an allow rule restricts nothing.
            # User settings are never read (no personal skills, installed
            # plugins or their hooks); only their sign-in and network keys are
            # carried. With no library the chat has no Skill tool at all; with
            # one, --settings also turns off the bundled skills and every
            # leftover one. The turn hands the index it already read for its recipes.
            current = (library or self._library)()
            skills = skill_plugins.plugin_dir(self.runtime_root, current)
            if current is None:
                carried = skill_plugins.carried_settings(providers._claude_user_settings())
                only_library = ("--disable-slash-commands", "--setting-sources", "project,local",
                                *(("--settings", json.dumps(carried, ensure_ascii=False)) if carried else ()))
            else:
                only_library = (*(("--plugin-dir", str(skills)) if skills is not None else ()),
                                "--setting-sources", "project,local", "--settings",
                                skill_plugins.claude_settings(self.runtime_root, providers._claude_user_settings()))
            command = [*commands[kind], "-p", "--output-format", "stream-json", "--verbose",
                       "--include-partial-messages", "--permission-mode", "dontAsk", "--permission-prompts", "none",
                       # Two different questions, and both have to be answered.
                       # `--tools` is what exists; `--allowedTools` is what is
                       # approved without anybody to ask, which is the whole of
                       # it with no prompt attached. Named rather than bypassed:
                       # reading, editing and running in the workspace above,
                       # plus this adapter's own tools and nothing else.
                       "--tools", "default", "--allowedTools", ",".join(providers._claude_approved(self.runtime_root)),
                       *(("--add-dir", session.projectDir) if workdir != session.projectDir else ()),
                       *only_library,
                       "--add-dir", str(scratch),
                       # alwaysLoad: the CLI otherwise defers every MCP tool behind
                       # a ToolSearch round trip, one model call before any design work.
                       "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": {"monkeyhub": {**mcp, "alwaysLoad": True}}})]
            command += ["--resume", session.nativeSessionId] if session.nativeSessionId else ["--session-id", session.cliStartId or session.id]
            # Every turn reads stream-json from stdin, so a message sent while
            # it runs reaches it at its next step (#301). Each one read is
            # echoed back, which is how the conversation knows it arrived.
            command += ["--input-format", "stream-json", "--replay-user-messages"]
            if model:
                command += ["--model", model]
        return command, environment

    def _library(self) -> skill_plugins.Library | None:
        return skill_plugins.configured_library(self.runtime_root, self.hub_url)

    def _turn_library(self) -> Callable[[], skill_plugins.Library | None]:
        """The configured library, read at most once for one turn: its recipes and its plugin share it."""

        held: list = []

        def read() -> skill_plugins.Library | None:
            if not held:
                try:
                    held.append(self._library())
                except HubFailure as failure:
                    held.append(failure)
            if isinstance(held[0], HubFailure):
                raise held[0]
            return held[0]
        return read

    def _recipe_skills(self, session: _SavedChat, rows, library: Callable[[], skill_plugins.Library | None]) -> None:
        """Say, on every recipe this turn carries, which skill to load and whether its version is current.

        Only a turn that carries a recipe reads the library for it. A library
        that cannot be read is said on the recipe; the turn still runs.
        """

        recipes = [row for row in rows or () if isinstance(row, dict)
                   and isinstance(row.get("memory"), dict) and row["memory"].get("kind") == "recipe"]
        if not recipes:
            return
        try:
            current = library()
        except HubFailure as failure:
            for row in recipes:
                row["skill"] = {"load": None, "pinned": (row["memory"].get("value") or {}).get("skill"),
                                "libraryVersion": None, "note": f"{failure.error.detail} Tell the user."}
            return
        for row in recipes:
            row["skill"] = skill_plugins.pinned_status(current, str((row["memory"].get("value") or {}).get("skill")),
                                                       loadable=session.provider == "claude")

    def _acp_permission(self, session_id: str, request: dict) -> Future:
        future = Future()
        with self._lock:
            session = self._session(session_id)
            running = self._running.get(session_id)
            if (self._closing or running is None or running.stop.is_set() or running.redirected
                    or request.get("sessionId") != session.acpSessionId):
                future.set_result(None)
                return future
            permission = ChatPermission(
                id=str(uuid4()), title=providers._redact(str(request.get("toolCall", {}).get("title") or "Permission requested")),
                options=[ChatPermissionOption.model_validate(item) for item in request["options"]],
            )
            session.messages.append(ChatMessage(
                id=f"{self._answering(session)}:permission:{permission.id}", role="tool", content=permission.title,
                createdAt=_now(), status="streaming", permission=permission,
            ))
            # A request is a change of its own (#300): two requests in a row are
            # two moments in the summary, never one.
            session.updatedAt = _now()
            self._permissions[(session_id, permission.id)] = future
            if running.trace:
                running.trace.permission(permission.id)
            self._save(session)
        return future

    def resolve_permission(self, session_id: str, permission_id: str, request: ChatPermissionRequest) -> ChatDetail:
        with self._lock:
            session = self._session(session_id)
            if request.projectId != session.projectId or projects._project(session.projectDir) != (session.projectId, session.projectDir):
                raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "This permission belongs to a different project.")
            future = self._permissions.get((session_id, permission_id))
            message = next((row for row in session.messages if row.permission and row.permission.id == permission_id), None)
            if future is None or future.done() or message is None or session_id not in self._running:
                raise HubFailure(409, "CHAT_PERMISSION_EXPIRED", "This permission request is no longer waiting for a decision.")
            option = next((item for item in message.permission.options if item.optionId == request.optionId), None)
            if request.optionId is not None and option is None:
                raise HubFailure(422, "CHAT_PERMISSION_OPTION_INVALID", "Choose an option from this permission request.")
            try:
                future.set_result(request.optionId)
            except InvalidStateError:
                message.permission, message.status = None, "interrupted"
                session.updatedAt = _now()
                self._permissions.pop((session_id, permission_id), None)
                self._save(session)
                raise HubFailure(409, "CHAT_PERMISSION_EXPIRED", "This permission request is no longer waiting for a decision.") from None
            message.content += " · " + (option.name if option else "Cancelled")
            active = self._running[session_id]
            if active.trace:
                active.trace.permission(permission_id, completed=True)
            message.permission, message.status = None, "complete"
            session.updatedAt = _now()
            self._save(session)
            self._permissions.pop((session_id, permission_id))
            return self.get(session_id)

    def _clear_permissions(self, session_id: str) -> None:
        for key, future in list(self._permissions.items()):
            if key[0] == session_id:
                self._permissions.pop(key)
                with suppress(InvalidStateError):
                    future.set_result(None)
        for message in self._sessions[session_id].messages:
            if message.permission is not None:
                message.permission, message.status = None, "interrupted"

    def _acp_update(self, session_id: str, event: dict, environment: dict) -> None:
        with self._lock:
            session = self._session(session_id)
            running = self._running.get(session_id)
            if running is None or running.stop.is_set() or event.get("sessionId") != session.acpSessionId:
                return
            update = event["update"]
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk" and update.get("content", {}).get("type") == "text":
                if running.trace and update["content"].get("text"):
                    running.trace.first_response()
                identifier = f"{self._answering(session)}:acp-answer"
                message = next((row for row in session.messages if row.id == identifier), None)
                if message is None:
                    message = ChatMessage(id=identifier, role="assistant", content="", createdAt=_now(), status="streaming")
                    session.messages.append(message)
                message.content += providers._redact(update["content"]["text"], environment)
            elif kind in {"tool_call", "tool_call_update"}:
                call_id = update["toolCallId"]
                call = self._acp_tools.setdefault(session_id, {}).setdefault(call_id, {})
                call.update(update)
                raw_input = call.get("rawInput") if isinstance(call.get("rawInput"), dict) else {}
                raw_output = call.get("rawOutput") if isinstance(call.get("rawOutput"), dict) else {}
                mcp = call.get("_meta", {}).get("is_mcp_tool_call") is True
                item = {
                    "id": call_id, "server": raw_input.get("server") if mcp else None,
                    "tool": raw_input.get("tool") if mcp else call.get("title", "tool"),
                    "arguments": raw_input.get("arguments") if mcp else raw_input,
                    "status": call.get("status", "in_progress"),
                    "result": raw_output.get("result") if mcp else call.get("rawOutput"),
                    "error": raw_output.get("error") if mcp else None,
                }
                self._tool_message(session, item, "item.started" if item["status"] in {"pending", "in_progress"} else "item.completed", environment)
            else:
                return
            session.updatedAt = _now()
            now = time.monotonic()
            if now - running.last_save >= 0.3:
                self._save(session)
                running.last_save = now

    def _run_acp(self, session_id: str, prompt: str, running: _Running) -> HubError | None:
        from .acp_session import AcpCancelled, CodexAcpSession

        environment = dict(os.environ)
        environment["MONKEYHUB_PRESENTATION_TOKEN"] = self.presentation_token(session_id)
        try:
            with self._lock:
                session = self._sessions[session_id]
                client = self._acp_sessions.get(session_id)
                # A continuation keeps what the cancelled step's calls said so far.
                self._acp_tools.setdefault(session_id, {})
            if client is None:
                commands = self.commands if self.commands is not None else providers._cli_commands()
                environment["CODEX_PATH"] = providers._native_codex(commands["codex"])
                # In this pinned adapter, read-only means workspace-write with
                # user approvals. Its default agent mode uses auto-review.
                environment["INITIAL_AGENT_MODE"] = "read-only"
                environment["CODEX_CONFIG"] = json.dumps({
                    "mcp_servers": self._codex_mcp(session, commands["codex"], environment),
                    "sandbox_workspace_write": {"writable_roots": [session.projectDir]},
                })
                client = CodexAcpSession(
                    command=self._acp_command, cwd=str(providers._source_checkout() or session.projectDir),
                    environment=environment,
                    # Nonempty ACP mcpServers would replace the disabled entries
                    # above. This per-chat adapter already has the exact binding.
                    mcp_servers=[], default_model=session.acpDefaultModel,
                    on_update=lambda event: self._acp_update(session_id, event, environment),
                    on_permission=lambda request: self._acp_permission(session_id, request),
                )
                with self._lock:
                    self._acp_sessions[session_id] = client
            if running.stop.is_set():
                raise AcpCancelled("The chat was stopped before its turn was sent.")

            def connected(identifier: str) -> None:
                with self._lock:
                    session.acpSessionId = identifier
                    session.acpDefaultModel = client.default_model
                    if running.trace:
                        running.trace.bind(identifier, session.model or client.default_model)
                    self._save(session)

            # This adapter's own limit is an inactivity interval that its updates
            # reschedule, not a total for the turn. It keeps the whole of it.
            images = tuple((attachment.mimeType, base64.b64encode(path.read_bytes()).decode("ascii"))
                           for attachment, path in running.attachments if attachment.mimeType in media._IMAGE_MIMES)
            with self._lock:
                # What was sent before the prompt left goes with it; what is
                # sent after is steered into it or continues after it.
                prompt = self._take_waiting(session_id, running, prompt)
                running.prompting = True
            try:
                client.prompt(prompt, session.acpSessionId, session.model, connected, self.timeout_s, images=images)
            finally:
                with self._lock:
                    running.prompting = False
            return None
        except AcpCancelled:
            if not running.redirected:
                running.stop.set()
            return None
        except Exception as exc:
            with self._lock:
                client = self._acp_sessions.pop(session_id, None)
            if client is not None:
                client.close()
            return HubError(code="CHAT_ACP_FAILED", detail=providers._redact(str(exc), environment)[:1000])

    def _fresh_provider_session(self, session_id: str, *, stage: dict | None = None,
                                automatic: bool = False) -> _SavedChat:
        """Replace provider continuity only after this turn's source was verified."""
        with self._lock:
            session = self._sessions[session_id].model_copy(deep=True)
            identifier = session.acpSessionId if session.transport == "acp" else session.nativeSessionId
            if identifier and identifier not in session.priorProviderSessionIds:
                session.priorProviderSessionIds.append(identifier)
            session.nativeSessionId = None
            session.acpSessionId = None
            # Claude requires a fresh UUID even before it reports a native ID.
            session.cliStartId = str(uuid4())
            # A manual reset replaces the provider, not the boundary already
            # handled: without a named Stage the recorded one still stands, so
            # returning to it does not rotate the provider a second time.
            if stage is not None:
                session.providerStageRef = stage["stageRef"]
            if automatic:
                message = _asked(session)
                message.contextMode = "stage"
                message.confirmedStageRef = stage["stageRef"]
                message.confirmedStageLabel = stage["label"]
            self._save(session)
            self._sessions[session_id] = session
            client = self._acp_sessions.pop(session_id, None)
        if client is not None:
            client.close()
        return session

    def _retained_continuity(self, session: _SavedChat) -> dict:
        """What a rotation is about to replace, kept in case nothing replaces it."""
        boundary = _asked(session)
        return {
            "session": {"nativeSessionId": session.nativeSessionId, "acpSessionId": session.acpSessionId,
                        "cliStartId": session.cliStartId, "acpDefaultModel": session.acpDefaultModel,
                        "providerStageRef": session.providerStageRef,
                        "priorProviderSessionIds": list(session.priorProviderSessionIds)},
            "message": None if boundary is None else (boundary.id, {
                "contextMode": boundary.contextMode, "confirmedStageRef": boundary.confirmedStageRef,
                "confirmedStageLabel": boundary.confirmedStageLabel}),
        }

    def _restore_continuity(self, session: _SavedChat, retained: dict):
        """Put back continuity a replacement took away and then never replaced.

        Only what the rotation itself wrote is undone: this turn's messages,
        outcome and error are its own and stay exactly as they happened.
        """
        for field, value in retained["session"].items():
            setattr(session, field, value)
        if retained["message"] is not None:
            identifier, marks = retained["message"]
            message = next((row for row in session.messages if row.id == identifier), None)
            if message is not None:
                for field, value in marks.items():
                    setattr(message, field, value)
        # The adapter opened for the replacement negotiated no session of its
        # own, and it cannot be asked to load the restored one: it is given up
        # here, and the next turn loads that identity on an adapter of its own.
        return self._acp_sessions.pop(session.id, None)

    def _run(self, session_id: str, content: str, running: _Running, carried: tuple[str, ...] = ()) -> None:
        error: HubError | None = None
        retained: dict | None = None
        stderr: list[str] = []
        completed_turn = False
        # When this turn's own limit is spent. Preparing a named source is bound
        # by it and cancellable within it, and the CLI transport — which holds
        # one total limit for the turn — gets what preparing left rather than a
        # fresh limit beside it. The ACP adapter keeps its own inactivity
        # interval, which this is not and cannot stand in for.
        deadline = time.monotonic() + self.timeout_s
        try:
            with self._lock:
                session = self._sessions[session_id]
                prompt = (
                    "You are the assistant in MonkeyHub. Reply in the user's language. "
                    "Develop the user's design through reversible candidates. Batch steps whose outcome is already clear; "
                    "generate and inspect a candidate when the next design decision depends on its result, then revise as needed. "
                    "Check the actual result against the user's spatial intent before reporting completion; distinguish "
                    "geometry readback from visual inspection. Judge a spatial or formal result with visual_review, which "
                    "delivers exact images for you to inspect within a small allowance for each user message; only explicit "
                    "delivery=observation uses the separately configured structured provider and returns finding ids; check a "
                    "deterministic edit by readback without looking, and ask the user about a finding marked escalate "
                    "rather than spending another review on it. Close each completed loop with one admission that lists the "
                    "attempts each result superseded, such as a redone first try; for a request for several alternatives, "
                    "declare its Study with an id and label from the request and admit each finished alternative. Never "
                    "admit intermediate runs. Drawing-only work finishes with its registered documents and exact pages: "
                    "do not query or create a model admission for an unchanged source. Reject a result, or continue from one, only when the user's own words say so; "
                    "the chat binds them to that message. Choose suitable modeling methods and reasonable reversible "
                    "defaults, stating material assumptions. Ask when a design choice needs the user's judgment. "
                    "Use the connected monkeyhub tools for project queries and changes: studio_request lists entry points "
                    "and studio_schema supplies their contracts on demand. Refresh evidence when it has changed or is unclear. "
                    "Preserve the exact editing base, object identity and the user's keep conditions; use existing controls "
                    "and dependencies for linked edits. Correct recoverable errors and continue; explain a concrete limit "
                    "when the available tools cannot preserve the requested design meaning. "
                    "Project files are read-only: use Studio/P036 for changes rather than editing project.json, HEAD, input, runs or records. "
                    "Show reversible candidates; do not claim approval, issuance or printer upload. "
                    f"This conversation is bound to project {session.projectId} at {session.projectDir}. "
                    + (
                        f"You are working in this Hub's own source checkout at {providers._source_checkout()}, which is where "
                        "you start and what you may read, change and run: its rules are in AGENTS.md, its services "
                        "start from apps/monkeyhub/run.py and services/project-runtime, and the design tools you call "
                        "are the ones running from it. Project data is written only through those tools and the "
                        "P036 interfaces, never by editing the project's own files. "
                        if providers._source_checkout() is not None else
                        "This Hub runs from an installed bundle rather than a source checkout, so there is no code "
                        "here for you to change; say so instead of describing an edit you cannot make. "
                    ) +
                    (
                        f"Use this chat's writable scratch directory at {self._scratch_path(session.id)} for "
                        "temporary calculation scripts and derived working files; Write, Edit and Bash are available "
                        "there. Use this directory rather than the CLI's protected .claude directory. Scratch files "
                        "are not project records; save design results through the connected tools and P036. "
                        if session.provider != "codex" else ""
                    ) +
                    "Do not switch Hub configuration, open a different project, or guess a service URL. "
                    "If a required domain action is unavailable, say what cannot be done. "
                    + guides._SUGGESTION_INSTRUCTIONS + "\n\n"
                    + content
                )
                # Where the request itself begins: a note about the tools goes
                # before it, beside the envelope's other sentences about them.
                request_at = len(prompt) - len(content)
            if running.attachments:
                prompt += ("\n\nFiles attached to this message (read-only reference material; prefer attachment_read with "
                           "attachmentId=id. Follow nextOffset for remaining content and page for PDF pages. "
                           "PDF reads extract text only; empty text does not mean the page image was inspected. "
                           "Do not use Get-Content on these paths; native Windows sandbox access may be denied. "
                           "Paths are retained for complex formats that need explicitly authorized local processing):\n")
                prompt += json.dumps([{"id": attachment.id, "name": attachment.name, "mimeType": attachment.mimeType, "path": str(path)}
                                      for attachment, path in running.attachments], ensure_ascii=False)
            if running.render:
                # The exact pages and the roles the user gave them; the Agent
                # looks at each through the registered-page export (#253).
                prompt += "\n\n" + turn_context._RENDER_NOTE + "\n" + providers._redact(json.dumps([
                    {"role": row.role, "fileName": row.fileName, "mimeType": row.mimeType,
                     "page": {"runId": row.runId, "assetSha256": row.assetSha256,
                              "revisionRef": row.revisionRef, "pageIndex": row.pageIndex}}
                    for row in running.render], ensure_ascii=False))
            prepared = None
            library = self._turn_library()
            if running.design_context is not None:
                if running.stop.is_set():
                    return
                try:
                    # Outside the lock deliberately: preparing this reads the
                    # Hub's own session over HTTP, and that read takes the same
                    # lock this turn would still be holding.
                    prepared = turn_context._prepared_context(self.hub_url, session_id, content,
                                                              running.design_context, running.stop, deadline)
                except (HubFailure, OSError, ValueError, TimeoutError) as cause:
                    # The preparation refused, or could not be read. That is
                    # this turn's answer, in the words the refusal came with. No
                    # provider starts, and no other source is tried to get one
                    # started anyway.
                    error = (cause.error if isinstance(cause, HubFailure)
                             else HubError(code="CHAT_TOOL_FAILED", detail=transport._reason(cause)))
                    return
                if prepared is None:
                    # Stopped, or this turn's limit ran out while it waited. The
                    # read is abandoned either way: no provider starts on a turn
                    # that is already over, and the answer it may still produce
                    # belongs to nothing.
                    if not running.stop.is_set():
                        error = HubError(code="CHAT_TIMEOUT", detail="This turn's time limit ran out while "
                                         "its selected context was being prepared.")
                    return
                if isinstance(prepared, dict):
                    self._recipe_skills(session, prepared.get("memory"), library)
                prompt += "\n\n" + turn_context._CONTEXT_NOTE + "\n" + providers._redact(json.dumps(prepared, ensure_ascii=False))
            else:
                # Memory is the project's, not the design state's: a turn with
                # no design context still gets what its words are about. The
                # prepared context above already carries it as ContextPack.memory.
                if running.stop.is_set():
                    return
                try:
                    remembered = turn_context._project_memory(self.hub_url, session_id, content, running.stop, deadline)
                except Exception as cause:  # noqa: BLE001 - any failed read is reported the same way
                    # Memory informs the turn; it is not what the turn needs to
                    # run. The provider still starts, told plainly what is missing.
                    remembered = cause
                if remembered is None:
                    if running.stop.is_set():
                        return
                    remembered = TimeoutError("this turn's time limit ran out while it was being read")
                if isinstance(remembered, BaseException):
                    # The request stays the last thing said when nothing was found.
                    prompt = (prompt[:request_at]
                              + f"Project memory could not be read for this turn: {transport._reason(remembered)}\n\n"
                              + prompt[request_at:])
                elif remembered:
                    self._recipe_skills(session, remembered, library)
                    prompt += ("\n\n" + turn_context._MEMORY_NOTE + "\n"
                               + providers._redact(json.dumps(remembered, ensure_ascii=False, separators=(",", ":"))))
            if running.stop.is_set():
                return
            stage = (prepared or {}).get("confirmedStage")
            # A candidate's inherited Stage is context, not a new boundary.
            # Only the exact committed source may replace provider history.
            stage = stage if isinstance(stage, dict) and stage.get("isSource") is True else None
            automatic = (running.context_mode == "stage" and stage is not None
                         and stage["stageRef"] != session.providerStageRef)
            if running.context_mode == "project" or automatic:
                if time.monotonic() >= deadline:
                    error = HubError(code="CHAT_TIMEOUT", detail="The turn expired before a new provider session could start.")
                    return
                with self._lock:
                    retained = self._retained_continuity(self._sessions[session_id])
                session = self._fresh_provider_session(session_id, stage=stage, automatic=automatic)
                prompt += ("\n\nThis is a new provider session reconstructed from the project state above. "
                           "Previous chat messages are not included; use the current request and project evidence.")
            with self._lock:
                # A continuation's own interjections are its prompt: the Agent
                # has them as soon as it starts.
                for identifier in carried:
                    self._delivered(self._sessions[session_id], identifier)
            if session.transport == "acp":
                if running.trace:
                    running.trace.ready()
                error = self._run_acp(session_id, prompt, running)
                return
            if time.monotonic() >= deadline:
                # The CLI transport holds one total limit for the turn, so a
                # budget preparing already spent starts no process: a CLI given
                # no time would be reported as its own failure rather than as
                # the limit it actually ran into.
                error = HubError(code="CHAT_TIMEOUT", detail="This turn's time limit was spent before "
                                 "the CLI could be started.")
                return
            command, environment = self._command(session, running.attachments, library)
            running.library_skills = "--setting-sources" in command and "--disable-slash-commands" not in command
            if running.trace:
                running.trace.bind(session.nativeSessionId, session.model)
                running.trace.ready()
            if running.stop.is_set():
                return
            with self._lock:
                prompt_input = prompt = self._take_waiting(session_id, running, prompt)
                channel = running.stdin
            if channel is not None:
                blocks = [{"type": "text", "text": prompt}]
                blocks.extend({"type": "image", "source": {"type": "base64", "media_type": attachment.mimeType,
                               "data": base64.b64encode(path.read_bytes()).decode("ascii")}}
                              for attachment, path in running.attachments if attachment.mimeType in media._IMAGE_MIMES)
                prompt_input = json.dumps({"type": "user", "session_id": session.nativeSessionId or session.cliStartId or session.id,
                    "parent_tool_use_id": None, "message": {"role": "user", "content": blocks}}, ensure_ascii=False) + "\n"
            kwargs = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
            process = subprocess.Popen(command, cwd=providers._source_checkout() or session.projectDir, env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                       errors="replace", shell=False, **kwargs)
            with self._lock:
                running.process = process
            if running.stop.is_set():
                providers._stop_process(process)

            def feed():
                try:
                    process.stdin.write(prompt_input)
                    process.stdin.flush()
                    # Claude keeps reading: each interjection is one more
                    # stream-json user message, until the turn's result.
                    while channel is not None and (line := channel.get()) is not None:
                        process.stdin.write(line)
                        process.stdin.flush()
                    process.stdin.close()
                except (OSError, ValueError):
                    pass

            def read_errors():
                for line in process.stderr:
                    stderr.append(providers._redact(line, environment)[-2000:])
                    del stderr[:-8]

            feeder = threading.Thread(target=feed, daemon=True)
            errors = threading.Thread(target=read_errors, daemon=True)
            feeder.start()
            errors.start()
            timer = threading.Timer(max(0.0, deadline - time.monotonic()),
                                    lambda: providers._stop_process(process) if process.poll() is None else None)
            timer.daemon = True
            timer.start()
            last_save = 0.0
            try:
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    with self._lock:
                        # A media presentation can atomically replace the saved chat between CLI events.
                        session = self._sessions[session_id]
                        event_error, finished = self._event(session, event, environment)
                        completed_turn = completed_turn or finished
                        if event_error:
                            error = event_error
                        if finished:
                            # After its result Claude answers what it has already
                            # read and exits once stdin closes; anything sent
                            # from here on is the next prompt.
                            self._close_input(running)
                        if time.monotonic() - last_save >= 0.3:
                            self._save(session)
                            last_save = time.monotonic()
                process.wait()
            finally:
                with self._lock:
                    self._close_input(running)
                timer.cancel()
                if process.poll() is None:
                    providers._stop_process(process)
                process.wait(timeout=10)
                feeder.join(timeout=1)
                errors.join(timeout=1)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            if not (running.stop.is_set() or running.redirected):
                if time.monotonic() >= deadline:
                    error = HubError(code="CHAT_TIMEOUT", detail="The CLI did not finish within this turn's time limit.")
                elif process.returncode and error is None:
                    detail = "".join(stderr).strip()[-1000:] or f"The CLI exited with code {process.returncode}."
                    error = HubError(code="CHAT_PROCESS_FAILED", detail=detail)
                elif not completed_turn and error is None:
                    error = HubError(code="CHAT_INCOMPLETE", detail="The CLI stopped without completing this turn.")
        except Exception as exc:
            error = HubError(code="CHAT_PROCESS_FAILED", detail=providers._redact(str(exc), providers._claude_env())[:1000] or "The installed CLI could not run.")
        finally:
            abandoned = None
            with self._lock:
                session = self._sessions[session_id]
                stopped = running.stop.is_set()
                halted = stopped or running.redirected
                self._clear_permissions(session_id)
                # What Claude read without echoing it back still reached it when
                # its turn finished normally; a stopped or failed turn read none.
                for identifier in list(running.written):
                    if not (stopped or error):
                        self._delivered(session, identifier)
                # Interjections the turn could no longer take are the next prompt,
                # unless the turn was stopped or failed or the Hub is closing.
                # A steer that left just as the turn ended was handed over already.
                handed = {row.id for row in session.messages if row.interjection == "delivered"}
                follow = [] if stopped or error or self._closing else [
                    (identifier, text) for identifier, text in running.waiting if identifier not in handed]
                following = {identifier for identifier, _ in follow}
                # Taken here, a message is never routed again by a steer that
                # finishes late; one handed over is left for that steer to settle.
                running.waiting = [row for row in running.waiting if row[0] in handed]
                for message in session.messages:
                    if message.interjection == "pending" and message.id not in following:
                        message.interjection = "undelivered"
                if not follow:
                    self._acp_tools.pop(session_id, None)
                session.status = "running" if follow else "interrupted" if stopped else "failed" if error else "idle"
                session.error = HubError(code="CHAT_STOPPED", detail="The response was stopped.") if stopped else error
                for message in session.messages:
                    if message.status == "streaming":
                        message.status = "interrupted" if halted else "failed" if error else "complete"
                session.updatedAt = _now()
                if running.trace:
                    running.trace.bind(session.acpSessionId if session.transport == "acp" else session.nativeSessionId)
                    running.trace.finish("cancelled" if halted else "failed" if error else "succeeded")
                # A replacement that never reported an identity of its own is no
                # replacement: the chat would be left with nothing to continue
                # from at all, so the continuity this turn set aside goes back. A
                # provider that did open its own session keeps it, whether or not
                # the turn it was opened for then failed. The trace above already
                # recorded what this turn itself reached, which is nothing.
                if retained is not None and session.nativeSessionId is None and session.acpSessionId is None:
                    abandoned = self._restore_continuity(session, retained)
                try:
                    self._save(session)
                finally:
                    self._running.pop(session_id, None)
                    if follow:
                        self._continue(session_id, follow, redirected=running.redirected, earlier=tuple(running.turns))
            if abandoned is not None:
                # Closing hands work to the adapter's own thread and waits for
                # it; that never happens while this store's lock is held.
                abandoned.close()

    def _close_input(self, running: _Running) -> None:
        """Close Claude's stdin once its turn has a result. The lock is held."""
        if running.stdin is not None:
            running.stdin.put(None)
            running.stdin = None

    def _continue(self, session_id: str, follow: list[tuple[str, str]], *, redirected: bool,
                  earlier: tuple[str, ...] = ()) -> None:
        """Send the interjections a finished turn could not take as the next prompt.

        The lock is held and the finished turn is already gone. The chat stays
        running; the continuation is a turn of its own with its own trace, on the
        same provider session.
        """
        session = self._sessions[session_id]
        content = "\n\n".join(text for _, text in follow)
        if redirected:
            content = ("The architect stopped your previous step to send the message below. "
                       "Everything already completed stays as it is.\n\n" + content)
        running = _Running(trace=HubTurnObserver(
            self.usage_log, follow[0][0], session.projectId, session.provider, session.model,
        ))
        self._start(session_id, running, content, carried=tuple(identifier for identifier, _ in follow),
                    earlier=earlier)

    def _tool_message(self, session: _SavedChat, item: Mapping, kind: str, environment) -> None:
        """Keep one visible row per MCP call, from started to its outcome."""
        content, candidate, failed = activity._tool_activity(item, environment)
        message_id = self._call_row(session, str(item.get("id") or "tool"))
        message = next((row for row in session.messages if row.id == message_id), None)
        running = kind in {"item.started", "item.updated"} and str(item.get("status") or "") not in {"completed", "failed", "cancelled", "interrupted"}
        outcome = "streaming" if running else "failed" if failed else "complete"
        if message is None:
            message = ChatMessage(id=message_id, role="tool", content=content, createdAt=_now(), status=outcome)
            session.messages.append(message)
        elif not running or message.status == "streaming":
            # A repeated started event never reopens a call that already ended.
            message.content, message.status = content, outcome
        if candidate:
            message.candidateId = candidate
        active = self._running.get(session.id)
        if active and active.trace:
            active.trace.tool(str(item.get("id") or "tool"), item.get("tool"), item.get("arguments"),
                              running=running, failed=failed, result=item.get("result"), candidate_id=candidate)
        session.updatedAt = _now()

    def _event(self, session: _SavedChat, event: dict, environment) -> tuple[HubError | None, bool]:
        kind = event.get("type")
        native = event.get("thread_id") if kind == "thread.started" else event.get("session_id")
        if isinstance(native, str):
            session.nativeSessionId = projects._identifier(native)
        active = self._running.get(session.id)
        trace = active.trace if active else None
        if trace and isinstance(native, str):
            trace.bind(session.nativeSessionId)
        if trace and kind == "assistant":
            trace.claude_usage(event.get("message"))
        if trace and kind == "stream_event":
            trace.claude_stream(event.get("event"))
        if kind == "system" and event.get("subtype") == "init" and active and active.library_skills:
            skill_plugins.learn(self.runtime_root, event)
        if kind in {"error", "turn.failed"} or (kind == "result" and event.get("is_error")):
            detail = event.get("message") or event.get("error") or event.get("result") or "The provider reported a failed turn."
            if isinstance(detail, Mapping):
                # The CLI wraps its own sentence; carry that, not this process's
                # rendering of a dict, so the reader gets the provider's words.
                detail = detail.get("message") or json.dumps(detail, ensure_ascii=False, default=str)
            return HubError(code="CHAT_PROVIDER_FAILED", detail=providers._redact(str(detail), environment)[:1000]), False
        text, message_id, append = None, None, False
        if kind in {"item.started", "item.completed", "item.updated"}:
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if item.get("type") == "mcp_tool_call":
                # What the CLI actually did through the bound tools, kept as a
                # readable row. A failed call is this turn's news, not its end.
                self._tool_message(session, item, kind, environment)
                return None, False
            if kind != "item.started" and item.get("type") == "agent_message":
                text, message_id = item.get("text", ""), str(item.get("id", "answer"))
        elif kind == "stream_event":
            inner = event.get("event", {})
            if inner.get("type") == "content_block_delta" and inner.get("delta", {}).get("type") == "text_delta":
                text, message_id, append = inner["delta"].get("text", ""), "claude-answer", True
        elif kind == "assistant":
            blocks = event.get("message", {}).get("content", []) or []
            for row in blocks:
                # Claude opens a call here and answers it in a later user event.
                if isinstance(row, dict) and row.get("type") == "tool_use":
                    self._tool_message(session, activity._claude_call(row, None), "item.started", environment)
            text = "".join(row.get("text", "") for row in blocks if isinstance(row, dict) and row.get("type") == "text")
            message_id = "claude-answer"
        elif kind == "user" and event.get("isReplay") is True:
            # Claude echoes each stdin message as it reads it: an interjection
            # echoed back is one the Agent now has (#301).
            if isinstance(event.get("uuid"), str):
                self._delivered(session, event["uuid"])
            return None, False
        elif kind == "command_lifecycle":
            if event.get("state") == "started" and isinstance(event.get("command_uuid"), str):
                self._delivered(session, event["command_uuid"])
            return None, False
        elif kind == "user":
            for row in event.get("message", {}).get("content", []) or []:
                if isinstance(row, dict) and row.get("type") == "tool_result":
                    identifier = self._call_row(session, str(row.get("tool_use_id") or "tool"))
                    asked = next((item for item in session.messages if item.id == identifier), None)
                    self._tool_message(session, activity._claude_call(row, asked), "item.completed", environment)
            return None, False
        elif kind == "result" and isinstance(event.get("result"), str):
            text, message_id = event["result"], "claude-answer"
        if text:
            if trace:
                trace.first_response()
            # Prefix provider ids with the current user message, since CLI item
            # ids may repeat in a resumed turn.
            output_id = f"{self._answering(session)}:{message_id}"
            message = next((row for row in session.messages if row.id == output_id), None)
            if message is None:
                message = ChatMessage(id=output_id, role="assistant", content="", createdAt=_now(), status="streaming")
                session.messages.append(message)
            clean = providers._redact(text, environment)
            message.content = message.content + clean if append else clean
            session.updatedAt = _now()
        return None, kind in {"turn.completed", "result"}

    def stop(self, session_id: str) -> ChatDetail:
        with self._lock:
            session = self._session(session_id)
            if session.sourceSessionId and session.status == "running":
                updated = session.model_copy(deep=True)
                updated.status, updated.updatedAt = "interrupted", _now()
                for message in updated.messages:
                    if message.status == "streaming":
                        message.status = "interrupted"
                self._save(updated)
                self._sessions[session_id] = updated
                return self.get(session_id)
            running = self._running.get(session_id)
            if running is None:
                return self.get(session_id)
            running.stop.set()
            process, thread = running.process, running.thread
            client = self._acp_sessions.get(session_id)
            self._clear_permissions(session_id)
        if client is not None:
            client.cancel()
        if process is not None:
            providers._stop_process(process)
        if thread is not None:
            thread.join(timeout=10)
        return self.get(session_id)

    def close_project(self, project_dir: str) -> None:
        """Detach only this project's agents and cancel their pending permissions."""
        target = os.path.normcase(str(Path(project_dir).resolve()))
        with self._lock:
            self._load()
            ids = [row.id for row in self._sessions.values()
                   if os.path.normcase(str(Path(row.projectDir).resolve())) == target]
        for session_id in ids:
            self.stop(session_id)
            with self._lock:
                client = self._acp_sessions.pop(session_id, None)
                self._clear_permissions(session_id)
            if client is not None:
                client.close()

    def shutdown(self) -> None:
        with self._lock:
            self._closing = True
            for session_id in list(self._running):
                self._clear_permissions(session_id)
            threads = [item.thread for item in self._running.values() if item.thread]
            checking = self._check_thread
        # A connection check is read-only and short; let it finish rather than
        # leaving a CLI probe behind.
        if checking is not None:
            checking.join(timeout=10)
        # Normal Hub shutdown lets admitted work finish. The explicit stop
        # endpoint only terminates that chat's own process tree.
        for thread in threads:
            thread.join()
        for client in self._acp_sessions.values():
            client.close()
        self._acp_sessions.clear()
