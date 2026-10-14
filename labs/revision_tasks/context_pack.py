"""The ContextPack@1 MonkeyHub would hand an agent for a record, read from a disposable project.

The record is authored where the runner reads it (``input/runner/state-record.json``)
in a new project under an explicit scratch root, and the Project Runtime is
created in process (``create_app`` + ``TestClient``) with CAD export off. Only
the read-only ``POST /api/intents/context`` is called: no proposal, candidate
or model provider, and nothing is written to the project by the read itself.
Production never retains a pack; the harness keeps its text and hash.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from archflow.contracts.canonical import canonical_json
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord

RECORD_PATH = "input/runner/state-record.json"


class ContextPackError(RuntimeError):
    """The runtime refused the read; ``status`` and ``body`` are its answer."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"context read refused ({status}): {body[:400]}")
        self.status = status


def read_context_pack(record: StateRecord, *, root: Path, utterance: str, element_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    """The pack for ``record`` focused on ``element_ids``; ``root`` must be a scratch directory the caller owns."""

    from fastapi.testclient import TestClient
    from project_runtime.main import create_app
    from project_runtime.settings import StudioSettings

    project_dir = root / record.project_id
    if project_dir.exists():
        shutil.rmtree(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    repository = FilesystemProjectRepository.initialize(project_dir, project_id=record.project_id,
                                                        initial_state={"project_id": record.project_id, "version": 0})
    path = repository.layout.resolve_relative(RECORD_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    app = create_app(StudioSettings(cad_export="off", project_dir=project_dir, cache_dir=root / "cache"))
    with TestClient(app) as client:
        state = client.get("/api/state")
        if state.status_code != 200:
            raise ContextPackError(state.status_code, state.text)
        body: dict[str, Any] = {"projectId": record.project_id, "stateDigest": state.json()["stateDigest"], "utterance": utterance}
        if element_ids:
            body["elementIds"] = list(element_ids)
        response = client.post("/api/intents/context", json=body)
        if response.status_code != 200:
            raise ContextPackError(response.status_code, response.text)
        return response.json()


def pack_digest(pack: dict[str, Any] | None) -> str | None:
    return None if pack is None else hashlib.sha256(canonical_json(pack).encode("utf-8")).hexdigest()
