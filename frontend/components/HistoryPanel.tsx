import { formatIsoTime } from "@/lib/utils";
import type { DocumentHistoryItem, UserPayload } from "@/lib/types";

function statusBadgeClass(s: string) {
    if (s === "completed") return "badge-green";
    if (s === "failed" || s === "canceled") return "badge-red";
    return "badge-yellow";
}

function statusLabel(s: string) {
    if (s === "completed") return "已完成";
    if (s === "processing") return "处理中";
    if (s === "failed") return "失败";
    if (s === "canceled") return "已取消";
    return s;
}

export function HistoryPanel({
    documents,
    currentDocumentId,
    isLoading,
    deletingDocumentId,
    onSelect,
    onDelete,
    onRefresh,
    currentUser,
}: {
    documents: DocumentHistoryItem[];
    currentDocumentId: string | null;
    isLoading: boolean;
    deletingDocumentId: string | null;
    onSelect: (docId: string) => void;
    onDelete: (docId: string) => void;
    onRefresh: () => void;
    currentUser: UserPayload;
}) {
    return (
        <>
            <div className="page-header">
                <h2>历史文档</h2>
                <div className="page-header-actions">
                    <button onClick={onRefresh} disabled={isLoading}>
                        {isLoading ? "刷新中…" : "🔄 刷新"}
                    </button>
                </div>
            </div>

            <div className="page-body">
                {documents.length === 0 && !isLoading && (
                    <div style={{ textAlign: "center", padding: "60px 0", color: "var(--text-muted)" }}>
                        <div style={{ fontSize: "3rem", marginBottom: 12, opacity: 0.3 }}>🗂️</div>
                        <p>还没有上传过文档</p>
                        <p style={{ fontSize: "0.85rem" }}>前往「学习工作台」上传第一份 PDF 或 PPTX</p>
                    </div>
                )}

                {isLoading && documents.length === 0 && (
                    <div style={{ textAlign: "center", padding: "60px 0", color: "var(--text-muted)" }}>加载中…</div>
                )}

                {documents.length > 0 && (
                    <div className="history-grid">
                        {documents.map((doc) => {
                            const translationTotal = doc.translation_total_pages || doc.progress.total_pages;
                            const translationReady = doc.translation_ready_pages || doc.progress.processed_pages;
                            const pct = translationTotal > 0
                                ? Math.round((translationReady / translationTotal) * 100)
                                : 0;
                            const deleting = deletingDocumentId === doc.document_id;

                            return (
                                <div
                                    key={doc.document_id}
                                    className={`history-card ${doc.document_id === currentDocumentId ? "active" : ""}`}
                                    title={doc.original_filename}
                                >
                                    <div className="history-card-head">
                                        <div className="history-card-title">{doc.original_filename}</div>
                                        <button
                                            className="history-delete-btn"
                                            onClick={() => {
                                                if (!window.confirm(`确认删除文档「${doc.original_filename}」吗？`)) return;
                                                onDelete(doc.document_id);
                                            }}
                                            disabled={deleting}
                                            title="删除该文档"
                                        >
                                            {deleting ? "删除中…" : "删除"}
                                        </button>
                                    </div>
                                    <div className="history-card-meta">
                                        <span className={`badge ${statusBadgeClass(doc.status)}`}>
                                            {statusLabel(doc.status)}
                                        </span>
                                        {doc.stage_label && <span>{doc.stage_label}</span>}
                                        <span>{formatIsoTime(doc.updated_at)}</span>
                                    </div>
                                    {doc.progress.total_pages > 0 && (
                                        <>
                                            <div className="progress-bar-wrap" style={{ marginTop: 8 }}>
                                                <div
                                                    className="progress-bar-fill"
                                                    style={{ width: `${pct}%` }}
                                                />
                                            </div>
                                            <div className="meta" style={{ marginTop: 4 }}>
                                                翻译 {translationReady}/{translationTotal} 页 · {pct}% · 讲解 {doc.explained_pages} 页 · 上次看到 P{doc.last_page_no || 1}
                                            </div>
                                        </>
                                    )}
                                    <div className="history-card-actions">
                                        <button
                                            className="history-open-btn"
                                            onClick={() => onSelect(doc.document_id)}
                                        >
                                            打开文档
                                        </button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </>
    );
}
