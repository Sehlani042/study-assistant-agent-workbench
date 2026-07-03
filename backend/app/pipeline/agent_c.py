from __future__ import annotations

import time
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from app.llm.base import LLMClient, PageContext
from app.pipeline.formulas import looks_like_formula_candidate
from app.services.quality import evaluate_page_explanation
from app.utils.markdown_math import normalize_math_markdown
from app.utils.text import split_sentences, tokenize


class _AgentCGraphState(TypedDict, total=False):
    payload: dict[str, Any]
    quality: dict[str, Any]
    model_used: str
    trace_nodes: list[str]
    citation_repair_attempted: bool
    citation_repair_applied: bool
    rewrite_attempted: bool
    fallback_attempted: bool


def _text(value: object) -> str:
    if isinstance(value, list):
        parts = [normalize_math_markdown(str(item or "").strip()) for item in value]
        return "；".join(part for part in parts if part)
    if isinstance(value, dict):
        parts = [normalize_math_markdown(str(item or "").strip()) for item in value.values()]
        return "；".join(part for part in parts if part)
    return normalize_math_markdown(str(value or "").strip())


def _as_string_list(value: object, *, limit: int = 6) -> list[str]:
    if isinstance(value, list):
        out = [_text(item) for item in value if _text(item)]
        return out[:limit]
    text = _text(value)
    return [text] if text else []


def _first_non_empty(*values: str, fallback: str = "") -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return fallback


def _topic_hint(payload: dict | None, *, page_no: int) -> str:
    if not isinstance(payload, dict):
        return f"第 {page_no} 页关键点"
    teaching_raw = payload.get("teaching", {})
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}
    key_points = payload.get("keyPoints", [])
    key_point = ""
    if isinstance(key_points, list) and key_points:
        key_point = _text(key_points[0])
    topic = _first_non_empty(
        _text(payload.get("overview", "")),
        key_point,
        _text(teaching.get("definition", "")),
        fallback=f"第 {page_no} 页关键点",
    )
    return topic[:72]


def _build_scope_pages(page_no: int, local_pages: list[int]) -> list[int]:
    # Scope pages should be evidence pages actually supplied to Agent C, not
    # guessed neighbors that may be outside the document.
    scope = {int(page_no), *[int(x) for x in local_pages if int(x) > 0]}
    return sorted(scope)


def _safe_page_no(value: object, fallback: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _normalize_citations(value: object, *, page_no: int) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "pageNo": _safe_page_no(item.get("pageNo", page_no), fallback=page_no),
                "span": _text(item.get("span", "")),
                "quote": _text(item.get("quote", "")),
            }
        )
    return out


def _pick_best_citation_for_claim(claim: str, citations: list[dict]) -> list[dict]:
    claim_tokens = {tok for tok in tokenize(claim) if len(tok) > 1}
    if not citations:
        return []
    if not claim_tokens:
        return [citations[0]]

    best_item = citations[0]
    best_score = -1
    for item in citations:
        quote_tokens = {tok for tok in tokenize(str(item.get("quote", ""))) if len(tok) > 1}
        score = len(claim_tokens & quote_tokens)
        if score > best_score:
            best_item = item
            best_score = score
    return [best_item] if best_item else []


def _normalize_clarity(payload: dict) -> dict:
    clarity_raw = payload.get("clarity")
    clarity = clarity_raw if isinstance(clarity_raw, dict) else {}
    teaching_raw = payload.get("teaching", {})
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}

    key_points = _as_string_list(payload.get("keyPoints"), limit=8)
    steps = _as_string_list(clarity.get("steps"), limit=5)
    if not steps:
        steps = key_points[:3]
    if not steps:
        steps = _as_string_list([teaching.get("definition", ""), teaching.get("focus", "")], limit=3)

    conclusion = _first_non_empty(
        _text(clarity.get("conclusion", "")),
        _text(payload.get("overview", "")),
        key_points[0] if key_points else "",
    )
    example = _first_non_empty(
        _text(clarity.get("example", "")),
        _text(teaching.get("example", "")),
        key_points[1] if len(key_points) > 1 else "",
    )
    return {
        "conclusion": conclusion,
        "steps": steps[:4],
        "example": example,
    }


