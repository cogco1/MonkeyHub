> **SUPERSEDED (2026-09-03, same day).** Task 1 of this plan was implemented, tested green, then archived as
> `2026-09-03-p108-stdlib-task1-wip.patch` and reverted uncommitted when Kaiwen adopted codex's review
> (FastAPI + rebuild inside the studio namespace, chain-first). The standing plan is
> `2026-09-03-p108-refoundation-plan.md`.

# P108 Round 1 / Plan A — In-Place Read Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grow `apps/archflow-studio` in place into the read-only half of the round-1 C/S vertical slice:
server-configured villa project binding, exact HEAD + StateRecord projection + component tree + dependency
edges, `.3dm` artifacts with ref/SHA/bytes, semantic pick resolution, a real SSE event stream, and the reviewed
UI shell — with three-state check display and zero fake UI.

**Architecture:** Per commit `83c6856` / brief Addendum 2: no new trees, no FastAPI. The stdlib
`backend/server.py` gains routes; `backend/kernel.py` grows from a presence probe into the read-only
orchestration facade delegating to `archflow`; `backend/contracts.py` and `src/contracts/studio.ts` stay
one-to-one transport pairs; `src/gateway` gains the calls. Proposal/intent/impact/validation is **Plan B**
(`docs/claude-worktree/2026-09-03-p108-round1-plan-b-proposal-chain.md`, written after this plan executes).

**Tech Stack:** Python 3.12 stdlib only (backend), React 19 + three.js + rhino3dm-wasm + Vite (frontend),
`unittest` via `npm run test:backend`.

## Global Constraints

- Write scope: `apps/archflow-studio/` only, plus dated lane docs in `docs/claude-worktree/`. Kernel gaps are
  carded to Kaiwen, never fixed in passing. `server/` and `shared/` root directories are firewall tripwires —
  never create them.
- Firewall: `apps/archflow-studio/backend` never imports `rhino3dm`, `numpy`, `networkx`, `OCP`, `build123d`,
  `shapely`, `trimesh`, `scipy`, `tests`, `tools`, `probes`. Run `py -3.12 tools/archcheck.py` after every
  backend change; must print `ARCHITECTURE PASS`.
- Browser law (Addendum 2 §6): no importing archflow; no client-side impact inference; no client-side
  "validation passed"; chat is not version history; no writes to project dir or canonical HEAD; a `.3dm`'s
  presence never implies success. Also: no new databases, no networkx, no studio-side
  MutationEngine/ProjectState/Validator, no parallel version history.
- Three-state law: held / violated / unchecked are three distinct states; unchecked is NEVER rendered green;
  the server issues any verdict.
- Round-1 boundary: one server-configured external project root (explicit config, not hardcoded logic);
  proposal-only; canonical write, live model providers, login identity all disabled. Missing data stops
  visibly, never silently invented.
- No fake UI: a panel exists only when its data path is real. Intent/proposal panels arrive with Plan B.
- Reuse law: project identity via `open_located_project()` / `FilesystemProjectRepository`; component tree via
  `StateRecord` entities (`design_components_of`); dependencies via `StateRecord.dependency_edges()`. Never
  re-derive kernel answers.
- Transport shapes only: `backend/contracts.py` dataclasses ↔ `src/contracts/studio.ts` interfaces,
  one-to-one, no domain-model copies.
- Git: stage explicit paths only; commit style matches repo; end commits with
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Backend tests: `py -3.12 -m unittest discover -s apps/archflow-studio/backend/tests -t apps/archflow-studio -v`
  from the repo root (repo root on `sys.path` gives archflow importability; mirror what `run_server.py` does if
  a test needs it explicitly).

---

### Task 1: Project binding — configured external root into the kernel facade

**Files:**
- Modify: `apps/archflow-studio/backend/kernel.py`, `apps/archflow-studio/backend/contracts.py`,
  `apps/archflow-studio/backend/server.py`, `apps/archflow-studio/run_server.py`
- Create: `apps/archflow-studio/backend/tests/support.py`
- Test: `apps/archflow-studio/backend/tests/test_project_binding.py`
- Modify: `docs/claude-worktree/2026-09-03-p108-phase1-foundation-plan.md` (supersession banner only)

**Interfaces:**
- Consumes: `archflow.project.location.open_located_project(project_id, *, local_projects_root)` →
  `(location, FilesystemProjectRepository)`; `repository.read_head()` → `ProjectVersionRef(project_id,
  version, state_sha256)`.
- Produces: `ArchFlowKernelFacade(project_dir: Path | None = None)`;
  `facade.project_binding() -> StudioProjectBinding`; `StudioProjectBinding` dataclass
  (`schema "StudioProjectBinding@1"`, fields `bound: bool`, `project_id: str | None`,
  `head_version: int | None`, `state_sha256: str | None`, `project_dir: str | None`, `detail: str`);
  route `GET /api/project`; env var `ARCHFLOW_STUDIO_PROJECT_DIR` and CLI `--project-dir` on `run_server.py`.

- [ ] **Step 1: Supersession banner on the reversed-layout plan**

