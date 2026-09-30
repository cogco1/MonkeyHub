"""Several massings on the table at once, each with its numbers, one chosen.

What an architect does at the massing stage is hold two or three shapes beside
each other and read them off: how much ground, how much floor, how many
storeys, how tall, how much of it the program actually asked for. The record
could always carry *one* massing; there was no way to hold a second one beside
it and nothing that measured either. This module is that table.

**An option is a record, not a picture.** Each one is the current record's own
``SchematicPack@1`` with one deterministic transform applied, and it is
measured by ``monkeyarch.domain.massing_metrics`` on the successor record that selecting
it would run — so the numbers on the card are the numbers of the thing that
would be built, not of an approximation of it. Nothing here computes geometry,
and nothing here writes the authored record.

**The transforms are closed, and one of them is a socket.** They are
MonkeyArch's (``monkeyarch.domain.massing_transforms``, #519): ``add_floor``,
``remove_floor``, ``shift_volume``, ``scale_volume`` and ``split_volume`` are
the moves a person makes with a mouse; ``pack`` takes a whole pack payload the
client sends and validates it with ``SchematicPack.from_dict``. A generative
agent plugs into ``pack`` and nowhere else, which is why no other transform
takes free-form geometry. This module applies one, measures it and keeps it.

**What is retained, and what is not.** Each option is retained as its own run,
``option-NNN``, holding the kernel's own ``SpatialOptionProposal@2`` under the
existing ``selected-spatial-option`` kind — the same record the runner writes
for the option it executes. The metrics ride on the option in this process
only: there is no retained record kind whose payload is a set of measurements,
and inventing one here would be a vocabulary this module does not own.
Selecting an option runs it as a candidate through the studio's one candidate
path, and the run the runner makes is where the selection becomes a fact
anybody else can read.
"""

from __future__ import annotations

from archflow.project.refs import ProjectRecordRef

from dataclasses import dataclass
import threading
from typing import Any, Mapping, Sequence

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import SELECTED_SPATIAL_OPTION
from monkeyarch.domain.massing_metrics import (
    EnvelopeFinding,
    MassingMetrics,
    MassingMetricsError,
    envelope_check,
    massing_metrics,
)
from monkeyarch.domain.massing_transforms import require_transform, transformed
from archflow.state.spatial import SpatialProposalError
from archflow.state.state_record import (
    SchematicPack,
    StateRecord,
    StateRecordEditKind,
    StateRecordError,
    StateRecordOperator,
    apply_state_record_operator,
    schematic_pack_of,
    schematic_proposal,
    volume_boxes_of,
)

from ..errors import StudioError
from ..binding import ProjectBinding
from .artifacts import ModelSource, require_model_source
from .projection import project_state

# What an option's run is called. The number is this process's counter: the
# options on the table are this process's memory, and their runs are named in
# the order it made them.
RUN_PREFIX = "option"

# What the store says about itself on every option it hands out.
PERSISTENCE = (
    "the option itself is in-memory (not version history); its pack is "
    "retained in its own run"
)

@dataclass(frozen=True, slots=True)
class MassingOption:
    """One massing on the table: how it was made, what it is, what it measures."""

    option_id: str
    run_id: str
    label: str
    transform: str
    parameters: Mapping[str, Any]
    base_record_digest: str
    base_state_digest: str
    pack: SchematicPack
    record: StateRecord
    metrics: MassingMetrics
    envelope_findings: tuple[EnvelopeFinding, ...]
    # The retained ``selected-spatial-option`` this option's run holds.
    record_ref: str
    honesty: tuple[str, ...]
    source_run_id: str | None = None
    source_stage_ref: ProjectRecordRef | None = None
    model_source: ModelSource | None = None


@dataclass(frozen=True, slots=True)
class OptionsTable:
    """The baseline and every option beside it, measured the same way."""

    state_digest: str
    baseline: MassingMetrics
    options: tuple[MassingOption, ...]
    source_run_id: str | None = None


class OptionStore:
    """The massing options this process holds, in the order they were made.

    In memory, like the proposal store beside it, and lost on restart — the
    runs the options were retained in are not. Locked, because options are
    made on request threads and read on others.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._options: dict[str, MassingOption] = {}
        self._next = 1

    def reserve(self) -> tuple[str, str]:
        """The next option id and the run id it will be retained under."""

        with self._lock:
            number = self._next
            self._next += 1
        return f"{RUN_PREFIX}-{number:03d}", f"{RUN_PREFIX}-{number:03d}"

    def add(self, option: MassingOption) -> MassingOption:
        with self._lock:
            self._options[option.option_id] = option
        return option

    def get(self, option_id: str) -> MassingOption:
        with self._lock:
            option = self._options.get(option_id)
        if option is None:
            raise StudioError(
                404,
                "OPTION_NOT_FOUND",
                f"no massing option {option_id} in this process. Options live "
                "in memory and are lost on restart; POST /api/options makes "
                "one, GET /api/options lists the ones this process holds.",
            )
        return option

    def all(self) -> tuple[MassingOption, ...]:
        with self._lock:
            return tuple(
                self._options[key] for key in sorted(self._options)
            )


# ---------------------------------------------------------------- reading the record


@dataclass(frozen=True, slots=True)
class RecordVolume:
    """One ``Volume@1`` as the panel needs it: its box and the ground it covers."""

    volume_id: str
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]
    level_ids: tuple[str, ...]
    footprint_m2: float


@dataclass(frozen=True, slots=True)
class RecordMassing:
    """The record's massing as the options panel reads it, before any transform."""

    volumes: tuple[RecordVolume, ...]
    metrics: MassingMetrics


