"""Scoped project decisions: what the architect settled, and against what.

An architect says "don't hatch this wall", "stop writing it that way", "keep
this span". Today those sentences live in one conversation and are gone by the
next turn. This retains each one as an immutable revision in one fixed
explicit run — ``studio-decisions`` — through the same P036 ports every other
Studio record uses. Nothing here is a second canonical state: a decision
accepts no Stage, moves no HEAD, acquires and releases no lock, and produces
no geometry. It records a judgement, the exact evidence it was made against,
and how far it reaches.

Three things travel with every decision and none of them is derived: the raw
wording, the exact source it was said about (a retained board revision and its
named elements, a registered document page at an exact revision, or a real
retained design run and its state digest), and the scope it claims. A later
turn asks for the ones that still apply to what it is doing, and gets the
architect's own words back with their provenance — not a transcript.

A drawing decision can also be the project recipe (correction capture,
option A): a ``require`` decision whose typed binding is ``recipe`` - the
paper-space values a new drawing starts from. It is not a second memory. A
person promotes it explicitly, it revokes and supersedes like every other
decision, and ``project_recipe`` is the one read of it.

A recipe can travel (#252): ``recipe_export`` writes one as a small file that
carries its values and identity and nothing else of its project, and
``import_recipe`` retains such a file in another project as that project's
preference, on a person's confirmation, evidenced by the file's own digest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping, Sequence
import uuid

from pydantic import ValidationError

from archflow.contracts.canonical import CanonicalValueError, canonical_digest
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_SCOPED_DECISION
from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import ProjectRepositoryError
from archflow.state.state_record import (
    Parameter,
    StateRecord,
    StateRecordError,
    parameter_bindings_of,
)

from ..transport.errors import StudioError
from .artifacts import document_bytes
from .authentication import ActorAttribution
from .binding import ProjectBinding, retained_sources
from .boards import BOARD_RUN_ID, read_board
from .projection import project_state, require_actionable

DECISIONS_RUN_ID = "studio-decisions"
DECISION_SCHEMA = "StudioScopedDecision@1"

_DESIGN_TARGET = re.compile(r"(parameter|entity|relation):([A-Za-z0-9][A-Za-z0-9._-]{0,127})")
# What a representation decision is about, from a closed set per domain. The
# drawing targets are the corrections an architect keeps making (#244, #223):
# hatch density, the line-weight hierarchy, what lies beyond the cut,
# entourage and poché.
DRAWING_TARGETS = ("drawing:hatch", "drawing:lineweight", "drawing:beyond", "drawing:entourage", "drawing:poche")
_DOMAIN_TARGETS = {"drawing": DRAWING_TARGETS, "copy": ("copy:style",)}
_DOMAIN_SOURCES = {"drawing": frozenset({"board", "document"}), "copy": frozenset({"document"})}
# The paper-space values a project recipe can set, each under the one drawing
# target it belongs to. The wire bounds them exactly as a drawing request does.
RECIPE_KEYS = {"cutLineMm": "drawing:lineweight", "visibleLineMm": "drawing:lineweight",
               "hatchSpacingMm": "drawing:hatch"}
# The order a new drawing reads recipes in. A standard (hard) comes first but is
# not enforced (D-05-3). A temporary correction is not memory: it stays on its
# own drawing as a local override.
RECIPE_STRENGTHS = ("hard", "strong_preference", "soft_preference")
# The one file form a recipe travels between projects in (#252), and the source
# a recipe imported from one cites. Another project's recipe is a preference
# here, for the whole project, until a person confirms it on this project's own
# page: that is the one hold and reach an import takes.
RECIPE_EXPORT_SCHEMA = "DrawingRecipeExport@1"
RECIPE_EXPORT = "recipe-export"
IMPORTED_RECIPE_STRENGTH = "soft_preference"
_EXPORT_FIELDS = frozenset({"schema", "recipe", "source", "sha256"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
# A turn that names no domain reads the design decisions and the drawing ones
# that reach it, so the agent drawing next sees the recipe a new drawing
# starts from. Copy stays opt-in.
_DEFAULT_DOMAINS = ("design", "drawing")

ACTIVE = "active"
DEFERRED = "deferred"
REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class DecisionRevision:
    """One retained revision: the P036 ref it is at, and what it says."""

    ref: str
    payload: Mapping[str, Any]

    @property
    def decision_id(self) -> str:
        return str(self.payload["decisionId"])

    @property
    def status(self) -> str:
        return str(self.payload["status"])


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """What a next turn is asking about, as its own evidence states it.

    ``domains`` holds the one domain a turn named, or the default pair a turn
    that named none reads.
    """

    domains: tuple[str, ...]
    stage_ref: str | None = None
    target_refs: tuple[str, ...] = ()
    source: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RecipeValue:
    """One paper-space value the project recipe gives a new drawing, and whose it is."""

    value: float
    decision_id: str
    revision_ref: str
    strength: str
    extent: str


@dataclass(frozen=True, slots=True)
class RecipeExport:
    """One recipe export, read and checked: what it carries, where from, and its own digest."""

    target_ref: str
    strength: str
    graphics: Mapping[str, float]
    decision_id: str
    revision_sha256: str
    sha256: str


def _invalid(message: str) -> StudioError:
    return StudioError(422, "DECISION_INVALID", message)


def _export_invalid(message: str) -> StudioError:
    return StudioError(422, "RECIPE_EXPORT_INVALID", message)


# ---- the fixed run ---------------------------------------------------------


def _run(binding: ProjectBinding, *, create: bool):
    """The decisions run, asked for by name; created only on an explicit save.

    Never ``binding.run_ids()``: reading decisions must not cost a scan of
    every run in the project. "Not there" is one bounded read-only question
    about this one known run directory, asked before loading it, because
    ``load_run`` answers every repository failure with the same 404 - and a
    damaged manifest has to refuse rather than quietly answer that this
    project holds no decisions, which would make active constraints vanish.
    """

    if not binding.repository.layout.run(DECISIONS_RUN_ID).root.is_dir():
        if not create:
            return None
        try:
            return binding.repository.create_run(DECISIONS_RUN_ID)
        except (ProjectRepositoryError, OSError) as failure:
            raise StudioError(409, "DECISION_WRITE_FAILED",
                              "The decision run could not be created in this project.") from failure
    return binding.load_run(DECISIONS_RUN_ID)


def _revisions(binding: ProjectBinding) -> dict[str, Mapping[str, Any]]:
    """Every retained revision in the decisions run, keyed by its exact ref."""

    run = _run(binding, create=False)
    if run is None:
        return {}
    refs = binding.repository.list_json(
        run=run,
        destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=DECISIONS_RUN_ID),
        record_kind=STUDIO_SCOPED_DECISION,
    )
    revisions: dict[str, Mapping[str, Any]] = {}
    for ref in refs:
        payload = binding.repository.load_json(ref)
        if payload.get("schema") != DECISION_SCHEMA or payload.get("projectId") != binding.project_id:
            raise StudioError(409, "DECISION_BINDING_MISMATCH",
                              "A retained decision belongs to another project.")
        revisions[ref.uri] = payload
    return revisions


def _chains(revisions: Mapping[str, Mapping[str, Any]]) -> dict[str, tuple[DecisionRevision, ...]]:
    """Each decision's one complete chain, oldest first, or a refusal.

    A decision is a chain, not a heap of records with timestamps: exactly one
    root, exactly one tip, and every named parent present. Competing tips, a
    missing parent and a cycle all fail here rather than being resolved by
    picking the newest file.
    """

    grouped: dict[str, dict[str, Mapping[str, Any]]] = {}
    for ref, payload in revisions.items():
        grouped.setdefault(str(payload.get("decisionId")), {})[ref] = payload
    chains: dict[str, tuple[DecisionRevision, ...]] = {}
    for decision_id, group in grouped.items():
        parents = {payload.get("previousRevisionRef") for payload in group.values()}
        roots = [ref for ref, payload in group.items() if payload.get("previousRevisionRef") is None]
        tips = group.keys() - (parents - {None})
        if len(roots) != 1 or len(tips) != 1 or not (parents - {None}).issubset(group):
            raise StudioError(409, "DECISION_CONFLICT",
                              f"decision {decision_id} has competing or incomplete revisions. "
                              "Every revision has been retained.")
        children: dict[str | None, list[str]] = {}
        for ref, payload in group.items():
            children.setdefault(payload.get("previousRevisionRef"), []).append(ref)
        if any(len(siblings) != 1 for siblings in children.values()):
            raise StudioError(409, "DECISION_CONFLICT",
                              f"decision {decision_id} has a branching revision chain. "
                              "Every revision has been retained.")
        ordered: list[DecisionRevision] = []
        cursor: str | None = roots[0]
        while cursor is not None:
            ordered.append(DecisionRevision(cursor, group[cursor]))
            cursor = next(iter(children.get(cursor, ())), None)
        if len(ordered) != len(group):
            raise StudioError(409, "DECISION_CONFLICT",
                              f"decision {decision_id} has revisions outside its own chain. "
                              "Every revision has been retained.")
        chains[decision_id] = tuple(ordered)
    return chains


def _latest(binding: ProjectBinding) -> dict[str, DecisionRevision]:
    return {decision_id: chain[-1] for decision_id, chain in _chains(_revisions(binding)).items()}


def _ordered(latest: Iterable[DecisionRevision]) -> tuple[DecisionRevision, ...]:
    return tuple(sorted(latest, key=lambda row: (str(row.payload["createdAt"]), row.decision_id)))


# ---- validating what a decision is said against ----------------------------


def _design_projection(binding: ProjectBinding, source: Mapping[str, Any]):
    stage_ref = source.get("sourceStageRef")
    projection = project_state(binding, run_id=source["sourceRunId"], source_stage_ref=stage_ref)
    require_actionable(projection)
    if projection.state_digest != source["stateDigest"]:
        raise StudioError(409, "DECISION_SOURCE_STALE",
                          f"the decision names state {source['stateDigest']}, but run "
                          f"{source['sourceRunId']} projects to {projection.state_digest}.")
    return projection


def _validate_source(
    binding: ProjectBinding, source: Mapping[str, Any], export: RecipeExport | None = None,
) -> tuple[dict[str, Any], tuple[str, ...], Any]:
    """The exact evidence, normalized, with the runs it must keep readable.

    A representation decision is validated against representation evidence: a
    board revision is a board revision and a page is a page. Neither is asked
    for a design run or a state digest it never had. A recipe export is
    another project's recipe, known here only by the file a person imported:
    no page of this project stands behind it, so it is cited only by
    ``import_recipe``, which has read that file (``export``) and checked its
    content against the digest it names.
    """

    kind = source["kind"]
    if kind == RECIPE_EXPORT:
        if export is None or source["exportSha256"] != export.sha256:
            raise _invalid("a recipe export is cited only by importing that export, which reads it and checks "
                           "its content against its sha256 first.")
        return {"kind": RECIPE_EXPORT, "exportSha256": export.sha256}, (), None
    if kind == "board":
        scene = read_board(binding, source["revisionSha256"])
        # An Excalidraw scene keeps deleted elements in its element list. A
        # decision is evidenced by what was actually on the board at that
        # revision, so a tombstone is not an element a decision can name.
        live = {element.get("id") for element in scene.elements if not element.get("isDeleted")}
        identifiers = list(source["elementIds"])
        if len(set(identifiers)) != len(identifiers):
            raise _invalid("elementIds must not repeat a board element.")
        missing = [identifier for identifier in identifiers if identifier not in live]
        if missing:
            raise StudioError(404, "DECISION_SOURCE_UNKNOWN",
                              f"that board revision has no live element {', '.join(sorted(missing))}.")
        # Sorted, so the same evidence submitted in another order is the same
        # evidence: exact-source identity is not the caller's element order.
        normalized = {"kind": "board", "revisionSha256": scene.revision_sha256,
                      "elementIds": sorted(identifiers)}
        return normalized, (BOARD_RUN_ID,), None
    if kind == "document":
        document, _ = document_bytes(binding, source["runId"], source["assetSha256"], source["revisionRef"])
        if document.revision_ref != source["revisionRef"]:
            raise StudioError(404, "DECISION_SOURCE_UNKNOWN",
                              "a document source names its exact registered revisionRef; a generated "
                              "revision is never selected for a request that named none.")
        if not 0 <= source["pageIndex"] < len(document.pages):
            raise StudioError(404, "DECISION_SOURCE_UNKNOWN",
                              "that page does not exist in this document version.")
        normalized = {"kind": "document", "runId": document.run_id, "assetSha256": document.asset_sha256,
                      "revisionRef": document.revision_ref, "pageIndex": source["pageIndex"]}
        retained = (document.run_id,) if document.revision_ref is None else (document.run_id, document.revision_ref)
        return normalized, retained, None
    projection = _design_projection(binding, source)
    normalized = {"kind": "design", "sourceRunId": projection.run.run_id,
                  "stateDigest": projection.state_digest,
                  "sourceStageRef": None if projection.source_stage_ref is None else projection.source_stage_ref.uri}
    if source.get("sourceStageRef") is not None and normalized["sourceStageRef"] != source["sourceStageRef"]:
        raise StudioError(409, "DECISION_SOURCE_STALE", "the decision names another design Stage.")
    return normalized, (projection.run.run_id,), projection


def _require_design_ref(record: StateRecord, target_ref: str) -> str:
    match = _DESIGN_TARGET.fullmatch(target_ref)
    if match is None:
        raise _invalid(f"{target_ref!r} is not a design target: use parameter:<key>, entity:<id> or relation:<id>.")
    kind, name = match.groups()
    try:
        if kind == "parameter":
            record.parameter(name)
        elif kind == "entity":
            record.entity(name)
        elif not any(relation.relation_id == name for relation in record.relations):
            raise StateRecordError(f"unknown relation {name!r}")
    except StateRecordError as exc:
        raise StudioError(404, "DECISION_TARGET_UNKNOWN",
                          f"{target_ref} is not declared by the source record: {exc}") from exc
    return target_ref


def _validate_target(
    domain: str, target_ref: str, source_kind: str, record: StateRecord | None, *, recipe: bool = False,
) -> None:
    if domain in _DOMAIN_TARGETS:
        if target_ref not in _DOMAIN_TARGETS[domain]:
            raise _invalid(f"the {domain} domain targets {' | '.join(_DOMAIN_TARGETS[domain])}.")
        # An export evidences the recipe it carries and nothing else.
        allowed = _DOMAIN_SOURCES[domain] | ({RECIPE_EXPORT} if recipe and domain == "drawing" else set())
        if source_kind not in allowed:
            raise _invalid(f"a {target_ref} decision is evidenced by {' or '.join(sorted(allowed))}, "
                           f"not by a {source_kind} source.")
        return
    if source_kind != "design" or record is None:
        raise _invalid("a design decision is evidenced by an exact retained design run.")
    _require_design_ref(record, target_ref)


def _validate_scope(binding: ProjectBinding, scope: Mapping[str, Any], record: StateRecord | None) -> dict[str, Any]:
    domain, extent = scope["domain"], scope["extent"]
    stage_ref, target_refs = scope.get("stageRef"), scope.get("targetRefs")
    if extent == "stage":
        if not stage_ref:
            raise _invalid("a stage-scoped decision names the exact Stage it applies to.")
        try:
            reference = record_ref_from_uri(stage_ref, binding.project_id)
        except (TypeError, ValueError) as exc:
            raise _invalid(f"stageRef is not a record in this project: {exc}") from exc
        # Independent of the evidence: a Stage claim is checked against
        # committed design history, never against the board or page it was
        # drawn on.
        binding.design_stage(reference)
    elif stage_ref:
        raise _invalid("stageRef belongs to a stage-scoped decision.")
    if extent == "targets":
        if domain != "design" or not target_refs or record is None:
            raise _invalid("a target-scoped decision names design targets of an exact retained design run.")
        target_refs = [_require_design_ref(record, ref) for ref in target_refs]
        if len(set(target_refs)) != len(target_refs):
            raise _invalid("targetRefs must not repeat a target.")
    elif target_refs:
        raise _invalid("targetRefs belong to a target-scoped decision.")
    return {"domain": domain, "extent": extent, "stageRef": stage_ref or None,
            "targetRefs": list(target_refs) if extent == "targets" else None}


def _parameter_of(record: StateRecord | None, key: str) -> Parameter:
    try:
        if record is None:
            raise StateRecordError("a parameter binding needs a design source")
        return record.parameter(key)
    except StateRecordError as exc:
        raise StudioError(404, "DECISION_TARGET_UNKNOWN",
                          f"parameter:{key} is not declared by the source record: {exc}") from exc


def _typed_binding(
    spec: Mapping[str, Any], record: StateRecord | None, scope: Mapping[str, Any], source: Mapping[str, Any],
) -> dict[str, Any] | None:
    """The parameter value and basis the server reads now, not a client claim.

    ``lock`` records an existing lock; it never takes one. A decision that
    named a parameter nobody has locked is refused rather than pretending to
    have acquired the lock it describes. A recipe binding is checked against
    the rest of the decision (``_recipe_binding``).
    """

    request, disposition = spec.get("typedBinding"), spec["disposition"]
    if request is None:
        if disposition == "lock":
            raise _invalid("a lock decision names the parameter binding whose existing lock it records.")
        return None
    if request["kind"] == "recipe":
        return _recipe_binding(spec, request, scope, source)
    key = request["parameterKey"]
    if spec["targetRef"] != f"parameter:{key}":
        raise _invalid("a typed parameter binding and its targetRef name the same parameter.")
    parameter = _parameter_of(record, key)
    if disposition == "lock" and not parameter.lock_authority:
        raise StudioError(409, "DECISION_LOCK_ABSENT",
                          f"parameter:{key} carries no lock. A decision records and verifies an "
                          "existing lock; it does not acquire one.")
    return {"kind": "parameter", "parameterKey": key, "value": parameter.value, "unit": parameter.unit,
            "epistemicStatus": parameter.epistemic_status, "lockAuthority": parameter.lock_authority}


def _recipe_binding(
    spec: Mapping[str, Any], request: Mapping[str, Any], scope: Mapping[str, Any], source: Mapping[str, Any],
) -> dict[str, Any]:
    """The project recipe: paper-space values a new drawing starts from.

    Only a person promotes a correction into one (#244: one correction never
    quietly becomes a lasting preference). That makes it a ``require``
    decision in the drawing domain, confirmed by a human, held as a standard
    (hard), a recipe (strong) or a preference (soft), reaching the project or
    one Stage, applying by scope and evidenced by the exact page it was
    confirmed on - or, imported from another project, by that project's
    export, and then held only as a preference for the whole project (#252).
    The values are the person's own; the wire, or the export's reader, has
    already bounded them as a drawing request would. No object or material is
    looked up, and each value sits under the one target it belongs to.
    """

    if spec["sourceKind"] != "human":
        raise _invalid("a project recipe is retained only on a person's explicit confirmation; an agent, "
                       "evaluator or rule may propose one but never retain it.")
    if scope["domain"] != "drawing":
        raise _invalid("a recipe binding belongs to a drawing decision.")
    if spec["disposition"] != "require" or spec["strength"] not in RECIPE_STRENGTHS:
        raise _invalid("a project recipe is a require decision held as hard, strong_preference or "
                       "soft_preference; a temporary correction stays on its own drawing.")
    if spec["applicability"] != "scope":
        raise _invalid("a project recipe applies by scope; an exact-source one would never reach a new drawing.")
    if source["kind"] == RECIPE_EXPORT:
        if spec["strength"] != IMPORTED_RECIPE_STRENGTH or scope["extent"] != "project":
            raise _invalid("an imported recipe is held as soft_preference for the whole project; a person "
                           "confirms it on this project's own page to hold it more strongly or for one Stage.")
    elif source["kind"] != "document":
        raise _invalid("a project recipe is evidenced by the exact document page it was confirmed on, or by the "
                       "recipe export it was imported from.")
    graphics = {key: value for key, value in request["graphics"].items() if value is not None}
    if not graphics:
        raise _invalid("a recipe binding sets at least one paper-space value.")
    for key in graphics:
        if key not in RECIPE_KEYS:
            raise _invalid(f"{key} is not a paper-space value a recipe sets; use {', '.join(RECIPE_KEYS)}.")
        if RECIPE_KEYS[key] != spec["targetRef"]:
            raise _invalid(f"{key} belongs to {RECIPE_KEYS[key]}, not {spec['targetRef']}.")
    return {"kind": "recipe", "graphics": {key: float(graphics[key]) for key in RECIPE_KEYS if key in graphics}}


def _reach(scope: Mapping[str, Any]) -> tuple[str, str | None]:
    return scope["extent"], scope.get("stageRef")


def _active_recipes(revisions: Iterable[DecisionRevision]) -> Iterable[DecisionRevision]:
    for revision in revisions:
        typed = revision.payload.get("typedBinding")
        if revision.status == ACTIVE and typed is not None and typed.get("kind") == "recipe":
            yield revision


def _require_one_recipe_value(
    chains: Mapping[str, Sequence[DecisionRevision]], binding: Mapping[str, Any], strength: str,
    scope: Mapping[str, Any], replacing: str | None,
) -> None:
    """One value per key, strength and reach: changing a recipe supersedes it.

    A second active value beside the first would leave a new drawing to pick
    one by age. The narrower reach (a Stage over the project) and a stronger
    hold are not conflicts: ``project_recipe`` orders those.
    """

    for other in _active_recipes(chain[-1] for chain in chains.values()):
        if other.decision_id == replacing:
            continue
        shared = [key for key in RECIPE_KEYS
                  if key in binding["graphics"] and key in other.payload["typedBinding"]["graphics"]]
        if shared and other.payload["strength"] == strength and _reach(other.payload["scope"]) == _reach(scope):
            raise StudioError(409, "DECISION_RECIPE_CONFLICT",
                              f"decision {other.decision_id} already sets {', '.join(shared)} as {strength} "
                              "for the same reach. Supersede it to change the recipe.")


def _status(disposition: str) -> str:
    return DEFERRED if disposition == "defer" else ACTIVE


def _message_source(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The caller's message reference, kept to its two declared fields."""

    return None if value is None else {"sessionId": value["sessionId"], "messageId": value["messageId"]}


def _content(
    binding: ProjectBinding, spec: Mapping[str, Any], chains: Mapping[str, Sequence[DecisionRevision]],
    *, replacing: str | None = None, export: RecipeExport | None = None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """One decision's checked content, and the runs its evidence needs kept.

    ``chains`` is the decision set this one joins; ``replacing`` names the
    decision a supersession replaces, whose own recipe values it may restate;
    ``export`` is the recipe export an import has read and checked.
    """

    source, retained, projection = _validate_source(binding, spec["source"], export)
    record = None if projection is None else projection.record
    scope = _validate_scope(binding, spec["scope"], record)
    requested = spec.get("typedBinding")
    _validate_target(scope["domain"], spec["targetRef"], source["kind"], record,
                     recipe=requested is not None and requested["kind"] == "recipe")
    typed = _typed_binding(spec, record, scope, source)
    if typed is not None and typed["kind"] == "recipe":
        _require_one_recipe_value(chains, typed, spec["strength"], scope, replacing)
    content = {
        "rawLanguage": spec["rawLanguage"],
        # A claim about which message these words came from, not a credential:
        # the boundary's own attribution is separate and is never read from a
        # request body. The Hub fills this from the actual user message.
        "messageSource": _message_source(spec.get("messageSource")),
        "disposition": spec["disposition"],
        "strength": spec["strength"],
        "targetRef": spec["targetRef"],
        "scope": scope,
        "source": source,
        "applicability": spec["applicability"],
        "sourceKind": spec["sourceKind"],
        "typedBinding": typed,
    }
    return content, retained


def _retain(
    binding: ProjectBinding, payload: Mapping[str, Any],
) -> DecisionRevision:
    run = _run(binding, create=True)
    try:
        ref = binding.repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=DECISIONS_RUN_ID),
            record_kind=STUDIO_SCOPED_DECISION,
            payload=payload,
        )
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(409, "DECISION_WRITE_FAILED",
                          "The decision could not be retained in its project.") from exc
    return DecisionRevision(ref.uri, payload)


