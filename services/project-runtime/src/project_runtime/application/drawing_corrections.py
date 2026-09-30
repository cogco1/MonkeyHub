"""What changed between a cut plan's revisions, and which changes repeat (05 3.2(B)(C)).

Correction capture keeps one memory owner, the decisions (option A), so
nothing here is recorded. A correction's evidence is the drawing's own revision
chain - each revision's receipt names the revision it continued - and the
difference between the two view recipes. Its class is a pure function of that
difference and of why the page was replaced (``replacement_cause``); the
difference and the class are MonkeyDiagram's (``monkeydiagram.corrections``,
#519), and this module pairs the revisions and reads the decisions. A recipe
correction people repeat on several drawings is only ever offered; a person
saves it as a project recipe decision or leaves it. Every read derives all of
this again from the retained revisions, and nothing is inferred from any
other file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from archflow.contracts.canonical import canonical_digest
from monkeydiagram.corrections import classify, recipe_diff

from .artifacts import SourceDocument, list_documents, replacement_cause
from ..binding import ProjectBinding
from .decisions import RECIPE_KEYS, RecipeValue, project_recipe

# D-05-1: how many distinct drawings must repeat a correction before it is offered.
SUGGESTION_DRAWINGS = 2


@dataclass(frozen=True, slots=True)
class Correction:
    """One cut-plan revision and the revision it continued.

    ``cause`` is why the later page replaced the earlier (``source`` or
    ``representation``), ``diff`` what changed (``recipe_diff``) and
    ``correction_class`` what kind of correction that is (``classify``).
    """

    before: SourceDocument
    after: SourceDocument
    cause: str | None
    diff: dict[str, list[Any]]
    correction_class: str


def corrections(documents: Iterable[SourceDocument]) -> tuple[Correction, ...]:
    """Every cut-plan revision paired with the revision it continued, per drawing in the order they were drawn.

    A revision pairs only with the registered revision its own receipt names
    (``previousRevisionRef``) and only within its drawing: a drawing's first
    revision pairs with nothing, and a rebuild from an older revision is a
    pair of its own. The order is the drawing, then each later revision's
    retained generation time and ref.
    """

    plans = {document.revision_ref: document for document in documents
             if document.revision_ref is not None and (document.view_recipe or {}).get("kind") == "cut-plan"}
    pairs = []
    for after in plans.values():
        before = plans.get(after.previous_revision_ref)
        if before is None or before.drawing_id != after.drawing_id:
            continue
        cause = replacement_cause(before, after)
        cause = cause if cause in ("source", "representation") else None
        diff = recipe_diff(before.view_recipe, after.view_recipe)
        # A V0 cleanup report cannot flag a hide (classify), so none is read.
        pairs.append(Correction(before, after, cause, diff, classify(diff, None, cause)))
    return tuple(sorted(pairs, key=_drawn))


def _drawn(pair: Correction) -> tuple[str, str, str]:
    return pair.after.drawing_id or "", pair.after.generated_at or "", pair.after.revision_ref or ""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def recipe_suggestions(pairs: Iterable[Correction], covered: Callable[[Correction, str], bool]) -> list[dict[str, Any]]:
    """The recipe corrections repeated the same way on enough distinct drawings to offer (05 3.2(C)).

    Evidence is a representation-only pair of class ``recipe`` that no agent
    asked for - a person did, or the request did not say: a source rebuild
    corrects nothing, and an agent's revision is its reading, not the
    architect's. Each recipe key it changed (``RECIPE_KEYS``) joins the group
    of that key and direction - 2 to 3 and 2 to 4 are both an increase -
    unless ``covered(pair, key)``: the recipe that drawing reads already
    decides it (``recipe_holds``). A group spanning ``SUGGESTION_DRAWINGS`` distinct
    drawings is offered at the value of its most recent revision, with that
    revision's page: the exact page a recipe decision cites. Its id is derived
    from what is offered, so the same offer keeps it.
    """

    groups: dict[tuple[str, str], list[Correction]] = {}
    for pair in pairs:
        if pair.cause != "representation" or pair.correction_class != "recipe" or pair.after.source_kind == "agent":
            continue
        for key in RECIPE_KEYS:
            old, new = pair.diff.get(f"graphics.{key}", (None, None))
            if not (_number(old) and _number(new)) or covered(pair, key):
                continue
            groups.setdefault((key, "increase" if new > old else "decrease"), []).append(pair)
    offers = []
    for (key, direction), evidence in groups.items():
        drawings = sorted({pair.after.drawing_id for pair in evidence})
        if len(drawings) < SUGGESTION_DRAWINGS:
            continue
        evidence = sorted(evidence, key=_drawn)
        latest = max(evidence, key=lambda pair: (pair.after.generated_at or "", pair.after.revision_ref or ""))
        value = float(latest.diff[f"graphics.{key}"][1])
        offers.append({
            "suggestionId": canonical_digest({"field": key, "direction": direction, "value": value,
                                              "drawingIds": drawings}),
            "field": key, "direction": direction, "value": value, "drawingIds": drawings,
            "evidence": [{"drawingId": pair.after.drawing_id, "beforeRevisionRef": pair.before.revision_ref,
                          "afterRevisionRef": pair.after.revision_ref} for pair in evidence],
            "page": {"runId": latest.after.run_id, "assetSha256": latest.after.asset_sha256,
                     "revisionRef": latest.after.revision_ref, "pageIndex": 0},
        })
    order = list(RECIPE_KEYS)
    return sorted(offers, key=lambda offer: (order.index(offer["field"]), offer["direction"]))


def recipe_holds(held: RecipeValue | None, value: float) -> bool:
    """Whether a correction to ``value`` is already the recipe's to decide.

    A person's strong or hard recipe changes only by superseding that
    decision, and an offer to save the key again could not be retained beside
    it. A soft preference - an imported firm default among them (#252) - is a
    starting point: corrections away from its value are still offered, and
    saving one holds the key more strongly.
    """

    return held is not None and (held.strength != "soft_preference" or float(held.value) == float(value))


def drawing_corrections(binding: ProjectBinding, *, drawing_id: str | None = None) -> dict[str, Any]:
    """One drawing's classified revision pairs and the project's recipe suggestions; nothing is written.

    ``pairs`` are ``drawing_id``'s, and none when no drawing is named. The
    suggestions always read the whole project, against the recipe each
    drawing's source reads (``project_recipe`` for its Stage, as a new
    drawing of it would).
    """

    pairs = corrections(list_documents(binding))
    layers: dict[str | None, Mapping[str, Any]] = {}

    def covered(pair: Correction, key: str) -> bool:
        stage = pair.after.source_stage_ref
        if stage not in layers:
            layers[stage] = project_recipe(binding, stage_ref=stage)
        return recipe_holds(layers[stage].get(key), pair.diff[f"graphics.{key}"][1])

    return {
        "projectId": binding.project_id, "drawingId": drawing_id,
        "pairs": [_pair(pair) for pair in pairs if drawing_id is not None and pair.after.drawing_id == drawing_id],
        "suggestions": recipe_suggestions(pairs, covered),
    }


def _pair(pair: Correction) -> dict[str, Any]:
    after = pair.after
    return {"drawingId": after.drawing_id, "beforeRevisionRef": pair.before.revision_ref,
            "afterRevisionRef": after.revision_ref, "cause": pair.cause,
            "origin": None if after.attribution is None else after.attribution.origin,
            "sourceKind": after.source_kind, "reason": after.reason, "class": pair.correction_class,
            "diff": pair.diff}
