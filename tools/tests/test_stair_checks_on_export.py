"""The stair checks read what OCCT really exported, not boxes written by hand (#659).

One small house is built by the product's own tools and exported: a flight up to a floor, which it reaches
through a stair hole over its upper treads and lands on at its head, a glass balustrade and a handrail post at
the flight's side, and a shed far away. Its conditions are then checked on the exported objects' boxes.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.dev import source_roots  # noqa: E402

source_roots.put_first(REPO)

from archflow.state.operational_state import DesignObligation  # noqa: E402
from archflow.state.state_record import StateRecord, check_obligations  # noqa: E402
from monkeycad.backends.occt.kernel import occt_available  # noqa: E402
from monkeycad.formats.state_file import object_rows  # noqa: E402
from tools.project import state_file  # noqa: E402

try:
    import rhino3dm
except ImportError:  # pragma: no cover - the exporter needs it
    rhino3dm = None


def _prism(element_id: str, profile: list, height: float, level: str = "level-ground", **references) -> dict:
    return {"entity_id": element_id, "schema": "Element@1", "parent_id": "house", "basis_refs": ["evidence:demo"],
            "fields": {"component_id": "house", "producer": "prism", "params": {"profile": profile, "height": height},
                       "references": {"base": {"level": level}, **references}}}


RECORD = {
    "schema": "StateRecord@1", "project_id": "stair-demo", "run_id": "authored",
    "entities": [
        {"entity_id": "house", "schema": "Component@1", "fields": {"intent": "a house with one flight"}},
        {"entity_id": "level-ground", "schema": "Level@1", "basis_refs": ["evidence:demo"], "fields": {"elevation": 0.0, "role": "ground"}},
        {"entity_id": "level-upper", "schema": "Level@1", "basis_refs": ["evidence:demo"], "fields": {"elevation": 2.8, "role": "upper"}},
        {"entity_id": "stair", "schema": "Element@1", "parent_id": "house", "basis_refs": ["evidence:demo"], "fields": {
            "component_id": "house", "producer": "stair", "params": {"count": 15, "rise": 0.2, "going": 0.2, "width": 1.0},
            "references": {"from": {"point": [1.0, 1.0]}, "to": {"point": [1.0, 4.0]}, "base": {"level": "level-ground"}}}},
        # The floor reached at 3.0 m; its stair hole opens over the treads above 0.6 m, so tread 3 has 2.2 m under it.
        _prism("slab", [[0, 0], [6, 0], [6, 6], [0, 6]], 0.2, "level-upper", voids=["hole"]),
        _prism("hole", [[0.4, 1.6], [1.6, 1.6], [1.6, 4.0], [0.4, 4.0]], 0.3, "level-upper"),
        _prism("glass", [[1.47, 1.0], [1.482, 1.0], [1.482, 4.0], [1.47, 4.0]], 4.0),
        _prism("post", [[1.475, 2.5], [1.525, 2.5], [1.525, 2.55], [1.475, 2.55]], 1.2),
        _prism("shed", [[20, 20], [24, 20], [24, 23], [20, 23]], 2.4),
    ],
    "parameters": [], "relations": [],
    "obligations": [
        DesignObligation("k6", "Headroom over the stair must stay at least 2.0 m.", "evidence:demo",
                         subject_refs=("entity:stair",), validator_ref="validator:headroom-at-least/2.0").to_dict(),
        DesignObligation("r8", "Landings stay at least 1.0 m deep.", "evidence:demo",
                         subject_refs=("entity:stair",), validator_ref="validator:landing-depth-at-least/1.0").to_dict(),
    ],
    "evidence_refs": ["evidence:demo"],
}


@unittest.skipUnless(occt_available() and rhino3dm is not None, "cadquery-ocp and rhino3dm are needed")
class StairChecksOnExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="stair-checks-")
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "record.json").write_text(json.dumps(RECORD), encoding="utf-8")
        (root / "opening.json").write_text("[]", encoding="utf-8")
        state_file.init(root / "record.json", root / "opening.json", root / "w", root / "house.3dm")
        cls.boxes = {row.name: (tuple(row.min), tuple(row.max)) for row in object_rows(root / "house.3dm")}
        cls.record = StateRecord.from_dict(RECORD)

    def results(self, record: StateRecord | None = None, boxes=None) -> dict[str, tuple[str, str]]:
        return {row.obligation_id: (row.status, row.detail)
                for row in check_obligations(record or self.record, boxes=self.boxes if boxes is None else boxes)}

    def test_the_built_house_keeps_its_headroom_and_landing_past_its_railing(self) -> None:
        self.assertTrue({"obj-slab", "obj-glass", "obj-post", "obj-shed"} <= set(self.boxes), sorted(self.boxes))
        self.assertTrue(any(name.startswith("obj-stair") for name in self.boxes), sorted(self.boxes))
        found = self.results()
        self.assertEqual(found["k6"][0], "held", found["k6"][1])
        self.assertIn("least headroom 2.2", found["k6"][1])
        self.assertEqual(found["r8"][0], "held", found["r8"][1])
        self.assertIn("stair head", found["r8"][1])

    def test_the_same_glass_in_the_middle_of_the_flight_is_in_the_way(self) -> None:
        (lo, hi) = self.boxes["obj-glass"]
        middle = ((0.994, lo[1], lo[2]), (1.006, hi[1], hi[2]))
        status, detail = self.results(boxes={**self.boxes, "obj-glass": middle})["k6"]
        self.assertEqual(status, "violated")
        self.assertIn("under obj-glass", detail)

    def test_a_missing_object_matters_only_where_the_check_looks(self) -> None:
        far = self.results(boxes={name: box for name, box in self.boxes.items() if name != "obj-shed"})
        self.assertEqual({k: v[0] for k, v in far.items()}, {"k6": "held", "r8": "held"})
        over = self.results(boxes={name: box for name, box in self.boxes.items() if name != "obj-slab"})
        self.assertEqual({k: v[0] for k, v in over.items()}, {"k6": "unchecked", "r8": "unchecked"})

    def test_a_low_slab_over_the_flight_is_a_real_violation(self) -> None:
        low = replace(self.record, obligations=(replace(self.record.obligations[0], validator_ref="validator:headroom-at-least/3.5"),))
        self.assertEqual(self.results(low)["k6"][0], "violated")


if __name__ == "__main__":
    unittest.main()
