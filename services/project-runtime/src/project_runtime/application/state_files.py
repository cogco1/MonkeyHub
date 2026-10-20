"""A 3DM that carries its design state, its decisions and a seal (#657).

Writing: the exact model of one run, with that run's State Record and the project's design decisions in the
architect's words, sealed by ``monkeycad.formats.state_file`` so that a later reader can tell what was
changed outside MonkeyHub. Reading: what such a file states, what changed since it was sealed, and, for each
such change, a condition no check can verify, on the parts it touched: whoever revises the design from that
file has to ask the architect before changing them, because the record does not know the change.

Nothing here is a project write: an export is retained by the export route like any other, and reading a
file writes nothing but a scratch copy in this project's cache, which is deleted with its directory.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from archflow.state.operational_state import DesignObligation
from archflow.state.state_record import StateRecord, StateRecordError, part_of_object, resolve_element_bindings
from monkeycad.formats.state_file import BOX_TOLERANCE_M, OutOfBand, StateFileError, read_state_file, seal_state_file

from ..binding import ProjectBinding
from ..errors import StudioError
from .decisions import list_decisions

# The decisions a file carries: the design's own, in the architect's words, without the run they were said
# against (a file outlives its project's run ids). Re-recorded, each one names the new project's source.
DECISION_KEYS = ("disposition", "targetRef", "rawLanguage", "strength")
FILE_SOURCE = "state-file"


def file_decisions(binding: ProjectBinding) -> list[dict[str, Any]]:
    """The project's active design decisions as portable rows: disposition, target, words and strength."""

    rows = []
    for revision in list_decisions(binding):
        payload = revision.payload
        if payload.get("status") != "active" or (payload.get("scope") or {}).get("domain") != "design":
            continue
        rows.append({"decisionId": revision.decision_id, **{key: payload.get(key) for key in DECISION_KEYS},
                     "extent": (payload.get("scope") or {}).get("extent")})
    return rows


def decision_request(row: Mapping[str, Any], *, project_id: str, run_id: str, state_digest: str) -> dict[str, Any]:
    """The POST /api/decisions body that records one carried decision against a project's exact source."""

    return {"projectId": project_id, "rawLanguage": row["rawLanguage"], "disposition": row["disposition"],
            "strength": row.get("strength") or "hard", "targetRef": row["targetRef"],
            "scope": {"domain": "design", "extent": "project"}, "applicability": "scope", "sourceKind": "human",
            "source": {"kind": "design", "sourceRunId": run_id, "stateDigest": state_digest, "sourceStageRef": None}}


def seal_model(binding: ProjectBinding, model: bytes, *, record: StateRecord, decisions: Sequence[Mapping[str, Any]],
               parent_file_sha256: str | None) -> bytes:
    """The model's bytes with the record, the decisions and their seal written into it."""

    with _scratch(binding) as folder:
        source, sealed = folder / "model.3dm", folder / "sealed.3dm"
        source.write_bytes(model)
        try:
            seal_state_file(source, sealed, record=record.to_dict(), decisions=list(decisions),
                            parent_file_sha256=parent_file_sha256)
        except StateFileError as exc:
            raise StudioError(422, f"STATE_FILE_{exc.code}", exc.detail or exc.code) from exc
        return sealed.read_bytes()


def inspect_state_file(binding: ProjectBinding, data: bytes) -> dict[str, Any]:
    """What one state-carrying 3DM states and what changed in it since its seal; nothing is retained."""

    with _scratch(binding) as folder:
        path = folder / "model.3dm"
        path.write_bytes(data)
        try:
            found = read_state_file(path)
        except StateFileError as exc:
            raise StudioError(422, f"STATE_FILE_{exc.code}", exc.detail or exc.code) from exc
    # What the seal vouches for is the design; what the file says beside it is only listed.
    sealed = found.sealed_record is not None
    record_rows = found.sealed_record if sealed else found.record
    decisions = list(found.sealed_decisions) if found.sealed_decisions is not None else list(found.decisions)
    try:
        record = StateRecord.from_dict(record_rows)
    except (StateRecordError, KeyError, TypeError, ValueError) as exc:
        raise StudioError(422, "STATE_FILE_RECORD_INVALID", f"The file's design record is not a valid record: {exc}") from exc
    conditions = outside_conditions(record, found.out_of_band, decisions, file_sha256=found.file_sha256)
    return {"fileSha256": found.file_sha256, "sealed": found.binding is not None, "sealKeepsRecord": sealed,
            "parentFileSha256": None if found.binding is None else found.binding.get("parent_file_sha256"),
            "record": record_rows, "decisions": decisions,
            "outOfBand": [{"kind": row.kind, "ref": row.ref, "detail": row.detail} for row in found.out_of_band],
            "reencoded": list(found.reencoded),
            "conditions": [row.to_dict() for row in conditions]}


