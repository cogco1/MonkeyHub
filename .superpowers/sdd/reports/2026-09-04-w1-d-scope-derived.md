# W1-D — Scope is a step; a derived control points at its source (E3 + E4)

## Status

DONE_WITH_CONCERNS

Both seams are built, tested and on the wire. Two deliberate narrowings of the
brief's literal rule (§ Concerns 1 and 2) were needed to keep every committed
test green, and one part of E4 (a *base* elevation derived from another
element's top) is not implemented because no element capability stands for it
today (§ Left out and why).

## Commits

- `81ad301` — Scope is a step, and a derived control names its source
- the branch tip — this report, committed alone

Base: the worktree started at `6a4a31a`, ten commits behind `main` and without
`b465d0e` (the commit whose `resolve(..., camera, compass, aliases, catalog)`
the task context names). The branch carried no commits of its own, so it was
fast-forwarded to `main` (`bc605aa`) before any work. Nothing was merged,
rebased or pushed after that.

## Reason codes added

Both in the existing enum block of
`apps/archflow-studio/api/archflow_studio_api/application/clarification.py`;
the four outcomes are untouched.

| code | outcome it arrives on | what it says |
| --- | --- | --- |
| `SCOPE_UNRESOLVED` | `NEEDS_CLARIFICATION` | the target is one element and the record reads the change as possibly reaching further; `missingSlots: ["scope"]`, options as candidates with refs `scope:element` / `scope:stack` / `scope:datum` |
| `CONTROL_IS_DERIVED` | `NEEDS_CLARIFICATION` *or* `MISSING_EDITABLE_CONTROL` | the number named is pinned by a reference; the answer names the source and offers the control that moves it, or is terminal when the source is a level |

Also added: `SLOT_SCOPE = "scope"` (a fifth slot) and the closed set
`SCOPES = ("element", "stack", "datum")`.

## DTO fields added

`transport/intent.py`

- `IntentRequestDto.scope: Literal["element","stack","datum"] | None = None`
- `ScopeOptionDto` (new): `scope`, `elementIds`, `label`
- `PendingIntentDto.scope_options` → `scopeOptions: ScopeOptionDto[]`, default `[]`
- `scope_option_dto(ScopeOption) -> ScopeOptionDto`

`transport/proposal.py`

- `ProposalScopeDto` (new): `scope`, `elementIds`
- `ProposalDto.scope: ProposalScopeDto | None = None`
- `to_dto(proposal, *, scope=None)` — the extra argument is keyword-only with a
  default, so `POST /api/proposals` is unchanged and answers `scope: null`.

Application values (not DTOs): `ScopeOption` (frozen dataclass:
`scope`, `element_ids`, `label`, `.ref` = `scope:<name>`),
`PendingIntent.scope_options`, `CandidateOption.ref_override` (so a scope
choice writes `scope:stack` and is never compared against an element ref), and
`clarification.scope_of(pending) -> (scope, element_ids) | None`.

Catalog: `Capability.derived_from` (property — the one reader of the `source`
string), the constants `DERIVED_FROM = "derived from "` and
`PINNED_BY = {"height": "top"}`, and `references_of(record)`.

## How each answer is derived (no string patterns)

Everything comes off `StateRecord.dependency_edges()` — the kernel's own
reading of `references` (a `base`/`top` naming a level, another element's
`-top` datum, a host or a grid role) *and* of declared relations (subject
upstream of object). The studio never parses `-top` or any other spelling.

- **stack** — B seats on A when an edge with `relation in ("base", "support")`
  runs A → B; the chain is then intersected with
  `StateRecord.closure(("entity:A",))`, so a member of the stack is one a
  change at A actually invalidates.
- **datum** — the `base` edge's `upstream_ref` (`entity:level-ground`,
  `entity:portico-columns-west`); two elements share a datum when that ref is
  the same string. Restricted to elements the catalog calls editable.
- **derived** — `catalog.py` marks a `height` capability `status: "derived"`
  with `source: "derived from <upstream ref>"` when the element declares a
  `top` reference. `clarification` reads `capability.derived_from`, then looks
  the id up in the record: `Element@1` → its editable capabilities are the
  candidates; `Level@1` → nothing to offer, terminal.

## Tests

New file `apps/archflow-studio/api/tests/test_clarification_scope.py`
(10 tests, synthetic record — the villa's *shape* with the stack put back in;
no `D:\PROJECTS` data). It builds its own record and reuses `make_project`,
`RECORD_PAYLOAD`, `run_records`, `write_runner_record` from `tests/support.py`
and `component` / `CAMERA` / `PROJECT_MD` / the object fixtures from
`tests/test_clarification_catalog.py`.

Covered: the three-reading question; `scope: "element"`; the reply `只这个`;
the reply `整个叠层` (coverage widens, the proposal still moves one scalar —
`set height to 9.898` on `portico-columns-west`); the reply `整条标高`
(and that `标高` is not misread as the `elevation` property); a leaf with no
stack and its own datum, asked nothing; a reply that settles nothing
terminating as `CLARIFICATION_MADE_NO_PROGRESS`; the two derived branches with
a compiler in the agent seam that fails the test if it is ever reached; and the
catalog's own `status` / `source` for a derived and an authored capability.

```
cd apps\archflow-studio\api
set PYTHONPATH=<worktree>
py -3.12 -m unittest tests.test_clarification_scope
Ran 10 tests in 3.9s — OK

py -3.12 -m unittest discover -s tests -t .
Ran 380 tests in 109.408s — OK (skipped=2)
```

(370 before this task; the 10 new ones are the difference. The baseline was run
green on the same worktree before any behaviour change, so the 380 is not
hiding a pre-existing failure.)

