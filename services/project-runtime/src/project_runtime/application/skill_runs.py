"""Run one granted skill version on project inputs and retain what it did.

A runnable skill version carries script files, names an ``entry`` and declares
the ``access`` it needs. It runs only under a grant a person gave on the Hub
page (``skills.require_granted``), in the person's own environment: the
runtime's Python without isolation, the user's environment variables, any
program, the network and the disk, as far as the grant reaches. An audit hook
installed before the entry runs holds the script to the grant: starting a
program, connecting out, ssh and writing outside the run's own folders are
checked against it, refused when not granted, and every one is recorded.

Runs are background jobs. Each gets its own P036 run ``skill-run-<id>`` whose
StudioSkillRun@1 records move from queued to running to succeeded or failed.
The record names the skill version, its code hash, the grant used (who gave
it, when, for what), every input and output digest, the programs it started,
the hosts it reached, where it wrote outside its folders, and what it tried
that was not granted. PDF, PNG and JPEG outputs are registered as project
documents, 3dm outputs as model assets with an ``undrawable`` report, small
text outputs are kept in the record, and any other file is retained as a
content-addressed project object.

Contract the entry script follows::

    python -B -X utf8 <bootstrap> <entry> --inputs <dir> --params <file.json> --outputs <dir>

``<inputs>/manifest.json`` maps each input name to its file, SHA-256, media type
and kind (``document``, ``model``, ``drawing`` with its registration under
``document``, or ``file``). What an audit hook cannot see (native code,
ctypes, programs started by those programs) is outside the check: the grant
is the person's judgement of the code, and the record is what they can read.
"""

from __future__ import annotations

import base64
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_SKILL_RUN
from archflow.project.repository import ProjectRepositoryError

from ..authentication import ActorAttribution
from ..binding import ProjectBinding, retained_sources
from ..errors import StudioError
from . import artifacts, skills

RUN_PREFIX = "skill-run-"
RUN_SCHEMA = "StudioSkillRun@1"
RUN_TIMEOUT_S = 4 * 3600.0
INSTALL_TIMEOUT_S = 1800.0
INPUT_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
OUTPUT_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}")
MAX_INPUTS = 32
MAX_FILE_INPUT_BYTES = 512 * 1024 * 1024
MAX_PARAMETERS_BYTES = 256 * 1024
MAX_OUTPUTS = 256
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
MAX_TEXT_INLINE_BYTES = 256 * 1024
MAX_SVG_INLINE_BYTES = 4 * 1024 * 1024
MAX_OBSERVED = 500
TAIL_CHARS = 4000
INPUT_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}
DOCUMENT_OUTPUTS = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
TEXT_OUTPUTS = {".json": "application/json", ".csv": "text/csv", ".txt": "text/plain", ".md": "text/markdown",
                ".svg": "image/svg+xml", ".geojson": "application/geo+json"}
MODEL_OUTPUTS = {".3dm": "model/vnd.3dm"}
STATUS_RANK = {"queued": 0, "running": 1, "failed": 2, "succeeded": 2}
ENVIRONMENT_ROOT_ENV = "MONKEYHUB_SKILL_ENVS"
PIP_PYTHON_ENV = "MONKEYHUB_SKILL_PIP_PYTHON"

Runner = Callable[..., subprocess.CompletedProcess]


def _invalid(message: str) -> StudioError:
    return StudioError(422, "SKILL_RUN_INVALID", message)


def _destination(run_id: str) -> PersistenceDestination:
    return PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)


def _tail(text: str | None) -> str:
    return (text or "")[-TAIL_CHARS:]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- run records -------------------------------------------------------------


def _save(binding: ProjectBinding, run, payload: dict) -> None:
    try:
        binding.repository.put_json(run=run, destination=_destination(run.run_id), record_kind=STUDIO_SKILL_RUN,
                                    payload=payload)
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(409, "SKILL_RUN_WRITE_FAILED", "The skill run record could not be retained.") from exc


