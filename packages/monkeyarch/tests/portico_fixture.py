"""The portico vertical slice: its grid, its two levels and its four element rows, produced and compiled.

The rows are the reference-reading producers' own slice (test_element_producers.py):
column array, capitals, entablature and pediment on shared datums. ``_compile``
turns rows into the compiled program over the spine fixture's state, as the
incremental patch tests (test_cad_patch.py) and the relation checks read it.
``_binding`` is the exact CAD binding of a compiled program, copied from the
integration suite's support (tests/integration/support.py) for the patch tests.
"""

from __future__ import annotations

import json
from dataclasses import replace

from monkeycad.execution import RhinoCadProgramBinding
from archflow.project.refs import BranchRef, ProjectRecordRef, RunRef
from archflow.state.geometry_program import CompiledGeometryProgram, ProjectGridAxis, ProjectGrids, ProjectLevel, ProjectLevels
from monkeyarch.authoring.element_producers import ElementRow, ProductionContext, produce_rows
from monkeyarch.compilation.geometry import compile_geometry_program
from monkeyarch.domain.reference_resolver import ReferenceContext
from spine_fixture import COMMITMENT, _only, _proposal, _state

BASIS = ("reading:plate",)
PN = "level-piano-nobile"


def _grids() -> ProjectGrids:
    axes = [ProjectGridAxis(f"axis-{k + 1}", str(k + 1), ((k - 2.5) * 1.6065, 0.0, 0.0), (0.0, 0.0, 1.0), BASIS) for k in range(6)]
    axes.append(ProjectGridAxis("axis-w", "W", (0.0, 0.0, -13.85), (1.0, 0.0, 0.0), BASIS))       # the west facade line
    axes.append(ProjectGridAxis("axis-ox", "OX", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), BASIS))        # three lines through the origin, for
    axes.append(ProjectGridAxis("axis-oz", "OZ", (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), BASIS))        # reading a stated direction off a
    axes.append(ProjectGridAxis("axis-od", "OD", (0.0, 0.0, 0.0), (1.0, 0.0, 1.0), BASIS))        # run whose plan coordinates are plain
    return ProjectGrids(project_id="demo", published_by="seat-coordination", axes=tuple(sorted(axes, key=lambda a: a.axis_id)))


def _levels(piano: float = 3.57) -> ProjectLevels:
    return ProjectLevels(project_id="demo", published_by="seat-coordination", levels=(
        ProjectLevel("level-ground", "terrain-grade", 0.0, BASIS), ProjectLevel(PN, "piano-nobile", piano, BASIS)))


def _rows(column_height: float = 6.426, engagement: float | None = None) -> tuple[ElementRow, ...]:
    capital_params = {"height": 0.18, "half_extent": 0.56}
    if engagement is not None:
        capital_params["engagement"] = {"depth": engagement}
    return (
        ElementRow("columns-west", "portico-columns", "column-array", {"axes": ["1", "2", "3", "4", "5", "6"], "facade": "W", "base": {"level": PN}},
                   {"radius": 0.357, "height": column_height, "segments": 24}, BASIS),
        ElementRow("capitals-west", "portico-capitals", "capitals", {"axes": ["1", "2", "3", "4", "5", "6"], "facade": "W", "columns": "columns-west", "base": {"datum": "columns-west-top"}},
                   capital_params, BASIS),
        ElementRow("entablature-west", "portico-entablature", "beam", {"from": {"grid": ["1", "W"]}, "to": {"grid": ["6", "W"]}, "base": {"datum": "capitals-west-top"}, "support": "capitals-west"},
                   {"depth": 0.92, "height": 1.33875, "end_overhang": 0.36}, BASIS),
        ElementRow("pediment-west", "portico-pediments", "pediment", {"from": {"grid": ["1", "W"]}, "to": {"grid": ["6", "W"]}, "base": {"datum": "entablature-west-top"}, "support": "entablature-west"},
                   {"rise": 1.78, "thickness": 0.3}, BASIS),
    )


def _produce(rows, levels=None):
    context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=levels or _levels()), published={})
    return produce_rows(rows, context), context


def _op_params(operation) -> dict:
    return {p.name: json.loads(p.value_json) for p in operation.parameters}


def _compile(rows, *, array_seed: str | None = None):
    """The slice as a compiled program; ``array_seed`` adds a P099-style block array over that operation."""

    context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=_levels()), published={}, frame_id="world")
    produced = produce_rows(rows, context)
    operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for e in produced for op in e.operations)
    if array_seed is not None:
        from archflow.state.geometry_program import GeometryOperation, GeometryOperationKind, GeometryParameter, GeometryParameterKind, LengthUnit

        seed = next(op for op in operations if op.op_id == array_seed)
        operations += (GeometryOperation(
            op_id=f"{array_seed}-array", kind=GeometryOperationKind.ARRAY, output_object_ids=(f"{seed.output_object_ids[0]}-array",),
            input_object_ids=seed.output_object_ids, frame_id=seed.frame_id,
            parameters=(GeometryParameter.create(name="count", kind=GeometryParameterKind.INTEGER, value=3),
                        GeometryParameter.create(name="step", kind=GeometryParameterKind.VECTOR3, value=[0.0, 0.0, 1.0], unit=LengthUnit.METER)),
            semantic_binding_ids=seed.semantic_binding_ids),)
    bindings = tuple(b for e in produced for b in e.bindings)
    datums = tuple(sorted(list(context.published.values()) + list(_levels().datums()), key=lambda d: d.datum_id))
    state = _state()
    proposal = _only(_proposal(state, extra_operations=operations), operations, ())
    result = compile_geometry_program(state, proposal, active_commitment_refs=(COMMITMENT,), interface_datums=datums, datum_bindings=bindings)
    assert result.program is not None, [(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues]
    return result.program


def _binding(
    program: CompiledGeometryProgram,
    *,
    branch_id: str = "candidate-a",
    stage_id: str = "stage-3",
    record_sha: str = "8" * 64,
    record_path: str | None = None,
    media_type: str = "application/json",
) -> RhinoCadProgramBinding:
    base = program.proposal.base
    branch = BranchRef(
        run=RunRef(program.proposal.project_id, program.proposal.run_id, base),
        branch_id=branch_id,
        epoch=3,
    )
    if record_path is None:
        record_path = (
            f"runs/{program.proposal.run_id}/branches/{branch_id}/records/"
            f"{stage_id}-geometry-program-{record_sha}.json"
        )
    return RhinoCadProgramBinding(
        program_ref=ProjectRecordRef(
            project_id=program.proposal.project_id,
            relative_path=record_path,
            sha256=record_sha,
            media_type=media_type,
        ),
        branch=branch,
        stage_id=stage_id,
        program_digest=program.program_digest,
        design_state_digest=program.proposal.design_state_digest,
        predecessor_program_digest=program.proposal.predecessor_program_digest,
    )
