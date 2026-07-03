from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    data_dir: Path
    database_path: Path
    llm_provider: str
    llm_default_provider: str
    llm_key_encryption_secret: str
    gemini_api_key: str | None
    gemini_flash_model: str
    gemini_fallback_model: str
    openai_api_key: str | None
    openai_model: str
    openai_last_resort_model: str
    openai_vision_model: str
    openai_base_url: str
    deepseek_api_key: str | None
    deepseek_model: str
    deepseek_fallback_model: str
    deepseek_base_url: str
    quality_threshold: float
    agent_b_concurrency: int
    agent_c_concurrency: int
    agent_c_min_concurrency: int
    agent_c_page_timeout_seconds: float
    agent_t_concurrency: int
    auth_enabled: bool
    auth_token_ttl_hours: int
    auth_registration_mode: str
    auth_registration_invite_code: str | None
    auth_email_verification_required: bool
    auth_email_code_ttl_minutes: int
    auth_email_code_resend_seconds: int
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_from: str | None
    smtp_from_name: str | None
    smtp_use_tls: bool
    smtp_use_ssl: bool
    admin_username: str
    admin_password: str


def _resolve_database_path(raw: str, data_dir: Path) -> Path:
    if raw.startswith("sqlite:///"):
        return Path(raw.removeprefix("sqlite:///"))
    if raw.startswith("sqlite://"):
        return Path(raw.removeprefix("sqlite://"))
    return data_dir / "assistant.db"


