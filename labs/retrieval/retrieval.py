"""Replaceable in-memory ranking experiment; no project writer or acceptance API."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import math
import re
from typing import Protocol


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    text: str
    corpus: str
    source: dict
    project: str | None = None
    jurisdiction: str | None = None
    version: str | None = None
    companions: tuple[str, ...] = ()
    link_basis: str | None = None


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    corpus: str
    project: str | None = None
    jurisdiction: str | None = None
    version: str | None = None
    max_bytes: int = 12288
    limit: int = 3

    def __post_init__(self):
        if self.limit < 1 or self.max_bytes < 0:
            raise ValueError("positive limit and non-negative budget required")
        if self.corpus == "project" and not self.project:
            raise ValueError("project corpus requires an explicit project")


@dataclass(frozen=True)
class RetrievalResult:
    ranked: tuple[tuple[str, float], ...]
    context: bytes
    status: str
    missing: tuple[str, ...]
    missing_details: tuple[dict, ...]
    reopen: tuple[dict, ...]


class EvidenceRanker(Protocol):
    def rank(self, query: str, items: list[EvidenceItem]) -> list[tuple[str, float]]: ...


def tokens(text: str) -> list[str]:
    # Deliberately simple English baseline, with numeric sections preserved.
    return re.findall(r"\w+(?:[.-]\w+)*", text.lower())


class Lexical:
    """BM25 with positive Robertson/Lucene IDF, k1=1.5 and b=.75."""
    def rank(self, query, items):
        if not items:
            return []
        docs = [Counter(tokens(x.text)) for x in items]
        lengths = [sum(x.values()) for x in docs]
        avg = sum(lengths) / len(docs) or 1
        df = Counter(t for d in docs for t in d)
        scores = []
        for item, d, length in zip(items, docs, lengths):
            score = sum(
                math.log(1 + (len(docs) - df[t] + .5) / (df[t] + .5))
                * d[t] * 2.5 / (d[t] + 1.5 * (.25 + .75 * length / avg))
                for t in sorted(set(tokens(query))) if d[t]
            )
            scores.append((item.id, score))
        return sorted(scores, key=lambda x: (-x[1], x[0]))


DENSE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DENSE_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANK_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"


class Dense:
    def __init__(self, items, cache_dir):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(DENSE_MODEL, revision=DENSE_REVISION,
                                         cache_folder=str(cache_dir), device="cpu")
        # Refuse silent truncation: these are source paragraphs, not token windows.
        self._check_length([x.text for x in items])
        vectors = self.model.encode([x.text for x in items], normalize_embeddings=True,
                                    show_progress_bar=False)
        self.vectors = {x.id: v for x, v in zip(items, vectors)}

    def _check_length(self, texts):
        encoded = self.model.tokenizer(texts, truncation=False)["input_ids"]
        if any(len(x) > self.model.max_seq_length for x in encoded):
            raise ValueError("dense input exceeds model context; change representation explicitly")

    def rank(self, query, items):
        if not items:
            return []
        self._check_length([query])
        vector = self.model.encode(query, normalize_embeddings=True, show_progress_bar=False)
        return sorted(((x.id, float(self.vectors[x.id] @ vector)) for x in items),
                      key=lambda x: (-x[1], x[0]))


class Hybrid:
    def __init__(self, dense):
        self.lexical, self.dense = Lexical(), dense

    def rank(self, query, items):
        scores = Counter()
        for ranking in (self.lexical.rank(query, items), self.dense.rank(query, items)):
            for rank, (item_id, _) in enumerate(ranking, 1):
                scores[item_id] += 1 / (60 + rank)
        return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


class Reranked:
    def __init__(self, hybrid, cache_dir):
        from sentence_transformers import CrossEncoder
        self.hybrid = hybrid
        self.model = CrossEncoder(RERANK_MODEL, revision=RERANK_REVISION, device="cpu",
                                  max_length=512, cache_dir=str(cache_dir))

    def rank(self, query, items):
        candidates = self.hybrid.rank(query, items)[:10]
        if not candidates:
            return []
        by_id = {x.id: x for x in items}
        pairs = [(query, by_id[i].text) for i, _ in candidates]
        encoded = self.model.tokenizer(pairs, truncation=False)["input_ids"]
        if any(len(x) > 512 for x in encoded):
            raise ValueError("reranker pair exceeds model context")
        scores = self.model.predict(pairs, show_progress_bar=False)
        return sorted(((i, float(s)) for (i, _), s in zip(candidates, scores)),
                      key=lambda x: (-x[1], x[0]))


def eligible(request, item, *, metadata=True):
    # Project isolation applies even in the raw-ranking ablation.
    if item.corpus != request.corpus or item.project != request.project:
        return False
    return not metadata or all(want is None or want == getattr(item, key)
                               for key in ("jurisdiction", "version")
                               for want in (getattr(request, key),))


def render(items):
    return json.dumps([
        {"id": x.id, "text": x.text, "source": x.source,
         "project": x.project, "jurisdiction": x.jurisdiction, "version": x.version,
         "companions": list(x.companions), "link_basis": x.link_basis}
        for x in items
    ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def reopen_ref(item):
    """Keep an item's binding identity available even when no context is emitted."""
    return {"id": item.id, "source": item.source, "corpus": item.corpus,
            "project": item.project, "jurisdiction": item.jurisdiction,
            "version": item.version}


def retrieve(request, items, ranker, *, metadata=True, expand=True):
    """Close declared source links without reading query relevance labels.

    Status is about bundle structure, never proof that it answers the query.
    A missing companion or overflow rejects the ENTIRE bundle, preserving refs.
    """
    allowed = [x for x in items if eligible(request, x, metadata=metadata)]
    ranked = tuple(ranker.rank(request.query, allowed)[:request.limit])
    if not ranked:
        return RetrievalResult((), b"", "insufficient:no_candidates", (), (), ())
    by_id = {x.id: x for x in allowed}
    queue = [(i, None) for i, _ in ranked]
    selected, missing, missing_details = {}, [], []
    while queue:
        key, required_by = queue.pop(0)
        if key in selected or key in missing:
            continue
        if key not in by_id:
            missing.append(key)
            principal = selected[required_by]
            missing_details.append({"id": key, "required_by": required_by,
                                    "link_basis": principal.link_basis,
                                    "required_by_ref": reopen_ref(principal)})
            continue
        selected[key] = by_id[key]
        if expand:
            queue.extend((companion, key) for companion in by_id[key].companions)
    reopen = tuple(reopen_ref(x) for x in selected.values())
    if missing:
        return RetrievalResult(ranked, b"", "insufficient:missing_companions",
                               tuple(missing), tuple(missing_details), reopen)
    context = render(selected.values())
    if len(context) > request.max_bytes:
        return RetrievalResult(ranked, b"", "insufficient:budget", (), (), reopen)
    return RetrievalResult(ranked, context, "unverified", (), (), reopen)
