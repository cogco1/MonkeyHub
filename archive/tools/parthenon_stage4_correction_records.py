"""Typed, path-neutral correction records for the Parthenon Stage 4 successor.

These compilers describe why ``reconstruction-005`` is useful evidence but is
not a valid Stage predecessor, and retain the human authorization for the
closed-double-leaf door candidate.  They return sealed JSON-compatible values;
they never choose a project path, write a record, mutate canonical ``HEAD``, or
grant persistence authority.
"""

from __future__ import annotations

import copy
from typing import Mapping, Sequence

from archflow.project.refs import require_identifier
from archflow.state.geometry_program import digest_value, require_sha256
from archflow.state.operational_state import require_logical_ref


PROJECT_ID = "parthenon-reconstruction"
BRANCH_ID = "idealized-periclean-original"
ONLY_STAGE_PREDECESSOR_RUN_ID = "reconstruction-004"
FAILED_ATTEMPT_RUN_ID = "reconstruction-005"
TARGET_RESEARCH_RUN_ID = "research-007"
TARGET_RECONSTRUCTION_RUN_ID = "reconstruction-006"

CANONICAL_VERSION = 0
CANONICAL_STATE_SHA256 = (
    "2aa733c5fb565428a4c08aed1d278135d571bded474b64f89b6039a6ee44f266"
)
STAGE3_STATE_SHA256 = (
    "cd7234b80a502afdcfb4251b945f5e90a4bc97dff634398315448ebbcd122bed"
)
STAGE3_STATE_DIGEST = (
    "2fbffcb6c1f7b8f2502f53acb0c9e899fa99d127043a8ce557d986babea13a32"
)
STAGE3_PACK_SHA256 = (
    "ac4d8ee9ac6910eb48b7349f92b6271556b13a9d5e166b799d7a7e2ed855a3e3"
)
STAGE3_PROGRAM_SHA256 = (
    "ba4eb7bbb0b32a2100880bedddcf52abf8a51afa9cf343a7390be46d2b88ef6c"
)
STAGE3_PROGRAM_DIGEST = (
    "9f54dbc72b758948d6c78aa738c9877195ec8cd616bba35d845dfac53ec948c6"
)
STAGE3_MODEL_SHA256 = (
    "b5815fa4f8f7b04fc276585cc760e37366527c94eeb8f992541e3cec47dae7f0"
)
STAGE3_PROGRESS_SHA256 = (
    "ac778e607e87f1f9625121f3e70742b28c0dffcc1426365f893d3b98b4f4799a"
)
STAGE3_SPATIAL_SHA256 = (
    "c07db4eb9f3547111bc1f61c5b642378cb4b5fc0802add672d6a968dce7b3969"
)

STAGE3_STATE_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-004/branches/"
    "idealized-periclean-original/records/stage-3-operational-state-"
    f"{STAGE3_STATE_SHA256}.json"
)
STAGE3_PACK_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-004/branches/"
    "idealized-periclean-original/records/stage-3-evidence-pack-"
    f"{STAGE3_PACK_SHA256}.json"
)
STAGE3_PROGRAM_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-004/branches/"
    "idealized-periclean-original/records/stage-3-geometry-program-"
    f"{STAGE3_PROGRAM_SHA256}.json"
)
STAGE3_MODEL_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-004/workspaces/"
    "cad-stage-3/parthenon-candidate.3dm"
)
STAGE3_PROGRESS_REF = (
    "project://parthenon-reconstruction/exports/parthenon-progress-snapshot-"
    f"{STAGE3_PROGRESS_SHA256}.json"
)
STAGE3_SPATIAL_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-004/branches/"
    "idealized-periclean-original/records/stage-3-spatial-validation-"
    f"{STAGE3_SPATIAL_SHA256}.json"
)

FAILED_STAGE4_PROGRESS_SHA256 = (
    "1060f6732d80606e449940c9107160f2fdd1e4ab9271367f8f83f8c32b5dbc7c"
)
FAILED_STAGE4_PACK_SHA256 = (
    "533cd7d76d7ed86a5152c9bc0d802cf9846725bfcb6d850235ec4b3835dc39ca"
)
FAILED_STAGE4_STATE_SHA256 = (
    "3ba5880632383d89b59ba372c46b3513b86400c2235deb91f4716e9e79b5c096"
)
FAILED_STAGE4_MODEL_SHA256 = (
    "79e333e33c8db6edcee7ee83068373fcba6bb97ed4aba6c6eda39e5514b1acba"
)
FAILED_STAGE4_PROGRAM_SHA256 = (
    "25d030ea54a415a93d83094b969dc3c48d622476fa3a7f7c0563f77c7ac1ce6c"
)
FAILED_STAGE4_PROGRAM_DIGEST = (
    "1402b4623daf5fff93e6e0e48621aefc8cebd457ffc63763946e2c45f4eb6a34"
)
FAILED_STAGE4_PROGRESS_REF = (
    "project://parthenon-reconstruction/exports/parthenon-stage-4-progress-"
    f"{FAILED_STAGE4_PROGRESS_SHA256}.json"
)
FAILED_STAGE4_PACK_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-005/branches/"
    "idealized-periclean-original/records/stage-4-evidence-pack-"
    f"{FAILED_STAGE4_PACK_SHA256}.json"
)
FAILED_STAGE4_STATE_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-005/branches/"
    "idealized-periclean-original/records/stage-4-operational-state-"
    f"{FAILED_STAGE4_STATE_SHA256}.json"
)
FAILED_STAGE4_MODEL_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-005/workspaces/"
    "cad-stage-4/parthenon-stage-4-detail-candidate.3dm"
)
FAILED_STAGE4_PROGRAM_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-005/branches/"
    "idealized-periclean-original/records/stage-4-geometry-program-"
    f"{FAILED_STAGE4_PROGRAM_SHA256}.json"
)
FAILED_STAGE4_INSPECTION_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-005/branches/"
    "idealized-periclean-original/records/stage-4-model-inspection-"
    "c9b199e6fa5d8701c91e6dd7398d00172e18217df0bfc859437f1b2b3b3adb4d.json"
)
FAILED_STAGE4_SPATIAL_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-005/branches/"
    "idealized-periclean-original/records/stage-4-spatial-validation-"
    "b6a48abea56f8ff6fa5cc0b78f059ac7746d747398fc3123337c808e7c474357.json"
)
FAILED_STAGE4_DETAIL_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-005/branches/"
    "idealized-periclean-original/records/stage-4-detail-validation-"
    "5d517a9b6dbc70bfa7b96ee71f77a0042330355a870ff364b776b7392fd5c310.json"
)
FAILED_STAGE4_EVIDENCE_GATE_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-005/branches/"
    "idealized-periclean-original/records/stage-4-evidence-gate-"
    "73fdf8ec205ec1342f44a76eac4972d9b0a3027be61edb468fab90f173662f24.json"
)
FAILED_STAGE4_USAGE_REF = (
    "project://parthenon-reconstruction/runs/reconstruction-005/records/"
    "stage-4-usage-2e2dc9024bd92400f81a489e0f3b9915e204740de0c9b7517f86e4c12feb0fa5.json"
)
FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS = (
    FAILED_STAGE4_PROGRESS_REF,
    FAILED_STAGE4_PACK_REF,
    FAILED_STAGE4_STATE_REF,
    FAILED_STAGE4_MODEL_REF,
    FAILED_STAGE4_PROGRAM_REF,
    FAILED_STAGE4_INSPECTION_REF,
    FAILED_STAGE4_SPATIAL_REF,
    FAILED_STAGE4_DETAIL_REF,
    FAILED_STAGE4_EVIDENCE_GATE_REF,
    FAILED_STAGE4_USAGE_REF,
)

