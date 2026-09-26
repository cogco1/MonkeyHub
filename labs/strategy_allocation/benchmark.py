"""Run, summarize and verify a sequential strategy-allocation experiment.

For each case: fork from the case's exact snapshot, let the allocation rule
choose the next batch of rollouts from the retained outcomes, run that batch
in a seeded shuffled order, record every rollout, and repeat until the budget
is spent. Initialization is the rule's own warmup, so allocation is sequential
from the first round. Every output goes to an explicit new directory.

    python -m labs.strategy_allocation.benchmark run --runner fake --out <new-dir>
    python -m labs.strategy_allocation.benchmark run --runner codex --out <new-dir> \\
        --cases provided-source protected-dependency --budget 8 --max-provider-calls 16
    python -m labs.strategy_allocation.benchmark verify <run-dir>
    python -m labs.strategy_allocation.benchmark rescore <run-dir> --out <new-dir>
    python -m labs.strategy_allocation.benchmark snapshots --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Callable, Mapping, Sequence

from labs.candidate_evaluation.allocation import VERSION as SEQUENTIAL_ALLOCATOR_VERSION

from .allocator import AllocationState, NextAllocation, StrategyStatistics, make_rule
from .environment import ENVIRONMENT_VERSION, Environment, Step
from .evaluator import EVALUATOR_VERSION, OUTCOME_METRICS, REFERENCE_VERSION, evaluate
from .rollout import (
    RECORD_FORMAT,
    RolloutRecord,
    append_jsonl,
    read_jsonl,
    run_rollout,
    verdict,
    write_csv,
    write_jsonl,
)
from .state_snapshot import StateSnapshot
from .strategies import PROMPT_CONTRACT, STRATEGIES, CodexRunner, Runner, demo_fake_runner


FIXTURES = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_id: str
    cases: tuple[str, ...]
    strategies: tuple[str, ...]
    rule: str = "equal"
    budget_per_case: int = 8
    warmup: int = 2
    parallel_capacity: int = 4
    horizon: int = 3
    token_cap: int = 4000
    timeout_s: float = 180.0
    seed: int = 0
    outcome_metric: str = "success"

    def __post_init__(self) -> None:
        if not self.cases or len(set(self.cases)) != len(self.cases):
            raise ValueError("cases must be present and unique")
        if not self.strategies or len(set(self.strategies)) != len(self.strategies):
            raise ValueError("strategies must be present and unique")
        unknown = set(self.strategies) - set(STRATEGIES)
        if unknown:
            raise ValueError(f"unknown strategies {sorted(unknown)}")
        if self.outcome_metric not in OUTCOME_METRICS:
            raise ValueError(f"unknown outcome metric {self.outcome_metric!r}")
        for name in ("budget_per_case", "warmup", "parallel_capacity", "horizon", "token_cap"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not self.timeout_s > 0:
            raise ValueError("timeout_s must be positive")

    @property
    def planned_rollouts(self) -> int:
        return len(self.cases) * self.budget_per_case

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "cases": list(self.cases), "strategies": list(self.strategies)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExperimentConfig:
        return cls(**{**value, "cases": tuple(value["cases"]), "strategies": tuple(value["strategies"])})


def rollout_seed(seed: int, case_id: str, strategy_id: str, index: int) -> int:
    digest = hashlib.sha256(f"{seed}:{case_id}:{strategy_id}:{index}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def allocation_state(config: ExperimentConfig, records: Sequence[RolloutRecord], round_index: int) -> AllocationState:
    """Reproducible statistics of the retained outcomes of one case, and its remaining budget."""
    metric = OUTCOME_METRICS[config.outcome_metric]
    outcomes = {strategy_id: [] for strategy_id in config.strategies}
    for record in records:
        outcomes[record.strategy_id].append(metric(record.outcome) if record.status == "ok" else 0.0)
    return AllocationState(
        strategies=tuple(StrategyStatistics.from_outcomes(strategy_id, outcomes[strategy_id])
                         for strategy_id in config.strategies),
        remaining_budget=config.budget_per_case - len(records),
        parallel_capacity=config.parallel_capacity,
        attempts_so_far=len(records),
        round_index=round_index,
        warmup=config.warmup,
        seed=config.seed,
    )


def _step_record(case_id: str, state: AllocationState, decision: NextAllocation, order: Sequence[str]) -> dict[str, Any]:
    return {
        "caseId": case_id,
        "round": state.round_index,
        "remainingBudget": state.remaining_budget,
        "attemptsSoFar": state.attempts_so_far,
        "statistics": [asdict(item) for item in state.strategies],
        "allocations": decision.as_dict(),
        "order": list(order),
        "stoppingReason": decision.stopping_reason,
        "diagnostics": list(decision.diagnostics),
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
                    newline="\n")


def run_experiment(config: ExperimentConfig, *, runner: Runner, out_dir: Path, env: Environment | None = None,
                   raw: bool = False, log: Callable[[str], None] = print) -> list[RolloutRecord]:
    """Run every case to its budget and retain everything in ``out_dir`` (which must be new or empty)."""
    env = env or Environment()
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty; results are never overwritten or silently resumed")
    rule = make_rule(config.rule)
    snapshots = {case_id: env.reset(case_id) for case_id in config.cases}
    (out_dir / "snapshots").mkdir()
    for case_id, snapshot in snapshots.items():
        (out_dir / "snapshots" / f"{case_id}.json").write_text(snapshot.to_json(), encoding="utf-8", newline="\n")
    _write_json(out_dir / "config.json", {
        "config": config.to_dict(),
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runner": runner.describe(),
        "versions": {"environment": ENVIRONMENT_VERSION, "evaluator": EVALUATOR_VERSION,
                     "reference": REFERENCE_VERSION, "promptContract": PROMPT_CONTRACT, "recordFormat": RECORD_FORMAT,
                     "sequentialAllocator": SEQUENTIAL_ALLOCATOR_VERSION},
        "strategyVersions": {strategy_id: STRATEGIES[strategy_id].version for strategy_id in config.strategies},
        "snapshots": {case_id: {"snapshotId": snapshot.snapshot_id, "digest": snapshot.digest}
                      for case_id, snapshot in snapshots.items()},
        "plannedRollouts": config.planned_rollouts,
    })
    records: list[RolloutRecord] = []
    for case_id, snapshot in snapshots.items():
        case_records: list[RolloutRecord] = []
        round_index = 0
        while True:
            state = allocation_state(config, case_records, round_index)
            decision = rule.next_allocation(state)
            batch = [strategy_id for strategy_id, count in decision.allocations for _ in range(count)]
            random.Random(f"order:{config.seed}:{case_id}:{round_index}").shuffle(batch)
            with (out_dir / "allocation.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(_step_record(case_id, state, decision, batch), sort_keys=True) + "\n")
            if not batch:
                log(f"{case_id}: stopped ({decision.stopping_reason}) after {len(case_records)} rollouts")
                break
            for strategy_id in batch:
                index = sum(record.strategy_id == strategy_id for record in case_records)
                rollout_id = f"{case_id}:{strategy_id}:{index:03d}"
                record = run_rollout(
                    env=env, snapshot=snapshot, strategy=STRATEGIES[strategy_id], runner=runner,
                    horizon=config.horizon, token_cap=config.token_cap, timeout_s=config.timeout_s,
                    seed=rollout_seed(config.seed, case_id, strategy_id, index),
                    experiment_id=config.experiment_id, rollout_id=rollout_id, allocation_rule=config.rule,
                    round_index=round_index, order_index=len(records),
                    raw_dir=out_dir / "raw" / rollout_id.replace(":", "_") if raw else None)
                append_jsonl(out_dir / "rollouts.jsonl", record)
                case_records.append(record)
                records.append(record)
                tokens = record.resources["tokens"]
                log(f"[{len(records)}/{config.planned_rollouts}] {rollout_id} {record.status} "
                    f"success={record.success} actions={'>'.join(record.chosen_actions) or '-'} "
                    f"output_tokens={tokens['output_tokens']} wall={record.resources['wallSeconds']}s")
            round_index += 1
    write_csv(out_dir / "rollouts.csv", records)
    _write_json(out_dir / "summary.json", summarize(config, records, _read_steps(out_dir)))
    return records


def _read_steps(out_dir: Path) -> list[dict[str, Any]]:
    path = out_dir / "allocation.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _mean(values: Sequence[float | int | None]) -> float | None:
    known = [value for value in values if value is not None]
    return round(fmean(known), 4) if known else None


def summarize(config: ExperimentConfig, records: Sequence[RolloutRecord],
              steps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per case and strategy: the outcome statistics the allocator saw, and the resources beside them."""
    cases = []
    for case_id in config.cases:
        case_records = [record for record in records if record.case_id == case_id]
        final = allocation_state(config, case_records, 0)
        arms = []
        for stats in final.strategies:
            mine = [record for record in case_records if record.strategy_id == stats.strategy_id]
            tokens = [record.resources["tokens"] for record in mine]
            arms.append({
                "strategyId": stats.strategy_id,
                "n": stats.n,
                "mean": stats.sample_mean,
                "variance": stats.sample_variance,
                "successes": sum(record.success for record in mine),
                "statuses": dict(sorted(Counter(record.status for record in mine).items())),
                "failureReasons": dict(sorted(Counter(record.failure_reason or "none" for record in mine).items())),
                "firstActionClasses": dict(sorted(Counter(record.evaluation["first_action_class"] or "none"
                                                          for record in mine).items())),
                "meanExcessSteps": _mean([record.evaluation["excess_steps"] for record in mine]),
                "plans": dict(sorted(Counter(">".join(record.chosen_actions) or "-" for record in mine).items())),
                "promptSha256": sorted({record.prompt_sha256 for record in mine}),
                "resources": {
                    "meanInputTokens": _mean([item["input_tokens"] for item in tokens]),
                    "meanCachedInputTokens": _mean([item["cached_input_tokens"] for item in tokens]),
                    "meanOutputTokens": _mean([item["output_tokens"] for item in tokens]),
                    "meanReasoningOutputTokens": _mean([item["reasoning_output_tokens"] for item in tokens]),
                    "meanWallSeconds": _mean([record.resources["wallSeconds"] for record in mine]),
                    "providerToolCalls": sum(record.resources["providerToolCalls"] or 0 for record in mine),
                    "humanInterruptions": sum(record.resources["humanInterruptions"] for record in mine),
                },
            })
        cases.append({
            "caseId": case_id,
            "snapshotDigest": sorted({record.snapshot_digest for record in case_records}),
            "rollouts": len(case_records),
            "allocationByRound": [step["allocations"] for step in steps if step["caseId"] == case_id],
            "stoppingReason": next((step["stoppingReason"] for step in steps
                                    if step["caseId"] == case_id and step["stoppingReason"]), None),
            "arms": arms,
        })
    sessions = [record.session["sessionId"] for record in records]
    return {
        "experimentId": config.experiment_id,
        "rule": config.rule,
        "outcomeMetric": config.outcome_metric,
        "rollouts": len(records),
        "providerCalls": sum(record.resources["providerCalls"] for record in records),
        "distinctSessions": len(set(sessions) - {None}),
        "missingSessionIds": sum(session is None for session in sessions),
        "cases": cases,
    }


