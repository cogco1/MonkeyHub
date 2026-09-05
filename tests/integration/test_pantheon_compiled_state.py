from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from archflow.project.refs import BranchRef, ProjectRecordRef, RunRef, record_file_name
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.state.commitments import Commitment, CommitmentKind, CommitmentStatus, CommitmentStrength, CriterionRef
from archflow.state.decision_operator import ConditionComparator, DecisionOperator, LegacyDecisionOperatorV1, StateCondition, compile_decision_operator, load_decision_operator_record
from archflow.state.operational_state import DependencyEffect, DependencyEdge, DesignObligation, FactEpistemicStatus, LegacyOperationalMarkovStateV2, ObligationStatus, OperationalMarkovState, StateDomain, StateFact, load_operational_state_record


REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ID = "test_pantheon"
BOOTSTRAP_RUN_ID = "bootstrap-001"
RUN_ID = "compiled-state-001"
M009_RUN_ID = "compiled-state-002"
BRANCH_ID = "research-primary"
AGENT_ID = "agent:pantheon-source-research"
AGENT_AUTHORITY = "architect-agent-pantheon-research"
ACCESSED_ON = "2026-07-25"

# The one case-specific input this fixture is allowed to carry. Everything the
# tests assert about is derived from it by the builders below; no case answer
# is read from a checked-in project.
RAW_REQUEST_PROMPT = (
    "In a voxel sandbox, design and build the Pantheon step "
    "by step. First retrieve reliable evidence, then derive "
    "its use, scale, spatial relations, construction, and "
    "materials from the current state without preset "
    "building data."
)


SOURCES = (
    {
        "evidence_id": "E-PAN-01",
        "url": (
            "https://direzionemuseiroma.cultura.gov.it/en/pantheon/"
            "historical-background/"
        ),
        "publisher": (
            "Direzione Musei statali della città di Roma, "
            "Italian Ministry of Culture"
        ),
        "accessed_on": ACCESSED_ON,
        "claims": [
            "The current monument is organized as pronaos, rectangular "
            "intermediate block, and circular hall.",
            "Rotunda interior diameter and height are each stated as 43.30 m.",
            "The oculus is described as about 9 m.",
        ],
        "certainty": "authoritative current-monument description",
        "unresolved_status": (
            "Chronology and exact oculus measurement definition remain "
            "unresolved across sources."
        ),
    },
    {
        "evidence_id": "E-PAN-02",
        "url": "https://museoomero.it/en/opere/the-pantheon/",
        "publisher": "Museo Tattile Statale Omero",
        "accessed_on": ACCESSED_ON,
        "claims": [
            "The current portico has sixteen Corinthian columns arranged "
            "as eight plus four plus four.",
            "The interior lower order includes alternating spaces and "
            "aedicules.",
        ],
        "certainty": "institutional interpretive description",
        "unresolved_status": (
            "This does not establish original statuary identities or a "
            "selected reconstruction phase."
        ),
    },
    {
        "evidence_id": "E-PAN-03",
        "url": "https://dome.mit.edu/handle/1721.3/145648?show=full",
        "publisher": "MIT DOME",
        "accessed_on": ACCESSED_ON,
        "claims": [
            "The catalog record supplies an 8.15 m oculus reading."
        ],
        "certainty": "institutional catalog reading",
        "unresolved_status": (
            "This conflicts with the Italian Ministry's approximate 9 m "
            "statement until measurement definitions are compared."
        ),
    },
    {
        "evidence_id": "E-PAN-04",
        "url": "https://openheritage3d.org/project.php?id=t9sj-mf53",
        "publisher": "Open Heritage 3D",
        "accessed_on": ACCESSED_ON,
        "claims": [
            "A citable digital-survey project exists for the Pantheon."
        ],
        "certainty": "dataset provenance lead",
        "unresolved_status": (
            "No precise survey value was imported into this run."
        ),
    },
    {
        "evidence_id": "E-PAN-05",
        "url": (
            "https://www.mpiwg-berlin.mpg.de/project/pantheon-project"
        ),
        "publisher": "Max Planck Institute for the History of Science",
        "accessed_on": ACCESSED_ON,
        "claims": [
            "The Bern Digital Pantheon Project used multi-campaign laser "
            "scanning from 2005 to 2008."
        ],
        "certainty": "research-project provenance",
        "unresolved_status": (
            "Survey authority is known; exact values were not loaded in "
            "this run."
        ),
    },
    {
        "evidence_id": "E-PAN-06",
        "url": (
            "https://archaeologie.phil-fak.uni-koeln.de/forschung/"
            "forschungsprojekte/bern-digital-pantheon-project"
        ),
        "publisher": "University of Cologne, Archaeological Institute",
        "accessed_on": ACCESSED_ON,
        "claims": [
            "Digital research documents the building and supports "
            "qualified chronology research."
        ],
        "certainty": "academic project description",
        "unresolved_status": (
            "Exact construction authorship and chronology remain disputed."
        ),
    },
    {
        "evidence_id": "E-PAN-07",
        "url": (
            "https://www.cambridge.org/core/journals/"
            "journal-of-roman-studies/article/cult-statues-of-the-pantheon/"
            "6F33641FC3985AA73E86F505477AA64F"
        ),
        "publisher": (
            "Journal of Roman Studies / Cambridge University Press"
        ),
        "accessed_on": ACCESSED_ON,
        "claims": [
            "Scholarly interpretation of cult statues exists and is not "
            "equivalent to an observed current fact."
        ],
        "certainty": "peer-reviewed interpretive source",
        "unresolved_status": (
            "The original statuary program remains disputed."
        ),
    },
    {
        "evidence_id": "E-PAN-08",
        "url": "https://smarthistory.org/the-pantheon/",
        "publisher": "Smarthistory",
        "accessed_on": ACCESSED_ON,
        "claims": [
            "The building's original function is interpreted through "
            "several historical hypotheses."
        ],
        "certainty": "secondary scholarly synthesis",
        "unresolved_status": (
            "Original function remains unknown or disputed."
        ),
    },
    {
        "evidence_id": "E-PAN-09",
        "url": (
            "https://archaeologie.phil-fak.uni-koeln.de/en/research/"
            "research-projects/the-bronze-roof-truss-of-the-vestibule-"
            "of-the-pantheon-in-rome"
        ),
        "publisher": "University of Cologne, Archaeological Institute",
        "accessed_on": ACCESSED_ON,
        "claims": [
            "The ancient bronze roof truss of the vestibule is lost and "
            "studied through reconstruction research."
        ],
        "certainty": "academic alteration/loss evidence",
        "unresolved_status": (
            "This evidence does not select a reconstruction option."
        ),
    },
    {
        "evidence_id": "E-PAN-10",
        "url": (
            "https://brill.com/view/journals/nu/58/4/article-p486_2.pdf"
        ),
        "publisher": "Numen / Brill",
        "accessed_on": ACCESSED_ON,
        "claims": [
            "Original religious function is an interpretive research "
            "question, not a settled project fact."
        ],
        "certainty": "peer-reviewed interpretive source",
        "unresolved_status": (
            "This does not authorize one reconstruction or program answer."
        ),
    },
)


def _compact(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fact(
    domain: StateDomain,
    key: str,
    value: object,
    source_ref: str,
) -> StateFact:
    return StateFact(
        domain=domain,
        key=key,
        value=_compact(value),
        source_ref=source_ref,
    )


def _obligation(
    obligation_id: str,
    statement: str,
    source_ref: str,
    *subjects: str,
) -> DesignObligation:
    return DesignObligation(
        obligation_id=obligation_id,
        statement=statement,
        source_ref=source_ref,
        subject_refs=tuple(subjects),
    )


def _commitment(
    commitment_id: str,
    *,
    strength: CommitmentStrength,
    source_ref: str,
    subject_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...] = (),
) -> Commitment:
    return Commitment(
        commitment_id=commitment_id,
        kind=CommitmentKind.ACHIEVEMENT,
        strength=strength,
        status=CommitmentStatus.PROPOSED,
        authority_id="authority-user",
        authorized_by=None,
        source_event_ref=source_ref,
        satisfaction_criterion=CriterionRef(
            criterion_id=f"criterion-{commitment_id}",
            provider_id="authority:user-or-branch-review",
            subject_refs=subject_refs,
        ),
        evidence_refs=evidence_refs,
        scope_refs=subject_refs,
    )


def _dependency(
    upstream_ref: str,
    downstream_ref: str,
    source_ref: str,
    relation: str = "supports",
) -> DependencyEdge:
    return DependencyEdge(
        upstream_ref=upstream_ref,
        downstream_ref=downstream_ref,
        relation=relation,
        source_ref=source_ref,
    )


