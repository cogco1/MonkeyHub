from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


CONTEXT_SCHEMA = "ArchFlowCompactionRecovery@1"
MAX_STDIN_BYTES = 65_536
MAX_ACTIVE_CARDS = 4
MAX_DIRTY_PATHS = 40
MAX_CONTEXT_CHARS = 9_000
COMMAND_TIMEOUT_SECONDS = 10


class RecoveryError(RuntimeError):
    """Bounded failure with a stable non-sensitive code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _repo_root(payload: dict[str, Any]) -> Path:
    root = Path(__file__).resolve().parents[2]
    required = (
        root / "AGENTS.md",
        root / "governance" / "work_registry.json",
        root / "tools" / "devctl.py",
    )
    if not all(path.is_file() for path in required):
        raise RecoveryError("repository-contract-missing")
    raw_cwd = payload.get("cwd")
    if not isinstance(raw_cwd, str) or not raw_cwd:
        raise RecoveryError("cwd-missing")
    try:
        cwd = Path(raw_cwd).resolve()
        cwd.relative_to(root)
    except (OSError, ValueError):
        raise RecoveryError("cwd-outside-repository") from None
    return root


def _run(root: Path, argv: list[str]) -> str:
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise RecoveryError("bounded-command-failed") from None
    if result.returncode != 0:
        raise RecoveryError("bounded-command-rejected")
    return result.stdout.rstrip("\r\n")


def _registry(root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            (root / "governance" / "work_registry.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RecoveryError("registry-invalid") from None
    if value.get("schema") != "ArchFlowDevelopmentRegistry@1":
        raise RecoveryError("registry-schema-invalid")
    return value


def _active_item_ids(registry: dict[str, Any]) -> tuple[str, ...]:
    items = registry.get("items")
    if not isinstance(items, list):
        raise RecoveryError("registry-items-invalid")
    active = tuple(
        sorted(
            item.get("id")
            for item in items
            if isinstance(item, dict)
            and item.get("status") == "active"
            and isinstance(item.get("id"), str)
        )
    )
    if len(active) > MAX_ACTIVE_CARDS:
        raise RecoveryError("too-many-active-cards")
    return active


def _capsule(root: Path, item_id: str) -> dict[str, Any]:
    output = _run(
        root,
        [sys.executable, "tools/devctl.py", "context", item_id],
    )
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        raise RecoveryError("context-capsule-invalid") from None
    authority = value.get("authority")
    if (
        value.get("schema") != "ArchFlowWorkContext@1"
        or not isinstance(authority, dict)
        or authority.get("read_only") is not True
        or authority.get("evidence_authority") is not False
        or authority.get("project_state_authority") is not False
    ):
        raise RecoveryError("context-capsule-authority-invalid")
    return value


def _git_snapshot(root: Path) -> dict[str, Any]:
    head = _run(root, ["git", "rev-parse", "HEAD"])
    branch = _run(root, ["git", "branch", "--show-current"]) or "DETACHED"
    dirty = tuple(
        line[:240]
        for line in _run(root, ["git", "status", "--short"]).splitlines()
        if line.strip()
    )
    if len(dirty) > MAX_DIRTY_PATHS:
        raise RecoveryError("too-many-dirty-paths")
    return {"branch": branch, "head": head, "dirty_paths": dirty}


def _summary(capsule: dict[str, Any]) -> dict[str, Any]:
    item = capsule.get("item")
    claims = capsule.get("planning_claims")
    repository_evidence = capsule.get("repository_evidence")
    if not all(
        isinstance(value, dict)
        for value in (item, claims, repository_evidence)
    ):
        raise RecoveryError("context-capsule-shape-invalid")
    verification = repository_evidence.get("verification")
    if not isinstance(verification, dict):
        raise RecoveryError("context-verification-shape-invalid")
    return {
        "id": item.get("id"),
        "actor": item.get("actor"),
        "status": item.get("status"),
        "goal": claims.get("goal"),
        "write_scope": item.get("write_scope"),
        "acceptance": claims.get("acceptance"),
        "tests": claims.get("tests"),
        "stop_conditions": claims.get("stop_conditions"),
        "verification": {
            "label": verification.get("label"),
            "verified": verification.get("verified"),
        },
        "completion_evidence_count": len(
            repository_evidence.get("completion_evidence", [])
        ),
        "capsule_sha256": capsule.get("capsule_sha256"),
    }


def build_recovery(root: Path) -> dict[str, Any]:
    active_ids = _active_item_ids(_registry(root))
    cards = tuple(_summary(_capsule(root, item_id)) for item_id in active_ids)
    return {
        "schema": CONTEXT_SCHEMA,
        "authority": {
            "orientation_only": True,
            "evidence_authority": False,
            "project_state_authority": False,
            "completion_authority": False,
        },
        "git": _git_snapshot(root),
        "active_cards": cards,
    }


def render_context(recovery: dict[str, Any]) -> str:
    git = recovery["git"]
    lines = [
        "ARCHFLOW COMPACTION RECOVERY @1",
        "Authority: orientation only; this hook is not project state, evidence, verification, or completion authority.",
        "Before modifying files, refresh git status and the active work-card capsule; never infer success from compressed history.",
        f"Git branch: {git['branch']}",
        f"Git HEAD: {git['head']}",
        "Dirty paths (status only):",
    ]
    dirty = git["dirty_paths"]
    lines.extend(f"  {item}" for item in dirty)
    if not dirty:
        lines.append("  none")
    cards = recovery["active_cards"]
    if not cards:
        lines.append("Active work cards: none; do not choose a new card from hook output.")
    for card in cards:
        lines.extend(
            (
                f"Active card {card['id']} | actor={card['actor']} | status={card['status']}",
                f"  Goal [unverified planning contract]: {card['goal']}",
                f"  P046 capsule sha256: {card['capsule_sha256']}",
                "  Write scope:",
            )
        )
        lines.extend(f"    - {item}" for item in card["write_scope"])
        lines.append("  Acceptance [planning claims, not proof]:")
        lines.extend(f"    - {item}" for item in card["acceptance"])
        lines.append("  Tests:")
        lines.extend(f"    - {item}" for item in card["tests"])
        lines.append("  Stop conditions:")
        lines.extend(f"    - {item}" for item in card["stop_conditions"])
        verification = card["verification"]
        lines.append(
            "  Repository verification: "
            f"label={verification['label']}; verified={verification['verified']}; "
            f"completion_evidence_count={card['completion_evidence_count']}"
        )
    text = "\n".join(lines)
    if len(text) > MAX_CONTEXT_CHARS:
        raise RecoveryError("recovery-context-too-large")
    return text


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    root = _repo_root(payload)
    event = payload.get("hook_event_name")
    if event == "PreCompact":
        if payload.get("trigger") not in {"manual", "auto"}:
            raise RecoveryError("precompact-trigger-invalid")
        recovery = build_recovery(root)
        ids = ",".join(card["id"] for card in recovery["active_cards"])
        return {
            "continue": True,
            "systemMessage": (
                "ArchFlow recovery context verified"
                + (f" for {ids}" if ids else " with no active work card")
            ),
        }
    if event == "SessionStart":
        if payload.get("source") != "compact":
            raise RecoveryError("session-source-invalid")
        context = render_context(build_recovery(root))
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            },
        }
    raise RecoveryError("hook-event-invalid")


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(raw) > MAX_STDIN_BYTES:
            raise RecoveryError("hook-input-too-large")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RecoveryError("hook-input-shape-invalid")
        output = handle(payload)
    except (UnicodeError, json.JSONDecodeError):
        output = {
            "continue": False,
            "stopReason": "ArchFlow context recovery failed: hook-input-invalid",
            "systemMessage": "ArchFlow context recovery failed closed",
        }
    except RecoveryError as exc:
        output = {
            "continue": False,
            "stopReason": f"ArchFlow context recovery failed: {exc.code}",
            "systemMessage": "ArchFlow context recovery failed closed",
        }
    sys.stdout.write(json.dumps(output, ensure_ascii=True, sort_keys=True))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
