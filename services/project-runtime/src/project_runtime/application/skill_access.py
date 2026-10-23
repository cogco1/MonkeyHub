"""Whether a skill's declared access stays within what a person allowed, and what it adds when it does not.

A grant of a runnable skill version binds its code hash, so every code update
asks the person again although nothing they decided has changed. What they
decided is the access: which programs it may call, whether it may use the
network, which ssh hosts, which folders it reads and writes, which Python
packages it installs. Read against a grant by scope, a later version whose
declared access lies within the allowed access may run on that grant, and one
that asks for more is asked about only what is new.

Per key:

- ``network``: asking for it needs a grant that allowed it;
- ``programs``, ``ssh``, ``packages``: each item as written (a package with
  another version specifier is another package);
- ``read``, ``write``: each folder lies within an allowed folder, the folder
  itself or below it, or the grant allowed ``*``. Writing does not imply
  reading, nor the other way round.

The access is the checked form ``skills`` keeps (``network`` and the five
lists); a missing declaration needs nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any

ACCESS_LISTS = ("programs", "ssh", "read", "write", "packages")
FOLDERS = ("read", "write")
ANYWHERE = "*"

_WINDOWS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_WORDS = {"programs": "运行", "ssh": "连接", "read": "读取", "write": "写入", "packages": "安装"}


def access_added(requested: Mapping[str, Any] | None, granted: Mapping[str, Any] | None) -> dict[str, Any]:
    """What ``requested`` asks for beyond ``granted``: ``{}`` when it stays within it."""

    requested, granted = requested or {}, granted or {}
    added: dict[str, Any] = {}
    if requested.get("network") and not granted.get("network"):
        added["network"] = True
    for key in ACCESS_LISTS:
        allowed = list(granted.get(key) or ())
        new = [item for item in requested.get(key) or () if not _covered(key, item, allowed)]
        if new:
            added[key] = new
    return added


def access_within(requested: Mapping[str, Any] | None, granted: Mapping[str, Any] | None) -> bool:
    """Whether a version declaring ``requested`` may run on a grant that allowed ``granted``."""

    return not access_added(requested, granted)


def describe_added(added: Mapping[str, Any]) -> str:
    """What is new, in one line for the card that asks the person: 「联网；运行 git、rhino」."""

    parts = ["联网"] if added.get("network") else []
    parts += [f"{_WORDS[key]} {'、'.join(added[key])}" for key in ACCESS_LISTS if added.get(key)]
    return "；".join(parts)


def _covered(key: str, item: str, allowed: list[str]) -> bool:
    if key not in FOLDERS:
        return item in allowed
    if ANYWHERE in allowed:
        return True
    if item == ANYWHERE:
        return False
    folder = _folder(item)
    return any(type(folder) is type(base) and (folder == base or base in folder.parents)
               for base in map(_folder, (row for row in allowed if row != ANYWHERE)))


def _folder(text: str) -> PurePosixPath | PureWindowsPath:
    # A drive or UNC path is compared as Windows compares it, without regard to case or slash direction.
    return PureWindowsPath(text) if _WINDOWS.match(text) else PurePosixPath(text)


__all__ = ["ACCESS_LISTS", "access_added", "access_within", "describe_added"]
