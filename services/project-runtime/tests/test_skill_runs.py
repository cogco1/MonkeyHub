"""An installed skill runs in the person's own environment, as far as the person allowed what it declares (S-1).

A skill is a folder in the skill folders; a run names it and the project
retains the version it ran. What the skill declares is allowed once in the
Hub, for every skill or one, or for one run, and read here from the grants
file the launch environment names. The run is a background job whose record
names the grants it went ahead on, every input and output digest, the programs
it started, the hosts it reached, where it wrote, and what it tried beyond its
declaration.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
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
from project_runtime.application import artifacts, skill_access, skill_library, skill_runs, skills
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
NO_IMAGE = HEADER + '''open(os.path.join(args.outputs, "done.txt"), "w").write("ok")
'''
# This Python, as a skill would name it in its access: an interpreter, which allows anything.
PYTHON = Path(sys.executable).stem.lower()


def png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 6), (110, 117, 101)).save(buffer, format="PNG")
    return buffer.getvalue()


class SkillRunTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skill runs ")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.project, self.user, self.bundled = root / PROJECT_ID, root / "user skills", root / "bundled skills"
        self.user.mkdir()
        self.bundled.mkdir()
        self.grants_file = root / "config" / "skill-grants.json"
        self.grants: list[dict] = []
        for value in (patch.object(skill_library, "BUNDLED", self.bundled),
                      patch.dict(os.environ, {"MONKEYHUB_SKILL_PACKAGES": str(self.user),
                                              skill_access.GRANTS_ENV: str(self.grants_file)})):
            value.start()
            self.addCleanup(value.stop)
        FilesystemProjectRepository.initialize(
            self.project, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0},
        )
        self.client = TestClient(create_app(StudioSettings(project_dir=self.project, cad_export="off")))
        self.addCleanup(self.client.close)
        self.binding = bound_project(self.client.app.state)
        self.image = artifacts.save_document(self.binding, None, "site.png", "image/png",
                                             base64.b64encode(png()).decode("ascii"))

    # ---- the folder, the grants file, a run ----

    def install(self, script: str = SCRIPT, *, access: dict | None = None, entry: str | None = "scripts/run.py",
                root: Path | None = None, manifest: dict | None = None) -> str:
        """Put the site-palette package in a skill folder, as a person does; its code hash."""

        folder = (root or self.user) / "site-palette"
        (folder / "scripts").mkdir(parents=True, exist_ok=True)
        spec = {"name": "site-palette", "description": "A colour card from an image."}
        if entry is not None:
            spec["entry"] = entry
        if access is not None:
            spec["access"] = access
        if manifest is not None:
            spec["manifest"] = manifest
        (folder / "skill.json").write_text(json.dumps(spec), encoding="utf-8")
        (folder / "SKILL.md").write_text("Run on one registered image.\n", encoding="utf-8")
        (folder / "scripts" / "run.py").write_text(script, encoding="utf-8")
        return str(skill_library.installed().find("site-palette").code_sha256)

    def allow(self, access: dict, *, skill: str | None = None, once: dict | None = None,
              grant_id: str | None = None) -> str:
        """What the Hub writes when a person answers a card: one grant in its file."""

        grant_id = grant_id or f"grant-{len(self.grants) + 1}"
        row = {"grantId": grant_id, "access": access, "grantedAt": "2026-10-24T10:00:00+00:00", "method": "card"}
        if skill is not None:
            row["skill"] = {"name": skill, "source": "user"}
        if once is not None:
            row["once"] = {**once, "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()}
        self.grants.append(row)
        self.write_grants()
        return grant_id

    def write_grants(self) -> None:
        self.grants_file.parent.mkdir(parents=True, exist_ok=True)
        self.grants_file.write_text(json.dumps({"schema": skill_access.GRANTS_SCHEMA, "grants": self.grants}),
                                    encoding="utf-8")

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

    def retained(self) -> list[int]:
        latest = skills.latest_version(self.binding, "skill:site-palette")
        return [] if latest is None else [row.version for row in skills.skill_versions(self.binding, "skill:site-palette")]

    # ---- what needs allowing ----

    def test_the_records_are_registered_kinds(self):
        self.assertEqual(require_registered(STUDIO_SKILL_APPROVAL).schema, "StudioSkillApproval@1")
        self.assertEqual(require_registered(STUDIO_SKILL_REVOCATION).schema, "StudioSkillRevocation@1")
        self.assertEqual(require_registered(STUDIO_SKILL_RUN).schema, "StudioSkillRun@1")

    def test_an_installed_skill_that_needs_nothing_runs_and_the_project_keeps_the_version_it_ran(self):
        code = self.install()
        started = self.start(parameters={"share": 0.25})
        self.assertEqual(started["status"], "queued")
        record = self.finish(started)
        self.assertEqual(record["status"], "succeeded", record)
        self.assertEqual((record["skillId"], record["version"], record["codeSha256"]), ("skill:site-palette", 1, code))
        self.assertEqual((record["grant"]["source"], record["grant"]["grants"], record["grant"]["onceGrantId"]),
                         ("user", [], None), "nothing to allow, no grant used")
        self.assertEqual(record["inputs"][0]["assetSha256"], self.image.asset_sha256)
        outputs = {row["name"]: row for row in record["outputs"]}
        self.assertEqual(outputs["copy.png"]["sha256"], self.image.asset_sha256)
        self.assertEqual(outputs["copy.png"]["document"]["runId"], record["runId"])
        self.assertEqual(outputs["result.csv"]["text"], "hex,share\n#6E7565,0.25\n")
        self.assertEqual(json.loads(outputs["run.json"]["text"]), {"run": record["runId"]})
        self.assertEqual(record["observed"], {"processes": [], "network": [], "writes": []})
        downloaded = self.client.get(f"/api/skills/runs/{record['runId']}/outputs/copy.png")
        self.assertEqual(hashlib.sha256(downloaded.content).hexdigest(), self.image.asset_sha256)
        self.assertEqual(self.run_skill()["version"], 1, "the same package runs as the same version")
        self.install(SCRIPT + "# changed\n")
        self.assertEqual(self.run_skill()["version"], 2, "a changed package is retained as the next version")
        self.assertEqual(self.retained(), [1, 2])

    def test_what_a_skill_declares_is_allowed_first_and_once_for_every_skill(self):
        code = self.install(NO_IMAGE, access={"network": True, "programs": ["Blender.exe"], "read": ["C:\\Windows\\Fonts"]})
        refused = self.start(status=403, inputs={})
        self.assertEqual(refused["code"], "SKILL_ACCESS_NEEDED")
        self.assertEqual((refused["needs"], refused["broad"]), ({"network": True, "programs": ["blender"]}, {}),
                         "reading is not asked about")
        self.assertEqual((refused["skill"], refused["projectId"], refused["codeSha256"]),
                         ({"name": "site-palette", "source": "user"}, PROJECT_ID, code))
        self.assertEqual((self.skill_runs(), self.retained()), ([], []), "nothing ran and nothing was retained")
        first = self.allow({"programs": ["blender"]})
        self.assertEqual(self.start(status=403, inputs={})["needs"], {"network": True}, "only what is still missing")
        second = self.allow({"network": True})
        record = self.run_skill(inputs={})
        self.assertEqual(record["status"], "succeeded", record)
        self.assertEqual([row["grantId"] for row in record["grant"]["grants"]], [first, second])
        self.install(NO_IMAGE + "# a new version\n", access={"programs": ["blender"]})
        self.assertEqual(self.run_skill(inputs={})["version"], 2, "a new version asking no more is not asked about")

    def test_a_grant_for_one_skill_holds_for_that_skill_only(self):
        self.install(NO_IMAGE, access={"programs": ["blender"]})
        self.allow({"programs": ["blender"]}, skill="other-skill")
        self.assertEqual(self.start(status=403, inputs={})["needs"], {"programs": ["blender"]})
        self.allow({"programs": ["blender"]}, skill="site-palette")
        self.assertEqual(self.run_skill(inputs={})["status"], "succeeded")

    def test_a_one_run_grant_allows_exactly_one_run_of_that_code(self):
        code = self.install(NO_IMAGE, access={"programs": ["blender"]})
        other = self.allow({"programs": ["blender"]}, skill="site-palette",
                           once={"projectId": PROJECT_ID, "codeSha256": "0" * 64})
        self.assertEqual(self.start(status=403, inputs={}, grantId=other)["code"], "SKILL_ACCESS_NEEDED", "other code")
        once = self.allow({"programs": ["blender"]}, skill="site-palette", once={"projectId": PROJECT_ID, "codeSha256": code})
        self.assertEqual(self.start(status=403, inputs={})["code"], "SKILL_ACCESS_NEEDED", "it is not a lasting grant")
        record = self.run_skill(inputs={}, grantId=once, agentRun=True)
        self.assertEqual((record["status"], record["grant"]["onceGrantId"], record["requestedBy"]["agent"]),
                         ("succeeded", once, True))
        self.assertEqual(self.start(status=403, inputs={}, grantId=once)["code"], "SKILL_ACCESS_NEEDED", "one run")

    def test_a_withdrawn_grant_allows_nothing(self):
        self.install(NO_IMAGE, access={"programs": ["blender"]})
        self.allow({"programs": ["blender"]})
        self.grants[0]["revokedAt"] = "2026-10-24T11:00:00+00:00"
        self.write_grants()
        self.assertEqual(self.start(status=403, inputs={})["code"], "SKILL_ACCESS_NEEDED")

    def test_an_item_that_allows_anything_counts_only_for_its_own_skill(self):
        script = HEADER + '''subprocess.run([sys.executable, "-c", "pass"], check=True)
open(os.path.join(args.outputs, "done.txt"), "w").write("ok")
'''
        self.install(script, access={"programs": [PYTHON]})
        self.allow({"programs": [PYTHON]})
        refused = self.start(status=403, inputs={})
        self.assertEqual((refused["needs"], refused["broad"]), ({"programs": [PYTHON]}, {"programs": [PYTHON]}),
                         "a grant for every skill does not reach an interpreter")
        self.allow({"programs": [PYTHON]}, skill="site-palette")
        record = self.run_skill(inputs={})
        self.assertEqual(record["status"], "succeeded", record)
        self.assertEqual(record["access"]["programs"], [PYTHON])
        self.assertEqual([row["program"] for row in record["observed"]["processes"]], [PYTHON])

    def test_a_damaged_grants_file_allows_nothing(self):
        self.install(NO_IMAGE, access={"programs": ["blender"]})
        self.grants_file.parent.mkdir(parents=True, exist_ok=True)
        self.grants_file.write_text("{", encoding="utf-8")
        self.assertEqual(self.start(status=409, inputs={})["code"], "SKILL_GRANTS_UNREADABLE")

    # ---- which package a name means ----

    def test_a_name_no_folder_holds_and_a_package_that_changed_since_it_was_read(self):
        self.assertEqual(self.start(status=404)["code"], "SKILL_NOT_INSTALLED")
        self.install()
        self.assertEqual(self.run_skill(version=None)["version"], 1)
        self.install(SCRIPT + "# changed\n")
        self.assertEqual(self.start(status=409, version=1)["code"], "SKILL_CHANGED",
                         "the caller read version 1; the folder now holds other code")
        self.assertEqual(self.retained(), [1], "and it is not retained by a refused run")

    def test_the_users_package_is_used_where_a_shipped_one_has_the_same_name(self):
        self.install(NO_IMAGE + "# shipped\n", root=self.bundled)
        self.assertEqual(self.run_skill(inputs={})["grant"]["source"], "bundled")
        self.install(NO_IMAGE + "# mine\n")
        record = self.run_skill(inputs={})
        self.assertEqual((record["grant"]["source"], record["version"]), ("user", 2))

    def test_a_package_without_an_entry_is_not_runnable(self):
        self.install(entry=None)
        self.assertEqual(self.start(status=422)["code"], "SKILL_NOT_RUNNABLE")
        for files, entry in (({"../run.py": "x"}, "../run.py"), ({"scripts/run.py": "x"}, "scripts/other.py")):
            bad = self.client.post("/api/skills", json={"projectId": PROJECT_ID, "name": "other", "description": "x",
                                                       "body": "x\n", "files": files, "entry": entry})
            self.assertEqual(bad.status_code, 422, bad.text)

    def test_the_declared_access_is_part_of_the_code_hash_and_is_checked(self):
        plain = skills.code_sha256("x\n", "scripts/run.py", {"scripts/run.py": "pass"})
        wider = skills.code_sha256("x\n", "scripts/run.py", {"scripts/run.py": "pass"},
                                   {"network": True, "programs": [], "ssh": [], "read": [], "write": [], "packages": []})
        self.assertNotEqual(plain, wider)
        for bad in ({"packages": ["--index-url=http://x"]}, {"write": ["relative/folder"]}, {"shell": True}):
            response = self.client.post("/api/skills", json={
                "projectId": PROJECT_ID, "name": "bad", "description": "x", "body": "x\n",
                "files": {"scripts/run.py": "pass"}, "entry": "scripts/run.py", "access": bad})
            self.assertEqual(response.status_code, 422, (bad, response.text))

    # ---- what the run is held to ----

    def test_what_was_not_declared_is_refused_and_named(self):
        script = HEADER + '''import socket
if params.get("program"):
    subprocess.run([sys.executable, "-c", "pass"], check=True)
if params.get("network"):
    socket.create_connection(("192.0.2.1", 9), timeout=1)
'''
        self.install(script)
        for parameters, category in (({"program": True}, "programs"), ({"network": True}, "network")):
            with self.subTest(category=category):
                record = self.run_skill(inputs={}, parameters=parameters)
                self.assertEqual((record["status"], record["failureCode"]), ("failed", "SKILL_PERMISSION_NEEDED"), record)
                self.assertEqual(record["needed"][0]["category"], category)

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
        self.install(self.ENV_SCRIPT, access=access)
        if access:
            self.allow(access)
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
        self.assertEqual([name for name in seen if name.upper().startswith(("ARCHFLOW_STUDIO_", "MONKEYHUB_SKILL_GRANTS"))],
                         [], "nor any of the runtime's own settings, nor where the grants are")
        self.assertEqual(seen["SKILL_TEST_KEEP"], "kept", "the person's other settings stay")
        self.assertIn("PATH", {name.upper() for name in seen})
        self.assertEqual(seen["MONKEYHUB_SKILL_RUN"], record["runId"], "the run's own variables are set")

    def test_the_ssh_agent_is_kept_only_for_a_skill_that_declares_ssh(self):
        _, seen = self.environment_of_a_run(access={"ssh": ["vast-gpu"]})
        self.assertEqual(seen.get("SSH_AUTH_SOCK"), self.SECRETS["SSH_AUTH_SOCK"])
        self.assertNotIn(self.SECRETS["ARCHFLOW_STUDIO_RENDER_API_KEY"], json.dumps(seen))

    @unittest.skipUnless(sys.platform == "win32", "Windows Credential Manager")
    def test_the_credentials_the_hub_saved_are_refused_to_a_skill(self):
        script = HEADER + '''import ctypes
ctypes.windll.advapi32.CredReadW
open(os.path.join(args.outputs, "read.txt"), "w").write("reached")
'''
        self.install(script)
        record = self.run_skill(inputs={})
        self.assertEqual((record["status"], record["failureCode"]), ("failed", "SKILL_CREDENTIALS_REFUSED"), record)
        self.assertEqual([(row["category"], row["detail"]) for row in record["needed"]], [("credentials", "CredReadW")])

    # ---- inputs and outputs ----

    def test_a_local_file_input_and_a_binary_output(self):
        source = Path(tempfile.mkdtemp(prefix="skill input ")) / "points.npz"
        source.write_bytes(b"\x93NUMPY fake")
        self.install(HEADER + '''data = open(os.path.join(args.inputs, manifest["points"]["file"]), "rb").read()
open(os.path.join(args.outputs, "points-copy.npz"), "wb").write(data)
''')
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
        self.install(HEADER + '''shutil.copyfile(os.path.join(args.inputs, manifest["site"]["file"]), os.path.join(args.outputs, "merged.3dm"))
open(os.path.join(args.outputs, "plan.svg"), "w", encoding="utf-8").write("<svg xmlns='http://www.w3.org/2000/svg'/>")
print(json.dumps({"kind": manifest["site"]["kind"], "text": "场地"}, ensure_ascii=False))
''')
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
        self.install(HEADER + '''entry = manifest["top"]
svg = open(os.path.join(args.inputs, entry["file"]), encoding="utf-8").read()
recipe = json.load(open(os.path.join(args.inputs, entry["document"]), encoding="utf-8"))
open(os.path.join(args.outputs, "seen.json"), "w", encoding="utf-8").write(json.dumps(
    {"kind": entry["kind"], "svg": svg.lstrip().startswith("<"), "hasRecipe": bool(recipe.get("viewRecipe")),
     "revisionRef": recipe["revisionRef"], "frame": recipe["viewRecipe"].get("frame"),
     "flatCrop": recipe["viewRecipe"].get("crop_uv")}))
''')
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

    @unittest.skipUnless(os.name == "nt", "a pending synchronous pipe read holds up the children that inherit it on Windows")
    def test_a_run_finishes_while_the_studio_stdin_is_waited_on(self):
        # Gap 29: the Hub starts a Studio with --managed-stdin and one of its
        # threads always waits to read that pipe; a skill run that inherited
        # it never got past its own Python start. Here the same granted run
        # goes in a process whose stdin is a held pipe with a read pending.
        code = ("import sys, threading, unittest\n"
                "threading.Thread(target=sys.stdin.readline, daemon=True).start()\n"
                "test = 'SkillRunTest.test_an_installed_skill_that_needs_nothing_runs_and_the_project_keeps_the_version_it_ran'\n"
                "result = unittest.main(module='test_skill_runs', argv=['managed', test], exit=False).result\n"
                "sys.exit(0 if result.wasSuccessful() and result.testsRun == 1 else 1)\n")
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join(path for path in sys.path if path)}
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen([sys.executable, "-X", "utf8", "-c", code], cwd=str(Path(__file__).parent),
                                     stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, env=environment)
            try:
                returned = child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(child.pid)], stdin=subprocess.DEVNULL,
                               capture_output=True, check=False)
                child.wait()
                returned = None
            finally:
                child.stdin.close()
            output.seek(0)
            told = output.read().decode("utf-8", "replace")[-3000:]
        self.assertEqual(returned, 0, told)

    def test_an_output_name_that_leaves_the_outputs_folder_fails_the_run(self):
        # A run.py that joins a name from its parameters into the outputs path:
        # "../x" lands beside the run's own files, and is refused however the
        # skill takes the refusal.
        script = HEADER + '''name = params["name"]
try:
    with open(os.path.join(args.outputs, name), "w", encoding="utf-8") as out:
        out.write("drawing")
except PermissionError:
    if not params.get("catch"):
        raise
with open(os.path.join(args.outputs, "kept.csv"), "w", encoding="utf-8") as out:
    out.write("a,b\\n")
'''
        self.install(script)
        for name, catch in (("../stray.pdf", False), ("../audit.jsonl", True), ("../../outside.pdf", True)):
            record = self.run_skill(parameters={"name": name, "catch": catch})
            self.assertEqual((record["status"], record["failureCode"]), ("failed", "SKILL_OUTPUT_INVALID"), (name, record))
            self.assertIn("outside its outputs folder", record["failureReason"])
            self.assertEqual(record.get("outputs"), None, "a run that strayed registers nothing")
        kept = self.run_skill(parameters={"name": "A-101_section.txt"})
        self.assertEqual(kept["status"], "succeeded", kept)
        self.assertEqual(sorted(row["name"] for row in kept["outputs"]), ["A-101_section.txt", "kept.csv"])

    def test_a_failing_script_says_why(self):
        self.install()
        record = self.run_skill(parameters={"fail": True})
        self.assertEqual((record["status"], record["failureCode"]), ("failed", "SKILL_RUN_FAILED"))
        self.assertIn("the image is too dark", record["failureReason"])
        self.assertEqual(record.get("outputs"), None, "a failed run registers no outputs")
        self.assertEqual((record["failureNext"], record["failureNextWords"].startswith("Change the input or parameter")),
                         ("fix_input", True), "a failure says what to do about it")

    # ---- a skill that returns nothing, or not what it says it returns ----

    MANIFEST = {"inputs": ["image: 一张登记过的图片", "params: share，默认 0.5"],
                "outputs": ["copy.png: 原图的副本", "result.csv: 颜色和占比", "<name>_extra.png: 只有给了 extra 才有",
                            "run.json: 本次运行的标识"], "permissions": []}

    def test_a_run_that_wrote_no_file_is_a_failure_not_a_success(self):
        # Before: the script printed, exited 0, and the run counted as succeeded with nothing to use.
        self.install(HEADER + 'print("all done")\n')
        record = self.run_skill(inputs={})
        self.assertEqual((record["status"], record["failureCode"]), ("failed", "SKILL_OUTPUT_MISSING"), record)
        self.assertIn("all done", record["failureReason"], "the failure carries what the skill last said")
        self.assertEqual((record["failureNext"], record.get("outputs")), ("stop", None))

    def test_the_files_a_manifest_names_are_checked_without_taking_a_run_away(self):
        self.install(manifest=self.MANIFEST)
        passed = self.run_skill()
        self.assertEqual((passed["status"], passed["verification"]), (
            "succeeded", {"status": "passed", "declared": ["copy.png", "result.csv", "run.json"], "missing": []}),
            "a placeholder name (<name>_extra.png) names no one file, so it is not asked for")
        # The script now forgets result.csv: its other files are still kept, and the record says what is missing.
        forgetful = SCRIPT.replace('with open(os.path.join(args.outputs, "result.csv"), "w", encoding="utf-8", newline="") as out:\n    out.write("hex,share\\n#6E7565,%s\\n" % params.get("share", 0.5))\n', "")
        self.assertNotEqual(forgetful, SCRIPT)
        self.install(forgetful, manifest=self.MANIFEST)
        partial = self.run_skill()
        self.assertEqual((partial["status"], partial["verification"]["status"], partial["verification"]["missing"]),
                         ("succeeded", "failed", ["result.csv"]))
        self.assertEqual(sorted(row["name"] for row in partial["outputs"]), ["copy.png", "run.json"])
        # A manifest that names no file by a plain name leaves the check inconclusive, not failed.
        self.install()
        self.assertEqual(self.run_skill()["verification"]["status"], "inconclusive")

    def test_a_missing_input_is_named_instead_of_a_bare_key_error(self):
        self.install(manifest=self.MANIFEST)
        record = self.run_skill(inputs={})
        self.assertEqual((record["status"], record["failureCode"], record["failureNext"]),
                         ("failed", "SKILL_INPUT_MISSING", "fix_input"), record)
        self.assertIn("needs the input 'image' (一张登记过的图片)", record["failureReason"])
        self.assertIn("this request gave no inputs", record["failureReason"])
        # Without a manifest line for it the failure stays the script's own words.
        self.install()
        self.assertEqual(self.run_skill(inputs={})["failureCode"], "SKILL_RUN_FAILED")

    def test_the_words_of_a_manifest_line_are_read_the_way_the_shipped_skills_write_them(self):
        manifest = {"inputs": [
            "model: 项目内已登记的模型资产（3dm）", "mesh（代替 model）: 本机网格文件 .glb", "params: style 取 params/<style>.json",
            "<名称>: Hub 视图图纸（kind drawing）", "site: 项目内已登记的场地模型：含地块曲线"],
            "outputs": ["blender-<style>.png: 渲染结果", "merged.3dm: 单位 Feet", "merge_report.json：变换和数量",
                        "<pdf_name>: 一个多页 PDF", "summary.json: 每张图的输入"]}
        self.assertEqual(skill_runs.declared_inputs(manifest), {
            "model": "项目内已登记的模型资产（3dm）", "mesh": "本机网格文件 .glb", "site": "项目内已登记的场地模型：含地块曲线"})
        self.assertEqual(skill_runs.declared_outputs(manifest), ["merged.3dm", "merge_report.json", "summary.json"])
        self.assertEqual(skill_runs.declared_outputs(None), [])
        self.assertEqual(skill_runs.verify_outputs(manifest, ["sub/summary.json", "merged.3dm"])["missing"], ["merge_report.json"],
                         "a declared file counts when it is under any subfolder")

    # ---- what to do after a failure ----

    def test_every_refusal_and_failure_says_what_to_do_next(self):
        refused = self.start(status=404, inputs={}, parameters={})
        self.assertEqual(refused["code"], "SKILL_NOT_INSTALLED")
        self.assertIn("Next (ask_person)", refused["detail"])
        self.install(access={"programs": ["blender"]})
        needed = self.start(status=403, inputs={})
        self.assertEqual(needed["code"], "SKILL_ACCESS_NEEDED")
        self.assertIn("Next (ask_person)", needed["detail"])
        self.assertEqual({code: step for code, (step, _) in skill_runs.NEXT_STEPS.items()}["SKILL_REPEATED_FAILURE"], "stop")
        self.assertEqual(skill_runs.next_step("A_CODE_NOBODY_KNEW")[0], "stop", "an unknown failure stops rather than retries")
        for code, (step, words) in skill_runs.NEXT_STEPS.items():
            self.assertIn(step, ("fix_input", "ask_person", "stop"), code)
            self.assertTrue(words.endswith("."), code)

    def test_an_agent_is_not_given_a_third_run_of_the_request_that_failed_twice(self):
        self.install()
        fails = {"parameters": {"fail": True}, "agentRun": True}
        for _ in range(2):
            self.assertEqual(self.run_skill(**fails)["status"], "failed")
        third = self.start(status=409, **fails)
        self.assertEqual(third["code"], "SKILL_REPEATED_FAILURE")
        self.assertIn("failed twice with exactly this request (SKILL_RUN_FAILED)", third["detail"])
        self.assertIn("Next (stop)", third["detail"])
        self.assertEqual(len(self.skill_runs()), 2, "the refused one made no run")
        # Another request, a person's own run, changed code, or time passing each allow it again.
        self.assertEqual(self.run_skill(parameters={"fail": True, "other": 1}, agentRun=True)["status"], "failed")
        self.assertEqual(self.run_skill(parameters={"fail": True})["status"], "failed", "a person's run is not stopped")
        with patch.object(skill_runs, "REPEAT_WINDOW_S", 0.0):
            self.assertEqual(self.run_skill(**fails)["status"], "failed")
        self.install(SCRIPT + "# the person fixed it\n")
        self.assertEqual(self.run_skill(agentRun=True, parameters={"fail": True})["status"], "failed")
        self.assertEqual(self.run_skill(agentRun=True)["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
