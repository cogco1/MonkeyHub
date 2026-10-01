"""Fast policy-as-code checks for ArchFlow V4 architecture boundaries.

Two modes. Without arguments it checks the tree: layer imports, filesystem
write ownership, state authorities, the probe boundary, the entries Git tracks
at the repository root and under docs/, the module registry, every path it
names, the namespace each module id begins with and the module ids each entry
depends on, and the live work registry -- GitHub Issue claims only, no two of
them holding the same path or checkout.
A path the policy configures must exist: a rule whose path is gone is
reported, never skipped. So is a layer rule's target that names no module,
since nothing can import it.

With ``--changed <base>`` it checks one branch instead, using each commit's
policy and work registry from Git. Once the scope rule exists in a parent, a
commit names its claim, ``GH-<issue>`` or ``GH-<issue>/<lane>``, in the
subject or body and may write only that claim's scope plus the shared ledgers;
a commit that names none may write the shared and unclaimed paths the policy
lists. Commits made before #358 retired the P/M/R work cards keep the reading
they were checked with. This is the mode CI runs on a pull request; it does not
impose a new rule on the commits that preceded or introduced that rule.
"""

from __future__ import annotations

import argparse
import ast
import json
import ntpath
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


POLICY_SCHEMA = "ArchFlowArchitecturePolicy@1"
# A module id's first segment, the namespace of the unit that holds its owner.
MODULE_ID_NAMESPACE = re.compile(r"[a-z][a-z0-9_]*")


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


