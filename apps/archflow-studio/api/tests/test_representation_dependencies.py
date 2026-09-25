"""Real Drawing/P036 inputs and Render dependency propagation; image transport is injected."""

import base64
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from archflow.adapters.occt_backend import occt_available
from archflow_studio_api.application.artifacts import ModelSource, save_document
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.representation_dependencies import RepresentationReads, representation_status
from archflow_studio_api.main import create_app

from . import test_drawing_plans as plans
from . import test_publications as publication
from . import test_rendering as renders
from .support import PROJECT_ID
from .test_document_annotations import stroke

setup = renders.setup


def page(document):
    return {key: document[key] for key in ("runId", "assetSha256", "revisionRef")} | {"pageIndex": 0}


def key(ref):
    return ref["runId"], ref["assetSha256"], ref["revisionRef"], ref["pageIndex"]


def distant_wall():
    """A real wall far outside every plan crop in the room fixture."""
    wall = deepcopy(plans.room_edit()["entities"][1])
    wall["entity_id"] = "distant-wall"
    wall["fields"]["references"]["line"] = {
        "from": {"point": [0, 20]}, "to": {"point": [4, 20]}, "inward": [0, 1]}
    return {"summary": "Add unrelated distant wall", "entities": [wall]}


def render(client, source, **changes):
    return renders.finished(client, renders.submit(client, renders.request(source, **changes)))


def reread(client, job):
    result = client.get("/api/render/jobs/" + job["jobId"])
    assert result.status_code == 200, result.text
    return result.json()


def test_replaced_reference_propagates_through_render_but_not_independent_variant(setup):
    client, app, repository, adapter = setup
    source, reference = renders.upload(client), renders.upload(client, "red")
    first = render(client, source, references=[reference])
    descendant = render(client, page(first["document"]))
    independent = render(client, source, direction="Neutral lighting")
    original_head = repository.read_head()
    renders.upload(client, "yellow", replacesPages=[reference | {"newPageIndex": 0}])
    for job, expected in ((first, "outdated"), (descendant, "outdated"), (independent, "current")):
        reading = reread(client, job)
        assert reading["sourceState"] == expected
        assert reading["request"] == job["request"]
        assert reading["document"] == job["document"]
        assert reading["status"] == "succeeded" and reading["resultAvailable"]
    # The same immutable owners reconstruct the answer in another runtime.
    reopened_adapter = renders.Adapter()
    reopened = create_app(app.state.settings, render_adapter=reopened_adapter)
    try:
        with TestClient(reopened) as reader:
            assert reread(reader, descendant)["sourceState"] == "outdated"
            assert reread(reader, independent)["sourceState"] == "current"
        assert reopened_adapter.calls == []
    finally:
        reopened.state.render_jobs.shutdown()
        reopened.state.jobs.shutdown()
    assert repository.read_head() == original_head
    assert len(adapter.calls) == 3


def test_missing_transitive_input_is_unavailable_without_destroying_render_result(setup):
    client, _, repository, adapter = setup
    source = renders.upload(client)
    first = render(client, source)
    descendant = render(client, page(first["document"]))
    digest = source["assetSha256"]
    repository.layout.resolve_relative(f"objects/sha256/{digest[:2]}/{digest}").write_bytes(b"replaced bytes")
    reading = reread(client, descendant)
    assert reading["sourceState"] == "unavailable"
    assert reading["resultAvailable"] and reading["document"] == descendant["document"]
    assert len(adapter.calls) == 2


@pytest.fixture
def room():
    if not occt_available():
        pytest.skip("cadquery-ocp is optional")
    fixture = plans.CutPlanTests()
    fixture.setUp()
    adapter = renders.Adapter()
    fixture.app.state.render_jobs.adapter = adapter
    try:
        yield fixture, adapter
    finally:
        fixture.app.state.render_jobs.shutdown()
        fixture.app.state.jobs.shutdown()
        fixture.doCleanups()


