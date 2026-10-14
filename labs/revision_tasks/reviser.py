"""The hostless reviser: read a state-bearing file, change it, and write the next one. No CAD application runs.

    read (carrier, binding checked) -> exact base from the file's binding -> apply_state_record_operator
    -> produce -> compile_geometry_program -> execute_occt_export -> write the next state file

The cold judgement of the new file is not made here; the oracle makes it, in
its own process. What the system checks itself (declared relations on the
cold-read STEP, and two harness additions — the at-most obligation and the
undeclared-interference scan) is reported with ``harness_added`` naming the
additions, as in ``pipeline.run_archflow_arm``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from archflow.contracts.canonical import canonical_json
from archflow.state.state_record import (
    StateRecord,
    StateRecordEditKind,
    StateRecordError,
    StateRecordOperator,
    apply_state_record_operator,
)

from labs.revision_tasks import carrier, fixtures, pipeline
from labs.revision_tasks.context_pack import read_context_pack

HARNESS_ADDED = ("binding_seal", "obligation_at_most", "undeclared_interference_scan", "context_pack_retention",
                 "state_from_external_file")


@dataclass
class Written:
    path: Path
    sidecar: Path | None
    record: StateRecord
    binding: dict[str, Any]
    realization: pipeline.Realization
    context_pack: dict[str, Any] | None
    seconds: dict[str, float]


@dataclass
class Revision:
    claim: str
    reasons: list[dict[str, Any]]
    written: Written | None = None
    read: carrier.StateFile | None = None
    operators: list[dict[str, Any]] = field(default_factory=list)
    closure: list[str] = field(default_factory=list)
    declared: list[dict[str, Any]] = field(default_factory=list)
    obligations: list[dict[str, Any]] = field(default_factory=list)
    new_interference: list[dict[str, Any]] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)
    harness_added: tuple[str, ...] = HARNESS_ADDED


def focus_elements(record: StateRecord, changed: tuple[str, ...]) -> tuple[str, ...]:
    """The Element@1 ids the system's own closure reaches: the agent's focus for a context read."""

    elements = {e.entity_id for e in record.entities_of("Element@1")}
    return tuple(sorted(ref.removeprefix("entity:") for ref in record.closure(changed) if ref.removeprefix("entity:") in elements))


def write_state_file(record: StateRecord, *, arm: str, fmt: str, out_dir: Path, stem: str, parent_sha256: str | None,
                     context_root: Path, utterance: str, focus: tuple[str, ...] = (),
                     prior: pipeline.Realization | None = None, context_pack: dict[str, Any] | None = None) -> Written:
    """Realize ``record`` and write it as ``<stem>.<fmt>`` (plus ``<stem>.sidecar.json`` for D) into ``out_dir``.

    A C-task file carries the context pack read from a disposable project holding the record, or ``context_pack``
    when the caller read one from a project that holds more (a stage run and its decisions).
    """

    if arm not in carrier.ARMS or fmt not in carrier.FORMATS:
        raise ValueError(f"unknown arm/format {arm!r}/{fmt!r}")
    t0 = time.perf_counter()
    carried = carrier.task_view(record) if arm == "C-task" else record
    pack = None
    if arm == "C-task":
        pack = context_pack if context_pack is not None else read_context_pack(carried, root=context_root, utterance=utterance, element_ids=focus)
    t1 = time.perf_counter()
    bound = fixtures.bound(carried)
    state = carrier.payload(carried, arm, pack)
    work = out_dir / f"{stem}.work"
    work.mkdir(parents=True, exist_ok=False)
    provenance = None if arm == "D" else {"state": canonical_json(state)}
    realization = pipeline.realize(bound, work.resolve(), "model", prior=prior, provenance=provenance)
    t2 = time.perf_counter()
    sidecar = out_dir / f"{stem}.sidecar.json" if arm == "D" else None
    if fmt == "3dm":
        geometry, _ = carrier.three_dm_geometry(realization.preview_path)
        bind = carrier.binding(carried, bound.state_digest, arm, pack, parent_sha256, geometry)
        final = out_dir / f"{stem}.3dm"
        carrier.seal_three_dm(realization.preview_path, final, None if arm == "D" else bind)
    else:
        document, tail = carrier.glb_from_three_dm(realization.preview_path)
        bind = carrier.binding(carried, bound.state_digest, arm, pack, parent_sha256, carrier.glb_geometry(document, tail))
        final = out_dir / f"{stem}.glb"
        carrier.write_glb(document, tail, final, state=None if arm == "D" else state, binding_value=None if arm == "D" else bind,
                          design_state_digest=bound.state_digest)
    if sidecar is not None:
        sidecar.write_text(json.dumps({"state": state, "binding": bind}, sort_keys=True, indent=1), encoding="utf-8")
    t3 = time.perf_counter()
    return Written(final, sidecar, carried, bind, realization, pack,
                   {"context_pack_s": t1 - t0, "realize_s": t2 - t1, "write_s": t3 - t2})


def read_state_file(path: Path, *, fmt: str, sidecar: Path | None = None) -> carrier.StateFile:
    return carrier.read_three_dm(path, sidecar=sidecar) if fmt == "3dm" else carrier.read_glb(path, sidecar=sidecar)


def revise(path: Path, *, fmt: str, arm: str, deltas: list[dict[str, Any]], out_dir: Path, stem: str, context_root: Path,
           utterance: str, sidecar: Path | None = None, expected_file_sha256: str | None = None, scan: bool = True) -> Revision:
    """One hostless revision of a state file; refusals come back as a claim, never as a partial file."""

    t0 = time.perf_counter()
    try:
        current = read_state_file(path, fmt=fmt, sidecar=sidecar)
    except carrier.CarrierRefused as exc:
        return Revision("refuse", [{"code": exc.code, "detail": exc.detail}], seconds={"read_s": time.perf_counter() - t0})
    t1 = time.perf_counter()
    if expected_file_sha256 is not None and current.file_sha256 != expected_file_sha256:
        return Revision("refuse", [{"code": "STALE_BASE", "detail": "the file is not the one this change was made against"}], read=current)
    changed = tuple(d["target"] for d in deltas)
    closure = list(current.record.closure(changed))
    missing = [d["target"] for d in deltas if d["to"] is None]
    if missing:
        return Revision("refuse", [{"code": "MISSING_INFORMATION", "refs": missing}], read=current, closure=closure)
    # The file carries the authored record; binding it to its run changes no content digest.
    record, operators = fixtures.bound(current.record), []
    base_record, base_state = current.binding["record_digest"], current.binding["state_digest"]
    for delta in deltas:
        operator = StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR, base_record_digest=base_record, base_state_digest=base_state,
                                       target_ref=delta["target"], key=delta["parameter"], value=delta["to"])
        try:
            record = apply_state_record_operator(record, operator)
        except StateRecordError as exc:
            return Revision("refuse", [{"code": "STALE_BASE" if "stale" in str(exc) else "OPERATOR_REFUSED", "detail": str(exc)}],
                            read=current, operators=operators, closure=closure)
        operators.append(operator.to_dict())
        base_record, base_state = record.digest, record.state_digest
    obligations = pipeline.obligation_checks(record)
    broken = [o["obligation_id"] for o in obligations if o["status"] == "violated"]
    if broken:
        return Revision("refuse", [{"code": "OBLIGATION_UNSATISFIABLE", "refs": broken}], read=current, operators=operators,
                        closure=closure, obligations=obligations)
    t2 = time.perf_counter()
    start_realization = None
    if scan:
        start_dir = out_dir / f"{stem}.start"
        start_dir.mkdir(parents=True, exist_ok=False)
        start_realization = pipeline.realize(fixtures.bound(current.record), start_dir.resolve(), "start")
    t3 = time.perf_counter()
    try:
        written = write_state_file(record, arm=arm, fmt=fmt, out_dir=out_dir, stem=stem, parent_sha256=current.file_sha256,
                                   context_root=context_root, utterance=utterance, focus=focus_elements(record, changed),
                                   prior=start_realization)
    except pipeline.RealizationError as exc:
        return Revision("refuse", [{"code": exc.code, "detail": exc.detail}], read=current, operators=operators, closure=closure,
                        obligations=obligations)
    t4 = time.perf_counter()
    declared = pipeline.declared_checks(written.record, written.realization)
    new: list[dict[str, Any]] = []
    if start_realization is not None:
        baseline = set(pipeline.penetrating_pairs(start_realization))
        covered = pipeline._covered_pairs(written.record, written.realization)
        new = [{"pair": list(pair), "common_volume_m3": volume} for pair, volume in sorted(pipeline.penetrating_pairs(written.realization).items())
               if pair not in baseline and frozenset(pair) not in covered]
    t5 = time.perf_counter()
    violated = [c["relation_id"] for c in declared if c["status"] == "violated"]
    reasons: list[dict[str, Any]] = [{"code": "DECLARED_RELATION_VIOLATED", "refs": violated}] if violated else []
    if new:
        reasons.append({"code": "UNDECLARED_INTERFERENCE", "refs": [" / ".join(n["pair"]) for n in new]})
    unchecked = [c["relation_id"] for c in declared if c["status"] == "unchecked"]
    if unchecked:
        reasons.append({"code": "DECLARED_RELATION_UNCHECKED", "refs": unchecked})
    claim = "reopen" if violated or new else "unknown" if unchecked else "accept"
    return Revision(claim, reasons, written=written, read=current, operators=operators, closure=closure, declared=declared,
                    obligations=obligations, new_interference=new,
                    seconds={"read_s": t1 - t0, "patch_s": t2 - t1, "start_realize_s": t3 - t2, "write_s": t4 - t3,
                             "system_checks_s": t5 - t4, **{f"write_{k}": v for k, v in written.seconds.items()}})
