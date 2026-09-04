"""The wire form of a massing option, its metrics and the envelope it sits in.

Three things this DTO is careful about. ``metrics`` carries ``efficiency`` as
``null`` rather than ``0`` when no program target was given, because a ratio
nobody computed is not a ratio of nothing. ``envelopeFindings`` is a list of
measured exceedances and never a verdict: a client is not handed a boolean it
could paint red. And every option says where it lives — the option is this
process's memory and its pack is a retained record — so nobody reads a card on
the table as something the project will remember.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from archflow.state.massing_metrics import EnvelopeFinding, MassingMetrics

from ..application.options import (
    PERSISTENCE,
    TRANSFORMS,
    MassingOption,
    OptionsTable,
    RecordMassing,
)

TransformName = Literal[
    "add_floor", "remove_floor", "shift_volume", "scale_volume", "split_volume", "pack"
]


class EnvelopeDto(BaseModel):
    """The buildable envelope an option is measured against; every field optional.

    A field that is absent makes no finding: an envelope that says nothing
    about height cannot be exceeded in height, and the studio does not invent
    a limit the site did not state.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    min: tuple[float, float, float] | None = Field(
        default=None,
        description="the buildable box's low corner, in the massing lattice "
        "(x and z are plan, y is up)",
    )
    max: tuple[float, float, float] | None = Field(
        default=None, description="the buildable box's high corner"
    )
    max_height_m: float | None = Field(default=None, alias="maxHeightM")
    far: float | None = Field(
        default=None, description="plot ratio; checked only with siteAreaM2"
    )
    site_area_m2: float | None = Field(default=None, alias="siteAreaM2")


class MassingOptionRequestDto(BaseModel):
    """Ask for one option: which transform, on what, measured against what.

    The parameter fields are flat and optional because each transform reads
    only its own: ``shift_volume`` reads ``volumeId``, ``dx`` and ``dz``,
    ``split_volume`` reads ``volumeId``, ``along`` and ``at``, and ``pack``
    reads ``pack``. A parameter the named transform does not read is ignored;
    one it needs and does not get is a 422 that says which.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    state_digest: str = Field(
        alias="stateDigest",
        min_length=1,
        description="the stateDigest GET /api/state answered; an option made "
        "against another state is refused",
    )
    transform: TransformName
    label: str | None = Field(default=None, min_length=1)
    volume_id: str | None = Field(default=None, alias="volumeId")
    dx: int | None = Field(default=None, description="whole plan cells on x")
    dz: int | None = Field(default=None, description="whole plan cells on z")
    sx: float | None = Field(default=None, description="plan scale on x, about the centre")
    sz: float | None = Field(default=None, description="plan scale on z, about the centre")
    along: Literal["x", "z"] | None = None
    at: int | None = Field(
        default=None,
        description="the first cell of the far part of a split",
    )
    pack: dict[str, Any] | None = Field(
        default=None, description="a whole SchematicPack@1, for the pack transform"
    )
    envelope: EnvelopeDto | None = None
    program_targets: dict[str, float] | None = Field(
        default=None,
        alias="programTargets",
        description="program node id to target area in m2; given, efficiency "
        "is their share of the gross floor area",
    )

    def parameters(self) -> dict[str, Any]:
        """What the application reads, in its own words."""

        return {
            "volume_id": self.volume_id,
            "dx": self.dx,
            "dz": self.dz,
            "sx": self.sx,
            "sz": self.sz,
            "along": self.along,
            "at": self.at,
            "pack": self.pack,
        }


class LevelFootprintDto(BaseModel):
    """One massing level and the plan area the volumes on it cover."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    level_id: str = Field(alias="levelId")
    base_y: float = Field(alias="baseY")
    height: float
    footprint_m2: float = Field(alias="footprintM2")


class MassingMetricsDto(BaseModel):
    """What one massing measures, and what could not be measured."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    footprint_m2: float = Field(
        alias="footprintM2",
        description="the ground the whole massing covers, counted once where "
        "volumes overlap",
    )
    gross_floor_area_m2: float = Field(
        alias="grossFloorAreaM2",
        description="the sum of the per-level footprints",
    )
    floor_count: int = Field(alias="floorCount")
    height_m: float = Field(alias="heightM")
    efficiency: float | None = Field(
        description="the program targets as a share of the floor area; null "
        "when no target was given",
    )
    per_level: list[LevelFootprintDto] = Field(alias="perLevel")
    honesty: list[str]


class EnvelopeFindingDto(BaseModel):
    """One way a massing leaves its envelope, with both numbers."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    code: str = Field(
        description="volume_outside_envelope | height_exceeded | far_exceeded",
    )
    subject: str | None = Field(
        description="the volume this is about; null for the massing as a whole",
    )
    detail: str
    measured: float
    limit: float


