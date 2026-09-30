"""``/api/domains``: which technical domain can evaluate what, and why not yet.

Two reads, both thin. ``GET /api/domains`` is the registry's own description
of every known domain, asked with no project in hand at all.
``GET /api/domains/{domain}/readiness`` is one domain's answer against the
bound project's projection: ``ready`` with the entities it will read, or
``enrichment_required`` with one request per entity naming the facets that
are missing and why. Neither route writes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from starlette.requests import Request

from ...application import domains
from ...binding import bound_project

router = APIRouter(tags=["domains"])


@router.get("/domains")
def read_domains() -> dict[str, Any]:
    """Every known domain: what it reads, what it needs, and the facets it asks about."""

    return domains.domains_index()


@router.get("/domains/{domain}/readiness")
def read_domain_readiness(
    request: Request,
    domain: str,
    run: str | None = Query(
        default=None,
        min_length=1,
        description="Answer against this retained run instead of the project's default projection.",
    ),
) -> dict[str, Any]:
    """Whether ``domain`` can evaluate the bound project's geometry-bearing components now."""

    binding = bound_project(request.app.state)
    return domains.domain_readiness(binding, domain, run)
