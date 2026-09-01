#!/usr/bin/env python3
"""P087: evidence-bound Parthenon Stage 4 detail reconstruction.

The runner consumes one exact Stage 3 run and one exact visual-region
manifest.  It never discovers a predecessor by directory order or mtime, it
does not replay Stages 0--3, and it never promotes the HOLD candidate to the
canonical P036 ``HEAD``.

Project-specific dimensions, source decisions, and operation ids deliberately
remain in this project runner.  Reusable lineage and persistence authority stay
in :mod:`archflow.capabilities.stage_evidence_pack` and :mod:`archflow.project`.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import re
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import rhino3dm

try:  # Package import in tests; direct import when executed as a script.
    from tools import parthenon_stage4_correction_records as stage4_corrections
    from tools import parthenon_stage4_doors as stage4_doors
    from tools import parthenon_stage4_inner_colonnade as stage4_inner_colonnade
    from tools import parthenon_stage4_relation_contracts as stage4_relation_contracts
    from tools import parthenon_stage4_relations as stage4_relations
    from tools import parthenon_stage4_roof as stage4_roof
    from tools import run_parthenon_reconstruction as stage3
    from tools._probe_paths import resolve_probe_root
except ModuleNotFoundError:  # pragma: no cover - direct CLI import path
    import parthenon_stage4_correction_records as stage4_corrections
    import parthenon_stage4_doors as stage4_doors
    import parthenon_stage4_inner_colonnade as stage4_inner_colonnade
    import parthenon_stage4_relation_contracts as stage4_relation_contracts
    import parthenon_stage4_relations as stage4_relations
    import parthenon_stage4_roof as stage4_roof
    import run_parthenon_reconstruction as stage3
    from _probe_paths import resolve_probe_root

from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow.adapters.three_dm_witness import (
    add_axis_aligned_box_brep_witnesses,
    primary_three_dm_objects,
)
from archflow.capabilities.stage_evidence_pack import (
    CrossRunStagePackPredecessor,
    StageArtifactBinding,
    StageArtifactRole,
    StageClosureSummary,
    StageEvidenceBinding,
    StageEvidencePack,
    StageEvidenceRole,
    StagePackCompilationStatus,
    compile_stage_evidence_pack,
)
from archflow.capabilities.stage_component_coverage import (
    OperationDisposition,
    OperationLineageResolution,
    PredecessorOperationDisposition,
    StageComponentCoverageStatus,
    StageOperation,
    StageOperationRef,
    compile_stage_component_coverage,
)
from archflow.project import (
    BranchRef,
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectArtifactRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.state import OperationalMarkovState
from archflow.state.operational_state import (
    DesignObligation,
    ObligationCondition,
    ObligationStatus,
)
from archflow.state.stage_convergence import (
    StageConvergenceEvidence,
    StageConvergenceOutcome,
    StageConvergencePolicy,
    StageTransitionKind,
    StageTransitionRequest,
    evaluate_stage_convergence,
)


PROJECT_ID = "parthenon-reconstruction"
RESEARCH_RUN_ID = "research-007"
RUN_ID = "reconstruction-006"
PREDECESSOR_RUN_ID = "reconstruction-004"
VISUAL_RUN_ID = "research-005"
BRANCH_ID = "idealized-periclean-original"

CANONICAL_VERSION = 0
CANONICAL_STATE_SHA256 = (
    "2aa733c5fb565428a4c08aed1d278135d571bded474b64f89b6039a6ee44f266"
)
PREDECESSOR_PROGRESS_SHA256 = (
    "ac778e607e87f1f9625121f3e70742b28c0dffcc1426365f893d3b98b4f4799a"
)
PREDECESSOR_STATE_SHA256 = (
    "cd7234b80a502afdcfb4251b945f5e90a4bc97dff634398315448ebbcd122bed"
)
PREDECESSOR_STATE_DIGEST = (
    "2fbffcb6c1f7b8f2502f53acb0c9e899fa99d127043a8ce557d986babea13a32"
)
PREDECESSOR_PACK_SHA256 = (
    "ac4d8ee9ac6910eb48b7349f92b6271556b13a9d5e166b799d7a7e2ed855a3e3"
)
PREDECESSOR_PROGRAM_SHA256 = (
    "ba4eb7bbb0b32a2100880bedddcf52abf8a51afa9cf343a7390be46d2b88ef6c"
)
PREDECESSOR_PROGRAM_DIGEST = (
    "9f54dbc72b758948d6c78aa738c9877195ec8cd616bba35d845dfac53ec948c6"
)
PREDECESSOR_MODEL_SHA256 = (
    "b5815fa4f8f7b04fc276585cc760e37366527c94eeb8f992541e3cec47dae7f0"
)
PREDECESSOR_INSPECTION_SHA256 = (
    "9918c6d6088898ad113de2e88a83af69f180cdf4b5acd0527df506f12174166a"
)
PREDECESSOR_SPATIAL_SHA256 = (
    "c07db4eb9f3547111bc1f61c5b642378cb4b5fc0802add672d6a968dce7b3969"
)
VISUAL_MANIFEST_SHA256 = (
    "d3ff9f427bbe4126d8346ad3be18831b638ea8f08d18e3b007234d7e9a4da7bd"
)

PREDECESSOR_PROGRESS_REF = ProjectRecordRef(
    project_id=PROJECT_ID,
    relative_path=(
        "exports/parthenon-progress-snapshot-"
        f"{PREDECESSOR_PROGRESS_SHA256}.json"
    ),
    sha256=PREDECESSOR_PROGRESS_SHA256,
)
PREDECESSOR_STATE_REF = ProjectRecordRef(
    project_id=PROJECT_ID,
    relative_path=(
        f"runs/{PREDECESSOR_RUN_ID}/branches/{BRANCH_ID}/records/"
        f"stage-3-operational-state-{PREDECESSOR_STATE_SHA256}.json"
    ),
    sha256=PREDECESSOR_STATE_SHA256,
)
PREDECESSOR_PACK_REF = ProjectRecordRef(
    project_id=PROJECT_ID,
    relative_path=(
        f"runs/{PREDECESSOR_RUN_ID}/branches/{BRANCH_ID}/records/"
        f"stage-3-evidence-pack-{PREDECESSOR_PACK_SHA256}.json"
    ),
    sha256=PREDECESSOR_PACK_SHA256,
)
PREDECESSOR_PROGRAM_REF = ProjectRecordRef(
    project_id=PROJECT_ID,
    relative_path=(
        f"runs/{PREDECESSOR_RUN_ID}/branches/{BRANCH_ID}/records/"
        f"stage-3-geometry-program-{PREDECESSOR_PROGRAM_SHA256}.json"
    ),
    sha256=PREDECESSOR_PROGRAM_SHA256,
)
PREDECESSOR_INSPECTION_REF = ProjectRecordRef(
    project_id=PROJECT_ID,
    relative_path=(
        f"runs/{PREDECESSOR_RUN_ID}/branches/{BRANCH_ID}/records/"
        f"stage-3-model-inspection-{PREDECESSOR_INSPECTION_SHA256}.json"
    ),
    sha256=PREDECESSOR_INSPECTION_SHA256,
)
PREDECESSOR_SPATIAL_REF = ProjectRecordRef(
    project_id=PROJECT_ID,
    relative_path=(
        f"runs/{PREDECESSOR_RUN_ID}/branches/{BRANCH_ID}/records/"
        f"stage-3-spatial-validation-{PREDECESSOR_SPATIAL_SHA256}.json"
    ),
    sha256=PREDECESSOR_SPATIAL_SHA256,
)
VISUAL_MANIFEST_REF = ProjectRecordRef(
    project_id=PROJECT_ID,
    relative_path=(
        f"runs/{VISUAL_RUN_ID}/branches/{BRANCH_ID}/records/"
        f"visual-region-manifest-{VISUAL_MANIFEST_SHA256}.json"
    ),
    sha256=VISUAL_MANIFEST_SHA256,
)

FAILED_STAGE4_PROGRAM_REF = ProjectRecordRef(
    project_id=PROJECT_ID,
    relative_path=(
        "runs/reconstruction-005/branches/idealized-periclean-original/records/"
        "stage-4-geometry-program-"
        f"{stage4_corrections.FAILED_STAGE4_PROGRAM_SHA256}.json"
    ),
    sha256=stage4_corrections.FAILED_STAGE4_PROGRAM_SHA256,
)
FAILED_STAGE4_MODEL_RELATIVE_PATH = (
    "runs/reconstruction-005/workspaces/cad-stage-4/"
    "parthenon-stage-4-detail-candidate.3dm"
)

PREDECESSOR_MODEL_RELATIVE_PATH = (
    f"runs/{PREDECESSOR_RUN_ID}/workspaces/cad-stage-3/parthenon-candidate.3dm"
)
MODEL_WORKSPACE_RELATIVE_PATH = (
    f"runs/{RUN_ID}/workspaces/cad-stage-4/"
    "parthenon-stage-4-detail-candidate.3dm"
)
ASSET_DOCUMENT_WORKSPACE_RELATIVE_PATH = (
    f"runs/{RESEARCH_RUN_ID}/workspaces/asset-rag/_外部资产来源清单.md"
)
CORRECTION_DOCUMENT_WORKSPACE_RELATIVE_PATH = (
    f"runs/{RESEARCH_RUN_ID}/workspaces/asset-rag/_Stage4纠错与人工授权.md"
)

PREDECESSOR_BBOX = {
    "min": (-17.24, -36.55, 0.0),
    "max": (17.24, 36.55, 19.3799991607666),
}

# Column height includes the capital.  The Stage 4 shaft is therefore shorter
# than the total column, unlike the Stage 3 schematic shaft.
EXTERIOR_TOTAL_COLUMN_HEIGHT = 10.43
CAPITAL_TOTAL_HEIGHT = 0.865
CAPITAL_NECK_HEIGHT = 0.150
CAPITAL_ECHINUS_HEIGHT = 0.370
CAPITAL_ABACUS_HEIGHT = CAPITAL_TOTAL_HEIGHT - CAPITAL_NECK_HEIGHT - CAPITAL_ECHINUS_HEIGHT
EXTERIOR_SHAFT_HEIGHT = EXTERIOR_TOTAL_COLUMN_HEIGHT - CAPITAL_TOTAL_HEIGHT
NORMAL_LOWER_DIAMETER = 1.91
NORMAL_UPPER_DIAMETER = 1.488
CORNER_LOWER_DIAMETER = 1.95
CORNER_UPPER_DIAMETER = 1.520
ENTASIS_MAX = 0.0167
ENTASIS_LOCATION_RATIO = 0.40
FLUTE_COUNT = 20

ENTABLATURE_LAYER_HEIGHTS = {
    "architrave": 1.15,
    "frieze": 1.28,
    "cornice": 0.87,
}
WINDOW_WIDTH = 2.50
WINDOW_HEIGHT = 2.75
WINDOW_SILL_Z = 4.00
WINDOW_CENTERS_X = (-6.25, 6.25)

SELECTED_COLUMN_ROIS = (
    "roi_ysma_12_11_center_fluting",
    "roi_ysma_12_11_left_fluting",
    "roi_ysma_11_1_outer_peristyle",
)
SELECTED_ENTABLATURE_ROIS = (
    "roi_ysma_12_11_entablature",
    "roi_ysma_12_7_frieze_entablature",
)
SELECTED_OPENING_ROIS = (
    "roi_ysma_11_6_central_doorway",
    "roi_ysma_11_6_far_wall",
)
SELECTED_ROOF_ROIS = (
    # Both regions are selected in the exact research-005 manifest.  They may
    # support only architectural presence/morphology; the roof compiler keeps
    # all introduced metric choices explicitly SOFT and never measures pixels.
    "roi_ysma_11_1_outer_peristyle",
    "roi_ysma_12_11_entablature",
)
SELECTED_INNER_COLONNADE_ROIS = (
    "roi_ysma_11_1_inner_colonnade",
    "roi_ysma_12_11_center_fluting",
)
SELECTED_DOOR_ROIS = (
    # This is an exact selected research-005 ROI.  It supports doorway
    # presence/topology only; the typed human decision authorizes the SOFT
    # closed-double-leaf candidate and does not turn pixels into dimensions.
    "roi_ysma_11_6_central_doorway",
)
REQUIRED_STAGE4_SELECTED_ROIS = tuple(
    sorted(
        {
            *SELECTED_COLUMN_ROIS,
            *SELECTED_ENTABLATURE_ROIS,
            *SELECTED_OPENING_ROIS,
            *SELECTED_ROOF_ROIS,
            *SELECTED_INNER_COLONNADE_ROIS,
            *SELECTED_DOOR_ROIS,
        }
    )
)

STAGE4_CLOSE_GATE_NAMES = (
    "model_readback",
    "spatial",
    "detail",
    "evidence",
    "roof_eaves_pediment",
    "inner_colonnade",
    "door_assembly",
    "correction_provenance",
    "successor_relations",
    "component_coverage",
    "material_bindings",
)


class ParthenonStage4Error(RuntimeError):
    """An exact predecessor, evidence, geometry, or persistence gate failed."""


class AssetLoginRequired(ParthenonStage4Error):
    """A candidate cannot be adopted without an explicit user login."""


TEXT_SOURCES: tuple[dict[str, object], ...] = (
    {
        "source_id": "ysma-parthenon",
        "canonical_url": "https://www.ysma.gr/en/monuments/parthenon/",
        "title": "The Parthenon",
        "author_or_publisher": "Acropolis Restoration Service (YSMA)",
        "publication_date": None,
        "license_or_access": "official public web page; all rights reserved",
        "evidence_role": "branch chronology, original topology, restoration separation",
    },
    {
        "source_id": "ysma-doric-morphology",
        "canonical_url": "https://learnmore.ancienttemple.ysma.gr/morphology-of-ancient-temples/?lang=en",
        "title": "Morphology of Ancient Temples",
        "author_or_publisher": "Acropolis Restoration Service (YSMA)",
        "publication_date": None,
        "license_or_access": "official public web page; all rights reserved",
        "evidence_role": "Doric member identity and morphological sequence",
    },
    {
        "source_id": "official-parthenon-frieze",
        "canonical_url": "https://www.parthenonfrieze.gr/en/",
        "title": "The Parthenon Frieze",
        "author_or_publisher": "Hellenic Ministry of Culture and Acropolis Museum",
        "publication_date": None,
        "license_or_access": "official public educational resource",
        "evidence_role": "frieze identity; no authority for missing sculptural figures",
    },
    {
        "source_id": "perseus-parthenon",
        "canonical_url": (
            "https://www.perseus.tufts.edu/hopper/artifact?"
            "name=Athens%2C+Parthenon&object=Building"
        ),
        "title": "Athens, Parthenon",
        "author_or_publisher": "Perseus Digital Library, Tufts University",
        "publication_date": None,
        "license_or_access": "public scholarly catalogue",
        "evidence_role": "cross-check overall dimensions and exterior order",
    },
    {
        "source_id": "penrose-1888",
        "canonical_url": "https://doi.org/10.11588/diglit.2984",
        "title": "An Investigation of the Principles of Athenian Architecture",
        "author_or_publisher": "Francis Cranmer Penrose; Macmillan and Co.",
        "publication_date": "1888",
        "license_or_access": "public-domain scan",
        "evidence_role": "measured column, capital, entasis, and entablature dimensions",
    },
    {
        "source_id": "bommelaer-doric",
        "canonical_url": "https://www.persee.fr/doc/ista_0000-0000_1984_ant_294_1_3340",
        "title": "Chapiteaux du Parthenon",
        "author_or_publisher": "Jean-Francois Bommelaer; Universite de Franche-Comte / Persee",
        "publication_date": "1984",
        "license_or_access": "authoritative digital scholarly copy; no CC licence asserted",
        "evidence_role": "position-aware capital morphology; exact top diameter and height remain SOFT",
    },
    {
        "source_id": "stevens-hs3",
        "canonical_url": (
            "https://www.ascsa.edu.gr/uploads/media/oa_ebooks/"
            "oa_hesperia_supplements/HS3.pdf"
        ),
        "title": "The Setting of the Periclean Parthenon",
        "author_or_publisher": "Gorham P. Stevens; American School of Classical Studies at Athens",
        "publication_date": "1940",
        "license_or_access": "ASCSA open-access PDF",
        "evidence_role": "principal doorway and Periclean setting cross-check",
    },
    {
        "source_id": "bsa-east-windows-2025",
        "canonical_url": "https://doi.org/10.1017/S0068245424000145",
        "title": "Illuminating the Parthenon",
        "author_or_publisher": "Juan de Lara; Annual of the British School at Athens",
        "publication_date": "2025",
        "license_or_access": "CC BY 4.0",
        "evidence_role": (
            "two openings flanking the east door, approximately 2.5 m wide and "
            "2.5-3.0 m high, in line with and above the lateral aisles; all "
            "metric realization and exact centre positions remain SOFT"
        ),
    },
    {
        "source_id": "acropolis-museum-entablature",
        "canonical_url": (
            "https://www.theacropolismuseum.gr/en/"
            "parthenon-west-beam-bearer-block-entablature"
        ),
        "title": "Parthenon west beam-bearing entablature block",
        "author_or_publisher": "Acropolis Museum",
        "publication_date": None,
        "license_or_access": "official museum object page",
        "evidence_role": "entablature member identity and material evidence",
    },
    {
        "source_id": "attic-inscriptions",
        "canonical_url": "https://www.atticinscriptions.com/",
        "title": "Attic Inscriptions Online",
        "author_or_publisher": "Attic Inscriptions Online",
        "publication_date": None,
        "license_or_access": "public scholarly corpus",
        "evidence_role": "identify and exclude later readable inscriptions from the selected branch",
    },
)


EVIDENCE_CLAIMS: tuple[dict[str, object], ...] = (
    {
        "claim_id": "exterior-column-total-height-and-lower-diameter",
        "classification": "HARD",
        "value": {"total_height_m": 10.43, "normal_lower_diameter_m": 1.91},
        "source_ids": ("penrose-1888", "perseus-parthenon"),
        "reason": "independent measured/catalogue cross-check; total column is distinct from shaft",
    },
    {
        "claim_id": "exterior-shaft-upper-diameter-and-entasis",
        "classification": "MIXED_HARD_SOFT",
        "value": {
            "normal_upper_diameter_m": NORMAL_UPPER_DIAMETER,
            "maximum_entasis_m": ENTASIS_MAX,
            "maximum_location_height_ratio": ENTASIS_LOCATION_RATIO,
        },
        "source_ids": ("penrose-1888", "bommelaer-doric"),
        "reason": "entasis position is cross-checked by YSMA; exact amplitude and top diameter remain source-bound SOFT values",
    },
    {
        "claim_id": "doric-capital-sequence-and-height",
        "classification": "MIXED_HARD_SOFT",
        "value": {
            "sequence": ("neck", "echinus", "abacus"),
            "total_height_m": CAPITAL_TOTAL_HEIGHT,
        },
        "source_ids": ("penrose-1888", "ysma-doric-morphology"),
        "reason": "capital sequence is HARD; 0.865 m is a Bommelaer-bound SOFT choice inside the 0.864-0.866 m range",
    },
    {
        "claim_id": "entablature-total-and-three-layer-stack",
        "classification": "MIXED_HARD_SOFT",
        "value": {"total_height_m": 3.30, "layers": ENTABLATURE_LAYER_HEIGHTS},
        "source_ids": ("penrose-1888", "perseus-parthenon", "acropolis-museum-entablature"),
        "reason": "3.30 m total and three-layer identity are HARD; individual layer thicknesses are explicit SOFT decomposition choices",
    },
    {
        "claim_id": "metope-and-triglyph-rhythm",
        "classification": "HARD",
        "value": {"metopes": (14, 32, 32, 14), "triglyphs": (15, 33, 33, 15)},
        "source_ids": ("ysma-doric-morphology", "penrose-1888"),
        "reason": "Doric alternation is realized without inventing lost relief figures",
    },
    {
        "claim_id": "east-paired-window-reconstruction",
        "classification": "SOFT",
        "value": {
            "count": 2,
            "flanks": "east-principal-door",
            "relative_alignment": "in-line-with-and-above-lateral-aisles",
            "reported_approximate_width_m": 2.5,
            "reported_height_range_m": (2.5, 3.0),
            "candidate_width_m": WINDOW_WIDTH,
            "candidate_height_m": WINDOW_HEIGHT,
        },
        "source_ids": ("bsa-east-windows-2025",),
        "reason": (
            "single recent reconstruction study; paired topology and lateral-aisle "
            "relation are adopted, while the exact centre and metric realization "
            "remain SOFT and are not measured from ROI pixels"
        ),
    },
    {
        "claim_id": "selected-visual-regions-topology-only",
        "classification": "VISUAL_TOPOLOGY",
        "value": {
            "selected_count": 14,
            "cannot_authorize": "exact_dimension",
        },
        "source_ids": ("ysma-parthenon",),
        "reason": "selected ROIs support only existence, morphology, topology, and relative position",
    },
    {
        "claim_id": "west-room-ionic-detail",
        "classification": "PARKED",
        "value": None,
        "source_ids": ("ysma-parthenon",),
        "reason": "Stage 3 four-axis topology is inherited; Stage 4 Ionic detail lacks sufficient evidence",
    },
    {
        "claim_id": "later-inscriptions-and-interventions",
        "classification": "BRANCH_EXCLUDED",
        "value": (
            "Nero bronze-letter traces",
            "shield holes",
            "medieval inscriptions and paintings",
            "modern restoration and construction traces",
        ),
        "source_ids": ("ysma-parthenon", "attic-inscriptions"),
        "reason": "not part of the idealized Periclean-original branch",
    },
)


OPEN_ASSET_CANDIDATES: tuple[dict[str, object], ...] = (
    {
        "asset_id": "zenodo-parthenon-photogrammetry",
        "canonical_page_url": "https://zenodo.org/records/10319004",
        "direct_download_url": "public GLB listed by the record; deliberately not downloaded",
        "author_or_publisher": "Zenodo depositor / original Sketchfab creator",
        "license": "CC BY on the linked original model",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "downloaded_at_utc": None,
        "bytes": None,
        "media_type": None,
        "format": "GLB",
        "compression": "container metadata available; geometry compression not adopted",
        "sha256": None,
        "object_ref": None,
        "workspace_path": None,
        "native_units": "unknown",
        "native_units_evidence": None,
        "up_axis": "unknown",
        "handedness": "unknown",
        "axis_evidence": None,
        "bbox": None,
        "mesh_stats": "about 450k triangles reported by the source, not independently adopted",
        "converter": None,
        "scale_transform": None,
        "axis_transform": None,
        "derived_sha256": None,
        "intended_use": "measured-detail donor candidate",
        "evidence_role": "optional geometry asset",
        "adoption_decision": "PARKED",
        "requires_login": False,
        "decided_by": "codex",
        "decision_reason": "native metric scale, up-axis, handedness, and survey calibration are not evidenced; ruined/current-condition geometry also leaks the selected branch",
    },
    {
        "asset_id": "zenodo-south-metope-iii",
        "canonical_page_url": "https://zenodo.org/records/21648767",
        "direct_download_url": None,
        "author_or_publisher": "Scan the World / Zenodo aggregator",
        "license": "linked metadata indicates CC BY-NC-SA 4.0; landing rights are incomplete",
        "license_url": None,
        "downloaded_at_utc": None,
        "bytes": None,
        "media_type": None,
        "format": "GLB",
        "compression": "Draco candidate",
        "sha256": None,
        "object_ref": None,
        "workspace_path": None,
        "native_units": "unknown",
        "native_units_evidence": None,
        "up_axis": "unknown",
        "handedness": "unknown",
        "axis_evidence": None,
        "bbox": None,
        "mesh_stats": None,
        "converter": None,
        "scale_transform": None,
        "axis_transform": None,
        "derived_sha256": None,
        "intended_use": "metope sculpture candidate",
        "evidence_role": "optional sculptural asset",
        "adoption_decision": "PARKED",
        "requires_login": False,
        "decided_by": "codex",
        "decision_reason": "native units, axes, and calibration are absent and sculptural figures are PARKED for this branch",
    },
    {
        "asset_id": "ut-austin-east-pediment-cast",
        "canonical_page_url": "https://battlecasts.la.utexas.edu/3D-model-entries/parthpedmerged-model.html",
        "direct_download_url": None,
        "author_or_publisher": "Battle Casts, University of Texas at Austin",
        "license": "CC0 on the model page",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "downloaded_at_utc": None,
        "bytes": None,
        "media_type": None,
        "format": "web 3D model candidate",
        "compression": "unknown",
        "sha256": None,
        "object_ref": None,
        "workspace_path": None,
        "native_units": "unknown",
        "native_units_evidence": None,
        "up_axis": "unknown",
        "handedness": "unknown",
        "axis_evidence": None,
        "bbox": None,
        "mesh_stats": None,
        "converter": None,
        "scale_transform": None,
        "axis_transform": None,
        "derived_sha256": None,
        "intended_use": "east pediment reference candidate",
        "evidence_role": "optional sculptural asset",
        "adoption_decision": "PARKED",
        "requires_login": False,
        "decided_by": "codex",
        "decision_reason": "19th-century painted plaster cast, not architectural survey geometry; native mesh units and axes are unproved and sculpture is PARKED",
    },
    {
        "asset_id": "sketchfab-doric-column",
        "canonical_page_url": "https://sketchfab.com/3d-models/doric-column-dff6bae2e8e24a17b7976245eee64a02",
        "direct_download_url": None,
        "author_or_publisher": "individual Sketchfab creator; page claims Parthenon-derived dimensions",
        "license": "CC BY claimed on page; download still requires user login",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "downloaded_at_utc": None,
        "bytes": None,
        "media_type": None,
        "format": None,
        "compression": None,
        "sha256": None,
        "object_ref": None,
        "workspace_path": None,
        "native_units": "unknown",
        "native_units_evidence": None,
        "up_axis": "unknown",
        "handedness": "unknown",
        "axis_evidence": None,
        "bbox": None,
        "mesh_stats": None,
        "converter": None,
        "scale_transform": None,
        "axis_transform": None,
        "derived_sha256": None,
        "intended_use": "Doric column detail donor candidate",
        "evidence_role": "optional geometry asset",
        "adoption_decision": "BLOCKED_LOGIN",
        "requires_login": True,
        "decided_by": "codex",
        "decision_reason": "download requires user login and anonymous page access returned 403; no attempt made",
    },
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _record_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
        "uri": ref.uri,
    }


def _artifact_dict(ref: ProjectArtifactRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "artifact_id": ref.artifact_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
        "uri": ref.uri,
    }


def _branch_destination(run_id: str) -> PersistenceDestination:
    return PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=run_id,
        branch_id=BRANCH_ID,
    )


def _run_destination(run_id: str) -> PersistenceDestination:
    return PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)


def _review_destination(run_id: str) -> PersistenceDestination:
    return PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run_id)


def _normalize_captured_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("captured_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("captured_at must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _operation_fingerprint(operation: Mapping[str, object]) -> str:
    identity = {
        key: operation[key]
        for key in (
            "operation_id",
            "component_id",
            "kind",
            "parameters",
            "decision_refs",
            "source_refs",
            "material_id",
        )
    }
    return _digest(identity)


def _visual_refs(manifest_ref: str, candidate_ids: Sequence[str]) -> list[str]:
    return [f"{manifest_ref}#candidate={candidate_id}" for candidate_id in candidate_ids]


def _delta_operation(
    *,
    operation_id: str,
    component_id: str,
    kind: str,
    parameters: Mapping[str, object],
    decision_refs: Sequence[str],
    source_refs: Sequence[str],
    visual_refs: Sequence[str],
    basis_classification: str,
    basis_statement: str,
    replaces_operation_id: str | None = None,
    refines_operation_id: str | None = None,
    material_id: str = "pentelic-marble",
) -> dict[str, object]:
    if (replaces_operation_id is None) == (refines_operation_id is None):
        raise ValueError("a Stage 4 delta operation needs exactly one replaces/refines id")
    if not source_refs:
        raise ValueError("a Stage 4 delta operation needs textual evidence")
    if not visual_refs:
        raise ValueError("a Stage 4 delta operation needs a selected ROI reference")
    operation: dict[str, object] = {
        "operation_id": operation_id,
        "component_id": component_id,
        "kind": kind,
        "parameters": dict(parameters),
        "decision_refs": sorted(set(decision_refs)),
        "source_refs": sorted(set(source_refs)),
        "visual_region_refs": sorted(set(visual_refs)),
        "parameter_basis": {
            "classification": basis_classification,
            "statement": basis_statement,
            "source_refs": sorted(set(source_refs)),
            "visual_role": "topology_or_morphology_only_not_exact_dimension",
        },
        "material_id": material_id,
    }
    if replaces_operation_id is not None:
        operation["replaces_operation_id"] = replaces_operation_id
    else:
        operation["refines_operation_id"] = refines_operation_id
    return operation


def _capital_operations(
    shaft_id: str,
    component_id: str,
    center: Sequence[float],
    total_height: float,
    lower_diameter: float,
    upper_diameter: float,
    *,
    replaced_shaft_id: str,
    replaced_capital_id: str,
    source_refs: Sequence[str],
    visual_refs: Sequence[str],
) -> tuple[dict[str, object], ...]:
    x, y, z0 = (float(item) for item in center)
    shaft_height = total_height - CAPITAL_TOTAL_HEIGHT
    shaft = _delta_operation(
        operation_id=shaft_id,
        component_id=component_id,
        kind="doric_shaft",
        parameters={
            "center": [x, y, z0],
            "total_column_height": total_height,
            "shaft_height": shaft_height,
            "capital_height": CAPITAL_TOTAL_HEIGHT,
            "lower_diameter": lower_diameter,
            "upper_diameter": upper_diameter,
            "entasis_max": ENTASIS_MAX,
            "entasis_location_ratio": ENTASIS_LOCATION_RATIO,
            "flutes": FLUTE_COUNT,
            "radial_segments": 120,
        },
        decision_refs=(
            "decision:exterior-column-measures",
            "decision:optical-refinements",
            "decision:peristyle-topology",
        ),
        source_refs=source_refs,
        visual_refs=visual_refs,
        basis_classification="MIXED_HARD_SOFT",
        basis_statement=(
            "total height, lower diameter, 20 flutes, and entasis location are HARD; "
            "upper diameter and exact entasis amplitude remain source-bound SOFT values"
        ),
        replaces_operation_id=replaced_shaft_id,
    )
    capital_bottom = z0 + shaft_height
    cap_source_id = replaced_capital_id
    neck = _delta_operation(
        operation_id=f"{shaft_id}-neck",
        component_id=component_id,
        kind="doric_neck",
        parameters={
            "center": [x, y, capital_bottom],
            "height": CAPITAL_NECK_HEIGHT,
            "lower_diameter": upper_diameter,
            "upper_diameter": upper_diameter * 1.025,
            "host_shaft_id": shaft_id,
        },
        decision_refs=("decision:exterior-column-measures",),
        source_refs=source_refs,
        visual_refs=visual_refs,
        basis_classification="MIXED_HARD_SOFT",
        basis_statement="position-bound Doric sequence is HARD; exact capital subdivision is SOFT",
        refines_operation_id=cap_source_id,
    )
    echinus_bottom = capital_bottom + CAPITAL_NECK_HEIGHT
    echinus = _delta_operation(
        operation_id=f"{shaft_id}-echinus",
        component_id=component_id,
        kind="doric_echinus",
        parameters={
            "center": [x, y, echinus_bottom],
            "height": CAPITAL_ECHINUS_HEIGHT,
            "lower_diameter": upper_diameter * 1.025,
            "upper_diameter": lower_diameter * 1.16,
            "profile": "convex-doric",
            "host_shaft_id": shaft_id,
        },
        decision_refs=("decision:exterior-column-measures",),
        source_refs=source_refs,
        visual_refs=visual_refs,
        basis_classification="MIXED_HARD_SOFT",
        basis_statement="position-bound echinus is HARD morphology; exact profile and height subdivision are SOFT",
        refines_operation_id=cap_source_id,
    )
    abacus_bottom = echinus_bottom + CAPITAL_ECHINUS_HEIGHT
    abacus_width = lower_diameter * 1.25
    abacus = _delta_operation(
        operation_id=f"{shaft_id}-abacus",
        component_id=component_id,
        kind="doric_abacus",
        parameters={
            "origin": [
                x - abacus_width / 2.0,
                y - abacus_width / 2.0,
                abacus_bottom,
            ],
            "size": [abacus_width, abacus_width, CAPITAL_ABACUS_HEIGHT],
            "center_xy": [x, y],
            "host_shaft_id": shaft_id,
        },
        decision_refs=("decision:exterior-column-measures",),
        source_refs=source_refs,
        visual_refs=visual_refs,
        basis_classification="MIXED_HARD_SOFT",
        basis_statement="square abacus is HARD morphology; exact dimensions use Bommelaer-bound SOFT values",
        refines_operation_id=cap_source_id,
    )
    return shaft, neck, echinus, abacus


def _entablature_replacements(
    predecessor_by_id: Mapping[str, Mapping[str, object]],
    *,
    source_refs: Sequence[str],
    visual_refs: Sequence[str],
) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    metope_counts = {"east": 14, "north": 32, "south": 32, "west": 14}
    for side in ("east", "north", "south", "west"):
        old_id = f"entablature-{side}"
        old = predecessor_by_id[old_id]
        params = old["parameters"]
        assert isinstance(params, Mapping)
        origin = [float(item) for item in params["origin"]]
        size = [float(item) for item in params["size"]]
        z = origin[2]
        for layer_name, height in ENTABLATURE_LAYER_HEIGHTS.items():
            layer_origin = [origin[0], origin[1], z]
            layer_size = [size[0], size[1], height]
            result.append(
                _delta_operation(
                    operation_id=f"entablature-{side}-{layer_name}",
                    component_id="entablature",
                    kind="entablature_layer",
                    parameters={
                        "origin": layer_origin,
                        "size": layer_size,
                        "side": side,
                        "layer": layer_name,
                        "total_entablature_height": sum(ENTABLATURE_LAYER_HEIGHTS.values()),
                    },
                    decision_refs=("decision:entablature-pediment",),
                    source_refs=source_refs,
                    visual_refs=visual_refs,
                    basis_classification="MIXED_HARD_SOFT",
                    basis_statement="three-layer identity and 3.30 m total are HARD; individual layer thicknesses are SOFT",
                    refines_operation_id=old_id,
                )
            )
            z += height

        metopes = sorted(
            (
                item
                for item in predecessor_by_id.values()
                if str(item["operation_id"]).startswith(f"metope-{side}-")
            ),
            key=lambda item: str(item["operation_id"]),
        )
        if len(metopes) != metope_counts[side]:
            raise ParthenonStage4Error(f"Stage 3 {side} metope topology drifted")
        along_x = side in {"east", "west"}
        centers: list[float] = []
        for metope in metopes:
            metope_params = metope["parameters"]
            assert isinstance(metope_params, Mapping)
            metope_origin = [float(item) for item in metope_params["origin"]]
            metope_size = [float(item) for item in metope_params["size"]]
            axis = 0 if along_x else 1
            centers.append(metope_origin[axis] + metope_size[axis] / 2.0)
        step = centers[1] - centers[0]
        boundaries = [centers[0] - step / 2.0]
        boundaries.extend((left + right) / 2.0 for left, right in zip(centers, centers[1:]))
        boundaries.append(centers[-1] + step / 2.0)
        for index, position in enumerate(boundaries):
            endpoint = index in {0, len(boundaries) - 1}
            width = 0.32 if endpoint else 0.48
            if along_x:
                if index == 0:
                    x0 = position
                elif index == len(boundaries) - 1:
                    x0 = position - width
                else:
                    x0 = position - width / 2.0
                y0 = 34.75 if side == "east" else -34.91
                triglyph_origin = [x0, y0, origin[2] + ENTABLATURE_LAYER_HEIGHTS["architrave"]]
                triglyph_size = [width, 0.16, ENTABLATURE_LAYER_HEIGHTS["frieze"]]
                rhythm_position = position
            else:
                if index == 0:
                    y0 = position
                elif index == len(boundaries) - 1:
                    y0 = position - width
                else:
                    y0 = position - width / 2.0
                x0 = 15.44 if side == "north" else -15.60
                triglyph_origin = [x0, y0, origin[2] + ENTABLATURE_LAYER_HEIGHTS["architrave"]]
                triglyph_size = [0.16, width, ENTABLATURE_LAYER_HEIGHTS["frieze"]]
                rhythm_position = position
            result.append(
                _delta_operation(
                    operation_id=f"triglyph-{side}-{index:02d}",
                    component_id="decoration",
                    kind="triglyph",
                    parameters={
                        "origin": triglyph_origin,
                        "size": triglyph_size,
                        "side": side,
                        "rhythm_index": index,
                        "rhythm_position": rhythm_position,
                        "alternates_with_metope_count": len(metopes),
                        "glyph_count": 3,
                        "corner_half_triglyph": endpoint,
                    },
                    decision_refs=("decision:decorative-layout", "decision:entablature-pediment"),
                    source_refs=source_refs,
                    visual_refs=visual_refs,
                    basis_classification="HARD",
                    basis_statement="one triglyph at every metope interval boundary; no relief figure generated",
                    refines_operation_id=old_id,
                )
            )
    return tuple(result)


def _east_window_replacements(
    predecessor_by_id: Mapping[str, Mapping[str, object]],
    *,
    source_refs: Sequence[str],
    visual_refs: Sequence[str],
) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    specs = (
        ("left", "cella-wall-east-left", WINDOW_CENTERS_X[0]),
        ("right", "cella-wall-east-right", WINDOW_CENTERS_X[1]),
    )
    for side, old_id, center_x in specs:
        old = predecessor_by_id[old_id]
        params = old["parameters"]
        assert isinstance(params, Mapping)
        origin = [float(item) for item in params["origin"]]
        size = [float(item) for item in params["size"]]
        wall_x0, wall_y0, wall_z0 = origin
        wall_x1 = wall_x0 + size[0]
        wall_z1 = wall_z0 + size[2]
        window_x0 = center_x - WINDOW_WIDTH / 2.0
        window_x1 = center_x + WINDOW_WIDTH / 2.0
        window_z0 = WINDOW_SILL_Z
        window_z1 = WINDOW_SILL_Z + WINDOW_HEIGHT
        pieces = (
            ("outer-pier", [wall_x0, wall_y0, wall_z0], [window_x0 - wall_x0, size[1], size[2]]),
            ("inner-pier", [window_x1, wall_y0, wall_z0], [wall_x1 - window_x1, size[1], size[2]]),
            ("sill", [window_x0, wall_y0, wall_z0], [WINDOW_WIDTH, size[1], window_z0 - wall_z0]),
            ("lintel", [window_x0, wall_y0, window_z1], [WINDOW_WIDTH, size[1], wall_z1 - window_z1]),
        )
        for label, piece_origin, piece_size in pieces:
            if any(value <= 0.0 for value in piece_size):
                raise ParthenonStage4Error("east window decomposition escaped its Stage 3 wall segment")
            result.append(
                _delta_operation(
                    operation_id=f"cella-wall-east-{side}-window-{label}",
                    component_id="cella",
                    kind="box",
                    parameters={
                        "origin": piece_origin,
                        "size": piece_size,
                        "window_side": side,
                        "window_clear": {
                            "center_x": center_x,
                            "width": WINDOW_WIDTH,
                            "sill_z": WINDOW_SILL_Z,
                            "height": WINDOW_HEIGHT,
                        },
                    },
                    decision_refs=("decision:cella-layout", "decision:cella-openings"),
                    source_refs=source_refs,
                    visual_refs=visual_refs,
                    basis_classification="SOFT",
                    basis_statement="single-study paired-window reconstruction; wall void is real and mirrored",
                    refines_operation_id=old_id,
                )
            )
    return tuple(result)


def compile_stage4_operations(
    predecessor_operations: Sequence[Mapping[str, object]],
    *,
    column_source_refs: Sequence[str],
    entablature_source_refs: Sequence[str],
    opening_source_refs: Sequence[str],
    visual_manifest_ref: str,
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    """Compile the explicit 110-operation Stage 3 replacement allowlist."""

    if len(predecessor_operations) != 271:
        raise ParthenonStage4Error("exact Stage 3 program must contain 271 operations")
    predecessor_by_id = {
        str(item["operation_id"]): item for item in predecessor_operations
    }
    if len(predecessor_by_id) != len(predecessor_operations):
        raise ParthenonStage4Error("Stage 3 operation ids are not unique")

    peristyle_shafts = sorted(
        operation_id
        for operation_id, item in predecessor_by_id.items()
        if item["component_id"] == "peristyle" and item["kind"] == "fluted_column"
    )
    peristyle_capitals = sorted(f"{operation_id}-capital" for operation_id in peristyle_shafts)
    porch_shafts = sorted(
        operation_id
        for operation_id, item in predecessor_by_id.items()
        if item["component_id"] == "porches" and item["kind"] == "tapered_column"
    )
    entablatures = tuple(f"entablature-{side}" for side in ("east", "north", "south", "west"))
    east_walls = ("cella-wall-east-left", "cella-wall-east-right")
    replacement_allowlist = tuple(
        sorted((*peristyle_shafts, *peristyle_capitals, *porch_shafts, *entablatures, *east_walls))
    )
    if (
        len(peristyle_shafts) != 46
        or len(peristyle_capitals) != 46
        or len(porch_shafts) != 12
        or len(replacement_allowlist) != 110
        or any(operation_id not in predecessor_by_id for operation_id in replacement_allowlist)
    ):
        raise ParthenonStage4Error("Stage 3 replacement allowlist drifted from 46+46+12+4+2")

    operations: list[dict[str, object]] = [
        copy.deepcopy(dict(item))
        for item in predecessor_operations
        if str(item["operation_id"]) not in replacement_allowlist
    ]
    column_visual_refs = _visual_refs(visual_manifest_ref, SELECTED_COLUMN_ROIS)
    entablature_visual_refs = _visual_refs(visual_manifest_ref, SELECTED_ENTABLATURE_ROIS)
    opening_visual_refs = _visual_refs(visual_manifest_ref, SELECTED_OPENING_ROIS)

    for shaft_id in peristyle_shafts:
        old = predecessor_by_id[shaft_id]
        params = old["parameters"]
        assert isinstance(params, Mapping)
        lower = float(params["diameter"])
        corner = math.isclose(lower, stage3.CORNER_COLUMN_DIAMETER, abs_tol=1e-6)
        operations.extend(
            _capital_operations(
                shaft_id,
                "peristyle",
                params["center"],
                EXTERIOR_TOTAL_COLUMN_HEIGHT,
                CORNER_LOWER_DIAMETER if corner else NORMAL_LOWER_DIAMETER,
                CORNER_UPPER_DIAMETER if corner else NORMAL_UPPER_DIAMETER,
                replaced_shaft_id=shaft_id,
                replaced_capital_id=f"{shaft_id}-capital",
                source_refs=column_source_refs,
                visual_refs=column_visual_refs,
            )
        )
    for shaft_id in porch_shafts:
        old = predecessor_by_id[shaft_id]
        params = old["parameters"]
        assert isinstance(params, Mapping)
        lower = float(params["diameter"])
        operations.extend(
            _capital_operations(
                shaft_id,
                "porches",
                params["center"],
                float(params["height"]),
                lower,
                lower * 0.79,
                replaced_shaft_id=shaft_id,
                replaced_capital_id=shaft_id,
                source_refs=column_source_refs,
                visual_refs=column_visual_refs,
            )
        )
    operations.extend(
        _entablature_replacements(
            predecessor_by_id,
            source_refs=entablature_source_refs,
            visual_refs=entablature_visual_refs,
        )
    )
    operations.extend(
        _east_window_replacements(
            predecessor_by_id,
            source_refs=opening_source_refs,
            visual_refs=opening_visual_refs,
        )
    )
    operations_tuple = tuple(sorted(operations, key=lambda item: str(item["operation_id"])))
    ids = tuple(str(item["operation_id"]) for item in operations_tuple)
    if len(set(ids)) != len(ids):
        raise ParthenonStage4Error("Stage 4 operation ids are not unique")

    preserved = {
        operation_id: _operation_fingerprint(predecessor_by_id[operation_id])
        for operation_id in sorted(set(predecessor_by_id) - set(replacement_allowlist))
    }
    current_by_id = {str(item["operation_id"]): item for item in operations_tuple}
    for operation_id, fingerprint in preserved.items():
        if _operation_fingerprint(current_by_id[operation_id]) != fingerprint:
            raise ParthenonStage4Error(f"non-whitelisted operation changed: {operation_id}")
    delta = tuple(item for item in operations_tuple if str(item["operation_id"]) not in preserved)
    for item in delta:
        if not ({"replaces_operation_id", "refines_operation_id"} & set(item)):
            raise ParthenonStage4Error(f"delta operation lacks lineage: {item['operation_id']}")
        if not item.get("source_refs") or not item.get("visual_region_refs") or not item.get("parameter_basis"):
            raise ParthenonStage4Error(f"delta operation lacks evidence basis: {item['operation_id']}")

    lineage = {
        "schema": "ParthenonStage4IRDelta@1",
        "predecessor_operation_count": len(predecessor_operations),
        "current_operation_count": len(operations_tuple),
        "replacement_allowlist": list(replacement_allowlist),
        "superseded_operation_ids": list(replacement_allowlist),
        "preserved_operation_count": len(preserved),
        "preserved_operation_fingerprints": preserved,
        "delta_operation_ids": [str(item["operation_id"]) for item in delta],
        "explanation": (
            "The 110-item allowlist is exactly 46 peristyle shafts, 46 schematic "
            "capital boxes, 12 porch shafts, four entablature monoliths, and two "
            "east wall segments.  The remaining 161 canonical fingerprints are unchanged."
        ),
    }
    if len(preserved) != 161:
        raise ParthenonStage4Error("Stage 4 did not preserve exactly 161 Stage 3 operations")
    window_result = validate_window_voids(operations_tuple)
    if not window_result["passed"]:
        raise ParthenonStage4Error("compiled east windows are not clear wall voids")
    return operations_tuple, lineage


def _collapse_stage4_lineage_to_stage3(
    predecessor_operations: Sequence[Mapping[str, object]],
    compiler_snapshots: Sequence[Sequence[Mapping[str, object]]],
    *,
    transitive_ancestor_maps: Sequence[Mapping[str, object]] = (),
) -> dict[str, tuple[str, ...]]:
    """Collapse an ordered compiler DAG to the exact Stage 3 roots.

    Each compiler snapshot may replace/refine operations produced by the
    preceding snapshot.  This helper is deliberately reusable across the
    architectural, inner-colonnade, door, roof, and any future compiler layers;
    final operations never need to pretend an intermediate Stage 4 operation
    was a Stage 3 predecessor.
    """

    predecessor_ids = {
        str(operation["operation_id"]) for operation in predecessor_operations
    }
    root_lookup = {
        operation_id: (operation_id,)
        for operation_id in predecessor_ids
    }
    if len(root_lookup) != len(predecessor_operations):
        raise ParthenonStage4Error("Stage 3 lineage roots are not unique")
    if transitive_ancestor_maps and len(transitive_ancestor_maps) != len(
        compiler_snapshots
    ):
        raise ParthenonStage4Error(
            "Stage 4 compiler snapshots and ancestor maps have different denominators"
        )
    ancestor_maps = (
        tuple(transitive_ancestor_maps)
        if transitive_ancestor_maps
        else tuple({} for _ in compiler_snapshots)
    )
    current_roots = dict(root_lookup)
    previous_by_id = {
        str(operation["operation_id"]): operation
        for operation in predecessor_operations
    }
    for snapshot_index, snapshot in enumerate(compiler_snapshots, start=1):
        ancestor_map = ancestor_maps[snapshot_index - 1]
        snapshot_ids = {str(item["operation_id"]) for item in snapshot}
        unknown_overrides = set(str(key) for key in ancestor_map) - snapshot_ids
        if unknown_overrides:
            raise ParthenonStage4Error(
                f"Stage 4 snapshot {snapshot_index} ancestor map names absent operations"
            )
        next_roots: dict[str, tuple[str, ...]] = {}
        for operation in snapshot:
            operation_id = str(operation["operation_id"])
            if operation_id in next_roots:
                raise ParthenonStage4Error(
                    f"Stage 4 compiler snapshot {snapshot_index} has duplicate operation ids"
                )
            override = ancestor_map.get(operation_id)
            if override is not None:
                if not isinstance(override, Mapping):
                    raise ParthenonStage4Error(
                        f"Stage 4 ancestor override is malformed: {operation_id}"
                    )
                raw_roots = override.get("exact_stage3_root_operation_ids")
                if (
                    not isinstance(raw_roots, Sequence)
                    or isinstance(raw_roots, (str, bytes))
                    or not raw_roots
                ):
                    raise ParthenonStage4Error(
                        f"Stage 4 ancestor override has no Stage 3 roots: {operation_id}"
                    )
                roots = tuple(sorted(set(str(item) for item in raw_roots)))
                if any(item not in predecessor_ids for item in roots):
                    raise ParthenonStage4Error(
                        f"Stage 4 ancestor override escaped Stage 3: {operation_id}"
                    )
            elif (
                operation_id in previous_by_id
                and operation == previous_by_id[operation_id]
            ):
                # Copied operations retain their historical lineage fields.
                # Preserve the already-collapsed root set instead of
                # reinterpreting those fields at every later compiler layer.
                roots = root_lookup[operation_id]
            else:
                immediate = operation.get(
                    "replaces_operation_id",
                    operation.get("refines_operation_id", operation_id),
                )
                immediate_id = str(immediate)
                roots = root_lookup.get(immediate_id)
                if roots is None:
                    raise ParthenonStage4Error(
                        "Stage 4 compiler lineage is unbound at snapshot "
                        f"{snapshot_index}: {operation_id} -> {immediate_id}"
                    )
            next_roots[operation_id] = roots
        root_lookup.update(next_roots)
        current_roots = next_roots
        previous_by_id = {
            str(operation["operation_id"]): operation for operation in snapshot
        }
    return current_roots


def compile_full_building_stage4_operations(
    predecessor_operations: Sequence[Mapping[str, object]],
    *,
    column_source_refs: Sequence[str],
    entablature_source_refs: Sequence[str],
    opening_source_refs: Sequence[str],
    visual_manifest_ref: str,
    human_authorization_ref: str,
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    """Compile the complete architectural Stage 4 successor.

    ``compile_stage4_operations`` remains the frozen 110-target P087 delta so
    its contract can be tested independently.  This outer compiler then
    corrects the inherited inner U-colonnade/window relation, compiles the
    explicitly human-authorized SOFT principal-door candidate, replaces the
    seven inherited roof/pediment/cornice proxies, and resolves every resulting
    successor operation transitively back to its exact Stage 3 root set.  No
    intermediate Stage 4 operation becomes a second predecessor authority.
    """

    architectural_operations, architectural_lineage = compile_stage4_operations(
        predecessor_operations,
        column_source_refs=column_source_refs,
        entablature_source_refs=entablature_source_refs,
        opening_source_refs=opening_source_refs,
        visual_manifest_ref=visual_manifest_ref,
    )
    inner_visual_refs = _visual_refs(
        visual_manifest_ref,
        SELECTED_INNER_COLONNADE_ROIS,
    )
    inner_operations, inner_lineage = (
        stage4_inner_colonnade.compile_inner_colonnade_delta(
            architectural_operations,
            textual_evidence_refs=tuple(
                sorted(set((*column_source_refs, *opening_source_refs)))
            ),
            selected_visual_refs=inner_visual_refs,
        )
    )
    door_visual_refs = _visual_refs(visual_manifest_ref, SELECTED_DOOR_ROIS)
    door_delta, door_lineage = stage4_doors.compile_door_assembly_delta(
        inner_operations,
        textual_evidence_refs=opening_source_refs,
        selected_visual_refs=door_visual_refs,
        resolution=(
            stage4_doors.DoorAssemblyResolution.AUTHORIZED_CLOSED_DOUBLE_LEAF
        ),
        human_authorization_ref=human_authorization_ref,
    )
    door_operations = stage4_doors.apply_door_assembly_delta(
        inner_operations,
        door_delta,
        door_lineage,
    )
    roof_visual_refs = _visual_refs(visual_manifest_ref, SELECTED_ROOF_ROIS)
    full_operations, roof_lineage = stage4_roof.compile_roof_eaves_pediment_delta(
        door_operations,
        textual_evidence_refs=tuple(
            sorted(set((*column_source_refs, *entablature_source_refs)))
        ),
        selected_visual_refs=roof_visual_refs,
    )

    predecessor_by_id = {
        str(item["operation_id"]): item for item in predecessor_operations
    }
    successor_to_predecessor = _collapse_stage4_lineage_to_stage3(
        predecessor_operations,
        (
            architectural_operations,
            inner_operations,
            door_operations,
            full_operations,
        ),
        transitive_ancestor_maps=(
            {},
            inner_lineage["transitive_ancestor_map"],
            {},
            {},
        ),
    )

    if set(successor_to_predecessor) != {
        str(item["operation_id"]) for item in full_operations
    }:
        raise ParthenonStage4Error("full-building Stage 4 contains unbound successor operations")

    exact_unchanged: dict[str, str] = {}
    for operation in full_operations:
        operation_id = str(operation["operation_id"])
        predecessor = predecessor_by_id.get(operation_id)
        if (
            predecessor is not None
            and successor_to_predecessor[operation_id] == (operation_id,)
            and _operation_fingerprint(operation) == _operation_fingerprint(predecessor)
        ):
            exact_unchanged[operation_id] = _operation_fingerprint(predecessor)

    combined_lineage = {
        "schema": "ParthenonStage4FullBuildingIRDelta@1",
        "predecessor_operation_count": len(predecessor_operations),
        "current_operation_count": len(full_operations),
        "stage3_component_count": len(
            {str(item["component_id"]) for item in predecessor_operations}
        ),
        "architectural_delta": architectural_lineage,
        "inner_colonnade_delta": inner_lineage,
        "door_assembly_delta": door_lineage,
        "roof_eaves_pediment_delta": roof_lineage,
        "successor_to_predecessor_operation_ids": {
            key: list(value) for key, value in sorted(successor_to_predecessor.items())
        },
        "exact_unchanged_predecessor_fingerprints": dict(sorted(exact_unchanged.items())),
        "exact_unchanged_predecessor_count": len(exact_unchanged),
        "all_successors_bound_to_exact_stage3": True,
        "canonical_write_authority": False,
    }
    return full_operations, combined_lineage


def compile_stage4_component_coverage(
    predecessor_operations: Sequence[Mapping[str, object]],
    successor_operations: Sequence[Mapping[str, object]],
    *,
    lineage: Mapping[str, object],
    lineage_ref: str,
    evidence_refs: Sequence[str],
    relational_revalidation_refs: Sequence[str],
):
    """Compile the exact 271-operation Stage 3 coverage denominator.

    Existence of a file, component, or same-named operation is never treated
    as coverage.  An operation is ``VERIFIED_UNCHANGED`` only when its exact
    project fingerprint survives and a relational revalidation reference is
    supplied; every other realized predecessor is ``REFINED`` with explicit
    lineage and evidence.
    """

    if len(predecessor_operations) != 271:
        raise ParthenonStage4Error(
            "Stage 4 component coverage requires the exact 271-operation Stage 3 denominator"
        )
    predecessor_ids = [str(item["operation_id"]) for item in predecessor_operations]
    if len(set(predecessor_ids)) != 271:
        raise ParthenonStage4Error("Stage 3 coverage denominator operation ids are not unique")
    predecessor_components = {
        str(item["component_id"]) for item in predecessor_operations
    }
    if len(predecessor_components) != 11:
        raise ParthenonStage4Error("Stage 3 coverage denominator must contain 11 components")
    if not evidence_refs or not relational_revalidation_refs:
        raise ParthenonStage4Error(
            "Stage 4 coverage requires evidence and relational revalidation refs"
        )

    raw_mapping = lineage.get("successor_to_predecessor_operation_ids")
    if not isinstance(raw_mapping, Mapping):
        raise ParthenonStage4Error("full-building lineage lacks successor-to-Stage-3 mapping")
    successor_by_id = {
        str(item["operation_id"]): item for item in successor_operations
    }
    if len(successor_by_id) != len(successor_operations):
        raise ParthenonStage4Error("Stage 4 coverage successor ids are not unique")
    if set(str(key) for key in raw_mapping) != set(successor_by_id):
        raise ParthenonStage4Error(
            "Stage 4 coverage lineage does not bind the exact successor denominator"
        )
    predecessor_by_id = {
        str(item["operation_id"]): item for item in predecessor_operations
    }
    successors_for_predecessor: dict[str, list[Mapping[str, object]]] = {
        operation_id: [] for operation_id in predecessor_by_id
    }
    for successor_id, predecessor_ids_value in raw_mapping.items():
        successor_id = str(successor_id)
        if (
            not isinstance(predecessor_ids_value, Sequence)
            or isinstance(predecessor_ids_value, (str, bytes))
            or not predecessor_ids_value
        ):
            raise ParthenonStage4Error(
                f"Stage 4 coverage successor has no Stage 3 roots: {successor_id}"
            )
        predecessor_root_ids = tuple(
            sorted(set(str(item) for item in predecessor_ids_value))
        )
        unknown_roots = [
            item for item in predecessor_root_ids if item not in predecessor_by_id
        ]
        if unknown_roots:
            raise ParthenonStage4Error(
                "Stage 4 coverage lineage names unknown Stage 3 operations: "
                + ", ".join(unknown_roots)
            )
        for predecessor_id in predecessor_root_ids:
            successors_for_predecessor[predecessor_id].append(
                successor_by_id[successor_id]
            )
    missing = sorted(
        operation_id
        for operation_id, successors in successors_for_predecessor.items()
        if not successors
    )
    if missing:
        raise ParthenonStage4Error(
            "Stage 4 coverage omitted Stage 3 operations: " + ", ".join(missing[:8])
        )

    def operation_ref(operation: Mapping[str, object]) -> StageOperationRef:
        return StageOperationRef(
            component_id=str(operation["component_id"]),
            operation_id=str(operation["operation_id"]),
        )

    predecessor_stage_operations = tuple(
        StageOperation(operation_ref(item), _operation_fingerprint(item))
        for item in predecessor_operations
    )
    successor_stage_operations = tuple(
        StageOperation(operation_ref(item), _operation_fingerprint(item))
        for item in successor_operations
    )
    lineage_resolutions: list[OperationLineageResolution] = []
    dispositions: list[PredecessorOperationDisposition] = []
    for predecessor_id, predecessor in predecessor_by_id.items():
        predecessor_ref = operation_ref(predecessor)
        successors = tuple(
            sorted(
                successors_for_predecessor[predecessor_id],
                key=lambda item: (str(item["component_id"]), str(item["operation_id"])),
            )
        )
        lineage_resolutions.append(
            OperationLineageResolution(
                predecessor_ref=predecessor_ref,
                successor_refs=tuple(operation_ref(item) for item in successors),
                lineage_refs=(lineage_ref,),
            )
        )
        unchanged = (
            len(successors) == 1
            and operation_ref(successors[0]) == predecessor_ref
            and _operation_fingerprint(successors[0])
            == _operation_fingerprint(predecessor)
        )
        dispositions.append(
            PredecessorOperationDisposition(
                predecessor_ref=predecessor_ref,
                disposition=(
                    OperationDisposition.VERIFIED_UNCHANGED
                    if unchanged
                    else OperationDisposition.REFINED
                ),
                evidence_refs=tuple(evidence_refs) if not unchanged else (),
                relational_revalidation_refs=tuple(relational_revalidation_refs),
            )
        )

    receipt = compile_stage_component_coverage(
        predecessor_stage_id="stage-3",
        successor_stage_id="stage-4",
        predecessor_operations=predecessor_stage_operations,
        successor_operations=successor_stage_operations,
        lineage_resolutions=lineage_resolutions,
        dispositions=dispositions,
    )
    if (
        receipt.status is not StageComponentCoverageStatus.PASS
        or receipt.operation_count != 271
        or receipt.component_count != 11
    ):
        raise ParthenonStage4Error("Stage 4 component coverage did not pass exact denominator")
    return receipt


def _box_brep(origin: Iterable[float], size: Iterable[float]):
    return stage3._box_brep(origin, size)


def _revolved_profile_mesh(
    center: Sequence[float],
    rings: Sequence[tuple[float, float]],
    *,
    segments: int = 64,
) -> rhino3dm.Mesh:
    x, y, z0 = (float(item) for item in center)
    mesh = rhino3dm.Mesh()
    for z_offset, radius in rings:
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            mesh.Vertices.Add(
                x + radius * math.cos(angle),
                y + radius * math.sin(angle),
                z0 + z_offset,
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
    mesh.Vertices.Add(x, y, z0 + rings[0][0])
    top_center = len(mesh.Vertices)
    mesh.Vertices.Add(x, y, z0 + rings[-1][0])
    top_start = (len(rings) - 1) * segments
    for index in range(segments):
        nxt = (index + 1) % segments
        mesh.Faces.AddFace(bottom_center, nxt, index)
        mesh.Faces.AddFace(top_center, top_start + index, top_start + nxt)
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def _doric_shaft_mesh(parameters: Mapping[str, object]) -> rhino3dm.Mesh:
    x, y, z0 = (float(item) for item in parameters["center"])
    height = float(parameters["shaft_height"])
    lower_radius = float(parameters["lower_diameter"]) / 2.0
    upper_radius = float(parameters["upper_diameter"]) / 2.0
    entasis_max = float(parameters["entasis_max"])
    entasis_location = float(parameters["entasis_location_ratio"])
    flutes = int(parameters["flutes"])
    segments = int(parameters["radial_segments"])
    if flutes != FLUTE_COUNT or segments % flutes:
        raise ParthenonStage4Error("Doric shaft requires 20 geometrically sampled flutes")
    samples_per_flute = segments // flutes
    mesh = rhino3dm.Mesh()
    ring_ratios = (0.0, 0.10, 0.20, 0.30, 0.40, 0.55, 0.70, 0.85, 1.0)
    flute_depth = 0.030
    for t in ring_ratios:
        linear_radius = lower_radius + (upper_radius - lower_radius) * t
        if t <= entasis_location:
            entasis = entasis_max * math.sin(math.pi * t / (2.0 * entasis_location))
        else:
            entasis = entasis_max * math.cos(
                math.pi * (t - entasis_location) / (2.0 * (1.0 - entasis_location))
            )
        outer_radius = linear_radius + entasis
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            phase = (index % samples_per_flute) / samples_per_flute
            groove = flute_depth * math.sin(math.pi * phase) ** 2
            radius = outer_radius - groove
            mesh.Vertices.Add(
                x + radius * math.cos(angle),
                y + radius * math.sin(angle),
                z0 + height * t,
            )
    ring_count = len(ring_ratios)
    for ring in range(ring_count - 1):
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
    top_start = (ring_count - 1) * segments
    for index in range(segments):
        nxt = (index + 1) % segments
        mesh.Faces.AddFace(bottom_center, nxt, index)
        mesh.Faces.AddFace(top_center, top_start + index, top_start + nxt)
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def _append_box_mesh(mesh: rhino3dm.Mesh, origin: Sequence[float], size: Sequence[float]) -> None:
    x, y, z = (float(item) for item in origin)
    sx, sy, sz = (float(item) for item in size)
    start = len(mesh.Vertices)
    for point in (
        (x, y, z),
        (x + sx, y, z),
        (x + sx, y + sy, z),
        (x, y + sy, z),
        (x, y, z + sz),
        (x + sx, y, z + sz),
        (x + sx, y + sy, z + sz),
        (x, y + sy, z + sz),
    ):
        mesh.Vertices.Add(*point)
    for face in (
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ):
        mesh.Faces.AddFace(*(start + item for item in face))


def _triglyph_mesh(parameters: Mapping[str, object]) -> rhino3dm.Mesh:
    origin = [float(item) for item in parameters["origin"]]
    size = [float(item) for item in parameters["size"]]
    side = str(parameters["side"])
    along_axis = 0 if side in {"east", "west"} else 1
    total = size[along_axis]
    bar = total / 5.0
    mesh = rhino3dm.Mesh()
    for bar_index in (0, 2, 4):
        bar_origin = list(origin)
        bar_size = list(size)
        bar_origin[along_axis] += bar_index * bar
        bar_size[along_axis] = bar
        _append_box_mesh(mesh, bar_origin, bar_size)
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def _sloped_panel_mesh(
    envelope: Mapping[str, object],
    *,
    side: str,
    lower_offset: float,
    ridge_offset: float,
    thickness: float,
) -> rhino3dm.Mesh:
    """Create one closed half-roof field inside the inherited envelope."""

    width = float(envelope["width"])
    length = float(envelope["length"])
    eave_z = float(envelope["eave_z"])
    ridge_z = float(envelope["ridge_z"])
    y_center = float(envelope["y_center"])
    if side == "north":
        x_eave, x_ridge = width / 2.0, 0.0
    elif side == "south":
        x_eave, x_ridge = -width / 2.0, 0.0
    else:
        raise ParthenonStage4Error(f"unsupported roof slope side: {side}")
    y0 = y_center - length / 2.0
    y1 = y_center + length / 2.0
    z_eave = eave_z + lower_offset
    z_ridge = ridge_z - ridge_offset
    if thickness <= 0.0 or z_ridge <= z_eave or z_ridge + thickness > ridge_z + 1e-9:
        raise ParthenonStage4Error("roof slope proxy escaped its inherited envelope")

    mesh = rhino3dm.Mesh()
    for point in (
        (x_eave, y0, z_eave),
        (x_eave, y1, z_eave),
        (x_ridge, y1, z_ridge),
        (x_ridge, y0, z_ridge),
        (x_eave, y0, z_eave + thickness),
        (x_eave, y1, z_eave + thickness),
        (x_ridge, y1, z_ridge + thickness),
        (x_ridge, y0, z_ridge + thickness),
    ):
        mesh.Vertices.Add(*point)
    for face in (
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ):
        mesh.Faces.AddFace(*face)
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def _raking_member_mesh(
    envelope: Mapping[str, object],
    *,
    half: str,
    base_offset: float,
    ridge_offset: float,
    height: float,
) -> rhino3dm.Mesh:
    """Create one blank architectural raking member, never a figure body."""

    width = float(envelope["width"])
    depth = float(envelope["depth"])
    eave_z = float(envelope["eave_z"])
    ridge_z = float(envelope["ridge_z"])
    y_center = float(envelope["y_center"])
    if half == "left":
        x0, x1 = -width / 2.0, 0.0
    elif half == "right":
        x0, x1 = 0.0, width / 2.0
    else:
        raise ParthenonStage4Error(f"unsupported pediment half: {half}")
    z0 = eave_z + base_offset if half == "left" else ridge_z - ridge_offset
    z1 = ridge_z - ridge_offset if half == "left" else eave_z + base_offset
    if height <= 0.0 or max(z0, z1) + height > ridge_z + 1e-9:
        raise ParthenonStage4Error("raking member escaped its inherited pediment envelope")
    y0 = y_center - depth / 2.0
    y1 = y_center + depth / 2.0
    mesh = rhino3dm.Mesh()
    for point in (
        (x0, y0, z0),
        (x0, y1, z0),
        (x1, y1, z1),
        (x1, y0, z1),
        (x0, y0, z0 + height),
        (x0, y1, z0 + height),
        (x1, y1, z1 + height),
        (x1, y0, z1 + height),
    ):
        mesh.Vertices.Add(*point)
    for face in (
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ):
        mesh.Faces.AddFace(*face)
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def _roof_detail_geometry(
    operation: Mapping[str, object],
) -> tuple[object, object]:
    """Realize every typed roof delta as one deterministic Rhino object."""

    kind = str(operation["kind"])
    operation_id = str(operation["operation_id"])
    parameters = operation["parameters"]
    assert isinstance(parameters, Mapping)
    roof_envelope = parameters.get("inherited_roof_envelope")
    pediment_envelope = parameters.get("inherited_pediment_envelope")
    host_envelope = parameters.get("inherited_host_envelope")

    if kind in {"eave_geison", "eave_sima"}:
        if not isinstance(host_envelope, Mapping):
            raise ParthenonStage4Error(f"{operation_id} lacks its inherited host envelope")
        origin = [float(item) for item in host_envelope["origin"]]
        size = [float(item) for item in host_envelope["size"]]
        geison_height = size[2] * 0.62
        if kind == "eave_geison":
            size[2] = geison_height
        else:
            origin[2] += geison_height
            size[2] -= geison_height
        return _box_brep(origin, size), "brep"

    if kind == "pediment_horizontal_geison":
        if not isinstance(host_envelope, Mapping):
            raise ParthenonStage4Error(f"{operation_id} lacks its inherited host envelope")
        return _box_brep(host_envelope["origin"], host_envelope["size"]), "brep"

    if kind == "pediment_tympanum":
        if not isinstance(pediment_envelope, Mapping):
            raise ParthenonStage4Error(f"{operation_id} lacks its inherited pediment envelope")
        return (
            stage3._roof_mesh(
                float(pediment_envelope["width"]),
                float(pediment_envelope["depth"]),
                float(pediment_envelope["eave_z"]),
                float(pediment_envelope["ridge_z"]),
                float(pediment_envelope["y_center"]),
            ),
            "mesh",
        )

    if kind in {"pediment_raking_geison", "pediment_raking_sima"}:
        if not isinstance(pediment_envelope, Mapping):
            raise ParthenonStage4Error(f"{operation_id} lacks its inherited pediment envelope")
        half = "left" if operation_id.endswith("-left") else "right"
        if kind == "pediment_raking_geison":
            offsets = (0.03, 0.30, 0.16)
        else:
            offsets = (0.20, 0.12, 0.10)
        return (
            _raking_member_mesh(
                pediment_envelope,
                half=half,
                base_offset=offsets[0],
                ridge_offset=offsets[1],
                height=offsets[2],
            ),
            "mesh",
        )

    if kind == "acroterion_seat":
        if not isinstance(pediment_envelope, Mapping):
            raise ParthenonStage4Error(f"{operation_id} lacks its inherited pediment envelope")
        width = float(pediment_envelope["width"])
        depth = float(pediment_envelope["depth"])
        eave_z = float(pediment_envelope["eave_z"])
        ridge_z = float(pediment_envelope["ridge_z"])
        y_center = float(pediment_envelope["y_center"])
        position = str(parameters["position"])
        seat_width = min(0.60, width * 0.03)
        if position == "apex":
            x = -seat_width / 2.0
            z = ridge_z - 0.12
        elif position == "left":
            x = -width / 2.0
            z = eave_z + 0.08
        elif position == "right":
            x = width / 2.0 - seat_width
            z = eave_z + 0.08
        else:
            raise ParthenonStage4Error(f"unsupported acroterion seat position: {position}")
        return (
            _box_brep(
                (x, y_center - depth / 2.0, z),
                (seat_width, depth, 0.12),
            ),
            "brep",
        )

    if not isinstance(roof_envelope, Mapping):
        raise ParthenonStage4Error(f"{operation_id} lacks its inherited roof envelope")
    topology = str(parameters.get("topology", ""))
    side = "north" if "north" in topology else "south" if "south" in topology else None
    width = float(roof_envelope["width"])
    length = float(roof_envelope["length"])
    eave_z = float(roof_envelope["eave_z"])
    ridge_z = float(roof_envelope["ridge_z"])
    y_center = float(roof_envelope["y_center"])
    y0 = y_center - length / 2.0

    if kind == "timber_rafter_field" and side is not None:
        return (
            _sloped_panel_mesh(
                roof_envelope,
                side=side,
                lower_offset=0.05,
                ridge_offset=0.42,
                thickness=0.10,
            ),
            "mesh",
        )
    if kind == "marble_pan_tile_field" and side is not None:
        return (
            _sloped_panel_mesh(
                roof_envelope,
                side=side,
                lower_offset=0.18,
                ridge_offset=0.28,
                thickness=0.08,
            ),
            "mesh",
        )
    if kind == "marble_cover_tile_field" and side is not None:
        return (
            _sloped_panel_mesh(
                roof_envelope,
                side=side,
                lower_offset=0.29,
                ridge_offset=0.15,
                thickness=0.06,
            ),
            "mesh",
        )
    if kind in {"timber_bearing_beam", "marble_eave_terminal"} and side is not None:
        x_center = width / 2.0 - 0.16 if side == "north" else -width / 2.0 + 0.16
        cross = 0.24 if kind == "timber_bearing_beam" else 0.18
        z = eave_z + (0.02 if kind == "timber_bearing_beam" else 0.31)
        return (
            _box_brep(
                (x_center - cross / 2.0, y0, z),
                (cross, length, cross),
            ),
            "brep",
        )
    if kind in {"timber_ridge_beam", "marble_ridge_terminal"}:
        cross = 0.24 if kind == "timber_ridge_beam" else 0.18
        z = ridge_z - (0.42 if kind == "timber_ridge_beam" else 0.18)
        return _box_brep((-cross / 2.0, y0, z), (cross, length, cross)), "brep"
    raise ParthenonStage4Error(f"unsupported Stage 4 roof detail kind: {kind}")


def create_stage4_model(
    path: Path,
    *,
    operations: Sequence[Mapping[str, object]],
    program_digest: str,
) -> int:
    """Write one deterministic metre/Z-up 3DM object for every Stage 4 op."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
    model.Settings.ModelAbsoluteTolerance = 0.001
    for key, value in {
        "archflow:project_id": PROJECT_ID,
        "archflow:branch_id": BRANCH_ID,
        "archflow:stage": "4",
        "archflow:coordinate_system": "RhinoWorldXY_ZUp",
        "archflow:up_axis": "Z",
        "archflow:program_digest": program_digest,
        "archflow:predecessor_program_digest": PREDECESSOR_PROGRAM_DIGEST,
        "archflow:predecessor_model_sha256": PREDECESSOR_MODEL_SHA256,
        "archflow:disposition": "HOLD",
    }.items():
        model.Strings[key] = value

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
        layer.Name = f"P087::{component_id}"
        layer.Color = component_colors.get(component_id, (220, 215, 200, 255))
        layers[component_id] = model.Layers.Add(layer)
    materials: dict[str, int] = {}
    for material_id in sorted({str(item["material_id"]) for item in operations}):
        material = rhino3dm.Material()
        material.Name = material_id
        if material_id in {
            "bronze-timber-candidate",
            "door-timber-candidate",
            "roof-timber",
        }:
            material.DiffuseColor = (92, 62, 39, 255)
        elif material_id == "pentelic-marble-roof-tile":
            material.DiffuseColor = (215, 209, 193, 255)
        else:
            material.DiffuseColor = (226, 219, 198, 255)
        materials[material_id] = model.Materials.Add(material)

    for operation in operations:
        parameters = operation["parameters"]
        assert isinstance(parameters, Mapping)
        kind = str(operation["kind"])
        box_brep = False
        if kind in {"box", "bearing_block", "doric_abacus", "entablature_layer"}:
            geometry = _box_brep(parameters["origin"], parameters["size"])
            add = model.Objects.AddBrep
            box_brep = True
        elif kind == "doric_shaft":
            geometry = _doric_shaft_mesh(parameters)
            add = model.Objects.AddMesh
        elif kind == "doric_neck":
            geometry = _revolved_profile_mesh(
                parameters["center"],
                (
                    (0.0, float(parameters["lower_diameter"]) / 2.0),
                    (float(parameters["height"]), float(parameters["upper_diameter"]) / 2.0),
                ),
            )
            add = model.Objects.AddMesh
        elif kind == "doric_echinus":
            height = float(parameters["height"])
            lower = float(parameters["lower_diameter"]) / 2.0
            upper = float(parameters["upper_diameter"]) / 2.0
            rings = tuple(
                (
                    height * index / 6.0,
                    lower + (upper - lower) * (1.0 - (1.0 - index / 6.0) ** 1.65),
                )
                for index in range(7)
            )
            geometry = _revolved_profile_mesh(parameters["center"], rings)
            add = model.Objects.AddMesh
        elif kind == "triglyph":
            geometry = _triglyph_mesh(parameters)
            add = model.Objects.AddMesh
        elif kind in {"tapered_column", "fluted_column", "ionic_column"}:
            geometry = stage3._column_mesh(
                parameters["center"],
                float(parameters["height"]),
                float(parameters["diameter"]),
                fluted=kind in {"fluted_column", "ionic_column"},
            )
            add = model.Objects.AddMesh
        elif kind == "roof_prism":
            geometry = stage3._roof_mesh(
                float(parameters["width"]),
                float(parameters["length"]),
                float(parameters["eave_z"]),
                float(parameters["ridge_z"]),
                float(parameters["y_center"]),
            )
            add = model.Objects.AddMesh
        elif kind == "gable_panel":
            geometry = stage3._roof_mesh(
                float(parameters["width"]),
                float(parameters["depth"]),
                float(parameters["eave_z"]),
                float(parameters["ridge_z"]),
                float(parameters["y_center"]),
            )
            add = model.Objects.AddMesh
        elif kind in (
            stage4_roof.TIMBER_KINDS
            | stage4_roof.ROOF_TILE_KINDS
            | stage4_roof.STRUCTURAL_MARBLE_KINDS
        ):
            geometry, geometry_type = _roof_detail_geometry(operation)
            add = (
                model.Objects.AddMesh
                if geometry_type == "mesh"
                else model.Objects.AddBrep
            )
            box_brep = geometry_type == "brep"
        else:
            raise ParthenonStage4Error(f"unsupported Stage 4 geometry kind: {kind}")

        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = str(operation["operation_id"])
        attributes.LayerIndex = layers[str(operation["component_id"])]
        attributes.MaterialIndex = materials[str(operation["material_id"])]
        attributes.MaterialSource = rhino3dm.ObjectMaterialSource.MaterialFromObject
        user_strings = {
            "archflow:project_id": PROJECT_ID,
            "archflow:branch_id": BRANCH_ID,
            "archflow:stage": "4",
            "archflow:component_id": str(operation["component_id"]),
            "archflow:operation_id": str(operation["operation_id"]),
            "archflow:operation_kind": kind,
            "archflow:program_digest": program_digest,
            "archflow:coordinate_system": "RhinoWorldXY_ZUp",
            "archflow:up_axis": "Z",
            "archflow:material_id": str(operation["material_id"]),
            "archflow:decision_refs": _canonical_json(operation["decision_refs"]),
            "archflow:source_refs": _canonical_json(operation["source_refs"]),
        }
        for lineage_key in ("replaces_operation_id", "refines_operation_id"):
            if lineage_key in operation:
                user_strings[f"archflow:{lineage_key}"] = str(operation[lineage_key])
        if kind == "doric_shaft":
            user_strings["archflow:actual_flute_count"] = str(FLUTE_COUNT)
            user_strings["archflow:shaft_height_m"] = str(parameters["shaft_height"])
            declared_total = parameters.get(
                "total_column_height", parameters.get("tier_total_height")
            )
            if declared_total is not None:
                user_strings["archflow:total_column_height_m"] = str(declared_total)
        for key, value in user_strings.items():
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
        raise ParthenonStage4Error(f"rhino3dm failed to write {path}")
    return len(operations)