def test_three_exact_design_representations_reopen_and_only_follow_read_geometry(room):
    fixture, adapter = room
    client = fixture.client
    # Real OCCT cut plans: the three retained view/style recipes have the same
    # exact architectural source, not three copies of design state.
    variants = [fixture.generate(drawingId=name, dimensions=[], **options) for name, options in (
        ("review-plan", {}),
        ("heavy-cut-plan", {"cutLineMm": .7}),
        ("upper-plan", {"cutHeight": 2.5}),
    )]
    assert all(doc["modelSource"] == fixture.model for doc in variants)
    assert len({doc["revisionRef"] for doc in variants}) == 3
    assert len({doc["assetSha256"] for doc in variants}) == 3
    jobs = [render(client, page(doc), direction=direction) for doc, direction in zip(
        variants, ("Neutral review", "Daylight material study", "Presentation lighting"), strict=True)]
    descendant = render(client, page(jobs[0]["document"]))
    review_page = {"runId": jobs[0]["document"]["runId"],
                   "assetSha256": jobs[0]["document"]["assetSha256"], "pageIndex": 0}
    reviewed = client.put("/api/document-annotations", json={"projectId": PROJECT_ID, **review_page,
        "baseRevisionSha256": None, "annotations": [stroke()], "comment": "Check the doorway."})
    assert reviewed.status_code == 200, reviewed.text
    review = reviewed.json()
    assert fixture.repository.read_head() == fixture.head
    assert fixture.repository.read_design_branches() == fixture.branches

    # A style variant is not a replacement decision. It neither changes the
    # architecture nor invalidates siblings or inherits an old page review.
    changed_style = fixture.generate(drawingId=variants[0]["drawingId"], dimensions=[],
        previousRevisionRef=variants[0]["revisionRef"], cutLineMm=.9)
    refreshed = render(client, page(changed_style))
    assert all(reread(client, job)["sourceState"] == "current" for job in [*jobs, descendant, refreshed])
    new_review = client.get("/api/document-annotations", params={
        "runId": refreshed["document"]["runId"], "assetSha256": refreshed["document"]["assetSha256"], "pageIndex": 0})
    assert new_review.status_code == 200 and new_review.json()["annotations"] == []

    # A real accepted design change outside all three views must remain current,
    # even though its Stage/source identity changed.
    wall = deepcopy(plans.room_edit()["entities"][1])
    wall["entity_id"] = "distant-wall"
    wall["fields"]["references"]["line"] = {
        "from": {"point": [0, 20]}, "to": {"point": [4, 20]}, "inward": [0, 1]}
    fixture.stage, fixture.model = fixture.commit_edit({"summary": "Add unrelated distant wall", "entities": [wall]})
    assert all(reread(client, job)["sourceState"] == "current" for job in [*jobs, descendant])

    # Moving the room's wall changes actual read geometry for each view.
    fixture.stage, fixture.model = fixture.commit_edit({"summary": "Move front wall",
        "parameters": [{"key": "front_shift", "value": .4}]})
    for job in [*jobs, descendant]:
        reading = reread(client, job)
        assert reading["sourceState"] == "outdated", reading
        assert reading["document"] == job["document"] and reading["resultAvailable"]
    # Existing review ink remains evidence against the exact old artifact; it
    # is not promoted to a review of the changed design or new representation.
    assert client.get("/api/document-annotations", params=review_page).json() == review
    reopened_adapter = renders.Adapter()
    reopened = create_app(fixture.settings, render_adapter=reopened_adapter)
    try:
        with TestClient(reopened) as reader:
            for job in [*jobs, descendant]:
                reading = reread(reader, job)
                assert reading["sourceState"] == "outdated"
                assert reading["request"] == job["request"]
            assert reader.get("/api/document-annotations", params=review_page).json() == review
        assert reopened_adapter.calls == []
    finally:
        reopened.state.render_jobs.shutdown()
        reopened.state.jobs.shutdown()
    assert fixture.repository.read_head() == fixture.head
    assert len(adapter.calls) == 5


def test_missing_drawing_host_makes_descendant_unavailable_without_rebinding(room):
    fixture, _ = room
    drawing = fixture.generate()  # The opening dimension has an explicit wall host.
    job = render(fixture.client, page(drawing))
    fixture.commit_edit({"summary": "Remove dimension host", "removeEntityIds": ["passage-wall"]})
    reading = reread(fixture.client, job)
    assert reading["sourceState"] == "unavailable", reading
    assert "anchor" in reading["sourceStateReason"]
    assert reading["document"] == job["document"] and reading["resultAvailable"]


