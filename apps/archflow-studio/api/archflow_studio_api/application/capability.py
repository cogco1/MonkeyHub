"""The capability index: from what a user wants, to an entry point that exists.

An agent that has to read this repository's source, or guess a request body,
before it can change a height is paying for knowledge the project already
wrote down. The module registry records who owns what; this reads the
``capabilities`` entries beside those owners — the goals each one serves, the
routes it is actually performed through, and what it does *not* cover — and
answers two questions with them:

- *query*: which written-down capability serves this goal;
- *describe*: for one capability and the currently bound project, what the
  concrete target is, which of its numbers can move, and what may be kept.

Nothing here decides anything about the design. The target and its editable
numbers come from the existing catalog (``application/catalog.py``), the exact
base comes from the existing projection, and running the result stays with
``POST /api/proposals`` and the candidate route. A capability entry is a
description of those owners, never a second implementation of them, and this
module holds no state of its own.

Two honesty rules travel with every answer:

- a registry ``status`` describes the capability as written down, and never
  claims the bound project can run it now; runtime availability is read from
  the live binding on each request and reported separately;
- no match is not a claim that nothing can do it. The index is the set of
  capabilities somebody has written down, and the answer says so rather than
  letting an empty search read as "missing".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ..transport.errors import StudioError

if TYPE_CHECKING:  # pragma: no cover - imported for types only
    from .binding import ProjectBinding
    from .catalog import Capability
    from .projection import StateProjection

# The searching half of this module imports nothing but the standard library
# and one error type, so the governance CLI can import *this* matcher instead
# of keeping a second one that drifts from what the service answers. The
# describing half needs the projection and the catalog, and imports them where
# it uses them.

REGISTRY_RELATIVE = Path("governance") / "module_registry.json"
REGISTRY_SCHEMA = "ArchFlowModuleRegistry@1"

# How many refs a describe answer lists before it says how many more there are:
# a keep list is written by hand, so an unbounded dump of a large record helps
# nobody.
REF_PAGE = 40

# The sentence a search with no hit answers with. It states what the index is,
# so that "nothing matched" cannot be read as "the system cannot do this".
NO_MATCH = (
    "No capability entry matches {query!r}. This index holds {count} written-down "
    "capabilities; it is not the list of everything the system does. Read GET /api/capabilities for "
    "the whole index, and the existing API actions, before concluding that nothing here does this."
)


class CapabilityIndexUnavailable(StudioError):
    """The registry this index is read from is not beside the application."""

    def __init__(self) -> None:
        super().__init__(
            503,
            "CAPABILITY_INDEX_UNAVAILABLE",
            "this installation has no governance/module_registry.json beside it, so the capability "
            "index cannot be read. The existing API entry points are unaffected and still work.",
        )


def registry_path(start: Path | None = None) -> Path | None:
    """The module registry beside this installation, or ``None``.

    Source checkout and installed bundle share one layout — the registry sits
    at the root both ``apps/`` and ``archflow/`` live under — so walking up
    from this file finds the same file in either, and nothing has to be told
    where it is.
    """

    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        candidate = parent / REGISTRY_RELATIVE
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def _read(path: str, stamp: tuple[int, int]) -> tuple[dict[str, Any], ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != REGISTRY_SCHEMA:
        return ()
    return tuple(data.get("capabilities", ()))


def capability_index() -> tuple[dict[str, Any], ...]:
    """Every registered capability entry, read from the registry beside us."""

    path = registry_path()
    if path is None:
        raise CapabilityIndexUnavailable()
    info = path.stat()
    return _read(str(path), (info.st_mtime_ns, info.st_size))


# ---- the one matching rule, shared with the governance CLI
#
# People do not type search terms; they type what they want. "把这个体块高度改成
# 4.2 米，雨棚不动" and "change the main body height to 4.2 m and keep the canopy"
# have to reach the entry that serves them without anyone first guessing which
# words the index was written with. So the *entry* is the thing that is
# tokenised — its registered goals and aliases — and the sentence is only
# searched for those tokens. That is a keyword index over written-down words,
# not an understanding of the sentence: it suggests a capability to read, and
# decides nothing about the design.

# Words too common to mean anything on their own. Matching one of these is not
# evidence, so they are dropped from both sides.
_STOPWORDS = frozenset({
    "a", "an", "and", "any", "are", "as", "at", "be", "by", "can", "do", "for", "from", "how",
    "i", "in", "into", "is", "it", "its", "me", "my", "of", "on", "one", "or", "please", "that",
    "the", "then", "there", "this", "to", "up", "want", "was", "we", "what", "with", "would",
    "的", "了", "把", "个", "这", "那", "和", "与", "请", "帮", "我", "在", "是", "要", "给", "它",
})

# A Latin token has to be this long to count on its own; a CJK term is counted
# as a two-character run, which is the shortest unit that carries a word there.
_MIN_LATIN = 3
_CJK = re.compile(r"[㐀-䶿一-鿿぀-ヿ]")
_LATIN = re.compile(r"[a-z0-9]+")


def _latin_tokens(text: str) -> set[str]:
    return {token for token in _LATIN.findall(text.casefold())
            if len(token) >= _MIN_LATIN and token not in _STOPWORDS}


def _cjk_runs(text: str) -> list[str]:
    """The Han/Kana runs of a string, with nothing between them."""

    runs, current = [], []
    for character in text:
        if _CJK.match(character):
            current.append(character)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs


def _cjk_terms(text: str) -> set[str]:
    """Two-character terms of every CJK run, minus the ones that say nothing."""

    terms: set[str] = set()
    for run in _cjk_runs(text):
        for index in range(len(run) - 1):
            pair = run[index:index + 2]
            if pair[0] not in _STOPWORDS and pair[1] not in _STOPWORDS:
                terms.add(pair)
    return terms


def entry_terms(entry: Mapping[str, Any]) -> set[str]:
    """The words this capability is written down under: its goals and aliases.

    Deliberately not the purpose sentence: a purpose is prose written for a
    reader, and searching it would match on incidental words and turn the
    index into something that answers almost anything.
    """

    written = [str(value) for value in
               (*entry.get("goals", ()), *entry.get("aliases", ()), str(entry.get("capability_id", "")))]
    text = " ".join(written)
    return _latin_tokens(text) | _cjk_terms(text)


def query_terms(query: str) -> set[str]:
    return _latin_tokens(query) | _cjk_terms(query)


def relevance(entry: Mapping[str, Any], query: str) -> tuple[float, tuple[str, ...]]:
    """How relevant one entry is to a sentence, and which of its words matched.

    The score is the matched written-down text length: a longer agreed term is
    stronger evidence than a short one, and an exact id or a whole registered
    goal appearing verbatim outranks any number of loose words.
    """

    asked = query.strip().casefold()
    if not asked:
        return 0.0, ()
    if str(entry.get("capability_id", "")).casefold() == asked:
        return 1000.0, (str(entry.get("capability_id")),)
    matched = sorted(term for term in entry_terms(entry) if term in asked)
    score = float(sum(len(term) for term in matched))
    for phrase in (*entry.get("goals", ()), *entry.get("aliases", ())):
        written = str(phrase).casefold()
        if written and (written in asked or (len(asked) >= 4 and asked in written)):
            score += 10.0 + len(written)
            if written not in matched:
                matched.append(written)
    return score, tuple(matched)


def match_capabilities(
    entries: Sequence[Mapping[str, Any]], query: str | None
) -> tuple[tuple[dict[str, Any], ...], tuple[tuple[float, tuple[str, ...]], ...], str | None]:
    """Rank registered entries against what someone said they want to do.

    Returns the entries in relevance order with the evidence for each, and the
    sentence to answer with when nothing was relevant. The caller pages and
    renders; this is the only place the rule itself lives, so the CLI and the
    served index cannot disagree about what matches.
    """

    rows = [dict(entry) for entry in entries]
    if query is None or not query.strip():
        return tuple(rows), tuple((0.0, ()) for _ in rows), None
    scored = [(entry, relevance(entry, query)) for entry in rows]
    hits = sorted(
        (pair for pair in scored if pair[1][0] > 0),
        key=lambda pair: (-pair[1][0], str(pair[0].get("capability_id", ""))),
    )
    if not hits:
        return (), (), NO_MATCH.format(query=query, count=len(rows))
    return tuple(entry for entry, _ in hits), tuple(evidence for _, evidence in hits), None


def find_capabilities(goal: str | None) -> tuple[tuple[dict[str, Any], ...], str | None]:
    """The registered entries relevant to a goal, most relevant first."""

    matched, _, note = match_capabilities(capability_index(), goal)
    return matched, note


def find_with_evidence(goal: str | None):
    """The same search, keeping why each entry was returned."""

    return match_capabilities(capability_index(), goal)


def capability(capability_id: str) -> dict[str, Any]:
    """One entry by id, or a refusal that names the index it was not in."""

    for entry in capability_index():
        if entry.get("capability_id") == capability_id:
            return dict(entry)
    known = ", ".join(str(entry.get("capability_id")) for entry in capability_index()) or "none"
    raise StudioError(
        404,
        "CAPABILITY_UNKNOWN",
        f"no capability {capability_id!r} is registered. This index holds: {known}. "
        "A capability that is not written down may still exist as an API entry point.",
    )


# The one capability this API performs. Being written down in the registry is
# being *described*; being performed is this constant, and adding an entry to
# the index therefore grants no execution. A capability whose id is not here is
# readable and refused to run, with its own entrypoints named.
RUNNABLE = "candidate.modify_existing"


def require_runnable(entry: Mapping[str, Any]) -> str:
    """The capability id, when this route is the thing that performs it."""

    capability_id = str(entry.get("capability_id", ""))
    if capability_id == RUNNABLE:
        return capability_id
    entrypoints = [point for point in entry.get("entrypoints", ())
                   if not str(point).startswith(("GET /api/capabilities", "POST /api/capabilities"))]
    raise StudioError(
        422,
        "CAPABILITY_NOT_RUNNABLE_HERE",
        f"{capability_id} is not runnable through this route: this route runs only "
        f"{RUNNABLE}. Use its own entry points"
        + (f" ({', '.join(str(point) for point in entrypoints)})" if entrypoints else "")
        + ". Nothing was run.",
    )


# ---- describe: the same entry, against the project that is actually bound


@dataclass(frozen=True, slots=True)
class EditableField:
    """One number of one element, as the catalog already decided it."""

    element_id: str
    # The component the *element* belongs to, which is not always the component
    # that was asked about: describing a parent lists its descendants, and a
    # proposal naming the parent as targetComponentId with a grandchild's
    # element is exactly what POST /api/proposals refuses.
    component_id: str
    field: str
    value: int | float
    unit: str | None
    status: str
    source: str
    capability_id: str
    utterance: str


@dataclass(frozen=True, slots=True)
class DescribedTarget:
    component_id: str
    element_id: str | None
    editable: tuple[EditableField, ...]
    not_editable: tuple[EditableField, ...]


@dataclass(frozen=True, slots=True)
class KeepScope:
    """What this record will accept in a keep clause, and how many there are."""

    accepted: tuple[str, ...]
    remaining: int
    note: str


@dataclass(frozen=True, slots=True)
class Description:
    entry: Mapping[str, Any]
    project_id: str
    run_id: str
    state_digest: str | None
    exact_source: bool
    actionable: bool
    source_stage_ref: str | None
    read_with: str
    write_with: str
    target: DescribedTarget | None
    keep: KeepScope | None
    request: Mapping[str, Any] | None
    honesty: tuple[str, ...]


def _field(item: Capability, element_id: str, component_id: str) -> EditableField:
    number = item.value if isinstance(item.value, int) else round(float(item.value), 6)
    return EditableField(
        element_id=element_id,
        component_id=component_id,
        field=item.key,
        value=item.value,
        unit=item.unit,
        status=item.status,
        source=item.source,
        capability_id=item.capability_id,
        utterance=f"set {item.key} to {number}",
    )


def _keep_scope(projection: StateProjection, target_element: str | None) -> KeepScope:
    """The refs a keep clause may name, as ``intent._resolve_ref`` accepts them.

    The element being changed is left out: keeping the thing you are changing
    is a contradiction the proposal would refuse, and offering it would read
    as a suggestion.
    """

    entities = [
        f"entity:{entity.entity_id}"
        for entity in projection.record.entities
        if entity.entity_id != target_element
    ]
    parameters = [f"parameter:{item.key}" for item in projection.parameters]
    refs = tuple(dict.fromkeys(entities + parameters))
    return KeepScope(
        accepted=refs[:REF_PAGE],
        remaining=max(0, len(refs) - REF_PAGE),
        note=(
            "keep names what the change must not disturb. A change whose dependency closure reaches a "
            "kept ref comes back as a conflict and is never run past the protection."
        ),
    )


def initialization_description(
    binding: ProjectBinding,
    entry: Mapping[str, Any],
    projection: StateProjection | None = None,
) -> Description:
    """Offer input preparation even when no authored record can be projected."""

    return Description(
        entry=entry, project_id=binding.project_id,
        run_id=binding.reference_run().run.run_id if projection is None else projection.run.run_id,
        state_digest=None if projection is None else projection.state_digest,
        exact_source=projection is not None and projection.reference_state_exact,
        actionable=projection is not None and projection.state is not None,
        source_stage_ref=None, read_with="GET /api/state",
        write_with="POST /api/project/modeling with projectId",
        target=None, keep=None,
        request={"method": "POST", "path": "/api/project/modeling",
                 "body": {"projectId": binding.project_id}},
        honesty=("Initializes only missing or empty authored modeling inputs. Existing design, uploads, "
                 "runs and HEAD are preserved. Creates no geometry; read state and frame, then "
                 "use POST /api/proposals/sketch and its candidate route. Before the first real "
                 "candidate omit sourceRunId; studio-projection is not a stored run.",),
    )


def describe_capability(
    binding: ProjectBinding,
    projection: StateProjection,
    entry: Mapping[str, Any],
    *,
    component_id: str | None,
    element_id: str | None,
) -> Description:
    """One capability against the bound project, with a target when one was named.

    Without a target this answers the entry and the exact base only: which
    component to change is the architect's selection, and this module will not
    pick one by resembling a name.

    An entry this API does not perform is described as itself and no further:
    its own metadata and entry points, no numeric target read off this
    project's catalog, and no ready request — because the request it would
    hand back is one this route refuses to run. Describing something is not
    quietly promising to do it.
    """

    # Imported here, not at module scope: the searching half above is shared
    # with the governance CLI and must stay importable without the kernel.
    from .catalog import DERIVED_STATUS, EDITABLE, catalog_of

    if entry.get("capability_id") == "project.initialize_modeling":
        return initialization_description(binding, entry, projection)

    honesty: list[str] = []
    status = str(entry.get("status", ""))
    performed = str(entry.get("capability_id", "")) == RUNNABLE
    if not performed:
        honesty.append(
            f"{entry.get('capability_id')} is listed here but is not performed by this API: it is "
            f"described, and only {RUNNABLE} runs here. Its own entry points are "
            + (", ".join(str(point) for point in entry.get("entrypoints", ())) or "not listed")
            + "; no target of this project is read for it and no request is offered."
        )
    elif status != "PRODUCTION":
        honesty.append(
            f"status {status}: written down and implemented as described, and not yet certified "
            "against a real project by this entry."
        )
    target: DescribedTarget | None = None
    keep: KeepScope | None = None
    request: Mapping[str, Any] | None = None
    if component_id is not None and performed:
        declared = {item.entity_id for item in projection.record.entities_of("Component@1")}
        if component_id not in declared:
            raise StudioError(
                404,
                "TARGET_UNKNOWN",
                f"{component_id} is not a component this record declares; it declares "
                f"{', '.join(sorted(declared))}.",
            )
        catalog = catalog_of(binding, projection)
        node = catalog.component(component_id)
        wanted = set(node.descendant_element_ids) if node is not None else set()
        if element_id is not None:
            wanted &= {element_id}
        editable: list[EditableField] = []
        blocked: list[EditableField] = []
        for element in catalog.elements:
            if element.element_id not in wanted:
                continue
            for item in element.capabilities:
                (editable if item.status == EDITABLE else blocked).append(
                    _field(item, element.element_id, element.component_id)
                )
        target = DescribedTarget(
            component_id=component_id,
            element_id=element_id,
            editable=tuple(editable),
            not_editable=tuple(blocked),
        )
        keep = _keep_scope(projection, element_id)
        if not editable:
            honesty.append(
                f"{component_id} has no number this capability can move"
                + (
                    "; the ones it has are pinned by a reference and are changed at their source."
                    if blocked
                    else " in the record as it stands."
                )
            )
        request = _request(entry, projection, target)
        if any(item.status == DERIVED_STATUS for item in blocked):
            honesty.append(
                "a value a reference pins is reported with its source and is never compiled here."
            )
        others = sorted({item.component_id for item in editable} - {component_id})
        if others:
            honesty.append(
                f"{component_id} is a parent here: these elements belong to "
                f"{', '.join(others)}, and a request must name the element's own component as "
                "targetComponentId. Each row carries the componentId to send with it."
            )
    stage_ref = None if projection.source_stage_ref is None else projection.source_stage_ref.uri
    source_run_id = None if projection.reference.source == "none" else projection.run.run_id
    return Description(
        entry=entry,
        project_id=projection.project_id,
        run_id=projection.run.run_id,
        state_digest=projection.state_digest,
        exact_source=projection.reference_state_exact,
        actionable=projection.state is not None,
        source_stage_ref=stage_ref,
        # The two halves of "the same base", which are spelled differently:
        # reads select a retained run with ?run=, writes name it as sourceRunId
        # in the body. A read sent with sourceRunId (or a write with runId) is
        # ignored by the server and silently answers for the default run — so
        # each half is written out in full, with the Stage this was read
        # against when one was selected. A hint that said "the same base" and
        # pointed at another projection would be worse than no hint.
        read_with=_read_with(source_run_id, stage_ref),
        write_with=("omit sourceRunId; use stateDigest from GET /api/state for authored input"
                    if source_run_id is None else
                    f'sourceRunId="{source_run_id}"'
                    + ("" if stage_ref is None else f', sourceStageRef="{stage_ref}"')),
        target=target,
        keep=keep,
        request=request,
        honesty=tuple(honesty) + projection.honesty,
    )


def _read_with(run_id: str | None, stage_ref: str | None) -> str:
    """The read that answers for this same base, as a URL a client can send."""

    query = {} if run_id is None else {"run": run_id}
    if stage_ref is not None:
        query["sourceStageRef"] = stage_ref
    return "GET /api/state" + ("?" + urlencode(query) if query else "")


def _request(
    entry: Mapping[str, Any],
    projection: StateProjection,
    target: DescribedTarget,
) -> Mapping[str, Any] | None:
    """The next request, with this project's real base already filled in.

    The utterance is the first editable field written in the grammar's own
    form, with the value it holds now: it is a shape to edit, not a proposed
    change, and sending it unchanged would set the number to what it already
    is.
    """

    if not target.editable or projection.state_digest is None:
        return None
    # The element's own component, not the one that was asked about: a request
    # naming a parent with a grandchild's element is one POST /api/proposals
    # refuses, and a ready request that cannot run is worse than none.
    first = target.editable[0]
    body: dict[str, Any] = {
        "projectId": projection.project_id,
        "stateDigest": projection.state_digest,
        "targetComponentId": first.component_id,
        "elementId": first.element_id,
        "utterance": first.utterance,
        "keep": [],
    }
    if projection.reference.source != "none":
        body["sourceRunId"] = projection.run.run_id
    if projection.source_stage_ref is not None:
        # The Stage this description was read against travels into the write,
        # so the run is made from the source the reader was looking at.
        body["sourceStageRef"] = projection.source_stage_ref.uri
    return {
        "method": "POST",
        "path": f"/api/capabilities/{entry.get('capability_id')}/run",
        "body": body,
    }


def summary(entries: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """The short index: enough to choose one, not the whole entry."""

    return tuple(
        {
            "capability_id": entry.get("capability_id"),
            "owner": entry.get("owner"),
            "kind": entry.get("kind"),
            "status": entry.get("status"),
            "purpose": entry.get("purpose"),
            "purpose_zh": entry.get("purpose_zh"),
            "goals": tuple(entry.get("goals", ())),
            "entrypoints": tuple(entry.get("entrypoints", ())),
        }
        for entry in entries
    )
