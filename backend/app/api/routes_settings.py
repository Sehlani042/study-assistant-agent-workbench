from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.api.deps_auth import get_current_user, require_permission
from app.learning import normalize_learning_preferences
from app.llm.provider import (
    default_model_for_provider,
    normalize_provider,
    recommended_models_for_provider,
    resolve_provider_model_metadata,
    shared_api_key_for_provider,
)
from app.schemas import (
    LLMFallbackChainPayload,
    LLMFallbackChainUpdateRequest,
    LLMProviderOptionPayload,
    LLMRecommendedModelPayload,
    LLMSettingsGlobalPayload,
    LLMSettingsPayload,
    LLMSettingsPayload,
    LLMSettingsUpdateRequest,
    LLMSettingsUserPayload,
    LearningPreferencesResponse,
    LearningPreferencesUpdateRequest,
    PromptConfigPayload,
    PromptConfigUpdateRequest,
)
from app.state import AppState

router = APIRouter(prefix="/api/v1/settings", tags=["settings"], dependencies=[Depends(get_current_user)])


def _get_state(request: Request) -> AppState:
    state = getattr(request.app.state, "container", None)
    if state is None:
        raise RuntimeError("app state container not initialized")
    return state


def _fallback_models(state: AppState) -> dict[str, str]:
    return {
        "openai": str(state.settings.openai_model or "gpt-5.2"),
        "gemini": str(state.settings.gemini_flash_model or "gemini-3.1-flash-lite"),
        "deepseek": str(state.settings.deepseek_model or "deepseek-chat"),
        "mock": "mock",
    }


def _default_fallback_chain(state: AppState) -> list[str]:
    openai_model = str(state.settings.openai_last_resort_model or state.settings.openai_model or "gpt-5.2-mini").strip()
    return [
        "gemini:flash",
        "gemini:pro",
        f"openai:{openai_model}",
    ]


def _resolve_user_api_key_for_provider(*, state: AppState, user_id: str, provider: str) -> str:
    normalized_provider = normalize_provider(provider, fallback=state.settings.llm_default_provider)
    if normalized_provider == "mock":
        return "mock-key"
    shared_key = shared_api_key_for_provider(settings=state.settings, provider=normalized_provider)
    if shared_key:
        return str(shared_key)
    runtime_key = state.get_runtime_user_key(user_id=f"{normalized_provider}:{user_id}")
    if runtime_key:
        return runtime_key
    if not user_id:
        return ""
    return state.store.get_user_personal_llm_key(
        user_id=user_id,
        provider=normalized_provider,
        encryption_secret=state.settings.llm_key_encryption_secret,
    )


def _provider_options_payload(*, state: AppState, user_id: str) -> list[LLMProviderOptionPayload]:
    items: list[LLMProviderOptionPayload] = []
    for provider, label in (
        ("gemini", "Google Gemini"),
        ("openai", "OpenAI"),
        ("deepseek", "DeepSeek"),
        ("mock", "Mock"),
    ):
        provider_key = _resolve_user_api_key_for_provider(state=state, user_id=user_id, provider=provider)
        recommended = recommended_models_for_provider(
            settings=state.settings,
            provider=provider,
            api_key=provider_key,
        )
        items.append(
            LLMProviderOptionPayload(
                id=provider,
                label=label,
                recommended_models=[LLMRecommendedModelPayload(**item) for item in recommended],
            )
        )
    return items


def _build_llm_settings_payload(*, state: AppState, user_id: str) -> LLMSettingsPayload:
    global_defaults = state.store.get_global_llm_settings(
        default_provider=normalize_provider(state.settings.llm_default_provider or state.settings.llm_provider),
        default_models=_fallback_models(state),
    )
    user_defaults = state.store.get_user_llm_settings(user_id=user_id) if user_id else {}

    global_provider = normalize_provider(global_defaults.get("default_provider"), fallback=state.settings.llm_default_provider)
    global_models = global_defaults.get("default_models", {})
    if not isinstance(global_models, dict):
        global_models = _fallback_models(state)

    user_provider = normalize_provider(user_defaults.get("provider"), fallback=global_provider)
    user_model = str(user_defaults.get("model", "")).strip()
    if not user_model:
        user_model = str(global_models.get(user_provider, "")).strip() or default_model_for_provider(
            settings=state.settings,
            provider=user_provider,
        )

    effective_provider = user_provider
    effective_model = user_model or str(global_models.get(effective_provider, "")).strip()
    if not effective_model:
        effective_model = default_model_for_provider(settings=state.settings, provider=effective_provider)
    effective_key = _resolve_user_api_key_for_provider(state=state, user_id=user_id, provider=effective_provider)
    effective_meta = resolve_provider_model_metadata(
        settings=state.settings,
        provider=effective_provider,
        model=effective_model,
        api_key=effective_key,
    )

    return LLMSettingsPayload(
        global_default=LLMSettingsGlobalPayload(
            default_provider=global_provider,
            default_models={str(k): str(v) for k, v in global_models.items()},
        ),
        user_default=LLMSettingsUserPayload(provider=user_provider, model=user_model),
        effective=LLMSettingsUserPayload(
            provider=effective_provider,
            model=effective_model,
            display_label=effective_meta["display_label"],
            resolved_model=effective_meta["resolved_model"],
            resolution_source=effective_meta["resolution_source"],
        ),
        providers=_provider_options_payload(state=state, user_id=user_id),
    )


