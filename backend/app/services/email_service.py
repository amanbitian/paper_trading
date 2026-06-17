from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)


def _smtp_configured() -> bool:
    return bool(settings.smtp_user and settings.smtp_password)


def _send_email(to_email: str, subject: str, plain_body: str, html_body: str) -> None:
    if not _smtp_configured():
        return
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
    message["To"] = to_email
    message.attach(MIMEText(plain_body, "plain"))
    message.attach(MIMEText(html_body, "html"))
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, [to_email], message.as_string())
    except Exception:
        logger.exception("Failed to send email to %s", to_email)


def send_signal_alert(
    to_email: str,
    user_name: str,
    signal_type: str,
    symbol: str,
    strategy_name: str,
    confidence: float,
    reason: str,
    suggested_qty: int,
    latest_price: float,
    indicators: dict | None = None,
) -> None:
    if not settings.email_alerts_enabled:
        return
    indicators = indicators or {}
    stop_price = indicators.get("stop_price")
    take_profit = indicators.get("take_profit_price")
    subject = f"[{signal_type} Signal] {symbol} — {strategy_name} (Confidence: {confidence:.0f}%)"
    plain = (
        f"Hi {user_name},\n\n"
        f"Your strategy \"{strategy_name}\" generated a {signal_type} signal for {symbol}.\n\n"
        f"Action: {signal_type}\nConfidence: {confidence:.2f}%\n"
        f"Latest Price: ₹{latest_price:,.2f}\nSuggested Qty: {suggested_qty}\n"
        f"Reason: {reason}\n"
    )
    if stop_price:
        plain += f"ATR Stop: ₹{float(stop_price):,.2f}\n"
    if take_profit:
        plain += f"Take Profit: ₹{float(take_profit):,.2f}\n"
    plain += "\nEducational paper trading only — not financial advice.\n"
    html = plain.replace("\n", "<br>")
    html += f'<p><a href="{settings.app_base_url}">View in App</a></p>'
    _send_email(to_email, subject, plain, html)


def send_reset_email(to_email: str, token: str) -> None:
    if not _smtp_configured():
        logger.info("SMTP not configured; password reset email skipped for %s", to_email)
        return
    link = f"{settings.app_base_url}/Reset_Password?token={token}"
    subject = "Reset your Paper Trading App password"
    plain = (
        f"Hi,\n\nClick the link below to reset your password "
        f"(valid for {settings.password_reset_token_expire_minutes} minutes):\n\n"
        f"{link}\n\nIf you didn't request this, ignore this email.\n"
    )
    html = plain.replace("\n", "<br>")
    _send_email(to_email, subject, plain, html)
