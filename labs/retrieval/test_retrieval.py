from dataclasses import replace
import hashlib
import json

import pytest

from .benchmark import aggregate, load_fixture, measure, quality
from .retrieval import EvidenceItem, Lexical, RetrievalRequest, retrieve, render


def item(key, text="ramp", **kw):
    return EvidenceItem(key, text, "project", {"ref": "fixture:" + key}, project="alpha", **kw)


def test_fixture_exact_excerpts_and_proxy_labels():
    items, queries = load_fixture()
    assert len({x.id for x in items}) == len(items)
    assert {x.corpus for x in items} == {"regulation", "precedent", "project", "technical"}
    assert 30 <= len(queries) <= 50
    for x in items:
        assert hashlib.sha256(x.text.encode()).hexdigest() == x.source["excerpt_sha256"]
        if "normalized_offsets" in x.source:
            lo, hi = x.source["normalized_offsets"]
            assert hi - lo == len(x.text)
        assert x.source.get("url") or x.source.get("ref")
    ids = {x.id for x in items}
    for q in queries:
        assert "agent" in q["label_origin"]
        assert set(q["relevant"]) <= ids
        assert set(q["principal"]) <= set(q["relevant"])
        assert set(q["companions"]) <= set(q["relevant"])


@pytest.mark.parametrize("metadata", [True, False])
@pytest.mark.parametrize("expand", [True, False])
def test_project_isolation_precedes_ranking_and_expansion(metadata, expand):
    secret = replace(item("secret", "ramp ramp ramp protected beta answer"), project="beta")
    public = replace(item("public", "ramp"), project=None)
    own = item("own", "irrelevant")
    request = RetrievalRequest("ramp", "project", project="alpha")
    result = retrieve(request, [secret, public, own], Lexical(), metadata=metadata, expand=expand)
    assert [i for i, _ in result.ranked] == ["own"]
    assert b"beta" not in result.context
    assert {x["id"] for x in result.reopen} == {"own"}


def test_project_request_refuses_missing_scope():
    with pytest.raises(ValueError, match="explicit project"):
        RetrievalRequest("entrance", "project")


def test_wrong_version_and_jurisdiction_are_not_companions():
    a = EvidenceItem("a", "width", "regulation", {"ref": "fixture:a"},
                     jurisdiction="ADA", version="2010", companions=("b", "c"))
    b = replace(a, id="b", version="1991", companions=())
    c = replace(a, id="c", jurisdiction="ABA", companions=())
    result = retrieve(RetrievalRequest("width", "regulation", jurisdiction="ADA", version="2010"),
                      [a, b, c], Lexical())
    assert result.status == "insufficient:missing_companions"
    assert result.context == b""
    assert result.missing == ("b", "c")
    assert {x["id"] for x in result.reopen} == {"a"}


def test_companion_cycle_is_bounded_and_preserves_full_text_and_sources():
    a = item("a", "slope 1:12", companions=("b",), link_basis="source adjacency")
    b = item("b", "EXCEPTION only in existing facilities", companions=("a",))
    r = retrieve(RetrievalRequest("slope", "project", project="alpha", limit=1), [a, b], Lexical())
    assert r.context == render([a, b])
    assert r.status == "unverified"  # Structural completeness is not relevance or authority.


def test_byte_budget_is_utf8_including_refs_and_metadata_and_never_splits():
    a = item("a", "坡道 must preserve 条件", companions=("b",))
    b = item("b", "例外 and scope")
    req = RetrievalRequest("坡道", "project", project="alpha", limit=1)
    exact = len(render([a, b]))
    assert retrieve(replace(req, max_bytes=exact), [a, b], Lexical()).context == render([a, b])
    result = retrieve(replace(req, max_bytes=exact - 1), [a, b], Lexical())
    assert result.status == "insufficient:budget"
    assert result.context == b""
    assert [x["id"] for x in result.reopen] == ["a", "b"]


def test_missing_evidence_is_not_empty_success():
    req = RetrievalRequest("ramp", "project", project="alpha")
    absent = retrieve(req, [], Lexical())
    assert absent.status == "insufficient:no_candidates"
    linked = retrieve(req, [item("a", companions=("missing:section",))], Lexical())
    assert linked.status == "insufficient:missing_companions"
    assert linked.missing == ("missing:section",)
    assert not linked.context


def test_ranker_has_no_access_to_judgments_and_ties_are_stable():
    docs = [item("b", "width"), item("a", "width")]
    assert Lexical().rank("width", docs) == Lexical().rank("width", list(reversed(docs)))
    assert Lexical().rank("width", docs)[0][0] == "a"
    assert set(RetrievalRequest.__dataclass_fields__) == {
        "query", "corpus", "project", "jurisdiction", "version", "max_bytes", "limit"}


def test_metrics_use_rank_and_emitted_context_separately():
    q = {"id": "test", "request": {"query": "slope", "corpus": "project", "project": "alpha", "limit": 1},
         "relevant": ["a", "b"], "principal": ["a"], "companions": ["b"], "known_missing": False}
    docs = [item("a", "slope", companions=("b",)), item("b", "exception")]
    req = RetrievalRequest(**q["request"])
    row = measure(q, retrieve(req, docs, Lexical()), docs, .01)
    assert row["recall"] == .5
    assert row["companion_recall"] == 1
    assert row["complete_evidence"] == 1
    refused = measure(q, retrieve(replace(req, max_bytes=1), docs, Lexical()), docs, .01)
    assert refused["recall"] == .5 and refused["companion_recall"] == 0
    assert refused["bytes"] == 0
    assert aggregate([row, refused])["companion_recall"] == .5


def test_known_relevance_metrics_hand_calculated():
    r = quality(["x", "a", "b"], {"a", "b"}, 3)
    assert r["recall"] == 1 and r["precision"] == 2 / 3 and r["mrr"] == .5
    assert r["ndcg"] == pytest.approx(.6934264)
    assert quality([], [], 3)["recall"] is None


def test_conditions_remain_visible_with_no_accepted_truth_field():
    items, _ = load_fixture()
    req = RetrievalRequest("transfer through-connection silhouette", "precedent", limit=1,
                           version="27e7f2fddb8bf969abd4db8e1c575b8e1ba14b0d")
    result = retrieve(req, items, Lexical())
    supplied = json.loads(result.context)
    assert {x["id"] for x in supplied} >= {"study-pattern", "study-gap", "study-changed-context"}
    assert any("proposed by agent" in (x["link_basis"] or "") for x in supplied)
    assert result.status == "unverified"


def test_exception_first_hit_cannot_bypass_missing_table():
    items, _ = load_fixture()
    request = RetrievalRequest("Table 405.2 where slopes necessary due space limitations existing buildings",
                               "regulation", jurisdiction="US-ADA", version="2010", limit=1)
    result = retrieve(request, items, Lexical())
    assert result.ranked[0][0] == "ada-ramp-exception"
    assert result.status == "insufficient:missing_companions"
    assert "missing:ada-table-405.2" in result.missing
    assert not result.context


def test_technical_qualification_first_hit_retains_warning_and_method():
    items, _ = load_fixture()
    request = RetrievalRequest("walk_up false parent different drives", "technical", version="3.12", limit=1)
    result = retrieve(request, items, Lexical())
    supplied = {x["id"]: x for x in json.loads(result.context)}
    assert {"py3.12-relative", "py3.12-relative-condition", "py3.12-relative-warning"} <= supplied.keys()
    assert "symlink" in supplied["py3.12-relative-warning"]["text"]