def _revision(
    binding: ProjectBinding, *, decision_id: str, previous: str | None, content: Mapping[str, Any],
    retained: Sequence[str], status: str, reason: str | None, attribution: ActorAttribution,
    revision_message_source: Mapping[str, Any] | None = None,
) -> DecisionRevision:
    payload = {
        "schema": DECISION_SCHEMA,
        "projectId": binding.project_id,
        "decisionId": decision_id,
        "previousRevisionRef": previous,
        "status": status,
        "reason": reason,
        # The message that asked for *this* revision. A revocation keeps the
        # original wording and its own message reference and adds this one:
        # they are two different messages and stay apart.
        "revisionMessageSource": _message_source(revision_message_source),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "attribution": {"actorId": attribution.actor_id, "authenticated": attribution.authenticated,
                        "origin": attribution.origin},
        "retainedSourceRefs": list(dict.fromkeys(retained)),
        **content,
    }
    return _retain(binding, payload)


# ---- the public operations -------------------------------------------------


def list_decisions(binding: ProjectBinding) -> tuple[DecisionRevision, ...]:
    """Every decision's current revision, oldest first."""

    return _ordered(_latest(binding).values())


def decision_history(binding: ProjectBinding, decision_id: str) -> tuple[DecisionRevision, ...]:
    """One decision's whole chain, oldest first; old wording is never rewritten."""

    chain = _chains(_revisions(binding)).get(decision_id)
    if chain is None:
        raise StudioError(404, "DECISION_NOT_FOUND", f"{decision_id} is not a decision this project retains.")
    return chain


