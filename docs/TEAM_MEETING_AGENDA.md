# ArchFlow / Monkey Team Meeting Agenda

> Meeting purpose: briefly align everyone on the current system, then divide the next two weeks into **four active tracks only: Paper / Blender / Interaction / Backend**. Everything else is a later development plan, not tomorrow's assignment list.

## 0. One-sentence framing

**Generation is replaceable; design state is not.**

ArchFlow is the shared design-state / dependency / runtime layer. MonkeyArch, MonkeyBoard, MonkeyDiagram, MonkeyFab and MonkeyMonitor are product surfaces over that state and runtime. Humans, general-purpose agents and different CAD backends may all generate or edit; the system keeps identity, dependencies, provenance, revisions and recovery coherent.

---

# 1. Meeting flow — 25 to 30 minutes

## 0–4 min — Introductions

Each person says only:

- name;
- background / current focus;
- which of the four tracks they are interested in.

No long personal introductions.

## 4–8 min — Where the project is now

Very short update only:

- MonkeyHub already connects the main product surfaces;
- manual modeling is now largely local/immediate rather than one candidate per gesture;
- Board can carry visual review + selected text back into design feedback;
- OCCT / Rhino / Blender already sit behind a common CAD backend contract;
- Hub has the first project-runtime/recovery slice;
- MonkeyMonitor already records usage/timing sources, but its observability UX still needs redesign;
- a Windows desktop host exists in draft PR #33.

The meeting is not a Git-history presentation. The important point is that the project has moved from **"can we build the pieces?"** to **"how do we turn them into a coherent system and research result?"**

## 8–11 min — Shared architecture

```text
Human / General Agent / Generator
            ↓
      Intent / Typed Action
            ↓
          ArchFlow
 identity / dependencies / provenance / revisions
            ↓
        Runtime Layer
 operations / workers / recovery / projections
            ↓
 OCCT / Rhino / Blender / Diagram / Fab / Viewer
```

Product rule:

> Tools may change; project state should not reset every time the tool changes.

Research rule:

> The general-purpose agent should make the design judgment once; deterministic software should execute the rest whenever possible.

## 11–24 min — Four active tracks

Spend roughly three minutes on each track. For each one agree on:

- owner;
- first PR / first artifact;
- exact boundary with the other tracks;
- what counts as a useful result in two weeks.

## 24–30 min — GitHub workflow + assignment confirmation

Memorize this sentence:

> **先看谁在改什么，再开自己的 lane；共享接口先合，业务实现后分；main 是共同事实。**

Then each owner states their first concrete deliverable before the meeting ends.

---

# 2. Track A — Paper / Research

## Goal

Turn the existing prototype into a **clear research claim + benchmark**, rather than writing a product feature list as a paper.

The central question for the next paper iteration is:

> **Can a general-purpose agent stop acting as a CAD operator / workflow engine and instead compile design intent into a typed transaction executed by a dependency-aware design runtime?**

The research mechanism should connect the existing ideas:

```text
Canonical State
      +
Dependency / provenance structure
      ↓
Compiled Context
      ↓
General-purpose Agent
      ↓
Declarative Design Transaction
      ↓
Deterministic Runtime
      ↓
Verified Candidate State
```

## Two-week work

1. Freeze the core terminology and formal diagram.
2. Define a narrow `ContextPack` / `DesignTransaction` research abstraction without trying to model every possible design action.
3. Build a fixed benchmark set from real tasks.
4. Compare three conditions:

```text
A. direct/raw CAD agent
B. current ArchFlow multi-round agent loop
C. compiled-context + transaction-runtime path
```

5. Measure at least:

- model rounds;
- schema/state/context reads;
- tool calls;
- input/output/cached tokens when available;
- time to first visible result;
- time to verified candidate;
- semantic failure / ambiguity rate;
- drift across repeated edits.

## Useful deliverable in two weeks

- one paper-ready architecture figure;
- one benchmark protocol;
- one first results table;
- one written claim that can be falsified by the experiment.

The paper track may use #32 instrumentation/Monitor results as evidence, but it should not own every product implementation detail.

---

# 3. Track B — Blender Projection (Panny)