def _repository_path(value: str, *, root_allowed: bool = False) -> bool:
    """Is this a literal repository-relative path written with forward slashes?"""

    if root_allowed and value == ".":
        return True
    return not (
        value.startswith("/")
        or any(character in value for character in "\\:*?[]{}")
        or any(part in ("", ".", "..") for part in value.rstrip("/").split("/"))
    )


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
        "python_source_roots",
        "repository_root_entries",
        "forbidden_instance_literals",
        "forbidden_framework_identifiers",
        "probe_executable_suffixes",
        "authority_symbol_patterns",
        "import_only_source_roots",
        "shared_write_scope",
        "unclaimed_write_scope",
    ):
        _require_string_list(policy, field)
    python_roots = policy["python_source_roots"]
    if len(python_roots) != len(set(python_roots)):
        raise ArchitecturePolicyError(
            "python_source_roots must not contain duplicates"
        )
    for python_root in python_roots:
        if not _repository_path(python_root, root_allowed=True):
            raise ArchitecturePolicyError(
                f"python_source_roots entry {python_root!r} must be a repository-relative directory"
            )
    root_entries = policy["repository_root_entries"]
    if len(root_entries) != len(set(root_entries)):
        raise ArchitecturePolicyError("repository_root_entries must not contain duplicates")
    for name in root_entries:
        if "/" in name or not _repository_path(name):
            raise ArchitecturePolicyError(
                f"repository_root_entries entry {name!r} must be one top-level name"
            )
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
    namespaces = policy.get("module_id_namespaces")
    if not isinstance(namespaces, dict) or not namespaces:
        raise ArchitecturePolicyError(
            "module_id_namespaces must map each distribution unit's directory to its namespace"
        )
    units: set[tuple[str, ...]] = set()
    for unit, namespace in namespaces.items():
        if not _repository_path(unit):
            raise ArchitecturePolicyError(
                f"module_id_namespaces entry {unit!r} must be a repository-relative directory"
            )
        if _scope_parts(unit) in units:
            raise ArchitecturePolicyError(
                f"module_id_namespaces names {unit!r} twice"
            )
        units.add(_scope_parts(unit))
        if not isinstance(namespace, str) or not MODULE_ID_NAMESPACE.fullmatch(namespace):
            raise ArchitecturePolicyError(
                f"module_id_namespaces[{unit!r}] must be one lowercase id segment, not {namespace!r}"
            )
    declared = _require_string_list(policy, "declared_dependency_namespaces")
    if len(declared) != len(set(declared)):
        raise ArchitecturePolicyError("declared_dependency_namespaces must not contain duplicates")
    for namespace in declared:
        if namespace not in namespaces.values():
            raise ArchitecturePolicyError(
                f"declared_dependency_namespaces entry {namespace!r} is no namespace that module_id_namespaces "
                "gives a unit, so it holds no import to depends_on"
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
        allowed = rule.get("allowed")
        if allowed is not None:
            if (
                not isinstance(allowed, list)
                or not allowed
                or any(not isinstance(item, str) or not item for item in allowed)
            ):
                raise ArchitecturePolicyError(
                    f"forbidden_layer_imports[{index}].allowed is invalid"
                )
            for item in allowed:
                if not any(_module_matches(item, target) for target in targets):
                    raise ArchitecturePolicyError(
                        f"forbidden_layer_imports[{index}].allowed entry {item!r} is under none of its "
                        "targets, so it excepts nothing"
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


# Directories under a source root that hold no source of this repository:
# bytecode caches, installed environments and vendored third-party trees, such
# as apps/monkeyhub/installer/third-party. A checkout may keep some of them
# untracked (node_modules, a virtualenv), so walking them would also make a
# local run disagree with CI about code nobody here wrote.
NOT_SOURCE_DIRECTORIES = frozenset({
    "__pycache__", ".venv", "node_modules", "site-packages", "third-party", "venv",
})


def _not_source_directory(name: str) -> bool:
    return name in NOT_SOURCE_DIRECTORIES or name.endswith(".egg-info")


def _python_files(root: Path, relative_root: str) -> tuple[Path, ...]:
    """The Python files under one source root; a missing root has none.

    A missing root is not an error here: ``check_policy_paths`` reports it.
    """

    found: list[Path] = []
    for directory, subdirectories, files in os.walk(root / relative_root):
        subdirectories[:] = [
            name for name in subdirectories if not _not_source_directory(name)
        ]
        found.extend(Path(directory, name) for name in files if name.endswith(".py"))
    return tuple(
        sorted(found, key=lambda path: path.relative_to(root).as_posix())
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


def _import_targets(
    nodes: Iterable[ast.AST], module: str | None = None, package: bool = False
) -> Iterator[tuple[tuple[str, ...], int]]:
    """What each import statement names, the module it imports from first.

    ``import a.b`` names ``a.b``; ``from x import y`` names ``x`` and then
    ``x.y``, since ``y`` may be a submodule. A relative import is resolved
    against ``module``, the importing file's import name (``package`` when that
    file is its package's ``__init__.py``), so a rule also holds inside a
    package that imports itself relatively; without a name it is skipped.
    """

    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield (alias.name,), node.lineno
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = [] if module is None else module.split(".")[: None if package else -1]
                if node.level > len(parts):
                    continue
                base = ".".join([*parts[: len(parts) - node.level + 1], *([base] if base else [])])
            if base:
                yield (base, *(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")), node.lineno


def _module_matches(target: str, prefix: str) -> bool:
    return target == prefix or target.startswith(prefix + ".")


def _outside(names: tuple[str, ...], allowed: Iterable[str]) -> str | None:
    """The first name an import statement takes that no allowed entry covers; None when every one is.

    ``names`` is what ``_import_targets`` yields for one statement: the module
    first, then each member a ``from`` import takes. An entry is a module or a
    module's member. The statement is covered when its module is an allowed
    module or inside one, or when each member it takes is: so ``from x import y``
    needs ``x`` or ``x.y`` allowed, while ``import x`` and ``from x import *``
    take the whole module and need ``x`` itself.
    """

    module, members = names[0], names[1:]
    entries = tuple(allowed)
    if any(_module_matches(module, entry) for entry in entries):
        return None
    if not members:
        return module
    return next((member for member in members if not any(_module_matches(member, entry) for entry in entries)), None)


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


def _in_tests(relative: str) -> bool:
    """Is this repository-relative file part of a test suite?

    Judged on the path inside the repository only, so a checkout that happens
    to live under some ``tests`` directory is not taken for a suite.
    """

    return relative.startswith("tests/") or "/tests/" in relative


def _python_source_root(parts: tuple[str, ...], python_source_roots: Iterable[str]) -> tuple[str, ...] | None:
    """The longest Python source root holding a path, as path segments."""

    roots = [
        prefix
        for prefix in map(_scope_parts, python_source_roots)
        if parts[: len(prefix)] == prefix
    ]
    return max(roots, key=len) if roots else None


def check_policy_paths(root: Path, policy: dict[str, Any]) -> Iterator[PolicyFinding]:
    """A configured path must exist; a rule on a missing path guards nothing.

    A checked source root that is gone, or holds no Python source, is walked as
    an empty set, and a layer rule whose source matches no checked file never
    fires: both would pass forever after a move that forgot the policy. The
    Python source roots name where module import names begin, so a ``src``
    directory holding checked Python must be one of them; otherwise its modules
    would be named after the directories around them. A distribution unit that
    names a module-id namespace must exist too. Write sites and authority
    symbols are held to the same rule by ``validate_policy``.
    """

    checked = tuple(
        path.relative_to(root).as_posix()
        for path in _checked_python_files(root, policy)
    )

    def directory_state(relative: str) -> str | None:
        target = root / relative
        if not target.exists():
            return "does not exist"
        return None if target.is_dir() else "is not a directory"

    for source_root in policy["checked_source_roots"]:
        state = directory_state(source_root)
        if state is None and not any(_source_matches(relative, source_root) for relative in checked):
            state = "holds no Python source"
        if state is not None:
            yield PolicyFinding(
                ARCHITECTURE_POLICY, 1, "POLICY_PATH_MISSING",
                f"checked_source_roots entry {source_root!r} {state}; nothing under it is checked",
            )
    for python_root in policy["python_source_roots"]:
        state = directory_state(python_root)
        if state is not None:
            yield PolicyFinding(
                ARCHITECTURE_POLICY, 1, "POLICY_PATH_MISSING",
                f"python_source_roots entry {python_root!r} {state}; no module import name begins there",
            )
    for unit in policy["module_id_namespaces"]:
        state = directory_state(unit)
        if state is not None:
            yield PolicyFinding(
                ARCHITECTURE_POLICY, 1, "POLICY_PATH_MISSING",
                f"module_id_namespaces entry {unit!r} {state}; no module id is checked against it",
            )
    unlisted: set[str] = set()
    for relative in checked:
        parts = _scope_parts(relative)
        prefix = _python_source_root(parts, policy["python_source_roots"]) or ()
        directories = parts[len(prefix):-1]
        if "src" in directories:
            unlisted.add("/".join(parts[: len(prefix) + directories.index("src") + 1]))
    for source in sorted(unlisted):
        yield PolicyFinding(
            ARCHITECTURE_POLICY, 1, "POLICY_PATH_MISSING",
            f"python_source_roots has no entry {source!r}; the modules below it would be "
            "named after the directories around them",
        )
    for index, rule in enumerate(policy["forbidden_layer_imports"]):
        source = rule["source"]
        if any(_source_matches(relative, source) for relative in checked):
            continue
        exists = (root / source).is_dir() or (root / f"{source.rstrip('/')}.py").is_file()
        state = "matches no checked Python file" if exists else "does not exist"
        yield PolicyFinding(
            ARCHITECTURE_POLICY, 1, "POLICY_PATH_MISSING",
            f"forbidden_layer_imports[{index}] source {source!r} {state}; the rule guards nothing",
        )


def _module_names(root: Path, python_source_roots: Iterable[str]) -> frozenset[str]:
    """Every module under the Python source roots, and every package above one.

    The names are import names, as ``_module_name`` gives them. A package is
    named whether or not it has an ``__init__.py``: a directory of modules
    imports as a namespace package. A Python source root inside another is
    walked as its own root, so its modules are not also named after the
    directories around it. A directory whose name cannot be part of an import
    name (``.git``, ``src-tauri``) holds no module and is not walked, nor are
    the trees ``NOT_SOURCE_DIRECTORIES`` names.
    """

    roots = tuple(python_source_roots)
    nested = {"/".join(_scope_parts(python_root)) for python_root in roots}
    names: set[str] = set()
    for python_root in roots:
        for directory, subdirectories, files in os.walk(root / python_root):
            relative = Path(directory).relative_to(root).as_posix()
            prefix = "" if relative == "." else f"{relative}/"
            subdirectories[:] = [
                name for name in subdirectories
                if name.isidentifier() and not _not_source_directory(name) and prefix + name not in nested
            ]
            for name in files:
                module = _module_name(prefix + name, roots) if name.endswith(".py") else None
                if module is not None:
                    parts = module.split(".")
                    names.update(".".join(parts[:end]) for end in range(1, len(parts) + 1))
    return frozenset(names)


def _absolute_imports(nodes: Iterable[ast.AST]) -> Iterator[str]:
    """The modules one file imports by absolute name.

    A relative import names a module of the file's own package, never a
    third-party one, so it is left out.
    """

    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module


def _git_ignored(root: Path, paths: Iterable[str]) -> frozenset[str]:
    """The given repository-relative paths that Git ignores in this checkout.

    ``git check-ignore`` exits 1 when it ignores none of them: an answer, not
    a failure.
    """

    listed = sorted(set(paths))
    if not listed:
        return frozenset()
    try:
        completed = subprocess.run(
            ("git", "check-ignore", "--", *listed),
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise ArchitecturePolicyError(f"git check-ignore failed: {exc}") from exc
    if completed.returncode not in (0, 1):
        raise ArchitecturePolicyError(f"git check-ignore failed: {completed.stderr.strip()}")
    return frozenset(line for line in completed.stdout.splitlines() if line)


def _module_definitions(root: Path, python_source_roots: Iterable[str], module: str) -> frozenset[str] | None:
    """The top-level names one module defines (functions, classes, assignments), or None without its file."""

    roots = tuple(python_source_roots)
    for python_root in roots:
        base = root / python_root / Path(*module.split("."))
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if not candidate.is_file():
                continue
            if _module_name(candidate.relative_to(root).as_posix(), roots) != module:
                continue
            try:
                tree = ast.parse(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError):
                return frozenset()
            names: set[str] = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Assign):
                    names.update(target.id for target in node.targets if isinstance(target, ast.Name))
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
            return frozenset(names)
    return None


def check_layer_targets(
    root: Path,
    policy: dict[str, Any],
    imported: Iterable[str],
) -> Iterator[PolicyFinding]:
    """A layer rule's target names a module; a target that names none forbids nothing.

    Targets are import names, so they resolve the way an import does, not as
    paths. A target resolves when it names a module under the Python source
    roots or a package above one. A name that begins with one of this
    repository's top-level packages must resolve that way: a leftover import
    of a module that has gone does not keep it alive. Any other name is a
    third-party module and resolves while a checked file imports it
    (``imported``, absolute imports only), so it names a library the
    repository really uses. The last kind is a directory Git ignores at a
    Python source root, such as ``archive/``, where retired lanes are kept
    outside the public tree: its modules exist only in the checkouts that keep
    them, and the rule is what stops committed code from reaching for them.
    Every other target is reported once for each rule that names it. A rule's
    ``allowed`` exceptions resolve too: each names a module under the roots or
    a member one module defines at its top level.
    """

    python_roots = policy["python_source_roots"]
    modules = _module_names(root, python_roots)
    first_party = {name.partition(".")[0] for name in modules}
    used = frozenset(imported)
    unresolved: list[tuple[int, str]] = []
    for index, rule in enumerate(policy["forbidden_layer_imports"]):
        for target in rule["targets"]:
            if target in modules:
                continue
            if target.partition(".")[0] not in first_party and any(_module_matches(name, target) for name in used):
                continue
            unresolved.append((index, target))
    directories = {
        top: tuple("/".join((*_scope_parts(python_root), top)) + "/" for python_root in python_roots)
        for top in {target.partition(".")[0] for _, target in unresolved} - first_party
    }
    ignored = _git_ignored(root, (path for paths in directories.values() for path in paths))
    for index, target in unresolved:
        top = target.partition(".")[0]
        if top in first_party:
            parts = target.split(".")
            depth = max(end for end in range(1, len(parts)) if ".".join(parts[:end]) in modules)
            detail = f"names no module: {'.'.join(parts[:depth])} has no module {parts[depth]}"
        elif any(path in ignored for path in directories[top]):
            continue
        else:
            detail = (
                "names no module under the Python source roots, no checked file imports it "
                "and Git ignores no directory of that name"
            )
        yield PolicyFinding(
            ARCHITECTURE_POLICY, 1, "POLICY_TARGET_MISSING",
            f"forbidden_layer_imports[{index}] target {target!r} {detail}; the rule guards nothing against it",
        )
    # An exception names a module or one member a module defines; one that names
    # neither lets nothing through and would hide the rename that emptied it.
    for index, rule in enumerate(policy["forbidden_layer_imports"]):
        for entry in rule.get("allowed", ()):
            if entry in modules:
                continue
            parent, _, member = entry.rpartition(".")
            defined = _module_definitions(root, python_roots, parent) if parent in modules else None
            if defined is not None and member in defined:
                continue
            detail = (f"names no module, and {parent} defines no {member}" if defined is not None
                      else "names no module and no member of one")
            yield PolicyFinding(
                ARCHITECTURE_POLICY, 1, "POLICY_TARGET_MISSING",
                f"forbidden_layer_imports[{index}] allowed {entry!r} {detail}; the exception lets nothing through",
            )


def check_imports(
    relative: str,
    index: _SourceIndex,
    policy: dict[str, Any],
) -> Iterator[PolicyFinding]:
    module = _module_name(relative, policy["python_source_roots"])
    package = relative.rsplit("/", 1)[-1] == "__init__.py"
    framework = _source_matches(relative, policy["source_root"])
    for names, line in _import_targets(index.nodes, module, package):
        probe = next((name for name in names if _module_matches(name, "probes")), None) if framework else None
        if probe is not None:
            yield PolicyFinding(
                relative,
                line,
                "PROBE_REVERSE_IMPORT",
                f"framework imports project probe module {probe!r}",
            )
        for rule in policy["forbidden_layer_imports"]:
            if not _source_matches(relative, rule["source"]):
                continue
            for forbidden in rule["targets"]:
                target = next((name for name in names if _module_matches(name, forbidden)), None)
                if target is not None and rule.get("allowed"):
                    target = _outside(names, rule["allowed"])
                if target is not None:
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


def check_repository_root(root: Path, policy: dict[str, Any]) -> Iterator[PolicyFinding]:
    """The repository root holds only the entries docs/architecture/repository-layout.md names.

    Read from Git's index, not the filesystem: a checkout also holds ignored
    caches, build output and private notes that belong to no layout. The
    allowlist is the whole of the root: a package moved back there is reported
    like any other entry it does not name.
    """

    allowed = set(policy["repository_root_entries"])
    entries = {
        path.split("/", 1)[0]
        for path in _git(root, "ls-files", "-z").split("\0")
        if path
    }
    for entry in sorted(entries - allowed):
        yield PolicyFinding(
            entry, 1, "ROOT_ENTRY",
            f"{entry!r} is not a repository root entry; put it under an existing "
            "top-level directory (docs/architecture/repository-layout.md) or add it to repository_root_entries",
        )


# The docs tree (#494): README.md alone at the root, the rest in category
# directories, lowercase kebab-case names, decisions numbered.
DOCS_DIRECTORY = "docs"
KEBAB_DOC_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.md")
DECISION_DOC_NAME = re.compile(r"\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
KEBAB_DIRECTORY_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# KEBAB_DOC_NAME alone accepts 2026-09-28-construction-api.md; the date
# belongs in the document's front matter (created:), not in its name.
DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}-")
# The one name a convention fixes; GitHub shows it as the directory's page.
CONVENTIONAL_DOC_NAMES = frozenset({"README.md"})
# A prototype's files keep the names its page and scripts load them by.
PROTOTYPE_ASSETS = "docs/prototypes/"


def check_docs_layout(root: Path) -> Iterator[PolicyFinding]:
    """docs/ holds README.md at its root and categorised, kebab-case documents.

    Read from Git's index, like the root entries: a checkout keeps ignored
    private notes under docs/ that belong to no layout. Every other file sits
    in a category directory (finding ``DOCS_ROOT``). A document is lowercase
    kebab-case Markdown, a decision is ``NNN-kebab.md``, a directory is
    kebab-case, and no name begins with a date (finding ``DOC_NAME``).
    README.md is allowed everywhere; the non-Markdown files of a prototype
    under docs/prototypes/ keep their own names, and no other directory holds
    anything but Markdown.
    """

    directories: set[str] = set()
    for path in sorted(
        entry for entry in _git(root, "ls-files", "-z", "--", DOCS_DIRECTORY).split("\0") if entry
    ):
        parts = path.split("/")
        name = parts[-1]
        if len(parts) == 2:
            if name not in CONVENTIONAL_DOC_NAMES:
                yield PolicyFinding(
                    path, 1, "DOCS_ROOT",
                    f"only README.md stays at the docs root; move {name} into its category "
                    "directory (docs/README.md lists them)",
                )
            continue
        for depth in range(2, len(parts)):
            directory = "/".join(parts[:depth])
            if directory not in directories and not KEBAB_DIRECTORY_NAME.fullmatch(parts[depth - 1]):
                directories.add(directory)
                yield PolicyFinding(
                    directory, 1, "DOC_NAME",
                    f"directory {parts[depth - 1]!r} is not lowercase kebab-case",
                )
        if name in CONVENTIONAL_DOC_NAMES:
            continue
        if path.startswith(PROTOTYPE_ASSETS) and not name.endswith(".md"):
            continue
        if DATE_PREFIX.match(name):
            yield PolicyFinding(
                path, 1, "DOC_NAME",
                f"{name} begins with a date; name the subject and put the date in the "
                "document's front matter (created: YYYY-MM-DD)",
            )
        elif parts[1] == "decisions" and len(parts) == 3:
            if not DECISION_DOC_NAME.fullmatch(name):
                yield PolicyFinding(
                    path, 1, "DOC_NAME",
                    f"{name} is not a decision name: NNN-lowercase-kebab-case.md, "
                    "keeping the ADR's three-digit number",
                )
        elif not KEBAB_DOC_NAME.fullmatch(name):
            yield PolicyFinding(
                path, 1, "DOC_NAME",
                f"{name} is not a lowercase kebab-case Markdown name"
                + ("" if name.endswith(".md") else "; only docs/prototypes/ keeps other files"),
            )


def _normalized_body(src: str, node: ast.FunctionDef) -> str:
    seg = ast.get_source_segment(src, node) or ""
    seg = re.sub(r'"""[\s\S]*?"""', "", seg)
    seg = re.sub(r"#.*", "", seg)
    seg = re.sub(r"\s+", " ", seg).strip()
    return re.sub(r"def \w+", "def F", seg)


def _module_name(relative: str, python_source_roots: Iterable[str]) -> str | None:
    """The import name of one repository-relative Python file, or None.

    The name begins below the longest Python source root holding the file, so
    a package under ``packages/<name>/src`` is named as it is imported rather
    than after the directories around it; a package's ``__init__.py`` is the
    package itself.
    """

    parts = _scope_parts(relative)
    if not parts or not parts[-1].endswith(".py"):
        return None
    prefix = _python_source_root(parts, python_source_roots)
    if prefix is None:
        return None
    names = [*parts[len(prefix):-1], parts[-1][:-3]]
    if names[-1] == "__init__":
        names.pop()
    if not names or not all(name.isidentifier() for name in names):
        return None
    return ".".join(names)


def _registry_path_fields(data: dict[str, Any]) -> Iterator[tuple[str, object, str]]:
    """Every path the module registry names, besides owner_path and a module's tests.

    Yields ``(where, value, kind)``: a ``file`` must be a file and a ``path``
    a file or a directory. ``owner_path`` and module tests keep their own
    findings in ``check_registry``; prose fields are not read for paths.
    """

    for entry in data.get("modules", ()):
        module_id = entry.get("module_id", "?")
        for value in entry.get("files") or ():
            yield f"{module_id} files", value, "path"
    spine = data.get("spine") or {}
    if "spec" in spine:
        yield "spine.spec", spine["spec"], "file"
    for value in spine.get("entry_points") or ():
        yield "spine.entry_points", value, "file"
    for interface in data.get("interfaces") or ():
        for implementation in interface.get("implementations") or ():
            if "implementation_file" in implementation:
                yield (
                    f"interface {interface.get('interface_id', '?')} implementation_file",
                    implementation["implementation_file"],
                    "file",
                )
    for capability in data.get("capabilities") or ():
        for value in capability.get("tests") or ():
            yield f"capability {capability.get('capability_id', '?')} tests", value, "file"


def _registry_span(entry: dict[str, Any]) -> list[str]:
    """The paths a registry entry holds: its files, or its owner_path alone.

    A value that is not text names no file; the path check reports it.
    """

    return [path for path in entry.get("files") or [entry.get("owner_path", "")] if isinstance(path, str)]


def _registry_owners(entries: Iterable[dict[str, Any]], python_source_roots: Iterable[str]) -> dict[str, str]:
    """The module id that holds each registered Python file, keyed by the file's import name.

    An owner is known by the name it is imported with, not by its path, and
    every file an entry lists is its own: ``monkeycad.backends.occt.step`` is
    held by ``monkeycad.occt``, whose files list it.
    """

    roots = tuple(python_source_roots)
    owners: dict[str, str] = {}
    for entry in entries:
        for path in _registry_span(entry):
            name = _module_name(path, roots)
            if name is not None:
                owners.setdefault(name, entry.get("module_id", "?"))
    return owners


def _taken_modules(names: tuple[str, ...], modules: frozenset[str]) -> Iterator[str]:
    """The modules one import statement takes, given what ``_import_targets`` names.

    ``import a.b`` takes ``a.b``. ``from a import b`` takes ``a.b`` when that is
    a module, and ``a`` itself when ``b`` is a name ``a`` defines, so a name a
    package's ``__init__.py`` defines is taken from the package.
    """

    module, members = names[0], names[1:]
    submodules = [member for member in members if member in modules]
    if len(submodules) < len(members) or not members:
        yield module
    yield from submodules


def _dependency_findings(
    rel_registry: str,
    entry: dict[str, Any],
    imported: dict[str, str],
    complete: bool,
    known: frozenset[str],
    holders: dict[str, str],
    namespaces: Iterable[str],
) -> Iterator[PolicyFinding]:
    """depends_on names registered modules that a module's files import, and every one of them it must.

    ``imported`` maps each other registered module the files import to the
    first import name that reached it. ``complete`` is False when the files
    hold no Python source that parses, so what they import is unknown and no
    declaration is called stale. An entry that is no registered module id is
    unknown: an import name is answered with the module that holds it
    (``holders``). A module the files import must be declared when its id
    begins with one of ``namespaces``, the policy's
    ``declared_dependency_namespaces``.
    """

    module_id = entry.get("module_id", "?")
    declared = [value for value in entry.get("depends_on", ()) if isinstance(value, str)]
    for dependency in entry.get("depends_on", ()):
        if not isinstance(dependency, str) or dependency not in known:
            holder = holders.get(dependency) if isinstance(dependency, str) else None
            if holder is None:
                detail = "; depends_on lists module ids, and no entry's files hold a module of that name"
            elif holder == module_id:
                detail = " but the import name of one of its own files; drop it"
            else:
                detail = f" but an import name; {holder} holds that module, so name {holder}"
            yield PolicyFinding(
                rel_registry, 1, "REGISTRY_DEPENDS_ON_UNKNOWN",
                f"{module_id} depends_on {dependency!r} is not a registered module id{detail}",
            )
        elif dependency == module_id:
            yield PolicyFinding(rel_registry, 1, "REGISTRY_DEPENDS_ON_STALE", f"{module_id} names itself in depends_on")
        elif complete and dependency not in imported:
            yield PolicyFinding(
                rel_registry, 1, "REGISTRY_DEPENDS_ON_STALE",
                f"{module_id} declares {dependency} in depends_on, but none of its files imports a module "
                f"{dependency} holds; drop it",
            )
    required = frozenset(namespaces)
    undeclared = sorted(
        (holder, name) for holder, name in imported.items()
        if holder.split(".", 1)[0] in required and holder not in declared
    )
    if undeclared:
        taken = ", ".join(holder if name == holder else f"{name} (held by {holder})" for holder, name in undeclared)
        yield PolicyFinding(
            rel_registry, 1, "REGISTRY_DEPENDS_ON_DRIFT",
            f"{module_id} imports {taken} but depends_on does not name {', '.join(holder for holder, _ in undeclared)}",
        )


def _id_namespace(path: str, namespaces: dict[str, str]) -> tuple[str, str] | None:
    """The distribution unit holding a repository path, and its module-id namespace.

    Units are matched on whole path segments, so ``tools`` holds
    ``tools/governance/archcheck.py`` but not ``toolsets/check.py``; the
    longest unit holding the path wins.
    """

    parts = _scope_parts(path)
    held = [
        (len(_scope_parts(unit)), unit, namespace)
        for unit, namespace in namespaces.items()
        if parts[: len(_scope_parts(unit))] == _scope_parts(unit)
    ]
    if not held:
        return None
    _, unit, namespace = max(held)
    return unit, namespace


def _id_namespace_problem(module_id: str, owner_path: str, namespaces: dict[str, str]) -> str | None:
    """Why a module id does not begin with its owner's namespace, or None when it does."""

    found = _id_namespace(owner_path, namespaces)
    if found is None:
        return (
            f"{module_id}: owner_path {owner_path} is in no distribution unit that module_id_namespaces "
            "names, so its id has no namespace to begin with; name the unit there or move the owner into one"
        )
    unit, namespace = found
    first = module_id.split(".", 1)[0]
    if first == namespace:
        return None
    return (
        f"{module_id}: owner_path {owner_path} is in {unit}, whose module ids begin with "
        f"{namespace!r}, but this id begins with {first!r}"
    )


def check_registry(root: Path, policy: dict[str, Any]) -> Iterator[PolicyFinding]:
    """The module registry must tell the truth, and a capability has one owner.

    Owner paths exist; every public_api symbol is defined in its owner; listed
    tests exist; every other path the registry names exists; every string in
    ``owns`` appears in exactly one entry; and no spine module outside an owner
    defines a function whose normalised body equals one of the owner's
    functions (a copied helper is a duplicate owner).

    ``depends_on`` lists registered module ids (#537). A module's imports are
    resolved the way ``_import_targets`` names them, relative imports
    included, and matched to the entry whose files hold the imported module,
    by import names that begin at the policy's ``python_source_roots``. An
    entry that is no module id is ``REGISTRY_DEPENDS_ON_UNKNOWN``; one whose
    module the files no longer import is ``REGISTRY_DEPENDS_ON_STALE``; an
    imported module in one of the policy's ``declared_dependency_namespaces``
    that depends_on does not name is ``REGISTRY_DEPENDS_ON_DRIFT``. An import
    of a module no entry holds is matched to nothing. Who uses a module is
    read from the same imports: the hand-kept ``used_by`` field is retired
    (``REGISTRY_RETIRED_FIELD``).

    A module id's first segment is the namespace of the distribution unit
    holding its ``owner_path``, as the policy's ``module_id_namespaces`` maps
    them: ``packages/<p>/src/<p>`` is ``<p>``, the Project Runtime is
    ``project_runtime``, the Hub API ``hub`` and ``tools/`` ``tools``. An id
    named after a product, a retired service or another package's
    subpackage is ``REGISTRY_ID_NAMESPACE``, and so is an owner outside every
    unit. The rest of the id is not checked: MonkeyCAD and the Hub name
    their owners by capability.
    """

    registry_path = root / "governance" / "module_registry.json"
    if not registry_path.is_file():
        return
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    entries = data.get("modules", [])
    rel_registry = registry_path.relative_to(root).as_posix()
    namespaces = policy["module_id_namespaces"]
    python_roots = policy["python_source_roots"]
    owners: dict[str, str] = {}
    ids: set[str] = set()
    owner_bodies: dict[str, tuple[str, str]] = {}
    known = frozenset(entry.get("module_id", "?") for entry in entries)
    holders = _registry_owners(entries, python_roots)
    modules = _module_names(root, python_roots)
    for entry in entries:
        module_id = entry.get("module_id", "?")
        if module_id in ids:
            yield PolicyFinding(rel_registry, 1, "REGISTRY_DUPLICATE_MODULE", f"module_id {module_id} listed twice")
        ids.add(module_id)
        problem = _id_namespace_problem(module_id, entry.get("owner_path", ""), namespaces)
        if problem is not None:
            yield PolicyFinding(rel_registry, 1, "REGISTRY_ID_NAMESPACE", problem)
        if "used_by" in entry:
            yield PolicyFinding(
                rel_registry, 1, "REGISTRY_RETIRED_FIELD",
                f"{module_id}: used_by is retired (#537); the modules that use it are read from their imports",
            )
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
        defined: set[str] = set()
        # Each other registered module the files import, with the first import name that reached it.
        imported: dict[str, str] = {}
        parsed = unparsed = 0
        for relative in _registry_span(entry):
            path = root / relative
            if path.suffix != ".py" or not path.is_file():
                continue
            src = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                unparsed += 1
                continue
            parsed += 1
            defined |= {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            defined |= {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
            defined |= {n.target.id for n in tree.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("__"):
                    body = _normalized_body(src, node)
                    if len(body) > 80:
                        owner_bodies.setdefault(body, (module_id, node.name))
            name = _module_name(relative, python_roots)
            for targets, _ in _import_targets(ast.walk(tree), name, path.name == "__init__.py"):
                for taken in _taken_modules(targets, modules):
                    holder = holders.get(taken)
                    if holder is not None and holder != module_id:
                        imported.setdefault(holder, taken)
        for symbol in entry.get("public_api", ()):
            if symbol.isidentifier() and symbol not in defined:
                yield PolicyFinding(rel_registry, 1, "REGISTRY_SYMBOL_MISSING", f"{module_id}: public_api symbol {symbol} is not defined in {entry['owner_path']} or its files")
        yield from _dependency_findings(
            rel_registry, entry, imported, bool(parsed) and not unparsed, known, holders,
            policy["declared_dependency_namespaces"],
        )
        if not entry.get("tests") and not entry.get("untested_reason"):
            yield PolicyFinding(rel_registry, 1, "REGISTRY_UNTESTED_OWNER", f"{module_id} lists no test and gives no untested_reason")
    for where, value, kind in _registry_path_fields(data):
        if not isinstance(value, str) or not _repository_path(value):
            yield PolicyFinding(rel_registry, 1, "REGISTRY_PATH_MISSING", f"{where}: {value!r} is not a literal repository-relative path")
        elif not (root / value).exists():
            yield PolicyFinding(rel_registry, 1, "REGISTRY_PATH_MISSING", f"{where}: {value} does not exist")
        elif kind == "file" and not (root / value).is_file():
            yield PolicyFinding(rel_registry, 1, "REGISTRY_PATH_MISSING", f"{where}: {value} is not a file")
    owner_paths = {
        "/".join(_scope_parts(f))
        for e in entries
        for f in _registry_span(e)
    }
    for path in _checked_python_files(root, policy):
        relative_path = path.relative_to(root).as_posix()
        if relative_path in owner_paths or _in_tests(relative_path) or path.name == "__init__.py":
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
# Since #358 the registry holds live GitHub Issue claims only. A commit whose
# own registry has an older schema was made before that; see LEGACY_* below.
REGISTRY_SCHEMA = "ArchFlowDevelopmentRegistry@3"
WORK_ID = re.compile(r"GH-[1-9]\d*")
LANE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
CLAIM_ID = re.compile(r"GH-[1-9]\d*(?:/[a-z0-9][a-z0-9_-]*)?")
WORK_CLAIM = re.compile(r"\b" + CLAIM_ID.pattern + r"(?![\w/-])")
CLAIM_STATUSES = frozenset({"active", "review", "blocked"})
LIVE_CLAIM_STATUSES = frozenset({"active", "review"})
# A claim is one Issue claimed directly, or one lane of an Issue claimed
# through lanes; such an Issue then carries nothing but its lanes.
CLAIM_FIELDS = frozenset({
    "id", "status", "branch", "worktree", "base_ref", "contributor", "reviewer",
    "handoff", "modules", "write_scope", "depends_on", "blocked_reason",
})
LANED_ISSUE_FIELDS = frozenset({"id", "lanes"})
# A current commit whose subject opens with a card-era id still declares one.
RETIRED_CLAIM = re.compile(r"\s*(P000-governance|[PMR]\d{3})(?!\d)")

# Commits made before #358 keep the reading they were checked with: P### cards
# and their lanes, the P000-governance marker, GitHub claims, active or ready
# cards, and these two path lists. Nothing current depends on them.
LEGACY_LIVE_STATUSES = frozenset({"active", "ready"})
LEGACY_CARD_ID = re.compile(
    r"\b(?:"
    r"P000-governance(?![\w-])"
    r"|P\d{3}(?!\d)(?:/[a-z0-9][a-z0-9_-]*)?"
    r"|GH-[1-9]\d*(?:/[a-z0-9][a-z0-9_-]*)?(?![\w/-])"
    r")"
)
LEGACY_UNCARDED_WRITE_SCOPE = ("docs/adr/", "docs/REPO_LAYOUT.md", "CONTRIBUTING.md")
LEGACY_GOVERNANCE_WRITE_SCOPE = LEGACY_UNCARDED_WRITE_SCOPE + (
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


def _claim_problems(row: dict[str, Any]) -> list[str]:
    """Why one claim's coordination fields cannot be used, in reading order."""

    problems = []
    status = row.get("status") if isinstance(row.get("status"), str) else None
    if status not in CLAIM_STATUSES:
        problems.append(
            f"status must be one of {sorted(CLAIM_STATUSES)}; finished work is released, not kept as done"
        )
    for field in ("branch", "worktree", "base_ref", "contributor", "reviewer", "handoff"):
        value = row.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            problems.append(f"{field} must be non-empty text or null")
        if status in LIVE_CLAIM_STATUSES and field in ("branch", "worktree", "base_ref", "contributor") and not value:
            problems.append(f"{field} is required for active/review work")
    for field in ("modules", "write_scope", "depends_on"):
        values = row.get(field)
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            problems.append(f"{field} must be a list of non-empty strings")
    if isinstance(row.get("modules"), list) and not row["modules"]:
        problems.append("modules must identify an intended owner")
    scope = row.get("write_scope")
    if isinstance(scope, list):
        if status in LIVE_CLAIM_STATUSES and not scope:
            problems.append("active/review work requires narrow write_scope")
        for path in scope:
            if isinstance(path, str) and (path.startswith("/") or any(c in path for c in "\\:*?[]{}") or
                                          any(p in ("", ".", "..") for p in path.rstrip("/").split("/"))):
                problems.append(f"write_scope {path!r} must be a literal repository-relative file or directory")
    dependencies = row.get("depends_on")
    if isinstance(dependencies, list):
        for dependency in dependencies:
            if isinstance(dependency, str) and dependency.strip() and not CLAIM_ID.fullmatch(dependency):
                problems.append(f"depends_on {dependency!r} must name GH-<issue> or GH-<issue>/<lane>")
    if status == "blocked" and (not isinstance(row.get("blocked_reason"), str) or not row["blocked_reason"].strip()):
        problems.append("blocked_reason is required for blocked work")
    return problems


def _unexpected_fields(name: str, row: dict[str, Any], allowed: frozenset[str]) -> Iterator[PolicyFinding]:
    extra = sorted(str(field) for field in set(row) - allowed)
    if not extra:
        return
    listed = ", ".join(extra)
    if allowed == LANED_ISSUE_FIELDS and set(extra) <= CLAIM_FIELDS:
        reason = f"{listed} belong on its lanes; an Issue claimed through lanes carries only id and lanes"
    else:
        reason = (
            f"{listed} {'is' if len(extra) == 1 else 'are'} not live coordination; the GitHub Issue holds "
            "goal and acceptance, and Git history the finished work (#358)"
        )
    yield PolicyFinding(WORK_REGISTRY, 1, "WORK_ITEM", f"{name}: {reason}")


def check_scopes(
    root: Path,
    policy: dict[str, Any],
    registry: dict[str, Any] | None,
) -> Iterator[PolicyFinding]:
    """Validate the live claims and report competing path and checkout claims.

    Since #358 every entry is a GitHub Issue, claimed directly or through named
    lanes that are each a claim of their own. Only active and review claims
    hold paths and checkouts; shared tests and ledgers are exempt according to
    the policy.
    """

    if registry is None:
        return
    shared = policy["shared_write_scope"]
    if registry.get("schema") != REGISTRY_SCHEMA:
        yield PolicyFinding(
            WORK_REGISTRY, 1, "WORK_ITEM",
            f"schema must be {REGISTRY_SCHEMA}: since #358 the registry holds live "
            "GitHub Issue claims only (GH-<issue>, GH-<issue>/<lane>)",
        )
        return
    yield from _unexpected_fields("the registry", registry, frozenset({"schema", "items"}))
    live = []
    registered: set[str] = set()
    for item in registry["items"]:
        if not isinstance(item, dict):
            yield PolicyFinding(WORK_REGISTRY, 1, "WORK_ITEM", f"every work item must be an object, not {item!r}")
            continue
        work_id = item.get("id")
        if not isinstance(work_id, str) or not WORK_ID.fullmatch(work_id):
            yield PolicyFinding(
                WORK_REGISTRY, 1, "WORK_ITEM",
                f"{work_id!r}: a work item is a GitHub Issue, GH-<issue>; the P/M/R cards retired with #358",
            )
            continue
        if work_id in registered:
            yield PolicyFinding(WORK_REGISTRY, 1, "WORK_ITEM", f"{work_id} is listed twice; claim more of it with lanes")
            continue
        registered.add(work_id)
        if "lanes" not in item:
            yield from _unexpected_fields(work_id, item, CLAIM_FIELDS)
            claims = [(work_id, item)]
        else:
            yield from _unexpected_fields(work_id, item, LANED_ISSUE_FIELDS)
            lanes = item["lanes"]
            if not isinstance(lanes, list) or not lanes:
                yield PolicyFinding(
                    WORK_REGISTRY, 1, "LANE_METADATA",
                    f"{work_id}: lanes must list its live lanes; remove the entry once every lane is released",
                )
                continue
            claims = []
            seen = set()
            for lane in lanes:
                if not isinstance(lane, dict):
                    yield PolicyFinding(WORK_REGISTRY, 1, "LANE_METADATA", f"{work_id}: lane must be an object")
                    continue
                lane_id = lane.get("id")
                name = f"{work_id}/{lane_id}"
                yield from _unexpected_fields(name, lane, CLAIM_FIELDS)
                if not isinstance(lane_id, str) or not LANE_NAME.fullmatch(lane_id):
                    yield PolicyFinding(WORK_REGISTRY, 1, "LANE_METADATA", f"{name}: id must be a short lowercase lane name")
                    continue
                if lane_id in seen:
                    yield PolicyFinding(WORK_REGISTRY, 1, "LANE_METADATA", f"{name}: duplicate lane id")
                    continue
                seen.add(lane_id)
                claims.append((name, lane))
        for name, row in claims:
            problems = _claim_problems(row)
            for problem in problems:
                yield PolicyFinding(WORK_REGISTRY, 1, "LANE_METADATA", f"{name}: {problem}")
            if problems:
                continue
            registered.add(name)
            if row["status"] in LIVE_CLAIM_STATUSES:
                live.append({**row, "id": name})
    for claim in live:
        waiting = sorted(set(claim["depends_on"]) & registered - {claim["id"]})
        if waiting:
            yield PolicyFinding(WORK_REGISTRY, 1, "LANE_DEPENDENCY", f"{claim['id']} cannot be {claim['status']} while waiting for {', '.join(waiting)}; mark it blocked and record the handoff/order")
    live.sort(key=lambda item: str(item.get("id", "")))
    for index, first in enumerate(live):
        for second in live[index + 1 :]:
            for field in ("branch", "worktree"):
                left_value, right_value = first.get(field), second.get(field)
                if not left_value or not right_value:
                    continue
                identities = [
                    ntpath.normcase(ntpath.normpath(value))
                    if field == "worktree" and ntpath.splitdrive(value)[0]
                    else value.replace("\\", "/").rstrip("/")
                    for value in (left_value, right_value)
                ]
                if identities[0] == identities[1]:
                    yield PolicyFinding(WORK_REGISTRY, 1, "LANE_CHECKOUT", f"{first['id']} and {second['id']} share {field} {left_value!r}; use independent short branches/worktrees")
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
                    "narrow the claims or mark the later lane blocked with depends_on and an explicit handoff/order; shared surfaces come from policy",
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


def _commit_claim(message: str, grammar: re.Pattern[str] = WORK_CLAIM) -> str | None:
    """The work claim in the subject, else in the body.

    The subject wins because a body says things about other work -- what this
    change unblocks, which item a finding belongs to -- and a commit would
    otherwise be filed under whichever claim it mentioned last. Within one part
    of the message the last id still wins, so a subject or a body naming its
    claim twice is unambiguous. A card-era commit is read with
    ``LEGACY_CARD_ID``, which also knows P### and P000-governance.
    """

    subject = message.splitlines()[0] if message.strip() else ""
    found = grammar.findall(subject) or grammar.findall(message)
    return found[-1] if found else None


def _legacy_issue_suffix(message: str) -> str | None:
    """A card-era subject that ends with one ``(#<n>)`` and names no other Issue.

    Some commits made before #358 carry their Issue only as that suffix
    (GH-319's branch has one). It identifies the claim and nothing more: the
    commit still meets that claim's scope as registered then. Incidental,
    ambiguous or malformed references, and any message with a ``GH-`` marker,
    claim nothing. Current commits name ``GH-<issue>`` explicitly.
    """

    subject = message.splitlines()[0] if message.strip() else ""
    suffix = re.search(r"(?:^|\s)\(#([1-9][0-9]*)\)\s*$", subject)
    if suffix and re.findall(r"#\d+", subject) == [f"#{suffix[1]}"] and "GH-" not in message:
        return f"GH-{suffix[1]}"
    return None


def _paths(value: object) -> list[str]:
    return [entry for entry in value if isinstance(entry, str)] if isinstance(value, list) else []


def _live(row: object) -> bool:
    return isinstance(row, dict) and isinstance(row.get("status"), str) and row["status"] in LIVE_CLAIM_STATUSES


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


def _lane(card: dict[str, Any], lane_id: str) -> dict[str, Any] | None:
    lanes = card.get("lanes", ())
    if not isinstance(lanes, (list, tuple)):
        return None
    return next((row for row in lanes if isinstance(row, dict) and row.get("id") == lane_id), None)


def check_changed_scopes(
    root: Path,
    base: str,
    policy_path: str = ARCHITECTURE_POLICY,
) -> Iterator[PolicyFinding]:
    """Check the scope that applied when each commit was made.

    Policy and claims come from the commit, not today's live registry. A
    claim closed by a commit may use its first parent's live scope. The rule
    starts after a parent first has ``shared_write_scope``; removing that
    configuration later is an error, not a way to turn the check off.

    A commit whose own registry is ``REGISTRY_SCHEMA`` claims a GitHub Issue
    or one of its lanes; without a claim it may write the policy's shared and
    unclaimed paths. A commit on an older registry was made before #358 and
    keeps the card-era reading it was checked with. Once a parent is on the
    current registry, returning to the old one is an error, so that reading
    cannot authorize card work again.
    """

    policies: dict[str, dict[str, Any] | None] = {}
    registries: dict[str, dict[str, Any] | None] = {}
    enabled: dict[str, bool] = {}

    def policy_at(revision: str) -> dict[str, Any] | None:
        if revision not in policies:
            policies[revision] = _git_json(root, revision, policy_path)
        return policies[revision]

    def registry_at(revision: str) -> dict[str, Any] | None:
        if revision not in registries:
            registries[revision] = _git_json(root, revision, WORK_REGISTRY)
        return registries[revision]

    def retired_at(revision: str) -> bool:
        registry = registry_at(revision)
        return registry is not None and registry.get("schema") == REGISTRY_SCHEMA

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

    def items_at(revision: str) -> list[Any]:
        registry = registry_at(revision)
        if registry is None or not isinstance(registry.get("items"), list):
            raise ArchitecturePolicyError(
                f"invalid work registry at {revision[:8]}: {WORK_REGISTRY}"
            )
        return registry["items"]

    def cards_at(revision: str) -> dict[str, dict[str, Any]]:
        return {
            str(item.get("id")): item
            for item in items_at(revision)
            if isinstance(item, dict) and item.get("status") in LEGACY_LIVE_STATUSES
        }

    def claims_at(revision: str) -> dict[str, tuple[list[str], ...] | None]:
        """Each registered claim: the scopes that must all cover a path it
        writes, or None while it is registered but not live."""

        claims: dict[str, tuple[list[str], ...] | None] = {}
        if not retired_at(revision):
            # A card-era first parent: a lane also stays inside its card.
            for card_id, card in cards_at(revision).items():
                ceiling = _paths(card.get("write_scope"))
                if "lanes" not in card:
                    claims[card_id] = (ceiling,)
                    continue
                for lane in card["lanes"] if isinstance(card["lanes"], list) else ():
                    if _live(lane):
                        claims[f"{card_id}/{lane.get('id')}"] = (_paths(lane.get("write_scope")), ceiling)
            return claims
        for item in items_at(revision):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            if "lanes" not in item:
                rows = [(item["id"], item)]
            elif isinstance(item["lanes"], list):
                rows = [(f"{item['id']}/{lane.get('id')}", lane) for lane in item["lanes"] if isinstance(lane, dict)]
            else:
                rows = []
            for name, row in rows:
                claims[name] = (_paths(row.get("write_scope")),) if _live(row) else None
        return claims

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
        retired = retired_at(revision)
        if not retired and any(retired_at(parent) for parent in parents):
            raise ArchitecturePolicyError(
                f"{WORK_REGISTRY} at {revision[:8]} is not {REGISTRY_SCHEMA}: after #358 "
                "a commit cannot return to the card-era registry"
            )
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
        if retired:
            subject = message.splitlines()[0] if message.strip() else ""
            declared = RETIRED_CLAIM.match(subject)
            if declared:
                yield PolicyFinding(
                    revision[:8], 1, "RETIRED_WORK_CLAIM",
                    f"commit {revision[:8]} opens with {declared.group(1)}, a card-era work id; "
                    "since #358 a commit claims GH-<issue> or GH-<issue>/<lane>",
                )
            claim_id = _commit_claim(message)
            claims = claims_at(revision)
            previous: dict[str, tuple[list[str], ...] | None] = {}
            if claim_id in claims:
                scopes = claims[claim_id]
            else:
                # Only the commit that removes a claim writes on its parent's
                # scope; one that keeps it registered but not live does not.
                previous = claims_at(parents[0]) if claim_id and parents else {}
                scopes = previous.get(claim_id)
            if scopes is not None:
                required = tuple(scope + shared for scope in scopes)
                code = "SCOPE_VIOLATION"
                named = f"commit {revision[:8]} is {claim_id}"
            else:
                unclaimed = _require_string_list(policy, "unclaimed_write_scope") if "unclaimed_write_scope" in policy else []
                required = (shared + unclaimed,)
                code = "SCOPE_UNDECLARED"
                if claim_id is None:
                    named = f"commit {revision[:8]} names no work item"
                elif any(name.startswith(claim_id + "/") for name in (*claims, *previous)):
                    named = f"commit {revision[:8]} names {claim_id}, which is claimed through its lanes; name {claim_id}/<lane>"
                else:
                    named = f"commit {revision[:8]} names {claim_id}, which has no live claim at that commit"
            for path in files:
                if not all(_covered_by_any(path, scope) for scope in required):
                    yield PolicyFinding(path, 1, code, f"{named}; {path} is outside its write scope")
            continue
        cards = cards_at(revision)
        claim_id = _commit_claim(message, LEGACY_CARD_ID) or _legacy_issue_suffix(message)
        card_id, _, lane_id = (claim_id or "").partition("/")
        card_id = card_id or None
        card = cards.get(card_id) if card_id else None
        if card is None and card_id not in (None, "P000-governance") and parents:
            card = cards_at(parents[0]).get(card_id)
        parent_allowed = None
        if card_id == "P000-governance":
            allowed = shared + list(LEGACY_GOVERNANCE_WRITE_SCOPE)
            code = "SCOPE_VIOLATION"
            named = f"commit {revision[:8]} is {card_id}"
        elif card is None:
            allowed = shared + list(LEGACY_UNCARDED_WRITE_SCOPE)
            code = "SCOPE_UNDECLARED"
            named = (
                f"commit {revision[:8]} names no work item"
                if card_id is None
                else f"commit {revision[:8]} names {card_id}, which has no scope at that commit"
            )
        else:
            allowed = list(card.get("write_scope", ())) + shared
            code = "SCOPE_VIOLATION"
            named = f"commit {revision[:8]} is {card_id}"
            if "lanes" in card:
                parent_allowed = allowed
                lane = _lane(card, lane_id)
                if lane_id and parents and (lane is None or lane.get("status") == "done"):
                    previous = cards_at(parents[0]).get(card_id, {})
                    lane = _lane(previous, lane_id)
                if lane is not None and lane.get("status") not in ("active", "review"):
                    lane = None
                allowed = list(lane.get("write_scope", ())) + shared if lane is not None else shared
                named = f"commit {revision[:8]} is {claim_id}" if lane is not None else f"commit {revision[:8]} must name an active/review {card_id}/<lane>"
        for path in files:
            if (_covered_by_any(path, allowed) and (parent_allowed is None or _covered_by_any(path, parent_allowed))) or (
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
    """Check the tree under ``root``, which must be a Git checkout.

    The root entries and the docs tree are read from Git's index
    (``check_repository_root``, ``check_docs_layout``), and a layer-rule
    target that no module or import accounts for is looked up in Git's ignore
    rules (``check_layer_targets``); everything else is read from the files
    on disk.
    """

    validate_policy(policy, root)
    findings: list[PolicyFinding] = list(check_probe_boundary(root, policy))
    findings.extend(check_policy_paths(root, policy))
    findings.extend(check_repository_root(root, policy))
    findings.extend(check_docs_layout(root))
    findings.extend(check_registry(root, policy))
    findings.extend(check_scopes(root, policy, load_work_registry(root)))
    imported: set[str] = set()
    for path in _checked_python_files(root, policy):
        relative = path.relative_to(root).as_posix()
        tree, parse_finding = _parse(path, root)
        if parse_finding is not None:
            findings.append(parse_finding)
            continue
        assert tree is not None
        index = _index_tree(tree)
        imported.update(_absolute_imports(index.nodes))
        checks: list[Iterable[PolicyFinding]] = [
            check_imports(relative, index, policy)
        ]
        if not _in_tests(relative) and not _is_import_only(relative, policy) and any(_source_matches(relative, root_prefix) for root_prefix in policy["checked_source_roots"]):
            checks.extend(
                (
                    check_instance_answers(relative, index, policy),
                    check_filesystem_writes(relative, index, policy),
                    check_authority_symbols(relative, index, policy),
                )
            )
        for result in checks:
            findings.extend(result)
    findings.extend(check_layer_targets(root, policy, imported))
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
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--changed",
        metavar="BASE",
        help=(
            "Check commits in BASE..HEAD against the work scopes in each "
            "commit's own registry, after the rule first exists in a parent. A "
            "commit names GH-<issue> or GH-<issue>/<lane> in its subject, else "
            "in its body."
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
