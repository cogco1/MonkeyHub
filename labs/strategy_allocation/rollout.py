"""One rollout from one exact snapshot, and the standardized record it leaves (JSON lines and CSV).

A rollout is one fresh runner call plus the environment's execution of the
proposal that call yields. The record keeps the external outcome (from
``evaluator.py``) and the resources (tokens, wall clock, tool calls, human
interruptions) in separate fields. A timeout, a malformed answer or a
token-cap overrun is a record like any other: it is retained, it is an
unsuccessful outcome, and it is never replaced by a retry.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from monkeymonitor.usage import TokenUsage

from .environment import Environment
from .evaluator import Outcome, evaluate
from .state_snapshot import StateSnapshot
from .strategies import ProposalError, Runner, Strategy


RECORD_FORMAT = "strategy-allocation-rollout@1"
STATUSES = ("ok", "timeout", "provider_error", "malformed", "policy_violation", "token_cap_exceeded")


@dataclass(frozen=True)
class RolloutRecord:
    record_format: str
    experiment_id: str
    rollout_id: str
    case_id: str
    case_version: str
    environment_version: str
    snapshot_id: str
    snapshot_digest: str
    strategy_id: str
    strategy_version: str
    prompt_contract: str
    prompt_sha256: str
    provider: str
    provider_version: str
    model: str
    reported_model: str | None
    sampling: Mapping[str, Any]
    seed: int
    token_cap: int
    horizon: int
    timeout_s: float
    allocation_rule: str
    round_index: int
    order_index: int
    status: str
    detail: str | None
    proposal: Mapping[str, Any] | None
    chosen_actions: tuple[str, ...]
    plan_truncated: bool
    trajectory: tuple[Mapping[str, Any], ...]
    evaluation: Mapping[str, Any]
    success: bool
    failure_reason: str | None
    resources: Mapping[str, Any]
    session: Mapping[str, Any]
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["chosen_actions"] = list(self.chosen_actions)
        value["trajectory"] = [dict(step) for step in self.trajectory]
        return json.loads(json.dumps(value))  # a deep, JSON-only copy

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RolloutRecord:
        names = {item.name for item in fields(cls)}
        if set(value) != names:
            raise ValueError(f"a rollout record has exactly the fields {sorted(names)}")
        if value["record_format"] != RECORD_FORMAT:
            raise ValueError(f"unsupported record format {value['record_format']!r}")
        data = dict(value)
        data["chosen_actions"] = tuple(data["chosen_actions"])
        data["trajectory"] = tuple(data["trajectory"])
        return cls(**data)

    @property
    def outcome(self) -> Outcome:
        return Outcome.from_dict(self.evaluation)


def _tokens(usage: Mapping[str, int | None]) -> tuple[dict[str, int | None], bool]:
    """Monitor's convention: input includes cached input, output includes reasoning output."""
    written = usage.get("cache_write_input_tokens")
    try:
        tokens = TokenUsage(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                            cached_input_tokens=usage.get("cached_input_tokens"),
                            cache_write_input_tokens=written,
                            # zero writes make the one-hour subset zero; otherwise its split is unknown
                            cache_write_1h_input_tokens=0 if written == 0 else None,
                            reasoning_output_tokens=usage.get("reasoning_output_tokens"))
        return tokens.to_dict(), True
    except (TypeError, ValueError):
        return {name: usage.get(name) for name in TokenUsage().to_dict()}, False


def verdict(status: str, outcome: Outcome) -> tuple[bool, str | None]:
    """A rollout succeeds only when its call yielded a proposal and the outcome is a success."""
    if status == "ok" and outcome.success:
        return True, None
    return False, status if status != "ok" else outcome.failure_reasons[0]