@retained_sources
def save_decision(
    binding: ProjectBinding, spec: Mapping[str, Any], attribution: ActorAttribution,
) -> DecisionRevision:
    """Check one decision against its exact evidence and retain its first revision."""

    # A decision set nobody can read is not a decision set to add to: the same
    # refusal a read answers with stops a write from landing beside it.
    chains = _chains(_revisions(binding))
    content, retained = _content(binding, spec, chains)
    return _revision(binding, decision_id=str(uuid.uuid4()), previous=None, content=content,
                     retained=retained, status=_status(spec["disposition"]), reason=None,
                     attribution=attribution)


@retained_sources
def revise_decision(
    binding: ProjectBinding, decision_id: str, *, expected_revision_ref: str, action: str,
    reason: str | None, replacement: Mapping[str, Any] | None, attribution: ActorAttribution,
    revision_message_source: Mapping[str, Any] | None = None,
) -> DecisionRevision:
    """Revoke or supersede one decision, against the revision the caller read.

    Revoking preserves the wording, source and scope it revokes and adds the
    reason. It unlocks nothing: a parameter lock is the record's, and a
    decision that recorded one never held it.
    """

    chains = _chains(_revisions(binding))
    chain = chains.get(decision_id)
    if chain is None:
        raise StudioError(404, "DECISION_NOT_FOUND", f"{decision_id} is not a decision this project retains.")
    current = chain[-1]
    if current.ref != expected_revision_ref:
        raise StudioError(409, "DECISION_STALE",
                          "This decision has a newer revision. Read it before revising it again.")
    if current.status == REVOKED:
        raise StudioError(409, "DECISION_REVOKED", "This decision has already been revoked.")
    if action == "revoke":
        content = {key: current.payload.get(key) for key in
                   ("rawLanguage", "messageSource", "disposition", "strength", "targetRef", "scope",
                    "source", "applicability", "sourceKind", "typedBinding")}
        retained = tuple(current.payload.get("retainedSourceRefs", ()))
        status = REVOKED
    elif replacement is None:
        raise _invalid("superseding a decision needs the replacement it is superseded by.")
    else:
        content, retained = _content(binding, replacement, chains, replacing=decision_id)
        status = _status(replacement["disposition"])
    return _revision(binding, decision_id=decision_id, previous=current.ref, content=content,
                     retained=retained, status=status, reason=reason, attribution=attribution,
                     revision_message_source=revision_message_source)


