import { useEffect, useMemo, useRef, useState } from "react";
import { MarkdownMath } from "./MarkdownMath";
import type { useDocumentStudy } from "@/lib/hooks/useDocumentStudy";
import type { useChat } from "@/lib/hooks/useChat";
import type { Language } from "@/lib/types";
import { toReadableBullets } from "@/lib/utils";
import { assetUrl } from "@/lib/api";

type BlockBBox = { x: number; y: number; width: number; height: number };

type ReflowReaderBlock = {
    id: string;
    kind: string;
    text: string;
    sourceText: string;
    status: "translated" | "preserved";
    reason: string;
};

function untranslatedReasonLabel(reason: string): string {
    switch (reason) {
        case "formula":
            return "公式保留原文";
        case "low_confidence_ocr":
            return "OCR 置信度不足";
        case "page_marker":
            return "页码/装饰文本已跳过";
        case "too_short":
            return "过短文本已跳过";
        case "translation_failed":
            return "翻译失败，暂保留原文";
        default:
            return reason || "未覆盖";
    }
}

function agentStatusLabel(status: string): string {
    switch (status) {
        case "completed":
            return "完成";
        case "available":
            return "可触发";
        case "skipped":
            return "跳过";
        case "pending":
            return "等待";
        default:
            return status || "未知";
    }
}