def test_one_status_vocabulary_for_drawing_render_and_upload_pages(room):
    fixture, _ = room
    client = fixture.client
    binding = bound_project(fixture.app.state)
    stage_ref, model = fixture.stage["stageRef"], fixture.model
    drawing = fixture.generate(dimensions=[])
    kept = fixture.generate(drawingId="kept-plan", dimensions=[], follow="frozen")
    rendered = render(client, page(drawing))
    view = save_document(binding, model["runId"], "exact-view.png", "image/png",
                         base64.b64encode(renders.png("green")).decode(), model_source=ModelSource.from_dict(model),
                         source_stage_ref=stage_ref, view_recipe={"kind": "model-view", "camera": {"projection": "orthographic"}})
    upload = renders.upload(client)
    pages = {"drawing": page(drawing), "kept": page(kept), "render": page(rendered["document"]),
             "view": {"runId": view.run_id, "assetSha256": view.asset_sha256, "revisionRef": None, "pageIndex": 0},
             "upload": upload}

    def statuses(frozen=()):
        reads = RepresentationReads(binding)  # one Working Head and one document listing, as one request reads them
        return {name: representation_status(binding, key(ref), frozen=name in frozen, reads=reads)
                for name, ref in pages.items()}

    kept_upload = publication.page("kept-upload", "Kept", upload)
    kept_upload["elements"][1]["frozen"] = True
    saved = client.put("/api/publication", json=publication.request(
        [*(publication.page(name, name, ref) for name, ref in pages.items()), kept_upload]))
    assert saved.status_code == 200, saved.text

    def published():
        return {row["elementId"].removesuffix("-image"): (row["status"], row["replacement"])
                for row in client.get("/api/publication").json()["sources"]}

    before = statuses()
    assert {name: status.state for name, status in before.items()} == {
        "drawing": "current", "kept": "frozen", "render": "current", "view": "current", "upload": "current"}
    # Each page names only the exact inputs its own owner retained.
    assert before["drawing"].upstream == ({"modelSource": model}, {"sourceStageRef": stage_ref})
    assert before["render"].upstream == ({"source": page(drawing)},)
    assert before["upload"].upstream == ()
    # Publish reads the same answers in its own words; a drawing kept on its
    # version is a current source, and only Publish's own freeze says frozen.
    assert published() == {name: ("current", None) for name in pages} | {"kept-upload": ("frozen", None)}

    # A new Stage outside every crop, and a newer registered page for the upload.
    fixture.stage, fixture.model = fixture.commit_edit(distant_wall())
    newer = renders.upload(client, "red", replacesPages=[upload | {"newPageIndex": 0}])
    after = statuses()
    assert {name: status.state for name, status in after.items()} == {
        "drawing": "current", "kept": "frozen", "render": "current", "view": "outdated", "upload": "outdated"}
    # Each answer is its owner's: the plan's read set, the render's retained
    # request, the viewed model state, the page's own replacement.
    assert after["drawing"].reason == fixture.status(drawing)["detail"]
    assert reread(client, rendered)["sourceState"] == after["render"].state
    assert after["upload"].replacement == key(newer)
    assert statuses(frozen={"upload"})["upload"].state == "frozen"
    assert published() == {name: ("current", None) for name in pages} | {
        "view": ("stale", None), "upload": ("stale", newer), "kept-upload": ("frozen", newer)}

    # A page whose own bytes can no longer be read is unavailable; Publish says missing.
    digest = upload["assetSha256"]
    fixture.repository.layout.resolve_relative(f"objects/sha256/{digest[:2]}/{digest}").write_bytes(b"damaged")
    lost = representation_status(binding, key(upload))
    assert (lost.state, lost.page_available) == ("unavailable", False)
    assert published()["upload"][0] == published()["kept-upload"][0] == "missing"
    assert fixture.repository.read_head() == fixture.head
