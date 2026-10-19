"""tools/project/state_file.py: start a project from a record, open a state-carrying 3DM, save the working design.

One project, built by the product's own tools, goes the whole way: its sealed file is changed outside
MonkeyHub (one element moved on its own, one that carries another moved), opened, revised once, and saved.
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

    def test_init_open_and_save_keep_the_record_the_decisions_and_what_was_changed_outside(self) -> None:
        start = self.root / "start.3dm"
        made = state_file.init(self.root / "record.json", self.root / "opening.json", self.root / "w0", start)
        self.assertEqual(made["decisions"], 2)
        sealed = read_state_file(start)
        self.assertEqual((sealed.out_of_band, sealed.binding["parent_file_sha256"]), ((), None))
        self.assertEqual(sorted((row["disposition"], row["rawLanguage"]) for row in sealed.decisions),
                         [("keep", "门廊基座别动"), ("reject", "不要长凳靠墙")])

        # A consultant moves the bench, which nothing depends on; the cornice north, off the base it stands on; and the
        # base east and up, without the cornice, which the record measures from the base's top.
        model = rhino3dm.File3dm.Read(str(start))
        for item in model.Objects:
            offset = {"obj-garden-bench": (0.5, 1.0, 0.2), "obj-portico-cornice": (0.0, 0.5, 0.0),
                      "obj-portico-base": (0.3, 0.0, 0.2)}.get(item.Attributes.Name)
            if offset:
                self.assertTrue(item.Geometry.Translate(rhino3dm.Vector3d(*offset)))
        delivered = self.root / "delivered.3dm"
        self.assertTrue(model.Write(str(delivered), 8))

        opened = state_file.open_file(delivered, self.root / "w1")
        self.assertEqual([(row["element"], row["offset"]) for row in opened["kept"]],
                         [("garden-bench", [0.5, 1.0, 0.2]), ("portico-cornice", [0.0, 0.5, 0.0])],
                         "the cornice moves alone: the relation naming it is checked again, not a reason to refuse")
        self.assertEqual([row["element"] for row in opened["notKept"]], ["portico-base"])
        self.assertIn("would also move obj-portico-cornice", opened["notKept"][0]["reason"],
                      "raised, the base would lift the cornice measured from it, which the delivered file did not move")
        self.assertEqual([row["subject_refs"] for row in opened["conditions"]], [["entity:portico-base", "entity:portico"]])
        for words in ("garden-bench was moved by (0.500, 1.000, 0.200) m", "kept in the design",
                      "portico-cornice was moved by (0.000, 0.500, 0.000) m",
                      "portico-base was moved by (0.300, 0.000, 0.200) m", "could not be kept",
                      "Ask the architect before changing what these touched: entity:portico, entity:portico-base."):
            self.assertIn(words, opened["excerpt"])

        # One change built during the hop, then the save.
        project = Path(opened["projectDir"])
        with state_file.runtime(project) as client:
            outside = state_file._call(client, "GET", "/api/revisions/context")["outside"]
            self.assertEqual(sorted((row["subjects"][0], row["inTheDesign"]) for row in outside),
                             [("entity:garden-bench", True), ("entity:portico-base", False), ("entity:portico-cornice", True)])
            self.assertTrue(any("(0.500, 1.000, 0.200) m (east, north, up)" in row["statement"] for row in outside))
            self.assertTrue(any("(0.300, 0.000, 0.200) m (east, north, up)" in row["statement"] for row in outside))
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
        before, after = _boxes(delivered), _boxes(Path(saved["file"]))
        for name in ("obj-garden-bench", "obj-portico-cornice"):
            self.assertTrue(all(abs(a - b) <= 0.002 for a, b in zip((*before[name][0], *before[name][1]),
                                                                     (*after[name][0], *after[name][1]))),
                            f"{name} is where the delivered file had it")
        self.assertEqual(sorted(row["rawLanguage"] for row in out.decisions), ["不要长凳靠墙", "门廊基座别动"])

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
                state_file._land_as_person(client, "file-demo", started["candidateId"], f"hop {hop}")
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
