from __future__ import annotations

import re
from typing import Any

from app.llm.base import LLMClient
from app.pipeline.formulas import looks_like_formula_candidate
from app.utils.markdown_math import normalize_math_markdown

BULLET_PREFIX_RE = re.compile(r"^(\s*)[•●▪◦·]\s+")
ORDERED_PREFIX_RE = re.compile(r"^(\s*)(\d{1,3})[\)\.]?\s+")
TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")
PAGE_MARKER_RE = re.compile(r"^\d{1,3}/\d{1,3}$")
TEACHING_SCAFFOLD_RE = re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?(结论|三步讲解|例子|立即可做的小任务|立即可做|教学解释)(?:\*\*)?\s*[：:]", re.MULTILINE)
MARKDOWN_PREFIX_RE = re.compile(r"^(#{1,6}\s*|[-*]\s+|\d+\.\s+|>\s+)")
INLINE_EMPHASIS_RE = re.compile(r"[*_`]+")
ASCII_WORD_RE = re.compile(r"[A-Za-z]{3,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def normalize_translation_layout(raw: str) -> str:
    source = str(raw or "").replace("\r\n", "\n")
    lines = source.split("\n")
    out: list[str] = []
    in_code_fence = False
    previous_blank = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            out.append(stripped)
            previous_blank = False
            continue

        if in_code_fence:
            out.append(line.rstrip("\n"))
            previous_blank = False
            continue

        if not stripped:
            if out and not previous_blank:
                out.append("")
            previous_blank = True
            continue

        normalized = line.rstrip()
        normalized = BULLET_PREFIX_RE.sub(r"\1- ", normalized)
        normalized = ORDERED_PREFIX_RE.sub(r"\1\2. ", normalized)

        table_like = normalized.strip()
        has_table_bars = table_like.count("|") >= 2 and (
            table_like.startswith("|") or table_like.endswith("|") or " | " in table_like
        )
        if has_table_bars:
            cells = [cell.strip() for cell in table_like.strip("|").split("|")]
            if all(TABLE_SEPARATOR_CELL_RE.fullmatch(cell or "---") for cell in cells):
                separator = [cell if TABLE_SEPARATOR_CELL_RE.fullmatch(cell) else "---" for cell in cells]
                normalized = "| " + " | ".join(separator) + " |"
            else:
                normalized = "| " + " | ".join(cells) + " |"

        out.append(normalized)
        previous_blank = False

    return "\n".join(out).strip()


def run_agent_t_translation(
    *,
    llm_client: LLMClient,
    page_text: str,
    language: str,
    instruction: str | None = None,
    allow_source_fallback: bool = True,
) -> str:
    """Agent T: literal page translation only, no explanation."""
    try:
        translated = llm_client.translate_page_text(
            page_text=page_text,
            language=language,
            instruction=instruction,
        )
    except Exception:
        if not allow_source_fallback:
            raise
        translated = ""

    normalized = normalize_translation_layout(str(translated or "").strip())
    normalized = normalize_math_markdown(normalized)
    if normalized:
        if not allow_source_fallback and _looks_untranslated_output(
            source_text=page_text,
            translated_text=normalized,
            language=language,
        ):
            raise RuntimeError("translation appears unchanged")
        return normalized

    if not allow_source_fallback:
        raise RuntimeError("empty translation")

    lines = [line.strip() for line in str(page_text or "").splitlines() if line.strip()]
    fallback = normalize_translation_layout("\n".join(lines[:10]))
    return normalize_math_markdown(fallback)


def _looks_untranslated_output(*, source_text: str, translated_text: str, language: str) -> bool:
    if str(language or "").strip().lower() != "zh":
        return False
    source = str(source_text or "").strip()
    translated = str(translated_text or "").strip()
    if not source or not translated:
        return False
    source_words = ASCII_WORD_RE.findall(source)
    if len(source_words) < 2:
        return False
    if CJK_RE.search(translated):
        return False
    source_compact = re.sub(r"\s+", " ", source).strip().lower()
    translated_compact = re.sub(r"\s+", " ", translated).strip().lower()
    if source_compact == translated_compact:
        return True
    translated_words = set(word.lower() for word in ASCII_WORD_RE.findall(translated))
    source_word_set = set(word.lower() for word in source_words)
    if not source_word_set:
        return False
    overlap = len(source_word_set & translated_words) / max(1, len(source_word_set))
    return overlap >= 0.85


def _sanitize_block_translation(*, source_text: str, translated_text: str, kind: str) -> str:
    normalized = normalize_math_markdown(normalize_translation_layout(translated_text or "")).strip()
    if not normalized:
        return normalize_math_markdown(normalize_translation_layout(source_text or "")).strip()

    kind_value = str(kind or "paragraph").strip().lower()
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return normalized

    if kind_value == "title":
        for line in lines:
            if TEACHING_SCAFFOLD_RE.search(line):
                continue
            candidate = MARKDOWN_PREFIX_RE.sub("", line).strip()
            candidate = INLINE_EMPHASIS_RE.sub("", candidate).strip()
            if candidate:
                return candidate
        return INLINE_EMPHASIS_RE.sub("", MARKDOWN_PREFIX_RE.sub("", lines[0])).strip()

    if TEACHING_SCAFFOLD_RE.search(normalized):
        trimmed_lines: list[str] = []
        for line in lines:
            if TEACHING_SCAFFOLD_RE.search(line):
                break
            trimmed_lines.append(line)
        compact = "\n".join(trimmed_lines).strip()
        if compact:
            return compact

    return normalized


def _should_translate_block(block: dict[str, Any]) -> tuple[bool, str]:
    text = str(block.get("text", "")).strip()
    kind = str(block.get("kind", "paragraph")).strip().lower()
    source = str(block.get("source", "pdf_text")).strip().lower()
    confidence = float(block.get("confidence", 1.0) or 0.0)
    compact = text.replace(" ", "")
    if not text:
        return False, "empty"
    if PAGE_MARKER_RE.fullmatch(compact):
        return False, "page_marker"
    if compact.isdigit() and len(compact) <= 3:
        return False, "page_marker"
    if kind in {"caption", "footer"}:
        return False, "page_marker"
    if looks_like_formula_candidate(text) or kind == "formula":
        return False, "formula"
    if source == "ocr" and confidence < 0.72:
        return False, "low_confidence_ocr"
    if len(text) <= 1:
        return False, "too_short"
    return True, ""


def _fit_font_size(*, source_font_size: float, source_text: str, translated_text: str) -> float:
    base = float(source_font_size or 14.0)
    source_len = max(1, len(str(source_text or "").strip()))
    translated_len = max(1, len(str(translated_text or "").strip()))
    ratio = min(1.15, max(0.62, source_len / translated_len))
    return round(max(9.0, min(28.0, base * ratio)), 2)


def translate_layout_blocks(
    *,
    llm_client: LLMClient,
    layout_blocks: list[dict[str, Any]],
    language: str,
    instruction: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str]:
    translation_blocks: list[dict[str, Any]] = []
    untranslated_blocks: list[dict[str, Any]] = []
    reading_parts: list[str] = []

    for order, block in enumerate(layout_blocks, start=1):
        text = str(block.get("text", "")).strip()
        should_translate, skip_reason = _should_translate_block(block)
        if not should_translate:
            untranslated_blocks.append(
                {
                    "block_id": str(block.get("id", f"block-{order}")),
                    "text": text,
                    "kind": str(block.get("kind", "paragraph")),
                    "source": str(block.get("source", "pdf_text")),
                    "reason": skip_reason,
                    "confidence": float(block.get("confidence", 1.0) or 0.0),
                }
            )
            continue

        try:
            translated = run_agent_t_translation(
                llm_client=llm_client,
                page_text=text,
                language=language,
                instruction=(
                    "当前输入只是一段单独文字块。保持同等粒度翻译，不要扩写成结论、讲解、例子或任务。"
                    if not instruction
                    else f"当前输入只是一段单独文字块。保持同等粒度翻译，不要扩写成结论、讲解、例子或任务。\n{instruction}"
                ),
                allow_source_fallback=False,
            )
        except Exception as exc:
            untranslated_blocks.append(
                {
                    "block_id": str(block.get("id", f"block-{order}")),
                    "text": text,
                    "kind": str(block.get("kind", "paragraph")),
                    "source": str(block.get("source", "pdf_text")),
                    "reason": "translation_failed",
                    "confidence": float(block.get("confidence", 1.0) or 0.0),
                    "error": str(exc)[:240],
                }
            )
            continue
        translated_text = _sanitize_block_translation(
            source_text=text,
            translated_text=str(translated or ""),
            kind=str(block.get("kind", "paragraph")),
        ) or text
        reading_parts.append(translated_text)
        translation_blocks.append(
            {
                "id": f"t-{block.get('id', order)}",
                "block_id": str(block.get("id", f"block-{order}")),
                "text": translated_text,
                "bbox": dict(block.get("bbox", {})),
                "kind": str(block.get("kind", "paragraph")),
                "source": str(block.get("source", "pdf_text")),
                "confidence": float(block.get("confidence", 1.0) or 0.0),
                "line_count": max(1, translated_text.count("\n") + 1),
                "fitted_font_size": _fit_font_size(
                    source_font_size=float(block.get("font_size", 14.0) or 14.0),
                    source_text=text,
                    translated_text=translated_text,
                ),
                "reading_order": int(block.get("reading_order", order) or order),
                "status": "ready",
            }
        )

    overlay_status = "empty"
    if translation_blocks and not untranslated_blocks:
        overlay_status = "ready"
    elif translation_blocks:
        overlay_status = "partial"
    elif untranslated_blocks:
        overlay_status = "unavailable"

    literal_translation = normalize_translation_layout("\n\n".join(part for part in reading_parts if part.strip()))
    literal_translation = normalize_math_markdown(literal_translation)
    return translation_blocks, untranslated_blocks, literal_translation, overlay_status
