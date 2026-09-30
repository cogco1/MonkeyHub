"""Local integration metadata and explicit qualification over existing adapters.

Discovery/status never starts a host. Qualification is an explicit runtime call
with the existing caller-owned workspace/source; it grants no project writer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .discovery import SoftwareDiscoveryRegistry


BridgeKind = Literal["background-cli", "desktop-host", "local-extension",
                     "dotnet-addin", "python-host", "native-sdk", "local-http",
                     "mcp", "cloud"]


@dataclass(frozen=True)
class PackComponent:
    component_id: str
    bridge: BridgeKind
    delivery: Literal["bundled", "optional-payload"]
    install_target: Literal["monkeyhub", "application-extension"]
    protocol: str | None = None
    events: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackCapability:
    capability_id: str
    component_id: str
    implementation: str | None
    permissions: tuple[str, ...]
    supported_os: tuple[str, ...]


@dataclass(frozen=True)
class PackWorkflow:
    workflow_id: str
    requires: tuple[str, ...]


@dataclass(frozen=True)
class IntegrationPack:
    pack_id: str
    product_name: str
    version: str
    components: tuple[PackComponent, ...]
    capabilities: tuple[PackCapability, ...]
    workflows: tuple[PackWorkflow, ...] = ()
    cad_backend: str | None = None

    def __post_init__(self):
        components = {row.component_id for row in self.components}
        capabilities = {row.capability_id for row in self.capabilities}
        if len(components) != len(self.components) or len(capabilities) != len(self.capabilities):
            raise ValueError("Pack component and capability ids must be unique")
        if any(row.component_id not in components for row in self.capabilities):
            raise ValueError("Capability must name a declared pack component")
        if any(not row.requires or not set(row.requires) <= capabilities for row in self.workflows):
            raise ValueError("Workflow must require declared capabilities")

    def manifest(self):
        return {
            "schema": "MonkeyIntegrationPack@1", "packId": self.pack_id,
            "product": {"id": self.pack_id, "name": self.product_name},
            "version": self.version, "discoveryProvider": "local-software",
            "components": [
                {"id": row.component_id, "bridge": row.bridge, "delivery": row.delivery,
                 "installTarget": row.install_target, "protocol": row.protocol,
                 "events": list(row.events)} for row in self.components
            ],
            "capabilities": [
                {"id": row.capability_id, "componentId": row.component_id,
                 "implementation": row.implementation, "permissions": list(row.permissions),
                 "supportedOs": list(row.supported_os)} for row in self.capabilities
            ],
            "workflows": [{"id": row.workflow_id, "requires": list(row.requires)}
                          for row in self.workflows],
        }


PACKS = (
    IntegrationPack(
        "rhino", "Rhino", "1",
        (PackComponent("rhino-host", "desktop-host", "bundled", "monkeyhub"),),
        (
            PackCapability("model.execute", "rhino-host", "compiled-cad-execution:rhino",
                           ("execute-in-speculative-workspace",), ("Windows",)),
            PackCapability("model.patch", "rhino-host", "compiled-cad-execution:rhino",
                           ("read-exact-source", "execute-in-speculative-workspace"), ("Windows",)),
        ),
        (PackWorkflow("rhino-patch-current-model", ("model.patch",)),),
        cad_backend="rhino",
    ),
    IntegrationPack(
        "blender", "Blender", "1",
        (PackComponent("blender-worker", "background-cli", "bundled", "monkeyhub"),),
        (
            PackCapability("model.execute", "blender-worker", "compiled-cad-execution:blender",
                           ("execute-in-speculative-workspace",), ("Windows", "Darwin", "Linux")),
            PackCapability("visualization.project", "blender-worker",
                           "monkeycad.backends.blender.projection.execute_blender_projection",
                           ("read-verified-model", "execute-in-speculative-workspace"),
                           ("Windows", "Darwin", "Linux")),
        ),
        (PackWorkflow("blender-visualization-render", ("visualization.project",)),),
        cad_backend="blender",
    ),
    IntegrationPack(
        "sketchup", "SketchUp", "1",
        (
            PackComponent("sketchup-sdk-reader", "native-sdk", "bundled", "monkeyhub"),
            PackComponent("sketchup-live-extension", "local-extension", "optional-payload",
                          "application-extension", events=("viewport.changed",)),
        ),
        (
            PackCapability("source.read", "sketchup-sdk-reader",
                           "monkeycad.formats.sketchup_reader.read_skp", ("read-source",), ("Windows",)),
            PackCapability("viewport.observe", "sketchup-live-extension", None,
                           ("observe-enabled-application",), ("Windows", "Darwin")),
            PackCapability("viewport.capture", "sketchup-live-extension", None,
                           ("capture-enabled-application",), ("Windows", "Darwin")),
        ),
        (PackWorkflow("sketchup-live-view-to-board", ("viewport.observe", "viewport.capture")),),
    ),
)


@dataclass(frozen=True)
class PackInstallation:
    """Installed Monkey components, supplied by the host, never by discovery.

    Phase 1 ships adapter code in the Hub. A future installer supplies the
    verified, activated payload version here only after successful staging.
    """
    pack_id: str
    version: str
    components: tuple[str, ...]
    enabled: bool = True


class IntegrationUnavailable(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class IntegrationPackManager:
    """Hub-scoped, in-memory view; no installer, background watcher or writer."""

    def __init__(self, *, discovery=None, installations=None):
        self.discovery = discovery if discovery is not None else SoftwareDiscoveryRegistry()
        self.packs = {pack.pack_id: pack for pack in PACKS}
        if installations is None:
            installations = (
                PackInstallation(pack.pack_id, pack.version,
                                 tuple(row.component_id for row in pack.components if row.delivery == "bundled"))
                for pack in PACKS
            )
        self.installations = {row.pack_id: row for row in installations}
        if not self.installations.keys() <= self.packs.keys():
            raise ValueError("Unknown integration pack installation")
        self._software = {}
        self._qualified = {}

    def rescan(self):
        # Explicit rescans revoke prior qualification, including removed/updated
        # applications. Merely visiting the status view never polls the machine.
        # Replace the snapshot so an already-running qualification cannot
        # republish its old evidence after this rescan.
        self._qualified = {}
        self._software = {pack_id: self.discovery.discover(pack_id) for pack_id in self.packs}

    def _pack(self, pack_id):
        try:
            return self.packs[pack_id]
        except KeyError:
            raise IntegrationUnavailable("unsupported", "Unknown integration pack") from None

    def _capability(self, pack_id, capability_id):
        pack = self._pack(pack_id)
        for row in pack.capabilities:
            if row.capability_id == capability_id:
                return row
        raise IntegrationUnavailable("unsupported", "Capability is not declared by this pack")

    def capability_status(self, pack_id, capability_id):
        capability = self._capability(pack_id, capability_id)
        if not self._software:
            self.rescan()
        pack = self.packs[pack_id]
        installed = self.installations.get(pack_id)
        software = self._software[pack_id]
        status, reason = "not-qualified", "An explicit runtime qualification has not succeeded."
        if software.system not in capability.supported_os:
            status, reason = "unsupported-os", "This capability is not implemented on this operating system."
        elif installed is None or capability.component_id not in installed.components:
            status, reason = "not-installed", "The required Monkey component is not installed."
        elif installed.version != pack.version:
            status, reason = "blocked-version", "The installed pack version does not match this contract."
        elif not installed.enabled:
            status, reason = "disabled", "This integration is disabled."
        elif capability.implementation is None:
            status, reason = "unsupported", "This bridge capability has not been implemented."
        elif (pack_id, capability_id) in self._qualified:
            return {"id": capability_id, **self._qualified[pack_id, capability_id]}
        return {"id": capability_id, "status": status, "reason": reason, "checkedAt": None}

    def _require(self, pack_id, capability_id):
        state = self.capability_status(pack_id, capability_id)
        if state["status"] not in ("not-qualified", "ready", "failed"):
            raise IntegrationUnavailable(state["status"], state["reason"])
        self._qualified.pop((pack_id, capability_id), None)
        return self._qualified

    def backend(self, pack_id):
        """Resolve the existing registry entry; never copy or replace a backend."""
        from .registry import get_cad_backend
        pack = self._pack(pack_id)
        if pack.cad_backend is None:
            raise IntegrationUnavailable("unsupported", "This pack has no compiled CAD backend")
        return get_cad_backend(pack.cad_backend)

    def _record(self, snapshot, pack_id, capability_id, *, ready, reason):
        snapshot[pack_id, capability_id] = {
            "status": "ready" if ready else "failed", "reason": reason,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
        }

    def qualify_cad(self, pack_id, request):
        """Explicitly execute a caller-supplied bounded fixture with cold readback.

        This method is not an HTTP/Agent action. Its caller must already have
        permission to run the selected host and supply the exact bound request.
        It returns the original backend result, without a new receipt.
        """
        capability_id = "model.patch" if request.source is not None and pack_id == "rhino" else "model.execute"
        snapshot = self._require(pack_id, capability_id)
        backend = self.backend(pack_id)
        try:
            result = backend.execute(request)
            result.validate(request, backend.backend_id)
        except Exception:
            self._record(snapshot, pack_id, capability_id, ready=False, reason="The existing backend could not verify the fixture.")
            raise
        self._record(snapshot, pack_id, capability_id, ready=result.status == "succeeded" and result.readback_verified,
                     reason="Saved fixture and independent readback passed." if result.readback_verified
                     else "The existing backend refused or failed this fixture.")
        return result

    def qualify_sketchup_read(self, data, *, sdk_path=None):
        """Explicit native SDK read; its existing API/version checks still apply."""
        from .formats.sketchup_reader import read_skp
        snapshot = self._require("sketchup", "source.read")
        try:
            scene = read_skp(data, sdk_path=sdk_path)
        except Exception:
            self._record(snapshot, "sketchup", "source.read", ready=False,
                         reason="The existing SKP reader could not qualify this source and SDK.")
            raise
        self._record(snapshot, "sketchup", "source.read", ready=True,
                     reason="The existing SDK reader opened the supplied source and checked its required API.")
        return scene

    def qualify_blender_projection(self, request, source, **kwargs):
        """Delegate the existing source-bound projection and artifact validator."""
        from .backends.blender.projection import execute_blender_projection, verify_projection_artifacts
        snapshot = self._require("blender", "visualization.project")
        try:
            receipt = execute_blender_projection(request, source, **kwargs)
            if receipt.get("status") != "succeeded":
                self._record(snapshot, "blender", "visualization.project", ready=False,
                             reason="The existing projection worker refused or failed this fixture.")
                return receipt
            # The executor already reopens the scene and image independently.
            # Also require its existing retained-artifact verification.
            verify_projection_artifacts(request, source, receipt)
        except Exception:
            self._record(snapshot, "blender", "visualization.project", ready=False,
                         reason="The existing projection worker could not verify the saved scene and image.")
            raise
        self._record(snapshot, "blender", "visualization.project", ready=True,
                     reason="Saved scene, render and source-bound artifact verification passed.")
        return receipt

    def status(self, *, rescan=False):
        if rescan or not self._software:
            self.rescan()
        rows = []
        for pack in PACKS:
            software = self._software[pack.pack_id]
            installed = self.installations.get(pack.pack_id)
            capabilities = [self.capability_status(pack.pack_id, row.capability_id) for row in pack.capabilities]
            ready = {row["id"] for row in capabilities if row["status"] == "ready"}
            rows.append({
                "manifest": pack.manifest(),
                "software": {"detected": bool(software.installations), "operatingSystem": software.system,
                             "installations": [row.public() for row in software.installations],
                             "diagnostics": list(software.diagnostics)},
                "installation": {"status": "not-installed" if installed is None else
                                 "enabled" if installed.enabled else "disabled",
                                 "version": installed.version if installed else None,
                                 "components": list(installed.components) if installed else []},
                "capabilities": capabilities,
                "workflows": [{"id": row.workflow_id, "available": set(row.requires) <= ready}
                              for row in pack.workflows],
            })
        return {"packs": rows}
