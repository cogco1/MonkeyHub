"""Exercise the source-bound visual observation channel against a running project runtime.

Development and benchmark entry only (GH-303 V0); production chat does not call it.
Frames come from the runtime's own projection owners: ``GET /api/drawings/model-view``
for a model, ``POST /api/board/export`` (PNG) for one registered page. The review
goes through the Studio's Codex transport and prints one JSON result; this tool
writes no file and changes no project. ``--monitor-dir`` adds the MonkeyMonitor
rows the runtime itself would record.

    python tools/benchmark_visual_observation.py --runtime http://127.0.0.1:8000 \
        --spec review.json --task-class spatial_formal --reason first_bundle

A follow-up review replays the Harness allowance from the previous result:
``--reason after_repair --prior previous.json --addressed f1,f3``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
for path in (REPO, REPO / "apps/archflow-studio/api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from archflow_studio_api.application.intent_agent import CodexCompiler
from archflow_studio_api.application.monitoring import MonitoredCompiler, StudioMonitor
from archflow_studio_api.application.visual_observation import (
    Criterion, EvidenceFrame, PriorFinding, ReviewReason, SourceRef, StudioModelVisualProvider, TaskClass,
    VisualObservation, VisualReviewBudget, VisualReviewRequest, model_view_frame, observe_frames, page_frame,
)


def _call(url: str, *, body: Mapping[str, Any] | None = None, timeout_s: float = 300) -> tuple[bytes, str]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, method="GET" if body is None else "POST",
                      headers={} if body is None else {"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout_s) as response:
        return response.read(), response.headers.get("Content-Type", "")


def fetch_model_frames(runtime: str, source: SourceRef, views: Sequence[str]) -> list[EvidenceFrame]:
    """Ask the model-view projection owner for each view of one exact model."""

    frames = []
    for view in views:
        query = urlencode({"runId": source.run_id, "stateDigest": source.state_digest,
                           "assetSha256": source.asset_sha256, "view": view})
        raw, _ = _call(f"{runtime.rstrip('/')}/api/drawings/model-view?{query}")
        frames.append(model_view_frame(json.loads(raw)))
    return frames


def fetch_page_frame(runtime: str, project_id: str, source: SourceRef, *, max_edge: int = 2048) -> EvidenceFrame:
    """Ask the Board export owner for one exact registered page as a clean PNG."""

    raw, media_type = _call(f"{runtime.rstrip('/')}/api/board/export", body={
        "projectId": project_id, "format": "png", "zip": False, "maxEdge": max_edge,
        "pages": [{"runId": source.run_id, "assetSha256": source.asset_sha256,
                   "revisionRef": source.revision_ref, "pageIndex": source.page_index}],
    })
    if not media_type.startswith("image/png"):
        raise ValueError(f"board export answered {media_type}, not one PNG page")
    return page_frame(source, raw)


def review_request(spec: Mapping[str, Any], budget: VisualReviewBudget) -> VisualReviewRequest:
    return VisualReviewRequest(
        domain=spec["domain"], source_refs=tuple(SourceRef.from_dict(row) for row in spec["sources"]),
        view_recipe=tuple(spec["views"]), task=spec["task"],
        criteria=tuple(Criterion(row["id"], row["text"]) for row in spec["criteria"]),
        preserve=tuple(spec.get("preserve", ())), budget=budget.allowed,
        prior_observations=tuple(PriorFinding(row["ref"], row["type"], row["text"]) for row in spec.get("prior", ())),
    )


def build_provider(*, codex: str = "codex", model: str | None = None, timeout_s: float = 240.0,
                   monitor_dir: Path | None = None) -> tuple[StudioModelVisualProvider, StudioMonitor]:
    from monkeymonitor.store import UsageLog

    monitor = StudioMonitor(None if monitor_dir is None else UsageLog(monitor_dir))
    # A bare name is resolved the way a shell would (codex is a .cmd shim on Windows).
    compiler = CodexCompiler(executable=shutil.which(codex) or codex, model=model, timeout_s=timeout_s)
    return StudioModelVisualProvider(MonitoredCompiler(compiler, monitor)), monitor


def replay_budget(task_class: str, reason: str, prior: Mapping[str, Any] | None, addressed: Sequence[str],
                  polish_reviews: int | None) -> VisualReviewBudget:
    """Rebuild one loop's Harness allowance from the previous review's own result."""

    budget = VisualReviewBudget.for_task(task_class, polish_reviews=polish_reviews)
    if prior is not None:
        previous = VisualObservation.from_dict(prior["observation"])
        budget.used = previous.review_index
        budget.settle(previous)
        if ReviewReason(reason) is ReviewReason.AFTER_REPAIR:
            budget.note_repair(addressed)
    return budget


def run_review(runtime: str, spec: Mapping[str, Any], *, task_class: str, reason: str,
               prior: Mapping[str, Any] | None = None, addressed: Sequence[str] = (),
               polish_reviews: int | None = None, provider=None, monitor=None,
               budget: VisualReviewBudget | None = None) -> dict[str, Any]:
    """Fetch the owner frames, then observe once; an in-process loop passes its own budget."""

    budget = budget or replay_budget(task_class, reason, prior, addressed, polish_reviews)
    refused = budget.refusal(reason)
    if refused is not None:
        raise refused  # nothing is rendered for a review the Harness will not admit
    started = time.perf_counter()
    frames: list[EvidenceFrame] = []
    for row in spec["sources"]:
        source = SourceRef.from_dict(row)
        if source.kind == "model":
            frames += fetch_model_frames(runtime, source, spec["views"])
        else:
            frames.append(fetch_page_frame(runtime, spec["projectId"], source, max_edge=int(spec.get("maxEdge", 2048))))
    fetched = time.perf_counter()
    request = review_request(spec, budget)
    result = observe_frames(request, frames, provider=provider, budget=budget, reason=reason, monitor=monitor,
                     project_id=spec.get("projectId"))
    done = time.perf_counter()
    return {
        "request": {"reviewId": request.review_id, "domain": request.domain, "taskClass": task_class,
                    "reason": reason, "budget": request.budget, "views": list(request.view_recipe)},
        "frames": [{"viewRef": f.view_ref, "sha256": f.sha256, "bytes": len(f.png), "width": f.width,
                    "height": f.height, "source": f.source.to_dict()} for f in frames],
        "observation": result.observation.to_dict(),
        "usage": result.usage.to_dict(),
        "timings": {"frameFetchS": round(fetched - started, 3), "reviewS": round(done - fetched, 3)},
        "budgetAfter": {"allowed": budget.allowed, "used": budget.used},
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", required=True, help="project runtime base URL")
    parser.add_argument("--spec", required=True, type=Path, help="review spec JSON (domain, sources, views, task, criteria, preserve)")
    parser.add_argument("--task-class", required=True, choices=[c.value for c in TaskClass])
    parser.add_argument("--reason", required=True, choices=[r.value for r in ReviewReason])
    parser.add_argument("--polish-reviews", type=int)
    parser.add_argument("--prior", type=Path, help="the previous review result JSON of this loop")
    parser.add_argument("--addressed", default="", help="comma-separated finding ids the repair addressed")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--monitor-dir", type=Path)
    args = parser.parse_args(argv)
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    prior = None if args.prior is None else json.loads(args.prior.read_text(encoding="utf-8"))
    provider, monitor = build_provider(codex=args.codex, model=args.model, timeout_s=args.timeout,
                                       monitor_dir=args.monitor_dir)
    result = run_review(args.runtime, spec, task_class=args.task_class, reason=args.reason, prior=prior,
                        addressed=[item for item in args.addressed.split(",") if item],
                        polish_reviews=args.polish_reviews, provider=provider, monitor=monitor)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