def _normalize_evidence_blocks(
    *,
    raw_value: object,
    clarity: dict,
    citations: list[dict],
    page_no: int,
) -> list[dict]:
    out: list[dict] = []
    allowed_kinds = {"conclusion", "step", "example"}
    if isinstance(raw_value, list):
        for item in raw_value:
            if not isinstance(item, dict):
                continue
            claim = _text(item.get("claim", ""))
            if not claim:
                continue
            raw_kind = str(item.get("kind", "step")).strip().lower()
            kind = raw_kind if raw_kind in allowed_kinds else "step"
            item_citations = _normalize_citations(item.get("citations", []), page_no=page_no)
            if not item_citations:
                item_citations = _pick_best_citation_for_claim(claim, citations)
            out.append(
                {
                    "kind": kind,
                    "claim": claim,
                    "citations": item_citations,
                }
            )

    if out:
        dedup_out: list[dict] = []
        seen_claims: list[str] = []
        for item in out:
            claim = _text(item.get("claim", ""))
            if not claim:
                continue
            if any(_semantic_overlap(claim, seen) >= 0.92 for seen in seen_claims):
                continue
            seen_claims.append(claim)
            dedup_out.append(item)
        return dedup_out[:8]

    synthesized: list[dict] = []
    conclusion = _text(clarity.get("conclusion", ""))
    if conclusion:
        synthesized.append(
            {
                "kind": "conclusion",
                "claim": conclusion,
                "citations": _pick_best_citation_for_claim(conclusion, citations),
            }
        )
    for step in _as_string_list(clarity.get("steps"), limit=3):
        synthesized.append(
            {
                "kind": "step",
                "claim": step,
                "citations": _pick_best_citation_for_claim(step, citations),
            }
        )
    example = _text(clarity.get("example", ""))
    if example:
        synthesized.append(
            {
                "kind": "example",
                "claim": example,
                "citations": _pick_best_citation_for_claim(example, citations),
            }
        )
    return synthesized[:8]


def _build_candidate_quotes(*, page_no: int, page_text: str, local_context: list[dict], scope_pages: set[int]) -> list[dict]:
    candidates: list[dict] = []

    def collect(target_page: int, text: str) -> None:
        if target_page not in scope_pages:
            return
        for sentence in split_sentences(text)[:8]:
            clean = sentence.strip()
            if len(clean) < 6:
                continue
            candidates.append(
                {
                    "pageNo": target_page,
                    "span": clean[:50],
                    "quote": clean[:180],
                }
            )

    collect(page_no, page_text)
    for item in local_context:
        ctx_page_no = _safe_page_no(item.get("page_no"), fallback=page_no)
        collect(ctx_page_no, str(item.get("text_content", "")))
    return candidates


def _claim_list_for_citation_repair(payload: dict) -> list[str]:
    clarity_raw = payload.get("clarity")
    clarity = clarity_raw if isinstance(clarity_raw, dict) else {}
    claims: list[str] = []
    claims.extend(_as_string_list([clarity.get("conclusion", "")], limit=1))
    claims.extend(_as_string_list(clarity.get("steps", []), limit=3))
    claims.extend(_as_string_list([clarity.get("example", "")], limit=1))
    if not claims:
        claims.extend(_as_string_list(payload.get("keyPoints", []), limit=4))
    if not claims:
        claims.append(_text(payload.get("overview", "")))
    return [item for item in claims if item]


def repair_citation_alignment(
    *,
    payload: dict,
    page_no: int,
    page_text: str,
    local_context: list[dict],
    scope_pages: list[int],
) -> dict:
    out = dict(payload)
    scope_set = {int(x) for x in scope_pages if int(x) > 0}
    if page_no not in scope_set:
        scope_set.add(page_no)
    claims = _claim_list_for_citation_repair(out)
    quote_candidates = _build_candidate_quotes(
        page_no=page_no,
        page_text=page_text,
        local_context=local_context,
        scope_pages=scope_set,
    )
    if not quote_candidates:
        return out

    repaired_citations: list[dict] = []
    for claim in claims:
        claim_tokens = {tok for tok in tokenize(claim) if len(tok) > 1}
        best = quote_candidates[0]
        best_score = -1
        for candidate in quote_candidates:
            quote_tokens = {tok for tok in tokenize(str(candidate.get("quote", ""))) if len(tok) > 1}
            score = len(claim_tokens & quote_tokens)
            if score > best_score:
                best = candidate
                best_score = score
        repaired_citations.append(
            {
                "pageNo": _safe_page_no(best.get("pageNo"), fallback=page_no),
                "span": _text(best.get("span", "")),
                "quote": _text(best.get("quote", "")),
            }
        )

    dedup: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for item in repaired_citations:
        key = (int(item["pageNo"]), str(item["quote"]))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    out["citations"] = dedup[:8]
    clarity = _normalize_clarity(out)
    out["clarity"] = clarity
    out["evidenceBlocks"] = _normalize_evidence_blocks(
        raw_value=out.get("evidenceBlocks", []),
        clarity=clarity,
        citations=out["citations"],
        page_no=page_no,
    )
    out["citationRepair"] = {"attempted": True, "applied": True, "method": "heuristic-alignment"}
    return out


