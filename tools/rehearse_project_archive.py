"""Rehearse one project archive: export it, restore it elsewhere, keep designing.

GH-56's acceptance is not "the ZIP extracted". It is "the project survived the
machine and can keep designing", so this driver exports a project through the
landed ``tools/create_project.py`` command, restores it into a folder with no
path back to the source, re-reads the restored copy with the ordinary project
readers, and - when a project runtime is given - asks that runtime for one
bounded, non-destructive candidate from the restored base.

Run both halves at once::

    python tools/rehearse_project_archive.py --source D:/projects/my-project \
        --archive D:/backups/my-project.zip --restore-parent D:/restored

Or split them, which is what ``scripts/dev/run-archive-rehearsal.ps1`` does,
because the runtime can only be started once the restored project exists::

    ... --phase export-restore     # writes <restore parent>/<id>.rehearsal-evidence.json
    ... --phase verify --runtime-url http://127.0.0.1:8111

Only the summary block is printed to stdout, in the order #56 asks for it, and
it carries identities alone: digests, counts, ids. Local paths and refusal
detail go to stderr, so what an operator pastes back into the issue is exactly
what stdout said. A category the source project genuinely lacks is reported
``SKIPPED (absent in source)`` and is never answered MATCH.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.project.archive import archive_target
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import DESIGN_STAGE
from archflow.project.repository import FilesystemProjectRepository, ProjectRepositoryError

CREATE_PROJECT = REPO / "tools" / "create_project.py"
EVIDENCE_SCHEMA = "ProjectArchiveRehearsalEvidence@1"
EVIDENCE_SUFFIX = ".rehearsal-evidence.json"
ABSENT = "SKIPPED (absent in source)"
NO_RUNTIME = "SKIPPED (no runtime)"
# The restored record answered, and what it answered gives this driver nothing
# it may restate. That is the runtime's side of the rehearsal, not a category
# the source project lacks, so it says which of the two it is.
NO_PARAMETER = "SKIPPED (no restatable parameter)"
UNSTATED = "unstated restore environment"
# The checked rows of the summary block #56 asks for, in its order; the four
# value rows above them are the report's own fields.
CHECKS = (
    "project identity", "HEAD/state digest", "branch/Stage identities", "retained runs",
    "Board revision", "registered source object hashes", "drawing/model receipt refs",
    "normal-reader reopen", "post-restore candidate from exact restored base",
    "runtime/config/chat transported", "source project changed by export",
)
REFUSED = frozenset({"MISMATCH", "FAIL", "YES"})
# What an archive must never carry across: the source machine's process state,
# its application settings and its chats.
FOREIGN_DIRECTORIES = ("chats", "config", "runtime")
RECORD_AREAS = ("records", "reviews", "candidates", "branches")
JOB_DEADLINE = 600.0
MAX_CONTINUATIONS = 6


class RehearsalError(RuntimeError):
    """The rehearsal could not be carried out; nothing is claimed either way."""


# What the runtime phase is allowed to raise. A runtime that refuses, drops the
# connection or answers a shape this driver cannot read is one FAIL row, never
# a traceback that throws away the identity rows already established.
RUNTIME_FAILURES = (
    OSError, ValueError, KeyError, TypeError, RehearsalError, ProjectRepositoryError,
)


@dataclass(frozen=True, slots=True)
class RehearsalReport:
    """One content-free acceptance summary, exactly as #56 may receive it."""

    source_build: str
    archive_sha256: str
    archive_bytes: int
    environment: str
    checks: dict[str, str]

    def lines(self) -> list[str]:
        return [
            f"source build: {self.source_build}",
            f"archive sha256: {self.archive_sha256}",
            f"archive size: {self.archive_bytes}",
            f"restore environment: {self.environment}",
            *(f"{name}: {self.checks[name]}" for name in CHECKS),
        ]

    @property
    def ok(self) -> bool:
        """No refusal. A SKIPPED category is an absence, not a failure."""

        return not any(value in REFUSED for value in self.checks.values())


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _tree(root: Path) -> dict[str, str]:
    """Every file under one project, by project-relative path and digest.

    Lock files belong to whichever process is reading, not to the project:
    they appear and disappear around any read, including this one.
    """

    return {path.relative_to(root).as_posix(): _digest(path)
            for path in sorted(root.rglob("*")) if path.is_file() and path.suffix != ".lock"}


