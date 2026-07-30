from __future__ import annotations

import json
import sys
from pathlib import Path


PROVIDER_ID = "provider.v3-load-path-pilot"
PROVIDER_VERSION = "v3-load-path-pilot-1"
CAPABILITY_ID = "v3.gate.load_path_analysis"
ROLE_MAP = {
    "load_source": "roof",
    "collector": "beam",
    "support": "column",
    "nonstructural": "opening",
}
FORBIDDEN_MODULE_PREFIXES = (
    "archflow.apps",
    "archflow.decision",
    "archflow.examples",
    "archflow.packs",
    "archflow.support.llm",
)


def main() -> int:
    v3_root = Path(sys.argv[1]).resolve()
    v3_fingerprint = sys.argv[2]
    sys.path.insert(0, str(v3_root))

    from archflow.brain.making.load_trace import (  # noqa: PLC0415
        analyze,
        support_edges,
        trace,
    )
    from archflow.core.contracts.component import (  # noqa: PLC0415
        BBox,
        Component,
    )

    forbidden = sorted(
        name
        for name in sys.modules
        if name.startswith(FORBIDDEN_MODULE_PREFIXES)
    )
    if forbidden:
        print(
            "forbidden transitive modules loaded: " + ", ".join(forbidden),
            file=sys.stderr,
        )
        return 9

    request = json.load(sys.stdin)
    snapshot = request["detached_snapshot"]
    graph = snapshot["component_graph"]
    components = []
    neutral_by_id = {}
    for item in graph["components"]:
        neutral_by_id[item["component_id"]] = item
        metadata = {
            "structural_layer": item["structural_layer"],
            "bears": item["bears"],
        }
        components.append(
            Component(
                id=item["component_id"],
                stage="structure",
                role=ROLE_MAP[item["load_role"]],
                source_rule="v3-readonly-pilot",
                reason="Detached neutral support-graph observation.",
                bbox=BBox(
                    tuple(item["bounds"]["min"]),
                    tuple(item["bounds"]["max"]),
                ),
                metadata=metadata,
            )
        )

    findings = analyze(components)
    paths = trace(components)
    edges = support_edges(components)
    component_by_id = {item.id: item for item in components}
    unsupported = []
    support_paths = []
    for source_id in sorted(paths):
        path = paths[source_id]
        support_paths.append(
            {
                "source_id": source_id,
                "path": path,
            }
        )
        endpoint = component_by_id[path[-1]]
        if endpoint.bbox.min[2] > 1e-6:
            unsupported.append(source_id)

    invariants = {
        "schema": "V3LoadPathSemanticInvariants@1",
        "all_load_sources_grounded": not unsupported,
        "hard_finding_count": sum(
            1 for item in findings if item.severity == "hard"
        ),
        "finding_codes": sorted({item.code for item in findings}),
        "unsupported_component_ids": sorted(unsupported),
        "support_paths": support_paths,
        "loaded_forbidden_modules": forbidden,
    }
    response = {
        "schema": "V3LegacyCapabilityOutput@1",
        "request_id": request["request_id"],
        "provider_id": PROVIDER_ID,
        "provider_version": PROVIDER_VERSION,
        "capability_id": CAPABILITY_ID,
        "v3_fingerprint": v3_fingerprint,
        "workspace_id": request["workspace_id"],
        "base": request["base"],
        "kind": "observation",
        "evidence_refs": [
            *request["evidence_refs"],
            "v3://archflow.brain.making.load_trace",
        ],
        "payload": {
            "schema": "V3LoadPathObservation@1",
            "input_id": snapshot["input_id"],
            "input_sha256": snapshot["input_sha256"],
            "semantic_invariants": invariants,
        },
    }
    print(json.dumps(response, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
