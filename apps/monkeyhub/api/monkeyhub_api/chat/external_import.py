"""Confirmed first import of an existing Codex conversation.

The Hub association and the provider's execution directory are separate facts.
Preview lists public provider metadata only. Confirmation may acquire the same
native writer in its original directory; it never changes a native cwd or reads
provider-private files. The existing ChatStore owns every retained write.
"""
from __future__ import annotations

import base64
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import time
from typing import Callable
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from archflow.contracts.canonical import canonical_digest

from .. import projects
from ..models import ChatAttachment, ChatDetail, ChatMessage, HubFailure
from . import providers
from .acp_session import CodexAcpSession
from .continuation import check_native_version


_MAX_FILE_BYTES = 20 * 1024 * 1024
_MAX_IMPORT_BYTES = 40 * 1024 * 1024
_MAX_RAW_MEDIA_CHARS = 56 * 1024 * 1024
_MAX_TEXT_CHARS = 8_000_000
_MAX_CHUNKS = 50_000
_PREVIEW_LIFETIME = 600
_MAX_PREVIEWS = 128
_NATIVE_LINK = re.compile(r"\[@([^\]\r\n]{1,255})\]\(([^\r\n)]{1,8192})\)")


def _failure(code: str, detail: str, status: int = 409) -> HubFailure:
    return HubFailure(status, "CHAT_IMPORT_" + code, detail)


def _identity(value: str, limit: int) -> str:
    if (not isinstance(value, str) or not value or len(value) > limit
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
        raise _failure("IDENTITY", "The provider returned an invalid public identity.")
    return value


def same_directory(left: str, right: str) -> bool:
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


@dataclass(frozen=True)
class PublicItem:
    id: str
    role: str
    blocks: tuple[dict, ...]


def public_items(events: list[dict], session_id: str) -> list[PublicItem]:
    """Validate complete ordered public items; never read a referenced resource."""
    _identity(session_id, 200)
    rows: list[tuple[str, str, list[dict]]] = []
    seen: set[str] = set()
    text_size = media_size = chunks = 0
    for event in events:
        if not isinstance(event, dict) or event.get("sessionId") != session_id:
            raise _failure("HISTORY", "The provider returned history for another native session.")
        update = event.get("update")
        if not isinstance(update, dict):
            raise _failure("HISTORY", "The provider returned a malformed public update.")
        kind = update.get("sessionUpdate")
        if kind not in {"user_message_chunk", "agent_message_chunk"}:
            continue
        identifier = _identity(update.get("messageId"), 500)
        role = "user" if kind == "user_message_chunk" else "assistant"
        block = update.get("content")
        if not isinstance(block, dict) or block.get("type") not in {"text", "image", "audio", "resource", "resource_link"}:
            raise _failure("HISTORY", "This public content type cannot be imported without losing information.")
        # Copy plain public content so callers cannot mutate the retained prefix.
        block = json.loads(json.dumps(block, ensure_ascii=False))
        if block["type"] == "text":
            if not isinstance(block.get("text"), str):
                raise _failure("HISTORY", "The provider returned malformed public text.")
            text_size += len(block["text"])
        else:
            media_size += len(json.dumps(block, ensure_ascii=False))
        chunks += 1
        if chunks > _MAX_CHUNKS or text_size > _MAX_TEXT_CHARS or media_size > _MAX_RAW_MEDIA_CHARS:
            raise _failure("LIMIT", "This public history exceeds the import limit; nothing was truncated or saved.")
        if rows and rows[-1][0] == identifier:
            if rows[-1][1] != role:
                raise _failure("HISTORY", "The provider reused a public identity for different roles.")
            blocks = rows[-1][2]
        else:
            if identifier in seen:
                raise _failure("HISTORY", "The provider repeated a noncontiguous public identity.")
            seen.add(identifier)
            blocks = []
            rows.append((identifier, role, blocks))
        if block["type"] == "text" and blocks and blocks[-1].get("type") == "text":
            blocks[-1]["text"] += block["text"]
        else:
            blocks.append(block)
    return [PublicItem(identifier, role, tuple(blocks)) for identifier, role, blocks in rows]


@dataclass(frozen=True)
class ImportedHistory:
    messages: tuple[ChatMessage, ...]
    attachments: tuple[tuple[ChatAttachment, bytes], ...]
    source_references: tuple[dict, ...]


def _reference(identifier: str, uri, name=None) -> dict:
    if (not isinstance(uri, str) or not uri or len(uri) > 8192
            or any(ord(c) < 32 or ord(c) == 127 for c in uri)):
        raise _failure("MEDIA", "An attachment reference has no usable public source.")
    return {"messageId": identifier, "uri": uri,
            "name": str(name or "Source attachment")[:255], "availability": "source-only"}


def _filename(block: dict, index: int, mime: str) -> str:
    resource = block.get("resource", {})
    candidate = block.get("name") or resource.get("name")
    uri = block.get("uri") or resource.get("uri")
    if not candidate and isinstance(uri, str):
        candidate = unquote(urlsplit(uri).path.replace("\\", "/").rsplit("/", 1)[-1])
    if (not isinstance(candidate, str) or not candidate or len(candidate) > 255
            or candidate in {".", ".."} or any(c in "/\\" or ord(c) < 32 for c in candidate)):
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "text/plain": ".txt"}.get(mime)
        candidate = f"native-attachment-{index + 1}{extension or mimetypes.guess_extension(mime) or '.bin'}"
    return candidate


