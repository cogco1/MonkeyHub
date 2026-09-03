"""P064 inverse-derivation contract tests: parser, quarantine, fidelity."""

import gzip
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from archive.archflow.evaluation.inverse_derivation import (
    CoarseMassing,
    InverseDerivationError,
    OccupancyGrid,
    iou,
    load_sponge_schematic,
)

ROOT = Path(__file__).resolve().parents[2]


def _tag(kind: int, name: str, payload: bytes) -> bytes:
    encoded = name.encode()
    return bytes([kind]) + struct.pack(">H", len(encoded)) + encoded + payload


def _tiny_schematic() -> bytes:
    """A 2x1x2 Sponge v2 region: one stone cell at (0,0,0), rest air."""

    palette = (
        _tag(3, "minecraft:air", struct.pack(">i", 0))
        + _tag(3, "minecraft:stone", struct.pack(">i", 1))
        + b"\x00"
    )
    block_data = bytes([1, 0, 0, 0])
    body = (
        _tag(3, "Version", struct.pack(">i", 2))
        + _tag(2, "Width", struct.pack(">h", 2))
        + _tag(2, "Height", struct.pack(">h", 1))
        + _tag(2, "Length", struct.pack(">h", 2))
        + _tag(10, "Palette", palette)
        + _tag(7, "BlockData", struct.pack(">i", len(block_data)) + block_data)
        + b"\x00"
    )
    return gzip.compress(_tag(10, "Schematic", body))


class InverseDerivationContractTests(unittest.TestCase):
    def test_parser_rejects_digest_drift_fail_closed(self):
        raw = _tiny_schematic()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.schem"
            path.write_bytes(raw)
            with self.assertRaises(InverseDerivationError):
                load_sponge_schematic(path, expected_sha256="0" * 64)

    def test_parser_reads_occupancy_deterministically(self):
        raw = _tiny_schematic()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.schem"
            path.write_bytes(raw)
            grid = load_sponge_schematic(path, expected_sha256=digest)
        self.assertEqual(1, grid.solid_count)
        self.assertEqual(frozenset({(0, 0, 0)}), grid.solid)
        self.assertEqual((1, 1, 1), grid.bounding_box())

    def test_iou_denominator_is_the_union(self):
        a = frozenset({(0, 0), (1, 0)})
        b = frozenset({(1, 0), (2, 0)})
        self.assertEqual(round(1 / 3, 6), iou(a, b))
        self.assertEqual(0.0, iou(frozenset(), frozenset()))

    def test_coarse_massing_uses_transcribed_numbers_only(self):
        massing = CoarseMassing(
            width=98, length=127, height=94,
            drum_span=94.0, rise_over_span=0.5, oculus_over_span=0.188,
        )
        footprint = massing.footprint()
        self.assertTrue(footprint)
        self.assertTrue(all(0 <= x < 98 and 0 <= z < 127 for x, z in footprint))

    def test_module_holds_the_quarantine_boundary(self):
        source = (
            ROOT / "archive" / "archive" / "archflow" / "evaluation" / "inverse_derivation.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("ARCHFLOW_V3", source)
        self.assertNotIn("import archflow_v3", source)

    def test_retained_receipt_denies_upgraded_claims(self):
        records = (
            ROOT
            / "probes"
            / "p064-pantheon-inverse"
            / "runs"
            / "inverse-001"
            / "records"
        )
        if not records.exists():
            self.skipTest("P064 evidence not yet promoted")
        receipts = sorted(records.glob("coarse-fidelity-receipt-*.json"))
        self.assertTrue(receipts)
        payload = json.loads(receipts[-1].read_text(encoding="utf-8"))
        boundary = payload["claim_boundary"]
        self.assertFalse(boundary["byte_replay_claimed"])
        self.assertFalse(boundary["full_fidelity_claimed"])
        self.assertFalse(boundary["v3_execution_claimed"])
        self.assertTrue(boundary["coarse_massing_only"])
        self.assertTrue(payload["metrics"]["solid_cell_count_matches_manifest"])

    def test_transcription_citations_are_complete(self):
        records = (
            ROOT
            / "probes"
            / "p064-pantheon-inverse"
            / "runs"
            / "inverse-001"
            / "records"
        )
        if not records.exists():
            self.skipTest("P064 evidence not yet promoted")
        for pattern in (
            "transcribed-component-tree-*.json",
            "transcribed-decision-sequence-*.json",
            "transcribed-dependency-edges-*.json",
        ):
            payload = json.loads(
                sorted(records.glob(pattern))[-1].read_text(encoding="utf-8")
            )
            rows = (
                payload.get("components")
                or payload.get("decisions")
                or payload.get("edges")
            )
            self.assertTrue(rows)
            for row in rows:
                self.assertTrue(row.get("cites"), row)


if __name__ == "__main__":
    unittest.main()
