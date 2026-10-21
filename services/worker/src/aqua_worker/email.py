"""Notification email: rendering (plain text + minimal HTML) and delivery backends.

Backends: `console` logs the message (development; the recipient is logged only as a keyed hash)
and `smtp` speaks to any SMTP provider (STARTTLS on 587 or implicit TLS on 465), so a free tier of
Brevo, Resend, Mailjet, … works without code changes. No address is ever written to the logs."""

import hashlib
import hmac
import html
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

from aqua_core.logging import get_logger
from aqua_core.settings import Settings

log = get_logger("aqua_worker.email")

PREFERENCES_PATH = "/settings/notifications"


class TransientEmailError(Exception):
    """Delivery may succeed later (network, 4xx): the task retries with backoff."""


class PermanentEmailError(Exception):
    """Retrying will not help (5xx, recipient refused, bad credentials)."""


def address_hash(address: str, settings: Settings) -> str:
    """Keyed hash of an address for logs (correlate deliveries without storing the address)."""
    key = settings.jwt_secret.get_secret_value().encode()
    return hmac.new(key, address.strip().lower().encode(), hashlib.sha256).hexdigest()[:16]


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str
    unsubscribe_url: str


def render(
    *, to: str, full_name: str, title: str, body: str, link: str | None, settings: Settings
) -> OutgoingEmail:
    base = settings.public_base_url.rstrip("/")
    url = f"{base}{link}" if link else base
    prefs = f"{base}{PREFERENCES_PATH}"
    text = (
        f"Hello {full_name},\n\n{body}\n\nOpen in AquaCreds: {url}\n\n"
        f"--\nYou receive this email because of your AquaCreds notification settings.\n"
        f"Unsubscribe or change which emails you get: {prefs}\n"
    )
    e = html.escape
    page = (
        '<!doctype html><html><body style="font-family:sans-serif;line-height:1.5">'
        f"<p>Hello {e(full_name)},</p><p>{e(body)}</p>"
        f'<p><a href="{e(url, quote=True)}">Open in AquaCreds</a></p>'
        '<hr><p style="font-size:12px;color:#555">You receive this email because of your '
        f'AquaCreds notification settings. <a href="{e(prefs, quote=True)}">Unsubscribe or '
        "change which emails you get</a>.</p></body></html>"
    )
    return OutgoingEmail(to=to, subject=title, text=text, html=page, unsubscribe_url=prefs)


def to_mime(message: OutgoingEmail, sender: str) -> EmailMessage:
    mime = EmailMessage()
    mime["From"] = sender
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime["Message-ID"] = make_msgid(domain="aquacreds")
    mime["List-Unsubscribe"] = f"<{message.unsubscribe_url}>"
    mime.set_content(message.text)
    mime.add_alternative(message.html, subtype="html")
    return mime


class EmailBackend(Protocol):
    def send(self, message: OutgoingEmail) -> None: ...


class ConsoleEmailBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, message: OutgoingEmail) -> None:
        log.info(
            "email.console",
            to_hash=address_hash(message.to, self.settings),
            subject=message.subject,
            text=message.text,
        )


class SmtpEmailBackend:
    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host:
            raise PermanentEmailError("SMTP_HOST is not configured")
        self.settings = settings

    def _connect(self) -> smtplib.SMTP:
        s = self.settings
        host, port, timeout = s.smtp_host or "", s.smtp_port, s.smtp_timeout_s
        if s.smtp_security == "ssl":
            return smtplib.SMTP_SSL(
                host, port, timeout=timeout, context=ssl.create_default_context()
            )
        conn = smtplib.SMTP(host, port, timeout=timeout)
        if s.smtp_security == "starttls":
            conn.starttls(context=ssl.create_default_context())
        return conn

    def send(self, message: OutgoingEmail) -> None:
        s = self.settings
        mime = to_mime(message, s.notification_email_from)
        try:
            with self._connect() as conn:
                if s.smtp_username:
                    conn.login(s.smtp_username, s.smtp_password.get_secret_value())
                conn.send_message(mime)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused) as exc:
            raise PermanentEmailError(type(exc).__name__) from exc
        except smtplib.SMTPResponseException as exc:
            kind = PermanentEmailError if 500 <= exc.smtp_code < 600 else TransientEmailError
            raise kind(f"SMTP {exc.smtp_code}") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise TransientEmailError(type(exc).__name__) from exc


def email_backend(settings: Settings) -> EmailBackend:
    if settings.notification_email_backend == "smtp":
        return SmtpEmailBackend(settings)
    return ConsoleEmailBackend(settings)
