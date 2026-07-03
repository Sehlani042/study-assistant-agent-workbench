from __future__ import annotations

import re
from typing import Any

INLINE_DOLLAR = re.compile(r"\$(.+?)\$", re.DOTALL)
INLINE_PAREN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
BLOCK_BRACKET = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)

LATEX_CMD_RE = re.compile(r"\\(frac|sum|int|sqrt|alpha|beta|gamma|theta|lambda|mu|sigma|pi)\b", re.IGNORECASE)
EQUATION_MARK_RE = re.compile(r"(=|<=|>=|≠|≈|\\leq|\\geq|≤|≥)")
BINARY_EXPR_RE = re.compile(r"([A-Za-z0-9\)\]])\s*([+\-*/^])\s*([A-Za-z0-9\(\[])")
LONG_WORD_RE = re.compile(r"[A-Za-z]{3,}")
WORD_RE = re.compile(r"[A-Za-z]+")
SYMBOLIC_EXPR_RE = re.compile(r"\b[A-Za-z]{1,2}\b\s*([+\-*/^])\s*\b[A-Za-z]{1,2}\b")
PAGE_MARKER_RE = re.compile(r"^(?:p(?:age)?\.?\s*)?\d{1,4}\s*/\s*\d{1,4}$", re.IGNORECASE)
CN_PAGE_MARKER_RE = re.compile(r"^第?\s*\d{1,4}\s*/\s*\d{1,4}\s*页?$")
SIMPLE_FRACTION_RE = re.compile(r"^(\d{1,4})\s*/\s*(\d{1,4})$")


def normalize_latex(latex: str) -> str:
    cleaned = latex.strip()
    cleaned = cleaned.replace("λ", "\\lambda ")
    cleaned = cleaned.replace("≤", "\\leq ")
    cleaned = cleaned.replace("≥", "\\geq ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _balanced(expr: str) -> bool:
    pairs = {')': '(', ']': '[', '}': '{'}
    stack: list[str] = []
    for ch in expr:
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return not stack


def validate_latex(latex: str) -> bool:
    candidate = latex.strip()
    if not candidate:
        return False
    return _balanced(candidate)


def repair_latex(latex: str) -> str:
    candidate = normalize_latex(latex)
    if validate_latex(candidate):
        return candidate

    # Small repair heuristic: trim unmatched right brackets.
    while candidate and not validate_latex(candidate):
        candidate = candidate[:-1].rstrip()
    return candidate


def _is_likely_page_marker(text: str) -> bool:
    candidate = text.strip()
    if PAGE_MARKER_RE.fullmatch(candidate) or CN_PAGE_MARKER_RE.fullmatch(candidate):
        return True

    match = SIMPLE_FRACTION_RE.fullmatch(candidate)
    if not match:
        return False

    numerator = int(match.group(1))
    denominator = int(match.group(2))
    # Keep simple math fractions like 1/2, but reject typical slide/page markers like 1/25.
    return denominator >= 10 and 1 <= numerator <= denominator


def _contains_page_marker_line(text: str) -> bool:
    return any(_is_likely_page_marker(line) for line in str(text or "").splitlines())


def looks_like_formula_candidate(text: str) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    if _is_likely_page_marker(candidate):
        return False
    if "\n" in candidate and _contains_page_marker_line(candidate):
        return False

    if LATEX_CMD_RE.search(candidate):
        return True
    if EQUATION_MARK_RE.search(candidate):
        return True
    if not BINARY_EXPR_RE.search(candidate):
        return False

    words = WORD_RE.findall(candidate)
    has_math_tokens = bool(re.search(r"[0-9_^{}\\()]", candidate))
    if not has_math_tokens:
        if SYMBOLIC_EXPR_RE.search(candidate):
            return True
        return False

    # Reject long plain-English sentences that only contain punctuation/hyphens.
    long_words = LONG_WORD_RE.findall(candidate)
    if len(long_words) >= 6 and "=" not in candidate and "\\" not in candidate:
        return False
    return True


def extract_latex_blocks(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def _append(raw: str, source: str) -> None:
        latex = normalize_latex(raw)
        repaired = repair_latex(latex)
        if not repaired:
            return
        out.append(
            {
                "latex": repaired,
                "sourceSpan": source,
                "valid": validate_latex(repaired),
            }
        )

    for match in INLINE_DOLLAR.finditer(text):
        _append(match.group(1), match.group(0))
    for match in INLINE_PAREN.finditer(text):
        _append(match.group(1), match.group(0))
    for match in BLOCK_BRACKET.finditer(text):
        _append(match.group(1), match.group(0))

    if out:
        return dedupe_formulas(out)

    # Fallback for common non-LaTeX equation lines.
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 3:
            continue
        if looks_like_formula_candidate(line):
            _append(line, line)

    return dedupe_formulas(out)


def dedupe_formulas(formulas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in formulas:
        key = item.get("latex", "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
