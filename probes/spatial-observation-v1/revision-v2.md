# Spatial Observation Revision Benchmark — revision-v2

## Measured conclusion and caveat

On a 34-entity public synthetic fixture, the guarded `SelectionIndex.select_revision` lab interface let every reduced-context selector (graph, lexical, MiniLM hybrid) reach 10/10 retrieval completeness and 17–19/20 downstream passes, at roughly 51–57% of the full-context input tokens of the `full` baseline. This is an opt-in laboratory measurement only. It is not a canonical acceptance, not evidence of a universal speed or quality advantage, and not a basis for production deployment. Scoring covers the strict returned-evidence plan only — not CAD edits, architecture acceptance, or visual reasoning accuracy. Dollar figures are API-equivalent estimates, not actual subscription charges (`actual_charge_usd` is null throughout).

Scope: a new opt-in lab selection interface plus an actual, limited `ClaudeConsumer`. It inherits #198 at `72f42480`; the #195 A/B/D representation runs are unchanged. No natural-language resolver and no product integration is claimed — the user/caller supplies query targets and conditions directly.

## Run shape

| Item | Value |
| --- | --- |
| Frozen implementation (before heldout) | `ca82e5a7` |
| Dependency head | `72f42480` |
| Fixture | 34 entities, retained through P036/OCCT |
| Queries | 4 development, 10 independently reviewed synthetic heldout |
| Budget / repeats | 8 / 2 |
| Planned vs retained unique CLI consumer trials | 80 / 80 |
| Runner retries / transport failures | 0 / 0 |
| Gate refusals (main run) | 16, all succeeded, counted separately, never model wins |
| Reported primary init alias | `claude-opus-5[1m]` |
| Assistant model | `claude-opus-5` |
| Auxiliary model | `claude-haiku-4-5-20251001` |

These identifiers were reported by CLI init, assistant messages and usage metadata. The requested alias is not an immutable checkpoint guarantee. The runner made no retries; CLI/provider internal retries were not independently observable.

The guard preserves the propagating closure, all affected upstream entities, supplied conditions, and the actual source-bound PNG. Required-closure overflow or open-ended search returns the full record; absent or stale images fail before the provider is called.

## Per-method results

| Method | Passed / 20 | Guarded completeness | Raw baseline completeness | Fallbacks | Mean text-context bytes | All-model input tokens | API-equiv USD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full | 19 | 10/10 | 10 | 0 | 18417.1 | 338,973 | 2.302526 |
| graph | 19 | 10/10 | 0 | 3 | 8452.3 | 172,579 | 1.266734 |
| lexical | 19 | 10/10 | 3 | 3 | 9048.8 | 189,774 | 1.359797 |
| embedding_graph (MiniLM hybrid) | 17 | 10/10 | 2 | 3 | 9205.7 | 191,971 | 1.371314 |

The raw baseline completeness column is measured against a **new, stricter upstream evidence truth**. It is not a like-for-like regression against earlier runs and is not proof that the old graph selector was broken. Guarded completeness is 10/10 for all four methods.

The three fallback tasks (`H5-unresolved`, `H6-similarity`, `H8-datum`) receive contexts identical to `full`; all their costs are counted in the tables above. Any output differences on those tasks are stochastic, not selector effects.

## Failures (six total, all scored)

| Failure mode | full | graph | lexical | hybrid |
| --- | --- | --- | --- | --- |
| H6: extra ground edges (correct, supported entities; out-of-answer-set edges) | 1 | 0 | 1 | 2 |
| H4: missed screen/lintel/seal although supplied — **critical omission** | 0 | 1 | 0 | 1 |

The H6 failures are edge-completeness violations with supported entities and correct decisions; they are qualitatively milder. The H4 failures carry `missed_critical_refs: [lintel, screen, seal]` and are reported distinctly as critical omissions: the evidence was supplied, and the model did not return it.

