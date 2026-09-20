"""Capability-specific checks and solver-neutral projection of existing fields.

No persistence, geometry edits, default material lookup, or Stage authority.
The shapes below are transient values consumed by this experiment only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
import math
from collections.abc import Mapping

from archflow.project.refs import RunRef
from archflow.semantics.roles import ROLE_IDS
from archflow.state.state_record import StateRecord, StateRecordError


@dataclass(frozen=True)
class Finding:
    status: str
    entity_id: str
    field: str
    detail: str


@dataclass(frozen=True)
class Readiness:
    findings: tuple[Finding, ...]

    @property
    def ready(self):
        return not self.findings


@dataclass(frozen=True)
class AnalysisSpec:
    source: dict
    nodes: tuple[dict, ...]
    members: tuple[dict, ...]
    supports: tuple[dict, ...]
    loads: tuple[dict, ...]
    policy: dict

    def to_dict(self):
        return asdict(self)


class AnalysisUnavailable(ValueError):
    def __init__(self, readiness):
        self.readiness = readiness
        super().__init__("; ".join(f"{f.entity_id}.{f.field}: {f.detail}" for f in readiness.findings))


_UNITS = {"pressure": {"Pa": 1, "MPa": 1e6, "GPa": 1e9},
          "length": {"m": 1, "mm": .001}, "area": {"m2": 1, "mm2": 1e-6},
          "inertia": {"m4": 1, "mm4": 1e-12}, "force": {"N": 1, "kN": 1000},
          "ratio": {"1": 1}, "density": {"kg/m3": 1}}
_STATUSES = {"hypothesis", "declared", "observed"}


def _number(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)
    except OverflowError:
        return False


def _project(record, member_ids, expected_run, expected_digest):
    findings = []

    def fail(status, entity, path, message):
        findings.append(Finding(status, entity, path, message))

    def sourced(value, entity, path):
        if not isinstance(value, Mapping) or not value:
            fail("unavailable", entity, path, "missing explicit sourced fact")
            return False
        if value.get("status") == "unknown":
            fail("unavailable", entity, path, "explicitly unknown")
            return False
        if (not isinstance(value.get("status"), str) or value["status"] not in _STATUSES
                or not isinstance(value.get("source"), str) or not value["source"].strip()):
            fail("invalid", entity, path, "requires source and hypothesis/declared/observed status")
            return False
        return True

    def quantity(value, dimension, entity, path, *, positive=False, vector=False):
        if not sourced(value, entity, path):
            return None
        unit = value.get("unit")
        if not isinstance(unit, str):
            fail("invalid", entity, path, "unit must be text")
            return None
        scale = _UNITS[dimension].get(unit)
        raw = value.get("value")
        values = raw if vector and isinstance(raw, (tuple, list)) else [raw]
        if scale is None:
            fail("unsupported", entity, path, f"unsupported {dimension} unit")
            return None
        if (vector and len(values) != 3) or any(not _number(v) or (positive and v <= 0) for v in values):
            fail("invalid", entity, path, "requires finite values" + (" > 0" if positive else ""))
            return None
        converted = [v * scale for v in values]
        if not all(math.isfinite(v) for v in converted):
            fail("invalid", entity, path, "unit conversion overflow")
            return None
        uncertainty = value.get("uncertainty")
        if uncertainty is not None:
            valid = (isinstance(uncertainty, Mapping) and uncertainty.get("unit") == value["unit"]
                     and _number(uncertainty.get("low")) and _number(uncertainty.get("high"))
                     and not vector and uncertainty["low"] <= raw <= uncertainty["high"])
            if not valid:
                fail("invalid", entity, path, "uncertainty must bound the value in the declared unit")
                return None
        return tuple(converted) if vector else converted[0]

    if record.base is None or record.run_ref != expected_run or record.digest != expected_digest:
        fail("invalid", "source", "revision", "exact project/run/base/content binding mismatch")
        return Readiness(tuple(findings)), None
    if not member_ids or len(set(member_ids)) != len(member_ids):
        fail("invalid", "selection", "members", "select nonempty unique canonical entities")
    members, nodes, supports, loads = [], {}, {}, []
    for entity_id in member_ids:
        try:
            entity = record.entity(entity_id)
        except StateRecordError:
            fail("invalid", entity_id, "entity", "not present in source revision")
            continue
        semantic = entity.fields.get("semantic")
        if sourced(semantic, entity_id, "semantic"):
            roles = semantic.get("roles", ())
            if not isinstance(roles, (list, tuple)) or any(not isinstance(r, str) or r not in ROLE_IDS for r in roles):
                fail("invalid", entity_id, "semantic.roles", "roles must use the existing registry")
            elif not set(roles).intersection({"role.structural_support", "role.load_transfer"}):
                fail("unavailable", entity_id, "semantic.roles", "no committed load-bearing role")
        material = entity.fields.get("material")
        if sourced(material, entity_id, "material") and not material.get("material_ref"):
            fail("unavailable", entity_id, "material.material_ref", "missing semantic material identity")
        structural = entity.fields.get("structural")
        if not sourced(structural, entity_id, "structural"):
            continue
        if structural.get("analysis_role") != "beam_column" or structural.get("idealization") != "euler_bernoulli_3d":
            fail("unsupported", entity_id, "structural.idealization", "only explicit elastic Euler-Bernoulli beam-column members supported")
        profile = structural.get("physical_profile")
        if not isinstance(profile, Mapping) or not profile.get("profile_ref"):
            fail("unavailable", entity_id, "physical_profile", "semantic material is insufficient: missing physical profile")
            profile = {}
        elif not isinstance(material, Mapping) or profile.get("material_ref") != material.get("material_ref"):
            fail("invalid", entity_id, "physical_profile.material_ref", "profile must name this semantic material")
        props = profile.get("properties", {})
        if not isinstance(props, Mapping):
            fail("invalid", entity_id, "physical_profile.properties", "requires property mapping")
            props = {}
        e = quantity(props.get("E"), "pressure", entity_id, "physical_profile.E", positive=True)
        nu = quantity(props.get("poisson"), "ratio", entity_id, "physical_profile.poisson")
        if nu is not None and not -1 < nu < .5:
            fail("invalid", entity_id, "physical_profile.poisson", "isotropic elastic poisson ratio must be between -1 and 0.5")
        if "density" in props:
            quantity(props["density"], "density", entity_id, "physical_profile.density", positive=True)
        section = structural.get("section", {})
        if not isinstance(section, Mapping):
            section = {}
        if not section.get("section_ref"):
            fail("unavailable", entity_id, "section", "missing explicit section identity")
        section_si = {key: quantity(section.get(key), dim, entity_id, f"section.{key}", positive=True)
                      for key, dim in (("A", "area"), ("Iy", "inertia"), ("Iz", "inertia"), ("J", "inertia"))}
        endpoints = structural.get("endpoints", {})
        node_ids, positions = [], []
        for end in ("i", "j"):
            point = endpoints.get(end, {}) if isinstance(endpoints, Mapping) else {}
            if not isinstance(point, Mapping):
                point = {}
            name = point.get("joint")
            position = quantity(point.get("position"), "length", entity_id, f"endpoints.{end}.position", vector=True)
            if not isinstance(name, str) or not name:
                fail("unavailable", entity_id, f"endpoints.{end}", "missing connectivity joint")
                continue
            if position is None:
                continue
            if name in nodes and nodes[name]["position_m"] != position:
                fail("invalid", entity_id, f"endpoints.{end}", "joint coordinates conflict; no implicit snapping")
            node = nodes.setdefault(name, {"id": name, "position_m": position, "source_entities": []})
            node["source_entities"].append(entity_id)
            node_ids.append(name)
            positions.append(position)
        if len(positions) == 2 and math.dist(*positions) == 0:
            fail("invalid", entity_id, "endpoints", "zero-length member")
        releases = structural.get("releases")
        if (not isinstance(releases, Mapping) or set(releases) != {"i_rz", "j_rz"}
                or any(type(v) is not bool for v in releases.values())):
            fail("unavailable", entity_id, "releases", "state both end moment-release assumptions explicitly")
        boundary = structural.get("supports")
        if not isinstance(boundary, Mapping):
            fail("unavailable", entity_id, "supports", "missing boundary declaration (empty allowed on internal members)")
            boundary = {}
        for end, dofs in boundary.items():
            if end not in ("i", "j") or not isinstance(dofs, (list, tuple)) or len(dofs) != 6 or any(type(v) is not bool for v in dofs):
                fail("invalid", entity_id, "supports", "support needs endpoint and six explicit boolean DOFs")
                continue
            point = endpoints.get(end, {}) if isinstance(endpoints, Mapping) else {}
            name = point.get("joint") if isinstance(point, Mapping) else None
            if name not in nodes:
                fail("invalid", entity_id, "supports", "support joint unavailable")
                continue
            if name in supports and supports[name]["restrained"] != list(dofs):
                fail("invalid", entity_id, "supports", "conflicting boundary declarations")
            supports[name] = {"node": name, "restrained": list(dofs), "source_entity": entity_id}
        member_loads = structural.get("loads")
        if not isinstance(member_loads, (list, tuple)):
            fail("unavailable", entity_id, "loads", "explicit load list missing (empty allowed)")
            member_loads = []
        for load in member_loads:
            if not isinstance(load, Mapping):
                fail("invalid", entity_id, "loads", "load must be a mapping")
                continue
            p = quantity(load.get("magnitude"), "force", entity_id, "loads.magnitude")
            at = quantity(load.get("at"), "length", entity_id, "loads.at")
            if load.get("direction") != "FY" or load.get("case") != "gravity":
                fail("unsupported", entity_id, "loads", "this adapter handles the explicit global-Y gravity point case only")
            if len(positions) == 2 and at is not None and not 0 <= at <= math.dist(*positions):
                fail("invalid", entity_id, "loads.at", "point load lies outside member")
            loads.append({"member": entity_id, "case": load.get("case"), "direction": load.get("direction"),
                          "force_N": p, "at_m": at, "provenance": deepcopy(load)})
        members.append({"source_entity": entity_id, "node_ids": tuple(node_ids), "E_Pa": e, "poisson": nu,
                        "section_si": section_si, "releases": deepcopy(releases),
                        "source_facts": deepcopy({"semantic": semantic, "material": material, "structural": structural})})
    if not any(any(s["restrained"]) for s in supports.values()):
        fail("unavailable", "selection", "supports", "no explicit restraints in selected subset")
    if not loads:
        fail("unavailable", "selection", "loads", "no explicit load in selected subset")
    try:
        policy = deepcopy(record.entity("analysis-policy").fields)
    except StateRecordError:
        policy = {}
    if sourced(policy, "analysis-policy", "policy"):
        if (policy.get("analysis") != "linear_static" or policy.get("self_weight") is not False
                or policy.get("load_case") != "gravity" or policy.get("mesh") != "one_member_per_entity"):
            fail("unsupported", "analysis-policy", "policy", "only explicit linear-static, no-self-weight, gravity, one-member policy supported")
        if not isinstance(policy.get("assumptions"), (list, tuple)) or not policy["assumptions"]:
            fail("unavailable", "analysis-policy", "assumptions", "state idealization assumptions")
    readiness = Readiness(tuple(findings))
    spec = None if not readiness.ready else AnalysisSpec(
        source={"run": expected_run.to_dict(), "record_digest": record.digest, "state_digest": record.state_digest,
                "entity_ids": list(member_ids)},
        nodes=tuple(nodes.values()), members=tuple(members), supports=tuple(supports.values()),
        loads=tuple(loads), policy=policy)
    return readiness, spec


def readiness(record: StateRecord, member_ids: tuple[str, ...], *, expected_run: RunRef, expected_digest: str):
    return _project(record, member_ids, expected_run, expected_digest)[0]


def compile_analysis(record: StateRecord, member_ids: tuple[str, ...], *, expected_run: RunRef, expected_digest: str):
    check, spec = _project(record, member_ids, expected_run, expected_digest)
    if not check.ready:
        raise AnalysisUnavailable(check)
    return spec
