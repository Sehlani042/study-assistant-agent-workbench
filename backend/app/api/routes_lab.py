from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps_auth import get_current_user, require_permission
from app.learning import build_translation_instruction, normalize_learning_preferences
from app.llm.access import resolve_user_llm_client
from app.llm.provider import normalize_provider, resolve_provider_model_metadata
from app.pipeline.agent_c import run_agent_c_with_quality, stitch_page_explanation
from app.pipeline.agent_t import run_agent_t_translation
from app.schemas import ExplanationPreviewRequest, ExplanationPreviewResponse
from app.state import AppState
from app.utils.text import top_keywords

router = APIRouter(prefix="/api/v1/lab", tags=["lab"], dependencies=[Depends(get_current_user)])


def _get_state(request: Request) -> AppState:
    state = getattr(request.app.state, "container", None)
    if state is None:
        raise RuntimeError("app state container not initialized")
    return state


@router.post("/explanation-preview", response_model=ExplanationPreviewResponse)
def explanation_preview(
    request: Request,
    body: ExplanationPreviewRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> ExplanationPreviewResponse:
    require_permission(request, "can_manage_prompts")
    state = _get_state(request)
    provider_override = str(body.llm_provider or "").strip() or None
    model_override = str(body.llm_model or "").strip() or None
    llm_client = resolve_user_llm_client(
        state=state,
        user=current_user,
        request=request,
        provider_override=provider_override,
        model_override=model_override,
    )
    learning_profile = normalize_learning_preferences(body.model_dump())
    user_id = str((current_user or {}).get("id", "")).strip() or None
    prompt_config = state.store.build_effective_prompt_config(
        user_id=user_id,
        prompt_profile=body.prompt_profile,
        task_prompt=body.task_prompt,
        prompt_overrides=body.prompt_overrides,
        learning_profile=learning_profile,
    )
    text = str(body.page_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="page_text required")
    summary = "该实验页主要围绕：" + "、".join(top_keywords(text, top_n=4)) if text else "单页解释实验。"
    page = {
        "page_no": 1,
        "text_content": text,
        "formulas": body.formulas,
    }
    group = {
        "id": "lab-group",
        "title": "实验室预览",
        "summary": summary,
    }
    global_memory = {
        "summary": summary,
        "keywords": top_keywords(text, top_n=8),
        "version": 1,
    }
    payload, quality, _model_used = run_agent_c_with_quality(
        llm_client=llm_client,
        page=page,
        global_memory=global_memory,
        group=group,
        local_context=[],
        language=body.language,
        quality_threshold=state.settings.quality_threshold,
        instruction=prompt_config.get("agent_c_instruction"),
        page_budget_seconds=max(20.0, float(getattr(state.settings, "agent_c_page_timeout_seconds", 90.0)) - 12.0),
    )
    payload = stitch_page_explanation(payload=payload, page_no=1)
    translation_preview = run_agent_t_translation(
        llm_client=llm_client,
        page_text=text,
        language=body.language,
        instruction=build_translation_instruction(learning_profile),
    )
    provider = normalize_provider(provider_override or getattr(llm_client, "provider_name", state.settings.llm_default_provider))
    model_meta = resolve_provider_model_metadata(
        settings=state.settings,
        provider=provider,
        model=model_override,
        api_key="",
    )
    return ExplanationPreviewResponse(
        explanation_preview=payload,
        translation_preview=translation_preview,
        quality_preview=quality,
        model_meta=model_meta,
    )
