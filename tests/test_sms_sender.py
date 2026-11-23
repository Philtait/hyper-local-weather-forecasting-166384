"""SMS sender: phone validation, preview fallback and a mocked Africa's Talking SDK.

No test calls the network: every Africa's Talking call goes to a mock SDK.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from sms_sender import (
    AfricasTalkingSender, PreviewSender, load_credentials, send_with_fallback, validate_phone,
)

SECRET = "atsk_test_secret_value"
NO_ENV = {key: value for key, value in os.environ.items() if key not in ("AT_USERNAME", "AT_API_KEY")}


def fake_sdk(status="Success", error=None):
    sdk = MagicMock()
    if error:
        sdk.SMS.send.side_effect = error
    else:
        sdk.SMS.send.return_value = {"SMSMessageData": {"Message": "Sent to 1/1",
                                                        "Recipients": [{"status": status}]}}
    return sdk


class SmsSenderTests(unittest.TestCase):
    def test_phone_validation_accepts_only_kenyan_mobile_numbers(self):
        self.assertEqual(validate_phone("+254712345678"), "+254712345678")
        self.assertEqual(validate_phone(" +254 712-345-678 "), "+254712345678")
        self.assertEqual(validate_phone("+254112345678"), "+254112345678")
        for bad in ["0712345678", "254712345678", "+25471234567", "+2547123456789",
                    "+255712345678", "+254812345678", "+2547abc45678", "", None]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate_phone(bad)

    def test_preview_sender_returns_text_without_sending(self):
        result = PreviewSender().send("+254712345678", "Hello")
        self.assertFalse(result.sent)
        self.assertEqual((result.mode, result.text), ("preview", "Hello"))

    def test_falls_back_to_preview_when_credentials_are_missing(self):
        sdk = fake_sdk()
        with patch.dict(os.environ, NO_ENV, clear=True), \
                patch.dict(sys.modules, {"africastalking": sdk}):
            self.assertIsNone(load_credentials(secrets={}))
            result = send_with_fallback("+254712345678", "Flood watch", secrets={})
        self.assertFalse(result.sent)
        self.assertEqual(result.mode, "preview")
        self.assertIn("not configured", result.detail)
        sdk.initialize.assert_not_called()
        sdk.SMS.send.assert_not_called()

    def test_environment_credentials_take_priority_over_secrets(self):
        env = {**NO_ENV, "AT_USERNAME": "sandbox", "AT_API_KEY": SECRET}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(load_credentials(secrets={"AT_USERNAME": "other", "AT_API_KEY": "x"}),
                             ("sandbox", SECRET))
        with patch.dict(os.environ, NO_ENV, clear=True):
            self.assertEqual(load_credentials(secrets={"AT_USERNAME": "s", "AT_API_KEY": "k"}), ("s", "k"))

    def test_sends_through_mocked_sdk_when_credentials_exist(self):
        sdk = fake_sdk()
        with patch.dict(os.environ, NO_ENV, clear=True):
            result = send_with_fallback("+254712345678", "Flood watch",
                                        secrets={"AT_USERNAME": "sandbox", "AT_API_KEY": SECRET}, sdk=sdk)
        self.assertTrue(result.sent)
        sdk.initialize.assert_called_once_with("sandbox", SECRET)
        sdk.SMS.send.assert_called_once_with("Flood watch", ["+254712345678"])
        self.assertNotIn(SECRET, result.detail)

    def test_api_failure_falls_back_to_preview_and_hides_the_key(self):
        secrets = {"AT_USERNAME": "sandbox", "AT_API_KEY": SECRET}
        with patch.dict(os.environ, NO_ENV, clear=True):
            failed = send_with_fallback("+254712345678", "Flood watch", secrets=secrets,
                                        sdk=fake_sdk(error=RuntimeError(f"bad key {SECRET}")))
            rejected = send_with_fallback("+254712345678", "Flood watch", secrets=secrets,
                                          sdk=fake_sdk(status="InvalidPhoneNumber"))
        for result in (failed, rejected):
            self.assertFalse(result.sent)
            self.assertEqual(result.mode, "preview")
            self.assertEqual(result.text, "Flood watch")
            self.assertNotIn(SECRET, result.detail)
        self.assertIn("[hidden]", failed.detail)

    def test_invalid_number_is_rejected_before_any_send(self):
        sdk = fake_sdk()
        with self.assertRaises(ValueError):
            AfricasTalkingSender("sandbox", SECRET, sdk=sdk).send("0712345678", "Hi")
        sdk.SMS.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
