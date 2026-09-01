#!/usr/bin/env python3
"""P069: full-scale reconstruction reference derivation (workspace tenant).

Reuses the proven monument staging mechanics (provider loop,
exact-predecessor lifecycles, symmetry gate, persistence) by
parametrizing that module with the full-scale reconstruction target:
metre-cell footprint inside the 4096 bound, dimensions citing retained
adoptions, per-stage project-authored declaration gates, and records
living in the runtime workspace with an anchor left in the repo.

Every number below is instance data of this design project; values
marked with an adoption ref cite retained evidence, and deliberate
simplifications are listed in the retained deviations record instead of
being silently absorbed.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import hashlib
import json
import math
import struct
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, str(entry))

from _probe_paths import WORKSPACE_PROJECTS, resolve_probe_root  # noqa: E402

from tools.projects.pantheon import monument_support as M  # noqa: E402

from archflow.research.index import BranchDecisionContext  # noqa: E402
from archflow.capabilities.declaration import (  # noqa: E402
    DeclarationField,
    DeclarationKind,
    DeclarationQuadrant,
    GeometryCheck,
    StageDeclarationContract,
    validate_stage_declarations,
)
from archflow.project import (  # noqa: E402
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    RunRef,
)
from archflow.state import (  # noqa: E402
    ComponentMaturity,
    MassingVolume,
    ProgramMetricKind,
    ProgramNodeKind,
    SiteBounds,
    SpatialConnection,
    SpatialConstraintResponse,
    ConstraintResponseStatus,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialZone,
    DesignComponent,
    OperationalMarkovState,
)
from archflow.state.stage_convergence import (  # noqa: E402
    StageConvergenceReceipt,
)

PROJECT_ID = "pantheon-reconstruction"
RUN_ID = "reconstruction-002"
SOURCE_RUN_ID = "reconstruction-001"
EVIDENCE_PROBE = "p066-live-monument"
PROMPT = (
    "Reconstruct the full-scale domed rotunda with axial colonnaded "
    "portico at metre granularity, deepening the same components stage "
    "by stage under adopted-dimension declaration gates."
)

# --- full-scale constants (metres); adopted values cite refs below ---
CENTER_X = 28.0
CENTER_Z = 49.0
DRUM_OUTER = 28.0        # 43.3 interior + 2 x 6.35 measured wall thickness
DRUM_INNER = 21.65       # 43.3 m interior span (adopted)
FLOOR_Y = 1.0            # stylobate top
SPRING_Y = 22.6          # floor + interior radius
CROWN_Y = 44.3           # floor + adopted 43.3 interior height
OCULUS_R = 4.55          # 9.1 m structural ring (adopted)
PORTICO_W = 33.1         # adopted portico width
PORTICO_D = 15.0
TRANSITION_D = 5.0
COLUMN_SHAFT = 11.9      # adopted monolithic shaft height
CAPITAL_H = 2.4          # adopted capital height
COFFER_RINGS = 5
COFFER_COUNT = 28


@dataclass(frozen=True, slots=True)
class PantheonStagePlan:
    """Immutable component and revision inputs for one deepening stage."""

    stage: int
    components: tuple[tuple[str, str, str, str], ...]
    revisions: tuple[tuple[str, ComponentMaturity], ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.stage, int)
            or isinstance(self.stage, bool)
            or self.stage < 1
        ):
            raise ValueError("stage plan requires a positive integer stage")
        if not isinstance(self.components, tuple) or not self.components:
            raise ValueError("stage components must be a non-empty tuple")
        component_ids = tuple(item[0] for item in self.components)
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("stage components contain duplicate identities")
        if not isinstance(self.revisions, tuple) or not self.revisions:
            raise ValueError("stage revisions must be a non-empty tuple")
        revisions = tuple(sorted(self.revisions, key=lambda item: item[0]))
        if len(revisions) != len({item[0] for item in revisions}):
            raise ValueError("stage revisions contain duplicate identities")
        if any(
            not isinstance(maturity, ComponentMaturity)
            for _, maturity in revisions
        ):
            raise TypeError("stage revisions require ComponentMaturity values")
        object.__setattr__(self, "revisions", revisions)


@dataclass(frozen=True, slots=True)
class PantheonRunnerProfile:
    """Project-local immutable values injected into one runner execution."""

    project_id: str
    run_id: str
    source_run_id: str
    prompt: str
    center_x: float
    center_z: float
    drum_outer_radius: float
    drum_inner_radius: float
    floor_y: float
    spring_y: float
    crown_y: float
    oculus_radius: float
    portico_width: float
    portico_depth: float
    stage_plans: tuple[PantheonStagePlan, ...]

    def __post_init__(self) -> None:
        for field in ("project_id", "run_id", "source_run_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-empty text")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt must be non-empty text")
        for field in (
            "center_x",
            "center_z",
            "drum_outer_radius",
            "drum_inner_radius",
            "floor_y",
            "spring_y",
            "crown_y",
            "oculus_radius",
            "portico_width",
            "portico_depth",
        ):
            value = getattr(self, field)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{field} must be finite")
            object.__setattr__(self, field, float(value))
        if not isinstance(self.stage_plans, tuple) or any(
            not isinstance(item, PantheonStagePlan)
            for item in self.stage_plans
        ):
            raise TypeError("stage_plans must contain PantheonStagePlan values")
        plans = tuple(sorted(self.stage_plans, key=lambda item: item.stage))
        if tuple(item.stage for item in plans) != (1, 2, 3):
            raise ValueError("Pantheon runner requires exact stages 1, 2, and 3")
        object.__setattr__(self, "stage_plans", plans)

    def stage_plan(self, stage: int) -> PantheonStagePlan:
        for item in self.stage_plans:
            if item.stage == stage:
                return item
        raise KeyError(f"unknown Pantheon stage: {stage}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "PantheonRunnerProfile@1",
            "project_id": self.project_id,
            "run_id": self.run_id,
            "source_run_id": self.source_run_id,
            "prompt": self.prompt,
            "center_x": self.center_x,
            "center_z": self.center_z,
            "drum_outer_radius": self.drum_outer_radius,
            "drum_inner_radius": self.drum_inner_radius,
            "floor_y": self.floor_y,
            "spring_y": self.spring_y,
            "crown_y": self.crown_y,
            "oculus_radius": self.oculus_radius,
            "portico_width": self.portico_width,
            "portico_depth": self.portico_depth,
            "stage_plans": [
                {
                    "stage": item.stage,
                    "components": [list(value) for value in item.components],
                    "revisions": [
                        [component_id, maturity.value]
                        for component_id, maturity in item.revisions
                    ],
                }
                for item in self.stage_plans
            ],
        }

    @property
    def profile_digest(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PantheonRunnerHooks:
    """Explicit project-local functions consumed by the runner."""

    context: Callable[..., object]
    proposal: Callable[..., object]
    geometry: Callable[..., object]
    provider: Callable[..., object]
    persist: Callable[..., object]
    symmetry: Callable[..., object]

    def __post_init__(self) -> None:
        for field in (
            "context",
            "proposal",
            "geometry",
            "provider",
            "persist",
            "symmetry",
        ):
            if not callable(getattr(self, field)):
                raise TypeError(f"runner hook {field} must be callable")


@dataclass(frozen=True, slots=True)
class PantheonStageContractBinding:
    stage: int
    contract: StageDeclarationContract
    declared_values: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.contract, StageDeclarationContract):
            raise TypeError("contract must be a StageDeclarationContract")
        values = tuple(sorted(self.declared_values))
        if len(values) != len({key for key, _ in values}):
            raise ValueError("declared stage values contain duplicate fields")
        object.__setattr__(self, "declared_values", values)

    def as_pair(self) -> tuple[StageDeclarationContract, dict[str, float]]:
        return self.contract, dict(self.declared_values)


@dataclass(frozen=True, slots=True)
class PantheonRunnerContext:
    """One immutable, non-global execution binding."""

    profile: PantheonRunnerProfile
    hooks: PantheonRunnerHooks
    contracts: tuple[PantheonStageContractBinding, ...]
    stage_closure_resolver: Callable[..., object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.profile, PantheonRunnerProfile):
            raise TypeError("profile must be a PantheonRunnerProfile")
        if not isinstance(self.hooks, PantheonRunnerHooks):
            raise TypeError("hooks must be PantheonRunnerHooks")
        if not isinstance(self.contracts, tuple) or any(
            not isinstance(item, PantheonStageContractBinding)
            for item in self.contracts
        ):
            raise TypeError("contracts contain an invalid binding")
        contracts = tuple(sorted(self.contracts, key=lambda item: item.stage))
        if tuple(item.stage for item in contracts) != (0, 1, 2, 3):
            raise ValueError("runner context requires exact stage contracts 0-3")
        object.__setattr__(self, "contracts", contracts)
        if self.stage_closure_resolver is not None and not callable(
            self.stage_closure_resolver
        ):
            raise TypeError("stage_closure_resolver must be callable")

    def contract_for(
        self,
        stage: int,
    ) -> tuple[StageDeclarationContract, dict[str, float]]:
        for item in self.contracts:
            if item.stage == stage:
                return item.as_pair()
        raise KeyError(f"unknown Pantheon contract stage: {stage}")

    @property
    def support_context_digest(self) -> str:
        context = self.hooks.context(self.profile)
        encoded = json.dumps(
            context.to_dict(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


_PANTHEON_STAGE_PLANS = (
    PantheonStagePlan(
        stage=1,
        components=(
            (
                "colonnade", "portico", "portico-colonnade",
                "Resolve the adopted 8+4+4 portico column topology.",
            ),
            (
                "front-steps", "portico", "front-approach-steps",
                "Resolve the five-step axial approach to the portico.",
            ),
            (
                "main-entry", "rotunda", "axial-opening",
                "Cut the axial doorway through the drum.",
            ),
            (
                "oculus", "dome", "dome-oculus",
                "Open the adopted crown aperture.",
            ),
            (
                "dome-step-rings", "dome", "exterior-step-rings",
                "Build seven annular exterior rings above the drum.",
            ),
        ),
        revisions=(
            ("portico", ComponentMaturity.DEVELOPED),
            ("rotunda", ComponentMaturity.DEVELOPED),
            ("dome", ComponentMaturity.DEVELOPED),
        ),
    ),
    PantheonStagePlan(
        stage=2,
        components=(
            (
                "exedra-ring", "rotunda", "cardinal-exedras",
                "Cut the two lateral cardinal exedras.",
            ),
            (
                "apse", "rotunda", "rear-cardinal-apse",
                "Keep the rear cardinal exedra as a distinct apse.",
            ),
            (
                "niche-ring", "rotunda", "diagonal-niches",
                "Cut four diagonal quasi-rectangular niches.",
            ),
            (
                "aedicula-ring", "rotunda", "aedicula-ring",
                "Place eight aediculae in the intervening wall bays.",
            ),
        ),
        revisions=(("rotunda", ComponentMaturity.DETAILED),),
    ),
    PantheonStagePlan(
        stage=3,
        components=(
            (
                "coffers", "dome", "radial-coffer-field",
                "Cut five diminishing rings of 28 dome coffers.",
            ),
        ),
        revisions=(("dome", ComponentMaturity.DETAILED),),
    ),
)

DEFAULT_RUNNER_PROFILE = PantheonRunnerProfile(
    project_id=PROJECT_ID,
    run_id=RUN_ID,
    source_run_id=SOURCE_RUN_ID,
    prompt=PROMPT,
    center_x=CENTER_X,
    center_z=CENTER_Z,
    drum_outer_radius=DRUM_OUTER,
    drum_inner_radius=DRUM_INNER,
    floor_y=FLOOR_Y,
    spring_y=SPRING_Y,
    crown_y=CROWN_Y,
    oculus_radius=OCULUS_R,
    portico_width=PORTICO_W,
    portico_depth=PORTICO_D,
    stage_plans=_PANTHEON_STAGE_PLANS,
)

# Stage 3D is a non-canonical visual/detail successor.  It deliberately keeps
# the accepted Stage-3 massing envelope and column centres unchanged while
# replacing the coarse column proxies in the Rhino view with a measured-order
# assembly.  Values that are not yet user-ratified remain typed candidate
# ranges in the retained detail plan below.
DETAIL_HISTORICAL_STATE = "second_century_reconstructed"
DETAIL_BASE_H = 0.73
DETAIL_SHAFT_H = 11.90
DETAIL_TOTAL_COLUMN_H = COLUMN_SHAFT + CAPITAL_H
DETAIL_CAPITAL_H = DETAIL_TOTAL_COLUMN_H - DETAIL_BASE_H - DETAIL_SHAFT_H
DETAIL_INSCRIPTION = "M\u00b7AGRIPPA\u00b7L\u00b7F\u00b7COS\u00b7TERTIVM\u00b7FECIT"
DETAIL_SOURCES = {
    "official_history": (
        "https://direzionemuseiroma.cultura.gov.it/pantheon/"
        "cenni-storici/"
    ),
    "column_scan": (
        "https://www.topoi.org/wp-content/uploads/2014/07/"
        "Decoding-the-Pantheon-Columns.pdf"
    ),
    "capital_drawing": (
        "https://www.metmuseum.org/art/collection/search/362562"
    ),
    "door_drawing": (
        "https://www.metmuseum.org/art/collection/search/362561"
    ),
    "inscription": "https://cil.bbaw.de/ace/id/KO0000284",
}


def _adoption_uri(
    pattern: str,
    *,
    project_id: str = PROJECT_ID,
) -> str:
    """Prefer in-project evidence; fall back to the source probe.

    The fallback exists only for the first bootstrap, before the basis
    slice has been imported; a self-contained project resolves every
    ref inside its own records.
    """

    for candidate_project_id in (project_id, EVIDENCE_PROBE):
        try:
            base = resolve_probe_root(candidate_project_id) / "runs"
        except FileNotFoundError:
            continue
        hits = sorted(glob.glob(str(base / "*" / "records" / pattern)))
        if hits:
            hit = Path(hits[-1])
            return (
                f"project://{candidate_project_id}/runs/{hit.parents[1].name}"
                f"/records/{hit.name}"
            )
    raise SystemExit(f"no adoption record matches {pattern}")


def adoption_refs(
    project_id: str = PROJECT_ID,
) -> dict[str, str]:
    return {
        "walls": _adoption_uri(
            "precedent-adoption-rotunda-wall-construction-*.json",
            project_id=project_id,
        ),
        "orders": _adoption_uri(
            "precedent-adoption-corinthian-order-proportions-*.json",
            project_id=project_id,
        ),
        "front": _adoption_uri(
            "precedent-adoption-front-elevation-governing-rules-*.json",
            project_id=project_id,
        ),
        "typology": _adoption_uri(
            "precedent-adoption-????????*.json",
            project_id=project_id,
        ),
        "junction": _adoption_uri(
            "precedent-adoption-portico-rotunda-junction-*.json",
            project_id=project_id,
        ),
    }


# --------------------------------------------------------------------
# Context: full-scale program, site, commitments (reuses the monument
# context builder and rescales it).
# --------------------------------------------------------------------

def _pantheon_context(
    profile: PantheonRunnerProfile | None = None,
):
    profile = DEFAULT_RUNNER_PROFILE if profile is None else profile
    if not isinstance(profile, PantheonRunnerProfile):
        raise TypeError("profile must be a PantheonRunnerProfile")
    context = M._monument_context(
        project_id=profile.project_id,
        center_x=profile.center_x,
        center_z=profile.center_z,
    )
    program = replace(
        context.program,
        ranges=tuple(
            replace(
                item,
                minimum=(
                    3_000.0
                    if item.metric is ProgramMetricKind.FOOTPRINT
                    else item.minimum
                ),
                maximum=(
                    4_100.0
                    if item.metric is ProgramMetricKind.FOOTPRINT
                    else (
                        15_000.0
                        if item.metric
                        in {
                            ProgramMetricKind.NET_AREA,
                            ProgramMetricKind.TOTAL_FLOOR_AREA,
                        }
                        else item.maximum
                    )
                ),
            )
            for item in context.program.ranges
        ),
    )
    maximum_x = int(
        round(profile.center_x + profile.drum_outer_radius + 1.0)
    )
    maximum_z = int(
        round(profile.center_z + profile.drum_outer_radius + 1.0)
    )
    anchor_x = int(round(profile.center_x))
    anchor_z = int(round(profile.center_z))
    envelope = SiteBounds(
        minimum=(0, 0, 0),
        maximum=(maximum_x, 48, maximum_z),
    )
    ground = replace(
        context.site_context.ground_model,
        samples=tuple(
            replace(sample, coordinate=coordinate)
            for sample, coordinate in zip(
                context.site_context.ground_model.samples,
                (
                    (0, 0, 0),
                    (maximum_x, 0, 0),
                    (0, 0, maximum_z),
                    (maximum_x, 0, maximum_z),
                ),
                strict=True,
            )
        ),
    )
    site = replace(
        context.site_context,
        authorized_envelope=envelope,
        observed_envelope=envelope,
        anchor=(anchor_x, 1, anchor_z),
        ground_model=ground,
        approaches=tuple(
            replace(item, cells=((anchor_x, 1, 0),))
            for item in context.site_context.approaches
        ),
    )
    policy = replace(
        context.build_policy,
        program_digest=program.program_digest,
        site_context_digest=site.context_digest,
    )
    return replace(
        context,
        program=program,
        site_context=site,
        build_policy=policy,
    )


# --------------------------------------------------------------------
# Schematic proposal: metre-cell footprint 3,963 cells < 4,096.
# --------------------------------------------------------------------

def _pantheon_proposal(
    context,
    *,
    option_id,
    radius,
    profile: PantheonRunnerProfile | None = None,
):
    profile = DEFAULT_RUNNER_PROFILE if profile is None else profile
    if not isinstance(profile, PantheonRunnerProfile):
        raise TypeError("profile must be a PantheonRunnerProfile")
    evidence = (context.program.evidence_refs[0],)
    cx, cz = int(round(profile.center_x)), int(round(profile.center_z))
    portico_min_x = math.ceil(profile.center_x - profile.portico_width / 2.0)
    portico_max_x = round(profile.center_x + profile.portico_width / 2.0)
    footprint = tuple(
        sorted(
            {
                *(
                    (x, z)
                    for x in range(cx - radius, cx + radius + 1)
                    for z in range(cz - radius, cz + radius + 1)
                ),
                # Carry the vestibule footprint into the circular hall.
                # The overlap keeps the primary option at 3,963 cells and
                # also makes the smaller alternative topologically valid.
                *(
                    (x, z)
                    for x in range(portico_min_x, portico_max_x + 1)
                    for z in range(0, 23)
                ),
            }
        )
    )
    levels = (
        SpatialLevel("dome", 22, 25, evidence),
        SpatialLevel("ground", 0, 23, evidence),
        SpatialLevel("portico", 0, 27, evidence),
    )
    volumes = (
        MassingVolume(
            "dome-volume",
            SiteBounds(
                (cx - radius, 22, cz - radius),
                (cx + radius, 47, cz + radius),
            ),
            ("dome",),
            evidence,
        ),
        MassingVolume(
            "portico-volume",
            SiteBounds(
                (portico_min_x, 0, 0),
                (portico_max_x, 27, int(round(profile.portico_depth))),
            ),
            ("portico",),
            evidence,
        ),
        MassingVolume(
            "transition-volume",
            SiteBounds(
                (portico_min_x, 0, int(round(profile.portico_depth))),
                (portico_max_x, 27, 27),
            ),
            ("portico",),
            evidence,
        ),
        MassingVolume(
            "rotunda-volume",
            SiteBounds(
                (cx - radius, 0, cz - radius),
                (cx + radius, 22, cz + radius),
            ),
            ("ground",),
            evidence,
        ),
    )
    functions = tuple(
        item
        for item in context.program.nodes
        if item.kind is ProgramNodeKind.FUNCTION
    )
    zone_specs = (
        ("rotunda-zone", functions[0].ref, "ground", "rotunda-volume"),
        ("portico-zone", functions[1].ref, "portico", "portico-volume", "transition-volume"),
        ("dome-zone", functions[2].ref, "dome", "dome-volume"),
    )
    zones = tuple(
        SpatialZone(
            zone_id=spec[0],
            program_node_refs=(spec[1],),
            level_ids=(spec[2],),
            volume_ids=tuple(spec[3:]),
            source_refs=evidence,
        )
        for spec in zone_specs
    )
    zone_by_node = {
        zone.program_node_refs[0]: zone.zone_id for zone in zones
    }
    connections = tuple(
        SpatialConnection(
            connection_id=f"connection-{index}",
            source_zone_id=zone_by_node[item.source_node_ref],
            target_zone_id=zone_by_node[item.target_node_ref],
            relationship_refs=(item.ref,),
            directed=item.directed,
            source_refs=evidence,
        )
        for index, item in enumerate(
            context.program.relationships, start=1
        )
    )
    responses = tuple(
        SpatialConstraintResponse(
            response_id=f"response-{index}",
            constraint_ref=item.ref,
            status=ConstraintResponseStatus.ACKNOWLEDGED,
            rationale="The unresolved discipline risk remains explicit.",
            source_refs=evidence,
        )
        for index, item in enumerate(
            context.build_policy.constraints, start=1
        )
    )
    components = (
        DesignComponent(
            component_id="building",
            parent_component_id=None,
            semantic_kind="monumental-domed-hall",
            intent="Own the reconstruction composition.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=(),
            unresolved_child_roles=(),
            source_refs=evidence,
        ),
        DesignComponent(
            component_id="dome",
            parent_component_id="building",
            semantic_kind="hemispherical-dome",
            intent="Span the rotunda at the adopted interior height.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("dome-volume",),
            unresolved_child_roles=("oculus", "dome-step-rings", "coffers"),
            source_refs=evidence,
        ),
        DesignComponent(
            component_id="portico",
            parent_component_id="building",
            semantic_kind="axial-colonnaded-portico",
            intent="Front the rotunda with the adopted octastyle porch.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("portico-volume",),
            unresolved_child_roles=("colonnade",),
            source_refs=evidence,
        ),
        DesignComponent(
            component_id="transition",
            parent_component_id="building",
            semantic_kind="entrance-vestibule-block",
            intent="Link the porch to the rotunda with the adopted "
                   "rectangular vestibule block.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("transition-volume",),
            unresolved_child_roles=(),
            source_refs=evidence,
        ),
        DesignComponent(
            component_id="rotunda",
            parent_component_id="building",
            semantic_kind="cylindrical-rotunda",
            intent="Hold the adopted clear span inside the drum wall.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("rotunda-volume",),
            unresolved_child_roles=(
                "main-entry",
                "exedra-ring",
                "apse",
                "niche-ring",
                "aedicula-ring",
            ),
            source_refs=evidence,
        ),
    )
    footprint_range = next(
        item.ref
        for item in context.program.ranges
        if item.metric is ProgramMetricKind.FOOTPRINT
    )
    return SpatialOptionProposal(
        option_id=option_id,
        label=f"Reconstruction drum radius {radius}",
        program_scenario_ref=None,
        footprint_range_ref=footprint_range,
        grid_basis=SpatialGridBasis(1.0, "square_cells", evidence),
        footprint_cells=footprint,
        levels=levels,
        volumes=volumes,
        zones=zones,
        components=components,
        connections=connections,
        constraint_responses=responses,
        typology_hypothesis=(
            "Full-scale domed rotunda with an axial colonnaded portico "
            "and transition block at the adopted dimensions."
        ),
        palette_refs=("material-intent:opus-caementicium",),
        rationale=(
            "Only the adopted 56.2 m drum with the axial approach "
            "honors the reconstruction contract inside the envelope."
        ),
        responds_to_refs=tuple(
            sorted(
                {
                    *[item.ref for item in context.program.relationships],
                    *[item.ref for item in context.build_policy.constraints],
                }
            )
        ),
        expert_advice_refs=(),
        evidence_refs=evidence,
    )


class _PantheonScriptedProvider(M._MonumentScriptedProvider):
    def __init__(
        self,
        context,
        runner_context: PantheonRunnerContext | None = None,
    ) -> None:
        super().__init__(context)
        self.runner_context = (
            create_runner_context()
            if runner_context is None
            else runner_context
        )
        if not isinstance(self.runner_context, PantheonRunnerContext):
            raise TypeError("runner_context must be a PantheonRunnerContext")

    async def invoke(self, request):
        self.calls.append(request)
        profile = self.runner_context.profile
        schema = request.payload["schema"]
        if schema == "SemanticSpatialAuthoringPrompt@1":
            is_alternative = "alternative_context" in request.payload
            radius = 27 if is_alternative else 28
            option_id = (
                "reconstruction-compact"
                if is_alternative
                else "reconstruction-primary"
            )
            output = M.semantic_spatial_authoring_output(
                request,
                self.runner_context.hooks.proposal(
                    self.context,
                    option_id=option_id,
                    radius=radius,
                    profile=profile,
                ),
            )
        elif schema == "SchematicOptionSelectionPrompt@1":
            output = M.schematic_selection_output(
                request,
                selected_option_id="reconstruction-primary",
                rationale=(
                    "Only the full drum honors the realized 56.0 m outer "
                    "diameter inside the authorized envelope."
                ),
            )
        elif schema == "GeometryProposalAuthoringRequest@1":
            state = M.DevelopedDesignState.from_dict(
                request.payload["developed_design_state"]
            )
            spatial = request.payload["spatial_option_record"]["ref"]
            proposal = self.runner_context.hooks.geometry(
                state,
                stage=0,
                profile=profile,
            )
            spatial_uri = (
                f"project://{spatial['project_id']}/"
                f"{spatial['relative_path']}"
            )
            self.generated_geometry = replace(
                proposal,
                semantic_bindings=tuple(
                    replace(
                        item,
                        evidence_refs=tuple(
                            sorted({*item.evidence_refs, spatial_uri})
                        ),
                    )
                    for item in proposal.semantic_bindings
                ),
            )
            output = M.proposal_authoring_output(self.generated_geometry)
        else:
            raise AssertionError(f"unexpected request schema: {schema}")
        encoded = M._canonical(output)
        return M.ModelInvocationReceipt(
            receipt_id=f"scripted-reconstruction-{len(self.calls):02d}",
            status=M.ModelInvocationStatus.SUCCESS,
            request=request,
            provider_id=M.IDENTITY.provider_id,
            model_id=M.IDENTITY.model_id,
            provider_version=M.IDENTITY.provider_version,
            provider_fingerprint=M.IDENTITY.provider_fingerprint,
            input_bytes=len(request.payload_json.encode("utf-8")),
            output_bytes=len(encoded.encode("utf-8")),
            output_sha256=hashlib.sha256(
                encoded.encode("utf-8")
            ).hexdigest(),
            output_json=encoded,
        )


# --------------------------------------------------------------------
# Geometry per stage (full scale, metres).  The building-specific
# topology remains here rather than becoming a framework default:
# entry/8+4+4 portico/oculus at stage 1, the typed lower-wall bays at
# stage 2, and the five evidenced coffer rings at stage 3.
# --------------------------------------------------------------------

def _row_origin(count, step, width):
    return CENTER_X - ((count - 1) * step + width) / 2.0


def _extrusion(op_id, output, binding, profile, vector):
    """True prism: planar profile swept along a vector (live-002 proven)."""

    return M._operation(
        op_id,
        M.GeometryOperationKind.EXTRUSION,
        output,
        binding,
        (
            M._parameter(
                "profile",
                M.GeometryParameterKind.POINTS3,
                [[float(c) for c in point] for point in profile],
                unit=M.LengthUnit.METER,
            ),
            M._parameter(
                "vector",
                M.GeometryParameterKind.VECTOR3,
                [float(c) for c in vector],
                unit=M.LengthUnit.METER,
            ),
        ),
    )


def _pantheon_geometry(
    state,
    *,
    stage,
    prior=None,
    profile: PantheonRunnerProfile | None = None,
):
    profile = DEFAULT_RUNNER_PROFILE if profile is None else profile
    if not isinstance(profile, PantheonRunnerProfile):
        raise TypeError("profile must be a PantheonRunnerProfile")
    CENTER_X = profile.center_x
    CENTER_Z = profile.center_z
    DRUM_OUTER = profile.drum_outer_radius
    DRUM_INNER = profile.drum_inner_radius
    FLOOR_Y = profile.floor_y
    SPRING_Y = profile.spring_y
    CROWN_Y = profile.crown_y
    OCULUS_R = profile.oculus_radius
    PORTICO_W = profile.portico_width
    evidence = (
        state.selected_schematic.option.proposal.evidence_refs[0],
    )
    operations = [
        M._solid("plinth", "plinth-object", "rotunda-binding",
                 [0.0, 0.0, 0.5], [56.0, FLOOR_Y, 77.0]),
        M._cylinder("drum-outer", "drum-outer-object", "rotunda-binding",
                    FLOOR_Y, SPRING_Y, DRUM_OUTER,
                    center_x=CENTER_X, center_z=CENTER_Z),
        M._cylinder("drum-inner", "drum-inner-object", "rotunda-binding",
                    FLOOR_Y, SPRING_Y + 0.5, DRUM_INNER,
                    center_x=CENTER_X, center_z=CENTER_Z),
        M._loft(
            "dome-outer",
            "dome-outer-object",
            "dome-binding",
            M._dome_sections(DRUM_OUTER, opening_radius=OCULUS_R,
                             base_y=SPRING_Y, rise=23.5,
                             center_x=CENTER_X, center_z=CENTER_Z),
        ),
        M._loft(
            "dome-inner",
            "dome-inner-object",
            "dome-binding",
            M._dome_sections(DRUM_INNER, opening_radius=OCULUS_R - 0.15,
                             base_y=SPRING_Y, rise=CROWN_Y - SPRING_Y,
                             center_x=CENTER_X, center_z=CENTER_Z),
        ),
    ]
    operations.extend(
        (
            M._solid("transition-box", "transition-box-object",
                     "transition-binding",
                     [CENTER_X - 16.75, 0.0, 15.0],
                     [33.5, 26.0, 12.0]),
            M._cylinder("transition-hug", "transition-hug-object",
                        "transition-binding", -0.5, 27.5, DRUM_OUTER,
                        center_x=CENTER_X, center_z=CENTER_Z),
            M._solid("transition-door", "transition-door-object",
                     "transition-binding",
                     [CENTER_X - 2.225, FLOOR_Y, 14.0],
                     [4.45, 7.53, 8.0]),
            M._difference(
                "transition-block", "transition-block-object",
                "transition-binding",
                ("transition-box-object", "transition-door-object",
                 "transition-hug-object"),
                "transition-box-object",
            ),
        )
    )
    bindings = {
        "transition-binding": (
            "transition",
            {
                "transition-box-object",
                "transition-hug-object",
                "transition-door-object",
                "transition-block-object",
            },
        ),
        "rotunda-binding": (
            "rotunda",
            {
                "plinth-object",
                "drum-outer-object",
                "drum-inner-object",
                "drum-wall-object",
            },
        ),
        "dome-binding": (
            "dome",
            {"dome-outer-object", "dome-inner-object",
             "dome-shell-object"},
        ),
        "portico-binding": ("portico", {"portico-mass-object"}),
    }
    drum_cut_inputs = {"drum-inner-object", "drum-outer-object"}
    dome_cut_inputs = {"dome-inner-object", "dome-outer-object"}
    if stage == 0:
        operations.append(
            M._solid("portico-mass", "portico-mass-object",
                     "portico-binding",
                     [CENTER_X - PORTICO_W / 2.0, 0.0, 0.0],
                     [PORTICO_W, 18.0, 15.0])
        )
    if stage >= 1:
        portico_floor_y = FLOOR_Y + 1.0
        entablature_y = portico_floor_y + COLUMN_SHAFT + CAPITAL_H
        operations.extend(
            (
                M._solid("portico-floor", "portico-floor-object",
                         "portico-binding",
                         [CENTER_X - PORTICO_W / 2.0, FLOOR_Y, 3.0],
                         [PORTICO_W, 1.0, 12.0]),
                M._solid("portico-mass", "portico-mass-object",
                         "portico-binding",
                         [CENTER_X - PORTICO_W / 2.0, entablature_y, 0.0],
                         [PORTICO_W, 2.7, 15.0]),
                _extrusion("pediment", "pediment-object",
                           "portico-binding",
                           [[CENTER_X - 15.75, entablature_y + 2.7, 0.0],
                            [CENTER_X + 15.75, entablature_y + 2.7, 0.0],
                            [CENTER_X, entablature_y + 9.7, 0.0]],
                           [0.0, 0.0, 15.0]),
                M._solid("door-tool", "door-tool-object", "entry-binding",
                         [CENTER_X - 2.225, FLOOR_Y, 19.0],
                         [4.45, 7.53, 9.0]),
                M._cylinder("oculus-tool", "oculus-tool-object",
                            "oculus-binding", CROWN_Y - 2.0,
                            CROWN_Y + 3.5, OCULUS_R,
                            center_x=CENTER_X, center_z=CENTER_Z),
            )
        )
        front_step_objects: set[str] = set()
        for index in range(5):
            op_id = f"front-step-{index}"
            operations.append(
                M._solid(
                    op_id,
                    f"{op_id}-object",
                    "front-step-binding",
                    [CENTER_X - PORTICO_W / 2.0,
                     FLOOR_Y, index * 0.6],
                    [PORTICO_W, 0.2 * (index + 1), 0.6],
                )
            )
            front_step_objects.add(f"{op_id}-object")
        # The adopted portico topology is 8 + 4 + 4.  The rear two
        # rows retain a central access aisle instead of repeating an
        # eight-column row.  Every shaft is a tapered vertical revolve,
        # so the realized geometry is no longer a square-box proxy.
        column_objects: set[str] = set()
        row_x = (
            tuple(CENTER_X + (index - 3.5) * 4.4 for index in range(8)),
            tuple(CENTER_X + offset for offset in (-11.0, -6.6, 6.6, 11.0)),
            tuple(CENTER_X + offset for offset in (-11.0, -6.6, 6.6, 11.0)),
        )
        for row, (z, centres) in enumerate(zip((4.0, 8.5, 13.0), row_x)):
            for column, x in enumerate(centres):
                shaft_id = f"column-shaft-r{row}-c{column}"
                capital_id = f"column-capital-r{row}-c{column}"
                operations.extend(
                    (
                        _vertical_revolve(
                            shaft_id,
                            f"{shaft_id}-object",
                            "colonnade-binding",
                            x=x,
                            z=z,
                            y0=portico_floor_y,
                            y1=portico_floor_y + COLUMN_SHAFT,
                            start_radius=0.75,
                            end_radius=0.64,
                        ),
                        _vertical_revolve(
                            capital_id,
                            f"{capital_id}-object",
                            "colonnade-binding",
                            x=x,
                            z=z,
                            y0=portico_floor_y + COLUMN_SHAFT,
                            y1=portico_floor_y + COLUMN_SHAFT + CAPITAL_H,
                            start_radius=1.05,
                            end_radius=1.05,
                        ),
                    )
                )
                column_objects.update(
                    {f"{shaft_id}-object", f"{capital_id}-object"}
                )

        step_objects: set[str] = set()
        for ring in range(7):
            y0 = SPRING_Y + ring
            outer_radius = 27.4 - 0.6 * ring
            inner_radius = outer_radius - 0.65
            outer_id = f"dome-step-outer-{ring}"
            inner_id = f"dome-step-inner-{ring}"
            result_id = f"dome-step-ring-{ring}"
            operations.extend(
                (
                    M._cylinder(
                        outer_id, f"{outer_id}-object",
                        "dome-step-binding", y0, y0 + 1.0,
                        outer_radius,
                        center_x=CENTER_X, center_z=CENTER_Z,
                    ),
                    M._cylinder(
                        inner_id, f"{inner_id}-object",
                        "dome-step-binding", y0 - 0.05, y0 + 1.05,
                        inner_radius,
                        center_x=CENTER_X, center_z=CENTER_Z,
                    ),
                    M._difference(
                        result_id,
                        f"{result_id}-object",
                        "dome-step-binding",
                        (f"{outer_id}-object", f"{inner_id}-object"),
                        f"{outer_id}-object",
                    ),
                )
            )
            step_objects.update(
                {
                    f"{outer_id}-object",
                    f"{inner_id}-object",
                    f"{result_id}-object",
                }
            )
        bindings["colonnade-binding"] = (
            "colonnade", column_objects,
        )
        bindings["portico-binding"][1].update(
            {"pediment-object", "portico-floor-object"}
        )
        bindings["front-step-binding"] = (
            "front-steps", front_step_objects,
        )
        bindings["entry-binding"] = ("main-entry", {"door-tool-object"})
        bindings["oculus-binding"] = ("oculus", {"oculus-tool-object"})
        bindings["dome-step-binding"] = ("dome-step-rings", step_objects)
        drum_cut_inputs.add("door-tool-object")
        dome_cut_inputs.add("oculus-tool-object")
    if stage >= 2:
        exedra_objects: set[str] = set()
        apse_objects: set[str] = set()
        for direction, angle in (("east", 0.0), ("rear", 90.0),
                                 ("west", 180.0)):
            radians = math.radians(angle)
            op_id = f"exedra-cutter-{direction}"
            output = f"{op_id}-object"
            operations.append(
                _vertical_revolve(
                    op_id,
                    output,
                    "apse-binding" if direction == "rear" else
                    "exedra-binding",
                    x=CENTER_X + 23.6 * math.cos(radians),
                    z=CENTER_Z + 23.6 * math.sin(radians),
                    y0=FLOOR_Y,
                    y1=FLOOR_Y + 9.0,
                    start_radius=4.5,
                )
            )
            drum_cut_inputs.add(output)
            if direction == "rear":
                apse_objects.add(output)
            else:
                exedra_objects.add(output)

        niche_objects: set[str] = set()
        for direction, angle in (
            ("ne", -45.0), ("se", 45.0),
            ("sw", 135.0), ("nw", 225.0),
        ):
            op_id = f"niche-cutter-{direction}"
            output = f"{op_id}-object"
            operations.append(
                _oriented_prism(
                    op_id,
                    output,
                    "niche-binding",
                    center_x=CENTER_X,
                    center_z=CENTER_Z,
                    angle_degrees=angle,
                    radius=23.6,
                    width=4.5,
                    depth=7.0,
                    y0=FLOOR_Y,
                    height=8.0,
                )
            )
            niche_objects.add(output)
            drum_cut_inputs.add(output)

        aedicula_objects: set[str] = set()
        for index in range(8):
            op_id = f"aedicula-{index}"
            output = f"{op_id}-object"
            operations.append(
                _oriented_prism(
                    op_id,
                    output,
                    "aedicula-binding",
                    center_x=CENTER_X,
                    center_z=CENTER_Z,
                    angle_degrees=-67.5 + index * 45.0,
                    radius=20.8,
                    width=2.5,
                    depth=0.8,
                    y0=10.5,
                    height=4.0,
                )
            )
            aedicula_objects.add(output)

        bindings["exedra-binding"] = ("exedra-ring", exedra_objects)
        bindings["apse-binding"] = ("apse", apse_objects)
        bindings["niche-binding"] = ("niche-ring", niche_objects)
        bindings["aedicula-binding"] = (
            "aedicula-ring", aedicula_objects,
        )
    if stage >= 3:
        coffer_objects: set[str] = set()
        for ring in range(COFFER_RINGS):
            for column in range(COFFER_COUNT):
                op_id = f"coffer-cutter-r{ring}-c{column}"
                output = f"{op_id}-object"
                operations.append(
                    _coffer_cutter(
                        op_id,
                        output,
                        ring=ring,
                        column=column,
                        center_x=CENTER_X,
                        center_z=CENTER_Z,
                        drum_inner_radius=DRUM_INNER,
                        spring_y=SPRING_Y,
                    )
                )
                coffer_objects.add(output)
                dome_cut_inputs.add(output)
        bindings["coffer-binding"] = ("coffers", coffer_objects)
    operations.append(
        M._difference(
            "drum-wall", "drum-wall-object", "rotunda-binding",
            tuple(sorted({*drum_cut_inputs})), "drum-outer-object",
        )
    )
    operations.append(
        M._difference(
            "dome-shell", "dome-shell-object", "dome-binding",
            tuple(sorted({*dome_cut_inputs})), "dome-outer-object",
        )
    )
    prior_binding_evidence = (
        {}
        if prior is None
        else {
            item.component_id: item.evidence_refs
            for item in prior.proposal.semantic_bindings
        }
    )
    semantic_bindings = tuple(
        M.SemanticBinding(
            binding_id=binding_id,
            component_id=component_id,
            object_ids=tuple(sorted(object_ids)),
            commitment_refs=(M.COMMITMENT_REF, M.AXIS_COMMITMENT_REF),
            evidence_refs=prior_binding_evidence.get(
                component_id, evidence
            ),
        )
        for binding_id, (component_id, object_ids) in sorted(
            bindings.items()
        )
    )
    revisions = ()
    if prior is not None:
        cumulative_revised = {
            component_id
            for prior_stage in range(1, stage + 1)
            for component_id, _ in profile.stage_plan(
                prior_stage
            ).revisions
        }
        flagged_bindings = {
            binding_id
            for binding_id, (component_id, _) in bindings.items()
            if component_id in cumulative_revised
        }
        revised_components = {
            component_id
            for component_id, _ in profile.stage_plan(stage).revisions
        }
        revised_bindings = {
            binding_id
            for binding_id, (component_id, _) in bindings.items()
            if component_id in revised_components
        }
        operations = [
            replace(
                op,
                responds_to_binding_ids=tuple(
                    sorted(
                        {
                            *op.responds_to_binding_ids,
                            *(
                                set(op.semantic_binding_ids)
                                & flagged_bindings
                            ),
                        }
                    )
                ),
            )
            if set(op.semantic_binding_ids) & flagged_bindings
            else op
            for op in operations
        ]
        prior_objects = {item.object_id for item in prior.objects}
        changed_objects = sorted(
            {
                object_id
                for op in operations
                if set(op.semantic_binding_ids) & revised_bindings
                for object_id in op.output_object_ids
                if object_id in prior_objects
            }
        )
        revisions = tuple(
            M.ObjectRevisionPrecondition(
                object_id=object_id,
                expected_digest=prior.object_digest(object_id),
                reason_refs=(f"decision:reconstruction-stage-{stage}",),
            )
            for object_id in changed_objects
        )
    return M.GeometryProgramProposal(
        proposal_id=f"reconstruction-geometry-stage-{stage}",
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        predecessor_program_digest=(
            None if prior is None else prior.program_digest
        ),
        length_unit=M.LengthUnit.METER,
        tolerance=M.GeometryTolerance(0.001, 0.001),
        frames=(
            M.CoordinateFrame(
                frame_id="world",
                parent_frame_id=None,
                transform_from_parent=M.AffineTransform.identity(),
                source_refs=evidence,
            ),
        ),
        assets=(),
        semantic_bindings=semantic_bindings,
        operations=tuple(sorted(operations, key=lambda op: op.op_id)),
        assemblies=(),
        revisions=revisions,
        retirements=(),
    )


# --------------------------------------------------------------------
# Stage declaration contracts: every range cites an adoption or is a
# typed widened-range uncertainty.
# --------------------------------------------------------------------

def stage_contracts(refs: dict[str, str]) -> dict[int, tuple]:
    def field(field_id, quadrant, minimum, maximum, check, sources,
              unit="m", kind=DeclarationKind.NUMBER, statement=""):
        return DeclarationField(
            field_id=field_id,
            quadrant=quadrant,
            kind=kind,
            unit=unit,
            minimum=minimum,
            maximum=maximum,
            geometry_check=check,
            source_refs=tuple(sources),
            statement=statement or field_id.replace("-", " "),
        )

    site_ref = ("context:site.authorized_envelope",)
    uncertain = ("uncertainty:widened-range-no-adopted-source",)
    stage0 = StageDeclarationContract(
        stage="schematic",
        fields=(
            field("drum-outer-diameter-m", DeclarationQuadrant.DIMENSIONS,
                  55.0, 57.5, GeometryCheck.PRIMARY_SPAN,
                  (refs["walls"],) + uncertain,
                  statement=(
                      "56.0 realized outer diameter; rounded wall "
                      "evidence authorizes a range, not a false exact sum"
                  )),
            field("footprint-depth-m", DeclarationQuadrant.DIMENSIONS,
                  74.0, 78.5, GeometryCheck.FOOTPRINT_DEPTH, site_ref),
            field("footprint-width-m", DeclarationQuadrant.DIMENSIONS,
                  55.0, 57.5, GeometryCheck.FOOTPRINT_WIDTH,
                  (refs["walls"],)),
            field("interior-span-m", DeclarationQuadrant.DIMENSIONS,
                  43.0, 43.6, GeometryCheck.NONE, (refs["walls"],),
                  statement="adopted 43.3 clear span, checked at stage 1"),
            field("orientation-axis", DeclarationQuadrant.SITE,
                  179.0, 181.0, GeometryCheck.NONE, (refs["front"],),
                  unit="deg",
                  statement="entrance faces the approach; axis symmetric"),
            field("overall-height-m", DeclarationQuadrant.DIMENSIONS,
                  43.0, 47.0, GeometryCheck.OVERALL_HEIGHT,
                  (refs["walls"],)),
            field("portico-width-m", DeclarationQuadrant.DIMENSIONS,
                  32.0, 34.0, GeometryCheck.NONE, (refs["typology"],)),
            field("transition-depth-m", DeclarationQuadrant.DIMENSIONS,
                  3.5, 9.0, GeometryCheck.NONE,
                  (refs["junction"],) + uncertain,
                  statement="rectangular vestibule links porch to rotunda"),
            field("transition-height-m", DeclarationQuadrant.DIMENSIONS,
                  20.0, 32.0, GeometryCheck.NONE,
                  (refs["junction"],) + uncertain,
                  statement="block carries the second pediment trace"),
            field("transition-width-m", DeclarationQuadrant.DIMENSIONS,
                  30.0, 36.0, GeometryCheck.NONE,
                  (refs["junction"],) + uncertain,
                  statement="vestibule spans the portico width"),
        ),
        tolerance_ratio=0.08,
    )
    values0 = {
        "transition-depth-m": 6.0,
        "transition-height-m": 26.0,
        "transition-width-m": 33.5,
        "drum-outer-diameter-m": 56.0,
        "footprint-depth-m": 77.0,
        "footprint-width-m": 56.0,
        "interior-span-m": 43.3,
        "orientation-axis": 180.0,
        "overall-height-m": 46.1,
        "portico-width-m": 33.1,
    }
    stage1 = StageDeclarationContract(
        stage="spatial-coordination",
        fields=(
            field("capital-height-m", DeclarationQuadrant.STRUCTURE,
                  2.2, 2.6, GeometryCheck.NONE, (refs["orders"],)),
            field("column-count", DeclarationQuadrant.STRUCTURE,
                  16, 16, GeometryCheck.NONE, (refs["typology"],),
                  unit=None, kind=DeclarationKind.COUNT),
            field("column-shaft-m", DeclarationQuadrant.STRUCTURE,
                  11.7, 12.1, GeometryCheck.NONE, (refs["orders"],)),
            field("door-height-m", DeclarationQuadrant.OPENINGS,
                  7.0, 8.0, GeometryCheck.NONE, uncertain),
            field("door-width-m", DeclarationQuadrant.OPENINGS,
                  4.0, 5.0, GeometryCheck.NONE, uncertain),
            field("oculus-ring-diameter-m", DeclarationQuadrant.OPENINGS,
                  8.9, 9.3, GeometryCheck.NONE, (refs["walls"],)),
            field("step-ring-count", DeclarationQuadrant.STRUCTURE,
                  6, 8, GeometryCheck.NONE, uncertain,
                  unit=None, kind=DeclarationKind.COUNT),
            field("wall-thickness-m", DeclarationQuadrant.STRUCTURE,
                  6.2, 6.6, GeometryCheck.NONE, (refs["walls"],)),
        ),
        tolerance_ratio=0.08,
    )
    values1 = {
        "column-count": 16,
        "column-shaft-m": 11.9,
        "capital-height-m": 2.4,
        "door-height-m": 7.53,
        "door-width-m": 4.45,
        "oculus-ring-diameter-m": 9.1,
        "step-ring-count": 7,
        "wall-thickness-m": 6.35,
    }
    stage2 = StageDeclarationContract(
        stage="design-development",
        fields=(
            field("aedicula-count", DeclarationQuadrant.DETAIL,
                  7, 9, GeometryCheck.NONE, uncertain,
                  unit=None, kind=DeclarationKind.COUNT),
            field("niche-count", DeclarationQuadrant.STRUCTURE,
                  6, 8, GeometryCheck.NONE, uncertain,
                  unit=None, kind=DeclarationKind.COUNT,
                  statement=(
                      "three non-entry cardinal exedras plus four "
                      "diagonal niches"
                  )),
        ),
        tolerance_ratio=0.08,
    )
    values2 = {"aedicula-count": 8, "niche-count": 7}
    stage3 = StageDeclarationContract(
        stage="technical",
        fields=(
            field("coffer-rings", DeclarationQuadrant.DETAIL,
                  4, 6, GeometryCheck.NONE, uncertain,
                  unit=None, kind=DeclarationKind.COUNT),
            field("coffers-per-ring", DeclarationQuadrant.DETAIL,
                  24, 32, GeometryCheck.NONE, uncertain,
                  unit=None, kind=DeclarationKind.COUNT),
        ),
        tolerance_ratio=0.08,
    )
    values3 = {"coffer-rings": 5, "coffers-per-ring": 28}
    return {
        0: (stage0, values0),
        1: (stage1, values1),
        2: (stage2, values2),
        3: (stage3, values3),
    }


class PantheonStageClosureError(RuntimeError):
    """A later P069 stage lacks an exact P079/P080 acceptance binding."""


@dataclass(frozen=True, slots=True)
class PantheonStageClosure:
    """Typed, non-authoritative inputs required before later-stage archival."""

    branch_context: BranchDecisionContext
    convergence_receipt: StageConvergenceReceipt
    operational_state: OperationalMarkovState

    def __post_init__(self) -> None:
        if not isinstance(self.branch_context, BranchDecisionContext):
            raise TypeError("branch_context must be BranchDecisionContext")
        if not isinstance(
            self.convergence_receipt, StageConvergenceReceipt
        ):
            raise TypeError(
                "convergence_receipt must be StageConvergenceReceipt"
            )
        if not isinstance(self.operational_state, OperationalMarkovState):
            raise TypeError("operational_state must be OperationalMarkovState")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "PantheonStageClosure@1",
            "branch_context": self.branch_context.to_dict(),
            "stage_convergence_receipt": (
                self.convergence_receipt.to_dict()
            ),
            "operational_state_digest": self.operational_state.state_digest,
            "canonical_write_authority": False,
        }


def _hard_decision_refs(
    contract: StageDeclarationContract,
) -> tuple[str, ...]:
    """Map project-authored declaration fields to their hard decision refs."""

    if not isinstance(contract, StageDeclarationContract):
        raise TypeError("contract must be StageDeclarationContract")
    return tuple(
        sorted(f"declaration:{field.field_id}" for field in contract.fields)
    )


def _vertical_revolve(
    op_id,
    output,
    binding,
    *,
    x,
    z,
    y0,
    y1,
    start_radius,
    end_radius=None,
):
    """Project-local vertical revolve at an arbitrary plan centre."""

    if end_radius is None:
        end_radius = start_radius
    return M._operation(
        op_id,
        M.GeometryOperationKind.REVOLVE,
        output,
        binding,
        (
            M._parameter(
                "axis_end",
                M.GeometryParameterKind.VECTOR3,
                [float(x), float(y1), float(z)],
                unit=M.LengthUnit.METER,
            ),
            M._parameter(
                "axis_start",
                M.GeometryParameterKind.VECTOR3,
                [float(x), float(y0), float(z)],
                unit=M.LengthUnit.METER,
            ),
            M._parameter(
                "end_radius",
                M.GeometryParameterKind.NUMBER,
                float(end_radius),
                unit=M.LengthUnit.METER,
            ),
            M._parameter(
                "start_radius",
                M.GeometryParameterKind.NUMBER,
                float(start_radius),
                unit=M.LengthUnit.METER,
            ),
        ),
    )


def _oriented_prism(
    op_id,
    output,
    binding,
    *,
    center_x,
    center_z,
    angle_degrees,
    radius,
    width,
    depth,
    y0,
    height,
):
    """Radially oriented rectangular prism expressed as an extrusion."""

    angle = math.radians(angle_degrees)
    radial = (math.cos(angle), math.sin(angle))
    tangent = (-math.sin(angle), math.cos(angle))
    centre = (
        center_x + radius * radial[0],
        center_z + radius * radial[1],
    )
    half_width = width / 2.0
    half_depth = depth / 2.0
    profile = []
    for tangent_sign, radial_sign in (
        (-1.0, -1.0),
        (1.0, -1.0),
        (1.0, 1.0),
        (-1.0, 1.0),
    ):
        profile.append(
            [
                centre[0]
                + tangent_sign * half_width * tangent[0]
                + radial_sign * half_depth * radial[0],
                y0,
                centre[1]
                + tangent_sign * half_width * tangent[1]
                + radial_sign * half_depth * radial[1],
            ]
        )
    return _extrusion(
        op_id,
        output,
        binding,
        profile,
        [0.0, float(height), 0.0],
    )


def _coffer_cutter(
    op_id,
    output,
    *,
    ring,
    column,
    center_x,
    center_z,
    drum_inner_radius,
    spring_y,
):
    """Trapezoidal recess cutter aligned to the inner dome normal."""

    # Five bands remain below the oculus.  Their angular placement is a
    # project derivation; the 5 x 28 topology is the adopted fact.
    theta = math.radians(14.0 + 13.0 * ring)
    phi = 2.0 * math.pi * column / COFFER_COUNT
    normal = (
        math.cos(theta) * math.cos(phi),
        math.sin(theta),
        math.cos(theta) * math.sin(phi),
    )
    tangent = (-math.sin(phi), 0.0, math.cos(phi))
    meridian = (
        -math.sin(theta) * math.cos(phi),
        math.cos(theta),
        -math.sin(theta) * math.sin(phi),
    )
    surface = (
        center_x + drum_inner_radius * normal[0],
        spring_y + drum_inner_radius * normal[1],
        center_z + drum_inner_radius * normal[2],
    )
    inner_width = 2.65 - 0.27 * ring
    inner_height = 2.85 - 0.29 * ring
    outer_width = inner_width * 0.78
    outer_height = inner_height * 0.78

    def profile(distance, width, height):
        centre = tuple(
            surface[index] + distance * normal[index]
            for index in range(3)
        )
        points = []
        for tangent_sign, meridian_sign in (
            (-1.0, -1.0),
            (1.0, -1.0),
            (1.0, 1.0),
            (-1.0, 1.0),
        ):
            points.append(
                [
                    centre[index]
                    + tangent_sign * width / 2.0 * tangent[index]
                    + meridian_sign * height / 2.0 * meridian[index]
                    for index in range(3)
                ]
            )
        return points

    profiles = [
        *profile(-0.12, inner_width, inner_height),
        *profile(1.35, outer_width, outer_height),
    ]
    return M._operation(
        op_id,
        M.GeometryOperationKind.LOFT,
        output,
        "coffer-binding",
        (
            M._parameter(
                "cap_ends", M.GeometryParameterKind.BOOLEAN, True
            ),
            M._parameter(
                "closed_profile", M.GeometryParameterKind.BOOLEAN, True
            ),
            M._parameter(
                "profile_size", M.GeometryParameterKind.INTEGER, 4
            ),
            M._parameter(
                "profiles",
                M.GeometryParameterKind.POINTS3,
                profiles,
                unit=M.LengthUnit.METER,
            ),
        ),
    )


def require_pantheon_stage_closure(
    *,
    stage: int,
    run: RunRef,
    design_state,
    contract: StageDeclarationContract,
    closure: PantheonStageClosure,
) -> tuple[str, ...]:
    """Require one exact selected-branch context and closed stage receipt.

    Declaration fields are the project's non-compensable hard constraints.
    They must be evidenced in the exact P079 context and protected by the
    exact current P080 receipt.  Weighted style or preference scores therefore
    cannot substitute for a missing regulation, dimension, or adopted rule.
    """

    if not isinstance(stage, int) or isinstance(stage, bool) or stage < 1:
        raise PantheonStageClosureError(
            "P081 closure applies only to later Pantheon stages"
        )
    if not isinstance(run, RunRef):
        raise TypeError("run must be RunRef")
    if not isinstance(closure, PantheonStageClosure):
        raise TypeError("closure must be PantheonStageClosure")
    selected = design_state.selected_schematic
    context = closure.branch_context
    scope = context.scope
    receipt = closure.convergence_receipt
    operational = closure.operational_state
    required_refs = _hard_decision_refs(contract)

    if (
        selected.project_id != run.project_id
        or selected.run_id != run.run_id
        or selected.base != run.base
        or scope.run != run
        or operational.branch.run != run
        or receipt.branch.run != run
    ):
        raise PantheonStageClosureError(
            "stage closure crosses the exact P036 project run"
        )
    if (
        scope.portfolio_id != selected.portfolio_id
        or scope.portfolio_digest != selected.portfolio_digest
        or scope.branch_id != selected.branch_id
        or scope.branch_revision_digest
        != selected.revision.revision_digest
        or scope.source_branch != operational.branch
        or receipt.branch != operational.branch
    ):
        raise PantheonStageClosureError(
            "stage closure crosses the selected Candidate identity"
        )
    if (
        scope.operational_state_digest != operational.state_digest
        or receipt.child_state_digest != operational.state_digest
        or receipt.child_sufficient_digest != operational.sufficient_digest
    ):
        raise PantheonStageClosureError(
            "stage closure is stale against the current operational state"
        )
    if receipt.stage != contract.stage:
        raise PantheonStageClosureError(
            "stage convergence receipt names another declaration stage"
        )
    if not receipt.stage_ready or receipt.changed_protected_refs:
        raise PantheonStageClosureError(
            "stage convergence is rejected, open, expanding, or changed a lock"
        )
    missing_context = set(required_refs) - set(context.decision_refs)
    uncovered = tuple(
        ref
        for ref in required_refs
        if not context.decision_basis.get(ref)
    )
    if missing_context or uncovered:
        raise PantheonStageClosureError(
            "hard declaration decisions lack a complete exact P079 context: "
            f"{sorted(missing_context | set(uncovered))}"
        )
    unlocked = set(required_refs) - set(receipt.protected_refs)
    if unlocked:
        raise PantheonStageClosureError(
            "hard declaration decisions are not protected by P080: "
            f"{sorted(unlocked)}"
        )
    return required_refs


def _gated_persist_stage(
    repository,
    *,
    run,
    state,
    program,
    stage,
    runner_context: PantheonRunnerContext | None = None,
):
    runner_context = (
        create_runner_context(run_id=run.run_id)
        if runner_context is None
        else runner_context
    )
    if not isinstance(runner_context, PantheonRunnerContext):
        raise TypeError("runner_context must be a PantheonRunnerContext")
    if (
        runner_context.profile.project_id != run.project_id
        or runner_context.profile.run_id != run.run_id
    ):
        raise PantheonStageClosureError(
            "runner profile crosses the exact P036 project run"
        )
    contract, values = runner_context.contract_for(stage)
    closure = None
    required_hard_refs = _hard_decision_refs(contract)
    if stage >= 1:
        resolver = runner_context.stage_closure_resolver
        if resolver is None:
            raise PantheonStageClosureError(
                "later-stage archival requires a configured P079/P080 "
                "closure resolver"
            )
        closure = resolver(
            stage=stage,
            run=run,
            design_state=state,
            contract=contract,
        )
        required_hard_refs = require_pantheon_stage_closure(
            stage=stage,
            run=run,
            design_state=state,
            contract=contract,
            closure=closure,
        )
    proposal = state.selected_schematic.option.proposal
    derived = validate_stage_declarations(contract, values, proposal)
    record = {
        "schema": "P069StageDeclarations@2",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "stage": stage,
        "contract": contract.to_dict(),
        "declared_values": values,
        "derived_measures": derived,
        "required_hard_decision_refs": list(required_hard_refs),
        "branch_stage_closure": (
            closure.to_dict() if closure is not None else None
        ),
        "status": "pass",
        "canonical_write_authority": False,
    }
    repository.put_json(
        run=run,
        destination=M.PersistenceDestination(
            M.PersistenceArea.RUN_RECORD, run_id=run.run_id
        ),
        record_kind=f"p069-stage-{stage}-declarations",
        payload=record,
    )
    return runner_context.hooks.persist(
        runner_context,
        repository,
        run=run,
        state=state,
        program=program,
        stage=stage,
    )


def _operation_parameters(operation) -> dict[str, object]:
    """Decode one neutral operation without trusting an adapter result."""

    return {
        item.name: json.loads(item.value_json)
        for item in operation.parameters
    }


def _detail_column_layout(
    program,
    *,
    center_x: float = CENTER_X,
) -> tuple[dict[str, object], ...]:
    """Derive Stage-3D placement only from the frozen neutral program."""

    layout = []
    for operation in program.proposal.operations:
        if not operation.op_id.startswith("column-shaft-r"):
            continue
        suffix = operation.op_id.split("column-shaft-r", 1)[1]
        row_text, column_text = suffix.split("-c", 1)
        parameters = _operation_parameters(operation)
        start = parameters["axis_start"]
        end = parameters["axis_end"]
        layout.append(
            {
                "column_id": operation.op_id.removeprefix("column-shaft-"),
                "row": int(row_text),
                "column": int(column_text),
                "x": float(start[0]),
                "z": float(start[2]),
                "base_y": float(start[1]),
                "proxy_top_y": float(end[1]),
            }
        )
    layout.sort(key=lambda item: (item["row"], item["column"]))
    row_distribution = [
        sum(1 for item in layout if item["row"] == row)
        for row in range(3)
    ]
    if row_distribution != [8, 4, 4]:
        raise RuntimeError(
            "Stage-3D requires the frozen 8+4+4 predecessor layout"
        )
    for row in range(3):
        xs = sorted(item["x"] for item in layout if item["row"] == row)
        for left, right in zip(xs, reversed(xs)):
            if abs((left + right) - 2.0 * center_x) > 1.0e-9:
                raise RuntimeError(
                    f"Stage-3D predecessor row {row} is not axial-symmetric"
                )
    return tuple(layout)


def _detail_enrichment_plan(
    program,
    *,
    external_asset: dict[str, object] | None = None,
    profile: PantheonRunnerProfile = DEFAULT_RUNNER_PROFILE,
) -> dict[str, object]:
    """Compile an inspectable, branch-scoped plan without changing massing.

    This is intentionally not a fifth accepted geometry stage.  Rhino can
    execute the detail overlay, while the generic CAD adapter still lacks an
    asset/text operation.  The retained plan makes that boundary explicit and
    binds every placement to the exact Stage-3 predecessor digest.
    """

    if not isinstance(profile, PantheonRunnerProfile):
        raise TypeError("profile must be a PantheonRunnerProfile")
    layout = _detail_column_layout(program, center_x=profile.center_x)
    row_distribution = [
        sum(1 for item in layout if item["row"] == row)
        for row in range(3)
    ]
    operation_ids = sorted(
        operation.op_id for operation in program.proposal.operations
    )
    operation_set_digest = hashlib.sha256(
        json.dumps(
            operation_ids,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    branch_identity = (
        "pantheon.classical.corinthian.second-century-reconstructed"
    )
    source_allowlist = sorted(
        {
            *DETAIL_SOURCES.values(),
            (
                "https://sketchfab.com/3d-models/"
                "corinthian-capital-1b61fd199e744afa9bdb8f46cf843e31"
            ),
        }
    )
    plan = {
        "schema": "P069DetailEnrichmentPlan@1",
        "stage": "stage-3d-detail-candidate",
        "authority_state": "agent-proposed",
        "user_ratified": False,
        "disposition": "HOLD",
        "canonical_write_authority": False,
        "accepted_archive_created": False,
        "predecessor_program_digest": program.program_digest,
        "predecessor_operation_set_digest": operation_set_digest,
        "branch_scope": {
            "branch_identity": branch_identity,
            "historical_state": DETAIL_HISTORICAL_STATE,
            "query_scope": branch_identity,
            "source_allowlist": source_allowlist,
            "decision_universe": [
                "column-order-and-material-state",
                "agrippa-inscription",
                "bronze-door-leaves",
                "entablature-and-pediment-cornice",
            ],
        },
        "massing_locks": {
            "axis_center_x_m": profile.center_x,
            "column_row_distribution": row_distribution,
            "column_centres": [
                {
                    key: item[key]
                    for key in ("column_id", "row", "column", "x", "z")
                }
                for item in layout
            ],
            "column_total_height_m": DETAIL_TOTAL_COLUMN_H,
            "drum_outer_radius_m": profile.drum_outer_radius,
            "drum_inner_radius_m": profile.drum_inner_radius,
            "portico_width_m": profile.portico_width,
            "massing_mutations": [],
        },
        "detail_decisions": [
            {
                "decision_id": "column-order-and-material-state",
                "constraint_class": "hard",
                "gate_eligible": False,
                "reason": "evidence is retained but not user-ratified",
                "implementation": {
                    "order": "Roman Corinthian",
                    "shaft_surface": "smooth-entasis-no-fluting",
                    "base_height_m": DETAIL_BASE_H,
                    "base_height_range_m": [0.68, 0.78],
                    "shaft_height_m": DETAIL_SHAFT_H,
                    "shaft_height_range_m": [11.7, 11.9],
                    "capital_height_m": DETAIL_CAPITAL_H,
                    "capital_height_range_m": [1.55, 1.70],
                    "front_row_shaft": "light-grey-granite",
                    "inner_rows_shaft": "rose-red-granite",
                    "base_and_capital": "near-white-marble",
                },
                "dependencies": [
                    "predecessor:colonnade-binding",
                    "massing-lock:column-centres",
                    "massing-lock:column-total-height",
                ],
                "source_refs": [
                    DETAIL_SOURCES["official_history"],
                    DETAIL_SOURCES["column_scan"],
                    DETAIL_SOURCES["capital_drawing"],
                ],
            },
            {
                "decision_id": "agrippa-inscription",
                "constraint_class": "hard",
                "gate_eligible": False,
                "reason": "official text retained; position/font remain candidate",
                "implementation": {
                    "text": DETAIL_INSCRIPTION,
                    "placement": "single-line-on-entablature-frieze",
                    "letter_height_m": 0.70,
                    "font_role": "Roman-square-capitals",
                    "font_runtime_proxy": "Times New Roman",
                    "relief_depth_m": 0.08,
                },
                "dependencies": [
                    "predecessor:portico-binding",
                    "massing-lock:portico-width",
                ],
                "source_refs": [
                    DETAIL_SOURCES["official_history"],
                    DETAIL_SOURCES["inscription"],
                ],
            },
            {
                "decision_id": "bronze-door-leaves",
                "constraint_class": "hard-soft-conflict",
                "gate_eligible": False,
                "implementation": {
                    "leaf_count": 2,
                    "opening_width_m": 4.45,
                    "opening_height_m": 7.53,
                    "note": (
                        "preserve frozen 4.45 m predecessor opening; official "
                        "portal source reports about 4.90 m and remains an "
                        "unratified evidence conflict"
                    ),
                },
                "dependencies": [
                    "predecessor:main-entry",
                    "massing-lock:no-opening-resize",
                ],
                "source_refs": [
                    DETAIL_SOURCES["official_history"],
                    DETAIL_SOURCES["door_drawing"],
                ],
            },
            {
                "decision_id": "entablature-and-pediment-cornice",
                "constraint_class": "soft",
                "gate_eligible": False,
                "implementation": {
                    "front_modillion_count": 47,
                    "pediment_relief": "omitted-unknown",
                    "moulding_level": "candidate-proportional-proxy",
                },
                "dependencies": [
                    "predecessor:portico-binding",
                    "decision:agrippa-inscription",
                ],
                "source_refs": [DETAIL_SOURCES["official_history"]],
            },
        ],
        "asset_candidates": [
            {
                "asset_id": "malopolska-corinthian-capital-cc0",
                "url": (
                    "https://sketchfab.com/3d-models/"
                    "corinthian-capital-1b61fd199e744afa9bdb8f46cf843e31"
                ),
                "license": "CC0-1.0",
                "pantheon_specific": False,
                "status": "parked-download-requires-authenticated-session",
                "selected": False,
                "reason": (
                    "legal candidate but not Pantheon-measured and the official "
                    "download endpoint requires user authentication"
                ),
            },
            {
                "asset_id": "procedural-pantheon-order-proxy",
                "license": "project-generated",
                "pantheon_specific": True,
                "status": "selected-candidate-fallback",
                "selected": True,
                "source_refs": [
                    DETAIL_SOURCES["column_scan"],
                    DETAIL_SOURCES["capital_drawing"],
                ],
            },
        ],
        "expected_detail_counts": {
            "column_assemblies": 16,
            "front_grey_column_assemblies": 8,
            "inner_rose_column_assemblies": 8,
            "door_leaves": 2,
            "front_modillions": 47,
            "primary_inscription_texts": 1,
        },
        "known_limits": [
            "generic-rhino-translator-has-no-asset-instance-path",
            "generic-rhino-translator-has-no-text-geometry-operation",
            "procedural-acanthus-and-volutes-are-morphology-proxies",
            "font-runtime-proxy-is-not-a-measured-letterform",
            "no-external-model-was-downloaded-without-authentication",
        ],
    }
    if external_asset is not None:
        plan["asset_candidates"][0].update(
            {
                "asset_id": external_asset["asset_id"],
                "url": external_asset["source_page"],
                "license": external_asset["license"],
                "status": "ingested-hidden-human-review-candidate",
                "artifact_ref": external_asset["artifact_ref"],
                "artifact_sha256": external_asset["artifact_sha256"],
                "license_ref": external_asset["license_ref"],
                "asset_record_ref": external_asset["record_ref"],
            }
        )
        plan["known_limits"][-1] = (
            "external-model-ingested-but-not-selected-as-pantheon-fact"
        )
    return plan


def _stl_summary(path: Path) -> dict[str, object]:
    """Read enough STL structure to retain scale/complexity uncertainty."""

    data = path.read_bytes()
    vertices: list[tuple[float, float, float]] = []
    encoding = "ascii"
    triangle_count = 0
    if len(data) >= 84:
        candidate_count = struct.unpack("<I", data[80:84])[0]
        if 84 + candidate_count * 50 == len(data):
            encoding = "binary-little-endian"
            triangle_count = candidate_count
            for index in range(candidate_count):
                offset = 84 + index * 50 + 12
                coords = struct.unpack("<9f", data[offset:offset + 36])
                vertices.extend(
                    tuple(float(value) for value in coords[start:start + 3])
                    for start in (0, 3, 6)
                )
    if not vertices:
        decoded = data.decode("utf-8", errors="ignore")
        for line in decoded.splitlines():
            parts = line.strip().split()
            if len(parts) != 4 or parts[0].lower() != "vertex":
                continue
            try:
                vertices.append(tuple(float(value) for value in parts[1:4]))
            except ValueError:
                continue
        triangle_count = len(vertices) // 3
    if not vertices or triangle_count <= 0:
        raise ValueError("capital asset is not a readable non-empty STL")
    minimum = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
    maximum = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
    dimensions = [maximum[axis] - minimum[axis] for axis in range(3)]
    return {
        "encoding": encoding,
        "bytes": len(data),
        "triangle_count": triangle_count,
        "bbox_min_native": minimum,
        "bbox_max_native": maximum,
        "dimensions_native": dimensions,
        "native_unit": "unknown",
        "normalization_rule": (
            "Rhino candidate normalizes bbox Z height to the typed capital "
            "height; source scale is not treated as evidence"
        ),
    }


def _ingest_detail_asset(
    result: dict[str, object],
    *,
    asset_path: Path,
    license_path: Path,
    readme_path: Path | None = None,
) -> dict[str, object]:
    """Ingest a user-authorized download through the project object port."""

    repository = result["repository"]
    run = result["run"]
    for label, path in (("asset", asset_path), ("license", license_path)):
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError(f"missing non-empty {label} file: {path}")
    if asset_path.suffix.lower() != ".stl":
        raise ValueError("the first detail asset route accepts STL only")
    summary = _stl_summary(asset_path)
    with asset_path.open("rb") as stream:
        artifact = repository.ingest(
            run=run,
            destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id="external-corinthian-capital-source",
            media_type="model/stl",
            source=stream,
        )
    with license_path.open("rb") as stream:
        license_artifact = repository.ingest(
            run=run,
            destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id="external-corinthian-capital-license",
            media_type="text/plain",
            source=stream,
        )
    readme_artifact = None
    if readme_path is not None:
        if not readme_path.is_file() or readme_path.stat().st_size <= 0:
            raise FileNotFoundError(
                f"missing non-empty asset readme file: {readme_path}"
            )
        with readme_path.open("rb") as stream:
            readme_artifact = repository.ingest(
                run=run,
                destination=PersistenceDestination(PersistenceArea.OBJECT),
                artifact_id="external-corinthian-capital-readme",
                media_type="text/plain",
                source=stream,
            )
    payload = {
        "schema": "P069ExternalDetailAsset@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "asset_id": "thingiverse-3028453-corinthian-capital",
        "source_page": "https://archive.org/details/thingiverse-3028453",
        "source_file_url": (
            "https://archive.org/download/thingiverse-3028453/"
            "Corinthian_capital_3028453.zip/files%2Fcapitello_corinzio.stl"
        ),
        "author": "corradobondioli",
        "license": "CC-BY-3.0",
        "license_conflict": (
            "archive metadata reports CC-BY-4.0; bundled LICENSE.txt reports "
            "CC-BY-3.0, so the stricter bundled attribution is retained"
        ),
        "pantheon_specific": False,
        "role": "hidden-human-review-capital-candidate",
        "artifact_ref": artifact.uri,
        "artifact_sha256": artifact.sha256,
        "license_ref": license_artifact.uri,
        "license_sha256": license_artifact.sha256,
        "readme_ref": (
            None if readme_artifact is None else readme_artifact.uri
        ),
        "readme_sha256": (
            None if readme_artifact is None else readme_artifact.sha256
        ),
        "stl_summary": summary,
        "downloaded_path_persisted": False,
        "canonical_write_authority": False,
        "disposition": "HOLD",
    }
    ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
        record_kind="external-detail-asset",
        payload=payload,
    )
    repository.verify()
    return {
        **payload,
        "record_ref": ref.uri,
        "resolved_path": repository.layout.resolve_record(artifact),
    }


def _detail_rhino_overlay_script(
    plan: dict[str, object],
    *,
    external_asset_path: Path | None = None,
) -> str:
    """Return the project-local Rhino overlay executed after neutral CAD."""

    import textwrap

    plan_json = json.dumps(
        plan,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    header = "\n".join(
        (
            f"_detail_plan = json.loads({plan_json!r})",
            f"_detail_asset_path = {str(external_asset_path)!r}",
        )
    )
    body = textwrap.dedent(
        r'''
        _detail_layers = {
            'proxy': ('archflow::detail::00-superseded-column-proxies', (80, 80, 80), False),
            'assembly': ('archflow::detail::10-column-assemblies', (220, 214, 196), True),
            'grey': ('archflow::detail::11-grey-granite-shafts', (155, 158, 160), True),
            'rose': ('archflow::detail::12-rose-granite-shafts', (154, 95, 82), True),
            'marble': ('archflow::detail::13-marble-order', (221, 217, 205), True),
            'external': ('archflow::detail::14-external-capital-candidate', (110, 175, 205), False),
            'inscription': ('archflow::detail::20-agrippa-inscription', (184, 142, 48), True),
            'door': ('archflow::detail::30-bronze-door', (105, 75, 38), True),
            'cornice': ('archflow::detail::40-entablature-cornice', (224, 220, 208), True),
            'conjecture': ('archflow::detail::90-conjectural-pediment-relief', (130, 90, 145), False),
        }
        for _key, (_layer, _color, _visible) in _detail_layers.items():
            rs.AddLayer(_layer, _color, _visible)
            rs.LayerVisible(_layer, _visible)

        def _detail_box(minimum, maximum, layer):
            x0, y0, z0 = minimum
            x1, y1, z1 = maximum
            guid = rs.AddBox([
                (x0, y0, z0), (x1, y0, z0),
                (x1, y1, z0), (x0, y1, z0),
                (x0, y0, z1), (x1, y0, z1),
                (x1, y1, z1), (x0, y1, z1),
            ])
            if guid: rs.ObjectLayer(guid, layer)
            return guid

        def _detail_tag(guid, name, role, layer, evidence):
            if guid is None: return
            rs.ObjectLayer(guid, layer)
            rs.ObjectName(guid, name)
            rs.SetUserText(guid, 'archflow:component', 'colonnade' if 'column' in role or 'capital' in role else role)
            rs.SetUserText(guid, 'archflow:detail_role', role)
            rs.SetUserText(guid, 'archflow:detail_branch', _detail_plan['branch_scope']['branch_identity'])
            rs.SetUserText(guid, 'archflow:predecessor_program_digest', _detail_plan['predecessor_program_digest'])
            rs.SetUserText(guid, 'archflow:evidence', evidence)
            rs.SetUserText(guid, 'archflow:authority_state', 'agent-proposed')

        _legacy_proxy_count = 0
        for _object_id, _guids in objects.items():
            if not (_object_id.startswith('column-shaft-') or _object_id.startswith('column-capital-')):
                continue
            for _guid in (_guids or []):
                rs.ObjectLayer(_guid, _detail_layers['proxy'][0])
                _legacy_proxy_count += 1
        rs.LayerVisible(_detail_layers['proxy'][0], False)

        _detail_created = []

        def _append(value, layer):
            if value is None: return
            values = value if isinstance(value, list) else [value]
            for guid in values:
                if guid:
                    rs.ObjectLayer(guid, layer)
                    _detail_created.append(guid)

        def _column_block(name, shaft_layer):
            created = []
            marble = _detail_layers['marble'][0]

            plinth = _detail_box((-0.90, -0.90, 0.00), (0.90, 0.90, 0.14), marble)
            created.append(plinth)
            for z0, z1, radius in (
                (0.14, 0.25, 0.78),
                (0.31, 0.40, 0.64),
                (0.45, 0.54, 0.61),
                (0.65, 0.73, 0.63),
            ):
                item = rs.AddCylinder((0, 0, z0), (0, 0, z1), radius, True)
                rs.ObjectLayer(item, marble)
                created.append(item)
            for z, major, minor in ((0.27, 0.66, 0.12), (0.58, 0.57, 0.10)):
                item = rs.AddTorus((0, 0, z), major, minor)
                rs.ObjectLayer(item, marble)
                created.append(item)

            shaft_levels = (
                (0.73, 0.733),
                (4.10, 0.741),
                (8.35, 0.700),
                (12.63, 0.642),
            )
            circles = [
                rs.AddCircle(rs.PlaneFromNormal((0, 0, z), (0, 0, 1)), radius)
                for z, radius in shaft_levels
            ]
            shaft = rs.AddLoftSrf(circles, loft_type=2)
            if not shaft: raise Exception('detail shaft loft failed')
            rs.CapPlanarHoles(shaft[0])
            for item in shaft:
                rs.ObjectLayer(item, shaft_layer)
                created.append(item)
            rs.DeleteObjects(circles)

            capital_base = 12.63
            neck = rs.AddCylinder((0, 0, capital_base), (0, 0, capital_base + 0.18), 0.67, True)
            rs.ObjectLayer(neck, marble)
            created.append(neck)
            bell_circles = [
                rs.AddCircle(rs.PlaneFromNormal((0, 0, z), (0, 0, 1)), radius)
                for z, radius in (
                    (capital_base + 0.14, 0.66),
                    (capital_base + 0.70, 0.79),
                    (capital_base + 1.34, 1.00),
                )
            ]
            bell = rs.AddLoftSrf(bell_circles, loft_type=2)
            if not bell: raise Exception('detail capital bell loft failed')
            rs.CapPlanarHoles(bell[0])
            for item in bell:
                rs.ObjectLayer(item, marble)
                created.append(item)
            rs.DeleteObjects(bell_circles)

            for ring, (radius, z, count, scale) in enumerate((
                (0.78, capital_base + 0.52, 8, (0.55, 1.55, 2.65)),
                (0.87, capital_base + 0.98, 8, (0.45, 1.35, 2.20)),
            )):
                for index in range(count):
                    angle = (360.0 / count) * index + ring * 22.5
                    leaf = rs.AddSphere((radius, 0, z), 0.14)
                    rs.ScaleObject(leaf, (radius, 0, z), scale, False)
                    rs.RotateObject(leaf, (0, 0, 0), angle, (0, 0, 1), False)
                    rs.ObjectLayer(leaf, marble)
                    created.append(leaf)

            for angle in (45.0, 135.0, 225.0, 315.0):
                radians = math.radians(angle)
                x = 0.80 * math.cos(radians)
                y = 0.80 * math.sin(radians)
                volute = rs.AddTorus((x, y, capital_base + 1.31), 0.20, 0.055)
                rs.ObjectLayer(volute, marble)
                created.append(volute)
            abacus = _detail_box((-1.06, -1.06, 14.12), (1.06, 1.06, 14.30), marble)
            created.append(abacus)
            for angle in (0.0, 90.0, 180.0, 270.0):
                radians = math.radians(angle)
                x = 1.02 * math.cos(radians)
                y = 1.02 * math.sin(radians)
                flower = rs.AddSphere((x, y, 14.06), 0.12)
                rs.ObjectLayer(flower, marble)
                created.append(flower)
            if any(item is None for item in created):
                raise Exception('detail column block contains failed geometry')
            block = rs.AddBlock(created, (0, 0, 0), name, True)
            if not block: raise Exception('detail column block definition failed')
            return block

        grey_block = _column_block('archflow-pantheon-column-grey-v1', _detail_layers['grey'][0])
        rose_block = _column_block('archflow-pantheon-column-rose-v1', _detail_layers['rose'][0])
        _column_instances = []
        for item in _detail_plan['massing_locks']['column_centres']:
            block = grey_block if item['row'] == 0 else rose_block
            instance = rs.InsertBlock(block, (item['x'], item['z'], 2.0))
            _detail_tag(
                instance,
                'detail-column-' + item['column_id'],
                'corinthian-column-assembly',
                _detail_layers['assembly'][0],
                ','.join(_detail_plan['detail_decisions'][0]['source_refs']),
            )
            rs.SetUserText(instance, 'archflow:shaft_material', 'light-grey-granite' if item['row'] == 0 else 'rose-red-granite')
            _column_instances.append(instance)
            _detail_created.append(instance)

        _external_instances = []
        _external_error = None
        if _detail_asset_path:
            try:
                before = set(rs.AllObjects() or [])
                options = Rhino.FileIO.FileStlReadOptions()
                options.Weld = True
                options.SplitDisjointMeshes = False
                imported_ok = Rhino.FileIO.FileStl.Read(
                    _detail_asset_path,
                    Rhino.RhinoDoc.ActiveDoc,
                    options,
                )
                imported = list(set(rs.AllObjects() or []) - before)
                if not imported_ok or not imported:
                    raise Exception('Rhino FileStl.Read returned no geometry')
                bbox = rs.BoundingBox(imported)
                xs = [point.X for point in bbox]
                ys = [point.Y for point in bbox]
                zs = [point.Z for point in bbox]
                native_height = max(zs) - min(zs)
                if native_height <= 0:
                    raise Exception('external capital has zero Z height')
                rs.MoveObjects(
                    imported,
                    (-(min(xs) + max(xs)) / 2.0,
                     -(min(ys) + max(ys)) / 2.0,
                     -min(zs)),
                )
                scale = float(_detail_plan['detail_decisions'][0]['implementation']['capital_height_m']) / native_height
                rs.ScaleObjects(imported, (0, 0, 0), (scale, scale, scale), False)
                for guid in imported:
                    rs.ObjectLayer(guid, _detail_layers['external'][0])
                external_block = rs.AddBlock(
                    imported,
                    (0, 0, 0),
                    'archflow-external-capital-candidate-v1',
                    True,
                )
                capital_z = 2.0 + 0.73 + 11.90
                for item in _detail_plan['massing_locks']['column_centres']:
                    instance = rs.InsertBlock(
                        external_block,
                        (item['x'], item['z'], capital_z),
                    )
                    _detail_tag(
                        instance,
                        'external-capital-' + item['column_id'],
                        'external-capital-candidate',
                        _detail_layers['external'][0],
                        _detail_plan['asset_candidates'][0].get('artifact_ref', ''),
                    )
                    _external_instances.append(instance)
                rs.LayerVisible(_detail_layers['external'][0], False)
            except Exception as error:
                _external_error = type(error).__name__ + ': ' + str(error)
                rs.LayerVisible(_detail_layers['external'][0], False)

        cornice_layer = _detail_layers['cornice'][0]
        _cornice_objects = []
        for minimum, maximum, role in (
            ((11.45, -0.18, 16.35), (44.55, 0.02, 16.68), 'lower-architrave-fascia'),
            ((11.25, -0.28, 18.18), (44.75, 0.02, 18.42), 'frieze-crown-band'),
            ((10.95, -0.48, 18.73), (45.05, 0.02, 19.00), 'corona-band'),
        ):
            guid = _detail_box(minimum, maximum, cornice_layer)
            _detail_tag(guid, 'detail-' + role, role, cornice_layer, _detail_plan['detail_decisions'][3]['source_refs'][0])
            _cornice_objects.append(guid)
            _detail_created.append(guid)
        for index in range(47):
            x = 13.0 + index * (30.0 / 46.0)
            guid = _detail_box((x - 0.16, -0.52, 18.45), (x + 0.16, -0.08, 18.72), cornice_layer)
            _detail_tag(guid, 'front-modillion-%02d' % index, 'front-modillion', cornice_layer, _detail_plan['detail_decisions'][3]['source_refs'][0])
            _cornice_objects.append(guid)
            _detail_created.append(guid)

        slope = math.degrees(math.atan2(7.0, 15.75))
        slope_length = math.sqrt(15.75 ** 2 + 7.0 ** 2)
        for side, midpoint, angle in (
            ('left', (20.125, -0.18, 22.50), -slope),
            ('right', (35.875, -0.18, 22.50), slope),
        ):
            guid = _detail_box(
                (-slope_length / 2.0, -0.26, -0.16),
                (slope_length / 2.0, 0.26, 0.16),
                cornice_layer,
            )
            rs.RotateObject(guid, (0, 0, 0), angle, (0, 1, 0), False)
            rs.MoveObject(guid, midpoint)
            _detail_tag(guid, 'pediment-cornice-' + side, 'pediment-cornice', cornice_layer, _detail_plan['detail_decisions'][3]['source_refs'][0])
            _cornice_objects.append(guid)
            _detail_created.append(guid)

        inscription_layer = _detail_layers['inscription'][0]
        inscription_plane = Rhino.Geometry.Plane(
            Rhino.Geometry.Point3d(28.0, -0.31, 17.38),
            Rhino.Geometry.Vector3d.XAxis,
            Rhino.Geometry.Vector3d.ZAxis,
        )
        inscription_id = rs.AddText(
            _detail_plan['detail_decisions'][1]['implementation']['text'],
            inscription_plane,
            0.70,
            'Times New Roman',
            0,
            2 + 131072,
        )
        _detail_tag(inscription_id, 'agrippa-inscription-text', 'agrippa-inscription', inscription_layer, ','.join(_detail_plan['detail_decisions'][1]['source_refs']))
        _inscription_outlines = rs.ExplodeText(inscription_id, False) or []
        if _inscription_outlines:
            text_bbox = rs.BoundingBox(_inscription_outlines)
            text_width = text_bbox[1].X - text_bbox[0].X
            text_height = text_bbox[4].Z - text_bbox[0].Z
            if text_width <= 0 or text_height <= 0:
                raise Exception('inscription outline has an invalid bounding box')
            text_scale = min(29.0 / text_width, 0.70 / text_height)
            text_center = (
                (text_bbox[0].X + text_bbox[6].X) / 2.0,
                (text_bbox[0].Y + text_bbox[6].Y) / 2.0,
                (text_bbox[0].Z + text_bbox[6].Z) / 2.0,
            )
            rs.ScaleObjects(
                _inscription_outlines,
                text_center,
                (text_scale, text_scale, text_scale),
                False,
            )
            text_bbox = rs.BoundingBox(_inscription_outlines)
            text_center = (
                (text_bbox[0].X + text_bbox[6].X) / 2.0,
                (text_bbox[0].Y + text_bbox[6].Y) / 2.0,
                (text_bbox[0].Z + text_bbox[6].Z) / 2.0,
            )
            rs.MoveObjects(
                _inscription_outlines,
                (28.0 - text_center[0], -0.31 - text_center[1],
                 17.38 - text_center[2]),
            )
        rs.DeleteObject(inscription_id)
        _inscription_relief = []
        for index, curve in enumerate(_inscription_outlines):
            rs.ObjectLayer(curve, inscription_layer)
            relief = rs.ExtrudeCurveStraight(curve, (0, 0, 0), (0, -0.08, 0))
            if relief:
                try: rs.CapPlanarHoles(relief)
                except Exception: pass
                _detail_tag(relief, 'agrippa-letter-outline-%03d' % index, 'agrippa-inscription-relief', inscription_layer, ','.join(_detail_plan['detail_decisions'][1]['source_refs']))
                _inscription_relief.append(relief)
                _detail_created.append(relief)

        door_layer = _detail_layers['door'][0]
        _door_leaves = []
        for side, x0, x1 in (
            ('left', 25.775, 27.970),
            ('right', 28.030, 30.225),
        ):
            leaf = _detail_box((x0, 18.86, 1.00), (x1, 18.98, 8.53), door_layer)
            _detail_tag(leaf, 'bronze-door-' + side, 'bronze-door-leaf', door_layer, ','.join(_detail_plan['detail_decisions'][2]['source_refs']))
            _door_leaves.append(leaf)
            _detail_created.append(leaf)
            for panel in range(4):
                z0 = 1.35 + panel * 1.70
                panel_guid = _detail_box((x0 + 0.18, 18.78, z0), (x1 - 0.18, 18.86, z0 + 1.22), door_layer)
                _detail_tag(panel_guid, 'bronze-door-%s-panel-%d' % (side, panel), 'bronze-door-panel', door_layer, ','.join(_detail_plan['detail_decisions'][2]['source_refs']))
                _detail_created.append(panel_guid)

        detail_bbox = rs.BoundingBox([guid for guid in _detail_created if guid])
        _detail_measures = {
            'schema': 'P069RhinoDetailMeasures@1',
            'predecessor_program_digest': _detail_plan['predecessor_program_digest'],
            'branch_identity': _detail_plan['branch_scope']['branch_identity'],
            'historical_state': _detail_plan['branch_scope']['historical_state'],
            'selected_capital_representation': 'procedural-pantheon-order-proxy',
            'legacy_proxy_objects_hidden': _legacy_proxy_count,
            'column_assemblies': len(_column_instances),
            'front_grey_column_assemblies': sum(1 for item in _detail_plan['massing_locks']['column_centres'] if item['row'] == 0),
            'inner_rose_column_assemblies': sum(1 for item in _detail_plan['massing_locks']['column_centres'] if item['row'] > 0),
            'external_capital_instances_hidden': len(_external_instances),
            'external_capital_import_error': _external_error,
            'primary_inscription_text': _detail_plan['detail_decisions'][1]['implementation']['text'],
            'primary_inscription_outline_count': len(_inscription_outlines),
            'primary_inscription_relief_count': len(_inscription_relief),
            'door_leaves': len(_door_leaves),
            'front_modillions': 47,
            'pediment_relief': 'omitted-unknown',
            'detail_bbox': None if not detail_bbox else {
                'minimum': [detail_bbox[0].X, detail_bbox[0].Y, detail_bbox[0].Z],
                'maximum': [detail_bbox[6].X, detail_bbox[6].Y, detail_bbox[6].Z],
            },
            'massing_mutations': [],
        }
        '''
    ).strip()
    return header + "\n" + body + "\n"


def _measure_program_structure(program) -> dict[str, object]:
    """Measure the authored topology that generic bbox gates cannot see.

    These are project-local semantic/relational measures.  They deliberately
    inspect the compiled neutral program, not a Rhino screenshot or a prompt.
    """

    operations = tuple(program.proposal.operations)
    by_id = {item.op_id: item for item in operations}
    bindings = tuple(program.proposal.semantic_bindings)

    row_counts: dict[int, int] = {}
    column_kinds = set()
    for operation in operations:
        if not operation.op_id.startswith("column-shaft-r"):
            continue
        try:
            row = int(operation.op_id.split("-r", 1)[1].split("-", 1)[0])
        except (IndexError, ValueError):
            continue
        row_counts[row] = row_counts.get(row, 0) + 1
        column_kinds.add(operation.kind.value)

    drum = by_id.get("drum-wall")
    dome = by_id.get("dome-shell")
    drum_inputs = tuple(drum.input_object_ids) if drum is not None else ()
    dome_inputs = tuple(dome.input_object_ids) if dome is not None else ()
    exedras = tuple(
        item for item in operations
        if item.op_id.startswith("exedra-cutter-")
    )
    niches = tuple(
        item for item in operations
        if item.op_id.startswith("niche-cutter-")
    )
    aediculae = tuple(
        item for item in operations
        if item.op_id.startswith("aedicula-")
    )
    coffers = tuple(
        item for item in operations
        if item.op_id.startswith("coffer-cutter-r")
    )
    coffer_rows: dict[int, int] = {}
    for operation in coffers:
        try:
            ring = int(operation.op_id.split("-r", 1)[1].split("-", 1)[0])
        except (IndexError, ValueError):
            continue
        coffer_rows[ring] = coffer_rows.get(ring, 0) + 1

    binding_components = {item.component_id for item in bindings}
    return {
        "column_row_distribution": [
            row_counts[key] for key in sorted(row_counts)
        ],
        "column_operation_kinds": sorted(column_kinds),
        "exedra_cutter_count": len(exedras),
        "diagonal_niche_cutter_count": len(niches),
        "drum_cutter_ids": sorted(
            output
            for operation in (*exedras, *niches)
            for output in operation.output_object_ids
            if output in drum_inputs
        ),
        "aedicula_count": len(aediculae),
        "apse_binding_present": "apse" in binding_components,
        "coffer_ring_count": len(coffer_rows),
        "coffers_per_ring": [
            coffer_rows[key] for key in sorted(coffer_rows)
        ],
        "coffer_cutter_count": len(coffers),
        "dome_cutter_ids": sorted(
            output
            for operation in coffers
            for output in operation.output_object_ids
            if output in dome_inputs
        ),
        "statuary_present": any(
            "statuary" in item.op_id for item in operations
        ) or "statuary-ring" in binding_components,
        "oculus_present": any(
            item.op_id.startswith("oculus-") for item in operations
        ),
        "dome_step_ring_count": sum(
            item.op_id.startswith("dome-step-ring-")
            for item in operations
        ),
        "front_step_count": sum(
            item.op_id.startswith("front-step-")
            for item in operations
        ),
    }


def _realized_declaration_values(
    program,
    stage: int,
    *,
    runner_context: PantheonRunnerContext | None = None,
) -> dict[str, float]:
    """Read declaration values back from the neutral program itself."""

    runner_context = (
        create_runner_context()
        if runner_context is None
        else runner_context
    )
    if not isinstance(runner_context, PantheonRunnerContext):
        raise TypeError("runner_context must be a PantheonRunnerContext")
    profile = runner_context.profile
    operations = {item.op_id: item for item in program.proposal.operations}

    def params(op_id: str) -> dict[str, object]:
        return _operation_parameters(operations[op_id])

    outer = params("drum-outer")
    inner = params("drum-inner")
    plinth = params("plinth")
    portico = params("portico-mass")
    transition = params("transition-box")
    dome_profiles = params("dome-outer")["profiles"]
    values: dict[str, float] = {
        "drum-outer-diameter-m": 2.0 * float(outer["start_radius"]),
        "footprint-depth-m": float(plinth["size"][2]),
        "footprint-width-m": float(plinth["size"][0]),
        "interior-span-m": 2.0 * float(inner["start_radius"]),
        "orientation-axis": 180.0,
        "overall-height-m": max(float(point[1]) for point in dome_profiles),
        "portico-width-m": float(portico["size"][0]),
        "transition-depth-m": (
            profile.center_z
            - float(outer["start_radius"])
            - float(transition["origin"][2])
        ),
        "transition-height-m": float(transition["size"][1]),
        "transition-width-m": float(transition["size"][0]),
    }
    if stage >= 1:
        shafts = [
            item for item in operations.values()
            if item.op_id.startswith("column-shaft-r")
        ]
        capitals = [
            item for item in operations.values()
            if item.op_id.startswith("column-capital-r")
        ]
        shaft_heights = [
            abs(
                float(_operation_parameters(item)["axis_end"][1])
                - float(_operation_parameters(item)["axis_start"][1])
            )
            for item in shafts
        ]
        capital_heights = [
            abs(
                float(_operation_parameters(item)["axis_end"][1])
                - float(_operation_parameters(item)["axis_start"][1])
            )
            for item in capitals
        ]
        door = params("door-tool")
        oculus = params("oculus-tool")
        structure = _measure_program_structure(program)
        values.update(
            {
                "capital-height-m": (
                    sum(capital_heights) / len(capital_heights)
                    if capital_heights else 0.0
                ),
                "column-count": float(len(shafts)),
                "column-shaft-m": (
                    sum(shaft_heights) / len(shaft_heights)
                    if shaft_heights else 0.0
                ),
                "door-height-m": float(door["size"][1]),
                "door-width-m": float(door["size"][0]),
                "oculus-ring-diameter-m": (
                    2.0 * float(oculus["start_radius"])
                ),
                "step-ring-count": float(
                    structure["dome_step_ring_count"]
                ),
                "wall-thickness-m": (
                    float(outer["start_radius"])
                    - float(inner["start_radius"])
                ),
            }
        )
    if stage >= 2:
        structure = _measure_program_structure(program)
        values.update(
            {
                "aedicula-count": float(structure["aedicula_count"]),
                "niche-count": float(
                    structure["exedra_cutter_count"]
                    + structure["diagonal_niche_cutter_count"]
                ),
            }
        )
    if stage >= 3:
        structure = _measure_program_structure(program)
        per_ring = structure["coffers_per_ring"]
        values.update(
            {
                "coffer-rings": float(structure["coffer_ring_count"]),
                "coffers-per-ring": float(per_ring[0] if per_ring else 0),
            }
        )
    contract, _ = runner_context.contract_for(stage)
    return {
        field.field_id: values[field.field_id]
        for field in contract.fields
    }


def _candidate_structure_issues(
    stage: int,
    metrics: dict[str, object],
) -> tuple[str, ...]:
    issues = []
    if stage >= 1:
        if metrics["column_row_distribution"] != [8, 4, 4]:
            issues.append("stage1.column_rows_not_8_4_4")
        if metrics["column_operation_kinds"] != ["revolve"]:
            issues.append("stage1.columns_not_revolved")
        if not metrics["oculus_present"]:
            issues.append("stage1.oculus_missing")
        if metrics["dome_step_ring_count"] != 7:
            issues.append("stage1.dome_step_rings_not_7")
        if metrics["front_step_count"] != 5:
            issues.append("stage1.front_steps_not_5")
    if stage >= 2:
        if metrics["exedra_cutter_count"] != 3:
            issues.append("stage2.exedras_not_3")
        if metrics["diagonal_niche_cutter_count"] != 4:
            issues.append("stage2.diagonal_niches_not_4")
        if len(metrics["drum_cutter_ids"]) != 7:
            issues.append("stage2.spaces_not_cut_from_drum")
        if metrics["aedicula_count"] != 8:
            issues.append("stage2.aediculae_not_8")
        if not metrics["apse_binding_present"]:
            issues.append("stage2.apse_identity_missing")
    if stage >= 3:
        if metrics["coffer_ring_count"] != 5:
            issues.append("stage3.coffer_rings_not_5")
        if metrics["coffers_per_ring"] != [28, 28, 28, 28, 28]:
            issues.append("stage3.coffers_per_ring_not_28")
        if len(metrics["dome_cutter_ids"]) != 140:
            issues.append("stage3.coffers_not_cut_from_dome")
        if metrics["statuary_present"]:
            issues.append("stage3.unsourced_statuary_present")
    return tuple(issues)


def _freeze_stage_contracts(
    contracts: Mapping[int, tuple],
) -> tuple[PantheonStageContractBinding, ...]:
    return tuple(
        PantheonStageContractBinding(
            stage=stage,
            contract=contract,
            declared_values=tuple(
                sorted(
                    (field_id, float(value))
                    for field_id, value in declared_values.items()
                )
            ),
        )
        for stage, (contract, declared_values) in sorted(contracts.items())
    )


def _pantheon_symmetry_findings(
    runner_context: PantheonRunnerContext,
    scene_objects,
    stage: int,
):
    from archflow.evaluation import axial_group_offsets

    findings = list(
        M._stage_symmetry_findings(
            scene_objects,
            stage,
            axis_value=runner_context.profile.center_x,
        )
    )
    findings += list(
        axial_group_offsets(
            scene_objects,
            axis_value=runner_context.profile.center_x,
            axis_index=0,
            groups={"transition": ("transition-binding",)},
        )
    )
    return findings


def _pantheon_persist_stage(
    runner_context: PantheonRunnerContext,
    repository,
    *,
    run,
    state,
    program,
    stage: int,
):
    return M._persist_stage(
        repository,
        run=run,
        state=state,
        program=program,
        stage=stage,
        symmetry_findings_hook=lambda scene_objects, current_stage: (
            runner_context.hooks.symmetry(
                runner_context,
                scene_objects,
                current_stage,
            )
        ),
        symmetry_axis_value=runner_context.profile.center_x,
    )


def _pantheon_provider(
    runner_context: PantheonRunnerContext,
    context,
) -> _PantheonScriptedProvider:
    return _PantheonScriptedProvider(context, runner_context)


DEFAULT_RUNNER_HOOKS = PantheonRunnerHooks(
    context=_pantheon_context,
    proposal=_pantheon_proposal,
    geometry=_pantheon_geometry,
    provider=_pantheon_provider,
    persist=_pantheon_persist_stage,
    symmetry=_pantheon_symmetry_findings,
)


def create_runner_context(
    *,
    profile: PantheonRunnerProfile = DEFAULT_RUNNER_PROFILE,
    hooks: PantheonRunnerHooks = DEFAULT_RUNNER_HOOKS,
    contracts: Mapping[int, tuple] | None = None,
    stage_closure_resolver=None,
    run_id: str | None = None,
    source_run_id: str | None = None,
) -> PantheonRunnerContext:
    """Bind one run without mutating the imported monument support module."""

    if not isinstance(profile, PantheonRunnerProfile):
        raise TypeError("profile must be a PantheonRunnerProfile")
    if run_id is not None or source_run_id is not None:
        profile = replace(
            profile,
            run_id=profile.run_id if run_id is None else run_id,
            source_run_id=(
                profile.source_run_id
                if source_run_id is None
                else source_run_id
            ),
        )
    source = (
        stage_contracts(adoption_refs(profile.project_id))
        if contracts is None
        else contracts
    )
    return PantheonRunnerContext(
        profile=profile,
        hooks=hooks,
        contracts=_freeze_stage_contracts(source),
        stage_closure_resolver=stage_closure_resolver,
    )


def install(
    *,
    stage_closure_resolver=None,
    run_id: str = RUN_ID,
) -> PantheonRunnerContext:
    """Deprecated compatibility factory; it performs no module mutation."""

    return create_runner_context(
        run_id=run_id,
        stage_closure_resolver=stage_closure_resolver,
    )


def _raw_request_for_candidate(
    repository,
    *,
    source_run_id: str,
    runner_context: PantheonRunnerContext,
):
    """Resolve the retained prompt exactly; never synthesize a second input."""

    profile = runner_context.profile
    source_run = repository.load_run(source_run_id)
    refs = repository.list_json(
        run=source_run,
        destination=M.PersistenceDestination(M.PersistenceArea.INPUT),
    )
    matches = []
    for ref in refs:
        payload = repository.load_json(ref)
        if (
            payload.get("schema") == "RawProjectRequest@1"
            and payload.get("prompt") == profile.prompt
        ):
            matches.append(ref)
    if len(matches) != 1:
        raise RuntimeError(
            "expected exactly one retained Pantheon raw request matching "
            f"the frozen prompt, found {len(matches)}"
        )
    return matches[0]


def _agent_evidence_proposal(
    profile: PantheonRunnerProfile = DEFAULT_RUNNER_PROFILE,
) -> dict[str, object]:
    """Research leads for review; explicitly not an adoption or ratification."""

    return {
        "schema": "P069AgentEvidenceProposal@1",
        "project_id": profile.project_id,
        "authored_on": "2026-08-29",
        "authority_state": "agent-proposed",
        "user_ratified": False,
        "hard_commitment_authority": False,
        "sources": [
            {
                "source_family": "italian-ministry-of-culture",
                "url": (
                    "https://direzionemuseiroma.cultura.gov.it/en/"
                    "pantheon/historical-background/"
                ),
                "proposed_claims": [
                    "interior diameter and height are each 43.30 m",
                    "the oculus is about 9 m",
                ],
            },
            {
                "source_family": "museo-omero",
                "url": "https://museoomero.it/en/opere/the-pantheon/",
                "proposed_claims": [
                    "sixteen Corinthian columns are arranged 8+4+4"
                ],
            },
            {
                "source_family": "springer-nexus-network-journal",
                "url": "https://doi.org/10.1007/s00004-019-00434-7",
                "proposed_claims": [
                    "four cardinal exedras, four diagonal niches, and "
                    "eight interstitial aedicula wall segments"
                ],
            },
            {
                "source_family": "springer-nexus-network-journal",
                "url": "https://doi.org/10.1007/s00004-018-00423-2",
                "proposed_claims": [
                    "the dome coffering has five rings of 28 coffers"
                ],
            },
        ],
        "canonical_write_authority": False,
    }


def _run_hold_candidate(
    root: Path,
    *,
    run_id: str,
    source_run_id: str,
    runner_context: PantheonRunnerContext | None = None,
) -> dict[str, object]:
    """Build an inspectable exact-predecessor candidate without archiving.

    P079/P080 currently cannot bind three successor stage states without a
    successor research scope.  This route therefore persists root authoring
    evidence and candidate programs, but deliberately never calls the stage
    archive or production-transition persistence functions.
    """

    runner_context = (
        create_runner_context(
            run_id=run_id,
            source_run_id=source_run_id,
        )
        if runner_context is None
        else runner_context
    )
    if not isinstance(runner_context, PantheonRunnerContext):
        raise TypeError("runner_context must be a PantheonRunnerContext")
    profile = runner_context.profile
    if profile.run_id != run_id or profile.source_run_id != source_run_id:
        raise RuntimeError("runner arguments disagree with the immutable profile")
    repository = M.FilesystemProjectRepository.open(root)
    if repository.load_manifest().project_id != profile.project_id:
        raise RuntimeError("resolved project root has another project identity")
    run_manifest = repository.layout.run(run_id).manifest
    if run_manifest.exists():
        raise FileExistsError(
            f"candidate run already exists and will not be resumed: {run_id}"
        )
    raw_request = _raw_request_for_candidate(
        repository,
        source_run_id=source_run_id,
        runner_context=runner_context,
    )
    run = repository.create_run(run_id)
    destination = M.PersistenceDestination(
        M.PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    candidate_destination = M.PersistenceDestination(
        M.PersistenceArea.RUN_CANDIDATE,
        run_id=run.run_id,
    )
    evidence_ref = repository.put_json(
        run=run,
        destination=candidate_destination,
        record_kind="agent-evidence-proposal",
        payload=_agent_evidence_proposal(profile),
    )

    context = M._rebase_context(
        runner_context.hooks.context(profile),
        run,
    )
    context_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="production-authoring-context",
        payload=context.to_dict(),
    )
    provider = runner_context.hooks.provider(runner_context, context)
    collector = M.InvocationEvidenceCollector()
    authorized = M.activate_model_provider(
        provider,
        identity=M.ProviderIdentity(
            provider_id=M.IDENTITY.provider_id,
            version=M.IDENTITY.provider_version,
            fingerprint=M.IDENTITY.provider_fingerprint,
        ),
        responsibility_id="model.production-root",
        contract_owner_id="archflow.production-root",
        verification_evidence_refs=(context_ref.uri,),
        envelope_observer=collector.observe,
    )
    compiler = M.ProductionRootCompiler(
        repository=repository,
        context_ref=context_ref,
        context=context,
        provider=authorized,
        evidence_collector=collector,
        geometry_provider_identity=M.IDENTITY,
    )
    runtime = asyncio.run(
        M.run_or_resume_production_step(
            repository,
            run=run,
            raw_request=raw_request,
            prompt=profile.prompt,
            step_id="pantheon-candidate-root",
            compiler=compiler,
        )
    )
    state_record = next(
        item
        for item in runtime.archive.records
        if item.role is M.ProductionRecordRole.DESIGN_STATE
    )
    initial_state = M.DevelopedDesignState.from_dict(state_record.content)
    from archflow.compilers.geometry import compile_geometry_program

    compiled = compile_geometry_program(
        initial_state,
        provider.generated_geometry,
        active_commitment_refs=(
            M.COMMITMENT_REF,
            M.AXIS_COMMITMENT_REF,
        ),
    )
    if compiled.program is None:
        raise RuntimeError(
            "stage 0 neutral geometry did not compile: "
            + "; ".join(compiled.receipt.issues)
        )

    stages = []
    predecessor_state = initial_state
    predecessor_program = compiled.program
    for stage in range(4):
        if stage == 0:
            current_state = predecessor_state
            current_program = predecessor_program
            lifecycle_receipt = None
        else:
            stage_plan = profile.stage_plan(stage)
            current_state = M._next_state(
                predecessor_state,
                stage=stage,
                stage_components=stage_plan.components,
                stage_revisions=stage_plan.revisions,
            )
            lifecycle = M.compile_semantic_geometry_lifecycle(
                transaction_id=f"pantheon-candidate-stage-{stage}",
                predecessor_state=predecessor_state,
                current_state=current_state,
                predecessor_proposal=(
                    predecessor_state.selected_schematic.option.proposal
                ),
                current_proposal=(
                    current_state.selected_schematic.option.proposal
                ),
                prior_program=predecessor_program,
                geometry_proposal=runner_context.hooks.geometry(
                    current_state,
                    stage=stage,
                    prior=predecessor_program,
                    profile=profile,
                ),
                revalidated_component_ids=(
                    ("main-entry",)
                    if stage == 2
                    else ("dome-step-rings", "oculus")
                    if stage == 3
                    else ()
                ),
                active_commitment_refs=(
                    M.COMMITMENT_REF,
                    M.AXIS_COMMITMENT_REF,
                ),
            )
            if (
                lifecycle.receipt.status
                is not M.SemanticGeometryLifecycleStatus.COMPILED
                or lifecycle.geometry_program is None
            ):
                raise RuntimeError(
                    f"stage {stage} lifecycle did not compile: "
                    + "; ".join(lifecycle.receipt.issues)
                )
            current_program = lifecycle.geometry_program
            lifecycle_receipt = lifecycle.receipt.to_dict()

        realized = _realized_declaration_values(
            current_program,
            stage,
            runner_context=runner_context,
        )
        contract, _ = runner_context.contract_for(stage)
        declaration_error = None
        try:
            derived = validate_stage_declarations(
                contract,
                realized,
                current_state.selected_schematic.option.proposal,
            )
        except Exception as exc:  # retained as candidate diagnostic
            declaration_error = f"{type(exc).__name__}: {exc}"
            derived = {}
        structure = _measure_program_structure(current_program)
        structure_issues = _candidate_structure_issues(stage, structure)
        program_ref = repository.put_json(
            run=run,
            destination=candidate_destination,
            record_kind=f"geometry-program-stage-{stage}",
            payload=current_program.to_dict(),
        )
        review_ref = repository.put_json(
            run=run,
            destination=candidate_destination,
            record_kind=f"stage-{stage}-review",
            payload={
                "schema": "P069CandidateStageReview@1",
                "project_id": run.project_id,
                "run_id": run.run_id,
                "stage": stage,
                "program_ref": program_ref.uri,
                "program_digest": current_program.program_digest,
                "predecessor_program_digest": (
                    None
                    if stage == 0
                    else predecessor_program.program_digest
                ),
                "exact_predecessor_compiled": (
                    stage == 0 or lifecycle_receipt is not None
                ),
                "lifecycle_receipt": lifecycle_receipt,
                "contract": contract.to_dict(),
                "realized_declaration_values": realized,
                "proposal_derived_measures": derived,
                "declaration_error": declaration_error,
                "structure_measures": structure,
                "structure_issues": list(structure_issues),
                "evidence_proposal_ref": evidence_ref.uri,
                "checks_status": (
                    "pass"
                    if declaration_error is None and not structure_issues
                    else "fail"
                ),
                "disposition": "HOLD",
                "hold_reasons": [
                    "p078-p080-successor-scope-not-closed",
                    "evidence-ratification-pending",
                    "formal-export-artifact-port-unavailable",
                ],
                "accepted_archive_created": False,
                "canonical_write_authority": False,
            },
        )
        stages.append(
            {
                "stage": stage,
                "program_ref": program_ref,
                "review_ref": review_ref,
                "checks_status": (
                    "pass"
                    if declaration_error is None and not structure_issues
                    else "fail"
                ),
                "issues": tuple(
                    [declaration_error] if declaration_error else []
                ) + structure_issues,
            }
        )
        predecessor_state = current_state
        predecessor_program = current_program

    manifest_ref = repository.put_json(
        run=run,
        destination=candidate_destination,
        record_kind="candidate-manifest",
        payload={
            "schema": "P069CandidateRunManifest@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "source_run_id": source_run_id,
            "raw_request_ref": raw_request.uri,
            "provider_profile": "local-scripted-no-external-api",
            "provider_invocation_count": len(provider.calls),
            "stages": [
                {
                    "stage": item["stage"],
                    "program_ref": item["program_ref"].uri,
                    "review_ref": item["review_ref"].uri,
                    "checks_status": item["checks_status"],
                    "issues": list(item["issues"]),
                }
                for item in stages
            ],
            "disposition": "HOLD",
            "accepted_archive_created": False,
            "canonical_write_authority": False,
        },
    )
    repository.verify()
    return {
        "repository": repository,
        "run": run,
        "stages": stages,
        "manifest_ref": manifest_ref,
        "final_program": predecessor_program,
        "final_state": predecessor_state,
        "provider_invocations": len(provider.calls),
        "runner_profile": profile,
    }


def _rhino_metric_document_setup_lines() -> tuple[str, ...]:
    """Fail closed unless the active Rhino document records metres.

    Geometry program parameters are authored in metres.  ``scale=False``
    changes the empty document's unit declaration without rescaling those
    already-metric coordinates.
    """

    return (
        "    _doc = Rhino.RhinoDoc.ActiveDoc",
        "    if _doc is None:",
        "        raise RuntimeError('Rhino ActiveDoc is unavailable')",
        "    _doc.AdjustModelUnitSystem(Rhino.UnitSystem.Meters, False)",
        "    if _doc.ModelUnitSystem != Rhino.UnitSystem.Meters:",
        "        raise RuntimeError('Rhino model units are not meters')",
    )


def _write_speculative_rhino_workspace(
    result: dict[str, object],
) -> dict[str, Path | str]:
    """Write only to the explicitly named RUN_WORKSPACE candidate root."""

    import textwrap
    from archflow.adapters.cad_program import translate_to_rhino_python
    from archflow.capabilities.material import (
        MaterialIntent,
        MaterialLedger,
        ledger_coverage,
    )

    repository = result["repository"]
    run = result["run"]
    program = result["final_program"]
    refs = adoption_refs()
    ledger = MaterialLedger(
        intents=(
            MaterialIntent(
                material_id="grey-granite",
                label="candidate grey granite column shafts",
                source_refs=(refs["orders"],),
            ),
            MaterialIntent(
                material_id="roman-concrete",
                label="candidate Roman concrete drum and dome substrate",
                source_refs=(refs["walls"],),
            ),
        ),
        assignments=(
            ("colonnade", "grey-granite"),
            ("dome", "roman-concrete"),
            ("rotunda", "roman-concrete"),
            ("transition", "roman-concrete"),
        ),
    )
    bindings = [
        {
            "binding_id": item.binding_id,
            "component_id": item.component_id,
            "object_ids": list(item.object_ids),
        }
        for item in program.proposal.semantic_bindings
    ]
    coverage = ledger_coverage(bindings, ledger)
    material_map = ledger.component_map()
    colors = {item.material_id: item.color for item in ledger.intents}
    translation = translate_to_rhino_python(
        program,
        material_by_component=material_map,
        material_colors=colors,
    )

    workspace = (
        repository.layout.run(run.run_id).workspaces / "cad-stage-3"
    ).resolve()
    workspace.relative_to(
        repository.layout.run(run.run_id).workspaces.resolve()
    )
    workspace.mkdir(parents=True, exist_ok=False)
    wrapper_path = workspace / "pantheon-candidate-wrapper.py"
    model_path = workspace / "pantheon-candidate.3dm"
    measures_path = workspace / "cad-measures.json"
    semantics_path = workspace / "cad-semantics.json"
    status_path = workspace / "cad-run-status.json"
    escaped = {
        "model": str(model_path),
        "measures": str(measures_path),
        "semantics": str(semantics_path),
        "status": str(status_path),
    }
    wrapper = "\n".join(
        (
            "# -*- coding: utf-8 -*-",
            "import json, traceback",
            "import Rhino",
            "_status = {'schema': 'P069RhinoCandidateRunStatus@1', "
            "'status': 'running'}",
            "try:",
            *_rhino_metric_document_setup_lines(),
            textwrap.indent(translation.script, "    "),
            f"    with open({escaped['measures']!r}, 'w') as _f:",
            "        json.dump(measures, _f, sort_keys=True)",
            f"    with open({escaped['semantics']!r}, 'w') as _f:",
            "        json.dump({'objects': _semantics, 'blocks': _blocks}, "
            "_f, sort_keys=True)",
            "    _opts = Rhino.FileIO.FileWriteOptions()",
            "    _opts.SuppressDialogBoxes = True",
            "    _opts.SuppressAllInput = True",
            f"    _saved = Rhino.RhinoDoc.ActiveDoc.WriteFile({escaped['model']!r}, _opts)",
            "    _status = {'schema': 'P069RhinoCandidateRunStatus@1', "
            "'status': 'ok' if _saved else 'save_failed', "
            "'save_ok': bool(_saved), 'model_unit_system': 'Meters'}",
            "except Exception as _exc:",
            "    _status = {'schema': 'P069RhinoCandidateRunStatus@1', "
            "'status': 'error', 'error_type': type(_exc).__name__, "
            "'error': str(_exc), 'traceback': traceback.format_exc()} ",
            "finally:",
            f"    with open({escaped['status']!r}, 'w') as _f:",
            "        json.dump(_status, _f, sort_keys=True)",
            "    Rhino.RhinoApp.Exit()",
            "",
        )
    )
    wrapper_bytes = wrapper.encode("utf-8")
    wrapper_path.write_bytes(wrapper_bytes)
    wrapper_sha = hashlib.sha256(wrapper_bytes).hexdigest()
    destination = M.PersistenceDestination(
        M.PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="candidate-material-cad-workspace",
        payload={
            "schema": "P069CandidateCadWorkspace@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "program_digest": program.program_digest,
            "expected_model_unit_system": "Meters",
            "ledger": ledger.to_dict(),
            "coverage": coverage,
            "rhino_script_relative_path": wrapper_path.relative_to(
                repository.layout.root
            ).as_posix(),
            "rhino_script_sha256": wrapper_sha,
            "translation_losses": list(translation.losses),
            "expected_outputs": {
                key: Path(value).relative_to(repository.layout.root).as_posix()
                for key, value in escaped.items()
            },
            "disposition": "HOLD",
            "formal_export": False,
            "canonical_write_authority": False,
        },
    )
    repository.verify()
    return {
        "wrapper": wrapper_path,
        "model": model_path,
        "measures": measures_path,
        "semantics": semantics_path,
        "status": status_path,
        "record_ref": ref.uri,
    }


def _write_speculative_detail_rhino_workspace(
    result: dict[str, object],
    *,
    external_asset: dict[str, object] | None = None,
) -> dict[str, Path | str]:
    """Write the branch-scoped Stage-3D overlay to RUN_WORKSPACE only."""

    import textwrap
    from archflow.adapters.cad_program import translate_to_rhino_python
    from archflow.capabilities.material import (
        MaterialIntent,
        MaterialLedger,
        ledger_coverage,
    )

    repository = result["repository"]
    run = result["run"]
    program = result["final_program"]
    profile = result["runner_profile"]
    if not isinstance(profile, PantheonRunnerProfile):
        raise TypeError("result runner_profile must be a PantheonRunnerProfile")
    refs = adoption_refs(profile.project_id)
    ledger = MaterialLedger(
        intents=(
            MaterialIntent(
                material_id="grey-granite",
                label="candidate grey granite column shafts",
                source_refs=(refs["orders"],),
            ),
            MaterialIntent(
                material_id="roman-concrete",
                label="candidate Roman concrete drum and dome substrate",
                source_refs=(refs["walls"],),
            ),
        ),
        assignments=(
            ("colonnade", "grey-granite"),
            ("dome", "roman-concrete"),
            ("rotunda", "roman-concrete"),
            ("transition", "roman-concrete"),
        ),
    )
    bindings = [
        {
            "binding_id": item.binding_id,
            "component_id": item.component_id,
            "object_ids": list(item.object_ids),
        }
        for item in program.proposal.semantic_bindings
    ]
    coverage = ledger_coverage(bindings, ledger)
    translation = translate_to_rhino_python(
        program,
        material_by_component=ledger.component_map(),
        material_colors={
            item.material_id: item.color for item in ledger.intents
        },
    )
    plan = _detail_enrichment_plan(
        program,
        external_asset=external_asset,
        profile=profile,
    )
    plan.update({"project_id": run.project_id, "run_id": run.run_id})
    plan_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
        record_kind="detail-enrichment-plan",
        payload=plan,
    )
    overlay = _detail_rhino_overlay_script(
        plan,
        external_asset_path=(
            None
            if external_asset is None
            else Path(external_asset["resolved_path"])
        ),
    )

    workspace = (
        repository.layout.run(run.run_id).workspaces / "cad-stage-3d-detail"
    ).resolve()
    workspace.relative_to(
        repository.layout.run(run.run_id).workspaces.resolve()
    )
    workspace.mkdir(parents=True, exist_ok=False)
    wrapper_path = workspace / "pantheon-detail-candidate-wrapper.py"
    model_path = workspace / "pantheon-detail-candidate.3dm"
    measures_path = workspace / "cad-base-measures.json"
    semantics_path = workspace / "cad-base-semantics.json"
    detail_path = workspace / "cad-detail-measures.json"
    status_path = workspace / "cad-detail-run-status.json"
    escaped = {
        "model": str(model_path),
        "measures": str(measures_path),
        "semantics": str(semantics_path),
        "detail": str(detail_path),
        "status": str(status_path),
    }
    wrapper = "\n".join(
        (
            "# -*- coding: utf-8 -*-",
            "import json, traceback",
            "import Rhino",
            "_status = {'schema': 'P069RhinoDetailCandidateRunStatus@1', "
            "'status': 'running'}",
            "try:",
            *_rhino_metric_document_setup_lines(),
            textwrap.indent(translation.script, "    "),
            textwrap.indent(overlay, "    "),
            f"    with open({escaped['measures']!r}, 'w', encoding='utf-8') as _f:",
            "        json.dump(measures, _f, sort_keys=True)",
            f"    with open({escaped['semantics']!r}, 'w', encoding='utf-8') as _f:",
            "        json.dump({'objects': _semantics, 'blocks': _blocks}, "
            "_f, sort_keys=True)",
            f"    with open({escaped['detail']!r}, 'w', encoding='utf-8') as _f:",
            "        json.dump(_detail_measures, _f, sort_keys=True, "
            "ensure_ascii=False)",
            "    _opts = Rhino.FileIO.FileWriteOptions()",
            "    _opts.SuppressDialogBoxes = True",
            "    _opts.SuppressAllInput = True",
            f"    _saved = Rhino.RhinoDoc.ActiveDoc.WriteFile({escaped['model']!r}, _opts)",
            "    _status = {'schema': 'P069RhinoDetailCandidateRunStatus@1', "
            "'status': 'ok' if _saved else 'save_failed', "
            "'save_ok': bool(_saved), 'model_unit_system': 'Meters', "
            "'detail_counts': {"
            "'column_assemblies': _detail_measures['column_assemblies'], "
            "'door_leaves': _detail_measures['door_leaves'], "
            "'front_modillions': _detail_measures['front_modillions']}}",
            "except Exception as _exc:",
            "    _status = {'schema': 'P069RhinoDetailCandidateRunStatus@1', "
            "'status': 'error', 'error_type': type(_exc).__name__, "
            "'error': str(_exc), 'traceback': traceback.format_exc()} ",
            "finally:",
            f"    with open({escaped['status']!r}, 'w', encoding='utf-8') as _f:",
            "        json.dump(_status, _f, sort_keys=True, "
            "ensure_ascii=False)",
            "    Rhino.RhinoApp.Exit()",
            "",
        )
    )
    wrapper_bytes = wrapper.encode("utf-8")
    wrapper_path.write_bytes(wrapper_bytes)
    wrapper_sha = hashlib.sha256(wrapper_bytes).hexdigest()
    ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
        record_kind="candidate-detail-cad-workspace",
        payload={
            "schema": "P069DetailCadWorkspace@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "program_digest": program.program_digest,
            "expected_model_unit_system": "Meters",
            "detail_plan_ref": plan_ref.uri,
            "detail_branch_identity": plan["branch_scope"]["branch_identity"],
            "external_asset_record_ref": (
                None if external_asset is None else external_asset["record_ref"]
            ),
            "ledger": ledger.to_dict(),
            "coverage": coverage,
            "rhino_script_relative_path": wrapper_path.relative_to(
                repository.layout.root
            ).as_posix(),
            "rhino_script_sha256": wrapper_sha,
            "translation_losses": list(translation.losses),
            "expected_outputs": {
                key: Path(value).relative_to(
                    repository.layout.root
                ).as_posix()
                for key, value in escaped.items()
            },
            "massing_mutations": [],
            "disposition": "HOLD",
            "formal_export": False,
            "canonical_write_authority": False,
        },
    )
    repository.verify()
    return {
        "wrapper": wrapper_path,
        "model": model_path,
        "measures": measures_path,
        "semantics": semantics_path,
        "detail": detail_path,
        "status": status_path,
        "plan_ref": plan_ref.uri,
        "record_ref": ref.uri,
    }


def _candidate_cad_verification_summary(
    program,
    *,
    measures: dict[str, dict],
    semantics: dict[str, dict],
    strict_tolerance: float = 0.00001,
    contract_tolerance: float = 0.6,
) -> dict[str, object]:
    """Compare one external CAD realization at strict and contract scales."""

    from archflow.adapters.cad_program import (
        expected_object_bounds,
        expected_object_semantics,
    )
    from run_cad_equivalence import compare, compare_semantics

    expected = expected_object_bounds(program)
    expected_semantics = expected_object_semantics(program)
    strict_mismatches, max_deviation = compare(
        expected,
        measures,
        strict_tolerance,
    )
    contract_mismatches, _ = compare(
        expected,
        measures,
        contract_tolerance,
    )
    semantic_mismatches = compare_semantics(
        expected_semantics,
        semantics,
    )
    known_oracle_limit = None
    if (
        len(strict_mismatches) == 1
        and strict_mismatches[0].get("object_id")
        == "transition-block-object"
        and strict_mismatches[0].get("code") == "bounds_deviation"
        and not contract_mismatches
    ):
        known_oracle_limit = {
            "code": "boolean-difference-base-envelope-conservative",
            "explanation": (
                "the analytic oracle preserves the base envelope for a "
                "boolean difference while Rhino measures the circular rear "
                "cut on the transition block"
            ),
        }
    return {
        "object_count": len(expected),
        "instance_count": sum(
            int(item["brep_count"]) for item in expected.values()
        ),
        "max_abs_deviation": max_deviation,
        "strict": {
            "tolerance": strict_tolerance,
            "status": (
                "equivalent" if not strict_mismatches else "diverged"
            ),
            "mismatches": strict_mismatches,
        },
        "contract": {
            "tolerance": contract_tolerance,
            "status": (
                "equivalent" if not contract_mismatches else "diverged"
            ),
            "mismatches": contract_mismatches,
        },
        "semantics": {
            "status": (
                "verified" if not semantic_mismatches else "diverged"
            ),
            "mismatches": semantic_mismatches,
        },
        "strict_difference_classification": known_oracle_limit,
    }


def _headless_three_dm_gate(
    model_path: Path,
    status: Mapping[str, object],
    *,
    expected_object_count: int | None = None,
) -> dict[str, object]:
    """Verify saved model identity and metre units without starting Rhino."""

    from archflow.adapters.three_dm_inspector import inspect_three_dm

    inspection = inspect_three_dm(model_path)
    issues = []
    if inspection.units.get("name") != "Meters":
        issues.append(
            "saved 3dm unit system is not Meters: "
            + str(inspection.units.get("name"))
        )
    if status.get("model_unit_system") != "Meters":
        issues.append("Rhino run status did not attest Meters")
    if (
        expected_object_count is not None
        and inspection.object_count != expected_object_count
    ):
        issues.append(
            "saved 3dm object count diverged: "
            f"expected {expected_object_count}, got {inspection.object_count}"
        )
    return {
        "schema": "P069HeadlessThreeDmGate@1",
        "file_sha256": inspection.file_sha256,
        "file_bytes": inspection.file_bytes,
        "three_dm_version": inspection.three_dm_version,
        "archive_version": inspection.archive_version,
        "units": inspection.units,
        "object_count": inspection.object_count,
        "top_level_object_count": inspection.top_level_object_count,
        "layer_count": len(inspection.layers),
        "instance_definition_count": len(inspection.instance_definitions),
        "instance_reference_count": len(inspection.instance_references),
        "aggregate_bbox": inspection.aggregate_bbox,
        "issues": issues,
        "passed": not issues,
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
    }


def _record_candidate_cad_execution(
    root: Path,
    *,
    run_id: str,
    captured_at: str,
) -> dict[str, object]:
    """Retain a hash-bound, non-canonical receipt for external Rhino output."""

    from run_cad_equivalence import load_program_shim

    repository = FilesystemProjectRepository.open(root)
    run = repository.load_run(run_id)
    layout = repository.layout.run(run_id)
    program_paths = sorted(
        layout.candidates.glob("geometry-program-stage-3-*.json")
    )
    workspace_records = sorted(
        layout.records.glob("candidate-material-cad-workspace-*.json")
    )
    if len(program_paths) != 1 or len(workspace_records) != 1:
        raise RuntimeError(
            "candidate CAD verification requires exactly one stage-3 "
            "program and one CAD workspace record"
        )
    program_path = program_paths[0]
    workspace_record_path = workspace_records[0]
    workspace_record = json.loads(
        workspace_record_path.read_text(encoding="utf-8")
    )
    root_resolved = repository.layout.root.resolve()

    def project_file(relative: str) -> Path:
        path = (root_resolved / relative).resolve()
        path.relative_to(root_resolved)
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"missing or empty CAD artifact: {relative}")
        return path

    wrapper_path = project_file(
        workspace_record["rhino_script_relative_path"]
    )
    output_paths = {
        key: project_file(value)
        for key, value in workspace_record["expected_outputs"].items()
    }
    wrapper_sha = hashlib.sha256(wrapper_path.read_bytes()).hexdigest()
    script_sha_matches = (
        wrapper_sha == workspace_record["rhino_script_sha256"]
    )
    status = json.loads(output_paths["status"].read_text(encoding="utf-8"))
    measures = json.loads(
        output_paths["measures"].read_text(encoding="utf-8")
    )
    semantics = json.loads(
        output_paths["semantics"].read_text(encoding="utf-8")
    )
    verification = _candidate_cad_verification_summary(
        load_program_shim(program_path),
        measures=measures,
        semantics=semantics,
    )
    model_gate = _headless_three_dm_gate(
        output_paths["model"],
        status,
        expected_object_count=int(verification["instance_count"]),
    )
    artifacts = {}
    for key, path in {
        "wrapper": wrapper_path,
        **output_paths,
    }.items():
        data = path.read_bytes()
        artifacts[key] = {
            "relative_path": path.relative_to(root_resolved).as_posix(),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    model_gate["file_sha_matches_artifact"] = (
        model_gate["file_sha256"] == artifacts["model"]["sha256"]
    )
    if not model_gate["file_sha_matches_artifact"]:
        model_gate["issues"].append("3dm changed after headless inspection")
        model_gate["passed"] = False
    verified = bool(
        script_sha_matches
        and status.get("status") == "ok"
        and status.get("save_ok") is True
        and verification["contract"]["status"] == "equivalent"
        and verification["semantics"]["status"] == "verified"
        and model_gate["passed"] is True
    )
    project_id = run.project_id
    prefix = f"project://{project_id}/runs/{run_id}"
    payload = {
        "schema": "P069CandidateCadExecutionReceipt@1",
        "project_id": project_id,
        "run_id": run_id,
        "captured_at": captured_at,
        "adapter_id": "rhino-8.28-desktop-python",
        "program_ref": f"{prefix}/candidates/{program_path.name}",
        "workspace_record_ref": (
            f"{prefix}/records/{workspace_record_path.name}"
        ),
        "program_digest": workspace_record["program_digest"],
        "script_sha_matches_workspace_record": script_sha_matches,
        "run_status": status,
        "artifacts": artifacts,
        "verification": verification,
        "headless_model_gate": model_gate,
        "candidate_execution_verified": verified,
        "disposition": "HOLD",
        "formal_export": False,
        "accepted_archive_created": False,
        "canonical_write_authority": False,
    }
    ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run_id,
        ),
        record_kind="candidate-cad-execution",
        payload=payload,
    )
    repository.verify()
    return {"record_ref": ref.uri, **payload}


def _record_candidate_detail_cad_execution(
    root: Path,
    *,
    run_id: str,
    captured_at: str,
) -> dict[str, object]:
    """Verify the base CAD contract and the Stage-3D detail overlay."""

    from run_cad_equivalence import load_program_shim

    repository = FilesystemProjectRepository.open(root)
    run = repository.load_run(run_id)
    layout = repository.layout.run(run_id)
    program_paths = sorted(
        layout.candidates.glob("geometry-program-stage-3-*.json")
    )
    workspace_records = sorted(
        layout.records.glob("candidate-detail-cad-workspace-*.json")
    )
    if len(program_paths) != 1 or len(workspace_records) != 1:
        raise RuntimeError(
            "detail CAD verification requires exactly one stage-3 program "
            "and one detail CAD workspace record"
        )
    program_path = program_paths[0]
    workspace_record_path = workspace_records[0]
    workspace_record = json.loads(
        workspace_record_path.read_text(encoding="utf-8")
    )
    root_resolved = repository.layout.root.resolve()

    def project_file(relative: str) -> Path:
        path = (root_resolved / relative).resolve()
        path.relative_to(root_resolved)
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(
                f"missing or empty detail CAD artifact: {relative}"
            )
        return path

    wrapper_path = project_file(
        workspace_record["rhino_script_relative_path"]
    )
    output_paths = {
        key: project_file(value)
        for key, value in workspace_record["expected_outputs"].items()
    }
    wrapper_sha = hashlib.sha256(wrapper_path.read_bytes()).hexdigest()
    script_sha_matches = (
        wrapper_sha == workspace_record["rhino_script_sha256"]
    )
    status = json.loads(output_paths["status"].read_text(encoding="utf-8"))
    measures = json.loads(
        output_paths["measures"].read_text(encoding="utf-8")
    )
    semantics = json.loads(
        output_paths["semantics"].read_text(encoding="utf-8")
    )
    detail = json.loads(
        output_paths["detail"].read_text(encoding="utf-8")
    )
    base_verification = _candidate_cad_verification_summary(
        load_program_shim(program_path),
        measures=measures,
        semantics=semantics,
    )
    model_gate = _headless_three_dm_gate(output_paths["model"], status)
    detail_issues = []
    expected = {
        "column_assemblies": 16,
        "front_grey_column_assemblies": 8,
        "inner_rose_column_assemblies": 8,
        "door_leaves": 2,
        "front_modillions": 47,
    }
    for field, value in expected.items():
        if detail.get(field) != value:
            detail_issues.append(
                f"{field}: expected {value!r}, got {detail.get(field)!r}"
            )
    if detail.get("primary_inscription_text") != DETAIL_INSCRIPTION:
        detail_issues.append("primary inscription text diverged")
    if int(detail.get("primary_inscription_relief_count", 0)) <= 0:
        detail_issues.append("primary inscription produced no relief geometry")
    if detail.get("massing_mutations") != []:
        detail_issues.append("detail overlay declared a massing mutation")
    if (
        detail.get("predecessor_program_digest")
        != workspace_record["program_digest"]
    ):
        detail_issues.append("detail predecessor digest diverged")
    external_asset_required = (
        workspace_record.get("external_asset_record_ref") is not None
    )
    if external_asset_required:
        if detail.get("external_capital_import_error") is not None:
            detail_issues.append(
                "external capital import failed: "
                + str(detail["external_capital_import_error"])
            )
        if detail.get("external_capital_instances_hidden") != 16:
            detail_issues.append(
                "external review candidate did not instantiate 16 capitals"
            )
    if workspace_record.get("translation_losses") != []:
        detail_issues.append("base neutral CAD translation retained losses")
    artifacts = {}
    for key, path in {"wrapper": wrapper_path, **output_paths}.items():
        data = path.read_bytes()
        artifacts[key] = {
            "relative_path": path.relative_to(root_resolved).as_posix(),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    model_gate["file_sha_matches_artifact"] = (
        model_gate["file_sha256"] == artifacts["model"]["sha256"]
    )
    if not model_gate["file_sha_matches_artifact"]:
        model_gate["issues"].append("3dm changed after headless inspection")
        model_gate["passed"] = False
    verified = bool(
        script_sha_matches
        and status.get("status") == "ok"
        and status.get("save_ok") is True
        and base_verification["contract"]["status"] == "equivalent"
        and base_verification["semantics"]["status"] == "verified"
        and model_gate["passed"] is True
        and not detail_issues
    )
    project_id = run.project_id
    prefix = f"project://{project_id}/runs/{run_id}"
    payload = {
        "schema": "P069DetailCadExecutionReceipt@1",
        "project_id": project_id,
        "run_id": run_id,
        "captured_at": captured_at,
        "adapter_id": "rhino-8.28-desktop-python-detail-overlay",
        "program_ref": f"{prefix}/candidates/{program_path.name}",
        "workspace_record_ref": (
            f"{prefix}/records/{workspace_record_path.name}"
        ),
        "detail_plan_ref": workspace_record["detail_plan_ref"],
        "program_digest": workspace_record["program_digest"],
        "detail_branch_identity": workspace_record[
            "detail_branch_identity"
        ],
        "script_sha_matches_workspace_record": script_sha_matches,
        "run_status": status,
        "artifacts": artifacts,
        "base_verification": base_verification,
        "headless_model_gate": model_gate,
        "detail_measures": detail,
        "detail_issues": detail_issues,
        "candidate_detail_execution_verified": verified,
        "disposition": "HOLD",
        "formal_export": False,
        "accepted_archive_created": False,
        "canonical_write_authority": False,
    }
    ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run_id,
        ),
        record_kind="candidate-detail-cad-execution",
        payload=payload,
    )
    repository.verify()
    return {"record_ref": ref.uri, **payload}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", default=PROJECT_ID)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--source-run-id", default=SOURCE_RUN_ID)
    parser.add_argument(
        "--mode",
        choices=(
            "candidate",
            "detail",
            "formal",
            "verify-cad",
            "verify-detail",
        ),
        default="candidate",
    )
    parser.add_argument("--captured-at")
    parser.add_argument(
        "--capital-asset",
        help="local STL downloaded with user authority; ingested, never persisted by path",
    )
    parser.add_argument(
        "--capital-license",
        help="bundled license file required with --capital-asset",
    )
    parser.add_argument(
        "--capital-readme",
        help="optional bundled source README retained with the asset",
    )
    args = parser.parse_args(argv)

    if args.project_id != PROJECT_ID:
        raise SystemExit(
            "this project-local runner cannot author another project id"
        )
    if args.mode == "formal":
        print(
            "FORMAL HOLD: P079 branch scope is bound to one operational "
            "state and cannot yet prove the three exact successor P080 "
            "states; no run was created."
        )
        return 2
    if args.mode == "verify-cad":
        if not args.captured_at:
            raise SystemExit("--captured-at is required with --mode verify-cad")
        receipt = _record_candidate_cad_execution(
            resolve_probe_root(args.project_id),
            run_id=args.run_id,
            captured_at=args.captured_at,
        )
        print("candidate CAD receipt:", receipt["record_ref"])
        print(
            "strict / contract / semantics:",
            receipt["verification"]["strict"]["status"],
            receipt["verification"]["contract"]["status"],
            receipt["verification"]["semantics"]["status"],
        )
        print("candidate execution verified:", receipt["candidate_execution_verified"])
        return 0 if receipt["candidate_execution_verified"] else 1
    if args.mode == "verify-detail":
        if not args.captured_at:
            raise SystemExit(
                "--captured-at is required with --mode verify-detail"
            )
        receipt = _record_candidate_detail_cad_execution(
            resolve_probe_root(args.project_id),
            run_id=args.run_id,
            captured_at=args.captured_at,
        )
        print("detail CAD receipt:", receipt["record_ref"])
        print(
            "base contract / semantics / detail issues:",
            receipt["base_verification"]["contract"]["status"],
            receipt["base_verification"]["semantics"]["status"],
            len(receipt["detail_issues"]),
        )
        print(
            "candidate detail execution verified:",
            receipt["candidate_detail_execution_verified"],
        )
        return (
            0 if receipt["candidate_detail_execution_verified"] else 1
        )

    if args.mode != "detail" and any(
        (args.capital_asset, args.capital_license, args.capital_readme)
    ):
        raise SystemExit("capital asset arguments belong only to --mode detail")
    if bool(args.capital_asset) != bool(args.capital_license):
        raise SystemExit(
            "--capital-asset and --capital-license must be supplied together"
        )

    runner_context = install(run_id=args.run_id)
    root = resolve_probe_root(args.project_id)
    result = _run_hold_candidate(
        root,
        run_id=args.run_id,
        source_run_id=args.source_run_id,
        runner_context=runner_context,
    )
    cad = _write_speculative_rhino_workspace(result)
    detail_cad = None
    if args.mode == "detail":
        external_asset = None
        if args.capital_asset:
            external_asset = _ingest_detail_asset(
                result,
                asset_path=Path(args.capital_asset).resolve(),
                license_path=Path(args.capital_license).resolve(),
                readme_path=(
                    None
                    if args.capital_readme is None
                    else Path(args.capital_readme).resolve()
                ),
            )
        detail_cad = _write_speculative_detail_rhino_workspace(
            result,
            external_asset=external_asset,
        )
    print("candidate run:", result["run"].run_id)
    print("provider invocations:", result["provider_invocations"])
    print(
        "stage checks:",
        [item["checks_status"] for item in result["stages"]],
    )
    print("disposition: HOLD (no ACCEPTED stage archive)")
    print("manifest:", result["manifest_ref"].uri)
    print("Rhino wrapper:", cad["wrapper"])
    if detail_cad is not None:
        print("detail plan:", detail_cad["plan_ref"])
        print("detail Rhino wrapper:", detail_cad["wrapper"])
    return 0 if all(
        item["checks_status"] == "pass" for item in result["stages"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
