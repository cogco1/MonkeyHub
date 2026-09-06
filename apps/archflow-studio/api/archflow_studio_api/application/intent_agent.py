"""Compile an architect's request against one projected design state.

The record sheet exposes components, parameters, types, readings, references,
relationships and producer signatures. A provider returns either one scalar
grammar sentence, a semantic edit of named design data, or a question. The
application types that answer against the existing state and geometry owners;
the provider never supplies a geometry program or project writer.

The answer retains the provider's explanation, model and timing. External
model calls use ModelInvocationRequest and ModelInvocationReceipt at the
INTENT_COMPILATION boundary. The deterministic scalar compiler calls no model
and produces no invocation receipt.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import tempfile
import time
from typing import Any, Mapping, Protocol, Sequence
import uuid

from archflow.contracts.canonical import canonical_digest, canonical_json
from archflow.ports.model import (
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)

from ..settings import INTENT_PROVIDER_ENV, SettingsError, StudioSettings
from ..transport.errors import StudioError
from .intent import ACCEPTED_FORMS, KEEP_SENTENCE
from .projection import StateProjection

DETERMINISTIC = "deterministic"
CODEX = "codex"
ANTHROPIC = "anthropic"
PROVIDERS = (DETERMINISTIC, CODEX, ANTHROPIC)

AGENT_FAILED = "INTENT_AGENT_FAILED"

# What a receipt calls the boundary that was crossed. Not a vendor's product
# name: the pair (provider_id, provider_version) is what a reader compares.
CODEX_PROVIDER_ID = "codex-exec"
ANTHROPIC_PROVIDER_ID = "anthropic-messages"

# The codex arguments that never vary between calls. They are what the
# fingerprint binds, beside the executable, the model and the timeout; the
# per-call paths (the temporary directory, the schema and answer files) name
# one call and are no part of the provider's identity.
CODEX_FIXED_ARGUMENTS = (
    "exec",
    "--ephemeral",
    "--skip-git-repo-check",
    "--ignore-user-config",
    "--color",
    "never",
    "-s",
    "read-only",
)

# What a codex call names as its model when none was configured: the CLI
# chooses one, and the receipt says that rather than inventing an id.
CODEX_DEFAULT_MODEL_ID = "codex-cli-default"

# The Anthropic model a process falls back to when the provider is chosen and
# no model is named. Anthropic's API has no default of its own.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"

# How long ``codex --version`` may take while a compiler is being built. It
# prints a version string; it is not an inference call.
VERSION_PROBE_TIMEOUT_S = 30.0

# The one shape the agent may answer in. A model that answers anything else
# has failed, and the failure says so rather than being parsed leniently.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "targetComponentId", "elementId", "utterance", "semanticEdit", "why", "question"],
    "properties": {
        "status": {"type": "string", "enum": ["compiled", "question"]},
        "targetComponentId": {"type": ["string", "null"]},
        "elementId": {"type": ["string", "null"]},
        "utterance": {"type": ["string", "null"]},
        "semanticEdit": {"type": "null"},
        "why": {"type": "string"},
        "question": {"type": ["string", "null"]},
    },
}


def response_schema(*, strict: bool = True) -> dict[str, Any]:
    """The provider's closed design-input schema, from the producer owner."""

    from archflow.capabilities.element_producers import producer_signatures
    from archflow.relations.contracts import ArchitecturalRelationKind

    text = {"type": "string"}
    nullable_text = {"type": ["string", "null"]}
    strings = {"type": "array", "items": text}

    def object_of(properties, required=None):
        return {
            "type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required,
        }

    element_fields = []
    type_fields = []
    for producer, signature in producer_signatures().items():
        element_fields.append(object_of({
            "component_id": text,
            "producer": {"type": "string", "enum": [producer]},
            "type_ref": nullable_text,
            "references": signature["references"],
            "params": signature["parameters"],
            "name": nullable_text,
            "label": nullable_text,
            "note": nullable_text,
        }, ["component_id", "producer", "references", "params"]))
        type_fields.append(object_of({
            "producer": {"type": "string", "enum": [producer]},
            "references": signature["references"], "params": signature["parameters"],
            "name": nullable_text, "label": nullable_text, "note": nullable_text,
        }, ["producer", "references", "params"]))
    entity_variants = [object_of({
        "entity_id": text, "schema": {"type": "string", "enum": ["Element@1"]},
        "parent_id": nullable_text, "basis_refs": strings,
        "fields": {"anyOf": element_fields},
    })]
    entity_variants.append(object_of({
        "entity_id": text, "schema": {"type": "string", "enum": ["Component@1"]},
        "parent_id": nullable_text, "basis_refs": strings,
        "fields": object_of({"semantic_kind": text, "intent": text, "source_refs": strings}),
    }))
    entity_variants.append(object_of({
        "entity_id": text, "schema": {"type": "string", "enum": ["Type@1"]},
        "parent_id": nullable_text, "basis_refs": strings,
        "fields": {"anyOf": type_fields},
    }))
    parameter = object_of({
        "key": text, "value": {"type": "number"}, "unit": text,
        "expr": nullable_text, "inputs": strings,
        "epistemic_status": {"type": "string", "enum": ["declared", "derived", "hypothesis"]},
        "source_ref": nullable_text,
    })
    relation = object_of({
        "relation_id": text,
        "kind": {"type": "string", "enum": [kind.value for kind in ArchitecturalRelationKind]},
        "subject": text, "object": text, "datum_role": nullable_text,
        "propagation": {"type": "string", "enum": ["unchanged", "revalidate", "invalidate"]},
        "validator": {"anyOf": [{"type": "null"}, object_of({
            "check_kind": {"type": "string", "enum": ["support_contact", "aperture_exists"]},
            "tolerance": {"type": "number"},
        }), object_of({
            "check_kind": {"type": "string", "enum": ["clearance_interval"]},
            "interval_m": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        })]},
        "parameters": object_of({
            "engagement_depth": {"type": "number"}, "rise": {"type": "number"},
        }, []),
        "epistemic_status": {"type": "string", "enum": ["declared", "derived", "hypothesis"]},
        "basis_refs": strings,
    })
    edit = object_of({
        "summary": text,
        "entities": {"type": "array", "items": {"anyOf": entity_variants}},
        "parameters": {"type": "array", "items": parameter},
        "relations": {"type": "array", "items": relation},
        "removeEntityIds": strings, "removeParameterKeys": strings, "removeRelationIds": strings,
        "protected": strings, "kept": strings,
    })
    schema = {
        **RESPONSE_SCHEMA,
        "properties": {**RESPONSE_SCHEMA["properties"], "semanticEdit": {"anyOf": [{"type": "null"}, edit]}},
    }
    return _strict_response_schema(schema) if strict else schema


