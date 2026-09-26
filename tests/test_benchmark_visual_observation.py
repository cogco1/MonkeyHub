"""The drawing benchmark's own logic (GH-303 303-S3): arm plan, exact checks, repair, metrics and arguments.

Nothing here reaches a runtime or a provider. ``FakeRuntime`` answers the
tool's requests the way the Studio routes do, from a canned page and canned
visual reviews, so the order of the arms and every number they report are
checked without a model call.
"""

from __future__ import annotations

import contextlib
from copy import deepcopy
import io
from itertools import count
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

from tools import benchmark_visual_observation as bench

TOLERANCE = 0.0025
CLEANUP = {"tolerance": TOLERANCE, "input_lines": 12, "output_lines": 8, "micro": 0, "collinear": 1,
           "cut_precedence": 3, "duplicate": 0, "hidden_under_cut": 0}
CURRENT = {"status": "current", "bindingChanged": False, "unresolvedObjectIds": [], "dimensions": [], "dressing": [],
           "lengthUnit": "meter"}
RECIPE = {"cutLineMm": 0.5, "visibleLineMm": 0.13, "hatchSpacingMm": 3.0}


def line(name, *points):
    named = "" if name is None else f' data-object="{name}"'
    return f'<polyline{named} points="{" ".join(f"{x},{y}" for x, y in points)}"/>'


def group(role, *marks):
    return f'<g id="{role}" fill="none" stroke="#000">' + "".join(marks) + "</g>"


def entourage(*items):
    return '<g id="dressing">' + "".join(
        f'<g data-dressing="{name}" data-asset="person-plan">' + "".join(
            f'<polyline points="{" ".join(f"{x},{y}" for x, y in points)}"/>' for points in polylines) + "</g>"
        for name, polylines in items) + "</g>"


def page(*groups, hidden_lines="false"):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="250.0000mm" '
            'height="250.0000mm" viewBox="0 0 5.0000 5.0000" data-unit="meter" data-scale="1:50" '
            f'data-crop-uv="0 0 5 5" data-hidden-lines="{hidden_lines}">' + "".join(groups) + "</svg>\n")


# One cut wall along the left side, hatched: every exact check passes.
WALL_A = [((0.5, 0.5), (0.5, 4.5)), ((0.8, 0.5), (0.8, 4.5)), ((0.5, 0.5), (0.8, 0.5)), ((0.5, 4.5), (0.8, 4.5))]
CLEAN_PAGE = page(group("visible", line("slab", (2, 2), (4, 2))),
                  group("section-hatch", line("wall-a", (0.5, 1), (0.8, 0.7))),
                  group("section", *(line("wall-a", *segment) for segment in WALL_A)))
ANCHORS = [{"objectId": name, "positionUv": [0, 0]} for name in ("wall-a", "wall-b", "slab", "speck")]


class ArmPlanTests(unittest.TestCase):
    def test_the_arms_follow_the_owners_order(self) -> None:
        plan = bench.arm_plan()
        self.assertEqual([f"{step.arm}.{step.action}" for step in plan], [
            "A.compile", "A.checks", "B.compile", "B.checks", "B.recipe", "C.review", "D.repair", "D.checks",
            "D.recheck"])
        # On the path to D (B, then C, then D): exact checks, recipe, one review, typed repair, one re-check.
        path = [step.stage for step in plan if step.arm != "A" and step.stage is not None]
        firsts = [stage for index, stage in enumerate(path) if stage not in path[:index]]
        self.assertEqual(firsts, list(bench.ORDER))
        self.assertEqual([step.action for step in plan].count("recheck"), 1)
        # A page's exact checks come before any look at it.
        for look, page in (("C.review", "B.checks"), ("D.recheck", "D.checks")):
            keys = [f"{step.arm}.{step.action}" for step in plan]
            self.assertLess(keys.index(page), keys.index(look))

    def test_a_spec_names_one_source_and_leaves_the_arms_their_own_fields(self) -> None:
        good = {"projectId": "p", "drawings": [{"name": "plan-1", "plan": {"sourceStageRef": "s", "cutHeight": 1.2}}]}
        self.assertEqual(bench.drawing_spec(good)[0], "p")
        for spec in (
            {"drawings": good["drawings"]},
            {"projectId": "p", "drawings": []},
            {"projectId": "p", "drawings": [{"name": "plan 1", "plan": {"sourceStageRef": "s"}}]},
            {"projectId": "p", "drawings": good["drawings"] * 2},
            {"projectId": "p", "drawings": [{"name": "plan-1", "plan": {"sourceStageRef": "s", "hatchSpacingMm": 3}}]},
            {"projectId": "p", "drawings": [{"name": "plan-1", "plan": {"cutHeight": 1.2}}]},
            {"projectId": "p", "drawings": [{"name": "plan-1", "plan": {"sourceStageRef": "s", "modelSource": {}}}]},
        ):
            with self.subTest(spec=spec), self.assertRaises(bench.DrawingSpecInvalid):
                bench.drawing_spec(spec)


