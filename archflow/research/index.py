"""Branch-bound canonical research index and progress contracts.

These derived views are scoped to one selected branch revision. They carry no
evidence, closure, selection, or canonical-write authority and substitute for
no retained record read during verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping

from archflow.research.branch import (
    BranchEvidenceSnapshot,
    BranchPrecedentAdoption,
    BranchPrecedentQuery,
    BranchResearchError,
    BranchResearchScope,
    DecisionResearchNeed,
    compile_branch_query,
    require_branch_source_url,
    require_record_payload,
)
from archflow.evidence.sufficiency import (
    DecisionUniverseClosure,
    DecisionUniverseRevision,
    EvidenceSufficiencyReceipt,
    EvidenceSufficiencyPolicy,
    FrontierStatus,
    GapReason,
    ResearchFrontier,
    build_research_frontier,
)
from archflow.project import ProjectRecordRef
from archflow.state.geometry_program import digest_value


class BasisIndexError(ValueError):
    """A record payload offered to the index is malformed."""


def require_branch_frontier_matches(
    index: "BranchBasisIndex",
    *,
    universe: DecisionUniverseRevision,
    policy: EvidenceSufficiencyPolicy,
    closure: DecisionUniverseClosure,
    sufficiency: EvidenceSufficiencyReceipt,
    frontier: ResearchFrontier,
    require_complete: bool = False,
) -> None:
    """Fail closed unless a P079 frontier is exact and internally coherent."""

    if not isinstance(index, BranchBasisIndex):
        raise TypeError("index must be BranchBasisIndex")
    if not isinstance(universe, DecisionUniverseRevision):
        raise TypeError("universe must be DecisionUniverseRevision")
    if not isinstance(policy, EvidenceSufficiencyPolicy):
        raise TypeError("policy must be EvidenceSufficiencyPolicy")
    if not isinstance(closure, DecisionUniverseClosure):
        raise TypeError("closure must be DecisionUniverseClosure")
    if not isinstance(sufficiency, EvidenceSufficiencyReceipt):
        raise TypeError("sufficiency must be EvidenceSufficiencyReceipt")
    if not isinstance(frontier, ResearchFrontier):
        raise TypeError("frontier must be ResearchFrontier")
    if universe.scope_digest != index.scope.scope_digest:
        raise BasisIndexError("decision universe crosses the branch research scope")
    if (
        frontier.universe_digest != universe.universe_digest
        or frontier.policy_digest != policy.policy_digest
        or closure.universe_digest != universe.universe_digest
        or closure.policy_digest != policy.policy_digest
        or sufficiency.universe_digest != universe.universe_digest
        or sufficiency.policy_digest != policy.policy_digest
    ):
        raise BasisIndexError("research frontier crosses its exact universe or policy")
    rebuilt = build_research_frontier(closure, sufficiency)
    if rebuilt != frontier:
        raise BasisIndexError(
            "research frontier does not recompile from its exact closure and sufficiency"
        )
    reasons = {item.gap.reason for item in frontier.work_items}
    if GapReason.UNRESOLVED_CONFLICT in reasons:
        expected = FrontierStatus.HUMAN_REVIEW_REQUIRED
    elif reasons & {
        GapReason.PENDING_UNIVERSE_EXPANSION,
        GapReason.ADOPTED_EXPANSION_NOT_APPLIED,
    }:
        expected = FrontierStatus.UNIVERSE_EXPANSION_REQUIRED
    elif frontier.work_items:
        expected = FrontierStatus.CONTINUE
    else:
        expected = FrontierStatus.COMPLETE
    if frontier.status is not expected:
        raise BasisIndexError("research frontier status was tampered or contradicts its work")
    if require_complete and frontier.status is not FrontierStatus.COMPLETE:
        raise BasisIndexError("branch decision context requires a COMPLETE research frontier")
    universe_decisions = {item.decision_ref for item in universe.nodes}
    uncovered_in_universe = set(index.uncovered) & universe_decisions
    if frontier.status is FrontierStatus.COMPLETE and uncovered_in_universe:
        raise BasisIndexError(
            "COMPLETE research frontier contradicts uncovered branch basis"
        )


@dataclass(frozen=True, slots=True)
class BranchBasisIndex:
    """Derived evidence view for exactly one selected branch revision."""

    scope: BranchResearchScope
    decisions: dict[str, dict]
    sources: dict[str, dict]
    uncovered: tuple[str, ...]
    derived_from: tuple[str, ...]

    SCHEMA = "BranchBasisIndex@1"

    def __post_init__(self) -> None:
        if not isinstance(self.scope, BranchResearchScope):
            raise TypeError("scope must be BranchResearchScope")
        if tuple(self.decisions) != tuple(sorted(self.decisions)):
            raise BasisIndexError("branch decision shards must use stable order")
        if set(self.decisions) != set(self.scope.active_decision_refs):
            raise BasisIndexError(
                "branch index must cover the complete active decision universe"
            )
        expected_uncovered = tuple(
            ref
            for ref, shard in self.decisions.items()
            if not shard.get("facts")
        )
        if self.uncovered != expected_uncovered:
            raise BasisIndexError("branch uncovered decision set changed")

    @property
    def index_digest(self) -> str:
        return digest_value(self.to_dict())

    def summary(self) -> dict[str, object]:
        return {
            "scope_digest": self.scope.scope_digest,
            "branch_id": self.scope.branch_id,
            "branch_revision_digest": self.scope.branch_revision_digest,
            "decision_count": len(self.decisions),
            "evidence_present": [
                ref for ref, shard in self.decisions.items() if shard["facts"]
            ],
            "uncovered": list(self.uncovered),
            "source_count": len(self.sources),
            "derived_from_count": len(self.derived_from),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "scope": self.scope.to_dict(),
            "scope_digest": self.scope.scope_digest,
            "decisions": self.decisions,
            "sources": self.sources,
            "uncovered": list(self.uncovered),
            "derived_from": list(self.derived_from),
            "evidence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchBasisIndex":
        if not isinstance(value, Mapping):
            raise TypeError("branch basis index must be a mapping")
        expected = {
            "schema",
            "scope",
            "scope_digest",
            "decisions",
            "sources",
            "uncovered",
            "derived_from",
            "evidence_authority",
            "canonical_write_authority",
        }
        if set(value) != expected:
            raise BasisIndexError("branch basis index schema drifted")
        if (
            value["schema"] != cls.SCHEMA
        ):
            raise BasisIndexError("branch basis index acquired authority")
        scope = BranchResearchScope.from_dict(value["scope"])
        if value["scope_digest"] != scope.scope_digest:
            raise BasisIndexError("branch basis scope digest changed")
        decisions = value["decisions"]
        sources = value["sources"]
        uncovered = value["uncovered"]
        derived_from = value["derived_from"]
        if not isinstance(decisions, Mapping) or not isinstance(sources, Mapping):
            raise TypeError("branch basis shards must be mappings")
        if not isinstance(uncovered, list) or not isinstance(derived_from, list):
            raise TypeError("branch basis index refs must be lists")
        return cls(
            scope=scope,
            decisions={str(key): dict(item) for key, item in decisions.items()},
            sources={str(key): dict(item) for key, item in sources.items()},
            uncovered=tuple(str(item) for item in uncovered),
            derived_from=tuple(str(item) for item in derived_from),
        )


def _branch_record_prefix(scope: BranchResearchScope) -> str:
    return (
        f"project://{scope.run.project_id}/runs/{scope.run.run_id}/"
        f"branches/{scope.branch_id}/records/"
    )


def _require_branch_record_name(
    record_name: str,
    scope: BranchResearchScope,
) -> None:
    if not record_name.startswith(_branch_record_prefix(scope)):
        raise BasisIndexError(
            "branch record reference is outside the exact P036 branch area"
        )


def build_branch_basis_index(
    records: Iterable[tuple[str, Mapping[str, object]]],
    *,
    scope: BranchResearchScope,
) -> BranchBasisIndex:
    """Build one index without reading facts from any other branch scope.

    A branch adoption is accepted only when its exact query is retained in the
    same scope.  Every fact must resolve to the retained snapshot named by its
    ``snapshot_ref`` and that snapshot URL must satisfy the query allowlist.
    Legacy unscoped ``@1`` queries/adoptions are deliberately ignored here.
    """

    if not isinstance(scope, BranchResearchScope):
        raise TypeError("scope must be BranchResearchScope")
    queries: dict[str, tuple[str, BranchPrecedentQuery]] = {}
    adoption_rows: list[tuple[str, BranchPrecedentAdoption]] = []
    snapshots: dict[str, tuple[str, BranchEvidenceSnapshot]] = {}
    for record_name, payload in records:
        if not isinstance(record_name, str) or not record_name:
            raise BasisIndexError("record name must be non-empty text")
        if not isinstance(payload, Mapping):
            raise BasisIndexError(f"{record_name}: payload must be a mapping")
        schema = payload.get("schema")
        try:
            if schema == BranchPrecedentQuery.SCHEMA:
                query = BranchPrecedentQuery.from_dict(payload)
                if query.scope.scope_digest != scope.scope_digest:
                    continue
                _require_branch_record_name(record_name, scope)
                if query.query_id in queries:
                    raise BasisIndexError(
                        f"duplicate branch query_id: {query.query_id}"
                    )
                queries[query.query_id] = (record_name, query)
            elif schema == BranchPrecedentAdoption.SCHEMA:
                adoption = BranchPrecedentAdoption.from_dict(payload)
                if adoption.scope_digest != scope.scope_digest:
                    continue
                _require_branch_record_name(record_name, scope)
                adoption_rows.append((record_name, adoption))
            elif schema == BranchEvidenceSnapshot.SCHEMA:
                snapshot = BranchEvidenceSnapshot.from_dict(payload)
                if snapshot.scope_digest != scope.scope_digest:
                    continue
                _require_branch_record_name(record_name, scope)
                if record_name in snapshots:
                    raise BasisIndexError(
                        f"duplicate snapshot record name: {record_name}"
                    )
                snapshots[record_name] = (record_name, snapshot)
        except BranchResearchError as exc:
            raise BasisIndexError(f"{record_name}: {exc}") from exc

    decisions: dict[str, dict] = {
        decision_ref: {
            "scope_digest": scope.scope_digest,
            "branch_id": scope.branch_id,
            "branch_revision_digest": scope.branch_revision_digest,
            "decision_ref": decision_ref,
            "facts": [],
            "queries": [],
        }
        for decision_ref in scope.active_decision_refs
    }
    derived_from: set[str] = set()
    for record_name, query in queries.values():
        derived_from.add(record_name)
        for decision_ref in query.decision_refs:
            decisions[decision_ref]["queries"].append(
                {
                    "query_id": query.query_id,
                    "query_digest": query.query_digest,
                    "question": query.question,
                    "search_terms": list(query.search_terms),
                    "domain_allowlist": list(query.domain_allowlist),
                }
            )

    sources: dict[str, dict] = {}
    seen_adoption_ids: set[str] = set()
    for record_name, wrapped in adoption_rows:
        if wrapped.adoption_id in seen_adoption_ids:
            raise BasisIndexError(
                f"duplicate branch adoption_id: {wrapped.adoption_id}"
            )
        seen_adoption_ids.add(wrapped.adoption_id)
        query_row = queries.get(wrapped.query_id)
        if query_row is None or query_row[1].query_digest != wrapped.query_digest:
            raise BasisIndexError(
                f"{wrapped.adoption_id}: exact branch query is missing or changed"
            )
        query = query_row[1]
        if (
            wrapped.branch_id != scope.branch_id
            or wrapped.branch_revision_digest != scope.branch_revision_digest
        ):
            raise BasisIndexError(
                f"{wrapped.adoption_id}: branch revision identity changed"
            )
        derived_from.add(record_name)
        for fact in wrapped.adoption.facts:
            if not fact.decision_refs or not set(fact.decision_refs) <= set(
                query.decision_refs
            ):
                raise BasisIndexError(
                    f"{fact.fact_id}: fact exceeds its exact branch query"
                )
            snapshot_row = snapshots.get(fact.snapshot_ref)
            if snapshot_row is None:
                raise BasisIndexError(
                    f"{fact.fact_id}: retained snapshot record is missing"
                )
            snapshot_record_name, snapshot = snapshot_row
            if (
                snapshot.branch_id != scope.branch_id
                or snapshot.branch_revision_digest
                != scope.branch_revision_digest
                or snapshot.query_id != query.query_id
                or snapshot.query_digest != query.query_digest
            ):
                raise BasisIndexError(
                    f"{fact.fact_id}: snapshot crosses its exact branch query"
                )
            if snapshot.text_sha256 != fact.snapshot_text_sha256:
                raise BasisIndexError(
                    f"{fact.fact_id}: snapshot content digest changed"
                )
            try:
                require_branch_source_url(
                    query,
                    snapshot.requested_url,
                    field="retained_requested_url",
                )
                require_branch_source_url(
                    query,
                    snapshot.final_url,
                    field="retained_final_url",
                )
                fact.require_quote_in(snapshot.text)
            except (TypeError, ValueError) as exc:
                raise BasisIndexError(
                    f"{fact.fact_id}: retained snapshot is invalid: {exc}"
                ) from exc
            derived_from.add(snapshot_record_name)
            row = {
                "scope_digest": scope.scope_digest,
                "branch_id": scope.branch_id,
                "branch_revision_digest": scope.branch_revision_digest,
                "query_id": query.query_id,
                "query_digest": query.query_digest,
                "adoption_id": wrapped.adoption_id,
                "authority_id": wrapped.adoption.authority_id,
                "fact_id": fact.fact_id,
                "statement": fact.statement,
                "strength": fact.strength.value,
                "topic": fact.topic.value,
                "quote": fact.quote,
                "quote_start": fact.quote_start,
                "quote_end": fact.quote_end,
                "snapshot_ref": fact.snapshot_ref,
                "snapshot_text_sha256": fact.snapshot_text_sha256,
                "decision_refs": list(fact.decision_refs),
            }
            for decision_ref in fact.decision_refs:
                decisions[decision_ref]["facts"].append(dict(row))
            source = sources.setdefault(
                fact.snapshot_ref,
                {
                    "scope_digest": scope.scope_digest,
                    "branch_id": scope.branch_id,
                    "snapshot_ref": fact.snapshot_ref,
                    "snapshot_text_sha256": fact.snapshot_text_sha256,
                    "requested_url": snapshot.requested_url,
                    "url": snapshot.final_url,
                    "facts": [],
                    "decision_refs": [],
                },
            )
            if (
                source["snapshot_text_sha256"] != fact.snapshot_text_sha256
                or source["url"] != snapshot.final_url
                or source["requested_url"] != snapshot.requested_url
            ):
                raise BasisIndexError("branch source identity changed")
            source["facts"].append(fact.fact_id)
            source["decision_refs"].extend(fact.decision_refs)

    for shard in decisions.values():
        shard["queries"] = sorted(
            shard["queries"], key=lambda item: item["query_id"]
        )
        shard["facts"] = sorted(
            shard["facts"],
            key=lambda item: (item["fact_id"], item["adoption_id"]),
        )
        shard["status"] = (
            "evidence_present" if shard["facts"] else "uncovered"
        )
    for source in sources.values():
        source["facts"] = sorted(set(source["facts"]))
        source["decision_refs"] = sorted(set(source["decision_refs"]))
    uncovered = tuple(
        decision_ref
        for decision_ref, shard in decisions.items()
        if not shard["facts"]
    )
    return BranchBasisIndex(
        scope=scope,
        decisions=decisions,
        sources={key: sources[key] for key in sorted(sources)},
        uncovered=uncovered,
        derived_from=tuple(sorted(derived_from)),
    )


@dataclass(frozen=True, slots=True)
class BranchDecisionContext:
    """Bounded prompt context that cannot lose its selected-branch identity."""

    scope: BranchResearchScope
    index_digest: str
    index_record_ref: ProjectRecordRef
    universe_digest: str
    policy_digest: str
    frontier_digest: str
    decision_refs: tuple[str, ...]
    decision_basis: dict[str, list[dict]]
    source_refs: tuple[str, ...]

    SCHEMA = "BranchDecisionContext@2"

    def __post_init__(self) -> None:
        if not isinstance(self.scope, BranchResearchScope):
            raise TypeError("scope must be BranchResearchScope")
        for value, field in (
            (self.index_digest, "index_digest"),
            (self.universe_digest, "universe_digest"),
            (self.policy_digest, "policy_digest"),
            (self.frontier_digest, "frontier_digest"),
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise BasisIndexError(f"{field} must be SHA-256")
        if not isinstance(self.index_record_ref, ProjectRecordRef):
            raise TypeError("index_record_ref must be ProjectRecordRef")
        prefix = (
            f"runs/{self.scope.run.run_id}/branches/{self.scope.branch_id}/"
            "records/branch-basis-"
        )
        if (
            self.index_record_ref.project_id != self.scope.run.project_id
            or not self.index_record_ref.relative_path.startswith(prefix)
        ):
            raise BasisIndexError("context index record crosses branch scope")
        if self.decision_refs != tuple(sorted(set(self.decision_refs))):
            raise BasisIndexError("context decision refs must be stable")
        if set(self.decision_basis) != set(self.decision_refs):
            raise BasisIndexError("context basis does not match its decisions")
        if self.source_refs != tuple(sorted(set(self.source_refs))):
            raise BasisIndexError("context source refs must be stable")

    def to_dict(self) -> dict[str, object]:
        identity = {
            "schema": self.SCHEMA,
            "branch_scope": self.scope.to_dict(),
            "scope_digest": self.scope.scope_digest,
            "index_digest": self.index_digest,
            "universe_digest": self.universe_digest,
            "policy_digest": self.policy_digest,
            "frontier_digest": self.frontier_digest,
            "index_record_ref": {
                "project_id": self.index_record_ref.project_id,
                "relative_path": self.index_record_ref.relative_path,
                "sha256": self.index_record_ref.sha256,
                "media_type": self.index_record_ref.media_type,
            },
            "decision_refs": list(self.decision_refs),
            "decision_basis": self.decision_basis,
            "context_refs": list(self.scope.context_refs),
            "source_refs": list(self.source_refs),
            "selection_authority": False,
            "canonical_write_authority": False,
        }
        return {
            **identity,
            "context_digest": digest_value(identity),
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchDecisionContext":
        if not isinstance(value, Mapping):
            raise TypeError("branch decision context must be a mapping")
        expected = {
            "schema",
            "branch_scope",
            "scope_digest",
            "index_digest",
            "universe_digest",
            "policy_digest",
            "frontier_digest",
            "index_record_ref",
            "decision_refs",
            "decision_basis",
            "context_refs",
            "source_refs",
            "selection_authority",
            "canonical_write_authority",
            "context_digest",
        }
        if set(value) != expected or value.get("schema") != cls.SCHEMA:
            raise BasisIndexError("branch decision context schema drifted")
        identity = {key: value[key] for key in expected - {"context_digest"}}
        if value["context_digest"] != digest_value(identity):
            raise BasisIndexError("branch decision context digest changed")
        scope = BranchResearchScope.from_dict(value["branch_scope"])
        if value["scope_digest"] != scope.scope_digest:
            raise BasisIndexError("branch decision context scope changed")
        ref = value["index_record_ref"]
        if not isinstance(ref, Mapping) or set(ref) != {
            "project_id",
            "relative_path",
            "sha256",
            "media_type",
        }:
            raise BasisIndexError("branch context index ref schema drifted")
        decision_refs = value["decision_refs"]
        context_refs = value["context_refs"]
        source_refs = value["source_refs"]
        basis = value["decision_basis"]
        if not all(
            isinstance(item, list)
            for item in (decision_refs, context_refs, source_refs)
        ) or not isinstance(basis, Mapping):
            raise TypeError("branch decision context collections are invalid")
        if context_refs != list(scope.context_refs):
            raise BasisIndexError("branch decision context refs changed")
        return cls(
            scope=scope,
            index_digest=str(value["index_digest"]),
            universe_digest=str(value["universe_digest"]),
            policy_digest=str(value["policy_digest"]),
            frontier_digest=str(value["frontier_digest"]),
            index_record_ref=ProjectRecordRef(
                project_id=str(ref["project_id"]),
                relative_path=str(ref["relative_path"]),
                sha256=str(ref["sha256"]),
                media_type=str(ref["media_type"]),
            ),
            decision_refs=tuple(str(item) for item in decision_refs),
            decision_basis={str(key): list(rows) for key, rows in basis.items()},
            source_refs=tuple(str(item) for item in source_refs),
        )


def compile_branch_decision_context(
    index: BranchBasisIndex,
    *,
    index_record: ProjectRecordRef,
    decision_refs: tuple[str, ...],
    universe: DecisionUniverseRevision,
    policy: EvidenceSufficiencyPolicy,
    closure: DecisionUniverseClosure,
    sufficiency: EvidenceSufficiencyReceipt,
    frontier: ResearchFrontier,
) -> BranchDecisionContext:
    """Select branch-local facts, failing closed on any uncovered decision."""

    if not isinstance(index, BranchBasisIndex):
        raise TypeError("index must be BranchBasisIndex")
    require_record_payload(
        index_record,
        index.to_dict(),
        run=index.scope.run,
        area_prefix=f"branches/{index.scope.branch_id}/records",
        field="index_record",
    )
    require_branch_frontier_matches(
        index,
        universe=universe,
        policy=policy,
        closure=closure,
        sufficiency=sufficiency,
        frontier=frontier,
        require_complete=True,
    )
    if decision_refs != tuple(sorted(set(decision_refs))) or not decision_refs:
        raise BasisIndexError("decision_refs must be sorted, unique, and non-empty")
    if not set(decision_refs) <= set(index.scope.active_decision_refs):
        raise BasisIndexError("context requested a decision outside the branch")
    universe_decisions = {item.decision_ref for item in universe.nodes}
    if not set(decision_refs) <= universe_decisions:
        raise BasisIndexError("context requested a decision outside its P079 universe")
    missing = tuple(ref for ref in decision_refs if not index.decisions[ref]["facts"])
    if missing:
        raise BasisIndexError(
            f"branch decision context is uncovered: {missing}"
        )
    keep = (
        "fact_id",
        "statement",
        "strength",
        "quote",
        "snapshot_ref",
        "adoption_id",
        "authority_id",
    )
    basis = {
        decision_ref: [
            {key: fact[key] for key in keep}
            for fact in index.decisions[decision_ref]["facts"]
        ]
        for decision_ref in decision_refs
    }
    source_refs = tuple(
        sorted(
            {
                fact["snapshot_ref"]
                for decision_ref in decision_refs
                for fact in index.decisions[decision_ref]["facts"]
            }
        )
    )
    return BranchDecisionContext(
        scope=index.scope,
        index_digest=index.index_digest,
        index_record_ref=index_record,
        universe_digest=universe.universe_digest,
        policy_digest=policy.policy_digest,
        frontier_digest=frontier.frontier_digest,
        decision_refs=decision_refs,
        decision_basis=basis,
        source_refs=source_refs,
    )


def compile_next_branch_queries(
    index: BranchBasisIndex,
    *,
    needs: tuple[DecisionResearchNeed, ...],
) -> tuple[BranchPrecedentQuery, ...]:
    """Compile the next research wave from this branch's uncovered set."""

    if not isinstance(index, BranchBasisIndex):
        raise TypeError("index must be BranchBasisIndex")
    if not isinstance(needs, tuple) or any(
        not isinstance(item, DecisionResearchNeed) for item in needs
    ):
        raise TypeError("needs must be DecisionResearchNeed tuple")
    refs = tuple(sorted(item.decision_ref for item in needs))
    if refs != index.uncovered:
        raise BasisIndexError(
            "next research needs must cover every uncovered branch decision "
            "exactly once"
        )
    if len(refs) != len(set(refs)):
        raise BasisIndexError("next research needs repeat a decision")
    return tuple(
        compile_branch_query(index.scope, need)
        for need in sorted(needs, key=lambda item: item.decision_ref)
    )


