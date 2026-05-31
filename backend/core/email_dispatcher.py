"""Automated email dispatch.

Sends each employee their salary slip PDF as an attachment, with an HTML body
rendered from a Jinja2 template. Built for resilience over raw speed:

* One SMTP connection is opened and reused for the whole batch (logging in
  per message is the real bottleneck and a common cause of provider throttling).
* Each send is wrapped individually, so one bad recipient produces a failed
  `SendResult` instead of aborting the run or raising an uncaught error.
* A connection / authentication failure is a configuration problem, not a
  per-recipient one, so it raises `SMTPConnectionError` for the API layer to
  surface clearly.

The module is framework-agnostic and takes SMTP credentials as constructor
arguments (the route builds it from `config.settings`), keeping it unit-testable
with a mocked SMTP server.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger("salary_slip_api.email")


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class EmailDispatchError(Exception):
    """Base class for email dispatch failures."""


class SMTPConnectionError(EmailDispatchError):
    """Could not connect to or authenticate with the SMTP server."""


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclass
class EmailJob:
    """One employee's email: who to send to and what to attach."""

    recipient_email: str
    recipient_name: str
    employee_id: str
    month_year: str
    pdf_bytes: bytes = field(repr=False)
    password_protected: bool = False
    password_hint: str | None = None


@dataclass
class SendResult:
    """Outcome of a single send attempt."""

    employee_id: str
    recipient_email: str
    success: bool
    error: str | None = None


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O — easy to unit test)
# --------------------------------------------------------------------------- #