Prepend to `docs/claude-worktree/2026-09-03-p108-phase1-foundation-plan.md` (above the title):

```markdown
> **SUPERSEDED (2026-09-03, same day).** This plan implemented the /client /server /shared + FastAPI decision
> that commit 83c6856 reversed. Nothing in it was executed. The standing plans are
> `2026-09-03-p108-round1-plan-a-read-slice.md` and `2026-09-03-p108-round1-plan-b-proposal-chain.md`,
> which build the slice in place on the studio seams per the brief's Addendum 2.

```

- [ ] **Step 2: Write the failing test**

`apps/archflow-studio/backend/tests/support.py`:

```python
"""Builds a real, minimal P036 project for studio backend tests (no mocks)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from archflow.project.ports import PersistenceArea, PersistenceDestination  # noqa: E402
from archflow.project.repository import FilesystemProjectRepository  # noqa: E402

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
        {
            "entity_id": "ground",
            "schema": "Level@1",
            "fields": {"role": "ground", "elevation": 0.0},
            "parent_id": None,
        },
        {
            "entity_id": "portico-columns",
            "schema": "Element@1",
            "fields": {
                "producer": "produce_column_array",
                "component": "portico",
                "level": "ground",
                "height_m": 5.4,
            },
            "parent_id": "portico",
        },
    ],
    "parameters": [],
    "relations": [],
    "obligations": [],
}


def make_project(root: Path) -> tuple[FilesystemProjectRepository, str, str]:
    """Initialize a project with one run, one retained record, one input record.

    Returns (repository, record_relative_path, record_sha256). The runner-style
    input record lands at input/runner/state-record.json exactly like the villa.
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
    import json

    runner_dir = repository.layout.inputs / "runner"
    runner_dir.mkdir(parents=True, exist_ok=True)
    (runner_dir / "state-record.json").write_text(
        json.dumps(STATE_RECORD, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return repository, ref.relative_path, ref.sha256
```

`apps/archflow-studio/backend/tests/test_project_binding.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.tests.support import PROJECT_ID, make_project
from backend.kernel import ArchFlowKernelFacade


class ProjectBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        make_project(self.root)

    def test_unbound_facade_reports_unbound(self) -> None:
        binding = ArchFlowKernelFacade().project_binding()
        self.assertFalse(binding.bound)
        self.assertIsNone(binding.project_id)

    def test_bound_facade_reports_exact_head(self) -> None:
        facade = ArchFlowKernelFacade(project_dir=self.root / PROJECT_ID)
        binding = facade.project_binding()
        self.assertTrue(binding.bound)
        self.assertEqual(binding.project_id, PROJECT_ID)
        self.assertEqual(binding.head_version, 0)
        self.assertEqual(len(binding.state_sha256), 64)

    def test_missing_project_dir_is_a_visible_error_not_a_guess(self) -> None:
        facade = ArchFlowKernelFacade(project_dir=self.root / "absent")
        binding = facade.project_binding()
        self.assertFalse(binding.bound)
        self.assertIn("absent", binding.detail)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test, verify it fails**

Run: `py -3.12 -m unittest apps.archflow-studio... ` — not importable as dotted path; use discovery:
`py -3.12 -m unittest discover -s apps/archflow-studio/backend/tests -t apps/archflow-studio -v`
Expected: ERROR — `ArchFlowKernelFacade() got an unexpected keyword argument 'project_dir'` /
`AttributeError: project_binding`.

- [ ] **Step 4: Implement**

`backend/contracts.py` — append:

```python
@dataclass(frozen=True, slots=True)
class StudioProjectBinding:
    bound: bool
    project_id: str | None = None
    head_version: int | None = None
    state_sha256: str | None = None
    project_dir: str | None = None
    detail: str = ""

    SCHEMA = "StudioProjectBinding@1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "bound": self.bound,
            "projectId": self.project_id,
            "headVersion": self.head_version,
            "stateSha256": self.state_sha256,
            "projectDir": self.project_dir,
            "detail": self.detail,
            "canonicalWriteAuthority": False,
        }
```

`backend/kernel.py` — the facade grows real state (keep the existing health/capabilities/session methods;
`session()` now reports the bound project):

```python
"""Read-only orchestration facade: binds one configured P036 project."""

from __future__ import annotations

import sys
from importlib.util import find_spec
from pathlib import Path

from backend.contracts import (
    CapabilityAvailability,
    CapabilityDescriptor,
    StudioHealthSnapshot,
    StudioProjectBinding,
    StudioSessionSnapshot,
)

