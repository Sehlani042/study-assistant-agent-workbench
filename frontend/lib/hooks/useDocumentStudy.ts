import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
    getDocumentStatus,
    getOutline,
    getPage,
    listDocuments,
    uploadDocument,
    explainPage,
    regeneratePage,
    clearDocument,
    cancelDocument,
} from "@/lib/api";
import type {
    DocumentStatus,
    Outline,
    PagePayload,
    Language,
    DocumentHistoryItem,
    LearningPreferences,
    UserPayload,
    PromptOverridePayload,
    PendingRun,
} from "@/lib/types";
import { isPageNotReadyError, SESSION_KEY } from "@/lib/utils";

type StudySessionPayload = {
    user_id?: string;
    document_id?: string;
    page_no?: number;
};

const PAGE_CACHE_MAX_ENTRIES = 240;

function pageCacheKey(documentId: string, pageNo: number, language: Language): string {
    return `${documentId}::${language}::${pageNo}`;
}

function loadStudySession(): StudySessionPayload | null {
    if (typeof window === "undefined") return null;
    try {
        const raw = window.localStorage.getItem(SESSION_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw) as StudySessionPayload;
        if (!parsed || typeof parsed !== "object") return null;
        return parsed;
    } catch {
        return null;
    }
}

function saveStudySession(payload: StudySessionPayload | null): void {
    if (typeof window === "undefined") return;
    try {
        if (!payload || !payload.document_id) {
            window.localStorage.removeItem(SESSION_KEY);
            return;
        }
        window.localStorage.setItem(SESSION_KEY, JSON.stringify(payload));
    } catch {
        // ignore storage errors
    }
}

