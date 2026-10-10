"""Outgoing email (verification and password reset) over SMTP.

Without CRM_SMTP_HOST, non-production environments log the link so you can click it while developing.
Production refuses to start without SMTP when email verification is required (see config.py).
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger("app.mailer")


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def send(self, to: str, subject: str, text: str, dev_link: str = "") -> None:
        if not self._s.smtp_host:
            if not self._s.is_production:
                # Development convenience only. Links carry one-time tokens, so never do this in production.
                log.info("mail_not_sent_no_smtp", extra={"to": to, "subject": subject, "link": dev_link})
            return
        await asyncio.to_thread(self._send_sync, to, subject, text)

    def _send_sync(self, to: str, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self._s.smtp_from, to, subject
        msg.set_content(text)
        try:
            with smtplib.SMTP(self._s.smtp_host, self._s.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                if self._s.smtp_user:
                    smtp.login(self._s.smtp_user, self._s.smtp_password or "")
                smtp.send_message(msg)
        except Exception:
            log.exception("mail_send_failed")  # message body and recipient link are not logged

    async def send_verification(self, to: str, link: str) -> None:
        await self.send(to, "Verify your Synthra CRM email",
                        f"Welcome to Synthra CRM.\n\nConfirm your email address:\n{link}\n\n"
                        "If you did not create an account, ignore this message.", link)

    async def send_password_reset(self, to: str, link: str) -> None:
        await self.send(to, "Reset your Synthra CRM password",
                        f"Use this link to choose a new password:\n{link}\n\n"
                        "If you did not request this, ignore this message; your password is unchanged.", link)
