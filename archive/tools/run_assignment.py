#!/usr/bin/env python3
"""Execute one preregistered P062 full-condition assignment end to end.

Stages:
  rehearse   deterministic terminal-chain rehearsal against existing production
             records (no provider call, target a disposable case copy)
  root       run the P053/P056 production root step with the frozen Codex CLI
             provider profile (the only stage that invokes a model)
  terminal   sandbox realization + P060 architectural usability + P061 family
             binding from this attempt's exact production records
  metrics    compute and persist the thirteen preregistered metric values
  bind       print the experiment receipt/outcome payloads for run_experiment.py

The driver owns no design, validation, review, promotion, or canonical-write
authority. Every output is a P036 run record in the case project; the study
project is only written through archive/tools/run_experiment.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archflow.capabilities.geometry_proposal import (  # noqa: E402
    GeometryProposalProviderIdentity,
    load_compiled_geometry_program,
)
from archive.archflow.capabilities.spatial import SpatialOptionProposal  # noqa: E402
from archive.archflow.production.provider_runtime import InvocationEvidenceCollector, activate_codex_agent_cli_provider
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef
from archive.archflow.realization.sandbox import SandboxRealizationPolicy, realize_geometry
from archive.archflow.runtime.family_compiler import (  # noqa: E402
    ComponentFamilyRealizationReceipt,
    FamilyRealizationStatus,
    compile_component_families,
)
from archive.archflow.runtime.production_compiler import (  # noqa: E402
    ProductionRootCompiler,
)
from archive.archflow.runtime.production_runtime import (  # noqa: E402
    ProductionAuthoringContext,
    ProductionRuntimeStepFailed,
    run_or_resume_production_step,
)
from archive.archflow.state.component_family import ComponentFamilySet
from archive.archflow.state.component_family import (  # noqa: E402
    ComponentFamilyInstance,
    ComponentFamilyKind,
    FamilyParameterRef,
    FamilySocket,
)
from archflow.state.developed_design import DevelopedDesignState  # noqa: E402
from archflow.contracts.canonical import canonical_digest
from archive.archflow.validation.architectural import (  # noqa: E402
    ArchitecturalCriterion,
    ArchitecturalObservation,
    AuthorizedCriterionSource,
    CriterionOperator,
    CriterionSourceKind,
    canonical_value,
    compile_architectural_usability_contract,
    evaluate_architectural_usability,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _record_content(payload: dict, *, expected_role: str) -> dict:
    if payload.get("schema") != "ProductionTransitionRecord@1" or (
        payload.get("role") != expected_role
    ):
        raise SystemExit(
            f"expected ProductionTransitionRecord role={expected_role}, "
            f"found schema={payload.get('schema')} role={payload.get('role')}"
        )
    return payload["content"]


def _run_records(repository, run):
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=run.run_id
    )
    return repository.list_json(run=run, destination=destination)


def _put(repository, run, kind: str, payload: dict) -> ProjectRecordRef:
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=run.run_id
    )
    ref = repository.put_json(
        run=run, destination=destination, record_kind=kind, payload=payload
    )
    print(f"  retained {ref.uri}")
    return ref


class Assignment:
    """Load one case project plus the refs this attempt binds."""

    def __init__(self, case_root: Path, condition: str = "full") -> None:
        self.condition = condition
        self.repository = FilesystemProjectRepository.open(case_root)
        self.run = self.repository.load_run("experiment-001")
        self.base = self.run.base
        records = _run_records(self.repository, self.run)
        self.by_kind: dict[str, list[ProjectRecordRef]] = {}
        for ref in records:
            name = ref.relative_path.rsplit("/", 1)[-1]
            kind = name.rsplit("-", 1)[0]
            self.by_kind.setdefault(kind, []).append(ref)
        preflights = self.by_kind.get("p062-assignment-preflight") or []
        if len(preflights) != 1:
            raise SystemExit("case must retain exactly one passed preflight")
        self.preflight = self.repository.load_json(preflights[0])
        if self.preflight.get("status") != "passed":
            raise SystemExit("assignment preflight did not pass")
        if condition == "generation-context":
            self.context_ref = self._ref(
                self.preflight["generation_ablation_context_ref"]
            )
        else:
            self.context_ref = self._ref(self.preflight["full_context_ref"])
        self.context = ProductionAuthoringContext.from_dict(
            self.repository.load_json(self.context_ref)
        )
        inputs = self.repository.list_json(
            run=self.run,
            destination=PersistenceDestination(PersistenceArea.INPUT),
        )
        raw = [
            (ref, self.repository.load_json(ref))
            for ref in inputs
            if "raw-request" in ref.relative_path
        ]
        if len(raw) != 1:
            raise SystemExit("case must retain exactly one raw request")
        self.raw_request_ref, raw_payload = raw[0]
        if raw_payload.get("schema") != "RawProjectRequest@1":
            raise SystemExit("raw request schema drifted")
        self.prompt = raw_payload["prompt"]
        self.evaluation_brief_ref = self._single("case-evaluation-brief")
        self.evaluation_brief = self.repository.load_json(
            self.evaluation_brief_ref
        )
        self.family_brief_ref = self._single("case-family-brief")
        self.family_brief = self.repository.load_json(self.family_brief_ref)
        self.program_ref = self._single("case-design-program")
        self.program = self.repository.load_json(self.program_ref)

    def _single(self, kind: str) -> ProjectRecordRef:
        refs = self.by_kind.get(kind) or []
        if len(refs) != 1:
            raise SystemExit(f"case must retain exactly one {kind}")
        return refs[0]

    def _ref(self, uri: str) -> ProjectRecordRef:
        for ref in _run_records(self.repository, self.run):
            if ref.uri == uri:
                return ref
        raise SystemExit(f"referenced record is absent: {uri}")


def _load_envelope(
    assignment: Assignment, envelope_kind: str, assignment_id: str
) -> dict:
    refs = assignment.by_kind.get(envelope_kind) or []
    payloads = [
        payload
        for payload in (assignment.repository.load_json(ref) for ref in refs)
        if payload.get("assignment_id") == assignment_id
    ]
    if not payloads:
        raise SystemExit(
            f"no {envelope_kind} record for {assignment_id}; "
            "run the earlier stage first"
        )
    payloads.sort(key=lambda item: item.get("sequence", 0))
    return payloads[-1]


def _resolve(assignment: Assignment, uri: str) -> dict:
    for ref in _run_records(assignment.repository, assignment.run):
        if ref.uri == uri:
            return assignment.repository.load_json(ref)
    raise SystemExit(f"record vanished: {uri}")


def stage_root(args, assignment: Assignment) -> int:
    step_id = f"{args.study_run}-{args.assignment}-attempt-{args.attempt:03d}"
    envelope_kind = f"p062-{args.study_run}-execution-envelope"
    profile = {
        "executable": args.codex,
        "model_id": "gpt-5.6-sol",
        "version": "codex-cli-0.145.0",
        "timeout_seconds": 300.0,
        "reasoning_effort": "low",
    }
    collector = InvocationEvidenceCollector()
    provider = activate_codex_agent_cli_provider(
        executable=profile["executable"],
        model_id=profile["model_id"],
        version=profile["version"],
        responsibility_id="model.production-root",
        contract_owner_id="archflow.production-root",
        verification_evidence_refs=(assignment.context_ref.uri,),
        timeout_seconds=profile["timeout_seconds"],
        reasoning_effort=profile["reasoning_effort"],
        envelope_observer=collector.observe,
    )
    active = provider.router.state("model.production-root").active_provider
    if active is None:
        raise SystemExit("codex provider failed activation")
    compiler = ProductionRootCompiler(
        repository=assignment.repository,
        context_ref=assignment.context_ref,
        context=assignment.context,
        provider=provider,
        evidence_collector=collector,
        geometry_provider_identity=GeometryProposalProviderIdentity(
            provider_id=active.provider_id,
            model_id=profile["model_id"],
            provider_version=active.version,
            provider_fingerprint=active.fingerprint,
        ),
    )
    before_refs = {
        ref.uri for ref in _run_records(assignment.repository, assignment.run)
    }
    started = time.time()
    envelope = {
        "schema": "P062AssignmentExecutionEnvelope@1",
        "project_id": assignment.run.project_id,
        "run_id": assignment.run.run_id,
        "base": {
            "project_id": assignment.base.project_id,
            "version": assignment.base.version,
            "state_sha256": assignment.base.require_digest(),
        },
        "study_run_id": args.study_run,
        "assignment_id": args.assignment,
        "attempt_index": args.attempt,
        "step_id": step_id,
        "provider_profile_id": "codex-agent-cli-gpt-5-6-sol-low-win-cmd-300s",
        "provider_fingerprint": active.fingerprint,
        "canonical_write_authority": False,
        "persistence_authority": False,
        "fallback_used": False,
    }
    try:
        result = asyncio.run(
            run_or_resume_production_step(
                assignment.repository,
                run=assignment.run,
                raw_request=assignment.raw_request_ref,
                prompt=assignment.prompt,
                step_id=step_id,
                compiler=compiler,
            )
        )
    except ProductionRuntimeStepFailed as failure:
        attempt = failure.args[0] if failure.args else None
        after_refs = {
            ref.uri
            for ref in _run_records(assignment.repository, assignment.run)
        }
        envelope.update(
            {
                "status": "pipeline_rejected",
                "error_code": getattr(attempt, "error_code", "unknown"),
                "message": str(failure.__cause__ or failure)[:2000],
                "observed_wall_clock_ms": int((time.time() - started) * 1000),
                "provider_invocation_count": len(collector.since(0)),
                "result_ref": None,
                "record_refs": [],
                "created_record_refs": sorted(after_refs - before_refs),
            }
        )
        _put(assignment.repository, assignment.run, envelope_kind, envelope)
        print("ROOT FAILED (typed production failure retained)")
        return 2
    after_refs = {
        ref.uri for ref in _run_records(assignment.repository, assignment.run)
    }
    envelope.update(
        {
            "status": "compiled",
            "resumed": result.resumed,
            "observed_wall_clock_ms": int((time.time() - started) * 1000),
            "provider_invocation_count": len(collector.since(0)),
            "intent_digest": result.archive.intent_digest,
            "checkpoint_ref": result.archive.checkpoint_ref.uri,
            "record_refs": [item.ref.uri for item in result.archive.records],
            "created_record_refs": sorted(after_refs - before_refs),
        }
    )
    if result.resumed:
        raise SystemExit(
            "root step resumed an existing transition; this cannot be a "
            "fresh preregistered attempt"
        )
    _put(assignment.repository, assignment.run, envelope_kind, envelope)
    print("ROOT COMPILED")
    return 0


def _classify_archive(assignment: Assignment, record_refs: list[str]):
    roles: dict[str, list[dict]] = {}
    uris: dict[str, list[str]] = {}
    for uri in record_refs:
        payload = _resolve(assignment, uri)
        role = payload.get("role") or payload.get("schema") or "unknown"
        roles.setdefault(role, []).append(payload)
        uris.setdefault(role, []).append(uri)
    return roles, uris


def _node_components(design_program, proposal_dict):
    """Map program node id -> component id through zones and volumes."""

    volume_component: dict[str, str] = {}
    for component in proposal_dict.get("components", []):
        for volume_id in component.get("volume_ids", []):
            volume_component[volume_id] = component["component_id"]
    node_component: dict[str, str] = {}
    for zone in proposal_dict.get("zones", []):
        component_id = None
        for volume_id in zone.get("volume_ids", []):
            component_id = volume_component.get(volume_id, component_id)
        if component_id is None:
            continue
        for ref in zone.get("program_node_refs", []):
            node_component[ref.rsplit(":", 1)[-1]] = component_id
    return node_component


def _measurements(design_program, proposal_dict, program, scene, criteria):
    """Compute the four generic P060 measurement keys from exact records."""

    program_nodes = [n["node_id"] for n in design_program.get("nodes", [])]
    zone_node_refs = set()
    for zone in proposal_dict.get("zones", []):
        for ref in zone.get("program_node_refs", []):
            zone_node_refs.add(ref.rsplit(":", 1)[-1])
    covered = [n for n in program_nodes if n in zone_node_refs]
    coverage = (len(covered) / len(program_nodes)) if program_nodes else 0.0

    required_rel = [
        rel["relationship_id"]
        for rel in design_program.get("relationships", [])
    ]
    connection_refs = set()
    for connection in proposal_dict.get("connections", []):
        for ref in connection.get("relationship_refs", []):
            connection_refs.add(ref.rsplit(":", 1)[-1])
    satisfied_responses = {
        response.get("response_id", "")
        for response in proposal_dict.get("constraint_responses", [])
        if response.get("status") == "satisfied"
    }
    rel_covered = [
        rel
        for rel in required_rel
        if rel in connection_refs
        or any(rid.endswith(rel) for rid in satisfied_responses)
    ]
    rel_coverage = (
        (len(rel_covered) / len(required_rel)) if required_rel else 1.0
    )

    values = {
        "program_component_coverage_ratio": round(coverage, 6),
        "required_relationship_coverage_ratio": round(rel_coverage, 6),
        "usable_entry_count": len(scene.opening_object_ids),
    }
    separation_components: tuple[str, ...] = ()
    keys = {c["measurement_key"] for c in criteria}
    if "required_separation_satisfied" in keys:
        pair = _separation_pair(design_program, proposal_dict)
        separation_components = pair
        values["required_separation_satisfied"] = (
            not _xz_overlap(program, scene, pair[0], pair[1])
            if len(pair) == 2
            else None
        )
    if "storey_count" in keys:
        values["storey_count"] = len(proposal_dict.get("levels", []))
    if "double_height_hall_realized" in keys:
        values["double_height_hall_realized"] = _double_height_realized(
            design_program, proposal_dict
        )
    if "courtyard_open_to_sky" in keys:
        values["courtyard_open_to_sky"] = _courtyard_open(
            design_program, proposal_dict, program, scene
        )
    if "required_courtyard_access_ratio" in keys:
        values["required_courtyard_access_ratio"] = _courtyard_access_ratio(
            design_program, proposal_dict
        )
    return values, separation_components


def _label_node(design_program, needle: str) -> str | None:
    for node in design_program.get("nodes", []):
        if needle in str(node.get("label", "")).lower():
            return node["node_id"]
    return None


def _node_volumes(proposal_dict, node_id: str) -> list[dict]:
    volume_ids: set[str] = set()
    for zone in proposal_dict.get("zones", []):
        refs = [
            r.rsplit(":", 1)[-1] for r in zone.get("program_node_refs", [])
        ]
        if node_id in refs:
            volume_ids.update(zone.get("volume_ids", []))
    return [
        v
        for v in proposal_dict.get("volumes", [])
        if v["volume_id"] in volume_ids
    ]


def _double_height_realized(design_program, proposal_dict):
    """Hall volume height reaches twice the smallest declared level height."""

    node = _label_node(design_program, "hall")
    if node is None:
        return None
    levels = proposal_dict.get("levels", [])
    if not levels:
        return None
    minimum_height = min(level["height"] for level in levels)
    for volume in _node_volumes(proposal_dict, node):
        bounds = volume["bounds"]
        height = bounds["maximum"][1] - bounds["minimum"][1] + 1
        if height >= 2 * minimum_height or len(
            volume.get("level_ids", [])
        ) >= 2:
            return True
    return False


def _courtyard_open(design_program, proposal_dict, program, scene):
    """No other component's realized object covers the courtyard from above."""

    node = _label_node(design_program, "courtyard")
    if node is None:
        return None
    node_component = _node_components(design_program, proposal_dict)
    courtyard_component = node_component.get(node)
    volumes = _node_volumes(proposal_dict, node)
    if not volumes or courtyard_component is None:
        return None
    owner: dict[str, str] = {}
    for binding in program.proposal.semantic_bindings:
        for object_id in binding.object_ids:
            owner[object_id] = binding.component_id
    for volume in volumes:
        lo, hi = volume["bounds"]["minimum"], volume["bounds"]["maximum"]
        for obj in scene.objects:
            if owner.get(obj.object_id) == courtyard_component:
                continue
            box = obj.to_dict().get("bounds")
            if not box:
                continue
            blo, bhi = box["minimum"], box["maximum"]
            overlaps_xz = (
                blo[0] <= hi[0]
                and lo[0] <= bhi[0]
                and blo[2] <= hi[2]
                and lo[2] <= bhi[2]
            )
            if overlaps_xz and bhi[1] > hi[1]:
                return False
    return True


