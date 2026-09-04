"""Declared controls: the authored control a terminal clarification asked for, kept.

A request that ends in MISSING_EDITABLE_CONTROL comes back with an
``AuthoredControlDraft`` - the control somebody would have to author, read off
the elements near the component, with its provenance. That draft is a value
the answer carries and nothing retains. This module is the retaining step: the
architect confirms the draft and the studio keeps it as a *declared control*,
in process, with the state it was drafted against, the sentence that asked for
it and what the catalog showed. Nothing here writes the authored record:
turning a declared control into an ``Element@1`` row is a successor record
made when somebody applies it, or the re-index tool's work for a whole model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Any, Mapping
from uuid import uuid4

from ..transport.errors import StudioError
from .clarification import AuthoredControlDraft

PERSISTENCE = "in-memory (not version history)"
PROPOSED = "proposed"


@dataclass(frozen=True, slots=True)
class DeclaredControl:
    control_id: str
    status: str
    state_digest: str
    component_id: str
    requested_property: str | None
    utterance: str
    draft: AuthoredControlDraft
    created_at: str
    honesty: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        d = self.draft
        return {
            "controlId": self.control_id,
            "status": self.status,
            "stateDigest": self.state_digest,
            "componentId": self.component_id,
            "requestedProperty": self.requested_property,
            "utterance": self.utterance,
            "suggestedElementId": d.suggested_element_id,
            "producer": d.producer,
            "binding": d.binding,
            "unit": d.unit,
            "provenance": list(d.provenance),
            "confidence": d.confidence,
            "dependencyRequirements": list(d.dependency_requirements),
            "suggestedAction": d.suggested_action,
            "catalogStatus": d.catalog_status,
            "objectNames": list(d.object_names),
            "createdAt": self.created_at,
            "honesty": list(self.honesty),
            "persistence": PERSISTENCE,
        }


def declare_control(
    *,
    draft: AuthoredControlDraft,
    state_digest: str,
    utterance: str,
    declared_components: Mapping[str, Any] | set[str],
    editable_element_ids: tuple[str, ...],
) -> DeclaredControl:
    """A declared control from a confirmed draft, checked against the state that answers now.

    The component must be one the record declares, and it must still have no
    editable element under it - a draft for a component that gained a control
    since it was drafted is refused rather than kept beside the control.
    """

    if draft.target_component_id not in declared_components:
        raise StudioError(
            409,
            "CONTROL_COMPONENT_UNKNOWN",
            f"the draft names component {draft.target_component_id}, which the record does not declare",
        )
    if editable_element_ids:
        raise StudioError(
            409,
            "CONTROL_ALREADY_EXISTS",
            f"component {draft.target_component_id} now has editable elements ({', '.join(editable_element_ids[:4])}); "
            "change one of them instead of declaring a control",
        )
    honesty = [
        "held in this process; not a row of the record, not a run, not a candidate",
        "the draft invents no number: the control's value is authored when the row is",
    ]
    if draft.catalog_status == "MODEL_VISIBLE_CATALOG_MISSING":
        honesty.append(f"the model shows {len(draft.object_names)} object(s) of the component with no row; the re-index tool drafts rows from them")
    return DeclaredControl(
        control_id=f"ctl-{uuid4().hex[:12]}",
        status=PROPOSED,
        state_digest=state_digest,
        component_id=draft.target_component_id,
        requested_property=draft.semantic_property,
        utterance=utterance,
        draft=draft,
        created_at=datetime.now(timezone.utc).isoformat(),
        honesty=tuple(honesty),
    )


class DeclaredControlStore:
    """The declared controls this process holds, by id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, DeclaredControl] = {}

    def put(self, control: DeclaredControl) -> DeclaredControl:
        with self._lock:
            self._items[control.control_id] = control
        return control

    def get(self, control_id: str) -> DeclaredControl:
        with self._lock:
            control = self._items.get(control_id)
        if control is None:
            raise StudioError(
                404,
                "CONTROL_NOT_FOUND",
                f"no declared control {control_id} in this process. Declared controls are held {PERSISTENCE}.",
            )
        return control

    def list(self) -> tuple[DeclaredControl, ...]:
        with self._lock:
            return tuple(self._items.values())
