"""Read-only V3 load-path pilot behind the quarantined CLI boundary."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from archflow.adapters.v3_legacy_cli import (
    V3LegacyCapabilityRequest,
    V3LegacyCapabilityReceipt,
    V3LegacyCliBridge,
    V3LegacyStatus,
)
from archflow.capabilities.experts import (
    ExpertAdvice,
    ExpertRegistry,
    ExpertSnapshot,
    ExpertSpec,
)
from archflow.project import ProjectVersionRef
from archflow.project.refs import require_identifier
from archflow.contracts.canonical import canonical_digest, canonical_json, require_sha256


CAPABILITY_ID = "v3.gate.load_path_analysis"
EXPERT_ID = "expert.v3_load_path_analysis"
COMPONENT_GRAPH_EVIDENCE_KIND = "component_graph"
LOAD_PATH_TOPICS = frozenset(
    {
        "constructibility",
        "floating",
        "load_path",
        "structural_continuity",
        "support",
    }
)
_LOAD_ROLES = {"load_source", "collector", "support", "nonstructural"}
_STRUCTURAL_LAYERS = {"primary", "infill", "nonstructural"}


class V3DiagnosticError(ValueError):
    """The selected diagnostic input does not bind the current expert state."""


class V3DiagnosticStatus(StrEnum):
    OBSERVATION = "observation"
    PROVIDER_FAILURE = "provider_failure"
    INVALID_OUTPUT = "invalid_output"


@dataclass(frozen=True, slots=True)
class V3DiagnosticInput:
    input_id: str
    run_id: str
    workspace_id: str
    base: ProjectVersionRef
    evidence_ref: str
    component_graph_json: str
    input_sha256: str

    SCHEMA = "V3DiagnosticInput@1"

    def __post_init__(self) -> None:
        require_identifier(self.input_id, "input_id")
        require_identifier(self.run_id, "run_id")
        require_identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        self.base.require_digest()
        _text(self.evidence_ref, "evidence_ref", maximum=4_000)
        graph = _validate_component_graph_json(self.component_graph_json)
        if _sha(self.component_graph_json.encode("utf-8")) != self.input_sha256:
            raise V3DiagnosticError(
                "input_sha256 does not bind the component graph"
            )
        if graph["schema"] != "NeutralComponentGraph@1":
            raise AssertionError("validated graph schema changed")

    @classmethod
    def create(
        cls,
        *,
        input_id: str,
        run_id: str,
        workspace_id: str,
        base: ProjectVersionRef,
        evidence_ref: str,
        component_graph: dict[str, Any],
    ) -> V3DiagnosticInput:
        encoded = _validate_component_graph(component_graph)
        return cls(
            input_id=input_id,
            run_id=run_id,
            workspace_id=workspace_id,
            base=base,
            evidence_ref=evidence_ref,
            component_graph_json=encoded,
            input_sha256=_sha(encoded.encode("utf-8")),
        )

    @classmethod
    def from_dict(cls, value: object) -> V3DiagnosticInput:
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "input_id",
            "run_id",
            "workspace_id",
            "base",
            "evidence_ref",
            "component_graph",
            "input_sha256",
        }:
            raise V3DiagnosticError("diagnostic input fields drifted")
        if value["schema"] != cls.SCHEMA:
            raise V3DiagnosticError("diagnostic input schema is unsupported")
        base = value["base"]
        if not isinstance(base, dict) or set(base) != {
            "project_id",
            "version",
            "state_sha256",
        }:
            raise V3DiagnosticError("diagnostic base fields drifted")
        return cls(
            input_id=value["input_id"],
            run_id=value["run_id"],
            workspace_id=value["workspace_id"],
            base=ProjectVersionRef(
                project_id=base["project_id"],
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
            evidence_ref=value["evidence_ref"],
            component_graph_json=_validate_component_graph(
                value["component_graph"]
            ),
            input_sha256=value["input_sha256"],
        )

    @property
    def component_graph(self) -> dict[str, Any]:
        return json.loads(self.component_graph_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "input_id": self.input_id,
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "base": _base_dict(self.base),
            "evidence_ref": self.evidence_ref,
            "component_graph": self.component_graph,
            "input_sha256": self.input_sha256,
        }


@dataclass(frozen=True, slots=True)
class V3SupportPath:
    source_id: str
    path: tuple[str, ...]

    def __post_init__(self) -> None:
        require_identifier(self.source_id, "source_id")
        if not isinstance(self.path, tuple) or not self.path:
            raise V3DiagnosticError("support path must be non-empty")
        for item in self.path:
            require_identifier(item, "support path item")
        if self.path[0] != self.source_id:
            raise V3DiagnosticError("support path must start at its source")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "path": list(self.path),
        }


@dataclass(frozen=True, slots=True)
class V3LoadPathInvariants:
    all_load_sources_grounded: bool
    hard_finding_count: int
    finding_codes: tuple[str, ...]
    unsupported_component_ids: tuple[str, ...]
    support_paths: tuple[V3SupportPath, ...]

    SCHEMA = "V3LoadPathSemanticInvariants@1"

    def __post_init__(self) -> None:
        if not isinstance(self.all_load_sources_grounded, bool):
            raise TypeError("all_load_sources_grounded must be bool")
        if (
            type(self.hard_finding_count) is not int
            or self.hard_finding_count < 0
        ):
            raise ValueError("hard_finding_count must be non-negative")
        for values, field in (
            (self.finding_codes, "finding_codes"),
            (
                self.unsupported_component_ids,
                "unsupported_component_ids",
            ),
        ):
            if not isinstance(values, tuple):
                raise TypeError(f"{field} must be tuple")
            if values != tuple(sorted(set(values))):
                raise V3DiagnosticError(
                    f"{field} must be unique and sorted"
                )
            for item in values:
                require_identifier(item, f"{field} item")
        if (
            not isinstance(self.support_paths, tuple)
            or any(
                not isinstance(item, V3SupportPath)
                for item in self.support_paths
            )
        ):
            raise TypeError("support_paths must contain V3SupportPath values")
        if tuple(
            item.source_id for item in self.support_paths
        ) != tuple(
            sorted(item.source_id for item in self.support_paths)
        ):
            raise V3DiagnosticError("support_paths must be sorted by source")
        if self.all_load_sources_grounded == bool(
            self.unsupported_component_ids
        ):
            raise V3DiagnosticError(
                "grounded invariant disagrees with unsupported components"
            )

    @classmethod
    def from_payload(cls, value: object) -> V3LoadPathInvariants:
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "all_load_sources_grounded",
            "hard_finding_count",
            "finding_codes",
            "unsupported_component_ids",
            "support_paths",
            "loaded_forbidden_modules",
        }:
            raise V3DiagnosticError("semantic invariant fields drifted")
        if value["schema"] != cls.SCHEMA:
            raise V3DiagnosticError(
                "semantic invariant schema is unsupported"
            )
        if value["loaded_forbidden_modules"] != []:
            raise V3DiagnosticError(
                "provider loaded a forbidden V2 or Pack module"
            )
        paths = value["support_paths"]
        if not isinstance(paths, list):
            raise TypeError("support_paths must be a JSON array")
        parsed_paths = []
        for item in paths:
            if not isinstance(item, dict) or set(item) != {
                "source_id",
                "path",
            }:
                raise V3DiagnosticError("support path fields drifted")
            if not isinstance(item["path"], list):
                raise TypeError("support path value must be a JSON array")
            parsed_paths.append(
                V3SupportPath(
                    source_id=item["source_id"],
                    path=tuple(item["path"]),
                )
            )
        return cls(
            all_load_sources_grounded=value[
                "all_load_sources_grounded"
            ],
            hard_finding_count=value["hard_finding_count"],
            finding_codes=tuple(value["finding_codes"]),
            unsupported_component_ids=tuple(
                value["unsupported_component_ids"]
            ),
            support_paths=tuple(parsed_paths),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "all_load_sources_grounded": (
                self.all_load_sources_grounded
            ),
            "hard_finding_count": self.hard_finding_count,
            "finding_codes": list(self.finding_codes),
            "unsupported_component_ids": list(
                self.unsupported_component_ids
            ),
            "support_paths": [
                item.to_dict() for item in self.support_paths
            ],
        }


@dataclass(frozen=True, slots=True)
class V3DiagnosticReceipt:
    receipt_id: str
    status: V3DiagnosticStatus
    base: ProjectVersionRef
    input_id: str
    input_sha256: str
    obligation_id: str
    provider_receipt: V3LegacyCapabilityReceipt
    invariants: V3LoadPathInvariants | None
    suggested_obligations: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    message: str | None = None

    SCHEMA = "V3DiagnosticReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.receipt_id, "receipt_id")
        if not isinstance(self.status, V3DiagnosticStatus):
            raise TypeError("status must be V3DiagnosticStatus")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        self.base.require_digest()
        require_identifier(self.input_id, "input_id")
        require_sha256(self.input_sha256, "input_sha256")
        require_identifier(self.obligation_id, "obligation_id")
        if not isinstance(
            self.provider_receipt,
            V3LegacyCapabilityReceipt,
        ):
            raise TypeError(
                "provider_receipt must be V3LegacyCapabilityReceipt"
            )
        if self.provider_receipt.request.base != self.base:
            raise V3DiagnosticError(
                "provider receipt targets another canonical base"
            )
        if self.status is V3DiagnosticStatus.OBSERVATION:
            if (
                self.invariants is None
                or self.provider_receipt.status
                is not V3LegacyStatus.SUCCESS
            ):
                raise V3DiagnosticError(
                    "observation requires successful provider invariants"
                )
        elif self.invariants is not None or self.suggested_obligations:
            raise V3DiagnosticError(
                "failed diagnostic cannot suggest design changes"
            )
        if not isinstance(self.suggested_obligations, tuple):
            raise TypeError("suggested_obligations must be tuple")
        if not isinstance(self.evidence_refs, tuple):
            raise TypeError("evidence_refs must be tuple")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "status": self.status.value,
            "base": _base_dict(self.base),
            "input_id": self.input_id,
            "input_sha256": self.input_sha256,
            "obligation_id": self.obligation_id,
            "provider_receipt": self.provider_receipt.to_dict(),
            "invariants": (
                self.invariants.to_dict()
                if self.invariants is not None
                else None
            ),
            "suggested_obligations": list(
                self.suggested_obligations
            ),
            "evidence_refs": list(self.evidence_refs),
            "message": self.message,
            "geometry_edit_authority": False,
            "hard_gate_waiver_authority": False,
            "aesthetic_winner_authority": False,
            "canonical_write_authority": False,
            "live_world_authority": False,
        }


class V3LoadPathDiagnostic:
    def __init__(self, bridge: V3LegacyCliBridge) -> None:
        if not isinstance(bridge, V3LegacyCliBridge):
            raise TypeError("bridge must be V3LegacyCliBridge")
        if bridge.spec.capability_id != CAPABILITY_ID:
            raise V3DiagnosticError(
                "bridge does not bind the load-path capability"
            )
        self.bridge = bridge

    def diagnose(
        self,
        snapshot: ExpertSnapshot,
        diagnostic_input: V3DiagnosticInput,
        *,
        obligation_id: str,
    ) -> V3DiagnosticReceipt:
        if not isinstance(snapshot, ExpertSnapshot):
            raise TypeError("snapshot must be ExpertSnapshot")
        if not isinstance(diagnostic_input, V3DiagnosticInput):
            raise TypeError(
                "diagnostic_input must be V3DiagnosticInput"
            )
        if snapshot.base_state != diagnostic_input.base:
            raise V3DiagnosticError(
                "diagnostic input does not bind the expert base"
            )
        obligations = {
            item.obligation_id: item for item in snapshot.obligations
        }
        obligation = obligations.get(obligation_id)
        if obligation is None or obligation.topic not in LOAD_PATH_TOPICS:
            raise V3DiagnosticError(
                "selected obligation does not request load-path analysis"
            )
        matching_evidence = [
            item
            for item in snapshot.evidence
            if item.kind == COMPONENT_GRAPH_EVIDENCE_KIND
            and item.evidence_ref == diagnostic_input.evidence_ref
        ]
        if len(matching_evidence) != 1:
            raise V3DiagnosticError(
                "exact component-graph evidence is unavailable"
            )
        request_id = (
            f"v3diag-{canonical_digest({
                'input_sha256': diagnostic_input.input_sha256,
                'obligation_id': obligation_id,
                'base': _base_dict(diagnostic_input.base),
            }, ascii=False)[:20]}"
        )
        provider_receipt = self.bridge.invoke(
            V3LegacyCapabilityRequest.create(
                request_id=request_id,
                project_id=diagnostic_input.base.project_id,
                run_id=diagnostic_input.run_id,
                workspace_id=diagnostic_input.workspace_id,
                base=diagnostic_input.base,
                capability_id=CAPABILITY_ID,
                detached_snapshot={
                    "schema": "V3LoadPathInput@1",
                    "input_id": diagnostic_input.input_id,
                    "input_sha256": diagnostic_input.input_sha256,
                    "component_graph": (
                        diagnostic_input.component_graph
                    ),
                },
                obligation={
                    "obligation_id": obligation.obligation_id,
                    "topic": obligation.topic,
                    "statement": obligation.statement,
                    "source_ref": obligation.source_ref,
                },
                evidence_refs=tuple(
                    dict.fromkeys(
                        (
                            diagnostic_input.evidence_ref,
                            obligation.source_ref,
                        )
                    )
                ),
            )
        )
        if provider_receipt.status is not V3LegacyStatus.SUCCESS:
            return self._receipt(
                diagnostic_input,
                obligation_id,
                provider_receipt,
                V3DiagnosticStatus.PROVIDER_FAILURE,
                message=provider_receipt.message,
            )
        try:
            invariants = self._parse_observation(
                provider_receipt,
                diagnostic_input,
            )
        except (TypeError, ValueError) as exc:
            return self._receipt(
                diagnostic_input,
                obligation_id,
                provider_receipt,
                V3DiagnosticStatus.INVALID_OUTPUT,
                message=f"{type(exc).__name__}: {exc}"[:500],
            )
        suggestions = tuple(
            (
                "Resolve the detached load path for component "
                f"{component_id}."
            )
            for component_id in invariants.unsupported_component_ids
        )
        return self._receipt(
            diagnostic_input,
            obligation_id,
            provider_receipt,
            V3DiagnosticStatus.OBSERVATION,
            invariants=invariants,
            suggestions=suggestions,
        )

    def expert_handler(
        self,
        input_resolver: Callable[
            [ExpertSnapshot],
            tuple[V3DiagnosticInput, str],
        ],
    ) -> Callable[[ExpertSnapshot], ExpertAdvice]:
        if not callable(input_resolver):
            raise TypeError("input_resolver must be callable")

        def handle(snapshot: ExpertSnapshot) -> ExpertAdvice:
            diagnostic_input, obligation_id = input_resolver(snapshot)
            receipt = self.diagnose(
                snapshot,
                diagnostic_input,
                obligation_id=obligation_id,
            )
            if receipt.status is not V3DiagnosticStatus.OBSERVATION:
                raise RuntimeError(
                    f"{receipt.status.value}: {receipt.message}"
                )
            invariants = receipt.invariants
            assert invariants is not None
            return ExpertAdvice(
                summary=(
                    "Detached V3 load-path observation: "
                    f"all_load_sources_grounded="
                    f"{invariants.all_load_sources_grounded}."
                ),
                findings=tuple(
                    (
                        f"{code}: "
                        f"{invariants.hard_finding_count} hard finding(s)"
                    )
                    for code in invariants.finding_codes
                ),
                suggested_obligations=receipt.suggested_obligations,
                evidence_refs=receipt.evidence_refs,
            )

        return handle

    @staticmethod
    def _parse_observation(
        provider_receipt: V3LegacyCapabilityReceipt,
        diagnostic_input: V3DiagnosticInput,
    ) -> V3LoadPathInvariants:
        output = provider_receipt.output
        if output is None or output.kind != "observation":
            raise V3DiagnosticError(
                "load-path provider did not return an observation"
            )
        payload = output.payload
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "input_id",
            "input_sha256",
            "semantic_invariants",
        }:
            raise V3DiagnosticError(
                "load-path observation fields drifted"
            )
        if (
            payload["schema"] != "V3LoadPathObservation@1"
            or payload["input_id"] != diagnostic_input.input_id
            or payload["input_sha256"]
            != diagnostic_input.input_sha256
        ):
            raise V3DiagnosticError(
                "load-path observation targets another input"
            )
        return V3LoadPathInvariants.from_payload(
            payload["semantic_invariants"]
        )

    @staticmethod
    def _receipt(
        diagnostic_input: V3DiagnosticInput,
        obligation_id: str,
        provider_receipt: V3LegacyCapabilityReceipt,
        status: V3DiagnosticStatus,
        *,
        invariants: V3LoadPathInvariants | None = None,
        suggestions: tuple[str, ...] = (),
        message: str | None = None,
    ) -> V3DiagnosticReceipt:
        evidence_refs = tuple(
            dict.fromkeys(
                (
                    diagnostic_input.evidence_ref,
                    *(
                        provider_receipt.output.evidence_refs
                        if provider_receipt.output is not None
                        else ()
                    ),
                    f"provider-receipt:{provider_receipt.receipt_id}",
                )
            )
        )
        identity = {
            "status": status.value,
            "base": _base_dict(diagnostic_input.base),
            "input_sha256": diagnostic_input.input_sha256,
            "obligation_id": obligation_id,
            "provider_receipt_id": provider_receipt.receipt_id,
        }
        return V3DiagnosticReceipt(
            receipt_id=f"v3-diagnostic-{canonical_digest(identity, ascii=False)[:20]}",
            status=status,
            base=diagnostic_input.base,
            input_id=diagnostic_input.input_id,
            input_sha256=diagnostic_input.input_sha256,
            obligation_id=obligation_id,
            provider_receipt=provider_receipt,
            invariants=invariants,
            suggested_obligations=suggestions,
            evidence_refs=evidence_refs,
            message=message,
        )


def v3_load_path_expert_spec() -> ExpertSpec:
    return ExpertSpec(
        expert_id=EXPERT_ID,
        description=(
            "Reads a detached neutral component graph and reports V3 "
            "load-path semantic invariants."
        ),
        topics=LOAD_PATH_TOPICS,
        required_evidence_kinds=frozenset(
            {COMPONENT_GRAPH_EVIDENCE_KIND}
        ),
        max_attempts=1,
        side_effects=False,
    )


def register_v3_load_path_capability(
    registry: ExpertRegistry,
    diagnostic: V3LoadPathDiagnostic,
    input_resolver: Callable[
        [ExpertSnapshot],
        tuple[V3DiagnosticInput, str],
    ],
) -> None:
    if not isinstance(registry, ExpertRegistry):
        raise TypeError("registry must be ExpertRegistry")
    if not isinstance(diagnostic, V3LoadPathDiagnostic):
        raise TypeError("diagnostic must be V3LoadPathDiagnostic")
    registry.register(
        v3_load_path_expert_spec(),
        diagnostic.expert_handler(input_resolver),
    )


def _validate_component_graph_json(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        raise TypeError("component_graph_json must be text")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise V3DiagnosticError("component graph JSON is malformed") from exc
    if _validate_component_graph(decoded) != value:
        raise V3DiagnosticError("component graph JSON must be canonical")
    return decoded


def _validate_component_graph(value: object) -> str:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "coordinate_system",
        "components",
    }:
        raise V3DiagnosticError("component graph fields drifted")
    if value["schema"] != "NeutralComponentGraph@1":
        raise V3DiagnosticError("component graph schema is unsupported")
    if value["coordinate_system"] != "cartesian_xyz_ground_z0":
        raise V3DiagnosticError("component graph coordinate system drifted")
    components = value["components"]
    if not isinstance(components, list) or not 1 <= len(components) <= 256:
        raise V3DiagnosticError(
            "component graph must contain 1 to 256 components"
        )
    ids = []
    for component in components:
        if not isinstance(component, dict) or set(component) != {
            "component_id",
            "load_role",
            "bounds",
            "structural_layer",
            "bears",
        }:
            raise V3DiagnosticError("component fields drifted")
        require_identifier(component["component_id"], "component_id")
        ids.append(component["component_id"])
        if component["load_role"] not in _LOAD_ROLES:
            raise V3DiagnosticError("component load_role is unsupported")
        if component["structural_layer"] not in _STRUCTURAL_LAYERS:
            raise V3DiagnosticError(
                "component structural_layer is unsupported"
            )
        bounds = component["bounds"]
        if not isinstance(bounds, dict) or set(bounds) != {"min", "max"}:
            raise V3DiagnosticError("component bounds fields drifted")
        minimum = bounds["min"]
        maximum = bounds["max"]
        if (
            not isinstance(minimum, list)
            or not isinstance(maximum, list)
            or len(minimum) != 3
            or len(maximum) != 3
        ):
            raise V3DiagnosticError(
                "component bounds must contain two xyz triples"
            )
        for low, high in zip(minimum, maximum, strict=True):
            if (
                not isinstance(low, (int, float))
                or isinstance(low, bool)
                or not isinstance(high, (int, float))
                or isinstance(high, bool)
                or not math.isfinite(low)
                or not math.isfinite(high)
                or low >= high
            ):
                raise V3DiagnosticError(
                    "component bounds must be finite and increasing"
                )
        bears = component["bears"]
        if (
            not isinstance(bears, list)
            or len(bears) != len(set(bears))
        ):
            raise V3DiagnosticError("bears must be a unique JSON array")
        for carried in bears:
            require_identifier(carried, "bears item")
    if len(ids) != len(set(ids)):
        raise V3DiagnosticError("component ids must be unique")
    known = set(ids)
    for component in components:
        carried = set(component["bears"])
        if component["component_id"] in carried or not carried <= known:
            raise V3DiagnosticError(
                "declared support edges must name other components"
            )
    return canonical_json(value, ascii=False)


def _base_dict(base: ProjectVersionRef) -> dict[str, Any]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(value: object, field: str, *, maximum: int = 1_000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be bounded non-empty text")
