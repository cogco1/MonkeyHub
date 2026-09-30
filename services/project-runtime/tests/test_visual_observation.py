"""Source-bound, read-only visual observation: binding, Harness budget and answer contract (GH-303)."""

import base64
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from archflow.ports.model import ModelPhase
from archflow_studio_api.application import intent_agent
from archflow_studio_api.application.intent_agent import CodexCompiler
from archflow_studio_api.application.monitoring import MonitoredCompiler, StudioMonitor
from archflow_studio_api.application.visual_observation import (
    Criterion, EvidenceFrame, ObservationUsage, ProviderAnswer, ProviderCapability, ReviewReason, SourceRef,
    StudioModelVisualProvider, TaskClass, VisualBudgetRefused, VisualObservationInvalid, VisualProviderFailed,
    VisualObservation, VisualReviewBudget, VisualReviewInvalid, VisualReviewRequest, VisualSourceMismatch, bind_frames,
    model_view_frame, observation_schema, observe_frames, page_frame, parse_observation,
)
from monkeymonitor.store import UsageLog


RUN = "hub-cand-visual"
STATE = "a" * 64
OLDER_STATE = "b" * 64
ASSET = "c" * 64


def png(width=4, height=3, shade=255):
    """A tiny valid grayscale PNG, so no image library decides the test."""

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + bytes([shade]) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def view_answer(view, state=STATE, shade=255):
    data = png(shade=shade)
    return {"source": {"runId": RUN, "stateDigest": state, "assetSha256": ASSET}, "view": view,
            "mimeType": "image/png", "data": base64.b64encode(data).decode("ascii"),
            "width": 4, "height": 3, "representation": "orthographic-line-projection"}


def request(budget=2, views=("top", "front")):
    return VisualReviewRequest(
        domain="modeling", source_refs=(SourceRef.model(RUN, STATE, ASSET),), view_recipe=views,
        task="Make the void feel generous.",
        criteria=(Criterion("void-generous", "The central void reads as the dominant open space."),),
        preserve=("Keep the half-level path.",), budget=budget,
    )


GOOD = {
    "observations": [{
        "type": "spatial", "target_refs": ["criterion:void-generous"],
        "description": "Stair flights crowd two sides of the void.", "confidence": 0.7, "severity": "major",
        "evidence_region": {"view_ref": "top", "x0": 0.3, "y0": 0.3, "x1": 0.6, "y1": 0.6},
    }],
    "unresolved_questions": [], "suggested_checks": ["Measure the clear void width."],
}


class CountingProvider:
    def __init__(self, payload=GOOD):
        self.payload, self.calls = payload, []

    def capability(self):
        return ProviderCapability("fake", "fake-model", 4, 4 * 1024 * 1024, ("image/png",), True)

    def observe(self, request, frames):
        self.calls.append((request, tuple(frames)))
        usage = ObservationUsage("fake", "fake-model", 1, len(frames), sum(len(f.png) for f in frames),
                                 None, None, None, None, None, None)
        return ProviderAnswer(self.payload, usage)