# ---- what the next turn is handed ------------------------------------------


def focus_refs(record: StateRecord | None, element_ids: Sequence[str]) -> tuple[str, ...]:
    """What a turn focused on these elements is actually about, as design refs.

    An element is not only itself: the parameters its rows are explicitly
    bound to (including the bindings it inherits from its type) are what a
    "keep this dimension" decision was made about, and the relations incident
    to it are the conditions a change to it has to respect. Follow declared
    upstream reads as the ContextPack does: a later element can consume an
    earlier Stage's parameter through expressions, a type or a host. This
    does not follow those inputs into other consumers or global locks, widen
    edit permission, or change any decision's scope, strength or applicability.
    """

    if record is None:
        return tuple(f"entity:{identifier}" for identifier in element_ids)
    focused = set(element_ids)
    refs: list[str] = []
    for identifier in element_ids:
        refs.append(f"entity:{identifier}")
        try:
            entity = record.entity(identifier)
        except StateRecordError:
            continue
        refs.extend(f"parameter:{key}" for _path, key in parameter_bindings_of(entity, record))
    refs.extend(f"relation:{relation.relation_id}" for relation in record.relations
                if relation.subject in focused or relation.object in focused)
    upstream: dict[str, list[tuple[str, str]]] = {}
    edges = record.dependency_edges()
    types = {entity.ref: entity for entity in record.entities_of("Type@1")}
    type_edges: dict[str, list] = {}
    for edge in edges:
        # Type invalidation includes all defaults, even values overridden by
        # an instance. A read focus must use that instance's effective fields.
        if edge.downstream_ref in types:
            type_edges.setdefault(edge.downstream_ref, []).append(edge)
            continue
        upstream.setdefault(edge.downstream_ref, []).append((edge.upstream_ref, edge.source_ref))
    for entity in record.entities_of("Element@1"):
        dependencies = upstream.setdefault(entity.ref, [])
        dependencies.extend((f"parameter:{key}", entity.ref)
                            for _path, key in parameter_bindings_of(entity, record))
        type_ref = entity.fields.get("type_ref")
        declared_type = types.get("entity:" + type_ref.removeprefix("entity:")) if type_ref else None
        if declared_type is not None:
            inherited = declared_type.fields.get("references", {}).keys() - entity.fields.get("references", {}).keys()
            dependencies.extend((edge.upstream_ref, edge.source_ref) for edge in type_edges.get(declared_type.ref, ())
                                if edge.source_ref == declared_type.ref and edge.relation in inherited)
    included = set(refs)
    pending = list(refs)
    while pending:
        for ref, source in upstream.get(pending.pop(), ()):
            if ref not in included:
                included.add(ref)
                pending.append(ref)
            # Retain the relation actually traversed, not every other relation
            # incident to its upstream end (which can belong to another task).
            if source.startswith("relation:"):
                included.add(source)
    return tuple(sorted(included))


