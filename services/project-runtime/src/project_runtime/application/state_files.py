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
from monkeycad.formats.state_file import OutOfBand, StateFileError, read_state_file, seal_state_file

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
    try:
        record = StateRecord.from_dict(found.record)
    except (StateRecordError, KeyError, TypeError, ValueError) as exc:
        raise StudioError(422, "STATE_FILE_RECORD_INVALID", f"The file's design record is not a valid record: {exc}") from exc
    conditions = outside_conditions(record, found.out_of_band, found.decisions, file_sha256=found.file_sha256)
    return {"fileSha256": found.file_sha256, "sealed": found.binding is not None,
            "parentFileSha256": None if found.binding is None else found.binding.get("parent_file_sha256"),
            "record": found.record, "decisions": list(found.decisions),
            "outOfBand": [{"kind": row.kind, "ref": row.ref, "detail": row.detail} for row in found.out_of_band],
            "reencoded": list(found.reencoded),
            "conditions": [row.to_dict() for row in conditions]}


def outside_conditions(record: StateRecord, findings: Sequence[OutOfBand], decisions: Sequence[Mapping[str, Any]], *,
                       file_sha256: str) -> tuple[DesignObligation, ...]:
    """One condition no check can verify per change made outside MonkeyHub, on the parts it touched.

    The record does not have these changes: a reviser who reaches one of those parts must ask whether the
    change made outside is to be kept. An object that belongs to no part of the record, a seal that is
    missing and a re-encoded mesh make no condition.
    """

    elements = resolve_element_bindings(record)
    components = {entity_id: row.get("component_id") for entity_id, row in elements.items()}
    targets = sorted({str(row.get("targetRef")) for row in decisions if row.get("targetRef")})
    conditions: list[DesignObligation] = []
    for finding in findings:
        subjects: tuple[str, ...] = ()
        if finding.kind.startswith("object_"):
            name = finding.ref.removeprefix("object:")
            element = part_of_object(name, elements)
            if element is not None:
                component = components.get(element)
                subjects = tuple(dict.fromkeys([f"entity:{element}", *([f"entity:{component}"] if component else [])]))
            what = _object_words(finding, name)
        elif finding.kind.startswith("row_"):
            subjects = (finding.ref,) if finding.ref.split(":", 1)[0] in ("entity", "parameter", "relation") else ()
            what = {"row_added": f"{finding.ref} was added", "row_removed": f"{finding.ref} was removed",
                    "row_changed": f"{finding.ref} was changed"}[finding.kind] + " in the file's design record"
        elif finding.kind == "decisions_changed":
            subjects = tuple(targets)
            what = "the decisions the file carries were changed"
        else:
            continue
        if not subjects:
            continue
        conditions.append(DesignObligation(
            obligation_id=f"outside-{len(conditions) + 1}",
            statement=f"Outside MonkeyHub, {what}. The design record does not have this change: ask the architect "
                      "whether it is kept before changing what it touched.",
            source_ref=f"{FILE_SOURCE}:{file_sha256}", subject_refs=subjects))
    return tuple(conditions)


def _object_words(finding: OutOfBand, name: str) -> str:
    if finding.kind == "object_moved":
        return f"{name} was moved by {float(finding.detail.get('moved_m', 0.0)):.3f} m"
    return {"object_added": f"{name} was added", "object_removed": f"{name} was removed",
            "object_duplicate": f"{name} was duplicated"}.get(finding.kind, f"{name} changed")


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
