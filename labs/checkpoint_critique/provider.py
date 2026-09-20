"""One tool-free, fresh Claude CLI call for the GH-173 experiment.

The CLI's structured public answer and numeric accounting cross this boundary.
Raw stdout, stderr, thinking blocks and native sessions are never retained here.
This lab uses Hub's CLI discovery/process cleanup and Monitor's token convention;
it starts neither Hub nor a project service and has no project writer.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from monkeymonitor.usage import TokenUsage


@dataclass(frozen=True)
class CallResult:
    answer: dict | None
    usage: dict
    elapsed_seconds: float
    outcome: str
    actual_model: str | None
    provider_metadata: dict


def _hub_helpers():
    # The repository keeps its two app packages outside the installed core.
    root = Path(__file__).resolve().parents[2]
    for relative in ("apps/monkeyhub/api", "apps/archflow-studio/api"):
        location = str(root / relative)
        if location not in sys.path:
            sys.path.insert(0, location)
    from monkeyhub_api.chat import _cli_commands, _stop_process
    return _cli_commands, _stop_process


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _model_name(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[1m\])?", value) else None


def _tokens(row):
    ordinary = _count(row.get("inputTokens"))
    cached = _count(row.get("cacheReadInputTokens"))
    written = _count(row.get("cacheCreationInputTokens"))
    total = ordinary + cached + written if None not in (ordinary, cached, written) else None
    return TokenUsage(input_tokens=total, output_tokens=_count(row.get("outputTokens")),
                      cached_input_tokens=cached, cache_write_input_tokens=written)


def _accounting(payload, requested_model):
    """Use every reported model, including CLI auxiliary models, exactly once."""
    models = payload.get("modelUsage")
    rows = {}
    if isinstance(models, dict):
        for model, value in models.items():
            if _model_name(model) and isinstance(value, dict):
                rows[model] = {
                    **_tokens(value).to_dict(),
                    "api_equivalent_cost_usd": _number(value.get("costUSD")),
                    # The observed CLI reports the model's capacity here, not
                    # the per-request environment cap (64000 vs requested 4096).
                    "reported_model_max_output_tokens": _count(value.get("maxOutputTokens")),
                }
    totals = {}
    for key in TokenUsage().to_dict():
        values = [row[key] for row in rows.values()]
        totals[key] = sum(values) if values and all(value is not None for value in values) else None
    costs = [row["api_equivalent_cost_usd"] for row in rows.values()]
    reported_cost = _number(payload.get("total_cost_usd"))
    summed_cost = sum(costs) if costs and all(cost is not None for cost in costs) else None
    totals["api_equivalent_cost_usd"] = reported_cost if reported_cost is not None else summed_cost
    requested = requested_model.removesuffix("[1m]")
    matching = [model for model in rows if model.removesuffix("[1m]") == requested]
    actual = matching[0] if len(matching) == 1 else None
    # A single unexpected model is observable, but is not quietly substituted.
    if actual is None and len(rows) == 1:
        actual = next(iter(rows))
    metadata = {
        "per_model_usage": rows,
        "reported_total_cost_usd": reported_cost,
        "summed_model_cost_usd": summed_cost,
        "token_source": "modelUsage" if rows else "unavailable",
        "cost_basis": "provider_reported_api_equivalent_not_account_bill",
        "reported_agent_turns": _count(payload.get("num_turns")),
        "provider_duration_ms": _number(payload.get("duration_ms")),
        "provider_api_duration_ms": _number(payload.get("duration_api_ms")),
        # Neither agent turns nor a CLI subprocess is an exact API request count.
        "provider_request_count": None,
    }
    return totals, actual, metadata


class ClaudeProvider:
    def __init__(self, model="claude-opus-5", effort="low", output_cap=4096):
        if not _model_name(model) or not model.startswith("claude-"):
            raise ValueError("an explicit Claude model ID is required")
        if effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("unsupported effort")
        if type(output_cap) is not int or output_cap < 1:
            raise ValueError("output_cap must be a positive integer")
        self.model, self.effort, self.output_cap = model, effort, output_cap
        self._command = None
        self._stop = None
        self._version = None

    def _resolve(self):
        if self._command is not None:
            return
        locate, self._stop = _hub_helpers()
        self._command = locate().get("claude", ())
        if self._command:
            try:
                result = subprocess.run([*self._command, "--version"], capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=10,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                match = re.search(r"\b\d+\.\d+\.\d+\b", result.stdout or "")
                self._version = match.group() if result.returncode == 0 and match else None
            except (OSError, subprocess.TimeoutExpired):
                pass

    def describe(self):
        try:
            self._resolve()
        except (ImportError, OSError):
            pass
        return {
            "provider": "claude-cli", "cli_version": self._version,
            "model": self.model, "effort": self.effort, "output_cap": self.output_cap,
            "output_cap_enforcement": "requested_not_verified_for_all_internal_calls",
            "native_session": "fresh_no_persistence", "tools": [], "mcp_servers": [],
            "api_retry_limit": 0, "structured_output_attempt_limit": 1,
            "max_agent_turns": 2, "seed": None,
            "output": "json_schema", "cost_basis": "api_equivalent_not_account_bill",
        }

    def call(self, prompt: dict, schema: dict, *, timeout_seconds: float, max_cost_usd: float) -> CallResult:
        started = time.monotonic()
        metadata = {"process_calls": 0, "provider_request_count": None, **self.describe()}

        def finish(outcome, payload=None, answer=None):
            usage, actual, observed = _accounting(payload or {}, self.model)
            return CallResult(answer, usage, time.monotonic() - started, outcome, actual,
                              {**metadata, **observed})

        if _number(timeout_seconds) is None or timeout_seconds == 0:
            return finish("timeout")
        if _number(max_cost_usd) is None or max_cost_usd == 0:
            return finish("budget_exhausted")
        if not self._command or self._version is None:
            return finish("provider_unavailable")
        try:
            Draft202012Validator.check_schema(schema)
            if not isinstance(prompt, dict):
                return finish("invalid_request")
            text = json.dumps(prompt, ensure_ascii=False, allow_nan=False)
            schema_text = json.dumps(schema, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError, SchemaError) as exc:
            # SchemaError can contain the caller's entire schema; do not export it.
            metadata["error_category"] = type(exc).__name__
            return finish("invalid_request")
        command = [*self._command, "-p", "--output-format", "json", "--safe-mode",
                   "--tools", "", "--no-session-persistence", "--setting-sources", "",
                   "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--no-chrome", "--permission-mode", "dontAsk", "--permission-prompts", "none",
                   "--model", self.model, "--effort", self.effort, "--max-turns", "2",
                   "--max-budget-usd", str(max_cost_usd), "--json-schema", schema_text,
                   "--system-prompt", "Use only the synthetic public JSON supplied on stdin. Return the requested public structured answer. Do not expose private reasoning or call tools."]
        environment = dict(os.environ)
        environment.update(CLAUDE_CODE_EFFORT_LEVEL=self.effort,
                           CLAUDE_CODE_MAX_OUTPUT_TOKENS=str(self.output_cap),
                           CLAUDE_CODE_MAX_RETRIES="0", MAX_STRUCTURED_OUTPUT_RETRIES="1")
        # The parent's CLI nesting marker is not authentication or configuration.
        environment.pop("CLAUDECODE", None)
        if time.monotonic() - started >= timeout_seconds:
            return finish("timeout")
        captured = {}
        timed_out = False
        try:
            with tempfile.TemporaryDirectory(prefix="checkpoint-critique-") as cwd:
                process = subprocess.Popen(command, cwd=cwd, env=environment, shell=False,
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                           start_new_session=os.name != "nt")
                metadata["process_calls"] = 1

                def communicate():
                    try:
                        captured["stdout"], _ = process.communicate(text)
                    except (OSError, ValueError):
                        captured["pipe_error"] = True

                # A Windows stdin write may block before communicate's timeout;
                # keep pipe I/O on a worker and enforce the deadline on wait().
                reader = threading.Thread(target=communicate, daemon=True)
                reader.start()
                try:
                    process.wait(timeout=max(0.001, timeout_seconds - (time.monotonic() - started)))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._stop(process)
                reader.join(timeout=5)
                metadata["exit_code"] = process.poll()
                metadata["pipe_capture_complete"] = not reader.is_alive() and not captured.get("pipe_error", False)
        except (OSError, ValueError) as exc:
            metadata["error_category"] = type(exc).__name__
            return finish("provider_error")
        try:
            payload = json.loads(captured.get("stdout", ""))
            if not isinstance(payload, dict) or payload.get("type") != "result":
                payload = {}
        except (ValueError, TypeError):
            payload = {}
        # Drop the transport immediately. No raw text is ever returned or saved.
        captured.clear()
        if timed_out:
            return finish("timeout", payload)
        if not payload:
            return finish("malformed" if metadata.get("exit_code") == 0 else "provider_error")
        subtype = payload.get("subtype")
        known = {"success", "error_max_turns", "error_max_budget_usd",
                 "error_during_execution", "error_max_structured_output_retries"}
        metadata["result_subtype"] = subtype if subtype in known else "unrecognized"
        if subtype == "error_max_budget_usd":
            return finish("budget_exhausted", payload)
        if subtype != "success" or payload.get("is_error") or metadata.get("exit_code") != 0:
            return finish("provider_error", payload)
        answer = payload.get("structured_output")
        if not isinstance(answer, dict) or not Draft202012Validator(schema).is_valid(answer):
            return finish("malformed", payload)
        usage, actual, _ = _accounting(payload, self.model)
        if actual is not None and actual.removesuffix("[1m]") != self.model.removesuffix("[1m]"):
            return finish("model_mismatch", payload, answer)
        if actual is None or any(usage[key] is None for key in
                                 ("input_tokens", "output_tokens", "api_equivalent_cost_usd")):
            return finish("telemetry_missing", payload, answer)
        return finish("success", payload, answer)
