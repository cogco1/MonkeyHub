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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archflow.adapters.model_provider import (  # noqa: E402
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.adapters.web_evidence import fetch_web_evidence  # noqa: E402
from archflow.capabilities.precedent import PrecedentAdoption  # noqa: E402
from archflow.capabilities.research import (  # noqa: E402
    PrecedentQuery,
    decode_research_json,
    extract_windows,
    parse_research_output,
    research_prompt,
)
from archflow.production import (  # noqa: E402
    InvocationEvidenceCollector,
    activate_codex_agent_cli_provider,
)
from archflow.project import (  # noqa: E402
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)
from archflow.state.geometry_program import digest_value  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
from _probe_paths import resolve_probe_root  # noqa: E402

PROJECT_ID = "p066-live-monument"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True)
    parser.add_argument("--run-id", default="research-001")
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--decision-ref", action="append", required=True)
    parser.add_argument("--term", action="append", required=True)
    parser.add_argument("--url", action="append", required=True)
    parser.add_argument("--codex", default="codex.cmd")
    args = parser.parse_args(argv)

    repository = FilesystemProjectRepository.open(resolve_probe_root(PROJECT_ID))
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

    snapshot = fetch_web_evidence(args.url[0], retrieved_at=args.now)
    snapshot_ref = put("web-evidence-snapshot", snapshot.to_dict())
    windows = extract_windows(snapshot.text, query.search_terms)
    print(f"  windows: {len(windows)}")
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
        request_id=f"research-{query.query_id}",
        phase=ModelPhase.RESEARCH,
        checkpoint_digest=snapshot.text_sha256,
        context_digest=digest_value(prompt),
        payload=prompt,
    )
    receipt = asyncio.run(provider.invoke(request))
    envelopes = collector.since(0)
    put(
        f"research-invocation-{query.query_id}",
        {
            "schema": "P070ResearchInvocation@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "query_id": query.query_id,
            "provider_envelopes": [item.to_dict() for item in envelopes],
            "status": receipt.status.value,
            "canonical_write_authority": False,
        },
    )
    if receipt.status is not ModelInvocationStatus.SUCCESS:
        print("RESEARCH FAILED:", receipt.error_code, receipt.message)
        return 2
    output = receipt.output
    if isinstance(output, str):
        output = decode_research_json(output)
    facts, rejections = parse_research_output(
        output,
        query=query,
        snapshot_ref=snapshot_ref.uri,
        snapshot_text=snapshot.text,
        snapshot_text_sha256=snapshot.text_sha256,
        annotator=f"model:{receipt.model_id}",
    )
    put(
        f"research-candidates-{query.query_id}",
        {
            "schema": "P070ResearchCandidates@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "query_id": query.query_id,
            "candidates": [fact.to_dict() for fact in facts],
            "rejected_candidates": list(rejections),
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
