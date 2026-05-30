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

import logging
import re
import smtplib
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