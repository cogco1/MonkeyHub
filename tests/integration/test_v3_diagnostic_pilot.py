from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.adapters.v3_legacy_cli import (
    V3LegacyCliBridge,
    V3LegacyProviderSpec,
)
from archflow.capabilities.experts import (
    ExpertAdvice,
    ExpertEvidence,
    ExpertRegistry,
    ExpertSnapshot,
    ExpertSpec,
)
from archflow.capabilities.v3_diagnostic import (
    CAPABILITY_ID,
    COMPONENT_GRAPH_EVIDENCE_KIND,
    EXPERT_ID,
    V3DiagnosticError,
    V3DiagnosticInput,
    V3DiagnosticStatus,
    V3LoadPathDiagnostic,
    register_v3_load_path_capability,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectVersionRef,
)
from archflow.state import CanonicalState, Obligation


ROOT = Path(__file__).resolve().parents[2]
V3_ROOT = Path("D:/ARCHFLOW_V3")
V3_PYTHON = V3_ROOT / (
    ".venv/Scripts/python.exe"
    if sys.platform == "win32"
    else ".venv/bin/python"
)
PROVIDER = (
    ROOT
    / "tests"
    / "fixtures"
    / "v3_legacy"
    / "v3_load_path_provider.py"
)
V3_FINGERPRINT = (
    "93370f2808db3303579a819798a0a9e475d06f4bfc000c9fe7e41429deec29c9"
)
PROBES = (
    ROOT / "probes" / "p013-diagnostic-control",
    ROOT / "probes" / "p013-diagnostic-contrast",
)


def _provider_spec(
    *,
    command: tuple[str, ...] | None = None,
) -> V3LegacyProviderSpec:
    return V3LegacyProviderSpec(
        provider_id="provider.v3-load-path-pilot",
        provider_version="v3-load-path-pilot-1",
        capability_id=CAPABILITY_ID,
        v3_fingerprint=V3_FINGERPRINT,
        command=command
        or (
            str(V3_PYTHON),
            str(PROVIDER),
            str(V3_ROOT),
            V3_FINGERPRINT,
        ),
        timeout_seconds=5,
    )


def _records(
    probe: Path,
) -> tuple[
    FilesystemProjectRepository,
    dict[str, object],
    dict[str, object],
]:
    repository = FilesystemProjectRepository.open(probe)
    run = repository.load_run("pilot-001")
    inputs = [
        repository.load_json(ref)
        for ref in repository.list_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.INPUT),
        )
    ]
    records = [
        repository.load_json(ref)
        for ref in repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )
    ]
    case = next(
        item
        for item in inputs
        if item.get("schema") == "V3DiagnosticProbeCase@1"
    )
    evidence = next(
        item
        for item in records
        if item.get("schema") == "V3DiagnosticProbeEvidence@1"
    )
    return repository, case, evidence


def _snapshot(
    case: dict[str, object],
    diagnostic_input: V3DiagnosticInput,
    *,
    topic: str | None = None,
    include_graph: bool = True,
) -> ExpertSnapshot:
    obligation = case["obligation"]
    state = CanonicalState(
        ref=diagnostic_input.base,
        open_obligations=(
            Obligation(
                obligation_id=obligation["obligation_id"],
                statement=obligation["statement"],
                source_ref=obligation["source_ref"],
            ),
        ),
    )
    evidence = (
        (
            ExpertEvidence(
                kind=COMPONENT_GRAPH_EVIDENCE_KIND,
                evidence_ref=diagnostic_input.evidence_ref,
                summary="Detached neutral component graph.",
            ),
        )
        if include_graph
        else ()
    )
    return ExpertSnapshot.detach(
        state,
        obligation_topics={
            obligation["obligation_id"]: topic or obligation["topic"]
        },
        evidence=evidence,
    )


def _actual_invariant_projection(
    receipt,
) -> dict[str, object]:
    invariants = receipt.invariants
    assert invariants is not None
    return {
        "all_load_sources_grounded": (
            invariants.all_load_sources_grounded
        ),
        "hard_finding_count": invariants.hard_finding_count,
        "finding_codes": list(invariants.finding_codes),
        "unsupported_component_ids": list(
            invariants.unsupported_component_ids
        ),
        "support_paths": {
            item.source_id: list(item.path)
            for item in invariants.support_paths
        },
    }