VISUAL_MANIFEST_SHA256 = (
    "d3ff9f427bbe4126d8346ad3be18831b638ea8f08d18e3b007234d7e9a4da7bd"
)
VISUAL_MANIFEST_REF = (
    "project://parthenon-reconstruction/runs/research-005/branches/"
    "idealized-periclean-original/records/visual-region-manifest-"
    f"{VISUAL_MANIFEST_SHA256}.json"
)

ONLY_STAGE_PREDECESSOR = {
    "schema": "ParthenonOnlyStagePredecessor@1",
    "project_id": PROJECT_ID,
    "run_id": ONLY_STAGE_PREDECESSOR_RUN_ID,
    "branch_id": BRANCH_ID,
    "stage_index": 3,
    "canonical_base": {
        "version": CANONICAL_VERSION,
        "state_sha256": CANONICAL_STATE_SHA256,
    },
    "state": {
        "ref": STAGE3_STATE_REF,
        "record_sha256": STAGE3_STATE_SHA256,
        "state_digest": STAGE3_STATE_DIGEST,
        "fact_count": 18,
        "lock_count": 1,
        "satisfied_obligation_count": 4,
    },
    "pack": {
        "ref": STAGE3_PACK_REF,
        "sha256": STAGE3_PACK_SHA256,
        "status": "COMPLETE",
    },
    "program": {
        "ref": STAGE3_PROGRAM_REF,
        "sha256": STAGE3_PROGRAM_SHA256,
        "program_digest": STAGE3_PROGRAM_DIGEST,
        "operation_count": 271,
    },
    "model": {
        "ref": STAGE3_MODEL_REF,
        "sha256": STAGE3_MODEL_SHA256,
        "object_count": 271,
        "units": "Meters",
        "coordinate_system": "RhinoWorldXY_ZUp",
    },
    "progress": {
        "ref": STAGE3_PROGRESS_REF,
        "sha256": STAGE3_PROGRESS_SHA256,
    },
    "spatial": {
        "ref": STAGE3_SPATIAL_REF,
        "sha256": STAGE3_SPATIAL_SHA256,
        "status": "PASSED",
    },
}

FAILED_ATTEMPT_POLICY = {
    "schema": "ParthenonFailedStageAttemptPolicy@1",
    "run_id": FAILED_ATTEMPT_RUN_ID,
    "branch_id": BRANCH_ID,
    "stage_index": 4,
    "disposition": "HOLD",
    "role": "FAILED_ATTEMPT_EVIDENCE_ONLY",
    "eligible_as_exact_stage_predecessor": False,
    "eligible_for_canonical_promotion": False,
    "directory_order_or_mtime_selection_forbidden": True,
    "may_inform_correction": True,
}

EXPECTED_FAILURE_FINDINGS = {
    "schema": "ParthenonIncompleteStage4Findings@1",
    "stage3_root_operation_count": 271,
    "old_refined_root_count": 110,
    "old_copied_root_count": 161,
    "old_stage4_operation_count": 509,
    "old_model_object_count": 509,
    "old_program_record_sha256": FAILED_STAGE4_PROGRAM_SHA256,
    "old_program_digest": FAILED_STAGE4_PROGRAM_DIGEST,
    "full_stage3_denominator_gate_present": False,
    "roof_pediment": {
        "unrefined_stage3_root_ids": [
            "main-gabled-roof",
            "pediment-east",
            "pediment-west",
        ],
        "architectural_detail_complete": False,
        "sculpture_scope": "PARKED",
    },
    "inner_colonnade": {
        "direct_lower_to_upper_shaft_contact_count": 23,
        "typed_intertier_support_chain_present": False,
    },
    "east_windows": {
        "obstruction_count": 2,
        "obstruction_kind": "projected_side_aisle_or_view_overlap",
        "ordinary_positive_volume_column_wall_collision": False,
    },
    "principal_doors": {
        "stone_opening_width_m": 4.92,
        "stone_opening_height_m": 9.84,
        "old_leaf_width_m": 4.20,
        "old_leaf_height_m": 7.00,
        "side_gap_each_m": 0.36,
        "top_gap_m": 2.84,
        "shared_aperture_dependency_present": False,
    },
    "materials": {
        "model_object_count": 509,
        "effective_render_material_object_count": 0,
        "effective_render_material_coverage_ratio": 0.0,
        "metadata_only_material_object_count": 509,
    },
    "closure_interpretation": {
        "old_run_remains_hold": True,
        "old_complete_pack_does_not_prove_full_building_detail": True,
        "old_run_must_not_be_new_exact_stage_predecessor": True,
    },
}

