"""Compare a candidate code root's projections with a base's on one project (GH-376).

    python tools/benchmarks/projection_check.py --base <code-root> --candidate <code-root> \\
        --project <project-dir> --out <result.json> [--summary <summary.md>] \\
        [--index-dir <cache-dir>]

The project is copied once into a scratch directory under its own folder name
and every step reads that one copy:

1. the base, in a fresh process, reads every route;
2. the candidate, in one process, reads every route cold, retains one
   candidate review through ``POST /api/candidate-reviews`` and reads every
   route again;
3. the base, in a fresh process, reads every route once more, after the write.

The candidate's cold answers must equal the base's first answers and its
answers after the write must equal the base's second answers, byte for byte.
A candidate that tags its answers (ADR-008 conditional reads) must also answer
``If-None-Match`` with 304, show its own write at once under a new tag and serve
artifact bytes ``immutable``; a base that predates them is not asked. Timings
are reported, and fail only when the candidate's cold read is both more than
twice the base's and more than 200 ms slower.

With ``--index-dir`` (opt-in, ADR-008 phase 1b) the candidate runs with that
directory, outside the project copy, as its project cache directory, keeps its
project index in ``<dir>/index`` and waits for the index to
load before its cold reads; the result then says how the index loaded, how
long that took and where it stood after the write. What is compared and judged
does not change. Without it the candidate keeps no index.

Each side runs in its own interpreter with its kernel source
(``<code-root>/packages/archflow/src``), ``<code-root>`` and its runtime source
(``<code-root>/services/project-runtime/src``) first on ``sys.path``, and refuses to
run if ``archflow`` or ``project_runtime`` resolves anywhere else. This file imports
nothing from either root; the worker half (``worker`` subcommand) imports the side it
reads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence

ROUTES = (
    "/api/design-history?branchId=main",
    "/api/worktrees",
    "/api/artifacts",
    "/api/documents",
    "/api/working-source?workspace=modeling",
    "/api/board",
)
DESIGN_HISTORY = ROUTES[0]
REVIEW_REASON = "projection check"
# Timing gate: both must hold before a slower candidate fails.
SLOWER_RATIO = 2.0
SLOWER_MS = 200.0
# How far into the past the scratch copy's newest time is moved, so the
# project is settled (older than any racy window) before a side reads it.
SETTLED_AGE_NS = 3600 * 1_000_000_000
# Where a code root keeps archflow and the Project Runtime.
KERNEL_SOURCE = Path("packages") / "archflow" / "src"
RUNTIME_SOURCE = Path("services") / "project-runtime" / "src"
# How long an opt-in index (``--index-dir``) is waited for to load or catch up.
INDEX_WAIT_S = 600.0


# ---- the project copy --------------------------------------------------------


def settle(root: Path) -> None:
    """Move every time in ``root`` back by one offset, keeping their order.

    A fresh copy or a just-generated project was written seconds ago; a
    runtime does not answer 304 for a project that is still settling. One
    offset keeps "newest" meaning what it meant.
    """

    paths = [root, *root.rglob("*")]
    stats = {path: path.stat() for path in paths}
    shift = time.time_ns() - SETTLED_AGE_NS - max(stat.st_mtime_ns for stat in stats.values())
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        stat = stats[path]
        os.utime(path, ns=(stat.st_atime_ns + shift, stat.st_mtime_ns + shift))


def scratch_copy(project: Path, scratch: Path) -> Path:
    """Copy the project into ``scratch`` under its own folder name."""

    target = scratch / project.name
    shutil.copytree(project, target)
    settle(target)
    return target


# ---- one side, in its own interpreter ----------------------------------------


def _bind(code_root: Path) -> None:
    """Put this side's code first on ``sys.path`` and prove it is what imports.

    Importing the runtime package puts the side's other source roots in front as well.
    """

    here = Path(__file__).resolve().parent
    rest = [entry for entry in sys.path if Path(entry or ".").resolve() != here]
    sys.path[:] = [str(code_root / KERNEL_SOURCE), str(code_root), str(code_root / RUNTIME_SOURCE), *rest]
    import archflow
    import project_runtime

    for module in (archflow, project_runtime):
        location = Path(module.__file__).resolve()
        if not location.is_relative_to(code_root.resolve()):
            raise SystemExit(f"{module.__name__} resolved to {location}, outside {code_root}")


def _write(path: Path, data: bytes) -> None:
    """The one place this tool writes a file: under ``--work``, ``--out`` or ``--summary``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _get(client: Any, route: str, bodies: Path | None, **headers: str) -> tuple[dict[str, Any], Any]:
    started = time.perf_counter()
    response = client.get(route, headers=headers)
    elapsed = (time.perf_counter() - started) * 1000.0
    content = response.content
    if bodies is not None:
        _write(bodies / _slug(route), content)
    return {
        "route": route,
        "status": response.status_code,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "ms": round(elapsed, 2),
        "etag": response.headers.get("etag"),
    }, response


