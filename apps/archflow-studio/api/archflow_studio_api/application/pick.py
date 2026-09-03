"""What a clicked object is, answered from the record rather than from its name.

A viewer can see a `.3dm` and nothing else: a mesh, a layer, a string. It is the
export's ``archflow:*`` user strings that carry identity, and it is the State
Record that says whether that identity means anything here. So this module reads
only the keys the exporter actually writes (the M088 set, verbatim from
``RhinoCadExecutionReceipt@4``) and checks them against the projection's own
``Component@1`` and ``Element@1`` entities. Nothing is inferred from geometry,
from a layer path, or from the shape of a name.

Three answers are possible and they are kept apart on purpose. *Unbound* is an
object this project never produced — a hand-drawn line, an imported block — and
it is not an error, it is a fact about the object. *Unknown component* is an
object that claims an identity this record cannot honour, which is what an
export from another project, or from a record that has since changed, looks like
from here. Only *resolved* names a component, and it names the kernel's, never a
guess. Collapsing any two of these would let the UI show a confident answer
where the server has none.

Whether the file the object came from is current is a separate question, and it
is reported rather than enforced: opening last week's export to look at it is
legitimate. The base is enforced where a change is proposed, not where one is
clicked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..transport.errors import StudioError
from .projection import StateProjection

# The identity namespace. Every key this module reads starts with it, and no key
# outside the pinned set below is read at all — a receipt also carries evidence,
# commitment and binding strings, and none of them decide what was picked.
IDENTITY_PREFIX = "archflow:"

# Object-level keys (``inspection.object_user_strings[].attributes``).
COMPONENT_KEY = "archflow:component"
OBJECT_REF_KEY = "archflow:object_ref"
PRODUCER_OP_KEY = "archflow:producer_op"

# Document-level keys (``inspection.document_user_strings``): what file this is,
# not what object was picked.
DESIGN_STATE_DIGEST_KEY = "archflow:design_state_digest"
RUN_ID_KEY = "archflow:run_id"
PROGRAM_DIGEST_KEY = "archflow:program_digest"

# ``archflow:object_ref`` is a CAD-object reference, not a bare name.
OBJECT_REF_PREFIX = "cad-object:"

# How an exported object's name is built from an ``Element@1`` id: ``obj-<id>``,
# and ``obj-<id>-<suffix>`` when one element produced several objects.
OBJECT_NAME_PREFIX = "obj-"

RESOLVED = "resolved"
UNBOUND = "unbound"
UNKNOWN_COMPONENT = "unknown_component"

# What the file says about itself, in three states. ``unknown`` is not
# ``current``: a document that claims no digest has not been shown to be either.
SOURCE_CURRENT = "current"
SOURCE_STALE = "stale"
SOURCE_UNKNOWN = "unknown"

UNBOUND_DETAIL = (
    "the picked object carries no archflow identity; it is not bound to this "
    "project"
)


@dataclass(frozen=True, slots=True)
class PickRequest:
    """One click, as the viewer read it off the loaded file.

    ``user_strings`` and ``document_user_strings`` are forwarded verbatim: the
    client derives nothing, so anything wrong with them is visible here.
    """

    state_digest: str
    user_strings: Mapping[str, str]
    document_user_strings: Mapping[str, str] | None = None
    object_name: str | None = None


@dataclass(frozen=True, slots=True)
class PickResolution:
    """What the server can say the picked object is, and what it cannot."""

    status: str
    component_id: str | None
    element_id: str | None
    operation_id: str | None
    source_state: str
    source_run: str | None
    source_program_digest: str | None
    detail: str | None


def resolve_pick(
    projection: StateProjection, request: PickRequest
) -> PickResolution:
    """Resolve one picked object against the record this projection carries."""

    if request.state_digest != projection.state_digest:
        raise StudioError(
            409,
            "STALE_BASE",
            f"the pick names state {request.state_digest}, but "
            f"{projection.project_id} is at {projection.state_digest}. Read "
            "/api/state again and resolve the pick against the state that "
            "answers now.",
        )
    documents = request.document_user_strings or {}
    source_state = _source_state(documents, projection.state_digest)
    source_run = _text(documents.get(RUN_ID_KEY))
    source_program_digest = _text(documents.get(PROGRAM_DIGEST_KEY))

    def answer(
        status: str,
        *,
        component_id: str | None = None,
        element_id: str | None = None,
        operation_id: str | None = None,
        detail: str | None = None,
    ) -> PickResolution:
        # The document's own claims travel with every outcome: what file this
        # is does not depend on whether the object in it could be named.
        return PickResolution(
            status=status,
            component_id=component_id,
            element_id=element_id,
            operation_id=operation_id,
            source_state=source_state,
            source_run=source_run,
            source_program_digest=source_program_digest,
            detail=detail,
        )

    if not any(
        key.startswith(IDENTITY_PREFIX) for key in request.user_strings
    ):
        return answer(UNBOUND, detail=UNBOUND_DETAIL)
    component_id = _text(request.user_strings.get(COMPONENT_KEY))
    if component_id is None:
        return answer(
            UNKNOWN_COMPONENT,
            detail=(
                f"the picked object carries archflow identity but no "
                f"{COMPONENT_KEY} value, so no component answers for it"
            ),
        )
    if component_id not in _component_ids(projection):
        return answer(
            UNKNOWN_COMPONENT,
            detail=(
                f"the picked object names component {component_id!r}, which "
                f"the State Record of {projection.project_id} at "
                f"{projection.state_digest} does not declare"
            ),
        )
    return answer(
        RESOLVED,
        component_id=component_id,
        element_id=_element_id(
            projection, _object_name(request), component_id
        ),
        operation_id=_text(request.user_strings.get(PRODUCER_OP_KEY)),
    )


def _component_ids(projection: StateProjection) -> frozenset[str]:
    """The components the record declares, straight off its entities.

    Read from the entities rather than from the kernel's component tree so that
    a record whose tree will not build can still resolve a pick: the tree is a
    view of these ids, and a failure to arrange them is not a claim that they
    are absent.
    """

    return frozenset(
        entity.entity_id
        for entity in projection.record.entities_of("Component@1")
    )


def _object_name(request: PickRequest) -> str | None:
    """The exported object's name: the object ref's, or the one the client saw.

    ``archflow:object_ref`` is preferred because the exporter wrote it; the
    viewer's ``objectName`` answers only when the object carries no ref.
    """

    ref = _text(request.user_strings.get(OBJECT_REF_KEY))
    if ref is not None and ref.startswith(OBJECT_REF_PREFIX):
        return ref[len(OBJECT_REF_PREFIX) :]
    return _text(request.object_name)


def _element_id(
    projection: StateProjection,
    object_name: str | None,
    component_id: str,
) -> str | None:
    """The ``Element@1`` row of *this component* that produced that object.

    Many exported objects are not element rows — the villa's openings are
    produced by operations under a component — so ``None`` here is a normal
    answer meaning component-level resolution, not a failure to look.

    Only the resolved component's own elements are considered. An object that
    claims one component and carries a name matching an element of another is
    two claims that disagree, and answering with the element would hand the
    next request an element the picked component does not contain; the
    component still answers, and the element stays unresolved.

    Among that component's elements, an exact ``obj-<id>`` beats a prefix match
    and the longest prefix wins: with elements ``portico`` and ``portico-base``
    both declared, ``obj-portico-base-0`` belongs to the second.
    """

    if object_name is None or not object_name.startswith(OBJECT_NAME_PREFIX):
        return None
    stem = object_name[len(OBJECT_NAME_PREFIX) :]
    prefixed: list[str] = []
    for element in projection.elements:
        if element.component_id != component_id:
            continue
        if stem == element.element_id:
            return element.element_id
        if stem.startswith(f"{element.element_id}-"):
            prefixed.append(element.element_id)
    if not prefixed:
        return None
    return max(prefixed, key=len)


def _source_state(documents: Mapping[str, str], state_digest: str) -> str:
    """Whether the file the object came from is the state answering now."""

    claimed = _text(documents.get(DESIGN_STATE_DIGEST_KEY))
    if claimed is None:
        return SOURCE_UNKNOWN
    return SOURCE_CURRENT if claimed == state_digest else SOURCE_STALE


def _text(value: object) -> str | None:
    """A user string that is actually a string, and not an empty one."""

    return value if isinstance(value, str) and value else None
