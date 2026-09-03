from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from archive.archflow.adapters.site_observation import (
    SiteObservationAuthorization,
    SiteObservationError,
    SiteObservationErrorCode,
    authorize_site_observation,
)
from archive.archflow.compilers.site import (
    SiteCompilationError,
    compile_site_context,
)
from archflow.project.refs import ProjectVersionRef
from archive.archflow.compilers.brief import compile_design_brief
from archflow.state.spatial import SiteBounds
from archive.archflow.state.site_context import GroundModelKind, SiteContext


_FIXTURES = Path(__file__).parent / "fixtures" / "site"


def _payload(name: str) -> dict[str, object]:
    return json.loads(
        (_FIXTURES / name).read_text(encoding="utf-8")
    )


def _base(project_id: str, digest: str) -> ProjectVersionRef:
    return ProjectVersionRef(
        project_id=project_id,
        version=0,
        state_sha256=digest,
    )


def _brief(
    project_id: str,
    digest: str,
    *,
    run_id: str = "site-001",
):
    return compile_design_brief(
        project_id=project_id,
        run_id=run_id,
        base=_base(project_id, digest),
        raw_request_ref=f"project://{project_id}/input/raw-request.json",
    ).brief


def _authorization(
    *,
    project_id: str,
    digest: str,
    world_id: str,
    envelope: SiteBounds,
    anchor: tuple[int, int, int],
) -> SiteObservationAuthorization:
    return SiteObservationAuthorization(
        project_id=project_id,
        run_id="site-001",
        base=_base(project_id, digest),
        world_id=world_id,
        dimension_id="minecraft:overworld",
        authorized_envelope=envelope,
        anchor=anchor,
        authority_id="authority.user",
        authorization_ref=(
            f"project://{project_id}/input/site-authorization.json"
        ),
    )


def _flat_authorization(
    project_id: str = "site-flat",
    *,
    digest: str = "a" * 64,
    world_id: str = "fixture-flat-world",
) -> SiteObservationAuthorization:
    return _authorization(
        project_id=project_id,
        digest=digest,
        world_id=world_id,
        envelope=SiteBounds(
            minimum=(0, 60, 0),
            maximum=(31, 90, 31),
        ),
        anchor=(16, 65, 16),
    )


