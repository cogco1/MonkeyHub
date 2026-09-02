"""Record-driven project runner (P089, first cut).

A project enters as three record packs and two datum records:

* ``SchematicPack@1`` — the selected schematic as data: levels, massing
  volumes, zones, connections and the semantic component tree with its
  evidence. It becomes a real ``SpatialOptionProposal`` / ``SchematicOption``
  / ``SelectedSchematicInput`` / ``DevelopedDesignState`` through the state
  dataclasses' own validation; no test fixture, no building literal.
* ``ElementPack@1`` — what each seat authors, as element specs bound to
  project levels by role: walls with hosted openings, prisms, ring walls,
  lofts, column arrays, dome caps. A producer turns a spec into neutral
  geometry operations; heights are differences of project levels or the
  element's own dimensions, never a restated elevation.
* ``SeatPack@1`` — the discipline seats (P095) and the provider identity.
* ``ProjectLevels@1`` / ``ProjectGrids@1`` (P098).

The runner owns orchestration only: seat rounds from ``schedule_seats``,
producers, one proposal per seat through the real producer, coverage and
datum gates, handovers to consuming seats (published datums and realized
bounds as exclusions), optional CAD export with read-back, and receipts
with per-seat wall time.  It will not run without a retained
``ProjectStageWorkflow@1`` and exact ``StageRunEnvelope@1``.  A successful
seat proposal is never reported as stage acceptance: the stage close
obligation remains OPEN until the independent stage-artifact/check/closure
path satisfies it.  Every record written here remains authority-free.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from archflow.adapters.cad_program import expected_object_bounds
from archflow.capabilities.discipline_seats import (
    SeatSpec,
    check_seat_datums,
    compile_handover,
    owned_subtree,
    project_seat_context,
    schedule_seats,
)
from archflow.capabilities.geometry_proposal import (
    GeometryProposalPolicy,
    GeometryProposalProviderIdentity,
    GeometryProposalStatus,
    produce_geometry_program_proposal,
    proposal_authoring_output,
)
from archflow.capabilities.opening_solver import DoorType, WindowType, solve_openings
from archflow.capabilities.wall_solver import OpeningKind, OpeningRequest, WallElement, WallSolverError, solve_wall
from archflow.contracts.authority import no_authority
from archflow.contracts.canonical import canonical_json
from archflow.ports.model import ModelInvocationReceipt, ModelInvocationStatus
from archflow.project import FilesystemProjectRepository, PersistenceArea, PersistenceDestination
from archflow.project.refs import BranchRef, ProjectRecordRef, RunRef, require_identifier
from archflow.control.stage_closure import (
    CompositeStageClosureReceipt,
    StageClosureStatus,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.design_portfolio import BranchRevisionRef
from archflow.state.developed_design import (
    DevelopedDesignState,
    DevelopmentCoordinationStatus,
    SelectedSchematicInput,
)
from archflow.state.geometry_program import (
    AffineTransform,
    CoordinateFrame,
    DatumBinding,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    HostedAssembly,
    InterfaceDatum,
    InterfaceDatumKind,
    LengthUnit,
    ProjectGrids,
    ProjectLevels,
    SemanticBinding,
)
from archflow.state.site_context import SiteBounds
from archflow.state.stage_workflow import (
    ProjectStageWorkflow,
    StageExitBinding,
    StageRunEnvelope,
    require_stage_exit_binding,
    require_stage_run_envelope,
)
from archflow.state.spatial import (
    DesignComponent,
    MassingVolume,
    SchematicOption,
    SpatialConnection,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialZone,
)

_AUTH = ("canonical_write_authority", "design_authority", "stage_acceptance_authority")
_M = LengthUnit.METER
FRAME_ID = "building-local"


class ProjectRunnerError(ValueError):
    """Typed failure of the runner's contracts."""


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectRunnerError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ProjectRunnerError(f"{field_name} must be finite")
    return number


# ---------------------------------------------------------------- schematic pack -> real state
@dataclass(frozen=True, slots=True)
class SchematicPack:
    project_id: str
    option_id: str
    label: str
    typology: str
    rationale: str
    evidence_refs: tuple[str, ...]
    levels: tuple[dict, ...]
    volumes: tuple[dict, ...]
    zones: tuple[dict, ...]
    connections: tuple[dict, ...]
    components: tuple[DesignComponent, ...]
    footprint_cells: tuple[tuple[int, int], ...]
    assumption_refs: tuple[str, ...] = ()

    SCHEMA = "SchematicPack@1"

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SchematicPack":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise ProjectRunnerError("schematic pack payload malformed")
        return cls(
            project_id=value["project_id"], option_id=value["option_id"], label=value["label"], typology=value["typology"],
            rationale=value["rationale"], evidence_refs=tuple(sorted(set(value["evidence_refs"]))),
            levels=tuple(value["levels"]), volumes=tuple(value["volumes"]), zones=tuple(value["zones"]), connections=tuple(value["connections"]),
            components=tuple(DesignComponent.from_dict(c) for c in value["components"]),
            footprint_cells=tuple((int(x), int(z)) for x, z in value["footprint_cells"]),
            assumption_refs=tuple(value.get("assumption_refs", ())),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "project_id": self.project_id, "option_id": self.option_id, "label": self.label, "typology": self.typology,
            "rationale": self.rationale, "evidence_refs": list(self.evidence_refs), "levels": list(self.levels), "volumes": list(self.volumes),
            "zones": list(self.zones), "connections": list(self.connections), "components": [c.to_dict() for c in self.components],
            "footprint_cells": [list(c) for c in self.footprint_cells], "assumption_refs": list(self.assumption_refs),
        }