def _ref_payload(ref: object) -> dict[str, str]:
    return {
        "uri": ref.uri,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _put_retired_lane_json(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    record_kind: str,
    payload: dict[str, object],
) -> ProjectRecordRef:
    """Install one retained record whose kind the spine never writes.

    These two runs are a retired lane's vocabulary (P042, then its M009
    successor). ADR-004 keeps reads unrestricted so a run like this stays
    readable, but ``put_json`` writes only kinds
    ``archflow.project.record_kinds`` holds, and a case's kinds do not belong
    in the spine's table -- registering ``pantheon-evidence`` there is exactly
    the leakage the guard below forbids. So the fixture lays these records
    down the way an archived lane's run already sits on disk, byte-for-byte as
    the repository would have written them, and reads every one of them back
    through the repository.
    """

    run_layout = repository.layout.run(run.run_id)
    if destination.area is PersistenceArea.INPUT:
        directory = repository.layout.inputs
    elif destination.area is PersistenceArea.RUN_RECORD:
        directory = run_layout.records
    elif destination.area is PersistenceArea.RUN_BRANCH:
        if destination.branch_id is None:
            raise ValueError("run branch destination lacks branch_id")
        directory = run_layout.branches / destination.branch_id / "records"
    else:
        raise ValueError(
            f"the fixture retains no records in {destination.area.value}"
        )
    data = (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    path = directory / record_file_name(record_kind, digest)
    if path.exists():
        raise RuntimeError(f"refusing to overwrite retained record: {path}")
    directory.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return ProjectRecordRef(
        project_id=run.project_id,
        relative_path=path.relative_to(repository.layout.root).as_posix(),
        sha256=digest,
        media_type="application/json",
    )


def _put_p042_record(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    record_kind: str,
    payload: dict[str, object],
) -> ProjectRecordRef:
    """One retained record of the P042 run, in the schema of its own day."""

    return _put_retired_lane_json(
        repository,
        run=run,
        destination=destination,
        record_kind=record_kind,
        payload=_as_p042_legacy(payload),
    )


# The compiled-state run is a retired lane's output: it was written before the
# OperationalMarkovState@3 bump gave a fact its epistemic status, made an
# obligation conditional and blockable, and gave a dependency edge an effect.
# The successor run below exists to prove that such a source is explicitly
# recompiled and never silently migrated, so the fixture has to lay the P042
# run down in its own schema rather than today's. This is the whole
# difference: four schema literals, and six fields the later schema added.
_P042_SCHEMA_AT_THE_TIME = {
    "OperationalMarkovState@3": "OperationalMarkovState@2",
    "DecisionOperator@2": "DecisionOperator@1",
    "StateDelta@2": "StateDelta@1",
    "DesignStateClosureReceipt@2": "DesignStateClosureReceipt@1",
}
_P042_FIELDS_NOT_YET_INTRODUCED = frozenset(
    {
        "epistemic_status",
        "confidence",
        "qualification",
        "condition",
        "blocked_by",
        "effect",
    }
)


def _as_p042_legacy(value: object) -> object:
    """One record as the P042 run wrote it, before the @3 state bump."""

    if isinstance(value, dict):
        return {
            key: (
                _P042_SCHEMA_AT_THE_TIME.get(item, item)
                if key == "schema" and isinstance(item, str)
                else _as_p042_legacy(item)
            )
            for key, item in value.items()
            if key not in _P042_FIELDS_NOT_YET_INTRODUCED
        }
    if isinstance(value, list):
        return [_as_p042_legacy(item) for item in value]
    return value


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _build_transitions(
    planned_run: RunRef,
    input_uri: str,
) -> tuple[
    OperationalMarkovState,
    tuple[object, ...],
    dict[str, object],
]:
    evidence_refs = tuple(
        f"evidence:{source['evidence_id']}" for source in SOURCES
    )
    state0 = OperationalMarkovState(
        branch=BranchRef(
            run=planned_run,
            branch_id=BRANCH_ID,
            epoch=0,
        ),
        compiler_version="operational-markov-compiler-p041",
        phase="research-brief",
        facts=(
            _fact(
                StateDomain.BRIEF,
                "request-status",
                "available",
                input_uri,
            ),
        ),
        evidence_refs=(input_uri,),
    )

    decision_a = "P041-PANTHEON-A-COMPILE-SECURE-EVIDENCE"
    source_a = f"decision:{decision_a}"
    entity = _fact(
        StateDomain.BRIEF,
        "entity-candidate",
        "Pantheon, Piazza della Rotonda, Rome",
        "evidence:E-PAN-01",
    )
    sequence = _fact(
        StateDomain.SEMANTIC,
        "current-sequence",
        {
            "ordered_spaces": [
                "pronaos",
                "rectangular-vestibule",
                "circular-hall",
            ]
        },
        "evidence:E-PAN-01",
    )
    diameter = _fact(
        StateDomain.PARAMETER,
        "current-rotunda-interior-diameter",
        {"number": 43.30, "unit": "m"},
        "evidence:E-PAN-01",
    )
    height = _fact(
        StateDomain.PARAMETER,
        "current-interior-height-to-oculus",
        {"number": 43.30, "unit": "m"},
        "evidence:E-PAN-01",
    )
    observed_relation = _fact(
        StateDomain.SEMANTIC,
        "observed-diameter-height-relation",
        "approximately equal; design-intent claim not inferred",
        "evidence:E-PAN-01",
    )
    columns = _fact(
        StateDomain.PARAMETER,
        "current-portico-columns",
        {
            "total": 16,
            "row_distribution": [8, 4, 4],
            "order": "Corinthian",
        },
        "evidence:E-PAN-02",
    )
    lower = _fact(
        StateDomain.PARAMETER,
        "current-lower-interior-articulation",
        {"alternating_spaces": 7, "aedicules": 8},
        "evidence:E-PAN-02",
    )
    coffers = _fact(
        StateDomain.PARAMETER,
        "current-coffer-rings",
        5,
        "evidence:E-PAN-01",
    )
    drains = _fact(
        StateDomain.PARAMETER,
        "current-floor-drain-openings",
        {"count": 22, "ancient_phase_status": "unresolved"},
        "evidence:E-PAN-01",
    )
    alteration = _fact(
        StateDomain.UNKNOWN,
        "current-alteration-mask",
        {
            "replacement_columns_17c": 3,
            "attic_decoration_date": 1757,
            "original_pediment_bronze": "lost",
            "ancient_bronze_roof_truss": "lost",
        },
        "evidence:E-PAN-09",
    )
    oculus_mit = _fact(
        StateDomain.PARAMETER,
        "oculus-reading-mit",
        {
            "number": 8.15,
            "unit": "m",
            "qualifier": "stated",
            "status": "unresolved",
        },
        "evidence:E-PAN-03",
    )
    oculus_italian = _fact(
        StateDomain.PARAMETER,
        "oculus-reading-italian-museums",
        {
            "number": 9.0,
            "unit": "m",
            "qualifier": "approximately",
            "status": "unresolved",
        },
        "evidence:E-PAN-01",
    )
    survey = _fact(
        StateDomain.BRIEF,
        "geometry-survey-authority",
        {
            "dataset": "Bern-Digital-Pantheon",
            "acquisition": "multi-campaign-laser-scan",
            "campaigns": "2005-2008",
            "precise_values_loaded": False,
        },
        "evidence:E-PAN-05",
    )
    chronology = _fact(
        StateDomain.UNKNOWN,
        "exact-construction-chronology-status",
        "DISPUTED",
        "evidence:E-PAN-06",
    )
    original_function = _fact(
        StateDomain.UNKNOWN,
        "original-function-status",
        "UNKNOWN_OR_DISPUTED",
        "evidence:E-PAN-08",
    )
    statuary = _fact(
        StateDomain.UNKNOWN,
        "original-statuary-status",
        "UNKNOWN_OR_DISPUTED",
        "evidence:E-PAN-07",
    )
    operator_a = DecisionOperator(
        decision_id=decision_a,
        decision_type="compile-authority-evidence",
        base_state_digest=state0.state_digest,
        authority_id=AGENT_AUTHORITY,
        intent=(
            "Compile sourced current facts and preserve historical "
            "uncertainty without generating form."
        ),
        preconditions=(
            StateCondition(
                "state:phase",
                ConditionComparator.EQUALS,
                "research-brief",
            ),
        ),
        add_facts=(
            entity,
            sequence,
            diameter,
            height,
            observed_relation,
            columns,
            lower,
            coffers,
            drains,
            alteration,
            oculus_mit,
            oculus_italian,
            survey,
            chronology,
            original_function,
            statuary,
        ),
        spawn_obligations=(
            _obligation(
                "pantheon.confirm-entity-identity",
                "Confirm the requested entity is the Pantheon in Rome "
                "rather than a generic Roman pantheon.",
                source_a,
                entity.ref,
            ),
            _obligation(
                "pantheon.preserve-source-scope",
                "Keep current, ancient-phase, reconstruction, and "
                "interpretation claims explicitly separated.",
                source_a,
                chronology.ref,
                original_function.ref,
                statuary.ref,
            ),
        ),
        add_dependencies=(
            _dependency("evidence:E-PAN-01", sequence.ref, source_a),
            _dependency("evidence:E-PAN-01", diameter.ref, source_a),
            _dependency("evidence:E-PAN-03", diameter.ref, source_a),
            _dependency("evidence:E-PAN-01", height.ref, source_a),
            _dependency("evidence:E-PAN-03", height.ref, source_a),
            _dependency("evidence:E-PAN-02", columns.ref, source_a),
            _dependency("evidence:E-PAN-09", alteration.ref, source_a),
            _dependency("evidence:E-PAN-05", survey.ref, source_a),
            _dependency("evidence:E-PAN-06", chronology.ref, source_a),
            _dependency(
                "evidence:E-PAN-08",
                original_function.ref,
                source_a,
            ),
            _dependency("evidence:E-PAN-07", statuary.ref, source_a),
        ),
        evidence_refs=evidence_refs,
    )
    transition_a = compile_decision_operator(state0, operator_a)
    state_a = transition_a.state

    decision_b = "P041-PANTHEON-B-COMPILE-TARGET-BRANCHES"
    source_b = f"decision:{decision_b}"
    target_status = _fact(
        StateDomain.DECISION,
        "target-kind-status",
        "UNSELECTED",
        source_b,
    )
    target_allowed = _fact(
        StateDomain.DECISION,
        "target-kind-allowed-values",
        [
            "HISTORICAL_RECONSTRUCTION",
            "PANTHEON_INSPIRED_NEW_BUILDING",
            "MINECRAFT_VOXEL_TRANSLATION",
        ],
        source_b,
    )
    historical = _fact(
        StateDomain.SEMANTIC,
        "branch-historical-reconstruction",
        {
            "purpose": "reconstruct one explicitly selected historical phase",
            "required_inputs": [
                "reference-phase",
                "alteration-policy",
                "unknown-reconstruction-policy",
            ],
            "forbidden_claims": [
                "current hybrid fabric equals ancient original"
            ],
        },
        source_b,
    )
    inspired = _fact(
        StateDomain.SEMANTIC,
        "branch-inspired-new-building",
        {
            "purpose": (
                "derive a new building from selected precedent relations"
            ),
            "required_inputs": [
                "site",
                "program",
                "scale",
                "structure",
                "applicable-codes",
                "selected-precedent-principles",
            ],
            "forbidden_claims": [
                "historical reconstruction",
                "historical dimensions are mandatory",
            ],
        },
        source_b,
    )
    voxel_branch = _fact(
        StateDomain.SEMANTIC,
        "branch-voxel-translation",
        {
            "purpose": (
                "discretize a selected phase or selected new design"
            ),
            "required_inputs": [
                "source-target",
                "game-version",
                "blocks-per-meter",
                "block-palette",
                "maximum-error",
                "fidelity-priorities",
            ],
            "forbidden_claims": [
                "voxel geometry or material is historically exact"
            ],
        },
        source_b,
    )
    unknowns = _fact(
        StateDomain.UNKNOWN,
        "open-unknown-set",
        {
            "entity_identity": "OPEN",
            "target_kind": "OPEN",
            "reference_phase": "OPEN",
            "exact_oculus_diameter": "OPEN_CONFLICT",
            "exact_authorship_and_chronology": "DISPUTED",
            "original_function": "UNKNOWN_OR_DISPUTED",
            "original_statuary": "UNKNOWN_OR_DISPUTED",
            "lost_finishes_and_pediment_decoration": "UNKNOWN",
            "original_forecourt": "INSUFFICIENT_EVIDENCE",
            "inspired_building_program_site_code": "MISSING",
            "voxel_specification": "MISSING",
        },
        source_b,
    )
    commitment_historical = _commitment(
        "pantheon.target.historical-reconstruction",
        strength=CommitmentStrength.NEGOTIABLE,
        source_ref=source_b,
        subject_refs=(target_status.ref, historical.ref),
        evidence_refs=("evidence:E-PAN-01",),
    )
    commitment_inspired = _commitment(
        "pantheon.target.inspired-new-building",
        strength=CommitmentStrength.NEGOTIABLE,
        source_ref=source_b,
        subject_refs=(target_status.ref, inspired.ref),
    )
    commitment_voxel = _commitment(
        "pantheon.target.voxel-translation",
        strength=CommitmentStrength.NEGOTIABLE,
        source_ref=source_b,
        subject_refs=(target_status.ref, voxel_branch.ref),
    )
    commitment_phase = _commitment(
        "pantheon.reference-phase.trajanic-hadrianic",
        strength=CommitmentStrength.HYPOTHESIS,
        source_ref=source_b,
        subject_refs=(historical.ref, chronology.ref),
        evidence_refs=("evidence:E-PAN-06",),
    )
    commitment_unknown = _commitment(
        "pantheon.unknown-policy",
        strength=CommitmentStrength.NEGOTIABLE,
        source_ref=source_b,
        subject_refs=(unknowns.ref,),
    )
    operator_b = DecisionOperator(
        decision_id=decision_b,
        decision_type="compile-conditional-target-branches",
        base_state_digest=state_a.state_digest,
        authority_id=AGENT_AUTHORITY,
        intent=(
            "Represent three mutually exclusive target interpretations as "
            "proposals and block premature realization."
        ),
        preconditions=(
            StateCondition(
                "state:phase",
                ConditionComparator.EQUALS,
                "research-brief",
            ),
            StateCondition(
                target_status.ref,
                ConditionComparator.ABSENT,
            ),
        ),
        add_facts=(
            target_status,
            target_allowed,
            historical,
            inspired,
            voxel_branch,
            unknowns,
        ),
        spawn_commitments=(
            commitment_historical,
            commitment_inspired,
            commitment_voxel,
            commitment_phase,
            commitment_unknown,
        ),
        spawn_obligations=(
            _obligation(
                "pantheon.select-target-kind",
                "Select exactly one allowed target kind.",
                source_b,
                target_status.ref,
            ),
            _obligation(
                "pantheon.select-reference-phase",
                "If historical reconstruction is selected, choose a named "
                "phase and date tolerance.",
                source_b,
                f"commitment:{commitment_historical.commitment_id}",
            ),
            _obligation(
                "pantheon.select-alteration-policy",
                "If historical reconstruction is selected, classify every "
                "known later alteration.",
                source_b,
                f"commitment:{commitment_historical.commitment_id}",
                alteration.ref,
            ),
            _obligation(
                "pantheon.supply-new-building-brief",
                "If an inspired new building is selected, supply site, "
                "program, scale, structure, codes, and precedent principles.",
                source_b,
                f"commitment:{commitment_inspired.commitment_id}",
            ),
            _obligation(
                "pantheon.supply-voxel-spec",
                "If voxel translation is selected, supply source target, "
                "game version, scale, palette, error tolerance, and "
                "fidelity priorities.",
                source_b,
                f"commitment:{commitment_voxel.commitment_id}",
            ),
            _obligation(
                "pantheon.no-unlabeled-unknown-fill",
                "Use omission, neutral placeholders, or labeled variants "
                "for missing historical evidence.",
                source_b,
                unknowns.ref,
            ),
        ),
        add_dependencies=(
            _dependency(
                f"commitment:{commitment_historical.commitment_id}",
                f"commitment:{commitment_phase.commitment_id}",
                source_b,
                "conditions",
            ),
            _dependency(
                chronology.ref,
                f"commitment:{commitment_phase.commitment_id}",
                source_b,
                "qualifies",
            ),
            _dependency(
                f"commitment:{commitment_historical.commitment_id}",
                "obligation:pantheon.select-reference-phase",
                source_b,
                "activates",
            ),
            _dependency(
                f"commitment:{commitment_inspired.commitment_id}",
                "obligation:pantheon.supply-new-building-brief",
                source_b,
                "activates",
            ),
            _dependency(
                f"commitment:{commitment_voxel.commitment_id}",
                "obligation:pantheon.supply-voxel-spec",
                source_b,
                "activates",
            ),
        ),
        evidence_refs=(
            "evidence:E-PAN-01",
            "evidence:E-PAN-06",
            "evidence:E-PAN-07",
            "evidence:E-PAN-08",
            "evidence:E-PAN-09",
            "evidence:E-PAN-10",
        ),
    )
    transition_b = compile_decision_operator(state_a, operator_b)
    state_b = transition_b.state

    decision_c = (
        "P041-PANTHEON-C-COMPILE-CURRENT-PRECEDENT-PACKET"
    )
    source_c = f"decision:{decision_c}"
    role = _fact(
        StateDomain.DECISION,
        "current-precedent-role",
        "REFERENCE_ONLY_LOW_RES",
        source_c,
    )
    relation_graph = _fact(
        StateDomain.SEMANTIC,
        "current-precedent-relation-graph",
        {
            "nodes": [
                "portico",
                "vestibule",
                "rotunda",
                "dome",
                "oculus",
                "lower-perimeter-articulation",
            ],
            "directed_relations": [
                ["portico", "precedes", "vestibule"],
                ["vestibule", "compresses-transition-to", "rotunda"],
                ["rotunda", "supports-spatial-reading-of", "dome"],
                ["dome", "terminates-at", "oculus"],
                [
                    "lower-perimeter-articulation",
                    "surrounds",
                    "rotunda",
                ],
            ],
            "symmetry": {
                "principal_axis": "portico-to-rear-interior",
                "rotational_reading": (
                    "present-but-not-asserted-as-perfect"
                ),
            },
        },
        source_c,
    )
    numeric_envelope = _fact(
        StateDomain.PARAMETER,
        "current-precedent-numeric-envelope",
        {
            "rotunda_interior_diameter_m": 43.30,
            "interior_height_to_oculus_m": 43.30,
            "portico_column_total": 16,
            "portico_row_distribution": [8, 4, 4],
            "lower_spaces": 7,
            "aedicules": 8,
            "coffer_rings": 5,
        },
        source_c,
    )
    plan = _fact(
        StateDomain.DELIVERABLE,
        "current-precedent-output-plan-data",
        {
            "output_kind": "PROVENANCE_CODED_PLAN",
            "include": [
                "axial-entry-sequence",
                "portico-column-distribution",
                "rotunda-boundary",
                "seven-space-eight-aedicule-articulation",
            ],
            "exclude": [
                "inferred-statuary-identities",
                "unverified-ancient-finishes",
            ],
        },
        source_c,
    )
    axial_section = _fact(
        StateDomain.DELIVERABLE,
        "current-precedent-output-axial-section-data",
        {
            "output_kind": "PROVENANCE_CODED_AXIAL_SECTION",
            "stable_inputs": {
                "rotunda_diameter_m": 43.30,
                "interior_height_m": 43.30,
            },
            "unstable_inputs": {
                "oculus_diameter_m": [8.15, "about 9"]
            },
            "status": "PRELIMINARY_PENDING_OCULUS_RESOLUTION",
        },
        source_c,
    )
    exterior_axon = _fact(
        StateDomain.DELIVERABLE,
        "current-precedent-output-exterior-axon-data",
        {
            "output_kind": "LOW_RES_EXTERIOR_AXON",
            "include": [
                "portico-mass",
                "vestibule-connector",
                "cylindrical-rotunda-mass",
                "dome-mass",
            ],
            "required_overlay": [
                "alteration-mask",
                "unknown-lost-elements",
            ],
        },
        source_c,
    )
    voxel_prep = _fact(
        StateDomain.DELIVERABLE,
        "current-precedent-output-voxel-quantization-prep",
        {
            "status": (
                "PRELIMINARY_PENDING_TARGET_AND_OCULUS_RESOLUTION"
            ),
            "stable_metric_inputs": {
                "rotunda_diameter_m": 43.30,
                "interior_height_m": 43.30,
            },
            "unstable_metric_inputs": {
                "oculus_diameter_m": [8.15, "about 9"]
            },
            "missing_inputs": [
                "game-version",
                "blocks-per-meter",
                "block-palette",
                "maximum-error",
            ],
        },
        source_c,
    )
    usability_boundary = _fact(
        StateDomain.DECISION,
        "current-precedent-usability-boundary",
        {
            "final_scheme": False,
            "historical_reconstruction": False,
            "code_compliant_new_building": False,
            "voxel_build_ready": False,
            "generation_authority": False,
        },
        source_c,
    )
    commitment_low_res = _commitment(
        "pantheon.use-current-precedent-low-res",
        strength=CommitmentStrength.NEGOTIABLE,
        source_ref=source_c,
        subject_refs=(
            role.ref,
            relation_graph.ref,
            numeric_envelope.ref,
        ),
    )
    commitment_entry = _commitment(
        "pantheon.preserve-entry-sequence",
        strength=CommitmentStrength.HYPOTHESIS,
        source_ref=source_c,
        subject_refs=(relation_graph.ref,),
        evidence_refs=(
            "evidence:E-PAN-01",
            "evidence:E-PAN-02",
        ),
    )
    operator_c = DecisionOperator(
        decision_id=decision_c,
        decision_type="compile-low-resolution-current-precedent",
        base_state_digest=state_b.state_digest,
        authority_id=AGENT_AUTHORITY,
        intent=(
            "Compile a provenance-coded low-resolution reference packet "
            "without creating a design or reconstruction template."
        ),
        preconditions=(
            StateCondition(
                sequence.ref,
                ConditionComparator.EXISTS,
            ),
            StateCondition(
                target_status.ref,
                ConditionComparator.EQUALS,
                "UNSELECTED",
            ),
        ),
        add_facts=(
            role,
            relation_graph,
            numeric_envelope,
            plan,
            axial_section,
            exterior_axon,
            voxel_prep,
            usability_boundary,
        ),
        spawn_commitments=(
            commitment_low_res,
            commitment_entry,
        ),
        spawn_obligations=(
            _obligation(
                "pantheon.label-current-precedent-not-final",
                "Label every future visual as current-surviving precedent, "
                "low-resolution, and not final.",
                source_c,
                plan.ref,
                axial_section.ref,
                exterior_axon.ref,
            ),
            _obligation(
                "pantheon.overlay-alteration-provenance",
                "Show later replacements, lost elements, and unknown "
                "reconstructions with distinct provenance labels.",
                source_c,
                alteration.ref,
                exterior_axon.ref,
            ),
            _obligation(
                "pantheon.no-realizer-consumption-before-target",
                "Do not allow a realizer to consume this packet before "
                "target selection and branch blockers are resolved.",
                source_c,
                target_status.ref,
                usability_boundary.ref,
            ),
        ),
        add_dependencies=(
            _dependency(
                sequence.ref,
                relation_graph.ref,
                source_c,
                "derives",
            ),
            _dependency(
                lower.ref,
                relation_graph.ref,
                source_c,
                "derives",
            ),
            _dependency(
                diameter.ref,
                numeric_envelope.ref,
                source_c,
                "derives",
            ),
            _dependency(
                height.ref,
                numeric_envelope.ref,
                source_c,
                "derives",
            ),
            _dependency(
                columns.ref,
                numeric_envelope.ref,
                source_c,
                "derives",
            ),
            _dependency(
                lower.ref,
                numeric_envelope.ref,
                source_c,
                "derives",
            ),
            _dependency(
                coffers.ref,
                numeric_envelope.ref,
                source_c,
                "derives",
            ),
            _dependency(
                relation_graph.ref,
                plan.ref,
                source_c,
                "feeds",
            ),
            _dependency(
                numeric_envelope.ref,
                plan.ref,
                source_c,
                "feeds",
            ),
            _dependency(
                numeric_envelope.ref,
                axial_section.ref,
                source_c,
                "feeds",
            ),
            _dependency(
                oculus_mit.ref,
                axial_section.ref,
                source_c,
                "feeds",
            ),
            _dependency(
                oculus_italian.ref,
                axial_section.ref,
                source_c,
                "feeds",
            ),
            _dependency(
                relation_graph.ref,
                exterior_axon.ref,
                source_c,
                "feeds",
            ),
            _dependency(
                alteration.ref,
                exterior_axon.ref,
                source_c,
                "qualifies",
            ),
            _dependency(
                numeric_envelope.ref,
                voxel_prep.ref,
                source_c,
                "feeds",
            ),
            _dependency(
                oculus_mit.ref,
                voxel_prep.ref,
                source_c,
                "feeds",
            ),
            _dependency(
                oculus_italian.ref,
                voxel_prep.ref,
                source_c,
                "feeds",
            ),
            _dependency(
                voxel_branch.ref,
                voxel_prep.ref,
                source_c,
                "conditions",
            ),
        ),
        evidence_refs=(
            "evidence:E-PAN-01",
            "evidence:E-PAN-02",
            "evidence:E-PAN-03",
            "evidence:E-PAN-04",
            "evidence:E-PAN-05",
        ),
    )
    transition_c = compile_decision_operator(state_b, operator_c)
    state_c = transition_c.state

    decision_d = "P041-PANTHEON-D-REGISTER-OCULUS-CONFLICT"
    source_d = f"decision:{decision_d}"
    conflict = _fact(
        StateDomain.UNKNOWN,
        "oculus-diameter-conflict",
        {
            "status": "OPEN_CONFLICT",
            "quantity": "oculus-diameter",
            "alternatives": [
                {
                    "reading_ref": oculus_mit.ref,
                    "value": {
                        "number": 8.15,
                        "unit": "m",
                        "qualifier": "stated",
                    },
                },
                {
                    "reading_ref": oculus_italian.ref,
                    "value": {
                        "number": 9.0,
                        "unit": "m",
                        "qualifier": "approximately",
                    },
                },
            ],
            "possible_causes": [
                "measurement-definition",
                "measurement-position",
                "rounding",
                "source-transcription",
            ],
            "resolution": None,
        },
        source_d,
    )
    resolution = _fact(
        StateDomain.UNKNOWN,
        "oculus-diameter-resolution-status",
        "UNRESOLVED",
        source_d,
    )
    invalidation_boundary = _fact(
        StateDomain.DECISION,
        "oculus-conflict-invalidation-boundary",
        {
            "invalidated": [
                axial_section.ref,
                voxel_prep.ref,
            ],
            "preserved": [
                relation_graph.ref,
                numeric_envelope.ref,
                plan.ref,
                exterior_axon.ref,
                diameter.ref,
                height.ref,
                columns.ref,
            ],
        },
        source_d,
    )
    commitment_resolve = _commitment(
        "pantheon.resolve-oculus-by-survey-definition",
        strength=CommitmentStrength.NEGOTIABLE,
        source_ref=source_d,
        subject_refs=(conflict.ref, resolution.ref),
        evidence_refs=(
            "evidence:E-PAN-01",
            "evidence:E-PAN-03",
            "evidence:E-PAN-05",
        ),
    )
    commitment_range = _commitment(
        "pantheon.use-oculus-range-for-concept-only",
        strength=CommitmentStrength.HYPOTHESIS,
        source_ref=source_d,
        subject_refs=(
            conflict.ref,
            axial_section.ref,
            voxel_prep.ref,
        ),
        evidence_refs=(
            "evidence:E-PAN-01",
            "evidence:E-PAN-03",
        ),
    )
    operator_d = DecisionOperator(
        decision_id=decision_d,
        decision_type=(
            "register-measurement-conflict-and-local-invalidation"
        ),
        base_state_digest=state_c.state_digest,
        authority_id=AGENT_AUTHORITY,
        intent=(
            "Register incompatible oculus readings and invalidate only "
            "exact consumers without selecting a repair."
        ),
        preconditions=(
            StateCondition(
                oculus_mit.ref,
                ConditionComparator.EXISTS,
            ),
            StateCondition(
                oculus_italian.ref,
                ConditionComparator.EXISTS,
            ),
            StateCondition(
                axial_section.ref,
                ConditionComparator.EXISTS,
            ),
            StateCondition(
                voxel_prep.ref,
                ConditionComparator.EXISTS,
            ),
        ),
        add_facts=(
            conflict,
            resolution,
            invalidation_boundary,
        ),
        spawn_commitments=(
            commitment_resolve,
            commitment_range,
        ),
        spawn_obligations=(
            _obligation(
                "pantheon.resolve-oculus-measurement-definition",
                "Determine whether the two readings use different "
                "positions, definitions, rounding, or transcription; "
                "emit a resolved value or bounded range.",
                source_d,
                conflict.ref,
            ),
            _obligation(
                "pantheon.emit-resolved-oculus-fact",
                "After the measurement definition is resolved, emit a "
                "value, unit, tolerance, definition, and evidence refs.",
                source_d,
                resolution.ref,
                conflict.ref,
            ),
            _obligation(
                "pantheon.revalidate-axial-section-after-oculus",
                "Recompute only the oculus-dependent axial-section datum "
                "after resolution.",
                source_d,
                axial_section.ref,
            ),
            _obligation(
                "pantheon.revalidate-voxel-prep-after-oculus",
                "Recompute only oculus-dependent quantization after "
                "resolution and voxel specification.",
                source_d,
                voxel_prep.ref,
                "obligation:pantheon.supply-voxel-spec",
            ),
        ),
        add_dependencies=(
            _dependency(
                oculus_mit.ref,
                conflict.ref,
                source_d,
                "conflicts-in",
            ),
            _dependency(
                oculus_italian.ref,
                conflict.ref,
                source_d,
                "conflicts-in",
            ),
            _dependency(
                conflict.ref,
                resolution.ref,
                source_d,
                "blocks",
            ),
            _dependency(
                "obligation:pantheon.resolve-oculus-measurement-definition",
                "obligation:pantheon.emit-resolved-oculus-fact",
                source_d,
                "blocks",
            ),
        ),
        invalidates=(
            axial_section.ref,
            voxel_prep.ref,
        ),
        evidence_refs=(
            "evidence:E-PAN-01",
            "evidence:E-PAN-02",
            "evidence:E-PAN-03",
            "evidence:E-PAN-05",
        ),
    )
    transition_d = compile_decision_operator(state_c, operator_d)

    return (
        state0,
        (
            transition_a,
            transition_b,
            transition_c,
            transition_d,
        ),
        {
            "target_status_ref": target_status.ref,
            "axial_section_ref": axial_section.ref,
            "voxel_prep_ref": voxel_prep.ref,
            "preserved_output_refs": [
                relation_graph.ref,
                numeric_envelope.ref,
                plan.ref,
                exterior_axon.ref,
            ],
        },
    )


def bootstrap_fixture_envelope(root: Path) -> None:
    """Create the P036 envelope the compiled-state runs are derived from.

    Generic raw-request bootstrap: one project, one run, one raw request, one
    receipt that denies generation authority. No building knowledge.
    """

    repository = FilesystemProjectRepository.initialize(
        root,
        project_id=PROJECT_ID,
        initial_state={
            "schema": "CanonicalProjectState@1",
            "phase": "project_initialized",
            "authoritative_record_refs": [],
            "derived_record_refs": [],
        },
    )
    run = repository.create_run(BOOTSTRAP_RUN_ID)
    request = _put_retired_lane_json(
        repository,
        run=run,
        destination=PersistenceDestination(PersistenceArea.INPUT),
        record_kind="raw-request",
        payload={
            "schema": "RawProjectRequest@1",
            "prompt": RAW_REQUEST_PROMPT,
        },
    )
    _put_retired_lane_json(
        repository,
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
        record_kind="project-bootstrap",
        payload={
            "schema": "ProjectBootstrapReceipt@1",
            "project_id": PROJECT_ID,
            "run_id": run.run_id,
            "base": {
                "project_id": run.base.project_id,
                "version": run.base.version,
                "state_sha256": run.base.require_digest(),
            },
            "request_ref": request.uri,
            "synthetic_test": True,
            "generation_authority": False,
            "architectural_usability_proven": False,
            "derived_design_available": False,
        },
    )
    repository.verify()


def build_probe(project_root: Path) -> dict[str, object]:
    repository = FilesystemProjectRepository.open(project_root)
    head_before = repository.read_head()
    run_path = project_root / "runs" / RUN_ID / "run.json"
    if run_path.exists():
        raise RuntimeError(
            f"refusing to overwrite existing run: {run_path}"
        )
    bootstrap_run = repository.load_run(BOOTSTRAP_RUN_ID)
    input_refs = repository.list_json(
        run=bootstrap_run,
        destination=PersistenceDestination(PersistenceArea.INPUT),
    )
    if len(input_refs) != 1:
        raise RuntimeError(
            f"expected exactly one raw input, got {len(input_refs)}"
        )
    input_ref = input_refs[0]
    raw_request = repository.load_json(input_ref)
    planned_run = RunRef(
        project_id=head_before.project_id,
        run_id=RUN_ID,
        base=head_before,
    )
    state0, transitions, named_refs = _build_transitions(
        planned_run,
        input_ref.uri,
    )
    final_state = transitions[-1].state
    expected_invalidated = tuple(
        sorted(
            (
                named_refs["axial_section_ref"],
                named_refs["voxel_prep_ref"],
            )
        )
    )
    if (
        final_state.epoch != 4
        or final_state.phase != "research-brief"
        or final_state.invalidated_refs != expected_invalidated
        or transitions[-1].receipt.closure_invalidations
        != expected_invalidated
        or len(
            transitions[-1].receipt.generated_obligation_ids
        )
        != 2
        or final_state.value_for_ref(
            named_refs["target_status_ref"]
        )
        != "UNSELECTED"
        or not all(
            item.status is CommitmentStatus.PROPOSED
            for item in final_state.commitments
        )
        or repository.read_head() != head_before
    ):
        raise RuntimeError(
            "in-memory compilation did not meet the persistence boundary"
        )

    run = repository.create_run(RUN_ID, base=head_before)
    record_destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=RUN_ID,
    )
    branch_destination = PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=RUN_ID,
        branch_id=BRANCH_ID,
    )
    object_destination = PersistenceDestination(
        PersistenceArea.OBJECT
    )

    protocol_ref = _put_p042_record(
        repository,
        run=run,
        destination=record_destination,
        record_kind="v3-protocol-reference",
        payload={
            "schema": "V3ProtocolReference@1",
            "status": "reference_only",
            "authority": {
                "generation": False,
                "v2_pack": False,
                "gold_answer": False,
                "phase_implementation": False,
            },
            "logical_sources": [
                {
                    "ref": (
                        "legacy://ARCHFLOW_V3/archflow/governance/"
                        "artifacts.py#L227"
                    ),
                    "use": "run identity and routed artifact protocol",
                },
                {
                    "ref": (
                        "legacy://ARCHFLOW_V3/archflow/knowledge/"
                        "rad_corpus.py#L19"
                    ),
                    "use": "building-scoped provenance field pattern",
                },
                {
                    "ref": (
                        "legacy://ARCHFLOW_V3/archflow/blackboard/"
                        "derivation.py#L18-L60"
                    ),
                    "use": "per-round derivation record pattern",
                },
                {
                    "ref": (
                        "legacy://ARCHFLOW_V3/archflow/support/connectors/"
                        "sample_export.py#L88"
                    ),
                    "use": (
                        "visual evidence package only after geometry "
                        "materialization"
                    ),
                },
                {
                    "ref": (
                        "legacy://ARCHFLOW_V3/archflow/governance/"
                        "retirement_inventory.json#L3-L20"
                    ),
                    "use": "legacy composer quarantine",
                },
            ],
            "inherited_protocol": [
                "run-manifest",
                "facts",
                "obligations",
                "evidence",
                "derivation",
                "human-derivation",
            ],
            "excluded_generation_surfaces": [
                "archflow.examples._RULES",
                "example_planner",
                "compose_building",
                "roman_pantheon.py",
                "Pantheon Gold",
                "V2 phase wrappers",
            ],
            "replay_proven": False,
            "replay_note": (
                "Provenance and exact state compilation reload are "
                "retained; external model/tool replay is not claimed."
            ),
        },
    )
    evidence_ref = _put_p042_record(
        repository,
        run=run,
        destination=record_destination,
        record_kind="pantheon-evidence",
        payload={
            "schema": "PantheonEvidenceBundle@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "authored_by": AGENT_ID,
            "accessed_on": ACCESSED_ON,
            "raw_request_ref": _ref_payload(input_ref),
            "sources": list(SOURCES),
            "conflicts": [
                {
                    "quantity": "oculus-diameter",
                    "readings": [
                        {
                            "evidence_id": "E-PAN-03",
                            "value": "8.15 m",
                        },
                        {
                            "evidence_id": "E-PAN-01",
                            "value": "about 9 m",
                        },
                    ],
                    "status": "OPEN_CONFLICT",
                }
            ],
            "generation_authority": False,
        },
    )
    mapping_ref = _put_p042_record(
        repository,
        run=run,
        destination=record_destination,
        record_kind="agent-compiler-mapping",
        payload={
            "schema": "AgentCompilerMappingReceipt@1",
            "agent": {
                "id": AGENT_ID,
                "role": "research-capable project design author",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "authority": "proposal_only",
            },
            "compiler": (
                "OperationalMarkovState@2 + DecisionOperator@1 + "
                "StateDelta@1"
            ),
            "mappings": [
                {
                    "agent_field": (
                        "structured fact value + epistemic_status"
                    ),
                    "compiled_field": (
                        "StateFact.value canonical JSON string + source_ref"
                    ),
                    "loss": (
                        "epistemic status is retained in round metadata but "
                        "is not a first-class StateFact field"
                    ),
                },
                {
                    "agent_field": (
                        "BLOCKED and conditional obligation states"
                    ),
                    "compiled_field": (
                        "DesignObligation status=open with condition/blocker "
                        "text and subject refs"
                    ),
                    "loss": (
                        "P041 does not type conditional or blocked "
                        "obligation lifecycle"
                    ),
                },
                {
                    "agent_field": "phase transition request",
                    "compiled_field": "phase remains research-brief",
                    "loss": (
                        "P039 maturity gates are not implemented"
                    ),
                },
                {
                    "agent_field": (
                        "dependency edges from invalidated output to repair "
                        "obligation"
                    ),
                    "compiled_field": (
                        "retained as authored metadata, not executable "
                        "closure edges"
                    ),
                    "loss": (
                        "P041 closure would incorrectly mark downstream "
                        "obligation refs as invalidated"
                    ),
                },
            ],
            "exact_base_mechanical_binding": True,
            "external_execution_replay_proven": False,
            "geometry_materialized": False,
        },
    )
    initial_ref = _put_p042_record(
        repository,
        run=run,
        destination=branch_destination,
        record_kind="state-epoch-000",
        payload={
            "schema": "CompiledStateCheckpoint@1",
            "checkpoint": "initial",
            "state": state0.to_dict(),
        },
    )

    rationales = (
        "Compile only sourced project facts; preserve incompatible oculus "
        "readings and keep interpretations below secure-fact authority.",
        "The request is insufficient to choose historical reconstruction, "
        "an inspired new building, or voxel translation.",
        "Compile current-surviving relations and scale as a low-resolution "
        "communication packet, not a final scheme.",
        "The measurement conflict affects two exact consumers; preserve "
        "plan, relation graph, numeric envelope, and exterior axon data.",
    )
    original_semantics = (
        {
            "epistemic_statuses": [
                "SECURE_CURRENT_FACT",
                "SOURCE_READING_UNRESOLVED",
                "SECURE_META_FACT",
            ],
            "blocked_semantics": [],
        },
        {
            "epistemic_statuses": [
                "OPEN_DECISION",
                "CONDITIONAL_BRANCH",
                "OPEN_UNKNOWN_SET",
            ],
            "conditional_obligations": [
                "select-reference-phase",
                "select-alteration-policy",
                "supply-new-building-brief",
                "supply-voxel-spec",
            ],
        },
        {
            "epistemic_statuses": [
                "LOW_RES_DERIVATION",
                "PRELIMINARY_OUTPUT_DATA",
                "SECURE_USAGE_BOUNDARY",
            ],
            "generation_authority": False,
        },
        {
            "epistemic_statuses": [
                "REGISTERED_CONFLICT",
                "OPEN_DECISION",
                "SECURE_INVALIDATION_BOUNDARY",
            ],
            "original_blocked_obligations": [
                "emit-resolved-oculus-fact",
                "revalidate-axial-section-after-oculus",
                "revalidate-voxel-prep-after-oculus",
            ],
        },
    )
    states_before = (state0,) + tuple(
        transition.state for transition in transitions[:-1]
    )
    round_refs = []
    for index, (
        state_before,
        transition,
        rationale,
        semantics,
    ) in enumerate(
        zip(
            states_before,
            transitions,
            rationales,
            original_semantics,
        ),
        start=1,
    ):
        round_refs.append(
            _put_p042_record(
                repository,
                run=run,
                destination=branch_destination,
                record_kind=f"agent-round-{index:02d}",
                payload={
                    "schema": "AgentDecisionRound@1",
                    "round": index,
                    "formula": (
                        "Q_t = F_t(D_v,k, K_t, O_t, A_t^valid)"
                    ),
                    "agent": {
                        "id": AGENT_ID,
                        "authority": "proposal_only",
                    },
                    "phase": state_before.phase,
                    "state_before": state_before.to_dict(),
                    "facts_used": list(
                        transition.operator.evidence_refs
                    ),
                    "legal_menu": [
                        transition.operator.decision_type,
                        "declare-unresolved",
                    ],
                    "agent_rationale": rationale,
                    "agent_authored_semantics": semantics,
                    "operator": transition.operator.to_dict(),
                    "delta": transition.delta.to_dict(),
                    "closure": transition.receipt.to_dict(),
                    "state_after": transition.state.to_dict(),
                    "phase_transition_requested": False,
                    "canonical_write_authority": False,
                },
            )
        )

    facts_ref = _put_p042_record(
        repository,
        run=run,
        destination=record_destination,
        record_kind="building-facts",
        payload={
            "schema": "BuildingFactsArchive@1",
            "state_digest": final_state.state_digest,
            "facts": [
                item.to_dict() for item in final_state.facts
            ],
            "invalidated_refs": list(
                final_state.invalidated_refs
            ),
            "case_scope": f"project:{PROJECT_ID}",
            "framework_default_authority": False,
        },
    )
    obligations_ref = _put_p042_record(
        repository,
        run=run,
        destination=record_destination,
        record_kind="building-obligations",
        payload={
            "schema": "BuildingObligationsArchive@1",
            "state_digest": final_state.state_digest,
            "commitments": [
                item.to_dict() for item in final_state.commitments
            ],
            "obligations": [
                item.to_dict() for item in final_state.obligations
            ],
            "original_richer_status_retained_in": (
                _ref_payload(mapping_ref)
            ),
            "all_commitments_proposed": True,
        },
    )
    state_chain = [state0.state_digest] + [
        transition.state.state_digest for transition in transitions
    ]
    derivation_ref = _put_p042_record(
        repository,
        run=run,
        destination=record_destination,
        record_kind="derivation",
        payload={
            "schema": "DerivationArchive@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "branch_id": BRANCH_ID,
            "base": {
                "project_id": head_before.project_id,
                "version": head_before.version,
                "state_sha256": head_before.state_sha256,
            },
            "initial_state_ref": _ref_payload(initial_ref),
            "round_refs": [
                _ref_payload(ref) for ref in round_refs
            ],
            "state_digest_chain": state_chain,
            "final_state_digest": final_state.state_digest,
            "maturity": (
                "RESEARCH_BRIEF_COMPILED_WITH_OPEN_BLOCKERS"
            ),
            "phase": "research-brief",
            "next_phase_allowed": False,
            "blocking_decisions": [
                "pantheon.confirm-entity-identity",
                "pantheon.select-target-kind",
                "pantheon.resolve-oculus-measurement-definition",
                "pantheon.emit-resolved-oculus-fact",
            ],
            "invalidated_outputs": list(expected_invalidated),
            "preserved_outputs": named_refs[
                "preserved_output_refs"
            ],
            "generation_authority": False,
            "architectural_usability_proven": False,
            "external_execution_replay_proven": False,
        },
    )
    markdown = f"""# Pantheon compiled-state derivation

- Project: `{run.project_id}`
- Run: `{run.run_id}`
- Agent author: `{AGENT_ID}` (proposal authority only)
- Canonical base: version {head_before.version}, `{head_before.state_sha256}`
- Final operational epoch: {final_state.epoch}
- Maturity: `RESEARCH_BRIEF_COMPILED_WITH_OPEN_BLOCKERS`

## What the compiler established

1. It retained sourced current facts and kept disputed historical interpretation separate.
2. It preserved three mutually exclusive target branches without selecting one for the user.
3. It compiled a low-resolution current-precedent relation and scale packet.
4. It registered the 8.15 m versus about 9 m oculus conflict and invalidated only the axial-section datum and voxel-quantization preparation.

## Why the run stops here

Entity identity and target kind are not authorized, the oculus measurement definition is unresolved, P039 design-maturity gates are not implemented, and no program/site/voxel specification exists. Therefore this run has no phase-transition, geometry, generation, canonical-write, or architectural-usability authority.

## V3 protocol inheritance

The probe retains facts, obligations, evidence, derivation, and a manifest. V3 Pack, example composer, Pantheon Gold, and phase wrappers are reference-only. The V3 six-item visual package becomes mandatory only after geometry is materialized; it is intentionally absent here.
"""
    human_ref = repository.ingest(
        run=run,
        destination=object_destination,
        artifact_id="derivation-human",
        media_type="text/markdown; charset=utf-8",
        source=io.BytesIO(markdown.encode("utf-8")),
    )

    retained_refs = [
        protocol_ref,
        evidence_ref,
        mapping_ref,
        facts_ref,
        obligations_ref,
        derivation_ref,
        initial_ref,
        *round_refs,
    ]
    manifest_ref = _put_p042_record(
        repository,
        run=run,
        destination=record_destination,
        record_kind="compiled-dossier-manifest",
        payload={
            "schema": "CompiledDesignDossierManifest@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "branch_id": BRANCH_ID,
            "agent": AGENT_ID,
            "raw_request_ref": _ref_payload(input_ref),
            "records": [
                _ref_payload(ref) for ref in retained_refs
            ],
            "artifacts": [_ref_payload(human_ref)],
            "final_state_digest": final_state.state_digest,
            "final_sufficient_digest": (
                final_state.sufficient_digest
            ),
            "maturity": (
                "RESEARCH_BRIEF_COMPILED_WITH_OPEN_BLOCKERS"
            ),
            "phase": "research-brief",
            "phase_gate_passed": False,
            "next_phase_allowed": False,
            "derived_design_available": True,
            "derived_design_scope": (
                "sourced conditional research dossier and low-resolution "
                "precedent packet only"
            ),
            "generation_authority": False,
            "geometry_materialized": False,
            "architectural_usability_proven": False,
            "external_execution_replay_proven": False,
            "canonical_promoted": False,
            "visual_package": {
                "required_when_geometry_materialized": [
                    "iso",
                    "section_A",
                    "section_B",
                    "elevation",
                    "schem",
                    "README",
                ],
                "present": [],
                "reason": (
                    "compiled research brief only; no geometry was "
                    "materialized"
                ),
            },
            "stop_reason": (
                "Target authority, measurement definition, downstream "
                "maturity gates, site/program, and realization inputs "
                "remain unresolved."
            ),
        },
    )

    reopened = FilesystemProjectRepository.open(project_root)
    reopened_run = reopened.load_run(RUN_ID)
    for destination in (
        record_destination,
        branch_destination,
    ):
        for record_ref in reopened.list_json(
            run=reopened_run,
            destination=destination,
        ):
            reopened.load_json(record_ref)
    if reopened.read_head() != head_before:
        raise RuntimeError("compiled probe unexpectedly advanced HEAD")
    if reopened.load_json(input_ref) != raw_request:
        raise RuntimeError("compiled probe changed its raw request")
    object_bytes = (
        project_root / human_ref.relative_path
    ).read_bytes()
    if hashlib.sha256(object_bytes).hexdigest() != human_ref.sha256:
        raise RuntimeError("human derivation artifact digest mismatch")
    return {
        "manifest_ref": _ref_payload(manifest_ref),
        "final_state_digest": final_state.state_digest,
        "round_count": len(round_refs),
        "head_version": head_before.version,
    }


