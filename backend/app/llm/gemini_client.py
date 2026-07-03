from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.llm.base import PageContext
from app.utils.text import top_keywords

JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)

GEMINI_FLASH_LITE_TARGET_MODEL = "gemini-3.1-flash-lite"
GEMINI_FLASH_LITE_TARGET_LABEL = "Gemini 3.1 Flash-Lite"
GEMINI_FLASH_LITE_STABLE_MODEL = "gemini-2.5-flash-lite"

FLASH_FALLBACK_MODELS = [
    "gemini-3-flash-preview",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

FLASH_LITE_FALLBACK_MODELS = [
    GEMINI_FLASH_LITE_STABLE_MODEL,
]

PRO_FALLBACK_MODELS = [
    "gemini-3.1-pro",
    "gemini-3-pro-preview",
    "gemini-pro-latest",
    "gemini-2.5-pro",
]

MODEL_ALIASES = {
    "gemini-3-flash": "gemini-3-flash-preview",
    "gemini-3-pro": "gemini-3-pro-preview",
    "gemini-3.1-pro": "gemini-3-pro-preview",
    "gemini-3.1-pro-preview": "gemini-3-pro-preview",
}


@dataclass(frozen=True)
class GeminiModelSelection:
    requested_model: str
    display_label: str
    resolved_model: str
    resolution_source: str


DOCUMENT_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": ["summary", "keywords", "glossary", "knowledge_map", "learning_arc", "groups"],
    "properties": {
        "summary": {"type": "STRING"},
        "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
        "glossary": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["term", "definition"],
                "properties": {
                    "term": {"type": "STRING"},
                    "definition": {"type": "STRING"},
                },
            },
        },
        "knowledge_map": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["source", "target", "relation"],
                "properties": {
                    "source": {"type": "STRING"},
                    "target": {"type": "STRING"},
                    "relation": {"type": "STRING"},
                },
            },
        },
        "learning_arc": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["from_group", "to_group", "why"],
                "properties": {
                    "from_group": {"type": "STRING"},
                    "to_group": {"type": "STRING"},
                    "why": {"type": "STRING"},
                },
            },
        },
        "groups": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["id", "title", "page_start", "page_end"],
                "properties": {
                    "id": {"type": "STRING"},
                    "title": {"type": "STRING"},
                    "page_start": {"type": "INTEGER"},
                    "page_end": {"type": "INTEGER"},
                },
            },
        },
    },
}

GROUP_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": ["summary", "key_concepts", "prerequisites", "misconceptions"],
    "properties": {
        "summary": {"type": "STRING"},
        "key_concepts": {"type": "ARRAY", "items": {"type": "STRING"}},
        "prerequisites": {"type": "ARRAY", "items": {"type": "STRING"}},
        "misconceptions": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
}

