"""Observable trace attribution, exact usage and shared diagnostic journal behavior."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote as encode
from urllib.request import urlopen

from monkeymonitor.pricing import RateCard, match_rate
from monkeymonitor.server import MonitorData, make_server
from monkeymonitor.store import UsageLog
from monkeymonitor.trace import build_traces
from monkeymonitor.usage import TokenUsage, UsageEvent


START = datetime(2026, 9, 13, tzinfo=timezone.utc)


def stamp(ms):
    return (START + timedelta(milliseconds=ms)).isoformat()


def event(key, phase, start, end=None, **kwargs):
    values = dict(event_id=key, source="hub", provider="unknown", model="unknown", phase=phase,
                  status="running" if end is None else "completed", started_at=stamp(start), ended_at=stamp(end) if end is not None else None,
                  duration_ms=end - start if end is not None else None, tokens=TokenUsage(), model_call=False,
                  project_id="project", session_id="native-session", turn_id="hub-turn", timing_scope="service", details={"blocking": True})
    values.update(kwargs)
    return UsageEvent(**values)


def trace(rows, **kwargs):
    return build_traces([row.to_dict() for row in rows], now=START + timedelta(seconds=20), **kwargs)


class TraceTests(unittest.TestCase):
    def test_complete_turn_uses_disjoint_spans_and_exact_native_usage(self):
        root = event("root", "hub_turn", 0, 10000, timing_scope="agent_turn")
        rows = [root,
                event("context", "context_build", 0, 1000, parent_event_id="root"),
                event("round1", "provider_round", 1000, 3000, parent_event_id="root", model_call=True,
                      details={"blocking": True, "provider_timing_basis": "agent_activity_interval"}),
                event("tool", "tool_call", 3000, 8000, parent_event_id="root", details={"blocking": True, "tool_name": "studio_request", "request_kind": "mutation"}),
                event("candidate", "candidate", 3500, 7500, source="studio", parent_event_id="tool"),
                event("cad", "geometry_build", 4000, 7000, source="studio", parent_event_id="candidate"),
                event("validation", "validation", 7000, 7500, source="studio", parent_event_id="candidate", details={"blocking": True, "validator_pass": True}),
                event("background", "model_projection", 6000, 9000, source="studio", parent_event_id="root", details={"blocking": False}),
                event("visible", "first_visible", 8000, 8000, parent_event_id="root"),
                event("round2", "provider_round", 8000, 10000, parent_event_id="root", model_call=True),
                event("native1", "agent", 2999, 2999, source="codex", model_call=True, turn_id="native-turn",
                      tokens=TokenUsage(100, 10, 20, 0, 0, 5), billing_mode="subscription_equivalent"),
                event("native2", "agent", 9999, 9999, source="codex", model_call=True, turn_id="native-turn",
                      tokens=TokenUsage(150, 20, 50, 0, 0, 10), billing_mode="subscription_equivalent")]
        result = trace(rows)
        self.assertEqual(len(result["traces"]), 1)
        actual = result["traces"][0]
        self.assertEqual(actual["summary"]["elapsed_ms"], 10000)
        self.assertEqual(actual["summary"]["first_visible_ms"], 8000)
        self.assertEqual(actual["summary"]["verified_ms"], 7500)
        self.assertEqual(actual["summary"]["provider_rounds"], 2)
        self.assertIsNone(actual["summary"]["model_rounds"])
        self.assertEqual(actual["summary"]["usage_events"], 2)
        self.assertEqual(actual["summary"]["agent_resumes"], 1)
        self.assertEqual(actual["summary"]["background_ms"], 3000)
        self.assertEqual(sum(row["duration_ms"] for row in actual["attribution"]), 10000)
        self.assertEqual(actual["critical_path"]["duration_ms"], 10000)
        self.assertEqual(actual["usage"]["tokens"]["input_tokens"], 250)
        self.assertEqual(set(actual["usage"]["excluded_duplicate_event_ids"]), {"round1", "round2"})
        self.assertIsNone(actual["price"]["amount_usd"])
        self.assertIn("非实际扣款", actual["price"]["label"])
        self.assertEqual({row["lane"] for row in actual["spans"]}, {"agent", "hub", "studio", "cad", "client"})

    def test_unknown_and_parallel_spans_do_not_claim_a_complete_critical_path(self):
        rows = [event("root", "hub_turn", 0, 1000),
                event("a", "tool_call", 0, 600, parent_event_id="root"),
                event("b", "tool_call", 200, 800, parent_event_id="root"),
                event("unknown", "model_projection", 800, 1000, details={})]
        actual = trace(rows)["traces"][0]
        self.assertEqual(actual["summary"]["blocking_ms"], 800)
        self.assertEqual(actual["critical_path"]["duration_ms"], 400)
        self.assertEqual(actual["summary"]["unattributed_ms"], 600)
        self.assertIn("并行阻塞", actual["critical_path"]["note"])
        self.assertIsNone(actual["usage"]["tokens"]["input_tokens"])

    def test_running_root_advances_but_missing_model_time_never_becomes_duration(self):
        rows = [event("root", "hub_turn", 0),
                event("round", "provider_round", 1000, model_call=True, parent_event_id="root"),
                event("tokens", "agent", 2000, 2000, source="codex", turn_id="native", model_call=True,
                      duration_ms=None, ended_at=None, tokens=TokenUsage(20, None), status="observed")]
        actual = trace(rows)["traces"][0]
        self.assertEqual(actual["summary"]["elapsed_ms"], 20000)
        self.assertEqual(next(row for row in actual["spans"] if row["event_id"] == "round")["duration_ms"], 19000)
        self.assertIsNone(next(row for row in actual["spans"] if row["event_id"] == "tokens")["duration_ms"])
        self.assertEqual(actual["usage"]["tokens"]["input_tokens"], 20)
        self.assertIsNone(actual["usage"]["tokens"]["output_tokens"])

    def test_completed_root_does_not_grow_unfinished_child_or_invent_its_end(self):
        rows = [event("root", "hub_turn", 0, 1000),
                event("tool", "tool_call", 200, parent_event_id="root"),
                event("request", "api_request", 300, source="studio", parent_event_id="tool"),
                event("finished", "geometry_build", 400, 600, source="studio", parent_event_id="request")]
        first = trace(rows)["traces"][0]
        later = build_traces([row.to_dict() for row in rows], now=START + timedelta(days=1))["traces"][0]
        self.assertEqual(first["summary"], later["summary"])
        self.assertEqual(first["summary"]["timeline_ms"], 1000)
        self.assertEqual(first["summary"]["blocking_ms"], 200)
        self.assertEqual(first["summary"]["unattributed_ms"], 800)
        for span in first["spans"]:
            if span["event_id"] in {"tool", "request"}:
                self.assertEqual(span["status"], "incomplete")
                self.assertIsNone(span["duration_ms"])
                self.assertIsNone(span["ended_at"])
        self.assertTrue(any("未观测到结束" in warning for warning in first["warnings"]))
        self.assertEqual(rows[1].status, "running", "the retained observation was not rewritten")

    def test_late_child_completion_replaces_unknown_with_its_observed_interval(self):
        root = event("root", "hub_turn", 0, 1000)
        child = event("background", "model_projection", 800, source="studio", parent_event_id="root")
        self.assertEqual(trace([root, child])["traces"][0]["spans"][1]["status"], "incomplete")
        finished = replace(child, status="succeeded", ended_at=stamp(1500), duration_ms=700)
        actual = trace([root, child, finished])["traces"][0]
        self.assertEqual(actual["summary"]["elapsed_ms"], 1000)
        self.assertEqual(actual["summary"]["timeline_ms"], 1500)
        self.assertEqual(actual["spans"][1]["status"], "succeeded")
        self.assertEqual(actual["spans"][1]["duration_ms"], 700)

    def test_host_activity_and_usage_metadata_do_not_establish_model_request_count(self):
        rows = [event("root", "hub_turn", 0, 4000),
                event("first", "provider_round", 0, 1000),
                event("second", "provider_round", 2000, 3000),
                event("unknown-call", "model_usage", 3100, 3500, model_call=True)]
        actual = trace(rows)["traces"][0]
        self.assertEqual(actual["summary"]["provider_rounds"], 2)
        self.assertIsNone(actual["summary"]["model_rounds"])
        self.assertEqual(actual["summary"]["agent_resumes"], 1)
        self.assertNotIn("model_wakeups", actual["summary"])
        diagnostic = next(row for row in actual["diagnostics"] if row["code"] == "agent_resumes")
        self.assertIn("不能据此证明", diagnostic["note"])
        zero_usage = event("reported", "model_usage", 3500, 3500, model_call=True, tokens=TokenUsage(0, 0))
        actual = trace(rows + [zero_usage])["traces"][0]
        self.assertIsNone(actual["summary"]["model_rounds"])
        self.assertEqual(actual["summary"]["usage_events"], 2)

    def test_explicit_request_boundary_counts_even_when_its_usage_is_unknown(self):
        rows = [event("root", "hub_turn", 0, 1000),
                event("request", "model_request", 100, 900, source="studio", timing_scope="model_call", model_call=True)]
        actual = trace(rows)["traces"][0]
        self.assertEqual(actual["summary"]["model_rounds"], 1)
        self.assertEqual(actual["summary"]["usage_events"], 1)
        self.assertIsNone(actual["usage"]["tokens"]["input_tokens"])

    def test_unbound_native_usage_keeps_total_unknown_despite_a_known_studio_request(self):
        root = event("root", "hub_turn", 0, 2000)
        request = event("request", "model_request", 1000, 1900, source="studio", timing_scope="model_call", model_call=True,
                        tokens=TokenUsage(10, 2))
        native = event("native", "agent", 900, 900, source="codex", model_call=True, turn_id="native-turn", tokens=TokenUsage(100, 20))
        actual = trace([root, request, native])["traces"][0]
        self.assertIsNone(actual["summary"]["model_rounds"])
        self.assertEqual(actual["summary"]["usage_events"], 2)
        self.assertEqual(actual["usage"]["tokens"]["input_tokens"], 110)

    def test_multiple_usage_updates_linked_to_one_explicit_request_are_one_request(self):
        rows = [event("root", "hub_turn", 0, 2000),
                event("request", "model_request", 100, 1900, source="studio", timing_scope="model_call", model_call=True)]
        rows.extend(event(f"native-{index}", "agent", 200 + index * 100, 200 + index * 100, source="codex", model_call=True,
                          turn_id="native-turn", related_event_id="request", tokens=TokenUsage(10, 2)) for index in range(5))
        actual = trace(rows)["traces"][0]
        self.assertEqual(actual["summary"]["model_rounds"], 1)
        self.assertEqual(actual["summary"]["usage_events"], 6)

    def test_first_response_is_distinct_from_successful_browser_first_frame(self):
        rows = [event("root", "hub_turn", 0, 1000),
                event("text", "first_response", 200, 200),
                event("failed-preview", "model_projection", 300, 400, source="studio", status="failed"),
                event("running-preview", "model_projection", 450, source="studio")]
        actual = trace(rows)["traces"][0]
        self.assertEqual(actual["summary"]["first_response_ms"], 200)
        self.assertIsNone(actual["summary"]["first_visible_ms"])
        visible = event("preview", "model_projection", 600, 950, source="studio", status="succeeded", duration_ms=300)
        actual = trace(rows + [visible])["traces"][0]
        self.assertEqual(actual["summary"]["first_response_ms"], 200)
        self.assertEqual(actual["summary"]["first_visible_ms"], 900)
        actual = trace(rows + [visible, event("paint-marker", "first_visible", 700, 700)])["traces"][0]
        self.assertEqual(actual["summary"]["first_visible_ms"], 700)

    def test_first_candidate_is_successful_object_readback_not_job_or_compare_success(self):
        rows = [event("root", "hub_turn", 0, 2000),
                event("job", "candidate", 100, 400, source="studio", run_id="candidate-1", status="succeeded"),
                event("partial", "candidate_readback", 400, 500, source="studio", run_id="candidate-1",
                      status="succeeded", details={"success": False}),
                event("read", "candidate_readback", 600, 900, source="studio", run_id="candidate-1",
                      status="succeeded", details={"success": True}),
                event("compare", "api_request", 600, 1000, source="studio", status="failed"),
                event("read-again", "candidate_readback", 1200, 1400, source="studio", run_id="candidate-1",
                      status="succeeded", details={"success": True})]
        actual = trace(rows)["traces"][0]
        self.assertEqual(actual["summary"]["first_candidate_ms"], 900)
        self.assertIsNone(actual["summary"]["verified_ms"])
        self.assertIsNone(actual["summary"]["first_visible_ms"])
        self.assertIsNone(trace(rows[:3])["traces"][0]["summary"]["first_candidate_ms"])

    def test_first_candidate_stays_unknown_without_complete_bound_readback(self):
        root = event("root", "hub_turn", 0, 2000)
        generated = event("generated", "candidate", 100, 400, source="studio", run_id="candidate-1", status="succeeded")
        read = event("read", "candidate_readback", 500, 900, source="studio", run_id="candidate-1",
                     status="succeeded", details={"success": True})
        for fields in ({"status": "failed"}, {"details": {"success": False}}, {"details": {}},
                       {"ended_at": None, "duration_ms": None}, {"ended_at": None},
                       {"run_id": None}, {"source": "hub"}, {"phase": "candidate"}):
            with self.subTest(fields=fields):
                actual = trace([root, generated, replace(read, **fields)])["traces"][0]
                self.assertIsNone(actual["summary"]["first_candidate_ms"])
        self.assertIsNone(trace([read])["traces"][0]["summary"]["first_candidate_ms"])

    def test_first_candidate_ignores_old_input_readback_before_new_result(self):
        rows = [event("root", "hub_turn", 0, 10000),
                event("old-read", "candidate_readback", 100, 250, source="studio", run_id="old-input",
                      status="succeeded", details={"success": True}),
                event("generated", "candidate", 1000, 7000, source="studio", run_id="new-result", status="succeeded"),
                event("new-read", "candidate_readback", 7000, 7500, source="studio", run_id="new-result",
                      status="succeeded", details={"success": True}),
                event("compare", "api_request", 7000, 8000, source="studio", status="failed")]
        self.assertEqual(trace(rows)["traces"][0]["summary"]["first_candidate_ms"], 7500)
        self.assertIsNone(trace(rows[:2])["traces"][0]["summary"]["first_candidate_ms"])

    def test_first_candidate_requires_complete_successful_same_turn_generation(self):
        root = event("root", "hub_turn", 0, 10000)
        generated = event("generated", "candidate", 1000, 7000, source="studio", run_id="new-result", status="succeeded")
        read = event("read", "candidate_readback", 7000, 7500, source="studio", run_id="new-result",
                     status="succeeded", details={"success": True})
        for fields in ({"run_id": "different-result"}, {"run_id": None}, {"source": "hub"},
                       {"phase": "candidate_queue"}, {"status": "failed"}, {"status": "running"},
                       {"ended_at": None, "duration_ms": None}, {"ended_at": None},
                       {"started_at": stamp(-100)},
                       {"started_at": stamp(-100), "ended_at": stamp(-50)},
                       {"ended_at": stamp(8000), "duration_ms": 7000}):
            with self.subTest(fields=fields):
                actual = trace([root, replace(generated, **fields), read])["traces"][0]
                self.assertIsNone(actual["summary"]["first_candidate_ms"])
        self.assertIsNone(trace([root, read])["traces"][0]["summary"]["first_candidate_ms"])
        self.assertIsNone(trace([root, generated])["traces"][0]["summary"]["first_candidate_ms"])

    def test_native_time_binding_requires_unique_exact_session_and_never_expands_window(self):
        first = event("first", "hub_turn", 0, 1000, turn_id="hub-1")
        second = event("second", "hub_turn", 500, 1500, turn_id="hub-2")
        usage = event("native", "agent", 750, 750, source="codex", turn_id="native", model_call=True, tokens=TokenUsage(10, 1))
        ambiguous = trace([first, second, usage])
        self.assertEqual(len(ambiguous["traces"]), 3)
        self.assertTrue(ambiguous["warnings"])
        unrelated = trace([first, replace(usage, session_id="another")])
        self.assertEqual(len(unrelated["traces"]), 2)
        late = trace([first, replace(usage, started_at=stamp(1001), ended_at=stamp(1001))])
        self.assertEqual(len(late["traces"]), 2)
        explicit = trace([replace(first, details={"native_turn_id": "native"}), second, usage])
        self.assertEqual(len(explicit["traces"]), 2)
        self.assertEqual(next(row for row in explicit["traces"] if row["trace_id"] == "first")["usage"]["tokens"]["input_tokens"], 10)

    def test_linked_client_event_inherits_turn_and_export_drops_private_references(self):
        rows = [event("root", "hub_turn", 0, 5000),
                event("candidate", "candidate", 1000, 3000, source="studio", parent_event_id="root", run_id="candidate-run"),
                event("client", "model_install", 3000, 4000, source="studio", turn_id=None, operation_id="browser-op",
                      related_event_id="candidate", source_ref="C:/private/design/model.3dm", details={"output_refs": ["C:/private/design/result.json"],
                      "input_identity": {"source_ref": "private prompt body", "program_digest": "a" * 64}})]
        result = trace(rows)
        self.assertEqual(len(result["traces"]), 1)
        client = next(row for row in result["traces"][0]["spans"] if row["event_id"] == "client")
        self.assertEqual(client["parent_event_id"], "candidate")
        self.assertEqual(client["operation_id"], "browser-op")
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(client["details"]["input_identity"], {"program_digest": "a" * 64})

    def test_cross_project_parent_and_related_links_cannot_bypass_turn_project_binding(self):
        root = event("root", "hub_turn", 0, 2000, project_id="project-a")
        unknown_project = event("bridge", "api_request", 100, 1500, project_id=None, parent_event_id="root")
        for link in ("parent_event_id", "related_event_id"):
            with self.subTest(link=link):
                rows = [root, unknown_project,
                        event("foreign-direct", "model_projection", 500, 900, source="studio", project_id="project-b", status="succeeded", **{link: "root"}),
                        event("foreign-indirect", "model_projection", 1000, 1200, source="studio", project_id="project-c", status="succeeded", **{link: "bridge"})]
                result = trace(rows)
                self.assertEqual(len(result["traces"]), 3)
                owner = next(item for item in result["traces"] if item["trace_id"] == "root")
                self.assertEqual({span["event_id"] for span in owner["spans"]}, {"root", "bridge"})
                self.assertIsNone(owner["summary"]["first_visible_ms"])
                self.assertEqual({item["project_id"] for item in result["traces"]}, {"project-a", "project-b", "project-c"})
                self.assertTrue(any("跨项目" in warning for warning in result["warnings"]))

    def test_diagnostics_count_observed_repetition_without_avoidable_claim(self):
        root = event("root", "hub_turn", 0, 4000)
        query = event("schema1", "tool_call", 0, 1000, details={"tool_name": "studio_schema", "request_kind": "schema_read", "input_identity": {"context_digest": "a" * 64}})
        rows = [root, query, replace(query, event_id="schema2", started_at=stamp(1000), ended_at=stamp(2000)),
                event("retry", "tool_call", 2000, 3000, details={"retry_attempt": 1, "retry_reason": "timeout"})]
        diagnostics = {row["code"]: row for row in trace(rows)["traces"][0]["diagnostics"]}
        self.assertEqual(diagnostics["schema_read"]["count"], 1)
        self.assertEqual(diagnostics["retries"]["count"], 1)
        self.assertIn("不能据此断言", diagnostics["repeated_tools"]["note"])

    def test_same_category_with_different_arguments_is_not_duplicate_schema(self):
        rows = [event("root", "hub_turn", 0, 2000),
                event("a", "tool_call", 0, 1000, details={"request_kind": "schema_read", "input_identity": {"context_digest": "a" * 64}}),
                event("b", "tool_call", 1000, 2000, details={"request_kind": "schema_read", "input_identity": {"context_digest": "b" * 64}})]
        self.assertNotIn("schema_read", {item["code"] for item in trace(rows)["traces"][0]["diagnostics"]})

    def test_monotonic_duration_survives_clock_jump_and_client_tail_stays_on_timeline(self):
        rows = [event("root", "hub_turn", 0, 10000, duration_ms=1000),
                event("tool", "tool_call", 100, 9000, duration_ms=800, parent_event_id="root"),
                event("client", "model_install", 900, 1500, source="studio", parent_event_id="tool", details={"blocking": False})]
        actual = trace(rows)["traces"][0]
        self.assertEqual(actual["summary"]["elapsed_ms"], 1000)
        self.assertEqual(actual["summary"]["timeline_ms"], 1500)
        self.assertEqual(next(row for row in actual["spans"] if row["event_id"] == "tool")["duration_ms"], 800)
        self.assertIn("单调时钟", "".join(actual["warnings"]))

    def test_parallel_deeper_branch_does_not_erase_an_unrelated_blocking_sibling(self):
        rows = [event("root", "hub_turn", 0, 1000),
                event("tool-a", "tool_call", 0, 1000, parent_event_id="root"),
                event("tool-b", "tool_call", 0, 1000, parent_event_id="root"),
                event("cad", "geometry_build", 0, 1000, source="studio", parent_event_id="tool-b")]
        actual = trace(rows)["traces"][0]
        self.assertEqual(actual["critical_path"]["segments"], [])
        self.assertEqual(actual["summary"]["unattributed_ms"], 1000)


class HistoricalPricingTests(unittest.TestCase):
    def rate(self, **kwargs):
        values = dict(provider="test", model="exact", billing_plan="api-standard", input="2", cached_input="1", output="10",
                      effective_date="2026-09-01", source_url="https://provider.example/pricing")
        values.update(kwargs)
        return RateCard(**values)

    def test_exact_plan_date_and_unambiguous_sourced_rate_match(self):
        rate = self.rate()
        self.assertEqual(match_rate("test", "exact", "api-standard", stamp(0), (rate,)), (rate, "matched"))
        for provider, model, plan in (("alias", "exact", "api-standard"), ("test", "alias", "api-standard"), ("test", "exact", "subscription")):
            self.assertEqual(match_rate(provider, model, plan, stamp(0), (rate,))[1], "not_found")
        self.assertEqual(match_rate("test", "exact", None, stamp(0), (rate,))[1], "missing_identity")
        self.assertEqual(match_rate("test", "exact", "api-standard", stamp(0), (rate, replace(rate, input="3")))[1], "ambiguous")
        for invalid in (replace(rate, effective_date="2026-10-01"), replace(rate, source_url=None)):
            self.assertEqual(match_rate("test", "exact", "api-standard", stamp(0), (invalid,))[1], "not_found")

    def test_persisted_rate_snapshot_survives_catalog_change_and_unknown_stays_unknown(self):
        with TemporaryDirectory() as directory:
            store = UsageLog(Path(directory))
            sample = event("priced", "provider_round", 0, 1000, provider="test", model="exact", model_call=True,
                           tokens=TokenUsage(1000, 100, 400, 0, 0, 0), billing_mode="subscription_equivalent", details={"billing_plan": "api-standard"})
            with patch("monkeymonitor.store.load_rates", return_value=(self.rate(),)):
                store.append(sample)
            with patch("monkeymonitor.store.load_rates", return_value=()):
                store.append(replace(sample, event_id="unknown"))
            rows, warnings = store.read()
            self.assertFalse(warnings)
            result = trace(rows, rates=(self.rate(input="900"),))["traces"][0]
            self.assertEqual(result["price"]["known_subtotal_usd"], "0.0026")
            self.assertIsNone(result["price"]["amount_usd"])
            self.assertEqual({row["basis"] for row in result["price"]["rate_snapshots"]}, {"recorded_snapshot"})
            self.assertEqual(rows[0].rate_snapshot["input"], "2")
            self.assertEqual(rows[1].rate_match_status, "not_found")

    def test_private_or_undated_rate_source_is_not_a_valid_historical_snapshot(self):
        for source in ("file:///C:/private/price.json", "https://user:secret@provider.example/pricing", None):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "dated public source"):
                event("invalid", "model_usage", 0, 1, provider="test", model="exact", model_call=True,
                      details={"billing_plan": "api-standard"}, rate_snapshot=self.rate(source_url=source).to_dict(), rate_match_status="matched")

    def test_catalog_failure_never_discards_provider_usage(self):
        with TemporaryDirectory() as directory, patch("monkeymonitor.store.load_rates", side_effect=ValueError("invalid catalog")):
            store = UsageLog(Path(directory))
            store.append(event("usage", "model_usage", 0, 1, provider="test", model="exact", model_call=True,
                               tokens=TokenUsage(100, 20), details={"billing_plan": "api-standard"}))
            retained = store.read()[0][0]
            self.assertEqual(retained.tokens.input_tokens, 100)
            self.assertEqual(retained.rate_match_status, "not_found")


class JournalTests(unittest.TestCase):
    def test_retained_segments_keep_a_live_span_and_final_revision_and_read_legacy(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            store = UsageLog(path, max_bytes=2400, backups=2)
            root = event("root", "hub_turn", 0)
            store.append(root)
            for index in range(6):
                store.append(event(f"done-{index}", "tool_call", index, index + 1))
            rows, warnings = store.read()
            self.assertTrue(any("轮转" in warning for warning in warnings))
            self.assertEqual(next(row for row in rows if row.event_id == "root").status, "running")
            self.assertLessEqual(len(list(path.glob("usage*.jsonl"))), 3)
            store.append(replace(root, status="completed", ended_at=stamp(100), duration_ms=100))
            self.assertEqual(next(row for row in store.read()[0] if row.event_id == "root").status, "completed")
            self.assertLess(len(store.read()[0]), 16)
        with TemporaryDirectory() as directory:
            path = Path(directory)
            legacy = event("legacy", "agent", 0, 1).to_dict()
            for key in ("rate_snapshot", "rate_match_status"):
                legacy.pop(key)
            (path / "usage.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
            self.assertEqual(UsageLog(path).read()[0][0].event_id, "legacy")
            self.assertFalse((path / "usage.lock").exists(), "read-only legacy access creates no new file")

    def test_skipped_observations_name_the_writer_not_this_turn_and_carry_no_count(self):
        rows = [event("root", "hub_turn", 0, 10000, timing_scope="agent_turn"),
                event("tool", "tool_call", 1000, 2000, parent_event_id="root",
                      details={"blocking": True, "missing_observations": True})]
        result = trace(rows)["traces"][0]
        notice = next(warning for warning in result["warnings"] if "跳过" in warning)
        self.assertIn("写入方", notice)
        self.assertIn("未知", notice)
        self.assertNotRegex(notice, r"\d", "the notice never states a count or a duration")
        # The carrier is only where the notice was stored, not the loss itself.
        self.assertEqual(result["summary"]["elapsed_ms"], 10000)
        self.assertIsNotNone(next(span for span in result["spans"] if span["event_id"] == "tool")["duration_ms"])
        self.assertFalse(trace(rows[:1])["traces"][0]["warnings"])

    def test_multiple_processes_append_and_rotate_one_journal_without_corruption(self):
        with TemporaryDirectory() as directory:
            script = """import sys
