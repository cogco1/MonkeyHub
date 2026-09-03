from __future__ import annotations

import copy
import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from http.server import ThreadingHTTPServer

from archive.tools import state_tree_viewer as viewer


PROJECT_ID = "pantheon-test"


def _digest(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _write_content_addressed_json(
    project_root: Path,
    relative_directory: str,
    stem: str,
    payload: object,
) -> tuple[str, str]:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sha256 = hashlib.sha256(raw).hexdigest()
    relative_path = Path(relative_directory) / f"{stem}-{sha256}.json"
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return relative_path.as_posix(), sha256


def _record_ref(relative_path: str, sha256: str) -> dict[str, object]:
    return {
        "project_id": PROJECT_ID,
        "relative_path": relative_path,
        "sha256": sha256,
        "media_type": "application/json",
    }


def _artifact_ref(relative_path: str, sha256: str) -> dict[str, object]:
    return {
        "project_id": PROJECT_ID,
        "artifact_id": "pantheon-model",
        "relative_path": relative_path,
        "sha256": sha256,
        "media_type": "model/vnd.rhino.3dm",
    }


def _stage_pack(
    *,
    scope_ref: dict[str, object] | None = None,
    contract_ref: dict[str, object] | None = None,
) -> dict[str, object]:
    program_digest = _digest("stage-program")
    scope = scope_ref or _record_ref(
        "runs/stage-001/branches/branch-a/records/scope.json",
        _digest("scope"),
    )
    contract = contract_ref or _record_ref(
        "runs/stage-001/records/contract.json", _digest("contract")
    )
    return {
        "schema": "StageEvidencePack@1",
        "project_id": PROJECT_ID,
        "run_id": "stage-001",
        "base": {
            "project_id": PROJECT_ID,
            "version": 0,
            "state_sha256": _digest("canonical-state"),
        },
        "branch": {"branch_id": "branch-a", "epoch": 0},
        "scope_ref": scope,
        "stage": {
            "stage_id": "schematic-design",
            "stage_index": 0,
            "revision": 1,
        },
        "program_digest": program_digest,
        "contract_ref": contract,
        "predecessor": None,
        "supersedes_pack_ref": None,
        "bindings": [
            {
                "schema": "StageEvidenceBinding@1",
                "role": "stage_gate",
                "ref": scope,
            }
        ],
        "artifacts": [
            {
                "schema": "StageArtifactBinding@1",
                "role": "cad_model",
                "ref": _artifact_ref(
                    "objects/sha256/"
                    + _digest("synthetic-3dm")
                    + ".3dm",
                    _digest("synthetic-3dm"),
                ),
                "program_digest": program_digest,
            }
        ],
        "gaps": [
            {
                "schema": "StageEvidenceGap@1",
                "gap_id": "formal-review-missing",
                "kind": "missing_evidence",
                "severity": "blocking",
                "description": "Formal review is not closed.",
                "decision_refs": [],
                "remediation": "Create the formal review record.",
            }
        ],
        "closure": {
            "schema": "StageClosureSummary@1",
            "evidence_sufficient": False,
            "dependencies_closed": False,
            "hard_gates_passed": False,
            "stage_ready": False,
            "model_artifact_current": True,
            "closed": False,
        },
        "compilation_status": "INCOMPLETE",
        "selection_authority": False,
        "evidence_authority": False,
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
    }


def _inspection(file_bytes: bytes) -> dict[str, object]:
    return {
        "schema": "ThreeDmInspectionSummary@1",
        "file_sha256": _digest(file_bytes),
        "file_bytes": len(file_bytes),
        "three_dm_version": 8,
        "archive_version": 80,
        "units": {"name": "Inches"},
        "layers": [{"index": 0, "name": "Default"}],
        "object_count": 59,
        "top_level_object_count": 59,
        "instance_definition_member_count": 0,
        "object_counts_by_type": {"Brep": 59},
        "object_counts_by_layer": [{"layer": "Default", "count": 59}],
        "instance_definitions": [],
        "instance_references": [],
        "document_user_strings": [],
        "object_user_strings": [],
        "aggregate_bbox": {
            "min": [-10.0, -10.0, 0.0],
            "max": [10.0, 10.0, 20.0],
        },
        "bbox_contributing_geometry_count": 59,
        "read_only": True,
        "rhino_process_started": False,
    }


def _pantheon_snapshot(project_root: Path) -> tuple[dict[str, object], bytes, str]:
    records = project_root / "runs" / "reconstruction-006" / "records"
    review_path = records / "review.json"
    review_sha = _write_json(
        review_path,
        {"schema": "SyntheticStageReview@1", "checks_status": "pass"},
    )
    manifest_path = records / "candidate-manifest.json"
    manifest_sha = _write_json(
        manifest_path,
        {"schema": "SyntheticCandidateManifest@1", "disposition": "HOLD"},
    )
    plan_path = records / "detail-plan.json"
    plan_sha = _write_json(plan_path, {"schema": "SyntheticDetailPlan@1"})
    execution_path = (
        project_root
        / "runs"
        / "reconstruction-004"
        / "records"
        / "execution.json"
    )
    execution_sha = _write_json(
        execution_path,
        {"schema": "SyntheticExecution@1", "run_id": "reconstruction-004"},
    )

    model_relative = "runs/reconstruction-004/objects/pantheon.3dm"
    model_bytes = b"synthetic-rhino-3dm-bytes"
    model_path = project_root / Path(model_relative)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(model_bytes)

    current_program = _digest("reconstruction-006-program")
    stale_program = _digest("reconstruction-004-program")
    snapshot = {
        "schema": "PantheonStageProgressSnapshot@1",
        "project_id": PROJECT_ID,
        "generated_at": "2026-08-29T00:00:00Z",
        "canonical_head": {
            "version": 0,
            "state_sha256": _digest("canonical-state"),
        },
        "current_stage_run_id": "reconstruction-006",
        "stages": [
            {
                "stage": 6,
                "label": "reconstruction-006",
                "program_digest": current_program,
                "predecessor_program_digest": _digest(
                    "reconstruction-005-program"
                ),
                "disposition": "HOLD",
                "checks_status": "pass",
                "accepted_archive_created": False,
                "formal_stage_pack_status": "missing",
                "hold_reasons": ["model.unit_mismatch", "model.stale_program"],
                "review_ref": _record_ref(
                    review_path.relative_to(project_root).as_posix(), review_sha
                ),
            }
        ],
        "candidate_manifest": {
            "disposition": "HOLD",
            "ref": _record_ref(
                manifest_path.relative_to(project_root).as_posix(), manifest_sha
            ),
        },
        "detail_candidate": {
            "authority_state": "agent-proposed",
            "branch_identity": "detail-agent-proposal",
            "plan_ref": _record_ref(
                plan_path.relative_to(project_root).as_posix(), plan_sha
            ),
        },
        "expected_model_workspaces": [
            {
                "schema": "PantheonExpectedModelWorkspace@1",
                "run_id": "reconstruction-006",
                "model_relative_path": (
                    "runs/reconstruction-006/objects/pantheon.3dm"
                ),
                "model_exists": False,
            }
        ],
        "model": {
            "run_id": "reconstruction-004",
            "model_relative_path": model_relative,
            "execution_ref": _record_ref(
                execution_path.relative_to(project_root).as_posix(), execution_sha
            ),
            "inspection": _inspection(model_bytes),
        },
        "model_alignment": {
            "schema": "StageModelAlignment@1",
            "status": "BLOCKED",
            "reason_codes": ["model.unit_mismatch", "model.stale_program"],
            "expected_unit": "Meters",
            "actual_unit": "Inches",
            "unit_gate": False,
            "program_current_gate": False,
            "execution_program_digest": stale_program,
            "current_program_digest": current_program,
            "stage_acceptance_authority": False,
            "canonical_write_authority": False,
        },
        "formal_closure": {
            "schema": "StageClosureInventory@1",
            "formal_closure_present": False,
            "record_counts": {
                "branch_scope": 0,
                "evidence_sufficiency": 0,
                "stage_convergence": 0,
                "stage_evidence_pack": 0,
            },
            "note": "No formal P079/P080/StageEvidencePack records exist.",
        },
        "view_authority": False,
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
    }
    return snapshot, model_bytes, review_sha


class StageProgressViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_real_stage_evidence_pack_shape_is_strict_and_unknown_schema_fails(self):
        pack = _stage_pack()

        guarded = viewer._fallback_validate_stage_pack(pack)
        adapted = viewer.adapt_panel_input(pack)

        self.assertEqual("StageEvidencePack@1", guarded["schema"])
        self.assertEqual("schematic-design", adapted["pack"]["identity"]["stage_id"])
        self.assertEqual(
            "objects/sha256/" + _digest("synthetic-3dm") + ".3dm",
            adapted["pack"]["artifact_ref"]["relative_path"],
        )

        drifted = copy.deepcopy(pack)
        drifted["unexpected_completion_hint"] = True
        with self.assertRaisesRegex(viewer.StagePanelError, "schema drifted"):
            viewer._fallback_validate_stage_pack(drifted)

        with self.assertRaisesRegex(
            viewer.UnsupportedStageSchema,
            "unsupported Stage panel snapshot schema",
        ):
            viewer.adapt_panel_input({"schema": "StageEvidencePack@2"})

    def test_pantheon_snapshot_keeps_hold_blocked_despite_passing_local_checks(self):
        project_root = self.root / "project"
        project_root.mkdir()
        snapshot, model_bytes, _ = _pantheon_snapshot(project_root)
        snapshot_path = self.root / "pantheon-stage-progress.json"
        _write_json(snapshot_path, snapshot)
        inspection = _inspection(model_bytes)

        with patch.object(
            viewer,
            "_direct_three_dm_inspection",
            return_value=("direct", inspection),
        ) as direct_inspection:
            result = viewer.load_stage_panel_view(
                snapshot_path,
                artifact_root=project_root,
            )

        direct_inspection.assert_called_once_with(
            (project_root / "runs/reconstruction-004/objects/pantheon.3dm").resolve()
        )
        current_stage = result["panel"]["stages"][-1]
        self.assertEqual("pass", current_stage["checks_status"])
        self.assertEqual("HOLD", current_stage["disposition"])
        self.assertEqual("BLOCKED", result["panel"]["model_alignment"]["status"])
        self.assertEqual(
            ["model.unit_mismatch", "model.stale_program"],
            result["panel"]["model_alignment"]["reason_codes"],
        )
        self.assertEqual("blocked", result["verification"]["status"])
        self.assertEqual("CANDIDATE HOLD / BLOCKED", result["verification"]["label"])
        self.assertIn("model.unit_mismatch", result["verification"]["message"])
        self.assertIn("model.stale_program", result["verification"]["message"])
        self.assertFalse(result["three_dm"]["verified_stage_artifact"])
        self.assertFalse(result["three_dm"]["stage_accepted"])
        self.assertEqual("direct", result["three_dm"]["inspection_mode"])
        checks = {
            item["check_id"]: item for item in result["three_dm"]["checks"]
        }
        self.assertEqual("fail", checks["cad_binding_program_digest"]["status"])
        self.assertEqual("fail", checks["model_units"]["status"])
        self.assertEqual("fail", checks["snapshot_pack_digest"]["status"])
        self.assertEqual("inch", checks["model_units"]["actual"])
        self.assertEqual("meter", checks["model_units"]["expected"])

    def test_observed_lineage_is_not_formal_rag_sufficiency(self):
        project_root = self.root / "project"
        project_root.mkdir()
        snapshot, model_bytes, _ = _pantheon_snapshot(project_root)
        snapshot["detail_candidate"] = None

        aligned_ids = [f"aligned-{index}" for index in range(7)]
        misbound_ids = [f"misbound-{index}" for index in range(7)]
        uncertainty_only_ids = [f"uncertainty-only-{index}" for index in range(7)]
        aligned_relative, aligned_sha = _write_content_addressed_json(
            project_root,
            "runs/research-001/records",
            "precedent-adoption-aligned",
            {
                "schema": "PrecedentAdoption@1",
                "adoption_id": "aligned-adoption",
                "facts": [
                    {
                        "schema": "PrecedentFact@1",
                        "fact_id": "aligned-fact",
                        "decision_refs": [
                            f"declaration:{field_id}" for field_id in aligned_ids
                        ],
                    }
                ],
            },
        )
        misbound_relative, _ = _write_content_addressed_json(
            project_root,
            "runs/research-001/records",
            "precedent-adoption-misbound",
            {
                "schema": "PrecedentAdoption@1",
                "adoption_id": "misbound-adoption",
                "facts": [
                    {
                        "schema": "PrecedentFact@1",
                        "fact_id": "misbound-fact",
                        "decision_refs": ["declaration:not-any-test-field"],
                    }
                ],
            },
        )
        aligned_uri = f"project://{PROJECT_ID}/{aligned_relative}"
        misbound_uri = f"project://{PROJECT_ID}/{misbound_relative}"
        fields = []
        for index, field_id in enumerate(aligned_ids):
            sources = [aligned_uri]
            if index < 4:
                sources.append("uncertainty:widened-range")
            fields.append(
                {
                    "field_id": field_id,
                    "source_refs": sources,
                    "statement": "exact adoption decision ref is present",
                }
            )
        for field_id in misbound_ids:
            fields.append(
                {
                    "field_id": field_id,
                    "source_refs": [misbound_uri],
                    "statement": "adoption file is present but decision ref differs",
                }
            )
        for field_id in uncertainty_only_ids:
            fields.append(
                {
                    "field_id": field_id,
                    "source_refs": ["uncertainty:no-adopted-source"],
                    "statement": "uncertainty retained without adoption",
                }
            )
        fields.append(
            {
                "field_id": "context-only",
                "source_refs": ["context:coarse-brief"],
                "statement": "context only",
            }
        )
        review_ref = snapshot["stages"][0]["review_ref"]
        review_path = project_root / review_ref["relative_path"]
        review_sha = _write_json(
            review_path,
            {
                "schema": "P069CandidateStageReview@1",
                "project_id": PROJECT_ID,
                "run_id": snapshot["current_stage_run_id"],
                "stage": snapshot["stages"][0]["stage"],
                "program_digest": snapshot["stages"][0]["program_digest"],
                "predecessor_program_digest": snapshot["stages"][0][
                    "predecessor_program_digest"
                ],
                "program_ref": None,
                "evidence_proposal_ref": None,
                "contract": {
                    "schema": "StageDeclarationContract@1",
                    "fields": fields,
                },
                "realized_declaration_values": {
                    field["field_id"]: index for index, field in enumerate(fields)
                },
                "checks_status": "pass",
                "disposition": "HOLD",
                "hold_reasons": ["formal-scope-and-sufficiency-missing"],
                "exact_predecessor_compiled": True,
                "canonical_write_authority": False,
            },
        )
        snapshot["stages"][0]["review_ref"] = _record_ref(
            review_ref["relative_path"], review_sha
        )
        snapshot_path = self.root / "lineage-stage-progress.json"
        _write_json(snapshot_path, snapshot)

        with patch.object(
            viewer,
            "_direct_three_dm_inspection",
            return_value=("direct", _inspection(model_bytes)),
        ):
            result = viewer.load_stage_panel_view(
                snapshot_path,
                artifact_root=project_root,
            )

        rag = result["panel"]["web_rag"]
        observed = rag["observed_declaration_lineage"]
        self.assertEqual(22, observed["decision_count"])
        self.assertEqual(14, observed["adoption_reference_count"])
        self.assertEqual(7, observed["decision_aligned_adoption_count"])
        self.assertEqual(7, observed["misbound_adoption_count"])
        self.assertEqual(11, observed["uncertainty_present_count"])
        self.assertEqual(7, observed["uncertainty_only_count"])
        self.assertEqual(1, observed["context_only_count"])
        self.assertEqual(0, observed["source_less_count"])
        self.assertFalse(observed["evidence_sufficiency_claim"])
        self.assertFalse(observed["stage_pass_claim"])
        decisions_by_id = {
            item["decision_id"]: item for item in observed["decisions"]
        }
        expected_alignments = {
            **{f"stage-6:{field_id}": "aligned" for field_id in aligned_ids},
            **{f"stage-6:{field_id}": "mismatched" for field_id in misbound_ids},
            **{
                f"stage-6:{field_id}": "none"
                for field_id in uncertainty_only_ids
            },
            "stage-6:context-only": "none",
        }
        self.assertEqual(set(expected_alignments), set(decisions_by_id))
        for decision_id, expected_alignment in expected_alignments.items():
            with self.subTest(decision_id=decision_id):
                decision = decisions_by_id[decision_id]
                self.assertEqual(
                    expected_alignment,
                    decision["adoption_alignment"],
                )
                self.assertEqual(
                    expected_alignment != "none",
                    decision["adoption_reference_present"],
                )
        aligned = decisions_by_id["stage-6:aligned-0"]
        self.assertTrue(aligned["adoption_reference_present"])
        self.assertEqual("aligned", aligned["adoption_alignment"])
        misbound = decisions_by_id["stage-6:misbound-0"]
        self.assertTrue(misbound["adoption_reference_present"])
        self.assertEqual("mismatched", misbound["adoption_alignment"])
        uncertainty_only = decisions_by_id["stage-6:uncertainty-only-0"]
        self.assertFalse(uncertainty_only["adoption_reference_present"])
        self.assertEqual("none", uncertainty_only["adoption_alignment"])
        self.assertTrue(uncertainty_only["uncertainty_only"])

        formal = rag["formal_branch_conditioned_rag"]
        self.assertEqual("UNAVAILABLE", formal["status"])
        self.assertEqual("NOT_COMPILED", formal["compilation_status"])
        self.assertEqual(0, formal["branch_scope_record_count"])
        self.assertEqual(0, formal["evidence_sufficiency_record_count"])
        self.assertIsNone(formal["coverage"])
        self.assertFalse(formal["evidence_sufficiency_claim"])
        self.assertFalse(result["pack"]["closure"]["evidence_sufficient"])
        self.assertNotEqual("accepted", result["verification"]["status"])

        source = viewer.load_stage_source_record(
            snapshot_path,
            aligned_sha,
            project_root=project_root,
        )
        self.assertTrue(source["digest_verified"])
        self.assertEqual("PrecedentAdoption@1", source["payload"]["schema"])

    def test_source_record_requires_containment_and_matching_sha256(self):
        project_root = self.root / "project"
        project_root.mkdir()
        source_path = project_root / "records" / "source.json"
        source_sha = _write_json(
            source_path,
            {"schema": "SyntheticSource@1", "value": "verified"},
        )
        input_path = self.root / "stage-pack.json"
        _write_json(
            input_path,
            _stage_pack(
                scope_ref=_record_ref("records/source.json", source_sha),
            ),
        )

        loaded = viewer.load_stage_source_record(
            input_path,
            source_sha,
            project_root=project_root,
        )
        self.assertTrue(loaded["digest_verified"])
        self.assertEqual("verified", loaded["payload"]["value"])
        self.assertEqual(source_path.resolve(), Path(loaded["resolved_local_path"]))

        outside_path = self.root / "outside.json"
        outside_sha = _write_json(
            outside_path,
            {"schema": "SyntheticSource@1", "value": "outside"},
        )
        escaping_input = self.root / "escaping-stage-pack.json"
        _write_json(
            escaping_input,
            _stage_pack(
                scope_ref=_record_ref("../outside.json", outside_sha),
            ),
        )
        with self.assertRaisesRegex(
            viewer.StagePanelError,
            "escapes the explicit project root",
        ):
            viewer.load_stage_source_record(
                escaping_input,
                outside_sha,
                project_root=project_root,
            )

        wrong_sha = "f" * 64
        mismatched_input = self.root / "mismatched-stage-pack.json"
        _write_json(
            mismatched_input,
            _stage_pack(
                scope_ref=_record_ref("records/source.json", wrong_sha),
            ),
        )
        with self.assertRaisesRegex(viewer.StagePanelError, "digest mismatch"):
            viewer.load_stage_source_record(
                mismatched_input,
                wrong_sha,
                project_root=project_root,
            )

    def test_http_stage_endpoints_are_read_only(self):
        project_root = self.root / "project"
        project_root.mkdir()
        snapshot, model_bytes, review_sha = _pantheon_snapshot(project_root)
        snapshot_path = self.root / "pantheon-stage-progress.json"
        _write_json(snapshot_path, snapshot)
        source = viewer.StagePackSource(snapshot_path)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            viewer.make_handler(
                None,
                stage_source=source,
                artifact_root=project_root,
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=5
        )
        try:
            connection.request("GET", "/api/stage/packs")
            response = connection.getresponse()
            packs = json.loads(response.read())
            self.assertEqual(200, response.status)
            self.assertTrue(packs[0]["supported"])

            with patch.object(
                viewer,
                "_direct_three_dm_inspection",
                return_value=("direct", _inspection(model_bytes)),
            ):
                connection.request(
                    "GET", f"/api/stage/view?pack={snapshot_path.name}"
                )
                response = connection.getresponse()
                view = json.loads(response.read())
            self.assertEqual(200, response.status)
            self.assertEqual("blocked", view["verification"]["status"])

            connection.request(
                "GET",
                (
                    f"/api/stage/source?pack={snapshot_path.name}"
                    f"&sha256={review_sha}"
                ),
            )
            response = connection.getresponse()
            source_view = json.loads(response.read())
            self.assertEqual(200, response.status)
            self.assertTrue(source_view["digest_verified"])

            connection.request("POST", "/api/stage/view", body=b"{}")
            response = connection.getresponse()
            response.read()
            self.assertEqual(405, response.status)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