class ExactCheckTests(unittest.TestCase):
    def test_a_clean_page_passes_every_check(self) -> None:
        result = bench.drawing_checks(CLEAN_PAGE, status=CURRENT, anchors=ANCHORS, cleanup=CLEANUP)
        self.assertEqual(result["counts"], dict.fromkeys(bench.CHECKS, 0))
        self.assertEqual(result["found"]["cutObjects"], 1)

    def test_each_check_finds_its_own_fault_in_what_the_page_draws(self) -> None:
        wall_b = [((0.5, 0.5), (4.5, 0.5)), ((0.5, 0.8), (4.5, 0.8)), ((0.5, 0.5), (0.5, 0.8)), ((4.5, 0.5), (4.5, 0.8))]
        svg = page(
            group("hidden", line("slab", (3, 1.2), (4, 1.2))),
            group("visible", line("slab", (0.8, 1.0), (0.8, 2.0)), line("speck", (2.0, 2.0), (2.001, 2.0)),
                  line(None, (3, 3), (4, 3)), line("ghost", (3, 3.5), (4, 3.5))),
            group("section-hatch", line("wall-a", (0.5, 1), (0.8, 0.7))),
            group("section", *(line("wall-a", *segment) for segment in WALL_A),
                  *(line("wall-b", *segment) for segment in wall_b)),
            entourage(("person-1", [[(2.0, 0.6), (2.0, 1.0)]]), ("person-2", [[(4.8, 4.8), (5.3, 4.8)]])),
        )
        status = {**CURRENT, "dressing": [{"id": "person-3", "status": "outside-view"}]}
        result = bench.drawing_checks(svg, status=status, anchors=ANCHORS, cleanup=CLEANUP)
        self.assertEqual(result["counts"], {
            # the slab on wall-a's cut, and each wall's end drawn on the other's face
            "duplicates": 3, "hidden_edges": 1, "micro_segments": 1,
            "out_of_bounds": 2, "missing_hatch": 1, "source_binding": 2, "collisions": 1})
        found = result["found"]
        self.assertEqual(found["cleanupAgain"]["cut_precedence"], 1)
        self.assertEqual(sorted(row["object"] for row in found["onOtherObjects"]), ["wall-a", "wall-b"])
        self.assertEqual(found["missingHatch"], ["wall-b"])
        self.assertEqual(found["entourageOffSheet"], ["person-2", "person-3"])
        self.assertEqual(found["colliding"], ["person-1"])
        self.assertEqual(found["bindingFailures"], ["1 projected marks name no source object",
                                                    "object ghost is not in the exact source"])

    def test_the_status_says_what_binds_the_page(self) -> None:
        status = {"status": "outdated", "bindingChanged": True, "unresolvedObjectIds": ["gone"],
                  "dimensions": [{"id": "door", "status": "missing"}, {"id": "ok", "status": "resolved"}],
                  "dressing": [{"id": "person-1", "status": "missing"}]}
        result = bench.drawing_checks(CLEAN_PAGE, status=status, anchors=ANCHORS, cleanup=CLEANUP)
        self.assertEqual(result["counts"]["source_binding"], 5)
        # An imported model offers no anchors, so its objects are not checked against any.
        self.assertEqual(bench.drawing_checks(page(group("section", line("x", (1, 1), (2, 1)))), status=CURRENT,
                                              anchors=[], cleanup=CLEANUP)["counts"]["source_binding"], 0)

    def test_without_a_cleanup_report_the_cleanup_rules_are_not_measured(self) -> None:
        counts = bench.drawing_checks(CLEAN_PAGE, status=CURRENT, anchors=ANCHORS, cleanup=None)["counts"]
        self.assertEqual([counts[key] for key in ("duplicates", "hidden_edges", "micro_segments")], [None] * 3)
        self.assertEqual(bench.remaining_corrections(counts, [], None), 0)

    def test_nearly_vertical_lines_either_side_of_vertical_meet(self) -> None:
        lines = [("section", "a", ((1.0, 0.0), (1.0001, 2.0))), ("section", "b", ((1.0001, 0.5), (1.0, 1.5))),
                 ("section", "c", ((2.0, 0.0), (2.0, 1.0))), ("section", "d", ((2.0, 1.0), (2.0, 0.0)))]
        # b lies on a; c and d are the same edge drawn by two objects, each covering the other.
        self.assertEqual(bench._on_other_objects(lines, TOLERANCE), [1, 2, 3])

    def test_recipe_deviations_are_the_keys_the_recipe_draws_otherwise(self) -> None:
        self.assertEqual(bench.recipe_deviations(bench.PAPER_DEFAULTS, RECIPE),
                         ["cutLineMm", "visibleLineMm", "hatchSpacingMm"])
        self.assertEqual(bench.recipe_deviations({**RECIPE, "beyond": {"fade": 0.4}}, RECIPE), [])


