"""Evidence-grounded precedent Study without a second design truth.

A Study begins from one exact registered document page. Its editable truth is a
small set of trace-evidence primitives in normalized page coordinates.
Measurements, relations, the CompositionGraph, hypotheses and counterfactual
judgements are deterministic projections of confirmed traces. A saved revision
is an archival snapshot of the method that produced it: cold reads validate its
exact source, its evidence and the agreement between its own retained relations,
receipts and evidence, but never reinterpret retained derivations with today's
rules. A correction runs the current method and records that method on the new
revision.

Nothing here edits a StateRecord, DesignStage, design branch or canonical HEAD.
The existing ``research-evidence-ledger`` record kind is the durable substrate.
Later image/model reasoning may propose traces, but it must enter through this
same evidence contract and can never bypass user correction.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import threading
from typing import Any, Iterable, Mapping

from archflow.contracts.canonical import canonical_digest
from archflow.ports.model import ModelInvocationReceipt, ModelPhase, ModelInvocationStatus
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import RESEARCH_EVIDENCE_LEDGER, STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri, require_identifier
from archflow.project.repository import ProjectRepositoryError

from .artifacts import _registered_document_bytes, list_documents
from .binding import ProjectBinding, record_kind
from ..transport.errors import StudioError


LEDGER_SCHEMA = "EvidenceLedger@1"
LEGACY_LEDGER_KEYS = frozenset({
    "schema",
    "project_id",
    "run_id",
    "study_id",
    "previous_ref",
    "source",
    "evidence",
    "measurements",
    "relations",
    "hypotheses",
    "counterfactuals",
    "canonical_state_changed",
})
CURRENT_DERIVATION_METHOD = "StudyDerivation@1"
LEDGER_KEYS = LEGACY_LEDGER_KEYS | {"derivation_method"}
RESEARCH_LEDGER_KEYS = LEDGER_KEYS | {"research"}
RESEARCH_METHOD = "manual-conjecture-polygon-intervention@1"
STUDY_RUN_PREFIX = "study-"
TRACE_KINDS = frozenset({"envelope", "mass", "void", "floor_plate"})
TRACE_STATUSES = frozenset({"proposed", "confirmed", "rejected"})
TRACE_ORIGINS = frozenset({"machine", "user", "imported"})
_COORD_EPS = 1e-9
# Traces are retained at six decimals, so anything smaller than one
# quantisation step would measure as a zero-area trace the moment it is
# written down. Refuse it at the door instead of retaining that measurement.
_MIN_TRACE_AREA = 1e-6
_ALIGN_TOLERANCE = 0.02
_EQUAL_SIZE_TOLERANCE = 0.03
_CENTRED_VOID_TOLERANCE = 0.05
_DOMINANT_MASS_RATIO = 1.5
_COUNTERFACTUAL_SHIFT = 0.08
_COUNTERFACTUAL_SCALE = 0.82
_study_lock = threading.RLock()


@dataclass(frozen=True, slots=True)
class StudySource:
    run_id: str
    asset_sha256: str
    revision_ref: str | None
    page_index: int
    page_width: float
    page_height: float
    mime_type: str
    document_ref: str

    @property
    def basis_ref(self) -> str:
        revision = self.revision_ref or "uploaded"
        return (
            f"document:{self.run_id}:{self.asset_sha256}:"
            f"{revision}:page:{self.page_index}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "asset_sha256": self.asset_sha256,
            "revision_ref": self.revision_ref,
            "page_index": self.page_index,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "mime_type": self.mime_type,
            "document_ref": self.document_ref,
            "coordinate_frame": "normalized-page-xy-top-left@1",
            "basis_ref": self.basis_ref,
        }


@dataclass(frozen=True, slots=True)
class StudyView:
    ref: ProjectRecordRef
    payload: Mapping[str, Any]
    composition_graph: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def to_dict(self) -> dict[str, float]:
        return {
            "x_min": _q(self.x0),
            "y_min": _q(self.y0),
            "x_max": _q(self.x1),
            "y_max": _q(self.y1),
            "width": _q(self.width),
            "height": _q(self.height),
            "area": _q(self.area),
            "centroid_x": _q(self.cx),
            "centroid_y": _q(self.cy),
        }


def _q(value: float) -> float:
    return round(float(value), 6)


def _study_run_id(study_id: str) -> str:
    try:
        require_identifier(study_id, "study_id")
    except (TypeError, ValueError) as exc:
        raise StudioError(
            422,
            "STUDY_ID_INVALID",
            "Study ids must be stable identifiers.",
        ) from exc
    if len(study_id) > 80:
        raise StudioError(
            422,
            "STUDY_ID_INVALID",
            "Study ids may contain at most 80 characters.",
        )
    return f"{STUDY_RUN_PREFIX}{study_id}"


def _source_document(
    binding: ProjectBinding,
    *,
    run_id: str,
    asset_sha256: str,
    revision_ref: str | None,
    document_ref: str | None,
) -> tuple[Any, ProjectRecordRef]:
    """Resolve one exact StudioSourceDocument record, never a latest match.

    ``revision_ref=None`` means the unversioned uploaded document. It is not a
    wildcard for a later generated drawing that happens to have identical bytes.
    New Study revisions also pin the content-addressed source-document record so
    a cold read never depends on document ordering.
    """

    try:
        documents = [
            row for row in list_documents(binding, run_id)
            if row.asset_sha256 == asset_sha256
            and row.revision_ref == revision_ref
        ]
        refs: list[ProjectRecordRef] = []
        for ref in binding.record_refs(run_id):
            if record_kind(ref) != STUDIO_SOURCE_DOCUMENT:
                continue
            payload = binding.repository.load_json(ref)
            if (
                payload.get("schema") == "StudioSourceDocument@1"
                and payload.get("project_id") == binding.project_id
                and payload.get("run_id") == run_id
                and payload.get("asset_sha256") == asset_sha256
                and payload.get("revisionRef") == revision_ref
            ):
                refs.append(ref)
    except (ProjectRepositoryError, OSError, TypeError, ValueError) as exc:
        raise StudioError(
            404,
            "STUDY_SOURCE_UNREGISTERED",
            "The Study source is not an exact registered document in this project.",
        ) from exc
    if not documents or not refs:
        raise StudioError(
            404,
            "STUDY_SOURCE_UNREGISTERED",
            "The Study source is not an exact registered document in this project.",
        )
    if len(documents) != 1 or len(refs) != 1:
        raise StudioError(
            409,
            "STUDY_SOURCE_AMBIGUOUS",
            "The Study source has competing retained document registrations.",
        )
    ref = refs[0]
    if document_ref is not None:
        try:
            named = record_ref_from_uri(document_ref, binding.project_id)
        except (TypeError, ValueError) as exc:
            raise StudioError(
                409,
                "STUDY_SOURCE_MISMATCH",
                "The retained Study source binding is not a project document reference.",
            ) from exc
        prefix = f"runs/{run_id}/records/"
        if (
            record_kind(named) != STUDIO_SOURCE_DOCUMENT
            or not named.relative_path.startswith(prefix)
            or "/" in named.relative_path[len(prefix):]
            or named != ref
        ):
            raise StudioError(
                409,
                "STUDY_SOURCE_MISMATCH",
                "The retained Study source binding no longer names its exact document record.",
            )
    # Validate the exact registered bytes. This deliberately bypasses
    # document_bytes' convenience lookup, whose null revision is a wildcard.
    _registered_document_bytes(binding, documents[0])
    return documents[0], ref


def _source(
    binding: ProjectBinding,
    *,
    run_id: str,
    asset_sha256: str,
    revision_ref: str | None,
    page_index: int,
    document_ref: str | None = None,
) -> StudySource:
    document, retained_ref = _source_document(
        binding,
        run_id=run_id,
        asset_sha256=asset_sha256,
        revision_ref=revision_ref,
        document_ref=document_ref,
    )
    page = next(
        (item for item in document.pages if item.page_index == page_index),
        None,
    )
    if page is None:
        raise StudioError(
            422,
            "STUDY_SOURCE_PAGE_INVALID",
            "The Study page index is not present in the registered source document.",
        )
    return StudySource(
        run_id=document.run_id,
        asset_sha256=document.asset_sha256,
        revision_ref=document.revision_ref,
        page_index=page.page_index,
        page_width=page.width,
        page_height=page.height,
        mime_type=document.mime_type,
        document_ref=retained_ref.uri,
    )


def _source_payload_matches(retained: Mapping[str, Any], exact: StudySource) -> bool:
    expected = exact.to_dict()
    # EvidenceLedger@1 records written before exact source-record pinning are
    # still resolvable: their run + asset + exact null/non-null revision tuple
    # identifies the old document. New records additionally prove that identity
    # with the content-addressed StudioSourceDocument ref.
    if "document_ref" not in retained:
        expected.pop("document_ref")
    return dict(retained) == expected


def _points(value: object, evidence_id: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) < 3:
        raise StudioError(
            422,
            "STUDY_EVIDENCE_INVALID",
            f"Evidence {evidence_id!r} needs at least three polygon points.",
        )
    result: list[list[float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise StudioError(
                422,
                "STUDY_EVIDENCE_INVALID",
                f"Evidence {evidence_id!r} has an invalid point.",
            )
        x, y = point
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
            or not 0.0 <= float(x) <= 1.0
            or not 0.0 <= float(y) <= 1.0
        ):
            raise StudioError(
                422,
                "STUDY_EVIDENCE_INVALID",
                f"Evidence {evidence_id!r} points must be finite normalized page coordinates.",
            )
        result.append([_q(float(x)), _q(float(y))])
    if len({(point[0], point[1]) for point in result}) < 3:
        raise StudioError(
            422,
            "STUDY_EVIDENCE_INVALID",
            f"Evidence {evidence_id!r} polygon collapses to fewer than three distinct points.",
        )
    if _polygon_area(result) < _MIN_TRACE_AREA:
        raise StudioError(
            422,
            "STUDY_EVIDENCE_INVALID",
            f"Evidence {evidence_id!r} polygon has no measurable area.",
        )
    return result


def _polygon_area(points: list[list[float]]) -> float:
    return abs(
        sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
    ) / 2.0


def _box(points: list[list[float]]) -> _Box:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return _Box(min(xs), min(ys), max(xs), max(ys))


def _evidence(
    source: StudySource,
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Canonicalize request rows and already-retained evidence identically."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise StudioError(
                422,
                "STUDY_EVIDENCE_INVALID",
                "Every trace is an object of evidence fields.",
            )
        evidence_id = raw.get("evidence_id")
        kind = raw.get("kind")
        status = raw.get("status", "proposed")
        origin = raw.get("origin", "user")
        confidence = raw.get("confidence", 1.0 if origin == "user" else 0.5)
        if not isinstance(evidence_id, str):
            raise StudioError(
                422,
                "STUDY_EVIDENCE_INVALID",
                "Every trace needs an evidence id.",
            )
        try:
            require_identifier(evidence_id, "evidence_id")
        except (TypeError, ValueError) as exc:
            raise StudioError(
                422,
                "STUDY_EVIDENCE_INVALID",
                "Evidence ids must be stable identifiers.",
            ) from exc
        if evidence_id in seen:
            raise StudioError(
                422,
                "STUDY_EVIDENCE_INVALID",
                f"Evidence id {evidence_id!r} is duplicated.",
            )
        seen.add(evidence_id)
        if (
            kind not in TRACE_KINDS
            or status not in TRACE_STATUSES
            or origin not in TRACE_ORIGINS
        ):
            raise StudioError(
                422,
                "STUDY_EVIDENCE_INVALID",
                f"Evidence {evidence_id!r} has an unsupported kind, status or origin.",
            )
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            raise StudioError(
                422,
                "STUDY_EVIDENCE_INVALID",
                f"Evidence {evidence_id!r} confidence must be between zero and one.",
            )
        points_value = raw.get("points")
        geometry = raw.get("geometry")
        if points_value is None and isinstance(geometry, Mapping):
            points_value = geometry.get("points")
        points = _points(points_value, evidence_id)
        result.append(
            {
                "evidence_id": evidence_id,
                "primitive": "polygon-trace",
                "kind": kind,
                "geometry": {"type": "polygon", "points": points},
                "status": status,
                "confidence": _q(float(confidence)),
                "origin": origin,
                "basis_refs": [source.basis_ref],
            }
        )
    return sorted(result, key=lambda item: item["evidence_id"])


def _confirmed(
    evidence: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [item for item in evidence if item.get("status") == "confirmed"]


def _measurement_rows(
    evidence: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _confirmed(evidence):
        box = _box(item["geometry"]["points"])
        rows.append(
            {
                "measurement_id": f"bbox-{item['evidence_id']}",
                "evidence_id": item["evidence_id"],
                "method": "normalized-axis-aligned-bounds@1",
                **box.to_dict(),
            }
        )
    return rows


def _contains(a: _Box, b: _Box) -> bool:
    return (
        a.x0 <= b.x0 + _COORD_EPS
        and a.y0 <= b.y0 + _COORD_EPS
        and a.x1 + _COORD_EPS >= b.x1
        and a.y1 + _COORD_EPS >= b.y1
        and (
            a.width > b.width + _COORD_EPS
            or a.height > b.height + _COORD_EPS
        )
    )


def _intersection(a: _Box, b: _Box) -> float:
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(
        0.0,
        min(a.y1, b.y1) - max(a.y0, b.y0),
    )


def _relation_rows(
    evidence: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    items = _confirmed(evidence)
    boxes = {
        item["evidence_id"]: _box(item["geometry"]["points"])
        for item in items
    }
    rows: list[dict[str, Any]] = []
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            a_id = left["evidence_id"]
            b_id = right["evidence_id"]
            a = boxes[a_id]
            b = boxes[b_id]
            facts: list[tuple[str, float | None, str, str]] = []
            if _contains(a, b):
                facts.append(("contains", None, a_id, b_id))
            elif _contains(b, a):
                facts.append(("contains", None, b_id, a_id))
            overlap = _intersection(a, b)
            if overlap > _COORD_EPS:
                union = a.area + b.area - overlap
                facts.append(
                    ("overlaps", overlap / union if union else 0.0, a_id, b_id)
                )
            if abs(a.cx - b.cx) <= _ALIGN_TOLERANCE:
                facts.append(
                    ("aligned_x_center", abs(a.cx - b.cx), a_id, b_id)
                )
            if abs(a.cy - b.cy) <= _ALIGN_TOLERANCE:
                facts.append(
                    ("aligned_y_center", abs(a.cy - b.cy), a_id, b_id)
                )
            if abs(a.width - b.width) <= _EQUAL_SIZE_TOLERANCE:
                facts.append(
                    ("same_width", abs(a.width - b.width), a_id, b_id)
                )
            if abs(a.height - b.height) <= _EQUAL_SIZE_TOLERANCE:
                facts.append(
                    ("same_height", abs(a.height - b.height), a_id, b_id)
                )
            for kind, value, subject, object_id in facts:
                row: dict[str, Any] = {
                    "relation_id": f"{kind}:{subject}:{object_id}",
                    "kind": kind,
                    "subject_evidence_id": subject,
                    "object_evidence_id": object_id,
                    "method": "normalized-bounds-relation@1",
                }
                if value is not None:
                    row["value"] = _q(value)
                rows.append(row)
    return sorted(rows, key=lambda item: item["relation_id"])


def _graph(
    evidence: Iterable[Mapping[str, Any]],
    relations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    nodes = [
        {
            "evidence_id": item["evidence_id"],
            "kind": item["kind"],
            "bounds": _box(item["geometry"]["points"]).to_dict(),
        }
        for item in _confirmed(evidence)
    ]
    nodes.sort(key=lambda item: item["evidence_id"])
    node_ids = {node["evidence_id"] for node in nodes}
    edges = [dict(row) for row in relations]
    edges.sort(key=lambda item: item["relation_id"])
    for edge in edges:
        # An edge is a statement about two traces this graph carries. A
        # retained endpoint naming a trace that is absent, or one the same
        # revision kept proposed or rejected, describes a composition this
        # archive does not hold: serving it would publish a graph whose own
        # edges contradict its nodes. Refusing here is not re-judging the old
        # method's conclusions — it is declining to reconstruct a graph the
        # retained evidence cannot support.
        for end in ("subject_evidence_id", "object_evidence_id"):
            if edge.get(end) not in node_ids:
                raise ValueError(
                    f"relation {edge['relation_id']!r} names {edge.get(end)!r}, "
                    "which is not a confirmed trace of this revision",
                )
    body = {
        "schema": "CompositionGraph@1",
        "nodes": nodes,
        "edges": edges,
    }
    return {**body, "graph_digest": canonical_digest(body, ascii=False)}


def _aligned_columns(
    plates: list[Mapping[str, Any]],
    aligned: set[frozenset[str]],
) -> list[list[str]]:
    """Group plates into columns whose members are all aligned with each other.

    Alignment is a tolerance, so it does not chain: a-b and b-c can both hold
    while a-c does not. Taking connected components would claim a stacking the
    relation layer refused to state, so a plate joins a column only when it is
    aligned with every plate already in it. Sweeping left to right by centre
    makes that choice deterministic and independent of trace names.
    """

    columns: list[list[str]] = []
    for plate in sorted(
        plates,
        key=lambda node: (node["bounds"]["centroid_x"], node["evidence_id"]),
    ):
        plate_id = plate["evidence_id"]
        for column in columns:
            if all(
                frozenset((plate_id, member)) in aligned
                for member in column
            ):
                column.append(plate_id)
                break
        else:
            columns.append([plate_id])
    return sorted(
        (sorted(column) for column in columns if len(column) > 1),
        key=lambda column: column[0],
    )


def _hypotheses(
    evidence: Iterable[Mapping[str, Any]],
    graph: Mapping[str, Any],
) -> list[dict[str, Any]]:
    items = {
        item["evidence_id"]: item
        for item in _confirmed(evidence)
    }
    nodes = {
        node["evidence_id"]: node
        for node in graph["nodes"]
    }
    edges = list(graph["edges"])
    rows: list[dict[str, Any]] = []

    # Dominance is a comparison between masses. An envelope encloses them by
    # definition, so ranking it here would only ever report that an envelope
    # was traced; and a single mass has nothing to dominate.
    masses = sorted(
        (node for node in nodes.values() if node["kind"] == "mass"),
        key=lambda node: (
            -node["bounds"]["area"],
            node["evidence_id"],
        ),
    )
    if len(masses) > 1:
        leader, runner_up = masses[0], masses[1]
        if leader["bounds"]["area"] >= _DOMINANT_MASS_RATIO * runner_up["bounds"]["area"]:
            rows.append(
                {
                    "hypothesis_id": f"dominant-mass:{leader['evidence_id']}",
                    "rule": "dominant_mass",
                    # The verdict rests on exactly this comparison. The masses
                    # ranked below the runner-up agree with it, so they are not
                    # counter-evidence to it.
                    "support_evidence_ids": sorted(
                        {leader["evidence_id"], runner_up["evidence_id"]}
                    ),
                    "counter_evidence_ids": [],
                    "status": "supported",
                }
            )

    for edge in edges:
        if edge["kind"] != "contains":
            continue
        subject = edge["subject_evidence_id"]
        object_id = edge["object_evidence_id"]
        if (
            items[object_id]["kind"] == "void"
            and items[subject]["kind"] in {"mass", "envelope"}
        ):
            rows.append(
                {
                    "hypothesis_id": f"nested-void:{subject}:{object_id}",
                    "rule": "void_nested_in_mass",
                    "support_evidence_ids": [subject, object_id],
                    "counter_evidence_ids": [],
                    "status": "supported",
                }
            )
            a = nodes[subject]["bounds"]
            b = nodes[object_id]["bounds"]
            if (
                abs(a["centroid_x"] - b["centroid_x"]) <= _CENTRED_VOID_TOLERANCE
                and abs(a["centroid_y"] - b["centroid_y"]) <= _CENTRED_VOID_TOLERANCE
            ):
                rows.append(
                    {
                        "hypothesis_id": f"central-void:{subject}:{object_id}",
                        "rule": "void_centrality",
                        "support_evidence_ids": [subject, object_id],
                        "counter_evidence_ids": [],
                        "status": "supported",
                    }
                )

    plates = [
        node for node in nodes.values()
        if node["kind"] == "floor_plate"
    ]
    if len(plates) >= 2:
        plate_ids = {node["evidence_id"] for node in plates}
        aligned = {
            frozenset((edge["subject_evidence_id"], edge["object_evidence_id"]))
            for edge in edges
            if edge["kind"] == "aligned_x_center"
            and edge["subject_evidence_id"] in plate_ids
            and edge["object_evidence_id"] in plate_ids
        }
        for column in _aligned_columns(plates, aligned):
            rows.append(
                {
                    "hypothesis_id": "stacked-floor-plates:" + ":".join(column),
                    "rule": "stacked_floor_plate_alignment",
                    "support_evidence_ids": column,
                    "counter_evidence_ids": sorted(plate_ids - set(column)),
                    "status": "supported",
                }
            )

    for row in rows:
        row["reasoning_receipt"] = {
            "method": "deterministic-study-rules@1",
            "graph_digest": graph["graph_digest"],
            "observe": "confirmed-trace-evidence",
            "normalize": "normalized-page-bounds",
            "hypothesize": row["rule"],
            "falsify": "counterfactual-relation-signature",
        }
    return sorted(rows, key=lambda item: item["hypothesis_id"])


def _copy_evidence(
    evidence: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return json.loads(json.dumps(list(evidence)))


def _transform_points(
    points: list[list[float]],
    *,
    dx: float = 0.0,
    scale: float = 1.0,
) -> list[list[float]] | None:
    """Move a trace inside the page, or refuse rather than fabricate geometry.

    Clamping a transformed point back onto the page silently flattens the
    trace: a shift at the page edge becomes a contraction, and a narrow trace
    collapses to a line this module's own validator would reject. A variant
    that cannot be carried out on this page is not a counterfactual.
    """

    box = _box(points)
    cx = box.cx
    cy = box.cy
    transformed = []
    for x, y in points:
        tx = _q(cx + (x - cx) * scale + dx)
        ty = _q(cy + (y - cy) * scale)
        if not (0.0 <= tx <= 1.0 and 0.0 <= ty <= 1.0):
            return None
        transformed.append([tx, ty])
    if _polygon_area(transformed) < _MIN_TRACE_AREA:
        return None
    return transformed


def _signature(
    evidence: Iterable[Mapping[str, Any]],
    relations: Iterable[Mapping[str, Any]],
) -> set[str]:
    return {
        *(
            f"node:{item['evidence_id']}:{item['kind']}"
            for item in _confirmed(evidence)
        ),
        *(f"edge:{item['relation_id']}" for item in relations),
    }


def _counterfactuals(
    evidence: Iterable[Mapping[str, Any]],
    baseline_relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    confirmed = sorted(
        _confirmed(evidence),
        key=lambda item: item["evidence_id"],
    )
    if not confirmed:
        return []
    baseline_signature = _signature(evidence, baseline_relations)
    # Falsify the trace the composition actually rests on — the one carrying
    # the most relations, then the largest. Preferring a kind, or the first id
    # in the alphabet, would let renaming a trace change what the Study claims
    # to have tested.
    incident = {item["evidence_id"]: 0 for item in confirmed}
    for row in baseline_relations:
        for end in ("subject_evidence_id", "object_evidence_id"):
            if row[end] in incident:
                incident[row[end]] += 1
    target = min(
        confirmed,
        key=lambda item: (
            -incident[item["evidence_id"]],
            -_box(item["geometry"]["points"]).area,
            item["evidence_id"],
        ),
    )
    variants = (
        ("shift_x", {"dx": _COUNTERFACTUAL_SHIFT, "scale": 1.0}),
        ("contract", {"dx": 0.0, "scale": _COUNTERFACTUAL_SCALE}),
        ("remove", None),
    )
    rows: list[dict[str, Any]] = []
    for operation, parameters in variants:
        changed = _copy_evidence(evidence)
        candidate = next(
            item
            for item in changed
            if item["evidence_id"] == target["evidence_id"]
        )
        if operation == "remove":
            candidate["status"] = "rejected"
        else:
            moved = _transform_points(candidate["geometry"]["points"], **parameters)
            if moved is None and operation == "shift_x":
                parameters = {**parameters, "dx": -parameters["dx"]}
                moved = _transform_points(candidate["geometry"]["points"], **parameters)
            if moved is None:
                # The page has no room to carry this variant out on this
                # trace. A clamped one would falsify nothing.
                continue
            candidate["geometry"]["points"] = moved
        relations = _relation_rows(changed)
        signature = _signature(changed, relations)
        union = baseline_signature | signature
        similarity = (
            1.0
            if not union
            else len(baseline_signature & signature) / len(union)
        )
        rows.append(
            {
                "counterfactual_id": f"{operation}:{target['evidence_id']}",
                "target_evidence_id": target["evidence_id"],
                "operation": operation,
                "parameters": parameters or {},
                "relation_signature_similarity": _q(similarity),
                "removed_facts": sorted(baseline_signature - signature),
                "added_facts": sorted(signature - baseline_signature),
                "judgement": (
                    "preserve-family"
                    if similarity >= 0.67
                    else "transition"
                ),
                "method": "composition-signature-jaccard@1",
            }
        )
    return rows


def _derived(
    evidence: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    measurements = _measurement_rows(evidence)
    relations = _relation_rows(evidence)
    graph = _graph(evidence, relations)
    hypotheses = _hypotheses(evidence, graph)
    counterfactuals = _counterfactuals(evidence, relations)
    return measurements, relations, graph, hypotheses, counterfactuals


def _polygon_observations(
    source: StudySource, evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from monkeydiagram import study as drawing_study

    return drawing_study.polygon_observations(
        [{"evidence_id": row["evidence_id"], "kind": row["kind"],
          "points": row["geometry"]["points"]} for row in _confirmed(evidence)],
        page_width=source.page_width, page_height=source.page_height,
    )


def _research_links(research: Mapping[str, Any], evidence: list[dict[str, Any]]) -> None:
    """Check references without reinterpreting the person's arguments."""
    def identifiers(rows, key):
        if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
            raise ValueError("Research entries must be object lists.")
        result = [row[key] for row in rows]
        if not all(isinstance(value, str) and value for value in result) or len(set(result)) != len(result):
            raise ValueError(f"Research {key} values must be unique nonempty identifiers.")
        return set(result)

    def refs(row, field, allowed):
        values = row[field]
        if not isinstance(values, list) or not all(isinstance(value, str) and value in allowed for value in values):
            raise ValueError(f"Research {field} names an absent source, trace or hypothesis.")

    evidence_ids = {row["evidence_id"] for row in evidence}
    source_ids = identifiers(research["historical_sources"], "source_id")
    hypothesis_ids = identifiers(research["hypotheses"], "hypothesis_id")
    identifiers(research["gaps"], "gap_id")
    identifiers(research["counterfactuals"], "counterfactual_id")
    for hypothesis in research["hypotheses"]:
        refs(hypothesis, "evidence_ids", evidence_ids)
        if "counter_evidence_ids" in hypothesis:
            refs(hypothesis, "counter_evidence_ids", evidence_ids)
        refs(hypothesis, "historical_source_ids", source_ids)
        refs(hypothesis, "competes_with", hypothesis_ids - {hypothesis["hypothesis_id"]})
    for gap in research["gaps"]:
        refs(gap, "evidence_ids", evidence_ids)
    for intervention in research["counterfactuals"]:
        refs(intervention, "hypothesis_ids", hypothesis_ids)
        if intervention["target_evidence_id"] not in evidence_ids:
            raise ValueError("A counterfactual target must name an existing trace.")
    pattern = research["composition_pattern"]
    if pattern is not None:
        refs(pattern, "evidence_ids", evidence_ids)
    prior = research["design_prior"]
    if prior is not None:
        refs(prior, "hypothesis_ids", hypothesis_ids)
        if pattern is None or prior["pattern_id"] != pattern["pattern_id"]:
            raise ValueError("A DesignPrior must name this Study's CompositionPattern.")
        if prior["preference_status"] == "stated" and not prior["preference"].strip():
            raise ValueError("A stated preference needs the person's actual preference text.")
        context = prior["changed_context"]
        if context and context["decision"] != "unresolved":
            if not any(value.strip() for value in context["changed_conditions"]) or not context["reason"].strip():
                raise ValueError("A changed-context decision needs changed conditions and a reason.")
            if context["decision"] == "revise" and not context["revised_statement"].strip():
                raise ValueError("Revising a prior needs the revised statement.")


