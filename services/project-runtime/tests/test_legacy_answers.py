"""In legacy, every route main has answers byte for byte as main does (#185).

One scenario - a project whose record names a condition with an executable check, a candidate that violates it,
its read, its validation, the Agent's admission, a Continue, a rejected second candidate and a refused direct move -
is run on this checkout and compared with what main answered to the same requests, kept in
``fixtures/legacy_answers_main.json``. Only what differs between any two runs of the same code is masked: run,
proposal and job ids, digests, clock times and durations, and the temporary directory; each by order of first
appearance, so a field added, dropped or moved still shows; a candidate's objects, which main too lists in no
fixed order, are compared sorted by name. The two routes main did not have,
``POST /api/revisions`` and ``GET /api/revisions/context``, are not in the scenario.

To write the file: export main, copy this file into the copy's tests, and from its ``services/project-runtime``
run ``python -m tests.test_legacy_answers --golden <path> --source "main <commit>"``; both export modes are written.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow.state.operational_state import DesignObligation
from monkeycad.backends.occt.kernel import occt_available
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, RUNNER_RECORD_PATH, make_project, retain_runner_receipt, runner_state_digest

GOLDEN = Path(__file__).parent / "fixtures" / "legacy_answers_main.json"
AGENT = {"X-Monkey-Agent": "chat-1", "X-Monkey-Worker": "hub-1"}
MESSAGE = {"sessionId": "chat-1", "messageId": "m-1"}
_MASKS = (
    ("cand", re.compile(r"studio-cand-\d{8}-\d{6}-[0-9a-f]{8}-[0-9a-f]{4}")),
    ("sha", re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")),
    ("uuid", re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")),
    ("hex", re.compile(r"(?<![0-9a-zA-Z])[0-9a-f]{32}(?![0-9a-zA-Z])")),
    ("ref", re.compile(r"(?<![0-9a-zA-Z])[a-z]+-[0-9a-f]{12,40}(?![0-9a-zA-Z])")),
    ("file", re.compile(r"(?<=@)[0-9a-f]{12}(?![0-9a-zA-Z])")),
    ("id", re.compile(r"(?<![0-9a-zA-Z])(?:prop|job|adm|dec|exp|op|ws)[-_][0-9a-zA-Z_-]{6,}")),
    ("time", re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")),
    ("stamp", re.compile(r"(?<![0-9])\d{8}[-T]\d{6}(?:-\d+)?(?![0-9])")),
)
_DURATION = re.compile(r'"(\w*(?:[Tt]ime|[Ss]econds|Ms|_s|S))"\s*:\s*-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?')


def _in_order(text: str) -> str:
    """The body with a candidate's objects sorted by name: main serves that one list in no fixed order, and so does
    this checkout. Written back as the server writes JSON, so nothing else in it changes."""

    try:
        body = json.loads(text)
    except ValueError:
        return text

    def walk(value):
        if isinstance(value, dict):
            return {key: (sorted((walk(item) for item in row), key=lambda item: str(item.get("name")))
                          if key == "objects" and isinstance(row, list) and all(isinstance(item, dict) for item in row)
                          else walk(row)) for key, row in value.items()}
        return [walk(item) for item in value] if isinstance(value, list) else value

    return json.dumps(walk(body), ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _masked(texts: list[str], roots: list[str]) -> list[str]:
    """The texts with every value that differs between runs of the same code replaced, consistently by order."""

    seen: dict[tuple[str, str], str] = {}
    out = []
    for text in map(_in_order, texts):
        for root in roots:
            for spelling in {root, root.replace("\\", "\\\\"), root.replace("\\", "/")}:
                text = text.replace(spelling, "<root>")
        for name, pattern in _MASKS:
            def mark(match: re.Match, name: str = name) -> str:
                key = (name, match.group(0))
                if key not in seen:
                    seen[key] = f"<{name}-{sum(1 for k in seen if k[0] == name) + 1}>"
                return seen[key]
            text = pattern.sub(mark, text)
        out.append(_DURATION.sub(lambda match: f'"{match.group(1)}":<n>', text))
    return out


def _wait(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never finished")


def scenario(root: Path, cad_export: str) -> list[list]:
    """The steps, each [name, status, body as served], on a fresh project under ``root``."""

    repository, _ = make_project(root)
    payload = json.loads(repository.layout.resolve_relative(RUNNER_RECORD_PATH).read_text(encoding="utf-8"))
    payload["obligations"] = [DesignObligation(
        obligation_id="base-height-limit", statement="The portico base stays low.", source_ref="test:declared-duty",
        subject_refs=("entity:portico-base",), validator_ref="validator:element-param-at-most/height/2.0").to_dict()]
    run = repository.create_run("obligation-source")
    retain_runner_receipt(repository, run, record_payload=payload,
                          design_state_digest=runner_state_digest(repository, run.run_id, payload))
    settings = StudioSettings(cad_export=cad_export, project_dir=root / PROJECT_ID, reference_run=run.run_id)
    steps: list[list] = []

    def step(name: str, response) -> dict:
        steps.append([name, response.status_code, response.text])
        try:
            return response.json()
        except ValueError:
            return {}

    person = TestClient(create_app(settings))
    hub = create_app(settings)
    hub.state.managed_instance_id = "hub-1"
    agent = TestClient(hub)
    try:
        state = step("state", person.get("/api/state"))
        candidates = []
        for height in (2.2, 2.4):
            proposal = step(f"propose {height}", person.post("/api/proposals", json={
                "stateDigest": state["stateDigest"], "targetComponentId": "portico", "elementId": "portico-base",
                "utterance": f"set height to {height}"}))
            started = step(f"candidate {height}", person.post(f"/api/proposals/{proposal['proposalId']}/candidate"))
            job = _wait(person, started["jobId"])
            steps.append([f"job {height}", 200, json.dumps({key: job.get(key) for key in ("status", "error")})])
            step(f"read {height}", person.get(f"/api/candidates/{started['candidateId']}"))
            step(f"validation {height}", person.get(f"/api/candidates/{started['candidateId']}/validation"))
            candidates.append(started["candidateId"])
            # A run id starts with its second: one second apart, lists sorted by id keep the order they were made in.
            time.sleep(1.1)
        step("agent admits", agent.post("/api/admissions", headers=AGENT, json={
            "projectId": PROJECT_ID, "task": {"kind": "hub-chat"}, "messageSource": MESSAGE,
            "results": [{"runId": candidates[0], "outcome": "admitted"}]}))
        source = step("working source", person.get("/api/working-source"))
        step("continue", person.put("/api/working-draft", json={
            "projectId": PROJECT_ID, "runId": candidates[0], "baseRevisionSha256": source.get("revisionSha256")}))
        step("working source after", person.get("/api/working-source"))
        step("agent rejects", agent.post("/api/admissions", headers=AGENT, json={
            "projectId": PROJECT_ID, "task": {"kind": "hub-chat"}, "messageSource": MESSAGE,
            "rawLanguage": "not the taller one", "results": [{"runId": candidates[1], "outcome": "rejected"}]}))
        step("agent continues", agent.put("/api/working-draft", headers=AGENT, json={
            "projectId": PROJECT_ID, "runId": candidates[1], "messageSource": MESSAGE, "rawLanguage": "this one",
            "baseRevisionSha256": person.get("/api/working-source").json().get("revisionSha256")}))
        step("direct move of a carried part", person.post("/api/proposals/transform", json={
            "stateDigest": person.get("/api/state").json()["stateDigest"], "elementId": "portico-base",
            "kind": "move", "translation": [0.3, 0, 0]}))
        step("admissions", person.get("/api/admissions"))
    finally:
        person.close()
        agent.close()
    return steps


def answers(cad_export: str) -> list[list]:
    """The scenario's steps with every run-to-run value masked."""

    root = Path(tempfile.mkdtemp(prefix="legacy-answers-"))
    try:
        steps = scenario(root, cad_export)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    texts = _masked([text for _, _, text in steps], [str(root), str(root.resolve())])
    return [[name, status, text] for (name, status, _), text in zip(steps, texts)]


