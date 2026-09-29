# MonkeyHub MVP roadmap

This document holds MonkeyHub's first MVP definition, migrated from product issue #254
(opened 2026-09-23), which closes when PR #470 merges. References to #254 as the MVP plan now
mean this document.

- It records product direction, rules and acceptance; concrete execution stays in bounded,
  independently closable issues.
- Live status, priority and dates: the GitHub Project, see
  [Project tracking](../PROJECT_TRACKING.md).

## Product decision

> **Do not ask architects to trust AI to design the building first. Start from the model they
> already trust, then remove a large part of the repetitive drawing / rendering / reporting
> work around it.**

The MVP is **not** "prompt → AI architecture". It is three connected loops over an externally
authored project:

```text
A. Model → Board → AI Render → Publish
B. Change propagation
C. Meeting → Work → Report
```

The user keeps modeling in SketchUp / Rhino and still gets the value of MonkeyHub.

The adoption wedge:

> **Trust the human-authored model; automate the production and continuity around it.**

Product sentence, which the MVP should prove before autonomous design generation expands:

> **Keep designing in the tools you trust. MonkeyHub keeps the rest of the project coherent
> as it changes.**

### Why this MVP

Architects may reasonably distrust "AI invents the architectural scheme". They do not need the
same leap of faith for: my own Rhino / SketchUp model → keep views synced → update drawings /
presentation graphics → generate visualization options → assemble review material.

If the MVP removes a meaningful share of the following work, it is useful before AI
architectural authorship is solved:

- exporting and re-exporting views;
- manually replacing images in boards;
- re-rendering after model changes;
- rebuilding weekly presentation decks;
- finding the correct source/revision;
- repeatedly explaining the same project context to different AI/tools.

## MVP 0 — fast external model ingestion (prerequisite)

Before polishing the three loops, make **existing SketchUp / Rhino projects cheap to bring
into MonkeyHub**. This is the highest-priority prerequisite:

```text
architect keeps modeling in SketchUp / Rhino
                    ↓
MonkeyHub consumes the trusted current project source
                    ↓
Board / Drawing / Render / Publish
```

The first product proof must work even if **AI generates no architectural geometry at all**.
Users are not required to remodel inside MonkeyHub.

Owners (snapshot 2026-09-29):

- #255 — SketchUp/Rhino external source → Board/Render ingestion performance; split out of
  #254 on 2026-09-23 as MVP P0.
- #53 — SketchUp import/export and identity-preserving ingestion.
- #260 — contributes only its native-identity slice to the MVP: #261 (merged 2026-09-24)
  reuses the exact ModelSource / P036 and gives read-only, paginated native metadata and
  GUID queries, with no automatic architectural semantics. The full #260 harness is **not** a
  gate for the MVP main line (comment 2026-09-23T23:22). Its next slice — one Rhino edit that
  lowers a specified window by 300 mm while the roof and unrelated objects stay, comparing a
  direct agent with the compiled harness on the same model, provider, task and starting state
  — is #260 research, with #32 supplying usage/time evidence and no presumption that the
  harness or OCBA brings a benefit.
- Listed as related in #254: #13 (CAD backend / Rhino boundary) and #14 / #136 (local
  interaction performance). #13 and #14 closed on 2026-09-15; #136 is open.

Measured ingest baseline and its limits: [EXTERNAL_MODEL_INGEST_BASELINE.md](../EXTERNAL_MODEL_INGEST_BASELINE.md).

Not yet proven as of the 2026-09-23T23:22 comment: #261 does not prove native modification,
Stage admission, cold-reopen verification, exact downstream invalidation or end-to-end
chaining on a real architecture project. Full files still need decoding, so no reduction in
import latency is claimed.

## MVP A — Model → Board → AI Render → Publish

Target experience:

```text
SketchUp / Rhino
→ choose current view
→ send / bind to Board
→ pick one image from project / office visual library
→ "render this in that direction"
→ leave it running
→ chosen result comes back
→ Publish composes the next usable presentation draft
```

The user decides only the model/view, the visual reference/direction, which result is
acceptable, and what needs to be presented. MonkeyHub owns the repetitive continuity around
those decisions.

Required behavior:

- exact external model/source identity is retained;
- current view/camera is retained where the source integration exposes it;
- the Board card is source-bound, not an anonymous screenshot;
- AI Render consumes the current source + selected visual references;
- generated results are retained individually with source/recipe/provider evidence;
- the selected result can flow into Board and Publish;
- Publish produces a usable report/presentation draft from project-linked artifacts, not a
  detached AI PPT.

AI Render is accepted with a real provider, not a mock (comment 2026-09-23T23:22: real
provider calls still needed configuration and acceptance).

Owners (snapshot 2026-09-29):

- #253 — AI Render first;
- #216 — Render results workspace;
- #218 — exact source-view → Physical Render later (#254 also listed #40, closed as not
  planned on 2026-09-15);
- #66 — Publish;
- #244 — 2D representation / Drawing where needed;
- #253 and #478 — Office Library / visual references / capability reuse (formerly #252,
  retired 2026-09-29): visual references are part of #253; promoting approved recipes into
  the shared library is #478.

## MVP B — change propagation

> **Change the model once; the project knows what became outdated.**

```text
external model rev 182
  → Board live view
  → AI Render v3
  → Publish slide 07

external model rev 183 arrives
          ↓
dependency update
          ↓
- live source view regenerates / replaces
- selected AI Render becomes OUTDATED
- Publish knows slide 07 references an older result
- cheap deterministic projections may refresh automatically
- expensive / ambiguous outputs become candidates for rerun or human review
```

### Update policy

The #254 body listed LIVE, PINNED and OUTDATED; its 2026-09-23T23:22 comment clarified that
these are two orthogonal axes:

