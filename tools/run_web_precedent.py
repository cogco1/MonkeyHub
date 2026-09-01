#!/usr/bin/env python3
"""P067: snapshot live web precedent, adopt quoted facts, enrich the policy.

Pipeline (three gates between the web and generation):
snapshot (no authority) -> quoted facts (harness-marked, span-bound) ->
typed adoption -> build-policy constraints with a full provenance chain.
The enriched authoring context is persisted into a fresh run of the live
monument project; raw snapshot text never enters any prompt.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archflow.adapters.web_evidence import fetch_web_evidence  # noqa: E402
from archflow.capabilities.precedent import (  # noqa: E402
    PrecedentAdoption,
    PrecedentFact,
    compile_precedent_constraints,
)
from archflow.project import (  # noqa: E402
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)
from archflow.state.build_policy import (  # noqa: E402
    ConstructabilityTopic,
    PolicyConstraintStrength,
)
from tools.projects.web_precedent.support import (  # noqa: E402
    load_rebased_authoring_context,
)

sys.path.insert(0, str(ROOT / "tools"))
from _probe_paths import resolve_probe_root  # noqa: E402

PROJECT_ID = "p066-live-monument"
URL = "https://en.wikipedia.org/wiki/Pantheon,_Rome"

FACT_SPECS = (
    {
        "fact_id": "portico-pediment-pitched",
        "needle": (
            "The building is round in plan, except for the portico with "
            "large granite Corinthian columns (eight in the first rank and "
            "two groups of four behind) under a pediment"
        ),
        "statement": (
            "The portico must be crowned by a triangular pediment carrying "
            "a pitched (gabled) roof form; a flat slab roof does not "
            "satisfy the adopted precedent."
        ),
        "topic": ConstructabilityTopic.SUPPORT,
        "strength": PolicyConstraintStrength.HARD,
    },
    {
        "fact_id": "portico-octastyle-colonnade",
        "needle": (
            "large granite Corinthian columns (eight in the first rank and "
            "two groups of four behind)"
        ),
        "statement": (
            "The portico colonnade is octastyle: eight columns in the "
            "first rank with two groups of four columns behind them."
        ),
        "topic": ConstructabilityTopic.SUPPORT,
        "strength": PolicyConstraintStrength.HARD,
    },
    {
        "fact_id": "rotunda-interior-43-metres",
        "needle": (
            "The height to the oculus and the diameter of the interior "
            "circle are the same, 43.3 metres (142 ft) , so the whole "
            "interior would fit exactly within a cube"
        ),
        "statement": (
            "The rotunda interior diameter and the height to the oculus "
            "are each 43.3 metres; the massing must be sized to this "
            "monument-scale precedent within the authorized site "
            "envelope, filling the declared footprint rather than "
            "placing a token model on it."
        ),
        "topic": ConstructabilityTopic.SUPPORT,
        "strength": PolicyConstraintStrength.HARD,
    },
    {
        "fact_id": "dome-coffered-oculus",
        "needle": (
            "a coffered concrete dome made from Roman concrete (also "
            "called opus caementicium ), with a central opening"
        ),
        "statement": (
            "The rotunda dome is coffered on its interior and pierced by "
            "one central oculus opening at the crown."
        ),
        "topic": ConstructabilityTopic.SUPPORT,
        "strength": PolicyConstraintStrength.SOFT,
    },
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True)
    parser.add_argument("--run-id", default="live-002")
    parser.add_argument("--source-run-id", default="live-001")
    args = parser.parse_args(argv)

    repository = FilesystemProjectRepository.open(resolve_probe_root(PROJECT_ID))
    try:
        run = repository.load_run(args.run_id)
    except Exception:
        run = repository.create_run(args.run_id)
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=args.run_id
    )
    context, source_context_ref = load_rebased_authoring_context(
        repository,
        source_run_id=args.source_run_id,
        target_run=run,
    )
    print("source context:", source_context_ref.uri[:100])

    snapshot = fetch_web_evidence(URL, retrieved_at=args.now)
    snapshot_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="web-evidence-snapshot",
        payload=snapshot.to_dict(),
    )
    print("snapshot:", snapshot_ref.uri[:100])

    facts = []
    for spec in FACT_SPECS:
        start = snapshot.text.find(spec["needle"])
        if start < 0:
            raise SystemExit(
                f"{spec['fact_id']}: quote not found in the retained "
                "snapshot; stopping rather than paraphrasing"
            )
        fact = PrecedentFact(
            fact_id=spec["fact_id"],
            statement=spec["statement"],
            quote=spec["needle"],
            quote_start=start,
            quote_end=start + len(spec["needle"]),
            snapshot_ref=snapshot_ref.uri,
            snapshot_text_sha256=snapshot.text_sha256,
            annotator="harness:claude-fable-5",
            annotator_is_harness=True,
            topic=spec["topic"],
            strength=spec["strength"],
        )
        fact.require_quote_in(snapshot.text)
        facts.append(fact)
    facts.sort(key=lambda fact: fact.fact_id)
    adoption = PrecedentAdoption(
        adoption_id="pantheon-precedent-adoption-001",
        authority_id="authority.user",
        adopted_at=args.now,
        facts=tuple(facts),
    )
    adoption_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="precedent-adoption",
        payload=adoption.to_dict(),
    )
    print("adoption:", adoption_ref.uri[:100])

    policy = context.build_policy
    constraints = compile_precedent_constraints(
        adoption,
        adoption_ref=adoption_ref.uri,
        compiler_id=policy.compiler_id,
        base_state_sha256=policy.policy_provenance.base_state_sha256,
    )
    enriched_policy = replace(
        policy,
        constraints=tuple(
            sorted(
                (*policy.constraints, *constraints),
                key=lambda item: item.constraint_id,
            )
        ),
        evidence_refs=tuple(
            sorted({*policy.evidence_refs, adoption_ref.uri})
        ),
    )
    enriched = replace(context, build_policy=enriched_policy)
    context_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="production-authoring-context",
        payload=enriched.to_dict(),
    )
    print("enriched context:", context_ref.uri[:100])
    print(
        "constraints:",
        [item.constraint_id for item in enriched_policy.constraints],
    )
    repository.verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
