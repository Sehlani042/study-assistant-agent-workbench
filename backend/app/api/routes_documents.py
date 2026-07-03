from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

from app.api.deps_auth import get_current_user
from app.learning import normalize_learning_preferences
from app.llm.access import resolve_user_llm_client
from app.schemas import (
    ChatRequest,
    ChatHistoryItem,
    ChatHistoryResponse,
    ChatResponse,
    DocumentPromptSnapshotResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentRunDetail,
    DocumentRunListResponse,
    DocumentRunSummary,
    DocumentStatusResponse,
    GroupPayload,
    LearningPreferencesPayload,
    OutlineResponse,
    PageResponse,
    PipelineDetailPayload,
    PromptFields,
    ProgressPayload,
    RegenerateResponse,
    UploadResponse,
    LearningArcPayload,
)
from app.state import AppState

router = APIRouter(prefix="/api/v1/documents", tags=["documents"], dependencies=[Depends(get_current_user)])


ALLOWED_EXTENSIONS = {
    ".pdf": "pdf",
    ".pptx": "pptx",
}

STAGE_LABELS = {
    "queued": "排队中",
    "preprocess:convert": "预处理中：文件转换",
    "preprocess:extract": "预处理中：提取文本、公式与页面块",
    "preprocess:index": "预处理中：写入页面索引",
    "translate:blocks": "生成覆盖翻译",
    "translate:overlay": "整理翻译覆盖层",
    "agent_a:overview": "Agent A：文档综述与分组",
    "agent_b:groups": "Agent B：分组总结",
    "agent_c:start": "Agent C：逐页解释准备中",
    "agent_c1:draft": "Agent C1：并发草稿生成中",
    "agent_c2:stitch": "Agent C2：顺序连贯拼接中",
    "quality:gate": "质量门禁：验收与打分",
    "completed": "处理完成",
    "failed": "处理失败",
    "canceled": "任务已取消",
}


def _get_state(request: Request) -> AppState:
    state = getattr(request.app.state, "container", None)
    if state is None:
        raise RuntimeError("app state container not initialized")
    return state


def _is_admin_user(user: dict[str, Any]) -> bool:
    return str((user or {}).get("role", "")).strip() == "admin"


def _current_user_id(user: dict[str, Any]) -> str:
    return str((user or {}).get("id", "")).strip()


def _enforce_document_access(
    *,
    state: AppState,
    document_id: str,
    current_user: dict[str, Any],
) -> dict[str, Any]:
    doc = state.store.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    if not state.settings.auth_enabled:
        return doc
    if _is_admin_user(current_user):
        return doc
    owner_user_id = str(doc.get("owner_user_id", "")).strip()
    if not owner_user_id or owner_user_id != _current_user_id(current_user):
        raise HTTPException(status_code=404, detail="document not found")
    return doc


def _build_progress(total_pages: int, processed_pages: int) -> ProgressPayload:
    if total_pages <= 0:
        percent = 0.0
    else:
        percent = round((processed_pages / total_pages) * 100, 2)
    return ProgressPayload(total_pages=total_pages, processed_pages=processed_pages, percent=percent)


def _stage_label(stage: str, *, status: str) -> str:
    stage = (stage or "").strip()
    if stage in STAGE_LABELS:
        return STAGE_LABELS[stage]
    if stage.startswith("agent_b:groups"):
        suffix = stage.removeprefix("agent_b:groups")
        if suffix.startswith(":w"):
            workers = suffix.removeprefix(":w")
            return f"Agent B：分组总结中 (并发 {workers})"
        return STAGE_LABELS["agent_b:groups"]
    if stage.startswith("translate:blocks:start"):
        suffix = stage.removeprefix("translate:blocks:start")
        if suffix.startswith(":w"):
            workers = suffix.removeprefix(":w")
            return f"生成覆盖翻译中 (并发 {workers})"
        return STAGE_LABELS["translate:blocks"]
    if stage.startswith("translate:blocks:"):
        progress = stage.removeprefix("translate:blocks:")
        return f"生成覆盖翻译中 ({progress})"
    if stage.startswith("translate:overlay"):
        return STAGE_LABELS["translate:overlay"]
    if stage.startswith("agent_c:start"):
        suffix = stage.removeprefix("agent_c:start")
        if suffix.startswith(":w"):
            workers = suffix.removeprefix(":w")
            return f"Agent C：逐页解释准备中 (并发 {workers})"
        return STAGE_LABELS["agent_c:start"]
    if stage.startswith("agent_c1:draft:start"):
        suffix = stage.removeprefix("agent_c1:draft:start")
        if suffix.startswith(":w"):
            workers = suffix.removeprefix(":w")
            return f"Agent C1：并发草稿生成中 (并发 {workers})"
        return STAGE_LABELS["agent_c1:draft"]
    if stage.startswith("agent_c1:draft:"):
        progress = stage.removeprefix("agent_c1:draft:")
        return f"Agent C1：并发草稿生成中 ({progress})"
    if stage.startswith("agent_c2:stitch:start"):
        suffix = stage.removeprefix("agent_c2:stitch:start:")
        return f"Agent C2：顺序连贯拼接准备中 ({suffix})"
    if stage.startswith("agent_c2:stitch:"):
        progress = stage.removeprefix("agent_c2:stitch:")
        return f"Agent C2：顺序连贯拼接中 ({progress})"
    if stage.startswith("agent_c:page:"):
        suffix = stage.removeprefix("agent_c:page:")
        parts = suffix.split(":")
        progress = parts[0]
        worker_text = ""
        if len(parts) > 1 and parts[1].startswith("w"):
            worker_text = f"，并发 {parts[1].removeprefix('w')}"
        return f"Agent C：逐页解释中 ({progress}{worker_text})"
    if stage.startswith("quality:gate"):
        return STAGE_LABELS["quality:gate"]
    if status == "completed":
        return STAGE_LABELS["completed"]
    if status == "failed":
        return STAGE_LABELS["failed"]
    if status == "canceled":
        return STAGE_LABELS["canceled"]
    if status == "queued":
        return STAGE_LABELS["queued"]
    return "处理中"


