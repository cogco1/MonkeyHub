"""One revision in one call (#185): the order of the checks, and what stops a change before it lands.

The first half drives ``revise`` through ports that answer as the routes do, so every
stop is shown on its own, with a counter-example for each way around a check that a
review found. The second half sends ``POST /api/revisions`` to a real Runtime that
builds with OCCT and reads the model back, and shows the design-loop setting refusing
the Agent's own way onto the working design, and changing nothing while it is off.
"""

from __future__ import annotations

import json

from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from typing import Any, Mapping

from fastapi.testclient import TestClient

from archflow.state.operational_state import DesignObligation
from archflow.state.state_record import Entity, StateRecord
from monkeycad.backends.occt.kernel import occt_available
from project_runtime.application.revision_transaction import (
    ADMITTED, BUILT, LANDED, NEEDS_QUESTION, PROBLEMS, REFUSED, Decision, RevisionRequest, revise,
)
from project_runtime.authentication import request_action
from project_runtime.errors import StudioError
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import (
    PROJECT_ID, RUNNER_RECORD_PATH, make_empty_project, make_project, retain_runner_receipt, runner_state_digest,
)


def _obligation(obligation_id: str, validator_ref: str | None, *subjects: str, statement: str = "") -> DesignObligation:
    return DesignObligation(obligation_id, statement or f"{obligation_id} as the architect said it", "decision:opening",
                            subject_refs=subjects, validator_ref=validator_ref)


def _record(*obligations: DesignObligation) -> StateRecord:
    entities = (Entity("house", "Component@1", {"semantic_kind": "whole-building", "intent": "house"}),
                Entity("stair", "Element@1", {"component_id": "house", "producer": "stair", "references": {},
                                              "params": {"rise": 0.17}}, parent_id="house"),
                Entity("slab", "Element@1", {"component_id": "house", "producer": "prism", "references": {},
                                             "params": {"height": 0.2}}, parent_id="house"))
    return StateRecord("demo", "run-1", entities, (), (), obligations)


class FakePorts:
    """Answers as the routes do; ``calls`` records what the transaction asked, in order."""

    def __init__(self, *, base: StateRecord | None = None, built: StateRecord | None = None,
                 decisions: tuple[Decision, ...] = (), direct: tuple[str, ...] = ("entity:stair",),
                 propagated: tuple[str, ...] = (), status: str = "proposed", conflicts: tuple[str, ...] = (),
                 job: str = "succeeded", objects: list | None = None, compare: Mapping[str, Any] | None = None,
                 compare_error: StudioError | None = None, never_exported: bool = False,
                 validation: Mapping[str, Any] | None = None, obligations: Mapping[str, Any] | None = None,
                 propose_error: StudioError | None = None, source: tuple | None = None) -> None:
        self.head = ("run-1", "digest-1", "rev-1")
        self.base, self.built = base or _record(), built
        self._decisions, self.direct, self.propagated = decisions, direct, propagated
        self.status, self.conflicts = status, conflicts
        self.job_status, self.objects = job, [{"name": "obj-stair"}] if objects is None else objects
        self.compare_answer = compare if compare is not None else {"changed": 1, "unchanged": 4, "added": 0, "removed": 0,
                                                                   "objects": [{"name": "obj-stair", "status": "changed"}]}
        self.compare_error, self._never_exported = compare_error, never_exported
        self.validation_answer = validation or {"blockedBy": [], "obligationProblems": [], "honesty": []}
        self.obligation_answer = obligations or {"results": [], "new": []}
        self.propose_error, self.source = propose_error, source or ("run-1", "digest-1")
        self.calls: list[str] = []
        self.admitted: list[Mapping[str, Any]] = []
        self.proposed: list[Mapping[str, Any]] = []
        self.on_build = None

    def working_head(self):
        return self.head

    def record(self, run_id):
        return self.base if run_id == "run-1" else (self.built or self.base)

    def decisions(self, run_id):
        return self._decisions

    def propose(self, kind, body):
        self.calls.append(f"propose {kind}")
        self.proposed.append(body)
        if self.propose_error is not None:
            raise self.propose_error
        return {"proposalId": "prop-1", "status": self.status,
                "impact": {"direct": list(self.direct), "propagated": list(self.propagated),
                           "conflicts": list(self.conflicts)}}

    def run(self, proposal_id):
        self.calls.append("run")
        return {"candidateId": "cand-1", "jobId": "job-1"}

    def job(self, job_id):
        if self.on_build is not None:
            self.on_build()
        return {"status": self.job_status, "error": "the seat failed" if self.job_status == "failed" else None}

    def source_of(self, candidate_id):
        return self.source

    def candidate(self, candidate_id):
        self.calls.append("candidate")
        return {"candidateId": candidate_id, "stateDigest": "digest-2", "objects": self.objects,
                "objectReadbackError": None, "relationProblems": []}

    def compare(self, candidate_id, against):
        self.calls.append("compare")
        if self.compare_error is not None:
            raise self.compare_error
        return self.compare_answer

    def never_exported(self, run_id):
        return self._never_exported

    def validation(self, candidate_id):
        self.calls.append("validation")
        return self.validation_answer

    def obligations(self, candidate_id):
        return self.obligation_answer

    def admit(self, body):
        self.calls.append("admit")
        self.admitted.append(body)
        return {}

    def continue_(self, run_id, revision):
        self.calls.append(f"continue {revision}")
        self.head = (run_id, "digest-2", "rev-2")
        return {}


