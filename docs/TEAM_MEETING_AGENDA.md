# ArchFlow / Monkey Team Meeting Agenda

[Download the eight-slide meeting deck](TEAM_MEETING_DECK.pptx) — Chinese slides with editable diagrams, a four-track allocation table and speaker notes.

> Meeting purpose: briefly align everyone on the current system, then divide the next two weeks into **four active tracks only: Paper / Blender / Interaction / Backend**. Everything else is mentioned only as later development, not assigned tomorrow.

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

## 4–8 min — Where the project is now

Very short update:

- product prototype already exists;
- MonkeyHub connects the main surfaces;
- manual modeling is increasingly local/immediate instead of one candidate per gesture;
- Board can carry visual review + selected text back into design feedback;
- OCCT / Rhino / Blender sit behind a common CAD backend contract;
- Hub has the first runtime/recovery slice;
- MonkeyMonitor already records timing/usage sources but still needs a better observability UI;
- Windows desktop host Phase A has merged in PR #33; full distribution and clean-machine acceptance remain later work.

The project has moved from **“can we build the pieces?”** to **“how do we turn them into a coherent product and research result?”**

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

Spend about three minutes on each track. For every track agree on:

- owner;
- first PR / first artifact;
- exact boundary with the other tracks;
- what counts as a useful result in two weeks.

## 24–30 min — GitHub workflow + assignment confirmation

> **先看谁在改什么，再开自己的 lane；共享接口先合，业务实现后分；main 是共同事实。**

Each owner states the first concrete deliverable before the meeting ends.

---

# 2. Track A — Paper / Research

## Goal

Turn the existing prototype into a **clear research claim + benchmark**, rather than writing a product feature list as a paper.

Central question:

> **Can a general-purpose agent stop acting as a CAD operator / workflow engine and instead compile design intent into a typed transaction executed by a dependency-aware design runtime?**

Research mechanism:

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

1. Freeze the terminology and formal diagram.
2. Define a narrow `ContextPack` / `DesignTransaction` research abstraction.
3. Build a fixed benchmark set from real tasks.
4. Compare:

```text
A. direct/raw CAD agent
B. current ArchFlow multi-round agent loop
C. compiled-context + transaction-runtime path
```

5. Measure model rounds, state/schema/context reads, tool calls, tokens, first-visible latency, verified-candidate latency, ambiguity/failure, and repeated-edit drift.

## Two-week deliverable

- one paper-ready architecture figure;
- one benchmark protocol;
- one first results table;
- one falsifiable written claim.

The paper track may use #32 / MonkeyMonitor data as evidence, but it does not own all product implementation.

---

# 3. Track B — Blender Projection

## Goal

Do **not** make Blender another MonkeyArch or another source of project truth.

Make Blender a **rich scene projection / execution target** of the same ArchFlow state.

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

The target is a vertical slice, not generic “continue adapting Blender”.

### Required demo

```text
ArchFlow project
    ↓ one explicit projection action
native .blend
    ↓
semantic collections + materials preserved
camera + basic lighting created
    ↓
render one PNG
    ↓
change Canonical State
    ↓
rebuild / update Blender projection
    ↓
render changed result
```

### Priorities

1. preserve ArchFlow semantic/object identity;
2. usable material mapping;
3. semantic collection / scene organization;
4. camera + lighting defaults;
5. one reliable render path first;
6. unsupported geometry fails explicitly instead of silently approximating.

### Why Blender matters

This one boundary can later support rendering, assets/vegetation/furniture, Geometry Nodes, animation/phasing and presentation output.

> **Blender may be a powerful executor, but it is not Canonical State.**

Use the existing #13 backend contract rather than inventing a Blender-specific parallel architecture.

---

# 4. Track C — Interaction

## Goal

Make MonkeyArch feel like a **real direct modeling environment**, not a collection of correct commands hidden behind panels.

Core rule from #14:

> **While the hand is moving, state is transient interaction state. When the gesture settles, it becomes one typed design action.**

Already delivered:

- local preview reuse + RAF;
- local hover / preselection;
- immediate click feedback;
- pointer-first Push/Pull;
- local draft + manual Sync;
- basic line / curve / arc drawing;
- delete / undo / redo without per-gesture candidate generation.

## Two-week work