def _courtyard_access_ratio(design_program, proposal_dict):
    """Declared circulation links to the courtyard covered by connections."""

    node = _label_node(design_program, "courtyard")
    if node is None:
        return None
    required = [
        rel["relationship_id"]
        for rel in design_program.get("relationships", [])
        if rel.get("kind") == "circulation"
        and node
        in (
            str(rel.get("source_node_ref", "")).rsplit(":", 1)[-1],
            str(rel.get("target_node_ref", "")).rsplit(":", 1)[-1],
        )
    ]
    if not required:
        return 1.0
    connection_refs = {
        ref.rsplit(":", 1)[-1]
        for connection in proposal_dict.get("connections", [])
        for ref in connection.get("relationship_refs", [])
    }
    satisfied = {
        response.get("response_id", "")
        for response in proposal_dict.get("constraint_responses", [])
        if response.get("status") == "satisfied"
    }
    covered = [
        rel
        for rel in required
        if rel in connection_refs
        or any(rid.endswith(rel) for rid in satisfied)
    ]
    return round(len(covered) / len(required), 6)


def _separation_pair(design_program, proposal_dict):
    """Components at the endpoints of declared separation relationships."""

    node_component = _node_components(design_program, proposal_dict)
    for rel in design_program.get("relationships", []):
        if rel.get("kind") != "separation":
            continue
        source = str(rel.get("source_node_ref", "")).rsplit(":", 1)[-1]
        target = str(rel.get("target_node_ref", "")).rsplit(":", 1)[-1]
        pair = tuple(
            sorted(
                {
                    node_component.get(source),
                    node_component.get(target),
                }
                - {None}
            )
        )
        if len(pair) == 2:
            return pair
    return ()


