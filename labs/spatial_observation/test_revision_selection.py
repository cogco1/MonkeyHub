"""Revision-consumer boundaries, independent of retrieval rankings or provider gold."""
from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from labs.spatial_observation.selection import Query, SelectionIndex, require_current


METHODS = ("full", "graph", "lexical")
REQUIRED = {"ground", "screen", "lintel", "drain", "brace", "sensor"}


def snapshot():
    identifiers = (
        "ground", "screen", "lintel", "drain", "brace", "sensor",
        "support-child", "blocked-child", "sibling", "mystery",
    )

    def dependency(source, target, effect):
        return {"id": f"edge:{source}-to-{target}", "source": source,
                "target": target, "kind": "dependency", "effect": effect}

    dependencies = [dependency("ground", key, "requires_revalidation")
                    for key in identifiers if key != "ground"]
    dependencies += [
        dependency("screen", "lintel", "invalidates"),
        dependency("lintel", "drain", "requires_revalidation"),
        dependency("brace", "drain", "supports_only"),
        dependency("sensor", "drain", "blocks"),
        dependency("screen", "support-child", "supports_only"),
        dependency("screen", "blocked-child", "blocks"),
    ]
    return {
        "revision": "revision-consumer-unit-1", "state_digest": "bound-state-1",
        "record_digest": "authored-content-1", "step_sha256": "retained-step-1",
        "units": "meter", "coordinate_system": "CAD Z-up",
        "entities": [{"id": key, "metadata": {"label": key,
                     "role": None if key == "mystery" else "declared-role",
                     "producer": "prism"}, "params": {"height": 1.0}}
                     for key in identifiers],
        "relations": [], "dependencies": dependencies,
    }


def view(source, identifier="front"):
    stream = BytesIO()
    Image.new("RGB", (1, 1), "white").save(stream, format="PNG")
    return {"id": identifier, "png": stream.getvalue(),
            "source": {key: source[key] for key in ("revision", "state_digest", "step_sha256")}}


def revise(index, source, query=None, budget=6, **kwargs):
    query = query or Query("Change the screen and inspect its declared impact.", ("screen",), "change")
    return index.select_revision(query, snapshot=source, budget=budget, **kwargs)


@pytest.mark.parametrize("method", METHODS)
def test_all_affected_upstream_is_preserved_without_expanding_shared_ground(method):
    source = snapshot()
    result = revise(SelectionIndex(source, method), source)
    assert set(result["coverage"]["affected_refs"]) == {"screen", "lintel", "drain"}
    assert set(result["coverage"]["required_declared_refs"]) == REQUIRED
    assert not result["coverage"]["missing_declared_dependency_refs"]
    assert not result["coverage"]["fallback_to_full"]
    if method != "full":
        assert set(result["entity_refs"]) == REQUIRED
        assert "sibling" not in result["entity_refs"]
    retained = {edge["id"]: edge for edge in result["context"]["edges"]}
    assert retained["edge:brace-to-drain"]["effect"] == "supports_only"
    assert retained["edge:sensor-to-drain"]["effect"] == "blocks"
    assert retained["edge:screen-to-lintel"]["effect"] == "invalidates"
    assert retained["edge:lintel-to-drain"]["effect"] == "requires_revalidation"


def test_impact_does_not_confuse_upstream_support_with_changed_objects():
    source = snapshot()
    result = revise(SelectionIndex(source, "graph"), source,
                    Query("Inspect declared downstream impact.", ("screen",), "impact"), budget=3)
    assert set(result["entity_refs"]) == {"screen", "lintel", "drain"}
    assert set(result["coverage"]["required_declared_refs"]) == {"screen", "lintel", "drain"}


@pytest.mark.parametrize("method", METHODS)
def test_budget_shortfall_returns_full_instead_of_losing_required_support(method):
    source = snapshot()
    result = revise(SelectionIndex(source, method), source, budget=5)
    assert set(result["entity_refs"]) == {row["id"] for row in source["entities"]}
    assert result["coverage"]["exceeds_budget"]
    assert result["coverage"]["fallback_to_full"] == (method != "full")
    assert not result["coverage"]["missing_declared_dependency_refs"]
    assert result["coverage"]["fallback_reasons"]


@pytest.mark.parametrize("method", METHODS)
def test_more_named_targets_than_budget_falls_back_without_rejecting_the_request(method):
    source = snapshot()
    query = Query("Revise these named objects.", ("mystery", "sibling", "brace"), "change")
    result = revise(SelectionIndex(source, method), source, query, budget=2)
    assert set(query.target_refs) <= set(result["entity_refs"])
    assert len(result["entity_refs"]) == len(source["entities"])
    assert result["coverage"]["exceeds_budget"]
    assert result["coverage"]["fallback_to_full"] == (method != "full")


