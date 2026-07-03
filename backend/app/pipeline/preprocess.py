from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import fitz  # type: ignore

from app.llm.base import LLMClient
from app.pipeline.formulas import (
    dedupe_formulas,
    extract_latex_blocks,
    looks_like_formula_candidate,
    repair_latex,
    validate_latex,
)
from app.utils.text import hash_embedding

PAGE_RENDER_SCALE = 2.2
_BULLET_RE = re.compile(r"^(\s*[-*•●▪◦·]|\s*\d+[\.\)])\s+")
_SHORT_DECORATIVE_RE = re.compile(r"^[\W_]{1,4}$")


def _should_attempt_visual_formula_recognition(text_content: str) -> bool:
    if not text_content.strip():
        return False

    # Skip expensive visual formula recognition for plain prose pages.
    for line in text_content.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if looks_like_formula_candidate(candidate):
            return True
    return False


def ensure_pdf(source_path: Path, source_type: str, target_dir: Path) -> Path:
    if source_type == "pdf":
        return source_path

    if source_type != "pptx":
        raise ValueError(f"unsupported source type: {source_type}")

    output_dir = target_dir / "converted"
    output_dir.mkdir(parents=True, exist_ok=True)

    command = [
        "soffice",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(source_path),
    ]

    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            "PPTX to PDF conversion failed. Please install LibreOffice (soffice)."
            f" stderr={completed.stderr.strip()}"
        )

    expected_pdf = output_dir / f"{source_path.stem}.pdf"
    if not expected_pdf.exists():
        raise RuntimeError("PPTX conversion did not produce expected PDF output")

    final_pdf = target_dir / "normalized.pdf"
    shutil.copy2(expected_pdf, final_pdf)
    return final_pdf


def _normalize_formulas(formulas: list[Any]) -> list[dict]:
    normalized: list[dict] = []
    for item in formulas:
        if isinstance(item, dict):
            raw_latex = str(item.get("latex", ""))
            source_span = str(item.get("sourceSpan", raw_latex))
        elif isinstance(item, str):
            raw_latex = item
            source_span = item
        else:
            continue

        latex = repair_latex(raw_latex.strip())
        if not latex:
            continue
        if not looks_like_formula_candidate(latex):
            continue
        normalized.append(
            {
                "latex": latex,
                "sourceSpan": source_span,
                "valid": validate_latex(latex),
            }
        )
    return dedupe_formulas(normalized)


def _normalize_bbox(*, x0: float, y0: float, x1: float, y1: float, width: float, height: float) -> dict[str, float]:
    safe_width = max(1.0, float(width or 1.0))
    safe_height = max(1.0, float(height or 1.0))
    left = max(0.0, min(float(x0), safe_width))
    top = max(0.0, min(float(y0), safe_height))
    right = max(left, min(float(x1), safe_width))
    bottom = max(top, min(float(y1), safe_height))
    return {
        "x": round(left / safe_width, 6),
        "y": round(top / safe_height, 6),
        "width": round((right - left) / safe_width, 6),
        "height": round((bottom - top) / safe_height, 6),
    }


