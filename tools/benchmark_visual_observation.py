"""Exercise the source-bound visual observation channel against a running project runtime.

Development and benchmark entry only (GH-303); production chat does not call it.

Review mode (V0). Frames come from the runtime's own projection owners:
``GET /api/drawings/model-view`` for a model, ``POST /api/board/export`` (PNG)
for one registered page. The review goes through the Studio's Codex transport
and prints one JSON result; this mode writes no file and changes no project.
``--monitor-dir`` adds the MonkeyMonitor rows the runtime itself would record.

    python tools/benchmark_visual_observation.py --runtime http://127.0.0.1:8000 \
        --spec review.json --task-class spatial_formal --reason first_bundle

A follow-up review replays the Harness allowance from the previous result:
``--reason after_repair --prior previous.json --addressed f1,f3``.

Drawing mode (``--drawing``, 303-S3) measures the Drawing consumer on cut
plans in the owner's order: exact drawing checks, the project recipe, one
visual review of the drawing revision page, a typed representation repair
from its findings, at most one re-check. Four arms on the same drawings (A
the compiler's output, B with the project recipe, C with one review, D with
the repair and its re-check; ``arm_plan``). Every revision is asked of the
runtime (``POST /api/drawings/plans``, marked ``sourceKind: agent`` so none
counts toward a recipe suggestion) and every look goes through
``POST /api/visual-reviews``, which renders the page itself with the
runtime's configured provider. The revisions are representation only and
never move Design HEAD, but they are retained: run it on a copy of a
project. It writes no file and no decision and prints one JSON result.

    python tools/benchmark_visual_observation.py --drawing --runtime http://127.0.0.1:8000 \
        --spec drawings.json > result.json
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

REPO = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
for path in (REPO, REPO / "apps/archflow-studio/api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from archflow.adapters.occt_backend import OcctDrawingPolyline
from monkeydiagram.drawing_svg import clean_drawing

from archflow_studio_api.application.decisions import RECIPE_KEYS
from archflow_studio_api.application.drawing_plans import PAPER_DEFAULTS
from archflow_studio_api.application.intent_agent import CodexCompiler
from archflow_studio_api.application.monitoring import MonitoredCompiler, StudioMonitor
from archflow_studio_api.application.visual_observation import (
    MAX_FACT_TEXT, MAX_FACTS, MAX_PRIOR, Criterion, EvidenceFrame, PriorFinding, ReviewReason, SourceRef,
    StudioModelVisualProvider, TaskClass, VisualObservation, VisualReviewBudget, VisualReviewRequest,
    model_view_frame, observe_frames, page_frame,
)


def _call(url: str, *, body: Mapping[str, Any] | None = None, timeout_s: float = 300) -> tuple[bytes, str]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, method="GET" if body is None else "POST",
                      headers={} if body is None else {"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout_s) as response:
        return response.read(), response.headers.get("Content-Type", "")


def fetch_model_frames(runtime: str, source: SourceRef, views: Sequence[str]) -> list[EvidenceFrame]:
    """Ask the model-view projection owner for each view of one exact model."""

    frames = []
    for view in views:
        query = urlencode({"runId": source.run_id, "stateDigest": source.state_digest,
                           "assetSha256": source.asset_sha256, "view": view})
        raw, _ = _call(f"{runtime.rstrip('/')}/api/drawings/model-view?{query}")
        frames.append(model_view_frame(json.loads(raw)))
    return frames


def fetch_page_frame(runtime: str, project_id: str, source: SourceRef, *, max_edge: int = 2048) -> EvidenceFrame:
    """Ask the Board export owner for one exact registered page as a clean PNG."""

    raw, media_type = _call(f"{runtime.rstrip('/')}/api/board/export", body={
        "projectId": project_id, "format": "png", "zip": False, "maxEdge": max_edge,
        "pages": [{"runId": source.run_id, "assetSha256": source.asset_sha256,
                   "revisionRef": source.revision_ref, "pageIndex": source.page_index}],
    })
    if not media_type.startswith("image/png"):
        raise ValueError(f"board export answered {media_type}, not one PNG page")
    return page_frame(source, raw)


def review_request(spec: Mapping[str, Any], budget: VisualReviewBudget) -> VisualReviewRequest:
    return VisualReviewRequest(
        domain=spec["domain"], source_refs=tuple(SourceRef.from_dict(row) for row in spec["sources"]),
        view_recipe=tuple(spec["views"]), task=spec["task"],
        criteria=tuple(Criterion(row["id"], row["text"]) for row in spec["criteria"]),
        preserve=tuple(spec.get("preserve", ())), budget=budget.allowed,
        prior_observations=tuple(PriorFinding(row["ref"], row["type"], row["text"]) for row in spec.get("prior", ())),
    )


def build_provider(*, codex: str = "codex", model: str | None = None, timeout_s: float = 240.0,
                   monitor_dir: Path | None = None) -> tuple[StudioModelVisualProvider, StudioMonitor]:
    from monkeymonitor.store import UsageLog

    monitor = StudioMonitor(None if monitor_dir is None else UsageLog(monitor_dir))
    # A bare name is resolved the way a shell would (codex is a .cmd shim on Windows).
    compiler = CodexCompiler(executable=shutil.which(codex) or codex, model=model, timeout_s=timeout_s)
    return StudioModelVisualProvider(MonitoredCompiler(compiler, monitor)), monitor


def replay_budget(task_class: str, reason: str, prior: Mapping[str, Any] | None, addressed: Sequence[str],
                  polish_reviews: int | None) -> VisualReviewBudget:
    """Rebuild one loop's Harness allowance from the previous review's own result."""

    budget = VisualReviewBudget.for_task(task_class, polish_reviews=polish_reviews)
    if prior is not None:
        previous = VisualObservation.from_dict(prior["observation"])
        budget.used = previous.review_index
        budget.settle(previous)
        if ReviewReason(reason) is ReviewReason.AFTER_REPAIR:
            budget.note_repair(addressed)
    return budget


def run_review(runtime: str, spec: Mapping[str, Any], *, task_class: str, reason: str,
               prior: Mapping[str, Any] | None = None, addressed: Sequence[str] = (),
               polish_reviews: int | None = None, provider=None, monitor=None,
               budget: VisualReviewBudget | None = None) -> dict[str, Any]:
    """Fetch the owner frames, then observe once; an in-process loop passes its own budget."""

    budget = budget or replay_budget(task_class, reason, prior, addressed, polish_reviews)
    refused = budget.refusal(reason)
    if refused is not None:
        raise refused  # nothing is rendered for a review the Harness will not admit
    started = time.perf_counter()
    frames: list[EvidenceFrame] = []
    for row in spec["sources"]:
        source = SourceRef.from_dict(row)
        if source.kind == "model":
            frames += fetch_model_frames(runtime, source, spec["views"])
        else:
            frames.append(fetch_page_frame(runtime, spec["projectId"], source, max_edge=int(spec.get("maxEdge", 2048))))
    fetched = time.perf_counter()
    request = review_request(spec, budget)
    result = observe_frames(request, frames, provider=provider, budget=budget, reason=reason, monitor=monitor,
                     project_id=spec.get("projectId"))
    done = time.perf_counter()
    return {
        "request": {"reviewId": request.review_id, "domain": request.domain, "taskClass": task_class,
                    "reason": reason, "budget": request.budget, "views": list(request.view_recipe)},
        "frames": [{"viewRef": f.view_ref, "sha256": f.sha256, "bytes": len(f.png), "width": f.width,
                    "height": f.height, "source": f.source.to_dict()} for f in frames],
        "observation": result.observation.to_dict(),
        "usage": result.usage.to_dict(),
        "timings": {"frameFetchS": round(fetched - started, 3), "reviewS": round(done - fetched, 3)},
        "budgetAfter": {"allowed": budget.allowed, "used": budget.used},
    }


# ---------------------------------------------------------------- drawing mode (303-S3)

ARMS = ("A", "B", "C", "D")
#: The owner's fixed order for the Drawing consumer (#303): exact facts, the
#: recipe, one look, a typed repair, at most one more look.
ORDER = ("drawing_checks", "recipe", "visual_review", "typed_repair", "re_check")
#: The exact checks of one drawn page (``drawing_checks``), in report order.
CHECKS = ("duplicates", "hidden_edges", "micro_segments", "out_of_bounds", "missing_hatch", "source_binding",
          "collisions")
#: A finding a repair answers; ``info`` states a fact or a criterion that holds.
ACTIONABLE = ("minor", "major")
#: Everything a drawing request of one arm sets itself; a spec's plan leaves it out.
ARM_FIELDS = frozenset({"projectId", "drawingId", "previousRevisionRef", "cutLineMm", "visibleLineMm",
                        "hatchSpacingMm", "hatch", "beyond", "dressingOperations", "sourceKind", "reason"})
SOURCE_FIELDS = ("sourceStageRef", "modelSource", "sourceAsset")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")

# One criterion per perceptual question the owner lists for a drawing (#303),
# each stated one way, so a minor or major finding on it says which way to
# go, and each with the one typed paper-space lever the benchmark's repair
# turns for it (None: no lever; the finding stays for the architect).
DRAWING_CRITERIA = (
    ("hierarchy", "The cut walls read clearly heavier than every line below the cut plane.", "cut_line"),
    ("hatch", "The section hatch stays quieter than the cut outline; it does not read as dense or dominant.",
     "hatch_spacing"),
    ("density", "Lines below the cut stay in the background; the sheet does not feel busy.", "beyond_fade"),
    ("entourage", "People and trees are few enough that they do not crowd or hide the plan.", "thin_entourage"),
    ("balance", "No local area carries awkward visual weight, such as a dark clump or a stray mark.", None),
)
DRAWING_TASK = (
    "Look at this cut plan as a printed drawing and report how it reads against each criterion. The known facts "
    "are exact checks already made (lines drawn twice, stroke length, hatch presence, sheet bounds, pens, source); "
    "do not check them again. Findings drive a representation repair of pens, hatch spacing, the fading of lines "
    "below the cut and the number of people and trees; the model itself is not changed."
)
DRAWING_PRESERVE = (
    "The drawn geometry is an exact projection of the model: walls, openings and the cut stay as they are.",
    "The scale, the crop and the dimensions stay as they are.",
)


class DrawingSpecInvalid(ValueError):
    """The drawing spec does not name a benchmark the arms can run."""


@dataclass(frozen=True, slots=True)
class Step:
    """One step of one drawing's arms: the arm it serves, what it does and which of ``ORDER`` it is."""

    arm: str
    action: str
    stage: str | None
    what: str


