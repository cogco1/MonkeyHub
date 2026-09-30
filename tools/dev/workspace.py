"""Configure the development root and create or reuse task Git worktrees.

The existing personal Git setting also supplies the packaging CLI. Git owns
worktree registration; this tool never copies, moves or deletes project data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


SOURCE_ROOT = Path(__file__).resolve().parents[1]
# Retain the already-configured key instead of introducing a second root.
WORKSPACE_CONFIG_KEY = "archflow.package.workspace-root"


def git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=source, check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


def configured_root(source: Path) -> Path | None:
    try:
        value = git(source, "config", "--get", WORKSPACE_CONFIG_KEY)
    except subprocess.CalledProcessError as error:
        if error.returncode == 1:
            return None
        raise
    return validate_root(source, Path(value).expanduser())


def validate_root(source: Path, root: Path) -> Path:
    if not root.is_absolute():
        raise ValueError("The workspace root must be an absolute directory.")
    root, source = root.resolve(), source.resolve()
    if root == Path(root.anchor) or root == source or root.is_relative_to(source):
        raise ValueError("Choose a workspace directory outside the source checkout, not a drive root.")
    return root


def configure_root(source: Path, root: Path) -> Path:
    root = validate_root(source, root.expanduser())
    git(source, "config", "--global", "--replace-all", WORKSPACE_CONFIG_KEY, str(root))
    return root


def task_name(source: Path, requested: str | None = None, *, branch: str | None = None) -> str:
    if requested is None:
        branch = branch or git(source, "branch", "--show-current")
        if not branch:
            branch = "detached-" + git(source, "rev-parse", "--short=12", "HEAD")
        requested = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip(".-") or "task"
    reserved = requested.split(".")[0].upper()
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", requested)
            or requested.endswith(".") or reserved in {"CON", "PRN", "AUX", "NUL"}
            or re.fullmatch(r"(?:COM|LPT)[1-9]", reserved)):
        raise ValueError("--task must be one portable directory name using letters, digits, '.', '_' or '-'.")
    return requested


def task_paths(root: Path, task: str) -> dict[str, Path]:
    paths = {
        "worktreeDir": root / "workspace/worktrees" / task,
        "stagingDir": root / "temp/package-monkeyapps" / task,
        "outputDir": root / "packages" / task,
        "cacheDir": root / "cache/package-monkeyapps",
    }
    resolved = {key: path.resolve() for key, path in paths.items()}
    if any(not path.is_relative_to(root) for path in resolved.values()):
        raise ValueError("A workspace directory link resolves outside the configured root.")
    return resolved


def create_worktree(source: Path, destination: Path, branch: str, base: str | None = None) -> Path:
    git(source, "check-ref-format", "--branch", branch)
    records = git(source, "worktree", "list", "--porcelain", "-z")
    for block in records.split("\0\0"):
        fields = dict(field.split(" ", 1) for field in block.split("\0") if " " in field)
        if "worktree" not in fields:
            continue
        path = Path(fields["worktree"]).resolve()
        if fields.get("branch") == "refs/heads/" + branch:
            if path != destination:
                raise ValueError(f"Branch {branch} already has a worktree at {path}; no directory was moved or created.")
            if not destination.is_dir():
                raise ValueError(f"Git registers {destination}, but the directory is missing; inspect it before continuing.")
            if base is not None:
                raise ValueError("--base is only for a new branch; an existing worktree is never reset.")
            return destination
        if path == destination:
            raise ValueError(f"The requested directory is already another Git worktree: {destination}")
    if destination.exists():
        raise ValueError(f"The requested directory already exists and is not this branch's worktree: {destination}")
    try:
        git(source, "show-ref", "--verify", "--quiet", "refs/heads/" + branch)
        exists = True
    except subprocess.CalledProcessError as error:
        if error.returncode != 1:
            raise
        exists = False
    if exists and base is not None:
        raise ValueError("--base is only for a new branch; an existing branch is never reset.")
    start = None if exists else git(source, "rev-parse", "--verify", (base or "HEAD") + "^{commit}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if exists:
        git(source, "worktree", "add", str(destination), branch)
    else:
        git(source, "worktree", "add", "-b", branch, str(destination), start)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure", help="save the personal root once; create no worktree")
    configure.add_argument("--root", required=True, type=Path)
    paths = commands.add_parser("paths", help="show resolved paths without creating directories")
    paths.add_argument("--task")
    create = commands.add_parser("create", help="create or reuse a branch at its configured task location")
    create.add_argument("--branch", required=True, help="for example codex/window-edit")
    create.add_argument("--task", help="defaults to the branch name with slashes replaced by hyphens")
    create.add_argument("--base", help="start a new branch at this commit/ref; defaults to source HEAD")
    args = parser.parse_args(argv)
    try:
        source = args.source_root.resolve()
        root = configure_root(source, args.root) if args.command == "configure" else configured_root(source)
        if root is None:
            raise ValueError("Run tools/workspace.py configure --root <external directory> once.")
        task = task_name(source, getattr(args, "task", None), branch=getattr(args, "branch", None))
        selected = task_paths(root, task)
        if args.command == "create":
            create_worktree(source, selected["worktreeDir"], args.branch, args.base)
        print(json.dumps({
            "sourceRoot": str(source), "workspaceRoot": str(root), "task": task,
            **{key: str(value) for key, value in selected.items()},
        }, ensure_ascii=False, indent=2))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        parser.exit(1, f"workspace: {detail}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
