"""Requests for working in the Rhino document the architect has open."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .artifacts import ProjectArtifactDto

Point = Annotated[list[float], Field(min_length=2, max_length=3, description="[x, y] or [x, y, z] in document units")]
Point3 = Annotated[list[float], Field(min_length=3, max_length=3)]
UserText = Annotated[dict[str, str] | None, Field(default=None, alias="userText", description="extra user text on each created object")]


class LiveRhinoOpenRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    confirmed: bool = Field(description="true only when the person said yes to MonkeyHub opening Rhino for them")
    document_path: str | None = Field(default=None, alias="documentPath", max_length=1024,
                                      description="an existing .3dm file to open; omitted opens Rhino with its usual new model")


class LiveRhinoOpenDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    rhino: dict[str, Any] = Field(description="the Rhino that now answers RhinoCode, as GET /api/live-rhino/instances lists it")
    opened: bool = Field(description="false when a Rhino that answers RhinoCode already had the document")


class _Op(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class _AddOp(_Op):
    layer: str = Field(description="full layer path, created when missing, e.g. HUB::Site::Streets")
    name: str | None = None
    user_text: UserText


class LayerOp(_Op):
    op: Literal["layer"]
    path: str
    color: Annotated[list[int], Field(min_length=3, max_length=3)] | None = Field(default=None, description="[r, g, b] for a new layer")


class PolylineOp(_AddOp):
    op: Literal["polyline"]
    points: Annotated[list[Point], Field(min_length=2)]
    closed: bool = False


class ExtrusionOp(_AddOp):
    """A closed planar outline at height z, extruded vertically into a capped solid."""

    op: Literal["extrusion"]
    outline: Annotated[list[Point], Field(min_length=3)]
    z: float = 0.0
    height: float


class BoxOp(_AddOp):
    op: Literal["box"]
    min: Point3
    max: Point3


class MeshOp(_AddOp):
    op: Literal["mesh"]
    vertices: Annotated[list[Point3], Field(min_length=3)]
    faces: Annotated[list[Annotated[list[int], Field(min_length=3, max_length=4)]], Field(min_length=1)]


class DotOp(_AddOp):
    op: Literal["dot"]
    text: str
    point: Point


class ImportOp(_AddOp):
    """Every object of a .3dm on this machine, scaled about the origin then moved."""

    op: Literal["import"]
    path: str = Field(description="absolute path of an existing .3dm")
    scale: float = 1.0
    translate: Point3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    keep_layers: bool = Field(default=True, alias="keepLayers", description="put each object under layer::<its source layer>")


class Rotation(_Op):
    angle_deg: float = Field(alias="angleDeg")
    center: Point3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    axis: Point3 = Field(default_factory=lambda: [0.0, 0.0, 1.0])


class Scaling(_Op):
    factor: float
    center: Point3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class TransformOp(_Op):
    """Scale, then rotate, then translate the named objects."""

    op: Literal["transform"]
    ids: Annotated[list[str], Field(min_length=1)]
    translate: Point3 | None = None
    rotate: Rotation | None = None
    scale: Scaling | None = None


class DeleteOp(_Op):
    op: Literal["delete"]
    ids: Annotated[list[str], Field(min_length=1)]


class AttributesOp(_Op):
    op: Literal["attributes"]
    ids: Annotated[list[str], Field(min_length=1)]
    name: str | None = None
    layer: str | None = None
    user_text: UserText


Operation = Annotated[
    Union[LayerOp, PolylineOp, ExtrusionOp, BoxOp, MeshOp, DotOp, ImportOp, TransformOp, DeleteOp, AttributesOp],
    Field(discriminator="op"),
]


class LiveRhinoSnapshotRequest(BaseModel):
    """Which open document to copy into the project as a model asset to draw from."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    rhino_id: str | None = Field(default=None, alias="rhinoId")
    document_path: str | None = Field(default=None, alias="documentPath")
    file_name: str | None = Field(default=None, alias="fileName", pattern=r"^[^/\\\r\n\x00]{1,200}\.3dm$")
    drawable_only: bool = Field(
        default=False, alias="drawableOnly",
        description="write only what the drawing routes project (Breps, extrusions, meshes, curves, blocks); "
                    "text dots, hatches, annotations and the like are left out and counted")
    layer_prefixes: list[str] | None = Field(
        default=None, alias="layerPrefixes", description="only objects on these layers or below them")


class LiveRhinoWriteRequest(BaseModel):
    """One write: all operations land as one Rhino command, undone with one Undo."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    rhino_id: str | None = Field(default=None, alias="rhinoId", description="from GET /api/live-rhino/instances; omit when one Rhino runs")
    document_path: str | None = Field(default=None, alias="documentPath", description="the open document this write is meant for")
    label: Annotated[str, Field(min_length=1, max_length=120, description="what this write does, shown in Rhino's undo list")]
    operations: Annotated[list[Operation], Field(min_length=1, max_length=5000)]
    allow_user_objects: bool = Field(
        default=False, alias="allowUserObjects",
        description="transform, delete and attributes may touch objects the architect made only when they asked for it",
    )


# ---- answers ----------------------------------------------------------------


class _Answer(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class LiveRhinoInstanceDto(_Answer):
    """One running Rhino with its script server on."""

    rhino_id: str = Field(alias="rhinoId", description="pass as rhinoId when more than one Rhino runs")
    process_id: int = Field(alias="processId")
    version: str | None = None
    document_title: str | None = Field(default=None, alias="documentTitle")
    document_path: str | None = Field(default=None, alias="documentPath", description="empty for an unsaved document")
    active_viewport: str | None = Field(default=None, alias="activeViewport")


class LiveRhinoInstancesDto(_Answer):
    instances: list[LiveRhinoInstanceDto]
    note: str


class LiveRhinoDocumentSummaryDto(_Answer):
    """The open document as Rhino holds it now."""

    title: str | None = None
    path: str | None = None
    units: str = Field(description="Rhino's model unit system, e.g. Feet")
    tolerance: float
    modified: bool = Field(description="unsaved changes; a write and its Undo leave it true")
    object_count: int = Field(alias="objectCount", description="the objects in the document, hidden and locked included")


class LiveRhinoCreatedDto(_Answer):
    operation: int = Field(description="index of the operation in the request")
    id: str = Field(description="the new object's id")
    op: str


class LiveRhinoWriteDto(_Answer):
    """What one write did: one Rhino command, undone with one Undo."""

    rhino: LiveRhinoInstanceDto
    write_id: str = Field(alias="writeId", description="stamped on every created object as monkeyhub.write")
    label: str
    created: list[LiveRhinoCreatedDto]
    changed: list[Any]
    deleted: list[Any]
    imported: list[Any]
    document: LiveRhinoDocumentSummaryDto


class LiveRhinoUndrawableDto(_Answer):
    """Which objects of the copy the drawing routes cannot project."""

    checked: bool
    drawable: int
    objects: list[dict[str, Any]] = Field(description="{id, reason} of each object the drawing routes would skip")


class LiveRhinoSnapshotDto(_Answer):
    """The copy of the open document registered as an external model asset."""

    rhino: LiveRhinoInstanceDto
    document: LiveRhinoDocumentSummaryDto
    meshed_objects: int = Field(alias="meshedObjects")
    written_objects: int = Field(alias="writtenObjects")
    omitted: dict[str, int] = Field(description="object type -> count left out with drawableOnly, e.g. TextDot, Hatch")
    artifact: ProjectArtifactDto = Field(description="pass {runId, assetSha256} to a drawing route as sourceAsset")
    undrawable: LiveRhinoUndrawableDto
