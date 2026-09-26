"""Strategies A-D over one base model and one prompt contract, and the runners that call it.

A strategy turns one exact snapshot into one request (a prompt and an output
schema) and turns the model's answer into a proposal (a plan of action ids).
Strategies differ only in policy, context and tool exposure:

- A  free choice: the snapshot and the action set, nothing else;
- B  fixed procedure: the same three-step procedure for every state;
- C  state-ranked actions: the environment's compiled legal actions; the model
     ranks them and the plan starts with its top choice;
- D  short lookahead: public one-step previews of every action before choosing.

A runner executes one request in a fresh session. ``CodexRunner`` starts one
``codex exec --json --ephemeral --ignore-user-config -s read-only
--output-schema`` process per rollout, in an empty directory, with the prompt
on a stdin pipe it owns and closes: no session is resumed and no transcript is
inherited. ``FakeRunner`` answers from scripted behaviours for tests and
offline runs. Nothing here imports the hidden reference or the evaluator.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .environment import Environment
from .state_snapshot import StateSnapshot


PROMPT_CONTRACT = "strategy-allocation-prompt@1"
RATIONALE_LIMIT = 600

_PREAMBLE = (
    "You are one arm of a controlled experiment. You choose actions for an agent working in a small, "
    "synthetic, finite environment that stands in for an architectural design task. Everything you may use "
    "is in this message: there are no files to read and no tools or commands to run, so do not run any. "
    "Answer only with the JSON object that the output schema describes."
)


@dataclass(frozen=True)
class StrategyRequest:
    """What one rollout sends to a runner. It is built from public data only."""

    strategy_id: str
    strategy_version: str
    prompt_contract: str
    case_id: str
    snapshot_digest: str
    horizon: int
    action_ids: tuple[str, ...]
    prompt: str
    schema_json: str

    @property
    def schema(self) -> dict[str, Any]:
        return json.loads(self.schema_json)

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Proposal:
    plan: tuple[str, ...]
    ranking: tuple[tuple[str, float], ...]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {"plan": list(self.plan), "ranking": [{"action": action, "score": score} for action, score in self.ranking],
                "rationale": self.rationale}


class ProposalError(ValueError):
    """An answer that is not a usable proposal. ``code`` is the rollout status it becomes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Strategy(Protocol):
    """The strategy interface: one snapshot in, one request out; one answer in, one proposal out."""

    strategy_id: str
    version: str

    def request(self, env: Environment, snapshot: StateSnapshot, *, horizon: int) -> StrategyRequest: ...

    def proposal(self, request: StrategyRequest, answer: object) -> Proposal: ...


def answer_schema(action_ids: Sequence[str]) -> dict[str, Any]:
    """The one output schema every strategy uses; only the action enum follows the case."""
    action = {"type": "string", "enum": list(action_ids)}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["plan", "ranking", "rationale"],
        "properties": {
            "plan": {"type": "array", "items": action},
            "ranking": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["action", "score"],
                    "properties": {"action": action, "score": {"type": "number"}},
                },
            },
            "rationale": {"type": "string"},
        },
    }