def run_report(binding: ProjectBinding, run_id: str) -> dict:
    """The run's latest record: queued, running, succeeded or failed."""

    if not run_id.startswith(RUN_PREFIX) or run_id not in binding.run_ids():
        raise StudioError(404, "SKILL_RUN_NOT_FOUND", f"No skill run {run_id}.")
    rows = [binding.repository.load_json(ref) for ref in binding.record_refs(run_id, kind=STUDIO_SKILL_RUN)]
    rows = [row for row in rows if row.get("schema") == RUN_SCHEMA]
    if not rows:
        raise StudioError(404, "SKILL_RUN_NOT_FOUND", f"No skill run {run_id}.")
    return max(rows, key=lambda row: (STATUS_RANK.get(row.get("status"), -1), str(row.get("updatedAt"))))


def list_skill_runs(binding: ProjectBinding, skill_id: str | None = None) -> tuple[dict, ...]:
    """Every retained skill run's latest record, or one skill's, oldest first."""

    rows = []
    for run_id in binding.run_ids():
        if not run_id.startswith(RUN_PREFIX):
            continue
        try:
            row = run_report(binding, run_id)
        except StudioError:
            continue
        if skill_id is None or row.get("skillId") == skill_id:
            rows.append(row)
    return tuple(sorted(rows, key=lambda row: str(row.get("submittedAt"))))


def used_once_grants(binding: ProjectBinding) -> set[str]:
    """Once grants a run has taken: each allows exactly one run, whatever became of it."""

    return {str(row["grant"]["approvalId"]) for row in list_skill_runs(binding)
            if (row.get("grant") or {}).get("scope") == "once" and row["grant"].get("approvalId")}


# ---- inputs, outputs ----------------------------------------------------------


def _write_skill(folder: Path, row: skills.SkillVersion) -> None:
    (folder / "SKILL.md").write_text(str(row.payload["body"]), encoding="utf-8")
    for relative, text in row.files.items():
        target = folder.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


# The view kind an elevation's registration states at its top level: its frame
# keys lie flat there, as its retained receipt has them, where a cut plan keeps
# them under "frame".
def _drawing_entry(document) -> dict:
    """The drawing's registration as the documents list states it: what a skill needs to place its lines."""

    source = document.model_source
    return {"runId": document.run_id, "assetSha256": document.asset_sha256, "revisionRef": document.revision_ref,
            "fileName": document.file_name, "drawingId": document.drawing_id, "generatedAt": document.generated_at,
            "modelSource": None if source is None else {"runId": source.run_id, "stateDigest": source.state_digest,
                                                       "assetSha256": source.asset_sha256},
            "sourceStageRef": document.source_stage_ref, "viewRecipe": artifacts.view_recipe_with_frame(document.view_recipe),
            "pages": [{"pageIndex": page.page_index, "width": page.width, "height": page.height} for page in document.pages]}


