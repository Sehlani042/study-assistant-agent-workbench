from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from app.llm.base import LLMClient
from app.llm.provider import (
    build_provider_client,
    default_model_for_provider,
    normalize_provider,
    shared_api_key_for_provider,
)
from app.state import AppState


def _user_id_from_payload(user: dict[str, Any]) -> str:
    return str((user or {}).get("id", "")).strip()


def _is_admin(user: dict[str, Any]) -> bool:
    return str((user or {}).get("role", "")).strip() == "admin"


def _provider_default_models(state: AppState) -> dict[str, str]:
    return {
        "openai": str(state.settings.openai_model or "gpt-5.2"),
        "gemini": str(state.settings.gemini_flash_model or "gemini-flash-latest"),
        "deepseek": str(state.settings.deepseek_model or "deepseek-chat"),
        "mock": "mock",
    }


def _resolve_effective_provider_model(
    *,
    state: AppState,
    user: dict[str, Any],
    provider_override: str | None = None,
    model_override: str | None = None,
) -> tuple[str, str]:
    user_id = _user_id_from_payload(user)
    global_settings = state.store.get_global_llm_settings(
        default_provider=normalize_provider(state.settings.llm_default_provider or state.settings.llm_provider),
        default_models=_provider_default_models(state),
    )
    user_settings = state.store.get_user_llm_settings(user_id=user_id) if user_id else {}

    provider = normalize_provider(
        provider_override or user_settings.get("provider") or global_settings.get("default_provider"),
        fallback=normalize_provider(state.settings.llm_default_provider or state.settings.llm_provider),
    )
    model = str(model_override or "").strip()
    if not model:
        user_provider = normalize_provider(user_settings.get("provider"), fallback=provider)
        if user_provider == provider and str(user_settings.get("model", "")).strip():
            model = str(user_settings.get("model", "")).strip()
        else:
            default_models = global_settings.get("default_models", {})
            if isinstance(default_models, dict):
                model = str(default_models.get(provider, "")).strip()
    if not model:
        model = default_model_for_provider(settings=state.settings, provider=provider)
    return provider, model


def can_user_use_shared_key(state: AppState, user: dict[str, Any], *, provider: str | None = None) -> bool:
    resolved_provider, _ = _resolve_effective_provider_model(
        state=state,
        user=user,
        provider_override=provider,
        model_override=None,
    )
    if resolved_provider == "mock":
        return True
    if not shared_api_key_for_provider(settings=state.settings, provider=resolved_provider):
        return False
    if _is_admin(user):
        return True
    user_id = _user_id_from_payload(user)
    if not user_id:
        return False
    return state.store.user_can_use_shared_key(user_id=user_id, provider=resolved_provider)


def requires_personal_key(state: AppState, user: dict[str, Any], *, provider: str | None = None) -> bool:
    resolved_provider, _ = _resolve_effective_provider_model(
        state=state,
        user=user,
        provider_override=provider,
        model_override=None,
    )
    if resolved_provider == "mock":
        return False
    return not can_user_use_shared_key(state, user, provider=resolved_provider)


def resolve_user_llm_client(
    *,
    state: AppState,
    user: dict[str, Any],
    request: Request | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> LLMClient:
    provider, model = _resolve_effective_provider_model(
        state=state,
        user=user,
        provider_override=provider_override,
        model_override=model_override,
    )
    if provider == "mock":
        return build_provider_client(
            provider="mock",
            settings=state.settings,
            api_key="mock-key",
            model="mock",
        )

    if can_user_use_shared_key(state, user, provider=provider):
        shared_key = shared_api_key_for_provider(settings=state.settings, provider=provider)
        return build_provider_client(
            provider=provider,
            settings=state.settings,
            api_key=shared_key,
            model=model,
        )

    user_id = _user_id_from_payload(user)
    incoming_key = ""
    runtime_key_id = f"{provider}:{user_id}"
    if request is not None:
        incoming_key = str(request.headers.get("X-LLM-Api-Key", "")).strip()
        if not incoming_key and provider == "gemini":
            incoming_key = str(request.headers.get("X-Gemini-Api-Key", "")).strip()
        if not incoming_key and provider == "openai":
            incoming_key = str(request.headers.get("X-OpenAI-Api-Key", "")).strip()
        if not incoming_key and provider == "deepseek":
            incoming_key = str(request.headers.get("X-DeepSeek-Api-Key", "")).strip()
        if incoming_key:
            state.set_runtime_user_key(user_id=runtime_key_id, api_key=incoming_key)

    api_key = incoming_key or state.get_runtime_user_key(user_id=runtime_key_id)
    if not api_key:
        api_key = state.store.get_user_personal_llm_key(
            user_id=user_id,
            provider=provider,
            encryption_secret=state.settings.llm_key_encryption_secret,
        )
    if not api_key:
        raise HTTPException(
            status_code=428,
            detail=f"{provider} api key required for this account; set it in account settings first",
        )

    return build_provider_client(
        provider=provider,
        settings=state.settings,
        api_key=api_key,
        model=model,
    )
