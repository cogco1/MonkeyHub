"""The reserved Studio ports survived the move out of the preview backend."""

from __future__ import annotations

import unittest

from archflow_studio_api.ports import (
    HumanReviewPort,
    IntentProvider,
    PreviewBackend,
    RetrievalProvider,
    StudioEventSink,
    ViewerAssetProvider,
)


class ReservedPortsTests(unittest.TestCase):
    def test_all_six_reserved_ports_are_still_protocols(self) -> None:
        for port in (
            IntentProvider,
            RetrievalProvider,
            PreviewBackend,
            ViewerAssetProvider,
            HumanReviewPort,
            StudioEventSink,
        ):
            with self.subTest(port=port.__name__):
                self.assertTrue(getattr(port, "_is_protocol", False))

    def test_intent_provider_still_refuses_to_commit(self) -> None:
        self.assertEqual(
            IntentProvider.__doc__,
            "Translate a user utterance into a proposal candidate, "
            "never a commit.",
        )


if __name__ == "__main__":
    unittest.main()