def _research_intervention(source, evidence, intervention, baseline) -> dict[str, Any] | None:
    if not intervention["execute"]:
        return None
    if not intervention["prediction"].strip() or not any(value.strip() for value in intervention["conditions"]) or not intervention["hypothesis_ids"]:
        raise ValueError("An executed counterfactual needs its prior prediction, conditions and hypotheses.")
    changed = _copy_evidence(evidence)
    target = next(row for row in changed if row["evidence_id"] == intervention["target_evidence_id"])
    if target["status"] != "confirmed":
        return {"status": "unsupported", "method": "aspect-correct-polygon-intervention@1",
                "reason": "The intervention target has not been confirmed by the user."}
    parameters = intervention["parameters"]
    operation = intervention["operation"]
    if operation == "remove":
        target["status"] = "rejected"
    else:
        points = target["geometry"]["points"]
        # Uniform page-coordinate scaling preserves physical similarity even
        # on a rectangular page. Translation is expressed in page fractions.
        box = _box(points)
        scale = parameters["scale"] if operation == "scale" else 1.0
        dx = parameters["dx"] if operation == "translate" else 0.0
        dy = parameters["dy"] if operation == "translate" else 0.0
        moved = [[_q(box.cx + (x - box.cx) * scale + dx),
                  _q(box.cy + (y - box.cy) * scale + dy)] for x, y in points]
        if any(not 0 <= coordinate <= 1 for point in moved for coordinate in point):
            return {"status": "unsupported", "method": "aspect-correct-polygon-intervention@1",
                    "reason": "The requested geometry leaves the source page; it was not clamped."}
        target["geometry"]["points"] = moved
    if changed == evidence:
        return {"status": "unsupported", "method": "aspect-correct-polygon-intervention@1",
                "reason": "The requested intervention makes no geometric change."}
    try:
        measurements, relations = _polygon_observations(source, changed)
    except ValueError as exc:
        return {"status": "unsupported", "method": "aspect-correct-polygon-intervention@1", "reason": str(exc)}
    baseline_measurements, baseline_relations = baseline
    before = _signature(evidence, baseline_relations)
    after = _signature(changed, relations)
    union = before | after
    return {
        "status": "computed", "category": "computed", "method": "aspect-correct-polygon-intervention@1",
        "metric_frame": "page-aspect-correct-long-edge@1",
        "evidence": changed, "measurements": measurements, "relations": relations,
        "baseline_measurements": baseline_measurements, "baseline_relations": baseline_relations,
        "removed_facts": sorted(before - after), "added_facts": sorted(after - before),
        "relation_signature_similarity": _q(len(before & after) / len(union)) if union else 1.0,
        "interpretation": "underdetermined",
    }


