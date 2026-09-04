from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archflow.project.repository import FilesystemProjectRepository
from archive.archflow.runtime.branch_portfolio import (
    BranchPortfolioArchive,
    BranchPortfolioArchiveError,
)
from archive.archflow.runtime.state_reducer import canonical_state_to_dict
from archflow.state.design_portfolio import DesignOptionPortfolio, park_branch, select_branch
from archflow.state.model import initialize_canonical_project
from tests.test_design_portfolio import (
    DECISION,
    EVIDENCE,
    PROJECT_ID,
    _portfolio,
)


_RETIRED_LANE_KINDS = (
    "a retired lane writes the record kinds this needs; put_json writes only "
    "kinds registered in archflow.project.record_kinds, and a kind no spine "
    "module writes, reads or names is not registered"
)


@unittest.skip(_RETIRED_LANE_KINDS)
class BranchPortfolioReloadTests(unittest.TestCase):
    def _repository(self, root: Path) -> FilesystemProjectRepository:
        canonical = initialize_canonical_project(PROJECT_ID)
        return FilesystemProjectRepository.initialize(
            root,
            project_id=PROJECT_ID,
            initial_state=canonical_state_to_dict(canonical),
        )

    def test_restart_loads_exact_latest_lineage_and_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / PROJECT_ID
            repository = self._repository(root)
            run = repository.create_run("run-001")
            archive = BranchPortfolioArchive(repository, run=run)
            portfolio = _portfolio(run)
            first = archive.save(portfolio)

            portfolio = park_branch(
                portfolio,
                expected_portfolio_digest=portfolio.portfolio_digest,
                branch_id="branch-b",
                authority_id="architect-lead",
                decision_ref=DECISION,
                rationale="Keep this option available but inactive.",
                evidence_refs=(EVIDENCE,),
                transition_id="park-b",
            )
            archive.save(portfolio)
            portfolio = select_branch(
                portfolio,
                expected_portfolio_digest=portfolio.portfolio_digest,
                branch_id="branch-a",
                authority_id="user-owner",
                decision_ref=DECISION,
                rationale="Explicitly select branch A for coordination.",
                evidence_refs=(EVIDENCE,),
                transition_id="select-a",
            )
            latest_record = archive.save(portfolio)

            del archive
            del repository
            reopened = FilesystemProjectRepository.open(root)
            durable_run = reopened.load_run("run-001")
            archive = BranchPortfolioArchive(reopened, run=durable_run)
            latest = archive.load_latest(
                portfolio_id="schematic-portfolio"
            )

            self.assertNotEqual(first.record_ref, latest.record_ref)
            self.assertEqual(latest.record_ref, latest_record.record_ref)
            self.assertEqual(latest.portfolio, portfolio)
            self.assertEqual(
                DesignOptionPortfolio.from_dict(
                    latest.portfolio.to_dict()
                ),
                portfolio,
            )
            self.assertEqual(
                latest.portfolio.selected_branch.branch_id,
                "branch-a",
            )
            self.assertEqual(reopened.verify().orphan_paths, ())

    def test_equal_depth_divergence_is_not_registration_order_winner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / PROJECT_ID
            repository = self._repository(root)
            run = repository.create_run("run-001")
            archive = BranchPortfolioArchive(repository, run=run)
            initial = _portfolio(run)
            for branch_id in ("branch-a", "branch-b"):
                divergent = park_branch(
                    initial,
                    expected_portfolio_digest=initial.portfolio_digest,
                    branch_id=branch_id,
                    authority_id="architect-lead",
                    decision_ref=DECISION,
                    rationale=f"Alternative lifecycle for {branch_id}.",
                    evidence_refs=(EVIDENCE,),
                    transition_id=f"park-{branch_id}",
                )
                archive.save(divergent)

            with self.assertRaisesRegex(
                BranchPortfolioArchiveError,
                "ambiguous",
            ):
                archive.load_latest(
                    portfolio_id="schematic-portfolio"
                )


if __name__ == "__main__":
    unittest.main()
