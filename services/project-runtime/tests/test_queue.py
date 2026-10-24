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

from project_runtime.jobs import (
    EXCLUSIVE,
    EXCLUSIVE_REASON,
    PARALLEL,
    QUEUED,
    RUN,
    RUNNING,
    SUCCEEDED,
    JobRegistry,
    report_step,
)
from project_runtime.api.dto.events import to_dto
from project_runtime.errors import StudioError


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


class ShutdownTests(QueueTestCase):
    def test_shutdown_drains_running_and_queued_jobs_and_refuses_new_work(self) -> None:
        first, second = Gate(), Gate()
        first_job = self.submit("first", first, exclusive=True)
        second_job = self.submit("second", second, exclusive=True)
        stopped = threading.Event()
        thread = threading.Thread(target=lambda: (self.registry.shutdown(), stopped.set()))
        thread.start()
        try:
            self.assertTrue(wait_until(lambda: not self.registry.accepting))
            with self.assertRaises(StudioError) as refused:
                self.submit("late", Gate())
            self.assertEqual(refused.exception.status, 503)
            self.assertEqual(refused.exception.code, "STUDIO_STOPPING")
            self.assertFalse(stopped.is_set())
            first.release.set()
            self.assertTrue(second.started.wait(2))
            self.assertFalse(stopped.is_set())
            second.release.set()
            self.assertTrue(stopped.wait(2))
        finally:
            first.release.set()
            second.release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.registry.get(first_job.job_id).status, SUCCEEDED)
        self.assertEqual(self.registry.get(second_job.job_id).status, SUCCEEDED)
        self.assertEqual(len(self.events.of("candidate.queued")), 2)

    def test_shutdown_keeps_a_job_accepted_before_its_submitter_can_admit_it(self) -> None:
        accepted, resume, stopped = (threading.Event() for _ in range(3))
        gate = Gate()
        original_admit = self.registry._admit

        def pause_admission():
            if threading.current_thread().name == "late-admission":
                accepted.set()
                if not resume.wait(5):
                    raise TimeoutError("admission was never resumed")
            original_admit()

        with patch.object(self.registry, "_admit", side_effect=pause_admission):
            submitter = threading.Thread(
                target=lambda: self.submit("accepted", gate), name="late-admission",
            )
            submitter.start()
            shutdown = threading.Thread(target=lambda: (self.registry.shutdown(), stopped.set()))
            try:
                self.assertTrue(accepted.wait(2))
                shutdown.start()
                self.assertTrue(wait_until(lambda: not self.registry.accepting))
                self.assertFalse(stopped.is_set())
                resume.set()
                self.assertTrue(gate.started.wait(2))
                gate.release.set()
                self.assertTrue(stopped.wait(2))
            finally:
                resume.set()
                gate.release.set()
                submitter.join(5)
                if shutdown.ident is not None:
                    shutdown.join(5)
        self.assertFalse(submitter.is_alive())
        self.assertFalse(shutdown.is_alive())
        self.assertEqual(self.registry.for_candidate("accepted").status, SUCCEEDED)


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