```
cd apps\archflow-studio\web
npm run -s api:generate   → 4 files regenerated
npm run -s api:check      → api:check — 16 generated files match the current schema.
npm run -s typecheck      → exit 0, no output
```

`py -3.12 tools/archcheck.py` → `ARCHITECTURE PASS (190 files, 1.651s)`.

Note: `apps/archflow-studio/web/node_modules` and
`apps/archflow-studio/web/tools/openapi-ts/node_modules` were absent in a fresh
worktree; `npm ci` in both was needed before `api:generate` and `typecheck`
would run. Neither directory is committed.

## Registrations needed

`governance/module_registry.json` was **not** edited. The controller should
apply:

**`studio.intent`** — add to `owns`:

- `"the scope step: which elements a change is agreed to reach - the element, the stack that seats on it (support relations and base references, intersected with the record's closure), or everything on its datum - asked as SCOPE_UNRESOLVED and carried on the proposal as a coverage, never as a multi-element mutation"`
- `"the derived-control answer: a capability a reference pins is named with its source (CONTROL_IS_DERIVED) and never compiled - the source's own editable controls when it is an element, the terminal MISSING_EDITABLE_CONTROL naming the level when it is one"`

add to `public_api`:

- `"ScopeOption"`
- `"scope_of"`
- `"scope_in"`

add to `tests` and to `used_by`:

- `"apps/archflow-studio/api/tests/test_clarification_scope.py"`

add to `invariants`:

- `"a scope is a coverage, not a mutation: whatever scope is settled, the proposal moves one scalar on one element"`
- `"a derived capability is shown with its source and never compiled; the agent is not asked about one"`

**`studio.binding`** (owns `application/catalog.py`) — amend the catalog `owns`
line to say the status is not always `editable`, e.g. append to the existing
component-catalog line:

- `"; a capability a reference pins (a height whose top names a level, another element's datum or a grid role) is status=derived with source='derived from <ref>', read from the record's own dependency edges"`

add to `public_api`:

- `"references_of"`

No new record kind and no change to `archflow/project/record_kinds.py`.

## Concerns

1. **The scope question is gated on a stack existing.** The brief says "when
   there are ≥ 2 options with different coverage and the request said nothing".
   Implemented literally, that fires on a *datum* option alone, and it broke
   three committed tests (`test_clarification.TheGrammarStillWorks.
   test_increase_by_a_percentage`, and both
   `test_clarification_catalog.CameraAndCompassTests` cases) because in those
   fixtures every element sits on `level-ground`. The rule I implemented, and
   believe is the right one: **the question is the stack.** Something seats on
   the element and the record's own closure carries the change into it, so
   "how far" has two honest answers. A datum is a grouping and not a
   propagation — raising the west columns invalidates nothing on the east ones
   — so a datum option travels in `scopeOptions` as a filter the architect may
   apply and never stops a change by itself. All three options are still
   offered and answerable whenever the question is asked.

2. **An explicit pick is not asked about.** When the target element came from
   `elementId` in the request or from a gesture (`_Target.how in ("picked",
   "gesture")`), no scope question is asked. Without this, ten tests in
   `test_intents.py` and `test_clarification.py` that pass
   `elementId="portico-base"` would start being asked how far (the demo
   fixture's `portico-cornice` seats on `portico-base` through
   `rel-cornice-on-base`). The justification: the architect pointed at one
   element, which is a scope they already gave; the step is for the *words*,
   where "the columns" names a kind and a kind does not say whether what sits
   on them comes too. It is a narrowing of the brief's opening sentence ("a
   selection is not the intended scope") and worth a second opinion — a client
   can still send `scope` explicitly on a picked request and it is honoured.

3. **The route now recovers the change from `originalUtterance` on a reply.**
   A reply like `整个叠层` restates no number, so `grammar_sentence_for` found
   nothing and the exchange fell through to the agent and answered
   `VALUE_UNRESOLVED`. `routes/intents.py` now falls back to the pending
   intent's `originalUtterance` when the reply itself yields no grammar
   sentence and a pending exists — which is what `originalUtterance` is for.
   The reading is the same deterministic one (a number, a unit and a direction
   word, or nothing). The full suite is green with it, but it changes the reply
   turn for every clarification chain, not only the scope one.

4. **`ProposalDto.scope` is emitted on every compiled intent**, as
   `{"scope": "element", "elementIds": [<the one element>]}` when nothing wider
   was settled. `POST /api/proposals` answers `null`. This is additive on the
   wire; no existing client field changed.

## Left out and why

- **A derived *base* / elevation.** The brief's E4 names "a beam's base from
  the capitals' top" as a derived control. There is no `elevation` (or `base`)
  numeric param on `Element@1` in this record, so there is no capability for
  the catalog to mark derived and nothing for `resolve` to trigger on — the
  brief's own rule is "when the target element's capability for the requested
  property has `status == "derived"`". I implemented the one pin the record can
  actually express today: `height` pinned by a `top` reference. Both test
  branches are driven by what that `top` names — a `Level@1` (terminal, "move
  level `<id>`") and another element's published top (candidates = that
  element's editable controls) — and the fixture's derived window literally has
  `base: {"datum": "portico-floor-east-top"}` as the brief describes. Making a
  base elevation editable would mean synthesising a capability with a value
  nothing in the record states, which rule 9 forbids.

- **Multi-element mutation.** Out of scope by the brief, and said so in
  `resolve`'s docstring, in `ScopeOption`'s and `ProposalScopeDto`'s
  docstrings, in `docs/PROTOCOL.md` §5.1, and asserted by
  `test_the_whole_stack_widens_the_coverage_and_still_moves_one_scalar`.

- **No web client work.** W1-D touches the API only; `scopeOptions` and
  `proposal.scope` are on the generated client for whoever renders them.
