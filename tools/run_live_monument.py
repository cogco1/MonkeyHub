#!/usr/bin/env python3
"""P066: run the live-model monument root step through the formal runtime.

The monument authoring context (program, site, policy, commitment) is the
exact context proven by the P065 scripted derivation, persisted as project
records; the live Codex CLI provider then authors both options, the
selection, and the neutral geometry itself. Every attempt retains exact
P053 envelopes; a failed or rejected attempt is terminal evidence and is
never silently retried.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from archflow.capabilities.geometry_proposal import (  # noqa: E402
    GeometryProposalProviderIdentity,
)
from archflow.production import (  # noqa: E402
    InvocationEvidenceCollector,
    activate_codex_agent_cli_provider,
)
from archflow.project import (  # noqa: E402
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    bootstrap_raw_request_project,
)
from archflow.runtime.production_compiler import (  # noqa: E402
    ProductionRootCompiler,
)
from archflow.runtime.production_runtime import (  # noqa: E402
    ProductionRuntimeStepFailed,
    run_or_resume_production_step,
)
from tests.integration.test_monument_derivation import (  # noqa: E402
    PROMPT,
    _monument_context,
)
from tests.test_production_root_compiler import _rebase_context  # noqa: E402

PROJECT_ID = "p066-live-monument"
DEFAULT_RUN_ID = "live-001"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex.cmd")
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args(argv)

    root = ROOT / "probes" / PROJECT_ID
    if not (root / "project.json").exists():
        bootstrap_raw_request_project(
            root,
            project_id=PROJECT_ID,
            prompt=PROMPT,
            run_id=args.run_id,
            synthetic_test=False,
        )
    repository = FilesystemProjectRepository.open(root)
    try:
        run = repository.load_run(args.run_id)
    except Exception:
        run = repository.create_run(args.run_id)
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=args.run_id
    )
    inputs = repository.list_json(
        run=run, destination=PersistenceDestination(PersistenceArea.INPUT)
    )
    raw_request = next(
        ref for ref in inputs if "raw-request" in ref.relative_path
    )
    from archflow.runtime.production_runtime import (
        ProductionAuthoringContext,
    )

    existing = [
        ref
        for ref in repository.list_json(run=run, destination=destination)
        if "production-authoring-context" in ref.relative_path
    ]
    if existing:
        # A persisted context (possibly precedent-enriched) is authoritative.
        context_ref = existing[0]
        context = ProductionAuthoringContext.from_dict(
            repository.load_json(context_ref)
        )
    else:
        context = _rebase_context(_monument_context(), run)
        context_ref = repository.put_json(
            run=run,
            destination=destination,
            record_kind="production-authoring-context",
            payload=context.to_dict(),
        )
    collector = InvocationEvidenceCollector()
    provider = activate_codex_agent_cli_provider(
        executable=args.codex,
        model_id="gpt-5.6-sol",
        version="codex-cli-0.145.0",
        responsibility_id="model.production-root",
        contract_owner_id="archflow.production-root",
        verification_evidence_refs=(context_ref.uri,),
        timeout_seconds=300.0,
        reasoning_effort="low",
        envelope_observer=collector.observe,
    )
    active = provider.router.state("model.production-root").active_provider
    if active is None:
        raise SystemExit("codex provider failed activation")
    compiler = ProductionRootCompiler(
        repository=repository,
        context_ref=context_ref,
        context=context,
        provider=provider,
        evidence_collector=collector,
        geometry_provider_identity=GeometryProposalProviderIdentity(
            provider_id=active.provider_id,
            model_id="gpt-5.6-sol",
            provider_version=active.version,
            provider_fingerprint=active.fingerprint,
        ),
    )
    started = time.time()
    envelope = {
        "schema": "P066LiveRootEnvelope@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "base": {
            "project_id": run.base.project_id,
            "version": run.base.version,
            "state_sha256": run.base.require_digest(),
        },
        "attempt_index": args.attempt,
        "provider_profile_id": "codex-agent-cli-gpt-5-6-sol-low-win-cmd-300s",
        "provider_fingerprint": active.fingerprint,
        "scripted_fallback_used": False,
        "canonical_write_authority": False,
    }
    try:
        result = asyncio.run(
            run_or_resume_production_step(
                repository,
                run=run,
                raw_request=raw_request,
                prompt=PROMPT,
                step_id=f"live-monument-root-{args.attempt:03d}",
                compiler=compiler,
            )
        )
    except ProductionRuntimeStepFailed as failure:
        attempt = failure.args[0] if failure.args else None
        envelope.update(
            {
                "status": "pipeline_rejected",
                "error_code": getattr(attempt, "error_code", "unknown"),
                "message": str(failure.__cause__ or failure)[:2000],
                "observed_wall_clock_ms": int(
                    (time.time() - started) * 1000
                ),
                "provider_invocation_count": len(collector.since(0)),
            }
        )
        repository.put_json(
            run=run,
            destination=destination,
            record_kind=f"p066-live-root-envelope-{args.attempt:03d}",
            payload=envelope,
        )
        print("LIVE ROOT FAILED (typed failure retained)")
        print(envelope["error_code"], "|", envelope["message"][:300])
        return 2
    envelope.update(
        {
            "status": "compiled",
            "resumed": result.resumed,
            "observed_wall_clock_ms": int((time.time() - started) * 1000),
            "provider_invocation_count": len(collector.since(0)),
            "intent_digest": result.archive.intent_digest,
            "record_refs": [item.ref.uri for item in result.archive.records],
        }
    )
    repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p066-live-root-envelope-{args.attempt:03d}",
        payload=envelope,
    )
    print(
        f"LIVE ROOT COMPILED calls={envelope['provider_invocation_count']} "
        f"wall_ms={envelope['observed_wall_clock_ms']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
