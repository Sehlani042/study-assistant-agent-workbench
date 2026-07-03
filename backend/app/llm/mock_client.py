from __future__ import annotations

from collections import defaultdict
from uuid import uuid4

from app.llm.base import PageContext
from app.utils.text import split_sentences, top_keywords


class MockLLMClient:
    provider_name = "mock"

    def summarize_document(self, pages: list[dict], *, instruction: str | None = None) -> dict:
        all_text = "\n".join(page.get("text_content", "") for page in pages)
        keywords = top_keywords(all_text, top_n=10)

        groups: list[dict] = []
        step = 5
        for i in range(0, len(pages), step):
            chunk = pages[i : i + step]
            if not chunk:
                continue
            title = chunk[0].get("text_content", "Group").splitlines()[0][:40] or f"Section {i//step + 1}"
            groups.append(
                {
                    "id": str(uuid4()),
                    "title": title,
                    "page_start": chunk[0]["page_no"],
                    "page_end": chunk[-1]["page_no"],
                }
            )

        if not groups:
            groups = [{"id": str(uuid4()), "title": "Section 1", "page_start": 1, "page_end": 1}]

        glossary = [{"term": k, "definition": f"与 {k} 相关的概念"} for k in keywords[:6]]
        knowledge_map = [
            {"source": keywords[i], "target": keywords[i + 1], "relation": "related"}
            for i in range(max(0, len(keywords) - 1))
        ]
        learning_arc: list[dict[str, str]] = []
        for idx in range(max(0, len(groups) - 1)):
            learning_arc.append(
                {
                    "from_group": str(groups[idx]["id"]),
                    "to_group": str(groups[idx + 1]["id"]),
                    "why": "先学基础再学扩展。",
                }
            )

        summary = "该文档主要讨论：" + "、".join(keywords[:5]) if keywords else "该文档包含多页学习内容。"

        return {
            "summary": summary,
            "keywords": keywords,
            "groups": groups,
            "glossary": glossary,
            "knowledge_map": knowledge_map,
            "learning_arc": learning_arc,
        }

    def summarize_group(
        self,
        document_summary: str,
        group: dict,
        pages: list[dict],
        *,
        instruction: str | None = None,
    ) -> dict:
        text = "\n".join(p.get("text_content", "") for p in pages)
        keywords = top_keywords(text, top_n=6)
        return {
            "summary": f"该分组围绕 {', '.join(keywords[:3]) or '核心概念'} 展开，承接文档主线。",
            "key_concepts": keywords[:4],
            "prerequisites": keywords[1:3] if len(keywords) >= 3 else keywords[:2],
            "misconceptions": [f"不要把 {keywords[0]} 与无关概念混淆"] if keywords else ["不要机械记忆结论"],
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
        sentences = split_sentences(context.page_text)
        lead = sentences[0] if sentences else "本页内容"
        second = sentences[1] if len(sentences) > 1 else lead
        local_pages = [item["page_no"] for item in context.local_context]

        formula_blocks = []
        for item in context.page_formulas:
            formula_blocks.append(
                {
                    "latex": item.get("latex", ""),
                    "meaning": f"该公式用于解释本页主题：{lead[:20]}",
                    "sourceSpan": item.get("sourceSpan", item.get("latex", "")),
                }
            )

        explanation = {
            "overview": f"本页核心在于：{lead}",
            "keyPoints": [lead, second][:3],
            "conceptLinks": [f"与分组主题相关：{context.group_summary[:40]}"] if context.group_summary else [],
            "formulaBlocks": formula_blocks,
            "citations": [
                {
                    "pageNo": context.page_no,
                    "span": lead[:40],
                    "quote": lead[:120],
                }
            ],
            "confidence": 0.82 if model_tier == "flash" else 0.9,
            "teaching": {
                "definition": lead,
                "intuition": f"直觉上可把它理解为：{second[:50]}",
                "example": f"可结合本页关键词进行练习：{', '.join(top_keywords(context.page_text, 3))}",
                "focus": "先看定义，再看公式和结论的对应关系。",
                "pitfall": "不要只记结论，忽略公式适用条件。",
            },
            "scaffold": {
                "quick30": [lead],
                "understand2m": [second],
                "master5m": ["尝试复述本页重点并给一个例子。"],
            },
            "continuity": {
                "prevBridge": "承接上一页的核心结论。",
                "thisPageNew": lead,
                "nextPreview": "下一页会继续深化本页概念。",
            },
            "microTask": {
                "doNow": "用一句话复述本页。",
                "checkQuestion": "本页新增了什么？",
                "answerHint": lead[:40],
            },
            "scopePages": sorted({context.page_no, *local_pages, context.page_no - 1, context.page_no + 1}),
            "memoryUsed": {
                "globalVersion": "v1",
                "groupId": "",
                "localPages": local_pages,
            },
        }

        if feedback:
            explanation["overview"] += "（已根据质控反馈改写）"
        return explanation

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
        summary = explanation.get("overview", "")
        local_lines = [ctx.get("text_content", "")[:60] for ctx in local_context[:2]]
        return {
            "answer": f"问题：{question}\n结合当前页，关键点是：{summary}",
            "citations": [
                {
                    "pageNo": page["page_no"],
                    "span": page.get("text_content", "")[:50],
                    "quote": page.get("text_content", "")[:120],
                }
            ],
            "relatedContext": local_lines,
            "scopePages": sorted({int(page["page_no"]), *[int(item.get("page_no", 0)) for item in local_context]}),
        }

    def recognize_formulas_from_visual(self, *, page_text: str, instruction: str | None = None) -> list[dict]:
        # Mock fallback: return nothing extra.
        return []

    def translate_page_text(
        self,
        *,
        page_text: str,
        language: str,
        instruction: str | None = None,
    ) -> str:
        lines = [line.strip() for line in str(page_text or "").splitlines() if line.strip()]
        if not lines:
            return ""
        core = lines[:8]
        if language == "zh":
            return "（直译 Mock）\n" + "\n".join(core)
        return "\n".join(core)
