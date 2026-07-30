from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from archflow.adapters.model_provider import (
    ModelInvocationReceipt,
    ModelInvocationStatus,
)
from archflow.project import FilesystemProjectRepository
from archflow.runtime.sandbox_gold import (
    RawSandboxRequest,
    execute_sandbox_gold,
    reload_sandbox_gold,
)


def _concept_proposal() -> dict[str, object]:
    return {
        "schema": "SandboxArchitectConcept@1",
        "proposal_id": "concept-shelter",
        "functions": [
            {
                "function_id": "gathering",
                "label": "Gathering room",
                "capacity": 8,
                "area_m2": 40.0,
            }
        ],
        "relations": [],
        "envelope": {
            "width_m": 8,
            "depth_m": 8,
            "clear_height_m": 3,
        },
        "performance_requirements": {
            "minimum_clear_height_m": 2,
            "circulation_min_width_m": 1,
        },
        "material_strategy": "warm timber over a stone plinth",
        "rationale": "One gathering volume answers the raw request.",
    }


def _revision_proposal() -> dict[str, object]:
    return {
        **_concept_proposal(),
        "schema": "SandboxArchitectProposal@1",
        "proposal_id": "revised-shelter",
        "entry": {"offset_m": 3, "width_m": 2, "height_m": 2},
        "window": {
            "offset_m": 3,
            "width_m": 2,
            "height_m": 1,
            "sill_m": 1,
        },
        "detail_asset": {
            "asset_id": "detail-lantern",
            "uri": "archflow://assets/detail-lantern",
            "media_type": "model/gltf+json",
            "sockets": ["origin"],
            "vertices": [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ],
            "faces": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]],
            "provenance_refs": ["evidence://model/p026-detail"],
        },
    }


class _ScriptedArchitectProvider:
    """Deterministic stand-in for the asynchronous Architect provider."""

    async def invoke(self, request) -> ModelInvocationReceipt:
        proposal = (
            _concept_proposal()
            if request.request_id.endswith("-concept")
            else _revision_proposal()
        )
        output_json = json.dumps(
            proposal,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = output_json.encode("utf-8")
        return ModelInvocationReceipt(
            receipt_id=f"receipt-{request.request_id}",
            status=ModelInvocationStatus.SUCCESS,
            request=request,
            provider_id="scripted-architect",
            model_id="scripted-model",
            provider_version="1.0",
            provider_fingerprint="scripted-architect-fingerprint",
            input_bytes=len(request.to_json().encode("utf-8"))
            if hasattr(request, "to_json")
            else 0,
            output_bytes=len(encoded),
            output_sha256=hashlib.sha256(encoded).hexdigest(),
            output_json=output_json,
        )


class SandboxGoldTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repository = FilesystemProjectRepository.initialize(
            Path(temp.name) / "p026-gold",
            project_id="p026-gold",
            initial_state={"phase": "request", "commitments": []},
        )

    def test_gold_run_promotes_reloads_and_survives_restart(self) -> None:
        run = self.repository.create_run("gold-001")
        request = RawSandboxRequest(
            request_id="request-001",
            prompt=(
                "A small gathering shelter for about eight people with "
                "daylight and one sheltered entrance."
            ),
        )

        result = asyncio.run(
            execute_sandbox_gold(
                self.repository,
                run,
                _ScriptedArchitectProvider(),
                request,
                issued_at_utc="2026-07-30T10:00:00Z",
                valid_until_utc="2026-07-30T11:00:00Z",
            )
        )

        self.assertEqual(result.committed.version, 1)
        self.assertEqual(
            self.repository.read_head().version,
            1,
        )

        summary = reload_sandbox_gold(self.repository, run_id="gold-001")
        self.assertIs(summary["accepted"], True)
        self.assertTrue(summary["concept_findings"])

        reopened = FilesystemProjectRepository.open(
            self.repository.layout.root
        )
        resurvived = reload_sandbox_gold(reopened, run_id="gold-001")
        self.assertEqual(
            resurvived["scene_digest"],
            summary["scene_digest"],
        )


if __name__ == "__main__":
    unittest.main()
