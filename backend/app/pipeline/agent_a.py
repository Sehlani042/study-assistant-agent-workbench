from __future__ import annotations

from uuid import uuid4

from app.llm.base import LLMClient
from app.utils.text import top_keywords


def _sanitize_groups(groups: list[dict], total_pages: int) -> list[dict]:
    valid: list[dict] = []

    for idx, raw in enumerate(groups):
        if not isinstance(raw, dict):
            continue
        start = int(raw.get("page_start", idx + 1))
        end = int(raw.get("page_end", start))
        start = max(1, min(start, total_pages))
        end = max(start, min(end, total_pages))
        valid.append(
            {
                "id": str(raw.get("id") or uuid4()),
                "title": str(raw.get("title") or f"Section {idx + 1}"),
                "page_start": start,
                "page_end": end,
            }
        )

    if not valid:
        valid = [{"id": str(uuid4()), "title": "Section 1", "page_start": 1, "page_end": total_pages}]

    # Re-sort and force contiguous coverage.
    valid.sort(key=lambda item: item["page_start"])
    cursor = 1
    for group in valid:
        if group["page_start"] > cursor:
            group["page_start"] = cursor
        if group["page_end"] < group["page_start"]:
            group["page_end"] = group["page_start"]
        cursor = group["page_end"] + 1

    if valid[-1]["page_end"] < total_pages:
        valid[-1]["page_end"] = total_pages

    compact: list[dict] = []
    for group in valid:
        if compact and group["page_start"] <= compact[-1]["page_end"]:
            compact[-1]["page_end"] = max(compact[-1]["page_end"], group["page_end"])
            continue
        compact.append(group)

    return compact


def run_agent_a(*, llm_client: LLMClient, pages: list[dict], instruction: str | None = None) -> dict:
    if not pages:
        raise ValueError("agent A requires pages")

    result = llm_client.summarize_document(pages, instruction=instruction)
    summary = str(result.get("summary", "")).strip()
    if not summary:
        summary = "该文档覆盖多个学习主题，建议按分组逐步理解。"

    page_blob = "\n".join(page.get("text_content", "") for page in pages)
    keywords = [str(x) for x in result.get("keywords", []) if str(x).strip()]
    if not keywords:
        keywords = top_keywords(page_blob, 10)

    glossary = result.get("glossary", [])
    if not isinstance(glossary, list):
        glossary = []

    knowledge_map = result.get("knowledge_map", [])
    if not isinstance(knowledge_map, list):
        knowledge_map = []

    learning_arc = result.get("learning_arc", [])
    if not isinstance(learning_arc, list):
        learning_arc = []
    normalized_learning_arc: list[dict[str, str]] = []
    for item in learning_arc:
        if not isinstance(item, dict):
            continue
        from_group = str(item.get("from_group", "")).strip()
        to_group = str(item.get("to_group", "")).strip()
        why = str(item.get("why", "")).strip()
        if not from_group or not to_group:
            continue
        normalized_learning_arc.append(
            {
                "from_group": from_group,
                "to_group": to_group,
                "why": why or "建议按顺序学习。",
            }
        )

    raw_groups = result.get("groups", [])
    if not isinstance(raw_groups, list):
        raw_groups = []
    groups = _sanitize_groups(raw_groups, total_pages=len(pages))

    if not normalized_learning_arc and groups:
        for idx in range(max(0, len(groups) - 1)):
            normalized_learning_arc.append(
                {
                    "from_group": str(groups[idx]["id"]),
                    "to_group": str(groups[idx + 1]["id"]),
                    "why": "先学前置内容，再学习后续内容。",
                }
            )

    return {
        "summary": summary,
        "keywords": keywords,
        "glossary": glossary,
        "knowledge_map": knowledge_map,
        "learning_arc": normalized_learning_arc,
        "groups": groups,
    }
