# P052 zoning city project to ArchFlow building runtime mapping

## Scope and evidence boundary

This comparison reads only the zoning V2 control structure for the external
Fremont and Livermore projects: directory names, compact manifests, run-lock
keys, run-state keys, cloud configuration, and repository contracts. It does
not ingest city PDFs, sealed Gold content, detailed evidence packs, answers, or
large CSV data.

The observed zoning hierarchy is:

```text
V2_RUNTIME/
├─ workspace/
│  ├─ artifact_store/sha256/
│  └─ projects/<project_id>/
│     ├─ inputs/
│     ├─ cases/<case_id>/
│     │  ├─ facts/
│     │  ├─ question/
│     │  └─ gold/
│     └─ runs/<run_id>/
│        ├─ run.lock.json
│        ├─ run_state.json
│        ├─ artifacts/
│        ├─ answers/
│        ├─ evaluation/
│        ├─ metrics/
│        └─ report/
├─ cache/
└─ temp/
```

Fremont currently has one case and two recorded runs. Livermore has one case
and seven recorded runs. Their `run-lock@2.0.0` records bind release, protocol,
component/schema/prompt/Skill/model identities, case/question hashes, budget,
and gate policy. Their roughly 97–105 KB run-state files retain module timing,
artifacts, evidence packs, cache identity, slot routing, four module receipts,
Gold access, model calls, and completion state.

## Semantic mapping

| Zoning V2 | ArchFlow V4 | Mapping decision |
| --- | --- | --- |
| `project_id` city/development application | building `project_id` | Direct ownership boundary. |
| `inputs/application` | raw request, client brief, site/program inputs | Store through project `input/`; never framework defaults. |
| `inputs/regulations` | authorized research, code, site, climate, precedent evidence | Store through project `input/` with content identity. |
| private `gold_sources` | no direct equivalent | ArchFlow has explicit commitments, authority, reviews, and gates; it must not invent a hidden design Gold. |
| `case_id` question package | no mandatory new directory layer | A building's brief, facts, commitments, and obligations evolve in project/run records. Independent commissions should use separate project ids. |
| fact ledger | evidence-bound facts and commitments | Project records referenced by canonical or branch-local state. |
| public question | raw brief plus unresolved obligations | Compiled into operational context, not a separate answer task authority. |
| sealed Gold | acceptance policy and review evidence | Hard gates, commitment checks, human authority, and review receipts stay distinct. |
| `run.lock.json` | `run.json` exact-base identity plus versioned records | ArchFlow already binds a run to exact canonical `HEAD`; provider/policy receipts remain run records. |
| monolithic `run_state.json` | events, canonical snapshots, branch records, receipts | Do not copy the monolith; ArchFlow already has finer event-sourced ownership. |
| answers | Architect proposals and candidates | Remain non-canonical until independent validation and promotion. |
| evaluation/metrics | `reviews/` and typed validation/evaluation receipts | Hard, commitment, and aesthetic authority remain separate. |
| report | `exports/` | Human-facing and explicitly non-authoritative. |
| global Artifact Store | project-local `objects/sha256/` | P052 retains project containment; global deduplication requires a later contract. |
| preprocess cache | external `cache/` | Rebuildable and non-canonical. |

## ArchFlow external runtime

```text
ARCHFLOW_RUNTIME/
├─ workspace/
│  └─ projects/<project_id>/
│     ├─ project.json
│     ├─ HEAD
│     ├─ input/
│     ├─ objects/sha256/
│     ├─ events/
│     ├─ canonical/
│     ├─ runs/<run_id>/
│     └─ exports/
├─ cache/
└─ temp/
```

The important reuse is the external-root contract, cloud parity, and clear
project/run ownership. The important non-reuse is zoning's `case/question/Gold`
business hierarchy. ArchFlow's native hierarchy is project, canonical version,
run, branch, proposal/candidate, event, and review.

## Promotion boundary

The active external project is the working project document. A committed probe
is selected evidence for deterministic regression or publication. P052 does
not automate promotion because selection changes repository evidence and must
be reviewed. It also does not add a database. A future SQLite read model may
index external and promoted projects only after its ownership and rebuild
contract are separately accepted.
