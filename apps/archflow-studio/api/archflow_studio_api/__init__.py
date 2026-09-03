"""The ArchFlow Studio API: a product shell in front of the archflow kernel.

The package sits outside ``archflow`` on purpose. It validates requests, shapes
transport payloads and manages task lifecycle; design state, dependencies,
validation, geometry and commit are calls *into* the kernel, never a second
implementation beside it.

Importing this package puts the repository root on ``sys.path`` so ``import
archflow`` resolves no matter which directory the service was started from.
"""

from __future__ import annotations

from pathlib import Path
import sys

# __init__.py -> archflow_studio_api -> api -> archflow-studio -> apps -> root
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

if str(REPOSITORY_ROOT) not in sys.path:
    # Index 0, not ``append``: this repository's ``archflow`` is the one this
    # service answers for, and an ``archflow`` installed into the environment
    # would otherwise win the import and be answering with another checkout's
    # kernel — silently, and about somebody's building.
    sys.path.insert(0, str(REPOSITORY_ROOT))

__all__ = ["REPOSITORY_ROOT"]
