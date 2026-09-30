"""One Gemini Interactions attempt with an ordered base and visual references.

Wire format checked against https://ai.google.dev/api/interactions-api and
https://ai.google.dev/gemini-api/docs/image-generation on 2026-09-23.
The LINE+ full-frame/patch adapters informed the one-attempt boundary; their
admission, persistence and receipt systems are deliberately not imported.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
import hashlib
from io import BytesIO
import json
import math
import re
from typing import TYPE_CHECKING
import warnings
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

from archflow.contracts.canonical import canonical_json
from ..application.render_contract import (
    RenderCapability, RenderInput, RenderOutput, RenderProviderError,
)

if TYPE_CHECKING:
    from ..settings import StudioSettings

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODELS = ("gemini-3-pro-image", "gemini-3.1-flash-image")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_INPUT_BYTES = 12 * 1024 * 1024
MAX_REQUEST_BYTES = 20_000_000
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 32_000_000
MAX_EDGE = 8192
MAX_REFERENCES = 3
ASPECT_RATIOS = ("source", "1:1", "4:3", "3:4", "16:9", "9:16")
Transport = Callable[[str, Mapping[str, str], bytes, float], tuple[int, Mapping[str, str], bytes]]
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+\-=]{0,239}\Z")


class _NoRedirect(HTTPRedirectHandler):
    # A redirect must neither replay the POST nor forward its API key.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_transport(
    endpoint: str, headers: Mapping[str, str], body: bytes, timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(endpoint, data=body, headers=dict(headers), method="POST")
    try:
        response = build_opener(_NoRedirect()).open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        # No SDK retry policy; every invocation issues exactly one request.
        return response.code, dict(response.headers), response.read(MAX_RESPONSE_BYTES + 1)


def _image_size(data: bytes, mime_type: str, maximum: int) -> tuple[int, int]:
    if not isinstance(data, bytes) or not 0 < len(data) <= maximum:
        raise ValueError("image_size")
    if mime_type not in ("image/png", "image/jpeg"):
        raise ValueError("image_format")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as image:
            expected = {"image/png": "PNG", "image/jpeg": "JPEG"}[mime_type]
            width, height = image.size
            if (image.format != expected or getattr(image, "n_frames", 1) != 1
                    or not 0 < width <= MAX_EDGE or not 0 < height <= MAX_EDGE
                    or width * height > MAX_PIXELS):
                raise ValueError("image_format_or_dimensions")
            image.verify()
        # verify() alone does not decode JPEG pixels or detect all truncation.
        with Image.open(BytesIO(data)) as image:
            image.load()
    return width, height


class GeminiImageRenderAdapter:
    """Credentials are injected in memory; generate never writes or retries."""

    def __init__(
        self, *, api_key: str | None, model: str | None,
        timeout_s: float = 120.0, transport: Transport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._transport = transport or _http_transport

    def capability(self) -> RenderCapability:
        key = self._api_key
        configured = (isinstance(key, str) and 0 < len(key) <= 512
                      and key.isascii() and all(32 < ord(char) < 127 for char in key))
        timeout = self._timeout_s
        timeout_valid = (isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
                         and math.isfinite(timeout) and 1 <= timeout <= 300)
        available = configured and self._model in MODELS and timeout_valid
        return RenderCapability(
            provider_id="gemini", label="Gemini", model=self._model if self._model in MODELS else None,
            available=available, unavailable_reason=None if available else "not_configured",
            sizes=("1K", "2K", "4K"), aspect_ratios=ASPECT_RATIOS,
            max_references=MAX_REFERENCES,
        )

    def _payload(self, request: RenderInput) -> bytes:
        if (not isinstance(request.direction, str) or not request.direction.strip()
                or len(request.direction.encode("utf-8")) > 32_000
                or len(request.references) > MAX_REFERENCES
                or request.output.size not in ("1K", "2K", "4K")
                or request.output.aspect_ratio not in ASPECT_RATIOS):
            raise ValueError("invalid_request")
        images = (request.source, *request.references)
        if sum(len(image.data) for image in images) > MAX_INPUT_BYTES:
            raise ValueError("input_too_large")
        content = [{"type": "text", "text": (
            "Render the BASE image using this visual direction:\n" + request.direction.strip()
            + "\nKeep the BASE camera, composition, architecture and object placement."
            " Preserve existing material identities by default; apply material changes explicitly"
            " requested in the visual direction. Use REFERENCE images for the requested materials"
            " and visual qualities, never as the source view."
            " Return exactly one finished image."
        )}]
        for index, image in enumerate(images):
            if hashlib.sha256(image.data).hexdigest() != image.ref.asset_sha256:
                raise ValueError("source_bytes_changed")
            _image_size(image.data, image.mime_type, MAX_IMAGE_BYTES)
            # Keep every byte and the caller's order; never sort or deduplicate refs.
            # P036 refs remain with the Runtime and are not sent to the provider.
            role = "BASE" if index == 0 else f"REFERENCE {index}"
            content.extend([
                {"type": "text", "text": f"Image {index + 1}: {role}"},
                {"type": "image", "mime_type": image.mime_type,
                 "data": base64.b64encode(image.data).decode("ascii")},
            ])
        response_format = {"type": "image", "mime_type": "image/jpeg",
                           "image_size": request.output.size}
        if request.output.aspect_ratio != "source":
            response_format["aspect_ratio"] = request.output.aspect_ratio
        body = canonical_json({"model": self._model, "store": False, "input": content,
                               "response_format": response_format}).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError("request_too_large")
        return body

    def generate(self, request: RenderInput) -> RenderOutput:
        # Separate preflight failures from exceptions after a paid call may have started.
        if not self.capability().available:
            raise RenderProviderError("failed", "not_configured")
        try:
            body = self._payload(request)
        except Exception:
            raise RenderProviderError("failed", "unsupported_input") from None
        try:
            status, _headers, response = self._transport(
                ENDPOINT, {"Content-Type": "application/json", "x-goog-api-key": self._api_key},
                body, self._timeout_s,
            )
            if type(status) is not int or not 100 <= status <= 599:
                raise ValueError("invalid_http_status")
        except Exception as error:
            reason = error.reason if isinstance(error, URLError) else error
            code = "timeout" if isinstance(reason, TimeoutError) else "transport_unknown"
            raise RenderProviderError("unknown", code) from None
        if status in (408, 409, 429) or status >= 500 or 300 <= status < 400:
            raise RenderProviderError("unknown", "timeout" if status == 408 else "transport_unknown")
        if 400 <= status < 500:
            raise RenderProviderError("failed", "provider_rejected")
        if not 200 <= status < 300:
            raise RenderProviderError("unknown", "transport_unknown")
        try:
            if not isinstance(response, bytes) or len(response) > MAX_RESPONSE_BYTES:
                raise ValueError("response_too_large")
            interaction = json.loads(response)
            if not isinstance(interaction, dict):
                raise ValueError("invalid_interaction")
        except Exception:
            raise RenderProviderError("unknown", "invalid_output") from None
        if interaction.get("status") in ("failed", "cancelled"):
            raise RenderProviderError("failed", "provider_rejected")
        if interaction.get("status") != "completed":
            raise RenderProviderError("unknown", "transport_unknown")
        # A completed interaction with unusable output is a terminal failed result.
        # Neither outcome allows an automatic paid replay.
        try:
            return self._output(interaction, request)
        except Exception:
            raise RenderProviderError("failed", "invalid_output") from None

    def _output(self, interaction: dict, request: RenderInput) -> RenderOutput:
        if interaction.get("model", self._model) != self._model:
            raise ValueError("model_changed")
        images = []
        for step in interaction.get("steps", []):
            if step.get("type") == "model_output":
                images.extend(block for block in step.get("content", [])
                              if block.get("type") == "image" and not block.get("thought", False))
        if len(images) != 1:
            raise ValueError("one_final_image_required")
        image = images[0]
        encoded = image.get("data")
        if (not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_OUTPUT_BYTES + 2) // 3)
                or image.get("mime_type", "image/jpeg") != "image/jpeg"):
            raise ValueError("invalid_output")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("invalid_image_encoding") from None
        width, height = _image_size(data, "image/jpeg", MAX_OUTPUT_BYTES)
        # Gemini-specific sanity floors reject thumbnails, not certify a universal
        # resolution class. Actual dimensions remain those of the returned JPEG.
        minimum_edge, minimum_pixels = {"1K": (1000, 500_000), "2K": (2000, 2_000_000),
                                        "4K": (4000, 8_000_000)}[request.output.size]
        if max(width, height) < minimum_edge or width * height < minimum_pixels:
            raise ValueError("output_below_requested_size")
        # Report actual service usage only; the API does not report dollar cost.
        usage = interaction.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        def tokens(name: str) -> int | None:
            value = usage.get(name)
            return value if type(value) is int and 0 <= value <= 10**12 else None

        request_id = interaction.get("id")
        if (not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id)
                or self._api_key in request_id):
            request_id = None
        return RenderOutput(data=data, mime_type="image/jpeg", provider_request_id=request_id,
                            input_tokens=tokens("total_input_tokens"),
                            output_tokens=tokens("total_output_tokens"), cost_usd=None)


def adapter_from_settings(settings: StudioSettings) -> GeminiImageRenderAdapter | None:
    """Use only the Runtime's resolved settings, never environment or secret files."""
    if settings.render_provider != "gemini":
        return None
    return GeminiImageRenderAdapter(api_key=settings.render_api_key, model=settings.render_model,
                                    timeout_s=settings.render_timeout_s)
