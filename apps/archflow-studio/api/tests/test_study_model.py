"""Study model contracts; mocked transport tests do not claim provider performance.

Set ARCHFLOW_STUDY_LIVE=1 for the separately labelled opt-in real Codex run.
Only this repository's original CC0 synthetic drawing is sent by that test.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from archflow.contracts.canonical import canonical_digest
from archflow.ports.model import ModelInvocationReceipt, ModelInvocationStatus, ModelPhase
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import RESEARCH_EVIDENCE_LEDGER
from archflow_studio_api.application import intent_agent, study_model
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.intent_agent import CodexCompiler, AnthropicCompiler, DeterministicCompiler, IntentAgentFailed
from archflow_studio_api.application.monitoring import MonitoredCompiler, StudioMonitor
from archflow_studio_api.transport.errors import StudioError
from archflow_studio_api.transport.study import StudyResearchRequestDto
from archflow_studio_api.routes import study as study_routes
from monkeymonitor.store import UsageLog

from . import test_study_research as research_tests
from .study_fixture import fixture_evidence, fixture_png, fixture_research
from .support import PROJECT_ID


TRACE = {"traces": [{"kind": "void", "points": [[0.4, 0.1], [0.6, 0.1], [0.6, 0.9], [0.4, 0.9]], "confidence": 0.7}]}


class StudyModelTests(unittest.TestCase):
    setUp = research_tests.StudyResearchTests.setUp
    new_client = research_tests.StudyResearchTests.new_client
    upload = research_tests.StudyResearchTests.upload
    request = research_tests.StudyResearchTests.request
    save = research_tests.StudyResearchTests.save

    @contextmanager
    def transport(self, name="codex", output=None, *, raw=None, timeout=False, before_answer=None):
        """Replace only the external process/SDK, preserving production adapters."""
        calls = []
        output = TRACE if output is None else output
        raw = json.dumps(output) if raw is None else raw
        with ExitStack() as stack:
            if name == "codex":
                stack.enter_context(patch.object(intent_agent, "_codex_version", return_value="study-contract-test-1"))
                compiler = CodexCompiler(model="test-only-model", timeout_s=0.25)

                def run(command, prompt, timeout_s):
                    images = [Path(command[index + 1]).read_bytes() for index, token in enumerate(command) if token == "--image"]
                    calls.append({"prompt": prompt, "images": images,
                        "schema": json.loads(Path(command[command.index("--output-schema") + 1]).read_text(encoding="utf-8"))})
                    if before_answer:
                        before_answer()
                    if timeout:
                        raise subprocess.TimeoutExpired(command, timeout_s)
                    Path(command[command.index("-o") + 1]).write_text(raw, encoding="utf-8")
                    event = json.dumps({"type": "turn.completed", "model": "test-reported-model",
                        "usage": {"input_tokens": 12, "output_tokens": 8}})
                    return subprocess.CompletedProcess(command, 0, event, "")

                stack.enter_context(patch.object(intent_agent, "_run_bounded", side_effect=run))
            else:
                class FakeTimeout(Exception):
                    pass

                def create(**kwargs):
                    content = kwargs["messages"][0]["content"]
                    calls.append({"prompt": kwargs["system"] + "".join(row["text"] for row in content if row["type"] == "text"),
                        "images": [base64.b64decode(row["source"]["data"]) for row in content if row["type"] == "image"]})
                    if before_answer:
                        before_answer()
                    if timeout:
                        raise FakeTimeout("test timeout")
                    return SimpleNamespace(content=[SimpleNamespace(type="text", text=raw)],
                        usage={"input_tokens": 12, "output_tokens": 8}, model="test-reported-model")

                sdk = SimpleNamespace(APITimeoutError=FakeTimeout,
                    Anthropic=lambda **kwargs: SimpleNamespace(messages=SimpleNamespace(create=create)))
                stack.enter_context(patch.object(intent_agent, "_anthropic_sdk", return_value=(sdk, "study-contract-sdk-1")))
                compiler = AnthropicCompiler(model="test-only-model", timeout_s=0.25)
            yield compiler, calls

    def propose(self, compiler, previous, action="trace"):
        return study_model.propose_study(bound_project(self.client.app.state), compiler,
            study_id="synthetic-passage", expected_previous_ref=previous, action=action)

    def manual_empty(self):
        body = self.request()
        body["evidence"] = []
        body["research"] = None
        result = self.client.post("/api/studies", json=body)
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def test_exact_source_pixels_reach_both_transports_and_receipts_reopen(self):
        first = self.manual_empty()
        for provider in ("codex", "anthropic"):
            with self.subTest(provider=provider), self.transport(provider) as (compiler, calls):
                result = self.propose(compiler, first["ledgerRef"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(calls[0]["images"]), 1)
            with Image.open(BytesIO(calls[0]["images"][0])) as actual, Image.open(BytesIO(fixture_png())) as expected:
                self.assertEqual(actual.size, expected.size)
                self.assertEqual(actual.tobytes(), expected.tobytes())
            invocation = ModelInvocationReceipt.from_dict(result.payload["model_invocations"][-1])
            self.assertIs(invocation.request.phase, ModelPhase.RESEARCH)
            self.assertEqual(invocation.request.payload["ledger_ref"], first["ledgerRef"])
            self.assertEqual(invocation.request.payload["page_png_sha256"], hashlib.sha256(calls[0]["images"][0]).hexdigest())
            self.assertEqual(invocation.request.context_digest, canonical_digest(invocation.request.payload, ascii=False))
            self.assertEqual(invocation.output, TRACE)
            self.assertEqual(invocation.model_id, "test-reported-model")
            self.assertEqual(invocation.input_bytes, len(calls[0]["prompt"].encode("utf-8")) + len(calls[0]["images"][0]))
            self.assertEqual(result.payload["measurements"], [])
            self.assertTrue(all(row["origin"] == "machine" and row["status"] == "proposed" for row in result.payload["evidence"]))
            reopened = self.new_client().get("/api/studies/synthetic-passage")
            self.assertEqual(reopened.status_code, 200, reopened.text)
            self.assertEqual(reopened.json()["modelInvocations"][-1]["request"]["phase"], "research")
            self.assertEqual(reopened.json()["ledgerRef"], result.ref.uri)
            first = reopened.json()
        self.assertEqual(self.repository.read_head(), self.head)

    def test_model_cannot_forge_documentary_history_or_personal_preference(self):
        original = fixture_research()
        original["designPrior"].update(preference="保留安静的庭院", preferenceStatus="stated")
        first = self.save(research=original)
        output = StudyResearchRequestDto.model_validate(fixture_research(changed_context=True)).model_dump()
        output["historical_sources"] = [{"source_id": "invented", "citation": "Fabricated author quote", "url": "", "locator": "", "summary": ""}]
        with self.transport(output=output) as (compiler, _), self.assertRaises(StudioError) as caught:
            self.propose(compiler, first["ledgerRef"], "reason")
        self.assertEqual(caught.exception.code, "STUDY_MODEL_UNSOURCED_HISTORY")
        self.assertEqual(self.client.get("/api/studies/synthetic-passage").json()["ledgerRef"], first["ledgerRef"])
        output["historical_sources"] = []
        output["design_prior"].update(preference="The model prefers a public plaza", preference_status="stated")
        with self.transport(output=output) as (compiler, _):
            result = self.propose(compiler, first["ledgerRef"], "reason")
        self.assertEqual(result.payload["research"]["design_prior"]["preference"], "保留安静的庭院")
        self.assertEqual(result.payload["research"]["design_prior"]["preference_status"], "stated")
        output["design_prior"] = None
        with self.transport(output=output) as (compiler, _), self.assertRaises(StudioError) as caught:
            self.propose(compiler, result.ref.uri, "reason")
        self.assertEqual(caught.exception.code, "STUDY_MODEL_PREFERENCE_LOST")

    def test_unknown_preference_stays_unresolved_and_model_needs_confirmed_evidence(self):
        first = self.manual_empty()
        with self.transport() as (compiler, calls), self.assertRaises(StudioError) as caught:
            self.propose(compiler, first["ledgerRef"], "reason")
        self.assertEqual(caught.exception.code, "STUDY_CONFIRMED_EVIDENCE_REQUIRED")
        self.assertEqual(calls, [])
        confirmed = self.save(previous=first["ledgerRef"])
        output = StudyResearchRequestDto.model_validate(fixture_research(changed_context=True)).model_dump()
        output["design_prior"].update(preference="Invented preference", preference_status="stated")
        with self.transport(output=output) as (compiler, _):
            result = self.propose(compiler, confirmed["ledgerRef"], "reason")
        self.assertEqual(result.payload["research"]["design_prior"]["preference_status"], "unresolved")
        self.assertNotEqual(result.payload["research"]["design_prior"]["preference"], "Invented preference")

    def test_stale_revision_refuses_before_call_and_racing_save_leaves_no_model_write(self):
        first = self.save()
        latest = self.save(previous=first["ledgerRef"], research=fixture_research(changed_context=True))
        with self.transport() as (compiler, calls), self.assertRaises(StudioError) as caught:
            self.propose(compiler, first["ledgerRef"])
        self.assertEqual(caught.exception.code, "STUDY_STALE")
        self.assertEqual(calls, [])
        manual = {}

        def edit_while_calling():
            research = fixture_research(changed_context=True)
            research["question"] = "Manual edit that arrived while the provider was working"
            manual.update(self.save(previous=latest["ledgerRef"], research=research))

        with self.transport(before_answer=edit_while_calling) as (compiler, calls), self.assertRaises(StudioError) as caught:
            self.propose(compiler, latest["ledgerRef"])
        self.assertEqual(caught.exception.code, "STUDY_REVISION_STALE")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.client.get("/api/studies/synthetic-passage").json(), manual)
        self.assertEqual(manual["modelInvocations"], [])

    def test_unavailable_provider_is_explicit_and_manual_correction_still_works(self):
        first = self.manual_empty()
        with self.assertRaises(StudioError) as caught:
            self.propose(DeterministicCompiler(), first["ledgerRef"])
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.code, "STUDY_MODEL_UNAVAILABLE")
        corrected = self.save(previous=first["ledgerRef"])
        self.assertEqual(len(corrected["evidence"]), 4)
        self.assertEqual(corrected["modelInvocations"], [])

    def test_shared_malformed_and_timeout_paths_keep_research_failure_receipts(self):
        first = self.manual_empty()
        for provider in ("codex", "anthropic"):
            for kind in ("bad-json", "bad-schema", "timeout"):
                raw = "not JSON" if kind == "bad-json" else '{"traces": "not-an-array"}'
                with self.subTest(provider=provider, failure=kind), self.transport(provider, raw=raw, timeout=kind == "timeout") as (compiler, _):
                    with self.assertRaises(IntentAgentFailed) as caught:
                        self.propose(compiler, first["ledgerRef"])
                receipt = caught.exception.receipt
                self.assertEqual(receipt.status, ModelInvocationStatus.TIMEOUT if kind == "timeout" else ModelInvocationStatus.MALFORMED)
                self.assertIs(receipt.request.phase, ModelPhase.RESEARCH)
                self.assertIsNone(receipt.output)
                self.assertEqual(receipt.output_bytes, 0 if kind == "timeout" else len(raw.encode("utf-8")))
                self.assertEqual(self.client.get("/api/studies/synthetic-passage").json()["ledgerRef"], first["ledgerRef"])
        self.assertEqual(self.repository.read_head(), self.head)

    def test_cold_read_rejects_receipt_with_false_exact_evidence_binding(self):
        first = self.save()
        with self.transport() as (compiler, _):
            proposed = self.propose(compiler, first["ledgerRef"])
        payload = deepcopy(proposed.payload)
        row = payload["model_invocations"][0]
        row["request"]["payload"]["evidence"] = []
        # Even an internally self-consistent digest cannot substitute other
        # evidence for the exact archived input revision named by the call.
        row["request"]["context_digest"] = canonical_digest(row["request"]["payload"], ascii=False)
        binding = bound_project(self.client.app.state)
        corrupt = binding.repository.put_json(run=binding.load_run(payload["run_id"]),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=payload["run_id"]),
            record_kind=RESEARCH_EVIDENCE_LEDGER, payload=payload)
        answer = self.client.get("/api/studies/synthetic-passage", params={"ledgerRef": corrupt.uri})
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(answer.json()["code"], "STUDY_LEDGER_INVALID")

    def test_model_sees_retained_comparison_results_and_cannot_change_their_sources(self):
        first = self.save()
        other = self.save(study_id="comparison-source")
        research = fixture_research(changed_context=True)
        research["comparisons"] = [{"studies": [
            {"studyId": value["studyId"], "ledgerRef": value["ledgerRef"]} for value in (first, other)
        ]}]
        saved = self.save(research=research, previous=first["ledgerRef"])
        output = StudyResearchRequestDto.model_validate(research).model_dump()
        with self.transport(output=output) as (compiler, calls), patch.object(study_routes, "_compare_exact_revisions", side_effect=AssertionError("reuse archived comparison")):
            proposed = self.propose(compiler, saved["ledgerRef"], "reason")
        self.assertIn('"comparison_results"', calls[0]["prompt"])
        self.assertIn("aspect-correct-composition-compare@1", calls[0]["prompt"])
        self.assertIn(other["ledgerRef"], calls[0]["prompt"])
        receipt = proposed.payload["model_invocations"][-1]
        self.assertEqual(proposed.payload["research"]["comparison_results"],
                         receipt["request"]["payload"]["research"]["comparison_results"])
        self.assertEqual(proposed.payload["research"]["comparisons"], output["comparisons"])
        reopened = self.new_client().get("/api/studies/synthetic-passage")
        self.assertEqual(reopened.json()["research"]["comparisonResults"], saved["research"]["comparisonResults"])
        output["comparisons"] = []
        with self.transport(output=output) as (compiler, _), self.assertRaises(StudioError) as caught:
            self.propose(compiler, proposed.ref.uri, "reason")
        self.assertEqual(caught.exception.code, "STUDY_MODEL_COMPARISON_CHANGED")
        self.assertEqual(self.client.get("/api/studies/synthetic-passage").json()["ledgerRef"], proposed.ref.uri)

    def monitored_http_proposal(self, compiler, previous, action, store):
        monitor = StudioMonitor(store)
        self.client.app.state.monitor = monitor
        self.client.app.state.intent_compiler = MonitoredCompiler(compiler, monitor)
        return self.client.post("/api/studies/propose", json={
            "projectId": PROJECT_ID, "studyId": previous["studyId"],
            "expectedPreviousRef": previous["ledgerRef"], "action": action,
        }, headers={"x-monkey-operation": "11111111-1111-4111-8111-111111111111",
                    "x-monkey-parent": "22222222-2222-4222-8222-222222222222"})

    def assert_monitored_association(self, store, previous, action, *, service_status):
        events, warnings = store.read()
        self.assertFalse(warnings)
        models = [event for event in events if event.phase == "model_request"]
        services = [event for event in events if event.phase == f"study_{action}"]
        requests = [event for event in events if event.phase == "api_request"]
        self.assertEqual(len(models), 1, "A paid Study call must appear once in the configured UsageLog.")
        self.assertEqual(sum(event.model_call is True for event in events), 1)
        self.assertEqual(len(services), 1)
        self.assertEqual(len(requests), 1)
        model, service, request = models[0], services[0], requests[0]
        self.assertEqual(model.parent_event_id, service.event_id)
        self.assertEqual(service.parent_event_id, request.event_id)
        self.assertEqual(request.parent_event_id, "studio:client:22222222-2222-4222-8222-222222222222")
        for event in (model, service):
            self.assertEqual(event.operation_id, "studio:client:11111111-1111-4111-8111-111111111111")
            self.assertEqual(event.project_id, PROJECT_ID)
            self.assertEqual(event.run_id, previous["runId"])
            self.assertEqual(event.source_ref, previous["ledgerRef"])
            self.assertIsNotNone(event.ended_at)
            self.assertGreaterEqual(event.duration_ms, 0)
        self.assertGreaterEqual(service.duration_ms, model.duration_ms)
        self.assertTrue(model.model_call)
        self.assertEqual(model.timing_scope, "model_call")
        self.assertFalse(service.model_call)
        self.assertEqual(service.timing_scope, "service")
        self.assertEqual(service.status, service_status)
        self.assertTrue(all(count is None for count in service.tokens.to_dict().values()))
        return model

    def test_monitored_study_success_records_one_call_usage_and_private_free_lineage(self):
        research = fixture_research()
        research["question"] = "PRIVATE_STUDY_RESEARCH_QUESTION_29014"
        previous = self.save(research=research)
        for provider in ("codex", "anthropic"):
            for action in ("trace", "reason"):
                output = TRACE if action == "trace" else StudyResearchRequestDto.model_validate(research).model_dump()
                if action == "reason":
                    output["design_prior"]["statement"] = "PRIVATE_MODEL_OUTPUT_57261"
                store = UsageLog(self.root.parent / f"monitor-success-{provider}-{action}")
                with self.subTest(provider=provider, action=action), self.transport(provider, output=output) as (compiler, calls):
                    answer = self.monitored_http_proposal(compiler, previous, action, store)
                self.assertEqual(answer.status_code, 200, answer.text)
                self.assertEqual(len(calls), 1)
                event = self.assert_monitored_association(store, previous, action, service_status="succeeded")
                self.assertEqual(event.status, "succeeded")
                self.assertEqual(event.provider, provider)
                self.assertEqual(event.model, "test-reported-model")
                self.assertEqual(event.tokens.input_tokens, 12)
                self.assertEqual(event.tokens.output_tokens, 8)
                self.assertEqual(event.event_id, f"studio:model:{answer.json()['modelInvocations'][-1]['receiptId']}")
                self.assertTrue(event.details["success"])
                contents = store.path.read_text(encoding="utf-8")
                self.assertNotIn("PRIVATE_STUDY_RESEARCH_QUESTION_29014", contents)
                self.assertNotIn("PRIVATE_MODEL_OUTPUT_57261", contents)
                self.assertNotIn('"prompt":', contents)
                self.assertNotIn('"response":', contents)
                previous = answer.json()
        self.assertEqual(self.repository.read_head(), self.head)

    def test_monitored_study_timeout_and_malformed_record_call_without_ledger_write(self):
        previous = self.save()
        for provider in ("codex", "anthropic"):
            for failure in ("timeout", "malformed"):
                store = UsageLog(self.root.parent / f"monitor-failure-{provider}-{failure}")
                with self.subTest(provider=provider, failure=failure), self.transport(provider, raw="PRIVATE_MALFORMED_OUTPUT_68192", timeout=failure == "timeout") as (compiler, calls):
                    answer = self.monitored_http_proposal(compiler, previous, "trace", store)
                self.assertEqual(answer.status_code, 502, answer.text)
                self.assertEqual(answer.json()["code"], "INTENT_AGENT_FAILED")
                self.assertEqual(len(calls), 1)
                event = self.assert_monitored_association(store, previous, "trace", service_status="failed")
                self.assertEqual(event.provider, provider)
                self.assertFalse(event.details["success"])
                if failure == "timeout":
                    self.assertEqual(event.status, "failed")
                    self.assertIsNone(event.tokens.input_tokens)
                    self.assertIsNone(event.tokens.output_tokens)
                    self.assertIn("did not answer", answer.json()["detail"])
                else:
                    self.assertEqual(event.tokens.input_tokens, 12)
                    self.assertEqual(event.tokens.output_tokens, 8)
                    self.assertIn("not JSON", answer.json()["detail"])
                self.assertNotIn("PRIVATE_MALFORMED_OUTPUT_68192", store.path.read_text(encoding="utf-8"))
                self.assertEqual(self.client.get("/api/studies/synthetic-passage").json(), previous)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_study_diagnostic_append_failure_preserves_success_and_original_error_without_retry(self):
        previous = self.save()
        for provider in ("codex", "anthropic"):
            for outcome in ("success", "timeout", "malformed"):
                store = UsageLog(self.root.parent / f"monitor-broken-{provider}-{outcome}")
                raw = "ORIGINAL_PROVIDER_MALFORMED_77041" if outcome == "malformed" else None
                with self.subTest(provider=provider, outcome=outcome), self.transport(provider, raw=raw, timeout=outcome == "timeout") as (compiler, calls):
                    with patch.object(store, "append", side_effect=OSError("test diagnostic disk failure")) as append:
                        answer = self.monitored_http_proposal(compiler, previous, "trace", store)
                self.assertEqual(len(calls), 1, "Diagnostic failures must never resubmit a model request.")
                self.assertGreater(append.call_count, 0)
                if outcome == "success":
                    self.assertEqual(answer.status_code, 200, answer.text)
                    self.assertEqual(answer.json()["modelInvocations"][-1]["output"], TRACE)
                    self.assertNotEqual(answer.json()["ledgerRef"], previous["ledgerRef"])
                    previous = answer.json()
                else:
                    self.assertEqual(answer.status_code, 502, answer.text)
                    self.assertEqual(answer.json()["code"], "INTENT_AGENT_FAILED")
                    self.assertIn("did not answer" if outcome == "timeout" else "ORIGINAL_PROVIDER_MALFORMED_77041", answer.json()["detail"])
                    self.assertEqual(self.client.get("/api/studies/synthetic-passage").json(), previous)
        self.assertEqual(self.repository.read_head(), self.head)


@unittest.skipUnless(os.environ.get("ARCHFLOW_STUDY_LIVE") == "1", "Opt-in real provider run; synthetic CC0 source only")
class StudyLiveModelTests(unittest.TestCase):
    setUp = research_tests.StudyResearchTests.setUp
    new_client = research_tests.StudyResearchTests.new_client
    upload = research_tests.StudyResearchTests.upload
    request = research_tests.StudyResearchTests.request
    save = research_tests.StudyResearchTests.save

    def test_real_codex_trace_evidence_only_reason_then_challenge_prior(self):
        output = Path(tempfile.mkdtemp(prefix="study-live-result-")) / "responses.json"
        retained = {}

        def retain_response(name, response):
            retained[name] = {"statusCode": response.status_code, "body": response.json()}
            output.write_text(json.dumps(retained, ensure_ascii=False, indent=2), encoding="utf-8")

        print(json.dumps({"liveStudyResult": str(output), "source": "original CC0 synthetic fixture", "modelOverride": None}))
        self.client.app.state.intent_compiler = CodexCompiler(
            executable=os.environ.get("ARCHFLOW_STUDY_LIVE_CODEX", "codex.cmd" if os.name == "nt" else "codex"),
            timeout_s=240,
        )
        body = self.request()
        body.update(evidence=[], research=None)
        first = self.client.post("/api/studies", json=body)
        retain_response("initial", first)
        self.assertEqual(first.status_code, 201, first.text)
        trace = self.client.post("/api/studies/propose", json={"projectId": PROJECT_ID, "studyId": body["studyId"],
            "expectedPreviousRef": first.json()["ledgerRef"], "action": "trace"})
        retain_response("trace", trace)
        self.assertEqual(trace.status_code, 200, trace.text)
        traced = trace.json()
        self.assertTrue(traced["evidence"], "The real provider proposed no inspectable traces.")
        self.assertTrue(all(row["status"] == "proposed" and row["origin"] == "machine" for row in traced["evidence"]))
        self.assertEqual(traced["modelInvocations"][-1]["request"]["phase"], "research")
        self.assertEqual(self.new_client().get("/api/studies/synthetic-passage").json(), traced)
        # Preserve all machine proposals as rejected; the authored manual
        # baseline is explicit test data, not a fabricated user endorsement.
        rejected = [{"evidenceId": row["evidenceId"], "kind": row["kind"], "points": row["geometry"]["points"],
                     "status": "rejected", "origin": "machine", "confidence": row["confidence"]} for row in traced["evidence"]]
        # Initial model reasoning gets no authored competing explanations,
        # predictions, outcomes, pattern or prior. Only the independent manual
        # trace correction and this open research question are supplied.
        correction = self.request(previous=traced["ledgerRef"], research={
            "question": "Which geometric relationships are present in this orthographic drawing, what competing spatial explanations remain possible, and what evidence would distinguish them?",
        })
        correction["evidence"] = rejected + fixture_evidence()
        corrected = self.client.post("/api/studies", json=correction)
        retain_response("manualCorrection", corrected)
        self.assertEqual(corrected.status_code, 201, corrected.text)
        initial_research = corrected.json()["research"]
        self.assertEqual(initial_research["hypotheses"], [])
        self.assertEqual(initial_research["gaps"], [])
        self.assertEqual(initial_research["counterfactuals"], [])
        self.assertIsNone(initial_research["compositionPattern"])
        self.assertIsNone(initial_research["designPrior"])
        reason = self.client.post("/api/studies/propose", json={"projectId": PROJECT_ID, "studyId": body["studyId"],
            "expectedPreviousRef": corrected.json()["ledgerRef"], "action": "reason"})
        retain_response("reasonEvidenceOnly", reason)
        self.assertEqual(reason.status_code, 200, reason.text)
        reasoned = reason.json()
        research = reasoned["research"]
        self.assertGreaterEqual(len(research["hypotheses"]), 2)
        self.assertTrue(any(row["competesWith"] for row in research["hypotheses"]), "The first response did not name competing explanations.")
        self.assertTrue(research["gaps"])
        self.assertTrue(3 <= len(research["counterfactuals"]) <= 5)
        self.assertTrue(all(row["actual"] and row["actual"]["status"] == "computed" for row in research["counterfactuals"]))
        self.assertIsNotNone(research["compositionPattern"])
        self.assertIsNotNone(research["designPrior"])
        self.assertEqual(research["designPrior"]["preferenceStatus"], "unresolved")
        self.assertEqual(self.new_client().get("/api/studies/synthetic-passage").json(), reasoned)

        # Retain an independently registered, separately authored CC0 variant.
        # This calls the existing comparison route through Study save, not a
        # provider, so the live sequence still contains exactly three calls.
        variant_document = self.upload("contracted")
        variant_request = self.request(study_id="synthetic-contracted", document=variant_document,
            research={"question": "What changes when the right mass is uniformly contracted in the original synthetic fixture?"})
        variant_request["evidence"] = fixture_evidence("contracted")
        variant_response = self.client.post("/api/studies", json=variant_request)
        retain_response("independentContractedVariant", variant_response)
        self.assertEqual(variant_response.status_code, 201, variant_response.text)
        variant = variant_response.json()
        self.assertNotEqual(variant["source"]["assetSha256"], reasoned["source"]["assetSha256"])
        comparisons = [{"studies": [{"studyId": value["studyId"], "ledgerRef": value["ledgerRef"]}
                                     for value in (reasoned, variant)]}]
        input_keys = {field.alias or name for name, field in StudyResearchRequestDto.model_fields.items()}

        def editable_research(snapshot):
            result = {key: deepcopy(value) for key, value in snapshot.items() if key in input_keys}
            result["counterfactuals"] = [
                {key: value for key, value in row.items() if key != "actual"}
                for row in result["counterfactuals"]
            ]
            return result

        comparison_research = editable_research(research)
        comparison_research["comparisons"] = comparisons
        comparison_request = self.request(previous=reasoned["ledgerRef"], research=comparison_research)
        comparison_request["evidence"] = correction["evidence"]
        comparison_response = self.client.post("/api/studies", json=comparison_request)
        retain_response("comparisonRetained", comparison_response)
        self.assertEqual(comparison_response.status_code, 201, comparison_response.text)
        compared = comparison_response.json()
        comparison_results = compared["research"]["comparisonResults"]
        self.assertEqual(len(comparison_results), 1)
        self.assertEqual(self.new_client().get("/api/studies/synthetic-passage").json(), compared)

        # A second, explicitly authored test scenario negates one condition of
        # the model's own prior. Preserve its exact first-round actual results
        # as input evidence; this is a synthetic challenge, not user preference
        # or proof that the second response is better than the first.
        prior = research["designPrior"]
        self.assertTrue(prior["conditions"], "A transferable prior needs an explicit applicability condition.")
        condition = prior["conditions"][0]
        changed_conditions = [f"In a second hypothetical project, this first-round applicability condition is explicitly false: {condition}"]
        challenged_research = editable_research(compared["research"])
        challenged_research["question"] = (
            "Revisit the first-round design prior using its already measured counterfactual actual results "
            "and the retained exact-revision comparison with synthetic-contracted. Distinguish shared topology from proportion changes; "
            "explain whether that comparison supports or limits transfer of the prior, or why it is not relevant. "
            + changed_conditions[0]
            + " This is an authored synthetic transfer scenario, not new documentary evidence or a user's preference. "
            "Choose retain, revise, reject or unresolved according to the evidence and explain your reasoning; no judgment is prescribed. "
            "Preserve supported geometric observations and unresolved alternatives. "
            "In changed_context.reason cite at least one exact counterfactual_id and an actual metric or relation, "
            "and explain what it does and does not support. Do not present a new prediction as an observed result."
        )
        challenged_research["designPrior"]["changedContext"] = {
            "changedConditions": changed_conditions, "decision": "unresolved", "reason": "", "revisedStatement": "",
        }
        challenge = self.request(previous=compared["ledgerRef"], research=challenged_research)
        challenge["evidence"] = correction["evidence"]
        challenged = self.client.post("/api/studies", json=challenge)
        retain_response("authoredContextChallenge", challenged)
        self.assertEqual(challenged.status_code, 201, challenged.text)
        self.assertEqual(
            [row["actual"] for row in challenged.json()["research"]["counterfactuals"]],
            [row["actual"] for row in research["counterfactuals"]],
        )
        revision = self.client.post("/api/studies/propose", json={"projectId": PROJECT_ID, "studyId": body["studyId"],
            "expectedPreviousRef": challenged.json()["ledgerRef"], "action": "reason"})
        retain_response("reasonAfterActualAndChangedContext", revision)
        self.assertEqual(revision.status_code, 200, revision.text)
        revised = revision.json()
        revised_prior = revised["research"]["designPrior"]
        self.assertIsNotNone(revised_prior)
        context = revised_prior["changedContext"]
        self.assertIsNotNone(context)
        self.assertIn(context["decision"], {"retain", "revise", "reject", "unresolved"})
        self.assertTrue(context["reason"].strip())
        if context["decision"] == "revise":
            self.assertTrue(context["revisedStatement"].strip())
        first_round_counterfactual_cited = any(row["counterfactualId"] in context["reason"] for row in research["counterfactuals"])
        last_request_research = revised["modelInvocations"][-1]["request"]["payload"]["research"]
        self.assertEqual(last_request_research["comparisons"], comparisons)
        self.assertEqual(last_request_research["comparisonResults"], comparison_results)
        self.assertEqual(revised["research"]["comparisons"], comparisons)
        self.assertEqual(revised["research"]["comparisonResults"], comparison_results)
        self.assertEqual(len(revised["modelInvocations"]), 3)
        self.assertEqual(revised_prior["preferenceStatus"], "unresolved")
        self.assertEqual(self.new_client().get("/api/studies/synthetic-passage").json(), revised)
        self.assertEqual(self.new_client().get("/api/studies/synthetic-passage", params={"ledgerRef": reasoned["ledgerRef"]}).json(), reasoned)
        self.assertEqual(self.repository.read_head(), self.head)
        print(json.dumps({"liveStudyResult": str(output), "traceCount": len(traced["evidence"]),
            "evidenceOnlyHypothesisCount": len(research["hypotheses"]),
            "evidenceOnlyCounterfactualCount": len(research["counterfactuals"]), "changedContextDecision": context["decision"],
            "comparisonResultCount": len(comparison_results),
            "comparisonVariantCitedInPrior": "synthetic-contracted" in context["reason"],
            "firstRoundCounterfactualCitedInPrior": first_round_counterfactual_cited,
            "providers": [{"provider": row["providerId"], "model": row["modelId"], "durationMs": row["durationMs"]}
                          for row in revised["modelInvocations"]]}, ensure_ascii=False))
