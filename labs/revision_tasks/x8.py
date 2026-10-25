"""X8, third layer (I3): a model proposes undeclared relations; each is kept only if the start geometry confirms it.

For each start (the three fixtures and the BB building), one ``claude -p`` call per repeat, with the same fairness
controls as ``x2`` and its own system prompt (``prompts/i3_system.md``). The materials are every element with its
role (``infer.role_of``, or its component's stated intent where the role table names none) and its cold-read box,
and the relations and obligations the record declares. Each proposal naming two known elements and a known kind
becomes an ``Inferred("I3", ...)`` and is measured on the start exactly as I1 and I2 are (``infer.check_inferred``);
only those that hold are kept, as unconfirmed candidate obligations; those that fail are counted. Per call: the proposals,
the valid ones, the kept ones, those that fail verification, and recall (by element pair and kind) of the start's
undeclared-by-design relation; per start, the union of what the repeats kept and whether it recalls. The prompt's limit
of 20 proposals may hold recall down on the buildings.
"""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from archflow.state.state_record import StateRecord

from labs.revision_tasks import buildings, fixtures, infer, pipeline, x2
from labs.revision_tasks import run as runner

STARTS = {"F1": fixtures.f1_portico, "F2": fixtures.f2_portico_rotunda, "F3": fixtures.f3_wall_stair, "BB": buildings.bb_house,
          "BA": buildings.ba_pantheon}
# The relation each start leaves undeclared by design: element pair and the kind that would catch its breach.
GOLD = {"F1": ("entablature", "pediment", "support"), "F2": ("portico-entablature-rear", "transition-roof", "non_interference"),
        "F3": ("wall", "stair", "non_interference"), "BB": ("wall-core-W-L1", "stair-F1", "non_interference"),
        "BA": ("portico-roof", "intermediate-pediment", "non_interference")}
KINDS = ("support", "abutment", "near", "non_interference", "same_level")
SYSTEM = x2.PROMPTS / "i3_system.md"


def materials(record: StateRecord, realization: pipeline.Realization) -> str:
    roles = infer.role_of(record)
    intents = {e.entity_id: str(e.fields.get("intent", "")) for e in record.entities_of("Component@1")}
    boxes = infer._boxes(realization)
    elements = []
    for entity in sorted(record.entities_of("Element@1"), key=lambda e: e.entity_id):
        if entity.entity_id not in boxes:
            continue
        lo, hi = boxes[entity.entity_id]
        role = roles.get(entity.entity_id) or intents.get(entity.parent_id or "", "") or "unstated"
        elements.append(f"- {entity.entity_id}: role {role}; min [{', '.join(f'{v:.3f}' for v in lo)}] max [{', '.join(f'{v:.3f}' for v in hi)}]")
    declared = [f"- {r.relation_id}: {r.subject} {r.kind} {r.object}" for r in record.relations]
    declared += [f"- {o.obligation_id}: {o.statement}" for o in record.obligations]
    template = (x2.PROMPTS / "i3_request.md").read_text(encoding="utf-8")
    return template.replace("{elements}", "\n".join(elements)).replace("{declared}", "\n".join(declared) or "(none)")


def verify(record: StateRecord, realization: pipeline.Realization, answer: dict[str, Any] | None) -> dict[str, Any]:
    """Proposals turned into I3 inferences and measured on the start; what holds is kept."""

    proposals = (answer or {}).get("relations") or []
    elements = {e.entity_id for e in record.entities_of("Element@1")}
    declared = {frozenset((r.subject, r.object)) for r in record.relations}
    valid, rejected = [], []
    for row in proposals if isinstance(proposals, list) else []:
        a, b, kind = (row or {}).get("a"), (row or {}).get("b"), (row or {}).get("kind")
        if a not in elements or b not in elements or a == b or kind not in KINDS:
            rejected.append({"row": row, "why": "unknown element or kind"})
        elif frozenset((a, b)) in declared:
            rejected.append({"row": row, "why": "already declared"})
        else:
            basis = {"reason": str(row.get("reason", ""))[:200], **({"face": "top"} if kind == "same_level" else {})}   # the prompt's same_level is tops
            valid.append(infer.Inferred("I3", kind, a, b, basis))
    # the same narrowing as I1 and I2: an alignment the record's datums already explain is not added
    valid, narrowed = infer.narrow(record, valid, realization)
    rejected += [{"row": {"a": n["item"].a, "b": n["item"].b, "kind": n["item"].kind}, "why": n["rule"]} for n in narrowed]
    measured ={r["obligation_id"]: r for r in infer.check_inferred(infer.with_inferred(record, valid), realization)} if valid else {}
    kept = [v for v in valid if measured.get(v.obligation().obligation_id, {}).get("status") == "held"]
    return {"proposed": len(proposals) if isinstance(proposals, list) else 0, "valid": len(valid), "kept": len(kept),
            "rejected": rejected, "kept_relations": [{"a": k.a, "b": k.b, "kind": k.kind, "reason": k.basis["reason"]} for k in kept],
            "not_held": [{"a": v.a, "b": v.b, "kind": v.kind} for v in valid if v not in kept]}


