"""The architect's standing instructions and the conversation memory every turn reads.

Two scopes, each one small file under ``<runtime>/config/notes``: one for every
conversation of this Hub, and one per project folder. A scope holds the
instructions the architect writes (what CLAUDE.md is to Claude Code) and the
short notes an Agent was asked to keep (``memory_note``) or the architect added.
Both are put in front of every turn's request (``turn_block``), so a rule said
once holds in every later conversation, and both are shown and edited in the
Hub (the Notes page), so nothing the Agent keeps is out of the architect's
sight. Removing a note keeps it, marked removed, so it can be put back.

This is not the project's design memory (``project_runtime.memory``: locators,
source policies, recipes), which the bound Studio owns and hands each turn by
the words it is about; the Notes page lists that too, read from the Studio.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import threading
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..models import HubFailure
from ..runtime.workers import project_key

#: Where the notes live, below the runtime root. ``config`` is guarded from the Agent's file tools (providers._guarded).
NOTES_DIR = Path("config") / "notes"
INSTRUCTIONS_LIMIT = 20_000
NOTE_LIMIT = 500
ACTIVE_NOTES_LIMIT = 200
#: The most a turn is given; the rest is named, not silently dropped.
TURN_BLOCK_LIMIT = 16_000

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryNote(BaseModel):
    id: str
    text: str
    createdAt: str
    source: Literal["agent", "person"]
    chatId: str | None = None
    removedAt: str | None = None


class NotesScope(BaseModel):
    """One scope's instructions and its notes, removed ones included."""

    scope: Literal["global", "project"]
    projectDir: str | None = None
    instructions: str = ""
    memories: list[MemoryNote] = Field(default_factory=list)
    updatedAt: str | None = None


class NotesView(BaseModel):
    """What the Notes page shows: every conversation's scope, and this project's when one is named."""

    global_: NotesScope = Field(alias="global")
    project: NotesScope | None = None

    model_config = ConfigDict(populate_by_name=True)


class InstructionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectDir: str | None = Field(default=None, description="the project folder; omitted, every conversation's scope")
    text: str = Field(max_length=INSTRUCTIONS_LIMIT)


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectDir: str | None = None
    text: str = Field(min_length=1, max_length=NOTE_LIMIT)


class NoteChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectDir: str | None = None


def _path(runtime_root: Path, project_dir: str | None) -> Path:
    root = Path(runtime_root) / NOTES_DIR
    if project_dir is None:
        return root / "global.json"
    # One file per folder, however the folder is spelled (gap 26).
    return root / "projects" / (sha256(project_key(project_dir).encode("utf-8")).hexdigest()[:24] + ".json")


