from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from monkeymonitor.algorithms import (
    Action, Budget, Decision, FirstAvailablePolicy, SelectionContext, choose_action,
)
from monkeymonitor.codex import bound_codex_sources, iter_codex_events
from monkeymonitor.pricing import RateCard, quote
from monkeymonitor.usage import TokenUsage, UsageEvent


class UsagePricingTests(unittest.TestCase):
    def test_disjoint_cache_and_reasoning_counts(self):
        usage = TokenUsage(1000, 200, 400, 300, 100, 150)
        rate = RateCard("test", "exact-model", "2", "0.2", "2.5", "4", "10")
        result = quote(usage, rate)
        self.assertEqual(result["amount_usd"], "0.00358")
        self.assertEqual(result["missing"], [])
        self.assertEqual(quote(TokenUsage(1000, 200, 400, 300, 100, 0), rate), result)

    def test_missing_totals_or_rate_remain_unpriced(self):
        rate = RateCard("test", "exact-model", input="2", output="10")
        result = quote(TokenUsage(None, 200), rate)
        self.assertIsNone(result["amount_usd"])
        self.assertEqual(result["known_subtotal_usd"], "0.002")
        self.assertIn("tokens.input", result["missing"])
        self.assertIsNone(quote(TokenUsage(10, 2), None)["amount_usd"])
        self.assertIn("rate.cached_input", quote(TokenUsage(10, 2, 3), rate)["missing"])

    def test_unknown_cache_partition_is_not_charged_as_ordinary_input(self):
        result = quote(TokenUsage(1000, 0, None), RateCard("test", "model", input="1"))
        self.assertIsNone(result["amount_usd"])
        self.assertEqual(result["known_subtotal_usd"], "0")

    def test_omitted_cache_counts_remain_unknown(self):
        usage = TokenUsage.from_dict({"input_tokens": 1000, "output_tokens": 20})
        self.assertIsNone(usage.cached_input_tokens)
        self.assertIsNone(usage.cache_write_input_tokens)
        self.assertIsNone(usage.cache_write_1h_input_tokens)
        result = quote(usage, RateCard("test", "model", input="1", output="2"))
        self.assertIsNone(result["amount_usd"])
        self.assertEqual(result["known_subtotal_usd"], "0.00004")

    def test_invalid_counts_and_subsets_are_rejected(self):
        for value in (-1, True, 1.2, "2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TokenUsage(input_tokens=value)
        for kwargs in (
            {"input_tokens": 10, "cached_input_tokens": 11},
            {"input_tokens": 10, "cached_input_tokens": 6, "cache_write_input_tokens": 5},
            {"cache_write_input_tokens": 1, "cache_write_1h_input_tokens": 2},
            {"input_tokens": 10, "cached_input_tokens": 6, "cache_write_1h_input_tokens": 5},
            {"output_tokens": 1, "reasoning_output_tokens": 2},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TokenUsage(**kwargs)

    def test_rates_require_finite_nonnegative_decimal_strings(self):
        for value in (True, 2, 0.5, "-1", "NaN", "Infinity", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RateCard("test", "model", input=value)

    def test_content_free_event_roundtrip_preserves_unknowns(self):
        event = UsageEvent("call-1", "studio", "test", "model", "intent", "failed",
                           "2026-09-09T12:00:00Z", TokenUsage(), "api_estimate")
        payload = json.loads(json.dumps(event.to_dict()))
        self.assertIsNone(payload["tokens"]["input_tokens"])
        self.assertEqual(UsageEvent.from_dict(payload), event)
        self.assertNotIn("prompt", payload)

    def test_retained_event_without_timing_fields_remains_readable(self):
        old_row = {
            "event_id": "retained-call", "source": "studio", "provider": "test",
            "model": "model", "phase": "intent", "status": "completed",
            "started_at": "2026-09-09T12:00:00Z", "tokens": counts(10, 2),
            "billing_mode": "api_estimate", "duration_ms": 200, "project_id": "project",
        }
        old_row["tokens"].pop("total_tokens")
        event = UsageEvent.from_dict(old_row)
        self.assertEqual(event.duration_ms, 200)
        self.assertEqual(event.timing_scope, "unknown")
        for name in ("ended_at", "model_call", "source_ref", "related_event_id",
                     "session_id", "parent_session_id", "turn_id"):
            self.assertIsNone(getattr(event, name))
        self.assertEqual(UsageEvent.from_dict(event.to_dict()), event)

    def test_non_model_phase_rejects_even_zero_provider_counters(self):
        base = dict(event_id="service", source="studio", provider="unknown", model="unknown",
                    phase="preview", status="completed", started_at="2026-09-09T12:00:00Z",
                    tokens=TokenUsage(None, None, None, None, None, None), billing_mode="unknown",
                    model_call=False, timing_scope="service")
        self.assertFalse(UsageEvent(**base).model_call)
        for name in base["tokens"].to_dict():
            with self.subTest(counter=name), self.assertRaisesRegex(ValueError, "no provider token"):
                UsageEvent(**{**base, "tokens": TokenUsage(**{**base["tokens"].to_dict(), name: 0})})

    def test_operation_metadata_roundtrip_and_content_refusal(self):
        details = {"input_identity": {"program_digest": "a" * 64, "seat_id": "shell"},
                   "recomputed_object_ids": ["wall-1"], "cache_checks": {"source": "missing"}, "active_wait_ms": 150}
        event = UsageEvent("op", "studio", "none", "none", "design_edit", "succeeded",
            "2026-09-09T12:00:00Z", TokenUsage(), model_call=False, timing_scope="interaction",
            operation_id="op", parent_event_id=None, details=details)
        details["recomputed_object_ids"].append("later")
        self.assertEqual(event.details["recomputed_object_ids"], ["wall-1"])
        self.assertEqual(UsageEvent.from_dict(event.to_dict()), event)
        for invalid in ({"prompt": "private"}, {"input_identity": {"prompt": "private"}},
                        {"input_identity": {"program_digest": "private"}}, {"active_wait_ms": -1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                UsageEvent(**{**event.to_dict(), "tokens": TokenUsage(), "details": invalid})

    def test_end_time_must_not_precede_start(self):
        with self.assertRaisesRegex(ValueError, "precedes"):
            UsageEvent("call", "studio", "test", "model", "intent", "completed",
                       "2026-09-09T12:00:00Z", TokenUsage(), "api_estimate",
                       ended_at="2026-09-09T11:59:59Z")

    def test_service_status_and_unknown_tokens_do_not_change_a_model_quote(self):
        rate = RateCard("test", "model", input="1", output="2")
        model_tokens = TokenUsage(10, 2)
        expected = quote(model_tokens, rate)
        for status in ("completed", "failed"):
            service = UsageEvent("service", "studio", "test", "model", "preview", status,
                                 "2026-09-09T12:00:00Z", TokenUsage(None, None, None, None, None, None),
                                 "unknown", model_call=False, timing_scope="service")
            result = quote(service.tokens, rate)
            self.assertIsNone(result["amount_usd"])
            self.assertEqual(result["known_subtotal_usd"], "0")
            self.assertEqual(quote(model_tokens, rate), expected)


class AlgorithmTests(unittest.TestCase):
    def test_baseline_uses_priority_order_and_all_budget_dimensions(self):
        context = SelectionContext((
            Action("costly", "2", 2, 2),
            Action("slow", "0.1", 2, 200),
            Action("many-tokens", "0.1", 20, 2),
            Action("unknown"),
            Action("fits", "0.1", 2, 2),
        ), Budget("1", 10, 100), {"quality": 0.8}, {"objective": "repair"})
        self.assertEqual(choose_action(FirstAvailablePolicy(), context).action_id, "fits")

    def test_stop_when_no_action_fits(self):
        context = SelectionContext((Action("unknown"),), Budget(tokens=10))
        self.assertIsNone(choose_action(FirstAvailablePolicy(), context).action_id)

    def test_caller_list_cannot_be_expanded_during_selection(self):
        actions = [Action("allowed")]
        class Policy:
            def choose(self, context):
                actions.append(Action("injected"))
                return Decision("injected", "not caller-supplied")
        context = SelectionContext(actions, Budget())
        with self.assertRaisesRegex(ValueError, "unavailable"):
            choose_action(Policy(), context)

    def test_custom_policy_consumes_observations_without_execution(self):
        class Policy:
            def choose(self, context):
                return Decision(context.observations["preferred"], context.objectives["reason"])
        context = SelectionContext((Action("inspect"), Action("compare")), Budget(),
                                   {"preferred": "compare"}, {"reason": "Improve evidence"})
        self.assertEqual(choose_action(Policy(), context), Decision("compare", "Improve evidence"))

    def test_invalid_actions_and_budget_violations_are_rejected(self):
        class Policy:
            def __init__(self, action_id):
                self.action_id = action_id
            def choose(self, context):
                return Decision(self.action_id, "custom")
        context = SelectionContext((Action("expensive", "2", 2, 2),), Budget("1", 10, 10))
        for action_id in ("not-supplied", "expensive"):
            with self.subTest(action=action_id), self.assertRaises(ValueError):
                choose_action(Policy(action_id), context)


def counts(input_tokens, output_tokens, cached=0, reasoning=0):
    return {"input_tokens": input_tokens, "cached_input_tokens": cached,
            "cache_write_input_tokens": 0, "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning,
            "total_tokens": None if input_tokens is None or output_tokens is None else input_tokens + output_tokens}


def token_row(total=None, last=None, timestamp="2026-09-09T12:00:00Z"):
    return {"type": "event_msg", "timestamp": timestamp,
            "payload": {"type": "token_count", "info": {
                "total_token_usage": total, "last_token_usage": last}}}


def session_rows(session_id, *, parent=None, **metadata):
    return [
        {"type": "session_meta", "payload": {"id": session_id, "model_provider": "openai",
         **({"source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}} if parent else {}),
         **metadata}},
        {"type": "turn_context", "payload": {"model": "exact-model"}},
    ]


def boundary_row(kind, timestamp, turn_id=None, **fields):
    return {"type": "event_msg", "timestamp": timestamp,
            "payload": {"type": kind, **({"turn_id": turn_id} if turn_id else {}), **fields}}


class CodexTests(unittest.TestCase):
    def read_selected(self, *sources, warnings=None):
        with TemporaryDirectory() as directory:
            paths = []
            for index, rows in enumerate(sources):
                path = Path(directory) / f"selected-{index}.jsonl"
                path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                paths.append(path)
            return list(iter_codex_events(paths, warnings=warnings))

    def read(self, rows, trailing=""):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "explicit-session.jsonl"
            metadata = [
                {"type": "session_meta", "payload": {"id": "session-id", "model_provider": "openai", "cwd": "private-path"}},
                {"type": "turn_context", "payload": {"model": "exact-model"}},
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in metadata + rows) + trailing, encoding="utf-8")
            return list(iter_codex_events([path, path]))

    def test_cumulative_and_last_are_not_both_added_and_duplicates_skipped(self):
        first, second = counts(100, 10, 40, 5), counts(150, 15, 60, 7)
        rows = [token_row(first, first), token_row(first, first), token_row(first, counts(0, 0)),
                token_row(second, counts(50, 5, 20, 2))]
        events = self.read(rows)
        self.assertEqual(len(events), 2)
        self.assertEqual(sum(event.tokens.input_tokens for event in events), 150)
        self.assertEqual(sum(event.tokens.output_tokens for event in events), 15)
        self.assertEqual(events[1].tokens.cached_input_tokens, 20)
        self.assertEqual(events[1].tokens.reasoning_output_tokens, 2)
        self.assertEqual(events[0].model, "exact-model")
        self.assertEqual(events[0].provider, "openai")
        self.assertEqual(events[0].billing_mode, "subscription_equivalent")
        self.assertNotIn("private-path", json.dumps([event.to_dict() for event in events]))

    def test_reset_uses_reported_last_and_marks_uncertainty(self):
        events = self.read([
            token_row(counts(1000, 100), counts(1000, 100)),
            token_row(counts(700, 70), counts(20, 2)),
            token_row(counts(730, 73), counts(30, 3)),
        ])
        self.assertEqual([event.tokens.input_tokens for event in events], [1000, 20, 30])
        self.assertEqual(events[1].status, "counter_discontinuity")

    def test_reset_without_last_is_unknown(self):
        events = self.read([token_row(counts(100, 10)), token_row(counts(50, 5))])
        self.assertIsNone(events[1].tokens.input_tokens)
        self.assertEqual(events[1].status, "counter_reset_unknown")

    def test_cumulative_jump_never_becomes_a_fabricated_call(self):
        events = self.read([
            token_row(counts(100, 10), counts(100, 10)),
            token_row(counts(10000, 100), counts(50, 5)),
        ])
        self.assertEqual(events[1].tokens.input_tokens, 50)
        self.assertEqual(events[1].status, "counter_discontinuity")

    def test_partial_history_and_last_only_are_explicit(self):
        events = self.read([token_row(counts(1000, 100), counts(50, 5)),
                            token_row(last=counts(30, 3)), token_row(last=counts(30, 3))])
        self.assertEqual([event.tokens.input_tokens for event in events], [50, 30])
        self.assertEqual([event.status for event in events], ["partial_history", "last_only"])

    def test_last_only_then_cumulative_does_not_count_the_call_again(self):
        events = self.read([
            token_row(counts(100, 10), counts(100, 10)),
            token_row(last=counts(30, 3)),
            token_row(counts(130, 13), counts(30, 3)),
            token_row(counts(150, 15), counts(20, 2)),
        ])
        self.assertEqual([event.tokens.input_tokens for event in events], [100, 30, 20])

    def test_model_change_keeps_cumulative_delta_in_the_new_model(self):
        events = self.read([
            token_row(counts(100, 10), counts(100, 10)),
            {"type": "turn_context", "payload": {"model": "second-model"}},
            token_row(counts(140, 14), counts(40, 4)),
        ])
        self.assertEqual(events[1].model, "second-model")
        self.assertEqual(events[1].tokens.input_tokens, 40)

    def test_missing_totals_and_partial_trailing_record(self):
        events = self.read([token_row(last={"output_tokens": 2})], '{"type":')
        self.assertIsNone(events[0].tokens.input_tokens)
        self.assertEqual(events[0].tokens.output_tokens, 2)

    def test_completed_malformed_record_raises(self):
        with self.assertRaisesRegex(ValueError, "Malformed Codex JSON on line"):
            self.read([], '{"type":\n')

    def test_copies_with_different_line_numbers_merge_before_cumulative_deltas(self):
        first = token_row(counts(100, 10), counts(100, 10))
        second = token_row(counts(150, 15), counts(50, 5), "2026-09-09T12:00:01Z")
        third = token_row(counts(180, 18), counts(30, 3), "2026-09-09T12:00:02Z")
        earlier = session_rows("same-session") + [first, second]
        later = session_rows("same-session") + [
            {"type": "response_item", "payload": {"content": "PRIVATE_RESPONSE"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "PRIVATE_PROMPT"}},
            second, second, third,
        ]
        events = self.read_selected(later, earlier, earlier)
        self.assertEqual([event.tokens.input_tokens for event in events], [100, 50, 30])
        self.assertEqual(events, self.read_selected(earlier, later))
        self.assertEqual(len({event.event_id for event in events}), 3)
        self.assertEqual({event.source_ref for event in events}, {"codex:same-session"})
        self.assertNotIn("PRIVATE", json.dumps([event.to_dict() for event in events]))

    def test_sources_without_session_metadata_are_not_guessed_to_be_copies(self):
        rows = [token_row(counts(100, 10), counts(100, 10))]
        events = self.read_selected(rows, rows)
        self.assertEqual(len(events), 2)
        self.assertEqual({event.source_ref for event in events}, {"codex:file-0", "codex:file-1"})
        self.assertTrue(all(event.session_id is None for event in events))

    def test_legacy_child_inherited_turn_is_not_counted_twice(self):
        parent_turn = [
            boundary_row("task_started", "2026-09-09T12:00:00Z", "parent-turn"),
            token_row(counts(100, 10), counts(100, 10), "2026-09-09T12:00:01Z"),
            boundary_row("task_complete", "2026-09-09T12:00:02Z", "parent-turn"),
        ]
        child_turn = [
            boundary_row("task_started", "2026-09-09T12:00:03Z", "child-turn"),
            token_row(counts(130, 13), counts(30, 3), "2026-09-09T12:00:04Z"),
            boundary_row("task_complete", "2026-09-09T12:00:05Z", "child-turn"),
        ]
        warnings = []
        events = self.read_selected(
            session_rows("child", parent="parent") + parent_turn + child_turn,
            session_rows("parent") + parent_turn, warnings=warnings,
        )
        calls = [event for event in events if event.model_call]
        turns = [event for event in events if event.timing_scope == "agent_turn"]
        self.assertEqual(sorted(event.tokens.input_tokens for event in calls), [30, 100])
        self.assertEqual(len(turns), 2)
        self.assertEqual(warnings, [])
        child = next(event for event in calls if event.session_id == "child")
        self.assertEqual(child.parent_session_id, "parent")
        self.assertEqual(child.turn_id, "child-turn")
        self.assertEqual(child.related_event_id, "codex:child:turn:child-turn")
        self.assertEqual(child.tokens.input_tokens, 30)

    def test_paginated_boundary_uses_recorded_ordinal_even_without_parent_source(self):
        inherited = token_row(counts(100, 10), counts(100, 10))
        inherited["ordinal"] = 9
        started = boundary_row("task_started", "2026-09-09T12:00:01Z", "child-turn")
        started["ordinal"] = 10
        own = token_row(counts(130, 13), counts(30, 3), "2026-09-09T12:00:02Z")
        own["ordinal"] = 20
        ended = boundary_row("task_complete", "2026-09-09T12:00:04Z", "child-turn")
        ended["ordinal"] = 21
        warnings = []
        events = self.read_selected(session_rows("child", parent="parent", history_mode="paginated",
            subagent_history_start_ordinal=10) + [
                {"type": "response_item", "ordinal": 8, "payload": {"content": "PRIVATE_HISTORY"}},
                inherited, started, own, ended,
            ], warnings=warnings)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].tokens.input_tokens, 30)
        self.assertEqual(events[0].session_id, "child")
        self.assertEqual(events[1].duration_ms, 3000)
        self.assertEqual(warnings, [])

    def test_missing_paginated_ordinal_stays_visible_with_warning(self):
        warnings = []
        events = self.read_selected(session_rows("child", parent="parent", history_mode="paginated",
            subagent_history_start_ordinal=100) + [
                token_row(counts(100, 10), counts(30, 3)),
            ], warnings=warnings)
        self.assertEqual(events[0].tokens.input_tokens, 30)
        self.assertEqual(events[0].status, "partial_history")
        self.assertTrue(any("ordinal" in warning for warning in warnings))

    def test_copy_with_explicit_inherited_ordinal_resolves_an_incomplete_copy(self):
        metadata = session_rows("child", parent="parent", history_mode="paginated",
                                subagent_history_start_ordinal=2)
        started = boundary_row("task_started", "2026-09-09T12:00:00Z", "inherited-turn")
        without_ordinal = metadata + [started]
        with_ordinal = metadata + [{**started, "ordinal": 1}]
        self.assertEqual(self.read_selected(without_ordinal, with_ordinal), [])
        self.assertEqual(self.read_selected(with_ordinal, without_ordinal), [])

    def test_copy_with_reported_last_usage_resolves_a_partial_first_snapshot(self):
        metadata = session_rows("same-session")
        missing_last = metadata + [token_row(counts(1000, 100))]
        reported_last = metadata + [token_row(counts(1000, 100), counts(50, 5))]
        events = self.read_selected(missing_last, reported_last)
        self.assertEqual(events, self.read_selected(reported_last, missing_last))
        self.assertEqual(events[0].tokens.input_tokens, 50)
        self.assertEqual(events[0].status, "partial_history")

    def test_task_boundaries_measure_whole_turn_and_leave_call_durations_unknown(self):
        events = self.read([
            boundary_row("task_started", "2026-09-09T12:00:00Z", "task-turn"),
            token_row(counts(10, 1), counts(10, 1), "2026-09-09T12:01:00Z"),
            token_row(counts(30, 3), counts(20, 2), "2026-09-09T12:04:00Z"),
            boundary_row("task_complete", "2026-09-09T12:05:00Z", "task-turn"),
        ])
        self.assertEqual([event.duration_ms for event in events[:2]], [None, None])
        self.assertTrue(all(event.timing_scope == "model_call" for event in events[:2]))
        turn = events[2]
        self.assertEqual(turn.duration_ms, 300000)
        self.assertEqual(turn.ended_at, "2026-09-09T12:05:00Z")
        self.assertEqual(turn.status, "completed")
        self.assertIsNone(turn.model_call)
        self.assertTrue(all(value is None for value in turn.tokens.to_dict().values()))

    def test_native_completion_times_and_duration_survive_delayed_log_write(self):
        events = self.read([
            boundary_row("task_complete", "2026-09-09T12:05:00Z", "task-turn",
                         started_at=100, completed_at=104, duration_ms=3500),
        ])
        self.assertEqual(events[0].started_at, "1970-01-01T00:01:40Z")
        self.assertEqual(events[0].ended_at, "1970-01-01T00:01:44Z")
        self.assertEqual(events[0].duration_ms, 3500)

    def test_missing_boundary_remains_unknown_and_aborted_turn_is_retained(self):
        events = self.read([
            token_row(counts(10, 1), counts(10, 1)),
            boundary_row("task_complete", "2026-09-09T12:00:01Z", "missing-start"),
            boundary_row("task_started", "2026-09-09T12:00:02Z", "aborted-turn"),
            boundary_row("turn_aborted", "2026-09-09T12:00:05Z", "aborted-turn"),
            boundary_row("task_started", "2026-09-09T12:00:06Z", "running-turn"),
        ])
        self.assertEqual(len(events), 3)
        self.assertIsNone(events[0].duration_ms)
        self.assertEqual(events[1].status, "aborted")
        self.assertEqual(events[1].duration_ms, 3000)
        self.assertEqual(events[2].status, "running")
        self.assertIsNone(events[2].duration_ms)
        self.assertIsNone(events[2].ended_at)

    def test_consecutive_legacy_boundaries_without_turn_ids_are_separate(self):
        events = self.read([
            boundary_row("task_started", "2026-09-09T12:00:00Z"),
            boundary_row("task_complete", "2026-09-09T12:00:01Z"),
            boundary_row("task_started", "2026-09-09T12:00:02Z"),
            boundary_row("task_complete", "2026-09-09T12:00:05Z"),
        ])
        self.assertEqual([event.duration_ms for event in events], [1000, 3000])
        self.assertEqual(len({event.event_id for event in events}), 2)
        self.assertTrue(all(event.turn_id is None for event in events))


class BoundCodexSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.database = sqlite3.connect(self.home / "state_5.sqlite")
        self.addCleanup(self.database.close)
        self.database.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
        self.database.commit()

    def source(self, session, *, metadata=None, name="selected.jsonl"):
        path = self.home / name
        rows = session_rows(session if metadata is None else metadata) + [token_row(counts(10, 2), counts(10, 2))]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        self.database.execute("INSERT INTO threads VALUES (?, ?)", (session, str(path)))
        self.database.commit()
        return path

    def test_exact_opaque_ids_resolve_without_scanning_other_logs_or_writing(self):
        session = "fixture/session:not-a-uuid"
        path = self.source(session)
        (self.home / "unselected.jsonl").write_text("private unselected body is not JSON", encoding="utf-8")
        before = {item.name: item.read_bytes() for item in self.home.iterdir()}
        warnings = []
        paths, projects = bound_codex_sources([{"projectId": "project-a", "sessionId": session}] * 2, self.home, warnings)
        self.assertEqual(paths, {path: session})
        self.assertEqual(projects, {session: "project-a"})
        events = list(iter_codex_events(paths, expected_sessions=paths, warnings=warnings))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].session_id, session)
        self.assertIsNone(events[0].duration_ms)
        self.assertFalse(warnings)
        self.assertEqual(before, {item.name: item.read_bytes() for item in self.home.iterdir()})

    def test_conflicting_projects_missing_ids_and_paths_never_guess(self):
        self.source("conflict")
        self.database.execute("INSERT INTO threads VALUES ('outside', ?)", (str(self.home.parent / "outside.jsonl"),))
        self.database.commit()
        warnings = []
        paths, projects = bound_codex_sources([
            {"projectId": "a", "sessionId": "conflict"}, {"projectId": "b", "sessionId": "conflict"},
            {"projectId": "a", "sessionId": "not-indexed"}, {"projectId": "a", "sessionId": "outside"},
        ], self.home, warnings)
        self.assertEqual((paths, projects), ({}, {}))
        self.assertEqual(len(warnings), 3)
        self.assertNotIn(str(self.home), "".join(warnings))

    def test_native_metadata_mismatch_or_absence_discards_the_whole_bound_source(self):
        path = self.source("expected", metadata="other")
        expected = {path: "expected"}
        for rows in (
            session_rows("other") + [token_row(counts(10, 2), counts(10, 2))],
            [token_row(counts(10, 2), counts(10, 2))],
            session_rows("expected") + [token_row(counts(10, 2), counts(10, 2))] + session_rows("other"),
        ):
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            warnings = []
            self.assertEqual(list(iter_codex_events([path], expected_sessions=expected, warnings=warnings)), [])
            self.assertEqual(len(warnings), 1)
            self.assertIn("身份不符", warnings[0])

    def test_missing_database_is_not_created_and_a_new_index_can_be_read_later(self):
        root = self.home / "not-created"
        bindings = [{"projectId": "a", "sessionId": "new"}]
        warnings = []
        self.assertEqual(bound_codex_sources(bindings, root, warnings), ({}, {}))
        self.assertFalse(root.exists())
        self.assertEqual(len(warnings), 1)
        path = self.source("new")
        self.assertEqual(bound_codex_sources(bindings, self.home, []), ({path: "new"}, {"new": "a"}))

    def test_read_only_lookup_sees_the_running_codex_wal_and_updated_archive_path(self):
        self.database.execute("PRAGMA journal_mode=WAL")
        path = self.source("live")
        bindings = [{"projectId": "a", "sessionId": "live"}]
        self.assertTrue((self.home / "state_5.sqlite-wal").exists())
        self.assertEqual(bound_codex_sources(bindings, self.home, [])[0], {path: "live"})
        archive = self.home / "archived_sessions"
        archive.mkdir()
        moved = archive / path.name
        path.rename(moved)
        self.database.execute("UPDATE threads SET rollout_path = ? WHERE id = 'live'", (str(moved),))
        self.database.commit()
        self.assertEqual(bound_codex_sources(bindings, self.home, [])[0], {moved: "live"})

    def test_shared_rollout_path_and_invalid_binding_payloads_are_rejected(self):
        path = self.source("first")
        self.database.execute("INSERT INTO threads VALUES ('second', ?)", (str(path),))
        self.database.commit()
        warnings = []
        self.assertEqual(bound_codex_sources([
            {"projectId": "a", "sessionId": session} for session in ("first", "second")
        ], self.home, warnings), ({}, {}))
        self.assertEqual(len(warnings), 1)
        for body in ({}, [None], [{"projectId": "a", "sessionId": ""}],
                     [{"projectId": "a", "sessionId": "first", "messages": ["private"]}]):
            with self.assertRaises(ValueError):
                bound_codex_sources(body, self.home, [])


if __name__ == "__main__":
    unittest.main()
