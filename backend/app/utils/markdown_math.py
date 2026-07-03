from __future__ import annotations

import re

FORMULA_LABEL_RE = re.compile(
    r"^(\s*(?:[-*]\s*)?(?:模型公式|公式|方程|equation|model(?:\s+equation)?)\s*[：:]\s*)(.+)$",
    re.IGNORECASE,
)
PURE_EQUATION_RE = re.compile(
    r"^([A-Za-z\u0370-\u03FF][A-Za-z0-9_\u0370-\u03FF()\[\]{}|.,\s+\-*/^\\=<>−×÷]{1,220})$"
)
INLINE_SUBSCRIPT_TOKEN_RE = re.compile(
    r"(^|[\s(（:：,，])([A-Za-z\u0370-\u03FF]+_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)?)(?=([\s)）:：,，。；;]|$))"
)
FORMULA_WITH_SUFFIX_RE = re.compile(
    r"^(?P<formula>[A-Za-z0-9_\u0370-\u03FF()\[\]{}|.,\s+\-*/^\\=<>−×÷•·]{2,220}?)\s*(?P<suffix>(?:（[^）]{1,40}）|\([^()]*[\u4e00-\u9fff][^()]*\)))$"
)
PREFIX_EQUATION_RE = re.compile(r"^(\s*[^：:\n]{0,20}[：:]\s*)([^，,。；;\n]+=[^，,。；;\n]+)(.*)$")
INLINE_EQUATION_SEGMENT_RE = re.compile(
    r"((?:\([A-Za-z\u0370-\u03FF]{2,8}\)[A-Za-z0-9]{1,6}|[A-Za-z][A-Za-z0-9]{0,12})\s*=\s*[^，,。；;\n]+)"
)
GREEK_CHAR_RE = re.compile(r"[\u0370-\u03FF]")
SHORT_INDEX_VAR_RE = re.compile(r"\b[A-Za-z]{1,2}[ijklmn]{2,6}\b")
INTERACTION_TOKEN_RE = re.compile(r"\([A-Za-z\u0370-\u03FF]{2,8}\)[A-Za-z0-9]{1,6}")
MATH_OPERATOR_RE = re.compile(r"[=+\-*/^−×÷]")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LONG_EN_WORD_RE = re.compile(r"[A-Za-z]{4,}")
COMBINING_BAR_RE = re.compile(r"([A-Za-z\u0370-\u03FF])\u0304")
COMBINING_HAT_RE = re.compile(r"([A-Za-z\u0370-\u03FF])\u0302")
PRECOMPOSED_HAT_MAP = {
    "ŷ": r"\hat{y}",
    "Ŷ": r"\hat{Y}",
}
PRECOMPOSED_BAR_MAP = {
    "ȳ": r"\bar{y}",
    "Ȳ": r"\bar{Y}",
}


def _sanitize_formula(expr: str) -> str:
    out = expr.strip().replace("−", "-").replace("×", r"\times ").replace("÷", r"\div ")
    for src, target in PRECOMPOSED_HAT_MAP.items():
        out = out.replace(src, target)
    for src, target in PRECOMPOSED_BAR_MAP.items():
        out = out.replace(src, target)
    out = COMBINING_BAR_RE.sub(lambda m: rf"\bar{{{m.group(1)}}}", out)
    out = COMBINING_HAT_RE.sub(lambda m: rf"\hat{{{m.group(1)}}}", out)
    return out


