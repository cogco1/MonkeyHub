"""The Project Runtime: the project-scoped backend MonkeyHub starts once per open project.

Historically the ArchFlow Studio API; since #491 the package is ``project_runtime`` in
services/project-runtime (contract: docs/architecture/project-runtime.md). The package sits outside
``archflow`` on purpose. It validates requests, shapes transport payloads and manages task
lifecycle; design state, dependencies, validation, geometry and commit are calls *into* the
kernel, never a second implementation beside it.

Importing this package puts this checkout's Python source roots on ``sys.path`` so
``archflow``, ``monkeydiagram`` and the other packages resolve no matter which directory the
service was started from.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

# __init__.py -> project_runtime -> src -> project-runtime -> services -> root
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

# The architecture policy lists where import names begin (tools/source_roots.py). A root
# the path lacks goes in front, not at the end: this checkout's ``archflow`` is the one
# this service answers for, and an ``archflow`` installed into the environment would
# otherwise win the import and be answering with another checkout's kernel — silently,
# and about somebody's building. An installed bundle ships no policy: its python313._pth
# lists the same roots.
_POLICY = REPOSITORY_ROOT / "governance" / "architecture_policy.json"
if _POLICY.is_file():
    _ROOTS = [str(REPOSITORY_ROOT / root)
              for root in json.loads(_POLICY.read_text(encoding="utf-8"))["python_source_roots"]]
    sys.path[:0] = [root for root in _ROOTS if root not in sys.path]

__all__ = ["REPOSITORY_ROOT"]
