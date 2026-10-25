"""Run one installed skill on project inputs and retain what it did.

A runnable skill carries script files, names an ``entry`` and declares the
``access`` it needs. A run names the skill; the skill folders say which package
that is (``skill_library``), and the project retains that version's exact
content before it runs. What it declares must be allowed first: a person allows
kinds of access once, for every skill or for one, in the Hub
(``skill_access``, read from the grants file the launch environment names), or
for this one run. The run goes on in the person's own environment: the
runtime's Python without isolation, the user's environment variables, any
program, the network and the disk, as far as the declaration reaches. An audit
hook installed before the entry runs holds the script to what it declared:
starting a program, connecting out, ssh and writing outside the run's own
folders are checked against it, refused when not declared, and every one is
recorded.

Runs are background jobs. Each gets its own P036 run ``skill-run-<id>`` whose
StudioSkillRun@1 records move from queued to running to succeeded or failed.
The record names the skill version, the folder it came from, its code hash,
the grants it went ahead on (each as it was when the run started: what it
allowed, for which skills, how and when it was given), every input and output digest, the programs it started,
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
from contextlib import suppress
from dataclasses import asdict, dataclass
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
from ..errors import SkillAccessNeeded, StudioError
from . import artifacts, skill_access, skill_library, skills

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

# What whoever reads a failure does next, by its code: change the request ("fix_input"), tell the person what only they
# can give or allow ("ask_person"), or stop and say what happened ("stop"). A refusal says it after its own words; a
# failed run's record carries it as failureNext and failureNextWords.
NEXT_STEPS = {
    "SKILL_INPUT_MISSING": ("fix_input", "Give the input named above and run it again; do not run it again as it was."),
    "SKILL_RUN_INVALID": ("fix_input", "Change the request as the message says and run it again."),
    "SKILL_INPUT_TOO_LARGE": ("fix_input", "Give a smaller input and run it again."),
    "SKILL_OUTPUT_INVALID": ("fix_input", "Strip path separators and '..' from any name taken from the parameters and run it again."),
    "SKILL_RUN_FAILED": ("fix_input", "Change the input or parameter the message points to and run it again; the same request is not run a third time."),
    "SKILL_CHANGED": ("fix_input", "Read the skill again, then run it."),
    "SKILL_PERMISSION_NEEDED": ("ask_person", "Tell the person what it tried; a person allows it in the Hub, or the skill's access is corrected. Do not run it again."),
    "SKILL_ACCESS_NEEDED": ("ask_person", "A person allows what it needs; ask once for all of it. Do not run it again until they have."),
    "SKILL_PACKAGES_FAILED": ("ask_person", "Tell the person which package could not be installed; do not run it again."),
    "SKILL_PACKAGES_UNAVAILABLE": ("ask_person", "Tell the person a Python with pip is needed; do not run it again."),
    "SKILL_PACKAGES_TIMEOUT": ("ask_person", "Tell the person the packages did not install in time; do not run it again."),
    "SKILL_NOT_INSTALLED": ("ask_person", "Tell the person the skill folder is missing; the skill is installed by putting its folder in the skill folder."),
    "SKILL_PACKAGE_INVALID": ("ask_person", "Tell the person what is wrong with the skill's folder; it runs again once they fix it."),
    "SKILL_INVALID": ("ask_person", "Tell the person what is wrong with the skill's folder; it runs again once they fix it."),
    "SKILL_NOT_RUNNABLE": ("stop", "This skill has no script to run; follow its instructions by hand instead."),
    "SKILL_CREDENTIALS_REFUSED": ("stop", "No grant allows this. Do not run it again; a skill that needs a provider asks the Hub to call it."),
    "SKILL_OUTPUT_MISSING": ("stop", "The skill produced nothing. Do not run it again as it was; tell the person which skill and what it printed."),
    "SKILL_RUN_TIMEOUT": ("stop", "Do not run it again as it was; tell the person it took too long."),
    "SKILL_REPEATED_FAILURE": ("stop", "Change the input or parameters, or tell the person; do not run this exact request again."),
}
DEFAULT_NEXT = ("stop", "Do not run it again as it was; tell the person what failed.")
# The same request that failed twice in a row is not run a third time by an Agent within this long.
REPEAT_WINDOW_S = 30 * 60.0
REPEATED_FAILURES = 2


def next_step(code: str) -> tuple[str, str]:
    """What to do after a failure with this code: (fix_input | ask_person | stop, in words)."""

    return NEXT_STEPS.get(code, DEFAULT_NEXT)


def _with_next(refusal: StudioError) -> StudioError:
    """A refusal that says, after its own words, what to do next."""

    step, words = next_step(refusal.code)
    if "Next:" not in refusal.detail:
        refusal.detail = f"{refusal.detail} Next ({step}): {words}"
    return refusal


def _failure_fields(code: str) -> dict[str, str]:
    step, words = next_step(code)
    return {"failureNext": step, "failureNextWords": words}


# What a person reads when a run was stopped for doing what it had not said it would (S23), one sentence each.
DENIED_WORDS = {
    "outputs": "已拦下：这个脚本想把文件写到自己的输出以外（{detail}），没让它写。",
    "write": "已拦下：这个脚本想写项目以外的地方（{detail}），它没说要写那里，所以没让它写。",
    "read": "已拦下：这个脚本想读项目以外的文件（{detail}），它没说要读那里，所以没让它读。",
    "network": "已拦下：这个脚本想联网（{detail}），它没说要联网，所以没让它连。",
    "programs": "已拦下：这个脚本想运行别的程序（{detail}），它没说要运行，所以没让它运行。",
    "ssh": "已拦下：这个脚本想用 ssh 连接 {detail}，它没说要连，所以没让它连。",
    "loopback": "已拦下：这个脚本想连本机的 Hub 接口（{detail}），低风险档不允许；要用请换成需要批准的方式。",
    "credentials": "已拦下：这个脚本想读 Hub 保存的密钥，这一项谁都不能允许。",
}
FAILURE_WORDS = {
    "SKILL_RUN_TIMEOUT": "这个脚本跑得太久，停下了，没有结果。",
    "SKILL_OUTPUT_MISSING": "这个脚本跑完了，但一个文件也没写出来。",
    "SKILL_INPUT_MISSING": "这个脚本缺一个输入，没跑成。",
    "SKILL_PACKAGES_FAILED": "这个脚本要的 Python 包装不上，没跑。",
    "SKILL_OUTPUT_TOO_LARGE": "这个脚本写出的文件太大，没收下。",
}


def failure_words(code: str, denied) -> str:
    """One sentence in Chinese on why a run failed: what was stopped, or what went wrong."""

    for item in denied:
        words = DENIED_WORDS.get(str(item.get("category")))
        if words is not None:
            return words.format(detail=str(item.get("detail") or "")[:200])
    return FAILURE_WORDS.get(code, "这个脚本出错停下了，没有结果。")


def declared_inputs(manifest: Mapping[str, Any] | None) -> dict[str, str]:
    """The inputs a skill's manifest names, by input name, each with its description.

    A manifest line reads "name: what it is"; a name may be followed by a note in brackets before the colon.
    ``params`` is not an input.
    """

    found: dict[str, str] = {}
    for item in (manifest or {}).get("inputs") or ():
        named = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)[^:：]*[:：](.*)", str(item), re.S)
        if named and named.group(1) != "params":
            found.setdefault(named.group(1), named.group(2).strip())
    return found


def declared_outputs(manifest: Mapping[str, Any] | None) -> list[str]:
    """The file names a skill's manifest says every run writes: a line "name.ext: what it is" with no placeholder in the name."""

    names = []
    for item in (manifest or {}).get("outputs") or ():
        head = re.split(r"[:：]", str(item), maxsplit=1)[0].strip()
        if OUTPUT_PART.fullmatch(head) and "." in head:
            names.append(head)
    return list(dict.fromkeys(names))


