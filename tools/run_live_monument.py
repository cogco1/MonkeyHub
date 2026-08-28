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

sys.path.insert(0, str(ROOT / "tools"))
from _probe_paths import resolve_probe_root  # noqa: E402

PROJECT_ID = "p066-live-monument"
DEFAULT_RUN_ID = "live-001"


def _stage0_contract(repository, run):
    """Project-authored white-model contract for the monument root.

    Ranges come from the retained authoring context (site envelope and
    program) and the adopted precedent facts; every field cites its
    provenance. The framework supplies no value.
    """

    import glob as _glob

    from archflow.capabilities.declaration import (
        DeclarationField,
        DeclarationKind,
        DeclarationQuadrant,
        GeometryCheck,
        StageDeclarationContract,
    )

    def adoption_refs(pattern):
        base = resolve_probe_root(PROJECT_ID) / "runs"
        hits = sorted(_glob.glob(str(base / "*" / "records" / pattern)))
        return tuple(
            f"project://{PROJECT_ID}/runs/{Path(item).parents[1].name}"
            f"/records/{Path(item).name}"
            for item in hits[-1:]
        )

    site_ref = ("context:site.authorized_envelope",)
    program_ref = ("context:program.footprint_range",)
    axis_refs = adoption_refs(
        "precedent-adoption-front-elevation-governing-rules-*.json"
    ) or site_ref
    span_refs = adoption_refs("precedent-adoption-*.json") or program_ref
    fields = (
        DeclarationField(
            field_id="footprint-depth-m",
            quadrant=DeclarationQuadrant.DIMENSIONS,
            kind=DeclarationKind.NUMBER,
            unit="m",
            minimum=50.0,
            maximum=64.0,
            geometry_check=GeometryCheck.FOOTPRINT_DEPTH,
            source_refs=site_ref,
            statement="overall footprint depth of the massing",
        ),
        DeclarationField(
            field_id="footprint-fill-ratio",
            quadrant=DeclarationQuadrant.DIMENSIONS,
            kind=DeclarationKind.RATIO,
            unit=None,
            minimum=0.55,
            maximum=1.0,
            geometry_check=GeometryCheck.FOOTPRINT_FILL_RATIO,
            source_refs=program_ref,
            statement="massing must fill the claimed footprint",
        ),
        DeclarationField(
            field_id="footprint-width-m",
            quadrant=DeclarationQuadrant.DIMENSIONS,
            kind=DeclarationKind.NUMBER,
            unit="m",
            minimum=40.0,
            maximum=49.0,
            geometry_check=GeometryCheck.FOOTPRINT_WIDTH,
            source_refs=site_ref,
            statement="overall footprint width of the massing",
        ),
        DeclarationField(
            field_id="orientation-axis",
            quadrant=DeclarationQuadrant.SITE,
            kind=DeclarationKind.NUMBER,
            unit="deg",
            minimum=0.0,
            maximum=359.0,
            geometry_check=GeometryCheck.NONE,
            source_refs=axis_refs,
            statement="azimuth of the primary entrance axis",
        ),
        DeclarationField(
            field_id="overall-height-m",
            quadrant=DeclarationQuadrant.DIMENSIONS,
            kind=DeclarationKind.NUMBER,
            unit="m",
            minimum=30.0,
            maximum=50.0,
            geometry_check=GeometryCheck.OVERALL_HEIGHT,
            source_refs=site_ref,
            statement="overall height of the massing above the base",
        ),
        DeclarationField(
            field_id="portico-width-m",
            quadrant=DeclarationQuadrant.DIMENSIONS,
            kind=DeclarationKind.NUMBER,
            unit="m",
            minimum=24.0,
            maximum=34.0,
            geometry_check=GeometryCheck.NONE,
            source_refs=axis_refs,
            statement="width of the colonnaded portico front",
        ),
        DeclarationField(
            field_id="primary-span-m",
            quadrant=DeclarationQuadrant.DIMENSIONS,
            kind=DeclarationKind.NUMBER,
            unit="m",
            minimum=40.0,
            maximum=47.0,
            geometry_check=GeometryCheck.PRIMARY_SPAN,
            source_refs=span_refs,
            statement="clear primary span of the domed hall",
        ),
    )
    return StageDeclarationContract(
        stage="schematic",
        fields=fields,
        tolerance_ratio=0.08,
    )


