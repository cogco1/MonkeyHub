"""Real P036 and HTTP control flow with injected image transport, not a live call."""
import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import threading
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.render_contract import RenderCapability, RenderOutput, RenderProviderError
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings, SettingsError
from archflow_studio_api.application.artifacts import save_document
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_RENDER_JOB

from .support import make_project, PROJECT_ID


def png(color="blue"):
    stream = BytesIO()
    Image.new("RGB", (24, 16), color).save(stream, format="PNG")
    return stream.getvalue()


class Adapter:
    def __init__(self):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.failure = None

    def capability(self):
        return RenderCapability("test-image", "Test image adapter", "test-model", True)

    def generate(self, request):
        self.calls.append(request)
        self.entered.set()
        assert self.release.wait(5), "test did not release provider"
        if self.failure:
            raise self.failure
        return RenderOutput(png("green"), "image/png", input_tokens=17, output_tokens=9)


@pytest.fixture
def setup(tmp_path):
    repository, _ = make_project(tmp_path)
    adapter = Adapter()
    app = create_app(StudioSettings(project_dir=tmp_path / PROJECT_ID, cad_export="off", monitor_dir=tmp_path / "monitor"),
                     render_adapter=adapter)
    client = TestClient(app)
    yield client, app, repository, adapter
    adapter.release.set()
    app.state.render_jobs.shutdown()
    app.state.jobs.shutdown()
    client.close()


def upload(client, color="blue", **extra):
    response = client.post("/api/documents", json={"projectId": PROJECT_ID, "fileName": "source.png", "mimeType": "image/png",
                                                "contentBase64": base64.b64encode(png(color)).decode(), **extra})
    assert response.status_code == 201, response.text
    doc = response.json()
    return {key: doc[key] for key in ("runId", "assetSha256", "revisionRef")} | {"pageIndex": 0}


def request(source, **extra):
    return {"projectId": PROJECT_ID, "requestId": str(uuid4()), "providerId": "test-image", "source": source,
            "references": [], "direction": "Soft morning light", **extra}


def submit(client, payload):
    response = client.post("/api/render/jobs", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def finished(client, job):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get("/api/render/jobs/" + job["jobId"])
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] not in ("queued", "running"):
            return result
        time.sleep(0.02)
    pytest.fail("render did not finish")


def test_same_request_only_dispatches_once_and_conflicts_on_any_change(setup):
    client, _, _, adapter = setup
    source = upload(client)
    refs = [upload(client, "red"), upload(client, "yellow")]
    payload = request(source, references=refs)
    adapter.release.clear()
    with ThreadPoolExecutor(4) as pool:
        jobs = list(pool.map(lambda _: submit(client, payload), range(4)))
    assert len({job["jobId"] for job in jobs}) == 1
    assert adapter.entered.wait(2)
    assert len(adapter.calls) == 1
    assert [image.ref.asset_sha256 for image in adapter.calls[0].references] == [ref["assetSha256"] for ref in refs]
    conflict = client.post("/api/render/jobs", json=payload | {"references": list(reversed(refs))})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "RENDER_REQUEST_CONFLICT"
    adapter.release.set()
    assert finished(client, jobs[0])["status"] == "succeeded"
    assert submit(client, payload)["status"] == "succeeded"
    assert len(adapter.calls) == 1


def test_same_pixels_have_distinct_runs_documents_and_head_is_unchanged(setup):
    client, app, repository, adapter = setup
    source = upload(client)
    head = repository.read_head()
    first = finished(client, submit(client, request(source)))
    second = finished(client, submit(client, request(source, direction="Evening light")))
    assert first["status"] == second["status"] == "succeeded"
    assert first["document"]["assetSha256"] == second["document"]["assetSha256"]
    assert first["document"]["runId"] != second["document"]["runId"]
    assert first["document"]["modelSource"] is None
    documents = client.get("/api/documents").json()["documents"]
    assert {job["document"]["runId"] for job in (first, second)} <= {doc["runId"] for doc in documents}
    doc = first["document"]
    response = client.get(f'/api/documents/{doc["assetSha256"]}/bytes', params={"runId": doc["runId"]})
    assert response.status_code == 200 and response.content == png("green")
    assert repository.read_head() == head
    assert first["costUsd"] is None and first["inputTokens"] == 17
    assert len(adapter.calls) == 2
    # Diagnostics contain real adapter counters but never prompt or image bytes.
    logs = list(app.state.settings.monitor_dir.rglob("*.jsonl"))
    assert logs
    text = "\n".join(path.read_text() for path in logs)
    assert '"image_render"' in text and "Soft morning light" not in text