def schematic_proposal(pack: SchematicPack) -> SpatialOptionProposal:
    """The pack as a validated spatial option; the dataclasses reject gaps."""

    ev = pack.evidence_refs
    levels = tuple(SpatialLevel(l["level_id"], int(l["base_y"]), int(l["height"]), ev) for l in pack.levels)
    volumes = tuple(MassingVolume(v["volume_id"], SiteBounds(tuple(int(c) for c in v["min"]), tuple(int(c) for c in v["max"])), tuple(v["level_ids"]), ev) for v in pack.volumes)
    zones = tuple(SpatialZone(zone_id=z["zone_id"], program_node_refs=tuple(z["program_node_refs"]), level_ids=tuple(z["level_ids"]), volume_ids=tuple(z["volume_ids"]), source_refs=ev) for z in pack.zones)
    connections = tuple(SpatialConnection(connection_id=c["connection_id"], source_zone_id=c["source_zone_id"], target_zone_id=c["target_zone_id"],
                                          relationship_refs=tuple(c["relationship_refs"]), directed=bool(c.get("directed", False)), source_refs=ev) for c in pack.connections)
    return SpatialOptionProposal(
        option_id=pack.option_id, label=pack.label, program_scenario_ref=None, footprint_range_ref=None,
        grid_basis=SpatialGridBasis(horizontal_area_per_cell=1.0, area_unit="square_metres", source_refs=ev),
        footprint_cells=pack.footprint_cells, levels=levels, volumes=volumes, zones=zones,
        components=tuple(sorted(pack.components, key=lambda c: c.component_id)),
        connections=tuple(sorted(connections, key=lambda c: c.connection_id)), constraint_responses=(),
        typology_hypothesis=pack.typology, palette_refs=(), rationale=pack.rationale, responds_to_refs=ev, expert_advice_refs=(), evidence_refs=ev,
    )


def bootstrap_developed_state(pack: SchematicPack, *, run: RunRef, portfolio_id: str, branch_id: str, selection_decision_ref: str) -> DevelopedDesignState:
    """A developed-design state whose selected schematic is the pack.

    The portfolio ceremony (branches, votes, handoff) is replaced by one
    declared selection: the pack *is* the selected option, and the record
    that carries it says so. Everything downstream is the real state.
    """

    if run.project_id != pack.project_id:
        raise ProjectRunnerError("schematic pack belongs to another project")
    proposal = schematic_proposal(pack)
    option = SchematicOption(proposal=proposal, footprint_area=float(len(proposal.footprint_cells)),
                             topology_signature=_digest({"components": [c.to_dict() for c in proposal.components], "option_id": proposal.option_id}))
    revision_digest = _digest({"branch_id": branch_id, "option_digest": option.option_digest})
    selected = SelectedSchematicInput(
        portfolio_id=portfolio_id, portfolio_digest=_digest({"portfolio_id": portfolio_id, "option_digest": option.option_digest}),
        project_id=run.project_id, run_id=run.run_id, base=run.base, branch_id=branch_id,
        revision=BranchRevisionRef(branch_id=branch_id, revision_id="revision-declared-selection", revision_digest=revision_digest),
        option=option, selection_transition_id="select-by-declared-record", selection_decision_ref=selection_decision_ref,
    )
    return DevelopedDesignState(
        selected_schematic=selected, active_phase=DesignPhase.DESIGN_DEVELOPMENT, coordination_status=DevelopmentCoordinationStatus.IN_PROGRESS,
        obligations=(), components=(), dependencies=(), advice=(), decisions=(), transitions=(), assumption_refs=tuple(sorted(set(pack.assumption_refs))),
    )


# ---------------------------------------------------------------- element pack -> producers
@dataclass(frozen=True, slots=True)
class ElementSpec:
    element_id: str
    component_id: str
    producer: str
    params: Mapping[str, Any]
    base_level: str | None = None

    def __post_init__(self) -> None:
        require_identifier(self.element_id, "element_id")
        require_identifier(self.component_id, "component_id")
        require_identifier(self.producer, "producer")
        if self.base_level is not None:
            require_identifier(self.base_level, "base_level")


@dataclass(frozen=True, slots=True)
class ElementPack:
    elements: tuple[ElementSpec, ...]

    SCHEMA = "ElementPack@1"

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ElementPack":
        if not isinstance(value, Mapping) or value.get("schema") != cls.SCHEMA:
            raise ProjectRunnerError("element pack payload malformed")
        elements = tuple(ElementSpec(e["element_id"], e["component_id"], e["producer"], dict(e.get("params", {})), e.get("base_level")) for e in value["elements"])
        ids = [e.element_id for e in elements]
        if len(set(ids)) != len(ids):
            raise ProjectRunnerError("element ids must be unique")
        return cls(elements=elements)

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "elements": [{"element_id": e.element_id, "component_id": e.component_id, "producer": e.producer,
                                                     "params": dict(e.params), "base_level": e.base_level} for e in self.elements]}


@dataclass(frozen=True, slots=True)
class ProducerInputs:
    levels: ProjectLevels
    grids: ProjectGrids | None
    exclusions: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...]
    frame_id: str = FRAME_ID


@dataclass(frozen=True, slots=True)
class Produced:
    operations: tuple[GeometryOperation, ...]
    bindings: tuple[DatumBinding, ...]
    assemblies: tuple[HostedAssembly, ...] = ()
    datums: tuple[InterfaceDatum, ...] = ()


def _level_value(levels: ProjectLevels, level_id: str) -> float:
    for item in levels.levels:
        if item.level_id == level_id:
            return item.elevation
    raise ProjectRunnerError(f"unknown project level {level_id!r}")


