"""P036-backed persistence for branch-conditioned research records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from archive.archflow.research.index import (
    BranchBasisIndex,
    BranchDecisionContext,
    BranchRAGProgress,
    build_branch_basis_index,
    compile_branch_decision_context,
    compile_branch_rag_progress,
    compile_next_branch_queries,
    require_branch_frontier_matches,
)
from archive.archflow.research.branch import (
    BranchPrecedentAdoption,
    BranchPrecedentQuery,
    BranchResearchError,
    BranchResearchScope,
    BranchSelectionDecision,
    DecisionResearchNeed,
    _apply_branch_selection,
    _compile_branch_research_scope,
    bind_branch_snapshot,
)
from archive.archflow.capabilities.evidence_sufficiency import (
    DecisionUniverseClosure,
    DecisionUniverseRevision,
    EvidenceSufficiencyReceipt,
    EvidenceSufficiencyPolicy,
    ResearchFrontier,
)
from archflow.project.repository import FilesystemProjectRepository, ProjectRepositoryError
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, RunRef
from archive.archflow.runtime.branch_portfolio import (
    BranchPortfolioArchive,
    PersistedDesignPortfolio,
)
from archflow.state.design_portfolio import (
    BranchLifecycle,
    DesignOptionPortfolio,
    PortfolioTransitionKind,
)
from archflow.state.operational_state import OperationalMarkovState
from archflow.state.spatial import SchematicOptionSet


class BranchResearchArchiveError(BranchResearchError):
    """A record crosses its exact run, selected branch, or research scope."""


@dataclass(frozen=True, slots=True)
class PersistedBranchResearchWave:
    """One persisted retrieve/adopt/reindex feedback-loop checkpoint."""

    index: BranchBasisIndex
    index_ref: ProjectRecordRef
    next_queries: tuple[BranchPrecedentQuery, ...]
    query_refs: tuple[ProjectRecordRef, ...]
    frontier: ResearchFrontier
    frontier_ref: ProjectRecordRef
    progress: BranchRAGProgress
    progress_ref: ProjectRecordRef


class BranchResearchArchive:
    """Write immutable RAG records through the canonical project repository.

    The pre-selection decision lives at run scope.  Everything after selection
    lives under ``runs/<run>/branches/<branch>/records`` so an equal decision
    ref on another candidate can never become an implicit persistence scope.
    """

    def __init__(
        self,
        repository: FilesystemProjectRepository,
        *,
        run: RunRef,
    ) -> None:
        if not isinstance(repository, FilesystemProjectRepository):
            raise TypeError("repository must be FilesystemProjectRepository")
        if not isinstance(run, RunRef):
            raise TypeError("run must be RunRef")
        if repository.load_run(run.run_id) != run:
            raise BranchResearchArchiveError(
                "archive run does not match project repository"
            )
        self._repository = repository
        self._run = run
        self._run_destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        )

    @property
    def run(self) -> RunRef:
        return self._run

    def _require_scope_identity(self, scope: BranchResearchScope) -> None:
        if not isinstance(scope, BranchResearchScope):
            raise TypeError("scope must be BranchResearchScope")
        if scope.run != self._run:
            raise BranchResearchArchiveError(
                "research scope belongs to another exact-base run"
            )

    def load_selection(
        self,
        ref: ProjectRecordRef,
    ) -> BranchSelectionDecision:
        """Load one exact P036 selection record; URI text is insufficient."""

        if not isinstance(ref, ProjectRecordRef):
            raise TypeError("ref must be ProjectRecordRef")
        prefix = f"runs/{self._run.run_id}/records/branch-selection-"
        if ref.project_id != self._run.project_id or not ref.relative_path.startswith(
            prefix
        ):
            raise BranchResearchArchiveError(
                "selection ref is outside this run's P036 selection area"
            )
        try:
            decision = BranchSelectionDecision.from_dict(
                self._repository.load_json(ref)
            )
        except (ProjectRepositoryError, OSError, TypeError, ValueError) as exc:
            raise BranchResearchArchiveError(
                "selection record is missing, corrupt, or cross-scoped"
            ) from exc
        if (
            decision.project_id != self._run.project_id
            or decision.run_id != self._run.run_id
            or decision.base_state_sha256 != self._run.base.require_digest()
        ):
            raise BranchResearchArchiveError(
                "selection decision belongs to another exact-base run"
            )
        return decision

    def apply_selection(
        self,
        portfolio: DesignOptionPortfolio,
        selection_ref: ProjectRecordRef,
    ) -> PersistedDesignPortfolio:
        """Apply only the decision loaded from ``selection_ref`` and persist it."""

        decision = self.load_selection(selection_ref)
        selected = _apply_branch_selection(
            portfolio,
            decision,
            selection_record=selection_ref,
        )
        return BranchPortfolioArchive(
            self._repository,
            run=self._run,
        ).save(selected)

    def _require_current_scope(self, scope: BranchResearchScope) -> None:
        self._require_scope_identity(scope)
        source_option_set, source_state = self._load_source_inputs(
            scope.source_option_set_ref,
            scope.source_state_ref,
        )

        latest = BranchPortfolioArchive(
            self._repository,
            run=self._run,
        ).load_latest(portfolio_id=scope.portfolio_id).portfolio

        selected = latest.selected_branch
        if (
            latest.portfolio_digest != scope.portfolio_digest
            or latest.operational_state_digest != scope.operational_state_digest
            or selected is None
            or selected.lifecycle is not BranchLifecycle.SELECTED
            or selected.branch_id != scope.branch_id
            or selected.head.revision_id != scope.branch_revision_id
            or selected.head.revision_digest != scope.branch_revision_digest
            or latest.source_option_set_digest
            != source_option_set.option_set_digest
            or latest.operational_state_digest != source_state.state_digest
            or source_option_set.branch != source_state.branch
            or source_option_set.branch != scope.source_branch
            or source_option_set.operational_state_digest
            != source_state.state_digest
        ):
            raise BranchResearchArchiveError(
                "research scope is not the latest selected Candidate revision"
            )
        decision = self.load_selection(scope.selection_record_ref)
        transition = next(
            (
                item
                for item in reversed(latest.transitions)
                if item.kind is PortfolioTransitionKind.SELECT
                and scope.branch_id in item.affected_branch_ids
            ),
            None,
        )
        if (
            decision.decision_digest != scope.selection_decision_digest
            or decision.selected_branch_id != scope.branch_id
            or transition is None
            or transition.decision_ref != scope.selection_record_ref.uri
            or transition.predecessor_portfolio_digest
            != decision.candidate_portfolio_digest
            or transition.authority_id != decision.authority_id
        ):
            raise BranchResearchArchiveError(
                "scope does not replay its persisted selection"
            )
        selected_card = next(
            (
                card
                for card in decision.scorecards
                if card.branch_id == scope.branch_id
            ),
            None,
        )
        if selected_card is None:
            raise BranchResearchArchiveError(
                "scope selected Candidate has no persisted scorecard"
            )
        profile = selected_card.research_profile
        if (
            profile.branch_revision_digest != scope.branch_revision_digest
            or profile.active_decision_refs != scope.active_decision_refs
            or profile.branch_search_terms != scope.branch_search_terms
            or profile.excluded_search_terms != scope.excluded_search_terms
            or profile.domain_allowlist != scope.domain_allowlist
            or not set(profile.evidence_refs) <= set(scope.context_refs)
        ):
            raise BranchResearchArchiveError(
                "scope does not replay the selected Candidate research profile"
            )

    def _load_source_inputs(
        self,
        source_option_set_ref: ProjectRecordRef,
        source_state_ref: ProjectRecordRef,
    ) -> tuple[SchematicOptionSet, OperationalMarkovState]:
        try:
            source_option_set = SchematicOptionSet.from_dict(
                self._repository.load_json(source_option_set_ref)
            )
            source_state = OperationalMarkovState.from_dict(
                self._repository.load_json(source_state_ref)
            )
        except (ProjectRepositoryError, OSError, TypeError, ValueError) as exc:
            raise BranchResearchArchiveError(
                "scope source records are missing, corrupt, or cross-scoped"
            ) from exc
        if (
            source_option_set.branch.run != self._run
            or source_state.branch.run != self._run
        ):
            raise BranchResearchArchiveError(
                "scope source records belong to another exact-base run"
            )
        return source_option_set, source_state

    def _require_persisted_scope(self, scope: BranchResearchScope) -> None:
        self._require_current_scope(scope)
        destination = self._branch_destination(scope)
        for ref in self._repository.list_json(
            run=self._run,
            destination=destination,
        ):
            payload = self._repository.load_json(ref)
            if (
                payload.get("schema") == BranchResearchScope.SCHEMA
                and payload == scope.to_dict()
            ):
                return
        raise BranchResearchArchiveError(
            "exact branch research scope is not persisted"
        )

    def _branch_destination(
        self,
        scope: BranchResearchScope,
    ) -> PersistenceDestination:
        self._require_scope_identity(scope)
        return PersistenceDestination(
            PersistenceArea.RUN_BRANCH,
            run_id=self._run.run_id,
            branch_id=scope.branch_id,
        )

    def save_selection(
        self,
        decision: BranchSelectionDecision,
    ) -> ProjectRecordRef:
        if not isinstance(decision, BranchSelectionDecision):
            raise TypeError("decision must be BranchSelectionDecision")
        if (
            decision.project_id != self._run.project_id
            or decision.run_id != self._run.run_id
            or decision.base_state_sha256 != self._run.base.require_digest()
        ):
            raise BranchResearchArchiveError(
                "selection decision belongs to another exact-base run"
            )
        return self._repository.put_json(
            run=self._run,
            destination=self._run_destination,
            record_kind=f"branch-selection-{decision.decision_digest[:16]}",
            payload=decision.to_dict(),
        )

    def save_source_inputs(
        self,
        source_option_set: SchematicOptionSet,
        source_state: OperationalMarkovState,
    ) -> tuple[ProjectRecordRef, ProjectRecordRef]:
        """Persist the exact source objects later replayed by every scope use."""

        if not isinstance(source_option_set, SchematicOptionSet):
            raise TypeError("source_option_set must be SchematicOptionSet")
        if not isinstance(source_state, OperationalMarkovState):
            raise TypeError("source_state must be OperationalMarkovState")
        if (
            source_option_set.branch.run != self._run
            or source_state.branch.run != self._run
            or source_option_set.branch != source_state.branch
            or source_option_set.operational_state_digest
            != source_state.state_digest
        ):
            raise BranchResearchArchiveError(
                "source option set and Markov state do not share this exact run"
            )
        option_set_ref = self._repository.put_json(
            run=self._run,
            destination=self._run_destination,
            record_kind=(
                "branch-source-option-set-"
                f"{source_option_set.option_set_digest[:16]}"
            ),
            payload=source_option_set.to_dict(),
        )
        state_ref = self._repository.put_json(
            run=self._run,
            destination=self._run_destination,
            record_kind=f"branch-source-state-{source_state.state_digest[:16]}",
            payload=source_state.to_dict(),
        )
        return option_set_ref, state_ref

    def create_scope(
        self,
        *,
        scope_id: str,
        selection_ref: ProjectRecordRef,
        source_option_set_ref: ProjectRecordRef,
        source_state_ref: ProjectRecordRef,
        predecessor_scope_digest: str | None = None,
        context_refs: tuple[str, ...] = (),
    ) -> tuple[BranchResearchScope, ProjectRecordRef]:
        """Reload exact P036 inputs, compile one scope, and persist it atomically."""

        decision = self.load_selection(selection_ref)
        source_option_set, source_state = self._load_source_inputs(
            source_option_set_ref,
            source_state_ref,
        )
        portfolio = BranchPortfolioArchive(
            self._repository,
            run=self._run,
        ).load_latest(portfolio_id=decision.portfolio_id).portfolio
        scope = _compile_branch_research_scope(
            portfolio,
            decision,
            scope_id=scope_id,
            selection_record=selection_ref,
            source_option_set=source_option_set,
            source_option_set_ref=source_option_set_ref,
            source_state=source_state,
            source_state_ref=source_state_ref,
            predecessor_scope_digest=predecessor_scope_digest,
            context_refs=context_refs,
        )
        return scope, self.save_scope(scope)

    def save_scope(self, scope: BranchResearchScope) -> ProjectRecordRef:
        self._require_current_scope(scope)
        return self._repository.put_json(
            run=self._run,
            destination=self._branch_destination(scope),
            record_kind=f"branch-research-scope-{scope.scope_digest[:16]}",
            payload=scope.to_dict(),
        )

    def save_query(self, query: BranchPrecedentQuery) -> ProjectRecordRef:
        if not isinstance(query, BranchPrecedentQuery):
            raise TypeError("query must be BranchPrecedentQuery")
        self._require_persisted_scope(query.scope)
        return self._repository.put_json(
            run=self._run,
            destination=self._branch_destination(query.scope),
            record_kind=f"branch-query-{query.query_digest[:16]}",
            payload=query.to_dict(),
        )

    def save_snapshot(
        self,
        query: BranchPrecedentQuery,
        *,
        snapshot_id: str,
        requested_url: str,
        payload: Mapping[str, object],
    ) -> ProjectRecordRef:
        if not isinstance(query, BranchPrecedentQuery):
            raise TypeError("query must be BranchPrecedentQuery")
        self._require_persisted_scope(query.scope)
        if not isinstance(payload, Mapping):
            raise TypeError("snapshot payload must be a mapping")
        try:
            snapshot = bind_branch_snapshot(
                query,
                requested_url=requested_url,
                snapshot=payload,
            )
        except (TypeError, BranchResearchError) as exc:
            raise BranchResearchArchiveError(str(exc)) from exc
        return self._repository.put_json(
            run=self._run,
            destination=self._branch_destination(query.scope),
            record_kind=f"branch-snapshot-{snapshot_id}",
            payload=snapshot.to_dict(),
        )

    def save_adoption(
        self,
        scope: BranchResearchScope,
        adoption: BranchPrecedentAdoption,
    ) -> ProjectRecordRef:
        self._require_persisted_scope(scope)
        if not isinstance(adoption, BranchPrecedentAdoption):
            raise TypeError("adoption must be BranchPrecedentAdoption")
        if (
            adoption.scope_digest != scope.scope_digest
            or adoption.branch_id != scope.branch_id
            or adoption.branch_revision_digest != scope.branch_revision_digest
        ):
            raise BranchResearchArchiveError(
                "branch adoption crosses its research scope"
            )
        return self._repository.put_json(
            run=self._run,
            destination=self._branch_destination(scope),
            record_kind=(
                f"branch-adoption-{adoption.adoption.adoption_digest[:16]}"
            ),
            payload=adoption.to_dict(),
        )

    def save_index(self, index: BranchBasisIndex) -> ProjectRecordRef:
        if not isinstance(index, BranchBasisIndex):
            raise TypeError("index must be BranchBasisIndex")
        self._require_persisted_scope(index.scope)
        return self._repository.put_json(
            run=self._run,
            destination=self._branch_destination(index.scope),
            record_kind=f"branch-basis-{index.index_digest[:16]}",
            payload=index.to_dict(),
        )

    def load_index(
        self,
        ref: ProjectRecordRef,
        *,
        expected_scope_digest: str,
    ) -> BranchBasisIndex:
        """Reload and re-derive a current index from its retained branch records."""

        if not isinstance(ref, ProjectRecordRef):
            raise TypeError("ref must be ProjectRecordRef")
        try:
            index = BranchBasisIndex.from_dict(
                self._repository.load_json(ref)
            )
        except (ProjectRepositoryError, OSError, TypeError, ValueError) as exc:
            raise BranchResearchArchiveError(
                "branch basis record is missing, corrupt, or cross-scoped"
            ) from exc
        if index.scope.scope_digest != expected_scope_digest:
            raise BranchResearchArchiveError(
                "branch basis index belongs to another research scope"
            )
        prefix = (
            f"runs/{self._run.run_id}/branches/{index.scope.branch_id}/"
            "records/branch-basis-"
        )
        if (
            ref.project_id != self._run.project_id
            or not ref.relative_path.startswith(prefix)
        ):
            raise BranchResearchArchiveError(
                "branch basis ref is outside its P036 branch area"
            )
        self._require_persisted_scope(index.scope)
        rebuilt = build_branch_basis_index(
            self.records(index.scope),
            scope=index.scope,
        )
        if rebuilt != index:
            raise BranchResearchArchiveError(
                "persisted branch basis does not re-derive from retained records"
            )
        return index

    def load_decision_context(
        self,
        ref: ProjectRecordRef,
        *,
        expected_scope_digest: str,
        decision_refs: tuple[str, ...],
        universe: DecisionUniverseRevision,
        policy: EvidenceSufficiencyPolicy,
        closure: DecisionUniverseClosure,
        sufficiency: EvidenceSufficiencyReceipt,
        frontier: ResearchFrontier,
    ) -> BranchDecisionContext:
        """Build prompt context only after P036 reload and exact re-derivation."""

        index = self.load_index(
            ref,
            expected_scope_digest=expected_scope_digest,
        )
        return compile_branch_decision_context(
            index,
            index_record=ref,
            decision_refs=decision_refs,
            universe=universe,
            policy=policy,
            closure=closure,
            sufficiency=sufficiency,
            frontier=frontier,
        )

    def save_frontier(
        self,
        index: BranchBasisIndex,
        *,
        universe: DecisionUniverseRevision,
        policy: EvidenceSufficiencyPolicy,
        closure: DecisionUniverseClosure,
        sufficiency: EvidenceSufficiencyReceipt,
        frontier: ResearchFrontier,
    ) -> ProjectRecordRef:
        """Validate a typed P079 result before persisting it at branch scope."""

        require_branch_frontier_matches(
            index,
            universe=universe,
            policy=policy,
            closure=closure,
            sufficiency=sufficiency,
            frontier=frontier,
        )
        self._require_persisted_scope(index.scope)
        return self._repository.put_json(
            run=self._run,
            destination=self._branch_destination(index.scope),
            record_kind=f"research-frontier-{frontier.frontier_digest[:16]}",
            payload=frontier.to_dict(),
        )

    def save_progress(self, progress: BranchRAGProgress) -> ProjectRecordRef:
        if not isinstance(progress, BranchRAGProgress):
            raise TypeError("progress must be BranchRAGProgress")
        self._require_persisted_scope(progress.scope)
        return self._repository.put_json(
            run=self._run,
            destination=self._branch_destination(progress.scope),
            record_kind=f"branch-rag-progress-{progress.progress_digest[:16]}",
            payload=progress.to_dict(),
        )

    def advance_feedback_wave(
        self,
        scope: BranchResearchScope,
        *,
        needs: tuple[DecisionResearchNeed, ...],
        universe: DecisionUniverseRevision,
        policy: EvidenceSufficiencyPolicy,
        closure: DecisionUniverseClosure,
        sufficiency: EvidenceSufficiencyReceipt,
        frontier: ResearchFrontier,
    ) -> PersistedBranchResearchWave:
        """Reindex retained adoptions and persist the exact next query wave.

        Acquisition and adoption remain explicit boundaries: callers fetch only
        these saved queries, persist typed snapshots, and submit an authorised
        adoption before invoking the next wave.  This method owns the durable
        query -> reindex -> progress feedback loop through P036.
        """

        self._require_persisted_scope(scope)
        index = build_branch_basis_index(self.records(scope), scope=scope)
        require_branch_frontier_matches(
            index,
            universe=universe,
            policy=policy,
            closure=closure,
            sufficiency=sufficiency,
            frontier=frontier,
        )
        index_ref = self.save_index(index)
        frontier_ref = self.save_frontier(
            index,
            universe=universe,
            policy=policy,
            closure=closure,
            sufficiency=sufficiency,
            frontier=frontier,
        )
        next_queries = compile_next_branch_queries(index, needs=needs)
        query_refs = tuple(self.save_query(query) for query in next_queries)
        progress = compile_branch_rag_progress(
            index,
            next_queries=next_queries,
            universe=universe,
            policy=policy,
            closure=closure,
            sufficiency=sufficiency,
            frontier=frontier,
        )
        progress_ref = self.save_progress(progress)
        return PersistedBranchResearchWave(
            index=index,
            index_ref=index_ref,
            next_queries=next_queries,
            query_refs=query_refs,
            frontier=frontier,
            frontier_ref=frontier_ref,
            progress=progress,
            progress_ref=progress_ref,
        )

    def records(
        self,
        scope: BranchResearchScope,
    ) -> tuple[tuple[str, Mapping[str, object]], ...]:
        """Reload immutable records from exactly one run/branch directory."""

        self._require_persisted_scope(scope)
        destination = self._branch_destination(scope)
        rows = []
        for ref in self._repository.list_json(
            run=self._run,
            destination=destination,
        ):
            rows.append(
                (
                    ref.uri,
                    self._repository.load_json(ref),
                )
            )
        return tuple(sorted(rows, key=lambda item: item[0]))
