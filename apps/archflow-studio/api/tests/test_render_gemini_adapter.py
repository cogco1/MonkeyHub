"""Synthetic images and injected HTTP responses; no paid provider calls."""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
from io import BytesIO
import json
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from PIL import Image

from archflow_studio_api.application.render_contract import (
    RenderImage, RenderInput, RenderOutputOptions, RenderPageRef, RenderProviderError,
)
from archflow_studio_api.render_adapters import gemini

KEY = "synthetic-test-key-never-live"
MODEL = "gemini-3-pro-image"


def image_bytes(format="PNG", size=(32, 24), color="white"):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format=format)
    return output.getvalue()


def source(data=None, *, mime_type="image/png", run_id="private-project-run"):
    data = data if data is not None else image_bytes()
    return RenderImage(RenderPageRef(run_id, hashlib.sha256(data).hexdigest()), data, mime_type)


def final_image(data):
    return {"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(data).decode("ascii")}


class RecordingTransport:
    def __init__(self, response, status=200):
        self.response = json.dumps(response).encode() if not isinstance(response, bytes) else response
        self.status = status
        self.calls = []

    def __call__(self, endpoint, headers, body, timeout):
        self.calls.append((endpoint, headers, body, timeout))
        return self.status, {}, self.response


class GeminiAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jpeg = image_bytes("JPEG", (1024, 1024))

    def setUp(self):
        self.request = RenderInput("attempt-1", source(), (), "Warm afternoon light", RenderOutputOptions())
        self.response = {
            "id": "interactions/synthetic-result-1", "model": MODEL, "status": "completed",
            "steps": [{"type": "model_output", "content": [final_image(self.jpeg)]}],
            "usage": {"total_input_tokens": 73, "total_output_tokens": 1290},
        }

    def adapter(self, transport, **kwargs):
        return gemini.GeminiImageRenderAdapter(api_key=KEY, model=MODEL, transport=transport, **kwargs)

    def assert_outcome(self, outcome, transport, request=None):
        with self.assertRaises(RenderProviderError) as caught:
            self.adapter(transport).generate(request or self.request)
        self.assertEqual(caught.exception.outcome, outcome)
        self.assertEqual(str(caught.exception), outcome)
        return caught.exception

    def test_ordered_roles_preserve_original_bytes_and_do_not_send_project_identity(self):
        references = (source(image_bytes(color="red")), source(image_bytes("JPEG", color="blue"), mime_type="image/jpeg"))
        for ordered in (references, references[::-1], (references[0], references[0])):
            with self.subTest(ordered=[item.ref.asset_sha256 for item in ordered]):
                request = replace(self.request, references=ordered)
                transport = RecordingTransport(self.response)
                result = self.adapter(transport).generate(request)
                self.assertEqual(len(transport.calls), 1)
                endpoint, headers, body, timeout = transport.calls[0]
                self.assertEqual(endpoint, gemini.ENDPOINT)
                self.assertEqual(headers["x-goog-api-key"], KEY)
                self.assertEqual(timeout, 120)
                payload = json.loads(body)
                self.assertEqual(payload["model"], MODEL)
                self.assertFalse(payload["store"])
                self.assertEqual(payload["response_format"], {"type": "image", "mime_type": "image/jpeg", "image_size": "1K"})
                content = payload["input"]
                self.assertEqual([part["text"] for part in content[1::2]], ["Image 1: BASE", "Image 2: REFERENCE 1", "Image 3: REFERENCE 2"])
                self.assertEqual([base64.b64decode(part["data"]) for part in content[2::2]], [self.request.source.data, *(item.data for item in ordered)])
                self.assertNotIn(KEY.encode(), body)
                self.assertNotIn(b"private-project-run", body)
                self.assertNotIn(b"attempt-1", body)
                self.assertEqual(result.data, self.jpeg)
                self.assertEqual(result.mime_type, "image/jpeg")
                self.assertEqual((result.input_tokens, result.output_tokens), (73, 1290))
                self.assertEqual(result.provider_request_id, self.response["id"])
                self.assertIsNone(result.cost_usd)
                self.assertEqual(request.source, self.request.source)

    def test_explicit_aspect_ratio_and_sizes_are_sent_without_fallback(self):
        for model, size, dimensions in ((MODEL, "2K", (2048, 2048)), ("gemini-3.1-flash-image", "4K", (4096, 4096))):
            with self.subTest(model=model, size=size):
                response = dict(self.response, model=model, steps=[{"type": "model_output", "content": [final_image(image_bytes("JPEG", dimensions))]}])
                transport = RecordingTransport(response)
                adapter = gemini.GeminiImageRenderAdapter(api_key=KEY, model=model, transport=transport)
                adapter.generate(replace(self.request, output=RenderOutputOptions(size, "1:1")))
                payload = json.loads(transport.calls[0][2])
                self.assertEqual(payload["model"], model)
                self.assertEqual(payload["response_format"]["image_size"], size)
                self.assertEqual(payload["response_format"]["aspect_ratio"], "1:1")

    def test_source_or_reference_hash_drift_is_rejected_before_dispatch(self):
        changed = replace(self.request.source, data=image_bytes(color="red"))
        for request in (replace(self.request, source=changed), replace(self.request, references=(changed,))):
            transport = RecordingTransport(self.response)
            self.assert_outcome("failed", transport, request)
            self.assertFalse(transport.calls)

    def test_invalid_inputs_never_dispatch(self):
        requests = [replace(self.request, direction=" "), replace(self.request, direction="x" * 32001),
                    replace(self.request, references=(self.request.source,) * 4),
                    replace(self.request, output=RenderOutputOptions("8K")),
                    replace(self.request, output=RenderOutputOptions("1K", "arbitrary")),
                    replace(self.request, source=source(b"invalid image")),
                    replace(self.request, source=source(mime_type="image/jpeg")),
                    replace(self.request, source=source(mime_type="image/webp")),
                    replace(self.request, source=source(b"x" * (gemini.MAX_IMAGE_BYTES + 1)))]
        for request in requests:
            with self.subTest(output=request.output, length=len(request.direction)):
                transport = RecordingTransport(self.response)
                self.assert_outcome("failed", transport, request)
                self.assertFalse(transport.calls)

    def test_pixel_and_total_request_limits_before_dispatch(self):
        for constant, limit in (("MAX_PIXELS", 100), ("MAX_EDGE", 20), ("MAX_INPUT_BYTES", 1), ("MAX_REQUEST_BYTES", 50)):
            with self.subTest(constant=constant), patch.object(gemini, constant, limit):
                transport = RecordingTransport(self.response)
                self.assert_outcome("failed", transport)
                self.assertFalse(transport.calls)

    def test_animated_image_is_not_a_static_base(self):
        output = BytesIO()
        Image.new("RGB", (32, 24), "white").save(output, format="PNG", save_all=True,
                                                append_images=[Image.new("RGB", (32, 24), "black")])
        transport = RecordingTransport(self.response)
        self.assert_outcome("failed", transport, replace(self.request, source=source(output.getvalue())))
        self.assertFalse(transport.calls)

    def test_missing_credentials_or_unsupported_model_never_dispatch(self):
        for key, model in ((None, MODEL), ("", MODEL), (KEY + "\r\n", MODEL), (KEY, None), (KEY, "gemini-old-unverified")):
            with self.subTest(model=model):
                transport = RecordingTransport(self.response)
                adapter = gemini.GeminiImageRenderAdapter(api_key=key, model=model, transport=transport)
                self.assertFalse(adapter.capability().available)
                self.assertNotIn(KEY, repr(adapter))
                with self.assertRaises(RenderProviderError):
                    adapter.generate(self.request)
                self.assertFalse(transport.calls)

    def test_timeout_configuration_is_finite_bounded_and_not_silently_changed(self):
        for timeout in (0, 301, float("inf"), float("nan"), True, "120"):
            with self.subTest(timeout=timeout):
                transport = RecordingTransport(self.response)
                adapter = self.adapter(transport, timeout_s=timeout)
                self.assertFalse(adapter.capability().available)
                with self.assertRaises(RenderProviderError):
                    adapter.generate(self.request)
                self.assertFalse(transport.calls)
        transport = RecordingTransport(self.response)
        self.adapter(transport, timeout_s=37).generate(self.request)
        self.assertEqual(transport.calls[0][3], 37)

    def test_unknown_http_responses_are_not_retried(self):
        for status in (202, 302, 307, 408, 409, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                transport = RecordingTransport({"error": {"message": KEY}}, status)
                self.assert_outcome("unknown", transport)
                self.assertEqual(len(transport.calls), 1)

    def test_definitive_rejections_do_not_expose_error_body(self):
        for status in (400, 401, 403, 404, 413, 422):
            with self.subTest(status=status):
                transport = RecordingTransport({"error": {"message": KEY}}, status)
                error = self.assert_outcome("failed", transport)
                self.assertNotIn(KEY, repr(error))
                self.assertEqual(len(transport.calls), 1)

    def test_transport_exceptions_are_unknown_and_sanitized(self):
        for error in (TimeoutError(KEY), URLError(KEY), ConnectionResetError(KEY), OSError(KEY), RuntimeError(KEY)):
            with self.subTest(error=type(error).__name__):
                transport = Mock(side_effect=error)
                try:
                    self.adapter(transport).generate(self.request)
                except RenderProviderError as caught:
                    self.assertEqual(caught.outcome, "unknown")
                    self.assertNotIn(KEY, traceback.format_exc())
                else:
                    self.fail("Expected a sanitized error")
                transport.assert_called_once()

    def test_malformed_or_unfinished_response_is_unknown(self):
        for response in (b"not-json " + KEY.encode(), [], {}, {"status": "in_progress"}, {"status": "requires_action"}):
            with self.subTest(response_type=type(response).__name__):
                transport = RecordingTransport(response)
                self.assert_outcome("unknown", transport)
                self.assertEqual(len(transport.calls), 1)

    def test_response_body_is_bounded_even_for_injected_transport(self):
        transport = RecordingTransport(self.response)
        with patch.object(gemini, "MAX_RESPONSE_BYTES", 8):
            self.assert_outcome("unknown", transport)
        self.assertEqual(len(transport.calls), 1)

    def test_completed_interaction_requires_one_final_image(self):
        steps = [[], [{"type": "thought", "content": [final_image(self.jpeg)]}],
                 [{"type": "model_output", "content": [{"type": "text", "text": KEY}]}],
                 [{"type": "model_output", "content": [final_image(self.jpeg), final_image(self.jpeg)]}],
                 [{"type": "model_output", "content": [dict(final_image(self.jpeg), thought=True)]}]]
        for value in steps:
            with self.subTest(steps=len(value)):
                transport = RecordingTransport(dict(self.response, steps=value))
                self.assert_outcome("failed", transport)
                self.assertEqual(len(transport.calls), 1)

    def test_thought_image_is_ignored_when_real_final_image_exists(self):
        response = dict(self.response, steps=[{"type": "thought", "content": [final_image(b"not-a-final-image")]}, *self.response["steps"]])
        result = self.adapter(RecordingTransport(response)).generate(self.request)
        self.assertEqual(result.data, self.jpeg)

    def test_invalid_output_format_encoding_and_dimensions_are_rejected(self):
        images = [dict(final_image(self.jpeg), data="%%%"), dict(final_image(self.jpeg), mime_type="image/png"),
                  {"type": "image", "uri": "https://example.invalid/private-image.jpg"},
                  final_image(image_bytes()), final_image(self.jpeg[:len(self.jpeg)//2]),
                  final_image(image_bytes("JPEG", (64, 64))), final_image(image_bytes("JPEG", (4096, 1)))]
        for image in images:
            transport = RecordingTransport(dict(self.response, steps=[{"type": "model_output", "content": [image]}]))
            self.assert_outcome("failed", transport)
            self.assertEqual(len(transport.calls), 1)

    def test_requested_output_size_cannot_be_claimed_by_tiny_preview(self):
        self.assert_outcome("failed", RecordingTransport(self.response), replace(self.request, output=RenderOutputOptions("4K")))

    def test_output_pixel_and_byte_limits_are_enforced(self):
        for constant, limit in (("MAX_OUTPUT_BYTES", 10), ("MAX_PIXELS", 1_000_000), ("MAX_EDGE", 1000)):
            with self.subTest(constant=constant), patch.object(gemini, constant, limit):
                self.assert_outcome("failed", RecordingTransport(self.response))

    def test_optional_mime_is_verified_from_real_jpeg(self):
        del self.response["steps"][0]["content"][0]["mime_type"]
        self.assertEqual(self.adapter(RecordingTransport(self.response)).generate(self.request).data, self.jpeg)

    def test_model_mismatch_is_rejected_without_fallback(self):
        transport = RecordingTransport(dict(self.response, model="unexpected-model"))
        self.assert_outcome("failed", transport)
        self.assertEqual(len(transport.calls), 1)

    def test_missing_or_untrusted_usage_and_request_id_stays_unknown(self):
        for usage in (None, {"total_input_tokens": True, "total_output_tokens": -1}, {"total_input_tokens": "13", "total_output_tokens": 10**13}):
            for request_id in (None, KEY, "prefix" + KEY, "id\n" + KEY, "x" * 241):
                response = dict(self.response, usage=usage, id=request_id, cost_usd=123)
                result = self.adapter(RecordingTransport(response)).generate(self.request)
                self.assertIsNone(result.input_tokens)
                self.assertIsNone(result.output_tokens)
                self.assertIsNone(result.provider_request_id)
                self.assertIsNone(result.cost_usd)

    def test_failed_and_cancelled_provider_status_is_terminal(self):
        for status in ("failed", "cancelled"):
            self.assert_outcome("failed", RecordingTransport(dict(self.response, status=status)))

    def test_each_generate_is_one_explicit_attempt_without_adapter_cache(self):
        transport = RecordingTransport(self.response)
        adapter = self.adapter(transport)
        first = adapter.generate(self.request)
        second = adapter.generate(replace(self.request, request_id="attempt-2"))
        self.assertEqual(first.data, second.data)
        self.assertEqual(len(transport.calls), 2)

    def test_factory_only_uses_resolved_settings(self):
        settings = SimpleNamespace(render_provider="off", render_api_key=None, render_model=None, render_timeout_s=120)
        self.assertIsNone(gemini.adapter_from_settings(settings))
        settings.render_provider = "gemini"
        self.assertFalse(gemini.adapter_from_settings(settings).capability().available)
        settings.render_api_key, settings.render_model = KEY, MODEL
        capability = gemini.adapter_from_settings(settings).capability()
        self.assertTrue(capability.available)
        self.assertEqual(capability.max_references, 3)

    def test_default_transport_bounds_read_and_disables_redirects(self):
        response = Mock(code=200, headers={"Content-Type": "application/json"})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"{}"
        opener = Mock()
        opener.open.return_value = response
        with patch.object(gemini, "build_opener", return_value=opener) as builder:
            result = gemini._http_transport(gemini.ENDPOINT, {"x-goog-api-key": KEY}, b"{}", 12)
        response.read.assert_called_once_with(gemini.MAX_RESPONSE_BYTES + 1)
        opener.open.assert_called_once()
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 12)
        self.assertEqual(result, (200, {"Content-Type": "application/json"}, b"{}"))
        handler = builder.call_args.args[0]
        self.assertIsNone(handler.redirect_request(None, None, 307, None, {}, "https://example.invalid"))

    def test_default_transport_reads_http_error_once_without_replay(self):
        error = HTTPError(gemini.ENDPOINT, 503, "unavailable", {}, BytesIO(b"private provider error"))
        opener = Mock()
        opener.open.side_effect = error
        with patch.object(gemini, "build_opener", return_value=opener):
            result = gemini._http_transport(gemini.ENDPOINT, {}, b"{}", 12)
        self.assertEqual(result[0], 503)
        opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