@dataclass(frozen=True)
class PromptStrategy:
    """One policy over the shared prompt contract; A-D are instances of this class."""

    strategy_id: str
    title: str
    version: str
    policy: str
    show_legal_actions: bool = False
    lookahead_depth: int = 0
    requires_ranking: bool = False

    def request(self, env: Environment, snapshot: StateSnapshot, *, horizon: int) -> StrategyRequest:
        if type(horizon) is not int or horizon < 1:
            raise ValueError("horizon must be a positive integer")
        state = env.state_of(snapshot)
        sections = [
            _PREAMBLE,
            "## State snapshot\nThe exact state you start from. `allowedActions` is the complete action set; "
            "`contextPack.context.facts` is what is known now.\n```json\n"
            + json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=1, sort_keys=True) + "\n```",
            f"## Horizon\nReturn `plan`: 1 to {horizon} action ids from allowedActions, in the order they should "
            f"run. The environment runs them in that order and stops after {horizon}. You will not see any "
            "observation between steps. An action that is not legal when its turn comes is refused and still "
            "uses that step.",
            f"## Policy {self.strategy_id}: {self.title}\n{self.policy}",
        ]
        if self.show_legal_actions:
            lines = []
            for action in snapshot.action_ids:
                refusal = env.refusal(state, action)
                lines.append(f"- {action}: legal" if refusal is None else f"- {action}: not legal now ({refusal})")
            sections.append("## Legal actions compiled from the current state\n" + "\n".join(lines))
        if self.lookahead_depth:
            previews = env.preview(state, self.lookahead_depth)
            sections.append("## One-step previews from the environment's public simulator\n"
                            "Each entry is what the environment would report after that single action from the "
                            "current state. Nothing has been executed.\n```json\n"
                            + json.dumps(previews, ensure_ascii=False, indent=1) + "\n```")
        sections.append(
            "## Answer\n"
            f"- `plan`: 1 to {horizon} action ids, in execution order.\n"
            "- `ranking`: an empty list unless your policy asks for a ranking; then one object per ranked "
            "action, best first, with a score between 0 and 1.\n"
            "- `rationale`: one or two sentences on why."
        )
        return StrategyRequest(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            prompt_contract=PROMPT_CONTRACT,
            case_id=snapshot.case_id,
            snapshot_digest=snapshot.digest,
            horizon=horizon,
            action_ids=snapshot.action_ids,
            prompt="\n\n".join(sections) + "\n",
            schema_json=json.dumps(answer_schema(snapshot.action_ids), sort_keys=True),
        )

    def proposal(self, request: StrategyRequest, answer: object) -> Proposal:
        if not isinstance(answer, Mapping) or set(answer) != {"plan", "ranking", "rationale"}:
            raise ProposalError("malformed", "the answer is not an object with plan, ranking and rationale")
        plan, ranking, rationale = answer["plan"], answer["ranking"], answer["rationale"]
        allowed = set(request.action_ids)
        if not isinstance(plan, list) or not plan or any(not isinstance(item, str) or item not in allowed
                                                         for item in plan):
            raise ProposalError("malformed", "plan must list at least one action id from the action set")
        if not isinstance(rationale, str):
            raise ProposalError("malformed", "rationale must be text")
        if not isinstance(ranking, list):
            raise ProposalError("malformed", "ranking must be a list")
        ranked = []
        for item in ranking:
            score = item.get("score") if isinstance(item, Mapping) else None
            if (not isinstance(item, Mapping) or set(item) != {"action", "score"} or item["action"] not in allowed
                    or isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score)):
                raise ProposalError("malformed", "each ranking entry is an allowed action with a finite score")
            ranked.append((item["action"], float(score)))
        if len({action for action, _ in ranked}) != len(ranked):
            raise ProposalError("malformed", "ranking names an action twice")
        if self.requires_ranking:
            if not ranked:
                raise ProposalError("policy_violation", "this policy ranks the legal actions before acting")
            top = max(ranked, key=lambda item: item[1])[0]  # the first of equal best scores
            if plan[0] != top:
                raise ProposalError("policy_violation", "the plan does not start with the top-ranked action")
        return Proposal(tuple(plan), tuple(ranked), rationale[:RATIONALE_LIMIT])


STRATEGIES: Mapping[str, PromptStrategy] = {
    strategy.strategy_id: strategy
    for strategy in (
        PromptStrategy(
            "A", "free choice", "A-free-choice@1",
            "Choose the plan you judge best for the task, from the snapshot alone. Leave `ranking` empty.",
        ),
        PromptStrategy(
            "B", "fixed procedure", "B-fixed-procedure@1",
            "Follow this fixed procedure. It is the same for every task and does not depend on the state.\n"
            "1. GATHER: the action that retrieves or reads information for the task.\n"
            "2. PRODUCE: the action that creates or changes the design.\n"
            "3. CHECK: the action that inspects the result.\n"
            "Map each step, in this order, to the one allowed action that matches it best, and skip a step "
            "only when no allowed action matches it. Do not add, repeat or reorder steps. Leave `ranking` empty.",
        ),
        PromptStrategy(
            "C", "state-ranked actions", "C-state-ranked@1",
            "Rank every action that is legal in the current state (see the compiled list below) by how well it "
            "fits the current state, best first, each with a score between 0 and 1. `plan` must start with your "
            "top-ranked action; continue it only with actions that follow from the state.",
            show_legal_actions=True, requires_ranking=True,
        ),
        PromptStrategy(
            "D", "short lookahead", "D-short-lookahead@1",
            "Before choosing, consider the one-step previews below: for every action, what the environment "
            "would report after taking it from the current state. Then choose your plan. Leave `ranking` empty.",
            lookahead_depth=1,
        ),
    )
}


# --- Runners -------------------------------------------------------------------

