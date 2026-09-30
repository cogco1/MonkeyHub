from datetime import datetime, timezone
import unittest

from monkeymonitor.server import _trace_contract
from monkeymonitor.trace import LANES, build_traces
from monkeymonitor.usage import TokenUsage


BASE = datetime(2026, 9, 14, tzinfo=timezone.utc)


def _stamp(milliseconds: int) -> str:
    return datetime.fromtimestamp(BASE.timestamp() + milliseconds / 1000, tz=timezone.utc).isoformat()


def _row(event_id: str, phase: str, offset: int, duration: int, *, source: str = "hub",
         parent: str | None = "hub:turn:t1", details: dict | None = None) -> dict:
    return {
        "event_id": event_id,
        "source": source,
        "provider": "none",
        "model": "none",
        "phase": phase,
        "status": "succeeded",
        "started_at": _stamp(offset),
        "ended_at": _stamp(offset + duration),
        "duration_ms": duration,
        "timing_scope": "service",
        "model_call": False,
        "tokens": TokenUsage().to_dict(),
        "project_id": "project-a",
        "run_id": None,
        "source_ref": None,
        "related_event_id": None,
        "session_id": "session-a",
        "parent_session_id": None,
        "turn_id": "t1",
        "operation_id": "t1",
        "parent_event_id": parent,
        "details": {"blocking": True, **(details or {})},
        "billing_mode": "unknown",
        "rate_snapshot": None,
        "rate_match_status": None,
    }


def _root(*, details: dict | None = None, duration: int = 100) -> dict:
    row = _row("hub:turn:t1", "hub_turn", 0, duration, parent=None, details=details)
    row["timing_scope"] = "agent_turn"
    return row


def _trace(rows: list[dict]) -> dict:
    return _trace_contract(build_traces(rows, now=BASE))["traces"][0]


class TurnTraceContractTests(unittest.TestCase):
    def test_one_trace_exposes_all_observed_lanes_and_span_identity(self):
        trace = _trace([
            _root(),
            _row("hub:provider:t1:1", "provider_round", 0, 40),
            _row("hub:tool:t1:1", "tool_call", 40, 10),
            _row("studio:proposal:t1", "proposal", 50, 20, source="studio", parent="hub:tool:t1:1"),
            _row("studio:cad:t1", "geometry_build", 70, 20, source="studio", parent="studio:proposal:t1"),
            _row("studio:client:t1", "model_install", 90, 10, source="studio"),
        ])

        self.assertEqual(trace["schema"], "TurnTrace@1")
        self.assertEqual(trace["trace_id"], "hub:turn:t1")
        self.assertEqual({span["lane"] for span in trace["spans"]}, {"agent", "hub", "studio", "cad", "client"})
        for span in trace["spans"]:
            self.assertEqual(span["trace_id"], trace["trace_id"])
            self.assertEqual(span["span_id"], span["event_id"])
            self.assertEqual(span["parent_span_id"], span["parent_event_id"])

        self.assertEqual(trace["coverage"]["blocking_ratio"], 1.0)
        self.assertEqual(trace["coverage"]["observed_blocking_ms"], 100)
        self.assertEqual(trace["coverage"]["tool_events"], "observed")
        self.assertEqual(
            {entry["lane"]: entry["status"] for entry in trace["coverage"]["lanes"]},
            {lane: "observed" for lane in ("agent", "hub", "studio", "cad", "client")},
        )

    def test_lane_vocabulary_comes_from_the_producer(self):
        trace = _trace([_root()])
        self.assertEqual([entry["lane"] for entry in trace["coverage"]["lanes"]],
                         [lane["id"] for lane in LANES])

    def test_zero_tools_is_observed_none_without_a_drop_notice(self):
        trace = _trace([_root()])
        self.assertEqual(trace["summary"]["tool_rounds"], 0)
        self.assertEqual(trace["coverage"]["tool_events"], "observed-none")

    def test_zero_tools_is_incomplete_after_a_dropped_observation(self):
        trace = _trace([_root(details={"missing_observations": True})])
        self.assertEqual(trace["summary"]["tool_rounds"], 0)
        self.assertEqual(trace["coverage"]["tool_events"], "incomplete")
        self.assertEqual(trace["coverage"]["blocking_ratio"], 0.0)

    def test_missing_tool_end_is_incomplete_even_without_a_drop_notice(self):
        tool = _row("tool", "tool_call", 20, 30)
        tool.update(status="running", ended_at=None, duration_ms=None)
        trace = _trace([_root(), tool])
        self.assertEqual(trace["coverage"]["tool_events"], "incomplete")
        self.assertEqual(trace["coverage"]["observed_blocking_ms"], 0)
        self.assertEqual(trace["coverage"]["outside_root_ms"], 0)
        self.assertIsNone(next(span for span in trace["spans"] if span["event_id"] == "tool")["duration_ms"])