def _xz_overlap(program, scene, component_a, component_b) -> bool:
    """Whether two components' realized scene objects overlap in plan."""

    owner: dict[str, str] = {}
    for obj in program.objects:
        payload = obj.to_dict()
        if payload.get("component_id"):
            owner[payload["object_id"]] = payload["component_id"]

    def boxes(component_id):
        out = []
        for obj in scene.objects:
            if owner.get(obj.object_id) != component_id:
                continue
            box = obj.to_dict().get("bounds")
            if box:
                out.append(box)
        return out

    def xz(box):
        lo, hi = box["minimum"], box["maximum"]
        return (lo[0], hi[0], lo[2], hi[2])

    for box_a in boxes(component_a):
        ax0, ax1, az0, az1 = xz(box_a)
        for box_b in boxes(component_b):
            bx0, bx1, bz0, bz1 = xz(box_b)
            if ax0 < bx1 and bx0 < ax1 and az0 < bz1 and bz0 < az1:
                return True
    return False


def stage_terminal(args, assignment: Assignment) -> int:
    envelope_kind = f"p062-{args.study_run}-execution-envelope"
    envelope = _load_envelope(assignment, envelope_kind, args.assignment)
    if envelope.get("status") != "compiled":
        raise SystemExit("root step is not compiled; terminal chain refused")
    roles, uris = _classify_archive(assignment, envelope["record_refs"])
    state = DevelopedDesignState.from_dict(
        _record_content(roles["design-state"][0], expected_role="design-state")
    )
    program = load_compiled_geometry_program(
        _record_content(
            roles["geometry-program"][0], expected_role="geometry-program"
        )
    )
    proposal_content = _record_content(
        roles["component-proposal"][0], expected_role="component-proposal"
    )
    proposal = SpatialOptionProposal.from_dict(proposal_content)
    proposal_dict = proposal.to_dict()
    prefix = f"s{args.study_run.rsplit('-', 1)[-1]}-{args.assignment}"
    workspace_id = f"{args.study_run}-{args.assignment}-p060"
    result = realize_geometry(
        program,
        workspace_id=workspace_id,
        policy=SandboxRealizationPolicy(),
    )
    scene_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-sandbox-scene",
        result.scene.to_dict(),
    )
    receipt_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-sandbox-realization",
        result.receipt.to_dict(),
    )

    criteria_specs = assignment.evaluation_brief["criteria"]
    measurements, separation_components = _measurements(
        assignment.program, proposal_dict, program, result.scene, criteria_specs
    )
    methods = {
        "program_component_coverage_ratio": (
            "declared DesignProgram node refs represented by current proposal "
            "zones divided by all declared node refs"
        ),
        "required_relationship_coverage_ratio": (
            "required DesignProgram relationship refs represented by current "
            "connections or satisfied constraint responses divided by all "
            "required refs"
        ),
        "required_separation_satisfied": (
            "non-overlapping realized horizontal X-Z bounds for the exact "
            "component locality in the authorized criterion"
        ),
        "usable_entry_count": (
            "count of exact realized HybridScene opening_object_ids"
        ),
    }
    inputs_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-measurement-inputs",
        {
            "schema": "M062ArchitecturalMeasurementInputs@1",
            "project_id": assignment.run.project_id,
            "run_id": assignment.run.run_id,
            "base": envelope["base"],
            "measurement_methods": methods,
            "measurements": {
                key: value
                for key, value in measurements.items()
                if value is not None
            },
            "source_refs": sorted(
                {
                    assignment.raw_request_ref.uri,
                    assignment.program_ref.uri,
                    assignment.evaluation_brief_ref.uri,
                    scene_ref.uri,
                    receipt_ref.uri,
                    *uris["design-state"],
                    *uris["geometry-program"],
                    *uris["component-proposal"],
                }
            ),
            "canonical_write_authority": False,
            "geometry_mutation_authority": False,
            "model_self_certification_authority": False,
        },
    )

    proposal_dict = proposal.to_dict()
    component_ids = tuple(
        sorted(c["component_id"] for c in proposal_dict["components"])
    )
    object_by_component: dict[str, list[str]] = {}
    for obj in program.objects:
        payload = obj.to_dict()
        owner = payload.get("component_id")
        if owner:
            object_by_component.setdefault(owner, []).append(
                payload["object_id"]
            )
    all_object_ids = tuple(
        sorted(obj.to_dict()["object_id"] for obj in program.objects)
    )
    root_component = next(
        c["component_id"]
        for c in proposal_dict["components"]
        if c.get("parent_component_id") is None
    )

    def _criterion(spec) -> ArchitecturalCriterion:
        key = spec["measurement_key"]
        operator = {
            "minimum": CriterionOperator.MINIMUM,
            "maximum": CriterionOperator.MAXIMUM,
            "exact": CriterionOperator.EQUAL,
            "equal": CriterionOperator.EQUAL,
        }[spec["operator"]]
        if key == "usable_entry_count":
            locality_components: tuple[str, ...] = (root_component,)
            locality_objects: tuple[str, ...] = ()
        elif key == "required_separation_satisfied":
            locality_components = separation_components
            locality_objects = tuple(
                sorted(
                    obj_id
                    for component in separation_components
                    for obj_id in object_by_component.get(component, [])
                )
            )
        else:
            locality_components = component_ids
            locality_objects = all_object_ids
        return ArchitecturalCriterion(
            criterion_id=spec["criterion_id"],
            measurement_key=key,
            operator=operator,
            expected_json=canonical_value(spec["threshold"]),
            unit="count" if key == "usable_entry_count" else None,
            mandatory=bool(spec["mandatory"]),
            source_refs=(assignment.evaluation_brief_ref.uri,),
            component_ids=locality_components,
            geometry_object_ids=locality_objects,
        )

    criteria = tuple(
        sorted(
            (_criterion(spec) for spec in criteria_specs),
            key=lambda item: item.criterion_id,
        )
    )
    contract = compile_architectural_usability_contract(
        design_state=state,
        geometry_program=program,
        scene=result.scene,
        realization_receipt=result.receipt,
        artifact_ref=scene_ref.uri,
        authorized_record_refs=tuple(
            sorted(
                {
                    assignment.evaluation_brief_ref.uri,
                    assignment.raw_request_ref.uri,
                }
            )
        ),
        sources=(
            AuthorizedCriterionSource(
                source_ref=assignment.evaluation_brief_ref.uri,
                kind=CriterionSourceKind.PROGRAM,
                authority_ref=assignment.raw_request_ref.uri,
            ),
        ),
        criteria=criteria,
    )
    contract_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-usability-contract",
        contract.to_dict(),
    )
    evidence_refs = tuple(
        sorted(
            {
                inputs_ref.uri,
                scene_ref.uri,
                receipt_ref.uri,
                contract_ref.uri,
            }
        )
    )
    observations = []
    for spec in criteria_specs:
        key = spec["measurement_key"]
        value = measurements[key]
        if value is None:
            continue
        criterion = next(c for c in criteria if c.measurement_key == key)
        observations.append(
            ArchitecturalObservation(
                observation_id=f"measure-{spec['criterion_id']}",
                contract_digest=contract.contract_digest,
                measurement_key=key,
                value_json=canonical_value(value),
                unit=criterion.unit,
                component_ids=criterion.component_ids,
                geometry_object_ids=criterion.geometry_object_ids,
                obligation_refs=(),
                evidence_refs=evidence_refs,
            )
        )
    receipt = evaluate_architectural_usability(contract, tuple(observations))
    observations_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-usability-observations",
        {
            "schema": "P062ArchitecturalObservationSet@1",
            "project_id": assignment.run.project_id,
            "run_id": assignment.run.run_id,
            "base": envelope["base"],
            "contract_digest": contract.contract_digest,
            "observations": [item.to_dict() for item in observations],
            "canonical_write_authority": False,
        },
    )
    usability_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-usability-receipt",
        receipt.to_dict(),
    )

    try:
        family_refs = _family_binding(
            args,
            assignment,
            prefix,
            envelope,
            state,
            program,
            proposal_dict,
            result,
        )
    except SystemExit as failure:
        family_refs = {}
        _put(
            assignment.repository,
            assignment.run,
            f"{prefix}-family-binding-failure",
            {
                "schema": "P062FamilyBindingFailure@1",
                "project_id": assignment.run.project_id,
                "run_id": assignment.run.run_id,
                "base": envelope["base"],
                "assignment_id": args.assignment,
                "error": str(failure),
                "detail": (
                    "the accepted candidate exposes no non-root component "
                    "owning bound geometry operations, so the case family "
                    "brief cannot be satisfied; no family is invented"
                ),
                "canonical_write_authority": False,
                "geometry_mutation_authority": False,
            },
        )

    envelope_update = dict(envelope)
    envelope_update.update(
        {
            "sequence": envelope.get("sequence", 0) + 1,
            "terminal_refs": {
                "sandbox-scene": scene_ref.uri,
                "sandbox-realization": receipt_ref.uri,
                "measurement-inputs": inputs_ref.uri,
                "usability-contract": contract_ref.uri,
                "usability-observations": observations_ref.uri,
                "architectural-usability": usability_ref.uri,
                **family_refs,
            },
            "usability_status": receipt.to_dict()["findings"]
            and receipt.status.value,
        }
    )
    _put(
        assignment.repository,
        assignment.run,
        f"p062-{args.study_run}-execution-envelope",
        envelope_update,
    )
    print(f"TERMINAL CHAIN DONE (usability: {receipt.status.value})")
    return 0


