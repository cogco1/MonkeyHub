"""Public synthetic StateRecord, retained OCCT STEP, and read-only lab observations.

There is one geometric source: the authored record goes through existing element
producers/compiler and the existing OCCT export/readback boundary. Gold answers
belong to fixture_protocol.md and tests, never to this query adapter.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from io import BytesIO
from math import ceil, isfinite
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

from archflow.adapters.cad_execution import CadProgramBinding, execute_occt_export
from archflow.adapters.occt_backend import classify_point, measure_occt_solid_pairs, measure_shape
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import SEAT_OCCT_EXECUTION, STATE_RECORD, stage_geometry_program
from archflow.project.refs import BranchRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.geometry_program import (
    AffineTransform, CoordinateFrame, GeometryProgramProposal, GeometryTolerance,
    LengthUnit, SemanticBinding,
)
from archflow.state.state_record import (
    Entity, Relation, StateRecord, RECORD_BINDING_PHASE, developed_design_view,
    project_grids_of, project_levels_of,
)
from monkeyarch.capabilities.element_producers import ProductionContext, element_rows_of, produce_rows
from monkeyarch.capabilities.reference_resolver import ReferenceContext
from monkeyarch.compilers.geometry import compile_geometry_program
from monkeydiagram.drawing_elevation import (
    ElevationSource, project_model_axis_elevation, read_elevation_source,
)

VIEWS = ("front", "back", "left", "right", "top")
EVIDENCE = "evidence:public-spatial-observation-fixture"
COMMITMENT = "commitment:preserve-authored-fixture"


class SourceMismatch(ValueError):
    """An observation request does not name this exact retained source."""


def cad_to_hub(point):
    """CAD (x,y,z), Z-up -> Hub (x,z,y), Y-up; no unit conversion."""
    x, y, z = point
    return (x, z, y)


def hub_to_cad(point):
    """Hub (x,y,z), Y-up -> CAD (x,z,y), Z-up; no unit conversion."""
    return cad_to_hub(point)


def authored_record(*, variant="base", scale=1.0) -> StateRecord:
    """Only declared model inputs. Neither expected answers nor selector gold."""
    if variant not in ("base", "heldout", "role-drift"):
        raise ValueError("variant must be base, heldout or role-drift")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not isfinite(scale) or scale <= 0:
        raise ValueError("scale must be finite and positive")
    heldout = variant == "heldout"
    entities = [Entity("building", "Component@1", {
        "semantic_kind": "building", "intent": "Public synthetic observation fixture",
        "typology": "synthetic CAD objects", "source_refs": [EVIDENCE],
    }), Entity("ground", "Level@1", {"role": "terrain-grade", "elevation": 0.0}, basis_refs=(EVIDENCE,))]

    def prism(identifier, profile, height, elevation=0.0, role=None, label=None):
        entities.append(Entity(identifier, "Element@1", {
            "component_id": "building", "producer": "prism",
            "label": label or identifier, "role": role,
            "references": {"base": {"level": "ground"}},
            "params": {"profile": [[x * scale, y * scale] for x, y in profile],
                       "height": height * scale, "elevation": elevation * scale},
        }, parent_id="building", basis_refs=(EVIDENCE,)))

    def box(identifier, x, y, dx, dy, height, elevation=0.0, role=None, label=None):
        prism(identifier, [(x, y), (x + dx, y), (x + dx, y + dy), (x, y + dy)], height, elevation, role, label)

    width = 4.5 if heldout else 4.0
    box("screen", 0, 2, width, .3, 3, role="role.weather_enclosure", label="south screen")
    box("twin-a", 1, 4, 1, 1, 1, 1, role="role.structural_support", label="square marker A")
    box("twin-b", 6 if heldout else 5, 4, 1, 1, 1, 1, role="role.structural_support", label="square marker B")
    prism("u-shell", [(8, 0), (12, 0), (12, 4), (11, 4), (11, 1), (9, 1), (9, 4), (8, 4)], 2,
          role="role.weather_enclosure", label="U shaped shell")
    box("insert", 9.25 if heldout else 9.5, 2, 1, 1, 1, label="freestanding insert")
    box("clash-a", 0, 8, 1, 1, 1, label="service block A")
    box("clash-b", .5 if heldout else .75, 8, 1, 1, 1, label="service block B")
    box("lintel", 0, 2, width, .3, .2, 3, role="role.structural_support", label="head member")
    box("seal", 0, 2, width, .3, .1, 3.2, role="role.weather_enclosure", label="upper gasket")
    box("fixing", 3.7, 2, .1, .3, .2, 3.3, label="fastener")
    box("drain", 4, 2, .2, .3, .2, label="weep outlet")
    box("mystery", 6, 8, 1, 1, 1, label="unclassified object",
        role="role.structural_support" if variant == "role-drift" else None)
    relations = tuple(Relation(f"{a}-to-{b}", "dependency", a, b, propagation="revalidate",
                               basis_refs=(EVIDENCE,))
                      for a, b in (("screen", "lintel"), ("lintel", "seal"), ("seal", "fixing"),
                                   ("fixing", "drain"), ("twin-b", "drain")))
    return StateRecord("spatial-observation-public", "authored", tuple(entities), relations=relations,
                       evidence_refs=(EVIDENCE,), decision_ref="decision:public-synthetic-fixture",
                       option={"option_id": "public-synthetic-fixture"})


def _drawing_recipe(receipt, view):
    # Import the actual application recipe, not a separately maintained camera.
    # The Studio API is a source workspace package, not installed in every venv.
    api = str(Path(__file__).resolve().parents[2] / "apps" / "archflow-studio" / "api")
    if api not in sys.path:
        sys.path.insert(0, api)
    from archflow_studio_api.application.drawings import _elevation_view
    recipe = _elevation_view(receipt, view, hidden_lines=False, scale_denominator=1)
    u0, v0, u1, v1 = recipe.crop_uv
    # Exactly model_view's meter recipe and 150 dpi <= 1024 pixel policy.
    scale = max(1, ceil(max(u1 - u0, v1 - v0) * 1000 * 150 / (25.4 * 1023)))
    return replace(recipe, scale_denominator=scale, linear_deflection=.0001)


@dataclass
class Fixture:
    repository: FilesystemProjectRepository
    record: StateRecord
    revision: str
    elevation_source: ElevationSource
    preprocessing_seconds: dict[str, float]

    @classmethod
    def create(cls, root: Path, *, variant="base", scale=1.0,
               authored: StateRecord | None = None, revision: str | None = None):
        """Create a disposable public fixture only in the caller's explicit root.

        Persistent records/binaries use P036. The CAD adapter receives its
        existing speculative workspace and independently reads its saved STEP.
        """
        start = perf_counter()
        root = Path(root).resolve()
        authored = authored if authored is not None else authored_record(variant=variant, scale=scale)
        repository = FilesystemProjectRepository.initialize(
            root, project_id=authored.project_id, initial_state={"phase": "request", "commitments": []},
            authored_record=authored.to_dict(),
        )
        run = repository.create_run("observation-source")
        record = authored.bound_to(run)
        destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id)
        record_ref = repository.put_json(run=run, destination=destination, record_kind=STATE_RECORD, payload=record.to_dict())
        levels, grids = project_levels_of(record), project_grids_of(record)
        context = ProductionContext(ReferenceContext(grids=grids, levels=levels), {}, frame_id="world")
        produced = produce_rows(element_rows_of(record), context)
        operations = tuple(sorted((op for item in produced for op in item.operations), key=lambda op: op.op_id))
        state = developed_design_view(record, run=run, phase=RECORD_BINDING_PHASE)
        proposal = GeometryProgramProposal(
            proposal_id="public-fixture", project_id=run.project_id, run_id=run.run_id, base=run.base,
            design_state_digest=state.state_digest, predecessor_program_digest=None,
            length_unit=LengthUnit.METER, tolerance=GeometryTolerance(.001, .001),
            frames=(CoordinateFrame("world", None, AffineTransform.identity(), (EVIDENCE,)),), assets=(),
            semantic_bindings=(SemanticBinding("binding-building", "building",
                tuple(sorted(oid for op in operations for oid in op.output_object_ids)), (COMMITMENT,), (record_ref.uri,)),),
            operations=operations, assemblies=(),
        )
        compiled = compile_geometry_program(state, proposal, active_commitment_refs=(COMMITMENT,),
            interface_datums=tuple(sorted((*levels.datums(), *context.published.values()), key=lambda d: d.datum_id)),
            datum_bindings=tuple(binding for item in produced for binding in item.bindings))
        if compiled.program is None:
            raise ValueError(f"synthetic fixture compile failed: {compiled.receipt.issues}")
        program = compiled.program
        branch = BranchRef(run, "experiment", 1)
        program_ref = repository.put_json(run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_BRANCH, run_id=run.run_id, branch_id=branch.branch_id),
            record_kind=stage_geometry_program("fixture"), payload=program.to_dict())
        binding = CadProgramBinding(program_ref, branch, "fixture", program.program_digest,
                                    program.proposal.design_state_digest, None)
        workspace = repository.layout.run(run.run_id).workspaces / "cad-fixture"
        workspace.mkdir(parents=True, exist_ok=True)
        compiled_at = perf_counter()
        receipt = execute_occt_export(program, binding=binding, speculative_workspace=workspace,
                                      artifact_stem="fixture", preview=False)
        if not receipt.readback_verified:
            raise ValueError(f"synthetic fixture exact export failed: {receipt.failures}")
        receipt_ref = repository.put_json(run=run, destination=destination,
                                         record_kind=SEAT_OCCT_EXECUTION, payload=receipt.to_dict())
        artifact = receipt.exact_artifact
        source = ElevationSource(run.run_id, (workspace / artifact["relative_path"]).relative_to(root).as_posix(),
                                artifact["sha256"], receipt_ref.relative_path, receipt_ref.sha256)
        exported_at = perf_counter()
        read_elevation_source(repository, source)
        return cls(repository, record, revision or f"{variant}-scale-{scale:g}", source,
                   {"author_compile_persist": compiled_at - start,
                    "cad_export_readback_persist": exported_at - compiled_at,
                    "retained_source_verify": perf_counter() - exported_at})

    @property
    def source(self):
        return {"revision": self.revision, "state_digest": self.record.state_digest,
                "step_sha256": self.elevation_source.step_sha256}

    def _verified(self):
        verified = read_elevation_source(self.repository, self.elevation_source)
        binding = verified.receipt["identity"]["binding"]
        if binding["design_state_digest"] != self.record.state_digest:
            raise SourceMismatch("retained STEP and authored state have different bindings")
        return verified

    def snapshot(self):
        """A derived plain value; no readback results or precomputed answers."""
        entities = [{"id": e.entity_id,
                     "metadata": {"label": e.fields["label"], "role": e.fields.get("role"),
                                  "producer": e.fields["producer"], "object_id": f"obj-{e.entity_id}"},
                     "params": deepcopy(e.fields["params"])} for e in self.record.entities_of("Element@1")]
        entities.extend({"id": e.entity_id,
                         "metadata": {"label": "ground datum", "role": e.fields["role"],
                                      "producer": None, "object_id": None},
                         "params": {"elevation": e.fields["elevation"]}}
                        for e in self.record.entities_of("Level@1"))
        relations = [{"id": f"relation:{r.relation_id}", "source": r.subject, "target": r.object,
                      "kind": r.kind, "propagation": r.propagation} for r in self.record.relations]
        dependencies = [{"id": edge.source_ref, "source": edge.upstream_ref.removeprefix("entity:"),
                         "target": edge.downstream_ref.removeprefix("entity:"), "kind": edge.relation,
                         "effect": edge.effect.value, "source_ref": edge.source_ref}
                        for edge in self.record.dependency_edges()]
        return {**self.source, "record_digest": self.record.digest,
                "units": "meter", "coordinate_system": "CAD Z-up; profile XY, height/elevation Z",
                "hub_conversion": "CAD(x,y,z) = Hub(x,z,y)", "entities": entities,
                "relations": relations, "dependencies": dependencies}

    def exact_query(self, action: str, args: dict[str, Any], *, source: dict[str, Any]):
        """Source-bound queries; their arguments select facts, never gold."""
        if source != self.source:
            raise SourceMismatch("stale or mismatched revision/state_digest/step_sha256")
        start = perf_counter()
        verified = self._verified()
        snapshot = self.snapshot()
        by_id = {e["id"]: e for e in snapshot["entities"]}

        def ids(values):
            result = tuple(v.removeprefix("entity:") for v in values)
            if any(v not in by_id for v in result):
                raise ValueError("unknown entity id")
            return result

        if action == "state":
            selected = ids(args.get("ids", by_id))
            result = {**snapshot, "entities": [by_id[v] for v in selected]}
        elif action == "dependencies":
            selected = ids(args["ids"])
            closure = self.record.closure(tuple(f"entity:{v}" for v in selected))
            result = {"changed": list(selected), "closure": [v.removeprefix("entity:") for v in closure],
                      "dependencies": snapshot["dependencies"], "scope": "declared dependencies only"}
        elif action == "pair":
            first, second = ids((args["first"], args["second"]))
            pair = (f"obj-{first}", f"obj-{second}")
            result = {"first": first, "second": second,
                      **measure_occt_solid_pairs(verified.entries, object_pairs=(pair,), length_unit="meter")[pair]}
        elif action in ("point", "shape"):
            identifier, = ids((args["id"],))
            if by_id[identifier]["metadata"]["object_id"] is None:
                raise ValueError("entity has no physical solid")
            entry, = (e for e in verified.entries if e.name == f"obj-{identifier}")
            if action == "point":
                point = args["point"]
                if len(point) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in point):
                    raise ValueError("point needs three finite CAD coordinates in meters")
                result = {"id": identifier, "point": point, "classification": classify_point(entry.shape, point)}
            else:
                result = {"id": identifier, **measure_shape(entry.shape).to_dict()}
        elif action == "visibility":
            selected = ids(args.get("ids", [identifier for identifier, row in by_id.items()
                                            if row["metadata"]["object_id"] is not None]))
            if any(by_id[identifier]["metadata"]["object_id"] is None for identifier in selected):
                raise ValueError("entity has no physical solid")
            view = args["view"]
            if view not in VIEWS:
                raise ValueError("view must be front/back/left/right/top")
            recipe = _drawing_recipe(verified.receipt, view)
            projected = project_model_axis_elevation(verified.entries, object_ids=verified.physical_object_ids,
                                                      view=recipe, unit="meter")
            result = {"view": view, "scope": "all scene objects participate; edge visibility, not pixel occlusion ratio",
                      "objects": [{"id": identifier,
                          "visible_edges": sum(line.object_id == f"obj-{identifier}" and line.kind == "visible" for line in projected.lines),
                          "hidden_edges": sum(line.object_id == f"obj-{identifier}" and line.kind == "hidden" for line in projected.lines)}
                          for identifier in selected]}
        else:
            raise ValueError("supported actions: state, dependencies, pair, point, shape, visibility")
        return {"source": self.source, "action": action, "result": result, "query_seconds": perf_counter() - start}

    def render_views(self, views=VIEWS):
        """Current B0: unlabelled, visible-only orthographic line PNGs, no RGB."""
        from PIL import Image
        verified = self._verified()
        results = {}
        for view in views:
            if view not in VIEWS:
                raise ValueError("view must be front/back/left/right/top")
            start = perf_counter()
            recipe = _drawing_recipe(verified.receipt, view)
            projected = project_model_axis_elevation(verified.entries, object_ids=verified.physical_object_ids,
                                                      view=recipe, unit="meter")
            with Image.open(BytesIO(projected.png)) as image:
                width, height = image.size
            results[view] = {"png": projected.png, "width": width, "height": height,
                             "recipe": recipe.to_dict(), "source": self.source,
                             "render_seconds": perf_counter() - start}
        return results
