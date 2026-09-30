"""The Study method: deterministic projections of a precedent drawing's confirmed polygon traces.

A Study begins from one exact registered document page. Its editable truth is a
small set of trace-evidence primitives in normalized page coordinates. This module
is the method that reads them: it canonicalizes trace rows (``canonical_evidence``),
derives measurements, relations, the CompositionGraph, hypotheses and
counterfactual judgements from the confirmed traces (``derive``), observes the
polygons in an aspect-correct page frame (``polygon_observations``) and checks a
research snapshot's links, runs its interventions and states its completion
(``research_snapshot_of``). Values describe 2D drawings only; no filesystem,
project state, source acceptance or design preference belongs here.

The Project Runtime keeps the retained ledger: the exact source page, revisions,
cold reads that replay a revision's own findings, and writes (#519). A trace the
method cannot take is a ``StudyEvidenceError`` naming its refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Iterable, Mapping

from archflow.contracts.canonical import canonical_digest
from archflow.project.refs import require_identifier

CURRENT_DERIVATION_METHOD = "StudyDerivation@1"
RESEARCH_METHOD = "manual-conjecture-polygon-intervention@1"
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


class StudyEvidenceError(ValueError):
    """A trace the method cannot take; the message says which and why."""

    code = "STUDY_EVIDENCE_INVALID"


@dataclass(frozen=True, slots=True)
class StudySource:
    """The exact registered page a Study reads, and the frame its traces are written in."""

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
class Box:
    """A trace's axis-aligned bounds in its page frame."""

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
            "x_min": rounded(self.x0),
            "y_min": rounded(self.y0),
            "x_max": rounded(self.x1),
            "y_max": rounded(self.y1),
            "width": rounded(self.width),
            "height": rounded(self.height),
            "area": rounded(self.area),
            "centroid_x": rounded(self.cx),
            "centroid_y": rounded(self.cy),
        }


