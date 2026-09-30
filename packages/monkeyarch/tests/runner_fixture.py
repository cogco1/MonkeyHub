"""The project runner demo block's evidence and component row.

A copy of ``EVIDENCE`` and ``_component`` from the integration suite's
runner_support.py (tests/integration/runner_support.py), for the State Record test
that rebuilds the runner's schematic pack from a record.
"""

EVIDENCE = "evidence:demo-survey"


def _component(cid, parent, kind, intent, volumes=()):
    return {"schema": "DesignComponent@1", "component_id": cid, "parent_component_id": parent, "semantic_kind": kind, "intent": intent,
            "maturity": "schematic", "revision": 1, "volume_ids": list(volumes), "unresolved_child_roles": [], "source_refs": [EVIDENCE]}