def _research_snapshot(source, evidence, research) -> dict[str, Any]:
    result = json.loads(json.dumps(research, allow_nan=False))
    _research_links(result, evidence)
    try:
        baseline = _polygon_observations(source, evidence)
    except ValueError as exc:
        baseline = None
        unsupported = str(exc)
    for row in result["counterfactuals"]:
        if row["execute"] and (not row["prediction"].strip() or not any(value.strip() for value in row["conditions"]) or not row["hypothesis_ids"]):
            raise ValueError("An executed counterfactual needs its prior prediction, conditions and hypotheses.")
        row["actual"] = (
            _research_intervention(source, evidence, row, baseline) if baseline is not None
            else ({"status": "unsupported", "method": "aspect-correct-polygon-intervention@1", "reason": unsupported}
                  if row["execute"] else None)
        )
    missing = []
    hypotheses = result["hypotheses"]
    if not result["question"].strip():
        missing.append("research-question")
    if len(hypotheses) < 2 or not any(row["competes_with"] for row in hypotheses):
        missing.append("two-competing-explanations")
    if any(not row["statement"].strip() or not any(value.strip() for value in row["assumptions"]) or not row["falsification"].strip()
           or not (row["evidence_ids"] or row["historical_source_ids"]) for row in hypotheses):
        missing.append("evidence-conditions-and-falsification")
    if not any(row["description"].strip() for row in result["gaps"]):
        missing.append("evidence-gap")
    executed = [row for row in result["counterfactuals"] if row["actual"] and row["actual"]["status"] == "computed"]
    distinct_results = {json.dumps(row["actual"]["evidence"], sort_keys=True) for row in executed}
    if not 3 <= len(distinct_results) <= 5:
        missing.append("three-to-five-computed-interventions")
    pattern, prior = result["composition_pattern"], result["design_prior"]
    if not pattern or not pattern["rule"].strip() or not pattern["evidence_ids"] or not any(value.strip() for value in pattern["conditions"]):
        missing.append("composition-pattern")
    if not prior or not prior["statement"].strip() or not prior["hypothesis_ids"] or not any(value.strip() for value in prior["conditions"]):
        missing.append("conditional-design-prior")
    context = prior and prior["changed_context"]
    if not context or context["decision"] not in {"retain", "revise", "reject"}:
        missing.append("changed-context-decision")
    result.update({
        "method": RESEARCH_METHOD, "source_binding": source.to_dict(),
        "observations": None if baseline is None else {"category": "computed", "method": "aspect-correct-polygon@1",
            "metric_frame": "page-aspect-correct-long-edge@1", "measurements": baseline[0], "relations": baseline[1]},
        "completion": {"ready": not missing, "missing": missing},
    })
    return result


