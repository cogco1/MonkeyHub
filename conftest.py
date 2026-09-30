"""pytest runs on this checkout's code: its Python source roots go first (tools/dev/source_roots.py)."""

from pathlib import Path

from tools.dev import source_roots

source_roots.put_first(Path(__file__).resolve().parent)
