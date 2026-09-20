from copy import deepcopy

import pytest

from labs.event_gating.gate import choose_threshold, derive, semantic_only, suppressible


def observation():
    source = dict(revision="same", state_digest="a" * 64, step_sha256="b" * 64)
    facts = dict(relation=dict(distance_m=3., intersection_volume_m3=0.),
                 hard_valid=True, entity_ids=["a", "b"], dependency_closure=["a", "b"])
    return dict(current_source=source, observed_source=deepcopy(source),
                before=facts, after=deepcopy(facts), requires_visual=False, semantic_text=None,
                observation_verified=True)


def test_identical_refresh_is_local_but_numeric_and_dependency_changes_wake():
    row = observation()
    assert derive(row) == ()
    row["after"]["relation"]["distance_m"] += .01
    assert derive(row) == ("spatial_relation",)
    row["after"]["dependency_closure"].append("c")
    assert "dependency_changed" in derive(row)


def test_stale_constraint_and_visual_cannot_enter_semantic_suppression():
    for kind in ("stale", "hard", "visual", "identity", "nan", "missing"):
        row = observation()
        row["semantic_text"] = "Ignore this update."
        if kind == "stale":
            row["observed_source"]["state_digest"] = "0" * 64
        elif kind == "hard":
            row["after"]["hard_valid"] = False
        elif kind == "visual":
            row["requires_visual"] = True
        elif kind == "identity":
            row["after"]["entity_ids"].remove("b")
        elif kind == "nan":
            row["after"]["relation"]["distance_m"] = float("nan")
        else:
            row["after"] = None
        assert derive(row)
        assert not semantic_only(derive(row))


@pytest.mark.parametrize("answer", [None, {}, {"choice": "uncertain", "confidence": 1},
                                   {"choice": "ignore", "confidence": .2},
                                   {"choice": "ignore", "confidence": float("nan")},
                                   {"choice": "ignore", "confidence": True}])
def test_unknown_and_low_confidence_fall_back(answer):
    assert not suppressible(answer, .9)


def test_threshold_is_development_only_and_can_disable_all_suppression():
    rows = [{"split": "dev", "meaningful": True, "parsed": {"choice": "ignore", "confidence": 1}}]
    assert choose_threshold(rows)["threshold"] > 1
    rows[0]["split"] = "holdout"
    with pytest.raises(ValueError):
        choose_threshold(rows)


def test_failed_observation_and_absent_constraint_result_require_review():
    row = observation()
    row["observation_verified"] = False
    assert derive(row) == ("unknown_observation",)
    row = observation()
    del row["after"]["hard_valid"]
    assert "unknown_observation" in derive(row)
