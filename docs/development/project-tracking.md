# Project tracking and roadmap policy

MonkeyHub uses GitHub surfaces for different kinds of information instead of keeping
long-lived plans as permanently open issues. Issues are the execution layer, the GitHub
Project holds live planning state, and repository docs hold principles, charters and roadmap
narrative.

## Source-of-truth rule

| Information | GitHub surface |
| --- | --- |
| Concrete bug, feature, experiment, or bounded implementation task | Issue |
| Large bounded deliverable made of several tasks | Parent issue / sub-issues |
| Priority, status, initiative and dates; cross-cutting product or research sequencing | GitHub Project **MonkeyHub Development** (Roadmap views) |
| Release or deadline with a finite set of deliverables | Milestone |
| Long-lived architecture, team charter, product principles, research direction | Repository docs / ADRs |
| Early open-ended discussion before it becomes a task | Discussion |

An issue should have a condition under which it can be closed. "Keep open while the
team/project exists" is not a valid issue close condition.

Rules:

- The Issue stays the only task identity ([CONTRIBUTING.md](../CONTRIBUTING.md)). Priority,
  Status, Initiative and dates are Project fields on its item, not issue text or labels.
- Project Status is planning state. [`governance/work_registry.json`](../governance/work_registry.json)
  stays the only record of live source-edit claims. Neither mirrors the other.
- Durable design decisions go to [ARCHITECTURE.md](ARCHITECTURE.md) or an ADR in
  [adr/](adr/).
- Set Start date or Target date only with evidence: a sourced external deadline, a release, or
  an owner-stated target in the issue. Otherwise leave them empty.
- Use milestones only for real endpoints with a finite scope, such as a release or an external
  deadline. Area and Initiative names are never milestones.
- Do not add label families for Initiative, Kind or Priority. The existing `area:*` labels
  remain for repository search; the Project Area field is the planning dimension. Whether to
  retire the `area:*` labels is the owner's decision.
- Anything that would exist in three places keeps one source of truth and links from the
  others.

## GitHub Project

The Project **MonkeyHub Development** — a user project of cogco1, linked to this
repository — is the live planning surface.

Link: <https://github.com/users/cogco1/projects/1> (also listed under the repository's **Projects** tab).

### Fields

| Field | Values |
| --- | --- |
| Status | Backlog / Ready / In progress / Review / Done |
| Priority | P0 / P1 / P2 / Later |
| Area | Product / Core / Architecture / UI / Research / Render / Drawing / Model IO / Agent / Performance / Release / Office |
| Initiative | MVP / Agent / Rendering / Drawing / Projection / Research / Office AI / Infrastructure (multi-select: an item can serve several initiatives) |
| Kind | Bug / Feature / Experiment / Epic (GitHub reserves the name Type for its built-in issue types) |
| Effort | S / M / L / XL |
| Start date | date, only with evidence |
| Target date | date, only with evidence |
| Owner | the person or team driving the item — not the software module owner of [AGENTS.md](../AGENTS.md) |

Status meanings:

- **Backlog** — legitimate work, not scheduled yet.
- **Ready** — scope clear, dependencies met, can start.
- **In progress** — active branch, claim, draft PR or explicit current work.
- **Review** — completion PR open, or only waiting for CI/merge.
- **Done** — closed, or completion PR merged.

### Views

- **Current** — items in In progress or Review.
- **Backlog** — items in Backlog or Ready.
- **Product Roadmap** — Initiative MVP, Rendering or Drawing, on the roadmap layout.
- **Research** — Initiative Research, on the roadmap layout.
- **MVP** — Initiative MVP; tracks the [MVP acceptance](product/MVP_ROADMAP.md#acceptance-end-to-end-mvp-proof).
- **UI** — Area UI.
- **Bugs** — Kind Bug.
- **Recently Done** — Status Done, newest first.

The filters above define the views. Sorting, grouping and the roadmap's date fields are view
settings in the Project itself.

## Before opening an issue

- Search open and closed issues and the Project for an existing owner.
- If one exists, comment there instead of opening a parallel issue.
- A new issue states its close condition.
- Never open a roadmap, master or umbrella issue. Direction goes into docs; sequencing and
  priority go into the Project.

## Issue hygiene

- Do not keep an issue open solely as a roadmap, status dashboard, or team charter.
- When a test/review issue has served its purpose, move remaining work into existing owner
  issues and close it.
- When a later general issue supersedes a one-off fixture issue, keep the fixture as
  regression evidence and close the old umbrella.
- Negative experimental results are valid close conditions when the experiment was completed
  reproducibly.
- Child tasks should be independently closable.

## Roadmap documents

- [Algorithm team charter](research/ALGORITHM_TEAM.md) — from #119.
- [Research roadmap 2026–2027](research/ROADMAP_2026_2027.md) — from #176.
- [MVP roadmap](product/MVP_ROADMAP.md) — from #254.

These documents state direction and principles. The Project holds live status, priority and
dates; issues remain the execution layer.
