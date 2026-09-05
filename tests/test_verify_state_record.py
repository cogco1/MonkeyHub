"""``tools/verify_state_record`` runs its equivalence harness in the reference run's phase (P112).

The reference run states the phase its stage ran in (``RunnerRunReceipt@3``
``stage.phase``). The verifier already read the reference state in that phase
for the identity check; the equivalence run it opens beside it has to be
projected and enveloped in the same phase, or a schematic reference with a
schematic-only seat pack passes identity and is then refused by the runner
("a producing seat is not admitted in the envelope phase"). An older receipt
that names no phase is read in design_development, which is the phase every
projection had when it was written.

The project, its record, its frozen workflow and the reference run are the
runner suite's own builders; nothing here is a second fixture framework.
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from archflow.project.record_kinds import RUNNER_RUN_RECEIPT, STATE_RECORD_EQUIVALENCE
from archflow.project.refs import parse_record_file_name
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.stage_workflow import DesignPhase
from tests.test_project_runner import DECLARED_LIVE_IDENTITY, _ladder_project, _run_opened_stage, _seats
from tools import verify_state_record
from tools.open_stage_run import open_stage_run

REFERENCE_RUN = "stage-0-001"
EQUIVALENCE_RUN = "equivalence-001"


def _reference_project(root: Path, phase: DesignPhase) -> tuple[FilesystemProjectRepository, dict]:
    """A temporary P036 project whose WIP record ran one stage in ``phase``, with a seat pack admitted only in that phase.

    The seat pack is the same seats the runner suite hands the reference run,
    written the way the verifier's own readers expect them (a seat as
    ``tools.run_project._seat`` reads it, the declared provider identity as the
    identity's constructor fields); the reference receipt is what the real
    runner retained.
    """

    repository, workflow_ref = _ladder_project(root, (phase,))
    seat_pack = repository.layout.seat_pack
    seat_pack.parent.mkdir(parents=True, exist_ok=True)
    seat_pack.write_text(json.dumps({
        "schema": "RunnerSeats@1", "commitment_ref": "commitment:demo-survey", "provider_identity": dataclasses.asdict(DECLARED_LIVE_IDENTITY),
        "seats": [seat.to_dict() for seat in _seats(phase)],
    }), encoding="utf-8")
    opened = open_stage_run(project_root=root, workflow_uri=workflow_ref, stage_index=0, run_id=REFERENCE_RUN)
    reference = _run_opened_stage(root, workflow_ref, opened)
    assert reference["stage"]["phase"] == phase.value and reference["closure_status"] == "SATISFIED", reference["seat_results"]
    return repository, reference


def _only(records_dir: Path, record_kind: str) -> Path:
    paths = [path for path in records_dir.glob(f"{record_kind}-*.json") if parse_record_file_name(path.name)[0] == record_kind]
    assert len(paths) == 1, paths
    return paths[0]


def _verify(root: Path, *extra: str) -> tuple[int, str]:
    """The command itself, as ``tools/verify_state_record.py --project ... --reference-run ... --run ...`` would run it."""

    argv = ["verify_state_record.py", "--project", str(root), "--reference-run", REFERENCE_RUN, "--run", EQUIVALENCE_RUN, *extra]
    out = io.StringIO()
    with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
        code = verify_state_record.main()
    return code, out.getvalue()


def _invoke(root: Path, *extra: str) -> tuple[int, str, str]:
    """``_verify`` with the parser's own refusals caught: (exit code, stdout, stderr)."""

    argv = ["verify_state_record.py", "--project", str(root), "--reference-run", REFERENCE_RUN, "--run", EQUIVALENCE_RUN, *extra]
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = verify_state_record.main()
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def _fake_run(seen: list):
    """A ``run_project`` stand-in that records the options it was handed and answers an empty, comparable receipt."""

    def capture(repository, *, run, stage_guard, record, seats, options):
        seen.append(options)
        return {"seat_results": [], "state_record_ref": "project://demo/runs/equivalence-001/records/state-record-" + "0" * 64 + ".json",
                "receipt_ref": "project://demo/runs/equivalence-001/records/runner-run-receipt-" + "0" * 64 + ".json"}

    return capture


class VerifyStateRecordPhaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "demo"

    def _equivalence(self, repository: FilesystemProjectRepository) -> tuple[dict, dict]:
        records = Path(repository.layout.run(EQUIVALENCE_RUN).records)
        equivalence = json.loads(_only(records, STATE_RECORD_EQUIVALENCE).read_text(encoding="utf-8"))
        receipt = json.loads(_only(records, RUNNER_RUN_RECEIPT).read_text(encoding="utf-8"))
        return equivalence, receipt

    def test_a_schematic_reference_with_a_schematic_only_seat_pack_is_verified_in_its_own_phase(self) -> None:
        repository, reference = _reference_project(self.root, DesignPhase.SCHEMATIC_DESIGN)

        code, printed = _verify(self.root)                       # used to raise "a producing seat is not admitted in the envelope phase"

        self.assertEqual(code, 0, printed)
        equivalence, receipt = self._equivalence(repository)
        self.assertTrue(equivalence["state_digest_equal"], equivalence)
        self.assertTrue(equivalence["geometry_equal"], equivalence)
        self.assertEqual(equivalence["reference_phase"], "schematic_design")
        self.assertEqual(equivalence["reference_state_digest"], reference["design_state_digest"])
        # the harness stage ran in the reference phase, under the verifier's own harness workflow, and exported nothing
        self.assertEqual(receipt["stage"]["phase"], "schematic_design")
        self.assertEqual(receipt["stage"]["stage_id"], "equivalence-check")
        self.assertTrue(receipt["workflow_is_harness"])
        self.assertEqual(receipt["closure_status"], "SATISFIED", receipt["seat_results"])
        self.assertEqual([s["status"] for s in receipt["seat_results"] if s["seat_id"] != "seat-review"], ["proposal_accepted", "proposal_accepted"])
        self.assertFalse(any(s.get("cad") for s in receipt["seat_results"]), receipt["seat_results"])
        self.assertEqual([], list(Path(repository.layout.run(EQUIVALENCE_RUN).workspaces).glob("cad-equivalence-check-*")))
        # the same record, projected in the same phase, in two runs: one content identity, one digest per binding
        self.assertEqual(receipt["state_record_digest"], reference["state_record_digest"])
        self.assertNotEqual(receipt["design_state_digest"], reference["design_state_digest"])
        self.assertEqual(sorted(c["seat_id"] for c in equivalence["seats"] if c["compared"]), ["seat-envelope", "seat-structure"])

    def test_a_design_development_reference_still_verifies_in_design_development(self) -> None:
        repository, reference = _reference_project(self.root, DesignPhase.DESIGN_DEVELOPMENT)

        code, printed = _verify(self.root)

        self.assertEqual(code, 0, printed)
        equivalence, receipt = self._equivalence(repository)
        self.assertTrue(equivalence["state_digest_equal"] and equivalence["geometry_equal"], equivalence)
        self.assertEqual(equivalence["reference_phase"], "design_development")
        self.assertEqual(receipt["stage"]["phase"], "design_development")
        self.assertEqual(equivalence["reference_state_digest"], reference["design_state_digest"])

    def test_a_retained_receipt_that_names_no_phase_is_read_in_design_development(self) -> None:
        """RunnerRunReceipt@2 predates ``stage.phase``; every run it describes was projected in design_development."""

        repository, reference = _reference_project(self.root, DesignPhase.DESIGN_DEVELOPMENT)
        receipt_path = _only(Path(repository.layout.run(REFERENCE_RUN).records), RUNNER_RUN_RECEIPT)
        legacy = json.loads(receipt_path.read_text(encoding="utf-8"))
        legacy["schema"] = "RunnerRunReceipt@2"
        legacy["stage"] = {k: v for k, v in legacy["stage"].items() if k != "phase"}
        receipt_path.write_text(json.dumps(legacy), encoding="utf-8")

        code, printed = _verify(self.root)

        self.assertEqual(code, 0, printed)
        equivalence, receipt = self._equivalence(repository)
        self.assertTrue(equivalence["state_digest_equal"] and equivalence["geometry_equal"], equivalence)
        self.assertEqual(equivalence["reference_phase"], "design_development")
        self.assertEqual(receipt["stage"]["phase"], "design_development")
        self.assertEqual(equivalence["reference_state_digest"], reference["design_state_digest"])