def arm_plan() -> tuple[Step, ...]:
    """Every step of one drawing's four arms, in the order they run.

    A and B compile the same drawing: A names the code's own pens, B leaves
    them to the project recipe, and the recipe step then says which recipe
    keys A draws otherwise. A page's exact checks always run before anything
    looks at it. C looks once at B's page; D repairs from C's findings and
    looks once more, at the repair. C and D build on B, so the path to D is
    the owner's order: exact checks, recipe, one review, typed repair, one
    re-check.
    """

    return (
        Step("A", "compile", None, "the compiler's output: the code's default pens, no recipe"),
        Step("A", "checks", "drawing_checks", "exact checks of A's page"),
        Step("B", "compile", None, "the same drawing with its pens left to the project recipe"),
        Step("B", "checks", "drawing_checks", "exact checks of B's page"),
        Step("B", "recipe", "recipe", "the recipe keys A's page draws otherwise"),
        Step("C", "review", "visual_review", "one first_bundle look at B's page (domain drawing)"),
        Step("D", "repair", "typed_repair", "typed paper-space levers for C's actionable findings"),
        Step("D", "checks", "drawing_checks", "exact checks of the repaired page"),
        Step("D", "recheck", "re_check", "one after_repair look at the repaired page, the loop's last"),
    )


