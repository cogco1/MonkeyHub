"""A skill runs in the person's own environment, only as far as the person granted it (P271).

A version declares the access it needs; the Hub page grants it once or for
the version until revoked. The run is a background job whose record names
the grant, every input and output digest, the programs it started, the hosts
it reached, where it wrote, and what it tried beyond the grant.
"""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import sys
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from archflow.project.record_kinds import (STUDIO_SKILL_APPROVAL, STUDIO_SKILL_REVOCATION, STUDIO_SKILL_RUN,
                                           require_registered)
from archflow.project.repository import FilesystemProjectRepository
from project_runtime.application import artifacts, skills
from project_runtime.binding import bound_project
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

PROJECT_ID = "skill-runs"
HEADER = '''import argparse, json, os, shutil, subprocess, sys
parser = argparse.ArgumentParser()
for flag in ("--inputs", "--params", "--outputs"):
    parser.add_argument(flag)
args = parser.parse_args()
params = json.load(open(args.params, encoding="utf-8"))
manifest = json.load(open(os.path.join(args.inputs, "manifest.json"), encoding="utf-8"))
'''
SCRIPT = HEADER + '''if params.get("fail"):
    print("the image is too dark", file=sys.stderr)
    sys.exit(3)
image = manifest["image"]
shutil.copyfile(os.path.join(args.inputs, image["file"]), os.path.join(args.outputs, "copy.png"))
with open(os.path.join(args.outputs, "result.csv"), "w", encoding="utf-8", newline="") as out:
    out.write("hex,share\\n#6E7565,%s\\n" % params.get("share", 0.5))
with open(os.path.join(args.outputs, "run.json"), "w", encoding="utf-8") as out:
    json.dump({"run": os.environ.get("MONKEYHUB_SKILL_RUN")}, out)
'''


def png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 6), (110, 117, 101)).save(buffer, format="PNG")
    return buffer.getvalue()


class SkillRunTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skill runs ")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / PROJECT_ID
        FilesystemProjectRepository.initialize(
            self.project, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0},
        )
        self.client = TestClient(create_app(StudioSettings(project_dir=self.project, cad_export="off")))
        self.addCleanup(self.client.close)
        self.binding = bound_project(self.client.app.state)
        self.image = artifacts.save_document(self.binding, None, "site.png", "image/png",
                                             base64.b64encode(png()).decode("ascii"))

    def add(self, script: str = SCRIPT, supersedes: int | None = None, access: dict | None = None) -> dict:
        body = {"projectId": PROJECT_ID, "name": "site-palette", "description": "A colour card from an image.",
                "body": "Run on one registered image.\n", "files": {"scripts/run.py": script},
                "entry": "scripts/run.py"}
        if supersedes is not None:
            body["supersedesVersion"] = supersedes
        if access is not None:
            body["access"] = access
        response = self.client.post("/api/skills", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def approve(self, version: int, code: str, status: int = 201, scope: str = "version",
                method: str = "hub-page") -> dict:
        response = self.client.post("/api/skills/skill:site-palette/approvals", json={
            "projectId": PROJECT_ID, "version": version, "codeSha256": code, "scope": scope, "method": method})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def start(self, status: int = 202, inputs: dict | None = None, **body) -> dict:
        if inputs is None:
            inputs = {"image": {"runId": self.image.run_id, "assetSha256": self.image.asset_sha256}}
        response = self.client.post("/api/skills/skill:site-palette/run",
                                    json={"projectId": PROJECT_ID, "inputs": inputs, **body})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def finish(self, started: dict) -> dict:
        for _ in range(600):
            record = self.client.get(started["statusPath"]).json()
            if record["status"] in ("succeeded", "failed"):
                return record
            time.sleep(0.05)
        self.fail("the run did not finish")

    def run_skill(self, **body) -> dict:
        return self.finish(self.start(**body))

    def skill_runs(self) -> list[str]:
        return sorted(path.name for path in (self.project / "runs").iterdir() if path.name.startswith("skill-run-"))

    def test_the_records_are_registered_kinds(self):
        self.assertEqual(require_registered(STUDIO_SKILL_APPROVAL).schema, "StudioSkillApproval@1")
        self.assertEqual(require_registered(STUDIO_SKILL_REVOCATION).schema, "StudioSkillRevocation@1")
        self.assertEqual(require_registered(STUDIO_SKILL_RUN).schema, "StudioSkillRun@1")

    def test_an_ungranted_version_is_refused_before_anything_runs(self):
        added = self.add()
        expected = skills.code_sha256("Run on one registered image.\n", "scripts/run.py", {"scripts/run.py": SCRIPT})
        self.assertEqual(added["codeSha256"], expected)
        row = self.client.get("/api/skills").json()["skills"][0]
        self.assertEqual((row["runnable"], row["approved"], row["codeSha256"], row["access"]), (True, False, expected, None))
        self.assertEqual(self.start(status=403)["code"], "SKILL_NOT_APPROVED")
        self.assertEqual(self.skill_runs(), [], "nothing ran and nothing was retained")

    def test_the_access_is_part_of_what_is_granted(self):
        plain = skills.code_sha256("x\n", "scripts/run.py", {"scripts/run.py": "pass"})
        wider = skills.code_sha256("x\n", "scripts/run.py", {"scripts/run.py": "pass"},
                                   {"network": True, "programs": [], "ssh": [], "read": [], "write": [], "packages": []})
        self.assertNotEqual(plain, wider)
        for bad in ({"packages": ["--index-url=http://x"]}, {"write": ["relative/folder"]}, {"shell": True}):
            response = self.client.post("/api/skills", json={
                "projectId": PROJECT_ID, "name": "bad", "description": "x", "body": "x\n",
                "files": {"scripts/run.py": "pass"}, "entry": "scripts/run.py", "access": bad})
            self.assertEqual(response.status_code, 422, (bad, response.text))

    def test_a_version_grant_binds_the_code_that_was_shown(self):
        added = self.add()
        self.assertEqual(self.approve(1, "0" * 64, status=409)["code"], "SKILL_CODE_CHANGED")
        approval = self.approve(1, added["codeSha256"])
        self.assertEqual((approval["version"], approval["scope"], approval["method"]), (1, "version", "hub-page"))
        self.assertEqual(self.approve(1, added["codeSha256"]), approval, "asking again answers the same grant")
        self.assertTrue(self.client.get("/api/skills").json()["skills"][0]["approved"])

    def test_a_granted_run_records_its_grant_inputs_outputs_and_documents(self):
        added = self.add()
        grant = self.approve(1, added["codeSha256"])
        started = self.start(parameters={"share": 0.25})
        self.assertEqual(started["status"], "queued")
        record = self.finish(started)
        self.assertEqual(record["status"], "succeeded", record)
        self.assertEqual((record["skillId"], record["version"], record["codeSha256"]),
                         ("skill:site-palette", 1, added["codeSha256"]))
        self.assertEqual((record["grant"]["approvalId"], record["grant"]["scope"]), (grant["approvalId"], "version"))
        self.assertEqual(record["inputs"][0]["assetSha256"], self.image.asset_sha256)
        outputs = {row["name"]: row for row in record["outputs"]}
        self.assertEqual(outputs["copy.png"]["sha256"], self.image.asset_sha256)
        self.assertEqual(outputs["copy.png"]["document"]["runId"], record["runId"])
        self.assertEqual(outputs["result.csv"]["text"], "hex,share\n#6E7565,0.25\n")
        self.assertEqual(json.loads(outputs["run.json"]["text"]), {"run": record["runId"]})
        self.assertEqual(record["observed"], {"processes": [], "network": [], "writes": []})
        downloaded = self.client.get(f"/api/skills/runs/{record['runId']}/outputs/copy.png")
        self.assertEqual(hashlib.sha256(downloaded.content).hexdigest(), self.image.asset_sha256)
        runs = self.client.get("/api/skills/skill:site-palette/runs").json()["runs"]
        self.assertEqual([row["runId"] for row in runs], [record["runId"]])

    def test_a_once_grant_allows_one_run(self):
        added = self.add()
        grant = self.approve(1, added["codeSha256"], scope="once")
        self.assertEqual(self.run_skill()["status"], "succeeded")
        refused = self.start(status=403)
        self.assertEqual(refused["code"], "SKILL_NOT_APPROVED")
        self.assertIn("allowed once", refused["detail"])
        statuses = {row["approvalId"]: row["status"] for row in self.client.get("/api/skills/approvals").json()["approvals"]}
        self.assertEqual(statuses, {grant["approvalId"]: "used"})

    def test_an_agent_runs_only_an_always_allowed_version(self):
        added = self.add()
        self.approve(1, added["codeSha256"], scope="once")
        self.assertEqual(self.start(status=403, agentRun=True)["code"], "SKILL_NOT_APPROVED")
        self.approve(1, added["codeSha256"], scope="version")
        self.assertEqual(self.run_skill(agentRun=True)["requestedBy"]["agent"], True)

    def test_an_agent_may_use_the_once_grant_a_person_gave_in_the_chat_for_it(self):
        added = self.add()
        page = self.approve(1, added["codeSha256"], scope="once")
        self.assertEqual(self.start(status=403, agentRun=True, grantId=page["approvalId"])["code"],
                         "SKILL_NOT_APPROVED", "a once grant from the Hub page is for the person's own run")
        chat = self.approve(1, added["codeSha256"], scope="once", method="hub-chat")
        record = self.run_skill(agentRun=True, grantId=chat["approvalId"])
        self.assertEqual((record["status"], record["grant"]["approvalId"], record["grant"]["method"]),
                         ("succeeded", chat["approvalId"], "hub-chat"))
        self.assertEqual(self.start(status=403, agentRun=True, grantId=chat["approvalId"])["code"],
                         "SKILL_NOT_APPROVED", "allowed once is one run")

    def test_a_revoked_grant_runs_nothing(self):
        added = self.add()
        grant = self.approve(1, added["codeSha256"])
        revoked = self.client.post(f"/api/skills/approvals/{grant['approvalId']}/revoke", json={"projectId": PROJECT_ID})
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(self.start(status=403)["code"], "SKILL_NOT_APPROVED")
        rows = self.client.get("/api/skills/approvals").json()["approvals"]
        self.assertEqual([(row["approvalId"], row["status"]) for row in rows], [(grant["approvalId"], "revoked")])
        self.assertFalse(self.client.get("/api/skills").json()["skills"][0]["approved"])

    def test_code_that_no_longer_matches_its_grant_is_refused(self):
        self.add()
        run = self.binding.load_run(skills.SKILLS_RUN_ID)
        self.binding.repository.put_json(run=run, destination=skills._destination(), record_kind=STUDIO_SKILL_APPROVAL,
                                         payload={"schema": skills.APPROVAL_SCHEMA, "projectId": PROJECT_ID,
                                                  "approvalId": "forged", "skillId": "skill:site-palette", "version": 1,
                                                  "codeSha256": "f" * 64, "method": "hub-page", "scope": "version",
                                                  "approvedAt": "2026-10-19T00:00:00+00:00",
                                                  "approvedBy": {"actorId": "x", "authenticated": False, "origin": "local"}})
        self.assertEqual(self.start(status=409)["code"], "SKILL_CODE_CHANGED")

    def test_what_was_not_granted_is_refused_and_named(self):
        script = HEADER + '''import socket
if params.get("program"):
    subprocess.run([sys.executable, "-c", "pass"], check=True)
if params.get("network"):
    socket.create_connection(("192.0.2.1", 9), timeout=1)
'''
        added = self.add(script)
        self.approve(1, added["codeSha256"])
        for parameters, category in (({"program": True}, "programs"), ({"network": True}, "network")):
            with self.subTest(category=category):
                record = self.run_skill(inputs={}, parameters=parameters)
                self.assertEqual((record["status"], record["failureCode"]), ("failed", "SKILL_PERMISSION_NEEDED"), record)
                self.assertEqual(record["needed"][0]["category"], category)

    def test_a_granted_program_runs_and_is_recorded(self):
        script = HEADER + '''subprocess.run([sys.executable, "-c", "pass"], check=True)
open(os.path.join(args.outputs, "done.txt"), "w").write("ok")
'''
        program = Path(os.sys.executable).stem.lower()
        added = self.add(script, access={"programs": [program]})
        self.approve(1, added["codeSha256"])
        record = self.run_skill(inputs={})
        self.assertEqual(record["status"], "succeeded", record)
        self.assertEqual(record["access"]["programs"], [program])
        self.assertEqual([row["program"] for row in record["observed"]["processes"]], [program])

    # Synthetic values only: what the Hub hands the runtime, and credentials the
    # Hub's own environment might carry from the person's shell.
    SECRETS = {"ARCHFLOW_STUDIO_RENDER_API_KEY": "synthetic-gemini-0001", "ARCHFLOW_STUDIO_SYNC_TOKEN": "synthetic-sync-0002",
               "MONKEYHUB_RENDER_API_KEY": "synthetic-launch-0003", "GEMINI_API_KEY": "synthetic-provider-0004",
               "OPENAI_API_KEY": "synthetic-provider-0005", "ANTHROPIC_AUTH_TOKEN": "synthetic-provider-0006",
               "GH_TOKEN": "synthetic-provider-0007", "AWS_SECRET_ACCESS_KEY": "synthetic-provider-0008",
               "ZHIPU_PASSWORD": "synthetic-provider-0009", "SSH_AUTH_SOCK": "synthetic-agent-0010"}
    ENV_SCRIPT = HEADER + '''json.dump(dict(os.environ), open(os.path.join(args.outputs, "env.json"), "w", encoding="utf-8"))
'''

    def environment_of_a_run(self, access=None) -> tuple[dict, dict]:
        added = self.add(self.ENV_SCRIPT, access=access)
        self.approve(1, added["codeSha256"])
        with patch.dict(os.environ, {**self.SECRETS, "SKILL_TEST_KEEP": "kept"}):
            record = self.run_skill(inputs={})
        self.assertEqual(record["status"], "succeeded", record)
        answer = self.client.get(f"/api/skills/runs/{record['runId']}/outputs/env.json")
        self.assertEqual(answer.status_code, 200, answer.text)
        return record, json.loads(answer.content)

    def test_a_run_reads_none_of_the_hubs_credentials(self):
        record, seen = self.environment_of_a_run()
        text = json.dumps(seen)
        self.assertEqual([name for name, value in self.SECRETS.items() if value in text], [],
                         "no key or token the Hub or the shell holds reaches a skill")
        self.assertEqual([name for name in seen if name.upper().startswith("ARCHFLOW_STUDIO_")], [],
                         "nor any of the runtime's own settings")
        self.assertEqual(seen["SKILL_TEST_KEEP"], "kept", "the person's other settings stay")
        self.assertIn("PATH", {name.upper() for name in seen})
        self.assertEqual(seen["MONKEYHUB_SKILL_RUN"], record["runId"], "the run's own variables are set")

    def test_the_ssh_agent_is_kept_only_for_a_version_granted_ssh(self):
        _, seen = self.environment_of_a_run(access={"ssh": ["vast-gpu"]})
        self.assertEqual(seen.get("SSH_AUTH_SOCK"), self.SECRETS["SSH_AUTH_SOCK"])
        self.assertNotIn(self.SECRETS["ARCHFLOW_STUDIO_RENDER_API_KEY"], json.dumps(seen))

    @unittest.skipUnless(sys.platform == "win32", "Windows Credential Manager")
    def test_the_credentials_the_hub_saved_are_refused_to_a_skill(self):
        script = HEADER + '''import ctypes
ctypes.windll.advapi32.CredReadW
open(os.path.join(args.outputs, "read.txt"), "w").write("reached")
'''
        added = self.add(script)
        self.approve(1, added["codeSha256"])
        record = self.run_skill(inputs={})
        self.assertEqual((record["status"], record["failureCode"]), ("failed", "SKILL_CREDENTIALS_REFUSED"), record)
        self.assertEqual([(row["category"], row["detail"]) for row in record["needed"]], [("credentials", "CredReadW")])

    def test_a_local_file_input_and_a_binary_output(self):
        source = Path(tempfile.mkdtemp(prefix="skill input ")) / "points.npz"
        source.write_bytes(b"\x93NUMPY fake")
        script = HEADER + '''data = open(os.path.join(args.inputs, manifest["points"]["file"]), "rb").read()
open(os.path.join(args.outputs, "points-copy.npz"), "wb").write(data)
'''
        added = self.add(script)
        self.approve(1, added["codeSha256"])
        record = self.run_skill(inputs={"points": {"kind": "file", "path": str(source)}})
        self.assertEqual(record["status"], "succeeded", record)
        self.assertEqual(record["inputs"][0]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        output = {row["name"]: row for row in record["outputs"]}["points-copy.npz"]
        self.assertIn("object", output)
        copy = self.client.get(f"/api/skills/runs/{record['runId']}/outputs/points-copy.npz").content
        self.assertEqual(copy, source.read_bytes())

    def test_a_model_input_and_model_and_svg_outputs(self):
        fixture = Path(__file__).parent / "fixtures" / "model-source-a.3dm"
        source = artifacts.register_model_asset(self.binding, None, None, "site.3dm",
                                                base64.b64encode(fixture.read_bytes()).decode("ascii"))
        script = HEADER + '''shutil.copyfile(os.path.join(args.inputs, manifest["site"]["file"]), os.path.join(args.outputs, "merged.3dm"))
open(os.path.join(args.outputs, "plan.svg"), "w", encoding="utf-8").write("<svg xmlns='http://www.w3.org/2000/svg'/>")
print(json.dumps({"kind": manifest["site"]["kind"], "text": "场地"}, ensure_ascii=False))
'''
        added = self.add(script)
        self.approve(1, added["codeSha256"])
        record = self.run_skill(inputs={"site": {"modelSha256": source.sha256}})
        self.assertEqual(record["status"], "succeeded", record)
        self.assertEqual((record["inputs"][0]["kind"], record["inputs"][0]["modelSha256"]), ("model", source.sha256))
        outputs = {row["name"]: row for row in record["outputs"]}
        self.assertEqual(outputs["merged.3dm"]["modelAsset"]["sha256"], source.sha256)
        self.assertTrue(outputs["merged.3dm"]["undrawable"]["checked"])
        self.assertEqual(outputs["merged.3dm"]["undrawable"]["objects"], [], "the fixture draws whole")
        self.assertEqual(outputs["plan.svg"]["text"], "<svg xmlns='http://www.w3.org/2000/svg'/>")
        self.assertIn("场地", record["stdoutTail"], "UTF-8 output survives")

    def test_a_drawing_input_gives_its_svg_and_its_view_recipe(self):
        fixture = Path(__file__).parent / "fixtures" / "model-source-a.3dm"
        model = artifacts.register_model_asset(self.binding, None, None, "site.3dm",
                                               base64.b64encode(fixture.read_bytes()).decode("ascii"))
        drawn = self.client.post("/api/drawings/elevations", json={
            "projectId": PROJECT_ID, "sourceAsset": {"runId": model.run_id, "assetSha256": model.sha256}, "view": "top"})
        self.assertEqual(drawn.status_code, 201, drawn.text)
        drawing = drawn.json()
        script = HEADER + '''entry = manifest["top"]
svg = open(os.path.join(args.inputs, entry["file"]), encoding="utf-8").read()
recipe = json.load(open(os.path.join(args.inputs, entry["document"]), encoding="utf-8"))
open(os.path.join(args.outputs, "seen.json"), "w", encoding="utf-8").write(json.dumps(
    {"kind": entry["kind"], "svg": svg.lstrip().startswith("<"), "hasRecipe": bool(recipe.get("viewRecipe")),
     "revisionRef": recipe["revisionRef"], "frame": recipe["viewRecipe"].get("frame"),
     "flatCrop": recipe["viewRecipe"].get("crop_uv")}))
'''
        added = self.add(script)
        self.approve(1, added["codeSha256"])
        record = self.run_skill(inputs={"top": {"kind": "drawing", "runId": drawing["runId"],
                                                "assetSha256": drawing["assetSha256"], "revisionRef": drawing["revisionRef"]}})
        self.assertEqual(record["status"], "succeeded", record)
        seen = json.loads({row["name"]: row for row in record["outputs"]}["seen.json"]["text"])
        frame, crop = seen.pop("frame"), seen.pop("flatCrop")
        self.assertEqual(seen, {"kind": "drawing", "svg": True, "hasRecipe": True, "revisionRef": drawing["revisionRef"]})
        # An elevation's record keeps its frame flat; every reader, a skill and the drawing's own answer
        # alike, is given it again under "frame" as for a cut plan (gap 16).
        self.assertEqual(drawing["viewRecipe"]["frame"]["crop_uv"], drawing["viewRecipe"]["crop_uv"])
        listed = self.client.get("/api/documents", params={"runId": drawing["runId"]}).json()["documents"]
        self.assertEqual([row["viewRecipe"]["frame"]["look"] for row in listed if row["revisionRef"] == drawing["revisionRef"]],
                         [drawing["viewRecipe"]["look"]])
        self.assertEqual((frame["crop_uv"], frame["kind"], crop), (drawing["viewRecipe"]["crop_uv"],
                                                                   "model-axis-elevation", drawing["viewRecipe"]["crop_uv"]))

    def test_a_failing_script_says_why(self):
        added = self.add()
        self.approve(1, added["codeSha256"])
        record = self.run_skill(parameters={"fail": True})
        self.assertEqual((record["status"], record["failureCode"]), ("failed", "SKILL_RUN_FAILED"))
        self.assertIn("the image is too dark", record["failureReason"])
        self.assertEqual(record.get("outputs"), None, "a failed run registers no outputs")

    def test_a_version_without_an_entry_is_not_runnable(self):
        response = self.client.post("/api/skills", json={"projectId": PROJECT_ID, "name": "site-palette",
                                                        "description": "Words only.", "body": "Do it by hand.\n"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertFalse(self.client.get("/api/skills").json()["skills"][0]["runnable"])
        self.assertEqual(self.start(status=422)["code"], "SKILL_NOT_RUNNABLE")
        for files, entry in (({"../run.py": "x"}, "../run.py"), ({"scripts/run.py": "x"}, "scripts/other.py")):
            bad = self.client.post("/api/skills", json={"projectId": PROJECT_ID, "name": "other", "description": "x",
                                                       "body": "x\n", "files": files, "entry": entry})
            self.assertEqual(bad.status_code, 422, bad.text)

    def test_the_page_may_send_the_id_encoded(self):
        added = self.add()
        response = self.client.post("/api/skills/skill%3Asite-palette/approvals",
                                    json={"projectId": PROJECT_ID, "version": 1, "codeSha256": added["codeSha256"]})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(self.client.get("/api/skills/skill%3Asite-palette").json()["approved"])


if __name__ == "__main__":
    unittest.main()