def finding(name, severity, *targets, kind="composition"):
    return {"findingId": name, "type": kind, "targetRefs": list(targets), "description": f"{name} as seen",
            "confidence": 0.8, "severity": severity, "evidenceRegion": None}


def reviewed(*dressing):
    return {"graphics": dict(RECIPE), "dressing": [{"id": name, "assetId": "person-plan"} for name in dressing]}


class RepairTests(unittest.TestCase):
    def test_each_criterion_turns_its_one_lever_and_a_preserve_finding_is_escalated(self) -> None:
        plan = bench.repair_plan(reviewed("person-1", "tree-1", "person-2"), [
            finding("f1", "minor", "criterion:hierarchy"),
            finding("f2", "major", "criterion:hatch", "criterion:density"),
            finding("f3", "minor", "criterion:balance"),
            finding("f4", "minor", "criterion:entourage", "preserve:1"),
            finding("f5", "info", "criterion:entourage"),
            finding("f6", "major", "criterion:entourage"),
            finding("f7", "minor", "criterion:hierarchy"),
        ])
        self.assertEqual(plan["fields"], {"cutLineMm": 0.7, "hatchSpacingMm": 4.5, "beyond": {"fade": 0.4},
                                          "dressingOperations": [{"op": "delete", "id": "person-2"}]})
        self.assertEqual(plan["levers"], {"cut_line": ["f1", "f7"], "hatch_spacing": ["f2"], "beyond_fade": ["f2"],
                                          "thin_entourage": ["f6"]})
        self.assertEqual((plan["addressed"], plan["escalated"], plan["unaddressed"]),
                         (["f1", "f2", "f6", "f7"], ["f4"], ["f3"]))

    def test_a_lever_at_its_bound_addresses_nothing(self) -> None:
        bold = {"graphics": {**RECIPE, "cutLineMm": 2.0, "beyond": {"fade": 0.8}}, "dressing": [{"id": "p"}]}
        plan = bench.repair_plan(bold, [finding("f1", "major", "criterion:hierarchy"),
                                        finding("f2", "minor", "criterion:density"),
                                        finding("f3", "minor", "criterion:entourage")])
        self.assertEqual((plan["fields"], plan["addressed"], plan["unaddressed"]), ({}, [], ["f1", "f2", "f3"]))

    def test_the_look_is_told_the_exact_facts_within_the_routes_bounds(self) -> None:
        checks = bench.drawing_checks(CLEAN_PAGE, status=CURRENT, anchors=ANCHORS, cleanup=CLEANUP)
        document = {"viewRecipe": {"frame": {"origin": [0, 0, 1.2], "far_depth": 1.2, "scale": "1:50"},
                                   "graphics": {**RECIPE, "beyond": {"fade": 0.4}},
                                   "dressing": [{"id": "p", "assetId": "person-plan"}]}}
        facts = bench.known_facts(document, checks, CURRENT, CLEANUP)
        self.assertLessEqual(len(facts), 8)
        self.assertTrue(all(0 < len(fact) <= 120 for fact in facts), facts)
        self.assertIn("Cut plan at 1:50: cut plane at 1.2 m; lines below drawn down to 0 m.", facts)
        self.assertIn("Pens on paper: cut 0.5 mm, below the cut 0.13 mm, greyed 0.4.", facts)
        body = bench.review_body("p", {"runId": "r", "assetSha256": "a" * 64, "revisionRef": "rev"}, facts,
                                 reason="after_repair", budget_state={"taskClass": "spatial_formal", "allowed": 2,
                                                                      "used": 1, "lastFindingIds": ["f1"]},
                                 prior=[{"ref": "vr-1:f1", "type": "composition", "description": "x" * 400}],
                                 addressed=["f1"])
        self.assertEqual(body["sourceRefs"], [{"kind": "page", "runId": "r", "assetSha256": "a" * 64,
                                               "revisionRef": "rev", "pageIndex": 0}])
        self.assertEqual((body["domain"], body["viewRecipe"], body["addressedFindingIds"]), ("drawing", ["page-0"], ["f1"]))
        self.assertEqual(len(body["priorObservations"][0]["description"]), 300)
        self.assertLessEqual(len(body["task"]), 600)