export function useDocumentStudy({
    currentUser,
    language,
    setLanguage,
    loadDocumentPromptSnapshot,
    ensureLLMReady,
}: {
    currentUser: UserPayload | null;
    language: Language;
    setLanguage: (lang: Language) => void;
    loadDocumentPromptSnapshot: (docId: string, options?: { silent?: boolean }) => Promise<void>;
    ensureLLMReady: () => boolean;
}) {
    const [documentId, setDocumentId] = useState<string | null>(null);
    const [status, setStatus] = useState<DocumentStatus | null>(null);
    const [outline, setOutline] = useState<Outline | null>(null);
    const [page, setPage] = useState<PagePayload | null>(null);
    const [pageNo, setPageNo] = useState(1);
    const [readingMode, setReadingMode] = useState<"focus" | "detail">("focus");
    const [readerSurfaceMode, setReaderSurfaceMode] = useState<"original" | "translated" | "bilingual">("translated");

    const [isUploading, setIsUploading] = useState(false);
    const [regeneratingTarget, setRegeneratingTarget] = useState<{ documentId: string; pageNo: number; mode: "explain" | "regenerate" } | null>(null);
    const [isCanceling, setIsCanceling] = useState(false);
    const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null);

    const [historyDocuments, setHistoryDocuments] = useState<DocumentHistoryItem[]>([]);
    const [queueDocuments, setQueueDocuments] = useState<DocumentHistoryItem[]>([]);
    const [isLoadingHistory, setIsLoadingHistory] = useState(false);
    const [isLoadingQueue, setIsLoadingQueue] = useState(false);
    const [historyOpen, setHistoryOpen] = useState(false);
    const [historyHydrated, setHistoryHydrated] = useState(false);

    const [pickedUploadFile, setPickedUploadFile] = useState<File | null>(null);
    const [uploadPickerKey, setUploadPickerKey] = useState(0);

    const [pendingRun, setPendingRun] = useState<PendingRun | null>(null);
    const [error, setError] = useState<string | null>(null);
    const documentIdRef = useRef<string | null>(null);
    const pageNoRef = useRef<number>(1);
    const pageRequestSeqRef = useRef(0);
    const pageCacheRef = useRef<Map<string, PagePayload>>(new Map());
    const pageReadyStateRef = useRef<Map<string, boolean>>(new Map());

    useEffect(() => {
        documentIdRef.current = documentId;
    }, [documentId]);

    useEffect(() => {
        pageNoRef.current = pageNo;
    }, [pageNo]);

    const totalPages = status?.progress.total_pages ?? 0;
    const processedPages = status?.progress.processed_pages ?? 0;
    const isCurrentPageReady = Boolean(totalPages > 0 && pageNo <= processedPages);
    const translationPending = Number(status?.pipeline_detail?.translation_pending ?? 0);
    const translationDone = Number(status?.pipeline_detail?.translation_done ?? 0);
    const translationFailed = Number(status?.pipeline_detail?.translation_failed ?? 0);
    const isRegenerating = regeneratingTarget !== null;
    const regenerateStatusText = useMemo(() => {
        if (!regeneratingTarget) return "";
        const actionLabel = regeneratingTarget.mode === "explain" ? "解释" : "重生成";
        if (regeneratingTarget.documentId === documentId) {
            if (regeneratingTarget.pageNo === pageNo) {
                return `正在${actionLabel}第 ${regeneratingTarget.pageNo} 页（仅本页）`;
            }
            return `正在${actionLabel}第 ${regeneratingTarget.pageNo} 页（仅本页）；你当前在第 ${pageNo} 页`;
        }
        return `正在${actionLabel}其他文档的第 ${regeneratingTarget.pageNo} 页`;
    }, [regeneratingTarget, documentId, pageNo]);

    const pipelineDetailText = useMemo(() => {
        if (!status?.pipeline_detail) return "";
        const d = status.pipeline_detail;
        const stageSecs = Number.isFinite(d.stage_elapsed_seconds) ? Math.round(d.stage_elapsed_seconds) : 0;
        const totalSecs = Number.isFinite(d.total_elapsed_seconds) ? Math.round(d.total_elapsed_seconds) : 0;
        const active = d.current_pages?.length ? ` | 处理中页: ${d.current_pages.join(", ")}` : "";
        const repairable = d.repairable_pages?.length ? ` | 可修复页: ${d.repairable_pages.join(", ")}` : "";
        const failReason =
            d.failed_page_details && d.failed_page_details.length > 0
                ? ` | 最近失败: P${d.failed_page_details[0].page_no} ${d.failed_page_details[0].reason || ""}`.trim()
                : "";
        const c1Perf = ` | C1延迟(ms): avg ${Math.round(d.avg_c1_latency_ms || 0)} / p95 ${Math.round(d.p95_c1_latency_ms || 0)} | C1超时: ${d.c1_timeout_pages || 0}`;
        const promotion = ` | Pro升级: ${d.pro_escalation_pages || 0} | 末级兜底(OpenAI): ${d.last_resort_pages || 0}`;
        const modelPaths = Object.entries(d.model_path_counts || {})
            .filter(([, count]) => Number(count) > 0)
            .map(([name, count]) => `${name}:${count}`)
            .join(", ");
        const modelPathsText = modelPaths ? ` | 模型路径: ${modelPaths}` : "";
        const llmErrors = Object.entries(d.llm_error_counts || {})
            .filter(([, count]) => Number(count) > 0)
            .map(([name, count]) => `${name}:${count}`)
            .join(", ");
        const llmErrorsText = llmErrors ? ` | LLM异常: ${llmErrors}` : "";
        const adaptiveText = d.adaptive_worker_reason ? ` | 自适应: ${d.adaptive_worker_reason}` : "";
        const coverageText = d.coverage_lang_mode ? ` | coverage模式: ${d.coverage_lang_mode}` : "";
        const translation = ` | 覆盖翻译: 待 ${d.translation_pending || 0} / 成 ${d.translation_done || 0} / 败 ${d.translation_failed || 0}`;
        const clock = ` | 阶段耗时: ${stageSecs}s | 总耗时: ${totalSecs}s`;
        const streakText = ` | 连续质控失败: ${d.quality_fail_streak || 0}`;
        return `运行: ${d.running_agent} | 并发: ${d.active_workers} | 队列: ${d.queued_pages} | 已完成: ${d.done_pages} | 质控未过: ${d.failed_pages} | 重试: ${d.retry_pages}${clock}${c1Perf}${promotion}${modelPathsText}${llmErrorsText}${streakText}${adaptiveText}${coverageText}${translation}${active}${repairable}${failReason}`;
    }, [status, translationPending]);

    const statusText = useMemo(() => {
        if (!status) return "尚未上传文档";
        const stageText = status.stage_label ? ` | 阶段：${status.stage_label}` : "";
        const translationSuffix =
            status.status === "completed" && translationPending > 0
                ? ` | 覆盖翻译整理中：待 ${translationPending} 页`
                : "";
        return `状态：${status.status}${stageText} | 进度：${status.progress.processed_pages}/${status.progress.total_pages} (${status.progress.percent}%)${translationSuffix}`;
    }, [status]);

    const currentHistoryItem = useMemo(
        () => historyDocuments.find((item) => item.document_id === documentId) ?? null,
        [historyDocuments, documentId],
    );

    const refreshStatus = useCallback(async (docId: string) => {
        const next = await getDocumentStatus(docId);
        setStatus(next);
        return next;
    }, []);

    const getCachedPage = useCallback((docId: string, targetPage: number, lang: Language): PagePayload | null => {
        const key = pageCacheKey(docId, targetPage, lang);
        const hit = pageCacheRef.current.get(key);
        if (!hit) return null;
        // refresh LRU order
        pageCacheRef.current.delete(key);
        pageCacheRef.current.set(key, hit);
        return hit;
    }, []);

    const cachePage = useCallback((docId: string, targetPage: number, lang: Language, payload: PagePayload) => {
        const key = pageCacheKey(docId, targetPage, lang);
        pageCacheRef.current.delete(key);
        pageCacheRef.current.set(key, payload);
        while (pageCacheRef.current.size > PAGE_CACHE_MAX_ENTRIES) {
            const firstKey = pageCacheRef.current.keys().next().value as string | undefined;
            if (!firstKey) break;
            pageCacheRef.current.delete(firstKey);
        }
    }, []);

    const loadPage = useCallback(async (
        docId: string,
        targetPage: number,
        lang: Language,
        options?: { signal?: AbortSignal; preferCache?: boolean; requestSeq?: number },
    ) => {
        if (options?.preferCache && !options?.signal?.aborted) {
            const cached = getCachedPage(docId, targetPage, lang);
            if (cached) {
                if (!options.requestSeq || options.requestSeq === pageRequestSeqRef.current) {
                    setPage(cached);
                }
                return cached;
            }
        }
        const data = await getPage(docId, targetPage, lang, { signal: options?.signal });
        cachePage(docId, targetPage, lang, data);
        if (!options?.requestSeq || options.requestSeq === pageRequestSeqRef.current) {
            setPage(data);
        }
        return data;
    }, [getCachedPage, cachePage]);

    const prefetchPage = useCallback(async (docId: string, targetPage: number, lang: Language, signal?: AbortSignal) => {
        if (targetPage <= 0) return;
        if (getCachedPage(docId, targetPage, lang)) return;
        try {
            const data = await getPage(docId, targetPage, lang, { signal });
            cachePage(docId, targetPage, lang, data);
        } catch {
            // ignore prefetch failures
        }
    }, [getCachedPage, cachePage]);

    const loadDocumentHistory = useCallback(async (options?: { silent?: boolean }): Promise<DocumentHistoryItem[]> => {
        try {
            setIsLoadingHistory(true);
            const payload = await listDocuments(200, "library");
            const items = payload.items ?? [];
            setHistoryDocuments(items);
            setHistoryHydrated(true);
            return items;
        } catch (err) {
            setHistoryHydrated(true);
            if (!options?.silent) {
                setError(err instanceof Error ? err.message : "加载历史文档失败");
            }
            return [];
        } finally {
            setIsLoadingHistory(false);
        }
    }, []);

    const loadQueueDocuments = useCallback(async (options?: { silent?: boolean }): Promise<DocumentHistoryItem[]> => {
        try {
            setIsLoadingQueue(true);
            const payload = await listDocuments(200, "active");
            const items = payload.items ?? [];
            setQueueDocuments(items);
            return items;
        } catch (err) {
            if (!options?.silent) {
                setError(err instanceof Error ? err.message : "加载任务队列失败");
            }
            return [];
        } finally {
            setIsLoadingQueue(false);
        }
    }, []);

    useEffect(() => {
        if (!currentUser) {
            setHistoryDocuments([]);
            setHistoryHydrated(false);
            return;
        }
        void Promise.all([loadDocumentHistory({ silent: true }), loadQueueDocuments({ silent: true })]);
    }, [currentUser, loadDocumentHistory, loadQueueDocuments]);

    useEffect(() => {
        if (!currentUser || !historyHydrated) return;
        // Auto-restore should only run when no active document is selected.
        // Otherwise it may overwrite a newly submitted task with older history.
        if (documentId) return;
        if (historyDocuments.length <= 0) return;

        const saved = loadStudySession();
        const savedDocId =
            saved?.user_id === currentUser.id && saved.document_id
                ? String(saved.document_id).trim()
                : "";
        const fallbackDocId = historyDocuments[0]?.document_id ?? "";
        const targetDocId = historyDocuments.some((item) => item.document_id === savedDocId)
            ? savedDocId
            : fallbackDocId;
        if (!targetDocId) return;

        const rawPageNo = saved?.user_id === currentUser.id && saved.document_id === targetDocId
            ? Number(saved.page_no ?? 1)
            : 1;
        const targetPageNo = Number.isFinite(rawPageNo) && rawPageNo > 0 ? Math.floor(rawPageNo) : 1;

        setDocumentId(targetDocId);
        setStatus(null);
        setOutline(null);
        setPage(null);
        setPageNo(targetPageNo);

        void refreshStatus(targetDocId).catch((err) => {
            setError(err instanceof Error ? err.message : "恢复历史文档失败");
        });
        void loadDocumentPromptSnapshot(targetDocId, { silent: true });
    }, [
        currentUser,
        historyHydrated,
        historyDocuments,
        documentId,
        refreshStatus,
        loadDocumentPromptSnapshot,
    ]);

    useEffect(() => {
        if (!currentUser) return;
        if (!documentId) {
            saveStudySession(null);
            return;
        }
        saveStudySession({
            user_id: currentUser.id,
            document_id: documentId,
            page_no: pageNo,
        });
    }, [currentUser, documentId, pageNo]);

    // Poll status
    useEffect(() => {
        if (!currentUser || !documentId || !status) return;
        const terminal = status.status === "completed" || status.status === "failed" || status.status === "canceled";
        if (terminal && translationPending <= 0) return;
        const pollMs = terminal ? 3000 : 1200;

        const timer = window.setInterval(async () => {
            try {
                const next = await refreshStatus(documentId);
                if (next.status === "failed") {
                    setError(next.error ?? "处理失败");
                }
            } catch (err) {
                setError(err instanceof Error ? err.message : "状态轮询失败");
            }
        }, pollMs);

        return () => window.clearInterval(timer);
    }, [currentUser, documentId, status, refreshStatus, translationPending]);

    // Reload history when status finishes
    useEffect(() => {
        if (!currentUser || !status) return;
        if (status.status !== "completed" && status.status !== "failed" && status.status !== "canceled") return;
        void Promise.all([loadDocumentHistory({ silent: true }), loadQueueDocuments({ silent: true })]);
    }, [currentUser, status, loadDocumentHistory, loadQueueDocuments]);

    // Load Outline
    useEffect(() => {
        if (!currentUser || !documentId || !status) return;

        const stage = status.stage ?? "";
        const canLoadOutline =
            status.status === "completed" ||
            status.status === "canceled" ||
            stage.startsWith("agent_b:") ||
            stage.startsWith("agent_c:") ||
            stage.startsWith("agent_c1:") ||
            stage.startsWith("agent_c2:") ||
            stage.startsWith("quality:") ||
            stage === "failed" ||
            stage === "canceled";
        if (!canLoadOutline) return;

        const run = async () => {
            try {
                const nextOutline = await getOutline(documentId);
                const hasUsefulContent = Boolean(nextOutline.global_summary) || (nextOutline.groups?.length ?? 0) > 0;
                if (hasUsefulContent || status.status === "completed" || status.status === "canceled") {
                    setOutline(nextOutline);
                }
            } catch {
                // Ignore transient
            }
        };
        run();
    }, [currentUser, documentId, status]);

    // Load Page
    useEffect(() => {
        if (!currentUser || !documentId || !status) return;
        if (status.progress.total_pages <= 0) return;

        const boundedPage = Math.min(Math.max(1, pageNo), status.progress.total_pages);
        if (boundedPage !== pageNo) {
            setPageNo(boundedPage);
            return;
        }

        const currentReqSeq = ++pageRequestSeqRef.current;
        const controller = new AbortController();
        const run = async () => {
            try {
                const readyKey = pageCacheKey(documentId, boundedPage, language);
                const prevReady = pageReadyStateRef.current.get(readyKey);
                pageReadyStateRef.current.set(readyKey, isCurrentPageReady);
                const shouldForceRefresh = prevReady === false && isCurrentPageReady;
                const shouldRefreshForTranslation = status.status === "completed" && translationPending > 0;

                const next = await loadPage(documentId, boundedPage, language, {
                    signal: controller.signal,
                    preferCache: !(shouldForceRefresh || shouldRefreshForTranslation),
                    requestSeq: currentReqSeq,
                });
                if (!next) return;

                if (status.status === "processing" && !isCurrentPageReady) {
                    return;
                }

                if (boundedPage > 1) {
                    void prefetchPage(documentId, boundedPage - 1, language, controller.signal);
                }
                if (boundedPage < status.progress.total_pages) {
                    void prefetchPage(documentId, boundedPage + 1, language, controller.signal);
                }
            } catch (err) {
                if (controller.signal.aborted) return;
                const message = err instanceof Error ? err.message : "加载页面失败";
                if (message.includes("404")) {
                    setPage(null);
                    return;
                }
                setError(message);
            }
        };

        run();
        return () => controller.abort();
    }, [
        currentUser,
        documentId,
        status?.progress.total_pages,
        status?.status,
        isCurrentPageReady,
        translationPending,
        translationDone,
        translationFailed,
        pageNo,
        language,
        loadPage,
        prefetchPage,
    ]);

    useEffect(() => {
        if (!historyOpen) return;
        const onKey = (event: KeyboardEvent) => {
            if (event.key === "Escape") setHistoryOpen(false);
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [historyOpen]);

    // Return API actions
    const performUpload = async (
        file: File,
        taskPromptProfile: string,
        taskPrompt: string,
        promptOverrides?: PromptOverridePayload,
        learningProfile?: LearningPreferences,
    ) => {
        const beforeDocIds = new Set(historyDocuments.map((item) => item.document_id));
        const keepCurrentDocument = Boolean(documentIdRef.current);
        const cleanTaskPrompt = taskPrompt.trim();
        setError(null);
        if (!keepCurrentDocument) {
            setStatus(null);
            setOutline(null);
            setPage(null);
            setPageNo(1);
            pageCacheRef.current.clear();
            pageReadyStateRef.current.clear();
        }

        try {
            setIsUploading(true);
            const result = await uploadDocument(file, {
                prompt_profile: taskPromptProfile as "default" | "personal",
                task_prompt: cleanTaskPrompt || undefined,
                prompt_overrides: promptOverrides,
                learning_profile: learningProfile,
            });
            await Promise.all([loadDocumentHistory({ silent: true }), loadQueueDocuments({ silent: true })]);
            if (!keepCurrentDocument) {
                setDocumentId(result.document_id);
                await refreshStatus(result.document_id);
                await loadDocumentPromptSnapshot(result.document_id, { silent: true });
            } else {
                setError("新任务已加入队列，当前工作台保持在原文档。");
            }
        } catch (err) {
            const docs = await loadDocumentHistory({ silent: true });
            const newlyCreated = docs.find((item) => !beforeDocIds.has(item.document_id));
            if (newlyCreated) {
                try {
                    await loadQueueDocuments({ silent: true });
                    if (!keepCurrentDocument) {
                        setDocumentId(newlyCreated.document_id);
                        setStatus(null);
                        setOutline(null);
                        setPage(null);
                        setPageNo(1);
                        await refreshStatus(newlyCreated.document_id);
                        await loadDocumentPromptSnapshot(newlyCreated.document_id, { silent: true });
                    }
                    setError(keepCurrentDocument ? "上传请求已提交成功，任务已加入队列。" : "上传请求已提交成功。");
                    return;
                } catch {
                    // Fall through to error message handling.
                }
            }

            const message = err instanceof Error ? err.message : "上传失败";
            if (message.toLowerCase().includes("load failed") || message.toLowerCase().includes("failed to fetch")) {
                setError("上传失败：网络中断（Load failed）。请重新选择文件再提交；若是 iPhone/iPad，请先把文件下载到本地后再传。");
            } else {
                setError(message);
            }
        } finally {
            setIsUploading(false);
        }
    };

    const performRegenerate = async (
        targetDocumentId: string,
        targetPageNo: number,
        taskPromptProfile: string,
        taskPrompt: string,
        promptOverrides?: PromptOverridePayload,
        learningProfile?: LearningPreferences,
    ) => {
        const cleanTaskPrompt = taskPrompt.trim();
        setError(null);
        setRegeneratingTarget({ documentId: targetDocumentId, pageNo: targetPageNo, mode: "regenerate" });
        try {
            const next = await regeneratePage(targetDocumentId, targetPageNo, language, {
                prompt_profile: taskPromptProfile as "default" | "personal",
                task_prompt: cleanTaskPrompt,
                prompt_overrides: promptOverrides,
                learning_profile: learningProfile,
            });
            cachePage(targetDocumentId, targetPageNo, language, next.page);
            pageReadyStateRef.current.set(pageCacheKey(targetDocumentId, targetPageNo, language), true);
            const activeDocumentId = documentIdRef.current;
            const activePageNo = pageNoRef.current;
            if (activeDocumentId === targetDocumentId && activePageNo === targetPageNo) {
                setPage(next.page);
            }
            await loadDocumentPromptSnapshot(targetDocumentId, { silent: true });
            await loadQueueDocuments({ silent: true });
        } catch (err) {
            const message = err instanceof Error ? err.message : "重生成失败";
            if (isPageNotReadyError(message)) {
                setError("页面尚未就绪，正在处理中，请稍后再试。");
            } else {
                setError(message);
            }
        } finally {
            setRegeneratingTarget((current) => {
                if (!current) return null;
                if (current.documentId === targetDocumentId && current.pageNo === targetPageNo && current.mode === "regenerate") {
                    return null;
                }
                return current;
            });
        }
    };

    const performExplain = async (
        targetDocumentId: string,
        targetPageNo: number,
        taskPromptProfile: string,
        taskPrompt: string,
        promptOverrides?: PromptOverridePayload,
        learningProfile?: LearningPreferences,
    ) => {
        const cleanTaskPrompt = taskPrompt.trim();
        setError(null);
        setRegeneratingTarget({ documentId: targetDocumentId, pageNo: targetPageNo, mode: "explain" });
        try {
            const next = await explainPage(targetDocumentId, targetPageNo, language, {
                prompt_profile: taskPromptProfile as "default" | "personal",
                task_prompt: cleanTaskPrompt,
                prompt_overrides: promptOverrides,
                learning_profile: learningProfile,
            });
            cachePage(targetDocumentId, targetPageNo, language, next.page);
            pageReadyStateRef.current.set(pageCacheKey(targetDocumentId, targetPageNo, language), true);
            const activeDocumentId = documentIdRef.current;
            const activePageNo = pageNoRef.current;
            if (activeDocumentId === targetDocumentId && activePageNo === targetPageNo) {
                setPage(next.page);
            }
            await loadDocumentPromptSnapshot(targetDocumentId, { silent: true });
            await loadQueueDocuments({ silent: true });
        } catch (err) {
            const message = err instanceof Error ? err.message : "解释生成失败";
            if (isPageNotReadyError(message)) {
                setError("页面尚未就绪，正在处理中，请稍后再试。");
            } else {
                setError(message);
            }
        } finally {
            setRegeneratingTarget((current) => {
                if (!current) return null;
                if (current.documentId === targetDocumentId && current.pageNo === targetPageNo && current.mode === "explain") {
                    return null;
                }
                return current;
            });
        }
    };

    const onSelectHistoryDocument = async (docId: string) => {
        if (!docId || docId === documentId) return;
        setError(null);
        setHistoryOpen(false);
        setDocumentId(docId);
        setStatus(null);
        setOutline(null);
        setPage(null);
        setPageNo(1);
        pageCacheRef.current.clear();
        pageReadyStateRef.current.clear();
        try {
            await refreshStatus(docId);
            await loadDocumentPromptSnapshot(docId, { silent: true });
        } catch (err) {
            setError(err instanceof Error ? err.message : "切换历史文档失败");
        }
    };

    const onDeleteHistoryDocument = async (targetDocumentId: string) => {
        const docId = String(targetDocumentId || "").trim();
        if (!docId) return;
        setError(null);
        setDeletingDocumentId(docId);
        try {
            await clearDocument(docId);
            const docs = await loadDocumentHistory({ silent: true });

            if (documentId !== docId) return;

            const fallback = docs[0]?.document_id ?? null;
            if (!fallback) {
                setDocumentId(null);
                setStatus(null);
                setOutline(null);
                setPage(null);
                setPageNo(1);
                window.localStorage.removeItem(SESSION_KEY);
                return;
            }

            setDocumentId(fallback);
            setStatus(null);
            setOutline(null);
            setPage(null);
            setPageNo(1);
            pageCacheRef.current.clear();
            pageReadyStateRef.current.clear();
            await refreshStatus(fallback);
            await loadDocumentPromptSnapshot(fallback, { silent: true });
        } catch (err) {
            setError(err instanceof Error ? err.message : "删除文档失败");
        } finally {
            setDeletingDocumentId(null);
        }
    };

    const onClear = async () => {
        if (!documentId) return;
        await onDeleteHistoryDocument(documentId);
    };

    const onCancelTask = async () => {
        const targetDocumentId = documentId;
        const targetStatus = status;
        if (!targetDocumentId || !targetStatus) return;
        if (targetStatus.status === "completed" || targetStatus.status === "failed" || targetStatus.status === "canceled") return;
        setError(null);
        setIsCanceling(true);
        try {
            await cancelDocument(targetDocumentId);
            await refreshStatus(targetDocumentId);
            await Promise.all([loadDocumentHistory({ silent: true }), loadQueueDocuments({ silent: true })]);
        } catch (err) {
            setError(err instanceof Error ? err.message : "取消任务失败");
        } finally {
            setIsCanceling(false);
        }
    };

    const cancelSpecificTask = async (targetDocumentId: string) => {
        const clean = String(targetDocumentId || "").trim();
        if (!clean) return;
        setError(null);
        setIsCanceling(true);
        try {
            await cancelDocument(clean);
            if (clean === documentIdRef.current) {
                await refreshStatus(clean);
            }
            await Promise.all([loadDocumentHistory({ silent: true }), loadQueueDocuments({ silent: true })]);
        } catch (err) {
            setError(err instanceof Error ? err.message : "取消任务失败");
        } finally {
            setIsCanceling(false);
        }
    };

    const onPrev = () => setPageNo((p) => Math.max(1, p - 1));
    const onNext = () => setPageNo((p) => {
        const ceiling = totalPages > 0 ? totalPages : p + 1;
        return Math.min(ceiling, p + 1);
    });

    const onPickUploadFile = (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file) return;
        setPickedUploadFile(file);
    };

    const resetStudyState = useCallback(() => {
        setDocumentId(null);
        setStatus(null);
        setOutline(null);
        setPage(null);
        setPageNo(1);
        pageCacheRef.current.clear();
        pageReadyStateRef.current.clear();
        setRegeneratingTarget(null);
        setHistoryDocuments([]);
        setQueueDocuments([]);
        setPickedUploadFile(null);
        setPendingRun(null);
    }, []);

    return {
        documentId,
        setDocumentId,
        status,
        setStatus,
        outline,
        setOutline,
        page,
        setPage,
        pageNo,
        setPageNo,
        language,
        setLanguage,
        readingMode,
        setReadingMode,
        readerSurfaceMode,
        setReaderSurfaceMode,
        isUploading,
        isRegenerating,
        regeneratingTarget,
        regenerateStatusText,
        isCanceling,
        deletingDocumentId,
        historyDocuments,
        queueDocuments,
        isLoadingHistory,
        isLoadingQueue,
        historyOpen,
        setHistoryOpen,
        pickedUploadFile,
        setPickedUploadFile,
        uploadPickerKey,
        setUploadPickerKey,
        pendingRun,
        setPendingRun,
        error,
        setError,
        totalPages,
        processedPages,
        pipelineDetailText,
        statusText,
        currentHistoryItem,
        loadDocumentHistory,
        loadQueueDocuments,
        performUpload,
        performExplain,
        performRegenerate,
        onSelectHistoryDocument,
        onDeleteHistoryDocument,
        onClear,
        onCancelTask,
        cancelSpecificTask,
        onPrev,
        onNext,
        onPickUploadFile,
        resetStudyState,
    };
}
