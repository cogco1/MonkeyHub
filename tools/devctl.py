"""Bounded development controls for the ArchFlow V4 work registry.

The JSON registry is the machine source for work state.  This tool deliberately
does not run coding agents, edit product code, or merge changes.  It only
performs deterministic registry transitions, scope checks, and map rendering.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "governance" / "work_registry.json"
LOCK_PATH = ROOT / "governance" / ".work_registry.lock"
MAP_PATH = ROOT / "docs" / "DYNAMIC_MAP.md"
MAPPING_ROOT = ROOT / "docs" / "mapping"
SCHEMA = "ArchFlowDevelopmentRegistry@1"
VERIFICATION_SCHEMA = "ArchFlowWorkVerification@1"
CONTEXT_SCHEMA = "ArchFlowWorkContext@1"
STREAMS = ("retirement", "modify", "planning", "archive")
ACTIVE_STATUSES = ("active", "ready", "blocked")
DEFAULT_VERIFICATION_COMMANDS = (
    ("{python}", "-m", "unittest", "discover", "-s", "tests"),
)
MAX_VERIFICATION_COMMANDS = 16
VERIFICATION_TIMEOUT_SECONDS = 600
MANDATORY_ARCHITECTURE_COMMAND = (
    "{python}",
    "tools/archcheck.py",
)
ARCHITECTURE_STATUSES = (
    "proven",
    "partial",
    "missing",
    "planned",
    "blocked",
    "at_risk",
)
MAX_CONTEXT_SOURCES = 8
MAX_CONTEXT_LINES = 160
MAX_CONTEXT_FILE_BYTES = 2_000_000
FORBIDDEN_CONTEXT_ROOTS = frozenset(
    {
        ".git",
        ".runs",
        "__pycache__",
        "build",
        "dist",
        "history",
        "logs",
        "node_modules",
        "probes",
        "sessions",
    }
)
FORBIDDEN_CONTEXT_NAME_PARTS = (
    ".env",
    "credential",
    "id_rsa",
    "private_key",
    "secret",
)


class RegistryError(RuntimeError):
    """A named registry or transition failure."""


@contextmanager
def registry_lock() -> Iterator[None]:
    try:
        LOCK_PATH.mkdir()
    except FileExistsError as exc:
        raise RegistryError(f"registry is locked: {LOCK_PATH}") from exc
    try:
        yield
    finally:
        LOCK_PATH.rmdir()


def load_registry() -> dict[str, Any]:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise RegistryError(f"unsupported schema: {data.get('schema')!r}")
    items = data.get("items")
    if not isinstance(items, list):
        raise RegistryError("items must be a list")
    ids = [item.get("id") for item in items]
    if any(not isinstance(item_id, str) or not item_id for item_id in ids):
        raise RegistryError("every item requires a non-empty string id")
    if len(ids) != len(set(ids)):
        raise RegistryError("duplicate item id")
    valid_ids = set(ids)
    for item in items:
        if item.get("stream") not in STREAMS:
            raise RegistryError(f"{item['id']}: invalid stream")
        unknown = set(item.get("depends_on", ())) - valid_ids
        if unknown:
            raise RegistryError(
                f"{item['id']}: unknown dependencies {sorted(unknown)}"
            )
        card = resolve_repo_path(item.get("card", ""))
        if not card.is_file():
            raise RegistryError(f"{item['id']}: missing card {card}")
    layers = data.get("architecture_layers")
    if not isinstance(layers, list) or not layers:
        raise RegistryError("architecture_layers must be a non-empty list")
    layer_ids = [layer.get("id") for layer in layers if isinstance(layer, dict)]
    if len(layer_ids) != len(layers) or any(
        not isinstance(layer_id, str) or not layer_id for layer_id in layer_ids
    ):
        raise RegistryError("every architecture layer requires a non-empty id")
    if len(layer_ids) != len(set(layer_ids)):
        raise RegistryError("duplicate architecture layer id")
    for layer in layers:
        if layer.get("status") not in ARCHITECTURE_STATUSES:
            raise RegistryError(
                f"{layer['id']}: invalid architecture status "
                f"{layer.get('status')!r}"
            )
        for field in ("name", "responsibility", "gap"):
            if not isinstance(layer.get(field), str) or not layer[field].strip():
                raise RegistryError(f"{layer['id']}: invalid {field}")
        referenced_cards = set(layer.get("evidence_cards", ())) | set(
            layer.get("open_cards", ())
        )
        unknown_cards = referenced_cards - valid_ids
        if unknown_cards:
            raise RegistryError(
                f"{layer['id']}: unknown card references "
                f"{sorted(unknown_cards)}"
            )
    lanes = data.get("roadmap_lanes")
    if not isinstance(lanes, list) or not lanes:
        raise RegistryError("roadmap_lanes must be a non-empty list")
    for index, lane in enumerate(lanes):
        if not isinstance(lane, dict):
            raise RegistryError(f"roadmap_lanes[{index}] must be an object")
        for field in ("name", "purpose"):
            if not isinstance(lane.get(field), str) or not lane[field].strip():
                raise RegistryError(f"roadmap_lanes[{index}]: invalid {field}")
        cards = lane.get("cards")
        if not isinstance(cards, list) or not cards:
            raise RegistryError(f"roadmap_lanes[{index}]: cards must be non-empty")
        unknown_cards = set(cards) - valid_ids
        if unknown_cards:
            raise RegistryError(
                f"roadmap_lanes[{index}]: unknown cards "
                f"{sorted(unknown_cards)}"
            )
    return data


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def resolve_repo_path(relative: str) -> Path:
    if not relative:
        raise RegistryError("empty repository path")
    candidate = (ROOT / relative).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise RegistryError(f"path escapes repository: {relative}") from exc
    return candidate


def find_item(data: dict[str, Any], item_id: str) -> dict[str, Any]:
    for item in data["items"]:
        if item["id"] == item_id:
            return item
    raise RegistryError(f"unknown item: {item_id}")


def dependency_ids_done(data: dict[str, Any]) -> set[str]:
    return {item["id"] for item in data["items"] if item["status"] == "done"}


def is_ready(item: dict[str, Any], done: set[str]) -> bool:
    return item["status"] == "ready" and set(item["depends_on"]) <= done


def sync_completed_architecture_evidence(data: dict[str, Any]) -> None:
    """Move completed cards from open gaps to layer evidence deterministically."""

    done = dependency_ids_done(data)
    for layer in data["architecture_layers"]:
        open_cards = list(layer.get("open_cards", ()))
        completed = [card for card in open_cards if card in done]
        layer["open_cards"] = [
            card for card in open_cards if card not in done
        ]
        evidence = list(layer.get("evidence_cards", ()))
        layer["evidence_cards"] = [
            *evidence,
            *(card for card in completed if card not in evidence),
        ]


def path_in_scope(relative: str, scope: str) -> bool:
    relative_path = Path(relative.replace("\\", "/"))
    scope_path = Path(scope.replace("\\", "/"))
    return relative_path == scope_path or scope_path in relative_path.parents


def check_scope(item: dict[str, Any], paths: list[str]) -> None:
    scopes = item.get("write_scope", [])
    if not scopes:
        raise RegistryError(f"{item['id']}: empty write_scope")
    for raw_path in paths:
        candidate = resolve_repo_path(raw_path)
        relative = candidate.relative_to(ROOT).as_posix()
        if not any(path_in_scope(relative, scope) for scope in scopes):
            raise RegistryError(f"{item['id']}: outside write_scope: {relative}")


def stable_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _card_section_bullets(card_text: str, heading: str) -> tuple[str, ...]:
    marker = f"## {heading}"
    lines = card_text.splitlines()
    try:
        start = lines.index(marker) + 1
    except ValueError as exc:
        raise RegistryError(f"work card is missing {marker}") from exc
    bullets: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
        elif bullets:
            bullets[-1] = f"{bullets[-1]} {stripped}"
        else:
            raise RegistryError(
                f"{marker} must contain only markdown bullets"
            )
    if not bullets:
        raise RegistryError(f"{marker} must contain at least one bullet")
    return tuple(bullets)


def _context_source_specs(item: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    defaults: list[dict[str, Any]] = [
        {
            "path": "AGENTS.md",
            "line_start": 1,
            "line_end": MAX_CONTEXT_LINES,
            "purpose": "repository governance contract",
            "source_kind": "repository_governance",
        },
        {
            "path": item["card"],
            "line_start": 1,
            "line_end": MAX_CONTEXT_LINES,
            "purpose": "current work-card contract",
            "source_kind": "work_card",
        },
    ]
    raw_specs = item.get("context_files", [])
    if not isinstance(raw_specs, list):
        raise RegistryError(f"{item['id']}: context_files must be a list")
    if len(defaults) + len(raw_specs) > MAX_CONTEXT_SOURCES:
        raise RegistryError(
            f"{item['id']}: context sources exceed {MAX_CONTEXT_SOURCES}"
        )
    for index, raw in enumerate(raw_specs):
        if not isinstance(raw, dict) or set(raw) != {
            "path",
            "line_start",
            "line_end",
            "purpose",
        }:
            raise RegistryError(
                f"{item['id']}: context_files[{index}] schema is invalid"
            )
        defaults.append(
            {
                **raw,
                "source_kind": "explicit_relevant_source",
            }
        )
    return tuple(defaults)


def _context_source(spec: dict[str, Any]) -> dict[str, Any]:
    raw_path = spec["path"]
    purpose = spec["purpose"]
    start = spec["line_start"]
    end = spec["line_end"]
    if not isinstance(raw_path, str) or not raw_path:
        raise RegistryError("context source path must be non-empty text")
    if not isinstance(purpose, str) or not purpose.strip():
        raise RegistryError(
            f"{raw_path}: context source purpose must be non-empty"
        )
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 1
        or end < start
        or end - start + 1 > MAX_CONTEXT_LINES
    ):
        raise RegistryError(
            f"{raw_path}: context excerpt must contain 1.."
            f"{MAX_CONTEXT_LINES} lines"
        )
    path = resolve_repo_path(raw_path)
    relative = path.relative_to(ROOT).as_posix()
    lowered_parts = tuple(part.casefold() for part in Path(relative).parts)
    if (
        any(part in FORBIDDEN_CONTEXT_ROOTS for part in lowered_parts)
        or any(
            token in part
            for part in lowered_parts
            for token in FORBIDDEN_CONTEXT_NAME_PARTS
        )
        or (
            spec["source_kind"] == "explicit_relevant_source"
            and relative.startswith("docs/mapping/")
        )
    ):
        raise RegistryError(f"forbidden context source: {relative}")
    if not path.is_file():
        raise RegistryError(f"missing context source: {relative}")
    payload = path.read_bytes()
    if len(payload) > MAX_CONTEXT_FILE_BYTES:
        raise RegistryError(f"context source is too large: {relative}")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegistryError(
            f"context source is not UTF-8 text: {relative}"
        ) from exc
    lines = text.splitlines()
    if start > len(lines):
        raise RegistryError(
            f"context excerpt starts after EOF: {relative}:{start}"
        )
    actual_end = min(end, len(lines))
    excerpt = "\n".join(lines[start - 1 : actual_end])
    return {
        "path": relative,
        "purpose": purpose.strip(),
        "source_kind": spec["source_kind"],
        "line_start": start,
        "line_end": actual_end,
        "line_count": actual_end - start + 1,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "excerpt_sha256": hashlib.sha256(
            excerpt.encode("utf-8")
        ).hexdigest(),
        "excerpt": excerpt,
    }


def build_context_capsule(
    data: dict[str, Any],
    item_id: str,
) -> dict[str, Any]:
    """Compile one deterministic, bounded, read-only work context."""

    item = find_item(data, item_id)
    card = resolve_repo_path(item["card"])
    card_text = card.read_text(encoding="utf-8")
    dependencies = []
    for dependency_id in item.get("depends_on", ()):
        dependency = find_item(data, dependency_id)
        dependencies.append(
            {
                "id": dependency_id,
                "status": dependency["status"],
                "stream": dependency["stream"],
            }
        )
    receipt = item.get("verification_receipt")
    verified = (
        isinstance(receipt, dict)
        and receipt.get("schema") == VERIFICATION_SCHEMA
        and receipt.get("item_id") == item_id
        and isinstance(receipt.get("commands"), list)
        and bool(receipt["commands"])
        and all(
            isinstance(result, dict)
            and result.get("returncode") == 0
            for result in receipt["commands"]
        )
    )
    verification = {
        "label": (
            "machine_verification_receipt"
            if verified
            else "no_machine_verification_receipt"
        ),
        "verified": verified,
    }
    if verified:
        verification.update(
            {
                "verified_at": receipt.get("verified_at"),
                "contract_sha256": receipt.get("contract_sha256"),
                "scope_sha256": receipt.get("scope_sha256"),
                "command_count": len(receipt["commands"]),
            }
        )
    sources = tuple(
        _context_source(spec) for spec in _context_source_specs(item)
    )
    capsule: dict[str, Any] = {
        "schema": CONTEXT_SCHEMA,
        "item": {
            "id": item_id,
            "status": item["status"],
            "stream": item["stream"],
            "actor": item.get("actor"),
            "dependencies": dependencies,
            "write_scope": list(item.get("write_scope", ())),
        },
        "planning_claims": {
            "label": "unverified_planning_contract",
            "goal": item["goal"],
            "acceptance": list(item.get("acceptance", ())),
            "tests": list(item.get("tests", ())),
            "stop_conditions": list(
                _card_section_bullets(card_text, "Stop conditions")
            ),
        },
        "repository_evidence": {
            "label": "repository_recorded_evidence",
            "completion_evidence": list(item.get("evidence", ())),
            "verification": verification,
        },
        "sources": list(sources),
        "authority": {
            "read_only": True,
            "can_claim": False,
            "can_modify": False,
            "can_verify": False,
            "can_complete": False,
            "project_state_authority": False,
            "evidence_authority": False,
        },
        "exclusions": [
            "raw_history",
            "unrelated_work_cards",
            "probe_data",
            "secrets",
            "sibling_work",
        ],
    }
    capsule["capsule_sha256"] = stable_digest(capsule)
    return capsule


def command_context(data: dict[str, Any], item_id: str) -> None:
    print(
        json.dumps(
            build_context_capsule(data, item_id),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def verification_contract(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "id",
            "origin_stream",
            "stream",
            "goal",
            "depends_on",
            "write_scope",
            "acceptance",
            "tests",
            "verification_commands",
            "context_files",
            "card",
            "actor",
        )
    }


def verification_contract_digest(item: dict[str, Any]) -> str:
    return stable_digest(verification_contract(item))


def _scope_files(item: dict[str, Any]) -> tuple[Path, ...]:
    files: set[Path] = set()
    for raw_scope in item.get("write_scope", ()):
        scope = resolve_repo_path(raw_scope)
        if scope == REGISTRY_PATH:
            continue
        if scope.is_file():
            files.add(scope)
            continue
        if not scope.exists():
            continue
        files.update(
            path
            for path in scope.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
            and path != REGISTRY_PATH
            and path != LOCK_PATH
        )
    return tuple(
        sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())
    )


def verification_scope_digest(item: dict[str, Any]) -> str:
    hasher = hashlib.sha256()
    for path in _scope_files(item):
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        hasher.update(len(relative).to_bytes(8, "big"))
        hasher.update(relative)
        payload = path.read_bytes()
        hasher.update(len(payload).to_bytes(8, "big"))
        hasher.update(payload)
    return hasher.hexdigest()


def normalized_verification_commands(
    item: dict[str, Any],
) -> tuple[tuple[str, ...], ...]:
    raw_commands = item.get("verification_commands")
    commands = (
        DEFAULT_VERIFICATION_COMMANDS
        if raw_commands is None
        else raw_commands
    )
    if (
        not isinstance(commands, (list, tuple))
        or not commands
        or len(commands) > MAX_VERIFICATION_COMMANDS
    ):
        raise RegistryError(
            f"{item['id']}: verification_commands must contain "
            f"1..{MAX_VERIFICATION_COMMANDS} commands"
        )
    normalized: list[tuple[str, ...]] = []
    for index, command in enumerate(commands):
        if (
            not isinstance(command, (list, tuple))
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise RegistryError(
                f"{item['id']}: invalid verification command {index}"
            )
        normalized.append(
            tuple(
                sys.executable if part == "{python}" else part
                for part in command
            )
        )
    return tuple(normalized)


def required_verification_commands(
    item: dict[str, Any],
) -> tuple[tuple[str, ...], ...]:
    commands = normalized_verification_commands(item)
    architecture_command = tuple(
        sys.executable if part == "{python}" else part
        for part in MANDATORY_ARCHITECTURE_COMMAND
    )
    if architecture_command in commands:
        return commands
    return (architecture_command, *commands)


def _output_tail(value: str, limit: int = 1200) -> str:
    return value[-limit:]


def _run_verification_commands(
    item: dict[str, Any],
    commands: tuple[tuple[str, ...], ...],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=VERIFICATION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RegistryError(
                f"{item['id']}: verification timed out after "
                f"{VERIFICATION_TIMEOUT_SECONDS}s: {command!r}"
            ) from exc
        result = {
            "argv": list(command),
            "returncode": completed.returncode,
            "stdout_sha256": hashlib.sha256(
                completed.stdout.encode("utf-8")
            ).hexdigest(),
            "stderr_sha256": hashlib.sha256(
                completed.stderr.encode("utf-8")
            ).hexdigest(),
            "stdout_tail": _output_tail(completed.stdout),
            "stderr_tail": _output_tail(completed.stderr),
        }
        results.append(result)
        if completed.returncode != 0:
            detail = completed.stderr or completed.stdout or "(no output)"
            raise RegistryError(
                f"{item['id']}: verification command failed "
                f"({completed.returncode}): {command!r}\n"
                f"{_output_tail(detail)}"
            )
    return results


def require_current_verification(item: dict[str, Any]) -> None:
    receipt = item.get("verification_receipt")
    if not isinstance(receipt, dict):
        raise RegistryError(
            f"{item['id']}: missing verification receipt; run devctl verify"
        )
    if receipt.get("schema") != VERIFICATION_SCHEMA:
        raise RegistryError(f"{item['id']}: malformed verification receipt")
    if receipt.get("item_id") != item["id"]:
        raise RegistryError(f"{item['id']}: verification item mismatch")
    if receipt.get("actor") != item.get("actor"):
        raise RegistryError(f"{item['id']}: verification actor mismatch")
    results = receipt.get("commands")
    if (
        not isinstance(results, list)
        or not results
        or any(
            not isinstance(result, dict)
            or result.get("returncode") != 0
            for result in results
        )
    ):
        raise RegistryError(f"{item['id']}: verification did not pass")
    if receipt.get("contract_sha256") != verification_contract_digest(item):
        raise RegistryError(
            f"{item['id']}: work contract changed after verification"
        )
    if receipt.get("scope_sha256") != verification_scope_digest(item):
        raise RegistryError(
            f"{item['id']}: source state changed after verification"
        )


def command_verify(item_id: str) -> None:
    with registry_lock():
        before_data = load_registry()
        before_item = find_item(before_data, item_id)
        if before_item["status"] != "active":
            raise RegistryError(f"{item_id}: only active work can verify")
        if not before_item.get("actor"):
            raise RegistryError(f"{item_id}: active work requires an actor")
        before_contract = verification_contract_digest(before_item)
        commands = required_verification_commands(before_item)

    results = _run_verification_commands(before_item, commands)

    with registry_lock():
        data = load_registry()
        item = find_item(data, item_id)
        if item["status"] != "active":
            raise RegistryError(f"{item_id}: state changed during verification")
        if verification_contract_digest(item) != before_contract:
            raise RegistryError(
                f"{item_id}: work contract changed during verification"
            )
        item["verification_receipt"] = {
            "schema": VERIFICATION_SCHEMA,
            "item_id": item_id,
            "actor": item["actor"],
            "verified_at": datetime.now(UTC).isoformat(),
            "contract_sha256": before_contract,
            "scope_sha256": verification_scope_digest(item),
            "commands": results,
        }
        atomic_write_json(REGISTRY_PATH, data)
    print(f"{item_id}: verification PASS ({len(results)} commands)")


def render_map(data: dict[str, Any]) -> str:
    done_ids = dependency_ids_done(data)
    ready_ids = [
        item["id"] for item in data["items"] if is_ready(item, done_ids)
    ]
    active_ids = [
        item["id"] for item in data["items"] if item["status"] == "active"
    ]
    if active_ids:
        next_transition = f"finish active work: {', '.join(active_ids)}"
    elif ready_ids:
        next_transition = f"explicitly claim: {', '.join(ready_ids)}"
    else:
        next_transition = "no dependency-ready work; integration decision required"
    counts = {
        stream: sum(1 for item in data["items"] if item["stream"] == stream)
        for stream in STREAMS
    }
    lines = [
        "# ArchFlow V4 Dynamic Map",
        "",
        "> Generated by `python tools/devctl.py render-map` from",
        "> `governance/work_registry.json`. Architecture rationale remains in",
        "> [ARCHITECTURE.md](ARCHITECTURE.md).",
        "",
        "## Current state",
        "",
        f"- Phase: **{data['phase']}**",
        f"- Registry updated: **{data['updated_on']}**",
        (
            "- Product capability: "
            f"**runtime={data['modules']['runtime']}; "
            f"adapters={data['modules']['adapters']}**"
        ),
        f"- Next bounded transition: {next_transition}",
        "",
        "## Architecture coverage",
        "",
        (
            "Coverage status describes the target architecture, not merely whether "
            "a card exists. A fixture or interface can be evidence while the layer "
            "remains partial, missing, blocked, or at risk."
        ),
        "",
        "| Layer | Status | Responsibility | Evidence | Open work | Gap |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    by_id = {item["id"]: item for item in data["items"]}

    def card_links(card_ids: list[str]) -> str:
        if not card_ids:
            return "—"
        return ", ".join(
            (
                f"[{item_id}]"
                f"({by_id[item_id]['card'].removeprefix('docs/')})"
                f" `{by_id[item_id]['status']}`"
            )
            for item_id in card_ids
        )

    for layer in data["architecture_layers"]:
        responsibility = layer["responsibility"].replace("|", "/")
        gap = layer["gap"].replace("|", "/")
        lines.append(
            f"| {layer['id']} {layer['name']} | **{layer['status']}** | "
            f"{responsibility} | {card_links(layer['evidence_cards'])} | "
            f"{card_links(layer['open_cards'])} | {gap} |"
        )
    lines.extend(
        [
            "",
            "## Dependency lanes",
            "",
            (
                "These are the main responsibility chains. Individual cards retain "
                "their exact fan-in dependencies in the registry."
            ),
            "",
            "| Lane | Main chain | Purpose |",
            "| --- | --- | --- |",
        ]
    )
    for lane in data["roadmap_lanes"]:
        chain = " → ".join(
            f"[{item_id}]({by_id[item_id]['card'].removeprefix('docs/')})"
            for item_id in lane["cards"]
        )
        purpose = lane["purpose"].replace("|", "/")
        lines.append(f"| {lane['name']} | {chain} | {purpose} |")
    lines.extend(
        [
        "",
        "## RMPA ledgers",
        "",
        "| Ledger | Meaning | Items | Index |",
        "| --- | --- | ---: | --- |",
        f"| Retirement | Replacement-first removal of old ownership | {counts['retirement']} | [R](mapping/retirement/INDEX.md) |",
        f"| Modify | Scoped repair of the active path | {counts['modify']} | [M](mapping/modify/INDEX.md) |",
        f"| Planning | Not implemented or not yet accepted | {counts['planning']} | [P](mapping/planning/INDEX.md) |",
        f"| Archive | Completed and evidenced work | {counts['archive']} | [A](mapping/archive/INDEX.md) |",
        "",
        "R, M, and P are parallel ledgers, not mandatory runtime stages. Archive is",
        "the cleared construction record, not a fourth execution phase.",
        "",
        "## Active and ready work",
        "",
        "| ID | Stream | Status | Goal | Card |",
        "| --- | --- | --- | --- | --- |",
        ]
    )
    active = [
        item
        for item in data["items"]
        if item["stream"] != "archive" and item["status"] in ACTIVE_STATUSES
    ]
    if active:
        for item in active:
            goal = item["goal"].replace("|", "/")
            lines.append(
                f"| {item['id']} | {item['stream']} | {item['status']} | "
                f"{goal} | [{item['id']}]({item['card'].removeprefix('docs/')}) |"
            )
    else:
        lines.append("| - | - | - | No active work | - |")
    lines.extend(
        [
            "",
            "## Module state",
            "",
            "| Module | Status | Responsibility |",
            "| --- | --- | --- |",
        ]
    )
    responsibilities = {
        "state": "Canonical decision state, never raw transcript",
        "runtime": "Bounded Architect session coordination",
        "workspace": "Isolated mutable drafts and tool outputs",
        "capabilities": "Open discovery without fixed expert pipeline",
        "submission": "Only boundary from working to formal review",
        "validation": "Read-only deterministic gates and obligations",
        "evaluation": "Read-only multi-objective observations",
        "commit": "Single-writer canonical promotion",
        "adapters": "External MCP/CLI/model/storage boundaries",
        "project": "Project identity, layout, references, and persistence ports",
    }
    for module, status in data["modules"].items():
        lines.append(
            f"| [{module}](../archflow/{module}/README.md) | {status} | "
            f"{responsibilities[module]} |"
        )
    lines.extend(
        [
            "",
            "## Promotion boundary",
            "",
            "```text",
            "Canonical C_v + raw brief/evidence",
            "        -> compile branch-local D_v,0",
            "        -> Architect + state-responsive experts",
            "        -> Proposal/Delta -> verified transition -> D_v,k+1",
            "        -> CandidateSubmission",
            "        -> hard usability + commitment monitor",
            "        -> separate read-only aesthetic observations",
            "        -> single-writer commit event -> reducer -> C_v+1",
            "```",
            "",
            "Hard gates constrain submitted artifacts, not the Architect's internal",
            "reasoning or draft tool sequence. Complete history and raw MCP receipts",
            "remain outside canonical state.",
            "",
            "## Change rules",
            "",
            "1. Change machine work state only through `tools/devctl.py`.",
            "2. A work item may write only inside its declared `write_scope`.",
            "3. Completion requires named evidence and moves the card to Archive.",
            "4. A soft evaluator cannot waive a failed hard gate.",
            "5. Do not claim replay until an executor reproduces transitions.",
            "6. Real MCP or LLM behavior requires its own claimed work item and",
            "   an explicit bounded smoke test; registry readiness alone is not a claim.",
            "",
            "![V4 bounded-agency architecture](diagrams/v4-bounded-agency.svg)",
            "",
        ]
    )
    return "\n".join(lines)


def render_index(data: dict[str, Any], stream: str) -> str:
    titles = {
        "retirement": (
            "Retirement ledger",
            "Which old responsibility may leave after a named replacement "
            "demonstrably owns it?",
        ),
        "modify": (
            "Modify ledger",
            "Which observed defect in the active path needs a narrow repair?",
        ),
        "planning": (
            "Planning ledger",
            "Which capability is not yet implemented or accepted?",
        ),
        "archive": (
            "Archive ledger",
            "Which completed work has named acceptance evidence?",
        ),
    }
    title, question = titles[stream]
    items = [item for item in data["items"] if item["stream"] == stream]
    lines = [
        f"# {title}",
        "",
        "> Generated from `governance/work_registry.json`.",
        "",
        question,
        "",
        "| ID | Origin | Status | Goal | Card |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not items:
        lines.append("| - | - | - | No cards | - |")
    for item in items:
        card = Path(item["card"]).name
        goal = item["goal"].replace("|", "/")
        lines.append(
            f"| {item['id']} | {item['origin_stream']} | {item['status']} | "
            f"{goal} | [{item['id']}]({card}) |"
        )
    lines.extend(["", "[Back to RMPA](../README.md)", ""])
    return "\n".join(lines)


def render_documents(data: dict[str, Any]) -> None:
    MAP_PATH.write_text(render_map(data), encoding="utf-8", newline="\n")
    for stream in STREAMS:
        index = MAPPING_ROOT / stream / "INDEX.md"
        index.write_text(
            render_index(data, stream),
            encoding="utf-8",
            newline="\n",
        )


def command_status(data: dict[str, Any]) -> None:
    for item in data["items"]:
        print(f"{item['id']} | {item['stream']} | {item['status']} | {item['goal']}")


def command_next(data: dict[str, Any]) -> None:
    done = dependency_ids_done(data)
    ready = [item for item in data["items"] if is_ready(item, done)]
    if not ready:
        print("NO_READY_WORK")
        return
    for item in ready:
        print(f"{item['id']} | {item['goal']}")


def command_claim(data: dict[str, Any], item_id: str, actor: str) -> None:
    item = find_item(data, item_id)
    done = dependency_ids_done(data)
    if not is_ready(item, done):
        raise RegistryError(f"{item_id}: not ready or dependencies incomplete")
    item["status"] = "active"
    item["actor"] = actor
    item.pop("verification_receipt", None)
    data["updated_on"] = date.today().isoformat()
    atomic_write_json(REGISTRY_PATH, data)
    render_documents(data)


def command_complete(
    data: dict[str, Any], item_id: str, evidence: list[str]
) -> None:
    if not evidence:
        raise RegistryError("completion requires at least one evidence string")
    item = find_item(data, item_id)
    if item["status"] != "active":
        raise RegistryError(f"{item_id}: only active work can complete")
    require_current_verification(item)
    source_card = resolve_repo_path(item["card"])
    target_card = ROOT / "docs" / "mapping" / "archive" / source_card.name
    if target_card.exists():
        raise RegistryError(f"archive card already exists: {target_card}")
    origin_stream = item["stream"]
    original_card_text = source_card.read_text(encoding="utf-8")
    shutil.move(str(source_card), str(target_card))
    original = dict(item)
    try:
        archived_card_text = original_card_text.replace(
            "- Status: Active",
            "- Status: Done",
            1,
        )
        archived_card_text += (
            f"\n\n## Completion\n\n- Completed: {date.today().isoformat()}\n"
            + "\n".join(f"- Evidence: {entry}" for entry in evidence)
            + "\n"
        )
        target_card.write_text(
            archived_card_text,
            encoding="utf-8",
            newline="\n",
        )
        item["origin_stream"] = origin_stream
        item["stream"] = "archive"
        item["status"] = "done"
        item["completed_on"] = date.today().isoformat()
        item["evidence"] = evidence
        item["card"] = target_card.relative_to(ROOT).as_posix()
        item.pop("actor", None)
        on_complete = item.get("on_complete", {})
        if "phase" in on_complete:
            data["phase"] = on_complete["phase"]
        module_updates = on_complete.get("modules", {})
        unknown_modules = set(module_updates) - set(data["modules"])
        if unknown_modules:
            raise RegistryError(
                f"{item_id}: unknown on_complete modules "
                f"{sorted(unknown_modules)}"
            )
        data["modules"].update(module_updates)
        sync_completed_architecture_evidence(data)
        data["updated_on"] = date.today().isoformat()
        atomic_write_json(REGISTRY_PATH, data)
    except BaseException:
        item.clear()
        item.update(original)
        target_card.write_text(
            original_card_text,
            encoding="utf-8",
            newline="\n",
        )
        shutil.move(str(target_card), str(source_card))
        raise
    render_documents(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("next")
    subparsers.add_parser("render-map")
    context = subparsers.add_parser("context")
    context.add_argument("item_id")
    claim = subparsers.add_parser("claim")
    claim.add_argument("item_id")
    claim.add_argument("--actor", required=True)
    scope = subparsers.add_parser("check-scope")
    scope.add_argument("item_id")
    scope.add_argument("paths", nargs="*")
    verify = subparsers.add_parser("verify")
    verify.add_argument("item_id")
    complete = subparsers.add_parser("complete")
    complete.add_argument("item_id")
    complete.add_argument("--evidence", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "verify":
            command_verify(args.item_id)
            return 0
        if args.command == "context":
            command_context(load_registry(), args.item_id)
            return 0
        with registry_lock():
            data = load_registry()
            if args.command == "status":
                command_status(data)
            elif args.command == "next":
                command_next(data)
            elif args.command == "render-map":
                render_documents(data)
            elif args.command == "claim":
                command_claim(data, args.item_id, args.actor)
            elif args.command == "check-scope":
                item = find_item(data, args.item_id)
                paths = args.paths or item["write_scope"]
                check_scope(item, paths)
                print(f"{args.item_id}: scope PASS ({len(paths)} paths)")
            elif args.command == "complete":
                command_complete(data, args.item_id, args.evidence)
    except (OSError, json.JSONDecodeError, RegistryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