def read_scope(runtime_root: Path, project_dir: str | None) -> NotesScope:
    """A scope as saved; an absent file is an empty scope, an unreadable one is refused rather than replaced."""

    path = _path(runtime_root, project_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return NotesScope(scope="global" if project_dir is None else "project", projectDir=project_dir)
    except OSError as exc:
        raise HubFailure(503, "NOTES_UNREADABLE", "The saved instructions could not be read.") from exc
    try:
        scope = NotesScope.model_validate_json(raw)
    except ValueError as exc:
        raise HubFailure(503, "NOTES_UNREADABLE", f"The saved instructions at {path} are not valid; fix or move "
                         "the file, and nothing will overwrite it meanwhile.") from exc
    if project_dir is not None:
        scope.projectDir = scope.projectDir or project_dir
    return scope


def _write(runtime_root: Path, scope: NotesScope) -> None:
    path = _path(runtime_root, scope.projectDir if scope.scope == "project" else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(scope.model_dump_json(indent=1) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _clean(text: str, limit: int) -> str:
    text = text.replace("\r\n", "\n").strip()
    if len(text) > limit:
        raise HubFailure(422, "NOTES_TOO_LONG", f"Keep it under {limit} characters.")
    return text


def set_instructions(runtime_root: Path, project_dir: str | None, text: str) -> NotesScope:
    with _LOCK:
        scope = read_scope(runtime_root, project_dir)
        scope.instructions = _clean(text, INSTRUCTIONS_LIMIT)
        scope.updatedAt = _now()
        _write(runtime_root, scope)
        return scope


def add_note(runtime_root: Path, project_dir: str | None, text: str, *, source: Literal["agent", "person"],
             chat_id: str | None = None) -> MemoryNote:
    """Keep one short note; the same words kept again are the note already there."""

    clean = " ".join(_clean(text, NOTE_LIMIT).split())
    if not clean:
        raise HubFailure(422, "NOTES_EMPTY", "Say what to remember.")
    with _LOCK:
        scope = read_scope(runtime_root, project_dir)
        active = [row for row in scope.memories if row.removedAt is None]
        same = next((row for row in active if row.text.casefold() == clean.casefold()), None)
        if same is not None:
            return same
        if len(active) >= ACTIVE_NOTES_LIMIT:
            raise HubFailure(409, "NOTES_FULL", f"This scope already keeps {ACTIVE_NOTES_LIMIT} notes; remove some first.")
        note = MemoryNote(id=str(uuid4()), text=clean, createdAt=_now(), source=source, chatId=chat_id)
        scope.memories.append(note)
        scope.updatedAt = note.createdAt
        _write(runtime_root, scope)
        return note


def _change(runtime_root: Path, project_dir: str | None, note_id: str, removed: bool) -> NotesScope:
    with _LOCK:
        scope = read_scope(runtime_root, project_dir)
        note = next((row for row in scope.memories if row.id == note_id or (len(note_id) >= 8 and row.id.startswith(note_id))), None)
        if note is None:
            raise HubFailure(404, "NOTE_NOT_FOUND", "This note is not kept here.")
        note.removedAt = _now() if removed else None
        scope.updatedAt = _now()
        _write(runtime_root, scope)
        return scope


def remove_note(runtime_root: Path, project_dir: str | None, note_id: str) -> NotesScope:
    """Mark a note removed: no turn reads it any more, and it can be put back."""

    return _change(runtime_root, project_dir, note_id, True)


def restore_note(runtime_root: Path, project_dir: str | None, note_id: str) -> NotesScope:
    return _change(runtime_root, project_dir, note_id, False)


def turn_block(runtime_root: Path, project_dir: str | None, *, place: str = "project") -> str:
    """What every turn is given before its request: the instructions and the notes, or nothing at all.

    A scope that cannot be read is said to be unreadable, never skipped in silence.
    """

    sections: list[str] = []
    notes: list[str] = []
    # A conversation in a plain folder keeps the same per-folder scope; it is called what it is ("This folder").
    own = "This folder" if place == "folder" else "This project"
    for label, scoped in (("All conversations", None), (own, project_dir)):
        if scoped is None and label == own:
            continue
        try:
            scope = read_scope(runtime_root, scoped)
        except HubFailure as failure:
            sections.append(f"[{label}] could not be read: {failure.error.detail}")
            continue
        if scope.instructions:
            sections.append(f"[{label}]\n{scope.instructions}")
        # memory_note's own scope words: "project" is this conversation's project or folder.
        where = "all" if scoped is None else "project"
        notes += [f"- {row.text} (id {row.id[:8]}, {where})" for row in scope.memories if row.removedAt is None]
    if not sections and not notes:
        return ""
    block = ("The architect's standing instructions and the notes kept for them in MonkeyHub. The architect wrote "
             "the instructions and sees every note; follow both unless the current message says otherwise. Keep a "
             "new note only when asked to remember something, with memory_note; never in a file.\n")
    if sections:
        block += "\n".join(sections) + "\n"
    if notes:
        block += "[Remembered]\n" + "\n".join(notes) + "\n"
    if len(block) > TURN_BLOCK_LIMIT:
        block = block[:TURN_BLOCK_LIMIT] + "\n[The rest of the instructions and notes was cut here: they are too long " \
                                           "for one turn. Tell the architect.]\n"
    return block + "\n"