def document(arm, graphics, *, dressing=()):
    return {"runId": "run-1", "assetSha256": f"{ord(arm):064x}", "revisionRef": f"rev-{arm}", "drawingId": f"d-{arm}",
            "viewRecipe": {"graphics": graphics, "dressing": list(dressing)}}


class MetricTests(unittest.TestCase):
    def state(self, recheck):
        counts = {**dict.fromkeys(bench.CHECKS, 0), "duplicates": 8}
        return {
            "documents": {"A": document("A", dict(bench.PAPER_DEFAULTS)), "B": document("B", dict(RECIPE)),
                          "D": document("D", {**RECIPE, "cutLineMm": 0.7})},
            "checks": {arm: {"counts": dict(counts)} for arm in "ABD"},
            "deviations": ["cutLineMm", "visibleLineMm", "hatchSpacingMm"],
            "reviews": {"C": {"ok": True, "findings": [finding("f1", "minor", "criterion:hierarchy"),
                                                       finding("f2", "info", "criterion:hatch")],
                              "usage": {"providerCalls": 1, "imageInputs": 1, "inputTokens": 20000, "outputTokens": 500}},
                        "D": {"ok": True, "findings": recheck,
                              "usage": {"providerCalls": 1, "imageInputs": 1, "inputTokens": 21000, "outputTokens": 400}}},
            "repair": {"addressed": ["f1"]},
            "timings": {"A.compile": 3, "A.checks": 1, "B.compile": 3, "B.checks": 1, "B.recipe": 0, "C.review": 40,
                        "D.repair": 3, "D.checks": 1, "D.recheck": 45},
        }

    def test_corrections_cost_and_wall_clock_by_arm(self) -> None:
        arms = bench.arm_results(self.state([finding("f1", "info", "criterion:hierarchy")]))
        self.assertEqual({arm: row["remainingCorrections"] for arm, row in arms.items()},
                         {"A": 4, "B": 1, "C": 2, "D": 1})
        self.assertEqual([arms[arm]["visualObserved"] for arm in bench.ARMS], [False, False, True, True])
        self.assertEqual(arms["B"]["laterObservedFindings"], 1)
        self.assertEqual(arms["D"]["page"]["revisionRef"], "rev-D")
        self.assertEqual(arms["C"]["page"]["revisionRef"], "rev-B")
        self.assertEqual({arm: row["wallS"]["cumulative"] for arm, row in arms.items()},
                         {"A": 4, "B": 4, "C": 44, "D": 93})
        self.assertEqual((arms["C"]["cost"]["inputTokens"], arms["D"]["cost"]["inputTokens"]), (20000, 41000))
        self.assertEqual((arms["A"]["cost"]["providerCalls"], arms["D"]["cost"]["providerCalls"]), (None, 2))
        # The repair answered f1 and the second look agrees: it changed nothing about acceptance.
        self.assertEqual((arms["D"]["visuallyAcceptedWithoutSecondLook"], arms["D"]["visuallyAccepted"],
                          arms["D"]["secondLookChanged"]), (True, True, False))
        self.assertEqual((arms["D"]["reopenedCriteria"], arms["D"]["newCriteria"],
                          arms["D"]["remainingCorrectionsWithoutSecondLook"]), ([], [], 1))
        self.assertEqual((arms["C"]["openBySeverity"], arms["A"]["openBySeverity"]), ({"minor": 1, "major": 0}, None))

    def test_a_second_look_that_still_sees_the_fault_changes_the_acceptance(self) -> None:
        arms = bench.arm_results(self.state([finding("f1", "minor", "criterion:hierarchy")]))
        self.assertEqual((arms["D"]["visuallyAcceptedWithoutSecondLook"], arms["D"]["visuallyAccepted"],
                          arms["D"]["secondLookChanged"], arms["D"]["remainingCorrections"]), (True, False, True, 2))
        self.assertEqual((arms["D"]["reopenedCriteria"], arms["D"]["remainingCorrectionsWithoutSecondLook"]),
                         (["hierarchy"], 1))

    def test_a_second_look_can_matter_when_the_acceptance_does_not_turn(self) -> None:
        # A finding with no lever keeps D open either way; the look still reopens what the repair claimed.
        state = self.state([finding("f1", "minor", "criterion:hierarchy"), finding("f2", "minor", "criterion:balance"),
                            finding("f3", "major", "criterion:hatch")])
        state["reviews"]["C"]["findings"].append(finding("f3", "minor", "criterion:balance"))
        d = bench.arm_results(state)["D"]
        self.assertEqual((d["openWithoutSecondLook"], d["visuallyAcceptedWithoutSecondLook"], d["visuallyAccepted"],
                          d["secondLookChanged"]), (["f3"], False, False, False))
        self.assertEqual((d["reopenedCriteria"], d["newCriteria"]), (["hierarchy"], ["hatch"]))
        self.assertEqual((d["remainingCorrectionsWithoutSecondLook"], d["remainingCorrections"]), (2, 4))


