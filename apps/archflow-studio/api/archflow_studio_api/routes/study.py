"""Evidence-grounded precedent Study routes."""

from __future__ import annotations

from collections import Counter
from itertools import combinations
import math
from typing import Any, Mapping

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..application import study as study_application
from ..application.binding import bound_project
from ..application.study import StudyView, list_studies, read_study, save_study
from ..transport.errors import StudioError
from ..transport.study import (
    CompareStudiesRequestDto,
    ProposeStudyRequestDto,
    SaveStudyRequestDto,
    StudyComparisonDto,
    StudyViewDto,
    study_comparison_dto,
    study_dto,
)


router = APIRouter(tags=["study"])

_COMPARISON_METHOD = "aspect-correct-composition-compare@1"
_METRIC_FRAME = "page-aspect-correct-long-edge@1"
_DIRECTIONAL_RELATIONS = frozenset({"contains"})
_PROPORTION_FIELDS = (
    "width_ratio",
    "height_ratio",
    "area_ratio",
    "centroid_offset_x",
    "centroid_offset_y",
)


def _metric_scale(source: Mapping[str, Any]) -> tuple[float, float]:
    """Map normalized page x/y into one aspect-correct, resolution-free frame.

    A normalized page coordinate deliberately forgets whether one page axis has
    more physical/pixel extent than the other. That is right for provenance and
    overlay editing, but wrong for geometric comparison: the same 12 mm offset
    must not cross an alignment threshold merely because the drawing sits on a
    wider sheet. The page's long edge is one comparison unit; original evidence
    coordinates remain untouched in the retained Study.
    """

    width = source.get("page_width")
    height = source.get("page_height")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, (int, float))
        or not isinstance(height, (int, float))
        or not math.isfinite(float(width))
        or not math.isfinite(float(height))
        or float(width) <= 0
        or float(height) <= 0
    ):
        raise StudioError(
            409,
            "STUDY_SOURCE_METRICS_INVALID",
            "The retained Study page has no usable width/height for geometric comparison.",
        )
    longest = max(float(width), float(height))
    return float(width) / longest, float(height) / longest


def _metric_evidence(view: StudyView) -> tuple[list[dict[str, Any]], tuple[float, float]]:
    """Project retained evidence for comparison without rewriting its ledger."""

    source = view.payload["source"]
    x_scale, y_scale = _metric_scale(source)
    rows: list[dict[str, Any]] = []
    for retained in view.payload["evidence"]:
        row = dict(retained)
        geometry = dict(row["geometry"])
        geometry["points"] = [
            [study_application._q(float(x) * x_scale), study_application._q(float(y) * y_scale)]
            for x, y in geometry["points"]
        ]
        row["geometry"] = geometry
        rows.append(row)
    return rows, (x_scale, y_scale)


