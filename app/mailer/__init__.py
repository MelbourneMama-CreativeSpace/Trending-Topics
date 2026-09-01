"""Email rendering and delivery (PRD 42-47, 61)."""

from app.mailer.render import Briefing, build_subject, render_html, render_text, shortfall_note
from app.mailer.sender import (
    EmailSender,
    NullSender,
    ResendSender,
    SendGridSender,
    SendResult,
    SmtpSender,
    build_mail_client,
    require_send_failure,
)

__all__ = [
    "Briefing",
    "EmailSender",
    "NullSender",
    "ResendSender",
    "SendGridSender",
    "SmtpSender",
    "SendResult",
    "build_mail_client",
    "build_subject",
    "render_html",
    "render_text",
    "require_send_failure",
    "shortfall_note",
]
