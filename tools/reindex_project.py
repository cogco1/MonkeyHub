"""Re-index a project: draft Element@1 rows from its exported models, as records of a new run.

    py tools/reindex_project.py --project <root> --out-run reindex-001 \
        --source project://<id>/runs/.../stage-4-3dm-inspection-<sha>.json@0 \
        --source project://<id>/runs/.../seat-3dm-inspection-<sha>.json@1 ...

Each ``--source`` is a 3dm inspection record the repository holds, with a
precedence after ``@``: 0 is the base, a higher number re-realizes the base
for the components and sides it carries. The tool reads the authored record
and the sources, drafts the elements (capabilities.element_reindex), and
writes two records into the run named by ``--out-run`` through the
repository: a ``component-catalog`` (objects, drafts, coverage, ambiguities)
and a draft ``state-record`` successor. It writes nothing else: not the
authored record, not canonical/, not the runs it read. ``--dry-run`` prints
the summary and writes no record; ``--markdown`` prints a coverage summary
in Markdown to stdout after the run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from archflow.capabilities.element_reindex import AMBIGUOUS, DRAFT, ERROR, EXISTING, reindex  # noqa: E402
from archflow.project.inputs import load_authored_record  # noqa: E402
from archflow.project.ports import PersistenceArea, PersistenceDestination  # noqa: E402
from archflow.project.record_kinds import COMPONENT_CATALOG, STATE_RECORD  # noqa: E402
from archflow.project.refs import record_ref_from_uri  # noqa: E402
from archflow.project.repository import FilesystemProjectRepository  # noqa: E402


def _source(arg: str) -> tuple[str, int]:
    uri, _, precedence = arg.rpartition("@")
    if not uri or not precedence.isdigit():
        raise argparse.ArgumentTypeError(f"--source needs <record uri>@<precedence>, got {arg!r}")
    return uri, int(precedence)


def markdown(catalog: dict) -> str:
    s = catalog["summary"]
    lines = [f"# Re-index coverage: {catalog['project_id']} / {catalog['run_id']}", "",
             f"Base record digest `{catalog['base_record_digest'][:16]}...`; state digest `{str(catalog['base_state_digest'] or 'none')[:16]}...`.", "",
             "## Sources", ""]
    for src in catalog["sources"]:
        lines.append(f"- precedence {src['precedence']}: `{src['ref']}` ({src['object_count']} objects, file {str(src.get('file_sha256'))[:12]})")
    lines += ["", "## Summary", "",
              f"- objects {s['objects']}: bound {s['bound']}, superseded {s['superseded']}, alternate {s['alternate']}",
              f"- elements: draft {s['elements_draft']}, existing {s['elements_existing']}, ambiguous {s['elements_ambiguous']}, error {s['elements_error']}",
              f"- axes drafted {s['axes_drafted']}; relations derived {s['relations_derived']}",
              f"- components: " + ", ".join(f"{k} {v}" for k, v in s["components"].items()),
              "", "## Components", "", "| component | status | objects (bound/total) | with element | elements | ambiguous |", "|---|---|---|---|---|---|"]
    for c in catalog["components"]:
        lines.append(f"| {c['component_id']} | {c['catalog_status']} | {c['objects_bound']}/{c['objects_total']} | {c['objects_with_element']} | {', '.join(c['elements']) or '—'} | {', '.join(c['ambiguous']) or '—'} |")
    lines += ["", "## Elements", "", "| element | producer | status | confidence | residual m | objects | notes |", "|---|---|---|---|---|---|---|"]
    for e in catalog["elements"]:
        lines.append(f"| {e['element_id']} | {e['producer'] or '—'} | {e['status']} | {e['confidence']} | {e['residual_m'] if e['residual_m'] is not None else '—'} | {len(e['objects'])} | {'; '.join(e['notes'])} |")
    if catalog["axes_drafted"]:
        lines += ["", "## Axes drafted", ""]
        for a in catalog["axes_drafted"]:
            lines.append(f"- {a['axis_id']} role {a['role']}: {a['const']} = {a['value']}")
    if catalog["unknown_components"]:
        lines += ["", "## Unknown components (objects tagged with a component the record does not declare)", ""]
        lines += [f"- {u}" for u in catalog["unknown_components"]]
    alternates = [o for o in catalog["objects"] if o["status"] == "alternate"]
    if alternates:
        lines += ["", f"## Alternates ({len(alternates)} objects with no precedence over another source)", ""]
        seen = set()
        for o in alternates:
            key = (o["component_id"], o["side"], o["source_ref"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {o['component_id']} / {o['side']}: `{o['source_ref']}` — {o['note']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="project root (the repository's manifest lives here)")
    parser.add_argument("--out-run", required=True, help="the run the catalog and the draft successor are written into")
    parser.add_argument("--source", action="append", required=True, type=_source, help="<inspection record uri>@<precedence>; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="derive and summarize; write no record")
    parser.add_argument("--markdown", action="store_true", help="print the coverage summary as Markdown")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # the summary carries record uris and notes; the console is not always utf-8
    except (AttributeError, ValueError):
        pass

    repository = FilesystemProjectRepository.open(Path(args.project))
    authored = load_authored_record(repository)
    record = authored.record
    sources = []
    for uri, precedence in args.source:
        ref = record_ref_from_uri(uri, record.project_id)
        sources.append((repository.load_json(ref), uri, precedence))
    result = reindex(record, sources)
    catalog = result.catalog(run_id=args.out_run)
    successor = result.successor(run_id=args.out_run, basis_refs=[uri for uri, _ in args.source])

    s = catalog["summary"]
    print(f"objects {s['objects']} (bound {s['bound']}, superseded {s['superseded']}, alternate {s['alternate']})")
    print(f"elements draft {s['elements_draft']} existing {s['elements_existing']} ambiguous {s['elements_ambiguous']} error {s['elements_error']}; axes drafted {s['axes_drafted']}; relations {s['relations_derived']}")
    print("components " + ", ".join(f"{k} {v}" for k, v in s["components"].items()))
    for d in result.drafts:
        if d.status in (ERROR,):
            print(f"  ERROR {d.element_id}: {'; '.join(d.notes)}")
    print(f"successor record: {len(successor.entities)} entities, {len(successor.relations)} relations, digest {successor.digest[:16]}... (authored {record.digest[:16]}...)")
    if args.markdown:
        print(markdown(catalog))
    if args.dry_run:
        return 0
    run_layout = repository.layout.run(args.out_run)
    run = repository.load_run(args.out_run) if run_layout.manifest.exists() else repository.create_run(args.out_run)
    destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=args.out_run)
    catalog_ref = repository.put_json(run=run, destination=destination, record_kind=COMPONENT_CATALOG, payload=catalog)
    successor_ref = repository.put_json(run=run, destination=destination, record_kind=STATE_RECORD, payload=successor.to_dict())
    print(f"wrote {catalog_ref.uri}")
    print(f"wrote {successor_ref.uri}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
