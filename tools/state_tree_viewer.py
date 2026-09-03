"""Read-only live design-state tree viewer over P036 project directories.

This is a derived, disposable development view in the spirit of P057: it scans
project records, renders a browser tree bound to the exact backing files, and
owns no design, validation, persistence, or promotion authority. It never
writes inside the served root.

Usage:
    python tools/state_tree_viewer.py <projects-root> [--port 8766]
    python tools/state_tree_viewer.py <projects-root> \
        --stage-input <snapshot.json> --artifact-root <project-root>

``<root>`` is either one P036 project directory (contains ``canonical/``) or a
directory of projects (e.g. ``probes/``). The page polls a cheap directory
fingerprint and re-renders only when files actually change. ``--stage-input``
adds a separate ``/stage`` view over an explicitly supplied, version-guarded
pack/snapshot. The server never invokes snapshot generators or writes project
data; source drill-down resolves only refs present in that input and verifies
their SHA-256 under the explicit project root.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import sys

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from archflow.adapters.three_dm_inspector import (  # noqa: E402
    INSPECTION_SCHEMA_KEY_SETS,
    REQUIRED_INSPECTION_KEYS,
    SUPPORTED_INSPECTION_SCHEMAS,
)
from archflow.contracts.reading import read_key_diagnostics  # noqa: E402
from archflow.capabilities.stage_evidence_pack import (  # noqa: E402
    STAGE_EVIDENCE_PACK_KEYS,
)
from tools.build_pantheon_progress_snapshot import (  # noqa: E402
    PANTHEON_STAGE_PROGRESS_SNAPSHOT_KEYS,
)
from archflow.contracts.canonical import canonical_json_bytes

_STAGE_RE = re.compile(r"^(?P<kind>.+?)-(?P<stage>\d{3})-[0-9a-f]{64}\.json$")
_DIGEST_RE = re.compile(r"-(?P<digest>[0-9a-f]{64})\.json$")
_CANONICAL_RE = re.compile(r"^state-v(?P<version>\d+)-[0-9a-f]{64}\.json$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROJECT_URI_RE = re.compile(r"^project://(?P<project>[^/]+)/(?P<path>.+\.json)$")
_STAGE_PACK_SCHEMAS = frozenset({"StageEvidencePack@1"})
_PANEL_SNAPSHOT_SCHEMAS = frozenset(
    {"StageEvidencePanelSnapshot@1", "PantheonStageProgressSnapshot@1"}
)
_MAX_STAGE_BYTES = 16_000_000

_STAGE_PACK_KEYS = STAGE_EVIDENCE_PACK_KEYS
# StageEvidencePanelSnapshot@1 has no in-repo emitter: this viewer owns
# the input contract for externally supplied panel snapshots (M088).
_PANEL_SNAPSHOT_KEYS = frozenset(
    {
        "schema",
        "stage_pack",
        "stage_pack_digest",
        "panel",
        "three_dm_inspection",
        "read_only",
        "selection_authority",
        "evidence_authority",
        "stage_acceptance_authority",
        "canonical_write_authority",
    }
)
_PANTHEON_SNAPSHOT_KEYS = PANTHEON_STAGE_PROGRESS_SNAPSHOT_KEYS
class StagePanelError(ValueError):
    """An explicit Stage panel input is unreadable or malformed."""


class UnsupportedStageSchema(StagePanelError):
    """A Stage pack or 3DM inspection has an unsupported schema version."""


def _schema_guard(
    payload: object,
    *,
    supported: frozenset[str],
    label: str,
) -> dict:
    if not isinstance(payload, dict):
        raise StagePanelError(f"{label} must be a JSON object")
    schema = payload.get("schema")
    if not isinstance(schema, str) or not schema:
        raise UnsupportedStageSchema(
            f"{label} has no explicit schema; supported: "
            f"{', '.join(sorted(supported))}"
        )
    if schema not in supported:
        raise UnsupportedStageSchema(
            f"unsupported {label} schema {schema!r}; supported: "
            f"{', '.join(sorted(supported))}"
        )
    return payload


def _mapping(
    value: object,
    path: str,
    diagnostics: list[dict[str, str]],
    *,
    required: bool = False,
) -> dict:
    if value is None:
        if required:
            diagnostics.append(
                {"severity": "error", "path": path, "message": "missing"}
            )
        return {}
    if not isinstance(value, dict):
        diagnostics.append(
            {
                "severity": "error",
                "path": path,
                "message": "must be an object",
            }
        )
        return {}
    return dict(value)


def _items(
    value: object,
    path: str,
    diagnostics: list[dict[str, str]],
    *,
    required: bool = False,
) -> list[object]:
    if value is None:
        if required:
            diagnostics.append(
                {"severity": "error", "path": path, "message": "missing"}
            )
        return []
    if not isinstance(value, list):
        diagnostics.append(
            {
                "severity": "error",
                "path": path,
                "message": "must be an array",
            }
        )
        return []
    return list(value)


def _required_identity(
    value: object,
    diagnostics: list[dict[str, str]],
) -> dict[str, object]:
    identity = _mapping(value, "identity", diagnostics, required=True)
    normalized: dict[str, object] = {}
    for field in ("project_id", "run_id", "stage_id", "branch_id"):
        item = identity.get(field)
        if not isinstance(item, str) or not item.strip():
            diagnostics.append(
                {
                    "severity": "error",
                    "path": f"identity.{field}",
                    "message": "must be a non-empty string",
                }
            )
            normalized[field] = None
        else:
            normalized[field] = item
    stage_index = identity.get("stage_index")
    if stage_index is not None and not isinstance(stage_index, int):
        diagnostics.append(
            {
                "severity": "error",
                "path": "identity.stage_index",
                "message": "must be an integer when declared",
            }
        )
        stage_index = None
    normalized["stage_index"] = stage_index
    return normalized


def _normalize_decisions(
    value: object,
    diagnostics: list[dict[str, str]],
) -> list[dict[str, object]]:
    decisions = _items(value, "decisions", diagnostics, required=True)
    normalized: list[dict[str, object]] = []
    for index, item in enumerate(decisions):
        path = f"decisions[{index}]"
        decision = _mapping(item, path, diagnostics)
        decision_id = decision.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id:
            diagnostics.append(
                {
                    "severity": "error",
                    "path": f"{path}.decision_id",
                    "message": "must be a non-empty string",
                }
            )
        candidates = _items(
            decision.get("candidates"),
            f"{path}.candidates",
            diagnostics,
            required=True,
        )
        adjudication = _mapping(
            decision.get("adjudication"),
            f"{path}.adjudication",
            diagnostics,
            required=True,
        )
        normalized.append(
            {
                "decision_id": decision_id,
                "title": decision.get("title"),
                "status": decision.get("status"),
                "exact_predecessor": decision.get("exact_predecessor"),
                "candidates": candidates,
                "adjudication": adjudication,
                "constraints": _mapping(
                    decision.get("constraints"),
                    f"{path}.constraints",
                    diagnostics,
                ),
                "dependencies": _items(
                    decision.get("dependencies"),
                    f"{path}.dependencies",
                    diagnostics,
                ),
                "source_refs": _items(
                    decision.get("source_refs"),
                    f"{path}.source_refs",
                    diagnostics,
                ),
            }
        )
    return normalized


def _normalize_coverage(
    value: object,
    diagnostics: list[dict[str, str]],
) -> dict[str, object]:
    coverage = _mapping(value, "web_rag.coverage", diagnostics, required=True)
    covered = coverage.get("covered")
    total = coverage.get("total")
    ratio = coverage.get("ratio")
    origin = "declared"
    if ratio is None and isinstance(covered, int) and isinstance(total, int):
        ratio = covered / total if total else 0.0
        origin = "derived_from_declared_counts"
    if ratio is not None and not isinstance(ratio, (int, float)):
        diagnostics.append(
            {
                "severity": "error",
                "path": "web_rag.coverage.ratio",
                "message": "must be numeric when declared",
            }
        )
        ratio = None
    return {
        **coverage,
        "covered": covered,
        "total": total,
        "ratio": ratio,
        "ratio_origin": origin,
    }


def _stage_digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value, ascii=False)).hexdigest()


def _exact_keys(value: dict, expected: frozenset[str], label: str) -> None:
    if set(value) != set(expected):
        missing = sorted(set(expected) - set(value))
        extra = sorted(set(value) - set(expected))
        detail = []
        if missing:
            detail.append(f"missing={missing}")
        if extra:
            detail.append(f"extra={extra}")
        raise StagePanelError(f"{label} schema drifted ({'; '.join(detail)})")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StagePanelError(f"{field} must be non-empty text")
    return value


def _required_integer(
    value: object,
    field: str,
    *,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise StagePanelError(f"{field} must be an integer >= {minimum}")
    return value


def _required_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise StagePanelError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _strict_mapping(value: object, field: str) -> dict:
    if not isinstance(value, dict):
        raise StagePanelError(f"{field} must be an object")
    return dict(value)


def _strict_list(value: object, field: str) -> list:
    if not isinstance(value, list):
        raise StagePanelError(f"{field} must be an array")
    return list(value)


def _validate_record_ref(value: object, field: str) -> dict[str, object]:
    ref = _strict_mapping(value, field)
    expected = frozenset({"project_id", "relative_path", "sha256", "media_type"})
    _exact_keys(ref, expected, field)
    _required_text(ref["project_id"], f"{field}.project_id")
    _required_text(ref["relative_path"], f"{field}.relative_path")
    _required_sha256(ref["sha256"], f"{field}.sha256")
    _required_text(ref["media_type"], f"{field}.media_type")
    return ref


def _validate_artifact_ref(value: object, field: str) -> dict[str, object]:
    ref = _strict_mapping(value, field)
    expected = frozenset(
        {"project_id", "artifact_id", "relative_path", "sha256", "media_type"}
    )
    _exact_keys(ref, expected, field)
    _required_text(ref["project_id"], f"{field}.project_id")
    _required_text(ref["artifact_id"], f"{field}.artifact_id")
    _required_text(ref["relative_path"], f"{field}.relative_path")
    _required_sha256(ref["sha256"], f"{field}.sha256")
    _required_text(ref["media_type"], f"{field}.media_type")
    return ref


def _fallback_validate_stage_pack(payload: object) -> dict[str, object]:
    """Strict JSON fallback used until the typed capability reaches this tree."""

    pack = _schema_guard(
        payload,
        supported=_STAGE_PACK_SCHEMAS,
        label="StageEvidencePack",
    )
    _exact_keys(pack, _STAGE_PACK_KEYS, "StageEvidencePack@1")
    project_id = _required_text(pack["project_id"], "project_id")
    run_id = _required_text(pack["run_id"], "run_id")
    base = _strict_mapping(pack["base"], "base")
    _exact_keys(
        base,
        frozenset({"project_id", "version", "state_sha256"}),
        "base",
    )
    if base["project_id"] != project_id:
        raise StagePanelError("base belongs to another project")
    _required_integer(base["version"], "base.version")
    _required_sha256(base["state_sha256"], "base.state_sha256")

    branch = _strict_mapping(pack["branch"], "branch")
    _exact_keys(branch, frozenset({"branch_id", "epoch"}), "branch")
    _required_text(branch["branch_id"], "branch.branch_id")
    _required_integer(branch["epoch"], "branch.epoch")

    stage = _strict_mapping(pack["stage"], "stage")
    _exact_keys(
        stage,
        frozenset({"stage_id", "stage_index", "revision"}),
        "stage",
    )
    _required_text(stage["stage_id"], "stage.stage_id")
    stage_index = _required_integer(stage["stage_index"], "stage.stage_index")
    revision = _required_integer(stage["revision"], "stage.revision", minimum=1)
    program_digest = _required_sha256(pack["program_digest"], "program_digest")

    for field in ("scope_ref", "contract_ref"):
        ref = _validate_record_ref(pack[field], field)
        if ref["project_id"] != project_id:
            raise StagePanelError(f"{field} belongs to another project")

    predecessor = pack["predecessor"]
    if stage_index == 0:
        if predecessor is not None:
            raise StagePanelError("stage 0 cannot have a predecessor")
    else:
        predecessor = _strict_mapping(predecessor, "predecessor")
        _exact_keys(
            predecessor,
            frozenset(
                {"schema", "stage_id", "stage_index", "pack_ref", "program_digest"}
            ),
            "predecessor",
        )
        if predecessor["schema"] != "StagePackPredecessor@1":
            raise StagePanelError("predecessor schema changed")
        _required_text(predecessor["stage_id"], "predecessor.stage_id")
        if _required_integer(
            predecessor["stage_index"], "predecessor.stage_index"
        ) != stage_index - 1:
            raise StagePanelError("predecessor is not the exact previous stage")
        _validate_record_ref(predecessor["pack_ref"], "predecessor.pack_ref")
        _required_sha256(
            predecessor["program_digest"], "predecessor.program_digest"
        )

    supersedes = pack["supersedes_pack_ref"]
    if revision == 1 and supersedes is not None:
        raise StagePanelError("revision 1 cannot supersede another pack")
    if revision > 1 and supersedes is None:
        raise StagePanelError("revision N must bind the superseded pack")
    if supersedes is not None:
        _validate_record_ref(supersedes, "supersedes_pack_ref")

    bindings = _strict_list(pack["bindings"], "bindings")
    for index, item in enumerate(bindings):
        binding = _strict_mapping(item, f"bindings[{index}]")
        _exact_keys(
            binding,
            frozenset({"schema", "role", "ref"}),
            f"bindings[{index}]",
        )
        if binding["schema"] != "StageEvidenceBinding@1":
            raise StagePanelError(f"bindings[{index}] schema changed")
        _required_text(binding["role"], f"bindings[{index}].role")
        _validate_record_ref(binding["ref"], f"bindings[{index}].ref")

    artifacts = _strict_list(pack["artifacts"], "artifacts")
    cad_count = 0
    for index, item in enumerate(artifacts):
        artifact = _strict_mapping(item, f"artifacts[{index}]")
        _exact_keys(
            artifact,
            frozenset({"schema", "role", "ref", "program_digest"}),
            f"artifacts[{index}]",
        )
        if artifact["schema"] != "StageArtifactBinding@1":
            raise StagePanelError(f"artifacts[{index}] schema changed")
        role = _required_text(artifact["role"], f"artifacts[{index}].role")
        ref = _validate_artifact_ref(artifact["ref"], f"artifacts[{index}].ref")
        if ref["project_id"] != project_id:
            raise StagePanelError(f"artifacts[{index}] belongs to another project")
        if not str(ref["relative_path"]).startswith("objects/"):
            raise StagePanelError(f"artifacts[{index}] is outside the object store")
        if _required_sha256(
            artifact["program_digest"], f"artifacts[{index}].program_digest"
        ) != program_digest:
            raise StagePanelError(f"artifacts[{index}] program digest drifted")
        cad_count += role == "cad_model"

    gaps = _strict_list(pack["gaps"], "gaps")
    blocking = False
    for index, item in enumerate(gaps):
        gap = _strict_mapping(item, f"gaps[{index}]")
        _exact_keys(
            gap,
            frozenset(
                {
                    "schema",
                    "gap_id",
                    "kind",
                    "severity",
                    "description",
                    "decision_refs",
                    "remediation",
                }
            ),
            f"gaps[{index}]",
        )
        if gap["schema"] != "StageEvidenceGap@1":
            raise StagePanelError(f"gaps[{index}] schema changed")
        _required_text(gap["gap_id"], f"gaps[{index}].gap_id")
        _required_text(gap["kind"], f"gaps[{index}].kind")
        severity = _required_text(gap["severity"], f"gaps[{index}].severity")
        if severity not in {"blocking", "advisory"}:
            raise StagePanelError(f"gaps[{index}].severity is unsupported")
        blocking = blocking or severity == "blocking"
        _required_text(gap["description"], f"gaps[{index}].description")
        _strict_list(gap["decision_refs"], f"gaps[{index}].decision_refs")
        if not isinstance(gap["remediation"], str):
            raise StagePanelError(f"gaps[{index}].remediation must be text")

    closure = _strict_mapping(pack["closure"], "closure")
    _exact_keys(
        closure,
        frozenset(
            {
                "schema",
                "evidence_sufficient",
                "dependencies_closed",
                "hard_gates_passed",
                "stage_ready",
                "model_artifact_current",
                "closed",
            }
        ),
        "closure",
    )
    if closure["schema"] != "StageClosureSummary@1":
        raise StagePanelError("closure schema changed")
    flags = []
    for field in (
        "evidence_sufficient",
        "dependencies_closed",
        "hard_gates_passed",
        "stage_ready",
        "model_artifact_current",
    ):
        if not isinstance(closure[field], bool):
            raise StagePanelError(f"closure.{field} must be boolean")
        flags.append(closure[field])
    if closure["closed"] is not all(flags):
        raise StagePanelError("closure.closed drifted")
    if closure["model_artifact_current"] is not (cad_count > 0):
        raise StagePanelError("closure model artifact flag disagrees with CAD binding")

    status = pack["compilation_status"]
    if status not in {"INCOMPLETE", "COMPLETE"}:
        raise StagePanelError("compilation_status is unsupported")
    if status == "COMPLETE" and (blocking or not closure["closed"] or not cad_count):
        raise StagePanelError("COMPLETE pack has open closure, gap, or CAD binding")
    return dict(pack)


def _typed_stage_pack(payload: object) -> tuple[dict[str, object], bool]:
    """Use the canonical parser when present; otherwise use the strict guard."""

    module_name = "archflow.capabilities.stage_evidence_pack"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return _fallback_validate_stage_pack(payload), False
        raise StagePanelError(
            f"cannot load StageEvidencePack dependency: {exc.name}"
        ) from exc
    except Exception as exc:
        raise StagePanelError(f"cannot load StageEvidencePack contract: {exc}") from exc
    pack_type = getattr(module, "StageEvidencePack", None)
    loader = getattr(pack_type, "from_dict", None)
    if not callable(loader):
        raise StagePanelError("StageEvidencePack.from_dict(value) is unavailable")
    try:
        parsed = loader(payload)
        canonical = _inspection_payload(parsed)
    except Exception as exc:
        raise StagePanelError(f"StageEvidencePack validation failed: {exc}") from exc
    if not isinstance(canonical, dict):
        raise StagePanelError("StageEvidencePack.to_dict() did not return an object")
    return canonical, True


def _binding_card(binding: dict[str, object]) -> dict[str, object]:
    role = str(binding.get("role") or "binding")
    ref = binding.get("ref")
    return {
        "title": role.replace("_", " "),
        "role": role,
        "status": "bound_ref_only",
        "description": "Bound by StageEvidencePack; expanded record not in this input.",
        "ref": ref,
        "source_refs": [ref] if isinstance(ref, dict) else [],
    }


def _compact_panel(pack: dict[str, object]) -> dict[str, object]:
    bindings = [
        item for item in pack.get("bindings", []) if isinstance(item, dict)
    ]
    by_role: dict[str, list[dict[str, object]]] = {}
    for item in bindings:
        by_role.setdefault(str(item.get("role")), []).append(_binding_card(item))
    artifacts = [
        item for item in pack.get("artifacts", []) if isinstance(item, dict)
    ]
    gaps = [item for item in pack.get("gaps", []) if isinstance(item, dict)]
    return {
        "program_length_unit": None,
        "decisions": [],
        "constraints": {"hard": [], "soft": []},
        "dependencies": by_role.get("dependency_ledger", []),
        "web_rag": {
            "queries": by_role.get("query", []),
            "coverage": {
                "covered": None,
                "total": None,
                "ratio": None,
                "ratio_origin": "unavailable_in_compact_pack",
            },
            "conflicts": [item for item in gaps if item.get("kind") == "conflict"],
            "gaps": gaps,
        },
        "conflicts": [item for item in gaps if item.get("kind") == "conflict"],
        "gaps": gaps,
        "commitments": by_role.get("design_state", []),
        "intermediate_representations": by_role.get("geometry_program", []),
        "gates": [
            *by_role.get("stage_gate", []),
            *by_role.get("stage_convergence", []),
            {
                "title": "Stage pack closure",
                "status": "closed" if pack.get("closure", {}).get("closed") else "open",
                "description": (
                    "Compact pack closure; this is not Stage acceptance authority."
                ),
                "closure": pack.get("closure"),
            },
        ],
        "models": [
            item for item in artifacts if item.get("role") in {"cad_model", "ifc_model"}
        ],
        "exports": [
            item
            for item in artifacts
            if item.get("role") not in {"cad_model", "ifc_model"}
        ],
        "stage_acceptance": None,
        "diagnostics": [
            {
                "severity": "error",
                "path": "panel.decisions",
                "message": (
                    "StageEvidencePack is a compact index; a project-generated "
                    "StageEvidencePanelSnapshot@1 must explicitly dereference decision, "
                    "constraint, RAG, commitment, IR, and review records."
                ),
            }
        ],
    }


def adapt_stage_evidence_pack(payload: object) -> dict[str, object]:
    """Normalize the real compact StageEvidencePack@1 without inference."""

    pack, typed_validation = _typed_stage_pack(payload)
    stage = dict(pack["stage"])
    branch = dict(pack["branch"])
    base = dict(pack["base"])
    diagnostics: list[dict[str, str]] = []
    cad = [
        item
        for item in pack["artifacts"]
        if isinstance(item, dict) and item.get("role") == "cad_model"
    ]
    preview = [
        item
        for item in pack["artifacts"]
        if isinstance(item, dict) and item.get("role") == "preview"
    ]
    selected_cad: dict[str, object] | None = cad[0] if len(cad) == 1 else None
    selected_preview = preview[0] if len(preview) == 1 else None
    if not cad:
        diagnostics.append(
            {
                "severity": "error",
                "path": "artifacts[cad_model]",
                "message": "no CAD model artifact is bound; no older file is selected",
            }
        )
    elif len(cad) > 1:
        diagnostics.append(
            {
                "severity": "error",
                "path": "artifacts[cad_model]",
                "message": (
                    "multiple CAD artifacts are bound; the snapshot must explicitly "
                    "select one instead of using filename or mtime"
                ),
            }
        )
    if len(preview) > 1:
        diagnostics.append(
            {
                "severity": "error",
                "path": "artifacts[preview]",
                "message": "multiple preview artifacts are ambiguous",
            }
        )
    artifact_ref = selected_cad.get("ref") if selected_cad else None
    return {
        "schema": pack["schema"],
        "compact_index": True,
        "typed_validation": typed_validation,
        "read_only": True,
        "selection_authority": False,
        "evidence_authority": False,
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
        "identity": {
            "project_id": pack["project_id"],
            "run_id": pack["run_id"],
            "stage_id": stage["stage_id"],
            "stage_index": stage["stage_index"],
            "revision": stage["revision"],
            "branch_id": branch["branch_id"],
            "branch_epoch": branch["epoch"],
            "base_version": base["version"],
            "base_state_sha256": base["state_sha256"],
        },
        "scope_ref": pack["scope_ref"],
        "contract_ref": pack["contract_ref"],
        "exact_predecessor": pack["predecessor"],
        "supersedes_pack_ref": pack["supersedes_pack_ref"],
        "program_digest": pack["program_digest"],
        "artifact_ref": artifact_ref,
        "artifact_sha256": (
            artifact_ref.get("sha256") if isinstance(artifact_ref, dict) else None
        ),
        "artifact_size_bytes": None,
        "selected_cad_binding": selected_cad,
        "selected_preview_binding": selected_preview,
        "bindings": pack["bindings"],
        "artifacts": pack["artifacts"],
        "gaps": pack["gaps"],
        "closure": pack["closure"],
        "compilation_status": pack["compilation_status"],
        "pack_digest": _stage_digest(pack),
        "diagnostics": diagnostics,
    }


def _normalize_panel(panel_value: object) -> dict[str, object]:
    diagnostics: list[dict[str, str]] = []
    panel = _mapping(panel_value, "panel", diagnostics, required=True)
    constraints = _mapping(
        panel.get("constraints"), "panel.constraints", diagnostics, required=True
    )
    web_rag = _mapping(
        panel.get("web_rag"), "panel.web_rag", diagnostics, required=True
    )
    normalized = {
        "program_length_unit": panel.get("program_length_unit"),
        "decisions": _normalize_decisions(panel.get("decisions"), diagnostics),
        "constraints": {
            "hard": _items(
                constraints.get("hard"),
                "panel.constraints.hard",
                diagnostics,
                required=True,
            ),
            "soft": _items(
                constraints.get("soft"),
                "panel.constraints.soft",
                diagnostics,
                required=True,
            ),
        },
        "dependencies": _items(
            panel.get("dependencies"),
            "panel.dependencies",
            diagnostics,
            required=True,
        ),
        "web_rag": {
            **web_rag,
            "queries": _items(
                web_rag.get("queries"),
                "panel.web_rag.queries",
                diagnostics,
                required=True,
            ),
            "coverage": _normalize_coverage(web_rag.get("coverage"), diagnostics),
            "conflicts": _items(
                web_rag.get("conflicts"),
                "panel.web_rag.conflicts",
                diagnostics,
            ),
            "gaps": _items(
                web_rag.get("gaps"), "panel.web_rag.gaps", diagnostics
            ),
        },
        "conflicts": _items(panel.get("conflicts"), "panel.conflicts", diagnostics),
        "gaps": _items(panel.get("gaps"), "panel.gaps", diagnostics),
        "commitments": _items(
            panel.get("commitments"),
            "panel.commitments",
            diagnostics,
            required=True,
        ),
        "intermediate_representations": _items(
            panel.get("intermediate_representations"),
            "panel.intermediate_representations",
            diagnostics,
            required=True,
        ),
        "gates": _items(
            panel.get("gates"), "panel.gates", diagnostics, required=True
        ),
        "models": _items(
            panel.get("models"), "panel.models", diagnostics, required=True
        ),
        "exports": _items(
            panel.get("exports"), "panel.exports", diagnostics, required=True
        ),
        "stage_acceptance": panel.get("stage_acceptance"),
        "diagnostics": diagnostics,
    }
    return normalized


def _pantheon_gap(
    gap_id: str,
    description: str,
    *,
    kind: str = "missing_evidence",
) -> dict[str, object]:
    return {
        "gap_id": gap_id,
        "kind": kind,
        "severity": "blocking",
        "description": description,
        "decision_refs": [],
        "remediation": "Resolve in project evidence; the viewer has no write authority.",
    }


def adapt_pantheon_stage_snapshot(payload: object) -> dict[str, object]:
    """Adapt the project-generated read-only Pantheon snapshot without promotion."""

    snapshot = _schema_guard(
        payload,
        supported=frozenset({"PantheonStageProgressSnapshot@1"}),
        label="Pantheon stage progress snapshot",
    )
    _exact_keys(
        snapshot,
        _PANTHEON_SNAPSHOT_KEYS,
        "PantheonStageProgressSnapshot@1",
    )
    project_id = _required_text(snapshot["project_id"], "project_id")
    run_id = _required_text(
        snapshot["current_stage_run_id"], "current_stage_run_id"
    )
    stages = _strict_list(snapshot["stages"], "stages")
    if not stages:
        raise StagePanelError("Pantheon snapshot has no Stage rows")
    stage_rows: list[dict[str, object]] = []
    indexes: set[int] = set()
    for index, value in enumerate(stages):
        row = _strict_mapping(value, f"stages[{index}]")
        stage_index = _required_integer(row.get("stage"), f"stages[{index}].stage")
        if stage_index in indexes:
            raise StagePanelError("Pantheon snapshot contains duplicate Stage indexes")
        indexes.add(stage_index)
        review_ref = _validate_record_ref(
            row.get("review_ref"), f"stages[{index}].review_ref"
        )
        if review_ref["project_id"] != project_id:
            raise StagePanelError("Stage review belongs to another project")
        stage_rows.append(dict(row))
    stage_rows.sort(key=lambda item: int(item["stage"]))
    current = stage_rows[-1]

    detail_value = snapshot["detail_candidate"]
    if detail_value is None:
        detail: dict[str, object] = {}
    else:
        detail = _strict_mapping(detail_value, "detail_candidate")
    candidate_manifest = _strict_mapping(
        snapshot["candidate_manifest"], "candidate_manifest"
    )
    manifest_ref = _validate_record_ref(
        candidate_manifest.get("ref"), "candidate_manifest.ref"
    )
    plan_ref = (
        _validate_record_ref(detail.get("plan_ref"), "detail_candidate.plan_ref")
        if detail
        else None
    )
    formal = _strict_mapping(snapshot["formal_closure"], "formal_closure")
    if formal.get("schema") != "StageClosureInventory@1":
        raise StagePanelError("formal closure inventory schema changed")
    record_counts = _strict_mapping(
        formal.get("record_counts"), "formal_closure.record_counts"
    )
    formal_pack_count = record_counts.get("stage_evidence_pack")
    if isinstance(formal_pack_count, bool) or not isinstance(formal_pack_count, int):
        raise StagePanelError("formal StageEvidencePack count must be an integer")
    formal_pack_present = bool(formal.get("formal_closure_present")) and formal_pack_count > 0

    alignment = _strict_mapping(snapshot["model_alignment"], "model_alignment")
    if alignment.get("schema") != "StageModelAlignment@1":
        raise StagePanelError("model alignment schema changed")
    model = _strict_mapping(snapshot["model"], "model")
    inspection = model.get("inspection")
    normalized_inspection = (
        adapt_three_dm_inspection(inspection)
        if isinstance(inspection, dict)
        else None
    )
    model_path = model.get("model_relative_path")
    file_sha = (
        normalized_inspection.get("file_sha256")
        if isinstance(normalized_inspection, dict)
        else None
    )
    artifact_ref = None
    if isinstance(model_path, str) and model_path and isinstance(file_sha, str):
        artifact_ref = {
            "project_id": project_id,
            "relative_path": model_path,
            "sha256": file_sha,
            "media_type": "model/vnd.rhino.3dm",
            "source": "PantheonStageProgressSnapshot@1.model",
        }
    execution_program = alignment.get("execution_program_digest")
    current_program = _required_sha256(
        current.get("program_digest"), "current Stage program_digest"
    )
    selected_binding = (
        {
            "role": "cad_model",
            "ref": artifact_ref,
            "program_digest": execution_program,
            "binding_status": "retained_prior_run_model",
        }
        if artifact_ref is not None
        else None
    )

    gaps: list[dict[str, object]] = []
    for reason in alignment.get("reason_codes", []):
        gaps.append(
            _pantheon_gap(
                str(reason),
                f"model alignment blocked: {reason}",
                kind="digest_mismatch" if reason == "model.stale_program" else "gate_failed",
            )
        )
    expected_workspaces = _strict_list(
        snapshot["expected_model_workspaces"], "expected_model_workspaces"
    )
    for item in expected_workspaces:
        if isinstance(item, dict) and item.get("model_exists") is not True:
            gaps.append(
                _pantheon_gap(
                    f"model.missing.{item.get('schema', 'workspace')}",
                    f"current Stage model is missing: {item.get('model_relative_path')}",
                    kind="artifact_missing",
                )
            )
    if not formal_pack_present:
        gaps.append(
            _pantheon_gap(
                "formal.stage_evidence_pack_missing",
                "formal StageEvidencePack record count is zero",
                kind="artifact_missing",
            )
        )
    for reason in current.get("hold_reasons", []):
        gaps.append(_pantheon_gap(f"stage.hold.{reason}", str(reason), kind="gate_failed"))

    branch_identity = detail.get("branch_identity")
    branch_status = (
        "agent_proposed_unbound"
        if detail.get("authority_state") == "agent-proposed"
        else "unbound"
    )
    predecessor_digest = current.get("predecessor_program_digest")
    predecessor = None
    if current["stage"] != 0:
        predecessor = {
            "stage_index": int(current["stage"]) - 1,
            "program_digest": predecessor_digest,
            "pack_ref": None,
            "binding_status": "program_digest_only_formal_pack_missing",
        }
    closure = {
        "schema": "StageClosureInventoryView@1",
        "evidence_sufficient": False,
        "dependencies_closed": False,
        "hard_gates_passed": False,
        "stage_ready": False,
        "model_artifact_current": False,
        "closed": False,
        "record_counts": record_counts,
        "formal_closure_present": bool(formal.get("formal_closure_present")),
        "note": formal.get("note"),
    }
    pack = {
        "schema": snapshot["schema"],
        "compact_index": False,
        "typed_validation": True,
        "read_only": True,
        "selection_authority": False,
        "evidence_authority": False,
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
        "identity": {
            "project_id": project_id,
            "run_id": run_id,
            "stage_id": str(current.get("label") or f"stage-{current['stage']}"),
            "stage_index": current["stage"],
            "revision": None,
            "branch_id": branch_identity,
            "branch_epoch": None,
            "branch_status": branch_status,
            "base_version": snapshot.get("canonical_head", {}).get("version"),
            "base_state_sha256": snapshot.get("canonical_head", {}).get(
                "state_sha256"
            ),
        },
        "scope_ref": None,
        "contract_ref": current.get("review_ref"),
        "exact_predecessor": predecessor,
        "supersedes_pack_ref": None,
        "program_digest": current_program,
        "artifact_ref": artifact_ref,
        "artifact_sha256": file_sha,
        "artifact_size_bytes": (
            normalized_inspection.get("file_size_bytes")
            if isinstance(normalized_inspection, dict)
            else None
        ),
        "selected_cad_binding": selected_binding,
        "selected_preview_binding": None,
        "bindings": [],
        "artifacts": [],
        "gaps": gaps,
        "closure": closure,
        "compilation_status": "INCOMPLETE",
        "pack_digest": None,
        "stage_pack_binding_status": (
            "inventory_present_unexpanded" if formal_pack_present else "missing"
        ),
        "diagnostics": [
            {
                "severity": "error",
                "path": "formal_closure.stage_evidence_pack",
                "message": (
                    "formal StageEvidencePack is absent; declaration checks are not "
                    "Stage completion"
                ),
            },
            {
                "severity": "error",
                "path": "identity.branch",
                "message": (
                    "formal branch scope record count is zero; any detail branch "
                    "identity is unavailable or agent-proposed"
                ),
            },
        ],
    }
    gates = []
    for row in stage_rows:
        gates.append(
            {
                "title": f"Stage {row.get('stage')} · {row.get('label')}",
                "status": row.get("disposition"),
                "checks_status": row.get("checks_status"),
                "accepted_archive_created": row.get("accepted_archive_created"),
                "formal_stage_pack_status": row.get("formal_stage_pack_status"),
                "hold_reasons": row.get("hold_reasons", []),
                "description": (
                    "Declaration checks are local only; disposition and formal pack "
                    "control this card's blocked status."
                ),
                "source_refs": [row.get("review_ref")],
            }
        )
    panel = {
        "program_length_unit": alignment.get("expected_unit"),
        "stages": stage_rows,
        "decisions": [],
        "constraints": {"hard": [], "soft": []},
        "dependencies": [],
        "web_rag": {
            "queries": [],
            "coverage": {
                "covered": None,
                "total": None,
                "ratio": None,
                "ratio_origin": "unavailable_formal_sufficiency_missing",
            },
            "conflicts": [],
            "gaps": gaps,
        },
        "conflicts": [],
        "gaps": gaps,
        "commitments": [
            {
                "title": "Current Stage commitments",
                "status": "missing_formal_record",
                "description": "No formal design-state/commitment record is in closure inventory.",
            }
        ],
        "intermediate_representations": [
            {
                "title": "Current Stage program",
                "status": "candidate_hold",
                "program_digest": current_program,
                "predecessor_program_digest": predecessor_digest,
                "source_refs": [
                    item
                    for item in (current.get("review_ref"), plan_ref)
                    if item is not None
                ],
            }
        ],
        "gates": gates,
        "models": [
            *expected_workspaces,
            {
                "title": "Latest retained inspected 3DM",
                "status": alignment.get("status"),
                "run_id": model.get("run_id"),
                "model_relative_path": model_path,
                "reason_codes": alignment.get("reason_codes", []),
                "source_refs": [model.get("execution_ref")],
            },
        ],
        "exports": [
            {
                "title": "Candidate manifest",
                "status": candidate_manifest.get("disposition"),
                "source_refs": [manifest_ref],
            }
        ],
        "stage_acceptance": {
            "accepted": False,
            "authority_ref": manifest_ref,
            "program_digest": current_program,
            "artifact_ref": None,
            "artifact_sha256": None,
            "disposition": candidate_manifest.get("disposition"),
            "reason": "accepted_archive_created=false and disposition=HOLD",
        },
        "detail_plan_ref": plan_ref,
        "model_execution_ref": model.get("execution_ref"),
        "embedded_execution_verification": model.get("execution_verification"),
        "preview": {
            "status": "missing",
            "message": "No preview/thumbnail artifact is bound by this snapshot.",
            "source_refs": [],
        },
        "model_alignment": alignment,
        "formal_closure": formal,
        "diagnostics": list(pack["diagnostics"]),
    }
    return {
        "input_schema": snapshot["schema"],
        "pack": pack,
        "panel": panel,
        "injected_three_dm_inspection": inspection,
        "snapshot_binding_valid": False,
        "snapshot_diagnostics": [],
    }


def adapt_panel_input(payload: object) -> dict[str, object]:
    """Accept a compact pack or a project-generated non-authoritative snapshot."""

    if not isinstance(payload, dict):
        raise StagePanelError("Stage panel input must be a JSON object")
    schema = payload.get("schema")
    if schema in _STAGE_PACK_SCHEMAS:
        pack = adapt_stage_evidence_pack(payload)
        return {
            "input_schema": schema,
            "pack": pack,
            "panel": _compact_panel(pack),
            "injected_three_dm_inspection": None,
            "snapshot_binding_valid": None,
            "snapshot_diagnostics": [],
        }
    if schema == "PantheonStageProgressSnapshot@1":
        return adapt_pantheon_stage_snapshot(payload)
    snapshot = _schema_guard(
        payload,
        supported=_PANEL_SNAPSHOT_SCHEMAS,
        label="Stage panel snapshot",
    )
    _exact_keys(snapshot, _PANEL_SNAPSHOT_KEYS, "StageEvidencePanelSnapshot@1")
    if snapshot["read_only"] is not True:
        raise StagePanelError("Stage panel snapshot must declare read_only=true")
    raw_pack = _strict_mapping(snapshot["stage_pack"], "stage_pack")
    pack = adapt_stage_evidence_pack(raw_pack)
    declared_digest = _required_sha256(
        snapshot["stage_pack_digest"], "stage_pack_digest"
    )
    actual_digest = _stage_digest(raw_pack)
    valid = declared_digest == actual_digest == pack["pack_digest"]
    snapshot_diagnostics: list[dict[str, str]] = []
    if not valid:
        snapshot_diagnostics.append(
            {
                "severity": "error",
                "path": "stage_pack_digest",
                "message": "snapshot is not bound to the embedded StageEvidencePack",
            }
        )
    injected = snapshot["three_dm_inspection"]
    if injected is not None and not isinstance(injected, dict):
        raise StagePanelError("three_dm_inspection must be null or an object")
    return {
        "input_schema": snapshot["schema"],
        "pack": pack,
        "panel": _normalize_panel(snapshot["panel"]),
        "injected_three_dm_inspection": injected,
        "snapshot_binding_valid": valid,
        "snapshot_diagnostics": snapshot_diagnostics,
    }


def adapt_three_dm_inspection(payload: object) -> dict[str, object]:
    """Normalize a supported headless ThreeDmInspectionSummary record.

    Version-aware and read-tolerant (P088): a missing required key fails
    closed; unknown keys inside a supported version become warning
    diagnostics and the read continues. Write-side exactness is the
    producer's contract, not this reader's.
    """

    inspection = _schema_guard(
        payload,
        supported=SUPPORTED_INSPECTION_SCHEMAS,
        label="3DM inspection",
    )
    schema = inspection["schema"]
    missing, diagnostics = read_key_diagnostics(
        inspection,
        required=REQUIRED_INSPECTION_KEYS,
        known=INSPECTION_SCHEMA_KEY_SETS[schema],
        label=schema,
    )
    if missing:
        raise StagePanelError(
            "3DM inspection is missing required keys: " + ", ".join(missing)
        )
    if inspection["read_only"] is not True:
        diagnostics.append(
            {
                "severity": "error",
                "path": "read_only",
                "message": "inspection must declare read_only=true",
            }
        )
    if inspection["rhino_process_started"] is not False:
        diagnostics.append(
            {
                "severity": "error",
                "path": "rhino_process_started",
                "message": "inspection is rejected because a Rhino process was started",
            }
        )
    file_sha256 = inspection["file_sha256"]
    if not isinstance(file_sha256, str) or _SHA256_RE.fullmatch(file_sha256) is None:
        diagnostics.append(
            {
                "severity": "error",
                "path": "file_sha256",
                "message": "must be a lowercase SHA-256 digest",
            }
        )
    integer_fields = (
        "file_bytes",
        "three_dm_version",
        "archive_version",
        "object_count",
        "top_level_object_count",
        "instance_definition_member_count",
        "bbox_contributing_geometry_count",
    )
    for field in integer_fields:
        value = inspection[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            diagnostics.append(
                {
                    "severity": "error",
                    "path": field,
                    "message": "must be a non-negative integer",
                }
            )
    layers = _items(inspection["layers"], "layers", diagnostics, required=True)
    by_type = _mapping(
        inspection["object_counts_by_type"],
        "object_counts_by_type",
        diagnostics,
        required=True,
    )
    by_layer = _items(
        inspection["object_counts_by_layer"],
        "object_counts_by_layer",
        diagnostics,
        required=True,
    )
    units = _mapping(inspection["units"], "units", diagnostics, required=True)
    bbox = inspection["aggregate_bbox"]
    if bbox is not None and not isinstance(bbox, dict):
        diagnostics.append(
            {
                "severity": "error",
                "path": "aggregate_bbox",
                "message": "must be null or an object",
            }
        )
        bbox = None
    return {
        "schema": inspection["schema"],
        "headless": (
            inspection["read_only"] is True
            and inspection["rhino_process_started"] is False
        ),
        "read_only": inspection["read_only"],
        "rhino_process_started": inspection["rhino_process_started"],
        "file_sha256": file_sha256,
        "file_size_bytes": inspection["file_bytes"],
        "file_version": {
            "three_dm_version": inspection["three_dm_version"],
            "archive_version": inspection["archive_version"],
        },
        "model_units": units,
        "layers": layers,
        "object_counts": {
            "total": inspection["object_count"],
            "top_level": inspection["top_level_object_count"],
            "instance_definition_members": inspection[
                "instance_definition_member_count"
            ],
            "by_type": by_type,
            "by_layer": by_layer,
        },
        "block_definitions": _items(
            inspection["instance_definitions"],
            "instance_definitions",
            diagnostics,
            required=True,
        ),
        "block_instances": _items(
            inspection["instance_references"],
            "instance_references",
            diagnostics,
            required=True,
        ),
        "bbox": {
            "source": f"{schema}.aggregate_bbox",
            "coordinate_system": "rhino_native_xyz",
            "aggregate": bbox,
            "contributing_geometry_count": inspection[
                "bbox_contributing_geometry_count"
            ],
        },
        "document_user_strings": _items(
            inspection["document_user_strings"],
            "document_user_strings",
            diagnostics,
            required=True,
        ),
        "object_user_strings": _items(
            inspection["object_user_strings"],
            "object_user_strings",
            diagnostics,
            required=True,
        ),
        "diagnostics": diagnostics,
    }


def _read_json_object(path: Path, *, label: str) -> tuple[dict, bytes]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise StagePanelError(f"cannot stat {label}: {exc}") from exc
    if size > _MAX_STAGE_BYTES:
        raise StagePanelError(
            f"{label} exceeds {_MAX_STAGE_BYTES} byte read bound"
        )
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise StagePanelError(f"cannot read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StagePanelError(f"{label} must contain one JSON object")
    return payload, raw


class StagePackSource:
    """Read only explicitly supplied pack files; never discovers run state."""

    def __init__(self, source: Path) -> None:
        resolved = source.resolve()
        if resolved.is_file():
            self._directory = resolved.parent
            self._paths = {resolved.name: resolved}
        elif resolved.is_dir():
            self._directory = resolved
            self._paths = {
                path.name: path
                for path in sorted(resolved.glob("*.json"))
                if path.is_file()
            }
        else:
            raise StagePanelError(f"stage-pack input does not exist: {resolved}")
        if not self._paths:
            raise StagePanelError(
                f"no direct JSON files found in stage-pack input: {resolved}"
            )

    @property
    def directory(self) -> Path:
        return self._directory

    def names(self) -> list[str]:
        return sorted(self._paths)

    def path_for(self, name: str) -> Path:
        path = self._paths.get(name)
        if path is None:
            raise StagePanelError("unknown StageEvidencePack selection")
        return path

    def describe(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for name in self.names():
            path = self._paths[name]
            payload: dict[str, object] = {}
            try:
                payload, raw = _read_json_object(path, label=name)
                adapted = adapt_panel_input(payload)
                pack = adapted["pack"]
                entries.append(
                    {
                        "name": name,
                        "schema": adapted["input_schema"],
                        "supported": True,
                        "identity": pack["identity"],
                        "fingerprint": hashlib.sha256(raw).hexdigest()[:16],
                        "snapshot_binding_valid": adapted[
                            "snapshot_binding_valid"
                        ],
                    }
                )
            except (StagePanelError, TypeError, ValueError) as exc:
                entries.append(
                    {
                        "name": name,
                        "schema": payload.get("schema"),
                        "supported": False,
                        "error": str(exc),
                    }
                )
        return sorted(
            entries,
            key=lambda item: (
                not bool(item.get("supported")),
                -int(item.get("identity", {}).get("stage_index") or 0),
                -int(item.get("identity", {}).get("revision") or 0),
                str(item.get("name")),
            ),
        )


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise StagePanelError(f"cannot read 3DM artifact: {exc}") from exc
    return digest.hexdigest(), size


def _artifact_relative_path(artifact_ref: object) -> str:
    if isinstance(artifact_ref, str) and artifact_ref.strip():
        return artifact_ref
    if isinstance(artifact_ref, dict):
        value = artifact_ref.get("relative_path")
        if isinstance(value, str) and value.strip():
            return value
    raise StagePanelError(
        "artifact_ref must be a relative path or an object with relative_path"
    )


def _resolve_artifact(
    artifact_ref: object,
    *,
    artifact_root: Path | None,
) -> Path:
    if artifact_root is None:
        raise StagePanelError(
            "artifact root is required; no path is inferred from the pack"
        )
    root = artifact_root.resolve()
    if not root.is_dir():
        raise StagePanelError(f"artifact root is not a directory: {root}")
    relative = Path(_artifact_relative_path(artifact_ref))
    if relative.is_absolute():
        raise StagePanelError("artifact_ref must be project-root-relative")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise StagePanelError("artifact_ref escapes the explicit artifact root") from exc
    if candidate.suffix.lower() != ".3dm":
        raise StagePanelError("artifact_ref does not identify a .3dm file")
    return candidate


def _resolve_record_ref(
    record_ref: object,
    *,
    project_root: Path | None,
) -> Path:
    if project_root is None:
        raise StagePanelError("project root is required to open a source record")
    if not isinstance(record_ref, dict):
        raise StagePanelError("source record ref must be an object")
    relative_path = record_ref.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise StagePanelError("source record ref has no relative_path")
    relative = Path(relative_path)
    if relative.is_absolute():
        raise StagePanelError("source record ref must be project-root-relative")
    root = project_root.resolve()
    if not root.is_dir():
        raise StagePanelError(f"project root is not a directory: {root}")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise StagePanelError("source record ref escapes the explicit project root") from exc
    if candidate.suffix.lower() != ".json":
        raise StagePanelError("source record ref does not identify a JSON record")
    return candidate


def _load_bound_json_ref(
    record_ref: object,
    *,
    project_root: Path | None,
) -> tuple[dict[str, object], Path]:
    path = _resolve_record_ref(record_ref, project_root=project_root)
    payload, raw = _read_json_object(path, label=path.name)
    expected = record_ref.get("sha256") if isinstance(record_ref, dict) else None
    actual = hashlib.sha256(raw).hexdigest()
    if not isinstance(expected, str) or expected != actual:
        raise StagePanelError(
            f"source record digest mismatch: expected {expected}, actual {actual}"
        )
    return payload, path


def _collect_record_refs(value: object) -> list[dict[str, object]]:
    found: dict[tuple[str, str], dict[str, object]] = {}

    def visit(item: object) -> None:
        if isinstance(item, dict):
            keys = {"project_id", "relative_path", "sha256", "media_type"}
            if keys <= set(item):
                relative_path = item.get("relative_path")
                sha256 = item.get("sha256")
                media_type = item.get("media_type")
                if (
                    isinstance(relative_path, str)
                    and relative_path.endswith(".json")
                    and isinstance(sha256, str)
                    and _SHA256_RE.fullmatch(sha256)
                    and media_type == "application/json"
                ):
                    found[(relative_path, sha256)] = {
                        key: item.get(key) for key in sorted(keys)
                    }
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(
        found.values(),
        key=lambda item: (str(item["relative_path"]), str(item["sha256"])),
    )


def _project_uri_ref(
    value: object,
    *,
    expected_project_id: str | None = None,
) -> dict[str, object] | None:
    """Convert one content-addressed project URI into a bounded record ref."""

    if not isinstance(value, str):
        return None
    match = _PROJECT_URI_RE.fullmatch(value)
    if match is None:
        return None
    project_id = match.group("project")
    relative_path = match.group("path")
    if expected_project_id is not None and project_id != expected_project_id:
        return None
    if (
        "\\" in relative_path
        or relative_path.startswith("/")
        or ".." in Path(relative_path).parts
    ):
        return None
    digest_match = _DIGEST_RE.search(relative_path)
    if digest_match is None:
        return None
    return {
        "project_id": project_id,
        "relative_path": relative_path,
        "sha256": digest_match.group("digest"),
        "media_type": "application/json",
    }


def _normalize_source_refs(
    value: object,
    *,
    expected_project_id: str,
) -> list[object]:
    """Keep explicit sources while making bound project URIs drillable."""

    if not isinstance(value, list):
        return []
    normalized: list[object] = []
    for item in value:
        if item is None or item == "":
            continue
        converted = _project_uri_ref(
            item,
            expected_project_id=expected_project_id,
        )
        normalized.append(converted if converted is not None else item)
    return normalized


def _collect_project_uri_refs(
    value: object,
    *,
    expected_project_id: str,
) -> list[dict[str, object]]:
    found: dict[tuple[str, str], dict[str, object]] = {}

    def visit(item: object) -> None:
        converted = _project_uri_ref(
            item,
            expected_project_id=expected_project_id,
        )
        if converted is not None:
            found[(str(converted["relative_path"]), str(converted["sha256"]))] = (
                converted
            )
            return
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(
        found.values(),
        key=lambda item: (str(item["relative_path"]), str(item["sha256"])),
    )


def load_stage_source_record(
    input_path: Path,
    source_sha256: str,
    *,
    project_root: Path | None,
) -> dict[str, object]:
    _required_sha256(source_sha256, "source sha256")
    input_payload, _ = _read_json_object(input_path, label=input_path.name)
    direct_refs = _collect_record_refs(input_payload)
    matches = [
        ref
        for ref in direct_refs
        if ref.get("sha256") == source_sha256
    ]
    if not matches:
        nested_refs: dict[tuple[str, str], dict[str, object]] = {}
        for direct_ref in direct_refs[:64]:
            try:
                bound_payload, _ = _load_bound_json_ref(
                    direct_ref,
                    project_root=project_root,
                )
            except StagePanelError:
                continue
            project_id = direct_ref.get("project_id")
            if not isinstance(project_id, str) or not project_id:
                continue
            for nested_ref in _collect_project_uri_refs(
                bound_payload,
                expected_project_id=project_id,
            ):
                nested_refs[
                    (str(nested_ref["relative_path"]), str(nested_ref["sha256"]))
                ] = nested_ref
        matches = [
            ref
            for ref in nested_refs.values()
            if ref.get("sha256") == source_sha256
        ]
    if len(matches) != 1:
        raise StagePanelError(
            "source selection is not one exact input ref or one-hop project URI "
            "from a digest-verified input record"
        )
    ref = matches[0]
    payload, path = _load_bound_json_ref(ref, project_root=project_root)
    return {
        "schema": "StageSourceView@1",
        "read_only": True,
        "canonical_write_authority": False,
        "digest_verified": True,
        "ref": ref,
        "resolved_local_path": str(path),
        "payload": payload,
    }


_PREVIEW_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)


def _preview_path(
    binding: object,
    *,
    project_root: Path | None,
) -> tuple[Path, str, str]:
    if not isinstance(binding, dict) or not isinstance(binding.get("ref"), dict):
        raise StagePanelError("no unambiguous preview artifact is bound")
    ref = binding["ref"]
    media_type = ref.get("media_type")
    expected_sha = ref.get("sha256")
    if media_type not in _PREVIEW_MEDIA_TYPES:
        raise StagePanelError(f"preview media type is unsupported: {media_type}")
    _required_sha256(expected_sha, "preview ref sha256")
    if project_root is None:
        raise StagePanelError("project root is required to open a preview artifact")
    relative_path = ref.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path:
        raise StagePanelError("preview ref has no relative_path")
    relative = Path(relative_path)
    if relative.is_absolute():
        raise StagePanelError("preview ref must be project-root-relative")
    root = project_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise StagePanelError("preview ref escapes the explicit project root") from exc
    actual_sha, _ = _sha256_file(candidate)
    if actual_sha != expected_sha:
        raise StagePanelError("preview artifact digest does not match its binding")
    return candidate, str(media_type), str(expected_sha)


def compile_stage_preview(
    pack: dict[str, object],
    *,
    project_root: Path | None,
) -> dict[str, object]:
    binding = pack.get("selected_preview_binding")
    try:
        path, media_type, sha256 = _preview_path(
            binding,
            project_root=project_root,
        )
    except StagePanelError as exc:
        return {
            "status": "missing" if binding is None else "blocked",
            "message": str(exc),
            "source_refs": [],
        }
    return {
        "status": "verified",
        "message": "Pack-bound preview digest verified.",
        "media_type": media_type,
        "sha256": sha256,
        "resolved_local_path": str(path),
        "source_refs": [binding.get("ref")],
    }


def load_stage_preview(
    input_path: Path,
    *,
    project_root: Path | None,
) -> tuple[bytes, str]:
    input_payload, _ = _read_json_object(input_path, label=input_path.name)
    adapted = adapt_panel_input(input_payload)
    path, media_type, _ = _preview_path(
        adapted["pack"].get("selected_preview_binding"),
        project_root=project_root,
    )
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise StagePanelError(f"cannot read preview artifact: {exc}") from exc
    return body, media_type


def _json_key(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _object_value(value: object, key: str) -> object:
    return value.get(key) if isinstance(value, dict) else None


def _check(
    check_id: str,
    status: str,
    message: str,
    *,
    expected: object = None,
    actual: object = None,
) -> dict[str, object]:
    return {
        "check_id": check_id,
        "status": status,
        "message": message,
        "expected": expected,
        "actual": actual,
    }


def _inspection_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise StagePanelError(
        "three_dm_inspector must return a mapping or an object with to_dict()"
    )


def _direct_three_dm_inspection(path: Path) -> tuple[str, object]:
    """Call the optional headless framework inspector when it is available."""

    module_name = "archflow.adapters.three_dm_inspector"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return "dependency_missing", None
        return "dependency_missing", {
            "error": f"three_dm_inspector dependency missing: {exc.name}"
        }
    except Exception as exc:  # fail closed at the optional integration boundary
        return "read_failed", {"error": f"cannot load three_dm_inspector: {exc}"}
    inspect = getattr(module, "inspect_three_dm", None)
    if not callable(inspect):
        return "interface_missing", {
            "error": "three_dm_inspector has no inspect_three_dm(path) callable"
        }
    try:
        return "direct", _inspection_payload(inspect(path))
    except Exception as exc:  # the failure is rendered; it is never retried silently
        to_dict = getattr(exc, "to_dict", None)
        detail = to_dict() if callable(to_dict) else None
        if isinstance(detail, dict):
            code = detail.get("code")
            mode = (
                "dependency_missing"
                if code == "three_dm_inspection.dependency_unavailable"
                else "read_failed"
            )
            return mode, detail
        return "read_failed", {"error": f"headless 3DM inspection failed: {exc}"}


def _injected_three_dm_inspection(path: Path | None) -> object | None:
    if path is None:
        return None
    payload, _ = _read_json_object(path.resolve(), label="3DM inspection JSON")
    return payload


def _artifact_expected_sha(pack: dict[str, object]) -> object:
    declared = pack.get("artifact_sha256")
    if declared is not None:
        return declared
    artifact_ref = pack.get("artifact_ref")
    if isinstance(artifact_ref, dict):
        return artifact_ref.get("sha256")
    return None


def _artifact_expected_size(pack: dict[str, object]) -> object:
    declared = pack.get("artifact_size_bytes")
    if declared is not None:
        return declared
    artifact_ref = pack.get("artifact_ref")
    if isinstance(artifact_ref, dict):
        return artifact_ref.get("size_bytes")
    return None


def _normalized_unit(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    if not isinstance(value, str) or not value.strip():
        return None
    token = value.strip().lower().replace(" ", "_")
    aliases = {
        "meters": "meter",
        "metres": "meter",
        "metre": "meter",
        "inches": "inch",
        "feet": "foot",
        "millimeters": "millimeter",
        "millimetres": "millimeter",
        "centimeters": "centimeter",
        "centimetres": "centimeter",
    }
    return aliases.get(token, token)


def _check_status(checks: list[dict[str, object]], check_id: str) -> str | None:
    for item in checks:
        if item.get("check_id") == check_id:
            return str(item.get("status"))
    return None


def compile_three_dm_alignment(
    pack: dict[str, object],
    *,
    panel: dict[str, object],
    artifact_root: Path | None,
    inspection_json: Path | None,
    injected_inspection: object = None,
    snapshot_binding_valid: bool | None = None,
) -> dict[str, object]:
    """Read and inspect the exact CAD binding; never select an older model."""

    checks: list[dict[str, object]] = []
    artifact_path: Path | None = None
    actual_sha: str | None = None
    actual_size: int | None = None
    binding = pack.get("selected_cad_binding")
    if isinstance(binding, dict):
        binding_program = binding.get("program_digest")
        pack_program = pack.get("program_digest")
        matched = (
            isinstance(binding_program, str)
            and binding_program == pack_program
        )
        checks.append(
            _check(
                "cad_binding_program_digest",
                "pass" if matched else "fail",
                "CAD binding is bound to the Stage program digest"
                if matched
                else "CAD binding program digest is missing or mismatched",
                expected=pack_program,
                actual=binding_program,
            )
        )
    else:
        checks.append(
            _check(
                "cad_binding_program_digest",
                "fail",
                "no unambiguous CAD model binding is available",
            )
        )

    try:
        artifact_path = _resolve_artifact(
            pack.get("artifact_ref"), artifact_root=artifact_root
        )
        checks.append(
            _check(
                "artifact_ref_resolved",
                "pass",
                "CAD artifact_ref resolved inside the explicit project root",
                actual=str(artifact_path),
            )
        )
    except StagePanelError as exc:
        checks.append(_check("artifact_ref_resolved", "fail", str(exc)))

    if artifact_path is not None:
        try:
            actual_sha, actual_size = _sha256_file(artifact_path)
            checks.append(
                _check(
                    "artifact_read",
                    "pass",
                    "3DM bytes read without launching Rhino",
                    actual={"sha256": actual_sha, "size_bytes": actual_size},
                )
            )
        except StagePanelError as exc:
            checks.append(_check("artifact_read", "fail", str(exc)))
    else:
        checks.append(
            _check("artifact_read", "fail", "3DM bytes were not available to read")
        )

    expected_sha = _artifact_expected_sha(pack)
    sha_match = (
        isinstance(expected_sha, str)
        and actual_sha is not None
        and expected_sha == actual_sha
    )
    checks.append(
        _check(
            "artifact_sha256",
            "pass" if sha_match else "fail",
            "file digest matches the StageEvidencePack artifact_ref"
            if sha_match
            else "file digest is unavailable or mismatches the StageEvidencePack",
            expected=expected_sha,
            actual=actual_sha,
        )
    )

    stage_pack_status = pack.get("stage_pack_binding_status")
    if stage_pack_status == "missing":
        checks.append(
            _check(
                "snapshot_pack_digest",
                "fail",
                "formal StageEvidencePack is missing; declaration checks cannot close the Stage",
                actual=stage_pack_status,
            )
        )
    elif stage_pack_status == "inventory_present_unexpanded":
        checks.append(
            _check(
                "snapshot_pack_digest",
                "unknown",
                "StageEvidencePack inventory exists but its exact payload is not expanded",
                actual=stage_pack_status,
            )
        )
    elif snapshot_binding_valid is None:
        checks.append(
            _check(
                "snapshot_pack_digest",
                "unknown",
                "direct compact pack input has no project-generated panel snapshot binding",
            )
        )
    else:
        checks.append(
            _check(
                "snapshot_pack_digest",
                "pass" if snapshot_binding_valid else "fail",
                "panel snapshot digest matches the embedded StageEvidencePack"
                if snapshot_binding_valid
                else "panel snapshot digest does not match the embedded StageEvidencePack",
            )
        )

    mode = "unavailable"
    raw_inspection: object = None
    if artifact_path is not None and actual_sha is not None:
        mode, raw_inspection = _direct_three_dm_inspection(artifact_path)
    if mode == "dependency_missing":
        fallback = injected_inspection
        if fallback is None:
            try:
                fallback = _injected_three_dm_inspection(inspection_json)
            except StagePanelError as exc:
                raw_inspection = {"error": str(exc)}
                mode = "read_failed"
        if fallback is not None and mode == "dependency_missing":
            raw_inspection = fallback
            mode = "injected"

    inspection: dict[str, object] | None = None
    if mode in {"direct", "injected"}:
        try:
            inspection = adapt_three_dm_inspection(raw_inspection)
        except (StagePanelError, TypeError, ValueError) as exc:
            checks.append(_check("three_dm_inspection", "fail", str(exc)))
        else:
            direct = mode == "direct"
            checks.append(
                _check(
                    "three_dm_inspection",
                    "pass" if direct else "fail",
                    "direct headless three_dm_inspector result loaded"
                    if direct
                    else (
                        "injected inspection is displayed, but the direct headless "
                        "dependency is missing"
                    ),
                    actual=mode,
                )
            )
    else:
        if isinstance(raw_inspection, dict):
            detail = raw_inspection.get("message") or raw_inspection.get("error")
            code = raw_inspection.get("code")
        else:
            detail = None
            code = None
        checks.append(
            _check(
                "three_dm_inspection",
                "fail",
                str(detail or "direct headless 3DM inspection is unavailable"),
                actual=code or mode,
            )
        )

    if inspection is not None:
        inspected_sha = inspection.get("file_sha256")
        inspected_size = inspection.get("file_size_bytes")
        inspected_headless = inspection.get("headless") is True
        checks.extend(
            [
                _check(
                    "inspection_file_sha256",
                    "pass" if inspected_sha == actual_sha else "fail",
                    "inspection is bound to the same file bytes"
                    if inspected_sha == actual_sha
                    else "inspection digest does not match the file bytes",
                    expected=actual_sha,
                    actual=inspected_sha,
                ),
                _check(
                    "inspection_file_bytes",
                    "pass" if inspected_size == actual_size else "fail",
                    "inspection byte count matches the file snapshot"
                    if inspected_size == actual_size
                    else "inspection byte count does not match the file snapshot",
                    expected=actual_size,
                    actual=inspected_size,
                ),
                _check(
                    "inspection_headless",
                    "pass" if inspected_headless else "fail",
                    "inspection is read-only and did not start Rhino"
                    if inspected_headless
                    else "inspection does not satisfy the headless boundary",
                ),
            ]
        )
        expected_unit = _normalized_unit(panel.get("program_length_unit"))
        inspected_unit = _normalized_unit(inspection.get("model_units"))
        if expected_unit is None:
            checks.append(
                _check(
                    "model_units",
                    "unknown",
                    "program length unit is not present in the panel snapshot",
                    actual=inspection.get("model_units"),
                )
            )
        else:
            unit_match = expected_unit == inspected_unit
            checks.append(
                _check(
                    "model_units",
                    "pass" if unit_match else "fail",
                    "3DM units match the geometry program"
                    if unit_match
                    else "3DM units do not match the geometry program",
                    expected=expected_unit,
                    actual=inspected_unit,
                )
            )
        for diagnostic in inspection.get("diagnostics", []):
            if isinstance(diagnostic, dict):
                checks.append(
                    _check(
                        f"inspection.{diagnostic.get('path')}",
                        "fail",
                        str(diagnostic.get("message")),
                    )
                )

    geometry_exactness = panel.get("geometry_exactness")
    if isinstance(geometry_exactness, dict):
        strict_status = str(geometry_exactness.get("status") or "").lower()
        strict_pass = strict_status in {"pass", "passed", "verified", "equivalent", "exact"}
        checks.append(
            _check(
                "strict_geometry",
                "pass" if strict_pass else "fail",
                "strict geometry matches within its declared tolerance"
                if strict_pass
                else "strict geometry diverges or has no passing receipt",
                expected={
                    "status": "verified/equivalent",
                    "tolerance": geometry_exactness.get("strict_tolerance"),
                },
                actual={
                    "status": geometry_exactness.get("status"),
                    "max_abs_deviation": geometry_exactness.get(
                        "max_abs_deviation"
                    ),
                    "mismatches": geometry_exactness.get("mismatches"),
                },
            )
        )
    else:
        checks.append(
            _check(
                "strict_geometry",
                "unknown",
                "no strict geometry verification is expanded",
            )
        )

    critical_ids = {
        "cad_binding_program_digest",
        "artifact_ref_resolved",
        "artifact_read",
        "artifact_sha256",
        "snapshot_pack_digest",
        "three_dm_inspection",
        "inspection_file_sha256",
        "inspection_file_bytes",
        "inspection_headless",
        "model_units",
        "strict_geometry",
    }
    artifact_verified = all(
        _check_status(checks, check_id) == "pass" for check_id in critical_ids
    )

    acceptance_checks: list[dict[str, object]] = []
    acceptance = panel.get("stage_acceptance")
    if not isinstance(acceptance, dict):
        acceptance_checks.append(
            _check(
                "stage_acceptance",
                "unknown",
                "no independent persisted review/promotion acceptance is expanded",
            )
        )
        acceptance_status = "unavailable"
    else:
        accepted = acceptance.get("accepted")
        disposition = str(acceptance.get("disposition") or "").upper()
        acceptance_status = (
            "accepted"
            if accepted is True
            else "hold"
            if accepted is False and disposition == "HOLD"
            else "rejected"
            if accepted is False
            else "invalid"
        )
        acceptance_checks.append(
            _check(
                "stage_acceptance",
                "pass" if accepted is True else "fail",
                "independent persisted authority accepted the Stage"
                if accepted is True
                else "Stage is not explicitly accepted",
                expected=True,
                actual=accepted,
            )
        )
        authority_ref = acceptance.get("authority_ref")
        acceptance_checks.append(
            _check(
                "acceptance_authority_ref",
                "pass" if isinstance(authority_ref, dict) else "fail",
                "acceptance cites a persisted authority record"
                if isinstance(authority_ref, dict)
                else "acceptance authority ref is missing",
                actual=authority_ref,
            )
        )
        for check_id, field, expected in (
            ("acceptance_program_digest", "program_digest", pack.get("program_digest")),
            ("acceptance_artifact_ref", "artifact_ref", pack.get("artifact_ref")),
            ("acceptance_artifact_sha256", "artifact_sha256", actual_sha),
        ):
            actual = acceptance.get(field)
            matched = actual is not None and _json_key(actual) == _json_key(expected)
            acceptance_checks.append(
                _check(
                    check_id,
                    "pass" if matched else "fail",
                    f"acceptance {field} aligns with the inspected Stage"
                    if matched
                    else f"acceptance {field} is missing or mismatched",
                    expected=expected,
                    actual=actual,
                )
            )
    stage_accepted = artifact_verified and bool(acceptance_checks) and all(
        item["status"] == "pass" for item in acceptance_checks
    )
    return {
        "verified_stage_artifact": artifact_verified,
        "stage_accepted": stage_accepted,
        "stage_acceptance_status": acceptance_status,
        "inspection_mode": mode,
        "resolved_local_path": str(artifact_path) if artifact_path else None,
        "actual_file_sha256": actual_sha,
        "actual_file_size_bytes": actual_size,
        "inspection": inspection,
        "checks": checks,
        "acceptance_checks": acceptance_checks,
    }


def _expand_pantheon_stage_reviews(
    panel: dict[str, object],
    pack: dict[str, object],
    *,
    project_root: Path | None,
) -> None:
    """Expand only the declaration decisions explicitly bound by Stage reviews."""

    identity = pack.get("identity")
    if not isinstance(identity, dict):
        return
    project_id = identity.get("project_id")
    run_id = identity.get("run_id")
    if not isinstance(project_id, str) or not isinstance(run_id, str):
        return

    decisions: list[dict[str, object]] = []
    dependencies: list[dict[str, object]] = []
    evidence_sources: list[object] = []
    lineage_decisions: list[dict[str, object]] = []
    adoption_facts_cache: dict[
        tuple[str, str], tuple[bool, frozenset[str]]
    ] = {}
    expanded_review_count = 0

    def adoption_decision_refs(
        source_ref: object,
        *,
        diagnostic_path: str,
    ) -> tuple[bool, frozenset[str]]:
        if not isinstance(source_ref, dict):
            return False, frozenset()
        relative_path = source_ref.get("relative_path")
        sha256 = source_ref.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(sha256, str):
            return False, frozenset()
        cache_key = (relative_path, sha256)
        if cache_key in adoption_facts_cache:
            return adoption_facts_cache[cache_key]
        try:
            source_payload, _ = _load_bound_json_ref(
                source_ref,
                project_root=project_root,
            )
        except StagePanelError as exc:
            adoption_facts_cache[cache_key] = False, frozenset()
            panel.setdefault("diagnostics", []).append(
                {
                    "severity": "error",
                    "path": diagnostic_path,
                    "message": str(exc),
                }
            )
            return adoption_facts_cache[cache_key]
        if source_payload.get("schema") != "PrecedentAdoption@1":
            adoption_facts_cache[cache_key] = False, frozenset()
            return adoption_facts_cache[cache_key]
        facts = source_payload.get("facts")
        decision_refs: set[str] = set()
        if isinstance(facts, list):
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                refs = fact.get("decision_refs")
                if not isinstance(refs, list):
                    continue
                decision_refs.update(item for item in refs if isinstance(item, str))
        else:
            panel.setdefault("diagnostics", []).append(
                {
                    "severity": "error",
                    "path": diagnostic_path,
                    "message": (
                        "PrecedentAdoption@1 facts are not an array; decision "
                        "alignment cannot be established"
                    ),
                }
            )
        adoption_facts_cache[cache_key] = True, frozenset(decision_refs)
        return adoption_facts_cache[cache_key]

    for row_index, raw_row in enumerate(panel.get("stages", [])):
        if not isinstance(raw_row, dict):
            continue
        review_ref = raw_row.get("review_ref")
        if not isinstance(review_ref, dict):
            continue
        try:
            review, _ = _load_bound_json_ref(
                review_ref,
                project_root=project_root,
            )
        except StagePanelError as exc:
            panel.setdefault("diagnostics", []).append(
                {
                    "severity": "error",
                    "path": f"stages[{row_index}].review_ref",
                    "message": str(exc),
                }
            )
            continue
        stage_index = raw_row.get("stage")
        if (
            review.get("schema") != "P069CandidateStageReview@1"
            or review.get("project_id") != project_id
            or review.get("run_id") != run_id
            or review.get("stage") != stage_index
        ):
            panel.setdefault("diagnostics", []).append(
                {
                    "severity": "error",
                    "path": f"stages[{row_index}].review_ref.identity",
                    "message": "Stage review schema, identity, or authority drifted",
                }
            )
            continue
        contract = review.get("contract")
        if (
            not isinstance(contract, dict)
            or contract.get("schema") != "StageDeclarationContract@1"
            or not isinstance(contract.get("fields"), list)
        ):
            panel.setdefault("diagnostics", []).append(
                {
                    "severity": "error",
                    "path": f"stages[{row_index}].review_ref.contract",
                    "message": "Stage declaration contract schema or fields drifted",
                }
            )
            continue
        realized = review.get("realized_declaration_values")
        if not isinstance(realized, dict):
            panel.setdefault("diagnostics", []).append(
                {
                    "severity": "error",
                    "path": (
                        f"stages[{row_index}].review_ref."
                        "realized_declaration_values"
                    ),
                    "message": "realized declaration values are not an object",
                }
            )
            continue

        expanded_review_count += 1
        review_sources = _normalize_source_refs(
            [review.get("program_ref"), review.get("evidence_proposal_ref")],
            expected_project_id=project_id,
        )
        hold_reasons = [
            str(item)
            for item in review.get("hold_reasons", [])
            if isinstance(item, str)
        ]
        adjudication_reason = (
            "HOLD: " + "; ".join(hold_reasons)
            if hold_reasons
            else "No independent Stage approval or rejection reason is recorded."
        )
        predecessor_digest = review.get("predecessor_program_digest")
        for field_index, raw_field in enumerate(contract["fields"]):
            if not isinstance(raw_field, dict):
                panel.setdefault("diagnostics", []).append(
                    {
                        "severity": "error",
                        "path": (
                            f"stages[{row_index}].review_ref.contract.fields"
                            f"[{field_index}]"
                        ),
                        "message": "declaration field is not an object",
                    }
                )
                continue
            field_id = raw_field.get("field_id")
            if not isinstance(field_id, str) or not field_id:
                panel.setdefault("diagnostics", []).append(
                    {
                        "severity": "error",
                        "path": (
                            f"stages[{row_index}].review_ref.contract.fields"
                            f"[{field_index}].field_id"
                        ),
                        "message": "declaration field id is missing",
                    }
                )
                continue
            field_sources = _normalize_source_refs(
                raw_field.get("source_refs"),
                expected_project_id=project_id,
            )
            diagnostic_path = (
                f"stages[{row_index}].review_ref.contract.fields"
                f"[{field_index}].source_refs"
            )
            expected_adoption_decision_ref = f"declaration:{field_id}"
            adoption_reference_present = False
            adoption_reference_aligned = False
            for item in field_sources:
                reference_present, exact_decision_refs = adoption_decision_refs(
                    item,
                    diagnostic_path=diagnostic_path,
                )
                if not reference_present:
                    continue
                adoption_reference_present = True
                if expected_adoption_decision_ref in exact_decision_refs:
                    adoption_reference_aligned = True
            adoption_alignment = (
                "aligned"
                if adoption_reference_aligned
                else "mismatched"
                if adoption_reference_present
                else "none"
            )
            uncertainty_present = any(
                isinstance(item, str) and item.startswith("uncertainty:")
                for item in field_sources
            )
            uncertainty_only = bool(field_sources) and all(
                isinstance(item, str) and item.startswith("uncertainty:")
                for item in field_sources
            )
            context_only = bool(field_sources) and all(
                isinstance(item, str) and item.startswith("context:")
                for item in field_sources
            )
            source_less = not field_sources
            source_refs = [*field_sources, *review_sources, review_ref]
            evidence_sources.extend(field_sources)
            lineage_decisions.append(
                {
                    "decision_id": f"stage-{stage_index}:{field_id}",
                    "title": f"Stage {stage_index} · {field_id}",
                    "stage": stage_index,
                    "status": (
                        "mismatched"
                        if adoption_alignment == "mismatched"
                        else "observed_aligned"
                        if adoption_alignment == "aligned"
                        else "observed"
                    ),
                    "adoption_reference_present": adoption_reference_present,
                    "adoption_alignment": adoption_alignment,
                    "expected_adoption_decision_ref": (
                        expected_adoption_decision_ref
                    ),
                    "uncertainty_present": uncertainty_present,
                    "uncertainty_only": uncertainty_only,
                    "context_only": context_only,
                    "source_less": source_less,
                    "observed_source_count": len(field_sources),
                    "source_refs": field_sources,
                }
            )
            candidate_status = (
                "pass" if review.get("checks_status") == "pass" else "fail"
            )
            candidate = {
                "candidate_id": (
                    f"stage-{stage_index}:{field_id}:realized-declaration"
                ),
                "label": "Realized declaration",
                "status": candidate_status,
                "disposition": review.get("disposition"),
                "implementation": {
                    "value": realized.get(field_id),
                    "kind": raw_field.get("kind"),
                    "unit": raw_field.get("unit"),
                    "minimum": raw_field.get("minimum"),
                    "maximum": raw_field.get("maximum"),
                    "geometry_check": raw_field.get("geometry_check"),
                    "verification_scope": "local P069 declaration check only",
                },
                "reason": raw_field.get("statement")
                or "No field-level reason is recorded.",
                "source_refs": source_refs,
            }
            decisions.append(
                {
                    "decision_id": f"stage-{stage_index}:{field_id}",
                    "title": f"Stage {stage_index} · {field_id}",
                    "status": review.get("disposition"),
                    "exact_predecessor": predecessor_digest,
                    "candidates": [candidate],
                    "adjudication": {
                        "approved": False,
                        "selected_candidate": None,
                        "disposition": review.get("disposition"),
                        "adjudicator": None,
                        "authority_state": (
                            "local candidate review; no Stage acceptance authority"
                        ),
                        "user_ratified": None,
                        "reason": adjudication_reason,
                        "source_ref": review_ref,
                    },
                    "constraints": {
                        "class": "stage_declaration_contract",
                        "hard_soft_classification": "not_declared",
                    },
                    "dependencies": [predecessor_digest]
                    if isinstance(predecessor_digest, str)
                    else [],
                    "source_refs": source_refs,
                }
            )

        dependencies.append(
            {
                "title": f"Stage {stage_index} exact predecessor",
                "status": (
                    "pass"
                    if stage_index == 0
                    or review.get("exact_predecessor_compiled") is True
                    else "fail"
                ),
                "description": (
                    "root Stage has no predecessor"
                    if stage_index == 0
                    else f"predecessor program digest={predecessor_digest}"
                ),
                "program_digest": review.get("program_digest"),
                "predecessor_program_digest": predecessor_digest,
                "source_refs": [review_ref],
            }
        )

    panel["decisions"] = [*panel.get("decisions", []), *decisions]
    panel["dependencies"] = [*panel.get("dependencies", []), *dependencies]
    web_rag = panel.get("web_rag")
    if isinstance(web_rag, dict):
        web_rag["source_refs"] = [
            *web_rag.get("source_refs", []),
            *evidence_sources,
        ]
        decision_count = len(lineage_decisions)
        web_rag["observed_declaration_lineage"] = {
            "schema": "ObservedDeclarationLineageView@1",
            "status": "OBSERVED",
            "decision_count": decision_count,
            "adoption_reference_count": sum(
                item["adoption_reference_present"] is True
                for item in lineage_decisions
            ),
            "decision_aligned_adoption_count": sum(
                item["adoption_alignment"] == "aligned"
                for item in lineage_decisions
            ),
            "misbound_adoption_count": sum(
                item["adoption_alignment"] == "mismatched"
                for item in lineage_decisions
            ),
            "uncertainty_present_count": sum(
                item["uncertainty_present"] is True for item in lineage_decisions
            ),
            "uncertainty_only_count": sum(
                item["uncertainty_only"] is True for item in lineage_decisions
            ),
            "context_only_count": sum(
                item["context_only"] is True for item in lineage_decisions
            ),
            "source_less_count": sum(
                item["source_less"] is True for item in lineage_decisions
            ),
            "decisions": lineage_decisions,
            "evidence_sufficiency_claim": False,
            "stage_pass_claim": False,
            "note": (
                "Adoption reference presence and exact decision alignment are "
                "lineage inventory only; neither count establishes branch-conditioned "
                "evidence sufficiency or Stage pass."
            ),
        }
        formal = panel.get("formal_closure")
        record_counts = (
            formal.get("record_counts", {}) if isinstance(formal, dict) else {}
        )
        if not isinstance(record_counts, dict):
            record_counts = {}
        branch_scope_count = record_counts.get("branch_scope", 0)
        evidence_sufficiency_count = record_counts.get("evidence_sufficiency", 0)
        if not isinstance(branch_scope_count, int):
            branch_scope_count = 0
        if not isinstance(evidence_sufficiency_count, int):
            evidence_sufficiency_count = 0
        formal_missing = branch_scope_count == 0 or evidence_sufficiency_count == 0
        reason_codes: list[str] = []
        if branch_scope_count == 0:
            reason_codes.append("formal.branch_scope_missing")
        if evidence_sufficiency_count == 0:
            reason_codes.append("formal.evidence_sufficiency_missing")
        web_rag["formal_branch_conditioned_rag"] = {
            "schema": "FormalBranchConditionedRagView@1",
            "status": "UNAVAILABLE" if formal_missing else "UNVERIFIED",
            "compilation_status": (
                "NOT_COMPILED"
                if formal_missing
                else "RECORDS_PRESENT_NOT_VALIDATED"
            ),
            "branch_scope_record_count": branch_scope_count,
            "evidence_sufficiency_record_count": evidence_sufficiency_count,
            "reason_codes": reason_codes,
            "coverage": None,
            "evidence_sufficiency_claim": False,
            "stage_pass_claim": False,
            "stage_acceptance_authority": False,
            "note": (
                "Formal P078 branch scope and P079 evidence sufficiency must be "
                "compiled and validated before coverage or sufficiency exists."
            ),
        }
    if expanded_review_count:
        panel.setdefault("diagnostics", []).extend(
            [
                {
                    "severity": "advisory",
                    "path": "stages[].review_ref.adjudicator",
                    "message": (
                        "P069 candidate Stage reviews expose declaration candidates "
                        "and sources but do not name an independent adjudicator."
                    ),
                },
                {
                    "severity": "advisory",
                    "path": "stages[].review_ref.contract.fields",
                    "message": (
                        "Stage declaration contracts do not label fields HARD or SOFT; "
                        "the panel leaves that classification undeclared."
                    ),
                },
            ]
        )


def _expand_pantheon_detail_plan(
    panel: dict[str, object],
    pack: dict[str, object],
    *,
    project_root: Path | None,
) -> None:
    """Dereference exactly the snapshot's plan_ref for decision/source drill-down."""

    plan_ref = panel.get("detail_plan_ref")
    if not isinstance(plan_ref, dict):
        return
    try:
        plan, _ = _load_bound_json_ref(plan_ref, project_root=project_root)
    except StagePanelError as exc:
        panel.setdefault("diagnostics", []).append(
            {
                "severity": "error",
                "path": "detail_candidate.plan_ref",
                "message": str(exc),
            }
        )
        return
    if plan.get("schema") != "P069DetailEnrichmentPlan@1":
        panel.setdefault("diagnostics", []).append(
            {
                "severity": "error",
                "path": "detail_candidate.plan_ref.schema",
                "message": "detail plan schema is unsupported",
            }
        )
        return
    identity = pack.get("identity", {})
    if (
        plan.get("project_id") != identity.get("project_id")
        or plan.get("run_id") != identity.get("run_id")
    ):
        panel.setdefault("diagnostics", []).append(
            {
                "severity": "error",
                "path": "detail_candidate.plan_ref.identity",
                "message": "detail plan identity or authority boundary drifted",
            }
        )
        return
    raw_decisions = plan.get("detail_decisions")
    if not isinstance(raw_decisions, list):
        panel.setdefault("diagnostics", []).append(
            {
                "severity": "error",
                "path": "detail_decisions",
                "message": "detail decisions are not an array",
            }
        )
        return
    decisions: list[dict[str, object]] = []
    hard: list[dict[str, object]] = []
    soft: list[dict[str, object]] = []
    dependencies: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []
    all_sources: set[str] = set()
    for index, value in enumerate(raw_decisions):
        if not isinstance(value, dict):
            panel.setdefault("diagnostics", []).append(
                {
                    "severity": "error",
                    "path": f"detail_decisions[{index}]",
                    "message": "decision is not an object",
                }
            )
            continue
        decision_id = value.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id:
            panel.setdefault("diagnostics", []).append(
                {
                    "severity": "error",
                    "path": f"detail_decisions[{index}].decision_id",
                    "message": "decision id is missing",
                }
            )
            continue
        source_refs = [
            item for item in value.get("source_refs", []) if isinstance(item, str)
        ]
        all_sources.update(source_refs)
        reason = value.get("reason")
        if not isinstance(reason, str) or not reason:
            reason = "No explicit decision-level approval/rejection reason recorded."
        constraint_class = str(value.get("constraint_class") or "unclassified")
        candidate = {
            "candidate_id": f"{decision_id}:proposed-implementation",
            "label": "Proposed implementation",
            "status": "not_gate_eligible"
            if value.get("gate_eligible") is False
            else "candidate",
            "disposition": plan.get("disposition"),
            "implementation": value.get("implementation"),
            "reason": reason,
            "source_refs": source_refs,
        }
        decisions.append(
            {
                "decision_id": decision_id,
                "title": decision_id.replace("-", " "),
                "status": plan.get("disposition"),
                "exact_predecessor": plan.get("predecessor_program_digest"),
                "candidates": [candidate],
                "adjudication": {
                    "approved": False,
                    "selected_candidate": None,
                    "disposition": plan.get("disposition"),
                    "adjudicator": None,
                    "authority_state": plan.get("authority_state"),
                    "user_ratified": plan.get("user_ratified"),
                    "reason": reason,
                    "source_ref": plan_ref,
                },
                "constraints": {"class": constraint_class},
                "dependencies": list(value.get("dependencies", [])),
                "source_refs": [*source_refs, plan_ref],
            }
        )
        constraint = {
            "title": decision_id,
            "status": constraint_class,
            "description": reason,
            "source_refs": [*source_refs, plan_ref],
        }
        if "hard" in constraint_class:
            hard.append(constraint)
        if "soft" in constraint_class:
            soft.append(constraint)
        if "conflict" in constraint_class:
            conflicts.append(
                {
                    "title": decision_id,
                    "status": "conflict",
                    "description": reason,
                    "source_refs": [*source_refs, plan_ref],
                }
            )
        for dependency in value.get("dependencies", []):
            dependencies.append(
                {
                    "title": str(dependency),
                    "status": "declared_unclosed",
                    "decision_id": decision_id,
                    "source_refs": [plan_ref],
                }
            )

    branch_scope = plan.get("branch_scope")
    if not isinstance(branch_scope, dict):
        branch_scope = {}
    allowlist = [
        item for item in branch_scope.get("source_allowlist", []) if isinstance(item, str)
    ]
    all_sources.update(allowlist)
    panel["decisions"] = decisions
    panel["constraints"] = {"hard": hard, "soft": soft}
    panel["dependencies"] = dependencies
    panel["conflicts"] = [*panel.get("conflicts", []), *conflicts]
    panel["web_rag"] = {
        "queries": [
            {
                "title": branch_scope.get("query_scope") or "query scope unavailable",
                "status": "scope_only_no_formal_query_coverage",
                "source_allowlist": allowlist,
                "source_refs": [plan_ref],
            }
        ],
        "coverage": {
            "covered": None,
            "total": None,
            "ratio": None,
            "ratio_origin": "unavailable_formal_sufficiency_missing",
        },
        "conflicts": conflicts,
        "gaps": panel.get("gaps", []),
        "source_refs": sorted(all_sources),
    }
    assets = plan.get("asset_candidates")
    if isinstance(assets, list):
        panel["models"] = [*panel.get("models", []), *assets]
    panel.setdefault("diagnostics", []).append(
        {
            "severity": "advisory",
            "path": "detail_decisions.adjudicator",
            "message": (
                "The plan records authority_state=agent-proposed and user_ratified=false; "
                "it does not name an adjudicator."
            ),
        }
    )


