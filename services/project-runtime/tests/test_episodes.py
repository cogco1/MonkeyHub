"""The judgement is retained: what was on the table, what was chosen, and why.

Nothing here rehearses a decision. The project is a real P036 project, the
proposals are made through the deterministic seam the studio actually uses, and
judgements are written into a real candidate run — so what these tests read
afterwards is a ``deliberation-episode`` record on disk, not something the API
remembered about its own work.

The asymmetry is the whole point. A rejection and a modification happen before
any run exists: they are held in this process, they say so, and they are lost on
restart. They stop being lost the moment a candidate runs against the same
state, because the reasons behind whichever proposal the architect finally
chooses belong in a run that answers for it.

Running a candidate is not choosing it. The route that runs one makes no
judgement on the proposal and closes none of the others. Accepting is the
architect's own act — ``decision: accepted`` naming the ``candidateId`` of the
finished run being chosen — and it is refused, leaving no judgement, whenever
that run cannot be bound to this proposal in this process.

The last test is the one that would catch the worst bug: retaining a judgement
must not touch the authored record. The record is read before and after, byte
for byte.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.application import episodes
from archflow_studio_api.application.binding import record_kind
from archflow_studio_api.application.episodes import SCHEMA
from archflow_studio_api.application.proposals import PERSISTENCE
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    EVIDENCE,
    PROJECT_ID,
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    RUNNER_RECORD_PATH,
    make_project,
    retain_runner_receipt,
    run_records,
    runner_state_digest,
)

# A run of this fixture takes well under a second; the ceiling stops a wedged
# worker from hanging the suite instead of failing it.
JOB_DEADLINE = 120.0
TERMINAL = ("succeeded", "failed")

EPISODE_KIND = "deliberation-episode"


class EpisodeTestCase(unittest.TestCase):
    """One real project, one client, and the proposals it can be given."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.app = create_app(
            StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID)
        )
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(
            self.repository, REFERENCE_RUN_ID
        )

    # ---- what a client does

    def propose(self, utterance: str, element_id: str = "portico-base") -> dict:
        response = self.client.post(
            "/api/proposals",
            json={
                "stateDigest": self.state_digest,
                "targetComponentId": "portico",
                "elementId": element_id,
                "utterance": utterance,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def decide(self, proposal_id: str, **body: object) -> tuple[int, dict]:
        response = self.client.post(
            f"/api/proposals/{proposal_id}/decision", json=body
        )
        return response.status_code, response.json()

    def run_candidate(self, proposal_id: str) -> dict:
        """Start the candidate and wait for the job that runs it to finish."""

        started = self.client.post(f"/api/proposals/{proposal_id}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        accepted = started.json()
        deadline = time.monotonic() + JOB_DEADLINE
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{accepted['jobId']}")
            self.assertEqual(job.status_code, 200, job.text)
            if job.json()["status"] in TERMINAL:
                self.assertEqual(job.json()["status"], "succeeded", job.text)
                return accepted
            time.sleep(0.02)
        raise AssertionError(f"job {accepted['jobId']} never finished")

    # ---- what the project holds

    def episode_records(self, run_id: str) -> list[dict]:
        """Every ``deliberation-episode`` the run retained, digest-verified."""

        return [
            self.repository.load_json(ref)
            for ref in self.repository.list_json(
                run=self.repository.load_run(run_id),
                destination=run_records(run_id),
            )
            if record_kind(ref) == EPISODE_KIND
        ]

    def authored_record_bytes(self) -> bytes:
        return self.repository.layout.resolve_relative(
            RUNNER_RECORD_PATH
        ).read_bytes()


class RejectionIsAJudgement(EpisodeTestCase):
    def test_a_rejected_proposal_is_kept_with_its_reason(self) -> None:
        proposal = self.propose("set height to 0.9")

        status, episode = self.decide(
            proposal["proposalId"],
            decision="rejected",
            reason="too heavy against the cornice",
        )

        self.assertEqual(status, 201, episode)
        self.assertRegex(episode["episodeId"], r"^ep-[0-9a-f]{12}$")
        self.assertEqual(episode["stateDigest"], self.state_digest)
        self.assertEqual(len(episode["proposals"]), 1)
        decided = episode["proposals"][0]
        self.assertEqual(decided["proposalId"], proposal["proposalId"])
        self.assertEqual(decided["decision"], "rejected")
        self.assertEqual(decided["reason"], "too heavy against the cornice")
        self.assertIsNone(decided["modifiedTo"])
        # What the option would have touched travels with the decision: the
        # proposal it came from is lost on restart, the closure is not.
        self.assertIn("entity:portico-base", decided["closure"])
        self.assertEqual(decided["change"], {"kind": "set_scalar", "key": "height", "old": 0.6, "new": 0.9})
        # No run exists yet, and the episode says exactly that rather than
        # implying the project can account for it.
        self.assertIsNone(episode["producedRun"])
        self.assertEqual(episode["persistence"], PERSISTENCE)

    def test_the_judgement_is_readable_by_state_and_by_id(self) -> None:
        proposal = self.propose("set height to 0.9")
        _, episode = self.decide(proposal["proposalId"], decision="rejected")

        listed = self.client.get(
            "/api/episodes", params={"stateDigest": self.state_digest}
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(
            [item["episodeId"] for item in listed.json()],
            [episode["episodeId"]],
        )
        one = self.client.get(f"/api/episodes/{episode['episodeId']}")
        self.assertEqual(one.status_code, 200, one.text)
        self.assertEqual(one.json(), episode)
        # A state nothing was decided against answers an empty list, not a 404:
        # "no judgement here" is an answer.
        other = self.client.get(
            "/api/episodes", params={"stateDigest": "f" * 64}
        )
        self.assertEqual(other.status_code, 200, other.text)
        self.assertEqual(other.json(), [])

    def test_an_episode_this_process_never_made_says_where_they_live(
        self,
    ) -> None:
        missing = self.client.get("/api/episodes/ep-000000000000")

        self.assertEqual(missing.status_code, 404, missing.text)
        body = missing.json()
        self.assertEqual(body["code"], "EPISODE_NOT_FOUND")
        self.assertIn(PERSISTENCE, body["detail"])


class ModificationLinksTwoOptions(EpisodeTestCase):
    def test_a_modified_proposal_names_the_proposal_that_replaced_it(
        self,
    ) -> None:
        proposal = self.propose("set height to 0.9")

        status, episode = self.decide(
            proposal["proposalId"],
            decision="modified",
            reason="the same idea, less of it",
            modifiedTo={"utterance": "set height to 0.75"},
        )

        self.assertEqual(status, 201, episode)
        decided = episode["proposals"][0]
        self.assertEqual(decided["decision"], "modified")
        self.assertEqual(decided["reason"], "the same idea, less of it")
        self.assertEqual(
            decided["modifiedTo"]["utterance"], "set height to 0.75"
        )
        # The replacement is a real proposal, made through the same
        # deterministic path, and this process can still hand it over.
        replacement = self.client.get(
            f"/api/proposals/{decided['modifiedTo']['proposalId']}"
        )
        self.assertEqual(replacement.status_code, 200, replacement.text)
        self.assertEqual(replacement.json()["utterance"], "set height to 0.75")
        self.assertEqual(replacement.json()["change"]["new"], 0.75)
        self.assertNotEqual(
            replacement.json()["proposalId"], proposal["proposalId"]
        )

    def test_a_modification_that_says_nothing_to_modify_to_is_refused(
        self,
    ) -> None:
        proposal = self.propose("set height to 0.9")

        status, body = self.decide(proposal["proposalId"], decision="modified")

        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "DECISION_INVALID")


class RunningACandidateIsNotAJudgement(EpisodeTestCase):
    def test_running_a_candidate_decides_nothing_and_closes_nothing(
        self,
    ) -> None:
        """The run holds the judgement already made against this base, and
        nothing about the proposal it ran or the ones it did not."""

        rejected = self.propose("set height to 0.9")
        _, earlier = self.decide(
            rejected["proposalId"],
            decision="rejected",
            reason="too heavy against the cornice",
        )
        shown = self.propose("set height to 2.2")
        other = self.propose("set height to 0.5", element_id="portico-cornice")
        self.assertEqual(earlier["producedRun"], None)

        accepted = self.run_candidate(shown["proposalId"])

        run_id = accepted["candidateId"]
        retained = self.episode_records(run_id)
        # Exactly the flushed rejection: a run that carried only a conclusion
        # would be a decision with its reasons deleted, and a run that carried
        # a conclusion nobody made would be worse.
        self.assertEqual(
            [payload["episodeId"] for payload in retained],
            [earlier["episodeId"]],
        )
        flushed = retained[0]
        self.assertEqual(flushed["schema"], SCHEMA)
        self.assertEqual(flushed["producedRun"], run_id)
        self.assertEqual(flushed["projectId"], PROJECT_ID)
        self.assertEqual(flushed["stateDigest"], self.state_digest)
        self.assertEqual(flushed["proposals"][0]["decision"], "rejected")
        self.assertEqual(
            flushed["proposals"][0]["reason"], "too heavy against the cornice"
        )
        # ADR-004: a new record kind carries no authority block at all.
        self.assertNotIn("no_authority", flushed)
        self.assertEqual(
            [key for key in flushed if key.endswith("_authority")], []
        )
        # Neither the proposal that ran nor the one that did not has been
        # judged: no episode anywhere names either of them.
        judged = {
            item["proposalId"]
            for episode in self.client.get("/api/episodes").json()
            for item in episode["proposals"]
        }
        self.assertEqual(judged, {rejected["proposalId"]})
        # The link that is kept is proposal -> candidate, and it is the job
        # registry's: the candidate names the proposal it was run from.
        candidate = self.client.get(f"/api/candidates/{run_id}")
        self.assertEqual(candidate.status_code, 200, candidate.text)
        self.assertEqual(candidate.json()["proposalId"], shown["proposalId"])
        self.assertEqual(
            self.app.state.jobs.candidates_of(shown["proposalId"]), (run_id,)
        )
        # The other proposal against the same base is still open: it can be
        # looked at too, and looking at it is not choosing it either.
        second = self.run_candidate(other["proposalId"])
        self.assertEqual(self.episode_records(second["candidateId"]), [])
        self.assertEqual(
            episodes.still_open(
                self.app.state.episodes,
                self.app.state.proposals.for_state(self.state_digest),
                without=shown["proposalId"],
            ),
            (self.app.state.proposals.get(other["proposalId"]),),
        )

    def test_a_retained_judgement_says_it_lives_in_the_run(self) -> None:
        rejected = self.propose("set height to 0.9")
        _, earlier = self.decide(rejected["proposalId"], decision="rejected")
        self.assertEqual(earlier["persistence"], PERSISTENCE)
        chosen = self.propose("set height to 2.2")

        accepted = self.run_candidate(chosen["proposalId"])

        again = self.client.get(f"/api/episodes/{earlier['episodeId']}")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(
            again.json()["persistence"], f"run:{accepted['candidateId']}"
        )
        self.assertEqual(again.json()["producedRun"], accepted["candidateId"])

    def test_a_failed_candidate_retains_no_judgement(self) -> None:
        """A run that never happened carries no decision about a building."""

        refused = self.propose("set height to -3")
        started = self.client.post(
            f"/api/proposals/{refused['proposalId']}/candidate"
        )
        self.assertEqual(started.status_code, 202, started.text)
        accepted = started.json()
        deadline = time.monotonic() + JOB_DEADLINE
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{accepted['jobId']}").json()
            if job["status"] in TERMINAL:
                break
            time.sleep(0.02)
        self.assertEqual(job["status"], "failed", job)

        self.assertEqual(
            self.client.get("/api/episodes").json(), [], "no run, no judgement"
        )


class AcceptanceIsTheArchitectsOwnAct(EpisodeTestCase):
    def test_an_explicit_acceptance_names_its_run_and_retains_the_whole_deliberation(
        self,
    ) -> None:
        """``decision: accepted`` with a ``candidateId`` writes into *that*
        run — not the latest one — the option chosen with the architect's
        reason, the ones closed by that choice, and the one already turned
        down. Nothing runs again and HEAD stays where it was."""

        rejected = self.propose("set height to 0.9")
        _, earlier = self.decide(
            rejected["proposalId"],
            decision="rejected",
            reason="too heavy against the cornice",
        )
        chosen = self.propose("set height to 2.2")
        other = self.propose("set height to 0.5", element_id="portico-cornice")
        # Two looks at the same proposal. The architect chooses the first,
        # which is exactly the run a "latest" default would not have picked.
        first = self.run_candidate(chosen["proposalId"])["candidateId"]
        second = self.run_candidate(chosen["proposalId"])["candidateId"]
        self.assertNotEqual(first, second)
        state = self.app.state
        self.assertEqual(state.jobs.candidates_of(chosen["proposalId"]), (first, second))
        # Neither run decided anything: the other option is still open.
        self.assertEqual(
            [item.proposal_id for item in episodes.still_open(
                state.episodes,
                state.proposals.for_state(self.state_digest),
                without=chosen["proposalId"],
            )],
            [other["proposalId"]],
        )
        before_head = self.repository.read_head()

        status, episode = self.decide(
            chosen["proposalId"],
            decision="accepted",
            candidateId=first,
            reason="the taller base reads better from the approach",
        )

        self.assertEqual(status, 201, episode)
        self.assertEqual(episode["producedRun"], first)
        self.assertEqual(episode["persistence"], f"run:{first}")
        self.assertEqual(episode["stateDigest"], self.state_digest)
        decisions = {item["proposalId"]: item for item in episode["proposals"]}
        accepted = decisions[chosen["proposalId"]]
        self.assertEqual(accepted["decision"], "accepted")
        # The architect's own sentence, verbatim, on the option chosen.
        self.assertEqual(
            accepted["reason"], "the taller base reads better from the approach"
        )
        self.assertIsNone(accepted["modifiedTo"])
        # Choosing one result preserves other explorations and alternatives.
        self.assertNotIn(other["proposalId"], decisions)
        # The one already rejected is not rejected a second time.
        self.assertNotIn(rejected["proposalId"], decisions)
        # The intent that was answered, not the chat log.
        self.assertEqual(episode["intent"]["utterance"], "set height to 2.2")
        self.assertEqual(episode["intent"]["targetComponentId"], "portico")
        self.assertEqual(episode["intent"]["elementId"], "portico-base")
        self.assertEqual(episode["intent"]["requestedProperty"], "height")
        # No scope word was said, so none is claimed.
        self.assertIsNone(episode["chosenScope"])
        # The evidence cited is the record's own, read off the same projection
        # the rejection cited it from.
        self.assertEqual(episode["evidenceRefs"], earlier["evidenceRefs"])
        # Nothing was validated before the decision, and the empty list is
        # that fact rather than a missing field.
        self.assertEqual(episode["validationRefs"], [])

        # The run named holds the acceptance and the earlier rejection; the
        # run not named holds nothing.
        by_id = {
            payload["episodeId"]: payload for payload in self.episode_records(first)
        }
        self.assertEqual(
            set(by_id), {earlier["episodeId"], episode["episodeId"]}
        )
        self.assertEqual(self.episode_records(second), [])
        # The DeliberationEpisode@1 payload on disk is the judgement the API
        # answered, under the schema it is written under. (The wire adds a
        # ``kind`` discriminator to each change; the payload never carried it.)
        payload = by_id[episode["episodeId"]]
        self.assertEqual(payload["schema"], SCHEMA)
        self.assertEqual(
            {k: v for k, v in payload.items() if k not in ("schema", "proposals")},
            {k: v for k, v in episode.items() if k not in ("persistence", "proposals")},
        )
        self.assertEqual(
            [(p["proposalId"], p["decision"], p["reason"], p["modifiedTo"]) for p in payload["proposals"]],
            [(p["proposalId"], p["decision"], p["reason"], p["modifiedTo"]) for p in episode["proposals"]],
        )
        self.assertNotIn("no_authority", payload)
        self.assertEqual(
            by_id[earlier["episodeId"]]["proposals"][0]["decision"], "rejected"
        )
        # Accepting ran nothing and moved nothing: the same two candidates,
        # the same HEAD, the authored record as it was.
        self.assertEqual(state.jobs.candidates_of(chosen["proposalId"]), (first, second))
        self.assertEqual(self.repository.read_head(), before_head)
        # The acceptance is readable back through the same API as any
        # judgement, and says where it lives.
        read = self.client.get(f"/api/episodes/{episode['episodeId']}")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json(), episode)
        # The other proposal remains available against its original base.
        self.assertEqual(
            episodes.still_open(
                state.episodes,
                state.proposals.for_state(self.state_digest),
                without=chosen["proposalId"],
            ),
            (state.proposals.get(other["proposalId"]),),
        )

    def test_accepting_cites_the_named_candidates_own_evidence_not_the_default_runs(
        self,
    ) -> None:
        """A proposal made with no ``sourceRunId`` was projected from whatever
        run answered by default at the time. A candidate run is a harness and
        never becomes that default — but a later *design* run does, and its
        record can cite different evidence. Accepting a finished candidate by
        name must read the evidence off that candidate's own retained record,
        not off whichever run happens to answer now."""

        chosen = self.propose("set height to 2.2")
        first = self.run_candidate(chosen["proposalId"])["candidateId"]
        before = self.client.get("/api/state").json()["referenceRun"]["runId"]
        self.assertEqual(before, REFERENCE_RUN_ID)

        # A newer complete design run — not a harness — whose record cites
        # other evidence. Built the way ``add_later_run`` builds one, with the
        # record payload that helper does not take.
        later_run_id = "run-003"
        # The same record, citing another source throughout: the kernel's view
        # of a massing-less record carries exactly one evidence ref, and every
        # component's sources must sit inside it.
        later_payload = json.loads(
            json.dumps(RECORD_PAYLOAD).replace(EVIDENCE, "evidence:later")
        )
        self.assertEqual(later_payload["evidence_refs"], ["evidence:later"])
        later = self.repository.create_run(later_run_id)
        receipt_ref = retain_runner_receipt(
            self.repository,
            later,
            design_state_digest=runner_state_digest(
                self.repository, later_run_id, later_payload
            ),
            record_payload=later_payload,
        )
        receipt_path = self.repository.layout.resolve_record(receipt_ref)
        newest = receipt_path.stat().st_mtime + 60.0
        os.utime(receipt_path, (newest, newest))
        self.assertEqual(
            self.client.get("/api/state").json()["referenceRun"]["runId"],
            later_run_id,
            "the later design run is now the default reference",
        )
        before_head = self.repository.read_head()

        status, episode = self.decide(
            chosen["proposalId"], decision="accepted", candidateId=first
        )

        self.assertEqual(status, 201, episode)
        self.assertEqual(episode["producedRun"], first)
        # The candidate's own record, as the runner retained it, is what the
        # judgement cites — the base it was actually run from.
        self.assertEqual(episode["evidenceRefs"], [EVIDENCE])
        self.assertNotIn("evidence:later", episode["evidenceRefs"])
        retained = self.episode_records(first)
        self.assertEqual(
            [payload["evidenceRefs"] for payload in retained], [[EVIDENCE]]
        )
        self.assertEqual(self.repository.read_head(), before_head)


class AcceptanceRefusesARunItCannotBind(EpisodeTestCase):
    """Every refusal here leaves no judgement at all, held or retained."""

    def assert_no_judgement(self) -> None:
        self.assertEqual(self.client.get("/api/episodes").json(), [])

    def test_a_candidate_this_process_never_ran_is_refused(self) -> None:
        chosen = self.propose("set height to 2.2")

        status, body = self.decide(
            chosen["proposalId"],
            decision="accepted",
            candidateId="studio-cand-00000000-000000-00000000-0000",
        )

        self.assertEqual(status, 404, body)
        self.assertEqual(body["code"], "CANDIDATE_NOT_FOUND")
        self.assert_no_judgement()

    def test_a_candidate_of_another_proposal_is_refused(self) -> None:
        chosen = self.propose("set height to 2.2")
        other = self.propose("set height to 0.5", element_id="portico-cornice")
        run_id = self.run_candidate(other["proposalId"])["candidateId"]

        status, body = self.decide(
            chosen["proposalId"], decision="accepted", candidateId=run_id
        )

        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "DECISION_INVALID")
        self.assertIn(other["proposalId"], body["detail"])
        self.assert_no_judgement()
        self.assertEqual(self.episode_records(run_id), [])

    def test_a_failed_or_unfinished_candidate_is_refused(self) -> None:
        refused = self.propose("set height to -3")
        started = self.client.post(
            f"/api/proposals/{refused['proposalId']}/candidate"
        ).json()
        deadline = time.monotonic() + JOB_DEADLINE
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{started['jobId']}").json()
            if job["status"] in TERMINAL:
                break
            time.sleep(0.02)
        self.assertEqual(job["status"], "failed", job)

        status, body = self.decide(
            refused["proposalId"],
            decision="accepted",
            candidateId=started["candidateId"],
        )

        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "DECISION_INVALID")
        self.assertIn("failed", body["detail"])
        self.assert_no_judgement()

        # A run still in progress has shown the architect nothing to accept.
        # The job is held open on the registry's own worker, as a real
        # candidate would be while its geometry runs.
        chosen = self.propose("set height to 2.2")
        gate = threading.Event()
        self.addCleanup(gate.set)
        held = self.app.state.jobs.submit(
            candidate_id="studio-cand-held-open",
            proposal_id=chosen["proposalId"],
            work=gate.wait,
        )
        status, body = self.decide(
            chosen["proposalId"],
            decision="accepted",
            candidateId=held.candidate_id,
        )
        gate.set()

        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "DECISION_INVALID")
        self.assertIn(held.candidate_id, body["detail"])
        self.assert_no_judgement()