def _height(spec: ElementSpec, levels: ProjectLevels) -> float:
    """An element's height is its own dimension or a difference of levels."""

    if "height" in spec.params:
        return _finite(spec.params["height"], f"{spec.element_id} height")
    if "top_level" in spec.params:
        if spec.base_level is None:
            raise ProjectRunnerError(f"{spec.element_id}: top_level needs a base_level")
        return round(_level_value(levels, spec.params["top_level"]) - _level_value(levels, spec.base_level), 9)
    raise ProjectRunnerError(f"{spec.element_id}: height or top_level required")


def _points(name: str, pts) -> GeometryParameter:
    return GeometryParameter.create(name=name, kind=GeometryParameterKind.POINTS3, value=[[round(float(c), 9) for c in p] for p in pts], unit=_M)


def _extrusion(op_id: str, profile, height: float, binding_id: str, frame_id: str, base_offset: float | None = None) -> GeometryOperation:
    params = [_points("profile", profile), GeometryParameter.create(name="vector", kind=GeometryParameterKind.VECTOR3, value=[0.0, round(height, 9), 0.0], unit=_M)]
    if base_offset is not None:
        params.insert(0, GeometryParameter.create(name="base_offset", kind=GeometryParameterKind.NUMBER, value=round(base_offset, 9), unit=_M))
    return GeometryOperation(op_id=op_id, kind=GeometryOperationKind.EXTRUSION, output_object_ids=(f"obj-{op_id}",), input_object_ids=(), frame_id=frame_id,
                             parameters=tuple(params), semantic_binding_ids=(binding_id,))


def _bind(op_id: str, datum_id: str) -> DatumBinding:
    return DatumBinding(binding_id=f"bind-{op_id}", datum_id=datum_id, op_id=op_id, parameter_name="base_level")


def _require_base(spec: ElementSpec) -> str:
    if spec.base_level is None:
        raise ProjectRunnerError(f"{spec.element_id}: {spec.producer} needs a base_level")
    return spec.base_level


def _produce_wall(spec: ElementSpec, inputs: ProducerInputs) -> Produced:
    p = spec.params
    base = _require_base(spec)
    wall = WallElement(spec.element_id, tuple(p["origin"]), tuple(p["direction"]), _finite(p["length"], "length"), _finite(p["thickness"], "thickness"),
                       _height(spec, inputs.levels), base, inputs.frame_id, f"binding-{spec.component_id}")
    openings = tuple(OpeningRequest(o["opening_id"], OpeningKind(o["kind"]), _finite(o["along"], "along"), _finite(o["width"], "width"), _finite(o["sill"], "sill"),
                                    _finite(o["head"], "head"), f"binding-{o.get('component_id', spec.component_id)}", int(o.get("count", 1)), _finite(o.get("step", 0.0), "step"))
                     for o in p.get("openings", ()))
    exclusions = inputs.exclusions if p.get("respect_exclusions", True) else ()
    solution = solve_wall(wall, openings, exclusions=exclusions, base_elevation=_level_value(inputs.levels, base) if exclusions else None)
    assemblies, ops, bindings = [], list(solution.operations), list(solution.datum_bindings)
    types = {}
    for t in p.get("types", ()):
        fields = {k: v for k, v in t.items() if k not in ("schema", "kind")}
        types[t["type_id"]] = WindowType(**fields) if "glazing_thickness" in fields else DoorType(**fields)
    fills_by = {o["opening_id"]: o["type_id"] for o in p.get("openings", ()) if o.get("type_id")}
    filled = tuple(v for v in solution.voids if v.opening_id in fills_by)
    if filled:
        fills = solve_openings(filled, {v.opening_id: types[fills_by[v.opening_id]] for v in filled},
                               binding_ids={v.opening_id: f"binding-{next(o.get('component_id', spec.component_id) for o in p['openings'] if o['opening_id'] == v.opening_id)}" for v in filled},
                               interface_refs={v.opening_id: next(o["interface_ref"] for o in p["openings"] if o["opening_id"] == v.opening_id) for v in filled if any(o.get("interface_ref") for o in p["openings"] if o["opening_id"] == v.opening_id)})
        for fill in fills:
            ops.extend(fill.operations); bindings.extend(fill.datum_bindings); assemblies.append(fill.assembly)
    return Produced(tuple(ops), tuple(bindings), tuple(assemblies))


def _produce_prism(spec: ElementSpec, inputs: ProducerInputs) -> Produced:
    p = spec.params
    base = _require_base(spec)
    profile = [(float(x), 0.0, float(z)) for x, z in p["profile"]]
    op = _extrusion(spec.element_id, profile, _height(spec, inputs.levels), f"binding-{spec.component_id}", inputs.frame_id, p.get("base_offset"))
    return Produced((op,), (_bind(spec.element_id, base),))


def _produce_ring(spec: ElementSpec, inputs: ProducerInputs) -> Produced:
    p = spec.params
    base = _require_base(spec)
    cx, cz = (float(v) for v in p["centre"])
    r_in, r_out, pieces = _finite(p["inner_radius"], "inner_radius"), _finite(p["outer_radius"], "outer_radius"), int(p.get("pieces", 8))
    if r_out <= r_in:
        raise ProjectRunnerError(f"{spec.element_id}: outer radius must exceed inner radius")
    height = _height(spec, inputs.levels)
    ops, bindings = [], []
    for k in range(pieces):
        a0, a1 = 2 * math.pi * k / pieces, 2 * math.pi * (k + 1) / pieces
        arc = [a0 + (a1 - a0) * i / 6 for i in range(7)]
        outer = [(cx + r_out * math.cos(a), 0.0, cz + r_out * math.sin(a)) for a in arc]
        inner = [(cx + r_in * math.cos(a), 0.0, cz + r_in * math.sin(a)) for a in reversed(arc)]
        op_id = f"{spec.element_id}-{k}"
        ops.append(_extrusion(op_id, outer + inner, height, f"binding-{spec.component_id}", inputs.frame_id))
        bindings.append(_bind(op_id, base))
    return Produced(tuple(ops), tuple(bindings))


