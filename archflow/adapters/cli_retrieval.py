"""Bounded read-only JSON CLI retrieval adapter."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass

from archflow.ports.retrieval import (
    RetrievalQuery,
    RetrievalReceipt,
    RetrievalStatus,
    RetrievedEvidence,
)
from archflow.project.refs import require_identifier


@dataclass(frozen=True, slots=True)
class CliProviderSpec:
    provider_id: str
    version: str
    command: tuple[str, ...]
    timeout_seconds: float = 15.0
    max_output_bytes: int = 64_000
    max_results: int = 16
    max_excerpt_chars: int = 4_000

    def __post_init__(self) -> None:
        require_identifier(self.provider_id, "provider_id")
        _text(self.version, "version")
        if not isinstance(self.command, tuple) or not self.command:
            raise ValueError("command must be a non-empty tuple")
        for item in self.command:
            _text(item, "command item", maximum=8_000)
        if not isinstance(self.timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be numeric")
        if not 0 < self.timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be greater than 0 and at most 120")
        if (
            type(self.max_output_bytes) is not int
            or not 256 <= self.max_output_bytes <= 2_000_000
        ):
            raise ValueError("max_output_bytes must be between 256 and 2000000")
        if type(self.max_results) is not int or not 1 <= self.max_results <= 64:
            raise ValueError("max_results must be between 1 and 64")
        if (
            type(self.max_excerpt_chars) is not int
            or not 64 <= self.max_excerpt_chars <= 20_000
        ):
            raise ValueError("max_excerpt_chars must be between 64 and 20000")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "provider_id": self.provider_id,
                "version": self.version,
                "command": list(self.command),
            }
        )


class CliRetrievalAdapter:
    """Calls one configured provider without shell parsing or fallback."""

    def __init__(self, spec: CliProviderSpec) -> None:
        if not isinstance(spec, CliProviderSpec):
            raise TypeError("spec must be a CliProviderSpec")
        self.spec = spec

    def retrieve(self, query: RetrievalQuery) -> RetrievalReceipt:
        if not isinstance(query, RetrievalQuery):
            raise TypeError("query must be a RetrievalQuery")
        input_bytes = (
            json.dumps(
                query.to_payload(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        creation_flags = (
            subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        try:
            completed = subprocess.run(
                self.spec.command,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.spec.timeout_seconds,
                check=False,
                shell=False,
                creationflags=creation_flags,
            )
        except FileNotFoundError:
            return self._failure(
                query,
                RetrievalStatus.OFFLINE,
                "retrieval.provider_unavailable",
                "configured provider executable was not found",
            )
        except subprocess.TimeoutExpired:
            return self._failure(
                query,
                RetrievalStatus.TIMEOUT,
                "retrieval.timeout",
                f"provider exceeded {self.spec.timeout_seconds:g} seconds",
            )
        except OSError as exc:
            return self._failure(
                query,
                RetrievalStatus.OFFLINE,
                "retrieval.provider_os_error",
                f"{type(exc).__name__}: {exc}",
            )

        output_digest = hashlib.sha256(completed.stdout).hexdigest()
        if len(completed.stdout) > self.spec.max_output_bytes:
            return self._failure(
                query,
                RetrievalStatus.OVERSIZED,
                "retrieval.output_oversized",
                f"output exceeds {self.spec.max_output_bytes} bytes",
                output_sha256=output_digest,
            )
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace")[:500]
            return self._failure(
                query,
                RetrievalStatus.EXIT_ERROR,
                "retrieval.provider_exit",
                f"provider exited {completed.returncode}: {message}",
                output_sha256=output_digest,
            )
        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
            results = self._parse_results(query, payload, output_digest)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return self._failure(
                query,
                RetrievalStatus.MALFORMED,
                "retrieval.output_malformed",
                f"{type(exc).__name__}: {exc}",
                output_sha256=output_digest,
            )

        identity = self._identity(
            query,
            RetrievalStatus.SUCCESS,
            output_digest,
            None,
        )
        return RetrievalReceipt(
            schema="RetrievalReceipt@1",
            receipt_id=f"retrieval-{_digest(identity)[:20]}",
            status=RetrievalStatus.SUCCESS,
            query=query,
            provider_id=self.spec.provider_id,
            provider_version=self.spec.version,
            provider_fingerprint=self.spec.fingerprint,
            command=self.spec.command,
            output_sha256=output_digest,
            results=results,
        )

    def _parse_results(
        self,
        query: RetrievalQuery,
        payload: object,
        output_digest: str,
    ) -> tuple[RetrievedEvidence, ...]:
        if not isinstance(payload, dict) or set(payload) != {"schema", "results"}:
            raise ValueError("provider output fields drifted")
        if payload["schema"] != "CliRetrievalOutput@1":
            raise ValueError("provider output schema is unsupported")
        raw_results = payload["results"]
        if not isinstance(raw_results, list):
            raise TypeError("results must be a JSON array")
        if len(raw_results) > self.spec.max_results:
            raise ValueError("provider returned too many results")
        parsed = []
        for index, item in enumerate(raw_results):
            if not isinstance(item, dict) or set(item) != {
                "title",
                "source_uri",
                "excerpt",
            }:
                raise ValueError(f"result {index} fields drifted")
            title = item["title"]
            source_uri = item["source_uri"]
            excerpt = item["excerpt"]
            _text(title, "result title")
            _text(source_uri, "result source_uri", maximum=8_000)
            _text(
                excerpt,
                "result excerpt",
                maximum=self.spec.max_excerpt_chars,
            )
            evidence_digest = _digest(
                {
                    "project_id": query.project_id,
                    "query_id": query.query_id,
                    "base": query.base.require_digest(),
                    "title": title,
                    "source_uri": source_uri,
                    "excerpt": excerpt,
                    "output_sha256": output_digest,
                }
            )
            parsed.append(
                RetrievedEvidence(
                    evidence_id=f"evidence-{evidence_digest[:20]}",
                    project_id=query.project_id,
                    query_id=query.query_id,
                    title=title,
                    source_uri=source_uri,
                    excerpt=excerpt,
                    output_sha256=output_digest,
                )
            )
        return tuple(parsed)

    def _failure(
        self,
        query: RetrievalQuery,
        status: RetrievalStatus,
        error_code: str,
        message: str,
        *,
        output_sha256: str | None = None,
    ) -> RetrievalReceipt:
        identity = self._identity(query, status, output_sha256, error_code)
        return RetrievalReceipt(
            schema="RetrievalReceipt@1",
            receipt_id=f"retrieval-{_digest(identity)[:20]}",
            status=status,
            query=query,
            provider_id=self.spec.provider_id,
            provider_version=self.spec.version,
            provider_fingerprint=self.spec.fingerprint,
            command=self.spec.command,
            output_sha256=output_sha256,
            error_code=error_code,
            message=message[:500],
        )

    def _identity(
        self,
        query: RetrievalQuery,
        status: RetrievalStatus,
        output_sha256: str | None,
        error_code: str | None,
    ) -> dict[str, object]:
        return {
            "query": query.to_payload(),
            "provider_id": self.spec.provider_id,
            "provider_fingerprint": self.spec.fingerprint,
            "status": status.value,
            "output_sha256": output_sha256,
            "error_code": error_code,
        }


def missing_provider_receipt(
    query: RetrievalQuery,
    provider_id: str,
) -> RetrievalReceipt:
    require_identifier(provider_id, "provider_id")
    identity = {
        "query": query.to_payload(),
        "provider_id": provider_id,
        "status": RetrievalStatus.MISSING_PROVIDER.value,
    }
    return RetrievalReceipt(
        schema="RetrievalReceipt@1",
        receipt_id=f"retrieval-{_digest(identity)[:20]}",
        status=RetrievalStatus.MISSING_PROVIDER,
        query=query,
        provider_id=provider_id,
        provider_version="unavailable",
        provider_fingerprint="unavailable",
        command=(),
        output_sha256=None,
        error_code="retrieval.provider_missing",
        message="requested provider is not registered; fallback is forbidden",
    )


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: object, field: str, *, maximum: int = 1_000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be bounded non-empty text")


__all__ = [
    "CliProviderSpec",
    "CliRetrievalAdapter",
    "RetrievalQuery",
    "RetrievalReceipt",
    "RetrievalStatus",
    "RetrievedEvidence",
    "missing_provider_receipt",
]
