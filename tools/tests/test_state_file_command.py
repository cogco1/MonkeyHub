"""tools/project/state_file.py: start a project from a record, open a state-carrying 3DM, save the working design.

One project, built by the product's own tools, goes the whole way: its sealed file is changed outside
MonkeyHub (three parts moved), opened, revised once, and saved with every part where its record has it.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.dev import source_roots  # noqa: E402

source_roots.put_first(REPO)

from monkeycad.backends.occt.kernel import occt_available  # noqa: E402
from monkeycad.formats.state_file import object_rows, read_state_file  # noqa: E402
from tools.project import state_file  # noqa: E402

try:
    import rhino3dm
except ImportError:  # pragma: no cover - the exporter needs it
    rhino3dm = None

RECORD = {
    "schema": "StateRecord@1", "project_id": "file-demo", "run_id": "authored",
    "entities": [
        {"entity_id": "building", "schema": "Component@1", "fields": {"intent": "the demo building"}},
        {"entity_id": "portico", "schema": "Component@1", "parent_id": "building", "fields": {"intent": "a portico"}},
        {"entity_id": "garden", "schema": "Component@1", "parent_id": "building", "fields": {"intent": "a garden"}},
        {"entity_id": "level-ground", "schema": "Level@1", "basis_refs": ["evidence:demo"],
         "fields": {"elevation": 0.0, "role": "ground"}},
        {"entity_id": "portico-base", "schema": "Element@1", "basis_refs": ["evidence:demo"], "parent_id": "portico", "fields": {
            "component_id": "portico", "producer": "prism", "params": {"height": 0.6, "profile": [[0, 0], [4, 0], [4, 2], [0, 2]]},
            "references": {"base": {"level": "level-ground"}}}},
        {"entity_id": "portico-cornice", "schema": "Element@1", "basis_refs": ["evidence:demo"], "parent_id": "portico", "fields": {
            "component_id": "portico", "producer": "prism", "params": {"height": 0.3, "profile": [[0, 0], [4, 0], [4, 2], [0, 2]]},
            "references": {"base": {"datum": "portico-base-top"}}}},
        {"entity_id": "garden-bench", "schema": "Element@1", "basis_refs": ["evidence:demo"], "parent_id": "garden", "fields": {
            "component_id": "garden", "producer": "prism", "params": {"height": 0.5, "profile": [[6, 0], [8, 0], [8, 1], [6, 1]]},
            "references": {"base": {"level": "level-ground"}}}},
    ],
    "parameters": [],
    "relations": [{"relation_id": "rel-cornice-on-base", "kind": "support", "subject": "portico-base",
                   "object": "portico-cornice", "propagation": "revalidate",
                   "validator": {"check_kind": "support_contact", "tolerance": 0.001}}],
    "obligations": [],
    "evidence_refs": ["evidence:demo"],
}
OPENING = [{"text": "门廊基座别动", "target_ref": "entity:portico-base", "disposition": "keep"},
           {"text": "不要长凳靠墙", "target_ref": "entity:garden-bench", "disposition": "reject"}]


def _land_as_person(client, project_id: str, run_id: str, label: str) -> None:
    """Admit a built candidate and continue the working design onto it, as the architect does."""

    state_file._call(client, "POST", "/api/admissions", {"projectId": project_id, "task": {"kind": "ui"},
                                                        "results": [{"runId": run_id, "outcome": "admitted", "label": label}]})
    _, _, revision = state_file._head(client)
    state_file._call(client, "PUT", "/api/working-draft", {"projectId": project_id, "runId": run_id,
                                                          "baseRevisionSha256": revision})


def _boxes(path: Path) -> dict[str, tuple]:
    return {row.name: (row.min, row.max) for row in object_rows(path)}


@unittest.skipUnless(occt_available() and rhino3dm is not None, "cadquery-ocp and rhino3dm are needed")
class StateFileCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="state-file-command-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "record.json").write_text(json.dumps(RECORD), encoding="utf-8")
        (self.root / "opening.json").write_text(json.dumps(OPENING, ensure_ascii=False), encoding="utf-8")

    def test_init_open_and_save_keep_the_record_and_put_what_was_changed_outside_to_the_architect(self) -> None:
        start = self.root / "start.3dm"
        made = state_file.init(self.root / "record.json", self.root / "opening.json", self.root / "w0", start)
        self.assertEqual(made["decisions"], 2)
        sealed = read_state_file(start)
        self.assertEqual((sealed.out_of_band, sealed.binding["parent_file_sha256"]), ((), None))
        self.assertEqual(sorted((row["disposition"], row["rawLanguage"]) for row in sealed.decisions),
                         [("keep", "门廊基座别动"), ("reject", "不要长凳靠墙")])

        # A consultant moves the bench, the cornice north, and the base east and up without the cornice.
        moves = {"obj-garden-bench": (0.5, 1.0, 0.2), "obj-portico-cornice": (0.0, 0.5, 0.0), "obj-portico-base": (0.3, 0.0, 0.2)}
        model = rhino3dm.File3dm.Read(str(start))
        for item in model.Objects:
            offset = moves.get(item.Attributes.Name)
            if offset:
                self.assertTrue(item.Geometry.Translate(rhino3dm.Vector3d(*offset)))
        delivered = self.root / "delivered.3dm"
        self.assertTrue(model.Write(str(delivered), 8))

        opened = state_file.open_file(delivered, self.root / "w1")
        self.assertNotIn("kept", opened, "nothing made outside MonkeyHub is taken into the design")
        self.assertEqual(sorted(row["subject_refs"] for row in opened["conditions"]),
                         [["entity:garden-bench"], ["entity:portico-base"], ["entity:portico-cornice"]],
                         "one question on each moved part, none on the whole it belongs to")
        self.assertTrue(all(row["validator_ref"] is None for row in opened["conditions"]))
        for words in ("obj-garden-bench was moved by (0.500, 1.000, 0.200) m (east, north, up) (part garden-bench)",
                      "obj-portico-cornice was moved by (0.000, 0.500, 0.000) m",
                      "obj-portico-base was moved by (0.300, 0.000, 0.200) m",
                      "ask the architect whether to adopt it",
                      "Until the architect answers, ask before changing entity:garden-bench, entity:portico-base, "
                      "entity:portico-cornice."):
            self.assertIn(words, opened["excerpt"])

        # The questions reach the reviser; one change is built during the hop, then the save.
        project = Path(opened["projectDir"])
        with state_file.runtime(project) as client:
            outside = state_file._call(client, "GET", "/api/revisions/context")["outside"]
            self.assertEqual(sorted(row["subjects"] for row in outside),
                             [["entity:garden-bench"], ["entity:portico-base"], ["entity:portico-cornice"]])
            self.assertTrue(any("waits for the architect's answer" in row["statement"] for row in outside))
            run_id, digest, _ = state_file._head(client)
            proposal = state_file._call(client, "POST", "/api/proposals/transform", {
                "projectId": "file-demo", "stateDigest": digest, "sourceRunId": run_id, "elementId": "garden-bench",
                "kind": "move", "translation": [0.0, 0.0, 0.5]})
            started = state_file._call(client, "POST", f"/api/proposals/{proposal['proposalId']}/candidate")
            self.assertEqual(state_file._wait_job(client, started["jobId"])["status"], "succeeded")
        saved = state_file.save(project, self.root / "model.3dm", None, self.root / "candidates")
        self.assertEqual(saved["parentFileSha256"], opened["fileSha256"], "the file names the one it was opened from")
        self.assertEqual([row["runId"] for row in saved["candidates"]], [started["candidateId"]])
        self.assertTrue(Path(saved["candidates"][0]["file"]).is_file())

        out = read_state_file(Path(saved["file"]))
        self.assertEqual(out.out_of_band, ())
        recorded, after = _boxes(start), _boxes(Path(saved["file"]))
        for name in recorded:
            self.assertTrue(all(abs(a - b) <= 0.002 for a, b in zip((*recorded[name][0], *recorded[name][1]),
                                                                     (*after[name][0], *after[name][1]))),
                            f"{name} is where the record has it, not where the consultant put it")
        self.assertEqual(sorted(row["subject_refs"] for row in out.record["obligations"] if row["obligation_id"].startswith("outside-")),
                         [["entity:garden-bench"], ["entity:portico-base"], ["entity:portico-cornice"]],
                         "the file written back carries the questions until the architect answers")
        self.assertEqual(sorted(row["rawLanguage"] for row in out.decisions), ["不要长凳靠墙", "门廊基座别动"])

    def test_a_record_or_decision_edited_in_the_file_is_listed_and_not_taken_in(self) -> None:
        start = self.root / "start.3dm"
        state_file.init(self.root / "record.json", self.root / "opening.json", self.root / "w0", start)

        # Someone outside MonkeyHub raises the bench in the file's record and drops the architect's rejection of it.
        model = rhino3dm.File3dm.Read(str(start))
        state = json.loads(model.Strings["archflow:state"])
        bench = next(row for row in state["record"]["entities"] if row["entity_id"] == "garden-bench")
        bench["fields"]["params"]["height"] = 0.9
        state["decisions"] = [row for row in state["decisions"] if row["disposition"] != "reject"]
        model.Strings["archflow:state"] = json.dumps(state)
        edited = self.root / "edited.3dm"
        self.assertTrue(model.Write(str(edited), 8))

        opened = state_file.open_file(edited, self.root / "w1")
        for words in ("entity:garden-bench was changed in the file's design record",
                      "removed reject on entity:garden-bench (\"不要长凳靠墙\")", "ask the architect whether to adopt it"):
            self.assertIn(words, opened["excerpt"])
        self.assertIn(["entity:garden-bench"], [row["subject_refs"] for row in opened["conditions"]])
        saved = read_state_file(Path(state_file.save(Path(opened["projectDir"]), self.root / "model.3dm", None, None)["file"]))
        bench = next(row for row in saved.record["entities"] if row["entity_id"] == "garden-bench")
        self.assertEqual(bench["fields"]["params"]["height"], 0.5, "the design keeps the record MonkeyHub sealed")
        self.assertIn(("reject", "不要长凳靠墙"), [(row["disposition"], row["rawLanguage"]) for row in saved.decisions],
                      "a decision removed in the file is still the architect's")

        # A file whose seal was taken away opens from the record it states, and the whole design waits for the architect.
        model.Strings.Delete("archflow:binding")
        unsealed = self.root / "unsealed.3dm"
        self.assertTrue(model.Write(str(unsealed), 8))
        reopened = state_file.open_file(unsealed, self.root / "w2")
        self.assertEqual([row["subject_refs"] for row in reopened["conditions"]], [["entity:building"]])
        self.assertEqual(reopened["decisions"], 0, "decisions no seal vouches for are not recorded as the architect's")
        for words in ("MonkeyHub cannot tell this file's design from changes made outside it",
                      "they are not recorded as the architect's: keep on entity:portico-base"):
            self.assertIn(words, reopened["excerpt"])

    def test_the_events_each_hop_saves_rebuild_the_state_its_file_carries(self) -> None:
        file = self.root / "start.3dm"
        made = state_file.init(self.root / "record.json", self.root / "opening.json", self.root / "w0", file)
        project, events = Path(made["projectDir"]), []
        for hop, translation in enumerate(([0.5, 0.0, 0.0], [0.0, 0.0, 0.5]), 1):
            if hop > 1:
                project = Path(state_file.open_file(file, self.root / f"w{hop}")["projectDir"])
            with state_file.runtime(project) as client:
                run_id, digest, _ = state_file._head(client)
                proposal = state_file._call(client, "POST", "/api/proposals/transform", {
                    "projectId": "file-demo", "stateDigest": digest, "sourceRunId": run_id, "elementId": "garden-bench",
                    "kind": "move", "translation": translation})
                started = state_file._call(client, "POST", f"/api/proposals/{proposal['proposalId']}/candidate")
                self.assertEqual(state_file._wait_job(client, started["jobId"])["status"], "succeeded")
                _land_as_person(client, "file-demo", started["candidateId"], f"hop {hop}")
                if hop == 1:
                    state_file.record_decisions(client, "file-demo", [
                        {"text": "柱廊两侧要对称", "target_ref": "entity:portico", "disposition": "keep"}])
            start, file = file, self.root / f"model-{hop}.3dm"
            saved = state_file.save(project, file, None, None, self.root / f"hop-{hop}.jsonl")
            hop_events = [json.loads(line) for line in (self.root / f"hop-{hop}.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["kind"] for row in hop_events], ["accept", "decision"] if hop == 1 else ["accept"])
            self.assertEqual(saved["eventCount"], len(hop_events))
            events += hop_events

            found = read_state_file(file)
            parts = state_file.state_parts(found.record, found.decisions)
            for rebuilt in (state_file.replay(RECORD, OPENING, events), state_file.replay(
                    read_state_file(start).record, read_state_file(start).decisions, hop_events)):
                self.assertEqual(rebuilt["parts"], parts, f"hop {hop}")
                self.assertTrue(all(step.get("baseMatches", True) and step.get("resultMatches", True)
                                    for step in rebuilt["steps"]), rebuilt["steps"])
            self.assertIn("conditions", parts)
            self.assertEqual(sorted(parts["rejected"]["rows"])[0].split(":")[0], "reject")

        # The same answers on the command line, as the shell calls them.
        answers = []
        for argv in (["parts", "--file", str(file)],
                     ["replay", "--record", str(self.root / "record.json"), "--opening", str(self.root / "opening.json"),
                      "--events", str(self.root / "hop-1.jsonl"), str(self.root / "hop-2.jsonl")],
                     ["replay", "--start-file", str(self.root / "model-1.3dm"), "--events", str(self.root / "hop-2.jsonl")]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(state_file.main(argv), 0)
            answers.append(json.loads(out.getvalue())["parts"])
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(answers[0], answers[2])

    def test_a_failure_says_why_on_stderr_and_exits_nonzero(self) -> None:
        missing = self.root / "missing.3dm"
        self.assertEqual(state_file.main(["open", "--file", str(missing), "--work-root", str(self.root / "w")]), 1)


@unittest.skipUnless(rhino3dm is not None, "rhino3dm is needed")
class RefusalTests(unittest.TestCase):
    """open refuses a file with exit code 2, before anything is built; every other failure is exit code 1."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="state-file-refusal-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def open(self, file: Path, *flags: str) -> int:
        with contextlib.redirect_stderr(io.StringIO()):
            return state_file.main(["open", "--file", str(file), "--work-root", str(self.root / "w"), *flags])

    def state_file(self, name: str, **strings: str) -> Path:
        model = rhino3dm.File3dm()
        model.Strings["archflow:state"] = json.dumps({"schema": "ArchFlowStateFile@1", "record": RECORD, "decisions": []})
        for key, value in strings.items():
            model.Strings[key] = value
        path = self.root / name
        self.assertTrue(model.Write(str(path), 8))
        return path

    def test_exit_codes(self) -> None:
        self.assertEqual(self.open(self.root / "missing.3dm"), 1, "no file is the caller's mistake, not a refusal")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as usage:
            state_file.main(["open", "--work-root", str(self.root / "w")])
        self.assertEqual(usage.exception.code, 1, "a mistaken command line is not a refusal either")
        garbage = self.root / "garbage.3dm"
        garbage.write_bytes(b"not a model")
        self.assertEqual(self.open(garbage), 2)
        self.assertEqual(self.open(self.state_file("unsealed.3dm"), "--refuse-unvouched"), 2)
        self.assertEqual(self.open(self.state_file("emptied.3dm", **{"archflow:binding": "{}"}), "--refuse-unvouched"), 2)
        self.assertFalse((self.root / "w").exists(), "nothing was built")


class ReplayTests(unittest.TestCase):
    """The history's own rules, without building anything."""

    def test_a_withdrawn_decision_leaves_the_state_and_one_the_history_lacks_is_refused(self) -> None:
        reject = {"disposition": "reject", "targetRef": "entity:garden-bench", "rawLanguage": "不要长凳靠墙", "strength": "hard"}
        rebuilt = state_file.replay(RECORD, OPENING, [{"kind": "withdraw", "row": reject}])
        self.assertEqual(rebuilt["parts"]["rejected"]["rows"], {})
        self.assertEqual(rebuilt["parts"], state_file.state_parts(RECORD, OPENING[:1]))
        with self.assertRaises(state_file.StateFileCommandError):
            state_file.replay(RECORD, OPENING, [{"kind": "withdraw", "row": reject}] * 2)
        with self.assertRaises(state_file.StateFileCommandError):
            state_file.replay(RECORD, OPENING, [{"kind": "rename"}])


if __name__ == "__main__":
    unittest.main()