def verify(out_dir: Path, env: Environment | None = None) -> list[str]:
    """Cold-read a run directory and check that it reproduces from its exact snapshots.

    It re-derives each snapshot, replays and re-judges every rollout, replays
    every allocation decision from the retained outcomes before it, and
    recomputes the summary. An empty list means everything reproduced.
    """
    env = env or Environment()
    problems: list[str] = []
    meta = json.loads((out_dir / "config.json").read_text(encoding="utf-8"))
    config = ExperimentConfig.from_dict(meta["config"])
    records = read_jsonl(out_dir / "rollouts.jsonl")
    steps = _read_steps(out_dir)
    snapshots = {}
    for case_id in config.cases:
        snapshot = env.reset(case_id)
        saved = StateSnapshot.from_json((out_dir / "snapshots" / f"{case_id}.json").read_text(encoding="utf-8"))
        if saved != snapshot or meta["snapshots"][case_id]["digest"] != snapshot.digest:
            problems.append(f"{case_id}: the retained snapshot differs from the environment's")
        snapshots[case_id] = snapshot
    judges = Counter(_judge(record) for record in records)
    for judge, count in sorted(judges.items()):
        if judge != _CURRENT_JUDGE:
            problems.append(f"{count} rollouts were judged by {judge}, not {_CURRENT_JUDGE}; "
                            "rescore re-judges their retained trajectories")
    for record in records:
        snapshot = snapshots[record.case_id]
        if record.snapshot_digest != snapshot.digest:
            problems.append(f"{record.rollout_id}: bound to another snapshot")
            continue
        if _judge(record) != _CURRENT_JUDGE:
            continue
        recorded = [Step.from_dict(record.case_id, step) for step in record.trajectory]
        try:
            outcome = evaluate(env, snapshot, record.chosen_actions, recorded)
        except ValueError as exc:
            problems.append(f"{record.rollout_id}: {exc}")
            continue
        if outcome.to_dict() != record.evaluation:
            problems.append(f"{record.rollout_id}: the outcome does not re-evaluate to the retained one")
        if (record.success, record.failure_reason) != verdict(record.status, outcome):
            problems.append(f"{record.rollout_id}: success disagrees with its status and outcome")
    rule = make_rule(config.rule)
    for step in steps:
        before = [record for record in records if record.case_id == step["caseId"] and record.round_index < step["round"]]
        state = allocation_state(config, before, step["round"])
        decision = rule.next_allocation(state)
        if decision.as_dict() != step["allocations"] or decision.stopping_reason != step["stoppingReason"]:
            problems.append(f"{step['caseId']} round {step['round']}: the allocation does not replay")
        ran = Counter(record.strategy_id for record in records
                      if record.case_id == step["caseId"] and record.round_index == step["round"])
        if dict(ran) != step["allocations"]:
            problems.append(f"{step['caseId']} round {step['round']}: the rollouts run differ from the allocation")
    summary_path = out_dir / "summary.json"
    if summary_path.exists() and json.loads(summary_path.read_text(encoding="utf-8")) != summarize(config, records, steps):
        problems.append("summary.json does not recompute from the retained records")
    return problems


