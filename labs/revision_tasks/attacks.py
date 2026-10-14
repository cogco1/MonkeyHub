"""Adversarial transport of state-bearing files: eleven attacks, each judged against the honest revision.

For one family, arm and attack:

1. write the start state file;
2. apply the attack to what an outside party holds — the model file, never a D
   arm's sidecar, which stands for the authority kept outside the file;
3. run the probe revision on the attacked file;
4. compare with the same probe on the untouched file (the honest outcome).

| class | meaning |
|---|---|
| ``detected`` | the attacked file is refused (an integrity refusal, or a refusal where the honest run is not refused) |
| ``undetected_same_outcome`` | not refused, and the same claim and geometry as the honest run. Correct for D under A02 (it never needed the file's metadata); a silent discard of the outside edit for C-unbound under A01 |
| ``undetected_different_outcome`` | not refused, and a different answer or different geometry: the attack changed the result unnoticed |
| ``not_applicable`` | the attack edits state the file does not carry (D keeps its state outside) |

Attackers who recompute digests use the same public library a reader uses:
the format is open, and a digest a file computes over itself proves only that
the file is self-consistent.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)
from archflow.contracts.canonical import canonical_json
from archflow.state.state_record import StateRecord

from labs.revision_tasks import fixtures, oracle, reviser

INTEGRITY_CODES = frozenset({"METADATA_MISSING", "METADATA_CORRUPT", "STATE_DIGEST_MISMATCH", "CONTEXT_DIGEST_MISMATCH",
                             "DESIGN_STATE_REFERENCE_MISMATCH", "GEOMETRY_BINDING_STALE", "SCHEMA_UNSUPPORTED", "STATE_INVALID",
                             "STALE_BASE"})


@dataclass(frozen=True)
class Family:
    name: str
    record: Callable[[], StateRecord]
    benign: list[dict[str, Any]]        # honest outcome: accept
    guarded: list[dict[str, Any]]       # honest outcome: refuse or reopen, because of what the guard attacks remove
    guard: str                          # "obligation" (F1) or "relation" (F3)
    second: list[dict[str, Any]]        # another accepted change, on a parameter ``benign`` does not touch


FAMILIES = {
    "F1": Family("F1", fixtures.f1_portico,
                 [{"target": "parameter:column_count", "parameter": "column_count", "from": 6, "to": 4}],
                 [{"target": "parameter:column_count", "parameter": "column_count", "from": 6, "to": 3}], "obligation",
                 [{"target": "parameter:column_height", "parameter": "column_height", "from": 9.0, "to": 9.5}]),
    "F3": Family("F3", fixtures.f3_wall_stair,
                 [{"target": "parameter:door_width", "parameter": "door_width", "from": 1.2, "to": 1.6}],
                 [{"target": "parameter:lintel_bearing", "parameter": "lintel_bearing", "from": 0.15, "to": 0.05}], "relation",
                 [{"target": "parameter:door_height", "parameter": "door_height", "from": 2.1, "to": 2.2}]),
}


# ---------------------------------------------------------------- what an outside party can do to a 3DM

def _strings(path: Path) -> dict[str, str]:
    import rhino3dm

    model = rhino3dm.File3dm.Read(str(path))
    return {str(model.Strings[i][0]): str(model.Strings[i][1]) for i in range(len(model.Strings))}


def _rewrite(source: Path, target: Path, *, strings: dict[str, str | None] | None = None, move: str | None = None,
             rename: tuple[str, str] | None = None) -> Path:
    import rhino3dm

    model = rhino3dm.File3dm.Read(str(source))
    for key, value in (strings or {}).items():
        if value is None:
            model.Strings.Delete(key)
        else:
            model.Strings[key] = value
    for obj in list(model.Objects):
        name = obj.Attributes.Name
        if move is not None and name == move:
            geometry, attributes = obj.Geometry, obj.Attributes
            geometry.Translate(rhino3dm.Vector3d(0.5, 0.0, 0.0))
            model.Objects.Delete(attributes.Id)
            model.Objects.AddMesh(geometry, attributes)
        if rename is not None and name == rename[0]:
            obj.Attributes.Name = rename[1]
    if not model.Write(str(target), 8):
        raise RuntimeError(f"could not write {target.name}")
    return target


def _restate(source: Path, target: Path, edit: Callable[[dict[str, Any]], None], *, recompute: bool) -> Path | None:
    """Edit the embedded record; with ``recompute``, rewrite the binding's digests the way the reader computes them."""

    strings = _strings(source)
    if "archflow:state" not in strings:
        return None
    state = json.loads(strings["archflow:state"])
    binding = json.loads(strings["archflow:binding"])
    edit(state["record"])
    if recompute:
        try:
            record = StateRecord.from_dict(state["record"])
            binding["record_digest"] = record.digest
            binding["state_digest"] = fixtures.bound(record).state_digest
        except Exception:  # noqa: BLE001 - a record the library will not read cannot be given a digest
            recompute = False
    updates: dict[str, str | None] = {"archflow:state": canonical_json(state), "archflow:binding": canonical_json(binding)}
    if recompute:
        updates["archflow:design_state_digest"] = binding["state_digest"]
    return _rewrite(source, target, strings=updates)


