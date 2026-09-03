from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.ports.model import (
    ModelInvocationReceipt,
    ModelInvocationStatus,
)
from archflow.contracts.canonical import canonical_digest
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archive.archflow.project import production_checkpoint as project_checkpoint_facade
from archive.archflow.project import production_transition as project_transition_facade
from archflow.production.provider_runtime import AuthorizedAsyncModelProvider
from archive.archflow.runtime import persistence as runtime_persistence
from archive.archflow.runtime.persistence import production_checkpoint as runtime_checkpoint
from archive.archflow.runtime.persistence import production_transition as runtime_transition
from archive.archflow.runtime.persistence.production_checkpoint import (
    ProductionCheckpointError,
    ProductionRunCheckpoint,
    checkpoint_destination,
    load_production_checkpoint,
    persist_production_checkpoint,
)
from archive.archflow.runtime.persistence.production_transition import (
    ProductionFailedAttemptReceipt,
    ProductionTransitionError,
    load_failed_production_attempts,
    load_production_transition,
    persist_compiled_production_transition,
    persist_failed_production_attempt,
    production_intent_digest,
)
from archflow.compilers.geometry import compile_geometry_program
from archive.archflow.runtime.semantic_geometry_lifecycle import (
    bind_initial_semantic_geometry,
    compile_semantic_geometry_lifecycle,
)
from tests.test_geometry_compiler import COMMITMENT
from tests.test_production_responsibility import (
    RESPONSIBILITY_ID,
    MODEL_IDENTITY,
    _ScriptedModelProvider,
    _active,
    _request,
)
from archive.tests.test_semantic_geometry_lifecycle import (
    _design_state,
    _geometry_proposal,
)


class _CrashBeforeCheckpoint:
    def __init__(self, repository: FilesystemProjectRepository) -> None:
        self.repository = repository
        self.crashed = False

    def put_json(self, **kwargs):  # type: ignore[no-untyped-def]
        destination = kwargs["destination"]
        if destination == checkpoint_destination(kwargs["run"]):
            self.crashed = True
            raise RuntimeError("simulated interruption before checkpoint")
        return self.repository.put_json(**kwargs)

    def load_json(self, ref):  # type: ignore[no-untyped-def]
        return self.repository.load_json(ref)

    def list_json(self, **kwargs):  # type: ignore[no-untyped-def]
        return self.repository.list_json(**kwargs)


def _bind_to_run(state, run):  # type: ignore[no-untyped-def]
    return replace(
        state,
        selected_schematic=replace(
            state.selected_schematic,
            project_id=run.project_id,
            run_id=run.run_id,
            base=run.base,
        ),
    )


def _compiled_transition(run):  # type: ignore[no-untyped-def]
    before = _bind_to_run(_design_state(0), run)
    after = _bind_to_run(_design_state(1), run)
    coarse = compile_geometry_program(
        before,
        _geometry_proposal(before, stage=0),
        active_commitment_refs=(COMMITMENT,),
    )
    assert coarse.program is not None
    result = compile_semantic_geometry_lifecycle(
        transaction_id="durable-dome-shell",
        predecessor_state=before,
        current_state=after,
        predecessor_proposal=before.selected_schematic.option.proposal,
        current_proposal=after.selected_schematic.option.proposal,
        prior_program=coarse.program,
        geometry_proposal=_geometry_proposal(
            after,
            coarse.program,
            stage=1,
        ),
        active_commitment_refs=(COMMITMENT,),
    )
    return after, result


def _compiled_initial(run):  # type: ignore[no-untyped-def]
    state = _bind_to_run(_design_state(0), run)
    compiled = compile_geometry_program(
        state,
        _geometry_proposal(state, stage=0),
        active_commitment_refs=(COMMITMENT,),
    )
    assert compiled.program is not None
    return state, bind_initial_semantic_geometry(
        transaction_id="initial-durable-dome",
        current_state=state,
        current_proposal=state.selected_schematic.option.proposal,
        geometry_program=compiled.program,
        source_refs=(state.selected_schematic.option.proposal.evidence_refs[0],),
    )


