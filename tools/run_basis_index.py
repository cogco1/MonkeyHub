#!/usr/bin/env python3
"""P076: derive the decision-keyed basis index for one probe.

Scans every run's records, derives the sharded index, and writes it
under ``probes/<p>/index/basis/`` — one shard per decision ref, one
reverse shard per source, and a manifest naming the record files the
view was derived from. The index is rebuildable at any time and holds
no authority; re-running overwrites it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archflow.research.compat.unscoped_v1 import (  # noqa: E402
    build_basis_index,
    decision_slug,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-root", required=True)
    args = parser.parse_args(argv)

    root = Path(args.probe_root)
    pairs = []
    for record_path in sorted(root.glob("runs/*/records/*.json")):
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        pairs.append((record_path.name, payload))
    index = build_basis_index(pairs)

    out_dir = root / "index" / "basis"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.json"):
        stale.unlink()
    for decision_ref, shard in index.decisions.items():
        (out_dir / f"{decision_slug(decision_ref)}.json").write_text(
            json.dumps(shard, indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
    (out_dir / "_sources.json").write_text(
        json.dumps(index.sources, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "_manifest.json").write_text(
        json.dumps(
            {
                "schema": "BasisIndexManifest@1",
                "derived_view": True,
                "authority": False,
                "derived_from": list(index.derived_from),
                "summary": index.summary(),
            },
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summary = index.summary()
    print(f"  shards: {summary['decision_count']} decisions, "
          f"{summary['source_count']} sources")
    print(f"  covered: {len(summary['covered'])}")
    for ref in summary["covered"]:
        facts = len(index.decisions[ref]["facts"])
        print(f"    {ref}  ({facts} facts)")
    print(f"  uncovered: {len(summary['uncovered'])}")
    for ref in summary["uncovered"]:
        print(f"    {ref}  <-- no adopted fact")
    print(f"BASIS INDEX WRITTEN -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
