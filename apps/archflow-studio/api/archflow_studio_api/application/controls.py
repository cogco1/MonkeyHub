"""Declared controls: an authored control proposed for a component the model
shows and the catalog lacks.

"Make the columns taller" on a component that has no ``Element@1`` row is not
a change the record can take; it is a request for a control that does not
exist yet. The studio answers with a declaration: which component, which
property, what the model shows for it (the unbound objects, their extents,
the inspection they were read from) and how confident the draft is. The
declaration is held in process like a proposal; nothing here writes the
authored record. Turning a declaration into an ``Element@1`` row is a
candidate's successor record, made only when the architect applies it, and
the re-index tool is what drafts rows with provenance for a whole model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Any, Mapping
from uuid import uuid4

from ..transport.errors import StudioError
from .catalog import MODEL_VISIBLE_CATALOG_MISSING, Catalog

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
    reason_code: str
    # What the model shows for the component and where that was read.
    object_names: tuple[str, ...]
    inspection_run: str | None
    # The draft the reindex tool would author, when one can be drafted here:
    # an Element@1 row shape with its provenance and confidence, or None.
    draft: Mapping[str, Any] | None
    confidence: float
    provenance: tuple[str, ...]
    created_at: str
    honesty: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "controlId": self.control_id,
            "status": self.status,
            "stateDigest": self.state_digest,
            "componentId": self.component_id,
            "requestedProperty": self.requested_property,
            "utterance": self.utterance,
            "reasonCode": self.reason_code,
            "objectNames": list(self.object_names),
            "inspectionRun": self.inspection_run,
            "draft": None if self.draft is None else dict(self.draft),
            "confidence": self.confidence,
            "provenance": list(self.provenance),
            "createdAt": self.created_at,
            "honesty": list(self.honesty),
            "persistence": PERSISTENCE,
        }


def declare_control(
    catalog: Catalog,
    *,
    state_digest: str,
    component_id: str,
    requested_property: str | None,
    utterance: str,
    reason_code: str,
) -> DeclaredControl:
    """The declaration for one component, from what the catalog can vouch for."""

    node = catalog.component(component_id)
    if node is None:
        raise StudioError(404, "COMPONENT_NOT_FOUND", f"{component_id} is not a component of this record")
    objects = tuple(
        item.name
        for item in catalog.objects
        if item.component_id == component_id and item.status == MODEL_VISIBLE_CATALOG_MISSING
    )
    provenance = [f"utterance:{utterance}", f"component:{component_id}"]
    honesty: list[str] = []
    if catalog.inspection_run is not None:
        provenance.append(f"inspection-run:{catalog.inspection_run}")
    if objects:
        provenance.extend(f"object:{name}" for name in objects[:20])
        if len(objects) > 20:
            honesty.append(f"{len(objects) - 20} more objects not listed in the provenance")
    else:
        honesty.append("the model shows no object of this component; the draft has nothing to read extents from")
    draft = None
    if objects:
        draft = {
            "schema": "Element@1",
            "component_id": component_id,
            "producer": None,
            "params": {requested_property: None} if requested_property else {},
            "references": {},
            "reads": list(objects[:20]),
            "note": (
                "a draft, not a row: the producer, the references and the value "
                "come from the re-index of the inspected objects, with provenance; "
                "the studio writes nothing to the authored record"
            ),
        }
        honesty.append("the draft names the objects to read; producer and references are the re-index tool's to derive")
    return DeclaredControl(
        control_id=f"ctl-{uuid4().hex[:12]}",
        status=PROPOSED,
        state_digest=state_digest,
        component_id=component_id,
        requested_property=requested_property,
        utterance=utterance,
        reason_code=reason_code,
        object_names=objects,
        inspection_run=catalog.inspection_run,
        draft=draft,
        # Objects were read off a certified inspection; the row is not drafted.
        confidence=0.5 if objects else 0.0,
        provenance=tuple(provenance),
        created_at=datetime.now(timezone.utc).isoformat(),
        honesty=tuple(honesty),
    )


class DeclaredControlStore:
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