def _strict_response_schema(value: Any) -> Any:
    """Represent optional signature fields as null for strict model output.

    The domain signature remains unchanged. Optional nulls are removed from
    params/references when reading the answer, before the owner validates it.
    """

    if isinstance(value, list):
        return [_strict_response_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _strict_response_schema(item) for key, item in value.items()}
    if value.get("type") == "object" and "properties" in value:
        required = set(value.get("required", ()))
        result["properties"] = {
            key: item if key in required else {"anyOf": [item, {"type": "null"}]}
            for key, item in result["properties"].items()
        }
        result["required"] = list(result["properties"])
    return result


def _present_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _present_fields(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_present_fields(item) for item in value]
    return value

SYSTEM_PROMPT = """You compile an architect's request into typed architectural design changes.

You are given a RECORD SHEET containing the current components, elements, parameter bindings, reference frame, named relationships, project types, design readings, and the available producer signatures. Use this one design state. Existing references must name it or another item declared in the same edit. New elements may have new meaningful ids. Only the producer signatures on the sheet define supported element parameters and references. Never emit a GeometryProgram, CAD command, profile/loft vertex array, deferred restoration payload, or a replay recipe.

Reference names follow the existing resolver: axis_point.axis and grid references use GridAxis@1 fields.role, never the GridAxis entity_id. Level references use the Level@1 entity_id. Read these exact names from frame; do not substitute an entity id for a grid role.

For adding, removing, or changing components, their parameter bindings, openings or relationships, answer status "compiled" with semanticEdit and utterance null. semanticEdit contains only named Entity, Parameter and Relation edits and explicit removal lists. Reuse the project's Type definitions through type_ref, its authored parameters through @key, and its references. Explain the proposed building change briefly in summary. Give human-readable retained conditions in kept and bind them to actual entity:/parameter: refs in protected. Update or remove relationships together with the elements they refer to. Do not add a new dependency table; those references and relationships are the dependency declaration. Never invent source evidence. Cite the sheet's basis refs or studio:intent for a new decision requested here. A Reading is evidence/context, not a command: its assumptions stay assumptions.

An existing entity is upserted. Each supplied params or references object replaces that whole declared object; include all its current declared members from the sheet, with the requested changes, rather than only the changed nested member. A type_ref instance may supply only its own declared overrides and inherit the rest from its Type. Omitting optional values with null does not erase inherited declarations.

A protected entity means its whole dependency closure must remain untouched, including new or changed relationships that reach it. Protecting an existing landing entity can therefore conflict with adding a new support relation to that landing even if its own fields are unchanged. When the architect preserves specific dimensions or levels, protect their actual parameter or level controls and state exactly those conditions in kept. Never silently weaken a request to keep an entire entity into a narrower parameter protection.

A current pick or circle is context about the requested area, not an instruction to change its numeric values. When the request says to add something missing while preserving a stair's width, landing height and step relationship, propose the missing architectural members and protect those existing controls. Do not substitute a scalar edit on the selected stair. Missing information warrants a question only when it changes the design; describe the design choice naturally, never ask for an element id, producer name, schema or field name.

For changing one existing numeric value, semanticEdit is null and utterance uses one of these four forms:
  set <field> to <number>[ <unit>]
  set <field> = <number>[ <unit>]
  increase <field> by <number> %
  decrease <field> by <number> %
Any of them may end with: keep <ref>[, <ref>...]  — naming what must not change (refs are entity:<elementId> or parameter:<key>).

Rules:
- The field is one of the element's numeric fields (for an element) or one of the record's parameters (when the sheet declares parameters).
- Element fields carry no unit; never write a unit for them. A parameter's unit, if you write one, must be the unit the sheet declares.
- Prefer a relative form (increase/decrease by %) when the request is qualitative ("a little taller"), and say the assumption in `why` (e.g. "a little = +10 %").
- If the available signatures and design context cannot express the request, explain what architectural information is missing. Never offer an unrelated numeric control as a substitute.
- If a selection is given, stay on it unless the request clearly names another element on the sheet.
- The sheet's "gestures" are what the architect drew on the model, already resolved to the record's names by the server: "arrow on <element> · world direction +Z (up)" means the architect pointed that element upward (Z is up), "circle covering <component> (...)" names the area they meant, "keep mark on ..." names what must not change (the server adds those keep refs itself; you need not repeat them). Read a gesture as part of the request: an arrow up on an element with a height field and the words "a little" is "increase height by 10 %" on that element. A remove mark with an unambiguous bound target can populate semanticEdit.removeEntityIds; update its affected references and relationships together. Ask only when the target or resulting design is ambiguous.
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


class IntentAgentFailed(StudioError):
    """A model call that failed, carrying the receipt that says how it failed.

    The wire body is the one it always was — 502 ``INTENT_AGENT_FAILED`` with
    the same sentence — so no client sees a new shape. The receipt is for this
    side of the wire: a call that reached the provider and came back a
    timeout, a non-zero exit or an unreadable answer still crossed the
    boundary, and only a receipt can say which of the three it was.
    """

    def __init__(self, detail: str, receipt: ModelInvocationReceipt) -> None:
        super().__init__(502, AGENT_FAILED, detail)
        self.receipt = receipt


@dataclass(frozen=True, slots=True)
class _ProviderBinding:
    """Who a receipt says answered, fixed when the compiler is built.

    ``fingerprint`` is a digest over exactly what would make two calls
    incomparable: the provider, the model, the provider's own version, the
    timeout, and — for a subprocess provider — the executable and the fixed
    argument list. Same configuration, same fingerprint; a different model, a
    different one.
    """

    provider_id: str
    model_id: str
    version: str
    fingerprint: str


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
    # The receipt of the model call this answer came out of, on the shared
    # ``ModelInvocationReceipt@2`` contract. ``None`` when no model was
    # called, which is the deterministic compiler's whole case.
    receipt: ModelInvocationReceipt | None = None
    semantic_edit: Mapping[str, Any] | None = None


class IntentCompiler(Protocol):
    def compile(
        self, *, message: str, selection: Selection, projection: StateProjection
    ) -> Compilation: ...


# ---- the record sheet ------------------------------------------------------


def record_sheet(projection: StateProjection, selection: Selection) -> dict[str, Any]:
    """What the agent is allowed to know: the projection, as facts, nothing else."""

    from archflow.capabilities.element_producers import producer_signatures
    from archflow.semantics.registry import registered_ids

    signatures = producer_signatures()
    authored = {entity.entity_id: entity for entity in projection.record.entities}

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
            "parameterBindings": dict(element.bindings),
            "references": dict(authored[element.element_id].fields.get("references", {})),
            "params": {
                key: value
                for key, value in authored[element.element_id].fields.get("params", {}).items()
                if key in signatures.get(element.producer, {}).get("parameters", {}).get("properties", {})
                or isinstance(value, (str, int, float, bool))
            },
            "typeRef": authored[element.element_id].fields.get("type_ref"),
            "basisRefs": list(authored[element.element_id].basis_refs),
        }
        for element in projection.elements
    ]
    parameters = [
        {
            "key": parameter.key,
            "value": parameter.value,
            "unit": parameter.unit,
            "lockAuthority": parameter.lock_authority,
            "expr": parameter.expr,
            "inputs": list(parameter.reads()),
            "sourceRef": parameter.source_ref,
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
        "producerSignatures": signatures,
        "semanticIds": list(registered_ids()),
        "frame": [
            entity.to_dict() for entity in projection.record.entities
            if entity.schema in {"Level@1", "GridAxis@1"}
        ],
        "types": [entity.to_dict() for entity in projection.record.entities_of("Type@1")],
        "readings": [entity.to_dict() for entity in projection.record.entities_of("Reading@1")],
        "relationships": [relation.to_dict() for relation in projection.record.relations],
        "basisRefs": sorted({
            "studio:intent", *projection.record.basis_refs, *projection.record.evidence_refs,
            *(ref for entity in projection.record.entities for ref in entity.basis_refs),
        }),
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


# ---- the shared model contract ---------------------------------------------


def _invocation_request(
    *,
    message: str,
    selection: Selection,
    projection: StateProjection,
    sheet: Mapping[str, Any],
) -> ModelInvocationRequest:
    """The request every Studio model call is bound to, in the shared contract.

    The checkpoint is the projection's ``state_digest`` — the number a runner
    receipt carries — and, when the kernel would not build a bound view and
    there is none, the record's own content digest. Those are the two
    identities the request already has; nothing here computes a third
    (ADR-003).
    """

    payload = {
        "message": message,
        "selection": {
            "component_id": selection.component_id,
            "element_id": selection.element_id,
            "gestures": list(selection.gestures),
        },
        "record_sheet": dict(sheet),
    }
    return ModelInvocationRequest.create(
        request_id=f"intent-{uuid.uuid4().hex}",
        phase=ModelPhase.INTENT_COMPILATION,
        checkpoint_digest=projection.state_digest or projection.record_digest,
        context_digest=canonical_digest(payload, ascii=False),
        payload=payload,
    )


def _answer_object(compilation: Compilation) -> dict[str, Any]:
    """The agent's answer as the schema's own object, after the field checks."""

    return {
        "status": compilation.status,
        "targetComponentId": compilation.component_id,
        "elementId": compilation.element_id,
        "utterance": compilation.utterance,
        "semanticEdit": None if compilation.semantic_edit is None else dict(compilation.semantic_edit),
        "why": compilation.why,
        "question": compilation.question,
    }


def _model_receipt(
    binding: _ProviderBinding,
    request: ModelInvocationRequest,
    *,
    status: ModelInvocationStatus,
    prompt: str,
    raw: str | None,
    output: Mapping[str, Any] | None,
    duration_ms: int,
    error_code: str | None = None,
    message: str | None = None,
) -> ModelInvocationReceipt:
    """One receipt of one call: what was sent, what came back, and how it ended.

    ``output_sha256`` is over the answer exactly as the provider wrote it;
    ``output`` is that answer decoded, and it is present only on a success —
    a timeout or a non-zero exit carries an ``error_code`` and no output,
    which is the receipt's own rule.
    """

    answer = b"" if raw is None else raw.encode("utf-8")
    output_sha256 = None if raw is None else hashlib.sha256(answer).hexdigest()
    # The receipt's own bound: a sentence, not a transcript.
    said = (message or "").strip()[:1_000] or None
    identity = {
        "request": request.to_dict(),
        "provider_fingerprint": binding.fingerprint,
        "status": status.value,
        "output_sha256": output_sha256,
        "error_code": error_code,
        "duration_ms": duration_ms,
    }
    return ModelInvocationReceipt(
        receipt_id=f"intent-{canonical_digest(identity, ascii=False)[:24]}",
        status=status,
        request=request,
        provider_id=binding.provider_id,
        model_id=binding.model_id,
        provider_version=binding.version,
        provider_fingerprint=binding.fingerprint,
        input_bytes=len(prompt.encode("utf-8")),
        output_bytes=len(answer),
        output_sha256=output_sha256,
        duration_ms=duration_ms,
        output_json=(
            None if output is None else canonical_json(dict(output), ascii=False)
        ),
        error_code=error_code,
        message=said,
    )


def _failed(
    binding: _ProviderBinding,
    request: ModelInvocationRequest,
    *,
    status: ModelInvocationStatus,
    prompt: str,
    duration_ms: int,
    error_code: str,
    detail: str,
    raw: str | None = None,
) -> IntentAgentFailed:
    """The refusal a caller sees, with the receipt of the call behind it."""

    return IntentAgentFailed(
        detail,
        _model_receipt(
            binding,
            request,
            status=status,
            prompt=prompt,
            raw=raw,
            output=None,
            duration_ms=duration_ms,
            error_code=error_code,
            message=detail,
        ),
    )


def _parse_answer(
    raw: str,
    *,
    provider: str,
    model: str | None,
    latency_ms: int,
    prompt_sha: str,
    receipt: ModelInvocationReceipt | None = None,
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
    semantic_edit = payload.get("semanticEdit")
    if semantic_edit is not None and not isinstance(semantic_edit, dict):
        raise StudioError(502, AGENT_FAILED, f"the {provider} agent's semanticEdit is not an object")
    if semantic_edit is not None:
        # Strict provider schemas spell absent optional signature keys as
        # null. Their domain spelling is absence, so type defaults still work.
        for entity in semantic_edit.get("entities", ()):
            if isinstance(entity, dict) and isinstance(entity.get("fields"), dict):
                entity["fields"] = _present_fields(entity["fields"])
        for relation in semantic_edit.get("relations", ()):
            if isinstance(relation, dict) and isinstance(relation.get("parameters"), dict):
                relation["parameters"] = _present_fields(relation["parameters"])
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
        receipt=receipt,
        semantic_edit=semantic_edit,
    )
    if compilation.status == "compiled" and (compilation.utterance is None) == (semantic_edit is None):
        raise StudioError(502, AGENT_FAILED, f"the {provider} agent must compile exactly one scalar sentence or semantic edit")
    if compilation.status == "question" and semantic_edit is not None:
        raise StudioError(502, AGENT_FAILED, f"the {provider} agent asked a question and also supplied an edit")
    if compilation.status == "question" and compilation.question is None:
        raise StudioError(502, AGENT_FAILED, f"the {provider} agent said question but asked none")
    return compilation


# ---- providers -------------------------------------------------------------


class DeterministicCompiler:
    """No agent: the sentence is taken as already compiled.

    It invokes no model, so it mints no receipt: there is no boundary to
    prove a crossing of, and a receipt of a call that never happened would be
    the one thing a reader could not tell from a real one.
    """

    provider = DETERMINISTIC
    model: str | None = None

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


def _codex_version(executable: str) -> str:
    """The first line of ``codex --version``, taken once, or a refusal.

    A provider that cannot say which version it is cannot sign a receipt: the
    version and the fingerprint over it are how two calls are told apart
    later. So the compiler refuses to be built rather than writing "unknown"
    into every receipt it would go on to mint.
    """

    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERSION_PROBE_TIMEOUT_S,
            shell=False,
            check=False,
        )
    except OSError as exc:
        raise SettingsError(
            f"the codex executable {executable!r} could not be run to read its "
            f"version: {type(exc).__name__}: {exc.strerror or exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SettingsError(
            f"{executable!r} did not print a version within "
            f"{VERSION_PROBE_TIMEOUT_S:g} s"
        ) from exc
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-300:]
        raise SettingsError(
            f"{executable!r} --version exited with {completed.returncode}: {tail}"
        )
    lines = [line.strip() for line in (completed.stdout or "").splitlines()]
    first = next((line for line in lines if line), "")
    if not first:
        raise SettingsError(f"{executable!r} --version printed nothing")
    return first[:1_000]


class CodexCompiler:
    """``codex exec`` as a subprocess: ephemeral, read-only, schema-bound.

    The agent gets the prompt on stdin, may not run commands (read-only
    sandbox), keeps no session, and must answer in ``RESPONSE_SCHEMA``. Auth is
    the user's own codex login; this process handles no credential.

    Building one runs ``codex --version`` once: every receipt this compiler
    mints is signed with that version, and a codex that will not say which one
    it is refuses the compiler here rather than at the first sentence.
    """

    provider = CODEX

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
        version = _codex_version(executable)
        model_id = model or CODEX_DEFAULT_MODEL_ID
        self.binding = _ProviderBinding(
            provider_id=CODEX_PROVIDER_ID,
            model_id=model_id,
            version=version,
            fingerprint=canonical_digest(
                {
                    "provider_id": CODEX_PROVIDER_ID,
                    "model_id": model_id,
                    "provider_version": version,
                    "timeout_s": float(timeout_s),
                    "executable": executable,
                    "arguments": list(CODEX_FIXED_ARGUMENTS),
                },
                ascii=False,
            ),
        )

    def compile(
        self, *, message: str, selection: Selection, projection: StateProjection
    ) -> Compilation:
        sheet = record_sheet(projection, selection)
        prompt = SYSTEM_PROMPT + "\n\n" + _prompt(message, sheet)
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        request = _invocation_request(
            message=message,
            selection=selection,
            projection=projection,
            sheet=sheet,
        )
        with tempfile.TemporaryDirectory(prefix="archflow-intent-") as tmp:
            workdir = Path(tmp)
            schema_path = workdir / "schema.json"
            schema_path.write_text(json.dumps(response_schema()), encoding="utf-8")
            answer_path = workdir / "answer.json"
            command = [self.executable, *CODEX_FIXED_ARGUMENTS]
            command += [
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
                raise _failed(
                    self.binding,
                    request,
                    status=ModelInvocationStatus.EXIT_ERROR,
                    prompt=prompt,
                    duration_ms=_elapsed_ms(started),
                    error_code="model.executable_missing",
                    detail=(
                        f"the codex executable {self.executable!r} was not "
                        f"found: {exc.strerror}"
                    ),
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise _failed(
                    self.binding,
                    request,
                    status=ModelInvocationStatus.TIMEOUT,
                    prompt=prompt,
                    duration_ms=_elapsed_ms(started),
                    error_code="model.timeout",
                    detail=f"codex did not answer within {self.timeout_s:g} s",
                ) from exc
            latency_ms = _elapsed_ms(started)
            if completed.returncode != 0:
                tail = (completed.stderr or completed.stdout or "").strip()[-600:]
                raise _failed(
                    self.binding,
                    request,
                    status=ModelInvocationStatus.EXIT_ERROR,
                    prompt=prompt,
                    duration_ms=latency_ms,
                    error_code="model.provider_exit",
                    detail=f"codex exited with {completed.returncode}: {tail}",
                )
            raw = answer_path.read_text(encoding="utf-8") if answer_path.exists() else completed.stdout
        return _answered(
            self.binding,
            request,
            raw=raw,
            prompt=prompt,
            prompt_sha=prompt_sha,
            provider=CODEX,
            model=self.model,
            duration_ms=latency_ms,
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _answered(
    binding: _ProviderBinding,
    request: ModelInvocationRequest,
    *,
    raw: str,
    prompt: str,
    prompt_sha: str,
    provider: str,
    model: str | None,
    duration_ms: int,
) -> Compilation:
    """The provider answered: type the answer, then sign what came back.

    An answer that is not the schema is a failed call, not a bug: it becomes a
    ``MALFORMED`` receipt carrying the bytes that did arrive, and the refusal
    keeps the sentence the field checks wrote.
    """

    try:
        compilation = _parse_answer(
            raw,
            provider=provider,
            model=model,
            latency_ms=duration_ms,
            prompt_sha=prompt_sha,
        )
    except StudioError as exc:
        raise IntentAgentFailed(
            exc.detail,
            _model_receipt(
                binding,
                request,
                status=ModelInvocationStatus.MALFORMED,
                prompt=prompt,
                raw=raw,
                output=None,
                duration_ms=duration_ms,
                error_code="model.output_malformed",
                message=exc.detail,
            ),
        ) from exc
    return replace(
        compilation,
        receipt=_model_receipt(
            binding,
            request,
            status=ModelInvocationStatus.SUCCESS,
            prompt=prompt,
            raw=raw,
            output=_answer_object(compilation),
            duration_ms=duration_ms,
        ),
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
    elsewhere — before the pipes are drained.

    The prompt is fed from its own thread. ``communicate(input=...)`` writes
    stdin on the calling thread on Windows, and a sheet larger than the pipe
    buffer (the catalog made it so) blocks that write until the child reads or
    dies - the timeout was never consulted. Readers run on threads too, so a
    child that fills stdout cannot wedge the wait. The ``TimeoutExpired`` is
    raised so the caller answers as before; nothing here reads the answer.
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
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    captured: dict[str, str] = {"stdout": "", "stderr": ""}

    def feed() -> None:
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def drain(name: str, stream: Any) -> None:
        try:
            captured[name] = stream.read()
        except (OSError, ValueError):
            pass

    threads = [
        threading.Thread(target=feed, name="agent-stdin", daemon=True),
        threading.Thread(target=drain, args=("stdout", process.stdout), name="agent-stdout", daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), name="agent-stderr", daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        # The tree is dead, so this returns at once; the bound is for a child
        # the kill could not reach, and then the pipes are abandoned to their
        # daemon threads rather than waited on.
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass
        raise subprocess.TimeoutExpired(command, timeout_s)
    for thread in threads:
        thread.join(timeout=5.0)
    return subprocess.CompletedProcess(command, process.returncode, captured["stdout"], captured["stderr"])


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


def _anthropic_sdk() -> tuple[Any, str]:
    """The SDK module and the version its receipts are signed with, or a refusal."""

    try:
        import anthropic  # optional dependency; imported only when chosen
    except ImportError as exc:
        raise SettingsError(
            "the anthropic provider is configured but the anthropic package "
            "is not installed"
        ) from exc
    version = getattr(anthropic, "__version__", "")
    if not isinstance(version, str) or not version.strip():
        raise SettingsError(
            "the installed anthropic package does not name its version, so a "
            "receipt of a call to it could not say which one answered"
        )
    return anthropic, version.strip()[:1_000]


class AnthropicCompiler:
    """The Anthropic Messages API. The key is the SDK's to read from the
    environment; this process never holds, logs or forwards it.

    Building one resolves the SDK and its ``__version__`` once, for the same
    reason ``CodexCompiler`` runs ``codex --version``: that string is what the
    receipts are signed with, and a provider that cannot name its version is
    refused here rather than at the first sentence.
    """

    provider = ANTHROPIC

    def __init__(self, *, model: str, timeout_s: float = 120.0) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self._sdk, version = _anthropic_sdk()
        self.binding = _ProviderBinding(
            provider_id=ANTHROPIC_PROVIDER_ID,
            model_id=model,
            version=version,
            fingerprint=canonical_digest(
                {
                    "provider_id": ANTHROPIC_PROVIDER_ID,
                    "model_id": model,
                    "provider_version": version,
                    "timeout_s": float(timeout_s),
                },
                ascii=False,
            ),
        )

    def compile(
        self, *, message: str, selection: Selection, projection: StateProjection
    ) -> Compilation:
        sheet = record_sheet(projection, selection)
        user = _prompt(message, sheet)
        prompt = SYSTEM_PROMPT + user
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        request = _invocation_request(
            message=message,
            selection=selection,
            projection=projection,
            sheet=sheet,
        )
        started = time.perf_counter()
        try:
            client = self._sdk.Anthropic(timeout=self.timeout_s)
            response = client.messages.create(
                model=self.model,
                max_tokens=5000,
                system=SYSTEM_PROMPT + "\n\nJSON schema of the only acceptable answer:\n" + json.dumps(response_schema()),
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # the SDK's own errors, stated not swallowed
            timeout_error = getattr(self._sdk, "APITimeoutError", None)
            timed_out = timeout_error is not None and isinstance(exc, timeout_error)
            raise _failed(
                self.binding,
                request,
                status=(
                    ModelInvocationStatus.TIMEOUT
                    if timed_out
                    else ModelInvocationStatus.EXIT_ERROR
                ),
                prompt=prompt,
                duration_ms=_elapsed_ms(started),
                error_code="model.timeout" if timed_out else "model.provider_error",
                detail=f"the Anthropic API did not answer: {type(exc).__name__}: {exc}",
            ) from exc
        latency_ms = _elapsed_ms(started)
        raw = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        return _answered(
            self.binding,
            request,
            raw=raw,
            prompt=prompt,
            prompt_sha=prompt_sha,
            provider=ANTHROPIC,
            model=self.model,
            duration_ms=latency_ms,
        )


def compiler_from_settings(settings: StudioSettings) -> IntentCompiler:
    """Which compiler this process runs, from the settings it was built with.

    The four ``ARCHFLOW_STUDIO_INTENT_*`` variables are read once, by
    ``StudioSettings.from_env``; nothing here reads the environment, so a test
    or a second process configures a compiler by passing settings.
    """

    provider = settings.intent_provider
    if provider == DETERMINISTIC:
        return DeterministicCompiler()
    if provider == CODEX:
        return CodexCompiler(
            executable=settings.codex_executable,
            model=settings.intent_model,
            timeout_s=settings.intent_timeout_s,
        )
    if provider == ANTHROPIC:
        return AnthropicCompiler(
            model=settings.intent_model or DEFAULT_ANTHROPIC_MODEL,
            timeout_s=settings.intent_timeout_s,
        )
    raise SettingsError(
        f"{INTENT_PROVIDER_ENV}={provider!r} is not one of {', '.join(PROVIDERS)}"
    )


# ---- the selection the seam hands on ---------------------------------------

# ``require_grammatical`` used to live here. It raised a bare
# ``BLOCKED_NEEDS_HUMAN`` and threw away the two things the agent had just
# worked out — which component and which element it was talking about — so the
# next request began from nothing and asked the same question again.
# ``clarification.read_compilation`` replaces it: the same two refusals, in the
# same words, with the target kept and the exchange it belongs to named.


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
