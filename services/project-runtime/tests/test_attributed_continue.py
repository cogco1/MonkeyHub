"""Continue is an attributed act, whoever moves the Working Head (#294 slice S4).

Every move onto a run leaves one ``AuditEvent@1`` ``design.continued`` beside
that run, naming ids only. The Hub Agent may continue only on the user's bound
words, through a Runtime a Hub manages. A Continue admits nothing, generation
still never moves the head, and the acceptance reader passes the event by.
Every check reads retained records, and the ones that matter read them again
from a new process.
"""

from __future__ import annotations

from unittest import mock

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import AUDIT_EVENT
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.main import create_app

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_candidate_admission import AdmissionFixture

EVENT_FIELDS = {"schema", "eventId", "occurredAt", "action", "status", "projectId", "actorId",
                "authenticatedActor", "origin", "previousHeadRunId", "targetRunId", "messageSource"}
MESSAGE = {"sessionId": "chat-1", "messageId": "message-9"}
WORDS = "就从这个方案继续"


class AttributedContinueTests(AdmissionFixture):
    def continue_on(self, run_id: str | None, *, client: TestClient | None = None, expect: int = 200,
                    **body: object) -> dict:
        client = client or self.client
        position = client.get("/api/working-draft").json()
        response = client.put("/api/working-draft", json={
            "projectId": PROJECT_ID, "runId": run_id, "baseRevisionSha256": position["revisionSha256"], **body})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def events(self, client: TestClient | None = None) -> dict[str, list[dict]]:
        """Every retained design.continued event, by the run it sits beside, oldest first."""

        binding = bound_project((client or self.client).app.state)
        found: dict[str, list[dict]] = {}
        for run_id in binding.run_ids():
            refs = binding.repository.list_json(
                run=binding.load_run(run_id), record_kind=AUDIT_EVENT,
                destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run_id))
            rows = [binding.repository.load_json(ref) for ref in refs]
            rows = [row for row in rows if row.get("action") == "design.continued"]
            if rows:
                found[run_id] = sorted(rows, key=lambda row: row["occurredAt"])
        return found

    def head(self, client: TestClient | None = None) -> str:
        return self.working_source(client=client)["head"]["runId"]

    def test_a_continue_moves_the_head_and_is_retained_beside_its_run(self) -> None:
        stage = self.stage()
        first = self.result(stage, 2.2)
        # Generating is recorded and shown, never adopted, and records no Continue (Q2).
        self.assertEqual(self.head(), REFERENCE_RUN_ID)
        self.assertEqual(self.events(), {})

        self.continue_on(first)
        self.assertEqual(self.head(), first)
        [event] = self.events()[first]
        self.assertEqual(set(event), EVENT_FIELDS, "ids only; no words or state travel with the event")
        self.assertEqual({key: event[key] for key in EVENT_FIELDS - {"eventId", "occurredAt"}}, {
            "schema": "AuditEvent@1", "action": "design.continued", "status": "succeeded", "projectId": PROJECT_ID,
            "actorId": "studio:explicit-user-action", "authenticatedActor": False, "origin": "studio",
            "previousHeadRunId": REFERENCE_RUN_ID, "targetRunId": first, "messageSource": None})
        self.assertTrue(event["eventId"].startswith("aud-"))

        # Work continues from the head without moving it, whoever generates.
        later = self.result(source=first, height=2.6)
        aside = self.result(stage, 2.8)
        self.assertEqual(self.head(), first)
        self.assertEqual(list(self.events()), [first])

        # A Continue admits nothing: the pool holds only what the Stage proves.
        self.assertEqual(self.listed(include="rejected")["admissions"], [])
        self.assertEqual(self.admitted(self.pool(include="rejected")), {})
        self.no_admission_run()

        # Continuing again from there names the head it left; returning to the default names no run.
        self.continue_on(later)
        self.assertEqual(self.events()[later][0]["previousHeadRunId"], first)
        self.continue_on(None)
        self.assertEqual(self.head(), REFERENCE_RUN_ID)
        self.assertEqual({run: len(rows) for run, rows in self.events().items()}, {first: 1, later: 1})

        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(self.events(restarted), {first: [event], later: self.events()[later]})
            self.assertEqual(self.head(restarted), REFERENCE_RUN_ID)
        self.assertNotIn(aside, self.events())

    def test_the_acceptance_reader_passes_a_continue_by(self) -> None:
        stage = self.stage()
        first = self.result(stage, 2.2)
        # The S0 Stage retained no acceptance attribution, so any acceptance event
        # beside its run would be refused as unconfirmable; a Continue is not one.
        self.continue_on(first)
        self.continue_on(REFERENCE_RUN_ID)
        self.assertEqual(len(self.events()[REFERENCE_RUN_ID]), 1)
        [s0] = self.history()["stages"]
        self.assertIsNone(s0["acceptance"])

        # Accepting a run that carries a Continue event reads back exactly one acceptance.
        accepted = self.accept(first, stage)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        s1 = accepted.json()
        self.assertEqual((s1["acceptance"]["action"], s1["acceptance"]["origin"]), ("design.accepted", "studio"))
        with TestClient(create_app(self.settings)) as restarted:
            stages = self.history(client=restarted)["stages"]
            self.assertEqual(stages, [s0, s1])
            runtime = restarted.get("/api/runtime")
            self.assertEqual(runtime.status_code, 200, runtime.text)
        self.assertEqual(len(self.events()[first]), 1)

    def test_the_agent_continues_only_on_bound_words_through_the_hub(self) -> None:
        stage = self.stage()
        first = self.result(stage, 2.2)
        second = self.result(stage, 2.6)
        hub = self.managed()
        revision = self.client.get("/api/working-draft/revision").json()["revisionSha256"]

        refusals = (
            (self.client, {"messageSource": MESSAGE, "rawLanguage": WORDS}, first, "not managed by a Hub"),
            (hub, {"messageSource": MESSAGE}, first, "rawLanguage"),
            (hub, {"rawLanguage": WORDS}, first, "messageSource"),
            (hub, {"messageSource": MESSAGE, "rawLanguage": WORDS}, None, "names the run"),
        )
        for client, body, run_id, words in refusals:
            with self.subTest(body=body, run_id=run_id):
                answer = self.continue_on(run_id, client=client, expect=422, **body)
                self.assertEqual(answer["code"], "WORKING_DRAFT_ATTRIBUTION_INVALID", answer)
                self.assertIn(words, answer["detail"])
        self.assertEqual(self.client.get("/api/working-draft/revision").json()["revisionSha256"], revision)
        self.assertEqual((self.head(), self.events()), (REFERENCE_RUN_ID, {}))

        self.continue_on(first, client=hub, messageSource=MESSAGE, rawLanguage=WORDS)
        self.assertEqual(self.head(), first)
        [agent] = self.events()[first]
        self.assertEqual((agent["origin"], agent["messageSource"], agent["previousHeadRunId"], agent["targetRunId"]),
                         ("hub-agent", MESSAGE, REFERENCE_RUN_ID, first))
        self.assertNotIn(WORDS, repr(agent), "the words stay with the chat message the ids name")

        # The architect's own Continue through the same Hub is the architect's, not the Agent's.
        self.continue_on(second, client=hub)
        [person] = self.events()[second]
        self.assertEqual((person["origin"], person["messageSource"], person["previousHeadRunId"]),
                         ("hub", None, first))

        # Generation after an Agent's Continue still leaves the head where the words put it.
        self.continue_on(first, client=hub, messageSource=MESSAGE, rawLanguage=WORDS)
        self.result(source=first, height=3.0)
        self.assertEqual(self.head(), first)
        self.assertEqual(self.listed(include="rejected")["admissions"], [])
        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(self.head(restarted), first)
            self.assertEqual([row["origin"] for row in self.events(restarted)[first]], ["hub-agent", "hub-agent"])

    def test_a_continue_whose_event_cannot_be_retained_is_undone(self) -> None:
        stage = self.stage()
        first = self.result(stage, 2.2)
        binding = bound_project(self.app.state)
        before = self.client.get("/api/working-draft/revision").json()["revisionSha256"]
        original = binding.repository.put_json

        def refuse_events(**kwargs):
            if kwargs["record_kind"] == AUDIT_EVENT:
                raise OSError("disk full")
            return original(**kwargs)

        with mock.patch.object(binding.repository, "put_json", side_effect=refuse_events):
            answer = self.continue_on(first, expect=500)
        self.assertEqual(answer["code"], "CONTINUE_NOT_RETAINED", answer)
        self.assertIn("left where it was", answer["detail"])
        self.assertEqual(self.client.get("/api/working-draft/revision").json()["revisionSha256"], before)
        self.assertEqual((self.head(), self.events()), (REFERENCE_RUN_ID, {}))
        # The same Continue succeeds once its event can be retained.
        self.continue_on(first)
        self.assertEqual((self.head(), list(self.events())), (first, [first]))