def _relax(family: Family) -> Callable[[dict[str, Any]], None]:
    def edit(record: dict[str, Any]) -> None:
        if family.guard == "obligation":
            for parameter in record["parameters"]:
                if parameter["key"] == "max_clear_spacing":
                    parameter["value"] = 5.0
        else:
            for relation in record["relations"]:
                if relation.get("validator", {}) and relation["validator"].get("check_kind") == "lintel_minimum_bearing":
                    relation["parameters"]["minimum_bearing_m"] = 0.0
    return edit


def _delete_guard(family: Family) -> Callable[[dict[str, Any]], None]:
    def edit(record: dict[str, Any]) -> None:
        if family.guard == "obligation":
            record["obligations"] = []
        else:
            record["relations"] = [r for r in record["relations"]
                                   if (r.get("validator") or {}).get("check_kind") != "lintel_minimum_bearing"]
    return edit


def _self_cycle(record: dict[str, Any]) -> None:
    for parameter in record["parameters"]:
        if parameter.get("expr"):
            parameter["expr"] = f"{parameter['key']} + 0"
            parameter["inputs"] = [parameter["key"]]
            return


@dataclass(frozen=True)
class Attack:
    name: str
    probe: str        # "benign" or "guarded"
    apply: Callable[[Path, Path, Family], Path | None]


def _schema(source: Path, target: Path, family: Family) -> Path | None:
    strings = _strings(source)
    if "archflow:state" not in strings:
        return None
    state = json.loads(strings["archflow:state"])
    state["schema"] = "ArchFlowStateFile@2"
    return _rewrite(source, target, strings={"archflow:state": canonical_json(state)})


def _forge(source: Path, target: Path, family: Family) -> Path | None:
    strings = _strings(source)
    if "archflow:binding" not in strings:
        return None
    binding = json.loads(strings["archflow:binding"])
    binding["state_digest"] = "f" * 64
    return _rewrite(source, target, strings={"archflow:binding": canonical_json(binding)})


ATTACKS: dict[str, Attack] = {a.name: a for a in (
    Attack("A01_foreign_move_keep_metadata", "benign", lambda s, t, f: _rewrite(s, t, move=_unrelated(f))),
    Attack("A02_strip_all_metadata", "benign", lambda s, t, f: _rewrite(s, t, strings={k: None for k in _strings(s) if k.startswith("archflow:")})),
    Attack("A03_strip_guards_naive", "guarded", lambda s, t, f: _restate(s, t, _delete_guard(f), recompute=False)),
    Attack("A04_relax_guard_recomputed", "guarded", lambda s, t, f: _restate(s, t, _relax(f), recompute=True)),
    Attack("A05_delete_guard_recomputed", "guarded", lambda s, t, f: _restate(s, t, _delete_guard(f), recompute=True)),
    Attack("A06_forged_state_digest", "benign", _forge),
    Attack("A07_concurrent_stale_writer", "benign", lambda s, t, f: None),      # driven in run_case
    Attack("A08_replay_older_file", "benign", lambda s, t, f: None),            # driven in run_case
    Attack("A09_schema_upgrade", "benign", _schema),
    Attack("A10_dependency_cycle_recomputed", "benign", lambda s, t, f: _restate(s, t, _self_cycle, recompute=True)),
    Attack("A11_duplicate_object_name", "benign", lambda s, t, f: _rewrite(s, t, rename=(_unrelated(f), _other(f)))),
)}


def _unrelated(family: Family) -> str:
    return "obj-stylobate" if family.name == "F1" else "obj-stair"


def _other(family: Family) -> str:
    return "obj-pediment" if family.name == "F1" else "obj-slab-l2"


# ---------------------------------------------------------------- one case

def _outcome(revision: reviser.Revision) -> dict[str, Any]:
    shapes = None
    if revision.written is not None:
        shapes = {n: oracle.signature(m) for n, m in oracle.load_meshes(revision.written.path).items()}
    return {"claim": revision.claim, "codes": [r["code"] for r in revision.reasons], "shapes": shapes}


