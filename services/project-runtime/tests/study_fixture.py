"""Original synthetic orthographic Study input; no historical building or provider output.

The diagram bytes and coordinate data are dedicated to CC0-1.0. Test helper code
keeps the repository license. Functions return values only; the test/browser
caller decides its temporary output location and registers bytes through the
ordinary document API. Confirmed/user rows are an authored manual baseline,
never evidence that a person approved a machine proposal.
"""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO

from PIL import Image, ImageDraw


FIXTURE_NAME = "Synthetic stepped passage — not a historical building"
FIXTURE_LICENSE = "CC0-1.0"
CASES = ("base", "narrowed", "blocked", "detached", "contracted")


def fixture_evidence(case: str = "base") -> list[dict]:
    """Return page-normalized, manually authored polygons, with a concave mass."""
    if case not in CASES:
        raise ValueError(f"unknown synthetic Study case: {case}")
    definitions = (
        ("envelope", "envelope", [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]),
        ("left-mass", "mass", [
            (0.1, 0.1), (0.4, 0.1), (0.4, 0.4), (0.5, 0.4),
            (0.5, 0.6), (0.4, 0.6), (0.4, 0.9), (0.1, 0.9),
        ]),
        ("right-mass", "mass", [(0.6, 0.1), (0.9, 0.1), (0.9, 0.9), (0.6, 0.9)]),
        ("clear-strip", "void", [
            (0.4, 0.1), (0.6, 0.1), (0.6, 0.9), (0.4, 0.9),
            (0.4, 0.6), (0.5, 0.6), (0.5, 0.4), (0.4, 0.4),
        ]),
    )
    rows = [
        {"evidenceId": evidence_id, "kind": kind,
         "points": [list(p) for p in points], "status": "confirmed",
         "confidence": 1.0, "origin": "user"}
        for evidence_id, kind, points in definitions
    ]
    right = rows[2]
    if case in {"narrowed", "blocked", "detached"}:
        dx = {"narrowed": -0.05, "blocked": -0.12, "detached": 0.05}[case]
        right["points"] = [[round(x + dx, 8), y] for x, y in right["points"]]
    elif case == "contracted":
        right["points"] = [
            [round(0.75 + (x - 0.75) * 0.8, 8), round(0.5 + (y - 0.5) * 0.8, 8)]
            for x, y in right["points"]
        ]
    return rows


def fixture_png(case: str = "base", size: int = 800) -> bytes:
    """Render the exact authored coordinates; no generated-image provider is used."""
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    rows = fixture_evidence(case)
    # Draw the declared void first so overlaps remain visible as solid in variants.
    for row in sorted(rows, key=lambda item: item["kind"] == "mass"):
        points = [(round(x * size), round(y * size)) for x, y in row["points"]]
        if row["kind"] == "envelope":
            draw.polygon(points, outline="#111827", width=3)
        else:
            draw.polygon(points, fill="#dceef4" if row["kind"] == "void" else "#71808d",
                         outline="#263340", width=2)
    draw.text((0.1 * size, 0.035 * size), "SYNTHETIC ORTHOGRAPHIC FIXTURE / " + case.upper(), fill="#111827")
    draw.text((0.1 * size, 0.94 * size), "CC0 original diagram | grey = mass | blue = traced void | no scale", fill="#111827")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _inside(x: float, y: float, points: list[list[float]]) -> bool:
    """Ray parity for interior cell centers (never points on an edge)."""
    odd = False
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            odd = not odd
    return odd


def fixture_observations(case: str = "base") -> dict:
    """Independent exact rectangular-cell audit for these orthogonal fixtures.

    All polygon vertex coordinates split the plane into cells on which membership
    is constant. Flood fill joins cells sharing an edge of positive length, never
    just a corner. This measures the *declared void minus masses*, not traversable
    building space: surface, doors, headroom and human dimensions are unknown.
    The oracle deliberately does not call Shapely or the production Study method.
    """
    rows = fixture_evidence(case)
    polygons = {row["evidenceId"]: row["points"] for row in rows}
    xs = sorted({x for points in polygons.values() for x, _ in points})
    ys = sorted({y for points in polygons.values() for _, y in points})
    areas = {key: 0.0 for key in polygons}
    overlaps = {"void_left": 0.0, "void_right": 0.0, "mass_mass": 0.0}
    clear: dict[tuple[int, int], float] = {}
    for i, (x0, x1) in enumerate(zip(xs, xs[1:])):
        for j, (y0, y1) in enumerate(zip(ys, ys[1:])):
            area = (x1 - x0) * (y1 - y0)
            inside = {key: _inside((x0 + x1) / 2, (y0 + y1) / 2, p)
                      for key, p in polygons.items()}
            for key, yes in inside.items():
                if yes:
                    areas[key] += area
            v, a, b = (inside[key] for key in ("clear-strip", "left-mass", "right-mass"))
            for key, present in (("void_left", v and a), ("void_right", v and b),
                                 ("mass_mass", a and b)):
                if present:
                    overlaps[key] += area
            if v and inside["envelope"] and not a and not b:
                clear[i, j] = area
    remaining = set(clear)
    components: list[set[tuple[int, int]]] = []
    while remaining:
        component = {remaining.pop()}
        frontier = list(component)
        while frontier:
            i, j = frontier.pop()
            for neighbor in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    frontier.append(neighbor)
        components.append(component)
    joins_ends = any(
        any(abs(ys[j] - 0.1) < 1e-9 for _, j in component)
        and any(abs(ys[j + 1] - 0.9) < 1e-9 for _, j in component)
        for component in components
    )
    return {
        "method": "authored-orthogonal-cell-audit@1",
        "polygonAreas": {key: round(area, 8) for key, area in areas.items()},
        "intersectionAreas": {key: round(area, 8) for key, area in overlaps.items()},
        "clearVoidArea": round(sum(clear.values()), 8),
        "clearVoidComponents": len(components),
        "oneComponentJoinsNorthAndSouth": joins_ends,
        "passabilityEstablished": False,
    }


