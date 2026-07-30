from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectVersionRef,
    RunRef,
    bootstrap_raw_request_project,
)
from archflow.runtime.probe_loader import (
    ProbeInputError,
    compile_probe_design_brief,
    load_probe_input_envelope,
    persist_compiled_design_brief,
)
from archflow.runtime.brief_compiler import BriefObservation
from archflow.state import (
    BriefClaimKind,
    BriefSlot,
    BriefSlotStatus,
    FactEpistemicStatus,
)


class ProbeLoaderTests(unittest.TestCase):
    def test_generic_loader_separates_request_material_and_run_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "case-a"
            bootstrap = bootstrap_raw_request_project(
                root,
                project_id="case-a",
                prompt="Create a public-use building.",
            )
            repository = FilesystemProjectRepository.open(root)
            artifact = repository.ingest(
                run=bootstrap.run,
                destination=PersistenceDestination(
                    PersistenceArea.OBJECT
                ),
                artifact_id="authorized-source",
                media_type="text/plain",
                source=io.BytesIO(b"authorized source material"),
            )
            repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(
                    PersistenceArea.INPUT
                ),
                record_kind="authorized-material",
                payload={
                    "schema": "AuthorizedMaterialInput@1",
                    "input_id": "source-001",
                    "authority_id": "authority.user",
                    "source_uri": "https://example.invalid/source",
                    "object_ref": artifact.uri,
                    "media_type": artifact.media_type,
                    "sha256": artifact.sha256,
                },
            )
            repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=bootstrap.run.run_id,
                ),
                record_kind="derived-brief",
                payload={
                    "schema": "DesignBrief@1",
                    "derived": True,
                },
            )

            envelope = load_probe_input_envelope(
                repository,
                run=bootstrap.run,
            )

            self.assertEqual(envelope.project_id, "case-a")
            self.assertEqual(
                envelope.raw_request.to_payload()["prompt"],
                "Create a public-use building.",
            )
            self.assertEqual(len(envelope.materials), 1)
            self.assertEqual(
                envelope.materials[0].to_payload()["object_ref"],
                artifact.uri,
            )
            self.assertFalse(
                any(
                    item.schema == "DesignBrief@1"
                    for item in (
                        envelope.raw_request,
                        *envelope.materials,
                    )
                )
            )
            compiled = compile_probe_design_brief(
                repository,
                run=bootstrap.run,
                observations=(
                    BriefObservation(
                        observation_id="requested-use",
                        slot=BriefSlot.USE,
                        kind=BriefClaimKind.USER_FACT,
                        key="requested-use",
                        value="public use",
                        epistemic_status=FactEpistemicStatus.DECLARED,
                        authority_id="authority.user",
                        source_refs=(envelope.raw_request.ref.uri,),
                        resolves_slot=True,
                    ),
                ),
            )
            self.assertEqual(
                next(
                    item.status
                    for item in compiled.brief.slots
                    if item.slot is BriefSlot.USE
                ),
                BriefSlotStatus.SUPPORTED,
            )
            self.assertEqual(
                compiled.brief.raw_request_ref,
                envelope.raw_request.ref.uri,
            )
            persisted = persist_compiled_design_brief(
                repository,
                run=bootstrap.run,
                compiled=compiled,
            )
            for ref in (persisted.brief, persisted.receipt):
                self.assertTrue(
                    ref.relative_path.startswith(
                        "runs/bootstrap-001/records/"
                    )
                )
                self.assertFalse(ref.relative_path.startswith("input/"))
                self.assertEqual(
                    repository.load_json(ref)["project_id"],
                    "case-a",
                )

    def test_executable_and_binary_input_fail_closed(self) -> None:
        for name, content, expected in (
            ("derive.py", "print('answer')", "executable"),
            ("source.pdf", "not an object ref", "immutable objects"),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "case-a"
                bootstrap = bootstrap_raw_request_project(
                    root,
                    project_id="case-a",
                    prompt="Create a building.",
                )
                (root / "input" / name).write_text(
                    content,
                    encoding="utf-8",
                )
                repository = FilesystemProjectRepository.open(root)

                with self.assertRaisesRegex(ProbeInputError, expected):
                    load_probe_input_envelope(
                        repository,
                        run=bootstrap.run,
                    )

    def test_derived_record_cannot_masquerade_as_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "case-a"
            bootstrap = bootstrap_raw_request_project(
                root,
                project_id="case-a",
                prompt="Create a building.",
            )
            repository = FilesystemProjectRepository.open(root)
            repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(PersistenceArea.INPUT),
                record_kind="derived-output",
                payload={
                    "schema": "DesignBrief@1",
                    "generation_authority": False,
                },
            )

            with self.assertRaisesRegex(
                ProbeInputError,
                "derived or unsupported",
            ):
                load_probe_input_envelope(
                    repository,
                    run=bootstrap.run,
                )

    def test_nested_input_and_material_digest_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "case-a"
            bootstrap = bootstrap_raw_request_project(
                root,
                project_id="case-a",
                prompt="Create a building.",
            )
            nested = root / "input" / "nested"
            nested.mkdir()
            (nested / "ignored.json").write_text(
                '{"schema":"RawProjectRequest@1","prompt":"hidden"}',
                encoding="utf-8",
            )
            repository = FilesystemProjectRepository.open(root)
            with self.assertRaisesRegex(
                ProbeInputError,
                "nested input paths",
            ):
                load_probe_input_envelope(
                    repository,
                    run=bootstrap.run,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "case-a"
            bootstrap = bootstrap_raw_request_project(
                root,
                project_id="case-a",
                prompt="Create a building.",
            )
            repository = FilesystemProjectRepository.open(root)
            artifact = repository.ingest(
                run=bootstrap.run,
                destination=PersistenceDestination(
                    PersistenceArea.OBJECT
                ),
                artifact_id="authorized-source",
                media_type="text/plain",
                source=io.BytesIO(b"authorized source material"),
            )
            repository.put_json(
                run=bootstrap.run,
                destination=PersistenceDestination(PersistenceArea.INPUT),
                record_kind="bad-material-digest",
                payload={
                    "schema": "AuthorizedMaterialInput@1",
                    "input_id": "source-001",
                    "authority_id": "authority.user",
                    "source_uri": "https://example.invalid/source",
                    "object_ref": artifact.uri,
                    "media_type": artifact.media_type,
                    "sha256": "b" * 64,
                },
            )
            with self.assertRaisesRegex(
                ProbeInputError,
                "digest-mismatched",
            ):
                load_probe_input_envelope(
                    repository,
                    run=bootstrap.run,
                )

    def test_cross_project_path_escape_and_runs_fallback_reject(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "case-a"
            bootstrap = bootstrap_raw_request_project(
                root,
                project_id="case-a",
                prompt="Create a building.",
            )
            repository = FilesystemProjectRepository.open(root)
            with self.assertRaisesRegex(
                ValueError,
                "unsafe segment|escapes",
            ):
                repository.layout.resolve_relative("../outside.json")
            foreign = RunRef(
                project_id="case-b",
                run_id=bootstrap.run.run_id,
                base=ProjectVersionRef(
                    project_id="case-b",
                    version=bootstrap.run.base.version,
                    state_sha256=bootstrap.run.base.state_sha256,
                ),
            )
            with self.assertRaises(ProbeInputError):
                load_probe_input_envelope(repository, run=foreign)

        with tempfile.TemporaryDirectory() as temporary:
            fallback_root = Path(temporary) / ".runs" / "case-a"
            bootstrap = bootstrap_raw_request_project(
                fallback_root,
                project_id="case-a",
                prompt="Create a building.",
            )
            repository = FilesystemProjectRepository.open(fallback_root)
            with self.assertRaisesRegex(
                ProbeInputError,
                ".runs fallback",
            ):
                load_probe_input_envelope(
                    repository,
                    run=bootstrap.run,
                )


if __name__ == "__main__":
    unittest.main()
