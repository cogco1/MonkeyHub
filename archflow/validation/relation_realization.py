"""Fail-closed validation of semantic relation realization bindings.

The checker joins an exact architectural relation graph, compiled geometry
program, CAD readback snapshot, and caller-authored endpoint pairing/path
manifest.  It never derives a relationship from geometry-operation inputs and
never expands endpoint sets into a Cartesian product.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import replace

from archflow.relations.contracts import (
    ArchitecturalRelationGraph,
    ArchitecturalRelationKind,
)
from archflow.relations.realization import (
    RELATION_REALIZATION_CHECKER_ID,
    RelationEndpointObjectBinding,
    RelationEndpointPairing,
    RelationObjectPath,
    RelationRealizationManifest,
    RelationRealizationPurpose,
    RelationVerificationBinding,
    relation_endpoint_slot_ref,
    relation_realization_denominator,
)
from archflow.runtime.geometry_compiler import CompiledGeometryProgram
from archflow.validation.cad_readback import CadReadbackSnapshot
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


_RELATION_PURPOSES = {
    ArchitecturalRelationKind.INTERFACE: (
        RelationRealizationPurpose.CONTACT_INTERFACE
    ),
    ArchitecturalRelationKind.INTERSECTS: (
        RelationRealizationPurpose.CONTACT_INTERFACE
    ),
    ArchitecturalRelationKind.HOST: RelationRealizationPurpose.HOST_INTERFACE,
    ArchitecturalRelationKind.HOSTS_VOID: (
        RelationRealizationPurpose.HOST_INTERFACE
    ),
    ArchitecturalRelationKind.FILLS_VOID: (
        RelationRealizationPurpose.HOST_INTERFACE
    ),
    ArchitecturalRelationKind.SUPPORT: (
        RelationRealizationPurpose.SUPPORT_CHAIN
    ),
    ArchitecturalRelationKind.LOAD_TRANSFER: (
        RelationRealizationPurpose.LOAD_PATH
    ),
    ArchitecturalRelationKind.ACCESS: (
        RelationRealizationPurpose.WALKING_PATH
    ),
    ArchitecturalRelationKind.ALLOWS_PASSAGE: (
        RelationRealizationPurpose.WALKING_PATH
    ),
}

_DIRECT_PAIRING_RELATION_KINDS = frozenset(
    {
        ArchitecturalRelationKind.INTERFACE,
        ArchitecturalRelationKind.INTERSECTS,
        ArchitecturalRelationKind.HOST,
        ArchitecturalRelationKind.HOSTS_VOID,
        ArchitecturalRelationKind.FILLS_VOID,
    }
)


def _required_relation_purpose(
    relation_kind: ArchitecturalRelationKind,
) -> RelationRealizationPurpose:
    if not isinstance(relation_kind, ArchitecturalRelationKind):
        raise TypeError("relation_kind must be ArchitecturalRelationKind")
    return _RELATION_PURPOSES.get(
        relation_kind,
        RelationRealizationPurpose.GENERIC_PAIR,
    )


def _verification_subject_refs(
    owner: RelationEndpointPairing | RelationObjectPath,
) -> tuple[str, ...]:
    """Bind a receipt to the exact topology without a digest fixed point.

    Pair/path refs include their verification binding.  The no-verification
    owner ref therefore commits the topology that the independent receipt is
    attesting without making that receipt digest recursively define itself.
    """

    topology_owner = replace(owner, verification=None)
    if isinstance(owner, RelationEndpointPairing):
        refs = (
            owner.relation_ref,
            topology_owner.ref,
            owner.first_binding_ref,
            owner.second_binding_ref,
        )
    elif isinstance(owner, RelationObjectPath):
        refs = (
            owner.relation_ref,
            topology_owner.ref,
            *owner.endpoint_binding_refs,
            *owner.pairing_refs,
        )
    else:  # pragma: no cover - protected by the private typed call sites
        raise TypeError("verification owner must be a pairing or path")
    return tuple(sorted(set(refs)))


def _measurement(
    manifest: RelationRealizationManifest,
    measurement_id: str,
    value: int | str,
) -> CheckMeasurement:
    return CheckMeasurement(
        measurement_id=measurement_id,
        subject_ref=manifest.ref,
        name=measurement_id,
        value=value,
        unit_ref="unit:count" if isinstance(value, int) else None,
    )


def check_relation_realization(
    graph: ArchitecturalRelationGraph,
    manifest: RelationRealizationManifest,
    program: CompiledGeometryProgram,
    readback: CadReadbackSnapshot,
    *,
    verification_receipts: tuple[CheckReceiptEnvelope, ...] = (),
) -> CheckReceiptEnvelope:
    """Validate the exact semantic-endpoint to geometry/readback closure.

    Malformed typed values raise at their own contract boundary.  Cross-input
    identity drift, missing endpoint bindings, ambiguous object ownership, and
    incomplete pairing/path manifests become ``FAIL`` findings so they cannot
    satisfy composite stage closure.
    """

    if not isinstance(graph, ArchitecturalRelationGraph):
        raise TypeError("graph must be ArchitecturalRelationGraph")
    if not isinstance(manifest, RelationRealizationManifest):
        raise TypeError("manifest must be RelationRealizationManifest")
    if not isinstance(program, CompiledGeometryProgram):
        raise TypeError("program must be CompiledGeometryProgram")
    if not isinstance(readback, CadReadbackSnapshot):
        raise TypeError("readback must be CadReadbackSnapshot")
    if not isinstance(verification_receipts, tuple) or any(
        not isinstance(item, CheckReceiptEnvelope)
        for item in verification_receipts
    ):
        raise TypeError(
            "verification_receipts must be a CheckReceiptEnvelope tuple"
        )

    denominator = relation_realization_denominator(graph, manifest)
    denominator_set = set(denominator)
    findings: list[CheckFinding] = []

    def add(
        code: str,
        message: str,
        *subject_refs: str,
        severity: FindingSeverity = FindingSeverity.ERROR,
    ) -> None:
        refs = tuple(sorted(set(subject_refs)))
        if not refs or not set(refs) <= denominator_set:
            raise ValueError("relation realization finding escaped its denominator")
        findings.append(
            CheckFinding(
                code=code,
                severity=severity,
                message=message,
                subject_refs=refs,
            )
        )

    verification_by_digest: dict[str, CheckReceiptEnvelope] = {}
    verification_digest_counts = Counter(
        item.receipt_digest for item in verification_receipts
    )
    for receipt in verification_receipts:
        verification_by_digest.setdefault(receipt.receipt_digest, receipt)
    if any(count > 1 for count in verification_digest_counts.values()):
        add(
            "relation-verification-receipt-duplicate",
            "independent verification receipts repeat an exact digest",
            manifest.ref,
        )

    def verify_independent_receipt(
        verification: RelationVerificationBinding | None,
        *,
        owner_ref: str,
        relation_ref: str,
        relation_kind: ArchitecturalRelationKind,
        expected_subject_refs: tuple[str, ...],
        expected_purpose: RelationRealizationPurpose | None = None,
    ) -> bool:
        if verification is None:
            add(
                "relation-narrow-phase-verification-missing",
                "object topology is bound, but no exact independent geometry/readback check is bound",
                owner_ref,
                relation_ref,
                severity=FindingSeverity.UNKNOWN,
            )
            return False
        if expected_purpose is not None and verification.purpose is not expected_purpose:
            add(
                "relation-verification-purpose-mismatch",
                "verification purpose differs from the declared relation path purpose",
                owner_ref,
                verification.ref,
            )
            return False
        if verification.purpose is not _required_relation_purpose(relation_kind):
            add(
                "relation-verification-purpose-incompatible",
                "contact, host, walking, load, support, and generic verification purposes are not interchangeable",
                owner_ref,
                relation_ref,
                verification.ref,
            )
            return False
        if verification.subject_refs != expected_subject_refs:
            add(
                "relation-verification-subject-unbound",
                "independent verification does not exactly bind its semantic relation, owner topology, and binding/pairing denominator",
                owner_ref,
                relation_ref,
                verification.ref,
            )
            return False
        receipt = verification_by_digest.get(verification.receipt_digest)
        if receipt is None:
            add(
                "relation-verification-receipt-missing",
                "bound independent verification receipt was not supplied",
                owner_ref,
                verification.ref,
                verification.receipt_ref,
                severity=FindingSeverity.UNKNOWN,
            )
            return False
        exact_identity = (
            receipt.checker_id == verification.checker_id
            and receipt.branch == manifest.branch
            and receipt.scope_digest == manifest.scope_digest
            and receipt.subject_digest == manifest.stage_subject_digest
            and receipt.subject_refs == verification.subject_refs
            and receipt.coverage_denominator == verification.subject_refs
        )
        if not exact_identity:
            add(
                "relation-verification-receipt-binding-mismatch",
                "independent verification crossed checker, branch, scope, subject, or denominator",
                owner_ref,
                verification.ref,
                verification.receipt_ref,
            )
            return False
        if receipt.status is CheckStatus.FAIL:
            add(
                "relation-independent-check-failed",
                "independent geometry/readback verification failed",
                owner_ref,
                relation_ref,
                verification.ref,
            )
            return False
        if receipt.status is not CheckStatus.PASS:
            add(
                "relation-independent-check-open",
                "independent geometry/readback verification is not PASS",
                owner_ref,
                relation_ref,
                verification.ref,
                severity=FindingSeverity.UNKNOWN,
            )
            return False
        if receipt.covered_refs != verification.subject_refs:
            add(
                "relation-verification-coverage-incomplete",
                "independent verification did not cover its exact relation denominator",
                owner_ref,
                verification.ref,
            )
            return False
        return True

    # Exact global identity joins.  The manifest is not allowed to associate an
    # otherwise valid graph/program/readback from another branch or stage.
    if manifest.branch != graph.branch:
        add(
            "relation-realization-branch-mismatch",
            "relation graph and realization manifest crossed branches",
            graph.ref,
            manifest.ref,
        )
    if readback.branch != manifest.branch:
        add(
            "relation-readback-branch-mismatch",
            "CAD readback and relation realization crossed branches",
            manifest.ref,
            manifest.readback_ref,
        )
    if manifest.scope_digest != graph.scope_digest:
        add(
            "relation-realization-scope-mismatch",
            "relation graph and realization manifest crossed scopes",
            graph.ref,
            manifest.ref,
        )
    if manifest.stage_id != graph.stage_id:
        add(
            "relation-realization-stage-mismatch",
            "relation graph and realization manifest crossed stages",
            graph.ref,
            manifest.ref,
        )
    if readback.stage_id != manifest.stage_id:
        add(
            "relation-readback-stage-mismatch",
            "CAD readback does not name the exact realization stage",
            manifest.ref,
            manifest.readback_ref,
        )
    if manifest.relation_graph_digest != graph.graph_digest:
        add(
            "relation-graph-digest-mismatch",
            "realization manifest is not bound to the exact relation graph",
            graph.ref,
            manifest.graph_ref,
        )
    if manifest.stage_subject_digest != graph.stage_subject_digest:
        add(
            "relation-stage-subject-digest-mismatch",
            "relation realization crossed the exact stage subject set",
            graph.ref,
            manifest.stage_subject_ref,
        )
    if manifest.program_digest != program.program_digest:
        add(
            "relation-program-digest-mismatch",
            "relation realization is not bound to the supplied compiled program",
            manifest.ref,
            manifest.program_ref,
        )
    if readback.program_digest != manifest.program_digest:
        add(
            "relation-readback-program-mismatch",
            "CAD readback does not name the exact compiled program",
            manifest.program_ref,
            manifest.readback_ref,
        )
    if manifest.readback_digest != readback.snapshot_digest:
        add(
            "relation-readback-digest-mismatch",
            "relation realization is not bound to the supplied readback bytes",
            manifest.ref,
            manifest.readback_ref,
        )

    proposal = program.proposal
    if (
        proposal.project_id != manifest.branch.run.project_id
        or proposal.run_id != manifest.branch.run.run_id
        or proposal.base != manifest.branch.run.base
    ):
        add(
            "relation-program-branch-identity-mismatch",
            "compiled program project/run/base differs from the exact branch",
            manifest.ref,
            manifest.program_ref,
        )
    if proposal.design_state_digest != graph.state_digest:
        add(
            "relation-program-state-mismatch",
            "compiled program and semantic relation graph name different states",
            graph.ref,
            manifest.program_ref,
        )
    if readback.project_id != manifest.branch.run.project_id:
        add(
            "relation-readback-project-mismatch",
            "CAD readback does not name the exact branch project",
            manifest.ref,
            manifest.readback_ref,
        )

    relations = {item.ref: item for item in graph.relations}
    expected_slots: dict[str, tuple[str, object]] = {}
    for relation in graph.relations:
        for participant in relation.participants:
            slot_ref = relation_endpoint_slot_ref(relation.ref, participant)
            expected_slots[slot_ref] = (relation.ref, participant)

    bindings_by_ref = {item.ref: item for item in manifest.endpoint_bindings}
    bindings_by_slot: dict[str, list[RelationEndpointObjectBinding]] = defaultdict(
        list
    )
    valid_binding_refs: set[str] = set()
    program_objects = {item.object_id: item for item in program.objects}
    operations = {item.op_id: item for item in proposal.operations}
    semantic_bindings = {
        item.binding_id: item for item in proposal.semantic_bindings
    }
    readback_by_object: dict[str, list[object]] = defaultdict(list)
    for observed in readback.objects:
        readback_by_object[observed.object_ref].append(observed)
    readback_operation_counts = Counter(readback.operation_refs)

    for binding in manifest.endpoint_bindings:
        local_valid = True
        expected = expected_slots.get(binding.participant_slot_ref)
        if expected is None or expected[0] != binding.relation_ref:
            local_valid = False
            add(
                "relation-endpoint-binding-unknown",
                "endpoint binding does not match an exact graph participant",
                binding.ref,
                binding.participant_slot_ref,
                binding.relation_ref,
            )
        else:
            bindings_by_slot[binding.participant_slot_ref].append(binding)

        compiled_object = program_objects.get(binding.program_object_id)
        if compiled_object is None:
            local_valid = False
            add(
                "relation-program-object-missing",
                "endpoint binding names an object absent from the compiled program",
                binding.ref,
                manifest.program_ref,
            )
        elif (
            compiled_object.object_digest != binding.program_object_digest
            or compiled_object.producer_op_id != binding.producer_operation_id
        ):
            local_valid = False
            add(
                "relation-program-object-binding-mismatch",
                "compiled object digest or producer differs from the endpoint binding",
                binding.ref,
                manifest.program_ref,
            )

        semantic = semantic_bindings.get(binding.semantic_binding_id)
        if (
            semantic is None
            or binding.program_object_id not in semantic.object_ids
        ):
            local_valid = False
            add(
                "relation-semantic-geometry-binding-mismatch",
                "program semantic binding does not own the endpoint object",
                binding.ref,
                manifest.program_ref,
            )

        operation = operations.get(binding.producer_operation_id)
        if (
            operation is None
            or binding.program_object_id not in operation.output_object_ids
            or binding.semantic_binding_id not in operation.semantic_binding_ids
        ):
            local_valid = False
            add(
                "relation-producing-operation-mismatch",
                "endpoint object is not an output of its bound semantic operation",
                binding.ref,
                manifest.program_ref,
            )
        # Deliberately do not inspect GeometryOperation.input_object_ids here.
        # Those IDs encode consumption only and cannot evidence a relationship.

        observed_candidates = readback_by_object.get(
            binding.readback_object_ref,
            [],
        )
        if len(observed_candidates) != 1:
            local_valid = False
            add(
                "relation-readback-object-cardinality",
                "endpoint readback object must resolve exactly once",
                binding.ref,
                binding.readback_object_ref,
                manifest.readback_ref,
            )
        else:
            observed = observed_candidates[0]
            if observed.operation_ref != binding.readback_operation_ref:
                local_valid = False
                add(
                    "relation-readback-operation-mismatch",
                    "endpoint readback object names a different operation",
                    binding.ref,
                    binding.readback_object_ref,
                    binding.readback_operation_ref,
                )
        if readback_operation_counts[binding.readback_operation_ref] == 0:
            local_valid = False
            add(
                "relation-readback-operation-missing",
                "endpoint operation is absent from the readback operation registry",
                binding.ref,
                binding.readback_operation_ref,
                manifest.readback_ref,
            )
        if local_valid:
            valid_binding_refs.add(binding.ref)

    for slot_ref, (relation_ref, _participant) in expected_slots.items():
        if not bindings_by_slot.get(slot_ref):
            add(
                "relation-endpoint-binding-missing",
                "semantic relation endpoint has no exact geometry/readback binding",
                relation_ref,
                slot_ref,
            )

    # One endpoint may use several objects, but one object cannot ambiguously
    # realize two endpoints of the same semantic relation.
    associations: dict[
        tuple[str, str, str], list[RelationEndpointObjectBinding]
    ] = defaultdict(list)
    program_endpoint_owners: dict[
        tuple[str, str], set[str]
    ] = defaultdict(set)
    readback_endpoint_owners: dict[
        tuple[str, str], set[str]
    ] = defaultdict(set)
    for binding in manifest.endpoint_bindings:
        associations[
            (
                binding.relation_ref,
                binding.participant_slot_ref,
                binding.program_object_id,
            )
        ].append(binding)
        program_endpoint_owners[
            (binding.relation_ref, binding.program_object_id)
        ].add(binding.participant_slot_ref)
        readback_endpoint_owners[
            (binding.relation_ref, binding.readback_object_ref)
        ].add(binding.participant_slot_ref)
    for rows in associations.values():
        if len(rows) > 1:
            add(
                "relation-endpoint-object-binding-duplicate",
                "one endpoint object has more than one binding record",
                *(item.ref for item in rows),
            )
    for owners in program_endpoint_owners.values():
        if len(owners) > 1:
            add(
                "relation-program-object-endpoint-ambiguous",
                "one program object is assigned to multiple relation endpoints",
                *owners,
            )
    for owners in readback_endpoint_owners.values():
        if len(owners) > 1:
            add(
                "relation-readback-object-endpoint-ambiguous",
                "one readback object is assigned to multiple relation endpoints",
                *owners,
            )

    pairings_by_ref = {item.ref: item for item in manifest.pairings}
    valid_pairing_refs: set[str] = set()
    valid_pairs_by_relation: dict[
        str, list[RelationEndpointPairing]
    ] = defaultdict(list)
    pair_keys: dict[tuple[str, frozenset[str]], list[RelationEndpointPairing]] = (
        defaultdict(list)
    )
    for pairing in manifest.pairings:
        first = bindings_by_ref.get(pairing.first_binding_ref)
        second = bindings_by_ref.get(pairing.second_binding_ref)
        pair_keys[
            (
                pairing.relation_ref,
                frozenset((pairing.first_binding_ref, pairing.second_binding_ref)),
            )
        ].append(pairing)
        if pairing.relation_ref not in relations:
            add(
                "relation-pairing-relation-unknown",
                "endpoint pairing names a relation outside the exact graph",
                pairing.ref,
                pairing.relation_ref,
            )
            continue
        if first is None or second is None:
            add(
                "relation-pairing-binding-missing",
                "endpoint pairing names an absent object binding",
                pairing.ref,
                pairing.first_binding_ref,
                pairing.second_binding_ref,
            )
            continue
        if (
            first.relation_ref != pairing.relation_ref
            or second.relation_ref != pairing.relation_ref
        ):
            add(
                "relation-pairing-crossed-relation",
                "endpoint pairing combines bindings from different relations",
                pairing.ref,
                first.ref,
                second.ref,
            )
            continue
        if first.participant_slot_ref == second.participant_slot_ref:
            add(
                "relation-pairing-same-endpoint",
                "endpoint pairing does not connect distinct semantic endpoints",
                pairing.ref,
                first.participant_slot_ref,
            )
            continue
        if (
            first.ref not in valid_binding_refs
            or second.ref not in valid_binding_refs
        ):
            add(
                "relation-pairing-unverified-binding",
                "endpoint pairing depends on an invalid geometry/readback binding",
                pairing.ref,
                first.ref,
                second.ref,
            )
            continue
        valid_pairing_refs.add(pairing.ref)
        valid_pairs_by_relation[pairing.relation_ref].append(pairing)

    for rows in pair_keys.values():
        if len(rows) > 1:
            add(
                "relation-pairing-ambiguous",
                "the same endpoint-object pair is declared more than once",
                *(item.ref for item in rows),
            )

    for relation in graph.relations:
        relation_bindings = tuple(
            item
            for item in manifest.endpoint_bindings
            if item.relation_ref == relation.ref
            and item.participant_slot_ref in expected_slots
        )
        relation_pairs = valid_pairs_by_relation.get(relation.ref, [])
        if not relation_pairs:
            add(
                "relation-pairing-manifest-missing",
                "relation has no explicit endpoint-object pairing; automatic Cartesian expansion is forbidden",
                relation.ref,
            )
            continue
        paired_binding_refs = {
            ref
            for pairing in relation_pairs
            for ref in (pairing.first_binding_ref, pairing.second_binding_ref)
        }
        missing_binding_pairs = tuple(
            item.ref
            for item in relation_bindings
            if item.ref not in paired_binding_refs
        )
        if missing_binding_pairs:
            add(
                "relation-endpoint-object-unpaired",
                "one or more endpoint objects lack an explicit pairing",
                relation.ref,
                *missing_binding_pairs,
            )

        expected_relation_slots = {
            relation_endpoint_slot_ref(relation.ref, participant)
            for participant in relation.participants
        }
        adjacency: dict[str, set[str]] = defaultdict(set)
        for pairing in relation_pairs:
            first = bindings_by_ref[pairing.first_binding_ref]
            second = bindings_by_ref[pairing.second_binding_ref]
            adjacency[first.participant_slot_ref].add(second.participant_slot_ref)
            adjacency[second.participant_slot_ref].add(first.participant_slot_ref)
        visited: set[str] = set()
        if expected_relation_slots:
            queue = deque((next(iter(expected_relation_slots)),))
            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                queue.extend(adjacency.get(current, set()) - visited)
        if visited != expected_relation_slots:
            add(
                "relation-pairing-disconnected",
                "explicit endpoint pairings do not connect every relation endpoint",
                relation.ref,
                *(expected_relation_slots - visited),
            )

    valid_path_refs: set[str] = set()
    paths_by_relation: dict[str, list[RelationObjectPath]] = defaultdict(list)
    path_keys: dict[tuple[str, tuple[str, ...]], list[RelationObjectPath]] = (
        defaultdict(list)
    )
    for path in manifest.paths:
        path_keys[(path.relation_ref, path.endpoint_binding_refs)].append(path)
        paths_by_relation[path.relation_ref].append(path)
        if path.relation_ref not in relations:
            add(
                "relation-path-relation-unknown",
                "object path names a relation outside the exact graph",
                path.ref,
                path.relation_ref,
            )
            continue
        local_valid = True
        for ordinal, pairing_ref in enumerate(path.pairing_refs):
            pairing = pairings_by_ref.get(pairing_ref)
            expected_first = path.endpoint_binding_refs[ordinal]
            expected_second = path.endpoint_binding_refs[ordinal + 1]
            if (
                pairing is None
                or pairing.ref not in valid_pairing_refs
                or pairing.relation_ref != path.relation_ref
                or pairing.first_binding_ref != expected_first
                or pairing.second_binding_ref != expected_second
            ):
                local_valid = False
                add(
                    "relation-path-hop-mismatch",
                    "object path hop is not its exact ordered endpoint pairing",
                    path.ref,
                    pairing_ref,
                    expected_first,
                    expected_second,
                )
        if local_valid:
            valid_path_refs.add(path.ref)

    for rows in path_keys.values():
        if len(rows) > 1:
            add(
                "relation-path-ambiguous",
                "the same ordered endpoint-object path is declared more than once",
                *(item.ref for item in rows),
            )

    for relation in graph.relations:
        via_slots = {
            relation_endpoint_slot_ref(relation.ref, participant)
            for participant in relation.participants
            if participant.role == "via"
        }
        if not via_slots:
            continue
        paths = paths_by_relation.get(relation.ref, [])
        if not paths:
            add(
                "relation-path-manifest-missing",
                "relation with explicit via endpoints requires an ordered object path",
                relation.ref,
                *via_slots,
            )
            continue
        path_slots = {
            bindings_by_ref[binding_ref].participant_slot_ref
            for path in paths
            if path.ref in valid_path_refs
            for binding_ref in path.endpoint_binding_refs
            if binding_ref in bindings_by_ref
        }
        missing_via_slots = via_slots - path_slots
        if missing_via_slots:
            add(
                "relation-path-via-binding-missing",
                "ordered object paths omit one or more semantic via endpoints",
                relation.ref,
                *missing_via_slots,
            )

    verified_path_pairing_refs: set[str] = set()
    pairing_path_purposes: dict[
        str, set[RelationRealizationPurpose]
    ] = defaultdict(set)
    for path in manifest.paths:
        if path.ref not in valid_path_refs:
            continue
        relation = relations[path.relation_ref]
        for pairing_ref in path.pairing_refs:
            pairing_path_purposes[pairing_ref].add(path.purpose)
        if path.purpose is not _required_relation_purpose(relation.kind):
            add(
                "relation-path-purpose-incompatible",
                "contact, host, walking, load, and support paths are separate relation obligations",
                path.ref,
                relation.ref,
            )
            continue
        if verify_independent_receipt(
            path.verification,
            owner_ref=path.ref,
            relation_ref=relation.ref,
            relation_kind=relation.kind,
            expected_subject_refs=_verification_subject_refs(path),
            expected_purpose=path.purpose,
        ):
            # A direct relation without semantic via endpoints is realized by
            # independently verified object pairs, never by path closure.
            has_via_endpoint = any(
                participant.role == "via"
                for participant in relation.participants
            )
            if (
                relation.kind not in _DIRECT_PAIRING_RELATION_KINDS
                or has_via_endpoint
            ):
                verified_path_pairing_refs.update(path.pairing_refs)

    for pairing_ref, purposes in pairing_path_purposes.items():
        if len(purposes) > 1:
            add(
                "relation-pairing-path-purpose-ambiguous",
                "one endpoint pairing is reused by incompatible path purposes",
                pairing_ref,
            )

    for pairing in manifest.pairings:
        if (
            pairing.ref not in valid_pairing_refs
            or pairing.ref in verified_path_pairing_refs
        ):
            continue
        relation = relations[pairing.relation_ref]
        verify_independent_receipt(
            pairing.verification,
            owner_ref=pairing.ref,
            relation_ref=relation.ref,
            relation_kind=relation.kind,
            expected_subject_refs=_verification_subject_refs(pairing),
        )

    # De-duplicate any convergent diagnostics before constructing the strict
    # receipt envelope.
    finding_map = {
        (item.code, item.subject_refs, item.message): item for item in findings
    }
    ordered_findings = tuple(
        sorted(
            finding_map.values(),
            key=lambda item: (item.code, item.subject_refs, item.message),
        )
    )
    has_error = any(
        item.severity is FindingSeverity.ERROR for item in ordered_findings
    )
    has_unknown = any(
        item.severity is FindingSeverity.UNKNOWN for item in ordered_findings
    )
    status = (
        CheckStatus.FAIL
        if has_error
        else CheckStatus.UNKNOWN
        if has_unknown
        else CheckStatus.PASS
    )
    measurements = tuple(
        sorted(
            (
                _measurement(
                    manifest,
                    "endpoint-binding-count",
                    len(manifest.endpoint_bindings),
                ),
                _measurement(
                    manifest,
                    "pairing-count",
                    len(manifest.pairings),
                ),
                _measurement(
                    manifest,
                    "path-count",
                    len(manifest.paths),
                ),
                _measurement(
                    manifest,
                    "program-digest",
                    program.program_digest,
                ),
                _measurement(
                    manifest,
                    "readback-digest",
                    readback.snapshot_digest,
                ),
                _measurement(
                    manifest,
                    "relation-graph-digest",
                    graph.graph_digest,
                ),
                _measurement(
                    manifest,
                    "relation-realization-manifest-digest",
                    manifest.manifest_digest,
                ),
                _measurement(
                    manifest,
                    "verification-receipt-count",
                    len(verification_receipts),
                ),
            ),
            key=lambda item: item.measurement_id,
        )
    )
    return CheckReceiptEnvelope(
        check_id=manifest.check_id,
        checker_id=RELATION_REALIZATION_CHECKER_ID,
        checker_version="1.0.0",
        branch=manifest.branch,
        scope_digest=manifest.scope_digest,
        subject_refs=denominator,
        subject_digest=manifest.stage_subject_digest,
        status=status,
        findings=ordered_findings,
        measurements=measurements,
        coverage_denominator=denominator,
        covered_refs=denominator if status is CheckStatus.PASS else (),
    )


validate_relation_realization = check_relation_realization


__all__ = [
    "RELATION_REALIZATION_CHECKER_ID",
    "check_relation_realization",
    "validate_relation_realization",
]
