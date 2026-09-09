"""The dependency-aware queue: what runs beside what, what waits, and why.

These tests drive the registry with work that blocks on events, so the order
and the overlap of runs are observed, not assumed. No project is involved:
the queue's rule is about closures and lanes, and that is all it is given.
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from archflow_studio_api.application.jobs import (
    EXCLUSIVE,
    EXCLUSIVE_REASON,
    PARALLEL,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    JobRegistry,
)


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def publish(self, *, event: dict) -> None:
        with self._lock:
            self.events.append(dict(event))

    def of(self, event_type: str) -> list[dict]:
        with self._lock:
            return [event for event in self.events if event["type"] == event_type]


class Gate:
    """Work that reports it started and finishes when told to."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self) -> None:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("the gate was never released")


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class QueueTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.events = Recorder()
        self.registry = JobRegistry(self.events, max_workers=2)
        self.addCleanup(self.registry.shutdown)

    def submit(self, candidate: str, gate: Gate, closure=(), exclusive=False):
        return self.registry.submit(
            candidate_id=candidate,
            proposal_id=f"p-{candidate}",
            work=gate,
            closure=closure,
            exclusive=exclusive,
        )


class ConcurrentSubmissionTests(QueueTestCase):
    def test_another_submit_can_admit_a_job_before_its_submitter_returns(self) -> None:
        a, b = Gate(), Gate()
        paused, resume = threading.Event(), threading.Event()
        original_admit = self.registry._admit
        errors = []

        def pause_first_admission(*args, **kwargs):
            if threading.current_thread().name == "submit-a":
                paused.set()
                if not resume.wait(5):
                    raise TimeoutError("the first submitter was never resumed")
            return original_admit(*args, **kwargs)

        def submit_a():
            try:
                self.submit("a", a, closure={"entity:a"})
            except Exception as exc:
                errors.append(exc)

        with patch.object(self.registry, "_admit", side_effect=pause_first_admission):
            thread = threading.Thread(target=submit_a, name="submit-a")
            thread.start()
            try:
                self.assertTrue(paused.wait(2))
                self.submit("b", b, closure={"entity:b"})
                self.assertTrue(a.started.wait(2))
                self.assertTrue(b.started.wait(2))
            finally:
                resume.set()
                a.release.set()
                b.release.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 2))
        for candidate in ("a", "b"):
            self.assertEqual(self.registry.for_candidate(candidate).status, SUCCEEDED)
            self.assertEqual(
                [event["type"] for event in self.events.events
                 if event["candidate_id"] == candidate],
                ["candidate.queued", "candidate.running", "candidate.succeeded"],
            )

    def test_queued_publication_preserves_order_before_a_competing_submit(self) -> None:
        a, b = Gate(), Gate()
        publishing, resume = threading.Event(), threading.Event()
        second_submitting = threading.Event()
        original_publish = self.events.publish
        errors = []

        def pause_first_publication(*, event):
            if event["candidate_id"] == "a" and event["type"] == "candidate.queued":
                publishing.set()
                if not resume.wait(5):
                    raise TimeoutError("the queued event was never published")
            original_publish(event=event)

        def submit(candidate, gate):
            try:
                if candidate == "b":
                    second_submitting.set()
                self.submit(candidate, gate, closure={"entity:shared"})
            except Exception as exc:
                errors.append(exc)

        with patch.object(self.events, "publish", side_effect=pause_first_publication):
            threads = [
                threading.Thread(target=submit, args=(candidate, gate))
                for candidate, gate in (("a", a), ("b", b))
            ]
            threads[0].start()
            try:
                self.assertTrue(publishing.wait(2))
                threads[1].start()
                self.assertTrue(second_submitting.wait(2))
                self.assertFalse(b.started.wait(0.2))
                resume.set()
                self.assertTrue(a.started.wait(2))
                self.assertFalse(b.started.is_set())
                a.release.set()
                self.assertTrue(b.started.wait(2))
            finally:
                resume.set()
                a.release.set()
                b.release.set()
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(2)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 2))
        self.assertEqual(
            [event["candidate_id"] for event in self.events.of("candidate.running")],
            ["a", "b"],
        )


class DisjointCandidatesTests(QueueTestCase):
    def test_two_disjoint_candidates_run_at_the_same_time(self) -> None:
        a, b = Gate(), Gate()
        job_a = self.submit("a", a, closure={"entity:portico-base"})
        job_b = self.submit("b", b, closure={"entity:door-leaf"})
        self.assertTrue(a.started.wait(2))
        self.assertTrue(b.started.wait(2))
        self.assertEqual(self.registry.get(job_a.job_id).status, RUNNING)
        self.assertEqual(self.registry.get(job_b.job_id).status, RUNNING)
        self.assertEqual(self.registry.get(job_b.job_id).lane, PARALLEL)
        self.assertIsNone(self.registry.get(job_b.job_id).waiting_for)
        a.release.set()
        b.release.set()
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 2))

    def test_a_third_disjoint_candidate_waits_for_a_worker_and_says_so(self) -> None:
        gates = [Gate() for _ in range(3)]
        jobs = [
            self.submit(f"c{i}", gate, closure={f"entity:e{i}"})
            for i, gate in enumerate(gates)
        ]
        self.assertTrue(gates[0].started.wait(2))
        self.assertTrue(gates[1].started.wait(2))
        third = self.registry.get(jobs[2].job_id)
        self.assertEqual(third.status, QUEUED)
        self.assertIsNone(third.waiting_for)
        self.assertEqual(third.waiting_reason, "every worker is busy (2 of 2)")
        gates[0].release.set()
        self.assertTrue(gates[2].started.wait(2))
        self.assertIsNone(self.registry.get(jobs[2].job_id).waiting_reason)
        for gate in gates[1:]:
            gate.release.set()
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 3))


