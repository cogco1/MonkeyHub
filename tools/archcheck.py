"""Fast policy-as-code checks for ArchFlow V4 architecture boundaries."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


POLICY_SCHEMA = "ArchFlowArchitecturePolicy@1"


class ArchitecturePolicyError(ValueError):
    """The policy cannot be interpreted deterministically."""


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


@dataclass(frozen=True, slots=True)
class _SourceIndex:
    nodes: tuple[ast.AST, ...]
    parents: dict[ast.AST, ast.AST]


def _index_tree(tree: ast.AST) -> _SourceIndex:
    """Index one syntax tree once for every architecture check."""

    nodes: list[ast.AST] = []
    parents: dict[ast.AST, ast.AST] = {}
    stack = [tree]
    while stack:
        node = stack.pop()
        nodes.append(node)
        children = tuple(ast.iter_child_nodes(node))
        for child in children:
            parents[child] = node
        stack.extend(reversed(children))
    return _SourceIndex(tuple(nodes), parents)


def load_policy(path: Path) -> dict[str, Any]:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchitecturePolicyError(f"invalid policy: {path}") from exc
    validate_policy(policy)
    return policy


def _require_string_list(policy: dict[str, Any], field: str) -> list[str]:
    value = policy.get(field)
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ArchitecturePolicyError(f"{field} must be a string list")
    return value


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise ArchitecturePolicyError("unsupported architecture policy schema")
    for field in (
        "source_root",
        "probe_root",
    ):
        if not isinstance(policy.get(field), str) or not policy[field]:
            raise ArchitecturePolicyError(f"{field} must be non-empty text")
    for field in (
        "forbidden_instance_literals",
        "forbidden_framework_identifiers",
        "probe_executable_suffixes",
        "authority_symbol_patterns",
        "forbidden_commit_symbols",
    ):
        _require_string_list(policy, field)

    write_sites = policy.get("allowed_write_sites")
    if not isinstance(write_sites, list):
        raise ArchitecturePolicyError("allowed_write_sites must be a list")
    for index, site in enumerate(write_sites):
        if not isinstance(site, dict):
            raise ArchitecturePolicyError(
                f"allowed_write_sites[{index}] must be an object"
            )
        for field in ("path", "function", "kind", "owner", "reason"):
            if not isinstance(site.get(field), str) or not site[field]:
                raise ArchitecturePolicyError(
                    f"allowed_write_sites[{index}].{field} is invalid"
                )
        operations = site.get("operations")
        if (
            not isinstance(operations, list)
            or not operations
            or any(not isinstance(item, str) or not item for item in operations)
        ):
            raise ArchitecturePolicyError(
                f"allowed_write_sites[{index}].operations is invalid"
            )
        if site["function"] == "*" and site["kind"] != "project_repository":
            raise ArchitecturePolicyError(
                "only the project repository may use a whole-file write allowance"
            )

    layer_rules = policy.get("forbidden_layer_imports")
    if not isinstance(layer_rules, list):
        raise ArchitecturePolicyError(
            "forbidden_layer_imports must be a list"
        )
    for index, rule in enumerate(layer_rules):
        if not isinstance(rule, dict):
            raise ArchitecturePolicyError(
                f"forbidden_layer_imports[{index}] must be an object"
            )
        if not isinstance(rule.get("source"), str) or not rule["source"]:
            raise ArchitecturePolicyError(
                f"forbidden_layer_imports[{index}].source is invalid"
            )
        targets = rule.get("targets")
        if (
            not isinstance(targets, list)
            or not targets
            or any(not isinstance(item, str) or not item for item in targets)
        ):
            raise ArchitecturePolicyError(
                f"forbidden_layer_imports[{index}].targets is invalid"
            )
        if not isinstance(rule.get("reason"), str) or not rule["reason"]:
            raise ArchitecturePolicyError(
                f"forbidden_layer_imports[{index}].reason is invalid"
            )

    authorities = policy.get("allowed_authority_symbols")
    if not isinstance(authorities, list):
        raise ArchitecturePolicyError(
            "allowed_authority_symbols must be a list"
        )
    for index, authority in enumerate(authorities):
        if not isinstance(authority, dict):
            raise ArchitecturePolicyError(
                f"allowed_authority_symbols[{index}] must be an object"
            )
        for field in ("path", "symbol", "owner"):
            if (
                not isinstance(authority.get(field), str)
                or not authority[field]
            ):
                raise ArchitecturePolicyError(
                    f"allowed_authority_symbols[{index}].{field} is invalid"
                )


def _python_files(root: Path, relative_root: str) -> tuple[Path, ...]:
    source = root / relative_root
    if not source.is_dir():
        return ()
    return tuple(
        sorted(
            (
                path
                for path in source.rglob("*.py")
                if "__pycache__" not in path.parts
            ),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )


def _parse(path: Path, root: Path) -> tuple[ast.Module | None, Finding | None]:
    relative = path.relative_to(root).as_posix()
    try:
        return ast.parse(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        line = exc.lineno if isinstance(exc, SyntaxError) and exc.lineno else 1
        return None, Finding(relative, line, "PARSE_ERROR", str(exc))


def _import_targets(nodes: Iterable[ast.AST]) -> Iterator[tuple[str, int]]:
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, node.lineno


def _module_matches(target: str, prefix: str) -> bool:
    return target == prefix or target.startswith(prefix + ".")


def _source_matches(relative: str, prefix: str) -> bool:
    normalized = prefix.rstrip("/")
    return relative == normalized + ".py" or relative.startswith(
        normalized + "/"
    )


def check_imports(
    relative: str,
    index: _SourceIndex,
    policy: dict[str, Any],
) -> Iterator[Finding]:
    for target, line in _import_targets(index.nodes):
        if _module_matches(target, "probes"):
            yield Finding(
                relative,
                line,
                "PROBE_REVERSE_IMPORT",
                f"framework imports project probe module {target!r}",
            )
        for rule in policy["forbidden_layer_imports"]:
            if not _source_matches(relative, rule["source"]):
                continue
            for forbidden in rule["targets"]:
                if _module_matches(target, forbidden):
                    yield Finding(
                        relative,
                        line,
                        "LAYER_AUTHORITY_VIOLATION",
                        f"{target!r} is forbidden here: {rule['reason']}",
                    )


def check_instance_answers(
    relative: str,
    index: _SourceIndex,
    policy: dict[str, Any],
) -> Iterator[Finding]:
    forbidden_literals = tuple(
        item.casefold() for item in policy["forbidden_instance_literals"]
    )
    forbidden_identifiers = set(policy["forbidden_framework_identifiers"])
    for node in index.nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            folded = node.value.casefold()
            for forbidden in forbidden_literals:
                if forbidden in folded:
                    yield Finding(
                        relative,
                        node.lineno,
                        "INSTANCE_ANSWER_LITERAL",
                        f"framework contains project answer literal {forbidden!r}",
                    )
                    break
        elif isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in forbidden_identifiers:
                yield Finding(
                    relative,
                    node.lineno,
                    "FIXED_FRAMEWORK_ANSWER",
                    f"forbidden framework identifier {name!r}",
                )


def _enclosing_function(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def _write_operation(node: ast.Call) -> str | None:
    function = node.func
    if isinstance(function, ast.Name):
        if function.id == "open":
            mode = node.args[1] if len(node.args) > 1 else None
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                if set(mode.value) & set("wax+"):
                    return "open_write"
        if function.id == "_write_json":
            return "helper:_write_json"
        return None
    if not isinstance(function, ast.Attribute):
        return None
    operation = function.attr
    if (
        operation == "replace"
        and isinstance(function.value, ast.Name)
        and function.value.id == "os"
    ):
        return "replace"
    if operation == "open":
        mode_node = node.args[0] if node.args else None
        if isinstance(mode_node, ast.Constant) and isinstance(
            mode_node.value, str
        ):
            return (
                "open_write"
                if set(mode_node.value) & set("wax+")
                else None
            )
        return None
    if operation in {
        "mkdir",
        "rename",
        "rmdir",
        "unlink",
        "write_bytes",
        "write_text",
    }:
        return operation
    return None


def _allowed_write(
    relative: str,
    function: str,
    operation: str,
    policy: dict[str, Any],
) -> bool:
    for site in policy["allowed_write_sites"]:
        if site["path"] != relative:
            continue
        if site["function"] not in {"*", function}:
            continue
        if operation in site["operations"] or "*" in site["operations"]:
            return True
    return False


def check_filesystem_writes(
    relative: str,
    index: _SourceIndex,
    policy: dict[str, Any],
) -> Iterator[Finding]:
    for node in index.nodes:
        if not isinstance(node, ast.Call):
            continue
        operation = _write_operation(node)
        if operation is None:
            continue
        function = _enclosing_function(node, index.parents)
        if not _allowed_write(relative, function, operation, policy):
            yield Finding(
                relative,
                node.lineno,
                "UNOWNED_FILESYSTEM_WRITE",
                f"{function} performs {operation} outside an allowed writer",
            )


def check_authority_symbols(
    relative: str,
    index: _SourceIndex,
    policy: dict[str, Any],
) -> Iterator[Finding]:
    patterns = tuple(re.compile(item) for item in policy["authority_symbol_patterns"])
    allowed = {
        (item["path"], item["symbol"])
        for item in policy["allowed_authority_symbols"]
    }
    for node in index.nodes:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            continue
        if not any(pattern.fullmatch(node.name) for pattern in patterns):
            continue
        if (relative, node.name) not in allowed:
            yield Finding(
                relative,
                node.lineno,
                "DUPLICATE_STATE_AUTHORITY",
                f"unregistered state or promotion authority {node.name!r}",
            )


def check_commit_soft_gate_leak(
    relative: str,
    index: _SourceIndex,
    policy: dict[str, Any],
) -> Iterator[Finding]:
    if not _source_matches(relative, "archflow/commit"):
        return
    forbidden = set(policy["forbidden_commit_symbols"])
    for node in index.nodes:
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        else:
            continue
        if name in forbidden:
            yield Finding(
                relative,
                node.lineno,
                "SOFT_GATE_PROMOTION_LEAK",
                f"commit layer references forbidden soft-ranking symbol {name!r}",
            )


def check_probe_boundary(root: Path, policy: dict[str, Any]) -> Iterator[Finding]:
    probe_root = root / policy["probe_root"]
    if (probe_root / "__init__.py").exists():
        yield Finding(
            (probe_root / "__init__.py").relative_to(root).as_posix(),
            1,
            "PROBE_PACKAGE",
            "probes must remain data-only and cannot be a Python package",
        )
    suffixes = set(policy["probe_executable_suffixes"])
    if probe_root.is_dir():
        for path in sorted(probe_root.rglob("*")):
            if path.is_file() and path.suffix.casefold() in suffixes:
                yield Finding(
                    path.relative_to(root).as_posix(),
                    1,
                    "PROBE_EXECUTABLE",
                    "project probe contains executable logic",
                )
    root_runs = root / ".runs"
    if root_runs.exists():
        yield Finding(
            ".runs",
            1,
            "ROOT_RUN_STORE",
            "repository-level project run storage is forbidden",
        )


def run_checks(root: Path, policy: dict[str, Any]) -> tuple[Finding, ...]:
    validate_policy(policy)
    findings: list[Finding] = list(check_probe_boundary(root, policy))
    for path in _python_files(root, policy["source_root"]):
        relative = path.relative_to(root).as_posix()
        tree, parse_finding = _parse(path, root)
        if parse_finding is not None:
            findings.append(parse_finding)
            continue
        assert tree is not None
        index = _index_tree(tree)
        checks: Iterable[Iterable[Finding]] = (
            check_imports(relative, index, policy),
            check_instance_answers(relative, index, policy),
            check_filesystem_writes(relative, index, policy),
            check_authority_symbols(relative, index, policy),
            check_commit_soft_gate_leak(relative, index, policy),
        )
        for result in checks:
            findings.extend(result)
    return tuple(sorted(set(findings)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    policy_path = (
        args.policy.resolve()
        if args.policy
        else root / "governance" / "architecture_policy.json"
    )
    started = time.perf_counter()
    try:
        policy = load_policy(policy_path)
        findings = run_checks(root, policy)
    except ArchitecturePolicyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - started
    if args.json:
        print(
            json.dumps(
                {
                    "schema": "ArchFlowArchitectureCheck@1",
                    "passed": not findings,
                    "files_checked": len(
                        _python_files(root, policy["source_root"])
                    ),
                    "elapsed_seconds": round(elapsed, 6),
                    "findings": [item.to_dict() for item in findings],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    elif findings:
        for finding in findings:
            print(
                f"{finding.path}:{finding.line}: "
                f"{finding.code}: {finding.message}"
            )
    else:
        print(
            "ARCHITECTURE PASS "
            f"({len(_python_files(root, policy['source_root']))} files, "
            f"{elapsed:.3f}s)"
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
