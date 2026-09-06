"""One authored State Record and the run the spine binds it to.

The spine's design state is a ``StateRecord@1`` and its constructor is
``developed_design_view(record, run=..., phase=...)`` (docs/CANONICAL_SPINE.md, the
"Design state" row). ``initialize_developed_design`` — the portfolio
ceremony that used to build the fixture state for these tests — left with
that lane, so the fixture is authored here as a record and projected the
way ``runtime.project_runner`` and the Studio project it, with the same
three constants.

Nothing here is a mock: ``bound_state`` initializes a real P036 project,
creates a run, binds the record to it and returns the projection, so a
test that compiles against this state is compiling against what
production would hand the compiler. It takes the run's phase, as the
projection does in production, and every caller states one.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.capabilities.geometry_proposal import (
    GeometryProposalProviderIdentity,
)
from archflow.compilers.geometry import compile_geometry_program
from archflow.ports.model import ModelInvocationReceipt, ModelInvocationStatus
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import SELECTED_SPATIAL_OPTION
from archflow.project.refs import RunRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.developed_design import (
    DevelopedDesignState,
    DevelopmentDiscipline,
    DevelopmentObligation,
    DevelopmentObligationPriority,
    DevelopmentObligationStatus,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.state.geometry_program import (
    AffineTransform,
    AssemblyKind,
    AssemblyMember,
    AssemblyRole,
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

# The one interface the record declares, as the connection between its two
# zones names it. A hosted assembly may only cite an interface the supplied
# spatial option already carries, so the fixture's assemblies cite this.
INTERFACE_REF = "relation:outside-to-room"


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


def coordination_obligations(
    *,
    open_disciplines: tuple[DevelopmentDiscipline, ...] = (),
) -> tuple[DevelopmentObligation, ...]:
    """One coordination duty per discipline, resolved unless named open.

    A developed-design state carries the duties its disciplines still owe
    each other; ``capabilities.discipline_seats`` reads them when it
    compiles a handover. The fixture state itself carries none, so a test
    that is about obligations says which of them are still open.
    """

    open_set = frozenset(open_disciplines)
    return tuple(
        DevelopmentObligation(
            obligation_id=f"coordinate-{discipline.value}",
            discipline=discipline,
            statement=(
                f"Coordinate the project-authored {discipline.value} "
                "requirements against the declared schematic."
            ),
            priority=DevelopmentObligationPriority.BLOCKING,
            status=(
                DevelopmentObligationStatus.OPEN
                if discipline in open_set
                else DevelopmentObligationStatus.RESOLVED
            ),
            source_refs=(EVIDENCE,),
            dependency_refs=("selected-schematic:runner-v1",),
        )
        for discipline in DevelopmentDiscipline
    )


# ---------------------------------------------------------------- one compiled room

def _vector(name: str, value: list[float]) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.VECTOR3,
        value=value,
        unit=LengthUnit.METER,
    )


def _points(value: list[list[float]]) -> GeometryParameter:
    return GeometryParameter.create(
        name="points",
        kind=GeometryParameterKind.POINTS3,
        value=value,
        unit=LengthUnit.METER,
    )


def _solid(op_id: str, output: str, origin, size) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.SOLID,
        output_object_ids=(output,),
        input_object_ids=(),
        frame_id="world",
        parameters=(_vector("origin", origin), _vector("size", size)),
        semantic_binding_ids=("room-binding",),
    )


def _curve(op_id: str, output: str, points) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.CURVE,
        output_object_ids=(output,),
        input_object_ids=(),
        frame_id="world",
        parameters=(_points(points),),
        semantic_binding_ids=("room-binding",),
    )


def _boolean(
    op_id: str,
    output: str,
    kind: GeometryOperationKind,
    inputs: tuple[str, ...],
    *,
    base_id: str | None = None,
) -> GeometryOperation:
    ordered = tuple(sorted(inputs))
    parameters: tuple[GeometryParameter, ...] = ()
    if kind is GeometryOperationKind.BOOLEAN_DIFFERENCE:
        assert base_id is not None
        parameters = (
            GeometryParameter.create(
                name="base_index",
                kind=GeometryParameterKind.INTEGER,
                value=ordered.index(base_id),
            ),
        )
    return GeometryOperation(
        op_id=op_id,
        kind=kind,
        output_object_ids=(output,),
        input_object_ids=ordered,
        frame_id="world",
        parameters=parameters,
        semantic_binding_ids=("room-binding",),
    )


def compiled_room():
    """The fixture state and one compiled program of a room in it.

    A shell with an opening, a leaf, a frame, hardware and a clearance:
    boolean chains, curves and one hosted assembly, all owned by the
    record's ``building`` component. Seats and the proposal producer need
    a real compiled program to hand around; this is it.
    """

    state = shared_bound_state()[3]
    operations = [
        _solid("clearance", "clearance", [0, 1, 2], [2, 2, 1]),
        _curve("frame", "frame", [[0, 1, 2], [0, 3, 2]]),
        _solid("floor", "floor", [0, 0, 0], [5, 1, 5]),
        _curve("hardware", "hardware", [[0, 2, 2], [0, 2, 2.1]]),
        _solid("inner", "inner", [1, 1, 1], [3, 2, 3]),
        _curve("leaf", "leaf", [[0, 1, 2], [0, 3, 2]]),
        _solid("opening-tool", "opening-tool", [0, 1, 2], [1, 2, 1]),
        _solid("outer", "outer", [0, 1, 0], [5, 3, 5]),
        _boolean(
            "shell",
            "shell",
            GeometryOperationKind.BOOLEAN_DIFFERENCE,
            ("inner", "outer"),
            base_id="outer",
        ),
        _boolean(
            "opening",
            "opening",
            GeometryOperationKind.BOOLEAN_INTERSECTION,
            ("opening-tool", "shell"),
        ),
        _boolean(
            "walls",
            "walls",
            GeometryOperationKind.BOOLEAN_DIFFERENCE,
            ("opening", "shell"),
            base_id="shell",
        ),
    ]
    operations_tuple = tuple(sorted(operations, key=lambda item: item.op_id))
    object_ids = tuple(
        sorted(
            object_id
            for operation in operations_tuple
            for object_id in operation.output_object_ids
        )
    )
    proposal = GeometryProgramProposal(
        proposal_id="fixture-room",
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        predecessor_program_digest=None,
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
        assets=(),
        semantic_bindings=(
            SemanticBinding(
                binding_id="room-binding",
                component_id="building",
                object_ids=object_ids,
                commitment_refs=(COMMITMENT,),
                evidence_refs=(EVIDENCE,),
            ),
        ),
        operations=operations_tuple,
        assemblies=(
            HostedAssembly(
                assembly_id="entry",
                kind=AssemblyKind.DOOR,
                host_object_id="shell",
                host_socket_id="entry-axis",
                members=(
                    AssemblyMember(AssemblyRole.CLEARANCE, ("clearance",)),
                    AssemblyMember(AssemblyRole.FRAME, ("frame",)),
                    AssemblyMember(AssemblyRole.HARDWARE, ("hardware",)),
                    AssemblyMember(AssemblyRole.HOST_CUT, ("opening",)),
                    AssemblyMember(AssemblyRole.LEAF, ("leaf",)),
                ),
                interface_refs=(INTERFACE_REF,),
                semantic_binding_ids=("room-binding",),
                maturity=DetailMaturity.FUNCTIONAL,
            ),
        ),
    )
    compiled = compile_geometry_program(
        state,
        proposal,
        active_commitment_refs=(COMMITMENT,),
    )
    if compiled.program is None:
        raise AssertionError(compiled.receipt.issues)
    return state, compiled.program


# ---------------------------------------------------------------- a scripted provider

IDENTITY = GeometryProposalProviderIdentity(
    provider_id="scripted-provider",
    model_id="scripted-model",
    provider_version="1",
    provider_fingerprint="f" * 64,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


class ScriptedProvider:
    """A model provider that answers a fixed script, one round at a time.

    Each output is either an authoring payload the producer will read or a
    ``ModelInvocationStatus`` to refuse with; every answer is signed as a
    real ``ModelInvocationReceipt``, so the producer sees what it would see
    from a live provider and nothing is stubbed away.
    """

    def __init__(
        self,
        outputs: tuple[dict[str, object] | ModelInvocationStatus, ...],
        *,
        identity: GeometryProposalProviderIdentity = IDENTITY,
    ) -> None:
        self.outputs = list(outputs)
        self.identity = identity
        self.requests: list[object] = []

    async def invoke(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, ModelInvocationStatus):
            return ModelInvocationReceipt(
                receipt_id=f"scripted-receipt-{len(self.requests):02d}",
                status=output,
                request=request,
                provider_id=self.identity.provider_id,
                model_id=self.identity.model_id,
                provider_version=self.identity.provider_version,
                provider_fingerprint=self.identity.provider_fingerprint,
                input_bytes=len(request.payload_json.encode("utf-8")),
                output_bytes=0,
                output_sha256=None,
                output_json=None,
                error_code=output.value,
                message="scripted refusal",
            )
        encoded = _canonical_json(output)
        return ModelInvocationReceipt(
            receipt_id=f"scripted-receipt-{len(self.requests):02d}",
            status=ModelInvocationStatus.SUCCESS,
            request=request,
            provider_id=self.identity.provider_id,
            model_id=self.identity.model_id,
            provider_version=self.identity.provider_version,
            provider_fingerprint=self.identity.provider_fingerprint,
            input_bytes=len(request.payload_json.encode("utf-8")),
            output_bytes=len(encoded.encode("utf-8")),
            output_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            output_json=encoded,
        )


class ProducerFixture(unittest.IsolatedAsyncioTestCase):
    """A project, its run, the record's own spatial option, and a proposal.

    ``design_state`` is the record's projection, ``option_ref`` the
    selected spatial option retained where the producer reads it, and
    ``proposal`` the compiled room re-homed onto this run: what the
    geometry-proposal producer needs to run for real.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        (
            self.repository,
            self.run,
            self.record,
            self.design_state,
        ) = bound_state(Path(self.temporary.name), phase=FIXTURE_PHASE)
        self.destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=self.run.run_id,
        )
        self.option = self.design_state.selected_schematic.option.proposal
        self.option_ref = self.repository.put_json(
            run=self.run,
            destination=self.destination,
            record_kind=SELECTED_SPATIAL_OPTION,
            payload=self.option.to_dict(),
        )
        _, program = compiled_room()
        binding = replace(
            program.proposal.semantic_bindings[0],
            commitment_refs=(COMMITMENT,),
            evidence_refs=(EVIDENCE, self.option_ref.uri),
        )
        self.proposal = replace(
            program.proposal,
            project_id=self.run.project_id,
            run_id=self.run.run_id,
            base=self.run.base,
            design_state_digest=self.design_state.state_digest,
            semantic_bindings=(binding,),
        )

    async def produce(self, provider, **extra):
        """One bounded production run against this fixture's project."""

        from archflow.capabilities.geometry_proposal import (
            GeometryProposalPolicy,
            produce_geometry_program_proposal,
        )

        return await produce_geometry_program_proposal(
            self.repository,
            provider,
            run=self.run,
            destination=self.destination,
            spatial_option_ref=self.option_ref,
            design_state=self.design_state,
            required_commitment_refs=(COMMITMENT,),
            provider_identity=IDENTITY,
            policy=GeometryProposalPolicy(extra.pop("rounds", 1)),
            **extra,
        )