def run_rollout(*, env: Environment, snapshot: StateSnapshot, strategy: Strategy, runner: Runner, horizon: int,
                token_cap: int, timeout_s: float, seed: int, experiment_id: str, rollout_id: str,
                allocation_rule: str = "manual", round_index: int = 0, order_index: int = 0,
                raw_dir: Path | None = None) -> RolloutRecord:
    """Fork one fresh session from ``snapshot``, execute its proposal and judge the outcome."""
    if type(token_cap) is not int or token_cap < 1:
        raise ValueError("token_cap must be a positive integer")
    initial = env.state_of(snapshot)  # refuses a snapshot this environment would not produce
    request = strategy.request(env, snapshot, horizon=horizon)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    described = runner.describe()
    result = runner.run(request, seed=seed, timeout_s=timeout_s, raw_dir=raw_dir)
    tokens, tokens_consistent = _tokens(result.usage)
    output = tokens["output_tokens"]
    cap_check = "unknown" if output is None else "exceeded" if output > token_cap else "within"
    status, detail, proposal = result.status, result.detail, None
    if status == "ok" and cap_check == "exceeded":
        status, detail = "token_cap_exceeded", f"{output} output tokens exceed the cap of {token_cap}"
    if status == "ok":
        try:
            proposal = strategy.proposal(request, result.answer)
        except ProposalError as exc:
            status, detail = exc.code, str(exc)
    if status not in STATUSES:
        raise ValueError(f"runner returned an unknown status {status!r}")
    trajectory = env.rollout(initial, proposal.plan if proposal else (), horizon=horizon)
    outcome = evaluate(env, snapshot, trajectory.executed, trajectory.steps)
    success, failure_reason = verdict(status, outcome)
    asks_human = {action.action_id for action in env.case(snapshot.case_id).actions if action.asks_human}
    return RolloutRecord(
        record_format=RECORD_FORMAT,
        experiment_id=experiment_id,
        rollout_id=rollout_id,
        case_id=snapshot.case_id,
        case_version=snapshot.case_version,
        environment_version=snapshot.environment_version,
        snapshot_id=snapshot.snapshot_id,
        snapshot_digest=snapshot.digest,
        strategy_id=request.strategy_id,
        strategy_version=request.strategy_version,
        prompt_contract=request.prompt_contract,
        prompt_sha256=request.prompt_sha256,
        provider=described["provider"],
        provider_version=described["providerVersion"],
        model=described["model"],
        reported_model=result.reported_model,
        sampling=dict(described.get("sampling", {})),
        seed=seed,
        token_cap=token_cap,
        horizon=horizon,
        timeout_s=float(timeout_s),
        allocation_rule=allocation_rule,
        round_index=round_index,
        order_index=order_index,
        status=status,
        detail=detail,
        proposal=proposal.to_dict() if proposal else None,
        chosen_actions=trajectory.executed,
        plan_truncated=trajectory.truncated,
        trajectory=tuple(step.to_dict() for step in trajectory.steps),
        evaluation=outcome.to_dict(),
        success=success,
        failure_reason=failure_reason,
        resources={
            "tokens": tokens,
            "tokensConsistent": tokens_consistent,
            "tokenCapCheck": cap_check,
            "wallSeconds": round(result.wall_seconds, 3),
            "providerCalls": 1,
            "providerToolCalls": result.provider_tool_calls,
            "environmentActions": len(trajectory.steps),
            "humanInterruptions": sum(step.legal and step.action in asks_human for step in trajectory.steps),
            "apiCostUsd": None,
        },
        session={"sessionId": result.session_id, "inheritsTranscript": False, "kind": described.get("session")},
        started_at=started_at,
    )


# --- JSON lines and CSV --------------------------------------------------------

def append_jsonl(path: Path, record: RolloutRecord) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def write_jsonl(path: Path, records: Iterable[RolloutRecord]) -> None:
    path.write_text("".join(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
                            for record in records), encoding="utf-8", newline="\n")


def read_jsonl(path: Path) -> list[RolloutRecord]:
    return [RolloutRecord.from_dict(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


CSV_COLUMNS = (
    "experiment_id", "rollout_id", "case_id", "snapshot_digest", "strategy_id", "strategy_version",
    "provider", "provider_version", "model", "reported_model", "seed", "token_cap", "horizon", "allocation_rule",
    "round_index", "order_index", "status", "chosen_actions", "plan_truncated", "success", "failure_reason",
    "completed", "preconditions_satisfied", "hard_constraints_kept", "right_target", "no_forbidden_dependency",
    "no_erroneous_pruning", "illegal_actions", "steps_executed", "reference_optimal_steps", "excess_steps",
    "first_action", "first_action_class", "input_tokens", "cached_input_tokens", "output_tokens",
    "reasoning_output_tokens", "token_cap_check", "wall_seconds", "provider_tool_calls", "environment_actions",
    "human_interruptions", "session_id", "prompt_sha256", "started_at",
)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ">".join(str(item) for item in value)
    return str(value)


def csv_row(record: RolloutRecord) -> dict[str, str]:
    """The flat view of one record: identities, outcome columns, then resource columns."""
    evaluation, resources, tokens = record.evaluation, record.resources, record.resources["tokens"]
    value = {name: getattr(record, name) for name in CSV_COLUMNS if hasattr(record, name)}
    value.update({name: evaluation[name] for name in (
        "completed", "preconditions_satisfied", "hard_constraints_kept", "right_target", "no_forbidden_dependency",
        "no_erroneous_pruning", "illegal_actions", "steps_executed", "reference_optimal_steps", "excess_steps",
        "first_action", "first_action_class")})
    value.update({name: tokens[name] for name in (
        "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")})
    value.update(token_cap_check=resources["tokenCapCheck"], wall_seconds=resources["wallSeconds"],
                 provider_tool_calls=resources["providerToolCalls"], environment_actions=resources["environmentActions"],
                 human_interruptions=resources["humanInterruptions"], session_id=record.session["sessionId"])
    return {name: _cell(value[name]) for name in CSV_COLUMNS}


def write_csv(path: Path, records: Sequence[RolloutRecord]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(csv_row(record) for record in records)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))
