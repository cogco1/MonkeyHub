"""Read-only, revision-bound selectors for the controlled #170 experiment.

This module receives a derived StateRecord snapshot. It neither measures geometry
nor proposes/accepts edits. Audited answers are deliberately not selector inputs.
"""
from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import re
from time import perf_counter
from typing import Any, Mapping, Sequence


METHODS = ("full", "graph", "lexical", "embedding_graph")
METHOD_VERSION = "controlled-selection-v1"
ENCODER_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
ENCODER_REPOSITORY = "qdrant/all-MiniLM-L6-v2-onnx"
ENCODER_REVISION = "5f1b8cd78bc4fb444dd171e59b18f3a3af89a079"
ENCODER_WEIGHT_SHA256 = "bbd7b466f6d58e646fdc2bd5fd67b2f5e93c0b687011bd4548c420f7bd46f0c5"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\w]+", value.casefold())


@dataclass(frozen=True)
class Query:
    """Same user-visible inputs for every method; no gold or relevance labels."""

    text: str
    target_refs: tuple[str, ...] = ()
    operation: str = "inspect"


class MiniLMEncoder:
    """Real pretrained text encoder, loaded from an explicitly supplied cache.

    Download the pinned public snapshot separately. Loading never sends project
    records to a service. No fallback vectors are produced if inference fails.
    """

    def __init__(self, model_path: str):
        started = perf_counter()
        from fastembed import TextEmbedding
        from hashlib import file_digest
        from importlib.metadata import version
        from pathlib import Path
        from tokenizers import Tokenizer

        with (Path(model_path) / "model.onnx").open("rb") as stream:
            weight_sha256 = file_digest(stream, "sha256").hexdigest()
        if weight_sha256 != ENCODER_WEIGHT_SHA256:
            raise ValueError("model weights do not match the pinned ONNX artifact")
        self.model = TextEmbedding(
            ENCODER_MODEL, specific_model_path=model_path,
            local_files_only=True, threads=1, providers=["CPUExecutionProvider"],
        )
        self.untruncated_tokenizer = Tokenizer.from_file(str(Path(model_path) / "tokenizer.json"))
        self.untruncated_tokenizer.no_truncation()
        self.untruncated_tokenizer.no_padding()
        self.max_tokens = self.model.model.tokenizer.truncation["max_length"]
        self.identity = {
            "model": ENCODER_MODEL, "repository": ENCODER_REPOSITORY,
            "revision": ENCODER_REVISION, "license": "Apache-2.0",
            "fastembed": version("fastembed"), "onnxruntime": version("onnxruntime"),
            "dimensions": 384, "input": "text", "max_tokens": self.max_tokens,
            "pooling": "attention-mask mean pooling; L2 normalized",
            "training": "pretrained; no task-specific training",
            "provider": "CPUExecutionProvider", "threads": 1,
            "weight_sha256": weight_sha256,
        }
        self.weight_bytes = (Path(model_path) / "model.onnx").stat().st_size
        self.setup_ms = (perf_counter() - started) * 1000

    def token_lengths(self, texts: Sequence[str]) -> list[int]:
        return [len(row.ids) for row in self.untruncated_tokenizer.encode_batch(list(texts))]

    def encode(self, texts: Sequence[str]):
        import numpy as np

        values = np.asarray(list(self.model.embed(list(texts))), dtype=np.float32)
        if values.shape != (len(texts), 384) or not np.isfinite(values).all():
            raise ValueError("encoder returned invalid embeddings")
        return values


def require_current(result: Mapping[str, Any], snapshot: Mapping[str, Any]) -> None:
    """Consumers must call this against their intended current source."""
    if any(value != snapshot.get(key) for key, value in result["source"].items()):
        raise ValueError("stale selection: rebuild against the intended source revision")