@pytest.mark.parametrize("method", METHODS)
def test_named_unknown_role_remains_null(method):
    source = snapshot()
    result = revise(SelectionIndex(source, method), source,
                    Query("Resize the unclassified object without assigning its role.", ("mystery",), "change"),
                    budget=2)
    entity = next(row for row in result["context"]["entities"] if row["id"] == "mystery")
    assert entity == next(row for row in source["entities"] if row["id"] == "mystery")
    assert entity["metadata"]["role"] is None
    assert set(result["coverage"]["required_declared_refs"]) == {"ground", "mystery"}


def test_conditions_on_other_entities_and_conflicts_are_preserved_verbatim():
    source = snapshot()
    conditions = [
        {"id": "keep-height", "text": "Preserve the current sibling height.",
         "entity_refs": ["sibling"], "constraint": {"path": "params.height", "value": 1.0}},
        {"id": "raise-height", "text": "The sibling height must be 2 metres.",
         "entity_refs": ["sibling"], "constraint": {"path": "params.height", "value": 2.0}},
    ]
    result = revise(SelectionIndex(source, "graph"), source, budget=7, conditions=conditions)
    assert result["context"]["conditions"] == conditions
    assert set(result["coverage"]["required_declared_refs"]) == REQUIRED | {"sibling"}
    assert "sibling" in result["entity_refs"]
    assert "sibling" not in result["coverage"]["affected_refs"]


@pytest.mark.parametrize("operation,targets", (("inspect", ()), ("inspect", ("screen",)),
                                                ("similar", ("screen",))))
def test_open_ended_evidence_requests_fall_back(operation, targets):
    source = snapshot()
    result = revise(SelectionIndex(source, "graph"), source,
                    Query("Find the relevant objects.", targets, operation))
    assert result["coverage"]["fallback_to_full"]
    assert len(result["entity_refs"]) == len(source["entities"])


def test_nonexistent_explicit_target_is_rejected_without_inventing_an_entity():
    source = snapshot()
    with pytest.raises(ValueError, match="absent from this source"):
        revise(SelectionIndex(source, "graph"), source,
               Query("Revise the named object.", ("unresolved-target",), "change"))


@pytest.mark.parametrize("effect", (None, "future-effect"))
def test_unknown_dependency_effect_falls_back(effect):
    source = snapshot()
    source["dependencies"][-1]["effect"] = effect
    result = revise(SelectionIndex(source, "graph"), source)
    assert result["coverage"]["fallback_to_full"]
    assert len(result["entity_refs"]) == len(source["entities"])


def test_required_current_png_is_delivered_with_its_source_and_bytes():
    source = snapshot()
    image = view(source)
    result = revise(SelectionIndex(source, "graph"), source,
                    required_images=("front",), images=(image,))
    assert result["images"] == [image]
    assert result["context"]["images"] == [{"id": "front", "source": image["source"]}]
    assert result["cost"]["image_bytes"] == len(image["png"])


def test_required_missing_image_refuses_inference():
    source = snapshot()
    with pytest.raises(ValueError, match="image.*missing"):
        revise(SelectionIndex(source, "graph"), source, required_images=("front",))


@pytest.mark.parametrize("field", ("revision", "state_digest", "step_sha256"))
def test_image_from_another_source_refuses_inference(field):
    source = snapshot()
    image = view(source)
    image["source"][field] = "older-source"
    with pytest.raises(ValueError, match="stale or unbound image"):
        revise(SelectionIndex(source, "graph"), source, required_images=("front",), images=(image,))


@pytest.mark.parametrize("payload", (None, "image-reference-only", b"not a PNG"))
def test_image_reference_or_non_png_bytes_are_not_image_evidence(payload):
    source = snapshot()
    image = view(source)
    image["png"] = payload
    with pytest.raises(ValueError, match="PNG bytes"):
        revise(SelectionIndex(source, "graph"), source, required_images=("front",), images=(image,))


def test_png_signature_alone_is_not_decodable_image_evidence():
    source = snapshot()
    image = view(source)
    image["png"] = b"\x89PNG\r\n\x1a\n"
    with pytest.raises(ValueError, match="PNG"):
        revise(SelectionIndex(source, "graph"), source, required_images=("front",), images=(image,))


@pytest.mark.parametrize("field", ("revision", "state_digest", "record_digest", "step_sha256"))
def test_missing_source_field_cannot_weaken_the_current_source_check(field):
    source = snapshot()
    index = SelectionIndex(source, "graph")
    incomplete = deepcopy(source)
    del incomplete[field]
    with pytest.raises(ValueError, match="stale"):
        revise(index, incomplete)
    result = revise(index, source)
    del result["source"][field]
    with pytest.raises(ValueError, match="stale"):
        require_current(result, source)


