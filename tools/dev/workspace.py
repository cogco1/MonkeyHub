"""Configure the development root, create or reuse task Git worktrees and retire merged ones.

The existing personal Git setting also supplies the packaging CLI. Git owns
worktree registration and branches. Retiring renames a merged, clean task
worktree into the root's _TRASH_<YYYYMMDD> folder before Git prunes its
registration and deletes its merged branch; the tool itself never deletes a
file and never copies or moves project data.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess


SOURCE_ROOT = Path(__file__).resolve().parents[2]
# Retain the already-configured key instead of introducing a second root.
WORKSPACE_CONFIG_KEY = "archflow.package.workspace-root"
RETIRE_STATES = ("merged-clean", "unmerged", "local-changes", "detached", "locked", "missing-directory")


def git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=source, check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


def _git_error(error: subprocess.CalledProcessError) -> str:
    return (error.stderr or "").strip() or str(error)


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


def _worktree_records(source: Path) -> list[dict[str, str]]:
    """Git's registered worktrees, the main checkout first; a bare attribute such as locked maps to ""."""
    records = []
    for block in git(source, "worktree", "list", "--porcelain", "-z").split("\0\0"):
        fields = dict(field.partition(" ")[::2] for field in block.split("\0") if field)
        if "worktree" in fields:
            records.append(fields)
    return records


def create_worktree(source: Path, destination: Path, branch: str, base: str | None = None) -> Path:
    git(source, "check-ref-format", "--branch", branch)
    for fields in _worktree_records(source):
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


def _is_ancestor(source: Path, commit: str, ref: str) -> bool:
    try:
        git(source, "merge-base", "--is-ancestor", commit, ref)
    except subprocess.CalledProcessError as error:
        if error.returncode != 1:
            raise
        return False
    return True


def _move_into_trash(directory: Path, destination: Path) -> None:
    """One rename into the root's trash on the root's volume; never a copy or a delete."""
    destination.parent.mkdir(exist_ok=True)
    if os.path.lexists(destination):
        raise FileExistsError(f"{destination} already exists")
    directory.rename(destination)