def recalled(name: str, kept: list[dict[str, Any]]) -> bool:
    a, b, kind = GOLD[name]
    return any({k["a"], k["b"]} == {a, b} and k["kind"] == kind for k in kept)


def run(out: Path, *, starts: tuple[str, ...], repeats: int, ledger: x2.Ledger | None, cli: str | None) -> dict[str, Any]:
    config = out / "config"
    x2.write_config(config)
    (out / "identity.json").write_text(json.dumps({"harness": runner.harness_identity(), "prompt_files_sha256": x2.prompt_files_sha256(),
                                                   "model": x2.MODEL, "command": x2.command("claude", config, system=SYSTEM)[1:]},
                                                  indent=1, sort_keys=True) + "\n", encoding="utf-8")
    summary: dict[str, Any] = {"stopped": None, "by_start": {}}
    for name in starts:
        record = fixtures.bound(STARTS[name]())
        work = out / "starts" / name
        work.mkdir(parents=True)
        realization = pipeline.realize(record, work.resolve(), "start")
        prompt = materials(record, realization)
        (out / "starts" / f"{name}.prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
        cell = summary["by_start"].setdefault(name, {"calls": [], "recalled_in": 0})
        union: set[tuple[str, str, str]] = set()
        for repeat in range(repeats):
            if cli is None:
                continue
            try:
                ledger.check("x8_i3")
            except x2.CapReached as exc:
                summary["stopped"] = str(exc)
                break
            trial_id = f"X8-I3-{name}-r{repeat}"
            call = x2.call_model(prompt, out / "trials" / trial_id, cli=cli, config=config, system=SYSTEM)
            cost, priced = x2.price(call["result"])
            ledger.add({"at": datetime.now(timezone.utc).isoformat(), "experiment": "X8", "part": "x8_i3", "trial_id": trial_id,
                        "out": str(out), "cost_usd": cost, **priced})
            answer = x2.parse_answer(call["text"])
            checked = verify(record, realization, answer)
            hit = recalled(name, checked["kept_relations"])
            cell["recalled_in"] += int(hit)
            cell["calls"].append({"trial_id": trial_id, "proposed": checked["proposed"], "valid": checked["valid"], "kept": checked["kept"],
                                  "verified_fail": len(checked["not_held"]), "recalled": hit, "cost_usd": round(cost, 4)})
            union |= {(min(k["a"], k["b"]), max(k["a"], k["b"]), k["kind"]) for k in checked["kept_relations"]}
            cell["union_kept"] = len(union)
            cell["recalled_in_union"] = recalled(name, [{"a": a, "b": b, "kind": kind} for a, b, kind in union])
            bundle = {"trial_id": trial_id, "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                      "call": {k: call[k] for k in ("exit_code", "seconds", "init", "text", "stderr")}, "usage": priced, "cost_usd": cost,
                      "answer": answer, "checked": checked, "recalled": hit}
            (out / "trials" / f"{trial_id}.bundle.json").write_text(json.dumps(bundle, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
            print(f"{trial_id:16} proposed {checked['proposed']:2} valid {checked['valid']:2} kept {checked['kept']:2} recalled {hit} ${cost:.3f}", flush=True)
        if summary["stopped"]:
            break
    (out / "x8_i3_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the model-proposed relations (I3) into an empty directory.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--starts", default=",".join(STARTS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--ledger", type=Path, help="the shared cost ledger (required with --cli)")
    parser.add_argument("--cli", help="the claude executable; without it, only the prompts are written")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    if args.cli and not args.ledger:
        parser.error("--ledger is required with --cli")
    out.mkdir(parents=True, exist_ok=True)
    summary = run(out, starts=tuple(args.starts.split(",")), repeats=args.repeats,
                  ledger=x2.Ledger(args.ledger.resolve()) if args.ledger else None, cli=args.cli)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