from pathlib import Path
from monkeymonitor.store import UsageLog
from monkeymonitor.usage import UsageEvent, TokenUsage
store=UsageLog(Path(sys.argv[1]),max_bytes=12000,backups=3)
for index in range(25):
    store.append(UsageEvent(sys.argv[2]+str(index),'hub','none','none','tool_call','completed','2026-09-13T00:00:00Z',TokenUsage(),model_call=False))
"""
            processes = [subprocess.Popen([sys.executable, "-c", script, directory, f"writer-{index}-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for index in range(4)]
            for process in processes:
                _, error = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, error.decode())
            rows, warnings = UsageLog(Path(directory)).read()
            self.assertTrue(any("轮转" in warning for warning in warnings))
            self.assertTrue(rows)
            self.assertEqual(len(rows), len({row.event_id for row in rows}))
            self.assertLessEqual(len(list(Path(directory).glob("usage*.jsonl"))), 4)


class TraceHTTPTests(unittest.TestCase):
    def test_real_endpoint_download_and_loopback_boundary(self):
        with TemporaryDirectory() as directory:
            store = UsageLog(Path(directory))
            store.append(event("root", "hub_turn", 0, 1000))
            server = make_server(MonitorData(Path(directory)), 0)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(base + "/api/traces") as response:
                    body = json.load(response)
                self.assertEqual(body["traces"][0]["summary"]["elapsed_ms"], 1000)
                with urlopen(base + "/api/traces/export?trace_id=" + encode("root")) as response:
                    self.assertIn("attachment", response.headers["Content-Disposition"])
                    self.assertEqual(json.load(response), body["traces"][0])
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + "/api/traces/export?trace_id=missing")
                self.assertEqual(error.exception.code, 404)
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + "/api/traces/export?trace_id=root&path=private")
                self.assertEqual(error.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == "__main__":
    unittest.main()
