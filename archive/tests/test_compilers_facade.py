"""The archived compilers facade re-exports the spine's compiler itself.

``archive/archflow/compilers/__init__.py`` is the lane's stable public API:
it bundles the archived program, resources, site and voxel compilers with
the spine's geometry compiler. This asserts that the geometry half is the
same objects, not a copy — the archive imports the spine and never forks
it. It lived in ``test_geometry_compiler.py`` until that module came back
to the spine suite, which may not import the archive at all (ADR-001).
"""

from __future__ import annotations

import unittest

import archive.archflow.compilers as compiler_api
import archflow.compilers.geometry as canonical_geometry_compiler


class CompilersFacadeTests(unittest.TestCase):
    def test_the_lane_facade_re_exports_the_spine_compiler_itself(self) -> None:
        for symbol in canonical_geometry_compiler.__all__:
            self.assertIs(
                getattr(canonical_geometry_compiler, symbol),
                getattr(compiler_api, symbol),
                symbol,
            )


if __name__ == "__main__":
    unittest.main()
