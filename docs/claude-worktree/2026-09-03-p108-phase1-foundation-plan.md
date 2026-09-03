# P108 Phase 1 — /client /server /shared Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the formal vibe-modeling trees (`/client`, `/server`, `/shared`) with a FastAPI read-only
server over the P036 project repository, relocate the demo's seams by moving them, and ship a browser client
that browses projects/runs/records, renders the component tree, and views exported `.3dm` artifacts.

**Architecture:** FastAPI app (`server/`) delegates every state/geometry question to `archflow` (firewall-
enforced); Pydantic models in `shared/contracts/` are transport shapes only; the Vite/React/three.js client
(`client/web/`, moved from the demo) talks exclusively to the gateway. Read-only over existing runs — the
mutation engine, planner, intent parsing and export loop are later plans.

**Tech Stack:** Python 3.12, FastAPI + Pydantic + uvicorn (server), httpx (tests only), React 19 + three.js +
rhino3dm-wasm + Vite (client), `unittest` (server tests, matching repo convention).

**Scope note (spec coverage):** This plan covers spec §30 Phases 1–2 as re-scoped by the brief's mapping table
(state schema, component tree, dependency graph, villa fixture, exporter all EXIST in archflow — Phase 1–2 is
scaffolding plus wiring, not creation). Spec §8–§12, §17–§21, §23–§24 (mutation engine, planner, scheduler,
diff/leakage, intent, review UX, demos) are the next plans. §31's architecture.md lands here as
`docs/claude-worktree/2026-09-03-vibe-modeling-architecture.md`.

## Global Constraints

- Write scope: `client/`, `server/`, `shared/`, `apps/archflow-studio/` (harvest-by-move and marking ONLY),
  plus dated lane docs in `docs/claude-worktree/`. Nothing else — no `archflow/`, no `governance/`, no
  repo-root `tests/`, no repo-root `pyproject.toml`.
- Firewall (machine-enforced): in `server/` and `shared/`, never import `rhino3dm`, `numpy`, `networkx`,
  `OCP`, `build123d`, `shapely`, `trimesh`, `scipy`, `tests`, `tools`, `probes`. Run
  `py -3.12 tools/archcheck.py` after every Python change; it must print `ARCHITECTURE PASS`.
- Pydantic models are transport shapes only ("Convert at the boundary, delegate inward"). A model mirroring an
  archflow schema field-for-field is a violation; raw canonical payloads travel as opaque `dict`.
- The client never computes geometry — it renders what the backend serves (rhino3dm-wasm decode of a served
  `.3dm` is display, not authoring — the demo's existing behavior).
- Harvest = `git mv`, never copy-and-diverge, never extend the demo (`apps/archflow-studio`).
- One canonical in, one parallel out: the demo loses each piece the product gains (ports, viewer, launch
  pattern, build skeleton); the README marks the demotion.
- Records are read through `FilesystemProjectRepository` only (digest-verified `load_json`); the only raw
  filesystem reads are directory enumeration via `ProjectLayout` and `.3dm` artifact serving guarded by
  `layout.resolve_relative`.
- Tests live in `server/tests` (`py -3.12 -m unittest discover -s server/tests -t .` from repo root).
- Git: stage explicit paths only (never `git add -A` — the tree is shared with other lanes). Every commit ends
  with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Server port 8000 (uvicorn default); demo gateway stays on 8765 and is not touched at runtime.

---

### Task 1: Python dependencies, `shared/contracts`, FastAPI app with `/api/health`

**Files:**
- Create: `server/requirements.txt`, `server/__init__.py`, `server/settings.py`, `server/app.py`,
  `server/main.py`, `shared/__init__.py`, `shared/contracts/__init__.py`, `shared/contracts/studio.py`
- Test: `server/tests/__init__.py`, `server/tests/test_api_health.py`

**Interfaces:**
- Produces: `shared.contracts.studio` transport models (`StudioHealth`, `StudioError`, `TransportModel` base);
  `server.app.create_app(settings: StudioSettings | None = None) -> FastAPI`;
  `server.settings.StudioSettings(projects_root: Path)` with `StudioSettings.from_env()` reading
  `ARCHFLOW_PROJECTS_ROOT`.

- [ ] **Step 1: Install dependencies and write `server/requirements.txt`**

```powershell
py -3.12 -m pip install fastapi uvicorn httpx
```

`server/requirements.txt`:

```
fastapi>=0.115
uvicorn>=0.30
pydantic>=2.7
# tests only
httpx>=0.27
```

- [ ] **Step 2: Write the failing test**

`server/tests/__init__.py`: empty file. `server/tests/test_api_health.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.settings import StudioSettings


class HealthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = StudioSettings(projects_root=Path(__file__).parent / "missing-root")
        self.client = TestClient(create_app(settings))

    def test_health_is_read_only_and_names_the_service(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], "StudioHealth@2")
        self.assertEqual(payload["service"], "archflow-vibe-server")
        self.assertTrue(payload["readOnly"])
        self.assertTrue(payload["kernelImportable"])
        self.assertFalse(payload["canonicalWriteAuthority"])

    def test_unknown_api_route_is_a_typed_error(self) -> None:
        response = self.client.get("/api/nonsense")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["schema"], "StudioError@1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test, verify it fails**

Run: `py -3.12 -m unittest discover -s server/tests -t . -v` (from `D:\ARCHFLOW_V4`)
Expected: FAIL — `ModuleNotFoundError: No module named 'server'` (packages not created yet).

- [ ] **Step 4: Implement**

`shared/__init__.py` and `shared/contracts/__init__.py`: empty files. `server/__init__.py`: empty file.

`shared/contracts/studio.py`:

```python
"""Transport contracts shared by the vibe-modeling server and client.

These are wire shapes only. Canonical design state lives in archflow
(StateRecord@1 and the P036 repository); payloads of canonical records travel
through these models as opaque dictionaries, never re-modelled field by field.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TransportModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class StudioError(TransportModel):
    schema_id: str = Field(default="StudioError@1", alias="schema")
    code: str
    detail: str


class StudioHealth(TransportModel):
    schema_id: str = Field(default="StudioHealth@2", alias="schema")
    status: str
    service: str = "archflow-vibe-server"
    read_only: bool = Field(default=True, alias="readOnly")
    kernel_importable: bool = Field(alias="kernelImportable")
    canonical_write_authority: bool = Field(
        default=False, alias="canonicalWriteAuthority"
    )


class ProjectSummary(TransportModel):
    project_id: str = Field(alias="projectId")
    head_version: int = Field(alias="headVersion")
    state_sha256: str | None = Field(default=None, alias="stateSha256")


class StudioProjectList(TransportModel):
    schema_id: str = Field(default="StudioProjectList@1", alias="schema")
    projects: tuple[ProjectSummary, ...]


class RunSummary(TransportModel):
    run_id: str = Field(alias="runId")


class StudioRunList(TransportModel):
    schema_id: str = Field(default="StudioRunList@1", alias="schema")
    project_id: str = Field(alias="projectId")
    runs: tuple[RunSummary, ...]


class RecordSummary(TransportModel):
    record_kind: str = Field(alias="recordKind")
    relative_path: str = Field(alias="relativePath")
    sha256: str


class StudioRecordList(TransportModel):
    schema_id: str = Field(default="StudioRecordList@1", alias="schema")
    project_id: str = Field(alias="projectId")
    run_id: str = Field(alias="runId")
    records: tuple[RecordSummary, ...]


class ComponentNode(TransportModel):
    component_id: str = Field(alias="componentId")
    parent_component_id: str | None = Field(
        default=None, alias="parentComponentId"
    )
    semantic_kind: str = Field(alias="semanticKind")
    intent: str
    maturity: str
    revision: int


class RecordCounts(TransportModel):
    entities: int
    parameters: int
    relations: int
    obligations: int


class StudioRecordDetail(TransportModel):
    schema_id: str = Field(default="StudioRecordDetail@1", alias="schema")
    project_id: str = Field(alias="projectId")
    relative_path: str = Field(alias="relativePath")
    sha256: str
    payload_schema: str | None = Field(default=None, alias="payloadSchema")
    payload: dict[str, Any]
    state_digest: str | None = Field(default=None, alias="stateDigest")
    counts: RecordCounts | None = None
    component_tree: tuple[ComponentNode, ...] | None = Field(
        default=None, alias="componentTree"
    )
    component_tree_error: str | None = Field(
        default=None, alias="componentTreeError"
    )


class ArtifactSummary(TransportModel):
    relative_path: str = Field(alias="relativePath")
    size_bytes: int = Field(alias="sizeBytes")


class StudioArtifactList(TransportModel):
    schema_id: str = Field(default="StudioArtifactList@1", alias="schema")
    project_id: str = Field(alias="projectId")
    artifacts: tuple[ArtifactSummary, ...]
```

`server/settings.py`:

```python
"""Server configuration. The projects root is the P036 workspace directory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROJECTS_ROOT = Path(
    r"D:\PROJECTS\01_ACTIVE_当前项目\ARCHFLOW CAADRIA 2027"
    r"\V4_RUNTIME\workspace\projects"
)


@dataclass(frozen=True, slots=True)
class StudioSettings:
    projects_root: Path

    @classmethod
    def from_env(cls) -> "StudioSettings":
        raw = os.environ.get("ARCHFLOW_PROJECTS_ROOT")
        return cls(projects_root=Path(raw) if raw else DEFAULT_PROJECTS_ROOT)
```

`server/app.py` (health-only in this task; routes grow in Tasks 4–5):

```python
"""FastAPI application for the vibe-modeling server.

Read-only slice: it orchestrates and delegates to archflow; it computes no
geometry, holds no second state schema, and writes nothing canonical.
"""

from __future__ import annotations

from importlib.util import find_spec

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from server.settings import StudioSettings
from shared.contracts.studio import StudioError, StudioHealth


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=StudioError(code=code, detail=detail).model_dump(by_alias=True),
    )


