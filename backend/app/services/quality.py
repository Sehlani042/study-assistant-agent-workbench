from __future__ import annotations

import re

from app.pipeline.formulas import validate_latex
from app.utils.text import top_keywords, tokenize

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_TEMPLATE_HINTS = (
    "承接上一页的核心结论",
    "下一页会继续深化本页概念",
    "尝试复述本页重点并给一个例子",
    "用一句话复述本页",
    "不要只记结论，忽略公式适用条件",
    "承接上一页：先回顾前一页结论，再看本页新增内容",
    "下一页预告：会继续围绕本页概念做深化或应用",
    "可结合本页关键词进行练习",
)


def _round(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _cjk_ratio(text: str) -> float:
    candidates = [ch for ch in text if ch.isalpha() or _CJK_RE.match(ch)]
    if not candidates:
        return 0.0
    cjk_count = sum(1 for ch in candidates if _CJK_RE.match(ch))
    return cjk_count / len(candidates)


def _build_explanation_text(explanation: dict) -> str:
    teaching_raw = explanation.get("teaching", {})
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}

    key_points_raw = explanation.get("keyPoints", [])
    key_points = key_points_raw if isinstance(key_points_raw, list) else [str(key_points_raw)]

    return " ".join(
        [
            _safe_text(explanation.get("overview")),
            " ".join(_safe_text(item) for item in key_points),
            _safe_text(teaching.get("definition")),
            _safe_text(teaching.get("intuition")),
            _safe_text(teaching.get("example")),
            _safe_text(teaching.get("focus")),
            _safe_text(teaching.get("pitfall")),
        ]
    )


def _iter_text_values(value: object) -> list[str]:
    if isinstance(value, str):
        text = _safe_text(value)
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_iter_text_values(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_iter_text_values(item))
        return out
    text = _safe_text(value)
    return [text] if text else []


def _coverage_channels(explanation: dict) -> tuple[str, str]:
    teaching_raw = explanation.get("teaching", {})
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}
    clarity_raw = explanation.get("clarity", {})
    clarity = clarity_raw if isinstance(clarity_raw, dict) else {}

    primary_segments: list[str] = []
    primary_segments.extend(_iter_text_values(explanation.get("overview", "")))
    primary_segments.extend(_iter_text_values(explanation.get("keyPoints", [])))
    primary_segments.extend(_iter_text_values(explanation.get("conceptLinks", [])))
    primary_segments.extend(_iter_text_values(teaching))
    primary_segments.extend(_iter_text_values(clarity))

    evidence_segments: list[str] = []
    evidence_segments.extend(_iter_text_values(explanation.get("citations", [])))
    evidence_segments.extend(_iter_text_values(explanation.get("evidenceBlocks", [])))
    evidence_segments.extend(_iter_text_values(explanation.get("formulaBlocks", [])))
    evidence_segments.extend(_iter_text_values(explanation.get("scopePages", [])))
    return (" ".join(primary_segments), " ".join(evidence_segments))


def _keyword_match_score(*, keywords: list[str], text: str) -> float:
    if not keywords:
        return 0.0
    text_lower = str(text or "").lower()
    matched = 0
    for kw in keywords:
        token = str(kw or "").strip().lower()
        if not token:
            continue
        if token in text_lower:
            matched += 1
    return (matched / max(1, len(keywords))) * 100


def _cross_language_topic_signal(explanation: dict) -> float:
    teaching_raw = explanation.get("teaching", {})
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}
    clarity_raw = explanation.get("clarity", {})
    clarity = clarity_raw if isinstance(clarity_raw, dict) else {}
    anchors = [
        _safe_text(explanation.get("overview", "")),
        _safe_text(clarity.get("conclusion", "")),
        _safe_text(teaching.get("definition", "")),
        _safe_text(teaching.get("intuition", "")),
        _safe_text(teaching.get("focus", "")),
    ]
    filled = sum(1 for item in anchors if item)
    return min(100.0, (filled / 5) * 100)


def _token_set(text: str) -> set[str]:
    return {tok for tok in tokenize(str(text).lower()) if len(tok) > 1}