H9/H10 really transmitted the source-bound PNG (16 image calls across the run), but this run produced **no** visual-understanding or revised-geometry quality score.

## Embeddings

A real pinned MiniLM encoder from the prior protocol was used. All 34 input documents were truncated at the actual 128-token limit; fields of fully selected records are untruncated. Encoder setup took 933.7 ms; model download took 11.443 s (reported separately).

## Cost and overhead accounting

Token totals include auxiliary Haiku calls plus ordinary, cache-write, and cache-read tokens. `cache_read_tokens: 0` does **not** mean no cache was written — cache-write tokens are non-zero for every method (e.g. 194,097 for `full`, 100,925 for `graph`).

Measured API-equivalent provider cost reductions versus `full`: graph 45.0%, lexical 40.9%, hybrid 40.4%. All-model input reductions were 49.1%, 44.0% and 43.4%, respectively; token totals include image inputs while text-context bytes exclude PNG bytes. Each carries a distinct quality caveat: graph has one critical H4 omission; lexical has no critical omission but one H6 edge-completeness failure; the hybrid is both the most expensive reduced method and the weakest downstream (17/20, two H6 and one H4 failure).

| Method | full-rebuild ms | index ms | query total ms (10 queries) | gate ms | CLI wait s |
| --- | --- | --- | --- | --- | --- |
| full | 1.47 | 1.76 | 13.11 | 0.022 | 126.82 |
| graph | 1.57 | 1.70 | 3.78 | 0.014 | 119.50 |
| lexical | 1.62 | 1.60 | 6.99 | 0.014 | 126.39 |
| embedding_graph | 1034.98 | 1039.39 | 69.16 | 0.020 | 130.27 |

Query figures are totals across the 10 heldout queries, not per-query. Shared fixture preparation took 1.690 s and the front/top render 0.718 s; both are shared across methods. Serialization totals remain per-method in the summary JSON.

Total API-equivalent estimate: $6.300371. Local index/update/query overhead, encoder load, download, fixture preparation, render, serialization and CLI waits are reported separately. The CLI wait is not the entire end-to-end wall time, and no unmeasured end-to-end total is reported. There is no incremental updater. Package-install time, hardware, electricity and account charges are unknown. Other tasks used Claude concurrently, so wall time is descriptive and **no controlled latency gain** is claimed. Read the totals as a cost/input tradeoff against quality, not as universal speed or learned superiority.

## Validation

Validation has been independently confirmed: 78 tests passed using the real pinned encoder, `python tools/archcheck.py` reported PASS over 418 files, and the scope check passed. All data is public synthetic; there is no canonical acceptance.

Two separate Claude CLI documentation drafting calls cost $0.250677 in API-equivalent estimates; they are excluded from the 80 consumer trials. The primary agent checked and corrected the resulting text and executed its reproduction snippet.

## Artifacts

- [revision_protocol.md](../../labs/spatial_observation/revision_protocol.md)
- [revision_benchmark.py](../../labs/spatial_observation/revision_benchmark.py)
- [revision-v2-selection.json](revision-v2-selection.json)
- [revision-v2-downstream.jsonl](revision-v2-downstream.jsonl)
- [revision-v2-summary.json](revision-v2-summary.json)
- [revision-v2-development.json](revision-v2-development.json)
- [revision-v2-front.png](revision-v2-front.png)
- [revision-v2-top.png](revision-v2-top.png)

## Reproducibility

Run from the repository root:

```python
import json
from pathlib import Path
from labs.spatial_observation.revision_benchmark import METHODS, summarize

root = Path("probes/spatial-observation-v1")

selection = json.loads((root / "revision-v2-selection.json").read_text(encoding="utf-8"))
summary = json.loads((root / "revision-v2-summary.json").read_text(encoding="utf-8"))
downstream = [
    json.loads(line)
    for line in (root / "revision-v2-downstream.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]

assert summarize(selection["rows"], downstream, METHODS) == summary["methods"]
```
