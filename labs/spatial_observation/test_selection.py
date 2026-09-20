"""Boundary/behavior tests; retrieval findings belong to the benchmark output."""
from copy import deepcopy
import os

import pytest

from labs.spatial_observation.selection import (
    MiniLMEncoder, Query, SelectionIndex, require_current, retrieval_metrics,
)


def snapshot():
    identifiers = ("screen", "lintel", "seal", "fixing", "drain", "mystery", "other")
    edges = [{"id": f"relation:{a}-to-{b}", "source": a, "target": b, "kind": "dependency"}
             for a, b in zip(identifiers[:4], identifiers[1:5])]
    return {"revision": "unit-base", "state_digest": "unit-source-1", "units": "meter",
            "coordinate_system": "CAD Z-up", "entities": [
                {"id": key, "metadata": {"label": "unclassified object" if key == "mystery" else key,
                                         "role": None if key == "mystery" else "known",
                                         "producer": "prism"},
                 "params": {"height": 1}} for key in identifiers],
            "relations": edges, "dependencies": deepcopy(edges)}


def select(index, query, budget=4):
    return index.select(query, **index.source, budget=budget)


def test_selectors_do_not_mutate_source_or_retained_result():
    source = snapshot()
    before = deepcopy(source)
    index = SelectionIndex(source, "graph")
    result = select(index, Query("impact", ("screen",), "impact"))
    result["context"]["entities"][0]["params"]["height"] = 999
    source["entities"][0]["params"]["height"] = 222
    next_result = select(index, Query("impact", ("screen",), "impact"))
    assert next_result["context"]["entities"][0]["params"]["height"] == 1
    assert index.snapshot == before


def test_critical_dependency_omission_is_reported_without_truncating_full():
    source = snapshot()
    query = Query("impact", ("screen",), "impact")
    result = select(SelectionIndex(source, "graph"), query)
    assert result["entity_refs"] == ["screen", "lintel", "seal", "fixing"]
    assert result["coverage"]["missing_declared_dependency_refs"] == ["drain"]
    assert not result["coverage"]["complete_declared_impact_context"]
    full = select(SelectionIndex(source, "full"), query)
    assert len(full["entity_refs"]) == 7
    assert full["coverage"]["full_reference_exceeds_budget"]
    metrics = retrieval_metrics(result, relevant_entities=("screen", "lintel", "seal", "fixing", "drain"),
                                critical_entities=("drain",))
    assert metrics["entity_recall"] == .8
    assert metrics["missed_critical_refs"] == ["drain"]


def test_graph_closure_is_directed_and_independent_of_metadata_roles():
    index = SelectionIndex(snapshot(), "graph")
    assert index.dependency_closure(("seal",)) == ("seal", "fixing", "drain")
    assert index.dependency_closure(("drain",)) == ("drain",)
    assert index.dependency_closure(("mystery",)) == ("mystery",)


@pytest.mark.parametrize("method", ("full", "graph", "lexical"))
def test_stale_source_refused_and_rebuild_includes_edit(method):
    source = snapshot()
    query = Query("screen", ("screen",), "impact")
    old = SelectionIndex(source, method)
    result = select(old, query)
    updated = deepcopy(source)
    updated.update(revision="unit-edited", state_digest="unit-source-2")
    updated["entities"][0]["params"]["height"] = 3
    with pytest.raises(ValueError, match="stale"):
        require_current(result, updated)
    with pytest.raises(ValueError, match="stale"):
        old.select(query, revision=updated["revision"], state_digest=updated["state_digest"], budget=4)
    rebuilt = select(SelectionIndex(updated, method), query)
    require_current(rebuilt, updated)
    assert rebuilt["context"]["entities"][0]["params"]["height"] == 3


@pytest.mark.parametrize("method", ("full", "graph", "lexical"))
def test_irrelevant_serialization_order_has_no_effect(method):
    source = snapshot()
    query = Query("screen impact", ("screen",), "impact")
    original = select(SelectionIndex(source, method), query)
    for key in ("entities", "relations", "dependencies"):
        source[key].reverse()
    reordered = select(SelectionIndex(source, method), query)
    assert original["context"] == reordered["context"]


def test_unknown_role_lexical_search_retains_source_identity():
    source = snapshot()
    result = select(SelectionIndex(source, "lexical"), Query("unclassified object"), budget=1)
    assert result["entity_refs"] == ["mystery"]
    assert result["context"]["entities"][0]["metadata"]["role"] is None
    assert result["source"] == {"revision": source["revision"], "state_digest": source["state_digest"]}


def test_no_invented_relation_or_synthetic_encoder_fallback():
    source = snapshot()
    with pytest.raises(ValueError, match="real encoder"):
        SelectionIndex(source, "embedding_graph")
    result = select(SelectionIndex(source, "graph"), Query("similar", ("mystery",), "similar"))
    actual_edges = {row["id"] for row in source["dependencies"] + source["relations"]}
    assert set(result["edge_refs"]) <= actual_edges
    assert all(edge["family"] in ("relations", "dependencies") for edge in result["context"]["edges"])


def test_dangling_edge_is_rejected_instead_of_assuming_coverage():
    source = snapshot()
    source["dependencies"].append({"id": "missing", "source": "screen", "target": "absent"})
    with pytest.raises(ValueError, match="unresolved"):
        SelectionIndex(source, "graph")


@pytest.mark.skipif(not os.environ.get("SPATIAL_ENCODER_PATH"), reason="explicit pinned model cache required")
def test_real_pinned_minilm_inference_and_readonly_selection():
    encoder = MiniLMEncoder(os.environ["SPATIAL_ENCODER_PATH"])
    vectors = encoder.encode(["unclassified object", "unclassified object", "cloudy weather"])
    assert vectors.shape == (3, 384)
    assert float(vectors[0] @ vectors[1]) > .99
    assert float(vectors[0] @ vectors[2]) < .99
    source = snapshot()
    before = deepcopy(source)
    index = SelectionIndex(source, "embedding_graph", encoder=encoder)
    result = select(index, Query("unclassified object"), budget=2)
    assert result["entity_refs"][0] == "mystery"
    assert result["cost"]["api_calls"] == 0
    assert index.cost["vector_bytes"] == 7 * 384 * 4
    assert source == before
