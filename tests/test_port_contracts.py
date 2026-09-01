from __future__ import annotations

import ast
import hashlib
import json
import unittest
from pathlib import Path

import archflow.adapters.cli_retrieval as retrieval_adapter
import archflow.adapters.model_provider as model_adapter
import archflow.ports as external_ports
import archflow.ports.model as model_port
import archflow.ports.retrieval as retrieval_port
from archflow.project.refs import ProjectVersionRef


ROOT = Path(__file__).resolve().parents[1]


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


class ExternalPortContractTests(unittest.TestCase):
    def test_adapter_facades_preserve_boundary_object_identity(self) -> None:
        for name in (
            "AsyncModelProvider",
            "ModelInvocationReceipt",
            "ModelInvocationRequest",
            "ModelInvocationStatus",
            "ModelPhase",
        ):
            self.assertIs(
                getattr(model_adapter, name),
                getattr(model_port, name),
            )
            self.assertIs(
                getattr(external_ports, name),
                getattr(model_port, name),
            )
        for name in (
            "RetrievalQuery",
            "RetrievalReceipt",
            "RetrievalStatus",
            "RetrievedEvidence",
        ):
            self.assertIs(
                getattr(retrieval_adapter, name),
                getattr(retrieval_port, name),
            )
            self.assertIs(
                getattr(external_ports, name),
                getattr(retrieval_port, name),
            )

    def test_model_request_schema_payload_and_digest_are_fixed(self) -> None:
        request = model_port.ModelInvocationRequest.create(
            request_id="request-001",
            phase=model_port.ModelPhase.RESEARCH,
            checkpoint_digest="a" * 64,
            context_digest="b" * 64,
            payload={
                "question": "roof evidence",
                "evidence_refs": [
                    "project://demo/evidence/source-1"
                ],
            },
        )

        self.assertEqual(
            request.to_dict(),
            {
                "schema": "ModelInvocationRequest@1",
                "request_id": "request-001",
                "phase": "research",
                "checkpoint_digest": "a" * 64,
                "context_digest": "b" * 64,
                "payload": {
                    "evidence_refs": [
                        "project://demo/evidence/source-1"
                    ],
                    "question": "roof evidence",
                },
            },
        )
        self.assertEqual(
            _digest(request.to_dict()),
            "533c6f14c13ce91f3e5627a074a4d35c043d7d4836cd986b8b100cfe9e07e67b",
        )
        self.assertEqual(
            model_port.ModelInvocationReceipt.SCHEMA,
            "ModelInvocationReceipt@2",
        )
        self.assertEqual(
            model_port.ModelInvocationReceipt.LEGACY_SCHEMA,
            "ModelInvocationReceipt@1",
        )

    def test_retrieval_query_schema_payload_and_digest_are_fixed(self) -> None:
        query = retrieval_port.RetrievalQuery(
            query_id="query-001",
            project_id="demo",
            run_id="run-001",
            base=ProjectVersionRef("demo", 0, "c" * 64),
            query_text="roof evidence",
            evidence_refs=("project://demo/evidence/source-1",),
        )

        self.assertEqual(query.to_payload()["schema"], "CliRetrievalQuery@1")
        self.assertEqual(
            _digest(query.to_payload()),
            "3a9401f93fde1e2c1794b458746d05bcf9de07491aada89b102715df6cab9b97",
        )

    def test_ports_never_depend_on_concrete_adapters(self) -> None:
        model_imports = _imported_modules(
            ROOT / "archflow" / "ports" / "model.py"
        )
        retrieval_imports = _imported_modules(
            ROOT / "archflow" / "ports" / "retrieval.py"
        )
        self.assertFalse(
            {
                name
                for name in model_imports | retrieval_imports
                if name.startswith("archflow.adapters")
            }
        )
        self.assertIn(
            "archflow.ports.model",
            _imported_modules(
                ROOT / "archflow" / "adapters" / "model_provider.py"
            ),
        )
        self.assertIn(
            "archflow.ports.retrieval",
            _imported_modules(
                ROOT / "archflow" / "adapters" / "cli_retrieval.py"
            ),
        )


if __name__ == "__main__":
    unittest.main()