_CURRENT_JUDGE = f"{EVALUATOR_VERSION} / {REFERENCE_VERSION}"


def _judge(record: RolloutRecord) -> str:
    return f"{record.evaluation['evaluator_version']} / {record.evaluation['reference_version']}"


def rescore(run_dir: Path, out_dir: Path, env: Environment | None = None) -> list[RolloutRecord]:
    """Re-judge a run's retained trajectories with this code's evaluator, into a new directory.

    No model is called: the proposals and trajectories were retained, and they
    answered snapshots this environment still produces (a changed snapshot is
    refused). ``allocation.jsonl`` is copied as it happened. A count-based
    rule's history replays under the new outcomes; an adaptive rule's may not,
    and ``verify`` says so. The source directory is not modified.
    """
    env = env or Environment()
    meta = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    config = ExperimentConfig.from_dict(meta["config"])
    records = read_jsonl(run_dir / "rollouts.jsonl")
    snapshots = {case_id: env.reset(case_id) for case_id in config.cases}
    stale = [record.rollout_id for record in records if record.snapshot_digest != snapshots[record.case_id].digest]
    if stale:
        raise ValueError(f"{len(stale)} rollouts answered a snapshot this environment no longer produces")
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty; results are never overwritten")
    rescored = []
    for record in records:
        steps = [Step.from_dict(record.case_id, step) for step in record.trajectory]
        outcome = evaluate(env, snapshots[record.case_id], record.chosen_actions, steps)
        success, failure_reason = verdict(record.status, outcome)
        rescored.append(replace(record, evaluation=outcome.to_dict(), success=success, failure_reason=failure_reason))
    (out_dir / "snapshots").mkdir()
    for case_id, snapshot in snapshots.items():
        (out_dir / "snapshots" / f"{case_id}.json").write_text(snapshot.to_json(), encoding="utf-8", newline="\n")
    _write_json(out_dir / "config.json", {
        **meta,
        "versions": {**meta["versions"], "evaluator": EVALUATOR_VERSION, "reference": REFERENCE_VERSION},
        "rescore": {"fromExperiment": config.experiment_id, "judgedBefore": sorted({_judge(record) for record in records}),
                    "judgedNow": _CURRENT_JUDGE, "rescoredAt": datetime.now(timezone.utc).isoformat(timespec="seconds")},
    })
    shutil.copyfile(run_dir / "allocation.jsonl", out_dir / "allocation.jsonl")
    write_jsonl(out_dir / "rollouts.jsonl", rescored)
    write_csv(out_dir / "rollouts.csv", rescored)
    _write_json(out_dir / "summary.json", summarize(config, rescored, _read_steps(out_dir)))
    return rescored


