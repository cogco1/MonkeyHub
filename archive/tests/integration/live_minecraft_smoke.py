"""Explicit P002 live smoke against a disposable gemini-minecraft world.

This script is intentionally excluded from unittest discovery.  It requires an
already-running localhost bridge and an explicit world-write flag.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from archive.archflow.adapters.minecraft_mcp import MinecraftMcpAdapter, MinecraftMcpConfig, MinecraftMcpFailure
from archive.archflow.commit.store import InMemoryStateStore
from archive.archflow.runtime.walking_skeleton import initial_state
from archive.archflow.workspace.manager import WorkspaceManager


SMOKE_PLAN = {
    "version": 2,
    "summary": "ArchFlow P002 five by five disposable smoke floor",
    "coordMode": "player",
    "offset": {"x": 4, "y": 0, "z": 4},
    "snapToGround": True,
    "clearVegetation": True,
    "autoFix": True,
    "cuboids": [
        {
            "name": "p002_smoke_floor",
            "block": "minecraft:smooth_stone",
            "from": {"x": 0, "y": 0, "z": 0},
            "to": {"x": 4, "y": 0, "z": 4},
        }
    ],
}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--project-root", type=Path, required=True)
    result.add_argument("--evidence-root", type=Path, required=True)
    result.add_argument("--allow-world-write", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.allow_world_write:
        raise SystemExit(
            "refusing live smoke without explicit --allow-world-write"
        )
    project_root = args.project_root.resolve()
    node = Path(r"C:\Program Files\nodejs\node.exe")
    sidecar = project_root / "run-mcp-sidecar-node.js"
    if not node.is_file() or not sidecar.is_file():
        raise SystemExit("Node sidecar command is not loadable")

    evidence_root = args.evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    command = (
        str(node),
        str(sidecar),
        "--project-root",
        str(project_root),
    )
    state = initial_state(
        "Build one minimal five by five smoke floor in a disposable world",
        run_id="run-p002-live",
    )
    store = InMemoryStateStore(state)
    before = store.read()

    live_workspace = WorkspaceManager(evidence_root / "success").fork(before)
    live_adapter = MinecraftMcpAdapter(
        MinecraftMcpConfig(
            command=command,
            timeout_seconds=15,
            allow_world_write=True,
            capture_after_build=True,
        )
    )
    artifact = live_adapter.build(before, live_workspace, SMOKE_PLAN)
    artifact_path = live_workspace.root / "minecraft-voxel-artifact.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    screenshot_path = live_workspace.root / "minecraft-view.png"
    preview_id = payload["preview"].get("planId")
    execution_id = payload["execution"].get("planId")
    if not preview_id or preview_id != execution_id:
        raise RuntimeError("preview/execute plan parity was not preserved")
    if not screenshot_path.is_file():
        raise RuntimeError("live capture did not produce a PNG")
    if store.read() != before:
        raise RuntimeError("candidate build mutated canonical state")

    failure_workspace = WorkspaceManager(evidence_root / "failure").fork(before)
    offline_command = (
        *command,
        "--bridge-url",
        "http://127.0.0.1:65534",
    )
    failure_adapter = MinecraftMcpAdapter(
        MinecraftMcpConfig(
            command=offline_command,
            timeout_seconds=5,
        )
    )
    failure: MinecraftMcpFailure | None = None
    try:
        failure_adapter.preview(before, failure_workspace, SMOKE_PLAN)
    except MinecraftMcpFailure as exc:
        failure = exc
    if failure is None or failure.code != "BRIDGE_UNAVAILABLE":
        raise RuntimeError("offline bridge did not yield BRIDGE_UNAVAILABLE")
    if store.read() != before:
        raise RuntimeError("external failure mutated canonical state")

    manifest = {
        "schema": "P002LiveSmokeEvidence@1",
        "world": "ArchFlow V4 Disposable",
        "base_state": {
            "run_id": before.ref.run_id,
            "version": before.ref.version,
        },
        "after_failure_state": {
            "run_id": store.read().ref.run_id,
            "version": store.read().ref.version,
        },
        "artifact_id": artifact.artifact_id,
        "artifact_uri": artifact_path.as_uri(),
        "artifact_sha256": artifact.sha256,
        "screenshot_uri": screenshot_path.as_uri(),
        "preview_plan_id": preview_id,
        "execution_plan_id": execution_id,
        "failure_code": failure.code,
        "failure_receipt_uri": failure.receipt_path.as_uri(),
        "canonical_state_mutated": False,
    }
    manifest_path = evidence_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
