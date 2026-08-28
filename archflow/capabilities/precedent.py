"""Authority-gated precedent facts from retained evidence snapshots.

The web (or any retrieved source) is untrusted input three gates from
generation. A ``PrecedentFact`` cites an exact quote with a character span
inside a named snapshot record; a ``PrecedentAdoption`` promotes selected
facts under a named authority; ``compile_precedent_constraints`` turns
each adopted fact into a build-policy constraint whose provenance chains
constraint to adoption to snapshot. The framework stores no fact values of
its own, and raw snapshot text never enters a prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from archflow.state.build_policy import (
    ConstructabilityConstraint,
    ConstructabilityTopic,
    PolicyConstraintStrength,
    PolicyProvenance,
)
from archflow.state.geometry_program import digest_value

_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{0,80}$")


class PrecedentError(ValueError):
    """A precedent fact or adoption is invalid."""


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise PrecedentError(f"{field} must be non-empty text")
    return value


@dataclass(frozen=True, slots=True)
class PrecedentFact:
    """One candidate fact quoted exactly from one retained snapshot."""

    fact_id: str
    statement: str
    quote: str
    quote_start: int
    quote_end: int
    snapshot_ref: str
    snapshot_text_sha256: str
    annotator: str
    annotator_is_harness: bool
    topic: ConstructabilityTopic
    strength: PolicyConstraintStrength

    SCHEMA = "PrecedentFact@1"

    def __post_init__(self) -> None:
        if not _ID.match(self.fact_id):
            raise PrecedentError("fact_id must be a kebab identifier")
        _text(self.statement, "statement")
        _text(self.quote, "quote")
        _text(self.snapshot_ref, "snapshot_ref")
        _text(self.annotator, "annotator")
        if not re.fullmatch(r"[0-9a-f]{64}", self.snapshot_text_sha256 or ""):
            raise PrecedentError("snapshot text digest must be sha256")
        if not (
            isinstance(self.quote_start, int)
            and isinstance(self.quote_end, int)
            and 0 <= self.quote_start < self.quote_end
        ):
            raise PrecedentError("quote span must be a valid range")
        if not isinstance(self.topic, ConstructabilityTopic):
            raise TypeError("topic must be ConstructabilityTopic")
        if not isinstance(self.strength, PolicyConstraintStrength):
            raise TypeError("strength must be PolicyConstraintStrength")

    def require_quote_in(self, snapshot_text: str) -> None:
        """Fail closed unless the quote sits exactly at its claimed span."""

        span = snapshot_text[self.quote_start : self.quote_end]
        if span != self.quote:
            raise PrecedentError(
                f"{self.fact_id}: quote does not match its claimed span"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "fact_id": self.fact_id,
            "statement": self.statement,
            "quote": self.quote,
            "quote_start": self.quote_start,
            "quote_end": self.quote_end,
            "snapshot_ref": self.snapshot_ref,
            "snapshot_text_sha256": self.snapshot_text_sha256,
            "annotator": self.annotator,
            "annotator_is_harness": self.annotator_is_harness,
            "topic": self.topic.value,
            "strength": self.strength.value,
        }

    @classmethod
    def from_dict(cls, value) -> "PrecedentFact":
        if not isinstance(value, dict) or value.get("schema") != cls.SCHEMA:
            raise PrecedentError("precedent fact schema drifted")
        return cls(
            fact_id=value["fact_id"],
            statement=value["statement"],
            quote=value["quote"],
            quote_start=int(value["quote_start"]),
            quote_end=int(value["quote_end"]),
            snapshot_ref=value["snapshot_ref"],
            snapshot_text_sha256=value["snapshot_text_sha256"],
            annotator=value["annotator"],
            annotator_is_harness=bool(value["annotator_is_harness"]),
            topic=ConstructabilityTopic(value["topic"]),
            strength=PolicyConstraintStrength(value["strength"]),
        )


@dataclass(frozen=True, slots=True)
class PrecedentAdoption:
    """A named authority promoting selected quoted facts."""

    adoption_id: str
    authority_id: str
    adopted_at: str
    facts: tuple[PrecedentFact, ...]

    SCHEMA = "PrecedentAdoption@1"

    def __post_init__(self) -> None:
        if not _ID.match(self.adoption_id):
            raise PrecedentError("adoption_id must be a kebab identifier")
        _text(self.authority_id, "authority_id")
        _text(self.adopted_at, "adopted_at")
        if not self.facts or not isinstance(self.facts, tuple):
            raise PrecedentError("adoption requires at least one fact")
        fact_ids = [fact.fact_id for fact in self.facts]
        if fact_ids != sorted(set(fact_ids)):
            raise PrecedentError("fact ids must be sorted and unique")

    @property
    def adoption_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "adoption_id": self.adoption_id,
            "authority_id": self.authority_id,
            "adopted_at": self.adopted_at,
            "facts": [fact.to_dict() for fact in self.facts],
            "retrieved_text_authority": False,
            "design_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value) -> "PrecedentAdoption":
        if not isinstance(value, dict) or value.get("schema") != cls.SCHEMA:
            raise PrecedentError("precedent adoption schema drifted")
        return cls(
            adoption_id=value["adoption_id"],
            authority_id=value["authority_id"],
            adopted_at=value["adopted_at"],
            facts=tuple(
                PrecedentFact.from_dict(item) for item in value["facts"]
            ),
        )


def compile_precedent_constraints(
    adoption: PrecedentAdoption,
    *,
    adoption_ref: str,
    compiler_id: str,
    base_state_sha256: str,
) -> tuple[ConstructabilityConstraint, ...]:
    """One build-policy constraint per adopted fact, provenance chained."""

    if not isinstance(adoption, PrecedentAdoption):
        raise TypeError("adoption must be PrecedentAdoption")
    _text(adoption_ref, "adoption_ref")
    constraints = []
    for fact in adoption.facts:
        constraints.append(
            ConstructabilityConstraint(
                constraint_id=f"precedent-{fact.fact_id}",
                topic=fact.topic,
                strength=fact.strength,
                statement=fact.statement,
                subject_refs=(fact.snapshot_ref,),
                provenance=PolicyProvenance(
                    authority_id=adoption.authority_id,
                    source_refs=(adoption_ref,),
                    assumption_refs=(),
                    compiler_id=compiler_id,
                    base_state_sha256=base_state_sha256,
                ),
            )
        )
    return tuple(constraints)