def _check_comparison_results(binding, research, results) -> None:
    """Verify archived inputs and result structure without rerunning comparison."""
    definitions = research.get("comparisons", [])
    if (not isinstance(definitions, list) or len(definitions) > 6
            or not isinstance(results, list) or len(definitions) != len(results)):
        raise ValueError("Each research comparison needs its exact retained result.")
    for definition, result in zip(definitions, results):
        if not isinstance(definition, Mapping) or not isinstance(result, Mapping):
            raise ValueError("Research comparisons and results must be objects.")
        requested = definition.get("studies")
        projected = result.get("studies")
        if (not isinstance(requested, list) or not 2 <= len(requested) <= 6
                or not isinstance(projected, list) or len(projected) != len(requested)
                or result.get("schema") != "StudyComparison@1"
                or result.get("project_id") != binding.project_id
                or not isinstance(result.get("method"), str) or not result["method"]
                or not isinstance(result.get("metric_frame"), str) or not result["metric_frame"]
                or result.get("canonical_state_changed") is not False):
            raise ValueError("The retained comparison result has a different input or method contract.")
        identities = set()
        for reference, projection in zip(requested, projected):
            if not isinstance(reference, Mapping) or not isinstance(projection, Mapping):
                raise ValueError("Comparison entries must name an exact Study revision.")
            study_id, ledger_ref = reference["study_id"], reference["ledger_ref"]
            identity = (study_id, ledger_ref)
            if identity in identities or any(projection.get(key) != reference[key] for key in ("study_id", "ledger_ref")):
                raise ValueError("The comparison result disagrees with its requested revisions.")
            identities.add(identity)
            named = _own_ledger_ref(ledger_ref, binding, _study_run_id(study_id))
            if named is None:
                raise ValueError("A comparison reference belongs to another Study or project.")
            archived = _identity(binding, named, study_id)
            if projection.get("source") != archived.get("source"):
                raise ValueError("The comparison projection names another source page.")
        for field in ("shared_topology", "pairwise"):
            if not isinstance(result.get(field), list) or not all(isinstance(row, Mapping) for row in result[field]):
                raise ValueError("The retained comparison results are structurally incomplete.")
        pairs = set()
        for row in result["pairwise"]:
            left = (row["left"]["study_id"], row["left"]["ledger_ref"])
            right = (row["right"]["study_id"], row["right"]["ledger_ref"])
            pair = frozenset((left, right))
            if left not in identities or right not in identities or left == right or pair in pairs:
                raise ValueError("A retained comparison pair names another input revision.")
            pairs.add(pair)
        if len(pairs) != len(identities) * (len(identities) - 1) // 2:
            raise ValueError("The retained comparison pair list is incomplete.")


