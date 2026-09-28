"""archflow.project.index: the derived project index (ADR-008 phase 1b).

``ProjectIndex`` keeps the rows one ``Projector`` derives from a project in a
SQLite file outside it, and answers read-only snapshots. ``IndexKeeper`` is
its only writer: a thread fed by the project's layout watch and this
process's write observer, so no reader ever projects, and it announces each
commit to the listeners ``add_commit_listener`` registers (#366). Neither is a
source of truth: the P036 records are, and every row names the record it was
read from.
"""

from .keeper import IndexKeeper, IndexState, add_commit_listener
from .store import (
    CHANGE_LOG_REVISIONS,
    INDEX_FILE,
    QUERYABLE,
    SCHEMA_VERSION,
    ArtifactRow,
    CandidateRow,
    DocumentRow,
    IndexCommit,
    IndexSnapshot,
    IndexStamp,
    IndexLocked,
    IndexToken,
    IndexUnavailable,
    ProjectIndex,
    Projector,
    RecordRow,
    RunRows,
    StageRow,
    TreeRows,
    entity_area,
    line_places,
    manifest_stamp,
    path_area,
    place_area,
)

__all__ = [
    "CHANGE_LOG_REVISIONS",
    "INDEX_FILE",
    "QUERYABLE",
    "SCHEMA_VERSION",
    "ArtifactRow",
    "CandidateRow",
    "DocumentRow",
    "IndexCommit",
    "IndexKeeper",
    "IndexLocked",
    "IndexSnapshot",
    "IndexStamp",
    "IndexState",
    "IndexToken",
    "IndexUnavailable",
    "ProjectIndex",
    "Projector",
    "RecordRow",
    "RunRows",
    "StageRow",
    "TreeRows",
    "add_commit_listener",
    "entity_area",
    "line_places",
    "manifest_stamp",
    "path_area",
    "place_area",
]
