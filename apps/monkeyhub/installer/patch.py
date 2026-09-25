"""File-level developer patches for the existing Windows desktop distribution.

This module only builds, checks and stages immutable version directories. Hub
owns selection, task draining, activation and rollback. Checksums establish
consistency, not publisher identity; no network release is trusted here.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
from typing import BinaryIO
import zipfile
import zlib


PATCH_SCHEMA = "MonkeyHubPatch@1"
PATCH_TRUST = "local-developer-unsigned"
MANIFEST_NAME = "patch-manifest.json"
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
# How long a finished staging copy waits for Windows to release it before the rename.
RENAME_SECONDS = 120.0
REQUIRED_FILES = (
    "source-version.txt", "build-info.json", "MonkeyHub.exe",
    "OPEN_MONKEYHUB.cmd", "_runtime/python/python.exe", "_runtime/desktop-Cargo.lock",
    "apps/monkeyhub/run.py", "apps/monkeyhub/launch-hub.ps1",
    "apps/monkeyhub/web/dist/index.html", "apps/monkeyfab/src/monkeyfab/__main__.py",
    "apps/monkeyfab/pyproject.toml",
)


class PatchError(ValueError):
    """The patch, base installation or requested destination is not usable."""


class PatchCancelled(PatchError):
    """The caller stopped the work; nothing was found wrong with the patch."""


def _proceed(cancelled: Callable[[], bool] | None) -> None:
    # A caller that is shutting down stops between files; the enclosing
    # function then removes only what it created, as for any other refusal.
    if cancelled is not None and cancelled():
        raise PatchCancelled("Stopped before the prepared version was complete; nothing was activated.")


def _path_name(value: object) -> str:
    if not isinstance(value, str) or not value or re.search(r'[\\:*?"<>|\x00-\x1f]', value):
        raise PatchError(f"Unsafe patch path: {value!r}")
    for part in value.split("/"):
        if (part in ("", ".", "..") or part != part.strip() or part.endswith(".")
                or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part, re.I)):
            raise PatchError(f"Unsafe patch path: {value!r}")
    return value


def _cache_file(name: str) -> bool:
    return "__pycache__" in name.casefold().split("/") or name.casefold().endswith(".pyc")


def _check_names(names: list[str]) -> None:
    """Reject aliases on Windows, including inconsistent directory casing."""
    spelling: dict[str, str] = {}
    files: set[str] = set()
    directories: set[str] = set()
    for name in names:
        _path_name(name)
        parts = name.split("/")
        for count in range(1, len(parts) + 1):
            prefix = "/".join(parts[:count])
            key = prefix.casefold()
            if key in spelling and spelling[key] != prefix:
                raise PatchError(f"Case-colliding patch paths: {spelling[key]} / {prefix}")
            if key in files:
                raise PatchError(f"Duplicate or overlapping patch path: {name}")
            spelling[key] = prefix
            if count < len(parts):
                directories.add(key)
        key = name.casefold()
        if key in directories:
            raise PatchError(f"File/directory collision: {name}")
        files.add(key)


def _check_plain(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise PatchError(f"Links and reparse points are not allowed: {path}")
    if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
        raise PatchError(f"Not a regular file or directory: {path}")
    return info


def _plain_path(value: Path, *, directory: bool | None = None) -> Path:
    path = Path(os.path.abspath(value))
    for part in (*reversed(path.parents), path):
        try:
            _check_plain(part)
        except FileNotFoundError:
            pass
    if directory is True and not path.is_dir():
        raise PatchError(f"Directory does not exist: {path}")
    if directory is False and not path.is_file():
        raise PatchError(f"File does not exist: {path}")
    return path


def _digest(source: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    length = 0
    while block := source.read(1024 * 1024):
        length += len(block)
        digest.update(block)
    return length, digest.hexdigest()


def _file_fact(path: Path) -> dict:
    _check_plain(path)
    with path.open("rb") as source:
        size, digest = _digest(source)
    return {"size": size, "sha256": digest}


def _files(root: Path, cancelled: Callable[[], bool] | None = None) -> Iterator[tuple[str, Path, os.stat_result]]:
    """Every distributed file under root; links and reparse points are refused."""
    def inaccessible(error: OSError) -> None:
        raise error
    for directory, children, filenames in os.walk(root, followlinks=False, onerror=inaccessible):
        for name in children:
            _check_plain(Path(directory) / name)
        for name in filenames:
            _proceed(cancelled)
            path = Path(directory) / name
            info = _check_plain(path)
            relative = path.relative_to(root).as_posix()
            if not _cache_file(relative):
                yield relative, path, info


def _inventory(root: Path, cancelled: Callable[[], bool] | None = None) -> dict[str, dict]:
    files = {relative: _file_fact(path) for relative, path, _ in _files(root, cancelled)}
    _check_names(list(files))
    return dict(sorted(files.items()))


def _layout(root: Path) -> dict[str, int]:
    sizes = {relative: info.st_size for relative, _, info in _files(root)}
    _check_names(list(sizes))
    return dict(sorted(sizes.items()))


def _json(data: bytes) -> dict:
    def pairs(items: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise PatchError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        document = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as error:
        raise PatchError(f"Invalid patch JSON: {error}") from error
    if not isinstance(document, dict):
        raise PatchError("Expected a JSON object.")
    return document


def _identity(version_data: bytes, build_data: bytes, files: dict[str, dict]) -> str:
    try:
        commit = version_data.decode("utf-8").strip()
    except UnicodeError as error:
        raise PatchError("Invalid source-version.txt.") from error
    build = _json(build_data)
    desktop = build.get("desktop")
    if (not re.fullmatch("[0-9a-f]{40}", commit) or build.get("sourceCommit") != commit
            or build.get("target") != "windows-x64" or not isinstance(desktop, dict)
            or desktop.get("sourceCommit") != commit):
        raise PatchError("Patch requires an exact-commit Windows desktop bundle.")
    missing = set(REQUIRED_FILES) - files.keys()
    if missing:
        raise PatchError("Incomplete desktop bundle: " + ", ".join(sorted(missing)))
    if desktop.get("executableSha256") != files["MonkeyHub.exe"]["sha256"]:
        raise PatchError("Desktop executable does not match build-info.json.")
    return commit


def _root_identity(root: Path, files: dict[str, dict]) -> str:
    for name in ("source-version.txt", "build-info.json"):
        if name not in files or files[name]["size"] > MAX_MANIFEST_BYTES:
            raise PatchError(f"Missing or oversized bundle metadata: {name}")
    try:
        return _identity((root / "source-version.txt").read_bytes(),
                         (root / "build-info.json").read_bytes(), files)
    except OSError as error:
        raise PatchError(f"Incomplete bundle: {error}") from error


def _table(value: object) -> dict[str, dict]:
    if not isinstance(value, dict) or not value:
        raise PatchError("Patch file table must be a non-empty object.")
    _check_names(list(value))
    for name, fact in value.items():
        if _cache_file(name):
            raise PatchError("Python bytecode caches are not distributed in patches.")
        if (not isinstance(fact, dict) or set(fact) != {"size", "sha256"}
                or type(fact["size"]) is not int or fact["size"] < 0
                or not isinstance(fact["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", fact["sha256"])):
            raise PatchError(f"Invalid patch file fact: {name}")
    return value


def _manifest(archive: zipfile.ZipFile) -> dict:
    entries = archive.infolist()
    _check_names([entry.filename for entry in entries])
    for entry in entries:
        mode = entry.external_attr >> 16
        if (entry.is_dir() or stat.S_ISLNK(mode) or entry.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or entry.flag_bits & 1
                or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise PatchError(f"Unsupported ZIP entry: {entry.filename}")
    try:
        entry = archive.getinfo(MANIFEST_NAME)
    except KeyError as error:
        raise PatchError("Patch manifest is missing.") from error
    if entry.file_size > MAX_MANIFEST_BYTES:
        raise PatchError("Patch manifest is too large.")
    document = _json(archive.read(entry))
    if set(document) != {"schema", "trust", "baseCommit", "targetCommit", "targetVersion", "baseFiles", "targetFiles", "changed", "removed"}:
        raise PatchError("Invalid patch manifest fields.")
    if document["schema"] != PATCH_SCHEMA or document["trust"] != PATCH_TRUST:
        raise PatchError("Unsupported patch format or trust mode.")
    for key in ("baseCommit", "targetCommit"):
        if not isinstance(document[key], str) or not re.fullmatch("[0-9a-f]{40}", document[key]):
            raise PatchError(f"Invalid {key}.")
    if (document["targetVersion"] != document["targetCommit"][:12] + "-desktop"
            or document["baseCommit"][:12] == document["targetCommit"][:12]):
        raise PatchError("Patch target must name a different desktop version.")
    base, target = _table(document["baseFiles"]), _table(document["targetFiles"])
    changed = sorted(name for name, fact in target.items() if base.get(name) != fact)
    removed = sorted(base.keys() - target.keys())
    if document["changed"] != changed or document["removed"] != removed:
        raise PatchError("Patch changes do not match the closed file tables.")
    expected = {MANIFEST_NAME, *("payload/" + name for name in changed)}
    if {entry.filename for entry in entries} != expected:
        raise PatchError("Patch has missing or unexpected payload files.")
    for name in changed:
        if archive.getinfo("payload/" + name).file_size != target[name]["size"]:
            raise PatchError(f"Wrong payload size: {name}")
    return document


def _summary(document: dict) -> dict:
    target = document["targetFiles"]
    return {
        "schema": PATCH_SCHEMA, "trust": PATCH_TRUST,
        "baseCommit": document["baseCommit"], "targetCommit": document["targetCommit"],
        "targetVersion": document["targetVersion"], "desktop": True,
        "changedFiles": len(document["changed"]), "removedFiles": len(document["removed"]),
        "reusedFiles": len(target) - len(document["changed"]),
        "targetBytes": sum(fact["size"] for fact in target.values()),
        "payloadBytes": sum(target[name]["size"] for name in document["changed"]),
        # A release manifest binds this same digest, so a caller can tie the
        # version this patch reconstructs to that release before staging it.
        "targetBuildInfoSha256": target["build-info.json"]["sha256"] if "build-info.json" in target else None,
    }


def _verify(archive: zipfile.ZipFile, document: dict, base: Path,
            cancelled: Callable[[], bool] | None = None) -> None:
    actual = _inventory(base, cancelled)
    if actual != document["baseFiles"]:
        raise PatchError("Base installation files differ from the patch base; use a matching complete version.")
    if _root_identity(base, actual) != document["baseCommit"]:
        raise PatchError("Base source commit does not match this patch.")
    for name in document["changed"]:
        _proceed(cancelled)
        with archive.open("payload/" + name) as source:
            size, digest = _digest(source)
        if {"size": size, "sha256": digest} != document["targetFiles"][name]:
            raise PatchError(f"Corrupt payload: {name}")
    def target_bytes(name: str) -> bytes:
        if name not in document["targetFiles"] or document["targetFiles"][name]["size"] > MAX_MANIFEST_BYTES:
            raise PatchError(f"Missing or oversized target metadata: {name}")
        return archive.read("payload/" + name) if name in document["changed"] else (base / name).read_bytes()
    if _identity(target_bytes("source-version.txt"), target_bytes("build-info.json"), document["targetFiles"]) != document["targetCommit"]:
        raise PatchError("Target build metadata does not match this patch.")


def describe_patch(path: Path) -> dict:
    """Read a container-checked summary; this does NOT verify the base or bytes."""
    try:
        with zipfile.ZipFile(_plain_path(path, directory=False)) as archive:
            return _summary(_manifest(archive))
    except (OSError, zipfile.BadZipFile, zlib.error, RuntimeError) as error:
        raise PatchError(f"Cannot read patch: {error}") from error


def inspect_patch(path: Path, base_root: Path) -> dict:
    """Verify the full base, closed archive and all target payload bytes."""
    try:
        base = _plain_path(base_root, directory=True)
        with zipfile.ZipFile(_plain_path(path, directory=False)) as archive:
            document = _manifest(archive)
            _verify(archive, document, base)
            return _summary(document)
    except (OSError, zipfile.BadZipFile, zlib.error, RuntimeError) as error:
        raise PatchError(f"Cannot verify patch: {error}") from error


def _verify_target_files(document: dict, target_root: Path,
                         cancelled: Callable[[], bool] | None = None) -> None:
    target = _plain_path(target_root, directory=True)
    actual = _inventory(target, cancelled)
    if actual != document["targetFiles"]:
        raise PatchError("Prepared version files changed; refusing reuse or activation.")
    if _root_identity(target, actual) != document["targetCommit"]:
        raise PatchError("Prepared source commit does not match this patch.")


def verify_target(path: Path, target_root: Path, *, cancelled: Callable[[], bool] | None = None) -> dict:
    """Recheck a staged directory immediately before activation or completion."""
    try:
        with zipfile.ZipFile(_plain_path(path, directory=False)) as archive:
            document = _manifest(archive)
            _verify_target_files(document, target_root, cancelled)
            return _summary(document)
    except (OSError, zipfile.BadZipFile, zlib.error, RuntimeError) as error:
        raise PatchError(f"Cannot verify prepared version: {error}") from error


def verify_target_layout(path: Path, target_root: Path, *, exact: tuple[str, ...] = ()) -> dict:
    """Quickly recheck a staged directory before its entry is switched to it.

    Staging already hashed every file. This rechecks the closed file set and
    every size, and the exact bytes of the identity files plus any file named
    in ``exact`` (a script about to be executed, for example). It reads a few
    files instead of the whole tree, so it does not replace verify_target.
    """
    try:
        with zipfile.ZipFile(_plain_path(path, directory=False)) as archive:
            document = _manifest(archive)
        target = _plain_path(target_root, directory=True)
        expected = document["targetFiles"]
        if _layout(target) != {name: fact["size"] for name, fact in expected.items()}:
            raise PatchError("Prepared version files changed; refusing activation.")
        for name in ("source-version.txt", "build-info.json", "MonkeyHub.exe", *exact):
            if name not in expected or _file_fact(target / name) != expected[name]:
                raise PatchError(f"Prepared version file changed or is not part of it: {name}")
        if _root_identity(target, expected) != document["targetCommit"]:
            raise PatchError("Prepared source commit does not match this patch.")
        return _summary(document)
    except (OSError, zipfile.BadZipFile, zlib.error, RuntimeError) as error:
        raise PatchError(f"Cannot check prepared version: {error}") from error


def stage_patch(path: Path, base_root: Path, destination_parent: Path, *,
                cancelled: Callable[[], bool] | None = None) -> Path:
    """Stage or reverify an identical existing target; never overwrite or activate.

    ``cancelled`` is polled between files; answering True stops with a refusal
    and removes only the temporary directory this call created.
    """
    temporary: Path | None = None
    try:
        base = _plain_path(base_root, directory=True)
        parent = _plain_path(destination_parent, directory=True)
        if parent == base or parent.is_relative_to(base):
            raise PatchError("Patch destination must be outside the current version.")
        with zipfile.ZipFile(_plain_path(path, directory=False)) as archive:
            document = _manifest(archive)
            final = _plain_path(parent / document["targetVersion"])
            _verify(archive, document, base, cancelled)
            if final.exists() or final.is_symlink():
                # Staging may have succeeded before Hub could save its update
                # transaction. Re-selecting that patch can recover the version,
                # but only after the same full verification used at activation.
                try:
                    _verify_target_files(document, final, cancelled)
                except PatchCancelled:
                    raise
                except PatchError as error:
                    raise PatchError(f"Existing target does not match this patch; left unchanged: {error}") from error
                return final
            if shutil.disk_usage(parent).free < sum(fact["size"] for fact in document["targetFiles"].values()):
                raise PatchError("Not enough disk space to prepare the complete new version.")
            temporary = Path(tempfile.mkdtemp(prefix=".monkeyhub-patch-", dir=parent))
            changed = set(document["changed"])
            for name in document["targetFiles"]:
                _proceed(cancelled)
                destination = temporary / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if name in changed:
                    with archive.open("payload/" + name) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output)
                else:
                    _plain_path(base / name, directory=False)
                    shutil.copyfile(base / name, destination)
            target_files = _inventory(temporary, cancelled)
            if target_files != document["targetFiles"] or _root_identity(temporary, target_files) != document["targetCommit"]:
                raise PatchError("Reconstructed version did not pass full file verification.")
            _plain_path(final)
            # Windows scanners and indexers briefly hold newly written files
            # open, and a directory cannot be renamed until they let go. Retry
            # only that refusal; the checked target still must not exist.
            deadline, delay = time.monotonic() + RENAME_SECONDS, 0.25
            while True:
                if final.exists() or final.is_symlink():
                    raise PatchError(f"Target appeared during staging; refusing to overwrite: {final}")
                try:
                    temporary.rename(final)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    _proceed(cancelled)
                    time.sleep(delay)
                    delay = min(delay * 2, 5.0)
            temporary = None
            return final
    except (OSError, zipfile.BadZipFile, zlib.error, RuntimeError) as error:
        raise PatchError(f"Cannot stage patch: {error}") from error
    finally:
        if temporary is not None:
            # Only the directory this call created under the checked parent.
            checked = _plain_path(temporary, directory=True)
            if checked.parent != parent:
                raise PatchError("Refusing to clean a staging directory outside the version parent.")
            shutil.rmtree(checked)


def create_patch(base_root: Path, target_root: Path, output_zip: Path) -> dict:
    """Build a developer delta from two explicit, complete desktop bundles."""
    temporary: Path | None = None
    try:
        base, target = (_plain_path(root, directory=True) for root in (base_root, target_root))
        output = _plain_path(output_zip)
        if any(output == root or output.is_relative_to(root) for root in (base, target)):
            raise PatchError("Patch output must be outside both input versions.")
        if output.exists():
            raise PatchError(f"Patch output already exists: {output}")
        base_files, target_files = _inventory(base), _inventory(target)
        base_commit, target_commit = _root_identity(base, base_files), _root_identity(target, target_files)
        if base_commit[:12] == target_commit[:12]:
            raise PatchError("Patch target must name a different source version.")
        document = {
            "schema": PATCH_SCHEMA, "trust": PATCH_TRUST,
            "baseCommit": base_commit, "targetCommit": target_commit,
            "targetVersion": target_commit[:12] + "-desktop",
            "baseFiles": base_files, "targetFiles": target_files,
            "changed": sorted(name for name, fact in target_files.items() if base_files.get(name) != fact),
            "removed": sorted(base_files.keys() - target_files.keys()),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".monkeyhub-patch-", suffix=".zip", dir=output.parent, delete=False) as opened:
            temporary = Path(opened.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(document, ensure_ascii=False, separators=(",", ":")))
            for name in document["changed"]:
                archive.write(target / name, "payload/" + name)
        summary = inspect_patch(temporary, base)
        if output.exists():
            raise PatchError(f"Patch output appeared during packaging: {output}")
        temporary.rename(output)
        temporary = None
        return summary
    except (OSError, zipfile.BadZipFile, zlib.error, RuntimeError) as error:
        raise PatchError(f"Cannot create patch: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
