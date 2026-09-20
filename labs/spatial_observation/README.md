# Native spatial observation and selective context pilot

This research slice addresses [#195](https://github.com/cogco1/MonkeyHub/issues/195)
and [#170](https://github.com/cogco1/MonkeyHub/issues/170). It composes existing
StateRecord, P036, OCCT, drawing and Monitor owners. It adds no production tool,
canonical state, persistent index or project acceptance capability.

Read [results](../../probes/spatial-observation-v1/README.md), the
[seven-direction source study](research.md), the [observation protocol](protocol.md),
[fixture audit](fixture_protocol.md), and [selection protocol](selection_protocol.md).
These distinguish measured results, input limitations and future hypotheses.

## Verified production baseline

The measured checkout is `3a92b41460b52c04963278a3300a29c34744c43e`, fetched from
origin/main before this branch was created. Source changes in this PR are lab-only.
The following table names production capability, not research effect claims.

| Fact | Existing implementation at the measured commit | Reuse / remaining gap / experiment |
|---|---|---|
| Machine-readable state already exists | [StateRecord](https://github.com/cogco1/MonkeyHub/blob/3a92b41460b52c04963278a3300a29c34744c43e/archflow/state/state_record.py#L359), [Agent state tools](https://github.com/cogco1/MonkeyHub/blob/3a92b41460b52c04963278a3300a29c34744c43e/apps/monkeyhub/api/monkeyhub_api/chat.py#L2361) | A uses declared parameters, identity, roles and dependencies; current product is not screenshot-only. Snapshot is derived, never authoritative. |
| Exact shape queries exist in the kernel | [measure_occt_solid_pairs](https://github.com/cogco1/MonkeyHub/blob/3a92b41460b52c04963278a3300a29c34744c43e/archflow/adapters/occt_backend.py#L1048) | Lab cold-verifies retained STEP and delegates true solid distance/common volume. It is not an existing general Agent query API; bbox is not clearance. |
| Object/line visibility exists before rasterization | [project_occt_lines](https://github.com/cogco1/MonkeyHub/blob/3a92b41460b52c04963278a3300a29c34744c43e/archflow/adapters/occt_backend.py#L1299) | D0 exposes real object IDs and visible/hidden polyline counts. All scene solids participate; no pixel occlusion-ratio claim. |
| Current model view is five-way orthographic line PNG | [model_view](https://github.com/cogco1/MonkeyHub/blob/3a92b41460b52c04963278a3300a29c34744c43e/apps/archflow-studio/api/archflow_studio_api/application/drawings.py#L117), [ModelViewDto](https://github.com/cogco1/MonkeyHub/blob/3a92b41460b52c04963278a3300a29c34744c43e/apps/archflow-studio/api/archflow_studio_api/transport/drawings.py#L14) | B0/D0 use identical PNGs checked byte-for-byte against production. No IDs, crop-in-meters, depth, normals, RGB materials or arbitrary camera are smuggled into B0. |
| Agent image transport is text and PNG content blocks | [Claude image input](https://github.com/cogco1/MonkeyHub/blob/3a92b41460b52c04963278a3300a29c34744c43e/apps/monkeyhub/api/monkeyhub_api/chat.py#L1472) | Real CLI calls consume image blocks. Raw GPU buffers/learned tensors have no connected consumer here; C/E are not emulated with text. |
| Costs have an existing diagnostic owner | [MonkeyMonitor](../../monkeymonitor/usage.py), [Hub usage normalization](../../apps/monkeyhub/api/monkeyhub_api/chat_trace.py) | Reuse UsageLog/TokenUsage; retain disjoint cache/ordinary inputs, auxiliary models, unavailable counters and CLI price-estimate basis. |

Related PR status was checked through GitHub: #184 (observation prompt), #188/#192
(continuation/measurement) and #193/#194 (registered drawing-page reads/locks)
are merged in the baseline. #178/#189 remain lab evaluator code. #182 was still
open at the 2026-09-20 source audit and is not counted as production capability.
Registered drawing-page images do not provide full 3D depth/normal/ID buffers.

## Reproduce

Use the repository's CAD environment (`cadquery-ocp==7.9.3.1.1`, Pillow 12.3.0,
Python 3.12) and its existing Studio API dependencies. The fixture is created
through P036 in the explicitly supplied external experiment root. Never use a
private/active project root as the output argument. All project geometry is
public synthetic code from `fixture.py`.

```console
python -m pytest labs/spatial_observation -q
python tools/archcheck.py
python -m labs.spatial_observation.benchmark --output <external-new-diagnostic-root> --claude <existing-claude-executable> --repetitions 3
```

The provider invocation makes real account calls. It uses the existing login,
`--safe-mode`, empty tools, strict empty MCP configuration, no session persistence,
an empty temporary working directory and a fixed system prompt. It does not change
global model/auth settings. The CLI protocol and price-estimate interpretation
follow the [official headless guide](https://code.claude.com/docs/en/headless).
No seed is available; actual returned versions and all costs are retained.

For selection, use `MiniLMEncoder` and `run_benchmark` as shown in
[selection_protocol.md](selection_protocol.md); optional FastEmbed lives in an
isolated environment, and the pinned ONNX weights stay in an external cache.
The actual inference tests skip when that optional dependency/cache is unavailable;
all baseline/binding tests still run. No fake embedding replaces unavailable weights.

```console
python -m labs.spatial_observation.downstream --selection-results <selection.json> --output <external-new-consumer-root> --claude <existing-claude-executable> --repetitions 3
```

This separate #170 consumer check asks five held-out questions under each fixed
structured selection method, three repetitions each. Every call has the same
policy/model/effort/output budget, only the selected context changes. It receives
no gold, method label, ranking scores or omitted-ID audit. It returns supported
entity/edge references, not architectural quality judgments. Contexts remain
incomplete; answer support is checked against the actual supplied references.
Retrieval cost, exact suspicion checks and downstream model usage are separate.

To recompute the retained summary without new model calls, run this Python from
the repository root in the same CAD environment. It reads the primary, earlier
and smoke runs independently and verifies the committed summary:

```python
import json
from pathlib import Path
from labs.spatial_observation.analyze import summarize_observation, summarize_downstream

root = Path("probes/spatial-observation-v1")
def rows(name):
    return [json.loads(line) for line in (root / name).read_text(encoding="utf-8").splitlines()]

summary = {
    key: summarize_observation(rows(f"observation-{name}.jsonl"))
    for key, name in (
        ("explicit_camera_v2", "explicit-camera-v2"),
        ("orientation_unspecified_v1", "orientation-unspecified-v1"),
        ("smoke", "smoke"),
    )
}
summary["selection_consumer"] = summarize_downstream(
    rows("downstream.jsonl"), json.loads((root / "selection.json").read_text(encoding="utf-8"))
)
assert summary == json.loads((root / "summary.json").read_text(encoding="utf-8"))
print("Retained summary matches recomputed observations and downstream results.")
```

## Boundaries

This small fixture establishes repeatable measurements and explicit information
loss, not architectural utility on a real building. Roles/relevance are authored
synthetic facts, independently checked in source/tests; they have not received
external human design-task approval. The edited fixture is an authored second
revision, not a claim that a production autonomous edit loop ran.

Unknown/hidden information must not be counted as a model reasoning failure.
Correct statement, wrong assertion, reasonable abstention and unnecessary
abstention are reported separately. Query distance is between named solids;
it is not a generalized path/circulation clearance or proof of design intent.
Learned methods can propose associations but cannot suppress declared closure or
replace source verification. Production integration is a separate owner decision.