def decision_context_for(
    binding: ProjectBinding, *, requested: Mapping[str, Any] | None, design_source: Mapping[str, Any],
    stage_ref: str | None, focus: Sequence[str], record: StateRecord | None,
) -> DecisionContext:
    """What this turn is about, checked as strictly as a decision's own evidence.

    A design turn is the design turn the request already named: an explicit
    design source has to be the very source this pack projects, because a
    decision compiled against one state and handed to a caller reading
    another would be a claim nobody checked. A drawing or copy turn brings
    its own representation evidence, which is never coerced into a Design
    run; omitting it is allowed and costs exactly the ``exact-source``
    decisions, which have no current identity to be compared against.

    A turn that names no domain reads design and drawing together (05, option
    A): the agent about to draw sees the same project recipe a new drawing
    starts from. Its only evidence is the design source it projects, so a
    drawing decision said against one exact page does not follow it. A turn
    that names a domain reads that domain alone.
    """

    if requested is None:
        return DecisionContext(_DEFAULT_DOMAINS, stage_ref, tuple(focus), design_source)
    domain = requested["domain"]
    if requested.get("stageRef") is not None and requested["stageRef"] != stage_ref:
        raise StudioError(409, "DECISION_STAGE_MISMATCH",
                          "decisionContext names a Stage other than the one this source is under; "
                          "a Stage scope is read from committed design history, never injected.")
    targets = tuple(requested.get("targetRefs") or ())
    source = requested.get("source")
    if domain == "design":
        if source is not None:
            if source["kind"] != "design":
                raise _invalid("a design turn reads a design source.")
            # The enclosing ContextPack already verified this exact projection.
            # Compare the requested identity against it without reading the same
            # design again through the reference-run survey.
            if (source["sourceRunId"] != design_source["sourceRunId"]
                    or source["stateDigest"] != design_source["stateDigest"]
                    or (source.get("sourceStageRef") is not None
                        and source["sourceStageRef"] != design_source.get("sourceStageRef"))):
                raise StudioError(409, "DECISION_SOURCE_MISMATCH",
                                  "decisionContext names another design source than the one this "
                                  "context pack projects; read the pack for that source instead.")
        if record is not None:
            targets = tuple(_require_design_ref(record, ref) for ref in targets)
        return DecisionContext((domain,), stage_ref, targets or tuple(focus), design_source)
    if targets:
        raise _invalid(f"a {domain} turn names no design targetRefs.")
    if source is not None and source["kind"] not in _DOMAIN_SOURCES[domain]:
        raise _invalid(f"a {domain} turn is evidenced by "
                       f"{' or '.join(sorted(_DOMAIN_SOURCES[domain]))}, not by a {source['kind']} source.")
    return DecisionContext((domain,), stage_ref, (),
                           None if source is None else _validate_source(binding, source)[0])


