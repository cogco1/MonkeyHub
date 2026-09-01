#!/usr/bin/env python3
"""Import a D2 basis slice into a project so its evidence travels with it.

RAG evidence belongs to the project that uses it. This tool copies the
full basis slice — snapshots (so quote spans stay verifiable
in-project), queries, adoptions, calibrations — from a source project
into the target project's records, each accompanied by a typed import
record naming the source URI and digest. The target then rebuilds its
own ``index/basis`` shards and a stage-contract execution JSON can cite
in-project URIs only. Original records are never modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, str(entry))

from _probe_paths import resolve_probe_root  # noqa: E402
from archflow.project import (  # noqa: E402
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)

IMPORT_SCHEMAS = {
    "WebEvidenceSnapshot@1": "web-evidence-snapshot",
    "PrecedentQuery@1": "precedent-query",
    "P070ResearchInvocation@1": "research-invocation",
    "P070ResearchCandidates@1": "research-candidates",
    "P070ResearchCandidates@2": "research-candidates",
    "PrecedentAdoption@1": "precedent-adoption",
    "P070DecisionCalibration@1": "decision-calibration",
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True)
    parser.add_argument("--source-project", required=True)
    parser.add_argument("--target-project", required=True)
    parser.add_argument("--target-run", required=True)
    args = parser.parse_args(argv)

    source_root = resolve_probe_root(args.source_project)
    target_root = resolve_probe_root(args.target_project)
    repository = FilesystemProjectRepository.open(target_root)
    run = repository.load_run(args.target_run)
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=args.target_run
    )

    imported = {kind: 0 for kind in IMPORT_SCHEMAS.values()}
    manifest_rows = []
    for record_path in sorted(source_root.glob("runs/*/records/*.json")):
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        schema = payload.get("schema")
        kind = IMPORT_SCHEMAS.get(schema)
        if kind is None:
            continue
        source_uri = (
            f"project://{args.source_project}/runs/"
            f"{record_path.parents[1].name}/records/{record_path.name}"
        )
        identity = (
            payload.get("adoption_id")
            or payload.get("query_id")
            or payload.get("text_sha256", "")[:12]
            or "record"
        )
        ref = repository.put_json(
            run=run,
            destination=destination,
            record_kind=f"{kind}-{identity}",
            payload=payload,
        )
        repository.put_json(
            run=run,
            destination=destination,
            record_kind=f"basis-import-{kind}-{identity}",
            payload={
                "schema": "BasisImport@1",
                "target_project": args.target_project,
                "imported_at": args.now,
                "imported_from": source_uri,
                "imported_record": ref.uri,
                "source_record_sha256": record_path.name.rsplit(
                    "-", 1
                )[-1].removesuffix(".json"),
                "note": (
                    "evidence travels with the project; the source "
                    "record remains canonical history"
                ),
                "canonical_write_authority": False,
            },
        )
        imported[kind] += 1
        manifest_rows.append(
            {"kind": kind, "id": identity, "from": source_uri}
        )
    repository.put_json(
        run=run,
        destination=destination,
        record_kind="basis-import-manifest",
        payload={
            "schema": "BasisImportManifest@1",
            "target_project": args.target_project,
            "imported_at": args.now,
            "source_project": args.source_project,
            "counts": imported,
            "rows": manifest_rows,
            "canonical_write_authority": False,
        },
    )
    repository.verify()
    print("imported:", imported)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