def retire_worktrees(
    source: Path, root: Path, base: str = "origin/main", apply: bool = False,
    fetch: bool = True, keep: tuple[str, ...] = ("main", "release-candidate"),
) -> dict:
    """Report, and with apply retire, the task worktrees under <root>/workspace/worktrees.

    A worktree qualifies when it is on a branch, `git status` lists nothing
    (ignored files such as node_modules move with it) and its branch head is an
    ancestor of base but not a commit of base's first-parent line, which would
    mean a task with no commits of its own yet, such as one another session
    has just created. Retiring renames it into <root>/_TRASH_<YYYYMMDD>, runs
    `git worktree prune` and deletes its branch with `git branch -d`; merged
    local branches no worktree holds are deleted the same way, and a
    registration under the root whose directory is gone is pruned. The source
    and main checkouts, worktrees elsewhere, keep branches, the source's
    current branch, remote branches and stashes are never touched. A failed
    fetch is reported and stops apply.
    """
    source = Path(git(source, "rev-parse", "--show-toplevel")).resolve()
    root = validate_root(source, root)
    worktrees = (root / "workspace" / "worktrees").resolve()
    fetch_error = None
    if fetch:
        try:
            git(source, "fetch", "--prune", "origin")
        except subprocess.CalledProcessError as error:
            fetch_error = _git_error(error)
    try:
        base_commit = git(source, "rev-parse", "--verify", base + "^{commit}")
    except subprocess.CalledProcessError:
        failed = f"; the fetch failed: {fetch_error}" if fetch_error else ""
        raise ValueError(f"The base {base} names no commit{failed}") from None

    def refs(*args: str) -> set[str]:
        return set(git(source, "for-each-ref", "--format=%(refname)", *args).splitlines())

    listing = git(source, "for-each-ref", "--format=%(refname)%00%(objectname)%00%(upstream)", "refs/heads")
    heads = {ref: (commit, upstream) for ref, commit, upstream in (line.split("\0") for line in listing.splitlines())}
    merged, in_head = refs("--merged", base_commit, "refs/heads"), refs("--merged", "HEAD", "refs/heads")
    existing = refs("refs/heads", "refs/remotes")
    # A worktree whose head is a commit of the base's own line has no commits of its own: a task just
    # created from main (or a fast-forwarded branch). Its work has not merged, so it is not retired.
    base_line = set(git(source, "rev-list", "--first-parent", base_commit).splitlines())
    kept = set(keep) | {git(source, "branch", "--show-current")}

    def refusal(ref: str) -> str | None:
        """Why `git branch -d` would refuse a merged branch: it checks the upstream, else the source HEAD."""
        commit, upstream = heads[ref]
        if upstream in existing:
            if _is_ancestor(source, commit, upstream):
                return None
            return f"git branch -d would refuse: its upstream {upstream} does not contain it"
        if ref in in_head:
            return None
        return ("git branch -d would refuse: the source checkout's HEAD does not contain it; "
                "fast-forward the source to the base, then retire again")

    records = _worktree_records(source)
    paths = [Path(record["worktree"]).resolve() for record in records]
    protected = {paths[0]: "main checkout", source: "source checkout"}
    trash = root / f"_TRASH_{date.today():%Y%m%d}"
    entries, planned, stale, outside = [], set(), [], 0
    for record, path in zip(records, paths):
        missing = not path.is_dir() or "prunable" in record
        if worktrees not in path.parents:
            outside += 1
            if missing and "locked" not in record:
                stale.append(str(path))
            continue
        ref, note = record.get("branch"), None
        if "locked" in record:
            state = "locked"
        elif missing:
            state = "missing-directory"
        elif not ref:
            state = "detached"
        elif ref not in merged:
            state = "unmerged"
        else:
            try:
                changes = git(path, "--no-optional-locks", "status", "--porcelain", "--untracked-files=normal")
            except subprocess.CalledProcessError as error:
                changes = note = f"git status failed: {_git_error(error)}"
            state = "local-changes" if changes else "merged-clean"
        entry = {"path": str(path), "branch": ref and ref.removeprefix("refs/heads/"),
                 "head": record.get("HEAD", "")[:12], "state": state, "action": "keep"}
        if path in protected:
            note = protected[path]
        elif state == "missing-directory":
            entry["action"] = "prune"
        elif state == "merged-clean" and entry["branch"] in keep:
            note = "kept branch"
        elif state == "merged-clean" and record.get("HEAD") in base_line:
            note = "no commits of its own yet: a new task or a fast-forwarded branch"
        elif state == "merged-clean" and any(path in other.parents for other in paths):
            note = "contains another worktree"
        elif state == "merged-clean":
            destination, number = trash / path.name, 1
            while os.path.lexists(destination) or destination in planned:
                number += 1
                destination = trash / f"{path.name}-{number}"
            planned.add(destination)
            entry.update(action="retire", trash=str(destination))
        if note:
            entry["note"] = note
        entries.append((entry, ref))

    released = {entry["path"] for entry, _ in entries if entry["action"] != "keep"}
    still_held = {record.get("branch") for record, path in zip(records, paths) if str(path) not in released}
    for entry, ref in entries:
        if entry["action"] == "keep" or not ref:
            continue
        if entry["branch"] in kept:
            reason = "kept branch"
        elif ref in still_held:
            reason = "checked out in another worktree"
        elif ref not in merged:
            reason = "unmerged"
        else:
            reason = refusal(ref)
        entry["branchAction"] = "keep" if reason else "delete"
        if reason:
            entry["branchNote"] = reason
    held = {record.get("branch") for record in records}
    branches = []
    for ref in sorted(merged - held):
        name = ref.removeprefix("refs/heads/")
        if name in kept:
            continue
        reason = refusal(ref)
        branches.append({"branch": name, "head": heads[ref][0][:12], "action": "keep" if reason else "delete",
                         **({"note": reason} if reason else {})})

    listed = [entry for entry, _ in entries]
    summary = dict.fromkeys(RETIRE_STATES, 0)
    for entry in listed:
        summary[entry["state"]] += 1
    summary.update({
        "retire": sum(entry["action"] == "retire" for entry in listed),
        "prune": sum(entry["action"] == "prune" for entry in listed),
        "deleteBranches": sum(entry.get("branchAction") == "delete" for entry in listed)
        + sum(item["action"] == "delete" for item in branches),
        "outsideRoot": outside,
    })
    report = {
        "sourceRoot": str(source), "workspaceRoot": str(root), "worktreesDir": str(worktrees),
        "base": base, "baseCommit": base_commit,
        "fetch": "skipped" if not fetch else "failed" if fetch_error else "ok",
        **({"fetchError": fetch_error} if fetch_error else {}),
        "apply": apply, "worktrees": listed, "branches": branches,
        # The git worktree prune that apply runs also clears these registrations; their directories are gone.
        "staleOutsideRoot": stale, "summary": summary,
    }
    if apply and fetch_error:
        report["stopped"] = "The fetch failed, so nothing was changed; repair origin or pass --no-fetch."
    elif apply:
        _apply_retirement(source, report)
    return report


