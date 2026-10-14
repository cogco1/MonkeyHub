"""X3 driver: one architect request worked in MonkeyHub's own way (M), by ``claude -p`` as an external host.

The agent reaches MonkeyHub only through MonkeyHub's own MCP tool server
(``apps/monkeyhub/api/monkeyhub_api/chat/mcp_server.py``) in its external-host binding: the CLI starts that
file with ``--project-dir`` and ``--source-session-id``, the agent calls ``presentation_bind``, publishes the
request into the bound Hub conversation (``chat_present`` kind=user), and then works with the Hub's own tools
(``studio_request``/``studio_schema``/``visual_review``): proposals, a reversible candidate, admission and the
Continue (``PUT /api/working-draft``) that lands it on the working design. The Hub's chat session runner is not
used; nothing here writes the project except through those tools.

Pieces:

- ``isolated_hub(root)``: makes (or reuses) the BB production project under ``root/projects`` with
  ``production_project.make_production_project`` and starts a disposable Hub (in this process) plus its Project
  Runtime (a child process the Hub owns) on free ports, with APPDATA/LOCALAPPDATA under ``root``, no
  ``ARCHFLOW_STUDIO_*``/``MONKEYMONITOR_DATA_DIR`` from the parent and CUDA hidden. Modelled on
  ``tools/benchmarks/run_design_loop.py::isolated_services``, which only opens the runtime's own fixture.
- ``run_turn(...)``: one ``claude -p`` turn, checked against and recorded in the shared cost ledger (``x2.Ledger``,
  priced with ``x2.price``) exactly as ``x2.run`` does.
- ``result_model(runtime_url, project_dir)``: the model the turn left, read through the Runtime's own routes:
  ``GET /api/working-source`` for the working design, the Hub's operation journal
  (``GET /api/runtime/projects/{id}``) for the candidates a conversation made, ``GET /api/admissions`` for what
  it admitted, and ``GET /api/artifacts`` for each run's certified ``.preview.3dm``. Which run stands for the
  result is ``choose_result``: the working design if the turn moved it, else its admitted candidate, else the
  candidate its answer names, else its last candidate (an agent may build a no-op baseline candidate last).
- ``lab_realization(project_dir, run_id, out_dir)``: when MonkeyHub exported no model for that run (MonkeyArch
  exports nothing for a run with a violated relation), the run's retained state record realized by the lab's
  own ``pipeline.realize``; labelled as such, never mistaken for a MonkeyHub artifact.

Session files: with ``--no-session-persistence`` (the default) the CLI keeps no transcript. A resumable turn
(``persist=True`` or ``resume=...``) needs ``config_dir``: the CLI's own directory (``CLAUDE_CONFIG_DIR``), where
it writes its transcript under ``projects/``; without one the transcript would go to the user's profile on C:,
which this lab may not write, so the call is refused. A config directory has to be signed in once by its owner
(``claude auth login`` with ``CLAUDE_CONFIG_DIR`` set); nothing here copies credentials.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

if __package__ in (None, ""):
    # Started by its path, as the CLI starts the lab's MCP proxy: the checkout is not on the path yet.
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from labs.revision_tasks import _bootstrap  # noqa: F401,E402  (source roots first)

import hashlib
import json
import os
import site
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import ProxyHandler, Request, build_opener

from labs.revision_tasks import x2

CHECKOUT = _bootstrap.CHECKOUT
MCP_SERVER = CHECKOUT / "apps" / "monkeyhub" / "api" / "monkeyhub_api" / "chat" / "mcp_server.py"
MODEL = x2.MODEL
SERVER = "monkeyhub"
# The external binding's tools that MonkeyHub's own chat approves for a headless Claude turn
# (providers._CLAUDE_APPROVED), plus presentation_bind, which only the external binding has. Named, as MonkeyHub
# names them: a wildcard would also approve the computer-use tools the server advertises, which MonkeyHub approves
# only on a machine whose policy enables them.
TOOLS = ("presentation_bind", "chat_present", "studio_schema", "studio_request", "visual_review", "attachment_read", "fab_request")
ALLOWED = tuple(f"mcp__{SERVER}__{name}" for name in TOOLS)
BUDGET_USD = 3.0
MAX_TURNS = 40
TIMEOUT_S = 1800
START_RUN = "stage-0-001"   # production_project.STAGE_RUN: the run the BB project starts from
# Variables of the Claude Code session this harness may itself run under that would change the child's tool
# surface; every other variable reaches the CLI as it does in x2.call_model.
HOST_ONLY = ("CLAUDE_CODE_TERMINAL_MCP_TOOLS",)
MCP_OUTPUT_TOKENS = 150000
# The CLI writes an MCP tool result longer than the tool's maxResultSizeChars (50,000 by default) to a file the agent
# cannot open without file tools; a tool may raise it through this _meta key (the CLI caps it at 500,000).
RESULT_SIZE_KEY = "anthropic/maxResultSizeChars"
RESULT_SIZE_CHARS = 400000


class X3Error(RuntimeError):
    pass


# ---------------------------------------------------------------- environment

def _scrubbed(parent: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in parent.items() if not key.startswith("ARCHFLOW_STUDIO_") and key != "MONKEYMONITOR_DATA_DIR"}


def service_environment(parent: Mapping[str, str], root: Path) -> dict[str, str]:
    """The Hub's and Runtime's environment: the parent's, isolated as ``run_design_loop.isolated_services`` does, CUDA hidden."""

    environment = _scrubbed(parent)
    environment.update(APPDATA=str(root / "settings"), LOCALAPPDATA=str(root / "local"), PYTHONUTF8="1", CUDA_VISIBLE_DEVICES="")
    user_site = site.getusersitepackages()
    if site.ENABLE_USER_SITE and user_site in sys.path:
        environment["PYTHONPATH"] = os.pathsep.join(value for value in (environment.get("PYTHONPATH"), user_site) if value)
    return environment