def _looks_like_page_marker(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    if _SHORT_DECORATIVE_RE.fullmatch(value):
        return True
    compact = value.replace(" ", "")
    if compact.isdigit() and len(compact) <= 3:
        return True
    if re.fullmatch(r"\d{1,3}/\d{1,3}", compact):
        return True
    return False


def _infer_block_kind(*, text: str, bbox: dict[str, float], avg_font_size: float, page_height: float) -> str:
    stripped = str(text or "").strip()
    if not stripped:
        return "paragraph"
    if bbox.get("y", 0.0) >= 0.94 and avg_font_size <= 7.5:
        return "footer"
    if looks_like_formula_candidate(stripped):
        return "formula"
    if _BULLET_RE.match(stripped):
        return "list"
    if bbox.get("y", 0.0) <= 0.18 and (avg_font_size >= 15.0 or len(stripped) <= 80):
        return "title"
    if "|" in stripped and stripped.count("|") >= 2:
        return "table"
    if len(stripped) <= 24 and bbox.get("y", 0.0) >= 0.88:
        return "caption"
    return "paragraph"


def _extract_layout_blocks_from_rawdict(page: fitz.Page) -> list[dict[str, Any]]:
    rawdict = page.get_text("rawdict") or {}
    page_width = float(page.rect.width or 1.0)
    page_height = float(page.rect.height or 1.0)
    blocks: list[dict[str, Any]] = []
    reading_order = 0

    for block in rawdict.get("blocks", []) or []:
        if int(block.get("type", 0)) != 0:
            continue
        bbox = block.get("bbox") or (0, 0, 0, 0)
        if len(bbox) != 4:
            continue
        lines = block.get("lines") or []
        text_lines: list[str] = []
        font_sizes: list[float] = []
        for line in lines:
            spans = line.get("spans") or []
            text = "".join(str(span.get("text", "")) for span in spans).rstrip()
            if not text.strip():
                text = "".join(
                    str(char.get("c", ""))
                    for span in spans
                    for char in (span.get("chars") or [])
                    if isinstance(char, dict)
                ).rstrip()
            if text.strip():
                text_lines.append(text)
            for span in spans:
                try:
                    size = float(span.get("size", 0.0) or 0.0)
                except (TypeError, ValueError):
                    size = 0.0
                if size > 0:
                    font_sizes.append(size)
        text_content = "\n".join(text_lines).strip()
        if not text_content or _looks_like_page_marker(text_content):
            continue
        reading_order += 1
        avg_font_size = round(sum(font_sizes) / len(font_sizes), 2) if font_sizes else 0.0
        normalized_bbox = _normalize_bbox(
            x0=float(bbox[0]),
            y0=float(bbox[1]),
            x1=float(bbox[2]),
            y1=float(bbox[3]),
            width=page_width,
            height=page_height,
        )
        blocks.append(
            {
                "id": f"pdf-{reading_order}",
                "text": text_content,
                "bbox": normalized_bbox,
                "kind": _infer_block_kind(
                    text=text_content,
                    bbox=normalized_bbox,
                    avg_font_size=avg_font_size,
                    page_height=page_height,
                ),
                "source": "pdf_text",
                "confidence": 1.0,
                "font_size": avg_font_size,
                "reading_order": reading_order,
            }
        )

    return blocks


def _should_attempt_ocr(*, text_content: str, layout_blocks: list[dict[str, Any]]) -> bool:
    if layout_blocks:
        return False
    return len(str(text_content or "").strip()) < 20


def _extract_ocr_blocks(
    *,
    image_path: Path,
    page_width: float,
    page_height: float,
) -> list[dict[str, Any]]:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except Exception:
        return []

    engine = RapidOCR()
    result, _ = engine(str(image_path))
    if not result:
        return []

    rendered_width = max(1.0, float(page_width) * PAGE_RENDER_SCALE)
    rendered_height = max(1.0, float(page_height) * PAGE_RENDER_SCALE)
    blocks: list[dict[str, Any]] = []
    for idx, item in enumerate(result, start=1):
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        points, text, score = item[0], str(item[1] or "").strip(), float(item[2] or 0.0)
        if not text or _looks_like_page_marker(text):
            continue
        if not isinstance(points, (list, tuple)) or len(points) < 4:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                xs.append(float(point[0]))
                ys.append(float(point[1]))
            except (TypeError, ValueError):
                continue
        if not xs or not ys:
            continue
        normalized_bbox = _normalize_bbox(
            x0=min(xs),
            y0=min(ys),
            x1=max(xs),
            y1=max(ys),
            width=rendered_width,
            height=rendered_height,
        )
        blocks.append(
            {
                "id": f"ocr-{idx}",
                "text": text,
                "bbox": normalized_bbox,
                "kind": _infer_block_kind(
                    text=text,
                    bbox=normalized_bbox,
                    avg_font_size=0.0,
                    page_height=page_height,
                ),
                "source": "ocr",
                "confidence": round(score, 4),
                "font_size": 0.0,
                "reading_order": idx,
            }
        )
    return blocks


def _should_attempt_vision(
    *,
    source_type: str,
    text_content: str,
    layout_blocks: list[dict[str, Any]],
) -> bool:
    normalized_source = str(source_type or "").strip().lower()
    return normalized_source == "pptx"


def _build_vision_layout_blocks(
    *,
    vision_result: dict[str, Any],
    start_order: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    blocks: list[dict[str, Any]] = []
    supplemental_texts: list[str] = []
    visual_summary = str(vision_result.get("visual_summary", "") or "").strip()
    if visual_summary:
        supplemental_texts.append(f"视觉摘要：{visual_summary}")

    raw_blocks = vision_result.get("text_blocks", [])
    if not isinstance(raw_blocks, list):
        raw_blocks = []
    chart_notes = vision_result.get("chart_notes", [])
    if not isinstance(chart_notes, list):
        chart_notes = []

    normalized_items: list[dict[str, Any]] = []
    for raw in raw_blocks[:18]:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text", "") or "").strip()
        if not text or _looks_like_page_marker(text):
            continue
        kind = str(raw.get("kind", "paragraph") or "paragraph").strip().lower()
        if kind not in {"title", "paragraph", "list", "formula", "table", "chart", "caption"}:
            kind = "paragraph"
        try:
            confidence = float(raw.get("confidence", 0.75))
        except (TypeError, ValueError):
            confidence = 0.75
        normalized_items.append(
            {
                "text": text,
                "kind": kind,
                "confidence": round(max(0.0, min(1.0, confidence)), 4),
            }
        )

    for note in chart_notes[:6]:
        text = str(note or "").strip()
        if text:
            normalized_items.append({"text": f"图表说明：{text}", "kind": "chart", "confidence": 0.8})

    for idx, item in enumerate(normalized_items, start=1):
        reading_order = start_order + idx
        y = min(0.88, 0.08 + (idx - 1) * 0.07)
        height = 0.055 if item["kind"] != "title" else 0.07
        blocks.append(
            {
                "id": f"vision-{reading_order}",
                "text": item["text"],
                "bbox": {
                    "x": 0.07,
                    "y": round(y, 6),
                    "width": 0.86,
                    "height": round(height, 6),
                },
                "kind": item["kind"],
                "source": "openai_vision",
                "confidence": item["confidence"],
                "font_size": 0.0,
                "reading_order": reading_order,
            }
        )
        supplemental_texts.append(str(item["text"]))

    return blocks, supplemental_texts


def _merge_vision_text(text_content: str, supplemental_texts: list[str]) -> str:
    merged = str(text_content or "").strip()
    seen = {line.strip() for line in merged.splitlines() if line.strip()}
    additions: list[str] = []
    for text in supplemental_texts:
        clean = str(text or "").strip()
        if not clean or clean in seen:
            continue
        additions.append(clean)
        seen.add(clean)
    if not additions:
        return merged
    if merged:
        return f"{merged}\n" + "\n".join(additions)
    return "\n".join(additions)


def extract_pages(
    pdf_path: Path,
    output_dir: Path,
    llm_client: LLMClient,
    *,
    formula_instruction: str | None = None,
    source_type: str = "pdf",
    vision_client: LLMClient | None = None,
    vision_instruction: str | None = None,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    pages: list[dict] = []

    try:
        for index in range(doc.page_count):
            page = doc[index]
            page_no = index + 1
            page_width = float(page.rect.width or 0.0)
            page_height = float(page.rect.height or 0.0)
            text_content = page.get_text("text") or ""
            layout_blocks = _extract_layout_blocks_from_rawdict(page)

            formulas = extract_latex_blocks(text_content)
            if not formulas and _should_attempt_visual_formula_recognition(text_content):
                visual_formulas = llm_client.recognize_formulas_from_visual(
                    page_text=text_content,
                    instruction=formula_instruction,
                )
                formulas = _normalize_formulas(visual_formulas)
            elif formulas:
                formulas = _normalize_formulas(formulas)
            else:
                formulas = []

            image_path = pages_dir / f"page-{page_no:04d}.png"
            matrix = fitz.Matrix(PAGE_RENDER_SCALE, PAGE_RENDER_SCALE)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pix.save(image_path)

            if _should_attempt_ocr(text_content=text_content, layout_blocks=layout_blocks):
                ocr_blocks = _extract_ocr_blocks(
                    image_path=image_path,
                    page_width=page_width,
                    page_height=page_height,
                )
                if ocr_blocks:
                    layout_blocks = ocr_blocks
                    if not text_content.strip():
                        text_content = "\n".join(str(block.get("text", "")).strip() for block in ocr_blocks if str(block.get("text", "")).strip())

            if (
                vision_client is not None
                and hasattr(vision_client, "describe_page_image")
                and _should_attempt_vision(
                    source_type=source_type,
                    text_content=text_content,
                    layout_blocks=layout_blocks,
                )
            ):
                try:
                    vision_result = vision_client.describe_page_image(
                        image_path=image_path,
                        page_text=text_content,
                        instruction=vision_instruction,
                    )
                except Exception:
                    vision_result = {}
                if isinstance(vision_result, dict) and vision_result:
                    vision_blocks, supplemental_texts = _build_vision_layout_blocks(
                        vision_result=vision_result,
                        start_order=len(layout_blocks),
                    )
                    if vision_blocks:
                        layout_blocks = [*layout_blocks, *vision_blocks]
                    text_content = _merge_vision_text(text_content, supplemental_texts)

            pages.append(
                {
                    "page_no": page_no,
                    "text_content": text_content.strip(),
                    "formulas": formulas,
                    "image_path": str(image_path),
                    "embedding": hash_embedding(text_content),
                    "page_width": page_width,
                    "page_height": page_height,
                    "layout_blocks": layout_blocks,
                }
            )
    finally:
        doc.close()

    if not pages:
        raise RuntimeError("document has zero pages after preprocessing")

    return pages
