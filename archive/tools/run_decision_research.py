#!/usr/bin/env python3
"""P070: run one decision-scoped research loop with a live model reader.

Pipeline: snapshot the named pages (no authority) -> cut bounded keyword
windows -> one RESEARCH-phase provider call that may output only quoted
fact candidates -> machine span-verification against the full snapshot ->
typed adoption under a named authority -> a calibration record binding
each adopted fact to the exact decisions it calibrates.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archive.archflow.adapters.model_provider import (  # noqa: E402
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archive.archflow.adapters.web_evidence import fetch_web_evidence  # noqa: E402
from archive.archflow.capabilities.precedent import PrecedentAdoption  # noqa: E402
from archive.archflow.research.query import (  # noqa: E402
    PrecedentQuery,
    ResearchError,
    decode_research_json,
    extract_windows,
    parse_research_output,
    research_prompt,
)
from archive.archflow.production.provider_runtime import InvocationEvidenceCollector, activate_codex_agent_cli_provider
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.contracts.canonical import canonical_digest

sys.path.insert(0, str(ROOT / "archive" / "tools"))
from _probe_paths import resolve_probe_root  # noqa: E402



def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True)
    parser.add_argument(
        "--project-id",
        required=True,
        help="target project owning this research (records land "
        "inside it; no default on purpose)",
    )
    parser.add_argument("--run-id", default="research-001")
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--decision-ref", action="append", required=True)
    parser.add_argument("--term", action="append", required=True)
    parser.add_argument("--url", action="append", required=True)
    parser.add_argument("--codex", default="codex.cmd")
    args = parser.parse_args(argv)

    repository = FilesystemProjectRepository.open(
        resolve_probe_root(args.project_id)
    )
    try:
        run = repository.load_run(args.run_id)
    except Exception:
        run = repository.create_run(args.run_id)
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=args.run_id
    )

    def put(kind, payload):
        ref = repository.put_json(
            run=run, destination=destination, record_kind=kind, payload=payload
        )
        print(f"  retained {ref.uri[:110]}")
        return ref

    query = PrecedentQuery(
        query_id=args.query_id,
        question=args.question,
        decision_refs=tuple(sorted(set(args.decision_ref))),
        search_terms=tuple(args.term),
        jurisdiction=None,
        domain_allowlist=(),
    )
    put(f"precedent-query-{query.query_id}", query.to_dict())

    facts_by_id: dict[str, object] = {}
    dropped_duplicates: list[dict] = []
    per_source: list[dict] = []
    all_rejections: list[dict] = []
    for source_index, url in enumerate(args.url):
        source_tag = f"{query.query_id}-s{source_index:02d}"
        snapshot = fetch_web_evidence(url, retrieved_at=args.now)
        snapshot_ref = put("web-evidence-snapshot", snapshot.to_dict())
        try:
            windows = extract_windows(snapshot.text, query.search_terms)
        except ResearchError as exc:
            per_source.append(
                {"url": url, "snapshot_ref": snapshot_ref.uri,
                 "status": "no_windows", "detail": str(exc)[:200]}
            )
            print(f"  [{source_index}] {url}: no windows (honest skip)")
            continue
        print(f"  [{source_index}] {url}: {len(windows)} windows")
        prompt = research_prompt(
            query,
            snapshot_ref=snapshot_ref.uri,
            snapshot_text_sha256=snapshot.text_sha256,
            windows=windows,
        )
        collector = InvocationEvidenceCollector()
        provider = activate_codex_agent_cli_provider(
            executable=args.codex,
            model_id="gpt-5.6-sol",
            version="codex-cli-0.145.0",
            responsibility_id="model.decision-research",
            contract_owner_id="archflow.decision-research",
            verification_evidence_refs=(snapshot_ref.uri,),
            timeout_seconds=300.0,
            reasoning_effort="low",
            envelope_observer=collector.observe,
        )
        request = ModelInvocationRequest.create(
            request_id=f"research-{source_tag}",
            phase=ModelPhase.RESEARCH,
            checkpoint_digest=snapshot.text_sha256,
            context_digest=canonical_digest(prompt),
            payload=prompt,
        )
        receipt = asyncio.run(provider.invoke(request))
        envelopes = collector.since(0)
        put(
            f"research-invocation-{source_tag}",
            {
                "schema": "P070ResearchInvocation@1",
                "project_id": run.project_id,
                "run_id": run.run_id,
                "query_id": query.query_id,
                "source_url": url,
                "provider_envelopes": [
                    item.to_dict() for item in envelopes
                ],
                "status": receipt.status.value,
                "canonical_write_authority": False,
            },
        )
        if receipt.status is not ModelInvocationStatus.SUCCESS:
            per_source.append(
                {"url": url, "snapshot_ref": snapshot_ref.uri,
                 "status": "invocation_failed",
                 "detail": f"{receipt.error_code}: {receipt.message}"[:200]}
            )
            print(f"  [{source_index}] invocation failed (retained)")
            continue
        output = receipt.output
        if isinstance(output, str):
            output = decode_research_json(output)
        try:
            source_facts, rejections = parse_research_output(
                output,
                query=query,
                snapshot_ref=snapshot_ref.uri,
                snapshot_text=snapshot.text,
                snapshot_text_sha256=snapshot.text_sha256,
                annotator=f"model:{receipt.model_id}",
            )
        except ResearchError as exc:
            per_source.append(
                {"url": url, "snapshot_ref": snapshot_ref.uri,
                 "status": "no_surviving_candidates",
                 "detail": str(exc)[:300]}
            )
            print(f"  [{source_index}] zero candidates (honest empty)")
            continue
        all_rejections.extend(rejections)
        kept = 0
        for fact in source_facts:
            if fact.fact_id in facts_by_id:
                dropped_duplicates.append(
                    {"fact_id": fact.fact_id, "url": url,
                     "code": "duplicate_fact_id_across_sources"}
                )
                continue
            facts_by_id[fact.fact_id] = fact
            kept += 1
        per_source.append(
            {"url": url, "snapshot_ref": snapshot_ref.uri,
             "status": "ok", "facts": kept}
        )
    facts = tuple(
        facts_by_id[fact_id] for fact_id in sorted(facts_by_id)
    )
    if not facts:
        put(
            f"research-candidates-{query.query_id}",
            {
                "schema": "P070ResearchCandidates@2",
                "project_id": run.project_id,
                "run_id": run.run_id,
                "query_id": query.query_id,
                "candidates": [],
                "rejected_candidates": all_rejections,
                "sources": per_source,
                "canonical_write_authority": False,
            },
        )
        print("RESEARCH SWEEP EMPTY: no source yielded a surviving fact")
        return 2
    put(
        f"research-candidates-{query.query_id}",
        {
            "schema": "P070ResearchCandidates@2",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "query_id": query.query_id,
            "candidates": [fact.to_dict() for fact in facts],
            "rejected_candidates": all_rejections,
            "dropped_duplicates": dropped_duplicates,
            "sources": per_source,
            "span_verified": True,
            "adoption_authority": False,
            "canonical_write_authority": False,
        },
    )
    adoption = PrecedentAdoption(
        adoption_id=f"{query.query_id}-adoption",
        authority_id="authority.user",
        adopted_at=args.now,
        facts=facts,
    )
    adoption_ref = put(
        f"precedent-adoption-{query.query_id}", adoption.to_dict()
    )
    calibration = {
        "schema": "P070DecisionCalibration@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "query_id": query.query_id,
        "adoption_ref": adoption_ref.uri,
        "calibrations": [
            {
                "decision_ref": decision_ref,
                "facts": [
                    fact.fact_id
                    for fact in facts
                    if decision_ref in fact.decision_refs
                ],
            }
            for decision_ref in query.decision_refs
        ],
        "note": (
            "adopted facts calibrate exactly the named decisions; numeric "
            "range tightening from these statements awaits authority "
            "confirmation before entering a declaration contract"
        ),
        "canonical_write_authority": False,
    }
    put(f"decision-calibration-{query.query_id}", calibration)
    repository.verify()
    print(f"RESEARCH LOOP DONE facts={len(facts)}")
    for fact in facts:
        print(f"   {fact.fact_id} -> {','.join(fact.decision_refs)}")
        print(f"     '{fact.quote[:90]}...'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