def _looks_formula_like(expr: str) -> bool:
    candidate = _sanitize_formula(expr)
    if not candidate or "`" in candidate or "http://" in candidate or "https://" in candidate:
        return False

    cjk_count = len(CJK_RE.findall(candidate))
    if cjk_count > 2:
        return False

    has_operator = bool(MATH_OPERATOR_RE.search(candidate))
    has_equals = "=" in candidate
    has_greek = bool(GREEK_CHAR_RE.search(candidate))
    has_subscript = bool(INLINE_SUBSCRIPT_TOKEN_RE.search(f" {candidate} "))
    has_interaction = bool(INTERACTION_TOKEN_RE.search(candidate))
    has_short_index_var = bool(SHORT_INDEX_VAR_RE.search(candidate))
    alpha_count = sum(1 for ch in candidate if ch.isalpha())
    long_en_word_count = len(LONG_EN_WORD_RE.findall(candidate))
    has_latex_token = any(token in candidate for token in (r"\frac", r"\sum", r"\prod", r"\int", r"\sqrt"))

    if has_interaction:
        return True
    if has_short_index_var and len(candidate) <= 24:
        return True
    if has_latex_token:
        return True
    if has_subscript or has_greek:
        return True
    if has_equals:
        # Prevent prose such as "One-at-a-time designs ..." from being forced into math.
        if long_en_word_count >= 4 and not (has_subscript or has_greek or has_interaction):
            return False
        return True
    if has_operator and (has_greek or has_subscript):
        return True
    return False


def normalize_math_markdown(raw: str) -> str:
    source = str(raw or "").replace("\r\n", "\n")
    lines = source.split("\n")
    out: list[str] = []
    in_code_fence = False

    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_code_fence = not in_code_fence
            out.append(line)
            continue

        if in_code_fence or not trimmed:
            out.append(line)
            continue

        if "$" in line or "\\(" in line or "\\[" in line:
            out.append(line)
            continue

        prefixed = PREFIX_EQUATION_RE.match(line)
        if prefixed:
            prefix = prefixed.group(1) or ""
            equation = _sanitize_formula(prefixed.group(2) or "")
            suffix = prefixed.group(3) or ""
            if _looks_formula_like(equation):
                out.append(f"{prefix}${equation}${suffix}")
                continue

        labeled = FORMULA_LABEL_RE.match(line)
        if labeled:
            prefix = labeled.group(1) or ""
            rhs = _sanitize_formula((labeled.group(2) or "").strip())
            if "=" in rhs and "$" not in rhs and _looks_formula_like(rhs):
                out.append(f"{prefix}${rhs}$")
                continue

        bullet_match = re.match(r"^(\s*[-*]\s+)(.+)$", line)
        if bullet_match:
            prefix = bullet_match.group(1) or ""
            body = (bullet_match.group(2) or "").strip()
            suffix_match = FORMULA_WITH_SUFFIX_RE.match(body)
            if suffix_match:
                formula = _sanitize_formula(suffix_match.group("formula") or "")
                suffix = (suffix_match.group("suffix") or "").strip()
                if _looks_formula_like(formula):
                    out.append(f"{prefix}${formula}$ {suffix}")
                    continue

            if PURE_EQUATION_RE.match(body) and "`" not in body and "=" in body and _looks_formula_like(body):
                out.append(f"{prefix}${_sanitize_formula(body)}$")
                continue
            if _looks_formula_like(body):
                out.append(f"{prefix}${_sanitize_formula(body)}$")
                continue

        suffix_match = FORMULA_WITH_SUFFIX_RE.match(trimmed)
        if suffix_match:
            formula = _sanitize_formula(suffix_match.group("formula") or "")
            suffix = (suffix_match.group("suffix") or "").strip()
            if _looks_formula_like(formula):
                leading = line[: len(line) - len(line.lstrip())]
                out.append(f"{leading}${formula}$ {suffix}")
                continue

        if PURE_EQUATION_RE.match(trimmed) and "`" not in trimmed and "=" in trimmed and _looks_formula_like(trimmed):
            leading = line[: len(line) - len(line.lstrip())]
            out.append(f"{leading}${_sanitize_formula(trimmed)}$")
            continue

        def _inline_replace(match: re.Match[str]) -> str:
            segment = _sanitize_formula(match.group(1) or "")
            if _looks_formula_like(segment):
                return f"${segment}$"
            return match.group(1) or ""

        replaced_inline_eq = INLINE_EQUATION_SEGMENT_RE.sub(_inline_replace, line)
        if replaced_inline_eq != line:
            out.append(replaced_inline_eq)
            continue

        replaced = INLINE_SUBSCRIPT_TOKEN_RE.sub(lambda m: f"{m.group(1)}${_sanitize_formula(m.group(2) or '')}$", line)
        out.append(replaced)

    return "\n".join(out)
