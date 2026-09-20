"""Thin adapter to PyNite; no FE stiffness or general solver implemented here."""
from __future__ import annotations

from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
import math

from labs.structural_analysis.analysis import AnalysisSpec

SOLVER_VERSION = "3.2.0"


def solve(spec: AnalysisSpec):
    """Return structured evidence; missing backend and real failures stay explicit."""
    result = {"source": deepcopy(spec.source), "policy": deepcopy(spec.policy),
              "solver": {"name": "PyNiteFEA", "required_version": SOLVER_VERSION},
              "member_provenance": {m["source_entity"]: deepcopy(m["source_facts"]) for m in spec.members}}
    try:
        actual = version("PyNiteFEA")
    except PackageNotFoundError:
        return {**result, "status": "unavailable", "reason": "Install the lab's pinned optional requirements in an isolated environment."}
    result["solver"]["version"] = actual
    if actual != SOLVER_VERSION:
        return {**result, "status": "unsupported", "reason": f"Unverified solver version {actual}; expected {SOLVER_VERSION}."}
    result["solver"]["numeric_dependencies"] = {package: version(package) for package in ("numpy", "scipy")}
    if (spec.policy.get("analysis") != "linear_static" or spec.policy.get("self_weight") is not False
            or spec.policy.get("load_case") != "gravity" or spec.policy.get("mesh") != "one_member_per_entity"):
        return {**result, "status": "unsupported", "reason": "Unsupported analysis policy; compile the explicit supported projection."}
    try:
        from Pynite import FEModel3D
        model = FEModel3D()
        for node in spec.nodes:
            model.add_node(node["id"], *node["position_m"])
        for member in spec.members:
            name = member["source_entity"]
            e, nu = member["E_Pa"], member["poisson"]
            # rho is weight density in PyNite, unused since self weight is explicitly
            # disabled. Zero here does NOT mean the canonical material has no mass.
            model.add_material(name, E=e, G=e/(2*(1+nu)), nu=nu, rho=0)
            model.add_section(name, **member["section_si"])
            model.add_member(name, *member["node_ids"], name, name)
            model.def_releases(name, Rzi=member["releases"]["i_rz"], Rzj=member["releases"]["j_rz"])
        for support in spec.supports:
            model.def_support(support["node"], *support["restrained"])
        for load in spec.loads:
            model.add_member_pt_load(load["member"], load["direction"], load["force_N"], load["at_m"], case=load["case"])
        case = spec.policy["load_case"]
        model.add_load_combo(case, {case: 1.0})
        model.analyze_linear(check_stability=True, check_statics=False)
        nodes = {name: {"displacement_m": [float(n.DX[case]), float(n.DY[case]), float(n.DZ[case])],
                        "rotation_rad": [float(n.RX[case]), float(n.RY[case]), float(n.RZ[case])],
                        "reaction_N": [float(n.RxnFX[case]), float(n.RxnFY[case]), float(n.RxnFZ[case])],
                        "reaction_moment_Nm": [float(n.RxnMX[case]), float(n.RxnMY[case]), float(n.RxnMZ[case])]}
                 for name, n in model.nodes.items()}
        members = {}
        for name, member in model.members.items():
            length = member.L()
            members[name] = {
                "length_m": length, "midpoint_local_dy_m": float(member.deflection("dy", length/2, case)),
                "midpoint_relative_dy_m": float(member.rel_deflection("dy", length/2, case)),
                "axial_mid_N": float(member.axial(length/2, case)),
                "shear_local_y_N": [float(member.shear("Fy", x, case)) for x in (0, length)],
                "moment_local_z_Nm": [float(member.moment("Mz", x, case)) for x in (0, length/2, length)],
            }
        numbers = [v for node in nodes.values() for vector in node.values() for v in vector]
        numbers += [v for m in members.values() for value in m.values()
                    for v in (value if isinstance(value, list) else [value])]
        if not all(math.isfinite(v) for v in numbers):
            raise ValueError("solver returned nonfinite physical output")
        return {**result, "status": "solved", "nodes": nodes, "members": members}
    except Exception as exc:
        # Retain genuine backend errors; no replacement analytic/fake solver result.
        return {**result, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}


def hand_solution(spec: AnalysisSpec):
    """Closed-form check ONLY for this symmetric two-span pin-ended fixture.

    Equilibrium gives P/2 at each beam end; column shortening is NL/EA;
    integrating EI*v''=M with pin ends gives PL^3/(48EI) at midspan.
    This is not a fallback solver. Reject a different topology/loading/policy.
    """
    by_id = {m["source_entity"]: m for m in spec.members}
    required = {"column-0", "column-1", "column-2", "beam-0", "beam-1"}
    expected_nodes = {f"{level}-{i}": (3.*i, y, 0.) for i in range(3) for level, y in (("base", 0.), ("top", 3.))}
    if set(by_id) != required or {n["id"]: tuple(n["position_m"]) for n in spec.nodes} != expected_nodes:
        raise ValueError("hand solution only supports the declared two-bay fixture")
    if spec.policy["self_weight"] is not False or spec.policy["analysis"] != "linear_static":
        raise ValueError("hand solution requires the explicit linear, no-self-weight policy")
    expected_supports = {f"base-{i}": [True]*6 for i in range(3)}
    expected_supports.update({f"top-{i}": [False, False, True, True, True, False] for i in range(3)})
    if {s["node"]: s["restrained"] for s in spec.supports} != expected_supports:
        raise ValueError("hand solution boundary conditions differ")
    for i in range(3):
        if (tuple(by_id[f"column-{i}"]["node_ids"]) != (f"base-{i}", f"top-{i}")
                or by_id[f"column-{i}"]["releases"] != {"i_rz": False, "j_rz": False}):
            raise ValueError("hand solution column connectivity differs")
    load_by_member = {load["member"]: load for load in spec.loads}
    if len(spec.loads) != 2 or set(load_by_member) != {"beam-0", "beam-1"}:
        raise ValueError("hand solution requires one point load per beam")
    p = []
    for i in range(2):
        m, load = by_id[f"beam-{i}"], load_by_member[f"beam-{i}"]
        if (tuple(m["node_ids"]) != (f"top-{i}", f"top-{i+1}") or m["releases"] != {"i_rz": True, "j_rz": True}
                or load["direction"] != "FY" or load["at_m"] != 1.5 or load["case"] != "gravity"):
            raise ValueError("hand solution needs pin-ended beams with midpoint global-Y loads")
        p.append(-load["force_N"])
    reactions = [p[0]/2, sum(p)/2, p[1]/2]
    top_dy = [-n*3/(by_id[f"column-{i}"]["E_Pa"]*by_id[f"column-{i}"]["section_si"]["A"])
              for i, n in enumerate(reactions)]
    beam_relative = [-p[i]*3**3/(48*by_id[f"beam-{i}"]["E_Pa"]*by_id[f"beam-{i}"]["section_si"]["Iz"])
                     for i in range(2)]
    return {"base_reaction_y_N": reactions, "top_dy_m": top_dy,
            "beam_mid_relative_dy_m": beam_relative,
            "beam_mid_dy_m": [beam_relative[i]+(top_dy[i]+top_dy[i+1])/2 for i in range(2)],
            "beam_mid_moment_z_Nm": [-p[i]*3/4 for i in range(2)]}
