"""The one reader of a project's work-in-progress inputs (ADR-007).

``input/runner/state-record.json``, ``input/runner/seats.json`` and
``input/runner/program-sheet.json`` are the designer's authored files. They are
the *work in progress* container: loose files inside the project root, at the
paths ``ProjectLayout`` owns, read here and by nothing else. The repository
never indexes, digests or verifies them — nothing about an authored file is
retained until a run uses it, which is exactly the rule: the shared area holds
what was issued to it, not the working file.

Two identities travel and neither is minted here. ``AuthoredRecord.digest`` is
the record's own content identity (``StateRecord.digest``, ADR-003), taken from
the record; the seat pack's and the program sheet's ``sha256`` is the identity
of the bytes on disk, because neither is a record and neither has a content
digest to take.

This module reads and refuses. It parses no ``SeatPack@1``, keeps nothing, and
says nothing about which run may use what it read.

It writes exactly one of the three. The program sheet is the one authored file
the studio edits — an architect types a brief into a table and expects to find
it again — so ``write_program_sheet_file`` is its single writer, and the record
and the seat pack stay files only a person edits. The write is a work-in-
progress write: it touches nothing under ``runs/``, ``canonical/`` or
``objects/``, and nothing about it is retained until a run reads it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from archflow.project.repository import FilesystemProjectRepository
from archflow.state.program_sheet import PROGRAM_SHEET_SCHEMA
from archflow.state.state_record import StateRecord, StateRecordError


class InputsError(Exception):
    """A work-in-progress input this project cannot be read.

    ``path`` is the file the reader wanted, so a caller that has to turn this
    into an HTTP body or a job failure has the one fact whoever fixes it needs.
    """

    def __init__(self, message: str, *, path: Path) -> None:
        super().__init__(message)
        self.path = path


class AuthoredRecordMissing(InputsError):
    """No authored State Record at the layout's path."""


class AuthoredRecordInvalid(InputsError):
    """The authored State Record is there and cannot be read as one."""


class SeatPackMissing(InputsError):
    """No authored seat pack at the layout's path."""


class SeatPackInvalid(InputsError):
    """The authored seat pack is there and is not a JSON object."""


class ProgramSheetMissing(InputsError):
    """No authored program sheet at the layout's path.

    Absent is ordinary here in a way it is not for the record: a project whose
    architect has not written a brief into the studio simply has no file, and
    the caller derives the sheet from the record instead.
    """


class ProgramSheetInvalid(InputsError):
    """The program sheet is there and is not a ``ProgramSheet@1`` object."""


@dataclass(frozen=True, slots=True)
class AuthoredRecord:
    """The record the designer authored, with the file it came from."""

    path: Path
    record: StateRecord
    digest: str


@dataclass(frozen=True, slots=True)
class SeatPackFile:
    """The seat pack as authored: the raw mapping and the bytes' identity.

    Nothing here knows what a seat is. ``payload`` is handed on to whoever
    owns ``SeatPack@1``.
    """

    path: Path
    payload: Mapping[str, Any]
    sha256: str


@dataclass(frozen=True, slots=True)
class ProgramSheetFile:
    """The program sheet as authored: the mapping and the bytes' identity.

    Nothing here reads a department or an adjacency. The schema literal is
    checked because it is the one claim the file makes about what it is;
    everything past it is ``state.program_sheet``'s to validate.
    """

    path: Path
    payload: Mapping[str, Any]
    sha256: str


def load_authored_record(
    repository: FilesystemProjectRepository,
) -> AuthoredRecord:
    """The project's authored State Record, or a typed refusal naming it.

    Absent and unreadable are different problems for whoever has to fix them,
    so they are different refusals; neither is a bug in the caller.
    """

    path = repository.layout.authored_record
    payload, _ = _read_json(
        path, missing=AuthoredRecordMissing, invalid=AuthoredRecordInvalid
    )
    try:
        record = StateRecord.from_dict(payload)
    except (StateRecordError, KeyError, TypeError, ValueError) as exc:
        raise AuthoredRecordInvalid(_sentence(path, exc), path=path) from exc
    return AuthoredRecord(path=path, record=record, digest=record.digest)


