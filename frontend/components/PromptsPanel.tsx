import type { useSettingsAdmin } from "@/lib/hooks/useSettingsAdmin";
import { PROMPT_FIELD_META } from "@/lib/utils";

// User-friendly labels for each Agent prompt field
const FRIENDLY_LABELS: Record<string, { name: string; desc: string }> = {
    agent_a_instruction: { name: "文档综述指令", desc: "控制 AI 如何理解整篇文档结构与分章方式" },
    agent_b_instruction: { name: "章节总结指令", desc: "控制 AI 如何对每个章节进行概要提炼" },
    agent_c_instruction: { name: "逐页解释指令", desc: "控制 AI 如何逐页生成核心解释（影响最大）" },
    chat_instruction: { name: "问答回复指令", desc: "控制页内问答的回复风格与详细程度" },
    formula_instruction: { name: "公式识别指令", desc: "控制 AI 如何提取和解释数学公式" },
};

export function PromptsPanel({
    settings,
    canManagePrompts,
}: {
    settings: ReturnType<typeof useSettingsAdmin>;
    canManagePrompts: boolean;
}) {
    const {
        promptSource,
        hasCustomPrompt,
        promptDraft,
        setPromptDraft,
        isSavingPrompt,
        onUseSystemDefaultForMyPrompt,
        onSavePrompt,
        onResetPrompt,
        defaultPromptDraft,
        setDefaultPromptDraft,
        isSavingDefaultPrompt,
        onSaveDefaultPrompt,
        taskPromptProfile,
        setTaskPromptProfile,
        taskPrompt,
        setTaskPrompt,
        confirmPromptBeforeRun,
        setConfirmPromptBeforeRun,
    } = settings;

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 780 }}>

            {/* ── 本次上传的附加说明（最常用） ─────────────────────────── */}
            <div className="card">
                <div className="card-header">
                    <h3>上传/重生成时的额外要求</h3>
                    <span className="badge badge-blue">常用</span>
                </div>
                <p style={{ margin: "0 0 12px", color: "var(--text-muted)", fontSize: "0.9rem" }}>
                    在此填写对 AI 的特别要求，只影响下次上传或重生成，不会修改你的默认设置。
                    <br />例如：<em>「请重点解释文中的数学推导步骤」「中英文术语对照」</em>
                </p>
                <textarea
                    value={taskPrompt}
                    onChange={(e) => setTaskPrompt(e.target.value)}
                    placeholder="（可留空）输入对 AI 的额外要求…"
                    style={{ minHeight: 88 }}
                />
                <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ fontSize: "0.84rem", color: "var(--text-muted)" }}>AI 风格基础：</span>
                    <button
                        onClick={() => setTaskPromptProfile("personal")}
                        className={taskPromptProfile === "personal" ? "primary" : ""}
                        style={{ fontSize: "0.84rem" }}
                    >
                        我的自定义风格
                    </button>
                    <button
                        onClick={() => setTaskPromptProfile("default")}
                        className={taskPromptProfile === "default" ? "primary" : ""}
                        style={{ fontSize: "0.84rem" }}
                    >
                        平台默认风格
                    </button>
                </div>
                <label style={{ display: "inline-flex", alignItems: "center", gap: 8, marginTop: 10, fontSize: "0.84rem", color: "var(--text-muted)", cursor: "pointer" }}>
                    <input
                        type="checkbox"
                        checked={confirmPromptBeforeRun}
                        onChange={(e) => setConfirmPromptBeforeRun(e.target.checked)}
                    />
                    每次上传/重生成前，先弹窗让我确认 AI 指令
                </label>
            </div>

            {/* ── 我的 AI 风格（个人自定义） ────────────────────────────── */}
            <div className="card">
                <div className="card-header">
                    <h3>我的 AI 风格（个人自定义）</h3>
                    <span className={`badge ${hasCustomPrompt && promptSource === "personal" ? "badge-green" : "badge-gray"}`}>
                        {hasCustomPrompt ? "已自定义" : "使用平台默认"}
                    </span>
                </div>
                <p style={{ margin: "0 0 14px", color: "var(--text-muted)", fontSize: "0.9rem" }}>
                    调整 AI 的解释风格，这些设置会作为你账号的默认偏好长期保存。
                    不确定时直接点「恢复平台默认」，不会影响已处理的文档。
                </p>
                {PROMPT_FIELD_META.map((field) => {
                    const friendly = FRIENDLY_LABELS[field.key];
                    return (
                        <div key={`my-${field.key}`} style={{ marginBottom: 14 }}>
                            <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
                                <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>{friendly?.name ?? field.label}</span>
                                {friendly?.desc && (
                                    <span style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>{friendly.desc}</span>
                                )}
                            </div>
                            <textarea
                                value={String(promptDraft[field.key] ?? "")}
                                onChange={(e) => setPromptDraft((prev) => ({ ...prev, [field.key]: e.target.value }))}
                                style={{ minHeight: field.key === "agent_c_instruction" ? 90 : 66 }}
                            />
                        </div>
                    );
                })}
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
                    <button onClick={onUseSystemDefaultForMyPrompt} disabled={isSavingPrompt}>
                        从平台默认填充
                    </button>
                    <button onClick={onResetPrompt} disabled={isSavingPrompt}>
                        恢复平台默认
                    </button>
                    <button className="primary" onClick={onSavePrompt} disabled={isSavingPrompt}>
                        {isSavingPrompt ? "保存中…" : "保存我的风格"}
                    </button>
                </div>
            </div>

            {/* ── 系统默认（仅管理员可编辑） ───────────────────────────── */}
            {canManagePrompts && (
                <details className="card" style={{ padding: "14px 18px" }}>
                    <summary style={{ fontWeight: 700, cursor: "pointer", userSelect: "none" }}>
                        ⚙️ 系统默认 AI 风格（管理员编辑）
                    </summary>
                    <p style={{ margin: "10px 0 14px", color: "var(--text-muted)", fontSize: "0.88rem" }}>
                        修改后所有未自定义的用户都会使用这个风格，请谨慎修改。
                    </p>
                    {PROMPT_FIELD_META.map((field) => {
                        const friendly = FRIENDLY_LABELS[field.key];
                        return (
                            <div key={`default-admin-${field.key}`} style={{ marginBottom: 12 }}>
                                <div style={{ fontWeight: 600, fontSize: "0.88rem", marginBottom: 4 }}>
                                    {friendly?.name ?? field.label}
                                </div>
                                <textarea
                                    value={String(defaultPromptDraft[field.key] ?? "")}
                                    onChange={(e) => setDefaultPromptDraft((prev) => ({ ...prev, [field.key]: e.target.value }))}
                                    style={{ minHeight: field.key === "agent_c_instruction" ? 84 : 60 }}
                                />
                            </div>
                        );
                    })}
                    <button className="primary" onClick={onSaveDefaultPrompt} disabled={isSavingDefaultPrompt}>
                        {isSavingDefaultPrompt ? "保存中…" : "保存系统默认"}
                    </button>
                </details>
            )}

            {/* 普通用户只读参考 */}
            {!canManagePrompts && (
                <details className="card" style={{ padding: "14px 18px" }}>
                    <summary style={{ fontWeight: 600, color: "var(--text-muted)", cursor: "pointer", userSelect: "none", fontSize: "0.9rem" }}>
                        查看平台默认 AI 指令（只读）
                    </summary>
                    <div style={{ marginTop: 12 }}>
                        {PROMPT_FIELD_META.map((field) => {
                            const friendly = FRIENDLY_LABELS[field.key];
                            return (
                                <div key={`default-readonly-${field.key}`} style={{ marginBottom: 12 }}>
                                    <div style={{ fontWeight: 600, fontSize: "0.88rem", marginBottom: 4 }}>
                                        {friendly?.name ?? field.label}
                                    </div>
                                    <textarea
                                        readOnly
                                        value={String(defaultPromptDraft[field.key] ?? "")}
                                        style={{ minHeight: 56, background: "var(--surface-alt)", color: "var(--text-muted)" }}
                                    />
                                </div>
                            );
                        })}
                    </div>
                </details>
            )}
        </div>
    );
}