def _is_citation_only_failure(quality: dict) -> bool:
    citation = float(quality.get("citationScore", 100.0))
    coverage = float(quality.get("coverage", 0.0))
    continuity = float(quality.get("continuityScore", 0.0))
    specificity = float(quality.get("specificityScore", 0.0))
    actionability = float(quality.get("actionabilityScore", 0.0))
    language_score = float(quality.get("languageScore", 100.0))
    hard_failed = bool(quality.get("hardFailed", False))
    if citation >= 60:
        return False
    if hard_failed:
        return False
    return (
        coverage >= 60
        and continuity >= 80
        and specificity >= 50
        and actionability >= 50
        and language_score >= 60
    )


def _token_set(text: str) -> set[str]:
    return {tok for tok in tokenize(str(text).lower()) if len(tok) > 1}


def _semantic_overlap(a: str, b: str) -> float:
    text_a = _text(a)
    text_b = _text(b)
    if not text_a or not text_b:
        return 0.0
    if text_a == text_b:
        return 1.0

    tokens_a = _token_set(text_a)
    tokens_b = _token_set(text_b)
    if not tokens_a or not tokens_b:
        return 0.0

    inter = len(tokens_a & tokens_b)
    if inter <= 0:
        return 0.0
    union = len(tokens_a | tokens_b)
    jaccard = inter / max(1, union)
    containment = max(inter / max(1, len(tokens_a)), inter / max(1, len(tokens_b)))
    return max(jaccard, containment)


def _dedupe_semantic_list(
    values: list[str],
    *,
    anchors: list[str] | None = None,
    limit: int = 8,
    threshold: float = 0.86,
) -> list[str]:
    refs = [_text(item) for item in (anchors or []) if _text(item)]
    out: list[str] = []
    for raw in values:
        value = _text(raw)
        if not value:
            continue
        if any(_semantic_overlap(value, ref) >= threshold for ref in refs):
            continue
        refs.append(value)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _normalize_translation_status(status: object, *, literal_translation: str) -> str:
    candidate = str(status or "").strip().lower()
    if candidate in {"pending", "ready", "failed"}:
        return candidate
    return "ready" if literal_translation else "pending"


