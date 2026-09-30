from __future__ import annotations

import unittest

from monkeycontrol.contract import (
    ACTION_SCHEMA,
    ACTION_TYPES,
    EXPECTATIONS,
    RECEIPT_SCHEMA,
    REFUSALS,
    Action,
    ContractError,
    TargetSpec,
    Verification,
    action_payload,
    validate_action,
)

CLICK = {
    "intent": "open",
    "application": "notepad",
    "target": {"controlType": "Button", "name": "Save"},
    "action": {"type": "click"},
}


def without(key: str) -> dict:
    payload = dict(CLICK)
    payload.pop(key)
    return payload


class ContractConstantsTests(unittest.TestCase):
    def test_the_agreed_strings_are_frozen(self) -> None:
        self.assertEqual(ACTION_SCHEMA, "ComputerAction@1")
        self.assertEqual(RECEIPT_SCHEMA, "ComputerActionReceipt@1")
        self.assertEqual(
            ACTION_TYPES,
            (
                "launch", "click", "double_click", "right_click", "invoke",
                "set_value", "type", "keypress", "drag", "scroll", "wait",
                "screenshot", "highlight",
            ),
        )
        self.assertEqual(EXPECTATIONS, ("window", "element", "absent", "file"))
        self.assertEqual(
            REFUSALS,
            (
                "ACTION_INVALID", "APP_NOT_ALLOWED", "WINDOW_NOT_FOUND",
                "TARGET_UNRESOLVED", "TARGET_AMBIGUOUS", "FOCUS_LOST",
                "BACKEND_UNAVAILABLE", "HOST_ERROR", "VERIFY_FAILED",
                "RECORDING_ACTIVE", "RECORDING_NOT_ACTIVE",
            ),
        )


