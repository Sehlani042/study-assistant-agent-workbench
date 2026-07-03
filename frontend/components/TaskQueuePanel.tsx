import type { DocumentHistoryItem } from "@/lib/types";
import { formatIsoTime } from "@/lib/utils";

export function TaskQueuePanel({
  items,
  isLoading,
  onRefresh,
  onOpenDocument,
  onCancelTask,
}: {
  items: DocumentHistoryItem[];
  isLoading: boolean;
  onRefresh: () => void;
  onOpenDocument: (documentId: string) => void;
  onCancelTask: (documentId: string) => void;
}) {
  return (
    <>
      <div className="page-header">
        <h2>任务队列</h2>
        <div className="page-header-actions">
          <button onClick={onRefresh} disabled={isLoading}>
            {isLoading ? "刷新中…" : "刷新队列"}
          </button>
        </div>
      </div>

      <div className="page-body">
        {items.length === 0 && !isLoading && (
          <div className="card" style={{ padding: 28, textAlign: "center", color: "var(--text-muted)" }}>
            当前没有排队或运行中的任务。
          </div>
        )}

        {items.length > 0 && (
          <div className="history-grid">
            {items.map((item) => (
              <div key={item.document_id} className="history-card">
                <div className="history-card-head">
                  <div className="history-card-title">{item.original_filename}</div>
                  <span className="badge badge-yellow">{item.stage_label || item.status}</span>
                </div>
                <div className="history-card-meta">
                  <span>
                    {item.job_type === "explain_page" ? "按需解释任务" : "翻译覆盖任务"}
                  </span>
                  <span>{formatIsoTime(item.updated_at)}</span>
                </div>
                <div className="progress-bar-wrap" style={{ marginTop: 8 }}>
                  <div className="progress-bar-fill" style={{ width: `${item.progress.percent}%` }} />
                </div>
                <div className="meta" style={{ marginTop: 4 }}>
                  翻译 {item.translation_ready_pages}/{item.translation_total_pages} 页 · 已解释 {item.explained_pages} 页
                </div>
                <div className="history-card-actions">
                  <button className="history-open-btn" onClick={() => onOpenDocument(item.document_id)}>
                    打开工作台
                  </button>
                  <button onClick={() => onCancelTask(item.document_id)}>停止任务</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