def _expand_pantheon_execution_receipt(
    panel: dict[str, object],
    pack: dict[str, object],
    *,
    project_root: Path | None,
) -> None:
    """Expose strict geometry separately from current-file identity checks."""

    execution_ref = panel.get("model_execution_ref")
    if not isinstance(execution_ref, dict):
        return
    try:
        receipt, _ = _load_bound_json_ref(
            execution_ref,
            project_root=project_root,
        )
    except StagePanelError as exc:
        panel.setdefault("diagnostics", []).append(
            {
                "severity": "error",
                "path": "model.execution_ref",
                "message": str(exc),
            }
        )
        return
    identity = pack.get("identity", {})
    if (
        receipt.get("schema") != "P069CandidateCadExecutionReceipt@1"
        or receipt.get("project_id") != identity.get("project_id")
        or receipt.get("run_id") != identity.get("run_id")
    ):
        panel.setdefault("diagnostics", []).append(
            {
                "severity": "error",
                "path": "model.execution_ref.identity",
                "message": "CAD execution receipt schema, identity, or authority drifted",
            }
        )
        return
    receipt_verification = receipt.get("verification")
    if not isinstance(receipt_verification, dict):
        panel.setdefault("diagnostics", []).append(
            {
                "severity": "error",
                "path": "model.execution_ref.verification",
                "message": "CAD execution verification is not expanded",
            }
        )
        return
    embedded = panel.get("embedded_execution_verification")
    embedded_verified = False
    if isinstance(embedded, dict):
        if (
            embedded.get("schema") != "P069CadExecutionVerificationSummary@1"
        ):
            panel.setdefault("diagnostics", []).append(
                {
                    "severity": "error",
                    "path": "model.execution_verification",
                    "message": "embedded execution verification schema or authority drifted",
                }
            )
            embedded = None
    if isinstance(embedded, dict):
        receipt_headless = receipt.get("headless_model_gate")
        embedded_headless = embedded.get("headless_model_gate")
        comparable = (
            embedded.get("candidate_execution_verified")
            == receipt.get("candidate_execution_verified"),
            embedded.get("disposition") == receipt.get("disposition"),
            _object_value(embedded.get("contract"), "status")
            == _object_value(receipt_verification.get("contract"), "status"),
            _object_value(embedded.get("semantics"), "status")
            == _object_value(receipt_verification.get("semantics"), "status"),
            _object_value(embedded.get("strict"), "status")
            == _object_value(receipt_verification.get("strict"), "status"),
            _json_key(_object_value(embedded.get("strict"), "mismatches"))
            == _json_key(
                _object_value(receipt_verification.get("strict"), "mismatches")
            ),
            embedded.get("max_abs_deviation")
            == receipt_verification.get("max_abs_deviation"),
            _object_value(embedded_headless, "passed")
            == _object_value(receipt_headless, "passed"),
            _json_key(_object_value(embedded_headless, "units"))
            == _json_key(_object_value(receipt_headless, "units")),
            _object_value(embedded_headless, "object_count")
            == _object_value(receipt_headless, "object_count"),
        )
        embedded_verified = all(comparable)
        if not embedded_verified:
            panel.setdefault("diagnostics", []).append(
                {
                    "severity": "error",
                    "path": "model.execution_verification",
                    "message": "embedded execution verification differs from its bound receipt",
                }
            )
    verification = embedded if isinstance(embedded, dict) else receipt_verification
    strict = verification.get("strict")
    contract = verification.get("contract")
    semantics = verification.get("semantics")
    if not isinstance(strict, dict):
        strict = {}
    if not isinstance(contract, dict):
        contract = {}
    if not isinstance(semantics, dict):
        semantics = {}
    mismatches = [
        item for item in strict.get("mismatches", []) if isinstance(item, dict)
    ]
    geometry = {
        "status": strict.get("status") or "unavailable",
        "strict_tolerance": strict.get("tolerance")
        if strict.get("tolerance") is not None
        else _object_value(receipt_verification.get("strict"), "tolerance"),
        "max_abs_deviation": verification.get("max_abs_deviation"),
        "mismatches": mismatches,
        "difference_classification": verification.get(
            "strict_difference_classification"
        ),
        "candidate_execution_verified": verification.get(
            "candidate_execution_verified",
            receipt.get("candidate_execution_verified"),
        ),
        "contract": contract,
        "semantics": semantics,
        "headless_model_gate": verification.get(
            "headless_model_gate", receipt.get("headless_model_gate")
        ),
        "disposition": verification.get("disposition", receipt.get("disposition")),
        "accepted_archive_created": receipt.get("accepted_archive_created"),
        "embedded_summary_verified_against_receipt": embedded_verified,
        "source_refs": [execution_ref],
    }
    panel["geometry_exactness"] = geometry
    panel["gates"] = [
        *panel.get("gates", []),
        {
            "title": "Candidate CAD execution",
            "status": "pass"
            if receipt.get("candidate_execution_verified") is True
            else "fail",
            "description": (
                "candidate_execution_verified is a model execution check, not "
                "Stage acceptance"
            ),
            "source_refs": [execution_ref],
        },
        {
            "title": "Headless unit/file gate",
            "status": "pass"
            if isinstance(receipt.get("headless_model_gate"), dict)
            and receipt["headless_model_gate"].get("passed") is True
            else "fail",
            "description": "Headless gate is independent from strict geometry.",
            "source_refs": [execution_ref],
        },
        {
            "title": "Strict geometry",
            "status": strict.get("status") or "unavailable",
            "description": (
                f"tolerance={strict.get('tolerance')}; "
                f"max_abs_deviation={verification.get('max_abs_deviation')}"
            ),
            "mismatches": mismatches,
            "source_refs": [execution_ref],
        },
    ]
    for mismatch in mismatches:
        gap = _pantheon_gap(
            "geometry.strict."
            + str(mismatch.get("object_id") or "object")
            + "."
            + str(mismatch.get("corner") or "bound")
            + ".axis-"
            + str(mismatch.get("axis")),
            (
                f"strict geometry {mismatch.get('code')}: object="
                f"{mismatch.get('object_id')}, corner={mismatch.get('corner')}, "
                f"axis={mismatch.get('axis')}, deviation={mismatch.get('deviation')}"
            ),
            kind="conflict",
        )
        gap["source_refs"] = [execution_ref]
        panel.setdefault("gaps", []).append(gap)
        panel.setdefault("conflicts", []).append(gap)