class TheDecisionBodyHasOneShapePerDecision(EpisodeTestCase):
    def test_fields_that_belong_to_another_decision_are_refused(self) -> None:
        chosen = self.propose("set height to 2.2")
        run_id = self.run_candidate(chosen["proposalId"])["candidateId"]
        replacement = {"utterance": "set height to 0.75"}

        for body in (
            {"decision": "accepted"},
            {"decision": "accepted", "candidateId": run_id, "modifiedTo": replacement},
            {"decision": "rejected", "candidateId": run_id},
            {"decision": "modified", "candidateId": run_id, "modifiedTo": replacement},
        ):
            with self.subTest(body=body):
                status, answer = self.decide(chosen["proposalId"], **body)
                self.assertEqual(status, 422, answer)
                self.assertEqual(answer["code"], "DECISION_INVALID")

        # None of those left a judgement anywhere.
        self.assertEqual(self.client.get("/api/episodes").json(), [])
        self.assertEqual(self.episode_records(run_id), [])
        # Having been run does not lock the proposal into acceptance: turning
        # it down afterwards is an ordinary held rejection, met by no run.
        status, episode = self.decide(
            chosen["proposalId"], decision="rejected", reason="seen, and no"
        )
        self.assertEqual(status, 201, episode)
        self.assertIsNone(episode["producedRun"])
        self.assertEqual(episode["persistence"], PERSISTENCE)
        self.assertEqual(episode["proposals"][0]["reason"], "seen, and no")
        self.assertEqual(self.episode_records(run_id), [])


class TheAuthoredRecordIsUntouched(EpisodeTestCase):
    def test_retaining_judgements_never_rewrites_the_record(self) -> None:
        before = self.authored_record_bytes()

        rejected = self.propose("set height to 0.9")
        self.decide(
            rejected["proposalId"], decision="rejected", reason="too heavy"
        )
        modified = self.propose("set height to 1.1")
        self.decide(
            modified["proposalId"],
            decision="modified",
            modifiedTo={"utterance": "set height to 0.8"},
        )
        chosen = self.propose("set height to 2.2")
        accepted = self.run_candidate(chosen["proposalId"])

        self.assertEqual(self.authored_record_bytes(), before)
        # And the judgement really was written: the assertion above would pass
        # just as well if nothing had happened at all.
        self.assertTrue(self.episode_records(accepted["candidateId"]))
        self.assertEqual(
            json.loads(before)["entities"][4]["fields"]["params"]["height"],
            0.6,
        )


if __name__ == "__main__":
    unittest.main()