class SourceBindingTests(unittest.TestCase):
    def test_model_view_answer_keeps_the_owner_source_and_pixels(self):
        frame = model_view_frame(view_answer("top"))
        self.assertEqual(frame.source, SourceRef.model(RUN, STATE, ASSET))
        self.assertEqual((frame.view_ref, frame.width, frame.height), ("top", 4, 3))
        with self.assertRaises(VisualReviewInvalid):
            model_view_frame({**view_answer("top"), "width": 5})
        with self.assertRaises(VisualReviewInvalid):
            model_view_frame({**view_answer("top"), "data": base64.b64encode(b"not a png").decode("ascii")})
        with self.assertRaises(VisualReviewInvalid):
            model_view_frame(view_answer("perspective"))

    def test_a_stale_frame_is_refused_before_the_provider_and_spends_nothing(self):
        provider, budget = CountingProvider(), VisualReviewBudget.for_task(TaskClass.SPATIAL_FORMAL)
        stale = [model_view_frame(view_answer("top", state=OLDER_STATE)), model_view_frame(view_answer("front"))]
        with self.assertRaises(VisualSourceMismatch):
            observe_frames(request(), stale, provider=provider, budget=budget, reason=ReviewReason.FIRST_BUNDLE)
        self.assertEqual((provider.calls, budget.used), ([], 0))

    def test_frames_must_match_the_view_recipe_once_each_and_cover_every_source(self):
        top, front = model_view_frame(view_answer("top")), model_view_frame(view_answer("front"))
        self.assertEqual(bind_frames(request(), [top, front]), (top, front))
        for frames in ([top, top], [model_view_frame(view_answer("right"))], []):
            with self.subTest(frames=[f.view_ref for f in frames]), self.assertRaises(VisualReviewInvalid):
                bind_frames(request(), frames)
        page = SourceRef.page("studio-documents", ASSET, None, 0)
        two = VisualReviewRequest(domain="board", source_refs=(page, SourceRef.page("studio-documents", "d" * 64, None, 0)),
                                  view_recipe=("page-0",), task="Board page", budget=1,
                                  criteria=(Criterion("legible", "Text is legible."),))
        with self.assertRaises(VisualReviewInvalid):
            bind_frames(two, [page_frame(page, png())])

    def test_page_sources_bind_their_exact_registration(self):
        with self.assertRaises(VisualReviewInvalid):
            SourceRef("page", "studio-documents", ASSET, state_digest=STATE, page_index=0)
        with self.assertRaises(VisualReviewInvalid):
            SourceRef.page("studio-documents", ASSET, None, -1)
        revised = SourceRef.page("studio-documents", ASSET, "project://P/runs/r/records/x.json", 0)
        self.assertNotEqual(revised, SourceRef.page("studio-documents", ASSET, None, 0))


