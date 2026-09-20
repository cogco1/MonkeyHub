"""Check cost accounting and failure handling; never make a provider call."""
import unittest

from labs.spatial_observation.provider import image_block, parse_answer, usage_events


class ProviderContractTests(unittest.TestCase):
    def test_usage_includes_auxiliary_and_all_disjoint_input_buckets(self):
        result = {"modelUsage": {"primary": {"inputTokens": 5, "outputTokens": 3,
                  "cacheReadInputTokens": 7, "cacheCreationInputTokens": 11},
                  "auxiliary": {"inputTokens": 2, "outputTokens": 1}}}
        events = usage_events(result, event_id="test", started="2026-09-20T00:00:00+00:00", elapsed=1)
        self.assertEqual(events[0].tokens.input_tokens, 23)
        self.assertEqual(events[0].tokens.cached_input_tokens, 7)
        self.assertIsNone(events[1].tokens.input_tokens)
        self.assertTrue(all(e.duration_ms is None for e in events))

    def test_failed_provider_preserves_unknown_usage(self):
        event, = usage_events({}, event_id="test", started="2026-09-20T00:00:00+00:00", elapsed=1.5)
        self.assertIsNone(event.tokens.input_tokens)
        self.assertIsNone(event.model_call)
        self.assertEqual(event.status, "failed")
        self.assertIsNone(parse_answer("```json\n{}\n```"))
        self.assertIsNone(parse_answer("[]"))
        self.assertIsNone(parse_answer('{"value":NaN}'))
        self.assertIsNone(parse_answer('{"value":1e999}'))

    def test_real_image_channel_is_base64_image_not_text(self):
        block = image_block(b"public-test-image")
        self.assertEqual(block["type"], "image")
        self.assertEqual(block["source"]["media_type"], "image/png")


if __name__ == "__main__":
    unittest.main()
