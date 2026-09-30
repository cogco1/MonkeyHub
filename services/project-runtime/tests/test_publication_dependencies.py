"""Publication follows existing Drawing/Render dependencies without rewriting its layout."""

from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from archflow.adapters.occt_backend import occt_available
from archflow_studio_api.main import create_app

from . import test_drawing_plans as plans
from . import test_publications as publication
from . import test_rendering as renders
from .support import PROJECT_ID

setup = renders.setup


def save(client, pages, base=None):
    response = client.put("/api/publication", json=publication.request(pages, base))
    assert response.status_code == 200, response.text
    return response.json()


def statuses(client):
    response = client.get("/api/publication")
    assert response.status_code == 200, response.text
    return {row["elementId"]: row["status"] for row in response.json()["sources"]}


def replacements(client):
    response = client.get("/api/publication")
    assert response.status_code == 200, response.text
    return {row["elementId"]: row["replacement"] for row in response.json()["sources"]}


@pytest.fixture
def room():
    if not occt_available():
        pytest.skip("cadquery-ocp is optional")
    fixture = plans.CutPlanTests()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.app.state.render_jobs.shutdown()
        fixture.app.state.jobs.shutdown()
        fixture.doCleanups()


def test_drawing_changes_stale_live_page_and_leave_frozen_output_and_layout_intact(room):
    client = room.client
    drawing = room.generate(dimensions=[])
    live = publication.page("live", "Keep this authored judgment", drawing)
    frozen = publication.page("frozen", "Keep the historical example", drawing)
    frozen["elements"][1]["frozen"] = True
    saved = save(client, [live, frozen])
    original_pages = deepcopy(saved["pages"])
    assert statuses(client) == {"live-image": "current", "frozen-image": "frozen"}
    export_request = {"projectId": PROJECT_ID, "revisionSha256": saved["revisionSha256"], "format": "pdf"}
    old_pdf = client.post("/api/publication/export", json=export_request)
    assert old_pdf.status_code == 200

    # Advancing the design branch does not by itself stale this Drawing crop.
    wall = deepcopy(plans.room_edit()["entities"][1])
    wall["entity_id"] = "distant-wall"
    wall["fields"]["references"]["line"] = {
        "from": {"point": [0, 20]}, "to": {"point": [4, 20]}, "inward": [0, 1]}
    room.stage, room.model = room.commit_edit({"summary": "Unrelated wall", "entities": [wall]})
    assert statuses(client) == {"live-image": "current", "frozen-image": "frozen"}
    room.stage, room.model = room.commit_edit({"summary": "Move source wall",
        "parameters": [{"key": "front_shift", "value": .4}]})
    assert statuses(client) == {"live-image": "stale", "frozen-image": "frozen"}
    assert client.get("/api/publication").json()["pages"] == original_pages
    assert client.post("/api/publication/export", json=export_request).content == old_pdf.content

    # The rebuild registers itself as the drawing's replacement (#291), so
    # Publish names the new page itself; nothing re-points the layout.
    rebuilt = room.generate(previousRevisionRef=drawing["revisionRef"], dimensions=[])
    expected = {"live-image": publication.source(rebuilt), "frozen-image": publication.source(rebuilt)}
    assert replacements(client) == expected
    assert statuses(client) == {"live-image": "stale", "frozen-image": "frozen"}
    current = client.get("/api/publication").json()
    assert current["pages"] == original_pages
    reopened = create_app(room.settings)
    try:
        with TestClient(reopened) as reader:
            assert reader.get("/api/publication").json() == current
            assert replacements(reader) == expected
            assert reader.post("/api/publication/export", json=export_request).content == old_pdf.content
    finally:
        reopened.state.render_jobs.shutdown()
        reopened.state.jobs.shutdown()
    # Updating only the live placement must leave the architect's composition
    # and historical frozen page intact, and must not advance the design.
    updated_pages = deepcopy(original_pages)
    updated_pages[0]["elements"][1]["source"] = publication.source(rebuilt)
    updated = save(client, updated_pages, saved["revisionSha256"])
    assert updated["pages"] == updated_pages
    assert statuses(client) == {"live-image": "current", "frozen-image": "frozen"}
    assert client.post("/api/publication/export", json=export_request).content == old_pdf.content
    assert client.get("/api/publication").json()["pages"] == updated_pages
    assert room.repository.read_head() == room.head


def test_render_reference_change_propagates_to_publication_but_keeps_frozen_history(setup):
    client, app, repository, adapter = setup
    source, reference = renders.upload(client), renders.upload(client, "red")
    first = renders.finished(client, renders.submit(client, renders.request(source, references=[reference])))
    second = renders.finished(client, renders.submit(client, renders.request(publication.source(first["document"]))))
    live = publication.page("live", document=second["document"])
    frozen = publication.page("frozen", document=second["document"])
    frozen["elements"][1]["frozen"] = True
    saved = save(client, [live, frozen])
    head = repository.read_head()
    renders.upload(client, "yellow", replacesPages=[reference | {"newPageIndex": 0}])
    assert statuses(client) == {"live-image": "stale", "frozen-image": "frozen"}
    assert client.get("/api/publication").json()["pages"] == saved["pages"]
    reopened = create_app(app.state.settings, render_adapter=renders.Adapter())
    try:
        with TestClient(reopened) as reader:
            assert statuses(reader) == {"live-image": "stale", "frozen-image": "frozen"}
    finally:
        reopened.state.render_jobs.shutdown()
        reopened.state.jobs.shutdown()
    assert repository.read_head() == head and len(adapter.calls) == 2