def build_m009_successor_probe(project_root: Path) -> dict[str, object]:
    """Explicitly recompile P042 evidence without mutating its legacy run."""

    repository = FilesystemProjectRepository.open(project_root)
    head_before = repository.read_head()
    run_path = project_root / "runs" / M009_RUN_ID / "run.json"
    if run_path.exists():
        raise RuntimeError(
            f"refusing to overwrite existing run: {run_path}"
        )
    source_run_root = project_root / "runs" / RUN_ID
    source_tree_digest = _tree_digest(source_run_root)
    source_run = repository.load_run(RUN_ID)
    source_record_destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=RUN_ID,
    )
    source_branch_destination = PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=RUN_ID,
        branch_id=BRANCH_ID,
    )
    source_records = [
        (ref, repository.load_json(ref))
        for ref in repository.list_json(
            run=source_run,
            destination=source_record_destination,
        )
    ]
    evidence_ref, _ = next(
        pair
        for pair in source_records
        if pair[1]["schema"] == "PantheonEvidenceBundle@1"
    )
    source_rounds = [
        (ref, repository.load_json(ref))
        for ref in repository.list_json(
            run=source_run,
            destination=source_branch_destination,
        )
        if repository.load_json(ref)["schema"] == "AgentDecisionRound@1"
    ]
    source_round_ref, source_round = max(
        source_rounds,
        key=lambda pair: pair[1]["round"],
    )
    legacy_state = load_operational_state_record(
        source_round["state_after"]
    )
    if not isinstance(legacy_state, LegacyOperationalMarkovStateV2):
        raise RuntimeError("P042 source state is no longer a V2 record")

    bootstrap_run = repository.load_run(BOOTSTRAP_RUN_ID)
    input_refs = repository.list_json(
        run=bootstrap_run,
        destination=PersistenceDestination(PersistenceArea.INPUT),
    )
    if len(input_refs) != 1:
        raise RuntimeError("successor requires one immutable raw input")
    input_ref = input_refs[0]
    planned_run = RunRef(
        project_id=head_before.project_id,
        run_id=M009_RUN_ID,
        base=head_before,
    )
    source_uri = evidence_ref.uri
    source_round_uri = source_round_ref.uri
    reading_mit = StateFact(
        domain=StateDomain.GEOMETRY,
        key="oculus-reading-mit",
        value={
            "quantity": "diameter",
            "value": 8.15,
            "unit": "m",
            "measurement_definition": "unspecified",
        },
        source_ref=source_uri,
        epistemic_status=FactEpistemicStatus.OBSERVED,
        qualification="Institutional catalog reading retained from P042.",
    )
    reading_ministry = StateFact(
        domain=StateDomain.GEOMETRY,
        key="oculus-reading-italian-museums",
        value={
            "quantity": "diameter",
            "value": 9,
            "approximate": True,
            "unit": "m",
            "measurement_definition": "unspecified",
        },
        source_ref=source_uri,
        epistemic_status=FactEpistemicStatus.OBSERVED,
        qualification="Approximate ministry reading retained from P042.",
    )
    axial_output = StateFact(
        domain=StateDomain.DELIVERABLE,
        key="current-precedent-output-axial-section-data",
        value={
            "status": "preliminary",
            "measurement_dependency": "oculus-diameter",
        },
        source_ref=source_round_uri,
        epistemic_status=FactEpistemicStatus.DERIVED,
    )
    voxel_output = StateFact(
        domain=StateDomain.DELIVERABLE,
        key="current-precedent-output-voxel-quantization-prep",
        value={
            "status": "preliminary",
            "measurement_dependency": "oculus-diameter",
        },
        source_ref=source_round_uri,
        epistemic_status=FactEpistemicStatus.DERIVED,
    )
    unrelated_output = StateFact(
        domain=StateDomain.DELIVERABLE,
        key="current-precedent-output-plan-relations",
        value={
            "status": "current",
            "measurement_dependency": None,
        },
        source_ref=source_round_uri,
        epistemic_status=FactEpistemicStatus.DERIVED,
    )
    resolve = DesignObligation(
        obligation_id="resolve-oculus-measurement-definition",
        statement=(
            "Resolve which measurement definition governs the two readings."
        ),
        source_ref=source_uri,
        subject_refs=(reading_mit.ref, reading_ministry.ref),
    )
    emit = DesignObligation(
        obligation_id="emit-resolved-oculus-fact",
        statement="Emit a resolved fact only after the definition is known.",
        source_ref=source_round_uri,
        status=ObligationStatus.BLOCKED,
        subject_refs=(reading_mit.ref, reading_ministry.ref),
        blocked_by=("obligation:resolve-oculus-measurement-definition",),
    )
    repair_axial = DesignObligation(
        obligation_id="revalidate-axial-section-after-oculus",
        statement="Revalidate the affected axial-section datum.",
        source_ref=source_round_uri,
        status=ObligationStatus.BLOCKED,
        subject_refs=(axial_output.ref,),
        blocked_by=("obligation:emit-resolved-oculus-fact",),
    )
    repair_voxel = DesignObligation(
        obligation_id="revalidate-voxel-prep-after-oculus",
        statement="Revalidate voxel preparation after the resolved fact exists.",
        source_ref=source_round_uri,
        status=ObligationStatus.BLOCKED,
        subject_refs=(voxel_output.ref,),
        blocked_by=("obligation:emit-resolved-oculus-fact",),
    )
    conflict_ref = "fact:unknown:oculus-measurement-conflict"
    dependencies = (
        DependencyEdge(
            upstream_ref=reading_mit.ref,
            downstream_ref=conflict_ref,
            relation="contributes-reading",
            source_ref=source_uri,
            effect=DependencyEffect.SUPPORTS_ONLY,
        ),
        DependencyEdge(
            upstream_ref=reading_ministry.ref,
            downstream_ref=conflict_ref,
            relation="contributes-reading",
            source_ref=source_uri,
            effect=DependencyEffect.SUPPORTS_ONLY,
        ),
        DependencyEdge(
            upstream_ref=conflict_ref,
            downstream_ref=axial_output.ref,
            relation="requires-resolution-before-current",
            source_ref=source_round_uri,
            effect=DependencyEffect.REQUIRES_REVALIDATION,
        ),
        DependencyEdge(
            upstream_ref=conflict_ref,
            downstream_ref=voxel_output.ref,
            relation="requires-resolution-before-current",
            source_ref=source_round_uri,
            effect=DependencyEffect.REQUIRES_REVALIDATION,
        ),
        DependencyEdge(
            upstream_ref="obligation:resolve-oculus-measurement-definition",
            downstream_ref="obligation:emit-resolved-oculus-fact",
            relation="must-resolve-before",
            source_ref=source_round_uri,
            effect=DependencyEffect.BLOCKS,
        ),
        DependencyEdge(
            upstream_ref="obligation:emit-resolved-oculus-fact",
            downstream_ref="obligation:revalidate-axial-section-after-oculus",
            relation="must-resolve-before",
            source_ref=source_round_uri,
            effect=DependencyEffect.BLOCKS,
        ),
        DependencyEdge(
            upstream_ref="obligation:emit-resolved-oculus-fact",
            downstream_ref="obligation:revalidate-voxel-prep-after-oculus",
            relation="must-resolve-before",
            source_ref=source_round_uri,
            effect=DependencyEffect.BLOCKS,
        ),
    )
    state0 = OperationalMarkovState(
        branch=BranchRef(
            run=planned_run,
            branch_id=BRANCH_ID,
            epoch=0,
        ),
        compiler_version="operational-markov-compiler-m009",
        phase="research-brief",
        facts=(
            reading_mit,
            reading_ministry,
            axial_output,
            voxel_output,
            unrelated_output,
        ),
        obligations=(resolve, emit, repair_axial, repair_voxel),
        dependencies=dependencies,
        evidence_refs=(
            input_ref.uri,
            source_uri,
            source_round_uri,
        ),
    )
    conflict = StateFact(
        domain=StateDomain.UNKNOWN,
        key="oculus-measurement-conflict",
        value={
            "quantity": "diameter",
            "readings": [
                reading_mit.python_value,
                reading_ministry.python_value,
            ],
            "resolution": None,
        },
        source_ref=source_uri,
        epistemic_status=FactEpistemicStatus.DISPUTED,
        qualification=(
            "The readings use unresolved measurement definitions; neither "
            "is compiled as the authoritative design input."
        ),
    )
    operator = DecisionOperator(
        decision_id="M009-PANTHEON-REGISTER-MEASUREMENT-CONFLICT",
        decision_type="register-evidence-conflict",
        base_state_digest=state0.state_digest,
        authority_id=AGENT_AUTHORITY,
        intent=(
            "Register the disputed measurement and stale only its exact "
            "derived consumers."
        ),
        preconditions=(
            StateCondition(
                ref=reading_mit.ref,
                comparator=ConditionComparator.EXISTS,
            ),
            StateCondition(
                ref=reading_ministry.ref,
                comparator=ConditionComparator.EXISTS,
            ),
        ),
        add_facts=(conflict,),
        invalidates=(axial_output.ref, voxel_output.ref),
        evidence_refs=(source_uri, source_round_uri),
    )
    transition = compile_decision_operator(state0, operator)
    expected_invalidated = tuple(
        sorted((axial_output.ref, voxel_output.ref))
    )
    if transition.state.invalidated_refs != expected_invalidated:
        raise RuntimeError("M009 successor invalidation closure is not local")
    statuses = {
        item.obligation_id: item.status
        for item in transition.state.obligations
    }
    if (
        statuses["resolve-oculus-measurement-definition"]
        is not ObligationStatus.OPEN
        or statuses["emit-resolved-oculus-fact"]
        is not ObligationStatus.BLOCKED
        or statuses["revalidate-axial-section-after-oculus"]
        is not ObligationStatus.BLOCKED
        or statuses["revalidate-voxel-prep-after-oculus"]
        is not ObligationStatus.BLOCKED
        or transition.state.value_for_ref(unrelated_output.ref)
        != unrelated_output.python_value
    ):
        raise RuntimeError("M009 successor obligation readiness drifted")

    run = repository.create_run(M009_RUN_ID, base=head_before)
    record_destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=M009_RUN_ID,
    )
    branch_destination = PersistenceDestination(
        PersistenceArea.RUN_BRANCH,
        run_id=M009_RUN_ID,
        branch_id=BRANCH_ID,
    )
    migration_ref = _put_retired_lane_json(
        repository,
        run=run,
        destination=record_destination,
        record_kind="explicit-recompile-receipt",
        payload={
            "schema": "PantheonM009ExplicitRecompileReceipt@1",
            "source_run_id": RUN_ID,
            "source_run_tree_sha256": source_tree_digest,
            "source_state_ref": _ref_payload(source_round_ref),
            "source_state_schema": legacy_state.SCHEMA,
            "target_state_schema": state0.SCHEMA,
            "automatic_migration": False,
            "recompile_from_authoritative_evidence": True,
            "fail_closed_reason": (
                "V2 lacks first-class epistemic status, conditional "
                "obligations, and typed dependency effects."
            ),
        },
    )
    initial_ref = _put_retired_lane_json(
        repository,
        run=run,
        destination=branch_destination,
        record_kind="state-epoch-000",
        payload={
            "schema": "M009StateCheckpoint@1",
            "checkpoint": "initial",
            "state": state0.to_dict(),
        },
    )
    round_ref = _put_retired_lane_json(
        repository,
        run=run,
        destination=branch_destination,
        record_kind="decision-round-01",
        payload={
            "schema": "M009DecisionRound@1",
            "round": 1,
            "state_before": state0.to_dict(),
            "operator": operator.to_dict(),
            "delta": transition.delta.to_dict(),
            "closure": transition.receipt.to_dict(),
            "state_after": transition.state.to_dict(),
            "claim": (
                "A disputed source fact locally stales two exact consumers; "
                "this is not geometry or usability proof."
            ),
        },
    )
    final_ref = _put_retired_lane_json(
        repository,
        run=run,
        destination=record_destination,
        record_kind="operational-state-final",
        payload={
            "schema": "M009OperationalStateArchive@1",
            "state": transition.state.to_dict(),
            "open_obligations": sorted(
                item.obligation_id
                for item in transition.state.obligations
                if item.status is ObligationStatus.OPEN
            ),
            "blocked_obligations": sorted(
                item.obligation_id
                for item in transition.state.obligations
                if item.status is ObligationStatus.BLOCKED
            ),
            "stale_deliverables": list(
                transition.state.invalidated_refs
            ),
            "current_deliverables": [unrelated_output.ref],
        },
    )
    manifest_ref = _put_retired_lane_json(
        repository,
        run=run,
        destination=record_destination,
        record_kind="m009-successor-manifest",
        payload={
            "schema": "PantheonM009SuccessorManifest@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "branch_id": BRANCH_ID,
            "base": {
                "project_id": run.base.project_id,
                "version": run.base.version,
                "state_sha256": run.base.state_sha256,
            },
            "source_run_id": RUN_ID,
            "source_run_tree_sha256": source_tree_digest,
            "records": [
                _ref_payload(migration_ref),
                _ref_payload(initial_ref),
                _ref_payload(round_ref),
                _ref_payload(final_ref),
            ],
            "final_state_digest": transition.state.state_digest,
            "final_sufficient_digest": transition.state.sufficient_digest,
            "stale_deliverables": list(expected_invalidated),
            "current_deliverables": [unrelated_output.ref],
            "geometry_materialized": False,
            "architectural_usability_proven": False,
            "canonical_promoted": False,
        },
    )

    reopened = FilesystemProjectRepository.open(project_root)
    reopened_run = reopened.load_run(M009_RUN_ID)
    for destination in (record_destination, branch_destination):
        for record_ref in reopened.list_json(
            run=reopened_run,
            destination=destination,
        ):
            reopened.load_json(record_ref)
    if reopened.read_head() != head_before:
        raise RuntimeError("M009 successor unexpectedly advanced HEAD")
    if _tree_digest(source_run_root) != source_tree_digest:
        raise RuntimeError("M009 successor modified the immutable P042 run")
    return {
        "manifest_ref": _ref_payload(manifest_ref),
        "final_state_digest": transition.state.state_digest,
        "source_run_tree_sha256": source_tree_digest,
        "head_version": head_before.version,
    }


