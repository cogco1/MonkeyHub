"""Build a read-only Pantheon stage-panel snapshot without starting Rhino.

The tool joins three independent surfaces:

* candidate Stage 0-3 reviews and the detail candidate;
* formal P078/P079/P080/StageEvidencePack closure records, when present; and
* a direct ``rhino3dm`` inspection of the latest retained ``.3dm`` artifact.

The result is a content-addressed, non-authoritative P036 export.  It never
promotes project state and never treats file existence as stage acceptance.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # Script execution resolves sibling tools directly.
    from _probe_paths import resolve_probe_root  # type: ignore[import-not-found]
except ModuleNotFoundError:  # Package import from tests resolves ``tools``.
    from tools._probe_paths import resolve_probe_root
from archflow.adapters.three_dm_inspector import (  # noqa: E402
    ThreeDmInspection,
    ThreeDmInspectionError,
    inspect_three_dm,
)
from archflow.project import (  # noqa: E402
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
    RunRef,
)


_RUN_ID = re.compile(r"^reconstruction-(\d+)$")
_STAGE_LABELS = {
    0: "site-and-massing",
    1: "structure-and-envelope",
    2: "interior-articulation",
    3: "dome-technical-pattern",
}


class PantheonProgressSnapshotError(RuntimeError):
    pass


def _record_ref_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _destination(area: PersistenceArea, run: RunRef) -> PersistenceDestination:
    return PersistenceDestination(area, run_id=run.run_id)


def _records(
    repository: FilesystemProjectRepository,
    run: RunRef,
    area: PersistenceArea,
) -> tuple[ProjectRecordRef, ...]:
    return repository.list_json(
        run=run,
        destination=_destination(area, run),
    )


def _load_unique_prefix(
    repository: FilesystemProjectRepository,
    refs: tuple[ProjectRecordRef, ...],
    prefix: str,
    *,
    required: bool = True,
) -> tuple[ProjectRecordRef, dict[str, Any]] | None:
    matches = tuple(
        ref
        for ref in refs
        if Path(ref.relative_path).name.startswith(prefix)
    )
    if not matches and not required:
        return None
    if len(matches) != 1:
        raise PantheonProgressSnapshotError(
            f"expected exactly one {prefix!r} record; found {len(matches)}"
        )
    ref = matches[0]
    return ref, repository.load_json(ref)


def _latest_stage_run(
    repository: FilesystemProjectRepository,
) -> RunRef:
    candidates: list[tuple[int, RunRef]] = []
    for directory in repository.layout.runs.iterdir():
        if not directory.is_dir():
            continue
        match = _RUN_ID.fullmatch(directory.name)
        if match is None or not (directory / "run.json").is_file():
            continue
        run = repository.load_run(directory.name)
        refs = _records(repository, run, PersistenceArea.RUN_CANDIDATE)
        if any(
            Path(ref.relative_path).name.startswith("candidate-manifest-")
            for ref in refs
        ):
            candidates.append((int(match.group(1)), run))
    if not candidates:
        raise PantheonProgressSnapshotError(
            "no reconstruction run has a candidate manifest"
        )
    return max(candidates, key=lambda item: item[0])[1]


def _stage_rows(
    repository: FilesystemProjectRepository,
    run: RunRef,
) -> tuple[
    ProjectRecordRef,
    dict[str, Any],
    list[dict[str, object]],
    dict[int, dict[str, Any]],
]:
    refs = _records(repository, run, PersistenceArea.RUN_CANDIDATE)
    loaded = _load_unique_prefix(repository, refs, "candidate-manifest-")
    assert loaded is not None
    manifest_ref, manifest = loaded
    if manifest.get("schema") != "P069CandidateRunManifest@1":
        raise PantheonProgressSnapshotError("candidate manifest schema drifted")
    by_uri = {ref.uri: ref for ref in refs}
    reviews: dict[int, dict[str, Any]] = {}
    rows: list[dict[str, object]] = []
    stages = manifest.get("stages")
    if not isinstance(stages, list):
        raise PantheonProgressSnapshotError("candidate manifest stages drifted")
    for item in stages:
        if not isinstance(item, Mapping):
            raise PantheonProgressSnapshotError("candidate stage entry drifted")
        stage = item.get("stage")
        review_uri = item.get("review_ref")
        if not isinstance(stage, int) or isinstance(stage, bool):
            raise PantheonProgressSnapshotError("candidate stage id drifted")
        if not isinstance(review_uri, str) or review_uri not in by_uri:
            raise PantheonProgressSnapshotError(
                f"stage {stage} review ref is absent from its P036 area"
            )
        review_ref = by_uri[review_uri]
        review = repository.load_json(review_ref)
        if (
            review.get("schema") != "P069CandidateStageReview@1"
            or review.get("stage") != stage
        ):
            raise PantheonProgressSnapshotError(
                f"stage {stage} review schema or identity drifted"
            )
        reviews[stage] = review
        contract = review.get("contract")
        fields = contract.get("fields", []) if isinstance(contract, Mapping) else []
        rows.append(
            {
                "stage": stage,
                "label": _STAGE_LABELS.get(stage, f"stage-{stage}"),
                "checks_status": review.get("checks_status"),
                "disposition": review.get("disposition"),
                "accepted_archive_created": bool(
                    review.get("accepted_archive_created", False)
                ),
                "program_digest": review.get("program_digest"),
                "predecessor_program_digest": review.get(
                    "predecessor_program_digest"
                ),
                "declaration_count": len(fields),
                "hold_reasons": list(review.get("hold_reasons", [])),
                "structure_issues": list(review.get("structure_issues", [])),
                "review_ref": _record_ref_dict(review_ref),
                "formal_stage_pack_status": "missing",
            }
        )
    rows.sort(key=lambda row: int(row["stage"]))
    return manifest_ref, manifest, rows, reviews


def _record_payloads(
    repository: FilesystemProjectRepository,
    run: RunRef,
) -> tuple[tuple[ProjectRecordRef, dict[str, Any]], ...]:
    return tuple(
        (ref, repository.load_json(ref))
        for ref in _records(repository, run, PersistenceArea.RUN_RECORD)
    )


def _latest_model_execution(
    repository: FilesystemProjectRepository,
) -> tuple[RunRef, ProjectRecordRef, dict[str, Any]] | None:
    rows: list[tuple[str, str, RunRef, ProjectRecordRef, dict[str, Any]]] = []
    for directory in repository.layout.runs.iterdir():
        if not directory.is_dir() or not (directory / "run.json").is_file():
            continue
        match = _RUN_ID.fullmatch(directory.name)
        if match is None:
            continue
        run = repository.load_run(directory.name)
        for ref in _records(repository, run, PersistenceArea.RUN_RECORD):
            if not Path(ref.relative_path).name.startswith(
                "candidate-cad-execution-"
            ):
                continue
            payload = repository.load_json(ref)
            if payload.get("schema") != "P069CandidateCadExecutionReceipt@1":
                raise PantheonProgressSnapshotError(
                    "candidate CAD execution schema drifted"
                )
            captured_at = str(payload.get("captured_at", ""))
            rows.append((captured_at, directory.name, run, ref, payload))
    if not rows:
        return None
    _, _, run, ref, payload = max(rows, key=lambda item: (item[0], item[1]))
    return run, ref, payload


def _model_alignment(
    inspection: ThreeDmInspection,
    receipt: Mapping[str, object],
    *,
    current_program_digest: str,
    expected_unit: str = "Meters",
) -> dict[str, object]:
    artifacts = receipt.get("artifacts")
    verification = receipt.get("verification")
    if not isinstance(artifacts, Mapping) or not isinstance(verification, Mapping):
        raise PantheonProgressSnapshotError("CAD receipt shape drifted")
    model = artifacts.get("model")
    if not isinstance(model, Mapping):
        raise PantheonProgressSnapshotError("CAD model receipt shape drifted")
    expected_sha = model.get("sha256")
    receipt_program = receipt.get("program_digest")
    receipt_count = verification.get("object_count")
    actual_unit = inspection.units.get("name")
    checks = {
        "file_sha_matches_execution_receipt": inspection.file_sha256 == expected_sha,
        "object_count_matches_execution_receipt": (
            inspection.object_count == receipt_count
        ),
        "unit_matches_stage_contract": actual_unit == expected_unit,
        "program_matches_current_stage": (
            receipt_program == current_program_digest
        ),
    }
    reason_codes = []
    if not checks["file_sha_matches_execution_receipt"]:
        reason_codes.append("model.digest_mismatch")
    if not checks["object_count_matches_execution_receipt"]:
        reason_codes.append("model.object_count_mismatch")
    if not checks["unit_matches_stage_contract"]:
        reason_codes.append("model.unit_mismatch")
    if not checks["program_matches_current_stage"]:
        reason_codes.append("model.stale_program")
    return {
        "schema": "StageModelAlignment@1",
        "expected_unit": expected_unit,
        "actual_unit": actual_unit,
        "execution_program_digest": receipt_program,
        "current_stage_program_digest": current_program_digest,
        "checks": checks,
        "status": "CURRENT" if not reason_codes else "BLOCKED",
        "reason_codes": reason_codes,
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
    }


def _execution_verification_summary(
    receipt: Mapping[str, object],
) -> dict[str, object]:
    """Expose compact CAD verification facts without granting acceptance.

    The direct 3DM inspection proves file identity and units.  The retained
    execution receipt separately carries strict, contract-level, and semantic
    comparisons; keeping those statuses visible prevents a current metric
    model from hiding a known geometric deviation.
    """

    verification = receipt.get("verification")
    if not isinstance(verification, Mapping):
        raise PantheonProgressSnapshotError("CAD verification shape drifted")

    def comparison(name: str) -> dict[str, object]:
        payload = verification.get(name)
        if not isinstance(payload, Mapping):
            raise PantheonProgressSnapshotError(
                f"CAD {name} verification shape drifted"
            )
        mismatches = payload.get("mismatches", [])
        if not isinstance(mismatches, list):
            raise PantheonProgressSnapshotError(
                f"CAD {name} mismatch list drifted"
            )
        return {
            "status": payload.get("status"),
            "mismatches": list(mismatches),
        }

    headless = receipt.get("headless_model_gate")
    if not isinstance(headless, Mapping):
        raise PantheonProgressSnapshotError("headless 3DM gate shape drifted")
    issues = headless.get("issues", [])
    if not isinstance(issues, list):
        raise PantheonProgressSnapshotError("headless 3DM gate issues drifted")

    return {
        "schema": "P069CadExecutionVerificationSummary@1",
        "candidate_execution_verified": bool(
            receipt.get("candidate_execution_verified", False)
        ),
        "disposition": receipt.get("disposition"),
        "strict": comparison("strict"),
        "contract": comparison("contract"),
        "semantics": comparison("semantics"),
        "strict_difference_classification": verification.get(
            "strict_difference_classification"
        ),
        "max_abs_deviation": verification.get("max_abs_deviation"),
        "headless_model_gate": {
            "passed": bool(headless.get("passed", False)),
            "units": headless.get("units"),
            "object_count": headless.get("object_count"),
            "issues": list(issues),
        },
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
    }


def _closure_inventory(
    repository: FilesystemProjectRepository,
    run: RunRef,
) -> dict[str, object]:
    branch_root = repository.layout.run(run.run_id).branches
    names: list[str] = []
    if branch_root.is_dir():
        names = [
            path.name
            for path in branch_root.glob("*/records/*.json")
            if path.is_file()
        ]
    counts = {
        "branch_scope": sum(name.startswith("branch-research-scope-") for name in names),
        "evidence_sufficiency": sum(
            "evidence-sufficiency" in name for name in names
        ),
        "stage_convergence": sum("stage-convergence" in name for name in names),
        "stage_evidence_pack": sum(
            name.startswith("stage-evidence-pack-") for name in names
        ),
    }
    return {
        "schema": "StageClosureInventory@1",
        "record_counts": counts,
        "formal_closure_present": all(value > 0 for value in counts.values()),
        "note": (
            "Record presence is inventory only; receipt payloads must still "
            "prove exact scope, sufficiency, and stage_ready."
        ),
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
    }


def build_snapshot(
    repository: FilesystemProjectRepository,
    *,
    stage_run: RunRef,
) -> dict[str, object]:
    manifest_ref, manifest, stages, reviews = _stage_rows(repository, stage_run)
    stage_three = reviews.get(3)
    if stage_three is None or not isinstance(stage_three.get("program_digest"), str):
        raise PantheonProgressSnapshotError("current run has no Stage 3 program")
    current_program_digest = str(stage_three["program_digest"])
    record_payloads = _record_payloads(repository, stage_run)
    detail_records = tuple(
        (ref, payload)
        for ref, payload in record_payloads
        if payload.get("schema") == "P069DetailEnrichmentPlan@1"
    )
    if len(detail_records) > 1:
        raise PantheonProgressSnapshotError("multiple detail plans are ambiguous")
    detail: dict[str, object] | None = None
    if detail_records:
        detail_ref, detail_payload = detail_records[0]
        detail = {
            "stage": detail_payload.get("stage"),
            "disposition": detail_payload.get("disposition"),
            "authority_state": detail_payload.get("authority_state"),
            "user_ratified": detail_payload.get("user_ratified"),
            "branch_identity": (
                detail_payload.get("branch_scope", {}).get("branch_identity")
                if isinstance(detail_payload.get("branch_scope"), Mapping)
                else None
            ),
            "decision_count": len(detail_payload.get("detail_decisions", [])),
            "known_limits": list(detail_payload.get("known_limits", [])),
            "plan_ref": _record_ref_dict(detail_ref),
        }

    expected_workspaces = []
    for ref, payload in record_payloads:
        if payload.get("schema") not in {
            "P069CandidateCadWorkspace@1",
            "P069DetailCadWorkspace@1",
        }:
            continue
        expected_outputs = payload.get("expected_outputs")
        relative = (
            expected_outputs.get("model")
            if isinstance(expected_outputs, Mapping)
            else None
        )
        exists = False
        if isinstance(relative, str):
            target = repository.layout.resolve_relative(relative)
            exists = target.is_file()
        expected_workspaces.append(
            {
                "schema": payload.get("schema"),
                "program_digest": payload.get("program_digest"),
                "expected_model_unit_system": payload.get(
                    "expected_model_unit_system",
                    "Meters",
                ),
                "model_relative_path": relative,
                "model_exists": exists,
                "workspace_ref": _record_ref_dict(ref),
            }
        )

    model_execution = _latest_model_execution(repository)
    model: dict[str, object]
    alignment: dict[str, object]
    if model_execution is None:
        model = {
            "status": "missing",
            "error": "No retained P069 candidate CAD execution exists.",
        }
        alignment = {
            "schema": "StageModelAlignment@1",
            "status": "BLOCKED",
            "reason_codes": ["model.missing"],
            "stage_acceptance_authority": False,
            "canonical_write_authority": False,
        }
    else:
        model_run, execution_ref, execution = model_execution
        artifacts = execution.get("artifacts")
        model_meta = artifacts.get("model") if isinstance(artifacts, Mapping) else None
        relative = model_meta.get("relative_path") if isinstance(model_meta, Mapping) else None
        if not isinstance(relative, str):
            raise PantheonProgressSnapshotError("CAD execution model path drifted")
        model_path = repository.layout.resolve_relative(relative)
        try:
            inspection = inspect_three_dm(model_path)
            model = {
                "status": "inspected",
                "run_id": model_run.run_id,
                "model_relative_path": relative,
                "execution_ref": _record_ref_dict(execution_ref),
                "inspection": inspection.to_dict(),
                "execution_verification": _execution_verification_summary(
                    execution
                ),
            }
            alignment = _model_alignment(
                inspection,
                execution,
                current_program_digest=current_program_digest,
            )
        except ThreeDmInspectionError as exc:
            model = {
                "status": "inspection_failed",
                "run_id": model_run.run_id,
                "model_relative_path": relative,
                "execution_ref": _record_ref_dict(execution_ref),
                "execution_verification": _execution_verification_summary(
                    execution
                ),
                "error": exc.to_dict(),
            }
            alignment = {
                "schema": "StageModelAlignment@1",
                "status": "BLOCKED",
                "reason_codes": [exc.code.value],
                "stage_acceptance_authority": False,
                "canonical_write_authority": False,
            }

    head = repository.read_head()
    return {
        "schema": "PantheonStageProgressSnapshot@1",
        "project_id": stage_run.project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "canonical_head": {
            "version": head.version,
            "state_sha256": head.require_digest(),
        },
        "current_stage_run_id": stage_run.run_id,
        "candidate_manifest": {
            "ref": _record_ref_dict(manifest_ref),
            "disposition": manifest.get("disposition"),
            "accepted_archive_created": manifest.get("accepted_archive_created"),
        },
        "stages": stages,
        "detail_candidate": detail,
        "expected_model_workspaces": expected_workspaces,
        "model": model,
        "model_alignment": alignment,
        "formal_closure": _closure_inventory(repository, stage_run),
        "view_authority": False,
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", default="pantheon-reconstruction")
    parser.add_argument(
        "--stage-run-id",
        help="candidate run to display; defaults to latest reconstruction run",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="print the snapshot without creating the P036 export record",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = resolve_probe_root(args.project_id)
    repository = FilesystemProjectRepository.open(root)
    run = (
        repository.load_run(args.stage_run_id)
        if args.stage_run_id
        else _latest_stage_run(repository)
    )
    snapshot = build_snapshot(repository, stage_run=run)
    result: dict[str, object] = {
        "snapshot": snapshot,
        "persisted": False,
    }
    if not args.no_persist:
        ref = repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.EXPORT),
            record_kind="pantheon-stage-progress-snapshot",
            payload=snapshot,
        )
        result["persisted"] = True
        result["snapshot_ref"] = _record_ref_dict(ref)
        repository.verify()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
