"""X7: what carrying the state inside the model file costs as the model grows, against a sidecar.

For N posts (a square grid of real prism B-reps, no instancing), each arm (C-full: state in the file; D: state in a
sidecar) and each format (3DM, GLB):

1. write the start state file;
2. make ``revisions`` single-parameter changes in a chain, each through the hostless reviser: read with every
   binding check (including every object's geometry digest), the typed change, a full rebuild, the write;
3. read each result again in a new process (the cold read).

The two arms alternate revision by revision, so drift in the machine falls on both. Recorded per revision: the
state's bytes, the file's bytes, the bytes rewritten, and the phases (read and check, patch and closure, kernel,
write, cold read). Per N and format, C-full and D are compared by a two one-sided Welch test on the total time with
an equivalence margin of 5 % of D's mean.
"""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean, median
from typing import Any

from archflow.state.state_record import Parameter, StateRecord

from labs.revision_tasks import carrier, reviser
from labs.revision_tasks.fixtures import _component, _element, _level, _record

CHECKOUT = _bootstrap.CHECKOUT
MARGIN = 0.05


def grid(n: int) -> StateRecord:
    """N posts of 0.5 x 0.5 m on a 2 m grid, each with its own height parameter."""

    side = int(n ** 0.5 + 0.999)
    entities = [_component("site", None, "whole-building", "a grid of posts"), _component("posts", "site", "vertical-support", "posts"),
                _level("level-0", "ground-floor", 0.0)]
    parameters = []
    for i in range(n):
        x, y = (i % side) * 2.0, (i // side) * 2.0
        parameters.append(Parameter(f"h_{i}", 3.0, "m", epistemic_status="declared"))
        entities.append(_element(f"post-{i}", "posts", "prism", {"base": {"level": "level-0"}},
                                 {"profile": [[x, y], [x + 0.5, y], [x + 0.5, y + 0.5], [x, y + 0.5]], "height": f"@h_{i}"}))
    return _record(f"grid-{n}", entities, tuple(parameters), ())


def state_bytes(path: Path, fmt: str, sidecar: Path | None) -> int:
    """The bytes the state and its binding take as compact JSON, wherever they are (the sidecar file itself is indented;
    its actual size counts in ``rewrite_bytes``)."""

    if sidecar is not None:
        carried = json.loads(sidecar.read_text(encoding="utf-8"))
        return len(json.dumps({k: carried.get(k) for k in ("state", "binding")}, separators=(",", ":")).encode("utf-8"))
    if fmt == "3dm":
        _, strings = carrier.three_dm_geometry(path)
        return sum(len(strings.get(k, "").encode("utf-8")) for k in ("archflow:state", "archflow:binding"))
    document, _ = carrier.glb_split(path.read_bytes())
    archflow = document.get("asset", {}).get("extras", {}).get("archflow", {})
    return len(json.dumps({k: archflow.get(k) for k in ("state", "binding")}, separators=(",", ":")).encode("utf-8"))


def cold_read(path: Path, fmt: str, sidecar: Path | None) -> float:
    """Seconds a new process takes to read and check the file (its own clock, not the process start)."""

    command = [sys.executable, "-m", "labs.revision_tasks.x7", "--cold-read", str(path), "--format", fmt]
    if sidecar is not None:
        command += ["--sidecar", str(sidecar)]
    done = subprocess.run(command, cwd=CHECKOUT, capture_output=True, text=True, check=True)
    return float(json.loads(done.stdout.strip().splitlines()[-1])["read_s"])


def tost(c: list[float], d: list[float], margin: float) -> dict[str, Any]:
    """Two one-sided Welch tests: is mean(C) - mean(D) within +/- margin * mean(D)?"""

    from scipy import stats

    delta = margin * mean(d)
    low = stats.ttest_ind([x + delta for x in c], d, equal_var=False, alternative="greater")
    high = stats.ttest_ind([x - delta for x in c], d, equal_var=False, alternative="less")
    p = max(float(low.pvalue), float(high.pvalue))
    return {"mean_c_s": mean(c), "mean_d_s": mean(d), "ratio": mean(c) / mean(d), "margin": margin, "p": p, "equivalent_at_0.05": p < 0.05}


FIELDS = ["n", "arm", "format", "revision", "claim", "codes", "total_s", "read_s", "patch_s", "kernel_s", "write_s", "cold_read_s",
          "state_bytes", "file_bytes", "rewrite_bytes", "file_sha256", "write_context_pack_s", "write_realize_s", "write_write_s"]


def _discard(folder: Path, stem: str) -> None:
    """Remove one superseded revision's files and work directory (``--keep-models`` only: a compute server's own output)."""

    import shutil

    for path in folder.glob(f"{stem}.*"):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def run(out: Path, *, sizes: tuple[int, ...], revisions: int, arms: tuple[str, ...], formats: tuple[str, ...],
        keep_models: int | None = None, min_free_gb: float | None = None) -> dict[str, Any]:
    """``keep_models``: keep the model files of the start and the first ``keep_models`` revisions of each chain and remove
    each later one once the next revision has read it (sizes, hashes and timings are recorded first); the last revision
    and every refused one are kept. ``min_free_gb``: stop before a revision when the output disk has less free space.

    Each row is appended to ``x7.csv`` as it is measured, so a run that stops or fails keeps what it measured. A refused
    revision is recorded (claim and reason codes) and its chain continues from the last written file."""

    rows: list[dict[str, Any]] = []
    box: dict[str, Any] = {"stopped": None}
    out.mkdir(parents=True, exist_ok=True)
    handle = (out / "x7.csv").open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=FIELDS, restval="", extrasaction="ignore")
    writer.writeheader()

    def record_row(row: dict[str, Any]) -> None:
        rows.append(row)
        writer.writerow(row)
        handle.flush()

    try:
        _chains(out, rows, record_row, box, sizes=sizes, revisions=revisions, arms=arms, formats=formats, keep_models=keep_models,
                min_free_gb=min_free_gb)
    except BaseException as exc:
        box["stopped"] = f"failed: {type(exc).__name__}: {str(exc)[:500]}"
        raise
    finally:
        handle.close()
        _summarize(out, rows, sizes=sizes, formats=formats, arms=arms, stopped=box["stopped"])
    return json.loads((out / "x7_summary.json").read_text(encoding="utf-8"))