def _read_all(client: Any, bodies: Path | None) -> list[dict[str, Any]]:
    return [_get(client, route, bodies)[0] for route in ROUTES]


def _first_candidate(client: Any) -> str | None:
    history = client.get(DESIGN_HISTORY).json()
    return next((row["candidateId"] for row in history.get("candidates", ()) if row.get("candidateId")), None)


def _index_state(binding: Any) -> dict[str, Any]:
    state = binding.index_state()
    if state is None:
        return {"state": None}
    return {"epoch": state.token.epoch, "revision": state.token.revision,
            "readable": binding.index_reader(wait=INDEX_WAIT_S) is not None}


def _worker(code_root: Path, project_dir: Path, mode: str, bodies: Path,
            index_dir: Path | None = None) -> dict[str, Any]:
    _bind(code_root)
    from fastapi.testclient import TestClient
    from project_runtime.main import create_app
    from project_runtime.settings import StudioSettings
    import archflow

    result: dict[str, Any] = {"mode": mode, "archflow": str(Path(archflow.__file__).resolve())}
    settings = StudioSettings(project_dir=project_dir, **({} if index_dir is None else {"cache_dir": index_dir}))
    app = create_app(settings)
    with TestClient(app) as client:
        binding = None
        if index_dir is not None:
            from project_runtime.application.binding import bound_project

            started = time.perf_counter()
            binding = bound_project(app.state)
            keeper = binding.await_index(INDEX_WAIT_S)
            result["index"] = {
                "dir": str(index_dir / "index"),
                "loaded": None if keeper is None else keeper.index.loaded,
                "loadMs": (time.perf_counter() - started) * 1000.0,
                "failure": getattr(binding._index_keeper, "failure", None) if keeper is None else None,
                **_index_state(binding),
            }
        if mode == "read":
            result["reads"] = _read_all(client, bodies / "reads")
            return result
        cold = _read_all(client, bodies / "cold")
        result["cold"] = cold
        supports = any(row["etag"] for row in cold)
        result["supportsConditional"] = supports
        if supports:
            for row in cold:
                if row["etag"]:
                    row["revalidateStatus"] = client.get(row["route"], headers={"If-None-Match": row["etag"]}).status_code
        subject = _first_candidate(client)
        write: dict[str, Any] = {"subjectRef": subject}
        if subject is not None:
            response = client.post("/api/candidate-reviews", json={
                "projectId": client.get(DESIGN_HISTORY).json()["projectId"], "subjectKind": "candidate",
                "subjectRef": subject, "action": "endorse", "reason": REVIEW_REASON,
            })
            write["status"] = response.status_code
            if response.status_code >= 400:
                write["detail"] = response.text[:500]
        result["write"] = write
        after = _read_all(client, bodies / "after")
        result["after"] = after
        if binding is not None:
            result["index"]["afterWrite"] = _index_state(binding)
        if supports:
            result["afterWrite"] = _after_write(client, subject, cold, after)
            result["artifactBytes"] = _artifact_bytes(client)
    return result


def _after_write(client: Any, subject: str | None, cold: list[dict], after: list[dict]) -> dict[str, Any]:
    """Whether this process's own write shows at once, under a new design-history tag."""

    before_tag = next(row["etag"] for row in cold if row["route"] == DESIGN_HISTORY)
    after_tag = next(row["etag"] for row in after if row["route"] == DESIGN_HISTORY)
    history = client.get(DESIGN_HISTORY).json()
    row = next((row for row in history.get("candidates", ()) if row.get("candidateId") == subject), None)
    review = None if row is None else row.get("review")
    return {
        "etagBefore": before_tag,
        "etagAfter": after_tag,
        "reviewVisible": bool(review) and review.get("reason") == REVIEW_REASON and bool(review.get("endorsed")),
    }


