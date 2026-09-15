"""Who authorized an accepted candidate, retained where the acceptance is.

The Stage already says what was accepted. These tests are about the other
half: the authenticated actor and the surface the request came through, kept
as ``AuditEvent@1`` in the accepted run's review area so a cold process can
still answer *who* a week later.

Nothing here accepts by generating a candidate: a run is a preview, and every
event in this file comes from ``POST /api/candidates/{id}/accept`` succeeding.
The refusals matter as much as the acceptance - a stale branch, another
project, a caller with no credentials and a repeat of an acceptance already
committed each leave the ledger exactly as it was.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import AUDIT_EVENT
from archflow_studio_api.application.binding import bound_project, record_kind
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID
from .test_design_history import DesignHistoryFixture

REVIEWER_TOKEN = "reviewer-secret"
DESIGNER_TOKEN = "designer-secret"
OUTSIDER_TOKEN = "outsider-secret"


class AcceptanceAuditTests(DesignHistoryFixture):
    """The same project and candidates, accepted through an actor boundary."""

    def setUp(self) -> None:
        super().setUp()
        self.actors_file = self.root / "actors.json"
        self.actors_file.write_text(json.dumps({"actors": [
            {"actor_id": actor, "token": token, "projects": {project: actions}}
            for actor, token, project, actions in (
                ("reviewer", REVIEWER_TOKEN, PROJECT_ID, ["read", "accept"]),
                ("designer", DESIGNER_TOKEN, PROJECT_ID, ["read", "propose"]),
                ("outsider", OUTSIDER_TOKEN, "other-project", ["read", "accept"]),
            )
        ]}), encoding="utf-8")
        self.authenticated_settings = StudioSettings(
            project_dir=self.root / PROJECT_ID, cad_export="off", mode="remote",
            origins=("https://studio.example",), actors_file=self.actors_file,
            service_role="runtime",
        )

    # ---- the two clients, and the ledger they write into

    def authenticated(self) -> TestClient:
        """A second process over the same project, with actors configured."""

        app = create_app(self.authenticated_settings)
        self.addCleanup(app.state.jobs.shutdown)
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def accept_as(self, token: str | None, candidate_id: str, stage: dict,
                  client: TestClient, branch_id: str = "main", project_id: str = PROJECT_ID):
        return client.post(f"/api/candidates/{candidate_id}/accept", headers=self.headers(token) if token else {}, json={
            "projectId": project_id, "branchId": branch_id, "expectedHeadStageRef": stage["stageRef"],
        })

    def audit_events(self, client: TestClient | None = None) -> list[dict]:
        """Every retained acceptance event in the project, oldest run first."""

        binding = bound_project((client or self.client).app.state)
        found = []
        for run_id in binding.run_ids():
            run = binding.load_run(run_id)
            for ref in binding.repository.list_json(
                run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run_id),
            ):
                if record_kind(ref) == AUDIT_EVENT:
                    found.append(binding.repository.load_json(ref))
        return found

    # ---- the acceptance that is audited

    def test_authenticated_acceptance_is_readable_after_restart(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        accepting = self.authenticated()
        accepted = self.accept_as(REVIEWER_TOKEN, candidate, initial, accepting)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        stage = accepted.json()
        evidence = stage["acceptance"]
        self.assertEqual(stage["acceptedBy"], "reviewer")
        self.assertEqual(
            {key: evidence[key] for key in ("action", "status", "actorId", "authenticated", "origin")},
            {"action": "design.accepted", "status": "succeeded", "actorId": "reviewer",
             "authenticated": True, "origin": "studio"},
        )
        self.assertTrue(evidence["eventId"].startswith("aud-"))
        self.assertTrue(evidence["auditRef"].startswith(f"project://{PROJECT_ID}/"))
        self.assertIn(f"/{AUDIT_EVENT}-", evidence["auditRef"])

        [event] = self.audit_events()
        self.assertEqual(event["schema"], "AuditEvent@1")
        self.assertEqual(event["projectId"], PROJECT_ID)
        self.assertEqual(event["candidateId"], candidate)
        self.assertEqual(event["branchId"], "main")
        self.assertEqual(event["baseStageRef"], initial["stageRef"])
        self.assertEqual(event["resultStageRef"], stage["stageRef"])
        self.assertEqual(event["resultRecordDigest"], stage["recordDigest"])

        # A cold process holds no proposal, job or actor memory, and still
        # answers who accepted this Stage.
        with TestClient(create_app(self.settings)) as restarted:
            read = self.history(client=restarted)["stages"]
            self.assertEqual(read, [initial, stage])
            self.assertEqual(read[1]["acceptance"]["actorId"], "reviewer")
            self.assertIsNone(read[0]["acceptance"])
            # The recovery view reads committed Stages by another path; it
            # answers with the same evidence rather than a silent null.
            status = restarted.get("/api/runtime")
            self.assertEqual(status.status_code, 200, status.text)
            committed = [row for row in status.json()["stages"] if row["stageRef"] == stage["stageRef"]]
            self.assertEqual([row["acceptance"]["actorId"] for row in committed], ["reviewer"])

    def test_origin_follows_the_process_and_not_the_request_headers(self) -> None:
        initial = self.initialize()
        first = self.candidate_from(initial, 2.2)
        accepting = self.authenticated()
        # Headers a caller controls - including the managed-Hub headers, whose
        # instance id /api/health publishes to anyone - say nothing about where
        # an acceptance came from.
        spoofed = accepting.post(f"/api/candidates/{first}/accept", headers={
            **self.headers(REVIEWER_TOKEN), "x-monkey-worker": "hub-1", "x-monkey-origin": "hub",
        }, json={"projectId": PROJECT_ID, "branchId": "main", "expectedHeadStageRef": initial["stageRef"]})
        self.assertEqual(spoofed.status_code, 200, spoofed.text)
        self.assertEqual(spoofed.json()["acceptance"]["origin"], "studio")

        # A Studio the Hub started carries that instance from its own launch.
        managed = self.authenticated()
        managed.app.state.managed_instance_id = "hub-1"
        second = self.candidate_from(spoofed.json(), 2.8)
        accepted = self.accept_as(REVIEWER_TOKEN, second, spoofed.json(), managed)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["acceptance"]["origin"], "hub")
        self.assertEqual(sorted(event["origin"] for event in self.audit_events()), ["hub", "studio"])

    def test_no_credential_states_the_local_boundary_rather_than_a_person(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        accepted = self.accept(candidate, initial)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        evidence = accepted.json()["acceptance"]
        self.assertEqual(accepted.json()["acceptedBy"], "studio:explicit-user-action")
        self.assertEqual(
            {key: evidence[key] for key in ("actorId", "authenticated", "origin")},
            {"actorId": "studio:explicit-user-action", "authenticated": False, "origin": "studio"},
        )
        self.assertEqual(len(self.audit_events()), 1)

    def test_configured_process_without_a_token_accepts_nothing(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        accepting = self.authenticated()
        refused = self.accept_as(None, candidate, initial, accepting)
        self.assertEqual(refused.status_code, 401, refused.text)
        self.assertEqual(self.audit_events(), [])
        self.assertEqual(self.history()["stages"], [initial])

    # ---- recovery must preserve the winning attribution

    def test_ambiguous_cas_recovers_the_committed_winner_without_re_attribution(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        accepting = self.authenticated()
        binding = bound_project(accepting.app.state)
        original_swap = binding.repository.compare_and_swap_design_branch

        def commit_then_lose_the_reply(**kwargs):
            original_swap(**kwargs)
            raise OSError("simulated transport failure after atomic branch replacement")

        with patch.object(binding.repository, "compare_and_swap_design_branch", side_effect=commit_then_lose_the_reply):
            recovered = self.accept_as(REVIEWER_TOKEN, candidate, initial, accepting)
        self.assertEqual(recovered.status_code, 200, recovered.text)
        winner = recovered.json()
        self.assertEqual(winner["acceptedBy"], "reviewer")
        self.assertEqual(winner["acceptance"]["origin"], "studio")

        # The same actor now arrives through a Hub-managed process. The retry
        # must return the exact committed winner, not mint Hub attribution.
        managed = self.authenticated()
        managed.app.state.managed_instance_id = "hub-1"
        repeated = self.accept_as(REVIEWER_TOKEN, candidate, initial, managed)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), winner)
        [event] = self.audit_events()
        self.assertEqual(event["origin"], "studio")
        self.assertEqual(event["resultStageRef"], winner["stageRef"])

    def test_missing_event_repairs_from_stage_winner_after_changed_origin_retry(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        accepting = self.authenticated()
        binding = bound_project(accepting.app.state)
        original_put = binding.repository.put_json

        def fail_only_the_audit_event(**kwargs):
            if kwargs.get("record_kind") == AUDIT_EVENT:
                raise OSError("simulated crash while retaining acceptance evidence")
            return original_put(**kwargs)

        # The Stage wins the branch, but its derived AuditEvent does not land.
        with patch.object(binding.repository, "put_json", side_effect=fail_only_the_audit_event):
            failed = self.accept_as(REVIEWER_TOKEN, candidate, initial, accepting)
        self.assertEqual(failed.status_code, 500, failed.text)
        self.assertEqual(failed.json()["code"], "ACCEPTANCE_EVIDENCE_NOT_RETAINED")
        self.assertEqual(self.audit_events(), [])

        # Recovery comes from a different surface. It must reconstruct the
        # Studio winner encoded by the committed Stage, never use this retry's
        # Hub origin. The deterministic payload means a further retry is the
        # same event/ref rather than a duplicate.
        managed = self.authenticated()
        managed.app.state.managed_instance_id = "hub-1"
        repaired = self.accept_as(REVIEWER_TOKEN, candidate, initial, managed)
        self.assertEqual(repaired.status_code, 200, repaired.text)
        stage = repaired.json()
        self.assertEqual(stage["acceptedBy"], "reviewer")
        self.assertEqual(stage["acceptance"]["origin"], "studio")
        event_id = stage["acceptance"]["eventId"]
        repeated = self.accept_as(REVIEWER_TOKEN, candidate, initial, managed)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["acceptance"]["eventId"], event_id)
        [event] = self.audit_events()
        self.assertEqual(event["origin"], "studio")
        self.assertEqual(event["eventId"], event_id)
        with TestClient(create_app(self.settings)) as restarted:
            [_, cold_stage] = self.history(client=restarted)["stages"]
            self.assertEqual(cold_stage["acceptance"]["origin"], "studio")
            self.assertEqual(cold_stage["acceptance"]["eventId"], event_id)

    # ---- the refusals that write nothing

    def test_wrong_project_and_ungranted_actor_write_no_event(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        accepting = self.authenticated()
        other_project = self.accept_as(REVIEWER_TOKEN, candidate, initial, accepting, project_id="other-project")
        self.assertEqual(other_project.status_code, 403, other_project.text)
        self.assertEqual(other_project.json()["code"], "PROJECT_MISMATCH")
        for token in (DESIGNER_TOKEN, OUTSIDER_TOKEN):
            with self.subTest(token=token):
                denied = self.accept_as(token, candidate, initial, accepting)
                self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self.audit_events(), [])
        self.assertEqual(self.history()["stages"], [initial])

    def test_stale_base_and_repeated_acceptance_keep_one_event(self) -> None:
        initial = self.initialize()
        first = self.candidate_from(initial, 2.2)
        second = self.candidate_from(initial, 2.8)
        accepting = self.authenticated()
        accepted = self.accept_as(REVIEWER_TOKEN, first, initial, accepting)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        stale = self.accept_as(REVIEWER_TOKEN, second, initial, accepting)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "DESIGN_BRANCH_STALE")
        # The repeat of an acceptance this project already committed answers
        # with the committed Stage and its first event, not a second one.
        repeated = self.accept_as(REVIEWER_TOKEN, first, initial, accepting)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), accepted.json())
        events = self.audit_events()
        self.assertEqual([event["resultStageRef"] for event in events], [accepted.json()["stageRef"]])
        self.assertEqual(events[0]["actorId"], "reviewer")

    def test_no_actor_credential_reaches_the_project_tree(self) -> None:
        initial = self.initialize()
        candidate = self.candidate_from(initial)
        accepting = self.authenticated()
        self.assertEqual(self.accept_as(REVIEWER_TOKEN, candidate, initial, accepting).status_code, 200)
        secrets = tuple(token.encode("utf-8") for token in (REVIEWER_TOKEN, DESIGNER_TOKEN, OUTSIDER_TOKEN))
        for path in (self.root / PROJECT_ID).rglob("*"):
            if not path.is_file():
                continue
            data = path.read_bytes()
            for secret in secrets:
                self.assertNotIn(secret, data, f"{path} holds an actor credential")