def _normalize_stage(stage: str, *, status: str) -> str:
    normalized_status = str(status or "").strip() or "queued"
    if normalized_status in {"completed", "failed", "canceled"}:
        return normalized_status
    candidate = str(stage or "").strip()
    return candidate or normalized_status


def _build_page_status_hint(
    *,
    doc: dict[str, Any],
    page_no: int,
    explanation: dict[str, Any] | None,
) -> str:
    if isinstance(explanation, dict):
        explicit = str(explanation.get("statusHint", "")).strip()
        if explicit:
            return explicit
        quality_raw = explanation.get("quality", {})
        quality = quality_raw if isinstance(quality_raw, dict) else {}
        if bool(quality.get("pass", False)):
            return "该页解释已通过自动质控。"
        return "该页解释可用，但建议结合左侧原文复核。"

    status = str(doc.get("status", "")).strip().lower()
    processed_pages = int(doc.get("processed_pages", 0) or 0)
    total_pages = int(doc.get("total_pages", 0) or 0)
    if status == "processing":
        if page_no > processed_pages:
            return "该页翻译覆盖层仍在生成中，请稍后自动刷新。"
        return "该页翻译已准备好；如需讲解，请点击“解释这一页”。"
    if status == "failed":
        return "文档处理失败；可先查看左侧原文，再尝试重生成本页。"
    if status == "canceled":
        return "任务已取消；可重新上传或重启处理。"
    if status == "completed" and total_pages > 0:
        return "该页翻译已就绪；如需讲解，可手动解释本页。"
    return "等待文档处理。"


def _collect_prompt_overrides(
    *,
    agent_a_instruction: str | None,
    agent_b_instruction: str | None,
    agent_c_instruction: str | None,
    chat_instruction: str | None,
    formula_instruction: str | None,
) -> dict[str, str]:
    fields = {
        "agent_a_instruction": agent_a_instruction,
        "agent_b_instruction": agent_b_instruction,
        "agent_c_instruction": agent_c_instruction,
        "chat_instruction": chat_instruction,
        "formula_instruction": formula_instruction,
    }
    overrides: dict[str, str] = {}
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            overrides[key] = text
    return overrides


def _default_fallback_chain(state: AppState) -> list[str]:
    openai_model = str(state.settings.openai_last_resort_model or state.settings.openai_model or "gpt-5.2-mini").strip()
    return ["gemini:flash", "gemini:pro", f"openai:{openai_model}"]


def _parse_fallback_chain(raw: str | None) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [item.strip() for item in text.split(",") if item.strip()]


def _build_quality_hint(*, explanation: dict[str, Any] | None, status_hint: str) -> str:
    if not isinstance(explanation, dict):
        return status_hint or "先看左侧原文标题和第一条要点。"
    quality_raw = explanation.get("quality", {})
    quality = quality_raw if isinstance(quality_raw, dict) else {}
    if bool(quality.get("pass", False)):
        return "先看“结论 + 3步讲解”，再做“你现在就做”，最后用自检问题确认是否理解。"
    feedback = [str(item).strip() for item in (quality.get("feedback", []) or []) if str(item).strip()]
    first = feedback[0] if feedback else ""
    if "覆盖" in first:
        return "先回到左侧本页，圈出 2-3 个关键词，再点“重生成本页解释”。"
    if "引用" in first:
        return "这页证据绑定偏弱，建议先看左侧原文对应段落，再触发重生成。"
    if "连续" in first:
        return "先看上一页最后一条，再看本页结论与下一页预告，确认链路后重生成。"
    if "模板" in first or "重复" in first:
        return "当前解释偏模板化，建议重生成并优先阅读“结论+例子”两块。"
    return "这页可先看结论与例子，再按微任务自检；若仍不清晰，重生成本页。"


