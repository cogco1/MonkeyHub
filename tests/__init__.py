"""ArchFlow V4 contract tests."""

from pathlib import Path

from tools import source_roots

# Importing a test as tests.<module> runs this first, so the suite runs on this checkout's code.
source_roots.put_first(Path(__file__).resolve().parents[1])
