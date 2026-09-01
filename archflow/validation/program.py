"""Compatibility facade for canonical voxel-program compilation.

New code imports :mod:`archflow.compilers.voxel_program`.  This module
preserves the legacy path without defining parallel contracts or behavior.
"""

from archflow.compilers.voxel_program import compile_building_program

__all__ = ["compile_building_program"]