class HarnessBudgetTests(unittest.TestCase):
    def test_deterministic_edits_take_no_visual_review(self):
        budget = VisualReviewBudget.for_task(TaskClass.DETERMINISTIC_EDIT)
        self.assertEqual(budget.allowed, 0)
        with self.assertRaises(VisualBudgetRefused) as refused:
            budget.admit(ReviewReason.FIRST_BUNDLE)
        self.assertEqual(refused.exception.code, "VISUAL_REVIEW_NOT_WARRANTED")

    def test_spatial_task_gets_one_review_and_one_follow_up_after_a_real_repair(self):
        budget = VisualReviewBudget.for_task(TaskClass.SPATIAL_FORMAL)
        self.assertIsNotNone(budget.refusal(ReviewReason.AFTER_REPAIR))
        self.assertIsNone(budget.refusal(ReviewReason.FIRST_BUNDLE))
        self.assertEqual(budget.used, 0)
        self.assertEqual(budget.admit(ReviewReason.FIRST_BUNDLE), 1)
        for reason in (ReviewReason.FIRST_BUNDLE, ReviewReason.AFTER_REPAIR, ReviewReason.POLISH_ROUND):
            with self.subTest(reason=reason), self.assertRaises(VisualBudgetRefused):
                budget.admit(reason)
        budget.settle(parse_observation(GOOD, request(), [model_view_frame(view_answer("top"))], review_index=1))
        with self.assertRaises(VisualBudgetRefused):
            budget.note_repair(["f9"])
        budget.note_repair(["f1"])
        self.assertEqual(budget.admit(ReviewReason.AFTER_REPAIR), 2)
        budget.settle(parse_observation(GOOD, request(), [model_view_frame(view_answer("top"))], review_index=2))
        budget.note_repair(["f1"])
        with self.assertRaises(VisualBudgetRefused) as refused:
            budget.admit(ReviewReason.AFTER_REPAIR)
        self.assertEqual(refused.exception.code, "VISUAL_BUDGET_EXHAUSTED")

    def test_only_an_explicit_polish_request_raises_the_bounded_budget(self):
        with self.assertRaises(VisualReviewInvalid):
            VisualReviewBudget.for_task(TaskClass.SPATIAL_FORMAL, polish_reviews=3)
        for rounds in (None, 0, 5):
            with self.subTest(rounds=rounds), self.assertRaises(VisualReviewInvalid):
                VisualReviewBudget.for_task(TaskClass.POLISH, polish_reviews=rounds)
        budget = VisualReviewBudget.for_task(TaskClass.POLISH, polish_reviews=3)
        self.assertEqual([budget.admit(ReviewReason.POLISH_ROUND) for _ in range(3)], [1, 2, 3])
        with self.assertRaises(VisualBudgetRefused):
            budget.admit(ReviewReason.POLISH_ROUND)

    def test_an_exhausted_budget_refuses_before_the_provider(self):
        provider, budget = CountingProvider(), VisualReviewBudget.for_task(TaskClass.SPATIAL_FORMAL)
        frames = [model_view_frame(view_answer("top")), model_view_frame(view_answer("front"))]
        result = observe_frames(request(), frames, provider=provider, budget=budget, reason="first_bundle")
        self.assertEqual(result.observation.review_index, 1)
        with self.assertRaises(VisualBudgetRefused):
            observe_frames(request(), frames, provider=provider, budget=budget, reason="after_repair")
        self.assertEqual(len(provider.calls), 1)
        with self.assertRaises(VisualReviewInvalid):
            observe_frames(request(budget=1), frames, provider=provider, budget=budget, reason="after_repair")

    def test_a_deterministic_loop_states_its_zero_allowance_and_is_refused_at_admission(self):
        provider, budget = CountingProvider(), VisualReviewBudget.for_task(TaskClass.DETERMINISTIC_EDIT)
        frames = [model_view_frame(view_answer("top")), model_view_frame(view_answer("front"))]
        with self.assertRaises(VisualBudgetRefused) as refused:
            observe_frames(request(budget=0), frames, provider=provider, budget=budget, reason="first_bundle")
        self.assertEqual(refused.exception.code, "VISUAL_REVIEW_NOT_WARRANTED")
        self.assertEqual((provider.calls, budget.used), ([], 0))
        with self.assertRaises(VisualReviewInvalid):
            request(budget=5)

    def test_a_carried_allowance_resumes_only_at_the_policy_value(self):
        """A stateless caller holds the loop; it can hand the state back, never raise the allowance."""

        budget = VisualReviewBudget.for_task(TaskClass.SPATIAL_FORMAL)
        budget.admit(ReviewReason.FIRST_BUNDLE)
        budget.settle(parse_observation(GOOD, request(), [model_view_frame(view_answer("top"))], review_index=1))
        carried = budget.to_dict()
        self.assertEqual(carried, {"taskClass": "spatial_formal", "allowed": 2, "used": 1, "lastFindingIds": ["f1"]})
        resumed = VisualReviewBudget.resume(carried["taskClass"], allowed=carried["allowed"], used=carried["used"],
                                            last_findings=carried["lastFindingIds"])
        self.assertEqual(resumed.to_dict(), carried)
        self.assertIsNotNone(resumed.refusal(ReviewReason.AFTER_REPAIR))
        resumed.note_repair(["f1"])
        self.assertEqual(resumed.admit(ReviewReason.AFTER_REPAIR), 2)
        self.assertEqual(resumed.to_dict()["lastFindingIds"], [])
        polish = VisualReviewBudget.resume("polish", allowed=3, used=0)
        self.assertEqual(polish.admit(ReviewReason.POLISH_ROUND), 1)
        exhausted = VisualReviewBudget.resume("spatial_formal", allowed=2, used=3)
        self.assertEqual(exhausted.refusal(ReviewReason.FIRST_BUNDLE).code, "VISUAL_BUDGET_EXHAUSTED")
        refused = {
            "a raised spatial allowance": dict(task_class="spatial_formal", allowed=3, used=0),
            "a deterministic allowance": dict(task_class="deterministic_edit", allowed=1, used=0),
            "a polish round count past the cap": dict(task_class="polish", allowed=5, used=0),
            "an unknown task class": dict(task_class="glance", allowed=2, used=0),
            "a negative count": dict(task_class="spatial_formal", allowed=2, used=-1),
            "a boolean count": dict(task_class="spatial_formal", allowed=2, used=True),
            "findings before any review": dict(task_class="spatial_formal", allowed=2, used=0, last_findings=["f1"]),
            "a foreign finding id": dict(task_class="spatial_formal", allowed=2, used=1, last_findings=["entity:roof"]),
            "a repeated finding id": dict(task_class="spatial_formal", allowed=2, used=1, last_findings=["f1", "f1"]),
        }
        for name, state in refused.items():
            task_class = state.pop("task_class")
            with self.subTest(name), self.assertRaises(VisualReviewInvalid):
                VisualReviewBudget.resume(task_class, **state)