REQUIRED_INVALIDATES_ON = (
    "authorization_changed_or_revoked",
    "branch_changed",
    "door_host_or_partition_changed",
    "evidence_scope_changed",
    "metric_authority_claimed",
    "only_stage_predecessor_changed",
    "principal_aperture_dependency_changed",
    "strategy_changed",
)

AUTHORIZED_DOOR_SCOPE = {
    "schema": "ParthenonAuthorizedDoorScope@1",
    "strategy": "AUTHORIZED_CLOSED_DOUBLE_LEAF",
    "authorized_component_ids": ["door-east", "door-west"],
    "authorized_geometry_roles": [
        "frame-header",
        "frame-jamb-left",
        "frame-jamb-right",
        "frame-threshold",
        "leaf-left",
        "leaf-right",
    ],
    "dependency_contract": (
        "stone aperture, frame/reveal, and leaf envelope must derive from one "
        "typed host-local aperture contract"
    ),
    "candidate_parameters": {
        "rough_opening_width_m": 4.96,
        "frame_jamb_reveal_m": 0.025,
        "frame_header_reveal_m": 0.030,
        "frame_threshold_reveal_m": 0.030,
        "leaf_perimeter_gap_m": 0.010,
        "leaf_meeting_gap_m": 0.020,
        "classification": "SOFT",
        "metric_authority": False,
    },
    "historical_truth_authorized": False,
    "exact_ancient_frame_profile_authorized": False,
    "exact_ancient_leaf_articulation_authorized": False,
    "canonical_promotion_authorized": False,
    "excluded_geometry": [
        "internal-room-door",
        "readable-inscription",
        "lost-figurative-decoration",
        "later-branch-intervention",
    ],
}


class ParthenonStage4CorrectionRecordError(ValueError):
    """A correction record is incomplete, ambiguous, or semantically unsafe."""


def compute_correction_record_digest(record: Mapping[str, object]) -> str:
    """Return the stable digest over a record excluding its digest field."""

    if not isinstance(record, Mapping):
        raise TypeError("record must be a mapping")
    payload = {key: copy.deepcopy(value) for key, value in record.items() if key != "record_digest"}
    return digest_value(payload)


def _seal(payload: Mapping[str, object]) -> dict[str, object]:
    result = copy.deepcopy(dict(payload))
    result["record_digest"] = compute_correction_record_digest(result)
    return result


def _ids(values: Sequence[str], field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or (not values and not allow_empty)
    ):
        raise ParthenonStage4CorrectionRecordError(
            f"{field} must be a {'possibly empty' if allow_empty else 'non-empty'} sequence"
        )
    result = tuple(sorted(require_identifier(value, field) for value in values))
    if len(result) != len(set(result)):
        raise ParthenonStage4CorrectionRecordError(f"{field} contains duplicates")
    return result


def _refs(values: Sequence[str], field: str) -> tuple[str, ...]:
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or not values
    ):
        raise ParthenonStage4CorrectionRecordError(
            f"{field} must be a non-empty sequence"
        )
    result = tuple(sorted(require_logical_ref(value, field) for value in values))
    if len(result) != len(set(result)):
        raise ParthenonStage4CorrectionRecordError(f"{field} contains duplicates")
    return result


def _authorization_ref(value: object) -> str:
    ref = require_logical_ref(value, "authorization_ref")
    if not (
        ref.startswith("decision:human-authorized-")
        or ref.startswith("authority-receipt:")
        or ref.startswith("project://")
    ):
        raise ParthenonStage4CorrectionRecordError(
            "authorization_ref must identify an explicit human decision"
        )
    return ref


def _validate_digest(record: Mapping[str, object], failures: list[str]) -> None:
    stored = record.get("record_digest")
    try:
        require_sha256(stored, "record_digest")
    except (TypeError, ValueError) as exc:
        failures.append(str(exc))
        return
    if stored != compute_correction_record_digest(record):
        failures.append("record_digest does not match canonical record content")


def _exact_keys(
    value: Mapping[str, object], expected: set[str], field: str, failures: list[str]
) -> None:
    observed = set(value)
    if observed != expected:
        failures.append(
            f"{field} keys drifted: missing={sorted(expected - observed)}, "
            f"unexpected={sorted(observed - expected)}"
        )


