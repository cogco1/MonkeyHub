"""A vertical section, an axonometric and a sheet of caller-placed views of one imported model, through the Runtime routes."""

import base64
import hashlib
import json
import math
from io import BytesIO, StringIO
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow.adapters.occt_backend import occt_available
from archflow.project.refs import record_ref_from_uri
from archflow_studio_api.application.artifacts import save_document
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project
from .test_drawing_plans import replacing

FIXTURE = Path(__file__).parent / "fixtures/model-source-a.3dm"
# The fixture's two boxes, in metres: A spans x 0..4, y 0..2, z 0..0.6; B spans x 8..9, y 0..1, z 0..2.
SECTION = {"line": [[-1, 0.5], [10, 0.5]], "keep": "left"}


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class ImportedModelDrawingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, reference_run=REFERENCE_RUN_ID, cad_export="off")
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.data = FIXTURE.read_bytes()
        response = self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "fileName": "boxes.3dm", "contentBase64": base64.b64encode(self.data).decode()})
        self.assertEqual(response.status_code, 201, response.text)
        self.asset = response.json()
        self.source = {"runId": self.asset["runId"], "assetSha256": self.asset["sha256"]}
        self.head = self.repository.read_head()

    def post(self, route, status=201, **body):
        response = self.client.post(f"/api/drawings/{route}", json={"projectId": PROJECT_ID, "sourceAsset": self.source, **body})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def documents(self):
        return self.client.get("/api/documents").json()["documents"]

    def receipt(self, document):
        return self.repository.load_json(record_ref_from_uri(document["revisionRef"], PROJECT_ID))

    def file(self, document, file_format, status=200):
        response = self.client.get(f"/api/drawings/{document['assetSha256']}/files/{file_format}", params={
            "runId": document["runId"], **({"revisionRef": document["revisionRef"]} if document.get("revisionRef") else {})})
        self.assertEqual(response.status_code, status, response.text[:300])
        return response

    def unchanged(self):
        self.assertEqual(self.repository.read_head(), self.head)
        registration = self.repository.load_json(record_ref_from_uri(self.asset["receiptRef"], PROJECT_ID))
        stored = self.repository.layout.resolve_relative(registration["artifact"]["relative_path"]).read_bytes()
        self.assertEqual(stored, self.data, "the imported model's bytes are never touched")

    def test_a_vertical_section_keeps_its_plane_side_and_depth_through_rebuilds_and_reads(self):
        section = self.post("plans", section=SECTION, depth=3, scaleDenominator=50, drawingId="section-a")
        frame = section["viewRecipe"]["frame"]
        self.assertEqual((frame["origin"], frame["look"], frame["right"], frame["up"]),
                         ([0.0, 0.5, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]))
        self.assertEqual((frame["near_depth"], frame["far_depth"], frame["scale"]), (0.0, 3.0, "1:50"))
        self.assertIsNone(section["modelSource"], "an imported model has no design state to name")
        self.assertEqual((section["viewRecipe"]["sourceAsset"], section["viewRecipe"]["follow"]), (self.source, "frozen"))
        self.assertEqual(section["viewRecipe"]["dimensions"], [])
        receipt = self.receipt(section)
        self.assertIn("vertical cut plane", receipt["projection"]["algorithm"])
        self.assertEqual(receipt["projection"]["section_regions"], 2, "both boxes stand in the plane y = 0.5")
        svg = self.file(section, "svg").content
        self.assertNotIn(b"data-component", svg, "an imported mesh carries no design semantics")
        # The window frames what the slab beyond the plane holds, in u = X and v = Z.
        u0, v0, u1, v1 = frame["crop_uv"]
        self.assertTrue(u0 < 0 and u1 > 9 and v0 < 0 and v1 > 2, frame["crop_uv"])

        again = self.post("plans", section=SECTION, depth=3, scaleDenominator=50, drawingId="section-a")
        self.assertEqual(again, section, "the same request reads the registered revision back")
        pens = self.post("plans", previousRevisionRef=section["revisionRef"], cutLineMm=0.5)
        self.assertEqual(pens["viewRecipe"]["frame"], frame, "a pen change keeps the plane, side, depth and window")
        self.assertEqual(pens["viewRecipe"]["graphics"]["cutLineMm"], 0.5)
        self.assertEqual(pens["replacesPages"], [replacing(section)])
        # The Diagram form sends its empty annotation lists with every edit: a section takes them as none.
        form = self.post("plans", previousRevisionRef=pens["revisionRef"], dimensions=[], dressing=[], hatchSpacingMm=0.8)
        self.assertEqual(form["viewRecipe"]["frame"], frame)
        ref = {key: pens[key] for key in ("runId", "assetSha256", "revisionRef")}
        status = self.client.post("/api/drawings/plans/status", json=ref).json()
        self.assertEqual((status["status"], status["lengthUnit"], status["dimensions"]), ("current", "meter", []))
        vector = self.client.get("/api/drawings/plans/vector", params=ref).json()
        self.assertEqual(vector["anchors"], [], "plan symbols are placed on plans, not sections")
        moved = self.post("plans", previousRevisionRef=form["revisionRef"], section={"origin": [0, 1.5, 0], "normal": [0, -1, 0]})
        self.assertEqual(moved["viewRecipe"]["frame"]["origin"], [0.0, 1.5, 0.0])
        self.assertEqual(moved["viewRecipe"]["frame"]["far_depth"], 3.0, "moving the plane keeps its depth")
        self.assertEqual(moved["viewRecipe"]["frame"]["crop_uv"], frame["crop_uv"], "and its window")
        self.assertEqual(self.receipt(moved)["projection"]["section_regions"], 1, "only box A reaches y = 1.5")

        refusals = (
            ({"previousRevisionRef": pens["revisionRef"], "cutHeight": 1.2}, 409, "DRAWING_ORIENTATION_CHANGED"),
            ({"section": {"line": [[0, 0], [4, 2]], "keep": "left"}}, 422, "SECTION_PLANE_NOT_MODEL_AXIS"),
            ({"section": {"line": [[-1, 5], [10, 5]], "keep": "left"}}, 422, "SECTION_PLANE_MISSES_MODEL"),
            ({"section": SECTION, "lengthUnit": "millimeter"}, 422, "DRAWING_UNIT_MISMATCH"),
            ({"section": SECTION, "dressing": [{"id": "p", "assetId": "person-plan", "positionUv": [1, 1], "size": 1}]},
             422, "DRAWING_SECTION_ANNOTATION_INVALID"),
            ({"section": SECTION, "cutHeight": 1.2}, 422, "REQUEST_INVALID"),
            ({"depth": 2}, 422, "DRAWING_SECTION_REQUIRED"),
        )
        before = self.documents()
        for body, status_code, code in refusals:
            with self.subTest(code=code):
                self.assertEqual(self.post("plans", status_code, **body)["code"], code)
        plan = self.post("plans", cutHeight=0.3, bottom=-0.1, scaleDenominator=50, drawingId="plan")
        self.assertEqual(plan["viewRecipe"]["frame"]["look"], [0.0, 0.0, -1.0], "a plan without section stays horizontal")
        self.assertNotIn("section", json.dumps(plan["viewRecipe"]))
        turned = self.post("plans", 409, previousRevisionRef=plan["revisionRef"], section=SECTION)
        self.assertEqual(turned["code"], "DRAWING_ORIENTATION_CHANGED")
        self.assertEqual(len(self.documents()), len(before) + 1, "no refusal registered a drawing")
        self.unchanged()

    def test_a_drawing_keeps_its_orientation_when_its_id_is_named_without_its_previous_revision(self):
        plan = self.post("plans", cutHeight=0.3, bottom=-0.1, scaleDenominator=50, drawingId="plan")
        section = self.post("plans", section=SECTION, depth=3, scaleDenominator=50, drawingId="section-a")
        before = self.documents()
        for body in ({"drawingId": "plan", "section": SECTION, "depth": 3},
                     {"drawingId": "section-a", "cutHeight": 0.3, "bottom": -0.1}):
            with self.subTest(drawing=body["drawingId"]):
                refused = self.post("plans", 409, scaleDenominator=50, **body)
                self.assertEqual(refused["code"], "DRAWING_ORIENTATION_CHANGED")
        # A sheet view names its drawing id without a previous revision; it cannot turn that drawing either.
        turned = self.sheet(409, views=[{"id": "plan", "placeMm": [20, 30],
                                         "plan": {"section": SECTION, "depth": 3, "scaleDenominator": 50}}])
        self.assertEqual(turned["code"], "DRAWING_ORIENTATION_CHANGED")
        self.assertTrue(turned["detail"].startswith("View plan: "), turned["detail"])
        self.assertEqual(self.documents(), before, "no refusal registered a drawing")
        lower = self.post("plans", cutHeight=0.2, bottom=-0.1, scaleDenominator=50, drawingId="plan")
        self.assertEqual((lower["drawingId"], lower["viewRecipe"]["frame"]["look"]), ("plan", [0.0, 0.0, -1.0]),
                         "the same orientation still registers another revision under the id")
        orientations = {(row["drawingId"], row["viewRecipe"]["frame"]["up"] == [0, 0, 1])
                        for row in self.documents() if row["viewRecipe"]["kind"] == "cut-plan"}
        self.assertEqual(orientations, {(plan["drawingId"], False), (section["drawingId"], True)})
        self.unchanged()

    def test_a_drawing_id_keeps_its_kind(self):
        # The Diagram opens the newest document of each drawing id: another kind under it would take that drawing's place.
        self.post("plans", cutHeight=0.3, bottom=-0.1, scaleDenominator=50, drawingId="plan")
        front = self.post("elevations", view="front", scaleDenominator=100, drawingId="front")
        self.post("section-perspectives", section=SECTION, depth=3, scaleDenominator=100, drawingId="perspective")
        before = self.documents()
        for route, body, detail in (
            ("elevations", {"view": "front", "drawingId": "plan"}, "Drawing plan is a plan; draw this elevation "),
            ("elevations", {"view": "axon", "drawingId": "perspective"},
             "Drawing perspective is a section perspective; draw this axonometric "),
            ("section-perspectives", {"section": SECTION, "depth": 3, "drawingId": "front"},
             "Drawing front is an elevation; draw this section perspective "),
            ("plans", {"cutHeight": 0.3, "bottom": -0.1, "drawingId": "front"}, "Drawing front is an elevation; draw this plan "),
            ("plans", {"section": SECTION, "depth": 3, "drawingId": "perspective"},
             "Drawing perspective is a section perspective; draw this vertical section "),
            ("sheets", {"styleId": "arch400-white", "scaleDenominator": 50}, None),
        ):
            with self.subTest(route=route, drawing=body.get("drawingId", body.get("styleId"))):
                if route == "sheets":
                    # A review sheet's drawing id is its style id: a plan named so keeps it.
                    self.post("plans", cutHeight=0.3, bottom=-0.1, scaleDenominator=50, drawingId="arch400-white")
                    before = self.documents()
                    detail = "Drawing arch400-white is a plan; draw this review sheet "
                refused = self.post(route, 409, scaleDenominator=body.pop("scaleDenominator", 100), **body)
                self.assertEqual(refused["code"], "DRAWING_KIND_CHANGED")
                self.assertTrue(refused["detail"].startswith(detail), refused["detail"])
        # A sheet view names its drawing id, and so does the sheet itself; neither takes another kind's.
        viewed = self.sheet(409, views=[{"id": "plan", "placeMm": [20, 30], "elevation": {"view": "front", "scaleDenominator": 100}}])
        self.assertEqual(viewed["code"], "DRAWING_KIND_CHANGED")
        self.assertTrue(viewed["detail"].startswith("View plan: Drawing plan is a plan; draw this elevation "), viewed["detail"])
        for body, detail in (({"drawingId": "front"}, "Drawing front is an elevation; draw this view sheet "),
                             ({"drawingId": "plan-low", "views": [{"id": "plan-low", "placeMm": [20, 30], "plan": {
                                 "cutHeight": 0.2, "bottom": -0.1, "scaleDenominator": 50}}]},
                              "View plan-low and its sheet cannot share one drawing id")):
            with self.subTest(sheet=body["drawingId"]):
                refused = self.sheet(409, **body)
                self.assertEqual(refused["code"], "DRAWING_KIND_CHANGED")
                self.assertTrue(refused["detail"].startswith(detail), refused["detail"])
        self.assertEqual(self.documents(), before, "no refusal drew or registered anything")
        # The same kind keeps its id: an identical request reads back, a changed one is another revision under it.
        self.assertEqual(self.post("elevations", view="front", scaleDenominator=100, drawingId="front"), front)
        right = self.post("elevations", view="right", scaleDenominator=100, drawingId="front")
        self.assertEqual((right["drawingId"], right["viewRecipe"]["kind"]), ("front", "model-axis-elevation"))
        self.unchanged()

    def test_an_axonometric_is_retained_from_a_stated_direction(self):
        iso = self.post("elevations", view="axon", direction=[1, -1, 1], scaleDenominator=200, drawingId="iso")
        look = iso["viewRecipe"]["look"]
        for actual, expected in zip(look, (-1 / math.sqrt(3), 1 / math.sqrt(3), -1 / math.sqrt(3))):
            self.assertAlmostEqual(actual, expected, places=12)
        self.assertEqual(iso["viewRecipe"]["scale"], "1:200")
        default = self.post("elevations", view="axon", scaleDenominator=200, drawingId="axon")
        self.assertEqual(default["viewRecipe"]["look"], [1 / math.sqrt(3), 1 / math.sqrt(3), -1 / math.sqrt(3)],
                         "the default axonometric is the model view's, from -X, -Y, +Z")
        self.assertEqual(self.post("elevations", 422, view="front", direction=[1, -1, 1])["code"], "REQUEST_INVALID")
        self.assertEqual(self.post("elevations", 422, view="axon", direction=[0, 0, 2])["code"], "DRAWING_VIEW_INVALID")
        self.unchanged()

    def sheet_views(self, **places):
        place = {"plan": [20, 30], "section-a": [20, 135], "iso": [270, 30], "section-perspective-a": [270, 150], **places}
        return [
            {"id": "plan", "placeMm": place["plan"],
             "plan": {"cutHeight": 0.3, "bottom": -0.1, "scaleDenominator": 50, "cropUv": [-1, -1, 10, 3]}},
            {"id": "section-a", "placeMm": place["section-a"], "markOn": "plan", "markLabel": "A",
             "plan": {"section": SECTION, "depth": 3, "scaleDenominator": 50, "cropUv": [-1, -0.5, 10, 2.5]}},
            {"id": "iso", "placeMm": place["iso"], "elevation": {"view": "axon", "direction": [1, -1, 1], "scaleDenominator": 100}},
            {"id": "section-perspective-a", "placeMm": place["section-perspective-a"], "markOn": "plan", "markLabel": "A",
             "sectionPerspective": {"section": SECTION, "depth": 3, "scaleDenominator": 100}},
        ]

    def sheet(self, status=201, **body):
        return self.post("sheets", status, styleId="arch364-technical", paperSizeMm=[420, 297], title="TWO BOXES",
                         sheetNumber="A3-01", lengthUnit="meter", **{"views": self.sheet_views(), **body})

    def test_one_source_several_views_on_one_sheet_as_pdf_dxf_svg_and_png(self):
        import ezdxf
        from PIL import Image
        from pypdf import PdfReader

        sheet = self.sheet()
        recipe = sheet["viewRecipe"]
        self.assertEqual((sheet["mimeType"], sheet["drawingId"], sheet["fileName"]), ("application/pdf", "sheet-A3-01", "sheet-A3-01.pdf"))
        self.assertIsNone(sheet["modelSource"])
        self.assertEqual((recipe["kind"], recipe["sourceAsset"], recipe["paperSizeMm"]), ("view-sheet", self.source, [420, 297]))
        rows = {row["id"]: row for row in recipe["views"]}
        self.assertEqual([row["kind"] for row in recipe["views"]], ["plan", "section", "axon", "section-perspective"])
        self.assertEqual(rows["plan"]["sizeMm"], [220.0, 80.0], "11 x 4 m at 1:50")
        self.assertEqual(rows["section-a"]["sizeMm"], [220.0, 60.0])
        self.assertEqual((rows["section-a"]["title"], rows["section-a"]["subtitle"], rows["section-a"]["scaleLabel"]),
                         ("SECTION A-A", "Vertical cut at Y +0.500 m, looking +Y", "1:50"))
        self.assertEqual((rows["iso"]["title"], rows["iso"]["scaleLabel"]), ("ISOMETRIC", "display 1:100"))
        self.assertEqual(rows["section-perspective-a"]["title"], "SECTION PERSPECTIVE A-A")
        self.assertIn("boxes.3dm", recipe["sourceText"])
        listed = {row["revisionRef"]: row for row in self.documents() if row["revisionRef"]}
        for row in recipe["views"]:
            view = listed[row["revisionRef"]]
            self.assertEqual((view["assetSha256"], view["drawingId"]), (row["assetSha256"], row["id"]))
            self.assertEqual(view["viewRecipe"]["sourceAsset"], self.source, "every view names the sheet's one source")

        pdf = self.file(sheet, "pdf").content
        self.assertEqual(hashlib.sha256(pdf).hexdigest(), sheet["assetSha256"])
        named = json.loads(PdfReader(BytesIO(pdf)).metadata["/ArchFlowSheetFiles"])
        self.assertEqual(json.loads(PdfReader(BytesIO(pdf)).metadata["/ArchFlowViewRecipe"]), recipe)
        text = PdfReader(BytesIO(pdf)).pages[0].extract_text()
        for expected in ("PLAN", "SECTION A-A", "ISOMETRIC", "TWO BOXES", "A3-01", "1:50"):
            self.assertIn(expected, text)
        files = {}
        for role in ("dxf", "svg", "png"):
            response = self.file(sheet, role)
            files[role] = response.content
            self.assertEqual(hashlib.sha256(response.content).hexdigest(), named[role])
            self.assertEqual(response.headers["etag"], f'"{named[role]}"')
        layout = ezdxf.read(StringIO(files["dxf"].decode())).layouts.get("A3-01")
        self.assertIn("SECTION A-A", [entity.dxf.text for entity in layout.query("TEXT")])
        self.assertIn(b'width="420mm" height="297mm"', files["svg"])
        with Image.open(BytesIO(files["png"])) as image:
            self.assertEqual(image.size, (2382, 1684), "A3 at the Board export's 144 dpi")
            self.assertLess(image.convert("L").getextrema()[0], 64, "the sheet has ink")
        view = listed[rows["section-a"]["revisionRef"]]
        self.assertTrue(self.file(view, "svg").content.startswith(b"<?xml"))
        for role in ("svg", "png"):
            # Two revisions can share a PNG and differ in SVG: a view's file is read by its exact revision.
            unnamed = self.client.get(f"/api/drawings/{view['assetSha256']}/files/{role}", params={"runId": view["runId"]})
            self.assertEqual((unnamed.status_code, unnamed.json()["code"]), (422, "DRAWING_REVISION_REQUIRED"), role)
        self.assertEqual(hashlib.sha256(self.file(view, "png").content).hexdigest(), view["assetSha256"])
        self.assertEqual(self.file(view, "pdf", 404).json()["code"], "DRAWING_FILE_UNAVAILABLE")

        documents = self.documents()
        self.assertEqual(self.sheet(), sheet, "the same request reads the registered sheet back")
        moved = self.sheet(views=self.sheet_views(iso=[280, 32]))
        self.assertNotEqual(moved["assetSha256"], sheet["assetSha256"])
        self.assertEqual([row["revisionRef"] for row in moved["viewRecipe"]["views"]],
                         [row["revisionRef"] for row in recipe["views"]], "moving a view on the paper redraws no view")
        self.assertEqual(len(self.documents()), len(documents) + 1)
        self.unchanged()

    def test_a_sheet_that_cannot_be_drawn_or_placed_is_refused_and_registers_no_sheet(self):
        first = self.sheet()
        sheets = [row for row in self.documents() if row["mimeType"] == "application/pdf"]
        for body, status, code in (
            ({"views": self.sheet_views(**{"section-a": [60, 60]})}, 422, "DRAWING_SHEET_LAYOUT_INVALID"),
            ({"views": self.sheet_views(iso=[380, 30])}, 422, "DRAWING_SHEET_LAYOUT_INVALID"),
            ({"views": self.sheet_views()[:1] + [{**self.sheet_views()[1], "plan": {
                "section": {"line": [[-1, 5], [10, 5]], "keep": "left"}, "scaleDenominator": 50}}]}, 422,
             "SECTION_PLANE_MISSES_MODEL"),
            ({"views": [{**self.sheet_views()[0], "plan": {"cutHeight": 0.3, "scaleDenominator": 50, "cropUv": [20, 20, 30, 30]}}]},
             422, "DRAWING_VIEW_EMPTY"),
            ({"views": [{**self.sheet_views()[2], "markOn": "iso", "markLabel": "B"}]}, 422, "REQUEST_INVALID"),
            ({"scaleDenominator": 50}, 422, "REQUEST_INVALID"),
            ({"lengthUnit": "foot"}, 422, "DRAWING_UNIT_MISMATCH"),
            ({"sourceAsset": {"runId": REFERENCE_RUN_ID, "assetSha256": self.asset["sha256"]}}, None, None),
        ):
            with self.subTest(code=code, body=list(body)):
                response = self.client.post("/api/drawings/sheets", json={
                    "projectId": PROJECT_ID, "sourceAsset": self.source, "styleId": "arch364-technical",
                    "paperSizeMm": [420, 297], "title": "TWO BOXES", "sheetNumber": "A3-01", "lengthUnit": "meter",
                    "views": self.sheet_views(), **body})
                if status is None:
                    self.assertIn(response.status_code, (404, 409), response.text)
                else:
                    self.assertEqual((response.status_code, response.json()["code"]), (status, code), response.text)
        views = [{"id": "plan-low", "placeMm": [20, 30],
                  "plan": {"cutHeight": 0.2, "bottom": -0.1, "scaleDenominator": 50, "cropUv": [-1, -1, 10, 3]}},
                 {"id": "section-b", "placeMm": [20, 135],
                  "plan": {"section": {"line": [[-1, 5], [10, 5]], "keep": "left"}, "scaleDenominator": 50}}]
        count = len(self.documents())
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                self.assertEqual(self.sheet(422, views=views)["code"], "SECTION_PLANE_MISSES_MODEL")
                self.assertEqual(len(self.documents()), count + 1,
                                 "a view drawn before the refusal stays registered, and a retry reads it back")
        self.assertEqual([row for row in self.documents() if row["mimeType"] == "application/pdf"], sheets,
                         "a refused sheet is never registered")
        self.assertEqual(self.sheet(), first)
        self.unchanged()

    def test_a_front_right_top_review_sheet_keeps_its_old_request_and_gains_its_files(self):
        review = self.post("sheets", styleId="arch400-white", scaleDenominator=50)
        self.assertEqual(review["viewRecipe"]["kind"], "review-sheet")
        self.assertEqual(review["drawingId"], "arch400-white")
        self.assertEqual(self.post("sheets", styleId="arch400-white", scaleDenominator=50), review)
        for role in ("pdf", "dxf", "svg", "png"):
            self.file(review, role)
        refused = self.post("sheets", 422, styleId="arch400-white", title="Views only")
        self.assertEqual(refused["code"], "REQUEST_INVALID")
        # A sheet retained before its PDF named its other files still reads, and only its PDF is served.
        pdf = self.file(review, "pdf").content
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf)))
        writer.add_metadata({"/ArchFlowSheetFiles": "", "/Producer": "an earlier sheet"})
        older = BytesIO()
        writer.write(older)
        document = save_document(bound_project(self.client.app.state), review["runId"], "older.pdf", "application/pdf",
                                 base64.b64encode(older.getvalue()).decode(), view_recipe={"kind": "review-sheet"},
                                 drawing_id="older")
        row = {"runId": document.run_id, "assetSha256": document.asset_sha256}
        self.assertEqual(self.file(row, "pdf").content, older.getvalue())
        self.assertEqual(self.file(row, "dxf", 404).json()["code"], "DRAWING_FILE_UNAVAILABLE")
        self.unchanged()

    def test_board_places_and_reopens_the_sheet_and_its_views(self):
        sheet = self.sheet()
        section = next(row for row in sheet["viewRecipe"]["views"] if row["id"] == "section-a")
        elements = [
            {"id": "sheet", "type": "image", "fileId": "sheet-page", "x": 0, "y": 0, "width": 420, "height": 297,
             "customData": {"sourceDocument": {"runId": sheet["runId"], "assetSha256": sheet["assetSha256"],
                                               "revisionRef": None, "pageIndex": 0}}},
            {"id": "section", "type": "image", "fileId": "section-page", "x": 500, "y": 0, "width": 220, "height": 60,
             "customData": {"sourceDocument": {"runId": section["runId"], "assetSha256": section["assetSha256"],
                                               "revisionRef": section["revisionRef"], "pageIndex": 0}}},
        ]
        saved = self.client.put("/api/board", json={"projectId": PROJECT_ID, "baseRevisionSha256": None,
                                                    "title": "Sheets", "elements": elements, "seenDocuments": []})
        self.assertEqual(saved.status_code, 200, saved.text)
        with TestClient(create_app(self.settings)) as reopened:
            board = reopened.get("/api/board").json()
            self.assertEqual((board["elements"], board["revisionSha256"]), (elements, saved.json()["revisionSha256"]))
            exported = reopened.post("/api/board/export", json={"projectId": PROJECT_ID, "format": "png", "pages": [
                {"runId": sheet["runId"], "assetSha256": sheet["assetSha256"], "revisionRef": None, "pageIndex": 0}]})
            self.assertEqual(exported.status_code, 200, exported.text[:200])
        self.unchanged()


if __name__ == "__main__":
    unittest.main()
