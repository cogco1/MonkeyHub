"""P103: incremental Rhino patch — selection, subset translation, patch plan."""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.adapters.cad_execution import CadExecutionError, RhinoPatchBase, prepare_rhino_three_dm_export
from archflow.adapters.cad_patch import CadPatchError, select_patch_operations
from archflow.adapters.cad_program import translate_to_rhino_python
from archflow.capabilities.element_producers import ProductionContext, produce_rows
from archflow.capabilities.reference_resolver import ReferenceContext
from archflow.compilers.geometry import compile_geometry_program
from tests.test_cad_execution import _binding
from tests.test_element_producers import _grids, _levels, _rows
from tests.test_geometry_compiler import COMMITMENT, _proposal, _state
from tests.test_wall_window_families import _only


def _compile(rows):
    context = ProductionContext(references=ReferenceContext(grids=_grids(), levels=_levels()), published={}, frame_id="world")
    produced = produce_rows(rows, context)
    operations = tuple(replace(op, semantic_binding_ids=("building-binding",)) for e in produced for op in e.operations)
    bindings = tuple(b for e in produced for b in e.bindings)
    datums = tuple(sorted(list(context.published.values()) + list(_levels().datums()), key=lambda d: d.datum_id))
    state = _state()
    proposal = _only(_proposal(state, extra_operations=operations), operations, ())
    result = compile_geometry_program(state, proposal, active_commitment_refs=(COMMITMENT,), interface_datums=datums, datum_bindings=bindings)
    assert result.program is not None, [(i.code.value, i.subject_id, i.detail) for i in result.receipt.issues]
    return result.program


def _capital_rows(**capital_params):
    rows = list(_rows())
    rows[1] = replace(rows[1], params={**rows[1].params, **capital_params})
    return tuple(rows)


class PatchSelectionTests(unittest.TestCase):
    def test_same_program_selects_nothing(self) -> None:
        a = _compile(_rows())
        selection = select_patch_operations(a, a)
        self.assertTrue(selection.empty)
        self.assertEqual(selection.rebuilt_op_ids, ())
        self.assertEqual(len(selection.kept_object_ids), 14)

    def test_a_capital_change_rebuilds_only_the_capitals(self) -> None:
        a = _compile(_rows())
        b = _compile(_capital_rows(half_extent=0.6))
        selection = select_patch_operations(b, a)
        self.assertEqual(selection.rebuilt_op_ids, tuple(f"capitals-west-{k}" for k in range(6)))
        self.assertEqual(selection.delete_object_names, tuple(f"obj-capitals-west-{k}" for k in range(6)))
        self.assertEqual(len(selection.kept_object_ids), 8)
        self.assertEqual(set(selection.reasons.values()), {"digest"})

    def test_a_column_height_change_moves_the_chain_even_where_digests_hold(self) -> None:
        a = _compile(_rows())
        b = _compile(_rows(column_height=7.0))
        selection = select_patch_operations(b, a)
        self.assertEqual(len(selection.rebuilt_op_ids), 14)                       # columns, capitals, entablature, pediment
        self.assertEqual(selection.kept_object_ids, ())
        self.assertIn("obj-pediment-west", selection.changed_object_ids)

    def test_retired_and_added_objects(self) -> None:
        full = _compile(_rows())
        short = _compile(_rows()[:3])
        retired = select_patch_operations(short, full)
        self.assertEqual(retired.retired_object_ids, ("obj-pediment-west",))
        self.assertEqual(retired.delete_object_names, ("obj-pediment-west",))
        self.assertEqual(retired.rebuilt_op_ids, ())
        added = select_patch_operations(full, short)
        self.assertEqual(added.added_object_ids, ("obj-pediment-west",))
        self.assertEqual(added.rebuilt_op_ids, ("pediment-west",))
        self.assertEqual(added.delete_object_names, ())

    def test_identity_only_changes_select_nothing(self) -> None:
        a = _compile(_rows())
        proposal = a.proposal
        bindings = tuple(replace(b, evidence_refs=("evidence:another-record",)) for b in proposal.semantic_bindings)
        a2 = replace(a, proposal=replace(proposal, proposal_id="other-proposal", semantic_bindings=bindings))
        selection = select_patch_operations(a2, a)
        self.assertTrue(selection.empty, selection.reasons)                         # semantics are re-stamped, not rebuilt

    def test_subset_translation_names_only_the_rebuilt_objects(self) -> None:
        b = _compile(_capital_rows(half_extent=0.6))
        translation = translate_to_rhino_python(b, operation_subset=tuple(f"capitals-west-{k}" for k in range(6)))
        self.assertNotIn("_register('obj-columns-west-0'", translation.script)
        self.assertIn("_register('obj-capitals-west-0'", translation.script)
        self.assertEqual(translation.physical_object_ids, tuple(f"obj-capitals-west-{k}" for k in range(6)))
        with self.assertRaises(ValueError):
            translate_to_rhino_python(b, operation_subset=("nowhere",))

    def test_patch_plan_carries_the_selection_and_the_whole_denominator(self) -> None:
        a = _compile(_rows())
        b = _compile(_capital_rows(half_extent=0.6))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            prior = workspace / "prior.3dm"
            prior.write_bytes(b"not a real model")
            binding = _binding(b)
            plan = prepare_rhino_three_dm_export(b, binding=binding, speculative_workspace=workspace, artifact_name="patched.3dm", readback_tolerance=0.003,
                                                 patch=RhinoPatchBase(prior_model_path=prior, prior_program=a))
            self.assertEqual(plan.patch["rebuilt_op_ids"], [f"capitals-west-{k}" for k in range(6)])
            self.assertEqual(len(plan.expected_bounds), 14)                      # the denominator is the whole program
            script = plan.script_path.read_text(encoding="utf-8")
            self.assertIn("File3dm.Read", script)
            self.assertIn("'obj-capitals-west-0'", script)
            self.assertIn("_patch_kept != 8", script)
            self.assertIn("_patch_semantics = json.loads(", script)
            self.assertIn("obj-columns-west-0", script.split("_patch_semantics = json.loads(")[1].split("\n")[0])
            self.assertEqual(plan.to_dict()["patch"]["prior_model_sha256"], plan.patch["prior_model_sha256"])
            (workspace / "same").mkdir()
            with self.assertRaises(CadExecutionError):                              # nothing to patch: same program
                prepare_rhino_three_dm_export(a, binding=_binding(a), speculative_workspace=workspace / "same", artifact_name="same.3dm",
                                              readback_tolerance=0.003, patch=RhinoPatchBase(prior_model_path=prior, prior_program=a))


if __name__ == "__main__":
    unittest.main()