class V3DiagnosticPilotTests(unittest.TestCase):
    def test_two_building_cases_match_named_semantic_invariants(self) -> None:
        if not V3_PYTHON.is_file():
            self.skipTest("explicit frozen V3 interpreter is unavailable")
        diagnostic = V3LoadPathDiagnostic(
            V3LegacyCliBridge(_provider_spec())
        )
        origin_relations = set()

        for probe in PROBES:
            with self.subTest(probe=probe.name):
                _, case, persisted = _records(probe)
                diagnostic_input = V3DiagnosticInput.from_dict(
                    case["diagnostic_input"]
                )
                snapshot = _snapshot(case, diagnostic_input)
                receipt = diagnostic.diagnose(
                    snapshot,
                    diagnostic_input,
                    obligation_id=case["obligation"]["obligation_id"],
                )

                self.assertIs(
                    receipt.status,
                    V3DiagnosticStatus.OBSERVATION,
                )
                self.assertEqual(
                    _actual_invariant_projection(receipt),
                    case["expected_semantic_invariants"],
                )
                self.assertEqual(
                    persisted["semantic_comparison"]["mode"],
                    "named_invariants",
                )
                self.assertTrue(
                    persisted["semantic_comparison"]["matched"]
                )
                self.assertEqual(
                    persisted["diagnostic_receipt"]["input_sha256"],
                    diagnostic_input.input_sha256,
                )
                origin_relations.add(case["origin_relation"])

        self.assertEqual(
            origin_relations,
            {"control", "non_origin_typology"},
        )

    def test_discovery_uses_current_obligation_and_evidence_not_order(
        self,
    ) -> None:
        _, case, _ = _records(PROBES[0])
        diagnostic_input = V3DiagnosticInput.from_dict(
            case["diagnostic_input"]
        )
        diagnostic = V3LoadPathDiagnostic(
            V3LegacyCliBridge(_provider_spec())
        )
        snapshot = _snapshot(case, diagnostic_input)

        def resolver(_snapshot):
            return (
                diagnostic_input,
                case["obligation"]["obligation_id"],
            )

        def harmless(_snapshot):
            return ExpertAdvice(summary="Detached comparison advice.")

        other = ExpertSpec(
            expert_id="expert.other_support",
            description="Another optional support observation.",
            topics=frozenset({"load_path"}),
            required_evidence_kinds=frozenset(
                {COMPONENT_GRAPH_EVIDENCE_KIND}
            ),
        )
        first = ExpertRegistry()
        register_v3_load_path_capability(first, diagnostic, resolver)
        first.register(other, harmless)
        second = ExpertRegistry()
        second.register(other, harmless)
        register_v3_load_path_capability(second, diagnostic, resolver)

        first_ids = tuple(item.expert_id for item in first.discover(snapshot))
        second_ids = tuple(
            item.expert_id for item in second.discover(snapshot)
        )
        self.assertEqual(first_ids, second_ids)
        self.assertIn(EXPERT_ID, first_ids)
        self.assertNotIn(
            EXPERT_ID,
            tuple(
                item.expert_id
                for item in first.discover(
                    _snapshot(
                        case,
                        diagnostic_input,
                        topic="facade",
                    )
                )
            ),
        )
        self.assertNotIn(
            EXPERT_ID,
            tuple(
                item.expert_id
                for item in first.discover(
                    _snapshot(
                        case,
                        diagnostic_input,
                        include_graph=False,
                    )
                )
            ),
        )

    def test_exact_base_digest_and_detached_authority(self) -> None:
        _, case, _ = _records(PROBES[0])
        diagnostic_input = V3DiagnosticInput.from_dict(
            case["diagnostic_input"]
        )
        snapshot = _snapshot(case, diagnostic_input)
        stale = replace(
            diagnostic_input,
            base=ProjectVersionRef(
                diagnostic_input.base.project_id,
                diagnostic_input.base.version + 1,
                "b" * 64,
            ),
        )
        diagnostic = V3LoadPathDiagnostic(
            V3LegacyCliBridge(_provider_spec())
        )

        with self.assertRaisesRegex(
            V3DiagnosticError,
            "does not bind the expert base",
        ):
            diagnostic.diagnose(
                snapshot,
                stale,
                obligation_id=case["obligation"]["obligation_id"],
            )

        persisted = _records(PROBES[0])[2]["diagnostic_receipt"]
        for field in (
            "geometry_edit_authority",
            "hard_gate_waiver_authority",
            "aesthetic_winner_authority",
            "canonical_write_authority",
            "live_world_authority",
        ):
            self.assertFalse(persisted[field])

    def test_provider_failure_is_bounded_and_preserves_state(self) -> None:
        _, case, _ = _records(PROBES[0])
        diagnostic_input = V3DiagnosticInput.from_dict(
            case["diagnostic_input"]
        )
        snapshot = _snapshot(case, diagnostic_input)
        state_before = CanonicalState(
            ref=diagnostic_input.base,
            open_obligations=(
                Obligation(
                    obligation_id=case["obligation"]["obligation_id"],
                    statement=case["obligation"]["statement"],
                    source_ref=case["obligation"]["source_ref"],
                ),
            ),
        )
        diagnostic = V3LoadPathDiagnostic(
            V3LegacyCliBridge(
                _provider_spec(command=("missing-v3-load-path-provider",))
            )
        )

        receipt = diagnostic.diagnose(
            snapshot,
            diagnostic_input,
            obligation_id=case["obligation"]["obligation_id"],
        )

        self.assertIs(
            receipt.status,
            V3DiagnosticStatus.PROVIDER_FAILURE,
        )
        self.assertIsNone(receipt.invariants)
        self.assertEqual(receipt.suggested_obligations, ())
        self.assertEqual(
            state_before,
            CanonicalState(
                ref=diagnostic_input.base,
                open_obligations=state_before.open_obligations,
            ),
        )

    def test_probe_projects_are_reloadable_data_only_boundaries(self) -> None:
        executable_suffixes = {
            ".bat",
            ".cmd",
            ".js",
            ".mcfunction",
            ".ps1",
            ".py",
            ".sh",
            ".ts",
        }
        for probe in PROBES:
            with self.subTest(probe=probe.name):
                repository, case, evidence = _records(probe)
                self.assertEqual(repository.verify().orphan_paths, ())
                self.assertEqual(
                    repository.read_head(),
                    V3DiagnosticInput.from_dict(
                        case["diagnostic_input"]
                    ).base,
                )
                self.assertFalse(
                    [
                        path
                        for path in probe.rglob("*")
                        if path.is_file()
                        and path.suffix.lower() in executable_suffixes
                    ]
                )
                self.assertFalse(evidence["generation_authority"])
                self.assertFalse(evidence["architectural_usability_proven"])
                self.assertFalse(evidence["canonical_write_authority"])


if __name__ == "__main__":
    unittest.main()