@pytest.mark.parametrize("field", ("revision", "state_digest", "record_digest", "step_sha256"))
def test_every_retained_source_identity_must_match(field):
    source = snapshot()
    index = SelectionIndex(source, "graph")
    changed = deepcopy(source)
    changed[field] = "different-current-source"
    with pytest.raises(ValueError, match="stale"):
        revise(index, changed)


def test_input_and_returned_contexts_do_not_share_mutable_values():
    source = snapshot()
    image = view(source)
    conditions = [{"id": "keep", "text": "Keep the screen height.", "entity_refs": ["screen"],
                   "constraint": {"path": "params.height", "value": 1.0}}]
    before_source, before_image, before_conditions = deepcopy((source, image, conditions))
    index = SelectionIndex(source, "graph")
    result = revise(index, source, conditions=conditions, required_images=("front",), images=(image,))
    result["context"]["entities"][0]["params"]["height"] = 999
    result["context"]["edges"][0]["effect"] = "tampered"
    result["context"]["conditions"][0]["constraint"]["value"] = 999
    result["images"][0]["source"]["revision"] = "tampered"
    assert (source, image, conditions) == (before_source, before_image, before_conditions)
    again = revise(index, source, conditions=conditions, required_images=("front",), images=(image,))
    assert all(row["params"]["height"] == 1.0 for row in again["context"]["entities"])
    assert again["context"]["conditions"] == before_conditions
    assert again["images"] == [before_image]
    source["entities"][0]["params"]["height"] = 888
    assert index.snapshot == before_source


@pytest.fixture
def scored_evidence():
    source = snapshot()
    task = {
        "query": Query("Plan a source-bound check for the unclassified object.", ("mystery",), "change"),
        "conditions": [{"id": "keep-height", "entity_refs": ["mystery"],
                        "text": "Preserve the current height.", "field": "params.height", "equals": 1.0}],
        "requested_changes": [], "required_images": ["front"], "exact_pairs": [],
        "gold": {"relevant_entities": ["ground", "mystery"], "critical_entities": ["mystery"],
                 "decision": "ready_for_checks", "private_audit_note": "EVALUATOR_ONLY_MARKER"},
    }
    result = revise(SelectionIndex(source, "graph"), source, task["query"], budget=2,
                    conditions=task["conditions"], required_images=task["required_images"],
                    images=(view(source),))
    answer = {"entity_refs": ["ground", "mystery"], "edge_refs": ["edge:ground-to-mystery"],
              "condition_refs": ["keep-height"], "image_refs": ["front"],
              "unknown_role_refs": ["mystery"], "exact_pairs": [], "decision": "ready_for_checks"}
    return source, task, result, answer


def test_consumer_prompt_contains_actual_image_and_no_evaluator_gold(scored_evidence):
    from labs.spatial_observation.revision_benchmark import prompt_blocks

    _, task, result, _ = scored_evidence
    prompt, blocks = prompt_blocks(task, result)
    assert "gold" not in prompt
    assert "EVALUATOR_ONLY_MARKER" not in str(prompt)
    assert "EVALUATOR_ONLY_MARKER" not in str(blocks)
    assert prompt["observation"]["conditions"] == task["conditions"]
    assert len([block for block in blocks if block["type"] == "image"]) == 1


def test_complete_supported_consumer_evidence_plan_passes(scored_evidence):
    from labs.spatial_observation.revision_benchmark import score

    source, task, result, answer = scored_evidence
    assert score(answer, task, result, source)["passed"]


@pytest.mark.parametrize("field", ("entity_refs", "edge_refs", "condition_refs", "image_refs", "unknown_role_refs"))
def test_consumer_omitting_required_evidence_fails(scored_evidence, field):
    from labs.spatial_observation.revision_benchmark import score

    source, task, result, answer = scored_evidence
    answer[field] = []
    assert not score(answer, task, result, source)["passed"]


def test_consumer_cannot_claim_entities_and_edges_it_was_not_given(scored_evidence):
    from labs.spatial_observation.revision_benchmark import score

    source, task, result, answer = scored_evidence
    result["entity_refs"].remove("mystery")
    result["edge_refs"].clear()
    scores = score(answer, task, result, source)
    assert not scores["passed"]
    assert not scores["checks"]["entities_supported"]
    assert not scores["checks"]["edges_supported"]
    assert scores["unsupported_entity_refs"] == ["mystery"]


def test_consumer_duplicate_references_or_unverified_geometry_claims_fail(scored_evidence):
    from labs.spatial_observation.revision_benchmark import score

    source, task, result, answer = scored_evidence
    duplicate = deepcopy(answer)
    duplicate["entity_refs"].append("mystery")
    assert not score(duplicate, task, result, source)["valid"]
    answer["collision_verified"] = True
    assert not score(answer, task, result, source)["passed"]