def _pick_non_redundant_overview(
    *,
    overview: str,
    clarity_conclusion: str,
    teaching: dict,
    key_points: list[str],
    continuity: dict,
) -> str:
    current = _text(overview)
    conclusion = _text(clarity_conclusion)
    if not current:
        return current
    if not conclusion:
        return current
    if _semantic_overlap(current, conclusion) < 0.9:
        return current

    candidates = [
        _text(teaching.get("focus", "")),
        _text(teaching.get("definition", "")),
        _text(teaching.get("intuition", "")),
        _text((continuity or {}).get("thisPageNew", "")),
        _text(key_points[0] if key_points else ""),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if _semantic_overlap(candidate, conclusion) < 0.88:
            return candidate
    return current


def _reduce_semantic_overlap(payload: dict) -> dict:
    out = dict(payload)
    teaching_raw = out.get("teaching", {})
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}
    clarity_raw = out.get("clarity", {})
    clarity = clarity_raw if isinstance(clarity_raw, dict) else {}
    scaffold_raw = out.get("scaffold", {})
    scaffold = scaffold_raw if isinstance(scaffold_raw, dict) else {}
    micro_raw = out.get("microTask", {})
    micro = micro_raw if isinstance(micro_raw, dict) else {}

    continuity_raw = out.get("continuity", {})
    continuity = continuity_raw if isinstance(continuity_raw, dict) else {}

    overview = _text(out.get("overview", ""))
    key_points = _dedupe_semantic_list(_as_string_list(out.get("keyPoints"), limit=8), limit=8, threshold=0.9)
    out["keyPoints"] = key_points

    clarity_conclusion = _text(clarity.get("conclusion", ""))
    clarity_steps = _dedupe_semantic_list(
        _as_string_list(clarity.get("steps", []), limit=6),
        anchors=[clarity_conclusion],
        limit=4,
        threshold=0.88,
    )
    if not clarity_steps:
        clarity_steps = _dedupe_semantic_list(key_points[:3], anchors=[clarity_conclusion], limit=3, threshold=0.88)
    clarity_example = _text(clarity.get("example", ""))
    if clarity_example and (
        _semantic_overlap(clarity_example, clarity_conclusion) >= 0.9
        or any(_semantic_overlap(clarity_example, step) >= 0.9 for step in clarity_steps)
    ):
        fallback_example = _text(teaching.get("example", ""))
        clarity_example = fallback_example if fallback_example and fallback_example != clarity_example else clarity_example
    out["clarity"] = {
        "conclusion": clarity_conclusion,
        "steps": clarity_steps[:4],
        "example": clarity_example,
    }
    out["overview"] = _pick_non_redundant_overview(
        overview=overview,
        clarity_conclusion=clarity_conclusion,
        teaching=teaching,
        key_points=key_points,
        continuity=continuity,
    )

    quick30 = _dedupe_semantic_list(
        _as_string_list(scaffold.get("quick30"), limit=4),
        anchors=[overview, clarity_conclusion],
        limit=4,
        threshold=0.88,
    )
    if not quick30:
        quick30 = _dedupe_semantic_list(key_points[:3], anchors=[overview, clarity_conclusion], limit=3, threshold=0.88)

    understand2m = _dedupe_semantic_list(
        _as_string_list(scaffold.get("understand2m"), limit=6),
        anchors=[overview, clarity_conclusion, *quick30],
        limit=6,
        threshold=0.86,
    )
    if not understand2m:
        understand2m = _dedupe_semantic_list(
            _as_string_list([teaching.get("definition", ""), teaching.get("intuition", ""), teaching.get("focus", "")], limit=6),
            anchors=[overview, clarity_conclusion, *quick30],
            limit=4,
            threshold=0.86,
        )

    master5m = _dedupe_semantic_list(
        _as_string_list(scaffold.get("master5m"), limit=8),
        anchors=[overview, clarity_conclusion, *quick30, *understand2m],
        limit=8,
        threshold=0.84,
    )
    if not master5m:
        master5m = _dedupe_semantic_list(
            _as_string_list([teaching.get("example", ""), teaching.get("focus", ""), teaching.get("pitfall", ""), micro.get("doNow", "")], limit=8),
            anchors=[overview, clarity_conclusion, *quick30, *understand2m],
            limit=6,
            threshold=0.84,
        )

    out["scaffold"] = {
        "quick30": quick30,
        "understand2m": understand2m,
        "master5m": master5m,
    }

    teaching_order = ["definition", "intuition", "focus", "pitfall"]
    dedup_teaching: dict[str, str] = {}
    seen_teaching: list[str] = []
    for key in teaching_order:
        value = _text(teaching.get(key, ""))
        if not value:
            dedup_teaching[key] = ""
            continue
        if any(_semantic_overlap(value, seen) >= 0.92 for seen in seen_teaching):
            dedup_teaching[key] = ""
            continue
        dedup_teaching[key] = value
        seen_teaching.append(value)
    dedup_teaching["example"] = _text(teaching.get("example", ""))
    out["teaching"] = {
        "definition": dedup_teaching.get("definition", ""),
        "intuition": dedup_teaching.get("intuition", ""),
        "example": dedup_teaching.get("example", ""),
        "focus": dedup_teaching.get("focus", ""),
        "pitfall": dedup_teaching.get("pitfall", ""),
    }
    return out