def _points(value: object, evidence_id: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) < 3:
        raise StudyEvidenceError(
            f"Evidence {evidence_id!r} needs at least three polygon points.",
        )
    result: list[list[float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise StudyEvidenceError(
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
            raise StudyEvidenceError(
                f"Evidence {evidence_id!r} points must be finite normalized page coordinates.",
            )
        result.append([rounded(float(x)), rounded(float(y))])
    if len({(point[0], point[1]) for point in result}) < 3:
        raise StudyEvidenceError(
            f"Evidence {evidence_id!r} polygon collapses to fewer than three distinct points.",
        )
    if _polygon_area(result) < _MIN_TRACE_AREA:
        raise StudyEvidenceError(
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


def box_of(points: list[list[float]]) -> Box:
    """The bounds of one polygon's points."""

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return Box(min(xs), min(ys), max(xs), max(ys))


def canonical_evidence(
    source: StudySource,
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Canonicalize request rows and already-retained evidence identically."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise StudyEvidenceError(
                "Every trace is an object of evidence fields.",
            )
        evidence_id = raw.get("evidence_id")
        kind = raw.get("kind")
        status = raw.get("status", "proposed")
        origin = raw.get("origin", "user")
        confidence = raw.get("confidence", 1.0 if origin == "user" else 0.5)
        if not isinstance(evidence_id, str):
            raise StudyEvidenceError(
                "Every trace needs an evidence id.",
            )
        try:
            require_identifier(evidence_id, "evidence_id")
        except (TypeError, ValueError) as exc:
            raise StudyEvidenceError(
                "Evidence ids must be stable identifiers.",
            ) from exc
        if evidence_id in seen:
            raise StudyEvidenceError(
                f"Evidence id {evidence_id!r} is duplicated.",
            )
        seen.add(evidence_id)
        if (
            kind not in TRACE_KINDS
            or status not in TRACE_STATUSES
            or origin not in TRACE_ORIGINS
        ):
            raise StudyEvidenceError(
                f"Evidence {evidence_id!r} has an unsupported kind, status or origin.",
            )
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            raise StudyEvidenceError(
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
                "confidence": rounded(float(confidence)),
                "origin": origin,
                "basis_refs": [source.basis_ref],
            }
        )
    return sorted(result, key=lambda item: item["evidence_id"])


def confirmed_traces(
    evidence: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """The traces a person confirmed: the only ones the method reads."""

    return [item for item in evidence if item.get("status") == "confirmed"]


def _measurement_rows(
    evidence: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in confirmed_traces(evidence):
        box = box_of(item["geometry"]["points"])
        rows.append(
            {
                "measurement_id": f"bbox-{item['evidence_id']}",
                "evidence_id": item["evidence_id"],
                "method": "normalized-axis-aligned-bounds@1",
                **box.to_dict(),
            }
        )
    return rows


def _contains(a: Box, b: Box) -> bool:
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


def _intersection(a: Box, b: Box) -> float:
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(
        0.0,
        min(a.y1, b.y1) - max(a.y0, b.y0),
    )


def relation_rows(
    evidence: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Every bounds relation between two confirmed traces, by relation id."""

    items = confirmed_traces(evidence)
    boxes = {
        item["evidence_id"]: box_of(item["geometry"]["points"])
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
                    row["value"] = rounded(value)
                rows.append(row)
    return sorted(rows, key=lambda item: item["relation_id"])


def composition_graph(
    evidence: Iterable[Mapping[str, Any]],
    relations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """The CompositionGraph@1 of the confirmed traces and the relations given, with its digest."""

    nodes = [
        {
            "evidence_id": item["evidence_id"],
            "kind": item["kind"],
            "bounds": box_of(item["geometry"]["points"]).to_dict(),
        }
        for item in confirmed_traces(evidence)
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
        for item in confirmed_traces(evidence)
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

    box = box_of(points)
    cx = box.cx
    cy = box.cy
    transformed = []
    for x, y in points:
        tx = rounded(cx + (x - cx) * scale + dx)
        ty = rounded(cy + (y - cy) * scale)
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
            for item in confirmed_traces(evidence)
        ),
        *(f"edge:{item['relation_id']}" for item in relations),
    }


def _counterfactuals(
    evidence: Iterable[Mapping[str, Any]],
    baseline_relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    confirmed = sorted(
        confirmed_traces(evidence),
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
            -box_of(item["geometry"]["points"]).area,
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
        relations = relation_rows(changed)
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
                "relation_signature_similarity": rounded(similarity),
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


def derive(
    evidence: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    measurements = _measurement_rows(evidence)
    relations = relation_rows(evidence)
    graph = composition_graph(evidence, relations)
    hypotheses = _hypotheses(evidence, graph)
    counterfactuals = _counterfactuals(evidence, relations)
    return measurements, relations, graph, hypotheses, counterfactuals


def _polygon_observations(
    source: StudySource, evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return polygon_observations(
        [{"evidence_id": row["evidence_id"], "kind": row["kind"],
          "points": row["geometry"]["points"]} for row in confirmed_traces(evidence)],
        page_width=source.page_width, page_height=source.page_height,
    )


def check_research_links(research: Mapping[str, Any], evidence: list[dict[str, Any]]) -> None:
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
        box = box_of(points)
        scale = parameters["scale"] if operation == "scale" else 1.0
        dx = parameters["dx"] if operation == "translate" else 0.0
        dy = parameters["dy"] if operation == "translate" else 0.0
        moved = [[rounded(box.cx + (x - box.cx) * scale + dx),
                  rounded(box.cy + (y - box.cy) * scale + dy)] for x, y in points]
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
        "relation_signature_similarity": rounded(len(before & after) / len(union)) if union else 1.0,
        "interpretation": "underdetermined",
    }


def research_snapshot_of(source, evidence, research) -> dict[str, Any]:
    """The research as retained: links checked, executed interventions computed, completion stated."""

    result = json.loads(json.dumps(research, allow_nan=False))
    check_research_links(result, evidence)
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


def rounded(value: float) -> float:
    """A number as a Study retains it: six decimals."""

    return round(float(value), 6)


def polygon_observations(
    polygons: list[Mapping[str, Any]], *, page_width: float, page_height: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Measure actual polygons in a resolution-free, aspect-correct page frame.

    These are drawing observations, not building performance or author intent.
    Shapely is already the drawing service's pinned geometry dependency. Invalid
    or self-crossing polygons are refused; buffer(0) would silently change the
    person's corrected evidence and is deliberately not a repair strategy here.
    """
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value <= 0 for value in (page_width, page_height)):
        raise ValueError("Page dimensions must be positive finite numbers.")
    if len({row["evidence_id"] for row in polygons}) != len(polygons):
        raise ValueError("Polygon ids must be unique.")
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    longest = max(page_width, page_height)
    sx, sy = page_width / longest, page_height / longest
    shapes = {}
    measurements = []
    for row in polygons:
        polygon = Polygon([(x * sx, y * sy) for x, y in row["points"]])
        if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
            raise ValueError(f"Trace {row['evidence_id']!r} is not a valid simple polygon.")
        shapes[row["evidence_id"]] = polygon
        measurements.append({
            "evidence_id": row["evidence_id"], "category": "computed",
            "method": "aspect-correct-polygon@1", "area": rounded(polygon.area),
            "perimeter": rounded(polygon.length),
            "centroid_x": rounded(polygon.centroid.x), "centroid_y": rounded(polygon.centroid.y),
        })
    masses = unary_union([shapes[row["evidence_id"]] for row in polygons if row["kind"] == "mass"])
    void_ids = {row["evidence_id"] for row in polygons if row["kind"] == "void"}
    for measurement in measurements:
        if measurement["evidence_id"] not in void_ids:
            continue
        clear = shapes[measurement["evidence_id"]].difference(masses)
        components = ([clear] if clear.geom_type == "Polygon" else list(getattr(clear, "geoms", ())))
        measurement.update({
            "clear_region_method": "declared-void-minus-confirmed-mass@1",
            "clear_area": rounded(clear.area),
            "clear_components": sum(part.geom_type == "Polygon" and part.area > 1e-9 for part in components),
            "passability_established": False,
        })
    relations = []
    ids = sorted(shapes)
    for index, left in enumerate(ids):
        for right in ids[index + 1:]:
            a, b = shapes[left], shapes[right]
            area = a.intersection(b).area
            shared = a.boundary.intersection(b.boundary).length
            distance = a.distance(b)
            facts = [("distance", distance, left, right)]
            if area > 1e-9:
                facts.append(("intersection_area", area, left, right))
            if shared > 1e-9:
                facts.append(("shared_boundary", shared, left, right))
            if a.covers(b) and not a.equals(b):
                facts.append(("contains", None, left, right))
            elif b.covers(a) and not b.equals(a):
                facts.append(("contains", None, right, left))
            if a.touches(b):
                facts.append(("touches", None, left, right))
            for kind, value, subject, target in facts:
                relations.append({
                    "relation_id": f"{kind}:{subject}:{target}", "kind": kind,
                    "subject_evidence_id": subject, "object_evidence_id": target,
                    "value": None if value is None else rounded(value),
                    "category": "computed", "method": "aspect-correct-polygon@1",
                })
    return measurements, sorted(relations, key=lambda row: row["relation_id"])
