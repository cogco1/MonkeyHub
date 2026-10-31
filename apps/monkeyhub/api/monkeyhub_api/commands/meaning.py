"""Find a command by what a sentence means, not by its words (08 提速, 向量检索).

Every command's name, aliases and written phrasings (``governance/command_phrases.json``) are
turned into vectors once by a small local embedding model and kept in memory; a sentence is
turned into a vector the same way and compared with them. "把这面墙往外推一点" finds the push/pull
tool although no phrasing says it that way. Nothing goes over the network and no database runs:
the model is a file beside the Hub (BAAI/bge-small-zh-v1.5, MIT, four BERT layers), run here with
numpy, which the Hub already ships. Without the model the index is simply unavailable and the
keyword search the web already has stays the only one.

A score is how alike two sentences read, nothing more: the web keeps a threshold and falls back
to its keyword search below it, and a command only runs after the person confirms it.
"""

from __future__ import annotations

import ctypes
import glob
import hashlib
import json
import os
import re
import struct
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

MODEL_NAME = "bge-small-zh-v1.5"
# The pinned files: a release fetches exactly these (tools/release/package_monkeyapps.py), and the Hub
# refuses a model whose weights differ, so a changed file cannot quietly change what a sentence finds.
MODEL_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
MODEL_FILES = {
    "config.json": None,
    "vocab.txt": "45bbac6b341c319adc98a532532882e91a9cefc0329aa57bac9ae761c27b291c",
    "model.safetensors": "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026",
}
# Where the model is looked for: an explicit directory, then beside the installed bundle.
MODEL_ENV = "MONKEYHUB_EMBEDDING_MODEL"
MAX_TOKENS = 64

# When a sentence is offered as a command (governance/command_meaning_cases.json sets these): its closest command
# reads at least ROUTE_SCORE alike and leads the next one by ROUTE_LEAD; a sentence shaped as a question
# ("…吗", "怎么…", "why …") needs QUESTION_SCORE, since most open questions are asked that way.
ROUTE_SCORE, ROUTE_LEAD, QUESTION_SCORE = 0.70, 0.04, 0.88
_QUESTION = re.compile(r"[?？]|怎么|为什么|为何|吗|什么|哪|多少|如何|能不能|是否|好不好|够不够|行不行|对不对|觉得|帮我想|帮我写|讲讲|介绍|总结|分析|建议"
                       r"|(what|why|how|should|which|could|would)|^(is|are|do|does|can)", re.IGNORECASE)


def model_dir(start: Path | None = None) -> Path | None:
    """The embedding model's directory, or ``None`` when this installation has none."""

    explicit = os.environ.get(MODEL_ENV)
    if explicit:
        path = Path(explicit)
        return path if (path / "model.safetensors").is_file() else None
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        candidate = parent / "_runtime" / "models" / MODEL_NAME
        if (candidate / "model.safetensors").is_file():
            return candidate
    return None


# ---- tokenizer: BERT's basic split, then WordPiece over the model's vocabulary ----

def _is_cjk(code: int) -> bool:
    return (0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0x20000 <= code <= 0x2A6DF or 0x2A700 <= code <= 0x2CEAF
            or 0xF900 <= code <= 0xFAFF or 0x2F800 <= code <= 0x2FA1F)


def _is_punctuation(character: str) -> bool:
    code = ord(character)
    if 33 <= code <= 47 or 58 <= code <= 64 or 91 <= code <= 96 or 123 <= code <= 126:
        return True
    return unicodedata.category(character).startswith("P")


class WordPiece:
    """The model's tokenizer (BertTokenizer); Latin text is lowercased, as the vocabulary only holds lowercase words."""

    def __init__(self, vocab: Sequence[str]):
        self.ids = {token: index for index, token in enumerate(vocab)}
        self.unk, self.cls, self.sep = self.ids["[UNK]"], self.ids["[CLS]"], self.ids["[SEP]"]

    def _words(self, text: str) -> list[str]:
        spaced: list[str] = []
        for character in text:
            code = ord(character)
            if code == 0 or code == 0xFFFD or unicodedata.category(character) in ("Cc", "Cf") and character not in "\t\n\r":
                continue
            if character.isspace():
                spaced.append(" ")
            elif _is_cjk(code) or _is_punctuation(character):
                spaced.append(f" {character} ")
            else:
                spaced.append(character)
        return "".join(spaced).split()

    def _pieces(self, word: str) -> list[int]:
        if len(word) > 100:
            return [self.unk]
        pieces, start = [], 0
        while start < len(word):
            end, found = len(word), None
            while start < end:
                piece = word[start:end] if start == 0 else f"##{word[start:end]}"
                if piece in self.ids:
                    found = self.ids[piece]
                    break
                end -= 1
            if found is None:
                return [self.unk]
            pieces.append(found)
            start = end
        return pieces

    def encode(self, text: str) -> list[int]:
        ids = [piece for word in self._words(unicodedata.normalize("NFKC", text).lower()) for piece in self._pieces(word)]
        return [self.cls, *ids[:MAX_TOKENS - 2], self.sep]