def _same_shapes(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if a is None or b is None:
        return a is b
    return set(a) == set(b) and all(oracle.same_shape(a[n], b[n]) for n in a)


def classify(honest: dict[str, Any], attacked: dict[str, Any] | None) -> str:
    if attacked is None:
        return "not_applicable"
    if attacked["claim"] == "refuse" and (honest["claim"] != "refuse" or set(attacked["codes"]) & INTEGRITY_CODES):
        return "detected"
    if attacked["claim"] == honest["claim"] and attacked["codes"] == honest["codes"] and _same_shapes(attacked["shapes"], honest["shapes"]):
        return "undetected_same_outcome"
    return "undetected_different_outcome"


def run_case(family: Family, arm: str, attack: Attack, root: Path, *, fmt: str = "3dm") -> dict[str, Any]:
    """Honest and attacked probe revisions of one fresh start file; returns the comparison row."""

    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    probe = family.benign if attack.probe == "benign" else family.guarded
    utterance = "Probe revision."
    start = reviser.write_state_file(family.record(), arm=arm, fmt=fmt, out_dir=root, stem="s0", parent_sha256=None,
                                     context_root=root / "ctx", utterance="Start state.")
    s0_sha = reviser.read_state_file(start.path, fmt=fmt, sidecar=start.sidecar).file_sha256

    def revise(path: Path, stem: str, sidecar: Path | None, expected: str | None = None,
               deltas: list[dict[str, Any]] | None = None) -> reviser.Revision:
        return reviser.revise(path, fmt=fmt, arm=arm, deltas=deltas or probe, out_dir=root, stem=stem, context_root=root / "ctx",
                              utterance=utterance, sidecar=sidecar, expected_file_sha256=expected, scan=False)

    honest = _outcome(revise(start.path, "honest", start.sidecar, s0_sha))
    attacked: dict[str, Any] | None
    if attack.name == "A07_concurrent_stale_writer":
        # Writer A commits a benign change first; writer B's change was made against s0.
        first = reviser.revise(start.path, fmt=fmt, arm=arm, deltas=family.benign, out_dir=root, stem="writer-a", context_root=root / "ctx",
                               utterance=utterance, sidecar=start.sidecar, expected_file_sha256=s0_sha, scan=False)
        attacked = _outcome(revise(first.written.path, "writer-b", first.written.sidecar, s0_sha, family.second))
        honest = _outcome(revise(first.written.path, "writer-b-honest", first.written.sidecar,
                                 reviser.read_state_file(first.written.path, fmt=fmt, sidecar=first.written.sidecar).file_sha256,
                                 family.second))
    elif attack.name == "A08_replay_older_file":
        # s1 is current; the older s0 is presented in its place (with the authority's current sidecar for D).
        current = reviser.revise(start.path, fmt=fmt, arm=arm, deltas=family.benign, out_dir=root, stem="current", context_root=root / "ctx",
                                 utterance=utterance, sidecar=start.sidecar, expected_file_sha256=s0_sha, scan=False)
        replayed = root / f"replayed.{fmt}"
        replayed.write_bytes(start.path.read_bytes())
        attacked = _outcome(revise(replayed, "replayed", current.written.sidecar, deltas=family.second))
        honest = _outcome(revise(current.written.path, "current-honest", current.written.sidecar, deltas=family.second))
    else:
        target = attack.apply(start.path, root / f"attacked.{fmt}", family)
        attacked = None if target is None else _outcome(revise(target, "attacked", start.sidecar))
    return {"family": family.name, "arm": arm, "attack": attack.name, "probe": attack.probe,
            "honest_claim": honest["claim"], "honest_codes": honest["codes"],
            "attacked_claim": None if attacked is None else attacked["claim"],
            "attacked_codes": None if attacked is None else attacked["codes"],
            "class": classify(honest, attacked)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run every attack on every arm and family into an empty directory.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--families", default="F1,F3")
    parser.add_argument("--arms", default=",".join(reviser.carrier.ARMS))
    parser.add_argument("--attacks", default=",".join(ATTACKS))
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    rows = []
    for family in args.families.split(","):
        for arm in args.arms.split(","):
            for name in args.attacks.split(","):
                try:
                    row = run_case(FAMILIES[family], arm, ATTACKS[name], out / "cases" / f"{family}-{arm}-{name}")
                except Exception as exc:  # noqa: BLE001 - a crash is an outcome class, recorded and counted
                    row = {"family": family, "arm": arm, "attack": name, "probe": ATTACKS[name].probe, "honest_claim": None,
                           "honest_codes": None, "attacked_claim": None, "attacked_codes": [f"{type(exc).__name__}: {str(exc)[:200]}"],
                           "class": "crash"}
                print(f"{family} {arm:9} {name:34} honest={str(row['honest_claim']):6} attacked={str(row['attacked_claim']):6} {row['class']}", flush=True)
                rows.append(row)
    with (out / "x6.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: json.dumps(v) if isinstance(v, list) else v for k, v in row.items()} for row in rows)
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        counts = summary.setdefault(row["arm"], {"detected": 0, "undetected_same_outcome": 0, "undetected_different_outcome": 0,
                                                 "not_applicable": 0, "crash": 0})
        counts[row["class"]] += 1
    (out / "x6_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