def _produce_loft(spec: ElementSpec, inputs: ProducerInputs) -> Produced:
    p = spec.params
    base = _require_base(spec)
    profiles = [[float(c) for c in pt] for section in p["profiles"] for pt in section]
    size = int(p["profile_size"])
    op = GeometryOperation(op_id=spec.element_id, kind=GeometryOperationKind.LOFT, output_object_ids=(f"obj-{spec.element_id}",), input_object_ids=(), frame_id=inputs.frame_id, parameters=(
        GeometryParameter.create(name="cap_ends", kind=GeometryParameterKind.BOOLEAN, value=True),
        GeometryParameter.create(name="loft_type", kind=GeometryParameterKind.TEXT, value=str(p.get("loft_type", "straight"))),
        GeometryParameter.create(name="profile_basis", kind=GeometryParameterKind.TEXT, value="polyline"),
        GeometryParameter.create(name="profile_size", kind=GeometryParameterKind.INTEGER, value=size),
        _points("profiles", profiles),
    ), semantic_binding_ids=(f"binding-{spec.component_id}",))
    return Produced((op,), (_bind(spec.element_id, base),))


def _produce_column_array(spec: ElementSpec, inputs: ProducerInputs) -> Produced:
    p = spec.params
    base = _require_base(spec)
    ox, oz = (float(v) for v in p["origin"])
    dx, dz = (float(v) for v in p["direction"])
    norm = math.hypot(dx, dz)
    if norm == 0.0:
        raise ProjectRunnerError(f"{spec.element_id}: direction must be nonzero")
    dx, dz = dx / norm, dz / norm
    count, spacing, radius = int(p["count"]), _finite(p["spacing"], "spacing"), _finite(p["radius"], "radius")
    segments = int(p.get("segments", 24))
    height = _height(spec, inputs.levels)
    ops, bindings = [], []
    for k in range(count):
        along = (k - (count - 1) / 2.0) * spacing
        cx, cz = ox + dx * along, oz + dz * along
        profile = [(cx + radius * math.cos(2 * math.pi * i / segments), 0.0, cz + radius * math.sin(2 * math.pi * i / segments)) for i in range(segments)]
        op_id = f"{spec.element_id}-{k}"
        ops.append(_extrusion(op_id, profile, height, f"binding-{spec.component_id}", inputs.frame_id))
        bindings.append(_bind(op_id, base))
    top = InterfaceDatum.create(datum_id=f"{spec.element_id}-top", kind=InterfaceDatumKind.LEVEL, published_by=f"obj-{spec.element_id}-0",
                                value=round(_level_value(inputs.levels, base) + height, 9), unit=_M, basis_refs=tuple(sorted(set(p.get("basis_refs", ()))) or ()))
    return Produced(tuple(ops), tuple(bindings), (), (top,))


def _produce_dome_cap(spec: ElementSpec, inputs: ProducerInputs) -> Produced:
    p = spec.params
    base = _require_base(spec)
    cx, cz = (float(v) for v in p["centre"])
    a, h = _finite(p["base_radius"], "base_radius"), _height(spec, inputs.levels)
    rings, top_fraction, n = int(p.get("rings", 5)), _finite(p.get("top_fraction", 0.95), "top_fraction"), int(p.get("segments", 24))
    sphere = (a * a + h * h) / (2 * h)
    profiles = []
    for i in range(rings):
        y = h * (i / (rings - 1)) * top_fraction if i < rings - 1 else h * top_fraction
        rr = math.sqrt(max(sphere * sphere - (sphere - h + y) ** 2, 0.0))
        profiles.append([(cx + rr * math.cos(2 * math.pi * j / n), y, cz + rr * math.sin(2 * math.pi * j / n)) for j in range(n)])
    return _produce_loft(replace(spec, params={**p, "profiles": profiles, "profile_size": n}), inputs)


def _produce_declined(spec: ElementSpec, inputs: ProducerInputs) -> Produced:
    """A typed declination: the component is owned, looked at, and left without geometry for a stated reason."""

    reason = spec.params.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ProjectRunnerError(f"{spec.element_id}: a declination needs a reason")
    return Produced((), ())


PRODUCERS: dict[str, Callable[[ElementSpec, ProducerInputs], Produced]] = {
    "wall": _produce_wall, "prism": _produce_prism, "ring": _produce_ring, "loft": _produce_loft, "column-array": _produce_column_array, "dome-cap": _produce_dome_cap,
    "declined": _produce_declined,
}


def produce_elements(elements: tuple[ElementSpec, ...], inputs: ProducerInputs) -> Produced:
    ops, bindings, assemblies, datums = [], [], [], []
    for spec in elements:
        producer = PRODUCERS.get(spec.producer)
        if producer is None:
            raise ProjectRunnerError(f"{spec.element_id}: unknown producer {spec.producer!r}")
        try:
            produced = producer(spec, inputs)
        except WallSolverError as exc:
            raise ProjectRunnerError(f"{spec.element_id}: {exc}") from exc
        ops.extend(produced.operations); bindings.extend(produced.bindings); assemblies.extend(produced.assemblies); datums.extend(produced.datums)
    ids = [op.op_id for op in ops]
    if len(set(ids)) != len(ids):
        raise ProjectRunnerError("element producers emitted duplicate operation ids")
    return Produced(tuple(sorted(ops, key=lambda o: o.op_id)), tuple(sorted(bindings, key=lambda b: b.binding_id)),
                    tuple(sorted(assemblies, key=lambda a: a.assembly_id)), tuple(sorted(datums, key=lambda d: d.datum_id)))