_APP_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _APP_ROOT.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
```

(keep `_module_available` as is), then inside the class:

```python
class ArchFlowKernelFacade:
    """Read-only/proposal facade; never a second mutation authority."""

    def __init__(self, project_dir: Path | None = None) -> None:
        self._project_dir = Path(project_dir) if project_dir else None
        self._repository = None
        self._bind_error: str | None = None

    def _bound_repository(self):
        if self._repository is not None:
            return self._repository
        if self._project_dir is None:
            self._bind_error = "no project directory is configured"
            return None
        try:
            from archflow.project.location import open_located_project

            _, repository = open_located_project(
                self._project_dir.name,
                local_projects_root=self._project_dir.parent,
            )
        except Exception as exc:  # visible failure, never a guess
            self._bind_error = f"{self._project_dir}: {exc}"
            return None
        self._repository = repository
        return repository

    def project_binding(self) -> StudioProjectBinding:
        repository = self._bound_repository()
        if repository is None:
            return StudioProjectBinding(
                bound=False,
                project_dir=(
                    str(self._project_dir) if self._project_dir else None
                ),
                detail=self._bind_error or "no project directory is configured",
            )
        head = repository.read_head()
        return StudioProjectBinding(
            bound=True,
            project_id=head.project_id,
            head_version=head.version,
            state_sha256=head.state_sha256,
            project_dir=str(self._project_dir),
            detail="read-only binding; canonical write stays disabled",
        )
```

and `session()` becomes:

```python
    def session(self) -> StudioSessionSnapshot:
        binding = self.project_binding()
        return StudioSessionSnapshot(
            project_id=binding.project_id if binding.bound else None
        )
```

`backend/server.py`: `StudioRequestHandler.do_GET` gains, before the 404 fallthrough:

```python
        if path == "/api/project":
            self._send_json(200, self._kernel.project_binding().to_dict())
            return
```

`run_server.py` `main()` gains the configuration (explicit, not hardcoded logic):

```python
    parser.add_argument(
        "--project-dir",
        default=os.environ.get("ARCHFLOW_STUDIO_PROJECT_DIR"),
        help="P036 project directory to bind read-only (env ARCHFLOW_STUDIO_PROJECT_DIR)",
    )
    args = parser.parse_args()
    project_dir = Path(args.project_dir) if args.project_dir else None
    run(
        host=args.host,
        port=args.port,
        static_dir=APP_ROOT / "dist",
        project_dir=project_dir,
    )
```

with `import os` added, and `backend/server.py`'s `create_server`/`run` accepting and forwarding
`project_dir: Path | None = None` into `ArchFlowKernelFacade(project_dir=project_dir)`.

- [ ] **Step 5: Run tests + firewall**

Run: `py -3.12 -m unittest discover -s apps/archflow-studio/backend/tests -t apps/archflow-studio -v` → `OK`
(existing `test_api.py` must stay green — `create_server` keeps working with no `project_dir`).
Run: `py -3.12 tools/archcheck.py` → `ARCHITECTURE PASS`

- [ ] **Step 6: Commit**

```powershell
git add apps/archflow-studio docs/claude-worktree/2026-09-03-p108-phase1-foundation-plan.md docs/claude-worktree/2026-09-03-p108-round1-plan-a-read-slice.md
git commit -m "P108 round 1: the studio binds one configured project, read-only, in place

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Read model — runs, records, StateRecord projection with tree and dependencies

**Files:**
- Modify: `backend/kernel.py`, `backend/contracts.py`, `backend/server.py`
- Test: `backend/tests/test_read_model.py`

**Interfaces:**
- Consumes: `repository.layout.runs` / `layout.resolve_relative`, `repository.load_run`,
  `repository.list_json(run=, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=))`,
  `repository.load_json(ProjectRecordRef)`, `archflow.state.state_record.StateRecord.from_dict`,
  `design_components_of(record)`, `record.dependency_edges()`, `record.state_digest()`.
- Produces: facade methods `run_ids()`, `record_refs(run_id)`, `load_record(relative_path, sha256)`,
  `state_projection()`; contracts `StudioRunList@1`, `StudioRecordList@1` (entries: `recordKind`,
  `relativePath`, `sha256`), `StudioStateProjection@1` (`sourcePath`, `stateDigest`, `counts` {entities,
  parameters, relations, obligations}, `componentTree` nodes {componentId, parentComponentId, semanticKind,
  intent, maturity, revision}, `dependencyEdges` {source, target, kind, propagation}, `stageBinding`
  {workflowRef, envelopeRef, stageId}, `checksSummary` {relations, withValidator, held, violated, unchecked,
  source}), `StudioRecordDetail@1` (`relativePath`, `sha256`, `payloadSchema`, `payload`);
  routes `GET /api/project/runs`, `GET /api/project/runs/<run_id>/records`,
  `GET /api/project/record?path=&sha256=`, `GET /api/project/state`; kernel exceptions
  `StudioLookupError(LookupError)` with `code` attribute mapped to typed 404s.
- Note: with no relation-check report in scope, `checksSummary` reports `held=0, violated=0,
  unchecked=<relation count>` and `source="static record summary; no relation-check report loaded"` — the
  three-state law starts here.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_read_model.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.kernel import ArchFlowKernelFacade, StudioLookupError
from backend.tests.support import PROJECT_ID, RUN_ID, make_project


class ReadModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        _, self.record_path, self.record_sha = make_project(root)
        self.facade = ArchFlowKernelFacade(project_dir=root / PROJECT_ID)

    def test_runs_and_records_are_listed(self) -> None:
        self.assertEqual(self.facade.run_ids(), (RUN_ID,))
        refs = self.facade.record_refs(RUN_ID)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].sha256, self.record_sha)

    def test_record_loads_digest_verified(self) -> None:
        payload = self.facade.load_record(self.record_path, self.record_sha)
        self.assertEqual(payload["schema"], "StateRecord@1")
        with self.assertRaises(StudioLookupError):
            self.facade.load_record(self.record_path, "0" * 64)

    def test_state_projection_serves_tree_edges_and_summary(self) -> None:
        projection = self.facade.state_projection()
        payload = projection.to_dict()
        self.assertEqual(payload["schema"], "StudioStateProjection@1")
        self.assertEqual(payload["counts"]["entities"], 4)
        tree = {n["componentId"]: n for n in payload["componentTree"]}
        self.assertEqual(tree["portico"]["parentComponentId"], "building")
        self.assertEqual(payload["checksSummary"]["unchecked"], 0)
        self.assertEqual(payload["checksSummary"]["relations"], 0)
        self.assertEqual(len(payload["stateDigest"]), 64)

    def test_unknown_run_raises_typed_lookup(self) -> None:
        with self.assertRaises(StudioLookupError):
            self.facade.record_refs("absent-run")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `py -3.12 -m unittest discover -s apps/archflow-studio/backend/tests -t apps/archflow-studio -v`
Expected: ERROR — `ImportError: cannot import name 'StudioLookupError'`.

- [ ] **Step 3: Implement**

`backend/contracts.py` — append transport dataclasses (all frozen, slots, `to_dict` with camelCase keys and a
`SCHEMA`): `StudioRunList` (run_ids tuple → `{"schema": "StudioRunList@1", "projectId", "runs": [{"runId"}]}`),
`StudioRecordList`, `StudioRecordDetail`, `StudioComponentNode`, `StudioDependencyEdge`,
`StudioChecksSummary`, `StudioStateProjection`. Exact field names as the Interfaces block above; follow the
existing file's dataclass style.

`backend/kernel.py` — append:

```python
class StudioLookupError(LookupError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
```

facade methods (each starts with `repository = self._require_repository()` which raises
`StudioLookupError("PROJECT_NOT_BOUND", ...)` when unbound):

```python
    def _require_repository(self):
        repository = self._bound_repository()
        if repository is None:
            raise StudioLookupError(
                "PROJECT_NOT_BOUND",
                self._bind_error or "no project directory is configured",
            )
        return repository

    def run_ids(self) -> tuple[str, ...]:
        repository = self._require_repository()
        runs_dir = repository.layout.runs
        if not runs_dir.is_dir():
            return ()
        return tuple(
            sorted(c.name for c in runs_dir.iterdir() if c.is_dir())
        )

    def record_refs(self, run_id: str):
        repository = self._require_repository()
        if run_id not in self.run_ids():
            raise StudioLookupError("RUN_NOT_FOUND", run_id)
        from archflow.project.ports import (
            PersistenceArea,
            PersistenceDestination,
        )

        try:
            run = repository.load_run(run_id)
            refs = repository.list_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD, run_id=run_id
                ),
            )
        except Exception as exc:
            raise StudioLookupError("RUN_NOT_FOUND", f"{run_id}: {exc}") from exc
        return tuple(sorted(refs, key=lambda ref: ref.relative_path))

    def load_record(self, relative_path: str, sha256: str) -> dict:
        repository = self._require_repository()
        from archflow.project.refs import ProjectRecordRef

        try:
            ref = ProjectRecordRef(
                repository.load_manifest().project_id, relative_path, sha256
            )
            return repository.load_json(ref)
        except Exception as exc:
            raise StudioLookupError(
                "RECORD_NOT_FOUND", f"{relative_path}: {exc}"
            ) from exc
```

`state_projection()` loads `input/runner/state-record.json` through the layout (visible failure when absent),
parses with `StateRecord.from_dict`, and assembles the transport projection via `design_components_of` and
`dependency_edges` — the kernel's own answers:

```python
    def state_projection(self):
        repository = self._require_repository()
        import json as _json

        from archflow.state.state_record import (
            StateRecord,
            StateRecordError,
            design_components_of,
        )
        from backend.contracts import (
            StudioChecksSummary,
            StudioComponentNode,
            StudioDependencyEdge,
            StudioStateProjection,
        )

        source = "input/runner/state-record.json"
        path = repository.layout.resolve_relative(source)
        if not path.is_file():
            raise StudioLookupError(
                "STATE_RECORD_NOT_FOUND",
                f"{source} is absent; the project carries no runner record",
            )
        record = StateRecord.from_dict(
            _json.loads(path.read_text(encoding="utf-8"))
        )
        tree_error = None
        nodes: tuple = ()
        try:
            nodes = tuple(
                StudioComponentNode(
                    component_id=c.component_id,
                    parent_component_id=c.parent_component_id,
                    semantic_kind=c.semantic_kind,
                    intent=c.intent,
                    maturity=c.maturity.value,
                    revision=c.revision,
                )
                for c in design_components_of(record)
            )
        except StateRecordError as exc:
            tree_error = str(exc)
        edges = tuple(
            StudioDependencyEdge(
                source=e.source, target=e.target, kind=str(e.kind),
                propagation=str(getattr(e, "propagation", "") or ""),
            )
            for e in record.dependency_edges()
        )
        with_validator = sum(
            1 for r in record.relations if r.validator is not None
        )
        summary = StudioChecksSummary(
            relations=len(record.relations),
            with_validator=with_validator,
            held=0,
            violated=0,
            unchecked=len(record.relations),
            source="static record summary; no relation-check report loaded",
        )
        return StudioStateProjection(
            source_path=source,
            state_digest=record.state_digest(),
            entities=len(record.entities),
            parameters=len(record.parameters),
            relations=len(record.relations),
            obligations=len(record.obligations),
            component_tree=nodes,
            component_tree_error=tree_error,
            dependency_edges=edges,
            stage_workflow_ref=record.stage.workflow_ref,
            stage_envelope_ref=record.stage.envelope_ref,
            stage_id=record.stage.stage_id,
            checks_summary=summary,
        )
```

(`DependencyEdge` field names verified at implementation time — adjust the two attribute reads to the real
dataclass, nothing else.)

`backend/server.py` `do_GET` — add routes before the 404 fallthrough; wrap the kernel calls:

```python
        try:
            if path == "/api/project/runs":
                self._send_json(200, self._kernel_run_list())
                return
            match = _RUN_RECORDS_ROUTE.fullmatch(path)
            if match:
                self._send_json(200, self._kernel_record_list(match.group(1)))
                return
            if path == "/api/project/record":
                query = parse_qs(urlsplit(self.path).query)
                self._send_json(
                    200,
                    self._kernel_record_detail(
                        (query.get("path") or [""])[0],
                        (query.get("sha256") or [""])[0],
                    ),
                )
                return
            if path == "/api/project/state":
                self._send_json(
                    200, self._kernel.state_projection().to_dict()
                )
                return
        except StudioLookupError as error:
            self._send_json(
                404,
                {
                    "schema": "StudioError@1",
                    "code": error.code,
                    "detail": error.detail,
                },
            )
            return
```

with module-level `_RUN_RECORDS_ROUTE = re.compile(r"/api/project/runs/([A-Za-z0-9_\-]+)/records")`, helper
methods building the list payloads from facade refs (`recordKind` = filename stem before the final `-`), and
imports `re`, `parse_qs`, `StudioLookupError`.

- [ ] **Step 4: Run tests + firewall**

Run: `py -3.12 -m unittest discover -s apps/archflow-studio/backend/tests -t apps/archflow-studio -v` → `OK`
Run: `py -3.12 tools/archcheck.py` → `ARCHITECTURE PASS`

- [ ] **Step 5: Commit**

```powershell
git add apps/archflow-studio/backend
git commit -m "P108 round 1: exact HEAD, runs, records and the StateRecord projection over the gateway

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Artifacts — ref, SHA, bytes, project binding

**Files:**
- Modify: `backend/kernel.py`, `backend/contracts.py`, `backend/server.py`
- Test: `backend/tests/test_artifacts.py`

**Interfaces:**
- Produces: facade `artifacts() -> tuple[StudioArtifact, ...]` (`relative_path`, `size_bytes`, `sha256`) and
  `artifact_bytes(relative_path) -> bytes` (layout-guarded, `.3dm` only, raises
  `StudioLookupError("ARTIFACT_NOT_FOUND", ...)`); contracts `StudioArtifactList@1` (with `projectId` — the
  binding travels with the list); routes `GET /api/project/artifacts`,
  `GET /api/project/artifact?path=` (`application/octet-stream`).

- [ ] **Step 1: Failing tests** — `backend/tests/test_artifacts.py`:

```python
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from backend.kernel import ArchFlowKernelFacade, StudioLookupError
from backend.tests.support import PROJECT_ID, make_project


class ArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        repository, _, _ = make_project(root)
        exports = repository.layout.exports
        exports.mkdir(parents=True, exist_ok=True)
        (exports / "model.3dm").write_bytes(b"3dm-bytes")
        self.expected_sha = hashlib.sha256(b"3dm-bytes").hexdigest()
        self.facade = ArchFlowKernelFacade(project_dir=root / PROJECT_ID)

    def test_lists_artifacts_with_sha(self) -> None:
        artifacts = self.facade.artifacts()
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].relative_path, "exports/model.3dm")
        self.assertEqual(artifacts[0].sha256, self.expected_sha)

    def test_serves_bytes(self) -> None:
        self.assertEqual(
            self.facade.artifact_bytes("exports/model.3dm"), b"3dm-bytes"
        )

    def test_escape_and_non_3dm_are_refused(self) -> None:
        with self.assertRaises(StudioLookupError):
            self.facade.artifact_bytes("../outside.3dm")
        with self.assertRaises(StudioLookupError):
            self.facade.artifact_bytes("project.json")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, verify FAIL** (missing methods).