def _applies_to_scope(scope: Mapping[str, Any], context: DecisionContext) -> str | None:
    if scope["domain"] not in context.domains:
        return "domain-mismatch"
    if scope["extent"] == "stage":
        if context.stage_ref is None or scope["stageRef"] != context.stage_ref:
            return "stage-mismatch"
    if scope["extent"] == "targets":
        # A decision about A and B still matters when this task changes only A.
        if set(scope["targetRefs"] or ()).isdisjoint(context.target_refs):
            return "targets-outside-focus"
    return None


def _target_still_declared(payload: Mapping[str, Any], record: StateRecord | None) -> str | None:
    """Whether the design object this decision is about is still in the record.

    A decision about an entity, relation or parameter that no longer exists is
    not a constraint to repeat at a later turn; it is derived-stale, and the
    revision that said it stays exactly as it was written.
    """

    if payload["scope"]["domain"] != "design":
        return None
    if record is None:
        return "target-unreadable"
    refs = [payload["targetRef"], *(payload["scope"].get("targetRefs") or ())]
    for ref in refs:
        try:
            _require_design_ref(record, ref)
        except StudioError:
            return "target-missing"
    return None


def _binding_still_holds(payload: Mapping[str, Any], record: StateRecord | None) -> str | None:
    typed = payload.get("typedBinding")
    if typed is None or typed.get("kind") != "parameter":
        # A recipe's values are the person's paper-space choice; no design
        # record holds them, so no design change moves them.
        return None
    if record is None:
        return "binding-unreadable"
    try:
        parameter = record.parameter(typed["parameterKey"])
    except StateRecordError:
        return "parameter-missing"
    if parameter.value != typed["value"] or parameter.unit != typed["unit"]:
        return "parameter-changed"
    if payload["disposition"] == "lock" and not parameter.lock_authority:
        return "lock-released"
    return None


