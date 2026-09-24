"""Runtime providers for conversion; no queue, project writer or implicit plugins."""
from dataclasses import dataclass, field
from typing import Literal, Protocol

from .local_cad_discovery import Discovery, discover_local_cad
from .model_formats import ConversionError, FORMATS, VERSION, ThreeDM, encode_mesh, validate_mesh

NO_EXECUTOR = "当前没有配置可用的执行器"
ExecutionMode = Literal["local desktop", "headless", "cloud", "SDK"]
CapabilityStatus = Literal["available", "available-local-host", "available-cloud",
                           "blocked-license", "blocked-runtime", "unsupported-semantics"]


@dataclass(frozen=True)
class Capability:
    available: bool
    verified: bool
    status: CapabilityStatus
    reason: str


@dataclass(frozen=True)
class ConvertedFile:
    data: bytes
    details: dict = field(default_factory=dict)
    intermediate_formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class Validation:
    passed: bool
    checks: tuple[str, ...] = ()
    reason: str | None = None
    source_dimension: int | None = None
    output_dimension: int | None = None
    measurements: dict = field(default_factory=dict)


class ConversionProvider(Protocol):
    """Server-owned implementation, not an executable chosen by a chat request.

    capability() verifies each route's runtime, license/session/version and
    implementation qualification. validate() must reopen actual output and
    measure it against input; a write return code alone is not validation.
    Native/SDK/cloud implementations may be explicitly supplied by the runtime
    after qualification. No formats or intermediary meshes are imposed on them.
    """
    name: str
    version: str
    input_formats: tuple[str, ...]
    output_formats: tuple[str, ...]
    execution_mode: ExecutionMode
    execution_host: str

    def capability(self, source: str, target: str) -> Capability: ...
    def convert(self, data: bytes, source: str, target: str) -> ConvertedFile: ...
    def validate(self, data: bytes, source: str, target: str, output: ConvertedFile) -> Validation: ...


class InProcessMeshProvider:
    name = "InProcessMeshProvider"
    version = VERSION
    input_formats = output_formats = ("3dm", "glb")
    execution_mode = "headless"
    execution_host = "in-process"

    def capability(self, source, target):
        if source not in self.input_formats or target not in self.output_formats:
            return Capability(False, False, "unsupported-semantics", "This provider implements only bounded 3DM/GLB mesh interchange.")
        if "3dm" in (source, target):
            try:
                ThreeDM()
            except Exception:
                return Capability(False, True, "blocked-runtime", "The required rhino3dm runtime could not be loaded.")
        return Capability(True, True, "available", "Verified bounded meshes; unsupported geometry is refused and losses are reported.")

    def convert(self, data, source, target):
        output, details = encode_mesh(data, source, target)
        details["losses"] = ({"units": "unchanged", "layers": "unchanged", "materials": "unchanged",
                              "structure": "unchanged", "geometry": "unchanged"} if source == target else {
            "units": "Normalized to meters; scale and placement checked by readback.",
            "layers": "Flattened; layer counts are recorded in source/output metrics.",
            "materials": "Materials, textures and custom normals are not transferred.",
            "structure": "Hierarchy, instances and CAD metadata are not transferred.",
            "geometry": "Triangle meshes only; saved BREP render meshes approximate exact surfaces; no solids reconstructed.",
        })
        return ConvertedFile(output, details)

    def validate(self, data, source, target, output):
        measurements = validate_mesh(data, source, target, output.data)
        return Validation(True, ("signature", "source-and-output-reopen", "object-and-triangle-counts", "meter-scale-and-bounds"),
                          source_dimension=3, output_dimension=3, measurements=measurements)


@dataclass(frozen=True)
class LocalSoftwareProvider:
    """Discovery-only native seam; installed software NEVER enables convert()."""
    name: str
    product: str
    execution_mode: ExecutionMode
    discovery: Discovery
    candidate_inputs: tuple[str, ...]
    candidate_outputs: tuple[str, ...]
    version: str = "native-discovery/1"
    execution_host: str = "local-application"
    # No native route has been implemented/qualified in this PR.
    input_formats: tuple[str, ...] = ()
    output_formats: tuple[str, ...] = ()

    def observation(self):
        installations = [row.public() for row in self.discovery.installations if row.product == self.product]
        return {"detected": bool(installations), "installations": installations,
                "operatingSystem": self.discovery.system, "versionCompatibility": "unverified",
                "licenseStatus": "unverified", "automationStatus": "not-implemented",
                "candidateInputFormats": list(self.candidate_inputs),
                "candidateOutputFormats": list(self.candidate_outputs),
                "diagnostics": list(self.discovery.diagnostics)}

    def capability(self, source, target):
        observed = self.observation()
        if observed["detected"]:
            reason = "Software detected, but no verified automation bridge/validator is configured; version compatibility, entitlement and session readiness are unverified."
        else:
            reason = "Software was not found in the inspected locations; no verified automation bridge/validator is configured."
        # Absence of a converter, not a permanent property of SKP/DWG.
        return Capability(False, False, "blocked-runtime", NO_EXECUTOR + ": " + reason)

    def convert(self, data, source, target):
        raise ConversionError(self.capability(source, target).reason)

    def validate(self, data, source, target, output):
        return Validation(False, reason=NO_EXECUTOR + ": native output validation is not implemented.")


def configured_providers():
    """Existing in-process provider plus safe local observations; no cloud calls."""
    discovery = discover_local_cad()
    return (
        InProcessMeshProvider(),
        LocalSoftwareProvider("SketchUpDesktopProvider", "sketchup", "local desktop", discovery,
                              ("skp", "dwg"), ("skp", "dwg", "glb")),
        LocalSoftwareProvider("AutoCADCoreProvider", "autocad-core", "headless", discovery,
                              ("dwg",), ("dwg",)),
        LocalSoftwareProvider("AutoCADDesktopProvider", "autocad", "local desktop", discovery,
                              ("dwg",), ("dwg",)),
    )


