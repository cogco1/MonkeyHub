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
