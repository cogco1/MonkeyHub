"""Side-effect-free project document contracts.

P035 defines identity, layout, logical references, and persistence ports only.
Durable filesystem implementations belong to P036.
"""

from archflow.project.bootstrap import (
    ProjectBootstrapResult,
    bootstrap_raw_request_project,
)
from archflow.project.digests import (
    canonical_json_sha256,
    project_state_sha256,
)
from archflow.project.layout import ProjectLayout, RunLayout
from archflow.project.manifest import (
    ProjectManifest,
    ProjectManifestError,
)
from archflow.project.ports import (
    ArtifactSink,
    CanonicalRepository,
    PersistenceArea,
    PersistenceDestination,
    PersistenceDestinationRequired,
    ProjectLoader,
    RecordSink,
    require_destination,
)
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
from archflow.project.runtime import (
    RUNTIME_CONFIG_SCHEMA,
    RuntimeConfigError,
    RuntimeInitializationReceipt,
    RuntimePaths,
    bootstrap_external_project,
    initialize_runtime,
    load_runtime_config,
)

__all__ = [
    "ArtifactSink",
    "BranchRef",
    "CanonicalRepository",
    "canonical_json_sha256",
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
    "require_destination",
]