def compile_incomplete_stage4_experience_record(
    *,
    stage3_root_operation_ids: Sequence[str],
    refined_stage3_root_ids: Sequence[str],
    copied_stage3_root_ids: Sequence[str],
    failed_attempt_evidence_refs: Sequence[str],
) -> dict[str, object]:
    """Compile the bounded reconstruction-005 failed-attempt experience."""

    roots = _ids(stage3_root_operation_ids, "stage3_root_operation_ids")
    refined = _ids(refined_stage3_root_ids, "refined_stage3_root_ids")
    copied = _ids(copied_stage3_root_ids, "copied_stage3_root_ids")
    evidence_refs = _refs(failed_attempt_evidence_refs, "failed_attempt_evidence_refs")
    if len(roots) != 271 or len(refined) != 110 or len(copied) != 161:
        raise ParthenonStage4CorrectionRecordError(
            "experience requires exactly 271 roots partitioned as 110 refined and 161 copied"
        )
    if set(refined) & set(copied) or set(refined) | set(copied) != set(roots):
        raise ParthenonStage4CorrectionRecordError(
            "refined/copied roots must be a disjoint full Stage 3 denominator"
        )
    if not set(FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS).issubset(evidence_refs):
        raise ParthenonStage4CorrectionRecordError(
            "failed-attempt evidence omits a required reconstruction-005 artifact"
        )
    payload = {
        "schema": "ParthenonStage4IncompleteAttemptExperience@1",
        "project_id": PROJECT_ID,
        "branch_id": BRANCH_ID,
        "record_owner_run_id": TARGET_RESEARCH_RUN_ID,
        "consumer_run_id": TARGET_RECONSTRUCTION_RUN_ID,
        "only_stage_predecessor": copy.deepcopy(ONLY_STAGE_PREDECESSOR),
        "failed_attempt_policy": copy.deepcopy(FAILED_ATTEMPT_POLICY),
        "failed_attempt_evidence_refs": list(evidence_refs),
        "coverage": {
            "stage3_root_operation_ids": list(roots),
            "refined_stage3_root_ids": list(refined),
            "copied_stage3_root_ids": list(copied),
            "stage3_root_operation_count": len(roots),
            "refined_stage3_root_count": len(refined),
            "copied_stage3_root_count": len(copied),
        },
        "failure_findings": copy.deepcopy(EXPECTED_FAILURE_FINDINGS),
        "correction_requirement": {
            "compile_from_only_stage_predecessor": ONLY_STAGE_PREDECESSOR_RUN_ID,
            "use_failed_attempt_as": "FAILED_ATTEMPT_EVIDENCE_ONLY",
            "full_stage3_semantic_denominator_required": True,
            "target_research_run_id": TARGET_RESEARCH_RUN_ID,
            "target_reconstruction_run_id": TARGET_RECONSTRUCTION_RUN_ID,
            "canonical_head_must_remain": {
                "version": CANONICAL_VERSION,
                "state_sha256": CANONICAL_STATE_SHA256,
            },
        },
    }
    record = _seal(payload)
    validation = validate_incomplete_stage4_experience_record(record)
    if not validation["passed"]:
        raise ParthenonStage4CorrectionRecordError(
            "; ".join(str(item) for item in validation["failures"])
        )
    return record


def validate_incomplete_stage4_experience_record(
    record: Mapping[str, object],
) -> dict[str, object]:
    failures: list[str] = []
    if not isinstance(record, Mapping):
        return {
            "schema": "ParthenonStage4CorrectionRecordValidation@1",
            "record_schema": None,
            "passed": False,
            "failures": ["record must be a mapping"],
        }
    expected_keys = {
        "schema",
        "project_id",
        "branch_id",
        "record_owner_run_id",
        "consumer_run_id",
        "only_stage_predecessor",
        "failed_attempt_policy",
        "failed_attempt_evidence_refs",
        "coverage",
        "failure_findings",
        "correction_requirement",
        "record_digest",
    }
    _exact_keys(record, expected_keys, "experience record", failures)
    _validate_digest(record, failures)
    if record.get("schema") != "ParthenonStage4IncompleteAttemptExperience@1":
        failures.append("experience schema drifted")
    if (
        record.get("project_id") != PROJECT_ID
        or record.get("branch_id") != BRANCH_ID
        or record.get("record_owner_run_id") != TARGET_RESEARCH_RUN_ID
        or record.get("consumer_run_id") != TARGET_RECONSTRUCTION_RUN_ID
    ):
        failures.append("experience project/branch/owner/consumer identity drifted")
    if record.get("only_stage_predecessor") != ONLY_STAGE_PREDECESSOR:
        failures.append("only_stage_predecessor is not exact reconstruction-004")
    if record.get("failed_attempt_policy") != FAILED_ATTEMPT_POLICY:
        failures.append("reconstruction-005 failed-attempt policy drifted")
    evidence = record.get("failed_attempt_evidence_refs")
    if (
        not isinstance(evidence, list)
        or evidence != sorted(set(evidence))
        or not set(FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS).issubset(evidence)
    ):
        failures.append("failed-attempt evidence refs are incomplete or non-canonical")
    else:
        for ref in evidence:
            try:
                require_logical_ref(ref, "failed_attempt_evidence_ref")
            except (TypeError, ValueError) as exc:
                failures.append(str(exc))
    coverage = record.get("coverage")
    if not isinstance(coverage, Mapping):
        failures.append("coverage must be a mapping")
    else:
        expected_coverage_keys = {
            "stage3_root_operation_ids",
            "refined_stage3_root_ids",
            "copied_stage3_root_ids",
            "stage3_root_operation_count",
            "refined_stage3_root_count",
            "copied_stage3_root_count",
        }
        _exact_keys(coverage, expected_coverage_keys, "coverage", failures)
        roots = coverage.get("stage3_root_operation_ids")
        refined = coverage.get("refined_stage3_root_ids")
        copied = coverage.get("copied_stage3_root_ids")
        if not all(isinstance(value, list) for value in (roots, refined, copied)):
            failures.append("coverage root arrays must be lists")
        else:
            assert isinstance(roots, list) and isinstance(refined, list) and isinstance(copied, list)
            if (
                len(roots) != 271
                or len(refined) != 110
                or len(copied) != 161
                or len(set(roots)) != 271
                or len(set(refined)) != 110
                or len(set(copied)) != 161
                or set(refined) & set(copied)
                or set(refined) | set(copied) != set(roots)
                or roots != sorted(roots)
                or refined != sorted(refined)
                or copied != sorted(copied)
            ):
                failures.append("coverage is not the exact 271 = 110 + 161 partition")
            if not {
                "main-gabled-roof",
                "pediment-east",
                "pediment-west",
                "cella-door-east",
                "cella-door-west",
            }.issubset(copied):
                failures.append("known unrefined roof/pediment/door roots are not copied")
            if (
                coverage.get("stage3_root_operation_count") != 271
                or coverage.get("refined_stage3_root_count") != 110
                or coverage.get("copied_stage3_root_count") != 161
            ):
                failures.append("coverage summary counts drifted")
    if record.get("failure_findings") != EXPECTED_FAILURE_FINDINGS:
        failures.append("incomplete Stage 4 failure findings drifted")
    expected_requirement = {
        "compile_from_only_stage_predecessor": ONLY_STAGE_PREDECESSOR_RUN_ID,
        "use_failed_attempt_as": "FAILED_ATTEMPT_EVIDENCE_ONLY",
        "full_stage3_semantic_denominator_required": True,
        "target_research_run_id": TARGET_RESEARCH_RUN_ID,
        "target_reconstruction_run_id": TARGET_RECONSTRUCTION_RUN_ID,
        "canonical_head_must_remain": {
            "version": CANONICAL_VERSION,
            "state_sha256": CANONICAL_STATE_SHA256,
        },
    }
    if record.get("correction_requirement") != expected_requirement:
        failures.append("correction requirement or canonical HEAD boundary drifted")
    return {
        "schema": "ParthenonStage4CorrectionRecordValidation@1",
        "record_schema": record.get("schema"),
        "record_digest": record.get("record_digest"),
        "passed": not failures,
        "failures": failures,
        "only_stage_predecessor_run_id": (
            record.get("only_stage_predecessor", {}).get("run_id")
            if isinstance(record.get("only_stage_predecessor"), Mapping)
            else None
        ),
    }


