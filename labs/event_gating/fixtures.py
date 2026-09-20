"""Fixed public event traces from the existing source-bound P036/OCCT fixture.

These are read-only observations of authored synthetic states, not a replay of
the provider-driven courtyard design loop. Expected actions are evaluation
labels; callers must remove them before constructing provider inputs.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter

from labs.spatial_observation.fixture import Fixture, SourceMismatch


MINIMUM_TWIN_DISTANCE = 3.5
DEPENDENCIES = {
    "spatial_fixture": {
        "pull_request": 198,
        "commit": "72f424800a4eadf71604648d2d93f8163f22b10e",
        "api": "labs.spatial_observation.fixture.Fixture",
    },
    "design_loop_fixture": {
        "pull_request": 202,
        "commit": "f4399f4f9db0cab3ceba6d2fd804d90bf0261afe",
        "api": "tests.monkeymonitor.run_design_loop: bench.project_fixture, retained_record, source_fields_preserved",
    },
}

# Wording and labels are fixed before any provider call. Holdout wording and
# scale differ, but use the same source-bound geometric operations as dev.
SEMANTICS = {
    "dev": (
        ("仅刷新进度文字，模型与设计要求都不变。", "continue"),
        ("Status refresh only; 保持当前设计和约束。", "continue"),
        ("入口好像有些拘束，请先重新考虑这里的空间关系。", "review"),
        ("Keep the geometry for now，但入口是否足够从容需要再判断。", "review"),
    ),
    "holdout": (
        ("这是同步备注：沿用已经确认的形体、关系和全部要求。", "continue"),
        ("Progress ping，no new requirement or model edit.", "continue"),
        ("两边的关系似乎还可以再斟酌一下，先不要改尺寸。", "review"),
        ("The arrangement may feel too closed；请重新斟酌，不要先动模型。", "review"),
    ),
}


def _observe(fixture, *, pair=("twin-a", "twin-b"), hard_minimum=None):
    """Use actual exact query keys; do arithmetic outside every model."""
    started = perf_counter()
    queries = [
        fixture.exact_query("pair", {"first": pair[0], "second": pair[1]}, source=fixture.source),
        fixture.exact_query("dependencies", {"ids": [pair[0]]}, source=fixture.source),
        fixture.exact_query("state", {"ids": list(pair)}, source=fixture.source),
    ]
    relation, dependencies, state = (row["result"] for row in queries)
    # Fail explicitly if the inherited adapter changes its public result keys.
    if not {"distance_m", "common_volume_m3"}.issubset(relation):
        raise ValueError("Exact pair query lacks distance_m/common_volume_m3")
    if any(row["source"] != fixture.source for row in queries):
        raise SourceMismatch("query source changed during fixed observation")
    observation = {
        "relation": {"distance_m": relation["distance_m"],
                     "intersection_volume_m3": relation["common_volume_m3"]},
        "hard_valid": None if hard_minimum is None else relation["distance_m"] >= hard_minimum,
        "dependency_closure": sorted(dependencies["closure"]),
        "entity_ids": sorted(row["id"] for row in state["entities"]),
    }
    return observation, {
        "observation_seconds": perf_counter() - started,
        "query_count": len(queries),
        "query_bytes": len(json.dumps(queries, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        "raw_queries": queries,
        "observation_verified": True,
    }


def _checkpoint(identifier, before, fixture, action, *, pair=("twin-a", "twin-b"),
                hard_minimum=None, text=None, view=None):
    after, measurements = _observe(fixture, pair=pair, hard_minimum=hard_minimum)
    images, image_sources, render_seconds = [], [], 0.0
    if view:
        rendered = fixture.render_views((view,))[view]
        if rendered["source"] != fixture.source:
            raise SourceMismatch("render does not match event source")
        images.append(rendered["png"])
        image_sources.append(rendered["source"])
        render_seconds = rendered["render_seconds"]
    return {
        "id": identifier,
        "current_source": deepcopy(fixture.source), "observed_source": deepcopy(fixture.source),
        "before": deepcopy(before), "after": after,
        "semantic_text": text, "requires_visual": bool(view), "images": images,
        "image_sources": image_sources, "image_sources_verified": bool(images) and all(
            source == fixture.source for source in image_sources),
        "render_seconds": render_seconds, "image_bytes": sum(map(len, images)),
        "expected_action": action, "meaningful": action != "continue", **measurements,
    }


def _portico_readback(root):
    """Reuse #202's retained-input checks without inventing loop outcomes."""
    from tests.monkeymonitor import run_design_loop

    fixture = run_design_loop.bench.project_fixture()
    repository, ref = fixture.make_project(root)
    before_head = repository.read_head()
    record = run_design_loop.retained_record(repository, fixture.REFERENCE_RUN_ID)
    preserved = run_design_loop.source_fields_preserved(record, repository.load_json(ref), "lower")
    if not preserved or repository.read_head() != before_head:
        raise ValueError("Existing portico fixture readback did not preserve source")
    return {
        "project_id": fixture.PROJECT_ID, "source_run": fixture.REFERENCE_RUN_ID,
        "state_digest": run_design_loop.bench.StateRecord.from_dict(record).state_digest,
        "retained_fields_preserved": preserved, "head_unchanged_by_readback": True,
        "provider_calls": 0, "courtyard_loop_replayed": False,
        "scope": "existing fixed portico StateRecord readback and source_fields_preserved only",
    }


