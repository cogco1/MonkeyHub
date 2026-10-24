"""The Working Head's history: each move onto a run, as the history page lists and goes back along it."""

from unittest import mock

from project_runtime.application import working_draft
from project_runtime.errors import StudioError

from .support import PROJECT_ID
from .test_working_source import WorkingSourceFixture


class WorkingDesignMovesTests(WorkingSourceFixture):
    def moves(self, **params) -> list[dict]:
        return self.answer(**params)["moves"]

    def answer(self, **params) -> dict:
        response = self.client.get("/api/working-design/moves", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["projectId"], PROJECT_ID)
        return response.json()

    def test_each_move_of_the_head_is_listed_newest_first_with_where_it_came_from(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        second = self.continue_from(first)
        self.adopt(first)
        self.adopt(second)
        moves = self.moves()
        self.assertEqual([(move["runId"], move["previousRunId"]) for move in moves[:2]],
                         [(second, first), (first, stage["candidateId"])])
        self.assertTrue(all(move["occurredAt"] and move["actorId"] and move["origin"] for move in moves))
        self.assertEqual(len(self.moves(limit=1)), 1)
        self.assertEqual(self.client.get("/api/working-design/moves", params={"limit": 0}).status_code, 422)

    def test_going_back_and_forth_is_walked_back_in_the_order_it_happened(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        second = self.continue_from(first)
        for run in (first, second, first):
            self.adopt(run)
        self.assertEqual([(move["runId"], move["previousRunId"]) for move in self.moves()][:3],
                         [(first, second), (second, first), (first, stage["candidateId"])])
        reads = []
        original = working_draft._moves_onto

        def counted(binding, run_id):
            reads.append(run_id)
            if run_id == second:
                raise StudioError(503, "RUN_UNREADABLE", "The run could not be read.")
            return original(binding, run_id)

        with mock.patch.object(working_draft, "_moves_onto", counted):
            answer = self.answer(limit=10)
        self.assertEqual([move["runId"] for move in answer["moves"]], [first])
        self.assertIn(second, answer["warnings"][0], "the walk says where it stopped")
        self.assertEqual(reads, [first, second], "only the runs on the way back are read")

    def test_a_project_nobody_moved_has_no_moves(self):
        self.initialize()
        self.assertEqual([move for move in self.moves() if move["previousRunId"] is not None], [])


class WorkingDesignSwitchTests(WorkingSourceFixture):
    """Switch and its Undo: the history page and the attention executor move the head through these (2-6)."""

    def position(self) -> dict:
        return self.client.get("/api/working-draft").json()

    def switch(self, run_id: str, *, revision: str | None = None, status: int = 200) -> dict:
        base = self.position()["revisionSha256"] if revision is None else revision
        response = self.client.post("/api/working-design/switch",
                                    json={"projectId": PROJECT_ID, "runId": run_id, "baseRevisionSha256": base})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def undo(self, undo: dict, *, status: int = 200) -> dict:
        response = self.client.post("/api/working-design/undo", json={
            "projectId": PROJECT_ID, "previous": undo["previous"], "continued": undo["continued"]})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_a_switch_moves_the_head_records_the_move_and_its_undo_puts_back_what_it_replaced(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        second = self.continue_from(first)
        self.adopt(first)
        answer = self.switch(second)
        self.assertEqual(answer["position"]["current"]["runId"], second)
        self.assertEqual(answer["undo"]["continued"], second)
        self.assertEqual(answer["undo"]["previous"]["runId"], first)
        self.assertEqual(answer["undo"]["revisionAfter"], answer["position"]["revisionSha256"])
        moves = self.client.get("/api/working-design/moves").json()["moves"]
        self.assertEqual((moves[0]["runId"], moves[0]["previousRunId"]), (second, first), "a switch is a move like any Continue")
        back = self.undo(answer["undo"])
        self.assertEqual(back["current"]["runId"], first)
        self.assertEqual(self.client.get("/api/working-design/moves").json()["moves"][0]["runId"], first)

    def test_an_undo_already_written_answers_as_done_and_one_after_the_head_moved_says_where_it_is(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        second = self.continue_from(first)
        third = self.continue_from(second)
        self.adopt(first)
        undo = self.switch(second)["undo"]
        self.undo(undo)
        revision = self.position()["revisionSha256"]
        again = self.undo(undo)
        self.assertEqual(again["current"]["runId"], first, "a retried Undo is done, not refused")
        self.assertEqual(again["revisionSha256"], revision, "and it writes nothing")
        undo = self.switch(second)["undo"]
        self.adopt(third)
        refused = self.undo(undo, status=409)
        self.assertEqual(refused["code"], "DESIGN_TREE_UNDO_MOVED")
        self.assertEqual(refused["currentRunId"], third)
        self.assertEqual(self.position()["current"]["runId"], third, "the newer choice stays")

    def test_a_switch_waits_for_unrecorded_edits_and_a_stale_base_is_refused(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        second = self.continue_from(first)
        self.adopt(first)
        stale = self.position()["revisionSha256"]
        self.adopt(second)
        self.assertEqual(self.switch(first, revision=stale, status=409)["code"], "WORKING_DRAFT_STALE")
        position = self.position()
        draft = {"source": {"projectId": PROJECT_ID, "sourceRunId": second, "sourceStageRef": None,
                            "stateDigest": self.state_of(second)["stateDigest"]}, "commands": [{"id": "edit"}]}
        retained = self.client.put("/api/working-draft/local", json={
            "projectId": PROJECT_ID, "baseRevisionSha256": position["revisionSha256"], "draft": draft})
        self.assertEqual(retained.status_code, 200, retained.text)
        refused = self.switch(first, status=409)
        self.assertEqual(refused["code"], "DESIGN_TREE_UNSYNCED_EDITS")
        self.assertEqual(self.position()["current"]["runId"], second, "nothing moved")

    def test_a_first_switch_from_no_entry_has_no_exact_undo(self):
        stage = self.initialize()
        first = self.candidate_from(stage)
        answer = self.switch(first)
        self.assertIsNone(answer["undo"]["previous"], "the head no entry named is returned to as the default, not guessed")