def compile_authorized_door_decision_record(
    *,
    authorization_ref: str,
    failed_attempt_experience_ref: str,
    failed_attempt_experience_digest: str | None = None,
    door_dependency_diagnosis_ref: str,
    selected_visual_manifest_ref: str,
    invalidates_on: Sequence[str] = REQUIRED_INVALIDATES_ON,
) -> dict[str, object]:
    """Compile the bounded human authorization for the SOFT door candidate."""

    authorization = _authorization_ref(authorization_ref)
    experience_ref = require_logical_ref(
        failed_attempt_experience_ref, "failed_attempt_experience_ref"
    )
    if failed_attempt_experience_digest is None:
        inferred_digest = experience_ref.rsplit("-", 1)[-1].removesuffix(".json")
        experience_digest = require_sha256(
            inferred_digest,
            "failed_attempt_experience_digest",
        )
    else:
        experience_digest = require_sha256(
            failed_attempt_experience_digest,
            "failed_attempt_experience_digest",
        )
    diagnosis_ref = require_logical_ref(
        door_dependency_diagnosis_ref, "door_dependency_diagnosis_ref"
    )
    visual_ref = require_logical_ref(
        selected_visual_manifest_ref, "selected_visual_manifest_ref"
    )
    conditions = _ids(invalidates_on, "invalidates_on")
    if conditions != tuple(sorted(REQUIRED_INVALIDATES_ON)):
        raise ParthenonStage4CorrectionRecordError(
            "invalidates_on must contain every required authorization invalidator"
        )
    if f"/runs/{TARGET_RESEARCH_RUN_ID}/" not in experience_ref:
        raise ParthenonStage4CorrectionRecordError(
            "failed_attempt_experience_ref must resolve inside research-007"
        )
    if f"/runs/{TARGET_RESEARCH_RUN_ID}/" not in diagnosis_ref:
        raise ParthenonStage4CorrectionRecordError(
            "door_dependency_diagnosis_ref must resolve inside research-007"
        )
    if visual_ref != VISUAL_MANIFEST_REF:
        raise ParthenonStage4CorrectionRecordError(
            "selected visual manifest is not the exact research-005 predecessor"
        )
    dependency_chain = [
        {
            "ordinal": 0,
            "role": "ONLY_STAGE_PREDECESSOR_PROGRAM",
            "ref": STAGE3_PROGRAM_REF,
            "authority": "EXACT_GEOMETRY_PREDECESSOR",
        },
        {
            "ordinal": 1,
            "role": "FAILED_ATTEMPT_EXPERIENCE",
            "ref": experience_ref,
            "record_digest": experience_digest,
            "authority": "DIAGNOSTIC_ONLY",
        },
        {
            "ordinal": 2,
            "role": "DOOR_DEPENDENCY_DIAGNOSIS",
            "ref": diagnosis_ref,
            "authority": "MEASURED_DEFECT",
        },
        {
            "ordinal": 3,
            "role": "SELECTED_VISUAL_MANIFEST",
            "ref": visual_ref,
            "authority": "TOPOLOGY_OR_MORPHOLOGY_ONLY",
        },
        {
            "ordinal": 4,
            "role": "HUMAN_AUTHORIZATION",
            "ref": authorization,
            "authority": "CANDIDATE_STRATEGY_ONLY",
        },
    ]
    payload = {
        "schema": "ParthenonStage4DoorHumanDecision@1",
        "project_id": PROJECT_ID,
        "branch_id": BRANCH_ID,
        "record_owner_run_id": TARGET_RESEARCH_RUN_ID,
        "consumer_run_id": TARGET_RECONSTRUCTION_RUN_ID,
        "authorization_ref": authorization,
        "authorization_status": "AUTHORIZED_CANDIDATE_ONLY",
        "authorization_scope": copy.deepcopy(AUTHORIZED_DOOR_SCOPE),
        "only_stage_predecessor": copy.deepcopy(ONLY_STAGE_PREDECESSOR),
        "failed_attempt_policy": copy.deepcopy(FAILED_ATTEMPT_POLICY),
        "failed_attempt_experience_ref": experience_ref,
        "failed_attempt_experience_digest": experience_digest,
        "door_dependency_diagnosis_ref": diagnosis_ref,
        "selected_visual_manifest_ref": visual_ref,
        "dependency_chain": dependency_chain,
        "invalidates_on": [
            {
                "condition_id": condition,
                "effect": "INVALIDATE_AND_REQUIRE_NEW_HUMAN_AUTHORIZATION",
            }
            for condition in conditions
        ],
        "authority_boundary": {
            "strategy_authorized": True,
            "candidate_geometry_authorized": True,
            "historical_truth_authorized": False,
            "visual_metric_authority": False,
            "soft_parameter_metric_authority": False,
            "canonical_promotion_authorized": False,
        },
    }
    record = _seal(payload)
    validation = validate_authorized_door_decision_record(record)
    if not validation["passed"]:
        raise ParthenonStage4CorrectionRecordError(
            "; ".join(str(item) for item in validation["failures"])
        )
    return record