def verify_outputs(manifest: Mapping[str, Any] | None, written: list[str]) -> dict[str, Any]:
    """Whether the files a run wrote are the ones its manifest says it writes: passed, failed or inconclusive.

    Inconclusive when the manifest names no file without a placeholder. The run's own status does not change: its
    outputs are kept, and the record says which declared file is not among them.
    """

    declared = declared_outputs(manifest)
    if not declared:
        return {"status": "inconclusive", "declared": [], "missing": []}
    have = set(written) | {name.rsplit("/", 1)[-1] for name in written}
    missing = [name for name in declared if name not in have]
    return {"status": "failed" if missing else "passed", "declared": declared, "missing": missing}


def missing_required(manifest: Mapping[str, Any] | None, given: Mapping[str, Any]) -> list[str]:
    """The required inputs (``requiredInputs``) a request did not give: each entry, written as it is (``model|mesh``)."""

    return [group for group in (manifest or {}).get(skills.REQUIRED_INPUTS) or ()
            if not any(name in given for name in str(group).split("|"))]


def required_words(skill: skill_library.InstalledSkill, lacking: list[str], given: Mapping[str, Any]) -> str:
    """What a refused request lacks, in a sentence: each missing input with what its manifest says it is."""

    described = declared_inputs(skill.content.get("manifest") if skill.content else None)
    parts = []
    for group in lacking:
        names = [f"{name} ({described[name]})" if described.get(name) else name for name in group.split("|")]
        parts.append(" or ".join(names))
    return (f"{skill.name} cannot run yet: it needs {'; and '.join(parts)}. "
            f"This request gave {', '.join(sorted(given)) or 'no inputs'}.")


def _missing_input(manifest: Mapping[str, Any] | None, given: Mapping[str, Any], stderr: str) -> tuple[str, str] | None:
    """The input a failed run needed and was not given, when its error names it (KeyError: 'image'), else None."""

    for name, description in declared_inputs(manifest).items():
        if name not in given and re.search(rf"KeyError: ['\"]{re.escape(name)}['\"]", stderr):
            return name, description
    return None


