"""The Hub's meaning search over its commands (08 提速, 向量检索)."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from monkeyhub_api.commands.meaning import (
    Encoder, MeaningHit, MeaningIndex, WordPiece, command_texts, model_dir, route, vectors_name,
)
from monkeyhub_api.commands.routes import CommandMeaning, commands_router

ROOT = Path(__file__).resolve().parents[4]
CASES = json.loads((ROOT / "governance" / "command_meaning_cases.json").read_text(encoding="utf-8"))


def test_the_tokenizer_splits_chinese_by_character_and_latin_into_word_pieces():
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "推", "墙", "export", "mod", "##el", ",", "3", "##dm"]
    tokenizer = WordPiece(vocab)
    ids = tokenizer.encode("推墙, Export Model 3dm ?")
    assert [vocab[i] for i in ids] == ["[CLS]", "推", "墙", ",", "export", "mod", "##el", "3", "##dm", "[UNK]", "[SEP]"]


def test_a_sentence_is_offered_only_when_its_meaning_is_clear_and_a_question_needs_more():
    hit = lambda score, command="draft.tools.push_pull": MeaningHit(command, score, "把墙往外推一点")  # noqa: E731
    assert route("把这面墙再往外挪一些", [hit(0.79), hit(0.70, "view.canvas.zoom_in")]).command == "draft.tools.push_pull"
    assert route("把这面墙再往外挪一些", [hit(0.79), hit(0.77, "view.canvas.zoom_in")]) is None, "two commands read alike: ask"
    assert route("把这面墙再往外挪一些", [hit(0.65), hit(0.50, "view.canvas.zoom_in")]) is None
    assert route("帮我想想入口怎么设计", [hit(0.80), hit(0.60, "view.canvas.zoom_in")]) is None, "an open question goes to the conversation"
    assert route("这根梁是什么构件", [hit(0.90, "component.inspect.open"), hit(0.70)]).command == "component.inspect.open"
    assert route("", []) is None


def test_a_command_is_indexed_by_its_names_and_every_written_phrasing_once():
    registry = {"capabilities": [{"ui": {"commands": [
        {"id": "draft.tools.push_pull", "label": "Push/pull", "label_zh": "推拉", "aliases": ["推拉"]},
        {"label": "no id"}]}}]}
    phrases = {"draft.tools.push_pull": {"zh": ["把墙往外推一点"], "en": ["push a face"], "aliases_zh": ["推拉", "往外推"]}}
    assert command_texts(registry, phrases) == [("draft.tools.push_pull", "推拉"), ("draft.tools.push_pull", "Push/pull"),
                                                ("draft.tools.push_pull", "把墙往外推一点"), ("draft.tools.push_pull", "push a face"),
                                                ("draft.tools.push_pull", "往外推")]


class HashEncoder:
    """A stand-in model: equal texts get equal vectors, so search and caching are tested without the weights."""

    identity = "hash"
    weights = {"embeddings.word_embeddings.weight": np.zeros((1, 8), np.float32)}

    def __init__(self):
        self.encoded = 0

    def encode(self, texts):
        self.encoded += len(texts)
        rows = [np.frombuffer(hashlib.sha256(text.encode()).digest()[:8], np.uint8).astype(np.float32) - 127.5 for text in texts]
        out = np.array(rows, np.float32)
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def test_vectors_are_computed_once_and_then_read_from_the_cache(tmp_path):
    texts = [("a.one", "第一个"), ("a.one", "first"), ("b.two", "第二个")]
    encoder = HashEncoder()
    first = MeaningIndex(encoder, texts, caches=[tmp_path / "shipped", tmp_path / "cache"])
    assert (tmp_path / "cache" / vectors_name(encoder, texts)).is_file()
    again = HashEncoder()
    second = MeaningIndex(again, texts, caches=[tmp_path / "shipped", tmp_path / "cache"])
    assert again.encoded == 0 and np.array_equal(first.vectors, second.vectors)
    hits = second.search("first", 5)
    assert [hit.command for hit in hits] == ["a.one", "b.two"], "each command once, closest first"
    assert hits[0].text == "first" and hits[0].score == pytest.approx(1.0)
    assert [hit.command for hit in second.search("first", 5, allowed={"b.two"})] == ["b.two"]


def test_without_a_model_the_endpoint_says_so_and_keyword_search_stays_the_only_one(tmp_path, monkeypatch):
    monkeypatch.delenv("MONKEYHUB_EMBEDDING_MODEL", raising=False)
    meaning = CommandMeaning(ROOT, tmp_path, model=None)
    meaning.model, meaning.failed = None, True
    app = FastAPI()
    app.include_router(commands_router(meaning))
    body = TestClient(app).post("/api/commands/meaning", json={"text": "把墙往外推"}).json()
    assert body == {"state": "unavailable", "hits": [], "route": None}


MODEL = model_dir()


@pytest.mark.skipif(MODEL is None, reason="the embedding model is fetched by a release or named by MONKEYHUB_EMBEDDING_MODEL")
def test_sentences_nobody_wrote_down_find_their_command_and_open_questions_find_none(tmp_path):
    registry = json.loads((ROOT / "governance" / "module_registry.json").read_text(encoding="utf-8"))
    phrases = json.loads((ROOT / "governance" / "command_phrases.json").read_text(encoding="utf-8"))["commands"]
    written = {text for said in phrases.values() for key in ("zh", "en", "aliases_zh", "aliases_en") for text in said.get(key, ())}
    index = MeaningIndex(Encoder(MODEL), command_texts(registry, phrases), caches=[Path(os.environ.get("MONKEYHUB_COMMAND_VECTORS", tmp_path))])
    first = routed = wrong = 0
    started = time.perf_counter()
    for case in CASES["finds"]:
        assert case["say"] not in written, f"{case['say']!r} is a written phrasing, not a held-out sentence"
        hits = index.search(case["say"], 3)
        first += hits[0].command in case["any"]
        chosen = route(case["say"], hits)
        routed += bool(chosen and chosen.command in case["any"])
        wrong += bool(chosen and chosen.command not in case["any"])
    elapsed = (time.perf_counter() - started) / len(CASES["finds"])
    offered = [say for say in CASES["asks"] if route(say, index.search(say, 3))]
    assert offered == [], "an open question must go to the conversation"
    assert wrong == 0
    assert routed >= 24, f"{routed} of {len(CASES['finds'])} sentences offered their command"
    assert first >= 33, f"the right command came first for {first} of {len(CASES['finds'])}"
    assert elapsed < 0.5, f"{elapsed * 1000:.0f} ms a sentence"
