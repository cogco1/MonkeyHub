"""Evidence-grounded precedent Study without a second design truth.

A Study begins from one exact registered document page. Its editable truth is a
small set of trace-evidence primitives in normalized page coordinates.
Measurements, relations, the CompositionGraph, hypotheses and counterfactual
judgements are deterministic projections of confirmed traces, and the method
that derives them is MonkeyDiagram's (``monkeydiagram.study``, #519); this
module keeps the retained ledger that records them. A saved revision
is an archival snapshot of the method that produced it: cold reads validate its
exact source, its evidence and the agreement between its own retained relations,
receipts and evidence, but never reinterpret retained derivations with today's
rules. A correction runs the current method and records that method on the new
revision.

Nothing here edits a StateRecord, DesignStage, design branch or canonical HEAD.
The existing ``research-evidence-ledger`` record kind is the durable substrate.
Later image/model reasoning may propose traces, but it must enter through this
same evidence contract and can never bypass user correction.
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import json
import threading
from typing import Any, Iterable, Mapping

from archflow.contracts.canonical import canonical_digest
from archflow.ports.model import ModelInvocationReceipt, ModelPhase, ModelInvocationStatus
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import RESEARCH_EVIDENCE_LEDGER, STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import ProjectRecordRef, record_ref_from_uri, require_identifier
from archflow.project.repository import ProjectRepositoryError
from monkeydiagram.study import (
    CURRENT_DERIVATION_METHOD,
    StudyEvidenceError,
    StudySource,
    canonical_evidence,
    check_research_links,
    composition_graph,
    derive,
    research_snapshot_of,
)

from .artifacts import _registered_document_bytes, list_documents
from ..binding import ProjectBinding, record_kind
from ..errors import StudioError


LEDGER_SCHEMA = "EvidenceLedger@1"
LEGACY_LEDGER_KEYS = frozenset({
    "schema",
    "project_id",
    "run_id",
    "study_id",
    "previous_ref",
    "source",
    "evidence",
    "measurements",
    "relations",
    "hypotheses",
    "counterfactuals",
    "canonical_state_changed",
})
LEDGER_KEYS = LEGACY_LEDGER_KEYS | {"derivation_method"}
RESEARCH_LEDGER_KEYS = LEDGER_KEYS | {"research"}
STUDY_RUN_PREFIX = "study-"
_study_lock = threading.RLock()


@dataclass(frozen=True, slots=True)
class StudyView:
    ref: ProjectRecordRef
    payload: Mapping[str, Any]
    composition_graph: Mapping[str, Any]


def study_evidence_context(view: StudyView, *, budget_bytes: int = 12288) -> dict[str, Any]:
    """Read a retained prior with its conditions, alternatives and source links.

    ``read_study`` checks the revision before this projection is called. This
    function selects already retained values only: it neither searches other
    revisions nor reruns research. The budget covers the complete selected
    content in compact UTF-8 JSON; identity and refusal information remain
    available even when that content does not fit.
    """
    if type(budget_bytes) is not int or budget_bytes < 1:
        raise ValueError("budget_bytes must be a positive integer")
    payload = view.payload
    result = {
        "study_id": payload["study_id"],
        "ledger_ref": view.ref.uri,
        "source": deepcopy(payload["source"]),
        "derivation_method": payload.get("derivation_method"),
        "reopen": {"study_id": payload["study_id"], "ledger_ref": view.ref.uri},
        "limitations": [
            "This is the named retained revision, not a claim that it is the latest revision or accepted design state.",
            "Completeness means the selected declared companion content fits this response; it does not establish that the evidence is sufficient or the prior is valid.",
            "Historical sources retain authored citations and summaries; their external source text has not been verified here.",
            "Counterfactual and comparison details must be reopened before relying on their omitted geometry or numerical results; nothing was rerun.",
        ],
    }
    research = payload.get("research") or {}
    prior = research.get("design_prior")
    if prior is None:
        result["completeness"] = {
            "complete": False, "reason": "no-design-prior", "required_bytes": 0,
            "budget_bytes": budget_bytes,
        }
        return result

    hypotheses = research["hypotheses"]
    selected_ids = set(prior["hypothesis_ids"])
    while True:
        companions: set[str] = set()
        for row in hypotheses:
            # Either endpoint can declare the comparison. Selecting A must
            # still expose B's challenge when only B names A as a competitor.
            if row["hypothesis_id"] in selected_ids or not selected_ids.isdisjoint(row["competes_with"]):
                companions.add(row["hypothesis_id"])
                companions.update(row["competes_with"])
        # A shared intervention also binds its hypotheses together: returning
        # its result without a joint hypothesis would omit that hypothesis's
        # assumptions and evidence. Its competitors are reached next round.
        for row in research["counterfactuals"]:
            if not selected_ids.isdisjoint(row["hypothesis_ids"]):
                companions.update(row["hypothesis_ids"])
        if companions.issubset(selected_ids):
            break
        selected_ids.update(companions)
    selected = [row for row in hypotheses if row["hypothesis_id"] in selected_ids]
    pattern = research["composition_pattern"]
    evidence_ids = set(pattern["evidence_ids"])
    source_ids: set[str] = set()
    for row in selected:
        evidence_ids.update(row["evidence_ids"])
        evidence_ids.update(row.get("counter_evidence_ids", ()))
        source_ids.update(row["historical_source_ids"])

    counterfactuals = []
    for row in research["counterfactuals"]:
        if row["hypothesis_ids"] and selected_ids.isdisjoint(row["hypothesis_ids"]):
            continue
        evidence_ids.add(row["target_evidence_id"])
        projected = deepcopy({key: value for key, value in row.items() if key != "actual"})
        actual = row.get("actual")
        if actual is None:
            projected["actual"] = None
        else:
            # Keep the retained status, failure reason and interpretation, not
            # a new prose conclusion about its repeated polygon snapshots.
            omitted = {"evidence", "measurements", "relations", "baseline_measurements",
                       "baseline_relations", "removed_facts", "added_facts"}
            projected["actual"] = deepcopy({key: value for key, value in actual.items() if key not in omitted})
            projected["details_omitted"] = sorted(omitted.intersection(actual))
        counterfactuals.append(projected)

    # A gap can name several traces together. Once relevant, all those
    # companions travel with it, including gaps reached through that addition.
    # Empty evidence_ids are global gaps; no declared scope excludes them.
    while True:
        companion_evidence = {identifier for row in research["gaps"]
                              if not evidence_ids.isdisjoint(row["evidence_ids"])
                              for identifier in row["evidence_ids"]}
        if companion_evidence.issubset(evidence_ids):
            break
        evidence_ids.update(companion_evidence)

    comparisons = []
    for row in research.get("comparison_results", ()):
        comparisons.append({
            "method": row["method"],
            "studies": [{"study_id": item["study_id"], "ledger_ref": item["ledger_ref"]}
                        for item in row["studies"]],
            "details_omitted": True,
        })
    content = deepcopy({
        "design_prior": prior,
        "composition_pattern": pattern,
        "hypotheses": selected,
        "evidence": [row for row in payload["evidence"] if row["evidence_id"] in evidence_ids],
        "historical_sources": [row for row in research["historical_sources"] if row["source_id"] in source_ids],
        "gaps": [row for row in research["gaps"]
                 if not row["evidence_ids"] or not evidence_ids.isdisjoint(row["evidence_ids"])],
        "counterfactuals": counterfactuals,
        "comparisons": research.get("comparisons", []),
        "comparison_results": comparisons,
    })
    size = len(json.dumps(content, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    complete = size <= budget_bytes
    result["completeness"] = {
        "complete": complete, "reason": None if complete else "budget-exceeded",
        "required_bytes": size, "budget_bytes": budget_bytes,
    }
    if complete:
        result.update(content)
    return result


def _study_run_id(study_id: str) -> str:
    try:
        require_identifier(study_id, "study_id")
    except (TypeError, ValueError) as exc:
        raise StudioError(
            422,
            "STUDY_ID_INVALID",
            "Study ids must be stable identifiers.",
        ) from exc
    if len(study_id) > 80:
        raise StudioError(
            422,
            "STUDY_ID_INVALID",
            "Study ids may contain at most 80 characters.",
        )
    return f"{STUDY_RUN_PREFIX}{study_id}"


def _source_document(
    binding: ProjectBinding,
    *,
    run_id: str,
    asset_sha256: str,
    revision_ref: str | None,
    document_ref: str | None,
) -> tuple[Any, ProjectRecordRef]:
    """Resolve one exact StudioSourceDocument record, never a latest match.

    ``revision_ref=None`` means the unversioned uploaded document. It is not a
    wildcard for a later generated drawing that happens to have identical bytes.
    New Study revisions also pin the content-addressed source-document record so
    a cold read never depends on document ordering.
    """

    try:
        documents = [
            row for row in list_documents(binding, run_id)
            if row.asset_sha256 == asset_sha256
            and row.revision_ref == revision_ref
        ]
        refs: list[ProjectRecordRef] = []
        for ref in binding.record_refs(run_id):
            if record_kind(ref) != STUDIO_SOURCE_DOCUMENT:
                continue
            payload = binding.repository.load_json(ref)
            if (
                payload.get("schema") == "StudioSourceDocument@1"
                and payload.get("project_id") == binding.project_id
                and payload.get("run_id") == run_id
                and payload.get("asset_sha256") == asset_sha256
                and payload.get("revisionRef") == revision_ref
            ):
                refs.append(ref)
    except (ProjectRepositoryError, OSError, TypeError, ValueError) as exc:
        raise StudioError(
            404,
            "STUDY_SOURCE_UNREGISTERED",
            "The Study source is not an exact registered document in this project.",
        ) from exc
    if not documents or not refs:
        raise StudioError(
            404,
            "STUDY_SOURCE_UNREGISTERED",
            "The Study source is not an exact registered document in this project.",
        )
    if len(documents) != 1 or len(refs) != 1:
        raise StudioError(
            409,
            "STUDY_SOURCE_AMBIGUOUS",
            "The Study source has competing retained document registrations.",
        )
    ref = refs[0]
    if document_ref is not None:
        try:
            named = record_ref_from_uri(document_ref, binding.project_id)
        except (TypeError, ValueError) as exc:
            raise StudioError(
                409,
                "STUDY_SOURCE_MISMATCH",
                "The retained Study source binding is not a project document reference.",
            ) from exc
        prefix = f"runs/{run_id}/records/"
        if (
            record_kind(named) != STUDIO_SOURCE_DOCUMENT
            or not named.relative_path.startswith(prefix)
            or "/" in named.relative_path[len(prefix):]
            or named != ref
        ):
            raise StudioError(
                409,
                "STUDY_SOURCE_MISMATCH",
                "The retained Study source binding no longer names its exact document record.",
            )
    # Validate the exact registered bytes. This deliberately bypasses
    # document_bytes' convenience lookup, whose null revision is a wildcard.
    _registered_document_bytes(binding, documents[0])
    return documents[0], ref


def _source(
    binding: ProjectBinding,
    *,
    run_id: str,
    asset_sha256: str,
    revision_ref: str | None,
    page_index: int,
    document_ref: str | None = None,
) -> StudySource:
    document, retained_ref = _source_document(
        binding,
        run_id=run_id,
        asset_sha256=asset_sha256,
        revision_ref=revision_ref,
        document_ref=document_ref,
    )
    page = next(
        (item for item in document.pages if item.page_index == page_index),
        None,
    )
    if page is None:
        raise StudioError(
            422,
            "STUDY_SOURCE_PAGE_INVALID",
            "The Study page index is not present in the registered source document.",
        )
    return StudySource(
        run_id=document.run_id,
        asset_sha256=document.asset_sha256,
        revision_ref=document.revision_ref,
        page_index=page.page_index,
        page_width=page.width,
        page_height=page.height,
        mime_type=document.mime_type,
        document_ref=retained_ref.uri,
    )


def _source_payload_matches(retained: Mapping[str, Any], exact: StudySource) -> bool:
    expected = exact.to_dict()
    # EvidenceLedger@1 records written before exact source-record pinning are
    # still resolvable: their run + asset + exact null/non-null revision tuple
    # identifies the old document. New records additionally prove that identity
    # with the content-addressed StudioSourceDocument ref.
    if "document_ref" not in retained:
        expected.pop("document_ref")
    return dict(retained) == expected


def _check_comparison_results(binding, research, results) -> None:
    """Verify archived inputs and result structure without rerunning comparison."""
    definitions = research.get("comparisons", [])
    if (not isinstance(definitions, list) or len(definitions) > 6
            or not isinstance(results, list) or len(definitions) != len(results)):
        raise ValueError("Each research comparison needs its exact retained result.")
    for definition, result in zip(definitions, results):
        if not isinstance(definition, Mapping) or not isinstance(result, Mapping):
            raise ValueError("Research comparisons and results must be objects.")
        requested = definition.get("studies")
        projected = result.get("studies")
        if (not isinstance(requested, list) or not 2 <= len(requested) <= 6
                or not isinstance(projected, list) or len(projected) != len(requested)
                or result.get("schema") != "StudyComparison@1"
                or result.get("project_id") != binding.project_id
                or not isinstance(result.get("method"), str) or not result["method"]
                or not isinstance(result.get("metric_frame"), str) or not result["metric_frame"]
                or result.get("canonical_state_changed") is not False):
            raise ValueError("The retained comparison result has a different input or method contract.")
        identities = set()
        for reference, projection in zip(requested, projected):
            if not isinstance(reference, Mapping) or not isinstance(projection, Mapping):
                raise ValueError("Comparison entries must name an exact Study revision.")
            study_id, ledger_ref = reference["study_id"], reference["ledger_ref"]
            identity = (study_id, ledger_ref)
            if identity in identities or any(projection.get(key) != reference[key] for key in ("study_id", "ledger_ref")):
                raise ValueError("The comparison result disagrees with its requested revisions.")
            identities.add(identity)
            named = _own_ledger_ref(ledger_ref, binding, _study_run_id(study_id))
            if named is None:
                raise ValueError("A comparison reference belongs to another Study or project.")
            archived = _identity(binding, named, study_id)
            if projection.get("source") != archived.get("source"):
                raise ValueError("The comparison projection names another source page.")
        for field in ("shared_topology", "pairwise"):
            if not isinstance(result.get(field), list) or not all(isinstance(row, Mapping) for row in result[field]):
                raise ValueError("The retained comparison results are structurally incomplete.")
        pairs = set()
        for row in result["pairwise"]:
            left = (row["left"]["study_id"], row["left"]["ledger_ref"])
            right = (row["right"]["study_id"], row["right"]["ledger_ref"])
            pair = frozenset((left, right))
            if left not in identities or right not in identities or left == right or pair in pairs:
                raise ValueError("A retained comparison pair names another input revision.")
            pairs.add(pair)
        if len(pairs) != len(identities) * (len(identities) - 1) // 2:
            raise ValueError("The retained comparison pair list is incomplete.")


def _ledger_refs(
    binding: ProjectBinding,
    run_id: str,
) -> tuple[ProjectRecordRef, ...]:
    if run_id not in binding.run_ids():
        return ()
    try:
        run = binding.load_run(run_id)
        refs = binding.repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run_id,
            ),
        )
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(
            409,
            "STUDY_LEDGER_UNREADABLE",
            "This Study's retained run cannot be read back from its project.",
        ) from exc
    return tuple(
        ref for ref in refs if record_kind(ref) == RESEARCH_EVIDENCE_LEDGER
    )


def _own_ledger_ref(
    value: object,
    binding: ProjectBinding,
    run_id: str,
) -> ProjectRecordRef | None:
    """The retained ledger of this Study run that ``value`` names, if any."""

    if not isinstance(value, str):
        return None
    try:
        ref = record_ref_from_uri(value, binding.project_id)
    except (TypeError, ValueError):
        return None
    if record_kind(ref) != RESEARCH_EVIDENCE_LEDGER:
        return None
    prefix = f"runs/{run_id}/records/"
    if not ref.relative_path.startswith(prefix):
        return None
    if "/" in ref.relative_path[len(prefix) :]:
        return None
    return ref


def _identity(
    binding: ProjectBinding,
    ref: ProjectRecordRef,
    study_id: str,
) -> dict[str, Any]:
    """Read a retained ledger and check only whose revision it is."""

    try:
        payload = binding.repository.load_json(ref)
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(
            404,
            "STUDY_LEDGER_NOT_FOUND",
            "No retained Study ledger answers that reference in this project.",
        ) from exc
    keys = frozenset(payload)
    if keys - {"model_invocations"} not in {LEGACY_LEDGER_KEYS, LEDGER_KEYS, RESEARCH_LEDGER_KEYS} or (
        payload.get("schema") != LEDGER_SCHEMA
        or payload.get("project_id") != binding.project_id
        or payload.get("study_id") != study_id
        or payload.get("run_id") != _study_run_id(study_id)
        or payload.get("canonical_state_changed") is not False
    ):
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study ledger has a different project, study, run or authority contract.",
        )
    if "derivation_method" in payload and (
        not isinstance(payload["derivation_method"], str)
        or not payload["derivation_method"]
    ):
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study method name is invalid.",
        )
    previous = payload["previous_ref"]
    if previous is not None and _own_ledger_ref(
        previous,
        binding,
        _study_run_id(study_id),
    ) is None:
        raise StudioError(
            409,
            "STUDY_REVISION_INVALID",
            "A Study revision does not name a predecessor retained by this Study run.",
        )
    return payload


def _load_payload(
    binding: ProjectBinding,
    ref: ProjectRecordRef,
    study_id: str,
) -> dict[str, Any]:
    payload = _identity(binding, ref, study_id)
    source = payload.get("source")
    evidence = payload.get("evidence")
    if not isinstance(source, Mapping) or not isinstance(evidence, list):
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study ledger is structurally incomplete.",
        )
    try:
        exact_source = _source(
            binding,
            run_id=source.get("run_id"),
            asset_sha256=source.get("asset_sha256"),
            revision_ref=source.get("revision_ref"),
            page_index=source.get("page_index"),
            document_ref=source.get("document_ref"),
        )
    except (StudioError, TypeError, ValueError) as exc:
        raise StudioError(
            409,
            "STUDY_SOURCE_MISMATCH",
            "The retained Study source no longer resolves to the exact registered page it names.",
        ) from exc
    if not _source_payload_matches(source, exact_source):
        raise StudioError(
            409,
            "STUDY_SOURCE_MISMATCH",
            "The retained Study source no longer resolves to the exact registered page it names.",
        )
    try:
        normalized = canonical_evidence(exact_source, evidence)
    except StudyEvidenceError as exc:
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study evidence is invalid.",
        ) from exc
    if normalized != evidence:
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study evidence is not canonical.",
        )
    if "model_invocations" in payload:
        try:
            if not isinstance(payload["model_invocations"], list):
                raise ValueError("Invalid model invocation list")
            for row in payload["model_invocations"]:
                receipt = ModelInvocationReceipt.from_dict(row)
                request_payload = receipt.request.payload
                if (receipt.request.phase != ModelPhase.RESEARCH
                        or receipt.status != ModelInvocationStatus.SUCCESS
                        or request_payload["source"] != source):
                    raise ValueError("Model receipt names another source or phase")
                input_ref = _own_ledger_ref(request_payload["ledger_ref"], binding, _study_run_id(study_id))
                if input_ref is None:
                    raise ValueError("Model receipt names another Study")
                input_ledger = _identity(binding, input_ref, study_id)
                if (input_ledger["source"] != source
                        or request_payload.get("evidence") != input_ledger["evidence"]
                        or request_payload.get("research") != input_ledger.get("research")
                        or receipt.request.context_digest != canonical_digest(request_payload, ascii=False)
                        or receipt.request.checkpoint_digest != composition_graph(input_ledger["evidence"], input_ledger["relations"])["graph_digest"]):
                    raise ValueError("Model receipt does not describe its exact retained input revision")
        except (KeyError, TypeError, ValueError, StudioError) as exc:
            raise StudioError(409, "STUDY_LEDGER_INVALID", "The retained Study model receipt is invalid.") from exc
    for field in ("measurements", "relations", "hypotheses", "counterfactuals"):
        rows = payload.get(field)
        # Shape only: what an older method concluded is not re-judged here, but
        # a retained finding is still a row of fields. Without this a corrupt
        # row reaches the wire contract and fails there as an unexplained 500
        # instead of naming the ledger that cannot be read.
        if not isinstance(rows, list) or not all(
            isinstance(row, Mapping) for row in rows
        ):
            raise StudioError(
                409,
                "STUDY_LEDGER_INVALID",
                "The retained Study derivation snapshot is structurally incomplete.",
            )
    if "research" in payload:
        try:
            research = payload["research"]
            if not isinstance(research, Mapping) or not isinstance(research.get("method"), str) or not research["method"]:
                raise ValueError("The research snapshot has no method.")
            if research["source_binding"] != source:
                raise ValueError("The research snapshot names another source page.")
            check_research_links(research, evidence)
            _check_comparison_results(binding, research, research.get("comparison_results", []))
            for row in research["counterfactuals"]:
                if row["actual"] is not None and (not isinstance(row["actual"], Mapping)
                                                 or row["actual"].get("status") not in {"computed", "unsupported"}):
                    raise ValueError("The retained counterfactual result is unreadable.")
                actual = row["actual"]
                if actual and actual["status"] == "computed":
                    retained = actual["evidence"]
                    if canonical_evidence(exact_source, retained) != retained:
                        raise ValueError("The retained intervention evidence is not canonical.")
                    originals = {item["evidence_id"]: item for item in evidence}
                    if {item["evidence_id"] for item in retained} != set(originals):
                        raise ValueError("The retained intervention changed the evidence identity set.")
                    if any(item != originals[item["evidence_id"]] for item in retained
                           if item["evidence_id"] != row["target_evidence_id"]):
                        raise ValueError("The retained intervention altered a trace outside its target.")
        except (KeyError, TypeError, ValueError, AttributeError, StudioError) as exc:
            raise StudioError(409, "STUDY_LEDGER_INVALID", "The retained Study research is invalid.") from exc
    # Retained derivations are historical evidence, not a cache. Re-running a
    # newer method here would rewrite the meaning of an old content-addressed
    # revision and make method evolution break archive readability.
    return payload


def _current_ref(
    binding: ProjectBinding,
    study_id: str,
) -> ProjectRecordRef | None:
    run_id = _study_run_id(study_id)
    refs = _ledger_refs(binding, run_id)
    if not refs:
        return None
    by_uri = {ref.uri: ref for ref in refs}
    referenced: set[str] = set()
    for ref in refs:
        # Choosing a head needs each revision's link, not its reasoning.
        # Recomputation belongs to the revision a caller actually reads or
        # extends: re-deriving every ancestor here would let one superseded
        # revision make the current head unreadable and uncorrectable.
        previous = _identity(binding, ref, study_id).get("previous_ref")
        if previous is not None:
            if previous not in by_uri:
                raise StudioError(
                    409,
                    "STUDY_REVISION_INVALID",
                    "A Study revision points outside its retained revision set.",
                )
            referenced.add(previous)
    heads = [ref for uri, ref in by_uri.items() if uri not in referenced]
    if len(heads) != 1:
        raise StudioError(
            409,
            "STUDY_REVISION_CONFLICT",
            "This Study has competing retained heads; choose and reconcile one before continuing.",
        )
    return heads[0]


def _receipts_bound_to(payload: Mapping[str, Any], graph_digest: str) -> None:
    """Refuse a retained finding whose own receipt names another graph.

    A reasoning receipt states which composition the finding was read off. It
    is checked against the graph rebuilt from this revision's own retained
    evidence and relations, not against today's rules or thresholds: what the
    old method concluded stands, but it may not be served under a graph digest
    it never bound itself to.
    """

    for field in ("measurements", "relations", "hypotheses", "counterfactuals"):
        for row in payload[field]:
            if "reasoning_receipt" not in row:
                # Revisions retained before findings carried a receipt stay
                # readable; no stamp is invented on their behalf.
                continue
            receipt = row["reasoning_receipt"]
            if (
                not isinstance(receipt, Mapping)
                or receipt.get("graph_digest") != graph_digest
            ):
                raise StudioError(
                    409,
                    "STUDY_LEDGER_INVALID",
                    "A retained Study finding carries a reasoning receipt bound to another composition graph.",
                )


def read_study(
    binding: ProjectBinding,
    study_id: str,
    ledger_ref: str | None = None,
) -> StudyView:
    run_id = _study_run_id(study_id)
    if ledger_ref is None:
        ref = _current_ref(binding, study_id)
        if ref is None:
            raise StudioError(
                404,
                "STUDY_NOT_FOUND",
                f"Study {study_id!r} has no retained evidence ledger.",
            )
    else:
        named = _own_ledger_ref(ledger_ref, binding, run_id)
        if named is None:
            raise StudioError(
                422,
                "STUDY_LEDGER_REF_INVALID",
                "The ledger reference does not belong to this Study run.",
            )
        ref = named
    payload = _load_payload(binding, ref, study_id)
    # CompositionGraph@1 is rebuilt only from the retained evidence and
    # retained relation snapshot. It does not invoke today's hypotheses or
    # counterfactual method.
    try:
        graph = composition_graph(payload["evidence"], payload["relations"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StudioError(
            409,
            "STUDY_LEDGER_INVALID",
            "The retained Study relation snapshot cannot form its CompositionGraph.",
        ) from exc
    _receipts_bound_to(payload, graph["graph_digest"])
    return StudyView(ref, payload, graph)


def list_studies(
    binding: ProjectBinding, *, source_run_id: str | None = None,
    asset_sha256: str | None = None, page_index: int | None = None,
) -> list[StudyView]:
    """Discover saved Studies through their existing P036 runs, without an index."""
    result = []
    for run_id in sorted(binding.run_ids()):
        if not run_id.startswith(STUDY_RUN_PREFIX):
            continue
        study_id = run_id[len(STUDY_RUN_PREFIX):]
        current = _current_ref(binding, study_id)
        if current is None:
            continue
        payload = _identity(binding, current, study_id)
        source = payload.get("source", {})
        if not isinstance(source, Mapping):
            raise StudioError(409, "STUDY_LEDGER_INVALID", "The retained Study source is structurally incomplete.")
        if ((source_run_id is not None and source.get("run_id") != source_run_id)
                or (asset_sha256 is not None and source.get("asset_sha256") != asset_sha256)
                or (page_index is not None and source.get("page_index") != page_index)):
            continue
        result.append(read_study(binding, study_id, current.uri))
    return result


def _same_revision_content(
    previous: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> bool:
    """Ignore only the revision link when deciding an exact idempotent retry."""

    return (
        {key: value for key, value in previous.items() if key != "previous_ref"}
        == {key: value for key, value in candidate.items() if key != "previous_ref"}
    )


def save_study(
    binding: ProjectBinding,
    *,
    study_id: str,
    source_run_id: str,
    asset_sha256: str,
    revision_ref: str | None,
    page_index: int,
    evidence_rows: Iterable[Mapping[str, Any]],
    expected_previous_ref: str | None,
    research: Mapping[str, Any] | None = None,
    model_receipt: ModelInvocationReceipt | None = None,
    comparison_results: list[Mapping[str, Any]] | None = None,
) -> StudyView:
    """Save one corrected evidence revision without writing design state."""

    run_id = _study_run_id(study_id)
    source = _source(
        binding,
        run_id=source_run_id,
        asset_sha256=asset_sha256,
        revision_ref=revision_ref,
        page_index=page_index,
    )
    evidence = canonical_evidence(source, evidence_rows)
    measurements, relations, graph, hypotheses, counterfactuals = derive(evidence)
    research_snapshot = None
    if research is not None:
        try:
            research_snapshot = research_snapshot_of(source, evidence, research)
        except (KeyError, TypeError, ValueError) as exc:
            raise StudioError(422, "STUDY_RESEARCH_INVALID", str(exc)) from exc

    with _study_lock:
        current = _current_ref(binding, study_id)
        actual_previous = None if current is None else current.uri
        if expected_previous_ref != actual_previous:
            raise StudioError(
                409,
                "STUDY_REVISION_STALE",
                "The Study changed. Reload its current evidence ledger before saving corrections.",
            )
        previous_payload: Mapping[str, Any] | None = None
        if current is not None:
            previous_payload = _load_payload(binding, current, study_id)
            if "research" in previous_payload and research is None:
                raise StudioError(409, "STUDY_RESEARCH_REQUIRED", "Reload and include this Study's research before saving corrections.")
            if not _source_payload_matches(previous_payload["source"], source):
                raise StudioError(
                    409,
                    "STUDY_SOURCE_IMMUTABLE",
                    "A Study revision cannot switch to another source page; start another Study.",
                )

        if research_snapshot is not None:
            previous_research = (previous_payload or {}).get("research") or {}
            if comparison_results is None:
                if research_snapshot.get("comparisons", []) != previous_research.get("comparisons", []):
                    raise StudioError(422, "STUDY_COMPARISON_REQUIRED", "Changed comparison references need server-computed results before they can be retained.")
                retained_comparisons = previous_research.get("comparison_results", [])
            else:
                retained_comparisons = json.loads(json.dumps(comparison_results, allow_nan=False))
            try:
                _check_comparison_results(binding, research_snapshot, retained_comparisons)
            except (KeyError, TypeError, ValueError, StudioError) as exc:
                raise StudioError(422, "STUDY_COMPARISON_INVALID", "The comparison result must name the exact retained revisions requested by this research.") from exc
            research_snapshot["comparison_results"] = retained_comparisons

        payload = {
            "schema": LEDGER_SCHEMA,
            "project_id": binding.project_id,
            "run_id": run_id,
            "study_id": study_id,
            "previous_ref": actual_previous,
            "source": source.to_dict(),
            "evidence": evidence,
            "measurements": measurements,
            "relations": relations,
            "hypotheses": hypotheses,
            "counterfactuals": counterfactuals,
            "derivation_method": CURRENT_DERIVATION_METHOD,
            "canonical_state_changed": False,
        }
        if research_snapshot is not None:
            payload["research"] = research_snapshot
        invocations = list((previous_payload or {}).get("model_invocations", []))
        if model_receipt is not None:
            if (model_receipt.request.phase != ModelPhase.RESEARCH
                    or model_receipt.status != ModelInvocationStatus.SUCCESS
                    or model_receipt.request.payload.get("source") != source.to_dict()
                    or model_receipt.request.payload.get("ledger_ref") != actual_previous
                    or previous_payload is None
                    or model_receipt.request.payload.get("evidence") != previous_payload["evidence"]
                    or model_receipt.request.payload.get("research") != previous_payload.get("research")
                    or model_receipt.request.context_digest != canonical_digest(model_receipt.request.payload, ascii=False)
                    or model_receipt.request.checkpoint_digest != composition_graph(previous_payload["evidence"], previous_payload["relations"])["graph_digest"]):
                raise StudioError(422, "STUDY_MODEL_BINDING_INVALID", "The model response must name this exact Study source and previous revision.")
            invocations.append(model_receipt.to_dict())
        if invocations:
            payload["model_invocations"] = invocations
        if (
            current is not None
            and previous_payload is not None
            and _same_revision_content(previous_payload, payload)
        ):
            return StudyView(current, previous_payload, graph)

        try:
            run = (
                binding.load_run(run_id)
                if run_id in binding.run_ids()
                else binding.repository.create_run(run_id)
            )
            ref = binding.repository.put_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_RECORD,
                    run_id=run_id,
                ),
                record_kind=RESEARCH_EVIDENCE_LEDGER,
                payload=payload,
            )
        except (ProjectRepositoryError, OSError, ValueError) as exc:
            raise StudioError(
                409,
                "STUDY_WRITE_FAILED",
                "The Study evidence ledger could not be retained in its project.",
            ) from exc

    # A Study has no canonical writer at all: it only creates its own run and
    # retains run records, and neither can move HEAD or a design branch. So a
    # HEAD that differs across a save is someone else issuing a version, which
    # this save may not refuse — the ledger it just retained is already valid.
    return read_study(binding, study_id, ref.uri)