def _request(**overrides) -> RevisionRequest:
    fields = dict(project_id="demo", state_digest="digest-1", source_run_id="run-1", kind="construction",
                  proposal={"script": "box('stair')"}, summary="make the stair less steep")
    fields.update(overrides)
    return RevisionRequest(**fields)


def _revise(ports: FakePorts, **overrides):
    return revise(_request(**overrides), ports, sleep=lambda _s: None)


class RevisionOrderTests(unittest.TestCase):
    def test_a_change_that_passes_every_check_lands(self) -> None:
        ports = FakePorts(decisions=(Decision("d1", "keep", "entity:slab", "楼板不动"),))
        outcome = _revise(ports)
        self.assertEqual((outcome.outcome, outcome.code), (LANDED, "LANDED"))
        self.assertEqual(ports.calls, ["propose construction", "run", "candidate", "compare", "validation", "admit",
                                       "continue rev-1"])
        self.assertEqual(ports.proposed[0]["keep"], ["entity:slab"], "every keep decision is protected")
        self.assertEqual(ports.proposed[0]["summary"], "make the stair less steep")
        self.assertEqual(outcome.working, {"runId": "cand-1", "stateDigest": "digest-2", "revisionSha256": "rev-2"})
        [admission] = ports.admitted
        self.assertEqual(admission["task"]["kind"], "ui", "no bound chat message: the caller is a person or a client")

    def test_an_agent_change_is_admitted_on_the_bound_message(self) -> None:
        ports = FakePorts()
        message = {"sessionId": "chat-1", "messageId": "m-1"}
        outcome = _revise(ports, message_source=message, raw_language="楼梯缓一点")
        self.assertEqual(outcome.outcome, LANDED)
        [admission] = ports.admitted
        self.assertEqual((admission["task"]["kind"], admission["messageSource"], admission["rawLanguage"]),
                         ("hub-chat", message, "楼梯缓一点"))

    def test_land_false_admits_without_moving_the_working_design(self) -> None:
        ports = FakePorts()
        outcome = _revise(ports, land=False)
        self.assertEqual(outcome.outcome, ADMITTED)
        self.assertNotIn("continue rev-1", ports.calls)
        self.assertEqual(outcome.working["runId"], "run-1")

    def test_build_only_checks_and_admits_nothing_then_the_same_candidate_lands(self) -> None:
        ports = FakePorts()
        built = _revise(ports, build_only=True)
        self.assertEqual((built.outcome, built.candidate_id), (BUILT, "cand-1"))
        self.assertNotIn("admit", ports.calls)
        landed = _revise(ports, kind=None, proposal=None, candidate_id="cand-1")
        self.assertEqual(landed.outcome, LANDED)
        self.assertEqual(ports.calls.count("run"), 1, "nothing is built again")

    def test_a_candidate_built_from_another_design_is_not_landed(self) -> None:
        outcome = _revise(FakePorts(source=("run-0", "digest-0")), kind=None, proposal=None, candidate_id="opt-1")
        self.assertEqual((outcome.outcome, outcome.code), (REFUSED, "STALE_BASE"))

    def test_a_stale_base_is_refused_before_anything_is_proposed(self) -> None:
        ports = FakePorts()
        outcome = _revise(ports, state_digest="digest-0")
        self.assertEqual((outcome.outcome, outcome.code), (REFUSED, "STALE_BASE"))
        self.assertEqual(ports.calls, [])

    def test_a_project_with_no_working_run_starts_from_its_default_design(self) -> None:
        ports = FakePorts()
        ports.head = (None, "digest-1", None)
        ports.record = lambda run_id: ports.base
        outcome = _revise(ports, source_run_id=None)
        self.assertEqual(outcome.outcome, LANDED)
        self.assertNotIn("sourceRunId", ports.proposed[0], "the default design is proposed against without a run")

    def test_the_working_design_moving_during_the_build_admits_nothing(self) -> None:
        ports = FakePorts()

        def someone_else_lands() -> None:
            ports.head = ("run-b", "digest-b", "rev-b")

        ports.on_build = someone_else_lands
        outcome = _revise(ports)
        self.assertEqual((outcome.outcome, outcome.code), (PROBLEMS, "WORKING_DESIGN_MOVED"))
        self.assertNotIn("admit", ports.calls)
        self.assertEqual(ports.head[0], "run-b", "the other change stays")

    def test_a_keep_conflict_asks_and_builds_nothing(self) -> None:
        ports = FakePorts(decisions=(Decision("d1", "keep", "entity:slab", "楼板不动"),), status="conflict",
                          conflicts=("entity:slab",))
        outcome = _revise(ports)
        self.assertEqual((outcome.outcome, outcome.code), (NEEDS_QUESTION, "KEEP_CONFLICT"))
        self.assertIn("楼板不动", outcome.detail)
        self.assertNotIn("run", ports.calls)

    def test_touching_a_rejected_option_asks_in_the_architects_words(self) -> None:
        ports = FakePorts(decisions=(Decision("d2", "reject", "entity:stair", "不要螺旋楼梯"),))
        outcome = _revise(ports)
        self.assertEqual((outcome.outcome, outcome.code), (NEEDS_QUESTION, "REJECTED_OPTION"))
        self.assertIn("不要螺旋楼梯", outcome.detail)
        self.assertNotIn("run", ports.calls)

    def test_changing_a_part_touches_the_rejected_whole(self) -> None:
        ports = FakePorts(decisions=(Decision("d3", "avoid", "entity:house", "别再改这栋房子"),))
        self.assertEqual(_revise(ports).code, "REJECTED_OPTION")
        apart = FakePorts(decisions=(Decision("d3", "avoid", "entity:slab", "楼板别动"),))
        self.assertEqual(_revise(apart).outcome, LANDED, "a sibling part is not touched")

    def test_a_parameter_that_drives_a_rejected_part_touches_it(self) -> None:
        ports = FakePorts(decisions=(Decision("d4", "reject", "entity:stair", "不要螺旋楼梯"),),
                          direct=("parameter:rise",), propagated=("entity:stair",))
        self.assertEqual(_revise(ports).code, "REJECTED_OPTION")

    def test_a_condition_in_reach_that_no_check_can_verify_asks_before_building(self) -> None:
        said = _obligation("k3", None, "entity:stair", statement="楼梯要舒服")
        checked = _obligation("rise", "validator:element-param-at-most/rise/0.18", "entity:stair")
        far = _obligation("far", None, "entity:slab")
        ports = FakePorts(base=_record(said, checked, far))
        outcome = _revise(ports)
        self.assertEqual((outcome.outcome, outcome.code), (NEEDS_QUESTION, "UNCHECKED_CONDITION"))
        self.assertEqual(outcome.question_refs, ("obligation:k3",), "a condition out of reach is not asked about")
        self.assertEqual({row["obligationId"]: row["checkable"] for row in outcome.conditions}, {"k3": False, "rise": True})
        self.assertNotIn("run", ports.calls)

    def test_the_architects_confirmation_lets_an_uncheckable_condition_pass_and_is_recorded(self) -> None:
        ports = FakePorts(base=_record(_obligation("k3", None, "entity:stair")))
        outcome = _revise(ports, confirmed=("obligation:k3",), raw_language="可以，舒服的")
        self.assertEqual(outcome.outcome, LANDED)
        self.assertIn("obligation:k3", ports.admitted[0]["results"][0]["reason"])

    def test_a_confirmation_needs_the_architects_words_and_names_only_conditions(self) -> None:
        self.assertEqual(_revise(FakePorts(), confirmed=("obligation:k3",)).code, "CONFIRMATION_INVALID")
        self.assertEqual(_revise(FakePorts(), confirmed=("entity:slab",), raw_language="可以").code, "CONFIRMATION_INVALID")

    def test_a_condition_the_built_model_cannot_check_asks_after_building(self) -> None:
        measured = _obligation("k6", "validator:headroom-at-least/2.0", "entity:stair")
        ports = FakePorts(base=_record(measured), obligations={"new": [], "results": [
            {"obligationId": "k6", "status": "unchecked", "detail": "stair has no inspected object"}]})
        outcome = _revise(ports)
        self.assertEqual((outcome.outcome, outcome.code), (NEEDS_QUESTION, "UNCHECKED_CONDITION"))
        self.assertIn("stair has no inspected object", outcome.detail)
        self.assertNotIn("admit", ports.calls)

    def test_the_whole_result_decides_not_a_shortened_list(self) -> None:
        old = [_obligation(f"old-{n}", "validator:element-param-at-most/rise/0.10", "entity:slab") for n in range(25)]
        measured = _obligation("k6", "validator:headroom-at-least/2.0", "entity:stair")
        results = [{"obligationId": f"old-{n}", "status": "violated", "detail": "already broken"} for n in range(25)]
        results.append({"obligationId": "k6", "status": "unchecked", "detail": "no inspected object"})
        shown = {"blockedBy": [], "honesty": [], "obligationProblems": [
            {"obligationId": row["obligationId"], "status": row["status"], "new": False, "detail": row["detail"]}
            for row in results[:20]]}
        ports = FakePorts(base=_record(*old, measured), validation=shown, obligations={"new": [], "results": results})
        self.assertEqual(_revise(ports).code, "UNCHECKED_CONDITION", "the 26th result is read too")

    def test_a_change_that_drops_a_condition_asks(self) -> None:
        base = _record(_obligation("rise", "validator:element-param-at-most/rise/0.18", "entity:stair"))
        ports = FakePorts(base=base, built=replace(base, obligations=()))
        outcome = _revise(ports)
        self.assertEqual((outcome.outcome, outcome.code), (NEEDS_QUESTION, "CONDITION_CHANGED"))
        self.assertNotIn("admit", ports.calls)

    def test_a_new_violation_is_a_problem_named_by_its_condition(self) -> None:
        # Review readiness holds: conditions are no clause of it, and the revision refuses the violation itself.
        ports = FakePorts(base=_record(_obligation("rise", "validator:element-param-at-most/rise/0.18", "entity:stair")),
                          validation={"blockedBy": [], "honesty": [], "obligationProblems": []},
                          obligations={"new": ["rise"], "results": [
                              {"obligationId": "rise", "status": "violated", "detail": "stair rise 0.2000 <= 0.1800"}]})
        outcome = _revise(ports)
        self.assertEqual((outcome.outcome, outcome.code), (PROBLEMS, "CHECKS_FAILED"))
        self.assertEqual([(p.source, p.kind) for p in outcome.problems], [("obligation:rise", "violated")])
        self.assertNotIn("admit", ports.calls)

    def test_a_violated_relation_is_a_problem(self) -> None:
        ports = FakePorts(validation={"blockedBy": ["relations.held"], "honesty": [], "obligationProblems": []})
        ports.candidate = lambda cid: {"candidateId": cid, "stateDigest": "d", "objects": [{"name": "o"}],
                                       "objectReadbackError": None, "relationProblems": [
                                           {"relationId": "rel-1", "status": "violated", "kind": "support_contact",
                                            "detail": "gap 0.3000 m"}]}
        outcome = _revise(ports)
        self.assertEqual(outcome.outcome, PROBLEMS)
        self.assertEqual(outcome.problems[0].source, "relation:rel-1")

    def test_a_model_that_reads_back_nothing_or_did_not_change_is_a_problem(self) -> None:
        self.assertEqual(_revise(FakePorts(objects=[])).problems[0].kind, "OBJECT_READBACK")
        still = FakePorts(compare={"changed": 0, "unchanged": 5, "added": 0, "removed": 0, "objects": []})
        self.assertEqual(_revise(still).problems[0].kind, "NO_VISIBLE_CHANGE")
        self.assertEqual(_revise(FakePorts(compare={"changed": 0, "unchanged": 5, "added": 0, "removed": 0, "objects": []}),
                                 kind="facets").outcome, LANDED, "meaning alone changes no geometry")

    def test_only_a_base_that_never_exported_is_excused_from_the_comparison(self) -> None:
        missing = StudioError(404, "INSPECTION_NOT_FOUND", "run-1 names a record that is not retained")
        broken = _revise(FakePorts(compare_error=missing, never_exported=False))
        self.assertEqual((broken.outcome, broken.problems[0].kind), (PROBLEMS, "COMPARE_UNAVAILABLE"))
        fresh = _revise(FakePorts(compare_error=StudioError(404, "INSPECTION_NOT_FOUND", "exported nothing"),
                                  never_exported=True))
        self.assertEqual(fresh.outcome, LANDED)
        self.assertTrue(fresh.compare["baseHasNoModel"])

    def test_a_failed_build_and_a_refused_proposal_are_problems(self) -> None:
        self.assertEqual(_revise(FakePorts(job="failed")).code, "BUILD_FAILED")
        refused = _revise(FakePorts(propose_error=StudioError(422, "CONSTRUCTION_INVALID", "line 1: unknown name")))
        self.assertEqual((refused.outcome, refused.code), (PROBLEMS, "PROPOSAL_REFUSED"))

    def test_a_refused_continue_reports_where_the_working_design_really_stands(self) -> None:
        ports = FakePorts()

        def stale(run_id, revision):
            raise StudioError(409, "WORKING_DRAFT_STALE", "The working position changed.")

        ports.continue_ = stale
        outcome = _revise(ports)
        self.assertEqual((outcome.outcome, outcome.code), (PROBLEMS, "CONTINUE_REFUSED"))
        self.assertEqual(outcome.working["runId"], "run-1")

        moved = FakePorts()

        def half_done(run_id, revision):
            moved.head = (run_id, "digest-2", "rev-2")
            raise StudioError(500, "CONTINUE_NOT_RETAINED", "The Working Head moved, but who continued could not be retained.")

        moved.continue_ = half_done
        outcome = _revise(moved)
        self.assertEqual((outcome.outcome, outcome.code), (LANDED, "LANDED_UNRECORDED"))
        self.assertEqual(outcome.working["runId"], "cand-1")

        lost = FakePorts()
        reads = iter([lost.head, lost.head, lost.head])

        def unreadable():
            try:
                return next(reads)
            except StopIteration:
                raise StudioError(503, "WORKING_SOURCE_UNAVAILABLE", "cannot read") from None

        lost.working_head = unreadable
        lost.continue_ = stale
        self.assertEqual(_revise(lost).code, "CONTINUE_UNKNOWN")

    def test_a_build_that_does_not_finish_in_time_moves_nothing(self) -> None:
        ports = FakePorts(job="running")
        ticks = iter(range(0, 10_000, 50))
        outcome = revise(_request(wait_s=60), ports, clock=lambda: float(next(ticks)), sleep=lambda _s: None)
        self.assertEqual((outcome.outcome, outcome.code, outcome.candidate_id), (PROBLEMS, "RUN_TIMEOUT", "cand-1"))
        self.assertNotIn("admit", ports.calls)

    def test_landing_takes_the_accept_grant(self) -> None:
        self.assertEqual(request_action("POST", "/api/revisions", shared_project=False), "accept")


