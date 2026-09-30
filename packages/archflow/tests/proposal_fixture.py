"""The authored State Record's bound projection, and the geometry compiler's fixture proposal over it.

A copy of the kernel-level part of the MonkeyArch suite's spine fixture
(packages/monkeyarch/tests/spine_fixture.py): ``shared_bound_state`` binds the
authored record to a real P036 run and projects it, and ``_proposal`` is the
canonical proposal the compiler tests compile over that state. The geometry
program tests read the proposal's delivered objects; a package's tests cannot
import another package's tests, so the kernel's suite keeps its own copy.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from archflow.project.refs import RunRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.stage_workflow import DesignPhase
from archflow.state.geometry_program import (
    AffineTransform,
    AssemblyKind,
    AssemblyMember,
    AssemblyRole,
    AssetReference,
    CoordinateFrame,
    DetailMaturity,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    HostedAssembly,
    LengthUnit,
    ObjectRevisionPrecondition,
    SemanticBinding,
)
from archflow.state.state_record import StateRecord, developed_design_view

PROJECT_ID = "demo"
RUN_ID = "run-1"

# The two logical refs every fixture proposal cites. ``EVIDENCE`` is the
# record's own basis; ``COMMITMENT`` is the one commitment a semantic
# binding must name for the compiler to accept it.
EVIDENCE = "evidence:geometry-compiler"
COMMITMENT = "commitment:maintain-egress"

# The kwargs ``runtime.project_runner`` and the Studio pass to
# ``developed_design_view``; a projection built with any other three names
# a different state and its digest cites nothing.
VIEW_KWARGS = {
    "portfolio_id": "declared-schematic",
    "branch_id": "runner-v1",
    "selection_decision_ref": "decision:declared-schematic-selection",
}

# Three components (one root, two siblings under it), two levels, three grid
# axes, the massing the schematic option is rebuilt from (one massing level,
# one volume, two zones and the connection between them), a plinth the south
# wall stands on, that wall with one window void, a locked parameter and two
# derived from it, and two declared relations - the support the wall makes on
# the plinth, with a validator, and the interface the connection names.
# Closure, seats, producers, relation checks and the geometry compiler all
# have something to read.
RECORD_PAYLOAD: dict[str, object] = {
    "schema": "StateRecord@1",
    "project_id": PROJECT_ID,
    "run_id": RUN_ID,
    "evidence_refs": [EVIDENCE],
    "decision_ref": "decision:declared-schematic-selection",
    "option": {
        "option_id": "declared-schematic",
        "label": "the declared schematic",
        "typology": "one block with a south wall",
        "rationale": "declared by the authored record, not chosen by a portfolio",
        "footprint_cells": [[0, 0], [0, 1], [1, 0], [1, 1]],
        "assumption_refs": ["assumption:declared-schematic"],
    },
    "entities": [
        {
            "entity_id": "building",
            "schema": "Component@1",
            "fields": {
                "semantic_kind": "building",
                "intent": "Own the selected schematic massing.",
                "typology": "one block with a south wall",
                "volume_ids": ["block"],
                "source_refs": [EVIDENCE],
            },
        },
        {
            "entity_id": "primary-support",
            "schema": "Component@1",
            "parent_id": "building",
            "fields": {
                "roles": ["role.structural_support"],
                "intent": "Carry the selected schematic massing.",
                "source_refs": [EVIDENCE],
            },
        },
        {
            "entity_id": "primary-surface",
            "schema": "Component@1",
            "parent_id": "building",
            "fields": {
                "roles": ["role.weather_enclosure"],
                "intent": "Resolve the selected schematic envelope.",
                "source_refs": [EVIDENCE],
            },
        },
        {
            "entity_id": "level-ground",
            "schema": "Level@1",
            "fields": {"role": "terrain-grade", "elevation": 0.0},
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "level-piano-nobile",
            "schema": "Level@1",
            "fields": {"role": "piano-nobile", "elevation": 3.57},
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "axis-1",
            "schema": "GridAxis@1",
            "fields": {
                "role": "1",
                "origin": [0.0, 0.0, 0.0],
                "direction": [0.0, 0.0, 1.0],
            },
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "axis-2",
            "schema": "GridAxis@1",
            "fields": {
                "role": "2",
                "origin": [6.0, 0.0, 0.0],
                "direction": [0.0, 0.0, 1.0],
            },
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "axis-w",
            "schema": "GridAxis@1",
            "fields": {
                "role": "W",
                "origin": [0.0, 0.0, 0.0],
                "direction": [1.0, 0.0, 0.0],
            },
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "ground",
            "schema": "MassingLevel@1",
            "fields": {"base_y": 0, "height": 12},
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "block",
            "schema": "Volume@1",
            "fields": {
                "min": [0, 0, 0],
                "max": [12, 12, 12],
                "level_ids": ["ground"],
            },
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "main-room",
            "schema": "Space@1",
            "fields": {
                "program_node_refs": ["program-node:main"],
                "level_ids": ["ground"],
                "volume_ids": ["block"],
            },
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "entry-court",
            "schema": "Space@1",
            "fields": {
                "program_node_refs": ["program-node:entry"],
                "level_ids": ["ground"],
                "volume_ids": ["block"],
            },
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "outside-to-room",
            "schema": "Connection@1",
            "fields": {
                "source_zone_id": "entry-court",
                "target_zone_id": "main-room",
                "relationship_refs": ["relation:outside-to-room"],
            },
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "plinth",
            "schema": "Element@1",
            "parent_id": "primary-support",
            "fields": {
                "component_id": "primary-support",
                "producer": "prism",
                "references": {"base": {"level": "level-ground"}},
                "params": {
                    "profile": [[0.0, -0.6], [6.0, -0.6], [6.0, 0.6], [0.0, 0.6]],
                    "height": 0.6,
                },
            },
            "basis_refs": [EVIDENCE],
        },
        {
            "entity_id": "wall-south",
            "schema": "Element@1",
            "parent_id": "primary-surface",
            "fields": {
                "component_id": "primary-surface",
                "producer": "wall",
                "references": {
                    "base": {"datum": "plinth-top"},
                    "line": {
                        "from": {"grid": ["1", "W"]},
                        "to": {"grid": ["2", "W"]},
                    },
                    "support": "plinth",
                },
                "params": {
                    "thickness": 0.3,
                    "height": 2.97,
                    "openings": [
                        {
                            "opening_id": "window-south",
                            "kind": "window",
                            "along": 3.0,
                            "width": 1.2,
                            "sill": 0.9,
                            "head": 2.4,
                        }
                    ],
                },
            },
            "basis_refs": [EVIDENCE],
        },
    ],
    "parameters": [
        {"key": "module", "value": 1.2, "unit": "m", "lock_authority": "client"},
        {
            "key": "bay",
            "value": 2.4,
            "unit": "m",
            "expr": "2 * module",
            "inputs": ["module"],
        },
        {
            "key": "wall_height",
            "value": 2.97,
            "unit": "m",
            "expr": "3 * module - 0.63",
            "inputs": ["module"],
        },
    ],
    "relations": [
        {
            "relation_id": "plinth-supports-wall-south",
            "kind": "support",
            "subject": "plinth",
            "object": "wall-south",
            "datum_role": "plinth-top",
            "propagation": "revalidate",
            "validator": {"check_kind": "support_contact", "tolerance": 0.001},
            "basis_refs": [EVIDENCE],
        },
        {
            "relation_id": "outside-to-room",
            "kind": "interface",
            "subject": "entry-court",
            "object": "main-room",
            "propagation": "revalidate",
            "basis_refs": [EVIDENCE],
        },
    ],
}

def authored_record() -> StateRecord:
    """The fixture record as authored: portable, with no base and no run."""

    return StateRecord.from_dict(RECORD_PAYLOAD)


# The phase the shared fixture run is in. A run states its phase in its stage
# envelope; this fixture creates a run without opening a stage, so the phase it
# would have executed in is named here once and passed explicitly. A test about
# phases builds its own ``bound_state`` with the phase it is about.
FIXTURE_PHASE = DesignPhase.DESIGN_DEVELOPMENT


def bound_state(
    tmp_path: Path | str,
    *,
    phase: DesignPhase,
) -> tuple[FilesystemProjectRepository, RunRef, StateRecord, DevelopedDesignState]:
    """One P036 project, one run, the record bound to it, and its projection.

    The four parts are what the archived portfolio fixture returned as a
    tuple, named: the repository the run lives in, the run itself, the
    record bound to that run's base, and the developed-design state the
    spine's constructor yields from it.

    ``phase`` is the phase the run executes in and every caller states it:
    it enters the state digest exactly as a stage envelope's phase does in
    production, so a fixture that did not name it would be projecting a run
    nobody described.
    """

    project_dir = Path(tmp_path) / PROJECT_ID
    repository = FilesystemProjectRepository.initialize(
        project_dir,
        project_id=PROJECT_ID,
        initial_state={"schema": "TestState@1"},
    )
    run = repository.create_run(RUN_ID)
    record = authored_record().bound_to(run)
    state = developed_design_view(record, run=run, phase=phase, **VIEW_KWARGS)
    return repository, run, record, state


_SHARED: tuple[
    FilesystemProjectRepository, RunRef, StateRecord, DevelopedDesignState
] | None = None


def shared_bound_state() -> tuple[
    FilesystemProjectRepository, RunRef, StateRecord, DevelopedDesignState
]:
    """``bound_state`` over one temporary project, built once per process.

    The project's initial state is fixed, so its version digest — and every
    digest derived from it — is the same on every run and on every machine.
    """

    global _SHARED
    if _SHARED is None:
        root = Path(tempfile.mkdtemp(prefix="archflow-spine-fixture-"))
        atexit.register(shutil.rmtree, root, True)
        _SHARED = bound_state(root, phase=FIXTURE_PHASE)
    return _SHARED


# ---------------------------------------------------------------- the compiler's fixture proposal

def _state(*, width: float = 6.0) -> DevelopedDesignState:
    """The fixture state; ``width`` re-authors the building component.

    A different width is a different design decision on the same
    component, so the component's revision advances and its intent says
    why: that is what the compiler must notice as a semantic change.
    """

    state = shared_bound_state()[3]
    if width == 6.0:
        return state
    proposal = state.selected_schematic.option.proposal
    components = tuple(
        replace(
            item,
            revision=item.revision + 1,
            intent=f"{item.intent} Width decision {width}.",
        )
        if item.component_id == "building"
        else item
        for item in proposal.components
    )
    option = replace(
        state.selected_schematic.option,
        proposal=replace(proposal, components=components),
    )
    return replace(
        state,
        selected_schematic=replace(
            state.selected_schematic,
            option=option,
        ),
    )


def _number(name: str, value: float) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.NUMBER,
        value=value,
        unit=LengthUnit.METER,
    )


def _operation(
    *,
    op_id: str,
    kind: GeometryOperationKind,
    output: str,
    inputs: tuple[str, ...] = (),
    parameter: GeometryParameter | None = None,
    asset_id: str | None = None,
    responds: tuple[str, ...] = (),
    responds_to_bindings: tuple[str, ...] = (),
) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=kind,
        output_object_ids=(output,),
        input_object_ids=inputs,
        frame_id="world",
        parameters=(parameter,) if parameter is not None else (),
        semantic_binding_ids=("building-binding",),
        asset_id=asset_id,
        asset_socket_id="origin" if asset_id is not None else None,
        asset_scale=(1.0, 1.0, 1.0) if asset_id is not None else None,
        responds_to_object_ids=responds,
        responds_to_binding_ids=responds_to_bindings,
    )


def _proposal(
    state: DevelopedDesignState,
    *,
    wall_width: float = 6.0,
    predecessor: str | None = None,
    revisions: tuple[ObjectRevisionPrecondition, ...] = (),
    respond_to_dependencies: bool = False,
    assets: tuple[AssetReference, ...] = (),
    extra_operations: tuple[GeometryOperation, ...] = (),
) -> GeometryProgramProposal:
    dependency_response = (
        {
            "cut": ("wall",),
            "frame": ("cut-result",),
            "leaf": ("cut-result",),
            "hardware": ("cut-result",),
            "clearance": ("cut-result",),
        }
        if respond_to_dependencies
        else {}
    )
    operations = (
        _operation(
            op_id="clearance",
            kind=GeometryOperationKind.SOLID,
            output="clearance",
            inputs=("cut-result",),
            parameter=_number("depth", 1.2),
            responds=dependency_response.get("clearance", ()),
        ),
        _operation(
            op_id="cut",
            kind=GeometryOperationKind.BOOLEAN_DIFFERENCE,
            output="cut-result",
            inputs=("opening-tool", "wall"),
            responds=dependency_response.get("cut", ()),
        ),
        _operation(
            op_id="frame",
            kind=GeometryOperationKind.SWEEP,
            output="frame",
            inputs=("cut-result",),
            parameter=_number("thickness", 0.08),
            responds=dependency_response.get("frame", ()),
        ),
        _operation(
            op_id="hardware",
            kind=GeometryOperationKind.SOLID,
            output="hardware",
            inputs=("cut-result",),
            parameter=_number("placeholder-size", 0.05),
            responds=dependency_response.get("hardware", ()),
        ),
        _operation(
            op_id="leaf",
            kind=GeometryOperationKind.SOLID,
            output="leaf",
            inputs=("cut-result",),
            parameter=_number("thickness", 0.04),
            responds=dependency_response.get("leaf", ()),
        ),
        _operation(
            op_id="opening-tool",
            kind=GeometryOperationKind.SOLID,
            output="opening-tool",
            parameter=_number("width", 0.9),
        ),
        _operation(
            op_id="unrelated",
            kind=GeometryOperationKind.CURVE,
            output="unrelated-axis",
            parameter=_number("length", 2.0),
        ),
        _operation(
            op_id="wall",
            kind=GeometryOperationKind.SOLID,
            output="wall",
            parameter=_number("width", wall_width),
        ),
        *extra_operations,
    )
    object_ids = tuple(
        sorted(
            {
                object_id
                for operation in operations
                for object_id in operation.output_object_ids
            }
        )
    )
    binding = SemanticBinding(
        binding_id="building-binding",
        component_id="building",
        object_ids=object_ids,
        commitment_refs=(COMMITMENT,),
        evidence_refs=(EVIDENCE,),
    )
    assembly = HostedAssembly(
        assembly_id="entry-assembly",
        kind=AssemblyKind.DOOR,
        host_object_id="wall",
        host_socket_id="entry-axis",
        members=(
            AssemblyMember(AssemblyRole.CLEARANCE, ("clearance",)),
            AssemblyMember(AssemblyRole.FRAME, ("frame",)),
            AssemblyMember(AssemblyRole.HARDWARE, ("hardware",)),
            AssemblyMember(AssemblyRole.HOST_CUT, ("cut-result",)),
            AssemblyMember(AssemblyRole.LEAF, ("leaf",)),
        ),
        interface_refs=("interface:inside-to-outside",),
        semantic_binding_ids=("building-binding",),
        maturity=DetailMaturity.FUNCTIONAL,
    )
    return GeometryProgramProposal(
        proposal_id="geometry-proposal",
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        predecessor_program_digest=predecessor,
        length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001),
        frames=(
            CoordinateFrame(
                frame_id="world",
                parent_frame_id=None,
                transform_from_parent=AffineTransform.identity(),
                source_refs=(EVIDENCE,),
            ),
        ),
        assets=assets,
        semantic_bindings=(binding,),
        operations=tuple(sorted(operations, key=lambda item: item.op_id)),
        assemblies=(assembly,),
        revisions=revisions,
    )
