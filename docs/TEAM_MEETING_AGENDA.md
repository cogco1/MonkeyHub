# ArchFlow / Monkey Team Meeting Agenda

> Meeting purpose: align the team on what the project is, separate research goals from product delivery, establish the GitHub collaboration rules, and assign the next implementation lanes without overlapping work.

## 0. One-sentence project framing

**Generation is replaceable; design state is not.**

ArchFlow is the shared design-state / dependency / runtime layer. MonkeyArch, MonkeyBoard, MonkeyDiagram, MonkeyFab and MonkeyMonitor are product surfaces over that state and runtime. Humans, agents and different CAD backends may all generate or edit; the system must keep identity, dependencies, provenance, revisions and recovery coherent.

---

## 1. Meeting flow — 25 to 30 minutes

### 0–4 min — Introductions

Each person says only:

- name;
- current technical/design focus;
- which part of the project they are most interested in touching.

Goal: everyone knows who to ask, not a long personal introduction.

### 4–8 min — Two tracks: paper vs product

**Research / paper track** asks:

- what is the new mechanism;
- why it matters;
- how it differs from terminal generation / one-shot CAD agents;
- how it is evaluated and benchmarked.

Current research mechanism family:

- canonical design state;
- dependency-aware editing;
- compiled context;
- declarative design transactions;
- human / agent / backend convergence at a typed action boundary;
- explicit revision, provenance and recovery.

**Product track** asks:

- is it understandable;
- is it fast;
- is it stable;
- can several people work on it safely;
- can the user finish a real design workflow without knowing the internals.

These tracks share code, but not every product feature must become a paper novelty and not every research idea must immediately become a UI button.

### 8–12 min — System map

Use this simple model:

```text
Human / Agent / Generator
        ↓
     Intent / Action
        ↓
     ArchFlow State
 identity / dependencies / provenance / revisions
        ↓
     Runtime / App Server
 operations / workers / recovery / projections
        ↓
 OCCT / Rhino / Blender / Diagram / Fab / Viewer
```

MonkeyArch is the main architectural-design workspace. MonkeyBoard is review / feedback. MonkeyDiagram is representation. MonkeyFab is fabrication. MonkeyMonitor is observability: what ran, how long it took, what consumed tokens/cost, and where the system wasted work.

### 12–18 min — What is already working

Do not retell the full Git history. Use the delivered milestones only:

- stable CAD backend contract for OCCT / Rhino / Blender;
- multi-contributor lane / worktree tooling;
- Hub project runtime and explicit recovery first slice;
- low-latency local modeling preview, hover/preselection, local push-pull and manual Sync;
- Board UX simplification, selected text → design feedback, contextual source replacement;
- Monitor automatic Hub/Codex usage binding;
- current desktop Windows host implementation is in PR #33.

### 18–24 min — GitHub collaboration rules

Memorize this sentence:

> **先看谁在改什么，再开自己的 lane；共享接口先合，业务实现后分；main 是共同事实。**

Rules:

1. One task = one Issue / lane / branch / worktree / PR.
2. Before coding, run `python tools/devctl.py work` and inspect the target module/contract.
3. Never share one dirty worktree or absorb another person's unmerged WIP.
4. If two lanes need the same public interface, stop and make the interface change a small upstream PR first.
5. Dependent work updates from landed `main`; do not maintain private copies of shared contracts.
6. Keep PRs narrow and independently reviewable.
7. Codex / Claude Code / other coding agents follow the same lane rules as human developers.
8. `main` is integration truth. Local memory, chat history and one person's machine are not project management systems.

### 24–30 min — Assign lanes and define first deliverables

Assign the lanes below. Each owner states the first PR they expect to open, the files/contracts they expect to touch, and any dependency before leaving the meeting.

---

## 2. Current open work snapshot

### PR to review first

| Item | Status | What it means tomorrow |
| --- | --- | --- |
| **PR #33 — Windows desktop host** | Open, draft, mergeable | Review exact-head Windows checks. If clean, merge Phase A first. Do not start another desktop implementation in parallel. |

### Open Issues

| Issue | Current state | Remaining work / decision | Priority |
| --- | --- | --- | --- |
| **#32 Agent latency + MonkeyMonitor** | Major work not implemented yet | First slice = `TurnTrace@1` + automatic timing/token/price context + live visual waterfall/activity tree + deterministic “Why slow?” diagnostics. Then ContextPack and DesignTransaction. | **P0 / primary** |
| **#14 Interaction Engine** | Large part delivered by PRs #19/#20/#22/#28 | Continue pointer-first M/Q/S/Copy, snap/inference hysteresis, large-scene snap indexing only where measured. Also decide whether unsynced local-draft reload recovery belongs here or a separate issue. | **P0 / primary** |
| **#21 Desktop EXE** | Phase A implemented in PR #33 | After #33, Phase B = App Server lifecycle/reconnect/recovery; Phase C = clean-machine standalone packaging. | **P1** |
| **#12 App Server/runtime** | First major slice delivered by PR #16 | Expand ownership/recovery only where #21/#32 reveal real gaps: provider/CAD worker coverage, cold-start reconstruction limits, unified operation visibility. Avoid another parallel server. | **P1, paired with #21** |
| **#13 CAD backend + multi-contributor** | Core contract, lane tooling and Blender backend already delivered by PRs #15/#17/#18 | Remaining acceptance: new teammate independent replay/handoff, real Rhino-host acceptance, verify the team can extend the backend without touching runner dispatch. Close when this rehearsal passes. | **P1 / good onboarding task** |
| **#23 MonkeyBoard zero-training UX** | Most core UX slices delivered by PRs #26/#27/#30 | Run a real no-explanation new-user test; decide whether sticky preset / non-file HTML-remote paste are required before closing the basic UX issue. | **P2** |
| **#24 Project Rooms / Board collaboration** | Architecture defined, implementation not started | Start only after Board basics and runtime boundary are stable. First implementation should be narrow room/share gateway + realtime Board path, not exposing MonkeyHub. | **P3 / later** |
| **#8 Warm runtime latency** | Older latency issue; partially overlaps #32 | Do not assign as an independent lane tomorrow. Use #32 trace first; then fold measured warm-provider/state/geometry work into the owner that actually shows up on the critical path. Consider closing/superseding #8 after that decision. | **HOLD** |

