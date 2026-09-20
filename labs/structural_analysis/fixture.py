"""Public synthetic two-bay gravity frame, not engineering specifications.

The four enrichment milestones are this experiment's commitment profiles, not
accepted DesignStage nodes or changes to the global Stage ontology.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from archflow.state.state_record import (
    Entity, Lineage, Parameter, StateRecord, apply_state_record_operator,
    compile_component_edit,
)
from monkeyarch.capabilities.element_producers import (
    ProductionContext, element_rows_of, produce_rows,
)
from monkeyarch.capabilities.reference_resolver import ReferenceContext

SOURCE = "fixture:gh-125-synthetic-assumptions"
MEMBERS = ("column-0", "column-1", "column-2", "beam-0", "beam-1")


def quantity(value, unit, **extra):
    return {"value": value, "unit": unit, "source": SOURCE,
            "status": "hypothesis", **extra}


def initial_record(project_id="structural-demo"):
    entities = [Entity("building", "Component@1", {"roles": ["role.whole"]}),
                Entity("assumptions", "Reading@1", {
                    "source": SOURCE,
                    "text": "Synthetic geometry and engineering assumptions for linear algebra verification only.",
                })]
    for index in range(3):
        x = 3 * index
        entities.append(Entity(f"column-{index}", "Element@1", {
            "producer": "prism", "params": {
                "profile": [[x-.15, -.15], [x+.15, -.15], [x+.15, .15], [x-.15, .15]],
                "height": "@height"}, "references": {"base": {"elevation": 0}},
            "semantic": {"status": "unknown"},
        }, parent_id="building", basis_refs=(SOURCE,), lineage=Lineage(introduced_at="geometry")))
    for index in range(2):
        x = 3 * index
        entities.append(Entity(f"beam-{index}", "Element@1", {
            "producer": "prism", "params": {
                "profile": [[x, -.1], [x+3, -.1], [x+3, .1], [x, .1]], "height": .4},
            "references": {"base": {"elevation": 2.8}}, "semantic": {"status": "unknown"},
        }, parent_id="building", basis_refs=(SOURCE,), lineage=Lineage(introduced_at="geometry")))
    entities.extend((Entity("roof", "Element@1", {
        "producer": "planar-surface", "params": {"profile": [[0, -.5], [6, -.5], [6, .5], [0, .5], [0, -.5]]},
        "references": {"base": {"elevation": 3.1}}, "semantic": {"status": "unknown"},
    }, parent_id="building", basis_refs=(SOURCE,), lineage=Lineage(introduced_at="geometry")),
        Entity("joint-overlap", "Reading@1", {
            "source": SOURCE, "status": "declared", "subjects": ["column-1", "beam-0"],
            "fact": "Untrimmed prism joint overlaps: x=[2.85,3], y=[2.8,3], z=[-0.1,0.1] metres.",
        })))
    return StateRecord(project_id, "authored", tuple(entities), parameters=(
        Parameter("height", 3, "m", epistemic_status="hypothesis", source_ref=SOURCE,
                  lock_authority="fixture-architect"),), evidence_refs=(SOURCE,), decision_ref="decision:synthetic-frame")


def enrich(record, milestone):
    """Compile the normal exact-base operator; no direct replacement state."""
    edits = []
    for member_id in MEMBERS:
        entity = record.entity(member_id)
        fields = deepcopy(entity.fields)
        column = member_id.startswith("column")
        if milestone == 2:
            fields["semantic"] = {
                "roles": ["role.structural_support" if column else "role.load_transfer"],
                "status": "hypothesis", "source": SOURCE,
                "interpretation": "column-like" if column else "beam-like",
            }
        elif milestone == 3:
            fields["material"] = {"material_ref": "demo-reinforced-concrete", "family": "concrete",
                                  "status": "hypothesis", "source": SOURCE}
        elif milestone == 4:
            index = int(member_id[-1])
            width, depth = (.3, .3) if column else (.2, .4)
            start = [3*index, 0 if column else 3, 0]
            end = [3*index if column else 3*(index+1), 3, 0]
            fields["structural"] = {
                "status": "hypothesis", "source": SOURCE,
                "analysis_role": "beam_column", "idealization": "euler_bernoulli_3d",
                "endpoints": {"i": {"joint": f"base-{index}" if column else f"top-{index}",
                                     "position": quantity(start, "m")},
                              "j": {"joint": f"top-{index}" if column else f"top-{index+1}",
                                     "position": quantity(end, "m")}},
                "physical_profile": {
                    "profile_ref": "demo-uncracked-concrete-v1", "material_ref": "demo-reinforced-concrete",
                    "properties": {"E": quantity(30, "GPa", conditions="Uncracked, linear, isotropic; no design strength claim.",
                                                    uncertainty={"low": 27, "high": 33, "unit": "GPa"}),
                                   "poisson": quantity(.2, "1"), "density": quantity(2400, "kg/m3")}},
                "section": {"section_ref": "column-300-square" if column else "beam-200x400",
                            "A": quantity(width*depth, "m2"),
                            "Iy": quantity(depth*width**3/12, "m4"),
                            "Iz": quantity(width*depth**3/12, "m4"),
                            "J": quantity(1e-4, "m4", conditions="Synthetic torsion constant; no torsion in this case.")},
                "releases": {"i_rz": not column, "j_rz": not column},
                "supports": ({"i": [True]*6, "j": [False, False, True, True, True, False]}
                             if column else {}),
                "loads": [] if column else [{"case": "gravity", "direction": "FY",
                                              "magnitude": quantity(-10, "kN"), "at": quantity(1.5, "m")}],
            }
        else:
            raise ValueError("enrichment milestone must be 2, 3 or 4")
        edits.append(replace(entity, fields=fields, lineage=replace(entity.lineage, revised_at=f"enrichment-{milestone}")))
    if milestone == 4:
        edits.append(Entity("analysis-policy", "Reading@1", {
            "source": SOURCE, "status": "hypothesis", "analysis": "linear_static",
            "self_weight": False, "load_case": "gravity", "mesh": "one_member_per_entity",
            "assumptions": ["Small displacement; uncracked isotropic elastic members.",
                            "Pinned beam ends; fixed column bases; restrained out-of-plane motion.",
                            "Roof gravity represented only by the two explicit point loads; self-weight excluded.",
                            "No reinforcement, cracking, creep, strength, buckling or code design checks."]}))
    return compile_component_edit(record, entities=tuple(edits))


def revise_facet(record, member_id, namespace, value, reason):
    """An explicit author revision, including revocation; solver never calls this."""
    entity = record.entity(member_id)
    fields = deepcopy(entity.fields)
    fields[namespace] = deepcopy(value)
    fields[namespace]["revision_reason"] = reason
    return compile_component_edit(record, entities=(replace(entity, fields=fields,
        lineage=replace(entity.lineage, revised_at=reason)),))


def geometry_projection(record):
    """Actual existing producer operations, keyed by canonical source entity."""
    rows = element_rows_of(record)
    produced = produce_rows(rows, ProductionContext(ReferenceContext(grids=None), {}))
    return {row.element_id: [op.to_dict() for op in result.operations]
            for row, result in zip(rows, produced, strict=True)}


def commitment_review(record, milestone):
    """Demo-local policy on one retained fact; separate from structural readiness.

    The overlap is an authored observation, not a CAD collision measurement.
    Policy changes severity only, and has no repair/accept/write authority.
    """
    if milestone not in (1, 2, 3, 4):
        raise ValueError("unknown demo commitment profile")
    return {
        "profile": milestone,
        "physical_facts": "not_required" if milestone < 4 else "required_for_selected_analysis_only",
        "roof_physical_facts": "not_required",
        "overlap": {"source_entity": "joint-overlap", "observation": dict(record.entity("joint-overlap").fields),
                    "status": ("informational", "unresolved", "warning", "blocker")[milestone-1]},
    }