class ConflictingCandidatesTests(QueueTestCase):
    def test_a_candidate_that_shares_a_ref_waits_and_names_the_one_ahead(self) -> None:
        a, b = Gate(), Gate()
        job_a = self.submit("a", a, closure={"entity:portico-base", "component:portico"})
        job_b = self.submit("b", b, closure={"entity:portico-cornice", "component:portico"})
        self.assertTrue(a.started.wait(2))
        self.assertFalse(b.started.wait(0.2))
        waiting = self.registry.get(job_b.job_id)
        self.assertEqual(waiting.status, QUEUED)
        self.assertEqual(waiting.waiting_for, "a")
        self.assertEqual(waiting.waiting_reason, "shares component:portico")
        self.assertEqual(
            [event["candidate_id"] for event in self.events.of("candidate.waiting")], ["b"]
        )
        a.release.set()
        self.assertTrue(b.started.wait(2))
        running = self.registry.get(job_b.job_id)
        self.assertEqual(running.status, RUNNING)
        self.assertIsNone(running.waiting_for)
        b.release.set()
        self.assertTrue(wait_until(lambda: self.registry.get(job_a.job_id).status == SUCCEEDED))
        self.assertTrue(wait_until(lambda: self.registry.get(job_b.job_id).status == SUCCEEDED))

    def test_conflicting_candidates_keep_their_order_behind_each_other(self) -> None:
        gates = [Gate() for _ in range(3)]
        jobs = [
            self.submit(f"c{i}", gate, closure={"entity:portico-base"})
            for i, gate in enumerate(gates)
        ]
        self.assertTrue(gates[0].started.wait(2))
        self.assertEqual(self.registry.get(jobs[1].job_id).waiting_for, "c0")
        # The third waits for the second, not the first: the one directly ahead.
        self.assertEqual(self.registry.get(jobs[2].job_id).waiting_for, "c1")
        gates[0].release.set()
        self.assertTrue(gates[1].started.wait(2))
        self.assertFalse(gates[2].started.wait(0.2))
        gates[1].release.set()
        self.assertTrue(gates[2].started.wait(2))
        gates[2].release.set()
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 3))

    def test_a_disjoint_candidate_overtakes_a_waiting_conflicting_one(self) -> None:
        a, b, c = Gate(), Gate(), Gate()
        self.submit("a", a, closure={"entity:x"})
        job_b = self.submit("b", b, closure={"entity:x"})
        self.submit("c", c, closure={"entity:y"})
        self.assertTrue(a.started.wait(2))
        self.assertTrue(c.started.wait(2))
        self.assertEqual(self.registry.get(job_b.job_id).waiting_for, "a")
        for gate in (a, b, c):
            gate.release.set()
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 3))


class ExclusiveLaneTests(QueueTestCase):
    def test_two_exporting_candidates_never_run_together_whatever_they_touch(self) -> None:
        a, b = Gate(), Gate()
        self.submit("a", a, closure={"entity:x"}, exclusive=True)
        job_b = self.submit("b", b, closure={"entity:y"}, exclusive=True)
        self.assertTrue(a.started.wait(2))
        self.assertFalse(b.started.wait(0.2))
        waiting = self.registry.get(job_b.job_id)
        self.assertEqual(waiting.lane, EXCLUSIVE)
        self.assertEqual(waiting.waiting_for, "a")
        self.assertEqual(waiting.waiting_reason, EXCLUSIVE_REASON)
        a.release.set()
        self.assertTrue(b.started.wait(2))
        b.release.set()
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 2))

    def test_a_kernel_only_candidate_runs_beside_an_exporting_one(self) -> None:
        a, b = Gate(), Gate()
        self.submit("a", a, closure={"entity:x"}, exclusive=True)
        self.submit("b", b, closure={"entity:y"}, exclusive=False)
        self.assertTrue(a.started.wait(2))
        self.assertTrue(b.started.wait(2))
        a.release.set()
        b.release.set()
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 2))


class FailureReleasesTheQueueTests(QueueTestCase):
    def test_a_failed_candidate_lets_the_one_waiting_on_it_run(self) -> None:
        def explode() -> None:
            raise RuntimeError("the seat pack is missing")

        b = Gate()
        job_a = self.submit("a", explode, closure={"entity:x"})
        job_b = self.submit("b", b, closure={"entity:x"})
        self.assertTrue(wait_until(lambda: self.registry.get(job_a.job_id).status == "failed"))
        self.assertEqual(self.registry.get(job_a.job_id).error, "the seat pack is missing")
        self.assertTrue(b.started.wait(2))
        b.release.set()
        self.assertTrue(wait_until(lambda: self.registry.get(job_b.job_id).status == SUCCEEDED))

    def test_a_registry_with_one_worker_is_the_old_serial_queue(self) -> None:
        registry = JobRegistry(self.events, max_workers=1)
        self.addCleanup(registry.shutdown)
        a, b = Gate(), Gate()
        registry.submit(candidate_id="a", proposal_id="p", work=a, closure={"entity:x"})
        job_b = registry.submit(candidate_id="b", proposal_id="q", work=b, closure={"entity:y"})
        self.assertTrue(a.started.wait(2))
        self.assertFalse(b.started.wait(0.2))
        self.assertEqual(registry.get(job_b.job_id).waiting_reason, "every worker is busy (1 of 1)")
        a.release.set()
        self.assertTrue(b.started.wait(2))
        b.release.set()
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 2))


if __name__ == "__main__":
    unittest.main()