# ---------------------------------------------------------------- provider: the proposal, recorded
class RecordedProposalProvider:
    """Returns one authored proposal as a provider receipt; nothing is invented."""

    def __init__(self, output: Mapping[str, object], identity: GeometryProposalProviderIdentity) -> None:
        self._output = output
        self.identity = identity
        self.requests: list = []

    async def invoke(self, request):
        self.requests.append(request)
        if len(self.requests) > 1:
            return ModelInvocationReceipt(
                receipt_id=f"runner-receipt-{len(self.requests):02d}", status=ModelInvocationStatus.BUDGET_EXHAUSTED,
                request=request, provider_id=self.identity.provider_id, model_id=self.identity.model_id, provider_version=self.identity.provider_version,
                provider_fingerprint=self.identity.provider_fingerprint, input_bytes=len(request.payload_json.encode("utf-8")), output_bytes=0,
                output_sha256=None, output_json=None, error_code="runner_single_round", message="the runner authors one proposal per seat; repair rounds are recorded, not improvised")
        encoded = canonical_json(self._output)
        return ModelInvocationReceipt(
            receipt_id="runner-receipt-01", status=ModelInvocationStatus.SUCCESS, request=request, provider_id=self.identity.provider_id,
            model_id=self.identity.model_id, provider_version=self.identity.provider_version, provider_fingerprint=self.identity.provider_fingerprint,
            input_bytes=len(request.payload_json.encode("utf-8")), output_bytes=len(encoded.encode("utf-8")),
            output_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(), output_json=encoded)


# ---------------------------------------------------------------- the run
@dataclass(frozen=True, slots=True)
class SeatResult:
    seat_id: str
    round_index: int
    status: str
    program_ref: str | None
    program_digest: str | None
    objects: int
    covered_components: tuple[str, ...]
    undeclared_components: tuple[str, ...]
    issues: tuple[dict, ...]
    wall_time_s: float
    cad: dict | None = None
    receipt_ref: str | None = None
    declined: tuple[str, ...] = ()
    declination_reasons: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunOptions:
    commitment_ref: str
    provider_identity: GeometryProposalProviderIdentity
    portfolio_id: str = "declared-schematic"
    branch_id: str = "runner-v1"
    branch_epoch: int = 1
    selection_decision_ref: str = "decision:declared-schematic-selection"
    strict_coverage: bool = True
    export: bool = False
    workspace_root: Path | None = None
    powershell: Path | None = None


@dataclass(frozen=True, slots=True)
class StageExecutionGuard:
    """Verified retained inputs required before any seat record is written.

    Stage zero needs only the workflow and its own envelope.  A successor
    additionally needs the exact predecessor envelope, its retained
    ``StageExitBinding@1`` and the independently compiled SATISFIED closure
    receipt named by that binding.  Passing equivalent-looking in-memory
    payloads without their P036 refs is intentionally insufficient.
    """

    workflow: ProjectStageWorkflow
    workflow_record_ref: ProjectRecordRef
    envelope: StageRunEnvelope
    envelope_record_ref: ProjectRecordRef
    predecessor: StageRunEnvelope | None = None
    predecessor_record_ref: ProjectRecordRef | None = None
    predecessor_exit: StageExitBinding | None = None
    predecessor_exit_record_ref: ProjectRecordRef | None = None
    predecessor_closure: CompositeStageClosureReceipt | None = None
    predecessor_closure_record_ref: ProjectRecordRef | None = None

    def require(
        self,
        repository: FilesystemProjectRepository,
        *,
        run: RunRef,
        branch: BranchRef,
        state: DevelopedDesignState,
        seats: tuple[SeatSpec, ...],
    ) -> None:
        def retained(ref: ProjectRecordRef, payload: Mapping[str, object], label: str) -> None:
            if ref.project_id != run.project_id:
                raise ProjectRunnerError(f"{label} belongs to another project")
            try:
                actual = repository.load_json(ref)
            except Exception as exc:
                raise ProjectRunnerError(f"{label} is not an exact retained P036 record") from exc
            if actual != dict(payload):
                raise ProjectRunnerError(f"{label} retained content does not match the supplied typed record")

        retained(self.workflow_record_ref, self.workflow.to_dict(), "stage workflow")
        retained(self.envelope_record_ref, self.envelope.to_dict(), "stage envelope")
        if self.envelope.workflow_ref != self.workflow_record_ref.uri:
            raise ProjectRunnerError("stage envelope does not name the retained workflow record")
        if self.envelope.project_id != run.project_id or self.envelope.run_id != run.run_id:
            raise ProjectRunnerError("stage envelope belongs to another project or run")
        if (
            self.envelope.base_version != run.base.version
            or self.envelope.base_state_sha256 != run.base.require_digest()
        ):
            raise ProjectRunnerError("stage envelope does not bind the run's exact canonical base")
        if (
            self.envelope.branch_id != branch.branch_id
            or self.envelope.branch_epoch != branch.epoch
        ):
            raise ProjectRunnerError("stage envelope belongs to another branch or epoch")
        if self.envelope.state_digest != state.state_digest:
            raise ProjectRunnerError("stage envelope does not bind the exact developed state")
        if self.envelope.phase is not state.active_phase:
            raise ProjectRunnerError("stage envelope phase disagrees with the developed state")
        if any(
            self.envelope.phase not in seat.phases
            for seat in seats
            if not seat.reviewer
        ):
            raise ProjectRunnerError("a producing seat is not admitted in the envelope phase")

        require_stage_run_envelope(
            self.workflow,
            self.envelope,
            workflow_ref=self.workflow_record_ref.uri,
            predecessor=self.predecessor,
            predecessor_ref=(
                None
                if self.predecessor_record_ref is None
                else self.predecessor_record_ref.uri
            ),
            predecessor_exit=self.predecessor_exit,
            predecessor_exit_ref=(
                None
                if self.predecessor_exit_record_ref is None
                else self.predecessor_exit_record_ref.uri
            ),
        )
        successor_values = (
            self.predecessor,
            self.predecessor_record_ref,
            self.predecessor_exit,
            self.predecessor_exit_record_ref,
            self.predecessor_closure,
            self.predecessor_closure_record_ref,
        )
        if self.envelope.stage_index == 0:
            if any(value is not None for value in successor_values):
                raise ProjectRunnerError("stage 0 cannot carry predecessor completion")
            return
        if any(value is None for value in successor_values):
            raise ProjectRunnerError(
                "stage N requires retained predecessor envelope, exit binding, and closure"
            )
        assert self.predecessor is not None
        assert self.predecessor_record_ref is not None
        assert self.predecessor_exit is not None
        assert self.predecessor_exit_record_ref is not None
        assert self.predecessor_closure is not None
        assert self.predecessor_closure_record_ref is not None
        retained(
            self.predecessor_record_ref,
            self.predecessor.to_dict(),
            "predecessor stage envelope",
        )
        retained(
            self.predecessor_exit_record_ref,
            self.predecessor_exit.to_dict(),
            "predecessor stage exit binding",
        )
        retained(
            self.predecessor_closure_record_ref,
            self.predecessor_closure.to_dict(),
            "predecessor stage closure",
        )
        require_stage_exit_binding(
            self.predecessor,
            self.predecessor_exit,
            envelope_ref=self.predecessor_record_ref.uri,
        )
        closure = self.predecessor_closure
        if self.predecessor_exit.closure_ref != self.predecessor_closure_record_ref.uri:
            raise ProjectRunnerError("stage exit binding does not name the retained closure")
        if (
            closure.status is not StageClosureStatus.SATISFIED
            or closure.receipt_digest != self.predecessor_exit.closure_digest
            or closure.stage_id != self.predecessor.stage_id
            or closure.stage_subject_ref != self.predecessor.subject_ref
            or closure.subject_digest != self.predecessor.state_digest
            or closure.branch.run.project_id != self.predecessor.project_id
            or closure.branch.run.run_id != self.predecessor.run_id
            or closure.branch.run.base.version != self.predecessor.base_version
            or closure.branch.run.base.require_digest()
            != self.predecessor.base_state_sha256
            or closure.branch.branch_id != self.predecessor.branch_id
            or closure.branch.epoch != self.predecessor.branch_epoch
        ):
            raise ProjectRunnerError("predecessor closure is not SATISFIED for the exact stage state")