def _family_binding(
    args,
    assignment,
    prefix,
    envelope,
    state,
    program,
    proposal_dict,
    result,
):
    """Bind one repeated component to a parametric family per the case brief."""

    proposal_obj = program.proposal
    root_component = next(
        c["component_id"]
        for c in proposal_dict["components"]
        if c.get("parent_component_id") is None
    )
    component_bindings: dict[str, list] = {}
    for binding in proposal_obj.semantic_bindings:
        component_bindings.setdefault(binding.component_id, []).append(binding)
    object_owner = {
        object_id: binding.component_id
        for binding in proposal_obj.semantic_bindings
        for object_id in binding.object_ids
    }
    candidates: list[tuple[int, str, tuple]] = []
    for component_id, bindings in sorted(component_bindings.items()):
        if component_id == root_component:
            continue
        binding_ids = {b.binding_id for b in bindings}
        operations = [
            op
            for op in proposal_obj.operations
            if set(op.semantic_binding_ids) <= binding_ids
            and set(op.semantic_binding_ids)
            and all(
                object_owner.get(o) == component_id
                for o in op.output_object_ids
            )
            and op.asset_id is None
            and op.parameters
        ]
        if operations:
            candidates.append((len(operations), component_id, tuple(operations)))
    if not candidates:
        raise SystemExit("no component with bound operations for family")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    _, component_id, operations = candidates[0]
    operation_ids = tuple(sorted(op.op_id for op in operations))
    operations_by_id = {op.op_id: op for op in operations}
    family_id = f"{assignment.run.project_id}-{component_id}-family"
    definition = {
        "schema": "P062ProjectFamilyDefinition@1",
        "project_id": assignment.run.project_id,
        "run_id": assignment.run.run_id,
        "family_id": family_id,
        "family_revision": 1,
        "component_id": component_id,
        "semantic_kind": next(
            c.get("semantic_kind", "component")
            for c in proposal_dict["components"]
            if c["component_id"] == component_id
        ),
        "aggregate_component": True,
        "enumerated_repeated_instances_claimed": False,
        "editable_parameters": sorted(
            {
                parameter.name
                for op in operations
                for parameter in op.parameters
            }
        ),
        "selection_rule_ref": assignment.family_brief_ref.uri,
        "repeatability_basis_refs": [
            assignment.raw_request_ref.uri,
            assignment.program_ref.uri,
        ],
        "component_tree_authority": False,
        "geometry_generation_authority": False,
        "canonical_write_authority": False,
    }
    definition_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-family-definition",
        definition,
    )
    owned_objects = sorted(
        object_id
        for object_id, owner_id in object_owner.items()
        if owner_id == component_id
    )
    binding_ids = sorted(
        binding.binding_id for binding in component_bindings[component_id]
    )
    selection = {
        "schema": "P062ComponentFamilySelection@1",
        "project_id": assignment.run.project_id,
        "run_id": assignment.run.run_id,
        "selected_component_id": component_id,
        "selected_operation_ids": list(operation_ids),
        "selected_object_ids": owned_objects,
        "selected_semantic_binding_ids": binding_ids,
        "aggregate_component": True,
        "enumerated_repeated_instances_claimed": False,
        "human_geometry_edit": False,
        "geometry_mutation_authority": False,
        "canonical_write_authority": False,
        "definition_ref": definition_ref.uri,
        "family_brief_ref": assignment.family_brief_ref.uri,
        "selection_basis": (
            "deterministic harness rule: the non-root component owning the "
            "largest count of bound geometry operations in the accepted "
            "program is bound as one aggregate family instance"
        ),
    }
    selection_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-family-selection",
        selection,
    )
    parameter_refs = tuple(
        FamilyParameterRef(
            parameter_id=f"{op_id}-{parameter.name}",
            operation_id=op_id,
            parameter_name=parameter.name,
            parameter_digest=canonical_digest(parameter.to_dict()),
            source_refs=(selection_ref.uri,),
        )
        for op_id in operation_ids
        for parameter in operations_by_id[op_id].parameters
    )
    interface_ref = f"interface:{family_id}-boundary"
    socket_object = sorted(
        object_id
        for op_id in operation_ids
        for object_id in operations_by_id[op_id].output_object_ids
    )[0]
    socket = FamilySocket(
        socket_id=f"{family_id}-boundary",
        object_id=socket_object,
        frame_id=operations_by_id[operation_ids[0]].frame_id,
        interface_refs=(interface_ref,),
    )
    instance = ComponentFamilyInstance(
        family_instance_id=f"{family_id}-instance",
        component_id=component_id,
        family_id=family_id,
        family_revision=1,
        kind=ComponentFamilyKind.PARAMETRIC_ASSEMBLY,
        definition_digest=definition_ref.sha256,
        predecessor_instance_digest=None,
        frame_id=operations_by_id[operation_ids[0]].frame_id,
        native_unit=proposal_obj.length_unit,
        scale=(1.0, 1.0, 1.0),
        parameter_refs=parameter_refs,
        sockets=(socket,),
        anchors=(),
        interface_refs=(interface_ref,),
        dependency_component_ids=(),
        semantic_binding_ids=tuple(binding_ids),
        operation_ids=operation_ids,
        assembly_ids=(),
        asset_ids=(),
        provenance_refs=(selection_ref.uri,),
    )
    family_set = ComponentFamilySet(
        project_id=assignment.run.project_id,
        run_id=assignment.run.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        component_tree_digest=(
            state.selected_schematic.option.proposal.proposal_digest
        ),
        geometry_program_digest=program.program_digest,
        available_interface_refs=(interface_ref,),
        instances=(instance,),
    )
    compilation = compile_component_families(state, program, family_set)
    inputs_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-family-inputs",
        {
            "schema": "P062ComponentFamilyInputs@1",
            "design_state_ref": envelope["record_refs"][0],
            "family_brief_ref": assignment.family_brief_ref.uri,
            "family_definition_ref": definition_ref.uri,
            "family_selection_ref": selection_ref.uri,
            "family_set": family_set.to_dict(),
            "component_tree_authority": False,
            "canonical_write_authority": False,
        },
    )
    compilation_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-family-compilation",
        compilation.to_dict(),
    )
    realized = compilation.to_dict()["status"] == "compiled" and all(
        object_id
        in {obj.to_dict()["object_id"] for obj in result.scene.objects}
        for object_id in owned_objects
    )
    realization = ComponentFamilyRealizationReceipt(
        project_id=assignment.run.project_id,
        run_id=assignment.run.run_id,
        base=state.base,
        family_compilation_receipt_digest=compilation.receipt_digest,
        family_set_digest=family_set.family_set_digest,
        geometry_program_digest=program.program_digest,
        scene_digest=result.receipt.scene_digest if realized else None,
        sandbox_realization_receipt_digest=result.receipt.receipt_digest,
        family_instance_ids=(instance.family_instance_id,),
        geometry_object_ids=tuple(owned_objects),
        status=(
            FamilyRealizationStatus.REALIZED
            if realized
            else FamilyRealizationStatus.REJECTED
        ),
        issues=(),
    )
    realization_ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-family-realization-receipt",
        realization.to_dict(),
    )
    return {
        "family-definition": definition_ref.uri,
        "family-selection": selection_ref.uri,
        "family-inputs": inputs_ref.uri,
        "component-family-compilation": compilation_ref.uri,
        "component-family-realization": realization_ref.uri,
    }


