"""Document pixels cross the CLI and real Messages SDK boundaries."""

from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import archflow_studio_api  # noqa: F401

from archflow.contracts.canonical import canonical_digest
from archflow.ports.model import ModelInvocationStatus
from archflow_studio_api.application import intent_agent
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.intent_agent import (
    AnthropicCompiler,
    CodexCompiler,
    DocumentVisual,
    IntentAgentFailed,
    Selection,
)
from archflow_studio_api.application.projection import project_state
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import CODEX_SHIM_VERSION, PROJECT_ID, make_project


MESSAGE = "Use the reference drawing and its blue line to adjust the portico."
MODEL = "existing-configured-test-model"
ANSWER = json.dumps({
    "status": "compiled",
    "targetComponentId": "portico",
    "elementId": "portico-base",
    "utterance": "set height to 0.8",
    "semanticEdit": None,
    "why": "The blue line indicates the requested height.",
    "question": None,
})


def _png(*, annotated: bool = False, revised: bool = False) -> bytes:
    image = Image.new("RGB", (128, 96), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 25, 108, 80), outline="black", width=2)
    if annotated:
        draw.line((12, 18, 116, 18), fill="#0066ff", width=4)
    if revised:
        draw.rectangle((42, 45, 48, 51), fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _write_visual_codex(root: Path, *, answer: str = ANSWER) -> tuple[Path, Path]:
    """The existing shim pattern, with argv and file reads in its child."""
    capture = root / "observed.json"
    script = root / "visual agent.py"
    script.write_text(
        "import base64, hashlib, json, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if args == ['--version']:\n"
        f"    print({CODEX_SHIM_VERSION!r})\n"
        "    raise SystemExit(0)\n"
        "sys.stdin.reconfigure(encoding='utf-8', errors='strict')\n"
        "prompt = sys.stdin.read()\n"
        "images = []\n"
        "for index, argument in enumerate(args):\n"
        "    if argument == '--image':\n"
        "        path = Path(args[index + 1])\n"
        "        data = path.read_bytes()\n"
        "        images.append({'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),\n"
        "                       'data': base64.b64encode(data).decode('ascii')})\n"
        "observed = {'argv': args, 'prompt': prompt, 'images': images,\n"
        "            'workdir': args[args.index('-C') + 1]}\n"
        f"Path({str(capture)!r}).write_text(json.dumps(observed), encoding='utf-8')\n"
        f"Path(args[args.index('-o') + 1]).write_text({answer!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        shim = root / "codex.cmd"
        shim.write_text(
            f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8"
        )
    else:
        shim = root / "codex"
        shim.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
    return shim, capture


class DocumentVisualTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="studio-document-visual-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        make_project(self.root)
        app = create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        self.binding = bound_project(app.state)
        self.projection = project_state(self.binding)
        self.original_record = canonical_digest(self.projection.record.to_dict(), ascii=False)
        self.head = self.root / PROJECT_ID / "HEAD"
        self.original_head = self.head.read_bytes()
        self.page = _png()
        self.annotated = _png(annotated=True)
        self.context = {
            "source": {"name": "reference-plan.png", "assetSha256": "a" * 64, "pageIndex": 0},
            "role": "reference",
            "referenceNote": "Follow the blue canopy line while keeping the existing model.",
        }

    def selection(self, *, page: bytes | None = None, repeated_page: bool = False) -> Selection:
        visuals = [DocumentVisual(
            context=self.context,
            page_png=self.page if page is None else page,
            annotated_png=self.annotated,
        )]
        if repeated_page:
            visuals.append(DocumentVisual(
                context={**self.context, "referenceNote": "The same original page is also a reference."},
                page_png=self.page,
            ))
        return Selection("portico", "portico-base", document_visuals=tuple(visuals))

    def assert_bound_visuals(self, compilation, selection: Selection, prompt: str) -> None:
        self.assertEqual(compilation.status, "compiled")
        self.assertEqual(compilation.utterance, "set height to 0.8")
        self.assertEqual(compilation.model, MODEL)
        receipt = compilation.receipt
        self.assertIsNotNone(receipt)
        self.assertIs(receipt.status, ModelInvocationStatus.SUCCESS)
        self.assertEqual(receipt.model_id, MODEL)
        request = receipt.request
        self.assertEqual(request.payload["message"], MESSAGE)
        self.assertEqual(request.context_digest, canonical_digest(request.payload, ascii=False))
        sheet = request.payload["record_sheet"]
        self.assertEqual(len(sheet["documentVisuals"]), len(selection.document_visuals))
        expected = [
            {"imageIndex": 1, "kind": "page", "sha256": hashlib.sha256(self.page).hexdigest()},
            {"imageIndex": 2, "kind": "annotated", "sha256": hashlib.sha256(self.annotated).hexdigest()},
        ]
        self.assertEqual(sheet["documentVisuals"][0], {**self.context, "images": expected})
        if len(selection.document_visuals) == 2:
            self.assertEqual(sheet["documentVisuals"][1]["images"], [expected[0]])
        sent_sheet = prompt.split("RECORD SHEET (JSON):\n", 1)[1].split("\n\nREQUEST:\n", 1)[0]
        self.assertEqual(json.loads(sent_sheet)["documentVisuals"], sheet["documentVisuals"])
        self.assertEqual(self.head.read_bytes(), self.original_head)
        self.assertEqual(
            canonical_digest(project_state(self.binding).record.to_dict(), ascii=False),
            self.original_record,
        )

    def assert_pixels_change_identity(self, compiler) -> None:
        original = self.selection()
        changed = replace(original, document_visuals=(
            replace(original.document_visuals[0], page_png=_png(revised=True)),
        ))
        # Hold attempt id and timing still: a new receipt must follow the pixels.
        with (
            patch.object(intent_agent.uuid, "uuid4", return_value=SimpleNamespace(hex="f" * 32)),
            patch.object(intent_agent, "_elapsed_ms", return_value=0),
        ):
            first, repeated, revised = [
                compiler.compile(message=MESSAGE, selection=selection, projection=self.projection)
                for selection in (original, original, changed)
            ]
        self.assertEqual(first.receipt.request.context_digest, repeated.receipt.request.context_digest)
        self.assertEqual(first.receipt.receipt_id, repeated.receipt.receipt_id)
        self.assertNotEqual(first.receipt.request.context_digest, revised.receipt.request.context_digest)
        self.assertNotEqual(first.receipt.receipt_id, revised.receipt.receipt_id)
        self.assertEqual(first.receipt.request.request_id, revised.receipt.request.request_id)
        self.assertEqual(first.receipt.request.payload["message"], revised.receipt.request.payload["message"])
        self.assertEqual(first.receipt.model_id, MODEL)
        self.assertEqual(revised.receipt.model_id, MODEL)


class CodexDocumentVisualTests(DocumentVisualTestCase):
    def setUp(self) -> None:
        super().setUp()
        shim, self.capture = _write_visual_codex(self.root)
        self.compiler = CodexCompiler(executable=str(shim), model=MODEL, timeout_s=30)

    def test_subprocess_reads_ordered_pngs_and_temporary_images_are_removed(self) -> None:
        selection = self.selection(repeated_page=True)
        compilation = self.compiler.compile(
            message=MESSAGE, selection=selection, projection=self.projection
        )
        observed = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(observed["argv"].count("--image"), 2)
        self.assertEqual(observed["argv"][observed["argv"].index("-m") + 1], MODEL)
        self.assertEqual(
            [base64.b64decode(row["data"]) for row in observed["images"]],
            [self.page, self.annotated],
        )
        self.assertEqual(
            [row["sha256"] for row in observed["images"]],
            [hashlib.sha256(data).hexdigest() for data in (self.page, self.annotated)],
        )
        workdir = Path(observed["workdir"])
        for row in observed["images"]:
            self.assertTrue(Path(row["path"]).is_relative_to(workdir))
            self.assertFalse(Path(row["path"]).exists())
        self.assertFalse(workdir.exists())
        self.assert_bound_visuals(compilation, selection, observed["prompt"])
        self.assertEqual(
            compilation.receipt.input_bytes,
            len(observed["prompt"].encode("utf-8"))
            + sum(len(base64.b64decode(row["data"])) for row in observed["images"]),
        )

    def test_changed_pixels_change_the_bound_request_and_receipt(self) -> None:
        self.assert_pixels_change_identity(self.compiler)

    def test_malformed_answer_still_counts_the_transmitted_pngs(self) -> None:
        _write_visual_codex(self.root, answer="This is not the required JSON answer.")
        with self.assertRaises(IntentAgentFailed) as caught:
            self.compiler.compile(
                message=MESSAGE,
                selection=self.selection(repeated_page=True),
                projection=self.projection,
            )
        receipt = caught.exception.receipt
        self.assertIs(receipt.status, ModelInvocationStatus.MALFORMED)
        self.assertEqual(receipt.error_code, "model.output_malformed")
        observed = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(len(observed["images"]), 2)
        self.assertEqual(
            receipt.input_bytes,
            len(observed["prompt"].encode("utf-8"))
            + sum(len(base64.b64decode(row["data"])) for row in observed["images"]),
        )
        self.assertFalse(Path(observed["workdir"]).exists())


class AnthropicDocumentVisualTests(DocumentVisualTestCase):
    def setUp(self) -> None:
        super().setUp()
        try:
            import anthropic
        except ImportError:
            self.skipTest("the optional Anthropic SDK is not installed")
        # Anthropic 1.3 uses httpx2; this is its real HTTP transport, not a fake SDK.
        import httpx2 as httpx

        self.requests = []

        def receive(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/messages")
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "msg-document-visual-test",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "content": [{"type": "text", "text": ANSWER}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 200, "output_tokens": 40},
            })

        client = httpx.Client(transport=httpx.MockTransport(receive), trust_env=False)
        self.addCleanup(client.close)
        real_anthropic = anthropic.Anthropic

        def create_client(**kwargs):
            return real_anthropic(
                api_key="test-only-document-visual-key",
                auth_token="",
                base_url="https://document-visual-test.invalid",
                max_retries=0,
                http_client=client,
                **kwargs,
            )

        factory = patch.object(anthropic, "Anthropic", side_effect=create_client)
        factory.start()
        self.addCleanup(factory.stop)
        self.compiler = AnthropicCompiler(model=MODEL, timeout_s=30)

    def test_real_sdk_serializes_labelled_pngs_without_changing_the_model(self) -> None:
        selection = self.selection(repeated_page=True)
        compilation = self.compiler.compile(
            message=MESSAGE, selection=selection, projection=self.projection
        )
        self.assertEqual(len(self.requests), 1)
        body = self.requests[0]
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["messages"][0]["role"], "user")
        content = body["messages"][0]["content"]
        self.assertIsInstance(content, list)
        images = [block for block in content if block["type"] == "image"]
        self.assertEqual(len(images), 2)
        self.assertEqual(
            [base64.b64decode(block["source"]["data"], validate=True) for block in images],
            [self.page, self.annotated],
        )
        image_index = 0
        for index, block in enumerate(content):
            if block["type"] != "image":
                continue
            image_index += 1
            self.assertEqual(block["source"]["type"], "base64")
            self.assertEqual(block["source"]["media_type"], "image/png")
            self.assertGreater(index, 0)
            self.assertEqual(content[index - 1]["type"], "text")
            self.assertRegex(content[index - 1]["text"], rf"\b{image_index}\b")
        prompt = "\n".join(block["text"] for block in content if block["type"] == "text")
        self.assert_bound_visuals(compilation, selection, prompt)
        self.assertEqual(
            compilation.receipt.input_bytes,
            len(body["system"].encode("utf-8"))
            + sum(len(block["text"].encode("utf-8")) for block in content if block["type"] == "text")
            + sum(len(base64.b64decode(block["source"]["data"])) for block in images),
        )

    def test_changed_pixels_change_the_bound_request_and_receipt(self) -> None:
        self.assert_pixels_change_identity(self.compiler)
        self.assertEqual(len(self.requests), 3)


if __name__ == "__main__":
    unittest.main()