## Goal

Do **not** make Blender another MonkeyArch or another source of project truth.

Make Blender a **rich scene projection / execution target** of the same ArchFlow state.

Current backend work already proves that Blender can receive supported compiled geometry, save native `.blend`, cold-read it again and verify ArchFlow identities / units / materials / exact binding. The next question is: **what is Blender for in the product?**

Answer:

```text
Canonical ArchFlow State
          ↓
     Blender Projection
          ↓
 geometry + materials + assets
 camera + lighting + environment
 procedural scene work
 render / animation / presentation
```

## Two-week work

Panny's target should be a vertical slice, not generic "continue adapting Blender".

### Required demo

```text
ArchFlow project
    ↓ one command / one explicit projection action
native .blend
    ↓
materials / semantic collections preserved
camera + basic lighting created
    ↓
render one PNG
    ↓
change Canonical State
    ↓
rebuild / update Blender projection
    ↓
render the changed result
```

### Priorities

1. preserve semantic/object identity from ArchFlow;
2. map ArchFlow materials into usable Blender materials;
3. establish collection / scene organization that follows semantic roles, not arbitrary object names;
4. camera + basic lighting defaults;
5. one reliable render path (Eevee or Cycles — choose one first);
6. document which geometry remains unsupported instead of silently approximating it.

### Later uses enabled by this boundary

- architectural rendering;
- furniture / vegetation / entourage assets;
- Geometry Nodes as a specialized execution backend;
- animation / phasing / exploded views;
- presentation output.

## Hard boundary

Blender object IDs / names are projection identity only.

> **Blender may be a powerful executor, but it is not Canonical State.**

Use #13's existing backend contract rather than inventing a Blender-specific parallel architecture.

---

# 4. Track C — Interaction

## Goal

Make MonkeyArch feel like a **real direct modeling environment**, not a collection of correct commands hidden behind panels.

Core rule from #14:

> **While the hand is moving, state is transient interaction state. When the gesture settles, it becomes one typed design action.**

A lot is already delivered:

- local preview reuse + RAF;
- local hover / preselection;
- immediate click feedback;
- pointer-first Push/Pull;
- local draft + manual Sync;
- basic line / curve / arc drawing;
- delete / undo / redo without per-gesture candidate generation.

## Two-week work

Do not add twenty new commands. Finish the hand-feel of the commands that already exist.

Recommended order:

1. **Move + Copy** pointer-first;
2. Rotate;
3. Scale / mirror;
4. snap hysteresis and only the inference modes the current tool actually needs;
5. large-scene snap indexing only where measurements show it is necessary.

Desired interaction pattern:

```text
select / hover
    ↓
activate tool
    ↓
pointer manipulation + local preview
    ↓
typed number optionally overrides pointer
    ↓
click / Enter
    ↓
one typed action / one settled local draft operation
```

No model/provider call while the pointer moves.

## Useful deliverable in two weeks

A user should be able to create and reshape a simple housing / massing model for several minutes without feeling that each action is waiting for ArchFlow.

Measure separately:

- pointer → preview;
- gesture settle → local acknowledgement;
- Sync → verified candidate.

Do not mix these into one latency number.

---

# 5. Track D — Backend / Runtime

## Goal

Make the existing product **faster, observable, recoverable and simple for a general-purpose agent to operate**.

This track owns the infrastructure underneath the other three tracks, but should still work in small slices.

The most important current problem is the remaining 30–150 second agent path. CAD execution is often not the dominant cost; repeated agent wake-ups, broad context exploration and serial tool orchestration are.

## First priority — #32 trace + MonkeyMonitor

Before further optimization, make one design turn observable end-to-end.

First backend PR should deliver:

```text
TurnTrace@1
Hub → provider/agent → Studio → candidate → CAD → verify → preview
```

MonkeyMonitor should then show:

- total elapsed;
- first visible result;
- verified candidate time;
- model rounds;
- token usage;
- qualified cost / API-equivalent estimate when appropriate;
- a horizontal waterfall / Gantt timeline;
- semantic activity tree;
- critical path;
- repeated state/schema reads, retries and avoidable model wake-ups.