def _normalize_explanation(payload: dict, *, page_no: int, group_id: str, local_pages: list[int]) -> dict:
    payload = dict(payload)
    payload.setdefault("overview", "")
    payload.setdefault("keyPoints", [])
    payload.setdefault("conceptLinks", [])
    payload.setdefault("formulaBlocks", [])
    payload.setdefault("citations", [])
    payload.setdefault("confidence", 0.6)
    payload.setdefault("literalTranslation", "")
    payload["overview"] = _text(payload.get("overview", ""))
    payload["keyPoints"] = _as_string_list(payload.get("keyPoints"), limit=8)
    payload["conceptLinks"] = _as_string_list(payload.get("conceptLinks"), limit=8)
    payload["literalTranslation"] = _text(payload.get("literalTranslation", ""))
    payload["translationStatus"] = _normalize_translation_status(
        payload.get("translationStatus"),
        literal_translation=payload["literalTranslation"],
    )
    payload["translationUpdatedAt"] = str(payload.get("translationUpdatedAt", "") or "")
    if payload["translationStatus"] != "failed":
        payload["translationError"] = ""
    else:
        payload["translationError"] = _text(payload.get("translationError", ""))

    teaching_raw = payload.get("teaching")
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}
    payload["teaching"] = {
        "definition": _text(teaching.get("definition", "")),
        "intuition": _text(teaching.get("intuition", "")),
        "example": _text(teaching.get("example", "")),
        "focus": _text(teaching.get("focus", "")),
        "pitfall": _text(teaching.get("pitfall", "")),
    }

    continuity_raw = payload.get("continuity")
    continuity = continuity_raw if isinstance(continuity_raw, dict) else {}
    payload["continuity"] = {
        "prevBridge": _text(continuity.get("prevBridge", "")),
        "thisPageNew": _text(continuity.get("thisPageNew", "")),
        "nextPreview": _text(continuity.get("nextPreview", "")),
    }

    scaffold_raw = payload.get("scaffold")
    scaffold = scaffold_raw if isinstance(scaffold_raw, dict) else {}
    payload["scaffold"] = {
        "quick30": _as_string_list(scaffold.get("quick30"), limit=4),
        "understand2m": _as_string_list(scaffold.get("understand2m"), limit=6),
        "master5m": _as_string_list(scaffold.get("master5m"), limit=8),
    }

    micro_raw = payload.get("microTask")
    micro = micro_raw if isinstance(micro_raw, dict) else {}
    payload["microTask"] = {
        "doNow": _text(micro.get("doNow", "")),
        "checkQuestion": _text(micro.get("checkQuestion", "")),
        "answerHint": _text(micro.get("answerHint", "")),
    }

    memory_raw = payload.get("memoryUsed")
    memory = memory_raw if isinstance(memory_raw, dict) else {}
    normalized_local_pages = sorted({int(x) for x in local_pages if int(x) > 0})
    payload["memoryUsed"] = {
        "globalVersion": str(memory.get("globalVersion", "v1")),
        "groupId": group_id,
        "localPages": normalized_local_pages,
    }
    payload["scopePages"] = _build_scope_pages(page_no, normalized_local_pages)

    payload["citations"] = _normalize_citations(payload.get("citations", []), page_no=page_no)

    normalized_formulas = []
    for item in payload.get("formulaBlocks", []):
        if not isinstance(item, dict):
            continue
        normalized_formulas.append(
            {
                "latex": str(item.get("latex", "")),
                "meaning": _text(item.get("meaning", "")),
                "sourceSpan": _text(item.get("sourceSpan", "")),
            }
        )
    payload["formulaBlocks"] = normalized_formulas
    clarity = _normalize_clarity(payload)
    payload["clarity"] = clarity
    payload["evidenceBlocks"] = _normalize_evidence_blocks(
        raw_value=payload.get("evidenceBlocks", []),
        clarity=clarity,
        citations=payload.get("citations", []),
        page_no=page_no,
    )
    payload = _reduce_semantic_overlap(payload)
    return payload


