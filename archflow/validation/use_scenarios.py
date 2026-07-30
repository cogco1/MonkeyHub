"""Deterministic, read-only voxel use-scenario validation."""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from typing import ClassVar

from archflow.adapters.voxel_observation import Coordinate, VoxelObservation
from archflow.state import BuildingProgram, CanonicalState
from archflow.submission import CandidateSubmission
from archflow.validation.model import Finding
from archflow.validation.usability import UseZoneEvidence


class UseScenarioKind(StrEnum):
    ENTRANCE_TO_ZONE = "entrance_to_zone"
    INTER_ZONE = "inter_zone"


class UseScenarioStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    EVIDENCE_UNKNOWN = "evidence_unknown"


class ScenarioObservationSource(StrEnum):
    SANDBOX_REALIZATION = "sandbox_realization"
    EXTERNAL_COMPARISON = "external_comparison"


def _sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value.lower())
    ):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _content_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ScenarioObservationBinding:
    """Exact source chain for a primary or comparison observation."""

    binding_id: str
    source: ScenarioObservationSource
    candidate_program_digest: str
    geometry_program_digest: str
    source_receipt_digest: str
    validation_program_digest: str
    observation_id: str
    observation_digest: str
    source_artifact_id: str
    source_artifact_sha256: str
    workspace_id: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "ScenarioObservationBinding@1"

    def __post_init__(self) -> None:
        for field, value in (
            ("binding_id", self.binding_id),
            ("observation_id", self.observation_id),
            ("source_artifact_id", self.source_artifact_id),
            ("workspace_id", self.workspace_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-empty")
        if not isinstance(self.source, ScenarioObservationSource):
            raise TypeError("source must be ScenarioObservationSource")
        for field in (
            "candidate_program_digest",
            "geometry_program_digest",
            "source_receipt_digest",
            "validation_program_digest",
            "observation_digest",
            "source_artifact_sha256",
        ):
            object.__setattr__(
                self,
                field,
                _sha256(getattr(self, field), field),
            )
        if (
            not isinstance(self.evidence_refs, tuple)
            or not self.evidence_refs
            or len(self.evidence_refs) != len(set(self.evidence_refs))
        ):
            raise ValueError(
                "observation binding requires unique evidence references"
            )
        if any(
            not isinstance(value, str) or not value.strip()
            for value in self.evidence_refs
        ):
            raise ValueError(
                "observation binding evidence must be non-empty text"
            )

    @classmethod
    def from_sandbox_realization(
        cls,
        *,
        binding_id: str,
        program: BuildingProgram,
        observation: VoxelObservation,
        candidate_program_digest: str,
        geometry_program_digest: str,
        realization_receipt_digest: str,
        evidence_refs: tuple[str, ...],
    ) -> ScenarioObservationBinding:
        if not isinstance(program, BuildingProgram):
            raise TypeError("program must be BuildingProgram")
        if not isinstance(observation, VoxelObservation):
            raise TypeError("observation must be VoxelObservation")
        return cls(
            binding_id=binding_id,
            source=ScenarioObservationSource.SANDBOX_REALIZATION,
            candidate_program_digest=candidate_program_digest,
            geometry_program_digest=geometry_program_digest,
            source_receipt_digest=realization_receipt_digest,
            validation_program_digest=_content_digest(program.to_json()),
            observation_id=observation.observation_id,
            observation_digest=_content_digest(observation.to_json()),
            source_artifact_id=observation.source_artifact_id,
            source_artifact_sha256=observation.source_artifact_sha256,
            workspace_id=observation.workspace_id,
            evidence_refs=evidence_refs,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "binding_id": self.binding_id,
            "source": self.source.value,
            "candidate_program_digest": self.candidate_program_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "source_receipt_digest": self.source_receipt_digest,
            "validation_program_digest": self.validation_program_digest,
            "observation_id": self.observation_id,
            "observation_digest": self.observation_digest,
            "source_artifact_id": self.source_artifact_id,
            "source_artifact_sha256": self.source_artifact_sha256,
            "workspace_id": self.workspace_id,
            "evidence_refs": list(self.evidence_refs),
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class VerticalCirculationEvidence:
    circulation_id: str
    kind: str
    path: tuple[Coordinate, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.circulation_id.strip():
            raise ValueError("circulation_id must be non-empty")
        if self.kind not in {"stair", "ladder", "ramp", "lift"}:
            raise ValueError("vertical circulation kind is unsupported")
        if not isinstance(self.path, tuple) or len(self.path) < 2:
            raise ValueError(
                "vertical circulation path requires at least two cells"
            )
        if not isinstance(self.evidence_refs, tuple) or not self.evidence_refs:
            raise ValueError(
                "vertical circulation requires evidence references"
            )
        pairs = tuple(zip(self.path, self.path[1:]))
        if not any(first[1] != second[1] for first, second in pairs):
            raise ValueError("vertical circulation path changes no level")
        for first, second in pairs:
            delta = tuple(
                abs(left - right)
                for left, right in zip(first, second, strict=True)
            )
            if delta[1] > 1 or sum(delta) > 2 or sum(delta) == 0:
                raise ValueError(
                    "vertical circulation cells must form bounded steps"
                )


@dataclass(frozen=True, slots=True)
class UseScenario:
    scenario_id: str
    kind: UseScenarioKind
    source_space: str | None
    target_space: str
    source_region_ids: tuple[str, ...]
    target_region_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UseScenarioResult:
    scenario: UseScenario
    status: UseScenarioStatus
    route: tuple[Coordinate, ...]
    measured: str
    threshold: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UseScenarioValidator:
    """Hard validator accepted by the existing validation engine."""

    program: BuildingProgram
    observation: VoxelObservation
    use_zones: tuple[UseZoneEvidence, ...]
    observation_binding: ScenarioObservationBinding | None = None
    candidate_program_digest: str | None = None
    geometry_program_digest: str | None = None
    realization_receipt_digest: str | None = None
    vertical_circulation: tuple[VerticalCirculationEvidence, ...] = ()

    name: ClassVar[str] = "voxel-use-scenarios"

    def __post_init__(self) -> None:
        if not isinstance(self.program, BuildingProgram):
            raise TypeError("program must be BuildingProgram")
        if not isinstance(self.observation, VoxelObservation):
            raise TypeError("observation must be VoxelObservation")
        if not isinstance(self.use_zones, tuple):
            raise TypeError("use_zones must be a tuple")
        if (
            self.observation_binding is not None
            and not isinstance(
                self.observation_binding,
                ScenarioObservationBinding,
            )
        ):
            raise TypeError(
                "observation_binding must be ScenarioObservationBinding"
            )
        for field in (
            "candidate_program_digest",
            "geometry_program_digest",
            "realization_receipt_digest",
        ):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(
                    self,
                    field,
                    _sha256(value, field),
                )
        if not isinstance(self.vertical_circulation, tuple):
            raise TypeError("vertical_circulation must be a tuple")

    def validate(
        self,
        state: CanonicalState,
        submission: CandidateSubmission,
    ) -> tuple[Finding, ...]:
        binding = self._binding_findings(state, submission)
        if binding:
            return binding
        results, graph_findings = evaluate_use_scenarios(
            self.program,
            self.observation,
            use_zones=self.use_zones,
            vertical_circulation=self.vertical_circulation,
        )
        findings = list(graph_findings)
        for result in results:
            if result.status is UseScenarioStatus.PASSED:
                continue
            findings.append(
                Finding(
                    code=(
                        f"use_scenario.{result.scenario.kind.value}."
                        f"{result.status.value}"
                    ),
                    message=(
                        f"{result.scenario.scenario_id}: "
                        f"{result.measured}; required {result.threshold}"
                    ),
                    evidence_refs=result.evidence_refs,
                )
            )
        return tuple(findings)

    def _binding_findings(
        self,
        state: CanonicalState,
        submission: CandidateSubmission,
    ) -> tuple[Finding, ...]:
        binding = self.observation_binding
        if binding is None or any(
            value is None
            for value in (
                self.candidate_program_digest,
                self.geometry_program_digest,
                self.realization_receipt_digest,
            )
        ):
            return (
                Finding(
                    code="use_scenario.sandbox_binding_missing",
                    message=(
                        "primary use-scenario validation requires an exact "
                        "sandbox-realization observation binding"
                    ),
                    evidence_refs=(self.observation.observation_id,),
                ),
            )
        if binding.source is not ScenarioObservationSource.SANDBOX_REALIZATION:
            return (
                Finding(
                    code="use_scenario.primary_source_not_sandbox",
                    message=(
                        "external comparison evidence cannot become the "
                        "primary use-scenario observation"
                    ),
                    evidence_refs=binding.evidence_refs,
                ),
            )
        expected = {
            "candidate_program_digest": self.candidate_program_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "source_receipt_digest": self.realization_receipt_digest,
            "validation_program_digest": _content_digest(
                self.program.to_json()
            ),
            "observation_id": self.observation.observation_id,
            "observation_digest": _content_digest(
                self.observation.to_json()
            ),
            "source_artifact_id": self.observation.source_artifact_id,
            "source_artifact_sha256": (
                self.observation.source_artifact_sha256
            ),
            "workspace_id": self.observation.workspace_id,
        }
        actual = {
            field: getattr(binding, field) for field in expected
        }
        if actual != expected:
            return (
                Finding(
                    code="use_scenario.sandbox_binding_mismatch",
                    message=(
                        "sandbox binding does not match the exact candidate, "
                        "program, observation, artifact, or workspace"
                    ),
                    evidence_refs=tuple(
                        dict.fromkeys(
                            (
                                self.observation.observation_id,
                                *binding.evidence_refs,
                            )
                        )
                    ),
                ),
            )
        if self.observation.base_state != state.ref:
            return (
                Finding(
                    code="use_scenario.state_binding_mismatch",
                    message=(
                        "voxel observation is bound to another canonical "
                        "state"
                    ),
                    evidence_refs=(self.observation.observation_id,),
                ),
            )
        matches = tuple(
            artifact
            for artifact in submission.delta.artifacts_add
            if (
                artifact.artifact_id
                == self.observation.source_artifact_id
                and artifact.sha256
                == self.observation.source_artifact_sha256
            )
        )
        if len(matches) != 1:
            return (
                Finding(
                    code="use_scenario.candidate_binding_mismatch",
                    message=(
                        "scenario observation must bind exactly one artifact "
                        "in the checked submission"
                    ),
                    evidence_refs=(self.observation.observation_id,),
                ),
            )
        return ()


def compile_use_scenarios(
    program: BuildingProgram,
    *,
    use_zones: tuple[UseZoneEvidence, ...],
) -> tuple[UseScenario, ...]:
    """Derive route obligations from current program and zone evidence."""

    if not isinstance(program, BuildingProgram):
        raise TypeError("program must be BuildingProgram")
    if not isinstance(use_zones, tuple):
        raise TypeError("use_zones must be a tuple")
    by_space: dict[str, list[UseZoneEvidence]] = {}
    for zone in use_zones:
        by_space.setdefault(zone.space, []).append(zone)
    scenarios: list[UseScenario] = []
    for space in program.required_spaces:
        zones = tuple(sorted(by_space.get(space, ()), key=_zone_key))
        scenarios.append(
            UseScenario(
                scenario_id=f"entrance-to-{space}",
                kind=UseScenarioKind.ENTRANCE_TO_ZONE,
                source_space=None,
                target_space=space,
                source_region_ids=(),
                target_region_ids=tuple(
                    dict.fromkeys(zone.region_id for zone in zones)
                ),
                evidence_refs=_zone_evidence(zones),
            )
        )
    for source_space, target_space in combinations(
        program.required_spaces,
        2,
    ):
        source_zones = tuple(
            sorted(by_space.get(source_space, ()), key=_zone_key)
        )
        target_zones = tuple(
            sorted(by_space.get(target_space, ()), key=_zone_key)
        )
        scenarios.append(
            UseScenario(
                scenario_id=f"{source_space}-to-{target_space}",
                kind=UseScenarioKind.INTER_ZONE,
                source_space=source_space,
                target_space=target_space,
                source_region_ids=tuple(
                    dict.fromkeys(
                        zone.region_id for zone in source_zones
                    )
                ),
                target_region_ids=tuple(
                    dict.fromkeys(
                        zone.region_id for zone in target_zones
                    )
                ),
                evidence_refs=_zone_evidence(
                    (*source_zones, *target_zones)
                ),
            )
        )
    return tuple(scenarios)


def evaluate_use_scenarios(
    program: BuildingProgram,
    observation: VoxelObservation,
    *,
    use_zones: tuple[UseZoneEvidence, ...],
    vertical_circulation: tuple[VerticalCirculationEvidence, ...] = (),
) -> tuple[tuple[UseScenarioResult, ...], tuple[Finding, ...]]:
    """Evaluate routes without an MCP client, writer, or mutation channel."""

    scenarios = compile_use_scenarios(program, use_zones=use_zones)
    graph, graph_findings = _walkable_graph(
        observation,
        vertical_circulation,
    )
    regions = {
        region.region_id: frozenset(region.cells)
        for region in observation.connected_regions
    }
    entrance_cells = frozenset(
        cell
        for cell in observation.openings
        if cell in graph and _is_exterior(cell, observation)
    )
    clearance = {
        cell: _clearance_status(cell, program, observation)
        for cell in graph
    }
    usable_graph = {
        cell: tuple(
            neighbor
            for neighbor in neighbors
            if clearance[neighbor] == "clear"
        )
        for cell, neighbors in graph.items()
        if clearance[cell] == "clear"
    }
    results = tuple(
        _evaluate_scenario(
            scenario,
            observation,
            graph,
            usable_graph,
            clearance,
            regions,
            entrance_cells,
        )
        for scenario in scenarios
    )
    return results, graph_findings


def _evaluate_scenario(
    scenario: UseScenario,
    observation: VoxelObservation,
    graph: dict[Coordinate, tuple[Coordinate, ...]],
    usable_graph: dict[Coordinate, tuple[Coordinate, ...]],
    clearance: dict[Coordinate, str],
    regions: dict[str, frozenset[Coordinate]],
    entrance_cells: frozenset[Coordinate],
) -> UseScenarioResult:
    sources = (
        entrance_cells
        if scenario.kind is UseScenarioKind.ENTRANCE_TO_ZONE
        else _region_cells(scenario.source_region_ids, regions)
    )
    targets = _region_cells(scenario.target_region_ids, regions)
    evidence = tuple(
        dict.fromkeys(
            (observation.observation_id, *scenario.evidence_refs)
        )
    )
    if not sources or not targets:
        return UseScenarioResult(
            scenario=scenario,
            status=UseScenarioStatus.FAILED,
            route=(),
            measured=(
                f"source_cells={len(sources)}, target_cells={len(targets)}"
            ),
            threshold="bound source and target voxel evidence",
            evidence_refs=evidence,
        )
    route = _route(usable_graph, sources, targets)
    if route:
        return UseScenarioResult(
            scenario=scenario,
            status=UseScenarioStatus.PASSED,
            route=route,
            measured=f"route_length={len(route) - 1} steps",
            threshold="one collision-free route with required headroom",
            evidence_refs=tuple(
                dict.fromkeys(
                    (
                        *evidence,
                        *(_coordinate_ref(cell) for cell in route[:16]),
                    )
                )
            ),
        )
    raw_route = _route(graph, sources, targets)
    if raw_route:
        has_unknown = any(
            clearance[cell] == "unknown" for cell in raw_route
        )
        return UseScenarioResult(
            scenario=scenario,
            status=(
                UseScenarioStatus.EVIDENCE_UNKNOWN
                if has_unknown
                else UseScenarioStatus.FAILED
            ),
            route=raw_route,
            measured=(
                "topological route has unproven clearance"
                if has_unknown
                else "topological route exists but headroom blocks it"
            ),
            threshold="required headroom along every occupied route cell",
            evidence_refs=tuple(
                dict.fromkeys(
                    (
                        *evidence,
                        *(
                            _coordinate_ref(cell)
                            for cell in raw_route[:16]
                        ),
                    )
                )
            ),
        )
    status = (
        UseScenarioStatus.EVIDENCE_UNKNOWN
        if observation.unknown_count
        else UseScenarioStatus.FAILED
    )
    return UseScenarioResult(
        scenario=scenario,
        status=status,
        route=(),
        measured=(
            f"no route across {len(graph)} observed walkable cells; "
            f"unknown_cells={observation.unknown_count}"
        ),
        threshold="one evidence-bound reachable route",
        evidence_refs=evidence,
    )


def _walkable_graph(
    observation: VoxelObservation,
    vertical_circulation: tuple[VerticalCirculationEvidence, ...],
) -> tuple[
    dict[Coordinate, tuple[Coordinate, ...]],
    tuple[Finding, ...],
]:
    walkable = set(observation.walkable_cells)
    adjacency: dict[Coordinate, set[Coordinate]] = {
        cell: set() for cell in walkable
    }
    for cell in walkable:
        for neighbor in (
            (cell[0] - 1, cell[1], cell[2]),
            (cell[0] + 1, cell[1], cell[2]),
            (cell[0], cell[1], cell[2] - 1),
            (cell[0], cell[1], cell[2] + 1),
        ):
            if neighbor in walkable:
                adjacency[cell].add(neighbor)
    findings: list[Finding] = []
    for circulation in vertical_circulation:
        missing = tuple(
            cell for cell in circulation.path if cell not in walkable
        )
        if missing:
            findings.append(
                Finding(
                    code="use_scenario.vertical_evidence_invalid",
                    message=(
                        f"{circulation.circulation_id}: "
                        f"{len(missing)} path cells are not observed walkable"
                    ),
                    evidence_refs=circulation.evidence_refs,
                )
            )
            continue
        for first, second in zip(
            circulation.path,
            circulation.path[1:],
        ):
            adjacency[first].add(second)
            adjacency[second].add(first)
    return (
        {
            cell: tuple(sorted(neighbors))
            for cell, neighbors in sorted(adjacency.items())
        },
        tuple(findings),
    )


def _clearance_status(
    cell: Coordinate,
    program: BuildingProgram,
    observation: VoxelObservation,
) -> str:
    occupied = set(observation.occupied_cells)
    unknown = {item.coordinate for item in observation.unknowns}
    unlocalized_unknown = observation.unknown_count > len(unknown)
    for offset in range(program.minimum_clear_height):
        body_cell = (cell[0], cell[1] + offset, cell[2])
        if not observation.envelope.contains(body_cell):
            return "unknown"
        if body_cell in occupied:
            return "blocked"
        if body_cell in unknown or (
            unlocalized_unknown and offset >= 2
        ):
            return "unknown"
    return "clear"


def _route(
    graph: dict[Coordinate, tuple[Coordinate, ...]],
    sources: frozenset[Coordinate],
    targets: frozenset[Coordinate],
) -> tuple[Coordinate, ...]:
    seeds = tuple(sorted(cell for cell in sources if cell in graph))
    pending = deque(seeds)
    parent: dict[Coordinate, Coordinate | None] = {
        seed: None for seed in seeds
    }
    found: Coordinate | None = None
    while pending:
        current = pending.popleft()
        if current in targets:
            found = current
            break
        for neighbor in graph.get(current, ()):
            if neighbor not in parent:
                parent[neighbor] = current
                pending.append(neighbor)
    if found is None:
        return ()
    reversed_route = [found]
    while parent[reversed_route[-1]] is not None:
        predecessor = parent[reversed_route[-1]]
        if predecessor is None:
            break
        reversed_route.append(predecessor)
    return tuple(reversed(reversed_route))


def _region_cells(
    region_ids: tuple[str, ...],
    regions: dict[str, frozenset[Coordinate]],
) -> frozenset[Coordinate]:
    return frozenset(
        cell
        for region_id in region_ids
        for cell in regions.get(region_id, ())
    )


def _is_exterior(
    cell: Coordinate,
    observation: VoxelObservation,
) -> bool:
    min_x, _, min_z = observation.envelope.minimum
    max_x, _, max_z = observation.envelope.maximum
    return cell[0] in {min_x, max_x} or cell[2] in {min_z, max_z}


def _zone_key(zone: UseZoneEvidence) -> tuple[str, tuple[str, ...]]:
    return zone.region_id, zone.evidence_refs


def _zone_evidence(
    zones: tuple[UseZoneEvidence, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            evidence
            for zone in zones
            for evidence in zone.evidence_refs
        )
    )


def _coordinate_ref(cell: Coordinate) -> str:
    return f"voxel:{cell[0]},{cell[1]},{cell[2]}"