def drawing_spec(spec: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """The project and the drawings a spec names: ``{projectId, drawings: [{name, plan}]}``.

    ``plan`` is the part of ``POST /api/drawings/plans`` every arm shares:
    the source (``sourceStageRef``, ``modelSource`` or ``sourceAsset``), the
    frame (cut, bottom, scale, crop) and the content (dimensions, dressing).
    What an arm sets itself (``ARM_FIELDS``: the pens, rules, identity,
    continuation and provenance) is refused, so every arm draws the same
    drawing.
    """

    project_id, drawings = spec.get("projectId"), spec.get("drawings")
    if not isinstance(project_id, str) or not project_id:
        raise DrawingSpecInvalid("the spec names its projectId")
    if not isinstance(drawings, list) or not drawings:
        raise DrawingSpecInvalid("the spec names at least one drawing")
    names: set[str] = set()
    for row in drawings:
        name = row.get("name") if isinstance(row, Mapping) else None
        plan = row.get("plan") if isinstance(row, Mapping) else None
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in names:
            raise DrawingSpecInvalid("each drawing has its own short name of letters, digits, '.', '_' or '-'")
        if not isinstance(plan, Mapping):
            raise DrawingSpecInvalid(f"{name}: plan is the drawing request every arm shares")
        taken = sorted(set(plan) & ARM_FIELDS)
        if taken:
            raise DrawingSpecInvalid(f"{name}: the arms set {', '.join(taken)} themselves")
        if sum(key in plan for key in SOURCE_FIELDS) != 1:
            raise DrawingSpecInvalid(f"{name}: name one source: {', '.join(SOURCE_FIELDS)}")
        names.add(name)
    return project_id, [dict(row) for row in drawings]


# ---- the exact checks of one drawn page ---------------------------------------------------

_SVG = "{http://www.w3.org/2000/svg}"
_LINE_GROUPS = ("hidden", "visible", "section")
#: A mark may touch the sheet edge: coordinates are written with 4 decimals.
_EDGE = 1e-4


def _points(element) -> tuple[tuple[float, float], ...]:
    return tuple((float(x), float(y)) for x, y in (pair.split(",") for pair in element.get("points", "").split()))


def read_page(svg: str) -> dict[str, Any]:
    """What one drawing SVG draws, by role, in sheet coordinates.

    ``lines`` are ``(group, data-object, points)`` of the hidden, visible
    and section groups; ``hatch`` and ``poche`` the section material by
    object; ``dimensions`` the dimension marks; ``dressing`` each entourage
    object's strokes by its ``data-dressing`` id.
    """

    root = ElementTree.fromstring(svg.encode("utf-8"))
    _, _, width, height = (float(value) for value in root.get("viewBox", "").split())
    page: dict[str, Any] = {"width": width, "height": height, "hiddenLines": root.get("data-hidden-lines") == "true",
                            "lines": [], "hatch": [], "poche": [], "dimensions": [], "dressing": {}}
    for group in root.findall(f"{_SVG}g"):
        role = group.get("id")
        if role in _LINE_GROUPS:
            page["lines"] += [(role, mark.get("data-object"), _points(mark)) for mark in group.iter(f"{_SVG}polyline")]
        elif role == "section-hatch":
            page["hatch"] += [(mark.get("data-object"), _points(mark)) for mark in group.iter(f"{_SVG}polyline")]
            page["poche"] += [(mark.get("data-object"), _points(mark)) for mark in group.iter(f"{_SVG}polygon")]
        elif role == "dimensions":
            page["dimensions"] += [_points(mark) for mark in group.iter(f"{_SVG}polyline")]
        elif role == "dressing":
            for item in group.findall(f"{_SVG}g"):
                page["dressing"][item.get("data-dressing")] = [_points(mark) for mark in item.iter(f"{_SVG}polyline")]
    return page


def _chord(points, tolerance: float):
    """A line straight to within the tolerance, as (start, unit direction, length); None otherwise.

    A line and its reverse have one chord: the direction has positive x, or
    is (0, -1). It turns over only at vertical, between the 89 and 90
    degree buckets (``_bucket``).
    """

    start, end = points[0], points[-1]
    length = math.dist(start, end)
    if length <= tolerance:
        return None
    ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
    if any(abs((x - start[0]) * uy - (y - start[1]) * ux) > tolerance for x, y in points[1:-1]):
        return None
    if ux < 0 or (ux == 0 and uy > 0):
        return end, (-ux, -uy), length
    return start, (ux, uy), length


def _span_on(chord, other, tolerance: float) -> tuple[float, float] | None:
    """The stretch of ``chord``, as distances along it, that lies within the tolerance of the segment ``other``."""

    (ax, ay), (ux, uy), length = chord
    (bx, by), (vx, vy), other_length = other
    ends = ((bx, by), (bx + vx * other_length, by + vy * other_length))
    along = [(x - ax) * ux + (y - ay) * uy for x, y in ends]
    across = [(y - ay) * ux - (x - ax) * uy for x, y in ends]
    if across[0] == across[1]:
        if abs(across[0]) > tolerance:
            return None
        low, high = 0.0, 1.0
    else:
        first, second = ((side - across[0]) / (across[1] - across[0]) for side in (-tolerance, tolerance))
        low, high = max(0.0, min(first, second)), min(1.0, max(first, second))
        if low > high:
            return None
    start, stop = sorted(along[0] + share * (along[1] - along[0]) for share in (low, high))
    start, stop = max(start, 0.0), min(stop, length)
    return (start, stop) if start < stop else None


def _covered(spans, length: float, tolerance: float) -> bool:
    """Whether the spans cover a line end to end; the tolerance at either end may stay open."""

    if not spans:
        return False
    spans = sorted(spans)
    if spans[0][0] > tolerance:
        return False
    reach = spans[0][1]
    for low, high in spans[1:]:
        if low > reach + 1e-9:
            return False
        reach = max(reach, high)
    return reach >= length - tolerance


def _bucket(chord, tolerance: float) -> tuple[int, float]:
    """A chord's direction, in whole degrees from 0 to 179, and its offset from the origin in twice the tolerance."""

    (x, y), (ux, uy), _ = chord
    return int(math.degrees(math.atan2(uy, ux)) % 180.0) % 180, (y * ux - x * uy) / (2 * tolerance)


def _on_other_objects(lines, tolerance: float) -> list[int]:
    """The straight lines lying, end to end, within the tolerance of straight lines of other objects in their group.

    Two solids meeting at a face both draw it, and the cleanup keeps each
    object's own line, so the pen inks that edge twice. Lines are compared
    within one direction (1 degree) and offset (twice the tolerance) bucket
    and its neighbours; a line of no object is not compared.
    """

    chords = []
    for index, (role, name, points) in enumerate(lines):
        chord = None if name is None else _chord(points, tolerance)
        if chord is not None:
            chords.append((index, role, name, chord))
    buckets: dict[tuple[str, int, int], list] = {}
    for index, role, name, chord in chords:
        angle, offset = _bucket(chord, tolerance)
        buckets.setdefault((role, angle, math.floor(offset)), []).append((name, chord))
    found = []
    for index, role, name, chord in chords:
        angle, offset = _bucket(chord, tolerance)
        spans = []
        for turn in (-1, 0, 1):
            neighbour = (angle + turn) % 180
            # The chord direction turns over at vertical, and its offset with it.
            level = math.floor(-offset if {angle, neighbour} == {89, 90} else offset)
            for shift in (-1, 0, 1):
                for other_name, other in buckets.get((role, neighbour, level + shift), ()):
                    span = None if other_name == name else _span_on(chord, other, tolerance)
                    if span is not None:
                        spans.append(span)
        if _covered(spans, chord[2], tolerance):
            found.append(index)
    return found


def _segments(points):
    return [(a, b) for a, b in zip(points, points[1:]) if a != b]


def _crosses(p, q, r, s) -> bool:
    """Whether the segments p-q and r-s share a point."""

    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def within(a, b, c):
        return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])

    d1, d2, d3, d4 = side(r, s, p), side(r, s, q), side(p, q, r), side(p, q, s)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True
    return ((d1 == 0 and within(r, s, p)) or (d2 == 0 and within(r, s, q))
            or (d3 == 0 and within(p, q, r)) or (d4 == 0 and within(p, q, s)))


def _box(segments):
    xs = [x for segment in segments for x, _ in segment]
    ys = [y for segment in segments for _, y in segment]
    return min(xs), min(ys), max(xs), max(ys)


def _meets(segments, others) -> bool:
    if not segments or not others:
        return False
    x0, y0, x1, y1 = _box(segments)
    near = [(r, s) for r, s in others
            if not (max(r[0], s[0]) < x0 or min(r[0], s[0]) > x1 or max(r[1], s[1]) < y0 or min(r[1], s[1]) > y1)]
    return any(_crosses(p, q, r, s) for p, q in segments for r, s in near)