def validate_authorized_door_decision_record(
    record: Mapping[str, object],
) -> dict[str, object]:
    failures: list[str] = []
    if not isinstance(record, Mapping):
        return {
            "schema": "ParthenonStage4CorrectionRecordValidation@1",
            "record_schema": None,
            "passed": False,
            "failures": ["record must be a mapping"],
        }
    expected_keys = {
        "schema",
        "project_id",
        "branch_id",
        "record_owner_run_id",
        "consumer_run_id",
        "authorization_ref",
        "authorization_status",
        "authorization_scope",
        "only_stage_predecessor",
        "failed_attempt_policy",
        "failed_attempt_experience_ref",
        "failed_attempt_experience_digest",
        "door_dependency_diagnosis_ref",
        "selected_visual_manifest_ref",
        "dependency_chain",
        "invalidates_on",
        "authority_boundary",
        "record_digest",
    }
    _exact_keys(record, expected_keys, "door decision record", failures)
    _validate_digest(record, failures)
    if record.get("schema") != "ParthenonStage4DoorHumanDecision@1":
        failures.append("door decision schema drifted")
    if (
        record.get("project_id") != PROJECT_ID
        or record.get("branch_id") != BRANCH_ID
        or record.get("record_owner_run_id") != TARGET_RESEARCH_RUN_ID
        or record.get("consumer_run_id") != TARGET_RECONSTRUCTION_RUN_ID
    ):
        failures.append("door decision project/branch/owner/consumer identity drifted")
    try:
        authorization = _authorization_ref(record.get("authorization_ref"))
    except (TypeError, ValueError) as exc:
        failures.append(str(exc))
        authorization = None
    if record.get("authorization_status") != "AUTHORIZED_CANDIDATE_ONLY":
        failures.append("door authorization status drifted")
    if record.get("authorization_scope") != AUTHORIZED_DOOR_SCOPE:
        failures.append("door authorization scope or SOFT authority boundary drifted")
    if record.get("only_stage_predecessor") != ONLY_STAGE_PREDECESSOR:
        failures.append("only_stage_predecessor is not exact reconstruction-004")
    if record.get("failed_attempt_policy") != FAILED_ATTEMPT_POLICY:
        failures.append("reconstruction-005 escaped failed-attempt-only policy")
    experience_ref = record.get("failed_attempt_experience_ref")
    experience_digest = record.get("failed_attempt_experience_digest")
    diagnosis_ref = record.get("door_dependency_diagnosis_ref")
    visual_ref = record.get("selected_visual_manifest_ref")
    for value, field in (
        (experience_ref, "failed_attempt_experience_ref"),
        (diagnosis_ref, "door_dependency_diagnosis_ref"),
        (visual_ref, "selected_visual_manifest_ref"),
    ):
        try:
            require_logical_ref(value, field)
        except (TypeError, ValueError) as exc:
            failures.append(str(exc))
    if not isinstance(experience_ref, str) or f"/runs/{TARGET_RESEARCH_RUN_ID}/" not in experience_ref:
        failures.append("failed attempt experience is not owned by research-007")
    try:
        experience_digest = require_sha256(
            experience_digest,
            "failed_attempt_experience_digest",
        )
    except (TypeError, ValueError) as exc:
        failures.append(str(exc))
        experience_digest = None
    if not isinstance(diagnosis_ref, str) or f"/runs/{TARGET_RESEARCH_RUN_ID}/" not in diagnosis_ref:
        failures.append("door diagnosis is not owned by research-007")
    if visual_ref != VISUAL_MANIFEST_REF:
        failures.append("door decision visual predecessor drifted")
    expected_chain = [
        {
            "ordinal": 0,
            "role": "ONLY_STAGE_PREDECESSOR_PROGRAM",
            "ref": STAGE3_PROGRAM_REF,
            "authority": "EXACT_GEOMETRY_PREDECESSOR",
        },
        {
            "ordinal": 1,
            "role": "FAILED_ATTEMPT_EXPERIENCE",
            "ref": experience_ref,
            "record_digest": experience_digest,
            "authority": "DIAGNOSTIC_ONLY",
        },
        {
            "ordinal": 2,
            "role": "DOOR_DEPENDENCY_DIAGNOSIS",
            "ref": diagnosis_ref,
            "authority": "MEASURED_DEFECT",
        },
        {
            "ordinal": 3,
            "role": "SELECTED_VISUAL_MANIFEST",
            "ref": VISUAL_MANIFEST_REF,
            "authority": "TOPOLOGY_OR_MORPHOLOGY_ONLY",
        },
        {
            "ordinal": 4,
            "role": "HUMAN_AUTHORIZATION",
            "ref": authorization,
            "authority": "CANDIDATE_STRATEGY_ONLY",
        },
    ]
    if record.get("dependency_chain") != expected_chain:
        failures.append("door dependency chain drifted or changed authority")
    expected_invalidates = [
        {
            "condition_id": condition,
            "effect": "INVALIDATE_AND_REQUIRE_NEW_HUMAN_AUTHORIZATION",
        }
        for condition in sorted(REQUIRED_INVALIDATES_ON)
    ]
    if record.get("invalidates_on") != expected_invalidates:
        failures.append("door invalidates_on conditions are incomplete or non-canonical")
    expected_boundary = {
        "strategy_authorized": True,
        "candidate_geometry_authorized": True,
        "historical_truth_authorized": False,
        "visual_metric_authority": False,
        "soft_parameter_metric_authority": False,
        "canonical_promotion_authorized": False,
    }
    if record.get("authority_boundary") != expected_boundary:
        failures.append("door authority boundary drifted")
    return {
        "schema": "ParthenonStage4CorrectionRecordValidation@1",
        "record_schema": record.get("schema"),
        "record_digest": record.get("record_digest"),
        "passed": not failures,
        "failures": failures,
        "only_stage_predecessor_run_id": (
            record.get("only_stage_predecessor", {}).get("run_id")
            if isinstance(record.get("only_stage_predecessor"), Mapping)
            else None
        ),
        "failed_attempt_run_id": (
            record.get("failed_attempt_policy", {}).get("run_id")
            if isinstance(record.get("failed_attempt_policy"), Mapping)
            else None
        ),
        "authorization_ref": authorization,
    }


