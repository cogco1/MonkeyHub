"""Explicit external runtime roots for active ArchFlow projects.

The runtime environment selects where a project repository lives.  It does not
replace :class:`FilesystemProjectRepository`, create a second canonical state,
or persist machine paths into project identity.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from archflow.project.bootstrap import (
    ProjectBootstrapResult,
    bootstrap_raw_request_project,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.refs import require_identifier


RUNTIME_CONFIG_SCHEMA = "ArchFlowRuntimeConfig@1"


class RuntimeConfigError(ValueError):
    """An external runtime configuration is missing or unsafe."""


def _resolved_absolute(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeConfigError(f"{field} must be a non-empty absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeConfigError(f"{field} must be an absolute path")
    return path.resolve(strict=False)


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Resolved roots for project documents, rebuildable cache, and temp data."""

    workspace_root: Path
    cache_root: Path
    temp_root: Path

    def __post_init__(self) -> None:
        roots = {
            "workspace_root": self.workspace_root,
            "cache_root": self.cache_root,
            "temp_root": self.temp_root,
        }
        resolved: dict[str, Path] = {}
        for name, value in roots.items():
            if not isinstance(value, Path):
                raise TypeError(f"{name} must be a Path")
            if not value.is_absolute():
                raise RuntimeConfigError(f"{name} must be an absolute path")
            resolved[name] = value.resolve(strict=False)
            object.__setattr__(self, name, resolved[name])

        names = tuple(resolved)
        for index, left_name in enumerate(names):
            for right_name in names[index + 1 :]:
                left = resolved[left_name]
                right = resolved[right_name]
                if _contains(left, right) or _contains(right, left):
                    raise RuntimeConfigError(
                        f"{left_name} and {right_name} must be distinct, "
                        "non-overlapping roots"
                    )

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        repository_root: Path | None = None,
    ) -> RuntimePaths:
        if not isinstance(payload, Mapping):
            raise RuntimeConfigError("runtime config must be an object")
        expected = {"schema", "workspace_root", "cache_root", "temp_root"}
        if set(payload) != expected:
            missing = sorted(expected - set(payload))
            extra = sorted(set(payload) - expected)
            raise RuntimeConfigError(
                f"runtime config fields mismatch; missing={missing}, extra={extra}"
            )
        if payload.get("schema") != RUNTIME_CONFIG_SCHEMA:
            raise RuntimeConfigError("unsupported runtime config schema")
        result = cls(
            workspace_root=_resolved_absolute(
                payload["workspace_root"], field="workspace_root"
            ),
            cache_root=_resolved_absolute(payload["cache_root"], field="cache_root"),
            temp_root=_resolved_absolute(payload["temp_root"], field="temp_root"),
        )
        if repository_root is not None:
            result.require_external_to(repository_root)
        return result

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": RUNTIME_CONFIG_SCHEMA,
            "workspace_root": self.workspace_root.as_posix(),
            "cache_root": self.cache_root.as_posix(),
            "temp_root": self.temp_root.as_posix(),
        }

    def require_external_to(self, repository_root: Path) -> None:
        source = repository_root.resolve(strict=False)
        for name, root in (
            ("workspace_root", self.workspace_root),
            ("cache_root", self.cache_root),
            ("temp_root", self.temp_root),
        ):
            if _contains(source, root) or _contains(root, source):
                raise RuntimeConfigError(
                    f"{name} must not overlap the source checkout"
                )

    @property
    def projects_root(self) -> Path:
        return self.workspace_root / "projects"

    def project(self, project_id: str) -> Path:
        require_identifier(project_id, "project_id")
        return (self.projects_root / project_id).resolve(strict=False)

    def cache(self, project_id: str, run_id: str | None = None) -> Path:
        require_identifier(project_id, "project_id")
        root = self.cache_root / "projects" / project_id
        if run_id is not None:
            require_identifier(run_id, "run_id")
            root = root / "runs" / run_id
        return root.resolve(strict=False)

    def temporary(self, project_id: str, run_id: str | None = None) -> Path:
        require_identifier(project_id, "project_id")
        root = self.temp_root / "projects" / project_id
        if run_id is not None:
            require_identifier(run_id, "run_id")
            root = root / "runs" / run_id
        return root.resolve(strict=False)