def stitch_page_explanation(
    *,
    payload: dict,
    page_no: int,
    prev_payload: dict | None = None,
    next_payload: dict | None = None,
) -> dict:
    out = dict(payload)
    continuity_raw = out.get("continuity", {})
    continuity = continuity_raw if isinstance(continuity_raw, dict) else {}
    scaffold_raw = out.get("scaffold", {})
    scaffold = scaffold_raw if isinstance(scaffold_raw, dict) else {}
    teaching_raw = out.get("teaching", {})
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}
    micro_raw = out.get("microTask", {})
    micro = micro_raw if isinstance(micro_raw, dict) else {}
    this_topic = _topic_hint(out, page_no=page_no)

    prev_topic = ""
    if isinstance(prev_payload, dict):
        prev_topic = _topic_hint(prev_payload, page_no=page_no - 1)

    next_topic = ""
    if isinstance(next_payload, dict):
        next_topic = _topic_hint(next_payload, page_no=page_no + 1)

    continuity["prevBridge"] = _first_non_empty(
        str(continuity.get("prevBridge", "")),
        (
            f"承接上一页：上一页核心是「{prev_topic}」，本页把它推进到「{this_topic}」。"
            if prev_topic
            else f"承接上一页：先回顾前一页结论，再看本页「{this_topic}」。"
        ),
    )
    continuity["thisPageNew"] = _first_non_empty(
        str(continuity.get("thisPageNew", "")),
        str(out.get("overview", "")),
        str((out.get("keyPoints") or [""])[0]),
        fallback=f"本页新增：围绕「{this_topic}」给出更具体的解释和应用。",
    )
    continuity["nextPreview"] = _first_non_empty(
        str(continuity.get("nextPreview", "")),
        (
            f"下一页预告：会从「{this_topic}」过渡到「{next_topic}」。"
            if next_topic
            else f"下一页预告：会继续用例子深化「{this_topic}」。"
        ),
    )
    out["continuity"] = {
        "prevBridge": _text(continuity["prevBridge"]),
        "thisPageNew": _text(continuity["thisPageNew"]),
        "nextPreview": _text(continuity["nextPreview"]),
    }

    quick30 = _as_string_list(scaffold.get("quick30"), limit=4)
    if not quick30:
        quick30 = _as_string_list(out.get("keyPoints"), limit=2)
    if not quick30:
        quick30 = [_first_non_empty(str(out.get("overview", "")), fallback=f"30 秒先看：{this_topic}")]

    understand2m = _as_string_list(scaffold.get("understand2m"), limit=6)
    if not understand2m:
        understand2m = _as_string_list([teaching.get("definition", ""), teaching.get("intuition", "")], limit=4)
    if not understand2m:
        understand2m = ["把这页当成“概念解释页”：先看定义，再看直觉。"]

    master5m = _as_string_list(scaffold.get("master5m"), limit=8)
    if not master5m:
        master5m = _as_string_list([teaching.get("example", ""), teaching.get("focus", ""), teaching.get("pitfall", "")], limit=6)
    if not master5m:
        master5m = [f"把「{this_topic}」讲给同学听，并补一个你自己的例子。"]

    out["scaffold"] = {
        "quick30": quick30,
        "understand2m": understand2m,
        "master5m": master5m,
    }

    out["microTask"] = {
        "doNow": _first_non_empty(
            str(micro.get("doNow", "")),
            f"现在先用 1 句话说明「{this_topic}」在解决什么问题。",
        ),
        "checkQuestion": _first_non_empty(
            str(micro.get("checkQuestion", "")),
            f"如果把「{this_topic}」里的术语换成白话，你还能解释同一个结论吗？",
        ),
        "answerHint": _first_non_empty(
            str(micro.get("answerHint", "")),
            str(out["continuity"]["thisPageNew"]),
            "答案提示：回到本页定义与例子对照。",  # noqa: E501
        ),
    }
    out = _reduce_semantic_overlap(out)
    return out