class FakeRuntime:
    """The Studio routes the benchmark calls, answered from a canned page and canned looks.

    A cut plan's pens are the request's, else its previous revision's, else
    the project recipe's, as ``generate_plan`` resolves them.
    """

    def __init__(self, looks, *, status=CURRENT, svg=CLEAN_PAGE):
        self.looks, self.status, self.svg = list(looks), status, svg
        self.calls, self.revisions = [], {}

    def request(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        route = urlsplit(path).path
        if (method, route) == ("GET", "/api/decisions"):
            return 200, {"projectId": "p", "decisions": [{
                "decisionId": "d1", "status": "active", "targetRef": "drawing:hatch", "strength": "strong_preference",
                "scope": {"domain": "drawing", "extent": "project"},
                "typedBinding": {"kind": "recipe", "graphics": {"hatchSpacingMm": 3.0}}}]}
        if (method, route) == ("POST", "/api/drawings/plans"):
            previous = self.revisions.get(body.get("previousRevisionRef"), {}).get("viewRecipe", {})
            graphics = {key: body.get(key, (previous.get("graphics") or RECIPE)[key]) for key in RECIPE}
            beyond = body.get("beyond", (previous.get("graphics") or {}).get("beyond"))
            if beyond:
                graphics["beyond"] = beyond
            dressing = [row for row in previous.get("dressing", body.get("dressing", []))
                        if row["id"] not in {op["id"] for op in body.get("dressingOperations", ())}]
            revision = f"rev-{len(self.revisions) + 1}"
            self.revisions[revision] = {
                "runId": "run-1", "assetSha256": f"{len(self.revisions) + 1:064x}", "revisionRef": revision,
                "drawingId": body["drawingId"], "sourceKind": body["sourceKind"],
                "viewRecipe": {"frame": {"origin": [0, 0, 1.2], "far_depth": 1.2, "scale": "1:50"},
                               "graphics": graphics, "dressing": dressing}}
            return 201, deepcopy(self.revisions[revision])
        if (method, route) == ("GET", "/api/drawings/plans/vector"):
            assert parse_qs(urlsplit(path).query)["revisionRef"][0] in self.revisions
            return 200, {"svg": self.svg, "anchors": ANCHORS, "cleanup": CLEANUP}
        if (method, route) == ("POST", "/api/drawings/plans/status"):
            return 200, {**self.status, "cleanup": CLEANUP}
        if (method, route) == ("POST", "/api/visual-reviews"):
            code, findings = self.looks.pop(0)
            if code != 200:
                return code, {"code": findings, "detail": "refused", "budgetState": body["budgetState"]}
            used = body["budgetState"]["used"] + 1
            return 200, {
                "observation": {"reviewId": f"vr-{used}", "reviewIndex": used, "domain": "drawing",
                                "sourceRefs": body["sourceRefs"], "viewRefs": ["page-0"], "frameSha256": ["f" * 64],
                                "observations": findings, "unresolvedQuestions": [], "suggestedChecks": []},
                "usage": {"provider": "codex", "model": "m", "providerCalls": 1, "imageInputs": 1, "imageBytes": 9000,
                          "inputTokens": 20000, "cachedInputTokens": 0, "outputTokens": 400,
                          "reasoningOutputTokens": 30, "durationMs": 30000, "receiptId": "r"},
                "budgetState": {**body["budgetState"], "used": used,
                                "lastFindingIds": [row["findingId"] for row in findings]}}
        raise AssertionError(f"unexpected {method} {path}")

    def routes(self):
        return [(method, urlsplit(path).path) for method, path, _ in self.calls]


SPEC = {"projectId": "p", "drawings": [{"name": "plan", "plan": {
    "sourceStageRef": "stage-1", "cutHeight": 1.2, "bottom": 0, "scaleDenominator": 50,
    "dressing": [{"id": name, "assetId": "person-plan", "positionUv": [1, 1], "size": 0.6, "flipped": False,
                  "anchorObjectId": None} for name in ("person-1", "person-2", "person-3")]}}]}


def run(runtime):
    ticks = count()
    return bench.run_drawing_benchmark(runtime, SPEC, tag="t1", clock=lambda: float(next(ticks)))


class DrawingBenchmarkTests(unittest.TestCase):
    def test_the_four_arms_run_in_order_against_the_runtime(self) -> None:
        runtime = FakeRuntime([(200, [finding("f1", "minor", "criterion:hatch"),
                                      finding("f2", "major", "criterion:entourage", kind="spatial"),
                                      finding("f3", "info", "criterion:hierarchy")]),
                               (200, [finding("f1", "minor", "criterion:entourage")])])
        result = run(runtime)
        plans, vector, status, looks = (("POST", "/api/drawings/plans"), ("GET", "/api/drawings/plans/vector"),
                                        ("POST", "/api/drawings/plans/status"), ("POST", "/api/visual-reviews"))
        self.assertEqual(runtime.routes(), [("GET", "/api/decisions"), plans, vector, status, plans, vector, status,
                                            looks, plans, vector, status, looks])
        bodies = [body for method, path, body in runtime.calls if body is not None]
        compile_a, compile_b, first, repair, second = (bodies[0], bodies[2], bodies[4], bodies[5], bodies[7])
        self.assertEqual({key: compile_a[key] for key in RECIPE}, bench.PAPER_DEFAULTS)
        self.assertFalse(set(RECIPE) & set(compile_b))
        self.assertEqual((compile_a["drawingId"], compile_b["drawingId"]), ("bench-t1-plan-a", "bench-t1-plan-b"))
        self.assertTrue(all(body["sourceKind"] == "agent" for body in (compile_a, compile_b, repair)))
        # One first look at B's page, then the repair of B's revision, then one look at the repair.
        self.assertEqual((first["reason"], first["domain"], first["sourceRefs"][0]["revisionRef"]),
                         ("first_bundle", "drawing", "rev-2"))
        self.assertEqual(first["budgetState"], {"taskClass": "spatial_formal", "allowed": 2, "used": 0,
                                                "lastFindingIds": []})
        self.assertTrue(first["knownFacts"])
        self.assertEqual((repair["previousRevisionRef"], repair["drawingId"], repair["sourceStageRef"]),
                         ("rev-2", "bench-t1-plan-b", "stage-1"))
        self.assertEqual({key: repair[key] for key in ("hatchSpacingMm", "dressingOperations")},
                         {"hatchSpacingMm": 4.5, "dressingOperations": [{"op": "delete", "id": "person-2"}]})
        self.assertNotIn("dressing", repair)
        self.assertEqual((second["reason"], second["sourceRefs"][0]["revisionRef"], second["addressedFindingIds"]),
                         ("after_repair", "rev-3", ["f1", "f2"]))
        self.assertEqual((second["budgetState"]["used"], [row["findingRef"] for row in second["priorObservations"]]),
                         (1, ["vr-1:f1", "vr-1:f2"]))
        arms = result["drawings"][0]["arms"]
        self.assertEqual(arms["A"]["recipeDeviations"], ["cutLineMm", "visibleLineMm", "hatchSpacingMm"])
        self.assertEqual({arm: row["remainingCorrections"] for arm, row in arms.items()},
                         {"A": 3, "B": 0, "C": 2, "D": 1})
        self.assertEqual((arms["D"]["secondLookChanged"], arms["D"]["repaired"], arms["D"]["reopenedCriteria"],
                          arms["D"]["remainingCorrectionsWithoutSecondLook"]), (True, True, ["entourage"], 0))
        # The recorded state scores again to the same arms.
        self.assertEqual(bench.arm_results(result["drawings"][0]["state"]), arms)
        self.assertEqual((result["totals"]["looks"], result["totals"]["providerCalls"],
                          result["totals"]["inputTokens"]), (2, 2, 40000))
        self.assertEqual([row["arm"] for row in result["summary"]], list(bench.ARMS))
        self.assertEqual(result["projectRecipe"][0]["graphics"], {"hatchSpacingMm": 3.0})
        # Every step took one tick of the fake clock: D is B, C and its own three steps.
        self.assertEqual(arms["D"]["wallS"]["cumulative"], 7)

    def test_without_an_actionable_finding_nothing_is_repaired_or_looked_at_again(self) -> None:
        runtime = FakeRuntime([(200, [finding("f1", "info", "criterion:hierarchy")])])
        drawing = run(runtime)["drawings"][0]
        self.assertEqual(runtime.routes().count(("POST", "/api/visual-reviews")), 1)
        self.assertEqual(runtime.routes().count(("POST", "/api/drawings/plans")), 2)
        skipped = {row["step"]: row.get("skipped") for row in drawing["steps"]}
        self.assertEqual(skipped["D.repair"], "no actionable finding")
        self.assertEqual(drawing["arms"]["D"]["secondLookChanged"], None)
        self.assertEqual((drawing["arms"]["C"]["visuallyAccepted"], drawing["arms"]["D"]["visuallyAccepted"]),
                         (True, True))

    def test_a_page_that_is_not_exactly_bound_is_not_looked_at(self) -> None:
        runtime = FakeRuntime([], status={**CURRENT, "status": "outdated"})
        drawing = run(runtime)["drawings"][0]
        self.assertNotIn(("POST", "/api/visual-reviews"), runtime.routes())
        self.assertIn("status outdated", {row["step"]: row.get("skipped") for row in drawing["steps"]}["C.review"])
        self.assertEqual(drawing["arms"]["C"]["visualObserved"], False)

    def test_a_refused_look_ends_the_loop_and_costs_nothing(self) -> None:
        runtime = FakeRuntime([(409, "VISUAL_PROVIDER_UNAVAILABLE")])
        result = run(runtime)
        drawing = result["drawings"][0]
        self.assertEqual(drawing["state"]["reviews"]["C"]["code"], "VISUAL_PROVIDER_UNAVAILABLE")
        self.assertEqual(runtime.routes().count(("POST", "/api/drawings/plans")), 2)
        self.assertEqual((result["totals"]["looks"], result["totals"]["answered"], result["totals"]["providerCalls"]),
                         (1, 0, None))


class ArgumentTests(unittest.TestCase):
    def refused(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bench.parse_args(list(argv))

    def test_the_drawing_benchmark_takes_its_own_arguments(self) -> None:
        args = bench.parse_args(["--drawing", "--runtime", "http://127.0.0.1:8000", "--spec", "drawings.json",
                                 "--tag", "smoke"])
        self.assertEqual((args.drawing, args.spec, args.tag, args.timeout), (True, Path("drawings.json"), "smoke", None))
        self.refused("--drawing", "--runtime", "u", "--spec", "s.json", "--reason", "first_bundle")
        self.refused("--drawing", "--runtime", "u", "--spec", "s.json", "--codex", "codex")
        self.refused("--drawing", "--runtime", "u")

    def test_a_single_review_still_needs_its_class_and_reason(self) -> None:
        args = bench.parse_args(["--runtime", "u", "--spec", "s.json", "--task-class", "spatial_formal",
                                 "--reason", "first_bundle"])
        self.assertEqual((args.drawing, args.task_class, args.addressed), (False, "spatial_formal", None))
        self.refused("--runtime", "u", "--spec", "s.json", "--reason", "first_bundle")
        self.refused("--runtime", "u", "--spec", "s.json", "--task-class", "spatial_formal", "--reason",
                     "first_bundle", "--tag", "x")


if __name__ == "__main__":
    unittest.main()