class MassingOptionDto(BaseModel):
    """One option on the table."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    option_id: str = Field(alias="optionId")
    run_id: str = Field(
        alias="runId", description="the run this option's pack is retained in"
    )
    label: str
    transform: str
    parameters: dict[str, Any] = Field(
        description="what the transform was given, as it was given",
    )
    state_digest: str = Field(
        alias="stateDigest", description="the state this option was made against"
    )
    metrics: MassingMetricsDto
    envelope_findings: list[EnvelopeFindingDto] = Field(alias="envelopeFindings")
    record_ref: str = Field(
        alias="recordRef",
        description="the retained selected-spatial-option this option is",
    )
    persistence: str
    honesty: list[str]


class OptionsDto(BaseModel):
    """The wire form of ``GET /api/options``: the baseline and every option."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    state_digest: str = Field(alias="stateDigest")
    baseline: MassingMetricsDto = Field(
        description="the current record's own massing, measured the same way",
    )
    options: list[MassingOptionDto]
    transforms: list[str] = Field(
        description="the whole transform vocabulary, so a client offers no "
        "button the server would refuse",
    )


class VolumeDto(BaseModel):
    """One ``Volume@1`` a transform can name."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    volume_id: str = Field(alias="volumeId")
    min: tuple[float, float, float]
    max: tuple[float, float, float]
    level_ids: list[str] = Field(alias="levelIds")
    footprint_m2: float = Field(
        alias="footprintM2", description="this volume's own plan area"
    )


class VolumesDto(BaseModel):
    """The wire form of ``GET /api/state/volumes``.

    Separate from ``GET /api/state/frame`` on purpose: the frame is what an
    *element* is positioned against — levels and axes — and a massing volume
    is positioned against neither. It declares its own box.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    volumes: list[VolumeDto]
    metrics: MassingMetricsDto
    honesty: list[str]


def metrics_dto(metrics: MassingMetrics) -> MassingMetricsDto:
    return MassingMetricsDto(
        footprint_m2=metrics.footprint_m2,
        gross_floor_area_m2=metrics.gross_floor_area_m2,
        floor_count=metrics.floor_count,
        height_m=metrics.height_m,
        efficiency=metrics.efficiency,
        per_level=[
            LevelFootprintDto(
                level_id=level.level_id,
                base_y=level.base_y,
                height=level.height,
                footprint_m2=level.footprint_m2,
            )
            for level in metrics.per_level
        ],
        honesty=list(metrics.honesty),
    )


def finding_dto(finding: EnvelopeFinding) -> EnvelopeFindingDto:
    return EnvelopeFindingDto(
        code=finding.code,
        subject=finding.subject,
        detail=finding.detail,
        measured=finding.measured,
        limit=finding.limit,
    )


def option_dto(option: MassingOption) -> MassingOptionDto:
    return MassingOptionDto(
        option_id=option.option_id,
        run_id=option.run_id,
        label=option.label,
        transform=option.transform,
        parameters={
            key: value
            for key, value in option.parameters.items()
            if value is not None
        },
        state_digest=option.base_state_digest,
        metrics=metrics_dto(option.metrics),
        envelope_findings=[finding_dto(f) for f in option.envelope_findings],
        record_ref=option.record_ref,
        persistence=PERSISTENCE,
        honesty=list(option.honesty),
    )


def options_dto(table: OptionsTable) -> OptionsDto:
    return OptionsDto(
        state_digest=table.state_digest,
        baseline=metrics_dto(table.baseline),
        options=[option_dto(option) for option in table.options],
        transforms=list(TRANSFORMS),
    )


def volumes_dto(massing: RecordMassing) -> VolumesDto:
    return VolumesDto(
        volumes=[
            VolumeDto(
                volume_id=volume.volume_id,
                min=volume.minimum,
                max=volume.maximum,
                level_ids=list(volume.level_ids),
                footprint_m2=volume.footprint_m2,
            )
            for volume in massing.volumes
        ],
        metrics=metrics_dto(massing.metrics),
        honesty=list(massing.metrics.honesty),
    )