- [ ] **Step 3: Implement** — kernel methods (rglob `*.3dm` under `layout.root`, sha256 each file, sorted;
`artifact_bytes` via `layout.resolve_relative` in try/except → `StudioLookupError`); contract `StudioArtifact`
+ `StudioArtifactList`; server routes (binary route writes bytes with
`Content-Type: application/octet-stream`, `Content-Length`, `Cache-Control: no-store` — add a `_send_bytes`
helper beside `_send_json`).

- [ ] **Step 4: Run all backend tests + firewall** → `OK`, `ARCHITECTURE PASS`.

- [ ] **Step 5: Commit**

```powershell
git add apps/archflow-studio/backend
git commit -m "P108 round 1: run artifacts travel as ref, SHA and bytes under the project binding

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Semantic pick resolution (server resolves; client only reads user strings)

**Files:**
- Modify: `backend/kernel.py`, `backend/contracts.py`, `backend/server.py`
- Test: `backend/tests/test_pick_resolution.py`

**Interfaces:**
- Consumes: the state projection's record (Component@1 / Element@1 entity ids); the `archflow:*` user-string
  key set (`archflow:component`, `archflow:object_ref`, `archflow:producer_op` — the M088 single-source set;
  never invent keys).
- Produces: facade `resolve_pick(user_strings: Mapping[str, str]) -> StudioPickResolution`
  (`status` ∈ `resolved` / `unbound` / `unknown_component`; `component_id`, `element_id`, `detail`);
  contract `StudioPickResolution@1`; route `POST /api/project/resolve-pick` (JSON body
  `{"userStrings": {...}}`) — the first real POST route; unknown `/api/` POSTs keep failing closed with 501.

- [ ] **Step 1: Failing tests** — `backend/tests/test_pick_resolution.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.kernel import ArchFlowKernelFacade
from backend.tests.support import PROJECT_ID, make_project


class PickResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        make_project(root)
        self.facade = ArchFlowKernelFacade(project_dir=root / PROJECT_ID)

    def test_component_user_string_resolves_uniquely(self) -> None:
        result = self.facade.resolve_pick(
            {"archflow:component": "portico",
             "archflow:object_ref": "object:portico-columns"}
        )
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.component_id, "portico")
        self.assertEqual(result.element_id, "portico-columns")

    def test_no_archflow_keys_is_unbound_not_a_guess(self) -> None:
        result = self.facade.resolve_pick({"material": "stone"})
        self.assertEqual(result.status, "unbound")
        self.assertIsNone(result.component_id)

    def test_unknown_component_is_typed(self) -> None:
        result = self.facade.resolve_pick({"archflow:component": "phantom"})
        self.assertEqual(result.status, "unknown_component")
        self.assertIn("phantom", result.detail)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement** — kernel `resolve_pick`: load the projection record (reuse a private
`_load_state_record()` extracted from `state_projection()`); component id from `archflow:component`; element
id from `archflow:object_ref` by stripping an `object:` prefix and matching an `Element@1`/entity id when
present (no match → element_id None, still resolved if the component matched); no `archflow:` keys at all →
`unbound` with detail "the picked object carries no archflow identity; it is not bound to this project";
component key present but absent from the record → `unknown_component`. Contract dataclass + server POST
route: parse JSON body (`Content-Length` read, `json.loads`, malformed → 400 typed error), route
`/api/project/resolve-pick`, keep the existing 501 fail-closed for every other `/api/` POST.

- [ ] **Step 4: Run all backend tests + firewall** → `OK`, `ARCHITECTURE PASS`.

- [ ] **Step 5: Commit**

```powershell
git add apps/archflow-studio/backend
git commit -m "P108 round 1: a picked object resolves server-side to its unique semantic component

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Real event stream — StudioEventSink implementation + SSE route

**Files:**
- Create: `backend/events.py`
- Modify: `backend/server.py`, `backend/kernel.py` (emit points)
- Test: `backend/tests/test_events.py`

**Interfaces:**
- Consumes: the reserved `StudioEventSink` protocol in `backend/ports.py` (`publish(*, event)`) — implemented,
  not duplicated.
- Produces: `backend.events.InMemoryStudioEventSink` with `publish(*, event: Mapping)`,
  `replay() -> tuple[dict, ...]` (ring buffer, last 200), `subscribe() -> queue.Queue`,
  `unsubscribe(q)`; every event stamped `{"schema": "StudioEvent@1", "seq": n, "at": iso8601, ...event}`;
  route `GET /api/events` (SSE: `Content-Type: text/event-stream`, replay then live `data: <json>\n\n`);
  emit points: `project-bound`/`project-bind-failed` (first successful/failed `_bound_repository`),
  `state-projected`, `artifact-served`, `pick-resolved`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_events.py`:

```python
from __future__ import annotations

import unittest

from backend.events import InMemoryStudioEventSink


class EventSinkTests(unittest.TestCase):
    def test_publish_stamps_schema_and_sequence(self) -> None:
        sink = InMemoryStudioEventSink()
        sink.publish(event={"type": "project-bound", "projectId": "demo"})
        sink.publish(event={"type": "pick-resolved"})
        replay = sink.replay()
        self.assertEqual(len(replay), 2)
        self.assertEqual(replay[0]["schema"], "StudioEvent@1")
        self.assertEqual(replay[0]["seq"], 1)
        self.assertEqual(replay[1]["seq"], 2)

    def test_subscribers_receive_live_events(self) -> None:
        sink = InMemoryStudioEventSink()
        q = sink.subscribe()
        sink.publish(event={"type": "artifact-served"})
        self.assertEqual(q.get(timeout=1)["type"], "artifact-served")
        sink.unsubscribe(q)

    def test_ring_buffer_caps_replay(self) -> None:
        sink = InMemoryStudioEventSink(capacity=3)
        for index in range(5):
            sink.publish(event={"type": "state-projected", "n": index})
        self.assertEqual([e["n"] for e in sink.replay()], [2, 3, 4])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, verify FAIL** (`backend.events` missing).

- [ ] **Step 3: Implement** — `backend/events.py` (threading.Lock around a deque + subscriber list;
`datetime.now(timezone.utc).isoformat()`); kernel facade takes `event_sink=None` kwarg and calls
`publish` at the four emit points (no-op when None); `create_server` builds one sink, hands it to the facade
and the handler; SSE handler loop: send replay, then block on `queue.get(timeout=15)` sending
`: keep-alive\n\n` on timeout, break on `BrokenPipeError`/`ConnectionAbortedError`. SSE endpoint test:
subscribe-then-HTTP smoke — open `http.client` request against a live `create_server` on port 0, publish one
event through the sink, read the first `data:` line with a short timeout, close.

- [ ] **Step 4: Run all backend tests + firewall** → `OK`, `ARCHITECTURE PASS`.

- [ ] **Step 5: Commit**

```powershell
git add apps/archflow-studio/backend
git commit -m "P108 round 1: the reserved StudioEventSink becomes a real SSE stream, replayed and live

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Client — contracts, gateway, and the reviewed shell (real panels only)

**Files:**
- Modify: `src/contracts/studio.ts` (add DTOs), `src/gateway/StudioGateway.ts`,
  `src/gateway/HttpStudioGateway.ts`, `src/App.tsx`, `src/styles.css` (additive),
  `src/ports/studioPorts.ts` (stage hardcode), `src/viewer/ThreeDmViewport.tsx` (pick + labeled source mode),
  `src/components/StageRail.tsx` (bind to stage ref, drop 1|2|3|4)
- Create: `src/components/ComponentTreePanel.tsx`, `src/components/ProjectPanel.tsx`,
  `src/components/ChecksPanel.tsx`, `src/components/EventStreamPanel.tsx`,
  `src/components/SelectionPanel.tsx`