def create_app(settings: StudioSettings | None = None) -> FastAPI:
    active = settings or StudioSettings.from_env()
    app = FastAPI(title="ArchFlow vibe-modeling server", docs_url=None)
    app.state.settings = active

    @app.get("/api/health")
    def health() -> JSONResponse:
        payload = StudioHealth(
            status="ok",
            kernel_importable=find_spec("archflow") is not None,
        )
        return JSONResponse(payload.model_dump(by_alias=True))

    @app.exception_handler(404)
    async def not_found(request, exc) -> JSONResponse:  # noqa: ANN001
        detail = getattr(exc, "detail", "The requested route is not defined.")
        return _error(404, "NOT_FOUND", str(detail))

    return app
```

`server/main.py`:

```python
"""Run the vibe-modeling server: py -3.12 -m server.main (from the repo root)."""

from __future__ import annotations

import uvicorn

from server.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the test, verify it passes; run the firewall**

Run: `py -3.12 -m unittest discover -s server/tests -t . -v` → Expected: `OK (2 tests)`
Run: `py -3.12 tools/archcheck.py` → Expected: `ARCHITECTURE PASS`

- [ ] **Step 6: Commit**

```powershell
git add server shared
git commit -m "P108: formal server and shared trees open with a FastAPI health slice

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Move the reserved ports seam to `server/ports.py`

**Files:**
- Move: `apps/archflow-studio/backend/ports.py` → `server/ports.py` (git mv, docstring header updated)
- Modify: `apps/archflow-studio/README.md` (the one line naming `backend/ports.py`)

**Interfaces:**
- Produces: `server.ports` with the six reserved protocols exactly as the demo declared them
  (`IntentProvider`, `RetrievalProvider`, `PreviewBackend`, `ViewerAssetProvider`, `HumanReviewPort`,
  `StudioEventSink`). `IntentProvider.propose(*, session_ref, message, context_refs)` keeps its docstring
  verbatim: "Translate a user utterance into a proposal candidate, never a commit."

- [ ] **Step 1: Move the file**

```powershell
git mv apps/archflow-studio/backend/ports.py server/ports.py
```

Edit only the module docstring's first line to: `"""Reserved application ports for the vibe-modeling server (P108)."""`
Every Protocol body stays byte-identical (verify with `git diff --cached -M`).

- [ ] **Step 2: Update the demo README line**

In `apps/archflow-studio/README.md` replace the sentence
"The reserved ports in `src/ports/studioPorts.ts` and `backend/ports.py` define future integration seams." with
"The Python application ports moved to `server/ports.py` (P108); `src/ports/studioPorts.ts` remains the
browser-side reservation."

- [ ] **Step 3: Verify nothing breaks**

Run: `py -3.12 -m unittest discover -s apps/archflow-studio/backend/tests -t apps/archflow-studio -v`
Expected: `OK` (the demo backend never imported its ports module).
Run: `py -3.12 -m unittest discover -s server/tests -t . -v` → `OK`
Run: `py -3.12 tools/archcheck.py` → `ARCHITECTURE PASS`

- [ ] **Step 4: Commit**

```powershell
git add server/ports.py apps/archflow-studio/backend/ports.py apps/archflow-studio/README.md
git commit -m "P108: the IntentProvider seam moves to server/ports.py, not a copy

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Repository read adapter (`ProjectCatalog`)

**Files:**
- Create: `server/catalog.py`
- Test: `server/tests/support.py`, `server/tests/test_catalog.py`

**Interfaces:**
- Consumes: `archflow.project.repository.FilesystemProjectRepository` (`open`, `read_head`, `load_run`,
  `list_json`, `load_json`), `archflow.project.ports.PersistenceDestination/PersistenceArea`,
  `archflow.project.refs.ProjectRecordRef`.
- Produces: `server.catalog.ProjectCatalog(projects_root)` with
  `project_ids() -> tuple[str, ...]`, `head(project_id) -> ProjectVersionRef`,
  `run_ids(project_id) -> tuple[str, ...]`, `record_refs(project_id, run_id) -> tuple[ProjectRecordRef, ...]`,
  `load_record(project_id, relative_path, sha256) -> dict`,
  `artifacts(project_id) -> tuple[tuple[str, int], ...]`,
  `artifact_path(project_id, relative_path) -> Path`; exceptions `UnknownProject`, `UnknownRun`,
  `UnknownRecord`, `UnknownArtifact` (all `LookupError`).

- [ ] **Step 1: Write the shared test fixture**

`server/tests/support.py`:

```python
"""Builds a real, minimal P036 project for server tests (never mocks)."""

from __future__ import annotations

from pathlib import Path

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.repository import FilesystemProjectRepository

PROJECT_ID = "demo-project"
RUN_ID = "run-001"

STATE_RECORD = {
    "schema": "StateRecord@1",
    "project_id": PROJECT_ID,
    "run_id": RUN_ID,
    "entities": [
        {
            "entity_id": "building",
            "schema": "Component@1",
            "fields": {
                "semantic_kind": "building",
                "intent": "demo building",
                "source_refs": ["evidence:demo"],
            },
            "parent_id": None,
        },
        {
            "entity_id": "portico",
            "schema": "Component@1",
            "fields": {
                "semantic_kind": "portico",
                "intent": "front portico",
                "source_refs": ["evidence:demo"],
            },
            "parent_id": "building",
        },
    ],
    "parameters": [],
    "relations": [],
    "obligations": [],
}


def make_project(root: Path) -> tuple[FilesystemProjectRepository, str, str]:
    """Initialize a project with one run and one state record.

    Returns (repository, record_relative_path, record_sha256).
    """

    repository = FilesystemProjectRepository.initialize(
        root / PROJECT_ID,
        project_id=PROJECT_ID,
        initial_state={"project_id": PROJECT_ID, "version": 0},
    )
    run = repository.create_run(RUN_ID)
    ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id=RUN_ID
        ),
        record_kind="state-record",
        payload=STATE_RECORD,
    )
    return repository, ref.relative_path, ref.sha256
```