def load_stage_panel_view(
    pack_path: Path,
    *,
    artifact_root: Path | None = None,
    inspection_json: Path | None = None,
) -> dict[str, object]:
    payload, raw = _read_json_object(pack_path, label=pack_path.name)
    adapted = adapt_panel_input(payload)
    pack = adapted["pack"]
    panel = adapted["panel"]
    if adapted["input_schema"] == "PantheonStageProgressSnapshot@1":
        _expand_pantheon_detail_plan(
            panel,
            pack,
            project_root=artifact_root,
        )
        _expand_pantheon_stage_reviews(
            panel,
            pack,
            project_root=artifact_root,
        )
        _expand_pantheon_execution_receipt(
            panel,
            pack,
            project_root=artifact_root,
        )
    preview = compile_stage_preview(pack, project_root=artifact_root)
    if preview["status"] == "verified" or not isinstance(panel.get("preview"), dict):
        panel["preview"] = preview
    three_dm = compile_three_dm_alignment(
        pack,
        panel=panel,
        artifact_root=artifact_root,
        inspection_json=inspection_json,
        injected_inspection=adapted["injected_three_dm_inspection"],
        snapshot_binding_valid=adapted["snapshot_binding_valid"],
    )
    if adapted["snapshot_diagnostics"]:
        panel["diagnostics"] = [
            *adapted["snapshot_diagnostics"],
            *panel.get("diagnostics", []),
        ]
    if not pack["typed_validation"]:
        panel["diagnostics"] = [
            {
                "severity": "advisory",
                "path": "StageEvidencePack@1",
                "message": (
                    "canonical StageEvidencePack parser is not present in this "
                    "worktree; strict exact-key fallback validation is active"
                ),
            },
            *panel.get("diagnostics", []),
        ]
    acceptance = panel.get("stage_acceptance")
    disposition = (
        str(acceptance.get("disposition") or "").upper()
        if isinstance(acceptance, dict)
        else ""
    )
    model_alignment = panel.get("model_alignment")
    model_status = (
        str(model_alignment.get("status") or "").upper()
        if isinstance(model_alignment, dict)
        else ""
    )
    if model_status == "BLOCKED":
        reason_codes = (
            model_alignment.get("reason_codes", [])
            if isinstance(model_alignment, dict)
            else []
        )
        verification = {
            "status": "blocked",
            "label": "CANDIDATE HOLD / BLOCKED",
            "message": ", ".join(str(item) for item in reason_codes)
            or "Candidate disposition is HOLD.",
        }
    elif disposition == "HOLD":
        verification = {
            "status": "hold",
            "label": "CANDIDATE HOLD / FORMAL CLOSURE OPEN",
            "message": (
                "Model alignment is current, but formal branch scope, sufficiency, "
                "convergence, or StageEvidencePack closure is not established."
            ),
        }
    elif three_dm["stage_accepted"]:
        verification = {
            "status": "accepted",
            "label": "STAGE ACCEPTED",
            "message": "Artifact alignment and independent acceptance both verify.",
        }
    elif three_dm["verified_stage_artifact"]:
        verification = {
            "status": "aligned",
            "label": "EVIDENCE ALIGNED",
            "message": (
                "3DM evidence aligns, but this view does not claim Stage acceptance."
            ),
        }
    else:
        verification = {
            "status": "blocked",
            "label": "NOT VERIFIED",
            "message": "One or more Stage/3DM evidence boundaries are open or failed.",
        }
    return {
        "schema": "StagePanelView@1",
        "pack_fingerprint": hashlib.sha256(raw).hexdigest()[:16],
        "pack_source": str(pack_path),
        "provisional_adapter": True,
        "supported_input_schemas": sorted(
            _STAGE_PACK_SCHEMAS | _PANEL_SNAPSHOT_SCHEMAS
        ),
        "input_schema": adapted["input_schema"],
        "pack": pack,
        "panel": panel,
        "three_dm": three_dm,
        "verification": verification,
        "source_catalog": _collect_record_refs(payload),
    }