class JobKindTests(QueueTestCase):
    def test_a_job_is_followed_in_four_steps_as_its_work_reports_them(self) -> None:
        events = Recorder()
        registry = JobRegistry(events, max_workers=1)
        self.addCleanup(registry.shutdown)

        def work() -> None:
            report_step("generate", "running", "Building the model")
            report_step("generate", "done")
            report_step("render", "running")  # told wrong: dropped, and the work goes on
            report_step("check", "running", "Checking 131 conditions")

        job = registry.submit(candidate_id="c-steps", proposal_id="p-steps", work=work, title="Remove four braces")
        self.assertTrue(wait_until(lambda: registry.get(job.job_id).status == SUCCEEDED))
        self.assertEqual([(row.name, row.state) for row in registry.get(job.job_id).steps],
                         [("generate", "done"), ("check", "done"), ("repair", "skipped"), ("save", "skipped")],
                         "a step still running ends with the work, and one it never reached is skipped")
        reported = events.of("job.step")
        self.assertEqual([(event["step"], event["step_state"]) for event in reported],
                         [("generate", "running"), ("generate", "done"), ("check", "running"), ("check", "done"),
                          ("repair", "skipped"), ("save", "skipped")])
        self.assertEqual((reported[0]["title"], reported[0]["step_text"]), ("Remove four braces", "Building the model"))
        wire = to_dto({**events.of("candidate.succeeded")[0], "seq": 1, "at": "now"}).model_dump(by_alias=True)
        self.assertEqual((wire["lane"], wire["title"], [row["state"] for row in wire["steps"]]),
                         (PARALLEL, "Remove four braces", ["done", "done", "skipped", "skipped"]))

    def test_a_step_running_when_the_work_fails_fails_with_it(self) -> None:
        events = Recorder()
        registry = JobRegistry(events, max_workers=1)
        self.addCleanup(registry.shutdown)

        def work() -> None:
            report_step("generate", "running")
            raise ValueError("seat pack missing")

        job = registry.submit(candidate_id="c-fails", proposal_id="p-fails", work=work)
        self.assertTrue(wait_until(lambda: registry.get(job.job_id).status == "failed"))
        self.assertEqual([row.state for row in registry.get(job.job_id).steps],
                         ["failed", "skipped", "skipped", "skipped"])
        self.assertEqual([event["step_state"] for event in events.of("job.step")][1:],
                         ["failed", "skipped", "skipped", "skipped"])

    def test_a_job_whose_work_reports_nothing_has_no_steps(self) -> None:
        events = Recorder()
        registry = JobRegistry(events, max_workers=1)
        self.addCleanup(registry.shutdown)
        report_step("generate", "running")  # outside a job, nothing happens
        job = registry.submit(candidate_id="c-quiet", proposal_id="p-quiet", work=lambda: None)
        self.assertTrue(wait_until(lambda: registry.get(job.job_id).status == SUCCEEDED))
        self.assertEqual((registry.get(job.job_id).steps, events.of("job.step")), ((), []))
        with self.assertRaises(ValueError):
            registry.step(job.job_id, "render", "running")
        with self.assertRaises(ValueError):
            registry.step(job.job_id, "check", "almost")

    def test_a_queued_job_says_on_the_wire_what_it_waits_for(self) -> None:
        events = Recorder()
        registry = JobRegistry(events, max_workers=2)
        self.addCleanup(registry.shutdown)
        first = Gate()
        self.addCleanup(first.release.set)
        registry.submit(candidate_id="c-export", proposal_id="p-1", work=first, exclusive=True)
        self.assertTrue(first.started.wait(5))
        registry.submit(candidate_id="c-export-2", proposal_id="p-2", work=lambda: None, exclusive=True)
        waiting, = events.of("candidate.waiting")
        wire = to_dto({**waiting, "seq": 1, "at": "now"}).model_dump(by_alias=True)
        self.assertEqual((wire["lane"], wire["waitingFor"], wire["waitingReason"], wire["steps"]),
                         (EXCLUSIVE, "c-export", EXCLUSIVE_REASON, []), "a job's events carry its steps, none yet")
        other = to_dto({"seq": 2, "at": "now", "type": "validation.computed"}).model_dump(by_alias=True)
        self.assertIsNone(other["steps"], "an event that is not a job's has no steps")

    def test_a_job_whose_steps_cannot_be_settled_still_ends_with_its_own_reason(self) -> None:
        class Refusing(Recorder):
            def publish(self, *, event: dict) -> None:
                if event["type"] == "job.step" and event["step_state"] == "skipped":
                    raise RuntimeError("the event sink is gone")
                super().publish(event=event)

        registry = JobRegistry(Refusing(), max_workers=1)
        self.addCleanup(registry.shutdown)

        def work() -> None:
            report_step("generate", "running")
            raise ValueError("seat pack missing")

        job = registry.submit(candidate_id="c-unsettled", proposal_id="p-1", work=work)
        self.assertTrue(wait_until(lambda: registry.get(job.job_id).status == "failed"))
        self.assertEqual(registry.get(job.job_id).error, "seat pack missing")

    def test_a_job_whose_event_cannot_be_published_still_gives_its_lane_back(self) -> None:
        class Refusing(Recorder):
            def publish(self, *, event: dict) -> None:
                if event["type"] == "candidate.succeeded" and event["candidate_id"] == "c-first":
                    raise RuntimeError("the event sink is gone")
                super().publish(event=event)

        events = Refusing()
        registry = JobRegistry(events, max_workers=1)
        self.addCleanup(registry.shutdown)
        registry.submit(candidate_id="c-first", proposal_id="p-1", work=lambda: None, exclusive=True)
        second = registry.submit(candidate_id="c-second", proposal_id="p-2", work=lambda: None, exclusive=True)
        self.assertTrue(wait_until(lambda: registry.get(second.job_id).status == SUCCEEDED))

    def test_a_run_keeps_its_published_event_names_and_another_kind_says_what_it_is(self) -> None:
        # A run is the job's own kind now (#294); its lifecycle events keep the names
        # the web client and the Hub relay read, and an export is named an export.
        run, export = Gate(), Gate()
        run.release.set()
        export.release.set()
        self.assertEqual(self.submit("run-1", run).kind, RUN)
        self.registry.submit(candidate_id="export-1", proposal_id="export-1", work=export, kind="export")
        self.assertTrue(wait_until(lambda: len(self.events.of("candidate.succeeded")) == 1
                                   and len(self.events.of("export.succeeded")) == 1))
        published = {name: [event["type"] for event in self.events.events if event["candidate_id"] == name]
                     for name in ("run-1", "export-1")}
        self.assertEqual(published, {"run-1": ["candidate.queued", "candidate.running", "candidate.succeeded"],
                                     "export-1": ["export.queued", "export.running", "export.succeeded"]})


if __name__ == "__main__":
    unittest.main()