`server/tests/test_catalog.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.catalog import (
    ProjectCatalog,
    UnknownProject,
    UnknownRecord,
    UnknownRun,
)
from server.tests.support import PROJECT_ID, RUN_ID, make_project


class ProjectCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _, self.record_path, self.record_sha = make_project(self.root)
        self.catalog = ProjectCatalog(self.root)

    def test_lists_only_directories_with_a_manifest(self) -> None:
        (self.root / "not-a-project").mkdir()
        self.assertEqual(self.catalog.project_ids(), (PROJECT_ID,))

    def test_head_reports_version_zero(self) -> None:
        self.assertEqual(self.catalog.head(PROJECT_ID).version, 0)

    def test_lists_runs_and_records(self) -> None:
        self.assertEqual(self.catalog.run_ids(PROJECT_ID), (RUN_ID,))
        refs = self.catalog.record_refs(PROJECT_ID, RUN_ID)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].sha256, self.record_sha)

    def test_loads_a_record_with_digest_verification(self) -> None:
        payload = self.catalog.load_record(
            PROJECT_ID, self.record_path, self.record_sha
        )
        self.assertEqual(payload["schema"], "StateRecord@1")

    def test_unknown_names_raise_typed_lookup_errors(self) -> None:
        with self.assertRaises(UnknownProject):
            self.catalog.run_ids("no-such-project")
        with self.assertRaises(UnknownRun):
            self.catalog.record_refs(PROJECT_ID, "no-such-run")
        with self.assertRaises(UnknownRecord):
            self.catalog.load_record(PROJECT_ID, self.record_path, "0" * 64)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `py -3.12 -m unittest server.tests.test_catalog -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.catalog'`.

- [ ] **Step 3: Implement `server/catalog.py`**

```python
"""Read-only browsing over the P036 project repositories.

Every record read goes through FilesystemProjectRepository (digest-verified);
this module never parses record JSON on its own and never writes anything.
"""

from __future__ import annotations

from pathlib import Path

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef
from archflow.project.repository import (
    FilesystemProjectRepository,
    ProjectRepositoryError,
)


class UnknownProject(LookupError):
    pass


class UnknownRun(LookupError):
    pass


class UnknownRecord(LookupError):
    pass


class UnknownArtifact(LookupError):
    pass


class ProjectCatalog:
    def __init__(self, projects_root: Path) -> None:
        self._root = Path(projects_root)
        self._repositories: dict[str, FilesystemProjectRepository] = {}

    def project_ids(self) -> tuple[str, ...]:
        if not self._root.is_dir():
            return ()
        return tuple(
            sorted(
                child.name
                for child in self._root.iterdir()
                if (child / "project.json").is_file()
            )
        )

    def _repository(self, project_id: str) -> FilesystemProjectRepository:
        cached = self._repositories.get(project_id)
        if cached is not None:
            return cached
        candidate = self._root / project_id
        if not (candidate / "project.json").is_file():
            raise UnknownProject(project_id)
        try:
            repository = FilesystemProjectRepository.open(candidate)
        except ProjectRepositoryError as exc:
            raise UnknownProject(f"{project_id}: {exc}") from exc
        self._repositories[project_id] = repository
        return repository

    def head(self, project_id: str) -> ProjectVersionRef:
        return self._repository(project_id).read_head()

    def run_ids(self, project_id: str) -> tuple[str, ...]:
        runs_dir = self._repository(project_id).layout.runs
        if not runs_dir.is_dir():
            return ()
        return tuple(
            sorted(child.name for child in runs_dir.iterdir() if child.is_dir())
        )

    def record_refs(
        self, project_id: str, run_id: str
    ) -> tuple[ProjectRecordRef, ...]:
        repository = self._repository(project_id)
        if run_id not in self.run_ids(project_id):
            raise UnknownRun(run_id)
        try:
            run = repository.load_run(run_id)
        except ProjectRepositoryError as exc:
            raise UnknownRun(f"{run_id}: {exc}") from exc
        refs = repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD, run_id=run_id
            ),
        )
        return tuple(sorted(refs, key=lambda ref: ref.relative_path))

    def load_record(
        self, project_id: str, relative_path: str, sha256: str
    ) -> dict:
        repository = self._repository(project_id)
        try:
            ref = ProjectRecordRef(project_id, relative_path, sha256)
            return repository.load_json(ref)
        except (ProjectRepositoryError, ValueError, OSError) as exc:
            raise UnknownRecord(f"{relative_path}: {exc}") from exc

    def artifacts(self, project_id: str) -> tuple[tuple[str, int], ...]:
        layout = self._repository(project_id).layout
        found = []
        for path in sorted(layout.root.rglob("*.3dm")):
            relative = path.relative_to(layout.root).as_posix()
            found.append((relative, path.stat().st_size))
        return tuple(found)

    def artifact_path(self, project_id: str, relative_path: str) -> Path:
        layout = self._repository(project_id).layout
        try:
            resolved = layout.resolve_relative(relative_path)
        except ValueError as exc:
            raise UnknownArtifact(f"{relative_path}: {exc}") from exc
        if not resolved.is_file() or resolved.suffix.lower() != ".3dm":
            raise UnknownArtifact(relative_path)
        return resolved
```

- [ ] **Step 4: Run tests, verify they pass; firewall**

Run: `py -3.12 -m unittest server.tests.test_catalog -v` → `OK (5 tests)`
Run: `py -3.12 tools/archcheck.py` → `ARCHITECTURE PASS`

- [ ] **Step 5: Commit**

```powershell
git add server/catalog.py server/tests
git commit -m "P108: the server browses P036 projects by delegation, never by parsing beside the repository

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Browsing API routes (projects, runs, records, record detail)

**Files:**
- Create: `server/records.py`
- Modify: `server/app.py`
- Test: `server/tests/test_api_browse.py`

**Interfaces:**
- Consumes: `ProjectCatalog` (Task 3), `shared.contracts.studio` models (Task 1),
  `archflow.state.state_record.StateRecord.from_dict`, `design_components_of`, `StateRecordError`.
- Produces: routes `GET /api/projects`, `GET /api/projects/{project_id}/runs`,
  `GET /api/projects/{project_id}/runs/{run_id}/records`,
  `GET /api/projects/{project_id}/record?path=&sha256=`;
  `server.records.detail_of(project_id, relative_path, sha256, payload) -> StudioRecordDetail`.

- [ ] **Step 1: Write the failing tests**

`server/tests/test_api_browse.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.settings import StudioSettings
from server.tests.support import PROJECT_ID, RUN_ID, make_project


class BrowseApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        _, self.record_path, self.record_sha = make_project(root)
        self.client = TestClient(create_app(StudioSettings(projects_root=root)))

    def test_projects_lists_the_demo_project_with_its_head(self) -> None:
        payload = self.client.get("/api/projects").json()
        self.assertEqual(payload["schema"], "StudioProjectList@1")
        self.assertEqual(payload["projects"][0]["projectId"], PROJECT_ID)
        self.assertEqual(payload["projects"][0]["headVersion"], 0)

    def test_runs_and_records_list(self) -> None:
        runs = self.client.get(f"/api/projects/{PROJECT_ID}/runs").json()
        self.assertEqual(runs["runs"], [{"runId": RUN_ID}])
        records = self.client.get(
            f"/api/projects/{PROJECT_ID}/runs/{RUN_ID}/records"
        ).json()
        self.assertEqual(records["records"][0]["recordKind"], "state-record")
        self.assertEqual(records["records"][0]["sha256"], self.record_sha)

    def test_record_detail_carries_component_tree_and_digest(self) -> None:
        detail = self.client.get(
            f"/api/projects/{PROJECT_ID}/record",
            params={"path": self.record_path, "sha256": self.record_sha},
        ).json()
        self.assertEqual(detail["payloadSchema"], "StateRecord@1")
        self.assertIsNotNone(detail["stateDigest"])
        self.assertEqual(detail["counts"]["entities"], 2)
        tree = {node["componentId"]: node for node in detail["componentTree"]}
        self.assertEqual(tree["portico"]["parentComponentId"], "building")

    def test_unknown_project_is_a_typed_404(self) -> None:
        response = self.client.get("/api/projects/absent/runs")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "PROJECT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `py -3.12 -m unittest server.tests.test_api_browse -v`
Expected: FAIL — 404s / missing routes.

- [ ] **Step 3: Implement**

`server/records.py`:

```python
"""Record-detail derivation: canonical views computed by archflow, not here."""

from __future__ import annotations

from typing import Any, Mapping

from archflow.state.state_record import (
    StateRecord,
    StateRecordError,
    design_components_of,
)
from shared.contracts.studio import (
    ComponentNode,
    RecordCounts,
    StudioRecordDetail,
)


def detail_of(
    project_id: str,
    relative_path: str,
    sha256: str,
    payload: Mapping[str, Any],
) -> StudioRecordDetail:
    base: dict[str, Any] = {
        "project_id": project_id,
        "relative_path": relative_path,
        "sha256": sha256,
        "payload_schema": payload.get("schema")
        if isinstance(payload.get("schema"), str)
        else None,
        "payload": dict(payload),
    }
    if payload.get("schema") != StateRecord.SCHEMA:
        return StudioRecordDetail(**base)
    record = StateRecord.from_dict(payload)
    base["state_digest"] = record.state_digest()
    base["counts"] = RecordCounts(
        entities=len(record.entities),
        parameters=len(record.parameters),
        relations=len(record.relations),
        obligations=len(record.obligations),
    )
    try:
        components = design_components_of(record)
    except StateRecordError as exc:
        base["component_tree_error"] = str(exc)
        return StudioRecordDetail(**base)
    base["component_tree"] = tuple(
        ComponentNode(
            component_id=component.component_id,
            parent_component_id=component.parent_component_id,
            semantic_kind=component.semantic_kind,
            intent=component.intent,
            maturity=component.maturity.value,
            revision=component.revision,
        )
        for component in components
    )
    return StudioRecordDetail(**base)
```

In `server/app.py`, inside `create_app` after the health route, add (and extend the imports with
`ProjectCatalog`, the four `Unknown*` exceptions, `detail_of`, and the list models):

```python
    catalog = ProjectCatalog(active.projects_root)
    app.state.catalog = catalog

    @app.get("/api/projects")
    def projects() -> JSONResponse:
        summaries = []
        for project_id in catalog.project_ids():
            head = catalog.head(project_id)
            summaries.append(
                ProjectSummary(
                    project_id=project_id,
                    head_version=head.version,
                    state_sha256=head.state_sha256,
                )
            )
        payload = StudioProjectList(projects=tuple(summaries))
        return JSONResponse(payload.model_dump(by_alias=True))

    @app.get("/api/projects/{project_id}/runs")
    def runs(project_id: str) -> JSONResponse:
        payload = StudioRunList(
            project_id=project_id,
            runs=tuple(
                RunSummary(run_id=run_id)
                for run_id in catalog.run_ids(project_id)
            ),
        )
        return JSONResponse(payload.model_dump(by_alias=True))

    @app.get("/api/projects/{project_id}/runs/{run_id}/records")
    def records(project_id: str, run_id: str) -> JSONResponse:
        summaries = []
        for ref in catalog.record_refs(project_id, run_id):
            stem = ref.relative_path.rsplit("/", 1)[-1]
            if stem.endswith(".json"):
                stem = stem[: -len(".json")]
            summaries.append(
                RecordSummary(
                    record_kind=stem.rsplit("-", 1)[0],
                    relative_path=ref.relative_path,
                    sha256=ref.sha256,
                )
            )
        payload = StudioRecordList(
            project_id=project_id, run_id=run_id, records=tuple(summaries)
        )
        return JSONResponse(payload.model_dump(by_alias=True))

    @app.get("/api/projects/{project_id}/record")
    def record_detail(project_id: str, path: str, sha256: str) -> JSONResponse:
        payload = catalog.load_record(project_id, path, sha256)
        detail = detail_of(project_id, path, sha256, payload)
        return JSONResponse(detail.model_dump(by_alias=True))

    @app.exception_handler(UnknownProject)
    async def unknown_project(request, exc) -> JSONResponse:  # noqa: ANN001
        return _error(404, "PROJECT_NOT_FOUND", str(exc))

    @app.exception_handler(UnknownRun)
    async def unknown_run(request, exc) -> JSONResponse:  # noqa: ANN001
        return _error(404, "RUN_NOT_FOUND", str(exc))

    @app.exception_handler(UnknownRecord)
    async def unknown_record(request, exc) -> JSONResponse:  # noqa: ANN001
        return _error(404, "RECORD_NOT_FOUND", str(exc))

    @app.exception_handler(UnknownArtifact)
    async def unknown_artifact(request, exc) -> JSONResponse:  # noqa: ANN001
        return _error(404, "ARTIFACT_NOT_FOUND", str(exc))
```

- [ ] **Step 4: Run all server tests, verify they pass; firewall**

Run: `py -3.12 -m unittest discover -s server/tests -t . -v` → `OK`
Run: `py -3.12 tools/archcheck.py` → `ARCHITECTURE PASS`

- [ ] **Step 5: Commit**

```powershell
git add server/app.py server/records.py server/tests/test_api_browse.py
git commit -m "P108: browsing API serves projects, runs, records and the record's own component tree

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Artifact endpoints (list and serve `.3dm`)

**Files:**
- Modify: `server/app.py`
- Test: `server/tests/test_api_artifacts.py`

**Interfaces:**
- Consumes: `ProjectCatalog.artifacts`, `ProjectCatalog.artifact_path` (Task 3),
  `StudioArtifactList`/`ArtifactSummary` (Task 1).
- Produces: `GET /api/projects/{project_id}/artifacts`,
  `GET /api/projects/{project_id}/artifact?path=` (binary `.3dm` response).

- [ ] **Step 1: Write the failing tests**

`server/tests/test_api_artifacts.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.settings import StudioSettings
from server.tests.support import PROJECT_ID, make_project


class ArtifactApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        repository, _, _ = make_project(root)
        exports = repository.layout.exports
        exports.mkdir(parents=True, exist_ok=True)
        (exports / "model.3dm").write_bytes(b"3dm-bytes")
        self.client = TestClient(create_app(StudioSettings(projects_root=root)))

    def test_artifacts_lists_the_export(self) -> None:
        payload = self.client.get(
            f"/api/projects/{PROJECT_ID}/artifacts"
        ).json()
        self.assertEqual(payload["schema"], "StudioArtifactList@1")
        self.assertEqual(
            payload["artifacts"],
            [{"relativePath": "exports/model.3dm", "sizeBytes": 9}],
        )

    def test_artifact_serves_the_bytes(self) -> None:
        response = self.client.get(
            f"/api/projects/{PROJECT_ID}/artifact",
            params={"path": "exports/model.3dm"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"3dm-bytes")

    def test_path_escape_is_refused(self) -> None:
        response = self.client.get(
            f"/api/projects/{PROJECT_ID}/artifact",
            params={"path": "../../secrets.3dm"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "ARTIFACT_NOT_FOUND")

    def test_non_3dm_files_are_not_served(self) -> None:
        response = self.client.get(
            f"/api/projects/{PROJECT_ID}/artifact",
            params={"path": "project.json"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `py -3.12 -m unittest server.tests.test_api_artifacts -v` → FAIL (routes missing).

- [ ] **Step 3: Implement in `server/app.py`** (add `FileResponse` to imports from `fastapi.responses`)

```python
    @app.get("/api/projects/{project_id}/artifacts")
    def artifacts(project_id: str) -> JSONResponse:
        payload = StudioArtifactList(
            project_id=project_id,
            artifacts=tuple(
                ArtifactSummary(relative_path=path, size_bytes=size)
                for path, size in catalog.artifacts(project_id)
            ),
        )
        return JSONResponse(payload.model_dump(by_alias=True))

    @app.get("/api/projects/{project_id}/artifact")
    def artifact(project_id: str, path: str) -> FileResponse:
        resolved = catalog.artifact_path(project_id, path)
        return FileResponse(resolved, media_type="application/octet-stream")
```

- [ ] **Step 4: Run all server tests; firewall**

Run: `py -3.12 -m unittest discover -s server/tests -t . -v` → `OK`
Run: `py -3.12 tools/archcheck.py` → `ARCHITECTURE PASS`

- [ ] **Step 5: Commit**

```powershell
git add server/app.py server/tests/test_api_artifacts.py
git commit -m "P108: exported .3dm artifacts are listed and served read-only, path-guarded by the layout

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Move the browser app to `client/web` (build skeleton parity)

**Files:**
- Move (git mv): `apps/archflow-studio/{package.json,package-lock.json,tsconfig.json,vite.config.ts,index.html,.gitignore,scripts,src}` → `client/web/…`
- Move (git mv): `apps/archflow-studio/launch.py` → `server/launch.py` (adapted in Task 8)
- Modify: `client/web/package.json`, `client/web/vite.config.ts`, `apps/archflow-studio/README.md`

**Interfaces:**
- Produces: buildable Vite app at `client/web` (same demo App for now — reworked in Task 7); demo directory
  reduced to `backend/`, `run_server.py`, `README.md`.

- [ ] **Step 1: Move the files**

```powershell
mkdir client\web
git mv apps/archflow-studio/package.json client/web/package.json
git mv apps/archflow-studio/package-lock.json client/web/package-lock.json
git mv apps/archflow-studio/tsconfig.json client/web/tsconfig.json
git mv apps/archflow-studio/vite.config.ts client/web/vite.config.ts
git mv apps/archflow-studio/index.html client/web/index.html
git mv apps/archflow-studio/.gitignore client/web/.gitignore
git mv apps/archflow-studio/scripts client/web/scripts
git mv apps/archflow-studio/src client/web/src
git mv apps/archflow-studio/launch.py server/launch.py
```

Also move any style entry the src references if it lives outside `src` (check `index.html`; `styles.css` is
inside `src`, so nothing extra).

- [ ] **Step 2: Point the client at the new server**

`client/web/package.json`: `"name": "archflow-vibe-client"`, and scripts become:

```json
  "scripts": {
    "sync:rhino3dm": "node scripts/sync-rhino3dm.mjs",
    "dev": "npm run sync:rhino3dm && vite",
    "backend": "cd ../.. && py -3.12 -m server.main",
    "typecheck": "tsc --noEmit",
    "build": "npm run sync:rhino3dm && tsc --noEmit && vite build"
  }
```

`client/web/vite.config.ts` proxy target: `"/api": "http://127.0.0.1:8000"`.

- [ ] **Step 3: Mark the demo (full demotion notice)**

Rewrite the top of `apps/archflow-studio/README.md`:

```markdown
# ArchFlow Studio (retained demo)

> **Demoted 2026-09-03 (P108, Kaiwen's ruling).** This directory is a retained
> demo, not the product. The formal vibe-modeling product lives in the
> top-level `client/`, `server/` and `shared/` trees. Its pieces were harvested
> by move, never by copy: the browser app (`src/`, `index.html`, the Vite/TS
> configs, `scripts/`, `package.json`) is now `client/web/`; the reserved
> application ports (`backend/ports.py`) are `server/ports.py`; the detached
> launch pattern (`launch.py`) is `server/launch.py`. What remains here is the
> dependency-free demo gateway (`backend/`, `run_server.py`) and its tests:
> run it with `py -3.12 run_server.py`, test it with
> `py -3.12 -m unittest discover -s backend/tests -t . -v`. Do not extend this
> directory; it no longer builds a frontend.
```

Delete the now-false sections (npm run/build instructions, planned slices list stays as history with a
one-line note "superseded by the P108 lanes"). Keep the ownership/boundaries section for the backend that
remains.

- [ ] **Step 4: Install and build**

```powershell
cd client\web
npm install
npm run typecheck
npm run build
```

Expected: typecheck and build succeed (the demo App still compiles against the moved sources; it will show
"GATEWAY OFFLINE" against the new server until Task 7 — that is expected and honest).

- [ ] **Step 5: Verify the demo backend still passes its tests**

Run (repo root): `py -3.12 -m unittest discover -s apps/archflow-studio/backend/tests -t apps/archflow-studio -v` → `OK`
Run: `py -3.12 tools/archcheck.py` → `ARCHITECTURE PASS`

- [ ] **Step 6: Commit**

```powershell
git add client server/launch.py apps/archflow-studio
git commit -m "P108: the browser app moves to client/web; the demo is marked demoted, not extended

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Client product shell — project browser, record panel, artifact viewing

**Files:**
- Rewrite: `client/web/src/contracts/studio.ts`, `client/web/src/gateway/StudioGateway.ts`,
  `client/web/src/gateway/HttpStudioGateway.ts`, `client/web/src/App.tsx`
- Create: `client/web/src/components/ProjectBrowser.tsx`, `client/web/src/components/RecordPanel.tsx`
- Delete: `client/web/src/components/CapabilityPanel.tsx`, `client/web/src/components/StageRail.tsx`
- Modify: `client/web/src/styles.css` (only additive: styles for the browser lists and tree indent)

**Interfaces:**
- Consumes: server routes from Tasks 1/4/5; viewer's `ViewportController.openFile(file: File)` (unchanged).
- Produces: TS transport types mirroring `shared/contracts/studio.py` payload keys exactly (camelCase wire
  names); `StudioGateway` interface with `health`, `projects`, `runs`, `records`, `recordDetail`, `artifacts`,
  `artifactFile`.

- [ ] **Step 1: Rewrite `client/web/src/contracts/studio.ts`**

```ts
// Wire shapes of the vibe-modeling server (shared/contracts/studio.py).

export interface StudioHealth {
  schema: "StudioHealth@2";
  status: string;
  service: string;
  readOnly: boolean;
  kernelImportable: boolean;
  canonicalWriteAuthority: boolean;
}

export interface ProjectSummary {
  projectId: string;
  headVersion: number;
  stateSha256: string | null;
}

export interface StudioProjectList {
  schema: "StudioProjectList@1";
  projects: ProjectSummary[];
}

export interface RunSummary {
  runId: string;
}

export interface StudioRunList {
  schema: "StudioRunList@1";
  projectId: string;
  runs: RunSummary[];
}

export interface RecordSummary {
  recordKind: string;
  relativePath: string;
  sha256: string;
}

export interface StudioRecordList {
  schema: "StudioRecordList@1";
  projectId: string;
  runId: string;
  records: RecordSummary[];
}

export interface ComponentNode {
  componentId: string;
  parentComponentId: string | null;
  semanticKind: string;
  intent: string;
  maturity: string;
  revision: number;
}

export interface RecordCounts {
  entities: number;
  parameters: number;
  relations: number;
  obligations: number;
}

export interface StudioRecordDetail {
  schema: "StudioRecordDetail@1";
  projectId: string;
  relativePath: string;
  sha256: string;
  payloadSchema: string | null;
  payload: Record<string, unknown>;
  stateDigest: string | null;
  counts: RecordCounts | null;
  componentTree: ComponentNode[] | null;
  componentTreeError: string | null;
}

export interface ArtifactSummary {
  relativePath: string;
  sizeBytes: number;
}

export interface StudioArtifactList {
  schema: "StudioArtifactList@1";
  projectId: string;
  artifacts: ArtifactSummary[];
}

export interface StudioError {
  schema: "StudioError@1";
  code: string;
  detail: string;
}
```

- [ ] **Step 2: Rewrite the gateway pair**

`client/web/src/gateway/StudioGateway.ts`:

```ts
import type {
  StudioArtifactList,
  StudioHealth,
  StudioProjectList,
  StudioRecordDetail,
  StudioRecordList,
  StudioRunList,
} from "../contracts/studio";

export interface StudioGateway {
  health(signal?: AbortSignal): Promise<StudioHealth>;
  projects(signal?: AbortSignal): Promise<StudioProjectList>;
  runs(projectId: string, signal?: AbortSignal): Promise<StudioRunList>;
  records(
    projectId: string,
    runId: string,
    signal?: AbortSignal,
  ): Promise<StudioRecordList>;
  recordDetail(
    projectId: string,
    relativePath: string,
    sha256: string,
    signal?: AbortSignal,
  ): Promise<StudioRecordDetail>;
  artifacts(projectId: string, signal?: AbortSignal): Promise<StudioArtifactList>;
  artifactFile(
    projectId: string,
    relativePath: string,
    signal?: AbortSignal,
  ): Promise<File>;
}
```

`client/web/src/gateway/HttpStudioGateway.ts`:

```ts
import type {
  StudioArtifactList,
  StudioHealth,
  StudioProjectList,
  StudioRecordDetail,
  StudioRecordList,
  StudioRunList,
} from "../contracts/studio";
import type { StudioGateway } from "./StudioGateway";

export class StudioGatewayError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "StudioGatewayError";
  }
}

export class HttpStudioGateway implements StudioGateway {
  constructor(private readonly baseUrl = "/api") {}

  health(signal?: AbortSignal): Promise<StudioHealth> {
    return this.getJson<StudioHealth>("/health", signal);
  }

  projects(signal?: AbortSignal): Promise<StudioProjectList> {
    return this.getJson<StudioProjectList>("/projects", signal);
  }

  runs(projectId: string, signal?: AbortSignal): Promise<StudioRunList> {
    return this.getJson<StudioRunList>(
      `/projects/${encodeURIComponent(projectId)}/runs`,
      signal,
    );
  }

  records(
    projectId: string,
    runId: string,
    signal?: AbortSignal,
  ): Promise<StudioRecordList> {
    return this.getJson<StudioRecordList>(
      `/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/records`,
      signal,
    );
  }

  recordDetail(
    projectId: string,
    relativePath: string,
    sha256: string,
    signal?: AbortSignal,
  ): Promise<StudioRecordDetail> {
    const query = new URLSearchParams({ path: relativePath, sha256 });
    return this.getJson<StudioRecordDetail>(
      `/projects/${encodeURIComponent(projectId)}/record?${query}`,
      signal,
    );
  }

  artifacts(
    projectId: string,
    signal?: AbortSignal,
  ): Promise<StudioArtifactList> {
    return this.getJson<StudioArtifactList>(
      `/projects/${encodeURIComponent(projectId)}/artifacts`,
      signal,
    );
  }

  async artifactFile(
    projectId: string,
    relativePath: string,
    signal?: AbortSignal,
  ): Promise<File> {
    const query = new URLSearchParams({ path: relativePath });
    const response = await fetch(
      `${this.baseUrl}/projects/${encodeURIComponent(projectId)}/artifact?${query}`,
      { signal },
    );
    if (!response.ok) {
      throw new StudioGatewayError(
        `Studio gateway returned HTTP ${response.status}`,
        response.status,
      );
    }
    const blob = await response.blob();
    const name = relativePath.split("/").pop() ?? "model.3dm";
    return new File([blob], name);
  }

  private async getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      headers: { Accept: "application/json" },
      signal,
    });
    if (!response.ok) {
      throw new StudioGatewayError(
        `Studio gateway returned HTTP ${response.status}`,
        response.status,
      );
    }
    return (await response.json()) as T;
  }
}
```

- [ ] **Step 3: New left-panel component `client/web/src/components/ProjectBrowser.tsx`**

```tsx
import type {
  ArtifactSummary,
  ProjectSummary,
  RecordSummary,
  RunSummary,
} from "../contracts/studio";

interface ProjectBrowserProps {
  projects: ProjectSummary[];
  selectedProject: string | null;
  onSelectProject(projectId: string): void;
  runs: RunSummary[];
  selectedRun: string | null;
  onSelectRun(runId: string): void;
  records: RecordSummary[];
  selectedRecordPath: string | null;
  onSelectRecord(record: RecordSummary): void;
  artifacts: ArtifactSummary[];
  onOpenArtifact(artifact: ArtifactSummary): void;
}

export function ProjectBrowser(props: ProjectBrowserProps) {
  return (
    <>
      <section className="panel-section" aria-labelledby="projects-heading">
        <div className="section-heading-row">
          <h2 id="projects-heading">PROJECTS</h2>
          <span className="micro-label">{props.projects.length}</span>
        </div>
        <ul className="browser-list">
          {props.projects.map((project) => (
            <li key={project.projectId}>
              <button
                type="button"
                className={
                  project.projectId === props.selectedProject
                    ? "browser-item browser-item--active"
                    : "browser-item"
                }
                onClick={() => props.onSelectProject(project.projectId)}
              >
                <strong>{project.projectId}</strong>
                <small>HEAD v{project.headVersion}</small>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {props.selectedProject && (
        <section className="panel-section" aria-labelledby="runs-heading">
          <div className="section-heading-row">
            <h2 id="runs-heading">RUNS</h2>
            <span className="micro-label">{props.runs.length}</span>
          </div>
          <ul className="browser-list">
            {props.runs.map((run) => (
              <li key={run.runId}>
                <button
                  type="button"
                  className={
                    run.runId === props.selectedRun
                      ? "browser-item browser-item--active"
                      : "browser-item"
                  }
                  onClick={() => props.onSelectRun(run.runId)}
                >
                  <strong>{run.runId}</strong>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {props.selectedRun && (
        <section className="panel-section" aria-labelledby="records-heading">
          <div className="section-heading-row">
            <h2 id="records-heading">RECORDS</h2>
            <span className="micro-label">{props.records.length}</span>
          </div>
          <ul className="browser-list">
            {props.records.map((record) => (
              <li key={record.relativePath}>
                <button
                  type="button"
                  className={
                    record.relativePath === props.selectedRecordPath
                      ? "browser-item browser-item--active"
                      : "browser-item"
                  }
                  onClick={() => props.onSelectRecord(record)}
                >
                  <strong>{record.recordKind}</strong>
                  <small>{record.sha256.slice(0, 12)}</small>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {props.selectedProject && (
        <section className="panel-section" aria-labelledby="artifacts-heading">
          <div className="section-heading-row">
            <h2 id="artifacts-heading">3DM ARTIFACTS</h2>
            <span className="micro-label">{props.artifacts.length}</span>
          </div>
          <ul className="browser-list">
            {props.artifacts.map((artifact) => (
              <li key={artifact.relativePath}>
                <button
                  type="button"
                  className="browser-item"
                  onClick={() => props.onOpenArtifact(artifact)}
                >
                  <strong>{artifact.relativePath.split("/").pop()}</strong>
                  <small>{artifact.relativePath}</small>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
```

- [ ] **Step 4: New right-panel component `client/web/src/components/RecordPanel.tsx`**

```tsx
import type { ComponentNode, StudioRecordDetail } from "../contracts/studio";

interface RecordPanelProps {
  detail: StudioRecordDetail | null;
}

function orderedTree(nodes: ComponentNode[]): Array<{
  node: ComponentNode;
  depth: number;
}> {
  const byParent = new Map<string | null, ComponentNode[]>();
  for (const node of nodes) {
    const key = node.parentComponentId;
    const bucket = byParent.get(key) ?? [];
    bucket.push(node);
    byParent.set(key, bucket);
  }
  const known = new Set(nodes.map((node) => node.componentId));
  const out: Array<{ node: ComponentNode; depth: number }> = [];
  const visit = (parent: string | null, depth: number) => {
    for (const node of byParent.get(parent) ?? []) {
      out.push({ node, depth });
      visit(node.componentId, depth + 1);
    }
  };
  visit(null, 0);
  for (const node of nodes) {
    if (node.parentComponentId && !known.has(node.parentComponentId)) {
      out.push({ node, depth: 0 });
      visit(node.componentId, 1);
    }
  }
  return out;
}

export function RecordPanel({ detail }: RecordPanelProps) {
  return (
    <section className="inspection-section" aria-labelledby="record-heading">
      <div className="section-heading-row">
        <h2 id="record-heading">STATE RECORD</h2>
        <span className="micro-label">
          {detail ? (detail.payloadSchema ?? "JSON") : "EMPTY"}
        </span>
      </div>
      {detail ? (
        <>
          {detail.counts && (
            <dl className="stat-grid">
              <div>
                <dt>Entities</dt>
                <dd>{detail.counts.entities}</dd>
              </div>
              <div>
                <dt>Parameters</dt>
                <dd>{detail.counts.parameters}</dd>
              </div>
              <div>
                <dt>Relations</dt>
                <dd>{detail.counts.relations}</dd>
              </div>
              <div>
                <dt>Obligations</dt>
                <dd>{detail.counts.obligations}</dd>
              </div>
            </dl>
          )}
          {detail.stateDigest && (
            <div className="bounds-readout">
              <span>STATE DIGEST</span>
              <strong>{detail.stateDigest.slice(0, 16)}…</strong>
              <small>{detail.relativePath}</small>
            </div>
          )}
          {detail.componentTree && detail.componentTree.length > 0 && (
            <ul className="component-tree">
              {orderedTree(detail.componentTree).map(({ node, depth }) => (
                <li
                  key={node.componentId}
                  style={{ paddingLeft: `${depth * 14}px` }}
                >
                  <strong>{node.componentId}</strong>
                  <small>
                    {node.semanticKind} · {node.maturity} · r{node.revision}
                  </small>
                </li>
              ))}
            </ul>
          )}
          {detail.componentTreeError && (
            <div className="warning-block">
              <strong>Component tree unavailable</strong>
              <p>{detail.componentTreeError}</p>
            </div>
          )}
        </>
      ) : (
        <p className="empty-copy">选择一条记录后，这里显示它的构件树与计数。</p>
      )}
    </section>
  );
}
```

- [ ] **Step 5: Rework `App.tsx`**

Keep the top bar, viewport center section, MODEL INSPECTION + LAYERS sections exactly as they are. Replace the
gateway wiring and the left panel content, and mount `RecordPanel` at the top of the right panel:

- Delete the imports of `CapabilityPanel`, `StageRail`, `StudioGatewaySnapshot`; add imports of
  `ProjectBrowser`, `RecordPanel` and the new contract types.
- Replace the `gatewaySnapshot` state with:

```tsx
  const [gatewayStatus, setGatewayStatus] = useState<
    "loading" | "online" | "offline"
  >("loading");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [records, setRecords] = useState<RecordSummary[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [recordDetail, setRecordDetail] = useState<StudioRecordDetail | null>(
    null,
  );

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      gateway.health(controller.signal),
      gateway.projects(controller.signal),
    ])
      .then(([health, projectList]) => {
        setGatewayStatus(health.status === "ok" ? "online" : "offline");
        setProjects(projectList.projects);
      })
      .catch(() => setGatewayStatus("offline"));
    return () => controller.abort();
  }, []);

  const selectProject = (projectId: string) => {
    setSelectedProject(projectId);
    setSelectedRun(null);
    setRecords([]);
    setRecordDetail(null);
    gateway.runs(projectId).then((list) => setRuns(list.runs)).catch(() => setRuns([]));
    gateway
      .artifacts(projectId)
      .then((list) => setArtifacts(list.artifacts))
      .catch(() => setArtifacts([]));
  };

  const selectRun = (runId: string) => {
    if (!selectedProject) return;
    setSelectedRun(runId);
    setRecordDetail(null);
    gateway
      .records(selectedProject, runId)
      .then((list) => setRecords(list.records))
      .catch(() => setRecords([]));
  };

  const selectRecord = (record: RecordSummary) => {
    if (!selectedProject) return;
    gateway
      .recordDetail(selectedProject, record.relativePath, record.sha256)
      .then(setRecordDetail)
      .catch(() => setRecordDetail(null));
  };

  const openArtifact = (artifact: ArtifactSummary) => {
    if (!selectedProject) return;
    gateway
      .artifactFile(selectedProject, artifact.relativePath)
      .then((file) => viewportRef.current?.openFile(file))
      .catch(() => undefined);
  };
```

- In the left `<aside>`, replace the MODEL SOURCE + StageRail + AUTHORITY stack with: the existing MODEL SOURCE
  section (unchanged — local drag-in stays), then
  `<ProjectBrowser projects={projects} selectedProject={selectedProject} onSelectProject={selectProject} runs={runs} selectedRun={selectedRun} onSelectRun={selectRun} records={records} selectedRecordPath={recordDetail?.relativePath ?? null} onSelectRecord={selectRecord} artifacts={artifacts} onOpenArtifact={openArtifact} />`,
  then the AUTHORITY section (unchanged).
- In the right `<aside>`, replace `<CapabilityPanel …/>` with `<RecordPanel detail={recordDetail} />`.
- Update the header subtitle from `PREVIEW SLICE 01` to `VIBE MODELING · READ-ONLY SLICE`.
- Delete `client/web/src/components/CapabilityPanel.tsx` and `client/web/src/components/StageRail.tsx`
  (`git rm`).

- [ ] **Step 6: Additive styles in `client/web/src/styles.css`**

```css
.browser-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
  max-height: 180px;
  overflow-y: auto;
}

.browser-item {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 6px 8px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
  text-align: left;
  color: inherit;
  font: inherit;
}

.browser-item small {
  opacity: 0.65;
  font-size: 11px;
  word-break: break-all;
}

.browser-item:hover {
  border-color: rgba(127, 127, 127, 0.35);
}

.browser-item--active {
  border-color: rgba(127, 127, 127, 0.6);
  background: rgba(127, 127, 127, 0.12);
}

.component-tree {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: grid;
  gap: 4px;
  max-height: 260px;
  overflow-y: auto;
}

.component-tree li {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.component-tree small {
  opacity: 0.65;
  font-size: 11px;
}
```

(If existing class names differ from what the moved `styles.css` actually defines, reuse the file's own
conventions — additive only.)

- [ ] **Step 7: Typecheck and build**

```powershell
cd client\web
npm run typecheck
npm run build
```

Expected: both succeed with no unused-import errors (deleted components fully unreferenced).

- [ ] **Step 8: Live smoke test against the real workspace**

Start the server (repo root): `py -3.12 -m server.main` (background). Start `npm run dev` in `client/web`.
Open `http://127.0.0.1:5174`; verify: projects list shows the workspace projects (villa-rotonda-reconstruction,
rocca-pisana, …), selecting villa → runs list, selecting a run → records, selecting a state record → component
tree renders, clicking a `.3dm` artifact loads it into the viewport. Then stop both processes.

- [ ] **Step 9: Commit**

```powershell
git add client/web
git commit -m "P108: the client browses projects, records and artifacts through the gateway alone

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Launch pattern, docs, full verification battery

**Files:**
- Rewrite: `server/launch.py` (moved in Task 6; adapt the detached pattern to uvicorn)
- Create: `server/README.md`, `client/README.md`,
  `docs/claude-worktree/2026-09-03-vibe-modeling-architecture.md`

**Interfaces:**
- Consumes: `server.main` (Task 1), the demo launch pattern (health-poll + detached spawn + PID file).

- [ ] **Step 1: Adapt `server/launch.py`**

```python
"""Start the vibe-modeling server as a durable, local background process.

The detached-spawn pattern is harvested from the Studio demo's launch.py
(health poll, DETACHED_PROCESS, PID + log files under a .generated runtime
directory that is not project evidence).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
HEALTH_URL = "http://127.0.0.1:8000/api/health"
RUNTIME_ROOT = REPO_ROOT / "server" / ".generated" / "runtime"


def server_is_ready() -> bool:
    try:
        with urlopen(HEALTH_URL, timeout=2) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False
    return (
        payload.get("schema") == "StudioHealth@2"
        and payload.get("service") == "archflow-vibe-server"
    )


def launch() -> int:
    if server_is_ready():
        print("vibe-modeling server is already running: http://127.0.0.1:8000")
        return 0

    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    stdout_path = RUNTIME_ROOT / "server.stdout.log"
    stderr_path = RUNTIME_ROOT / "server.stderr.log"
    command = [sys.executable, "-m", "server.main"]

    creation_flags = 0
    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        popen_kwargs["start_new_session"] = True

    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creation_flags,
            **popen_kwargs,
        )

    for _ in range(40):
        if server_is_ready():
            (RUNTIME_ROOT / "server.pid").write_text(
                str(process.pid), encoding="ascii"
            )
            print("vibe-modeling server started: http://127.0.0.1:8000")
            print(f"PID: {process.pid}")
            return 0
        if process.poll() is not None:
            break
        time.sleep(0.25)

    if process.poll() is None:
        process.terminate()
    detail = ""
    if stderr_path.is_file():
        detail = "\n".join(
            stderr_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()[-12:]
        )
    raise RuntimeError(f"vibe-modeling server failed to start.\n{detail}".rstrip())


if __name__ == "__main__":
    raise SystemExit(launch())
```

Add `server/.generated/` to `client/web/.gitignore`? No — give `server/` its own `.gitignore`:

```
.generated/
__pycache__/
```

- [ ] **Step 2: Write `server/README.md`**

```markdown
# vibe-modeling server (P108)

FastAPI application that orchestrates and delegates to `archflow`. Read-only
slice: browse P036 projects, runs, records (digest-verified), component trees,
and exported `.3dm` artifacts. It computes no geometry and writes nothing.

Run from the repo root:

```powershell
py -3.12 -m pip install -r server/requirements.txt
py -3.12 -m server.main            # foreground, http://127.0.0.1:8000
py -3.12 server/launch.py          # detached background process
py -3.12 -m unittest discover -s server/tests -t . -v
py -3.12 tools/archcheck.py        # must stay ARCHITECTURE PASS
```

Configuration: `ARCHFLOW_PROJECTS_ROOT` overrides the default V4_RUNTIME
workspace projects directory (see `server/settings.py`).

Seams: `server/ports.py` (reserved application ports — `IntentProvider` is the
vibe-modeling seam), `shared/contracts/` (transport shapes only),
`client/web/src/gateway/` (the browser's only backend surface).
```

- [ ] **Step 3: Write `client/README.md`**

```markdown
# vibe-modeling client (P108)

React 19 + three.js + rhino3dm-wasm browser app (moved from the Studio demo).
It renders what the server serves and computes no geometry.

```powershell
cd client/web
npm install
npm run backend    # starts the FastAPI server from the repo root
npm run dev        # http://127.0.0.1:5174 (proxies /api to :8000)
npm run build
```
```

- [ ] **Step 4: Write the architecture doc**

`docs/claude-worktree/2026-09-03-vibe-modeling-architecture.md` — content per this outline (spec §31 item 1):
the three trees and their firewall rules; the seam procedure (declare in `server/ports.py` → payloads in
`shared/contracts/` → implement by delegation → expose through the gateway); the API table (route → transport
model → archflow delegation target); what is deliberately NOT here yet (mutation engine, planner, intent,
leakage — next plans, per the brief §6 order); known limitations (repository cache is per-process and refreshed
on restart; artifact discovery is an rglob over the project directory, read-only; preview is served `.3dm`
artifacts until P107 lands sub-second meshes; the demo's `/api/capabilities`+`/api/session` routes died with
the demo shell — the client no longer calls them).

- [ ] **Step 5: Full verification battery**

```powershell
py -3.12 tools/archcheck.py
py -3.12 -m unittest discover -s server/tests -t . -v
py -3.12 -m unittest discover -s tests -v
py -3.12 -m unittest discover -s apps/archflow-studio/backend/tests -t apps/archflow-studio -v
cd client\web; npm run typecheck; npm run build
```

Expected: all green (`ARCHITECTURE PASS`, kernel suite `OK`, server `OK`, demo `OK`, client builds).

- [ ] **Step 6: Commit**

```powershell
git add server/launch.py server/README.md server/.gitignore client/README.md docs/claude-worktree/2026-09-03-vibe-modeling-architecture.md
git commit -m "P108: launch pattern harvested, trees documented, full battery green

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

1. **Spec coverage (Phases 1–2 as re-scoped):** repo structure → Tasks 1/6; schemas → Task 1 (transport only;
   canonical schema EXISTS in archflow per the brief §4 mapping); project state/component tree/dependency
   graph → served by delegation (Task 4); villa fixture → browsed live (Task 7 smoke test; record population is
   the mutation plan's job); 3DM export → existing artifacts served (Task 5; new exports wait for the mutation
   plan); client UX skeleton (spec §21 left/center/right) → Task 7; architecture.md → Task 8. Deliberately
   deferred: §8–§12, §17–§20, §23–§25 integration mutations, §31's mutation — named in the plan header.
2. **Placeholder scan:** all steps carry complete code or exact commands; the two "adapt if reality differs"
   notes (styles.css conventions, README trim) are bounded editorial instructions, not deferred design.
3. **Type consistency:** `StudioSettings(projects_root)` used identically in Tasks 1/4/5 tests;
   `ProjectCatalog` method names match between Task 3 implementation and Task 4/5 routes; TS contract keys
   match the Pydantic aliases (`projectId`, `relativePath`, `sha256`, `componentTree`, …);
   `artifactFile` returns `File` consumed by `ViewportController.openFile(file: File)`.

Execution risks called out for the implementer: (a) `put_json` may enforce payload identity fields not
anticipated here — if `make_project` fails, read the error and extend `STATE_RECORD`/`initial_state` minimally;
(b) `FilesystemProjectRepository.open(...).verify()` cost on the real villa project is unmeasured — if the live
smoke test in Task 7 is slow, note it in the architecture doc as a known limitation (do NOT bypass the
repository); (c) demo README trim must not delete the ownership section that still governs the retained
backend.