def render_stage4_correction_report(
    experience_record: Mapping[str, object],
    door_decision_record: Mapping[str, object],
) -> str:
    """Render a deterministic human-readable correction report.

    Rendering is deliberately downstream of both typed validators.  The door
    decision must also bind the digest of the exact experience record supplied
    to this call; two individually valid but mutually unrelated records cannot
    be presented as one correction chain.
    """

    experience_validation = validate_incomplete_stage4_experience_record(
        experience_record
    )
    if not experience_validation["passed"]:
        raise ParthenonStage4CorrectionRecordError(
            "cannot render invalid Stage 4 experience record: "
            + "; ".join(
                str(item) for item in experience_validation["failures"]
            )
        )
    decision_validation = validate_authorized_door_decision_record(
        door_decision_record
    )
    if not decision_validation["passed"]:
        raise ParthenonStage4CorrectionRecordError(
            "cannot render invalid door decision record: "
            + "; ".join(str(item) for item in decision_validation["failures"])
        )
    experience_digest = str(experience_record["record_digest"])
    decision_experience_digest = str(
        door_decision_record["failed_attempt_experience_digest"]
    )
    if decision_experience_digest != experience_digest:
        raise ParthenonStage4CorrectionRecordError(
            "door decision does not bind the supplied experience record digest"
        )

    coverage = experience_record["coverage"]
    findings = experience_record["failure_findings"]
    requirement = experience_record["correction_requirement"]
    predecessor = experience_record["only_stage_predecessor"]
    failed_policy = experience_record["failed_attempt_policy"]
    authorization_scope = door_decision_record["authorization_scope"]
    authority_boundary = door_decision_record["authority_boundary"]
    assert isinstance(coverage, Mapping)
    assert isinstance(findings, Mapping)
    assert isinstance(requirement, Mapping)
    assert isinstance(predecessor, Mapping)
    assert isinstance(failed_policy, Mapping)
    assert isinstance(authorization_scope, Mapping)
    assert isinstance(authority_boundary, Mapping)
    roof = findings["roof_pediment"]
    inner = findings["inner_colonnade"]
    windows = findings["east_windows"]
    doors = findings["principal_doors"]
    materials = findings["materials"]
    closure = findings["closure_interpretation"]
    parameters = authorization_scope["candidate_parameters"]
    assert all(
        isinstance(value, Mapping)
        for value in (
            roof,
            inner,
            windows,
            doors,
            materials,
            closure,
            parameters,
        )
    )

    lines = [
        "# Parthenon Stage 4 修正与人工决策报告",
        "",
        "## 结论与前驱边界",
        "",
        (
            f"- **唯一 Stage 前驱是 `{predecessor['run_id']}`**，固定为 "
            f"Stage {predecessor['stage_index']}、分支 `{predecessor['branch_id']}`；"
            "新 Stage 4 必须从其 exact state / pack / program / model 机械继承。"
        ),
        (
            f"- `{failed_policy['run_id']}` 仅作 "
            f"`{failed_policy['role']}`；其 disposition 为 `{failed_policy['disposition']}`，"
            "不得成为 exact Stage predecessor，也不得 canonical promotion。"
        ),
        (
            f"- 修正目标是 `{requirement['target_research_run_id']}` 与 "
            f"`{requirement['target_reconstruction_run_id']}`；canonical HEAD 保持 "
            f"version {requirement['canonical_head_must_remain']['version']} / "
            f"state SHA-256 `{requirement['canonical_head_must_remain']['state_sha256']}`。"
        ),
        "",
        "## reconstruction-005 的不完整 Stage 4 经验",
        "",
        (
            f"- 旧覆盖分母是 **{coverage['stage3_root_operation_count']} = "
            f"{coverage['refined_stage3_root_count']} refined + "
            f"{coverage['copied_stage3_root_count']} copied**。复制不等于深化或关系复验；"
            "旧流程没有完整 Stage 3 semantic denominator gate。"
        ),
        (
            "- 屋顶／山花未深化：`"
            + "`, `".join(str(item) for item in roof["unrefined_stage3_root_ids"])
            + "` 仍是 Stage 3 示意根；雕塑继续 PARKED，但建筑性屋面、檐口和山花必须深化。"
        ),
        (
            f"- 内柱关系：{inner['direct_lower_to_upper_shaft_contact_count']} 处上下柱身直接接触，"
            "缺少 typed inter-tier capital / support / architrave chain。"
        ),
        (
            f"- 东窗关系：{windows['obstruction_count']} 处"
            f" `{windows['obstruction_kind']}`；这不是普通正体积柱墙碰撞，"
            "因此旧碰撞门禁无法发现。"
        ),
        (
            f"- 主门依赖：石门洞 {doors['stone_opening_width_m']:.2f} × "
            f"{doors['stone_opening_height_m']:.2f} m，但旧门扇只有 "
            f"{doors['old_leaf_width_m']:.2f} × {doors['old_leaf_height_m']:.2f} m；"
            f"左右各空 {doors['side_gap_each_m']:.2f} m、顶部空 "
            f"{doors['top_gap_m']:.2f} m，且没有共享 aperture dependency。"
        ),
        (
            f"- 材质：有效渲染材质覆盖为 "
            f"{materials['effective_render_material_object_count']} / "
            f"{materials['model_object_count']}（"
            f"{materials['effective_render_material_coverage_ratio']:.0%}）；"
            f"{materials['metadata_only_material_object_count']} 个对象只有 metadata/display 表象。"
        ),
        (
            "- **旧 closure 的自证问题**：旧 COMPLETE/HOLD 只证明其局部门禁和引用闭合，"
            "没有证明全建筑深化、完整语义分母、关系复验或有效材质。"
            f"因此 `old_complete_pack_does_not_prove_full_building_detail = "
            f"{str(closure['old_complete_pack_does_not_prove_full_building_detail']).lower()}`。"
        ),
        "",
        "## 完整修复链",
        "",
        (
            f"1. 从唯一前驱 `{predecessor['run_id']}` 的 271 个 Stage 3 roots 开始，"
            "不从目录顺序、mtime 或旧 Stage 4 继续。"
        ),
        (
            f"2. 将 `{failed_policy['run_id']}` 的模型、门禁和读回结果仅编译为失败经验，"
            "用于解释遗漏，不授予 predecessor authority。"
        ),
        "3. 在 `research-007` 汇总全分母覆盖、关系诊断、视觉拓扑证据和人工决策。",
        "4. 在 `reconstruction-006` 从 exact Stage 3 编译全建筑 Stage 4 候选。",
        (
            "5. 依次通过 object/op 一一对应、全组件覆盖、屋顶／山花、内柱支承链、"
            "窗洞视线与宿主、门装配、材质、碰撞、branch leakage、3DM readback 和 evidence gates。"
        ),
        "6. 候选继续 HOLD；只有新门禁的 retained receipts 可关闭 Stage 4 obligation，canonical HEAD 不变。",
        "",
        "### 人工门决策的依赖链",
        "",
    ]
    for item in door_decision_record["dependency_chain"]:
        assert isinstance(item, Mapping)
        lines.append(
            f"{int(item['ordinal']) + 1}. `{item['role']}` → `{item['ref']}` "
            f"（authority: `{item['authority']}`）"
        )
    lines.extend(
        [
            "",
            "## 人工授权门策略与权威边界",
            "",
            (
                f"- 人工授权 ref：`{door_decision_record['authorization_ref']}`；"
                f"策略：`{authorization_scope['strategy']}`。"
            ),
            (
                "- 授权仅允许生成 **SOFT、闭合、左右对称双扇门候选**及其从同一 "
                "host-local aperture contract 派生的门框／门槛；它不是历史真值。"
            ),
            (
                f"- 候选 rough opening 为 {parameters['rough_opening_width_m']:.2f} m；"
                f"边缝 {parameters['leaf_perimeter_gap_m']:.2f} m、合缝 "
                f"{parameters['leaf_meeting_gap_m']:.2f} m 和 frame/reveal 参数均为 "
                f"`{parameters['classification']}`，`metric_authority = "
                f"{str(parameters['metric_authority']).lower()}`。"
            ),
            (
                "- `historical_truth_authorized = "
                f"{str(authority_boundary['historical_truth_authorized']).lower()}`，"
                "`visual_metric_authority = "
                f"{str(authority_boundary['visual_metric_authority']).lower()}`，"
                "`canonical_promotion_authorized = "
                f"{str(authority_boundary['canonical_promotion_authorized']).lower()}`。"
            ),
            "",
            "### invalidates_on",
            "",
        ]
    )
    for item in door_decision_record["invalidates_on"]:
        assert isinstance(item, Mapping)
        lines.append(
            f"- `{item['condition_id']}` → `{item['effect']}`"
        )
    lines.extend(
        [
            "",
            "## 外部资产与来源文档",
            "",
            (
                "- 本次修正不采用任何外部网格资产；原有 PARKED／REJECTED 资产继续不得进入几何。"
            ),
            (
                "- `research-006/workspaces/asset-rag/_外部资产来源清单.md` 仍是外部资产、"
                "未采用理由及程序化生成依据的权威来源文档；research-007 只追加修正经验和人工决策，"
                "不改写该来源历史。"
            ),
            "",
            "## 记录完整性",
            "",
            f"- 失败经验 digest：`{experience_record['record_digest']}`",
            f"- 人工门决策 digest：`{door_decision_record['record_digest']}`",
            "- 报告由两份通过 fail-closed validator 且互相 digest 绑定的记录确定性渲染。",
            "",
        ]
    )
    return "\n".join(lines)


