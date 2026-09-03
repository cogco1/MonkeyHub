from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.project.runtime import RuntimeConfigError, RuntimePaths, bootstrap_external_project, prepare_external_stage0
from archive.archflow.project.runtime import main as runtime_main
from archive.archflow.runtime.design_controller import (
    DesignControllerCheckpoint,
    DesignControllerError,
    ProjectControllerArchiveAdapter,
)
from archive.archflow.runtime.event_log import EventDecision
from archive.archflow.runtime.stage0_preparation import (
    Stage0Declaration,
    Stage0PreparationError,
    prepare_stage0_declaration,
)


def _record_payload(ref) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _branch_payload(run, branch_id: str = "main") -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "project_id": run.project_id,
        "run_id": run.run_id,
        "branch_id": branch_id,
        "epoch": 0,
        "base": {
            "project_id": run.base.project_id,
            "version": run.base.version,
            "state_sha256": run.base.require_digest(),
        },
    }


def _declaration_payload(
    run,
    evidence_ref,
    *,
    branch_id: str = "main",
) -> dict[str, object]:  # type: ignore[no-untyped-def]
    source_ref = evidence_ref.uri
    return {
        "schema": "Stage0Declaration@1",
        "branch": _branch_payload(run, branch_id),
        "phase": "research_brief",
        "root_node_id": "stage0-root",
        "allowed_authority_ids": ["architect"],
        "facts": [
            {
                "domain": "brief",
                "key": "requested-use",
                "value": "explicit-user-declaration",
                "source_ref": source_ref,
                "epistemic_status": "declared",
                "confidence": 1.0,
                "qualification": None,
            }
        ],
        "bindings": [
            {
                "key": "brief-source",
                "value": "external-json",
                "source_ref": source_ref,
            }
        ],
        "locks": [
            {
                "target_ref": "fact:brief:requested-use",
                "authority_id": "architect",
                "source_ref": source_ref,
            }
        ],
        "commitments": [],
        "obligations": [
            {
                "obligation_id": "verify-requested-use",
                "statement": "Verify the declared use before phase exit.",
                "source_ref": source_ref,
                "status": "open",
                "subject_refs": ["fact:brief:requested-use"],
                "validator_ref": "validator:brief-source",
                "condition": None,
                "blocked_by": [],
            }
        ],
        "dependencies": [
            {
                "upstream_ref": "fact:brief:requested-use",
                "downstream_ref": "obligation:verify-requested-use",
                "relation": "supports-verification",
                "source_ref": source_ref,
                "effect": "supports_only",
            }
        ],
        "invalidated_refs": [],
        "evidence_refs": [_record_payload(evidence_ref)],
        "actor_id": "stage0-preparer",
        "authority_id": "architect",
        "max_iterations": 8,
    }


class _AmbiguousCheckpointRepository:
    def __init__(self, inner):  # type: ignore[no-untyped-def]
        self.inner = inner
        self.injected = False

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        return getattr(self.inner, name)

    def put_json(  # type: ignore[no-untyped-def]
        self,
        *,
        run,
        destination,
        record_kind,
        payload,
    ):
        ref = self.inner.put_json(
            run=run,
            destination=destination,
            record_kind=record_kind,
            payload=payload,
        )
        if (
            not self.injected
            and str(record_kind).startswith("design-controller-")
        ):
            self.injected = True
            rival_payload = copy.deepcopy(dict(payload))
            rival_payload["checkpoint"]["max_iterations"] += 1
            rival = DesignControllerCheckpoint.from_dict(
                rival_payload["checkpoint"]
            )
            rival_payload["checkpoint"] = rival.to_dict()
            rival_payload["checkpoint_digest"] = rival.checkpoint_digest
            self.inner.put_json(
                run=run,
                destination=destination,
                record_kind=f"{record_kind}-rival",
                payload=rival_payload,
            )
        return ref


