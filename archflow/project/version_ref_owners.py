"""Load every module that declares where its records keep a version identity.

A declaration registers when its owner module is imported. That makes the
declared table depend on which modules a caller happened to import, which is
not a property anything may rely on: a command that never imported
``archflow.project.issue`` would find ``PromotionDecision@1`` undeclared and
refuse every project that ever promoted a candidate.

So this module names the owners, once, and importing it loads them all. Every
reader of the table imports this rather than trusting an incidental import.
Adding an owner means adding it here; :mod:`tests.test_version_refs` checks the
table against the record-kind registry, so an owner that is written but not
loaded is a test failure rather than a migration that quietly refuses.

It sits above the modules it loads on purpose and holds no logic of its own.

It loads the shared core's owners only. A record kind owned by a workflow
module (``monkeydiagram``, ``monkeyarch``) declares itself the same way, but
the core may not import a workflow, so in a core-only process that kind stays
undeclared - and a project retaining one is refused with a named blocker
rather than migrated on a guess. A caller that has the workflow loaded gets
the declaration; that is the boundary, stated rather than worked around.
"""

from __future__ import annotations

# Imported for the declaration each one registers; none of these names is used.
import archflow.adapters.cad_execution  # noqa: F401
import archflow.project.issue  # noqa: F401
import archflow.project.refs  # noqa: F401
import archflow.state.developed_design  # noqa: F401
import archflow.state.spatial  # noqa: F401
import archflow.state.stage_workflow  # noqa: F401
import archflow.state.state_record  # noqa: F401

OWNER_MODULES = (
    "archflow.adapters.cad_execution",
    "archflow.project.issue",
    "archflow.project.refs",
    "archflow.project.repository",
    "archflow.state.developed_design",
    "archflow.state.spatial",
    "archflow.state.stage_workflow",
    "archflow.state.state_record",
)