def _receipt_json(payload: dict) -> dict:
    return json.loads(payload["content"]["provider_receipt_json"])


def stage_metrics(args, assignment: Assignment) -> int:
    """Compute and persist the thirteen preregistered metric values."""

    envelope_kind = f"p062-{args.study_run}-execution-envelope"
    envelope = _load_envelope(assignment, envelope_kind, args.assignment)
    terminal = envelope.get("terminal_refs") or {}
    roles, uris = _classify_archive(
        assignment, envelope.get("record_refs") or []
    )
    invocations = roles.get("provider-invocation", [])
    receipts = [_receipt_json(item) for item in invocations]
    model_calls = len(receipts)
    provider_failures = sum(
        1 for item in receipts if item.get("error_code") is not None
    )
    created = envelope.get("created_record_refs") or []
    rejected_authoring = 0
    repair_rounds = 0
    for uri in created:
        name = uri.rsplit("/", 1)[-1]
        if name.startswith("semantic-spatial-authoring-") and "attempt-0" in name:
            payload = _resolve(assignment, uri)
            if payload.get("status") == "rejected":
                rejected_authoring += 1
        if name.startswith("geometry-proposal-round-"):
            round_index = int(name.split("round-")[1][:2])
            repair_rounds = max(repair_rounds, round_index - 1)
    repair_count = rejected_authoring + repair_rounds

    usability = None
    if terminal.get("architectural-usability"):
        usability = _resolve(assignment, terminal["architectural-usability"])
    scene = None
    if terminal.get("sandbox-scene"):
        scene = _resolve(assignment, terminal["sandbox-scene"])
    family_compilation = None
    if terminal.get("component-family-compilation"):
        family_compilation = _resolve(
            assignment, terminal["component-family-compilation"]
        )
    family_realization = None
    if terminal.get("component-family-realization"):
        family_realization = _resolve(
            assignment, terminal["component-family-realization"]
        )
    sandbox_receipt = None
    if terminal.get("sandbox-realization"):
        sandbox_receipt = _resolve(assignment, terminal["sandbox-realization"])

    values: dict[str, object] = {}
    notes: dict[str, str] = {}
    usable = bool(usability) and not usability.get("findings") is None and all(
        f["status"] == "pass" for f in usability["findings"] if f["mandatory"]
    ) and usability.get("artifact_presence_claimed") is not None
    usability_passed = (
        usability is not None
        and all(
            f["status"] == "pass"
            for f in usability["findings"]
            if f["mandatory"]
        )
    )
    values["architectural-usable"] = usability_passed
    notes["architectural-usable"] = (
        "all mandatory P060 findings pass in the retained receipt"
    )
    sandbox_ok = bool(sandbox_receipt) and sandbox_receipt.get(
        "status"
    ) == "realized"
    family_ok = bool(family_compilation) and family_compilation.get(
        "status"
    ) == "compiled" and bool(
        family_compilation.get("compiled_instances")
    )
    family_realized = bool(family_realization) and family_realization.get(
        "status"
    ) == "realized"
    production_ok = envelope.get("status") == "compiled"
    values["completion"] = bool(
        production_ok
        and sandbox_ok
        and usability_passed
        and family_ok
        and family_realized
    )
    notes["completion"] = (
        "exact terminal authority set: persisted production transition, "
        "realized sandbox, passed P060, compiled non-empty family, realized "
        "family"
    )
    if usability:
        mandatory = [f for f in usability["findings"] if f["mandatory"]]
        values["project-constraint-pass-rate"] = round(
            sum(1 for f in mandatory if f["status"] == "pass")
            / len(mandatory),
            6,
        )
        notes["project-constraint-pass-rate"] = (
            f"passed mandatory P060 findings over {len(mandatory)} mandatory "
            "findings"
        )
    values["model-call-count"] = model_calls
    values["provider-failure-count"] = provider_failures
    values["repair-count"] = repair_count
    notes["repair-count"] = (
        "rejected semantic-spatial authoring receipts plus geometry rounds "
        "beyond the first inside this attempt's created records"
    )
    values["human-intervention-count"] = 0
    notes["human-intervention-count"] = (
        "no retained human edit or choice record follows the preregistered "
        "intent"
    )
    if repair_count == 0:
        values["local-repair-containment"] = None
        notes["local-repair-containment"] = "not applicable: no repair required"
    else:
        values["local-repair-containment"] = 0.0
        notes["local-repair-containment"] = (
            "retained repairs requested complete replacement outputs, so no "
            "repair was subtree-local"
        )

    continuity = None
    consistency = None
    family_coverage = None
    if envelope.get("record_refs") and scene:
        proposal_content = _record_content(
            roles["component-proposal"][0], expected_role="component-proposal"
        )
        program_content = _record_content(
            roles["geometry-program"][0], expected_role="geometry-program"
        )
        bindings = program_content["proposal"]["semantic_bindings"]
        component_objects: dict[str, set[str]] = {}
        for binding in bindings:
            component_objects.setdefault(
                binding["component_id"], set()
            ).update(binding["object_ids"])
        scene_objects = {obj["object_id"] for obj in scene["objects"]}
        program_objects = {
            obj["object_id"] for obj in program_content["objects"]
        }
        components = [
            c["component_id"] for c in proposal_content["components"]
        ]
        bound = [c for c in components if component_objects.get(c)]
        realized = [
            c
            for c in bound
            if component_objects[c] <= scene_objects
        ]
        consistent = [
            c
            for c in bound
            if component_objects[c] <= program_objects
        ]
        continuity = round(len(realized) / len(bound), 6) if bound else 0.0
        consistency = (
            round(len(consistent) / len(components), 6) if components else 0.0
        )
        if family_compilation:
            family_components = {
                item["component_id"]
                for item in family_compilation.get("compiled_instances", [])
            }
            family_coverage = round(
                len(family_components) / len(components), 6
            ) if components else 0.0
    values["component-identity-continuity"] = continuity
    notes["component-identity-continuity"] = (
        "components with semantic geometry bindings whose objects all reload "
        "in the exact sandbox scene, over bound components"
    )
    values["semantic-geometry-consistency"] = consistency
    notes["semantic-geometry-consistency"] = (
        "components whose bound objects all exist in the compiled geometry "
        "program, over all ownership-tree components"
    )
    values["family-binding-coverage"] = family_coverage
    notes["family-binding-coverage"] = (
        "distinct components bound by compiled family instances over all "
        "ownership-tree components; one aggregate binding, no enumerated "
        "room instances claimed"
    )

    assignment.repository.verify()
    values["reload-equivalent"] = True
    notes["reload-equivalent"] = (
        "P036 case repository verify passed and exact terminal records "
        "reloaded before metric persistence"
    )
    values["wall-clock-ms"] = None
    notes["wall-clock-ms"] = (
        "measured at receipt binding from the retained attempt intent"
    )

    prefix = f"s{args.study_run.rsplit('-', 1)[-1]}-{args.assignment}"
    payload = {
        "schema": "P062AttemptMetricEvidence@1",
        "project_id": assignment.run.project_id,
        "run_id": assignment.run.run_id,
        "base": envelope["base"],
        "assignment_id": args.assignment,
        "attempt_id": (
            f"attempt-{args.study_run}-"
            f"{args.assignment.removeprefix('assignment-')}-"
            f"{args.attempt:03d}"
        ),
        "attempt_intent_digest": envelope.get("intent_digest"),
        "measurements": {
            key: value for key, value in values.items() if value is not None
        },
        "measurement_notes": notes,
        "source_refs": sorted(
            {
                *(uris.get("provider-invocation") or []),
                *(terminal.values()),
            }
        ),
        "status": "measured",
        "paper_result_authority": False,
        "canonical_write_authority": False,
    }
    ref = _put(
        assignment.repository,
        assignment.run,
        f"{prefix}-metric-evidence",
        payload,
    )
    print("METRICS DONE")
    print(json.dumps(values, indent=1, default=str))
    return 0