def _decision_basis(contract):
    """Select exactly the contract fields' basis shards from the index."""

    import glob as _glob
    import json as _json

    from archflow.capabilities.declaration import select_decision_basis

    shards = {}
    for path in _glob.glob(
        str(resolve_probe_root(PROJECT_ID) / "index" / "basis" / "*.json")
    ):
        name = Path(path).name
        if name.startswith("_"):
            continue
        shard = _json.loads(Path(path).read_text(encoding="utf-8"))
        shards[shard["decision_ref"]] = shard
    if not shards:
        return None, None
    return select_decision_basis(contract, shards)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex.cmd")
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--declare", action="store_true")
    parser.add_argument("--max-output-tokens", type=int, default=8_192)
    args = parser.parse_args(argv)

    root = resolve_probe_root(PROJECT_ID)
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
    if args.max_output_tokens != 8_192:
        # A larger output budget is a distinct frozen profile: build the
        # provider directly so the budget is explicit, never silent.
        from archflow.adapters.model_provider import (
            create_codex_cli_model_provider,
        )
        from archflow.production import activate_provider_from_spec

        raw_provider = create_codex_cli_model_provider(
            executable=args.codex,
            model_id="gpt-5.6-sol",
            version="codex-cli-0.145.0",
            timeout_seconds=300.0,
            reasoning_effort="low",
            max_output_tokens=args.max_output_tokens,
        )
        provider = activate_provider_from_spec(
            raw_provider,
            spec=raw_provider.spec,
            responsibility_id="model.production-root",
            contract_owner_id="archflow.production-root",
            verification_evidence_refs=(context_ref.uri,),
            envelope_observer=collector.observe,
        )
    else:
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
    declaration_contract = None
    decision_basis = None
    if args.declare:
        declaration_contract = _stage0_contract(repository, run)
        repository.put_json(
            run=run,
            destination=destination,
            record_kind="stage-declaration-contract",
            payload={
                "schema": "P068StageDeclarationContractRecord@1",
                "project_id": run.project_id,
                "run_id": run.run_id,
                "contract": declaration_contract.to_dict(),
                "canonical_write_authority": False,
            },
        )
        decision_basis, basis_metrics = _decision_basis(
            declaration_contract
        )
        if decision_basis is not None:
            repository.put_json(
                run=run,
                destination=destination,
                record_kind="decision-basis-selection",
                payload={
                    "schema": "P076DecisionBasisSelection@1",
                    "project_id": run.project_id,
                    "run_id": run.run_id,
                    "selection": decision_basis,
                    "metrics": basis_metrics,
                    "note": (
                        "prompt receives only the shards of this "
                        "contract's fields; the store stays out"
                    ),
                    "canonical_write_authority": False,
                },
            )
            print(
                "  decision basis: "
                f"{basis_metrics['facts_selected']}/"
                f"{basis_metrics['facts_total']} facts, "
                f"{basis_metrics['chars_selected']}/"
                f"{basis_metrics['chars_total']} chars injected"
            )
        print(
            "  declaration contract: "
            f"{len(declaration_contract.fields)} fields @ stage "
            f"{declaration_contract.stage}"
        )
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
        declaration_contract=declaration_contract,
        decision_basis=decision_basis,
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
        "provider_profile_id": (
            "codex-agent-cli-gpt-5-6-sol-low-win-cmd-300s"
            + (f"-out{args.max_output_tokens}"
               if args.max_output_tokens != 8_192 else "")
        ),
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
