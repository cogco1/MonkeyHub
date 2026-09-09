"""Fast policy-as-code checks for ArchFlow V4 architecture boundaries.

Two modes. Without arguments it checks the tree: layer imports, filesystem
write ownership, state authorities, the probe boundary, the module registry,
and -- since people now develop in parallel -- that no two live work cards claim
the same path.

With ``--changed <base>`` it checks one branch instead, using each commit's
policy and work registry from Git. Once the scope rule exists in a parent,
a commit declares its card (``P###``) in the subject, or in the body when the
subject names none, and may write only that card's scope plus shared ledgers.
``P000-governance`` permits only governance files and README.md maintenance.
This is the mode CI runs on a pull request; it does not impose a new rule on
the commits that preceded or introduced that rule.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


POLICY_SCHEMA = "ArchFlowArchitecturePolicy@1"


class ArchitecturePolicyError(ValueError):
    """The policy cannot be interpreted deterministically."""


@dataclass(frozen=True, slots=True, order=True)
class PolicyFinding:
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


def validate_policy(policy: dict[str, Any], root: Path | None = None) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise ArchitecturePolicyError("unsupported architecture policy schema")
    if root is not None:
        for entry in list(policy.get("allowed_write_sites", ())) + list(policy.get("allowed_authority_symbols", ())):
            path = entry.get("path") if isinstance(entry, dict) else None
            if path and not (root / path).is_file():
                raise ArchitecturePolicyError(f"policy names a file that does not exist: {path}")
    for field in (
        "source_root",
        "probe_root",
    ):
        if not isinstance(policy.get(field), str) or not policy[field]:
            raise ArchitecturePolicyError(f"{field} must be non-empty text")
    for field in (
        "checked_source_roots",
        "forbidden_instance_literals",
        "forbidden_framework_identifiers",
        "probe_executable_suffixes",
        "authority_symbol_patterns",
        "forbidden_commit_symbols",
        "import_only_source_roots",
        "shared_write_scope",
    ):
        _require_string_list(policy, field)
    checked_roots = policy["checked_source_roots"]
    if len(checked_roots) != len(set(checked_roots)):
        raise ArchitecturePolicyError(
            "checked_source_roots must not contain duplicates"
        )
    if policy["source_root"] not in checked_roots:
        raise ArchitecturePolicyError(
            "checked_source_roots must include source_root"
        )
    for import_only in policy["import_only_source_roots"]:
        if import_only not in checked_roots:
            raise ArchitecturePolicyError(
                "checked_source_roots must include every import_only_source_root"
            )
        if import_only == policy["source_root"]:
            raise ArchitecturePolicyError(
                "source_root cannot be import-only"
            )

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


def _checked_python_files(
    root: Path,
    policy: dict[str, Any],
) -> tuple[Path, ...]:
    """Return one stable, de-duplicated file set for the configured roots."""

    paths = {
        path
        for relative_root in policy["checked_source_roots"]
        for path in _python_files(root, relative_root)
    }
    return tuple(
        sorted(paths, key=lambda path: path.relative_to(root).as_posix())
    )


def _parse(path: Path, root: Path) -> tuple[ast.Module | None, PolicyFinding | None]:
    relative = path.relative_to(root).as_posix()
    try:
        return ast.parse(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        line = exc.lineno if isinstance(exc, SyntaxError) and exc.lineno else 1
        return None, PolicyFinding(relative, line, "PARSE_ERROR", str(exc))


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


def _is_import_only(relative: str, policy: dict[str, Any]) -> bool:
    """Is this file in a root that is checked for imports and nothing else?

    ``labs/`` is such a root. A lab is interest-driven exploration: it may
    import ``archflow`` and must therefore be walked by the import check, which
    is what keeps the spine from importing it back. It is deliberately outside
    every other check: no module-registry owner, no write-site registration, no
    duplicate-authority or instance-literal rule. Scoping it here, rather than
    by leaving ``labs`` out of ``checked_source_roots``, keeps one file list and
    states the exemption in one predicate.
    """

    return any(
        _source_matches(relative, root)
        for root in policy["import_only_source_roots"]
    )


def check_imports(
    relative: str,
    index: _SourceIndex,
    policy: dict[str, Any],
) -> Iterator[PolicyFinding]:
    for target, line in _import_targets(index.nodes):
        if _source_matches(
            relative,
            policy["source_root"],
        ) and _module_matches(target, "probes"):
            yield PolicyFinding(
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
                    yield PolicyFinding(
                        relative,
                        line,
                        "LAYER_AUTHORITY_VIOLATION",
                        f"{target!r} is forbidden here: {rule['reason']}",
                    )


def check_instance_answers(
    relative: str,
    index: _SourceIndex,
    policy: dict[str, Any],
) -> Iterator[PolicyFinding]:
    forbidden_literals = tuple(
        item.casefold() for item in policy["forbidden_instance_literals"]
    )
    forbidden_identifiers = set(policy["forbidden_framework_identifiers"])
    for node in index.nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            folded = node.value.casefold()
            for forbidden in forbidden_literals:
                if forbidden in folded:
                    yield PolicyFinding(
                        relative,
                        node.lineno,
                        "INSTANCE_ANSWER_LITERAL",
                        f"framework contains project answer literal {forbidden!r}",
                    )
                    break
        elif isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in forbidden_identifiers:
                yield PolicyFinding(
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
) -> Iterator[PolicyFinding]:
    for node in index.nodes:
        if not isinstance(node, ast.Call):
            continue
        operation = _write_operation(node)
        if operation is None:
            continue
        function = _enclosing_function(node, index.parents)
        if not _allowed_write(relative, function, operation, policy):
            yield PolicyFinding(
                relative,
                node.lineno,
                "UNOWNED_FILESYSTEM_WRITE",
                f"{function} performs {operation} outside an allowed writer",
            )


def check_authority_symbols(
    relative: str,
    index: _SourceIndex,
    policy: dict[str, Any],
) -> Iterator[PolicyFinding]:
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
            yield PolicyFinding(
                relative,
                node.lineno,
                "DUPLICATE_STATE_AUTHORITY",
                f"unregistered state or promotion authority {node.name!r}",
            )


def check_commit_soft_gate_leak(
    relative: str,
    index: _SourceIndex,
    policy: dict[str, Any],
) -> Iterator[PolicyFinding]:
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
            yield PolicyFinding(
                relative,
                node.lineno,
                "SOFT_GATE_PROMOTION_LEAK",
                f"commit layer references forbidden soft-ranking symbol {name!r}",
            )


def check_probe_boundary(root: Path, policy: dict[str, Any]) -> Iterator[PolicyFinding]:
    # The probe root left the repository on 2026-09-05: the one fixture project
    # under it is now built by the spine test itself, and retained records live
    # in the workspace. Both probe findings are existence-guarded, so a missing
    # directory is not a finding; they stay as the guard against the directory
    # coming back as anything but data. The root run-store check below is
    # independent of it and always runs.
    probe_root = root / policy["probe_root"]
    if (probe_root / "__init__.py").exists():
        yield PolicyFinding(
            (probe_root / "__init__.py").relative_to(root).as_posix(),
            1,
            "PROBE_PACKAGE",
            "probes must remain data-only and cannot be a Python package",
        )
    suffixes = set(policy["probe_executable_suffixes"])
    if probe_root.is_dir():
        for path in sorted(probe_root.rglob("*")):
            if path.is_file() and path.suffix.casefold() in suffixes:
                yield PolicyFinding(
                    path.relative_to(root).as_posix(),
                    1,
                    "PROBE_EXECUTABLE",
                    "project probe contains executable logic",
                )
    root_runs = root / ".runs"
    if root_runs.exists():
        yield PolicyFinding(
            ".runs",
            1,
            "ROOT_RUN_STORE",
            "repository-level project run storage is forbidden",
        )


def _normalized_body(src: str, node: ast.FunctionDef) -> str:
    seg = ast.get_source_segment(src, node) or ""
    seg = re.sub(r'"""[\s\S]*?"""', "", seg)
    seg = re.sub(r"#.*", "", seg)
    seg = re.sub(r"\s+", " ", seg).strip()
    return re.sub(r"def \w+", "def F", seg)


def check_registry(root: Path, policy: dict[str, Any]) -> Iterator[PolicyFinding]:
    """The module registry must tell the truth, and a capability has one owner.

    Owner paths exist; every public_api symbol is defined in its owner; listed
    tests exist; every string in ``owns`` appears in exactly one entry; and no
    spine module outside an owner defines a function whose normalised body
    equals one of the owner's functions (a copied helper is a duplicate owner).
    """

    registry_path = root / "governance" / "module_registry.json"
    if not registry_path.is_file():
        return
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    entries = data.get("modules", [])
    rel_registry = registry_path.relative_to(root).as_posix()
    owners: dict[str, str] = {}
    ids: set[str] = set()
    owner_bodies: dict[str, tuple[str, str]] = {}
    for entry in entries:
        module_id = entry.get("module_id", "?")
        if module_id in ids:
            yield PolicyFinding(rel_registry, 1, "REGISTRY_DUPLICATE_MODULE", f"module_id {module_id} listed twice")
        ids.add(module_id)
        owner = root / entry.get("owner_path", "")
        if not owner.is_file():
            yield PolicyFinding(rel_registry, 1, "REGISTRY_OWNER_MISSING", f"{module_id}: owner_path {entry.get('owner_path')} does not exist")
            continue
        for capability in entry.get("owns", ()):
            key = capability.strip().lower()
            if key in owners and owners[key] != module_id:
                yield PolicyFinding(rel_registry, 1, "REGISTRY_DUPLICATE_OWNER", f"capability {capability!r} owned by both {owners[key]} and {module_id}")
            owners.setdefault(key, module_id)
        for test in entry.get("tests", ()):
            if not (root / test).is_file():
                yield PolicyFinding(rel_registry, 1, "REGISTRY_TEST_MISSING", f"{module_id}: test {test} does not exist")
        span = [root / f for f in (entry.get("files") or [entry["owner_path"]])]
        defined: set[str] = set()
        for path in span:
            if path.suffix != ".py" or not path.is_file():
                continue
            src = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            defined |= {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            defined |= {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
            defined |= {n.target.id for n in tree.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("__"):
                    body = _normalized_body(src, node)
                    if len(body) > 80:
                        owner_bodies.setdefault(body, (module_id, node.name))
        for symbol in entry.get("public_api", ()):
            if symbol.isidentifier() and symbol not in defined:
                yield PolicyFinding(rel_registry, 1, "REGISTRY_SYMBOL_MISSING", f"{module_id}: public_api symbol {symbol} is not defined in {entry['owner_path']} or its files")
        # depends_on must be what the owner imports from archflow (module ids or dotted module paths)
        owner_by_module = {e2["owner_path"][:-3].replace("/", "."): e2["module_id"] for e2 in entries if e2.get("owner_path", "").endswith(".py")}
        actual: set[str] = set()
        for path in span:
            if path.suffix != ".py" or not path.is_file():
                continue
            try:
                tree2 = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree2):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("archflow."):
                    actual.add(owner_by_module.get(node.module, node.module))
                elif isinstance(node, ast.Import):
                    actual.update(owner_by_module.get(a.name, a.name) for a in node.names if a.name.startswith("archflow."))
        declared = set()
        for dep in entry.get("depends_on", ()):
            declared.add(owner_by_module.get(dep, dep))
            declared.add(dep)
        actual.discard(module_id)
        undeclared = sorted(a for a in actual if a not in declared and not any(a.endswith("." + d.split(".")[-1]) for d in declared))
        if undeclared:
            yield PolicyFinding(rel_registry, 1, "REGISTRY_DEPENDS_ON_DRIFT", f"{module_id} imports {', '.join(undeclared)} but depends_on does not say so")
        if not entry.get("tests") and not entry.get("untested_reason"):
            yield PolicyFinding(rel_registry, 1, "REGISTRY_UNTESTED_OWNER", f"{module_id} lists no test and gives no untested_reason")
    owner_paths = {root / f for e in entries for f in (e.get("files") or [e.get("owner_path", "")])}
    for path in _checked_python_files(root, policy):
        relative_path = path.relative_to(root).as_posix()
        if path in owner_paths or "/tests/" in path.as_posix() or path.name == "__init__.py":
            continue
        if _is_import_only(relative_path, policy):
            continue
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("__"):
                hit = owner_bodies.get(_normalized_body(src, node))
                if hit:
                    yield PolicyFinding(path.relative_to(root).as_posix(), node.lineno, "DUPLICATE_OWNED_FUNCTION", f"{node.name} duplicates {hit[0]}.{hit[1]}; import the owner")


WORK_REGISTRY = "governance/work_registry.json"
ARCHITECTURE_POLICY = "governance/architecture_policy.json"
LIVE_SCOPE_STATUSES = frozenset({"active", "ready"})
CARD_ID = re.compile(r"\b(?:P000-governance(?![\w-])|P\d{3}(?!\d))")
# Governance paths a commit may touch without naming a card. Everything else
# belongs to exactly one card, whose write_scope says so.
UNCARDED_WRITE_SCOPE = ("docs/adr/", "docs/REPO_LAYOUT.md", "CONTRIBUTING.md")
GOVERNANCE_WRITE_SCOPE = UNCARDED_WRITE_SCOPE + (
    "tools/archcheck.py",
    "governance/architecture_policy.json",
    ".github/workflows/verify.yml",
    ".github/pull_request_template.md",
    "AGENTS.md",
    "docs/SYSTEM_MAP.md",
)


def load_work_registry(root: Path) -> dict[str, Any] | None:
    """The live work registry, or None when the repository has none."""

    path = root / WORK_REGISTRY
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchitecturePolicyError(f"invalid work registry: {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ArchitecturePolicyError(f"invalid work registry: {path}")
    return data


def _scope_parts(entry: str) -> tuple[str, ...]:
    """One write-scope entry as path segments; a file and a directory look alike."""

    cleaned = entry.strip().replace("\\", "/").strip("/")
    return tuple(part for part in cleaned.split("/") if part and part != ".")


def _scope_covers(scope: str, path: str) -> bool:
    """Does one write-scope entry contain this path (or equal it)?"""

    prefix = _scope_parts(scope)
    parts = _scope_parts(path)
    return bool(prefix) and parts[: len(prefix)] == prefix


def _scopes_overlap(left: str, right: str) -> bool:
    """Two write-scope entries overlap when either contains the other."""

    return _scope_covers(left, right) or _scope_covers(right, left)


def _covered_by_any(path: str, scopes: Iterable[str]) -> bool:
    return any(_scope_covers(scope, path) for scope in scopes)


def check_scopes(
    root: Path,
    policy: dict[str, Any],
    registry: dict[str, Any] | None,
) -> Iterator[PolicyFinding]:
    """Two live cards cannot own the same path.

    ``write_scope`` is what a card may write, and it is the only boundary
    between people developing in parallel. If two cards that are active or
    ready claim the same directory, or one claims a directory inside the
    other's, nobody can say whose change a conflict is. The shared ledgers
    (``shared_write_scope`` in the policy: the suite, the card directory and the
    two registries) are exempt because every card must be able to write them.
    """

    if registry is None:
        return
    shared = policy["shared_write_scope"]
    live = [
        item
        for item in registry["items"]
        if isinstance(item, dict) and item.get("status") in LIVE_SCOPE_STATUSES
    ]
    live.sort(key=lambda item: str(item.get("id", "")))
    for index, first in enumerate(live):
        for second in live[index + 1 :]:
            pairs = sorted(
                {
                    (left, right)
                    for left in first.get("write_scope", ())
                    for right in second.get("write_scope", ())
                    if isinstance(left, str)
                    and isinstance(right, str)
                    and not _covered_by_any(left, shared)
                    and not _covered_by_any(right, shared)
                    and _scopes_overlap(left, right)
                }
            )
            for left, right in pairs:
                yield PolicyFinding(
                    WORK_REGISTRY,
                    1,
                    "SCOPE_OVERLAP",
                    f"{first.get('id')} write_scope {left!r} overlaps "
                    f"{second.get('id')} write_scope {right!r}; "
                    "narrow one card or make the path a shared ledger",
                )


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise ArchitecturePolicyError(
            f"git {' '.join(args)} failed: {detail.strip()}"
        ) from exc
    return completed.stdout


def _commit_card(message: str) -> str | None:
    """A card or explicit governance marker in the subject, else in the body.

    The subject wins because a body says things about other cards -- what this
    change unblocks, which card a finding belongs to -- and a commit would
    otherwise be filed under whichever card it mentioned last. Within one part
    of the message the last id still wins, so a subject or a body naming its
    card twice is unambiguous.
    """

    subject = message.splitlines()[0] if message.strip() else ""
    found = CARD_ID.findall(subject) or CARD_ID.findall(message)
    return found[-1] if found else None


def _git_json(root: Path, revision: str, path: str) -> dict[str, Any] | None:
    """Read a historical policy or registry without checking out another tree."""

    if not _git(root, "ls-tree", "--name-only", revision, "--", path).strip():
        return None
    try:
        value = json.loads(_git(root, "show", f"{revision}:{path}"))
    except json.JSONDecodeError as exc:
        raise ArchitecturePolicyError(f"invalid {path} at {revision[:8]}") from exc
    if not isinstance(value, dict):
        raise ArchitecturePolicyError(f"invalid {path} at {revision[:8]}")
    return value


def check_changed_scopes(
    root: Path,
    base: str,
    policy_path: str = ARCHITECTURE_POLICY,
) -> Iterator[PolicyFinding]:
    """Check the scope that applied when each commit was made.

    Policy and cards come from the commit, not today's live registry. A card
    closed by a commit may use its first parent's active scope. The rule
    starts after a parent first has ``shared_write_scope``; removing that
    configuration later is an error, not a way to turn the check off.
    """

    policies: dict[str, dict[str, Any] | None] = {}
    enabled: dict[str, bool] = {}

    def policy_at(revision: str) -> dict[str, Any] | None:
        if revision not in policies:
            policies[revision] = _git_json(root, revision, policy_path)
        return policies[revision]

    def rule_enabled_at(revision: str) -> bool:
        if revision not in enabled:
            policy = policy_at(revision)
            enabled[revision] = policy is not None and "shared_write_scope" in policy
            if not enabled[revision]:
                # A base after removal must not reset the rule. Inspect only
                # changes to this existing policy field, not an extra ledger.
                for earlier in _git(
                    root, "log", "--format=%H", "--full-history", "-G",
                    '"shared_write_scope"[[:space:]]*:', revision, "--", policy_path,
                ).splitlines():
                    previous = policy_at(earlier)
                    if previous is not None and "shared_write_scope" in previous:
                        enabled[revision] = True
                        break
        return enabled[revision]

    def cards_at(revision: str) -> dict[str, dict[str, Any]]:
        registry = _git_json(root, revision, WORK_REGISTRY)
        if registry is None or not isinstance(registry.get("items"), list):
            raise ArchitecturePolicyError(
                f"invalid work registry at {revision[:8]}: {WORK_REGISTRY}"
            )
        return {
            str(item.get("id")): item
            for item in registry["items"]
            if isinstance(item, dict) and item.get("status") in LIVE_SCOPE_STATUSES
        }

    head = _git(root, "rev-parse", "HEAD").strip()
    if policy_at(head) is None:
        raise ArchitecturePolicyError(f"missing policy {policy_path} at {head[:8]}")
    revisions = [
        line.split()
        for line in _git(
            root, "rev-list", "--reverse", "--topo-order", "--parents", f"{base}..{head}"
        ).splitlines()
        if line.strip()
    ]
    for revision, *parents in revisions:
        parent_enabled = any(rule_enabled_at(parent) for parent in parents)
        policy = policy_at(revision)
        has_scope = policy is not None and "shared_write_scope" in policy
        enabled[revision] = parent_enabled or has_scope
        if has_scope:
            _require_string_list(policy, "shared_write_scope")
        if not parent_enabled:
            continue
        if not has_scope:
            raise ArchitecturePolicyError(
                f"missing shared_write_scope in {policy_path} at {revision[:8]} "
                "after the scope rule took effect"
            )
        shared = list(policy["shared_write_scope"])
        cards = cards_at(revision)
        message = _git(root, "show", "-s", "--format=%B", revision)
        files = sorted(
            {
                line.strip()
                for line in _git(
                    root, "show", "--pretty=format:", "--name-only", revision
                ).splitlines()
                if line.strip()
            }
        )
        card_id = _commit_card(message)
        card = cards.get(card_id) if card_id else None
        if card is None and card_id not in (None, "P000-governance") and parents:
            card = cards_at(parents[0]).get(card_id)
        if card_id == "P000-governance":
            allowed = shared + list(GOVERNANCE_WRITE_SCOPE)
            code = "SCOPE_VIOLATION"
            named = f"commit {revision[:8]} is {card_id}"
        elif card is None:
            allowed = shared + list(UNCARDED_WRITE_SCOPE)
            code = "SCOPE_UNDECLARED"
            named = (
                f"commit {revision[:8]} names no card"
                if card_id is None
                else f"commit {revision[:8]} names {card_id}, which has no scope at that commit"
            )
        else:
            allowed = list(card.get("write_scope", ())) + shared
            code = "SCOPE_VIOLATION"
            named = f"commit {revision[:8]} is {card_id}"
        for path in files:
            if _covered_by_any(path, allowed) or (
                card_id == "P000-governance" and path.rsplit("/", 1)[-1] == "README.md"
            ):
                continue
            yield PolicyFinding(
                path,
                1,
                code,
                f"{named}; {path} is outside its write scope",
            )


def run_checks(root: Path, policy: dict[str, Any]) -> tuple[PolicyFinding, ...]:
    validate_policy(policy, root)
    findings: list[PolicyFinding] = list(check_probe_boundary(root, policy))
    findings.extend(check_registry(root, policy))
    findings.extend(check_scopes(root, policy, load_work_registry(root)))
    for path in _checked_python_files(root, policy):
        relative = path.relative_to(root).as_posix()
        tree, parse_finding = _parse(path, root)
        if parse_finding is not None:
            findings.append(parse_finding)
            continue
        assert tree is not None
        index = _index_tree(tree)
        checks: list[Iterable[PolicyFinding]] = [
            check_imports(relative, index, policy)
        ]
        in_tests = "/tests/" in relative or relative.startswith("tests/")
        if not in_tests and not _is_import_only(relative, policy) and any(_source_matches(relative, root_prefix) for root_prefix in policy["checked_source_roots"]):
            checks.extend(
                (
                    check_instance_answers(relative, index, policy),
                    check_filesystem_writes(relative, index, policy),
                    check_authority_symbols(relative, index, policy),
                    check_commit_soft_gate_leak(relative, index, policy),
                )
            )
        for result in checks:
            findings.extend(result)
    return tuple(sorted(set(findings)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Architecture boundaries as code. Without --changed it checks the "
            "tree; with --changed it checks one branch against the write "
            "scopes its commits declare."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--changed",
        metavar="BASE",
        help=(
            "Check commits in BASE..HEAD against their historical card scopes, "
            "after the rule first exists in a parent. A commit names P### or "
            "P000-governance in its subject, else in its body."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    policy_path = (
        args.policy.resolve()
        if args.policy
        else root / ARCHITECTURE_POLICY
    )
    started = time.perf_counter()
    try:
        if args.changed:
            try:
                relative_policy = policy_path.relative_to(root).as_posix()
            except ValueError as exc:
                raise ArchitecturePolicyError(
                    "--changed requires a policy path inside the repository"
                ) from exc
            findings = tuple(
                sorted(
                    set(
                        check_changed_scopes(
                            root, args.changed, relative_policy
                        )
                    )
                )
            )
            checked = 0
        else:
            policy = load_policy(policy_path)
            findings = run_checks(root, policy)
            checked = len(_checked_python_files(root, policy))
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
                    "files_checked": checked,
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
    elif args.changed:
        print(f"WRITE SCOPE PASS ({args.changed}..HEAD, {elapsed:.3f}s)")
    else:
        print(
            "ARCHITECTURE PASS "
            f"({checked} files, "
            f"{elapsed:.3f}s)"
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
