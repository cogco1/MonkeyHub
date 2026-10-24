"""The wire form of the edit log: a line's steps nearest first, and the same changes gathered per field."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...application.edit_log import EditStep, FieldEdit, TargetHistory


class _Wire(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)


class FieldEditDto(_Wire):
    target_ref: str = Field(alias="targetRef", description="entity:<id> or parameter:<key>")
    component_id: str | None = Field(alias="componentId", description="the component an element belongs to")
    op: Literal["set", "add", "remove", "edit"]
    field: str | None = Field(description="the numeric field's dotted path, set only for op set")
    before: float | None = None
    after: float | None = None
    delta: float | None = None
    direction: Literal[-1, 0, 1] = Field(description="the sign of the change; 0 where it is not a number")


class EditStepDto(_Wire):
    run_id: str = Field(alias="runId")
    source_run_id: str = Field(alias="sourceRunId", description="the run this step continued")
    kind: str = Field(description="the retained operator's kind, such as set_scalar or edit_components")
    request: str | None = Field(description="the words the change was asked in, when it was asked in words")
    edits: list[FieldEditDto]


class TargetHistoryDto(_Wire):
    target_ref: str = Field(alias="targetRef")
    component_id: str | None = Field(alias="componentId")
    field: str | None
    steps: list[int] = Field(description="positions in steps, 0 being the line's head")
    directions: list[Literal[-1, 0, 1]]
    net: float | None = Field(description="the summed change, when every change of it is a number")


class EditLogDto(_Wire):
    run_id: str | None = Field(alias="runId", description="the run whose line this is; null when the project has none")
    steps: list[EditStepDto] = Field(description="one per run along the line, nearest first")
    targets: list[TargetHistoryDto] = Field(description="the same changes gathered per element field or parameter")


def _edit(edit: FieldEdit) -> FieldEditDto:
    return FieldEditDto(target_ref=edit.target_ref, component_id=edit.component_id, op=edit.op, field=edit.field,
                        before=edit.before, after=edit.after, delta=edit.delta, direction=edit.direction)


def _step(step: EditStep) -> EditStepDto:
    return EditStepDto(run_id=step.run_id, source_run_id=step.source_run_id, kind=step.kind, request=step.request,
                       edits=[_edit(edit) for edit in step.edits])


def _target(row: TargetHistory) -> TargetHistoryDto:
    return TargetHistoryDto(target_ref=row.target_ref, component_id=row.component_id, field=row.field,
                            steps=list(row.steps), directions=list(row.directions), net=row.net)


def edit_log_dto(run_id: str | None, steps, targets) -> EditLogDto:
    return EditLogDto(run_id=run_id, steps=[_step(step) for step in steps], targets=[_target(row) for row in targets])
