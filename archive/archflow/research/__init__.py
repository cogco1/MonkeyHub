"""Stable research query and authority-gated adoption contracts."""

from archive.archflow.research.adoption import (
    PrecedentAdoption,
    PrecedentError,
    PrecedentFact,
    compile_precedent_constraints,
)
from archive.archflow.research.query import (
    PrecedentQuery,
    ResearchError,
    ResearchQuery,
    decode_research_json,
    extract_windows,
    parse_research_output,
    research_prompt,
)

__all__ = [
    "PrecedentAdoption",
    "PrecedentError",
    "PrecedentFact",
    "PrecedentQuery",
    "ResearchError",
    "ResearchQuery",
    "compile_precedent_constraints",
    "decode_research_json",
    "extract_windows",
    "parse_research_output",
    "research_prompt",
]