class BranchRAGProgressStatus(StrEnum):
    CONTINUE = "continue"
    UNIVERSE_EXPANSION_REQUIRED = "universe_expansion_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class BranchRAGProgress:
    """Machine-readable progress surface for one branch research round."""

    scope: BranchResearchScope
    index_digest: str
    universe_digest: str
    policy_digest: str
    frontier_digest: str
    frontier_status: FrontierStatus
    evidence_present_decision_refs: tuple[str, ...]
    uncovered_decision_refs: tuple[str, ...]
    next_query_decision_refs: tuple[str, ...]
    next_query_digests: tuple[str, ...]
    status: BranchRAGProgressStatus

    SCHEMA = "BranchRAGProgress@2"

    def __post_init__(self) -> None:
        if not isinstance(self.scope, BranchResearchScope):
            raise TypeError("scope must be BranchResearchScope")
        for value, field in (
            (self.index_digest, "index_digest"),
            (self.universe_digest, "universe_digest"),
            (self.policy_digest, "policy_digest"),
            (self.frontier_digest, "frontier_digest"),
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise BasisIndexError(f"progress {field} must be SHA-256")
        if not isinstance(self.frontier_status, FrontierStatus):
            raise TypeError("frontier_status must be FrontierStatus")
        for field, values in (
            (
                "evidence_present_decision_refs",
                self.evidence_present_decision_refs,
            ),
            ("uncovered_decision_refs", self.uncovered_decision_refs),
            ("next_query_decision_refs", self.next_query_decision_refs),
            ("next_query_digests", self.next_query_digests),
        ):
            if values != tuple(sorted(set(values))):
                raise BasisIndexError(f"{field} must be sorted and unique")
        if set(self.evidence_present_decision_refs) & set(
            self.uncovered_decision_refs
        ):
            raise BasisIndexError("covered and uncovered progress overlap")
        if set(self.evidence_present_decision_refs) | set(
            self.uncovered_decision_refs
        ) != set(self.scope.active_decision_refs):
            raise BasisIndexError("progress lost part of the decision universe")
        if self.next_query_decision_refs != self.uncovered_decision_refs:
            raise BasisIndexError("progress next queries do not close its gaps")
        if not isinstance(self.status, BranchRAGProgressStatus):
            raise TypeError("status must be BranchRAGProgressStatus")
        expected = BranchRAGProgressStatus(self.frontier_status.value)
        if self.status is not expected:
            raise BasisIndexError("progress status contradicts its P079 frontier")
        if self.status is BranchRAGProgressStatus.COMPLETE and self.uncovered_decision_refs:
            raise BasisIndexError("complete progress retains uncovered branch decisions")

    @property
    def progress_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "branch_scope": self.scope.to_dict(),
            "scope_digest": self.scope.scope_digest,
            "index_digest": self.index_digest,
            "universe_digest": self.universe_digest,
            "policy_digest": self.policy_digest,
            "frontier_digest": self.frontier_digest,
            "frontier_status": self.frontier_status.value,
            "evidence_present_decision_refs": list(
                self.evidence_present_decision_refs
            ),
            "uncovered_decision_refs": list(self.uncovered_decision_refs),
            "next_query_decision_refs": list(self.next_query_decision_refs),
            "next_query_digests": list(self.next_query_digests),
            "status": self.status.value,
            "progress": {
                "complete": len(self.evidence_present_decision_refs),
                "total": len(self.scope.active_decision_refs),
            },
            "selection_authority": False,
            "canonical_write_authority": False,
        }


