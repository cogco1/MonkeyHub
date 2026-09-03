"""Component template records — reusable typology as provenance-bound data.

P091: a converged project component becomes a citable input for future
projects. The anatomy generalizes the run-014 underpass recovery triple:
a *mathematics reference* (which architecture-side solver realizes it),
an *applicability domain*, and *interface obligations* expressed in the
P090 datum vocabulary. Parameters are module-bound ratio bands or
derivation expressions, each carrying its own evidence references — a
template never ships a bare number. The record reads as a treatise
page: plates, numbers, usage notes, sources, edition lineage.

Reuse is re-derivation, never copying: geometry is rebaked per project
by the referenced solver, and evidence re-binds in the receiving
project. Promotion to the shared library demands the two-vote rule —
a ratio earns library status only after surviving two non-isomorphic
cases — or an explicitly recorded waiver.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.authority import no_authority
from archflow.contracts.canonical import canonical_json, require_sha256
from archflow.project.refs import require_identifier
from archflow.state.design_maturity import DesignPhase
from archflow.state.geometry_program import InterfaceDatum
from archflow.state.operational_state import DependencyEdge, DependencyEffect

LIBRARY_PROMOTION_MIN_VOTES = 2

_MAX_ITEMS = 256
_MAX_TEXT = 2_000


class ComponentTemplateError(ValueError):
    """A component template payload violates its contract."""


class ParameterForm(StrEnum):
    MODULE_RATIO = "module_ratio"
    COUNT = "count"
    EXPRESSION = "expression"


class ObligationKind(StrEnum):
    MEETS = "meets"
    SUPPORTS = "supports"
    HOSTS_VOID = "hosts_void"
    FILLS_VOID = "fills_void"
    INTERSECTS_FORBIDDEN = "intersects_forbidden"
    CLEARANCE = "clearance"
    ENGAGEMENT = "engagement"


_INTERVAL_KINDS = frozenset(
    {ObligationKind.CLEARANCE, ObligationKind.ENGAGEMENT}
)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ComponentTemplateError(f"{field} must be non-empty text")
    if len(value) > _MAX_TEXT:
        raise ComponentTemplateError(f"{field} exceeds the text bound")
    return value


def _ref(value: object, field: str) -> str:
    text = _text(value, field)
    if ":" not in text or any(ch.isspace() for ch in text):
        raise ComponentTemplateError(
            f"{field} must be a typed reference like evidence:... or "
            f"project://..., got {text!r}"
        )
    return text


def _refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ComponentTemplateError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise ComponentTemplateError(f"{field} has an invalid item count")
    out = tuple(_ref(item, field) for item in values)
    if out != tuple(sorted(set(out))):
        raise ComponentTemplateError(
            f"{field} requires unique deterministic references"
        )
    return out


def _texts(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ComponentTemplateError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise ComponentTemplateError(f"{field} has an invalid item count")
    return tuple(_text(item, field) for item in values)


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ComponentTemplateError(f"{field} must be a finite number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ComponentTemplateError(f"{field} must be a finite number")
    return number


@dataclass(frozen=True, slots=True)
class TemplateModule:
    """The unit every module-ratio parameter multiplies (材份 discipline)."""

    name: str
    definition: str
    unit: str

    SCHEMA = "TemplateModule@1"

    def __post_init__(self) -> None:
        require_identifier(self.name, "module name")
        _text(self.definition, "module definition")
        _text(self.unit, "module unit")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "name": self.name,
            "definition": self.definition,
            "unit": self.unit,
        }

    @classmethod
    def from_dict(cls, value: object) -> "TemplateModule":
        if not isinstance(value, dict) or set(value) != {
            "schema", "name", "definition", "unit",
        }:
            raise ComponentTemplateError("template module payload malformed")
        if value["schema"] != cls.SCHEMA:
            raise ComponentTemplateError("template module schema changed")
        return cls(
            name=value["name"],
            definition=value["definition"],
            unit=value["unit"],
        )


@dataclass(frozen=True, slots=True)
class TemplateParameter:
    """One number of the treatise page, never bare of its sources."""

    name: str
    form: ParameterForm
    value_json: str
    basis_refs: tuple[str, ...]

    SCHEMA = "TemplateParameter@1"

    def __post_init__(self) -> None:
        require_identifier(self.name, "parameter name")
        if not isinstance(self.form, ParameterForm):
            raise ComponentTemplateError("parameter form is invalid")
        if not isinstance(self.value_json, str):
            raise ComponentTemplateError("parameter value_json must be text")
        try:
            value = json.loads(self.value_json)
        except json.JSONDecodeError as exc:
            raise ComponentTemplateError(
                "parameter value must contain JSON"
            ) from exc
        if canonical_json(value) != self.value_json:
            raise ComponentTemplateError(
                "parameter value JSON must be canonical"
            )
        normalized = self._validated(value)
        object.__setattr__(self, "value_json", canonical_json(normalized))
        object.__setattr__(
            self,
            "basis_refs",
            _refs(self.basis_refs, f"parameter {self.name} basis_refs"),
        )

    @classmethod
    def create(
        cls,
        *,
        name: str,
        form: ParameterForm,
        value: object,
        basis_refs: tuple[str, ...],
    ) -> "TemplateParameter":
        return cls(
            name=name,
            form=form,
            value_json=canonical_json(value),
            basis_refs=basis_refs,
        )

    def _validated(self, value: object) -> object:
        label = f"parameter {self.name}"
        if self.form is ParameterForm.MODULE_RATIO:
            if not isinstance(value, dict) or set(value) != {
                "min", "max", "adopted",
            }:
                raise ComponentTemplateError(
                    f"{label} ratio requires min, max, adopted"
                )
            low = _finite(value["min"], f"{label} min")
            high = _finite(value["max"], f"{label} max")
            if low > high:
                raise ComponentTemplateError(f"{label} band is inverted")
            adopted = value["adopted"]
            if adopted is not None:
                adopted = _finite(adopted, f"{label} adopted")
                if not low <= adopted <= high:
                    raise ComponentTemplateError(
                        f"{label} adopted value leaves its band"
                    )
            return {"adopted": adopted, "max": high, "min": low}
        if self.form is ParameterForm.COUNT:
            if not isinstance(value, dict) or set(value) != {
                "min", "max", "adopted",
            }:
                raise ComponentTemplateError(
                    f"{label} count requires min, max, adopted"
                )
            low, high = value["min"], value["max"]
            for item, sub in ((low, "min"), (high, "max")):
                if isinstance(item, bool) or not isinstance(item, int):
                    raise ComponentTemplateError(
                        f"{label} {sub} must be an integer"
                    )
            if low < 0 or low > high:
                raise ComponentTemplateError(f"{label} count band is invalid")
            adopted = value["adopted"]
            if adopted is not None:
                if isinstance(adopted, bool) or not isinstance(adopted, int):
                    raise ComponentTemplateError(
                        f"{label} adopted must be an integer"
                    )
                if not low <= adopted <= high:
                    raise ComponentTemplateError(
                        f"{label} adopted value leaves its band"
                    )
            return {"adopted": adopted, "max": high, "min": low}
        if not isinstance(value, dict) or set(value) != {
            "expression", "adopted",
        }:
            raise ComponentTemplateError(
                f"{label} expression requires expression, adopted"
            )
        _text(value["expression"], f"{label} expression")
        adopted = value["adopted"]
        if adopted is not None:
            adopted = _finite(adopted, f"{label} adopted")
        return {"adopted": adopted, "expression": value["expression"]}

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "name": self.name,
            "form": self.form.value,
            "value_json": self.value_json,
            "basis_refs": list(self.basis_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "TemplateParameter":
        if not isinstance(value, dict) or set(value) != {
            "schema", "name", "form", "value_json", "basis_refs",
        }:
            raise ComponentTemplateError(
                "template parameter payload malformed"
            )
        if value["schema"] != cls.SCHEMA:
            raise ComponentTemplateError("template parameter schema changed")
        return cls(
            name=value["name"],
            form=ParameterForm(value["form"]),
            value_json=value["value_json"],
            basis_refs=tuple(value["basis_refs"]),
        )


@dataclass(frozen=True, slots=True)
class TemplateObligation:
    """One interface duty the instantiated component owes its context."""

    obligation_id: str
    kind: ObligationKind
    counterpart: str
    datum_role: str | None = None
    interval_m: tuple[float, float] | None = None
    basis_refs: tuple[str, ...] = ()

    SCHEMA = "TemplateObligation@1"

    def __post_init__(self) -> None:
        require_identifier(self.obligation_id, "obligation_id")
        if not isinstance(self.kind, ObligationKind):
            raise ComponentTemplateError("obligation kind is invalid")
        require_identifier(self.counterpart, "obligation counterpart")
        if self.datum_role is not None:
            require_identifier(self.datum_role, "obligation datum_role")
        if self.kind in _INTERVAL_KINDS:
            if (
                not isinstance(self.interval_m, tuple)
                or len(self.interval_m) != 2
            ):
                raise ComponentTemplateError(
                    f"{self.kind.value} obligation requires an interval"
                )
            low = _finite(self.interval_m[0], "obligation interval min")
            high = _finite(self.interval_m[1], "obligation interval max")
            if low < 0 or low > high:
                raise ComponentTemplateError(
                    "obligation interval is invalid"
                )
            object.__setattr__(self, "interval_m", (low, high))
        elif self.interval_m is not None:
            raise ComponentTemplateError(
                f"{self.kind.value} obligation carries no interval"
            )
        object.__setattr__(
            self,
            "basis_refs",
            _refs(
                self.basis_refs,
                f"obligation {self.obligation_id} basis_refs",
                allow_empty=True,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "obligation_id": self.obligation_id,
            "kind": self.kind.value,
            "counterpart": self.counterpart,
            "datum_role": self.datum_role,
            "interval_m": (
                list(self.interval_m) if self.interval_m is not None else None
            ),
            "basis_refs": list(self.basis_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "TemplateObligation":
        if not isinstance(value, dict) or set(value) != {
            "schema", "obligation_id", "kind", "counterpart",
            "datum_role", "interval_m", "basis_refs",
        }:
            raise ComponentTemplateError(
                "template obligation payload malformed"
            )
        if value["schema"] != cls.SCHEMA:
            raise ComponentTemplateError("template obligation schema changed")
        interval = value["interval_m"]
        return cls(
            obligation_id=value["obligation_id"],
            kind=ObligationKind(value["kind"]),
            counterpart=value["counterpart"],
            datum_role=value["datum_role"],
            interval_m=tuple(interval) if interval is not None else None,
            basis_refs=tuple(value["basis_refs"]),
        )


@dataclass(frozen=True, slots=True)
class TemplatePlate:
    """One witness image of the treatise page."""

    plate_id: str
    media_type: str
    sha256: str
    caption: str

    SCHEMA = "TemplatePlate@1"

    def __post_init__(self) -> None:
        require_identifier(self.plate_id, "plate_id")
        _text(self.media_type, "plate media_type")
        object.__setattr__(
            self, "sha256", require_sha256(self.sha256, "plate sha256")
        )
        _text(self.caption, "plate caption")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "plate_id": self.plate_id,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "caption": self.caption,
        }

    @classmethod
    def from_dict(cls, value: object) -> "TemplatePlate":
        if not isinstance(value, dict) or set(value) != {
            "schema", "plate_id", "media_type", "sha256", "caption",
        }:
            raise ComponentTemplateError("template plate payload malformed")
        if value["schema"] != cls.SCHEMA:
            raise ComponentTemplateError("template plate schema changed")
        return cls(
            plate_id=value["plate_id"],
            media_type=value["media_type"],
            sha256=value["sha256"],
            caption=value["caption"],
        )


class CaseVoteKind(StrEnum):
    """What one case vote is allowed to prove."""

    ORGANISATION_ONLY = "organisation_only"
    STAGE_QUALIFIED = "stage_qualified"


@dataclass(frozen=True, slots=True)
class CaseVote:
    """One non-isomorphic case in which this template survived.

    ``organisation_only`` records remain useful comparison evidence, but do
    not prove that the template survived a standard design stage.  A
    ``stage_qualified`` vote therefore binds an exact project Stage workflow,
    run envelope, satisfied closure, maturity state, and phase-entry proof.
    ``phase`` is deliberately separate from ``stage_id``/``stage_index``:
    several project Stages may share one broad design phase.

    ``CaseVote@1`` is accepted on read and migrates to an
    ``organisation_only`` ``CaseVote@2`` on the next write.  This prevents a
    legacy record with no stage evidence from silently acquiring promotion
    weight.
    """

    project_id: str
    run_id: str
    receipt_ref: str
    vote_kind: CaseVoteKind = CaseVoteKind.ORGANISATION_ONLY
    branch_id: str | None = None
    branch_epoch: int | None = None
    phase: DesignPhase | None = None
    workflow_ref: str | None = None
    workflow_digest: str | None = None
    stage_id: str | None = None
    stage_index: int | None = None
    state_ref: str | None = None
    state_digest: str | None = None
    stage_proof_ref: str | None = None
    stage_proof_digest: str | None = None
    stage_envelope_ref: str | None = None
    envelope_digest: str | None = None
    stage_exit_ref: str | None = None
    stage_exit_digest: str | None = None
    stage_closure_ref: str | None = None
    closure_digest: str | None = None

    SCHEMA = "CaseVote@2"
    LEGACY_SCHEMA = "CaseVote@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "vote project_id")
        require_identifier(self.run_id, "vote run_id")
        _ref(self.receipt_ref, "vote receipt_ref")
        if not isinstance(self.vote_kind, CaseVoteKind):
            raise ComponentTemplateError("vote_kind must be a CaseVoteKind")

        qualification = (
            self.branch_id,
            self.branch_epoch,
            self.phase,
            self.workflow_ref,
            self.workflow_digest,
            self.stage_id,
            self.stage_index,
            self.state_ref,
            self.state_digest,
            self.stage_proof_ref,
            self.stage_proof_digest,
            self.stage_envelope_ref,
            self.envelope_digest,
            self.stage_exit_ref,
            self.stage_exit_digest,
            self.stage_closure_ref,
            self.closure_digest,
        )
        if self.vote_kind is CaseVoteKind.ORGANISATION_ONLY:
            if any(value is not None for value in qualification):
                raise ComponentTemplateError(
                    "organisation_only vote cannot carry stage qualification"
                )
            return

        if any(value is None for value in qualification):
            raise ComponentTemplateError(
                "stage_qualified vote requires exact workflow, project Stage, "
                "branch, phase, state, stage-proof, envelope, exit binding, "
                "and closure references and digests"
            )
        require_identifier(self.branch_id, "vote branch_id")
        if (
            isinstance(self.branch_epoch, bool)
            or not isinstance(self.branch_epoch, int)
            or self.branch_epoch < 0
        ):
            raise ComponentTemplateError(
                "vote branch_epoch must be a non-negative integer"
            )
        if not isinstance(self.phase, DesignPhase):
            raise ComponentTemplateError("vote phase must be a DesignPhase")
        require_identifier(self.stage_id, "vote stage_id")
        if (
            isinstance(self.stage_index, bool)
            or not isinstance(self.stage_index, int)
            or self.stage_index < 0
        ):
            raise ComponentTemplateError(
                "vote stage_index must be a non-negative integer"
            )
        _ref(self.workflow_ref, "vote workflow_ref")
        _ref(self.state_ref, "vote state_ref")
        _ref(self.stage_proof_ref, "vote stage_proof_ref")
        _ref(self.stage_envelope_ref, "vote stage_envelope_ref")
        _ref(self.stage_exit_ref, "vote stage_exit_ref")
        _ref(self.stage_closure_ref, "vote stage_closure_ref")
        for field in (
            "workflow_digest",
            "state_digest",
            "stage_proof_digest",
            "envelope_digest",
            "stage_exit_digest",
            "closure_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), f"vote {field}"),
            )

    @property
    def stage_qualified(self) -> bool:
        return self.vote_kind is CaseVoteKind.STAGE_QUALIFIED

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "receipt_ref": self.receipt_ref,
            "vote_kind": self.vote_kind.value,
            "branch_id": self.branch_id,
            "branch_epoch": self.branch_epoch,
            "phase": self.phase.value if self.phase is not None else None,
            "workflow_ref": self.workflow_ref,
            "workflow_digest": self.workflow_digest,
            "stage_id": self.stage_id,
            "stage_index": self.stage_index,
            "state_ref": self.state_ref,
            "state_digest": self.state_digest,
            "stage_proof_ref": self.stage_proof_ref,
            "stage_proof_digest": self.stage_proof_digest,
            "stage_envelope_ref": self.stage_envelope_ref,
            "envelope_digest": self.envelope_digest,
            "stage_exit_ref": self.stage_exit_ref,
            "stage_exit_digest": self.stage_exit_digest,
            "stage_closure_ref": self.stage_closure_ref,
            "closure_digest": self.closure_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CaseVote":
        if not isinstance(value, dict):
            raise ComponentTemplateError("case vote payload malformed")
        if value.get("schema") == cls.LEGACY_SCHEMA:
            if set(value) != {
                "schema",
                "project_id",
                "run_id",
                "receipt_ref",
            }:
                raise ComponentTemplateError("legacy case vote payload malformed")
            return cls(
                project_id=value["project_id"],
                run_id=value["run_id"],
                receipt_ref=value["receipt_ref"],
            )
        if set(value) != {
            "schema",
            "project_id",
            "run_id",
            "receipt_ref",
            "vote_kind",
            "branch_id",
            "branch_epoch",
            "phase",
            "workflow_ref",
            "workflow_digest",
            "stage_id",
            "stage_index",
            "state_ref",
            "state_digest",
            "stage_proof_ref",
            "stage_proof_digest",
            "stage_envelope_ref",
            "envelope_digest",
            "stage_exit_ref",
            "stage_exit_digest",
            "stage_closure_ref",
            "closure_digest",
        } or value.get("schema") != cls.SCHEMA:
            raise ComponentTemplateError("case vote payload malformed")
        phase = value["phase"]
        return cls(
            project_id=value["project_id"],
            run_id=value["run_id"],
            receipt_ref=value["receipt_ref"],
            vote_kind=CaseVoteKind(value["vote_kind"]),
            branch_id=value["branch_id"],
            branch_epoch=value["branch_epoch"],
            phase=DesignPhase(phase) if phase is not None else None,
            workflow_ref=value["workflow_ref"],
            workflow_digest=value["workflow_digest"],
            stage_id=value["stage_id"],
            stage_index=value["stage_index"],
            state_ref=value["state_ref"],
            state_digest=value["state_digest"],
            stage_proof_ref=value["stage_proof_ref"],
            stage_proof_digest=value["stage_proof_digest"],
            stage_envelope_ref=value["stage_envelope_ref"],
            envelope_digest=value["envelope_digest"],
            stage_exit_ref=value["stage_exit_ref"],
            stage_exit_digest=value["stage_exit_digest"],
            stage_closure_ref=value["stage_closure_ref"],
            closure_digest=value["closure_digest"],
        )


@dataclass(frozen=True, slots=True)
class ComponentTemplate:
    """One treatise page: a reusable component as a provenance-bound record."""

    template_id: str
    family: str
    edition: int
    mathematics_ref: str
    module: TemplateModule
    parameters: tuple[TemplateParameter, ...]
    obligations: tuple[TemplateObligation, ...]
    plates: tuple[TemplatePlate, ...]
    applicability: tuple[str, ...]
    basis_refs: tuple[str, ...]
    case_votes: tuple[CaseVote, ...]
    harvested_from_project: str
    harvested_from_run: str
    open_boundaries: tuple[str, ...] = ()
    predecessor_ref: str | None = None

    SCHEMA = "ComponentTemplate@1"

    def __post_init__(self) -> None:
        require_identifier(self.template_id, "template_id")
        require_identifier(self.family, "template family")
        if isinstance(self.edition, bool) or not isinstance(self.edition, int):
            raise ComponentTemplateError("edition must be an integer")
        if self.edition < 1:
            raise ComponentTemplateError("edition must be positive")
        _ref(self.mathematics_ref, "mathematics_ref")
        if not isinstance(self.module, TemplateModule):
            raise ComponentTemplateError("module must be a TemplateModule")
        self._items(self.parameters, TemplateParameter, "parameters", "name")
        self._items(
            self.obligations,
            TemplateObligation,
            "obligations",
            "obligation_id",
        )
        self._items(self.plates, TemplatePlate, "plates", "plate_id")
        object.__setattr__(
            self,
            "applicability",
            _texts(self.applicability, "applicability"),
        )
        object.__setattr__(
            self,
            "open_boundaries",
            _texts(self.open_boundaries, "open_boundaries", allow_empty=True),
        )
        object.__setattr__(
            self, "basis_refs", _refs(self.basis_refs, "template basis_refs")
        )
        if not isinstance(self.case_votes, tuple) or not self.case_votes:
            raise ComponentTemplateError(
                "template requires at least its harvest case vote"
            )
        if any(
            not isinstance(item, CaseVote) for item in self.case_votes
        ):
            raise ComponentTemplateError("case_votes contains an invalid item")
        keys = tuple(
            (item.project_id, item.run_id) for item in self.case_votes
        )
        if keys != tuple(sorted(set(keys))):
            raise ComponentTemplateError(
                "case_votes require unique deterministic cases"
            )
        require_identifier(
            self.harvested_from_project, "harvested_from_project"
        )
        require_identifier(self.harvested_from_run, "harvested_from_run")
        if self.predecessor_ref is not None:
            _ref(self.predecessor_ref, "predecessor_ref")

    @staticmethod
    def _items(
        values: object,
        item_type: type,
        field: str,
        id_field: str,
    ) -> None:
        if not isinstance(values, tuple) or not values:
            raise ComponentTemplateError(f"{field} must be a non-empty tuple")
        if any(not isinstance(item, item_type) for item in values):
            raise ComponentTemplateError(f"{field} contains an invalid item")
        ids = tuple(getattr(item, id_field) for item in values)
        if ids != tuple(sorted(set(ids))):
            raise ComponentTemplateError(
                f"{field} requires unique deterministic identities"
            )

    def distinct_vote_projects(self) -> tuple[str, ...]:
        return tuple(sorted({item.project_id for item in self.case_votes}))

    def cited_basis_refs(self) -> tuple[str, ...]:
        refs = set(self.basis_refs)
        for parameter in self.parameters:
            refs.update(parameter.basis_refs)
        for obligation in self.obligations:
            refs.update(obligation.basis_refs)
        return tuple(sorted(refs))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "template_id": self.template_id,
            "family": self.family,
            "edition": self.edition,
            "predecessor_ref": self.predecessor_ref,
            "mathematics_ref": self.mathematics_ref,
            "module": self.module.to_dict(),
            "parameters": [item.to_dict() for item in self.parameters],
            "obligations": [item.to_dict() for item in self.obligations],
            "plates": [item.to_dict() for item in self.plates],
            "applicability": list(self.applicability),
            "open_boundaries": list(self.open_boundaries),
            "basis_refs": list(self.basis_refs),
            "case_votes": [item.to_dict() for item in self.case_votes],
            "harvested_from_project": self.harvested_from_project,
            "harvested_from_run": self.harvested_from_run,
            **no_authority(
                (
                    "canonical_write_authority",
                    "design_authority",
                    "stage_acceptance_authority",
                )
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ComponentTemplate":
        expected = {
            "schema", "template_id", "family", "edition", "predecessor_ref",
            "mathematics_ref", "module", "parameters", "obligations",
            "plates", "applicability", "open_boundaries", "basis_refs",
            "case_votes", "harvested_from_project", "harvested_from_run",
            "canonical_write_authority", "design_authority",
            "stage_acceptance_authority",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ComponentTemplateError(
                "component template payload malformed"
            )
        if value["schema"] != cls.SCHEMA:
            raise ComponentTemplateError("component template schema changed")
        return cls(
            template_id=value["template_id"],
            family=value["family"],
            edition=value["edition"],
            predecessor_ref=value["predecessor_ref"],
            mathematics_ref=value["mathematics_ref"],
            module=TemplateModule.from_dict(value["module"]),
            parameters=tuple(
                TemplateParameter.from_dict(item)
                for item in value["parameters"]
            ),
            obligations=tuple(
                TemplateObligation.from_dict(item)
                for item in value["obligations"]
            ),
            plates=tuple(
                TemplatePlate.from_dict(item) for item in value["plates"]
            ),
            applicability=tuple(value["applicability"]),
            open_boundaries=tuple(value["open_boundaries"]),
            basis_refs=tuple(value["basis_refs"]),
            case_votes=tuple(
                CaseVote.from_dict(item) for item in value["case_votes"]
            ),
            harvested_from_project=value["harvested_from_project"],
            harvested_from_run=value["harvested_from_run"],
        )


def require_library_votes(
    template: ComponentTemplate,
    *,
    waiver_ref: str | None = None,
) -> None:
    """Fail closed unless the two-vote rule holds or a waiver is recorded.

    A ratio enters the shared library only after surviving two
    non-isomorphic cases (distinct projects). A recorded waiver reference
    lifts the guard explicitly and auditable — never silently.
    """

    if not isinstance(template, ComponentTemplate):
        raise ComponentTemplateError("template must be a ComponentTemplate")
    if waiver_ref is not None:
        _ref(waiver_ref, "two-vote waiver_ref")
        return
    votes = template.distinct_vote_projects()
    if len(votes) < LIBRARY_PROMOTION_MIN_VOTES:
        raise ComponentTemplateError(
            "library promotion requires votes from at least "
            f"{LIBRARY_PROMOTION_MIN_VOTES} distinct projects, found "
            f"{list(votes)}; record a waiver to proceed deliberately"
        )


def verify_template_datums(
    template: ComponentTemplate,
    published: tuple[InterfaceDatum, ...],
    binding: "dict[str, str] | None" = None,
) -> tuple[str, ...]:
    """Resolve every obligation datum_role against published datums.

    An obligation names a *role* ("landing-top"); a project publishes
    *datums* (InterfaceDatum ids). ``binding`` maps role to datum id.
    Without this check the link is string coincidence — the same
    disease as relations by coordinate coincidence, one level up.
    Returns sorted violation messages; empty means every role resolves
    to a datum the project actually publishes.
    """

    if not isinstance(template, ComponentTemplate):
        raise ComponentTemplateError("template must be a ComponentTemplate")
    if not isinstance(published, tuple) or any(
        not isinstance(item, InterfaceDatum) for item in published
    ):
        raise ComponentTemplateError("published must be a tuple of InterfaceDatum")
    binding = dict(binding or {})
    ids = {item.datum_id for item in published}
    violations: list[str] = []
    for obligation in template.obligations:
        role = obligation.datum_role
        if role is None:
            continue
        target = binding.get(role)
        if target is None:
            violations.append(
                f"{obligation.obligation_id}: role {role!r} has no datum binding"
            )
        elif target not in ids:
            violations.append(
                f"{obligation.obligation_id}: role {role!r} is bound to "
                f"{target!r}, which no published datum provides"
            )
    return tuple(sorted(violations))


# ---------------------------------------------------------------- P099
# A placed instance of a template edition. The binding is a dependency
# edge, so a new edition reopens exactly the instances of the old one
# (P063 repair lifted to the type level). An instance names what
# realizes it (operation and object ids), never a coordinate.


def template_edition_ref(template_id: str, edition: int) -> str:
    require_identifier(template_id, "template_id")
    if isinstance(edition, bool) or not isinstance(edition, int) or edition < 1:
        raise ComponentTemplateError("edition must be a positive integer")
    return f"template:{template_id}-edition-{edition}"


@dataclass(frozen=True, slots=True)
class ComponentInstance:
    instance_id: str
    template_id: str
    edition: int
    template_ref: str
    project_id: str
    run_id: str
    placement_refs: tuple[str, ...]
    host_ref: str | None = None

    SCHEMA = "ComponentInstance@1"

    def __post_init__(self) -> None:
        require_identifier(self.instance_id, "instance_id")
        template_edition_ref(self.template_id, self.edition)
        _ref(self.template_ref, "instance template_ref")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        object.__setattr__(
            self, "placement_refs", _refs(self.placement_refs, "instance placement_refs")
        )
        if self.host_ref is not None:
            _ref(self.host_ref, "instance host_ref")

    @property
    def ref(self) -> str:
        return f"instance:{self.instance_id}"

    @property
    def edition_ref(self) -> str:
        return template_edition_ref(self.template_id, self.edition)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "instance_id": self.instance_id,
            "template_id": self.template_id,
            "edition": self.edition,
            "template_ref": self.template_ref,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "placement_refs": list(self.placement_refs),
            "host_ref": self.host_ref,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ComponentInstance":
        if not isinstance(value, dict) or value.get("schema") != cls.SCHEMA:
            raise ComponentTemplateError("component instance payload malformed")
        return cls(
            instance_id=value["instance_id"],
            template_id=value["template_id"],
            edition=value["edition"],
            template_ref=value["template_ref"],
            project_id=value["project_id"],
            run_id=value["run_id"],
            placement_refs=tuple(value["placement_refs"]),
            host_ref=value.get("host_ref"),
        )

    @property
    def digest(self) -> str:
        import hashlib

        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


def instance_edition_edge(instance: ComponentInstance) -> DependencyEdge:
    """The instance depends on its template edition: a new edition reopens it."""

    if not isinstance(instance, ComponentInstance):
        raise ComponentTemplateError("instance must be a ComponentInstance")
    return DependencyEdge(
        upstream_ref=instance.edition_ref,
        downstream_ref=instance.ref,
        relation="instantiates_template_edition",
        source_ref=instance.template_ref,
        effect=DependencyEffect.REQUIRES_REVALIDATION,
    )