def _ledger_refs(
    binding: ProjectBinding,
    run_id: str,
) -> tuple[ProjectRecordRef, ...]:
    if run_id not in binding.run_ids():
        return ()
    try:
        run = binding.load_run(run_id)
        refs = binding.repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run_id,
            ),
        )
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(
            409,
            "STUDY_LEDGER_UNREADABLE",
            "This Study's retained run cannot be read back from its project.",
        ) from exc
    return tuple(
        ref for ref in refs if record_kind(ref) == RESEARCH_EVIDENCE_LEDGER
    )


def _own_ledger_ref(
    value: object,
    binding: ProjectBinding,
    run_id: str,
) -> ProjectRecordRef | None:
    """The retained ledger of this Study run that ``value`` names, if any."""

    if not isinstance(value, str):
        return None
    try:
        ref = record_ref_from_uri(value, binding.project_id)
    except (TypeError, ValueError):
        return None
    if record_kind(ref) != RESEARCH_EVIDENCE_LEDGER:
        return None
    prefix = f"runs/{run_id}/records/"
    if not ref.relative_path.startswith(prefix):
        return None
    if "/" in ref.relative_path[len(prefix) :]:
        return None
    return ref


def _identity(
    binding: ProjectBinding,
    ref: ProjectRecordRef,
    study_id: str,
) -> dict[str, Any]:
    """Read a retained ledger and check only whose revision it is."""

    try:
        payload = binding.repository.load_json(ref)
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(
            404,
            "STUDY_LEDGER_NOT_FOUND",
            "No retained Study ledger answers that reference in this project.",
        ) from exc
    keys = frozenset(payload)
    if keys - {"model_invocations"} not in {LEGACY_LEDGER_KEYS, LEDGER_KEYS, RESEARCH_LEDGER_KEYS} or (
        payload.get("schema") != LEDGER_SCHEMA
        or payload.get("project_id") != binding.project_id
        or payload.get("study_id") != study_id
        or payload.get("run_id") != _study_run_id(study_id)
        or payload.get("canonical_state_changed") is not False
    ):
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study ledger has a different project, study, run or authority contract.",
        )
    if "derivation_method" in payload and (
        not isinstance(payload["derivation_method"], str)
        or not payload["derivation_method"]
    ):
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study method name is invalid.",
        )
    previous = payload["previous_ref"]
    if previous is not None and _own_ledger_ref(
        previous,
        binding,
        _study_run_id(study_id),
    ) is None:
        raise StudioError(
            409,
            "STUDY_REVISION_INVALID",
            "A Study revision does not name a predecessor retained by this Study run.",
        )
    return payload


