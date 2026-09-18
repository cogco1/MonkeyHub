from __future__ import annotations

import unittest

from archflow.contracts.canonical import canonical_digest

from monkeycontrol.contract import RECEIPT_SCHEMA, validate_action
from monkeycontrol.trace import ResolvedTarget, WindowInfo, build_receipt

WINDOW = WindowInfo(197002, "Untitled - Notepad", 4242, "notepad", (0, 0, 800, 600))
TARGET = ResolvedTarget(
    "Button", "Save", "1001", "Button", (10, 20, 30, 40), "42.7.1", "windows-uia"
)
CLICK = {
    "step_id": "s-0001",
    "intent": "save the file",
    "application": "notepad",
    "target": {"controlType": "Button", "name": "Save"},
    "action": {"type": "click"},
}
DOCUMENTED_KEYS = [
    "action",
    "application",
    "backend",
    "digest",
    "execution",
    "fallback",
    "intent",
    "mode",
    "refusal",
    "schema",
    "screenshots",
    "status",
    "step_id",
    "target",
    "verification",
    "window",
]


def receipt(payload: dict | None = None, **overrides):
    arguments = {
        "action": validate_action(payload or CLICK),
        "step_id": "s-0001",
        "mode": "demo",
        "window": WINDOW,
        "target": TARGET,
        "fallback": False,
        "point": (20, 30),
        "started_at": "2026-09-18T09:30:00Z",
        "duration_ms": 120,
        "verification": {
            "expect": "window",
            "status": "passed",
            "detail": "Save As matched",
            "duration_ms": 30,
        },
        "status": "succeeded",
        "refusal": None,
        "screenshots": {"before": "shots/aa.png", "after": "shots/bb.png"},
    }
    arguments.update(overrides)
    return build_receipt(**arguments)


class ResolvedTargetTests(unittest.TestCase):
    def test_center_is_the_middle_of_the_bounds(self) -> None:
        self.assertEqual(TARGET.center, (20, 30))


class BuildReceiptTests(unittest.TestCase):
    def test_the_documented_keys_are_all_present(self) -> None:
        built = receipt()
        self.assertEqual(sorted(built), DOCUMENTED_KEYS)
        self.assertEqual(built["schema"], RECEIPT_SCHEMA)
        self.assertEqual(built["step_id"], "s-0001")
        self.assertEqual(built["intent"], "save the file")
        self.assertEqual(built["application"], "notepad")
        self.assertEqual(built["mode"], "demo")
        self.assertEqual(built["status"], "succeeded")
        self.assertIsNone(built["refusal"])
        self.assertEqual(
            built["execution"],
            {"point": [20, 30], "started_at": "2026-09-18T09:30:00Z", "duration_ms": 120},
        )
        self.assertEqual(
            built["screenshots"], {"before": "shots/aa.png", "after": "shots/bb.png"}
        )
        self.assertEqual(built["verification"]["status"], "passed")

    def test_the_digest_covers_the_receipt_without_itself(self) -> None:
        built = receipt()
        without_digest = {k: v for k, v in built.items() if k != "digest"}
        self.assertEqual(built["digest"], canonical_digest(without_digest))

    def test_the_window_and_both_target_views_are_recorded(self) -> None:
        built = receipt()
        self.assertEqual(
            built["window"],
            {
                "handle": 197002,
                "title": "Untitled - Notepad",
                "pid": 4242,
                "process": "notepad",
                "bounds": [0, 0, 800, 600],
            },
        )
        self.assertEqual(
            built["target"]["requested"], {"controlType": "Button", "name": "Save"}
        )
        self.assertEqual(
            built["target"]["resolved"],
            {
                "controlType": "Button",
                "name": "Save",
                "automationId": "1001",
                "className": "Button",
                "bounds": [10, 20, 30, 40],
                "runtimeId": "42.7.1",
                "backend": "windows-uia",
            },
        )
        self.assertEqual(built["backend"], "windows-uia")
        self.assertIs(built["fallback"], False)

    def test_without_a_resolution_the_backend_is_none(self) -> None:
        built = receipt(
            target=None,
            window=None,
            point=None,
            status="refused",
            refusal={"code": "TARGET_UNRESOLVED", "message": "no Button named Save"},
            verification=None,
        )
        self.assertEqual(built["backend"], "none")
        self.assertIsNone(built["window"])
        self.assertIsNone(built["target"]["resolved"])
        self.assertIsNone(built["execution"]["point"])
        self.assertIsNone(built["verification"])
        self.assertEqual(built["refusal"]["code"], "TARGET_UNRESOLVED")

    def test_the_fallback_flag_is_carried(self) -> None:
        self.assertIs(receipt(fallback=True)["fallback"], True)

    def test_sensitive_text_never_reaches_the_receipt(self) -> None:
        built = receipt(
            {
                **CLICK,
                "action": {"type": "type", "text": "s3cret", "sensitive": True},
            }
        )
        self.assertEqual(built["action"]["text"], "<redacted 6 chars>")
        self.assertNotIn("s3cret", str(built))

    def test_an_unknown_status_or_refusal_code_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            receipt(status="fine")
        with self.assertRaises(ValueError):
            receipt(status="refused", refusal={"code": "NOPE", "message": "no"})


if __name__ == "__main__":
    unittest.main()