def validate_stage4_material_bindings(
    path: Path,
    *,
    operations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Resolve each object's effective Rhino material and bind it to Stage 4 IR.

    ``ObjectAttributes.MaterialIndex`` is ignored by Rhino while the material
    source remains ``MaterialFromLayer``. This gate therefore resolves either
    an explicit object material or a valid layer render-material fallback and
    never treats layer/display colour as a render material.
    """

    model = rhino3dm.File3dm.Read(str(path))
    if model is None:
        return {
            "passed": False,
            "checks": {},
            "failures": ["rhino3dm could not read the Stage 4 model for material validation"],
        }
    objects = primary_three_dm_objects(model)

    expected_by_id = {
        str(operation["operation_id"]): str(operation["material_id"])
        for operation in operations
    }
    material_names = tuple(str(material.Name) for material in model.Materials)
    material_indices_by_name: dict[str, list[int]] = {}
    for index, name in enumerate(material_names):
        material_indices_by_name.setdefault(name, []).append(index)

    reason_counts: dict[str, int] = {}
    reason_examples: dict[str, list[str]] = {}

    def fail(reason: str, operation_id: str) -> None:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        examples = reason_examples.setdefault(reason, [])
        if len(examples) < 5:
            examples.append(operation_id)

    seen_ids: set[str] = set()
    source_counts: dict[str, int] = {}
    resolved_binding_count = 0
    semantic_identity_count = 0
    display_color_only_count = 0
    for model_object in objects:
        attributes = model_object.Attributes
        operation_id = str(attributes.Name)
        seen_ids.add(operation_id)
        expected_material_id = expected_by_id.get(operation_id)
        if expected_material_id is None:
            fail("model object has no matching Stage 4 operation", operation_id)
            continue

        user_strings = dict(attributes.GetUserStrings() or ())
        semantic_material_id = user_strings.get("archflow:material_id")
        if semantic_material_id != expected_material_id:
            fail("archflow material_id does not match the Stage 4 operation", operation_id)
        else:
            semantic_identity_count += 1

        material_source = attributes.MaterialSource
        source_name = str(material_source).rsplit(".", 1)[-1]
        source_counts[source_name] = source_counts.get(source_name, 0) + 1
        effective_index: int | None
        if material_source == rhino3dm.ObjectMaterialSource.MaterialFromObject:
            effective_index = int(attributes.MaterialIndex)
        elif material_source == rhino3dm.ObjectMaterialSource.MaterialFromLayer:
            layer_index = int(attributes.LayerIndex)
            if 0 <= layer_index < len(model.Layers):
                effective_index = int(model.Layers[layer_index].RenderMaterialIndex)
            else:
                effective_index = None
                fail("object has an invalid layer material fallback", operation_id)
        else:
            effective_index = None
            fail("top-level object uses an unresolved parent material source", operation_id)

        if effective_index is None or not 0 <= effective_index < len(model.Materials):
            if attributes.ColorSource in {
                rhino3dm.ObjectColorSource.ColorFromLayer,
                rhino3dm.ObjectColorSource.ColorFromObject,
            }:
                display_color_only_count += 1
            fail("object has no effective Rhino render material", operation_id)
            continue
        effective_material_id = material_names[effective_index]
        if effective_material_id != expected_material_id:
            fail("effective Rhino material does not match archflow material_id", operation_id)
            continue
        resolved_binding_count += 1

    missing_operation_ids = sorted(set(expected_by_id).difference(seen_ids))
    for operation_id in missing_operation_ids:
        fail("Stage 4 operation has no model object for material binding", operation_id)

    expected_material_ids = set(expected_by_id.values())
    table_material_ids = set(material_names)
    duplicate_material_ids = sorted(
        name for name, indices in material_indices_by_name.items() if len(indices) != 1
    )
    if table_material_ids != expected_material_ids:
        fail("material table identity differs from the Stage 4 program", "<material-table>")
    for material_id in duplicate_material_ids:
        fail("material table contains duplicate material identities", material_id)

    texture_file_references: list[str] = []
    for material in model.Materials:
        for getter_name in (
            "GetBitmapTexture",
            "GetBumpTexture",
            "GetEnvironmentTexture",
            "GetTransparencyTexture",
        ):
            texture = getattr(material, getter_name)()
            if texture is not None and str(texture.FileName):
                texture_file_references.append(str(texture.FileName))
    if texture_file_references:
        fail(
            "texture references lack a Stage 4 licensed-asset binding",
            "<material-table>",
        )

    expected_count = len(expected_by_id)
    failures = [
        f"{reason}: {count} (examples: {', '.join(reason_examples[reason])})"
        for reason, count in sorted(reason_counts.items())
    ]
    return {
        "passed": not failures,
        "checks": {
            "object_count": len(objects),
            "expected_operation_count": expected_count,
            "material_table_count": len(model.Materials),
            "material_table_ids": list(material_names),
            "expected_material_ids": sorted(expected_material_ids),
            "material_source_counts": source_counts,
            "resolved_binding_count": resolved_binding_count,
            "resolved_binding_coverage": (
                resolved_binding_count / expected_count if expected_count else 0.0
            ),
            "semantic_identity_count": semantic_identity_count,
            "semantic_identity_coverage": (
                semantic_identity_count / expected_count if expected_count else 0.0
            ),
            "display_color_only_count": display_color_only_count,
            "display_color_not_accepted_as_material": True,
            "texture_file_references": sorted(set(texture_file_references)),
            "duplicate_material_ids": duplicate_material_ids,
        },
        "failures": failures,
    }


def _operation_aabb(operation: Mapping[str, object]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    parameters = operation["parameters"]
    assert isinstance(parameters, Mapping)
    kind = str(operation["kind"])
    if "origin" in parameters and "size" in parameters:
        origin = tuple(float(item) for item in parameters["origin"])
        size = tuple(float(item) for item in parameters["size"])
        return origin, tuple(origin[index] + size[index] for index in range(3))
    if kind == "doric_shaft":
        center = tuple(float(item) for item in parameters["center"])
        radius = float(parameters["lower_diameter"]) / 2.0 + float(parameters["entasis_max"])
        return (
            (center[0] - radius, center[1] - radius, center[2]),
            (center[0] + radius, center[1] + radius, center[2] + float(parameters["shaft_height"])),
        )
    if kind in {"doric_neck", "doric_echinus"}:
        center = tuple(float(item) for item in parameters["center"])
        radius = max(float(parameters["lower_diameter"]), float(parameters["upper_diameter"])) / 2.0
        return (
            (center[0] - radius, center[1] - radius, center[2]),
            (center[0] + radius, center[1] + radius, center[2] + float(parameters["height"])),
        )
    raise ValueError(f"operation {operation['operation_id']} has no simple Stage 4 AABB")


def _intersection_volume(
    first: tuple[tuple[float, float, float], tuple[float, float, float]],
    second: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> float:
    lengths = [
        max(0.0, min(first[1][axis], second[1][axis]) - max(first[0][axis], second[0][axis]))
        for axis in range(3)
    ]
    return lengths[0] * lengths[1] * lengths[2]


def _window_clear_regions(
    operations: Sequence[Mapping[str, object]] | None = None,
) -> tuple[dict[str, object], ...]:
    if operations is not None:
        groups: dict[str, list[Mapping[str, object]]] = {"left": [], "right": []}
        for operation in operations:
            parameters = operation.get("parameters")
            if not isinstance(parameters, Mapping) or not isinstance(
                parameters.get("window_clear"), Mapping
            ):
                continue
            side = str(parameters.get("window_side", ""))
            if side in groups:
                groups[side].append(operation)
        if all(groups.values()):
            derived: list[dict[str, object]] = []
            for side, pieces in groups.items():
                clear_contracts = {
                    _canonical_json(item["parameters"]["window_clear"]): item[
                        "parameters"
                    ]["window_clear"]
                    for item in pieces
                }
                if len(clear_contracts) != 1:
                    raise ParthenonStage4Error(
                        f"{side} east-window pieces disagree on their clear contract"
                    )
                clear = next(iter(clear_contracts.values()))
                center_x = float(clear["center_x"])
                width = float(clear["width"])
                sill_z = float(clear["sill_z"])
                height = float(clear["height"])
                wall_bounds = [_operation_aabb(item) for item in pieces]
                y_min = min(item[0][1] for item in wall_bounds)
                y_max = max(item[1][1] for item in wall_bounds)
                derived.append(
                    {
                        "window_id": f"east-{side}-window",
                        "min": [center_x - width / 2.0, y_min, sill_z],
                        "max": [center_x + width / 2.0, y_max, sill_z + height],
                        "width": width,
                        "height": height,
                        "center_basis": str(
                            clear.get("center_basis", "declared-window-clear-contract")
                        ),
                        "metric_authority": bool(clear.get("metric_authority", False)),
                    }
                )
            return tuple(sorted(derived, key=lambda item: float(item["min"][0])))
    return tuple(
        {
            "window_id": "east-left-window" if center_x < 0 else "east-right-window",
            "min": [center_x - WINDOW_WIDTH / 2.0, 22.259999999999998, WINDOW_SILL_Z],
            "max": [center_x + WINDOW_WIDTH / 2.0, 23.61, WINDOW_SILL_Z + WINDOW_HEIGHT],
            "width": WINDOW_WIDTH,
            "height": WINDOW_HEIGHT,
        }
        for center_x in WINDOW_CENTERS_X
    )


def validate_window_voids(operations: Sequence[Mapping[str, object]]) -> dict[str, object]:
    walls = tuple(
        item
        for item in operations
        if item["component_id"] == "cella" and str(item["operation_id"]).startswith("cella-wall-")
    )
    failures: list[str] = []
    regions = _window_clear_regions(operations)
    for region in regions:
        clear = (
            tuple(float(item) for item in region["min"]),
            tuple(float(item) for item in region["max"]),
        )
        for wall in walls:
            if _intersection_volume(clear, _operation_aabb(wall)) > 1e-9:
                failures.append(f"{region['window_id']} intersects {wall['operation_id']}")
    if any(str(item["operation_id"]) in {"cella-wall-east-left", "cella-wall-east-right"} for item in operations):
        failures.append("schematic east wall segment survived the Stage 4 void replacement")
    left, right = regions
    if not (
        math.isclose(float(left["min"][0]), -float(right["max"][0]), abs_tol=1e-9)
        and math.isclose(float(left["max"][0]), -float(right["min"][0]), abs_tol=1e-9)
    ):
        failures.append("east window voids are not mirrored")
    return {
        "schema": "ParthenonStage4WindowVoidValidation@1",
        "passed": not failures,
        "window_count": len(regions),
        "regions": list(regions),
        "failures": failures,
        "measurement_classification": "SOFT",
    }


def validate_visual_manifest(manifest: Mapping[str, object]) -> tuple[str, ...]:
    if manifest.get("schema") != "ParthenonVisualRegionSelection@1":
        raise ValueError("visual manifest schema drifted")
    if manifest.get("branch_id") != BRANCH_ID:
        raise ValueError("visual manifest branch drifted")
    selected = tuple(str(item) for item in manifest.get("selected_candidate_ids", ()))
    parked = tuple(str(item) for item in manifest.get("parked_candidate_ids", ()))
    rejected = tuple(str(item) for item in manifest.get("rejected_candidate_ids", ()))
    if len(set(selected)) != len(selected):
        raise ValueError("selected visual candidates contain duplicates")
    if set(selected) & (set(parked) | set(rejected)) or set(parked) & set(rejected):
        raise ValueError("visual candidate partitions overlap")
    nested = manifest.get("manifest")
    if isinstance(nested, Mapping):
        if nested.get("measurement_authority") is not False:
            raise ValueError("visual manifest acquired exact-dimension authority")
        if nested.get("geometry_mutation_authority") is not False:
            raise ValueError("visual manifest acquired geometry authority")
        bindings = nested.get("candidate_bindings", ())
        by_id = {
            str(item["candidate"]["candidate_id"]): item["candidate"]
            for item in bindings
            if isinstance(item, Mapping) and isinstance(item.get("candidate"), Mapping)
        }
        for candidate_id in selected:
            candidate = by_id.get(candidate_id)
            if candidate is None:
                raise ValueError(f"selected ROI has no binding: {candidate_id}")
            if "exact_dimension" not in candidate.get("cannot_support", ()):
                raise ValueError(f"selected ROI can improperly authorize exact_dimension: {candidate_id}")
    return selected


def _mesh_flute_count(mesh: rhino3dm.Mesh, center: Sequence[float]) -> int:
    bbox = mesh.GetBoundingBox()
    z0 = float(bbox.Min.Z)
    x, y = float(center[0]), float(center[1])
    ring = []
    for vertex in mesh.Vertices:
        if abs(float(vertex.Z) - z0) <= 1e-5:
            radius = math.hypot(float(vertex.X) - x, float(vertex.Y) - y)
            if radius > 0.3:
                ring.append((math.atan2(float(vertex.Y) - y, float(vertex.X) - x), radius))
    ring.sort()
    if len(ring) < 40:
        return 0
    radii = [item[1] for item in ring]
    return sum(
        radii[index] < radii[index - 1] - 1e-6
        and radii[index] < radii[(index + 1) % len(radii)] - 1e-6
        for index in range(len(radii))
    )


def _actual_window_clear(
    model: rhino3dm.File3dm,
    operations: Sequence[Mapping[str, object]],
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    wall_objects = []
    for item in model.Objects:
        name = item.Attributes.Name or ""
        if name.startswith("cella-wall-"):
            wall_objects.append((name, item.Geometry.GetBoundingBox()))
    for region in _window_clear_regions(operations):
        clear = (
            tuple(float(item) for item in region["min"]),
            tuple(float(item) for item in region["max"]),
        )
        for name, bbox in wall_objects:
            actual = (
                (float(bbox.Min.X), float(bbox.Min.Y), float(bbox.Min.Z)),
                (float(bbox.Max.X), float(bbox.Max.Y), float(bbox.Max.Z)),
            )
            if _intersection_volume(clear, actual) > 1e-9:
                failures.append(f"{region['window_id']} intersects actual wall {name}")
    return not failures, failures


def _validate_entablature_rhythm(operations: Sequence[Mapping[str, object]]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    expected = {"east": (14, 15), "north": (32, 33), "south": (32, 33), "west": (14, 15)}
    for side, (metope_count, triglyph_count) in expected.items():
        metopes = sorted(
            (item for item in operations if str(item["operation_id"]).startswith(f"metope-{side}-")),
            key=lambda item: str(item["operation_id"]),
        )
        triglyphs = sorted(
            (item for item in operations if str(item["operation_id"]).startswith(f"triglyph-{side}-")),
            key=lambda item: str(item["operation_id"]),
        )
        if (len(metopes), len(triglyphs)) != (metope_count, triglyph_count):
            failures.append(f"{side} rhythm count drifted")
            continue
        positions = [float(item["parameters"]["rhythm_position"]) for item in triglyphs]
        if any(right <= left for left, right in zip(positions, positions[1:])):
            failures.append(f"{side} triglyph positions are not strictly alternating")
    return not failures, failures


def validate_stage4_spatial_model(
    path: Path,
    *,
    operations: Sequence[Mapping[str, object]],
    program_digest: str,
    predecessor_envelope: Mapping[str, Sequence[float]] | None = None,
) -> dict[str, object]:
    """Run the P087 host-local, opening, axis, contact, and leakage gates."""

    predecessor_envelope = predecessor_envelope or PREDECESSOR_BBOX
    model = rhino3dm.File3dm.Read(str(path))
    if model is None:
        raise ParthenonStage4Error(f"cannot read Stage 4 model: {path}")
    objects = primary_three_dm_objects(model)
    by_operation = {str(item["operation_id"]): item for item in operations}
    names = tuple(item.Attributes.Name or "" for item in objects)
    failures: list[str] = []

    if model.Settings.ModelUnitSystem != rhino3dm.UnitSystem.Meters:
        failures.append("model units are not Meters")
    if model.Strings["archflow:up_axis"] != "Z" or model.Strings["archflow:coordinate_system"] != "RhinoWorldXY_ZUp":
        failures.append("model is not RhinoWorldXY/Z-up")
    if len(names) != len(by_operation) or set(names) != set(by_operation) or len(set(names)) != len(names):
        failures.append("3DM object/operation mapping is not one-to-one")
    for item in objects:
        user_strings = dict(item.Attributes.GetUserStrings() or ())
        if user_strings.get("archflow:program_digest") != program_digest:
            failures.append(f"object {item.Attributes.Name} has wrong program digest")
        if user_strings.get("archflow:branch_id") != BRANCH_ID:
            failures.append(f"object {item.Attributes.Name} leaked another branch")

    bboxes = [item.Geometry.GetBoundingBox() for item in objects]
    bbox_mins = tuple(
        (float(bbox.Min.X), float(bbox.Min.Y), float(bbox.Min.Z))
        for bbox in bboxes
    )
    bbox_maxes = tuple(
        (float(bbox.Max.X), float(bbox.Max.Y), float(bbox.Max.Z))
        for bbox in bboxes
    )
    aggregate_min = tuple(min(point[index] for point in bbox_mins) for index in range(3))
    aggregate_max = tuple(max(point[index] for point in bbox_maxes) for index in range(3))
    envelope_min = tuple(float(item) for item in predecessor_envelope["min"])
    envelope_max = tuple(float(item) for item in predecessor_envelope["max"])
    envelope_passed = all(
        aggregate_min[axis] >= envelope_min[axis] - 1e-4
        and aggregate_max[axis] <= envelope_max[axis] + 1e-4
        for axis in range(3)
    )
    if not envelope_passed:
        failures.append("Stage 4 geometry escaped the fixed predecessor envelope")

    windows_passed, window_failures = _actual_window_clear(model, operations)
    failures.extend(window_failures)
    shaft_objects = {
        item.Attributes.Name: item
        for item in objects
        if dict(item.Attributes.GetUserStrings() or ()).get("archflow:operation_kind") == "doric_shaft"
    }
    expected_doric_shaft_count = sum(
        str(item.get("kind")) == "doric_shaft" for item in operations
    )
    axis_passed = True
    actual_flute_counts: dict[str, int] = {}
    for operation_id, item in shaft_objects.items():
        operation = by_operation[operation_id]
        params = operation["parameters"]
        assert isinstance(params, Mapping)
        bbox = item.Geometry.GetBoundingBox()
        height = float(bbox.Max.Z) - float(bbox.Min.Z)
        width = max(float(bbox.Max.X) - float(bbox.Min.X), float(bbox.Max.Y) - float(bbox.Min.Y))
        if height <= width * 3.0 or not math.isclose(height, float(params["shaft_height"]), abs_tol=2e-4):
            axis_passed = False
            failures.append(f"shaft {operation_id} is not a Z-axis shaft of the declared length")
        if isinstance(item.Geometry, rhino3dm.Mesh):
            actual_flute_counts[operation_id] = _mesh_flute_count(item.Geometry, params["center"])
        else:
            actual_flute_counts[operation_id] = 0
    if len(shaft_objects) != expected_doric_shaft_count or any(
        value != FLUTE_COUNT for value in actual_flute_counts.values()
    ):
        failures.append(
            "Doric shaft readback count or actual twenty-flute geometry drifted"
        )

    detail_ids = {
        str(item["operation_id"])
        for item in operations
        if item["kind"] in {"doric_neck", "doric_echinus", "doric_abacus", "triglyph"}
        or str(item["operation_id"]).startswith("metope-")
    }
    object_by_name = {item.Attributes.Name or "": item for item in objects}
    detail_bounds = {
        operation_id: object_by_name[operation_id].Geometry.GetBoundingBox()
        for operation_id in detail_ids
        if operation_id in object_by_name
    }
    collisions: list[dict[str, object]] = []
    numerical_planar_contacts: list[dict[str, object]] = []
    detail_names = sorted(detail_bounds)
    for index, first_name in enumerate(detail_names):
        first_bbox = detail_bounds[first_name]
        first = (
            (float(first_bbox.Min.X), float(first_bbox.Min.Y), float(first_bbox.Min.Z)),
            (float(first_bbox.Max.X), float(first_bbox.Max.Y), float(first_bbox.Max.Z)),
        )
        for second_name in detail_names[index + 1 :]:
            second_bbox = detail_bounds[second_name]
            second = (
                (float(second_bbox.Min.X), float(second_bbox.Min.Y), float(second_bbox.Min.Z)),
                (float(second_bbox.Max.X), float(second_bbox.Max.Y), float(second_bbox.Max.Z)),
            )
            overlap_lengths = [
                max(
                    0.0,
                    min(first[1][axis], second[1][axis])
                    - max(first[0][axis], second[0][axis]),
                )
                for axis in range(3)
            ]
            volume = math.prod(overlap_lengths)
            if volume > 1e-8:
                # rhino3dm stores mesh vertices as floats.  Nominally planar
                # capital contacts can therefore read back with a sub-micron Z
                # overlap.  Record that tolerance explicitly; never use it for
                # a volumetric X/Y clash.
                if overlap_lengths[2] <= 1e-5:
                    numerical_planar_contacts.append(
                        {
                            "first": first_name,
                            "second": second_name,
                            "z_overlap_m": overlap_lengths[2],
                            "volume_m3": volume,
                        }
                    )
                else:
                    collisions.append({"first": first_name, "second": second_name, "volume_m3": volume})
    if collisions:
        failures.append("detail-detail volume collisions were introduced")

    continuity_failures: list[str] = []
    peristyle_shafts = [item for item in operations if item["kind"] == "doric_shaft" and str(item["operation_id"]).startswith("peristyle-")]
    architrave_bottom = min(
        float(item["parameters"]["origin"][2])
        for item in operations
        if item["kind"] == "entablature_layer" and item["parameters"]["layer"] == "architrave"
    )
    for shaft in peristyle_shafts:
        shaft_id = str(shaft["operation_id"])
        abacus = by_operation[f"{shaft_id}-abacus"]
        params = abacus["parameters"]
        abacus_top = float(params["origin"][2]) + float(params["size"][2])
        if not math.isclose(abacus_top, architrave_bottom, abs_tol=1e-9):
            continuity_failures.append(shaft_id)
    if continuity_failures:
        failures.append("peristyle capital-to-architrave continuity failed")

    banned_tokens = (
        "nero",
        "shield-hole",
        "medieval",
        "byzantine",
        "graffiti",
        "fresco",
        "scaffold",
        "lifting-tackle",
        "restoration-trace",
    )
    leakage = [name for name in names if any(token in name.lower() for token in banned_tokens)]
    text_geometry = [name for name, item in object_by_name.items() if "Text" in type(item.Geometry).__name__]
    if leakage or text_geometry:
        failures.append("branch-excluded text/intervention geometry leaked into the model")

    return {
        "schema": "ParthenonStage4SpatialValidation@1",
        "status": "PASSED" if not failures else "FAILED",
        "passed": not failures,
        "length_unit": "meter",
        "checks": {
            "fixed_predecessor_envelope": envelope_passed,
            "actual_window_voids_clear": windows_passed,
            "z_up_column_long_axes": axis_passed,
            "actual_twenty_flute_shafts": (
                len(actual_flute_counts) == expected_doric_shaft_count
                and all(value == FLUTE_COUNT for value in actual_flute_counts.values())
            ),
            "doric_shaft_denominator": expected_doric_shaft_count,
            "detail_collision_free": not collisions,
            "capital_entablature_continuity": not continuity_failures,
            "branch_leakage_free": not leakage and not text_geometry,
            "object_operation_bijection": len(names) == len(by_operation) and set(names) == set(by_operation) and len(set(names)) == len(names),
        },
        "predecessor_envelope": {"min": list(envelope_min), "max": list(envelope_max)},
        "aggregate_bbox": {"min": list(aggregate_min), "max": list(aggregate_max)},
        "actual_flute_counts": actual_flute_counts,
        "detail_collisions": collisions,
        "allowed_contacts": [
            "shaft-to-neck planar contact",
            "neck-to-echinus planar contact",
            "echinus-to-abacus planar contact",
            "peristyle-abacus-to-architrave planar contact",
            "metope/triglyph relief to frieze host",
        ],
        "numerical_planar_contacts": numerical_planar_contacts,
        "failures": failures,
        "canonical_write_authority": False,
    }


def validate_stage4_model(
    path: Path,
    *,
    operations: Sequence[Mapping[str, object]],
    program_digest: str,
) -> dict[str, object]:
    spatial = validate_stage4_spatial_model(
        path,
        operations=operations,
        program_digest=program_digest,
    )
    materials = validate_stage4_material_bindings(path, operations=operations)
    rhythm_passed, rhythm_failures = _validate_entablature_rhythm(operations)
    ids = tuple(str(item["operation_id"]) for item in operations)
    exterior_shafts = tuple(
        item for item in operations if item["kind"] == "doric_shaft" and str(item["operation_id"]).startswith("peristyle-")
    )
    porch_shafts = tuple(
        item for item in operations if item["kind"] == "doric_shaft" and str(item["operation_id"]).startswith("porch-")
    )
    normal_exterior = tuple(
        item for item in exterior_shafts if math.isclose(float(item["parameters"]["lower_diameter"]), NORMAL_LOWER_DIAMETER, abs_tol=1e-9)
    )
    dimension_passed = bool(normal_exterior) and all(
        math.isclose(float(item["parameters"]["total_column_height"]), EXTERIOR_TOTAL_COLUMN_HEIGHT, abs_tol=1e-9)
        and 1.482 <= float(item["parameters"]["upper_diameter"]) <= 1.494
        and 0.016 <= float(item["parameters"]["entasis_max"]) <= 0.0174
        and math.isclose(float(item["parameters"]["entasis_location_ratio"]), 0.4, abs_tol=1e-9)
        and int(item["parameters"]["flutes"]) == FLUTE_COUNT
        for item in normal_exterior
    )
    entablature_totals = {}
    for side in ("east", "north", "south", "west"):
        layers = [
            item
            for item in operations
            if item["kind"] == "entablature_layer" and item["parameters"]["side"] == side
        ]
        layer_total = sum(float(item["parameters"]["size"][2]) for item in layers)
        if not any(item["parameters"]["layer"] == "cornice" for item in layers):
            replacement_id = (
                f"eave-{side}-geison"
                if side in {"north", "south"}
                else f"pediment-{side}-horizontal-geison"
            )
            replacement = next(
                (
                    item
                    for item in operations
                    if str(item["operation_id"]) == replacement_id
                ),
                None,
            )
            replacement_parameters = (
                replacement.get("parameters")
                if isinstance(replacement, Mapping)
                else None
            )
            inherited_host = (
                replacement_parameters.get("inherited_host_envelope")
                if isinstance(replacement_parameters, Mapping)
                else None
            )
            if isinstance(inherited_host, Mapping):
                layer_total += float(inherited_host["size"][2])
        entablature_totals[side] = layer_total
    window_validation = validate_window_voids(operations)
    failures = list(spatial["failures"])
    failures.extend(materials["failures"])
    failures.extend(rhythm_failures)
    if not dimension_passed:
        failures.append("normal exterior Doric dimensions or entasis drifted")
    if any(not math.isclose(value, 3.30, abs_tol=1e-9) for value in entablature_totals.values()):
        failures.append("entablature layer total drifted from 3.30 m")
    if not window_validation["passed"]:
        failures.extend(window_validation["failures"])
    checks = {
        "exterior_flute_count": FLUTE_COUNT,
        "exterior_shaft_count": len(exterior_shafts),
        "porch_shaft_count": len(porch_shafts),
        "echinus_count": sum(item.endswith("-echinus") for item in ids),
        "abacus_count": sum(item.endswith("-abacus") for item in ids),
        "metope_count": sum(item.startswith("metope-") for item in ids),
        "triglyph_count": sum(item.startswith("triglyph-") for item in ids),
        "normal_exterior_dimensions": dimension_passed,
        "entablature_total_height_m": entablature_totals,
        "strict_triglyph_metope_rhythm": rhythm_passed,
        "window_voids": window_validation["passed"],
        "material_bindings": materials["passed"],
        "material_binding_coverage": materials["checks"].get(
            "resolved_binding_coverage", 0.0
        ),
        "material_binding_checks": materials["checks"],
        **spatial["checks"],
    }
    return {
        "schema": "ParthenonStage4DetailValidation@1",
        "passed": not failures,
        "status": "PASSED" if not failures else "FAILED",
        "checks": checks,
        "failures": failures,
        "spatial_validation_digest": _digest(spatial),
        "canonical_write_authority": False,
    }


def rebind_predecessor_state(
    predecessor: OperationalMarkovState,
    successor_run: RunRef,
    *,
    binding_ref: str,
) -> tuple[OperationalMarkovState, dict[str, object]]:
    if predecessor.branch.run.project_id != PROJECT_ID or successor_run.project_id != PROJECT_ID:
        raise ParthenonStage4Error("state rebind crossed project identity")
    if predecessor.branch.branch_id != BRANCH_ID:
        raise ParthenonStage4Error("state rebind crossed the selected branch")
    if predecessor.branch.run.base != successor_run.base:
        raise ParthenonStage4Error("state rebind crossed the exact canonical base")
    if predecessor.branch.run.run_id == successor_run.run_id:
        raise ParthenonStage4Error("state rebind must cross runs")
    if any(item.obligation_id == "close-stage-4" for item in predecessor.obligations):
        raise ParthenonStage4Error("predecessor already contains Stage 4")
    if any(item.status is not ObligationStatus.SATISFIED for item in predecessor.obligations):
        raise ParthenonStage4Error("Stage 3 predecessor obligations are not all satisfied")
    stage4_obligation = DesignObligation(
        obligation_id="close-stage-4",
        statement=(
            "Close Stage 4 only after model readback, spatial, detail, evidence, "
            "roof/eaves/pediment, inner-colonnade, human-authorized principal-door "
            "assembly, full-successor relations, exact Stage 3 component coverage, "
            "and effective material-binding gates all pass."
        ),
        source_ref="requirement:parthenon-stage-4",
        status=ObligationStatus.OPEN,
        subject_refs=("deliverable:parthenon-stage-4-detail-candidate",),
        validator_ref="validator:parthenon-stage-4-close-gates",
        condition=ObligationCondition(
            ref="fact:semantic:reconstruction-strategy",
            expected_value="idealized-periclean-original",
        ),
    )
    opened = replace(
        predecessor,
        branch=BranchRef(
            run=successor_run,
            branch_id=BRANCH_ID,
            epoch=predecessor.branch.epoch + 1,
        ),
        compiler_version="p087-parthenon-stage4-runner-1",
        facts=predecessor.facts,
        locks=predecessor.locks,
        obligations=(*predecessor.obligations, stage4_obligation),
        evidence_refs=predecessor.evidence_refs,
    )
    receipt = {
        "schema": "ParthenonStage4StateRebind@1",
        "binding_ref": binding_ref,
        "predecessor_run_id": predecessor.branch.run.run_id,
        "successor_run_id": successor_run.run_id,
        "predecessor_state_digest": predecessor.state_digest,
        "opened_state_digest": opened.state_digest,
        "predecessor_branch_epoch": predecessor.branch.epoch,
        "successor_branch_epoch": opened.branch.epoch,
        "facts_unchanged": predecessor.facts == opened.facts,
        "locks_unchanged": predecessor.locks == opened.locks,
        "evidence_refs_unchanged": predecessor.evidence_refs == opened.evidence_refs,
        "facts_canonical_sha256": _digest([item.to_dict() for item in predecessor.facts]),
        "locks_canonical_sha256": _digest([item.to_dict() for item in predecessor.locks]),
        "evidence_refs_canonical_sha256": _digest(list(predecessor.evidence_refs)),
        "stage4_obligation_status": "open",
        "canonical_write_authority": False,
    }
    return opened, receipt


def compile_stage4_close_gate(
    *,
    gate_receipts: Mapping[str, Mapping[str, object]],
    gate_refs: Mapping[str, str],
) -> dict[str, object]:
    """Derive the Stage 4 close decision from typed retained receipts.

    A non-empty reference is never accepted as proof by itself.  Every named
    gate must have both a receipt payload and a logical reference, and the
    payload must report its own successful status.  Component coverage uses
    the generic capability's ``PASS`` status; all other project gates require
    an explicit boolean ``passed`` field.
    """

    expected = set(STAGE4_CLOSE_GATE_NAMES)
    supplied_receipts = set(gate_receipts)
    supplied_refs = set(gate_refs)
    failures: list[str] = []
    checks: dict[str, dict[str, object]] = {}
    hard_gate_failure_refs: list[str] = []
    for gate_name in STAGE4_CLOSE_GATE_NAMES:
        receipt = gate_receipts.get(gate_name)
        gate_ref = gate_refs.get(gate_name)
        missing_receipt = not isinstance(receipt, Mapping)
        missing_ref = not isinstance(gate_ref, str) or not gate_ref.strip()
        if missing_receipt:
            failures.append(f"missing Stage 4 gate receipt: {gate_name}")
        if missing_ref:
            failures.append(f"missing Stage 4 gate ref: {gate_name}")
        if missing_receipt or missing_ref:
            passed = False
            status = "MISSING"
            receipt_failures: list[str] = []
        else:
            assert receipt is not None
            status = str(receipt.get("status", ""))
            if gate_name == "component_coverage":
                passed = (
                    status == StageComponentCoverageStatus.PASS.value
                    and int(receipt.get("operation_count", -1)) == 271
                    and int(receipt.get("component_count", -1)) == 11
                )
            else:
                passed = receipt.get("passed") is True
            raw_failures = receipt.get("failures", ())
            receipt_failures = (
                [str(item) for item in raw_failures]
                if isinstance(raw_failures, Sequence)
                and not isinstance(raw_failures, (str, bytes))
                else []
            )
            if not passed:
                failures.append(f"Stage 4 gate did not pass: {gate_name}")
                failures.extend(
                    f"{gate_name}: {item}" for item in receipt_failures[:8]
                )
        failure_ref = (
            str(gate_ref)
            if not missing_ref
            else f"gate:parthenon-stage-4/{gate_name}"
        )
        if not passed:
            hard_gate_failure_refs.append(failure_ref)
        checks[gate_name] = {
            "passed": passed,
            "status": status,
            "ref": None if missing_ref else str(gate_ref),
            "failure_count": len(receipt_failures),
        }

    unknown_receipts = sorted(supplied_receipts - expected)
    unknown_refs = sorted(supplied_refs - expected)
    if unknown_receipts or unknown_refs:
        failures.append("Stage 4 close gate received unknown gate identities")
    complete_refs = [
        str(gate_refs[name])
        for name in STAGE4_CLOSE_GATE_NAMES
        if isinstance(gate_refs.get(name), str) and str(gate_refs[name]).strip()
    ]
    if len(complete_refs) == len(STAGE4_CLOSE_GATE_NAMES) and len(
        set(complete_refs)
    ) != len(complete_refs):
        failures.append("Stage 4 close gate refs must be distinct retained receipts")
    passed = not failures
    payload = {
        "schema": "ParthenonStage4CloseGate@1",
        "status": "PASSED" if passed else "FAILED",
        "passed": passed,
        "checks": checks,
        "required_gate_names": list(STAGE4_CLOSE_GATE_NAMES),
        "hard_gate_failure_refs": sorted(set(hard_gate_failure_refs)),
        "conflict_refs": [],
        "tolerance_failure_refs": [],
        "revalidation_refs": (
            sorted(str(gate_refs[name]) for name in STAGE4_CLOSE_GATE_NAMES)
            if passed
            else []
        ),
        "failures": failures,
        "canonical_write_authority": False,
    }
    return {**payload, "receipt_digest": _digest(payload)}


def close_stage4_state(
    opened: OperationalMarkovState,
    *,
    gate_receipts: Mapping[str, Mapping[str, object]],
    gate_refs: Mapping[str, str],
) -> tuple[OperationalMarkovState, object]:
    close_gate = compile_stage4_close_gate(
        gate_receipts=gate_receipts,
        gate_refs=gate_refs,
    )
    if not close_gate["passed"]:
        raise ParthenonStage4Error(
            "Stage 4 closure gates failed: " + "; ".join(close_gate["failures"])
        )
    realization_evidence_refs = tuple(close_gate["revalidation_refs"])
    target = next((item for item in opened.obligations if item.obligation_id == "close-stage-4"), None)
    if target is None or target.status is not ObligationStatus.OPEN:
        raise ParthenonStage4Error("Stage 4 obligation was not OPEN before closure")
    obligations = tuple(
        replace(item, status=ObligationStatus.SATISFIED)
        if item.obligation_id == "close-stage-4"
        else item
        for item in opened.obligations
    )
    closed = replace(
        opened,
        branch=BranchRef(
            run=opened.branch.run,
            branch_id=opened.branch.branch_id,
            epoch=opened.branch.epoch + 1,
        ),
        obligations=obligations,
        evidence_refs=tuple(sorted({*opened.evidence_refs, *realization_evidence_refs})),
    )
    policy = StageConvergencePolicy(
        policy_id="parthenon-stage-4-convergence",
        stage="stage-4",
        protected_refs=("fact:semantic:reconstruction-strategy",),
        mandatory_obligation_ids=("close-stage-4",),
    )
    request = StageTransitionRequest(
        request_id="parthenon-stage-4-resolve",
        stage="stage-4",
        kind=StageTransitionKind.RESOLVE,
        authority_id="authority.agent-ratifier",
        parent_state_digest=opened.state_digest,
        child_state_digest=closed.state_digest,
        trigger_refs=tuple(
            sorted(
                {
                    "deliverable:parthenon-stage-4-detail-candidate",
                    *realization_evidence_refs,
                }
            )
        ),
    )
    convergence_evidence_refs = tuple(sorted(realization_evidence_refs))
    receipt = evaluate_stage_convergence(
        policy,
        request,
        opened,
        closed,
        parent_evidence=StageConvergenceEvidence(
            opened.state_digest,
            hard_gate_failure_refs=(),
            conflict_refs=(),
            tolerance_failure_refs=(),
            revalidation_refs=convergence_evidence_refs,
        ),
        child_evidence=StageConvergenceEvidence(
            closed.state_digest,
            hard_gate_failure_refs=(),
            conflict_refs=(),
            tolerance_failure_refs=(),
            revalidation_refs=(),
        ),
    )
    if receipt.outcome is not StageConvergenceOutcome.PROGRESS or not receipt.stage_ready:
        raise ParthenonStage4Error(f"Stage 4 did not converge: {', '.join(receipt.reason_codes)}")
    return closed, receipt


def guard_open_asset_candidates(candidates: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    required_adopted_fields = (
        "canonical_page_url",
        "author_or_publisher",
        "license",
        "license_url",
        "downloaded_at_utc",
        "bytes",
        "media_type",
        "format",
        "sha256",
        "object_ref",
        "workspace_path",
        "native_units",
        "native_units_evidence",
        "up_axis",
        "handedness",
        "axis_evidence",
        "bbox",
        "mesh_stats",
        "converter",
        "scale_transform",
        "axis_transform",
        "derived_sha256",
        "intended_use",
        "evidence_role",
        "decided_by",
        "decision_reason",
    )
    for candidate in candidates:
        decision = str(candidate.get("adoption_decision", ""))
        adopted = decision in {"ADOPT", "ADOPTED"}
        if adopted and candidate.get("requires_login") is True:
            raise AssetLoginRequired(
                f"asset {candidate.get('asset_id')} requires user login; stop before download"
            )
        if adopted:
            missing = [field for field in required_adopted_fields if candidate.get(field) in {None, "", "unknown"}]
            if missing:
                raise ParthenonStage4Error(
                    f"adopted asset {candidate.get('asset_id')} lacks provenance: {', '.join(missing)}"
                )
        elif decision not in {"PARKED", "REJECTED", "BLOCKED_LOGIN"}:
            raise ParthenonStage4Error(f"asset {candidate.get('asset_id')} has invalid adoption decision")
    return tuple(candidates)


def render_asset_provenance_document(*, captured_at: str) -> str:
    captured_at = _normalize_captured_at(captured_at)
    guard_open_asset_candidates(OPEN_ASSET_CANDIDATES)
    lines = [
        "# Parthenon Stage 4 外部资产来源清单",
        "",
        f"- 编译时间（UTC）：{captured_at}",
        f"- 分支：`{BRANCH_ID}`",
        "- 裁决：本阶段采用程序化生成；没有外部网格进入几何程序或 3DM。",
        "- 原因：匿名核验的候选没有同时证明 Parthenon-specific、测绘级、标准格式、明确许可证、明确单位和明确上轴。",
        "- 视觉 ROI 仅支持元素存在、可见形态、拓扑与相对位置；不授权 exact_dimension。",
        "",
    ]
    for candidate in OPEN_ASSET_CANDIDATES:
        lines.extend(
            (
                f"## {candidate['asset_id']}",
                "",
                f"- 来源 URL：{candidate['canonical_page_url']}",
                f"- Direct URL：{candidate['direct_download_url'] or '未下载'}",
                f"- 作者/发布者：{candidate['author_or_publisher']}",
                f"- 许可证：{candidate['license']}",
                f"- 许可证 URL：{candidate['license_url'] or '未证明'}",
                f"- 下载时间：{candidate['downloaded_at_utc'] or '未下载'}",
                f"- Bytes / Media Type：{candidate['bytes'] or 'n/a'} / {candidate['media_type'] or 'n/a'}",
                f"- 格式 / Compression：{candidate['format'] or 'unknown'} / {candidate['compression'] or 'unknown'}",
                f"- SHA-256：{candidate['sha256'] or 'n/a'}",
                f"- P036 Object Ref：{candidate['object_ref'] or 'n/a'}",
                f"- Workspace Path：{candidate['workspace_path'] or 'n/a'}",
                f"- 单位：{candidate['native_units']}；证据：{candidate['native_units_evidence'] or '无'}",
                f"- 上轴 / Handedness：{candidate['up_axis']} / {candidate['handedness']}；证据：{candidate['axis_evidence'] or '无'}",
                f"- BBox / Mesh Stats：{candidate['bbox'] or 'n/a'} / {candidate['mesh_stats'] or 'n/a'}",
                f"- Converter / Scale / Axis Transform：{candidate['converter'] or 'n/a'} / {candidate['scale_transform'] or 'n/a'} / {candidate['axis_transform'] or 'n/a'}",
                f"- Derived SHA-256：{candidate['derived_sha256'] or 'n/a'}",
                f"- 用途 / Evidence Role：{candidate['intended_use']} / {candidate['evidence_role']}",
                f"- 采纳裁决：{candidate['adoption_decision']}（未采纳）",
                f"- 裁决者与理由：{candidate['decided_by']}；{candidate['decision_reason']}",
                "",
            )
        )
    lines.extend(
        (
            "## 程序化生成为何被采纳",
            "",
            "程序化生成从精确 Stage 3 的完整 271 个根操作分母出发逐项处置；每个根操作必须由 coverage receipt 标记为 REFINED、VERIFIED_UNCHANGED 或 PARKED_WITH_REASON，且所有最终 successor 必须通过 lineage 折回该分母。全建筑细化路线还受 inner-colonnade、roof/eaves/pediment、human-authorized door assembly、material binding、full-successor relation 与实际 3DM readback gates 共同约束。",
            "",
            "Sketchfab 候选需要登录且匿名页面访问受阻；本次没有尝试绕过登录、没有保存凭据，也没有下载。若未来要采用，必须由用户先完成登录并重新执行完整许可证/单位/上轴审查。",
            "",
        )
    )
    return "\n".join(lines)


def _load_exact_predecessors(
    repository: FilesystemProjectRepository,
) -> dict[str, object]:
    expected_base = ProjectVersionRef(
        PROJECT_ID,
        CANONICAL_VERSION,
        CANONICAL_STATE_SHA256,
    )
    head = repository.read_head()
    if head != expected_base:
        raise ParthenonStage4Error(
            "canonical HEAD drifted; P087 is bound to exact version 0/state digest"
        )
    predecessor_run = repository.load_run(PREDECESSOR_RUN_ID)
    visual_run = repository.load_run(VISUAL_RUN_ID)
    if predecessor_run.base != expected_base or visual_run.base != expected_base:
        raise ParthenonStage4Error("predecessor run base drifted from exact canonical HEAD")

    progress = repository.load_json(PREDECESSOR_PROGRESS_REF)
    if (
        progress.get("schema") != "ParthenonProgressSnapshot@1"
        or progress.get("run_id") != PREDECESSOR_RUN_ID
        or progress.get("selected_branch_id") != BRANCH_ID
        or progress.get("disposition") != "HOLD"
        or progress.get("canonical_write_authority") is not False
    ):
        raise ParthenonStage4Error("exact Stage 3 progress snapshot changed identity or disposition")

    state_payload = repository.load_json(PREDECESSOR_STATE_REF)
    predecessor_state = OperationalMarkovState.from_dict(state_payload)
    if (
        predecessor_state.state_digest != PREDECESSOR_STATE_DIGEST
        or predecessor_state.branch.run != predecessor_run
        or predecessor_state.branch.branch_id != BRANCH_ID
        or predecessor_state.branch.epoch != 5
        or len(predecessor_state.facts) != 18
        or len(predecessor_state.locks) != 1
        or len(predecessor_state.obligations) != 4
        or any(item.status is not ObligationStatus.SATISFIED for item in predecessor_state.obligations)
    ):
        raise ParthenonStage4Error("exact Stage 3 operational state drifted")

    pack_payload = repository.load_json(PREDECESSOR_PACK_REF)
    predecessor_pack = StageEvidencePack.from_dict(pack_payload)
    if (
        predecessor_pack.stage_id != "stage-3"
        or predecessor_pack.stage_index != 3
        or predecessor_pack.run_id != PREDECESSOR_RUN_ID
        or predecessor_pack.branch.branch_id != BRANCH_ID
        or predecessor_pack.branch.epoch != 5
        or predecessor_pack.program_digest != PREDECESSOR_PROGRAM_DIGEST
        or predecessor_pack.compilation_status is not StagePackCompilationStatus.COMPLETE
        or not predecessor_pack.closure.closed
    ):
        raise ParthenonStage4Error("exact Stage 3 evidence pack drifted or is incomplete")

    program = repository.load_json(PREDECESSOR_PROGRAM_REF)
    operations_value = program.get("operations")
    if (
        program.get("schema") != "ParthenonArchitecturalIR@1"
        or program.get("stage") != 3
        or program.get("program_digest") != PREDECESSOR_PROGRAM_DIGEST
        or program.get("length_unit") != "meter"
        or not isinstance(operations_value, list)
        or len(operations_value) != 271
    ):
        raise ParthenonStage4Error("exact Stage 3 geometry program drifted")
    coordinate = program.get("coordinate_system")
    if not isinstance(coordinate, Mapping) or (
        coordinate.get("target"), coordinate.get("up_axis"), coordinate.get("handedness")
    ) != ("RhinoWorldXY", "Z", "right"):
        raise ParthenonStage4Error("Stage 3 program is not exact right-handed Rhino Z-up")
    pack_program_bindings = tuple(
        item
        for item in predecessor_pack.bindings
        if item.role is StageEvidenceRole.GEOMETRY_PROGRAM
    )
    if len(pack_program_bindings) != 1 or pack_program_bindings[0].ref != PREDECESSOR_PROGRAM_REF:
        raise ParthenonStage4Error("Stage 3 pack does not bind the exact program record")

    model_path = repository.layout.resolve_relative(PREDECESSOR_MODEL_RELATIVE_PATH)
    try:
        model_bytes = model_path.read_bytes()
    except OSError as exc:
        raise ParthenonStage4Error("exact Stage 3 workspace model is unavailable") from exc
    if _sha_bytes(model_bytes) != PREDECESSOR_MODEL_SHA256:
        raise ParthenonStage4Error("exact Stage 3 workspace model digest drifted")
    cad_bindings = tuple(
        item
        for item in predecessor_pack.artifacts
        if item.role is StageArtifactRole.CAD_MODEL
    )
    if len(cad_bindings) != 1 or (
        cad_bindings[0].ref.sha256 != PREDECESSOR_MODEL_SHA256
        or cad_bindings[0].program_digest != PREDECESSOR_PROGRAM_DIGEST
    ):
        raise ParthenonStage4Error("Stage 3 pack does not bind the exact CAD model")
    object_bytes = repository.layout.resolve_relative(cad_bindings[0].ref.relative_path).read_bytes()
    if object_bytes != model_bytes:
        raise ParthenonStage4Error("Stage 3 workspace model and P036 object differ")
    model_inspection = inspect_three_dm(model_path)
    if (
        model_inspection.file_sha256 != PREDECESSOR_MODEL_SHA256
        or model_inspection.top_level_object_count != 271
        or model_inspection.units.get("name") != "Meters"
    ):
        raise ParthenonStage4Error("Stage 3 model readback drifted")
    document_strings = {
        item["key"]: item["value"] for item in model_inspection.document_user_strings
    }
    if document_strings.get("archflow:coordinate_system") != "RhinoWorldXY_ZUp" or document_strings.get("archflow:up_axis") != "Z":
        raise ParthenonStage4Error("Stage 3 model readback is not RhinoWorldXY/Z-up")
    bbox = model_inspection.aggregate_bbox
    if bbox is None or any(
        not math.isclose(float(bbox[key][axis]), float(PREDECESSOR_BBOX[key][axis]), abs_tol=2e-5)
        for key in ("min", "max")
        for axis in range(3)
    ):
        raise ParthenonStage4Error("Stage 3 model predecessor envelope drifted")

    retained_inspection = repository.load_json(PREDECESSOR_INSPECTION_REF)
    if retained_inspection.get("artifact_ref", {}).get("sha256") != PREDECESSOR_MODEL_SHA256:
        raise ParthenonStage4Error("Stage 3 retained inspection does not bind the model")
    retained_spatial = repository.load_json(PREDECESSOR_SPATIAL_REF)
    if retained_spatial.get("status") != "PASSED" or retained_spatial.get("canonical_write_authority") is not False:
        raise ParthenonStage4Error("Stage 3 predecessor spatial receipt is not PASSED/HOLD")

    visual_manifest = repository.load_json(VISUAL_MANIFEST_REF)
    selected = validate_visual_manifest(visual_manifest)
    if (
        visual_manifest.get("run_id") != VISUAL_RUN_ID
        or len(selected) != 14
        or len(visual_manifest.get("parked_candidate_ids", ())) != 13
        or len(visual_manifest.get("rejected_candidate_ids", ())) != 5
        or len(visual_manifest.get("region_record_refs", ())) != 32
        or len(visual_manifest.get("overlay_record_refs", ())) != 8
        or visual_manifest.get("canonical_write_authority") is not False
        or visual_manifest.get("geometry_mutation_authority") is not False
    ):
        raise ParthenonStage4Error("exact P086 visual-region manifest drifted")

    return {
        "head": head,
        "predecessor_run": predecessor_run,
        "visual_run": visual_run,
        "progress": progress,
        "predecessor_state": predecessor_state,
        "predecessor_pack": predecessor_pack,
        "predecessor_program": program,
        "predecessor_operations": tuple(copy.deepcopy(item) for item in operations_value),
        "predecessor_model_path": model_path,
        "predecessor_model_artifact": cad_bindings[0].ref,
        "predecessor_model_inspection": model_inspection,
        "predecessor_spatial": retained_spatial,
        "visual_manifest": visual_manifest,
        "selected_visual_ids": selected,
    }


def _load_exact_incomplete_stage4_attempt(
    repository: FilesystemProjectRepository,
) -> dict[str, object]:
    """Read reconstruction-005 only as immutable failed-attempt evidence."""

    expected_base = ProjectVersionRef(
        PROJECT_ID,
        CANONICAL_VERSION,
        CANONICAL_STATE_SHA256,
    )
    failed_run = repository.load_run(stage4_corrections.FAILED_ATTEMPT_RUN_ID)
    if failed_run.base != expected_base:
        raise ParthenonStage4Error(
            "immutable reconstruction-005 failed-attempt base drifted"
        )

    evidence_payloads: dict[str, Mapping[str, object]] = {}
    uri_prefix = f"project://{PROJECT_ID}/"
    for uri in stage4_corrections.FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS:
        if not uri.endswith(".json"):
            continue
        if not uri.startswith(uri_prefix):
            raise ParthenonStage4Error(
                "failed-attempt evidence escaped the exact project"
            )
        match = re.search(r"-([0-9a-f]{64})\.json$", uri)
        if match is None:
            raise ParthenonStage4Error(
                "failed-attempt evidence ref is not content addressed"
            )
        ref = ProjectRecordRef(
            project_id=PROJECT_ID,
            relative_path=uri.removeprefix(uri_prefix),
            sha256=match.group(1),
        )
        payload = repository.load_json(ref)
        if not isinstance(payload, Mapping):
            raise ParthenonStage4Error(
                "failed-attempt evidence record is not a JSON object"
            )
        evidence_payloads[uri] = payload
    expected_json_evidence_count = sum(
        uri.endswith(".json")
        for uri in stage4_corrections.FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS
    )
    if len(evidence_payloads) != expected_json_evidence_count:
        raise ParthenonStage4Error(
            "failed-attempt evidence JSON denominator is incomplete"
        )

    program = repository.load_json(FAILED_STAGE4_PROGRAM_REF)
    operations = program.get("operations")
    lineage = program.get("lineage")
    if (
        program.get("schema") != "ParthenonStage4ArchitecturalIR@1"
        or program.get("project_id") != PROJECT_ID
        or program.get("run_id") != stage4_corrections.FAILED_ATTEMPT_RUN_ID
        or program.get("stage") != 4
        or program.get("branch_id") != BRANCH_ID
        or program.get("program_digest")
        != stage4_corrections.FAILED_STAGE4_PROGRAM_DIGEST
        or not isinstance(operations, list)
        or len(operations) != 509
        or not isinstance(lineage, Mapping)
        or lineage.get("predecessor_operation_count") != 271
        or lineage.get("current_operation_count") != 509
        or len(lineage.get("replacement_allowlist", ())) != 110
        or lineage.get("preserved_operation_count") != 161
        or not isinstance(lineage.get("preserved_operation_fingerprints"), Mapping)
        or len(lineage["preserved_operation_fingerprints"]) != 161
    ):
        raise ParthenonStage4Error(
            "immutable reconstruction-005 failed-attempt program drifted"
        )
    model_path = repository.layout.resolve_relative(
        FAILED_STAGE4_MODEL_RELATIVE_PATH
    )
    try:
        model_sha256 = _sha_bytes(model_path.read_bytes())
    except OSError as exc:
        raise ParthenonStage4Error(
            "immutable reconstruction-005 failed-attempt model is unavailable"
        ) from exc
    if model_sha256 != stage4_corrections.FAILED_STAGE4_MODEL_SHA256:
        raise ParthenonStage4Error(
            "immutable reconstruction-005 failed-attempt model digest drifted"
        )
    inspection = inspect_three_dm(model_path)
    if (
        inspection.file_sha256 != stage4_corrections.FAILED_STAGE4_MODEL_SHA256
        or inspection.top_level_object_count != 509
        or inspection.units.get("name") != "Meters"
    ):
        raise ParthenonStage4Error(
            "immutable reconstruction-005 failed-attempt model readback drifted"
        )
    pack_payload = evidence_payloads[stage4_corrections.FAILED_STAGE4_PACK_REF]
    state_payload = evidence_payloads[stage4_corrections.FAILED_STAGE4_STATE_REF]
    progress_payload = evidence_payloads[
        stage4_corrections.FAILED_STAGE4_PROGRESS_REF
    ]
    failed_pack = StageEvidencePack.from_dict(pack_payload)
    failed_state = OperationalMarkovState.from_dict(state_payload)
    if (
        failed_pack.run_id != stage4_corrections.FAILED_ATTEMPT_RUN_ID
        or failed_pack.branch.branch_id != BRANCH_ID
        or failed_pack.compilation_status is not StagePackCompilationStatus.COMPLETE
        or not failed_pack.closure.closed
        or failed_pack.program_digest
        != stage4_corrections.FAILED_STAGE4_PROGRAM_DIGEST
        or failed_state.branch.run != failed_run
        or failed_state.branch.branch_id != BRANCH_ID
        or progress_payload.get("schema") != "ParthenonStage4Progress@1"
        or progress_payload.get("run_id")
        != stage4_corrections.FAILED_ATTEMPT_RUN_ID
        or progress_payload.get("disposition") != "HOLD"
        or progress_payload.get("program_ref", {}).get("sha256")
        != stage4_corrections.FAILED_STAGE4_PROGRAM_SHA256
        or progress_payload.get("pack_ref", {}).get("sha256")
        != stage4_corrections.FAILED_STAGE4_PACK_SHA256
    ):
        raise ParthenonStage4Error(
            "immutable reconstruction-005 state/pack/progress binding drifted"
        )
    return {
        "run": failed_run,
        "program": program,
        "operations": tuple(copy.deepcopy(item) for item in operations),
        "lineage": copy.deepcopy(dict(lineage)),
        "model_path": model_path,
        "inspection": inspection,
        "state": failed_state,
        "pack": failed_pack,
        "evidence_payloads": evidence_payloads,
    }


def compile_stage4_evidence_gate(
    *,
    visual_manifest: Mapping[str, object],
    source_record_refs: Mapping[str, ProjectRecordRef] | None = None,
) -> dict[str, object]:
    source_ids = {str(item["source_id"]) for item in TEXT_SOURCES}
    failures: list[str] = []
    if len(source_ids) != len(TEXT_SOURCES):
        failures.append("text source ids are not unique")
    for claim in EVIDENCE_CLAIMS:
        claim_sources = tuple(str(item) for item in claim["source_ids"])
        if not claim_sources or any(item not in source_ids for item in claim_sources):
            failures.append(f"claim {claim['claim_id']} has missing source identity")
        if claim["classification"] == "HARD" and len(set(claim_sources)) < 2:
            failures.append(f"HARD claim {claim['claim_id']} lacks two source families")
    try:
        selected = validate_visual_manifest(visual_manifest)
    except ValueError as exc:
        failures.append(str(exc))
        selected = ()
    if len(selected) != 14:
        failures.append("visual gate did not retain exact 14 selected ROIs")
    missing_required_rois = sorted(
        set(REQUIRED_STAGE4_SELECTED_ROIS) - set(selected)
    )
    if missing_required_rois:
        failures.append(
            "Stage 4 required ROI set is not selected: "
            + ", ".join(missing_required_rois)
        )
    guard_open_asset_candidates(OPEN_ASSET_CANDIDATES)
    adopted_assets = tuple(
        item["asset_id"]
        for item in OPEN_ASSET_CANDIDATES
        if item["adoption_decision"] in {"ADOPT", "ADOPTED"}
    )
    if adopted_assets:
        failures.append("P087 procedural route unexpectedly adopted an external mesh")
    if source_record_refs is not None and set(source_record_refs) != source_ids:
        failures.append("persisted source record denominator is incomplete")
    return {
        "schema": "ParthenonStage4EvidenceGate@1",
        "status": "PASSED" if not failures else "FAILED",
        "passed": not failures,
        "text_source_count": len(TEXT_SOURCES),
        "claim_count": len(EVIDENCE_CLAIMS),
        "hard_claims_have_two_sources": not any("HARD claim" in item for item in failures),
        "classifications": sorted({str(item["classification"]) for item in EVIDENCE_CLAIMS}),
        "selected_visual_count": len(selected),
        "required_selected_visual_count": len(REQUIRED_STAGE4_SELECTED_ROIS),
        "missing_required_visual_ids": missing_required_rois,
        "parked_visual_count": len(visual_manifest.get("parked_candidate_ids", ())),
        "rejected_visual_count": len(visual_manifest.get("rejected_candidate_ids", ())),
        "visual_exact_dimension_authority": False,
        "adopted_external_assets": list(adopted_assets),
        "procedural_geometry_adopted": not adopted_assets,
        "asset_decisions": {
            str(item["asset_id"]): str(item["adoption_decision"])
            for item in OPEN_ASSET_CANDIDATES
        },
        "source_record_refs": (
            None
            if source_record_refs is None
            else {key: _record_dict(value) for key, value in sorted(source_record_refs.items())}
        ),
        "failures": failures,
        "canonical_write_authority": False,
    }


def _write_workspace_bytes(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    relative_within_workspace: Path,
    data: bytes,
) -> Path:
    workspace_root = repository.layout.run(run.run_id).workspaces.resolve(strict=False)
    target = (workspace_root / relative_within_workspace).resolve(strict=False)
    if not target.is_relative_to(workspace_root):
        raise ParthenonStage4Error("workspace write escaped its assigned P036 run")
    if target.exists():
        raise FileExistsError(f"immutable Stage 4 workspace target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    if target.read_bytes() != data:
        raise ParthenonStage4Error("workspace write failed readback")
    return target


def _put_branch_json(
    repository: FilesystemProjectRepository,
    run: RunRef,
    record_kind: str,
    payload: Mapping[str, object],
) -> ProjectRecordRef:
    return repository.put_json(
        run=run,
        destination=_branch_destination(run.run_id),
        record_kind=record_kind,
        payload=payload,
    )


def _require_human_authorization_ref(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or not value.startswith(
            (
                "human-authorization:",
                "decision:human-authorized-",
                "project://",
            )
        )
    ):
        raise ParthenonStage4Error(
            "Stage 4 principal-door compilation requires a typed human "
            "authorization or persisted project record reference"
        )
    return value


def _compile_human_authorization_capture(
    *,
    authorization_ref: str,
    statement: str,
    captured_at: str,
) -> dict[str, object]:
    """Retain the user's bounded decision before compiling door geometry."""

    authorization = _require_human_authorization_ref(authorization_ref)
    if authorization.startswith("project://"):
        raise ParthenonStage4Error(
            "authorization capture needs the source human decision ref, not an "
            "unverified project URI"
        )
    if not isinstance(statement, str) or not statement.strip():
        raise ParthenonStage4Error(
            "human door authorization requires the retained user statement"
        )
    return {
        "schema": "ParthenonStage4HumanAuthorizationCapture@1",
        "project_id": PROJECT_ID,
        "run_id": RESEARCH_RUN_ID,
        "branch_id": BRANCH_ID,
        "captured_at": _normalize_captured_at(captured_at),
        "authorization_ref": authorization,
        "statement": statement.strip(),
        "authorized_strategy": (
            stage4_doors.DoorAssemblyResolution.AUTHORIZED_CLOSED_DOUBLE_LEAF.value
        ),
        "authority_boundary": {
            "candidate_strategy_authorized": True,
            "historical_truth_authorized": False,
            "exact_metric_authority": False,
            "canonical_promotion_authorized": False,
        },
        "dependency_completeness_required": True,
        "canonical_write_authority": False,
    }


def _require_research_record_uri(value: object, field_name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(
        rf"project://{PROJECT_ID}/runs/{RESEARCH_RUN_ID}/(?:branches/"
        rf"{BRANCH_ID}/)?records/[a-z0-9][a-z0-9._-]*-[0-9a-f]{{64}}\.json",
        value,
    ) is None:
        raise ParthenonStage4Error(
            f"{field_name} must be a content-addressed {RESEARCH_RUN_ID} record URI"
        )
    return value


def _compile_correction_provenance_gate(
    *,
    experience_record: Mapping[str, object],
    experience_ref: str,
    door_decision_record: Mapping[str, object],
    door_decision_ref: str,
    authorization_capture: Mapping[str, object],
    authorization_capture_ref: str,
) -> dict[str, object]:
    uri_failures: list[str] = []
    for value, field_name in (
        (experience_ref, "experience_ref"),
        (door_decision_ref, "door_decision_ref"),
        (authorization_capture_ref, "authorization_capture_ref"),
    ):
        try:
            _require_research_record_uri(value, field_name)
        except ParthenonStage4Error as exc:
            uri_failures.append(str(exc))
    experience_validation = (
        stage4_corrections.validate_incomplete_stage4_experience_record(
            experience_record
        )
    )
    door_validation = stage4_corrections.validate_authorized_door_decision_record(
        door_decision_record
    )
    capture_failures: list[str] = []
    if authorization_capture.get("schema") != (
        "ParthenonStage4HumanAuthorizationCapture@1"
    ):
        capture_failures.append("human authorization capture schema drifted")
    if authorization_capture.get("dependency_completeness_required") is not True:
        capture_failures.append("human authorization lost dependency-completeness scope")
    if authorization_capture.get("authority_boundary") != {
        "candidate_strategy_authorized": True,
        "historical_truth_authorized": False,
        "exact_metric_authority": False,
        "canonical_promotion_authorized": False,
    }:
        capture_failures.append("human authorization authority boundary drifted")
    if door_decision_record.get("authorization_ref") != authorization_capture_ref:
        capture_failures.append("door decision is not bound to the retained authorization")
    if door_decision_record.get("failed_attempt_experience_ref") != experience_ref:
        capture_failures.append("door decision is not bound to the retained experience ref")
    if door_decision_record.get("failed_attempt_experience_digest") != (
        experience_record.get("record_digest")
    ):
        capture_failures.append(
            "door decision is not bound to the retained experience digest"
        )
    failures = [
        *(f"experience: {item}" for item in experience_validation["failures"]),
        *(f"door decision: {item}" for item in door_validation["failures"]),
        *uri_failures,
        *capture_failures,
    ]
    return {
        "schema": "ParthenonStage4CorrectionProvenanceGate@1",
        "status": "PASSED" if not failures else "FAILED",
        "passed": not failures,
        "only_stage_predecessor_run_id": PREDECESSOR_RUN_ID,
        "failed_attempt_run_id": stage4_corrections.FAILED_ATTEMPT_RUN_ID,
        "failed_attempt_role": "FAILED_ATTEMPT_EVIDENCE_ONLY",
        "experience_ref": experience_ref,
        "door_decision_ref": door_decision_ref,
        "authorization_capture_ref": authorization_capture_ref,
        "experience_validation": experience_validation,
        "door_decision_validation": door_validation,
        "failures": failures,
        "canonical_write_authority": False,
    }


def _stage4_execution_projection(
    operations: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Remove only retained provenance URIs from the executable IR projection.

    Stage 4 is compiled once before fixed-run persistence with sentinel refs and
    once after P036 has assigned content-addressed refs.  Those refs may never
    influence geometry, object identity, assembly semantics, or relation input.
    """

    projected: list[dict[str, object]] = []
    for raw_operation in operations:
        operation = copy.deepcopy(dict(raw_operation))
        operation.pop("source_refs", None)
        parameter_basis = operation.get("parameter_basis")
        if isinstance(parameter_basis, dict):
            parameter_basis.pop("source_refs", None)
        parameters = operation.get("parameters")
        if isinstance(parameters, dict):
            aperture_contract = parameters.get("aperture_contract")
            if isinstance(aperture_contract, dict):
                aperture_contract.pop("human_authorization_ref", None)
        projected.append(operation)
    return tuple(projected)


def _preflight_research_record_uri(
    record_kind: str,
    payload: Mapping[str, object],
) -> str:
    return (
        f"project://{PROJECT_ID}/runs/{RESEARCH_RUN_ID}/branches/"
        f"{BRANCH_ID}/records/{record_kind}-{_digest(payload)}.json"
    )


def _preflight_reconstruction_record_ref(
    record_kind: str,
    payload: Mapping[str, object],
) -> ProjectRecordRef:
    digest = _digest(payload)
    return ProjectRecordRef(
        project_id=PROJECT_ID,
        relative_path=(
            f"runs/{RUN_ID}/branches/{BRANCH_ID}/records/"
            f"{record_kind}-{digest}.json"
        ),
        sha256=digest,
    )


def _preflight_run_record_ref(
    record_kind: str,
    payload: Mapping[str, object],
) -> ProjectRecordRef:
    digest = _digest(payload)
    return ProjectRecordRef(
        project_id=PROJECT_ID,
        relative_path=f"runs/{RUN_ID}/records/{record_kind}-{digest}.json",
        sha256=digest,
    )


def _preflight_review_record_ref(
    record_kind: str,
    payload: Mapping[str, object],
) -> ProjectRecordRef:
    digest = _digest(payload)
    return ProjectRecordRef(
        project_id=PROJECT_ID,
        relative_path=f"runs/{RUN_ID}/reviews/{record_kind}-{digest}.json",
        sha256=digest,
    )


def _compile_correction_semantics_preflight(
    *,
    predecessor_operations: Sequence[Mapping[str, object]],
    failed_attempt_lineage: Mapping[str, object],
    authorization_capture: Mapping[str, object],
) -> dict[str, object]:
    diagnosis_ref = _preflight_research_record_uri(
        "preflight-door-diagnosis",
        {"failed_attempt": stage4_corrections.FAILED_ATTEMPT_RUN_ID},
    )
    audit_ref = _preflight_research_record_uri(
        "preflight-failed-attempt-audit",
        {"program_digest": stage4_corrections.FAILED_STAGE4_PROGRAM_DIGEST},
    )
    experience_record = (
        stage4_corrections.compile_incomplete_stage4_experience_record(
            stage3_root_operation_ids=tuple(
                str(item["operation_id"]) for item in predecessor_operations
            ),
            refined_stage3_root_ids=tuple(
                str(item)
                for item in failed_attempt_lineage["replacement_allowlist"]
            ),
            copied_stage3_root_ids=tuple(
                str(item)
                for item in failed_attempt_lineage[
                    "preserved_operation_fingerprints"
                ]
            ),
            failed_attempt_evidence_refs=(
                *stage4_corrections.FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS,
                audit_ref,
                diagnosis_ref,
            ),
        )
    )
    experience_ref = _preflight_research_record_uri(
        "preflight-stage4-incomplete-experience",
        experience_record,
    )
    authorization_capture_ref = _preflight_research_record_uri(
        "preflight-human-authorization",
        authorization_capture,
    )
    door_decision_record = (
        stage4_corrections.compile_authorized_door_decision_record(
            authorization_ref=authorization_capture_ref,
            failed_attempt_experience_ref=experience_ref,
            failed_attempt_experience_digest=str(
                experience_record["record_digest"]
            ),
            door_dependency_diagnosis_ref=diagnosis_ref,
            selected_visual_manifest_ref=VISUAL_MANIFEST_REF.uri,
        )
    )
    door_decision_ref = _preflight_research_record_uri(
        "preflight-door-human-decision",
        door_decision_record,
    )
    return _compile_correction_provenance_gate(
        experience_record=experience_record,
        experience_ref=experience_ref,
        door_decision_record=door_decision_record,
        door_decision_ref=door_decision_ref,
        authorization_capture=authorization_capture,
        authorization_capture_ref=authorization_capture_ref,
    )


def preflight_stage4_candidate(
    predecessor_operations: Sequence[Mapping[str, object]],
    *,
    human_authorization_ref: str,
    visual_manifest: Mapping[str, object],
    failed_attempt_lineage: Mapping[str, object],
    authorization_capture: Mapping[str, object],
    predecessor_state: OperationalMarkovState,
) -> dict[str, object]:
    """Run every pure close gate before creating the fixed run ids."""

    authorization = _require_human_authorization_ref(human_authorization_ref)
    operations, lineage = compile_full_building_stage4_operations(
        predecessor_operations,
        column_source_refs=("preflight:evidence/columns",),
        entablature_source_refs=("preflight:evidence/entablature",),
        opening_source_refs=("preflight:evidence/openings",),
        visual_manifest_ref=VISUAL_MANIFEST_REF.uri,
        human_authorization_ref=authorization,
    )
    reference_variant_operations, _ = compile_full_building_stage4_operations(
        predecessor_operations,
        column_source_refs=(
            _preflight_research_record_uri(
                "preflight-column-source-a", {"variant": "column-a"}
            ),
            _preflight_research_record_uri(
                "preflight-column-source-b", {"variant": "column-b"}
            ),
        ),
        entablature_source_refs=(
            _preflight_research_record_uri(
                "preflight-entablature-source-a",
                {"variant": "entablature-a"},
            ),
            _preflight_research_record_uri(
                "preflight-entablature-source-b",
                {"variant": "entablature-b"},
            ),
        ),
        opening_source_refs=(
            _preflight_research_record_uri(
                "preflight-opening-source-a", {"variant": "opening-a"}
            ),
            _preflight_research_record_uri(
                "preflight-opening-source-b", {"variant": "opening-b"}
            ),
        ),
        visual_manifest_ref=VISUAL_MANIFEST_REF.uri,
        human_authorization_ref=_preflight_research_record_uri(
            "preflight-door-decision", {"variant": "door-decision"}
        ),
    )
    operation_execution_digest = _digest(
        _stage4_execution_projection(operations)
    )
    variant_execution_digest = _digest(
        _stage4_execution_projection(reference_variant_operations)
    )
    if operation_execution_digest != variant_execution_digest:
        raise ParthenonStage4Error(
            "Stage 4 executable IR improperly depends on evidence-record URIs"
        )
    relation_contracts, relation_compilation = (
        stage4_relation_contracts.compile_full_building_relation_contracts(
            operations
        )
    )
    if not relation_compilation["passed"]:
        raise ParthenonStage4Error(
            "Stage 4 preflight relation-contract compilation failed: "
            + "; ".join(str(item) for item in relation_compilation["failures"])
        )
    variant_relation_contracts, variant_relation_compilation = (
        stage4_relation_contracts.compile_full_building_relation_contracts(
            reference_variant_operations
        )
    )
    relation_contract_digest = _digest(list(relation_contracts))
    variant_relation_contract_digest = _digest(
        list(variant_relation_contracts)
    )
    if (
        not variant_relation_compilation["passed"]
        or relation_contract_digest != variant_relation_contract_digest
    ):
        raise ParthenonStage4Error(
            "Stage 4 relation contracts improperly depend on provenance refs"
        )
    preflight_program_digest = _digest(
        {
            "schema": "ParthenonStage4PreflightProgram@1",
            "operations": list(operations),
            "relation_contracts": list(relation_contracts),
        }
    )
    with tempfile.TemporaryDirectory(prefix="archflow-parthenon-stage4-") as directory:
        model_path = Path(directory) / "parthenon-stage-4-preflight.3dm"
        create_stage4_model(
            model_path,
            operations=operations,
            program_digest=preflight_program_digest,
        )
        inspection = inspect_three_dm(model_path)
        model_readback = {
            "schema": "ParthenonStage4PreflightReadback@1",
            "passed": (
                inspection.top_level_object_count == len(operations)
                and inspection.units.get("name") == "Meters"
            ),
            "failures": [],
        }
        if not model_readback["passed"]:
            model_readback["failures"].append(
                "preflight 3DM object denominator or metre units drifted"
            )
        receipts: dict[str, Mapping[str, object]] = {
            "model_readback": model_readback,
            "spatial": validate_stage4_spatial_model(
                model_path,
                operations=operations,
                program_digest=preflight_program_digest,
                predecessor_envelope=PREDECESSOR_BBOX,
            ),
            "detail": validate_stage4_model(
                model_path,
                operations=operations,
                program_digest=preflight_program_digest,
            ),
            "roof_eaves_pediment": (
                stage4_roof.validate_roof_eaves_pediment_operations(operations)
            ),
            "inner_colonnade": (
                stage4_inner_colonnade.validate_inner_colonnade_operations(
                    operations
                )
            ),
            "door_assembly": stage4_doors.validate_door_assembly_operations(
                operations,
                resolution=(
                    stage4_doors.DoorAssemblyResolution
                    .AUTHORIZED_CLOSED_DOUBLE_LEAF
                ),
                resolution_receipt=lineage["door_assembly_delta"],
            ),
            "successor_relations": (
                stage4_relations.validate_stage4_successor_relations(
                    operations,
                    model_path=model_path,
                    typed_contact_allowlist=(),
                    typed_relation_contracts=relation_contracts,
                )
            ),
            "material_bindings": validate_stage4_material_bindings(
                model_path,
                operations=operations,
            ),
            "evidence": compile_stage4_evidence_gate(
                visual_manifest=visual_manifest,
            ),
            "correction_provenance": (
                _compile_correction_semantics_preflight(
                    predecessor_operations=predecessor_operations,
                    failed_attempt_lineage=failed_attempt_lineage,
                    authorization_capture=authorization_capture,
                )
            ),
        }
        coverage = compile_stage4_component_coverage(
            predecessor_operations,
            operations,
            lineage=lineage,
            lineage_ref="preflight:stage4/program",
            evidence_refs=("preflight:stage4/evidence",),
            relational_revalidation_refs=("preflight:stage4/relations",),
        )
        receipts["component_coverage"] = coverage.to_dict()
        preflight_model_sha256 = inspection.file_sha256
    failures = [
        f"{name}: " + "; ".join(str(item) for item in receipt.get("failures", ()))
        for name, receipt in receipts.items()
        if not bool(receipt.get("passed", receipt.get("status") == "PASS"))
    ]
    if failures:
        raise ParthenonStage4Error(
            "Stage 4 fixed-run preflight failed before persistence: "
            + " | ".join(failures)
        )
    preflight_run = RunRef(
        PROJECT_ID,
        RUN_ID,
        predecessor_state.branch.run.base,
    )
    synthetic_refs = {
        name: _preflight_reconstruction_record_ref(
            f"preflight-{name.replace('_', '-')}",
            {"gate": name, "receipt_digest": _digest(receipt)},
        )
        for name, receipt in sorted(receipts.items())
    }
    binding_ref = _preflight_reconstruction_record_ref(
        "preflight-predecessor-binding",
        {"predecessor_state_digest": predecessor_state.state_digest},
    )
    opened_state, _ = rebind_predecessor_state(
        predecessor_state,
        preflight_run,
        binding_ref=binding_ref.uri,
    )
    close_gate_refs = {
        name: synthetic_refs[name].uri for name in STAGE4_CLOSE_GATE_NAMES
    }
    preclose_gate = compile_stage4_close_gate(
        gate_receipts=receipts,
        gate_refs=close_gate_refs,
    )
    if not preclose_gate["passed"]:
        raise ParthenonStage4Error(
            "Stage 4 preflight close-gate compilation failed: "
            + "; ".join(str(item) for item in preclose_gate["failures"])
        )
    closed_state, convergence = close_stage4_state(
        opened_state,
        gate_receipts=receipts,
        gate_refs=close_gate_refs,
    )
    if not convergence.stage_ready:
        raise ParthenonStage4Error(
            "Stage 4 preflight convergence did not become stage-ready"
        )
    pack_refs = {
        name: _preflight_reconstruction_record_ref(
            f"preflight-pack-{name.replace('_', '-')}",
            {"binding": name},
        )
        for name in (
            "branch_selection",
            "branch_scope",
            "decision_universe",
            "basis_index",
            "dependency_ledger",
            "evidence_sufficiency",
            "design_state",
            "geometry_program",
            "model_inspection",
            "stage_gate",
            "stage_convergence",
            "review",
            "contract",
        )
    }
    pack_refs["branch_selection"] = _preflight_run_record_ref(
        "preflight-pack-branch-selection",
        {"binding": "branch_selection"},
    )
    pack_refs["review"] = _preflight_review_record_ref(
        "preflight-pack-review",
        {"binding": "review"},
    )
    pack_refs["contract"] = _preflight_run_record_ref(
        "preflight-pack-contract",
        {"binding": "contract"},
    )
    preflight_model_artifact = ProjectArtifactRef(
        project_id=PROJECT_ID,
        artifact_id="parthenon-stage-4-preflight-3dm",
        relative_path=(
            f"objects/sha256/{preflight_model_sha256[:2]}/"
            f"{preflight_model_sha256}"
        ),
        sha256=preflight_model_sha256,
        media_type="model/vnd.3dm",
    )
    cross_run_predecessor = CrossRunStagePackPredecessor(
        stage_id="stage-3",
        stage_index=3,
        branch=predecessor_state.branch,
        pack_ref=PREDECESSOR_PACK_REF,
        program_digest=PREDECESSOR_PROGRAM_DIGEST,
    )
    preflight_pack = compile_stage_evidence_pack(
        project_id=PROJECT_ID,
        run=preflight_run,
        branch=closed_state.branch,
        scope_ref=pack_refs["branch_scope"],
        stage_id="stage-4",
        stage_index=4,
        revision=1,
        program_digest=preflight_program_digest,
        contract_ref=pack_refs["contract"],
        bindings=(
            StageEvidenceBinding(
                StageEvidenceRole.BRANCH_SELECTION,
                pack_refs["branch_selection"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.BRANCH_SCOPE,
                pack_refs["branch_scope"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.DECISION_UNIVERSE,
                pack_refs["decision_universe"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.BASIS_INDEX,
                pack_refs["basis_index"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.DEPENDENCY_LEDGER,
                pack_refs["dependency_ledger"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.EVIDENCE_SUFFICIENCY,
                pack_refs["evidence_sufficiency"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.DESIGN_STATE,
                pack_refs["design_state"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.GEOMETRY_PROGRAM,
                pack_refs["geometry_program"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.MODEL_INSPECTION,
                pack_refs["model_inspection"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.STAGE_GATE,
                pack_refs["stage_gate"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.STAGE_CONVERGENCE,
                pack_refs["stage_convergence"],
            ),
            StageEvidenceBinding(
                StageEvidenceRole.REVIEW,
                pack_refs["review"],
            ),
        ),
        artifacts=(
            StageArtifactBinding(
                role=StageArtifactRole.CAD_MODEL,
                ref=preflight_model_artifact,
                program_digest=preflight_program_digest,
            ),
        ),
        gaps=(),
        closure=StageClosureSummary(
            evidence_sufficient=True,
            dependencies_closed=True,
            hard_gates_passed=True,
            stage_ready=True,
            model_artifact_current=True,
        ),
        predecessor=cross_run_predecessor,
    )
    if preflight_pack.compilation_status is not StagePackCompilationStatus.COMPLETE:
        raise ParthenonStage4Error(
            "Stage 4 preflight evidence pack did not compile COMPLETE"
        )
    return {
        "schema": "ParthenonStage4PreflightReceipt@1",
        "passed": True,
        "operation_count": len(operations),
        "operation_execution_digest": operation_execution_digest,
        "relation_contract_count": len(relation_contracts),
        "relation_contract_digest": relation_contract_digest,
        "relation_contract_compilation": relation_compilation,
        "reference_invariance": {
            "passed": True,
            "operation_execution_digest": operation_execution_digest,
            "variant_operation_execution_digest": variant_execution_digest,
            "relation_contract_digest": relation_contract_digest,
            "variant_relation_contract_digest": (
                variant_relation_contract_digest
            ),
        },
        "preflight_gate_receipt_snapshot_digests": {
            name: _digest(receipt) for name, receipt in sorted(receipts.items())
        },
        "gate_names": sorted(receipts),
        "closure_preflight": {
            "preclose_gate_digest": _digest(preclose_gate),
            "closed_state_digest": closed_state.state_digest,
            "convergence_digest": _digest(convergence.to_dict()),
            "stage_ready": convergence.stage_ready,
            "pack_status": preflight_pack.compilation_status.value,
            "pack_digest": preflight_pack.pack_digest,
        },
        "canonical_write_authority": False,
    }


def verify_stage4_persisted_run_chain(
    repository: FilesystemProjectRepository,
    *,
    record_refs: Mapping[str, ProjectRecordRef],
    progress_binding_fields: Mapping[str, str],
    artifact_refs: Mapping[str, ProjectArtifactRef],
    workspace_artifact_paths: Mapping[str, Path],
) -> dict[str, object]:
    """Read every retained Stage 4 output through its typed P036 reference."""

    failures: list[str] = []
    payloads: dict[str, dict[str, object]] = {}
    for name, ref in sorted(record_refs.items()):
        try:
            payloads[name] = repository.load_json(ref)
        except Exception as exc:  # pragma: no cover - corruption boundary
            failures.append(f"record {name} failed typed readback: {exc}")
    required_payloads = {
        "progress",
        "program",
        "pack",
        "relation_contract_set",
        "fixed_run_preflight",
        "successor_relations",
        "aggregate_gate",
        "final_state",
    }
    missing_payloads = sorted(required_payloads - set(payloads))
    if missing_payloads:
        failures.append(
            "run-chain readback lacks required payloads: "
            + ", ".join(missing_payloads)
        )
    progress = payloads.get("progress", {})
    for record_name, progress_field in sorted(progress_binding_fields.items()):
        ref = record_refs.get(record_name)
        if ref is None:
            failures.append(f"progress binding has no supplied ref: {record_name}")
            continue
        if progress.get(progress_field) != _record_dict(ref):
            failures.append(
                f"progress field {progress_field} does not bind exact {record_name} ref"
            )

    program = payloads.get("program", {})
    relation_set = payloads.get("relation_contract_set", {})
    preflight = payloads.get("fixed_run_preflight", {})
    successor_relations = payloads.get("successor_relations", {})
    aggregate_gate = payloads.get("aggregate_gate", {})
    if program.get("program_digest") != progress.get("program_digest"):
        failures.append("progress/program digest cross-binding failed")
    operations = program.get("operations")
    if not isinstance(operations, list) or len(operations) != 687:
        failures.append("persisted program does not retain the exact 687 operations")
    else:
        execution_digest = _digest(_stage4_execution_projection(operations))
        if execution_digest != progress.get("operation_execution_digest"):
            failures.append("program/progress execution projection digest mismatch")
        if execution_digest != relation_set.get("operation_execution_digest"):
            failures.append("program/relation-set execution projection digest mismatch")
        if execution_digest != preflight.get("operation_execution_digest"):
            failures.append("program/preflight execution projection digest mismatch")
    contracts = relation_set.get("contracts")
    contract_digest = _digest(contracts) if isinstance(contracts, list) else None
    if (
        not isinstance(contracts, list)
        or len(contracts) != int(relation_set.get("contract_count", -1))
        or len(contracts) != int(progress.get("relation_contract_count", -1))
    ):
        failures.append("relation contract denominator is not cross-bound")
    if contract_digest != relation_set.get("contract_digest"):
        failures.append("relation contract payload digest mismatch")
    if contract_digest != progress.get("relation_contract_digest"):
        failures.append("progress/relation contract digest mismatch")
    if contract_digest != preflight.get("relation_contract_digest"):
        failures.append("preflight/relation contract digest mismatch")
    if successor_relations.get("passed") is not True:
        failures.append("persisted successor-relation gate is not passed")
    if successor_relations.get("relation_contract_digest") != contract_digest:
        failures.append("successor-relation gate lost the exact contract digest")
    if aggregate_gate.get("passed") is not True:
        failures.append("persisted aggregate Stage 4 gate is not passed")

    pack_payload = payloads.get("pack")
    if isinstance(pack_payload, Mapping):
        try:
            loaded_pack = StageEvidencePack.from_dict(pack_payload)
        except Exception as exc:
            failures.append(f"Stage 4 evidence pack typed reload failed: {exc}")
        else:
            if loaded_pack.compilation_status is not StagePackCompilationStatus.COMPLETE:
                failures.append("reloaded Stage 4 evidence pack is not COMPLETE")
            if loaded_pack.pack_digest != progress.get("pack_digest"):
                failures.append("progress/pack digest cross-binding failed")
            if loaded_pack.program_digest != program.get("program_digest"):
                failures.append("pack/program digest cross-binding failed")
    final_state_payload = payloads.get("final_state")
    if isinstance(final_state_payload, Mapping):
        try:
            loaded_state = OperationalMarkovState.from_dict(final_state_payload)
        except Exception as exc:
            failures.append(f"Stage 4 final state typed reload failed: {exc}")
        else:
            close_obligation = tuple(
                item
                for item in loaded_state.obligations
                if item.obligation_id == "close-stage-4"
            )
            if (
                len(close_obligation) != 1
                or close_obligation[0].status is not ObligationStatus.SATISFIED
            ):
                failures.append("reloaded Stage 4 state is not closed")

    artifact_sha256: dict[str, str] = {}
    for name, ref in sorted(artifact_refs.items()):
        object_path = (repository.layout.root / ref.relative_path).resolve(
            strict=False
        )
        try:
            data = object_path.read_bytes()
        except OSError as exc:
            failures.append(f"artifact {name} failed object-store readback: {exc}")
            continue
        digest = _sha_bytes(data)
        artifact_sha256[name] = digest
        if digest != ref.sha256:
            failures.append(f"artifact {name} object-store digest mismatch")
        workspace_path = workspace_artifact_paths.get(name)
        if workspace_path is None:
            failures.append(f"artifact {name} lacks workspace readback path")
            continue
        try:
            workspace_digest = _sha_bytes(workspace_path.read_bytes())
        except OSError as exc:
            failures.append(f"artifact {name} failed workspace readback: {exc}")
            continue
        if workspace_digest != ref.sha256:
            failures.append(f"artifact {name} workspace/object digest mismatch")

    model_path = workspace_artifact_paths.get("model")
    if model_path is not None:
        model = rhino3dm.File3dm.Read(str(model_path))
        if model is None:
            failures.append("retained Stage 4 3DM cannot be read back")
        else:
            if len(primary_three_dm_objects(model)) != 687:
                failures.append("retained Stage 4 3DM object denominator drifted")
            if model.Settings.ModelUnitSystem != rhino3dm.UnitSystem.Meters:
                failures.append("retained Stage 4 3DM is not metre-based")
            if model.Strings["archflow:program_digest"] != program.get(
                "program_digest"
            ):
                failures.append("retained Stage 4 3DM lost program identity")

    if failures:
        raise ParthenonStage4Error(
            "Stage 4 persisted run-chain verification failed: "
            + "; ".join(failures)
        )
    return {
        "schema": "ParthenonStage4PersistedRunChainVerification@1",
        "status": "PASSED",
        "passed": True,
        "checked_record_count": len(record_refs),
        "checked_record_refs": {
            name: _record_dict(ref) for name, ref in sorted(record_refs.items())
        },
        "checked_artifact_count": len(artifact_refs),
        "artifact_sha256": artifact_sha256,
        "progress_ref": _record_dict(record_refs["progress"]),
        "program_digest": program["program_digest"],
        "operation_count": len(operations),
        "operation_execution_digest": progress["operation_execution_digest"],
        "relation_contract_count": len(contracts),
        "relation_contract_digest": contract_digest,
        "pack_digest": progress["pack_digest"],
        "canonical_write_authority": False,
        "failures": [],
    }


def run_project(
    root: Path,
    *,
    captured_at: str,
    human_authorization_ref: str,
    human_authorization_statement: str,
) -> dict[str, object]:
    """Create research-007/reconstruction-006 after every exact input gate."""

    started = time.perf_counter()
    captured_at = _normalize_captured_at(captured_at)
    human_authorization_ref = _require_human_authorization_ref(
        human_authorization_ref
    )
    authorization_capture = _compile_human_authorization_capture(
        authorization_ref=human_authorization_ref,
        statement=human_authorization_statement,
        captured_at=captured_at,
    )
    repository = FilesystemProjectRepository.open(Path(root))
    predecessor = _load_exact_predecessors(repository)
    failed_attempt = _load_exact_incomplete_stage4_attempt(repository)
    if not set(REQUIRED_STAGE4_SELECTED_ROIS).issubset(
        set(predecessor["selected_visual_ids"])
    ):
        raise ParthenonStage4Error(
            "one or more required Stage 4 ROIs are not selected in the exact visual manifest"
        )
    head_before = repository.read_head()
    for run_id in (RESEARCH_RUN_ID, RUN_ID):
        if repository.layout.run(run_id).root.exists():
            raise FileExistsError(
                f"P087 refuses to overwrite or infer resume state for existing run: {run_id}"
            )
    guard_open_asset_candidates(OPEN_ASSET_CANDIDATES)
    preflight_receipt = preflight_stage4_candidate(
        predecessor["predecessor_operations"],
        human_authorization_ref=human_authorization_ref,
        visual_manifest=predecessor["visual_manifest"],
        failed_attempt_lineage=failed_attempt["lineage"],
        authorization_capture=authorization_capture,
        predecessor_state=predecessor["predecessor_state"],
    )

    research_run, run = repository.create_run_batch(
        (RESEARCH_RUN_ID, RUN_ID),
        base=head_before,
        require_current_base=True,
    )
    research_branch_destination = _branch_destination(RESEARCH_RUN_ID)
    source_record_refs: dict[str, ProjectRecordRef] = {}
    for source in TEXT_SOURCES:
        source_id = str(source["source_id"])
        source_record_refs[source_id] = repository.put_json(
            run=research_run,
            destination=research_branch_destination,
            record_kind=f"stage-4-text-source-{source_id}",
            payload={
                "schema": "ParthenonStage4TextSource@1",
                "project_id": PROJECT_ID,
                "run_id": RESEARCH_RUN_ID,
                "branch_id": BRANCH_ID,
                "captured_at": captured_at,
                **source,
                "claim_ids": [
                    str(claim["claim_id"])
                    for claim in EVIDENCE_CLAIMS
                    if source_id in claim["source_ids"]
                ],
                "downloaded_binary": False,
                "canonical_write_authority": False,
            },
        )

    research_manifest_ref = repository.put_json(
        run=research_run,
        destination=research_branch_destination,
        record_kind="stage-4-research-manifest",
        payload={
            "schema": "ParthenonStage4ResearchManifest@1",
            "project_id": PROJECT_ID,
            "run_id": RESEARCH_RUN_ID,
            "branch_id": BRANCH_ID,
            "captured_at": captured_at,
            "source_records": {
                key: _record_dict(value) for key, value in sorted(source_record_refs.items())
            },
            "claims": [
                {**claim, "source_record_refs": [_record_dict(source_record_refs[item]) for item in claim["source_ids"]]}
                for claim in EVIDENCE_CLAIMS
            ],
            "visual_manifest_ref": _record_dict(VISUAL_MANIFEST_REF),
            "visual_claim_limit": "existence_morphology_topology_relative_position_only",
            "visual_exact_dimension_authority": False,
            "asset_candidates": list(OPEN_ASSET_CANDIDATES),
            "procedural_geometry_selected": True,
            "canonical_write_authority": False,
        },
    )
    asset_document = render_asset_provenance_document(captured_at=captured_at).encode("utf-8")
    asset_document_path = _write_workspace_bytes(
        repository,
        run=research_run,
        relative_within_workspace=Path("asset-rag") / "_外部资产来源清单.md",
        data=asset_document,
    )
    asset_document_artifact = repository.ingest(
        run=research_run,
        destination=PersistenceDestination(PersistenceArea.OBJECT),
        artifact_id="parthenon-stage-4-asset-provenance-document",
        media_type="text/markdown",
        source=io.BytesIO(asset_document),
    )
    asset_provenance_ref = repository.put_json(
        run=research_run,
        destination=research_branch_destination,
        record_kind="stage-4-asset-provenance",
        payload={
            "schema": "ParthenonStage4AssetProvenance@1",
            "captured_at": captured_at,
            "workspace_relative_path": ASSET_DOCUMENT_WORKSPACE_RELATIVE_PATH,
            "workspace_sha256": _sha_bytes(asset_document),
            "artifact_ref": _artifact_dict(asset_document_artifact),
            "candidates": list(OPEN_ASSET_CANDIDATES),
            "procedural_geometry_selected": True,
            "canonical_write_authority": False,
        },
    )

    predecessor_state = predecessor["predecessor_state"]
    assert isinstance(predecessor_state, OperationalMarkovState)
    predecessor_pack = predecessor["predecessor_pack"]
    assert isinstance(predecessor_pack, StageEvidencePack)
    predecessor_model_artifact = predecessor["predecessor_model_artifact"]
    assert isinstance(predecessor_model_artifact, ProjectArtifactRef)
    predecessor_inspection = predecessor["predecessor_model_inspection"]
    binding_payload = {
        "schema": "ParthenonStage4PredecessorBinding@1",
        "project_id": PROJECT_ID,
        "successor_run_id": RUN_ID,
        "canonical_base": {
            "version": CANONICAL_VERSION,
            "state_sha256": CANONICAL_STATE_SHA256,
        },
        "predecessor_run_id": PREDECESSOR_RUN_ID,
        "predecessor_branch": {
            "branch_id": BRANCH_ID,
            "epoch": predecessor_state.branch.epoch,
        },
        "progress_ref": _record_dict(PREDECESSOR_PROGRESS_REF),
        "state_ref": _record_dict(PREDECESSOR_STATE_REF),
        "state_digest": predecessor_state.state_digest,
        "state_counts": {
            "facts": len(predecessor_state.facts),
            "locks": len(predecessor_state.locks),
            "obligations": len(predecessor_state.obligations),
            "satisfied_obligations": sum(item.status is ObligationStatus.SATISFIED for item in predecessor_state.obligations),
            "evidence_refs": len(predecessor_state.evidence_refs),
        },
        "facts_canonical_sha256": _digest([item.to_dict() for item in predecessor_state.facts]),
        "locks_canonical_sha256": _digest([item.to_dict() for item in predecessor_state.locks]),
        "evidence_refs_canonical_sha256": _digest(list(predecessor_state.evidence_refs)),
        "pack_ref": _record_dict(PREDECESSOR_PACK_REF),
        "pack_status": predecessor_pack.compilation_status.value,
        "pack_closed": predecessor_pack.closure.closed,
        "program_ref": _record_dict(PREDECESSOR_PROGRAM_REF),
        "program_digest": PREDECESSOR_PROGRAM_DIGEST,
        "program_operation_count": len(predecessor["predecessor_operations"]),
        "model_artifact_ref": _artifact_dict(predecessor_model_artifact),
        "model_workspace_relative_path": PREDECESSOR_MODEL_RELATIVE_PATH,
        "model_sha256": PREDECESSOR_MODEL_SHA256,
        "model_readback": {
            "units": predecessor_inspection.units,
            "object_count": predecessor_inspection.top_level_object_count,
            "aggregate_bbox": predecessor_inspection.aggregate_bbox,
            "coordinate_system": "RhinoWorldXY_ZUp",
            "up_axis": "Z",
        },
        "inspection_ref": _record_dict(PREDECESSOR_INSPECTION_REF),
        "spatial_ref": _record_dict(PREDECESSOR_SPATIAL_REF),
        "spatial_status": predecessor["predecessor_spatial"]["status"],
        "visual_manifest_ref": _record_dict(VISUAL_MANIFEST_REF),
        "visual_partition": {"selected": 14, "parked": 13, "rejected": 5},
        "facts_unchanged_required": True,
        "locks_unchanged_required": True,
        "evidence_refs_unchanged_at_open_required": True,
        "canonical_write_authority": False,
    }
    predecessor_binding_ref = _put_branch_json(
        repository,
        run,
        "stage-4-predecessor-binding",
        binding_payload,
    )
    opened_state, rebind_receipt = rebind_predecessor_state(
        predecessor_state,
        run,
        binding_ref=predecessor_binding_ref.uri,
    )
    if not all(
        rebind_receipt[key]
        for key in ("facts_unchanged", "locks_unchanged", "evidence_refs_unchanged")
    ):
        raise ParthenonStage4Error("Stage 4 open state changed inherited canonical arrays")
    opened_state_ref = _put_branch_json(
        repository,
        run,
        "stage-4-open-operational-state",
        opened_state.to_dict(),
    )
    state_rebind_ref = _put_branch_json(
        repository,
        run,
        "stage-4-state-rebind",
        rebind_receipt,
    )

    source_uri = {key: value.uri for key, value in source_record_refs.items()}

    failed_operations = failed_attempt["operations"]
    failed_lineage = failed_attempt["lineage"]
    assert isinstance(failed_operations, tuple)
    assert isinstance(failed_lineage, Mapping)
    failed_door_diagnosis = stage4_doors.measure_door_dependency_state(
        failed_operations
    )
    failed_door_diagnosis_ref = _put_branch_json(
        repository,
        research_run,
        "stage-4-principal-door-dependency-diagnosis",
        {
            **failed_door_diagnosis,
            "failed_attempt_run_id": stage4_corrections.FAILED_ATTEMPT_RUN_ID,
            "failed_attempt_program_ref": _record_dict(FAILED_STAGE4_PROGRAM_REF),
            "failed_attempt_model_sha256": (
                stage4_corrections.FAILED_STAGE4_MODEL_SHA256
            ),
            "canonical_write_authority": False,
        },
    )
    failed_attempt_audit = {
        "schema": "ParthenonStage4FailedAttemptMachineAudit@1",
        "project_id": PROJECT_ID,
        "run_id": RESEARCH_RUN_ID,
        "branch_id": BRANCH_ID,
        "failed_attempt_run_id": stage4_corrections.FAILED_ATTEMPT_RUN_ID,
        "failed_attempt_role": "FAILED_ATTEMPT_EVIDENCE_ONLY",
        "program_ref": _record_dict(FAILED_STAGE4_PROGRAM_REF),
        "program_digest": stage4_corrections.FAILED_STAGE4_PROGRAM_DIGEST,
        "operation_count": len(failed_operations),
        "model_workspace_relative_path": FAILED_STAGE4_MODEL_RELATIVE_PATH,
        "model_sha256": stage4_corrections.FAILED_STAGE4_MODEL_SHA256,
        "model_object_count": failed_attempt["inspection"].top_level_object_count,
        "door_dependency_diagnosis_ref": _record_dict(
            failed_door_diagnosis_ref
        ),
        "roof_eaves_pediment": (
            stage4_roof.validate_roof_eaves_pediment_operations(failed_operations)
        ),
        "inner_colonnade": (
            stage4_inner_colonnade.validate_inner_colonnade_operations(
                failed_operations
            )
        ),
        "material_bindings": validate_stage4_material_bindings(
            failed_attempt["model_path"],
            operations=failed_operations,
        ),
        "successor_relations": (
            stage4_relations.validate_stage4_successor_relations(
                failed_operations,
                model_path=failed_attempt["model_path"],
                typed_contact_allowlist=(),
                typed_relation_contracts=(),
            )
        ),
        "eligible_as_stage_predecessor": False,
        "canonical_write_authority": False,
    }
    failed_attempt_audit_ref = _put_branch_json(
        repository,
        research_run,
        "stage-4-failed-attempt-machine-audit",
        failed_attempt_audit,
    )
    experience_record = (
        stage4_corrections.compile_incomplete_stage4_experience_record(
            stage3_root_operation_ids=tuple(
                str(item["operation_id"])
                for item in predecessor["predecessor_operations"]
            ),
            refined_stage3_root_ids=tuple(
                str(item) for item in failed_lineage["replacement_allowlist"]
            ),
            copied_stage3_root_ids=tuple(
                str(item)
                for item in failed_lineage["preserved_operation_fingerprints"]
            ),
            failed_attempt_evidence_refs=(
                *stage4_corrections.FAILED_ATTEMPT_REQUIRED_EVIDENCE_REFS,
                failed_attempt_audit_ref.uri,
                failed_door_diagnosis_ref.uri,
            ),
        )
    )
    experience_ref = _put_branch_json(
        repository,
        research_run,
        "stage4-incomplete-experience",
        experience_record,
    )
    authorization_capture_ref = _put_branch_json(
        repository,
        research_run,
        "stage-4-human-authorization-capture",
        authorization_capture,
    )
    preflight_ref = _put_branch_json(
        repository,
        run,
        "stage-4-fixed-run-preflight",
        {
            **preflight_receipt,
            "authorization_capture_ref": _record_dict(
                authorization_capture_ref
            ),
            "predecessor_program_ref": _record_dict(PREDECESSOR_PROGRAM_REF),
            "predecessor_model_sha256": PREDECESSOR_MODEL_SHA256,
        },
    )
    door_decision_record = stage4_corrections.compile_authorized_door_decision_record(
        authorization_ref=authorization_capture_ref.uri,
        failed_attempt_experience_ref=experience_ref.uri,
        failed_attempt_experience_digest=str(experience_record["record_digest"]),
        door_dependency_diagnosis_ref=failed_door_diagnosis_ref.uri,
        selected_visual_manifest_ref=VISUAL_MANIFEST_REF.uri,
    )
    door_decision_ref = _put_branch_json(
        repository,
        research_run,
        "stage-4-door-human-decision",
        door_decision_record,
    )
    correction_document = stage4_corrections.render_stage4_correction_report(
        experience_record,
        door_decision_record,
    ).encode("utf-8")
    correction_document_path = _write_workspace_bytes(
        repository,
        run=research_run,
        relative_within_workspace=(
            Path("asset-rag") / "_Stage4纠错与人工授权.md"
        ),
        data=correction_document,
    )
    correction_document_artifact = repository.ingest(
        run=research_run,
        destination=PersistenceDestination(PersistenceArea.OBJECT),
        artifact_id="parthenon-stage-4-correction-report",
        media_type="text/markdown",
        source=io.BytesIO(correction_document),
    )
    correction_document_ref = _put_branch_json(
        repository,
        research_run,
        "stage-4-correction-report",
        {
            "schema": "ParthenonStage4CorrectionReport@1",
            "workspace_relative_path": CORRECTION_DOCUMENT_WORKSPACE_RELATIVE_PATH,
            "workspace_sha256": _sha_bytes(correction_document),
            "artifact_ref": _artifact_dict(correction_document_artifact),
            "experience_ref": _record_dict(experience_ref),
            "door_decision_ref": _record_dict(door_decision_ref),
            "canonical_write_authority": False,
        },
    )
    correction_provenance = _compile_correction_provenance_gate(
        experience_record=experience_record,
        experience_ref=experience_ref.uri,
        door_decision_record=door_decision_record,
        door_decision_ref=door_decision_ref.uri,
        authorization_capture=authorization_capture,
        authorization_capture_ref=authorization_capture_ref.uri,
    )
    correction_provenance_ref = _put_branch_json(
        repository,
        run,
        "stage-4-correction-provenance-gate",
        correction_provenance,
    )
    correction_manifest_ref = _put_branch_json(
        repository,
        research_run,
        "stage-4-correction-manifest",
        {
            "schema": "ParthenonStage4CorrectionManifest@1",
            "project_id": PROJECT_ID,
            "run_id": RESEARCH_RUN_ID,
            "consumer_run_id": RUN_ID,
            "branch_id": BRANCH_ID,
            "research_manifest_ref": _record_dict(research_manifest_ref),
            "asset_provenance_ref": _record_dict(asset_provenance_ref),
            "failed_attempt_audit_ref": _record_dict(failed_attempt_audit_ref),
            "experience_ref": _record_dict(experience_ref),
            "door_dependency_diagnosis_ref": _record_dict(
                failed_door_diagnosis_ref
            ),
            "authorization_capture_ref": _record_dict(
                authorization_capture_ref
            ),
            "door_decision_ref": _record_dict(door_decision_ref),
            "correction_report_ref": _record_dict(correction_document_ref),
            "correction_provenance_ref": _record_dict(
                correction_provenance_ref
            ),
            "fixed_run_preflight_ref": _record_dict(preflight_ref),
            "only_stage_predecessor_run_id": PREDECESSOR_RUN_ID,
            "failed_attempt_role": "FAILED_ATTEMPT_EVIDENCE_ONLY",
            "canonical_write_authority": False,
        },
    )
    operations, lineage = compile_full_building_stage4_operations(
        predecessor["predecessor_operations"],
        column_source_refs=(
            source_uri["perseus-parthenon"],
            source_uri["penrose-1888"],
            source_uri["bommelaer-doric"],
            source_uri["ysma-doric-morphology"],
        ),
        entablature_source_refs=(
            source_uri["penrose-1888"],
            source_uri["perseus-parthenon"],
            source_uri["ysma-doric-morphology"],
            source_uri["acropolis-museum-entablature"],
        ),
        opening_source_refs=(
            source_uri["stevens-hs3"],
            source_uri["bsa-east-windows-2025"],
        ),
        visual_manifest_ref=VISUAL_MANIFEST_REF.uri,
        human_authorization_ref=door_decision_ref.uri,
    )
    relation_contracts, relation_contract_compilation = (
        stage4_relation_contracts.compile_full_building_relation_contracts(
            operations
        )
    )
    if not relation_contract_compilation["passed"]:
        raise ParthenonStage4Error(
            "Stage 4 exact relation-contract compilation failed after preflight: "
            + "; ".join(
                str(item)
                for item in relation_contract_compilation["failures"]
            )
        )
    operation_execution_digest = _digest(
        _stage4_execution_projection(operations)
    )
    relation_contract_digest = _digest(list(relation_contracts))
    if operation_execution_digest != preflight_receipt.get(
        "operation_execution_digest"
    ):
        raise ParthenonStage4Error(
            "persisted-ref Stage 4 executable IR drifted from fixed-run preflight"
        )
    if relation_contract_digest != preflight_receipt.get(
        "relation_contract_digest"
    ):
        raise ParthenonStage4Error(
            "persisted-ref Stage 4 relation contracts drifted from fixed-run preflight"
        )
    relation_contract_set_ref = _put_branch_json(
        repository,
        run,
        "stage-4-relation-contract-set",
        {
            "schema": "ParthenonStage4RelationContractSet@1",
            "project_id": PROJECT_ID,
            "run_id": RUN_ID,
            "branch_id": BRANCH_ID,
            "operation_count": len(operations),
            "contract_count": len(relation_contracts),
            "contract_digest": relation_contract_digest,
            "operation_execution_digest": operation_execution_digest,
            "contracts": list(relation_contracts),
            "compilation": relation_contract_compilation,
            "fixed_run_preflight_ref": _record_dict(preflight_ref),
            "canonical_write_authority": False,
        },
    )
    program_identity = {
        "schema": "ParthenonStage4ArchitecturalIR@1",
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "stage": 4,
        "branch_id": BRANCH_ID,
        "branch_epoch": opened_state.branch.epoch,
        "canonical_base": {
            "version": CANONICAL_VERSION,
            "state_sha256": CANONICAL_STATE_SHA256,
        },
        "input_design_state_digest": opened_state.state_digest,
        "predecessor_binding_ref": _record_dict(predecessor_binding_ref),
        "predecessor_program_ref": _record_dict(PREDECESSOR_PROGRAM_REF),
        "predecessor_program_digest": PREDECESSOR_PROGRAM_DIGEST,
        "predecessor_model_sha256": PREDECESSOR_MODEL_SHA256,
        "visual_manifest_ref": _record_dict(VISUAL_MANIFEST_REF),
        "human_authorization_ref": door_decision_ref.uri,
        "human_authorization_capture_ref": _record_dict(
            authorization_capture_ref
        ),
        "failed_attempt_experience_ref": _record_dict(experience_ref),
        "correction_manifest_ref": _record_dict(correction_manifest_ref),
        "correction_provenance_ref": _record_dict(
            correction_provenance_ref
        ),
        "fixed_run_preflight_ref": _record_dict(preflight_ref),
        "relation_contract_set_ref": _record_dict(
            relation_contract_set_ref
        ),
        "relation_contract_digest": relation_contract_digest,
        "relation_contract_count": len(relation_contracts),
        "operation_execution_digest": operation_execution_digest,
        "research_manifest_ref": _record_dict(research_manifest_ref),
        "length_unit": "meter",
        "coordinate_system": {
            "schema": "CartesianCoordinateSystem@1",
            "target": "RhinoWorldXY",
            "plan_axes": ["X", "Y"],
            "up_axis": "Z",
            "longitudinal_axis": "Y",
            "handedness": "right",
            "origin": "crepidoma-base-center",
        },
        "operations": list(operations),
        "lineage": lineage,
        "canonical_write_authority": False,
    }
    program_digest = _digest(program_identity)
    program_ref = _put_branch_json(
        repository,
        run,
        "stage-4-geometry-program",
        {**program_identity, "program_digest": program_digest},
    )

    workspace_root = repository.layout.run(RUN_ID).workspaces.resolve(strict=False)
    model_path = (
        workspace_root / "cad-stage-4" / "parthenon-stage-4-detail-candidate.3dm"
    ).resolve(strict=False)
    if not model_path.is_relative_to(workspace_root):
        raise ParthenonStage4Error("CAD output escaped its assigned P036 workspace")
    if model_path.exists():
        raise FileExistsError(f"immutable Stage 4 model target already exists: {model_path}")
    create_stage4_model(model_path, operations=operations, program_digest=program_digest)
    model_bytes = model_path.read_bytes()
    model_artifact = repository.ingest(
        run=run,
        destination=PersistenceDestination(PersistenceArea.OBJECT),
        artifact_id="parthenon-stage-4-detail-3dm",
        media_type="model/vnd.3dm",
        source=io.BytesIO(model_bytes),
    )
    if model_artifact.sha256 != _sha_bytes(model_bytes):
        raise ParthenonStage4Error("Stage 4 workspace/object digest mismatch")
    inspection = inspect_three_dm(model_path)
    inspected_model = rhino3dm.File3dm.Read(str(model_path))
    model_readback_failures: list[str] = []
    if inspection.file_sha256 != model_artifact.sha256:
        model_readback_failures.append("inspection digest differs from retained CAD artifact")
    if inspection.top_level_object_count != len(operations):
        model_readback_failures.append("inspection object count differs from Stage 4 program")
    if inspection.units.get("name") != "Meters":
        model_readback_failures.append("inspection model units are not Meters")
    if inspected_model is None:
        model_readback_failures.append("rhino3dm could not read back the retained CAD artifact")
    elif (
        inspected_model.Strings["archflow:program_digest"] != program_digest
        or inspected_model.Strings["archflow:coordinate_system"] != "RhinoWorldXY_ZUp"
        or inspected_model.Strings["archflow:up_axis"] != "Z"
    ):
        model_readback_failures.append("model document identity/coordinate metadata drifted")
    model_readback_gate = {
        "schema": "ParthenonStage4ModelReadbackGate@1",
        "status": "PASSED" if not model_readback_failures else "FAILED",
        "passed": not model_readback_failures,
        "file_sha256": inspection.file_sha256,
        "object_count": inspection.top_level_object_count,
        "program_operation_count": len(operations),
        "failures": model_readback_failures,
        "canonical_write_authority": False,
    }
    inspection_ref = _put_branch_json(
        repository,
        run,
        "stage-4-model-inspection",
        {
            "schema": "ParthenonStage4ModelInspection@1",
            "artifact_ref": _artifact_dict(model_artifact),
            "workspace_relative_path": MODEL_WORKSPACE_RELATIVE_PATH,
            "program_ref": _record_dict(program_ref),
            "program_digest": program_digest,
            "inspection": inspection.to_dict(),
            "canonical_write_authority": False,
        },
    )
    spatial = validate_stage4_spatial_model(
        model_path,
        operations=operations,
        program_digest=program_digest,
        predecessor_envelope=PREDECESSOR_BBOX,
    )
    spatial_ref = _put_branch_json(repository, run, "stage-4-spatial-validation", spatial)
    detail = validate_stage4_model(model_path, operations=operations, program_digest=program_digest)
    detail_ref = _put_branch_json(repository, run, "stage-4-detail-validation", detail)
    material_bindings = validate_stage4_material_bindings(
        model_path,
        operations=operations,
    )
    material_bindings_ref = _put_branch_json(
        repository,
        run,
        "stage-4-material-binding-validation",
        material_bindings,
    )
    roof_eaves_pediment = stage4_roof.validate_roof_eaves_pediment_operations(
        operations
    )
    roof_eaves_pediment_ref = _put_branch_json(
        repository,
        run,
        "stage-4-roof-eaves-pediment-validation",
        roof_eaves_pediment,
    )
    inner_colonnade = (
        stage4_inner_colonnade.validate_inner_colonnade_operations(operations)
    )
    inner_colonnade_ref = _put_branch_json(
        repository,
        run,
        "stage-4-inner-colonnade-validation",
        inner_colonnade,
    )
    door_assembly = stage4_doors.validate_door_assembly_operations(
        operations,
        resolution=(
            stage4_doors.DoorAssemblyResolution.AUTHORIZED_CLOSED_DOUBLE_LEAF
        ),
        resolution_receipt=lineage["door_assembly_delta"],
    )
    door_assembly_ref = _put_branch_json(
        repository,
        run,
        "stage-4-door-assembly-validation",
        door_assembly,
    )
    successor_relations = stage4_relations.validate_stage4_successor_relations(
        operations,
        model_path=model_path,
        typed_contact_allowlist=(),
        typed_relation_contracts=relation_contracts,
    )
    successor_relations_ref = _put_branch_json(
        repository,
        run,
        "stage-4-successor-relations",
        {
            **successor_relations,
            "relation_contract_set_ref": _record_dict(
                relation_contract_set_ref
            ),
            "relation_contract_count": len(relation_contracts),
            "relation_contract_digest": relation_contract_digest,
        },
    )
    evidence_gate = compile_stage4_evidence_gate(
        visual_manifest=predecessor["visual_manifest"],
        source_record_refs=source_record_refs,
    )
    evidence_gate_ref = _put_branch_json(repository, run, "stage-4-evidence-gate", evidence_gate)
    component_coverage = compile_stage4_component_coverage(
        predecessor["predecessor_operations"],
        operations,
        lineage=lineage,
        lineage_ref=program_ref.uri,
        evidence_refs=(
            evidence_gate_ref.uri,
            detail_ref.uri,
            roof_eaves_pediment_ref.uri,
            door_assembly_ref.uri,
            correction_provenance_ref.uri,
            preflight_ref.uri,
            relation_contract_set_ref.uri,
        ),
        relational_revalidation_refs=(
            inspection_ref.uri,
            spatial_ref.uri,
            inner_colonnade_ref.uri,
            door_assembly_ref.uri,
            correction_provenance_ref.uri,
            preflight_ref.uri,
            relation_contract_set_ref.uri,
            successor_relations_ref.uri,
            material_bindings_ref.uri,
        ),
    )
    component_coverage_payload = {
        **component_coverage.to_dict(),
        "receipt_digest": component_coverage.receipt_digest,
    }
    component_coverage_ref = _put_branch_json(
        repository,
        run,
        "stage-4-component-coverage",
        component_coverage_payload,
    )
    close_gate_receipts = {
        "model_readback": model_readback_gate,
        "spatial": spatial,
        "detail": detail,
        "evidence": evidence_gate,
        "roof_eaves_pediment": roof_eaves_pediment,
        "inner_colonnade": inner_colonnade,
        "door_assembly": door_assembly,
        "correction_provenance": correction_provenance,
        "successor_relations": successor_relations,
        "component_coverage": component_coverage_payload,
        "material_bindings": material_bindings,
    }
    close_gate_refs = {
        "model_readback": inspection_ref.uri,
        "spatial": spatial_ref.uri,
        "detail": detail_ref.uri,
        "evidence": evidence_gate_ref.uri,
        "roof_eaves_pediment": roof_eaves_pediment_ref.uri,
        "inner_colonnade": inner_colonnade_ref.uri,
        "door_assembly": door_assembly_ref.uri,
        "correction_provenance": correction_provenance_ref.uri,
        "successor_relations": successor_relations_ref.uri,
        "component_coverage": component_coverage_ref.uri,
        "material_bindings": material_bindings_ref.uri,
    }
    preclose_gate = compile_stage4_close_gate(
        gate_receipts=close_gate_receipts,
        gate_refs=close_gate_refs,
    )
    preclose_gate_ref = _put_branch_json(
        repository,
        run,
        "stage-4-preclose-gate",
        preclose_gate,
    )
    if not preclose_gate["passed"]:
        raise ParthenonStage4Error(
            "Stage 4 cannot close: " + "; ".join(preclose_gate["failures"])
        )
    closed_state, convergence = close_stage4_state(
        opened_state,
        gate_receipts=close_gate_receipts,
        gate_refs=close_gate_refs,
    )
    closed_state_ref = _put_branch_json(
        repository,
        run,
        "stage-4-operational-state",
        closed_state.to_dict(),
    )
    convergence_ref = _put_branch_json(
        repository,
        run,
        "stage-4-convergence",
        convergence.to_dict(),
    )

    contract_ref = repository.put_json(
        run=run,
        destination=_run_destination(RUN_ID),
        record_kind="stage-4-contract",
        payload={
            "schema": "ParthenonStage4Contract@1",
            "predecessor_progress_ref": _record_dict(PREDECESSOR_PROGRESS_REF),
            "predecessor_state_ref": _record_dict(PREDECESSOR_STATE_REF),
            "predecessor_pack_ref": _record_dict(PREDECESSOR_PACK_REF),
            "predecessor_program_ref": _record_dict(PREDECESSOR_PROGRAM_REF),
            "predecessor_model_sha256": PREDECESSOR_MODEL_SHA256,
            "visual_manifest_ref": _record_dict(VISUAL_MANIFEST_REF),
            "human_authorization_ref": door_decision_ref.uri,
            "authorization_capture_ref": _record_dict(
                authorization_capture_ref
            ),
            "failed_attempt_experience_ref": _record_dict(experience_ref),
            "correction_manifest_ref": _record_dict(correction_manifest_ref),
            "correction_provenance_ref": _record_dict(
                correction_provenance_ref
            ),
            "fixed_run_preflight_ref": _record_dict(preflight_ref),
            "relation_contract_set_ref": _record_dict(
                relation_contract_set_ref
            ),
            "output_disposition": "HOLD",
            "canonical_write_authority": False,
        },
    )
    branch_selection_ref = repository.put_json(
        run=run,
        destination=_run_destination(RUN_ID),
        record_kind="stage-4-branch-selection",
        payload={
            "schema": "ParthenonStage4BranchSelection@1",
            "branch_id": BRANCH_ID,
            "source_state_ref": _record_dict(PREDECESSOR_STATE_REF),
            "selection_changed": False,
            "canonical_write_authority": False,
        },
    )
    scope_ref = _put_branch_json(
        repository,
        run,
        "stage-4-branch-scope",
        {
            "schema": "ParthenonStage4BranchScope@1",
            "branch_id": BRANCH_ID,
            "research_manifest_ref": _record_dict(research_manifest_ref),
            "asset_provenance_ref": _record_dict(asset_provenance_ref),
            "correction_manifest_ref": _record_dict(correction_manifest_ref),
            "correction_report_ref": _record_dict(correction_document_ref),
            "visual_manifest_ref": _record_dict(VISUAL_MANIFEST_REF),
            "excluded": ["later inscriptions", "lost sculptural figures", "modern restoration traces"],
            "canonical_write_authority": False,
        },
    )
    decision_universe_ref = _put_branch_json(
        repository,
        run,
        "stage-4-decision-universe",
        {
            "schema": "ParthenonStage4DecisionUniverse@1",
            "claim_ids": [str(item["claim_id"]) for item in EVIDENCE_CLAIMS],
            "open_decisions": [],
            "parked_decisions": ["west-room-ionic-detail", "lost-sculpture-figures"],
            "canonical_write_authority": False,
        },
    )
    basis_index_ref = _put_branch_json(
        repository,
        run,
        "stage-4-basis-index",
        {
            "schema": "ParthenonStage4BasisIndex@1",
            "research_manifest_ref": _record_dict(research_manifest_ref),
            "correction_manifest_ref": _record_dict(correction_manifest_ref),
            "source_record_refs": {key: _record_dict(value) for key, value in sorted(source_record_refs.items())},
            "visual_manifest_ref": _record_dict(VISUAL_MANIFEST_REF),
            "lineage_digest": _digest(lineage),
            "canonical_write_authority": False,
        },
    )
    dependency_ledger_ref = _put_branch_json(
        repository,
        run,
        "stage-4-dependency-ledger",
        {
            "schema": "ParthenonStage4DependencyLedger@1",
            "closed": bool(preclose_gate["passed"] and convergence.stage_ready),
            "edges": [
                [predecessor_binding_ref.uri, opened_state_ref.uri],
                [opened_state_ref.uri, program_ref.uri],
                [failed_attempt_audit_ref.uri, experience_ref.uri],
                [experience_ref.uri, door_decision_ref.uri],
                [failed_door_diagnosis_ref.uri, door_decision_ref.uri],
                [authorization_capture_ref.uri, door_decision_ref.uri],
                [authorization_capture_ref.uri, preflight_ref.uri],
                [PREDECESSOR_PROGRAM_REF.uri, preflight_ref.uri],
                [PREDECESSOR_STATE_REF.uri, preflight_ref.uri],
                [FAILED_STAGE4_PROGRAM_REF.uri, preflight_ref.uri],
                [VISUAL_MANIFEST_REF.uri, preflight_ref.uri],
                [predecessor_model_artifact.uri, preflight_ref.uri],
                [VISUAL_MANIFEST_REF.uri, door_decision_ref.uri],
                [experience_ref.uri, correction_manifest_ref.uri],
                [door_decision_ref.uri, correction_manifest_ref.uri],
                [correction_document_ref.uri, correction_manifest_ref.uri],
                [research_manifest_ref.uri, correction_manifest_ref.uri],
                [asset_provenance_ref.uri, correction_manifest_ref.uri],
                [failed_attempt_audit_ref.uri, correction_manifest_ref.uri],
                [failed_door_diagnosis_ref.uri, correction_manifest_ref.uri],
                [authorization_capture_ref.uri, correction_manifest_ref.uri],
                [preflight_ref.uri, correction_manifest_ref.uri],
                [correction_provenance_ref.uri, correction_manifest_ref.uri],
                [correction_manifest_ref.uri, program_ref.uri],
                [correction_provenance_ref.uri, program_ref.uri],
                [door_decision_ref.uri, program_ref.uri],
                [preflight_ref.uri, program_ref.uri],
                [preflight_ref.uri, relation_contract_set_ref.uri],
                [relation_contract_set_ref.uri, program_ref.uri],
                [program_ref.uri, model_artifact.uri],
                [program_ref.uri, successor_relations_ref.uri],
                [model_artifact.uri, successor_relations_ref.uri],
                [relation_contract_set_ref.uri, successor_relations_ref.uri],
                [inspection_ref.uri, closed_state_ref.uri],
                [spatial_ref.uri, closed_state_ref.uri],
                [detail_ref.uri, closed_state_ref.uri],
                [evidence_gate_ref.uri, closed_state_ref.uri],
                [material_bindings_ref.uri, closed_state_ref.uri],
                [roof_eaves_pediment_ref.uri, closed_state_ref.uri],
                [inner_colonnade_ref.uri, closed_state_ref.uri],
                [door_assembly_ref.uri, closed_state_ref.uri],
                [correction_provenance_ref.uri, closed_state_ref.uri],
                [successor_relations_ref.uri, closed_state_ref.uri],
                [component_coverage_ref.uri, closed_state_ref.uri],
                [preclose_gate_ref.uri, closed_state_ref.uri],
            ],
            "canonical_write_authority": False,
        },
    )
    aggregate_passed = bool(preclose_gate["passed"] and convergence.stage_ready)
    aggregate_gate = {
        "schema": "ParthenonStage4AggregateGate@1",
        "status": "PASSED" if aggregate_passed else "FAILED",
        "passed": aggregate_passed,
        "opened_state_ref": _record_dict(opened_state_ref),
        "inspection_ref": _record_dict(inspection_ref),
        "spatial_ref": _record_dict(spatial_ref),
        "detail_ref": _record_dict(detail_ref),
        "evidence_gate_ref": _record_dict(evidence_gate_ref),
        "material_bindings_ref": _record_dict(material_bindings_ref),
        "roof_eaves_pediment_ref": _record_dict(roof_eaves_pediment_ref),
        "inner_colonnade_ref": _record_dict(inner_colonnade_ref),
        "door_assembly_ref": _record_dict(door_assembly_ref),
        "correction_provenance_ref": _record_dict(
            correction_provenance_ref
        ),
        "fixed_run_preflight_ref": _record_dict(preflight_ref),
        "relation_contract_set_ref": _record_dict(
            relation_contract_set_ref
        ),
        "successor_relations_ref": _record_dict(successor_relations_ref),
        "component_coverage_ref": _record_dict(component_coverage_ref),
        "preclose_gate_ref": _record_dict(preclose_gate_ref),
        "closed_state_ref": _record_dict(closed_state_ref),
        "convergence_ref": _record_dict(convergence_ref),
        "checks": {
            **{
                gate_name: bool(preclose_gate["checks"][gate_name]["passed"])
                for gate_name in STAGE4_CLOSE_GATE_NAMES
            },
            "state_closed_after_gates": convergence.stage_ready,
        },
        "hard_gate_failure_refs": list(preclose_gate["hard_gate_failure_refs"]),
        "conflict_refs": list(preclose_gate["conflict_refs"]),
        "tolerance_failure_refs": list(preclose_gate["tolerance_failure_refs"]),
        "revalidation_refs": list(preclose_gate["revalidation_refs"]),
        "failures": list(preclose_gate["failures"]),
        "canonical_write_authority": False,
    }
    aggregate_gate_ref = _put_branch_json(repository, run, "stage-4-gate", aggregate_gate)
    review_ref = repository.put_json(
        run=run,
        destination=_review_destination(RUN_ID),
        record_kind="stage-4-agent-review",
        payload={
            "schema": "ParthenonStage4Review@1",
            "disposition": "HOLD",
            "reason": "complete evidence pack is not canonical acceptance or promotion authority",
            "aggregate_gate_ref": _record_dict(aggregate_gate_ref),
            "canonical_write_authority": False,
        },
    )

    cross_run_predecessor = CrossRunStagePackPredecessor(
        stage_id="stage-3",
        stage_index=3,
        branch=predecessor_state.branch,
        pack_ref=PREDECESSOR_PACK_REF,
        program_digest=PREDECESSOR_PROGRAM_DIGEST,
    )
    pack = compile_stage_evidence_pack(
        project_id=PROJECT_ID,
        run=run,
        branch=closed_state.branch,
        scope_ref=scope_ref,
        stage_id="stage-4",
        stage_index=4,
        revision=1,
        program_digest=program_digest,
        contract_ref=contract_ref,
        bindings=(
            StageEvidenceBinding(StageEvidenceRole.BRANCH_SELECTION, branch_selection_ref),
            StageEvidenceBinding(StageEvidenceRole.BRANCH_SCOPE, scope_ref),
            StageEvidenceBinding(StageEvidenceRole.DECISION_UNIVERSE, decision_universe_ref),
            StageEvidenceBinding(StageEvidenceRole.BASIS_INDEX, basis_index_ref),
            StageEvidenceBinding(StageEvidenceRole.DEPENDENCY_LEDGER, dependency_ledger_ref),
            StageEvidenceBinding(StageEvidenceRole.EVIDENCE_SUFFICIENCY, evidence_gate_ref),
            StageEvidenceBinding(StageEvidenceRole.DESIGN_STATE, closed_state_ref),
            StageEvidenceBinding(StageEvidenceRole.GEOMETRY_PROGRAM, program_ref),
            StageEvidenceBinding(StageEvidenceRole.MODEL_INSPECTION, inspection_ref),
            StageEvidenceBinding(StageEvidenceRole.STAGE_GATE, aggregate_gate_ref),
            StageEvidenceBinding(StageEvidenceRole.STAGE_CONVERGENCE, convergence_ref),
            StageEvidenceBinding(StageEvidenceRole.REVIEW, review_ref),
        ),
        artifacts=(
            StageArtifactBinding(
                role=StageArtifactRole.CAD_MODEL,
                ref=model_artifact,
                program_digest=program_digest,
            ),
        ),
        gaps=(),
        closure=StageClosureSummary(
            evidence_sufficient=bool(evidence_gate["passed"]),
            dependencies_closed=bool(
                preclose_gate["passed"] and convergence.stage_ready
            ),
            hard_gates_passed=bool(aggregate_gate["passed"]),
            stage_ready=bool(convergence.stage_ready and aggregate_gate["passed"]),
            model_artifact_current=bool(model_readback_gate["passed"]),
        ),
        predecessor=cross_run_predecessor,
    )
    if pack.compilation_status is not StagePackCompilationStatus.COMPLETE:
        raise ParthenonStage4Error("Stage 4 evidence pack did not compile COMPLETE")
    pack_ref = _put_branch_json(repository, run, "stage-4-evidence-pack", pack.to_dict())

    usage_ref = repository.put_json(
        run=run,
        destination=_run_destination(RUN_ID),
        record_kind="stage-4-usage",
        payload={
            "schema": "ParthenonStage4Usage@1",
            "captured_at": captured_at,
            "elapsed_seconds": time.perf_counter() - started,
            "provider_calls": 0,
            "network_downloads": 0,
            "adopted_external_assets": 0,
            "predecessor_operation_count": 271,
            "current_operation_count": len(operations),
            "relation_contract_count": len(relation_contracts),
            "relation_contract_digest": relation_contract_digest,
            "operation_execution_digest": operation_execution_digest,
            "model_bytes": len(model_bytes),
            "model_sha256": model_artifact.sha256,
            "canonical_write_authority": False,
        },
    )
    progress_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(PersistenceArea.EXPORT),
        record_kind="parthenon-stage-4-progress",
        payload={
            "schema": "ParthenonStage4Progress@1",
            "project_id": PROJECT_ID,
            "research_run_id": RESEARCH_RUN_ID,
            "run_id": RUN_ID,
            "selected_branch_id": BRANCH_ID,
            "disposition": "HOLD",
            "captured_at": captured_at,
            "predecessor_binding_ref": _record_dict(predecessor_binding_ref),
            "open_state_ref": _record_dict(opened_state_ref),
            "state_rebind_ref": _record_dict(state_rebind_ref),
            "final_state_ref": _record_dict(closed_state_ref),
            "program_ref": _record_dict(program_ref),
            "program_digest": program_digest,
            "pack_ref": _record_dict(pack_ref),
            "pack_digest": pack.pack_digest,
            "pack_status": pack.compilation_status.value,
            "model_artifact_ref": _artifact_dict(model_artifact),
            "model_workspace_relative_path": MODEL_WORKSPACE_RELATIVE_PATH,
            "inspection_ref": _record_dict(inspection_ref),
            "spatial_ref": _record_dict(spatial_ref),
            "detail_ref": _record_dict(detail_ref),
            "evidence_gate_ref": _record_dict(evidence_gate_ref),
            "material_bindings_ref": _record_dict(material_bindings_ref),
            "roof_eaves_pediment_ref": _record_dict(roof_eaves_pediment_ref),
            "inner_colonnade_ref": _record_dict(inner_colonnade_ref),
            "door_assembly_ref": _record_dict(door_assembly_ref),
            "correction_provenance_ref": _record_dict(
                correction_provenance_ref
            ),
            "fixed_run_preflight_ref": _record_dict(preflight_ref),
            "relation_contract_set_ref": _record_dict(
                relation_contract_set_ref
            ),
            "relation_contract_count": len(relation_contracts),
            "relation_contract_digest": relation_contract_digest,
            "operation_execution_digest": operation_execution_digest,
            "successor_relations_ref": _record_dict(successor_relations_ref),
            "component_coverage_ref": _record_dict(component_coverage_ref),
            "preclose_gate_ref": _record_dict(preclose_gate_ref),
            "aggregate_gate_ref": _record_dict(aggregate_gate_ref),
            "convergence_ref": _record_dict(convergence_ref),
            "dependency_ledger_ref": _record_dict(dependency_ledger_ref),
            "contract_ref": _record_dict(contract_ref),
            "usage_ref": _record_dict(usage_ref),
            "research_manifest_ref": _record_dict(research_manifest_ref),
            "asset_provenance_ref": _record_dict(asset_provenance_ref),
            "correction_manifest_ref": _record_dict(correction_manifest_ref),
            "correction_report_ref": _record_dict(correction_document_ref),
            "failed_attempt_audit_ref": _record_dict(failed_attempt_audit_ref),
            "failed_attempt_experience_ref": _record_dict(experience_ref),
            "door_dependency_diagnosis_ref": _record_dict(
                failed_door_diagnosis_ref
            ),
            "authorization_capture_ref": _record_dict(
                authorization_capture_ref
            ),
            "door_decision_ref": _record_dict(door_decision_ref),
            "asset_document_artifact_ref": _artifact_dict(asset_document_artifact),
            "review_ref": _record_dict(review_ref),
            "canonical_write_authority": False,
        },
    )

    retained_record_refs = {
        "progress": progress_ref,
        "predecessor_binding": predecessor_binding_ref,
        "open_state": opened_state_ref,
        "state_rebind": state_rebind_ref,
        "fixed_run_preflight": preflight_ref,
        "failed_attempt_diagnosis": failed_door_diagnosis_ref,
        "failed_attempt_audit": failed_attempt_audit_ref,
        "failed_attempt_experience": experience_ref,
        "authorization_capture": authorization_capture_ref,
        "door_decision": door_decision_ref,
        "correction_report": correction_document_ref,
        "correction_provenance": correction_provenance_ref,
        "correction_manifest": correction_manifest_ref,
        "research_manifest": research_manifest_ref,
        "asset_provenance": asset_provenance_ref,
        "relation_contract_set": relation_contract_set_ref,
        "program": program_ref,
        "inspection": inspection_ref,
        "spatial": spatial_ref,
        "detail": detail_ref,
        "material_bindings": material_bindings_ref,
        "roof_eaves_pediment": roof_eaves_pediment_ref,
        "inner_colonnade": inner_colonnade_ref,
        "door_assembly": door_assembly_ref,
        "successor_relations": successor_relations_ref,
        "evidence_gate": evidence_gate_ref,
        "component_coverage": component_coverage_ref,
        "preclose_gate": preclose_gate_ref,
        "final_state": closed_state_ref,
        "convergence": convergence_ref,
        "contract": contract_ref,
        "branch_selection": branch_selection_ref,
        "branch_scope": scope_ref,
        "decision_universe": decision_universe_ref,
        "basis_index": basis_index_ref,
        "dependency_ledger": dependency_ledger_ref,
        "aggregate_gate": aggregate_gate_ref,
        "review": review_ref,
        "pack": pack_ref,
        "usage": usage_ref,
        **{
            f"research_source:{source_id}": ref
            for source_id, ref in source_record_refs.items()
        },
    }
    progress_binding_fields = {
        "predecessor_binding": "predecessor_binding_ref",
        "open_state": "open_state_ref",
        "state_rebind": "state_rebind_ref",
        "final_state": "final_state_ref",
        "program": "program_ref",
        "pack": "pack_ref",
        "inspection": "inspection_ref",
        "spatial": "spatial_ref",
        "detail": "detail_ref",
        "evidence_gate": "evidence_gate_ref",
        "material_bindings": "material_bindings_ref",
        "roof_eaves_pediment": "roof_eaves_pediment_ref",
        "inner_colonnade": "inner_colonnade_ref",
        "door_assembly": "door_assembly_ref",
        "correction_provenance": "correction_provenance_ref",
        "fixed_run_preflight": "fixed_run_preflight_ref",
        "relation_contract_set": "relation_contract_set_ref",
        "successor_relations": "successor_relations_ref",
        "component_coverage": "component_coverage_ref",
        "preclose_gate": "preclose_gate_ref",
        "aggregate_gate": "aggregate_gate_ref",
        "convergence": "convergence_ref",
        "dependency_ledger": "dependency_ledger_ref",
        "contract": "contract_ref",
        "usage": "usage_ref",
        "research_manifest": "research_manifest_ref",
        "asset_provenance": "asset_provenance_ref",
        "correction_manifest": "correction_manifest_ref",
        "correction_report": "correction_report_ref",
        "failed_attempt_audit": "failed_attempt_audit_ref",
        "failed_attempt_experience": "failed_attempt_experience_ref",
        "failed_attempt_diagnosis": "door_dependency_diagnosis_ref",
        "authorization_capture": "authorization_capture_ref",
        "door_decision": "door_decision_ref",
        "review": "review_ref",
    }
    run_chain_verification = verify_stage4_persisted_run_chain(
        repository,
        record_refs=retained_record_refs,
        progress_binding_fields=progress_binding_fields,
        artifact_refs={
            "model": model_artifact,
            "asset_document": asset_document_artifact,
            "correction_document": correction_document_artifact,
        },
        workspace_artifact_paths={
            "model": model_path,
            "asset_document": asset_document_path,
            "correction_document": correction_document_path,
        },
    )
    run_chain_verification_ref = _put_branch_json(
        repository,
        run,
        "stage-4-persisted-run-chain-verification",
        run_chain_verification,
    )
    if repository.load_json(run_chain_verification_ref) != run_chain_verification:
        raise ParthenonStage4Error(
            "Stage 4 run-chain verification receipt failed typed reload"
        )
    repository.verify()
    head_after = repository.read_head()
    if head_after != head_before:
        raise ParthenonStage4Error("P087 changed canonical HEAD")
    return {
        "project_root": str(repository.layout.root),
        "research_run_id": RESEARCH_RUN_ID,
        "run_id": RUN_ID,
        "disposition": "HOLD",
        "final_model_path": str(model_path),
        "model_sha256": model_artifact.sha256,
        "asset_document_path": str(asset_document_path),
        "correction_document_path": str(correction_document_path),
        "progress_ref": progress_ref,
        "state_ref": closed_state_ref,
        "program_ref": program_ref,
        "pack_ref": pack_ref,
        "inspection_ref": inspection_ref,
        "spatial_ref": spatial_ref,
        "detail_ref": detail_ref,
        "evidence_gate_ref": evidence_gate_ref,
        "material_bindings_ref": material_bindings_ref,
        "roof_eaves_pediment_ref": roof_eaves_pediment_ref,
        "inner_colonnade_ref": inner_colonnade_ref,
        "door_assembly_ref": door_assembly_ref,
        "correction_provenance_ref": correction_provenance_ref,
        "fixed_run_preflight_ref": preflight_ref,
        "relation_contract_set_ref": relation_contract_set_ref,
        "relation_contract_digest": relation_contract_digest,
        "operation_execution_digest": operation_execution_digest,
        "run_chain_verification_ref": run_chain_verification_ref,
        "correction_manifest_ref": correction_manifest_ref,
        "correction_report_ref": correction_document_ref,
        "failed_attempt_audit_ref": failed_attempt_audit_ref,
        "failed_attempt_experience_ref": experience_ref,
        "door_dependency_diagnosis_ref": failed_door_diagnosis_ref,
        "authorization_capture_ref": authorization_capture_ref,
        "door_decision_ref": door_decision_ref,
        "successor_relations_ref": successor_relations_ref,
        "component_coverage_ref": component_coverage_ref,
        "preclose_gate_ref": preclose_gate_ref,
        "aggregate_gate_ref": aggregate_gate_ref,
        "convergence_ref": convergence_ref,
        "dependency_ledger_ref": dependency_ledger_ref,
        "contract_ref": contract_ref,
        "usage_ref": usage_ref,
        "canonical_head": head_after,
        "operation_count": len(operations),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument(
        "--captured-at",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    parser.add_argument(
        "--human-authorization-ref",
        required=True,
        help=(
            "typed human-authorization/decision source reference for "
            "the SOFT closed-double-leaf principal-door candidate"
        ),
    )
    parser.add_argument(
        "--human-authorization-statement",
        required=True,
        help="normalized retained user statement authorizing the SOFT door candidate",
    )
    args = parser.parse_args(argv)
    root = args.root or resolve_probe_root(PROJECT_ID)
    result = run_project(
        root,
        captured_at=args.captured_at,
        human_authorization_ref=args.human_authorization_ref,
        human_authorization_statement=args.human_authorization_statement,
    )
    print("Stage 4 disposition:", result["disposition"])
    print("final 3dm:", result["final_model_path"])
    print("model SHA-256:", result["model_sha256"])
    print("asset provenance:", result["asset_document_path"])
    print("correction report:", result["correction_document_path"])
    print("progress:", result["progress_ref"].uri)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through CLI
    raise SystemExit(main())
