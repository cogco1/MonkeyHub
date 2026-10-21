"""A model view leaves out what the model keeps hidden (gap 42).

A script's ``cut(mass, cutter)`` keeps the cutter in the model hidden under its own id, as evidence of what the cut
removed. The cut plans and elevations already left it out; the model view drew it, so a hole through a block showed
as the box that made it, and the frame grew to hold a cutter that stood proud of the block. The model view, and so a
visual review's frames, now draws the physical objects the source does not mark hidden, framed by them alone.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
from io import BytesIO
import unittest
from unittest.mock import patch

from PIL import Image

from project_runtime.application import drawings

from .test_construction_routes import ConstructionTestCase

# A 10 x 6 x 3 block with a 2 x 2 hole; the cutter runs from 0.5 below the block to 0.5 above it.
HOLE = "\n".join([
    "mass = extrude(rect(0, 0, 10, 6), 3)",
    "cutter = extrude(rect(4, 2, 2, 2), 4, at=-0.5)",
    "cut(mass, cutter)",
])


class HiddenObjectViewTestCase(ConstructionTestCase):
    cad_export = "occt"

    def setUp(self) -> None:
        super().setUp()
        # Each test draws from scratch: a view another test drew stays out of its answers.
        patcher = patch.object(drawings, "_MODEL_VIEWS", OrderedDict())
        patcher.start()
        self.addCleanup(patcher.stop)

    def view(self, source: dict, view: str, **params) -> dict:
        response = self.client.get("/api/drawings/model-view", params={**source, "view": view, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_cutter_a_cut_keeps_hidden_is_not_drawn_or_framed(self) -> None:
        run = self.run_candidate(self.construct(HOLE)["proposalId"])
        candidate = self.client.get(f"/api/candidates/{run}").json()
        [source] = [row["modelSource"] for row in candidate["artifacts"] if row.get("modelSource")]
        # Both stay in the model: the cutter is evidence, retained and hidden. The project's base also holds
        # a two-part portico inside the block's extent.
        objects = {row["name"] for row in candidate["objects"]}
        self.assertTrue({"obj-mass-body", "obj-cutter-body"} <= objects, objects)
        legend = self.view(source, "axon", display="material")["legend"]
        self.assertEqual(sum(row["objects"] for row in legend["materials"]) + legend["undeclared"], len(objects) - 1,
                         "every object but the cutter is drawn")
        # Framed by what is drawn, the block's 10 by 3: a frame that held the cutter, 4 high, is at most 10:4
        # whatever its margin, as it was before (2.2 with this view's margin).
        front = self.view(source, "front")
        with Image.open(BytesIO(base64.b64decode(front["data"], validate=True))) as image:
            width, height = image.size
        self.assertGreater(width / height, 10 / 4)


if __name__ == "__main__":
    unittest.main()
