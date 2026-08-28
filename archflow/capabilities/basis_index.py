"""Sharded, decision-keyed index over retained basis records.

The index is a derived view, never evidence: a pure deterministic
function over retained record payloads (precedent queries, adoptions,
calibrations, evidence snapshots). One shard per decision ref lets a
caller who is editing one decision read exactly that shard — bounding
both context tokens and blast radius. The same derivation surfaces the
coverage view: a decision named by any query but backed by zero adopted
facts is a typed ``uncovered`` entry, never silence. A per-source
reverse shard names exactly which facts and decisions a revised source
would reopen. The index carries no authority and substitutes for no
record read during verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping


class BasisIndexError(ValueError):
    """A record payload offered to the index is malformed."""


@dataclass(frozen=True, slots=True)
class BasisIndex:
    """Derived shards keyed by decision ref and by source snapshot."""

    decisions: dict[str, dict]
    sources: dict[str, dict]
    uncovered: tuple[str, ...]
    derived_from: tuple[str, ...]

    def summary(self) -> dict[str, object]:
        return {
            "decision_count": len(self.decisions),
            "covered": sorted(
                ref
                for ref, shard in self.decisions.items()
                if shard["facts"]
            ),
            "uncovered": list(self.uncovered),
            "source_count": len(self.sources),
            "derived_from_count": len(self.derived_from),
        }


def decision_slug(decision_ref: str) -> str:
    """Filesystem-safe shard name for one decision ref."""

    slug = re.sub(r"[^a-z0-9\-]+", "-", decision_ref.lower()).strip("-")
    if not slug:
        raise BasisIndexError(f"unusable decision ref: {decision_ref!r}")
    return slug


def build_basis_index(
    records: Iterable[tuple[str, Mapping[str, object]]],
) -> BasisIndex:
    """Derive the index from ``(record_name, payload)`` pairs.

    ``record_name`` is the retained record's file name (carrying its
    digest); it is listed in ``derived_from`` so the view names exactly
    the evidence it was computed over.
    """

    queries: dict[str, dict] = {}
    adoptions: dict[str, dict] = {}
    calibrations: list[Mapping[str, object]] = []
    snapshots: dict[str, dict] = {}
    derived_from: list[str] = []
    for record_name, payload in records:
        if not isinstance(payload, Mapping):
            raise BasisIndexError(f"{record_name}: payload must be a mapping")
        schema = payload.get("schema")
        if schema == "PrecedentQuery@1":
            queries[str(payload["query_id"])] = dict(payload)
        elif schema == "PrecedentAdoption@1":
            adoptions[str(payload["adoption_id"])] = dict(payload)
        elif schema == "P070DecisionCalibration@1":
            calibrations.append(payload)
        elif schema == "WebEvidenceSnapshot@1":
            snapshots[str(payload.get("text_sha256", record_name))] = {
                "url": payload.get("url"),
                "retrieved_at": payload.get("retrieved_at"),
            }
        else:
            continue
        derived_from.append(record_name)

    decisions: dict[str, dict] = {}

    def shard(decision_ref: str) -> dict:
        return decisions.setdefault(
            decision_ref,
            {"decision_ref": decision_ref, "facts": [], "queries": []},
        )

    for query in queries.values():
        for decision_ref in query.get("decision_refs", ()):
            entry = shard(str(decision_ref))
            entry["queries"].append(
                {
                    "query_id": query["query_id"],
                    "question": query.get("question"),
                }
            )

    sources: dict[str, dict] = {}
    for adoption in adoptions.values():
        for fact in adoption.get("facts", ()):
            fact_refs = tuple(fact.get("decision_refs", ()))
            row = {
                "fact_id": fact["fact_id"],
                "statement": fact.get("statement"),
                "strength": fact.get("strength"),
                "topic": fact.get("topic"),
                "quote": fact.get("quote"),
                "quote_start": fact.get("quote_start"),
                "quote_end": fact.get("quote_end"),
                "snapshot_ref": fact.get("snapshot_ref"),
                "snapshot_text_sha256": fact.get("snapshot_text_sha256"),
                "adoption_id": adoption["adoption_id"],
                "authority_id": adoption.get("authority_id"),
                "decision_refs": list(fact_refs),
            }
            for decision_ref in fact_refs:
                shard(str(decision_ref))["facts"].append(dict(row))
            source_key = str(
                fact.get("snapshot_text_sha256") or fact.get("snapshot_ref")
            )
            source = sources.setdefault(
                source_key,
                {
                    "snapshot_text_sha256": fact.get("snapshot_text_sha256"),
                    "snapshot_ref": fact.get("snapshot_ref"),
                    "url": snapshots.get(source_key, {}).get("url"),
                    "facts": [],
                    "decision_refs": [],
                },
            )
            source["facts"].append(fact["fact_id"])
            source["decision_refs"].extend(fact_refs)

    for calibration in calibrations:
        for row in calibration.get("calibrations", ()):
            entry = shard(str(row["decision_ref"]))
            entry.setdefault("calibrations", []).append(
                {
                    "query_id": calibration.get("query_id"),
                    "adoption_ref": calibration.get("adoption_ref"),
                    "fact_ids": list(row.get("facts", ())),
                }
            )

    for entry in decisions.values():
        entry["facts"] = sorted(
            entry["facts"], key=lambda item: item["fact_id"]
        )
        entry["queries"] = sorted(
            {item["query_id"]: item for item in entry["queries"]}.values(),
            key=lambda item: item["query_id"],
        )
        if "calibrations" in entry:
            entry["calibrations"] = sorted(
                entry["calibrations"], key=lambda item: item["query_id"]
            )
        entry["status"] = "covered" if entry["facts"] else "uncovered"
    for source in sources.values():
        source["facts"] = sorted(set(source["facts"]))
        source["decision_refs"] = sorted(set(source["decision_refs"]))

    uncovered = tuple(
        sorted(
            ref
            for ref, entry in decisions.items()
            if not entry["facts"]
        )
    )
    return BasisIndex(
        decisions={ref: decisions[ref] for ref in sorted(decisions)},
        sources={key: sources[key] for key in sorted(sources)},
        uncovered=uncovered,
        derived_from=tuple(sorted(set(derived_from))),
    )