- **Update policy** (更新策略): `LIVE` follows the compatible latest source automatically;
  `PINNED` keeps the chosen artifact / exact revision.
- **Source status** (来源状态): `current` / `outdated` (the source changed: preserve the old
  result and surface update / rerun) / `unresolved`.
- The axes are orthogonal: PINNED and OUTDATED are not mutually exclusive, and **a PINNED
  result can be outdated**.
- Never silently replace a selected final render, a reviewed image or a manually authored
  presentation result such as a slide. Keep it and flag it as outdated.
- Replace automatically only live projections — current model snapshots, generated plan
  previews — while their binding remains valid.
- External model edits and future #260 internal edits should end up updating the same source
  chain.

Reconciling this policy with main's representation status
([2026-09-26-representation-state.md](../2026-09-26-representation-state.md), which derives
`current` / `outdated` / `frozen` / `unavailable`, and where a `frozen` page is read but never
compared) is owned by #223.

Owners (snapshot 2026-09-29):

- #223 — shared change propagation; design vs representation/source binding;
- #90 — artifact change detection / Board refresh, closed 2026-09-21; its existing Board
  update entry is reused;
- #66 — Publish LIVE/FROZEN/STALE behavior;
- #218 / #253 — render source/version history;
- #271 — auto-follow Working Head; reuses #254's update-policy direction, but its body names
  LIVE / FROZEN / STALE as one three-way state;
- #278 — daily-use UI plan, listing #254 as related; its slices #287 (Live/Stale/Frozen on
  drawings) and #288 (stale Board pages) display this state.

## MVP C — Meeting → Work → Report

```text
Tencent Meeting transcript / minutes
        ↓
extract: discussed / decided / rejected / assigned / unresolved
        ↓
compare with current project state + previous report
        ↓
verify actual post-meeting work / issues / artifacts
        ↓
Project Delta
        ↓
next tasks / evidence
        ↓
Publish / Huaguoshan presentation recipe
        ↓
next review report
        ↓
human review
```

Critical rules:

- discussed ≠ decided;
- assigned ≠ completed;
- merged / generated ≠ accepted design result;
- use actual project evidence after the meeting;
- the presentation is a projection of project facts, not a second truth.

It can run in parallel with the main line (comment 2026-09-23T23:22): reuse the existing
report templates and selected project results, do not wait for the full Publish editor, and
do not treat the native object index as a new global State.

Owners (snapshot 2026-09-29): #480 (Meeting → Work → Report; took over from #252's office-demo
work, retired 2026-09-29); #66 (Publish); #55 (durable project mutation evidence where applicable,
closed 2026-09-15).

## Dependency order

Priority order in the #254 body:

| Priority | Step | Meaning |
| --- | --- | --- |
| P0 | External model ingestion performance | A real SketchUp / Rhino project enters, reopens and updates fast enough that users keep MonkeyHub attached to daily work. |
| P1 | Model → Board → AI Render | One exact external source/view → Board → one visual reference → one real AI Render result → retained history. |
| P2 | Change propagation | Modify/replace the external source → the live Board projection updates; the selected render / Publish use becomes explicitly outdated; no silent loss. |
| P3 | Publish | Current selected project artifacts produce a review-ready presentation/report draft. |
| P4 | Meeting → Work → Report | One real meeting + actual post-meeting project evidence produce the next report. |

Execution order (comment 2026-09-23T13:55, amended 2026-09-23T23:22):

```text
#255 fast external source ingestion (+ #260's native-identity slice, #261)
   ↓
#253 / #216 source model/view → AI Render → Board
   ↓
#223 change propagation (reusing #90's Board refresh)
   ↓
#66 Publish (fixed template first)

#480 Meeting → Work → Report — in parallel
```

The 13:55 comment gave `#255 → #253 → #90 / #223 → #66 → #252`. The 23:22 comment added #260's
first slice to the shared intake layer, put #216 beside #253, described the main line as
source model/view → AI Render → Board → fixed-template #66 Publish, routed model changes
through #223 while reusing #90's Board update entry, and made #252 parallel. #252's
Meeting → Work → Report now lives in #480.

## Acceptance (end-to-end MVP proof)

Use one representative, non-trivial architecture project, not only synthetic fixtures. A
successful demo:

1. open / update an external Rhino or SketchUp model;
2. place one exact current view on Board;
3. select one reference image;
4. request AI Render;
5. receive and choose a result;
6. modify the source model externally;
7. Hub updates live source projections and marks dependent selected results outdated;
8. generate / refresh the review presentation;
9. ingest one meeting record and produce the next-work / next-report delta;
10. close and reopen; project relationships remain intact.

Measure:

- external source import / update / reopen latency;
- time to first usable Board view;
- time from source/view to AI Render submission;
- manual steps eliminated;
- number of stale/outdated dependencies correctly detected;
- whether the final report uses the intended current/pinned artifacts;
- failures / retries / source mismatches.

Because #254 closes, this acceptance is tracked through the Project's MVP view and the child
issues listed above. It is an end-to-end proof; no single child issue satisfies it on its own.

## Non-goals

None of these is an MVP prerequisite:

- AI autonomous architectural scheme generation;
- fully replacing SketchUp / Rhino;
- Revit / AutoCAD rebuild;
- perfect bidirectional semantic reconciliation for every external CAD object;
- enterprise SSO / Billing / complex RBAC;
- universal multi-agent orchestration;
- full office Visual Library migration;
- perfect automatic regeneration of every downstream artifact.

The MVP enters the **existing** architecture workflow rather than demanding migration.

## Execution principle

The roadmap does not own implementation. Each child issue is independently closable and
defines what it proves. Product sequencing and priority belong in the GitHub Project, not in
a permanently open umbrella issue.