def cli_environment(parent: Mapping[str, str], config_dir: Path | None = None) -> dict[str, str]:
    """The CLI's environment: x2's (the parent's, auto-memory off as MonkeyHub's own chat runs it), isolated and CUDA hidden.

    The CLI keeps the user's APPDATA: its sign-in lives with its configuration, not the Hub's settings. The MCP server
    it starts inherits this and only speaks HTTP to the Hub.
    """

    environment = {key: value for key, value in _scrubbed(parent).items() if key not in HOST_ONLY}
    # An MCP result over the CLI's token limit (25k by default) is saved to a file under the CLI's projects directory
    # and replaced by its path; with no built-in tools the agent cannot read it (measured: BA's 51 KB model read).
    # MonkeyHub's own chat reads such a file with its built-in tools; here the answer comes inline instead.
    environment.update(CLAUDE_CODE_DISABLE_AUTO_MEMORY="1", CUDA_VISIBLE_DEVICES="", MAX_MCP_OUTPUT_TOKENS=str(MCP_OUTPUT_TOKENS))
    if config_dir is not None:
        environment["CLAUDE_CONFIG_DIR"] = str(config_dir)
    else:
        environment.pop("CLAUDE_CONFIG_DIR", None)
    return environment


# ---------------------------------------------------------------- HTTP

def _call(base: str, path: str, method: str = "GET", body: Any = None, timeout: float = 60, raw: bool = False) -> Any:
    request = Request(base.rstrip("/") + path, method=method, headers={"Content-Type": "application/json"},
                      data=None if body is None else json.dumps(body).encode("utf-8"))
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            data = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise X3Error(f"{method} {path}: HTTP {exc.code}: {detail}") from exc
    return data if raw else json.loads(data)


# ---------------------------------------------------------------- the services

@dataclass(frozen=True)
class HubServices:
    hub_url: str
    runtime_url: str
    monitor_url: str
    project_dir: Path
    runtime_id: str
    root: Path
    # The environment before isolation, for the CLI; never written anywhere.
    parent_env: Mapping[str, str] = field(repr=False, compare=False, default_factory=dict)


def building_project(projects: Path, building: str = "BB") -> Path:
    """A lab building's production project under ``projects`` (BB ``bb_house``, BA ``ba_pantheon``): made once,
    reused when complete, never removed."""

    from labs.revision_tasks import buildings, fixtures, production_project

    make = {"BB": buildings.bb_house, "BA": buildings.ba_pantheon}[building]
    project_dir = projects / fixtures.PROJECT_ID
    if project_dir.exists():
        if not (project_dir / "project.json").is_file() or not (project_dir / "runs" / production_project.STAGE_RUN).is_dir():
            raise X3Error(f"{project_dir} exists but is not a complete {building} project; choose a new root")
        return project_dir
    return production_project.make_production_project(make(), projects).project_dir


def bb_project(projects: Path) -> Path:
    return building_project(projects, "BB")


def ba_project(projects: Path) -> Path:
    return building_project(projects, "BA")