def request_digest(skill: skill_library.InstalledSkill, inputs: Mapping[str, Mapping[str, Any]],
                   parameters: Mapping[str, Any]) -> str:
    """One request's identity: the skill's code, its inputs and its parameters; a local file counts by its size and time."""

    given = {}
    for name, ref in sorted(inputs.items()):
        ref = dict(ref)
        if ref.get("kind") == "file":
            try:
                status = os.stat(str(ref.get("path") or ""))
                ref |= {"size": status.st_size, "modified": status.st_mtime_ns}
            except OSError:
                pass
        given[name] = ref
    return hashlib.sha256(json.dumps({"skillId": skill.skill_id, "code": skill.code_sha256, "inputs": given,
                                      "parameters": dict(parameters)}, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def _refuse_repeat(binding: ProjectBinding, skill_id: str, digest: str, now: datetime) -> None:
    """Refuse an Agent's run of the exact request that has just failed twice in a row."""

    same = [row for row in list_skill_runs(binding, skill_id) if row.get("requestDigest") == digest]
    last = same[-REPEATED_FAILURES:]
    if len(last) < REPEATED_FAILURES or any(row.get("status") != "failed" for row in last):
        return
    try:
        newest = datetime.fromisoformat(str(last[-1].get("updatedAt")))
    except ValueError:
        return
    if (now - newest).total_seconds() > REPEAT_WINDOW_S:
        return
    codes = ", ".join(dict.fromkeys(str(row.get("failureCode")) for row in last))
    raise StudioError(409, "SKILL_REPEATED_FAILURE",
                      f"{skill_id} has just failed twice with exactly this request ({codes}); it is not run a third time.")


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
    """One-run grants a run of this project has taken: each allows exactly one run, whatever became of it."""

    used = set()
    for row in list_skill_runs(binding):
        grant = row.get("grant") or {}
        if grant.get("onceGrantId"):
            used.add(str(grant["onceGrantId"]))
        elif grant.get("scope") == "once" and grant.get("approvalId"):
            # A run from before S-1, on a project's own once approval.
            used.add(str(grant["approvalId"]))
    return used


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
            probe = runner([*command, "-m", "pip", "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
            done = runner(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=INSTALL_TIMEOUT_S,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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


def _grants_file() -> Path | None:
    value = os.environ.get(skill_access.GRANTS_ENV)
    return Path(value) if value else None


def _snapshot(grant: skill_access.Grant) -> dict:
    """A grant as the run record keeps it: the grants file is this machine's, the record goes with the project."""

    return {"grantId": grant.grant_id, "access": dict(grant.access), "method": grant.method, "grantedAt": grant.granted_at,
            "skill": None if grant.skill is None else {"name": grant.skill.name, "source": grant.skill.source},
            "once": grant.once is not None}


def allowed(binding: ProjectBinding, skill: skill_library.InstalledSkill, *, grant_id: str | None,
            now: datetime | None = None) -> dict:
    """What a run of ``skill`` goes ahead on, or SkillAccessNeeded naming what a person still has to allow.

    The grants are the Hub's, at the path the launch environment names; a
    Runtime started without one has none and runs only skills that need
    nothing. ``grant_id`` names a one-run grant a person gave for exactly this
    project, skill and code. The record keeps each grant that allows part of
    what the skill declares, as it was then.
    """

    ref = skill_access.SkillRef(skill.name, skill.source)
    try:
        grants = skill_access.read_grants(_grants_file())
    except skill_access.GrantsUnreadable as failure:
        raise StudioError(409, "SKILL_GRANTS_UNREADABLE", f"{failure} Nothing is taken as allowed.") from failure
    declared, resolve = skill.access, os.path.realpath
    asked = skill_access.access_added(declared, None, resolve=resolve)
    used = [grant for grant in grants if grant.active and grant.once is None and grant.applies_to(ref)
            and skill_access.access_added(declared, skill_access.lasting([grant], ref), resolve=resolve) != asked]
    record = {"source": skill.source, "codeSha256": skill.code_sha256, "access": declared,
              "tier": skill_access.tier(declared), "grants": [_snapshot(grant) for grant in used], "onceGrantId": None}
    needs = skill_access.needed(declared, grants, ref, resolve=resolve)
    if not needs:
        return record
    if grant_id is not None:
        once = skill_access.once_grant(grants, grant_id, ref, project_id=binding.project_id,
                                       code_sha256=str(skill.code_sha256), needs=needs,
                                       now=now or datetime.now(timezone.utc), used=used_once_grants(binding),
                                       resolve=resolve)
        if once is not None:
            return record | {"grants": [*record["grants"], _snapshot(once)], "onceGrantId": once.grant_id}
    raise SkillAccessNeeded(
        f"{skill.name} needs a person to allow: {skill_access.describe_added(needs)}. Nothing ran.",
        needs=needs, broad=skill_access.broad(needs), skill={"name": skill.name, "source": skill.source},
        project_id=binding.project_id, code_sha256=str(skill.code_sha256))


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


def _installed(skill_id: str) -> skill_library.InstalledSkill:
    """The runnable package a skill id means, or the refusal that says why there is none."""

    name = skill_id.removeprefix(skills.SKILL_ID_PREFIX)
    skill = skill_library.installed().find(name) if skill_id.startswith(skills.SKILL_ID_PREFIX) else None
    if skill is None:
        raise StudioError(404, "SKILL_NOT_INSTALLED",
                          f"No skill folder holds {skill_id}. A skill is installed by putting its folder in the skill folder.")
    if skill.content is None:
        error = skill.error or {}
        raise StudioError(422, error.get("code", "SKILL_PACKAGE_INVALID"), error.get("detail", f"{name} cannot be read."))
    if skill.content.get("entry") is None:
        raise StudioError(422, "SKILL_NOT_RUNNABLE", f"{skill_id} carries no entry script.")
    return skill


def run_needs(binding: ProjectBinding, skill_id: str) -> dict:
    """What a run of the installed skill would need a person to allow now, without running or retaining anything.

    The Hub asks this before an Agent's run, to put what is missing to the
    person as one question; ``needs`` is empty when it may go ahead.
    """

    return _needs_report(binding, _installed(skill_id))


def _needs_report(binding: ProjectBinding, skill) -> dict:
    """What a run would need allowed now, its tier, and which of it only a grant for this one run allows (S23)."""

    try:
        allowed(binding, skill, grant_id=None)
        needs: Mapping[str, Any] = {}
    except SkillAccessNeeded as refusal:
        needs = refusal.needs
    return {"projectId": binding.project_id, "skillId": skill.skill_id,
            "skill": {"name": skill.name, "source": skill.source}, "codeSha256": skill.code_sha256,
            "access": skill.access, "tier": skill_access.tier(skill.access), "needs": dict(needs),
            "broad": skill_access.broad(needs) if needs else {}, "eachTime": skill_access.each_time(needs)}


def submit_skill_run(binding: ProjectBinding, jobs, skill_id: str, *, version: int | None,
                     inputs: Mapping[str, Mapping[str, Any]], parameters: Mapping[str, Any],
                     attribution: ActorAttribution, agent: bool = False, grant_id: str | None = None,
                     python: str | None = None, runner: Runner | None = None,
                     timeout_s: float = RUN_TIMEOUT_S, now: datetime | None = None) -> dict:
    """Find the installed skill, check what it needs is allowed, retain its version, and run it as a background job.

    ``version``, when given, is the project's version the caller read: a
    package that says something else now refuses with SKILL_CHANGED rather
    than running code the caller did not read.
    """

    try:
        skill = _installed(skill_id)
        current = skills.latest_version(binding, skill.skill_id)
        if version is not None and (current is None or current.version != version
                                    or not skills.same_content(current, skill.content)):
            raise StudioError(409, "SKILL_CHANGED",
                              f"{skill_id} in the {skill.source} skill folder is not version {version} of this project any more. "
                              "Read it again before running it.")
    except StudioError as refusal:
        raise _with_next(refusal)
    return _submit(binding, jobs, skill, lambda: skill_library.retain(binding, skill, attribution=attribution),
                   inputs=inputs, parameters=parameters, attribution=attribution, agent=agent, grant_id=grant_id,
                   python=python, runner=runner, timeout_s=timeout_s, now=now)


# ---- a script an Agent wrote in a chat (S23) -------------------------------------

SCRATCH_PREFIX = "scratch:"
SCRATCH_SOURCE = "scratch"
# A script written in a chat is small: its code is kept in its run's record.
MAX_SCRATCH_BYTES = 256 * 1024
SCRATCH_ENTRY = "main.py"
# A script written in a chat answers a question while the person waits; one that runs longer is stopped (S23: the
# first script a chat wrote searched whole folders and ran on until the Hub stopped).
SCRATCH_TIMEOUT_S = 15 * 60.0


@dataclass(frozen=True, slots=True)
class ScratchSkill:
    """A script an Agent wrote in a chat and runs as a skill nobody has kept yet (S23).

    It is named by its code (``scratch:<first 12 hex digits of codeSha256>``), comes from no folder, and is
    retained only in the record of each run of it. ``suggested`` is the name it would be kept under.
    """

    content: Mapping[str, Any]
    suggested: str | None

    @property
    def name(self) -> str:
        return "scratch-" + self.code_sha256[:12]

    @property
    def source(self) -> str:
        return SCRATCH_SOURCE

    @property
    def skill_id(self) -> str:
        return SCRATCH_PREFIX + self.code_sha256[:12]

    @property
    def code_sha256(self) -> str:
        return str(self.content["codeSha256"])

    @property
    def access(self) -> Mapping[str, Any] | None:
        return self.content.get("access")


def scratch_skill(*, description: str, code: str | None = None, files: Mapping[str, str] | None = None,
                  entry: str | None = None, access: Mapping[str, Any] | None = None,
                  manifest: Mapping[str, Any] | None = None, name: str | None = None) -> ScratchSkill:
    """A chat's script in the checked form a skill version has, or the refusal that says what is wrong.

    ``code`` is one Python file (``main.py``); ``files`` with ``entry`` is several. ``access`` says what it needs
    beyond reading the project and writing its outputs; none means it needs nothing.
    """

    try:
        if (code is None) == (files is None):
            raise _invalid("A script gives its code, or its files and entry: one of the two.")
        if code is not None:
            files, entry = {SCRATCH_ENTRY: code}, entry or SCRATCH_ENTRY
        if sum(len(str(text).encode("utf-8")) for text in (files or {}).values()) > MAX_SCRATCH_BYTES:
            raise StudioError(413, "SKILL_TOO_LARGE",
                              f"A script run from a chat is at most {MAX_SCRATCH_BYTES // 1024} KiB of code; keep a larger "
                              "one as a skill package.")
        if name is not None and not (isinstance(name, str) and skills.SKILL_NAME.fullmatch(name)
                                     and len(name) <= skills.MAX_NAME_LENGTH):
            raise _invalid("name is the skill name it would be kept under: lowercase words joined by hyphens.")
        description = skills._single_line(description, "description", 300)
        _, content = skills.skill_content(
            name=name or "scratch", description=description,
            body=f"A script written in a chat: {description}\n", manifest=manifest, files=files, entry=entry,
            access=access)
        if content.get("entry") is None:
            raise _invalid("A script names its entry, one of its .py files.")
        problem = skill_library.package_problem(content)
        if problem is not None and problem["kind"] == "entry_syntax":
            raise _invalid(problem["detail"])
    except StudioError as refusal:
        raise _with_next(refusal)
    return ScratchSkill(content, name)


def scratch_needs(binding: ProjectBinding, skill: ScratchSkill) -> dict:
    """What a run of this script would need a person to allow now, and its tier; nothing runs."""

    return _needs_report(binding, skill)


def submit_scratch_run(binding: ProjectBinding, jobs, skill: ScratchSkill, *,
                       inputs: Mapping[str, Mapping[str, Any]], parameters: Mapping[str, Any],
                       attribution: ActorAttribution, agent: bool = False, grant_id: str | None = None,
                       python: str | None = None, runner: Runner | None = None,
                       timeout_s: float = SCRATCH_TIMEOUT_S, now: datetime | None = None) -> dict:
    """Run a chat's script as a background job, its code, access and tier kept in the run's own record."""

    content = skill.content
    kept = {"name": skill.suggested, "description": content["description"], "entry": content["entry"],
            "files": dict(content["files"]), "access": content.get("access"), "manifest": content.get("manifest")}

    def version() -> skills.SkillVersion:
        return skills.SkillVersion(None, {"skillId": skill.skill_id, "version": 0, **content})

    return _submit(binding, jobs, skill, version, inputs=inputs, parameters=parameters, attribution=attribution,
                   agent=agent, grant_id=grant_id, python=python, runner=runner, timeout_s=timeout_s, now=now,
                   extra={"scratch": kept})


def _submit(binding: ProjectBinding, jobs, skill, version: Callable[[], skills.SkillVersion], *,
            inputs: Mapping[str, Mapping[str, Any]], parameters: Mapping[str, Any], attribution: ActorAttribution,
            agent: bool, grant_id: str | None, python: str | None, runner: Runner | None, timeout_s: float,
            now: datetime | None, extra: Mapping[str, Any] | None = None) -> dict:
    run_command = subprocess.run if runner is None else runner
    try:
        # Before anyone is asked to allow anything: a run that cannot happen asks for nothing.
        lacking = missing_required(skill.content.get("manifest"), inputs)
        if lacking:
            raise StudioError(422, "SKILL_INPUT_MISSING", required_words(skill, lacking, inputs))
        grant = allowed(binding, skill, grant_id=grant_id, now=now)
        parameters_text = json.dumps(dict(parameters), ensure_ascii=False, sort_keys=True)
        if len(parameters_text.encode("utf-8")) > MAX_PARAMETERS_BYTES:
            raise _invalid(f"Parameters are at most {MAX_PARAMETERS_BYTES // 1024} KiB of JSON.")
        digest = request_digest(skill, inputs, parameters)
        if agent:
            _refuse_repeat(binding, skill.skill_id, digest, now or datetime.now(timezone.utc))
    except StudioError as refusal:
        raise _with_next(refusal)
    row = version()
    run_id = RUN_PREFIX + uuid.uuid4().hex[:12]
    try:
        run = binding.repository.create_run(run_id)
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(409, "SKILL_RUN_WRITE_FAILED", "The skill run could not be created in this project.") from exc
    queued = {
        "schema": RUN_SCHEMA, "projectId": binding.project_id, "runId": run_id, "status": "queued",
        "skillId": row.skill_id, "version": row.version, "skillVersionRef": row.ref, "codeSha256": row.code_sha256,
        "entry": row.entry, "access": row.access, "tier": grant["tier"], "grant": grant,
        "parameters": json.loads(parameters_text), "requestDigest": digest, **(extra or {}),
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
                                        "failureReason": str(exc)[:500], **_failure_fields("SKILL_RUN_NOT_STARTED")})
        raise
    return {"runId": run_id, "status": "queued", "statusPath": queued["statusPath"], "skillId": row.skill_id,
            "codeSha256": row.code_sha256, "tier": queued["tier"], "grant": queued["grant"]}


@retained_sources
def _retain(binding: ProjectBinding, run, payload: dict) -> None:
    _save(binding, run, payload)


def _named(row: skills.SkillVersion) -> str:
    """How a failure names what ran: a skill and its version, or a chat's script by its code."""

    return f"the script {row.skill_id}" if row.skill_id.startswith(SCRATCH_PREFIX) else f"{row.skill_id} version {row.version}"


def _execute(binding: ProjectBinding, run, row: skills.SkillVersion, queued: dict,
             inputs: Mapping[str, Mapping[str, Any]], *, python: str | None, runner: Runner, timeout_s: float) -> None:
    current = queued | {"status": "running", "startedAt": _now(), "updatedAt": _now()}
    _retain(binding, run, current)
    try:
        result = _run_once(binding, run, row, current, inputs, python=python, runner=runner, timeout_s=timeout_s)
    except StudioError as exc:
        failed = current | {"status": "failed", "updatedAt": _now(), "failureCode": exc.code, "failureReason": exc.detail,
                            **_failure_fields(exc.code)}
        failed |= getattr(exc, "run_details", {})
        failed["failureWords"] = failure_words(exc.code, failed.get("needed") or ())
        _retain(binding, run, failed)
        raise
    except Exception as exc:
        _retain(binding, run, current | {"status": "failed", "updatedAt": _now(), "failureCode": "SKILL_RUN_FAILED",
                                         "failureReason": f"{type(exc).__name__}: {exc}"[:1500],
                                         "failureWords": failure_words("SKILL_RUN_FAILED", ()),
                                         **_failure_fields("SKILL_RUN_FAILED")})
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
        # Matplotlib keeps its font cache where every run finds it again, not in the user's home, which a run may not write.
        plots = _environment_root() / "matplotlib"
        with suppress(OSError):
            plots.mkdir(parents=True, exist_ok=True)
        access_path.write_text(json.dumps({
            "access": row.access or {}, "roots": [str(root), tempfile.gettempdir(), str(plots),
                                                  *([environment["path"]] if environment else [])],
            # What a run reads besides its own folders and its Python (S23): the project, and what its access names.
            "reads": [str(binding.project_dir)],
            "run": str(root), "outputs": str(outputs_dir), "skill": str(skill_dir),
        }, ensure_ascii=False), encoding="utf-8")
        bootstrap.write_text(BOOTSTRAP_SOURCE, encoding="utf-8")
        env = skill_environment(os.environ, ssh=bool((row.access or {}).get("ssh")))
        env |= {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "MONKEYHUB_SKILL_RUN": run.run_id, "MONKEYHUB_PROJECT_ID": binding.project_id,
                "MONKEYHUB_SKILL_ACCESS": str(access_path), "MONKEYHUB_SKILL_AUDIT": str(audit_path),
                "MPLCONFIGDIR": str(plots), "MPLBACKEND": "Agg"}
        if environment:
            # The runtime's embedded Python ignores PYTHONPATH; the bootstrap puts these on sys.path itself.
            env["MONKEYHUB_SKILL_PATHS"] = environment["path"]
            env["PYTHONPATH"] = os.pathsep.join(filter(None, (environment["path"], os.environ.get("PYTHONPATH"))))
        command = [python or sys.executable, "-B", "-X", "utf8", str(bootstrap),
                   str(skill_dir.joinpath(*row.entry.split("/"))),
                   "--inputs", str(inputs_dir), "--params", str(params_path), "--outputs", str(outputs_dir)]
        clock = time.monotonic()
        try:
            # Not the Studio's own stdin (gap 29): the Hub holds that pipe with a
            # read pending, and on Windows a child that inherits it never gets
            # past its own Python start.
            finished = runner(command, cwd=str(skill_dir), stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout_s, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise StudioError(504, "SKILL_RUN_TIMEOUT",
                              f"{_named(row)} did not finish within {int(timeout_s)} s.") from exc
        duration = round(time.monotonic() - clock, 3)
        observed, denied = _observed(audit_path)
        details = {"inputs": input_rows, "environment": environment, "observed": observed, "needed": denied,
                   "exitCode": finished.returncode, "durationSeconds": duration,
                   "stdoutTail": _tail(finished.stdout), "stderrTail": _tail(finished.stderr)}
        strayed = [item for item in denied if item.get("category") == "outputs"]
        if strayed:
            # However it exited: a write it was refused may have been an output.
            failure = StudioError(422, "SKILL_OUTPUT_INVALID",
                                  f"{_named(row)} wrote outside its outputs folder: "
                                  + "; ".join(str(item.get("detail")) for item in strayed[:3])
                                  + ". A skill writes its results under --outputs by plain file names; a name "
                                  "taken from the parameters needs its path separators and '..' removed first.")
            failure.run_details = details
            raise failure
        if finished.returncode != 0:
            if any(item.get("category") == "credentials" for item in denied):
                failure = StudioError(403, "SKILL_CREDENTIALS_REFUSED",
                                      f"{_named(row)} tried to read the credentials the Hub "
                                      "keeps, which no grant allows. A skill that needs a provider asks the Hub to "
                                      "call it.")
            elif denied:
                failure = StudioError(403, "SKILL_PERMISSION_NEEDED",
                                      f"{_named(row)} tried what it was not granted: "
                                      + "; ".join(f"{item.get('category')} {item.get('detail')}" for item in denied[:5])
                                      + ". Declare it in the skill's access, and a person allows it in the Hub.")
            elif (lacking := _missing_input(row.payload.get("manifest"), inputs, _tail(finished.stderr))) is not None:
                failure = StudioError(422, "SKILL_INPUT_MISSING",
                                      f"{_named(row)} needs the input '{lacking[0]}' ({lacking[1][:160]}), "
                                      f"and this request gave {', '.join(sorted(inputs)) or 'no inputs'}.")
            else:
                failure = StudioError(422, "SKILL_RUN_FAILED",
                                      f"{_named(row)} exited with {finished.returncode}: "
                                      + (_tail(finished.stderr).strip()[-1500:] or "no message"))
            failure.run_details = details
            raise failure
        # Registered while the run's folder still exists: it is removed only
        # after every output is in the project.
        written = _read_outputs(outputs_dir)
        if not written:
            failure = StudioError(422, "SKILL_OUTPUT_MISSING",
                                  f"{_named(row)} finished without writing a single file to its outputs. "
                                  "Its last words: " + (_tail(finished.stdout).strip()[-400:] or "none") + ".")
            failure.run_details = details
            raise failure
        return details | {"outputs": _retain_outputs(binding, run, written),
                          "verification": verify_outputs(row.payload.get("manifest"), [name for name, _ in written])}


# The program that holds a skill to its grant. It runs in the skill's Python,
# installs an audit hook, then runs the entry as __main__.
BOOTSTRAP_SOURCE = r'''import json
import os
import runpy
import shlex
import sys
import weakref

_settings = json.load(open(os.environ["MONKEYHUB_SKILL_ACCESS"], encoding="utf-8"))
_access = _settings["access"]
_roots = [os.path.normcase(os.path.realpath(root)) for root in _settings["roots"]]
_log = open(os.environ["MONKEYHUB_SKILL_AUDIT"], "a", encoding="utf-8")
_programs = {name.lower().removesuffix(".exe") for name in _access.get("programs") or []}
_ssh = set(_access.get("ssh") or [])
_writes = [os.path.normcase(os.path.realpath(path)) for path in _access.get("write") or [] if path != "*"]
# The run's own folder holds its control files, the inputs and this audit log.
# Inside it a skill writes only its outputs, or beside its own code; and no
# write climbs out of the outputs folder with "..". No grant changes this.
_run = os.path.normcase(os.path.realpath(_settings["run"])) if _settings.get("run") else None
_outputs = [os.path.normcase(os.path.realpath(_settings["outputs"]))] if _settings.get("outputs") else []
_kept = _outputs + ([os.path.normcase(os.path.realpath(_settings["skill"]))] if _settings.get("skill") else [])
_SSH = {"ssh", "scp", "sftp", "rsync"}
_inside = False
# Without the network a run reaches no other host and nothing on this machine either: the Hub, the Project Runtime
# and the Studio listen on loopback, and a script that called them would act as the person (S23). A socket the run
# bound on loopback itself is the one thing it may connect to there: socket.socketpair and an asyncio loop do that.
_LOOPBACK = ("localhost", "::1", "0:0:0:0:0:0:0:1")
_bound = weakref.WeakSet()
# Native libraries that reach the network past the socket module, and calls that open a pipe or another process.
_NETWORK_LIBRARIES = ("ws2_32", "wsock32", "mswsock", "winhttp", "wininet", "urlmon", "iphlpapi", "dnsapi", "rpcrt4",
                      "libcurl", "libssl", "libcrypto")
_NATIVE_PAST_CHECKS = ("LoadLibrary", "GetProcAddress", "LdrLoadDll", "CreateFile", "NtCreateFile", "CallNamedPipe",
                       "WaitNamedPipe", "OpenProcess", "CreateProcess", "ShellExecute", "WinExec", "socket", "connect",
                       "WSA", "dlopen", "dlsym")


def _loopback(host):
    host = str(host).strip("[]").lower()
    return host in _LOOPBACK or host.startswith("127.") or host.startswith("::ffff:127.")


def _own_port(port):
    for sock in list(_bound):
        try:
            if sock.getsockname()[1] == port:
                return True
        except (OSError, IndexError, TypeError):
            continue
    return False


def _address(value):
    if isinstance(value, tuple) and value:
        return value[0], (value[1] if len(value) > 1 else None)
    return value, None


def _system_reads():
    # What any Python program reads to start and to import: its own installation and import path, the operating
    # system's own folder (fonts, time zones, certificates). Fixed before the entry runs, so it cannot add to them.
    places = [sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix, *[path for path in sys.path if path]]
    if os.name == "nt":
        places += [os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows"]
        if os.environ.get("LOCALAPPDATA"):
            places.append(os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "Windows", "Fonts"))
    else:
        places += ["/usr", "/lib", "/lib64", "/etc", "/opt", "/proc", "/sys", "/dev", "/System", "/Library"]
    found = [os.path.normcase(os.path.realpath(path)) for path in places if path]
    # An import path of a whole drive or the home folder (a PYTHONPATH of C:\) is not a reason to read everything.
    home = os.path.normcase(os.path.realpath(os.path.expanduser("~")))
    return [path for path in found if os.path.dirname(path) != path and path != home]


# A run reads its own folders, the project, its Python and the system's; any other folder only when its access names
# it (S23). A file that is not there is not refused: trying to open it shows nothing.
_read_any = "*" in (_access.get("read") or []) or "reads" not in _settings
_reads = [os.path.normcase(os.path.realpath(path)) for path in [*(_settings.get("reads") or []), *(_access.get("read") or [])]
          if path != "*"] + _system_reads()


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
        given = os.fsdecode(os.fspath(path))
        real = os.path.realpath(given)
    except (TypeError, ValueError):
        return
    climbs = ".." in given.replace("\\", "/").split("/")
    if _run and ((_under(real, [_run]) and not _under(real, _kept)) or (climbs and not _under(real, _outputs))):
        _deny("outputs", real)
    if _under(real, _roots):
        return
    _emit("write", path=real)
    if "*" not in (_access.get("write") or []) and not _under(real, _writes):
        _deny("write", real)


def _read(path):
    if _read_any:
        return
    try:
        real = os.path.realpath(os.fsdecode(os.fspath(path)))
    except (TypeError, ValueError):
        return
    if _under(real, _roots) or _under(real, _reads) or not os.path.exists(real):
        return
    _deny("read", real)


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
            # Loading a library or opening a file or pipe natively goes past the checks above (S23).
            if not _access.get("network") and name.startswith(_NATIVE_PAST_CHECKS):
                _deny("network", "native call " + name)
        elif event == "ctypes.dlopen" and not _access.get("network"):
            library = os.path.basename(str(args[0] or "")).lower()
            if library.startswith(_NETWORK_LIBRARIES):
                _deny("network", "native library " + library)
        elif event == "socket.bind":
            host, port = _address(args[1])
            if _loopback(host):
                _bound.add(args[0])
            elif not _access.get("network"):
                _deny("network", f"listen on {host or '*'}:{port}")
        elif event in ("socket.connect", "socket.sendto", "socket.sendmsg"):
            host, port = _address(args[1] if len(args) > 1 else None)
            if host is None:
                pass
            elif not _loopback(host):
                _emit("network", host=str(host), port=port)
                if not _access.get("network"):
                    _deny("network", str(host))
            elif not _access.get("network") and not _own_port(port):
                _deny("loopback", f"{host}:{port}")
        elif event == "socket.getaddrinfo" and not _access.get("network"):
            # A name looked up is a message to a DNS server.
            if args and args[0] is not None and not _loopback(os.fsdecode(args[0]) if isinstance(args[0], bytes) else args[0]):
                _deny("network", f"look up {args[0]}")
        elif event == "_winapi.CreateFile":
            # A named pipe (\\.\pipe\...) is held as any other path outside the run's folders is.
            path, desired = (list(args) + [None, 0])[:2]
            if isinstance(path, (str, bytes, os.PathLike)):
                _write(path) if (desired or 0) & 0x40000000 else _read(path)
        elif event == "_winapi.CreateNamedPipe" and not _access.get("network"):
            _deny("loopback", "named pipe " + str(args[0] if args else ""))
        elif event == "_winapi.OpenProcess" and "*" not in _programs and not _programs:
            _deny("programs", f"open process {args[1] if len(args) > 1 else ''}")
        elif event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND)))
            if isinstance(path, (str, bytes, os.PathLike)):
                _write(path) if writes else _read(path)
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
