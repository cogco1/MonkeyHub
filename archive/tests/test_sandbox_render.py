from __future__ import annotations

import unittest

from archive.archflow.adapters.sandbox_render import (
    SandboxRenderPolicy,
    SandboxRenderSet,
    SandboxViewKind,
    render_paper_views,
)
from archive.archflow.realization.sandbox import realize_geometry
from tests.test_sandbox_realization import compiled_room


class SandboxRenderTests(unittest.TestCase):
    def test_five_views_are_deterministic_exact_scene_outputs(self) -> None:
        _, program, payloads = compiled_room(include_asset=True)
        realized = realize_geometry(
            program,
            workspace_id="sandbox-workspace",
            asset_payloads=payloads,
        )
        assert realized.scene is not None
        before = realized.scene.scene_digest
        policy = SandboxRenderPolicy(width=800, height=600)

        first = render_paper_views(realized.scene, policy=policy)
        second = render_paper_views(realized.scene, policy=policy)

        self.assertEqual(first, second)
        self.assertEqual(first.scene_digest, before)
        self.assertEqual(realized.scene.scene_digest, before)
        self.assertEqual(
            tuple(item.kind for item in first.views),
            tuple(SandboxViewKind),
        )
        self.assertEqual(len({item.svg_sha256 for item in first.views}), 5)
        self.assertTrue(
            all(
                f'data-scene-digest="{before}"' in item.svg_text
                for item in first.views
            )
        )
        self.assertTrue(
            all(
                item.to_dict()["repair_authority"] is False
                for item in first.views
            )
        )

    def test_render_set_reloads_and_detects_svg_drift(self) -> None:
        _, program, payloads = compiled_room()
        realized = realize_geometry(
            program,
            workspace_id="sandbox-workspace",
            asset_payloads=payloads,
        )
        assert realized.scene is not None
        rendered = render_paper_views(realized.scene)
        payload = rendered.to_dict()

        self.assertEqual(SandboxRenderSet.from_dict(payload), rendered)
        payload["views"][0]["svg_text"] += " "
        with self.assertRaisesRegex(ValueError, "SVG"):
            SandboxRenderSet.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