class ObservationContractTests(unittest.TestCase):
    frames = [model_view_frame(view_answer("top")), model_view_frame(view_answer("front", shade=0))]

    def test_a_valid_answer_is_bound_to_the_frames_actually_sent(self):
        observation = parse_observation(GOOD, request(), self.frames, review_index=1)
        self.assertEqual(observation.source_refs, (SourceRef.model(RUN, STATE, ASSET),))
        self.assertEqual(observation.view_refs, ("top", "front"))
        self.assertEqual(observation.frame_sha256, tuple(frame.sha256 for frame in self.frames))
        self.assertEqual(observation.observations[0].finding_id, "f1")
        self.assertEqual(observation.to_dict()["observations"][0]["evidenceRegion"]["viewRef"], "top")

    def test_answers_outside_the_contract_are_refused(self):
        finding = GOOD["observations"][0]
        broken = {
            "extra top-level field": {**GOOD, "verdict": "looks good"},
            "unknown type": {**GOOD, "observations": [{**finding, "type": "beauty"}]},
            "foreign target": {**GOOD, "observations": [{**finding, "target_refs": ["entity:roof"]}]},
            "confidence above one": {**GOOD, "observations": [{**finding, "confidence": 1.4}]},
            "boolean confidence": {**GOOD, "observations": [{**finding, "confidence": True}]},
            "unknown severity": {**GOOD, "observations": [{**finding, "severity": "fatal"}]},
            "region on an unsent view": {**GOOD, "observations": [{**finding, "evidence_region": {
                **finding["evidence_region"], "view_ref": "right"}}]},
            "inverted region": {**GOOD, "observations": [{**finding, "evidence_region": {
                **finding["evidence_region"], "x0": 0.8}}]},
            "empty description": {**GOOD, "observations": [{**finding, "description": " "}]},
            "too many questions": {**GOOD, "unresolved_questions": ["q"] * 5},
        }
        for name, answer in broken.items():
            with self.subTest(name), self.assertRaises(VisualObservationInvalid):
                parse_observation(answer, request(), self.frames, review_index=1)

    def test_an_answer_the_schema_admits_but_the_contract_refuses_keeps_its_cost(self):
        inverted = {**GOOD, "observations": [{**GOOD["observations"][0], "evidence_region": {
            **GOOD["observations"][0]["evidence_region"], "x0": 0.8}}]}
        provider, budget = CountingProvider(inverted), VisualReviewBudget.for_task(TaskClass.SPATIAL_FORMAL)
        with self.assertRaises(VisualProviderFailed) as failed:
            observe_frames(request(), self.frames, provider=provider, budget=budget, reason=ReviewReason.FIRST_BUNDLE)
        self.assertIsInstance(failed.exception.__cause__, VisualObservationInvalid)
        self.assertEqual((len(provider.calls), budget.used), (1, 1))
        self.assertEqual(failed.exception.usage.image_inputs, 2)
        self.assertEqual(failed.exception.usage.to_dict()["imageBytes"], sum(len(frame.png) for frame in self.frames))

    def test_known_readback_facts_are_bounded(self):
        with_facts = VisualReviewRequest(
            domain="modeling", source_refs=(SourceRef.model(RUN, STATE, ASSET),), view_recipe=("top",),
            task="Make the void feel generous.", criteria=(Criterion("void-generous", "The void dominates."),),
            known_facts=("Void clear width 3.90 m x 3.90 m.",) * 8)
        self.assertEqual(len(with_facts.known_facts), 8)
        for facts in (("fact",) * 9, ("x" * 121,), ("  ",)):
            with self.subTest(facts=facts[:1]), self.assertRaises(VisualReviewInvalid):
                VisualReviewRequest(domain="modeling", source_refs=(SourceRef.model(RUN, STATE, ASSET),),
                                    view_recipe=("top",), task="Look.", known_facts=facts,
                                    criteria=(Criterion("void-generous", "The void dominates."),))

    def test_an_observation_reopens_from_its_wire_form(self):
        observation = parse_observation(GOOD, request(), self.frames, review_index=2)
        self.assertEqual(VisualObservation.from_dict(observation.to_dict()), observation)
        with self.assertRaises(VisualObservationInvalid):
            VisualObservation.from_dict({"reviewId": "vr-1"})

    def test_schema_closes_refs_and_views_over_the_request(self):
        schema = observation_schema(request(), self.frames)
        item = schema["properties"]["observations"]["items"]
        self.assertEqual(item["properties"]["target_refs"]["items"]["enum"], ["criterion:void-generous", "preserve:1"])
        region = item["properties"]["evidence_region"]["anyOf"][1]
        self.assertEqual(region["properties"]["view_ref"]["enum"], ["top", "front"])
        self.assertEqual(set(item["required"]), set(item["properties"]))