def _safe_filename(employee_id: str, month_year: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{employee_id}_{month_year}").strip("_")
    return f"SalarySlip_{slug}.pdf"


def build_email_message(
    job: EmailJob,
    *,
    from_email: str,
    from_name: str,
    subject: str,
    html_body: str,
) -> EmailMessage:
    """Assemble a MIME message with an HTML body and the PDF attachment."""
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = f"{job.recipient_name} <{job.recipient_email}>"
    msg["Subject"] = subject

    # Plain-text fallback for non-HTML clients, then the HTML alternative.
    msg.set_content(
        f"Dear {job.recipient_name},\n\n"
        f"Please find attached your salary slip for {job.month_year}.\n\n"
        f"Regards,\n{from_name}"
    )
    msg.add_alternative(html_body, subtype="html")

    msg.add_attachment(
        job.pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=_safe_filename(job.employee_id, job.month_year),
    )
    return msg


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #


class EmailDispatcher:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_email: str,
        from_name: str = "Payroll Department",
        company_name: str = "Nippon Toyota",
        template_dir: str = "templates",
        template_name: str = "email_template.html",
        timeout: int = 30,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_email = from_email
        self.from_name = from_name
        self.company_name = company_name
        self.timeout = timeout
        # Port 465 implies implicit TLS (SMTPS); 587 implies STARTTLS.
        self.use_ssl = port == 465

        self._jinja = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self._template_name = template_name

    # -- internal -------------------------------------------------------- #

    def _render_body(self, job: EmailJob) -> str:
        template = self._jinja.get_template(self._template_name)
        return template.render(
            company_name=self.company_name,
            employee_name=job.recipient_name,
            month_year=job.month_year,
            password_protected=job.password_protected,
            password_hint=job.password_hint,
        )

    def _open_connection(self) -> smtplib.SMTP:
        try:
            if self.use_ssl:
                server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                server.ehlo()
                server.starttls()
                server.ehlo()
            server.login(self.username, self.password)
            return server
        except (smtplib.SMTPException, OSError) as exc:
            raise SMTPConnectionError(
                f"Failed to connect/authenticate to SMTP server "
                f"{self.host}:{self.port} — {exc}"
            ) from exc

    # -- public ---------------------------------------------------------- #

    def send_salary_slips(self, jobs: list[EmailJob]) -> list[SendResult]:
        """Send every job over a single reused connection.

        Raises SMTPConnectionError if the connection itself can't be
        established; otherwise returns one SendResult per job (never raises
        for an individual recipient).
        """
        if not jobs:
            return []

        results: list[SendResult] = []
        server = self._open_connection()
        try:
            for job in jobs:
                try:
                    message = build_email_message(
                        job,
                        from_email=self.from_email,
                        from_name=self.from_name,
                        subject=f"Salary Slip — {job.month_year}",
                        html_body=self._render_body(job),
                    )
                    server.send_message(message)
                    results.append(
                        SendResult(job.employee_id, job.recipient_email, success=True)
                    )
                    logger.info("Sent slip to %s (%s)", job.recipient_email, job.employee_id)
                except Exception as exc:  # noqa: BLE001 - isolate per-recipient failure
                    logger.warning("Failed sending to %s: %s", job.recipient_email, exc)
                    results.append(
                        SendResult(
                            job.employee_id,
                            job.recipient_email,
                            success=False,
                            error=str(exc),
                        )
                    )
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001 - connection cleanup is best-effort
                pass
        return results


# --------------------------------------------------------------------------- #
# HTTP API dispatcher (Mailtrap) — bypasses SMTP entirely
# --------------------------------------------------------------------------- #


class MailtrapDispatcher:
    """Sends salary slips via Mailtrap's HTTP API over HTTPS.

    Same public interface as EmailDispatcher (`send_salary_slips`), so the
    route can swap one for the other transparently. Because this rides on
    HTTPS (443) it is immune to the SMTP port/STARTTLS interference that
    antivirus mail-shields and some ISPs introduce.

    Set `sandbox=True` for an Email Testing inbox (captures mail without
    delivering); the inbox_id is required in that mode.
    """

    def __init__(
        self,
        *,
        api_token: str,
        inbox_id: str,
        from_email: str,
        from_name: str = "Payroll Department",
        company_name: str = "Nippon Toyota",
        template_dir: str = "templates",
        template_name: str = "email_template.html",
        sandbox: bool = True,
        timeout: int = 30,
    ) -> None:
        self.api_token = api_token
        self.inbox_id = inbox_id
        self.from_email = from_email
        self.from_name = from_name
        self.company_name = company_name
        self.timeout = timeout
        if sandbox:
            self.endpoint = f"https://sandbox.api.mailtrap.io/api/send/{inbox_id}"
        else:
            self.endpoint = "https://send.api.mailtrap.io/api/send"
        self._jinja = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self._template_name = template_name

    def _render_body(self, job: EmailJob) -> str:
        return self._jinja.get_template(self._template_name).render(
            company_name=self.company_name,
            employee_name=job.recipient_name,
            month_year=job.month_year,
            password_protected=job.password_protected,
            password_hint=job.password_hint,
        )

    def _build_payload(self, job: EmailJob, html_body: str) -> dict:
        return {
            "from": {"email": self.from_email, "name": self.from_name},
            "to": [{"email": job.recipient_email, "name": job.recipient_name}],
            "subject": f"Salary Slip - {job.month_year}",
            "html": html_body,
            "text": (
                f"Dear {job.recipient_name}, please find attached your salary "
                f"slip for {job.month_year}."
            ),
            "attachments": [
                {
                    "content": base64.b64encode(job.pdf_bytes).decode("ascii"),
                    "filename": _safe_filename(job.employee_id, job.month_year),
                    "type": "application/pdf",
                    "disposition": "attachment",
                }
            ],
        }

    def _post(self, payload: dict) -> tuple[int, str]:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        request.add_header("Authorization", f"Bearer {self.api_token}")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        # Mailtrap's API is fronted by Cloudflare, which rejects the default
        # "Python-urllib" User-Agent with a 403 / error code 1010. A normal
        # browser-style User-Agent avoids that block.
        request.add_header(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.status, response.read().decode("utf-8", "ignore")

    def send_salary_slips(self, jobs: list[EmailJob]) -> list[SendResult]:
        if not jobs:
            return []
        if not self.api_token or not self.inbox_id:
            raise EmailDispatchError("Mailtrap API token / inbox ID is not configured.")

        results: list[SendResult] = []
        for job in jobs:
            try:
                status_code, body = self._post(self._build_payload(job, self._render_body(job)))
                ok = 200 <= status_code < 300
                results.append(
                    SendResult(
                        job.employee_id,
                        job.recipient_email,
                        success=ok,
                        error=None if ok else f"HTTP {status_code}: {body[:200]}",
                    )
                )
                if ok:
                    logger.info("Sent slip to %s (%s)", job.recipient_email, job.employee_id)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "ignore")[:200]
                # Auth failures are a config problem -> abort the whole batch.
                if exc.code in (401, 403):
                    raise EmailDispatchError(
                        f"Mailtrap authentication failed (HTTP {exc.code}): {detail}"
                    ) from exc
                logger.warning("Mailtrap send failed for %s: %s", job.recipient_email, detail)
                results.append(
                    SendResult(
                        job.employee_id,
                        job.recipient_email,
                        success=False,
                        error=f"HTTP {exc.code}: {detail}",
                    )
                )
            except urllib.error.URLError as exc:
                raise EmailDispatchError(f"Could not reach the Mailtrap API: {exc.reason}") from exc
        return results