def _semantic_overlap(a: str, b: str) -> float:
    text_a = _safe_text(a)
    text_b = _safe_text(b)
    if not text_a or not text_b:
        return 0.0
    if text_a == text_b:
        return 1.0
    ta = _token_set(text_a)
    tb = _token_set(text_b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if inter <= 0:
        return 0.0
    union = len(ta | tb)
    jaccard = inter / max(1, union)
    containment = max(inter / max(1, len(ta)), inter / max(1, len(tb)))
    return max(jaccard, containment)


def _collect_overlap_segments(explanation: dict) -> list[str]:
    teaching_raw = explanation.get("teaching", {})
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}
    clarity_raw = explanation.get("clarity", {})
    clarity = clarity_raw if isinstance(clarity_raw, dict) else {}
    scaffold_raw = explanation.get("scaffold", {})
    scaffold = scaffold_raw if isinstance(scaffold_raw, dict) else {}
    micro_raw = explanation.get("microTask", {})
    micro = micro_raw if isinstance(micro_raw, dict) else {}

    segments: list[str] = []
    segments.append(_safe_text(explanation.get("overview", "")))
    key_points_raw = explanation.get("keyPoints", [])
    key_points = key_points_raw if isinstance(key_points_raw, list) else [key_points_raw]
    segments.extend(_safe_text(item) for item in key_points)
    for key in ("definition", "intuition", "example", "focus", "pitfall"):
        segments.append(_safe_text(teaching.get(key, "")))
    segments.append(_safe_text(clarity.get("conclusion", "")))
    clarity_steps_raw = clarity.get("steps", [])
    clarity_steps = clarity_steps_raw if isinstance(clarity_steps_raw, list) else [clarity_steps_raw]
    segments.extend(_safe_text(item) for item in clarity_steps)
    segments.append(_safe_text(clarity.get("example", "")))
    for key in ("quick30", "understand2m", "master5m"):
        values_raw = scaffold.get(key, [])
        values = values_raw if isinstance(values_raw, list) else [values_raw]
        segments.extend(_safe_text(item) for item in values)
    for key in ("doNow", "checkQuestion", "answerHint"):
        segments.append(_safe_text(micro.get(key, "")))
    return [item for item in segments if len(item) >= 8]


