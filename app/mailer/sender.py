"""Email delivery (PRD 46, 61).

`EmailSender` is the interface; `ResendSender` is the V1 implementation. Swapping to
SendGrid or Mailgun means one new class, because nothing above this layer knows the
provider.

Delivery is retried three times on transient failures and then reported as failed
(PRD 61) -- the run records `email_status=failed` and returns a non-success response so
GitHub Actions surfaces it, rather than a silent morning with no briefing.
"""

import asyncio
import logging
import re
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid

import httpx

from app.collect.http import NON_RETRYABLE_STATUS
from app.errors import BriefingError, ErrorCode, Severity
from app.logging_setup import LOGGER_NAME

RESEND_ENDPOINT = "https://api.resend.com/emails"
DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 30.0
ERROR_BODY_CHARS = 300


@dataclass
class SendResult:
    ok: bool
    message_id: str = ""
    error: str = ""
    attempts: int = 1


class EmailSender(ABC):
    @abstractmethod
    async def send(
        self,
        client: httpx.AsyncClient,
        *,
        sender: str,
        recipient: str,
        subject: str,
        html: str,
        text: str,
    ) -> SendResult: ...


class ResendSender(EmailSender):
    def __init__(
        self,
        api_key: str,
        attempts: int = DEFAULT_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._api_key = api_key
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._log = logger or logging.getLogger(LOGGER_NAME)

    async def send(
        self,
        client: httpx.AsyncClient,
        *,
        sender: str,
        recipient: str,
        subject: str,
        html: str,
        text: str,
    ) -> SendResult:
        payload = {
            "from": sender,
            "to": [recipient],
            "subject": subject,
            "html": html,
            # Resend uses this as the multipart/alternative text part, so a client
            # that cannot render HTML still receives a readable briefing.
            "text": text,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last = SendResult(ok=False, error="no attempt made")

        for attempt in range(1, self._attempts + 1):
            try:
                response = await client.post(RESEND_ENDPOINT, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = SendResult(ok=False, error=f"{type(exc).__name__}: {exc}", attempts=attempt)
            else:
                if response.status_code in (200, 201):
                    message_id = response.json().get("id", "")
                    self._log.info("EMAIL_SENT message_id=%s attempts=%d", message_id, attempt)
                    return SendResult(ok=True, message_id=message_id, attempts=attempt)

                body = " ".join(response.text[:ERROR_BODY_CHARS].split())
                last = SendResult(
                    ok=False, error=f"HTTP {response.status_code}: {body}", attempts=attempt
                )

                if response.status_code in NON_RETRYABLE_STATUS:
                    # An unverified sending domain returns 403 and will do so every
                    # time. The body is logged because the status alone does not say
                    # which of several setup problems it is.
                    self._log.error(
                        "EMAIL_REJECTED status=%d body=%s", response.status_code, body
                    )
                    return last

            if attempt < self._attempts:
                await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))

        self._log.error("EMAIL_FAILED attempts=%d error=%s", last.attempts, last.error)
        return last


class NullSender(EmailSender):
    """Renders and records, sends nothing. Used by dry runs (PRD 86)."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger(LOGGER_NAME)
        self.last_html = ""
        self.last_text = ""
        self.last_subject = ""

    async def send(
        self,
        client: httpx.AsyncClient,
        *,
        sender: str,
        recipient: str,
        subject: str,
        html: str,
        text: str,
    ) -> SendResult:
        self.last_html, self.last_text, self.last_subject = html, text, subject
        self._log.info("EMAIL_SKIPPED reason=dry_run subject=%s", subject)
        return SendResult(ok=True, message_id="dry-run", attempts=0)


def require_send_failure(result: SendResult) -> None:
    """Raise when delivery failed, so the run is not recorded as successful."""
    if not result.ok:
        raise BriefingError(
            ErrorCode.EMAIL_FAILED, result.error, severity=Severity.CRITICAL
        )


def build_mail_client(timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))


# --- SendGrid ----------------------------------------------------------------

SENDGRID_ENDPOINT = "https://api.sendgrid.com/v3/mail/send"

# SendGrid answers a successful send with 202 Accepted and an empty body, not 200.
SENDGRID_ACCEPTED = 202

_ADDRESS_RE = re.compile(r"^\s*(?P<name>.*?)\s*<\s*(?P<email>[^>]+?)\s*>\s*$")


def split_address(value: str) -> tuple[str, str]:
    """Split `Display Name <a@b.com>` into its parts.

    Resend takes the combined form in one field; SendGrid wants `name` and `email`
    separately and rejects the combined string. Configuration should not have to care
    which provider is in use, so the parsing happens here.
    """
    match = _ADDRESS_RE.match(value or "")
    if match:
        return match.group("name"), match.group("email")
    return "", (value or "").strip()


class SendGridSender(EmailSender):
    """SendGrid v3 Mail Send.

    Chosen because Single Sender Verification authorises one address by emailing it a
    confirmation link, with no DNS records at all -- which is what makes it usable
    before a sending domain exists. The trade-off is that a `from` address on a domain
    you do not control is not DMARC-aligned, so deliverability is weaker than a
    verified domain would give.
    """

    def __init__(
        self,
        api_key: str,
        attempts: int = DEFAULT_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._api_key = api_key
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._log = logger or logging.getLogger(LOGGER_NAME)

    async def send(
        self,
        client: httpx.AsyncClient,
        *,
        sender: str,
        recipient: str,
        subject: str,
        html: str,
        text: str,
    ) -> SendResult:
        name, address = split_address(sender)
        from_field: dict[str, str] = {"email": address}
        if name:
            from_field["name"] = name

        payload = {
            "personalizations": [{"to": [{"email": recipient}]}],
            "from": from_field,
            "subject": subject,
            # Order matters: SendGrid builds the MIME parts in the order given, and
            # the plain-text alternative must come before the HTML one.
            "content": [
                {"type": "text/plain", "value": text},
                {"type": "text/html", "value": html},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last = SendResult(ok=False, error="no attempt made")

        for attempt in range(1, self._attempts + 1):
            try:
                response = await client.post(SENDGRID_ENDPOINT, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = SendResult(ok=False, error=f"{type(exc).__name__}: {exc}", attempts=attempt)
            else:
                if response.status_code == SENDGRID_ACCEPTED:
                    # The body is empty; the id lives in a response header.
                    message_id = response.headers.get("X-Message-Id", "")
                    self._log.info("EMAIL_SENT message_id=%s attempts=%d", message_id, attempt)
                    return SendResult(ok=True, message_id=message_id, attempts=attempt)

                body = " ".join(response.text[:ERROR_BODY_CHARS].split())
                last = SendResult(
                    ok=False, error=f"HTTP {response.status_code}: {body}", attempts=attempt
                )

                if response.status_code in NON_RETRYABLE_STATUS:
                    # An unverified sender returns 403 and will do so every time. The
                    # body names which of several setup problems it is.
                    self._log.error(
                        "EMAIL_REJECTED status=%d body=%s", response.status_code, body
                    )
                    return last

            if attempt < self._attempts:
                await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))

        self._log.error("EMAIL_FAILED attempts=%d error=%s", last.attempts, last.error)
        return last


# --- SMTP --------------------------------------------------------------------

DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587

# Authentication failures repeat identically, so they are never retried. Everything
# else SMTP raises -- a dropped connection, a greylisting deferral, a busy server --
# is worth another attempt.
_PERMANENT_SMTP_ERRORS = (
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPNotSupportedError,
)


class SmtpSender(EmailSender):
    """Sends over SMTP, e.g. Gmail with an App Password.

    Needs no provider account and no sender verification: the mailbox already belongs
    to you, so nothing has to be proved to a third party. That makes it the shortest
    path to a working briefing.

    Two constraints worth knowing before relying on it:

    * **Render's free tier blocks outbound ports 25, 465 and 587.** Ports 465 and 587
      work on a paid instance; port 25 is blocked on every tier. So this works locally
      and on paid hosting, and silently times out on a free Render service.
    * Gmail requires an **App Password** with 2FA enabled. An account password is
      rejected.

    `smtplib` is synchronous, so the send runs in a worker thread rather than blocking
    the event loop. For one email a day that is simpler and cheaper than adding an
    async SMTP dependency.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        attempts: int = DEFAULT_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._timeout = timeout_seconds
        self._log = logger or logging.getLogger(LOGGER_NAME)

    async def send(
        self,
        client: httpx.AsyncClient,
        *,
        sender: str,
        recipient: str,
        subject: str,
        html: str,
        text: str,
    ) -> SendResult:
        message = self._build(sender=sender, recipient=recipient, subject=subject,
                              html=html, text=text)
        last = SendResult(ok=False, error="no attempt made")

        for attempt in range(1, self._attempts + 1):
            try:
                await asyncio.to_thread(self._deliver, message)
            except _PERMANENT_SMTP_ERRORS as exc:
                self._log.error("EMAIL_REJECTED reason=%s", type(exc).__name__)
                return SendResult(
                    ok=False, error=f"{type(exc).__name__}: {exc}", attempts=attempt
                )
            except (OSError, smtplib.SMTPException) as exc:
                # A free-tier Render service surfaces the port block here, as a
                # connection timeout rather than an SMTP-level rejection.
                last = SendResult(
                    ok=False, error=f"{type(exc).__name__}: {exc}", attempts=attempt
                )
            else:
                message_id = message["Message-ID"] or ""
                self._log.info("EMAIL_SENT message_id=%s attempts=%d", message_id, attempt)
                return SendResult(ok=True, message_id=message_id, attempts=attempt)

            if attempt < self._attempts:
                await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))

        self._log.error("EMAIL_FAILED attempts=%d error=%s", last.attempts, last.error)
        return last

    def _build(self, *, sender: str, recipient: str, subject: str,
               html: str, text: str) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = recipient
        message["Message-ID"] = make_msgid(domain="melbournemama.local")
        # set_content then add_alternative produces multipart/alternative with the
        # plain part first, which is the order mail clients expect. EmailMessage
        # handles UTF-8 encoding, which matters because headlines are often Telugu.
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        return message

    def _deliver(self, message: EmailMessage) -> None:
        """Blocking send, run in a worker thread."""
        if self._port == 465:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout) as server:
                server.login(self._username, self._password)
                server.send_message(message)
            return

        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
            server.starttls()
            server.login(self._username, self._password)
            server.send_message(message)