async def _failed_envelope(
    status: ModelInvocationStatus = ModelInvocationStatus.TIMEOUT,
):  # type: ignore[no-untyped-def]
    def receipt(request):  # type: ignore[no-untyped-def]
        return ModelInvocationReceipt(
            receipt_id=f"failed-{status.value}-receipt",
            status=status,
            request=request,
            provider_id=MODEL_IDENTITY.provider_id,
            model_id="scripted-model",
            provider_version=MODEL_IDENTITY.version,
            provider_fingerprint=MODEL_IDENTITY.fingerprint,
            input_bytes=100,
            output_bytes=0,
            output_sha256=None,
            duration_ms=120_000,
            error_code=f"model.{status.value}",
            message="bounded scripted provider failure",
        )

    envelopes = []
    await AuthorizedAsyncModelProvider(
        _active(_ScriptedModelProvider(receipt)),
        RESPONSIBILITY_ID,
        envelopes.append,
    ).invoke(_request())
    return envelopes[0]


async def _success_envelope():
    envelopes = []
    await AuthorizedAsyncModelProvider(
        _active(_ScriptedModelProvider()),
        RESPONSIBILITY_ID,
        envelopes.append,
    ).invoke(_request())
    return envelopes[0]


class ProductionTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.repo = FilesystemProjectRepository.initialize(
            self.root,
            project_id="portfolio-project",
            initial_state={
                "schema": "CanonicalProjectState@1",
                "phase": "initialized",
                "authoritative_record_refs": [],
                "derived_record_refs": [],
            },
        )
        self.run = self.repo.create_run("run-001")
        self.intent = production_intent_digest(
            self.run,
            intent={
                "schema": "TestProductionIntent@1",
                "step_id": "dome-shell",
            },
        )

    def test_compiled_values_are_checkpointed_and_resume_without_replay(self):
        state, result = _compiled_transition(self.run)
        envelopes = []
        asyncio.run(
            AuthorizedAsyncModelProvider(
                _active(_ScriptedModelProvider()),
                RESPONSIBILITY_ID,
                envelopes.append,
            ).invoke(_request())
        )

        first = persist_compiled_production_transition(
            self.repo,
            run=self.run,
            intent_digest=self.intent,
            current_design_state=state,
            result=result,
            invocation_envelopes=tuple(envelopes),
        )

        self.assertFalse(first.resumed)
        self.assertEqual(5, len(first.records))
        reopened = FilesystemProjectRepository.open(self.root)
        resumed = load_production_transition(
            reopened,
            run=reopened.load_run("run-001"),
            intent_digest=self.intent,
        )
        assert resumed is not None
        self.assertTrue(resumed.resumed)
        self.assertEqual(first.checkpoint_ref, resumed.checkpoint_ref)
        self.assertEqual(first.checkpoint.record_refs, resumed.checkpoint.record_refs)

        duplicate = persist_compiled_production_transition(
            reopened,
            run=reopened.load_run("run-001"),
            intent_digest=self.intent,
            current_design_state=state,
            result=result,
        )
        self.assertTrue(duplicate.resumed)
        self.assertEqual(first.checkpoint_ref, duplicate.checkpoint_ref)

    def test_failed_attempt_reloads_and_retry_is_separately_identified(self):
        head_before = self.repo.read_head()
        first = persist_failed_production_attempt(
            self.repo,
            run=self.run,
            intent_digest=self.intent,
            step_id="dome-shell",
            error_code="production.compilation_failed",
            message="provider timed out before a valid proposal",
            invocation_envelopes=(asyncio.run(_failed_envelope()),),
        )
        second = persist_failed_production_attempt(
            self.repo,
            run=self.run,
            intent_digest=self.intent,
            step_id="dome-shell",
            error_code="production.compilation_failed",
            message="provider exited before a valid proposal",
            invocation_envelopes=(
                asyncio.run(_failed_envelope(ModelInvocationStatus.EXIT_ERROR)),
            ),
        )

        reopened = FilesystemProjectRepository.open(self.root)
        attempts = load_failed_production_attempts(
            reopened,
            run=reopened.load_run(self.run.run_id),
            intent_digest=self.intent,
            step_id="dome-shell",
        )

        self.assertEqual((0, 1), tuple(item.receipt.attempt_index for item in attempts))
        self.assertEqual(first.ref.uri, second.receipt.retry_of_ref)
        self.assertEqual(second.ref, attempts[1].ref)
        self.assertEqual(head_before, reopened.read_head())
        self.assertIsNone(
            load_production_transition(
                reopened,
                run=reopened.load_run(self.run.run_id),
                intent_digest=self.intent,
            )
        )
        payload = attempts[0].receipt.to_dict()
        self.assertIsNone(payload["transition_checkpoint_ref"])
        self.assertFalse(payload["lifecycle_successor"])
        self.assertFalse(payload["fallback_used"])
        self.assertFalse(payload["canonical_write_authority"])
        provider_receipt = attempts[0].receipt.invocation_envelopes[0][
            "provider_receipt_json"
        ]
        self.assertIn('"duration_ms":120000', provider_receipt)
        self.assertIn('"status":"timeout"', provider_receipt)

    def test_failed_attempt_rejects_tampered_provider_evidence(self):
        archived = persist_failed_production_attempt(
            self.repo,
            run=self.run,
            intent_digest=self.intent,
            step_id="dome-shell",
            error_code="production.compilation_failed",
            message="provider timed out before a valid proposal",
            invocation_envelopes=(asyncio.run(_failed_envelope()),),
        )
        payload = archived.receipt.to_dict()
        payload["invocation_envelopes"][0]["provider_receipt_digest"] = "0" * 64

        with self.assertRaisesRegex(
            ProductionTransitionError,
            "provider digest",
        ):
            ProductionFailedAttemptReceipt.from_dict(payload)

    def test_rejected_attempt_retains_successful_provider_evidence(self):
        archived = persist_failed_production_attempt(
            self.repo,
            run=self.run,
            intent_digest=self.intent,
            step_id="dome-shell",
            error_code="production.compilation_failed",
            message="deterministic proposal validation rejected the output",
            invocation_envelopes=(asyncio.run(_success_envelope()),),
        )

        self.assertEqual("pipeline_rejected", archived.receipt.failure_class)
        self.assertIsNone(
            load_production_transition(
                self.repo,
                run=self.run,
                intent_digest=self.intent,
            )
        )
        reloaded = load_failed_production_attempts(
            FilesystemProjectRepository.open(self.root),
            run=self.run,
            intent_digest=self.intent,
            step_id="dome-shell",
        )
        self.assertEqual((archived.ref,), tuple(item.ref for item in reloaded))
        provider_receipt = reloaded[0].receipt.invocation_envelopes[0][
            "provider_receipt_json"
        ]
        self.assertIn('"status":"success"', provider_receipt)

    def test_completed_intent_cannot_gain_a_later_failed_attempt(self):
        state, result = _compiled_transition(self.run)
        persist_compiled_production_transition(
            self.repo,
            run=self.run,
            intent_digest=self.intent,
            current_design_state=state,
            result=result,
        )

        with self.assertRaisesRegex(
            ProductionTransitionError,
            "completed production intent",
        ):
            persist_failed_production_attempt(
                self.repo,
                run=self.run,
                intent_digest=self.intent,
                step_id="dome-shell",
                error_code="production.compilation_failed",
                message="contradictory late failure",
                invocation_envelopes=(asyncio.run(_failed_envelope()),),
            )

    def test_initial_binding_uses_the_same_p036_checkpoint_protocol(self):
        state, result = _compiled_initial(self.run)
        intent = production_intent_digest(
            self.run,
            intent={"schema": "TestProductionIntent@1", "step_id": "initial"},
        )

        archived = persist_compiled_production_transition(
            self.repo,
            run=self.run,
            intent_digest=intent,
            current_design_state=state,
            result=result,
        )

        receipt = next(
            item for item in archived.records if item.role.value == "lifecycle-receipt"
        )
        self.assertEqual("InitialSemanticGeometryReceipt@1", receipt.content["schema"])
        self.assertEqual(result.receipt.receipt_digest, archived.transition_digest)

    def test_orphan_records_after_interruption_are_not_completion(self):
        state, result = _compiled_transition(self.run)
        crashing = _CrashBeforeCheckpoint(self.repo)

        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            persist_compiled_production_transition(
                crashing,
                run=self.run,
                intent_digest=self.intent,
                current_design_state=state,
                result=result,
            )
        self.assertTrue(crashing.crashed)
        self.assertIsNone(
            load_production_transition(
                self.repo,
                run=self.run,
                intent_digest=self.intent,
            )
        )

        recovered = persist_compiled_production_transition(
            self.repo,
            run=self.run,
            intent_digest=self.intent,
            current_design_state=state,
            result=result,
        )
        self.assertFalse(recovered.resumed)
        self.assertEqual(4, len(recovered.records))

    def test_exact_run_base_is_required_before_any_record_write(self):
        state, result = _compiled_transition(self.run)
        other_root = Path(self.temp.name) / "other-project"
        other = FilesystemProjectRepository.initialize(
            other_root,
            project_id="portfolio-project",
            initial_state={
                "schema": "CanonicalProjectState@1",
                "phase": "different-base",
                "authoritative_record_refs": [],
                "derived_record_refs": [],
            },
        )
        other_run = other.create_run("run-001")
        other_intent = production_intent_digest(
            other_run,
            intent={"schema": "TestProductionIntent@1", "step_id": "dome-shell"},
        )

        with self.assertRaisesRegex(ProductionTransitionError, "exact run base"):
            persist_compiled_production_transition(
                other,
                run=other_run,
                intent_digest=other_intent,
                current_design_state=state,
                result=result,
            )
        self.assertEqual((), other.list_json(
            run=other_run,
            destination=checkpoint_destination(other_run),
        ))

    def test_completed_intent_rejects_a_different_transition_before_writing(self):
        state, result = _compiled_transition(self.run)
        persisted = persist_compiled_production_transition(
            self.repo,
            run=self.run,
            intent_digest=self.intent,
            current_design_state=state,
            result=result,
        )
        contradictory = replace(
            result,
            receipt=replace(
                result.receipt,
                transaction_id="contradictory-dome-shell",
            ),
        )
        before_refs = persisted.checkpoint.record_refs

        with self.assertRaisesRegex(
            ProductionTransitionError,
            "contradictory lifecycle",
        ):
            persist_compiled_production_transition(
                self.repo,
                run=self.run,
                intent_digest=self.intent,
                current_design_state=state,
                result=contradictory,
            )
        loaded = load_production_transition(
            self.repo,
            run=self.run,
            intent_digest=self.intent,
        )
        assert loaded is not None
        self.assertEqual(before_refs, loaded.checkpoint.record_refs)


class ProductionCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = FilesystemProjectRepository.initialize(
            Path(self.temp.name) / "checkpoint-project",
            project_id="checkpoint-demo",
            initial_state={
                "schema": "CanonicalProjectState@1",
                "phase": "initialized",
                "authoritative_record_refs": [],
                "derived_record_refs": [],
            },
        )
        self.run = self.repo.create_run("run-001")
        self.destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=self.run.run_id,
        )

    def _record(self, name: str):  # type: ignore[no-untyped-def]
        return self.repo.put_json(
            run=self.run,
            destination=self.destination,
            record_kind=name,
            payload={"schema": "TestRecord@1", "name": name},
        )

    def test_runtime_owner_preserves_facade_identity_and_digests(self) -> None:
        for name in project_checkpoint_facade.__all__:
            self.assertIs(
                getattr(project_checkpoint_facade, name),
                getattr(runtime_checkpoint, name),
            )
        for name in project_transition_facade.__all__:
            self.assertIs(
                getattr(project_transition_facade, name),
                getattr(runtime_transition, name),
            )
        self.assertIs(
            runtime_persistence.ProductionRunCheckpoint,
            ProductionRunCheckpoint,
        )
        self.assertIs(
            runtime_persistence.ProductionFailedAttemptReceipt,
            ProductionFailedAttemptReceipt,
        )

        record = ProjectRecordRef(
            project_id="checkpoint-demo",
            relative_path=(
                "runs/run-001/records/design-state-" + "a" * 64 + ".json"
            ),
            sha256="a" * 64,
        )
        checkpoint = ProductionRunCheckpoint(
            project_id="checkpoint-demo",
            run_id="run-001",
            sequence=0,
            intent_digest="b" * 64,
            transition_digest="c" * 64,
            record_refs=(record,),
        )
        self.assertEqual(
            checkpoint.SCHEMA,
            "ProductionRunCheckpoint@2",
        )
        self.assertEqual(
            canonical_digest(checkpoint.to_dict()),
            "93931eb17fa010ac1f2dd1557391b4003c5716b180ea046bc8c40775b1953b3a",
        )
        fixed_run = RunRef(
            project_id="checkpoint-demo",
            run_id="run-001",
            base=ProjectVersionRef(
                project_id="checkpoint-demo",
                version=0,
                state_sha256="d" * 64,
            ),
        )
        self.assertEqual(
            production_intent_digest(
                fixed_run,
                intent={
                    "schema": "TestProductionIntent@1",
                    "step_id": "dome-shell",
                },
            ),
            "399eefd38a091e07a82261f328f8799f4a15630e968e42138ecc099282fc94bd",
        )

    def test_p036_checkpoint_is_reloadable_and_idempotent(self) -> None:
        record = self._record("semantic-geometry")
        intent = hashlib.sha256(b"intent-1").hexdigest()
        digest = hashlib.sha256(b"transition-1").hexdigest()
        first = persist_production_checkpoint(
            self.repo,
            run=self.run,
            intent_digest=intent,
            transition_digest=digest,
            record_refs=(record,),
        )
        second = persist_production_checkpoint(
            self.repo,
            run=self.run,
            intent_digest=intent,
            transition_digest=digest,
            record_refs=(record,),
        )
        self.assertFalse(first[2])
        self.assertTrue(second[2])
        self.assertEqual(first[:2], second[:2])
        reopened = FilesystemProjectRepository.open(
            Path(self.temp.name) / "checkpoint-project"
        )
        self.assertEqual(
            first[:2],
            load_production_checkpoint(
                reopened,
                reopened.load_run("run-001"),
            ),
        )
        self.assertFalse(first[1].to_dict()["canonical_write_authority"])

    def test_orphan_run_record_is_not_a_checkpoint(self) -> None:
        self._record("orphan-before-crash")
        self.assertIsNone(load_production_checkpoint(self.repo, self.run))

    def test_same_digest_cannot_name_contradictory_records(self) -> None:
        one, two = self._record("one"), self._record("two")
        intent = hashlib.sha256(b"intent").hexdigest()
        digest = hashlib.sha256(b"transition").hexdigest()
        persist_production_checkpoint(
            self.repo,
            run=self.run,
            intent_digest=intent,
            transition_digest=digest,
            record_refs=(one,),
        )
        with self.assertRaisesRegex(ProductionCheckpointError, "contradictory"):
            persist_production_checkpoint(
                self.repo,
                run=self.run,
                intent_digest=intent,
                transition_digest=digest,
                record_refs=(two,),
            )


if __name__ == "__main__":
    unittest.main()