def _fingerprint(tree: Mapping[str, str]) -> str:
    rows = "\n".join(f"{path} {digest}" for path, digest in sorted(tree.items()))
    return hashlib.sha256(rows.encode("utf-8")).hexdigest()


def _is_artifact_path(path: str) -> bool:
    """Does this retained reference name a model/drawing artifact, not a record?

    Records are content-addressed JSON in a run's record areas; an artifact is
    an ingested object, an export, a workspace file, or the workspace-local
    name a native CAD receipt carries.
    """

    parts = PurePosixPath(path.replace("\\", "/")).parts
    if not parts:
        return False
    if parts[0] in ("objects", "exports"):
        return True
    if parts[0] == "runs":
        return len(parts) >= 3 and parts[2] not in RECORD_AREAS
    return len(parts) == 1


def _artifact_digests(value: Any, found: set[str]) -> None:
    """Every artifact digest a retained record names, wherever it names it."""

    if isinstance(value, Mapping):
        digest, location = value.get("sha256"), value.get("relative_path")
        if (isinstance(location, str) and isinstance(digest, str) and len(digest) == 64
                and _is_artifact_path(location)):
            found.add(digest)
        native, inspection = value.get("artifact_relative_path"), value.get("inspection")
        if isinstance(native, str) and isinstance(inspection, Mapping):
            file_digest = inspection.get("file_sha256")
            if isinstance(file_digest, str) and len(file_digest) == 64:
                found.add(file_digest)
        for item in value.values():
            _artifact_digests(item, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _artifact_digests(item, found)


def identities(root: Path) -> dict[str, Any]:
    """What one project says about itself, through the normal project readers.

    Identities only - ids, digests, counts and page counts. No design content,
    no file name and no prose is read into this, because it is written to disk
    beside the restored project and its numbers are what may be posted back.
    """

    repository = FilesystemProjectRepository.open(root)
    head = repository.read_head()
    runs = repository.layout.runs
    run_ids = sorted(item.name for item in runs.iterdir() if item.is_dir()) if runs.is_dir() else []
    kinds: dict[str, int] = {}
    stages: list[str] = []
    boards: dict[str, Any] = {}
    documents: list[list[Any]] = []
    artifacts: set[str] = set()
    for run_id in run_ids:
        destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)
        for ref in repository.list_json(run=repository.load_run(run_id), destination=destination):
            kinds[ref.record_kind] = kinds.get(ref.record_kind, 0) + 1
            payload = repository.load_json(ref)
            if ref.record_kind == DESIGN_STAGE:
                stages.append(ref.sha256)
            if payload.get("schema") == "StudioBoardScene@1":
                boards[ref.sha256] = payload.get("previousRevisionSha256")
            if payload.get("schema") == "StudioSourceDocument@1":
                documents.append([payload.get("run_id"), payload.get("asset_sha256"),
                                  payload.get("revisionRef") or "", len(payload.get("pages") or ())])
            _artifact_digests(payload, artifacts)
    tree = _tree(root)
    present = set(tree.values())
    branches = {branch_id: hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
                for branch_id, payload in repository.read_design_branches().items()}
    return {
        "project_id": repository.load_manifest().project_id,
        "head": {"version": head.version, "state_sha256": head.state_sha256},
        "branches": branches,
        "stages": sorted(stages),
        "run_ids": run_ids,
        "record_kinds": dict(sorted(kinds.items())),
        # Each board revision with the revision it continues, so a restored
        # board is the same saved scene and not merely a board.
        "board_revisions": dict(sorted(boards.items())),
        "documents": sorted(documents),
        # Recomputed from the bytes on disk: a registered original whose bytes
        # were altered answers a digest its registration never named.
        "objects": sorted(digest for path, digest in tree.items() if path.startswith("objects/")),
        "artifact_refs": sorted([digest, digest in present] for digest in artifacts),
        "foreign_directories": sorted({path.split("/", 1)[0] for path in tree} & set(FOREIGN_DIRECTORIES)),
        "tree_fingerprint": _fingerprint(tree),
    }