def _artifact_bytes(client: Any) -> dict[str, Any]:
    """The first available artifact's bytes: ``immutable`` and hashing to their sha."""

    listing = client.get("/api/artifacts").json()
    sha = next((row["artifactId"] for row in listing.get("artifacts", ())
                if not row.get("unavailableReason") and _is_sha(row.get("artifactId"))), None)
    if sha is None:
        return {"sha256": None}
    response = client.get(f"/api/artifacts/{sha}/bytes")
    return {
        "sha256": sha,
        "status": response.status_code,
        "cacheControl": response.headers.get("cache-control"),
        "bodySha256": hashlib.sha256(response.content).hexdigest(),
    }


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _slug(route: str) -> str:
    return route.strip("/").replace("/", "_").replace("?", "__").replace("=", "-").replace("&", "_") + ".body"


def run_side(code_root: Path, project_dir: Path, mode: str, work: Path, name: str,
             index_dir: Path | None = None) -> dict[str, Any]:
    """Run one side in a fresh interpreter and answer what it measured."""

    out = work / f"{name}.json"
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env.setdefault("PYTHONUTF8", "1")
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "worker", "--code-root", str(code_root),
         "--project-dir", str(project_dir), "--mode", mode, "--bodies", str(work / "bodies" / name),
         "--out", str(out), *(() if index_dir is None else ("--index-dir", str(index_dir)))],
        env=env, cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{name} ({code_root}) failed with exit code {completed.returncode}:\n"
                           f"{completed.stdout[-4000:]}\n{completed.stderr[-4000:]}")
    return json.loads(out.read_text(encoding="utf-8"))


# ---- the verdict -------------------------------------------------------------


