"""GH-170 revision evidence consumer; no production ContextPack integration.

Public synthetic fixtures persist through Fixture/P036. This module owns only
caller-directed lab diagnostics. Gold is separate from every provider request.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
from random import Random
from time import perf_counter

from archflow.state.state_record import Entity, Relation
from .benchmark import encoded
from .fixture import EVIDENCE, Fixture, authored_record
from .provider import ClaudeConsumer, image_block
from .selection import METHODS, MiniLMEncoder, Query, SelectionIndex, require_current, retrieval_metrics


SCREEN = ("ground", "screen", "lintel", "seal", "fixing", "drain", "twin-b")
ROOF = ("ground", "roof-post", "roof-beam", "canopy", "gutter", "outlet")
POLICY = (
    "Return the evidence needed for the supplied request, using only observation. "
    "For change, include the targets, transitive declared downstream objects along "
    "invalidates/requires_revalidation edges, and every upstream support of all affected "
    "objects. Do not descend from shared upstream supports to unrelated siblings. "
    "For inspect/similar return only the objects requested by the question. Return every "
    "dependency edge_ref between your returned entities. Preserve unknown roles. "
    "Include every supplied condition id and required image id. Compare requested_changes "
    "with conditions; if inconsistent use decision=needs_resolution, otherwise "
    "decision=ready_for_checks. Neither decision means an edit or exact check was performed. "
    "Return exact_pairs from the request as proposed checks, never claim geometry measured "
    "or a candidate accepted. Output JSON {entity_refs:[strings],edge_refs:[strings],"
    "condition_refs:[strings],image_refs:[strings],unknown_role_refs:[strings],"
    "exact_pairs:[[string,string]],decision:string}. No extra fields."
)


def revision_record(variant: str):
    """Extend the public pilot without changing its historical variants."""
    record = authored_record(variant=variant)
    entities = list(record.entities)
    specs = (
        ("roof-post", 18, 0, .3, .3, 3, 0),
        ("roof-beam", 18, 0, 5, .3, .25, 3.1 if variant == "heldout" else 3),
        ("canopy", 18, 0, 5, 3, .25 if variant == "heldout" else .2, 3.4),
        ("gutter", 18, 3, 5, .2, .15, 3.3),
        # Explicitly remote: spatial neighbors cannot recover this dependency.
        ("outlet", 34 if variant == "heldout" else 30, 6, .2, .2, .4, 0),
    )
    for identifier, x, y, dx, dy, height, elevation in specs + tuple(
        (f"facade-panel-{i:02}", i * 1.2, 16, 1, .2, 2, 0) for i in range(16)
    ):
        entities.append(Entity(identifier, "Element@1", {
            "component_id": "building", "producer": "prism", "label": identifier.replace("-", " "),
            "role": None, "references": {"base": {"level": "ground"}},
            "params": {"profile": [[x, y], [x + dx, y], [x + dx, y + dy], [x, y + dy]],
                       "height": height, "elevation": elevation},
        }, parent_id="building", basis_refs=(EVIDENCE,)))
    chain = ROOF[1:]
    edges = tuple(Relation(f"{a}-to-{b}", "dependency", a, b, propagation="revalidate",
                           basis_refs=(EVIDENCE,)) for a, b in zip(chain, chain[1:]))
    return replace(record, entities=tuple(entities), relations=record.relations + edges)


def tasks():
    """Reviewer-authored explicit sets; neither indexes nor selectors derive gold."""
    def task(identifier, split, text, refs, required, *, operation="change", budget=8,
             conditions=(), changes=(), images=(), pairs=(), conflict=False):
        return {"id": identifier, "split": split, "query": Query(text, tuple(refs), operation),
                "budget": budget, "conditions": list(conditions), "requested_changes": list(changes),
                "required_images": list(images), "exact_pairs": list(pairs),
                "gold": {"relevant_entities": list(required), "critical_entities": list(required),
                         "decision": "needs_resolution" if conflict else "ready_for_checks"}}

    def keep(identifier, entity, value):
        return {"id": identifier, "entity_refs": [entity], "text": "Preserve params.height exactly.",
                "field": "params.height", "equals": value}

    all_refs = ("ground", "screen", "twin-a", "twin-b", "u-shell", "insert", "clash-a", "clash-b",
                "lintel", "seal", "fixing", "drain", "mystery", *ROOF[1:],
                *(f"facade-panel-{i:02}" for i in range(16)))
    return (
        task("D1-screen", "development", "Widen the south screen; gather all declared revision evidence.",
             ("screen",), SCREEN),
        task("D2-unknown", "development", "Resize this object without assigning a new role.",
             ("mystery",), ("ground", "mystery")),
        task("D3-budget", "development", "Raise canopy height and inspect all declared consequences.",
             ("canopy",), ROOF, budget=4),
        task("D4-conflict", "development", "Increase the screen height while preserving the supplied keep condition.",
             ("screen",), SCREEN, conditions=(keep("keep-screen-height", "screen", 3),),
             changes=({"entity_ref": "screen", "field": "params.height", "value": 3.5},), conflict=True),
        task("H1-support", "heldout", "Raise the roof support. Include remote declared consequences and their supports.",
             ("roof-post",), ROOF),
        task("H2-upstream", "heldout", "Deepen the gutter. Identify all affected items and their upstream evidence.",
             ("gutter",), ROOF),
        task("H3-shared-drain", "heldout", "Move square marker B and gather all evidence for its affected outlet.",
             ("twin-b",), SCREEN),
        task("H4-unknown-fastener", "heldout", "Change the fastener height without replacing its recorded role.",
             ("fixing",), SCREEN),
        task("H5-unresolved", "heldout", "Find only the object labelled unclassified object whose recorded role is unknown.",
             (), ("mystery",), operation="inspect"),
        task("H6-similarity", "heldout", "Return square markers A and B and the weep outlet to inspect B's declared drain dependency. Do not invent an A-to-drain edge.",
             ("twin-a",), ("twin-a", "twin-b", "drain"), operation="similar"),
        task("H7-conflict", "heldout", "Thicken the canopy while honoring the supplied height constraint.",
             ("canopy",), ROOF, conditions=(keep("keep-canopy-height", "canopy", .25),),
             changes=({"entity_ref": "canopy", "field": "params.height", "value": .45},), conflict=True),
        task("H8-datum", "heldout", "Raise the shared ground datum; return every declared affected object and support.",
             ("ground",), all_refs),
        task("H9-front", "heldout", "Use the supplied current front image when planning checks before revising the canopy and beam. Return the required image and proposed exact pair; no measured collision claim.",
             ("canopy", "roof-beam"), ROOF, images=("front",), pairs=(("canopy", "roof-beam"),)),
        task("H10-top", "heldout", "Use the current top image before revising the freestanding insert while keeping the shell. Return the image and proposed exact pair, without assuming collision from overlapping bounding boxes.",
             ("insert", "u-shell"), ("ground", "insert", "u-shell"), images=("top",),
             pairs=(("insert", "u-shell"),)),
    )


def prompt_blocks(task, result):
    """The first real consumer; only supplied evidence crosses this boundary."""
    prompt = {"policy": POLICY, "query": asdict(task["query"]),
              "requested_changes": task["requested_changes"], "required_images": task["required_images"],
              "exact_pairs": task["exact_pairs"], "observation": result["context"]}
    blocks = [{"type": "text", "text": encoded(prompt)}]
    for item in result["images"]:
        blocks.extend([{"type": "text", "text": f"Source-bound image: {item['id']}"}, image_block(item["png"])])
    return prompt, blocks


def score(parsed, task, result, snapshot):
    """Strict supported evidence plan, not successful CAD revision or visual reasoning."""
    parsed = parsed if isinstance(parsed, dict) else {}
    fields = ("entity_refs", "edge_refs", "condition_refs", "image_refs", "unknown_role_refs")
    valid = all(isinstance(parsed.get(key), list) and all(isinstance(v, str) for v in parsed[key])
                and len(parsed[key]) == len(set(parsed[key])) for key in fields)
    wanted = set(task["gold"]["relevant_entities"])
    expected_edges = {e["id"] for e in snapshot["dependencies"] if {e["source"], e["target"]} <= wanted}
    unknown = {e["id"] for e in snapshot["entities"]
               if e["id"] in wanted and e["metadata"].get("role") is None and e["id"] != "ground"}
    actual = {key: set(parsed[key]) if valid else set() for key in fields}
    checks = {
        "entities_complete": valid and actual["entity_refs"] == wanted,
        "edges_complete": valid and actual["edge_refs"] == expected_edges,
        "entities_supported": valid and actual["entity_refs"] <= set(result["entity_refs"]),
        "edges_supported": valid and actual["edge_refs"] <= set(result["edge_refs"]),
        "conditions_preserved": valid and actual["condition_refs"] == {c["id"] for c in task["conditions"]},
        "conditions_supported": valid and actual["condition_refs"] <= {c["id"] for c in result["context"]["conditions"]},
        "image_refs_correct": valid and actual["image_refs"] == set(task["required_images"]),
        "images_supported": valid and actual["image_refs"] <= {i["id"] for i in result["images"] if i.get("png")},
        "unknown_roles_preserved": valid and actual["unknown_role_refs"] == unknown,
        "decision_correct": parsed.get("decision") == task["gold"]["decision"],
        "exact_pairs_correct": parsed.get("exact_pairs") == [list(p) for p in task["exact_pairs"]],
        "no_extra_claim_fields": set(parsed) <= {*fields, "exact_pairs", "decision"},
    }
    return {"valid": valid, "passed": all(checks.values()), "checks": checks,
            "missed_critical_refs": sorted(wanted - actual["entity_refs"]),
            "unsupported_entity_refs": sorted(actual["entity_refs"] - set(result["entity_refs"]))}


def negative_checks(index, old_snapshot, current, images):
    """Refusals happen before provider calls, including a full-context method."""
    query = Query("Change canopy height", ("canopy",), "change")
    attempts = []
    for name, action in (
        ("stale-index", lambda: index.select_revision(query, snapshot=current, budget=8)),
        ("missing-image", lambda: index.select_revision(query, snapshot=old_snapshot, budget=8,
                                                       required_images=("front",))),
        ("stale-image", lambda: index.select_revision(query, snapshot=old_snapshot, budget=8,
                                                     required_images=("front",), images=images)),
        ("stale-result", lambda: require_current({"source": index.source}, current)),
    ):
        start = perf_counter()
        try:
            action()
        except ValueError as exc:
            attempts.append({"case": name, "rejected": True, "reason": str(exc),
                             "elapsed_ms": (perf_counter() - start) * 1000, "provider_calls": 0})
        else:
            raise AssertionError(f"unsafe pre-consumer gate: {name}")
    return attempts


def summarize(rows, trials, methods):
    summary = {}
    for method in methods:
        selected = [row for row in rows if row["result"]["method"] == method]
        calls = [row for row in trials if row["method"] == method]
        usage = [u for row in calls for u in (row["call"]["provider_result"].get("modelUsage") or {}).values()]
        cost_values = [row["call"]["cli_api_equivalent_usd"] for row in calls]

        def count(*keys):
            if not calls or any(not row["call"]["provider_result"].get("modelUsage") for row in calls):
                return None
            values = [u.get(key) for u in usage for key in keys]
            return sum(values) if all(type(v) is int and v >= 0 for v in values) else None
        summary[method] = {
            "tasks": len(selected), "fallbacks": sum(row["result"]["coverage"]["fallback_to_full"] for row in selected),
            "retrieval_complete": sum(row["metrics"]["entity_recall"] == 1 for row in selected),
            "mean_context_bytes": sum(row["result"]["cost"]["context_bytes"] for row in selected) / len(selected),
            "query_ms_total": sum(row["result"]["cost"]["query_ms"] for row in selected),
            "calls": len(calls), "passed": sum(row["scores"]["passed"] for row in calls),
            "failures": [row["id"] for row in calls if not row["scores"]["passed"]],
            "all_model_input_tokens": count("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"),
            "ordinary_input_tokens": count("inputTokens"), "cache_read_tokens": count("cacheReadInputTokens"),
            "cache_write_tokens": count("cacheCreationInputTokens"), "output_tokens": count("outputTokens"),
            "missing_usage_calls": sum(not row["call"]["provider_result"].get("modelUsage") for row in calls),
            "cli_api_equivalent_usd": sum(cost_values) if calls and all(v is not None for v in cost_values) else None,
            "actual_charge_usd": None,
            "wall_seconds": sum(row["call"]["cli_wall_seconds"] for row in calls),
        }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--claude", type=Path)
    parser.add_argument("--phase", choices=("development", "heldout"), required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    fixtures = {}
    for variant in ("base", "heldout"):
        fixtures[variant] = Fixture.create(args.output / "fixtures" / variant,
            authored=revision_record(variant), revision=f"revision-v2-{variant}")
    snapshots = {name: fixture.snapshot() for name, fixture in fixtures.items()}
    views = fixtures["heldout"].render_views(("front", "top"))
    images = [{"id": key, "png": value["png"], "source": value["source"]} for key, value in views.items()]
    for item in images:
        (args.output / f"{item['id']}.png").write_bytes(item["png"])
    encoder = MiniLMEncoder(str(args.encoder))
    report = {"protocol": "revision-selection-v2", "phase": args.phase, "snapshots": snapshots,
              "encoder_once": {"identity": encoder.identity, "setup_ms": encoder.setup_ms,
                               "weight_bytes": encoder.weight_bytes, "download_seconds": None},
              "fixture_preparation_seconds": {k: v.preprocessing_seconds for k, v in fixtures.items()},
              "image_render_seconds": {k: v["render_seconds"] for k, v in views.items()},
              "methods": {}, "rows": [], "gate_checks": {}, "trials": []}
    selected_tasks = [t for t in tasks() if t["split"] == args.phase]
    name = "base" if args.phase == "development" else "heldout"
    snapshot = snapshots[name]
    results = {}
    for method in METHODS:
        indexes = {key: SelectionIndex(value, method, encoder=encoder if method == "embedding_graph" else None)
                   for key, value in snapshots.items()}
        report["methods"][method] = {"index": indexes["base"].cost, "full_rebuild": indexes["heldout"].cost,
                                     "update_policy": "full rebuild; no incremental index",
                                     "encoder_input_truncation": indexes[name].input_truncation}
        report["gate_checks"][method] = negative_checks(indexes["base"], snapshots["base"], snapshots["heldout"], images)
        for task in selected_tasks:
            result = indexes[name].select_revision(task["query"], snapshot=snapshot, budget=task["budget"],
                conditions=task["conditions"], required_images=task["required_images"],
                images=images if task["required_images"] else ())
            require_current(result, snapshot)
            gold = task["gold"]
            gold["relevant_edges"] = sorted({e["id"] for e in snapshot["dependencies"]
                if {e["source"], e["target"]} <= set(gold["relevant_entities"])})
            metrics = retrieval_metrics(result, **{k: gold[k] for k in
                ("relevant_entities", "relevant_edges", "critical_entities")})
            raw_metrics = retrieval_metrics({"entity_refs": result["raw_entity_refs"],
                "edge_refs": result["raw_edge_refs"]}, **{k: gold[k] for k in
                ("relevant_entities", "relevant_edges", "critical_entities")})
            retained = {**result, "images": [{k: v for k, v in image.items() if k != "png"} for image in result["images"]]}
            report["rows"].append({"task": {**task, "query": asdict(task["query"])},
                                   "result": retained, "metrics": metrics, "raw_metrics": raw_metrics})
            results[task["id"], method] = result
    (args.output / "retrieval.json").write_text(encoded(report), encoding="utf-8")
    if args.claude:
        consumer = ClaudeConsumer(args.claude, model="claude-opus-5[1m]", diagnostics=args.output / "diagnostics")
        jobs = [(rep, task, method) for rep in range(args.repetitions) for task in selected_tasks for method in METHODS]
        # Sequential, deterministic shuffle: avoid method/burst order and log each
        # attempt immediately. No hidden retries or provider result repair.
        Random(170).shuffle(jobs)
        for repetition, task, method in jobs:
            identifier = f"{task['id']}-{method}-r{repetition}"
            result = results[task["id"], method]
            require_current(result, fixtures[name].snapshot())
            prompt, blocks = prompt_blocks(task, result)
            call = consumer.call(blocks, call_id=identifier)
            provider = call["provider_result"]
            valid = call["exit_code"] == 0 and not call["timeout"] and provider.get("subtype") == "success" and not provider.get("is_error")
            trial = {"id": identifier, "task_id": task["id"], "method": method, "repetition": repetition,
                     "prompt": prompt, "image_block_count": len(result["images"]), "call": call,
                     "scores": score(call["parsed"] if valid else None, task, result, snapshot)}
            report["trials"].append(trial)
            with (args.output / "trials.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(encoded(trial) + "\n")
            print(encoded({"id": identifier, "passed": trial["scores"]["passed"],
                           "cost": call["cli_api_equivalent_usd"]}), flush=True)
    report["summary"] = summarize(report["rows"], report["trials"], METHODS)
    (args.output / "report.json").write_text(encoded(report), encoding="utf-8")
    print(encoded(report["summary"]), flush=True)


if __name__ == "__main__":
    main()