Actual time/token/rate facts should come from task configuration and execution records. Users should not manually type the measurement for a run.

## Second priority — collapse the agent loop

Once the trace proves where time goes:

```text
compiled ContextPack
        ↓
one general-agent decision
        ↓
DesignTransaction
        ↓
deterministic execution DAG
        ↓
candidate
```

The agent wakes again only for genuine ambiguity / design judgment.

Do not train a special architecture model; the default agent remains replaceable.

## Runtime / desktop work

PR #33 is the current open desktop Phase A implementation. Review/merge it separately; do not turn the backend lane into a simultaneous rewrite of desktop, App Server and Agent runtime.

After that, #12 / #21 work should proceed only when required by measured runtime/recovery gaps:

- restart/reconnect without replay;
- explicit operation status;
- provider/CAD worker supervision where needed;
- clean desktop lifecycle / standalone packaging.

## Useful deliverable in two weeks

A single design task should produce a trace that clearly explains where every major second went, and one simple benchmark should demonstrate fewer agent rounds / less context exploration than the current path.

---

# 6. Four-track boundary

The four tracks should meet at stable contracts rather than edit each other's internals.

```text
Paper
  observes / formalizes / benchmarks
             │
             ▼
Backend ── Context / Transaction / Runtime ──► Blender
   │                                         projection
   ▼
Interaction
 typed actions / local draft
```

### Paper does not

- own product UI implementation;
- change benchmark rules after seeing results.

### Blender does not

- create another Canonical State;
- own MonkeyArch interaction.

### Interaction does not

- route pointer motion through Agent/Backend round trips;
- implement runtime orchestration.

### Backend does not

- redesign the architecture UI while fixing latency;
- turn Monitor telemetry into design state.

---

# 7. GitHub collaboration rules

1. **One task = one lane / branch / worktree / PR.**
2. Run `python tools/devctl.py work` before starting and inspect the target module / contract.
3. `main` is the integration truth.
4. If two tracks need the same public contract, make a small upstream contract PR first.
5. Do not absorb another person's unmerged WIP.
6. Keep PRs small enough for another person to review independently.
7. Codex / Claude Code / other agents follow the same lane rules as humans.
8. Shared registries / generated files are coordination surfaces, not invitations for several lanes to edit the same production files casually.

Before leaving the meeting, every active track should state:

```text
Track:
Owner:
Base main SHA:
Issue / lane:
First deliverable:
Expected files / modules:
Tests / benchmark:
Blocked by:
```

---

# 8. Later development plan — mention only, do not assign tomorrow

These remain valid, but are **not part of the four active tracks for tomorrow's division of work**:

- #23 remaining MonkeyBoard new-user UX validation;
- #24 Project Rooms / realtime Board collaboration;
- fuller desktop packaging / clean-machine distribution after PR #33;
- additional App Server recovery coverage beyond measured needs;
- warm provider/state/geometry optimizations from #8 after #32 identifies the actual critical path;
- broader MonkeyDiagram improvements;
- MonkeyFab printer / CNC / laser workflows;
- richer imported-geometry ingestion / staged semantic decomposition;
- broader analysis integrations such as structural / environmental tools.

These can be developed gradually after the four current lines establish a stronger research and product core.

---

# 9. PPT generation guidance

Codex should make the meeting deck from this document. Prefer **8 concise slides**, diagram-heavy and not text-heavy:

1. **Where we are now** — product prototype exists; question is how to make it coherent and provable.
2. **Core architecture** — Human/Agent → ArchFlow State → Runtime → downstream tools.
3. **The four active tracks** — Paper / Blender / Interaction / Backend.
4. **Paper** — research question, mechanism, three-way benchmark.
5. **Blender** — rich scene projection; render-ready vertical slice.
6. **Interaction** — hand-fast, pointer-first direct modeling.
7. **Backend** — TurnTrace/Monitor → ContextPack/DesignTransaction → fewer agent rounds.
8. **How we work** — GitHub lane rules + later-development roadmap in one small footer/side panel.

Do not turn the deck into a list of all open Issues. The four tracks are the meeting structure; the rest are only a future-roadmap note.
