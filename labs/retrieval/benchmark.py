"""Frozen proxy-label experiment. Run from the repository root with -m."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import platform
from statistics import mean
from time import perf_counter

from .retrieval import (Dense, DENSE_MODEL, DENSE_REVISION, EvidenceItem, Hybrid,
                        Lexical, Reranked, RERANK_MODEL, RERANK_REVISION,
                        RetrievalRequest, eligible, retrieve)

HERE = Path(__file__).resolve().parent


def load_fixture():
    raw = json.loads((HERE / "corpus.json").read_text(encoding="utf-8"))
    items = [EvidenceItem(**{**x, "companions": tuple(x.get("companions", ()))})
             for x in raw["items"]]
    queries = json.loads((HERE / "queries.json").read_text(encoding="utf-8"))["queries"]
    return items, queries


def quality(ids, relevant, k):
    relevant, ids = set(relevant), ids[:k]
    if not relevant:
        return {"recall": None, "precision": None, "mrr": None, "ndcg": None}
    hits = [int(i in relevant) for i in ids]
    dcg = sum(h / math.log2(r + 2) for r, h in enumerate(hits))
    ideal = sum(1 / math.log2(r + 2) for r in range(min(k, len(relevant))))
    return {"recall": len(set(ids) & relevant) / len(relevant),
            "precision": sum(hits) / k,
            "mrr": next((1 / (r + 1) for r, h in enumerate(hits) if h), 0),
            "ndcg": dcg / ideal}


def measure(query, result, items, elapsed):
    by_id = {x.id: x for x in items}
    request = RetrievalRequest(**query["request"])
    ids = [i for i, _ in result.ranked]
    context = json.loads(result.context) if result.context else []
    supplied = {x["id"] for x in context}
    relevant = set(query["relevant"])
    companions = set(query["companions"])
    wrong = lambda i: not eligible(request, by_id[i])
    irrelevant_bytes = sum(len(x["text"].encode("utf-8")) for x in context if x["id"] not in relevant)
    texts = [x["text"] for x in context]
    duplicate_bytes = sum(len(t.encode("utf-8")) for n, t in enumerate(texts) if t in texts[:n])
    row = {"query": query["id"], "corpus": request.corpus,
           "rank_limit": request.limit, "max_bytes": request.max_bytes,
           **quality(ids, relevant, request.limit),
           "principal_hit": int(bool(set(ids) & set(query["principal"]))) if relevant else None,
           "numeric_principal_hit": int(bool(set(ids) & set(query["principal"]))) if query.get("numeric_rule") else None,
           "companion_recall": len(companions & supplied) / len(companions) if companions else None,
           "complete_evidence": int(relevant <= supplied) if relevant else None,
           "wrong_scope_rank_count": sum(wrong(i) for i in ids),
           "wrong_scope_context_count": sum(wrong(i) for i in supplied),
           "project_leak_count": sum(by_id[i].project != request.project for i in supplied),
           "bytes": len(result.context), "irrelevant_text_bytes": irrelevant_bytes,
           "duplicate_text_bytes": duplicate_bytes, "milliseconds": elapsed * 1000,
           "known_missing": query["known_missing"],
           "missing_abstention": int(result.status.startswith("insufficient:")) if query["known_missing"] else None,
           "status": result.status, "missing": list(result.missing),
           "ranking": [{"id": i, "score": s} for i, s in result.ranked],
           "context_ids": [x["id"] for x in context],
           "reopen_ids": [x["id"] for x in result.reopen]}
    return row


def aggregate(rows):
    keys = ("recall", "precision", "mrr", "ndcg", "principal_hit", "numeric_principal_hit", "companion_recall",
            "complete_evidence", "bytes", "irrelevant_text_bytes", "duplicate_text_bytes",
            "milliseconds", "missing_abstention")
    out = {key: mean(values) if (values := [r[key] for r in rows if r[key] is not None]) else None
           for key in keys}
    out["denominators"] = {key: sum(r[key] is not None for r in rows) for key in keys}
    out.update({key: sum(r[key] for r in rows) for key in
                ("wrong_scope_rank_count", "wrong_scope_context_count", "project_leak_count")})
    out["insufficient_count"] = sum(r["status"].startswith("insufficient:") for r in rows)
    out["query_count"] = len(rows)
    return out


def run(cache_dir, *, lexical_only=False):
    started_at = datetime.now(timezone.utc).isoformat()
    items, queries = load_fixture()
    methods = {"lexical": Lexical()}
    setup, unavailable = {}, {}
    if not lexical_only:
        started = perf_counter()
        try:
            import torch
            torch.manual_seed(0)
            torch.set_num_threads(4)
            methods["dense"] = Dense(items, cache_dir)
            setup["dense_load_and_index_seconds"] = perf_counter() - started
            methods["hybrid"] = Hybrid(methods["dense"])
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            unavailable.update({m: f"{type(exc).__name__}: {exc}" for m in ("dense", "hybrid", "hybrid_reranker")})
        if "hybrid" in methods:
            started = perf_counter()
            try:
                methods["hybrid_reranker"] = Reranked(methods["hybrid"], cache_dir)
                setup["reranker_load_seconds"] = perf_counter() - started
            except (ImportError, OSError, ValueError, RuntimeError) as exc:
                unavailable["hybrid_reranker"] = f"{type(exc).__name__}: {exc}"
    else:
        unavailable = {m: "not requested (--lexical-only)" for m in ("dense", "hybrid", "hybrid_reranker")}
    rows = []
    # Same queries and k, paired policies. Raw ranking is never a safe consumer path.
    policies = {"isolated": (False, False, 12288), "filtered": (True, False, 12288),
                "companions": (True, True, 12288), "companions_1024": (True, True, 1024)}
    for name, ranker in methods.items():
        method_rows = []
        try:
            for policy, (metadata, expand, budget) in policies.items():
                for query in queries:
                    measured_query = {**query, "request": {**query["request"], "max_bytes": budget}}
                    request = RetrievalRequest(**measured_query["request"])
                    start = perf_counter()
                    result = retrieve(request, items, ranker, metadata=metadata, expand=expand)
                    method_rows.append({"method": name, "policy": policy,
                                        **measure(measured_query, result, items, perf_counter() - start)})
        except (OSError, ValueError, RuntimeError) as exc:
            unavailable[name] = f"runtime failure; no partial comparison: {type(exc).__name__}: {exc}"
        else:
            rows.extend(method_rows)
    groups = defaultdict(list)
    for row in rows:
        for corpus in (row["corpus"], "all"):
            groups[f'{row["method"]}/{row["policy"]}/{corpus}'].append(row)
    # Scores are rank signals, not probabilities. Inspect every top-one false positive.
    qmap = {q["id"]: q for q in queries}
    failures = [{"method": r["method"], "policy": r["policy"], "query": r["query"],
                 "top": r["ranking"][0], "expected": qmap[r["query"]]["relevant"],
                 "reason": "known missing" if r["known_missing"] else
                 "scope mismatch" if not eligible(RetrievalRequest(**qmap[r["query"]]["request"]),
                                                      next(x for x in items if x.id == r["ranking"][0]["id"]))
                 else "proxy relevance mismatch"}
                for r in rows if r["ranking"] and r["ranking"][0]["id"] not in qmap[r["query"]]["relevant"]]
    packages = {}
    for package in ("numpy", "torch", "sentence-transformers", "transformers"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    return {
        "started_at_utc": started_at,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "label_origin": "agent-authored proxy judgments; no human audit or held-out set",
        "corpus_sha256": hashlib.sha256((HERE / "corpus.json").read_bytes()).hexdigest(),
        "queries_sha256": hashlib.sha256((HERE / "queries.json").read_bytes()).hexdigest(),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "processor": platform.processor(), "torch_threads": 4 if not lexical_only else None,
                        "device": "CPU", "packages": packages,
                        "uncontrolled_load": "Concurrent development, OCCT and provider experiments may run on this host; not isolated or measured."},
        "models": {"dense": [DENSE_MODEL, DENSE_REVISION], "reranker": [RERANK_MODEL, RERANK_REVISION]},
        "setup": setup, "unavailable": unavailable,
        "cost": {"external_inference_requests": 0, "external_inference_usd": 0,
                 "hardware_energy_usd": None, "model_download_bytes": None,
                 "note": "Local CPU inference. No API charge; energy/amortization/network cost not measured."},
        "extraction": "frozen inspected excerpts, not an extraction benchmark; source coverage is partial",
        "generation": {"status": "not_run", "correctness": None, "condition_preservation": None,
                       "unsupported_strengthening": None, "contradiction_errors": None,
                       "note": "No generated answer exists in this run. Retrieval metrics cannot score downstream reasoning."},
        "summary": {key: aggregate(value) for key, value in groups.items()},
        "top_one_false_positives": failures, "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True, help="Explicit external model cache")
    parser.add_argument("--output", type=Path, required=True, help="Explicit experiment output JSON")
    parser.add_argument("--lexical-only", action="store_true")
    args = parser.parse_args()
    # No project data is created. The caller chooses all cache/output destinations.
    result = run(args.cache_dir, lexical_only=args.lexical_only)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for key, row in result["summary"].items():
        if key.endswith("/all"):
            print(key, json.dumps({k: row[k] for k in ("recall", "companion_recall", "bytes", "milliseconds")}))
    if result["unavailable"]:
        print("UNAVAILABLE", json.dumps(result["unavailable"]))


if __name__ == "__main__":
    main()