def validate_stage4_correction_record(record: Mapping[str, object]) -> dict[str, object]:
    """Dispatch validation without accepting an unknown correction schema."""

    if not isinstance(record, Mapping):
        return {
            "schema": "ParthenonStage4CorrectionRecordValidation@1",
            "record_schema": None,
            "passed": False,
            "failures": ["record must be a mapping"],
        }
    schema = record.get("schema")
    if schema == "ParthenonStage4IncompleteAttemptExperience@1":
        return validate_incomplete_stage4_experience_record(record)
    if schema == "ParthenonStage4DoorHumanDecision@1":
        return validate_authorized_door_decision_record(record)
    return {
        "schema": "ParthenonStage4CorrectionRecordValidation@1",
        "record_schema": schema,
        "passed": False,
        "failures": ["unknown Stage 4 correction record schema"],
    }


__all__ = [
    "AUTHORIZED_DOOR_SCOPE",
    "BRANCH_ID",
    "FAILED_ATTEMPT_POLICY",
    "FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS",
    "FAILED_ATTEMPT_RUN_ID",
    "ONLY_STAGE_PREDECESSOR",
    "ONLY_STAGE_PREDECESSOR_RUN_ID",
    "PROJECT_ID",
    "ParthenonStage4CorrectionRecordError",
    "REQUIRED_INVALIDATES_ON",
    "TARGET_RECONSTRUCTION_RUN_ID",
    "TARGET_RESEARCH_RUN_ID",
    "VISUAL_MANIFEST_REF",
    "compile_authorized_door_decision_record",
    "compile_incomplete_stage4_experience_record",
    "compute_correction_record_digest",
    "render_stage4_correction_report",
    "validate_authorized_door_decision_record",
    "validate_incomplete_stage4_experience_record",
    "validate_stage4_correction_record",
]