def stage_bind(args, assignment: Assignment) -> int:
    """Assemble receipt and outcome payloads and persist them via the CLI."""

    import datetime
    import subprocess

    sys.path.insert(0, str(ROOT / "tools"))
    import run_experiment as rex

    from archive.archflow.evaluation.experiment import (
        ExperimentAttemptIntent,
        ExperimentAttemptReceipt,
        ExperimentAttemptStatus,
        ExperimentMetricObservation,
        ExperimentOutcome,
        ExperimentPreregistration,
        MetricObservationStatus,
    )

    study_repo = FilesystemProjectRepository.open(Path(args.study_root))
    study_run = study_repo.load_run(args.study_run)
    study_dest = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=args.study_run
    )
    prereg = intent = None
    for ref in study_repo.list_json(run=study_run, destination=study_dest):
        payload = study_repo.load_json(ref)
        if payload.get("schema") == "ExperimentPreregistration@1":
            prereg = ExperimentPreregistration.from_dict(payload)
        if payload.get("schema") == "ExperimentAttemptIntent@1" and (
            payload.get("assignment_id") == args.assignment
            and payload.get("attempt_index") == args.attempt
        ):
            intent = ExperimentAttemptIntent.from_dict(payload)
    if prereg is None or intent is None:
        raise SystemExit("study run lacks the preregistration or the intent")
    assignment_row = prereg.assignment(args.assignment)
    condition = next(
        c
        for c in prereg.conditions
        if c.condition_id == assignment_row.condition_id
    )

    envelope_kind = f"p062-{args.study_run}-execution-envelope"
    envelope = _load_envelope(assignment, envelope_kind, args.assignment)
    terminal = envelope.get("terminal_refs") or {}
    all_refs = {
        ref.uri: ref
        for ref in _run_records(assignment.repository, assignment.run)
    }

    provider_bindings = []
    for uri in envelope.get("record_refs") or []:
        payload = assignment.repository.load_json(all_refs[uri])
        if payload.get("role") == "provider-invocation":
            provider_bindings.append(
                rex.bind_provider_record(
                    assignment.repository,
                    run=assignment.run,
                    ref=all_refs[uri],
                )
            )
    provider_bindings.sort(key=lambda item: item.receipt_id)

    usability_payload = None
    if terminal.get("architectural-usability"):
        usability_payload = assignment.repository.load_json(
            all_refs[terminal["architectural-usability"]]
        )
    usability_passed = bool(usability_payload) and all(
        f["status"] == "pass"
        for f in usability_payload["findings"]
        if f["mandatory"]
    )
    lifecycle_uri = next(
        (
            uri
            for uri in envelope.get("record_refs") or []
            if assignment.repository.load_json(all_refs[uri]).get("role")
            == "lifecycle-receipt"
        ),
        None,
    )
    role_refs = {
        "semantic-geometry-production": lifecycle_uri,
        "sandbox-realization": terminal.get("sandbox-realization"),
        "architectural-usability": terminal.get("architectural-usability"),
        "component-family-compilation": terminal.get(
            "component-family-compilation"
        ),
        "component-family-realization": terminal.get(
            "component-family-realization"
        ),
    }
    requirements = {
        item.role: item for item in condition.terminal_requirements
    }
    if usability_passed and all(role_refs.values()):
        terminal_evidence = rex.bind_terminal_chain(
            assignment.repository,
            run=assignment.run,
            condition=condition,
            record_refs={
                role: all_refs[uri] for role, uri in role_refs.items()
            },
        )
        status = ExperimentAttemptStatus.COMPLETED
        error_code = None
        message = None
    else:
        bindings = []
        for role, uri in role_refs.items():
            if uri is None or role == "architectural-usability":
                continue
            try:
                bindings.append(
                    rex.bind_terminal_record(
                        assignment.repository,
                        run=assignment.run,
                        ref=all_refs[uri],
                        requirement=requirements[role],
                    )
                )
            except Exception:
                continue
        terminal_evidence = tuple(
            sorted(bindings, key=lambda item: item.record_ref)
        )
        status = ExperimentAttemptStatus.PIPELINE_REJECTED
        error_code = (
            envelope.get("error_code")
            or "architectural.usability_failed"
        )
        message = envelope.get("message") or (
            "deterministic P060 evaluation rejected the realized candidate; "
            "provider and terminal records are retained without upgrade"
        )

    issued = datetime.datetime.fromisoformat(intent.issued_at)
    duration_ms = int(
        (
            datetime.datetime.now(tz=issued.tzinfo) - issued
        ).total_seconds()
        * 1000
    )
    receipt = ExperimentAttemptReceipt(
        study_id=prereg.study_id,
        preregistration_digest=prereg.preregistration_digest,
        assignment_id=args.assignment,
        assignment_digest=assignment_row.assignment_digest,
        case_id=assignment_row.case_id,
        condition_id=assignment_row.condition_id,
        project_id=assignment.run.project_id,
        run_id=assignment.run.run_id,
        base=assignment.run.base,
        attempt_id=intent.attempt_id,
        attempt_index=args.attempt,
        attempt_intent_digest=intent.intent_digest,
        status=status,
        duration_ms=duration_ms,
        provider_receipts=tuple(provider_bindings),
        terminal_evidence=terminal_evidence,
        source_attempt_receipt_digest=None,
        retry_of_attempt_receipt_digest=None,
        error_code=error_code,
        message=message,
    )
    scratch = Path(args.scratch_dir)
    receipt_path = scratch / f"receipt-{args.study_run}-{args.assignment}.json"
    receipt_path.write_text(
        json.dumps(receipt.to_dict(), indent=1), encoding="utf-8"
    )
    cli = [
        sys.executable,
        str(ROOT / "archive" / "tools" / "run_experiment.py"),
        "receipt",
        "--study-root",
        str(args.study_root),
        "--run-id",
        args.study_run,
        "--payload",
        str(receipt_path),
        "--source-project",
        f"{assignment.run.project_id}={args.case_root}",
    ]
    print("persisting attempt receipt...")
    print(subprocess.run(cli, capture_output=True, text=True).stdout.strip())

    metric_kind = (
        f"s{args.study_run.rsplit('-', 1)[-1]}-{args.assignment}"
        "-metric-evidence"
    )
    metric_refs = assignment.by_kind.get(metric_kind) or [
        ref
        for uri, ref in all_refs.items()
        if metric_kind in uri
    ]
    if not metric_refs:
        raise SystemExit("metric evidence record is absent; run metrics first")
    metric_ref = metric_refs[-1]
    metric_payload = assignment.repository.load_json(metric_ref)
    measured = metric_payload["measurements"]
    notes = metric_payload.get("measurement_notes") or {}

    observations = []
    for spec in prereg.metric_specs:
        metric_id = spec.metric_id
        evidence = (
            rex.bind_evidence_record(
                assignment.repository,
                run=assignment.run,
                ref=metric_ref,
                role=f"metric-{metric_id}",
                expected_schema="P062AttemptMetricEvidence@1",
                expected_status="measured",
            ),
        )
        if metric_id == "wall-clock-ms":
            observations.append(
                ExperimentMetricObservation(
                    metric_id=metric_id,
                    status=MetricObservationStatus.MEASURED,
                    value=duration_ms,
                    evidence=evidence,
                    reason=None,
                )
            )
        elif metric_id in measured:
            observations.append(
                ExperimentMetricObservation(
                    metric_id=metric_id,
                    status=MetricObservationStatus.MEASURED,
                    value=measured[metric_id],
                    evidence=evidence,
                    reason=None,
                )
            )
        elif notes.get(metric_id, "").startswith("not applicable"):
            observations.append(
                ExperimentMetricObservation(
                    metric_id=metric_id,
                    status=MetricObservationStatus.NOT_APPLICABLE,
                    value=None,
                    evidence=evidence,
                    reason=notes.get(metric_id),
                )
            )
        else:
            observations.append(
                ExperimentMetricObservation(
                    metric_id=metric_id,
                    status=MetricObservationStatus.UNKNOWN,
                    value=None,
                    evidence=evidence,
                    reason=notes.get(metric_id)
                    or "no retained measurement for this attempt",
                )
            )
    required_ids = {
        spec.metric_id
        for spec in prereg.metric_specs
        if spec.required_for_comparison
    }
    measured_ids = {
        item.metric_id
        for item in observations
        if item.status is MetricObservationStatus.MEASURED
    }
    outcome = ExperimentOutcome(
        study_id=prereg.study_id,
        preregistration_digest=prereg.preregistration_digest,
        assignment_id=args.assignment,
        assignment_digest=assignment_row.assignment_digest,
        attempt_receipt_digest=receipt.receipt_digest,
        attempt_status=status,
        observations=tuple(observations),
        eligible_for_comparison=(
            status is ExperimentAttemptStatus.COMPLETED
            and required_ids <= measured_ids
        ),
    )
    outcome_path = scratch / f"outcome-{args.study_run}-{args.assignment}.json"
    outcome_path.write_text(
        json.dumps(outcome.to_dict(), indent=1), encoding="utf-8"
    )
    cli = [
        sys.executable,
        str(ROOT / "archive" / "tools" / "run_experiment.py"),
        "outcome",
        "--study-root",
        str(args.study_root),
        "--run-id",
        args.study_run,
        "--payload",
        str(outcome_path),
        "--source-project",
        f"{assignment.run.project_id}={args.case_root}",
    ]
    print("persisting outcome...")
    print(subprocess.run(cli, capture_output=True, text=True).stdout.strip())

    stamp = datetime.datetime.now(tz=issued.tzinfo).isoformat(
        timespec="seconds"
    )
    cli = [
        sys.executable,
        str(ROOT / "archive" / "tools" / "run_experiment.py"),
        "index",
        "--study-root",
        str(args.study_root),
        "--run-id",
        args.study_run,
        "--generated-at",
        stamp,
    ]
    print("rebuilding result index...")
    print(subprocess.run(cli, capture_output=True, text=True).stdout.strip())
    print(f"BIND DONE (status: {status.value}, duration_ms: {duration_ms})")
    return 0


