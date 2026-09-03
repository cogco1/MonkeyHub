# Container plan — the authored record, the stage ladder, the reserved containers, the leftovers

2026-09-03. Kaiwen's rulings on the memo of the same day: (1) fix the authored record as proposed;
(2) align the stages with an industry ladder while keeping ours finer; (3) name the four containers,
reserve interfaces for the shared and archived ones so parallel development has somewhere to land,
and register them; (4) fix the model-call and vocabulary leftovers. Six steps from the memo, regrouped
into three waves so Opus workers can run in parallel worktrees without touching the same files.

## The stage ladder (ruling 2)

Two axes, both industry vocabulary. The coarse axis is the **phase**: RIBA Plan of Work 2020 on the
left, the AIA phases and the Chinese design stages (方案设计 / 初步设计[扩初] / 施工图设计, per 《建筑
工程设计文件编制深度规定》) beside it. The fine axis is the BIMForum **Level of Development**, which is
the industry's own answer to "our stages are finer than SD/DD": LOD says how resolved the model is,
per element, inside a phase.

| `DesignPhase` (serialised literal, unchanged) | RIBA 2020 | AIA | 中国 | LOD range |
|---|---|---|---|---|
| `research_brief` | 0 Strategic Definition | pre-design | 前期调研 / 项目建议书 | — |
| `programming` | 1 Preparation and Briefing | programming | 任务书 / 策划 | — |
| `site_resource_coordination` | 1 Preparation and Briefing (site information) | pre-design | 场地 / 资源条件 | — |
| `schematic_design` | 2 Concept Design | Schematic Design | 方案设计 | 100–200 |
| `design_development` | 3 Spatial Coordination | Design Development | 初步设计(扩初) | 200–300 |
| `candidate_coordination` | 3 Spatial Coordination (coordination of alternatives) | DD coordination | 扩初深化 / 专业配合 | 300–350 |
| `execution_ready` | 4 Technical Design | Construction Documents | 施工图设计 | 350–400 |

LOD 500 (field-verified as-built) is the reconstruction case: a monument's as-built record is LOD 500
evidence, from which our stages work *down* to 100 and back up.

A `ProjectStage` gains an optional `lod` (one of 100, 200, 300, 350, 400, 500); a workflow requires
`lod` non-decreasing like `phase`, and inside the phase's range. The literals stay; `to_dict` emits
`lod` only when set, so every retained workflow round-trips byte-identically (ADR-004). The villa's six
stages read: typology-components 100, topology-relations 200, coordinated-geometry 300,
whole-building-detail 350, material-identity 400.

## The containers (ruling 3)

`archflow/project/containers.py` owns the four states of ADR-007 as code:

- `ContainerState`: `WORK_IN_PROGRESS` (S0), `SHARED` (S1 coordination, S4 suitable for stage approval),
  `PUBLISHED` (A), `ARCHIVED`.
- `Container(state, status_code, project_id, run_id | None, ref | path, author | None)`.
- `work_in_progress(repository, author=None)`: today the one runner slot (`input/runner/`); a second
  author lands at `input/<author>/` later without a layout change elsewhere. This is the reserved seam
  for parallel development: many WIP, one published.
- `shared(repository, *, branch_id=None)`: every run with its status code (S4 when a satisfied
  closure is retained, else S1) and the branch it ran on.
- `published(repository)`: HEAD as a container with its issue number.
- `archived(repository)`: superseded canonical snapshots, oldest first.

No new persistence, no pointer beside HEAD. The functions read the layout the repository already
owns; a later parallel-development step adds authors and branches without a second store.

## Waves

Each worker: its own worktree, explicit `git add`, no push, tests green before commit, archcheck
green, registry updated (`governance/module_registry.json`) and `devctl render-map` run. Fable
reviews and merges; nothing merges on a red suite.

### Wave A (parallel; disjoint files)

**W1 — the authored record** (memo step 1). `archflow/project/inputs.py` owns the WIP readers
(`load_authored_record`, `load_seat_pack_file`) at `ProjectLayout.authored_record` /
`ProjectLayout.seat_pack`; `tools/run_project.py` and `tools/verify_state_record.py` lose `--packs`;
the Studio's projection, candidate and seats adapter import the one reader. `StageBinding` leaves
`StateRecord@1` with its DTO and honesty line; `from_dict` refuses a `stage` key (ADR-007).
Files: `archflow/project/{layout,inputs}.py`, `archflow/state/state_record.py`, the two tools,
`apps/…/application/{projection,candidate}.py`, `apps/…/adapters/seats.py`,
`apps/…/transport/state.py`, the web client if it renders `stageBinding`, tests, registry.

**W2 — one model contract** (memo step 6). `production/provider_runtime.py`,
`production/responsibility.py`, `adapters/model_provider.py` and their tests move to `archive/`;
`ports/model.py` is the one contract. The intent compilers (codex, Anthropic) build a
`ModelInvocationRequest` and return a `ModelInvocationReceipt` on the `Compilation`; the receipt rides
on the `Proposal` so Wave B can retain it in the candidate run; the intent env vars move into
`StudioSettings`. Files: `archflow/production/`, `archflow/adapters/model_provider.py`,
`archflow/ports/model.py` (one new phase), `apps/…/application/{intent_agent,proposals}.py`,
`apps/…/routes/intents.py`, `apps/…/settings.py`, `launch-studio.ps1`, tests, registry.

**W3 — checkers and roles** (memo step 5, second half). The accepted `check_kind` set is the checker
table: `support_contact`, `clearance_interval` (new), `aperture_exists` (new); the four never-measured
kinds go. `ReferenceContext.axis` matches a role exactly, no `axis_id` fallback. Files:
`archflow/capabilities/{relation_checks,reference_resolver}.py`, `archflow/state/state_record.py`
(`ValidatorBinding` only), tests, registry.

### Wave B (after A merges)

**W4 — the runner tells the truth about its provider** (memo step 2). `RecordedProposalProvider`
declares `runner-recorded-proposal`; the seat pack's `provider_identity` applies only when a live
provider is invoked; the candidate run retains the intent receipt as `intent-compilation`. Villa
re-run as `runner-003`.

**W5 — record kinds and one URI parser** (memo step 5, first half). `archflow/project/record_kinds.py`
(kind → schema) checked by `put_json`; readers import the constants; `record_ref_from_uri` is the one
parser. After W4 (same files).

**W6 — containers and the ladder** (rulings 2 and 3). `archflow/project/containers.py`;
`ProjectStage.lod` and the phase table in `stage_workflow.py`; `docs/SYSTEM_MAP.md` gains the ladder.

### Wave C (after B)

**W7 — the runner closes a stage** (memo step 3): closure and exit binding from the relation checks
when every required check held; `freeze_project_stage_workflow` refuses checks outside the checker
table; villa workflow v2 in `input/`, v1's file retired from `inputs/`.

**W8 — issue** (memo step 4): `tools/issue_project.py` from a satisfied closure; the word HEAD leaves
the tools' and the Studio's vocabulary.

## Out of scope for the workers

The villa WIP file (`input/runner/state-record.json`) is Kaiwen's; when W1 lands, Fable strips its
dead `stage` key with a dated backup, and when W3 lands, Fable declares validators on its 14
relations the same way. Workers never write into `D:\PROJECTS`.
