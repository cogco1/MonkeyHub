"""P088: single-source authority blocks and the inline-literal ratchet."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from archflow.contracts.authority import (
    DEFAULT_AUTHORITY_FIELDS,
    AuthorityContractError,
    no_authority,
    require_no_authority,
)

_ROOT = Path(__file__).resolve().parents[1]

# Inline authority-flag literals counted in production code (archflow/ +
# tools/) on 2026-09-01 when the helper landed. New code must use
# archflow.contracts.authority.no_authority(); this ratchet only ever
# goes down.
_INLINE_AUTHORITY_BASELINE = 782
_INLINE_AUTHORITY_RE = re.compile(
    r'"[a-z0-9_]*_authority"\s*:\s*(False|True)'
)


class NoAuthorityTest(unittest.TestCase):
    def test_default_block_is_all_false_and_sorted(self) -> None:
        block = no_authority()
        self.assertEqual(tuple(block.keys()), DEFAULT_AUTHORITY_FIELDS)
        self.assertEqual(
            tuple(block.keys()), tuple(sorted(block.keys()))
        )
        self.assertTrue(all(value is False for value in block.values()))

    def test_custom_fields_are_sorted_and_validated(self) -> None:
        block = no_authority(["view_authority", "evidence_authority"])
        self.assertEqual(
            list(block.keys()), ["evidence_authority", "view_authority"]
        )

    def test_invalid_names_fail_closed(self) -> None:
        for bad in (
            ["authority"],
            ["Design_authority"],
            ["design-authority"],
            ["design_authority", "design_authority"],
            [],
            [42],
        ):
            with self.assertRaises(AuthorityContractError):
                no_authority(bad)  # type: ignore[arg-type]


class RequireNoAuthorityTest(unittest.TestCase):
    def test_helper_output_passes(self) -> None:
        require_no_authority(no_authority())

    def test_true_flag_fails(self) -> None:
        payload = no_authority()
        payload["canonical_write_authority"] = True
        with self.assertRaises(AuthorityContractError):
            require_no_authority(payload)

    def test_falsy_substitute_fails(self) -> None:
        payload = no_authority()
        payload["design_authority"] = 0
        with self.assertRaises(AuthorityContractError):
            require_no_authority(payload)

    def test_missing_flag_fails(self) -> None:
        payload = no_authority()
        del payload["promotion_authority"]
        with self.assertRaises(AuthorityContractError):
            require_no_authority(payload)

    def test_non_mapping_fails(self) -> None:
        with self.assertRaises(AuthorityContractError):
            require_no_authority(None)


class InlineAuthorityRatchetTest(unittest.TestCase):
    def test_inline_literals_do_not_grow(self) -> None:
        count = 0
        for base in ("archflow", "tools"):
            for path in sorted((_ROOT / base).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                count += len(
                    _INLINE_AUTHORITY_RE.findall(
                        path.read_text(encoding="utf-8")
                    )
                )
        self.assertGreater(count, 0, "ratchet scan found no code to scan")
        self.assertLessEqual(
            count,
            _INLINE_AUTHORITY_BASELINE,
            "new inline authority-flag literals detected; use "
            "archflow.contracts.authority.no_authority() instead and, if a "
            "literal was removed elsewhere, lower the baseline",
        )


if __name__ == "__main__":
    unittest.main()