def _short(digest: str | None) -> str:
    return (digest or "")[:12]


class _RecordCache:
    """mtime-keyed JSON parse cache so polling stays cheap."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[int, dict]] = {}
        self._lock = threading.Lock()

    def load(self, path: Path) -> dict:
        try:
            stat = path.stat()
        except OSError as exc:
            return {"error": str(exc)}
        key = str(path)
        with self._lock:
            hit = self._entries.get(key)
            if hit and hit[0] == stat.st_mtime_ns:
                return hit[1]
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                parsed = {"error": "record is not a JSON object"}
        except (OSError, ValueError) as exc:
            parsed = {"error": f"unreadable record: {exc}"}
        with self._lock:
            self._entries[key] = (stat.st_mtime_ns, parsed)
        return parsed


_CACHE = _RecordCache()


def fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.json")):
        try:
            stat = path.stat()
        except OSError:
            continue
        h.update(str(path.relative_to(root)).encode("utf-8", "replace"))
        h.update(f":{stat.st_mtime_ns}:{stat.st_size};".encode())
    return h.hexdigest()[:16]


def _node(label: str, kind: str, *, path: Path | None = None,
          root: Path | None = None, badge: str = "",
          meta: dict | None = None, children: list | None = None,
          flag: str = "") -> dict:
    rel = str(path.relative_to(root)) if path is not None and root else ""
    digest = ""
    if path is not None:
        m = _DIGEST_RE.search(path.name)
        digest = _short(m.group("digest")) if m else ""
    return {
        "label": label, "kind": kind, "path": rel, "digest": digest,
        "badge": badge, "flag": flag, "meta": meta or {},
        "children": children or [],
    }


def _component_tree(record: dict, prev_revisions: dict[str, int],
                    path: Path, root: Path) -> list[dict]:
    content = record.get("content") or record
    components = content.get("components") or []
    by_parent: dict[str | None, list[dict]] = {}
    for comp in components:
        if isinstance(comp, dict):
            by_parent.setdefault(comp.get("parent_component_id"), []).append(comp)

    def build(parent: str | None) -> list[dict]:
        nodes = []
        for comp in by_parent.get(parent, []):
            cid = str(comp.get("component_id"))
            revision = int(comp.get("revision") or 0)
            previous = prev_revisions.get(cid)
            flag = "changed" if previous is not None and previous != revision \
                else ("new" if previous is None and prev_revisions else "")
            nodes.append(_node(
                cid, "component", path=path, root=root,
                badge=f"{comp.get('maturity', '?')} r{revision}", flag=flag,
                meta={
                    "semantic_kind": comp.get("semantic_kind"),
                    "intent": comp.get("intent"),
                    "revision": revision,
                    "volume_ids": comp.get("volume_ids"),
                },
                children=build(cid),
            ))
        return nodes

    return build(None)


def _revisions(record: dict) -> dict[str, int]:
    content = record.get("content") or record
    out: dict[str, int] = {}
    for comp in content.get("components") or []:
        if isinstance(comp, dict) and comp.get("component_id") is not None:
            out[str(comp["component_id"])] = int(comp.get("revision") or 0)
    return out


def _summarise(record: dict) -> dict:
    content = record.get("content") if isinstance(record.get("content"), dict) else {}
    meta = {
        "schema": record.get("schema"),
        "content_schema": content.get("schema"),
        "role": record.get("role"),
        "base_version": (record.get("base") or {}).get("version"),
    }
    if "error" in record:
        meta["error"] = record["error"]
    for key in ("active_phase", "coordination_status", "status", "lifecycle",
                "option_id", "branch_id", "hard_usability_verdict",
                "selected", "label"):
        if key in content and content[key] is not None:
            meta[key] = content[key]
    obligations = content.get("obligations")
    if isinstance(obligations, list) and obligations:
        open_count = sum(1 for o in obligations
                         if isinstance(o, dict) and o.get("status") == "open")
        meta["obligations"] = f"{open_count} open / {len(obligations)} total"
    return {k: v for k, v in meta.items() if v is not None}


_ROLE_ORDER = ("provider-invocation", "component-proposal", "design-state",
               "geometry-program", "lifecycle-receipt")


def _run_tree(run_dir: Path, root: Path) -> dict:
    records_dir = run_dir / "records"
    staged: dict[str, list[tuple[int, int, Path, dict]]] = {}
    plain: dict[str, list[tuple[Path, dict]]] = {}
    for path in sorted(records_dir.glob("*.json")):
        record = _CACHE.load(path)
        match = _STAGE_RE.match(path.name)
        if match:
            try:
                mtime = path.stat().st_mtime_ns
            except OSError:
                mtime = 0
            staged.setdefault(match.group("kind"), []).append(
                (int(match.group("stage")), mtime, path, record))
        else:
            kind = _DIGEST_RE.sub("", path.name)
            plain.setdefault(kind, []).append((path, record))

    # The filename index alone does not order same-index successors, so
    # iterations are reconstructed per role by (index, write time).
    for entries in staged.values():
        entries.sort(key=lambda e: (e[0], e[1]))

    def role_rank(kind: str) -> int:
        for rank, role in enumerate(_ROLE_ORDER):
            if role in kind:
                return rank
        return len(_ROLE_ORDER)

    stage_nodes, prev_revisions = [], {}
    component_root: list[dict] = []
    depth = max((len(v) for v in staged.values()), default=0)
    for index in range(depth):
        children = []
        for kind in sorted(staged, key=role_rank):
            if index >= len(staged[kind]):
                continue
            _, _, path, record = staged[kind][index]
            children.append(_node(kind, "record", path=path, root=root,
                                  meta=_summarise(record)))
            if "component-proposal" in kind and "error" not in record:
                component_root = _component_tree(record, prev_revisions,
                                                 path, root)
                prev_revisions = _revisions(record)
        stage_nodes.append(_node(f"iteration {index} (write order)", "stage",
                                 children=children,
                                 badge=f"{len(children)} records"))

    other_nodes = [
        _node(kind, "record-group" if len(entries) > 1 else "record",
              path=entries[-1][0], root=root,
              badge=f"x{len(entries)}" if len(entries) > 1 else "",
              meta=_summarise(entries[-1][1]),
              children=[
                  _node(p.name[:44], "record", path=p, root=root,
                        meta=_summarise(r))
                  for p, r in entries[:-1]
              ] if len(entries) > 1 else [])
        for kind, entries in sorted(plain.items())
    ]

    children = []
    if component_root:
        children.append(_node("component tree (latest stage)", "section",
                              children=component_root))
    if stage_nodes:
        children.append(_node("production stages (D_v,k)", "section",
                              children=stage_nodes))
    if other_nodes:
        children.append(_node("records", "section", children=other_nodes))
    for extra in ("branches", "candidates", "reviews", "workspaces",
                  "recovery"):
        sub = run_dir / extra
        if sub.is_dir():
            entries = sorted(p for p in sub.rglob("*") if p.is_file())
            if entries:
                children.append(_node(
                    extra, "dir", badge=f"{len(entries)} files",
                    children=[_node(p.name[:44], "file", path=p, root=root)
                              for p in entries[:60]]))
    return _node(run_dir.name, "run", children=children)


def project_tree(project_dir: Path, root: Path) -> dict:
    children = []
    canonical = []
    for path in sorted((project_dir / "canonical").glob("state-v*.json")):
        match = _CANONICAL_RE.match(path.name)
        if not match:
            continue
        record = _CACHE.load(path)
        state = record.get("state") or {}
        canonical.append(_node(
            f"C_v{int(match.group('version'))}", "canonical",
            path=path, root=root, badge=str(state.get("phase") or ""),
            meta={
                "schema": record.get("schema"),
                "parent": record.get("parent"),
                "state_sha256": _short(record.get("state_sha256")),
                "authoritative_records":
                    len(state.get("authoritative_record_refs") or []),
            }))
    if canonical:
        children.append(_node("canonical chain (C_v)", "section",
                              children=canonical))

    runs_dir = project_dir / "runs"
    if runs_dir.is_dir():
        run_nodes = [_run_tree(d, root) for d in sorted(runs_dir.iterdir())
                     if d.is_dir()]
        if run_nodes:
            children.append(_node("runs", "section", children=run_nodes))

    counts = []
    for name in ("events", "objects", "input", "exports"):
        sub = project_dir / name
        if sub.is_dir():
            total = sum(1 for p in sub.rglob("*") if p.is_file())
            counts.append(f"{name}: {total}")
    return _node(project_dir.name, "project", badge=" · ".join(counts),
                 children=children)


def discover_projects(root: Path) -> list[Path]:
    if (root / "canonical").is_dir():
        return [root]
    return sorted(d for d in root.iterdir()
                  if d.is_dir() and (d / "canonical").is_dir())


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>ArchFlow design-state tree</title><style>
:root { --bg:#14161a; --panel:#1c1f26; --text:#d7dae0; --dim:#7d8590;
  --accent:#6cb6ff; --line:#2b303b; --changed:#e3b341; --new:#57ab5a; }
* { box-sizing:border-box; margin:0; }
body { background:var(--bg); color:var(--text); display:flex; height:100vh;
  font:13px/1.5 ui-monospace,Consolas,monospace; }
#tree { flex:1; overflow:auto; padding:14px 18px; }
#side { width:340px; border-left:1px solid var(--line); padding:14px;
  background:var(--panel); overflow:auto; flex-shrink:0; }
h1 { font-size:14px; margin-bottom:2px; }
#status { color:var(--dim); font-size:11px; margin-bottom:10px; }
#status.live::before { content:"● "; color:var(--new); }
select { background:var(--panel); color:var(--text); border:1px solid
  var(--line); border-radius:4px; padding:2px 6px; margin-bottom:10px; }
.panel-link { display:inline-block; color:var(--accent); margin:0 0 10px 10px;
  min-height:28px; padding:3px 8px; border:1px solid var(--line);
  border-radius:4px; text-decoration:none; }
.panel-link:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
details { padding-left:16px; }
#tree > details { padding-left:0; }
summary { cursor:pointer; white-space:nowrap; border-radius:4px;
  padding:0 4px; list-style-position:outside; }
summary:hover { background:var(--line); }
summary.sel { background:#28405c; }
.badge { color:var(--dim); font-size:11px; margin-left:8px; }
.digest { color:#4d5566; font-size:10px; margin-left:8px; }
.k-project > summary { color:#e6edf3; font-weight:600; }
.k-section > summary { color:var(--accent); }
.k-canonical > summary { color:#c297ff; }
.k-component > summary { color:#7ee787; }
.k-stage > summary { color:#ffa657; }
.f-changed > summary { outline:1px solid var(--changed); }
.f-changed > summary::after { content:" changed"; color:var(--changed);
  font-size:10px; }
.f-new > summary::after { content:" new"; color:var(--new); font-size:10px; }
#side h2 { font-size:12px; color:var(--accent); margin-bottom:8px;
  word-break:break-all; }
#side .row { margin-bottom:6px; word-break:break-all; }
#side .key { color:var(--dim); font-size:11px; }
.leaf { list-style:none; }
.leaf::-webkit-details-marker { display:none; }
@keyframes flash { from { background:#233043; } to { background:none; } }
.updated { animation:flash 0.9s; }
</style></head><body>
<div id="tree"><h1>ArchFlow design-state tree</h1>
<div id="status">connecting…</div>
<select id="proj"></select><a class="panel-link" href="/stage">Stage evidence panel</a><div id="nodes"></div></div>
<div id="side"><h2>details</h2>
<div id="detail" class="row key">click a node</div></div>
<script>
let fp = "", project = "", timer = null;
const openSet = new Set(), $ = id => document.getElementById(id);

function render(node, path) {
  const id = path + "/" + node.label;
  const d = document.createElement("details");
  d.className = "k-" + node.kind + (node.flag ? " f-" + node.flag : "");
  if (!node.children.length) d.classList.add("leafwrap");
  if (openSet.has(id) || node.kind === "project" || node.kind === "section")
    d.open = true;
  d.addEventListener("toggle", () =>
    d.open ? openSet.add(id) : openSet.delete(id));
  const s = document.createElement("summary");
  if (!node.children.length) s.className = "leaf";
  s.textContent = node.label;
  if (node.badge) {
    const b = document.createElement("span");
    b.className = "badge"; b.textContent = node.badge; s.appendChild(b);
  }
  if (node.digest) {
    const g = document.createElement("span");
    g.className = "digest"; g.textContent = node.digest; s.appendChild(g);
  }
  s.addEventListener("click", ev => {
    document.querySelectorAll("summary.sel")
      .forEach(e => e.classList.remove("sel"));
    s.classList.add("sel");
    showDetail(node);
    if (!node.children.length) ev.preventDefault();
  });
  d.appendChild(s);
  node.children.forEach(c => d.appendChild(render(c, id)));
  return d;
}

function showDetail(node) {
  const rows = [["kind", node.kind]];
  if (node.path) rows.push(["file", node.path]);
  if (node.digest) rows.push(["digest", node.digest + "…"]);
  Object.entries(node.meta).forEach(([k, v]) =>
    rows.push([k, typeof v === "object" ? JSON.stringify(v) : String(v)]));
  $("detail").innerHTML = rows.map(([k, v]) =>
    `<div class="row"><div class="key">${k}</div><div>${
      String(v).replace(/</g, "&lt;")}</div></div>`).join("");
}

async function poll() {
  try {
    const r = await fetch(`/api/tree?project=${encodeURIComponent(project)}&fp=${fp}`);
    const data = await r.json();
    $("status").className = "live";
    if (data.unchanged) {
      $("status").textContent =
        "live · unchanged · " + new Date().toLocaleTimeString();
    } else {
      fp = data.fp;
      const box = $("nodes");
      box.innerHTML = "";
      box.appendChild(render(data.tree, ""));
      box.classList.remove("updated"); void box.offsetWidth;
      box.classList.add("updated");
      $("status").textContent =
        "live · updated " + new Date().toLocaleTimeString() + " · fp " + fp;
    }
  } catch (e) {
    $("status").className = "";
    $("status").textContent = "server unreachable — retrying";
  }
  timer = setTimeout(poll, 1500);
}

async function init() {
  const projects = await (await fetch("/api/projects")).json();
  const sel = $("proj");
  projects.forEach(p => {
    const o = document.createElement("option");
    o.value = o.textContent = p; sel.appendChild(o);
  });
  project = projects[0] || "";
  sel.addEventListener("change", () => {
    project = sel.value; fp = ""; openSet.clear();
    clearTimeout(timer); poll();
  });
  poll();
}
init();
</script></body></html>"""


