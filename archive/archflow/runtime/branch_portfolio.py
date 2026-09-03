"""Project-repository persistence for P033 design option portfolios."""

from __future__ import annotations

from dataclasses import dataclass

from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, RunRef
from archflow.state.design_portfolio import (
    DesignOptionPortfolio,
    DesignPortfolioError,
)


class BranchPortfolioArchiveError(DesignPortfolioError):
    """A persisted portfolio record is missing, ambiguous, or changed."""


@dataclass(frozen=True, slots=True)
class PersistedDesignPortfolio:
    record_ref: ProjectRecordRef
    portfolio: DesignOptionPortfolio

    def __post_init__(self) -> None:
        if not isinstance(self.record_ref, ProjectRecordRef):
            raise TypeError("record_ref must be ProjectRecordRef")
        if not isinstance(self.portfolio, DesignOptionPortfolio):
            raise TypeError("portfolio must be DesignOptionPortfolio")
        if self.record_ref.project_id != self.portfolio.project_id:
            raise BranchPortfolioArchiveError(
                "record and portfolio belong to different projects"
            )


class BranchPortfolioArchive:
    """Immutable snapshots routed through the generic project repository."""

    RECORD_SCHEMA = "DesignOptionPortfolioRecord@1"

    def __init__(
        self,
        repository: FilesystemProjectRepository,
        *,
        run: RunRef,
    ) -> None:
        if not isinstance(repository, FilesystemProjectRepository):
            raise TypeError(
                "repository must be FilesystemProjectRepository"
            )
        if not isinstance(run, RunRef):
            raise TypeError("run must be RunRef")
        if repository.load_run(run.run_id) != run:
            raise BranchPortfolioArchiveError(
                "archive run does not match project repository"
            )
        self._repository = repository
        self._run = run
        self._destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        )

    def _require_identity(
        self,
        portfolio: DesignOptionPortfolio,
    ) -> None:
        if not isinstance(portfolio, DesignOptionPortfolio):
            raise TypeError("portfolio must be DesignOptionPortfolio")
        if portfolio.run != self._run:
            raise BranchPortfolioArchiveError(
                "portfolio belongs to another exact-base run"
            )

    def save(
        self,
        portfolio: DesignOptionPortfolio,
    ) -> PersistedDesignPortfolio:
        self._require_identity(portfolio)
        payload = {
            "schema": self.RECORD_SCHEMA,
            "project_id": portfolio.project_id,
            "run_id": portfolio.run_id,
            "portfolio_id": portfolio.portfolio_id,
            "base": {
                "project_id": portfolio.base.project_id,
                "version": portfolio.base.version,
                "state_sha256": portfolio.base.require_digest(),
            },
            "transition_count": len(portfolio.transitions),
            "portfolio_digest": portfolio.portfolio_digest,
            "portfolio": portfolio.to_dict(),
        }
        ref = self._repository.put_json(
            run=self._run,
            destination=self._destination,
            record_kind=(
                "design-portfolio-"
                f"{portfolio.portfolio_id}-"
                f"{len(portfolio.transitions):06d}"
            ),
            payload=payload,
        )
        return PersistedDesignPortfolio(
            record_ref=ref,
            portfolio=portfolio,
        )

    def load(
        self,
        ref: ProjectRecordRef,
    ) -> PersistedDesignPortfolio:
        if not isinstance(ref, ProjectRecordRef):
            raise TypeError("ref must be ProjectRecordRef")
        if ref.project_id != self._run.project_id:
            raise BranchPortfolioArchiveError(
                "portfolio record belongs to another project"
            )
        payload = self._repository.load_json(ref)
        if set(payload) != {
            "schema",
            "project_id",
            "run_id",
            "portfolio_id",
            "base",
            "transition_count",
            "portfolio_digest",
            "portfolio",
        } or payload.get("schema") != self.RECORD_SCHEMA:
            raise BranchPortfolioArchiveError(
                "portfolio record schema drifted"
            )
        portfolio = DesignOptionPortfolio.from_dict(payload["portfolio"])
        self._require_identity(portfolio)
        if (
            payload["project_id"] != portfolio.project_id
            or payload["run_id"] != portfolio.run_id
            or payload["portfolio_id"] != portfolio.portfolio_id
            or payload["base"]
            != {
                "project_id": portfolio.base.project_id,
                "version": portfolio.base.version,
                "state_sha256": portfolio.base.require_digest(),
            }
            or payload["transition_count"]
            != len(portfolio.transitions)
            or payload["portfolio_digest"]
            != portfolio.portfolio_digest
        ):
            raise BranchPortfolioArchiveError(
                "portfolio record content or exact base changed"
            )
        return PersistedDesignPortfolio(
            record_ref=ref,
            portfolio=portfolio,
        )

    def load_latest(
        self,
        *,
        portfolio_id: str,
    ) -> PersistedDesignPortfolio:
        candidates: list[
            tuple[int, str, PersistedDesignPortfolio]
        ] = []
        for ref in self._repository.list_json(
            run=self._run,
            destination=self._destination,
        ):
            payload = self._repository.load_json(ref)
            if (
                payload.get("schema") != self.RECORD_SCHEMA
                or payload.get("portfolio_id") != portfolio_id
            ):
                continue
            loaded = self.load(ref)
            candidates.append(
                (
                    len(loaded.portfolio.transitions),
                    loaded.portfolio.portfolio_digest,
                    loaded,
                )
            )
        if not candidates:
            raise BranchPortfolioArchiveError(
                "no durable portfolio snapshot found"
            )
        maximum = max(item[0] for item in candidates)
        latest = tuple(item for item in candidates if item[0] == maximum)
        if len({item[1] for item in latest}) != 1:
            raise BranchPortfolioArchiveError(
                "latest portfolio lineage is ambiguous"
            )
        return latest[0][2]
