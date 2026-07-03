from __future__ import annotations

import re

USERNAME_PATTERN = r"^[a-z][a-z0-9_.-]{2,31}$"
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

USERNAME_RE = re.compile(USERNAME_PATTERN)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def validate_username(username: str) -> str | None:
    normalized = normalize_username(username)
    if not normalized:
        return "username required"
    if not USERNAME_RE.fullmatch(normalized):
        return (
            f"username must be {USERNAME_MIN_LENGTH}-{USERNAME_MAX_LENGTH} chars, "
            "start with a letter, and use only letters/numbers/._-"
        )
    return None


def validate_password(password: str) -> str | None:
    raw = str(password or "")
    if len(raw) < PASSWORD_MIN_LENGTH:
        return f"password must be at least {PASSWORD_MIN_LENGTH} characters"
    if len(raw) > PASSWORD_MAX_LENGTH:
        return f"password must be at most {PASSWORD_MAX_LENGTH} characters"
    if any(ch.isspace() for ch in raw):
        return "password must not contain spaces"
    has_letter = any(ch.isalpha() for ch in raw)
    has_digit = any(ch.isdigit() for ch in raw)
    if not has_letter or not has_digit:
        return "password must include letters and numbers"
    return None


def auth_policy_payload() -> dict[str, object]:
    return {
        "username": {
            "pattern": USERNAME_PATTERN,
            "min_length": USERNAME_MIN_LENGTH,
            "max_length": USERNAME_MAX_LENGTH,
            "normalization": "trim + lowercase",
            "description": "3-32 characters, starts with a letter, only letters/numbers/._-",
        },
        "password": {
            "min_length": PASSWORD_MIN_LENGTH,
            "max_length": PASSWORD_MAX_LENGTH,
            "require_letters": True,
            "require_numbers": True,
            "forbid_whitespace": True,
            "description": "8-128 characters, must include letters and numbers, spaces are not allowed",
        },
    }


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def validate_email(email: str) -> str | None:
    normalized = normalize_email(email)
    if not normalized:
        return "email required"
    if len(normalized) > 254:
        return "email is too long"
    if not EMAIL_RE.fullmatch(normalized):
        return "invalid email format"
    return None


def mask_email(email: str) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized:
        return normalized
    local, domain = normalized.split("@", 1)
    if len(local) <= 2:
        local_masked = local[0] + "*" if local else "*"
    else:
        local_masked = local[0] + ("*" * (len(local) - 2)) + local[-1]
    return f"{local_masked}@{domain}"