def _chains(out: Path, rows: list[dict[str, Any]], record_row: Any, box: dict[str, Any], *, sizes: tuple[int, ...], revisions: int,
            arms: tuple[str, ...], formats: tuple[str, ...], keep_models: int | None, min_free_gb: float | None) -> None:
    import shutil

    stopped = None
    for n in sizes:
        if stopped:
            break
        record = grid(n)
        for fmt in formats:
            if stopped:
                break
            chains: dict[str, tuple[Path, Path | None, dict[str, float]]] = {}
            for arm in arms:
                folder = out / f"n{n}" / f"{arm}-{fmt}"
                folder.mkdir(parents=True)
                t = time.perf_counter()
                written = reviser.write_state_file(record, arm=arm, fmt=fmt, out_dir=folder, stem="r00", parent_sha256=None,
                                                   context_root=folder / "ctx", utterance="Scale run.")
                record_row({"n": n, "arm": arm, "format": fmt, "revision": 0, "claim": "start", "total_s": time.perf_counter() - t,
                             "state_bytes": state_bytes(written.path, fmt, written.sidecar), "file_bytes": written.path.stat().st_size,
                             "rewrite_bytes": written.path.stat().st_size + (written.sidecar.stat().st_size if written.sidecar else 0),
                             **{f"write_{k}": v for k, v in written.seconds.items()}})
                chains[arm] = (written.path, written.sidecar, {f"h_{k}": 3.0 for k in range(n)})
            refused: dict[str, set[int]] = {arm: set() for arm in arms}
            for i in range(1, revisions + 1):
                if min_free_gb is not None and shutil.disk_usage(out).free / 1e9 < min_free_gb:
                    stopped = box["stopped"] = f"free space below {min_free_gb} GB before n={n} {fmt} r{i:02d}"
                    break
                key = f"h_{(i * 7919) % n}"
                for arm in arms:
                    path, sidecar, values = chains[arm]
                    folder = path.parent
                    new = round(values[key] + 0.01 * i, 6)
                    delta = [{"target": f"parameter:{key}", "parameter": key, "from": values[key], "to": new}]
                    t = time.perf_counter()
                    revision = reviser.revise(path, fmt=fmt, arm=arm, deltas=delta, out_dir=folder, stem=f"r{i:02d}", context_root=folder / "ctx",
                                              utterance="Scale run.", sidecar=sidecar, scan=False)
                    total = time.perf_counter() - t
                    if revision.written is None:
                        refused[arm].add(i)
                        record_row({"n": n, "arm": arm, "format": fmt, "revision": i, "claim": revision.claim, "total_s": total,
                                    "codes": ";".join(str(r.get("code", "")) for r in revision.reasons)})
                        continue
                    written = revision.written
                    values[key] = new
                    chains[arm] = (written.path, written.sidecar, values)
                    previous = max((j for j in range(1, i) if j not in refused[arm]), default=0)
                    if keep_models is not None and previous > keep_models:
                        _discard(folder, f"r{previous:02d}")
                    seconds = revision.seconds
                    record_row({"n": n, "arm": arm, "format": fmt, "revision": i, "claim": revision.claim, "total_s": total,
                                 "read_s": seconds.get("read_s"), "patch_s": seconds.get("patch_s"),
                                 "kernel_s": seconds.get("write_realize_s"), "write_s": seconds.get("write_write_s"),
                                 "state_bytes": state_bytes(written.path, fmt, written.sidecar), "file_bytes": written.path.stat().st_size,
                                 "rewrite_bytes": written.path.stat().st_size + (written.sidecar.stat().st_size if written.sidecar else 0),
                                 "cold_read_s": cold_read(written.path, fmt, written.sidecar),
                                 "file_sha256": hashlib.sha256(written.path.read_bytes()).hexdigest()})
                print(f"n={n} {fmt} r{i:02d} " + " ".join(f"{r['arm']}={r['total_s']:.2f}s" for r in rows[-len(arms):]), flush=True)


