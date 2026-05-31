"""Tests for core.email_dispatcher — MIME assembly and all three transports
(SMTP, Brevo, Mailtrap) with the network mocked."""

import base64
import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from tests.conftest import TEMPLATES_DIR
from core.email_dispatcher import (
    BrevoDispatcher,
    EmailDispatcher,
    EmailDispatchError,
    EmailJob,
    MailtrapDispatcher,
    SMTPConnectionError,
    build_email_message,
)

PDF = b"%PDF-1.7 test bytes"


def _jobs():
    return [
        EmailJob("aarav@example.com", "Aarav Sharma", "EMP1001", "May 2026", PDF, True, "name + ID"),
        EmailJob("priya@example.com", "Priya Nair", "EMP1002", "May 2026", PDF),
    ]


# ---------------- MIME ----------------

def test_build_message_has_html_and_pdf_attachment():
    msg = build_email_message(
        _jobs()[0], from_email="p@c.com", from_name="Payroll",
        subject="Salary Slip", html_body="<p>hi</p>",
    )
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_filename() == "SalarySlip_EMP1001_May_2026.pdf"
    assert msg.get_body(preferencelist=("html",)) is not None


# ---------------- SMTP ----------------

def _smtp():
    return EmailDispatcher(
        host="smtp.test", port=587, username="u", password="p",
        from_email="p@c.com", from_name="Payroll", template_dir=TEMPLATES_DIR,
    )


def test_smtp_reuses_one_connection_and_sends_all():
    with patch("core.email_dispatcher.smtplib.SMTP") as MockSMTP:
        server = MockSMTP.return_value
        results = _smtp().send_salary_slips(_jobs())
        assert MockSMTP.call_count == 1          # one connection for the batch
        server.login.assert_called_once()
        assert server.send_message.call_count == 2
        assert all(r.success for r in results)


def test_smtp_failure_isolation():
    with patch("core.email_dispatcher.smtplib.SMTP") as MockSMTP:
        server = MockSMTP.return_value
        server.send_message.side_effect = [None, Exception("550 bad mailbox")]
        results = _smtp().send_salary_slips(_jobs())
        assert [r.success for r in results] == [True, False]
        assert "550" in results[1].error


def test_smtp_connection_failure_raises():
    import smtplib
    with patch("core.email_dispatcher.smtplib.SMTP") as MockSMTP:
        MockSMTP.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad")
        with pytest.raises(SMTPConnectionError):
            _smtp().send_salary_slips(_jobs())


# ---------------- Brevo ----------------

class _Resp:
    def __init__(self, status=201, body=b'{"messageId":"x"}'):
        self.status = status
        self._b = body
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _brevo():
    return BrevoDispatcher(
        api_key="xkeysib-test", from_email="payroll@nippontoyota.com",
        from_name="Payroll", template_dir=TEMPLATES_DIR,
    )


def test_brevo_builds_correct_request_and_succeeds():
    captured = []

    def fake(req, timeout=None):
        captured.append(req)
        return _Resp(201)

    with patch("core.email_dispatcher.urllib.request.urlopen", side_effect=fake):
        results = _brevo().send_salary_slips(_jobs())

    req = captured[0]
    payload = json.loads(req.data.decode())
    assert req.full_url == "https://api.brevo.com/v3/smtp/email"
    assert req.get_header("Api-key") == "xkeysib-test"
    assert base64.b64decode(payload["attachment"][0]["content"]) == PDF
    assert all(r.success for r in results)  # 201 counts as success


def test_brevo_auth_failure_raises():
    def u401(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(b'{"message":"bad"}'))

    with patch("core.email_dispatcher.urllib.request.urlopen", side_effect=u401):
        with pytest.raises(EmailDispatchError):
            _brevo().send_salary_slips(_jobs())


# ---------------- Mailtrap + retry ----------------

def test_mailtrap_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "rate", {}, io.BytesIO(b'{"e":"rate"}'))
        return _Resp(200, b'{"success":true}')

    d = MailtrapDispatcher(
        api_token="t", inbox_id="999", from_email="p@c.com",
        template_dir=TEMPLATES_DIR, retry_delay=0.0,
    )
    with patch("core.email_dispatcher.time.sleep"), patch(
        "core.email_dispatcher.urllib.request.urlopen", side_effect=flaky
    ):
        results = d.send_salary_slips([_jobs()[0]])
    assert results[0].success
    assert calls["n"] == 2  # one 429, one success