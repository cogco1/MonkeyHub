"""The intent compiler: an architect's sentence becomes a sentence in the
grammar, and nothing else is allowed to reach the record.

An agent — a local ``codex`` process, or the Anthropic Messages API — is shown
one thing: a *record sheet*, the components, elements, numeric fields,
parameters and honesty lines the projection already answers with, plus the
four forms of the grammar. It answers with one JSON object: a compiled
sentence against one selection, or a question. It never sees the file system,
never runs anything, and never produces a coordinate, a digest or an operator.
What it produced is then handed to the deterministic seam exactly as a typed
sentence would be, so the proposal that comes back is the record's, typed by
the same grammar, refusable by the same questions.

Everything the agent said is kept and shown as the agent's — its ``why``, the
sentence it compiled, which provider and model answered, and how long it took
— so a reader can tell the agent's reading from the record's answer.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Mapping, Protocol, Sequence

from ..transport.errors import BlockedNeedsHuman, StudioError
from .intent import ACCEPTED_FORMS, KEEP_SENTENCE, parse_utterance
from .projection import StateProjection

DETERMINISTIC = "deterministic"
CODEX = "codex"
ANTHROPIC = "anthropic"
PROVIDERS = (DETERMINISTIC, CODEX, ANTHROPIC)

AGENT_FAILED = "INTENT_AGENT_FAILED"

# The one shape the agent may answer in. A model that answers anything else
# has failed, and the failure says so rather than being parsed leniently.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "targetComponentId", "elementId", "utterance", "why", "question"],
    "properties": {
        "status": {"type": "string", "enum": ["compiled", "question"]},
        "targetComponentId": {"type": ["string", "null"]},
        "elementId": {"type": ["string", "null"]},
        "utterance": {"type": ["string", "null"]},
        "why": {"type": "string"},
        "question": {"type": ["string", "null"]},
    },
}

SYSTEM_PROMPT = """You compile an architect's request into ArchFlow's intent grammar.

You are given a RECORD SHEET: the components, elements, numeric fields, parameters and honesty lines a design record declares. You may only name components, elements and fields that appear on the sheet, spelled exactly as they appear. You never invent a field, a component, or a coordinate.

The grammar has four forms (each is a whole sentence):
  set <field> to <number>[ <unit>]
  set <field> = <number>[ <unit>]
  increase <field> by <number> %
  decrease <field> by <number> %
Any of them may end with: keep <ref>[, <ref>...]  — naming what must not change (refs are entity:<elementId> or parameter:<key>).