class Stage0PreparationTests(unittest.TestCase):
    def _paths(self, root: Path) -> RuntimePaths:
        return RuntimePaths(
            workspace_root=(root / "workspace").resolve(),
            cache_root=(root / "cache").resolve(),
            temp_root=(root / "temp").resolve(),
        )

    def _bootstrap(self, root: Path):  # type: ignore[no-untyped-def]
        paths = self._paths(root)
        result = bootstrap_external_project(
            paths,
            project_id="generic-stage0",
            prompt="Retain this exact external request.",
            run_id="run-001",
        )
        repository = FilesystemProjectRepository.open(
            paths.project(result.project_id)
        )
        return paths, result, repository

    def test_compiles_and_idempotently_replays_without_head_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _paths, bootstrap, repository = self._bootstrap(Path(temporary))
            before = repository.read_head()
            declaration = Stage0Declaration.from_dict(
                _declaration_payload(bootstrap.run, bootstrap.request)
            )

            first = prepare_stage0_declaration(repository, declaration)
            destination = PersistenceDestination(
                PersistenceArea.RUN_BRANCH,
                run_id=bootstrap.run.run_id,
                branch_id="main",
            )
            first_records = repository.list_json(
                run=bootstrap.run,
                destination=destination,
            )
            second = prepare_stage0_declaration(repository, declaration)
            second_records = repository.list_json(
                run=bootstrap.run,
                destination=destination,
            )

            self.assertEqual(first.to_dict(), second.to_dict())
            self.assertEqual(first_records, second_records)
            self.assertEqual(repository.read_head(), before)
            self.assertEqual(
                first.compilation.operational_state.to_dict()["schema"],
                "OperationalMarkovState@3",
            )
            self.assertEqual(len(first.compilation.tree.nodes), 1)
            self.assertEqual(
                first.compilation.event.decision,
                EventDecision.OBSERVED,
            )
            self.assertEqual(
                first.compilation.checkpoint.maturity.phase.value,
                "research_brief",
            )
            resumed = ProjectControllerArchiveAdapter(
                repository,
                branch=declaration.branch,
            ).load_latest_checkpoint()
            self.assertEqual(
                resumed.checkpoint,
                first.compilation.checkpoint,
            )
            self.assertEqual(resumed.event_chain, (first.compilation.event,))

            changed_payload = _declaration_payload(
                bootstrap.run,
                bootstrap.request,
            )
            changed_payload["max_iterations"] = 9
            changed = Stage0Declaration.from_dict(changed_payload)
            with self.assertRaisesRegex(
                Stage0PreparationError,
                "another initialization",
            ):
                prepare_stage0_declaration(repository, changed)
            self.assertEqual(
                repository.list_json(
                    run=bootstrap.run,
                    destination=destination,
                ),
                second_records,
            )

    def test_same_epoch_latest_checkpoint_ambiguity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _paths, bootstrap, repository = self._bootstrap(Path(temporary))
            declaration = Stage0Declaration.from_dict(
                _declaration_payload(bootstrap.run, bootstrap.request)
            )
            with self.assertRaisesRegex(
                DesignControllerError,
                "ambiguous latest checkpoint lineage",
            ):
                prepare_stage0_declaration(
                    _AmbiguousCheckpointRepository(repository),
                    declaration,
                )
            self.assertEqual(repository.read_head(), bootstrap.head)

    def test_nested_schema_and_cross_scope_evidence_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _paths, bootstrap, repository = self._bootstrap(Path(temporary))
            for collection, field, message in (
                ("facts", "qualification", "state fact schema drifted"),
                ("locks", "source_ref", "state lock schema drifted"),
                (
                    "obligations",
                    "blocked_by",
                    "design obligation schema drifted",
                ),
                (
                    "dependencies",
                    "effect",
                    "dependency edge schema drifted",
                ),
            ):
                with self.subTest(collection=collection):
                    malformed = _declaration_payload(
                        bootstrap.run,
                        bootstrap.request,
                    )
                    del malformed[collection][0][field]  # type: ignore[index]
                    with self.assertRaisesRegex(ValueError, message):
                        Stage0Declaration.from_dict(malformed)

            other_branch_ref = repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=bootstrap.run.run_id,
                    branch_id="other",
                ),
                record_kind="other-branch-evidence",
                payload={
                    "schema": "ExternalEvidence@1",
                    "project_id": bootstrap.project_id,
                    "run_id": bootstrap.run.run_id,
                    "branch_id": "other",
                    "branch_epoch": 0,
                },
            )
            declaration = Stage0Declaration.from_dict(
                _declaration_payload(bootstrap.run, other_branch_ref)
            )
            with self.assertRaisesRegex(
                Stage0PreparationError,
                "crosses the selected run or branch",
            ):
                prepare_stage0_declaration(repository, declaration)

            other_run = repository.create_run("run-002")
            other_run_ref = repository.put_json(
                run=other_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=other_run.run_id,
                ),
                record_kind="other-run-evidence",
                payload={
                    "schema": "ExternalEvidence@1",
                    "project_id": other_run.project_id,
                    "run_id": other_run.run_id,
                },
            )
            cross_run = Stage0Declaration.from_dict(
                _declaration_payload(bootstrap.run, other_run_ref)
            )
            with self.assertRaisesRegex(
                Stage0PreparationError,
                "crosses the selected run or branch",
            ):
                prepare_stage0_declaration(repository, cross_run)

            for branch_identity, message in (
                (
                    {"branch_id": "other", "branch_epoch": 0},
                    "another branch",
                ),
                (
                    {"branch_id": "main", "branch_epoch": 7},
                    "another branch epoch",
                ),
            ):
                with self.subTest(branch_identity=branch_identity):
                    run_level_ref = repository.put_json(
                        run=bootstrap.run,
                        destination=PersistenceDestination(
                            PersistenceArea.RUN_RECORD,
                            run_id=bootstrap.run.run_id,
                        ),
                        record_kind="branch-scoped-run-evidence",
                        payload={
                            "schema": "ExternalEvidence@1",
                            "project_id": bootstrap.project_id,
                            "run_id": bootstrap.run.run_id,
                            **branch_identity,
                        },
                    )
                    declaration = Stage0Declaration.from_dict(
                        _declaration_payload(bootstrap.run, run_level_ref)
                    )
                    with self.assertRaisesRegex(
                        Stage0PreparationError,
                        message,
                    ):
                        prepare_stage0_declaration(repository, declaration)

            run_scoped_input_ref = repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(PersistenceArea.INPUT),
                record_kind="run-scoped-project-input",
                payload={
                    "schema": "ExternalEvidence@1",
                    "project_id": bootstrap.project_id,
                    "run_id": "other-run",
                },
            )
            run_scoped_input = Stage0Declaration.from_dict(
                _declaration_payload(bootstrap.run, run_scoped_input_ref)
            )
            with self.assertRaisesRegex(
                Stage0PreparationError,
                "another run",
            ):
                prepare_stage0_declaration(repository, run_scoped_input)

            epochless_branch_ref = repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=bootstrap.run.run_id,
                    branch_id="main",
                ),
                record_kind="epochless-branch-evidence",
                payload={
                    "schema": "ExternalEvidence@1",
                    "project_id": bootstrap.project_id,
                    "run_id": bootstrap.run.run_id,
                    "branch_id": "main",
                },
            )
            epochless_branch = Stage0Declaration.from_dict(
                _declaration_payload(bootstrap.run, epochless_branch_ref)
            )
            with self.assertRaisesRegex(
                Stage0PreparationError,
                "requires branch_id and branch_epoch",
            ):
                prepare_stage0_declaration(repository, epochless_branch)

            boolean_epoch_ref = repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=bootstrap.run.run_id,
                    branch_id="main",
                ),
                record_kind="boolean-epoch-branch-evidence",
                payload={
                    "schema": "ExternalEvidence@1",
                    "project_id": bootstrap.project_id,
                    "run_id": bootstrap.run.run_id,
                    "branch_id": "main",
                    "branch_epoch": False,
                },
            )
            boolean_epoch = Stage0Declaration.from_dict(
                _declaration_payload(bootstrap.run, boolean_epoch_ref)
            )
            with self.assertRaisesRegex(
                Stage0PreparationError,
                "branch epoch is invalid",
            ):
                prepare_stage0_declaration(repository, boolean_epoch)

            boolean_version_ref = repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=bootstrap.run.run_id,
                ),
                record_kind="boolean-version-run-evidence",
                payload={
                    "schema": "ExternalEvidence@1",
                    "project_id": bootstrap.project_id,
                    "run_id": bootstrap.run.run_id,
                    "run_base": {
                        "project_id": bootstrap.project_id,
                        "version": False,
                        "state_sha256": bootstrap.run.base.require_digest(),
                    },
                },
            )
            boolean_version = Stage0Declaration.from_dict(
                _declaration_payload(bootstrap.run, boolean_version_ref)
            )
            with self.assertRaisesRegex(
                Stage0PreparationError,
                "run_base is invalid",
            ):
                prepare_stage0_declaration(repository, boolean_version)

            current_destination = PersistenceDestination(
                PersistenceArea.RUN_BRANCH,
                run_id=bootstrap.run.run_id,
                branch_id="main",
            )
            self.assertEqual(
                set(repository.list_json(
                    run=bootstrap.run,
                    destination=current_destination,
                )),
                {epochless_branch_ref, boolean_epoch_ref},
            )

    def test_selected_identity_and_historical_base_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, bootstrap, repository = self._bootstrap(Path(temporary))
            declaration_path = Path(temporary) / "stage0.json"
            declaration_path.write_text(
                json.dumps(
                    _declaration_payload(bootstrap.run, bootstrap.request)
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RuntimeConfigError,
                "project/run/base/branch/epoch",
            ):
                prepare_external_stage0(
                    paths,
                    project_id=bootstrap.project_id,
                    run_id=bootstrap.run.run_id,
                    branch_id="other",
                    epoch=0,
                    declaration_path=declaration_path,
                )

            promotion_run = repository.create_run("promotion-001")
            decision = repository.put_json(
                run=promotion_run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_REVIEW,
                    run_id=promotion_run.run_id,
                ),
                record_kind="promotion-decision",
                payload={
                    "schema": "PromotionDecision@1",
                    "status": "accepted",
                    "project_id": promotion_run.project_id,
                    "run_id": promotion_run.run_id,
                    "checked_state": {
                        "project_id": promotion_run.base.project_id,
                        "version": promotion_run.base.version,
                        "state_sha256": promotion_run.base.require_digest(),
                    },
                    "candidate_ref": (
                        "project://generic-stage0/candidate/head-advance"
                    ),
                },
            )
            prepared = repository.prepare_transition(
                run=promotion_run,
                expected=promotion_run.base,
                replacement_state={"phase": "concurrent-head-advance"},
                decision_receipt=decision,
            )
            repository.compare_and_swap(
                expected=prepared.expected,
                event=prepared.event,
                replacement=prepared.replacement,
            )
            declaration = Stage0Declaration.from_dict(
                _declaration_payload(bootstrap.run, bootstrap.request)
            )
            with self.assertRaisesRegex(
                Stage0PreparationError,
                "historical run base",
            ):
                prepare_stage0_declaration(repository, declaration)

    def test_runtime_cli_prepares_stage0(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, bootstrap, repository = self._bootstrap(root)
            config = root / "runtime.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "ArchFlowRuntimeConfig@1",
                        "workspace_root": paths.workspace_root.as_posix(),
                        "cache_root": paths.cache_root.as_posix(),
                        "temp_root": paths.temp_root.as_posix(),
                    }
                ),
                encoding="utf-8",
            )
            declaration = root / "stage0.json"
            declaration.write_text(
                json.dumps(
                    _declaration_payload(bootstrap.run, bootstrap.request)
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                status = runtime_main(
                    [
                        "--config",
                        str(config),
                        "--repository-root",
                        str(Path(__file__).resolve().parents[2]),
                        "prepare-stage0",
                        "--project-id",
                        bootstrap.project_id,
                        "--run-id",
                        bootstrap.run.run_id,
                        "--branch-id",
                        "main",
                        "--epoch",
                        "0",
                        "--declaration",
                        str(declaration),
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(payload["schema"], "Stage0PreparationResult@1")
            self.assertEqual(payload["phase"], "research_brief")
            self.assertTrue(payload["canonical_head_unchanged"])
            self.assertEqual(repository.read_head(), bootstrap.head)


if __name__ == "__main__":
    unittest.main()