@dataclass(frozen=True)
class RunnerResult:
    """One call's answer and its resources. ``status`` is ok, timeout, provider_error or malformed."""

    status: str
    answer: object | None
    usage: Mapping[str, int | None]
    wall_seconds: float
    reported_model: str | None
    session_id: str | None
    provider_tool_calls: int | None
    detail: str | None = None


class Runner(Protocol):
    provider: str

    def describe(self) -> dict[str, Any]: ...

    def run(self, request: StrategyRequest, *, seed: int, timeout_s: float,
            raw_dir: Path | None = None) -> RunnerResult: ...


CODEX_FIXED_ARGUMENTS = (
    "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
    "--color", "never", "-s", "read-only",
)
CODEX_PROVIDER = "codex-exec"
TOOL_ITEM_TYPES = frozenset({"command_execution", "file_change", "mcp_tool_call", "web_search"})
_USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
                 "reasoning_output_tokens")


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def parse_codex_events(stdout: str | bytes | None) -> tuple[dict[str, int | None], str | None, str | None, int]:
    """Usage, reported model, session (thread) id and provider tool calls from ``codex exec --json``.

    Only CLI event metadata is read, never numbers mentioned inside a message.
    The terminal turn event reports the whole turn; it is not added to earlier ones.
    """
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    usage: dict[str, int | None] = {}
    model = session = None
    tools = 0
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if isinstance(event.get("model"), str) and event["model"].strip():
            model = event["model"].strip()[:200]
        if kind == "thread.started" and isinstance(event.get("thread_id"), str):
            session = event["thread_id"]
        item = event.get("item")
        if kind == "item.completed" and isinstance(item, dict) and item.get("type") in TOOL_ITEM_TYPES:
            tools += 1
        if kind in ("turn.completed", "turn.failed") and isinstance(event.get("usage"), dict):
            usage = {name: _count(event["usage"].get(name)) for name in _USAGE_FIELDS}
    return usage, model, session, tools


