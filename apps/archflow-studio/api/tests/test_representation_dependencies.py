"""Real Drawing/P036 inputs and Render dependency propagation; image transport is injected."""

from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from archflow.adapters.occt_backend import occt_available
from archflow_studio_api.application.artifacts import _page_replacements, list_documents
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.main import create_app

from . import test_drawing_plans as plans
from . import test_rendering as renders
from .support import PROJECT_ID
from .test_document_annotations import stroke

setup = renders.setup


def page(document):
    return {key: document[key] for key in ("runId", "assetSha256", "revisionRef")} | {"pageIndex": 0}


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


def test_a_live_rebuild_on_a_new_source_outdates_its_render_and_leads_to_the_new_page(room):
    fixture, adapter = room
    client = fixture.client
    drawing = fixture.generate(dimensions=[])
    job = render(client, page(drawing))
    # An accepted design change outside the view leaves the drawing's read
    # inputs, and so its render, current.
    wall = deepcopy(plans.room_edit()["entities"][1])
    wall["entity_id"] = "distant-wall"
    wall["fields"]["references"]["line"] = {
        "from": {"point": [0, 20]}, "to": {"point": [4, 20]}, "inward": [0, 1]}
    fixture.stage, fixture.model = fixture.commit_edit({"summary": "Add unrelated distant wall", "entities": [wall]})
    assert reread(client, job)["sourceState"] == "current"

    # Following the new source replaces the page the render was made from.
    rebuilt = fixture.generate(previousRevisionRef=drawing["revisionRef"], dimensions=[], follow="live")
    assert rebuilt["modelSource"] == fixture.model
    assert rebuilt["replacesPages"] == [page(drawing) | {"newPageIndex": 0}]
    reading = reread(client, job)
    assert reading["sourceState"] == "outdated", reading
    assert "replacement" in reading["sourceStateReason"]
    assert reading["document"] == job["document"] and reading["resultAvailable"]
    # The relation Render's source update follows names the new page.
    replacements = _page_replacements(list_documents(bound_project(fixture.app.state)))
    assert replacements[plans.page_of(drawing)] == plans.page_of(rebuilt)
    assert fixture.repository.read_head() == fixture.head
    assert len(adapter.calls) == 1


def test_missing_drawing_host_makes_descendant_unavailable_without_rebinding(room):
    fixture, _ = room
    drawing = fixture.generate()  # The opening dimension has an explicit wall host.
    job = render(fixture.client, page(drawing))
    fixture.commit_edit({"summary": "Remove dimension host", "removeEntityIds": ["passage-wall"]})
    reading = reread(fixture.client, job)
    assert reading["sourceState"] == "unavailable", reading
    assert "anchor" in reading["sourceStateReason"]
    assert reading["document"] == job["document"] and reading["resultAvailable"]
