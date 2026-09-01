"""M088: the viewer's schema key sets come from their definition sites."""

from __future__ import annotations

import unittest

from archflow.capabilities.stage_evidence_pack import (
    STAGE_EVIDENCE_PACK_KEYS,
    StageEvidencePack,
)
from tools import state_tree_viewer as viewer
from tools.build_pantheon_progress_snapshot import (
    PANTHEON_STAGE_PROGRESS_SNAPSHOT_KEYS,
)


class ViewerKeySingleSourceTest(unittest.TestCase):
    def test_stage_pack_keys_are_the_record_contract(self) -> None:
        self.assertIs(viewer._STAGE_PACK_KEYS, STAGE_EVIDENCE_PACK_KEYS)
        self.assertIs(STAGE_EVIDENCE_PACK_KEYS, StageEvidencePack.RECORD_KEYS)
        self.assertIn("program_digest", STAGE_EVIDENCE_PACK_KEYS)

    def test_pantheon_snapshot_keys_are_the_builder_contract(self) -> None:
        self.assertIs(
            viewer._PANTHEON_SNAPSHOT_KEYS,
            PANTHEON_STAGE_PROGRESS_SNAPSHOT_KEYS,
        )
        self.assertIn("formal_closure", PANTHEON_STAGE_PROGRESS_SNAPSHOT_KEYS)

    def test_panel_snapshot_contract_stays_viewer_owned(self) -> None:
        self.assertIn("three_dm_inspection", viewer._PANEL_SNAPSHOT_KEYS)
        self.assertIn("stage_pack_digest", viewer._PANEL_SNAPSHOT_KEYS)


if __name__ == "__main__":
    unittest.main()
