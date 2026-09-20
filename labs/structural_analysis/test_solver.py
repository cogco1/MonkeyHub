"""Real optional backend, independent formulas, and cold P036 history replay."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from archflow.project.refs import ProjectRecordRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord, apply_state_record_operator, compile_component_edit
from labs.structural_analysis.analysis import compile_analysis, readiness
from labs.structural_analysis.demo import _read_artifact, run_demo, replay, sensitivity_edit
from labs.structural_analysis.fixture import MEMBERS, enrich, geometry_projection, initial_record
from labs.structural_analysis.solver import hand_solution, solve


class SolverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = FilesystemProjectRepository.initialize(self.root / "fixture", project_id="structural-demo", initial_state={})
        self.record = initial_record().bound_to(self.repo.create_run("analysis"))
        for milestone in (2, 3, 4):
            self.record = apply_state_record_operator(self.record, enrich(self.record, milestone))

    def spec(self, record=None):
        record = self.record if record is None else record
        return compile_analysis(record, MEMBERS, expected_run=record.run_ref, expected_digest=record.digest)

    def close(self, actual, expected, absolute=1e-12):
        self.assertAlmostEqual(actual, expected, delta=max(absolute, abs(expected)*1e-10))

    def verify_hand_solution(self, spec):
        result, hand = solve(spec), hand_solution(spec)
        self.assertEqual(result["status"], "solved", result.get("reason"))
        self.assertEqual(result["source"], spec.source)
        for i in range(3):
            self.close(result["nodes"][f"base-{i}"]["reaction_N"][1], hand["base_reaction_y_N"][i])
            self.close(result["nodes"][f"top-{i}"]["displacement_m"][1], hand["top_dy_m"][i])
            self.close(abs(result["members"][f"column-{i}"]["axial_mid_N"]), hand["base_reaction_y_N"][i])
        for i in range(2):
            beam = result["members"][f"beam-{i}"]
            self.close(beam["midpoint_local_dy_m"], hand["beam_mid_dy_m"][i])
            self.close(beam["midpoint_relative_dy_m"], hand["beam_mid_relative_dy_m"][i])
            self.close(beam["moment_local_z_Nm"][1], hand["beam_mid_moment_z_Nm"][i])
            self.close(beam["moment_local_z_Nm"][0], 0, absolute=1e-8)
            self.close(beam["moment_local_z_Nm"][2], 0, absolute=1e-8)
            self.close(beam["shear_local_y_N"][0], 5000)
            self.close(beam["shear_local_y_N"][1], -5000)
        self.close(sum(n["reaction_N"][1] for n in result["nodes"].values()), 20000)
        return result

    def test_actual_frame_and_both_sensitivities_against_independent_solution(self):
        before = self.record.to_dict()
        baseline = self.verify_hand_solution(self.spec())
        half = apply_state_record_operator(self.record, sensitivity_edit(self.record, "half-E"))
        section = apply_state_record_operator(self.record, sensitivity_edit(self.record, "double-beam-Iz"))
        half_result = self.verify_hand_solution(self.spec(half))
        section_result = self.verify_hand_solution(self.spec(section))
        for revised in (half, section):
            self.assertEqual(geometry_projection(revised), geometry_projection(self.record))
            self.assertEqual(revised.entity("roof"), self.record.entity("roof"))
            self.assertEqual([e.entity_id for e in revised.entities], [e.entity_id for e in self.record.entities])
            self.assertNotEqual(revised.digest, self.record.digest)
        for i in range(2):
            key = f"beam-{i}"
            self.close(half_result["members"][key]["midpoint_local_dy_m"], 2*baseline["members"][key]["midpoint_local_dy_m"])
            self.close(section_result["members"][key]["midpoint_relative_dy_m"], baseline["members"][key]["midpoint_relative_dy_m"]/2)
        self.assertEqual(before, self.record.to_dict())
        self.assertEqual(baseline["member_provenance"]["beam-0"]["structural"]["physical_profile"],
                         self.record.entity("beam-0").fields["structural"]["physical_profile"])

    def test_supplied_but_unstable_supports_are_solver_failure_not_unknown(self):
        # Enough information exists to run, but only out-of-plane restraints
        # leave a rigid-body mode. The actual solver must diagnose it.
        edits = []
        for i in range(3):
            entity = self.record.entity(f"column-{i}")
            fields = deepcopy(entity.fields)
            fields["structural"]["supports"]["i"] = [False, False, True, True, True, False]
            edits.append(replace(entity, fields=fields))
        unstable = apply_state_record_operator(self.record, compile_component_edit(self.record, entities=tuple(edits)))
        self.assertTrue(readiness(unstable, MEMBERS, expected_run=unstable.run_ref, expected_digest=unstable.digest).ready)
        result = solve(self.spec(unstable))
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("nodes", result)
        self.assertRegex(result["reason"].lower(), "singular|unstable")
        with self.assertRaises(ValueError):
            hand_solution(self.spec(unstable))

    def test_reopen_replays_edits_and_results_in_new_python_process(self):
        project = self.root / "persistent"
        report = run_demo(project)
        report_path = self.root / "report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        fresh = subprocess.run([sys.executable, "-m", "labs.structural_analysis.demo", "--project-dir", str(project),
                                "--replay-report", str(report_path)], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(fresh.stdout), report["cold_replay"])
        self.assertEqual([r["ready"] for r in report["cold_replay"]], [False, False, False, True, True, True, False])
        repo = FilesystemProjectRepository.open(project)
        versions = {e["label"]: StateRecord.from_dict(repo.load_json(ProjectRecordRef.from_dict(e["record_ref"])))
                    for e in report["revisions"]}
        self.assertEqual(versions["geometry"].entity("beam-0").fields["semantic"], {"status": "unknown"})
        self.assertIn("role.load_transfer", versions["physical"].entity("beam-0").fields["semantic"]["roles"])
        self.assertEqual(versions["withdraw-role"].entity("beam-0").fields["semantic"]["status"], "unknown")
        self.assertEqual(repo.read_head().to_dict(), report["canonical_head"])
        self.assertEqual(repo.read_design_branches(), {})
        physical = next(e for e in report["revisions"] if e["label"] == "physical")
        evidence = _read_artifact(repo, physical["evidence_ref"])
        self.assertIn("roof", evidence["unchanged_entities"])
        self.assertEqual(evidence["result"]["status"], "solved")

    def test_replay_rejects_wrong_revision_and_tampered_artifact(self):
        project = self.root / "retained"
        report = run_demo(project)
        wrong = deepcopy(report)
        wrong["revisions"][3]["record_ref"] = wrong["revisions"][2]["record_ref"]
        with self.assertRaisesRegex(ValueError, "source revision mismatch"):
            replay(project, wrong)
        wrong = deepcopy(report)
        wrong["revisions"][3]["evidence_ref"]["sha256"] = "0"*64
        with self.assertRaisesRegex(Exception, "digest mismatch"):
            replay(project, wrong)


if __name__ == "__main__":
    unittest.main()
