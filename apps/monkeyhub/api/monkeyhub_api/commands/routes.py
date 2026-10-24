"""The Hub's meaning search over its commands: one index per Hub, built in the background once."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .meaning import Encoder, MeaningIndex, command_texts, model_dir, route

LOG = logging.getLogger(__name__)
REGISTRY = Path("governance") / "module_registry.json"
PHRASES = Path("governance") / "command_phrases.json"


class CommandMeaningRequest(BaseModel):
    #: What the person typed; sent in the body so it stays out of request logs.
    text: str = Field(max_length=200)
    limit: int = Field(default=5, ge=1, le=20)


class MeaningHitDto(BaseModel):
    id: str
    score: float
    #: The written name or phrasing of the command the sentence read most like.
    text: str


class CommandMeaningDto(BaseModel):
    #: ``unavailable``: this installation has no embedding model; ``loading``: the index is still being built.
    state: Literal["ready", "loading", "unavailable"]
    hits: list[MeaningHitDto]
    #: The command to offer for the whole sentence, when its meaning is clear enough; the person still confirms it.
    route: str | None = None


class CommandMeaning:
    """Loads the model and builds the index on first use, off the request thread; searches once it is there."""

    def __init__(self, source_root: Path, cache_root: Path, model: Path | None = None):
        self.source_root, self.cache_root = source_root, cache_root
        self.model = model if model is not None else model_dir(source_root / "apps")
        self.index: MeaningIndex | None = None
        self.failed = self.model is None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self.failed or self.index is not None or self._thread is not None:
                return
            self._thread = threading.Thread(target=self._build, name="command-meaning", daemon=True)
            self._thread.start()

    def _build(self) -> None:
        try:
            registry = json.loads((self.source_root / REGISTRY).read_text(encoding="utf-8"))
            phrases_file = self.source_root / PHRASES
            phrases = json.loads(phrases_file.read_text(encoding="utf-8")).get("commands", {}) if phrases_file.is_file() else {}
            assert self.model is not None
            self.index = MeaningIndex(Encoder(self.model), command_texts(registry, phrases),
                                      caches=[self.model, self.cache_root / "command-vectors"])
        except Exception:  # noqa: BLE001 - a broken model leaves keyword search; the log says why.
            LOG.exception("command meaning index unavailable")
            self.failed = True

    def search(self, sentence: str, limit: int) -> CommandMeaningDto:
        if self.failed:
            return CommandMeaningDto(state="unavailable", hits=[])
        if self.index is None:
            self.start()
            return CommandMeaningDto(state="loading", hits=[])
        hits = self.index.search(sentence, max(limit, 2))
        chosen = route(sentence, hits)
        return CommandMeaningDto(state="ready", hits=[MeaningHitDto(id=hit.command, score=round(hit.score, 4), text=hit.text)
                                                       for hit in hits[:limit]], route=chosen.command if chosen else None)


def commands_router(meaning: CommandMeaning) -> APIRouter:
    router = APIRouter()

    @router.post("/api/commands/meaning", response_model=CommandMeaningDto)
    def command_meaning(body: CommandMeaningRequest) -> CommandMeaningDto:
        """The commands a sentence means, closest first, and the one to offer for it if any."""
        return meaning.search(body.text, body.limit)

    return router
