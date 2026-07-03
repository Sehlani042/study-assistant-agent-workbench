"use client";

import { useMemo, useState, useRef, useEffect } from "react";
import { previewExplanation } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { useDocumentStudy } from "@/lib/hooks/useDocumentStudy";
import { useChat } from "@/lib/hooks/useChat";
import { useSettingsAdmin } from "@/lib/hooks/useSettingsAdmin";
import type { DashboardPage } from "@/lib/types";
import { normalizePermissions, PROMPT_FIELD_META } from "@/lib/utils";

import { AuthPanel } from "@/components/AuthPanel";
import { Sidebar } from "@/components/Sidebar";
import { StudyPanel } from "@/components/StudyPanel";
import { HistoryPanel } from "@/components/HistoryPanel";
import { SettingsPanel } from "@/components/SettingsPanel";
import { PromptsPanel } from "@/components/PromptsPanel";
import { AccountsPanel } from "@/components/AccountsPanel";
import { TaskQueuePanel } from "@/components/TaskQueuePanel";
import { ExplanationLabPanel } from "@/components/ExplanationLabPanel";

export default function HomePage() {
  const [activePage, setActivePage] = useState<DashboardPage>("study");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  // ─── Auth ──────────────────────────────────────────────────────────────────
  const auth = useAuth();
  const { authReady, currentUser, authPolicy, setCurrentUser } = auth;

  const currentPermissions = useMemo(
    () => normalizePermissions(currentUser),
    [currentUser],
  );
  const canManageAccounts = currentPermissions.can_manage_accounts;
  const canManagePrompts = currentPermissions.can_manage_prompts;
  const canManageSharedKeys = currentPermissions.can_manage_shared_keys;

  // ─── Settings / Admin ──────────────────────────────────────────────────────
  const settings = useSettingsAdmin({
    currentUser,
    setCurrentUser,
    canManageAccounts,
    canManagePrompts,
    canManageSharedKeys,
    authPolicy,
  });

  // ─── Document Study ────────────────────────────────────────────────────────
  const study = useDocumentStudy({
    currentUser,
    language: "zh",
    setLanguage: () => { },
    loadDocumentPromptSnapshot: settings.loadDocumentPromptSnapshot,
    ensureLLMReady: settings.ensureLLMReady,
  });

  // ─── Chat ──────────────────────────────────────────────────────────────────
  const chat = useChat({
    currentUser,
    documentId: study.documentId,
    pageNo: study.pageNo,
    status: study.status,
    language: study.language ?? "zh",
    ensureLLMReady: settings.ensureLLMReady,
  });

  // ─── Global Error (first non-null wins) ───────────────────────────────────
  const error = auth.error || settings.error || study.error || chat.error;

  // ─── Compound actions ─────────────────────────────────────────────────────
  const onLogoutWithReset = async () => {
    await auth.onLogout();
    study.resetStudyState();
    chat.resetChatState();
    setActivePage("study");
  };

  const onConfirmPendingRun = async () => {
    if (!study.pendingRun) return;
    if (!settings.ensureLLMReady()) return;
    const next = study.pendingRun;
    const overrides = settings.collectRunPromptOverrides();
    study.setPendingRun(null);
    settings.setRunPromptOverrides({});
    if (next.kind === "upload") {
      study.setPickedUploadFile(null);
      study.setUploadPickerKey((k) => k + 1);
      await study.performUpload(next.file, settings.taskPromptProfile, settings.taskPrompt, overrides, next.learningProfile);
    } else if (next.kind === "explain") {
      await study.performExplain(next.documentId, next.pageNo, settings.taskPromptProfile, settings.taskPrompt, overrides, next.learningProfile);
    } else {
      await study.performRegenerate(next.documentId, next.pageNo, settings.taskPromptProfile, settings.taskPrompt, overrides, next.learningProfile);
    }
  };

  const closePendingRunModal = () => {
    study.setPendingRun(null);
    settings.setRunPromptOverrides({});
  };

  const onSubmitPickedFile = async () => {
    if (!study.pickedUploadFile) return;
    if (!settings.ensureLLMReady()) return;
    if (settings.confirmPromptBeforeRun) {
      settings.setRunPromptOverrides({});
      study.setPendingRun({ kind: "upload", file: study.pickedUploadFile, learningProfile: settings.learningPreferences });
      return;
    }
    await study.performUpload(study.pickedUploadFile, settings.taskPromptProfile, settings.taskPrompt, undefined, settings.learningPreferences);
  };

  const onRegenerate = async () => {
    if (!study.documentId || !study.page) return;
    if (!settings.ensureLLMReady()) return;
    if (settings.confirmPromptBeforeRun) {
      settings.setRunPromptOverrides({});
      study.setPendingRun({
        kind: "regenerate",
        documentId: study.documentId,
        pageNo: study.page.page_no,
        learningProfile: settings.learningPreferences,
      });
      return;
    }
    await study.performRegenerate(
      study.documentId,
      study.page.page_no,
      settings.taskPromptProfile,
      settings.taskPrompt,
      undefined,
      settings.learningPreferences,
    );
  };

  const onExplainCurrentPage = async () => {
    if (!study.documentId || !study.page) return;
    if (!settings.ensureLLMReady()) return;
    if (settings.confirmPromptBeforeRun) {
      settings.setRunPromptOverrides({});
      study.setPendingRun({
        kind: "explain",
        documentId: study.documentId,
        pageNo: study.page.page_no,
        learningProfile: settings.learningPreferences,
      });
      return;
    }
    await study.performExplain(
      study.documentId,
      study.page.page_no,
      settings.taskPromptProfile,
      settings.taskPrompt,
      undefined,
      settings.learningPreferences,
    );
  };

  const onSelectHistoryDoc = async (docId: string) => {
    await study.onSelectHistoryDocument(docId);
    setActivePage("study");
  };

  const onPreviewExplanation = async ({
    pageText,
    formulas,
    learningProfile,
    promptProfile,
    taskPrompt,
    promptOverrides,
  }: {
    pageText: string;
    formulas: { latex: string }[];
    learningProfile: typeof settings.learningPreferences;
    promptProfile: "default" | "personal";
    taskPrompt: string;
    promptOverrides: Record<string, string>;
  }) =>
    previewExplanation({
      page_text: pageText,
      formulas,
      language: "zh",
      prompt_profile: promptProfile,
      task_prompt: taskPrompt,
      prompt_overrides: promptOverrides,
      learning_profile: learningProfile,
      llm_provider: settings.effectiveProvider,
      llm_model: settings.effectiveModel,
    });

  // ─── LLM summary for sidebar ──────────────────────────────────────────────
  const llmStatusText = settings.llmSummaryText || undefined;
  const recentStudyDocs = study.historyDocuments.slice(0, 4);

  useEffect(() => {
    setMobileSidebarOpen(false);
  }, [activePage]);

  useEffect(() => {
    if (!currentUser || activePage !== "history") return;
    void study.loadDocumentHistory({ silent: true });
  }, [currentUser, activePage, study.loadDocumentHistory]);

  // ─── Loading / Auth gates ─────────────────────────────────────────────────
  if (!authReady) {
    return (
      <div className="auth-shell">
        <div style={{ color: "#9ca3af", fontSize: "0.9rem" }}>加载中…</div>
      </div>
    );
  }

  if (!currentUser) {
    return <AuthPanel auth={auth} />;
  }

  // ─── Toolbar for the study page ───────────────────────────────────────────
  const compactStudyStatus = study.status && study.documentId
    ? `${study.status.status === "completed" ? "已完成" : study.status.stage_label || study.status.status} · ${study.status.progress.processed_pages}/${study.status.progress.total_pages || "?"} 页`
    : "";

  const StudyPageHeader = (
    <div className="page-header core-study-header">
      <div className="core-study-title">
        <h2>学习阅读器</h2>
        {study.currentHistoryItem && (
          <span className="core-document-name" title={study.currentHistoryItem.original_filename}>
            {study.currentHistoryItem.original_filename}
          </span>
        )}
        {compactStudyStatus && <span className="core-status-pill">{compactStudyStatus}</span>}
      </div>
      <div className="page-header-actions">
        <button onClick={() => setActivePage("history")}>历史</button>
        <button onClick={() => setActivePage("queue")}>任务</button>
        {/* Compact upload bar when doc is already loaded */}
        {study.documentId && (
          <div className="upload-bar">
            <label>
              <input
                key={study.uploadPickerKey}
                type="file"
                accept=".pdf,.pptx"
                onChange={study.onPickUploadFile}
                disabled={study.isUploading}
              />
              新文档
            </label>
            {study.pickedUploadFile && (
              <>
                <span className="picked-name">{study.pickedUploadFile.name}</span>
                <button className="primary" onClick={() => void onSubmitPickedFile()} disabled={study.isUploading}>
                  {study.isUploading ? "上传中…" : "提交"}
                </button>
              </>
            )}
          </div>
        )}
        {study.status && !["completed", "failed", "canceled"].includes(study.status.status) && (
          <button className="warn" onClick={() => void study.onCancelTask()} disabled={study.isCanceling}>
            {study.isCanceling ? "取消中…" : "停止任务"}
          </button>
        )}
        {study.documentId && (
          <button className="danger ghost-danger" onClick={() => void study.onClear()}>清除</button>
        )}
      </div>
    </div>
  );

  return (
    <div className="app-shell">
      <Sidebar
        activePage={activePage}
        onNavigate={setActivePage}
        currentUser={currentUser}
        onLogout={() => void onLogoutWithReset()}
        llmStatusText={llmStatusText}
        collapsed={sidebarCollapsed}
        onToggleCollapsed={() => setSidebarCollapsed((prev) => !prev)}
        mobileOpen={mobileSidebarOpen}
        onCloseMobile={() => setMobileSidebarOpen(false)}
      />

      <div className="main-content">
        <div className="mobile-topbar">
          <button
            className="mobile-nav-trigger"
            onClick={() => setMobileSidebarOpen(true)}
            aria-label="打开菜单"
          >
            ☰
          </button>
          <div className="mobile-topbar-title">Study Assistant</div>
        </div>

        {/* ── Study Page ─────────────────────────────────────────────────── */}
        {activePage === "study" && (
          <>
            {StudyPageHeader}
            <div className="page-body" style={{ gap: 10 }}>
              {/* LLM warning */}
                  {settings.llmNeedsPersonalKey && (
                    <div className="llm-warning-panel panel">
                      <div>
                        <strong>模型尚未就绪</strong>
                        <p className="meta" style={{ margin: "2px 0 0" }}>请在「设置」中填写 {settings.effectiveProvider} Key 后才能开始生成</p>
                      </div>
                      <button className="primary" onClick={() => setActivePage("settings")}>去设置</button>
                    </div>
                  )}

              {/* Status bar when processing */}
              {study.statusText && study.documentId && (
                <div className="status-bar core-status-bar">
                  <div className={`status-dot ${study.status?.status === "processing" ? "running" : study.status?.status === "completed" ? "done" : study.status?.status === "failed" ? "error" : ""}`} />
                  <span>{compactStudyStatus || study.statusText}</span>
                  {study.pipelineDetailText && (
                    <details className="core-runtime-detail">
                      <summary>运行详情</summary>
                      <span>{study.pipelineDetailText}</span>
                    </details>
                  )}
                </div>
              )}

              {/* Empty state — no document yet */}
              {!study.documentId && !study.isUploading && (
                <div className="study-empty-shell">
                  <div className="upload-cta">
                    <div className="upload-cta-icon">📄</div>
                    <h3>开始一轮新的学习</h3>
                    <p>上传一份 PDF 或 PPTX。系统会先生成页内覆盖翻译，让你先读懂原页；讲解改为按需触发。</p>
                    <label className="upload-cta-btn">
                      <input
                        key={study.uploadPickerKey}
                        type="file"
                        accept=".pdf,.pptx"
                        onChange={study.onPickUploadFile}
                      />
                      选择文件并开始
                    </label>
                    {study.pickedUploadFile && (
                      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                        <span className="meta">{study.pickedUploadFile.name}</span>
                        <button className="primary" onClick={() => void onSubmitPickedFile()} disabled={study.isUploading}>
                          {study.isUploading ? "上传中…" : "开始处理"}
                        </button>
                      </div>
                    )}
                  </div>

                  {recentStudyDocs.length > 0 && (
                    <div className="study-recent-strip card">
                      <div className="card-header">
                        <div>
                          <h3>最近文档</h3>
                          <p className="card-subtitle">不离开学习页，直接回到最近的处理结果。</p>
                        </div>
                        <button onClick={() => setActivePage("history")}>查看全部历史</button>
                      </div>
                      <div className="study-recent-list">
                        {recentStudyDocs.map((doc) => (
                          <button
                            key={doc.document_id}
                            className="study-recent-item"
                            onClick={() => void onSelectHistoryDoc(doc.document_id)}
                          >
                            <strong>{doc.original_filename}</strong>
                            <span className="meta">
                              {doc.progress.processed_pages}/{doc.progress.total_pages} 页 · {doc.stage_label || doc.status}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {study.isUploading && (
                <div className="upload-cta">
                  <div className="upload-cta-icon" style={{ fontSize: "2rem", opacity: 0.6 }}>⏳</div>
                  <p className="meta">正在上传并启动处理流水线…</p>
                </div>
              )}

              {/* Workspace: doc viewer + explanation */}
              {study.documentId && (
                <StudyPanel
                  study={study}
                  chat={chat}
                  documentPromptSnapshot={settings.documentPromptSnapshot}
                  onExplain={onExplainCurrentPage}
                  onRegenerate={onRegenerate}
                />
              )}

              {error && <div className="error-banner">⚠️ {error}</div>}
            </div>
          </>
        )}

        {/* ── History Page ───────────────────────────────────────────────── */}
        {activePage === "queue" && (
          <>
            <TaskQueuePanel
              items={study.queueDocuments}
              isLoading={study.isLoadingQueue}
              onRefresh={() => void study.loadQueueDocuments()}
              onOpenDocument={(docId) => void onSelectHistoryDoc(docId)}
              onCancelTask={(docId) => void study.cancelSpecificTask(docId)}
            />
            {error && <div className="error-banner">⚠️ {error}</div>}
          </>
        )}

        {/* ── History Page ───────────────────────────────────────────────── */}
        {activePage === "history" && (
          <>
            <HistoryPanel
              documents={study.historyDocuments}
              currentDocumentId={study.documentId}
              isLoading={study.isLoadingHistory}
              deletingDocumentId={study.deletingDocumentId}
              onSelect={onSelectHistoryDoc}
              onDelete={(docId) => void study.onDeleteHistoryDocument(docId)}
              onRefresh={() => void study.loadDocumentHistory()}
              currentUser={currentUser}
            />
            {error && <div className="error-banner">⚠️ {error}</div>}
          </>
        )}

        {activePage === "lab" && (
          <>
            <ExplanationLabPanel
              canManagePrompts={canManagePrompts}
              learningPreferences={settings.learningPreferences}
              taskPromptProfile={settings.taskPromptProfile}
              taskPrompt={settings.taskPrompt}
              effectiveRunPrompt={settings.effectiveRunPrompt}
              onPreview={onPreviewExplanation}
            />
            {error && <div className="error-banner">⚠️ {error}</div>}
          </>
        )}

        {/* ── Settings Page ──────────────────────────────────────────────── */}
        {activePage === "settings" && (
          <>
            <div className="page-header"><h2>模型设置</h2></div>
            <div className="page-body">
              <SettingsPanel settings={settings} canManageSharedKeys={canManageSharedKeys} canManageAccounts={canManageAccounts} />
              {error && <div className="error-banner">⚠️ {error}</div>}
            </div>
          </>
        )}

        {/* ── Prompts Page ───────────────────────────────────────────────── */}
        {activePage === "prompts" && (
          <>
            <div className="page-header"><h2>Prompt 配置</h2></div>
            <div className="page-body">
              <PromptsPanel settings={settings} canManagePrompts={canManagePrompts} />
              {error && <div className="error-banner">⚠️ {error}</div>}
            </div>
          </>
        )}

        {/* ── Accounts Page (admin only) ─────────────────────────────────── */}
        {activePage === "accounts" && canManageAccounts && (
          <>
            <div className="page-header"><h2>账户管理</h2></div>
            <div className="page-body">
              <AccountsPanel settings={settings} canManageSharedKeys={canManageSharedKeys} authPolicy={authPolicy} />
              {error && <div className="error-banner">⚠️ {error}</div>}
            </div>
          </>
        )}

      </div>

      {/* ── Pending Run Confirm Modal ──────────────────────────────────────── */}
      {study.pendingRun && (
        <div className="modal-backdrop" onClick={closePendingRunModal}>
          <div className="modal-panel run-confirm-panel" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}>确认生成方案</h3>
              <button onClick={closePendingRunModal}>✕ 关闭</button>
            </div>
              <p className="meta">
              {study.pendingRun.kind === "upload"
                ? `即将处理文件：${study.pendingRun.file.name}`
                : study.pendingRun.kind === "explain"
                  ? `即将解释：第 ${study.pendingRun.pageNo} 页（仅本页）`
                  : `即将重生成：第 ${study.pendingRun.pageNo} 页（仅本页）`}
            </p>

            <div className="section" style={{ marginTop: 12 }}>
              <p className="meta">
                学习参数：{study.pendingRun.learningProfile.learner_level} · {study.pendingRun.learningProfile.learning_goal} · {study.pendingRun.learningProfile.depth_mode} · {study.pendingRun.learningProfile.attention_support}
              </p>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <span className="meta">基线 Prompt：</span>
                <select
                  value={settings.taskPromptProfile}
                  onChange={(e) => settings.setTaskPromptProfile(e.target.value as "default" | "personal")}
                >
                  <option value="personal">我的 Prompt</option>
                  <option value="default">系统默认</option>
                </select>
              </div>
              <textarea
                value={settings.taskPrompt}
                onChange={(e) => settings.setTaskPrompt(e.target.value)}
                placeholder="任务附加 Prompt（可选）"
                style={{ minHeight: 72, marginTop: 10 }}
              />

              <details style={{ marginTop: 10 }}>
                <summary className="meta" style={{ cursor: "pointer" }}>展开微调各 Agent Prompt</summary>
                {PROMPT_FIELD_META.map((field) => (
                  <div key={`pending-${field.key}`} style={{ marginTop: 8 }}>
                    <div className="section-subtitle" style={{ marginTop: 0 }}>{field.label}</div>
                    <textarea
                      value={String(settings.runPromptOverrides[field.key] ?? settings.effectiveRunPrompt[field.key] ?? "")}
                      onChange={(e) => {
                        const v = e.target.value;
                        settings.setRunPromptOverrides((prev) => {
                          const next = { ...prev };
                          if (!v.trim()) { delete next[field.key]; } else { next[field.key] = v; }
                          return next;
                        });
                      }}
                      style={{ minHeight: field.key === "agent_c_instruction" ? 76 : 56, marginTop: 4 }}
                    />
                  </div>
                ))}
              </details>
            </div>

            <div className="run-confirm-actions">
              <button onClick={closePendingRunModal}>取消</button>
              <button
                className="primary"
                onClick={() => void onConfirmPendingRun()}
                disabled={study.isUploading || study.isRegenerating}
              >
                {study.pendingRun.kind === "upload"
                  ? (study.isUploading ? "处理中…" : "确认并开始")
                  : study.pendingRun.kind === "explain"
                    ? (study.isRegenerating ? "解释生成中…" : "确认并解释")
                  : (study.isRegenerating ? "重生成中…" : "确认并重生成")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