def compile_branch_rag_progress(
    index: BranchBasisIndex,
    *,
    next_queries: tuple[BranchPrecedentQuery, ...],
    universe: DecisionUniverseRevision,
    policy: EvidenceSufficiencyPolicy,
    closure: DecisionUniverseClosure,
    sufficiency: EvidenceSufficiencyReceipt,
    frontier: ResearchFrontier,
) -> BranchRAGProgress:
    if not isinstance(index, BranchBasisIndex):
        raise TypeError("index must be BranchBasisIndex")
    require_branch_frontier_matches(
        index,
        universe=universe,
        policy=policy,
        closure=closure,
        sufficiency=sufficiency,
        frontier=frontier,
    )
    if not isinstance(next_queries, tuple) or any(
        not isinstance(item, BranchPrecedentQuery) for item in next_queries
    ):
        raise TypeError("next_queries must be BranchPrecedentQuery tuple")
    for query in next_queries:
        if query.scope.scope_digest != index.scope.scope_digest:
            raise BasisIndexError("next query crossed branch scope")
    next_refs = tuple(
        sorted(
            {
                decision_ref
                for query in next_queries
                for decision_ref in query.decision_refs
            }
        )
    )
    if next_refs != index.uncovered:
        raise BasisIndexError(
            "next query set must equal the uncovered branch decisions"
        )
    covered = tuple(
        ref for ref, shard in index.decisions.items() if shard["facts"]
    )
    return BranchRAGProgress(
        scope=index.scope,
        index_digest=index.index_digest,
        universe_digest=universe.universe_digest,
        policy_digest=policy.policy_digest,
        frontier_digest=frontier.frontier_digest,
        frontier_status=frontier.status,
        evidence_present_decision_refs=covered,
        uncovered_decision_refs=index.uncovered,
        next_query_decision_refs=next_refs,
        next_query_digests=tuple(
            sorted(query.query_digest for query in next_queries)
        ),
        status=BranchRAGProgressStatus(frontier.status.value),
    )


__all__ = [
    "BasisIndexError",
    "BranchBasisIndex",
    "BranchDecisionContext",
    "BranchRAGProgress",
    "BranchRAGProgressStatus",
    "build_branch_basis_index",
    "compile_branch_decision_context",
    "compile_branch_rag_progress",
    "compile_next_branch_queries",
    "require_branch_frontier_matches",
]
