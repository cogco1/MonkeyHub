"""The backends MonkeyControl reaches a desktop through.

``windows-uia`` is the semantic one and the only one that resolves an element
from what it is; ``visual-fallback`` exists so a caller who has no property to
name can still act, at the cost of saying plainly that it did. The presentation
provider draws and captures, and resolves nothing at all.
"""

from __future__ import annotations

from .base import FocusError, Provider, ResolutionError
from .presentation import HIGHLIGHT_COLOR, PresentationProvider
from .uia import UiaProvider
from .visual import VisualFallbackProvider

__all__ = [
    "HIGHLIGHT_COLOR",
    "FocusError",
    "PresentationProvider",
    "Provider",
    "ResolutionError",
    "UiaProvider",
    "VisualFallbackProvider",
]