class DroppedObservationTests(unittest.TestCase):
    """UsageLog stamps the sticky notice on the next stored event, so a drop
    lands on a sibling that is itself complete. Recorded tool rounds therefore
    never establish that every tool observation survived."""

    def test_recorded_tool_rounds_do_not_override_a_carried_drop(self):
        trace = _trace([
            _root(),
            _row("hub:tool:t1:1", "tool_call", 10, 10),
            _row("hub:tool:t1:2", "tool_call", 30, 10, details={"missing_observations": True}),
        ])
        self.assertEqual(trace["summary"]["tool_rounds"], 2)
        self.assertEqual(trace["coverage"]["tool_events"], "incomplete")

    def test_the_drop_reaches_the_projection_as_a_fact_not_a_message(self):
        trace = _trace([_root(), _row("hub:tool:t1:1", "tool_call", 10, 10,
                                      details={"missing_observations": True})])
        carried = [span for span in trace["spans"] if span["details"].get("missing_observations")]
        self.assertEqual([span["span_id"] for span in carried], ["hub:tool:t1:1"])
        self.assertIs(carried[0]["details"]["missing_observations"], True)
        self.assertEqual(trace["coverage"]["tool_events"], "incomplete")


class BlockingCoverageTests(unittest.TestCase):
    def test_a_zero_length_root_reports_no_ratio_rather_than_full_coverage(self):
        trace = _trace([_root(duration=0)])
        self.assertEqual(trace["summary"]["elapsed_ms"], 0)
        self.assertIsNone(trace["coverage"]["blocking_ratio"])
        self.assertEqual(trace["coverage"]["ratio_basis"], "unavailable")
        self.assertEqual(trace["coverage"]["basis"], "unavailable")

    def test_a_missing_root_interval_reports_no_ratio(self):
        trace = _trace([_row("studio:cad:t1", "geometry_build", 0, 20, source="studio", parent=None)])
        self.assertIsNone(trace["summary"]["elapsed_ms"])
        self.assertIsNone(trace["coverage"]["blocking_ratio"])
        self.assertIsNone(trace["coverage"]["observed_blocking_ms"])
        self.assertIsNone(trace["coverage"]["outside_root_ms"])

    def test_observed_blocking_is_the_producer_total_not_the_attributable_subset(self):
        # Two parallel blocking branches: the whole root was observed as
        # blocked, but no single branch can be named the waiting one.
        trace = _trace([
            _root(),
            _row("hub:tool:t1:1", "tool_call", 0, 100),
            _row("studio:cad:t1", "geometry_build", 0, 100, source="studio"),
        ])
        coverage = trace["coverage"]
        self.assertEqual(trace["summary"]["blocking_ms"], 100)
        self.assertEqual(coverage["observed_blocking_ms"], 100)
        self.assertEqual(coverage["attributed_blocking_ms"], 0)
        self.assertEqual(coverage["unattributed_ms"], 100)
        self.assertEqual(coverage["blocking_ratio"], 1.0)
        self.assertEqual(coverage["basis"], "unavailable")

    def test_nested_phases_are_counted_once(self):
        trace = _trace([
            _root(),
            _row("hub:tool:t1:1", "tool_call", 0, 100),
            _row("studio:cad:t1", "geometry_build", 20, 20, source="studio", parent="hub:tool:t1:1"),
        ])
        self.assertEqual(trace["coverage"]["observed_blocking_ms"], 100)
        self.assertEqual(trace["coverage"]["attributed_blocking_ms"], 100)
        self.assertEqual(trace["coverage"]["blocking_ratio"], 1.0)

    def test_phases_past_the_root_interval_are_reported_beside_the_ratio(self):
        trace = _trace([
            _root(),
            _row("hub:tool:t1:1", "tool_call", 0, 100),
            _row("studio:client:t1", "model_install", 100, 200, source="studio"),
        ])
        coverage = trace["coverage"]
        self.assertEqual(trace["summary"]["timeline_ms"], 300)
        self.assertEqual(coverage["root_elapsed_ms"], 100)
        self.assertEqual(coverage["outside_root_ms"], 200)
        # The ratio stays scoped to the root interval; the tail is stated, not
        # folded in and not silently treated as covered.
        self.assertEqual(coverage["ratio_basis"], "root_interval")
        self.assertEqual(coverage["blocking_ratio"], 1.0)
        self.assertTrue(any("超出聊天根区间" in warning for warning in trace["warnings"]))


if __name__ == "__main__":
    unittest.main()
