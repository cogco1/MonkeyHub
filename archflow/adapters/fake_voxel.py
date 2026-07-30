"""A deterministic local artifact adapter for the P1 walking skeleton."""

from __future__ import annotations

import hashlib
import json

from archflow.state import ArtifactRef, CanonicalState
from archflow.workspace import WorkspaceRef


class FakeVoxelAdapter:
    """Writes only inside the supplied speculative workspace."""

    capability_id = "fake.voxel.build"

    def build(self, state: CanonicalState, workspace: WorkspaceRef) -> ArtifactRef:
        if state.goal is None:
            raise ValueError("FakeVoxelAdapter accepts compatibility test state only")
        artifact_path = (workspace.root / "building.voxel.json").resolve()
        artifact_path.relative_to(workspace.root.resolve())
        payload = {
            "schema": "FakeVoxelArtifact@1",
            "prompt": state.goal.prompt,
            "base_state": {
                "project_id": state.ref.project_id,
                "version": state.ref.version,
            },
            "declared_must": list(state.goal.must),
            "voxel_summary": {
                "occupied_cells": 128,
                "connected_components": 1,
                "loadable": True,
            },
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        artifact_path.write_bytes(encoded)
        digest = hashlib.sha256(encoded).hexdigest()
        return ArtifactRef(
            artifact_id=f"artifact-{digest[:20]}",
            uri=artifact_path.as_uri(),
            media_type="application/vnd.archflow.fake-voxel+json",
            sha256=digest,
        )