def check_fixtures(env: Environment | None = None, directory: Path = FIXTURES) -> list[str]:
    """The committed snapshot fixtures must be exactly what the environment produces today."""
    env = env or Environment()
    problems = []
    for case_id in env.case_ids:
        path = directory / f"{case_id}.json"
        if not path.exists():
            problems.append(f"{path.name} is missing")
        elif path.read_text(encoding="utf-8") != env.reset(case_id).to_json():
            problems.append(f"{path.name} differs from the environment's snapshot")
    return problems


def _lower_priority() -> None:
    """Idle priority for this process; the runner's children start at idle priority too."""
    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x40)
    else:
        os.nice(10)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m labs.strategy_allocation.benchmark", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run an experiment into a new directory")
    run.add_argument("--out", type=Path, required=True, help="a new or empty directory outside the repository")
    run.add_argument("--runner", choices=("fake", "codex"), default="fake")
    run.add_argument("--cases", nargs="+", default=list(Environment().case_ids))
    run.add_argument("--strategies", nargs="+", default=list(STRATEGIES))
    run.add_argument("--rule", default="equal", choices=("equal", "round_robin", "ocba"))
    run.add_argument("--budget", type=int, default=8, help="rollouts per case, failures included")
    run.add_argument("--warmup", type=int, default=2, help="balanced initial rollouts per strategy")
    run.add_argument("--parallel", type=int, default=4, help="rollouts the rule schedules per round")
    run.add_argument("--horizon", type=int, default=3)
    run.add_argument("--token-cap", type=int, default=4000, help="output tokens (reasoning included) per rollout")
    run.add_argument("--timeout", type=float, default=180.0, help="seconds per rollout call")
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--metric", default="success", choices=sorted(OUTCOME_METRICS))
    run.add_argument("--experiment-id", default=None)
    run.add_argument("--model", default=None, help="codex model; default: the CLI's own default")
    run.add_argument("--reasoning-effort", default=None, help="codex reasoning effort; default: the CLI's own")
    run.add_argument("--max-provider-calls", type=int, default=16,
                     help="refuse a codex run that could make more calls than this")
    run.add_argument("--raw", action="store_true", help="keep each call's prompt, events, stderr and answer")
    run.add_argument("--low-priority", action="store_true", help="run this process and its children at idle priority")
    verify_command = commands.add_parser("verify", help="check that a run directory reproduces")
    verify_command.add_argument("run_dir", type=Path)
    summary_command = commands.add_parser("summarize", help="print a run's summary table")
    summary_command.add_argument("run_dir", type=Path)
    rescore_command = commands.add_parser("rescore", help="re-judge a run's retained trajectories, no model call")
    rescore_command.add_argument("run_dir", type=Path)
    rescore_command.add_argument("--out", type=Path, required=True, help="a new or empty directory")
    fixtures = commands.add_parser("snapshots", help="check or write the snapshot fixtures")
    fixtures.add_argument("--write", type=Path, default=None, help="write the snapshots into this directory")
    fixtures.add_argument("--check", action="store_true", help="compare the committed fixtures")
    args = parser.parse_args(argv)

    if args.command == "snapshots":
        env = Environment()
        if args.write:
            args.write.mkdir(parents=True, exist_ok=True)
            for case_id in env.case_ids:
                (args.write / f"{case_id}.json").write_text(env.reset(case_id).to_json(), encoding="utf-8",
                                                            newline="\n")
            print(f"wrote {len(env.case_ids)} snapshots to {args.write}")
            return 0
        problems = check_fixtures(env) if args.check else []
        for case_id in env.case_ids:
            snapshot = env.reset(case_id)
            print(f"{snapshot.snapshot_id}  {snapshot.digest}")
        for problem in problems:
            print(f"PROBLEM {problem}")
        return 1 if problems else 0
    if args.command == "rescore":
        records = rescore(args.run_dir, args.out)
        print(f"re-judged {len(records)} rollouts by {_CURRENT_JUDGE} into {args.out}")
        args.run_dir = args.out
    if args.command in ("verify", "rescore"):
        problems = verify(args.run_dir)
        for problem in problems:
            print(f"PROBLEM {problem}")
        print("reproduced" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    if args.command == "summarize":
        summary = json.loads((args.run_dir / "summary.json").read_text(encoding="utf-8"))
        print(f"{summary['experimentId']}: rule={summary['rule']} rollouts={summary['rollouts']} "
              f"sessions={summary['distinctSessions']}")
        for case in summary["cases"]:
            for arm in case["arms"]:
                resources = arm["resources"]
                print(f"{case['caseId']:22} {arm['strategyId']} n={arm['n']:2} mean={arm['mean']} "
                      f"var={arm['variance']} out_tokens={resources['meanOutputTokens']} "
                      f"wall={resources['meanWallSeconds']}s plans={arm['plans']}")
        return 0

    config = ExperimentConfig(
        experiment_id=args.experiment_id or args.out.name, cases=tuple(args.cases), strategies=tuple(args.strategies),
        rule=args.rule, budget_per_case=args.budget, warmup=args.warmup, parallel_capacity=args.parallel,
        horizon=args.horizon, token_cap=args.token_cap, timeout_s=args.timeout, seed=args.seed,
        outcome_metric=args.metric)
    if args.runner == "codex":
        if config.planned_rollouts > args.max_provider_calls:
            print(f"refused: {config.planned_rollouts} planned codex calls exceed --max-provider-calls "
                  f"{args.max_provider_calls}", file=sys.stderr)
            return 2
        runner: Runner = CodexRunner(model=args.model, reasoning_effort=args.reasoning_effort)
    else:
        runner = demo_fake_runner()
    if args.low_priority:
        _lower_priority()
    run_experiment(config, runner=runner, out_dir=args.out, raw=args.raw)
    problems = verify(args.out)
    print("verified: the run reproduces from its snapshots" if not problems else "\n".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