def evaluate_page_explanation(
    *,
    page_no: int,
    page_text: str,
    explanation: dict,
    global_keywords: list[str],
    threshold: float,
    scope_pages: list[int] | None = None,
    language: str | None = None,
) -> dict:
    expl_text = _build_explanation_text(explanation)
    explanation_lower = expl_text.lower()
    scope_set = {int(x) for x in (scope_pages or explanation.get("scopePages") or []) if str(x).strip()} if isinstance(
        scope_pages or explanation.get("scopePages"), list
    ) else {int(page_no)}
    if int(page_no) not in scope_set:
        scope_set.add(int(page_no))

    page_keywords = top_keywords(page_text, top_n=8)
    coverage_lang_mode = "direct"
    if page_keywords:
        normalized_keywords = [str(kw or "").strip().lower() for kw in page_keywords if str(kw or "").strip()]
        primary_channel, evidence_channel = _coverage_channels(explanation)
        direct_coverage = _keyword_match_score(keywords=normalized_keywords, text=primary_channel)
        evidence_coverage = _keyword_match_score(keywords=normalized_keywords, text=evidence_channel)
        keyword_latin_ratio = (
            sum(1 for kw in normalized_keywords if _LATIN_RE.search(kw)) / max(1, len(normalized_keywords))
        )
        language_mode = str(language or "").strip().lower()
        topic_signal = _cross_language_topic_signal(explanation)
        if language_mode == "zh" and keyword_latin_ratio >= 0.55:
            # Cross-language documents (English slides + Chinese explanation):
            # use evidence/citation channel and topic signal to avoid systematic under-scoring.
            coverage_lang_mode = "dual_zh_crosslingual"
            blended = max(
                direct_coverage * 0.45 + evidence_coverage * 0.55,
                evidence_coverage * 0.75 + topic_signal * 0.25,
            )
            floor = 0.0
            if evidence_coverage >= 45 and topic_signal >= 70:
                floor = 62.0
            elif evidence_coverage >= 35 and topic_signal >= 60:
                floor = 56.0
            coverage = max(blended, floor)
        else:
            coverage_lang_mode = "dual_default"
            coverage = max(
                direct_coverage,
                direct_coverage * 0.65 + evidence_coverage * 0.35,
            )
    else:
        coverage = 70.0

    citations_raw = explanation.get("citations", []) or []
    citations = citations_raw if isinstance(citations_raw, list) else []
    out_of_scope_citations = 0
    if citations:
        valid = 0
        text_lower = str(page_text or "").lower()
        for item in citations:
            if not isinstance(item, dict):
                continue
            quote = _safe_text(item.get("quote")).lower()
            try:
                pno = int(item.get("pageNo"))
            except (TypeError, ValueError):
                continue
            if pno not in scope_set:
                out_of_scope_citations += 1
                continue
            if pno == page_no:
                if quote and quote[:60] in text_lower:
                    valid += 1
            elif quote:
                # Neighbor page quotes are accepted when in scope.
                valid += 1
        citation_score = (valid / max(1, len(citations))) * 100
    else:
        citation_score = 10.0

    clarity_raw = explanation.get("clarity", {})
    clarity = clarity_raw if isinstance(clarity_raw, dict) else {}
    clarity_conclusion = _safe_text(clarity.get("conclusion"))
    clarity_steps_raw = clarity.get("steps", [])
    clarity_steps = clarity_steps_raw if isinstance(clarity_steps_raw, list) else []
    clarity_steps = [_safe_text(item) for item in clarity_steps if _safe_text(item)]
    clarity_example = _safe_text(clarity.get("example"))
    clarity_hits = sum(
        1
        for item in (
            clarity_conclusion,
            "ok" if len(clarity_steps) >= 3 else "",
            clarity_example,
        )
        if item
    )

    evidence_blocks_raw = explanation.get("evidenceBlocks", [])
    evidence_blocks = evidence_blocks_raw if isinstance(evidence_blocks_raw, list) else []
    evidence_with_citations = 0
    for block in evidence_blocks:
        if not isinstance(block, dict):
            continue
        block_claim = _safe_text(block.get("claim"))
        block_citations = block.get("citations", [])
        if block_claim and isinstance(block_citations, list) and len(block_citations) > 0:
            evidence_with_citations += 1
    if evidence_blocks and evidence_with_citations == 0:
        citation_score = min(citation_score, 35.0)

    formulas_raw = explanation.get("formulaBlocks", []) or []
    formulas = formulas_raw if isinstance(formulas_raw, list) else []
    if formulas:
        valid_formulas = 0
        for item in formulas:
            if not isinstance(item, dict):
                continue
            latex = _safe_text(item.get("latex"))
            if validate_latex(latex):
                valid_formulas += 1
        formula_render_rate = (valid_formulas / max(1, len(formulas))) * 100
    else:
        formula_render_rate = 100.0

    if global_keywords:
        consistency_hits = sum(1 for kw in global_keywords[:8] if kw.lower() in explanation_lower)
        terminology_consistency = min(100.0, 25.0 * consistency_hits)
    else:
        terminology_consistency = 75.0

    continuity_raw = explanation.get("continuity", {})
    continuity = continuity_raw if isinstance(continuity_raw, dict) else {}
    prev_bridge = _safe_text(continuity.get("prevBridge"))
    this_page_new = _safe_text(continuity.get("thisPageNew"))
    next_preview = _safe_text(continuity.get("nextPreview"))
    continuity_hits = sum(1 for item in (prev_bridge, this_page_new, next_preview) if item)
    continuity_score = (continuity_hits / 3) * 100

    teaching_raw = explanation.get("teaching", {})
    teaching = teaching_raw if isinstance(teaching_raw, dict) else {}
    example_text = _safe_text(teaching.get("example"))
    specific_markers = 0
    if example_text:
        specific_markers += 1
    if any(token in explanation_lower for token in ("例如", "比如", "for example")):
        specific_markers += 1
    if any(ch.isdigit() for ch in expl_text):
        specific_markers += 1
    if isinstance(explanation.get("keyPoints"), list) and len(explanation.get("keyPoints") or []) >= 2:
        specific_markers += 1
    if clarity_hits >= 2:
        specific_markers += 1
    specificity_score = min(100.0, (specific_markers / 4) * 100)

    micro_raw = explanation.get("microTask", {})
    micro = micro_raw if isinstance(micro_raw, dict) else {}
    do_now = _safe_text(micro.get("doNow"))
    check_question = _safe_text(micro.get("checkQuestion"))
    answer_hint = _safe_text(micro.get("answerHint"))
    action_hits = sum(1 for item in (do_now, check_question, answer_hint) if item)
    actionability_score = (action_hits / 3) * 100

    overlap_segments = _collect_overlap_segments(explanation)
    pair_total = 0
    repeated_pairs = 0
    for idx in range(len(overlap_segments)):
        for jdx in range(idx + 1, len(overlap_segments)):
            pair_total += 1
            if _semantic_overlap(overlap_segments[idx], overlap_segments[jdx]) >= 0.9:
                repeated_pairs += 1
    if pair_total <= 0:
        semantic_overlap_score = 100.0
    else:
        overlap_ratio = repeated_pairs / pair_total
        semantic_overlap_score = max(0.0, 100.0 - overlap_ratio * 160.0)

    scaffold_raw = explanation.get("scaffold", {})
    scaffold = scaffold_raw if isinstance(scaffold_raw, dict) else {}
    scaffold_texts = []
    for key in ("quick30", "understand2m", "master5m"):
        values = scaffold.get(key, [])
        if isinstance(values, list):
            scaffold_texts.extend(_safe_text(item) for item in values)
        else:
            scaffold_texts.append(_safe_text(values))

    template_check_text = " ".join(
        [
            prev_bridge,
            this_page_new,
            next_preview,
            do_now,
            check_question,
            answer_hint,
            _safe_text(teaching.get("pitfall")),
            *scaffold_texts,
        ]
    ).lower()
    boilerplate_hits = sum(1 for phrase in _TEMPLATE_HINTS if phrase.lower() in template_check_text)

    language_mode = str(language or "").strip().lower()
    language_score = 100.0
    if language_mode == "zh":
        ratio = _cjk_ratio(expl_text)
        if ratio >= 0.35:
            language_score = 100.0
        elif ratio >= 0.22:
            language_score = 80.0
        elif ratio >= 0.12:
            language_score = 55.0
        else:
            language_score = 20.0

    score = (
        0.25 * coverage
        + 0.20 * citation_score
        + 0.10 * formula_render_rate
        + 0.10 * terminology_consistency
        + 0.20 * continuity_score
        + 0.10 * specificity_score
        + 0.05 * actionability_score
    )
    if boilerplate_hits >= 2:
        score -= min(25.0, float(boilerplate_hits * 8))
    if language_mode == "zh" and language_score < 70:
        score -= min(20.0, (70.0 - language_score) * 0.35)
    if semantic_overlap_score < 70:
        score -= min(22.0, (70.0 - semantic_overlap_score) * 0.45)

    feedback: list[str] = []
    hard_fail = False
    if coverage < 65:
        feedback.append("需要更完整覆盖当前页核心概念。")
    if citation_score < 60:
        feedback.append("引用证据不足或引用未对齐当前页面。")
    if clarity_hits < 3:
        feedback.append("讲解结构不足：请补齐“结论 + 至少3步讲解 + 具体例子”。")
    if evidence_blocks and evidence_with_citations == 0:
        feedback.append("证据块缺少引用绑定：请为结论/步骤/例子补充原文证据。")
    if formula_render_rate < 85:
        feedback.append("公式表达存在不可渲染项，请修复 LaTeX。")
    if terminology_consistency < 50:
        feedback.append("术语与文档主线不一致，请统一表述。")
    if continuity_score < 100:
        feedback.append("连续性不足：请补齐上一页承接、本页新增和下一页预告。")
    if specificity_score < 50:
        feedback.append("解释偏抽象：请增加具体例子或场景化描述。")
    if actionability_score < 50:
        feedback.append("缺少可执行微任务：请明确“现在先做什么”。")
    if semantic_overlap_score < 60:
        feedback.append("解释重复度偏高：请减少同义反复，让三层台阶逐层递进。")
    if boilerplate_hits >= 2:
        feedback.append("解释模板化过强：请替换空泛套话，改为本页具体内容与具体例子。")
    if language_mode == "zh" and language_score < 70:
        feedback.append("中文模式下英文占比过高：请用中文讲解，术语可附英文括注。")

    if not prev_bridge or not next_preview:
        hard_fail = True
        feedback.append("连续性硬约束未满足：必须包含上一页承接和下一页预告。")
    if not example_text and not do_now:
        hard_fail = True
        feedback.append("具体性硬约束未满足：至少提供一个具体例子或可执行步骤。")
    if not clarity_conclusion or len(clarity_steps) < 3 or not clarity_example:
        hard_fail = True
        feedback.append("讲明白硬约束未满足：必须包含“结论 + 至少3步讲解 + 具体例子”。")
    if out_of_scope_citations > 0:
        hard_fail = True
        feedback.append("引用范围硬约束未满足：存在超出本页问答范围的引用页。")
    if boilerplate_hits >= 3:
        hard_fail = True
        feedback.append("模板化硬约束未满足：请删除通用占位句并改写为本页具体解释。")
    if language_mode == "zh" and language_score < 40:
        hard_fail = True
        feedback.append("语言硬约束未满足：中文输出不足。")
    if semantic_overlap_score < 35:
        hard_fail = True
        feedback.append("语义重复硬约束未满足：多个区块内容高度重复。")

    score = _round(score)
    passed = (score >= threshold) and (not hard_fail)
    return {
        "score": score,
        "coverage": _round(coverage),
        "coverageLangMode": str(coverage_lang_mode),
        "citationScore": _round(citation_score),
        "formulaRenderRate": _round(formula_render_rate),
        "terminologyConsistency": _round(terminology_consistency),
        "continuityScore": _round(continuity_score),
        "specificityScore": _round(specificity_score),
        "actionabilityScore": _round(actionability_score),
        "semanticOverlapScore": _round(semantic_overlap_score),
        "languageScore": _round(language_score),
        "boilerplateHits": int(boilerplate_hits),
        "hardFailed": hard_fail,
        "pass": passed,
        "feedback": feedback,
    }
