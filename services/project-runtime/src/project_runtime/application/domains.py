"""L4 domain readiness, served: the registry's own description, and one bound answer.

Two thin reads over ``monkeyarch.domain.domain_readiness``, the honest
entry gate every technical domain calls before it runs (spec
docs/design/construction-api.md §3.6). ``domains_index`` needs no bound
project at all — it is the registry's own description of what each domain
reads and needs. ``domain_readiness`` resolves the project's projection the
same way ``GET /api/state?run=`` does (``application.projection.project_state``)
and asks the capability whether that domain can evaluate it now; an unknown
domain is refused as ``404 DOMAIN_UNKNOWN``, the same shape every other
"no such X" refusal in this API takes (compare ``application.capability``'s
``CAPABILITY_UNKNOWN``).

Nothing here decides anything about the design, and nothing here writes:
enrichment happens through the facets route, and a domain's own evaluation
begins only once it reports ``ready``.
"""

from __future__ import annotations

from typing import Any

from monkeyarch.domain.domain_readiness import DOMAINS, DomainUnknown, describe, readiness

from ..binding import ProjectBinding
from .projection import project_state
from ..errors import StudioError


def domains_index() -> dict[str, Any]:
    """Every known domain, described: what it reads and what it needs once it does."""

    return {"domains": [describe(domain) for domain in sorted(DOMAINS)]}


def domain_readiness(binding: ProjectBinding, domain: str, run_id: str | None) -> dict[str, Any]:
    """One domain's readiness against the bound project's projection.

    ``run_id`` is resolved exactly as ``GET /api/state?run=`` resolves it:
    the named run when given, else the project's own default projection
    rule. The domain name is checked before the project is even read, so an
    unknown domain is refused the same way regardless of what the project
    looks like.
    """

    if domain not in DOMAINS:
        raise StudioError(404, "DOMAIN_UNKNOWN", str(DomainUnknown(domain)))
    projection = project_state(binding, run_id=run_id, require_view=False)
    return readiness(projection.record, domain)
