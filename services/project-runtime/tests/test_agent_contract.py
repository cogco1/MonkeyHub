"""The agent contract after #419 C6: no producers, no ``semanticKind``.

An agent authors geometry with a construction script and reads meaning back as
facets (spec Sec.3.2); it never chooses a producer and never states
``semanticKind`` on a sketch. This module builds the Studio's own OpenAPI
document and checks the request schemas an agent actually reads:
``POST /api/proposals`` (``semanticEdit``), ``POST /api/proposals/construction``
and ``POST /api/proposals/facets`` carry no producer enum/const and none of the
construction layer rule's tokens, ``SketchActionDto`` has no ``semanticKind``,
and ``GET /api/construction``'s response passes the same rule.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from monkeyarch.construction.vocabulary import LAYER_RULE_TOKENS, layer_rule_violations

# The three routes an agent reads to author and mean geometry (spec Sec.3.2);
# none of their request schemas may name a producer or semanticKind.
AGENT_REQUEST_ROUTES = (
    ("POST", "/api/proposals"),
    ("POST", "/api/proposals/construction"),
    ("POST", "/api/proposals/facets"),
)


def _texts(node: object, schemas: dict, seen: set[str]):
    """Every description, summary, title, property name and enum/const value reachable from ``node``.

    Resolves ``$ref`` against ``schemas`` (the OpenAPI document's
    ``components.schemas``), following each name once so a self- or
    mutually-recursive schema still terminates.
    """

    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[1]
            if name not in seen:
                seen.add(name)
                yield name
                yield from _texts(schemas[name], schemas, seen)
        for key, value in node.items():
            if key in ("description", "summary", "title") and isinstance(value, str):
                yield value
            elif key == "properties" and isinstance(value, dict):
                yield from value  # the field names themselves ("producer", "semanticKind", ...)
                yield from _texts(value, schemas, seen)
            elif key in ("enum", "const"):
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if isinstance(item, str):
                        yield item
            else:
                yield from _texts(value, schemas, seen)
    elif isinstance(node, list):
        for item in node:
            yield from _texts(item, schemas, seen)


class AgentContractTestCase(unittest.TestCase):
    """No project needs to be bound: every check here is static schema introspection."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="agent-contract-")
        self.addCleanup(temporary.cleanup)
        self.client = TestClient(create_app(StudioSettings(cad_export="off", project_dir=Path(temporary.name) / "proj")))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        document = self.client.get("/openapi.json").json()
        self.paths = document["paths"]
        self.schemas = document["components"]["schemas"]

    def request_schema(self, method: str, path: str) -> dict:
        operation = self.paths[path][method.lower()]
        return operation["requestBody"]["content"]["application/json"]["schema"]

    def response_schema(self, method: str, path: str, status: str = "200") -> dict:
        operation = self.paths[path][method.lower()]
        return operation["responses"][status]["content"]["application/json"]["schema"]

    def test_agent_request_schemas_carry_no_producer_and_no_semantic_kind(self) -> None:
        for method, path in AGENT_REQUEST_ROUTES:
            with self.subTest(method=method, path=path):
                schema = self.request_schema(method, path)
                texts = list(_texts(schema, self.schemas, set()))
                self.assertTrue(texts, f"{method} {path} carries no descriptive text at all")
                violations = {text: layer_rule_violations(text) for text in texts if layer_rule_violations(text)}
                self.assertEqual(violations, {}, f"{method} {path} names a layer-rule token")
                lowered = {text.lower() for text in texts}
                self.assertNotIn("producer", lowered, f"{method} {path} still names a producer field or value")
                self.assertNotIn("semantickind", lowered, f"{method} {path} still names semanticKind")
                self.assertNotIn("semantic_kind", lowered, f"{method} {path} still names semantic_kind")

    def test_sketch_action_dto_has_no_semantic_kind(self) -> None:
        sketch = self.schemas["SketchActionDto"]
        self.assertNotIn("semanticKind", sketch["properties"])
        self.assertNotIn("semantic_kind", sketch["properties"])
        document_tracing = self.schemas["DocumentTracingRequestDto"]
        self.assertNotIn("semanticKind", document_tracing["properties"])
        self.assertNotIn("semantic_kind", document_tracing["properties"])

    def test_construction_vocabulary_response_passes_the_layer_rule(self) -> None:
        schema = self.response_schema("GET", "/api/construction")
        texts = list(_texts(schema, self.schemas, set()))
        self.assertTrue(texts)
        violations = {text: layer_rule_violations(text) for text in texts if layer_rule_violations(text)}
        self.assertEqual(violations, {})

    def test_the_layer_rule_tokens_used_here_match_the_construction_vocabulary(self) -> None:
        # A guard against the two lists silently drifting apart: this test's
        # confidence depends on checking against the same tokens construction
        # text is held to.
        self.assertIn("producer", LAYER_RULE_TOKENS)
        self.assertIn("semantickind", LAYER_RULE_TOKENS)
        self.assertIn("wall", LAYER_RULE_TOKENS)


if __name__ == "__main__":
    unittest.main()
