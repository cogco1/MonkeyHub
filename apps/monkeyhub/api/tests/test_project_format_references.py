from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path

import pytest

from archflow.project.refs import ProjectVersionRef, record_file_name
from archflow.project.repository import (
    LEGACY_FORMAT_VERSION,
    FilesystemProjectRepository,
    ProjectIntegrityError,
    _json_bytes,
    _sha256,
    _write_immutable,
)
from monkeyhub_api.project_format_references import (
    analyze_project_version_references,
    collect_project_version_references,
)
from tools.create_project import main as create_project_main


def test_collector_requires_exact_project_version_shape() -> None:
    digest = "a" * 64
    payload = {
        "base": {"project_id": "p", "version": 3, "state_sha256": digest},
        "artifact": {"project_id": "p", "relative_path": "x", "sha256": digest,
                     "media_type": "application/json"},
        "almost": {"project_id": "p", "version": 3, "state_sha256": digest,
                   "extra": True},
        "list": [{"project_id": "p", "version": 1, "state_sha256": "b" * 64}],
    }
    refs = collect_project_version_references(payload, file="runs/r/run.json")
    assert [(ref.json_path, ref.version) for ref in refs] == [
        ("/base", 3),
        ("/list/0", 1),
    ]


def test_collector_rejects_invalid_digest_and_bool_version() -> None:
    payload = {
        "bad_digest": {"project_id": "p", "version": 1, "state_sha256": "nope"},
        "bool_version": {"project_id": "p", "version": True, "state_sha256": "a" * 64},
    }
    assert collect_project_version_references(payload, file="x.json") == ()


def _legacy_project(root: Path, project_id: str = "legacy") -> FilesystemProjectRepository:
    for directory in ("canonical", "events", "runs"):
        (root / directory).mkdir(parents=True)
    _write_immutable(
        root / "project.json",
        _json_bytes({
            "schema": "ArchFlowProject@1",
            "project_id": project_id,
            "format_version": LEGACY_FORMAT_VERSION,
        }),
    )
    snapshot_bytes = _json_bytes({
        "schema": "CanonicalSnapshot@1",
        "project_id": project_id,
        "version": 0,
        "parent": None,
        "state": {"phase": "design"},
    })
    snapshot_digest = _sha256(snapshot_bytes)
    snapshot_path = f"canonical/{record_file_name('state-v000000', snapshot_digest)}"
    _write_immutable(root / snapshot_path, snapshot_bytes)
    version = ProjectVersionRef(project_id, 0, snapshot_digest)
    event_bytes = _json_bytes({
        "schema": "ProjectEvent@1",
        "project_id": project_id,
        "event_type": "project.initialized",
        "decision": "accepted",
        "run_id": None,
        "from": None,
        "to": version.to_dict(),
        "previous_event": None,
        "decision_receipt": None,
    })
    event_digest = _sha256(event_bytes)
    event_path = f"events/{record_file_name('event-v000000', event_digest)}"
    _write_immutable(root / event_path, event_bytes)
    _write_immutable(
        root / "HEAD",
        _json_bytes({
            "schema": "ProjectHead@1",
            "project_id": project_id,
            "current": version.to_dict(),
            "snapshot": {"relative_path": snapshot_path, "sha256": snapshot_digest,
                         "media_type": "application/json"},
            "event": {"relative_path": event_path, "sha256": event_digest,
                      "media_type": "application/json"},
        }),
    )
    repository = FilesystemProjectRepository.open(root)
    # Planning intentionally refuses untouched legacy projects because guarded
    # reads would create these advisory locks. A normal historical project has
    # them already; initialize_authored_inputs creates them without changing HEAD.
    repository.initialize_authored_inputs(
        expected_head=repository.read_head(),
        expected_record=None,
        authored_record={"schema": "StateRecord@1", "draft": "initial"},
        seat_pack={"schema": "SeatPack@1", "seats": []},
    )
    return repository


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path.read_bytes())
        for path in root.rglob("*") if path.is_file()
    }


def test_legacy_scan_maps_head_and_event_refs_without_writing_project() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = _legacy_project(Path(temporary) / "legacy")
        root = repository.layout.root
        before = _fingerprint(root)
        report = analyze_project_version_references(root)
        assert report.project_id == "legacy"
        assert report.source_format_version == 1
        assert report.target_format_version == 2
        locations = {(ref.file, ref.json_path) for ref in report.references}
        assert ("HEAD", "/current") in locations
        assert any(file.startswith("events/") and pointer == "/to"
                   for file, pointer in locations)
        assert _fingerprint(root) == before


def test_plan_migration_command_prints_legacy_reference_locations_without_writing() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = _legacy_project(Path(temporary) / "legacy")
        root = repository.layout.root
        before = _fingerprint(root)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = create_project_main(["--project", str(root), "--plan-migration"])
        text = output.getvalue()

        assert result == 0
        assert "Legacy ProjectVersionRef scan:" in text
        assert "[project.repository] HEAD/current -> version 0" in text
        assert "[project.repository] events/" in text
        assert "/to -> version 0" in text
        assert "Location evidence only:" in text
        assert "owner-unconfirmed retained payloads" in text
        assert _fingerprint(root) == before


def test_scan_refuses_current_project_as_not_applicable(tmp_path: Path) -> None:
    repository = FilesystemProjectRepository.initialize(
        tmp_path / "current",
        project_id="current",
        initial_state={"phase": "design"},
    )
    with pytest.raises(ProjectIntegrityError, match="NOT_APPLICABLE"):
        analyze_project_version_references(repository.layout.root)