def test_reopen_restores_completed_attempt_without_dispatch(setup):
    client, app, _, _ = setup
    payload = request(upload(client))
    first = finished(client, submit(client, payload))
    other = Adapter()
    reopened = create_app(app.state.settings, render_adapter=other)
    try:
        with TestClient(reopened) as reader:
            result = reader.get("/api/render/jobs/" + first["jobId"]).json()
            assert result["document"] == first["document"]
            assert submit(reader, payload)["status"] == "succeeded"
            assert other.calls == []
    finally:
        reopened.state.render_jobs.shutdown()


def test_reopen_running_is_unknown_and_read_or_retry_never_replays(setup):
    client, app, _, adapter = setup
    payload = request(upload(client))
    adapter.release.clear()
    job = submit(client, payload)
    assert adapter.entered.wait(2)
    other = Adapter()
    reopened = create_app(app.state.settings, render_adapter=other)
    try:
        with TestClient(reopened) as reader:
            assert reader.get("/api/render/jobs/" + job["jobId"]).json()["status"] == "unknown"
            assert submit(reader, payload)["status"] == "unknown"
            assert reader.get("/api/render/jobs").json()["jobs"][0]["status"] == "unknown"
            assert other.calls == []
    finally:
        adapter.release.set()
        reopened.state.render_jobs.shutdown()
    assert finished(client, job)["status"] == "succeeded"


@pytest.mark.parametrize("outcome,code", [("failed", "provider_rejected"), ("unknown", "timeout")])
def test_provider_failure_keeps_prior_results_and_safe_error(setup, outcome, code):
    client, _, _, adapter = setup
    source = upload(client)
    old = finished(client, submit(client, request(source)))
    adapter.failure = RenderProviderError(outcome, code)
    payload = request(source)
    failed = finished(client, submit(client, payload))
    assert failed["status"] == outcome and failed["errorCode"] == code
    assert failed["document"] is None
    assert submit(client, payload)["status"] == outcome
    assert len(adapter.calls) == 2
    restored = client.get("/api/render/jobs/" + old["jobId"]).json()
    assert restored["status"] == "succeeded" and restored["resultAvailable"]


def test_replaced_source_is_outdated_without_rebinding(setup):
    client, _, _, _ = setup
    source = upload(client)
    old = finished(client, submit(client, request(source)))
    upload(client, "red", replacesPages=[source | {"newPageIndex": 0}])
    result = client.get("/api/render/jobs/" + old["jobId"]).json()
    assert result["sourceState"] == "outdated"
    assert result["request"]["source"] == source
    assert result["document"] == old["document"]


def test_invalid_source_rejected_and_missing_bytes_are_explicit(setup):
    client, _, repository, adapter = setup
    source = upload(client)
    for change in ({"pageIndex": 1}, {"revisionRef": "not-a-revision"}, {"assetSha256": "0" * 64}):
        response = client.post("/api/render/jobs", json=request(source | change))
        assert response.status_code == 422
    assert not adapter.calls
    old = finished(client, submit(client, request(source)))
    digest = source["assetSha256"]
    repository.layout.resolve_relative(f"objects/sha256/{digest[:2]}/{digest}").write_bytes(b"changed")
    result = client.get("/api/render/jobs/" + old["jobId"]).json()
    assert result["sourceState"] == "unavailable" and result["resultAvailable"]
    assert client.post("/api/render/jobs", json=request(source)).status_code == 409
    assert len(adapter.calls) == 1


def test_unconfigured_capabilities_and_cross_project_attempts(setup, tmp_path):
    client, _, _, adapter = setup
    source = upload(client)
    assert client.post("/api/render/jobs", json=request(source, projectId="other")).status_code == 403
    assert not adapter.calls
    job = finished(client, submit(client, request(source)))
    root = tmp_path / "separate"
    make_project(root)
    other_app = create_app(StudioSettings(project_dir=root / PROJECT_ID, cad_export="off"))
    with TestClient(other_app) as other:
        assert other.get("/api/render/capabilities").json() == {"providers": []}
        assert other.get("/api/render/jobs/" + job["jobId"]).status_code == 404
        assert other.post("/api/render/jobs", json=request(source)).status_code == 503


def test_health_remains_responsive_while_provider_is_waiting(setup):
    client, app, _, adapter = setup
    adapter.release.clear()
    submit(client, request(upload(client)))
    assert adapter.entered.wait(2)
    assert client.get("/api/health").status_code == 200
    app.state.render_jobs.stop_accepting()
    assert client.post("/api/render/jobs", json=request(upload(client, "red"))).status_code == 503
    assert len(adapter.calls) == 1