class SiteContextTests(unittest.TestCase):
    def test_authorized_superflat_context_is_explicit_and_read_only(self) -> None:
        payload = _payload("authorized_superflat.json")
        authorization = _flat_authorization()
        observation = authorize_site_observation(
            payload,
            authorization=authorization,
        )
        result = compile_site_context(
            brief=_brief("site-flat", "a" * 64),
            observation=observation,
        )
        context = result.context

        self.assertIs(
            context.ground_model.kind,
            GroundModelKind.SUPERFLAT,
        )
        self.assertEqual(
            context.authorized_envelope,
            context.observed_envelope,
        )
        self.assertEqual(context.anchor, (16, 65, 16))
        self.assertEqual(context.obligations, ())
        self.assertEqual(
            result.receipt.observation_digest,
            observation.observation_digest,
        )
        self.assertEqual(
            SiteContext.from_dict(context.to_dict()),
            context,
        )
        for field in (
            "generation_authority",
            "world_write_authority",
        ):
            self.assertFalse(context.to_dict()[field])
        self.assertEqual(context.to_dict()["schema"], "SiteContext@2")

    def test_unknown_and_uneven_context_create_obligations_not_answers(
        self,
    ) -> None:
        payload = _payload("uneven_unknown.json")
        authorization = _authorization(
            project_id="site-uneven",
            digest="b" * 64,
            world_id="fixture-uneven-world",
            envelope=SiteBounds(
                minimum=(-16, 50, -16),
                maximum=(16, 100, 16),
            ),
            anchor=(0, 72, 0),
        )
        observation = authorize_site_observation(
            payload,
            authorization=authorization,
        )
        context = compile_site_context(
            brief=_brief("site-uneven", "b" * 64),
            observation=observation,
        ).context
        obligation_ids = {
            item.obligation_id for item in context.obligations
        }

        self.assertIs(
            context.ground_model.kind,
            GroundModelKind.UNEVEN,
        )
        self.assertEqual(
            context.ground_model.elevation_range,
            (68, 76),
        )
        self.assertIn(
            "resolve.site.ground-response",
            obligation_ids,
        )
        self.assertIn(
            "resolve.site.approach-continuity",
            obligation_ids,
        )
        self.assertIn(
            "resolve.site.ground.ground-coverage",
            obligation_ids,
        )
        self.assertIn(
            "resolve.site.observation-coverage",
            obligation_ids,
        )
        self.assertFalse(
            any(
                key.endswith("_selected")
                for key in context.to_dict()
            )
        )

        unknown_payload = deepcopy(payload)
        unknown_payload["ground_model"] = {
            "kind": "unknown",
            "samples": [],
            "source_refs": payload["source_refs"],
        }
        unknown = authorize_site_observation(
            unknown_payload,
            authorization=authorization,
        )
        unknown_context = compile_site_context(
            brief=_brief("site-uneven", "b" * 64),
            observation=unknown,
        ).context
        self.assertIs(
            unknown_context.ground_model.kind,
            GroundModelKind.UNKNOWN,
        )
        self.assertIn(
            "resolve.site.ground-evidence",
            {
                item.obligation_id
                for item in unknown_context.obligations
            },
        )

    def test_protected_cells_remain_a_constraint_on_later_design(self) -> None:
        payload = _payload("protected_envelope.json")
        authorization = _flat_authorization(
            "site-protected",
            digest="c" * 64,
            world_id="fixture-protected-world",
        )
        observation = authorize_site_observation(
            payload,
            authorization=authorization,
        )
        context = compile_site_context(
            brief=_brief("site-protected", "c" * 64),
            observation=observation,
        ).context

        self.assertEqual(
            context.protected_cells,
            ((8, 64, 8), (9, 64, 8)),
        )
        self.assertIn(
            "preserve.site.protected-cells",
            {
                item.obligation_id for item in context.obligations
            },
        )
        self.assertFalse(context.to_dict()["world_write_authority"])

    def test_cross_world_dimension_stale_and_unauthorized_evidence_reject(
        self,
    ) -> None:
        payload = _payload("authorized_superflat.json")
        authorization = _flat_authorization()
        cases = (
            (
                "world_id",
                "another-world",
                SiteObservationErrorCode.CROSS_WORLD,
            ),
            (
                "dimension_id",
                "minecraft:the_nether",
                SiteObservationErrorCode.CROSS_DIMENSION,
            ),
            (
                "base_state_sha256",
                "f" * 64,
                SiteObservationErrorCode.STALE_BASE,
            ),
        )
        for field, value, code in cases:
            with self.subTest(field=field):
                changed = deepcopy(payload)
                changed[field] = value
                with self.assertRaises(SiteObservationError) as raised:
                    authorize_site_observation(
                        changed,
                        authorization=authorization,
                    )
                self.assertIs(raised.exception.code, code)

        outside = deepcopy(payload)
        outside["observed_envelope"]["maximum"] = [40, 90, 31]
        with self.assertRaises(SiteObservationError) as raised:
            authorize_site_observation(
                outside,
                authorization=authorization,
            )
        self.assertIs(
            raised.exception.code,
            SiteObservationErrorCode.UNAUTHORIZED_ENVELOPE,
        )

    def test_compiler_rejects_stale_brief_and_worlds_remain_isolated(
        self,
    ) -> None:
        flat = authorize_site_observation(
            _payload("authorized_superflat.json"),
            authorization=_flat_authorization(),
        )
        uneven = authorize_site_observation(
            _payload("uneven_unknown.json"),
            authorization=_authorization(
                project_id="site-uneven",
                digest="b" * 64,
                world_id="fixture-uneven-world",
                envelope=SiteBounds(
                    minimum=(-16, 50, -16),
                    maximum=(16, 100, 16),
                ),
                anchor=(0, 72, 0),
            ),
        )
        flat_context = compile_site_context(
            brief=_brief("site-flat", "a" * 64),
            observation=flat,
        ).context
        uneven_context = compile_site_context(
            brief=_brief("site-uneven", "b" * 64),
            observation=uneven,
        ).context

        self.assertNotEqual(
            flat_context.world_id,
            uneven_context.world_id,
        )
        self.assertNotEqual(
            flat_context.observation_digest,
            uneven_context.observation_digest,
        )
        with self.assertRaises(SiteCompilationError):
            compile_site_context(
                brief=_brief("site-flat", "d" * 64),
                observation=flat,
            )


if __name__ == "__main__":
    unittest.main()