def _load_payload(
    binding: ProjectBinding,
    ref: ProjectRecordRef,
    study_id: str,
) -> dict[str, Any]:
    payload = _identity(binding, ref, study_id)
    source = payload.get("source")
    evidence = payload.get("evidence")
    if not isinstance(source, Mapping) or not isinstance(evidence, list):
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study ledger is structurally incomplete.",
        )
    try:
        exact_source = _source(
            binding,
            run_id=source.get("run_id"),
            asset_sha256=source.get("asset_sha256"),
            revision_ref=source.get("revision_ref"),
            page_index=source.get("page_index"),
            document_ref=source.get("document_ref"),
        )
    except (StudioError, TypeError, ValueError) as exc:
        raise StudioError(
            409,
            "STUDY_SOURCE_MISMATCH",
            "The retained Study source no longer resolves to the exact registered page it names.",
        ) from exc
    if not _source_payload_matches(source, exact_source):
        raise StudioError(
            409,
            "STUDY_SOURCE_MISMATCH",
            "The retained Study source no longer resolves to the exact registered page it names.",
        )
    try:
        normalized = _evidence(exact_source, evidence)
    except StudioError as exc:
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study evidence is invalid.",
        ) from exc
    if normalized != evidence:
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study evidence is not canonical.",
        )
    if "model_invocations" in payload:
        try:
            if not isinstance(payload["model_invocations"], list):
                raise ValueError("Invalid model invocation list")
            for row in payload["model_invocations"]:
                receipt = ModelInvocationReceipt.from_dict(row)
                request_payload = receipt.request.payload
                if (receipt.request.phase != ModelPhase.RESEARCH
                        or receipt.status != ModelInvocationStatus.SUCCESS
                        or request_payload["source"] != source):
                    raise ValueError("Model receipt names another source or phase")
                input_ref = _own_ledger_ref(request_payload["ledger_ref"], binding, _study_run_id(study_id))
                if input_ref is None:
                    raise ValueError("Model receipt names another Study")
                input_ledger = _identity(binding, input_ref, study_id)
                if (input_ledger["source"] != source
                        or request_payload.get("evidence") != input_ledger["evidence"]
                        or request_payload.get("research") != input_ledger.get("research")
                        or receipt.request.context_digest != canonical_digest(request_payload, ascii=False)
                        or receipt.request.checkpoint_digest != _graph(input_ledger["evidence"], input_ledger["relations"])["graph_digest"]):
                    raise ValueError("Model receipt does not describe its exact retained input revision")
        except (KeyError, TypeError, ValueError, StudioError) as exc:
            raise StudioError(409, "STUDY_LEDGER_INVALID", "The retained Study model receipt is invalid.") from exc
    for field in ("measurements", "relations", "hypotheses", "counterfactuals"):
        rows = payload.get(field)
        # Shape only: what an older method concluded is not re-judged here, but
        # a retained finding is still a row of fields. Without this a corrupt
        # row reaches the wire contract and fails there as an unexplained 500
        # instead of naming the ledger that cannot be read.
        if not isinstance(rows, list) or not all(
            isinstance(row, Mapping) for row in rows
        ):
            raise StudioError(
                409,
                "STUDY_LEDGER_INVALID",
                "The retained Study derivation snapshot is structurally incomplete.",
            )
    if "research" in payload:
        try:
            research = payload["research"]
            if not isinstance(research, Mapping) or not isinstance(research.get("method"), str) or not research["method"]:
                raise ValueError("The research snapshot has no method.")
            if research["source_binding"] != source:
                raise ValueError("The research snapshot names another source page.")
            _research_links(research, evidence)
            _check_comparison_results(binding, research, research.get("comparison_results", []))
            for row in research["counterfactuals"]:
                if row["actual"] is not None and (not isinstance(row["actual"], Mapping)
                                                 or row["actual"].get("status") not in {"computed", "unsupported"}):
                    raise ValueError("The retained counterfactual result is unreadable.")
                actual = row["actual"]
                if actual and actual["status"] == "computed":
                    retained = actual["evidence"]
                    if _evidence(exact_source, retained) != retained:
                        raise ValueError("The retained intervention evidence is not canonical.")
                    originals = {item["evidence_id"]: item for item in evidence}
                    if {item["evidence_id"] for item in retained} != set(originals):
                        raise ValueError("The retained intervention changed the evidence identity set.")
                    if any(item != originals[item["evidence_id"]] for item in retained
                           if item["evidence_id"] != row["target_evidence_id"]):
                        raise ValueError("The retained intervention altered a trace outside its target.")
        except (KeyError, TypeError, ValueError, AttributeError, StudioError) as exc:
            raise StudioError(409, "STUDY_LEDGER_INVALID", "The retained Study research is invalid.") from exc
    # Retained derivations are historical evidence, not a cache. Re-running a
    # newer method here would rewrite the meaning of an old content-addressed
    # revision and make method evolution break archive readability.
    return payload


