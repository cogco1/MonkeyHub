"""Governance lookup: module contracts, live work and generated ledgers.

The registry holds only work that is not finished. Finished work lives in Git
history; a card that is done is deleted together with its registry entry.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The renderers read the source they document, so they must read *this* tree
# and not whichever archflow happens to be importable from the interpreter.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REGISTRY_PATH = ROOT / "governance" / "work_registry.json"
MAP_PATH = ROOT / "docs" / "DYNAMIC_MAP.md"
PLANNING_INDEX = ROOT / "docs" / "mapping" / "planning" / "INDEX.md"
MODULE_REGISTRY = ROOT / "governance" / "module_registry.json"
SYSTEM_MAP = ROOT / "docs" / "SYSTEM_MAP.md"
SEMANTIC_REGISTRY = ROOT / "docs" / "SEMANTIC_REGISTRY.md"
SCHEMA = "ArchFlowDevelopmentRegistry@2"
STATUSES = ("active", "ready", "blocked")
MODULE_SECTIONS = (
    "owns", "does_not_own", "public_api", "depends_on", "source_paths", "tests",
    "inputs", "outputs", "invariants",
)
# The capability index lives in the same registry file: a capability names the
# goals it serves, the owner that implements it and the entry points that
# already exist. It is a search index over registered owners, not a second
# place where software is registered.
CAPABILITY_SECTIONS = (
    "goals", "aliases", "requires", "reads", "writes", "effects", "entrypoints",
    "composes", "produces", "validators", "works", "missing", "tests",
)


def _matcher():
    """The one capability matching rule, imported from the module that serves it.

    The rule belongs to the capability index the Studio answers ``GET
    /api/capabilities`` with; keeping a second copy here would let the
    governance CLI and the running service disagree about what a goal finds.
    This CLI still owns the registry path, the paging and the printing — only
    the rule is borrowed, and the module it comes from imports nothing but the
    standard library to be borrowed this cheaply.
    """

    studio = ROOT / "apps" / "archflow-studio" / "api"
    if str(studio) not in sys.path:
        sys.path.append(str(studio))
    from archflow_studio_api.application.capability import match_capabilities

    return match_capabilities


def load_module_registry() -> dict:
    """The module registry, schema-checked once for every reader here."""

    data = json.loads(MODULE_REGISTRY.read_text(encoding="utf-8"))
    if data.get("schema") != "ArchFlowModuleRegistry@1":
        raise SystemExit(f"unsupported module registry schema: {data.get('schema')!r}")
    return data


def capabilities() -> list[dict]:
    """Every registered capability entry, in registry order."""

    return list(load_module_registry().get("capabilities", []))


def _capability_lookup(query: str, *, section: str | None, limit: int, offset: int) -> dict:
    """Find capabilities by id or by the words a goal is written in.

    No hit is not a claim that nothing can do this: the index covers the
    capabilities that have been written down, and the answer says so and
    points at the module lookup, which covers every registered owner.
    """

    entries = capabilities()
    if not query.strip():
        raise SystemExit("capability query must not be empty")
    matches, evidence, note = _matcher()(entries, query)
    matches, evidence = list(matches), list(evidence)
    result = {"query": query, "match_count": len(matches), "offset": offset, "limit": limit,
              "registered": len(entries)}
    if len(matches) != 1:
        result["candidates"] = [
            {"capability_id": c["capability_id"], "owner": c.get("owner"), "status": c.get("status"),
             "purpose": c.get("purpose", ""), "matched": list(evidence[index + offset][1])}
            for index, c in enumerate(matches[offset:offset + limit])
        ]
        result["remaining"] = max(0, len(matches) - offset - limit)
        if note is not None:
            result["note"] = note + (
                " Look the owner up with `devctl module <keywords>`: every registered owner is there, "
                "whether or not a capability entry has been written for it."
            )
        return result
    result["matched"] = list(evidence[0][1])
    entry = matches[0]
    detail = {k: entry[k] for k in ("capability_id", "owner", "execution_owner", "kind", "status",
                                    "purpose", "purpose_zh", "inputs_ref", "estimated_cost",
                                    "status_note") if k in entry}
    omitted = {}
    for name in (section,) if section else CAPABILITY_SECTIONS:
        values = entry.get(name, [])
        detail[name] = values[offset:offset + limit]
        before, after = min(offset, len(values)), max(0, len(values) - offset - limit)
        if before or after:
            omitted[name] = {"before": before, "after": after, "total": len(values)}
    result["capability"] = detail
    result["omitted"] = omitted
    return result


def _print_capability_lookup(result: dict) -> None:
    if "capability" not in result:
        if not result["match_count"]:
            print(result["note"])
            return
        print(f"{result['match_count']} capabilities are relevant to {result['query']!r}; "
              "read one with its exact capability id:")
        for candidate in result["candidates"]:
            print(f"- {candidate['capability_id']} ({candidate['status']}) - owner {candidate['owner']}")
            print(f"  {candidate['purpose']}")
            if candidate.get("matched"):
                print(f"  matched on: {', '.join(candidate['matched'])}")
        if result["remaining"]:
            print(f"{result['remaining']} more; next page: --offset {result['offset'] + result['limit']}")
        return
    entry = result["capability"]
    if result.get("matched"):
        print(f"relevant to {result['query']!r} on: {', '.join(result['matched'])}")
    print(f"{entry['capability_id']} ({entry.get('status')}) - owner {entry.get('owner')}"
          + (f", executed by {entry['execution_owner']}" if entry.get("execution_owner") else ""))
    print(entry.get("purpose", ""))
    if entry.get("purpose_zh"):
        print(entry["purpose_zh"])
    if entry.get("inputs_ref"):
        print(f"inputs: {entry['inputs_ref']}")
    for section in CAPABILITY_SECTIONS:
        if section not in entry:
            continue
        print(f"\n{section}:")
        for value in entry[section]:
            print(f"- {value}")
        if not entry[section]:
            print("- (no entries on this page)" if section in result["omitted"] else "- (none declared)")
        if section in result["omitted"]:
            page = result["omitted"][section]
            print(f"  [{page['total']} total; {page['before']} earlier, {page['after']} later]")
    if entry.get("estimated_cost"):
        print(f"\nestimated cost: {entry['estimated_cost']}")
    if entry.get("status_note"):
        print(f"status: {entry['status_note']}")


def _module_lookup(query: str, *, section: str | None, limit: int, offset: int) -> dict:
    """Read registry metadata only; never import or walk the matching source."""

    data = load_module_registry()
    needle = query.strip().casefold()
    if not needle:
        raise SystemExit("module query must not be empty")
    modules = data["modules"]
    exact = next((m for m in modules if m["module_id"].casefold() == needle), None)
    terms = needle.split()

    def relevance(entry: dict) -> tuple[int, str]:
        module_id = entry["module_id"].casefold()
        path = entry["owner_path"].casefold()
        rank = 0 if all(t in module_id for t in terms) else 1 if all(t in path for t in terms) else 2
        return rank, module_id

    matches = [exact] if exact is not None else sorted(
        (m for m in modules if all(t in " ".join(
            [m["module_id"], m["owner_path"], m["purpose"],
             *m.get("owns", []), *m.get("public_api", []), *m.get("files", [])]
        ).casefold() for t in terms)),
        key=relevance,
    )
    result = {"query": query, "match_count": len(matches), "offset": offset, "limit": limit}
    if len(matches) != 1:
        result["candidates"] = [
            {"module_id": m["module_id"], "owner_path": m["owner_path"],
             "purpose": m["purpose"][:240] + ("..." if len(m["purpose"]) > 240 else "")}
            for m in matches[offset:offset + limit]
        ]
        result["remaining"] = max(0, len(matches) - offset - limit)
        return result

    entry = matches[0]
    detail = {k: entry[k] for k in ("module_id", "owner_path", "purpose", "status") if k in entry}
    omitted = {}
    for name in (section,) if section else MODULE_SECTIONS:
        values = (list(dict.fromkeys([entry["owner_path"], *entry.get("files", [])]))
                  if name == "source_paths" else entry.get(name, []))
        detail[name] = values[offset:offset + limit]
        before = min(offset, len(values))
        after = max(0, len(values) - offset - limit)
        if before or after:
            omitted[name] = {"before": before, "after": after, "total": len(values)}
    result["module"] = detail
    result["omitted"] = omitted
    return result


def _print_module_lookup(result: dict) -> None:
    if "module" not in result:
        count = result["match_count"]
        if not count:
            print(f"No modules match {result['query']!r}.")
            return
        print(f"{count} modules match {result['query']!r}; use an exact module id:")
        for candidate in result["candidates"]:
            print(f"- {candidate['module_id']} - {candidate['owner_path']}")
            print(f"  {candidate['purpose']}")
        if result["remaining"]:
            print(f"{result['remaining']} more; next page: --offset {result['offset'] + result['limit']}")
        return
    module = result["module"]
    print(f"{module['module_id']} - {module['owner_path']}")
    print(module["purpose"])
    if module.get("status"):
        print(f"status: {module['status']}")
    for section in MODULE_SECTIONS:
        if section not in module:
            continue
        print(f"\n{section}:")
        for value in module[section]:
            print(f"- {value}")
        if not module[section]:
            print("- (no entries on this page)" if section in result["omitted"] else "- (none declared)")
        if section in result["omitted"]:
            page = result["omitted"][section]
            print(f"  [{page['total']} total; {page['before']} earlier, {page['after']} later]")
            if page["after"]:
                print(f"  Read more: --section {section} --offset {result['offset'] + result['limit']}")


def load_registry() -> dict:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise SystemExit(f"unsupported registry schema: {data.get('schema')!r}")
    seen: set[str] = set()
    for item in data["items"]:
        if item["id"] in seen:
            raise SystemExit(f"duplicate item id {item['id']}")
        seen.add(item["id"])
        if item["status"] not in STATUSES:
            raise SystemExit(f"{item['id']}: status must be one of {STATUSES}")
        if "card" not in item and re.fullmatch(r"GH-[1-9][0-9]*", item["id"]):
            continue
        card = item.get("card")
        if not isinstance(card, str) or not (ROOT / card).is_file():
            raise SystemExit(f"{item['id']}: missing card {card}")
    return data


def is_ready(item: dict, live_ids: set[str]) -> bool:
    """Ready when no live item blocks it; ids not in the registry are finished."""

    return item["status"] == "ready" and not any(
        dep in live_ids for dep in item.get("depends_on", ())
    )


def _work_lookup(data: dict, query: str | None) -> dict:
    """Read the current claims; archcheck owns their validation and overlaps."""

    from tools.archcheck import check_scopes, load_policy

    rows = []
    for item in data["items"]:
        if "lanes" not in item:
            candidates = [item]
        else:
            lanes = item["lanes"]
            candidates = [
                {**lane, "id": f"{item['id']}/{lane.get('id')}", "card": item.get("card")}
                for lane in lanes if isinstance(lane, dict)
            ] if isinstance(lanes, list) else []
        rows.extend(row for row in candidates if query is None or query in (item["id"], row["id"]))
    policy = load_policy(ROOT / "governance" / "architecture_policy.json")
    return {
        "query": query,
        "items": rows,
        "findings": [finding.to_dict() for finding in check_scopes(ROOT, policy, data)],
    }


def _print_work_lookup(result: dict) -> None:
    if not result["items"]:
        print(f"No work matches {result['query']!r}.")
    for row in result["items"]:
        title = row.get("issue", row.get("goal", ""))
        print(f"{row['id']} [{row.get('status')}] {title if result['query'] else str(title)[:100]}")
        if "/" not in row["id"] and result["query"] is None:
            continue
        print(f"  {row.get('branch') or 'unassigned'} | base {row.get('base_ref') or 'unassigned'} | {row.get('contributor') or 'unassigned'}")
        if result["query"] is None:
            modules = row.get("modules")
            scope = row.get("write_scope")
            dependencies = row.get("depends_on")
            print(f"  modules: {modules or 'see card'}; paths: {len(scope) if isinstance(scope, list) else 0}; depends on: {dependencies or 'none'}")
            continue
        for field in ("worktree", "reviewer", "handoff", "modules", "write_scope", "depends_on", "blocked_reason", "card"):
            value = row.get(field)
            if isinstance(value, list):
                value = ", ".join(str(entry) for entry in value) or "none"
            print(f"  {field}: {value if value is not None else 'unassigned'}")
    for finding in result["findings"]:
        print(f"{finding['code']}: {finding['message']}")


def render(data: dict) -> tuple[str, str]:
    items = data["items"]
    live_ids = {item["id"] for item in items}
    active = [i["id"] for i in items if i["status"] == "active"]
    ready = [i["id"] for i in items if is_ready(i, live_ids)]
    blocked = [i["id"] for i in items if i["status"] == "blocked" or (i["status"] == "ready" and i["id"] not in ready)]
    if active:
        nxt = f"continue active work: {', '.join(active)}"
        if ready:
            nxt += f"; independent ready work may start: {', '.join(ready)}"
    elif ready:
        nxt = f"claim one of: {', '.join(ready)}"
    else:
        nxt = "nothing is ready; decide what unblocks"
    def table(card_prefix: str) -> str:
        rows = []
        for item in items:
            target = (
                card_prefix + Path(item["card"]).name
                if item.get("card")
                else f"https://github.com/cogco1/MonkeyHub/issues/{item['id'][3:]}"
            )
            rows.append(f"| {item['id']} | {item['status']} | {item['goal']} | [{item['id']}]({target}) |")
        return "| ID | Status | Goal | Card |\n| --- | --- | --- | --- |\n" + "\n".join(rows)
    dyn = (
        "# ArchFlow V4 Dynamic Map\n\n"
        "> Generated by `python tools/devctl.py render-map` from `governance/work_registry.json`.\n"
        "> Architecture rationale is in [ARCHITECTURE.md](ARCHITECTURE.md); finished work is in Git history.\n\n"
        f"Phase: {data.get('phase', '')}\n\n"
        f"- active: {', '.join(active) or 'none'}\n"
        f"- ready: {', '.join(ready) or 'none'}\n"
        f"- blocked: {', '.join(blocked) or 'none'}\n"
        f"- next: {nxt}\n\n"
        "## Live work\n\n"
        + table("mapping/planning/")
        + "\n"
    )
    plan = (
        "# Planning ledger\n\n> Generated from `governance/work_registry.json`.\n\n"
        + table("")
        + "\n\n[Back to RMPA](../README.md)\n"
    )
    return dyn, plan


def render_system_map() -> str | None:
    """The short view of the registry: who owns what, and what they refuse."""

    if not MODULE_REGISTRY.is_file():
        return None
    data = json.loads(MODULE_REGISTRY.read_text(encoding="utf-8"))
    tick = chr(96)
    lines = [
        "# ArchFlow system map",
        "",
        "> Generated by " + tick + "python tools/devctl.py render-map" + tick + " from " + tick + "governance/module_registry.json" + tick + ".",
        "> One owner per capability. Read this before the tree; read " + tick + "docs/CANONICAL_SPINE.md" + tick + " for why.",
        "",
    ]
    groups: dict[str, list[dict]] = {}
    for entry in data["modules"]:
        groups.setdefault(entry["module_id"].split(".")[0], []).append(entry)
    for group, entries in groups.items():
        lines += [f"## {group}", ""]
        for e in sorted(entries, key=lambda x: x["module_id"]):
            flag = "" if e.get("status") == "canonical" else f" ({e.get('status')})"
            lines.append(f"### {e['module_id']}{flag} — {tick}{e['owner_path']}{tick}")
            lines.append(e["purpose"])
            lines.append("- owns: " + "; ".join(e.get("owns", [])))
            if e.get("does_not_own"):
                lines.append("- does not own: " + "; ".join(e["does_not_own"]))
            if e.get("public_api"):
                lines.append("- api: " + ", ".join(tick + x + tick for x in e["public_api"]))
            if e.get("invariants"):
                lines.append("- invariants: " + "; ".join(e["invariants"]))
            lines.append("")
    return "\n".join(lines + render_stage_ladder())


def render_stage_ladder() -> list[str]:
    """The phase ladder as the industry says it, straight from PHASE_LADDER.

    The table is generated so the map cannot drift from the enum: a phase
    added to ``DesignPhase`` without a ladder entry cannot be rendered here.
    """

    from archflow.state.stage_workflow import LOD_LEVELS, PHASE_LADDER

    tick = chr(96)
    lines = [
        "## Stage ladder",
        "",
        "Two axes: the phase (RIBA 2020 / AIA / 中国) and the BIMForum Level of",
        "Development inside it. A " + tick + "ProjectStage" + tick + " states an optional "
        + tick + "lod" + tick + " from "
        + ", ".join(str(level) for level in LOD_LEVELS)
        + "; LOD 500 is field-verified as-built and belongs to no design phase.",
        "",
        "| phase | RIBA 2020 | AIA | 中国 | LOD range |",
        "| --- | --- | --- | --- | --- |",
    ]
    for phase, entry in PHASE_LADDER.items():
        span = (
            "—"
            if entry.lod_range is None
            else f"{entry.lod_range[0]}–{entry.lod_range[1]}"
        )
        lines.append(
            f"| {tick}{phase.value}{tick} | {entry.riba_stage} | {entry.aia} "
            f"| {entry.cn} | {span} |"
        )
    lines.append("")
    return lines


def render_semantic_registry() -> str:
    """Roles, conditions and the compound phrases that resolve to them."""

    from archflow.semantics.conditions import CONDITIONS
    from archflow.semantics.registry import COMPOUND_PHRASES
    from archflow.semantics.roles import ROLES

    tick = chr(96)
    lines = ["# Architectural semantic registry", "",
             "> Generated by " + tick + "python tools/devctl.py render-map" + tick + " from " + tick + "archflow/semantics/" + tick + ".",
             "> A record's semantic_kind must resolve here (ADR-006). Entity, role and condition are three things.", ""]
    for title, terms in (("Roles", ROLES), ("Conditions", CONDITIONS)):
        lines += [f"## {title}", "", "| id | meaning | aliases |", "| --- | --- | --- |"]
        lines += [f"| {tick}{t.id}{tick} | {t.meaning} | {', '.join(t.aliases)} |" for t in terms]
        lines.append("")
    lines += ["## Compound phrases (authored before the registry; resolve once)", "", "| phrase | roles | conditions |", "| --- | --- | --- |"]
    lines += [f"| {tick}{k}{tick} | {', '.join(v[0])} | {', '.join(v[1])} |" for k, v in COMPOUND_PHRASES.items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("next")
    sub.add_parser("render-map")
    work_parser = sub.add_parser("work", help="read current lanes, bases, handoffs and scope conflicts")
    work_parser.add_argument("query", nargs="?", help="exact card or lane id, e.g. P115/team-lanes")
    work_parser.add_argument("--json", action="store_true", help="emit registered work and scope findings")
    module_parser = sub.add_parser("module", help="find one module contract without reading the whole registry")
    module_parser.add_argument("query", help="exact module id or keywords, e.g. wall")
    module_parser.add_argument("--json", action="store_true", help="emit structured lookup output")
    module_parser.add_argument("--section", choices=MODULE_SECTIONS, help="read only one contract section")
    module_parser.add_argument("--limit", type=int, default=8, help="entries per section or candidate page (1-20; default: 8)")
    module_parser.add_argument("--offset", type=int, default=0, help="skip this many section entries or candidates")
    capability_parser = sub.add_parser(
        "capability", help="find a registered capability by goal or id, without reading the whole registry")
    capability_parser.add_argument("query", help="exact capability id or goal words, e.g. 'change a height'")
    capability_parser.add_argument("--json", action="store_true", help="emit structured lookup output")
    capability_parser.add_argument("--section", choices=CAPABILITY_SECTIONS, help="read only one section")
    capability_parser.add_argument("--limit", type=int, default=8, help="entries per section or candidate page (1-20; default: 8)")
    capability_parser.add_argument("--offset", type=int, default=0, help="skip this many section entries or candidates")
    args = parser.parse_args(argv)
    if args.command == "capability":
        if not 1 <= args.limit <= 20 or args.offset < 0:
            parser.error("capability requires --limit between 1 and 20 and --offset >= 0")
        result = _capability_lookup(args.query, section=args.section, limit=args.limit, offset=args.offset)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_capability_lookup(result)
        return 0 if result["match_count"] else 1
    if args.command == "module":
        if not 1 <= args.limit <= 20 or args.offset < 0:
            parser.error("module requires --limit between 1 and 20 and --offset >= 0")
        result = _module_lookup(args.query, section=args.section, limit=args.limit, offset=args.offset)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_module_lookup(result)
        return 0 if result["match_count"] else 1
    data = load_registry()
    if args.command == "work":
        result = _work_lookup(data, args.query)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_work_lookup(result)
        return 1 if result["findings"] or (args.query is not None and not result["items"]) else 0
    if args.command == "status":
        for item in data["items"]:
            print(f"{item['id']:6s} {item['status']:8s} {item['goal'][:100]}")
    elif args.command == "next":
        live_ids = {i["id"] for i in data["items"]}
        for item in data["items"]:
            if item["status"] == "active" or is_ready(item, live_ids):
                print(f"{item['id']:6s} {item['status']}")
    else:
        dyn, plan = render(data)
        MAP_PATH.write_text(dyn, encoding="utf-8", newline="\n")
        PLANNING_INDEX.write_text(plan, encoding="utf-8", newline="\n")
        system_map = render_system_map()
        SEMANTIC_REGISTRY.write_text(render_semantic_registry(), encoding="utf-8", newline="\n")
        if system_map is not None:
            SYSTEM_MAP.write_text(system_map, encoding="utf-8", newline="\n")
        print(f"rendered {MAP_PATH.relative_to(ROOT)}, {PLANNING_INDEX.relative_to(ROOT)}" + (f" and {SYSTEM_MAP.relative_to(ROOT)}" if system_map else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