def _write_inputs(binding: ProjectBinding, folder: Path, inputs: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    if len(inputs) > MAX_INPUTS:
        raise _invalid(f"A run takes at most {MAX_INPUTS} inputs.")
    manifest, rows = {}, []
    for name, ref in sorted(inputs.items()):
        if not INPUT_NAME.fullmatch(name):
            raise _invalid(f"{name!r} is not an input name: lowercase letters, digits and _.")
        kind = ref.get("kind")
        run_id, asset, model = ref.get("runId"), ref.get("assetSha256"), ref.get("modelSha256")
        if kind == "file":
            source = Path(str(ref.get("path") or ""))
            if not source.is_absolute() or not source.is_file():
                raise _invalid(f"Input {name}: path must name an existing file on this machine.")
            if source.stat().st_size > MAX_FILE_INPUT_BYTES:
                raise StudioError(413, "SKILL_INPUT_TOO_LARGE", f"Input {name} is over {MAX_FILE_INPUT_BYTES >> 20} MiB.")
            data = source.read_bytes()
            file_name = name + source.suffix.lower()
            (folder / file_name).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            manifest[name] = {"file": file_name, "sha256": digest, "mimeType": "application/octet-stream", "kind": "file"}
            rows.append({"name": name, "kind": "file", "path": str(source), "sha256": digest, "bytes": len(data)})
            continue
        if kind == "drawing":
            if not isinstance(run_id, str) or not isinstance(asset, str):
                raise _invalid(f"Input {name}: a drawing is named by runId, assetSha256 and its revisionRef.")
            from .drawings import drawing_file

            revision = ref.get("revisionRef")
            svg, _, _ = drawing_file(binding, run_id=run_id, asset_sha256=asset, revision_ref=revision, file_format="svg")
            document, _ = artifacts.document_bytes(binding, run_id, asset, revision)
            (folder / f"{name}.svg").write_bytes(svg)
            (folder / f"{name}.json").write_text(json.dumps(_drawing_entry(document), ensure_ascii=False, indent=1),
                                                encoding="utf-8")
            digest = hashlib.sha256(svg).hexdigest()
            manifest[name] = {"file": f"{name}.svg", "document": f"{name}.json", "sha256": digest,
                              "mimeType": "image/svg+xml", "kind": "drawing"}
            rows.append({"name": name, "kind": "drawing", "runId": document.run_id, "assetSha256": document.asset_sha256,
                         "revisionRef": document.revision_ref, "svgSha256": digest, "fileName": document.file_name})
            continue
        if model is not None:
            record, data = artifacts.artifact_bytes(binding, model, run_id=run_id)
            if record.format != "3dm":
                raise _invalid(f"Input {name}: {model} is a {record.format} artifact; a model input is a 3dm.")
            file_name = name + ".3dm"
            (folder / file_name).write_bytes(data)
            manifest[name] = {"file": file_name, "sha256": model, "mimeType": "model/vnd.3dm", "kind": "model"}
            rows.append({"name": name, "kind": "model", "runId": record.run_id, "modelSha256": model,
                         "fileName": record.file_name, "mimeType": "model/vnd.3dm"})
            continue
        if not isinstance(run_id, str) or not isinstance(asset, str):
            raise _invalid(f"Input {name} names a registered document by runId and assetSha256, a model asset by "
                           "modelSha256, a drawing by kind \"drawing\", or a local file by kind \"file\" and path.")
        document, data = artifacts.document_bytes(binding, run_id, asset)
        file_name = name + INPUT_SUFFIXES.get(document.mime_type, ".bin")
        (folder / file_name).write_bytes(data)
        manifest[name] = {"file": file_name, "sha256": document.asset_sha256, "mimeType": document.mime_type,
                          "kind": "document"}
        rows.append({"name": name, "kind": "document", "runId": document.run_id, "assetSha256": document.asset_sha256,
                     "fileName": document.file_name, "mimeType": document.mime_type})
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def _read_outputs(folder: Path) -> list[tuple[str, bytes]]:
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    if len(files) > MAX_OUTPUTS:
        raise StudioError(422, "SKILL_OUTPUT_INVALID", f"The skill wrote {len(files)} files; at most {MAX_OUTPUTS} are kept.")
    outputs = []
    for path in files:
        name = path.relative_to(folder).as_posix()
        if any(not OUTPUT_PART.fullmatch(part) for part in name.split("/")):
            raise StudioError(422, "SKILL_OUTPUT_INVALID", f"{name} is not a plain output path.")
        if path.stat().st_size > MAX_OUTPUT_BYTES:
            raise StudioError(413, "SKILL_OUTPUT_TOO_LARGE", f"{name} is over {MAX_OUTPUT_BYTES >> 20} MiB.")
        outputs.append((name, path.read_bytes()))
    return outputs


def _retain_outputs(binding: ProjectBinding, run, outputs: list[tuple[str, bytes]]) -> list[dict]:
    rows = []
    for index, (name, data) in enumerate(outputs):
        suffix = Path(name).suffix.lower()
        flat = name.replace("/", "__")
        row: dict[str, Any] = {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        if suffix in DOCUMENT_OUTPUTS and len(data) <= artifacts.MAX_DOCUMENT_BYTES:
            document = artifacts.save_document(binding, run.run_id, flat, DOCUMENT_OUTPUTS[suffix],
                                               base64.b64encode(data).decode("ascii"))
            row |= {"mimeType": document.mime_type,
                    "document": {"runId": document.run_id, "assetSha256": document.asset_sha256}}
        elif suffix in MODEL_OUTPUTS:
            from monkeycad.backends.occt.native_models import drawable_report

            record = artifacts.register_model_asset(binding, None, None, flat, base64.b64encode(data).decode("ascii"))
            row |= {"mimeType": MODEL_OUTPUTS[suffix], "modelAsset": {"runId": record.run_id, "sha256": record.sha256},
                    "undrawable": drawable_report(data)}
        elif suffix in TEXT_OUTPUTS and len(data) <= (MAX_SVG_INLINE_BYTES if suffix == ".svg" else MAX_TEXT_INLINE_BYTES):
            try:
                row |= {"mimeType": TEXT_OUTPUTS[suffix], "text": data.decode("utf-8")}
            except UnicodeDecodeError as exc:
                raise StudioError(422, "SKILL_OUTPUT_INVALID", f"{name} is not UTF-8 text.") from exc
        else:
            stored = binding.repository.ingest(run=run, destination=PersistenceDestination(PersistenceArea.OBJECT),
                                               artifact_id=f"skill-output-{index}", media_type="application/octet-stream",
                                               source=BytesIO(data))
            row |= {"mimeType": TEXT_OUTPUTS.get(suffix, "application/octet-stream"), "object": asdict(stored)}
        rows.append(row)
    return rows


def output_bytes(binding: ProjectBinding, run_id: str, name: str) -> tuple[bytes, str]:
    """One retained output of a run, re-verified by its digest."""

    report = run_report(binding, run_id)
    row = next((item for item in report.get("outputs") or () if item.get("name") == name), None)
    if row is None:
        raise StudioError(404, "SKILL_OUTPUT_NOT_FOUND", f"Run {run_id} has no output {name}.")
    if "text" in row:
        data = row["text"].encode("utf-8")
    elif "document" in row:
        _, data = artifacts.document_bytes(binding, row["document"]["runId"], row["document"]["assetSha256"])
    elif "modelAsset" in row:
        _, data = artifacts.artifact_bytes(binding, row["modelAsset"]["sha256"], run_id=row["modelAsset"]["runId"])
    else:
        data = binding.repository.layout.resolve_relative(row["object"]["relative_path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise StudioError(409, "SKILL_OUTPUT_CHANGED", f"Output {name} no longer matches its retained digest.")
    return data, row.get("mimeType") or "application/octet-stream"


# ---- the Python packages a skill declares -------------------------------------


_install_lock = threading.Lock()


def _environment_root() -> Path:
    override = os.environ.get(ENVIRONMENT_ROOT_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "MonkeyHub" / "skill-envs"


def _pip_python(runner: Runner) -> list[str]:
    """A Python that has pip, to install wheels for the runtime's own Python (which ships without pip)."""

    candidates = [[os.environ[PIP_PYTHON_ENV]]] if os.environ.get(PIP_PYTHON_ENV) else []
    for name in ("py", "python", "python3"):
        found = shutil.which(name)
        if found and Path(found).resolve() != Path(sys.executable).resolve():
            candidates.append([found, "-3"] if name == "py" else [found])
    for command in candidates:
        try:
            probe = runner([*command, "-m", "pip", "--version"], capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return command
    raise StudioError(409, "SKILL_PACKAGES_UNAVAILABLE",
                      "This skill installs Python packages, and no Python with pip was found on this machine. "
                      f"Install Python with pip, or set {PIP_PYTHON_ENV} to one.")


# What a run's environment never carries. The runtime's own settings: among them
# the render key the Hub hands the runtime (ARCHFLOW_STUDIO_RENDER_API_KEY) and a
# team project's sync token (ARCHFLOW_STUDIO_SYNC_TOKEN). And anything named as a
# credential, wherever the Hub's own environment got it. A skill that needs a
# provider asks the Hub to call it; it is never given the key.
WITHHELD_PREFIXES = ("ARCHFLOW_STUDIO_", "MONKEYHUB_", "MONKEYMONITOR_")
CREDENTIAL_PARTS = frozenset({"KEY", "KEYS", "APIKEY", "TOKEN", "TOKENS", "SECRET", "SECRETS", "PASSWORD", "PASSWD",
                              "PASSPHRASE", "CREDENTIAL", "CREDENTIALS", "PAT", "COOKIE", "AUTH", "BEARER"})
CREDENTIAL_WORDS = ("APIKEY", "API_KEY", "ACCESSKEY", "ACCESS_KEY", "PRIVATEKEY", "PRIVATE_KEY", "TOKEN", "SECRET",
                    "PASSWORD", "PASSWD", "CREDENTIAL")


def _credential_name(name: str) -> bool:
    upper = name.upper()
    return bool(CREDENTIAL_PARTS & set(upper.replace("-", "_").split("_"))) or any(word in upper for word in CREDENTIAL_WORDS)


def skill_environment(base: Mapping[str, str], *, ssh: bool) -> dict[str, str]:
    """The environment a skill run starts with: the user's own, less the runtime's settings and every credential.

    ``SSH_AUTH_SOCK`` names an agent, not a secret; it is kept only for a
    version a person granted ssh hosts.
    """

    kept = {}
    for name, value in base.items():
        upper = name.upper()
        if upper.startswith(WITHHELD_PREFIXES):
            continue
        if _credential_name(name) and not (upper == "SSH_AUTH_SOCK" and ssh):
            continue
        kept[name] = value
    return kept


def _ensure_packages(row: skills.SkillVersion, runner: Runner) -> dict | None:
    """The skill's own package folder, installed once per requirement set and reused."""

    requirements = list((row.access or {}).get("packages") or ())
    if not requirements:
        return None
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    key = hashlib.sha256(json.dumps([tag, sorted(requirements)]).encode()).hexdigest()[:16]
    name = row.skill_id.removeprefix(skills.SKILL_ID_PREFIX)
    target = _environment_root() / f"{name}-{key}"
    marker = target / ".monkeyhub-environment.json"
    with _install_lock:
        if marker.is_file():
            return json.loads(marker.read_text(encoding="utf-8")) | {"reused": True}
        target.mkdir(parents=True, exist_ok=True)
        command = [*_pip_python(runner), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                   "--upgrade", "--target", str(target), "--python-version",
                   f"{sys.version_info.major}.{sys.version_info.minor}", "--implementation", "cp",
                   "--only-binary=:all:", *(["--platform", "win_amd64"] if os.name == "nt" else []), *requirements]
        try:
            done = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=INSTALL_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise StudioError(504, "SKILL_PACKAGES_TIMEOUT", "Installing the skill's packages took too long.") from exc
        if done.returncode != 0:
            raise StudioError(422, "SKILL_PACKAGES_FAILED",
                              "pip could not install " + ", ".join(requirements) + ": " + _tail(done.stderr)[-1500:])
        description = {"path": str(target), "requirements": requirements, "python": tag, "installedAt": _now(),
                       "pipTail": _tail(done.stdout)[-1500:]}
        marker.write_text(json.dumps(description, indent=1), encoding="utf-8")
        return description | {"reused": False}


# ---- running -------------------------------------------------------------------


def _granted_record(grant: Mapping[str, Any]) -> dict:
    return {key: grant.get(key) for key in ("approvalId", "scope", "codeSha256", "access", "approvedAt", "approvedBy",
                                            "method")}


def _observed(log: Path) -> tuple[dict, list]:
    processes, network, writes, denied = [], [], [], []
    if log.is_file():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.pop("event", None)
            target = {"process": processes, "shell": processes, "network": network, "write": writes,
                      "denied": denied}.get(kind)
            if target is not None and len(target) < MAX_OBSERVED:
                target.append(event if kind != "shell" else {"shell": event.get("command")})
    return {"processes": processes, "network": network, "writes": writes}, denied


def submit_skill_run(binding: ProjectBinding, jobs, skill_id: str, *, version: int | None,
                     inputs: Mapping[str, Mapping[str, Any]], parameters: Mapping[str, Any],
                     attribution: ActorAttribution, agent: bool = False, grant_id: str | None = None,
                     python: str | None = None, runner: Runner | None = None,
                     timeout_s: float = RUN_TIMEOUT_S) -> dict:
    """Check the grant now, retain the queued run, and run it as a background job."""

    run_command = subprocess.run if runner is None else runner
    row, grant = skills.require_granted(binding, skill_id, version, used_once=used_once_grants(binding),
                                        allow_once=not agent, grant_id=grant_id)
    parameters_text = json.dumps(dict(parameters), ensure_ascii=False, sort_keys=True)
    if len(parameters_text.encode("utf-8")) > MAX_PARAMETERS_BYTES:
        raise _invalid(f"Parameters are at most {MAX_PARAMETERS_BYTES // 1024} KiB of JSON.")
    run_id = RUN_PREFIX + uuid.uuid4().hex[:12]
    try:
        run = binding.repository.create_run(run_id)
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(409, "SKILL_RUN_WRITE_FAILED", "The skill run could not be created in this project.") from exc
    queued = {
        "schema": RUN_SCHEMA, "projectId": binding.project_id, "runId": run_id, "status": "queued",
        "skillId": row.skill_id, "version": row.version, "skillVersionRef": row.ref, "codeSha256": row.code_sha256,
        "entry": row.entry, "access": row.access, "grant": _granted_record(grant), "parameters": json.loads(parameters_text),
        "requestedBy": {"actorId": attribution.actor_id, "authenticated": attribution.authenticated,
                        "origin": attribution.origin, "agent": agent},
        "submittedAt": _now(), "updatedAt": _now(), "statusPath": f"/api/skills/runs/{run_id}",
    }
    _retain(binding, run, queued)

    def work():
        _execute(binding, run, row, queued, inputs, python=python, runner=run_command, timeout_s=timeout_s)

    try:
        jobs.submit(candidate_id=run_id, proposal_id=run_id, work=work, kind="skill-run")
    except Exception as exc:
        _retain(binding, run, queued | {"status": "failed", "updatedAt": _now(), "failureCode": "SKILL_RUN_NOT_STARTED",
                                        "failureReason": str(exc)[:500]})
        raise
    return {"runId": run_id, "status": "queued", "statusPath": queued["statusPath"], "grant": queued["grant"]}


@retained_sources
def _retain(binding: ProjectBinding, run, payload: dict) -> None:
    _save(binding, run, payload)


def _execute(binding: ProjectBinding, run, row: skills.SkillVersion, queued: dict,
             inputs: Mapping[str, Mapping[str, Any]], *, python: str | None, runner: Runner, timeout_s: float) -> None:
    current = queued | {"status": "running", "startedAt": _now(), "updatedAt": _now()}
    _retain(binding, run, current)
    try:
        result = _run_once(binding, run, row, current, inputs, python=python, runner=runner, timeout_s=timeout_s)
    except StudioError as exc:
        failed = current | {"status": "failed", "updatedAt": _now(), "failureCode": exc.code, "failureReason": exc.detail}
        failed |= getattr(exc, "run_details", {})
        _retain(binding, run, failed)
        raise
    except Exception as exc:
        _retain(binding, run, current | {"status": "failed", "updatedAt": _now(), "failureCode": "SKILL_RUN_FAILED",
                                         "failureReason": f"{type(exc).__name__}: {exc}"[:1500]})
        raise
    _retain(binding, run, current | result | {"status": "succeeded", "updatedAt": _now()})


def _run_once(binding: ProjectBinding, run, row: skills.SkillVersion, current: dict,
              inputs: Mapping[str, Mapping[str, Any]], *, python: str | None, runner: Runner, timeout_s: float) -> dict:
    environment = _ensure_packages(row, runner)
    with tempfile.TemporaryDirectory(prefix="monkeyhub-skill-") as folder:
        root = Path(folder)
        skill_dir, inputs_dir, outputs_dir = root / "skill", root / "inputs", root / "outputs"
        for directory in (skill_dir, inputs_dir, outputs_dir):
            directory.mkdir()
        _write_skill(skill_dir, row)
        input_rows = _write_inputs(binding, inputs_dir, inputs)
        params_path, access_path, audit_path, bootstrap = (root / "params.json", root / "access.json",
                                                           root / "audit.jsonl", root / "bootstrap.py")
        params_path.write_text(json.dumps(current["parameters"], ensure_ascii=False), encoding="utf-8")
        access_path.write_text(json.dumps({
            "access": row.access or {}, "roots": [str(root), tempfile.gettempdir(),
                                                  *([environment["path"]] if environment else [])],
        }, ensure_ascii=False), encoding="utf-8")
        bootstrap.write_text(BOOTSTRAP_SOURCE, encoding="utf-8")
        env = skill_environment(os.environ, ssh=bool((row.access or {}).get("ssh")))
        env |= {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "MONKEYHUB_SKILL_RUN": run.run_id, "MONKEYHUB_PROJECT_ID": binding.project_id,
                "MONKEYHUB_SKILL_ACCESS": str(access_path), "MONKEYHUB_SKILL_AUDIT": str(audit_path)}
        if environment:
            # The runtime's embedded Python ignores PYTHONPATH; the bootstrap puts these on sys.path itself.
            env["MONKEYHUB_SKILL_PATHS"] = environment["path"]
            env["PYTHONPATH"] = os.pathsep.join(filter(None, (environment["path"], os.environ.get("PYTHONPATH"))))
        command = [python or sys.executable, "-B", "-X", "utf8", str(bootstrap),
                   str(skill_dir.joinpath(*row.entry.split("/"))),
                   "--inputs", str(inputs_dir), "--params", str(params_path), "--outputs", str(outputs_dir)]
        clock = time.monotonic()
        try:
            finished = runner(command, cwd=str(skill_dir), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout_s, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise StudioError(504, "SKILL_RUN_TIMEOUT",
                              f"{row.skill_id} version {row.version} did not finish within {int(timeout_s)} s.") from exc
        duration = round(time.monotonic() - clock, 3)
        observed, denied = _observed(audit_path)
        details = {"inputs": input_rows, "environment": environment, "observed": observed, "needed": denied,
                   "exitCode": finished.returncode, "durationSeconds": duration,
                   "stdoutTail": _tail(finished.stdout), "stderrTail": _tail(finished.stderr)}
        if finished.returncode != 0:
            if any(item.get("category") == "credentials" for item in denied):
                failure = StudioError(403, "SKILL_CREDENTIALS_REFUSED",
                                      f"{row.skill_id} version {row.version} tried to read the credentials the Hub "
                                      "keeps, which no grant allows. A skill that needs a provider asks the Hub to "
                                      "call it.")
            elif denied:
                failure = StudioError(403, "SKILL_PERMISSION_NEEDED",
                                      f"{row.skill_id} version {row.version} tried what it was not granted: "
                                      + "; ".join(f"{item.get('category')} {item.get('detail')}" for item in denied[:5])
                                      + ". Declare it in the skill's access and allow that version on the Hub page.")
            else:
                failure = StudioError(422, "SKILL_RUN_FAILED",
                                      f"{row.skill_id} version {row.version} exited with {finished.returncode}: "
                                      + (_tail(finished.stderr).strip()[-1500:] or "no message"))
            failure.run_details = details
            raise failure
        # Registered while the run's folder still exists: it is removed only
        # after every output is in the project.
        return details | {"outputs": _retain_outputs(binding, run, _read_outputs(outputs_dir))}


# The program that holds a skill to its grant. It runs in the skill's Python,
# installs an audit hook, then runs the entry as __main__.
BOOTSTRAP_SOURCE = r'''import json
import os
import runpy
import shlex
import sys

_settings = json.load(open(os.environ["MONKEYHUB_SKILL_ACCESS"], encoding="utf-8"))
_access = _settings["access"]
_roots = [os.path.normcase(os.path.realpath(root)) for root in _settings["roots"]]
_log = open(os.environ["MONKEYHUB_SKILL_AUDIT"], "a", encoding="utf-8")
_programs = {name.lower().removesuffix(".exe") for name in _access.get("programs") or []}
_ssh = set(_access.get("ssh") or [])
_writes = [os.path.normcase(os.path.realpath(path)) for path in _access.get("write") or [] if path != "*"]
_SSH = {"ssh", "scp", "sftp", "rsync"}
_inside = False


def _emit(event, **data):
    _log.write(json.dumps({"event": event, **data}, ensure_ascii=False, default=str) + "\n")
    _log.flush()


def _deny(category, detail):
    _emit("denied", category=category, detail=detail)
    raise PermissionError(f"MonkeyHub: this skill version was not granted {category}: {detail}")


def _under(path, roots):
    path = os.path.normcase(path)
    return any(path == root or path.startswith(root.rstrip("\\/") + os.sep) for root in roots)


def _argv(value):
    # On Windows the event carries one command line; elsewhere a list.
    if isinstance(value, (list, tuple)):
        return [os.fsdecode(part) if isinstance(part, (bytes, os.PathLike)) else str(part) for part in value]
    if isinstance(value, (bytes, os.PathLike)):
        value = os.fsdecode(value)
    try:
        return [part.strip('"') for part in shlex.split(str(value), posix=False)]
    except ValueError:
        return str(value).split()


def _program(executable, argv):
    argv = _argv(argv)
    name = os.path.basename(str(executable or (argv[0] if argv else ""))).lower().removesuffix(".exe")
    arguments = [str(part) for part in (argv or [])][:60]
    _emit("process", program=name, executable=str(executable), args=arguments)
    if name in _SSH:
        hosts = [part for part in arguments[1:] if not part.startswith("-")]
        host = hosts[0].split("@")[-1].split(":")[0] if hosts else ""
        if "*" not in _ssh and host not in _ssh:
            _deny("ssh", host or " ".join(arguments))
    elif "*" not in _programs and name not in _programs:
        _deny("programs", name)


def _write(path):
    try:
        real = os.path.realpath(os.fsdecode(os.fspath(path)))
    except (TypeError, ValueError):
        return
    if _under(real, _roots):
        return
    _emit("write", path=real)
    if "*" not in (_access.get("write") or []) and not _under(real, _writes):
        _deny("write", real)


def _hook(event, args):
    global _inside
    if _inside:
        return
    _inside = True
    try:
        if event == "subprocess.Popen":
            _program(args[0], args[1])
        elif event == "os.system":
            _emit("shell", command=str(args[0]))
            if "*" not in _programs and "shell" not in _programs:
                _deny("programs", "shell: " + str(args[0])[:200])
        elif event in ("os.exec", "os.spawn", "os.posix_spawn"):
            _program(args[0], args[1] if len(args) > 1 else [])
        elif event == "os.startfile":
            _program(args[0], [args[0]])
        elif event in ("ctypes.dlsym", "ctypes.dlsym/handle"):
            # Windows Credential Manager holds the keys the Hub saved; no grant reaches them.
            name = args[1] if len(args) > 1 else ""
            name = name.decode("ascii", "replace") if isinstance(name, bytes) else str(name)
            if name.startswith(("CredRead", "CredEnumerate", "CredBackup", "CredUnprotect", "CredFind")):
                _deny("credentials", name)
        elif event == "socket.connect":
            address = args[1]
            host = address[0] if isinstance(address, tuple) and address else str(address)
            if host not in ("127.0.0.1", "::1", "localhost"):
                _emit("network", host=str(host), port=address[1] if isinstance(address, tuple) and len(address) > 1 else None)
                if not _access.get("network"):
                    _deny("network", str(host))
        elif event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND)))
            if writes and isinstance(path, (str, bytes, os.PathLike)):
                _write(path)
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.replace", "shutil.rmtree",
                       "shutil.copyfile", "shutil.move"):
            moves = event in ("os.rename", "os.replace", "shutil.copyfile", "shutil.move") and len(args) > 1
            target = args[1] if moves else args[0]
            if isinstance(target, (str, bytes, os.PathLike)):
                _write(target)
    finally:
        _inside = False


entry, *rest = sys.argv[1:]
sys.argv = [entry, *rest]
for _path in reversed([part for part in os.environ.get("MONKEYHUB_SKILL_PATHS", "").split(os.pathsep) if part]):
    sys.path.insert(0, _path)
sys.path.insert(0, os.path.dirname(os.path.abspath(entry)))
sys.addaudithook(_hook)
runpy.run_path(entry, run_name="__main__")
'''