def drawing_checks(svg: str, *, status: Mapping[str, Any], anchors: Sequence[Mapping[str, Any]],
                   cleanup: Mapping[str, Any] | None) -> dict[str, Any]:
    """The exact facts of one drawn page: a count per check and what each found (``ORDER[0]``).

    - ``duplicates``: lines drawn over other lines. A projected line on the
      cut that the cleanup's own rules still find in the page, a line drawn
      twice, and a straight line of one object lying on a line of another
      in its group (``_on_other_objects``).
    - ``hidden_edges``: hidden lines the cleanup's rules would still drop
      (under visible lines, inside the cut), and any hidden line on a page
      drawn without them.
    - ``micro_segments``: strokes the cleanup's rules would still drop as
      shorter than its tolerance.
    - ``out_of_bounds``: marks reaching past the sheet, and entourage the
      status places outside the view.
    - ``missing_hatch``: cut objects with neither hatch nor poché.
    - ``source_binding``: what the page's status cannot vouch for: a page not
      current or rebound, hidden objects, dimensions or entourage anchors it
      no longer resolves, a projected mark naming no object or one the
      source does not hold.
    - ``collisions``: entourage whose strokes meet a cut line, a dimension or
      other entourage.

    The cleanup's rules run again (``clean_drawing``) on what the page
    draws, at the tolerance its own cleanup report names; a page drawn
    before cleanup has no report and those three checks are not measured
    (None).
    """

    page = read_page(svg)
    lines = [(role, name, points) for role, name, points in page["lines"] if len(points) >= 2]
    counts: dict[str, int | None] = dict.fromkeys(CHECKS, 0)
    found: dict[str, Any] = {}
    tolerance = (cleanup or {}).get("tolerance")
    if isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool) and tolerance > 0:
        drawn = {OcctDrawingPolyline(name or "", role, points) for role, name, points in lines}
        _, report = clean_drawing(tuple(drawn), (), tolerance=float(tolerance))
        again = report.to_dict()
        on_others = _on_other_objects(lines, float(tolerance))
        counts["duplicates"] = again["cut_precedence"] + len(lines) - len(drawn) + len(on_others)
        counts["hidden_edges"] = again["duplicate"] + again["hidden_under_cut"]
        counts["micro_segments"] = again["micro"]
        found["cleanupAgain"] = again
        found["onOtherObjects"] = [{"group": lines[index][0], "object": lines[index][1]} for index in on_others]
    else:
        counts.update(duplicates=None, hidden_edges=None, micro_segments=None)
    stray = sum(role == "hidden" for role, _, _ in lines) if not page["hiddenLines"] else 0
    if stray:
        counts["hidden_edges"] = (counts["hidden_edges"] or 0) + stray

    width, height = page["width"], page["height"]

    def off_sheet(points) -> bool:
        return any(x < -_EDGE or y < -_EDGE or x > width + _EDGE or y > height + _EDGE for x, y in points)

    marks = ([points for _, _, points in lines] + [points for _, points in page["hatch"] + page["poche"]]
             + page["dimensions"])
    outside = sorted(row.get("id") for row in status.get("dressing") or () if row.get("status") == "outside-view")
    past_edge = sorted(item for item, polylines in page["dressing"].items() if any(map(off_sheet, polylines)))
    counts["out_of_bounds"] = sum(map(off_sheet, marks)) + len(past_edge) + len(outside)
    found["entourageOffSheet"] = past_edge + outside

    cut = {name for role, name, _ in lines if role == "section" and name}
    filled = {name for name, _ in page["hatch"] + page["poche"] if name}
    found["cutObjects"] = len(cut)
    found["missingHatch"] = sorted(cut - filled)
    counts["missing_hatch"] = len(found["missingHatch"])

    failures = []
    if status.get("status") != "current":
        failures.append(f"status {status.get('status')}")
    if status.get("bindingChanged"):
        failures.append("the source binding changed")
    failures += [f"hidden object {name} is gone" for name in status.get("unresolvedObjectIds") or ()]
    failures += [f"dimension {row.get('id')} is {row.get('status')}" for row in status.get("dimensions") or ()
                 if row.get("status") != "resolved"]
    failures += [f"entourage {row.get('id')} lost its anchor" for row in status.get("dressing") or ()
                 if row.get("status") == "missing"]
    named = [name for _, name, _ in lines] + [name for name, _ in page["hatch"] + page["poche"]]
    if None in named:
        failures.append(f"{named.count(None)} projected marks name no source object")
    known = {row.get("objectId") for row in anchors}
    if known:  # an imported model offers no anchors to check its objects against
        failures += [f"object {name} is not in the exact source" for name in sorted(set(named) - known - {None})]
    found["bindingFailures"] = failures
    counts["source_binding"] = len(failures)

    cut_strokes = [segment for role, _, points in lines if role == "section" for segment in _segments(points)]
    dimension_strokes = [segment for points in page["dimensions"] for segment in _segments(points)]
    entourage = {item: [segment for points in polylines for segment in _segments(points)]
                 for item, polylines in page["dressing"].items()}
    found["colliding"] = sorted(
        item for item, strokes in entourage.items()
        if _meets(strokes, cut_strokes + dimension_strokes
                  + [segment for other, others in entourage.items() if other != item for segment in others]))
    counts["collisions"] = len(found["colliding"])
    found["entourage"] = sorted(entourage)
    return {"counts": counts, "found": found}


def recipe_deviations(compiled: Mapping[str, Any], with_recipe: Mapping[str, Any]) -> list[str]:
    """The project-recipe keys a page draws otherwise than a new drawing of the project does (``ORDER[1]``).

    ``with_recipe`` is arm B's graphics: a new drawing whose request names
    no pen, so each key holds the project recipe's value, or the code
    default where the recipe is silent (03-C4).
    """

    return [key for key in RECIPE_KEYS if compiled.get(key) != with_recipe.get(key)]


# ---- the look and the repair ---------------------------------------------------------------

_UNITS = {"meter": "m", "millimeter": "mm", "inch": "in", "foot": "ft"}