@dataclass(frozen=True, slots=True)
class RuntimeInitializationReceipt:
    schema: str
    workspace_root: str
    projects_root: str
    cache_root: str
    temp_root: str
    canonical_authority: str = "FilesystemProjectRepository"
    durable_cloud_storage_claimed: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "schema": self.schema,
            "workspace_root": self.workspace_root,
            "projects_root": self.projects_root,
            "cache_root": self.cache_root,
            "temp_root": self.temp_root,
            "canonical_authority": self.canonical_authority,
            "durable_cloud_storage_claimed": self.durable_cloud_storage_claimed,
        }


def load_runtime_config(
    path: Path,
    *,
    repository_root: Path | None = None,
) -> RuntimePaths:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeConfigError(f"cannot read runtime config: {path}") from exc
    return RuntimePaths.from_dict(payload, repository_root=repository_root)


def initialize_runtime(paths: RuntimePaths) -> RuntimeInitializationReceipt:
    """Create only the assigned external runtime roots.

    Project directories are deliberately absent until the P036 repository is
    initialized for a named project.
    """

    paths.projects_root.mkdir(parents=True, exist_ok=True)
    paths.cache_root.mkdir(parents=True, exist_ok=True)
    paths.temp_root.mkdir(parents=True, exist_ok=True)
    return RuntimeInitializationReceipt(
        schema="ArchFlowRuntimeInitialization@1",
        workspace_root=paths.workspace_root.as_posix(),
        projects_root=paths.projects_root.as_posix(),
        cache_root=paths.cache_root.as_posix(),
        temp_root=paths.temp_root.as_posix(),
    )


def bootstrap_external_project(
    paths: RuntimePaths,
    *,
    project_id: str,
    prompt: str,
    run_id: str = "bootstrap-001",
) -> ProjectBootstrapResult:
    """Bootstrap through P036 at the configured external project root."""

    initialize_runtime(paths)
    return bootstrap_raw_request_project(
        paths.project(project_id),
        project_id=project_id,
        prompt=prompt,
        run_id=run_id,
        synthetic_test=False,
    )


def run_external_production(
    paths: RuntimePaths,
    *,
    project_id: str,
    prompt: str,
    run_id: str,
    context_path: Path | None,
    agent_executable: str,
    model_id: str,
    provider_version: str,
    timeout_seconds: float,
):
    """Start or resume the formal P053/P036 root production path."""

    from archflow.capabilities.geometry_proposal import (
        GeometryProposalProviderIdentity,
    )
    from archflow.production import (
        InvocationEvidenceCollector,
        activate_codex_agent_cli_provider,
    )
    from archflow.runtime.production_compiler import ProductionRootCompiler
    from archflow.runtime.production_runtime import (
        ProductionAuthoringContext,
        run_or_resume_production_step,
    )

    root = paths.project(project_id)
    if (root / "project.json").exists():
        repository = FilesystemProjectRepository.open(root)
        run = repository.load_run(run_id)
        request = _find_raw_request(repository, run, prompt)
    else:
        bootstrapped = bootstrap_external_project(
            paths,
            project_id=project_id,
            prompt=prompt,
            run_id=run_id,
        )
        repository = FilesystemProjectRepository.open(root)
        run = bootstrapped.run
        request = bootstrapped.request
    context_ref, context = _production_context(
        repository,
        run,
        context_path=context_path,
        context_type=ProductionAuthoringContext,
    )
    collector = InvocationEvidenceCollector()
    provider = activate_codex_agent_cli_provider(
        executable=agent_executable,
        model_id=model_id,
        version=provider_version,
        responsibility_id="model.production-root",
        contract_owner_id="archflow.production-root",
        verification_evidence_refs=(context_ref.uri,),
        timeout_seconds=timeout_seconds,
        envelope_observer=collector.observe,
    )
    active = provider.router.state("model.production-root").active_provider
    if active is None:
        raise RuntimeConfigError("configured production provider is not active")
    compiler = ProductionRootCompiler(
        repository=repository,
        context_ref=context_ref,
        context=context,
        provider=provider,
        evidence_collector=collector,
        geometry_provider_identity=GeometryProposalProviderIdentity(
            provider_id=active.provider_id,
            model_id=model_id,
            provider_version=active.version,
            provider_fingerprint=active.fingerprint,
        ),
    )
    return asyncio.run(
        run_or_resume_production_step(
            repository,
            run=run,
            raw_request=request,
            prompt=prompt,
            step_id="initial-semantic-geometry",
            compiler=compiler,
        )
    )