class ValidateActionTests(unittest.TestCase):
    def test_minimal_click_applies_the_documented_defaults(self) -> None:
        action = validate_action(CLICK)
        self.assertIsInstance(action, Action)
        self.assertEqual(action.type, "click")
        self.assertEqual(action.button, "left")
        self.assertIs(action.capture, False)
        self.assertIs(action.sensitive, False)
        self.assertIsNone(action.verification)
        self.assertIsNone(action.step_id)
        self.assertIsNone(action.window)
        self.assertEqual(action.intent, "open")
        self.assertEqual(action.application, "notepad")
        self.assertEqual(
            action.target, TargetSpec(control_type="Button", name="Save")
        )

    def test_application_drops_the_executable_suffix_and_case(self) -> None:
        self.assertEqual(
            validate_action({**CLICK, "application": "Notepad.EXE"}).application,
            "notepad",
        )

    def test_unknown_top_level_key_is_refused_by_name(self) -> None:
        with self.assertRaises(ContractError) as caught:
            validate_action({**CLICK, "speed": 3})
        self.assertEqual(caught.exception.code, "ACTION_INVALID")
        self.assertIn("speed", str(caught.exception))

    def test_unknown_action_type_is_refused_by_name(self) -> None:
        with self.assertRaises(ContractError) as caught:
            validate_action({**CLICK, "action": {"type": "teleport"}})
        self.assertEqual(caught.exception.code, "ACTION_INVALID")
        self.assertIn("action.type", str(caught.exception))
        self.assertIn("teleport", str(caught.exception))

    def test_bounds_need_the_visual_fallback_backend(self) -> None:
        with self.assertRaises(ContractError) as caught:
            validate_action({**CLICK, "target": {"bounds": [10, 20, 30, 40]}})
        self.assertIn("target.bounds", str(caught.exception))
        action = validate_action(
            {
                **CLICK,
                "target": {
                    "bounds": [10, 20, 30, 40],
                    "backend": "visual-fallback",
                },
            }
        )
        self.assertEqual(action.target.bounds, (10, 20, 30, 40))
        self.assertEqual(action.target.backend, "visual-fallback")

    def test_pointer_actions_need_a_target(self) -> None:
        with self.assertRaises(ContractError) as caught:
            validate_action(without("target"))
        self.assertIn("target", str(caught.exception))

    def test_keypress_needs_keys(self) -> None:
        payload = {**without("target"), "action": {"type": "keypress"}}
        with self.assertRaises(ContractError) as caught:
            validate_action(payload)
        self.assertIn("action.keys", str(caught.exception))
        self.assertEqual(
            validate_action(
                {**payload, "action": {"type": "keypress", "keys": "ctrl+s"}}
            ).keys,
            "ctrl+s",
        )

    def test_launch_needs_a_command(self) -> None:
        payload = {**without("target"), "action": {"type": "launch"}}
        with self.assertRaises(ContractError) as caught:
            validate_action(payload)
        self.assertIn("action.command", str(caught.exception))
        action = validate_action(
            {
                **payload,
                "action": {"type": "launch", "command": ["notepad.exe", "a.txt"]},
            }
        )
        self.assertEqual(action.command, ("notepad.exe", "a.txt"))

    def test_wait_needs_a_duration(self) -> None:
        payload = {**without("target"), "action": {"type": "wait"}}
        with self.assertRaises(ContractError) as caught:
            validate_action(payload)
        self.assertIn("action.ms", str(caught.exception))
        self.assertEqual(
            validate_action({**payload, "action": {"type": "wait", "ms": 250}}).ms,
            250,
        )

    def test_type_needs_text(self) -> None:
        with self.assertRaises(ContractError) as caught:
            validate_action({**CLICK, "action": {"type": "type"}})
        self.assertIn("action.text", str(caught.exception))

    def test_set_value_may_clear_a_field_but_typing_may_not(self) -> None:
        action = validate_action({**CLICK, "action": {"type": "set_value", "text": ""}})
        self.assertEqual(action.text, "")
        with self.assertRaises(ContractError) as caught:
            validate_action({**CLICK, "action": {"type": "type", "text": ""}})
        self.assertEqual(caught.exception.code, "ACTION_INVALID")
        self.assertIn("action.text", str(caught.exception))

    def test_drag_needs_a_destination(self) -> None:
        with self.assertRaises(ContractError) as caught:
            validate_action({**CLICK, "action": {"type": "drag"}})
        self.assertEqual(caught.exception.code, "ACTION_INVALID")
        self.assertIn("action.to", str(caught.exception))
        action = validate_action(
            {
                **CLICK,
                "action": {
                    "type": "drag",
                    "to": {"controlType": "ListItem", "name": "Drop here"},
                },
            }
        )
        self.assertEqual(
            action.to, TargetSpec(control_type="ListItem", name="Drop here")
        )

    def test_scroll_needs_a_non_zero_delta(self) -> None:
        payload = {**without("target"), "action": {"type": "scroll"}}
        with self.assertRaises(ContractError) as caught:
            validate_action(payload)
        self.assertEqual(caught.exception.code, "ACTION_INVALID")
        self.assertIn("action.delta", str(caught.exception))
        with self.assertRaises(ContractError) as caught:
            validate_action({**payload, "action": {"type": "scroll", "delta": 0}})
        self.assertIn("action.delta", str(caught.exception))
        self.assertEqual(
            validate_action({**payload, "action": {"type": "scroll", "delta": -3}}).delta,
            -3,
        )

    def test_window_and_name_regex_must_compile(self) -> None:
        with self.assertRaises(ContractError) as caught:
            validate_action({**CLICK, "window": "Save ("})
        self.assertIn("window", str(caught.exception))
        with self.assertRaises(ContractError) as caught:
            validate_action({**CLICK, "target": {"nameRegex": "[unclosed"}})
        self.assertIn("target.nameRegex", str(caught.exception))

    def test_verification_timeout_default_and_ceiling(self) -> None:
        action = validate_action(
            {**CLICK, "verification": {"expect": "window", "title": "Save As"}}
        )
        self.assertIsInstance(action.verification, Verification)
        self.assertEqual(action.verification.expect, "window")
        self.assertEqual(action.verification.title, "Save As")
        self.assertEqual(action.verification.timeout_ms, 5000)
        self.assertEqual(action.verification.state, {})
        with self.assertRaises(ContractError) as caught:
            validate_action(
                {
                    **CLICK,
                    "verification": {
                        "expect": "window",
                        "title": "Save As",
                        "timeout_ms": 60001,
                    },
                }
            )
        self.assertIn("verification.timeout_ms", str(caught.exception))

    def test_verification_expectation_and_state_are_closed_sets(self) -> None:
        with self.assertRaises(ContractError) as caught:
            validate_action({**CLICK, "verification": {"expect": "vibes"}})
        self.assertIn("verification.expect", str(caught.exception))
        with self.assertRaises(ContractError) as caught:
            validate_action(
                {
                    **CLICK,
                    "verification": {"expect": "element", "state": {"colour": "red"}},
                }
            )
        self.assertIn("verification.state.colour", str(caught.exception))

    def test_an_absence_must_say_what_is_gone(self) -> None:
        keypress = {
            "intent": "close it",
            "application": "notepad",
            "action": {"type": "keypress", "keys": "alt+f4"},
        }
        with self.assertRaises(ContractError) as caught:
            validate_action({**keypress, "verification": {"expect": "absent"}})
        self.assertIn("absent", str(caught.exception))
        # An element action already names what should be gone.
        self.assertIsNotNone(
            validate_action(
                {**CLICK, "verification": {"expect": "absent"}}
            ).verification
        )

    def test_an_explicit_null_reads_as_an_absent_optional(self) -> None:
        action = validate_action(
            {
                **CLICK,
                "step_id": None,
                "window": None,
                "capture": None,
                "verification": None,
                "action": {"type": "click", "button": None, "sensitive": None},
            }
        )
        self.assertEqual(action.button, "left")
        self.assertIs(action.sensitive, False)
        self.assertIs(action.capture, False)
        self.assertIsNone(action.verification)

    def test_a_payload_that_is_not_an_object_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            validate_action(["click"])


class ActionPayloadTests(unittest.TestCase):
    def test_sensitive_text_is_redacted_by_length(self) -> None:
        action = validate_action(
            {**CLICK, "action": {"type": "type", "text": "hello", "sensitive": True}}
        )
        payload = action_payload(action)
        self.assertEqual(payload["text"], "<redacted 5 chars>")
        self.assertIs(payload["sensitive"], True)
        self.assertEqual(payload["schema"], ACTION_SCHEMA)
        self.assertEqual(payload["type"], "type")

    def test_plain_text_is_kept(self) -> None:
        action = validate_action({**CLICK, "action": {"type": "type", "text": "hello"}})
        self.assertEqual(action_payload(action)["text"], "hello")

    def test_only_the_keys_the_action_uses_are_emitted(self) -> None:
        payload = action_payload(validate_action(CLICK))
        self.assertEqual(
            sorted(payload), ["button", "capture", "schema", "sensitive", "type"]
        )


if __name__ == "__main__":
    unittest.main()