def stage_validation(args, assignment: Assignment) -> int:
    """Bind one validation-ablation assignment from its exact source attempt.

    No provider is invoked. The named evaluator is withheld: its receipt is
    neither rebound nor reinterpreted, and artifact presence cannot become
    architectural usability.
    """

    import datetime
    import subprocess

    sys.path.insert(0, str(ROOT / "tools"))
    import run_experiment as rex

    from archive.archflow.evaluation.experiment import (
        ExperimentAttemptIntent,
        ExperimentAttemptReceipt,
        ExperimentAttemptStatus,
        ExperimentMetricObservation,
        ExperimentOutcome,
        ExperimentPreregistration,
        MetricObservationStatus,
    )

    study_repo = FilesystemProjectRepository.open(Path(args.study_root))
    study_run = study_repo.load_run(args.study_run)
    study_dest = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=args.study_run
    )
    prereg = intent = None
    source_receipt = None
    source_assignment = args.assignment.replace("-validation", "-full")
    for ref in study_repo.list_json(run=study_run, destination=study_dest):
        payload = study_repo.load_json(ref)
        schema = payload.get("schema")
        if schema == "ExperimentPreregistration@1":
            prereg = ExperimentPreregistration.from_dict(payload)
        elif schema == "ExperimentAttemptIntent@1" and (
            payload.get("assignment_id") == args.assignment
            and payload.get("attempt_index") == args.attempt
        ):
            intent = ExperimentAttemptIntent.from_dict(payload)
        elif schema == "ExperimentAttemptReceipt@1" and (
            payload.get("assignment_id") == source_assignment
        ):
            source_receipt = ExperimentAttemptReceipt.from_dict(payload)
    if prereg is None or intent is None:
        raise SystemExit("study run lacks the preregistration or the intent")
    if source_receipt is None:
        raise SystemExit(
            f"validation ablation requires the retained {source_assignment} "
            "attempt receipt"
        )
    assignment_row = prereg.assignment(args.assignment)
    condition = next(
        c
        for c in prereg.conditions
        if c.condition_id == assignment_row.condition_id
    )
    if condition.withheld_evaluator_ids != ("architectural-usability",):
        raise SystemExit("unexpected withheld evaluator set")

    envelope_kind = f"p062-{args.study_run}-execution-envelope"
    envelope = _load_envelope(assignment, envelope_kind, source_assignment)
    terminal = envelope.get("terminal_refs") or {}
    all_refs = {
        ref.uri: ref
        for ref in _run_records(assignment.repository, assignment.run)
    }
    provider_bindings = []
    for uri in envelope.get("record_refs") or []:
        payload = assignment.repository.load_json(all_refs[uri])
        if payload.get("role") == "provider-invocation":
            provider_bindings.append(
                rex.bind_provider_record(
                    assignment.repository,
                    run=assignment.run,
                    ref=all_refs[uri],
                )
            )
    provider_bindings.sort(key=lambda item: item.receipt_id)
    lifecycle_uri = next(
        uri
        for uri in envelope.get("record_refs") or []
        if assignment.repository.load_json(all_refs[uri]).get("role")
        == "lifecycle-receipt"
    )
    role_refs = {
        "semantic-geometry-production": lifecycle_uri,
        "sandbox-realization": terminal.get("sandbox-realization"),
        "component-family-compilation": terminal.get(
            "component-family-compilation"
        ),
        "component-family-realization": terminal.get(
            "component-family-realization"
        ),
    }
    if not all(role_refs.values()):
        raise SystemExit("source attempt lacks the reduced terminal chain")
    terminal_evidence = rex.bind_terminal_chain(
        assignment.repository,
        run=assignment.run,
        condition=condition,
        record_refs={role: all_refs[uri] for role, uri in role_refs.items()},
    )
    issued = datetime.datetime.fromisoformat(intent.issued_at)
    duration_ms = int(
        (
            datetime.datetime.now(tz=issued.tzinfo) - issued
        ).total_seconds()
        * 1000
    )
    receipt = ExperimentAttemptReceipt(
        study_id=prereg.study_id,
        preregistration_digest=prereg.preregistration_digest,
        assignment_id=args.assignment,
        assignment_digest=assignment_row.assignment_digest,
        case_id=assignment_row.case_id,
        condition_id=assignment_row.condition_id,
        project_id=assignment.run.project_id,
        run_id=assignment.run.run_id,
        base=assignment.run.base,
        attempt_id=intent.attempt_id,
        attempt_index=args.attempt,
        attempt_intent_digest=intent.intent_digest,
        status=ExperimentAttemptStatus.COMPLETED,
        duration_ms=duration_ms,
        provider_receipts=tuple(provider_bindings),
        terminal_evidence=terminal_evidence,
        source_attempt_receipt_digest=source_receipt.receipt_digest,
        retry_of_attempt_receipt_digest=None,
        error_code=None,
        message=None,
    )
    existing = None
    for ref in study_repo.list_json(run=study_run, destination=study_dest):
        payload = study_repo.load_json(ref)
        if payload.get("schema") == "ExperimentAttemptReceipt@1" and (
            payload.get("attempt_id") == intent.attempt_id
        ):
            existing = ExperimentAttemptReceipt.from_dict(payload)
    if existing is not None:
        print("attempt receipt already retained; reusing it")
        receipt = existing
    else:
        scratch = Path(args.scratch_dir)
        receipt_path = (
            scratch / f"receipt-{args.study_run}-{args.assignment}.json"
        )
        receipt_path.write_text(
            json.dumps(receipt.to_dict(), indent=1), encoding="utf-8"
        )
        print("persisting validation-ablation receipt...")
        out = subprocess.run(
            [
                sys.executable,
                str(ROOT / "archive" / "tools" / "run_experiment.py"),
                "receipt",
                "--study-root",
                str(args.study_root),
                "--run-id",
                args.study_run,
                "--payload",
                str(receipt_path),
                "--source-project",
                f"{assignment.run.project_id}={args.case_root}",
            ],
            capture_output=True,
            text=True,
        )
        print(out.stdout.strip() or out.stderr.strip()[-500:])
    scratch = Path(args.scratch_dir)
    duration_ms = receipt.duration_ms

    metric_kind = (
        f"s{args.study_run.rsplit('-', 1)[-1]}-{source_assignment}"
        "-metric-evidence"
    )
    metric_refs = [
        ref for uri, ref in all_refs.items() if metric_kind in uri
    ]
    if not metric_refs:
        raise SystemExit("source metric evidence is absent")
    metric_ref = sorted(metric_refs, key=lambda item: item.uri)[-1]
    metric_payload = assignment.repository.load_json(metric_ref)
    measured = metric_payload["measurements"]

    withheld = {"architectural-usable", "project-constraint-pass-rate"}
    observations = []
    for spec in prereg.metric_specs:
        metric_id = spec.metric_id
        evidence = (
            rex.bind_evidence_record(
                assignment.repository,
                run=assignment.run,
                ref=metric_ref,
                role=f"metric-{metric_id}",
                expected_schema="P062AttemptMetricEvidence@1",
                expected_status="measured",
            ),
        )
        if metric_id in withheld:
            observations.append(
                ExperimentMetricObservation(
                    metric_id=metric_id,
                    status=MetricObservationStatus.UNKNOWN,
                    value=None,
                    evidence=evidence,
                    reason=(
                        "the architectural-usability evaluator is withheld "
                        "by this preregistered validation ablation"
                    ),
                )
            )
        elif metric_id == "completion":
            observations.append(
                ExperimentMetricObservation(
                    metric_id=metric_id,
                    status=MetricObservationStatus.MEASURED,
                    value=True,
                    evidence=evidence,
                    reason=None,
                )
            )
        elif metric_id == "wall-clock-ms":
            observations.append(
                ExperimentMetricObservation(
                    metric_id=metric_id,
                    status=MetricObservationStatus.MEASURED,
                    value=duration_ms,
                    evidence=evidence,
                    reason=None,
                )
            )
        elif metric_id in measured:
            observations.append(
                ExperimentMetricObservation(
                    metric_id=metric_id,
                    status=MetricObservationStatus.MEASURED,
                    value=measured[metric_id],
                    evidence=evidence,
                    reason=None,
                )
            )
        else:
            note = (metric_payload.get("measurement_notes") or {}).get(
                metric_id, ""
            )
            observations.append(
                ExperimentMetricObservation(
                    metric_id=metric_id,
                    status=(
                        MetricObservationStatus.NOT_APPLICABLE
                        if note.startswith("not applicable")
                        else MetricObservationStatus.UNKNOWN
                    ),
                    value=None,
                    evidence=evidence,
                    reason=note or "no retained measurement",
                )
            )
    required_ids = {
        spec.metric_id
        for spec in prereg.metric_specs
        if spec.required_for_comparison
    }
    measured_ids = {
        item.metric_id
        for item in observations
        if item.status is not MetricObservationStatus.UNKNOWN
    }
    outcome = ExperimentOutcome(
        study_id=prereg.study_id,
        preregistration_digest=prereg.preregistration_digest,
        assignment_id=args.assignment,
        assignment_digest=assignment_row.assignment_digest,
        attempt_receipt_digest=receipt.receipt_digest,
        attempt_status=ExperimentAttemptStatus.COMPLETED,
        observations=tuple(observations),
        eligible_for_comparison=required_ids <= measured_ids,
    )
    outcome_path = scratch / f"outcome-{args.study_run}-{args.assignment}.json"
    outcome_path.write_text(
        json.dumps(outcome.to_dict(), indent=1), encoding="utf-8"
    )
    print("persisting validation-ablation outcome...")
    out = subprocess.run(
        [
            sys.executable,
            str(ROOT / "archive" / "tools" / "run_experiment.py"),
            "outcome",
            "--study-root",
            str(args.study_root),
            "--run-id",
            args.study_run,
            "--payload",
            str(outcome_path),
            "--source-project",
            f"{assignment.run.project_id}={args.case_root}",
        ],
        capture_output=True,
        text=True,
    )
    print(out.stdout.strip() or out.stderr.strip()[-500:])
    print(f"VALIDATION BIND DONE (duration_ms: {duration_ms})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--study-root", type=Path,
                        default=Path("probes/p062-experiment-study"))
    parser.add_argument(
        "--scratch-dir",
        type=Path,
        default=Path(
            r"C:/Users/asus/AppData/Local/Temp/claude/D--ARCHFLOW-V4"
            r"/f6dcb08c-c6c1-464f-9220-b61d7ba877e5/scratchpad"
        ),
    )
    parser.add_argument("--study-run", required=True)
    parser.add_argument("--assignment", required=True)
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument(
        "--condition",
        choices=("full", "generation-context"),
        default="full",
    )
    parser.add_argument(
        "--stage",
        choices=(
            "dry-run",
            "root",
            "terminal",
            "metrics",
            "bind",
            "validation",
        ),
        required=True,
    )
    parser.add_argument("--codex", default="codex.cmd")
    args = parser.parse_args(argv)
    assignment = Assignment(args.case_root, condition=args.condition)
    print(
        f"case={assignment.run.project_id} run={assignment.run.run_id} "
        f"base=v{assignment.base.version} prompt={assignment.prompt[:60]!r}"
    )
    if args.stage == "dry-run":
        print("context reloads:", assignment.context.schema
              if hasattr(assignment.context, "schema") else "ok")
        print("required commitments:",
              assignment.context.required_commitment_refs)
        print("evaluation criteria:",
              [c["criterion_id"] for c in assignment.evaluation_brief["criteria"]])
        print("DRY RUN OK — no provider call, no writes")
        return 0
    if args.stage == "root":
        return stage_root(args, assignment)
    if args.stage == "metrics":
        return stage_metrics(args, assignment)
    if args.stage == "bind":
        return stage_bind(args, assignment)
    if args.stage == "validation":
        return stage_validation(args, assignment)
    return stage_terminal(args, assignment)


if __name__ == "__main__":
    raise SystemExit(main())