def _find_raw_request(repository, run, prompt: str):  # type: ignore[no-untyped-def]
    matches = []
    for ref in repository.list_json(
        run=run,
        destination=PersistenceDestination(PersistenceArea.INPUT),
    ):
        payload = repository.load_json(ref)
        if payload == {"schema": "RawProjectRequest@1", "prompt": prompt}:
            matches.append(ref)
    if len(matches) != 1:
        raise RuntimeConfigError(
            "existing run requires exactly one immutable raw request matching prompt"
        )
    return matches[0]


def _production_context(
    repository,
    run,
    *,
    context_path: Path | None,
    context_type,
):  # type: ignore[no-untyped-def]
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    if context_path is not None:
        try:
            payload = json.loads(context_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeConfigError(
                f"cannot read production context: {context_path}"
            ) from exc
        context = context_type.from_dict(payload)
        context.require_run(run)
        ref = repository.put_json(
            run=run,
            destination=destination,
            record_kind="production-authoring-context",
            payload=context.to_dict(),
        )
        return ref, context
    matches = []
    for ref in repository.list_json(run=run, destination=destination):
        payload = repository.load_json(ref)
        if payload.get("schema") == context_type.SCHEMA:
            matches.append((ref, context_type.from_dict(payload)))
    if len(matches) != 1:
        raise RuntimeConfigError(
            "run/resume requires exactly one P036 production authoring context"
        )
    matches[0][1].require_run(run)
    return matches[0]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize an explicit external ArchFlow runtime."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    project = subparsers.add_parser("bootstrap-project")
    project.add_argument("--project-id", required=True)
    project.add_argument("--prompt", required=True)
    project.add_argument("--run-id", default="bootstrap-001")
    production = subparsers.add_parser("run-project")
    production.add_argument("--project-id", required=True)
    production.add_argument("--prompt", required=True)
    production.add_argument("--run-id", default="production-001")
    production.add_argument("--context", type=Path)
    production.add_argument("--agent-executable", default="codex")
    production.add_argument("--model", required=True)
    production.add_argument("--provider-version", default="agent-cli")
    production.add_argument("--timeout-seconds", type=float, default=60.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = load_runtime_config(
        args.config,
        repository_root=args.repository_root,
    )
    if args.command == "init":
        payload: Mapping[str, Any] = initialize_runtime(paths).to_dict()
    elif args.command == "bootstrap-project":
        result = bootstrap_external_project(
            paths,
            project_id=args.project_id,
            prompt=args.prompt,
            run_id=args.run_id,
        )
        payload = {
            "schema": "ArchFlowExternalProjectBootstrap@1",
            "project_id": result.project_id,
            "run_id": result.run.run_id,
            "project_root": paths.project(result.project_id).as_posix(),
            "head": {
                "version": result.head.version,
                "state_sha256": result.head.require_digest(),
            },
            "canonical_authority": "FilesystemProjectRepository",
        }
    else:
        result = run_external_production(
            paths,
            project_id=args.project_id,
            prompt=args.prompt,
            run_id=args.run_id,
            context_path=args.context,
            agent_executable=args.agent_executable,
            model_id=args.model,
            provider_version=args.provider_version,
            timeout_seconds=args.timeout_seconds,
        )
        payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
