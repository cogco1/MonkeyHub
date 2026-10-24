"""The failure kinds the Hub and the Runtime share (#300 4-1, 4-3)."""

import unittest

from archflow.contracts.failures import FAILURE_KINDS, LONGEST_WAIT_S, failure_body, failure_kind, resend_wait


class FailureKindTests(unittest.TestCase):
    def test_a_known_code_says_its_kind_and_an_unknown_one_is_answered_as_before(self):
        self.assertEqual(failure_body("DRAWING_WORKER_LOST", "gone"),
                         {"code": "DRAWING_WORKER_LOST", "detail": "gone", "category": "transient", "resend": True})
        self.assertEqual(failure_body("SKETCH_INVALID", "bad"), {"code": "SKETCH_INVALID", "detail": "bad"})
        self.assertIsNone(failure_kind(None))

    def test_only_what_did_nothing_is_sent_again_and_never_without_end(self):
        running = failure_kind("OPERATION_RUNNING")
        self.assertIsNone(resend_wait(running, 1), "a running operation is waited for, never sent twice")
        lost = failure_kind("DRAWING_WORKER_LOST")
        waits = [resend_wait(lost, sends) for sends in range(1, lost.tries + 1)]
        self.assertEqual(waits[-1], None)
        self.assertTrue(all(0 < wait <= LONGEST_WAIT_S for wait in waits[:-1]))
        for code, kind in FAILURE_KINDS.items():
            self.assertTrue(code.isupper() and kind.tries >= 1, code)
            if kind.category == "conflict":
                self.assertFalse(kind.resend, f"{code}: a conflict is read again, not sent again")


if __name__ == "__main__":
    unittest.main()