Do not add twenty new commands. Finish the hand-feel of existing commands.

Recommended order:

1. Move + Copy pointer-first;
2. Rotate;
3. Scale / mirror;
4. snap hysteresis and only the inference modes current tools need;
5. large-scene snap indexing only where measurement proves it necessary.

Desired pattern:

```text
select / hover
    ↓
activate tool
    ↓
pointer manipulation + local preview
    ↓
typed value optionally overrides pointer
    ↓
click / Enter
    ↓
one typed action / one settled local-draft operation
```

No model/provider call while the pointer moves.

## Two-week deliverable

A user should be able to create and reshape a simple housing / massing model for several minutes without feeling that each action is waiting for ArchFlow.

Measure separately:

- pointer → preview;
- gesture settle → local acknowledgement;
- Sync → verified candidate.

---

# 5. Track D — Backend / Runtime

## Goal

Make the existing product **faster, observable, recoverable and simple for a general-purpose agent to operate**.

The main current problem is the remaining 30–150 second agent path. CAD execution is often not the dominant cost; repeated model wake-ups, broad context exploration and serial tool orchestration are.

## First priority — #32 trace + MonkeyMonitor

Before further optimization, make one design turn observable end-to-end.

First backend PR:

```text
TurnTrace@1
Hub → provider/agent → Studio → candidate → CAD → verify → preview
```

MonkeyMonitor should show automatically:

- total elapsed;
- first visible result;
- verified candidate time;
- model rounds;
- token usage;
- qualified cost / API-equivalent estimate where appropriate;
- horizontal waterfall / Gantt timeline;
- semantic activity tree;
- critical path;
- repeated reads, retries and avoidable model wake-ups.

Actual time/token/rate facts come from execution/task configuration. The user should not manually fill them after the run.

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

The agent wakes again only for genuine ambiguity / design judgment. The default agent remains replaceable; no special architecture model is required.

## Two-week deliverable

One design task should produce a trace that clearly explains where the major seconds went, and one simple benchmark should demonstrate fewer agent rounds / less context exploration than the current path.

PR #33 and the existing #12/#21 runtime/desktop work stay as maintenance/infrastructure context; do not let them swallow this two-week backend objective unless they directly block it.

---

# 6. Four-track boundary

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

- **Paper** does not own product UI implementation or change benchmark rules after seeing results.
- **Blender** does not create another Canonical State or own MonkeyArch interaction.
- **Interaction** does not route pointer motion through Agent/backend round trips.
- **Backend** does not redesign the architecture UI while fixing latency or turn Monitor telemetry into design state.

---

# 7. GitHub collaboration rules

1. **One task = one lane / branch / worktree / PR.**
2. Run `python tools/devctl.py work` before starting and inspect the target module / contract.
3. `main` is integration truth.
4. If two tracks need the same public contract, make a small upstream contract PR first.
5. Do not absorb another person's unmerged WIP.
6. Keep PRs independently reviewable.
7. Coding agents follow the same lane rules as humans.

Before leaving the meeting, every active track states:

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

# 8. Later development — one slide note only

Other open directions — Board collaboration/UX, desktop packaging, additional App Server recovery, warm-runtime refinements, Diagram/Fab expansion, richer ingestion and analysis integrations — remain valid **future development plans**, but are not tomorrow's work allocation. They can be picked up gradually after the four current tracks establish a stronger product and research core.

---

# 9. PPT generation guidance

Codex should make the meeting deck from this document. Prefer **8 concise slides**, diagram-heavy and not text-heavy:

1. **Where we are now** — prototype exists; the question is how to make it coherent and provable.
2. **Core architecture** — Human/Agent → ArchFlow State → Runtime → downstream tools.
3. **Four active tracks** — Paper / Blender / Interaction / Backend.
4. **Paper** — research question, mechanism, three-way benchmark.
5. **Blender** — rich scene projection; render-ready vertical slice.
6. **Interaction** — hand-fast, pointer-first direct modeling.
7. **Backend** — TurnTrace/Monitor → ContextPack/DesignTransaction → fewer agent rounds.
8. **How we work** — GitHub lane rules; future development gets one small footer only.

Do not turn the deck into a list of all open Issues. The four tracks are the meeting structure; everything else is a short future-roadmap mention.
