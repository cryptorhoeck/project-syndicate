"""
Project Syndicate — Email Alert System

Handles daily reports, Yellow/Red/Circuit Breaker alerts,
and emergency notifications via Gmail SMTP.
"""

__version__ = "0.2.0"

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import structlog

from src.common.config import config

logger = structlog.get_logger()


class EmailService:
    """Email notification service for daily reports and alerts."""

    def __init__(self) -> None:
        self.log = logger.bind(component="email_service")
        self.smtp_host = config.smtp_host
        self.smtp_port = config.smtp_port
        self.smtp_user = config.smtp_user
        self.smtp_password = config.smtp_password
        self.email_to = config.alert_email_to
        self.email_from = config.alert_email_from or config.smtp_user

        if not self.smtp_user or not self.smtp_password:
            self.log.warning("email_not_configured — SMTP credentials missing")
        else:
            self.log.info("email_service_initialized", host=self.smtp_host, port=self.smtp_port)

    def _send(self, subject: str, body: str, html: bool = False) -> bool:
        """Send an email via SMTP. Returns True on success."""
        if not self.smtp_user or not self.smtp_password or not self.email_to:
            self.log.warning("email_skipped — not configured")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.email_from
            msg["To"] = self.email_to

            if html:
                msg.attach(MIMEText(body, "html"))
            else:
                msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            self.log.info("email_sent", subject=subject, to=self.email_to)
            return True

        except Exception as exc:
            self.log.error("email_send_failed", subject=subject, error=str(exc))
            return False

    async def send_daily_report(self, report: str, report_date: str) -> bool:
        """Send the daily report email."""
        # Determine status from report content
        status = "stable"
        for keyword in ("thriving", "struggling", "critical"):
            if keyword in report.lower():
                status = keyword
                break

        subject = f"Syndicate Daily Report — {report_date} — {status}"
        html_body = f"<html><body><pre style='font-family: monospace;'>{report}</pre></body></html>"
        return self._send(subject, html_body, html=True)

    async def send_alert(self, alert_type: str, message: str) -> bool:
        """Send a Yellow/Red/Circuit Breaker alert."""
        subject = f"SYNDICATE ALERT: {alert_type.upper()}"
        return self._send(subject, message)

    async def send_emergency(self, message: str) -> bool:
        """Send an emergency notification (circuit breaker)."""
        subject = "SYNDICATE EMERGENCY — IMMEDIATE ACTION REQUIRED"
        return self._send(subject, message)