def run_bounded(command: Sequence[str], prompt: str, timeout_s: float, *,
                low_priority: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one child with the prompt on a stdin pipe this function owns, and a hard deadline.

    The prompt goes in as exact UTF-8 bytes (a text-mode pipe would rewrite
    line endings on Windows, and the recorded prompt digest would no longer
    name what the child read). The pipe is written and closed on its own
    thread and stdout/stderr are drained on threads, so neither a large prompt
    nor a chatty child can block the wait. Past the deadline the whole process
    tree is killed (``codex`` is a ``.cmd`` shim on Windows, so killing only
    the direct child would leave the real process holding the pipes) and
    ``TimeoutExpired`` is raised with whatever output arrived. On Windows the
    child runs at idle priority.
    """
    options: dict[str, Any] = {}
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if low_priority:
            flags |= getattr(subprocess, "IDLE_PRIORITY_CLASS", 0)
        options["creationflags"] = flags
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               shell=False, **options)
    captured = {"stdout": "", "stderr": ""}

    def feed() -> None:
        try:
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def drain(name: str, stream: Any) -> None:
        try:
            captured[name] = stream.read().decode("utf-8", errors="replace")
        except (OSError, ValueError):
            pass

    threads = [threading.Thread(target=feed, daemon=True),
               threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
               threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True)]
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass
        _finish(process, threads, 1.0)
        raise subprocess.TimeoutExpired(list(command), timeout_s, output=captured["stdout"], stderr=captured["stderr"])
    _finish(process, threads, 5.0)
    return subprocess.CompletedProcess(list(command), process.returncode, captured["stdout"], captured["stderr"])


def _finish(process: subprocess.Popen[bytes], threads: Sequence[threading.Thread], wait_s: float) -> None:
    for thread in threads:
        thread.join(timeout=wait_s)
    for stream in (process.stdin, process.stdout, process.stderr):
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _kill_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False,
                       stdin=subprocess.DEVNULL)
        return
    import signal

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


@dataclass
class CodexRunner:
    """One fresh, ephemeral ``codex exec`` process per rollout; auth is the user's own codex login."""

    executable: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    low_priority: bool = True
    provider: str = field(default=CODEX_PROVIDER, init=False)
    _version: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.executable = self.executable or shutil.which("codex") or "codex"

    def version(self) -> str:
        if self._version is None:
            try:
                completed = subprocess.run([self.executable, "--version"], capture_output=True, text=True,
                                           encoding="utf-8", errors="replace", timeout=30, check=False,
                                           stdin=subprocess.DEVNULL)
                first = next((line.strip() for line in (completed.stdout or "").splitlines() if line.strip()), "")
                self._version = first[:200] if completed.returncode == 0 and first else "unknown"
            except (OSError, subprocess.TimeoutExpired):
                self._version = "unavailable"
        return self._version

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "providerVersion": self.version(),
            "model": self.model or "cli-default",
            "sampling": {
                "reasoningEffort": self.reasoning_effort or "cli-default",
                "temperature": "not exposed by codex exec",
                "seed": "not exposed by codex exec; the rollout seed is recorded only",
            },
            "arguments": list(CODEX_FIXED_ARGUMENTS),
            "session": "one fresh ephemeral process per rollout in an empty directory; the prompt arrives on a "
                       "stdin pipe the runner owns and closes; no session is resumed",
            "tokenCap": "codex exec has no output-token limit; the harness checks reported output tokens after "
                        "the call",
        }

    def command(self, workdir: Path, schema_path: Path, answer_path: Path) -> list[str]:
        command = [self.executable, *CODEX_FIXED_ARGUMENTS, "-C", str(workdir), "--output-schema", str(schema_path),
                   "-o", str(answer_path)]
        if self.model:
            command += ["-m", self.model]
        if self.reasoning_effort:
            command += ["-c", f"model_reasoning_effort={self.reasoning_effort}"]
        return command + ["-"]  # the prompt arrives on stdin

    def run(self, request: StrategyRequest, *, seed: int, timeout_s: float,
            raw_dir: Path | None = None) -> RunnerResult:
        with tempfile.TemporaryDirectory(prefix="strategy-allocation-") as temporary:
            root = Path(temporary)
            workdir = root / "empty"
            workdir.mkdir()
            schema_path, answer_path = root / "schema.json", root / "answer.json"
            schema_path.write_text(request.schema_json, encoding="utf-8")
            command = self.command(workdir, schema_path, answer_path)
            started = time.monotonic()
            status, detail, stdout, stderr = "ok", None, "", ""
            try:
                completed = run_bounded(command, request.prompt, timeout_s, low_priority=self.low_priority)
                stdout, stderr = completed.stdout or "", completed.stderr or ""
                if completed.returncode != 0:
                    status, detail = "provider_error", f"codex exited with {completed.returncode}: {stderr.strip()[-300:]}"
            except FileNotFoundError:
                status, detail = "provider_error", f"codex executable {self.executable!r} was not found"
            except subprocess.TimeoutExpired as exc:
                status, detail = "timeout", f"no answer within {timeout_s:g} s"
                stdout, stderr = exc.output or "", exc.stderr or ""
            wall = time.monotonic() - started
            answer_text = answer_path.read_text(encoding="utf-8") if answer_path.exists() else None
            if raw_dir is not None:
                raw_dir.mkdir(parents=True, exist_ok=True)
                (raw_dir / "prompt.txt").write_text(request.prompt, encoding="utf-8", newline="\n")
                (raw_dir / "schema.json").write_text(request.schema_json, encoding="utf-8", newline="\n")
                (raw_dir / "events.jsonl").write_text(stdout, encoding="utf-8", newline="\n")
                (raw_dir / "stderr.txt").write_text(stderr, encoding="utf-8", newline="\n")
                if answer_text is not None:
                    (raw_dir / "answer.json").write_text(answer_text, encoding="utf-8", newline="\n")
        usage, model, session, tools = parse_codex_events(stdout)
        answer = None
        if status == "ok":
            try:
                answer = json.loads(answer_text) if answer_text is not None else None
            except ValueError:
                answer = None
            if answer is None:
                status, detail = "malformed", "no JSON answer was written"
        return RunnerResult(status, answer, usage, wall, model, session, tools, detail)


# --- Fake runner (tests and offline runs) -------------------------------------

@dataclass(frozen=True)
class FakeReply:
    answer: object | None = None
    status: str = "ok"
    input_tokens: int = 1200
    output_tokens: int = 150
    provider_tool_calls: int = 0


FakeBehaviour = Callable[[StrategyRequest, random.Random], FakeReply]


def plan_answer(plan: Sequence[str], *, ranked: bool = False, rationale: str = "scripted") -> dict[str, Any]:
    """An answer in the shared schema; ``ranked`` puts the first action on top of the ranking."""
    ranking = [{"action": plan[0], "score": 0.9}] if ranked and plan else []
    return {"plan": list(plan), "ranking": ranking, "rationale": rationale}


def weighted(options: Sequence[tuple[float, Sequence[str]]], *, ranked: bool = False) -> FakeBehaviour:
    """A behaviour that samples one plan by weight from the rollout's seeded generator."""
    weights = [weight for weight, _ in options]
    plans = [tuple(plan) for _, plan in options]

    def behave(request: StrategyRequest, rng: random.Random) -> FakeReply:
        return FakeReply(plan_answer(rng.choices(plans, weights)[0], ranked=ranked))

    return behave