def build_fixture_project(project_root: Path) -> dict[str, object]:
    """Materialise the whole fixture project: envelope, P042 run, M009 run."""

    bootstrap_fixture_envelope(project_root)
    return {
        "project_id": PROJECT_ID,
        "root": str(project_root),
        RUN_ID: build_probe(project_root),
        M009_RUN_ID: build_m009_successor_probe(project_root),
    }


_FIXTURE_TEMPDIR: tempfile.TemporaryDirectory | None = None
_FIXTURE_ROOT: Path | None = None


def fixture_project_root() -> Path:
    """Build the fixture once per interpreter and reuse it across classes.

    The two classes read one project: the M009 run is derived from the
    compiled-state run in the same envelope, so they cannot be bootstrapped
    independently.
    """

    global _FIXTURE_TEMPDIR, _FIXTURE_ROOT
    if _FIXTURE_ROOT is None:
        _FIXTURE_TEMPDIR = tempfile.TemporaryDirectory(
            prefix="archflow-pantheon-fixture-",
            ignore_cleanup_errors=True,
        )
        root = Path(_FIXTURE_TEMPDIR.name) / PROJECT_ID
        build_fixture_project(root)
        _FIXTURE_ROOT = root
    return _FIXTURE_ROOT


def tearDownModule() -> None:
    global _FIXTURE_TEMPDIR, _FIXTURE_ROOT
    if _FIXTURE_TEMPDIR is not None:
        _FIXTURE_TEMPDIR.cleanup()
    _FIXTURE_TEMPDIR = None
    _FIXTURE_ROOT = None


class PantheonCompiledStateProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project_root = fixture_project_root()
        cls.repository = FilesystemProjectRepository.open(cls.project_root)
        cls.run_ref = cls.repository.load_run(RUN_ID)
        cls.record_destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=RUN_ID,
        )
        cls.branch_destination = PersistenceDestination(
            PersistenceArea.RUN_BRANCH,
            run_id=RUN_ID,
            branch_id=BRANCH_ID,
        )
        cls.records = {
            payload["schema"]: (ref, payload)
            for ref in cls.repository.list_json(
                run=cls.run_ref,
                destination=cls.record_destination,
            )
            for payload in (cls.repository.load_json(ref),)
        }
        cls.branch_records = [
            (ref, cls.repository.load_json(ref))
            for ref in cls.repository.list_json(
                run=cls.run_ref,
                destination=cls.branch_destination,
            )
        ]

    def test_raw_input_is_unchanged_and_canonical_head_is_not_promoted(
        self,
    ) -> None:
        bootstrap = self.repository.load_run("bootstrap-001")
        input_refs = self.repository.list_json(
            run=bootstrap,
            destination=PersistenceDestination(
                PersistenceArea.INPUT
            ),
        )
        self.assertEqual(len(input_refs), 1)
        raw = self.repository.load_json(input_refs[0])
        self.assertEqual(
            raw["prompt"],
            (
                "In a voxel sandbox, design and build the Pantheon step "
                "by step. First retrieve reliable evidence, then derive "
                "its use, scale, spatial relations, construction, and "
                "materials from the current state without preset "
                "building data."
            ),
        )
        self.assertFalse(
            any(char.isdigit() for char in raw["prompt"])
        )
        self.assertEqual(self.repository.read_head().version, 0)
        self.assertEqual(
            self.run_ref.base,
            self.repository.read_head(),
        )

    def test_evidence_has_provenance_certainty_and_open_unknowns(
        self,
    ) -> None:
        evidence = self.records["PantheonEvidenceBundle@1"][1]
        self.assertEqual(evidence["authored_by"], AGENT_ID)
        self.assertGreaterEqual(len(evidence["sources"]), 10)
        for source in evidence["sources"]:
            self.assertTrue(source["url"].startswith("https://"))
            self.assertTrue(source["publisher"])
            self.assertEqual(source["accessed_on"], ACCESSED_ON)
            self.assertTrue(source["claims"])
            self.assertTrue(source["certainty"])
            self.assertTrue(source["unresolved_status"])
        self.assertEqual(
            evidence["conflicts"][0]["status"],
            "OPEN_CONFLICT",
        )
        self.assertFalse(evidence["generation_authority"])

    def test_exact_base_chain_and_local_dependency_closure(
        self,
    ) -> None:
        initial = next(
            payload
            for _, payload in self.branch_records
            if payload["schema"] == "CompiledStateCheckpoint@1"
        )
        rounds = sorted(
            (
                payload
                for _, payload in self.branch_records
                if payload["schema"] == "AgentDecisionRound@1"
            ),
            key=lambda payload: payload["round"],
        )
        self.assertEqual(len(rounds), 4)
        previous_digest = initial["state"]["state_digest"]
        for expected_round, round_payload in enumerate(
            rounds,
            start=1,
        ):
            self.assertEqual(
                round_payload["round"],
                expected_round,
            )
            self.assertEqual(
                round_payload["state_before"]["state_digest"],
                previous_digest,
            )
            self.assertEqual(
                round_payload["operator"]["base_state_digest"],
                previous_digest,
            )
            self.assertEqual(
                round_payload["delta"]["base_state_digest"],
                previous_digest,
            )
            self.assertEqual(
                round_payload["closure"]["base_state_digest"],
                previous_digest,
            )
            result_digest = round_payload["state_after"][
                "state_digest"
            ]
            self.assertEqual(
                round_payload["closure"]["result_state_digest"],
                result_digest,
            )
            previous_digest = result_digest
        final_round = rounds[-1]
        expected_invalidations = sorted(
            [
                (
                    "fact:deliverable:"
                    "current-precedent-output-axial-section-data"
                ),
                (
                    "fact:deliverable:"
                    "current-precedent-output-voxel-quantization-prep"
                ),
            ]
        )
        self.assertEqual(
            final_round["closure"]["closure_invalidations"],
            expected_invalidations,
        )
        self.assertEqual(
            final_round["state_after"]["invalidated_refs"],
            expected_invalidations,
        )
        self.assertEqual(
            len(
                final_round["closure"][
                    "generated_obligation_ids"
                ]
            ),
            2,
        )
        self.assertEqual(final_round["state_after"]["branch"]["epoch"], 4)

    def test_restart_loads_content_addressed_dossier_without_overclaim(
        self,
    ) -> None:
        manifest = self.records[
            "CompiledDesignDossierManifest@1"
        ][1]
        self.assertEqual(
            manifest["maturity"],
            "RESEARCH_BRIEF_COMPILED_WITH_OPEN_BLOCKERS",
        )
        self.assertEqual(manifest["phase"], "research-brief")
        self.assertFalse(manifest["phase_gate_passed"])
        self.assertFalse(manifest["next_phase_allowed"])
        self.assertFalse(manifest["generation_authority"])
        self.assertFalse(manifest["geometry_materialized"])
        self.assertFalse(
            manifest["architectural_usability_proven"]
        )
        self.assertFalse(
            manifest["external_execution_replay_proven"]
        )
        self.assertFalse(manifest["canonical_promoted"])
        self.assertEqual(
            manifest["visual_package"]["present"],
            [],
        )
        for retained in (
            manifest["records"] + manifest["artifacts"]
        ):
            path = self.project_root / retained["relative_path"]
            self.assertTrue(path.is_file())
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                retained["sha256"],
            )

    def test_v3_protocol_is_reference_only_and_mapping_loss_is_named(
        self,
    ) -> None:
        protocol = self.records["V3ProtocolReference@1"][1]
        self.assertEqual(protocol["status"], "reference_only")
        self.assertFalse(protocol["authority"]["generation"])
        self.assertFalse(protocol["authority"]["v2_pack"])
        self.assertFalse(protocol["authority"]["gold_answer"])
        self.assertFalse(protocol["replay_proven"])
        mapping = self.records["AgentCompilerMappingReceipt@1"][1]
        self.assertTrue(mapping["exact_base_mechanical_binding"])
        losses = " ".join(
            item["loss"] for item in mapping["mappings"]
        )
        self.assertIn("epistemic status", losses)
        self.assertIn("conditional", losses)
        self.assertIn("P039", losses)

    def test_case_answers_do_not_enter_framework_or_generation_areas(
        self,
    ) -> None:
        # Tokens the synthetic fixture actually writes. The guard is one-way:
        # the fixture may name the case, framework code may not.
        banned = (
            "pantheon",
            "43.30",
            "oculus-reading-mit",
            "P041-PANTHEON",
            "current-portico-columns",
        )
        fixture_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(self.project_root.rglob("*"))
            if path.is_file()
        )
        for token in banned:
            self.assertIn(
                token,
                fixture_text,
                f"guard token {token!r} is no longer written by the fixture",
            )
        for path in (REPO_ROOT / "archflow").rglob("*"):
            if path.suffix.lower() not in {".py", ".json", ".md"}:
                continue
            text = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, text, str(path))
        self.assertFalse(
            any(self.project_root.rglob("*.py")),
            "the data probe must not contain executable case code",
        )
        run_root = self.project_root / "runs" / RUN_ID
        self.assertFalse(any((run_root / "candidates").rglob("*.json")))
        self.assertFalse(any((run_root / "reviews").rglob("*.json")))


