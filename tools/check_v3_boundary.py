"""Deterministic ownership and dependency-backflow audit for frozen V3 use."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


POLICY_SCHEMA = "V3LegacyOwnershipPolicy@1"
MANIFEST_SCHEMA = "V3LegacyResponsibilityManifest@1"
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True, order=True)
class Finding:
    path: str
    line: int
    code: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "path": self.path,
            "line": self.line,
            "message": self.message,
        }


class V3BoundaryPolicyError(ValueError):
    """The ownership declaration itself is incomplete or contradictory."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V3BoundaryPolicyError(f"cannot load JSON: {path}") from exc
    if not isinstance(value, dict):
        raise V3BoundaryPolicyError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_matches(target: str, prefix: str) -> bool:
    return target == prefix or target.startswith(prefix + ".")


def _module_name(relative: str) -> str:
    value = relative.removesuffix(".py").replace("/", ".")
    return value.removesuffix(".__init__")


def _imports(tree: ast.AST) -> Iterable[tuple[str, str | None, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, alias.name.rsplit(".", 1)[-1], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.names:
                for alias in node.names:
                    yield node.module, alias.name, node.lineno
            else:
                yield node.module, None, node.lineno


def _used_symbols(tree: ast.AST) -> Iterable[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.id, node.lineno
        elif isinstance(node, ast.Attribute):
            yield node.attr, node.lineno


def _string_literals(tree: ast.AST) -> Iterable[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node.lineno


def _parse(path: Path, root: Path) -> tuple[ast.Module | None, Finding | None]:
    relative = path.relative_to(root).as_posix()
    try:
        return ast.parse(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        line = exc.lineno if isinstance(exc, SyntaxError) and exc.lineno else 1
        return None, Finding(relative, line, "V3_PARSE_ERROR", str(exc))


def validate_ownership(
    root: Path,
    policy: dict[str, Any],
) -> list[Finding]:
    if policy.get("schema") != POLICY_SCHEMA:
        raise V3BoundaryPolicyError("unsupported ownership policy schema")
    source = policy.get("source_manifest")
    handover = policy.get("handover")
    scan = policy.get("production_scan")
    if not all(isinstance(item, dict) for item in (source, handover, scan)):
        raise V3BoundaryPolicyError(
            "source_manifest, handover, and production_scan must be objects"
        )

    findings: list[Finding] = []
    manifest_path = root / source.get("path", "")
    if not manifest_path.is_file():
        raise V3BoundaryPolicyError("source manifest is missing")
    if _sha256(manifest_path) != source.get("sha256"):
        findings.append(
            Finding(
                source["path"],
                1,
                "V3_MANIFEST_DRIFT",
                "ownership policy no longer binds the classified V3 manifest",
            )
        )
    manifest = _load_json(manifest_path)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise V3BoundaryPolicyError("unsupported V3 responsibility manifest")
    selected = source.get("selected_responsibility_id")
    if (
        selected != manifest.get("selected_pilot_id")
        or selected != handover.get("responsibility_id")
    ):
        findings.append(
            Finding(
                source["path"],
                1,
                "V3_RESPONSIBILITY_DRIFT",
                "manifest selection and ownership handover disagree",
            )
        )
    manifest_source = manifest.get("source_repository", {})
    if source.get("v3_source_commit") != manifest_source.get("git_commit"):
        findings.append(
            Finding(
                source["path"],
                1,
                "V3_SOURCE_REVISION_DRIFT",
                "ownership record does not bind the classified V3 revision",
            )
        )

    contract = handover.get("production_contract", {})
    owners = handover.get("production_owners")
    writers = handover.get("writer_owners")
    if (
        not isinstance(owners, list)
        or owners != [contract.get("owner_id")]
        or not isinstance(owners[0], str)
    ):
        findings.append(
            Finding(
                "governance/v3_legacy_ownership.json",
                1,
                "V3_OWNER_CONFLICT",
                "the retained responsibility must have exactly one V4 contract owner",
            )
        )
    if writers != []:
        findings.append(
            Finding(
                "governance/v3_legacy_ownership.json",
                1,
                "V3_WRITER_CONFLICT",
                "the read-only retained responsibility must have zero writers",
            )
        )
    providers = handover.get("providers", {})
    native = providers.get("native", {})
    legacy = providers.get("legacy", {})
    if native.get("production_authority") is not False:
        findings.append(
            Finding(
                "governance/v3_legacy_ownership.json",
                1,
                "V3_NATIVE_AUTHORITY",
                "an unimplemented native provider cannot own production authority",
            )
        )
    required_legacy = {
        "provider_id",
        "provider_version",
        "capability_id",
        "v3_fingerprint",
        "bridge_module",
        "consumer_module",
        "receipt_schema",
    }
    if (
        not required_legacy.issubset(legacy)
        or legacy.get("explicit_selection_required") is not True
        or legacy.get("fallback_allowed") is not False
        or legacy.get("production_authority") is not False
        or legacy.get("capability_id") != contract.get("capability_id")
    ):
        findings.append(
            Finding(
                "governance/v3_legacy_ownership.json",
                1,
                "V3_PROVIDER_AUTHORITY",
                "legacy use must be explicit, receipt-bound, non-fallback, and non-authoritative",
            )
        )
    required_receipt_fields = set(legacy.get("receipt_required_fields", ()))
    if not {
        "provider_id",
        "provider_version",
        "provider_fingerprint",
        "v3_fingerprint",
        "fallback_attempted",
    }.issubset(required_receipt_fields):
        findings.append(
            Finding(
                "governance/v3_legacy_ownership.json",
                1,
                "V3_RECEIPT_IDENTITY_MISSING",
                "legacy receipts must preserve provider and frozen-source identity",
            )
        )

    retirement = handover.get("retirement", {})
    if (
        retirement.get("meaning") != "zero_production_authority"
        or retirement.get("oracle_preserved") is not True
    ):
        findings.append(
            Finding(
                "governance/v3_legacy_ownership.json",
                1,
                "V3_RETIREMENT_SEMANTICS",
                "retirement must remove authority without deleting oracle evidence",
            )
        )
    return findings


def scan_production(
    root: Path,
    policy: dict[str, Any],
) -> list[Finding]:
    scan = policy["production_scan"]
    source_root = root / scan["root"]
    forbidden_modules = tuple(scan["forbidden_module_prefixes"])
    forbidden_symbols = set(scan["forbidden_symbols"])
    forbidden_literals = tuple(
        value.casefold() for value in scan["forbidden_path_literals"]
    )
    allowed_consumers = set(scan["allowed_legacy_bridge_consumers"])
    bridge_module = policy["handover"]["providers"]["legacy"]["bridge_module"]
    findings: list[Finding] = []

    for path in sorted(source_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        tree, parse_finding = _parse(path, root)
        if parse_finding is not None:
            findings.append(parse_finding)
            continue
        assert tree is not None
        for target, symbol, line in _imports(tree):
            if any(
                _module_matches(target, prefix)
                for prefix in forbidden_modules
            ):
                findings.append(
                    Finding(
                        relative,
                        line,
                        "V3_IMPORT_BACKFLOW",
                        f"production imports retired V3 surface {target!r}",
                    )
                )
            if (
                _module_matches(target, bridge_module)
                and relative not in allowed_consumers
            ):
                findings.append(
                    Finding(
                        relative,
                        line,
                        "V3_BRIDGE_BYPASS",
                        "only the declared capability consumer may import the V3 bridge",
                    )
                )
            if symbol in forbidden_symbols:
                findings.append(
                    Finding(
                        relative,
                        line,
                        "V3_SYMBOL_BACKFLOW",
                        f"production imports retired V3 symbol {symbol!r}",
                    )
                )
        if relative != "archflow/adapters/v3_legacy_cli.py":
            for symbol, line in _used_symbols(tree):
                if symbol in forbidden_symbols:
                    findings.append(
                        Finding(
                            relative,
                            line,
                            "V3_SYMBOL_BACKFLOW",
                            f"production uses retired V3 symbol {symbol!r}",
                        )
                    )
        for value, line in _string_literals(tree):
            folded = value.casefold()
            if any(item in folded for item in forbidden_literals):
                findings.append(
                    Finding(
                        relative,
                        line,
                        "V3_PATH_BACKFLOW",
                        "production embeds a machine-specific V3 repository path",
                    )
                )
    return findings


def validate_oracles(
    root: Path,
    policy: dict[str, Any],
) -> list[Finding]:
    legacy = policy["handover"]["providers"]["legacy"]
    findings: list[Finding] = []
    for item in policy["handover"]["oracle_evidence"]:
        relative = item.get("path", "")
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            raise V3BoundaryPolicyError("oracle path escapes repository")
        if not path.is_file():
            findings.append(
                Finding(relative, 1, "V3_ORACLE_MISSING", "oracle evidence is missing")
            )
            continue
        if _sha256(path) != item.get("sha256"):
            findings.append(
                Finding(relative, 1, "V3_ORACLE_DRIFT", "oracle evidence digest drifted")
            )
            continue
        evidence = _load_json(path)
        receipt = evidence.get("diagnostic_receipt", {}).get(
            "provider_receipt",
            {},
        )
        if (
            evidence.get("schema") != "V3DiagnosticProbeEvidence@1"
            or evidence.get("generation_authority") is not False
            or evidence.get("architectural_usability_proven") is not False
            or evidence.get("canonical_write_authority") is not False
            or receipt.get("schema") != legacy["receipt_schema"]
            or receipt.get("provider_id") != legacy["provider_id"]
            or receipt.get("provider_version") != legacy["provider_version"]
            or receipt.get("capability_id") != legacy["capability_id"]
            or receipt.get("v3_fingerprint") != legacy["v3_fingerprint"]
            or receipt.get("fallback_attempted") is not False
            or receipt.get("canonical_write_authority") is not False
            or receipt.get("live_world_authority") is not False
            or receipt.get("persistence_authority") is not False
        ):
            findings.append(
                Finding(
                    relative,
                    1,
                    "V3_ORACLE_AUTHORITY_DRIFT",
                    "oracle is not a versioned non-authoritative provider receipt",
                )
            )
    return findings


def run_checks(
    root: Path = ROOT,
    policy: dict[str, Any] | None = None,
) -> list[Finding]:
    active_policy = policy or _load_json(
        root / "governance" / "v3_legacy_ownership.json"
    )
    return sorted(
        (
            *validate_ownership(root, active_policy),
            *scan_production(root, active_policy),
            *validate_oracles(root, active_policy),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        findings = run_checks(args.root.resolve())
    except V3BoundaryPolicyError as exc:
        print(f"V3 boundary policy error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "schema": "V3BoundaryAudit@1",
                    "finding_count": len(findings),
                    "findings": [item.to_dict() for item in findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif findings:
        for item in findings:
            print(
                f"{item.path}:{item.line}: {item.code}: {item.message}",
                file=sys.stderr,
            )
    else:
        source_root = args.root / "archflow"
        count = sum(1 for path in source_root.rglob("*.py"))
        print(f"V3 boundary PASS ({count} production files, 2 frozen oracles)")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
