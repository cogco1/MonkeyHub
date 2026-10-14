"""X2 batch 5's pre-registered analysis: three contrasts in four-category accuracy, and each arm's counts.

The contrasts are D - AG, D - S and (N+S) - N. A row is correct when its verdict's category (manifest 7.2: held |
blocked = violated or refuse | unknown | flag_rejected, ``x2_g.CATEGORY``) is one of its gold verdicts' categories; an
unparsed answer is not correct. An arm's accuracy is its correct rows over its rows. Each difference has a two-sided 95%
interval from a task-cluster bootstrap: the 25 tasks are drawn with replacement (10,000 resamples, seed 20261014,
the generator restarted for each contrast), both arms' rows of every drawn task are pooled, and the interval is the
2.5th and 97.5th percentiles of the resampled differences (linear interpolation between ranks).

D's rows come from the already-counted batch-3 CSVs (``--batch3``); AG, S, N and N+S from batch 5's (``--batch5``).
Per arm: correct of its rows, false accepts (``held`` where the gold does not hold or the oracle finds a violation, as
x2 scores it), and both by task type (``x1_tasks.json``).

``python -m labs.revision_tasks.x2_batch5_analysis --batch3 <x2.csv>... --batch5 <x2.csv>... --out <file.json>``
"""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any

from labs.revision_tasks import x1
from labs.revision_tasks.x2_g import CATEGORY

CONTRASTS = (("D", "AG"), ("D", "S"), ("N+S", "N"))
BATCH3_ARMS = ("D",)
BATCH5_ARMS = ("AG", "S", "N", "N+S")
RESAMPLES = 10_000
SEED = 20261014
EXPECTED_ROWS = 125          # 25 tasks x 5 repeats


def correct(row: dict[str, str]) -> bool:
    gold = {CATEGORY.get(v) for v in row["gold_verdicts"].split("|")}
    return row["verdict"] in CATEGORY and CATEGORY[row["verdict"]] in gold


def load(paths: list[Path], arms: tuple[str, ...]) -> list[dict[str, str]]:
    """The called rows of ``arms`` in the CSVs (a dry run's rows carry no score and are left out)."""

    rows = []
    for path in paths:
        with path.open(encoding="utf-8", newline="") as handle:
            rows.extend(r for r in csv.DictReader(handle) if r["arm"] in arms and r.get("verdict_ok") not in (None, ""))
    return rows


def counts(rows: list[dict[str, str]], types: dict[str, str]) -> dict[str, Any]:
    by_arm: dict[str, Any] = {}
    for row in rows:
        cell = by_arm.setdefault(row["arm"], {"rows": 0, "correct": 0, "false_accept": 0, "by_type": {}})
        kind = cell["by_type"].setdefault(types[row["task_id"]], {"rows": 0, "correct": 0, "false_accept": 0})
        for target in (cell, kind):
            target["rows"] += 1
            target["correct"] += int(correct(row))
            target["false_accept"] += int(row["false_accept"] == "True")
    for cell in by_arm.values():
        cell["complete"] = cell["rows"] == EXPECTED_ROWS
        cell["accuracy"] = cell["correct"] / cell["rows"]
        cell["by_type"] = dict(sorted(cell["by_type"].items()))
    return dict(sorted(by_arm.items()))


def _quantile(ordered: list[float], q: float) -> float:
    h = (len(ordered) - 1) * q
    low = int(h)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (h - low) * (ordered[high] - ordered[low])


def bootstrap(rows: list[dict[str, str]], first: str, second: str, *, resamples: int = RESAMPLES, seed: int = SEED) -> dict[str, Any]:
    """``first`` - ``second`` in four-category accuracy, with its task-cluster bootstrap interval."""

    cells: dict[str, dict[str, list[int]]] = {}
    for row in rows:
        if row["arm"] in (first, second):
            cell = cells.setdefault(row["task_id"], {first: [0, 0], second: [0, 0]})[row["arm"]]
            cell[0] += int(correct(row))
            cell[1] += 1
    tasks = sorted(t for t, c in cells.items() if c[first][1] and c[second][1])

    def difference(sample: list[str]) -> float:
        a = [sum(cells[t][first][i] for t in sample) for i in (0, 1)]
        b = [sum(cells[t][second][i] for t in sample) for i in (0, 1)]
        return a[0] / a[1] - b[0] / b[1]

    rng = random.Random(seed)
    resampled = sorted(difference(rng.choices(tasks, k=len(tasks))) for _ in range(resamples))
    return {"contrast": f"{first} - {second}", "difference": difference(tasks), "ci95": [_quantile(resampled, 0.025), _quantile(resampled, 0.975)],
            "tasks": len(tasks), "tasks_dropped": sorted(set(cells) - set(tasks)), "resamples": resamples, "seed": seed}


def analyse(batch3: list[Path], batch5: list[Path]) -> dict[str, Any]:
    types = {t["task_id"]: t["type"] for t in json.loads(x1.TASKS.read_text(encoding="utf-8"))["tasks"]}
    rows = load(batch3, BATCH3_ARMS) + load(batch5, BATCH5_ARMS)
    return {"arms": counts(rows, types), "contrasts": [bootstrap(rows, a, b) for a, b in CONTRASTS],
            "sources": {"batch3": [str(p) for p in batch3], "batch5": [str(p) for p in batch5]}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="X2 batch 5's pre-registered contrasts and counts.")
    parser.add_argument("--batch3", type=Path, nargs="+", required=True, help="the counted batch-3 x2.csv files (D's rows)")
    parser.add_argument("--batch5", type=Path, nargs="+", required=True, help="batch 5's x2.csv files (AG, S, N, N+S)")
    parser.add_argument("--out", type=Path, required=True, help="the JSON file to write")
    args = parser.parse_args(argv)
    result = analyse(args.batch3, args.batch5)
    args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    for name, cell in result["arms"].items():
        print(f"{name:4} correct {cell['correct']:3}/{cell['rows']:3}  false accepts {cell['false_accept']:3}")
    for c in result["contrasts"]:
        print(f"{c['contrast']:9} {c['difference']:+.3f}  95% [{c['ci95'][0]:+.3f}, {c['ci95'][1]:+.3f}]  ({c['tasks']} tasks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