def known_facts(document: Mapping[str, Any], checks: Mapping[str, Any], status: Mapping[str, Any],
                cleanup: Mapping[str, Any] | None) -> list[str]:
    """The exact facts the look is told, so it spends nothing on them (#303: no multimodal call for a known fact)."""

    recipe = document["viewRecipe"]
    frame, graphics = recipe["frame"], recipe["graphics"]
    counts, found = checks["counts"], checks["found"]
    unit = _UNITS.get(status.get("lengthUnit"), status.get("lengthUnit") or "units")
    cut = frame["origin"][2]
    fade = (graphics.get("beyond") or {}).get("fade")
    measured = [f"{counts[key]} {what}" for key, what in (
        ("duplicates", "lines drawn over other lines"), ("micro_segments", "specks"),
        ("out_of_bounds", "marks off the sheet")) if counts[key] is not None]
    facts = [
        f"Cut plan at {frame['scale']}: cut plane at {cut:g} {unit}; lines below drawn down to "
        f"{cut - frame['far_depth']:g} {unit}.",
        f"Pens on paper: cut {graphics['cutLineMm']:g} mm, below the cut {graphics['visibleLineMm']:g} mm"
        + (f", greyed {fade:g}" if fade else "") + ".",
        f"Section hatch every {graphics['hatchSpacingMm']:g} mm; {found['cutObjects'] - counts['missing_hatch']} of "
        f"{found['cutObjects']} cut objects hatched or poched.",
        "Exact line checks: " + (", ".join(measured) or "not measured") + ".",
    ]
    if cleanup:
        rules = [f"{cleanup[rule]} {what}" for rule, what in (
            ("cut_precedence", "on the cut"), ("micro", "specks"), ("collinear", "joined"),
            ("duplicate", "hidden under visible"), ("hidden_under_cut", "hidden in the cut")) if cleanup.get(rule)]
        facts.append(f"Before drawing, cleanup took {cleanup['input_lines'] - cleanup['output_lines']} of "
                     f"{cleanup['input_lines']} projected lines" + (f": {', '.join(rules)}." if rules else "."))
    if found["entourage"]:
        people = sum(item["assetId"] == "person-plan" for item in recipe.get("dressing") or ())
        trees = sum(item["assetId"] == "tree-plan" for item in recipe.get("dressing") or ())
        facts.append(f"Entourage: {people} {'person' if people == 1 else 'people'} and {trees} "
                     f"tree{'' if trees == 1 else 's'}; {counts['collisions']} meet other marks.")
    facts.append("Source: this page is the exact current revision of its model." if not counts["source_binding"]
                 else f"Source: {counts['source_binding']} binding checks fail.")
    return [fact if len(fact) <= MAX_FACT_TEXT else fact[:MAX_FACT_TEXT - 1] + "…" for fact in facts][:MAX_FACTS]


def page_source(document: Mapping[str, Any]) -> dict[str, Any]:
    """A drawing revision's one page, exactly as a visual review names it."""

    return {"kind": "page", "runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "pageIndex": 0}


def review_body(project_id: str, document: Mapping[str, Any], facts: Sequence[str], *, reason: str,
                budget_state: Mapping[str, Any], prior: Sequence[Mapping[str, Any]] = (),
                addressed: Sequence[str] = ()) -> dict[str, Any]:
    """One ``POST /api/visual-reviews`` body for a drawing revision page."""

    body: dict[str, Any] = {
        "projectId": project_id, "domain": "drawing", "sourceRefs": [page_source(document)],
        "viewRecipe": ["page-0"], "task": DRAWING_TASK,
        "criteria": [{"criterionId": name, "text": text} for name, text, _ in DRAWING_CRITERIA],
        "preserve": list(DRAWING_PRESERVE), "knownFacts": list(facts), "reason": reason,
        "budgetState": dict(budget_state),
    }
    if prior:
        body["priorObservations"] = [
            {"findingRef": row["ref"][:40], "type": row["type"],
             "description": row["description"] if len(row["description"]) <= 300 else row["description"][:299] + "…"}
            for row in prior[:MAX_PRIOR]]
    if addressed:
        body["addressedFindingIds"] = list(addressed)
    return body


def _cut_line(recipe):
    current = recipe["graphics"]["cutLineMm"]
    value = min(2.0, round(current * 1.4, 2))
    return {"cutLineMm": value} if value != current else None


def _hatch_spacing(recipe):
    current = recipe["graphics"]["hatchSpacingMm"]
    value = min(20.0, round(current * 1.5, 1))
    return {"hatchSpacingMm": value} if value != current else None


def _beyond_fade(recipe):
    current = (recipe["graphics"].get("beyond") or {}).get("fade", 0.0)
    value = min(0.8, round(current + 0.4, 2))
    return {"beyond": {"fade": value}} if value != current else None


def _thin_entourage(recipe):
    names = sorted(item["id"] for item in recipe.get("dressing") or ())
    return {"dressingOperations": [{"op": "delete", "id": name} for name in names[1::2]]} if len(names) > 1 else None


#: Each lever's one step, on the reviewed revision's recipe; None when it is at its bound.
LEVERS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any] | None]] = {
    "cut_line": _cut_line,            # the cut pen x1.4, at most 2 mm
    "hatch_spacing": _hatch_spacing,  # the hatch spacing x1.5, at most 20 mm
    "beyond_fade": _beyond_fade,      # lines below the cut 0.4 greyer, at most 0.8
    "thin_entourage": _thin_entourage,  # every second entourage object by id deleted
}


def actionable(findings: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in findings if row.get("severity") in ACTIONABLE]