def prepare(root: Path) -> dict:
    """Prepare in a new explicit external diagnostic root; never edit a user project.

    Project records, STEP files and execution receipts are owned by the reused
    P036 fixture. The caller owns serializing returned diagnostic values/images.
    """
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("diagnostic root must be an explicit absolute path")
    root = root.resolve()
    repository_root = Path(__file__).resolve().parents[2]
    if root == repository_root or repository_root in root.parents:
        raise ValueError("diagnostic root must be outside the source checkout")
    if root.exists():
        raise FileExistsError("use a new diagnostic root; prior measurements are retained")
    started = perf_counter()
    root.mkdir(parents=True, exist_ok=False)
    common_started = perf_counter()
    portico = _portico_readback(root / "portico")
    common_seconds = perf_counter() - common_started
    tasks = []
    sources = {}
    for split, scale in (("dev", 1.0), ("holdout", 2.0)):
        common_started = perf_counter()
        base = Fixture.create(root / "spatial" / split / "base", scale=scale)
        changed = Fixture.create(root / "spatial" / split / "changed", variant="heldout", scale=scale)
        common_seconds += perf_counter() - common_started
        sources[split] = {"base": base.source, "changed": changed.source, "scale": scale}
        heads = [(fixture.repository, fixture.repository.read_head()) for fixture in (base, changed)]

        def task(kind, fixture=base, *, pair=("twin-a", "twin-b"), hard_minimum=None):
            baseline_started = perf_counter()
            before, baseline = _observe(fixture, pair=pair, hard_minimum=hard_minimum)
            row = {"id": f"{split}-{kind}", "split": split, "kind": kind, "scale": scale,
                   "checkpoints": [], "preparation_seconds": perf_counter() - baseline_started,
                   "baseline_observation": baseline}
            tasks.append(row)
            return row, before

        row, before = task("harmless")
        row["checkpoints"] = [_checkpoint(f"{row['id']}-{i}", before, base, "continue") for i in range(4)]

        pair = ("u-shell", "insert")
        row, before = task("relation", pair=pair)
        row["checkpoints"] = [_checkpoint(row["id"] + "-0", before, changed, "review", pair=pair)]

        minimum = MINIMUM_TWIN_DISTANCE * scale
        row, before = task("hard", changed, hard_minimum=minimum)
        row["constraint"] = {"pair": ["twin-a", "twin-b"], "minimum_distance_m": minimum,
                             "comparison": "distance_m >= minimum_distance_m"}
        failed = _checkpoint(row["id"] + "-0", before, base, "revise", hard_minimum=minimum)
        recovered = _checkpoint(row["id"] + "-1", failed["after"], changed, "review", hard_minimum=minimum)
        row["checkpoints"] = [failed, recovered]

        row, before = task("stale", changed)
        stale_baseline_started = perf_counter()
        stale_observation, stale_baseline = _observe(base)
        row["preparation_seconds"] += perf_counter() - stale_baseline_started
        row["stale_cached_observation"] = stale_baseline
        rejection_started = perf_counter()
        try:
            changed.exact_query("pair", {"first": "twin-a", "second": "twin-b"}, source=base.source)
        except SourceMismatch as exc:
            rejection = {"type": type(exc).__name__, "message": str(exc)}
        else:
            raise AssertionError("real exact query accepted stale source")
        row["checkpoints"] = [{
            "id": row["id"] + "-0", "current_source": changed.source, "observed_source": base.source,
            "before": before, "after": stale_observation, "semantic_text": None,
            "requires_visual": False, "images": [], "image_sources": [], "image_sources_verified": False,
            "render_seconds": 0.0, "image_bytes": 0, "expected_action": "refresh", "meaningful": True,
            "observation_seconds": perf_counter() - rejection_started, "query_count": 1,
            "query_bytes": 0, "raw_queries": [], "observation_verified": False, "stale_rejection": rejection,
        }]

        row, before = task("visual")
        row["checkpoints"] = [_checkpoint(f"{row['id']}-{i}", before, base, "visual-review", view=view)
                              for i, view in enumerate(("top", "front"))]

        row, before = task("semantic")
        row["checkpoints"] = [_checkpoint(f"{row['id']}-{i}", before, base, action, text=text)
                              for i, (text, action) in enumerate(SEMANTICS[split])]
        if any(repository.read_head() != head for repository, head in heads):
            raise ValueError("read-only event preparation changed canonical project HEAD")

    checkpoints = [point for row in tasks for point in row["checkpoints"]]
    return {
        "tasks": tasks, "preparation_seconds": perf_counter() - started,
        "preparation": {
            "common_seconds": common_seconds,
            "task_seconds": sum(row["preparation_seconds"] for row in tasks),
            "checkpoint_observation_seconds": sum(point["observation_seconds"] for point in checkpoints),
            "checkpoint_render_seconds": sum(point["render_seconds"] for point in checkpoints),
        },
        "provenance": {
            "dependencies": deepcopy(DEPENDENCIES), "portico_readback": portico, "sources": sources,
            "pair_volume_mapping": "exact_query.result.common_volume_m3 -> relation.intersection_volume_m3",
            "design_loop_replayed": False, "canonical_heads_unchanged_by_observation": True,
            "data": "public authored synthetic states; no private building inputs",
            "limits": ["fixed-state event replay; no production wake policy changes",
                       "declared dependency closure is not architectural completeness",
                       "orthographic line PNGs are not photorealistic appearance evidence",
                       "dev and holdout are fixed structural variants, not a broad architecture benchmark"],
        },
    }
