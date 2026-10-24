"""The attention routes: read the queue across projects, and put a card off, set it aside or bring it back.

None of them sends a request to a project. Choosing an answer on a card goes
through the executor, which runs the answer's command as the person who chose it.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from .models import (
    AttentionChannel, AttentionDismissRequest, AttentionItem, AttentionState, AttentionSummary,
)
from .store import AttentionStore


def attention_router(store: AttentionStore) -> APIRouter:
    routes = APIRouter()

    @routes.get("/api/attention", response_model=list[AttentionItem])
    def attention_items(projectDir: str | None = None,
                        state: Annotated[list[AttentionState] | None, Query()] = None,
                        channel: Annotated[list[AttentionChannel] | None, Query()] = None) -> list[AttentionItem]:
        """Cards newest first; every project unless projectDir names one, unsettled ones unless state is given."""

        return store.items(projectDir, states=state, channels=channel)

    @routes.get("/api/attention/summary", response_model=AttentionSummary)
    def attention_summary() -> AttentionSummary:
        """Per project: cards that need a person now, put off, and being carried out."""

        return store.summary()

    @routes.get("/api/attention/{item_id}", response_model=AttentionItem)
    def attention_item(item_id: str) -> AttentionItem:
        return store.get(item_id)

    @routes.post("/api/attention/{item_id}/later", response_model=AttentionItem)
    def attention_later(item_id: str) -> AttentionItem:
        return store.later(item_id)

    @routes.post("/api/attention/{item_id}/dismiss", response_model=AttentionItem)
    def attention_dismiss(item_id: str, body: AttentionDismissRequest | None = None) -> AttentionItem:
        return store.dismiss(item_id, days=body.days if body else None)

    @routes.post("/api/attention/{item_id}/reopen", response_model=AttentionItem)
    def attention_reopen(item_id: str) -> AttentionItem:
        return store.reopen(item_id)

    return routes