def _create_project(python: str, *arguments: str) -> str:
    """Run the landed CLI, with this checkout on the child's import path."""

    environment = dict(os.environ)
    entries = [str(REPO)] + [item for item in environment.get("PYTHONPATH", "").split(os.pathsep) if item]
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(entries))
    result = subprocess.run([python, str(CREATE_PROJECT), *arguments],
                            capture_output=True, text=True, env=environment)
    if result.returncode != 0:
        raise RehearsalError(f"create_project {arguments[-2]} refused: "
                             f"{(result.stderr or result.stdout).strip()}")
    return result.stdout


def _export(python: str, source_dir: Path, archive_path: Path) -> None:
    _create_project(python, "--project", str(source_dir), "--export-archive", str(archive_path))


def _restore(python: str, restored_dir: Path, archive_path: Path) -> Path:
    _create_project(python, "--project", str(restored_dir), "--restore-archive", str(archive_path))
    return restored_dir


def source_build() -> str:
    """The revision this driver runs from, or ``unknown`` outside a checkout."""

    try:
        result = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                                capture_output=True, text=True)
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def export_and_restore(source_dir: Path, archive_path: Path, restore_parent: Path, *,
                       python: str, environment: str) -> dict[str, Any]:
    """Capture the source identities, export, restore, and say what travelled."""

    source_dir, archive_path = Path(source_dir).resolve(), Path(archive_path).resolve()
    restore_parent = Path(restore_parent).resolve()
    source = identities(source_dir)
    _export(python, source_dir, archive_path)
    # Read once from the archive's own manifest: the folder a project sits in
    # is a location, and both the restored folder and the evidence file beside
    # it are named by the identity the archive carries.
    restored_dir = _restore(python, archive_target(restore_parent, archive_path), archive_path)
    return {
        "schema": EVIDENCE_SCHEMA,
        "source_build": source_build(),
        "environment": environment,
        "project_id": restored_dir.name,
        "archive_path": str(archive_path),
        "archive_sha256": _digest(archive_path),
        "archive_bytes": archive_path.stat().st_size,
        "restored_dir": str(restored_dir),
        # Export reads; it never writes the project it reads.
        "source_changed": "NO" if _fingerprint(_tree(source_dir)) == source["tree_fingerprint"] else "YES",
        "source": source,
    }


def _continuations(state: Mapping[str, Any], digest: str) -> list[dict[str, Any]]:
    """Restatements of what the restored record already says, bounded and few.

    Each proposal sets a number to the number it already holds: enough to make
    a real candidate through the real routes, and not a design change.
    """

    components = [row.get("componentId") for row in (state.get("componentTree") or []) if row.get("componentId")]
    bodies: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()

    def offer(utterance: str, component: str | None, element: str | None) -> None:
        if not isinstance(component, str) or (utterance, component, element) in seen:
            return
        seen.add((utterance, component, element))
        body = {"stateDigest": digest, "utterance": utterance, "targetComponentId": component}
        if element is not None:
            body["elementId"] = element
        bodies.append(body)

    def number(value: Any) -> str | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return json.dumps(value)

    for row in state.get("parameters") or []:
        written = number(row.get("value"))
        if written is None or not isinstance(row.get("key"), str):
            continue
        if row.get("epistemicStatus") != "declared" or row.get("lockAuthority"):
            continue
        for component in components[-1:] + components[:1]:
            offer(f"set {row['key']} to {written}", component, None)
    for element in state.get("elements") or []:
        for key, value in (element.get("numericFields") or {}).items():
            written = number(value)
            if written is not None:
                offer(f"set {key} to {written}", element.get("componentId"), element.get("elementId"))
    return bodies[:MAX_CONTINUATIONS]


def _continue_design(request: Callable[..., Mapping[str, Any]], head: Mapping[str, Any],
                     notes: list[str]) -> str:
    """One candidate from the restored base, contained to one row of the block.

    Ten identity rows are already established by the time this runs, and they
    are the point of the rehearsal; a runtime that refuses, disappears or
    answers a reply this driver cannot read costs this row and nothing else.
    """

    try:
        return _candidate_from_restored_base(request, head, notes)
    except RUNTIME_FAILURES as exc:
        notes.append(f"the runtime phase could not be completed: {exc!r}")
        return "FAIL"


