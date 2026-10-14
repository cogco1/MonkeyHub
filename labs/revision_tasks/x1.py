"""Does the state in the file decide a revision the way the full history does?

One reference decision function ``decide`` answers each task from one starting
context. It returns:

- a verdict: ``held``, ``violated``, ``refuse``, ``unknown`` or ``flag_rejected``;
- the elements it would rebuild;
- the obligations and relations it would re-check.

It uses only what that context holds. The four starting contexts are:

| context | what it holds |
|---|---|
| H | the history events (``history/events_v1.json``) and the record they agree with. The agreement is checked parameter by parameter, and every disagreement is reported |
| SF | a state file, C-full or C-task, read back from 3DM and from GLB through ``carrier`` with its binding checked |
| GF | the same model with every metadata string stripped; only the geometry and the request's own words remain |
| SF-O | SF without its obligations, and without the relations that bound, clear or fix (support contacts stay) |

Each task is answered twice. d_pre reads the state alone; d_post also realizes the candidate, reads it back and scans it. Both are reported for every context.

The verdict for an executable change follows these rules, in order:

1. **Unknown.** An unknown parameter, a missing value or a missing evidence topic.
2. **Refuse.** An open obligation fails, and the request itself moves the obligation's limit side.
3. **Violated.** An obligation fails otherwise; or the realized candidate breaks a declared relation; or the undeclared-interference scan finds a new penetration.
4. **Held.** Otherwise.

Two cases fall outside the change itself. A request matching a proposal the
context records as rejected is ``flag_rejected``. A request whose value equals
the current one is ``held`` with nothing rebuilt.

GF has no parametric state. It treats an executable request the way a terminal
model would: only the named elements change, nothing is checked, and the
verdict is ``held``.

The gold in ``x1_tasks.json`` was written from the physical definitions (gold
v2). ``decide`` never reads it.
"""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

import argparse
import csv
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from archflow.state.state_record import (
    StateRecord,
    StateRecordEditKind,
    StateRecordError,
    StateRecordOperator,
    apply_state_record_operator,
    evaluate_parameters,
)

from labs.revision_tasks import carrier, fixtures, pipeline, production_project, reviser

LAB = Path(__file__).resolve().parent
HISTORY = LAB / "history" / "events_v1.json"
TASKS = LAB / "x1_tasks.json"
FAMILY_FIXTURE = {"F1": fixtures.f1_portico, "F2": fixtures.f2_portico_rotunda, "F3": fixtures.f3_wall_stair}

# Each evidence topic, by its ids in the history and in the record.
TOPICS = {
    "F1": {"site_length": {"history": ("S1", "S2"), "record": ("evidence-site-s1", "evidence-site-s2")}},
    "F2": {"cornice_base": {"history": ("E-CORNICE-OLD", "E-CORNICE"), "record": ("evidence-cornice-old", "evidence-cornice")}},
    "F3": {"level_2": {"history": ("E-L2-OLD", "E-L2"), "record": ("evidence-l2-old", "evidence-l2")}},
}

# How a history target reads in the record: (parameter, value in record terms from the history's value).
HISTORY_PARAMETERS: dict[str, dict[str, tuple[str, Any]]] = {
    "F1": {"portico.stylobate_length": ("stylobate_length", lambda v, p: v),
           "portico.stylobate_depth": ("stylobate_depth", lambda v, p: v),
           "portico.column_count": ("column_count", lambda v, p: v),
           "portico.column_diameter": ("column_radius", lambda v, p: v / 2),
           "portico.end_column_axis_offset": ("column_span", lambda v, p: p["stylobate_length"] - 2 * v),
           "portico.column_height": ("column_height", lambda v, p: v),
           "portico.entablature_depth": ("entablature_height", lambda v, p: v)},
    "F2": {"rotunda.outer_radius": ("rotunda_radius", lambda v, p: v),
           "rotunda.cornice_base_level": ("cornice_base", lambda v, p: v)},
    "F3": {"level.L2": ("storey_height", lambda v, p: v),
           "door.width": ("door_width", lambda v, p: v),
           "door.height": ("door_height", lambda v, p: v),
           "door.centre_x": ("door_center", lambda v, p: v),
           "lintel.bearing": ("lintel_bearing", lambda v, p: v)},
}
# History declarations and where the record carries them (None: the record does not carry it).
HISTORY_DECLARATIONS = {
    "F1": {"P1": "p1-columns-on-stylobate", "P2": "p2-entablature-on-columns", "P3": None, "P4": None, "K1": "k1-central-clear",
           "P5": "p5-clear-spacing", "P5-OPEN": None},
    "F2": {"K2": "k2-dome-apex", "J0": "j0-transition-against-rotunda", "J1": "j1-roof-below-cornice", "J2": "j2-pediment-clear-of-drum"},
    "F3": {"D1": None, "D2": "lintel-bearing", "ST1": "stair-lands-on-l2", "ST2": "st2-riser-limit", "ST3": None,
           "K3": "k3-left-pier", "K4": "k4-wall-above-lintel"},
}
BOUNDING_CHECKS = frozenset({"clearance_interval", "lintel_minimum_bearing"})
# The element a rejected proposal in the history was about, by its history target.
REJECTION_TARGETS = {"F1": {"portico.column_count": "columns", "portico.capital_order": "columns"},
                     "F2": {"obj-transition-roof.material": "transition-roof"},
                     "F3": {"stair.type": "stair"}}