def test_render_settings_are_environment_only_and_secret_is_not_repr(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHFLOW_STUDIO_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("ARCHFLOW_STUDIO_RENDER_PROVIDER", "gemini")
    monkeypatch.setenv("ARCHFLOW_STUDIO_RENDER_MODEL", "configured-image-model")
    monkeypatch.setenv("ARCHFLOW_STUDIO_RENDER_API_KEY", "test-secret-do-not-print")
    settings = StudioSettings.from_env()
    assert settings.render_provider == "gemini" and settings.render_model == "configured-image-model"
    assert settings.render_api_key == "test-secret-do-not-print"
    assert settings.render_api_key not in repr(settings)
    for value in ("nan", "0", "301", "bad"):
        monkeypatch.setenv("ARCHFLOW_STUDIO_RENDER_TIMEOUT_S", value)
        with pytest.raises(SettingsError):
            StudioSettings.from_env()


def test_legacy_native_result_is_readable_without_inventing_ai_request(setup):
    client, app, repository, adapter = setup
    binding = bound_project(app.state)
    job_id = "render-" + uuid4().hex
    run = repository.create_run(job_id)
    document = save_document(binding, job_id, "native.png", "image/png", base64.b64encode(png()).decode())
    repository.put_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=job_id),
                        record_kind=STUDIO_RENDER_JOB, payload={
                            "schema": "StudioRenderJob@1", "projectId": PROJECT_ID, "jobId": job_id,
                            "instance": "old-runtime", "sequence": 1, "status": "succeeded", "createdAt": "2026-09-21T00:00:00Z",
                            "renderer": "monkeyhub-three-webgl2-v1", "documentSha256": document.asset_sha256,
                        })
    result = client.get("/api/render/jobs/" + job_id).json()
    assert result["status"] == "succeeded" and result["resultAvailable"]
    assert result["request"] is None and result["execution"] == "browser-native"
    assert client.get("/api/render/jobs").json()["jobs"][0] == result
    assert not adapter.calls


def test_result_survives_a_lost_completion_transition(setup, monkeypatch):
    client, app, _, _ = setup
    original = app.state.render_jobs._transition
    def interrupted(binding, row, **values):
        if values.get("status") == "succeeded":
            raise OSError("synthetic persistence interruption")
        return original(binding, row, **values)
    monkeypatch.setattr(app.state.render_jobs, "_transition", interrupted)
    result = finished(client, submit(client, request(upload(client))))
    assert result["status"] == "succeeded" and result["resultAvailable"]


def test_provider_preflight_rejection_does_not_count_as_a_model_call(setup):
    import json
    client, app, _, adapter = setup
    adapter.failure = RenderProviderError("failed", "unsupported_input")
    result = finished(client, submit(client, request(upload(client))))
    assert result["status"] == "failed"
    app.state.render_jobs.shutdown()
    events = [json.loads(line) for path in app.state.settings.monitor_dir.rglob("*.jsonl") for line in path.read_text().splitlines()]
    observed = [event for event in events if event.get("phase") == "image_render"]
    assert len(observed) == 1 and observed[0]["model_call"] is False


def test_real_gemini_adapter_mock_transport_through_http_and_p036(setup):
    from archflow_studio_api.render_adapters.gemini import GeminiImageRenderAdapter
    from .test_render_gemini_adapter import RecordingTransport, final_image, image_bytes

    client, app, repository, _ = setup
    source, ref = upload(client), upload(client, "red")
    output = image_bytes("JPEG", (1024, 1024))
    transport = RecordingTransport({
        "id": "test-interaction", "status": "completed", "model": "gemini-3-pro-image",
        "steps": [{"type": "model_output", "content": [final_image(output)]}],
        "usage": {"total_input_tokens": 12, "total_output_tokens": 34},
    })
    app.state.render_jobs.adapter = GeminiImageRenderAdapter(api_key="test-key", model="gemini-3-pro-image", transport=transport)
    capability = client.get("/api/render/capabilities").json()["providers"][0]
    assert capability["maxReferences"] == 3 and capability["available"]
    head = repository.read_head()
    payload = request(source, providerId="gemini", references=[ref])
    result = finished(client, submit(client, payload))
    assert result["status"] == "succeeded" and result["document"]["mimeType"] == "image/jpeg"
    assert result["providerRequestId"] == "test-interaction"
    assert result["inputTokens"] == 12 and result["costUsd"] is None
    assert result["request"]["references"] == [ref]
    assert result["document"]["viewRecipe"]["request"]["source"] == source
    assert submit(client, payload)["status"] == "succeeded" and len(transport.calls) == 1
    assert repository.read_head() == head