def _candidate_from_restored_base(request: Callable[..., Mapping[str, Any]],
                                  head: Mapping[str, Any], notes: list[str]) -> str:
    """One candidate from the restored base, through the runtime's own routes."""

    state = request("GET", "/api/state", None)
    published = state.get("published") or {}
    if (published.get("version"), published.get("stateSha256")) != (head["version"], head["state_sha256"]):
        notes.append("the runtime publishes a version the restored HEAD does not")
        return "FAIL"
    digest = state.get("stateDigest")
    if not isinstance(digest, str):
        notes.append("the restored record states no bound view to propose against")
        return NO_PARAMETER
    bodies = _continuations(state, digest)
    if not bodies:
        notes.append("the restored record declares no number this driver may restate")
        return NO_PARAMETER
    proposal: Mapping[str, Any] | None = None
    for body in bodies:
        try:
            proposal = request("POST", "/api/proposals", body)
            break
        except Exception as exc:  # a refused proposal is an answer, not a crash
            notes.append(f"proposal refused: {exc}")
    if proposal is None:
        return "FAIL"
    accepted = request("POST", f"/api/proposals/{proposal['proposalId']}/candidate", None)
    job_id, candidate_id = accepted["jobId"], accepted["candidateId"]
    deadline, status = time.monotonic() + JOB_DEADLINE, ""
    while time.monotonic() < deadline:
        status = request("GET", f"/api/jobs/{job_id}", None).get("status", "")
        if status in ("succeeded", "failed"):
            break
        time.sleep(0.2)
    if status != "succeeded":
        notes.append(f"the candidate job ended as {status or 'unfinished'}")
        return "FAIL"
    base = request("GET", f"/api/candidates/{candidate_id}", None).get("base") or {}
    if (base.get("version"), base.get("stateSha256")) != (head["version"], head["state_sha256"]):
        notes.append("the candidate names a base the restored HEAD does not")
        return "FAIL"
    return "PASS"


def verify_restored(evidence: Mapping[str, Any], *,
                    request: Callable[..., Mapping[str, Any]] | None = None) -> RehearsalReport:
    """Read the restored copy and answer the issue's block, line by line."""

    source = evidence["source"]
    restored_dir = Path(evidence["restored_dir"])
    notes: list[str] = []
    checks: dict[str, str] = {}
    try:
        restored: dict[str, Any] | None = identities(restored_dir)
        FilesystemProjectRepository.open(restored_dir).verify()
        reopen = "PASS"
    except (ProjectRepositoryError, OSError, ValueError, KeyError) as exc:
        restored, reopen = None, "FAIL"
        notes.append(f"the restored project did not reopen: {exc}")
    read: dict[str, Any] = restored if restored is not None else {}

    def compare(name: str, expected: Any, actual: Any, *, absent: bool = False) -> None:
        """A category the source lacks is absent; it is never answered MATCH."""

        if restored is None:
            checks[name] = "MISMATCH"
        elif absent:
            checks[name] = ABSENT
        elif expected == actual:
            checks[name] = "MATCH"
        else:
            checks[name] = "MISMATCH"
            notes.append(f"{name}: the restored copy answers something else")

    compare("project identity", [source["project_id"]] * 2,
            [read.get("project_id"), restored_dir.name])
    compare("HEAD/state digest", source["head"], read.get("head"))
    compare("branch/Stage identities", [source["branches"], source["stages"]],
            [read.get("branches"), read.get("stages")],
            absent=not (source["branches"] or source["stages"]))
    compare("retained runs", [source["run_ids"], source["record_kinds"]],
            [read.get("run_ids"), read.get("record_kinds")])
    compare("Board revision", source["board_revisions"], read.get("board_revisions"),
            absent=not source["board_revisions"])
    compare("registered source object hashes", [source["objects"], source["documents"]],
            [read.get("objects"), read.get("documents")],
            absent=not (source["objects"] or source["documents"]))
    compare("drawing/model receipt refs", source["artifact_refs"], read.get("artifact_refs"),
            absent=not source["artifact_refs"])
    checks["normal-reader reopen"] = reopen
    # A tree that was never read says nothing about what travelled; the failed
    # reopen above is the refusal, and this row does not invent a second one.
    checks["runtime/config/chat transported"] = (
        "SKIPPED (the restored project was not read)" if restored is None
        else ("NO" if not restored["foreign_directories"] else "YES"))
    checks["source project changed by export"] = evidence["source_changed"]
    if request is None or restored is None:
        checks["post-restore candidate from exact restored base"] = NO_RUNTIME
    else:
        checks["post-restore candidate from exact restored base"] = _continue_design(
            request, source["head"], notes)
        # A candidate is not an acceptance: the restored HEAD has to be where
        # the archive left it once the runtime has had its turn.
        moved = FilesystemProjectRepository.open(restored_dir).read_head()
        if {"version": moved.version, "state_sha256": moved.state_sha256} != source["head"]:
            checks["HEAD/state digest"] = "MISMATCH"
            notes.append("the continuation moved the restored HEAD")
    for note in notes:
        print(f"rehearse_project_archive: {note}", file=sys.stderr)
    return RehearsalReport(
        source_build=evidence["source_build"], archive_sha256=evidence["archive_sha256"],
        archive_bytes=evidence["archive_bytes"], environment=evidence["environment"],
        checks=checks,
    )