Rules:
- The field is one of the element's numeric fields (for an element) or one of the record's parameters (when the sheet declares parameters).
- Element fields carry no unit; never write a unit for them. A parameter's unit, if you write one, must be the unit the sheet declares.
- Prefer a relative form (increase/decrease by %) when the request is qualitative ("a little taller"), and say the assumption in `why` (e.g. "a little = +10 %").
- If the request names something the sheet does not have, or needs a decision only the architect can make, answer status "question" with a concrete question naming what is on the sheet.
- If a selection is given, stay on it unless the request clearly names another element on the sheet.
- The sheet's "gestures" are what the architect drew on the model, already resolved to the record's names by the server: "arrow on <element> · world direction +Z (up)" means the architect pointed that element upward (Z is up), "circle covering <component> (...)" names the area they meant, "keep mark on ..." names what must not change (the server adds those keep refs itself; you need not repeat them). Read a gesture as part of the request: an arrow up on an element with a height field and the words "a little" is "increase height by 10 %" on that element. A remove mark has no form in the grammar: answer with a question.
- Answer with the JSON object only. No prose outside it."""


@dataclass(frozen=True, slots=True)
class Selection:
    """What the request was made against: the pick, and what was drawn.

    ``gestures`` are the server's own sentences about the strokes the client
    sent (see ``application/gestures.py``) - resolved through the record,
    never the client's reading. They travel with the selection because they
    are the same kind of thing: context the words were said in.
    """

    component_id: str | None
    element_id: str | None
    gestures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Compilation:
    """What the agent answered, exactly, plus how it was obtained."""

    status: str  # compiled | question
    provider: str
    model: str | None
    utterance: str | None
    component_id: str | None
    element_id: str | None
    why: str
    question: str | None
    latency_ms: int
    prompt_sha256: str | None
    raw: str | None


class IntentCompiler(Protocol):
    def compile(
        self, *, message: str, selection: Selection, projection: StateProjection
    ) -> Compilation: ...


# ---- the record sheet ------------------------------------------------------


def record_sheet(projection: StateProjection, selection: Selection) -> dict[str, Any]:
    """What the agent is allowed to know: the projection, as facts, nothing else."""

    # The kernel's tree when it built; the record's own component entities
    # when it did not — the same ids either way, and never a name from anywhere
    # else.
    if projection.components is not None:
        components = [
            {
                "componentId": component.component_id,
                "semanticKind": component.semantic_kind,
                "intent": component.intent,
            }
            for component in projection.components
        ]
    else:
        components = [
            {
                "componentId": entity.entity_id,
                "semanticKind": entity.fields.get("semantic_kind"),
                "intent": entity.fields.get("intent"),
            }
            for entity in projection.record.entities_of("Component@1")
        ]
    elements = [
        {
            "elementId": element.element_id,
            "componentId": element.component_id,
            "producer": element.producer,
            "numericFields": dict(element.numeric_fields),
        }
        for element in projection.elements
    ]
    parameters = [
        {
            "key": parameter.key,
            "value": parameter.value,
            "unit": parameter.unit,
            "lockAuthority": parameter.lock_authority,
        }
        for parameter in projection.parameters
    ]
    return {
        "projectId": projection.project_id,
        "selection": {
            "componentId": selection.component_id,
            "elementId": selection.element_id,
        },
        "gestures": list(selection.gestures),
        "components": components,
        "elements": elements,
        "parameters": parameters,
        "honesty": list(projection.honesty),
        "grammar": {"forms": list(ACCEPTED_FORMS), "keep": KEEP_SENTENCE},
    }


def _prompt(message: str, sheet: Mapping[str, Any]) -> str:
    return (
        "RECORD SHEET (JSON):\n"
        + json.dumps(sheet, ensure_ascii=False, sort_keys=True)
        + "\n\nREQUEST:\n"
        + message
        + "\n\nAnswer with one JSON object matching the schema."
    )


def _parse_answer(
    raw: str, *, provider: str, model: str | None, latency_ms: int, prompt_sha: str
) -> Compilation:
    """The agent's JSON, checked field by field; anything else is a failure."""

    text = raw.strip()
    # A model that wraps its answer in a fence is answering the question; the
    # fence is stripped, nothing else is.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StudioError(
            502,
            AGENT_FAILED,
            f"the {provider} agent answered something that is not JSON "
            f"({exc.msg} at {exc.pos}); its answer began: {text[:200]!r}",
        ) from exc
    if not isinstance(payload, dict):
        raise StudioError(502, AGENT_FAILED, f"the {provider} agent answered a JSON {type(payload).__name__}, not an object")
    status = payload.get("status")
    if status not in ("compiled", "question"):
        raise StudioError(502, AGENT_FAILED, f"the {provider} agent answered status {status!r}; only compiled or question are answers")

    def text_or_none(key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise StudioError(502, AGENT_FAILED, f"the {provider} agent's {key} is a {type(value).__name__}, not text")
        return value.strip() or None

    why = payload.get("why")
    compilation = Compilation(
        status=status,
        provider=provider,
        model=model,
        utterance=text_or_none("utterance"),
        component_id=text_or_none("targetComponentId"),
        element_id=text_or_none("elementId"),
        why=why.strip() if isinstance(why, str) else "",
        question=text_or_none("question"),
        latency_ms=latency_ms,
        prompt_sha256=prompt_sha,
        raw=raw,
    )
    if compilation.status == "compiled" and compilation.utterance is None:
        raise StudioError(502, AGENT_FAILED, f"the {provider} agent said compiled but produced no sentence")
    if compilation.status == "question" and compilation.question is None:
        raise StudioError(502, AGENT_FAILED, f"the {provider} agent said question but asked none")
    return compilation


# ---- providers -------------------------------------------------------------


class DeterministicCompiler:
    """No agent: the sentence is taken as already compiled."""

    def compile(
        self, *, message: str, selection: Selection, projection: StateProjection
    ) -> Compilation:
        return Compilation(
            status="compiled",
            provider=DETERMINISTIC,
            model=None,
            utterance=message,
            component_id=selection.component_id,
            element_id=selection.element_id,
            why="",
            question=None,
            latency_ms=0,
            prompt_sha256=None,
            raw=None,
        )


class CodexCompiler:
    """``codex exec`` as a subprocess: ephemeral, read-only, schema-bound.

    The agent gets the prompt on stdin, may not run commands (read-only
    sandbox), keeps no session, and must answer in ``RESPONSE_SCHEMA``. Auth is
    the user's own codex login; this process handles no credential.
    """

    def __init__(
        self,
        *,
        executable: str = "codex",
        model: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.executable = executable
        self.model = model
        self.timeout_s = timeout_s

    def compile(
        self, *, message: str, selection: Selection, projection: StateProjection
    ) -> Compilation:
        prompt = SYSTEM_PROMPT + "\n\n" + _prompt(message, record_sheet(projection, selection))
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory(prefix="archflow-intent-") as tmp:
            workdir = Path(tmp)
            schema_path = workdir / "schema.json"
            schema_path.write_text(json.dumps(RESPONSE_SCHEMA), encoding="utf-8")
            answer_path = workdir / "answer.json"
            command = [
                self.executable,
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--color",
                "never",
                "-s",
                "read-only",
                "-C",
                str(workdir),
                "--output-schema",
                str(schema_path),
                "-o",
                str(answer_path),
            ]
            if self.model:
                command += ["-m", self.model]
            command.append("-")  # the prompt arrives on stdin
            started = time.perf_counter()
            try:
                completed = _run_bounded(command, prompt, self.timeout_s)
            except FileNotFoundError as exc:
                raise StudioError(
                    502,
                    AGENT_FAILED,
                    f"the codex executable {self.executable!r} was not found: {exc.strerror}",
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise StudioError(
                    502,
                    AGENT_FAILED,
                    f"codex did not answer within {self.timeout_s:g} s",
                ) from exc
            latency_ms = int((time.perf_counter() - started) * 1000)
            if completed.returncode != 0:
                tail = (completed.stderr or completed.stdout or "").strip()[-600:]
                raise StudioError(
                    502,
                    AGENT_FAILED,
                    f"codex exited with {completed.returncode}: {tail}",
                )
            raw = answer_path.read_text(encoding="utf-8") if answer_path.exists() else completed.stdout
        return _parse_answer(
            raw, provider=CODEX, model=self.model, latency_ms=latency_ms, prompt_sha=prompt_sha
        )


def _run_bounded(
    command: Sequence[str], prompt: str, timeout_s: float
) -> subprocess.CompletedProcess[str]:
    """Run the agent process and, past the timeout, kill it *and its children*.

    ``codex`` is a shim on this machine (``codex.cmd`` → ``cmd.exe`` →
    ``node``), and ``subprocess.run(timeout=...)`` kills only the process it
    started: the grandchild keeps the stdout pipe open and the follow-up read
    blocks until it exits, so the route would sit far past the timeout it
    promised. Here the process starts in its own group (session on POSIX) and
    a timeout ends the whole tree — ``taskkill /T`` on Windows, ``killpg``
    elsewhere — before the pipes are drained. The ``TimeoutExpired`` is
    re-raised so the caller answers as before; nothing here reads the answer.
    """

    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        **popen_kwargs,
    )
    try:
        stdout, stderr = process.communicate(input=prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        # The writers are dead, so this returns as soon as the pipes close; the
        # bound is for a child the kill could not reach, and then the pipes
        # are abandoned rather than waited on.
        try:
            process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _kill_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.kill()


class AnthropicCompiler:
    """The Anthropic Messages API. The key is the SDK's to read from the
    environment; this process never holds, logs or forwards it."""

    def __init__(self, *, model: str, timeout_s: float = 120.0) -> None:
        self.model = model
        self.timeout_s = timeout_s

    def compile(
        self, *, message: str, selection: Selection, projection: StateProjection
    ) -> Compilation:
        try:
            import anthropic  # optional dependency; imported only when chosen
        except ImportError as exc:
            raise StudioError(
                502,
                AGENT_FAILED,
                "the anthropic provider is configured but the anthropic package is not installed",
            ) from exc
        user = _prompt(message, record_sheet(projection, selection))
        prompt_sha = hashlib.sha256((SYSTEM_PROMPT + user).encode("utf-8")).hexdigest()
        started = time.perf_counter()
        try:
            client = anthropic.Anthropic(timeout=self.timeout_s)
            response = client.messages.create(
                model=self.model,
                max_tokens=800,
                system=SYSTEM_PROMPT + "\n\nJSON schema of the only acceptable answer:\n" + json.dumps(RESPONSE_SCHEMA),
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # the SDK's own errors, stated not swallowed
            raise StudioError(
                502, AGENT_FAILED, f"the Anthropic API did not answer: {type(exc).__name__}: {exc}"
            ) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        raw = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        return _parse_answer(
            raw, provider=ANTHROPIC, model=self.model, latency_ms=latency_ms, prompt_sha=prompt_sha
        )


def compiler_from_env(env: Mapping[str, str] = os.environ) -> IntentCompiler:
    """Which compiler this process runs, from ``ARCHFLOW_STUDIO_INTENT_*``."""

    provider = env.get("ARCHFLOW_STUDIO_INTENT_PROVIDER", "").strip() or DETERMINISTIC
    model = env.get("ARCHFLOW_STUDIO_INTENT_MODEL", "").strip() or None
    timeout_text = env.get("ARCHFLOW_STUDIO_INTENT_TIMEOUT_S", "").strip()
    timeout_s = float(timeout_text) if timeout_text else 120.0
    if provider == DETERMINISTIC:
        return DeterministicCompiler()
    if provider == CODEX:
        return CodexCompiler(
            executable=env.get("ARCHFLOW_STUDIO_CODEX", "").strip() or "codex",
            model=model,
            timeout_s=timeout_s,
        )
    if provider == ANTHROPIC:
        return AnthropicCompiler(model=model or "claude-sonnet-5", timeout_s=timeout_s)
    raise ValueError(
        f"ARCHFLOW_STUDIO_INTENT_PROVIDER={provider!r} is not one of {', '.join(PROVIDERS)}"
    )


# ---- the check the seam keeps for itself -----------------------------------


def require_grammatical(compilation: Compilation) -> None:
    """A compiled sentence must parse; an agent that claims it compiled and did
    not is answered like any ungrammatical utterance, with the agent's reading
    kept in the detail so the reader knows who said what."""

    if compilation.status != "compiled":
        raise BlockedNeedsHuman(
            f"the {compilation.provider} agent asked instead of compiling"
            + (f": {compilation.why}" if compilation.why else ""),
            question=compilation.question or "the agent asked a question it did not state",
        )
    assert compilation.utterance is not None
    if parse_utterance(compilation.utterance) is None:
        raise BlockedNeedsHuman(
            f"the {compilation.provider} agent compiled {compilation.utterance!r}, "
            "which is not in the grammar"
            + (f"; it said: {compilation.why}" if compilation.why else ""),
            question=(
                "The agent's sentence could not be typed. Say the change in one of the "
                "four forms, or rephrase the request."
            ),
            accepted_forms=ACCEPTED_FORMS,
        )


def context_refs(
    state_digest: str, compilation: Compilation, fallback: Selection
) -> Sequence[str]:
    """The selection the deterministic seam takes: the agent's, else the request's."""

    component_id = compilation.component_id or fallback.component_id
    element_id = (
        compilation.element_id
        if compilation.component_id is not None
        else fallback.element_id
    )
    refs = [f"state:{state_digest}", f"component:{component_id}"]
    if element_id:
        refs.append(f"element:{element_id}")
    return refs