def moved_by(detail: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """The one offset an object's box moved by, in the file's Z-up frame (east, north, up), or None when its
    size changed too."""

    try:
        before, after = detail["before"], detail["after"]
        low = [a - b for a, b in zip(after["min"], before["min"])]
        high = [a - b for a, b in zip(after["max"], before["max"])]
    except (KeyError, TypeError):
        return None
    if len(low) != 3 or max(abs(a - b) for a, b in zip(low, high)) > BOX_TOLERANCE_M:
        return None
    return tuple(round((a + b) / 2, 6) for a, b in zip(low, high))  # type: ignore[return-value]


def amount(offset: Sequence[float]) -> str:
    """An offset in words: metres east, north and up."""

    dx, dy, dz = offset
    return f"({dx:.3f}, {dy:.3f}, {dz:.3f}) m (east, north, up)"


def outside_conditions(record: StateRecord, findings: Sequence[OutOfBand], decisions: Sequence[Mapping[str, Any]], *,
                       file_sha256: str) -> tuple[DesignObligation, ...]:
    """One question awaiting the architect for each part a change made outside MonkeyHub touched.

    The design record does not take the change: the part keeps what its record says, and the file written back
    carries that. Whether to adopt the change is the architect's to answer, so each question is a condition no
    check can verify on that part alone, and a reviser who reaches the part asks. An object that belongs to no
    part of the record, a seal that is missing and a re-encoded mesh ask nothing. Ids are the file's, so
    questions from several files never meet.
    """

    elements = resolve_element_bindings(record)
    targets = sorted({str(row.get("targetRef")) for row in decisions if row.get("targetRef")})
    asked: dict[tuple[str, ...], list[str]] = {}
    for finding in findings:
        if finding.kind.startswith("object_"):
            element = part_of_object(finding.ref.removeprefix("object:"), elements)
            subjects: tuple[str, ...] = () if element is None else (f"entity:{element}",)
        elif finding.kind.startswith("row_"):
            subjects = (finding.ref,) if finding.ref.split(":", 1)[0] in ("entity", "parameter", "relation") else ()
        elif finding.kind == "decisions_changed":
            # The parts whose decisions were added or removed in the file; without that detail, every target.
            changed = [*(finding.detail.get("added") or ()), *(finding.detail.get("removed") or ())]
            subjects = tuple(sorted({str(row.get("targetRef")) for row in changed if row.get("targetRef")})) if changed else tuple(targets)
        else:
            continue
        if subjects:
            asked.setdefault(subjects, []).append(change_words(finding))
    prefix = f"outside-{file_sha256[:12]}"
    return tuple(DesignObligation(
        obligation_id=f"{prefix}-{number}",
        statement=f"Outside MonkeyHub, {'; '.join(words)}. The design keeps what its record says, and the file written "
                  f"back carries that; whether to adopt the change waits for the architect's answer: ask them before "
                  f"changing {', '.join(subjects)}.",
        source_ref=f"{FILE_SOURCE}:{file_sha256}", subject_refs=subjects)
        for number, (subjects, words) in enumerate(asked.items(), 1))


def outside_rows(record: StateRecord) -> list[dict[str, Any]]:
    """Every change made outside MonkeyHub that a file brought, newest file first: its words and the parts it
    touched. Each waits for the architect's answer; none is in the design."""

    rows = [{"ref": f"obligation:{row.obligation_id}", "statement": row.statement, "subjects": list(row.subject_refs),
             "fileSha256": row.source_ref.split(":", 1)[1]}
            for row in record.obligations if row.source_ref.startswith(f"{FILE_SOURCE}:")]
    order = list(dict.fromkeys(row["fileSha256"] for row in rows))
    return sorted(rows, key=lambda row: -order.index(row["fileSha256"]))


def change_words(finding: OutOfBand) -> str:
    """One change made outside MonkeyHub, in words, with what it changed and by how much."""

    if finding.kind.startswith("object_"):
        return _object_words(finding, finding.ref.removeprefix("object:"))
    if finding.kind.startswith("row_"):
        return {"row_added": f"{finding.ref} was added", "row_removed": f"{finding.ref} was removed",
                "row_changed": f"{finding.ref} was changed"}[finding.kind] + " in the file's design record"
    if finding.kind == "decisions_changed":
        added, removed = finding.detail.get("added") or (), finding.detail.get("removed") or ()
        rows = [*(f"added {row.get('disposition')} on {row.get('targetRef')} (\"{row.get('rawLanguage')}\")" for row in added),
                *(f"removed {row.get('disposition')} on {row.get('targetRef')} (\"{row.get('rawLanguage')}\")" for row in removed)]
        return "the decisions the file carries were changed" + (": " + "; ".join(rows) if rows else "")
    if finding.kind == "unsealed":
        return "the file carries no seal"
    return finding.kind


def _object_words(finding: OutOfBand, name: str) -> str:
    """One change to an object, in words; a moved box with its offset, a turned or resized one with its two boxes."""

    detail = finding.detail
    if finding.kind == "object_moved":
        offset = moved_by(detail)
        return f"{name} was moved by " + (amount(offset) if offset is not None
                                          else f"{float(detail.get('moved_m', 0.0)):.3f} m")
    if finding.kind == "object_transformed":
        return (f"{name} was turned, mirrored or resized: its box went from {_box_words(detail.get('before'))} "
                f"to {_box_words(detail.get('after'))}")
    if finding.kind == "object_reshaped":
        return f"{name} was turned, mirrored or reshaped inside its box {_box_words(detail.get('box'))}"
    if finding.kind == "object_unnamed":
        return (f"the objects without a name went from {detail.get('before')} to {detail.get('after')}; "
                "none of them belongs to a part of the design")
    return {"object_added": f"{name} was added", "object_removed": f"{name} was removed",
            "object_duplicate": f"{name} was duplicated"}.get(finding.kind, f"{name} changed")


def _box_words(box: Mapping[str, Any] | None) -> str:
    """A box as its size and its low corner, in metres east, north and up."""

    try:
        low, high = box["min"], box["max"]
        size = " x ".join(f"{b - a:.3f}" for a, b in zip(low, high))
        return f"{size} m at ({low[0]:.3f}, {low[1]:.3f}, {low[2]:.3f})"
    except (KeyError, TypeError, IndexError):
        return "(unknown)"


class _scratch:
    """A scratch directory in this project's cache, deleted with what it holds."""

    def __init__(self, binding: ProjectBinding) -> None:
        self.root = Path(binding.settings.project_cache_dir) / "state-files"

    def __enter__(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=self.root)
        return Path(self.folder.name)

    def __exit__(self, *exc: object) -> None:
        self.folder.cleanup()
