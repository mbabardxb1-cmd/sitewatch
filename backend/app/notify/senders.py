"""Delivery channels. Add Slack/webhook senders by implementing `Notifier` and registering it."""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

from app.config import get_settings


class NotifierError(Exception):
    pass


class Notifier(Protocol):
    channel: str

    def send(self, recipients: list[str], subject: str, body: str,
             attachments: list[tuple[str, bytes, str]] | None = None) -> None: ...


class SmtpNotifier:
    channel = "email"

    def send(self, recipients: list[str], subject: str, body: str,
             attachments: list[tuple[str, bytes, str]] | None = None) -> None:
        s = get_settings()
        if not s.smtp_host:
            raise NotifierError("SMTP is not configured (SMTP_HOST is empty).")
        msg = EmailMessage()
        msg["From"] = s.smtp_from
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain="sitewatch")
        msg.set_content(body)
        for filename, data, mime in attachments or []:
            maintype, subtype = mime.split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        try:
            if s.smtp_ssl:
                server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            with server:
                if s.smtp_starttls and not s.smtp_ssl:
                    server.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    server.login(s.smtp_username, s.smtp_password)
                server.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            raise NotifierError(f"{type(e).__name__}: {e}") from e


class MemoryNotifier:
    """Test double: records messages instead of sending them."""

    channel = "email"
    outbox: list[dict] = []
    fail_next: int = 0

    def send(self, recipients: list[str], subject: str, body: str,
             attachments: list[tuple[str, bytes, str]] | None = None) -> None:
        if MemoryNotifier.fail_next > 0:
            MemoryNotifier.fail_next -= 1
            raise NotifierError("simulated failure")
        MemoryNotifier.outbox.append({"to": list(recipients), "subject": subject, "body": body,
                                      "attachments": [(n, len(d), m) for n, d, m in attachments or []]})


def get_notifier(channel: str = "email") -> Notifier:
    backend = get_settings().notifier_backend
    if channel != "email":
        raise NotifierError(f"Unsupported channel {channel}")
    return MemoryNotifier() if backend == "memory" else SmtpNotifier()
