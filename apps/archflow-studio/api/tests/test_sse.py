"""``GET /api/events``: the replay, and then whatever happens next.

The stream is read here as SSE frames rather than as a body, because the two
halves of the promise are separate facts: what the process still remembered
when the client connected, and what happened after it did. A test that asked
for the buffer and called it a stream would pass against an endpoint that never
carried a live event at all — so the live event here is published by another
thread while the request is open, which is the only way it is really live.

``Last-Event-ID`` is the other half: a client whose connection dropped names the
last sequence it saw and gets what it missed, not the whole buffer again.

Every request here is bounded with ``limit`` — the same bounded form a poller or
a terminal would use — because the test client reads a response, not a socket.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import threading
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

    def test_stream_replays_then_carries_a_live_event(self) -> None:
        self.publish("candidate.queued", job_id="job-a", candidate_id="cand-a")
        # Published while the request is open, from another thread: what comes
        # back second is live, not something the buffer already held.
        live = threading.Timer(
            0.1,
            lambda: self.publish(
                "candidate.succeeded", job_id="job-a", wall_time_s=0.25
            ),
        )
        live.start()
        self.addCleanup(live.cancel)

        replayed, carried = self.read(limit=2)

        self.assertEqual(replayed["event"], "candidate.queued")
        self.assertEqual(replayed["data"]["jobId"], "job-a")
        self.assertEqual(replayed["data"]["seq"], 1)
        self.assertEqual(replayed["id"], "1")
        self.assertEqual(carried["event"], "candidate.succeeded")
        self.assertEqual(carried["data"]["seq"], 2)
        self.assertEqual(carried["data"]["wallTimeS"], 0.25)
        # A field this event has nothing to say about is null, not missing.
        self.assertIsNone(carried["data"]["error"])

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
