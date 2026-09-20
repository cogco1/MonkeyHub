"""Descriptive summaries from retained pilot responses, not model self-grading."""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import fmean

from .downstream import score_refs


def span(values):
    values = [v for v in values if v is not None]
    return {"mean": fmean(values), "min": min(values), "max": max(values)} if values else None


def call_cost(calls):
    by_model = defaultdict(Counter)
    unknown_counters = []
    for call in calls:
        for name, row in (call["provider_result"].get("modelUsage") or {}).items():
            for key in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "thinkingTokens"):
                if row.get(key) is None:
                    unknown_counters.append([call["call_id"], name, key])
                else:
                    by_model[name][key] += row[key]
    return {"cli_calls": len(calls), "api_equivalent_known_subtotal_usd": sum(c["cli_api_equivalent_usd"] or 0 for c in calls),
            "api_equivalent_complete": all(c["cli_api_equivalent_usd"] is not None for c in calls),
            "per_model_disjoint_tokens": dict(by_model), "unknown_counters": unknown_counters,
            "actual_charge_usd": None, "subscription_quota": None,
            "returned_models": sorted({a["model"] for c in calls for a in c["assistants"] if a.get("model")}),
            "cli_wall_seconds": sum(c["cli_wall_seconds"] for c in calls),
            "failed_calls": sum(c["timeout"] or c["exit_code"] != 0 or c["parsed"] is None or
                                c["provider_result"].get("subtype") != "success" or bool(c["provider_result"].get("is_error")) for c in calls)}


def summarize_observation(trials):
    groups = defaultdict(list)
    for trial in trials:
        group = f"{trial['variant']}-s{trial['scale']:g}-{'-'.join(trial['views'])}-{trial['condition']}"
        groups[group].append(trial)
    result = {}
    for key, rows in groups.items():
        calls = [call for row in rows for call in row["calls"]]
        categories, numeric = Counter(), defaultdict(list)
        resolved_abstentions, corrected_assertions = 0, 0
        failures, first_complete, first_complete_cost, first_complete_round = [], [], [], []
        for row in rows:
            for before, after in zip(row["scores_by_round"][0], row["final_scores"]):
                if not before["correct"] and after["correct"]:
                    if before["status"] == "unknown":
                        resolved_abstentions += 1
                    elif before["status"] == "known":
                        corrected_assertions += 1
            for mark in row["final_scores"]:
                if mark["status"] not in ("known", "unknown", "stale"):
                    category = "invalid_or_missing_answer"
                elif mark["id"] == "stale":
                    category = "correct_protocol_refusal" if mark["correct"] else "incorrect_protocol_response"
                elif mark["id"] == "unknown_role":
                    category = "role_abstention" if mark["correct"] else "incorrect_role_assertion"
                elif row["condition"] == "B0":
                    category = ("appropriate_abstention" if mark["status"] == "unknown" else
                                "correct_visual_inference_unverified_id_binding" if mark["correct"] and mark["id"] == "correction" and "top" in row["views"] else
                                "unsupported_correct_guess" if mark["correct"] else "wrong_assertion")
                else:
                    category = ("correct_answer_with_available_facts" if mark["correct"] else
                                "abstention_despite_available_facts" if mark["status"] == "unknown" else "wrong_assertion")
                categories[category] += 1
                if mark["absolute_error"] is not None:
                    numeric[mark["id"]].append(mark["absolute_error"])
                if not mark["correct"]:
                    failures.append({"trial_id": row["trial_id"], **mark})
            for turn, scores in enumerate(row["scores_by_round"]):
                if all(mark["correct"] for mark in scores):
                    first_complete.append(row["calls"][turn].get("completed_after_seconds"))
                    first_complete_cost.append(sum(c["cli_api_equivalent_usd"] or 0 for c in row["calls"][:turn+1]))
                    first_complete_round.append(turn)
                    break
        answerable = 0 if rows[0]["condition"] == "B0" else 9 * len(rows)
        result[key] = {
            "trials": len(rows), "mechanical_correct_per_repeat": [r["correct"] for r in rows],
            "mechanical_denominator": sum(r["denominator"] for r in rows), "answer_categories": categories,
            "answerable_exact_fact_denominator": answerable,
            "accuracy_on_answerable_exact_facts": categories["correct_answer_with_available_facts"] / answerable if answerable else None,
            "answerable_exact_fact_coverage": answerable / (9 * len(rows)),
            "numeric_absolute_error": {name: {"answered": len(values), **span(values)} for name, values in numeric.items()},
            "query_count_per_repeat": [len(r["queries"]) for r in rows],
            "failed_queries": sum("error" in q["response"] for r in rows for q in r["queries"]),
            "query_seconds": span([sum(q["elapsed_seconds"] for q in r["queries"]) for r in rows]),
            "corrections": sum(r["corrected"] for r in rows), "initial_wrong": sum(r["initial_wrong"] for r in rows),
            "resolved_initial_abstentions": resolved_abstentions,
            "corrected_initial_wrong_assertions": corrected_assertions,
            "regressions": sum(r["regressed"] for r in rows), "first_complete_answer_seconds": span(first_complete),
            "first_complete_rounds": first_complete_round, "first_complete_cli_estimate_usd": span(first_complete_cost),
            "final_trial_seconds": span([r["trial_seconds"] for r in rows]),
            "render_seconds_per_representation": span([r["render_seconds"] for r in rows]),
            "representation_bytes": {"structured": rows[0]["structured_bytes"], "png": rows[0]["image_bytes"]},
            "input_wire_bytes_all_calls": sum(c["input_wire_bytes"] for c in calls),
            "base64_seconds": sum(r["image_base64_seconds"] for r in rows),
            "serialization_seconds": sum(c["serialization_seconds"] for c in calls),
            "head_unchanged": all(r["head_unchanged"] for r in rows), "cost": call_cost(calls), "nonmatching_answers": failures,
        }
    return {"groups": result, "total": call_cost([c for r in trials for c in r["calls"]]),
            "trial_count": len(trials), "planned_primary_trials": 39}


def summarize_downstream(records, selection):
    groups = defaultdict(list)
    for record in records:
        groups[record["method"]].append(record)
    audited = {(row["task_id"], row["result"]["method"]): row for row in selection["rows"]}
    result = {}
    for method, rows in groups.items():
        scores = [(r, score_refs(r["call"]["parsed"] if r["call"]["provider_result"].get("subtype") == "success"
                                and r["call"]["exit_code"] == 0 else None, audited[(r["task_id"], method)])) for r in rows]
        with_edges = [s for r, s in scores if audited[(r["task_id"], method)]["gold"].get("relevant_edges")]
        result[method] = {"planned_calls": 15, "stored_calls": len(rows), "valid": sum(s["valid"] for r, s in scores),
            "supported_complete_entities": sum(s["supported_complete_entity_answer"] for r, s in scores),
            "supported_complete_edges": sum(s["supported_complete_edge_answer"] for r, s in scores),
            "unsupported_entity_refs": sum(len(s["unsupported_entity_refs"]) for r, s in scores),
            "unsupported_edge_refs": sum(len(s["unsupported_edge_refs"]) for r, s in scores),
            "entity_macro_precision": fmean(s["entity_precision"] for r, s in scores),
            "entity_macro_recall": fmean(s["entity_recall"] for r, s in scores),
            "nonempty_gold_edge_cases": len(with_edges), "nonempty_gold_edge_recall": fmean(s["edge_recall"] for s in with_edges),
            "cost": call_cost([r["call"] for r in rows]),
            "cases": [{"id": r["id"], **s} for r, s in scores]}
    return result