def record_massing(record: StateRecord) -> RecordMassing:
    """The volumes a client can name in a transform, with the current metrics.

    ``GET /api/state/frame`` answers the frame an *element* is positioned
    against — levels and axes. A massing volume is positioned against neither;
    it declares its own box, so it is a different resource rather than another
    field of the frame.
    """

    metrics = massing_metrics(record)
    boxes = volume_boxes_of(record)
    per_volume = {
        entity.entity_id: tuple(str(v) for v in entity.fields.get("level_ids", ()))
        for entity in record.entities_of("Volume@1")
    }
    volumes = tuple(
        RecordVolume(
            volume_id=volume_id,
            minimum=low,
            maximum=high,
            level_ids=per_volume.get(volume_id, ()),
            footprint_m2=_box_area(low, high),
        )
        for volume_id, (low, high) in sorted(boxes.items())
    )
    return RecordMassing(volumes=volumes, metrics=metrics)


def baseline_pack(record: StateRecord) -> SchematicPack:
    """The record's own massing as a pack, or a refusal saying it declares none."""

    try:
        pack = schematic_pack_of(record)
    except StateRecordError as exc:
        raise StudioError(422, "STATE_RECORD_INVALID", str(exc)) from exc
    if pack is None:
        raise StudioError(
            422,
            "NO_MASSING",
            "this record declares no massing: an option needs Volume@1, "
            "Space@1 and MassingLevel@1 entities together, and the record "
            "carries fewer than all three. GET /api/state/volumes shows what "
            "it does carry.",
        )
    return pack


# ---------------------------------------------------------------- making one option


def make_option(
    binding: ProjectBinding,
    store: OptionStore,
    record: StateRecord,
    *,
    state_digest: str,
    transform: str,
    parameters: Mapping[str, Any],
    label: str | None = None,
    envelope: Mapping[str, Any] | None = None,
    program_targets: Mapping[str, float] | None = None,
    source_run_id: str | None = None,
    model_source: ModelSource | None = None,
    source_stage_ref: ProjectRecordRef | None = None,
) -> MassingOption:
    """Apply one transform, measure the result, retain it, and put it on the table."""

    if model_source is not None:
        require_model_source(binding, model_source, project_state(binding, source_run_id, source_stage_ref=source_stage_ref))
    require_transform(transform)
    base = baseline_pack(record)
    existing_runs = set(binding.run_ids())
    while True:
        option_id, run_id = store.reserve()
        if run_id not in existing_runs:
            break
    pack, honesty = transformed(base, transform, parameters, option_id)
    successor, metrics, findings, more = _measure(
        record, pack, envelope=envelope, program_targets=program_targets
    )
    record_ref = _retain(binding, run_id, pack)
    return store.add(
        MassingOption(
            option_id=option_id,
            run_id=run_id,
            label=label or pack.label,
            transform=transform,
            parameters=dict(parameters),
            base_record_digest=record.digest,
            base_state_digest=state_digest,
            pack=pack,
            record=successor,
            metrics=metrics,
            envelope_findings=findings,
            record_ref=record_ref,
            honesty=tuple(honesty) + tuple(more),
            source_run_id=source_run_id,
            source_stage_ref=source_stage_ref,
            model_source=model_source,
        )
    )


def _measure(
    record: StateRecord,
    pack: SchematicPack,
    *,
    envelope: Mapping[str, Any] | None,
    program_targets: Mapping[str, float] | None,
) -> tuple[StateRecord, MassingMetrics, tuple[EnvelopeFinding, ...], tuple[str, ...]]:
    """Measure the option on the record selecting it would actually run.

    The successor is built here rather than at selection time for one reason:
    a transform that produces a massing the kernel refuses — a volume with no
    level, a zone with no volume, a component owning a volume that is gone —
    must be refused now, with the request that made it, and not minutes later
    as a failed run whose reason is somewhere else.
    """

    try:
        successor = apply_state_record_operator(
            record,
            StateRecordOperator(
                kind=StateRecordEditKind.REPLACE_MASSING,
                base_record_digest=record.digest,
                base_state_digest=record.state_digest,
                massing_pack=pack,
            ),
        )
        # The kernel's own validation of the option, run here so a refusal
        # arrives as a 422 about this transform. The runner would build the
        # same value from the same record.
        schematic_proposal(pack)
        metrics = massing_metrics(successor, program_targets=program_targets)
        findings = envelope_check(successor, envelope) if envelope else ()
    except (StateRecordError, SpatialProposalError, MassingMetricsError, TypeError, ValueError) as exc:
        raise StudioError(
            422,
            "MASSING_REFUSED",
            f"the kernel refuses this massing: {exc}",
        ) from exc
    return successor, metrics, findings, ()


def _retain(
    binding: ProjectBinding, run_id: str, pack: SchematicPack
) -> str:
    """Retain the option in its own run, as the kernel's own spatial option.

    ``selected-spatial-option`` is the kind the runner already writes for the
    option a run executed, and ``schematic_proposal`` is the value it writes:
    an option this process is holding is the same thing, retained before
    anybody has chosen it. Nothing new is invented for it — the studio has no
    record kind of its own for a set of measurements, so the metrics stay on
    the option in this process and are not written here.
    """

    run = binding.repository.create_run(run_id)
    return binding.repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id=run_id
        ),
        record_kind=SELECTED_SPATIAL_OPTION,
        payload=schematic_proposal(pack).to_dict(),
    ).uri


def _box_area(low: Sequence[float], high: Sequence[float]) -> float:
    """One volume's own plan area, in the same whole cells the metrics count."""

    if any(float(v) != int(v) for v in (low[0], low[2], high[0], high[2])):
        return 0.0
    return float((int(high[0]) - int(low[0]) + 1) * (int(high[2]) - int(low[2]) + 1))
