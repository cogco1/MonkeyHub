"""Load every module that declares where its records keep a version identity.

A declaration registers when its owner module is imported. That makes the
declared table depend on which modules a caller happened to import, which is
not a property anything may rely on: a command that never imported
``archflow.project.issue`` would find ``PromotionDecision@1`` undeclared and
refuse every project that ever promoted a candidate.

So this module names the owners, once, and importing it loads them all. Every
reader of the table imports this rather than trusting an incidental import.
Adding an owner means adding it here; :mod:`tests.integration.test_version_refs` checks the
table against the record-kind registry, so an owner that is written but not
loaded is a test failure rather than a migration that quietly refuses.

It sits above the modules it loads on purpose and holds no logic of its own.

It loads the shared core's owners only. A record kind owned by a workflow
module (``monkeydiagram``, ``monkeyarch``) declares itself the same way, but
the core may not import a workflow, so in a core-only process that kind stays
undeclared - and a project retaining one is refused with a named blocker
rather than migrated on a guess. A caller that has the workflow loaded gets
the declaration; that is the boundary, stated rather than worked around.

The CAD records are declared below on their writers' behalf. Nearly every
project that has run a stage retains one, so a core-only migration has to be
able to restate it, and the core does not import CAD code to learn where it
keeps its base.
"""

from __future__ import annotations

# Imported for the declaration each one registers; none of these names is used.
import archflow.project.issue  # noqa: F401
import archflow.project.refs  # noqa: F401
import archflow.state.developed_design  # noqa: F401
import archflow.state.spatial  # noqa: F401
import archflow.state.stage_workflow  # noqa: F401
import archflow.state.state_record  # noqa: F401
from archflow.project.version_refs import register, register_derived

OWNER_MODULES = (
    "archflow.project.issue",
    "archflow.project.refs",
    "archflow.project.repository",
    "archflow.state.developed_design",
    "archflow.state.spatial",
    "archflow.state.stage_workflow",
    "archflow.state.state_record",
)

# Owned by a workflow package. The shared core may not import these, so an
# entry point above both layers loads them; the names live here so there is
# still one list of owners rather than one per caller.
WORKFLOW_OWNER_MODULES = (
    "monkeydiagram.drawing_elevation",
)


def load_workflow_owners() -> tuple[str, ...]:
    """Import the workflow tier. Only a caller above both layers may do this."""

    from importlib import import_module

    for name in WORKFLOW_OWNER_MODULES:
        import_module(name)
    return WORKFLOW_OWNER_MODULES


# Declared here rather than by its owner, and said plainly: the runner receipt
# is written by ``packages/monkeyarch/src/monkeyarch/runtime/project_runner.py``, which another lane
# holds open. It derives nothing from the canonical base it carries - its
# ``state_record_digest`` is ``StateRecord.digest``, which excludes ``base``,
# and the envelope and developed-state digests it cites are other records'
# content digests, which the cascade moves with those records. Leaving it
# unstated would refuse every project that has ever run a stage. This belongs
# in project_runner.py the moment that file is free.
_BORROWED_DECLARATIONS = {"RunnerRunReceipt@3": ()}

for _schema, _fields in _BORROWED_DECLARATIONS.items():
    register_derived(_schema, _fields)


# Declared here rather than by the CAD package that writes these records,
# ``monkeycad.execution``, its backends and ``monkeycad.formats.three_dm_inspector``, and said
# plainly: the core does not import them, and a core-only migration still has
# to restate every CAD record a project retains. What the records serialise is
# still the CAD package's to say,
# so a change there is a change here; :mod:`tests.integration.test_version_refs` builds the
# binding and the inspection summary with their own writers and checks each
# declaration against what they write.
#
# The canonical base a CAD execution reaches the adapter with lives in the
# program binding, and every receipt family carries that binding rather than
# repeating the base: Rhino, OCCT and Blender receipts all serialise it at
# ``identity.binding``, and the Blender projection receipt at ``binding``.
# Declaring the binding once covers all four, and none of the receipts derives
# anything from the base: their object digests come from the compiled program,
# their artifact digests name files, and the base itself is only carried.
#
# A CAD document carries the canonical base it was exported from as one of its
# user strings, not as a field named for a version: the row is
# ``{"key": "archflow:base_state_sha256", "value": <digest>}`` and its position
# moves with the sorted table, so the declaration names the row by its key.
# Without this a migrated model still reports the version it was built at.
_CAD_VERSION_REF_POINTERS = {
    "RhinoCadProgramBinding@1": ("/base",),
    "ThreeDmInspectionSummary@4": (
        "/document_user_strings[key=archflow:base_state_sha256]/value",
    ),
}
_CAD_DERIVED_FIELDS = {
    "RhinoCadProgramBinding@1": (),
    "ThreeDmInspectionSummary@4": (),
    "RhinoCadExecutionReceipt@4": (),
    "OcctExecutionReceipt@1": (),
    "BlenderExecutionReceipt@1": (),
    "BlenderProjectionReceipt@1": (),
    "RhinoCadExportIdentity@2": (),
    "OcctCadExportIdentity@1": (),
}

register(_CAD_VERSION_REF_POINTERS)
for _schema, _fields in _CAD_DERIVED_FIELDS.items():
    register_derived(_schema, _fields)