def _build_fallback_chain_payload(*, state: AppState, user_id: str) -> LLMFallbackChainPayload:
    default_chain = _default_fallback_chain(state)
    global_chain = state.store.get_global_fallback_chain(default_chain=default_chain)
    user_chain = state.store.get_user_fallback_chain(user_id=user_id) if user_id else []
    effective = list(user_chain or global_chain or default_chain)
    source = "user" if user_chain else "global"
    return LLMFallbackChainPayload(
        global_default=global_chain,
        user_default=user_chain,
        effective=effective,
        source=source,
    )


@router.get("/prompt", response_model=PromptConfigPayload)
def get_prompt_config(
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> PromptConfigPayload:
    state = _get_state(request)
    user_id = str(current_user.get("id", ""))
    payload = state.store.get_prompt_config(user_id=user_id)
    has_custom = state.store.has_custom_prompt_config(user_id=user_id)
    return PromptConfigPayload(
        agent_a_instruction=str(payload.get("agent_a_instruction", "")),
        agent_b_instruction=str(payload.get("agent_b_instruction", "")),
        agent_c_instruction=str(payload.get("agent_c_instruction", "")),
        chat_instruction=str(payload.get("chat_instruction", "")),
        formula_instruction=str(payload.get("formula_instruction", "")),
        source="personal" if has_custom else "default",
        has_custom=has_custom,
    )


@router.get("/learning", response_model=LearningPreferencesResponse)
def get_learning_preferences(
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> LearningPreferencesResponse:
    state = _get_state(request)
    user_id = str(current_user.get("id", "")).strip()
    payload = state.store.get_user_learning_preferences(user_id=user_id)
    source = "personal"
    if payload == normalize_learning_preferences({}):
        source = "default"
    return LearningPreferencesResponse(source=source, **payload)


@router.put("/learning", response_model=LearningPreferencesResponse)
def update_learning_preferences(
    request: Request,
    body: LearningPreferencesUpdateRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> LearningPreferencesResponse:
    state = _get_state(request)
    user_id = str(current_user.get("id", "")).strip()
    payload = state.store.save_user_learning_preferences(
        user_id=user_id,
        learner_level=body.learner_level,
        learning_goal=body.learning_goal,
        depth_mode=body.depth_mode,
        attention_support=body.attention_support,
    )
    return LearningPreferencesResponse(source="personal", **payload)


@router.put("/prompt", response_model=PromptConfigPayload)
def update_prompt_config(
    request: Request,
    body: PromptConfigUpdateRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> PromptConfigPayload:
    state = _get_state(request)
    user_id = str(current_user.get("id", ""))
    payload = state.store.save_prompt_config(
        agent_a_instruction=body.agent_a_instruction,
        agent_b_instruction=body.agent_b_instruction,
        agent_c_instruction=body.agent_c_instruction,
        chat_instruction=body.chat_instruction,
        formula_instruction=body.formula_instruction,
        user_id=user_id,
    )
    return PromptConfigPayload(
        agent_a_instruction=str(payload.get("agent_a_instruction", "")),
        agent_b_instruction=str(payload.get("agent_b_instruction", "")),
        agent_c_instruction=str(payload.get("agent_c_instruction", "")),
        chat_instruction=str(payload.get("chat_instruction", "")),
        formula_instruction=str(payload.get("formula_instruction", "")),
        source="personal",
        has_custom=True,
    )


@router.post("/prompt/reset", response_model=PromptConfigPayload)
def reset_prompt_config(
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> PromptConfigPayload:
    state = _get_state(request)
    user_id = str(current_user.get("id", ""))
    payload = state.store.reset_prompt_config(user_id=user_id)
    return PromptConfigPayload(
        agent_a_instruction=str(payload.get("agent_a_instruction", "")),
        agent_b_instruction=str(payload.get("agent_b_instruction", "")),
        agent_c_instruction=str(payload.get("agent_c_instruction", "")),
        chat_instruction=str(payload.get("chat_instruction", "")),
        formula_instruction=str(payload.get("formula_instruction", "")),
        source="default",
        has_custom=False,
    )


@router.get("/prompt/default", response_model=PromptConfigPayload)
def get_default_prompt_config(request: Request) -> PromptConfigPayload:
    state = _get_state(request)
    payload = state.store.get_default_prompt_config()
    return PromptConfigPayload(
        agent_a_instruction=str(payload.get("agent_a_instruction", "")),
        agent_b_instruction=str(payload.get("agent_b_instruction", "")),
        agent_c_instruction=str(payload.get("agent_c_instruction", "")),
        chat_instruction=str(payload.get("chat_instruction", "")),
        formula_instruction=str(payload.get("formula_instruction", "")),
        source="default",
        has_custom=False,
    )


@router.put("/prompt/default", response_model=PromptConfigPayload)
def update_default_prompt_config(request: Request, body: PromptConfigUpdateRequest) -> PromptConfigPayload:
    require_permission(request, "can_manage_prompts")
    state = _get_state(request)
    payload = state.store.save_prompt_config(
        agent_a_instruction=body.agent_a_instruction,
        agent_b_instruction=body.agent_b_instruction,
        agent_c_instruction=body.agent_c_instruction,
        chat_instruction=body.chat_instruction,
        formula_instruction=body.formula_instruction,
    )
    return PromptConfigPayload(
        agent_a_instruction=str(payload.get("agent_a_instruction", "")),
        agent_b_instruction=str(payload.get("agent_b_instruction", "")),
        agent_c_instruction=str(payload.get("agent_c_instruction", "")),
        chat_instruction=str(payload.get("chat_instruction", "")),
        formula_instruction=str(payload.get("formula_instruction", "")),
        source="default",
        has_custom=False,
    )


@router.get("/llm", response_model=LLMSettingsPayload)
def get_llm_settings(
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> LLMSettingsPayload:
    state = _get_state(request)
    user_id = str(current_user.get("id", "")).strip()
    return _build_llm_settings_payload(state=state, user_id=user_id)


@router.put("/llm", response_model=LLMSettingsPayload)
def update_llm_settings(
    request: Request,
    body: LLMSettingsUpdateRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> LLMSettingsPayload:
    state = _get_state(request)
    user_id = str(current_user.get("id", "")).strip()
    scope = str(body.scope or "user").strip().lower()
    if scope not in {"user", "global", "both"}:
        scope = "user"

    if scope in {"global", "both"}:
        require_permission(request, "can_manage_accounts")
        current_global = state.store.get_global_llm_settings(
            default_provider=normalize_provider(state.settings.llm_default_provider or state.settings.llm_provider),
            default_models=_fallback_models(state),
        )
        target_provider = normalize_provider(body.provider or current_global.get("default_provider"))
        next_models: dict[str, str] = {}
        if body.model is not None and str(body.model).strip():
            next_models[target_provider] = str(body.model).strip()
        state.store.save_global_llm_settings(
            default_provider=target_provider if body.provider is not None else None,
            default_models=next_models or None,
            fallback_provider=normalize_provider(state.settings.llm_default_provider or state.settings.llm_provider),
            fallback_models=_fallback_models(state),
        )

    if scope in {"user", "both"}:
        provider = normalize_provider(body.provider) if body.provider is not None else None
        model = str(body.model).strip() if body.model is not None else None
        state.store.save_user_llm_settings(
            user_id=user_id,
            provider=provider,
            model=model,
        )

    return _build_llm_settings_payload(state=state, user_id=user_id)


@router.get("/llm/fallback-chain", response_model=LLMFallbackChainPayload)
def get_llm_fallback_chain(
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> LLMFallbackChainPayload:
    state = _get_state(request)
    user_id = str(current_user.get("id", "")).strip()
    return _build_fallback_chain_payload(state=state, user_id=user_id)


@router.put("/llm/fallback-chain", response_model=LLMFallbackChainPayload)
def update_llm_fallback_chain(
    request: Request,
    body: LLMFallbackChainUpdateRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> LLMFallbackChainPayload:
    state = _get_state(request)
    user_id = str(current_user.get("id", "")).strip()
    scope = str(body.scope or "user").strip().lower()
    if scope not in {"user", "global", "both"}:
        scope = "user"
    default_chain = _default_fallback_chain(state)

    if scope in {"global", "both"}:
        require_permission(request, "can_manage_accounts")
        state.store.save_global_fallback_chain(chain=body.chain, default_chain=default_chain)

    if scope in {"user", "both"}:
        state.store.save_user_fallback_chain(user_id=user_id, chain=body.chain, default_chain=default_chain)

    return _build_fallback_chain_payload(state=state, user_id=user_id)