def load_seat_pack_file(
    repository: FilesystemProjectRepository,
) -> SeatPackFile:
    """The project's authored seat pack, whole, with the digest of its bytes.

    The pack carries more than the seats — the commitment the run is made under
    and the identity a live provider would have to present — so it is returned
    as authored and the caller reads what it needs from it.
    """

    path = repository.layout.seat_pack
    payload, data = _read_json(
        path, missing=SeatPackMissing, invalid=SeatPackInvalid
    )
    if not isinstance(payload, Mapping):
        raise SeatPackInvalid(
            f"{path.as_posix()}: a seat pack is a JSON object, not "
            f"{type(payload).__name__}",
            path=path,
        )
    return SeatPackFile(
        path=path, payload=payload, sha256=hashlib.sha256(data).hexdigest()
    )


def load_program_sheet_file(
    repository: FilesystemProjectRepository,
) -> ProgramSheetFile:
    """The project's authored program sheet, or a typed refusal naming it.

    The schema literal is the only field read: a file at this path that does
    not claim to be a ``ProgramSheet@1`` is a different document somebody put
    there, and reading it as a program would be a guess about what it is.
    """

    path = repository.layout.program_sheet
    payload, data = _read_json(
        path, missing=ProgramSheetMissing, invalid=ProgramSheetInvalid
    )
    _require_program_sheet(payload, path=path)
    return ProgramSheetFile(
        path=path, payload=payload, sha256=hashlib.sha256(data).hexdigest()
    )


def write_program_sheet_file(
    repository: FilesystemProjectRepository,
    payload: Mapping[str, Any],
) -> ProgramSheetFile:
    """Write the program sheet to the layout's path; the only writer there is.

    A work-in-progress write and nothing more: one file inside ``input/``,
    created with its directory, replacing whatever was there. It writes no
    record, retains nothing, touches no run, and does not move ``HEAD`` — the
    project's shared and published areas are ``project.repository``'s alone.

    The bytes are UTF-8 with LF endings and sorted keys, so authoring the same
    sheet twice leaves the same file and its ``sha256`` means something.
    """

    path = repository.layout.program_sheet
    _require_program_sheet(payload, path=path)
    data = (
        json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not text: on Windows a text write would turn every LF into
        # CRLF, and the file's sha-256 would depend on which machine wrote it.
        path.write_bytes(data)
    except OSError as exc:
        raise ProgramSheetInvalid(_sentence(path, exc), path=path) from exc
    return ProgramSheetFile(
        path=path,
        payload=dict(payload),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _require_program_sheet(payload: object, *, path: Path) -> None:
    """A mapping claiming ``ProgramSheet@1``, or the refusal saying what it is.

    Read on the way in and checked again on the way out, so the file at this
    path always claims to be what its path says it is — a writer that could
    put anything there would make the reader's check a formality.
    """

    if not isinstance(payload, Mapping):
        raise ProgramSheetInvalid(
            f"{path.as_posix()}: a program sheet is a JSON object, not "
            f"{type(payload).__name__}",
            path=path,
        )
    schema = payload.get("schema")
    if schema != PROGRAM_SHEET_SCHEMA:
        raise ProgramSheetInvalid(
            f"{path.as_posix()}: a program sheet declares schema "
            f"{PROGRAM_SHEET_SCHEMA!r}, not {schema!r}",
            path=path,
        )


def _read_json(
    path: Path,
    *,
    missing: type[InputsError],
    invalid: type[InputsError],
) -> tuple[Any, bytes]:
    """The file's JSON and its bytes, read once, as strict UTF-8.

    The bytes come back with the value because the seat pack is identified by
    them; reading the file a second time to hash it could hash another file.
    """

    if not path.is_file():
        raise missing(f"{path.as_posix()}: no such authored file", path=path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise invalid(_sentence(path, exc), path=path) from exc
    try:
        # ``UnicodeDecodeError`` is a ``ValueError``: a file written in another
        # encoding is undecodable, not absent, and it is the project's to fix.
        return json.loads(data.decode("utf-8")), data
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise invalid(_sentence(path, exc), path=path) from exc


def _sentence(path: Path, exc: BaseException) -> str:
    """The path, then the reason: both, in that order, in one line."""

    return f"{path.as_posix()}: {exc}"