def _number(text: str) -> float:
    return float(re.findall(r"-?\d+(?:\.\d+)?", text)[-1])


@dataclass
class Context:
    name: str
    record: StateRecord | None
    rejections: set[tuple[str, Any]] = field(default_factory=set)
    evidence: dict[str, float | None] = field(default_factory=dict)
    state_bytes: int = 0
    notes: dict[str, Any] = field(default_factory=dict)
    decisions: list[dict[str, str]] = field(default_factory=list)


def history_context(family: str) -> Context:
    """H: the events, and the fixture record checked against every parameter and declaration they make."""

    events = json.loads(HISTORY.read_text(encoding="utf-8"))["families"][family]["events"]
    record = FAMILY_FIXTURE[family]()
    evaluated = evaluate_parameters(record)
    values = {p.key: float(evaluated[p.key]) for p in record.parameters}
    final: dict[str, Any] = {}
    for event in events:
        if event["type"] == "set_parameter":
            final[event["target"]] = event["value"]
    mismatches = []
    for target, value in final.items():
        mapped = HISTORY_PARAMETERS[family].get(target)
        if mapped is None:
            mismatches.append({"history": target, "record": None, "detail": "no record parameter"})
            continue
        key, convert = mapped
        expected = convert(value, final_values(final, family, values))
        if abs(float(values[key]) - float(expected)) > 1e-9:
            mismatches.append({"history": target, "record": key, "history_value": expected, "record_value": values[key]})
    obligation_ids = {o.obligation_id for o in record.obligations}
    relation_ids = {r.relation_id for r in record.relations}
    declared = [e["id"] for e in events if e["type"] in ("declare_relation", "commitment")]
    not_carried = [d for d in declared if HISTORY_DECLARATIONS[family].get(d) is None]
    dangling = [d for d in declared if HISTORY_DECLARATIONS[family].get(d) not in (None, *obligation_ids, *relation_ids)]
    rejections = {(e["change"]["target"], e["change"]["value"]) for e in events if e["type"] == "proposal" and e["status"] == "rejected"}
    superseded = {e["id"] for e in events if e["type"] == "evidence_supersede"}
    by_id = {e["id"]: e for e in events if e["type"] == "evidence"}
    evidence = {}
    for topic, ids in TOPICS[family].items():
        current = [by_id[i] for i in ids["history"] if i in by_id and i not in superseded]
        evidence[topic] = _number(current[-1]["claim"]) if current else None
    size = len(json.dumps(events, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return Context("H", record, rejections, evidence, size,
                   {"parameter_mismatches": mismatches, "declarations_not_carried_by_record": not_carried, "dangling": dangling})


def final_values(final: dict[str, Any], family: str, values: dict[str, float]) -> dict[str, float]:
    """The history's own parameter values in record terms, for conversions that read another parameter."""

    out = dict(values)
    for target, value in final.items():
        mapped = HISTORY_PARAMETERS[family].get(target)
        if mapped is not None and mapped[0] == "stylobate_length":
            out["stylobate_length"] = value
    return out


def _record_evidence(record: StateRecord, pack: dict[str, Any] | None, family: str) -> dict[str, float | None]:
    readings = {e.entity_id: dict(e.fields) for e in record.entities_of("Reading@1")}
    if pack is not None:
        for row in pack.get("context", {}).get("readings", []):
            readings.setdefault(row["entity_id"], row.get("fields", {}))
    evidence: dict[str, float | None] = {}
    for topic, ids in TOPICS[family].items():
        current = [readings[i] for i in ids["record"] if i in readings and readings[i].get("status") == "current"]
        evidence[topic] = float(current[-1]["value"]) if current else None
    return evidence


def _pack_rejections(pack: dict[str, Any] | None) -> list[dict[str, str]]:
    """Rejections the context pack carries in its scoped decisions (none unless the project holds decisions)."""

    return [{"target_ref": str(d.get("targetRef")), "text": str(d.get("rawLanguage", ""))}
            for d in (pack or {}).get("scopedDecisions", []) or []
            if str(d.get("disposition", "")).lower() in {"reject", "avoid"} and d.get("status", "active") == "active"]


def _rejected(context: "Context", task: dict[str, Any]) -> bool:
    """A history rejection of the same target and value, or a decision rejecting the request's element that names the value."""

    intent = task["intent"]
    if (intent["target"], intent["value"]) in context.rejections:
        return True
    element_refs = {f"entity:{name}" for name in task["named"]}
    return any(d["target_ref"] in element_refs and str(intent["value"]).lower() in d["text"].lower() for d in context.decisions)


def file_context(name: str, path: Path, fmt: str, family: str, *, sidecar: Path | None = None, minus_obligations: bool = False) -> Context:
    state = reviser.read_state_file(path, fmt=fmt, sidecar=sidecar)
    record = state.record
    if minus_obligations:
        record = replace(record, obligations=(), relations=tuple(r for r in record.relations
                                                                 if not (r.validator and r.validator.check_kind in BOUNDING_CHECKS)))
    size = len(json.dumps(carrier.payload(state.record, state.arm, state.context_pack), sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return Context(name, record, set(), _record_evidence(state.record, state.context_pack, family), size,
                   {"record_digest": state.record.digest, "arm": state.arm, "format": fmt}, _pack_rejections(state.context_pack))


def geometry_context(path: Path, fmt: str) -> Context:
    """GF: a file with every ``archflow:*`` string stripped. The reader refuses it; the decision falls back to geometry."""

    try:
        reviser.read_state_file(path, fmt=fmt)
        readable = True
    except carrier.CarrierRefused as exc:
        readable = exc.code
    return Context("GF", None, set(), {}, 0, {"state_reader": readable})


# ---------------------------------------------------------------- the reference decision

_REALIZED: dict[str, pipeline.Realization] = {}


def _realized(record: StateRecord, root: Path, label: str) -> pipeline.Realization:
    key = fixtures.bound(record).state_digest
    if key not in _REALIZED:
        work = root / "realized" / f"{label}-{key[:12]}"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        _REALIZED[key] = pipeline.realize(fixtures.bound(record), work.resolve(), "model")
    return _REALIZED[key]


def decide(context: Context, task: dict[str, Any], root: Path | None, *, execute: bool = True) -> dict[str, Any]:
    """``execute=False`` is d_pre: the state alone, no geometry. ``execute=True`` is d_post: also realize, read back and scan."""

    intent = task["intent"]
    if intent["kind"] == "attribute":
        if _rejected(context, task):
            return {"verdict": "flag_rejected", "affected": [], "recheck": [], "reason": "matches a rejected proposal"}
        return {"verdict": "unknown", "affected": [], "recheck": [], "reason": f"no parameter for {intent['target']}"}
    if intent["kind"] == "missing":
        return {"verdict": "unknown", "affected": [], "recheck": [], "reason": f"no value given for {intent['parameter']}"}
    if context.record is None:
        if intent["kind"] == "from_evidence":
            return {"verdict": "unknown", "affected": [], "recheck": [], "reason": "no evidence in a geometry-only file"}
        return {"verdict": "held", "affected": sorted(task["named"]), "recheck": [], "reason": "terminal geometry: only the named elements change"}
    if intent["kind"] == "from_evidence":
        value = context.evidence.get(intent["topic"])
        if value is None:
            return {"verdict": "unknown", "affected": [], "recheck": [], "reason": f"no current evidence for {intent['topic']}"}
        changes = {intent["parameter"]: round(intent["a"] * value + intent["b"], 9)}
    else:
        changes = dict(intent["set"])
    start = fixtures.bound(context.record)
    evaluated = evaluate_parameters(start)
    values = {p.key: float(evaluated[p.key]) for p in start.parameters}
    if any(key not in values for key in changes):
        return {"verdict": "unknown", "affected": [], "recheck": [], "reason": "a parameter the record does not declare", "changes": changes}
    if all(abs(float(values[k]) - float(v)) < 1e-9 for k, v in changes.items()):
        return {"verdict": "held", "affected": [], "recheck": [], "reason": "already so: nothing to change", "changes": changes}
    refs = tuple(f"parameter:{k}" for k in changes)
    closure = set(start.closure(refs))
    elements = {e.entity_id for e in start.entities_of("Element@1")}
    affected = sorted(ref.removeprefix("entity:") for ref in closure if ref.removeprefix("entity:") in elements)
    touched_params = {ref.removeprefix("parameter:") for ref in closure | set(refs) if ref.startswith("parameter:")}
    recheck = sorted({o.obligation_id for o in start.obligations if {s.removeprefix("parameter:") for s in o.subject_refs} & touched_params}
                     | {r.relation_id for r in start.relations if r.subject in affected or r.object in affected})
    record = start
    for key, value in changes.items():
        operator = StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
                                       target_ref=f"parameter:{key}", key=key, value=value)
        try:
            record = apply_state_record_operator(record, operator)
        except StateRecordError as exc:
            return {"verdict": "unknown", "affected": affected, "recheck": recheck, "reason": str(exc), "changes": changes}
    base = {"affected": affected, "recheck": recheck, "changes": changes}
    failed = [o for o in pipeline.obligation_checks(record) if o["status"] == "violated"]
    if failed:
        limit_side = any(o["right"] in changes or f"parameter:{o['right']}" in set(start.closure(refs)) for o in failed)
        return {"verdict": "refuse" if limit_side else "violated", **base,
                "reason": ("the request moves the limit of " if limit_side else "breaks ") + ", ".join(o["obligation_id"] for o in failed)}
    if not execute:
        return {"verdict": "held", **base, "reason": "no obligation fails; geometry not executed"}
    label = f"{task['task_id']}-{context.name}"
    before = _realized(context.record, root, f"start-{label}")
    after = _realized(record, root, label)
    declared = [c for c in pipeline.declared_checks(record, after) if c["status"] == "violated"]
    baseline = set(pipeline.penetrating_pairs(before))
    covered = pipeline._covered_pairs(record, after)
    new = sorted(p for p in pipeline.penetrating_pairs(after) if p not in baseline and frozenset(p) not in covered)
    if declared or new:
        return {"verdict": "violated", **base, "reason": "; ".join([c["relation_id"] for c in declared] + [" / ".join(p) for p in new])}
    return {"verdict": "held", **base, "reason": "every check holds"}


def _set_scores(found: list[str], gold: list[str]) -> tuple[float | None, float | None]:
    f, g = set(found), set(gold)
    precision = None if not f else len(f & g) / len(f)
    recall = None if not g else len(f & g) / len(g)
    return precision, recall


# ---------------------------------------------------------------- the run

def run(out: Path, *, families: tuple[str, ...] = ("F1", "F2", "F3")) -> dict[str, Any]:
    tasks = [t for t in json.loads(TASKS.read_text(encoding="utf-8"))["tasks"] if t["family"] in families]
    rows, consistency = [], {}
    for family in families:
        fam_dir = out / family
        fam_dir.mkdir(parents=True)
        h = history_context(family)
        consistency[family] = h.notes
        record = FAMILY_FIXTURE[family]()
        contexts = [h]
        files = {}
        # The C-task pack comes from a project that holds the record as production does: a stage run through
        # the runner, and the history's rejected proposals recorded as decisions against it.
        production = production_project.make_production_project(record, fam_dir / "production")
        events = json.loads(HISTORY.read_text(encoding="utf-8"))["families"][family]["events"]
        rejected = [e for e in events if e["type"] == "proposal" and e["status"] == "rejected"]
        decisions = [{"text": f"{e['change']['target']} = {e['change']['value']}: rejected by {e['authority']} ({e['reason']}).",
                      "target_ref": f"entity:{REJECTION_TARGETS[family][e['change']['target']]}"} for e in rejected]
        pack_read = production_project.record_rejections_and_read_context(
            production, decisions, utterance="Current design state.",
            element_ids=tuple(sorted(x.entity_id for x in record.entities_of("Element@1"))), cache_dir=fam_dir / "production" / "cache")
        consistency[family]["decisions_recorded"] = [d["status"] for d in pack_read["decisions"]]
        for arm in ("C-full", "C-task"):
            for fmt in carrier.FORMATS:
                arm_dir = fam_dir / f"{arm}-{fmt}"
                arm_dir.mkdir()
                written = reviser.write_state_file(record, arm=arm, fmt=fmt, out_dir=arm_dir, stem="state", parent_sha256=None,
                                                   context_root=arm_dir / "ctx", utterance="Current design state.",
                                                   focus=tuple(sorted(e.entity_id for e in record.entities_of("Element@1"))),
                                                   context_pack=pack_read["pack"] if arm == "C-task" else None)
                files[(arm, fmt)] = written.path
                contexts.append(file_context(f"SF[{arm},{fmt}]", written.path, fmt, family))
        contexts.append(file_context("SF-O[C-full,3dm]", files[("C-full", "3dm")], "3dm", family, minus_obligations=True))
        stripped = fam_dir / "geometry-only.3dm"
        from labs.revision_tasks.attacks import _rewrite, _strings
        _rewrite(files[("C-full", "3dm")], stripped, strings={k: None for k in _strings(files[("C-full", "3dm")]) if k.startswith("archflow:")})
        contexts.append(geometry_context(stripped, "3dm"))
        for task in (t for t in tasks if t["family"] == family):
            answers, pre = {}, {}
            for context in contexts:
                started = time.perf_counter()
                pre[context.name] = decide(context, task, fam_dir, execute=False)
                answer = decide(context, task, fam_dir)
                answers[context.name] = answer
                precision, recall = _set_scores(answer["affected"], task["gold"]["affected"])
                _, recheck_recall = _set_scores(answer["recheck"], task["gold"]["recheck"])
                rows.append({"task_id": task["task_id"], "type": task["type"], "context": context.name,
                             "verdict_pre": pre[context.name]["verdict"], "verdict_pre_ok": pre[context.name]["verdict"] in task["gold"]["verdicts"],
                             "verdict": answer["verdict"],
                             "gold_verdicts": "|".join(task["gold"]["verdicts"]), "verdict_ok": answer["verdict"] in task["gold"]["verdicts"],
                             "agrees_with_H": None, "affected": "|".join(answer["affected"]), "affected_precision": precision,
                             "affected_recall": recall, "recheck_recall": recheck_recall, "changes": json.dumps(answer.get("changes")),
                             "reason": answer["reason"], "state_bytes": context.state_bytes, "seconds": round(time.perf_counter() - started, 3)})
            for row in rows[-len(contexts):]:
                h_answer = answers["H"]
                mine = answers[row["context"]]
                row["agrees_with_H"] = (mine["verdict"], mine["affected"], mine.get("changes")) == (h_answer["verdict"], h_answer["affected"], h_answer.get("changes"))
            print(f"{task['task_id']:7} " + " ".join(f"{name}={pre[name]['verdict']}/{answers[name]['verdict']}" for name in answers), flush=True)
    with (out / "x1.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary: dict[str, Any] = {"consistency": consistency, "by_context": {}, "state_bytes": {}}
    for row in rows:
        summary["state_bytes"].setdefault(row["task_id"].split("-")[0], {})[row["context"]] = row["state_bytes"]
        cell = summary["by_context"].setdefault(row["context"], {"tasks": 0, "verdict_pre_ok": 0, "verdict_ok": 0, "agrees_with_H": 0})
        cell["tasks"] += 1
        cell["verdict_pre_ok"] += int(row["verdict_pre_ok"])
        cell["verdict_ok"] += int(row["verdict_ok"])
        cell["agrees_with_H"] += int(bool(row["agrees_with_H"]))
    (out / "x1_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the sufficiency tasks from every starting context into an empty directory.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--families", default="F1,F2,F3")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    summary = run(out, families=tuple(args.families.split(",")))
    print(json.dumps(summary["by_context"], indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