def _comparison_relations(metric_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the existing deterministic relation vocabulary in metric coordinates."""

    return [
        {**row, "method": "aspect-correct-bounds-relation@1"}
        for row in study_application._relation_rows(metric_evidence)
    ]


def _topology_counter(
    metric_evidence: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> Counter[str]:
    """Forget trace names while preserving semantic multiplicity and direction."""

    confirmed = study_application._confirmed(metric_evidence)
    kinds = {row["evidence_id"]: row["kind"] for row in confirmed}
    facts: Counter[str] = Counter(f"node:{row['kind']}" for row in confirmed)
    for relation in relations:
        subject_kind = kinds[relation["subject_evidence_id"]]
        object_kind = kinds[relation["object_evidence_id"]]
        if relation["kind"] not in _DIRECTIONAL_RELATIONS:
            subject_kind, object_kind = sorted((subject_kind, object_kind))
        facts[
            f"edge:{relation['kind']}:{subject_kind}:{object_kind}"
        ] += 1
    return facts


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"fact": fact, "count": count}
        for fact, count in sorted(counter.items())
        if count > 0
    ]


def _counter_difference(left: Counter[str], right: Counter[str]) -> Counter[str]:
    return Counter({
        fact: max(0, count - right.get(fact, 0))
        for fact, count in left.items()
        if count > right.get(fact, 0)
    })


def _multiset_jaccard(left: Counter[str], right: Counter[str]) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 1.0
    intersection = sum(min(left.get(key, 0), right.get(key, 0)) for key in keys)
    union = sum(max(left.get(key, 0), right.get(key, 0)) for key in keys)
    return study_application._q(intersection / union) if union else 1.0


def _proportions(
    metric_evidence: list[dict[str, Any]],
    metric_scale: tuple[float, float],
) -> dict[str, Any]:
    """Describe proportion separately from topology in a stable semantic order."""

    confirmed = study_application._confirmed(metric_evidence)
    boxes = {
        row["evidence_id"]: study_application._box(row["geometry"]["points"])
        for row in confirmed
    }

    def geometry_order(row: Mapping[str, Any]) -> tuple[float, float, float, str]:
        box = boxes[row["evidence_id"]]
        return (-box.area, box.cx, box.cy, row["evidence_id"])

    envelopes = sorted(
        (row for row in confirmed if row["kind"] == "envelope"),
        key=geometry_order,
    )
    if envelopes:
        basis_id = envelopes[0]["evidence_id"]
        basis = boxes[basis_id]
        basis_name = f"largest-envelope:{basis_id}"
    else:
        # Coordinates are already expressed in long-edge units. Dividing by
        # the source page's short edge here would reintroduce the very aspect
        # ratio the comparison projection removed. With no confirmed envelope,
        # report size/position directly against the long-edge unit frame.
        basis_id = None
        basis = study_application._Box(0.0, 0.0, 1.0, 1.0)
        basis_name = "source-page-long-edge"

    by_kind: dict[str, list[Mapping[str, Any]]] = {}
    for row in confirmed:
        by_kind.setdefault(row["kind"], []).append(row)

    slots: list[dict[str, Any]] = []
    for kind in sorted(by_kind):
        # IDs are provenance, not correspondence. Area first gives a stable
        # role ranking; equal-size traces are paired by position before the id
        # is used as a final deterministic tie-break for identical geometry.
        ranked = sorted(by_kind[kind], key=geometry_order)
        for index, row in enumerate(ranked, start=1):
            box = boxes[row["evidence_id"]]
            slots.append({
                "slot": f"{kind}:{index}",
                "kind": kind,
                "rank": index,
                "evidence_id": row["evidence_id"],
                "width_ratio": study_application._q(box.width / basis.width),
                "height_ratio": study_application._q(box.height / basis.height),
                "area_ratio": study_application._q(box.area / basis.area),
                "centroid_offset_x": study_application._q((box.cx - basis.cx) / basis.width),
                "centroid_offset_y": study_application._q((box.cy - basis.cy) / basis.height),
                "metric_bounds": box.to_dict(),
            })
    return {
        "basis": basis_name,
        "basis_evidence_id": basis_id,
        "slots": slots,
    }


def _projection(view: StudyView) -> tuple[dict[str, Any], Counter[str]]:
    metric_evidence, scale = _metric_evidence(view)
    relations = _comparison_relations(metric_evidence)
    topology = _topology_counter(metric_evidence, relations)
    source = view.payload["source"]
    return ({
        "study_id": view.payload["study_id"],
        "ledger_ref": view.ref.uri,
        "derivation_method": view.payload.get("derivation_method"),
        "source_graph_digest": view.composition_graph["graph_digest"],
        "source": source,
        "metric_scale": {"x": study_application._q(scale[0]), "y": study_application._q(scale[1])},
        "comparison_relations": relations,
        "topology": _counter_rows(topology),
        "proportions": _proportions(metric_evidence, scale),
    }, topology)


def _proportion_delta(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[list[dict[str, Any]], float]:
    left_slots = {row["slot"]: row for row in left["proportions"]["slots"]}
    right_slots = {row["slot"]: row for row in right["proportions"]["slots"]}
    rows: list[dict[str, Any]] = []
    maximum = 0.0
    for slot in sorted(set(left_slots) & set(right_slots)):
        a = left_slots[slot]
        b = right_slots[slot]
        delta = {
            field: study_application._q(abs(float(a[field]) - float(b[field])))
            for field in _PROPORTION_FIELDS
        }
        maximum = max(maximum, *delta.values())
        rows.append({
            "slot": slot,
            "left_evidence_id": a["evidence_id"],
            "right_evidence_id": b["evidence_id"],
            "delta": delta,
        })
    return rows, study_application._q(maximum)


def _compare_exact_revisions(binding, requested) -> dict[str, Any]:
    """Compare exact archives without choosing a head or writing a new record."""

    views: list[StudyView] = []
    seen: set[tuple[str, str]] = set()
    for item in requested:
        key = (item.study_id, item.ledger_ref)
        if key in seen:
            raise StudioError(
                422,
                "STUDY_COMPARE_DUPLICATE",
                "A Study comparison may name an exact revision only once.",
            )
        seen.add(key)
        views.append(read_study(binding, item.study_id, item.ledger_ref))

    projections: list[dict[str, Any]] = []
    topologies: list[Counter[str]] = []
    for view in views:
        projection, topology = _projection(view)
        projections.append(projection)
        topologies.append(topology)

    shared = Counter(topologies[0])
    for topology in topologies[1:]:
        for fact in tuple(shared):
            shared[fact] = min(shared[fact], topology.get(fact, 0))
            if shared[fact] <= 0:
                del shared[fact]

    pairwise: list[dict[str, Any]] = []
    for left_index, right_index in combinations(range(len(projections)), 2):
        left = projections[left_index]
        right = projections[right_index]
        left_topology = topologies[left_index]
        right_topology = topologies[right_index]
        proportion_deltas, max_delta = _proportion_delta(left, right)
        pairwise.append({
            "left": {"study_id": left["study_id"], "ledger_ref": left["ledger_ref"]},
            "right": {"study_id": right["study_id"], "ledger_ref": right["ledger_ref"]},
            "topology_similarity": _multiset_jaccard(left_topology, right_topology),
            "left_only_topology": _counter_rows(_counter_difference(left_topology, right_topology)),
            "right_only_topology": _counter_rows(_counter_difference(right_topology, left_topology)),
            "proportion_deltas": proportion_deltas,
            "max_proportion_delta": max_delta,
        })

    return {
        "schema": "StudyComparison@1",
        "project_id": binding.project_id,
        "method": _COMPARISON_METHOD,
        "metric_frame": _METRIC_FRAME,
        "studies": projections,
        "shared_topology": _counter_rows(shared),
        "pairwise": pairwise,
        "canonical_state_changed": False,
    }


@router.post("/studies", response_model=StudyViewDto, response_model_by_alias=True, status_code=201)
def retain_study(request: Request, payload: SaveStudyRequestDto) -> StudyViewDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The Study names another project.")
    source = payload.source
    return study_dto(save_study(
        binding,
        study_id=payload.study_id,
        source_run_id=source.run_id,
        asset_sha256=source.asset_sha256,
        revision_ref=source.revision_ref,
        page_index=source.page_index,
        evidence_rows=(row.model_dump() for row in payload.evidence),
        expected_previous_ref=payload.expected_previous_ref,
        research=None if payload.research is None else payload.research.model_dump(),
        comparison_results=None if payload.research is None else [
            _compare_exact_revisions(binding, comparison.studies)
            for comparison in payload.research.comparisons
        ],
    ))


@router.post(
    "/studies/compare",
    response_model=StudyComparisonDto,
    response_model_by_alias=True,
)
def compare_studies(request: Request, payload: CompareStudiesRequestDto) -> StudyComparisonDto:
    """Compare 2–6 exact retained revisions without updating their ledgers."""

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The Study comparison names another project.")
    return study_comparison_dto(_compare_exact_revisions(binding, payload.studies))


@router.get("/studies", response_model=list[StudyViewDto], response_model_by_alias=True)
def discover_studies(
    request: Request,
    source_run_id: str | None = Query(default=None, alias="sourceRunId"),
    asset_sha256: str | None = Query(default=None, alias="assetSha256", pattern=r"^[0-9a-f]{64}$"),
    page_index: int | None = Query(default=None, alias="pageIndex", ge=0),
) -> list[StudyViewDto]:
    return [study_dto(view) for view in list_studies(
        bound_project(request.app.state), source_run_id=source_run_id,
        asset_sha256=asset_sha256, page_index=page_index,
    )]


@router.get("/studies/{study_id}", response_model=StudyViewDto, response_model_by_alias=True)
def reopen_study(
    request: Request,
    study_id: str,
    ledger_ref: str | None = Query(default=None, alias="ledgerRef"),
) -> StudyViewDto:
    return study_dto(read_study(bound_project(request.app.state), study_id, ledger_ref))


@router.post("/studies/propose", response_model=StudyViewDto, response_model_by_alias=True)
def propose_study(request: Request, payload: ProposeStudyRequestDto) -> StudyViewDto:
    from ..application.study_model import propose_study as propose

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The Study names another project.")
    return study_dto(propose(binding, request.app.state.intent_compiler,
        study_id=payload.study_id, expected_previous_ref=payload.expected_previous_ref, action=payload.action))