class CodexTransportTests(unittest.TestCase):
    @contextmanager
    def codex(self, answer):
        calls = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(intent_agent, "_codex_version", return_value="visual-contract-test-1"))

            def run(command, prompt, timeout_s):
                images = [Path(command[i + 1]).read_bytes() for i, token in enumerate(command) if token == "--image"]
                schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text(encoding="utf-8"))
                calls.append({"command": command, "prompt": prompt, "images": images, "schema": schema})
                Path(command[command.index("-o") + 1]).write_text(json.dumps(answer), encoding="utf-8")
                event = json.dumps({"type": "turn.completed", "model": "test-reported-model", "usage": {
                    "input_tokens": 900, "cached_input_tokens": 300, "output_tokens": 70, "reasoning_output_tokens": 20}})
                return subprocess.CompletedProcess(command, 0, event, "")

            stack.enter_context(patch.object(intent_agent, "_run_bounded", side_effect=run))
            yield CodexCompiler(model="test-only-model", timeout_s=5), calls

    def test_exact_frames_reach_codex_and_the_cost_is_a_visual_observation_span(self):
        frames = [model_view_frame(view_answer("top")), model_view_frame(view_answer("front", shade=0))]
        with tempfile.TemporaryDirectory() as tmp, self.codex(GOOD) as (compiler, calls):
            store = UsageLog(Path(tmp) / "monitor")
            monitor = StudioMonitor(store)
            provider = StudioModelVisualProvider(MonitoredCompiler(compiler, monitor))
            budget = VisualReviewBudget.for_task(TaskClass.SPATIAL_FORMAL)
            result = observe_frames(request(), frames, provider=provider, budget=budget,
                             reason=ReviewReason.FIRST_BUNDLE, monitor=monitor, project_id="P")
            rows = [json.loads(line) for line in store.path.read_text(encoding="utf-8").splitlines()]
            log = store.path.read_text(encoding="utf-8")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["images"], [frame.png for frame in frames])
        self.assertEqual(calls[0]["schema"], observation_schema(request(), frames))
        self.assertIn("--ephemeral", calls[0]["command"])
        self.assertIn("criterion:void-generous", calls[0]["prompt"])
        self.assertEqual(result.observation.observations[0].severity, "major")
        self.assertEqual((result.usage.input_tokens, result.usage.cached_input_tokens, result.usage.output_tokens,
                          result.usage.image_inputs, result.usage.model), (900, 300, 70, 2, "test-reported-model"))
        visual = [row for row in rows if row["phase"] == "visual_observation" and row["status"] == "succeeded"]
        model = [row for row in rows if row["phase"] == "model_request"]
        self.assertEqual(len(visual), 1)
        self.assertEqual(visual[0]["details"]["request_kind"], "visual_observation")
        self.assertEqual(visual[0]["details"]["input_bytes"], sum(len(f.png) for f in frames))
        self.assertEqual(len(visual[0]["details"]["comparison_refs"]), 2)
        self.assertTrue(visual[0]["details"]["validator_pass"])
        self.assertEqual(model[0]["parent_event_id"], visual[0]["event_id"])
        self.assertEqual(model[0]["tokens"]["input_tokens"], 900)
        self.assertNotIn("Stair flights crowd", log)
        self.assertNotIn("Make the void feel generous", log)

    def test_codex_receipt_names_the_visual_observation_phase(self):
        frames = [model_view_frame(view_answer("top")), model_view_frame(view_answer("front"))]
        fact = "Void clear width 3.90 m by 3.90 m; flight F2 starts at 0.00."
        with self.codex(GOOD) as (compiler, calls):
            seen = []
            original = intent_agent.invoke_structured

            def spy(*args, **kwargs):
                output, receipt = original(*args, **kwargs)
                seen.append(receipt)
                return output, receipt

            with patch("archflow_studio_api.application.visual_observation.invoke_structured", side_effect=spy):
                with_facts = VisualReviewRequest(
                    domain="modeling", source_refs=(SourceRef.model(RUN, STATE, ASSET),), view_recipe=("top", "front"),
                    task="Make the void feel generous.", known_facts=(fact,),
                    criteria=(Criterion("void-generous", "The central void reads as the dominant open space."),),
                    budget=2)
                StudioModelVisualProvider(compiler).observe(with_facts, frames)
        self.assertIs(seen[0].request.phase, ModelPhase.VISUAL_OBSERVATION)
        self.assertEqual(seen[0].request.payload["frames"][0]["sha256"], frames[0].sha256)
        self.assertEqual(seen[0].request.payload["known_facts"], [fact])
        self.assertIn(fact, calls[0]["prompt"])

    def test_an_answer_outside_the_schema_is_a_failed_call_that_still_spent_its_review(self):
        bad = {**GOOD, "observations": [{**GOOD["observations"][0], "target_refs": ["entity:roof"]}]}
        frames = [model_view_frame(view_answer("top")), model_view_frame(view_answer("front"))]
        budget = VisualReviewBudget.for_task(TaskClass.SPATIAL_FORMAL)
        with self.codex(bad) as (compiler, calls), self.assertRaises(VisualProviderFailed) as failed:
            observe_frames(request(), frames, provider=StudioModelVisualProvider(compiler), budget=budget,
                    reason=ReviewReason.FIRST_BUNDLE)
        self.assertEqual(budget.used, 1)
        self.assertEqual(failed.exception.usage.input_tokens, 900)

    def test_the_deterministic_compiler_is_not_a_vision_provider(self):
        with self.assertRaises(VisualReviewInvalid):
            StudioModelVisualProvider(intent_agent.DeterministicCompiler())


if __name__ == "__main__":
    unittest.main()
