#!/usr/bin/env python3
"""Measure the promoted P065 monument against the frozen golden envelope.

Machine-local: reads the frozen V3 golden schematic (digest-pinned,
read-only) and the promoted P065 probe's final voxel view, downsamples the
golden by the declared factor two, and persists a fidelity receipt with
exact denominators. The monument was derived without loading any golden
geometry; this comparison is an external yardstick, not a reproduction
claim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archive.archflow.evaluation.inverse_derivation import (  # noqa: E402
    iou,
    load_sponge_schematic,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination

PROBE = ROOT / "probes" / "p065-monument-derivation"
RUN_ID = "monument-001"
GOLDEN_SCHEM = Path(r"D:/ARCHFLOW_V3/samples/pantheon_golden/pantheon.schem")
GOLDEN_SHA = "61e0e7cbdb0944b8065071d11a46bbaff843e8c8fe6802dd97484b8176886540"
SCALE = 2


def _normalised(cells):
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    zs = [c[2] for c in cells]
    x0, y0, z0 = min(xs), min(ys), min(zs)
    return frozenset((x - x0, y - y0, z - z0) for x, y, z in cells)


sys.path.insert(0, str(ROOT / "archive" / "tools"))
from _probe_paths import resolve_probe_root  # noqa: E402

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True)
    args = parser.parse_args(argv)

    repo = FilesystemProjectRepository.open(PROBE)
    run = repo.load_run(RUN_ID)
    dest = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=RUN_ID)
    view = None
    for ref in repo.list_json(run=run, destination=dest):
        if "p065-stage-3-voxel-view" in ref.relative_path:
            view = repo.load_json(ref)
            view_ref = ref
    if view is None:
        raise SystemExit("promoted probe lacks the stage-3 voxel view")
    monument = _normalised(
        {tuple(cell) for cell in view["occupied_cells"]}
    )

    golden = load_sponge_schematic(GOLDEN_SCHEM, expected_sha256=GOLDEN_SHA)
    down = frozenset(
        (x // SCALE, y // SCALE, z // SCALE) for x, y, z in golden.solid
    )
    down = _normalised(down)

    def footprint(cells):
        return frozenset((x, z) for x, _, z in cells)

    def silhouette(cells):
        return frozenset((x, y) for x, y, _ in cells)

    metrics = {
        "declared_scale_factor": SCALE,
        "monument_occupied_cells": len(monument),
        "golden_downsampled_cells": len(down),
        "plan_footprint_iou": iou(footprint(monument), footprint(down)),
        "elevation_silhouette_iou": iou(
            silhouette(monument), silhouette(down)
        ),
        "monument_bbox": [
            max(c[i] for c in monument) + 1 for i in range(3)
        ],
        "golden_downsampled_bbox": [
            max(c[i] for c in down) + 1 for i in range(3)
        ],
    }
    receipt = {
        "schema": "P065MonumentFidelityReceipt@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "base": {
            "project_id": run.base.project_id,
            "version": run.base.version,
            "state_sha256": run.base.require_digest(),
        },
        "measured_at": args.now,
        "voxel_view_ref": view_ref.uri,
        "golden_schematic_sha256": GOLDEN_SHA,
        "metrics": metrics,
        "claim_boundary": {
            "reproduction_claimed": False,
            "full_fidelity_claimed": False,
            "golden_loaded_as_generated_output": False,
            "note": (
                "the monument was derived from raw project input by a "
                "scripted provider without reading the golden; this "
                "receipt only measures coarse envelope agreement at the "
                "declared half scale"
            ),
        },
        "canonical_write_authority": False,
    }
    ref = repo.put_json(
        run=run,
        destination=dest,
        record_kind="p065-monument-fidelity",
        payload=receipt,
    )
    repo.verify()
    print(json.dumps(metrics, indent=1))
    print(f"retained {ref.uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
