"use client";

import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

type MarkdownMathProps = {
  content: string;
  className?: string;
  softBreaks?: boolean;
};

const FORMULA_LABEL_RE =
  /^(\s*(?:[-*]\s*)?(?:模型公式|公式|方程|equation|model(?:\s+equation)?)\s*[：:]\s*)(.+)$/i;
const PURE_EQUATION_RE =
  /^([A-Za-z\u0370-\u03FF][A-Za-z0-9_\u0370-\u03FF()\[\]{}|.,\s+\-*/^\\=<>−×÷]{1,220})$/;
const INLINE_SUBSCRIPT_TOKEN_RE =
  /(^|[\s(（:：,，])([A-Za-z\u0370-\u03FF]+_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)?)(?=([\s)）:：,，。；;]|$))/g;
const FORMULA_WITH_SUFFIX_RE =
  /^([A-Za-z0-9_\u0370-\u03FF()\[\]{}|.,\s+\-*/^\\=<>−×÷•·]{2,220}?)\s*((?:（[^）]{1,40}）|\([^()]*[\u4e00-\u9fff][^()]*\)))$/;
const PREFIX_EQUATION_RE = /^(\s*[^：:\n]{0,20}[：:]\s*)([^，,。；;\n]+=[^，,。；;\n]+)(.*)$/;
const INLINE_EQUATION_SEGMENT_RE =
  /((?:\([A-Za-z\u0370-\u03FF]{2,8}\)[A-Za-z0-9]{1,6}|[A-Za-z][A-Za-z0-9]{0,12})\s*=\s*[^，,。；;\n]+)/g;
const GREEK_CHAR_RE = /[\u0370-\u03FF]/;
const SHORT_INDEX_VAR_RE = /\b[A-Za-z]{1,2}[ijklmn]{2,6}\b/;
const INTERACTION_TOKEN_RE = /\([A-Za-z\u0370-\u03FF]{2,8}\)[A-Za-z0-9]{1,6}/;
const MATH_OPERATOR_RE = /[=+\-*/^−×÷]/;
const INLINE_SUBSCRIPT_PLAIN_RE =
  /(^|[\s(（:：,，])[A-Za-z\u0370-\u03FF]+_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)?(?=([\s)）:：,，。；;]|$))/;
const LONG_EN_WORD_RE = /[A-Za-z]{4,}/g;
const COMBINING_BAR_RE = /([A-Za-z\u0370-\u03FF])\u0304/g;
const COMBINING_HAT_RE = /([A-Za-z\u0370-\u03FF])\u0302/g;

function sanitizeFormula(expr: string): string {
  let out = String(expr ?? "")
    .trim()
    .replaceAll("−", "-")
    .replaceAll("×", "\\times ")
    .replaceAll("÷", "\\div ");
  out = out
    .replaceAll("ŷ", "\\hat{y}")
    .replaceAll("Ŷ", "\\hat{Y}")
    .replaceAll("ȳ", "\\bar{y}")
    .replaceAll("Ȳ", "\\bar{Y}")
    .replace(COMBINING_BAR_RE, (_m, letter: string) => `\\bar{${letter}}`)
    .replace(COMBINING_HAT_RE, (_m, letter: string) => `\\hat{${letter}}`);
  return out;
}

function looksFormulaLike(expr: string): boolean {
  const candidate = sanitizeFormula(expr);
  if (!candidate || candidate.includes("`") || candidate.includes("http://") || candidate.includes("https://")) {
    return false;
  }
  const cjkCount = (candidate.match(/[\u4e00-\u9fff]/g) || []).length;
  if (cjkCount > 2) return false;

  const hasOperator = MATH_OPERATOR_RE.test(candidate);
  const hasEquals = candidate.includes("=");
  const hasGreek = GREEK_CHAR_RE.test(candidate);
  const hasSubscript = INLINE_SUBSCRIPT_PLAIN_RE.test(` ${candidate} `);
  const hasInteraction = INTERACTION_TOKEN_RE.test(candidate);
  const hasShortIndexVar = SHORT_INDEX_VAR_RE.test(candidate);
  const alphaCount = Array.from(candidate).filter((ch) => /[A-Za-z]/.test(ch)).length;
  const longEnWordCount = (candidate.match(LONG_EN_WORD_RE) || []).length;
  const hasLatexToken = ["\\frac", "\\sum", "\\prod", "\\int", "\\sqrt"].some((token) =>
    candidate.includes(token),
  );

  if (hasInteraction) return true;
  if (hasShortIndexVar && candidate.length <= 24) return true;
  if (hasLatexToken) return true;
  if (hasSubscript || hasGreek) return true;
  if (hasEquals) {
    if (longEnWordCount >= 4 && !(hasSubscript || hasGreek || hasInteraction)) return false;
    return true;
  }
  if (hasOperator && (hasGreek || hasSubscript || alphaCount >= 2)) return true;
  return false;
}