export function StudyPanel({
    study,
    chat,
    documentPromptSnapshot: _documentPromptSnapshot,
    onExplain,
    onRegenerate,
}: {
    study: ReturnType<typeof useDocumentStudy>;
    chat: ReturnType<typeof useChat>;
    documentPromptSnapshot: unknown;
    onExplain: () => void;
    onRegenerate: () => void;
}) {
    const {
        documentId,
        pageNo,
        page,
        status,
        outline,
        totalPages,
        processedPages,
        setPageNo,
        isRegenerating,
        readingMode,
        setReadingMode,
        readerSurfaceMode,
        setReaderSurfaceMode,
        regeneratingTarget,
        regenerateStatusText,
        onPrev,
        onNext,
    } = study;

    const {
        chatInput,
        setChatInput,
        chatTurns,
        isAsking,
        onAsk,
    } = chat;

    const explanation = page?.explanation;
    const language = study.language ?? "zh";
    const [jumpValue, setJumpValue] = useState(String(pageNo));
    const touchStartXRef = useRef<number | null>(null);
    const touchStartYRef = useRef<number | null>(null);

    const readingTabs = Array.isArray(page?.reading_tabs) && page.reading_tabs.length > 0
        ? page.reading_tabs
        : ["translate"];
    const defaultReadingTab = explanation || (page?.default_tab === "explain" && readingTabs.includes("explain"))
        ? "explain"
        : "translate";
    const [activeReadingTab, setActiveReadingTab] = useState<"explain" | "translate">(defaultReadingTab);

    const layoutBlocks = useMemo(() => page?.layout_blocks ?? [], [page?.layout_blocks]);
    const translationBlocks = useMemo(
        () => [...(page?.translation_blocks ?? [])].sort((a, b) => (a.reading_order ?? 0) - (b.reading_order ?? 0)),
        [page?.translation_blocks],
    );
    const untranslatedBlocks = page?.untranslated_blocks ?? [];
    const translatedBlockMap = useMemo(() => {
        const entries = translationBlocks.map((block) => [block.block_id, block] as const);
        return new Map(entries);
    }, [translationBlocks]);
    const untranslatedBlockMap = useMemo(() => {
        const entries = untranslatedBlocks.map((block) => [block.block_id, block] as const);
        return new Map(entries);
    }, [untranslatedBlocks]);
    const reflowBlocks = useMemo<ReflowReaderBlock[]>(() => {
        const items: ReflowReaderBlock[] = [];
        for (const block of [...layoutBlocks].sort((a, b) => (a.reading_order ?? 0) - (b.reading_order ?? 0))) {
                const blockId = String(block.id || "");
                const translated = translatedBlockMap.get(blockId);
                const untranslated = untranslatedBlockMap.get(blockId);
                const sourceText = String(block.text || "").trim();
                const kind = String(translated?.kind || block.kind || "paragraph").trim().toLowerCase();
                if (!sourceText) {
                    continue;
                }
                if (translated?.text) {
                    items.push({
                        id: String(translated.id || blockId),
                        kind,
                        text: String(translated.text || "").trim(),
                        sourceText,
                        status: "translated",
                        reason: "",
                    });
                    continue;
                }
                if (untranslated?.reason === "page_marker" || kind === "footer") {
                    continue;
                }
                if (untranslated?.reason === "formula" || kind === "formula") {
                    items.push({
                        id: `preserved-${blockId}`,
                        kind: "formula",
                        text: sourceText,
                        sourceText,
                        status: "preserved",
                        reason: String(untranslated?.reason || "formula"),
                    });
                    continue;
                }
                if (readerSurfaceMode !== "original") {
                    items.push({
                        id: `fallback-${blockId}`,
                        kind,
                        text: sourceText,
                        sourceText,
                        status: "preserved",
                        reason: String(untranslated?.reason || "untranslated"),
                    });
                }
        }
        return items;
    }, [layoutBlocks, readerSurfaceMode, translatedBlockMap, untranslatedBlockMap]);

    const quickReadPoints = explanation
        ? toReadableBullets(explanation.scaffold?.quick30 || explanation.keyPoints, 4)
        : [];
    const clarityConclusion = explanation ? String(explanation.clarity?.conclusion || explanation.overview || "").trim() : "";
    const claritySteps = explanation?.clarity?.steps ?? [];
    const focusSteps = claritySteps.length > 0 ? claritySteps : quickReadPoints.slice(0, 3);
    const clarityExample = explanation ? String(explanation.clarity?.example || explanation.teaching?.example || "").trim() : "";
    const doNow = explanation ? String(explanation.microTask?.doNow || "").trim() : "";
    const literalTranslation = String(page?.literal_translation || explanation?.literalTranslation || "").trim();
    const overlayStatus = String(page?.translation_overlay_status || "pending").trim().toLowerCase();
    const qualityHint = String(page?.quality_hint || explanation?.statusHint || page?.statusHint || "").trim();
    const chapterGroups = page?.chapter_nav?.groups ?? [];
    const currentChapterGroupId = String(page?.chapter_nav?.current_group_id || "");
    const evidenceDrawer = page?.evidence_drawer;
    const agentGraph = page?.agent_graph;
    const agentGraphNodes = agentGraph?.nodes ?? [];
    const frameworkEntries = Object.entries(agentGraph?.framework_mapping ?? {});
    const activePageRunMode = regeneratingTarget
        && regeneratingTarget.documentId === documentId
        && regeneratingTarget.pageNo === pageNo
        ? regeneratingTarget.mode
        : null;

    const primaryActionText = !explanation
        ? activePageRunMode === "explain"
            ? "本页解释生成中…"
            : "解释这一页"
        : activePageRunMode === "regenerate"
            ? "本页重生成中…"
            : "重生成本页解释";
    const canPrev = Boolean(documentId) && pageNo > 1;
    const canNext = Boolean(documentId) && (totalPages <= 0 || pageNo < totalPages);
    const showReflowReader = readerSurfaceMode !== "original";
    const readerModeText = readerSurfaceMode === "original" ? "原文" : readerSurfaceMode === "translated" ? "翻译" : "双语";
    const overlayStatusText =
        overlayStatus === "ready"
            ? "译文就绪"
            : overlayStatus === "partial"
                ? "部分译文就绪"
                : overlayStatus === "unavailable"
                    ? "译文不可用"
                    : overlayStatus === "legacy"
                        ? "旧版文档"
                        : "译文生成中";

    useEffect(() => {
        setJumpValue(String(pageNo));
    }, [pageNo]);

    useEffect(() => {
        setActiveReadingTab(defaultReadingTab);
    }, [defaultReadingTab, documentId, pageNo]);

    const goToPage = () => {
        if (!documentId) return;
        const parsed = Number(jumpValue);
        if (!Number.isFinite(parsed)) {
            setJumpValue(String(pageNo));
            return;
        }
        const ceiling = totalPages > 0 ? totalPages : pageNo;
        const bounded = Math.min(Math.max(1, Math.floor(parsed)), Math.max(1, ceiling));
        setPageNo(bounded);
        setJumpValue(String(bounded));
    };

    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            if (!documentId) return;
            const target = event.target as HTMLElement | null;
            if (
                target
                && (
                    target.tagName === "INPUT"
                    || target.tagName === "TEXTAREA"
                    || target.isContentEditable
                )
            ) {
                return;
            }

            if (event.key === "ArrowLeft" || event.key === "PageUp" || (event.key === " " && event.shiftKey)) {
                event.preventDefault();
                onPrev();
                return;
            }
            if (event.key === "ArrowRight" || event.key === "PageDown" || (event.key === " " && !event.shiftKey)) {
                event.preventDefault();
                onNext();
            }
        };

        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [documentId, onPrev, onNext]);

    return (
        <section className="core-reader-shell">
            <div className="core-reader-document-pane">
                <div className="core-reader-pane-header">
                    <div className="core-page-controls">
                        <button onClick={onPrev} disabled={!canPrev}>上一页</button>
                        <span className="core-page-indicator">
                            第 {pageNo} 页{totalPages > 0 ? ` / ${totalPages}` : ""}
                            {status ? ` · 已处理 ${processedPages} 页` : ""}
                        </span>
                        <button onClick={onNext} disabled={!canNext}>下一页</button>
                    </div>

                    <div className="core-reader-actions">
                        <div className="mode-toggle reader-mode-toggle">
                            <button
                                className={`mode-btn ${readerSurfaceMode === "original" ? "active" : ""}`}
                                onClick={() => setReaderSurfaceMode("original")}
                            >原文</button>
                            <button
                                className={`mode-btn ${readerSurfaceMode === "translated" ? "active" : ""}`}
                                onClick={() => setReaderSurfaceMode("translated")}
                            >翻译</button>
                            <button
                                className={`mode-btn ${readerSurfaceMode === "bilingual" ? "active" : ""}`}
                                onClick={() => setReaderSurfaceMode("bilingual")}
                            >双语</button>
                        </div>
                        <div className="page-jump-wrap">
                            <span className="meta">跳转</span>
                            <input
                                type="number"
                                min={1}
                                max={Math.max(1, totalPages || pageNo)}
                                value={jumpValue}
                                onChange={(e) => setJumpValue(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") {
                                        e.preventDefault();
                                        goToPage();
                                    }
                                }}
                                onBlur={goToPage}
                            />
                        </div>
                    </div>
                </div>

                <div
                    className="core-page-stage"
                    onTouchStart={(event) => {
                        const touch = event.changedTouches[0];
                        if (!touch) return;
                        touchStartXRef.current = touch.clientX;
                        touchStartYRef.current = touch.clientY;
                    }}
                    onTouchEnd={(event) => {
                        const touch = event.changedTouches[0];
                        const sx = touchStartXRef.current;
                        const sy = touchStartYRef.current;
                        touchStartXRef.current = null;
                        touchStartYRef.current = null;
                        if (!touch || sx == null || sy == null) return;
                        const dx = touch.clientX - sx;
                        const dy = touch.clientY - sy;
                        if (Math.abs(dx) < 56 || Math.abs(dx) < Math.abs(dy)) return;
                        if (dx > 0) {
                            onPrev();
                        } else {
                            onNext();
                        }
                    }}
                >
                    {page?.image_url && readerSurfaceMode === "original" ? (
                        <div className="page-canvas">
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img className="page-image page-layer-image" src={assetUrl(page.image_url)} alt={`Page ${page.page_no}`} />
                            {page && overlayStatus !== "ready" && (
                                <div className="translation-overlay-badge">{overlayStatusText}</div>
                            )}
                        </div>
                    ) : showReflowReader ? (
                        <div className="translation-page-sheet core-translation-sheet">
                            {reflowBlocks.length > 0 ? (
                                <div className="translation-reflow-list">
                                    {reflowBlocks.map((block) => (
                                        <article
                                            key={block.id}
                                            className={`translation-reflow-block kind-${block.kind} status-${block.status}`}
                                        >
                                            {block.kind === "title" ? (
                                                <h2>{block.text}</h2>
                                            ) : (
                                                <MarkdownMath content={block.text} softBreaks />
                                            )}
                                            {block.status === "preserved" && (
                                                <div className="translation-reflow-note">
                                                    {untranslatedReasonLabel(block.reason)}
                                                </div>
                                            )}
                                            {readerSurfaceMode === "bilingual" && block.sourceText && block.sourceText !== block.text && (
                                                <div className="translation-reflow-source">
                                                    <div className="section-subtitle">原文</div>
                                                    <MarkdownMath content={block.sourceText} softBreaks />
                                                </div>
                                            )}
                                        </article>
                                    ))}
                                </div>
                            ) : literalTranslation ? (
                                <div className="translation-reflow-fallback">
                                    <MarkdownMath content={literalTranslation} softBreaks />
                                </div>
                            ) : (
                                <span className="meta">当前页译文还在生成或没有可靠译文。</span>
                            )}
                        </div>
                    ) : (
                        <span className="meta">
                            {totalPages > 0 ? "当前页还在抽取或加载中。" : "等待预处理完成后显示页面。"}
                        </span>
                    )}
                </div>

                {(chapterGroups.length > 0 || (outline && (outline.global_summary || (outline.groups?.length ?? 0) > 0))) && (
                    <details className="core-secondary-details">
                        <summary>章节与文档记忆</summary>
                        {chapterGroups.length > 0 && (
                            <div className="core-chapter-list">
                                {chapterGroups.map((group) => (
                                    <button
                                        key={group.id}
                                        className={`study-recent-item ${currentChapterGroupId === group.id ? "active" : ""}`}
                                        onClick={() => setPageNo(group.page_start)}
                                    >
                                        <strong>{group.title}</strong>
                                        <span className="meta">
                                            P{group.page_start}-{group.page_end} · 已解释 {group.explained_pages} 页
                                        </span>
                                    </button>
                                ))}
                            </div>
                        )}
                        {outline?.global_summary && <MarkdownMath content={outline.global_summary} />}
                        {(outline?.keywords || []).length > 0 && (
                            <div className="pills">
                                {(outline?.keywords || []).map((item) => (
                                    <span className="pill" key={item}>{item}</span>
                                ))}
                            </div>
                        )}
                    </details>
                )}
            </div>

            <aside className="core-reader-assistant-pane">
                <div className="assistant-primary-panel">
                    <div className="assistant-panel-header">
                        <div>
                            <h3>AI 讲解</h3>
                            <p className="meta">先抓结论，再看步骤，最后做一个小检查。</p>
                        </div>
                        <button
                            className="primary"
                            onClick={explanation ? onRegenerate : onExplain}
                            disabled={!page || isRegenerating}
                        >
                            {primaryActionText}
                        </button>
                    </div>

                    <div className="assistant-toolbar">
                        <select
                            value={language}
                            onChange={(e) => study.setLanguage(e.target.value as Language)}
                        >
                            <option value="zh">中文</option>
                            <option value="en">English</option>
                        </select>
                        <div className="mode-toggle">
                            <button
                                className={`mode-btn ${activeReadingTab === "explain" ? "active" : ""}`}
                                onClick={() => setActiveReadingTab("explain")}
                                disabled={!readingTabs.includes("explain") && !explanation}
                            >讲解</button>
                            <button
                                className={`mode-btn ${activeReadingTab === "translate" ? "active" : ""}`}
                                onClick={() => setActiveReadingTab("translate")}
                            >页面状态</button>
                        </div>
                        {activeReadingTab === "explain" && explanation && (
                            <div className="mode-toggle">
                                <button
                                    className={`mode-btn ${readingMode === "focus" ? "active" : ""}`}
                                    onClick={() => setReadingMode("focus")}
                                >聚焦</button>
                                <button
                                    className={`mode-btn ${readingMode === "detail" ? "active" : ""}`}
                                    onClick={() => setReadingMode("detail")}
                                >细节</button>
                            </div>
                        )}
                    </div>

                    {regenerateStatusText && <p className="meta">{regenerateStatusText}</p>}
                    {qualityHint && <p className="meta">{qualityHint}</p>}

                    {activeReadingTab === "translate" && (
                        <div className="assistant-status-panel">
                            <div className="core-status-grid">
                                <div>
                                    <span className="section-subtitle">阅读模式</span>
                                    <strong>{readerModeText}</strong>
                                </div>
                                <div>
                                    <span className="section-subtitle">页面状态</span>
                                    <strong>{overlayStatusText}</strong>
                                </div>
                                <div>
                                    <span className="section-subtitle">译文块</span>
                                    <strong>{translationBlocks.length}</strong>
                                </div>
                                <div>
                                    <span className="section-subtitle">保留原文</span>
                                    <strong>{untranslatedBlocks.length}</strong>
                                </div>
                            </div>
                            {untranslatedBlocks.length > 0 && (
                                <details className="core-secondary-details compact">
                                    <summary>查看保留原文</summary>
                                    <ul className="quick-read-list compact-list">
                                        {untranslatedBlocks.slice(0, 5).map((block) => (
                                            <li key={`${block.block_id}-${block.reason}`}>
                                                <strong>{untranslatedReasonLabel(block.reason)}：</strong>{block.text}
                                            </li>
                                        ))}
                                    </ul>
                                </details>
                            )}
                            {literalTranslation && (
                                <details className="core-secondary-details compact">
                                    <summary>查看整页直译文本</summary>
                                    <MarkdownMath content={literalTranslation} softBreaks />
                                </details>
                            )}
                            {!explanation && (
                                <button className="primary" onClick={onExplain} disabled={isRegenerating}>
                                    {activePageRunMode === "explain" ? "解释生成中…" : "生成本页讲解"}
                                </button>
                            )}
                        </div>
                    )}

                    {activeReadingTab === "explain" && !explanation && (
                        <div className="assistant-empty-state">
                            <h3>这页还没有讲解</h3>
                            <p className="meta">先读左侧译文；如果卡住，就生成本页讲解。</p>
                            <button className="primary" onClick={onExplain} disabled={isRegenerating}>
                                {activePageRunMode === "explain" ? "解释生成中…" : "生成本页讲解"}
                            </button>
                        </div>
                    )}

                    {activeReadingTab === "explain" && explanation && readingMode === "focus" && (
                        <div className="assistant-focus-stack">
                            <section className="assistant-answer-block lead">
                                <h3>一句话结论</h3>
                                <MarkdownMath content={clarityConclusion || explanation.overview || "先抓住本页结论。"} />
                            </section>
                            <section className="assistant-answer-block">
                                <h3>三步讲解</h3>
                                {focusSteps.length > 0 ? (
                                    <ol>
                                        {focusSteps.map((step, idx) => (
                                            <li key={`focus-step-${idx}`}><MarkdownMath content={step} /></li>
                                        ))}
                                    </ol>
                                ) : (
                                    <p className="meta">这页还没有提炼出明确的三步结构。</p>
                                )}
                            </section>
                            <section className="assistant-answer-block">
                                <h3>一个具体例子</h3>
                                <MarkdownMath content={clarityExample || "当前页没有现成例子，建议用自己的一个小例子替换进去。"} />
                            </section>
                            <section className="assistant-answer-block">
                                <h3>你现在就做</h3>
                                <MarkdownMath content={doNow || "先复述本页解决的问题，再回答自检问题。"} />
                                <div className="quick-read-action">
                                    <strong>自检问题：</strong>
                                    <MarkdownMath content={explanation.microTask?.checkQuestion || "如果换成白话，你能把这页重新讲一遍吗？"} />
                                </div>
                            </section>
                            <details className="core-secondary-details compact">
                                <summary>上一页承接 / 下一页预告</summary>
                                <div className="continuity-stack">
                                    <div><strong>上一页承接：</strong><MarkdownMath content={explanation.continuity?.prevBridge || "-"} /></div>
                                    <div><strong>本页新增：</strong><MarkdownMath content={explanation.continuity?.thisPageNew || "-"} /></div>
                                    <div><strong>下一页预告：</strong><MarkdownMath content={explanation.continuity?.nextPreview || "-"} /></div>
                                </div>
                            </details>
                        </div>
                    )}

                    {activeReadingTab === "explain" && explanation && readingMode === "detail" && (
                        <div className="assistant-focus-stack">
                            <section className="assistant-answer-block lead">
                                <h3>教学解释</h3>
                                <MarkdownMath content={explanation.overview || ""} />
                            </section>
                            <div className="teaching-grid">
                                <div className="teach-card">
                                    <div className="section-subtitle">定义</div>
                                    <MarkdownMath content={explanation.teaching?.definition || "-"} />
                                </div>
                                <div className="teach-card">
                                    <div className="section-subtitle">直觉</div>
                                    <MarkdownMath content={explanation.teaching?.intuition || "-"} />
                                </div>
                                <div className="teach-card">
                                    <div className="section-subtitle">例子</div>
                                    <MarkdownMath content={explanation.teaching?.example || "-"} />
                                </div>
                                <div className="teach-card">
                                    <div className="section-subtitle">本页重点</div>
                                    <MarkdownMath content={explanation.teaching?.focus || "-"} />
                                </div>
                                <div className="teach-card">
                                    <div className="section-subtitle">易错点</div>
                                    <MarkdownMath content={explanation.teaching?.pitfall || "-"} />
                                </div>
                            </div>
                            {(explanation.keyPoints || []).length > 0 && (
                                <section className="assistant-answer-block">
                                    <h3>关键点</h3>
                                    <ul className="quick-read-list">
                                        {(explanation.keyPoints || []).map((point: string, idx: number) => (
                                            <li key={`${idx}-${point.slice(0, 20)}`}><MarkdownMath content={point} /></li>
                                        ))}
                                    </ul>
                                </section>
                            )}
                        </div>
                    )}
                </div>

                <section className="core-chat-section">
                    <div className="core-chat-header">
                        <h3>页内追问</h3>
                        <span className="meta">{chatTurns.length > 0 ? `${chatTurns.length} 条` : "可选"}</span>
                    </div>
                    {chatTurns.length > 0 && (
                        <div className="chat-list">
                            {chatTurns.map((turn, idx) => (
                                <div className={`chat-item ${turn.role}`} key={`${turn.role}-${idx}`}>
                                    <strong>{turn.role === "ask" ? "你" : "助手"}：</strong>
                                    <MarkdownMath content={turn.text} />
                                    {turn.citations && turn.citations.length > 0 && (
                                        <div className="meta">证据页：{turn.citations.map((c) => c.pageNo).join(", ")}</div>
                                    )}
                                    {turn.role === "answer" && turn.scopePages && turn.scopePages.length > 0 && (
                                        <div className="meta">回答范围页：{turn.scopePages.join(", ")}</div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                    <div className="chat-input-row">
                        <textarea
                            className="chat-input"
                            value={chatInput}
                            onChange={(e) => setChatInput(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { void onAsk(); } }}
                            placeholder="问这一页哪里不明白"
                        />
                        <button className="primary" onClick={() => void onAsk()} disabled={!page || isAsking || !chatInput.trim()}>
                            {isAsking ? "…" : "发送"}
                        </button>
                    </div>
                </section>

                {(evidenceDrawer?.run || (evidenceDrawer?.citations || []).length > 0) && (
                    <details className="core-secondary-details">
                        <summary>证据与运行记录</summary>
                        <div className="meta" style={{ marginTop: 8 }}>
                            scope pages：{(evidenceDrawer?.scope_pages || []).join(", ") || "-"}
                        </div>
                        {evidenceDrawer?.run && (
                            <div className="meta" style={{ marginTop: 6 }}>
                                当前 run：{evidenceDrawer.run.trigger_type} · {evidenceDrawer.run.status}
                                {evidenceDrawer.run.target_page_no ? ` · P${evidenceDrawer.run.target_page_no}` : ""}
                            </div>
                        )}
                        {(evidenceDrawer?.citations || []).length > 0 && (
                            <ul style={{ marginTop: 10 }}>
                                {(evidenceDrawer?.citations || []).map((cite, idx) => (
                                    <li key={`drawer-cite-${idx}`}>
                                        P{cite.pageNo} · {cite.span} · {cite.quote}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </details>
                )}

                {agentGraphNodes.length > 0 && (
                    <details className="core-secondary-details agent-graph-details">
                        <summary>Agent Graph / 技术链路</summary>
                        <div className="agent-node-list">
                            {agentGraphNodes.map((node) => (
                                <div className={`agent-node-row status-${node.status}`} key={node.id}>
                                    <div className="agent-node-topline">
                                        <strong>{node.label}</strong>
                                        <span>{agentStatusLabel(node.status)}</span>
                                    </div>
                                    <div className="meta">{node.requirement}</div>
                                    <p>{node.evidence}</p>
                                </div>
                            ))}
                        </div>
                        {agentGraph?.run?.model_chain && agentGraph.run.model_chain.length > 0 && (
                            <div className="meta agent-model-chain">
                                model chain：{agentGraph.run.model_chain.join(" -> ")}
                            </div>
                        )}
                        {frameworkEntries.length > 0 && (
                            <div className="agent-framework-map">
                                {frameworkEntries.map(([name, desc]) => (
                                    <div key={name}>
                                        <strong>{name}</strong>
                                        <p>{desc}</p>
                                    </div>
                                ))}
                            </div>
                        )}
                    </details>
                )}
            </aside>
        </section>
    );
}
