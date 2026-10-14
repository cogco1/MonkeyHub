"""Every committed task is a valid TaskSpec@paper/1 and still matches its fixture and oracle."""

from __future__ import annotations

import copy
import json

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import fixtures, oracle, taskspec


def _specs():
    return [(path.stem, json.loads(path.read_text(encoding="utf-8"))) for path in taskspec.task_files()]


def test_the_six_smoke_tasks_are_present_and_every_task_is_valid():
    names = [name for name, _ in _specs()]
    assert {"F1-T1-01", "F1-T6-01", "F2-T3-01", "F2-T4-01", "F3-T1-01", "F3-T5-01"} <= set(names)
    for name, spec in _specs():
        assert taskspec.problems(spec) == [], name
        assert spec["task_id"] == name


def test_frozen_fields_match_the_fixtures_and_the_oracle():
    oracle_sha = taskspec.file_sha256(taskspec.LAB / "oracle.py")
    for name, spec in _specs():
        record = taskspec.start_record(spec)
        assert spec["start_state"]["content_digest"] == record.digest, name
        assert spec["start_state"]["exact_base_digest"] == fixtures.bound(record).state_digest, name
        assert (spec["oracle"]["oracle_id"], spec["oracle"]["version"], spec["oracle"]["sha256"]) == (oracle.ORACLE_ID, oracle.ORACLE_VERSION, oracle_sha), name


def test_gold_is_never_the_systems_own_closure():
    """Gold is written from the fixture's physical definition; it is not the record's dependency closure."""

    spec = dict(_specs())["F3-T5-01"]
    record = fixtures.f3_wall_stair()
    closure = {ref.removeprefix("entity:") for ref in record.closure(("parameter:storey_height",)) if ref.startswith("entity:")}
    assert set(spec["gold"]["affected_entities"]) != closure


@pytest.mark.parametrize("break_it, message", [
    (lambda s: s["oracle"].__setitem__("reads_metadata", True), "reads_metadata"),
    (lambda s: s["gold"].__setitem__("expected_verdict", "fine"), "expected_verdict"),
    (lambda s: s["gold"].__setitem__("acceptable_arm_outcomes", ["ship"]), "acceptable_arm_outcomes"),
    (lambda s: s["intended_change"].__setitem__("typed_delta", []), "typed_delta"),
    (lambda s: s["protected_relations"][0]["measure"].__setitem__("check", "looks_right"), "measure.check"),
    (lambda s: s.pop("gold"), "missing gold"),
])
def test_validator_rejects_broken_specs(break_it, message):
    spec = copy.deepcopy(dict(_specs())["F3-T1-01"])
    break_it(spec)
    assert any(message in p for p in taskspec.problems(spec)), taskspec.problems(spec)
