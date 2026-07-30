from __future__ import annotations

import json
import sys
import unittest

from archflow.adapters.cli_retrieval import (
    CliProviderSpec,
    CliRetrievalAdapter,
    RetrievalQuery,
    RetrievalStatus,
)
from archflow.capabilities.retrieval import (
    RetrievalCapability,
    RetrievalCapabilityRegistry,
)
from archflow.project import ProjectVersionRef


def _query(project_id: str = "project-a") -> RetrievalQuery:
    return RetrievalQuery(
        query_id="query-001",
        project_id=project_id,
        run_id="run-001",
        base=ProjectVersionRef(project_id, 2, "1" * 64),
        query_text="Find evidence relevant to the current building request.",
        evidence_refs=(f"project://{project_id}/input/request.json",),
    )


def _provider_command(
    *,
    payload: dict | None = None,
    raw: str | None = None,
    delay: float = 0,
    exit_code: int = 0,
) -> tuple[str, ...]:
    statements = [
        "import json,sys,time",
        "json.load(sys.stdin.buffer)",
        f"time.sleep({delay!r})",
    ]
    if raw is not None:
        statements.append(f"sys.stdout.write({raw!r})")
    elif payload is not None:
        statements.append(f"print(json.dumps({payload!r}, sort_keys=True))")
    if exit_code:
        statements.extend(
            [
                "sys.stderr.write('provider failed')",
                f"sys.exit({exit_code})",
            ]
        )
    return (sys.executable, "-c", ";".join(statements))


def _success_payload() -> dict:
    return {
        "schema": "CliRetrievalOutput@1",
        "results": [
            {
                "title": "Primary source",
                "source_uri": "https://example.test/source",
                "excerpt": "Evidence returned by the configured test provider.",
            }
        ],
    }


def _adapter(
    command: tuple[str, ...],
    *,
    timeout: float = 2,
    max_output_bytes: int = 64_000,
) -> CliRetrievalAdapter:
    return CliRetrievalAdapter(
        CliProviderSpec(
            provider_id="provider.test",
            version="test-1",
            command=command,
            timeout_seconds=timeout,
            max_output_bytes=max_output_bytes,
        )
    )


class CliRetrievalTests(unittest.TestCase):
    def test_success_binds_project_base_provider_command_and_digest(self) -> None:
        adapter = _adapter(_provider_command(payload=_success_payload()))
        query = _query()

        receipt = adapter.retrieve(query)

        self.assertIs(receipt.status, RetrievalStatus.SUCCESS)
        self.assertEqual(receipt.query, query)
        self.assertEqual(receipt.provider_id, "provider.test")
        self.assertEqual(receipt.provider_version, "test-1")
        self.assertEqual(receipt.command, adapter.spec.command)
        self.assertEqual(receipt.provider_fingerprint, adapter.spec.fingerprint)
        self.assertIsNotNone(receipt.output_sha256)
        self.assertEqual(len(receipt.results), 1)
        evidence = receipt.results[0]
        self.assertEqual(evidence.project_id, "project-a")
        self.assertEqual(evidence.epistemic_status, "hypothesis")
        self.assertFalse(hasattr(receipt, "committer"))
        self.assertFalse(hasattr(receipt, "world"))

    def test_timeout_malformed_oversized_exit_and_offline_are_named(self) -> None:
        cases = (
            (
                _adapter(
                    _provider_command(payload=_success_payload(), delay=0.2),
                    timeout=0.02,
                ),
                RetrievalStatus.TIMEOUT,
                "retrieval.timeout",
            ),
            (
                _adapter(_provider_command(raw="not-json")),
                RetrievalStatus.MALFORMED,
                "retrieval.output_malformed",
            ),
            (
                _adapter(
                    _provider_command(raw="x" * 1_000),
                    max_output_bytes=256,
                ),
                RetrievalStatus.OVERSIZED,
                "retrieval.output_oversized",
            ),
            (
                _adapter(_provider_command(raw="", exit_code=3)),
                RetrievalStatus.EXIT_ERROR,
                "retrieval.provider_exit",
            ),
            (
                _adapter(("archflow-provider-that-does-not-exist",)),
                RetrievalStatus.OFFLINE,
                "retrieval.provider_unavailable",
            ),
        )
        for adapter, status, error_code in cases:
            with self.subTest(status=status):
                receipt = adapter.retrieve(_query())
                self.assertIs(receipt.status, status)
                self.assertEqual(receipt.error_code, error_code)
                self.assertEqual(receipt.results, ())

    def test_registry_has_no_silent_fallback_and_discovery_is_not_schedule(self) -> None:
        registry = RetrievalCapabilityRegistry()
        adapter = _adapter(_provider_command(payload=_success_payload()))
        capability = RetrievalCapability(
            provider_id="provider.test",
            description="Reads configured public evidence.",
            topics=frozenset({"history", "program"}),
        )
        registry.register(capability, adapter)

        self.assertEqual(
            registry.discover(topics=frozenset({"history"})),
            (capability,),
        )
        missing = registry.invoke("provider.missing", _query())

        self.assertIs(missing.status, RetrievalStatus.MISSING_PROVIDER)
        self.assertEqual(missing.provider_id, "provider.missing")
        self.assertEqual(missing.error_code, "retrieval.provider_missing")
        self.assertEqual(missing.command, ())

    def test_same_provider_output_remains_isolated_by_building(self) -> None:
        adapter = _adapter(_provider_command(payload=_success_payload()))

        project_a = adapter.retrieve(_query("project-a"))
        project_b = adapter.retrieve(_query("project-b"))

        self.assertNotEqual(project_a.receipt_id, project_b.receipt_id)
        self.assertNotEqual(
            project_a.results[0].evidence_id,
            project_b.results[0].evidence_id,
        )
        self.assertEqual(project_a.results[0].project_id, "project-a")
        self.assertEqual(project_b.results[0].project_id, "project-b")

    def test_malformed_result_fields_and_excerpts_fail_closed(self) -> None:
        drifted = {
            "schema": "CliRetrievalOutput@1",
            "results": [
                {
                    "title": "Source",
                    "source_uri": "https://example.test",
                    "excerpt": "text",
                    "authority": "hard",
                }
            ],
        }
        adapter = _adapter(_provider_command(payload=drifted))

        receipt = adapter.retrieve(_query())

        self.assertIs(receipt.status, RetrievalStatus.MALFORMED)
        self.assertEqual(receipt.results, ())


if __name__ == "__main__":
    unittest.main()
