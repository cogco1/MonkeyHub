"""Operations, bindings and programs as plain namespaces: exactly what CAD translation reads.

The translation tests (test_cad_program.py) build programs this way so a case
states only the fields translation reads; the incremental patch tests
(test_cad_patch.py) use the same builders for their translated subsets.
"""

import json
from types import SimpleNamespace


def op(op_id, kind, outputs, inputs=(), bindings=(), statements=None, **params):
    return SimpleNamespace(
        op_id=op_id,
        kind=SimpleNamespace(value=kind),
        output_object_ids=tuple(outputs),
        input_object_ids=tuple(inputs),
        semantic_binding_ids=tuple(bindings),
        statements=dict(statements or {}),
        parameters=tuple(
            SimpleNamespace(name=name, value_json=json.dumps(value))
            for name, value in sorted(params.items())
        ),
    )


def binding(binding_id, component_id, object_ids, commitments=(), evidence=()):
    return SimpleNamespace(
        binding_id=binding_id,
        component_id=component_id,
        object_ids=tuple(object_ids),
        commitment_refs=tuple(commitments),
        evidence_refs=tuple(evidence),
    )


def program(*operations, bindings=()):
    return SimpleNamespace(
        proposal=SimpleNamespace(
            operations=tuple(operations),
            semantic_bindings=tuple(bindings),
        ),
        operation_order=tuple(item.op_id for item in operations),
    )