def _build_content_density(explanation: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(explanation, dict):
        return {"visible_blocks": 0, "visible_tokens": 0}
    clarity_raw = explanation.get("clarity", {})
    clarity = clarity_raw if isinstance(clarity_raw, dict) else {}
    continuity_raw = explanation.get("continuity", {})
    continuity = continuity_raw if isinstance(continuity_raw, dict) else {}
    micro_raw = explanation.get("microTask", {})
    micro = micro_raw if isinstance(micro_raw, dict) else {}

    visible_texts: list[str] = []
    visible_texts.append(str(clarity.get("conclusion", "")).strip())
    steps = clarity.get("steps", [])
    if isinstance(steps, list):
        visible_texts.extend(str(item).strip() for item in steps[:3])
    visible_texts.append(str(clarity.get("example", "")).strip())
    visible_texts.append(str(micro.get("doNow", "")).strip())
    visible_texts.append(str(continuity.get("prevBridge", "")).strip())
    visible_texts.append(str(continuity.get("nextPreview", "")).strip())
    filled = [item for item in visible_texts if item]
    token_count = sum(max(1, len(item.split())) for item in filled)
    return {
        "visible_blocks": min(6, max(0, len(filled))),
        "visible_tokens": int(token_count),
    }


def _build_page_view_model(explanation: dict[str, Any] | None) -> dict[str, object]:
    collapsed = [
        "scaffold",
        "keyPoints",
        "teaching",
        "evidenceBlocks",
        "citations",
        "formulaBlocks",
        "memoryUsed",
    ]
    if not isinstance(explanation, dict):
        return {"mode": "focus", "collapsedSections": collapsed}
    return {"mode": "focus", "collapsedSections": collapsed}


def _normalize_learning_form(
    *,
    learner_level: str | None = None,
    learning_goal: str | None = None,
    depth_mode: str | None = None,
    attention_support: str | None = None,
    fallback: dict[str, Any] | None = None,
) -> dict[str, str]:
    payload = dict(fallback or {})
    if learner_level is not None:
        payload["learner_level"] = learner_level
    if learning_goal is not None:
        payload["learning_goal"] = learning_goal
    if depth_mode is not None:
        payload["depth_mode"] = depth_mode
    if attention_support is not None:
        payload["attention_support"] = attention_support
    return normalize_learning_preferences(payload)


def _job_is_active(job: dict[str, Any] | None) -> bool:
    if not isinstance(job, dict):
        return False
    return str(job.get("status", "")).strip().lower() in {"queued", "processing"}


def _serialize_run_summary(run: dict[str, Any]) -> DocumentRunSummary:
    return DocumentRunSummary(
        run_id=str(run.get("id", "")),
        document_id=str(run.get("document_id", "")),
        trigger_type=str(run.get("trigger_type", "")),
        scope_type=str(run.get("scope_type", "")),
        target_page_no=run.get("target_page_no"),
        status=str(run.get("status", "")),
        error=((str(run.get("error", "")).strip()) or None),
        learning_profile=LearningPreferencesPayload(**normalize_learning_preferences(run.get("learning_profile", {}))),
        created_at=str(run.get("created_at", "")),
        started_at=str(run.get("started_at", "")),
        finished_at=((str(run.get("finished_at", "")).strip()) or None),
        updated_at=str(run.get("updated_at", "")),
    )


def _serialize_run_detail(run: dict[str, Any]) -> DocumentRunDetail:
    summary = _serialize_run_summary(run)
    return DocumentRunDetail(
        **summary.model_dump(),
        job_id=((str(run.get("job_id", "")).strip()) or None),
        prompt_snapshot={str(k): str(v) for k, v in dict(run.get("prompt_snapshot", {})).items()},
        model_chain=[str(item) for item in list(run.get("model_chain", []))],
        quality_stats=dict(run.get("quality_stats", {})),
    )


def _build_chapter_nav(*, groups: list[dict[str, Any]], page_no: int, language: str, document_id: str, state: AppState) -> dict[str, object]:
    items: list[dict[str, object]] = []
    current_group_id = ""
    for group in groups:
        gid = str(group.get("id", ""))
        explained = 0
        for candidate in range(int(group.get("page_start", 1)), int(group.get("page_end", 1)) + 1):
            if state.store.get_latest_explanation(document_id, candidate, language):
                explained += 1
        if int(group.get("page_start", 1)) <= page_no <= int(group.get("page_end", 1)):
            current_group_id = gid
        items.append(
            {
                "id": gid,
                "title": str(group.get("title", "")),
                "page_start": int(group.get("page_start", 1)),
                "page_end": int(group.get("page_end", 1)),
                "explained_pages": explained,
            }
        )
    return {"groups": items, "current_group_id": current_group_id}


def _build_evidence_drawer(*, explanation: dict[str, Any] | None, latest_run: dict[str, Any] | None) -> dict[str, object]:
    if not isinstance(explanation, dict):
        return {
            "scope_pages": [],
            "citations": [],
            "evidence_blocks": [],
            "memory_used": {},
            "run": _serialize_run_summary(latest_run).model_dump() if isinstance(latest_run, dict) else None,
        }
    return {
        "scope_pages": list(explanation.get("scopePages", []) or []),
        "citations": list(explanation.get("citations", []) or []),
        "evidence_blocks": list(explanation.get("evidenceBlocks", []) or []),
        "memory_used": dict(explanation.get("memoryUsed", {}) or {}),
        "run": _serialize_run_summary(latest_run).model_dump() if isinstance(latest_run, dict) else None,
    }


def _build_agent_graph(
    *,
    doc: dict[str, Any],
    page: dict[str, Any],
    explanation: dict[str, Any] | None,
    latest_run: dict[str, Any] | None,
    vision_model: str | None = None,
) -> dict[str, object]:
    layout_blocks = list(page.get("layout_blocks", []) or [])
    has_vision = any(str(block.get("source", "")) == "openai_vision" for block in layout_blocks if isinstance(block, dict))
    has_translation = str(page.get("translation_overlay_status", "") or "").strip().lower() in {"ready", "partial", "legacy"}
    has_explanation = isinstance(explanation, dict)
    quality = explanation.get("quality", {}) if isinstance(explanation, dict) else {}
    quality = quality if isinstance(quality, dict) else {}
    agent_trace = explanation.get("agentGraphTrace", {}) if isinstance(explanation, dict) else {}
    agent_trace = agent_trace if isinstance(agent_trace, dict) else {}
    citations = list(explanation.get("citations", []) or []) if isinstance(explanation, dict) else []
    scope_pages = list(explanation.get("scopePages", []) or []) if isinstance(explanation, dict) else []
    model_chain = [str(item) for item in list((latest_run or {}).get("model_chain", []) or [])] if isinstance(latest_run, dict) else []
    if has_vision and not any(":vision" in item for item in model_chain):
        model = str(vision_model or "").strip() or "vision"
        model_chain.append(f"openai:{model}:vision")
    run_status = str((latest_run or {}).get("status", "") or "")

    def node(
        node_id: str,
        label: str,
        status: str,
        requirement: str,
        evidence: str,
    ) -> dict[str, str]:
        return {
            "id": node_id,
            "label": label,
            "status": status,
            "requirement": requirement,
            "evidence": evidence,
        }

    nodes = [
        node(
            "preprocess",
            "Preprocess Tool",
            "completed" if page else "pending",
            "Tool-Use / 工具化业务流程",
            "PPT/PDF 转换、页面渲染、文本块抽取、embedding 写入。",
        ),
        node(
            "vision",
            "VLM Page Reader",
            "completed" if has_vision else ("skipped" if str(doc.get("source_type", "")) != "pptx" else "available"),
            "LLM/VLM 应用开发",
            "openai_vision blocks" if has_vision else "无视觉补全文本块。",
        ),
        node(
            "translation",
            "Agent T Translation",
            "completed" if has_translation else "pending",
            "Prompt Engineering / Tool output normalization",
            f"translation_overlay_status={page.get('translation_overlay_status', '')}",
        ),
        node(
            "planning",
            "Agent A/B Planner",
            "completed" if str(doc.get("status", "")) == "completed" or has_explanation else "pending",
            "Planner / 复杂流程拆解",
            "Agent A 生成文档记忆与分组，Agent B 生成章节学习摘要。",
        ),
        node(
            "retrieval",
            "Local RAG Context",
            "completed" if has_explanation and (scope_pages or citations) else "pending",
            "RAG / 长程上下文",
            f"scopePages={scope_pages or []}",
        ),
        node(
            "agent_c",
            "Agent C Page Tutor",
            "completed" if has_explanation else "pending",
            "LangGraph + ReAct-style Agent / Tool evidence grounding",
            "LangGraph StateGraph 编排 page tutor、quality gate 与 retry；生成时读取 page + group memory + local context。",
        ),
        node(
            "quality_gate",
            "Quality Gate",
            "completed" if has_explanation else "pending",
            "Reflexion / 自我评估",
            f"score={quality.get('score', '-')}, pass={quality.get('pass', '-')}",
        ),
        node(
            "reflection",
            "Reflection Retry",
            "completed"
            if bool(
                quality.get("citationRepairAttempted")
                or quality.get("feedbackRetryAttempted")
                or agent_trace.get("reflection_retry_attempted")
                or agent_trace.get("fallback_attempted")
            )
            else ("available" if has_explanation else "pending"),
            "Reflexion / 过程奖励式反馈",
            "引用修复、反馈重试、fallback model 组成可追踪纠错环。",
        ),
    ]

    return {
        "nodes": nodes,
        "edges": [
            {"from": "preprocess", "to": "vision"},
            {"from": "vision", "to": "translation"},
            {"from": "translation", "to": "planning"},
            {"from": "planning", "to": "retrieval"},
            {"from": "retrieval", "to": "agent_c"},
            {"from": "agent_c", "to": "quality_gate"},
            {"from": "quality_gate", "to": "reflection"},
            {"from": "reflection", "to": "agent_c"},
        ],
        "run": {
            "status": run_status,
            "model_chain": model_chain,
        },
        "framework_mapping": {
            "LangGraph": "Agent C 使用 LangGraph StateGraph 编排 page tutor -> quality gate -> reflection retry/fallback 的状态转移；外层 preprocess、vision、translation、planner、RAG 仍作为 Python 工具节点接入。",
            "ReAct": "Agent C 在生成讲解时显式使用 page text、layout blocks、citations、local context 等工具输出作为证据。",
            "Reflexion": "quality gate 将 coverage/citation/continuity/specificity 等反馈回写给重试和引用修复流程。",
            "RAG": "select_local_context 把相邻页和语义相近页作为局部上下文，避免只看单页。",
            "Tool-Use": "PPT 转 PDF、页面渲染、VLM 识图、OCR、translation overlay、quality evaluator 都是可替换工具节点。",
        },
    }


@router.get("", response_model=DocumentListResponse)
def list_documents(
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    view: Annotated[str, Query()] = "all",
) -> DocumentListResponse:
    state = _get_state(request)
    if not state.settings.auth_enabled or _is_admin_user(current_user):
        docs = state.store.list_documents()
    else:
        docs = state.store.list_documents_for_owner(_current_user_id(current_user))
    normalized_view = str(view or "all").strip().lower()
    payload: list[DocumentListItem] = []
    for doc in docs[:limit]:
        document_id = str(doc.get("id", ""))
        if not document_id:
            continue
        status = str(doc.get("status", "queued")) or "queued"
        job = state.store.get_job_by_document(document_id)
        has_active_job = _job_is_active(job)
        if normalized_view == "active" and not has_active_job:
            continue
        if normalized_view == "library" and has_active_job:
            continue
        stage = _normalize_stage(str((job or {}).get("stage", "")), status=status)
        payload.append(
            DocumentListItem(
                document_id=document_id,
                original_filename=str(doc.get("original_filename", "")),
                source_type=str(doc.get("source_type", "")),
                status=status,
                stage=stage,
                stage_label=_stage_label(stage, status=status),
                error=(str(doc.get("error")) if doc.get("error") else None),
                progress=_build_progress(int(doc.get("total_pages", 0)), int(doc.get("processed_pages", 0))),
                last_page_no=max(1, int(doc.get("last_page_no", 1) or 1)),
                latest_run_id=((str(doc.get("latest_run_id", "")).strip()) or None),
                has_active_job=has_active_job,
                translation_ready_pages=max(0, int(doc.get("processed_pages", 0) or 0)),
                translation_total_pages=max(0, int(doc.get("total_pages", 0) or 0)),
                explained_pages=len(state.store.list_explained_page_nos(document_id, "zh")),
                job_type=str((job or {}).get("job_type", "translate_document") or "translate_document"),
                created_at=str(doc.get("created_at", "")),
                updated_at=str(doc.get("updated_at", "")),
            )
        )
    return DocumentListResponse(items=payload)


@router.post("", response_model=UploadResponse)
async def upload_document(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    prompt_profile: Annotated[str, Form()] = "personal",
    task_prompt: Annotated[str | None, Form()] = None,
    agent_a_instruction: Annotated[str | None, Form()] = None,
    agent_b_instruction: Annotated[str | None, Form()] = None,
    agent_c_instruction: Annotated[str | None, Form()] = None,
    chat_instruction: Annotated[str | None, Form()] = None,
    formula_instruction: Annotated[str | None, Form()] = None,
    learner_level: Annotated[str | None, Form()] = None,
    learning_goal: Annotated[str | None, Form()] = None,
    depth_mode: Annotated[str | None, Form()] = None,
    attention_support: Annotated[str | None, Form()] = None,
    llm_provider: Annotated[str | None, Form()] = None,
    llm_model: Annotated[str | None, Form()] = None,
    llm_fallback_chain: Annotated[str | None, Form()] = None,
) -> UploadResponse:
    state = _get_state(request)
    provider_override = str(llm_provider or "").strip() or None
    model_override = str(llm_model or "").strip() or None
    if not state.settings.auth_enabled and provider_override is None and model_override is None:
        llm_client = state.worker.llm_client
    else:
        llm_client = resolve_user_llm_client(
            state=state,
            user=current_user,
            request=request,
            provider_override=provider_override,
            model_override=model_override,
        )

    suffix = Path(file.filename or "").suffix.lower()
    source_type = ALLOWED_EXTENSIONS.get(suffix)
    if source_type is None:
        raise HTTPException(status_code=400, detail="Only PDF and PPTX are supported")

    document_id = str(uuid4())
    job_id = str(uuid4())
    user_id = str((current_user or {}).get("id", "")).strip() or None
    normalized_profile = "default" if prompt_profile.strip().lower() == "default" else "personal"
    clean_task_prompt = (task_prompt or "").strip() or None
    learning_profile = _normalize_learning_form(
        learner_level=learner_level,
        learning_goal=learning_goal,
        depth_mode=depth_mode,
        attention_support=attention_support,
    )
    prompt_overrides = _collect_prompt_overrides(
        agent_a_instruction=agent_a_instruction,
        agent_b_instruction=agent_b_instruction,
        agent_c_instruction=agent_c_instruction,
        chat_instruction=chat_instruction,
        formula_instruction=formula_instruction,
    )
    prompt_config = state.store.build_effective_prompt_config(
        user_id=user_id,
        prompt_profile=normalized_profile,
        task_prompt=clean_task_prompt,
        prompt_overrides=prompt_overrides,
        learning_profile=learning_profile,
    )

    doc_dir = state.settings.data_dir / "documents" / document_id / "uploads"
    doc_dir.mkdir(parents=True, exist_ok=True)
    source_path = doc_dir / (file.filename or f"input{suffix}")

    with source_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    state.store.create_document(
        document_id=document_id,
        original_filename=file.filename or source_path.name,
        source_type=source_type,
        source_path=str(source_path),
        owner_user_id=user_id if state.settings.auth_enabled else None,
        prompt_profile=normalized_profile,
        task_prompt=clean_task_prompt,
        prompt_config=prompt_config,
        learning_profile=learning_profile,
        status="queued",
    )
    state.store.create_job(job_id, document_id, stage="queued", job_type="translate_document")
    run_id = str(uuid4())
    state.store.create_document_run(
        run_id=run_id,
        document_id=document_id,
        job_id=job_id,
        trigger_type="upload",
        scope_type="document",
        status="queued",
        prompt_snapshot=prompt_config,
        learning_profile=learning_profile,
        model_chain=[],
        quality_stats={},
    )
    parsed_chain = _parse_fallback_chain(llm_fallback_chain)
    if parsed_chain:
        state.store.save_document_fallback_chain(
            document_id=document_id,
            chain=parsed_chain,
            default_chain=_default_fallback_chain(state),
        )
    state.worker.enqueue_document(document_id=document_id, job_id=job_id, llm_client=llm_client)
    return UploadResponse(document_id=document_id, job_id=job_id)


@router.get("/{document_id}", response_model=DocumentStatusResponse)
def get_document_status(document_id: str, request: Request) -> DocumentStatusResponse:
    state = _get_state(request)
    current_user = get_current_user(request)
    doc = _enforce_document_access(state=state, document_id=document_id, current_user=current_user)

    job = state.store.get_job_by_document(document_id)
    stage = _normalize_stage(str((job or {}).get("stage", "")), status=doc["status"])
    detail_raw = state.worker.get_pipeline_detail(
        document_id=document_id,
        stage_code=stage,
        done_pages=doc["processed_pages"],
        total_pages=doc["total_pages"],
    )
    visible_processed_pages = int(doc["processed_pages"])
    if str(doc["status"]) == "processing" and (
        stage.startswith("agent_c1:draft") or stage.startswith("translate:blocks")
    ):
        visible_processed_pages = max(visible_processed_pages, int(detail_raw.get("done_pages", visible_processed_pages)))
    progress = _build_progress(int(doc["total_pages"]), visible_processed_pages)
    return DocumentStatusResponse(
        document_id=document_id,
        status=doc["status"],
        stage=stage,
        stage_label=_stage_label(stage, status=doc["status"]),
        error=doc["error"],
        progress=progress,
        pipeline_detail=PipelineDetailPayload(**detail_raw),
    )


@router.get("/{document_id}/prompt", response_model=DocumentPromptSnapshotResponse)
def get_document_prompt_snapshot(document_id: str, request: Request) -> DocumentPromptSnapshotResponse:
    state = _get_state(request)
    current_user = get_current_user(request)
    doc = _enforce_document_access(state=state, document_id=document_id, current_user=current_user)

    cfg = state.store.get_document_prompt_config(document_id)
    return DocumentPromptSnapshotResponse(
        document_id=document_id,
        prompt_profile=str(doc.get("prompt_profile", "personal")),
        task_prompt=((doc.get("task_prompt") or "").strip() or None),
        prompt_config=PromptFields(
            agent_a_instruction=str(cfg.get("agent_a_instruction", "")),
            agent_b_instruction=str(cfg.get("agent_b_instruction", "")),
            agent_c_instruction=str(cfg.get("agent_c_instruction", "")),
            chat_instruction=str(cfg.get("chat_instruction", "")),
            formula_instruction=str(cfg.get("formula_instruction", "")),
        ),
    )


@router.get("/{document_id}/outline", response_model=OutlineResponse)
def get_outline(document_id: str, request: Request) -> OutlineResponse:
    state = _get_state(request)
    current_user = get_current_user(request)
    _enforce_document_access(state=state, document_id=document_id, current_user=current_user)

    global_memory = state.store.get_global_memory(document_id)
    groups = state.store.list_groups(document_id)
    return OutlineResponse(
        document_id=document_id,
        global_summary=(global_memory or {}).get("summary", ""),
        keywords=(global_memory or {}).get("keywords", []),
        groups=[GroupPayload(**group) for group in groups],
        learning_arc=[
            LearningArcPayload(
                from_group=str(item.get("from_group", "")),
                to_group=str(item.get("to_group", "")),
                why=str(item.get("why", "")),
            )
            for item in ((global_memory or {}).get("learning_arc", []) or [])
            if isinstance(item, dict)
        ],
    )


@router.get("/{document_id}/runs", response_model=DocumentRunListResponse)
def list_document_runs(
    document_id: str,
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> DocumentRunListResponse:
    state = _get_state(request)
    _enforce_document_access(state=state, document_id=document_id, current_user=current_user)
    runs = state.store.list_document_runs(document_id)
    return DocumentRunListResponse(items=[_serialize_run_summary(item) for item in runs])


@router.get("/{document_id}/runs/{run_id}", response_model=DocumentRunDetail)
def get_document_run_detail(
    document_id: str,
    run_id: str,
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> DocumentRunDetail:
    state = _get_state(request)
    _enforce_document_access(state=state, document_id=document_id, current_user=current_user)
    run = state.store.get_document_run(run_id)
    if run is None or str(run.get("document_id", "")) != document_id:
        raise HTTPException(status_code=404, detail="run not found")
    return _serialize_run_detail(run)


@router.get("/{document_id}/pages/{page_no}", response_model=PageResponse)
def get_page(
    document_id: str,
    page_no: int,
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    language: Annotated[str, Query()] = "zh",
) -> PageResponse:
    state = _get_state(request)
    doc = _enforce_document_access(state=state, document_id=document_id, current_user=current_user)
    state.store.update_document_last_page(document_id, page_no=page_no)

    page = state.store.get_page(document_id, page_no)
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")

    explanation_row = state.store.get_latest_explanation(document_id, page_no, language)
    explanation = explanation_row["payload"] if explanation_row else None
    layout_blocks = list(page.get("layout_blocks", []) or [])
    translation_blocks = list(page.get("translation_blocks", []) or [])
    untranslated_blocks = list(page.get("untranslated_blocks", []) or [])
    literal_translation = str(page.get("literal_translation", "") or "")
    is_legacy_translation_missing = (
        str(doc.get("status", "")).strip().lower() == "completed"
        and not layout_blocks
        and not translation_blocks
        and not literal_translation.strip()
    )

    image_path = Path(page["image_path"])
    try:
        relative_path = image_path.relative_to(state.settings.data_dir)
    except ValueError:
        relative_path = image_path
    image_url = f"/assets/{relative_path.as_posix()}"

    status_hint = _build_page_status_hint(doc=doc, page_no=page_no, explanation=explanation if isinstance(explanation, dict) else None)
    if is_legacy_translation_missing:
        status_hint = "这是旧版处理结果，还没有生成翻译覆盖层。请重新生成翻译层或重新上传文档。"
    groups = state.store.list_groups(document_id)
    latest_run = state.store.get_document_run(str(doc.get("latest_run_id", "")).strip()) if str(doc.get("latest_run_id", "")).strip() else state.store.get_latest_document_run(document_id)
    reading_tabs = ["translate"]
    if isinstance(explanation, dict):
        reading_tabs.append("explain")
    return PageResponse(
        document_id=document_id,
        page_no=page_no,
        group_id=page.get("group_id"),
        image_url=image_url,
        text_preview=page.get("text_content", "")[:1200],
        formulas=page.get("formulas", []),
        explanation=explanation,
        reader_mode_default="translated",
        layout_blocks=layout_blocks,
        translation_blocks=translation_blocks,
        untranslated_blocks=untranslated_blocks,
        translation_overlay_status=("legacy" if is_legacy_translation_missing else str(page.get("translation_overlay_status", "pending") or "pending")),
        literal_translation=literal_translation,
        translation_updated_at=((str(page.get("translation_updated_at", "")).strip()) or None),
        statusHint=status_hint,
        view_model=_build_page_view_model(explanation if isinstance(explanation, dict) else None),
        quality_hint=_build_quality_hint(explanation=explanation if isinstance(explanation, dict) else None, status_hint=status_hint),
        content_density=_build_content_density(explanation if isinstance(explanation, dict) else None),
        reading_tabs=reading_tabs,
        default_tab="translate",
        run_id=((str((latest_run or {}).get("id", "")).strip()) or None),
        latest_run_status=str((latest_run or {}).get("status", "") or ""),
        chapter_nav=_build_chapter_nav(
            groups=groups,
            page_no=page_no,
            language=language,
            document_id=document_id,
            state=state,
        ),
        evidence_drawer=_build_evidence_drawer(
            explanation=explanation if isinstance(explanation, dict) else None,
            latest_run=latest_run,
        ),
        agent_graph=_build_agent_graph(
            doc=doc,
            page=page,
            explanation=explanation if isinstance(explanation, dict) else None,
            latest_run=latest_run,
            vision_model=state.settings.openai_vision_model,
        ),
    )


@router.post("/{document_id}/pages/{page_no}/explain", response_model=RegenerateResponse)
def explain_page(
    document_id: str,
    page_no: int,
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    language: Annotated[str, Query()] = "zh",
    prompt_profile: Annotated[str | None, Query()] = None,
    task_prompt: Annotated[str | None, Query()] = None,
    agent_a_instruction: Annotated[str | None, Query()] = None,
    agent_b_instruction: Annotated[str | None, Query()] = None,
    agent_c_instruction: Annotated[str | None, Query()] = None,
    chat_instruction: Annotated[str | None, Query()] = None,
    formula_instruction: Annotated[str | None, Query()] = None,
    learner_level: Annotated[str | None, Query()] = None,
    learning_goal: Annotated[str | None, Query()] = None,
    depth_mode: Annotated[str | None, Query()] = None,
    attention_support: Annotated[str | None, Query()] = None,
    llm_provider: Annotated[str | None, Query()] = None,
    llm_model: Annotated[str | None, Query()] = None,
    llm_fallback_chain: Annotated[str | None, Query()] = None,
) -> RegenerateResponse:
    return regenerate_page(
        document_id=document_id,
        page_no=page_no,
        request=request,
        current_user=current_user,
        language=language,
        prompt_profile=prompt_profile,
        task_prompt=task_prompt,
        agent_a_instruction=agent_a_instruction,
        agent_b_instruction=agent_b_instruction,
        agent_c_instruction=agent_c_instruction,
        chat_instruction=chat_instruction,
        formula_instruction=formula_instruction,
        learner_level=learner_level,
        learning_goal=learning_goal,
        depth_mode=depth_mode,
        attention_support=attention_support,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_fallback_chain=llm_fallback_chain,
    )


@router.post("/{document_id}/pages/{page_no}/regenerate", response_model=RegenerateResponse)
def regenerate_page(
    document_id: str,
    page_no: int,
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    language: Annotated[str, Query()] = "zh",
    prompt_profile: Annotated[str | None, Query()] = None,
    task_prompt: Annotated[str | None, Query()] = None,
    agent_a_instruction: Annotated[str | None, Query()] = None,
    agent_b_instruction: Annotated[str | None, Query()] = None,
    agent_c_instruction: Annotated[str | None, Query()] = None,
    chat_instruction: Annotated[str | None, Query()] = None,
    formula_instruction: Annotated[str | None, Query()] = None,
    learner_level: Annotated[str | None, Query()] = None,
    learning_goal: Annotated[str | None, Query()] = None,
    depth_mode: Annotated[str | None, Query()] = None,
    attention_support: Annotated[str | None, Query()] = None,
    llm_provider: Annotated[str | None, Query()] = None,
    llm_model: Annotated[str | None, Query()] = None,
    llm_fallback_chain: Annotated[str | None, Query()] = None,
) -> RegenerateResponse:
    state = _get_state(request)
    provider_override = str(llm_provider or "").strip() or None
    model_override = str(llm_model or "").strip() or None
    if not state.settings.auth_enabled and provider_override is None and model_override is None:
        llm_client = state.worker.llm_client
    else:
        llm_client = resolve_user_llm_client(
            state=state,
            user=current_user,
            request=request,
            provider_override=provider_override,
            model_override=model_override,
        )
    doc = _enforce_document_access(state=state, document_id=document_id, current_user=current_user)

    prompt_override = None
    learning_profile = state.store.get_document_learning_profile(document_id)
    current_profile = str(doc.get("prompt_profile", "personal"))
    next_profile = current_profile
    next_task_prompt = (doc.get("task_prompt") or "").strip() or None
    if prompt_profile is not None:
        next_profile = "default" if prompt_profile.strip().lower() == "default" else "personal"
    if task_prompt is not None:
        next_task_prompt = task_prompt.strip() or None
    prompt_overrides = _collect_prompt_overrides(
        agent_a_instruction=agent_a_instruction,
        agent_b_instruction=agent_b_instruction,
        agent_c_instruction=agent_c_instruction,
        chat_instruction=chat_instruction,
        formula_instruction=formula_instruction,
    )
    learning_profile = _normalize_learning_form(
        learner_level=learner_level,
        learning_goal=learning_goal,
        depth_mode=depth_mode,
        attention_support=attention_support,
        fallback=learning_profile,
    )

    if prompt_profile is not None or task_prompt is not None or prompt_overrides or any(
        value is not None for value in (learner_level, learning_goal, depth_mode, attention_support)
    ):
        user_id = str((current_user or {}).get("id", "")).strip() or None
        prompt_override = state.store.build_effective_prompt_config(
            user_id=user_id,
            prompt_profile=next_profile,
            task_prompt=next_task_prompt,
            prompt_overrides=prompt_overrides,
            learning_profile=learning_profile,
        )
        state.store.update_document_prompt_strategy(
            document_id,
            prompt_profile=next_profile,
            task_prompt=next_task_prompt,
            prompt_config=prompt_override,
        )
        state.store.update_document_learning_profile(document_id, learning_profile=learning_profile)
    parsed_chain = _parse_fallback_chain(llm_fallback_chain)
    if parsed_chain:
        state.store.save_document_fallback_chain(
            document_id=document_id,
            chain=parsed_chain,
            default_chain=_default_fallback_chain(state),
        )

    run_id = str(uuid4())
    state.store.create_document_run(
        run_id=run_id,
        document_id=document_id,
        trigger_type="regenerate",
        scope_type="page",
        target_page_no=page_no,
        status="processing",
        prompt_snapshot=prompt_override or state.store.get_document_prompt_config(document_id),
        learning_profile=learning_profile,
        model_chain=[],
        quality_stats={},
    )
    try:
        payload = state.worker.regenerate_page(
            document_id=document_id,
            page_no=page_no,
            language=language,
            prompt_override=prompt_override,
            learning_profile=learning_profile,
            run_id=run_id,
            llm_client=llm_client,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RegenerateResponse(document_id=document_id, page_no=page_no, run_id=run_id, explanation=payload)


@router.post("/{document_id}/pages/{page_no}/chat", response_model=ChatResponse)
def chat_on_page(
    document_id: str,
    page_no: int,
    request: Request,
    body: ChatRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> ChatResponse:
    state = _get_state(request)
    if state.settings.auth_enabled:
        llm_client = resolve_user_llm_client(state=state, user=current_user, request=request)
    else:
        llm_client = state.worker.llm_client
    _enforce_document_access(state=state, document_id=document_id, current_user=current_user)

    try:
        payload = state.worker.answer_page_chat(
            document_id=document_id,
            page_no=page_no,
            question=body.question,
            language=body.language,
            llm_client=llm_client,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ChatResponse(document_id=document_id, page_no=page_no, answer=payload)


@router.get("/{document_id}/pages/{page_no}/chat", response_model=ChatHistoryResponse)
def get_chat_history(
    document_id: str,
    page_no: int,
    request: Request,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ChatHistoryResponse:
    state = _get_state(request)
    _enforce_document_access(state=state, document_id=document_id, current_user=current_user)

    page = state.store.get_page(document_id, page_no)
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")

    items = state.store.list_chats(document_id=document_id, page_no=page_no, limit=limit)
    payload = [
        ChatHistoryItem(
            id=str(item.get("id", "")),
            page_no=int(item.get("page_no", page_no)),
            question=str(item.get("question", "")),
            answer=item.get("answer", {}),
            created_at=str(item.get("created_at", "")),
        )
        for item in items
    ]
    return ChatHistoryResponse(document_id=document_id, page_no=page_no, items=payload)


@router.post("/{document_id}/clear")
def clear_document(document_id: str, request: Request) -> dict:
    state = _get_state(request)
    current_user = get_current_user(request)
    _enforce_document_access(state=state, document_id=document_id, current_user=current_user)
    doc = state.store.delete_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")

    state.store.remove_document_files(document_id, state.settings.data_dir)
    return {"document_id": document_id, "cleared": True}


@router.post("/{document_id}/cancel")
def cancel_document(document_id: str, request: Request) -> dict:
    state = _get_state(request)
    current_user = get_current_user(request)
    doc = _enforce_document_access(state=state, document_id=document_id, current_user=current_user)

    canceled = state.worker.cancel_document(document_id=document_id)
    latest = state.store.get_document(document_id) or doc
    return {
        "document_id": document_id,
        "canceled": bool(canceled),
        "status": str(latest.get("status", "")),
    }
