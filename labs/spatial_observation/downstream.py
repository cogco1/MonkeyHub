"""Fixed-representation #170 consumer check, separate from A/B0/D0.

Uses only the already selected context and the held-out question. No gold,
selection score, method identity or omitted-object list enters the model prompt.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from .benchmark import encoded
from .provider import ClaudeConsumer


POLICY = (
    "Select only entity_refs and edge_refs supported by the supplied structured context that "
    "answer this question. For impact questions include the changed object and its "
    "transitive declared downstream dependents; follow dependency direction. For a suspected "
    "collision select the requested pair but do not assert collision without exact measurement. "
    "Unknown roles must remain unknown. Do not invent missing objects or links. Context may "
    "be incomplete. Output {entity_refs:[strings],edge_refs:[strings],limitations:[strings]}. "
    "Edge refs must be existing edge_ref values, not new inferred relations."
)


def score_refs(parsed, row):
    parsed = parsed or {}
    valid = all(isinstance(parsed.get(key), list) and all(isinstance(v, str) for v in parsed[key])
                for key in ("entity_refs", "edge_refs"))
    entities = set(parsed["entity_refs"]) if valid else set()
    edges = set(parsed["edge_refs"]) if valid else set()
    wanted = set(row["gold"]["relevant_entities"])
    wanted_edges = set(row["gold"].get("relevant_edges", []))
    supplied = set(row["result"]["entity_refs"])
    supplied_edges = set(row["result"]["edge_refs"])
    return {"valid": valid, "complete_entity_answer": valid and entities == wanted,
            "supported_complete_entity_answer": valid and entities == wanted and entities <= supplied,
            "supported_complete_edge_answer": valid and edges == wanted_edges and edges <= supplied_edges,
            "entity_precision": len(entities & wanted) / len(entities) if entities else 0,
            "entity_recall": len(entities & wanted) / len(wanted),
            "missed_critical_refs": sorted(set(row["gold"].get("critical_entities", [])) - entities),
            "unsupported_entity_refs": sorted(entities - supplied),
            "unsupported_edge_refs": sorted(edges - supplied_edges),
            "complete_edge_answer": valid and edges == wanted_edges,
            "edge_recall": len(edges & wanted_edges) / len(wanted_edges) if wanted_edges else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    report = json.loads(args.selection_results.read_text(encoding="utf-8"))
    rows = [row for row in report["rows"] if row["split"] == "heldout"]
    args.output.mkdir(parents=True, exist_ok=True)
    consumer = ClaudeConsumer(args.claude, model="claude-opus-5[1m]", diagnostics=args.output / "diagnostics")
    jobs = [(rep, row) for rep in range(args.repetitions) for row in rows]

    def run(job):
        repetition, row = job
        method = row["result"]["method"]
        identifier = f"{row['task_id']}-{method}-r{repetition}"
        prompt = {"policy": POLICY, "query": row["query"], "observation": row["result"]["context"]}
        result = consumer.call([{"type": "text", "text": encoded(prompt)}], call_id=identifier)
        valid = (result["exit_code"] == 0 and not result["timeout"] and not result["provider_result"].get("is_error")
                 and result["provider_result"].get("subtype") == "success")
        output = {"id": identifier, "task_id": row["task_id"], "method": method,
                  "repetition": repetition, "prompt": prompt, "call": result,
                  "scores": score_refs(result["parsed"] if valid else None, row)}
        (args.output / f"{identifier}.json").write_text(encoded(output), encoding="utf-8")
        return {key: output[key] for key in ("id", "method", "scores")}

    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(run, jobs):
            print(encoded(result), flush=True)


if __name__ == "__main__":
    main()