def fixture_research(*, changed_context: bool = False) -> dict:
    """Authored test protocol, not model output or the user's design preference."""
    assumptions = [
        "Orthographic 2D test only; all coordinates are page-normalized.",
        "Mass footprints represent blocked ground-plane regions for this experiment.",
        "The blue trace denotes the source void; recompute its unobstructed remainder after edits.",
        "No inference about historical intent, daylight, regulation, safety or user preference.",
    ]
    operations = (
        ("narrowed", "translate", {"dx": -0.05, "dy": 0.0},
         "Right mass intersects source void by 0.04 page-area units; clear area falls from 0.14 to 0.10. The remaining void stays connected."),
        ("blocked", "translate", {"dx": -0.12, "dy": 0.0},
         "Right mass intersects source void by 0.092; clear area becomes 0.048 and splits into two components. A 0.004 mass clash is a failed layout, not a valid solution."),
        ("detached", "translate", {"dx": 0.05, "dy": 0.0},
         "Right mass no longer touches the source void; polygon distance is 0.05. It also leaves the source envelope, so reject as a solution under fixed-envelope conditions."),
        ("contracted", "scale", {"scale": 0.8},
         "Right mass area becomes 0.1536 and no longer touches source void; distance is 0.03. Clear traced void area stays 0.14. Two-sided contact is lost without losing continuity."),
    )
    research = {
        "question": "Does the stepped void geometrically connect the two page edges, and does that establish its use?",
        "historicalSources": [],
        "hypotheses": [
            {"hypothesisId": "connection", "statement": "The clear strip could support a north-south connection if its ground surface and both ends are passable.",
             "evidenceIds": ["left-mass", "right-mass", "clear-strip"], "historicalSourceIds": [],
             "assumptions": assumptions + ["Its ground surface and both ends would need to be passable; this is not established by the drawing."],
             "falsification": "A full-width obstruction that splits the remaining void disproves geometric continuity in this model; a non-passable surface withdraws the circulation interpretation.",
             "competesWith": ["environmental-gap"], "status": "open"},
            {"hypothesisId": "environmental-gap", "statement": "The same geometric interval could be a non-passable light or drainage gap rather than a circulation connection.",
             "evidenceIds": ["left-mass", "right-mass", "clear-strip"], "historicalSourceIds": [],
             "assumptions": ["No section, roof, drainage, material or use record is available."],
             "falsification": "A source section and access record could distinguish the alternatives; plan geometry alone cannot demonstrate daylight or drainage performance.",
             "competesWith": ["connection"], "status": "open"},
        ],
        "gaps": [{"gapId": "use-and-section", "description": "Unknown surface, level, roof, endpoint access and metric scale; no historical project or actual designer intent exists for this synthetic fixture.",
                  "evidenceIds": ["clear-strip"]}],
        "counterfactuals": [
            {"counterfactualId": case, "hypothesisIds": ["connection"],
             "targetEvidenceId": "right-mass", "operation": operation,
             "parameters": parameters, "conditions": assumptions,
             "prediction": prediction, "execute": True}
            for case, operation, parameters, prediction in operations
        ],
        "compositionPattern": {
            "patternId": "conditional-clear-interval", "name": "Maintain a connected clear interval",
            "rule": "Preserve a connected unblocked interval when connection is required; two-sided contact is separately testable and not necessary for continuity.",
            "evidenceIds": ["left-mass", "right-mass", "clear-strip"],
            "conditions": ["The intended relation is geometric connection in this 2D model."],
            "exceptions": ["This does not prove usable passage, daylight, privacy or historical causation.", "A contracted flank loses contact but keeps the traced interval connected."],
        },
        "designPrior": {
            "priorId": "conditional-connection", "statement": "Where an independently established brief requires through-connection, test clear-region continuity before transferring the source silhouette.",
            "patternId": "conditional-clear-interval", "hypothesisIds": ["connection", "environmental-gap"],
            "conditions": ["Through-connection is an explicit requirement.", "Ground and endpoints are passable, and physical clearance is independently checked."],
            "preference": "No personal preference has been supplied.", "preferenceStatus": "unresolved",
            "changedContext": None,
        },
    }
    if changed_context:
        research = deepcopy(research)
        research["designPrior"]["changedContext"] = {
            "changedConditions": ["The interval is declared a non-passable planting/drainage strip; through-access is no longer required."],
            "decision": "revise",
            "reason": "Geometric connectivity remains measured, but the circulation applicability conditions are false. The environmental explanation remains untested.",
            "revisedStatement": "Retain the source as a geometric separation example; withdraw the circulation prior until a new brief and surface/access evidence support it.",
        }
    return research
