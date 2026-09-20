"""Run the small GH-123 experiment: python -m labs.candidate_evaluation.benchmark.

Prints raw success/failure results and fixed inputs to stdout; writes no project data.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import platform
from statistics import fmean
import subprocess

from archflow.project.refs import ProjectVersionRef, RunRef
from archflow.state.state_record import Entity, StateRecord
from .evaluator import EvaluationRequest, MassingEvaluator, evaluate_timed, normalize, pareto_front
from .synthetic import SyntheticEvaluator


EVIDENCE = "fixture:massing-rectangles-v1"
CONTEXT = ("fixture:envelope-policy-v1",)
ENVELOPE = {"min": (0, 0, 0), "max": (19, 29, 19), "max_height_m": 12.0,
            "far": 2.0, "site_area_m2": 100.0}


def candidate(name: str, *, width: int = 4, depth: int = 3, floors: int = 2,
              storey_height: int = 3, x: int = 0) -> StateRecord:
    """Synthetic declared massing; no implied real site, building or design acceptance."""
    levels = tuple(Entity(f"floor-{i}", "MassingLevel@1",
                          {"base_y": i * storey_height, "height": storey_height}, basis_refs=(EVIDENCE,))
                   for i in range(floors))
    volume = Entity("block", "Volume@1", {
        "min": [x, 0, 0], "max": [x + width - 1, floors * storey_height - 1, depth - 1],
        "level_ids": [level.entity_id for level in levels],
    }, basis_refs=(EVIDENCE,))
    # An empty authored record gives the fixture base its existing content digest.
    base_record = StateRecord("evaluation-fixture", "base", (), evidence_refs=(EVIDENCE,))
    base = ProjectVersionRef(base_record.project_id, 0, base_record.digest)
    return StateRecord(base.project_id, name, (*levels, volume), evidence_refs=(EVIDENCE,)).bound_to(
        RunRef(base.project_id, name, base)
    )


def request_for(record: StateRecord) -> EvaluationRequest:
    return EvaluationRequest(record, record.run_ref, record.digest, CONTEXT)


def validity_cases() -> tuple[tuple[str, EvaluationRequest, MassingEvaluator], ...]:
    good = candidate("valid")
    missing = replace(good, entities=(), run_id="missing-massing")
    off_lattice = replace(good, run_id="off-lattice", entities=(
        *good.entities[:-1], replace(good.entities[-1], fields={
            **good.entities[-1].fields, "max": [3.5, 5, 2],
        }),
    ))
    evaluator = MassingEvaluator(ENVELOPE)
    return (
        ("valid", request_for(good), evaluator),
        ("site-violation", request_for(candidate("site-violation", x=18)), evaluator),
        ("height-violation", request_for(candidate("height-violation", storey_height=7)), evaluator),
        ("far-violation", request_for(candidate("far-violation", width=12, depth=10)), evaluator),
        ("stale-base", replace(request_for(good), expected_run=RunRef(good.project_id, good.run_id,
            ProjectVersionRef(good.project_id, 1, good.base.state_sha256))), evaluator),
        ("missing-massing", request_for(missing), evaluator),
        ("off-lattice", request_for(off_lattice), evaluator),
        ("missing-site-area", request_for(good), MassingEvaluator({key: value for key, value in ENVELOPE.items()
                                                                 if key != "site_area_m2"})),
    )


def multiobjective_candidates() -> tuple[StateRecord, ...]:
    # 12 valid variants: widths 2/3/4, one/two floors, storey heights 3/4.
    # A 3 m storey dominates its 4 m counterpart with the same width/floors.
    return tuple(candidate(f"w{width}-f{floors}-h{height}", width=width, floors=floors, storey_height=height)
                 for width in (2, 3, 4) for floors in (1, 2) for height in (3, 4))


def run_benchmark() -> dict[str, object]:
    started_at = datetime.now(timezone.utc).isoformat()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "labs/candidate_evaluation"],
                                capture_output=True, text=True, check=True).stdout.strip())
    validity = []
    for name, request, evaluator in validity_cases():
        run = evaluate_timed(evaluator, request)
        validity.append({"case": name, "input": request.record.to_dict(), **run.to_dict()})
    evaluator = MassingEvaluator(ENVELOPE)
    variants = []
    results = []
    bounds = {"footprint_m2": (0, 12), "gross_floor_area_m2": (0, 24),
              "floor_count": (0, 2), "height_m": (0, 8)}
    for record in multiobjective_candidates():
        run = evaluate_timed(evaluator, request_for(record))
        results.append(run.result)
        variants.append({"input": record.to_dict(), **run.to_dict(), "normalized": normalize(run.result, bounds)})
    noisy_request = request_for(candidate("synthetic-context"))
    noisy = []
    for seed in range(8):
        sampler = SyntheticEvaluator(mean=10.0, deviation=2.0, sample_count=2048, seed=seed)
        run = evaluate_timed(sampler, noisy_request)
        noisy.append({**run.to_dict(), "raw_samples": sampler.samples()})
    return {
        "experiment_format": "candidate-evaluation-benchmark-v1", "fixture_version": "massing-rectangles-v1",
        "code_revision": revision, "lab_has_uncommitted_changes": dirty, "python_version": platform.python_version(),
        "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        "validity_cases": validity, "variants": variants, "normalization_bounds": bounds,
        "pareto_candidates": [result.run.run_id for result in pareto_front(results)],
        "synthetic_input": noisy_request.record.to_dict(),
        "synthetic_reference": {"mean": 10.0, "population_variance": 4.0, "sample_count_per_seed": 2048},
        "synthetic_runs": noisy,
        "summary": {
            "validity_counts": {status: sum(row["result"]["validity"] == status for row in validity)
                                for status in ("valid", "invalid", "unavailable")},
            "variant_count": len(results), "pareto_count": len(pareto_front(results)),
            "massing_mean_seconds": fmean(row["elapsed_seconds"] for row in variants),
            "synthetic_mean_of_means": fmean(row["result"]["objectives"][0]["statistics"]["mean"] for row in noisy),
            "synthetic_mean_sample_variance": fmean(row["result"]["objectives"][0]["statistics"]["variance"] for row in noisy),
        },
        "limitations": ["Small correctness experiment, not architectural evaluation validity or algorithm superiority.",
                        "Inputs are synthetic declared voxel massings; CAD realization and physical response are untested.",
                        "No persistence/reopen, OCBA, FEA, preference model or product API integration is implemented."],
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), ensure_ascii=False, allow_nan=False, indent=2))