def _current_ref(
    binding: ProjectBinding,
    study_id: str,
) -> ProjectRecordRef | None:
    run_id = _study_run_id(study_id)
    refs = _ledger_refs(binding, run_id)
    if not refs:
        return None
    by_uri = {ref.uri: ref for ref in refs}
    referenced: set[str] = set()
    for ref in refs:
        # Choosing a head needs each revision's link, not its reasoning.
        # Recomputation belongs to the revision a caller actually reads or
        # extends: re-deriving every ancestor here would let one superseded
        # revision make the current head unreadable and uncorrectable.
        previous = _identity(binding, ref, study_id).get("previous_ref")
        if previous is not None:
            if previous not in by_uri:
                raise StudioError(
                    409,
                    "STUDY_REVISION_INVALID",
                    "A Study revision points outside its retained revision set.",
                )
            referenced.add(previous)
    heads = [ref for uri, ref in by_uri.items() if uri not in referenced]
    if len(heads) != 1:
        raise StudioError(
            409,
            "STUDY_REVISION_CONFLICT",
            "This Study has competing retained heads; choose and reconcile one before continuing.",
        )
    return heads[0]


def _receipts_bound_to(payload: Mapping[str, Any], graph_digest: str) -> None:
    """Refuse a retained finding whose own receipt names another graph.

    A reasoning receipt states which composition the finding was read off. It
    is checked against the graph rebuilt from this revision's own retained
    evidence and relations, not against today's rules or thresholds: what the
    old method concluded stands, but it may not be served under a graph digest
    it never bound itself to.
    """

    for field in ("measurements", "relations", "hypotheses", "counterfactuals"):
        for row in payload[field]:
            if "reasoning_receipt" not in row:
                # Revisions retained before findings carried a receipt stay
                # readable; no stamp is invented on their behalf.
                continue
            receipt = row["reasoning_receipt"]
            if (
                not isinstance(receipt, Mapping)
                or receipt.get("graph_digest") != graph_digest
            ):
                raise StudioError(
                    409,
                    "STUDY_LEDGER_INVALID",
                    "A retained Study finding carries a reasoning receipt bound to another composition graph.",
                )