def _subtree_leaves(proposal: SpatialOptionProposal, subtree: tuple[str, ...]) -> tuple[str, ...]:
    parents = {c.parent_component_id for c in proposal.components if c.parent_component_id}
    return tuple(sorted(c for c in subtree if c not in parents))


def _export(repository, run, branch, branch_destination, program, stage_id: str, options: RunOptions, provenance: dict) -> dict:
    from archflow.adapters.cad_execution import RhinoCadProgramBinding, execute_rhino_three_dm_export, prepare_rhino_three_dm_export
    from archflow.adapters.three_dm_inspector import inspect_three_dm

    workspace = (options.workspace_root or Path(".")) / f"cad-{stage_id}"
    if not workspace.is_dir():
        raise ProjectRunnerError(f"export workspace {workspace} does not exist: the caller prepares workspaces; the runner never creates files outside records")
    destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id)
    model = workspace / f"{stage_id}.3dm"
    if model.exists():
        # a succeeded export of this exact program is reused; anything else is set aside so the workspace is clean
        records_dir = Path(repository.layout.run(run.run_id).records)
        for path in sorted(records_dir.glob("seat-rhino-execution-*.json"), key=lambda q: q.stat().st_mtime, reverse=True):
            payload = _load_json(path)
            if payload.get("status") == "succeeded" and program.program_digest in canonical_json(payload):
                return {"execution_ref": f"project://{run.project_id}/runs/{run.run_id}/records/{path.name}", "status": "succeeded",
                        "readback_verified": payload.get("readback_verified"), "failures": [], "model": str(model), "reused": True}
        raise ProjectRunnerError(
            f"speculative workspace {workspace} holds an export of another program ({model.name}); "
            "set it aside and rerun — the runner never moves or deletes files"
        )
    program_ref = repository.put_json(run=run, destination=branch_destination, record_kind=f"{stage_id}-geometry-program", payload=program.to_dict())
    binding = RhinoCadProgramBinding(program_ref=program_ref, branch=branch, stage_id=stage_id, program_digest=program.program_digest,
                                     design_state_digest=program.proposal.design_state_digest, predecessor_program_digest=None)
    plan = prepare_rhino_three_dm_export(program, binding=binding, speculative_workspace=workspace, artifact_name=f"{stage_id}.3dm", readback_tolerance=0.003, provenance=provenance)
    execution = execute_rhino_three_dm_export(plan, powershell_executable=options.powershell, timeout_seconds=900)
    execution_ref = repository.put_json(run=run, destination=destination, record_kind="seat-rhino-execution", payload=execution.to_dict())
    cad = {"execution_ref": execution_ref.uri, "status": execution.status.value, "readback_verified": execution.readback_verified,
           "failures": [dict(f) if isinstance(f, dict) else str(f) for f in execution.failures], "model": str(plan.model_path)}
    if execution.status.value == "succeeded":
        cad["inspection_ref"] = repository.put_json(run=run, destination=destination, record_kind="seat-3dm-inspection", payload=inspect_three_dm(plan.model_path).to_dict()).uri
    return cad