**Interfaces:**
- Consumes: every route from Tasks 1–5.
- Produces: TS interfaces `StudioProjectBinding`, `StudioRunList`, `StudioRecordList`, `StudioRecordDetail`,
  `StudioStateProjection`, `StudioComponentNode`, `StudioDependencyEdge`, `StudioChecksSummary`,
  `StudioArtifactList`, `StudioPickResolution`, `StudioEvent` — keys exactly as the Python `to_dict`s;
  gateway methods `projectBinding()`, `runs()`, `records(runId)`, `recordDetail(path, sha256)`,
  `stateProjection()`, `artifacts()`, `artifactFile(relativePath)`, `resolvePick(userStrings)`,
  `openEvents(onEvent): () => void` (EventSource, returns close); viewer `ViewportController` gains
  `openFile(file, sourceLabel?)` and an `onPick(userStrings | null)` prop reading
  `object.userData.attributes.userStrings` from the Rhino3dmLoader scene graph (data only — resolution is the
  server's).
- UI layout (reviewed skeleton, real data only): top bar = project id / HEAD `v{n}` + short SHA / authority
  `READ-ONLY · PROPOSAL DISABLED (Plan B)`; left = ProjectPanel (binding, runs, records, artifacts) +
  ComponentTreePanel (tree from projection; click = select component); center = viewer with explicit source
  chip `CANONICAL ARTIFACT` / `LOCAL · UNBOUND` per loaded file; right = SelectionPanel (picked/selected
  component: entity ids, dependency edges touching it) + ChecksPanel (three-state chips: held green ONLY when
  >0 and violated=0 and unchecked=0; violated red; unchecked amber with count; plus the summary `source`
  line); bottom = EventStreamPanel (live SSE, newest last, seq shown). No intent input, no proposal panel, no
  validation verdict — those arrive with Plan B's real endpoints.

- [ ] **Step 1: Extend `src/contracts/studio.ts`** with the interfaces above (keep the existing
  health/capability types — the demo routes still serve them).

- [ ] **Step 2: Extend the gateway** — add the methods; `openEvents` uses `new EventSource
  ("/api/events")`, JSON-parses `event.data`, returns `() => source.close()`.

- [ ] **Step 3: Viewer pick + source label** — in `ThreeDmViewport.tsx`: add `sourceLabel` state through
  `openFile(file, sourceLabel = "LOCAL · UNBOUND")`; on `pointerdown`+`pointerup` within 5px, raycast
  (`Raycaster.setFromCamera`) into the model group, walk `intersects[0].object` up via `.parent` until an
  object with `userData?.attributes?.userStrings` (array of `[key, value]` pairs per rhino3dm loader) or the
  model root; convert pairs to a `Record<string, string>`; call `onPick(record | null)`. Keep all existing
  behavior.

- [ ] **Step 4: New panels** — each a small typed component over its DTO; `ComponentTreePanel` reuses the
  depth-indent walk (byParent map, roots = parentComponentId null or unknown); `ChecksPanel` renders the
  three-state chips per the color law; `EventStreamPanel` keeps the last 100 events in state.

- [ ] **Step 5: Rework `App.tsx`** — on mount: `projectBinding()`, `stateProjection()`, `artifacts()`,
  `runs()`, `openEvents`; wire clicks (run → `records(runId)`; record → `recordDetail`; artifact →
  `artifactFile` → `openFile(file, "CANONICAL ARTIFACT")`; viewer `onPick` → `resolvePick` → select resolved
  component in tree + SelectionPanel). Keep the local-file flow labeled `LOCAL · UNBOUND`. Keep MODEL
  INSPECTION and LAYERS sections. Replace `StageRail` usage with the stage line from the projection
  (`stageId` or `NO STAGE BINDING`).

- [ ] **Step 6: `src/ports/studioPorts.ts`** — replace the `stage: 1 | 2 | 3 | 4` field with
  `stage: string | null` documented as "a ProjectStageWorkflow stage id; null when the record carries no
  stage binding"; fix resulting type errors.

- [ ] **Step 7: Typecheck + build**

```powershell
cd apps\archflow-studio
npm run typecheck
npm run build
```

Expected: both green.

- [ ] **Step 8: Live smoke test against the villa**

Start backend bound to the villa (repo root):
`$env:ARCHFLOW_STUDIO_PROJECT_DIR = "D:\PROJECTS\01_ACTIVE_当前项目\ARCHFLOW CAADRIA 2027\V4_RUNTIME\workspace\projects\villa-rotonda-reconstruction"; py -3.12 apps/archflow-studio/run_server.py`
plus `npm run dev`; verify in the browser pane: binding shows villa + HEAD; tree renders the record's
components; runs/records list; a run artifact loads labeled CANONICAL; clicking geometry resolves a pick
(or reports `unbound` honestly for objects without archflow strings); events stream in the bottom panel;
checks panel shows `relations 0 · all unchecked` in amber, not green. Screenshot for the report; stop both.

- [ ] **Step 9: Commit**

```powershell
git add apps/archflow-studio/src apps/archflow-studio/package.json
git commit -m "P108 round 1: the reviewed shell over real routes only - bind, browse, pick, watch events

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Full battery, README, session note

**Files:**
- Modify: `apps/archflow-studio/README.md` (the slice grows: binding config, new routes, run instructions;
  planned-slices list updated honestly — slice 2 partially delivered read-only, proposal chain next)
- Verify: everything

- [ ] **Step 1: README** — document `ARCHFLOW_STUDIO_PROJECT_DIR` / `--project-dir`, the route table, the
  three-state display rule, and the round-1 boundary (proposal-only, canonical write disabled).

- [ ] **Step 2: Full battery**

```powershell
py -3.12 tools/archcheck.py
py -3.12 -m unittest discover -s apps/archflow-studio/backend/tests -t apps/archflow-studio -v
py -3.12 -m unittest discover -s tests -v
cd apps\archflow-studio; npm run typecheck; npm run build
```

Expected: `ARCHITECTURE PASS`, all suites `OK`, build green.

- [ ] **Step 3: Commit**

```powershell
git add apps/archflow-studio/README.md
git commit -m "P108 round 1 read slice documented; full battery green

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

1. **Round-1 coverage:** task 1 (server binds configured project) → Task 1; task 2 (HEAD/workflow/
   projection/tree/dependencies) → Tasks 1–2 (workflow surfaces as the record's stage refs — the villa's are
   null and display honestly; full `ProjectStageWorkflow` rendering joins Plan B where run workflow records
   are read); task 3 (artifacts ref/SHA/bytes/binding) → Task 3; task 4 (pick → unique component) → Tasks 4+6;
   tasks 5–7 (intent/proposal/impact/validation/stopping line) → **Plan B by design**; task 8 tests → per-task
   (stale-base and cross-project-refusal tests belong to Plan B's proposal endpoint, where a base and a
   project claim first appear in a request).
2. **Placeholder scan:** Tasks 3–5 steps 3 are compressed but name exact methods, shapes, guards and error
   codes; UI steps carry exact prop/route names. No TBDs.
3. **Type consistency:** `StudioLookupError(code, detail)` used identically in Tasks 2–4;
   `resolve_pick` returns the dataclass consumed by the Task 4 route and Task 6 gateway; camelCase keys match
   between `to_dict`s and TS interfaces; `openFile(file, sourceLabel?)` matches the Task 6 App wiring.
