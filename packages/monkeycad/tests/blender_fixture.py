"""A Blender request over two objects: a box with a long identity and a non-rectangular extrusion.

The same request as the MonkeyArch suite's Blender slice
(packages/monkeyarch/tests/test_blender_cad.py), which builds it for its own
host and runner acceptance; a package's tests cannot import another package's
tests, so this suite keeps its own.
"""

from __future__ import annotations

from dataclasses import replace

from monkeycad.execution import CadExecutionRequest
from archflow.state.geometry_program import (
    GeometryOperation, GeometryOperationKind, GeometryParameter,
    GeometryParameterKind, LengthUnit,
)
from cad_fixture import _binding, _box, _program_of

LONG_BOX_ID = "box-" + "identity-preserved-" * 4


def _extrusion(*, vector=(2.0, 3.0, 1.0)):
    return GeometryOperation(
        op_id="triangle", kind=GeometryOperationKind.EXTRUSION,
        output_object_ids=("triangle-object",), input_object_ids=(),
        frame_id="world", semantic_binding_ids=("body-binding",),
        parameters=(
            GeometryParameter.create(name="base_level", kind=GeometryParameterKind.NUMBER, value=5.0, unit=LengthUnit.METER),
            GeometryParameter.create(name="base_offset", kind=GeometryParameterKind.NUMBER, value=2.0, unit=LengthUnit.METER),
            GeometryParameter.create(name="profile", kind=GeometryParameterKind.POINTS3,
                                     value=[[1.0, 0.0, 2.0], [5.0, 0.0, 2.0], [1.0, 0.0, 5.0]], unit=LengthUnit.METER),
            GeometryParameter.create(name="vector", kind=GeometryParameterKind.VECTOR3, value=list(vector), unit=LengthUnit.METER),
        ),
    )


def _two_objects(unit=LengthUnit.MILLIMETER):
    operations = (_box(LONG_BOX_ID, [10.0, 20.0, 30.0], [2.0, 3.0, 4.0]), _extrusion())
    operations = tuple(replace(op, parameters=tuple(
        replace(parameter, unit=unit) if parameter.unit is not None else parameter
        for parameter in op.parameters
    )) for op in operations)
    program = _program_of(*operations)
    return replace(program, proposal=replace(program.proposal, length_unit=unit))


def _request(workspace, *, program=None, **changes):
    program = program or _two_objects()
    return CadExecutionRequest(
        program=program, binding=_binding(program), speculative_workspace=workspace,
        artifact_stem="blender-candidate", **changes,
    )