---

## 3. Recommended team allocation

### Lane A — Agent / Monitor owner

**Issue #32**

First PR only:

- inventory existing Hub / Studio / provider / Monitor events;
- define `TurnTrace@1` normalization and stable correlation;
- automatically collect time and actual usage;
- make MonkeyMonitor show one live semantic waterfall + activity tree;
- leave raw Studio rows under advanced details;
- no ContextPack / DesignTransaction semantics in the same first PR.

Success criterion: after one real turn, a person can answer **what was happening, what blocked, and what consumed tokens/cost** without reading transport logs.

### Lane B — Interaction owner

**Issue #14**

Next PR:

- pick one direct-manipulation family, preferably Move + Copy first;
- pointer is primary, numeric entry is exact override;
- zero provider/API writes while pointer is moving;
- reuse current InteractionSession / local draft / typed-action boundary;
- add snap hysteresis only where the chosen tool needs it.

Do not add a large batch of unrelated modeling commands.

### Lane C — Desktop / Runtime owner

**PR #33 → Issues #21 + #12**

Order matters:

1. review/finish PR #33;
2. merge desktop Phase A if exact-head checks are clean;
3. only then open the next Phase B PR around App Server restart/reconnect and clean lifecycle;
4. do not create a shell-owned duplicate runtime.

#21 and #12 should normally be one sequential ownership lane because they overlap in Hub/runtime lifecycle. Do not give them to two people to edit the same core files simultaneously.

### Lane D — CAD backend / new teammate onboarding

**Issue #13 acceptance**

Use this as the first real teammate rehearsal:

- clone from current `main` independently;
- run `devctl work` / module lookup;
- execute the existing Blender backend contract tests on the teammate machine;
- independently trace the one request → backend → artifact → readback path;
- run real Rhino-host acceptance if the machine has Rhino;
- document any missing contract instead of bypassing the registry/factory.

If this works without asking “which private branch/file do I need?”, #13 has achieved its collaboration goal and can be closed.

### Optional Lane E — Board owner

Only if a fifth person is available.

**Issue #23** first, not #24.

Run a real new-user test and fix only observed usability failures. Once #23 is acceptably closed, prepare the smallest Phase 0 slice of #24.

---

## 4. If fewer people are available

### 4 people

Use A / B / C / D above. Leave #23/#24 for later.

### 3 people

1. #32 Monitor / agent trace;
2. #14 Interaction;
3. PR #33 → #21/#12 runtime/desktop.

Keep #13 as an onboarding/verification task rather than a large feature lane.

### 5+ people

Add #23 UX validation as the fifth lane. Do **not** start #24 merely to keep someone busy; collaboration work has a large cross-cutting surface and should start from a stable Board/runtime baseline.

---

## 5. What should *not* happen tomorrow

- Do not start another Blender architecture; Blender is already behind the common backend contract.
- Do not create another App Server beside MonkeyHub.
- Do not route manual pointer interactions through an LLM.
- Do not make MonkeyMonitor a second canonical project store.
- Do not let every person modify `App.tsx`, Hub runtime and shared registries at once.
- Do not try to finish all of #32 in one PR.
- Do not treat Issue count as workload count: several open Issues are mostly acceptance/remaining phases rather than greenfield implementation.

---

## 6. End-of-day deliverable for each lane

Every lane should be able to report:

```text
Issue / lane:
Owner:
Base main SHA:
Target contract/module:
Files expected to change:
First PR goal:
Tests / benchmark:
Blocked by:
Handoff needed from:
```

A good first day is not “everyone wrote lots of code.” It is:

> everyone can work independently, shared interfaces remain stable, and every PR has a clear reason to exist.

---

## 7. PPT generation guidance

Codex may turn this document into the meeting deck. Prefer **8 concise slides**, diagram-heavy and not text-heavy:

1. **What are ArchFlow + Monkey?** — one sentence + “Generation is replaceable; design state is not.”
2. **Two tracks** — Research vs Product, shared code but different success criteria.
3. **System architecture** — Human/Agent → ArchFlow State → Runtime → CAD/Diagram/Fab.
4. **What already works** — backend contract, Hub runtime, interaction, Board, Monitor, desktop PR.
5. **What remains** — open-issue matrix grouped into P0/P1/P2/Hold.
6. **How we work in GitHub** — one task/one lane/one worktree/one PR; main is truth; interface-first.
7. **Tomorrow's lanes** — A Monitor/#32, B Interaction/#14, C Desktop/#21+#12, D Backend acceptance/#13, optional E Board/#23.
8. **End state / next sync** — what each lane should bring back and the common product direction.

Visual preferences:

- use architecture diagrams, swim lanes and issue-number tags;
- use color by workstream, not decorative gradients;
- no screenshots of long Issue bodies;
- keep details in speaker notes / this document;
- make the assignment slide readable in under 30 seconds.

---

## 8. Closing line for the meeting

**把麻烦事交给 repo 管，把脑子留给真正的设计、系统和研究问题。**
