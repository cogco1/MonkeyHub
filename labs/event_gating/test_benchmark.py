from copy import deepcopy

from monkeymonitor.store import UsageLog

from labs.event_gating.benchmark import evaluate, main_blocks, public_state, run_task, summarize, workload_totals, jev_cost
from labs.event_gating.test_gate import observation


class Consumer:
    def __init__(self):
        self.calls = []

    def call(self, blocks, *, call_id):
        self.calls.append(blocks)
        return {"parsed": {"action": "review", "source": observation()["current_source"]},
                "exit_code": 0, "timeout": False, "provider_result": {}, "cli_wall_seconds": .01,
                "input_wire_bytes": 100, "monitor_operation_id": call_id}


def task():
    row = observation()
    row.update(id="one", expected_action="continue", meaningful=False)
    return {"id": "task", "split": "dev", "checkpoints": [row]}


def test_eager_invokes_provider_and_deterministic_ordinary_refresh_does_not(tmp_path):
    consumer = Consumer()
    eager = run_task(task(), "A", 0, consumer, UsageLog(tmp_path))
    gated = run_task(task(), "B", 0, consumer, UsageLog(tmp_path))
    assert len(consumer.calls) == 1
    assert not eager["completed"]  # Malclassified status update is an observed failure.
    assert gated["completed"]
    rows = summarize([eager, gated])
    assert rows[0]["main_api_equivalent_usd"] is None  # Missing is not free.
    assert rows[1]["main_wakes"] == 0


def test_labels_and_gate_results_never_enter_provider_context():
    row = task()["checkpoints"][0]
    row["events"] = ["SECRET_ORACLE"]
    row["raw_queries"] = {"meaningful": "SECRET_ORACLE"}
    assert "SECRET_ORACLE" not in str(main_blocks(row))
    assert "expected_action" not in public_state(row)
    assert "meaningful" not in public_state(row)


def test_invalid_main_result_and_missing_visual_evidence_fail():
    row = task()["checkpoints"][0]
    call = {"parsed": {"action": "continue", "source": row["current_source"]},
            "exit_code": 0, "timeout": True, "provider_result": {}}
    assert not evaluate(row, call, skipped=False)["correct_disposition"]
    row.update(requires_visual=True, expected_action="visual-review", meaningful=True)
    call.update(timeout=False, parsed={"action": "visual-review", "source": row["current_source"],
                                     "observation": "A claim without a supplied image."})
    assert not evaluate(row, call, skipped=False)["correct_disposition"]
    assert evaluate(row, None, skipped=True)["missed_meaningful_event"]
    call["parsed"]["action"] = "continue"
    score = evaluate(row, call, skipped=False)
    assert score["failed_meaningful_disposition"]
    assert not score["missed_meaningful_event"]  # Gate woke; the model failed the disposition.


def test_jev_error_falls_back_and_hard_events_bypass_jev(tmp_path):
    class BrokenJev:
        def __init__(self):
            self.calls = 0

        def call(self, state, *, call_id):
            self.calls += 1
            return {"parsed": None, "status": "timeout", "wall_seconds": .1, "monitor_operation_id": call_id}

    source = task()
    point = source["checkpoints"][0]
    point.update(semantic_text="是否需要重新推敲？", meaningful=True, expected_action="review")
    hard = deepcopy(point)
    hard["id"] = "hard"
    hard["after"]["hard_valid"] = False
    hard["expected_action"] = "revise"
    source["checkpoints"].append(hard)
    jev, consumer = BrokenJev(), Consumer()
    result = run_task(source, "C", 0, consumer, UsageLog(tmp_path), jev=jev, threshold=.9)
    assert jev.calls == 1
    assert len(consumer.calls) == 2
    assert result["checkpoints"][0]["fallback"]
    assert result["checkpoints"][1]["jev_call"] is None


def test_workload_costs_keep_holdout_separate_and_charge_calibration(tmp_path):
    rows = []
    for split, condition in (("dev", "A"), ("holdout", "A"), ("holdout", "C")):
        source = task()
        source.update(id=split, split=split)
        row = run_task(source, condition, 0, Consumer(), UsageLog(tmp_path))
        row["wall_seconds"] = 10
        for point in row["checkpoints"]:
            if point["main_call"]:
                point["main_call"]["cli_api_equivalent_usd"] = 1
        rows.append(row)
    calibration = [{"call": {"wall_seconds": 2, "usage": {"input_tokens": 1000}}}]
    totals = workload_totals(rows, 3, calibration)
    by_id = {(row["split"], row["condition"]): row for row in totals}
    assert by_id["holdout", "A"]["replay_seconds"] == 10
    assert by_id["holdout", "C"]["prepared_workload_seconds"] == 15
    assert by_id["holdout", "A"]["task_ids"] == by_id["holdout", "C"]["task_ids"]
    assert by_id["holdout", "C"]["api_equivalent_usd_including_calibration"] == .000042
    assert jev_cost({"usage": {}}) is None