class FakeRunner:
    """Scripted answers keyed by ``(case_id, strategy_id)`` or by ``strategy_id``; no process, no model."""

    provider = "fake"

    def __init__(self, behaviours: Mapping[object, FakeBehaviour], *, version: str = "fake@1") -> None:
        self.behaviours = dict(behaviours)
        self.version = version
        self.calls: list[tuple[str, str, int]] = []

    def describe(self) -> dict[str, Any]:
        return {"provider": self.provider, "providerVersion": self.version, "model": "fake",
                "sampling": {"seed": "the rollout seed drives the scripted choice"},
                "session": "no process; each call is independent"}

    def run(self, request: StrategyRequest, *, seed: int, timeout_s: float,
            raw_dir: Path | None = None) -> RunnerResult:
        behaviour = self.behaviours.get((request.case_id, request.strategy_id)) or self.behaviours.get(request.strategy_id)
        if behaviour is None:
            raise KeyError(f"no fake behaviour for {request.case_id}/{request.strategy_id}")
        self.calls.append((request.case_id, request.strategy_id, seed))
        reply = behaviour(request, random.Random(seed))
        session = hashlib.sha256(f"{request.case_id}:{request.strategy_id}:{seed}".encode()).hexdigest()[:16]
        usage = {"input_tokens": reply.input_tokens, "cached_input_tokens": 0, "output_tokens": reply.output_tokens,
                 "reasoning_output_tokens": 0}
        answer = reply.answer if reply.status == "ok" else None
        return RunnerResult(reply.status, answer, usage, 0.0, "fake", f"fake-{session}", reply.provider_tool_calls,
                            None if reply.status == "ok" else f"scripted {reply.status}")


# Invented behaviours for an offline run of the whole loop. They are not
# measurements of any model; they only give the arms different distributions.
DEMO_PLANS: Mapping[str, Mapping[str, Sequence[tuple[float, Sequence[str]]]]] = {
    "provided-source": {
        "A": ((0.6, ("ReadProvidedSource", "Model")), (0.25, ("BM25", "Model")), (0.15, ("Model",))),
        "B": ((0.5, ("RAG", "Model")), (0.5, ("ReadProvidedSource", "Model"))),
        "C": ((0.8, ("ReadProvidedSource", "Model")), (0.2, ("RAG", "ReadProvidedSource", "Model"))),
        "D": ((0.9, ("ReadProvidedSource", "Model")), (0.1, ("AskHuman", "ReadProvidedSource", "Model"))),
    },
    "protected-dependency": {
        "A": ((0.5, ("InspectDependency", "Model")), (0.5, ("Model",))),
        "B": ((0.7, ("InspectDependency", "Model")), (0.3, ("RAG", "Model"))),
        "C": ((0.8, ("InspectDependency", "Model")), (0.2, ("Model", "Repair"))),
        "D": ((0.9, ("InspectDependency", "Model")), (0.1, ("RAG", "InspectDependency", "Model"))),
    },
    "open-direction": {
        "A": ((0.5, ("LocalStudy", "AskHuman")), (0.5, ("CoarseModel", "Inspect"))),
        "B": ((0.8, ("LocalStudy", "CoarseModel", "Inspect")), (0.2, ("Inspect", "CoarseModel", "Inspect"))),
        "C": ((0.6, ("LocalStudy", "AskHuman")), (0.4, ("LocalStudy", "CoarseModel"))),
        "D": ((0.7, ("LocalStudy", "AskHuman")), (0.3, ("AskHuman", "LocalStudy"))),
    },
    "local-conflict": {
        "A": ((0.7, ("Repair",)), (0.3, ("ReModel",))),
        "B": ((0.8, ("Inspect", "Repair", "Inspect")), (0.2, ("RAG", "ReModel", "Inspect"))),
        "C": ((0.9, ("Repair",)), (0.1, ("Inspect", "Repair"))),
        "D": ((0.9, ("Repair", "Inspect")), (0.1, ("RAG", "Repair"))),
    },
}


def demo_fake_runner() -> FakeRunner:
    return FakeRunner({(case_id, strategy_id): weighted(options, ranked=STRATEGIES[strategy_id].requires_ranking)
                       for case_id, arms in DEMO_PLANS.items() for strategy_id, options in arms.items()},
                      version="fake-demo@1")