PAGE_EXPLANATION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": [
        "overview",
        "keyPoints",
        "conceptLinks",
        "formulaBlocks",
        "citations",
        "confidence",
        "teaching",
        "scaffold",
        "continuity",
        "microTask",
        "scopePages",
        "memoryUsed",
    ],
    "properties": {
        "overview": {"type": "STRING"},
        "keyPoints": {"type": "ARRAY", "items": {"type": "STRING"}},
        "conceptLinks": {"type": "ARRAY", "items": {"type": "STRING"}},
        "formulaBlocks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["latex", "meaning", "sourceSpan"],
                "properties": {
                    "latex": {"type": "STRING"},
                    "meaning": {"type": "STRING"},
                    "sourceSpan": {"type": "STRING"},
                },
            },
        },
        "citations": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["pageNo", "span", "quote"],
                "properties": {
                    "pageNo": {"type": "INTEGER"},
                    "span": {"type": "STRING"},
                    "quote": {"type": "STRING"},
                },
            },
        },
        "confidence": {"type": "NUMBER"},
        "teaching": {
            "type": "OBJECT",
            "required": ["definition", "intuition", "example", "focus", "pitfall"],
            "properties": {
                "definition": {"type": "STRING"},
                "intuition": {"type": "STRING"},
                "example": {"type": "STRING"},
                "focus": {"type": "STRING"},
                "pitfall": {"type": "STRING"},
            },
        },
        "scaffold": {
            "type": "OBJECT",
            "required": ["quick30", "understand2m", "master5m"],
            "properties": {
                "quick30": {"type": "ARRAY", "items": {"type": "STRING"}},
                "understand2m": {"type": "ARRAY", "items": {"type": "STRING"}},
                "master5m": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
        },
        "continuity": {
            "type": "OBJECT",
            "required": ["prevBridge", "thisPageNew", "nextPreview"],
            "properties": {
                "prevBridge": {"type": "STRING"},
                "thisPageNew": {"type": "STRING"},
                "nextPreview": {"type": "STRING"},
            },
        },
        "microTask": {
            "type": "OBJECT",
            "required": ["doNow", "checkQuestion", "answerHint"],
            "properties": {
                "doNow": {"type": "STRING"},
                "checkQuestion": {"type": "STRING"},
                "answerHint": {"type": "STRING"},
            },
        },
        "scopePages": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "memoryUsed": {
            "type": "OBJECT",
            "required": ["globalVersion", "groupId", "localPages"],
            "properties": {
                "globalVersion": {"type": "STRING"},
                "groupId": {"type": "STRING"},
                "localPages": {"type": "ARRAY", "items": {"type": "INTEGER"}},
            },
        },
    },
}

PAGE_CHAT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": ["answer", "citations", "relatedContext", "scopePages"],
    "properties": {
        "answer": {"type": "STRING"},
        "citations": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["pageNo", "span", "quote"],
                "properties": {
                    "pageNo": {"type": "INTEGER"},
                    "span": {"type": "STRING"},
                    "quote": {"type": "STRING"},
                },
            },
        },
        "relatedContext": {"type": "ARRAY", "items": {"type": "STRING"}},
        "scopePages": {"type": "ARRAY", "items": {"type": "INTEGER"}},
    },
}

FORMULA_RECOGNITION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": ["formulas"],
    "properties": {
        "formulas": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "required": ["latex", "sourceSpan"],
                "properties": {
                    "latex": {"type": "STRING"},
                    "sourceSpan": {"type": "STRING"},
                },
            },
        }
    },
}

PAGE_TRANSLATION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "required": ["translation"],
    "properties": {
        "translation": {"type": "STRING"},
    },
}