def run_agent_c_with_quality(
    *,
    llm_client: LLMClient,
    page: dict,
    global_memory: dict,
    group: dict,
    local_context: list[dict],
    language: str,
    quality_threshold: float,
    instruction: str | None = None,
    page_budget_seconds: float | None = None,
) -> tuple[dict, dict, str]:
    start_time = time.monotonic()
    deadline = start_time + float(page_budget_seconds) if page_budget_seconds and float(page_budget_seconds) > 0 else None

    def _time_left() -> float:
        if deadline is None:
            return 1e9
        return max(0.0, deadline - time.monotonic())

    def _budget_tight(*, reserve_s: float) -> bool:
        if deadline is None:
            return False
        return _time_left() <= max(0.0, reserve_s)

    def _brief_error(exc: Exception, *, limit: int = 180) -> str:
        text = str(exc).strip().replace("\n", " ")
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"

    page_no = int(page["page_no"])
    group_id = str(group.get("id", ""))
    local_pages = [int(item["page_no"]) for item in local_context if item.get("page_no")]
    raw_formulas = page.get("formulas", [])
    page_formulas = []
    if isinstance(raw_formulas, list):
        for item in raw_formulas:
            if not isinstance(item, dict):
                continue
            latex = str(item.get("latex", "")).strip()
            if not looks_like_formula_candidate(latex):
                continue
            page_formulas.append(item)
    context = PageContext(
        document_summary=str(global_memory.get("summary", "")),
        group_summary=str(group.get("summary", "")),
        page_no=page_no,
        page_text=page.get("text_content", ""),
        page_formulas=page_formulas,
        local_context=local_context,
    )

    def _mark_node(state: _AgentCGraphState, node: str) -> list[str]:
        return [*list(state.get("trace_nodes", [])), node]

    def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return _normalize_explanation(payload, page_no=page_no, group_id=group_id, local_pages=local_pages)

    def _evaluate_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return evaluate_page_explanation(
            page_no=page_no,
            page_text=page.get("text_content", ""),
            explanation=payload,
            global_keywords=global_memory.get("keywords", []),
            threshold=quality_threshold,
            scope_pages=payload.get("scopePages", []),
            language=language,
        )

    def _feedback_text(quality: dict[str, Any]) -> str:
        return " ".join(str(item) for item in (quality.get("feedback", []) or []) if str(item).strip())

    def agent_c_draft_node(state: _AgentCGraphState) -> _AgentCGraphState:
        payload = llm_client.explain_page(
            context,
            language=language,
            model_tier="flash",
            feedback=None,
            instruction=instruction,
        )
        normalized = _normalize_payload(payload)
        return {
            "payload": normalized,
            "quality": _evaluate_payload(normalized),
            "model_used": "flash",
            "trace_nodes": _mark_node(state, "agent_c_draft"),
        }

    def quality_gate_node(state: _AgentCGraphState) -> _AgentCGraphState:
        payload = dict(state.get("payload", {}))
        quality = _evaluate_payload(payload)
        citation_repair_attempted = bool(state.get("citation_repair_attempted", False))
        citation_repair_applied = bool(state.get("citation_repair_applied", False))
        model_used = str(state.get("model_used", "flash"))

        if float(quality.get("citationScore", 100.0)) < 70 and not citation_repair_attempted:
            citation_repair_attempted = True
            repaired_payload = repair_citation_alignment(
                payload=payload,
                page_no=page_no,
                page_text=str(page.get("text_content", "")),
                local_context=local_context,
                scope_pages=payload.get("scopePages", []),
            )
            repaired_payload = _normalize_payload(repaired_payload)
            repaired_quality = _evaluate_payload(repaired_payload)
            if (
                float(repaired_quality.get("citationScore", 0.0)) > float(quality.get("citationScore", 0.0))
                or float(repaired_quality.get("score", 0.0)) >= float(quality.get("score", 0.0))
            ):
                payload = repaired_payload
                quality = repaired_quality
                model_used = "flash-citation-repair"
                citation_repair_applied = True

        return {
            "payload": payload,
            "quality": quality,
            "model_used": model_used,
            "citation_repair_attempted": citation_repair_attempted,
            "citation_repair_applied": citation_repair_applied,
            "trace_nodes": _mark_node(state, "quality_gate"),
        }

    def _route_after_quality(state: _AgentCGraphState) -> Literal["reflection_retry", "fallback_reasoner", "finalize"]:
        quality = dict(state.get("quality", {}))
        if not quality:
            return "finalize"
        if bool(quality.get("pass", False)) or _is_citation_only_failure(quality):
            return "finalize"
        if not bool(state.get("rewrite_attempted", False)) and not _budget_tight(reserve_s=24.0):
            return "reflection_retry"
        if not bool(state.get("fallback_attempted", False)) and not _budget_tight(reserve_s=12.0):
            return "fallback_reasoner"
        return "finalize"

    def reflection_retry_node(state: _AgentCGraphState) -> _AgentCGraphState:
        payload = dict(state.get("payload", {}))
        quality = dict(state.get("quality", {}))
        model_used = str(state.get("model_used", "flash"))
        try:
            retry_payload = llm_client.explain_page(
                context,
                language=language,
                model_tier="flash",
                feedback=_feedback_text(quality),
                instruction=instruction,
            )
            retry_payload = _normalize_payload(retry_payload)
            retry_quality = _evaluate_payload(retry_payload)
            if float(retry_quality.get("score", 0.0)) > float(quality.get("score", 0.0)):
                payload = retry_payload
                quality = retry_quality
                model_used = "flash-rewrite"
        except Exception as exc:
            retry_msg = f"二次重写失败，已保留当前解释：{_brief_error(exc)}"
            payload.setdefault("qualityNotice", retry_msg)
            feedback_list = list(quality.get("feedback", [])) if isinstance(quality.get("feedback", []), list) else []
            feedback_list.append("二次重写失败，已保留当前解释。")
            quality["feedback"] = feedback_list

        return {
            "payload": payload,
            "quality": quality,
            "model_used": model_used,
            "rewrite_attempted": True,
            "trace_nodes": _mark_node(state, "reflection_retry"),
        }

    def fallback_reasoner_node(state: _AgentCGraphState) -> _AgentCGraphState:
        payload = dict(state.get("payload", {}))
        quality = dict(state.get("quality", {}))
        model_used = str(state.get("model_used", "flash"))
        try:
            fallback_payload = llm_client.explain_page(
                context,
                language=language,
                model_tier="fallback",
                feedback=_feedback_text(quality),
                instruction=instruction,
            )
            fallback_payload = _normalize_payload(fallback_payload)
            fallback_quality = _evaluate_payload(fallback_payload)
            if float(fallback_quality.get("score", 0.0)) >= float(quality.get("score", 0.0)):
                payload = fallback_payload
                quality = fallback_quality
                model_used = "fallback"
        except Exception as exc:
            fallback_msg = f"兜底重写失败，已保留当前解释：{_brief_error(exc)}"
            payload["qualityNotice"] = fallback_msg
            feedback_list = list(quality.get("feedback", [])) if isinstance(quality.get("feedback", []), list) else []
            feedback_list.append("兜底重写失败，已保留当前解释。")
            quality["feedback"] = feedback_list

        return {
            "payload": payload,
            "quality": quality,
            "model_used": model_used,
            "fallback_attempted": True,
            "trace_nodes": _mark_node(state, "fallback_reasoner"),
        }

    def finalize_node(state: _AgentCGraphState) -> _AgentCGraphState:
        payload = dict(state.get("payload", {}))
        quality = dict(state.get("quality", {}))
        model_used = str(state.get("model_used", "flash"))
        trace_nodes = _mark_node(state, "finalize")
        needs_more = bool(quality) and (not bool(quality.get("pass", False))) and not _is_citation_only_failure(quality)

        if needs_more and not bool(state.get("rewrite_attempted", False)) and _budget_tight(reserve_s=24.0):
            payload.setdefault("statusHint", "为避免任务超时，本页已跳过二次重写。")
        if needs_more and not bool(state.get("fallback_attempted", False)) and _budget_tight(reserve_s=12.0):
            payload.setdefault("statusHint", "为避免任务超时，本页已跳过兜底重写。")

        quality["citationRepairAttempted"] = bool(state.get("citation_repair_attempted", False))
        quality["citationRepaired"] = bool(state.get("citation_repair_applied", False))

        if not quality.get("pass", False):
            payload["confidence"] = min(float(payload.get("confidence", 0.7)), 0.55)
            payload.setdefault("qualityNotice", "该页解释未通过自动质控，建议人工复核或手动重生成。")
            payload.setdefault("statusHint", "该页解释置信度偏低，建议查看证据并手动重生成。")
        if bool(state.get("citation_repair_applied", False)):
            payload["statusHint"] = "该页已自动修复证据映射。"
        if quality.get("pass", False) and not payload.get("statusHint"):
            payload["statusHint"] = "该页解释已通过自动质控。"

        fallback_lines = [line.strip() for line in str(page.get("text_content", "")).splitlines() if line.strip()]
        payload["literalTranslation"] = _text("\n".join(fallback_lines[:10]))
        payload["translationStatus"] = "pending"
        payload["translationUpdatedAt"] = ""
        payload["translationError"] = ""
        if _budget_tight(reserve_s=8.0):
            if not payload.get("statusHint"):
                payload["statusHint"] = "为保证整体吞吐，本页直译先使用快速预览。"
        else:
            payload.setdefault("statusHint", "原文直译正在后台整理中。")

        payload["agentFramework"] = "LangGraph"
        payload["agentGraphTrace"] = {
            "framework": "LangGraph",
            "nodes": trace_nodes,
            "edges": [
                {"from": trace_nodes[idx], "to": trace_nodes[idx + 1]}
                for idx in range(max(0, len(trace_nodes) - 1))
            ],
            "quality_pass": bool(quality.get("pass", False)),
            "model_used": model_used,
            "citation_repair_attempted": bool(state.get("citation_repair_attempted", False)),
            "citation_repaired": bool(state.get("citation_repair_applied", False)),
            "reflection_retry_attempted": bool(state.get("rewrite_attempted", False)),
            "fallback_attempted": bool(state.get("fallback_attempted", False)),
        }

        return {
            "payload": payload,
            "quality": quality,
            "model_used": model_used,
            "trace_nodes": trace_nodes,
        }

    graph = StateGraph(_AgentCGraphState)
    graph.add_node("agent_c_draft", agent_c_draft_node)
    graph.add_node("quality_gate", quality_gate_node)
    graph.add_node("reflection_retry", reflection_retry_node)
    graph.add_node("fallback_reasoner", fallback_reasoner_node)
    graph.add_node("finalize", finalize_node)
    graph.set_entry_point("agent_c_draft")
    graph.add_edge("agent_c_draft", "quality_gate")
    graph.add_conditional_edges(
        "quality_gate",
        _route_after_quality,
        {
            "reflection_retry": "reflection_retry",
            "fallback_reasoner": "fallback_reasoner",
            "finalize": "finalize",
        },
    )
    graph.add_edge("reflection_retry", "quality_gate")
    graph.add_edge("fallback_reasoner", "quality_gate")
    graph.add_edge("finalize", END)

    result = graph.compile().invoke({"trace_nodes": []})
    return result["payload"], result["quality"], str(result.get("model_used", "flash"))