@contextmanager
def isolated_hub(root: Path, *, make_project: Callable[[Path], Path] = bb_project, reference_run: str = START_RUN,
                 start_timeout: float = 180) -> Iterator[HubServices]:
    """A disposable Hub and its Project Runtime on the BB project under ``root``; stopped on exit.

    ``make_project(root / "projects")`` returns the project directory (made or reused); ``reference_run`` is the run
    the Hub's settings name as its reference, the run the project starts from.
    """

    from tools.benchmarks import run_turn_benchmark as bench

    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    parent = dict(os.environ)
    with patch.dict(os.environ, service_environment(parent, root), clear=True):
        project = make_project(root / "projects").resolve()
        project_id = json.loads((project / "project.json").read_text(encoding="utf-8"))["project_id"]
        ports = bench.free_ports(3)
        runtime = root / "runtime"
        bench.save_application_settings(runtime, bench.ApplicationSettingsDto(
            projectDir=str(project), referenceRun=reference_run, cadExport="occt", studioPort=ports[1], monitorPort=ports[2]))
        app = bench.create_app(bench.HubSettings(runtime_root=runtime, port=ports[0]))
        server = bench.uvicorn.Server(bench.uvicorn.Config(app, host="127.0.0.1", port=ports[0], log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        hub, monitor = f"http://127.0.0.1:{ports[0]}", f"http://127.0.0.1:{ports[2]}"
        try:
            deadline = time.monotonic() + start_timeout
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(.1)
            if not server.started:
                raise X3Error("the isolated Hub did not start")
            opened = _call(hub, "/api/runtime/projects/open", "POST", {"projectId": project_id, "projectDir": str(project)}, timeout=120)
            _call(hub, "/api/apps/monkeyarch/start?" + urlencode({"projectDir": str(project)}), "POST", {}, timeout=120)
            while True:
                rows = _call(hub, "/api/apps?" + urlencode({"projectDir": str(project)}))
                studio = next(row for row in rows if row["appId"] == "monkeyarch")
                if studio["state"] == "running":
                    break
                if studio["state"] == "error" or time.monotonic() > deadline:
                    raise X3Error(f"the isolated Runtime did not start: {studio.get('state')} {studio.get('error')}")
                time.sleep(.25)
            yield HubServices(hub, studio["apiUrl"].rstrip("/"), monitor, project, opened["runtimeId"], root, parent)
        finally:
            app.state.chats.shutdown()
            server.should_exit = True
            worker.join(60)
            if worker.is_alive():
                raise X3Error("the isolated Hub failed to drain")


# ---------------------------------------------------------------- one turn

def mcp_config(*, hub_url: str, project_dir: Path, session_id: str, python: str = sys.executable,
               title: str | None = None) -> dict[str, Any]:
    """MonkeyHub's MCP server in its external-host binding, configured as MonkeyHub's own chat configures it (alwaysLoad)."""

    args = [str(MCP_SERVER), "--mcp", "--hub-url", hub_url, "--project-dir", str(project_dir),
            "--source-session-id", session_id, "--provider", "claude"]
    if title:
        args += ["--title", title]
    return {"mcpServers": {SERVER: {"command": python, "args": args, "alwaysLoad": True}}}


def command(cli: str, mcp_file: Path, *, max_turns: int = MAX_TURNS, budget_usd: float = BUDGET_USD,
            resume: str | None = None, persist: bool = False, system_prompt_file: Path | None = None,
            model: str = MODEL, allowed: tuple[str, ...] = ALLOWED, settings_file: Path | None = None) -> list[str]:
    """The CLI's flags: MonkeyHub's tools only, no built-in tool, project and local settings only, one bounded turn.

    ``settings_file`` is x2's ``settings.json`` (bundled skills off), passed as x2 passes it.
    """

    argv = [cli, "-p", "--model", model, "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
            "--setting-sources", "project,local", "--disable-slash-commands", "--tools", "",
            "--allowedTools", ",".join(allowed), "--disallowedTools", "WebSearch,WebFetch",
            "--strict-mcp-config", "--mcp-config", str(mcp_file), "--max-turns", str(max_turns),
            "--max-budget-usd", f"{budget_usd:g}"]
    if system_prompt_file is not None:
        argv += ["--append-system-prompt-file", str(system_prompt_file)]
    if settings_file is not None:
        argv += ["--settings", str(settings_file)]
    if resume:
        argv += ["--resume", resume]
    elif not persist:
        argv.append("--no-session-persistence")
    return argv


def _events(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text")
    return ""


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """What the stream says the turn did: its session, each tool call (name, method, path, refusal) and its result."""

    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    texts: list[str] = []
    for event in events:
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        for item in message.get("content") or ():
            if not isinstance(item, dict):
                continue
            if event.get("type") == "assistant" and item.get("type") == "tool_use":
                name = str(item.get("name", ""))
                arguments = item.get("input") if isinstance(item.get("input"), dict) else {}
                server, short = (name.split("__", 2)[1:] if name.startswith("mcp__") and name.count("__") >= 2 else (None, name))
                call = {"name": short, "server": server}
                for key in ("method", "path", "kind", "taskClass", "awaitSeconds", "pathPrefix", "section", "adopt", "rebuild_from_state"):
                    if key in arguments:
                        call[key] = arguments[key]
                if isinstance(arguments.get("body"), dict):
                    call["body_keys"] = sorted(arguments["body"])
                calls[item.get("id", str(len(order)))] = call
                order.append(item.get("id", str(len(order) - 1)))
            elif event.get("type") == "assistant" and item.get("type") == "text" and item.get("text"):
                texts.append(item["text"])
            elif event.get("type") == "user" and item.get("type") == "tool_result" and item.get("tool_use_id") in calls:
                call = calls[item["tool_use_id"]]
                text = _text(item.get("content"))
                call["is_error"] = bool(item.get("is_error"))
                if call["is_error"]:
                    try:
                        failure = json.loads(text)
                        call["error"] = {k: failure.get(k) for k in ("code", "httpStatus", "detail") if isinstance(failure, dict)}
                    except ValueError:
                        call["error"] = {"detail": text[:600]}
                else:
                    call["result_chars"] = len(text)
    servers = init.get("mcp_servers") or []
    return {"session_id": result.get("session_id") or init.get("session_id"), "model": init.get("model"),
            "permission_mode": init.get("permissionMode"), "claude_code_version": init.get("claude_code_version"),
            "tools": init.get("tools"), "mcp_servers": servers,
            "tool_calls": [calls[key] for key in order], "tool_call_count": len(order),
            "tool_errors": sum(1 for key in order if calls[key].get("is_error")),
            "assistant_text": texts, "final_text": result.get("result") or "", "result_subtype": result.get("subtype"),
            "is_error": result.get("is_error"), "num_turns": result.get("num_turns"), "duration_ms": result.get("duration_ms"),
            "permission_denials": result.get("permission_denials") or []}


def run_turn(request_text: str, *, workdir: Path, cli: str, ledger: x2.Ledger, part: str, hub_url: str | None = None,
             project_dir: Path | None = None, session_id: str | None = None, resume: str | None = None,
             system_prompt_file: Path | None = None, max_turns: int = MAX_TURNS, budget_usd: float = BUDGET_USD,
             persist: bool = False, config_dir: Path | None = None, default_config: bool = False,
             parent_env: Mapping[str, str] | None = None, trial_id: str | None = None, timeout: float = TIMEOUT_S,
             close_open_turn: bool = True, mcp: dict[str, Any] | None = None, allowed: tuple[str, ...] = ALLOWED,
             prompt: str | None = None, cwd: Path | None = None, settings_file: Path | None = None,
             experiment: str = "X3") -> dict[str, Any]:
    """One ``claude -p`` turn; its stream and cost are recorded.

    ``workdir`` is a new directory owned by this turn: the CLI runs in its empty ``cwd/`` (or in ``cwd``, which every
    turn of a resumed session must share, since the CLI finds a session by its working directory), and the MCP config,
    the prompt, the stream and a summary are written beside it. By default the turn is bound to the Hub conversation
    ``session_id`` through MonkeyHub's own server (``mcp_config``); ``mcp`` replaces that configuration (a lab proxy or
    server) and ``allowed`` names its tools. ``prompt`` is what the CLI reads (default ``request_text``, the architect's
    words). ``resume`` is the CLI session id a previous turn returned. ``parent_env`` is ``HubServices.parent_env``
    when the turn runs inside ``isolated_hub``. A conversation turn the agent left running is closed afterwards
    (``close_open_turn``) so the next request can open one; the record says who closed it.

    A resumable turn (``persist`` or ``resume``) keeps the CLI's transcript in its config directory: ``config_dir``, or
    with ``default_config`` the CLI's default one (allowed for M+ only; the caller accounts for the space it takes).
    """

    if (persist or resume) and config_dir is None and not default_config:
        raise X3Error("a resumable turn writes the CLI's transcript into its config directory: pass config_dir, or "
                      "default_config=True where the CLI's default directory is allowed (M+)")
    check_budget(ledger, part, budget_usd)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=False)
    if cwd is None:
        cwd = workdir / "cwd"
        cwd.mkdir()
    mcp_file = workdir / "mcp.json"
    configured = mcp if mcp is not None else mcp_config(hub_url=hub_url, project_dir=project_dir, session_id=session_id)
    mcp_file.write_text(json.dumps(configured, indent=1) + "\n", encoding="utf-8", newline="\n")
    text = request_text if prompt is None else prompt
    (workdir / "request.md").write_text(request_text, encoding="utf-8", newline="\n")
    (workdir / "prompt.md").write_text(text, encoding="utf-8", newline="\n")
    argv = command(cli, mcp_file, max_turns=max_turns, budget_usd=budget_usd, resume=resume, persist=persist,
                   system_prompt_file=system_prompt_file, allowed=allowed, settings_file=settings_file)
    environment = cli_environment(os.environ if parent_env is None else parent_env, config_dir)
    trial = trial_id or f"X3-{workdir.name}"
    (workdir / "command.json").write_text(json.dumps({"argv": argv[1:], "cwd": str(cwd), "config_dir": str(config_dir) if config_dir else None},
                                                     indent=1) + "\n", encoding="utf-8", newline="\n")
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    try:
        done = subprocess.run(argv, input=text, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=environment)
        stdout, stderr, exit_code = done.stdout, done.stderr, done.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr, exit_code = f"timed out after {timeout:.0f} s", None
    seconds = time.perf_counter() - started
    (workdir / "stream.jsonl").write_text(stdout, encoding="utf-8", newline="\n")
    events = _events(stdout)
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    cost, priced = x2.price(result)
    if not result:
        # No result event: what was spent is unknown, so the turn's whole budget is booked.
        cost = max(cost, budget_usd)
    ledger.add({"at": datetime.now(timezone.utc).isoformat(), "experiment": experiment, "part": part, "trial_id": trial,
                "out": str(workdir), "cost_usd": cost, **priced})
    summary = summarize(events)
    turn = {"trial_id": trial, "started_at": started_at, "seconds": round(seconds, 1), "exit_code": exit_code,
            "stderr": stderr[-2000:], "cost_usd": cost, "usage": priced, "cli_session_id": summary["session_id"],
            "hub_source_session_id": session_id, "resumed_from": resume, "persisted": bool(persist or resume),
            "tool_call_count": summary["tool_call_count"], "summary": summary,
            "hub_conversation": (_conversation_after(hub_url, project_dir, session_id, request_text, started_at, close_open_turn)
                                 if hub_url and session_id else None)}
    (workdir / "turn.json").write_text(json.dumps(turn, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return turn


def check_budget(ledger: x2.Ledger, part: str, budget_usd: float) -> None:
    """As ``x2.run``: the shared ledger refuses before a call that could cross a cap; a turn may spend its whole budget."""

    ledger.check(part)
    if ledger.spent(part) + budget_usd > x2.CAPS[part]:
        raise x2.CapReached(f"part {part}: {ledger.spent(part):.2f} of {x2.CAPS[part]:.2f} spent; a turn may spend up to {budget_usd:.2f}")
    if ledger.spent() + budget_usd > x2.TOTAL_CAP:
        raise x2.CapReached(f"total: {ledger.spent():.2f} of {x2.TOTAL_CAP:.2f} spent; a turn may spend up to {budget_usd:.2f}")


def _conversation_after(hub_url: str, project_dir: Path, session_id: str, request_text: str, started_at: str,
                        close_open_turn: bool) -> dict[str, Any]:
    """The Hub conversation's side of the turn: the user message the agent published, its status, and who closed the turn."""

    try:
        detail = conversation(hub_url, project_dir, session_id)
    except (X3Error, OSError) as exc:
        return {"error": str(exc)[:500]}
    if detail is None:
        return {"bound": False}
    users = [m for m in detail["messages"] if m.get("role") == "user" and str(m.get("createdAt", "")) >= started_at[:19]]
    answer: dict[str, Any] = {"bound": True, "chat_id": detail["id"], "status_after_turn": detail.get("status"),
                              "user_messages": [{"verbatim": m.get("content") == request_text, "content": m.get("content", "")[:500]} for m in users],
                              "assistant_messages": sum(1 for m in detail["messages"] if m.get("role") == "assistant"),
                              "closed_by": "agent" if detail.get("status") != "running" else None}
    if detail.get("status") == "running" and close_open_turn:
        try:
            answer["closed_by"] = "harness" if close_turn(hub_url, project_dir, session_id) else None
        except (X3Error, OSError) as exc:
            answer["close_error"] = str(exc)[:500]
    return answer


def cli_signed_in(cli: str, parent_env: Mapping[str, str], config_dir: Path | None = None) -> bool:
    """Whether the CLI is signed in with this configuration (``claude auth status``; no model call)."""

    done = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=cli_environment(parent_env, config_dir), timeout=60)
    try:
        return bool(json.loads(done.stdout).get("loggedIn"))
    except ValueError:
        return False


# ---------------------------------------------------------------- the conversation and its result

def conversation(hub_url: str, project_dir: Path, session_id: str) -> dict[str, Any] | None:
    """The Hub conversation an external source session is bound to, with its messages; None before its first bind."""

    rows = _call(hub_url, "/api/chat/sessions")
    row = next((r for r in rows if r.get("sourceSessionId") == session_id
                and Path(r.get("projectDir", "")).resolve() == Path(project_dir).resolve()), None)
    return None if row is None else _call(hub_url, f"/api/chat/sessions/{quote(row['id'])}")


def close_turn(hub_url: str, project_dir: Path, session_id: str, *, status: str = "interrupted",
               note: str = "The host turn ended without closing this conversation turn; closed by the X3 harness.") -> dict[str, Any] | None:
    """Close a conversation turn the agent left running, so the next request can start one; None when none was left open.

    Uses the same external binding the agent used (bind, then present with its token); it writes the conversation
    only, never the project.
    """

    detail = conversation(hub_url, project_dir, session_id)
    if detail is None or detail.get("status") != "running":
        return None
    binding = _call(hub_url, "/api/chat/presentation/bind", "POST",
                    {"projectDir": str(project_dir), "sourceSessionId": session_id, "provider": "claude"})
    turn = next((m.get("sourceTurnId") or m["id"] for m in reversed(detail["messages"]) if m.get("role") == "user"), None)
    body = {"projectId": detail["projectId"], "sourceSessionId": session_id, "turnId": turn, "messageId": str(uuid.uuid4()),
            "kind": "assistant", "content": note, "status": status}
    request = Request(hub_url.rstrip("/") + f"/api/chat/sessions/{quote(binding['chatId'])}/presentation", method="POST",
                      data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Authorization": "Bearer " + binding["token"]})
    with build_opener(ProxyHandler({})).open(request, timeout=60) as response:
        closed = json.load(response)
    return {"chatId": binding["chatId"], "turnId": turn, "status": closed.get("status")}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def preview_model(artifacts: list[dict[str, Any]], run_id: str, state_digest: str | None, project_dir: Path,
                  asset_sha256: str | None = None) -> dict[str, Any] | None:
    """The run's certified ``.preview.3dm`` among ``GET /api/artifacts`` rows (by its exact bytes when they are named)."""

    rows = [row for row in artifacts if row.get("runId") == run_id and row.get("format") == "3dm"
            and row.get("representation") == "preview" and row.get("available")
            and (state_digest is None or row.get("designStateDigest") == state_digest)
            and (asset_sha256 is None or row.get("sha256") == asset_sha256)]
    if not rows:
        return None
    row = rows[-1]
    path = (project_dir / row["relativePath"]) if row.get("relativePath") else None
    verified = path is not None and path.is_file() and _sha256(path) == row.get("sha256")
    return {"path": str(path) if path else None, "sha256": row.get("sha256"), "verified": verified,
            "file_name": row.get("fileName"), "object_count": row.get("objectCount"), "state_digest": row.get("designStateDigest")}


def candidates_made(hub_url: str, runtime_id: str, chat_id: str | None = None) -> list[dict[str, Any]]:
    """The candidates the Hub's operation journal says were made (by one conversation when ``chat_id`` is given), in order."""

    project = _call(hub_url, f"/api/runtime/projects/{quote(runtime_id)}")
    return [{key: row.get(key) for key in ("operationId", "candidateId", "jobId", "sessionId", "sourceRunId", "proposalId", "status")}
            for row in project.get("operations", ()) if row.get("candidateId") and row.get("jobId")
            and (chat_id is None or row.get("sessionId") == chat_id)]


def admitted_runs(runtime_url: str, chat_id: str | None = None) -> list[str]:
    """Runs admitted as a loop's result (``GET /api/admissions``), in order; by one conversation when ``chat_id`` is given."""

    rows = _call(runtime_url, "/api/admissions").get("admissions", [])
    return [result["runId"] for row in rows if chat_id is None or (row.get("messageSource") or {}).get("sessionId") == chat_id
            for result in row.get("results", ()) if result.get("outcome") == "admitted" and result.get("runId")]


def choose_result(head_moved: bool, head_run: str | None, candidates: list[str], admitted: list[str],
                  answer_text: str = "") -> tuple[str | None, str]:
    """Which run stands for a turn's result, and by which rule.

    The working design when the turn moved it; else the last candidate of this conversation that was admitted (the
    agent's own declaration of a finished loop); else the last of its candidates its final answer names; else its
    last candidate. A turn that made none leaves the head as it was.
    """

    if head_moved:
        return head_run, "working_design"
    for rule, pool in (("admitted_candidate", [c for c in candidates if c in admitted]),
                       ("candidate_named_in_answer", [c for c in candidates if c in answer_text]),
                       ("latest_candidate", candidates)):
        if pool:
            return pool[-1], rule
    return head_run, "unchanged"


def result_model(runtime_url: str, project_dir: Path, *, hub_url: str | None = None, runtime_id: str | None = None,
                 session_id: str | None = None, start_run: str = START_RUN, answer_text: str = "") -> dict[str, Any]:
    """The model a turn left: the working design when the turn moved it, else its result candidate (``choose_result``).

    Routes: ``GET /api/working-source?workspace=modeling`` (Runtime) for the Working Head and its exact model source;
    ``GET /api/runtime/projects/{runtimeId}`` (Hub) for the candidates the conversation bound to ``session_id`` made;
    ``GET /api/admissions`` (Runtime) for what it admitted; ``GET /api/candidates/{id}`` (Runtime) for a candidate's
    state digest; ``GET /api/artifacts`` (Runtime) for each run's certified ``.preview.3dm`` (its project-relative path
    resolved under ``project_dir`` and its sha256 checked on disk). ``model`` is None when MonkeyHub exported none.
    """

    project_dir = Path(project_dir)
    working = _call(runtime_url, "/api/working-source?" + urlencode({"workspace": "modeling"}))
    head = working.get("head") or {}
    artifacts = _call(runtime_url, "/api/artifacts").get("artifacts", [])
    candidates: list[dict[str, Any]] = []
    chat = None
    if hub_url and runtime_id:
        chat = conversation(hub_url, project_dir, session_id) if session_id else None
        candidates = candidates_made(hub_url, runtime_id, chat["id"] if chat else None)
    admitted = admitted_runs(runtime_url, chat["id"] if chat else None)
    head_moved = bool(head.get("runId")) and head.get("runId") != start_run
    run_id, how = choose_result(head_moved, head.get("runId") or start_run, [c["candidateId"] for c in candidates], admitted, answer_text)
    source = working.get("source") or {}
    asset = None
    if how == "working_design" or how == "unchanged":
        digest = head.get("stateDigest")
        asset = source.get("assetSha256") if source.get("runId") == run_id else None
    else:
        digest = _call(runtime_url, f"/api/candidates/{quote(run_id)}").get("stateDigest")
    return {"working_head": {k: head.get(k) for k in ("runId", "stateDigest", "origin", "label", "lineage")},
            "working_source": working.get("source"), "head_moved": head_moved, "candidates": candidates, "admitted": admitted,
            "how": how, "run_id": run_id, "state_digest": digest, "model": preview_model(artifacts, run_id, digest, project_dir, asset)}


def lab_realization(project_dir: Path, run_id: str, out_dir: Path) -> dict[str, Any]:
    """Fallback when MonkeyHub exported no model for a run: its retained state record, realized by the lab's pipeline.

    The record is read through the P036 repository port (the run's one ``state-record``), never written; the model
    is the lab's own realization (``pipeline.realize``: compile and OCCT export, no relation gate), so it measures
    the state the turn left, not an artifact MonkeyHub certified. ``out_dir`` must be new.
    """

    from archflow.project.ports import PersistenceArea, PersistenceDestination
    from archflow.project.record_kinds import STATE_RECORD
    from archflow.project.repository import FilesystemProjectRepository
    from archflow.state.state_record import StateRecord

    from labs.revision_tasks import pipeline

    repository = FilesystemProjectRepository.open(Path(project_dir))
    run = repository.load_run(run_id)
    refs = [ref for ref in repository.list_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id))
            if ref.record_kind == STATE_RECORD]
    if len(refs) != 1:
        raise X3Error(f"{run_id}: {len(refs)} retained state records, not one")
    record = StateRecord.from_dict(repository.load_json(refs[0]))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    realization = pipeline.realize(record, out_dir.resolve(), "model")
    return {"run_id": run_id, "record_ref": refs[0].uri, "record_digest": record.digest, "state_digest": record.state_digest,
            "path": str(realization.preview_path), "sha256": _sha256(realization.preview_path),
            "source": "lab pipeline.realize of the run's retained state record (not a MonkeyHub export)"}


def new_session_id() -> str:
    return f"x3-{uuid.uuid4()}"


def studio_write(hub_url: str, runtime_id: str, method: str, path: str, body: dict[str, Any]) -> Any:
    """A Studio write the way the Hub page sends it (the Hub's runtime route, with an idempotency key): a person's act."""

    request = Request(hub_url.rstrip("/") + f"/api/runtime/projects/{quote(runtime_id)}/studio{path}", method=method,
                      data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=120) as response:
            return json.load(response)
    except HTTPError as exc:
        raise X3Error(f"{method} {path}: HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:2000]}") from exc


def continue_on(services: HubServices, run_id: str) -> dict[str, Any]:
    """Move the Working Head onto ``run_id`` as the architect's Continue on the Hub page (no chat message bound)."""

    project_id = json.loads((services.project_dir / "project.json").read_text(encoding="utf-8"))["project_id"]
    base = _call(services.runtime_url, "/api/working-source?" + urlencode({"workspace": "modeling"})).get("revisionSha256")
    answer = studio_write(services.hub_url, services.runtime_id, "PUT", "/api/working-draft",
                          {"projectId": project_id, "runId": run_id, "baseRevisionSha256": base})
    return {k: answer.get(k) for k in ("revisionSha256", "current")}


def attach_model(services: HubServices, path: Path, file_name: str) -> dict[str, Any]:
    """The architect attaches a model file: ``POST /api/model-assets`` with the file's bytes and no run or state, sent as
    the Hub page sends a Studio write (``studio_write``). The Runtime keeps it as an external model asset (its own run
    ``studio-model-<sha256>``, no design state); the asset is checked to be these exact bytes."""

    import base64

    project_id = json.loads((services.project_dir / "project.json").read_text(encoding="utf-8"))["project_id"]
    data = Path(path).read_bytes()
    answer = studio_write(services.hub_url, services.runtime_id, "POST", "/api/model-assets",
                          {"projectId": project_id, "fileName": file_name, "contentBase64": base64.b64encode(data).decode("ascii")})
    sha = hashlib.sha256(data).hexdigest()
    if answer.get("sha256") != sha or answer.get("representation") != "external":
        raise X3Error(f"the attached model was registered as {answer.get('representation')} {answer.get('sha256')}, not external {sha}")
    return {key: answer.get(key) for key in ("artifactId", "runId", "fileName", "sha256", "sizeBytes", "objectCount", "representation",
                                             "designStateDigest", "available", "receiptRef")}


def attachment_line(attached: dict[str, Any]) -> str:
    """The one line the request carries for an attached model: its name and the ids the Runtime gave it."""

    return f"(Attached: {attached['fileName']}, model asset {attached['sha256']}, run {attached['runId']}.)"


def transcript_bytes(session_ids: list[str], projects_root: Path | None = None) -> dict[str, Any]:
    """Read-only: the size of the CLI's transcripts for these session ids in its default projects directory (M+)."""

    root = projects_root if projects_root is not None else Path.home() / ".claude" / "projects"
    files: dict[str, int] = {}
    for session in sorted(set(s for s in session_ids if s)):
        for path in root.glob(f"*/{session}.jsonl"):
            files[str(path)] = path.stat().st_size
        for folder in root.glob(f"*/{session}"):
            if folder.is_dir():
                files.update({str(p): p.stat().st_size for p in folder.rglob("*") if p.is_file()})
    return {"root": str(root), "files": files, "bytes": sum(files.values())}


# ---------------------------------------------------------------- the lab's MCP proxy (call cap; T/H allow-list)

# T and H: construction proposals, their candidates and the model readback only.
T_REQUESTS = (("GET", r"/api/construction/model"), ("GET", r"/api/construction"), ("POST", r"/api/proposals/construction"),
              ("GET", r"/api/proposals/[^/]+"), ("POST", r"/api/proposals/[^/]+/candidate"), ("GET", r"/api/candidates/[^/]+"),
              ("GET", r"/api/candidates/[^/]+/compare"), ("GET", r"/api/jobs/[^/]+"), ("POST", r"/api/project/modeling"))
T_TOOLS = ("studio_request", "studio_schema")
T_NOTE = ("In this session only these Studio actions are available: GET /api/construction/model, GET /api/construction, "
          "POST /api/proposals/construction, GET /api/proposals/{id}, POST /api/proposals/{id}/candidate, GET /api/candidates/{id}, "
          "GET /api/candidates/{id}/compare, GET /api/jobs/{id}, POST /api/project/modeling. Every other action is refused. "
          "The guide below describes the whole Studio.\n\n")
# A file hand-over (A10, B10) in T and H: the read MonkeyHub offers a client for a model asset's own objects, added for
# that request only (harness_added "returned_file_readable"), as (method, path pattern, the path as the note names it).
# The Runtime answers it for a state-bound asset only: an attached file's run has no exact state, so the read is refused
# (REFERENCE_STATE_NOT_EXACT) in M as in T and H, and the index it gives carries no geometry (see README, X3 sessions).
T_HANDOVER_REQUESTS = (("GET", r"/api/model-assets/[0-9a-f]{64}/index", "GET /api/model-assets/{assetSha256}/index"),)
MAX_TOOL_CALLS = 30          # design calls per request (protocol calls do not count); X2's G keeps its own 20
# MonkeyHub's presentation protocol for an external host (bind, then present the user's message and the answer): it does
# no work on the design and SF has none, so these calls do not count toward MAX_TOOL_CALLS.
PROTOCOL_TOOLS = ("presentation_bind", "chat_present")


def proxy_server_config(config_file: Path, python: str = sys.executable) -> dict[str, Any]:
    """The CLI's MCP entry for the lab proxy (``serve_proxy``), configured by ``config_file``."""

    return {"command": python, "args": [str(Path(__file__).resolve()), "proxy", str(config_file)], "alwaysLoad": True}


def proxy_settings(child: dict[str, Any], *, log: Path, restricted: bool, max_calls: int = MAX_TOOL_CALLS,
                   user_message: str | None = None, base_run: str | None = None,
                   extra_requests: tuple[tuple[str, str, str], ...] = ()) -> dict[str, Any]:
    """The proxy's own configuration: the child server it fronts, the call cap, and for T/H the allow-list, the
    user message it publishes into the bound conversation itself (so the agent spends no call on presentation) and
    the run to read and write against when the agent names none (``base_run``, the Working Head). ``extra_requests``
    (T/H only, e.g. ``T_HANDOVER_REQUESTS``) widens this request's allow-list and its note."""

    settings: dict[str, Any] = {"child": [child["command"], *child["args"]], "log": str(log), "max_calls": max_calls}
    if restricted:
        note = T_NOTE
        if extra_requests:
            note = T_NOTE.replace("POST /api/project/modeling. Every other", "POST /api/project/modeling, "
                                  + ", ".join(shown for _, _, shown in extra_requests) + ". Every other")
        settings.update(tools=list(T_TOOLS), requests=[list(r) for r in T_REQUESTS] + [[m, p] for m, p, _ in extra_requests],
                        note=note, drop_instructions=True)
    elif extra_requests:
        raise ValueError("extra_requests widen T/H's allow-list; M has no allow-list")
    if user_message is not None:
        settings["user_message"] = user_message
    if base_run is not None:
        settings["base_run"] = base_run
    return settings


def with_base_run(arguments: dict[str, Any], base_run: str | None) -> tuple[dict[str, Any], bool]:
    """A studio_request read of the construction model, or a construction proposal, that names no run, made against
    ``base_run``. Without a run the Runtime reads and writes its reference run, not the Working Head; MonkeyHub's own
    chat names the source in each turn's prepared context, which T and H do not get."""

    from urllib.parse import parse_qs, urlsplit

    if not base_run:
        return arguments, False
    method, path = str(arguments.get("method", "GET")).upper(), str(arguments.get("path", ""))
    parsed = urlsplit(path)
    if method == "GET" and parsed.path == "/api/construction/model" and "run" not in parse_qs(parsed.query):
        return {**arguments, "path": path + ("&" if parsed.query else "?") + urlencode({"run": base_run})}, True
    body = arguments.get("body")
    if method == "POST" and parsed.path == "/api/proposals/construction" and isinstance(body, dict) and not body.get("sourceRunId"):
        return {**arguments, "body": {**body, "sourceRunId": base_run}}, True
    return arguments, False


def proxy_refusal(settings: dict[str, Any], name: str, arguments: dict[str, Any], calls: int) -> dict[str, Any] | None:
    """Why the proxy refuses this tool call (a MonkeyHub-shaped failure), or None to pass it on."""

    import re
    from urllib.parse import urlsplit

    if name not in PROTOCOL_TOOLS and calls > int(settings.get("max_calls", MAX_TOOL_CALLS)):
        return {"code": "X3_TOOL_BUDGET_SPENT", "detail": f"This request's {settings.get('max_calls', MAX_TOOL_CALLS)} tool calls are "
                "spent. Answer the architect now, without further tools.", "httpStatus": 429}
    tools = settings.get("tools")
    if tools is None:
        return None
    if name not in tools:
        return {"code": "X3_TOOL_UNAVAILABLE", "detail": f"{name} is not available in this session.", "httpStatus": 403}
    method, path = str(arguments.get("method", "GET")).upper(), urlsplit(str(arguments.get("path", ""))).path
    if name == "studio_schema" and not path:
        return {"code": "X3_ACTION_UNAVAILABLE", "detail": "Action discovery is not available in this session; the actions are listed "
                "in studio_request's description.", "httpStatus": 403}
    if not any(m == method and re.fullmatch(pattern, path) for m, pattern in settings.get("requests", ())):
        return {"code": "X3_ACTION_UNAVAILABLE", "detail": f"{method} {path} is not available in this session. Available: "
                + ", ".join(f"{m} {p}" for m, p in settings.get("requests", ())), "httpStatus": 403}
    return None


def serve_proxy(settings: dict[str, Any], stdin=None, stdout=None) -> None:
    """A stdio MCP server in front of another (MonkeyHub's), passing every message on except what it refuses.

    It counts tool calls and refuses those past the cap; with an allow-list (T, H) it lists only the allowed tools,
    puts the session's restriction before studio_request's description, drops the server's presentation
    instructions, refuses every other tool and Studio action, and binds the conversation and publishes the user's
    message itself before the first call. Every call is logged (name, method, path, refusal) to ``settings["log"]``.
    """

    stdin = stdin or _sys.stdin
    stdout = stdout or _sys.stdout
    child = subprocess.Popen(settings["child"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
    log = Path(settings["log"])
    calls = counted = 0
    own = 0
    presented = settings.get("user_message") is None

    def ask_child(message: dict[str, Any]) -> dict[str, Any]:
        child.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        child.stdin.flush()
        line = child.stdout.readline()
        if not line:
            raise X3Error("the MonkeyHub MCP server closed")
        return json.loads(line)

    def own_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal own
        own += 1
        return ask_child({"jsonrpc": "2.0", "id": f"x3-proxy-{own}", "method": "tools/call", "params": {"name": name, "arguments": arguments}})

    def emit(message: dict[str, Any]) -> None:
        stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        stdout.flush()

    def record(row: dict[str, Any]) -> None:
        with log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **row}, ensure_ascii=False) + "\n")

    try:
        for line in stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "id" not in message:                                   # a notification: the server answers none
                child.stdin.write(json.dumps(message) + "\n")
                child.stdin.flush()
                continue
            method, params = message.get("method"), message.get("params") or {}
            if method == "tools/call":
                calls += 1
                name, arguments = str(params.get("name", "")), params.get("arguments") or {}
                if name not in PROTOCOL_TOOLS:
                    counted += 1
                refusal = proxy_refusal(settings, name, arguments, counted)
                row = {"call": calls, "counted": counted, "name": name, "method": arguments.get("method"), "path": arguments.get("path")}
                if refusal is not None:
                    record({**row, "refused": refusal["code"]})
                    emit({"jsonrpc": "2.0", "id": message["id"], "result": {"isError": True, "content": [
                        {"type": "text", "text": json.dumps(refusal, ensure_ascii=False)}]}})
                    continue
                if not presented:
                    presented = True
                    bound = own_call("presentation_bind", {})
                    turn = str(uuid.uuid4())
                    shown = own_call("chat_present", {"turnId": turn, "messageId": str(uuid.uuid4()), "kind": "user",
                                                      "content": settings["user_message"], "status": "complete"})
                    record({"call": 0, "name": "x3-proxy:bind+present", "bound": not bound.get("result", {}).get("isError"),
                            "presented": not shown.get("result", {}).get("isError")})
                rewritten = False
                if name == "studio_request":
                    arguments, rewritten = with_base_run(arguments, settings.get("base_run"))
                    if rewritten:
                        message = {**message, "params": {**params, "arguments": arguments}}
                answer = ask_child(message)
                result = answer.get("result") or {}
                record({**row, "is_error": bool(result.get("isError")), "base_run_added": rewritten,
                        "chars": sum(len(c.get("text", "")) for c in result.get("content", ()) if isinstance(c, dict))})
                emit(answer)
                continue
            answer = ask_child(message)
            if method == "tools/list" and "result" in answer:
                tools = [dict(t) for t in answer["result"].get("tools", ())
                         if settings.get("tools") is None or t.get("name") in settings["tools"]]
                for tool in tools:
                    if tool["name"] == "studio_request" and settings.get("note"):
                        tool["description"] = settings["note"] + tool.get("description", "")
                    tool["_meta"] = {**(tool.get("_meta") or {}), RESULT_SIZE_KEY: RESULT_SIZE_CHARS}
                answer = {**answer, "result": {**answer["result"], "tools": tools}}
            if method == "initialize" and settings.get("drop_instructions") and "result" in answer:
                answer = {**answer, "result": {k: v for k, v in answer["result"].items() if k != "instructions"}}
            emit(answer)
    finally:
        child.stdin.close()
        try:
            child.wait(30)
        except subprocess.TimeoutExpired:
            child.kill()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="X3 MonkeyHub driver pieces run as their own process.")
    sub = parser.add_subparsers(dest="command", required=True)
    proxy = sub.add_parser("proxy", help="serve the lab's MCP proxy on stdio")
    proxy.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    if args.command == "proxy":
        _sys.stdin.reconfigure(encoding="utf-8")
        _sys.stdout.reconfigure(encoding="utf-8")
        serve_proxy(json.loads(args.config.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    _sys.exit(main())