# ---- the model: four BERT layers in numpy, the [CLS] vector, unit length ----

_DTYPES = {"F32": np.float32, "F16": np.float16, "I64": np.int64}


def _safetensors(path: Path) -> dict[str, np.ndarray]:
    with path.open("rb") as handle:
        size = struct.unpack("<Q", handle.read(8))[0]
        header = json.loads(handle.read(size))
        body = handle.read()
    header.pop("__metadata__", None)
    tensors = {}
    for name, entry in header.items():
        begin, end = entry["data_offsets"]
        dtype = np.dtype(_DTYPES[entry["dtype"]])
        tensors[name] = np.frombuffer(body, dtype=dtype, count=(end - begin) // dtype.itemsize, offset=begin).reshape(entry["shape"])
    return tensors


def _layer_norm(x: np.ndarray, weight: np.ndarray, bias: np.ndarray, eps: float) -> np.ndarray:
    mean = x.mean(-1, keepdims=True)
    variance = ((x - mean) ** 2).mean(-1, keepdims=True)
    return (x - mean) / np.sqrt(variance + eps) * weight + bias


def _erf(x: np.ndarray) -> np.ndarray:
    # Abramowitz and Stegun 7.1.26 (error under 1.5e-7): numpy has no erf, and BERT's GELU is the exact one.
    sign = np.sign(x)
    x = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return sign * y


class Encoder:
    """Sentences to unit vectors with the local model; one instance is shared, and runs one batch at a time."""

    def __init__(self, directory: Path, *, verify: bool = True):
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        if verify:
            for name, expected in MODEL_FILES.items():
                if expected and hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
                    raise ValueError(f"{directory / name} is not the pinned {MODEL_NAME} file")
        self.tokenizer = WordPiece((directory / "vocab.txt").read_text(encoding="utf-8").splitlines())
        self.weights = {name: value.astype(np.float32) for name, value in _safetensors(directory / "model.safetensors").items()
                        if value.dtype != np.int64}
        self.layers = int(config["num_hidden_layers"])
        self.heads = int(config["num_attention_heads"])
        self.eps = float(config.get("layer_norm_eps", 1e-12))
        self.identity = hashlib.sha256(json.dumps([MODEL_NAME, MODEL_REVISION, MAX_TOKENS]).encode()).hexdigest()[:16]
        self._lock = threading.Lock()

    def encode(self, texts: Sequence[str], batch: int = 32) -> np.ndarray:
        # Texts of like length share a batch, so little of each batch is padding.
        order = sorted(range(len(texts)), key=lambda index: len(texts[index]))
        out = np.zeros((len(texts), self.weights["embeddings.word_embeddings.weight"].shape[1]), np.float32)
        for start in range(0, len(order), batch):
            chosen = order[start:start + batch]
            out[chosen] = self._batch([texts[index] for index in chosen])
        return out

    def _batch(self, texts: Sequence[str]) -> np.ndarray:
        w = self.weights
        ids = [self.tokenizer.encode(text) for text in texts]
        length = max(len(row) for row in ids)
        tokens = np.zeros((len(ids), length), np.int64)
        mask = np.zeros((len(ids), length), np.float32)
        for row, values in enumerate(ids):
            tokens[row, :len(values)] = values
            mask[row, :len(values)] = 1.0
        with self._lock:
            x = w["embeddings.word_embeddings.weight"][tokens] + w["embeddings.position_embeddings.weight"][:length] \
                + w["embeddings.token_type_embeddings.weight"][0]
            x = _layer_norm(x, w["embeddings.LayerNorm.weight"], w["embeddings.LayerNorm.bias"], self.eps)
            size, width = x.shape[0], x.shape[2]
            head = width // self.heads
            blocked = (1.0 - mask)[:, None, None, :] * -1e9
            for index in range(self.layers):
                p = f"encoder.layer.{index}."
                def project(name: str, value: np.ndarray) -> np.ndarray:
                    return value @ w[p + name + ".weight"].T + w[p + name + ".bias"]
                def split(value: np.ndarray) -> np.ndarray:
                    return value.reshape(size, length, self.heads, head).transpose(0, 2, 1, 3)
                q, k, v = (split(project(f"attention.self.{name}", x)) for name in ("query", "key", "value"))
                scores = q @ k.transpose(0, 1, 3, 2) / np.sqrt(head) + blocked
                scores = np.exp(scores - scores.max(-1, keepdims=True))
                scores /= scores.sum(-1, keepdims=True)
                attended = (scores @ v).transpose(0, 2, 1, 3).reshape(size, length, width)
                x = _layer_norm(x + project("attention.output.dense", attended),
                                w[p + "attention.output.LayerNorm.weight"], w[p + "attention.output.LayerNorm.bias"], self.eps)
                inner = project("intermediate.dense", x)
                inner = 0.5 * inner * (1.0 + _erf(inner / np.sqrt(2.0)))
                x = _layer_norm(x + project("output.dense", inner), w[p + "output.LayerNorm.weight"], w[p + "output.LayerNorm.bias"], self.eps)
        cls = x[:, 0]
        return (cls / np.linalg.norm(cls, axis=1, keepdims=True)).astype(np.float32)


# ---- the index: what each command is called and how it is said, as vectors ----

def command_texts(registry: Mapping[str, Any], phrases: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Each command's names and phrasings, one (command id, text) per text, in registry order, repeats once."""

    out: list[tuple[str, str]] = []
    for entry in registry.get("capabilities") or ():
        for command in ((entry.get("ui") or {}).get("commands") or ()):
            if not isinstance(command, dict) or not command.get("id"):
                continue
            command_id = str(command["id"])
            said = phrases.get(command_id) or {}
            texts = [command.get("label_zh"), command.get("label"), *(command.get("aliases") or ()),
                     *(said.get("zh") or ()), *(said.get("en") or ()), *(said.get("aliases_zh") or ()), *(said.get("aliases_en") or ())]
            seen: set[str] = set()
            for text in texts:
                if isinstance(text, str) and text.strip() and text.strip() not in seen:
                    seen.add(text.strip())
                    out.append((command_id, text.strip()))
    return out


@dataclass(frozen=True)
class MeaningHit:
    command: str
    score: float
    text: str


def vectors_name(encoder: Encoder, texts: Sequence[tuple[str, str]]) -> str:
    """The file the vectors of these texts are kept in: a new model or a changed phrasing is a new file."""

    digest = hashlib.sha256(json.dumps([encoder.identity, list(texts)], ensure_ascii=False).encode()).hexdigest()[:24]
    return f"command-vectors-{digest}.npy"


class _BlasThreads:
    """At most a few BLAS threads while the index is built: numpy's default of one per core spins them against each
    other on a long batch (measured 10-24: over eight minutes on 24 cores, against 37 s on four)."""

    def __init__(self, limit: int = 4):
        self.limit, self.lib, self.before = limit, None, None
        for path in glob.glob(os.path.join(os.path.dirname(np.__file__) + ".libs", "*openblas*")):
            try:
                lib = ctypes.CDLL(path)
                if hasattr(lib, "scipy_openblas_set_num_threads64_"):
                    self.lib = lib
                    break
            except OSError:
                continue

    def __enter__(self) -> None:
        if self.lib is not None:
            self.before = self.lib.scipy_openblas_get_num_threads64_()
            self.lib.scipy_openblas_set_num_threads64_(min(self.limit, self.before))

    def __exit__(self, *_: object) -> None:
        if self.lib is not None and self.before:
            self.lib.scipy_openblas_set_num_threads64_(self.before)


def _keep_vectors(target: Path, vectors: np.ndarray) -> None:
    """Keep computed vectors in the Hub's cache, whole or not at all, for the next start to read."""

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial.npy")
    np.save(partial, vectors)
    os.replace(partial, target)


class MeaningIndex:
    """The commands' texts as vectors in memory; a search compares one sentence with all of them.

    Vectors are read from the first of ``caches`` that holds them (a release ships them beside the model) and
    otherwise computed once and written to the last.
    """

    def __init__(self, encoder: Encoder, texts: Sequence[tuple[str, str]], caches: Sequence[Path] = ()):
        self.encoder = encoder
        self.commands = [command for command, _ in texts]
        self.texts = [text for _, text in texts]
        name = vectors_name(encoder, texts)
        vectors = None
        for cache in caches:
            try:
                found = np.load(cache / name)
            except (OSError, ValueError):
                continue
            if found.shape == (len(self.texts), encoder.weights["embeddings.word_embeddings.weight"].shape[1]):
                vectors = found.astype(np.float32)
                break
        if vectors is None:
            with _BlasThreads():
                vectors = encoder.encode(self.texts)
            if caches:
                _keep_vectors(caches[-1] / name, vectors)
        self.vectors = vectors

    def search(self, sentence: str, limit: int = 5, allowed: Iterable[str] | None = None) -> list[MeaningHit]:
        """The commands whose texts read most like the sentence, each once, with its closest text."""

        if not sentence.strip() or not self.texts:
            return []
        scores = self.vectors @ self.encoder.encode([sentence])[0]
        keep = set(allowed) if allowed is not None else None
        best: dict[str, MeaningHit] = {}
        for index in np.argsort(-scores):
            command = self.commands[index]
            if command in best or (keep is not None and command not in keep):
                continue
            best[command] = MeaningHit(command, float(scores[index]), self.texts[index])
            if len(best) >= limit:
                break
        return list(best.values())


def route(sentence: str, hits: Sequence[MeaningHit]) -> MeaningHit | None:
    """The command a sentence asks for, when its meaning is clear enough to offer it; otherwise none."""

    if not hits:
        return None
    first, lead = hits[0], hits[0].score - (hits[1].score if len(hits) > 1 else 0.0)
    needed = QUESTION_SCORE if _QUESTION.search(sentence) else ROUTE_SCORE
    return first if first.score >= needed and lead >= ROUTE_LEAD else None
