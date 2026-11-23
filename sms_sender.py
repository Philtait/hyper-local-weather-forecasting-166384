"""SMS delivery for advisories: a no-network preview and Africa's Talking.

Credentials are read from the AT_USERNAME and AT_API_KEY environment
variables first, then from Streamlit secrets. The API key is never logged,
returned or shown; error text is scrubbed of it before display. With the
username "sandbox" the Africa's Talking SDK uses its sandbox, so no real
message is delivered.
"""

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

PHONE_PATTERN = re.compile(r"\+254[17]\d{8}")


@dataclass
class SendResult:
    sent: bool          # True only when the provider accepted the message
    mode: str           # "preview" or "africastalking"
    phone: str
    text: str
    detail: str = ""    # safe to display; never contains the API key


def validate_phone(phone):
    """Return a Kenyan mobile number as +254XXXXXXXXX, or raise ValueError."""
    number = re.sub(r"[\s-]", "", str(phone or ""))
    if not PHONE_PATTERN.fullmatch(number):
        raise ValueError("Enter a Kenyan mobile number in +254 format, e.g. +254712345678.")
    return number


class SmsSender(ABC):
    mode = "base"

    @abstractmethod
    def send(self, phone, text):
        """Send text to a validated phone number and return a SendResult."""


class PreviewSender(SmsSender):
    """Makes no network call; returns the message it would have sent."""
    mode = "preview"

    def send(self, phone, text):
        return SendResult(sent=False, mode=self.mode, phone=validate_phone(phone), text=text,
                          detail="Preview only: no SMS was sent.")


class AfricasTalkingSender(SmsSender):
    mode = "africastalking"

    def __init__(self, username, api_key, sdk=None):
        if not username or not api_key:
            raise ValueError("Africa's Talking username and API key are required.")
        self._username = username
        self._api_key = api_key
        self._sdk = sdk

    def _scrub(self, text):
        return str(text).replace(self._api_key, "[hidden]")

    def send(self, phone, text):
        phone = validate_phone(phone)
        sdk = self._sdk
        if sdk is None:
            import africastalking as sdk  # imported only when a real send is attempted
        try:
            sdk.initialize(self._username, self._api_key)
            response = sdk.SMS.send(text, [phone])
        except Exception as error:
            raise RuntimeError(self._scrub(f"{type(error).__name__}: {error}")) from None
        recipients = (response or {}).get("SMSMessageData", {}).get("Recipients", [])
        statuses = [recipient.get("status", "") for recipient in recipients]
        if not statuses or any(status != "Success" for status in statuses):
            summary = (response or {}).get("SMSMessageData", {}).get("Message", "no recipient status")
            raise RuntimeError(self._scrub(f"Provider did not accept the message: {statuses or summary}"))
        return SendResult(sent=True, mode=self.mode, phone=phone, text=text,
                          detail=f"Accepted by Africa's Talking for {phone}.")


def load_credentials(secrets=None):
    """Return (username, api_key) from the environment, then secrets, or None."""
    username, api_key = os.environ.get("AT_USERNAME"), os.environ.get("AT_API_KEY")
    if username and api_key:
        return username, api_key
    if secrets is None:
        try:
            import streamlit as st
            secrets = st.secrets
        except Exception:
            secrets = {}
    try:
        username, api_key = secrets.get("AT_USERNAME"), secrets.get("AT_API_KEY")
    except Exception:  # Streamlit raises when no secrets file exists
        return None
    return (username, api_key) if username and api_key else None


def send_with_fallback(phone, text, secrets=None, sdk=None):
    """Try Africa's Talking; on missing credentials or any failure, preview.

    Raises ValueError only for an invalid phone number.
    """
    phone = validate_phone(phone)
    credentials = load_credentials(secrets)
    if credentials is None:
        result = PreviewSender().send(phone, text)
        result.detail = "SMS credentials are not configured, so this is a preview only. No SMS was sent."
        return result
    try:
        return AfricasTalkingSender(*credentials, sdk=sdk).send(phone, text)
    except Exception as error:
        result = PreviewSender().send(phone, text)
        result.detail = f"The SMS could not be sent ({error}). Showing a preview instead."
        return result