def _summarize(out: Path, rows: list[dict[str, Any]], *, sizes: tuple[int, ...], formats: tuple[str, ...], arms: tuple[str, ...],
               stopped: str | None) -> None:
    """Medians per size, format and arm over the written revisions, refusals counted, and the TOST on total time."""

    summary: dict[str, Any] = {"stopped": stopped} if stopped else {}
    written = [r for r in rows if r["revision"] > 0 and r.get("file_sha256")]
    for n in sizes:
        for fmt in formats:
            cell: dict[str, Any] = {}
            if not any(r["n"] == n and r["format"] == fmt for r in written):
                continue
            for arm in arms:
                refusals = sum(1 for r in rows if r["n"] == n and r["format"] == fmt and r["arm"] == arm and r["revision"] > 0
                               and not r.get("file_sha256"))
                chain = [r for r in written if r["n"] == n and r["format"] == fmt and r["arm"] == arm]
                if not chain:
                    cell[arm] = {"refused": refusals}
                    continue
                cell[arm] = {k: median(r[k] for r in chain) for k in ("total_s", "read_s", "patch_s", "kernel_s", "write_s", "cold_read_s",
                                                                       "state_bytes", "file_bytes", "rewrite_bytes")}
                cell[arm]["refused"] = refusals
            if set(arms) >= {"C-full", "D"}:
                totals = {arm: [r["total_s"] for r in written if r["n"] == n and r["format"] == fmt and r["arm"] == arm]
                          for arm in ("C-full", "D")}
                if len(totals["C-full"]) > 1 and len(totals["D"]) > 1:
                    cell["tost_total"] = tost(totals["C-full"], totals["D"], MARGIN)
            summary[f"n{n}-{fmt}"] = cell
    (out / "x7_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scale chains into an empty directory.")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--sizes", default="20,500,5000")
    parser.add_argument("--revisions", type=int, default=30)
    parser.add_argument("--arms", default="C-full,D")
    parser.add_argument("--formats", default="3dm,glb")
    parser.add_argument("--keep-models", type=int, help="keep only the start and this many revisions' model files per chain")
    parser.add_argument("--min-free-gb", type=float, help="stop before a revision when the output disk has less free space")
    parser.add_argument("--cold-read", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--format", help=argparse.SUPPRESS)
    parser.add_argument("--sidecar", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.cold_read is not None:
        t = time.perf_counter()
        reviser.read_state_file(args.cold_read, fmt=args.format, sidecar=args.sidecar)
        print(json.dumps({"read_s": time.perf_counter() - t}))
        return 0
    if args.out is None:
        parser.error("--out is required")
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    summary = run(out, sizes=tuple(int(s) for s in args.sizes.split(",")), revisions=args.revisions,
                  arms=tuple(args.arms.split(",")), formats=tuple(args.formats.split(",")), keep_models=args.keep_models,
                  min_free_gb=args.min_free_gb)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
