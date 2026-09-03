"""``GET /api/events``: the catch-up read, and what it promises.

Every request here is bounded with ``limit``, the same form a poller or a
terminal would use, because ``TestClient`` runs the whole ASGI app to
completion before handing back a response — it reads a body, not a socket, and
an unbounded stream would deadlock it. Live delivery over a held-open
connection is therefore tested at the sink (``test_events.py``), where the
queue hand-off actually lives; what is tested here is the transport: framing,
ordering, resume, and termination.

Termination is the promise worth stating. A bounded read must end whether or
not the process is busy: a ``limit`` that waited for its quota would hang
forever on a quiet server, which is the one thing a client asking for a
bounded read is trying to avoid.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterator
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, make_project

# Long enough that a slow machine is not a failure, short enough that a stream
# which will never produce anything does not hold the suite open.
READ_TIMEOUT = 30.0


def frames(body: str) -> Iterator[dict[str, Any]]:
    """The SSE frames in one response body, as their fields."""

    for block in body.split("\n\n"):
        if not block.strip():
            continue
        frame: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                frame["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                frame["data"] = json.loads(line.split(":", 1)[1])
            elif line.startswith("id:"):
                frame["id"] = line.split(":", 1)[1].strip()
        if "data" in frame:
            yield frame


class SseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_project(self.root)
        self.app = create_app(
            StudioSettings(project_dir=self.root / PROJECT_ID)
        )
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def publish(self, type_: str, **payload: object) -> None:
        self.app.state.events.publish(event={"type": type_, **payload})

    def read(self, **params: object) -> list[dict[str, Any]]:
        with self.client.stream(
            "GET", "/api/events", params=params, timeout=READ_TIMEOUT
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(
                response.headers["content-type"].startswith(
                    "text/event-stream"
                ),
                response.headers["content-type"],
            )
            return list(frames(response.read().decode("utf-8")))

    def test_the_replay_is_framed_with_its_type_and_resume_point(self) -> None:
        self.publish("candidate.queued", job_id="job-a", candidate_id="cand-a")
        self.publish("candidate.succeeded", job_id="job-a", wall_time_s=0.25)

        queued, succeeded = self.read(limit=2)

        self.assertEqual(queued["event"], "candidate.queued")
        self.assertEqual(queued["data"]["jobId"], "job-a")
        self.assertEqual(queued["data"]["candidateId"], "cand-a")
        self.assertEqual(queued["data"]["seq"], 1)
        # The id is the resume point, and it is the sequence.
        self.assertEqual(queued["id"], "1")
        self.assertEqual(succeeded["event"], "candidate.succeeded")
        self.assertEqual(succeeded["data"]["seq"], 2)
        self.assertEqual(succeeded["data"]["wallTimeS"], 0.25)
        # A field this event has nothing to say about is null, not missing.
        self.assertIsNone(succeeded["data"]["error"])

    def test_a_bounded_read_sends_no_more_than_its_limit(self) -> None:
        for index in range(5):
            self.publish("candidate.queued", job_id=f"job-{index}")

        read = self.read(limit=3)

        self.assertEqual([item["data"]["seq"] for item in read], [1, 2, 3])

    def test_a_bounded_read_ends_when_the_backlog_runs_out(self) -> None:
        """A limit is a ceiling, not a quota it will wait to fill.

        Nothing is publishing here and the buffer holds one event. Asking for
        fifty must return that one and close; an endpoint that waited for the
        other forty-nine would hang the client forever on a quiet process.
        """

        self.publish("candidate.queued", job_id="job-a")

        read = self.read(limit=50)

        self.assertEqual([item["data"]["seq"] for item in read], [1])

    def test_a_bounded_read_of_an_empty_buffer_ends_immediately(self) -> None:
        self.assertEqual(self.read(limit=10), [])

    def test_last_event_id_resumes_after_the_sequence_it_names(self) -> None:
        self.publish("candidate.queued", job_id="job-a")
        self.publish("candidate.running", job_id="job-a")
        self.publish("candidate.succeeded", job_id="job-a")

        with self.client.stream(
            "GET",
            "/api/events",
            params={"limit": 1},
            headers={"Last-Event-ID": "2"},
            timeout=READ_TIMEOUT,
        ) as response:
            resumed = list(frames(response.read().decode("utf-8")))

        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0]["data"]["seq"], 3)
        self.assertEqual(resumed[0]["event"], "candidate.succeeded")

    def test_unreadable_last_event_id_replays_the_whole_buffer(self) -> None:
        """A malformed resume point is not a reason to hide the buffer."""

        self.publish("candidate.queued", job_id="job-a")
        self.publish("candidate.running", job_id="job-a")

        with self.client.stream(
            "GET",
            "/api/events",
            params={"limit": 2},
            headers={"Last-Event-ID": "not-a-sequence"},
            timeout=READ_TIMEOUT,
        ) as response:
            replayed = list(frames(response.read().decode("utf-8")))

        self.assertEqual([item["data"]["seq"] for item in replayed], [1, 2])


if __name__ == "__main__":
    unittest.main()