class VerifyStateRecordCadBackendTests(unittest.TestCase):
    """``--export`` goes to OCCT unless ``--cad-backend rhino`` is named, as ``tools/run_project.py`` does; Rhino is never reached here."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "demo"
        self.repository, _ = _reference_project(self.root, DesignPhase.DESIGN_DEVELOPMENT)
        self.seen: list = []
        patcher = mock.patch.object(verify_state_record, "run_project", side_effect=_fake_run(self.seen))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_export_selects_occt_by_default_and_rhino_only_when_named(self) -> None:
        code, printed, _ = _invoke(self.root, "--export")
        self.assertEqual(code, 0, printed)
        code, printed, _ = _invoke(self.root, "--export", "--cad-backend", "rhino", "--powershell", "pwsh.exe")
        self.assertEqual(code, 0, printed)
        code, printed, _ = _invoke(self.root)
        self.assertEqual(code, 0, printed)
        self.assertEqual([(o.export, o.cad_backend) for o in self.seen], [(True, "occt"), (True, "rhino"), (False, "occt")])
        occt, rhino, plain = self.seen
        self.assertEqual(occt.workspace_root, self.root.resolve() / "runs" / EQUIVALENCE_RUN / "workspaces")
        self.assertFalse(occt.patch_oracle)
        self.assertEqual(rhino.powershell, Path("pwsh.exe"))
        self.assertIsNone(plain.workspace_root)
        # the export workspaces are prepared per producing seat, exactly as before
        self.assertEqual(sorted(p.name for p in occt.workspace_root.iterdir()), ["cad-equivalence-check-seat-envelope", "cad-equivalence-check-seat-structure"])
        # the phase semantics are untouched by the backend choice: the harness state is the reference phase
        self.assertEqual({o.branch_id for o in self.seen}, {"runner-v1"})

    def test_a_backend_nobody_implements_is_refused_before_anything_runs(self) -> None:
        code, _, err = _invoke(self.root, "--export", "--cad-backend", "freecad")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)
        self.assertEqual(self.seen, [])

    def test_the_help_names_the_backend_selector_and_no_longer_calls_the_export_rhino(self) -> None:
        code, printed, _ = _invoke(self.root, "--help")
        self.assertEqual(code, 0)
        self.assertIn("--cad-backend {occt,rhino}", printed)
        export_help = re.search(r"^\s+--export\s+(?P<help>.*?)^\s+--cad-backend", printed, re.S | re.M)
        self.assertIsNotNone(export_help, printed)
        self.assertNotIn("Rhino", export_help.group("help"))
        self.assertIn("--cad-backend", export_help.group("help"))
        self.assertEqual(self.seen, [])

    def test_the_patch_oracle_is_refused_under_occt_naming_the_rhino_backend(self) -> None:
        """Asked of OCCT the oracle neither runs Rhino unasked nor is reported as an oracle that never ran: it is refused by name."""

        for argv in (("--export", "--patch-oracle"), ("--export", "--cad-backend", "occt", "--patch-oracle"), ("--patch-oracle",)):
            with self.subTest(argv=argv):
                code, printed, err = _invoke(self.root, *argv)
                self.assertEqual(code, 2, printed)
                self.assertIn("--patch-oracle", err)
                self.assertIn("--cad-backend rhino", err)
        self.assertEqual(self.seen, [])
        self.assertEqual([], list(Path(self.repository.layout.run(EQUIVALENCE_RUN).records).glob(f"{STATE_RECORD_EQUIVALENCE}-*.json")))
        # named with Rhino it is the P103 oracle request it always was
        code, printed, _ = _invoke(self.root, "--export", "--cad-backend", "rhino", "--patch-oracle")
        self.assertEqual(code, 0, printed)
        (options,) = self.seen
        self.assertEqual((options.cad_backend, options.patch_oracle, options.export), ("rhino", True, True))


class VerifyStateRecordCompareOnlyTests(unittest.TestCase):
    def test_compare_only_reads_the_retained_runner_receipt_and_runs_nothing(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "demo"
        repository, _ = _reference_project(root, DesignPhase.DESIGN_DEVELOPMENT)
        code, printed = _verify(root)
        self.assertEqual(code, 0, printed)
        records = Path(repository.layout.run(EQUIVALENCE_RUN).records)
        retained = _only(records, RUNNER_RUN_RECEIPT)

        with mock.patch.object(verify_state_record, "run_project", side_effect=AssertionError("--compare-only must not run the runner")):
            code, printed = _verify(root, "--compare-only")

        self.assertEqual(code, 0, printed)
        self.assertEqual(_only(records, RUNNER_RUN_RECEIPT), retained)                                    # no second runner run
        # a second equivalence record, compared against the receipt already retained in the run
        equivalence = json.loads(verify_state_record._latest(records, STATE_RECORD_EQUIVALENCE).read_text(encoding="utf-8"))
        self.assertTrue(equivalence["state_digest_equal"] and equivalence["geometry_equal"], equivalence)
        self.assertEqual(equivalence["reference_phase"], "design_development")
        self.assertEqual(equivalence["state_record_ref"], json.loads(retained.read_text(encoding="utf-8"))["state_record_ref"])


if __name__ == "__main__":
    unittest.main()
