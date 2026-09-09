from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from monkeymonitor.algorithms import (
    Action, Budget, Decision, FirstAvailablePolicy, SelectionContext, choose_action,
)
from monkeymonitor.codex import iter_codex_events
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


class CodexTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
