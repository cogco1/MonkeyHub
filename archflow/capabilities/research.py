"""Decision-scoped precedent research.

A ``PrecedentQuery`` names the exact design decisions it calibrates. The
research invocation never sees raw pages: it receives bounded keyword
windows cut from retained snapshots, and its output is constrained to
quoted fact candidates. Every candidate quote is machine-located verbatim
inside the full snapshot text — the harness computes the authoritative
character span itself, so the model's offset arithmetic is advisory and a
quote that cannot be found is a typed rejection: the model cannot
paraphrase a source into existence. Candidates carry no authority; only a
typed adoption promotes them, and each adopted fact retains the decision
refs it calibrates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from archflow.capabilities.precedent import PrecedentFact
from archflow.state.build_policy import (
    ConstructabilityTopic,
    PolicyConstraintStrength,
)
from archflow.state.geometry_program import digest_value

_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{0,80}$")
_MAX_WINDOWS = 24
_WINDOW_CHARS = 700


class ResearchError(ValueError):
    """A research query, window set, or candidate output is invalid."""


@dataclass(frozen=True, slots=True)
class PrecedentQuery:
    """One research question scoped to named design decisions."""

    query_id: str
    question: str
    decision_refs: tuple[str, ...]
    search_terms: tuple[str, ...]
    jurisdiction: str | None
    domain_allowlist: tuple[str, ...]

    SCHEMA = "PrecedentQuery@1"

    def __post_init__(self) -> None:
        if not _ID.match(self.query_id):
            raise ResearchError("query_id must be a kebab identifier")
        if not isinstance(self.question, str) or not self.question.strip():
            raise ResearchError("question must be non-empty text")
        if not self.decision_refs or not isinstance(self.decision_refs, tuple):
            raise ResearchError(
                "a precedent query must name the decisions it calibrates"
            )
        if tuple(sorted(set(self.decision_refs))) != self.decision_refs:
            raise ResearchError("decision refs must be sorted and unique")
        if not self.search_terms or not isinstance(self.search_terms, tuple):
            raise ResearchError("search_terms must be a non-empty tuple")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "query_id": self.query_id,
            "question": self.question,
            "decision_refs": list(self.decision_refs),
            "search_terms": list(self.search_terms),
            "jurisdiction": self.jurisdiction,
            "domain_allowlist": list(self.domain_allowlist),
            "adoption_authority": False,
            "canonical_write_authority": False,
        }

    @property
    def query_digest(self) -> str:
        return digest_value(self.to_dict())


def extract_windows(
    snapshot_text: str,
    search_terms: Sequence[str],
    *,
    max_windows: int = _MAX_WINDOWS,
    window_chars: int = _WINDOW_CHARS,
) -> tuple[dict[str, object], ...]:
    """Bounded keyword windows the research invocation is allowed to read."""

    if not isinstance(snapshot_text, str) or not snapshot_text:
        raise ResearchError("snapshot text must be non-empty")
    spans: list[tuple[int, int]] = []
    lowered = snapshot_text.lower()
    for term in search_terms:
        needle = term.lower()
        start = 0
        while True:
            hit = lowered.find(needle, start)
            if hit < 0:
                break
            begin = max(0, hit - window_chars // 2)
            end = min(len(snapshot_text), hit + len(needle) + window_chars // 2)
            spans.append((begin, end))
            start = hit + len(needle)
    spans.sort()
    merged: list[list[int]] = []
    for begin, end in spans:
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])
    windows = tuple(
        {
            "window_start": begin,
            "window_end": end,
            "text": snapshot_text[begin:end],
        }
        for begin, end in merged[:max_windows]
    )
    if not windows:
        raise ResearchError("no snapshot window matches the search terms")
    return windows


def research_prompt(
    query: PrecedentQuery,
    *,
    snapshot_ref: str,
    snapshot_text_sha256: str,
    windows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """The bounded research invocation payload."""

    return {
        "schema": "PrecedentResearchPrompt@1",
        "query": query.to_dict(),
        "snapshot_ref": snapshot_ref,
        "snapshot_text_sha256": snapshot_text_sha256,
        "windows": [dict(window) for window in windows],
        "output_contract": {
            "schema": "PrecedentResearchOutput@1",
            "required_fields": ["schema", "query_id", "candidates"],
            "candidate_fields": [
                "fact_id",
                "statement",
                "quote",
                "quote_start",
                "quote_end",
                "decision_refs",
                "topic",
                "strength",
            ],
            "topic_values": [
                topic.value for topic in ConstructabilityTopic
            ],
            "strength_values": [
                strength.value for strength in PolicyConstraintStrength
            ],
            "rules": [
                "every quote must be copied verbatim from a supplied window; "
                "the harness locates it in the snapshot and rejects "
                "paraphrase",
                "quote_start and quote_end are best-effort absolute offsets "
                "(window_start + offset inside the window); the harness "
                "recomputes them authoritatively",
                "decision_refs must be a NON-EMPTY subset of the query "
                "decision_refs, copied verbatim from the query",
                "topic must be one of topic_values; strength must be one of "
                "strength_values",
                "candidates carry no authority and adopt nothing themselves",
            ],
        },
        "authority": {
            "adoption_authority": False,
            "design_authority": False,
            "canonical_write_authority": False,
        },
    }


def parse_research_output(
    output: Mapping[str, object],
    *,
    query: PrecedentQuery,
    snapshot_ref: str,
    snapshot_text: str,
    snapshot_text_sha256: str,
    annotator: str,
) -> tuple[tuple[PrecedentFact, ...], tuple[dict[str, str], ...]]:
    """Validate candidates fail-closed; return (facts, typed rejections)."""

    if not isinstance(output, Mapping):
        raise ResearchError("research output must be a mapping")
    if output.get("schema") != "PrecedentResearchOutput@1":
        raise ResearchError("research output schema drifted")
    if output.get("query_id") != query.query_id:
        raise ResearchError("research output does not answer this query")
    candidates = output.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ResearchError("research output carries no candidates")
    facts: list[PrecedentFact] = []
    rejections: list[dict[str, str]] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            rejections.append(
                {"fact_id": "?", "code": "candidate_not_a_mapping"}
            )
            continue
        fact_id = str(item.get("fact_id", "?"))
        refs = tuple(sorted(set(item.get("decision_refs", ()))))
        if not refs or not set(refs) <= set(query.decision_refs):
            rejections.append(
                {
                    "fact_id": fact_id,
                    "code": "decision_refs_outside_query",
                    "detail": (
                        "decision refs must be a non-empty subset of the "
                        "query decision refs"
                    ),
                }
            )
            continue
        quote = str(item.get("quote", ""))
        claimed_start = item.get("quote_start")
        start = -1
        if isinstance(claimed_start, int) and 0 <= claimed_start:
            if snapshot_text[
                claimed_start : claimed_start + len(quote)
            ] == quote:
                start = claimed_start
        if start < 0 and quote:
            start = snapshot_text.find(quote)
        if start < 0 or not quote:
            rejections.append(
                {
                    "fact_id": fact_id,
                    "code": "quote_not_in_snapshot",
                    "detail": (
                        "quote is not a verbatim span of the retained "
                        "snapshot text"
                    ),
                }
            )
            continue
        try:
            fact = PrecedentFact(
                fact_id=fact_id,
                statement=str(item["statement"]),
                quote=quote,
                quote_start=start,
                quote_end=start + len(quote),
                snapshot_ref=snapshot_ref,
                snapshot_text_sha256=snapshot_text_sha256,
                annotator=annotator,
                annotator_is_harness=False,
                topic=ConstructabilityTopic(item.get("topic", "support")),
                strength=PolicyConstraintStrength(
                    item.get("strength", "soft")
                ),
                decision_refs=refs,
            )
            fact.require_quote_in(snapshot_text)
        except Exception as exc:
            rejections.append(
                {
                    "fact_id": fact_id,
                    "code": "quote_or_schema_rejected",
                    "detail": str(exc)[:300],
                }
            )
            continue
        facts.append(fact)
    ids = [fact.fact_id for fact in facts]
    if len(ids) != len(set(ids)):
        raise ResearchError("candidate fact ids must be unique")
    if not facts:
        raise ResearchError(
            f"no candidate survived validation; rejections={rejections}"
        )
    return (
        tuple(sorted(facts, key=lambda fact: fact.fact_id)),
        tuple(rejections),
    )


def decode_research_json(text: str) -> Mapping[str, object]:
    """Decode a model's JSON research output, tolerating code fences."""

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n", "", stripped)
        stripped = re.sub(r"\n```$", "", stripped)
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ResearchError(f"research output is not valid JSON: {exc}")
    if not isinstance(decoded, Mapping):
        raise ResearchError("research output must decode to an object")
    return decoded
