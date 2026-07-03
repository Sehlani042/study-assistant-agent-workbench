import { useMemo, useState } from "react";

import { MarkdownMath } from "@/components/MarkdownMath";
import type {
  LearningPreferences,
  PromptConfig,
  PromptOverridePayload,
} from "@/lib/types";

type PreviewPayload = {
  explanation_preview: any;
  translation_preview: string;
  quality_preview: Record<string, unknown>;
  model_meta: Record<string, unknown>;
} | null;

const DEFAULT_PAGE_TEXT = `Why Use Factorial Designs?

- Alternative: "One-at-a-time" designs, where we vary the levels of a single factor while holding others constant.
- Advantage 1: Estimating Interaction.
- One-at-a-time experiments cannot estimate interaction.`;

export function ExplanationLabPanel({
  canManagePrompts,
  learningPreferences,
  taskPromptProfile,
  taskPrompt,
  effectiveRunPrompt,
  onPreview,
}: {
  canManagePrompts: boolean;
  learningPreferences: LearningPreferences;
  taskPromptProfile: "default" | "personal";
  taskPrompt: string;
  effectiveRunPrompt: PromptConfig;
  onPreview: (input: {
    pageText: string;
    formulas: { latex: string }[];
    learningProfile: LearningPreferences;
    promptProfile: "default" | "personal";
    taskPrompt: string;
    promptOverrides: PromptOverridePayload;
  }) => Promise<PreviewPayload>;
}) {
  const [pageText, setPageText] = useState(DEFAULT_PAGE_TEXT);
  const [formulaText, setFormulaText] = useState("y = \\mu + \\alpha_i + \\beta_j + (\\alpha\\beta)_{ij} + \\varepsilon_{ij}");
  const [agentCOverride, setAgentCOverride] = useState("");
  const [chatOverride, setChatOverride] = useState("");
  const [preview, setPreview] = useState<PreviewPayload>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resolvedModel = preview ? String(preview.model_meta?.resolved_model ?? "") : "";
  const displayLabel = preview ? String(preview.model_meta?.display_label ?? "") : "";

  const effectiveOverrides = useMemo<PromptOverridePayload>(() => {
    const out: PromptOverridePayload = {};
    if (agentCOverride.trim()) out.agent_c_instruction = agentCOverride.trim();
    if (chatOverride.trim()) out.chat_instruction = chatOverride.trim();
    return out;
  }, [agentCOverride, chatOverride]);

  if (!canManagePrompts) {
    return (
      <div className="page-body">
        <div className="card" style={{ padding: 28, color: "var(--text-muted)" }}>
          当前账号没有 Explanation Lab 权限。
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="page-header">
        <h2>Explanation Lab</h2>
      </div>

      <div className="page-body" style={{ display: "grid", gridTemplateColumns: "minmax(320px, 420px) 1fr", gap: 16 }}>
        <div className="card" style={{ padding: 16 }}>
          <div className="section-subtitle" style={{ marginTop: 0 }}>单页原文</div>
          <textarea value={pageText} onChange={(e) => setPageText(e.target.value)} style={{ minHeight: 180 }} />

          <div className="section-subtitle">公式片段（可选）</div>
          <textarea value={formulaText} onChange={(e) => setFormulaText(e.target.value)} style={{ minHeight: 72 }} />

          <div className="section-subtitle">学习参数快照</div>
          <div className="meta" style={{ marginBottom: 10 }}>
            {learningPreferences.learner_level} · {learningPreferences.learning_goal} · {learningPreferences.depth_mode} · {learningPreferences.attention_support}
          </div>

          <details>
            <summary>微调 Prompt</summary>
            <div className="section-subtitle">Agent C 覆盖</div>
            <textarea
              value={agentCOverride}
              onChange={(e) => setAgentCOverride(e.target.value)}
              placeholder={effectiveRunPrompt.agent_c_instruction || "可选"}
              style={{ minHeight: 110 }}
            />
            <div className="section-subtitle">Chat 覆盖</div>
            <textarea
              value={chatOverride}
              onChange={(e) => setChatOverride(e.target.value)}
              placeholder={effectiveRunPrompt.chat_instruction || "可选"}
              style={{ minHeight: 96 }}
            />
          </details>

          <div style={{ marginTop: 14, display: "flex", gap: 10 }}>
            <button
              className="primary"
              disabled={isRunning || !pageText.trim()}
              onClick={async () => {
                try {
                  setIsRunning(true);
                  setError(null);
                  const next = await onPreview({
                    pageText,
                    formulas: formulaText.trim() ? [{ latex: formulaText.trim() }] : [],
                    learningProfile: learningPreferences,
                    promptProfile: taskPromptProfile,
                    taskPrompt,
                    promptOverrides: effectiveOverrides,
                  });
                  setPreview(next);
                } catch (err) {
                  setError(err instanceof Error ? err.message : "预览失败");
                } finally {
                  setIsRunning(false);
                }
              }}
            >
              {isRunning ? "运行中…" : "生成预览"}
            </button>
          </div>
          {error && <div className="error-banner" style={{ marginTop: 12 }}>⚠️ {error}</div>}
        </div>

        <div style={{ display: "grid", gap: 16 }}>
          <div className="card" style={{ padding: 16 }}>
            <div className="section-subtitle" style={{ marginTop: 0 }}>模型元信息</div>
            <div className="meta">
              {displayLabel || "尚未运行"}
            </div>
            {Boolean(resolvedModel) && (
              <div className="meta">resolved: <code>{resolvedModel}</code></div>
            )}
          </div>

          <div className="card" style={{ padding: 16 }}>
            <div className="section-subtitle" style={{ marginTop: 0 }}>讲解预览</div>
            {preview ? (
              <MarkdownMath content={String(preview.explanation_preview?.overview || "")} />
            ) : (
              <div className="meta">运行后显示</div>
            )}
          </div>

          <div className="card" style={{ padding: 16 }}>
            <div className="section-subtitle" style={{ marginTop: 0 }}>直译预览</div>
            {preview ? <MarkdownMath content={preview.translation_preview} /> : <div className="meta">运行后显示</div>}
          </div>

          <div className="card" style={{ padding: 16 }}>
            <div className="section-subtitle" style={{ marginTop: 0 }}>质量预览</div>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
              {preview ? JSON.stringify(preview.quality_preview, null, 2) : "运行后显示"}
            </pre>
          </div>
        </div>
      </div>
    </>
  );
}
