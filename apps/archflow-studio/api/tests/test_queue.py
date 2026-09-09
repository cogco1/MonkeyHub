"""The candidate queue: isolated computations, worker capacity and resource lanes.

These tests drive the registry with work that blocks on events, so the order
and the overlap of runs are observed, not assumed. No project is involved:
the queue admits isolated candidates by worker capacity and external resource lanes.
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

    def submit(self, candidate: str, gate: Gate, write_refs=(), exclusive=False, read_refs=()):
        return self.registry.submit(
            candidate_id=candidate,
            proposal_id=f"p-{candidate}",
            work=gate,
            read_refs=read_refs,
            write_refs=write_refs,
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
                self.submit("a", a, write_refs={"entity:a"})
            except Exception as exc:
                errors.append(exc)

        with patch.object(self.registry, "_admit", side_effect=pause_first_admission):
            thread = threading.Thread(target=submit_a, name="submit-a")
            thread.start()
            try:
                self.assertTrue(paused.wait(2))
                self.submit("b", b, write_refs={"entity:b"})
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
                self.submit(candidate, gate, write_refs={"entity:shared"}, exclusive=True)
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
        job_a = self.submit("a", a, write_refs={"entity:portico-base"})
        job_b = self.submit("b", b, write_refs={"entity:door-leaf"})
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
            self.submit(f"c{i}", gate, write_refs={f"entity:e{i}"})
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


class OverlappingCandidatesTests(QueueTestCase):
    def test_shared_inputs_and_overlapping_edits_compute_in_separate_candidates(self) -> None:
        for reads_a, writes_a, reads_b, writes_b in (
            ({"entity:frozen-building"}, {"entity:cabinet-a"},
             {"entity:frozen-building"}, {"entity:cabinet-b"}),
            (set(), {"entity:cabinet"}, set(), {"entity:cabinet"}),
            ({"entity:cabinet"}, {"entity:wall"}, set(), {"entity:cabinet"}),
        ):
            with self.subTest(reads_a=reads_a, writes_a=writes_a, reads_b=reads_b, writes_b=writes_b):
                a, b = Gate(), Gate()
                suffix = len(self.events.of("candidate.succeeded"))
                job_a = self.submit(f"a-{suffix}", a, read_refs=reads_a, write_refs=writes_a)
                job_b = self.submit(f"b-{suffix}", b, read_refs=reads_b, write_refs=writes_b)
                try:
                    self.assertTrue(a.started.wait(2))
                    self.assertTrue(b.started.wait(2))
                    self.assertIsNone(self.registry.get(job_a.job_id).waiting_reason)
                    self.assertIsNone(self.registry.get(job_b.job_id).waiting_reason)
                    self.assertEqual(self.registry.get(job_a.job_id).read_refs, frozenset(reads_a))
                    self.assertEqual(self.registry.get(job_b.job_id).write_refs, frozenset(writes_b))
                finally:
                    a.release.set()
                    b.release.set()
                self.assertTrue(wait_until(lambda: self.registry.get(job_a.job_id).status == SUCCEEDED))
                self.assertTrue(wait_until(lambda: self.registry.get(job_b.job_id).status == SUCCEEDED))


class ExclusiveLaneTests(QueueTestCase):
    def test_exclusive_candidates_keep_resource_order_while_parallel_work_can_overtake(self) -> None:
        gates = [Gate() for _ in range(4)]
        jobs = [self.submit(f"c{i}", gate, exclusive=i < 3) for i, gate in enumerate(gates)]
        try:
            self.assertTrue(gates[0].started.wait(2))
            self.assertTrue(gates[3].started.wait(2))
            self.assertEqual(self.registry.get(jobs[1].job_id).waiting_for, "c0")
            self.assertEqual(self.registry.get(jobs[2].job_id).waiting_for, "c1")
            gates[0].release.set()
            self.assertTrue(gates[1].started.wait(2))
            self.assertFalse(gates[2].started.is_set())
            gates[1].release.set()
            self.assertTrue(gates[2].started.wait(2))
        finally:
            for gate in gates:
                gate.release.set()
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 4))

    def test_two_exporting_candidates_never_run_together_whatever_they_touch(self) -> None:
        a, b = Gate(), Gate()
        self.submit("a", a, write_refs={"entity:x"}, exclusive=True)
        job_b = self.submit("b", b, write_refs={"entity:y"}, exclusive=True)
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
        self.submit("a", a, write_refs={"entity:x"}, exclusive=True)
        self.submit("b", b, write_refs={"entity:y"}, exclusive=False)
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
        job_a = self.submit("a", explode, write_refs={"entity:x"}, exclusive=True)
        job_b = self.submit("b", b, write_refs={"entity:x"}, exclusive=True)
        self.assertTrue(wait_until(lambda: self.registry.get(job_a.job_id).status == "failed"))
        self.assertEqual(self.registry.get(job_a.job_id).error, "the seat pack is missing")
        self.assertTrue(b.started.wait(2))
        b.release.set()
        self.assertTrue(wait_until(lambda: self.registry.get(job_b.job_id).status == SUCCEEDED))

    def test_a_registry_with_one_worker_is_the_old_serial_queue(self) -> None:
        registry = JobRegistry(self.events, max_workers=1)
        self.addCleanup(registry.shutdown)
        a, b = Gate(), Gate()
        registry.submit(candidate_id="a", proposal_id="p", work=a, write_refs={"entity:x"})
        job_b = registry.submit(candidate_id="b", proposal_id="q", work=b, write_refs={"entity:y"})
        self.assertTrue(a.started.wait(2))
        self.assertFalse(b.started.wait(0.2))
        self.assertEqual(registry.get(job_b.job_id).waiting_reason, "every worker is busy (1 of 1)")
        a.release.set()
        self.assertTrue(b.started.wait(2))
        b.release.set()
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 2))


if __name__ == "__main__":
    unittest.main()