def _by_route(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {row["route"]: row for row in rows}


def _same(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    return (left is not None and right is not None
            and left["status"] == right["status"] and left["sha256"] == right["sha256"])


def slower(base_ms: float | None, candidate_ms: float | None) -> bool:
    """The timing gate: more than twice the base and more than 200 ms slower."""

    if base_ms is None or candidate_ms is None:
        return False
    return candidate_ms > SLOWER_RATIO * base_ms and candidate_ms - base_ms > SLOWER_MS


def evaluate(base_before: Sequence[Mapping[str, Any]], base_after: Sequence[Mapping[str, Any]],
             candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Judge one run from what each side measured; nothing here reads a project."""

    failures: list[str] = []
    before, after = _by_route(base_before), _by_route(base_after)
    cold, later = _by_route(candidate.get("cold", ())), _by_route(candidate.get("after", ()))
    rows = []
    for route in ROUTES:
        parity_before = _same(before.get(route), cold.get(route))
        parity_after = _same(after.get(route), later.get(route))
        base_ms = before.get(route, {}).get("ms")
        cold_ms = cold.get(route, {}).get("ms")
        too_slow = slower(base_ms, cold_ms)
        if not parity_before:
            failures.append(f"{route}: candidate cold read differs from base before the write")
        if not parity_after:
            failures.append(f"{route}: candidate read after the write differs from base after the write")
        if too_slow:
            failures.append(f"{route}: candidate cold read {cold_ms:.0f} ms against base {base_ms:.0f} ms")
        for side, row in (("base", before.get(route)), ("candidate", cold.get(route))):
            if row is not None and row["status"] != 200:
                failures.append(f"{route}: {side} answered {row['status']}")
        rows.append({
            "route": route, "parityBefore": parity_before, "parityAfter": parity_after,
            "baseColdMs": base_ms, "candidateColdMs": cold_ms,
            "candidateAfterMs": later.get(route, {}).get("ms"), "slower": too_slow,
        })

    write = candidate.get("write") or {}
    if write.get("subjectRef") is None:
        failures.append("the design history lists no candidate to review")
    elif write.get("status") != 201:
        failures.append(f"POST /api/candidate-reviews answered {write.get('status')}: {write.get('detail', '')}")

    conditional: list[dict[str, Any]] = []
    supports = any(row.get("etag") for row in candidate.get("cold", ()))
    if supports:
        for route in ROUTES:
            row = cold.get(route) or {}
            if not row.get("etag"):
                conditional.append({"check": f"{route} carries an ETag", "ok": False})
                continue
            conditional.append({"check": f"{route} carries an ETag", "ok": True})
            conditional.append({"check": f"{route} If-None-Match answers 304",
                                "ok": row.get("revalidateStatus") == 304,
                                "detail": f"answered {row.get('revalidateStatus')}"})
        own = candidate.get("afterWrite") or {}
        conditional.append({"check": "the review write changes the design-history ETag",
                            "ok": bool(own.get("etagAfter")) and own.get("etagAfter") != own.get("etagBefore")})
        conditional.append({"check": "the design history shows the review at once",
                            "ok": bool(own.get("reviewVisible"))})
        artifact = candidate.get("artifactBytes") or {}
        if artifact.get("sha256") is None:
            conditional.append({"check": "an available artifact to fetch by sha", "ok": False})
        else:
            conditional.append({"check": "artifact bytes are served immutable",
                                "ok": artifact.get("status") == 200
                                and "immutable" in (artifact.get("cacheControl") or ""),
                                "detail": f"{artifact.get('status')} {artifact.get('cacheControl')}"})
            conditional.append({"check": "artifact bytes hash to their sha",
                                "ok": artifact.get("bodySha256") == artifact["sha256"]})
        failures.extend(f"conditional: {row['check']}" + (f" ({row['detail']})" if row.get("detail") else "")
                        for row in conditional if not row["ok"])
    return {"passed": not failures, "failures": failures, "routes": rows,
            "supportsConditional": supports, "conditional": conditional}


def first_difference(left: Path, right: Path, context: int = 120) -> str | None:
    """Where two saved bodies first differ, for a failure someone has to read."""

    if not left.is_file() or not right.is_file():
        return None
    a, b = left.read_bytes(), right.read_bytes()
    if a == b:
        return None
    offset = next((index for index, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    start = max(0, offset - context // 2)
    return (f"first difference at byte {offset} (base {len(a)} bytes, candidate {len(b)} bytes)\n"
            f"  base:      {a[start:offset + context // 2].decode('utf-8', 'replace')!r}\n"
            f"  candidate: {b[start:offset + context // 2].decode('utf-8', 'replace')!r}")


# ---- the report --------------------------------------------------------------


def _ms(value: float | None) -> str:
    return "–" if value is None else f"{value:.0f}"


def summary_markdown(result: Mapping[str, Any]) -> str:
    verdict = result["verdict"]
    lines = [
        f"### Projection check: {'passed' if verdict['passed'] else 'FAILED'} ({result['platform']})",
        "",
        f"Base `{result['base']['revision']}` · candidate `{result['candidate']['revision']}` · "
        f"project `{result['project']['name']}` ({result['project']['runs']} runs, "
        f"{result['project']['jsonFiles']} JSON files) · reviewed `{result['write'].get('subjectRef')}`",
        "",
        "| Route | Parity before write | Parity after write | Base cold ms | Candidate cold ms | Candidate after-change ms |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in verdict["routes"]:
        cold = _ms(row["candidateColdMs"]) + (" ⚠" if row["slower"] else "")
        lines.append(f"| `{row['route']}` | {'✅' if row['parityBefore'] else '❌'} | "
                     f"{'✅' if row['parityAfter'] else '❌'} | {_ms(row['baseColdMs'])} | {cold} | "
                     f"{_ms(row['candidateAfterMs'])} |")
    lines.append("")
    if verdict["supportsConditional"]:
        passed = sum(1 for row in verdict["conditional"] if row["ok"])
        lines.append(f"Conditional reads: {passed}/{len(verdict['conditional'])} checks passed.")
    else:
        lines.append("Conditional reads: the candidate tags no answer; not checked.")
    index = result.get("index")
    if index:
        lines.append(f"Index (opt-in): {index.get('loaded') or 'not used'} in {_ms(index.get('loadMs'))} ms, "
                     f"revision {index.get('revision')}; after the write revision "
                     f"{index.get('afterWrite', {}).get('revision')}"
                     + (f"; failure: {index['failure']}" if index.get("failure") else "") + ".")
    if verdict["failures"]:
        lines += ["", "**Failures**", ""] + [f"- {failure}" for failure in verdict["failures"]]
    for route, text in result.get("differences", {}).items():
        lines += ["", f"<details><summary>{route}</summary>", "", "```", text, "```", "</details>"]
    return "\n".join(lines) + "\n"


def _revision(root: Path) -> str:
    try:
        completed = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
                                   capture_output=True, text=True, check=True)
        return completed.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def check(base: Path, candidate: Path, project: Path, work: Path, index_dir: Path | None = None) -> dict[str, Any]:
    """Run the three steps on one scratch copy and judge them; the candidate keeps an index only when asked."""

    copy = scratch_copy(project, work / "project")
    if index_dir is not None and index_dir.is_relative_to(copy):
        raise SystemExit(f"--index-dir must be outside the project copy {copy}")
    base_before = run_side(base, copy, "read", work, "base-before")
    settle(copy)
    candidate_side = run_side(candidate, copy, "candidate", work, "candidate", index_dir)
    base_after = run_side(base, copy, "read", work, "base-after")
    verdict = evaluate(base_before["reads"], base_after["reads"], candidate_side)
    differences: dict[str, str] = {}
    for row in verdict["routes"]:
        slug = _slug(row["route"])
        for ok, left, right, phase in (
            (row["parityBefore"], "base-before/reads", "candidate/cold", "before"),
            (row["parityAfter"], "base-after/reads", "candidate/after", "after"),
        ):
            if not ok:
                text = first_difference(work / "bodies" / left / slug, work / "bodies" / right / slug)
                if text:
                    differences[f"{row['route']} ({phase} the write)"] = text
    return {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "base": {"root": str(base), "revision": _revision(base), "archflow": base_before["archflow"]},
        "candidate": {"root": str(candidate), "revision": _revision(candidate),
                      "archflow": candidate_side["archflow"]},
        "project": {"name": project.name, "runs": sum(1 for path in (project / "runs").iterdir() if path.is_dir()),
                    "jsonFiles": sum(1 for _ in project.rglob("*.json"))},
        "write": candidate_side.get("write", {}),
        "index": candidate_side.get("index"),
        "sides": {"baseBefore": base_before, "candidate": candidate_side, "baseAfter": base_after},
        "verdict": verdict,
        "differences": differences,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["worker"]:
        parser = argparse.ArgumentParser(prog="projection_check.py worker")
        parser.add_argument("--code-root", type=Path, required=True)
        parser.add_argument("--project-dir", type=Path, required=True)
        parser.add_argument("--mode", choices=("read", "candidate"), required=True)
        parser.add_argument("--bodies", type=Path, required=True)
        parser.add_argument("--out", type=Path, required=True)
        parser.add_argument("--index-dir", type=Path)
        options = parser.parse_args(arguments[1:])
        result = _worker(options.code_root.resolve(), options.project_dir.resolve(), options.mode, options.bodies,
                         None if options.index_dir is None else options.index_dir.resolve())
        _write(options.out, json.dumps(result, indent=2).encode("utf-8"))
        return 0

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", type=Path, required=True, help="the base code root")
    parser.add_argument("--candidate", type=Path, required=True, help="the candidate code root")
    parser.add_argument("--project", type=Path, required=True, help="the project directory (left unchanged)")
    parser.add_argument("--out", type=Path, required=True, help="where to write the JSON result")
    parser.add_argument("--summary", type=Path, help="where to write the Markdown summary")
    parser.add_argument("--work", type=Path, help="scratch directory (default: a new temporary one)")
    parser.add_argument("--index-dir", type=Path,
                        help="opt-in: the candidate's project cache directory (outside the project); it keeps its "
                             "project index in <dir>/index and waits "
                             "for it to load; what is judged does not change")
    options = parser.parse_args(arguments)
    work = (options.work or Path(tempfile.mkdtemp(prefix="projection-check-"))).resolve()
    result = check(options.base.resolve(), options.candidate.resolve(), options.project.resolve(), work,
                   None if options.index_dir is None else options.index_dir.resolve())
    _write(options.out, json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8"))
    text = summary_markdown(result)
    if options.summary is not None:
        _write(options.summary, text.encode("utf-8"))
    sys.stdout.write(text)
    return 0 if result["verdict"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
