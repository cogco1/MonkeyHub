"""The event sink: an ordered, bounded, in-process record of what happened.

These tests are about the two promises the SSE transport rests on. Order:
``seq`` is assigned once, under a lock, so two threads publishing at the same
moment cannot both be number 7. Boundedness: the buffer holds a fixed number of
events and drops the oldest, so a long-running process cannot be made to grow
without limit by anybody who keeps asking it to run candidates.

A subscriber that arrives late is given the buffer it missed and then the live
stream, and the seam between them is the thing worth testing: an event
published while the replay is being read must arrive exactly once.
"""

from __future__ import annotations

import queue
import threading
import unittest

from archflow_studio_api.application.events import BUFFER_SIZE, StudioEvents


class EventSinkTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.events = StudioEvents()

    def test_publish_stamps_sequence_and_time(self) -> None:
        self.events.publish(event={"type": "candidate.queued"})
        self.events.publish(event={"type": "candidate.running"})
        first, second = self.events.replay()
        self.assertEqual(first["seq"], 1)
        self.assertEqual(second["seq"], 2)
        self.assertEqual(first["type"], "candidate.queued")
        # UTC, stated as such: a bare local timestamp on a progress event is a
        # number nobody can line up against a run receipt.
        self.assertTrue(first["at"].endswith("+00:00"), first["at"])

    def test_published_event_is_not_mutated_by_the_sink(self) -> None:
        event = {"type": "candidate.queued", "job_id": "job-1"}
        self.events.publish(event=event)
        self.assertNotIn("seq", event)

    def test_buffer_is_bounded_and_drops_the_oldest(self) -> None:
        for index in range(BUFFER_SIZE + 5):
            self.events.publish(event={"type": "candidate.queued", "n": index})
        buffered = self.events.replay()
        self.assertEqual(len(buffered), BUFFER_SIZE)
        self.assertEqual(buffered[0]["n"], 5)
        self.assertEqual(buffered[-1]["seq"], BUFFER_SIZE + 5)

    def test_replay_after_a_sequence_returns_only_later_events(self) -> None:
        for index in range(4):
            self.events.publish(event={"type": "candidate.queued", "n": index})
        later = self.events.replay(after=2)
        self.assertEqual([item["seq"] for item in later], [3, 4])

    def test_subscriber_receives_live_events_and_the_replay_it_missed(
        self,
    ) -> None:
        self.events.publish(event={"type": "candidate.queued"})
        with self.events.subscribe() as (replay, inbox):
            self.assertEqual([item["seq"] for item in replay], [1])
            self.events.publish(event={"type": "candidate.running"})
            live = inbox.get(timeout=1.0)
        self.assertEqual(live["seq"], 2)
        self.assertEqual(live["type"], "candidate.running")

    def test_subscription_ends_when_the_reader_leaves(self) -> None:
        with self.events.subscribe() as (_, inbox):
            pass
        self.events.publish(event={"type": "candidate.queued"})
        with self.assertRaises(queue.Empty):
            inbox.get_nowait()

    def test_sequence_is_unique_under_concurrent_publishers(self) -> None:
        """Two threads publishing at once still number the events once each."""

        def publish() -> None:
            for _ in range(50):
                self.events.publish(event={"type": "candidate.queued"})

        threads = [threading.Thread(target=publish) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        buffered = self.events.replay()
        sequences = [item["seq"] for item in buffered]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(set(sequences)), len(sequences))
        self.assertEqual(sequences[-1], 200)


if __name__ == "__main__":
    unittest.main()