function normalizeMathMarkdown(raw: string): string {
  const source = String(raw ?? "").replace(/\r\n/g, "\n");
  const lines = source.split("\n");
  let inCodeFence = false;

  const out = lines.map((line) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      inCodeFence = !inCodeFence;
      return line;
    }
    if (inCodeFence || !trimmed) {
      return line;
    }
    if (line.includes("$") || line.includes("\\(") || line.includes("\\[")) {
      return line;
    }

    const prefixed = line.match(PREFIX_EQUATION_RE);
    if (prefixed) {
      const prefix = prefixed[1] ?? "";
      const equation = sanitizeFormula(prefixed[2] ?? "");
      const suffix = prefixed[3] ?? "";
      if (looksFormulaLike(equation)) {
        return `${prefix}$${equation}$${suffix}`;
      }
    }

    const labeled = line.match(FORMULA_LABEL_RE);
    if (labeled) {
      const prefix = labeled[1] ?? "";
      const rhs = sanitizeFormula((labeled[2] ?? "").trim());
      if (rhs.includes("=") && !rhs.includes("$") && looksFormulaLike(rhs)) {
        return `${prefix}$${rhs}$`;
      }
    }

    const bulletMatch = line.match(/^(\s*[-*]\s+)(.+)$/);
    if (bulletMatch) {
      const prefix = bulletMatch[1] ?? "";
      const body = (bulletMatch[2] ?? "").trim();
      const withSuffix = body.match(FORMULA_WITH_SUFFIX_RE);
      if (withSuffix) {
        const formula = sanitizeFormula(withSuffix[1] ?? "");
        const suffix = (withSuffix[2] ?? "").trim();
        if (looksFormulaLike(formula)) {
          return `${prefix}$${formula}$ ${suffix}`;
        }
      }
      if (PURE_EQUATION_RE.test(body) && !body.includes("`") && body.includes("=") && looksFormulaLike(body)) {
        return `${prefix}$${sanitizeFormula(body)}$`;
      }
      if (looksFormulaLike(body)) {
        return `${prefix}$${sanitizeFormula(body)}$`;
      }
    } else if (PURE_EQUATION_RE.test(trimmed) && !trimmed.includes("`") && trimmed.includes("=") && looksFormulaLike(trimmed)) {
      const leading = line.slice(0, line.indexOf(trimmed));
      return `${leading}$${sanitizeFormula(trimmed)}$`;
    }

    const withSuffix = trimmed.match(FORMULA_WITH_SUFFIX_RE);
    if (withSuffix) {
      const formula = sanitizeFormula(withSuffix[1] ?? "");
      const suffix = (withSuffix[2] ?? "").trim();
      if (looksFormulaLike(formula)) {
        const leading = line.slice(0, line.indexOf(trimmed));
        return `${leading}$${formula}$ ${suffix}`;
      }
    }

    const replacedInlineEquation = line.replace(INLINE_EQUATION_SEGMENT_RE, (segment) => {
      const formula = sanitizeFormula(segment);
      return looksFormulaLike(formula) ? `$${formula}$` : segment;
    });
    if (replacedInlineEquation !== line) {
      return replacedInlineEquation;
    }

    return line.replace(INLINE_SUBSCRIPT_TOKEN_RE, (_, p1: string, p2: string) => `${p1}$${sanitizeFormula(p2)}$`);
  });

  return out.join("\n");
}

export function MarkdownMath({ content, className, softBreaks = false }: MarkdownMathProps) {
  const text = normalizeMathMarkdown(content);
  const remarkPlugins = softBreaks ? [remarkGfm, remarkMath, remarkBreaks] : [remarkGfm, remarkMath];
  return (
    <div className={["markdown-body", className].filter(Boolean).join(" ")}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore" }]]}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