def compile_scoped_decisions(
    binding: ProjectBinding, context: DecisionContext, record: StateRecord | None,
) -> tuple[tuple[DecisionRevision, ...], tuple[tuple[str, str], ...]]:
    """The decisions that still apply here, and why the rest were left out.

    Scope is what the architect claimed; source evidence is provenance. A
    ``scope`` decision therefore survives later revisions of the thing it was
    said about, while an ``exact-source`` one applies only while the caller is
    looking at the very source it was said against. A design decision is
    checked against the record as it stands now: a target that is gone, a
    parameter value that moved, and a lock that was released each make the
    decision derived-stale rather than a constraint to repeat. Nothing here
    enforces anything; a kept relation stays the architect's own words.
    """

    included: list[DecisionRevision] = []
    excluded: list[tuple[str, str]] = []
    for revision in _ordered(_latest(binding).values()):
        payload = revision.payload
        reason = None
        if payload["status"] != ACTIVE:
            reason = payload["status"]
        else:
            reason = _applies_to_scope(payload["scope"], context)
        if reason is None and payload["applicability"] == "exact-source" and payload["source"] != context.source:
            reason = "exact-source-changed"
        if reason is None:
            reason = _target_still_declared(payload, record) or _binding_still_holds(payload, record)
        if reason is None:
            included.append(revision)
        else:
            excluded.append((revision.decision_id, reason))
    return tuple(included), tuple(excluded)


# ---- the project recipe a new drawing starts from --------------------------


def project_recipe(binding: ProjectBinding, *, stage_ref: str | None = None) -> dict[str, RecipeValue]:
    """The project recipe layer: one value per paper-space key, or none.

    This is what a drawing reads between its own previous revision and the
    code default (03-C4, D-05-2): an explicit request value wins, a rebuild
    keeps its revision's values, and only a key neither names comes from
    here. ``stage_ref`` is the exact Stage the drawing's source is under; a
    Stage-scoped recipe reaches only that Stage. Per key the stronger hold
    wins (hard, strong_preference, soft_preference), then a Stage's own over
    the project's. Nothing enforces a hard value (D-05-3). Two values of the
    same hold and reach are refused, never ordered by age.

    Only active recipe decisions count: a revoked one is gone and a
    superseded one reads as its replacement. Nothing is inferred from earlier
    drawings, and only the decisions run is read.
    """

    ranked: dict[str, list[tuple[tuple[int, int], RecipeValue]]] = {}
    for revision in _active_recipes(_ordered(_latest(binding).values())):
        payload = revision.payload
        scope = payload["scope"]
        if scope["domain"] != "drawing" or payload["strength"] not in RECIPE_STRENGTHS:
            continue
        if scope["extent"] == "stage" and scope["stageRef"] != stage_ref:
            continue
        rank = (RECIPE_STRENGTHS.index(payload["strength"]), 0 if scope["extent"] == "stage" else 1)
        for key, value in payload["typedBinding"]["graphics"].items():
            ranked.setdefault(key, []).append((rank, RecipeValue(
                value=value, decision_id=revision.decision_id, revision_ref=revision.ref,
                strength=payload["strength"], extent=scope["extent"])))
    layer: dict[str, RecipeValue] = {}
    for key in RECIPE_KEYS:
        rows = sorted(ranked.get(key, ()), key=lambda row: row[0])
        if len(rows) > 1 and rows[0][0] == rows[1][0]:
            raise StudioError(409, "DECISION_RECIPE_CONFLICT",
                              f"decisions {rows[0][1].decision_id} and {rows[1][1].decision_id} both set {key} "
                              f"as {rows[0][1].strength} for the same reach. Revoke or supersede one.")
        if rows:
            layer[key] = rows[0][1]
    return layer


# ---- a recipe travelling to another project (#252) -------------------------


