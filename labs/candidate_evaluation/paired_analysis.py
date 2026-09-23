"""Read-only paired comparisons of complete, retained allocation experiments.

python -m labs.candidate_evaluation.paired_analysis --input <run-dir> --output <fresh-dir>
No sampling is performed. Incomplete inputs are rejected rather than paired by order.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
from random import Random
from statistics import fmean

from .allocation_benchmark import aggregate, stream_seed


class InputIntegrityError(ValueError):
    """The retained inputs cannot establish a complete matched experiment."""


CONDITION = ("fixture", "budget_unit", "budget", "policy")
VERSIONS = {"format": "allocation-benchmark-v1", "sampler_version": "controlled-iid-v1",
            "allocator_version": "sequential-ocba-v2"}


def condition(row: dict) -> tuple:
    return tuple(row[key] for key in CONDITION)


def _same_number(actual, expected) -> bool:
    if actual is None or expected is None:
        return actual is expected
    if isinstance(actual, (tuple, list)) and isinstance(expected, (tuple, list)):
        return len(actual) == len(expected) and all(_same_number(a, b) for a, b in zip(actual, expected))
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-12)
    return actual == expected


def _parsed(value: str):
    if value == "":
        return None
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def read_experiment(directory: Path, *, reference_csv: Path | None = None) -> tuple[list[dict], dict]:
    """Validate gzip EOF, unique trials, complete cells, seeds, versions and aggregates.

    Without the manifest an exact reference CSV is required. Such an association
    never establishes the execution source revision or sampler version on its own.
    """
    raw = directory / "trials.jsonl.gz"
    manifest_path = directory / "summary.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
        if manifest:
            for field, version in VERSIONS.items():
                if manifest.get(field) != version:
                    raise InputIntegrityError(f"unsupported manifest {field}: {manifest.get(field)!r}")
            if not manifest.get("code_revision"):
                raise InputIntegrityError("manifest source revision is missing")
        elif reference_csv is None:
            raise InputIntegrityError("manifest missing: an exact reference CSV is required")
        rows = []
        seen = set()
        groups = {}
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                row = json.loads(line)
                key = (*condition(row), row["repetition"])
                if key in seen:
                    raise InputIntegrityError(f"duplicate trial at line {line_number}: {key}")
                seen.add(key)
                seeds = row["candidate_seeds"]
                if not isinstance(seeds, list) or not seeds or any(type(seed) is not int for seed in seeds):
                    raise InputIntegrityError(f"missing or invalid candidate seeds: {key}")
                if len(set(seeds)) != len(seeds):
                    raise InputIntegrityError(f"candidate streams are not distinct: {key}")
                if type(row["repetition"]) is not int or type(row["master_seed"]) is not int:
                    raise InputIntegrityError(f"invalid trial/master seed: {key}")
                if seeds != [stream_seed(row["master_seed"], row["fixture"], row["repetition"], i)
                             for i in range(len(seeds))]:
                    raise InputIntegrityError(f"candidate seeds contradict declared stream inputs: {key}")
                if len(seeds) != len(row["final_estimates"]) or len(seeds) != len(row["attempts_by_candidate"]):
                    raise InputIntegrityError(f"candidate count/seed count mismatch: {key}")
                if row["budget_unit"] not in {"samples", "cost"}:
                    raise InputIntegrityError(f"unsupported budget unit: {key}")
                if type(row["correct_selection"]) is not bool:
                    raise InputIntegrityError(f"correct_selection is not boolean: {key}")
                selected = row["selected_candidate"]
                if row["correct_selection"] != (selected is not None and selected in row["true_best_candidates"]):
                    raise InputIntegrityError(f"selection outcome contradicts retained best identities: {key}")
                if (selected is None) != (row["simple_regret"] is None):
                    raise InputIntegrityError(f"unselected trial has fabricated regret: {key}")
                if row["simple_regret"] is not None and (not math.isfinite(row["simple_regret"]) or row["simple_regret"] < 0):
                    raise InputIntegrityError(f"invalid simple regret: {key}")
                if row["attempts"] != row["successful_samples"] + row["failures"]:
                    raise InputIntegrityError(f"failed attempts were not accounted for: {key}")
                if len(row["trace"]) != row["attempts"] or sum(row["attempts_by_candidate"]) != row["attempts"]:
                    raise InputIntegrityError(f"attempt trace/count mismatch: {key}")
                cost = row["normalized_compute_cost"]
                if not math.isfinite(cost) or cost < 0 or row["remaining_budget"] < 0:
                    raise InputIntegrityError(f"invalid charged cost: {key}")
                charged = row["attempts"] if row["budget_unit"] == "samples" else cost
                if not _same_number(charged + row["remaining_budget"], row["budget"]):
                    raise InputIntegrityError(f"budget does not reconcile: {key}")
                if manifest:
                    if row["master_seed"] != manifest["master_seed"] or row["warmup"] != manifest["warmup_successes"]:
                        raise InputIntegrityError(f"trial/manifest seed or warmup mismatch: {key}")
                    for field, version in VERSIONS.items():
                        if field in row and row[field] != version:
                            raise InputIntegrityError(f"trial/manifest version mismatch: {key}")
                row = {key: value for key, value in row.items() if key not in {"trace", "final_estimates"}}
                rows.append(row)
                groups.setdefault(condition(row), []).append(row)
        if not rows:
            raise InputIntegrityError("raw experiment is empty")
        if len({(row["master_seed"], row["warmup"]) for row in rows}) != 1:
            raise InputIntegrityError("one retained experiment must use one master seed and warmup setting")
        reference = None
        if reference_csv:
            with reference_csv.open(encoding="utf-8-sig", newline="") as stream:
                reference = [{key: _parsed(value) for key, value in row.items()} for row in csv.DictReader(stream)]
        expected = manifest["results"] if manifest else reference
        expected_map = {condition(row): row for row in expected}
        if len(expected_map) != len(expected) or set(groups) != set(expected_map):
            raise InputIntegrityError("missing, extra or duplicate conditions relative to retained aggregate")
        for key, values in groups.items():
            count = expected_map[key]["repetitions"]
            if {row["repetition"] for row in values} != set(range(count)):
                raise InputIntegrityError(f"missing or unexpected repetition indices: {key}")
            if manifest and count != manifest["repetitions"]:
                raise InputIntegrityError(f"condition/manifest repetition count mismatch: {key}")
            if len({(row["master_seed"], row["warmup"]) for row in values}) != 1:
                raise InputIntegrityError(f"mixed seed or warmup parameters in condition: {key}")
            actual = aggregate(values)
            for field, value in expected_map[key].items():
                if field not in actual or not _same_number(actual[field], value):
                    raise InputIntegrityError(f"raw/aggregate mismatch: {key}, {field}")
        if manifest and reference is not None:
            refmap = {condition(row): row for row in reference}
            if len(refmap) != len(reference) or set(refmap) != set(groups):
                raise InputIntegrityError("reference CSV condition set mismatch")
            for key in groups:
                for field, value in refmap[key].items():
                    if not _same_number(expected_map[key].get(field), value):
                        raise InputIntegrityError(f"manifest/reference CSV mismatch: {key}, {field}")
        with raw.open("rb") as stream:
            raw_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        metadata = {"raw_sha256": raw_hash, "raw_bytes": raw.stat().st_size,
                    "trial_count": len(rows), "condition_count": len(groups),
                    "manifest_status": "present" if manifest else "missing; aggregate association only",
                    "source_revision": manifest["code_revision"] if manifest else None,
                    "sampler_version": manifest["sampler_version"] if manifest else None,
                    "allocator_version": manifest["allocator_version"] if manifest else None,
                    "source_lab_dirty": manifest.get("lab_has_uncommitted_changes") if manifest else None}
        return rows, metadata
    except (OSError, EOFError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InputIntegrityError(f"incomplete or invalid retained experiment: {type(exc).__name__}: {exc}") from exc


def _binomial_cdf(k: int, n: int, p: float) -> float:
    if p == 0:
        return 1.
    if p == 1:
        return float(k >= n)
    return min(1., math.fsum(math.exp(math.lgamma(n + 1) - math.lgamma(j + 1)
                                     - math.lgamma(n - j + 1) + j * math.log(p)
                                     + (n - j) * math.log1p(-p)) for j in range(k + 1)))


def clopper_pearson(successes: int, total: int, alpha: float = .05) -> tuple[float, float]:
    """Exact binomial interval via inversion, with no optional statistics library."""
    if type(total) is not int or type(successes) is not int or not 0 <= successes <= total or total < 1 or not 0 < alpha < 1:
        raise ValueError("valid counts and alpha in (0,1) required")
    def solve(k, target):
        low, high = 0., 1.
        for _ in range(55):
            mid = (low + high) / 2
            if _binomial_cdf(k, total, mid) > target:
                low = mid
            else:
                high = mid
        return (low + high) / 2
    low = 0. if successes == 0 else solve(successes - 1, 1 - alpha / 2)
    high = 1. if successes == total else solve(successes, alpha / 2)
    return low, high


def paired_pcs_interval(wins: int, losses: int, total: int, alpha: float = .05) -> tuple[float, float]:
    """Conservative exact paired interval, including zero-discordance data.

    Bonferroni covers P(A correct,B wrong) and P(A wrong,B correct) jointly;
    subtracting their Clopper-Pearson intervals covers the PCS difference.
    No independence between these two discordance categories is assumed.
    """
    if wins < 0 or losses < 0 or wins + losses > total:
        raise ValueError("discordance counts must fit paired total")
    win = clopper_pearson(wins, total, alpha / 2)
    loss = clopper_pearson(losses, total, alpha / 2)
    return win[0] - loss[1], win[1] - loss[0]


def bootstrap_mean_interval(values: list[float], *, seed: int, draws: int) -> tuple[float | None, float | None, str]:
    if len(values) < 2:
        return None, None, "unavailable: fewer than two jointly selected trials"
    if min(values) == max(values):
        return None, None, "degenerate: all observed paired differences equal; no precision claim"
    rng = Random(seed)
    samples = sorted(fmean(rng.choices(values, k=len(values))) for _ in range(draws))
    return samples[int(.025 * (draws - 1))], samples[int(.975 * (draws - 1))], "paired-trial percentile bootstrap; approximate, pointwise"


def compare(rows: list[dict], *, bootstrap_draws: int = 10000) -> list[dict]:
    if bootstrap_draws < 200:
        raise ValueError("at least 200 bootstrap draws required")
    cells = {}
    for row in rows:
        key = condition(row)
        rep = row["repetition"]
        if rep in cells.setdefault(key, {}):
            raise InputIntegrityError(f"duplicate paired trial: {key}, {rep}")
        cells[key][rep] = row
    comparisons = [key for key in sorted(cells) if key[-1] in {"ocba", "cost_ocba"}]
    results = []
    for key in comparisons:
        base_key = (*key[:-1], "equal")
        if base_key not in cells or cells[base_key].keys() != cells[key].keys():
            raise InputIntegrityError(f"equal baseline or matched repetitions missing: {key}")
        pairs = [(cells[key][rep], cells[base_key][rep]) for rep in sorted(cells[key])]
        for a, b in pairs:
            for field in ("master_seed", "warmup", "candidate_seeds", "true_best_candidates"):
                if a[field] != b[field]:
                    raise InputIntegrityError(f"paired {field} mismatch: {key}, {a['repetition']}")
        n = len(pairs)
        wins = sum(a["correct_selection"] and not b["correct_selection"] for a, b in pairs)
        losses = sum(b["correct_selection"] and not a["correct_selection"] for a, b in pairs)
        both_correct = sum(a["correct_selection"] and b["correct_selection"] for a, b in pairs)
        both_wrong = n - wins - losses - both_correct
        low, high = paired_pcs_interval(wins, losses, n)
        family_low, family_high = paired_pcs_interval(wins, losses, n, .05 / len(comparisons))
        regret = [a["simple_regret"] - b["simple_regret"] for a, b in pairs
                  if a["simple_regret"] is not None and b["simple_regret"] is not None]
        jointly_selected = [(a, b) for a, b in pairs
                            if a["simple_regret"] is not None and b["simple_regret"] is not None]
        stable_seed = int.from_bytes(hashlib.sha256(repr(key).encode()).digest()[:8], "big")
        regret_low, regret_high, regret_status = bootstrap_mean_interval(regret, seed=stable_seed, draws=bootstrap_draws)
        result = {**dict(zip(CONDITION, key)), "baseline": "equal", "paired_trials": n,
                  "master_seed": pairs[0][0]["master_seed"], "warmup": pairs[0][0]["warmup"],
                  "policy_pcs": (wins + both_correct) / n, "equal_pcs": (losses + both_correct) / n,
                  "delta_pcs": (wins - losses) / n, "delta_pcs_ci95_low": low, "delta_pcs_ci95_high": high,
                  "delta_pcs_familywise95_low": family_low, "delta_pcs_familywise95_high": family_high,
                  "pcs_comparison_family_size": len(comparisons), "policy_only_correct": wins,
                  "equal_only_correct": losses, "both_correct": both_correct, "both_wrong": both_wrong,
                  "zero_discordance": wins + losses == 0,
                  "policy_unselected": sum(a["selected_candidate"] is None for a, b in pairs),
                  "equal_unselected": sum(b["selected_candidate"] is None for a, b in pairs),
                  "regret_jointly_selected_trials": len(regret),
                  "mean_policy_regret_on_jointly_selected": fmean(a["simple_regret"] for a, b in jointly_selected) if regret else None,
                  "mean_equal_regret_on_jointly_selected": fmean(b["simple_regret"] for a, b in jointly_selected) if regret else None,
                  "mean_delta_regret": fmean(regret) if regret else None,
                  "delta_regret_ci95_low": regret_low, "delta_regret_ci95_high": regret_high,
                  "regret_interval_status": regret_status}
        for field in ("attempts", "successful_samples", "failures", "normalized_compute_cost", "remaining_budget"):
            result[f"mean_policy_{field}"] = fmean(a[field] for a, b in pairs)
            result[f"mean_equal_{field}"] = fmean(b[field] for a, b in pairs)
            result[f"mean_delta_{field}"] = fmean(a[field] - b[field] for a, b in pairs)
        results.append(result)
    if not results:
        raise InputIntegrityError("no OCBA policy with an equal-allocation baseline")
    return results


def analyze(source: Path, output: Path, *, reference_csv: Path | None = None, bootstrap_draws: int = 10000) -> dict:
    rows, metadata = read_experiment(source, reference_csv=reference_csv)
    results = compare(rows, bootstrap_draws=bootstrap_draws)
    output = output.resolve()
    if output == source.resolve() or source.resolve() in output.parents:
        raise ValueError("analysis output must be outside the source experiment")
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("paired_results.csv", "paired_summary.json")):
        raise FileExistsError("choose a fresh analysis output directory")
    summary = {"input": metadata, "bootstrap_draws": bootstrap_draws,
               "pcs_interval": "Bonferroni difference of two exact Clopper-Pearson discordance intervals",
               "multiplicity": "simultaneous PCS intervals across all reported contrasts; regret intervals pointwise only",
               "interpretation": "exploratory comparison, not a finite-budget allocator guarantee or a universal winner",
               "results": results}
    with (output / "paired_results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(results)
    (output / "paired_summary.json").write_text(json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-csv", type=Path)
    parser.add_argument("--bootstrap-draws", type=int, default=10000)
    args = parser.parse_args()
    try:
        summary = analyze(args.input, args.output, reference_csv=args.reference_csv, bootstrap_draws=args.bootstrap_draws)
    except InputIntegrityError as exc:
        parser.exit(2, f"unavailable: {exc}\n")
    print(json.dumps({"comparisons": len(summary["results"]), "input": summary["input"]}))


if __name__ == "__main__":
    main()
