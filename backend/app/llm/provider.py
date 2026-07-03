from __future__ import annotations

from typing import Any

from app.config import Settings
from app.llm.base import LLMClient
from app.llm.deepseek_client import DeepSeekClient
from app.llm.gemini_client import (
    GEMINI_FLASH_LITE_STABLE_MODEL,
    GEMINI_FLASH_LITE_TARGET_LABEL,
    GEMINI_FLASH_LITE_TARGET_MODEL,
    GeminiClient,
)
from app.llm.mock_client import MockLLMClient
from app.llm.openai_client import OpenAIClient


SUPPORTED_PROVIDERS = {"openai", "gemini", "deepseek", "mock"}


def normalize_provider(provider: str | None, *, fallback: str = "openai") -> str:
    value = str(provider or "").strip().lower()
    if value in SUPPORTED_PROVIDERS:
        return value
    return str(fallback or "openai").strip().lower() or "openai"


def default_model_for_provider(*, settings: Settings, provider: str) -> str:
    normalized = normalize_provider(provider, fallback=settings.llm_default_provider)
    if normalized == "gemini":
        return settings.gemini_flash_model
    if normalized == "openai":
        return settings.openai_model
    if normalized == "deepseek":
        return settings.deepseek_model
    return "mock"


def shared_api_key_for_provider(*, settings: Settings, provider: str) -> str | None:
    normalized = normalize_provider(provider, fallback=settings.llm_default_provider)
    if normalized == "gemini":
        return settings.gemini_api_key
    if normalized == "openai":
        return settings.openai_api_key
    if normalized == "deepseek":
        return settings.deepseek_api_key
    return "mock-key"


def build_provider_client(
    *,
    provider: str,
    settings: Settings,
    api_key: str | None = None,
    model: str | None = None,
) -> LLMClient:
    normalized = normalize_provider(provider, fallback=settings.llm_default_provider)
    effective_model = str(model or "").strip() or default_model_for_provider(settings=settings, provider=normalized)
    effective_key = str(api_key or "").strip() or str(shared_api_key_for_provider(settings=settings, provider=normalized) or "")

    if normalized == "gemini" and effective_key:
        return GeminiClient(
            api_key=effective_key,
            flash_model=effective_model,
            fallback_model=settings.gemini_fallback_model,
        )
    if normalized == "openai" and effective_key:
        return OpenAIClient(
            api_key=effective_key,
            model=effective_model,
            base_url=settings.openai_base_url,
        )
    if normalized == "deepseek" and effective_key:
        return DeepSeekClient(
            api_key=effective_key,
            model=effective_model,
            base_url=settings.deepseek_base_url,
        )
    return MockLLMClient()


def resolve_provider_model_metadata(
    *,
    settings: Settings,
    provider: str,
    model: str | None = None,
    api_key: str | None = None,
) -> dict[str, str]:
    normalized = normalize_provider(provider, fallback=settings.llm_default_provider)
    requested_model = str(model or "").strip() or default_model_for_provider(settings=settings, provider=normalized)
    if normalized == "gemini":
        client = GeminiClient(
            api_key=str(api_key or "").strip(),
            flash_model=requested_model,
            fallback_model=settings.gemini_fallback_model,
        )
        selection = client.describe_flash_model(requested_model)
        return {
            "provider": normalized,
            "model": requested_model,
            "display_label": selection.display_label,
            "resolved_model": selection.resolved_model,
            "resolution_source": selection.resolution_source,
        }
    if normalized == "openai":
        return {
            "provider": normalized,
            "model": requested_model,
            "display_label": requested_model,
            "resolved_model": requested_model,
            "resolution_source": "exact",
        }
    if normalized == "deepseek":
        return {
            "provider": normalized,
            "model": requested_model,
            "display_label": requested_model,
            "resolved_model": requested_model,
            "resolution_source": "exact",
        }
    return {
        "provider": normalized,
        "model": "mock",
        "display_label": "Mock",
        "resolved_model": "mock",
        "resolution_source": "exact",
    }


def recommended_models_for_provider(
    *,
    settings: Settings,
    provider: str,
    api_key: str | None = None,
) -> list[dict[str, str]]:
    normalized = normalize_provider(provider, fallback=settings.llm_default_provider)
    if normalized == "gemini":
        auto_meta = resolve_provider_model_metadata(
            settings=settings,
            provider="gemini",
            model=GEMINI_FLASH_LITE_TARGET_MODEL,
            api_key=api_key,
        )
        stable_meta = resolve_provider_model_metadata(
            settings=settings,
            provider="gemini",
            model=GEMINI_FLASH_LITE_STABLE_MODEL,
            api_key=api_key,
        )
        return [
            {
                "id": GEMINI_FLASH_LITE_TARGET_MODEL,
                "display_label": GEMINI_FLASH_LITE_TARGET_LABEL,
                "resolved_model": auto_meta["resolved_model"],
                "resolution_source": auto_meta["resolution_source"],
            },
            {
                "id": GEMINI_FLASH_LITE_STABLE_MODEL,
                "display_label": GEMINI_FLASH_LITE_STABLE_MODEL,
                "resolved_model": stable_meta["resolved_model"],
                "resolution_source": stable_meta["resolution_source"],
            },
        ]
    if normalized == "openai":
        return [
            {
                "id": settings.openai_model,
                "display_label": settings.openai_model,
                "resolved_model": settings.openai_model,
                "resolution_source": "exact",
            },
            {
                "id": settings.openai_last_resort_model,
                "display_label": settings.openai_last_resort_model,
                "resolved_model": settings.openai_last_resort_model,
                "resolution_source": "exact",
            },
        ]
    if normalized == "deepseek":
        return [
            {
                "id": settings.deepseek_model,
                "display_label": settings.deepseek_model,
                "resolved_model": settings.deepseek_model,
                "resolution_source": "exact",
            },
            {
                "id": settings.deepseek_fallback_model,
                "display_label": settings.deepseek_fallback_model,
                "resolved_model": settings.deepseek_fallback_model,
                "resolution_source": "exact",
            },
        ]
    return [
        {
            "id": "mock",
            "display_label": "Mock",
            "resolved_model": "mock",
            "resolution_source": "exact",
        }
    ]


def build_llm_client(settings: Settings) -> LLMClient:
    return build_provider_client(
        provider=settings.llm_default_provider or settings.llm_provider,
        settings=settings,
        api_key=shared_api_key_for_provider(
            settings=settings,
            provider=settings.llm_default_provider or settings.llm_provider,
        ),
        model=default_model_for_provider(
            settings=settings,
            provider=settings.llm_default_provider or settings.llm_provider,
        ),
    )
