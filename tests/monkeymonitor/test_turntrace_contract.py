from datetime import datetime, timezone
import unittest

from monkeymonitor.server import _trace_contract
from monkeymonitor.trace import build_traces
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


def _root(*, details: dict | None = None) -> dict:
    row = _row("hub:turn:t1", "hub_turn", 0, 100, parent=None, details=details)
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

    def test_zero_tools_is_observed_none_without_a_drop_notice(self):
        trace = _trace([_root()])
        self.assertEqual(trace["summary"]["tool_rounds"], 0)
        self.assertEqual(trace["coverage"]["tool_events"], "observed-none")

    def test_zero_tools_is_incomplete_after_a_dropped_observation(self):
        trace = _trace([_root(details={"missing_observations": True})])
        self.assertEqual(trace["summary"]["tool_rounds"], 0)
        self.assertEqual(trace["coverage"]["tool_events"], "incomplete")
        self.assertEqual(trace["coverage"]["blocking_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
