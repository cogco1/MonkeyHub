"""Put this checkout's Python source roots first, so the lab runs the code it was checked out with."""

from __future__ import annotations

import sys
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[2]
if str(CHECKOUT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT))

from tools.dev import source_roots  # noqa: E402

source_roots.put_first(CHECKOUT)
