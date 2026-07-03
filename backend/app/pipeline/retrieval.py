from __future__ import annotations

from app.utils.text import cosine_similarity


def _score_page(current_page_no: int, target_page_no: int, similarity: float) -> float:
    distance_penalty = 0.06 * abs(current_page_no - target_page_no)
    continuity_bonus = 0.25 if abs(current_page_no - target_page_no) <= 2 else 0.0
    return similarity - distance_penalty + continuity_bonus


def _coarse_summary(text: str, *, max_chars: int = 120) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    first_line = source.splitlines()[0].strip()
    if len(first_line) <= max_chars:
        return first_line
    return f"{first_line[:max_chars].rstrip()}..."


def _evidence_spans(text: str, *, max_spans: int = 2, max_chars: int = 140) -> list[str]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    spans: list[str] = []
    for line in lines[: max(1, max_spans)]:
        if len(line) <= max_chars:
            spans.append(line)
        else:
            spans.append(f"{line[:max_chars].rstrip()}...")
    return spans


def select_local_context(
    *,
    current_page: dict,
    all_pages: list[dict],
    top_k: int = 3,
) -> list[dict]:
    current_page_no = int(current_page["page_no"])
    current_embedding = current_page.get("embedding", [])
    pages_by_no = {int(page.get("page_no", 0)): page for page in all_pages if page.get("page_no")}

    # Enforce continuity pages first (cross-group allowed).
    forced_page_nos: list[int] = []
    for offset in (-2, -1, 1, 2):
        target = current_page_no + offset
        if target in pages_by_no:
            forced_page_nos.append(target)

    forced_pages = [pages_by_no[no] for no in forced_page_nos]
    forced_set = set(forced_page_nos)

    semantic_candidates: list[tuple[float, dict]] = []
    for page in all_pages:
        page_no = int(page["page_no"])
        if page_no == current_page_no:
            continue
        if page_no in forced_set:
            continue
        sim = cosine_similarity(current_embedding, page.get("embedding", []))
        score = _score_page(current_page_no, page_no, sim)
        semantic_candidates.append((score, page))

    semantic_candidates.sort(key=lambda item: item[0], reverse=True)
    semantic_pages = [page for _, page in semantic_candidates[:top_k]]

    page_map = {int(page["page_no"]): page for page in forced_pages}
    for page in semantic_pages:
        page_map[int(page["page_no"])] = page

    ordered = sorted(
        page_map.values(),
        key=lambda page: (
            0 if abs(int(page["page_no"]) - current_page_no) <= 2 else 1,
            -_score_page(
                current_page_no,
                int(page["page_no"]),
                cosine_similarity(current_embedding, page.get("embedding", [])),
            ),
            abs(int(page["page_no"]) - current_page_no),
        ),
    )

    return [
        {
            "page_no": int(page["page_no"]),
            "text": str(page.get("text_content", ""))[:1000],
            "group_id": page.get("group_id"),
            "coarse": _coarse_summary(str(page.get("text_content", ""))),
            "evidence": _evidence_spans(str(page.get("text_content", ""))),
        }
        for page in ordered
    ]
