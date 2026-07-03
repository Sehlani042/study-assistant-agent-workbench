from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
import json
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.learning import build_translation_instruction, normalize_learning_preferences
from app.llm.base import LLMClient
from app.llm.openai_client import OpenAIClient
from app.pipeline.agent_a import run_agent_a
from app.pipeline.agent_b import run_agent_b
from app.pipeline.agent_c import run_agent_c_with_quality, stitch_page_explanation
from app.pipeline.agent_t import run_agent_t_translation, translate_layout_blocks
from app.pipeline.preprocess import ensure_pdf, extract_pages
from app.pipeline.retrieval import select_local_context
from app.services.quality import evaluate_page_explanation
from app.services.store import Store
from app.utils.markdown_math import normalize_math_markdown


class DocumentCancelledError(RuntimeError):
    """Raised when a document task is cancelled by user request."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _elapsed_seconds(start_iso: str | None, end_iso: str | None = None) -> float:
    start_text = str(start_iso or "").strip()
    if not start_text:
        return 0.0
    end_text = str(end_iso or "").strip() or _utc_now_iso()
    try:
        start_dt = datetime.fromisoformat(start_text)
        end_dt = datetime.fromisoformat(end_text)
    except ValueError:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds())


def _running_agent_from_stage(stage_code: str) -> str:
    stage = str(stage_code or "")
    if stage.startswith("preprocess:"):
        return "preprocess"
    if stage.startswith("translate:"):
        return "translate"
    if stage.startswith("agent_a:"):
        return "agent_a"
    if stage.startswith("agent_b:"):
        return "agent_b"
    if stage.startswith("agent_c1:"):
        return "agent_c1"
    if stage.startswith("agent_c2:"):
        return "agent_c2"
    if stage.startswith("quality:"):
        return "quality_gate"
    if stage.startswith("canceled"):
        return "canceled"
    if stage.startswith("completed"):
        return "completed"
    if stage.startswith("failed"):
        return "failed"
    return "idle"


def _default_pipeline_detail(stage_code: str = "queued") -> dict[str, Any]:
    now_iso = _utc_now_iso()
    return {
        "stage_code": stage_code,
        "running_agent": _running_agent_from_stage(stage_code),
        "active_workers": 0,
        "queued_pages": 0,
        "done_pages": 0,
        "failed_pages": 0,
        "retry_pages": 0,
        "current_pages": [],
        "page_status_counts": {},
        "current_page_details": [],
        "failed_page_details": [],
        "repairable_pages": [],
        "stage_started_at": now_iso,
        "total_started_at": now_iso,
        "updated_at": now_iso,
        "stage_elapsed_seconds": 0.0,
        "total_elapsed_seconds": 0.0,
        "c1_timeout_pages": 0,
        "avg_c1_latency_ms": 0.0,
        "p95_c1_latency_ms": 0.0,
        "translation_pending": 0,
        "translation_done": 0,
        "translation_failed": 0,
        "pro_escalation_pages": 0,
        "last_resort_pages": 0,
        "llm_error_counts": {},
        "model_path_counts": {},
        "quality_fail_streak": 0,
        "adaptive_worker_reason": "",
        "coverage_lang_mode": "",
        "last_error": "",
    }


def _adapt_workers_after_quality_fail(
    *,
    target_workers: int,
    min_workers: int,
    quality_failed: bool,
    quality_fail_streak: int,
    trigger_streak: int = 3,
) -> tuple[int, int, bool]:
    next_streak = int(quality_fail_streak)
    if quality_failed:
        next_streak += 1
    else:
        next_streak = 0

    reduced = False
    next_workers = int(target_workers)
    if quality_failed and next_streak >= max(2, int(trigger_streak)) and next_workers > int(min_workers):
        next_workers -= 1
        next_streak = 0
        reduced = True
    return max(1, next_workers), max(0, next_streak), reduced


def _classify_llm_error(reason: str) -> str:
    text = str(reason or "").strip().lower()
    if not text:
        return "unknown"
    if "429" in text or "rate limit" in text or "quota" in text or "resource_exhausted" in text:
        return "rate_limit"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "401" in text or "403" in text or "invalid api key" in text or "forbidden" in text or "unauthorized" in text:
        return "auth"
    if "404" in text or "not found" in text or "no candidate model" in text:
        return "model_unavailable"
    if "connection" in text or "network" in text or "dns" in text:
        return "network"
    if "schema" in text or "json" in text:
        return "schema_parse"
    if "末级兜底失败" in text or "last resort" in text:
        return "last_resort_failed"
    return "unknown"


def _short_error(reason: Any, *, limit: int = 240) -> str:
    text = str(reason or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _llm_task_label(llm_client: LLMClient, task: str) -> str:
    provider = str(getattr(llm_client, "provider_name", "") or "unknown").strip() or "unknown"
    model = str(getattr(llm_client, "model", "") or "").strip()
    clean_task = str(task or "").strip() or "task"
    if model:
        return f"{provider}:{model}:{clean_task}"
    return f"{provider}:{clean_task}"


def _normalize_page_detail_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            page_no = int(item.get("page_no", 0))
        except (TypeError, ValueError):
            continue
        if page_no <= 0:
            continue
        out.append(
            {
                "page_no": page_no,
                "status": str(item.get("status", "unknown")),
                "reason": str(item.get("reason", "")),
                "repairable": bool(item.get("repairable", False)),
                "model_used": str(item.get("model_used", "")),
            }
        )
    out.sort(key=lambda item: int(item["page_no"]))
    return out


class PipelineWorker:
    def __init__(self, *, store: Store, llm_client: LLMClient, settings: Settings) -> None:
        self.store = store
        self.llm_client = llm_client
        self.settings = settings
        self._threads: dict[str, threading.Thread] = {}
        self._translation_threads: dict[str, threading.Thread] = {}
        self._translation_pending_pages: dict[str, set[int]] = {}
        self._pipeline_details: dict[str, dict[str, Any]] = {}
        self._document_llm_overrides: dict[str, LLMClient] = {}
        self._openai_last_resort_clients: dict[tuple[str, str], LLMClient] = {}
        self._openai_vision_clients: dict[tuple[str, str], LLMClient] = {}
        self._lock = threading.RLock()

    def _set_pipeline_detail(self, document_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            current = dict(self._pipeline_details.get(document_id, _default_pipeline_detail()))
            previous_stage = str(current.get("stage_code", "") or "")
            current.update(changes)
            now_iso = _utc_now_iso()
            current_stage = str(current.get("stage_code", "") or "")
            total_started_at = str(current.get("total_started_at", "") or "")
            if not total_started_at:
                total_started_at = now_iso
            current["total_started_at"] = total_started_at

            stage_started_at = str(current.get("stage_started_at", "") or "")
            if not stage_started_at or current_stage != previous_stage:
                stage_started_at = now_iso
            current["stage_started_at"] = stage_started_at
            current["updated_at"] = now_iso
            current["stage_elapsed_seconds"] = round(_elapsed_seconds(stage_started_at, now_iso), 3)
            current["total_elapsed_seconds"] = round(_elapsed_seconds(total_started_at, now_iso), 3)

            current["running_agent"] = _running_agent_from_stage(str(current.get("stage_code", "")))
            current["current_pages"] = sorted({int(x) for x in current.get("current_pages", []) if int(x) > 0})
            for key in (
                "active_workers",
                "queued_pages",
                "done_pages",
                "failed_pages",
                "retry_pages",
                "c1_timeout_pages",
                "translation_pending",
                "translation_done",
                "translation_failed",
                "pro_escalation_pages",
                "last_resort_pages",
                "quality_fail_streak",
            ):
                current[key] = max(0, int(current.get(key, 0)))
            for key in ("avg_c1_latency_ms", "p95_c1_latency_ms", "stage_elapsed_seconds", "total_elapsed_seconds"):
                try:
                    current[key] = round(max(0.0, float(current.get(key, 0.0))), 3)
                except (TypeError, ValueError):
                    current[key] = 0.0
            raw_counts = current.get("page_status_counts", {})
            normalized_counts: dict[str, int] = {}
            if isinstance(raw_counts, dict):
                for key, value in raw_counts.items():
                    normalized_counts[str(key)] = max(0, int(value))
            current["page_status_counts"] = normalized_counts
            raw_llm_error_counts = current.get("llm_error_counts", {})
            normalized_llm_error_counts: dict[str, int] = {}
            if isinstance(raw_llm_error_counts, dict):
                for key, value in raw_llm_error_counts.items():
                    normalized_llm_error_counts[str(key)] = max(0, int(value))
            current["llm_error_counts"] = normalized_llm_error_counts
            raw_model_path_counts = current.get("model_path_counts", {})
            normalized_model_path_counts: dict[str, int] = {}
            if isinstance(raw_model_path_counts, dict):
                for key, value in raw_model_path_counts.items():
                    normalized_model_path_counts[str(key)] = max(0, int(value))
            current["model_path_counts"] = normalized_model_path_counts
            current["current_page_details"] = _normalize_page_detail_list(current.get("current_page_details", []))
            current["failed_page_details"] = _normalize_page_detail_list(current.get("failed_page_details", []))
            current["repairable_pages"] = sorted({int(x) for x in current.get("repairable_pages", []) if int(x) > 0})
            current["adaptive_worker_reason"] = str(current.get("adaptive_worker_reason", "") or "")
            current["coverage_lang_mode"] = str(current.get("coverage_lang_mode", "") or "")
            current["last_error"] = str(current.get("last_error", "") or "")
            self._pipeline_details[document_id] = current
            return dict(current)

    def get_pipeline_detail(self, *, document_id: str, stage_code: str, done_pages: int, total_pages: int) -> dict[str, Any]:
        fallback = _default_pipeline_detail(stage_code)
        fallback["done_pages"] = max(0, int(done_pages))
        fallback["queued_pages"] = max(0, int(total_pages) - int(done_pages))
        with self._lock:
            detail = self._pipeline_details.get(document_id)
            if detail is None:
                return fallback
            out = dict(detail)
        out["stage_code"] = str(out.get("stage_code") or stage_code or "queued")
        out["running_agent"] = _running_agent_from_stage(out["stage_code"])
        out["done_pages"] = max(0, int(out.get("done_pages", done_pages)))
        out["queued_pages"] = max(0, int(out.get("queued_pages", max(0, total_pages - out["done_pages"]))))
        out["current_pages"] = sorted({int(x) for x in out.get("current_pages", []) if int(x) > 0})
        now_iso = _utc_now_iso()
        out["stage_started_at"] = str(out.get("stage_started_at", "") or "")
        out["total_started_at"] = str(out.get("total_started_at", "") or out["stage_started_at"] or now_iso)
        out["updated_at"] = str(out.get("updated_at", "") or now_iso)
        out["stage_elapsed_seconds"] = round(_elapsed_seconds(out["stage_started_at"], now_iso), 3)
        out["total_elapsed_seconds"] = round(_elapsed_seconds(out["total_started_at"], now_iso), 3)

        raw_counts = out.get("page_status_counts", {})
        if isinstance(raw_counts, dict):
            out["page_status_counts"] = {str(k): max(0, int(v)) for k, v in raw_counts.items()}
        else:
            out["page_status_counts"] = {}
        out["current_page_details"] = _normalize_page_detail_list(out.get("current_page_details", []))
        out["failed_page_details"] = _normalize_page_detail_list(out.get("failed_page_details", []))
        out["repairable_pages"] = sorted({int(x) for x in out.get("repairable_pages", []) if int(x) > 0})
        for key in ("c1_timeout_pages", "translation_pending", "translation_done", "translation_failed", "quality_fail_streak"):
            out[key] = max(0, int(out.get(key, 0)))
        for key in ("pro_escalation_pages", "last_resort_pages"):
            out[key] = max(0, int(out.get(key, 0)))
        for key in ("avg_c1_latency_ms", "p95_c1_latency_ms"):
            try:
                out[key] = round(max(0.0, float(out.get(key, 0.0))), 3)
            except (TypeError, ValueError):
                out[key] = 0.0
        raw_llm_error_counts = out.get("llm_error_counts", {})
        if isinstance(raw_llm_error_counts, dict):
            out["llm_error_counts"] = {str(k): max(0, int(v)) for k, v in raw_llm_error_counts.items()}
        else:
            out["llm_error_counts"] = {}
        raw_model_path_counts = out.get("model_path_counts", {})
        if isinstance(raw_model_path_counts, dict):
            out["model_path_counts"] = {str(k): max(0, int(v)) for k, v in raw_model_path_counts.items()}
        else:
            out["model_path_counts"] = {}
        out["adaptive_worker_reason"] = str(out.get("adaptive_worker_reason", "") or "")
        out["coverage_lang_mode"] = str(out.get("coverage_lang_mode", "") or "")
        out["last_error"] = str(out.get("last_error", "") or "")
        return out

    def recover_incomplete_documents(self) -> None:
        processing_docs = self.store.list_processing_documents()
        for doc in processing_docs:
            document_id = str(doc.get("id", "")).strip()
            if not document_id:
                continue
            job = self.store.get_job_by_document(document_id)
            if job is None:
                job_id = str(uuid4())
                self.store.create_job(job_id, document_id, stage="queued")
                self.enqueue_document(document_id=document_id, job_id=job_id)
                continue

            job_id = str(job.get("id", "")).strip()
            if not job_id:
                continue
            self.store.update_job(job_id, status="processing", error=None)
            self.enqueue_document(document_id=document_id, job_id=job_id)

    def enqueue_document(self, *, document_id: str, job_id: str, llm_client: LLMClient | None = None) -> None:
        with self._lock:
            if document_id in self._threads and self._threads[document_id].is_alive():
                return
            if llm_client is not None:
                self._document_llm_overrides[document_id] = llm_client
            self._set_pipeline_detail(document_id, stage_code="queued")
            thread = threading.Thread(
                target=self._safe_process,
                kwargs={"document_id": document_id, "job_id": job_id},
                daemon=True,
            )
            self._threads[document_id] = thread
            thread.start()

    def _llm_client_for_document(self, document_id: str) -> LLMClient:
        with self._lock:
            return self._document_llm_overrides.get(document_id, self.llm_client)

    def _default_fallback_chain(self) -> list[str]:
        model = str(getattr(self.settings, "openai_last_resort_model", "") or self.settings.openai_model or "gpt-5.2-mini").strip()
        return ["gemini:flash", "gemini:pro", f"openai:{model}"]

    def _resolve_document_fallback_chain(self, *, document_id: str) -> list[str]:
        defaults = self._default_fallback_chain()
        global_chain = self.store.get_global_fallback_chain(default_chain=defaults)
        doc = self.store.get_document(document_id) or {}
        owner_user_id = str(doc.get("owner_user_id", "")).strip()
        user_chain = self.store.get_user_fallback_chain(user_id=owner_user_id) if owner_user_id else []
        task_chain = self.store.get_document_fallback_chain(document_id=document_id)
        return list(task_chain or user_chain or global_chain or defaults)

    def _resolve_openai_last_resort_model(self, *, document_id: str) -> str | None:
        chain = self._resolve_document_fallback_chain(document_id=document_id)
        for step in chain:
            text = str(step or "").strip().lower()
            if not text.startswith("openai:"):
                continue
            model = text.split(":", 1)[1].strip()
            if not model:
                continue
            if model in {"mini", "last_resort", "last-resort"}:
                return str(getattr(self.settings, "openai_last_resort_model", "") or self.settings.openai_model).strip()
            if model in {"default", "primary"}:
                return str(self.settings.openai_model or "").strip()
            return model
        return None

    def _get_openai_last_resort_client(
        self,
        *,
        document_id: str,
        model_override: str | None = None,
        primary_llm_client: LLMClient | None = None,
    ) -> LLMClient | None:
        model = str(model_override or "").strip() or str(getattr(self.settings, "openai_last_resort_model", "") or "").strip()
        if not model:
            return None

        api_key = ""
        if primary_llm_client is not None and str(getattr(primary_llm_client, "provider_name", "")).strip() == "openai":
            api_key = str(getattr(primary_llm_client, "api_key", "") or "").strip()
        if not api_key:
            doc = self.store.get_document(document_id) or {}
            owner_user_id = str(doc.get("owner_user_id", "")).strip()
            if owner_user_id:
                api_key = str(
                    self.store.get_user_personal_llm_key(
                        user_id=owner_user_id,
                        provider="openai",
                        encryption_secret=self.settings.llm_key_encryption_secret,
                    )
                    or ""
                ).strip()
        if not api_key:
            api_key = str(self.settings.openai_api_key or "").strip()
        if not api_key:
            return None

        cache_key = (api_key[-10:], model)
        with self._lock:
            cached = self._openai_last_resort_clients.get(cache_key)
            if cached is not None:
                return cached
            created = OpenAIClient(
                api_key=api_key,
                model=model,
                base_url=self.settings.openai_base_url,
            )
            self._openai_last_resort_clients[cache_key] = created
            return created

    def _get_openai_vision_client(
        self,
        *,
        document_id: str,
        primary_llm_client: LLMClient | None = None,
    ) -> LLMClient | None:
        model = str(getattr(self.settings, "openai_vision_model", "") or "").strip()
        if not model:
            return None

        api_key = ""
        if primary_llm_client is not None and str(getattr(primary_llm_client, "provider_name", "")).strip() == "openai":
            api_key = str(getattr(primary_llm_client, "api_key", "") or "").strip()
        if not api_key:
            doc = self.store.get_document(document_id) or {}
            owner_user_id = str(doc.get("owner_user_id", "")).strip()
            if owner_user_id:
                api_key = str(
                    self.store.get_user_personal_llm_key(
                        user_id=owner_user_id,
                        provider="openai",
                        encryption_secret=self.settings.llm_key_encryption_secret,
                    )
                    or ""
                ).strip()
        if not api_key:
            api_key = str(self.settings.openai_api_key or "").strip()
        if not api_key:
            return None

        cache_key = (api_key[-10:], model)
        with self._lock:
            cached = self._openai_vision_clients.get(cache_key)
            if cached is not None:
                return cached
            created = OpenAIClient(
                api_key=api_key,
                model=model,
                base_url=self.settings.openai_base_url,
            )
            self._openai_vision_clients[cache_key] = created
            return created

    def _safe_process(self, *, document_id: str, job_id: str) -> None:
        run_id = str((self.store.get_document(document_id) or {}).get("latest_run_id", "")).strip() or None
        try:
            if run_id:
                self.store.update_document_run(run_id, status="processing", job_id=job_id)
            self.process_document(document_id=document_id, job_id=job_id)
        except DocumentCancelledError as exc:
            self.store.update_job(job_id, status="canceled", stage="canceled", error=str(exc))
            self.store.update_document_status(document_id, status="canceled", error=str(exc))
            self._set_pipeline_detail(
                document_id,
                stage_code="canceled",
                active_workers=0,
                current_pages=[],
                last_error=str(exc),
            )
            if run_id:
                self.store.update_document_run(run_id, status="canceled", error=str(exc), finished_at=_utc_now_iso(), job_id=job_id)
                self._persist_run_artifacts(
                    document_id=document_id,
                    run_id=run_id,
                    pipeline_report=self.get_pipeline_detail(
                        document_id=document_id,
                        stage_code="canceled",
                        done_pages=int((self.store.get_document(document_id) or {}).get("processed_pages", 0) or 0),
                        total_pages=int((self.store.get_document(document_id) or {}).get("total_pages", 0) or 0),
                    ),
                )
        except Exception as exc:
            self.store.update_job(job_id, status="failed", stage="failed", error=str(exc))
            self.store.update_document_status(document_id, status="failed", error=str(exc))
            self._set_pipeline_detail(
                document_id,
                stage_code="failed",
                active_workers=0,
                current_pages=[],
                last_error=str(exc),
            )
            if run_id:
                self.store.update_document_run(run_id, status="failed", error=str(exc), finished_at=_utc_now_iso(), job_id=job_id)
                self._persist_run_artifacts(
                    document_id=document_id,
                    run_id=run_id,
                    pipeline_report=self.get_pipeline_detail(
                        document_id=document_id,
                        stage_code="failed",
                        done_pages=int((self.store.get_document(document_id) or {}).get("processed_pages", 0) or 0),
                        total_pages=int((self.store.get_document(document_id) or {}).get("total_pages", 0) or 0),
                    ),
                )
        finally:
            with self._lock:
                self._document_llm_overrides.pop(document_id, None)

    def _document_dir(self, document_id: str) -> Path:
        return self.settings.data_dir / "documents" / document_id

    def _run_dir(self, document_id: str, run_id: str) -> Path:
        return self._document_dir(document_id) / "runs" / run_id

    def _persist_run_artifacts(
        self,
        *,
        document_id: str,
        run_id: str,
        pipeline_report: dict[str, Any] | None = None,
        page_payloads: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        run = self.store.get_document_run(run_id)
        if run is None:
            return
        run_dir = self._run_dir(document_id, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run_meta.json").write_text(
            json.dumps(run, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if pipeline_report is not None:
            (run_dir / "pipeline_report.json").write_text(
                json.dumps(pipeline_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if page_payloads:
            pages_dir = run_dir / "pages"
            pages_dir.mkdir(parents=True, exist_ok=True)
            for page_no, payload in sorted(page_payloads.items()):
                (pages_dir / f"{int(page_no):04d}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    def _enqueue_translation_backfill(
        self,
        *,
        document_id: str,
        page_nos: list[int],
        llm_client: LLMClient,
        language: str,
        instruction: str | None = None,
    ) -> None:
        candidates = sorted({int(page_no) for page_no in page_nos if int(page_no) > 0})
        if not candidates:
            return

        with self._lock:
            pending = self._translation_pending_pages.setdefault(document_id, set())
            pending.update(candidates)
            pending_count = len(pending)
            thread = self._translation_threads.get(document_id)
            should_start = not (thread and thread.is_alive())
            if should_start:
                next_thread = threading.Thread(
                    target=self._run_translation_backfill_loop,
                    kwargs={
                        "document_id": document_id,
                        "llm_client": llm_client,
                        "language": language,
                        "instruction": instruction,
                    },
                    daemon=True,
                )
                self._translation_threads[document_id] = next_thread
            else:
                next_thread = None

        self._set_pipeline_detail(document_id, translation_pending=pending_count)
        if next_thread is not None:
            next_thread.start()

    def _run_translation_backfill_loop(
        self,
        *,
        document_id: str,
        llm_client: LLMClient,
        language: str,
        instruction: str | None = None,
    ) -> None:
        worker_count = max(1, int(getattr(self.settings, "agent_t_concurrency", 3)))
        while True:
            with self._lock:
                pending = self._translation_pending_pages.get(document_id)
                if not pending:
                    self._translation_pending_pages.pop(document_id, None)
                    self._translation_threads.pop(document_id, None)
                    break
                batch = sorted(pending)[:worker_count]
                for page_no in batch:
                    pending.remove(page_no)
                remaining = len(pending)

            self._set_pipeline_detail(document_id, translation_pending=remaining)
            with ThreadPoolExecutor(max_workers=max(1, len(batch)), thread_name_prefix="agent-t") as executor:
                future_to_page = {
                    executor.submit(
                        self._translate_page_literal,
                        document_id=document_id,
                        page_no=page_no,
                        language=language,
                        llm_client=llm_client,
                        instruction=instruction,
                    ): int(page_no)
                    for page_no in batch
                }
                for future, page_no in future_to_page.items():
                    try:
                        updated = future.result()
                        if updated:
                            with self._lock:
                                current = self._pipeline_details.get(document_id, _default_pipeline_detail())
                                done = int(current.get("translation_done", 0)) + 1
                            self._set_pipeline_detail(document_id, translation_done=done)
                    except Exception as exc:
                        with self._lock:
                            current = self._pipeline_details.get(document_id, _default_pipeline_detail())
                            failed = int(current.get("translation_failed", 0)) + 1
                        self._set_pipeline_detail(
                            document_id,
                            translation_failed=failed,
                            last_error=f"Agent T page {page_no} failed: {exc}",
                        )

    def _translate_page_literal(
        self,
        *,
        document_id: str,
        page_no: int,
        language: str,
        llm_client: LLMClient,
        instruction: str | None = None,
    ) -> bool:
        page = self.store.get_page(document_id, page_no)
        if page is None:
            return False

        explanation_row = self.store.get_latest_explanation(document_id, page_no, language)
        if explanation_row is None:
            return False

        payload = dict(explanation_row.get("payload") or {})
        current_status = str(payload.get("translationStatus", "")).strip().lower()
        if current_status == "ready" and str(payload.get("literalTranslation", "")).strip():
            return False

        try:
            translated = run_agent_t_translation(
                llm_client=llm_client,
                page_text=str(page.get("text_content", "")),
                language=language,
                instruction=instruction,
            )
            if not translated.strip():
                raise RuntimeError("empty translation")

            payload["literalTranslation"] = normalize_math_markdown(translated.strip())
            payload["translationStatus"] = "ready"
            payload["translationError"] = ""
            payload["translationUpdatedAt"] = _utc_now_iso()
            self.store.update_explanation_payload(
                document_id=document_id,
                page_no=page_no,
                language=language,
                version=int(explanation_row.get("version", 1)),
                payload=payload,
            )
            return True
        except Exception as exc:
            payload["translationStatus"] = "failed"
            payload["translationError"] = str(exc)[:240]
            payload["translationUpdatedAt"] = _utc_now_iso()
            self.store.update_explanation_payload(
                document_id=document_id,
                page_no=page_no,
                language=language,
                version=int(explanation_row.get("version", 1)),
                payload=payload,
            )
            raise

    def _is_cancelled(self, *, document_id: str, job_id: str) -> bool:
        doc = self.store.get_document(document_id)
        if doc is None:
            return True
        if str(doc.get("status", "")).strip().lower() == "canceled":
            return True
        job = self.store.get_job(job_id)
        if job is None:
            return False
        return str(job.get("status", "")).strip().lower() == "canceled"

    def _raise_if_cancelled(self, *, document_id: str, job_id: str) -> None:
        if self._is_cancelled(document_id=document_id, job_id=job_id):
            raise DocumentCancelledError("任务已取消")

    def cancel_document(self, *, document_id: str) -> bool:
        doc = self.store.get_document(document_id)
        if doc is None:
            return False

        status = str(doc.get("status", "")).strip().lower()
        if status in {"completed", "failed"}:
            return False
        if status == "canceled":
            return True

        job = self.store.get_job_by_document(document_id)
        if job is not None:
            self.store.update_job(
                str(job.get("id", "")),
                status="canceled",
                stage="canceled",
                error="任务已取消",
            )
        self.store.update_document_status(
            document_id,
            status="canceled",
            error="任务已取消",
        )
        self._set_pipeline_detail(
            document_id,
            stage_code="canceled",
            active_workers=0,
            current_pages=[],
            last_error="任务已取消",
        )
        latest_run_id = str(doc.get("latest_run_id", "") or "").strip()
        if latest_run_id:
            self.store.update_document_run(latest_run_id, status="canceled", error="任务已取消", finished_at=_utc_now_iso())
            self._persist_run_artifacts(
                document_id=document_id,
                run_id=latest_run_id,
                pipeline_report=self.get_pipeline_detail(
                    document_id=document_id,
                    stage_code="canceled",
                    done_pages=int((self.store.get_document(document_id) or {}).get("processed_pages", 0) or 0),
                    total_pages=int((self.store.get_document(document_id) or {}).get("total_pages", 0) or 0),
                ),
            )
        return True

    def _group_for_page(self, *, groups: list[dict], page_no: int) -> dict:
        for group in groups:
            if group["page_start"] <= page_no <= group["page_end"]:
                return group
        return groups[-1] if groups else {"id": "", "summary": ""}

    def _degraded_page_output(
        self,
        *,
        page: dict,
        group: dict,
        global_memory: dict,
        local_context: list[dict],
        reason: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        page_no = int(page["page_no"])
        summary = str(global_memory.get("summary", "")).strip()
        page_text = str(page.get("text_content", "")).strip()
        key_points = [line.strip() for line in page_text.splitlines() if line.strip()][:3]
        if not key_points:
            key_points = ["该页解释生成失败，已自动降级为兜底说明。"]
        local_pages = sorted({int(item.get("page_no", page_no)) for item in local_context if item.get("page_no")})
        scope_pages = sorted({page_no, *[x for x in local_pages if x > 0]})
        payload = {
            "overview": f"第 {page_no} 页在自动解释时出现异常，已提供降级说明。{summary[:120]}",
            "literalTranslation": "\n".join([line.strip() for line in page_text.splitlines() if line.strip()][:10]),
            "translationStatus": "pending",
            "translationUpdatedAt": "",
            "translationError": "",
            "keyPoints": key_points,
            "conceptLinks": [],
            "formulaBlocks": [],
            "citations": [],
            "confidence": 0.3,
            "qualityNotice": reason,
            "statusHint": "该页已降级输出，建议稍后重生成。",
            "teaching": {
                "definition": "该页解释生成失败。",
                "intuition": "可先阅读左侧原文档，稍后重试本页重生成。",
                "example": "",
                "focus": "建议优先关注本页标题与关键条目。",
                "pitfall": "当前结果为降级输出，可能不完整。",
            },
            "scaffold": {
                "quick30": ["先看左侧标题和第一条要点。"],
                "understand2m": ["回看本页原文，找 2 个关键词。"],
                "master5m": ["写下你对本页内容的 1 句话复述。"],
            },
            "continuity": {
                "prevBridge": "承接上一页：请先回顾上一页核心句。",
                "thisPageNew": "本页新增：需要手动阅读原文定位关键信息。",
                "nextPreview": "下一页预告：继续围绕本主题展开。",
            },
            "microTask": {
                "doNow": "现在先在左侧页面圈出 2 个关键词。",
                "checkQuestion": "这页到底在定义概念、推公式，还是给例子？",
                "answerHint": "看标题和第一条项目符号通常最关键。",
            },
            "clarity": {
                "conclusion": key_points[0],
                "steps": key_points[:3],
                "example": key_points[1] if len(key_points) > 1 else key_points[0],
            },
            "evidenceBlocks": [],
            "scopePages": scope_pages,
            "memoryUsed": {
                "globalVersion": str(global_memory.get("version", "v1")),
                "groupId": str(group.get("id", "")),
                "localPages": local_pages,
            },
            "version": 1,
        }
        quality = {
            "score": 0.0,
            "coverage": 0.0,
            "citationScore": 0.0,
            "formulaRenderRate": 0.0,
            "terminologyConsistency": 0.0,
            "continuityScore": 0.0,
            "specificityScore": 0.0,
            "actionabilityScore": 0.0,
            "hardFailed": True,
            "pass": False,
            "feedback": [reason],
        }
        payload["quality"] = quality
        return payload, quality, "agent-c-error-fallback"

    def _run_agent_c_for_page(
        self,
        *,
        llm_client: LLMClient,
        page: dict,
        groups: list[dict],
        all_pages: list[dict],
        global_memory: dict,
        language: str,
        page_explain_instruction: str | None = None,
        page_budget_seconds: float | None = None,
        document_id: str,
        job_id: str,
    ) -> tuple[int, dict, dict, str]:
        self._raise_if_cancelled(document_id=document_id, job_id=job_id)
        group = self._group_for_page(groups=groups, page_no=int(page["page_no"]))
        local_context = select_local_context(current_page=page, all_pages=all_pages, top_k=4)
        page_no = int(page["page_no"])
        primary_provider = str(getattr(llm_client, "provider_name", "unknown") or "unknown")
        openai_last_resort_model = self._resolve_openai_last_resort_model(document_id=document_id)

        def _try_openai_last_resort(
            *,
            trigger: str,
            primary_error: str,
            min_keep_score: float | None = None,
        ) -> tuple[tuple[int, dict, dict, str] | None, str | None]:
            if primary_provider == "openai":
                return None, None
            if not openai_last_resort_model:
                return None, None
            try:
                last_resort = self._get_openai_last_resort_client(
                    document_id=document_id,
                    model_override=openai_last_resort_model,
                    primary_llm_client=llm_client,
                )
            except Exception as exc:
                return None, _short_error(exc)
            if last_resort is None:
                return None, None
            try:
                payload, quality, _ = run_agent_c_with_quality(
                    llm_client=last_resort,
                    page=page,
                    global_memory=global_memory,
                    group=group,
                    local_context=local_context,
                    language=language,
                    quality_threshold=self.settings.quality_threshold,
                    instruction=page_explain_instruction,
                    page_budget_seconds=page_budget_seconds,
                )
                if min_keep_score is not None:
                    score = float(quality.get("score", 0.0))
                    passed = bool(quality.get("pass", False))
                    if (not passed) and score < float(min_keep_score):
                        return None, None
                payload["quality"] = quality
                payload["version"] = 1
                payload["statusHint"] = (
                    f"主模型结果不稳定，已自动切换末级兜底模型 {openai_last_resort_model}。"
                )
                payload["fallbackTrace"] = {
                    "trigger": trigger,
                    "primaryProvider": primary_provider,
                    "lastResortProvider": "openai",
                    "lastResortModel": openai_last_resort_model,
                    "primaryError": primary_error,
                }
                return (
                    page_no,
                    payload,
                    quality,
                    f"openai-last-resort:{openai_last_resort_model}",
                ), None
            except Exception as exc:
                return None, _short_error(exc)

        try:
            payload, quality, model_used = run_agent_c_with_quality(
                llm_client=llm_client,
                page=page,
                global_memory=global_memory,
                group=group,
                local_context=local_context,
                language=language,
                quality_threshold=self.settings.quality_threshold,
                instruction=page_explain_instruction,
                page_budget_seconds=page_budget_seconds,
            )
            if not bool(quality.get("pass", False)):
                feedback = " ".join(str(item) for item in (quality.get("feedback", []) or []) if str(item).strip())
                rescue_result, _ = _try_openai_last_resort(
                    trigger="quality_low",
                    primary_error=feedback or "quality_not_passed",
                    min_keep_score=float(quality.get("score", 0.0)),
                )
                if rescue_result is not None:
                    return rescue_result
            payload["quality"] = quality
            payload["version"] = 1
            return page_no, payload, quality, model_used
        except DocumentCancelledError:
            raise
        except Exception as exc:
            primary_error = _short_error(exc)
            rescue_result, rescue_error = _try_openai_last_resort(
                trigger="primary_error",
                primary_error=primary_error,
                min_keep_score=None,
            )
            if rescue_result is not None:
                return rescue_result
            if rescue_error:
                payload, quality, model_used = self._degraded_page_output(
                    page=page,
                    group=group,
                    global_memory=global_memory,
                    local_context=local_context,
                    reason=(
                        f"自动解释失败：{primary_error}；"
                        f"末级兜底失败（{openai_last_resort_model or 'openai'}）：{rescue_error}"
                    ),
                )
                return page_no, payload, quality, model_used
            payload, quality, model_used = self._degraded_page_output(
                page=page,
                group=group,
                global_memory=global_memory,
                local_context=local_context,
                reason=f"自动解释失败：{primary_error}",
            )
            return page_no, payload, quality, model_used

    def _sanitize_chat_answer(self, *, answer: dict[str, Any], allowed_pages: set[int]) -> dict[str, Any]:
        normalized_answer = normalize_math_markdown(str(answer.get("answer", "")))
        related_context_raw = answer.get("relatedContext", [])
        related_context = related_context_raw if isinstance(related_context_raw, list) else []

        citations_raw = answer.get("citations", [])
        citations: list[dict[str, Any]] = []
        if isinstance(citations_raw, list):
            for item in citations_raw:
                if not isinstance(item, dict):
                    continue
                try:
                    page_no = int(item.get("pageNo", 0))
                except (TypeError, ValueError):
                    continue
                if page_no not in allowed_pages:
                    continue
                citations.append(
                    {
                        "pageNo": page_no,
                        "span": str(item.get("span", "")),
                        "quote": str(item.get("quote", "")),
                    }
                )

        scope_pages = sorted({int(x) for x in allowed_pages if int(x) > 0})
        return {
            "answer": normalized_answer,
            "citations": citations,
            "relatedContext": [normalize_math_markdown(str(item)) for item in related_context[:10]],
            "scopePages": scope_pages,
        }

    def _translate_page_overlay(
        self,
        *,
        page: dict[str, Any],
        llm_client: LLMClient,
        language: str,
        instruction: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str]:
        layout_blocks = list(page.get("layout_blocks", []) or [])
        if layout_blocks:
            return translate_layout_blocks(
                llm_client=llm_client,
                layout_blocks=layout_blocks,
                language=language,
                instruction=instruction,
            )
        literal_translation = run_agent_t_translation(
            llm_client=llm_client,
            page_text=str(page.get("text_content", "")),
            language=language,
            instruction=instruction,
        )
        overlay_status = "ready" if literal_translation.strip() else "unavailable"
        return [], [], literal_translation, overlay_status

    def _ensure_document_memory(
        self,
        *,
        document_id: str,
        llm_client: LLMClient,
        prompt_config: dict[str, str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        global_memory = self.store.get_global_memory(document_id)
        groups = self.store.list_groups(document_id)
        pages = self.store.list_pages(document_id)
        has_group_summaries = bool(groups) and all(str(group.get("summary", "")).strip() for group in groups)
        if global_memory is not None and groups and has_group_summaries:
            return global_memory, groups, pages

        agent_a = run_agent_a(
            llm_client=llm_client,
            pages=pages,
            instruction=prompt_config.get("agent_a_instruction"),
        )
        self.store.upsert_global_memory(
            document_id=document_id,
            summary=agent_a["summary"],
            keywords=agent_a["keywords"],
            glossary=agent_a.get("glossary", []),
            knowledge_map=agent_a.get("knowledge_map", []),
            learning_arc=agent_a.get("learning_arc", []),
            version=1,
        )
        self.store.replace_groups(document_id, agent_a["groups"])
        for group in agent_a["groups"]:
            self.store.assign_pages_to_group(
                document_id,
                group_id=group["id"],
                page_start=group["page_start"],
                page_end=group["page_end"],
            )

        groups = self.store.list_groups(document_id)
        detailed_groups = run_agent_b(
            llm_client=llm_client,
            document_summary=agent_a["summary"],
            groups=groups,
            pages=pages,
            instruction=prompt_config.get("agent_b_instruction"),
            concurrency=max(1, min(int(getattr(self.settings, "agent_b_concurrency", 1)), len(groups) or 1)),
        )
        for group in detailed_groups:
            self.store.update_group_details(
                group_id=group["id"],
                summary=group["summary"],
                key_concepts=group["key_concepts"],
                prerequisites=group["prerequisites"],
                misconceptions=group["misconceptions"],
            )
        return (self.store.get_global_memory(document_id) or {"summary": "", "keywords": [], "version": 1}), self.store.list_groups(document_id), pages

    def process_document(self, *, document_id: str, job_id: str) -> None:
        doc = self.store.get_document(document_id)
        if doc is None:
            raise ValueError(f"document {document_id} does not exist")
        if str(doc.get("status", "")).strip().lower() == "canceled":
            raise DocumentCancelledError("任务已取消")
        prompt_config = self.store.get_document_prompt_config(document_id)
        llm_client = self._llm_client_for_document(document_id)
        learning_profile = self.store.get_document_learning_profile(document_id)
        translation_instruction = build_translation_instruction(learning_profile)

        self._raise_if_cancelled(document_id=document_id, job_id=job_id)
        self.store.update_job(job_id, status="processing", stage="preprocess:convert", error=None)
        self.store.update_document_status(document_id, status="processing", error=None)
        self._set_pipeline_detail(document_id, stage_code="preprocess:convert")

        source_path = Path(doc["source_path"])
        doc_dir = self._document_dir(document_id)
        doc_dir.mkdir(parents=True, exist_ok=True)

        normalized_pdf_path = ensure_pdf(source_path, doc["source_type"], doc_dir)
        self._raise_if_cancelled(document_id=document_id, job_id=job_id)
        self.store.update_job(job_id, stage="preprocess:extract")
        self._set_pipeline_detail(document_id, stage_code="preprocess:extract")
        source_type = str(doc.get("source_type", "") or "").strip().lower()
        vision_client = (
            self._get_openai_vision_client(document_id=document_id, primary_llm_client=llm_client)
            if source_type == "pptx"
            else None
        )
        pages = extract_pages(
            normalized_pdf_path,
            doc_dir,
            llm_client,
            formula_instruction=prompt_config.get("formula_instruction"),
            source_type=source_type,
            vision_client=vision_client,
            vision_instruction=prompt_config.get("formula_instruction"),
        )
        vision_augmented_pages = sum(
            1
            for page in pages
            if any(str(block.get("source", "")) == "openai_vision" for block in list(page.get("layout_blocks", []) or []))
        )
        self._raise_if_cancelled(document_id=document_id, job_id=job_id)
        self.store.update_job(job_id, stage="preprocess:index")
        model_path_counts = {"openai_vision": vision_augmented_pages} if vision_augmented_pages else {}
        self._set_pipeline_detail(document_id, stage_code="preprocess:index", model_path_counts=model_path_counts)
        for page in pages:
            self._raise_if_cancelled(document_id=document_id, job_id=job_id)
            self.store.upsert_page(
                document_id=document_id,
                page_no=page["page_no"],
                text_content=page["text_content"],
                formulas=page["formulas"],
                image_path=page["image_path"],
                embedding=page["embedding"],
                page_width=float(page.get("page_width", 0.0) or 0.0),
                page_height=float(page.get("page_height", 0.0) or 0.0),
                layout_blocks=list(page.get("layout_blocks", []) or []),
                translation_blocks=[],
                untranslated_blocks=[],
                literal_translation="",
                translation_overlay_status="pending",
            )

        self.store.update_document_status(
            document_id,
            pdf_path=str(normalized_pdf_path),
            total_pages=len(pages),
            processed_pages=0,
        )

        total_pages = len(pages)
        translated_pages = 0
        partial_pages = 0
        unavailable_pages = 0
        page_status_counts: dict[str, int] = {"pending": total_pages, "translated": 0, "partial": 0, "unavailable": 0}
        current_page_details: list[dict[str, Any]] = []
        page_payloads: dict[int, dict[str, Any]] = {}
        worker_count = max(1, min(int(getattr(self.settings, "agent_t_concurrency", 3)), total_pages or 1))
        stage_code = f"translate:blocks:start:w{worker_count}"
        self.store.update_job(job_id, stage=stage_code)
        self._set_pipeline_detail(
            document_id,
            stage_code=stage_code,
            active_workers=worker_count,
            queued_pages=total_pages,
            done_pages=0,
            translation_pending=total_pages,
            translation_done=0,
            translation_failed=0,
            page_status_counts=page_status_counts,
            current_page_details=[],
        )

        all_pages = self.store.list_pages(document_id)
        for page in all_pages:
            self._raise_if_cancelled(document_id=document_id, job_id=job_id)
            page_no = int(page["page_no"])
            translation_blocks, untranslated_blocks, literal_translation, overlay_status = self._translate_page_overlay(
                page=page,
                llm_client=llm_client,
                language="zh",
                instruction=translation_instruction,
            )
            self.store.update_page_translation(
                document_id=document_id,
                page_no=page_no,
                translation_blocks=translation_blocks,
                untranslated_blocks=untranslated_blocks,
                literal_translation=literal_translation,
                translation_overlay_status=overlay_status,
                translation_updated_at=_utc_now_iso(),
            )
            translated_pages += 1
            page_status_counts["pending"] = max(0, total_pages - translated_pages)
            if overlay_status == "partial":
                partial_pages += 1
                page_status_counts["partial"] = partial_pages
            elif overlay_status == "unavailable":
                unavailable_pages += 1
                page_status_counts["unavailable"] = unavailable_pages
            else:
                page_status_counts["translated"] = page_status_counts.get("translated", 0) + 1

            current_page_details = [
                {
                    "page_no": page_no,
                    "status": overlay_status,
                    "reason": "" if overlay_status == "ready" else ("部分块保留原文" if overlay_status == "partial" else "当前页无法可靠覆盖翻译"),
                    "repairable": overlay_status in {"partial", "unavailable"},
                    "model_used": f"{getattr(llm_client, 'provider_name', 'unknown')}:translate",
                }
            ]
            stage_code = f"translate:blocks:{translated_pages}/{total_pages}:w{worker_count}"
            self.store.update_job(job_id, stage=stage_code)
            self.store.update_document_status(document_id, processed_pages=translated_pages)
            self._set_pipeline_detail(
                document_id,
                stage_code=stage_code,
                active_workers=min(worker_count, max(1, total_pages - translated_pages + 1)),
                queued_pages=max(0, total_pages - translated_pages),
                done_pages=translated_pages,
                translation_pending=max(0, total_pages - translated_pages),
                translation_done=translated_pages,
                translation_failed=unavailable_pages,
                page_status_counts=page_status_counts,
                current_pages=[page_no],
                current_page_details=current_page_details,
            )
            page_payloads[page_no] = {
                "page_no": page_no,
                "translation_overlay_status": overlay_status,
                "translation_blocks": translation_blocks,
                "untranslated_blocks": untranslated_blocks,
                "literal_translation": literal_translation,
            }

        self._raise_if_cancelled(document_id=document_id, job_id=job_id)
        self.store.update_job(job_id, stage="translate:overlay")
        self._set_pipeline_detail(
            document_id,
            stage_code="translate:overlay",
            active_workers=0,
            queued_pages=0,
            done_pages=translated_pages,
            translation_pending=0,
            translation_done=translated_pages,
            translation_failed=unavailable_pages,
            page_status_counts=page_status_counts,
            current_pages=[],
            current_page_details=[],
        )

        self.store.update_document_status(document_id, status="completed", processed_pages=translated_pages, error=None)
        self.store.update_job(job_id, status="completed", stage="completed", error=None)
        self._set_pipeline_detail(
            document_id,
            stage_code="completed",
            active_workers=0,
            queued_pages=0,
            done_pages=translated_pages,
            translation_pending=0,
            translation_done=translated_pages,
            translation_failed=unavailable_pages,
            page_status_counts=page_status_counts,
            current_pages=[],
            current_page_details=[],
        )

        latest_run_id = str((self.store.get_document(document_id) or {}).get("latest_run_id", "")).strip()
        if latest_run_id:
            detail = self.get_pipeline_detail(
                document_id=document_id,
                stage_code="completed",
                done_pages=translated_pages,
                total_pages=total_pages,
            )
            explained_pages = len(self.store.list_explained_page_nos(document_id, "zh"))
            self.store.update_document_run(
                latest_run_id,
                status="completed",
                job_id=job_id,
                prompt_snapshot=prompt_config,
                learning_profile=learning_profile,
                model_chain=[
                    f"{getattr(llm_client, 'provider_name', 'unknown')}:translate_document",
                    *(
                        [f"openai:{getattr(vision_client, 'model', self.settings.openai_vision_model)}:vision"]
                        if vision_augmented_pages
                        else []
                    ),
                ],
                quality_stats={
                    "translation_ready_pages": translated_pages,
                    "translation_total_pages": total_pages,
                    "partial_pages": partial_pages,
                    "unavailable_pages": unavailable_pages,
                    "explained_pages": explained_pages,
                    "vision_augmented_pages": vision_augmented_pages,
                },
                finished_at=_utc_now_iso(),
            )
            self._persist_run_artifacts(
                document_id=document_id,
                run_id=latest_run_id,
                pipeline_report=detail,
                page_payloads=page_payloads,
            )

    def regenerate_page(
        self,
        *,
        document_id: str,
        page_no: int,
        language: str = "zh",
        prompt_override: dict[str, str] | None = None,
        learning_profile: dict[str, Any] | None = None,
        run_id: str | None = None,
        llm_client: LLMClient | None = None,
    ) -> dict[str, Any]:
        doc = self.store.get_document(document_id)
        if doc is None:
            raise ValueError("document not found")

        page = self.store.get_page(document_id, page_no)
        if page is None:
            raise ValueError("page not found")

        prompt_config = prompt_override or self.store.get_document_prompt_config(document_id)
        effective_llm_client = llm_client or self.llm_client
        global_memory, groups, all_pages = self._ensure_document_memory(
            document_id=document_id,
            llm_client=effective_llm_client,
            prompt_config=prompt_config,
        )
        group = self._group_for_page(groups=groups, page_no=page_no)
        local_context = select_local_context(current_page=page, all_pages=all_pages, top_k=4)
        effective_learning_profile = normalize_learning_preferences(
            learning_profile if learning_profile is not None else self.store.get_document_learning_profile(document_id)
        )

        payload, quality, model_used = run_agent_c_with_quality(
            llm_client=effective_llm_client,
            page=page,
            global_memory=global_memory,
            group=group,
            local_context=local_context,
            language=language,
            quality_threshold=self.settings.quality_threshold,
            instruction=prompt_config.get("agent_c_instruction"),
            page_budget_seconds=max(20.0, float(getattr(self.settings, "agent_c_page_timeout_seconds", 90.0)) - 12.0),
        )
        payload = stitch_page_explanation(payload=payload, page_no=page_no)
        quality = evaluate_page_explanation(
            page_no=page_no,
            page_text=str(page.get("text_content", "")),
            explanation=payload,
            global_keywords=global_memory.get("keywords", []),
            threshold=self.settings.quality_threshold,
            scope_pages=payload.get("scopePages", []),
            language=language,
        )

        version = self.store.next_explanation_version(document_id, page_no, language)
        payload["quality"] = quality
        payload["version"] = version

        self.store.save_explanation(
            document_id=document_id,
            page_no=page_no,
            language=language,
            version=version,
            payload=payload,
            quality_score=float(quality["score"]),
            quality=quality,
            model_used=model_used,
        )
        active_run_id = str(run_id or (self.store.get_document(document_id) or {}).get("latest_run_id", "")).strip()
        if active_run_id:
            self.store.update_document_run(
                active_run_id,
                status="completed",
                prompt_snapshot=prompt_config,
                learning_profile=effective_learning_profile,
                model_chain=[
                    _llm_task_label(effective_llm_client, "agent_c"),
                    *([model_used] if model_used and model_used != _llm_task_label(effective_llm_client, "agent_c") else []),
                ],
                quality_stats={
                    "score": float(quality.get("score", 0.0)),
                    "pass": bool(quality.get("pass", False)),
                    "citation_score": float(quality.get("citationScore", 0.0)),
                },
                finished_at=_utc_now_iso(),
            )
            self._persist_run_artifacts(
                document_id=document_id,
                run_id=active_run_id,
                pipeline_report={
                    "scope": "page",
                    "page_no": page_no,
                    "status": "completed",
                    "model_used": model_used,
                },
                page_payloads={page_no: payload},
            )

        return payload

    def answer_page_chat(
        self,
        *,
        document_id: str,
        page_no: int,
        question: str,
        language: str,
        llm_client: LLMClient | None = None,
    ) -> dict[str, Any]:
        effective_llm_client = llm_client or self.llm_client
        page = self.store.get_page(document_id, page_no)
        if page is None:
            raise ValueError("page not found")

        explanation_row = self.store.get_latest_explanation(document_id, page_no, language)
        if explanation_row is None:
            explanation = self.regenerate_page(
                document_id=document_id,
                page_no=page_no,
                language=language,
                llm_client=effective_llm_client,
            )
        else:
            explanation = explanation_row["payload"]

        global_memory = self.store.get_global_memory(document_id) or {"summary": ""}
        all_pages = self.store.list_pages(document_id)
        local_context = [
            candidate
            for candidate in all_pages
            if abs(int(candidate.get("page_no", 0)) - int(page_no)) <= 2
        ]
        if not local_context:
            local_context = select_local_context(current_page=page, all_pages=all_pages, top_k=4)
        prompt_config = self.store.get_document_prompt_config(document_id)
        allowed_pages = {int(page_no), *[int(item["page_no"]) for item in local_context if item.get("page_no")]}

        answer = effective_llm_client.answer_page_question(
            question=question,
            language=language,
            page=page,
            explanation=explanation,
            local_context=local_context,
            global_summary=str(global_memory.get("summary", "")),
            instruction=prompt_config.get("chat_instruction"),
        )
        answer = self._sanitize_chat_answer(answer=answer, allowed_pages=allowed_pages)
        self.store.save_chat(
            document_id=document_id,
            page_no=page_no,
            question=question,
            answer=answer,
        )
        return answer