class GeminiClient:
    provider_name = "gemini"
    _DEFAULT_HTTP_TIMEOUT_S = 35.0
    _MAX_FLASH_MODEL_CANDIDATES = 2
    _MAX_FALLBACK_MODEL_CANDIDATES = 3

    def __init__(self, api_key: str, flash_model: str, fallback_model: str) -> None:
        self.api_key = api_key
        self.flash_model = self._normalize_model(flash_model)
        self.fallback_model = self._normalize_model(fallback_model)
        self._available_models: set[str] | None = None
        self._models_discovery_done = False

    def _model_name(self, tier: str) -> str:
        return self.flash_model if tier == "flash" else self.fallback_model

    def _normalize_model(self, model: str) -> str:
        cleaned = (model or "").strip()
        if cleaned.startswith("models/"):
            cleaned = cleaned.removeprefix("models/")
        return cleaned

    @staticmethod
    def _is_flash_lite_auto_target(model: str) -> bool:
        return str(model or "").strip().lower() == GEMINI_FLASH_LITE_TARGET_MODEL

    @staticmethod
    def _is_flash_lite_model(model: str) -> bool:
        return "flash-lite" in str(model or "").strip().lower()

    @staticmethod
    def _model_matches_flash_lite_version(model: str, version: str) -> bool:
        normalized = str(model or "").strip().lower()
        return "flash-lite" in normalized and version in normalized

    @staticmethod
    def _flash_lite_sort_key(model: str, *, version: str) -> tuple[int, int, int, str]:
        normalized = str(model or "").strip().lower()
        exact_prefix = 0 if normalized.startswith(f"gemini-{version}-flash-lite") else 1
        stable_bias = 0 if "preview" not in normalized else 1
        return (exact_prefix, stable_bias, len(normalized), normalized)

    def _best_available_flash_lite(self, available: set[str] | None, *, version: str) -> str:
        if not available:
            return ""
        candidates = [
            model for model in available
            if self._model_matches_flash_lite_version(model, version)
        ]
        if not candidates:
            return ""
        return sorted(candidates, key=lambda item: self._flash_lite_sort_key(item, version=version))[0]

    def describe_flash_model(self, model: str | None = None) -> GeminiModelSelection:
        requested = self._normalize_model(model or self.flash_model) or GEMINI_FLASH_LITE_TARGET_MODEL
        if self._is_flash_lite_auto_target(requested):
            available = self._discover_available_models()
            discovered_31 = self._best_available_flash_lite(available, version="3.1")
            if discovered_31:
                return GeminiModelSelection(
                    requested_model=requested,
                    display_label=GEMINI_FLASH_LITE_TARGET_LABEL,
                    resolved_model=discovered_31,
                    resolution_source="discovered-3.1",
                )
            fallback_25 = self._best_available_flash_lite(available, version="2.5") or GEMINI_FLASH_LITE_STABLE_MODEL
            return GeminiModelSelection(
                requested_model=requested,
                display_label=GEMINI_FLASH_LITE_TARGET_LABEL,
                resolved_model=fallback_25,
                resolution_source="fallback-2.5-lite",
            )
        return GeminiModelSelection(
            requested_model=requested,
            display_label=requested,
            resolved_model=requested,
            resolution_source="exact",
        )

    def _unique_models(self, models: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for model in models:
            normalized = self._normalize_model(model)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    def _discover_available_models(self) -> set[str] | None:
        if self._models_discovery_done:
            return self._available_models

        self._models_discovery_done = True
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        headers = {"x-goog-api-key": self.api_key}
        try:
            with httpx.Client(timeout=20) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json()
            models: set[str] = set()
            for item in payload.get("models", []):
                methods = item.get("supportedGenerationMethods") or []
                if "generateContent" not in methods:
                    continue
                name = str(item.get("name", "")).strip()
                if name.startswith("models/"):
                    models.add(name.removeprefix("models/"))
            self._available_models = models
        except Exception:
            self._available_models = None
        return self._available_models

    def _candidate_models(self, model: str, tier: str) -> list[str]:
        requested = self._normalize_model(model)
        available = self._discover_available_models()

        if tier == "flash" and self._is_flash_lite_auto_target(requested):
            selection = self.describe_flash_model(requested)
            candidates = [selection.resolved_model]
            if selection.resolution_source == "discovered-3.1":
                candidates.extend(FLASH_LITE_FALLBACK_MODELS)
            candidates = self._unique_models(candidates)
            if available:
                filtered = [candidate for candidate in candidates if candidate in available]
                if filtered:
                    return filtered
            return candidates

        candidates = [requested]

        alias = MODEL_ALIASES.get(requested)
        if alias:
            candidates.append(alias)

        if tier == "flash":
            candidates.extend(FLASH_FALLBACK_MODELS)
        else:
            candidates.extend(PRO_FALLBACK_MODELS)

        candidates = self._unique_models(candidates)
        if available:
            filtered = [m for m in candidates if m in available]
            if filtered:
                return filtered
        return candidates

    def _extract_json(self, text: str) -> Any:
        text = text.strip()
        if not text:
            raise ValueError("empty llm response")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        match = JSON_BLOCK_RE.search(text)
        if match:
            return json.loads(match.group(1))

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])

        raise ValueError("unable to parse llm response as json")

    def _build_payload(self, *, prompt: str, schema: dict[str, Any] | None) -> dict[str, Any]:
        generation_config: dict[str, Any] = {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        }
        if schema is not None:
            generation_config["responseSchema"] = schema
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }

    def _request_timeout(self) -> float:
        return max(8.0, float(self._DEFAULT_HTTP_TIMEOUT_S))

    @staticmethod
    def _is_non_retryable_http_error(exc: Exception) -> bool:
        if not isinstance(exc, httpx.HTTPStatusError):
            return False
        if exc.response is None:
            return False
        status = int(exc.response.status_code)
        # Invalid credentials / forbidden access should fail immediately.
        return status in {401, 403}

    @staticmethod
    def _should_retry_without_schema(exc: Exception) -> bool:
        if not isinstance(exc, httpx.HTTPStatusError):
            return False
        if exc.response is None:
            return False
        status = int(exc.response.status_code)
        if status != 400:
            return False
        try:
            body = str(exc.response.text or "").lower()
        except Exception:
            body = ""
        hints = (
            "responseschema",
            "response schema",
            "responsemimetype",
            "response mime",
            "json_schema",
            "json schema",
            "schema",
        )
        return any(hint in body for hint in hints)

    def _generate_json(
        self,
        *,
        model: str,
        prompt: str,
        tier: str,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"x-goog-api-key": self.api_key}
        max_candidates = self._MAX_FLASH_MODEL_CANDIDATES if tier == "flash" else self._MAX_FALLBACK_MODEL_CANDIDATES
        model_candidates = self._candidate_models(model, tier)[:max_candidates]
        payload_with_schema = self._build_payload(prompt=prompt, schema=schema)
        payload_without_schema = self._build_payload(prompt=prompt, schema=None) if schema is not None else None
        last_exc: Exception | None = None

        for model_name in model_candidates:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            try:
                with httpx.Client(timeout=self._request_timeout()) as client:
                    response = client.post(url, headers=headers, json=payload_with_schema)
                    response.raise_for_status()
                    data = response.json()
                text = ""
                candidates = data.get("candidates") or []
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "\n".join(part.get("text", "") for part in parts)
                parsed = self._extract_json(text)
                if not isinstance(parsed, dict):
                    raise ValueError("expected object json")
                return parsed
            except Exception as exc:
                last_exc = exc
                if self._is_non_retryable_http_error(exc):
                    raise RuntimeError(f"Gemini generateContent failed with non-retryable error on model {model_name}") from exc

                # Only retry with schema disabled when the provider explicitly rejects schema.
                if payload_without_schema is None or not self._should_retry_without_schema(exc):
                    continue

            try:
                with httpx.Client(timeout=self._request_timeout()) as client:
                    response = client.post(url, headers=headers, json=payload_without_schema)
                    response.raise_for_status()
                    data = response.json()
                text = ""
                candidates = data.get("candidates") or []
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "\n".join(part.get("text", "") for part in parts)
                parsed = self._extract_json(text)
                if not isinstance(parsed, dict):
                    raise ValueError("expected object json")
                return parsed
            except Exception as exc:
                last_exc = exc
                if self._is_non_retryable_http_error(exc):
                    raise RuntimeError(f"Gemini generateContent failed with non-retryable error on model {model_name}") from exc
                continue

        if last_exc is not None:
            tried = ", ".join(model_candidates)
            raise RuntimeError(f"Gemini generateContent failed after trying models: {tried}") from last_exc
        raise RuntimeError("Gemini generateContent failed: no candidate model available")

    def summarize_document(self, pages: list[dict], *, instruction: str | None = None) -> dict:
        page_payload = [
            {"page": page["page_no"], "text": page["text_content"][:1500]}
            for page in pages
        ]
        behavior = "先提炼文档主线，再输出结构化分组与学习弧线，确保页码覆盖完整且不重叠。"
        if instruction and instruction.strip():
            behavior = f"{behavior}\n用户自定义要求：{instruction.strip()}"
        prompt = (
            "你是学习助手的文档规划 Agent。请基于页面内容输出 JSON："
            "{summary, keywords, glossary, knowledge_map, learning_arc, groups}。"
            "groups 数组元素为 {id,title,page_start,page_end}，page_start/page_end 必须有效覆盖全页。"
            "learning_arc 数组元素为 {from_group,to_group,why}，描述学习顺序依赖。"
            f"\n行为约束: {behavior}\n"
            "\n输入页面："
            f"{json.dumps(page_payload, ensure_ascii=False)}"
        )
        try:
            parsed = self._generate_json(
                model=self.flash_model,
                prompt=prompt,
                tier="flash",
                schema=DOCUMENT_SUMMARY_SCHEMA,
            )
            return {
                "summary": str(parsed.get("summary", "")),
                "keywords": list(parsed.get("keywords", []))[:20],
                "glossary": list(parsed.get("glossary", []))[:30],
                "knowledge_map": list(parsed.get("knowledge_map", []))[:30],
                "learning_arc": list(parsed.get("learning_arc", []))[:50],
                "groups": list(parsed.get("groups", [])),
            }
        except Exception:
            all_text = "\n".join(page.get("text_content", "") for page in pages)
            keywords = top_keywords(all_text, 10)
            fallback_groups = [
                {
                    "id": "group-1",
                    "title": "Section 1",
                    "page_start": pages[0]["page_no"],
                    "page_end": pages[-1]["page_no"],
                }
            ]
            return {
                "summary": "该文档围绕核心概念展开，建议按分组逐步学习。",
                "keywords": keywords,
                "glossary": [{"term": k, "definition": "核心术语"} for k in keywords[:5]],
                "knowledge_map": [],
                "learning_arc": [{"from_group": "group-1", "to_group": "group-1", "why": "先总览后细化"}],
                "groups": fallback_groups,
            }

    def summarize_group(
        self,
        document_summary: str,
        group: dict,
        pages: list[dict],
        *,
        instruction: str | None = None,
    ) -> dict:
        payload = [
            {"page": p["page_no"], "text": p["text_content"][:1200]}
            for p in pages
        ]
        behavior = "总结要包含核心概念、前置依赖和常见误区，避免空泛描述。"
        if instruction and instruction.strip():
            behavior = f"{behavior}\n用户自定义要求：{instruction.strip()}"
        prompt = (
            "你是学习助手的分组总结 Agent。输出 JSON："
            "{summary, key_concepts, prerequisites, misconceptions}。"
            f"\n行为约束: {behavior}\n"
            f"\n文档综述: {document_summary}\n"
            f"分组: {json.dumps(group, ensure_ascii=False)}\n"
            f"页面: {json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            parsed = self._generate_json(
                model=self.flash_model,
                prompt=prompt,
                tier="flash",
                schema=GROUP_SUMMARY_SCHEMA,
            )
            return {
                "summary": str(parsed.get("summary", "")),
                "key_concepts": list(parsed.get("key_concepts", []))[:8],
                "prerequisites": list(parsed.get("prerequisites", []))[:8],
                "misconceptions": list(parsed.get("misconceptions", []))[:8],
            }
        except Exception:
            keywords = top_keywords("\n".join(p.get("text_content", "") for p in pages), 6)
            return {
                "summary": f"本组主要涉及：{', '.join(keywords[:3])}",
                "key_concepts": keywords[:4],
                "prerequisites": keywords[1:3],
                "misconceptions": ["注意概念边界"],
            }

    def explain_page(
        self,
        context: PageContext,
        *,
        language: str,
        model_tier: str,
        feedback: str | None = None,
        instruction: str | None = None,
    ) -> dict:
        behavior = (
            "默认教学策略：先按老师/课件原有结构做忠实翻译，再用通俗语言解释这页在做什么、"
            "为什么重要、应如何理解。默认学习者画像为注意力容易分散（ADHD 友好）。"
            "语气要像耐心助教，接地气，术语出现时立刻补一句白话解释。"
            "输出必须短句、分点、先结论后解释，避免长段落。"
            "避免过度碎片化：优先给出连续叙事，再补要点。"
            "严格避免同义反复：keyPoints、clarity.steps、scaffold 三层之间不要复读同一句。"
            "quick30/understand2m/master5m 必须逐层加深，至少各给 1 条不重复信息。"
            "如果 language=zh，主体内容必须使用中文表达，英文仅保留术语括注。"
            "teaching 的 definition/intuition/example/focus/pitfall 每栏建议 1-2 段，不要堆砌空泛短句。"
            "必须输出三层台阶 scaffold: quick30/understand2m/master5m。"
            "必须输出 continuity: prevBridge/thisPageNew/nextPreview。"
            "continuity 必须具体到本页概念，不得使用“承接上一页的核心结论”“下一页会继续深化本页概念”等通用套话。"
            "必须输出 microTask: doNow/checkQuestion/answerHint。"
            "microTask 必须绑定本页概念，不能写“用一句话复述本页”这类空泛任务。"
            "必须输出 scopePages（当前页与连续链页）。"
            "数学表达必须使用 $...$（行内）或 $$...$$（块级），禁止裸写下标形式（如 y_ijk）。"
            "输出必须是可直接渲染的干净 Markdown，必要时可用 Markdown 表格。"
        )
        if instruction and instruction.strip():
            behavior = f"{behavior}\n用户自定义要求：{instruction.strip()}"
        prompt = (
            "你是教学型页面解释 Agent。返回 JSON，字段必须包含："
            "overview,keyPoints,conceptLinks,formulaBlocks,citations,confidence,teaching,scaffold,continuity,microTask,scopePages,memoryUsed。"
            "teaching包含definition,intuition,example,focus,pitfall。"
            "scaffold包含quick30,understand2m,master5m。"
            "continuity包含prevBridge,thisPageNew,nextPreview。"
            "microTask包含doNow,checkQuestion,answerHint。"
            "citations元素包含pageNo,span,quote。"
            f"\n行为约束: {behavior}\n"
            f"\n语言: {language}\n"
            f"文档综述: {context.document_summary}\n"
            f"分组总结: {context.group_summary}\n"
            f"当前页码: {context.page_no}\n"
            f"当前页文本: {context.page_text[:5000]}\n"
            f"当前页公式: {json.dumps(context.page_formulas, ensure_ascii=False)}\n"
            f"局部上下文: {json.dumps(context.local_context, ensure_ascii=False)}\n"
        )
        if feedback:
            prompt += f"\n质控反馈: {feedback}\n请针对问题修正解释并输出完整 JSON。"
        parsed = self._generate_json(
            model=self._model_name(model_tier),
            prompt=prompt,
            tier=model_tier,
            schema=PAGE_EXPLANATION_SCHEMA,
        )

        if "memoryUsed" not in parsed:
            parsed["memoryUsed"] = {"globalVersion": "v1", "groupId": "", "localPages": []}
        return parsed

    def answer_page_question(
        self,
        *,
        question: str,
        language: str,
        page: dict,
        explanation: dict,
        local_context: list[dict],
        global_summary: str,
        instruction: str | None = None,
    ) -> dict:
        allowed_pages = sorted(
            {
                int(page.get("page_no", 0)),
                *[int(item.get("page_no", 0)) for item in local_context if item.get("page_no") is not None],
            }
        )
        behavior = (
            "回答必须围绕当前页，优先使用当前页证据。"
            "只能引用允许页码内的证据；如果证据不足，请明确说明。"
            "默认学习者画像为注意力容易分散（ADHD 友好）：先给 1 句话结论，再给 2-4 条短要点。"
            "语气要接地气并给出可执行建议（例如“你现在先看哪两行”）。"
            "数学表达必须使用 $...$ 或 $$...$$，必要时可用 Markdown 表格。"
        )
        if instruction and instruction.strip():
            behavior = f"{behavior}\n用户自定义要求：{instruction.strip()}"
        prompt = (
            "你是页面学习问答助手，请输出 JSON: {answer,citations,relatedContext,scopePages}。"
            f"\n行为约束: {behavior}\n"
            f"允许引用页码: {allowed_pages}\n"
            f"\n语言: {language}\n"
            f"问题: {question}\n"
            f"当前页: {json.dumps(page, ensure_ascii=False)[:4000]}\n"
            f"当前页解释: {json.dumps(explanation, ensure_ascii=False)[:4000]}\n"
            f"局部上下文: {json.dumps(local_context, ensure_ascii=False)[:4000]}\n"
            f"文档综述: {global_summary}"
        )
        parsed = self._generate_json(
            model=self.flash_model,
            prompt=prompt,
            tier="flash",
            schema=PAGE_CHAT_SCHEMA,
        )
        return {
            "answer": parsed.get("answer", ""),
            "citations": parsed.get("citations", []),
            "relatedContext": parsed.get("relatedContext", []),
            "scopePages": parsed.get("scopePages", allowed_pages),
        }

    def recognize_formulas_from_visual(self, *, page_text: str, instruction: str | None = None) -> list[dict]:
        behavior = "只识别高度确定的数学表达式，禁止输出整句自然语言。"
        if instruction and instruction.strip():
            behavior = f"{behavior}\n用户自定义要求：{instruction.strip()}"
        prompt = (
            "你是公式识别器。根据输入文本线索推测可能的数学公式，输出 JSON: {formulas:[{latex,sourceSpan}]}。"
            f"\n行为约束: {behavior}\n"
            f"\n输入: {page_text[:3000]}"
        )
        try:
            parsed = self._generate_json(
                model=self.flash_model,
                prompt=prompt,
                tier="flash",
                schema=FORMULA_RECOGNITION_SCHEMA,
            )
            formulas = parsed.get("formulas", [])
            if not isinstance(formulas, list):
                return []
            return [f for f in formulas if isinstance(f, dict) and f.get("latex")]
        except Exception:
            return []

    def translate_page_text(
        self,
        *,
        page_text: str,
        language: str,
        instruction: str | None = None,
    ) -> str:
        target = "中文" if str(language).strip().lower() == "zh" else "English"
        behavior = (
            "你是逐页直译 Agent（Agent T）。只做忠实翻译，不解释、不总结、不补充背景。"
            "保持原段落顺序与层级，不得漏掉关键信息。"
            "如果目标语言是中文，必须把英文自然语言翻成简体中文；只保留专有名词括注、变量、公式和缩写。"
            "短标题也必须翻译，例如 What is Longitudinal Data? 应输出 什么是纵向数据（Longitudinal Data）？。"
            "输出 translation 必须是可直接渲染的 Markdown 文本，并尽量保留原排版："
            "标题层级、项目符号、编号列表、表格、代码块都要保持。"
            "公式保持原样（含 LaTeX/数学符号），不要把自然语言改写成公式。"
            "数学表达请显式使用 Markdown 数学包裹：行内用 $...$，独立公式行用 $$...$$。"
            "禁止添加“说明/注释/译者按”等额外内容。"
        )
        if instruction and instruction.strip():
            behavior = f"{behavior}\n用户自定义要求：{instruction.strip()}"
        prompt = (
            "请将输入内容翻译为目标语言，并返回 JSON: {translation}。"
            f"\n目标语言: {target}\n"
            f"行为约束: {behavior}\n"
            "\n格式规则："
            "\n1) 列表项保持逐条对应，不要合并成大段。"
            "\n2) 若检测到表格，输出 Markdown 表格（含分隔行）。"
            "\n3) 代码块必须保留 ``` 包裹和缩进。"
            "\n4) 数学表达保持原符号，不删除上下标。"
            "\n5) 若目标语言是中文，不能整段原样返回英文。"
            "\n6) 只返回 translation 文本内容，不要输出额外键。"
            f"\n输入文本:\n{str(page_text or '')[:7000]}"
        )
        try:
            parsed = self._generate_json(
                model=self.flash_model,
                prompt=prompt,
                tier="flash",
                schema=PAGE_TRANSLATION_SCHEMA,
            )
            return str(parsed.get("translation", "")).strip()
        except Exception:
            lines = [line.strip() for line in str(page_text or "").splitlines() if line.strip()]
            return "\n".join(lines[:10])
