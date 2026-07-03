from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import smtplib

from app.config import get_settings
from app.emailer import send_verification_email


class _FakeSMTP:
    def __init__(self, host: str, port: int, timeout: int):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent_to: str | None = None
        self.from_header: str | None = None

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message: Any) -> None:
        self.sent_to = str(message["To"])
        self.from_header = str(message["From"])


def test_settings_uses_smtp_from_email_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant.db'}")
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setenv("SMTP_FROM_NAME", "Study Assistant")

    settings = get_settings()
    assert settings.smtp_from == "noreply@example.com"
    assert settings.smtp_from_name == "Study Assistant"


def test_send_verification_email_uses_starttls_when_ssl_disabled(monkeypatch) -> None:
    fake = _FakeSMTP("smtp.example.com", 587, 20)

    def _smtp(host: str, port: int, timeout: int):
        return fake

    def _smtp_ssl(*args, **kwargs):  # pragma: no cover - guard branch
        raise AssertionError("SMTP_SSL should not be used when smtp_use_ssl is false")

    monkeypatch.setattr(smtplib, "SMTP", _smtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _smtp_ssl)

    settings = SimpleNamespace(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="pass",
        smtp_from="noreply@example.com",
        smtp_from_name="Study Assistant",
        smtp_use_tls=True,
        smtp_use_ssl=False,
    )
    send_verification_email(settings=settings, to_email="u@example.com", code="123456", ttl_minutes=10)

    assert fake.started_tls is True
    assert fake.login_args == ("user", "pass")
    assert fake.sent_to == "u@example.com"
    assert "noreply@example.com" in str(fake.from_header)


def test_send_verification_email_uses_ssl_transport(monkeypatch) -> None:
    fake = _FakeSMTP("smtp.example.com", 465, 20)

    def _smtp(*args, **kwargs):  # pragma: no cover - guard branch
        raise AssertionError("SMTP should not be used when smtp_use_ssl is true")

    def _smtp_ssl(host: str, port: int, timeout: int):
        return fake

    monkeypatch.setattr(smtplib, "SMTP", _smtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _smtp_ssl)

    settings = SimpleNamespace(
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_username="user",
        smtp_password="pass",
        smtp_from="noreply@example.com",
        smtp_from_name="Study Assistant",
        smtp_use_tls=True,
        smtp_use_ssl=True,
    )
    send_verification_email(settings=settings, to_email="u@example.com", code="123456", ttl_minutes=10)

    assert fake.started_tls is False
    assert fake.login_args == ("user", "pass")
    assert fake.sent_to == "u@example.com"
