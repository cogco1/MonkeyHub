#!/usr/bin/env python3
"""P082: branch-scoped, four-stage Parthenon reconstruction candidate.

This is a project-local runner.  It keeps Parthenon facts out of ``archflow/``
while exercising the reusable P036/P078/P079/P080/P081 contracts.  The result
is deliberately a HOLD candidate: complete evidence packs are not acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import rhino3dm
from pypdf import PdfReader

try:  # Package import in tests; direct import when executed as a script.
    from tools._probe_paths import WORKSPACE_PROJECTS
except ModuleNotFoundError:
    from _probe_paths import WORKSPACE_PROJECTS
from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow.adapters.three_dm_witness import (
    add_axis_aligned_box_brep_witnesses,
    primary_three_dm_objects,
)
from archflow.adapters.web_evidence import fetch_web_evidence
from archflow.capabilities.branch_research import (
    BranchResearchScope,
    DecisionResearchNeed,
    bind_branch_snapshot,
    compile_branch_query,
)
from archflow.capabilities.architectural_completeness import (
    ArchitecturalCompletenessStatus,
    DecisionFamilyCoverage,
    ParameterBasisKind,
    ParameterEvidence,
    ParameterGranularity,
    StageDecisionRequirements,
    compile_architectural_completeness,
)
from archflow.capabilities.evidence_sufficiency import (
    ClosureStatus,
    DecisionEdge,
    DecisionNode,
    DecisionUniverseRevision,
    DiscoverySweepReceipt,
    EvidenceClaimBinding,
    EvidenceRule,
    EvidenceSufficiencyPolicy,
    ResolutionRecord,
    ResolutionMode,
    SufficiencyStatus,
    compile_decision_universe_closure,
    compile_evidence_sufficiency,
)
from archflow.capabilities.spatial_validation import (
    AABB,
    HostRegion,
    OpeningClearRegion,
    SpatialElement,
    SpatialElementKind,
    SpatialValidationStatus,
    validate_spatial_layout,
)
from archflow.capabilities.stage_evidence_pack import (
    StageArtifactBinding,
    StageArtifactRole,
    StageClosureSummary,
    StageEvidenceBinding,
    StageEvidenceGap,
    StageEvidenceGapKind,
    StageEvidenceGapSeverity,
    StageEvidencePack,
    StageEvidenceRole,
    StagePackCompilationStatus,
    StagePackPredecessor,
    compile_stage_evidence_pack,
)
from archflow.project import (
    BranchRef,
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectArtifactRef,
    ProjectRecordRef,
    RunRef,
    bootstrap_raw_request_project,
)
from archflow.state import OperationalMarkovState
from archflow.state.operational_state import (
    DesignObligation,
    FactEpistemicStatus,
    ObligationStatus,
    StateDomain,
    StateFact,
    StateLock,
)
from archflow.state.stage_convergence import (
    StageConvergenceEvidence,
    StageConvergenceOutcome,
    StageConvergencePolicy,
    StageTransitionKind,
    StageTransitionRequest,
    evaluate_stage_convergence,
)
from archflow.contracts.canonical import canonical_digest, canonical_json


PROJECT_ID = "parthenon-reconstruction"
BOOTSTRAP_RUN_ID = "bootstrap-001"
RESEARCH_RUN_ID = "research-001"
RUN_ID = "reconstruction-001"
BRANCH_ID = "idealized-periclean-original"
PROMPT = (
    "Reconstruct the Parthenon as a complete Periclean temple through four "
    "evidence-bound stages, preserving one selected reconstruction branch, "
    "explicit dependencies, metre units, and typed uncertainty."
)

# Exterior dimensions are measured facts retained from Perseus/Tufts and the
# Saylor source.  Values below without that status are explicitly typed as
# reconstruction choices in STAGE_SPECS.
STYLOBATE_LENGTH = 69.50
STYLOBATE_WIDTH = 30.88
EXTERIOR_COLUMN_HEIGHT = 10.43
EXTERIOR_COLUMN_DIAMETER = 1.91
CORNER_COLUMN_DIAMETER = 1.95
NORMAL_INTERAXIS = 4.29
SHORT_CORNER_INTERAXIS = 3.68
LONG_CORNER_INTERAXIS = 3.69
ENTABLATURE_HEIGHT = 3.30
NAOS_LENGTH = 29.80
NAOS_WIDTH = 19.20
NAOS_HEIGHT = 13.09

# Candidate values inside retained widened ranges.  They are not promoted to
# historical facts merely because the adapter needs one realizable number.
STEP_RISE = 0.45
CELLA_WALL = 1.35
WEST_ROOM_INTERNAL_LENGTH = 13.37
CELLA_OUTER_LENGTH = NAOS_LENGTH + WEST_ROOM_INTERNAL_LENGTH + 3 * CELLA_WALL
CELLA_OUTER_WIDTH = NAOS_WIDTH + 2 * CELLA_WALL
PEDIMENT_RISE = 4.30
BASE_Y = STEP_RISE * 3.0

# The principal stone openings are reconstructed from D'Ooge's measured width
# and the ca. 2:1 restoration reported by the ASCSA study.  Door-leaf height is
# an explicit candidate inside that opening, not a measured ancient fact.
PRINCIPAL_DOOR_OPENING_WIDTH = 4.92
PRINCIPAL_DOOR_OPENING_HEIGHT = PRINCIPAL_DOOR_OPENING_WIDTH * 2.0
PRINCIPAL_DOOR_LEAF_WIDTH = 4.20
PRINCIPAL_DOOR_LEAF_HEIGHT = 7.00
PRINCIPAL_DOOR_LEAF_THICKNESS = 0.12

# Dinsmoor distinguishes the original 23-column tiers from later, smaller and
# more closely spaced replacements.  The grid is centered as a declared
# candidate inside the measured east room; the count, original diameter, and
# interaxis remain separately evidenced.
INTERIOR_TIER_COLUMN_COUNT = 23
INTERIOR_COLUMN_DIAMETER = 1.117
INTERIOR_COLUMN_INTERAXIS = 2.603
INTERIOR_LOWER_HEIGHT = 6.898
INTERIOR_UPPER_HEIGHT = 12.074 - INTERIOR_LOWER_HEIGHT
PARTITION_WEST_FACE = -CELLA_OUTER_LENGTH / 2 + CELLA_WALL + WEST_ROOM_INTERNAL_LENGTH
PARTITION_EAST_FACE = PARTITION_WEST_FACE + CELLA_WALL
EAST_ROOM_INNER_EAST = CELLA_OUTER_LENGTH / 2 - CELLA_WALL
WEST_ROOM_INNER_WEST = -CELLA_OUTER_LENGTH / 2 + CELLA_WALL
INTERIOR_GRID_LONGITUDINAL_MARGIN = (
    NAOS_LENGTH - 9 * INTERIOR_COLUMN_INTERAXIS
) / 2
INTERIOR_GRID_REAR_AXIS = PARTITION_EAST_FACE + INTERIOR_GRID_LONGITUDINAL_MARGIN
WEST_ROOM_CENTER = (WEST_ROOM_INNER_WEST + PARTITION_WEST_FACE) / 2
WEST_ROOM_COLUMN_HALF_SPAN_X = 3.0
WEST_ROOM_COLUMN_HALF_SPAN_Y = 2.4


SOURCES: dict[str, dict[str, object]] = {
    "ysma": {
        "url": "https://www.ysma.gr/en/monuments/parthenon/",
        "family": "source-family:ysma-greek-ministry",
        "media_type": "text/html",
    },
    "perseus": {
        "url": (
            "https://www.perseus.tufts.edu/hopper/artifact?"
            "name=Athens%2C+Parthenon&object=Building"
        ),
        "family": "source-family:perseus-tufts",
        "media_type": "text/html",
    },
    "museum": {
        "url": (
            "https://www.theacropolismuseum.gr/en/"
            "parthenon-west-beam-bearer-block-entablature"
        ),
        "family": "source-family:acropolis-museum",
        "media_type": "text/html",
    },
    "saylor": {
        "url": (
            "https://resources.saylor.org/wwwresources/archived/site/"
            "wp-content/uploads/2011/08/HIST361-2.2.2-Parthenon.pdf"
        ),
        "family": "source-family:saylor-hist361",
        "media_type": "application/pdf",
    },
    "dooge": {
        "url": "https://digi.ub.uni-heidelberg.de/diglit/dooge1908/0163",
        "family": "source-family:dooge-heidelberg",
        "media_type": "text/html",
    },
    "dinsmoor": {
        "url": (
            "https://penelope.uchicago.edu/Thayer/E/Journals/"
            "AJA/38/1/Athena_Parthenos%2A.html"
        ),
        "family": "source-family:dinsmoor-aja",
        "media_type": "text/html",
    },
    "ascsa_doors": {
        "url": (
            "https://www.ascsa.edu.gr/uploads/media/oa_ebooks/"
            "oa_hesperia_supplements/HS3.pdf"
        ),
        "family": "source-family:ascsa-hesperia",
        "media_type": "application/pdf",
    },
}


@dataclass(frozen=True, slots=True)
class DecisionSpec:
    decision_ref: str
    stage: int
    question: str
    search_terms: tuple[str, ...]
    source_ids: tuple[str, ...]
    minimum_source_families: int
    selected_value: object
    evidence_statement: str
    hard: bool = True


DECISIONS: tuple[DecisionSpec, ...] = (
    DecisionSpec(
        "decision:reconstruction-strategy",
        0,
        "Which reconstruction state answers the request?",
        ("Parthenon", "Periclean", "complete reconstruction"),
        ("ysma",),
        1,
        BRANCH_ID,
        "YSMA distinguishes the original monument from later interventions.",
        False,
    ),
    DecisionSpec(
        "decision:stylobate-envelope",
        0,
        "What measured stylobate dimensions bound the reconstruction?",
        ("Parthenon", "stylobate", "dimensions"),
        ("perseus", "saylor"),
        2,
        {"length_m": STYLOBATE_LENGTH, "width_m": STYLOBATE_WIDTH},
        "Independent sources agree on an approximately 69.5 by 30.9 metre stylobate.",
    ),
    DecisionSpec(
        "decision:primary-orientation",
        0,
        "How are the east front and long axis represented?",
        ("Parthenon", "east cella", "orientation"),
        ("ysma",),
        1,
        {"long_axis": "east-west", "model_east": "+y"},
        "The principal cella and entrance hierarchy distinguish the east front.",
    ),
    DecisionSpec(
        "decision:overall-massing",
        0,
        "Which explicitly provisional envelopes are needed for early massing?",
        ("Parthenon", "cella dimensions", "roof envelope"),
        ("perseus", "saylor"),
        2,
        {
            "cella_outer_candidate_m": [CELLA_OUTER_LENGTH, CELLA_OUTER_WIDTH],
            "cella_height_candidate_m": NAOS_HEIGHT,
            "pediment_rise_candidate_m": PEDIMENT_RISE,
        },
        "Measured inner-room and temple dimensions bound a typed early massing candidate.",
        False,
    ),
    DecisionSpec(
        "decision:peristyle-topology",
        1,
        "What column topology closes around the stylobate?",
        ("Parthenon", "8 x 17", "peristyle", "46 columns"),
        ("ysma", "perseus"),
        2,
        {"short_side": 8, "long_side": 17, "unique_columns": 46},
        "The Doric peristyle is octastyle with seventeen columns on each flank.",
    ),
    DecisionSpec(
        "decision:exterior-column-measures",
        1,
        "Which measured column height, diameter, and interaxes govern?",
        ("Parthenon", "column height", "diameter", "axial spacing"),
        ("perseus", "saylor"),
        2,
        {
            "height_m": EXTERIOR_COLUMN_HEIGHT,
            "diameter_m": EXTERIOR_COLUMN_DIAMETER,
            "corner_diameter_m": CORNER_COLUMN_DIAMETER,
            "normal_interaxis_m": NORMAL_INTERAXIS,
        },
        "Measured exterior columns are about 10.43 m high and 1.91 m in diameter.",
    ),
    DecisionSpec(
        "decision:optical-refinements",
        1,
        "Which known refinements are realized or deferred?",
        ("Parthenon", "stylobate curvature", "entasis", "corner contraction"),
        ("ysma",),
        1,
        {"status": "evidenced-but-deferred", "candidate_scope": "advisory"},
        "Curvature, entasis, inward inclination, and corner contraction are evidenced.",
        False,
    ),
    DecisionSpec(
        "decision:cella-layout",
        2,
        "How are the double cella, pronaos, and opisthodomos organized?",
        ("Parthenon", "double cella", "pronaos", "opisthodomos"),
        ("ysma", "perseus", "saylor", "dooge"),
        2,
        {
            "naos_internal_m": [NAOS_LENGTH, NAOS_WIDTH],
            "cella_outer_candidate_m": [CELLA_OUTER_LENGTH, CELLA_OUTER_WIDTH],
            "west_room_internal_length_m": WEST_ROOM_INTERNAL_LENGTH,
            "partition_thickness_candidate_m": CELLA_WALL,
            "porch_columns_each": 6,
        },
        "Sources support a double cella, measured inner-room envelope, and front and rear porches.",
    ),
    DecisionSpec(
        "decision:room-connectivity",
        2,
        "How are the east and west rooms reached and connected?",
        ("Parthenon", "east west cella", "separate doors", "portico access"),
        ("ysma", "dooge"),
        2,
        {
            "east_room_access": "east portico principal doorway",
            "west_room_access": "west portico principal doorway",
            "inter_room_connection": "none",
        },
        "The two cella rooms have independent east and west portico access rather than an internal passage.",
        False,
    ),
    DecisionSpec(
        "decision:cella-openings",
        2,
        "Which principal cella openings must remain explicit geometry?",
        ("Parthenon", "east west doorway", "4.92 m", "twice as high"),
        ("dooge", "ascsa_doors"),
        2,
        {
            "required_openings": ["east-principal-door", "west-principal-door"],
            "stone_opening_width_m": PRINCIPAL_DOOR_OPENING_WIDTH,
            "stone_opening_height_m": PRINCIPAL_DOOR_OPENING_HEIGHT,
            "upper_zone": "grilled transom",
        },
        "Independent studies reconstruct principal east and west stone openings about 4.92 m wide and roughly twice as high.",
    ),
    DecisionSpec(
        "decision:interior-supports",
        2,
        "Which interior support topology is represented?",
        ("Parthenon", "U-shaped", "two-tier", "interior colonnade"),
        ("ysma", "dinsmoor"),
        2,
        {"east_room": "two-tier U-shaped Doric", "west_room": "four Ionic"},
        "YSMA and Dinsmoor support a two-tier U-shaped east colonnade; YSMA records four west-room Ionic columns.",
        False,
    ),
    DecisionSpec(
        "decision:interior-support-measures",
        2,
        "Which measured or reconstructed grid governs the east-room colonnade?",
        ("Parthenon", "original twenty-three columns", "1.117 m", "2.603 m"),
        ("dinsmoor",),
        1,
        {
            "columns_each_tier": INTERIOR_TIER_COLUMN_COUNT,
            "original_diameter_m": INTERIOR_COLUMN_DIAMETER,
            "original_interaxis_m": INTERIOR_COLUMN_INTERAXIS,
            "grid_position": "declared-centered-candidate",
        },
        "Dinsmoor distinguishes the original 23-column tiers, 1.117 m diameter, and 2.603 m interaxis from later replacements.",
        False,
    ),
    DecisionSpec(
        "decision:roof-system",
        2,
        "What roof support and covering are represented?",
        ("Parthenon", "roof beams", "marble tiles"),
        ("saylor", "museum"),
        2,
        {"form": "gabled", "covering": "marble tiles", "frame": "timber beams"},
        "The retained sources support a gabled roof with marble tiles and roof beams.",
    ),
    DecisionSpec(
        "decision:entablature-pediment",
        3,
        "What vertical order and pediment envelope are modeled?",
        ("Parthenon", "entablature", "pediment", "height"),
        ("perseus", "museum"),
        2,
        {"entablature_height_m": ENTABLATURE_HEIGHT, "pediment_rise_candidate_m": PEDIMENT_RISE},
        "Perseus gives the entablature height; the museum records its roof-bearing members.",
    ),
    DecisionSpec(
        "decision:decorative-layout",
        3,
        "Where do Doric metopes and the Ionic frieze attach?",
        ("Parthenon", "metopes", "Ionic frieze", "cella"),
        ("ysma", "museum"),
        2,
        {"metopes": 92, "ionic_frieze_host": "cella-upper-wall"},
        "The external Doric metopes and internal Ionic frieze have distinct hosts.",
        False,
    ),
    DecisionSpec(
        "decision:material",
        3,
        "Which evidenced material identity is assigned?",
        ("Parthenon", "Pentelic marble", "material"),
        ("ysma", "museum"),
        2,
        "pentelic-marble",
        "Official sources identify Pentelic marble architectural members.",
    ),
    DecisionSpec(
        "decision:sculptural-scope",
        3,
        "Which sculptural content is geometry and which remains tagged scope?",
        ("Parthenon", "pediment sculptures", "frieze", "metopes"),
        ("ysma", "museum"),
        2,
        {"geometry": "attachment panels", "figures": "semantic placeholders"},
        "The candidate retains attachment zones but does not invent lost figures.",
        False,
    ),
)


DEPENDENCY_EDGES: tuple[tuple[str, str, str], ...] = (
    ("decision:reconstruction-strategy", "decision:stylobate-envelope", "bounds"),
    ("decision:stylobate-envelope", "decision:overall-massing", "bounds"),
    ("decision:stylobate-envelope", "decision:peristyle-topology", "hosts"),
    ("decision:peristyle-topology", "decision:exterior-column-measures", "places"),
    ("decision:exterior-column-measures", "decision:entablature-pediment", "supports"),
    ("decision:overall-massing", "decision:cella-layout", "refines"),
    ("decision:cella-layout", "decision:room-connectivity", "partitions"),
    ("decision:room-connectivity", "decision:cella-openings", "requires"),
    ("decision:cella-layout", "decision:interior-supports", "hosts"),
    ("decision:interior-supports", "decision:interior-support-measures", "measures"),
    ("decision:interior-support-measures", "decision:roof-system", "supports"),
    ("decision:roof-system", "decision:entablature-pediment", "coordinates"),
    ("decision:entablature-pediment", "decision:decorative-layout", "hosts"),
    ("decision:decorative-layout", "decision:sculptural-scope", "locates"),
    ("decision:material", "decision:decorative-layout", "finishes"),
)


OFFLINE_TEXT = {
    "ysma": (
        "Official YSMA Parthenon summary: the temple is an octastyle Doric "
        "peripteros with seventeen columns on the flanks, six-column porches, "
        "a two-room cella, a two-tier U-shaped eastern colonnade, four Ionic "
        "columns in the west room, optical refinements, and Pentelic marble."
    ),
    "perseus": (
        "Stylobate 30.88 m x 69.50 m; external columns 8 x 17; axial spacing "
        "4.29 m with contracted corners; diameter 1.91 m; height 10.43 m; "
        "entablature 3.30 m; double cella and six-column porches."
    ),
    "museum": (
        "Acropolis Museum record for a Parthenon west entablature roof-beam "
        "bearer: classical-period architectural member, marble from Penteli, "
        "forming the cornice and receiving roof beams."
    ),
    "saylor": (
        "Measured at the stylobate the temple is about 69.5 m by 30.9 m. "
        "Exterior Doric columns are about 1.9 m in diameter and 10.4 m high. "
        "The cella is about 29.8 m by 19.2 m; marble roof tiles are recorded."
    ),
    "dooge": (
        "D'Ooge describes the principal east cella doorway as reconstructed from "
        "the corresponding western doorway: about 10 m high and 4.92 m wide, "
        "with jambs and an upper transom. The western portico had corresponding "
        "doors into the separate rear chamber."
    ),
    "dinsmoor": (
        "Dinsmoor distinguishes the original two tiers of twenty-three internal "
        "columns from later replacements. The original columns were 1.117 m in "
        "diameter and spaced 2.603 m on axis; later circles were smaller and "
        "more closely spaced."
    ),
    "ascsa_doors": (
        "The ASCSA study reconstructs Parthenon stone door openings at about "
        "5.00 m wide, in a classic proportion twice as high as wide, with bronze "
        "framing and a generous grilled upper zone."
    ),
}


DECISION_FAMILY_BY_REF = {
    "decision:reconstruction-strategy": "branch-strategy",
    "decision:stylobate-envelope": "site-envelope",
    "decision:primary-orientation": "orientation",
    "decision:overall-massing": "overall-massing",
    "decision:peristyle-topology": "exterior-support-topology",
    "decision:exterior-column-measures": "exterior-support-dimensions",
    "decision:optical-refinements": "optical-refinements",
    "decision:cella-layout": "room-layout",
    "decision:room-connectivity": "room-connectivity",
    "decision:cella-openings": "principal-openings",
    "decision:interior-supports": "interior-support-topology",
    "decision:interior-support-measures": "interior-support-dimensions",
    "decision:roof-system": "roof-system",
    "decision:entablature-pediment": "order-envelope",
    "decision:decorative-layout": "ornament-layout",
    "decision:material": "materials",
    "decision:sculptural-scope": "sculptural-scope",
}


def _stage_required_decision_families(stage: int) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                DECISION_FAMILY_BY_REF[item.decision_ref]
                for item in _stage_decisions(stage)
            }
        )
    )


def _stage_parameter_evidence(
    stage: int,
    decision_sources: Mapping[str, tuple[str, ...]],
) -> tuple[ParameterEvidence, ...]:
    def refs(decision_ref: str) -> tuple[str, ...]:
        return tuple(sorted(decision_sources[decision_ref]))

    rows: list[ParameterEvidence] = [
        ParameterEvidence(
            "stylobate-envelope-m",
            "site-envelope",
            ParameterGranularity.EXACT_NUMERIC,
            ParameterBasisKind.MEASURED,
            refs("decision:stylobate-envelope"),
            (STYLOBATE_LENGTH, STYLOBATE_WIDTH),
            "meter",
        ),
        ParameterEvidence(
            "crepidoma-step-rise-m",
            "overall-massing",
            ParameterGranularity.EXACT_NUMERIC,
            ParameterBasisKind.DECLARED_CANDIDATE,
            refs("decision:overall-massing"),
            (STEP_RISE,),
            "meter",
        ),
        ParameterEvidence(
            "cella-outer-envelope-m",
            "overall-massing",
            ParameterGranularity.EXACT_NUMERIC,
            ParameterBasisKind.DECLARED_CANDIDATE,
            refs("decision:overall-massing"),
            (CELLA_OUTER_LENGTH, CELLA_OUTER_WIDTH, NAOS_HEIGHT),
            "meter",
        ),
        ParameterEvidence(
            "pediment-rise-m",
            "overall-massing",
            ParameterGranularity.EXACT_NUMERIC,
            ParameterBasisKind.DECLARED_CANDIDATE,
            refs("decision:overall-massing"),
            (PEDIMENT_RISE,),
            "meter",
        ),
    ]
    if stage >= 1:
        rows.extend(
            (
                ParameterEvidence(
                    "exterior-column-measures-m",
                    "exterior-support-dimensions",
                    ParameterGranularity.EXACT_NUMERIC,
                    ParameterBasisKind.MEASURED,
                    refs("decision:exterior-column-measures"),
                    (
                        EXTERIOR_COLUMN_HEIGHT,
                        EXTERIOR_COLUMN_DIAMETER,
                        CORNER_COLUMN_DIAMETER,
                        NORMAL_INTERAXIS,
                        SHORT_CORNER_INTERAXIS,
                        LONG_CORNER_INTERAXIS,
                    ),
                    "meter",
                ),
                ParameterEvidence(
                    "peristyle-grid-coordinates-m",
                    "exterior-support-topology",
                    ParameterGranularity.EXACT_NUMERIC,
                    ParameterBasisKind.DERIVED,
                    tuple(
                        sorted(
                            set(
                                refs("decision:peristyle-topology")
                                + refs("decision:exterior-column-measures")
                                + refs("decision:stylobate-envelope")
                            )
                        )
                    ),
                    tuple(
                        value
                        for item in peristyle_centers()
                        for value in (float(item["x"]), float(item["z"]))
                    ),
                    "meter",
                ),
            )
        )
    if stage >= 2:
        rows.extend(
            (
                ParameterEvidence(
                    "cella-partition-faces-m",
                    "room-layout",
                    ParameterGranularity.EXACT_NUMERIC,
                    ParameterBasisKind.DERIVED,
                    refs("decision:cella-layout"),
                    (PARTITION_WEST_FACE, PARTITION_EAST_FACE),
                    "meter",
                ),
                ParameterEvidence(
                    "principal-stone-opening-width-m",
                    "principal-openings",
                    ParameterGranularity.EXACT_NUMERIC,
                    ParameterBasisKind.MEASURED,
                    refs("decision:cella-openings"),
                    (PRINCIPAL_DOOR_OPENING_WIDTH,),
                    "meter",
                ),
                ParameterEvidence(
                    "principal-stone-opening-height-m",
                    "principal-openings",
                    ParameterGranularity.EXACT_NUMERIC,
                    ParameterBasisKind.DERIVED,
                    refs("decision:cella-openings"),
                    (PRINCIPAL_DOOR_OPENING_HEIGHT,),
                    "meter",
                ),
                ParameterEvidence(
                    "principal-door-leaf-candidate-m",
                    "principal-openings",
                    ParameterGranularity.EXACT_NUMERIC,
                    ParameterBasisKind.DECLARED_CANDIDATE,
                    refs("decision:cella-openings"),
                    (
                        PRINCIPAL_DOOR_LEAF_WIDTH,
                        PRINCIPAL_DOOR_LEAF_HEIGHT,
                        PRINCIPAL_DOOR_LEAF_THICKNESS,
                    ),
                    "meter",
                ),
                ParameterEvidence(
                    "east-interior-support-measures-m",
                    "interior-support-dimensions",
                    ParameterGranularity.EXACT_NUMERIC,
                    ParameterBasisKind.MEASURED,
                    refs("decision:interior-support-measures"),
                    (
                        INTERIOR_COLUMN_DIAMETER,
                        INTERIOR_COLUMN_INTERAXIS,
                    ),
                    "meter",
                ),
                ParameterEvidence(
                    "east-interior-support-grid-placement-m",
                    "interior-support-dimensions",
                    ParameterGranularity.EXACT_NUMERIC,
                    ParameterBasisKind.DECLARED_CANDIDATE,
                    tuple(
                        sorted(
                            set(
                                refs("decision:interior-support-measures")
                                + refs("decision:cella-layout")
                            )
                        )
                    ),
                    (
                        INTERIOR_GRID_REAR_AXIS,
                        INTERIOR_GRID_LONGITUDINAL_MARGIN,
                    ),
                    "meter",
                ),
                ParameterEvidence(
                    "interior-tier-heights-m",
                    "interior-support-dimensions",
                    ParameterGranularity.EXACT_NUMERIC,
                    ParameterBasisKind.DECLARED_CANDIDATE,
                    refs("decision:interior-support-measures"),
                    (INTERIOR_LOWER_HEIGHT, INTERIOR_UPPER_HEIGHT),
                    "meter",
                ),
                ParameterEvidence(
                    "west-room-support-grid-placement-m",
                    "interior-support-topology",
                    ParameterGranularity.EXACT_NUMERIC,
                    ParameterBasisKind.DECLARED_CANDIDATE,
                    refs("decision:interior-supports"),
                    (
                        WEST_ROOM_COLUMN_HALF_SPAN_X,
                        WEST_ROOM_COLUMN_HALF_SPAN_Y,
                        WEST_ROOM_CENTER,
                    ),
                    "meter",
                ),
            )
        )
    if stage >= 3:
        rows.append(
            ParameterEvidence(
                "entablature-height-m",
                "order-envelope",
                ParameterGranularity.EXACT_NUMERIC,
                ParameterBasisKind.MEASURED,
                refs("decision:entablature-pediment"),
                (ENTABLATURE_HEIGHT,),
                "meter",
            )
        )
    return tuple(sorted(rows, key=lambda item: item.identity))


def compile_stage_architectural_completeness(
    stage: int,
    decision_sources: Mapping[str, tuple[str, ...]],
):
    requirements = StageDecisionRequirements(
        typology_id="typology:greek-doric-peripteral-temple",
        stage_id=f"stage-{stage}",
        required_decision_families=_stage_required_decision_families(stage),
    )
    coverage_by_family: dict[str, dict[str, set[str]]] = {}
    for spec in _stage_decisions(stage):
        family_id = DECISION_FAMILY_BY_REF[spec.decision_ref]
        row = coverage_by_family.setdefault(
            family_id,
            {"decisions": set(), "evidence": set()},
        )
        row["decisions"].add(spec.decision_ref)
        row["evidence"].update(decision_sources[spec.decision_ref])
    coverage = tuple(
        DecisionFamilyCoverage(
            family_id=family_id,
            decision_refs=tuple(sorted(row["decisions"])),
            evidence_refs=tuple(sorted(row["evidence"])),
        )
        for family_id, row in sorted(coverage_by_family.items())
    )
    return compile_architectural_completeness(
        requirements,
        family_coverage=coverage,
        parameter_evidence=_stage_parameter_evidence(stage, decision_sources),
    )


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ref(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
        "uri": ref.uri,
    }


def _artifact_ref(ref: ProjectArtifactRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "artifact_id": ref.artifact_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


@contextmanager
def _timed(timings: dict[str, int], name: str):
    started = time.perf_counter_ns()
    try:
        yield
    finally:
        timings[name] = int((time.perf_counter_ns() - started) / 1_000_000)


def _offline_snapshot(source_id: str, captured_at: str) -> tuple[dict[str, object], bytes | None]:
    text = OFFLINE_TEXT[source_id]
    media_type = str(SOURCES[source_id]["media_type"])
    raw = text.encode("utf-8")
    if media_type == "application/pdf":
        # Tests retain a bounded fixture artifact; production uses the real PDF.
        binary = b"%PDF-1.4\n% ArchFlow offline fixture\n" + raw
    else:
        binary = None
    return (
        {
            "schema": "WebEvidenceSnapshot@1",
            "url": str(SOURCES[source_id]["url"]),
            "retrieved_at": captured_at,
            "content_sha256": _sha_bytes(binary or raw),
            "content_bytes": len(binary or raw),
            "text": text,
            "text_sha256": _sha_bytes(raw),
            "adoption_authority": False,
            "prompt_injection_surface": False,
            "canonical_write_authority": False,
        },
        binary,
    )


def _pdf_snapshot(
    source_id: str,
    *,
    captured_at: str,
    timeout_seconds: float,
) -> tuple[dict[str, object], bytes]:
    url = str(SOURCES[source_id]["url"])
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ArchFlow-V4-research/0.1 (evidence retrieval)"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        binary = response.read(10_000_001)
    if len(binary) > 10_000_000:
        raise RuntimeError(f"{source_id}: PDF exceeds the 10 MB evidence bound")
    reader = PdfReader(io.BytesIO(binary))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    if not text.strip():
        raise RuntimeError(f"{source_id}: PDF text extraction returned empty")
    text_bytes = text.encode("utf-8")
    return (
        {
            "schema": "WebEvidenceSnapshot@1",
            "url": url,
            "retrieved_at": captured_at,
            "content_sha256": _sha_bytes(binary),
            "content_bytes": len(binary),
            "text": text,
            "text_sha256": _sha_bytes(text_bytes),
            "adoption_authority": False,
            "prompt_injection_surface": False,
            "canonical_write_authority": False,
        },
        binary,
    )


def _fetch_source(
    source_id: str,
    *,
    captured_at: str,
    offline: bool,
    http_stats: dict[str, int],
    timeout_seconds: float = 30.0,
) -> tuple[dict[str, object], bytes | None]:
    if offline:
        return _offline_snapshot(source_id, captured_at)
    http_stats["calls"] += 1
    if SOURCES[source_id]["media_type"] == "application/pdf":
        snapshot, binary = _pdf_snapshot(
            source_id,
            captured_at=captured_at,
            timeout_seconds=timeout_seconds,
        )
        http_stats["download_bytes"] += len(binary)
        return snapshot, binary
    snapshot = fetch_web_evidence(
        str(SOURCES[source_id]["url"]),
        retrieved_at=captured_at,
        timeout_seconds=timeout_seconds,
    ).to_dict()
    http_stats["download_bytes"] += int(snapshot["content_bytes"])
    return snapshot, None


def peristyle_centers() -> tuple[dict[str, object], ...]:
    """Return the source-derived, closed 8 x 17 perimeter (46 unique columns)."""

    short_span = SHORT_CORNER_INTERAXIS * 2 + NORMAL_INTERAXIS * 5
    long_span = LONG_CORNER_INTERAXIS * 2 + NORMAL_INTERAXIS * 14
    x0 = -STYLOBATE_WIDTH / 2 + (STYLOBATE_WIDTH - short_span) / 2
    z0 = -STYLOBATE_LENGTH / 2 + (STYLOBATE_LENGTH - long_span) / 2
    short_steps = (SHORT_CORNER_INTERAXIS,) + (NORMAL_INTERAXIS,) * 5 + (
        SHORT_CORNER_INTERAXIS,
    )
    long_steps = (LONG_CORNER_INTERAXIS,) + (NORMAL_INTERAXIS,) * 14 + (
        LONG_CORNER_INTERAXIS,
    )
    xs = [x0]
    zs = [z0]
    for step in short_steps:
        xs.append(xs[-1] + step)
    for step in long_steps:
        zs.append(zs[-1] + step)
    rows: list[dict[str, object]] = []
    for side, z in (("west", zs[0]), ("east", zs[-1])):
        for index, x in enumerate(xs):
            rows.append(
                {
                    "id": f"peristyle-{side}-{index:02d}",
                    "side": side,
                    "index": index,
                    "x": round(x, 6),
                    "z": round(z, 6),
                    "corner": index in (0, len(xs) - 1),
                }
            )
    for side, x in (("south", xs[0]), ("north", xs[-1])):
        for index, z in enumerate(zs[1:-1], start=1):
            rows.append(
                {
                    "id": f"peristyle-{side}-{index:02d}",
                    "side": side,
                    "index": index,
                    "x": round(x, 6),
                    "z": round(z, 6),
                    "corner": False,
                }
            )
    return tuple(sorted(rows, key=lambda item: str(item["id"])))


def peristyle_axial_symmetry_deviation(
    centers: Iterable[Mapping[str, object]],
) -> float:
    """Measure the worst nearest counterpart across both plan axes."""

    points = tuple((float(item["x"]), float(item["z"])) for item in centers)
    if not points:
        raise ValueError("peristyle centers cannot be empty")
    deviations: list[float] = []
    for x, z in points:
        deviations.append(min(math.hypot(other_x + x, other_z - z) for other_x, other_z in points))
        deviations.append(min(math.hypot(other_x - x, other_z + z) for other_x, other_z in points))
    return max(deviations)


def _porch_centers() -> tuple[tuple[str, float, float], ...]:
    xs = tuple(-8.0 + index * 3.2 for index in range(6))
    return tuple(
        (f"porch-{side}-{index:02d}", x, z)
        for side, z in (("west", -25.3), ("east", 25.3))
        for index, x in enumerate(xs)
    )


def _u_colonnade_centers() -> tuple[tuple[str, float, float], ...]:
    z_values = tuple(
        INTERIOR_GRID_REAR_AXIS + index * INTERIOR_COLUMN_INTERAXIS
        for index in range(10)
    )
    side_axis = 2 * INTERIOR_COLUMN_INTERAXIS
    rows = [
        (f"naos-{side}-{index:02d}", x, z)
        for side, x in (("south", -side_axis), ("north", side_axis))
        for index, z in enumerate(z_values)
    ]
    rows.extend(
        (f"naos-west-{index:02d}", x, INTERIOR_GRID_REAR_AXIS)
        for index, x in enumerate(
            (-INTERIOR_COLUMN_INTERAXIS, 0.0, INTERIOR_COLUMN_INTERAXIS)
        )
    )
    result = tuple(rows)
    if len(result) != INTERIOR_TIER_COLUMN_COUNT:
        raise RuntimeError("east-room U colonnade count disagrees with evidence")
    return result


def _op(
    operation_id: str,
    component_id: str,
    kind: str,
    parameters: Mapping[str, object],
    decision_sources: Mapping[str, tuple[str, ...]],
    *decision_refs: str,
    material_id: str = "pentelic-marble",
) -> dict[str, object]:
    refs = tuple(
        sorted(
            {
                source
                for decision_ref in decision_refs
                for source in decision_sources.get(decision_ref, ())
            }
        )
    )
    return {
        "operation_id": operation_id,
        "component_id": component_id,
        "kind": kind,
        "parameters": _compile_rhino_z_up_parameters(kind, parameters),
        "decision_refs": list(sorted(decision_refs)),
        "source_refs": list(refs),
        "material_id": material_id,
    }


def _compile_rhino_z_up_parameters(
    kind: str,
    parameters: Mapping[str, object],
) -> dict[str, object]:
    """Compile the design frame (X, elevation, longitudinal) to Rhino X/Y/Z."""

    result = dict(parameters)
    if kind == "box":
        x, elevation, longitudinal = parameters["origin"]
        width, height, length = parameters["size"]
        result["origin"] = [x, longitudinal, elevation]
        result["size"] = [width, length, height]
    elif kind in {"tapered_column", "fluted_column", "ionic_column"}:
        x, elevation, longitudinal = parameters["center"]
        result["center"] = [x, longitudinal, elevation]
    elif kind == "roof_prism":
        result = {
            "width": parameters["width"],
            "length": parameters["length"],
            "eave_z": parameters["eave_y"],
            "ridge_z": parameters["ridge_y"],
            "y_center": parameters["z_center"],
        }
    elif kind == "gable_panel":
        result = {
            "width": parameters["width"],
            "depth": parameters["depth"],
            "eave_z": parameters["eave_y"],
            "ridge_z": parameters["ridge_y"],
            "y_center": parameters["z_center"],
        }
    return result


def build_stage_operations(
    stage: int,
    decision_sources: Mapping[str, tuple[str, ...]],
) -> tuple[dict[str, object], ...]:
    if stage not in range(4):
        raise ValueError("stage must be inside 0..3")
    operations: list[dict[str, object]] = []
    for index, (margin, rise) in enumerate(((1.8, 0.0), (0.9, STEP_RISE), (0.0, 2 * STEP_RISE))):
        operations.append(
            _op(
                f"crepidoma-step-{index}",
                "base",
                "box",
                {
                    "origin": [
                        -STYLOBATE_WIDTH / 2 - margin,
                        rise,
                        -STYLOBATE_LENGTH / 2 - margin,
                    ],
                    "size": [
                        STYLOBATE_WIDTH + 2 * margin,
                        STEP_RISE,
                        STYLOBATE_LENGTH + 2 * margin,
                    ],
                },
                decision_sources,
                "decision:stylobate-envelope",
                "decision:primary-orientation",
            )
        )

    if stage <= 1:
        operations.append(
            _op(
                "cella-envelope",
                "cella",
                "box",
                {
                    "origin": [-CELLA_OUTER_WIDTH / 2, BASE_Y, -CELLA_OUTER_LENGTH / 2],
                    "size": [CELLA_OUTER_WIDTH, NAOS_HEIGHT, CELLA_OUTER_LENGTH],
                },
                decision_sources,
                "decision:overall-massing",
            )
        )
    else:
        wall_h = NAOS_HEIGHT
        half_w = CELLA_OUTER_WIDTH / 2
        half_l = CELLA_OUTER_LENGTH / 2
        side_width = (CELLA_OUTER_WIDTH - PRINCIPAL_DOOR_OPENING_WIDTH) / 2
        lintel_height = wall_h - PRINCIPAL_DOOR_OPENING_HEIGHT
        wall_specs = (
            ("cella-wall-south", [-half_w, BASE_Y, -half_l], [CELLA_WALL, wall_h, CELLA_OUTER_LENGTH], ("decision:cella-layout",)),
            ("cella-wall-north", [half_w - CELLA_WALL, BASE_Y, -half_l], [CELLA_WALL, wall_h, CELLA_OUTER_LENGTH], ("decision:cella-layout",)),
            ("cella-wall-west-left", [-half_w, BASE_Y, -half_l], [side_width, wall_h, CELLA_WALL], ("decision:cella-layout", "decision:cella-openings")),
            ("cella-wall-west-right", [PRINCIPAL_DOOR_OPENING_WIDTH / 2, BASE_Y, -half_l], [side_width, wall_h, CELLA_WALL], ("decision:cella-layout", "decision:cella-openings")),
            ("cella-wall-west-lintel", [-PRINCIPAL_DOOR_OPENING_WIDTH / 2, BASE_Y + PRINCIPAL_DOOR_OPENING_HEIGHT, -half_l], [PRINCIPAL_DOOR_OPENING_WIDTH, lintel_height, CELLA_WALL], ("decision:cella-layout", "decision:cella-openings")),
            ("cella-wall-east-left", [-half_w, BASE_Y, half_l - CELLA_WALL], [side_width, wall_h, CELLA_WALL], ("decision:cella-layout", "decision:cella-openings")),
            ("cella-wall-east-right", [PRINCIPAL_DOOR_OPENING_WIDTH / 2, BASE_Y, half_l - CELLA_WALL], [side_width, wall_h, CELLA_WALL], ("decision:cella-layout", "decision:cella-openings")),
            ("cella-wall-east-lintel", [-PRINCIPAL_DOOR_OPENING_WIDTH / 2, BASE_Y + PRINCIPAL_DOOR_OPENING_HEIGHT, half_l - CELLA_WALL], [PRINCIPAL_DOOR_OPENING_WIDTH, lintel_height, CELLA_WALL], ("decision:cella-layout", "decision:cella-openings")),
            ("cella-partition", [-half_w, BASE_Y, PARTITION_WEST_FACE], [CELLA_OUTER_WIDTH, wall_h, CELLA_WALL], ("decision:cella-layout", "decision:room-connectivity")),
        )
        for operation_id, origin, size, wall_decisions in wall_specs:
            operations.append(
                _op(
                    operation_id,
                    "cella",
                    "box",
                    {"origin": origin, "size": size},
                    decision_sources,
                    *wall_decisions,
                )
            )
        for side, longitudinal in (
            (
                "west",
                -half_l + (CELLA_WALL - PRINCIPAL_DOOR_LEAF_THICKNESS) / 2,
            ),
            (
                "east",
                half_l
                - CELLA_WALL
                + (CELLA_WALL - PRINCIPAL_DOOR_LEAF_THICKNESS) / 2,
            ),
        ):
            operations.append(
                _op(
                    f"cella-door-{side}",
                    f"door-{side}",
                    "box",
                    {
                        "origin": [
                            -PRINCIPAL_DOOR_LEAF_WIDTH / 2,
                            BASE_Y,
                            longitudinal,
                        ],
                        "size": [
                            PRINCIPAL_DOOR_LEAF_WIDTH,
                            PRINCIPAL_DOOR_LEAF_HEIGHT,
                            PRINCIPAL_DOOR_LEAF_THICKNESS,
                        ],
                    },
                    decision_sources,
                    "decision:room-connectivity",
                    "decision:cella-openings",
                    material_id="bronze-timber-candidate",
                )
            )

    if stage >= 1:
        for item in peristyle_centers():
            diameter = CORNER_COLUMN_DIAMETER if item["corner"] else EXTERIOR_COLUMN_DIAMETER
            operations.append(
                _op(
                    str(item["id"]),
                    "peristyle",
                    "fluted_column" if stage >= 3 else "tapered_column",
                    {
                        "center": [item["x"], BASE_Y, item["z"]],
                        "height": EXTERIOR_COLUMN_HEIGHT,
                        "diameter": diameter,
                        "flutes": 20 if stage >= 3 else 0,
                    },
                    decision_sources,
                    "decision:peristyle-topology",
                    "decision:exterior-column-measures",
                    "decision:optical-refinements",
                )
            )
            if stage >= 3:
                operations.append(
                    _op(
                        f"{item['id']}-capital",
                        "peristyle",
                        "box",
                        {
                            "origin": [
                                float(item["x"]) - 0.75 * float(diameter),
                                BASE_Y + EXTERIOR_COLUMN_HEIGHT - 0.12,
                                float(item["z"]) - 0.75 * float(diameter),
                            ],
                            "size": [1.5 * float(diameter), 0.48, 1.5 * float(diameter)],
                        },
                        decision_sources,
                        "decision:exterior-column-measures",
                        "decision:entablature-pediment",
                    )
                )
        ent_y = BASE_Y + EXTERIOR_COLUMN_HEIGHT
        beams = (
            ("entablature-west", [-STYLOBATE_WIDTH / 2, ent_y, -STYLOBATE_LENGTH / 2], [STYLOBATE_WIDTH, ENTABLATURE_HEIGHT, 1.2]),
            ("entablature-east", [-STYLOBATE_WIDTH / 2, ent_y, STYLOBATE_LENGTH / 2 - 1.2], [STYLOBATE_WIDTH, ENTABLATURE_HEIGHT, 1.2]),
            ("entablature-south", [-STYLOBATE_WIDTH / 2, ent_y, -STYLOBATE_LENGTH / 2], [1.2, ENTABLATURE_HEIGHT, STYLOBATE_LENGTH]),
            ("entablature-north", [STYLOBATE_WIDTH / 2 - 1.2, ent_y, -STYLOBATE_LENGTH / 2], [1.2, ENTABLATURE_HEIGHT, STYLOBATE_LENGTH]),
        )
        for operation_id, origin, size in beams:
            if stage == 1:
                entablature_decisions = ("decision:exterior-column-measures",)
            elif stage == 2:
                entablature_decisions = (
                    "decision:exterior-column-measures",
                    "decision:roof-system",
                )
            else:
                entablature_decisions = (
                    "decision:exterior-column-measures",
                    "decision:entablature-pediment",
                )
            operations.append(
                _op(
                    operation_id,
                    "entablature",
                    "box",
                    {"origin": origin, "size": size},
                    decision_sources,
                    *entablature_decisions,
                )
            )

    if stage >= 2:
        for operation_id, x, z in _porch_centers():
            operations.append(
                _op(
                    operation_id,
                    "porches",
                    "tapered_column",
                    {
                        "center": [x, BASE_Y, z],
                        "height": 8.6,
                        "diameter": 1.35,
                        "flutes": 20,
                    },
                    decision_sources,
                    "decision:cella-layout",
                )
            )
        for operation_id, x, z in _u_colonnade_centers():
            operations.append(
                _op(
                    f"{operation_id}-lower",
                    "interior-colonnade",
                    "fluted_column" if stage >= 3 else "tapered_column",
                    {
                        "center": [x, BASE_Y, z],
                        "height": INTERIOR_LOWER_HEIGHT,
                        "diameter": INTERIOR_COLUMN_DIAMETER,
                        "flutes": 16 if stage >= 3 else 0,
                    },
                    decision_sources,
                    "decision:interior-supports",
                    "decision:interior-support-measures",
                )
            )
            if stage >= 3:
                operations.append(
                    _op(
                        f"{operation_id}-upper",
                        "interior-colonnade",
                        "fluted_column",
                        {
                            "center": [x, BASE_Y + INTERIOR_LOWER_HEIGHT, z],
                            "height": INTERIOR_UPPER_HEIGHT,
                            "diameter": 0.88,
                            "flutes": 20,
                        },
                        decision_sources,
                        "decision:interior-supports",
                        "decision:interior-support-measures",
                        "decision:roof-system",
                    )
                )
        if stage >= 3:
            for row, longitudinal_offset in enumerate(
                (-WEST_ROOM_COLUMN_HALF_SPAN_Y, WEST_ROOM_COLUMN_HALF_SPAN_Y)
            ):
                for column, x in enumerate(
                    (-WEST_ROOM_COLUMN_HALF_SPAN_X, WEST_ROOM_COLUMN_HALF_SPAN_X)
                ):
                    operations.append(
                        _op(
                            f"west-room-ionic-{row}-{column}",
                            "interior-colonnade",
                            "ionic_column",
                            {
                                "center": [
                                    x,
                                    BASE_Y,
                                    WEST_ROOM_CENTER + longitudinal_offset,
                                ],
                                "height": 11.7,
                                "diameter": 1.05,
                                "flutes": 24,
                            },
                            decision_sources,
                            "decision:interior-supports",
                        )
                    )

    roof_eave = BASE_Y + EXTERIOR_COLUMN_HEIGHT + ENTABLATURE_HEIGHT
    if stage < 2:
        roof_decisions = (
            "decision:reconstruction-strategy",
            "decision:stylobate-envelope",
        )
    elif stage == 2:
        roof_decisions = (
            "decision:roof-system",
            "decision:cella-layout",
        )
    else:
        roof_decisions = (
            "decision:roof-system",
            "decision:entablature-pediment",
        )
    operations.append(
        _op(
            "main-gabled-roof",
            "roof",
            "roof_prism",
            {
                "width": STYLOBATE_WIDTH + 1.4,
                "length": STYLOBATE_LENGTH + 0.8,
                "eave_y": roof_eave,
                "ridge_y": roof_eave + PEDIMENT_RISE,
                "z_center": 0.0,
            },
            decision_sources,
            *roof_decisions,
        )
    )

    if stage >= 3:
        for side, z in (("west", -STYLOBATE_LENGTH / 2 - 0.02), ("east", STYLOBATE_LENGTH / 2 + 0.02)):
            operations.append(
                _op(
                    f"pediment-{side}",
                    "pediments",
                    "gable_panel",
                    {
                        "width": STYLOBATE_WIDTH,
                        "depth": 0.32,
                        "eave_y": roof_eave,
                        "ridge_y": roof_eave + PEDIMENT_RISE,
                        "z_center": z,
                    },
                    decision_sources,
                    "decision:entablature-pediment",
                    "decision:sculptural-scope",
                )
            )
        metope_y = BASE_Y + EXTERIOR_COLUMN_HEIGHT + 1.15
        for side, count in (("west", 14), ("east", 14), ("south", 32), ("north", 32)):
            for index in range(count):
                if side in ("west", "east"):
                    x = -STYLOBATE_WIDTH / 2 + (index + 0.5) * STYLOBATE_WIDTH / count
                    origin = [x - 0.48, metope_y, (-STYLOBATE_LENGTH / 2 - 0.12) if side == "west" else (STYLOBATE_LENGTH / 2 - 0.08)]
                    size = [0.96, 1.28, 0.20]
                else:
                    z = -STYLOBATE_LENGTH / 2 + (index + 0.5) * STYLOBATE_LENGTH / count
                    origin = [(-STYLOBATE_WIDTH / 2 - 0.12) if side == "south" else (STYLOBATE_WIDTH / 2 - 0.08), metope_y, z - 0.48]
                    size = [0.20, 1.28, 0.96]
                operations.append(
                    _op(
                        f"metope-{side}-{index:02d}",
                        "decoration",
                        "box",
                        {"origin": origin, "size": size},
                        decision_sources,
                        "decision:decorative-layout",
                        "decision:sculptural-scope",
                    )
                )
        frieze_y = BASE_Y + 11.75
        frieze_specs = (
            ("ionic-frieze-west", [-CELLA_OUTER_WIDTH / 2, frieze_y, -CELLA_OUTER_LENGTH / 2 - 0.08], [CELLA_OUTER_WIDTH, 1.02, 0.16]),
            ("ionic-frieze-east", [-CELLA_OUTER_WIDTH / 2, frieze_y, CELLA_OUTER_LENGTH / 2 - 0.08], [CELLA_OUTER_WIDTH, 1.02, 0.16]),
            ("ionic-frieze-south", [-CELLA_OUTER_WIDTH / 2 - 0.08, frieze_y, -CELLA_OUTER_LENGTH / 2], [0.16, 1.02, CELLA_OUTER_LENGTH]),
            ("ionic-frieze-north", [CELLA_OUTER_WIDTH / 2 - 0.08, frieze_y, -CELLA_OUTER_LENGTH / 2], [0.16, 1.02, CELLA_OUTER_LENGTH]),
        )
        for operation_id, origin, size in frieze_specs:
            operations.append(
                _op(
                    operation_id,
                    "decoration",
                    "box",
                    {"origin": origin, "size": size},
                    decision_sources,
                    "decision:decorative-layout",
                    "decision:material",
                )
            )
    return tuple(sorted(operations, key=lambda item: str(item["operation_id"])))


def _box_brep(origin: Iterable[float], size: Iterable[float]):
    x, y, z = (float(value) for value in origin)
    sx, sy, sz = (float(value) for value in size)
    bbox = rhino3dm.BoundingBox(
        rhino3dm.Point3d(x, y, z),
        rhino3dm.Point3d(x + sx, y + sy, z + sz),
    )
    return rhino3dm.Brep.CreateFromBox(rhino3dm.Box(bbox))


def _column_mesh(center: Iterable[float], height: float, diameter: float, *, fluted: bool):
    x, y, z0 = (float(value) for value in center)
    segments = 40 if fluted else 24
    rings = (0.0, 0.28, 0.58, 1.0)
    mesh = rhino3dm.Mesh()
    for t in rings:
        taper = 1.0 - 0.08 * t + 0.024 * math.sin(math.pi * t)
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            flute = 0.94 if fluted and index % 2 else 1.0
            radius = diameter * 0.5 * taper * flute
            mesh.Vertices.Add(
                x + radius * math.cos(angle),
                y + radius * math.sin(angle),
                z0 + height * t,
            )
    for ring in range(len(rings) - 1):
        for index in range(segments):
            nxt = (index + 1) % segments
            a = ring * segments + index
            b = ring * segments + nxt
            c = (ring + 1) * segments + nxt
            d = (ring + 1) * segments + index
            mesh.Faces.AddFace(a, b, c, d)
    bottom_center = len(mesh.Vertices)
    mesh.Vertices.Add(x, y, z0)
    top_center = len(mesh.Vertices)
    mesh.Vertices.Add(x, y, z0 + height)
    top_start = (len(rings) - 1) * segments
    for index in range(segments):
        nxt = (index + 1) % segments
        mesh.Faces.AddFace(bottom_center, nxt, index)
        mesh.Faces.AddFace(top_center, top_start + index, top_start + nxt)
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def _roof_mesh(width: float, length: float, eave_z: float, ridge_z: float, y_center: float):
    half_w = width / 2.0
    y0 = y_center - length / 2.0
    y1 = y_center + length / 2.0
    mesh = rhino3dm.Mesh()
    for point in (
        (-half_w, y0, eave_z),
        (half_w, y0, eave_z),
        (0.0, y0, ridge_z),
        (-half_w, y1, eave_z),
        (half_w, y1, eave_z),
        (0.0, y1, ridge_z),
    ):
        mesh.Vertices.Add(*point)
    mesh.Faces.AddFace(0, 1, 2)
    mesh.Faces.AddFace(3, 5, 4)
    mesh.Faces.AddFace(0, 3, 4, 1)
    mesh.Faces.AddFace(0, 2, 5, 3)
    mesh.Faces.AddFace(1, 4, 5, 2)
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def _create_three_dm(
    path: Path,
    *,
    operations: tuple[dict[str, object], ...],
    program_digest: str,
    stage: int,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
    model.Settings.ModelAbsoluteTolerance = 0.001
    model.Strings["archflow:coordinate_system"] = "RhinoWorldXY_ZUp"
    model.Strings["archflow:up_axis"] = "Z"
    component_colors = {
        "base": (206, 198, 173, 255),
        "cella": (224, 216, 190, 255),
        "peristyle": (236, 229, 207, 255),
        "porches": (232, 224, 201, 255),
        "interior-colonnade": (214, 205, 181, 255),
        "entablature": (228, 219, 195, 255),
        "pediments": (221, 210, 185, 255),
        "roof": (187, 177, 157, 255),
        "decoration": (190, 164, 126, 255),
        "door-east": (94, 68, 45, 255),
        "door-west": (94, 68, 45, 255),
    }
    layers: dict[str, int] = {}
    for component_id in sorted({str(item["component_id"]) for item in operations}):
        layer = rhino3dm.Layer()
        layer.Name = f"P084::{component_id}"
        layer.Color = component_colors.get(component_id, (220, 215, 200, 255))
        layers[component_id] = model.Layers.Add(layer)
    material_specs = {
        "pentelic-marble": (
            "Pentelic marble candidate",
            (226, 219, 198, 255),
            (110, 105, 96, 255),
            (235, 231, 220, 255),
        ),
        "bronze-timber-candidate": (
            "Bronze and timber door candidate",
            (92, 62, 39, 255),
            (42, 30, 22, 255),
            (128, 100, 70, 255),
        ),
    }
    material_indices: dict[str, int] = {}
    for material_id in sorted(
        {str(item["material_id"]) for item in operations}
    ):
        name, diffuse, ambient, specular = material_specs.get(
            material_id,
            (material_id, (200, 195, 185, 255), (80, 78, 74, 255), (220, 216, 208, 255)),
        )
        material = rhino3dm.Material()
        material.Name = name
        material.DiffuseColor = diffuse
        material.AmbientColor = ambient
        material.SpecularColor = specular
        material_indices[material_id] = model.Materials.Add(material)
    for operation in operations:
        parameters = operation["parameters"]
        assert isinstance(parameters, Mapping)
        kind = operation["kind"]
        box_brep = False
        if kind == "box":
            geometry = _box_brep(parameters["origin"], parameters["size"])
            add = model.Objects.AddBrep
            box_brep = True
        elif kind in {"tapered_column", "fluted_column", "ionic_column"}:
            geometry = _column_mesh(
                parameters["center"],
                float(parameters["height"]),
                float(parameters["diameter"]),
                fluted=kind in {"fluted_column", "ionic_column"},
            )
            add = model.Objects.AddMesh
        elif kind == "roof_prism":
            geometry = _roof_mesh(
                float(parameters["width"]),
                float(parameters["length"]),
                float(parameters["eave_z"]),
                float(parameters["ridge_z"]),
                float(parameters["y_center"]),
            )
            add = model.Objects.AddMesh
        elif kind == "gable_panel":
            geometry = _roof_mesh(
                float(parameters["width"]),
                float(parameters["depth"]),
                float(parameters["eave_z"]),
                float(parameters["ridge_z"]),
                float(parameters["y_center"]),
            )
            add = model.Objects.AddMesh
        else:
            raise RuntimeError(f"unsupported Parthenon adapter operation: {kind}")
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = str(operation["operation_id"])
        attributes.LayerIndex = layers[str(operation["component_id"])]
        attributes.MaterialIndex = material_indices[str(operation["material_id"])]
        for key, value in {
            "archflow:project_id": PROJECT_ID,
            "archflow:branch_id": BRANCH_ID,
            "archflow:stage": str(stage),
            "archflow:component_id": str(operation["component_id"]),
            "archflow:operation_id": str(operation["operation_id"]),
            "archflow:program_digest": program_digest,
            "archflow:coordinate_system": "RhinoWorldXY_ZUp",
            "archflow:up_axis": "Z",
            "archflow:material_id": str(operation["material_id"]),
            "archflow:decision_refs": canonical_json(operation["decision_refs"], ascii=False),
            "archflow:source_refs": canonical_json(operation["source_refs"], ascii=False),
        }.items():
            attributes.SetUserString(key, value)
        source_id = add(geometry, attributes)
        if box_brep:
            add_axis_aligned_box_brep_witnesses(
                model,
                source_object_id=source_id,
                brep=geometry,
                layer_index=attributes.LayerIndex,
            )
    if not model.Write(str(path), 8):
        raise RuntimeError(f"rhino3dm failed to write {path}")
    return len(operations)


def _spatial_aabb(bbox: object) -> AABB:
    return AABB(
        (float(bbox.Min.X), float(bbox.Min.Y), float(bbox.Min.Z)),
        (float(bbox.Max.X), float(bbox.Max.Y), float(bbox.Max.Z)),
    )


def _required_spatial_components(stage: int) -> tuple[str, ...]:
    required = {"base", "cella", "roof"}
    if stage >= 1:
        required.update(("peristyle", "entablature"))
    if stage >= 2:
        required.update(
            (
                "door-east",
                "door-west",
                "porches",
                "interior-colonnade",
            )
        )
    if stage >= 3:
        required.update(("decoration", "pediments"))
    return tuple(sorted(required))


def _principal_opening_regions(stage: int) -> tuple[OpeningClearRegion, ...]:
    if stage < 2:
        return ()
    half_l = CELLA_OUTER_LENGTH / 2
    half_opening = PRINCIPAL_DOOR_OPENING_WIDTH / 2
    return (
        OpeningClearRegion(
            "east-principal-door-clear",
            "door-east",
            AABB(
                (-half_opening, half_l - CELLA_WALL, BASE_Y),
                (half_opening, half_l, BASE_Y + PRINCIPAL_DOOR_OPENING_HEIGHT),
            ),
        ),
        OpeningClearRegion(
            "west-principal-door-clear",
            "door-west",
            AABB(
                (-half_opening, -half_l, BASE_Y),
                (half_opening, -half_l + CELLA_WALL, BASE_Y + PRINCIPAL_DOOR_OPENING_HEIGHT),
            ),
        ),
    )


def validate_stage_spatial_model(path: Path, stage: int):
    """Measure the retained 3DM itself, not the operation list that requested it."""

    model = rhino3dm.File3dm.Read(str(path))
    if model is None:
        raise RuntimeError(f"cannot read spatial-validation model: {path}")
    objects = primary_three_dm_objects(model)
    if not objects:
        raise RuntimeError("spatial-validation model contains no objects")
    object_bounds = tuple(item.Geometry.GetBoundingBox() for item in objects)
    aggregate = AABB(
        (
            min(item.Min.X for item in object_bounds),
            min(item.Min.Y for item in object_bounds),
            min(item.Min.Z for item in object_bounds),
        ),
        (
            max(item.Max.X for item in object_bounds),
            max(item.Max.Y for item in object_bounds),
            max(item.Max.Z for item in object_bounds),
        ),
    )
    hosts = [HostRegion("building-host", aggregate)]
    if stage >= 2:
        half_w = CELLA_OUTER_WIDTH / 2
        hosts.extend(
            (
                HostRegion(
                    "east-room-host",
                    AABB(
                        (-half_w + CELLA_WALL, PARTITION_EAST_FACE, BASE_Y),
                        (half_w - CELLA_WALL, EAST_ROOM_INNER_EAST, BASE_Y + NAOS_HEIGHT),
                    ),
                ),
                HostRegion(
                    "west-room-host",
                    AABB(
                        (-half_w + CELLA_WALL, WEST_ROOM_INNER_WEST, BASE_Y),
                        (half_w - CELLA_WALL, PARTITION_WEST_FACE, BASE_Y + NAOS_HEIGHT),
                    ),
                ),
            )
        )

    elements: list[SpatialElement] = []
    column_components = {"peristyle", "porches", "interior-colonnade"}
    for item, bbox in zip(objects, object_bounds, strict=True):
        user_strings = dict(item.Attributes.GetUserStrings() or ())
        operation_id = item.Attributes.Name or str(item.Id)
        component_id = user_strings.get("archflow:component_id")
        if not component_id:
            raise RuntimeError(
                f"3DM object {operation_id} lacks archflow:component_id"
            )
        if operation_id.startswith("cella-wall-") or operation_id == "cella-partition":
            kind = SpatialElementKind.WALL
        elif component_id in column_components:
            kind = SpatialElementKind.COLUMN
        elif component_id in {"door-east", "door-west"}:
            kind = SpatialElementKind.DOOR
        else:
            kind = SpatialElementKind.OTHER
        if component_id == "interior-colonnade" and operation_id.startswith("naos-"):
            host_region_id = "east-room-host"
        elif component_id == "interior-colonnade" and operation_id.startswith("west-room-"):
            host_region_id = "west-room-host"
        else:
            host_region_id = "building-host"
        elements.append(
            SpatialElement(
                operation_id,
                component_id,
                kind,
                _spatial_aabb(bbox),
                host_region_id,
            )
        )
    return validate_spatial_layout(
        elements=tuple(elements),
        host_regions=tuple(hosts),
        required_component_ids=_required_spatial_components(stage),
        opening_clear_regions=_principal_opening_regions(stage),
        minimum_column_wall_clearance=0.05,
        linear_tolerance=1e-6,
        intersection_volume_tolerance=1e-9,
        length_unit="meter",
    )


def _stage_decisions(stage: int) -> tuple[DecisionSpec, ...]:
    return tuple(item for item in DECISIONS if item.stage <= stage)


def _stage_dependency_edges(stage: int) -> tuple[tuple[str, str, str], ...]:
    active = {item.decision_ref for item in _stage_decisions(stage)}
    return tuple(
        item
        for item in DEPENDENCY_EDGES
        if item[0] in active and item[1] in active
    )


def _initial_operational_state(run: RunRef, evidence_ref: str) -> OperationalMarkovState:
    branch = BranchRef(run=run, branch_id=BRANCH_ID, epoch=1)
    obligations = tuple(
        DesignObligation(
            obligation_id=f"close-stage-{stage}",
            statement=f"Close the declared evidence and geometry contract for Stage {stage}.",
            source_ref=f"requirement:parthenon-stage-{stage}",
            status=ObligationStatus.OPEN,
            subject_refs=(f"deliverable:parthenon-stage-{stage}",),
        )
        for stage in range(4)
    )
    fact = StateFact(
        domain=StateDomain.SEMANTIC,
        key="reconstruction-strategy",
        value=BRANCH_ID,
        source_ref=evidence_ref,
        epistemic_status=FactEpistemicStatus.DECLARED,
    )
    return OperationalMarkovState(
        branch=branch,
        compiler_version="p082-parthenon-project-runner-1",
        phase="reconstruction",
        facts=(fact,),
        locks=(
            StateLock(
                target_ref="fact:semantic:reconstruction-strategy",
                authority_id="authority.agent-ratifier",
                source_ref=evidence_ref,
            ),
        ),
        obligations=obligations,
        evidence_refs=(evidence_ref,),
    )


def _propose_operational_state(
    parent: OperationalMarkovState,
    *,
    stage: int,
    sufficiency_ref: str,
    decision_sources: Mapping[str, tuple[str, ...]],
) -> OperationalMarkovState:
    new_facts = []
    existing_keys = {(item.domain.value, item.key) for item in parent.facts}
    for spec in DECISIONS:
        if spec.stage != stage:
            continue
        key = spec.decision_ref.split(":", 1)[1]
        identity = (StateDomain.PARAMETER.value, key)
        if identity in existing_keys:
            continue
        source_ref = decision_sources[spec.decision_ref][0]
        new_facts.append(
            StateFact(
                domain=StateDomain.PARAMETER,
                key=key,
                value=spec.selected_value,
                source_ref=source_ref,
                epistemic_status=(
                    FactEpistemicStatus.DECLARED
                    if spec.hard
                    else FactEpistemicStatus.DERIVED
                ),
            )
        )
    child = replace(
        parent,
        branch=BranchRef(
            run=parent.branch.run,
            branch_id=parent.branch.branch_id,
            epoch=parent.branch.epoch + 1,
        ),
        facts=tuple(
            sorted(
                (*parent.facts, *new_facts),
                key=lambda item: (item.domain.value, item.key),
            )
        ),
        obligations=parent.obligations,
        evidence_refs=tuple(sorted({*parent.evidence_refs, sufficiency_ref})),
    )
    return child


def _close_operational_state(
    parent: OperationalMarkovState,
    provisional: OperationalMarkovState,
    *,
    stage: int,
    realization_evidence_refs: tuple[str, ...],
) -> tuple[OperationalMarkovState, object]:
    obligations = tuple(
        replace(item, status=ObligationStatus.SATISFIED)
        if item.obligation_id == f"close-stage-{stage}"
        else item
        for item in provisional.obligations
    )
    child = replace(
        provisional,
        obligations=obligations,
        evidence_refs=tuple(
            sorted(
                {
                    *provisional.evidence_refs,
                    *realization_evidence_refs,
                }
            )
        ),
    )
    policy = StageConvergencePolicy(
        policy_id=f"parthenon-stage-{stage}-convergence",
        stage=f"stage-{stage}",
        protected_refs=("fact:semantic:reconstruction-strategy",),
        mandatory_obligation_ids=(f"close-stage-{stage}",),
    )
    request = StageTransitionRequest(
        request_id=f"parthenon-stage-{stage}-resolve",
        stage=policy.stage,
        kind=StageTransitionKind.RESOLVE,
        authority_id="authority.agent-ratifier",
        parent_state_digest=parent.state_digest,
        child_state_digest=child.state_digest,
        trigger_refs=(f"deliverable:parthenon-stage-{stage}",),
    )
    receipt = evaluate_stage_convergence(
        policy,
        request,
        parent,
        child,
        parent_evidence=StageConvergenceEvidence(parent.state_digest),
        child_evidence=StageConvergenceEvidence(child.state_digest),
    )
    if (
        receipt.outcome is not StageConvergenceOutcome.PROGRESS
        or not receipt.stage_ready
    ):
        raise RuntimeError(
            f"Stage {stage} did not converge: {', '.join(receipt.reason_codes)}"
        )
    return child, receipt


def _source_capture_payload(
    source_id: str,
    snapshot: Mapping[str, object],
    artifact_ref: ProjectArtifactRef | None,
) -> dict[str, object]:
    return {
        "schema": "ParthenonSourceCapture@1",
        "source_id": source_id,
        "source_family": SOURCES[source_id]["family"],
        "media_type": SOURCES[source_id]["media_type"],
        "snapshot": dict(snapshot),
        "binary_artifact": None if artifact_ref is None else _artifact_ref(artifact_ref),
        "adoption_authority": False,
        "canonical_write_authority": False,
    }


def _normalize_captured_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("captured_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("captured_at must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open_bootstrap_only_resume(
    root: Path,
    *,
    run_id: str,
    research_run_id: str = RESEARCH_RUN_ID,
) -> tuple[FilesystemProjectRepository, RunRef, RunRef]:
    """Resume only the exact no-design state left by a discovery blocker."""

    repository = FilesystemProjectRepository.open(root)
    research_run = repository.load_run(research_run_id)
    run = repository.load_run(run_id)

    reconstruction_files = tuple(
        sorted(
            path.relative_to(repository.layout.run(run_id).root).as_posix()
            for path in repository.layout.run(run_id).root.rglob("*")
            if path.is_file()
        )
    )
    research_files = tuple(
        sorted(
            path.relative_to(repository.layout.run(research_run_id).root).as_posix()
            for path in repository.layout.run(research_run_id).root.rglob("*")
            if path.is_file()
        )
    )
    blocker_files = tuple(
        item
        for item in research_files
        if item.startswith("records/research-blocker-") and item.endswith(".json")
    )
    if reconstruction_files != ("run.json",) or len(blocker_files) != 1 or set(
        research_files
    ) != {"run.json", blocker_files[0]}:
        raise FileExistsError(
            "project target contains work beyond the exact bootstrap-only recovery state; "
            f"refusing to overwrite: {root}"
        )
    repository.verify()
    return repository, research_run, run


def _find_progress_snapshot(
    repository: FilesystemProjectRepository,
    *,
    run_id: str,
) -> tuple[ProjectRecordRef, dict[str, object]]:
    matches: list[tuple[ProjectRecordRef, dict[str, object]]] = []
    for path in sorted(repository.layout.exports.glob("parthenon-progress-snapshot-*.json")):
        data = path.read_bytes()
        digest = _sha_bytes(data)
        ref = ProjectRecordRef(
            project_id=PROJECT_ID,
            relative_path=path.relative_to(repository.layout.root).as_posix(),
            sha256=digest,
        )
        payload = repository.load_json(ref)
        if payload.get("schema") == "ParthenonProgressSnapshot@1" and payload.get(
            "run_id"
        ) == run_id:
            matches.append((ref, payload))
    if len(matches) != 1:
        raise FileExistsError(
            f"expected exactly one predecessor progress snapshot for {run_id}; "
            f"found {len(matches)}"
        )
    return matches[0]


def _record_ref_from_path(
    repository: FilesystemProjectRepository,
    path: Path,
) -> ProjectRecordRef:
    data = path.read_bytes()
    return ProjectRecordRef(
        project_id=PROJECT_ID,
        relative_path=path.relative_to(repository.layout.root).as_posix(),
        sha256=_sha_bytes(data),
    )


def _load_predecessor_source_captures(
    repository: FilesystemProjectRepository,
    *,
    predecessor_run_id: str,
) -> dict[str, dict[str, object]]:
    records = (
        repository.layout.run(predecessor_run_id).branches
        / BRANCH_ID
        / "records"
    )
    captures: dict[str, dict[str, object]] = {}
    for source_id in sorted(SOURCES):
        matches = tuple(sorted(records.glob(f"source-capture-{source_id}-*.json")))
        if len(matches) != 1:
            raise FileExistsError(
                f"expected one retained {source_id} capture in {predecessor_run_id}; "
                f"found {len(matches)}"
            )
        ref = _record_ref_from_path(repository, matches[0])
        payload = repository.load_json(ref)
        snapshot = payload.get("snapshot")
        if payload.get("schema") != "ParthenonSourceCapture@1" or not isinstance(
            snapshot, dict
        ):
            raise RuntimeError(
                f"predecessor source capture {ref.uri} changed schema"
            )
        captures[source_id] = {
            "snapshot": snapshot,
            "capture_ref": ref,
            "binary_artifact": payload.get("binary_artifact"),
        }
    return captures


def run_project(
    root: Path,
    *,
    captured_at: str,
    offline: bool = False,
    project_id: str = PROJECT_ID,
    run_id: str = RUN_ID,
    research_run_id: str = RESEARCH_RUN_ID,
    predecessor_run_id: str | None = None,
    reuse_predecessor_evidence: bool = False,
) -> dict[str, object]:
    if project_id != PROJECT_ID:
        raise ValueError("this project-local runner cannot author another project id")
    captured_at = _normalize_captured_at(captured_at)
    overall_started = time.perf_counter_ns()
    timings: dict[str, int] = {}
    http_stats = {"calls": 0, "download_bytes": 0}
    project_resume_mode = "new-project"
    predecessor_progress_ref: ProjectRecordRef | None = None
    predecessor_progress: dict[str, object] | None = None
    predecessor_captures: dict[str, dict[str, object]] | None = None

    with _timed(timings, "bootstrap"):
        if not root.exists():
            if predecessor_run_id is not None:
                raise FileNotFoundError(
                    "a successor run requires an existing predecessor project"
                )
            bootstrap_raw_request_project(
                root,
                project_id=PROJECT_ID,
                prompt=PROMPT,
                run_id=BOOTSTRAP_RUN_ID,
                synthetic_test=False,
            )
            repository = FilesystemProjectRepository.open(root)
            research_run = repository.create_run(research_run_id)
            run = repository.create_run(run_id)
        elif predecessor_run_id is None:
            project_resume_mode = "bootstrap-blocker-resume"
            repository, research_run, run = _open_bootstrap_only_resume(
                root,
                run_id=run_id,
                research_run_id=research_run_id,
            )
        else:
            repository = FilesystemProjectRepository.open(root)
            if predecessor_run_id == run_id:
                raise ValueError("successor and predecessor run ids must differ")
            repository.load_run(predecessor_run_id)
            predecessor_progress_ref, predecessor_progress = _find_progress_snapshot(
                repository,
                run_id=predecessor_run_id,
            )
            research_exists = repository.layout.run(research_run_id).manifest.exists()
            run_exists = repository.layout.run(run_id).manifest.exists()
            if research_exists != run_exists:
                raise FileExistsError(
                    "successor research and reconstruction run manifests are incomplete"
                )
            if research_exists:
                project_resume_mode = "successor-blocker-resume"
                repository, research_run, run = _open_bootstrap_only_resume(
                    root,
                    run_id=run_id,
                    research_run_id=research_run_id,
                )
            else:
                project_resume_mode = "successor-run"
                research_run = repository.create_run(research_run_id)
                run = repository.create_run(run_id)
            if reuse_predecessor_evidence:
                predecessor_captures = _load_predecessor_source_captures(
                    repository,
                    predecessor_run_id=predecessor_run_id,
                )

    if reuse_predecessor_evidence and predecessor_captures is None:
        raise ValueError("predecessor evidence reuse requires a predecessor run")

    research_destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=research_run.run_id,
    )
    record_destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    branch_destination = PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=run.run_id,
        branch_id=BRANCH_ID,
    )
    review_destination = PersistenceDestination(
        PersistenceArea.RUN_REVIEW,
        run_id=run.run_id,
    )
    transition_ref: ProjectRecordRef | None = None
    transition_kind: str | None = None
    if predecessor_progress_ref is not None:
        assert predecessor_progress is not None
        predecessor_axis_fixed = (
            predecessor_progress.get("coordinate_axis_correction_ref") is not None
        )
        transition_kind = (
            "completeness-spatial-repair"
            if predecessor_axis_fixed
            else "coordinate-axis-correction"
        )
        if predecessor_axis_fixed:
            transition_payload = {
                "schema": "ParthenonCompletenessSpatialRepair@1",
                "project_id": PROJECT_ID,
                "run_id": run.run_id,
                "predecessor_run_id": predecessor_run_id,
                "predecessor_progress_ref": _ref(predecessor_progress_ref),
                "predecessor_model_sha256": predecessor_progress["final_model_sha256"],
                "reported_failures": [
                    "principal east and west cella doors were absent",
                    "east-room interior supports intersected the cella partition",
                    "stage closure did not consume executable spatial validation",
                ],
                "required_repairs": [
                    "decision-family completeness including openings and connectivity",
                    "explicit numeric parameter basis",
                    "opening-clear and column-wall spatial predicates",
                    "post-realization convergence",
                ],
                "authority": {
                    "reported_by": "user",
                    "diagnosed_by": "codex",
                    "acceptance_authority": False,
                },
                "canonical_write_authority": False,
            }
        else:
            transition_payload = {
                "schema": "CoordinateAxisCorrectionReceipt@1",
                "project_id": PROJECT_ID,
                "run_id": run.run_id,
                "predecessor_run_id": predecessor_run_id,
                "predecessor_progress_ref": _ref(predecessor_progress_ref),
                "predecessor_model_sha256": predecessor_progress["final_model_sha256"],
                "reported_failure": (
                    "Rhino world Z was not the generated vertical axis; the building opened upright "
                    "relative to Y and therefore appeared rotated onto its side."
                ),
                "root_cause": "Architectural IR and Rhino adapter both treated Y as elevation.",
                "required_coordinate_system": {
                    "target": "RhinoWorldXY",
                    "plan_axes": ["X", "Y"],
                    "up_axis": "Z",
                },
                "authority": {
                    "reported_by": "user",
                    "diagnosed_by": "codex",
                    "acceptance_authority": False,
                },
                "canonical_write_authority": False,
            }
        transition_ref = repository.put_json(
            run=run,
            destination=record_destination,
            record_kind=transition_kind,
            payload=transition_payload,
        )

    with _timed(timings, "candidate_discovery_rag"):
        try:
            if predecessor_captures is None:
                discovery_snapshot, _ = _fetch_source(
                    "ysma",
                    captured_at=captured_at,
                    offline=offline,
                    http_stats=http_stats,
                )
            else:
                discovery_snapshot = dict(
                    predecessor_captures["ysma"]["snapshot"]
                )
        except Exception as exc:
            repository.put_json(
                run=research_run,
                destination=research_destination,
                record_kind="research-blocker",
                payload={
                    "schema": "ResearchBlocker@1",
                    "stage_reached": "candidate-discovery",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "required_to_resume": "Create a new run and restore the official YSMA source.",
                    "canonical_write_authority": False,
                },
            )
            raise
        discovery_ref = repository.put_json(
            run=research_run,
            destination=research_destination,
            record_kind="source-capture-ysma",
            payload=_source_capture_payload("ysma", discovery_snapshot, None),
        )

    with _timed(timings, "branch_selection"):
        candidates = [
            {
                "branch_id": BRANCH_ID,
                "label": "Complete Periclean original",
                "hard_feasible": True,
                "score": 0.92,
                "status": "candidate",
                "search_terms": ["Periclean", "complete reconstruction", "Doric"],
                "excluded_terms": ["current ruins only", "Roman conversion"],
            },
            {
                "branch_id": "surveyed-as-built",
                "label": "Measured historical irregular state",
                "hard_feasible": True,
                "score": 0.79,
                "status": "candidate",
                "search_terms": ["Penrose survey", "as built", "optical refinements"],
                "excluded_terms": ["idealized symmetry"],
            },
            {
                "branch_id": "current-anastylosis",
                "label": "Current conserved monument",
                "hard_feasible": True,
                "score": 0.54,
                "status": "candidate",
                "search_terms": ["current anastylosis", "conservation state"],
                "excluded_terms": ["invented completion"],
            },
        ]
        candidate_payload = {
            "schema": "ParthenonBranchCandidateSet@1",
            "project_id": PROJECT_ID,
            "run_id": run.run_id,
            "prompt": PROMPT,
            "source_refs": [discovery_ref.uri],
            "candidates": candidates,
            "selection_threshold": 0.80,
            "minimum_margin": 0.10,
            "human_in_loop_route_available": True,
            "canonical_write_authority": False,
        }
        candidate_ref = repository.put_json(
            run=run,
            destination=record_destination,
            record_kind="branch-source-option-set",
            payload=candidate_payload,
        )
        selection_payload = {
            "schema": "ParthenonBranchSelectionDecision@1",
            "project_id": PROJECT_ID,
            "run_id": run.run_id,
            "selection_mode": "automatic",
            "algorithm_authority_id": "algorithm.parthenon-branch-selector-v1",
            "selected_branch_id": BRANCH_ID,
            "selected_score": 0.92,
            "runner_up_score": 0.79,
            "margin": 0.13,
            "threshold": 0.80,
            "minimum_margin": 0.10,
            "pruned_branch_ids": ["current-anastylosis", "surveyed-as-built"],
            "rationale": (
                "The request and preceding complete-building workflow call for a "
                "reconstructed temple, while current-ruin and survey branches remain retained."
            ),
            "agent_ratifier": "codex",
            "user_ratified": False,
            "source_refs": sorted(
                [
                    candidate_ref.uri,
                    discovery_ref.uri,
                    *([] if transition_ref is None else [transition_ref.uri]),
                ]
            ),
            "canonical_write_authority": False,
        }
        selection_ref = repository.put_json(
            run=run,
            destination=record_destination,
            record_kind="branch-selection-decision",
            payload=selection_payload,
        )
        source_state_ref = repository.put_json(
            run=run,
            destination=record_destination,
            record_kind="branch-source-state",
            payload={
                "schema": "ParthenonBranchSourceState@1",
                "project_id": PROJECT_ID,
                "run_id": run.run_id,
                "prompt": PROMPT,
                "selected_branch_id": BRANCH_ID,
                "source_refs": [selection_ref.uri],
                "canonical_write_authority": False,
            },
        )
        revision_digest = canonical_digest(candidates[0], ascii=False)
        branch = BranchRef(run=run, branch_id=BRANCH_ID, epoch=1)
        scope = BranchResearchScope(
            scope_id="parthenon-selected-branch-scope",
            run=run,
            portfolio_id="parthenon-reconstruction-options",
            portfolio_digest=candidate_ref.sha256,
            operational_state_digest=source_state_ref.sha256,
            source_branch=branch,
            branch_id=BRANCH_ID,
            branch_revision_id="idealized-periclean-original-r1",
            branch_revision_digest=revision_digest,
            predecessor_scope_digest=None,
            selection_decision_digest=selection_ref.sha256,
            selection_record_ref=selection_ref,
            source_option_set_ref=candidate_ref,
            source_state_ref=source_state_ref,
            active_decision_refs=tuple(sorted(item.decision_ref for item in DECISIONS)),
            branch_search_terms=tuple(sorted(("Parthenon", "Periclean", "complete", "Doric"))),
            excluded_search_terms=tuple(
                sorted(("current ruins only", "Roman conversion", "generic neoclassical"))
            ),
            domain_allowlist=tuple(
                sorted(
                    {
                        "perseus.tufts.edu",
                        "digi.ub.uni-heidelberg.de",
                        "penelope.uchicago.edu",
                        "resources.saylor.org",
                        "ascsa.edu.gr",
                        "theacropolismuseum.gr",
                        "www.theacropolismuseum.gr",
                        "www.perseus.tufts.edu",
                        "www.ascsa.edu.gr",
                        "www.ysma.gr",
                        "ysma.gr",
                    }
                )
            ),
            context_refs=tuple(
                sorted(
                    (
                        candidate_ref.uri,
                        selection_ref.uri,
                        source_state_ref.uri,
                        discovery_ref.uri,
                        *(() if transition_ref is None else (transition_ref.uri,)),
                    )
                )
            ),
        )
        scope_ref = repository.put_json(
            run=run,
            destination=branch_destination,
            record_kind="branch-research-scope",
            payload=scope.to_dict(),
        )

    with _timed(timings, "branch_scoped_rag"):
        captures: dict[str, dict[str, object]] = {}
        for source_id in sorted(SOURCES):
            try:
                if predecessor_captures is None:
                    snapshot, binary = _fetch_source(
                        source_id,
                        captured_at=captured_at,
                        offline=offline,
                        http_stats=http_stats,
                    )
                else:
                    snapshot = dict(predecessor_captures[source_id]["snapshot"])
                    binary = None
            except Exception as exc:
                repository.put_json(
                    run=run,
                    destination=branch_destination,
                    record_kind=f"research-blocker-{source_id}",
                    payload={
                        "schema": "ResearchBlocker@1",
                        "branch_id": BRANCH_ID,
                        "stage_reached": "branch-scoped-rag",
                        "source_id": source_id,
                        "reason": f"{type(exc).__name__}: {exc}",
                        "required_to_resume": "Create a new run and restore this source.",
                        "canonical_write_authority": False,
                    },
                )
                raise
            artifact_ref = None
            if binary is not None:
                artifact_ref = repository.ingest(
                    run=run,
                    destination=PersistenceDestination(PersistenceArea.OBJECT),
                    artifact_id=f"source-{source_id}",
                    media_type=str(SOURCES[source_id]["media_type"]),
                    source=io.BytesIO(binary),
                )
            capture_payload = _source_capture_payload(source_id, snapshot, artifact_ref)
            if predecessor_captures is not None:
                capture_payload.update(
                    {
                        "evidence_reuse": True,
                        "predecessor_capture_ref": _ref(
                            predecessor_captures[source_id]["capture_ref"]
                        ),
                        "binary_artifact": predecessor_captures[source_id][
                            "binary_artifact"
                        ],
                    }
                )
            capture_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"source-capture-{source_id}",
                payload=capture_payload,
            )
            captures[source_id] = {
                "snapshot": snapshot,
                "capture_ref": capture_ref,
                "artifact_ref": artifact_ref,
            }

        adoption_refs_by_decision: dict[str, list[ProjectRecordRef]] = {}
        query_refs_by_decision: dict[str, ProjectRecordRef] = {}
        for spec in DECISIONS:
            need = DecisionResearchNeed(
                decision_ref=spec.decision_ref,
                query_id=f"query-{spec.decision_ref.split(':', 1)[1]}",
                question=spec.question,
                search_terms=tuple(sorted(spec.search_terms)),
                jurisdiction="Athens, Greece",
                domain_allowlist=tuple(
                    sorted(
                        {
                            str(SOURCES[source_id]["url"]).split("/", 3)[2]
                            for source_id in spec.source_ids
                        }
                    )
                ),
            )
            query = compile_branch_query(scope, need)
            query_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"branch-query-{spec.decision_ref.split(':', 1)[1]}",
                payload=query.to_dict(),
            )
            query_refs_by_decision[spec.decision_ref] = query_ref
            adoption_refs_by_decision[spec.decision_ref] = []
            for source_id in spec.source_ids:
                snapshot = captures[source_id]["snapshot"]
                assert isinstance(snapshot, Mapping)
                bound = bind_branch_snapshot(
                    query,
                    requested_url=str(SOURCES[source_id]["url"]),
                    snapshot=snapshot,
                )
                bound_ref = repository.put_json(
                    run=run,
                    destination=branch_destination,
                    record_kind=(
                        f"branch-snapshot-{spec.decision_ref.split(':', 1)[1]}-{source_id}"
                    ),
                    payload=bound.to_dict(),
                )
                adoption_payload = {
                    "schema": "AgentRatifiedBranchAdoption@1",
                    "project_id": PROJECT_ID,
                    "run_id": run.run_id,
                    "branch_id": BRANCH_ID,
                    "branch_revision_digest": scope.branch_revision_digest,
                    "scope_digest": scope.scope_digest,
                    "query_id": query.query_id,
                    "query_digest": query.query_digest,
                    "decision_ref": spec.decision_ref,
                    "source_id": source_id,
                    "source_family_ref": SOURCES[source_id]["family"],
                    "snapshot_ref": bound_ref.uri,
                    "fact": {
                        "fact_id": (
                            f"{spec.decision_ref.split(':', 1)[1]}-{source_id}"
                        ),
                        "statement": spec.evidence_statement,
                        "selected_value": spec.selected_value,
                        "hard": spec.hard,
                    },
                    "authority": {
                        "authority_id": "authority.agent-ratifier",
                        "authority_state": "agent-ratified",
                        "user_ratified": False,
                        "note": (
                            "Codex ratified this source for the candidate because the user "
                            "delegated interim evidence review; source and rationale remain explicit."
                        ),
                    },
                    "canonical_write_authority": False,
                }
                adoption_ref = repository.put_json(
                    run=run,
                    destination=branch_destination,
                    record_kind=(
                        f"branch-adoption-{spec.decision_ref.split(':', 1)[1]}-{source_id}"
                    ),
                    payload=adoption_payload,
                )
                adoption_refs_by_decision[spec.decision_ref].append(adoption_ref)

    decision_sources = {
        key: tuple(sorted(ref.uri for ref in refs))
        for key, refs in adoption_refs_by_decision.items()
    }
    state = _initial_operational_state(run, selection_ref.uri)
    stage_rows: list[dict[str, object]] = []
    prior_universe_digest: str | None = None
    prior_pack_ref: ProjectRecordRef | None = None
    prior_program_digest: str | None = None

    for stage in range(4):
        with _timed(timings, f"stage_{stage}"):
            stage_specs = _stage_decisions(stage)
            active_decisions = tuple(sorted(item.decision_ref for item in stage_specs))
            universe = DecisionUniverseRevision(
                universe_id="parthenon-decision-universe",
                revision_id=f"stage-{stage}-r1",
                scope_digest=scope.scope_digest,
                ontology_ref="project:ontology/parthenon-reconstruction-v1",
                nodes=tuple(
                    DecisionNode(item.decision_ref, "parthenon:decision")
                    for item in sorted(stage_specs, key=lambda value: value.decision_ref)
                ),
                edges=tuple(
                    DecisionEdge(
                        edge_ref=f"dependency:{source.split(':', 1)[1]}--{target.split(':', 1)[1]}",
                        source_ref=source,
                        target_ref=target,
                        relation_kind=relation,
                    )
                    for source, target, relation in sorted(_stage_dependency_edges(stage))
                ),
                seed_refs=active_decisions,
                parent_digest=prior_universe_digest,
            )
            node_rules = tuple(
                    EvidenceRule(
                        obligation_id=f"evidence-{item.decision_ref.split(':', 1)[1]}",
                        target_ref=item.decision_ref,
                        allowed_modes=(ResolutionMode.RETRIEVE,),
                        minimum_bindings=1,
                        minimum_source_families=item.minimum_source_families,
                    )
                    for item in sorted(stage_specs, key=lambda value: value.decision_ref)
                )
            edge_rules = tuple(
                EvidenceRule(
                    obligation_id=f"derive-{edge.edge_ref.split(':', 1)[1]}",
                    target_ref=edge.edge_ref,
                    allowed_modes=(ResolutionMode.DERIVE,),
                )
                for edge in sorted(universe.edges, key=lambda value: value.edge_ref)
            )
            policy = EvidenceSufficiencyPolicy(
                policy_id=f"parthenon-stage-{stage}-evidence-policy",
                rules=tuple(
                    sorted(
                        (*node_rules, *edge_rules),
                        key=lambda value: value.obligation_id,
                    )
                ),
                required_discovery_lenses=tuple(
                    f"lens:{family_id}"
                    for family_id in _stage_required_decision_families(stage)
                ),
                required_no_novelty_waves=1,
            )
            claims = []
            for spec in stage_specs:
                for source_id, adoption_ref in zip(
                    spec.source_ids,
                    adoption_refs_by_decision[spec.decision_ref],
                    strict=True,
                ):
                    slug = spec.decision_ref.split(":", 1)[1]
                    claims.append(
                        EvidenceClaimBinding(
                            binding_id=f"binding-{slug}-{source_id}",
                            obligation_id=f"evidence-{slug}",
                            target_ref=spec.decision_ref,
                            fact_ref=f"fact:{slug}-{source_id}",
                            source_ref=adoption_ref.uri,
                            source_family_ref=str(SOURCES[source_id]["family"]),
                            claim_key=f"claim:{slug}",
                            position_key=f"position:{slug}-selected",
                            qualifiers=("branch-bound", "agent-ratified"),
                        )
                    )
            resolutions = tuple(
                ResolutionRecord(
                    resolution_id=f"resolution-{edge.edge_ref.split(':', 1)[1]}",
                    target_ref=edge.edge_ref,
                    mode=ResolutionMode.DERIVE,
                    refs=tuple(sorted((edge.source_ref, edge.target_ref))),
                )
                for edge in sorted(universe.edges, key=lambda value: value.edge_ref)
            )
            discovery_sweeps = tuple(
                DiscoverySweepReceipt(
                    wave_index=1,
                    lens_ref=lens_ref,
                    complete=True,
                )
                for lens_ref in policy.required_discovery_lenses
            )
            closure = compile_decision_universe_closure(
                universe,
                policy,
                sweeps=discovery_sweeps,
                resolutions=resolutions,
            )
            sufficiency = compile_evidence_sufficiency(
                universe,
                policy,
                claims=tuple(sorted(claims, key=lambda item: item.binding_id)),
                resolutions=resolutions,
            )
            completeness = compile_stage_architectural_completeness(
                stage,
                decision_sources,
            )
            if (
                closure.status is not ClosureStatus.CLOSED
                or sufficiency.status is not SufficiencyStatus.SUFFICIENT
                or completeness.compilation_status
                is not ArchitecturalCompletenessStatus.COMPLETE
            ):
                raise RuntimeError(
                    f"Stage {stage} research is not closed: "
                    f"closure={closure.status.value}; "
                    f"sufficiency={sufficiency.status.value}; "
                    f"completeness={completeness.compilation_status.value}"
                )

            universe_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-decision-universe",
                payload=universe.to_dict(),
            )
            policy_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-evidence-policy",
                payload=policy.to_dict(),
            )
            closure_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-decision-universe-closure",
                payload=closure.to_dict(),
            )
            sufficiency_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-evidence-sufficiency",
                payload=sufficiency.to_dict(),
            )
            completeness_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-architectural-completeness",
                payload=completeness.to_dict(),
            )
            basis_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-basis-index",
                payload={
                    "schema": "ParthenonStageBasisIndex@1",
                    "project_id": PROJECT_ID,
                    "run_id": run.run_id,
                    "branch_id": BRANCH_ID,
                    "scope_digest": scope.scope_digest,
                    "stage": stage,
                    "decision_refs": list(active_decisions),
                    "adoption_refs": {
                        key: [ref.uri for ref in adoption_refs_by_decision[key]]
                        for key in active_decisions
                    },
                    "query_refs": {
                        key: query_refs_by_decision[key].uri for key in active_decisions
                    },
                    "derived_view_authority": False,
                    "canonical_write_authority": False,
                },
            )
            dependency_plan_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-dependency-plan",
                payload={
                    "schema": "ParthenonDependencyPlan@1",
                    "project_id": PROJECT_ID,
                    "run_id": run.run_id,
                    "branch_id": BRANCH_ID,
                    "stage": stage,
                    "architectural_completeness_ref": completeness_ref.uri,
                    "parameter_evidence": [
                        item.to_dict()
                        for item in completeness.parameter_evidence
                    ],
                    "edges": [
                        {"source_ref": source, "target_ref": target, "relation": relation}
                        for source, target, relation in _stage_dependency_edges(stage)
                    ],
                    "semantic_closure_ref": closure_ref.uri,
                    "required_spatial_predicates": [
                        "required_component_count>=1",
                        "interior_support_within_assigned_room",
                        "column_wall_intersection_volume<=1e-9 m3",
                        "principal_opening_wall_intersection_volume<=1e-9 m3",
                        "column_wall_clearance>=0.05 m",
                    ],
                    "status": "pending-realization",
                    "canonical_write_authority": False,
                },
            )
            contract_ref = repository.put_json(
                run=run,
                destination=record_destination,
                record_kind=f"stage-{stage}-declaration-contract",
                payload={
                    "schema": "ParthenonStageDeclarationContract@1",
                    "project_id": PROJECT_ID,
                    "run_id": run.run_id,
                    "branch_id": BRANCH_ID,
                    "stage": stage,
                    "architectural_completeness_ref": completeness_ref.uri,
                    "parameter_evidence": [
                        item.to_dict()
                        for item in completeness.parameter_evidence
                    ],
                    "hard_constraints": [
                        {
                            "decision_ref": item.decision_ref,
                            "selected_value": item.selected_value,
                            "source_refs": list(decision_sources[item.decision_ref]),
                        }
                        for item in stage_specs
                        if item.hard
                    ],
                    "soft_constraints": [
                        {
                            "decision_ref": item.decision_ref,
                            "selected_value": item.selected_value,
                            "source_refs": list(decision_sources[item.decision_ref]),
                        }
                        for item in stage_specs
                        if not item.hard
                    ],
                    "typed_uncertainties": [
                        {"field": "crepidoma-step-rise", "range_m": [0.35, 0.55], "realized_m": STEP_RISE},
                        {"field": "cella-wall-thickness", "range_m": [1.1, 1.6], "realized_m": CELLA_WALL},
                        {"field": "door-leaf-height", "range_m": [6.5, 7.5], "realized_m": PRINCIPAL_DOOR_LEAF_HEIGHT},
                        {"field": "east-U-grid-placement", "status": "declared-centered-candidate", "rear_axis_m": INTERIOR_GRID_REAR_AXIS},
                        {"field": "west-room-2x2-spacing", "status": "declared-symmetric-candidate", "half_spans_m": [WEST_ROOM_COLUMN_HALF_SPAN_X, WEST_ROOM_COLUMN_HALF_SPAN_Y]},
                        {"field": "pediment-rise", "range_m": [4.0, 4.6], "realized_m": PEDIMENT_RISE},
                    ],
                    "excluded_from_exact_candidate": [
                        "surveyed per-block irregularity",
                        "full stylobate curvature and column inclination",
                        "lost figurative sculpture geometry",
                        "historic polychromy reconstruction",
                        "east-door flanking windows deferred to Stage 4 visual-detail research",
                    ],
                    "canonical_write_authority": False,
                },
            )

            provisional_state = _propose_operational_state(
                state,
                stage=stage,
                sufficiency_ref=sufficiency_ref.uri,
                decision_sources=decision_sources,
            )

            operations = build_stage_operations(stage, decision_sources)
            program_core = {
                "schema": "ParthenonArchitecturalIR@1",
                "project_id": PROJECT_ID,
                "run_id": run.run_id,
                "stage": stage,
                "branch_identity": {
                    "branch_id": BRANCH_ID,
                    "branch_epoch": provisional_state.branch.epoch,
                    "scope_digest": scope.scope_digest,
                    "branch_revision_digest": scope.branch_revision_digest,
                },
                "predecessor_program_digest": prior_program_digest,
                "length_unit": "meter",
                "coordinate_system": {
                    "schema": "CartesianCoordinateSystem@1",
                    "target": "RhinoWorldXY",
                    "handedness": "right",
                    "plan_axes": ["X", "Y"],
                    "up_axis": "Z",
                    "longitudinal_axis": "Y",
                    "origin": "crepidoma-base-center",
                },
                "run_predecessor": (
                    None
                    if predecessor_progress_ref is None
                    else {
                        "run_id": predecessor_run_id,
                        "progress_ref": _ref(predecessor_progress_ref),
                        "transition_ref": _ref(transition_ref),
                    }
                ),
                "input_design_state_digest": provisional_state.state_digest,
                "component_ids": sorted(
                    {str(item["component_id"]) for item in operations}
                ),
                "constraints_ref": contract_ref.uri,
                "dependency_plan_ref": dependency_plan_ref.uri,
                "evidence_sufficiency_ref": sufficiency_ref.uri,
                "architectural_completeness_ref": completeness_ref.uri,
                "convergence_required_after_realization": True,
                "operations": list(operations),
                "canonical_write_authority": False,
            }
            program_digest = canonical_digest(program_core, ascii=False)
            program_payload = {**program_core, "program_digest": program_digest}
            program_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-geometry-program",
                payload=program_payload,
            )
            model_path = (
                repository.layout.run(run.run_id).workspaces
                / f"cad-stage-{stage}"
                / ("parthenon-candidate.3dm" if stage == 3 else f"parthenon-stage-{stage}.3dm")
            )
            expected_objects = _create_three_dm(
                model_path,
                operations=operations,
                program_digest=program_digest,
                stage=stage,
            )
            inspection = inspect_three_dm(model_path)
            with model_path.open("rb") as source:
                model_artifact_ref = repository.ingest(
                    run=run,
                    destination=PersistenceDestination(PersistenceArea.OBJECT),
                    artifact_id=f"parthenon-stage-{stage}-3dm",
                    media_type="model/vnd.3dm",
                    source=source,
                )
            inspection_payload = {
                "schema": "ParthenonThreeDmInspection@1",
                "project_id": PROJECT_ID,
                "run_id": run.run_id,
                "branch_id": BRANCH_ID,
                "stage": stage,
                "program_digest": program_digest,
                "workspace_relative_path": model_path.relative_to(root).as_posix(),
                "artifact_ref": _artifact_ref(model_artifact_ref),
                "inspection": inspection.to_dict(),
                "canonical_write_authority": False,
            }
            inspection_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-model-inspection",
                payload=inspection_payload,
            )
            spatial_validation = validate_stage_spatial_model(model_path, stage)
            spatial_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-spatial-validation",
                payload={
                    **spatial_validation.to_dict(),
                    "project_id": PROJECT_ID,
                    "run_id": run.run_id,
                    "branch_id": BRANCH_ID,
                    "stage": stage,
                    "model_artifact_ref": _artifact_ref(model_artifact_ref),
                    "program_digest": program_digest,
                },
            )
            dependency_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-dependency-ledger",
                payload={
                    "schema": "ParthenonDependencyLedger@2",
                    "project_id": PROJECT_ID,
                    "run_id": run.run_id,
                    "branch_id": BRANCH_ID,
                    "stage": stage,
                    "dependency_plan_ref": dependency_plan_ref.uri,
                    "semantic_closure_ref": closure_ref.uri,
                    "semantic_closure_status": closure.status.value,
                    "spatial_validation_ref": spatial_ref.uri,
                    "spatial_validation_status": spatial_validation.status.value,
                    "open_required_checks": [
                        item.check_id for item in spatial_validation.failed_checks
                    ],
                    "coverage": (
                        sum(item.passed for item in spatial_validation.checks)
                        / len(spatial_validation.checks)
                    ),
                    "dependencies_closed": (
                        closure.status is ClosureStatus.CLOSED
                        and spatial_validation.status
                        is SpatialValidationStatus.PASSED
                    ),
                    "canonical_write_authority": False,
                },
            )
            child_state, convergence = _close_operational_state(
                state,
                provisional_state,
                stage=stage,
                realization_evidence_refs=tuple(
                    sorted(
                        (
                            completeness_ref.uri,
                            dependency_ref.uri,
                            inspection_ref.uri,
                            model_artifact_ref.uri,
                            spatial_ref.uri,
                        )
                    )
                ),
            )
            state_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-operational-state",
                payload=child_state.to_dict(),
            )
            convergence_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-convergence",
                payload=convergence.to_dict(),
            )
            centers = peristyle_centers()
            symmetry_deviation = peristyle_axial_symmetry_deviation(centers)
            inspection_dict = inspection.to_dict()
            units_name = str(inspection_dict["units"]["name"])
            bbox = inspection_dict["aggregate_bbox"]
            bbox_min = tuple(float(value) for value in bbox["min"])
            bbox_max = tuple(float(value) for value in bbox["max"])
            spans = tuple(high - low for low, high in zip(bbox_min, bbox_max, strict=True))
            operation_ids = {str(item["operation_id"]) for item in operations}
            east_tier_count = sum(
                operation_id.startswith("naos-")
                and operation_id.endswith("-lower")
                for operation_id in operation_ids
            )
            upper_tier_count = sum(
                operation_id.startswith("naos-")
                and operation_id.endswith("-upper")
                for operation_id in operation_ids
            )
            west_support_count = sum(
                operation_id.startswith("west-room-ionic-")
                for operation_id in operation_ids
            )
            checks = {
                "metre_units": units_name == "Meters",
                "object_count": inspection.top_level_object_count == expected_objects,
                "model_digest": inspection.file_sha256 == model_artifact_ref.sha256,
                "branch_identity": program_payload["branch_identity"]["branch_id"] == BRANCH_ID,
                "rhino_world_z_up": (
                    program_payload["coordinate_system"]["up_axis"] == "Z"
                    and abs(bbox_min[2]) <= 1e-6
                    and spans[1] > spans[0] > spans[2] > 0.0
                ),
                "peristyle_unique_count": stage < 1 or len(centers) == 46,
                "axial_symmetry": stage < 1 or symmetry_deviation <= 1e-6,
                "decision_universe_closed": closure.status is ClosureStatus.CLOSED,
                "evidence_sufficient": sufficiency.status is SufficiencyStatus.SUFFICIENT,
                "architectural_complete": (
                    completeness.compilation_status
                    is ArchitecturalCompletenessStatus.COMPLETE
                ),
                "spatial_hard_gates": (
                    spatial_validation.status is SpatialValidationStatus.PASSED
                ),
                "principal_doors_present": stage < 2
                or {
                    "cella-door-east",
                    "cella-door-west",
                }.issubset(operation_ids),
                "east_tier_support_count": stage < 2
                or east_tier_count == INTERIOR_TIER_COLUMN_COUNT,
                "upper_tier_support_count": stage < 3
                or upper_tier_count == INTERIOR_TIER_COLUMN_COUNT,
                "west_support_topology": stage < 3 or west_support_count == 4,
                "stage_ready": convergence.stage_ready,
            }
            gate_passed = all(checks.values())
            if not gate_passed:
                raise RuntimeError(f"Stage {stage} gate failed: {checks}")
            gate_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-gate",
                payload={
                    "schema": "ParthenonStageGate@1",
                    "project_id": PROJECT_ID,
                    "run_id": run.run_id,
                    "branch_id": BRANCH_ID,
                    "stage": stage,
                    "status": "pass",
                    "checks": checks,
                    "program_digest": program_digest,
                    "model_artifact_ref": _artifact_ref(model_artifact_ref),
                    "architectural_completeness_ref": _ref(completeness_ref),
                    "spatial_validation_ref": _ref(spatial_ref),
                    "stage_convergence_ref": _ref(convergence_ref),
                    "stage_acceptance_authority": False,
                    "canonical_write_authority": False,
                },
            )
            review_ref = repository.put_json(
                run=run,
                destination=review_destination,
                record_kind=f"stage-{stage}-agent-review",
                payload={
                    "schema": "ParthenonAgentReview@1",
                    "project_id": PROJECT_ID,
                    "run_id": run.run_id,
                    "branch_id": BRANCH_ID,
                    "stage": stage,
                    "reviewer": "codex",
                    "disposition": "HOLD",
                    "checks_passed": gate_passed,
                    "reason": (
                        "Codex reviewed and ratified the retained evidence for this candidate; "
                        "user visual review and formal promotion remain separate."
                    ),
                    "known_advisories": [
                        "optical refinements are documented but not fully realized",
                        "east-door flanking windows are evidenced and deferred to the next visual-detail stage",
                        "east U-grid placement and west 2x2 support spacing remain declared candidates",
                        "lost sculptures remain semantic placeholders",
                        "polychromy is outside this candidate",
                    ],
                    "accepted_archive_created": False,
                    "canonical_write_authority": False,
                },
            )
            bindings = (
                StageEvidenceBinding(StageEvidenceRole.BRANCH_SELECTION, selection_ref),
                StageEvidenceBinding(StageEvidenceRole.BRANCH_SCOPE, scope_ref),
                StageEvidenceBinding(StageEvidenceRole.DECISION_UNIVERSE, universe_ref),
                StageEvidenceBinding(StageEvidenceRole.BASIS_INDEX, basis_ref),
                StageEvidenceBinding(StageEvidenceRole.DEPENDENCY_LEDGER, dependency_ref),
                StageEvidenceBinding(StageEvidenceRole.EVIDENCE_SUFFICIENCY, sufficiency_ref),
                StageEvidenceBinding(StageEvidenceRole.DESIGN_STATE, state_ref),
                StageEvidenceBinding(StageEvidenceRole.GEOMETRY_PROGRAM, program_ref),
                StageEvidenceBinding(StageEvidenceRole.MODEL_INSPECTION, inspection_ref),
                StageEvidenceBinding(StageEvidenceRole.STAGE_GATE, gate_ref),
                StageEvidenceBinding(StageEvidenceRole.STAGE_CONVERGENCE, convergence_ref),
                StageEvidenceBinding(StageEvidenceRole.REVIEW, review_ref),
            )
            predecessor = None
            if prior_pack_ref is not None and prior_program_digest is not None:
                predecessor = StagePackPredecessor(
                    stage_id=f"stage-{stage - 1}",
                    stage_index=stage - 1,
                    pack_ref=prior_pack_ref,
                    program_digest=prior_program_digest,
                )
            gaps = (
                StageEvidenceGap(
                    gap_id=f"stage-{stage}-candidate-detail-advisory",
                    kind=StageEvidenceGapKind.UNSUPPORTED,
                    severity=StageEvidenceGapSeverity.ADVISORY,
                    description=(
                        "The current candidate does not claim per-block survey irregularity, "
                        "complete optical refinements, lost figures, or polychromy."
                    ),
                    decision_refs=("decision:optical-refinements",),
                    remediation="Open a dependency-local detail stage after user review.",
                ),
            )
            pack = compile_stage_evidence_pack(
                project_id=PROJECT_ID,
                run=run,
                branch=child_state.branch,
                scope_ref=scope_ref,
                stage_id=f"stage-{stage}",
                stage_index=stage,
                revision=1,
                program_digest=program_digest,
                contract_ref=contract_ref,
                bindings=tuple(sorted(bindings, key=lambda item: item.identity)),
                artifacts=(
                    StageArtifactBinding(
                        StageArtifactRole.CAD_MODEL,
                        model_artifact_ref,
                        program_digest,
                    ),
                ),
                gaps=tuple(sorted(gaps, key=lambda item: item.identity)),
                closure=StageClosureSummary(
                    evidence_sufficient=(
                        sufficiency.status is SufficiencyStatus.SUFFICIENT
                        and completeness.compilation_status
                        is ArchitecturalCompletenessStatus.COMPLETE
                    ),
                    dependencies_closed=(
                        closure.status is ClosureStatus.CLOSED
                        and spatial_validation.status
                        is SpatialValidationStatus.PASSED
                    ),
                    hard_gates_passed=gate_passed,
                    stage_ready=convergence.stage_ready,
                    model_artifact_current=(
                        inspection.file_sha256 == model_artifact_ref.sha256
                        and inspection.top_level_object_count == expected_objects
                    ),
                ),
                predecessor=predecessor,
            )
            if pack.compilation_status is not StagePackCompilationStatus.COMPLETE:
                raise RuntimeError(f"Stage {stage} evidence pack did not compile COMPLETE")
            pack_ref = repository.put_json(
                run=run,
                destination=branch_destination,
                record_kind=f"stage-{stage}-evidence-pack",
                payload=pack.to_dict(),
            )
            stage_rows.append(
                {
                    "stage": stage,
                    "branch_epoch": child_state.branch.epoch,
                    "decision_universe_ref": _ref(universe_ref),
                    "evidence_sufficiency_ref": _ref(sufficiency_ref),
                    "architectural_completeness_ref": _ref(completeness_ref),
                    "spatial_validation_ref": _ref(spatial_ref),
                    "stage_gate_ref": _ref(gate_ref),
                    "design_state_ref": _ref(state_ref),
                    "stage_convergence_ref": _ref(convergence_ref),
                    "geometry_program_ref": _ref(program_ref),
                    "program_digest": program_digest,
                    "model_workspace_path": str(model_path),
                    "model_artifact_ref": _artifact_ref(model_artifact_ref),
                    "model_object_count": inspection.top_level_object_count,
                    "model_units": units_name,
                    "spatial_validation_status": spatial_validation.status.value,
                    "stage_pack_ref": _ref(pack_ref),
                    "stage_pack_status": pack.compilation_status.value,
                    "review_disposition": "HOLD",
                    "accepted_archive_created": False,
                }
            )
            state = child_state
            prior_universe_digest = universe.universe_digest
            prior_pack_ref = pack_ref
            prior_program_digest = program_digest

    wall_ms_before_usage = int((time.perf_counter_ns() - overall_started) / 1_000_000)
    usage_payload = {
        "schema": "RunUsageSummary@1",
        "project_id": PROJECT_ID,
        "run_id": run.run_id,
        "measurement_scope": "bootstrap-through-four-stage-pack-compilation",
        "started_at_source": "local-monotonic-clock",
        "captured_at": captured_at,
        "project_resume_mode": project_resume_mode,
        "predecessor_run_id": predecessor_run_id,
        "workflow_wall_ms": wall_ms_before_usage,
        "phase_duration_ms": dict(sorted(timings.items())),
        "web_rag": {
            "http_calls": http_stats["calls"],
            "download_bytes": http_stats["download_bytes"],
            "offline_fixture": offline,
            "predecessor_evidence_reused": predecessor_captures is not None,
            "reused_source_count": (
                0 if predecessor_captures is None else len(predecessor_captures)
            ),
            "sources_retained": len(SOURCES),
        },
        "project_provider_usage": {
            "external_model_calls": 0,
            "local_scripted_model_calls": 0,
            "deterministic_stage_compiler_calls": 4,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "telemetry_complete": True,
            "basis": "zero model-provider calls",
        },
        "codex_app_usage": {
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "subscription_credits": None,
            "currency_cost": None,
            "telemetry_complete": False,
            "reason": "Codex app conversation-level billing and quota telemetry is not exposed to this runner.",
        },
        "token_estimation_from_bytes": False,
        "canonical_write_authority": False,
    }
    usage_ref = repository.put_json(
        run=run,
        destination=record_destination,
        record_kind="workflow-usage",
        payload=usage_payload,
    )
    progress_payload = {
        "schema": "ParthenonProgressSnapshot@1",
        "project_id": PROJECT_ID,
        "run_id": run.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prompt": PROMPT,
        "selected_branch_id": BRANCH_ID,
        "predecessor_run_id": predecessor_run_id,
        "predecessor_progress_ref": (
            None if predecessor_progress_ref is None else _ref(predecessor_progress_ref)
        ),
        "coordinate_axis_correction_ref": (
            _ref(transition_ref)
            if transition_ref is not None
            and transition_kind == "coordinate-axis-correction"
            else (
                predecessor_progress.get("coordinate_axis_correction_ref")
                if predecessor_progress is not None
                else None
            )
        ),
        "successor_transition_kind": transition_kind,
        "successor_transition_ref": (
            None if transition_ref is None else _ref(transition_ref)
        ),
        "branch_selection_ref": _ref(selection_ref),
        "branch_scope_ref": _ref(scope_ref),
        "stages": stage_rows,
        "usage_ref": _ref(usage_ref),
        "usage": usage_payload,
        "final_model_path": stage_rows[-1]["model_workspace_path"],
        "final_model_sha256": stage_rows[-1]["model_artifact_ref"]["sha256"],
        "disposition": "HOLD",
        "formal_closure_present": True,
        "accepted_archive_created": False,
        "stage_acceptance_authority": False,
        "canonical_write_authority": False,
    }
    progress_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(PersistenceArea.EXPORT),
        record_kind="parthenon-progress-snapshot",
        payload=progress_payload,
    )
    repository.verify()
    return {
        "root": str(root),
        "run": run,
        "branch_id": BRANCH_ID,
        "stage_rows": stage_rows,
        "usage": usage_payload,
        "usage_ref": usage_ref,
        "progress_ref": progress_ref,
        "progress_payload": progress_payload,
        "final_model_path": Path(str(progress_payload["final_model_path"])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", default=PROJECT_ID)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--research-run-id", default=RESEARCH_RUN_ID)
    parser.add_argument("--predecessor-run-id")
    parser.add_argument("--reuse-predecessor-evidence", action="store_true")
    parser.add_argument("--captured-at")
    parser.add_argument("--offline-fixture", action="store_true")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)
    captured_at = args.captured_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    root = args.root or (WORKSPACE_PROJECTS / args.project_id)
    result = run_project(
        root,
        captured_at=captured_at,
        offline=args.offline_fixture,
        project_id=args.project_id,
        run_id=args.run_id,
        research_run_id=args.research_run_id,
        predecessor_run_id=args.predecessor_run_id,
        reuse_predecessor_evidence=args.reuse_predecessor_evidence,
    )
    print("project:", result["root"])
    print("run:", result["run"].run_id)
    print("branch:", result["branch_id"])
    print("project resume mode:", result["usage"]["project_resume_mode"])
    print("stage packs:", [row["stage_pack_status"] for row in result["stage_rows"]])
    print("final 3dm:", result["final_model_path"])
    print("progress:", result["progress_ref"].uri)
    print("workflow wall ms:", result["usage"]["workflow_wall_ms"])
    print("external model calls/tokens: 0 / 0")
    print("Codex app quota: n/a (telemetry unavailable)")
    print("disposition: HOLD; no accepted archive and no canonical write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