def import_history(events: list[dict], session_id: str, created_at: str, environment: dict,
                   *, boundary=None) -> ImportedHistory:
    """Retain inline bytes and exact public words; URI-only media stays labelled."""
    items = public_items(events, session_id)
    offset = 0
    if boundary is not None:
        if boundary.contentMode != "blocks" or boundary.sessionId != session_id or len(items) < len(boundary.items):
            raise _failure("HISTORY", "The provider did not preserve the released import history prefix.")
        for prior, item in zip(boundary.items, items):
            if (prior.id, prior.role, prior.contentDigest) != (item.id, item.role, canonical_digest(list(item.blocks))):
                raise _failure("HISTORY", "The provider changed or reordered the released import history prefix.")
        offset = len(boundary.items)
    messages, files, references = [], [], []
    total = 0
    for item in items[offset:]:
        text, attachments = [], []
        for index, block in enumerate(item.blocks):
            kind = block["type"]
            if kind == "text":
                public_text = providers._redact(block["text"], environment)
                text.append(public_text)
                # The pinned adapter serializes native image inputs as these
                # public links. Keep them verbatim, but never treat a link as a
                # local read grant or falsely claim the bytes were copied.
                for name, uri in _NATIVE_LINK.findall(public_text):
                    references.append(_reference(item.id, uri, name))
                continue
            resource = block.get("resource", {})
            if not isinstance(resource, dict):
                raise _failure("MEDIA", "The provider returned a malformed attachment resource.")
            mime = block.get("mimeType") or resource.get("mimeType") or "application/octet-stream"
            if not isinstance(mime, str) or len(mime) > 100 or any(ord(c) < 32 for c in mime):
                raise _failure("MEDIA", "The provider returned an invalid attachment media type.")
            encoded = block.get("data") if kind in {"image", "audio"} else resource.get("blob")
            if encoded is not None:
                if not isinstance(encoded, str) or len(encoded) > ((_MAX_FILE_BYTES + 2) // 3) * 4:
                    raise _failure("LIMIT", "A source attachment exceeds the import size limit.")
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as exc:
                    raise _failure("MEDIA", "The provider returned invalid attachment bytes.") from exc
            elif kind == "resource" and isinstance(resource.get("text"), str):
                data = providers._redact(resource["text"], environment).encode("utf-8")
            else:
                reference = _reference(item.id, block.get("uri") or resource.get("uri"), block.get("name"))
                reference["uri"] = providers._redact(reference["uri"], environment)
                references.append(reference)
                # Preserve the visible reference in its original message too.
                text.append(f"\n[{reference['name']}]({reference['uri']})\n")
                continue
            total += len(data)
            if len(data) > _MAX_FILE_BYTES or total > _MAX_IMPORT_BYTES:
                raise _failure("LIMIT", "Source attachments exceed the import limit; nothing was truncated or saved.")
            if mime.startswith("image/"):
                from .media import _verify_image
                _verify_image(data, mime)
            attachment = ChatAttachment(
                id=str(uuid5(NAMESPACE_URL, f"codex:{session_id}:{item.id}:attachment:{index}")),
                name=_filename(block, index, mime), mimeType=mime, size=len(data))
            attachments.append(attachment)
            files.append((attachment, data))
        messages.append(ChatMessage(id=f"native:{session_id}:{item.id}", role=item.role,
                                    content="".join(text), createdAt=created_at, attachments=attachments))
    return ImportedHistory(tuple(messages), tuple(files), tuple(references))


@dataclass(frozen=True)
class ImportPreview:
    previewToken: str
    sourceSessionId: str
    projectId: str
    projectDir: str
    providerCwd: str
    sameDirectory: bool
    canContinue: bool
    limitation: str | None
    existingChatId: str | None


@dataclass(frozen=True)
class ImportResult:
    chat: ChatDetail
    providerCwd: str
    executionMode: str
    state: str
    sourceReferences: tuple[dict, ...]


class ExternalImports:
    """One helper of one ChatStore; no second persistence or shared-chat owner."""

    def __init__(self, store, record_type, *, metadata_client: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.store, self.record_type = store, record_type
        self._metadata_client = metadata_client or self._new_metadata_client
        self._clock = clock
        self._previews: dict[str, tuple[float, ImportPreview]] = {}
        self._inflight: set[str] = set()
        self._fenced_sources: set[str] = set()

    def _new_metadata_client(self, project_dir):
        commands = self.store.commands if self.store.commands is not None else providers._cli_commands()
        environment = dict(os.environ)
        environment["CODEX_PATH"] = providers._native_codex(commands.get("codex", ()))
        def deny_permission(_):
            future = Future()
            future.set_result(None)
            return future
        return CodexAcpSession(self.store._acp_command, project_dir, environment, [],
                               lambda _: None, deny_permission)

    def _project(self, project_id, project_dir):
        identifier, resolved = projects._project(project_dir)
        if identifier != project_id or resolved != project_dir:
            raise _failure("PROJECT", "Select the existing project by its exact identity and resolved directory.")
        return identifier, resolved

    def _matching(self, native_id, project_id, project_dir):
        matches = []
        for row in self.store._sessions.values():
            if row.provider != "codex":
                continue
            if native_id in row.priorProviderSessionIds:
                raise _failure("HISTORY", "This is an earlier Hub context. Open its current Hub conversation.")
            identifiers = {row.sourceSessionId, row.acpSessionId, row.nativeSessionId, row.originalSourceSessionId}
            if native_id not in identifiers:
                continue
            if getattr(row, "sharedSource", None) is not None:
                raise _failure("READ_ONLY", "A shared author's record cannot become a writable native session.")
            if (row.projectId, row.projectDir) != (project_id, project_dir):
                raise _failure("PROJECT", "This native source is already associated with another Hub project.")
            matches.append(row)
        if len(matches) > 1:
            raise _failure("BINDING", "This native source has conflicting retained Hub records.")
        return matches[0] if matches else None

    def preview(self, source_session_id: str, project_id: str, project_dir: str) -> ImportPreview:
        _identity(source_session_id, 200)
        self._project(project_id, project_dir)
        with self.store._lock:
            self.store._load()
            if self.store._closing or not self.store._acp_command:
                raise _failure("UNAVAILABLE", "Native import is unavailable while Hub is closing or the adapter is absent.")
            row = self._matching(source_session_id, project_id, project_dir)
            existing_id = row.id if row else None
        limitation = None
        commands = self.store.commands if self.store.commands is not None else providers._cli_commands()
        try:
            check_native_version(commands.get("codex", ()))
        except HubFailure as exc:
            limitation = exc.error.detail
        client = self._metadata_client(project_dir)
        try:
            metadata = client.inspect_native(source_session_id, min(self.store.timeout_s, 60))
        except Exception as exc:
            raise _failure("SOURCE", providers._redact(str(exc))[:1000]) from exc
        finally:
            client.close()
        if metadata.session_id != source_session_id:
            raise _failure("IDENTITY", "The provider returned metadata for another native session.")
        if not metadata.can_restore:
            limitation = "This adapter can identify the source but cannot load it. Continue in Codex; no history was imported."
        preview = ImportPreview(secrets.token_urlsafe(32), source_session_id, project_id, project_dir,
                                metadata.cwd, same_directory(metadata.cwd, project_dir),
                                limitation is None, limitation, existing_id)
        with self.store._lock:
            self._project(project_id, project_dir)
            now = self._clock()
            self._previews = {key: value for key, value in self._previews.items() if value[0] > now}
            if len(self._previews) >= _MAX_PREVIEWS:
                raise _failure("BUSY", "Finish or cancel an existing import preview before opening another.")
            self._previews[preview.previewToken] = (now + _PREVIEW_LIFETIME, preview)
        return preview

    def _result(self, row) -> ImportResult:
        origin = row.nativeImport
        ready = row.sourceSessionId is None and not row.nativeReleasePending and row.id not in self.store._fenced_handoffs
        state = ("native-ready" if row.id in self.store._acp_sessions else "native-bound") if ready else "continue-in-codex"
        return ImportResult(self.store.get(row.id), origin.providerCwd, "original-directory",
                            state, tuple(reference.model_dump() if hasattr(reference, "model_dump") else reference
                                         for reference in origin.sourceReferences))

    def confirm(self, source_session_id: str, project_id: str, project_dir: str,
                preview_token: str, *, confirmed: bool) -> ImportResult:
        if confirmed is not True:
            raise _failure("CONFIRMATION", "Choose the target project and confirm the displayed source directory first.", 422)
        _identity(source_session_id, 200)
        self._project(project_id, project_dir)
        with self.store._lock:
            self.store._load()
            if source_session_id in self._fenced_sources:
                raise _failure("INCOMPLETE", "The previous import could not confirm release of its native writer. No automatic retry is allowed.")
            row = self._matching(source_session_id, project_id, project_dir)
            if row is not None and getattr(row, "nativeImport", None) is not None:
                return self._result(row)
            held = self._previews.get(preview_token)
            if held is None or held[0] <= self._clock():
                raise _failure("PREVIEW", "The import preview expired. Read the source and confirm the target again.")
            preview = held[1]
            if (preview.sourceSessionId, preview.projectId, preview.projectDir) != (source_session_id, project_id, project_dir):
                raise _failure("PREVIEW", "The confirmation does not match the source and project that were previewed.")
            if not preview.canContinue:
                raise _failure("UNSUPPORTED", preview.limitation or "Native import is unavailable.")
            if self.store._closing or source_session_id in self._inflight:
                raise _failure("BUSY", "This source is already being imported or Hub is closing.")
            if row is not None:
                if row.acpSessionId or row.nativeSessionId or row.nativeReleaseBoundary or row.nativePublicTimeline:
                    raise _failure("BINDING", "Use the original Hub conversation's native handoff controls.")
                if (row.archived or row.status == "running" or row.id in self.store._running
                        or row.id in self.store._handoffs or row.nativeReleasePending
                        or row.id in self.store._fenced_handoffs or self.store._native_workers.get(row.id)):
                    raise _failure("BUSY", "Finish the source conversation and its pending controls before importing it.")
            created = row is None
            now = datetime.now(timezone.utc).isoformat()
            if created:
                row = self.record_type(id=str(uuid4()), projectId=project_id, projectDir=project_dir,
                                       title="Imported Codex conversation", provider="codex",
                                       sourceSessionId=source_session_id, createdAt=now, updatedAt=now)
                # A transient guarded row lets existing project-close handling
                # see and cancel this admitted import. It is not saved yet.
                self.store._sessions[row.id] = row
            snapshot = row.model_copy(deep=True)
            self._inflight.add(source_session_id)
            self.store._handoffs.add(row.id)
        client = None
        try:
            commands = self.store.commands if self.store.commands is not None else providers._cli_commands()
            check_native_version(commands.get("codex", ()))
            environment = dict(os.environ)
            environment["MONKEYHUB_PRESENTATION_TOKEN"] = secrets.token_urlsafe(32)
            # The factory receives the provenance before constructing a client,
            # so initial launch and later reconnect both use the real source cwd.
            origin = {"sourceSessionId": source_session_id, "providerCwd": preview.providerCwd,
                      "importedAt": now, "sourceReferences": []}
            snapshot = self.record_type.model_validate({**snapshot.model_dump(), "nativeImport": origin})
            client = self.store._new_acp_client(row.id, snapshot, environment)
            with self.store._lock:
                if self.store._closing or row.id in self.store._cancelled_handoffs:
                    raise _failure("CANCELLED", "The project closed before the import completed.")
                self.store._handoff_clients[row.id] = client
            events = client.restore(source_session_id, preview.providerCwd, min(self.store.timeout_s, 60), include_media=True)
            history = import_history(events, source_session_id, now, environment)
            with self.store._lock:
                if self.store._closing or row.id in self.store._cancelled_handoffs:
                    raise _failure("CANCELLED", "The project closed before the import completed.")
                self._project(project_id, project_dir)
                # Other admission paths can retain a source while the adapter
                # loads it. Recheck every binding under the commit lock, not
                # only whether this transient row survived unchanged.
                if (self.store._sessions.get(row.id) is not row
                        or self._matching(source_session_id, project_id, project_dir) is not row):
                    raise _failure("BINDING", "The source binding changed while its history was loading.")
                known = {message.id for message in snapshot.messages}
                if any(message.id in known for message in history.messages):
                    raise _failure("HISTORY", "A provider message identity already exists in this Hub record.")
                origin["sourceReferences"] = list(history.source_references)
                updated = self.record_type.model_validate({**snapshot.model_dump(), "nativeImport": origin})
                updated.messages.extend(history.messages)
                updated.originalSourceSessionId = updated.originalSourceSessionId or source_session_id
                updated.sourceSessionId = None
                updated.transport, updated.acpSessionId, updated.nativeSessionId = "acp", source_session_id, None
                updated.acpDefaultModel = client.default_model
                updated.nativeHistory, updated.nativeHistoryOffset = None, 0
                updated.nativePublicTimeline = True
                updated.status, updated.error, updated.updatedAt = "idle", None, now
                client.confirm_history(source_session_id)
                self.store._save(updated, history.attachments)
                self.store._sessions[row.id] = updated
                self.store._presentation_tokens[row.id] = environment["MONKEYHUB_PRESENTATION_TOKEN"]
                self.store._progress_rows.pop(row.id, None)
                self.store._acp_sessions[row.id] = client
                client = None
                return self._result(updated)
        except HubFailure:
            raise
        except Exception as exc:
            raise _failure("FAILED", providers._redact(str(exc))[:1000]) from exc
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    with self.store._lock:
                        self.store._fenced_handoffs.add(row.id)
                        self._fenced_sources.add(source_session_id)
            with self.store._lock:
                if created and self.store._sessions.get(row.id) is row:
                    self.store._sessions.pop(row.id, None)
                self.store._handoff_clients.pop(row.id, None)
                self.store._handoffs.discard(row.id)
                self.store._cancelled_handoffs.discard(row.id)
                self._inflight.discard(source_session_id)
