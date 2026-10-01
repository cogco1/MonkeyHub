"""A candidate's declared materials are what its material view draws and its objects readback names (#580).

Through the Runtime as an agent drives it: a construction script makes four
parts and runs as a candidate; a facets proposal on that candidate declares
two materials and leaves one part undeclared; its candidate's exact model
asset is then read with ``GET /api/drawings/model-view?display=material``
and ``GET /api/model-assets/{sha}/index``. The colours come from that
asset's own material table, never from a part's id or a render. All
projects are disposable P036 fixtures.
"""

from __future__ import annotations

import base64
from importlib import import_module
from io import BytesIO
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services/project-runtime/src"))

from monkeycad.backends.occt.kernel import occt_available
from tests.integration.runner_support import _no_rhino

routes = import_module("services.project-runtime.tests.test_construction_routes")

PARTS = "\n".join([
    "west = extrude(rect(0, 0, 3, 0.4), 3)",
    "east = extrude(rect(4, 0, 3, 0.4), 3)",
    "roof = extrude(rect(0, 0, 7, 0.4), 0.3, at=top(west))",
    "sill = extrude(rect(0, -0.6, 7, 0.5), 0.2)",
])


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class CandidateMaterialViewTests(routes.ConstructionTestCase):
    cad_export = "occt"

    def setUp(self) -> None:
        super().setUp()
        for patcher in _no_rhino():
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_material_view_and_readback_show_the_colours_the_candidate_declared(self) -> None:
        from PIL import Image

        from monkeycad.program import _material_identity_color

        first = self.run_candidate(self.construct(PARTS)["proposalId"])
        declared = self.facets([
            {"id": "west", "set": {"material.name": "hemp-lime", "material.color": "#C8B98F"}},
            {"id": "east", "set": {"material.name": "hemp-lime"}},
            {"id": "roof", "set": {"material.name": "timber"}},
        ], sourceRunId=first)
        second = self.run_candidate(declared["proposalId"])
        candidate = self.client.get(f"/api/candidates/{second}").json()
        source = next(row for row in candidate["artifacts"] if row["format"] == "3dm")["modelSource"]
        timber = _material_identity_color("timber")
        timber_hex = "#{:02X}{:02X}{:02X}".format(*timber)

        response = self.client.get("/api/drawings/model-view", params={**source, "view": "axon", "display": "material"})
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()
        self.assertEqual((answer["source"], answer["representation"]), (source, "orthographic-material-projection"))
        legend = answer["legend"]
        self.assertEqual([(row["name"], row["color"], row["source"], row["objects"]) for row in legend["materials"]],
                         [("hemp-lime", "#C8B98F", "declared", 2), ("timber", timber_hex, "declared", 1)])
        # The sill and the fixture's own two portico parts declare none.
        self.assertEqual(legend["undeclared"], 3)
        with Image.open(BytesIO(base64.b64decode(answer["data"], validate=True))) as image:
            colours = {colour for _, colour in image.convert("RGB").getcolors(maxcolors=1 << 20)}
        self.assertTrue({(200, 185, 143), timber, (200, 200, 200)} <= colours,
                        "the declared colours and the undeclared grey are drawn")

        index = self.client.get(f"/api/model-assets/{source['assetSha256']}/index",
                                params={"runId": source["runId"], "stateDigest": source["stateDigest"]})
        self.assertEqual(index.status_code, 200, index.text)
        materials = {row["name"]: row["material"] for row in index.json()["objects"]}
        undeclared = {"name": None, "color": None, "source": "undeclared"}
        self.assertEqual(materials, {
            "obj-west-body": {"name": "hemp-lime", "color": "#C8B98F", "source": "declared"},
            "obj-east-body": {"name": "hemp-lime", "color": "#C8B98F", "source": "declared"},
            "obj-roof-body": {"name": "timber", "color": timber_hex, "source": "declared"},
            "obj-sill-body": undeclared, "obj-portico-base": undeclared, "obj-portico-cornice": undeclared,
        })


if __name__ == "__main__":
    unittest.main()
