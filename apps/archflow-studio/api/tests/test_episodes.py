"""The judgement is retained: what was on the table, what was chosen, and why.

Nothing here rehearses a decision. The project is a real P036 project, the
proposals are made through the deterministic seam the studio actually uses, and
the accepting judgement is written into a candidate run by the same worker
thread that ran it — so what these tests read afterwards is a
``deliberation-episode`` record on disk, not something the API remembered about
its own work.

The asymmetry is the whole point. A rejection and a modification happen before
any run exists: they are held in this process, they say so, and they are lost on
restart. They stop being lost the moment a candidate runs against the same
state, because the reasons the accepted proposal is the accepted one belong in
the run that answers for it.

The last test is the one that would catch the worst bug: retaining a judgement
must not touch the authored record. The record is read before and after, byte
for byte.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.application.binding import record_kind
from archflow_studio_api.application.episodes import SCHEMA
from archflow_studio_api.application.proposals import PERSISTENCE
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import (
    PROJECT_ID,
    REFERENCE_RUN_ID,
    RUNNER_RECORD_PATH,
    make_project,
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
        self.assertEqual(decided["change"], {"key": "height", "old": 0.6, "new": 0.9})
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


class AcceptanceIsWrittenIntoItsRun(EpisodeTestCase):
    def test_running_a_candidate_retains_the_whole_deliberation(self) -> None:
        """One run holds the option that was chosen, the one that was not, and
        the one that had already been turned down."""

        rejected = self.propose("set height to 0.9")
        _, earlier = self.decide(
            rejected["proposalId"],
            decision="rejected",
            reason="too heavy against the cornice",
        )
        chosen = self.propose("set height to 2.2")
        other = self.propose("set height to 0.5", element_id="portico-cornice")
        self.assertEqual(earlier["producedRun"], None)

        accepted = self.run_candidate(chosen["proposalId"])

        run_id = accepted["candidateId"]
        retained = self.episode_records(run_id)
        self.assertEqual(len(retained), 2, retained)
        for payload in retained:
            self.assertEqual(payload["schema"], SCHEMA)
            self.assertEqual(payload["producedRun"], run_id)
            self.assertEqual(payload["projectId"], PROJECT_ID)
            self.assertEqual(payload["stateDigest"], self.state_digest)
            # ADR-004: a new record kind carries no authority block at all.
            self.assertNotIn("no_authority", payload)
            self.assertEqual(
                [key for key in payload if key.endswith("_authority")], []
            )
        by_id = {payload["episodeId"]: payload for payload in retained}

        # The judgement made before the run was flushed into it: a run that
        # carried only its conclusion would be a decision with its reasons
        # deleted.
        flushed = by_id[earlier["episodeId"]]
        self.assertEqual(flushed["proposals"][0]["decision"], "rejected")
        self.assertEqual(
            flushed["proposals"][0]["reason"], "too heavy against the cornice"
        )

        acceptance = next(
            payload
            for payload in retained
            if payload["episodeId"] != earlier["episodeId"]
        )
        decisions = {
            item["proposalId"]: item for item in acceptance["proposals"]
        }
        self.assertEqual(
            decisions[chosen["proposalId"]]["decision"], "accepted"
        )
        self.assertIsNone(decisions[chosen["proposalId"]]["reason"])
        # Every other option still on the table is closed by the same act, and
        # says which run closed it.
        self.assertEqual(decisions[other["proposalId"]]["decision"], "rejected")
        self.assertEqual(
            decisions[other["proposalId"]]["reason"],
            f"superseded by {chosen['proposalId']}",
        )
        # The one already rejected is not rejected a second time.
        self.assertNotIn(rejected["proposalId"], decisions)
        # The intent that was answered, not the chat log.
        self.assertEqual(acceptance["intent"]["utterance"], "set height to 2.2")
        self.assertEqual(acceptance["intent"]["targetComponentId"], "portico")
        self.assertEqual(acceptance["intent"]["elementId"], "portico-base")
        self.assertEqual(acceptance["intent"]["requestedProperty"], "height")
        # No scope word was said, so none is claimed.
        self.assertIsNone(acceptance["chosenScope"])
        self.assertEqual(acceptance["evidenceRefs"], ["evidence:demo"])
        # Nothing was validated before the decision, and the empty list is
        # that fact rather than a missing field.
        self.assertEqual(acceptance["validationRefs"], [])

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