def read_study(
    binding: ProjectBinding,
    study_id: str,
    ledger_ref: str | None = None,
) -> StudyView:
    run_id = _study_run_id(study_id)
    if ledger_ref is None:
        ref = _current_ref(binding, study_id)
        if ref is None:
            raise StudioError(
                404,
                "STUDY_NOT_FOUND",
                f"Study {study_id!r} has no retained evidence ledger.",
            )
    else:
        named = _own_ledger_ref(ledger_ref, binding, run_id)
        if named is None:
            raise StudioError(
                422,
                "STUDY_LEDGER_REF_INVALID",
                "The ledger reference does not belong to this Study run.",
            )
        ref = named
    payload = _load_payload(binding, ref, study_id)
    # CompositionGraph@1 is rebuilt only from the retained evidence and
    # retained relation snapshot. It does not invoke today's hypotheses or
    # counterfactual method.
    try:
        graph = _graph(payload["evidence"], payload["relations"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study relation snapshot cannot form its CompositionGraph.",
        ) from exc
    _receipts_bound_to(payload, graph["graph_digest"])
    return StudyView(ref, payload, graph)


def list_studies(
    binding: ProjectBinding, *, source_run_id: str | None = None,
    asset_sha256: str | None = None, page_index: int | None = None,
) -> list[StudyView]:
    """Discover saved Studies through their existing P036 runs, without an index."""
    result = []
    for run_id in sorted(binding.run_ids()):
        if not run_id.startswith(STUDY_RUN_PREFIX):
            continue
        study_id = run_id[len(STUDY_RUN_PREFIX):]
        current = _current_ref(binding, study_id)
        if current is None:
            continue
        payload = _identity(binding, current, study_id)
        source = payload.get("source", {})
        if not isinstance(source, Mapping):
            raise StudioError(409, "STUDY_LEDGER_INVALID", "The retained Study source is structurally incomplete.")
        if ((source_run_id is not None and source.get("run_id") != source_run_id)
                or (asset_sha256 is not None and source.get("asset_sha256") != asset_sha256)
                or (page_index is not None and source.get("page_index") != page_index)):
            continue
        result.append(read_study(binding, study_id, current.uri))
    return result


def _same_revision_content(
    previous: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> bool:
    """Ignore only the revision link when deciding an exact idempotent retry."""

    return (
        {key: value for key, value in previous.items() if key != "previous_ref"}
        == {key: value for key, value in candidate.items() if key != "previous_ref"}
    )


def save_study(
    binding: ProjectBinding,
    *,
    study_id: str,
    source_run_id: str,
    asset_sha256: str,
    revision_ref: str | None,
    page_index: int,
    evidence_rows: Iterable[Mapping[str, Any]],
    expected_previous_ref: str | None,
    research: Mapping[str, Any] | None = None,
    model_receipt: ModelInvocationReceipt | None = None,
    comparison_results: list[Mapping[str, Any]] | None = None,
) -> StudyView:
    """Save one corrected evidence revision without writing design state."""

    run_id = _study_run_id(study_id)
    source = _source(
        binding,
        run_id=source_run_id,
        asset_sha256=asset_sha256,
        revision_ref=revision_ref,
        page_index=page_index,
    )
    evidence = _evidence(source, evidence_rows)
    measurements, relations, graph, hypotheses, counterfactuals = _derived(evidence)
    research_snapshot = None
    if research is not None:
        try:
            research_snapshot = _research_snapshot(source, evidence, research)
        except (KeyError, TypeError, ValueError) as exc:
            raise StudioError(422, "STUDY_RESEARCH_INVALID", str(exc)) from exc

    with _study_lock:
        current = _current_ref(binding, study_id)
        actual_previous = None if current is None else current.uri
        if expected_previous_ref != actual_previous:
            raise StudioError(
                409,
                "STUDY_REVISION_STALE",
                "The Study changed. Reload its current evidence ledger before saving corrections.",
            )
        previous_payload: Mapping[str, Any] | None = None
        if current is not None:
            previous_payload = _load_payload(binding, current, study_id)
            if "research" in previous_payload and research is None:
                raise StudioError(409, "STUDY_RESEARCH_REQUIRED", "Reload and include this Study's research before saving corrections.")
            if not _source_payload_matches(previous_payload["source"], source):
                raise StudioError(
                    409,
                    "STUDY_SOURCE_IMMUTABLE",
                    "A Study revision cannot switch to another source page; start another Study.",
                )

        if research_snapshot is not None:
            previous_research = (previous_payload or {}).get("research") or {}
            if comparison_results is None:
                if research_snapshot.get("comparisons", []) != previous_research.get("comparisons", []):
                    raise StudioError(422, "STUDY_COMPARISON_REQUIRED", "Changed comparison references need server-computed results before they can be retained.")
                retained_comparisons = previous_research.get("comparison_results", [])
            else:
                retained_comparisons = json.loads(json.dumps(comparison_results, allow_nan=False))
            try:
                _check_comparison_results(binding, research_snapshot, retained_comparisons)
            except (KeyError, TypeError, ValueError, StudioError) as exc:
                raise StudioError(422, "STUDY_COMPARISON_INVALID", "The comparison result must name the exact retained revisions requested by this research.") from exc
            research_snapshot["comparison_results"] = retained_comparisons

        payload = {
            "schema": LEDGER_SCHEMA,
            "project_id": binding.project_id,
            "run_id": run_id,
            "study_id": study_id,
            "previous_ref": actual_previous,
            "source": source.to_dict(),
            "evidence": evidence,
            "measurements": measurements,
            "relations": relations,
            "hypotheses": hypotheses,
            "counterfactuals": counterfactuals,
            "derivation_method": CURRENT_DERIVATION_METHOD,
            "canonical_state_changed": False,
        }
        if research_snapshot is not None:
            payload["research"] = research_snapshot
        invocations = list((previous_payload or {}).get("model_invocations", []))
        if model_receipt is not None:
            if (model_receipt.request.phase != ModelPhase.RESEARCH
                    or model_receipt.status != ModelInvocationStatus.SUCCESS
                    or model_receipt.request.payload.get("source") != source.to_dict()
                    or model_receipt.request.payload.get("ledger_ref") != actual_previous
                    or previous_payload is None
                    or model_receipt.request.payload.get("evidence") != previous_payload["evidence"]
                    or model_receipt.request.payload.get("research") != previous_payload.get("research")
                    or model_receipt.request.context_digest != canonical_digest(model_receipt.request.payload, ascii=False)
                    or model_receipt.request.checkpoint_digest != _graph(previous_payload["evidence"], previous_payload["relations"])["graph_digest"]):
                raise StudioError(422, "STUDY_MODEL_BINDING_INVALID", "The model response must name this exact Study source and previous revision.")
            invocations.append(model_receipt.to_dict())
        if invocations:
            payload["model_invocations"] = invocations
        if (
            current is not None
            and previous_payload is not None
            and _same_revision_content(previous_payload, payload)
        ):
            return StudyView(current, previous_payload, graph)

        try:
            run = (
                binding.load_run(run_id)
                if run_id in binding.run_ids()
                else binding.repository.create_run(run_id)
            )
            ref = binding.repository.put_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=run_id,
                ),
                record_kind=RESEARCH_EVIDENCE_LEDGER,
                payload=payload,
            )
        except (ProjectRepositoryError, OSError, ValueError) as exc:
            raise StudioError(
                409,
                "STUDY_WRITE_FAILED",
                "The Study evidence ledger could not be retained in its project.",
            ) from exc

    # A Study has no canonical writer at all: it only creates its own run and
    # retains run records, and neither can move HEAD or a design branch. So a
    # HEAD that differs across a save is someone else issuing a version, which
    # this save may not refuse — the ledger it just retained is already valid.
    return read_study(binding, study_id, ref.uri)
