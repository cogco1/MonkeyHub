"""Compile one authorized site observation into SiteContext@1."""

from __future__ import annotations

from dataclasses import dataclass

from archive.archflow.adapters.site_observation import AuthorizedSiteObservation
from archive.archflow.state.design_brief import DesignBrief
from archflow.state.operational_state import DesignObligation
from archive.archflow.state.site_context import GroundModelKind, SiteApproachStatus, SiteContext, SiteUnknownTopic
from archflow.contracts.canonical import canonical_digest


_COMPILER_ID = "archflow.site-context-compiler"
_COMPILER_VERSION = "1"


class SiteCompilationError(ValueError):
    """Site evidence is stale, unauthorized, or from another design state."""


@dataclass(frozen=True, slots=True)
class SiteCompilationReceipt:
    compilation_id: str
    project_id: str
    run_id: str
    base_state_sha256: str
    brief_digest: str
    observation_digest: str
    world_id: str
    dimension_id: str
    context_digest: str
    open_obligation_ids: tuple[str, ...]

    SCHEMA = "SiteCompilationReceipt@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "compilation_id": self.compilation_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base_state_sha256": self.base_state_sha256,
            "brief_digest": self.brief_digest,
            "observation_digest": self.observation_digest,
            "world_id": self.world_id,
            "dimension_id": self.dimension_id,
            "context_digest": self.context_digest,
            "open_obligation_ids": list(self.open_obligation_ids),
            "generation_authority": False,
            "world_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class CompiledSiteContext:
    context: SiteContext
    receipt: SiteCompilationReceipt


def compile_site_context(
    *,
    brief: DesignBrief,
    observation: AuthorizedSiteObservation,
    compiler_id: str = _COMPILER_ID,
    compiler_version: str = _COMPILER_VERSION,
) -> CompiledSiteContext:
    """Preserve site facts and unknowns without selecting a response."""

    if not isinstance(brief, DesignBrief):
        raise TypeError("brief must be DesignBrief")
    if not isinstance(observation, AuthorizedSiteObservation):
        raise TypeError(
            "observation must be AuthorizedSiteObservation"
        )
    if (
        observation.project_id != brief.project_id
        or observation.run_id != brief.run_id
    ):
        raise SiteCompilationError(
            "site observation belongs to another project run"
        )
    if observation.base != brief.base:
        raise SiteCompilationError(
            "site observation and brief have different exact bases"
        )
    _text(compiler_id, "compiler_id")
    _text(compiler_version, "compiler_version")

    evidence_refs = tuple(
        sorted(
            {
                *brief.evidence_refs,
                *observation.source_refs,
                observation.authorization_ref,
                f"site-observation:{observation.observation_digest}",
            }
        )
    )
    obligations = _compile_obligations(observation)
    context = SiteContext(
        project_id=brief.project_id,
        run_id=brief.run_id,
        base=brief.base,
        brief_digest=brief.brief_digest,
        compiler_id=compiler_id,
        compiler_version=compiler_version,
        world_id=observation.world_id,
        dimension_id=observation.dimension_id,
        authorization_ref=observation.authorization_ref,
        authority_id=observation.authority_id,
        authorized_envelope=observation.authorized_envelope,
        observed_envelope=observation.observed_envelope,
        anchor=observation.anchor,
        approaches=observation.approaches,
        ground_model=observation.ground_model,
        protected_cells=observation.protected_cells,
        protection_source_refs=observation.protection_source_refs,
        observation_digest=observation.observation_digest,
        unknowns=observation.unknowns,
        obligations=obligations,
        evidence_refs=evidence_refs,
    )
    compilation_id = canonical_digest(
        {
            "project_id": brief.project_id,
            "run_id": brief.run_id,
            "base_state_sha256": brief.base.require_digest(),
            "brief_digest": brief.brief_digest,
            "observation_digest": observation.observation_digest,
            "compiler_id": compiler_id,
            "compiler_version": compiler_version,
        }
    )[:24]
    receipt = SiteCompilationReceipt(
        compilation_id=f"site-compilation.{compilation_id}",
        project_id=brief.project_id,
        run_id=brief.run_id,
        base_state_sha256=brief.base.require_digest(),
        brief_digest=brief.brief_digest,
        observation_digest=observation.observation_digest,
        world_id=observation.world_id,
        dimension_id=observation.dimension_id,
        context_digest=context.context_digest,
        open_obligation_ids=tuple(
            item.obligation_id for item in obligations
        ),
    )
    return CompiledSiteContext(context=context, receipt=receipt)


def _compile_obligations(
    observation: AuthorizedSiteObservation,
) -> tuple[DesignObligation, ...]:
    source_ref = f"site-observation:{observation.observation_digest}"
    obligations: dict[str, DesignObligation] = {}

    for unknown in observation.unknowns:
        obligation_id = f"resolve.site.{unknown.topic.value}.{unknown.unknown_id}"
        obligations[obligation_id] = DesignObligation(
            obligation_id=obligation_id,
            statement=unknown.statement,
            source_ref=source_ref,
            subject_refs=(
                f"site-unknown:{unknown.unknown_id}",
            ),
        )

    ground_kind = observation.ground_model.kind
    if ground_kind is GroundModelKind.UNKNOWN:
        _add(
            obligations,
            obligation_id="resolve.site.ground-evidence",
            statement=(
                "Obtain bounded ground evidence or preserve the ground "
                "condition as unresolved."
            ),
            source_ref=source_ref,
        )
    elif ground_kind is GroundModelKind.UNEVEN:
        _add(
            obligations,
            obligation_id="resolve.site.ground-response",
            statement=(
                "The Architect must resolve the uneven-ground relationship; "
                "the site compiler provides no design response."
            ),
            source_ref=source_ref,
        )

    if not observation.approaches:
        _add(
            obligations,
            obligation_id="resolve.site.approach-evidence",
            statement=(
                "Obtain an approach observation or preserve access as "
                "unresolved."
            ),
            source_ref=source_ref,
        )
    elif any(
        item.status is not SiteApproachStatus.OBSERVED
        for item in observation.approaches
    ):
        _add(
            obligations,
            obligation_id="resolve.site.approach-continuity",
            statement=(
                "Resolve incomplete approach continuity before relying on it "
                "for spatial design."
            ),
            source_ref=source_ref,
        )

    if observation.protected_cells:
        _add(
            obligations,
            obligation_id="preserve.site.protected-cells",
            statement=(
                "Keep observed protected cells outside later mutation plans "
                "unless their named authority revises the protection."
            ),
            source_ref=source_ref,
        )

    if (
        observation.observed_envelope
        != observation.authorized_envelope
        and not any(
            item.topic is SiteUnknownTopic.ENVELOPE
            for item in observation.unknowns
        )
    ):
        _add(
            obligations,
            obligation_id="resolve.site.observation-coverage",
            statement=(
                "Resolve the unobserved portion of the authorized envelope "
                "before treating it as known."
            ),
            source_ref=source_ref,
        )
    return tuple(
        obligations[key] for key in sorted(obligations)
    )


def _add(
    obligations: dict[str, DesignObligation],
    *,
    obligation_id: str,
    statement: str,
    source_ref: str,
) -> None:
    obligations.setdefault(
        obligation_id,
        DesignObligation(
            obligation_id=obligation_id,
            statement=statement,
            source_ref=source_ref,
        ),
    )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


__all__ = [
    "SiteCompilationError",
    "SiteCompilationReceipt",
    "CompiledSiteContext",
    "compile_site_context",
]

