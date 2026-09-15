from __future__ import annotations

import os
from pathlib import Path

from monkeyhub_api.artifact_watcher import ArtifactBinding, ArtifactWatcher


def _binding(path: Path, *, artifact_id: str = "plan-02") -> ArtifactBinding:
    return ArtifactBinding(
        project_id="project-a",
        source_id="drawing:level-02",
        artifact_id=artifact_id,
        path=path,
        label="Level 02 Plan",
        state="candidate",
    )


def _settle(watcher: ArtifactWatcher, binding: ArtifactBinding) -> None:
    assert watcher.scan((binding,)) == ()
    assert watcher.scan((binding,)) == ()


def test_stable_change_emits_once_after_baseline(tmp_path: Path) -> None:
    path = tmp_path / "plan.png"
    path.write_bytes(b"png-one")
    watcher = ArtifactWatcher()
    binding = _binding(path)
    _settle(watcher, binding)

    path.write_bytes(b"png-two")
    assert watcher.scan((binding,)) == ()
    updates = watcher.scan((binding,))
    assert len(updates) == 1
    update = updates[0]
    assert update.project_id == "project-a"
    assert update.source_id == "drawing:level-02"
    assert update.artifact_id == "plan-02"
    assert update.label == "Level 02 Plan"
    assert update.state == "candidate"
    assert update.previous_sha256 != update.sha256
    assert watcher.scan((binding,)) == ()


def test_touch_without_content_change_does_not_emit(tmp_path: Path) -> None:
    path = tmp_path / "plan.png"
    path.write_bytes(b"same")
    watcher = ArtifactWatcher()
    binding = _binding(path)
    _settle(watcher, binding)

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert watcher.scan((binding,)) == ()
    assert watcher.scan((binding,)) == ()


def test_bursty_rewrite_coalesces_to_latest_stable_bytes(tmp_path: Path) -> None:
    path = tmp_path / "plan.png"
    path.write_bytes(b"base")
    watcher = ArtifactWatcher()
    binding = _binding(path)
    _settle(watcher, binding)

    path.write_bytes(b"partial")
    assert watcher.scan((binding,)) == ()
    path.write_bytes(b"final-complete")
    assert watcher.scan((binding,)) == ()
    updates = watcher.scan((binding,))
    assert len(updates) == 1
    assert updates[0].size == len(b"final-complete")
    assert watcher.scan((binding,)) == ()


def test_invalid_candidate_never_replaces_last_good_digest(tmp_path: Path) -> None:
    path = tmp_path / "plan.png"
    path.write_bytes(b"good-v1")
    watcher = ArtifactWatcher(validator=lambda _binding, data: data.startswith(b"good"))
    binding = _binding(path)
    _settle(watcher, binding)

    path.write_bytes(b"corrupt")
    assert watcher.scan((binding,)) == ()
    assert watcher.scan((binding,)) == ()

    path.write_bytes(b"good-v2")
    assert watcher.scan((binding,)) == ()
    updates = watcher.scan((binding,))
    assert len(updates) == 1
    assert updates[0].previous_sha256 != updates[0].sha256


def test_unbound_file_is_never_seen_and_removed_binding_forgets_identity(tmp_path: Path) -> None:
    bound = tmp_path / "bound.png"
    other = tmp_path / "temporary.png"
    bound.write_bytes(b"v1")
    other.write_bytes(b"noise")
    watcher = ArtifactWatcher()
    binding = _binding(bound)
    _settle(watcher, binding)

    other.write_bytes(b"more-noise")
    assert watcher.scan((binding,)) == ()
    assert watcher.scan((binding,)) == ()

    assert watcher.scan(()) == ()
    bound.write_bytes(b"v2")
    # Re-binding starts a fresh baseline instead of fabricating an update.
    _settle(watcher, binding)


def test_missing_file_during_atomic_save_keeps_previous_baseline(tmp_path: Path) -> None:
    path = tmp_path / "plan.png"
    path.write_bytes(b"v1")
    watcher = ArtifactWatcher()
    binding = _binding(path)
    _settle(watcher, binding)

    path.unlink()
    assert watcher.scan((binding,)) == ()
    path.write_bytes(b"v2")
    assert watcher.scan((binding,)) == ()
    updates = watcher.scan((binding,))
    assert len(updates) == 1
