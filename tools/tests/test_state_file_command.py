"""tools/project/state_file.py: start a project from a record, open a state-carrying 3DM, save the working design.

One project, built by the product's own tools, goes the whole way: its sealed file is changed outside
MonkeyHub (one element moved on its own, one that carries another moved), opened, revised once, and saved.
"""

from __future__ import annotations

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

        # A consultant moves the bench on its own, and the base without the cornice that stands on it.
        model = rhino3dm.File3dm.Read(str(start))
        for item in model.Objects:
            offset = {"obj-garden-bench": (0.5, 1.0, 0.2), "obj-portico-base": (0.3, 0.0, 0.0)}.get(item.Attributes.Name)
            if offset:
                self.assertTrue(item.Geometry.Translate(rhino3dm.Vector3d(*offset)))
        delivered = self.root / "delivered.3dm"
        self.assertTrue(model.Write(str(delivered), 8))

        opened = state_file.open_file(delivered, self.root / "w1")
        self.assertEqual([(row["element"], row["offset"]) for row in opened["kept"]], [("garden-bench", [0.5, 1.0, 0.2])])
        self.assertEqual([row["element"] for row in opened["notKept"]], ["portico-base"])
        self.assertIn("rel-cornice-on-base", opened["notKept"][0]["reason"])
        self.assertEqual([row["subject_refs"] for row in opened["conditions"]], [["entity:portico-base", "entity:portico"]])
        for words in ("garden-bench was moved by (0.500, 1.000, 0.200) m", "kept in the design",
                      "portico-base was moved by (0.300, 0.000, 0.000) m", "could not be kept",
                      "Ask the architect before changing what these touched: entity:portico, entity:portico-base."):
            self.assertIn(words, opened["excerpt"])

        # One change built during the hop, then the save.
        project = Path(opened["projectDir"])
        with state_file.runtime(project) as client:
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

    def test_a_failure_says_why_on_stderr_and_exits_nonzero(self) -> None:
        missing = self.root / "missing.3dm"
        self.assertEqual(state_file.main(["open", "--file", str(missing), "--work-root", str(self.root / "w")]), 1)


if __name__ == "__main__":
    unittest.main()