class LegacyAnswersTests(unittest.TestCase):
    maxDiff = None

    def compare(self, cad_export: str) -> None:
        mine = answers(cad_export)
        main = json.loads(GOLDEN.read_text(encoding="utf-8"))[cad_export]
        self.assertEqual([row[0] for row in mine], [row[0] for row in main])
        for (name, status, text), (_, main_status, main_text) in zip(mine, main):
            with self.subTest(step=name):
                self.assertEqual((status, text), (main_status, main_text))

    def test_without_an_export_every_answer_is_mains(self) -> None:
        self.compare("off")

    @unittest.skipUnless(occt_available(), "cadquery-ocp is needed; CI installs it and fails without it")
    def test_with_the_occt_export_every_answer_is_mains(self) -> None:
        self.compare("occt")

    def test_two_runs_of_this_checkout_mask_to_the_same_answers(self) -> None:
        modes = ["off"] + (["occt"] if occt_available() else [])
        for mode in modes:
            with self.subTest(mode=mode):
                self.assertEqual(answers(mode), answers(mode), "the masks cover everything that differs between runs")


if __name__ == "__main__" and "--golden" in sys.argv:
    # python -m tests.test_legacy_answers --golden <file> --source "main <commit>", from the copy of main.
    out = Path(sys.argv[sys.argv.index("--golden") + 1])
    source = sys.argv[sys.argv.index("--source") + 1] if "--source" in sys.argv else "main"
    modes = ["off"] + (["occt"] if occt_available() else [])
    out.write_text(json.dumps({"source": source, **{mode: answers(mode) for mode in modes}},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(out)