def run_project(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    stage_guard: StageExecutionGuard,
    schematic: SchematicPack,
    elements: ElementPack,
    seats: tuple[SeatSpec, ...],
    levels: ProjectLevels,
    grids: ProjectGrids | None,
    options: RunOptions,
) -> dict[str, object]:
    """Run admitted seat rounds; return an authority-free stage-run receipt."""

    started = time.perf_counter()
    destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id)
    branch = BranchRef(run=run, branch_id=options.branch_id, epoch=options.branch_epoch)
    branch_destination = PersistenceDestination(PersistenceArea.RUN_BRANCH, run_id=run.run_id, branch_id=options.branch_id)
    put = lambda kind, payload: repository.put_json(run=run, destination=destination, record_kind=kind, payload=payload)
    # Compute and admit the exact state before the first write.  This prevents
    # a raw create_run or a copied pack from acquiring a stage by side effect.
    state = bootstrap_developed_state(schematic, run=run, portfolio_id=options.portfolio_id, branch_id=options.branch_id, selection_decision_ref=options.selection_decision_ref)
    stage_guard.require(
        repository,
        run=run,
        branch=branch,
        state=state,
        seats=seats,
    )
    schematic_ref = put("runner-schematic-pack", {**schematic.to_dict(), **no_authority(_AUTH)})
    elements_ref = put("runner-element-pack", {**elements.to_dict(), **no_authority(_AUTH)})
    levels_ref = put("project-levels", {**levels.to_dict(), **no_authority(_AUTH)})
    grids_ref = put("project-grids", {**grids.to_dict(), **no_authority(_AUTH)}).uri if grids is not None else None
    proposal_tree = state.selected_schematic.option.proposal
    spatial_ref = put("selected-spatial-option", proposal_tree.to_dict())
    state_ref = put("developed-design-state", {**state.to_dict(), **no_authority(_AUTH)}) if hasattr(state, "to_dict") else None
    seat_by_id = {s.seat_id: s for s in seats}
    seat_refs = {s.seat_id: put("discipline-seat", s.to_dict()).uri for s in seats}
    rounds = schedule_seats(seats)
    project_datums = tuple(sorted(levels.datums() + (grids.datums() if grids is not None else ()), key=lambda d: d.datum_id))
    programs: dict[str, Any] = {}
    handovers_for: dict[str, list] = {s.seat_id: [] for s in seats}
    results: list[SeatResult] = []
    frame = CoordinateFrame(frame_id=FRAME_ID, parent_frame_id=None, transform_from_parent=AffineTransform.identity(), source_refs=tuple(schematic.evidence_refs[:1]) or (schematic_ref.uri,))
    for round_index, round_seats in enumerate(rounds):
        for seat_id in round_seats:
            seat = seat_by_id[seat_id]
            if seat.reviewer:
                continue
            t0 = time.perf_counter()
            subtree = owned_subtree(proposal_tree, seat.owned_component_ids)
            leaves = _subtree_leaves(proposal_tree, subtree)
            own = tuple(e for e in elements.elements if e.component_id in set(subtree))
            handovers = tuple(handovers_for[seat_id])
            context = project_seat_context(seat=seat, design_state=state, inherited_commitment_refs=(options.commitment_ref,), handovers=handovers,
                                           project_levels=levels, project_grids=grids)
            context_ref = put("seat-authoring-context", context.to_dict())
            exclusions = tuple(b for h in handovers for b in _exclusion_bounds(h))
            produced = produce_elements(own, ProducerInputs(levels=levels, grids=grids, exclusions=exclusions))
            check_seat_datums(seat=seat, published_datums=produced.datums, project_levels=levels, project_grids=grids)
            declined = tuple(sorted({e.component_id for e in own if e.producer == "declined"}))
            # a component is covered when an element names it or when produced geometry is bound to it
            # (openings hosted by a wall bind their own components)
            realized = {b.removeprefix("binding-") for op in produced.operations for b in op.semantic_binding_ids}
            covered = tuple(sorted({e.component_id for e in own if e.producer != "declined"} | (realized & set(subtree))))
            undeclared = tuple(c for c in leaves if c not in set(covered) | set(declined))
            if undeclared and options.strict_coverage:
                raise ProjectRunnerError(f"seat {seat_id!r} owns components with no element and no declination: {undeclared}")
            if not covered:
                results.append(SeatResult(seat_id, round_index, "empty", None, None, 0, covered, undeclared, (), time.perf_counter() - t0, declined=declined))
                continue
            by_component: dict[str, list[str]] = {}
            for op in produced.operations:
                by_component.setdefault(op.semantic_binding_ids[0].removeprefix("binding-"), []).extend(op.output_object_ids)
            bindings = tuple(SemanticBinding(binding_id=f"binding-{c}", component_id=c, object_ids=tuple(sorted(o)), commitment_refs=(options.commitment_ref,),
                                             evidence_refs=tuple(sorted({spatial_ref.uri, elements_ref.uri}))) for c, o in sorted(by_component.items()))
            proposal = GeometryProgramProposal(
                proposal_id=f"{run.project_id}-{seat_id}-round-{round_index}", project_id=run.project_id, run_id=run.run_id, base=run.base,
                design_state_digest=state.state_digest, predecessor_program_digest=None, length_unit=_M, tolerance=GeometryTolerance(0.001, 0.001),
                frames=(frame,), assets=(), semantic_bindings=bindings, operations=produced.operations, assemblies=produced.assemblies)
            datums = tuple(sorted({d.datum_id: d for d in project_datums + tuple(d for h in handovers for d in h.datums) + produced.datums}.values(), key=lambda d: d.datum_id))
            provider = RecordedProposalProvider(proposal_authoring_output(proposal), options.provider_identity)
            result = asyncio.run(produce_geometry_program_proposal(
                repository, provider, run=run, destination=destination, spatial_option_ref=spatial_ref, design_state=state,
                required_commitment_refs=(options.commitment_ref,), provider_identity=options.provider_identity, policy=GeometryProposalPolicy(1),
                seat_scope=subtree, interface_datums=datums, datum_bindings=produced.bindings))
            issues = tuple(row for r in result.round_refs for row in repository.load_json(r).get("issues", []))
            if result.status is not GeometryProposalStatus.ACCEPTED:
                results.append(SeatResult(seat_id, round_index, result.status.value, None, None, 0, covered, undeclared, issues, time.perf_counter() - t0))
                break
            program = result.program
            program_ref = put("seat-geometry-program", program.to_dict())
            programs[seat_id] = program
            bounds = expected_object_bounds(program)
            realized = {oid: (tuple(row["bbox_min"]), tuple(row["bbox_max"])) for oid, row in bounds.items()}
            digests = {o.object_id: o.object_digest for o in program.objects}
            for consumer in seats:
                if seat_id in consumer.consumes and not consumer.reviewer:
                    handover = compile_handover(from_seat=seat, to_seat=consumer, design_state=state, published_datums=program.interface_datums,
                                                program_bindings=program.proposal.semantic_bindings, realized_bounds=realized, object_digests=digests)
                    put("seat-handover", handover.to_dict())
                    handovers_for[consumer.seat_id].append(handover)
            cad = None
            if options.export:
                cad = _export(repository, run, branch, branch_destination, program, f"{stage_guard.envelope.stage_id}-{seat_id}", options,
                              {"target": "PROJECT_RUNNER", "stage_id": stage_guard.envelope.stage_id, "stage_index": stage_guard.envelope.stage_index, "stage_envelope_ref": stage_guard.envelope_record_ref.uri, "seat": seat_id, "candidate_status": "HOLD", "frame_semantics": "BUILDING_LOCAL_Y_UP", "schematic_pack_ref": schematic_ref.uri, "element_pack_ref": elements_ref.uri})
            seat_result = SeatResult(seat_id, round_index, "proposal_accepted", program_ref.uri, program.program_digest, len(program.objects), covered, undeclared, issues, time.perf_counter() - t0, cad, declined=declined,
                                     declination_reasons={e.component_id: str(e.params.get("reason")) for e in own if e.producer == "declined"})
            receipt_ref = put("seat-round-receipt", {"schema": "SeatRoundReceipt@1", "stage_id": stage_guard.envelope.stage_id, "stage_index": stage_guard.envelope.stage_index, "stage_envelope_ref": stage_guard.envelope_record_ref.uri, **_seat_dict(seat_result), "context_ref": context_ref.uri, "seat_ref": seat_refs[seat_id], **no_authority(_AUTH)})
            results.append(replace(seat_result, receipt_ref=receipt_ref.uri))
        else:
            continue
        break
    owned_any = set()
    for seat in seats:
        owned_any.update(owned_subtree(proposal_tree, seat.owned_component_ids))
    unowned = tuple(sorted(c for c in _subtree_leaves(proposal_tree, tuple(c.component_id for c in proposal_tree.components)) if c not in owned_any))
    payload = {
        "schema": "RunnerRunReceipt@2", "project_id": run.project_id, "run_id": run.run_id, "schematic_pack_ref": schematic_ref.uri, "element_pack_ref": elements_ref.uri,
        "unowned_components": list(unowned),
        "levels_ref": levels_ref.uri, "grids_ref": grids_ref, "spatial_option_ref": spatial_ref.uri, "design_state_ref": state_ref.uri if state_ref else None,
        "design_state_digest": state.state_digest,
        "workflow_ref": stage_guard.workflow_record_ref.uri,
        "workflow_digest": stage_guard.workflow.workflow_digest,
        "stage_envelope_ref": stage_guard.envelope_record_ref.uri,
        "stage_envelope_digest": stage_guard.envelope.envelope_digest,
        "stage": {
            "stage_id": stage_guard.envelope.stage_id,
            "stage_index": stage_guard.envelope.stage_index,
            "phase": stage_guard.envelope.phase.value,
            "status": "OPEN",
            "close_obligation_id": stage_guard.envelope.close_obligation.obligation_id,
        },
        "rounds": [list(r) for r in rounds], "seat_results": [_seat_dict(s) for s in results],
        "seat_execution_complete": all(s.status in ("proposal_accepted", "empty") for s in results) and any(s.status == "proposal_accepted" for s in results),
        "wall_time_s": round(time.perf_counter() - started, 3), **no_authority(_AUTH),
    }
    payload["receipt_ref"] = put("runner-run-receipt", payload).uri
    return payload


def _load_json(path: Path) -> dict:
    import json as _json

    return _json.loads(path.read_text(encoding="utf-8"))


def _exclusion_bounds(handover) -> tuple:
    import json as _json

    out = []
    for c in handover.constraints:
        if c.kind.value != "exclusion_bounds":
            continue
        payload = _json.loads(c.payload_json)
        low, high = payload.get("min"), payload.get("max")
        if low is not None and high is not None:
            out.append((tuple(float(v) for v in low), tuple(float(v) for v in high)))
    return tuple(out)


def _seat_dict(seat: SeatResult) -> dict[str, object]:
    return {"seat_id": seat.seat_id, "round": seat.round_index, "status": seat.status, "program_ref": seat.program_ref, "program_digest": seat.program_digest,
            "objects": seat.objects, "covered_components": list(seat.covered_components), "undeclared_components": list(seat.undeclared_components),
            "declined_components": list(seat.declined), "declination_reasons": dict(seat.declination_reasons),
            "issues": list(seat.issues), "wall_time_s": round(seat.wall_time_s, 3), "cad": seat.cad, "receipt_ref": seat.receipt_ref}