def preview_policy(source, dimension=None):
    # Unknown DWG content is not evidence of 3D. No preview is generated here.
    formats = ("pdf", "svg", "png", "jpg") if source == "dwg" and dimension != 3 else ("png", "jpg", "glb")
    return {"status": "not-generated", "sourceDimension": dimension,
            "suggestedFormats": list(formats), "role": "sibling-preview",
            "mayInvent3DGeometry": False}


def _identity(provider):
    return {"provider": provider.name, "providerVersion": provider.version,
            "executionMode": provider.execution_mode, "executionHost": provider.execution_host}


class ConversionFailure(ConversionError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


class ConversionCoordinator:
    def __init__(self, providers=None):
        self.providers = tuple(configured_providers() if providers is None else providers)

    def inspect(self, source, target):
        observations = []
        for provider in self.providers:
            try:
                cap = provider.capability(source, target)
            except Exception as exc:
                cap = Capability(False, False, "blocked-runtime", "Capability probe failed: " + type(exc).__name__)
            observation = _identity(provider) | {"available": cap.available, "verified": cap.verified,
                "status": cap.status, "reason": cap.reason,
                "inputFormats": list(provider.input_formats), "outputFormats": list(provider.output_formats)}
            if isinstance(provider, LocalSoftwareProvider):
                observation["discovery"] = provider.observation()
            observations.append((provider, cap, observation))
        return observations

    @staticmethod
    def _eligible(observations, source, target):
        eligible = [p for p, cap, _ in observations if cap.available and cap.verified
                    and source in p.input_formats and target in p.output_formats]
        # All candidates are verified first. Local-native beats in-process/SDK,
        # and cloud is used only when explicitly supplied and no local is eligible.
        return sorted(eligible, key=lambda p: (
            0 if p.execution_host == "local-application" else
            3 if p.execution_mode == "cloud" else 2 if p.execution_mode == "SDK" else 1))

    def capabilities(self):
        rows = []
        for source in FORMATS:
            for target in FORMATS:
                if source == target:
                    continue
                observations = self.inspect(source, target)
                eligible = self._eligible(observations, source, target)
                selected = eligible[0] if eligible else None
                selected_cap = next((c for p, c, _ in observations if p is selected), None)
                rows.append({"sourceFormat": source, "targetFormat": target,
                    "status": selected_cap.status if selected_cap else "blocked-runtime",
                    "available": selected is not None,
                    "provider": selected.name if selected else None,
                    "reason": selected_cap.reason if selected_cap else NO_EXECUTOR,
                    "providers": [obs for _, _, obs in observations]})
        return rows

    def convert(self, data, source, target):
        if source not in FORMATS or target not in FORMATS:
            raise ConversionError("Choose a 3DM, SKP, GLB or DWG format.")
        observations = self.inspect(source, target)
        eligible = self._eligible(observations, source, target)
        report = {"sourceFormat": source, "targetFormat": target,
                  "provider": None, "providerVersion": None, "executionMode": None, "executionHost": None,
                  "providerCandidates": [obs for _, _, obs in observations],
                  "intermediateFormats": [], "usedIntermediateFormats": False,
                  "previewArtifacts": [], "previewPolicy": preview_policy(source),
                  "artifactRoles": {"sourceArtifact": "original", "outputArtifact": "delivery", "previewArtifacts": "preview"},
                  "outputValidation": {"status": "not-run", "checks": []}}
        if not eligible:
            raise ConversionFailure(NO_EXECUTOR, report | {"failureCode": "NO_CONFIGURED_EXECUTOR"})
        selected = eligible[0]
        report.update(_identity(selected))
        stage = "convert"
        try:
            result = selected.convert(data, source, target)
            if not isinstance(result, ConvertedFile) or not isinstance(result.data, bytes) or not result.data:
                raise ConversionError("Provider returned no output bytes.")
            report.update({"intermediateFormats": list(result.intermediate_formats),
                           "usedIntermediateFormats": bool(result.intermediate_formats)})
            for key in ("losses", "warnings", "converter", "converterVersion"):
                if key in result.details:
                    report[key] = result.details[key]
            stage = "validate"
            validation = selected.validate(data, source, target, result)
            if not validation.passed or not validation.checks:
                raise ConversionError(validation.reason or "Provider returned no successful output validation.")
            if validation.source_dimension == 2 and validation.output_dimension != 2:
                raise ConversionError("A 2D source must remain 2D; conversion/preview cannot invent 3D geometry.")
            if source == "dwg" and validation.source_dimension not in (2, 3):
                raise ConversionError("DWG dimensionality must be validated; unknown content is not evidence of 3D geometry.")
            report["outputValidation"] = {"status": "passed", "checks": list(validation.checks),
                "sourceDimension": validation.source_dimension, "outputDimension": validation.output_dimension}
            report["previewPolicy"] = preview_policy(source, validation.source_dimension)
            # Provider details cannot replace coordinator-owned provenance/validation.
            return result.data, result.details | validation.measurements | report
        except Exception as exc:
            # Do not silently reroute a failed native/cloud job or leak local paths.
            reason = str(exc) if isinstance(exc, ConversionError) else "Provider " + stage + " failed: " + type(exc).__name__
            if stage == "validate":
                report["outputValidation"] = {"status": "failed", "checks": [], "reason": reason}
            raise ConversionFailure(reason, report | {"failureCode": "VALIDATION_FAILED" if stage == "validate" else "CONVERSION_FAILED"}) from exc