class SelectionIndex:
    """An ephemeral derived index. Rebuild fully after a source revision changes."""

    def __init__(self, snapshot: Mapping[str, Any], method: str, *, encoder=None):
        started = perf_counter()
        if method not in METHODS:
            raise ValueError(f"unknown selection method: {method}")
        self.method = method
        self.snapshot = deepcopy(dict(snapshot))
        self.source = {key: self.snapshot[key] for key in ("revision", "state_digest")}
        if not all(self.source.values()):
            raise ValueError("a source revision and state digest are required")
        self.source.update({key: self.snapshot[key] for key in ("record_digest", "step_sha256") if key in self.snapshot})
        self.entities = {row["id"]: row for row in self.snapshot["entities"]}
        if len(self.entities) != len(self.snapshot["entities"]):
            raise ValueError("duplicate entity refs")
        self.ids = sorted(self.entities)
        self.edges: list[dict] = []
        for family in ("relations", "dependencies"):
            for original in self.snapshot.get(family, []):
                row = deepcopy(original)
                edge_ref = row.get("id") or row.get("source_ref")
                if not edge_ref:
                    raise ValueError("edges must retain an existing source ref")
                if row["source"] not in self.entities or row["target"] not in self.entities:
                    raise ValueError("snapshot edge has unresolved entity refs")
                row["edge_ref"] = edge_ref
                row["family"] = family
                self.edges.append(row)
        self.edges.sort(key=lambda e: (e["family"], e["edge_ref"], e["source"], e["target"]))
        self.downstream: dict[str, set[str]] = {key: set() for key in self.ids}
        self.neighbors: dict[str, set[str]] = {key: set() for key in self.ids}
        for edge in self.edges:
            self.neighbors[edge["source"]].add(edge["target"])
            self.neighbors[edge["target"]].add(edge["source"])
            if edge["family"] == "dependencies":
                self.downstream[edge["source"]].add(edge["target"])
        # Both ranking methods receive exactly this deterministic text projection.
        self.documents = [self._document(key) for key in self.ids]
        self.term_counts = [Counter(_tokens(doc)) for doc in self.documents]
        self.document_frequency = Counter(term for counts in self.term_counts for term in counts)
        self.average_length = sum(map(lambda c: sum(c.values()), self.term_counts)) / max(1, len(self.ids))
        self.encoder = encoder
        self.vectors = None
        self.input_truncation = None
        if method == "embedding_graph":
            if encoder is None:
                raise ValueError("embedding_graph requires a real encoder; no synthetic fallback")
            self.vectors = encoder.encode(self.documents)
            token_lengths = encoder.token_lengths(self.documents)
            self.input_truncation = {
                "max_tokens": encoder.max_tokens,
                "untruncated_document_tokens": dict(zip(self.ids, token_lengths)),
                "truncated_entity_refs": [key for key, length in zip(self.ids, token_lengths) if length > encoder.max_tokens],
            }
        self.cost = {
            "index_ms": (perf_counter() - started) * 1000,
            "entity_count": len(self.ids), "document_bytes": sum(len(d.encode()) for d in self.documents),
            "vector_bytes": 0 if self.vectors is None else int(self.vectors.nbytes),
            "api_calls": 0, "actual_api_charge_usd": 0,
            "electricity_and_hardware_cost": "not measured",
        }

    def _document(self, key: str) -> str:
        entity = self.entities[key]
        incident = [{"family": e["family"], "kind": e.get("kind"),
                     "source": e["source"], "target": e["target"]}
                    for e in self.edges if key in (e["source"], e["target"])]
        return _json({"entity": entity, "incident_edges": incident})

    def dependency_closure(self, target_refs: Sequence[str]) -> tuple[str, ...]:
        """Traverse declared downstream edges; never claim undeclared impact."""
        seen = set(target_refs)
        pending = deque(sorted(seen))
        ordered = []
        while pending:
            key = pending.popleft()
            ordered.append(key)
            for child in sorted(self.downstream[key] - seen):
                seen.add(child)
                pending.append(child)
        return tuple(ordered)

    def _lexical_scores(self, text: str) -> dict[str, float]:
        scores = {}
        for key, counts in zip(self.ids, self.term_counts):
            length = sum(counts.values())
            score = 0.0
            for term in set(_tokens(text)):
                frequency = counts[term]
                if frequency:
                    df = self.document_frequency[term]
                    idf = math.log(1 + (len(self.ids) - df + .5) / (df + .5))
                    score += idf * frequency * 2.2 / (
                        frequency + 1.2 * (.25 + .75 * length / max(1, self.average_length)))
            scores[key] = score
        return scores

    def _graph_rank(self, query: Query) -> list[str]:
        if query.operation == "impact":
            return list(self.dependency_closure(query.target_refs))
        neighbors = set().union(*(self.neighbors[key] for key in query.target_refs)) if query.target_refs else set()
        if query.operation == "similar":
            # Simple declared-producer/parameter-key baseline, not shape matching.
            signatures = {self._signature(self.entities[key]) for key in query.target_refs}
            neighbors |= {key for key in self.ids if self._signature(self.entities[key]) in signatures}
        return list(query.target_refs) + sorted(neighbors - set(query.target_refs))

    @staticmethod
    def _signature(entity: Mapping[str, Any]) -> tuple:
        metadata = entity.get("metadata", {})
        return (str(metadata.get("producer", entity.get("producer", ""))),
                tuple(sorted(entity.get("params", {}))))

    def select(self, query: Query, *, revision: str, state_digest: str, budget: int) -> dict:
        started = perf_counter()
        if self.source["revision"] != revision or self.source["state_digest"] != state_digest:
            raise ValueError("stale index: rebuild against the intended source revision")
        if budget < 1 or len(set(query.target_refs)) > budget:
            raise ValueError("budget must be positive and fit the explicitly named targets")
        if set(query.target_refs) - self.entities.keys():
            raise ValueError("query names entity refs absent from this source")
        if query.operation not in ("inspect", "impact", "similar", "change"):
            raise ValueError("unsupported query operation")
        seeds = list(dict.fromkeys(query.target_refs))
        scores: dict[str, float] = {}
        if self.method == "full":
            ranked = seeds + [key for key in self.ids if key not in seeds]
        elif self.method == "graph":
            ranked = self._graph_rank(query)
        elif self.method == "lexical":
            scores = self._lexical_scores(query.text)
            ranked = seeds + sorted(self.ids, key=lambda key: (-scores[key], key))
        else:
            import numpy as np

            vector = self.encoder.encode([query.text])[0]
            similarities = self.vectors @ vector / np.maximum(
                np.linalg.norm(self.vectors, axis=1) * np.linalg.norm(vector), 1e-12)
            scores = {key: float(value) for key, value in zip(self.ids, similarities)}
            candidates = [key for key in sorted(self.ids, key=lambda key: (-scores[key], key)) if key not in seeds]
            # Fixed before held-out runs: half the remaining slots seed retrieval;
            # the rest expose explicit graph links. No relevance labels tune this.
            retrieved = candidates[:max(1, (budget - len(seeds)) // 2)]
            graph_seeds = seeds + retrieved
            expanded = list(self.dependency_closure(graph_seeds)) if query.operation == "impact" else sorted(
                set().union(*(self.neighbors[key] for key in graph_seeds)) - set(graph_seeds))
            ranked = graph_seeds + expanded + candidates
        ranked = list(dict.fromkeys(ranked))
        # Full is the explicitly labelled full-context cost/coverage reference.
        selected = ranked if self.method == "full" else ranked[:budget]
        selected_set = set(selected)
        edges = [deepcopy(edge) for edge in self.edges
                 if {edge["source"], edge["target"]} <= selected_set]
        closure = self.dependency_closure(seeds) if query.operation == "impact" else ()
        missing = sorted(set(closure) - selected_set)
        payload = {
            "source": deepcopy(self.source), "units": self.snapshot["units"],
            "coordinate_system": self.snapshot["coordinate_system"],
            "entities": [deepcopy(self.entities[key]) for key in selected], "edges": edges,
        }
        result = {
            "source": deepcopy(self.source), "method": self.method, "version": METHOD_VERSION,
            "encoder": None if self.encoder is None else deepcopy(self.encoder.identity),
            "entity_refs": selected, "edge_refs": sorted({edge["edge_ref"] for edge in edges}),
            "context": payload,
            "coverage": {
                "entity_budget": budget, "full_reference_exceeds_budget": self.method == "full" and len(selected) > budget,
                "omitted_entity_refs": sorted(set(self.ids) - selected_set),
                "missing_declared_dependency_refs": missing,
                "complete_declared_impact_context": query.operation == "impact" and not missing,
                "limits": "Only recorded edges are known. Similarity/proximity does not establish geometry or causality. Selection does not replace validation.",
            },
            "ranking_scores": scores,
            "cost": {"query_ms": (perf_counter() - started) * 1000,
                     "context_bytes": len(_json(payload).encode()), "api_calls": 0,
                     "actual_api_charge_usd": 0},
        }
        return result


def retrieval_metrics(result: Mapping[str, Any], *, relevant_entities: Sequence[str],
                      relevant_edges: Sequence[str] = (), critical_entities: Sequence[str] = ()) -> dict:
    """Evaluator only: these audited labels never enter SelectionIndex or Query."""
    selected, relevant = set(result["entity_refs"]), set(relevant_entities)
    edges, gold_edges = set(result["edge_refs"]), set(relevant_edges)
    return {
        "entity_precision": len(selected & relevant) / len(selected) if selected else 0,
        "entity_recall": len(selected & relevant) / len(relevant) if relevant else 1,
        "edge_precision": len(edges & gold_edges) / len(edges) if edges else (1 if not gold_edges else 0),
        "edge_recall": len(edges & gold_edges) / len(gold_edges) if gold_edges else 1,
        "missed_critical_refs": sorted(set(critical_entities) - selected),
        "missed_relevant_refs": sorted(relevant - selected),
        "false_inferred_links": 0,  # selectors emit source edges only, never similarity edges
    }


def audited_tasks() -> tuple[dict, ...]:
    """Frozen authored query/gold split; selection methods never read this gold.

    Audit these explicit facts against fixture.py/fixture_protocol.md before
    running. Held-out means no tuning on these query/edit pairs, not unseen
    buildings and not independent human approval of architectural relevance.
    """
    chain = ("screen", "lintel", "seal", "fixing", "drain")
    chain_edges = tuple(f"relation:{a}-to-{b}" for a, b in zip(chain, chain[1:]))
    return (
        {"id": "dev-screen-impact", "split": "development", "snapshot": "base",
         "query": Query("Which declared objects require checking when the south screen changes?", ("screen",), "impact"),
         "gold": {"relevant_entities": chain, "relevant_edges": chain_edges, "critical_entities": ("drain",)}},
        {"id": "dev-marker-b-impact", "split": "development", "snapshot": "base",
         "query": Query("Check the effect of moving square marker B on its declared dependents.", ("twin-b",), "impact"),
         "gold": {"relevant_entities": ("twin-b", "drain"), "relevant_edges": ("relation:twin-b-to-drain",), "critical_entities": ("drain",)}},
        {"id": "dev-unknown-ref", "split": "development", "snapshot": "base",
         "query": Query("Inspect the dimensions and recorded role of this unclassified object.", ("mystery",)),
         "gold": {"relevant_entities": ("mystery",)}},
        {"id": "heldout-head-member-impact", "split": "heldout", "snapshot": "heldout",
         "query": Query("After altering the head member, enumerate every declared downstream item to revalidate.", ("lintel",), "impact"),
         "gold": {"relevant_entities": chain[1:], "relevant_edges": chain_edges[1:], "critical_entities": ("drain",)}},
        {"id": "heldout-marker-a-impact", "split": "heldout", "snapshot": "heldout",
         "query": Query("Does square marker A have the same downstream effect as square marker B? Show A's declared impact.", ("twin-a",), "impact"),
         "gold": {"relevant_entities": ("twin-a",)}},
        {"id": "heldout-shell-suspicion", "split": "heldout", "snapshot": "heldout",
         "query": Query("Could the freestanding insert collide with this U shaped shell? Find the pair for an exact solid-distance check.", ("u-shell",)),
         "gold": {"relevant_entities": ("u-shell", "insert"), "critical_entities": ("insert",)},
         "exact_check": {"action": "pair", "args": {"first": "u-shell", "second": "insert"}}},
        {"id": "heldout-unknown-search", "split": "heldout", "snapshot": "heldout",
         "query": Query("Find the unclassified object whose role remains unknown."),
         "gold": {"relevant_entities": ("mystery",)}},
        {"id": "heldout-geometric-vs-causal", "split": "heldout", "snapshot": "heldout",
         "query": Query("Find square marker B similar to square marker A, and inspect the recorded connection to the weep outlet.", ("twin-a",), "similar"),
         "gold": {"relevant_entities": ("twin-a", "twin-b", "drain"),
                  "relevant_edges": ("relation:twin-b-to-drain",), "critical_entities": ("drain",)}},
    )


def run_benchmark(snapshots: Mapping[str, Mapping[str, Any]], *, encoder=None,
                  budget: int = 6) -> dict:
    """Return measurements to caller; no filesystem, model-provider or CAD calls.

    Encoder preparation is external. Index build and full update are separate
    timed operations. This measures retrieval, not downstream answer quality.
    """
    tasks = audited_tasks()
    methods = METHODS if encoder is not None else METHODS[:-1]
    report: dict[str, Any] = {
        "protocol": METHOD_VERSION, "budget": budget, "methods": {}, "rows": [],
        "embedding_status": "executed" if encoder is not None else "unavailable: caller supplied no real encoder",
        "gold_status": "authored synthetic facts; independently reviewable; no external human relevance audit claimed",
        "cost_scope": "retrieval only; downstream provider, exact-query and fixture costs accounted separately",
        "encoder_once": None if encoder is None else {"identity": encoder.identity,
            "setup_ms": encoder.setup_ms, "weight_bytes": encoder.weight_bytes,
            "download_ms": None, "download_note": "prepared separately; not counted as indexing or zero-cost download"},
    }
    for method in methods:
        indexes = {}
        for name in ("base", "heldout"):
            indexes[name] = SelectionIndex(snapshots[name], method, encoder=encoder if method == "embedding_graph" else None)
        report["methods"][method] = {
            "initial_index": indexes["base"].cost,
            "full_rebuild_update": indexes["heldout"].cost,
            "update_policy": "full rebuild, no incremental cache",
            "encoder_input_truncation": indexes["base"].input_truncation,
        }
        for task in tasks:
            name, query = task["snapshot"], task["query"]
            snapshot = snapshots[name]
            if set(task["gold"]["relevant_entities"]) - indexes[name].entities.keys():
                raise ValueError("frozen audit references absent fixture entities")
            result = indexes[name].select(query, revision=snapshot["revision"],
                                          state_digest=snapshot["state_digest"], budget=budget)
            report["rows"].append({"task_id": task["id"], "split": task["split"],
                "query": {"text": query.text, "target_refs": query.target_refs, "operation": query.operation},
                "gold": deepcopy(task["gold"]), "result": result,
                "metrics": retrieval_metrics(result, **task["gold"]),
                "exact_check": task.get("exact_check")})
        checks_started = perf_counter()
        source = snapshots["base"]
        test_query = Query("changed screen", ("screen",), "impact")
        old = indexes["base"].select(test_query, revision=source["revision"], state_digest=source["state_digest"], budget=budget)
        stale_rejected = False
        try:
            require_current(old, snapshots["heldout"])
        except ValueError:
            stale_rejected = True
        index_rejected = False
        try:
            indexes["base"].select(test_query, revision=snapshots["heldout"]["revision"],
                                   state_digest=snapshots["heldout"]["state_digest"], budget=budget)
        except ValueError:
            index_rejected = True
        updated = indexes["heldout"].select(test_query, revision=snapshots["heldout"]["revision"],
                                           state_digest=snapshots["heldout"]["state_digest"], budget=budget)
        require_current(updated, snapshots["heldout"])
        reordered = deepcopy(dict(source))
        for key in ("entities", "relations", "dependencies"):
            reordered[key].reverse()
        reordered_index = SelectionIndex(reordered, method, encoder=encoder if method == "embedding_graph" else None)
        invariant = reordered_index.select(test_query, revision=source["revision"], state_digest=source["state_digest"], budget=budget)
        report["methods"][method]["checks"] = {
            "stale_result_rejected": stale_rejected, "stale_index_rejected": index_rejected,
            "rebuild_valid_for_updated_source": True,
            "changed_content_propagated": old["context"]["entities"] != updated["context"]["entities"],
            "entity_order_invariant": old["entity_refs"] == invariant["entity_refs"],
            "context_order_invariant": old["context"] == invariant["context"],
            "verification_ms": (perf_counter() - checks_started) * 1000,
        }
    return report
