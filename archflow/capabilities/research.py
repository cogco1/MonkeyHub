"""Compatibility facade for decision-scoped research query contracts."""

from archflow.research.query import (
    PrecedentQuery,
    ResearchError,
    ResearchQuery,
    decode_research_json,
    extract_windows,
    parse_research_output,
    research_prompt,
)

__all__ = [
    "PrecedentQuery",
    "ResearchError",
    "ResearchQuery",
    "decode_research_json",
    "extract_windows",
    "parse_research_output",
    "research_prompt",
]
