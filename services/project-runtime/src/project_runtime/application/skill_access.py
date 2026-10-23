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
  reading, nor the other way round. A folder named with a ``..`` step is never
  within anything: ``D:\\proj\\out\\..\\..\\Windows`` reads below ``out``
  and is not. The runner holds a script to its folders after following links
  (``realpath``), so a caller on the machine that runs the skill passes the
  same ``resolve`` and a link out of an allowed folder is not within it either.

The access is the checked form ``skills`` keeps (``network`` and the five
lists); a missing declaration needs nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import ntpath
from pathlib import PurePosixPath, PureWindowsPath
import posixpath
import re
from typing import Any

ACCESS_LISTS = ("programs", "ssh", "read", "write", "packages")
FOLDERS = ("read", "write")
ANYWHERE = "*"

_WINDOWS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_WORDS = {"programs": "run", "ssh": "connect to", "read": "read", "write": "write", "packages": "install"}
_WORDS_ZH = {"programs": "运行", "ssh": "连接", "read": "读取", "write": "写入", "packages": "安装"}


Resolve = Callable[[str], str]


def access_added(requested: Mapping[str, Any] | None, granted: Mapping[str, Any] | None, *,
                 resolve: Resolve | None) -> dict[str, Any]:
    """What ``requested`` asks for beyond ``granted``: ``{}`` when it stays within it.

    ``resolve`` turns a declared folder into the folder the runner will hold
    the script to (``os.path.realpath`` where the skill runs). It is always
    named: None compares the folders as written, ``.`` steps removed, which
    only describes a declaration; deciding goes through ``access_within``.
    """

    requested, granted = requested or {}, granted or {}
    added: dict[str, Any] = {}
    if requested.get("network") and not granted.get("network"):
        added["network"] = True
    for key in ACCESS_LISTS:
        allowed = list(granted.get(key) or ())
        new = [item for item in requested.get(key) or () if not _covered(key, item, allowed, resolve)]
        if new:
            added[key] = new
    return added


def access_within(requested: Mapping[str, Any] | None, granted: Mapping[str, Any] | None, *,
                  resolve: Resolve) -> bool:
    """Whether a version declaring ``requested`` may run on a grant that allowed ``granted``, without asking.

    Deciding not to ask follows links as the runner will: ``resolve`` is
    required, so a link out of an allowed folder cannot be let through by
    a caller that forgot it.
    """

    if not callable(resolve):
        raise TypeError("access_within decides whether to ask again, so it resolves folders as the runner does")
    return not access_added(requested, granted, resolve=resolve)


def describe_added(added: Mapping[str, Any]) -> str:
    """What is new, in one line for the card that asks the person: "use the network; run git, rhino"."""

    parts = ["use the network"] if added.get("network") else []
    parts += [f"{_WORDS[key]} {', '.join(added[key])}" for key in ACCESS_LISTS if added.get(key)]
    return "; ".join(parts)


def describe_added_zh(added: Mapping[str, Any]) -> str:
    """The same line in Chinese: 「联网；运行 git、rhino」."""

    parts = ["联网"] if added.get("network") else []
    parts += [f"{_WORDS_ZH[key]} {'、'.join(added[key])}" for key in ACCESS_LISTS if added.get(key)]
    return "；".join(parts)


def _covered(key: str, item: str, allowed: list[str], resolve: Resolve | None) -> bool:
    if key not in FOLDERS:
        return item in allowed
    if ANYWHERE in allowed:
        return True
    folder = _folder(item, resolve)
    if folder is None:
        return False
    return any(base is not None and type(folder) is type(base) and (folder == base or base in folder.parents)
               for base in (_folder(row, resolve) for row in allowed if row != ANYWHERE))


def _folder(text: str, resolve: Resolve | None) -> PurePosixPath | PureWindowsPath | None:
    """The folder a declaration names, or None when no folder can stand for it (``*``, a ``..`` step)."""

    windows = bool(_WINDOWS.match(text))
    if text == ANYWHERE or ".." in re.split(r"[\\/]" if windows else "/", text):
        return None
    if resolve is not None:
        text = resolve(text)
        windows = bool(_WINDOWS.match(text))
    # A drive or UNC path is compared as Windows compares it, without regard to case or slash direction.
    return PureWindowsPath(ntpath.normpath(text)) if windows else PurePosixPath(posixpath.normpath(text))


__all__ = ["ACCESS_LISTS", "access_added", "access_within", "describe_added", "describe_added_zh"]
