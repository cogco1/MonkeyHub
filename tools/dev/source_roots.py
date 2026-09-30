"""Put this checkout's Python source roots in front of ``sys.path``.

``python_source_roots`` in governance/architecture_policy.json is the one list
of the directories import names begin at: the repository root, the service and
application roots, and each ``packages/<name>/src``. A checkout reads it
relative to itself, so each of many worktrees imports its own code; an editable
install into a shared interpreter would make every worktree import the one
checkout it was installed from.

Tools, the root test suite (``tests/__init__.py``) and pytest (the root
``conftest.py``) call ``put_first`` with their own checkout. The production
entry points read the same list themselves, because they run before anything
in the checkout is importable. An installed bundle ships no policy: its
python313._pth lists the roots, and this does nothing there.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

POLICY = Path("governance") / "architecture_policy.json"


def roots(checkout: Path) -> list[str]:
    """The checkout's source roots in policy order; none without a policy."""

    policy = checkout / POLICY
    if not policy.is_file():
        return []
    listed = json.loads(policy.read_text(encoding="utf-8"))["python_source_roots"]
    return [str(checkout / root) for root in listed]


def put_first(checkout: Path) -> None:
    """Put each root that ``sys.path`` lacks in front of it, in policy order.

    A root already on the path keeps the place its caller gave it. A missing
    one goes before everything else, so neither an installed copy nor another
    checkout answers in its name.
    """

    sys.path[:0] = [root for root in roots(checkout) if root not in sys.path]
