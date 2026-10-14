"""X2 harness pieces that make no model call: the text renderer, the guard-free state, answers and their execution mapping."""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import fixtures, x2


def test_render_writes_nested_data_as_indented_text():
    text = x2.render({"a": 1, "empty": [], "b": {"c": "x: y", "d": [1, 2]}, "e": [{"f": True}]})
    assert text.splitlines() == ["a: 1", "b:", '  c: "x: y"', "  d: [1, 2]", "e:", "  - f: true"]


def test_without_guards_removes_obligations_and_bounding_relations_everywhere():
    state = {"record": {"obligations": [{"obligation_id": "k1", "subject_refs": ["parameter:clear", "parameter:min_clear"]}],
                        "parameters": [{"key": "clear"}, {"key": "min_clear"}],
                        "relations": [{"relation_id": "p1", "validator": {"check_kind": "support_contact"}},
                                      {"relation_id": "j1", "validator": {"check_kind": "clearance_interval"}}]},
             "context_pack": {"context": {"relationships": [{"relation_id": "j1"}, {"relation_id": "p1"}],
                                          "readOnlyRefs": ["relation:j1", "relation:p1", "parameter:min_clear"]}}}
    out = x2.without_guards(state)
    assert out["record"]["obligations"] == []
    assert out["record"]["parameters"] == [{"key": "clear"}]
    assert [r["relation_id"] for r in out["record"]["relations"]] == ["p1"]
    assert out["context_pack"]["context"] == {"relationships": [{"relation_id": "p1"}], "readOnlyRefs": ["relation:p1"]}


def test_an_answer_is_read_from_fenced_or_bare_json():
    assert x2.parse_answer('```json\n{"verdict": "held"}\n```') == {"verdict": "held"}
    assert x2.parse_answer('Here: {"verdict": "refuse", "changes": null}') == {"verdict": "refuse", "changes": None}
    assert x2.parse_answer("no json") is None


def test_a_derived_value_must_follow_from_the_inputs(tmp_path):
    inputs = {"record": fixtures.f1_portico()}
    task = {"task_id": "F1-T1"}
    wrong = {"verdict": "held", "changes": {"column_count": 4, "column_spacing": 2.0}}
    assert x2.execute(wrong, "D", "F1", task, inputs, tmp_path / "t")["why"] == "DERIVED_CONFLICT: column_spacing"
    only = {"verdict": "held", "changes": {"column_spacing": 3.4}}
    assert x2.execute(only, "D", "F1", task, inputs, tmp_path / "t")["why"] == "DERIVED_CONFLICT: column_spacing"


def test_changes_map_history_names_and_refuse_unknown_ones():
    record = fixtures.f1_portico()
    assert x2.map_changes({"portico.column_diameter": 0.81}, "A", "F1", record) == ({"column_radius": 0.405}, None)
    assert x2.map_changes({"column_count": 4}, "D", "F1", record) == ({"column_count": 4.0}, None)
    assert x2.map_changes({"column_count": 4}, "B", "F1", record)[1] == "UNKNOWN_PARAMETER: column_count"
    assert x2.map_changes({"capital_order": "corinthian"}, "D", "F1", record)[1] == "NOT_A_NUMBER: capital_order"


def test_a_run_counts_only_its_own_rows(tmp_path):
    ledger = x2.Ledger(tmp_path / "ledger.jsonl")
    ledger.add({"part": "pilot", "out": str(tmp_path / "run-a" / "turn-1"), "cost_usd": 1.0})
    ledger.add({"part": "pilot", "out": str(tmp_path / "run-ab" / "turn-1"), "cost_usd": 2.0})   # a sibling, not a child
    assert ledger.spent_by(tmp_path / "run-a") == 1.0 and ledger.spent("pilot") == 3.0


def test_a_resumed_turn_is_priced_by_what_it_added():
    result = {"total_cost_usd": 7.59, "usage": {"input_tokens": 8, "output_tokens": 10}}
    alone, _ = x2.price(result)
    resumed, priced = x2.price(result, cli_offset=6.93)
    assert alone == 7.59                                            # without the offset the session's running total wins
    assert abs(resumed - 0.66) < 1e-9 and priced["cli_total_cost_usd"] == 7.59 and priced["cli_offset_usd"] == 6.93
