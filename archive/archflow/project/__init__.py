"""Side-effect-free project document contracts.

P035 defines identity, layout, logical references, and persistence ports only.
Durable filesystem implementations belong to P036.
"""

from archive.archflow.project.bootstrap import (
    ProjectBootstrapResult,
    bootstrap_raw_request_project,
)
from archflow.project.digests import (
    project_state_sha256,
)
from archflow.project.layout import ProjectLayout, RunLayout
from archflow.project.location import (
    ProjectLocation,
    ProjectLocationError,
    ProjectLocationKind,
    locate_project,
    open_located_project,
)
from archflow.project.manifest import (
    ProjectManifest,
    ProjectManifestError,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination, PersistenceDestinationRequired, RecordSink, require_destination
from archive.archflow.project.ports import ArtifactSink, CanonicalRepository, ProjectLoader
from archflow.project.refs import (
    BranchRef,
    ProjectArtifactRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.project.repository import (
    FilesystemProjectRepository,
    PreparedTransition,
    ProjectAlreadyExists,
    ProjectHeadLocked,
    ProjectIntegrityError,
    ProjectRepositoryError,
    PromotionAuthorityError,
    RecoveryReport,
    StaleProjectHead,
)
from archive.archflow.project.runtime import (
    RUNTIME_CONFIG_SCHEMA,
    RuntimeConfigError,
    RuntimeInitializationReceipt,
    RuntimePaths,
    bootstrap_external_project,
    initialize_runtime,
    load_runtime_config,
    prepare_external_stage0,
)

__all__ = [
    "ArtifactSink",
    "BranchRef",
    "CanonicalRepository",
    "FilesystemProjectRepository",
    "PersistenceArea",
    "PersistenceDestination",
    "PersistenceDestinationRequired",
    "PreparedTransition",
    "ProjectBootstrapResult",
    "ProjectAlreadyExists",
    "ProjectArtifactRef",
    "ProjectHeadLocked",
    "ProjectIntegrityError",
    "ProjectLayout",
    "ProjectLocation",
    "ProjectLocationError",
    "ProjectLocationKind",
    "ProjectLoader",
    "ProjectManifest",
    "ProjectManifestError",
    "ProjectRecordRef",
    "ProjectRepositoryError",
    "ProjectVersionRef",
    "project_state_sha256",
    "PromotionAuthorityError",
    "RecordSink",
    "RecoveryReport",
    "RUNTIME_CONFIG_SCHEMA",
    "RuntimeConfigError",
    "RuntimeInitializationReceipt",
    "RuntimePaths",
    "RunLayout",
    "RunRef",
    "StaleProjectHead",
    "bootstrap_raw_request_project",
    "bootstrap_external_project",
    "initialize_runtime",
    "load_runtime_config",
    "locate_project",
    "open_located_project",
    "prepare_external_stage0",
    "require_destination",
]