def recipe_export(binding: ProjectBinding, decision_id: str) -> dict[str, Any]:
    """One active project recipe as the file it travels in, and nothing else of this project.

    A ``DrawingRecipeExport@1`` carries the recipe's values, the one target
    they sit under and the hold a person gave them, the decision's id and the
    sha256 of the exact revision exported, and ``sha256``: the canonical
    digest of all of that, the export's own identity. No page, path, run,
    project id, actor or wording travels - whoever holds this project can
    resolve the decision and its revision from what does - and one revision
    always exports to the same content. Reading it writes nothing.
    """

    current = decision_history(binding, decision_id)[-1]
    typed = current.payload.get("typedBinding")
    if typed is None or typed.get("kind") != "recipe":
        raise _invalid(f"decision {decision_id} is not a project recipe; only a recipe's values travel.")
    if current.status != ACTIVE:
        raise StudioError(409, "DECISION_REVOKED",
                          f"decision {decision_id} has been revoked; a revoked recipe does not travel.")
    body = {
        "schema": RECIPE_EXPORT_SCHEMA,
        "recipe": {"targetRef": current.payload["targetRef"], "strength": current.payload["strength"],
                   "graphics": dict(typed["graphics"])},
        "source": {"decisionId": current.decision_id,
                   "revisionSha256": record_ref_from_uri(current.ref, binding.project_id).sha256},
    }
    return {**body, "sha256": canonical_digest(body)}


def read_recipe_export(document: Any) -> RecipeExport:
    """One ``DrawingRecipeExport@1`` exactly as it was exported, or a refusal.

    Its form is closed: a field an export never has is not this format, and
    could carry what an export must not. Its content hashes to the sha256 it
    names, so a file changed after export - a value nobody confirmed - is
    refused rather than imported. Its values are bounded exactly as a
    cut-plan request bounds them, each under its own target.
    """

    if not isinstance(document, Mapping) or set(document) != _EXPORT_FIELDS:
        raise _export_invalid("an export holds exactly schema, recipe, source and sha256.")
    if document["schema"] != RECIPE_EXPORT_SCHEMA:
        raise _export_invalid(f"this reads {RECIPE_EXPORT_SCHEMA}, not {document['schema']!r}.")
    body = {key: value for key, value in document.items() if key != "sha256"}
    try:
        digest = canonical_digest(body)
    except CanonicalValueError as exc:
        raise _export_invalid(f"an export is finite JSON: {exc}.") from exc
    if document["sha256"] != digest:
        raise _export_invalid("the export's content does not hash to the sha256 it names: it was changed after "
                              "it was exported, and nobody confirmed what it says now.")
    recipe, source = document["recipe"], document["source"]
    if (not isinstance(recipe, Mapping) or set(recipe) != {"targetRef", "strength", "graphics"}
            or not isinstance(source, Mapping) or set(source) != {"decisionId", "revisionSha256"}):
        raise _export_invalid("an export's recipe is targetRef, strength and graphics, and its source is "
                              "decisionId and revisionSha256.")
    decision_id, revision = source["decisionId"], source["revisionSha256"]
    if (not isinstance(decision_id, str) or not 0 < len(decision_id) <= 128
            or not isinstance(revision, str) or _SHA256.fullmatch(revision) is None):
        raise _export_invalid("an export's source is its decision's id and the sha256 of the revision exported.")
    target, strength, graphics = recipe["targetRef"], recipe["strength"], recipe["graphics"]
    if strength not in RECIPE_STRENGTHS:
        raise _export_invalid(f"an export's recipe is held as {', '.join(RECIPE_STRENGTHS)}.")
    if (not isinstance(graphics, Mapping) or not graphics
            or any(RECIPE_KEYS.get(key) != target or value is None for key, value in graphics.items())):
        raise _export_invalid("an export sets at least one paper-space value, each under its own target: "
                              + ", ".join(f"{key} under {owner}" for key, owner in RECIPE_KEYS.items()) + ".")
    # The wire's own bounds, read where they are defined. Imported here
    # because the transport module imports this one.
    from ..transport.decisions import RecipeGraphicsDto

    try:
        RecipeGraphicsDto.model_validate(dict(graphics), strict=True)
    except ValidationError as exc:
        error = exc.errors()[0]
        raise _export_invalid(f"an export's values are bounded as a drawing request bounds them: "
                              f"{'.'.join(map(str, error['loc']))}: {error['msg']}.") from exc
    return RecipeExport(target_ref=target, strength=strength,
                        graphics={key: float(graphics[key]) for key in RECIPE_KEYS if key in graphics},
                        decision_id=decision_id, revision_sha256=revision, sha256=digest)


@retained_sources
def import_recipe(
    binding: ProjectBinding, document: Mapping[str, Any], *, raw_language: str, source_kind: str,
    attribution: ActorAttribution,
) -> DecisionRevision:
    """Retain one recipe export as this project's preference: one decision, on a person's confirmation.

    The export is read and checked first (``read_recipe_export``). It then
    becomes a recipe like any other - a ``require`` in the drawing domain
    with the export's target and values, retained only for ``sourceKind``
    human - held as soft_preference for the whole project and evidenced by
    the export's sha256. ``raw_language`` is what the person was shown and
    confirmed. The one-value rule is the recipe's own: a key this project
    already holds as a soft_preference for the project is
    DECISION_RECIPE_CONFLICT, and changing it is a supersession. Like every
    decision write, this holds the project's lock from reading the decisions
    to retaining the new one, so a runtime writing beside it cannot interleave.
    """

    if not isinstance(raw_language, str) or not raw_language.strip() or len(raw_language) > 2000:
        raise _invalid("rawLanguage is the 1-2000 characters the person was shown and confirmed.")
    export = read_recipe_export(document)
    spec = {
        "rawLanguage": raw_language, "messageSource": None, "disposition": "require",
        "strength": IMPORTED_RECIPE_STRENGTH, "targetRef": export.target_ref,
        "scope": {"domain": "drawing", "extent": "project"},
        "source": {"kind": RECIPE_EXPORT, "exportSha256": export.sha256},
        "applicability": "scope", "sourceKind": source_kind,
        "typedBinding": {"kind": "recipe", "graphics": dict(export.graphics)},
    }
    content, retained = _content(binding, spec, _chains(_revisions(binding)), export=export)
    return _revision(binding, decision_id=str(uuid.uuid4()), previous=None, content=content,
                     retained=retained, status=ACTIVE, reason=None, attribution=attribution)