class PantheonM009SuccessorProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project_root = fixture_project_root()
        cls.repository = FilesystemProjectRepository.open(cls.project_root)
        cls.run_ref = cls.repository.load_run(M009_RUN_ID)
        cls.record_destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=M009_RUN_ID,
        )
        cls.branch_destination = PersistenceDestination(
            PersistenceArea.RUN_BRANCH,
            run_id=M009_RUN_ID,
            branch_id=BRANCH_ID,
        )
        cls.records = {
            payload["schema"]: (ref, payload)
            for ref in cls.repository.list_json(
                run=cls.run_ref,
                destination=cls.record_destination,
            )
            for payload in (cls.repository.load_json(ref),)
        }
        cls.branch_records = {
            payload["schema"]: (ref, payload)
            for ref in cls.repository.list_json(
                run=cls.run_ref,
                destination=cls.branch_destination,
            )
            for payload in (cls.repository.load_json(ref),)
        }

    def test_successor_preserves_p042_and_canonical_head(self) -> None:
        manifest = self.records["PantheonM009SuccessorManifest@1"][1]
        source_root = self.project_root / "runs" / RUN_ID

        self.assertEqual(
            _tree_digest(source_root),
            manifest["source_run_tree_sha256"],
        )
        self.assertEqual(self.repository.read_head().version, 0)
        self.assertEqual(
            self.run_ref.base,
            self.repository.read_head(),
        )
        self.assertFalse(manifest["canonical_promoted"])
        self.assertFalse(manifest["geometry_materialized"])
        self.assertFalse(manifest["architectural_usability_proven"])
        for retained in manifest["records"]:
            path = self.project_root / retained["relative_path"]
            self.assertTrue(path.is_file())
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                retained["sha256"],
            )

    def test_explicit_recompile_replaces_unsafe_automatic_migration(
        self,
    ) -> None:
        receipt = self.records[
            "PantheonM009ExplicitRecompileReceipt@1"
        ][1]

        self.assertEqual(
            receipt["source_state_schema"],
            "OperationalMarkovState@2",
        )
        self.assertEqual(
            receipt["target_state_schema"],
            "OperationalMarkovState@3",
        )
        self.assertFalse(receipt["automatic_migration"])
        self.assertTrue(receipt["recompile_from_authoritative_evidence"])

        source_run = self.repository.load_run(RUN_ID)
        source_branch = PersistenceDestination(
            PersistenceArea.RUN_BRANCH,
            run_id=RUN_ID,
            branch_id=BRANCH_ID,
        )
        source_rounds = [
            self.repository.load_json(ref)
            for ref in self.repository.list_json(
                run=source_run,
                destination=source_branch,
            )
            if self.repository.load_json(ref).get("schema")
            == "AgentDecisionRound@1"
        ]
        source_round = max(
            source_rounds,
            key=lambda payload: payload["round"],
        )
        self.assertIsInstance(
            load_operational_state_record(source_round["state_after"]),
            LegacyOperationalMarkovStateV2,
        )
        self.assertIsInstance(
            load_decision_operator_record(source_round["operator"]),
            LegacyDecisionOperatorV1,
        )

    def test_structured_conflict_stales_only_two_exact_consumers(
        self,
    ) -> None:
        round_payload = self.branch_records["M009DecisionRound@1"][1]
        state_before = OperationalMarkovState.from_dict(
            round_payload["state_before"]
        )
        operator = DecisionOperator.from_dict(round_payload["operator"])
        state_after = OperationalMarkovState.from_dict(
            round_payload["state_after"]
        )
        manifest = self.records["PantheonM009SuccessorManifest@1"][1]

        self.assertEqual(
            operator.base_state_digest,
            state_before.state_digest,
        )
        self.assertEqual(
            round_payload["closure"]["result_state_digest"],
            state_after.state_digest,
        )
        self.assertEqual(
            list(state_after.invalidated_refs),
            manifest["stale_deliverables"],
        )
        self.assertEqual(len(state_after.invalidated_refs), 2)
        for current_ref in manifest["current_deliverables"]:
            self.assertNotIn(current_ref, state_after.invalidated_refs)
            self.assertIsNotNone(state_after.value_for_ref(current_ref))
        conflict = next(
            item
            for item in state_after.facts
            if item.key == "oculus-measurement-conflict"
        )
        self.assertEqual(
            conflict.epistemic_status,
            FactEpistemicStatus.DISPUTED,
        )
        self.assertIsInstance(conflict.python_value, dict)
        self.assertNotIn(
            conflict.epistemic_status.value,
            json.dumps(conflict.python_value),
        )

    def test_blocking_edges_control_readiness_without_false_invalidation(
        self,
    ) -> None:
        archive = self.records["M009OperationalStateArchive@1"][1]
        state = OperationalMarkovState.from_dict(archive["state"])
        statuses = {
            item.obligation_id: item.status
            for item in state.obligations
        }

        self.assertEqual(
            statuses["resolve-oculus-measurement-definition"],
            ObligationStatus.OPEN,
        )
        for obligation_id in (
            "emit-resolved-oculus-fact",
            "revalidate-axial-section-after-oculus",
            "revalidate-voxel-prep-after-oculus",
        ):
            self.assertEqual(
                statuses[obligation_id],
                ObligationStatus.BLOCKED,
            )
        self.assertTrue(
            any(
                edge.effect is DependencyEffect.BLOCKS
                for edge in state.dependencies
            )
        )
        self.assertFalse(
            any(
                ref.startswith(("obligation:", "commitment:"))
                for ref in state.invalidated_refs
            )
        )


if __name__ == "__main__":
    # `--generate <dir>` materialises the same project the tests build, into a
    # directory of your choosing, for inspection or for seeding a workspace
    # copy. The tests never read it; they build their own under tempfile.
    if "--generate" in sys.argv:
        index = sys.argv.index("--generate")
        if index + 1 >= len(sys.argv):
            raise SystemExit("--generate requires a target directory")
        target = Path(sys.argv[index + 1]).resolve() / PROJECT_ID
        print(
            json.dumps(
                build_fixture_project(target),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        unittest.main()