AGENT = {"X-Monkey-Agent": "chat-1", "X-Monkey-Worker": "hub-1"}
MESSAGE = {"sessionId": "chat-1", "messageId": "m-1"}


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class RevisionRouteTests(unittest.TestCase):
    """POST /api/revisions on a real Runtime: built with OCCT, read back, admitted and continued."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.project = self.root / PROJECT_ID

    def runtime(self, design_loop: str = "enforced", *, managed: bool = False, project: Path | None = None,
                reference_run: str | None = None) -> TestClient:
        app = create_app(StudioSettings(cad_export="occt", project_dir=project or self.project, design_loop=design_loop,
                                        reference_run=reference_run))
        if managed:
            app.state.managed_instance_id = "hub-1"
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def head(self, client: TestClient) -> dict:
        response = client.get("/api/working-source")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def revise(self, client: TestClient, height: float = 2.2, headers: dict | None = None, **body: object) -> dict:
        head = self.head(client)["head"]
        response = client.post("/api/revisions", headers=headers or {}, json={
            "projectId": PROJECT_ID, "sourceRunId": head["runId"], "stateDigest": head["stateDigest"],
            "kind": "proposal", "summary": f"base height {height}",
            "proposal": {"utterance": f"set height to {height}", "targetComponentId": "portico",
                         "elementId": "portico-base"}, **body})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def candidate(self, client: TestClient, height: float = 2.2) -> str:
        built = client.post("/api/proposals", json={"stateDigest": self.head(client)["head"]["stateDigest"],
                                                    "utterance": f"set height to {height}", "targetComponentId": "portico",
                                                    "elementId": "portico-base"}).json()
        started = client.post(f"/api/proposals/{built['proposalId']}/candidate").json()
        deadline = time.monotonic() + 120
        while client.get(f"/api/jobs/{started['jobId']}").json()["status"] not in ("succeeded", "failed"):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        return started["candidateId"]

    def test_a_change_lands_on_the_working_design_after_its_model_is_read_back(self) -> None:
        client = self.runtime()
        answer = self.revise(client)
        self.assertEqual(answer["outcome"], "landed", answer)
        self.assertEqual(self.head(client)["head"]["runId"], answer["candidateId"])
        self.assertTrue(answer["compare"]["baseHasNoModel"], "the project's start was never exported")
        second = self.revise(client, 2.6)
        self.assertEqual(second["outcome"], "landed", second)
        moved = {row["name"]: row["status"] for row in second["compare"]["objects"]}
        self.assertEqual(moved.get("obj-portico-base"), "changed", second["compare"])

    def test_an_empty_project_takes_its_first_change_through_the_one_call(self) -> None:
        empty = make_empty_project(self.root / "empty")
        client = self.runtime(project=empty.layout.root)
        base = client.post("/api/project/modeling?base=true", json={"projectId": empty.layout.root.name}).json()
        self.assertIsNone(self.head(client)["head"])
        response = client.post("/api/revisions", json={
            "projectId": empty.layout.root.name, "sourceRunId": None, "stateDigest": base["stateDigest"],
            "kind": "construction", "summary": "a first volume",
            "proposal": {"script": "mass = extrude(rect(0, 0, 6, 4), 3)"}})
        answer = response.json()
        self.assertEqual(answer["outcome"], "landed", answer)
        self.assertEqual(self.head(client)["head"]["runId"], answer["candidateId"])

    def test_a_kept_target_stops_the_change_before_anything_is_built(self) -> None:
        client = self.runtime()
        source = self.head(client)["head"]
        saved = client.post("/api/decisions", json={
            "projectId": PROJECT_ID, "rawLanguage": "基座别动", "disposition": "keep", "strength": "hard",
            "targetRef": "entity:portico-base", "scope": {"domain": "design", "extent": "project"},
            "applicability": "scope", "sourceKind": "human",
            "source": {"kind": "design", "sourceRunId": source["runId"], "stateDigest": source["stateDigest"],
                       "sourceStageRef": None}})
        self.assertEqual(saved.status_code, 201, saved.text)
        runs = {path.name for path in (self.project / "runs").iterdir()}
        answer = self.revise(client)
        self.assertEqual((answer["outcome"], answer["code"]), ("needs_question", "KEEP_CONFLICT"), answer)
        self.assertIn("基座别动", answer["detail"])
        self.assertEqual({path.name for path in (self.project / "runs").iterdir()}, runs, "nothing was built")

    def test_the_context_names_the_working_design_its_decisions_and_the_last_change(self) -> None:
        client = self.runtime()
        self.revise(client, 2.2)
        source = self.head(client)["head"]
        saved = client.post("/api/decisions", json={
            "projectId": PROJECT_ID, "rawLanguage": "不要拱廊", "disposition": "reject", "strength": "hard",
            "targetRef": "entity:portico", "scope": {"domain": "design", "extent": "project"},
            "applicability": "scope", "sourceKind": "human",
            "source": {"kind": "design", "sourceRunId": source["runId"], "stateDigest": source["stateDigest"],
                       "sourceStageRef": None}})
        self.assertEqual(saved.status_code, 201, saved.text)
        second = self.revise(client, 2.6)
        self.assertEqual((second["outcome"], second["code"]), ("needs_question", "REJECTED_OPTION"), second)
        context = client.get("/api/revisions/context").json()
        self.assertEqual(context["working"]["runId"], source["runId"])
        self.assertEqual([row["words"] for row in context["rejected"]], ["不要拱廊"])
        self.assertEqual(context["lastChange"]["summary"], "base height 2.2")

    def test_an_option_built_from_the_working_design_lands_by_its_candidate_id(self) -> None:
        client = self.runtime()
        built = self.revise(client, 2.4, buildOnly=True)
        self.assertEqual(built["outcome"], "built", built)
        self.assertEqual(client.get("/api/admissions").json()["admissions"], [], "nothing admitted yet")
        head = self.head(client)["head"]
        landed = client.post("/api/revisions", json={
            "projectId": PROJECT_ID, "sourceRunId": head["runId"], "stateDigest": head["stateDigest"],
            "candidateId": built["candidateId"], "summary": "base height 2.4"}).json()
        self.assertEqual((landed["outcome"], landed["candidateId"]), ("landed", built["candidateId"]), landed)

    def test_a_stale_base_is_refused(self) -> None:
        client = self.runtime()
        head = self.head(client)["head"]
        response = client.post("/api/revisions", json={
            "projectId": PROJECT_ID, "sourceRunId": head["runId"], "stateDigest": "0" * 64, "kind": "proposal",
            "summary": "x", "proposal": {"utterance": "set height to 2.2", "targetComponentId": "portico"}})
        self.assertEqual(response.json()["code"], "STALE_BASE", response.text)

    def test_while_enforced_the_agent_reaches_the_working_design_only_through_the_one_call(self) -> None:
        agent = self.runtime(managed=True)
        run_id = self.candidate(agent)
        for body in ({"task": {"kind": "hub-chat"}, "messageSource": MESSAGE},
                     {"task": {"kind": "ui"}}):  # what the Agent says about itself in the body does not count
            admitted = agent.post("/api/admissions", headers=AGENT, json={
                "projectId": PROJECT_ID, "results": [{"runId": run_id, "outcome": "admitted"}], **body})
            self.assertEqual((admitted.status_code, admitted.json()["code"]), (409, "DESIGN_LOOP_REQUIRED"), admitted.text)
        for body in ({"messageSource": MESSAGE, "rawLanguage": "就这样"}, {}):
            moved = agent.put("/api/working-draft", headers=AGENT, json={
                "projectId": PROJECT_ID, "runId": run_id, "baseRevisionSha256": self.head(agent)["revisionSha256"], **body})
            self.assertEqual((moved.status_code, moved.json()["code"]), (409, "DESIGN_LOOP_REQUIRED"), moved.text)
        self.assertNotEqual(self.head(agent)["head"]["runId"], run_id)

        refused = agent.post("/api/revisions", headers=AGENT, json={
            "projectId": PROJECT_ID, "sourceRunId": self.head(agent)["head"]["runId"],
            "stateDigest": self.head(agent)["head"]["stateDigest"], "kind": "proposal", "summary": "x",
            "proposal": {"utterance": "set height to 2.4", "targetComponentId": "portico", "elementId": "portico-base"},
            "confirmed": ["obligation:anything"], "messageSource": MESSAGE, "rawLanguage": "加宽楼梯"})
        self.assertEqual((refused.status_code, refused.json()["code"]), (422, "REVISION_INVALID"), refused.text)

        answer = self.revise(agent, 2.4, headers=AGENT, messageSource=MESSAGE, rawLanguage="基座再高一点")
        self.assertEqual(answer["outcome"], "landed", answer)
        self.assertEqual(self.head(agent)["head"]["runId"], answer["candidateId"])

    def test_the_agent_carries_a_confirmation_only_with_the_hubs_word_for_it(self) -> None:
        agent = self.runtime(managed=True, reference_run=self.conditioned(None))
        asked = self.revise(agent, 2.4, headers=AGENT, messageSource=MESSAGE, rawLanguage="基座再高一点")
        self.assertEqual((asked["outcome"], asked["code"]), ("needs_question", "UNCHECKED_CONDITION"), asked)
        self.assertEqual(asked["questionRefs"], ["obligation:base-height-limit"])
        later = {"sessionId": "chat-1", "messageId": "m-2"}
        unvouched = agent.post("/api/revisions", headers=AGENT, json={
            "projectId": PROJECT_ID, "sourceRunId": self.head(agent)["head"]["runId"],
            "stateDigest": self.head(agent)["head"]["stateDigest"], "kind": "proposal", "summary": "x",
            "proposal": {"utterance": "set height to 2.4", "targetComponentId": "portico", "elementId": "portico-base"},
            "confirmed": asked["questionRefs"], "messageSource": later, "rawLanguage": "可以，抬吧"})
        self.assertEqual((unvouched.status_code, unvouched.json()["code"]), (422, "REVISION_INVALID"), unvouched.text)
        answer = self.revise(agent, 2.4, headers={**AGENT, "X-Monkey-Confirmation": "operation-1"},
                             messageSource=later, rawLanguage="可以，抬吧", confirmed=asked["questionRefs"])
        self.assertEqual(answer["outcome"], "landed", answer)

    def test_a_person_through_the_same_hub_is_not_the_agent(self) -> None:
        person = self.runtime(managed=True)
        run_id = self.candidate(person)
        moved = person.put("/api/working-draft", json={
            "projectId": PROJECT_ID, "runId": run_id, "baseRevisionSha256": self.head(person)["revisionSha256"]})
        self.assertEqual(moved.status_code, 200, moved.text)

    def test_legacy_answers_everything_as_before(self) -> None:
        agent = self.runtime("legacy", managed=True)
        run_id = self.candidate(agent)
        candidate = agent.get(f"/api/candidates/{run_id}").json()
        validation = agent.get(f"/api/candidates/{run_id}/validation").json()
        self.assertFalse({"relationProblems", "obligationProblems"} & set(candidate))
        self.assertFalse({"obligationProblems", "obligationsChecked"} & set(validation))
        self.assertNotIn("obligations.held", validation["blockedBy"])
        refused = agent.post("/api/revisions", json={"projectId": PROJECT_ID, "sourceRunId": None, "stateDigest": "x",
                                                     "kind": "proposal", "proposal": {}, "summary": "x"})
        self.assertEqual(refused.json()["code"], "DESIGN_LOOP_NOT_ENFORCED")
        admitted = agent.post("/api/admissions", headers=AGENT, json={
            "projectId": PROJECT_ID, "task": {"kind": "hub-chat"}, "messageSource": MESSAGE,
            "results": [{"runId": run_id, "outcome": "admitted"}]})
        self.assertNotEqual(admitted.json().get("code"), "DESIGN_LOOP_REQUIRED", admitted.text)
        moved = agent.put("/api/working-draft", headers=AGENT, json={
            "projectId": PROJECT_ID, "runId": run_id, "baseRevisionSha256": self.head(agent)["revisionSha256"],
            "messageSource": MESSAGE, "rawLanguage": "就这样"})
        self.assertEqual(moved.status_code, 200, moved.text)


    def conditioned(self, validator_ref: str | None) -> str:
        """A run whose record says the portico base keeps a condition; the design starts from it."""

        payload = json.loads(self.repository.layout.resolve_relative(RUNNER_RECORD_PATH).read_text(encoding="utf-8"))
        payload["obligations"] = [_obligation("base-height-limit", validator_ref, "entity:portico-base").to_dict()]
        run = self.repository.create_run("conditioned")
        retain_runner_receipt(self.repository, run, record_payload=payload,
                              design_state_digest=runner_state_digest(self.repository, run.run_id, payload))
        return run.run_id

    def test_advisory_lists_a_new_violation_and_blocks_nothing_on_the_old_ways_onto_the_working_design(self) -> None:
        agent = self.runtime("advisory", managed=True, reference_run=self.conditioned("validator:element-param-at-most/height/2.0"))
        run_id = self.candidate(agent)
        candidate = agent.get(f"/api/candidates/{run_id}").json()
        validation = agent.get(f"/api/candidates/{run_id}/validation").json()
        self.assertIn("relationProblems", candidate)
        [problem] = candidate["obligationProblems"]
        self.assertEqual((problem["obligationId"], problem["status"], problem["new"]), ("base-height-limit", "violated", True))
        self.assertEqual(validation["obligationProblems"], candidate["obligationProblems"])
        self.assertIs(validation["reviewReady"], True, validation["blockedBy"])
        refused = agent.post("/api/revisions", json={"projectId": PROJECT_ID, "sourceRunId": None, "stateDigest": "x",
                                                     "kind": "proposal", "proposal": {}, "summary": "x"})
        self.assertEqual(refused.json()["code"], "DESIGN_LOOP_NOT_ENFORCED")
        admitted = agent.post("/api/admissions", headers=AGENT, json={
            "projectId": PROJECT_ID, "task": {"kind": "hub-chat"}, "messageSource": MESSAGE,
            "results": [{"runId": run_id, "outcome": "admitted"}]})
        self.assertEqual(admitted.status_code, 201, admitted.text)
        self.assertEqual(admitted.json()["actor"]["origin"], "hub-agent")
        moved = agent.put("/api/working-draft", headers=AGENT, json={
            "projectId": PROJECT_ID, "runId": run_id, "baseRevisionSha256": self.head(agent)["revisionSha256"],
            "messageSource": MESSAGE, "rawLanguage": "就这样"})
        self.assertEqual(moved.status_code, 200, moved.text)


if __name__ == "__main__":
    unittest.main()
