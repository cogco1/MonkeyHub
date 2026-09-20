"""Disposable event decisions over already verified observations; no writer."""
from __future__ import annotations

from math import isfinite


SOURCE_KEYS = ("revision", "state_digest", "step_sha256")
TOLERANCE_M = 1e-6
TOLERANCE_M3 = 1e-9
THRESHOLDS = (.5, .75, .9, 1.000001)  # Last option disables suppression.


def derive(checkpoint: dict) -> tuple[str, ...]:
    """Exact facts take precedence over text. Missing evidence is never harmless."""
    current, observed = checkpoint.get("current_source"), checkpoint.get("observed_source")
    if (not isinstance(current, dict) or not isinstance(observed, dict)
            or any(not current.get(key) for key in SOURCE_KEYS) or current != observed):
        return ("stale_identity",)
    if checkpoint.get("observation_verified") is not True:
        return ("unknown_observation",)
    before, after = checkpoint.get("before"), checkpoint.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return ("unknown_observation",)
    events = []
    for field in ("entity_ids", "dependency_closure"):
        values = [row.get(field) for row in (before, after)]
        if any(not isinstance(value, list) or any(not isinstance(v, str) for v in value)
               or len(set(value)) != len(value) for value in values):
            events.append("unknown_observation")
        elif set(values[0]) != set(values[1]):
            events.append("identity_changed" if field == "entity_ids" else "dependency_changed")
    hard = [row.get("hard_valid") for row in (before, after)]
    if (any("hard_valid" not in row for row in (before, after))
            or any(value is not None and type(value) is not bool for value in hard)):
        events.append("unknown_observation")
    elif hard[0] != hard[1] or hard[1] is False:
        events.append("hard_constraint")
    pairs = [row.get("relation") for row in (before, after)]
    for key, tolerance in (("distance_m", TOLERANCE_M), ("intersection_volume_m3", TOLERANCE_M3)):
        values = [pair.get(key) if isinstance(pair, dict) else None for pair in pairs]
        if any(type(value) not in (int, float) or not isfinite(value) or value < 0 for value in values):
            events.append("unknown_observation")
        elif abs(values[0] - values[1]) > tolerance:
            events.append("spatial_relation")
    if checkpoint.get("requires_visual"):
        events.append("visual_judgement")
    if checkpoint.get("semantic_text"):
        events.append("semantic_judgement")
    return tuple(dict.fromkeys(events))


def semantic_only(events: tuple[str, ...]) -> bool:
    return events == ("semantic_judgement",)


def suppressible(parsed: dict | None, threshold: float) -> bool:
    if not isinstance(parsed, dict) or parsed.get("choice") != "ignore":
        return False
    confidence = parsed.get("confidence")
    return type(confidence) in (int, float) and isfinite(confidence) and threshold <= confidence <= 1


def choose_threshold(development: list[dict]) -> dict:
    """Tune only on development classifications; gold is never passed to a model."""
    if not development or any(row.get("split") != "dev" for row in development):
        raise ValueError("threshold selection requires nonempty development-only results")
    scores = []
    for threshold in THRESHOLDS:
        skipped = [row for row in development if suppressible(row.get("parsed"), threshold)]
        scores.append({"threshold": threshold, "skips": len(skipped),
                       "misses": sum(row["meaningful"] for row in skipped)})
    safe = [row for row in scores if row["misses"] == 0]
    selected = max(safe, key=lambda row: (row["skips"], row["threshold"]))
    return {"threshold": selected["threshold"], "development_count": len(development), "scores": scores}