def _apply_retirement(source: Path, report: dict) -> None:
    """Carry out a planned retirement in order, recording every step's result in the report."""
    counts = dict.fromkeys(("retired", "pruned", "deletedBranches", "failed"), 0)

    def fail(item: dict, error: str, result: str = "result", detail: str = "error") -> None:
        item[result], item[detail] = "failed", error
        counts["failed"] += 1

    def delete(item: dict, result: str = "result", detail: str = "error") -> None:
        try:
            git(source, "branch", "-d", item["branch"])
        except subprocess.CalledProcessError as error:
            fail(item, _git_error(error), result, detail)
        else:
            item[result] = "deleted"
            counts["deletedBranches"] += 1

    entries, pruned = report["worktrees"], False
    for entry in entries:
        if entry["action"] != "retire":
            continue
        try:
            _move_into_trash(Path(entry["path"]), Path(entry["trash"]))
        except OSError as error:
            fail(entry, f"move: {error}")
            continue
        try:
            git(source, "worktree", "prune")
        except subprocess.CalledProcessError as error:
            fail(entry, f"prune: {_git_error(error)}")
            continue
        pruned = True
        entry["result"] = "retired"
        counts["retired"] += 1
        if entry.get("branchAction") == "delete":
            delete(entry, "branchResult", "branchError")
    missing = [entry for entry in entries if entry["action"] == "prune"]
    if missing:
        try:
            if not pruned:
                git(source, "worktree", "prune")
            registered = {Path(record["worktree"]).resolve() for record in _worktree_records(source)}
        except subprocess.CalledProcessError as error:
            for entry in missing:
                fail(entry, f"prune: {_git_error(error)}")
            missing = []
        for entry in missing:
            if Path(entry["path"]) in registered:
                fail(entry, "Git kept the registration")
                continue
            entry["result"] = "pruned"
            counts["pruned"] += 1
            if entry.get("branchAction") == "delete":
                delete(entry, "branchResult", "branchError")
    for item in report["branches"]:
        if item["action"] == "delete":
            delete(item)
    report["summary"].update(counts)


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
    retire = commands.add_parser(
        "retire", help="list merged, clean task worktrees and merged local branches to retire; a dry run unless --apply")
    retire.add_argument("--apply", action="store_true",
                        help="move them into <root>/_TRASH_<date>, prune them and delete their merged branches")
    retire.add_argument("--base", default="origin/main",
                        help="a branch whose head is an ancestor of this ref is merged (default: origin/main)")
    retire.add_argument("--no-fetch", dest="fetch", action="store_false",
                        help="use the local base without running git fetch --prune origin first")
    args = parser.parse_args(argv)
    try:
        source = args.source_root.resolve()
        root = configure_root(source, args.root) if args.command == "configure" else configured_root(source)
        if root is None:
            raise ValueError("Run tools/dev/workspace.py configure --root <external directory> once.")
        if args.command == "retire":
            report = retire_worktrees(source, root, args.base, apply=args.apply, fetch=args.fetch)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if "stopped" in report or report["summary"].get("failed") else 0
        task = task_name(source, getattr(args, "task", None), branch=getattr(args, "branch", None))
        selected = task_paths(root, task)
        if args.command == "create":
            create_worktree(source, selected["worktreeDir"], args.branch, args.base)
        print(json.dumps({
            "sourceRoot": str(source), "workspaceRoot": str(root), "task": task,
            **{key: str(value) for key, value in selected.items()},
        }, ensure_ascii=False, indent=2))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        detail = _git_error(error) if isinstance(error, subprocess.CalledProcessError) else str(error)
        parser.exit(1, f"workspace: {detail}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
