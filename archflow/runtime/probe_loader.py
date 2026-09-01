"""Generic read-only loader for authorized project input envelopes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
    RunRef,
)
from archflow.project.refs import require_identifier
from archflow.compilers.brief import (
    BriefIntentObservation,
    BriefObservation,
    CompiledDesignBrief,
    compile_design_brief,
)
from archflow.state.operational_state import require_logical_ref


_EXECUTABLE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".exe",
    ".js",
    ".msi",
    ".ps1",
    ".py",
    ".sh",
    ".ts",
}
_MAX_INPUT_BYTES = 256_000


class ProbeInputError(ValueError):
    """Authorized input boundary is malformed or contains derived logic."""


@dataclass(frozen=True, slots=True)
class ProbeInputRecord:
    ref: ProjectRecordRef
    schema: str
    canonical_json: str

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ProjectRecordRef):
            raise TypeError("ref must be ProjectRecordRef")
        if not isinstance(self.schema, str) or not self.schema:
            raise ValueError("schema must be non-empty text")
        payload = json.loads(self.canonical_json)
        if not isinstance(payload, dict) or payload.get("schema") != self.schema:
            raise ValueError("canonical input record schema disagrees")

    def to_payload(self) -> dict[str, Any]:
        return dict(json.loads(self.canonical_json))


@dataclass(frozen=True, slots=True)
class ProbeInputEnvelope:
    project_id: str
    run: RunRef
    raw_request: ProbeInputRecord
    materials: tuple[ProbeInputRecord, ...]

    SCHEMA = "ProbeInputEnvelope@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        if not isinstance(self.run, RunRef):
            raise TypeError("run must be RunRef")
        if self.run.project_id != self.project_id:
            raise ValueError("input envelope and run belong to different projects")
        if self.raw_request.schema != "RawProjectRequest@1":
            raise ValueError("raw_request has unsupported schema")
        if any(
            item.schema != "AuthorizedMaterialInput@1"
            for item in self.materials
        ):
            raise ValueError("materials contain unsupported schemas")

    @property
    def input_refs(self) -> tuple[str, ...]:
        return (
            self.raw_request.ref.uri,
            *(item.ref.uri for item in self.materials),
        )


@dataclass(frozen=True, slots=True)
class PersistedDesignBrief:
    brief: ProjectRecordRef
    receipt: ProjectRecordRef


def load_probe_input_envelope(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
) -> ProbeInputEnvelope:
    """Load inputs through their project owner and reject executable cases."""

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    if not isinstance(run, RunRef):
        raise TypeError("run must be RunRef")
    manifest = repository.load_manifest()
    if manifest.project_id != run.project_id:
        raise ProbeInputError("run belongs to another project")
    loaded_run = repository.load_run(run.run_id)
    if loaded_run != run:
        raise ProbeInputError("run exact base does not match repository")
    root = repository.layout.root
    if ".runs" in {part.lower() for part in root.parts}:
        raise ProbeInputError(
            "repository-level .runs fallback is forbidden"
        )
    _validate_input_files(repository.layout.inputs)
    refs = repository.list_json(
        run=run,
        destination=PersistenceDestination(PersistenceArea.INPUT),
    )
    records = tuple(
        _load_input_record(repository, ref) for ref in refs
    )
    raw = tuple(
        item for item in records if item.schema == "RawProjectRequest@1"
    )
    if len(raw) != 1:
        raise ProbeInputError(
            "input envelope requires exactly one raw request"
        )
    materials = tuple(
        item
        for item in records
        if item.schema == "AuthorizedMaterialInput@1"
    )
    if len(records) != 1 + len(materials):
        unsupported = sorted(
            {
                item.schema
                for item in records
                if item.schema
                not in {
                    "RawProjectRequest@1",
                    "AuthorizedMaterialInput@1",
                }
            }
        )
        raise ProbeInputError(
            f"derived or unsupported input schema: {unsupported}"
        )
    return ProbeInputEnvelope(
        project_id=manifest.project_id,
        run=run,
        raw_request=raw[0],
        materials=materials,
    )


def compile_probe_design_brief(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    observations: tuple[BriefObservation, ...] = (),
    intent_observations: tuple[BriefIntentObservation, ...] = (),
) -> CompiledDesignBrief:
    """Generic probe caller; interpretation is injected, never case-owned."""

    envelope = load_probe_input_envelope(repository, run=run)
    return compile_design_brief(
        project_id=envelope.project_id,
        run_id=run.run_id,
        base=run.base,
        raw_request_ref=envelope.raw_request.ref.uri,
        observations=observations,
        intent_observations=intent_observations,
    )


def persist_compiled_design_brief(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    compiled: CompiledDesignBrief,
) -> PersistedDesignBrief:
    """Persist generated brief records in the named run, never in input."""

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    if not isinstance(run, RunRef):
        raise TypeError("run must be RunRef")
    if not isinstance(compiled, CompiledDesignBrief):
        raise TypeError("compiled must be CompiledDesignBrief")
    if repository.load_run(run.run_id) != run:
        raise ProbeInputError("run exact base does not match repository")
    brief = compiled.brief
    if (
        brief.project_id != run.project_id
        or brief.run_id != run.run_id
        or brief.base != run.base
        or compiled.receipt.brief_digest != brief.brief_digest
    ):
        raise ProbeInputError(
            "compiled brief identity does not match target run"
        )
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    brief_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="design-brief",
        payload=brief.to_dict(),
    )
    receipt_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="brief-compilation-receipt",
        payload=compiled.receipt.to_dict(),
    )
    return PersistedDesignBrief(
        brief=brief_ref,
        receipt=receipt_ref,
    )


def _validate_input_files(root: Path) -> None:
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.parent != root:
            raise ProbeInputError(
                "nested input paths are unsupported; use immutable objects "
                "and a flat authorized input record"
            )
        if path.suffix.lower() in _EXECUTABLE_SUFFIXES:
            raise ProbeInputError(
                f"executable project input is forbidden: {path.name}"
            )
        if path.suffix.lower() != ".json":
            raise ProbeInputError(
                "binary/source material belongs in immutable objects with "
                "an AuthorizedMaterialInput@1 reference"
            )
        if path.stat().st_size > _MAX_INPUT_BYTES:
            raise ProbeInputError(
                f"project input exceeds {_MAX_INPUT_BYTES} bytes"
            )


def _load_input_record(
    repository: FilesystemProjectRepository,
    ref: ProjectRecordRef,
) -> ProbeInputRecord:
    payload = repository.load_json(ref)
    schema = payload.get("schema")
    if not isinstance(schema, str):
        raise ProbeInputError("input record lacks schema")
    if schema == "RawProjectRequest@1":
        if set(payload) != {"schema", "prompt"}:
            raise ProbeInputError("raw request fields drifted")
        prompt = payload["prompt"]
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or len(prompt) > 8_000
        ):
            raise ProbeInputError("raw request prompt is invalid")
    elif schema == "AuthorizedMaterialInput@1":
        _validate_material(payload, repository)
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ProbeInputRecord(
        ref=ref,
        schema=schema,
        canonical_json=encoded,
    )


def _validate_material(
    payload: dict[str, Any],
    repository: FilesystemProjectRepository,
) -> None:
    expected = {
        "schema",
        "input_id",
        "authority_id",
        "source_uri",
        "object_ref",
        "media_type",
        "sha256",
    }
    if set(payload) != expected:
        raise ProbeInputError("authorized material fields drifted")
    for field in (
        "input_id",
        "authority_id",
        "source_uri",
        "object_ref",
        "media_type",
        "sha256",
    ):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ProbeInputError(
                f"authorized material {field} is invalid"
            )
    require_identifier(payload["input_id"], "input_id")
    require_identifier(payload["authority_id"], "authority_id")
    require_logical_ref(payload["source_uri"], "source_uri")
    project_id = repository.load_manifest().project_id
    if not payload["object_ref"].startswith(
        f"project://{project_id}/objects/"
    ):
        raise ProbeInputError(
            "authorized material object_ref is cross-project or non-object"
        )
    digest = payload["sha256"]
    if len(digest) != 64 or any(
        char not in "0123456789abcdef" for char in digest.lower()
    ):
        raise ProbeInputError("authorized material sha256 is invalid")
    prefix = f"project://{project_id}/"
    relative_path = unquote(payload["object_ref"][len(prefix) :])
    try:
        object_path = repository.layout.resolve_relative(relative_path)
    except ValueError as exc:
        raise ProbeInputError(
            "authorized material object_ref escapes the project"
        ) from exc
    if (
        not object_path.is_file()
        or hashlib.sha256(object_path.read_bytes()).hexdigest() != digest
    ):
        raise ProbeInputError(
            "authorized material object is missing or digest-mismatched"
        )
