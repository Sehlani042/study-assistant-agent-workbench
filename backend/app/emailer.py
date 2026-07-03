from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.config import Settings


def send_verification_email(*, settings: Settings, to_email: str, code: str, ttl_minutes: int) -> None:
    smtp_host = str(settings.smtp_host or "").strip()
    smtp_from = str(settings.smtp_from or "").strip()
    if not smtp_host or not smtp_from:
        raise RuntimeError("smtp is not configured")
    smtp_from_name = str(settings.smtp_from_name or "").strip()
    from_header = formataddr((smtp_from_name, smtp_from)) if smtp_from_name else smtp_from

    message = EmailMessage()
    message["From"] = from_header
    message["To"] = to_email
    message["Subject"] = "Study Assistant 验证码"
    message.set_content(
        f"你的验证码是：{code}\n"
        f"有效期：{ttl_minutes} 分钟。\n"
        "如果不是你本人操作，请忽略这封邮件。"
    )

    if settings.smtp_use_ssl:
        transport = smtplib.SMTP_SSL(smtp_host, int(settings.smtp_port), timeout=20)
    else:
        transport = smtplib.SMTP(smtp_host, int(settings.smtp_port), timeout=20)
    with transport as smtp:
        if not settings.smtp_use_ssl and settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(message)