def repair_plan(view_recipe: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The typed representation repair the review's findings drive, fixed so arm D is reproducible.

    A finding is actionable at minor or major severity. One that names a
    preserve condition is escalated, never repaired: it is a question for
    the architect, as the Hub marks it. Every other actionable finding turns
    the lever of each criterion it names (``DRAWING_CRITERIA``), once per
    lever however many findings name it; it is addressed when one of its
    levers changed something. A criterion without a lever, or a lever at its
    bound, addresses nothing. ``fields`` is exactly what a rebuild of the
    reviewed revision sends besides its source and continuation.
    """

    levers = {name: lever for name, _, lever in DRAWING_CRITERIA}
    rows = actionable(findings)
    escalated = [row["findingId"] for row in rows if any(ref.startswith("preserve:") for ref in row["targetRefs"])]
    asked: dict[str, list[str]] = {}
    for row in rows:
        if row["findingId"] in escalated:
            continue
        for ref in row["targetRefs"]:
            lever = levers.get(ref.removeprefix("criterion:")) if ref.startswith("criterion:") else None
            if lever is not None:
                asked.setdefault(lever, []).append(row["findingId"])
    fields: dict[str, Any] = {}
    turned: dict[str, list[str]] = {}
    for lever, change in LEVERS.items():
        if lever in asked and (step := change(view_recipe)) is not None:
            fields.update(step)
            turned[lever] = asked[lever]
    addressed = [row["findingId"] for row in rows if any(row["findingId"] in ids for ids in turned.values())]
    return {"fields": fields, "levers": turned, "addressed": addressed, "escalated": escalated,
            "unaddressed": [row["findingId"] for row in rows
                            if row["findingId"] not in addressed and row["findingId"] not in escalated]}


# ---- the metrics -------------------------------------------------------------------------

_USAGE = ("providerCalls", "imageInputs", "imageBytes", "inputTokens", "cachedInputTokens", "outputTokens",
          "reasoningOutputTokens", "durationMs")


def usage_total(usages: Sequence[Mapping[str, Any] | None]) -> dict[str, int | None]:
    """The provider and image cost of some looks, summed as reported; a value no look reported stays None."""

    total: dict[str, int | None] = dict.fromkeys(_USAGE)
    for usage in usages:
        for key in _USAGE:
            value = (usage or {}).get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                total[key] = (total[key] or 0) + value
    return total


def remaining_corrections(counts: Mapping[str, int | None], deviations: Sequence[str],
                          unresolved: Sequence[str] | None) -> int:
    """The proxy for the corrections a person would still make on a page, one per instruction they would give.

    One for each exact check that finds anything ("remove the double
    lines", "hatch the cut", however many lines or objects it names), one
    for each project-recipe key the page draws otherwise ("the hatch is too
    dense again"), and one for each actionable visual finding still open.
    A check not measured counts nothing; an arm that did not look counts
    no finding, so its number is a lower bound.
    """

    return sum(1 for key in CHECKS if counts.get(key)) + len(deviations) + len(unresolved or ())


def _open(review: Mapping[str, Any] | None) -> list[Mapping[str, Any]] | None:
    """The actionable findings of a look that answered; None when there was no answer."""

    return None if not review or not review.get("ok") else actionable(review["findings"])


def _criteria(findings: Sequence[Mapping[str, Any]]) -> list[str]:
    """The criteria some findings name, in ``DRAWING_CRITERIA`` order."""

    named = {ref.removeprefix("criterion:") for row in findings for ref in row["targetRefs"]
             if ref.startswith("criterion:")}
    return [name for name, _, _ in DRAWING_CRITERIA if name in named]


def arm_results(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Each arm's page, exact checks, recipe deviations, open findings, corrections, cost, wall clock and acceptance.

    ``state`` is what one drawing's steps recorded (``run_drawing``). C and D
    build on B: C's page is B's, and D's is the repaired page when the
    repair was drawn, else B's. A visual acceptance is only what a look can
    decide: no actionable finding known to be open.

    D is measured with and without its second look. Without it, D would
    hand the repair over believing every finding it addressed resolved;
    with it, the re-check's actionable findings are what stays open.
    ``secondLookChanged`` says whether the acceptance differs;
    ``reopenedCriteria`` names the criteria the repair addressed that the
    re-check still finds, ``newCriteria`` those it finds and the first look
    did not, and ``remainingCorrectionsWithoutSecondLook`` is the count D
    would have reported.
    """

    documents, checks, reviews = state["documents"], state["checks"], state["reviews"]
    repair, timings = state.get("repair") or {}, state["timings"]
    own = {arm: sum(seconds for step, seconds in timings.items() if step.startswith(arm + ".")) for arm in ARMS}
    wall = {"A": own["A"], "B": own["B"], "C": own["B"] + own["C"], "D": own["B"] + own["C"] + own["D"]}
    first, second = reviews.get("C"), reviews.get("D")
    first_open, second_open = _open(first), _open(second)
    repaired = "D" in documents
    addressed = set(repair.get("addressed") or ()) if repaired else set()
    without = None if first_open is None else [row for row in first_open if row["findingId"] not in addressed]
    still_open = {"A": None, "B": None, "C": first_open, "D": without if second_open is None else second_open}
    pages = {"A": "A", "B": "B", "C": "B", "D": "D" if repaired else "B"}
    looks = {"A": (), "B": (), "C": (first,), "D": (first, second)}
    results = {}
    for arm in ARMS:
        page, rows = pages[arm], still_open[arm]
        counts = (checks.get(page) or {}).get("counts") or {}
        # Only A is drawn without the recipe; a repair's explicit pen answers a finding.
        deviations = (state.get("deviations") or []) if arm == "A" else []
        open_ids = None if rows is None else [row["findingId"] for row in rows]
        document = documents.get(page)
        results[arm] = {
            "page": None if document is None else {key: document.get(key) for key in (
                "drawingId", "runId", "assetSha256", "revisionRef")},
            "graphics": None if document is None else document["viewRecipe"]["graphics"],
            "checks": counts,
            "failingChecks": [key for key in CHECKS if counts.get(key)],
            "recipeDeviations": deviations,
            "visualObserved": rows is not None,
            "openFindings": open_ids,
            "openBySeverity": None if rows is None else {level: sum(row["severity"] == level for row in rows)
                                                         for level in ACTIONABLE},
            "remainingCorrections": remaining_corrections(counts, deviations, open_ids),
            "exactChecksPass": bool(counts) and all(counts.get(key) == 0 for key in CHECKS),
            "visuallyAccepted": None if rows is None else not rows,
            "cost": usage_total([row.get("usage") for row in looks[arm] if row]),
            "wallS": {"own": round(own[arm], 3), "cumulative": round(wall[arm], 3)},
        }
    # The look at B's own page, which B does not take: what B's claim leaves unseen.
    results["B"]["laterObservedFindings"] = None if first_open is None else len(first_open)
    d = results["D"]
    d["repaired"] = repaired
    d["openWithoutSecondLook"] = None if without is None else [row["findingId"] for row in without]
    d["remainingCorrectionsWithoutSecondLook"] = (
        None if without is None else remaining_corrections(d["checks"], (), d["openWithoutSecondLook"]))
    d["visuallyAcceptedWithoutSecondLook"] = None if without is None else not without
    if second_open is None or without is None:
        d.update(secondLookChanged=None, reopenedCriteria=None, newCriteria=None)
    else:
        seen = _criteria(second_open)
        answered = set(_criteria([row for row in first_open if row["findingId"] in addressed]))
        before = set(_criteria(first_open))
        d.update(secondLookChanged=(not without) != (not second_open),
                 reopenedCriteria=[name for name in seen if name in answered],
                 newCriteria=[name for name in seen if name not in before])
    return results


# ---- running the arms against a runtime --------------------------------------------------------


class RuntimeClient:
    """One project runtime over HTTP, one JSON request at a time; a refusal is answered as data.

    A request the runtime never answered (refused connection, timeout) is
    status 0 with ``RUNTIME_UNANSWERED``, so the run goes on: a drawing
    request or read stops that drawing, and an unanswered look ends its
    loop, having perhaps cost what nobody reported.
    """

    def __init__(self, base: str, *, timeout_s: float = 600.0) -> None:
        self.base = base.rstrip("/")
        self.timeout_s = timeout_s

    def request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(self.base + path, data=data, method=method,
                          headers={} if body is None else {"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                return response.status, json.loads(response.read() or b"null")
        except HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw)
            except ValueError:
                return error.code, {"code": f"HTTP_{error.code}", "detail": raw.decode("utf-8", "replace")[:500]}
        except OSError as error:  # URLError and timeouts: no answer at all
            return 0, {"code": "RUNTIME_UNANSWERED", "detail": f"{method} {path.split('?')[0]}: {error}"[:300]}


class _Stop(RuntimeError):
    """A step the rest of this drawing cannot go on without failed; it is recorded, the next drawing runs."""


def run_drawing(runtime, project_id: str, drawing: Mapping[str, Any], *, tag: str,
                clock: Callable[[], float] = time.perf_counter,
                log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """One drawing's four arms, step by step as ``arm_plan`` orders them; what each step did, and the arms' results."""

    name, plan = drawing["name"], drawing["plan"]
    state: dict[str, Any] = {"documents": {}, "checks": {}, "statuses": {}, "reviews": {}, "repair": None,
                             "deviations": None, "timings": {}}
    steps: list[dict[str, Any]] = []
    source = {key: plan[key] for key in SOURCE_FIELDS if key in plan}
    ask = runtime.request

    def compile_(arm: str) -> str:
        pens = dict(PAPER_DEFAULTS) if arm == "A" else {}
        why = "compiler output" if arm == "A" else "project recipe"
        code, document = ask("POST", "/api/drawings/plans", {
            **plan, **pens, "projectId": project_id, "drawingId": f"bench-{tag}-{name}-{arm.lower()}",
            "sourceKind": "agent", "reason": f"GH-303 drawing benchmark, arm {arm}: {why}"})
        if code != 201:
            raise _Stop(f"POST /api/drawings/plans answered {code}: {_detail(document)}")
        state["documents"][arm] = document
        return f"revision {document['revisionRef'].rsplit('/', 1)[-1][:40]}"

    def checks(arm: str) -> str:
        document = state["documents"].get(arm)
        if document is None:
            return _skip("no repaired page to check")
        revision = {"runId": document["runId"], "assetSha256": document["assetSha256"],
                    "revisionRef": document["revisionRef"]}
        code, vector = ask("GET", "/api/drawings/plans/vector?" + urlencode(revision))
        if code != 200:
            raise _Stop(f"GET /api/drawings/plans/vector answered {code}: {_detail(vector)}")
        code, status = ask("POST", "/api/drawings/plans/status", revision)
        if code != 200:
            raise _Stop(f"POST /api/drawings/plans/status answered {code}: {_detail(status)}")
        cleanup = vector.get("cleanup") or status.get("cleanup")
        result = drawing_checks(vector["svg"], status=status, anchors=vector.get("anchors") or (), cleanup=cleanup)
        state["checks"][arm] = {**result, "cleanup": cleanup}
        state["statuses"][arm] = status
        failing = [key for key in CHECKS if result["counts"].get(key)]
        return "failing: " + ", ".join(failing) if failing else "every exact check passes"

    def recipe(_: str) -> str:
        state["deviations"] = recipe_deviations(state["documents"]["A"]["viewRecipe"]["graphics"],
                                                state["documents"]["B"]["viewRecipe"]["graphics"])
        return "A draws " + ", ".join(state["deviations"]) + " otherwise" if state["deviations"] else "A draws B's pens"

    def look(arm: str, document, reason: str, budget_state, prior=(), addressed=()) -> str:
        page = "B" if arm == "C" else "D"
        if state["checks"][page]["counts"]["source_binding"]:
            return _skip(f"{page}'s page is not exactly bound: {'; '.join(state['checks'][page]['found']['bindingFailures'])}")
        facts = known_facts(document, state["checks"][page], state["statuses"][page], state["checks"][page]["cleanup"])
        body = review_body(project_id, document, facts, reason=reason, budget_state=budget_state, prior=prior,
                           addressed=addressed)
        code, answer = ask("POST", "/api/visual-reviews", body)
        if code == 200:
            observation = answer["observation"]
            if observation["sourceRefs"] != [page_source(document)]:
                raise _Stop("the review answered for another source than the page it was asked about")
            state["reviews"][arm] = {
                "ok": True, "reason": reason, "reviewId": observation["reviewId"],
                "reviewIndex": observation["reviewIndex"], "frameSha256": observation["frameSha256"],
                "knownFacts": facts, "findings": observation["observations"],
                "unresolvedQuestions": observation["unresolvedQuestions"],
                "suggestedChecks": observation["suggestedChecks"], "usage": answer["usage"],
                "budgetState": answer["budgetState"]}
            open_ids = [row["findingId"] for row in actionable(observation["observations"])]
            return f"{len(observation['observations'])} findings, actionable: {', '.join(open_ids) or 'none'}"
        state["reviews"][arm] = {"ok": False, "reason": reason, "status": code, "code": _code(answer),
                                 "detail": _detail(answer), "usage": (answer or {}).get("usage"),
                                 "budgetState": (answer or {}).get("budgetState"), "knownFacts": facts}
        return f"refused {code} {_code(answer)}"

    def review(arm: str) -> str:
        return look(arm, state["documents"]["B"], ReviewReason.FIRST_BUNDLE.value,
                    VisualReviewBudget.for_task(TaskClass.SPATIAL_FORMAL).to_dict())

    def repair(_: str) -> str:
        first = state["reviews"].get("C")
        if not first or not first["ok"]:
            return _skip("the first look gave no findings to repair from")
        plan_ = repair_plan(state["documents"]["B"]["viewRecipe"], first["findings"])
        state["repair"] = plan_
        if not plan_["fields"]:
            return _skip("no actionable finding has a typed lever" if actionable(first["findings"])
                         else "no actionable finding")
        levers = ", ".join(plan_["levers"])
        before = state["documents"]["B"]
        code, document = ask("POST", "/api/drawings/plans", {
            **source, **plan_["fields"], "projectId": project_id, "drawingId": before["drawingId"],
            "previousRevisionRef": before["revisionRef"], "sourceKind": "agent",
            "reason": f"GH-303 drawing benchmark, arm D: {levers} for {', '.join(plan_['addressed'])}"[:200]})
        if code != 201:
            state["repair"] = {**plan_, "refused": {"status": code, "code": _code(document), "detail": _detail(document)}}
            return _skip(f"the repair was refused {code} {_code(document)}")
        state["documents"]["D"] = document
        return f"{levers} for {', '.join(plan_['addressed'])}"

    def recheck(arm: str) -> str:
        if "D" not in state["documents"]:
            return _skip("nothing was repaired, so a second look is not warranted")
        first = state["reviews"]["C"]
        prior = [{"ref": f"{first['reviewId']}:{row['findingId']}", "type": row["type"],
                  "description": row["description"]} for row in actionable(first["findings"])]
        return look(arm, state["documents"]["D"], ReviewReason.AFTER_REPAIR.value, first["budgetState"],
                    prior=prior, addressed=state["repair"]["addressed"])

    actions = {"compile": compile_, "checks": checks, "recipe": recipe, "review": review, "repair": repair,
               "recheck": recheck}
    stopped = None
    for step in arm_plan():
        key = f"{step.arm}.{step.action}"
        if stopped is not None:
            steps.append({"step": key, "stage": step.stage, "skipped": f"stopped: {stopped}"})
            continue
        started = clock()
        try:
            outcome = actions[step.action](step.arm)
        except _Stop as exc:
            stopped = str(exc)
            outcome = f"failed: {exc}"
        seconds = clock() - started
        state["timings"][key] = seconds
        row = {"step": key, "stage": step.stage, "seconds": round(seconds, 3)}
        row["skipped" if outcome.startswith(_SKIPPED) else "outcome"] = outcome.removeprefix(_SKIPPED)
        steps.append(row)
        if log is not None:
            log(f"[{name}] {key}: {outcome.removeprefix(_SKIPPED)} ({seconds:.1f} s)")
    # The whole recorded state stays with the result, so ``arm_results`` can score it again.
    return {"name": name, "steps": steps, "stopped": stopped,
            "arms": arm_results(state) if "B" in state["checks"] else None, "state": state}


_SKIPPED = "skipped: "


def _skip(reason: str) -> str:
    return _SKIPPED + reason


def _code(answer: Any) -> str | None:
    return answer.get("code") if isinstance(answer, Mapping) else None


def _detail(answer: Any) -> str:
    detail = answer.get("detail") if isinstance(answer, Mapping) else answer
    return str(detail)[:300]


def active_recipe(runtime) -> list[dict[str, Any]] | None:
    """The project's active recipe decisions, as ``GET /api/decisions`` lists them; None when it cannot say."""

    code, answer = runtime.request("GET", "/api/decisions")
    if code != 200 or not isinstance(answer, Mapping):
        return None
    return [{"decisionId": row["decisionId"], "targetRef": row["targetRef"], "strength": row["strength"],
             "scope": row["scope"], "graphics": row["typedBinding"]["graphics"]}
            for row in answer.get("decisions") or ()
            if row.get("status") == "active" and (row.get("typedBinding") or {}).get("kind") == "recipe"]


def summary(drawings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per drawing and arm: the numbers a comparison reads."""

    rows = []
    for drawing in drawings:
        for arm, row in (drawing["arms"] or {}).items():
            rows.append({
                "drawing": drawing["name"], "arm": arm, "remainingCorrections": row["remainingCorrections"],
                "visualObserved": row["visualObserved"], "failingChecks": row["failingChecks"],
                "recipeDeviations": len(row["recipeDeviations"]),
                "openFindings": None if row["openFindings"] is None else len(row["openFindings"]),
                "visuallyAccepted": row["visuallyAccepted"],
                "secondLookChanged": row.get("secondLookChanged"),
                "reopenedCriteria": row.get("reopenedCriteria"),
                "remainingCorrectionsWithoutSecondLook": row.get("remainingCorrectionsWithoutSecondLook"),
                **{key: row["cost"][key] for key in _USAGE}, "wallS": row["wallS"]["cumulative"]})
    return rows


def totals(drawings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every look of a run, answered or not, and what they cost together."""

    looks = [review for drawing in drawings for review in drawing["state"]["reviews"].values()]
    return {"looks": len(looks), "answered": sum(1 for review in looks if review.get("ok")),
            **usage_total([review.get("usage") for review in looks])}


def run_drawing_benchmark(runtime, spec: Mapping[str, Any], *, tag: str,
                          clock: Callable[[], float] = time.perf_counter,
                          log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Every drawing of the spec through the four arms; one JSON-ready result with a summary per drawing and arm."""

    project_id, drawings = drawing_spec(spec)
    if not _NAME.fullmatch(tag):
        raise DrawingSpecInvalid("the tag is a short name of letters, digits, '.', '_' or '-'")
    started = clock()
    recipe = active_recipe(runtime)
    results = [run_drawing(runtime, project_id, drawing, tag=tag, clock=clock, log=log) for drawing in drawings]
    return {
        "benchmark": "drawing-visual-observation", "issue": "#303 303-S3", "tag": tag, "projectId": project_id,
        "order": list(ORDER), "armPlan": [{"arm": s.arm, "action": s.action, "stage": s.stage, "what": s.what}
                                          for s in arm_plan()],
        "criteria": [{"criterionId": name, "text": text, "lever": lever} for name, text, lever in DRAWING_CRITERIA],
        "projectRecipe": recipe, "drawings": results, "summary": summary(results),
        "totals": {**totals(results), "wallS": round(clock() - started, 3)},
    }


# ---- the command line ------------------------------------------------------------------------

_REVIEW_ONLY = ("task_class", "reason", "polish_reviews", "prior", "addressed", "codex", "model", "monitor_dir")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", required=True, help="project runtime base URL")
    parser.add_argument("--spec", required=True, type=Path,
                        help="review spec JSON (domain, sources, views, task, criteria, preserve); with --drawing, "
                             "the drawing spec (projectId, drawings: [{name, plan}])")
    parser.add_argument("--drawing", action="store_true",
                        help="run the four-arm drawing benchmark (303-S3) through the runtime's own routes")
    parser.add_argument("--tag", help="--drawing: the run's name in every drawing id it makes (default: UTC start time)")
    parser.add_argument("--timeout", type=float,
                        help="seconds for one provider look (default 240); with --drawing, for one runtime request "
                             "(default 600)")
    parser.add_argument("--task-class", choices=[c.value for c in TaskClass])
    parser.add_argument("--reason", choices=[r.value for r in ReviewReason])
    parser.add_argument("--polish-reviews", type=int)
    parser.add_argument("--prior", type=Path, help="the previous review result JSON of this loop")
    parser.add_argument("--addressed", help="comma-separated finding ids the repair addressed")
    parser.add_argument("--codex", help="the Codex executable (default codex)")
    parser.add_argument("--model")
    parser.add_argument("--monitor-dir", type=Path)
    args = parser.parse_args(argv)
    if args.drawing:
        given = [f"--{name.replace('_', '-')}" for name in _REVIEW_ONLY if getattr(args, name) is not None]
        if given:
            parser.error(f"--drawing runs its own review loop with the runtime's provider; drop {', '.join(given)}")
        if args.tag is not None and not _NAME.fullmatch(args.tag):
            parser.error("--tag is a short name of letters, digits, '.', '_' or '-'")
    else:
        if args.task_class is None or args.reason is None:
            parser.error("a review needs --task-class and --reason (or run the drawing benchmark with --drawing)")
        if args.tag is not None:
            parser.error("--tag names a --drawing run")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    # A spec saved by a Windows editor may start with a byte order mark.
    spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.drawing:
        tag = args.tag or time.strftime("%y%m%d%H%M%S", time.gmtime())
        try:
            drawing_spec(spec)
        except DrawingSpecInvalid as exc:
            print(f"benchmark_visual_observation.py: error: {exc}", file=sys.stderr)
            return 2
        result = run_drawing_benchmark(RuntimeClient(args.runtime, timeout_s=args.timeout or 600.0), spec, tag=tag,
                                       log=lambda line: print(line, file=sys.stderr, flush=True))
    else:
        prior = None if args.prior is None else json.loads(args.prior.read_text(encoding="utf-8"))
        provider, monitor = build_provider(codex=args.codex or "codex", model=args.model,
                                           timeout_s=args.timeout or 240.0, monitor_dir=args.monitor_dir)
        result = run_review(args.runtime, spec, task_class=args.task_class, reason=args.reason, prior=prior,
                            addressed=[item for item in (args.addressed or "").split(",") if item],
                            polish_reviews=args.polish_reviews, provider=provider, monitor=monitor)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
