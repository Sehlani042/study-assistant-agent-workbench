from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx

from app.llm.base import PageContext
from app.llm.gemini_client import (
    DOCUMENT_SUMMARY_SCHEMA,
    FORMULA_RECOGNITION_SCHEMA,
    GROUP_SUMMARY_SCHEMA,
    PAGE_CHAT_SCHEMA,
    PAGE_EXPLANATION_SCHEMA,
    PAGE_TRANSLATION_SCHEMA,
)
from app.utils.text import top_keywords

JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)

PAGE_VISION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "visual_summary": {"type": "STRING"},
        "text_blocks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING"},
                    "kind": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["text", "kind", "confidence"],
            },
        },
        "chart_notes": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
    },
    "required": ["visual_summary", "text_blocks", "chart_notes"],
}


def _to_json_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if schema is None:
        return None

    type_map = {
        "OBJECT": "object",
        "ARRAY": "array",
        "STRING": "string",
        "INTEGER": "integer",
        "NUMBER": "number",
        "BOOLEAN": "boolean",
        "NULL": "null",
    }

    def convert(node: Any) -> Any:
        if isinstance(node, list):
            return [convert(item) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "type" and isinstance(value, str):
                out[key] = type_map.get(value, value.lower())
                continue
            out[key] = convert(value)
        if out.get("type") == "object" and "additionalProperties" not in out:
            out["additionalProperties"] = False
        return out

    converted = convert(schema)
    if isinstance(converted, dict) and converted.get("type") == "object" and "additionalProperties" not in converted:
        converted["additionalProperties"] = False
    return converted


class OpenAIClient:
    provider_name = "openai"
    _DEFAULT_HTTP_TIMEOUT_S = 45.0

    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.openai.com/v1") -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip() or "gpt-5.2"
        self.base_url = str(base_url or "https://api.openai.com/v1").rstrip("/")
        if not self.api_key:
            raise ValueError("openai api key required")

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

    def _extract_output_text(self, data: dict[str, Any]) -> str:
        snippets: list[str] = []
        for item in data.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).strip() != "message":
                continue
            for part in item.get("content", []) or []:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "")).strip()
                if part_type == "refusal":
                    refusal = str(part.get("refusal", "")).strip()
                    raise RuntimeError(f"OpenAI refused structured output: {refusal or 'refusal'}")
                if part_type == "output_text":
                    text = str(part.get("text", "")).strip()
                    if text:
                        snippets.append(text)
        if snippets:
            return "\n".join(snippets)
        direct = str(data.get("output_text", "")).strip()
        if direct:
            return direct
        raise RuntimeError("OpenAI response did not include output_text")

    def _request_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        return self._request_json_payload(
            input_payload=prompt,
            schema=schema,
            temperature=temperature,
            schema_name="study_assistant_response",
        )

    def _request_json_payload(
        self,
        *,
        input_payload: str | list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        temperature: float | None = 0.2,
        schema_name: str = "study_assistant_response",
    ) -> dict[str, Any]:
        url = f"{self.base_url}/responses"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_payload,
            "text": {"format": {"type": "text"}},
        }
        if temperature is not None:
            payload["temperature"] = temperature
        converted_schema = _to_json_schema(schema)
        if converted_schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": converted_schema,
                }
            }

        try:
            with httpx.Client(timeout=max(8.0, float(self._DEFAULT_HTTP_TIMEOUT_S))) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code) if exc.response is not None else 0
            body = ""
            if exc.response is not None:
                try:
                    body = exc.response.text
                except Exception:
                    body = ""
            raise RuntimeError(
                f"OpenAI Responses API request failed (status={status}, model={self.model}): {body[:500]}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"OpenAI Responses API request failed (model={self.model}): {exc}") from exc

        text = self._extract_output_text(data if isinstance(data, dict) else {})
        parsed = self._extract_json(text)
        if not isinstance(parsed, dict):
            raise RuntimeError("OpenAI structured output is not a JSON object")
        return parsed

    def describe_page_image(
        self,
        *,
        image_path: Path,
        page_text: str,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(str(path))
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        data_url = f"data:{mime_type};base64,{encoded}"

        behavior = (
            "你是课件视觉理解工具。任务是补全文本抽取遗漏的图像、图表、截图、箭头关系和嵌入式文字。"
            "只输出页面上能直接看见或高度确定的信息；不要扩展背景知识。"
            "text_blocks 应按阅读顺序列出可见标题、要点、图表标签或关键截图文字。"
            "kind 只能优先使用 title、paragraph、list、formula、table、chart、caption。"
        )
        if instruction and instruction.strip():
            behavior = f"{behavior}\n用户自定义要求：{instruction.strip()}"
        prompt = (
            "请阅读这张课件/文档页面图片，输出 JSON："
            "{visual_summary, text_blocks, chart_notes}。"
            f"\n行为约束: {behavior}\n"
            f"\n已有文本抽取结果（可能不完整）:\n{str(page_text or '')[:2000]}"
        )
        parsed = self._request_json_payload(
            input_payload=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": data_url, "detail": "high"},
                    ],
                }
            ],
            schema=PAGE_VISION_SCHEMA,
            temperature=None,
            schema_name="study_assistant_vision_response",
        )

        blocks: list[dict[str, Any]] = []
        for raw in list(parsed.get("text_blocks", []) or [])[:24]:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            kind = str(raw.get("kind", "paragraph")).strip().lower() or "paragraph"
            if kind not in {"title", "paragraph", "list", "formula", "table", "chart", "caption"}:
                kind = "paragraph"
            try:
                confidence = float(raw.get("confidence", 0.75))
            except (TypeError, ValueError):
                confidence = 0.75
            blocks.append(
                {
                    "text": text,
                    "kind": kind,
                    "confidence": round(max(0.0, min(1.0, confidence)), 4),
                }
            )

        return {
            "visual_summary": str(parsed.get("visual_summary", "")).strip(),
            "text_blocks": blocks,
            "chart_notes": [str(item).strip() for item in list(parsed.get("chart_notes", []) or []) if str(item).strip()][:12],
        }

    def summarize_document(self, pages: list[dict], *, instruction: str | None = None) -> dict:
        page_payload = [{"page": page["page_no"], "text": page.get("text_content", "")[:1500]} for page in pages]
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
        parsed = self._request_json(prompt=prompt, schema=DOCUMENT_SUMMARY_SCHEMA)
        return {
            "summary": str(parsed.get("summary", "")),
            "keywords": list(parsed.get("keywords", []))[:20],
            "glossary": list(parsed.get("glossary", []))[:30],
            "knowledge_map": list(parsed.get("knowledge_map", []))[:30],
            "learning_arc": list(parsed.get("learning_arc", []))[:50],
            "groups": list(parsed.get("groups", [])),
        }

    def summarize_group(
        self,
        document_summary: str,
        group: dict,
        pages: list[dict],
        *,
        instruction: str | None = None,
    ) -> dict:
        payload = [{"page": p["page_no"], "text": p.get("text_content", "")[:1200]} for p in pages]
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
        parsed = self._request_json(prompt=prompt, schema=GROUP_SUMMARY_SCHEMA)
        return {
            "summary": str(parsed.get("summary", "")),
            "key_concepts": list(parsed.get("key_concepts", []))[:8],
            "prerequisites": list(parsed.get("prerequisites", []))[:8],
            "misconceptions": list(parsed.get("misconceptions", []))[:8],
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
            "必须输出 microTask: doNow/checkQuestion/answerHint。"
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
        parsed = self._request_json(prompt=prompt, schema=PAGE_EXPLANATION_SCHEMA)
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
        parsed = self._request_json(prompt=prompt, schema=PAGE_CHAT_SCHEMA)
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
        parsed = self._request_json(prompt=prompt, schema=FORMULA_RECOGNITION_SCHEMA)
        formulas = parsed.get("formulas", [])
        if not isinstance(formulas, list):
            return []
        return [f for f in formulas if isinstance(f, dict) and f.get("latex")]

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
        parsed = self._request_json(prompt=prompt, schema=PAGE_TRANSLATION_SCHEMA)
        text = str(parsed.get("translation", "")).strip()
        if text:
            return text
        lines = [line.strip() for line in str(page_text or "").splitlines() if line.strip()]
        if not lines:
            return ""
        return "\n".join(lines[:10])