def make_handler(
    root: Path | None,
    *,
    stage_source: StagePackSource | None = None,
    artifact_root: Path | None = None,
    inspection_json: Path | None = None,
):
    projects = {p.name: p for p in discover_projects(root)} if root else {}
    stage_page = Path(__file__).with_name("stage_progress_panel.html")

    class Handler(BaseHTTPRequestHandler):
        def _send(
            self,
            body: bytes,
            content_type: str,
            *,
            status: int = 200,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value: object, *, status: int = 200) -> None:
            self._send(
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json",
                status=status,
            )

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            url = urlparse(self.path)
            if url.path == "/":
                if root is None and stage_source is not None:
                    self.send_response(302)
                    self.send_header("Location", "/stage")
                    self.end_headers()
                else:
                    self._send(_PAGE.encode("utf-8"), "text/html")
                return
            if url.path == "/stage":
                try:
                    body = stage_page.read_bytes()
                except OSError as exc:
                    self._json(
                        {"error": f"stage panel HTML is unavailable: {exc}"},
                        status=500,
                    )
                else:
                    self._send(body, "text/html")
                return
            if url.path == "/api/projects":
                if root is None:
                    self._json({"error": "state-tree root is not configured"}, status=404)
                    return
                nonlocal projects
                projects = {p.name: p for p in discover_projects(root)}
                self._json(sorted(projects))
                return
            if url.path == "/api/tree":
                if root is None:
                    self._json({"error": "state-tree root is not configured"}, status=404)
                    return
                query = parse_qs(url.query)
                name = (query.get("project") or [""])[0]
                target = projects.get(name)
                if target is None:
                    self._json({"error": "unknown project"}, status=404)
                    return
                fp = fingerprint(target)
                if fp == (query.get("fp") or [""])[0]:
                    payload = {"unchanged": True, "fp": fp}
                else:
                    payload = {"fp": fp, "tree": project_tree(target, root)}
                self._json(payload)
                return
            if url.path == "/api/stage/packs":
                if stage_source is None:
                    self._json(
                        {"error": "explicit Stage panel input is not configured"},
                        status=404,
                    )
                else:
                    self._json(stage_source.describe())
                return
            if url.path == "/api/stage/view":
                if stage_source is None:
                    self._json(
                        {"error": "explicit Stage panel input is not configured"},
                        status=404,
                    )
                    return
                query = parse_qs(url.query)
                name = (query.get("pack") or [""])[0]
                try:
                    path = stage_source.path_for(name)
                    payload = load_stage_panel_view(
                        path,
                        artifact_root=artifact_root,
                        inspection_json=inspection_json,
                    )
                except (StagePanelError, TypeError, ValueError) as exc:
                    self._json({"error": str(exc)}, status=422)
                else:
                    preview = payload.get("panel", {}).get("preview")
                    if isinstance(preview, dict) and preview.get("status") == "verified":
                        preview["href"] = (
                            "/api/stage/preview?pack="
                            + quote(name, safe="")
                        )
                    self._json(payload)
                return
            if url.path == "/api/stage/preview":
                if stage_source is None:
                    self._json(
                        {"error": "explicit Stage panel input is not configured"},
                        status=404,
                    )
                    return
                query = parse_qs(url.query)
                name = (query.get("pack") or [""])[0]
                try:
                    path = stage_source.path_for(name)
                    body, media_type = load_stage_preview(
                        path,
                        project_root=artifact_root,
                    )
                except (StagePanelError, TypeError, ValueError) as exc:
                    self._json({"error": str(exc)}, status=422)
                else:
                    self._send(body, media_type)
                return
            if url.path == "/api/stage/source":
                if stage_source is None:
                    self._json(
                        {"error": "explicit Stage panel input is not configured"},
                        status=404,
                    )
                    return
                query = parse_qs(url.query)
                name = (query.get("pack") or [""])[0]
                source_sha = (query.get("sha256") or [""])[0]
                try:
                    path = stage_source.path_for(name)
                    payload = load_stage_source_record(
                        path,
                        source_sha,
                        project_root=artifact_root,
                    )
                except (StagePanelError, TypeError, ValueError) as exc:
                    self._json({"error": str(exc)}, status=422)
                else:
                    self._json(payload)
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            self.send_error(405, "read-only viewer; POST is disabled")

        def log_message(self, *args) -> None:
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path)
    parser.add_argument(
        "--stage-input",
        type=Path,
        help=(
            "explicit StageEvidencePack/panel snapshot JSON, or a directory of "
            "direct JSON inputs"
        ),
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        help="explicit P036 project root used to resolve bound records and artifacts",
    )
    parser.add_argument(
        "--three-dm-inspection",
        type=Path,
        help=(
            "optional injected ThreeDmInspectionSummary JSON (any supported "
            "version) used only when the "
            "direct headless inspector dependency is unavailable"
        ),
    )
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.root is None and args.stage_input is None:
        parser.error("supply the state-tree root, --stage-input, or both")
    root = args.root.resolve() if args.root is not None else None
    if root is not None:
        if not root.is_dir():
            raise SystemExit(f"not a directory: {root}")
        if not discover_projects(root):
            raise SystemExit(f"no P036 project (canonical/) found under: {root}")
    try:
        stage_source = (
            StagePackSource(args.stage_input)
            if args.stage_input is not None
            else None
        )
    except StagePanelError as exc:
        raise SystemExit(str(exc)) from exc
    artifact_root = (
        args.artifact_root.resolve() if args.artifact_root is not None else None
    )
    if artifact_root is not None and not artifact_root.is_dir():
        raise SystemExit(f"artifact root is not a directory: {artifact_root}")
    inspection_json = (
        args.three_dm_inspection.resolve()
        if args.three_dm_inspection is not None
        else None
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        make_handler(
            root,
            stage_source=stage_source,
            artifact_root=artifact_root,
            inspection_json=inspection_json,
        ),
    )
    if root is not None:
        print(f"state tree: http://127.0.0.1:{args.port}/ ({root})")
    if stage_source is not None:
        print(
            f"stage panel: http://127.0.0.1:{args.port}/stage "
            f"({stage_source.directory})"
        )
    server.serve_forever()


if __name__ == "__main__":
    main()