def rehearse(source_dir: Path, archive_path: Path, restore_parent: Path, *, python: str,
             environment: str,
             request: Callable[..., Mapping[str, Any]] | None = None) -> RehearsalReport:
    """The whole rehearsal in one call: export, restore, verify, continue."""

    return verify_restored(
        export_and_restore(source_dir, archive_path, restore_parent,
                           python=python, environment=environment),
        request=request,
    )


def _evidence_path(restore_parent: Path, project_id: str) -> Path:
    """Beside the restored project, named by the same identity it is named by."""

    return Path(restore_parent).resolve() / f"{project_id}{EVIDENCE_SUFFIX}"


def _write_evidence(path: Path, evidence: Mapping[str, Any]) -> None:
    """Hand the source-side identities to a later verify phase, and nothing else."""

    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")


def _http_client(base_url: str, timeout: float = 60.0) -> Callable[..., dict[str, Any]]:
    """The default runtime client: plain JSON over HTTP to a running runtime."""

    root = base_url.rstrip("/")

    def request(method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data is not None else {}
        call = urllib.request.Request(f"{root}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(call, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RehearsalError(f"{method} {path} did not answer a JSON object")
        return payload

    return request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, type=Path, help="the project directory to export; its name is the project id")
    parser.add_argument("--archive", required=True, type=Path, help="where to write the archive; outside the project. The verify phase reads the project id from it")
    parser.add_argument("--restore-parent", required=True, type=Path, help="the parent folder the archive restores into, under the project id")
    parser.add_argument("--python", default=sys.executable, help="the interpreter that runs tools/create_project.py")
    parser.add_argument("--environment-label", help="how the restore environment is described in the summary block")
    parser.add_argument("--runtime-url", help="a project runtime already bound to the restored project, e.g. http://127.0.0.1:8111")
    parser.add_argument("--phase", choices=("all", "export-restore", "verify"), default="all",
                        help="all, or the two halves a wrapper needs in order to start a runtime between them")
    args = parser.parse_args(argv)
    try:
        if args.phase == "verify":
            # The archive names the project; this phase reads that name from it
            # rather than from whatever the source folder happens to be called.
            target = archive_target(args.restore_parent.resolve(), args.archive.resolve())
            evidence_path = _evidence_path(args.restore_parent, target.name)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            if evidence.get("schema") != EVIDENCE_SCHEMA:
                raise RehearsalError(f"not a rehearsal evidence file: {evidence_path}")
            if args.environment_label:
                evidence["environment"] = args.environment_label
        else:
            evidence = export_and_restore(args.source, args.archive, args.restore_parent,
                                          python=args.python,
                                          environment=args.environment_label or UNSTATED)
            if args.phase == "export-restore":
                evidence_path = _evidence_path(args.restore_parent, evidence["project_id"])
                _write_evidence(evidence_path, evidence)
                print(f"restored: {evidence['restored_dir']}", file=sys.stderr)
                print(f"evidence: {evidence_path}", file=sys.stderr)
                return 0
        report = verify_restored(
            evidence, request=_http_client(args.runtime_url) if args.runtime_url else None)
    except (RehearsalError, ProjectRepositoryError, OSError, ValueError) as exc:
        print(f"rehearse_project_archive: {exc}", file=sys.stderr)
        return 2
    for line in report.lines():
        print(line)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