def _parse_positive_int(value: str | None, *, default: int, min_value: int = 1, max_value: int = 64) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_secret_file(path_value: str | None) -> str | None:
    path_text = str(path_value or "").strip()
    if not path_text:
        return None
    try:
        return Path(path_text).expanduser().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _parse_positive_float(value: str | None, *, default: float, min_value: float = 1.0, max_value: float = 600.0) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def get_settings() -> Settings:
    base_dir = Path(__file__).resolve().parents[1]
    data_dir = Path(os.environ.get("DATA_DIR", str(base_dir.parent / "data"))).resolve()
    database_url = os.environ.get("DATABASE_URL", f"sqlite:///{data_dir / 'assistant.db'}")
    database_path = _resolve_database_path(database_url, data_dir)
    llm_provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
    llm_default_provider = os.environ.get("LLM_DEFAULT_PROVIDER", llm_provider).strip().lower() or llm_provider
    smtp_from = str(os.environ.get("SMTP_FROM", "")).strip()
    smtp_from_email = str(os.environ.get("SMTP_FROM_EMAIL", "")).strip()
    smtp_from_name = str(os.environ.get("SMTP_FROM_NAME", "")).strip()
    llm_key_encryption_secret = (
        str(os.environ.get("LLM_KEY_ENCRYPTION_SECRET", "")).strip()
        or str(os.environ.get("ADMIN_PASSWORD", "")).strip()
        or "study-assistant-local-secret"
    )
    openai_base_url = str(os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).strip()
    if openai_base_url.endswith("/"):
        openai_base_url = openai_base_url[:-1]
    deepseek_base_url = str(os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).strip()
    if deepseek_base_url.endswith("/"):
        deepseek_base_url = deepseek_base_url[:-1]
    deepseek_api_key = (
        os.environ.get("DEEPSEEK_API_KEY")
        or _read_secret_file(os.environ.get("DEEPSEEK_API_KEY_PATH"))
    )
    openai_api_key = (
        os.environ.get("OPENAI_API_KEY")
        or _read_secret_file(os.environ.get("OPENAI_API_KEY_PATH"))
    )

    return Settings(
        base_dir=base_dir,
        data_dir=data_dir,
        database_path=database_path,
        llm_provider=llm_provider,
        llm_default_provider=llm_default_provider,
        llm_key_encryption_secret=llm_key_encryption_secret,
        gemini_api_key=os.environ.get("GEMINI_API_KEY"),
        gemini_flash_model=os.environ.get("GEMINI_FLASH_MODEL", "gemini-3.1-flash-lite"),
        gemini_fallback_model=os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.1-pro"),
        openai_api_key=openai_api_key,
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-5.2"),
        openai_last_resort_model=os.environ.get("OPENAI_LAST_RESORT_MODEL", "gpt-5.2-mini"),
        openai_vision_model=os.environ.get("OPENAI_VISION_MODEL", "gpt-5.4-mini"),
        openai_base_url=openai_base_url,
        deepseek_api_key=deepseek_api_key,
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        deepseek_fallback_model=os.environ.get("DEEPSEEK_FALLBACK_MODEL", "deepseek-reasoner"),
        deepseek_base_url=deepseek_base_url,
        quality_threshold=float(os.environ.get("QUALITY_THRESHOLD", "80")),
        agent_b_concurrency=_parse_positive_int(
            os.environ.get("AGENT_B_CONCURRENCY"),
            default=3,
            min_value=1,
            max_value=32,
        ),
        agent_c_concurrency=_parse_positive_int(
            os.environ.get("AGENT_C_CONCURRENCY"),
            default=4,
            min_value=1,
            max_value=64,
        ),
        agent_c_min_concurrency=_parse_positive_int(
            os.environ.get("AGENT_C_MIN_CONCURRENCY"),
            default=2,
            min_value=1,
            max_value=64,
        ),
        agent_c_page_timeout_seconds=_parse_positive_float(
            os.environ.get("AGENT_C_PAGE_TIMEOUT_SECONDS"),
            default=180.0,
            min_value=5.0,
            max_value=1200.0,
        ),
        agent_t_concurrency=_parse_positive_int(
            os.environ.get("AGENT_T_CONCURRENCY"),
            default=3,
            min_value=1,
            max_value=16,
        ),
        auth_enabled=_parse_bool(os.environ.get("AUTH_ENABLED"), default=True),
        auth_token_ttl_hours=_parse_positive_int(
            os.environ.get("AUTH_TOKEN_TTL_HOURS"),
            default=168,
            min_value=1,
            max_value=24 * 365,
        ),
        auth_registration_mode=(
            str(os.environ.get("AUTH_REGISTRATION_MODE", "open")).strip().lower()
            if str(os.environ.get("AUTH_REGISTRATION_MODE", "open")).strip().lower() in {"open", "invite", "closed"}
            else "open"
        ),
        auth_registration_invite_code=(str(os.environ.get("AUTH_REGISTRATION_INVITE_CODE", "")).strip() or None),
        auth_email_verification_required=_parse_bool(
            os.environ.get("AUTH_EMAIL_VERIFICATION_REQUIRED"),
            default=False,
        ),
        auth_email_code_ttl_minutes=_parse_positive_int(
            os.environ.get("AUTH_EMAIL_CODE_TTL_MINUTES"),
            default=10,
            min_value=1,
            max_value=120,
        ),
        auth_email_code_resend_seconds=_parse_positive_int(
            os.environ.get("AUTH_EMAIL_CODE_RESEND_SECONDS"),
            default=60,
            min_value=1,
            max_value=600,
        ),
        smtp_host=(str(os.environ.get("SMTP_HOST", "")).strip() or None),
        smtp_port=_parse_positive_int(
            os.environ.get("SMTP_PORT"),
            default=587,
            min_value=1,
            max_value=65535,
        ),
        smtp_username=(str(os.environ.get("SMTP_USERNAME", "")).strip() or None),
        smtp_password=(str(os.environ.get("SMTP_PASSWORD", "")).strip() or None),
        smtp_from=(smtp_from or smtp_from_email or None),
        smtp_from_name=(smtp_from_name or None),
        smtp_use_tls=_parse_bool(os.environ.get("SMTP_USE_TLS"), default=True),
        smtp_use_ssl=_parse_bool(os.environ.get("SMTP_USE_SSL"), default=False),
        admin_username=str(os.environ.get("ADMIN_USERNAME", "admin")).strip() or "admin",
        admin_password=str(os.environ.get("ADMIN_PASSWORD", "change-me-in-production-123")).strip()
        or "change-me-in-production-123",
    )
