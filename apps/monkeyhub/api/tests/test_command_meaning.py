"""The Hub's meaning search over its commands (08 提速, 向量检索)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from monkeyhub_api.commands.meaning import (
    Encoder, MeaningHit, MeaningIndex, WordPiece, command_texts, model_dir, route, vectors_name,
)
from monkeyhub_api.commands.routes import CommandMeaning, commands_router

ROOT = Path(__file__).resolve().parents[4]
CASES = json.loads((ROOT / "governance" / "command_meaning_cases.json").read_text(encoding="utf-8"))


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


MODEL = model_dir()


class CommandMeaningTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp_path = Path(temp.name)

    def test_the_tokenizer_splits_chinese_by_character_and_latin_into_word_pieces(self):
        vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "推", "墙", "export", "mod", "##el", ",", "3", "##dm"]
        tokenizer = WordPiece(vocab)
        ids = tokenizer.encode("推墙, Export Model 3dm ?")
        self.assertEqual([vocab[i] for i in ids], ["[CLS]", "推", "墙", ",", "export", "mod", "##el", "3", "##dm", "[UNK]", "[SEP]"])

    def test_a_sentence_is_offered_only_when_its_meaning_is_clear_and_a_question_needs_more(self):
        hit = lambda score, command="draft.tools.push_pull": MeaningHit(command, score, "把墙往外推一点")  # noqa: E731
        self.assertEqual(route("把这面墙再往外挪一些", [hit(0.79), hit(0.70, "view.canvas.zoom_in")]).command, "draft.tools.push_pull")
        self.assertIsNone(route("把这面墙再往外挪一些", [hit(0.79), hit(0.77, "view.canvas.zoom_in")]), "two commands read alike: ask")
        self.assertIsNone(route("把这面墙再往外挪一些", [hit(0.65), hit(0.50, "view.canvas.zoom_in")]))
        self.assertIsNone(route("帮我想想入口怎么设计", [hit(0.80), hit(0.60, "view.canvas.zoom_in")]), "an open question goes to the conversation")
        self.assertEqual(route("这根梁是什么构件", [hit(0.90, "component.inspect.open"), hit(0.70)]).command, "component.inspect.open")
        self.assertIsNone(route("", []))

    def test_a_command_is_indexed_by_its_names_and_every_written_phrasing_once(self):
        registry = {"capabilities": [{"ui": {"commands": [
            {"id": "draft.tools.push_pull", "label": "Push/pull", "label_zh": "推拉", "aliases": ["推拉"]},
            {"label": "no id"}]}}]}
        phrases = {"draft.tools.push_pull": {"zh": ["把墙往外推一点"], "en": ["push a face"], "aliases_zh": ["推拉", "往外推"]}}
        self.assertEqual(command_texts(registry, phrases), [("draft.tools.push_pull", "推拉"), ("draft.tools.push_pull", "Push/pull"),
                                                            ("draft.tools.push_pull", "把墙往外推一点"), ("draft.tools.push_pull", "push a face"),
                                                            ("draft.tools.push_pull", "往外推")])

    def test_vectors_are_computed_once_and_then_read_from_the_cache(self):
        tmp_path = self.tmp_path
        texts = [("a.one", "第一个"), ("a.one", "first"), ("b.two", "第二个")]
        encoder = HashEncoder()
        first = MeaningIndex(encoder, texts, caches=[tmp_path / "shipped", tmp_path / "cache"])
        self.assertTrue((tmp_path / "cache" / vectors_name(encoder, texts)).is_file())
        again = HashEncoder()
        second = MeaningIndex(again, texts, caches=[tmp_path / "shipped", tmp_path / "cache"])
        self.assertEqual(again.encoded, 0)
        self.assertTrue(np.array_equal(first.vectors, second.vectors))
        hits = second.search("first", 5)
        self.assertEqual([hit.command for hit in hits], ["a.one", "b.two"], "each command once, closest first")
        self.assertEqual(hits[0].text, "first")
        self.assertAlmostEqual(hits[0].score, 1.0, places=5)
        self.assertEqual([hit.command for hit in second.search("first", 5, allowed={"b.two"})], ["b.two"])

    def test_without_a_model_the_endpoint_says_so_and_keyword_search_stays_the_only_one(self):
        environ = {key: value for key, value in os.environ.items() if key != "MONKEYHUB_EMBEDDING_MODEL"}
        with patch.dict(os.environ, environ, clear=True):
            meaning = CommandMeaning(ROOT, self.tmp_path, model=None)
            meaning.model, meaning.failed = None, True
            app = FastAPI()
            app.include_router(commands_router(meaning))
            body = TestClient(app).post("/api/commands/meaning", json={"text": "把墙往外推"}).json()
        self.assertEqual(body, {"state": "unavailable", "hits": [], "route": None})

    @unittest.skipIf(MODEL is None, "the embedding model is fetched by a release or named by MONKEYHUB_EMBEDDING_MODEL")
    def test_sentences_nobody_wrote_down_find_their_command_and_open_questions_find_none(self):
        registry = json.loads((ROOT / "governance" / "module_registry.json").read_text(encoding="utf-8"))
        phrases = json.loads((ROOT / "governance" / "command_phrases.json").read_text(encoding="utf-8"))["commands"]
        written = {text for said in phrases.values() for key in ("zh", "en", "aliases_zh", "aliases_en") for text in said.get(key, ())}
        index = MeaningIndex(Encoder(MODEL), command_texts(registry, phrases), caches=[Path(os.environ.get("MONKEYHUB_COMMAND_VECTORS", self.tmp_path))])
        first = routed = wrong = 0
        started = time.perf_counter()
        for case in CASES["finds"]:
            self.assertNotIn(case["say"], written, f"{case['say']!r} is a written phrasing, not a held-out sentence")
            hits = index.search(case["say"], 3)
            first += hits[0].command in case["any"]
            chosen = route(case["say"], hits)
            routed += bool(chosen and chosen.command in case["any"])
            wrong += bool(chosen and chosen.command not in case["any"])
        elapsed = (time.perf_counter() - started) / len(CASES["finds"])
        offered = [say for say in CASES["asks"] if route(say, index.search(say, 3))]
        self.assertEqual(offered, [], "an open question must go to the conversation")
        self.assertEqual(wrong, 0)
        self.assertGreaterEqual(routed, 24, f"{routed} of {len(CASES['finds'])} sentences offered their command")
        self.assertGreaterEqual(first, 33, f"the right command came first for {first} of {len(CASES['finds'])}")
        self.assertLess(elapsed, 0.5, f"{elapsed * 1000:.0f} ms a sentence")


if __name__ == "__main__":
    unittest.main()
