"""Compatibility facade for canonical site-context compilation.

New code imports :mod:`archive.archflow.compilers.site`.  This module preserves the
legacy runtime path without defining parallel contracts or behavior.
"""

from archive.archflow.compilers.site import (
    CompiledSiteContext,
    SiteCompilationError,
    SiteCompilationReceipt,
    compile_site_context,
)

__all__ = [
    "SiteCompilationError",
    "SiteCompilationReceipt",
    "CompiledSiteContext",
    "compile_site_context",
]
