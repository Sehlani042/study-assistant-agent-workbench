from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.auth import expires_at_iso, hash_password, new_auth_token, verify_password
from app.database import Database
from app.identity import normalize_email, normalize_username
from app.learning import (
    apply_learning_profile_to_prompt_config,
    default_learning_preferences,
    normalize_learning_preferences,
)
from app.prompts import default_prompt_config
from app.security.crypto import decrypt_secret, encrypt_secret


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_registration_invite_code() -> str:
    raw = secrets.token_urlsafe(18).replace("-", "").replace("_", "").upper()
    if len(raw) < 16:
        raw = (raw + secrets.token_hex(8).upper())[:16]
    return raw[:16]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


def _clean_prompt_overrides(overrides: dict[str, str] | None) -> dict[str, str]:
    if not isinstance(overrides, dict):
        return {}
    allowed = {
        "agent_a_instruction",
        "agent_b_instruction",
        "agent_c_instruction",
        "chat_instruction",
        "formula_instruction",
    }
    cleaned: dict[str, str] = {}
    for key, value in overrides.items():
        if key not in allowed:
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text
    return cleaned


USER_PERMISSION_KEYS = (
    "can_manage_accounts",
    "can_manage_prompts",
    "can_manage_shared_keys",
)

SUPPORTED_LLM_PROVIDERS = ("openai", "gemini", "deepseek", "mock")
SUPPORTED_FALLBACK_CHAIN_PROVIDERS = ("openai", "gemini", "deepseek", "mock")


def _normalize_provider(provider: str | None, *, fallback: str = "openai") -> str:
    candidate = str(provider or "").strip().lower()
    if candidate in SUPPORTED_LLM_PROVIDERS:
        return candidate
    fb = str(fallback or "openai").strip().lower()
    return fb if fb in SUPPORTED_LLM_PROVIDERS else "openai"


def _normalize_fallback_chain_step(step: object) -> str | None:
    text = str(step or "").strip().lower()
    if not text:
        return None
    if text == "flash":
        return "gemini:flash"
    if text == "pro":
        return "gemini:pro"
    if ":" not in text:
        return None
    provider_raw, model_raw = text.split(":", 1)
    provider = provider_raw.strip().lower()
    model = model_raw.strip()
    if provider not in SUPPORTED_FALLBACK_CHAIN_PROVIDERS:
        return None
    if not model:
        return None
    return f"{provider}:{model}"


def _normalize_fallback_chain(chain: list[object] | None, *, default_chain: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in list(chain or []):
        normalized = _normalize_fallback_chain_step(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    if cleaned:
        return cleaned[:8]
    fallback: list[str] = []
    for item in default_chain:
        normalized = _normalize_fallback_chain_step(item)
        if not normalized or normalized in fallback:
            continue
        fallback.append(normalized)
    return fallback[:8]


def _normalize_shared_provider_access(
    raw: dict[str, Any] | str | None,
    *,
    role: str,
    can_use_shared_key: bool,
    fallback: dict[str, bool] | None = None,
) -> dict[str, bool]:
    base = {"openai": False, "gemini": False, "deepseek": False}
    if isinstance(fallback, dict):
        for key in base:
            if key in fallback:
                base[key] = bool(fallback[key])

    parsed: dict[str, Any] = {}
    if isinstance(raw, dict):
        parsed = raw
    elif isinstance(raw, str):
        loaded = _json_loads(raw, {})
        if isinstance(loaded, dict):
            parsed = loaded

    for key in base:
        if key in parsed:
            base[key] = bool(parsed[key])

    normalized_role = str(role or "").strip().lower()
    if normalized_role == "admin":
        base["openai"] = True
        base["gemini"] = True
        base["deepseek"] = True
        return base

    if bool(can_use_shared_key):
        if not any(base.values()):
            base["openai"] = True
            base["gemini"] = True
            base["deepseek"] = True
    return base


def _normalize_permissions(
    raw: dict[str, Any] | str | None,
    *,
    role: str,
    fallback: dict[str, bool] | None = None,
) -> dict[str, bool]:
    base = {key: False for key in USER_PERMISSION_KEYS}
    if isinstance(fallback, dict):
        for key in USER_PERMISSION_KEYS:
            if key in fallback:
                base[key] = bool(fallback[key])

    parsed: dict[str, Any] = {}
    if isinstance(raw, dict):
        parsed = raw
    elif isinstance(raw, str):
        loaded = _json_loads(raw, {})
        if isinstance(loaded, dict):
            parsed = loaded

    for key in USER_PERMISSION_KEYS:
        if key in parsed:
            base[key] = bool(parsed[key])

    if str(role or "").strip().lower() == "admin":
        for key in USER_PERMISSION_KEYS:
            base[key] = True
    return base


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_document(
        self,
        document_id: str,
        original_filename: str,
        source_type: str,
        source_path: str,
        owner_user_id: str | None = None,
        prompt_profile: str = "personal",
        task_prompt: str | None = None,
        prompt_config: dict[str, str] | None = None,
        learning_profile: dict[str, Any] | None = None,
        status: str = "queued",
    ) -> None:
        now = _now()
        normalized_profile = "default" if str(prompt_profile).strip().lower() == "default" else "personal"
        normalized_learning = normalize_learning_preferences(learning_profile)
        self.db.execute(
            """
            INSERT INTO documents(
                id, owner_user_id, original_filename, source_type, source_path, pdf_path, status,
                prompt_profile, task_prompt, prompt_config_json,
                learner_level, learning_goal, depth_mode, attention_support, last_page_no, latest_run_id,
                total_pages, processed_pages, error, language_default, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?,
                      ?, ?, ?,
                      ?, ?, ?, ?, 1, NULL,
                      0, 0, NULL, 'zh', ?, ?)
            """,
            (
                document_id,
                (owner_user_id or "").strip() or None,
                original_filename,
                source_type,
                source_path,
                status,
                normalized_profile,
                (task_prompt or "").strip() or None,
                _json_dumps(prompt_config or default_prompt_config()),
                normalized_learning["learner_level"],
                normalized_learning["learning_goal"],
                normalized_learning["depth_mode"],
                normalized_learning["attention_support"],
                now,
                now,
            ),
        )

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM documents WHERE id = ?", (document_id,))
        if row is None:
            return None
        return _row_to_dict(row)

    def list_documents(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall("SELECT * FROM documents ORDER BY created_at DESC")
        return [_row_to_dict(r) for r in rows]

    def list_documents_for_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        owner_id = str(owner_user_id or "").strip()
        rows = self.db.fetchall(
            "SELECT * FROM documents WHERE owner_user_id = ? ORDER BY created_at DESC",
            (owner_id,),
        )
        return [_row_to_dict(r) for r in rows]

    def list_processing_documents(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM documents WHERE status = 'processing' ORDER BY updated_at DESC",
        )
        return [_row_to_dict(r) for r in rows]

    def update_document_status(
        self,
        document_id: str,
        *,
        status: str | None = None,
        pdf_path: str | None = None,
        total_pages: int | None = None,
        processed_pages: int | None = None,
        error: str | None = None,
    ) -> None:
        doc = self.get_document(document_id)
        if doc is None:
            return
        next_status = status if status is not None else doc["status"]
        next_pdf_path = pdf_path if pdf_path is not None else doc["pdf_path"]
        next_total = total_pages if total_pages is not None else doc["total_pages"]
        next_processed = processed_pages if processed_pages is not None else doc["processed_pages"]
        next_error = error if error is not None else doc["error"]
        self.db.execute(
            """
            UPDATE documents
            SET status = ?, pdf_path = ?, total_pages = ?, processed_pages = ?, error = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_status, next_pdf_path, next_total, next_processed, next_error, _now(), document_id),
        )

    def update_document_prompt_strategy(
        self,
        document_id: str,
        *,
        prompt_profile: str,
        task_prompt: str | None,
        prompt_config: dict[str, str],
    ) -> None:
        normalized_profile = "default" if str(prompt_profile).strip().lower() == "default" else "personal"
        self.db.execute(
            """
            UPDATE documents
            SET prompt_profile = ?, task_prompt = ?, prompt_config_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                normalized_profile,
                (task_prompt or "").strip() or None,
                _json_dumps(prompt_config),
                _now(),
                document_id,
            ),
        )

    def get_document_learning_profile(self, document_id: str) -> dict[str, str]:
        doc = self.get_document(document_id)
        if doc is None:
            return default_learning_preferences()
        return normalize_learning_preferences(
            {
                "learner_level": doc.get("learner_level"),
                "learning_goal": doc.get("learning_goal"),
                "depth_mode": doc.get("depth_mode"),
                "attention_support": doc.get("attention_support"),
            }
        )

    def update_document_learning_profile(
        self,
        document_id: str,
        *,
        learning_profile: dict[str, Any],
    ) -> dict[str, str]:
        normalized = normalize_learning_preferences(learning_profile)
        self.db.execute(
            """
            UPDATE documents
            SET learner_level = ?, learning_goal = ?, depth_mode = ?, attention_support = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                normalized["learner_level"],
                normalized["learning_goal"],
                normalized["depth_mode"],
                normalized["attention_support"],
                _now(),
                document_id,
            ),
        )
        return normalized

    def update_document_last_page(self, document_id: str, *, page_no: int) -> None:
        safe_page = max(1, int(page_no))
        self.db.execute(
            "UPDATE documents SET last_page_no = ?, updated_at = ? WHERE id = ?",
            (safe_page, _now(), document_id),
        )

    def set_document_latest_run(self, document_id: str, *, run_id: str | None) -> None:
        self.db.execute(
            "UPDATE documents SET latest_run_id = ?, updated_at = ? WHERE id = ?",
            ((str(run_id).strip() or None), _now(), document_id),
        )

    def get_document_prompt_config(self, document_id: str) -> dict[str, str]:
        doc = self.get_document(document_id)
        defaults = default_prompt_config()
        if doc is None:
            return defaults

        stored = _json_loads(doc.get("prompt_config_json"), {})
        if not isinstance(stored, dict):
            return defaults

        return {
            "agent_a_instruction": str(stored.get("agent_a_instruction", defaults["agent_a_instruction"])),
            "agent_b_instruction": str(stored.get("agent_b_instruction", defaults["agent_b_instruction"])),
            "agent_c_instruction": str(stored.get("agent_c_instruction", defaults["agent_c_instruction"])),
            "chat_instruction": str(stored.get("chat_instruction", defaults["chat_instruction"])),
            "formula_instruction": str(stored.get("formula_instruction", defaults["formula_instruction"])),
        }

    def delete_document(self, document_id: str) -> dict[str, Any] | None:
        doc = self.get_document(document_id)
        if doc is None:
            return None
        self.delete_document_fallback_chain(document_id=document_id)
        self.db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return doc

    def create_job(
        self,
        job_id: str,
        document_id: str,
        stage: str = "queued",
        *,
        job_type: str = "translate_document",
    ) -> None:
        now = _now()
        self.db.execute(
            """
            INSERT INTO jobs(id, document_id, job_type, status, stage, error, created_at, updated_at)
            VALUES (?, ?, ?, 'queued', ?, NULL, ?, ?)
            """,
            (job_id, document_id, job_type, stage, now, now),
        )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row is None:
            return None
        return _row_to_dict(row)

    def get_job_by_document(self, document_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM jobs WHERE document_id = ? ORDER BY created_at DESC LIMIT 1",
            (document_id,),
        )
        if row is None:
            return None
        return _row_to_dict(row)

    def update_job(
        self,
        job_id: str,
        *,
        job_type: str | None = None,
        status: str | None = None,
        stage: str | None = None,
        error: str | None = None,
    ) -> None:
        job = self.get_job(job_id)
        if job is None:
            return
        self.db.execute(
            """
            UPDATE jobs
            SET job_type = ?, status = ?, stage = ?, error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                job_type if job_type is not None else job.get("job_type", "translate_document"),
                status if status is not None else job["status"],
                stage if stage is not None else job["stage"],
                error if error is not None else job["error"],
                _now(),
                job_id,
            ),
        )

    def upsert_page(
        self,
        *,
        document_id: str,
        page_no: int,
        text_content: str,
        formulas: list[dict[str, Any]],
        image_path: str,
        embedding: list[float],
        page_width: float = 0.0,
        page_height: float = 0.0,
        layout_blocks: list[dict[str, Any]] | None = None,
        translation_blocks: list[dict[str, Any]] | None = None,
        untranslated_blocks: list[dict[str, Any]] | None = None,
        literal_translation: str = "",
        translation_overlay_status: str = "pending",
        translation_updated_at: str | None = None,
    ) -> None:
        page_id = str(uuid4())
        now = _now()
        self.db.execute(
            """
            INSERT INTO pages(
                id, document_id, page_no, text_content, formulas_json, image_path, embedding_json,
                page_width, page_height, layout_blocks_json, translation_blocks_json, untranslated_blocks_json,
                literal_translation, translation_overlay_status, translation_updated_at, group_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(document_id, page_no)
            DO UPDATE SET
                text_content = excluded.text_content,
                formulas_json = excluded.formulas_json,
                image_path = excluded.image_path,
                embedding_json = excluded.embedding_json,
                page_width = excluded.page_width,
                page_height = excluded.page_height,
                layout_blocks_json = excluded.layout_blocks_json,
                translation_blocks_json = excluded.translation_blocks_json,
                untranslated_blocks_json = excluded.untranslated_blocks_json,
                literal_translation = excluded.literal_translation,
                translation_overlay_status = excluded.translation_overlay_status,
                translation_updated_at = excluded.translation_updated_at
            """,
            (
                page_id,
                document_id,
                page_no,
                text_content,
                _json_dumps(formulas),
                image_path,
                _json_dumps(embedding),
                float(page_width or 0.0),
                float(page_height or 0.0),
                _json_dumps(layout_blocks or []),
                _json_dumps(translation_blocks or []),
                _json_dumps(untranslated_blocks or []),
                str(literal_translation or ""),
                str(translation_overlay_status or "pending"),
                translation_updated_at,
                now,
            ),
        )

    def update_page_translation(
        self,
        *,
        document_id: str,
        page_no: int,
        translation_blocks: list[dict[str, Any]],
        untranslated_blocks: list[dict[str, Any]],
        literal_translation: str,
        translation_overlay_status: str,
        translation_updated_at: str | None = None,
    ) -> None:
        self.db.execute(
            """
            UPDATE pages
            SET translation_blocks_json = ?, untranslated_blocks_json = ?, literal_translation = ?,
                translation_overlay_status = ?, translation_updated_at = ?
            WHERE document_id = ? AND page_no = ?
            """,
            (
                _json_dumps(translation_blocks or []),
                _json_dumps(untranslated_blocks or []),
                str(literal_translation or ""),
                str(translation_overlay_status or "pending"),
                translation_updated_at or _now(),
                document_id,
                page_no,
            ),
        )

    def get_page(self, document_id: str, page_no: int) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM pages WHERE document_id = ? AND page_no = ?",
            (document_id, page_no),
        )
        if row is None:
            return None
        payload = _row_to_dict(row)
        payload["formulas"] = _json_loads(payload.pop("formulas_json", "[]"), [])
        payload["embedding"] = _json_loads(payload.pop("embedding_json", "[]"), [])
        payload["layout_blocks"] = _json_loads(payload.pop("layout_blocks_json", "[]"), [])
        payload["translation_blocks"] = _json_loads(payload.pop("translation_blocks_json", "[]"), [])
        payload["untranslated_blocks"] = _json_loads(payload.pop("untranslated_blocks_json", "[]"), [])
        return payload

    def list_pages(self, document_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM pages WHERE document_id = ? ORDER BY page_no ASC",
            (document_id,),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = _row_to_dict(row)
            payload["formulas"] = _json_loads(payload.pop("formulas_json", "[]"), [])
            payload["embedding"] = _json_loads(payload.pop("embedding_json", "[]"), [])
            payload["layout_blocks"] = _json_loads(payload.pop("layout_blocks_json", "[]"), [])
            payload["translation_blocks"] = _json_loads(payload.pop("translation_blocks_json", "[]"), [])
            payload["untranslated_blocks"] = _json_loads(payload.pop("untranslated_blocks_json", "[]"), [])
            out.append(payload)
        return out

    def list_explained_page_nos(self, document_id: str, language: str) -> set[int]:
        rows = self.db.fetchall(
            """
            SELECT DISTINCT page_no FROM explanations
            WHERE document_id = ? AND language = ?
            """,
            (document_id, language),
        )
        out: set[int] = set()
        for row in rows:
            try:
                out.add(int(row["page_no"]))
            except (TypeError, ValueError, KeyError):
                continue
        return out

    def replace_groups(self, document_id: str, groups: list[dict[str, Any]]) -> None:
        self.db.execute("DELETE FROM groups_table WHERE document_id = ?", (document_id,))
        seq: list[tuple[Any, ...]] = []
        for group in groups:
            raw_group_id = str(group.get("id") or uuid4())
            group_id = raw_group_id if raw_group_id.startswith(f"{document_id}:") else f"{document_id}:{raw_group_id}"
            group["id"] = group_id
            seq.append(
                (
                    group_id,
                    document_id,
                    group["title"],
                    group["page_start"],
                    group["page_end"],
                    group.get("summary", ""),
                    _json_dumps(group.get("key_concepts", [])),
                    _json_dumps(group.get("prerequisites", [])),
                    _json_dumps(group.get("misconceptions", [])),
                )
            )
        self.db.executemany(
            """
            INSERT INTO groups_table(
                id, document_id, title, page_start, page_end, summary,
                key_concepts_json, prerequisites_json, misconceptions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            seq,
        )

    def update_group_details(
        self,
        group_id: str,
        *,
        summary: str,
        key_concepts: list[str],
        prerequisites: list[str],
        misconceptions: list[str],
    ) -> None:
        self.db.execute(
            """
            UPDATE groups_table
            SET summary = ?, key_concepts_json = ?, prerequisites_json = ?, misconceptions_json = ?
            WHERE id = ?
            """,
            (
                summary,
                _json_dumps(key_concepts),
                _json_dumps(prerequisites),
                _json_dumps(misconceptions),
                group_id,
            ),
        )

    def list_groups(self, document_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM groups_table WHERE document_id = ? ORDER BY page_start ASC",
            (document_id,),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = _row_to_dict(row)
            payload["key_concepts"] = _json_loads(payload.pop("key_concepts_json", "[]"), [])
            payload["prerequisites"] = _json_loads(payload.pop("prerequisites_json", "[]"), [])
            payload["misconceptions"] = _json_loads(payload.pop("misconceptions_json", "[]"), [])
            out.append(payload)
        return out

    def assign_pages_to_group(self, document_id: str, group_id: str, page_start: int, page_end: int) -> None:
        self.db.execute(
            """
            UPDATE pages
            SET group_id = ?
            WHERE document_id = ? AND page_no BETWEEN ? AND ?
            """,
            (group_id, document_id, page_start, page_end),
        )

    def upsert_global_memory(
        self,
        *,
        document_id: str,
        summary: str,
        keywords: list[str],
        glossary: list[dict[str, str]],
        knowledge_map: list[dict[str, str]],
        learning_arc: list[dict[str, str]],
        version: int,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO global_memory(
                document_id, summary, keywords_json, glossary_json, knowledge_map_json, learning_arc_json, version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id)
            DO UPDATE SET
                summary = excluded.summary,
                keywords_json = excluded.keywords_json,
                glossary_json = excluded.glossary_json,
                knowledge_map_json = excluded.knowledge_map_json,
                learning_arc_json = excluded.learning_arc_json,
                version = excluded.version,
                updated_at = excluded.updated_at
            """,
            (
                document_id,
                summary,
                _json_dumps(keywords),
                _json_dumps(glossary),
                _json_dumps(knowledge_map),
                _json_dumps(learning_arc),
                version,
                _now(),
            ),
        )

    def get_global_memory(self, document_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM global_memory WHERE document_id = ?", (document_id,))
        if row is None:
            return None
        payload = _row_to_dict(row)
        payload["keywords"] = _json_loads(payload.pop("keywords_json", "[]"), [])
        payload["glossary"] = _json_loads(payload.pop("glossary_json", "[]"), [])
        payload["knowledge_map"] = _json_loads(payload.pop("knowledge_map_json", "[]"), [])
        payload["learning_arc"] = _json_loads(payload.pop("learning_arc_json", "[]"), [])
        return payload

    def next_explanation_version(self, document_id: str, page_no: int, language: str) -> int:
        row = self.db.fetchone(
            """
            SELECT version FROM explanations
            WHERE document_id = ? AND page_no = ? AND language = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (document_id, page_no, language),
        )
        if row is None:
            return 1
        return int(row["version"]) + 1

    def save_explanation(
        self,
        *,
        document_id: str,
        page_no: int,
        language: str,
        version: int,
        payload: dict[str, Any],
        quality_score: float,
        quality: dict[str, Any],
        model_used: str,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO explanations(
                id, document_id, page_no, language, version,
                payload_json, quality_score, quality_json, model_used, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                document_id,
                page_no,
                language,
                version,
                _json_dumps(payload),
                quality_score,
                _json_dumps(quality),
                model_used,
                _now(),
            ),
        )

    def update_explanation_payload(
        self,
        *,
        document_id: str,
        page_no: int,
        language: str,
        version: int,
        payload: dict[str, Any],
    ) -> None:
        self.db.execute(
            """
            UPDATE explanations
            SET payload_json = ?
            WHERE document_id = ? AND page_no = ? AND language = ? AND version = ?
            """,
            (
                _json_dumps(payload),
                document_id,
                page_no,
                language,
                version,
            ),
        )

    def get_latest_explanation(
        self,
        document_id: str,
        page_no: int,
        language: str,
    ) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT * FROM explanations
            WHERE document_id = ? AND page_no = ? AND language = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (document_id, page_no, language),
        )
        if row is None:
            return None
        payload = _row_to_dict(row)
        payload["payload"] = _json_loads(payload.pop("payload_json", "{}"), {})
        payload["quality"] = _json_loads(payload.pop("quality_json", "{}"), {})
        return payload

    def save_chat(
        self,
        *,
        document_id: str,
        page_no: int,
        question: str,
        answer: dict[str, Any],
    ) -> None:
        self.db.execute(
            """
            INSERT INTO chats(id, document_id, page_no, question, answer_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), document_id, page_no, question, _json_dumps(answer), _now()),
        )

    def list_chats(self, document_id: str, page_no: int, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT * FROM chats
            WHERE document_id = ? AND page_no = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (document_id, page_no, max(1, min(limit, 500))),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = _row_to_dict(row)
            payload["answer"] = _json_loads(payload.pop("answer_json", "{}"), {})
            out.append(payload)
        return out

    def _prompt_row_id(self, user_id: str | None) -> str:
        return "default" if not user_id else f"user:{user_id}"

    def _normalize_prompt_payload(self, stored: dict[str, Any], defaults: dict[str, str]) -> dict[str, str]:
        legacy_agent_c = stored.get("page_explain_instruction", "")
        agent_a_instruction = str(stored.get("agent_a_instruction", defaults["agent_a_instruction"])).strip()
        agent_b_instruction = str(stored.get("agent_b_instruction", defaults["agent_b_instruction"])).strip()
        agent_c_instruction = str(stored.get("agent_c_instruction", legacy_agent_c or defaults["agent_c_instruction"])).strip()
        chat_instruction = str(stored.get("chat_instruction", defaults["chat_instruction"])).strip()
        formula_instruction = str(stored.get("formula_instruction", defaults["formula_instruction"])).strip()
        return {
            "agent_a_instruction": agent_a_instruction or defaults["agent_a_instruction"],
            "agent_b_instruction": agent_b_instruction or defaults["agent_b_instruction"],
            "agent_c_instruction": agent_c_instruction or defaults["agent_c_instruction"],
            "chat_instruction": chat_instruction or defaults["chat_instruction"],
            "formula_instruction": formula_instruction or defaults["formula_instruction"],
        }

    def get_default_prompt_config(self) -> dict[str, str]:
        defaults = default_prompt_config()
        row = self.db.fetchone("SELECT value_json FROM prompt_configs WHERE id = 'default'")
        if row is None:
            return defaults
        stored = _json_loads(row["value_json"], {})
        if not isinstance(stored, dict):
            return defaults
        return self._normalize_prompt_payload(stored, defaults)

    def get_prompt_config(self, *, user_id: str | None = None) -> dict[str, str]:
        defaults = self.get_default_prompt_config()
        if not user_id:
            return defaults

        row_id = self._prompt_row_id(user_id)
        row = self.db.fetchone("SELECT value_json FROM prompt_configs WHERE id = ?", (row_id,))
        if row is None:
            return defaults
        stored = _json_loads(row["value_json"], {})
        if not isinstance(stored, dict):
            return defaults
        return self._normalize_prompt_payload(stored, defaults)

    def has_custom_prompt_config(self, *, user_id: str) -> bool:
        row_id = self._prompt_row_id(user_id)
        row = self.db.fetchone("SELECT 1 FROM prompt_configs WHERE id = ?", (row_id,))
        return row is not None

    def save_prompt_config(
        self,
        *,
        agent_a_instruction: str | None = None,
        agent_b_instruction: str | None = None,
        agent_c_instruction: str | None = None,
        chat_instruction: str | None = None,
        formula_instruction: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, str]:
        current = self.get_prompt_config(user_id=user_id)
        next_value = {
            "agent_a_instruction": (
                agent_a_instruction.strip()
                if isinstance(agent_a_instruction, str) and agent_a_instruction.strip()
                else current["agent_a_instruction"]
            ),
            "agent_b_instruction": (
                agent_b_instruction.strip()
                if isinstance(agent_b_instruction, str) and agent_b_instruction.strip()
                else current["agent_b_instruction"]
            ),
            "agent_c_instruction": (
                agent_c_instruction.strip()
                if isinstance(agent_c_instruction, str) and agent_c_instruction.strip()
                else current["agent_c_instruction"]
            ),
            "chat_instruction": (
                chat_instruction.strip()
                if isinstance(chat_instruction, str) and chat_instruction.strip()
                else current["chat_instruction"]
            ),
            "formula_instruction": (
                formula_instruction.strip()
                if isinstance(formula_instruction, str) and formula_instruction.strip()
                else current["formula_instruction"]
            ),
        }
        row_id = self._prompt_row_id(user_id)
        self.db.execute(
            """
            INSERT INTO prompt_configs(id, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (row_id, _json_dumps(next_value), _now()),
        )
        return next_value

    def reset_prompt_config(self, *, user_id: str) -> dict[str, str]:
        row_id = self._prompt_row_id(user_id)
        self.db.execute("DELETE FROM prompt_configs WHERE id = ?", (row_id,))
        return self.get_prompt_config(user_id=user_id)

    def build_effective_prompt_config(
        self,
        *,
        user_id: str | None,
        prompt_profile: str,
        task_prompt: str | None,
        prompt_overrides: dict[str, str] | None = None,
        learning_profile: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        profile = "default" if str(prompt_profile).strip().lower() == "default" else "personal"
        base = self.get_default_prompt_config() if profile == "default" else self.get_prompt_config(user_id=user_id)
        task = (task_prompt or "").strip()
        if not task:
            out = dict(base)
        else:
            out = {
                key: f"{value}\n任务特定补充要求（仅当前文档任务）：{task}"
                for key, value in base.items()
            }
        for key, value in _clean_prompt_overrides(prompt_overrides).items():
            out[key] = value
        return apply_learning_profile_to_prompt_config(out, learning_profile=learning_profile)

    def create_document_run(
        self,
        *,
        run_id: str,
        document_id: str,
        trigger_type: str,
        scope_type: str,
        target_page_no: int | None = None,
        job_id: str | None = None,
        status: str = "queued",
        prompt_snapshot: dict[str, Any] | None = None,
        learning_profile: dict[str, Any] | None = None,
        model_chain: list[str] | None = None,
        quality_stats: dict[str, Any] | None = None,
        error: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        now = _now()
        normalized_learning = normalize_learning_preferences(learning_profile)
        self.db.execute(
            """
            INSERT INTO document_runs(
                id, document_id, job_id, trigger_type, scope_type, target_page_no,
                status, error, prompt_snapshot_json, learning_profile_json,
                model_chain_json, quality_stats_json, created_at, started_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                document_id,
                ((str(job_id).strip()) if job_id is not None and str(job_id).strip() else None),
                str(trigger_type or "upload").strip() or "upload",
                str(scope_type or "document").strip() or "document",
                int(target_page_no) if target_page_no is not None else None,
                str(status or "queued").strip() or "queued",
                (str(error).strip() or None),
                _json_dumps(prompt_snapshot or {}),
                _json_dumps(normalized_learning),
                _json_dumps(list(model_chain or [])),
                _json_dumps(quality_stats or {}),
                now,
                started_at or now,
                finished_at,
                now,
            ),
        )
        self.set_document_latest_run(document_id, run_id=run_id)

    def update_document_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        error: str | None = None,
        prompt_snapshot: dict[str, Any] | None = None,
        learning_profile: dict[str, Any] | None = None,
        model_chain: list[str] | None = None,
        quality_stats: dict[str, Any] | None = None,
        job_id: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        current = self.get_document_run(run_id)
        if current is None:
            return
        current_job_id = current.get("job_id")
        current_error = current.get("error")
        next_job_id = (
            ((str(job_id).strip()) or None)
            if job_id is not None
            else (((str(current_job_id).strip()) or None) if current_job_id is not None else None)
        )
        next_error = (
            ((str(error).strip()) or None)
            if error is not None
            else (((str(current_error).strip()) or None) if current_error is not None else None)
        )
        self.db.execute(
            """
            UPDATE document_runs
            SET job_id = ?, status = ?, error = ?, prompt_snapshot_json = ?, learning_profile_json = ?,
                model_chain_json = ?, quality_stats_json = ?, finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_job_id,
                str(status if status is not None else current.get("status", "queued")),
                next_error,
                _json_dumps(prompt_snapshot if prompt_snapshot is not None else current.get("prompt_snapshot", {})),
                _json_dumps(
                    normalize_learning_preferences(
                        learning_profile if learning_profile is not None else current.get("learning_profile", {})
                    )
                ),
                _json_dumps(list(model_chain if model_chain is not None else current.get("model_chain", []))),
                _json_dumps(quality_stats if quality_stats is not None else current.get("quality_stats", {})),
                finished_at if finished_at is not None else current.get("finished_at"),
                _now(),
                run_id,
            ),
        )

    def get_document_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM document_runs WHERE id = ? LIMIT 1", (run_id,))
        if row is None:
            return None
        payload = _row_to_dict(row)
        payload["prompt_snapshot"] = _json_loads(payload.pop("prompt_snapshot_json", "{}"), {})
        payload["learning_profile"] = normalize_learning_preferences(
            _json_loads(payload.pop("learning_profile_json", "{}"), {})
        )
        payload["model_chain"] = _json_loads(payload.pop("model_chain_json", "[]"), [])
        payload["quality_stats"] = _json_loads(payload.pop("quality_stats_json", "{}"), {})
        return payload

    def list_document_runs(self, document_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM document_runs WHERE document_id = ? ORDER BY created_at DESC",
            (document_id,),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = _row_to_dict(row)
            payload["prompt_snapshot"] = _json_loads(payload.pop("prompt_snapshot_json", "{}"), {})
            payload["learning_profile"] = normalize_learning_preferences(
                _json_loads(payload.pop("learning_profile_json", "{}"), {})
            )
            payload["model_chain"] = _json_loads(payload.pop("model_chain_json", "[]"), [])
            payload["quality_stats"] = _json_loads(payload.pop("quality_stats_json", "{}"), {})
            out.append(payload)
        return out

    def get_latest_document_run(self, document_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM document_runs WHERE document_id = ? ORDER BY created_at DESC LIMIT 1",
            (document_id,),
        )
        if row is None:
            return None
        payload = _row_to_dict(row)
        payload["prompt_snapshot"] = _json_loads(payload.pop("prompt_snapshot_json", "{}"), {})
        payload["learning_profile"] = normalize_learning_preferences(
            _json_loads(payload.pop("learning_profile_json", "{}"), {})
        )
        payload["model_chain"] = _json_loads(payload.pop("model_chain_json", "[]"), [])
        payload["quality_stats"] = _json_loads(payload.pop("quality_stats_json", "{}"), {})
        return payload

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        canonical = normalize_username(username)
        row = self.db.fetchone("SELECT * FROM users WHERE lower(username) = ? LIMIT 1", (canonical,))
        if row is None:
            return None
        return _row_to_dict(row)

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        if row is None:
            return None
        return _row_to_dict(row)

    def ensure_admin_account(self, *, username: str, password: str) -> dict[str, Any]:
        canonical_username = normalize_username(username)
        existing = self.get_user_by_username(canonical_username)
        if existing is not None:
            if str(existing.get("role", "")) != "admin":
                self.db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (existing["id"],))
                existing = self.get_user_by_username(canonical_username) or existing
            if int(existing.get("can_use_shared_key", 0)) != 1:
                self.db.execute("UPDATE users SET can_use_shared_key = 1 WHERE id = ?", (existing["id"],))
                existing = self.get_user_by_username(canonical_username) or existing
            shared_access = _normalize_shared_provider_access(
                existing.get("shared_key_providers_json"),
                role="admin",
                can_use_shared_key=True,
            )
            if _json_dumps(shared_access) != _json_dumps(
                _normalize_shared_provider_access(
                    existing.get("shared_key_providers_json"),
                    role=str(existing.get("role", "")),
                    can_use_shared_key=bool(int(existing.get("can_use_shared_key", 0))),
                )
            ):
                self.db.execute(
                    "UPDATE users SET shared_key_providers_json = ? WHERE id = ?",
                    (_json_dumps(shared_access), existing["id"]),
                )
                existing = self.get_user_by_username(canonical_username) or existing
            next_permissions = _normalize_permissions(existing.get("permissions_json"), role="admin")
            if _normalize_permissions(existing.get("permissions_json"), role=str(existing.get("role", ""))) != next_permissions:
                self.db.execute(
                    "UPDATE users SET permissions_json = ? WHERE id = ?",
                    (_json_dumps(next_permissions), existing["id"]),
                )
                existing = self.get_user_by_username(canonical_username) or existing
            if str(existing.get("username", "")) != canonical_username:
                self.db.execute("UPDATE users SET username = ? WHERE id = ?", (canonical_username, existing["id"]))
                existing = self.get_user_by_username(canonical_username) or existing
            return existing

        user_id = str(uuid4())
        admin_permissions = _normalize_permissions({}, role="admin")
        self.db.execute(
            """
            INSERT INTO users(
                id, username, password_hash, role, is_active, can_use_shared_key, shared_key_providers_json, permissions_json, created_at
            )
            VALUES (?, ?, ?, 'admin', 1, 1, ?, ?, ?)
            """,
            (
                user_id,
                canonical_username,
                hash_password(password),
                _json_dumps({"openai": True, "gemini": True, "deepseek": True}),
                _json_dumps(admin_permissions),
                _now(),
            ),
        )
        return self.get_user_by_id(user_id) or {
            "id": user_id,
            "username": canonical_username,
            "role": "admin",
            "is_active": 1,
            "can_use_shared_key": 1,
            "shared_key_providers_json": _json_dumps({"openai": True, "gemini": True, "deepseek": True}),
            "permissions_json": _json_dumps(admin_permissions),
        }

    def create_user(
        self,
        *,
        username: str,
        password: str,
        role: str = "user",
        email: str | None = None,
        email_verified: bool = False,
        is_active: bool = True,
        can_use_shared_key: bool = False,
        permissions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        canonical_username = normalize_username(username)
        canonical_email = normalize_email(email or "") or None
        normalized_role = str(role or "user").strip().lower()
        normalized_permissions = _normalize_permissions(permissions or {}, role=normalized_role)
        shared_key_enabled = bool(can_use_shared_key) or normalized_role == "admin"
        shared_access = _normalize_shared_provider_access(
            None,
            role=normalized_role,
            can_use_shared_key=shared_key_enabled,
        )
        user_id = str(uuid4())
        self.db.execute(
            """
            INSERT INTO users(
                id, username, email, email_verified, password_hash, role, is_active, can_use_shared_key,
                shared_key_providers_json, permissions_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                canonical_username,
                canonical_email,
                1 if email_verified else 0,
                hash_password(password),
                normalized_role,
                1 if is_active else 0,
                1 if shared_key_enabled else 0,
                _json_dumps(shared_access),
                _json_dumps(normalized_permissions),
                _now(),
            ),
        )
        return self.get_user_by_id(user_id) or {
            "id": user_id,
            "username": canonical_username,
            "email": canonical_email,
            "email_verified": 1 if email_verified else 0,
            "role": normalized_role,
            "is_active": 1 if is_active else 0,
            "can_use_shared_key": 1 if shared_key_enabled else 0,
            "shared_key_providers_json": _json_dumps(shared_access),
            "permissions_json": _json_dumps(normalized_permissions),
        }

    def delete_user(self, *, user_id: str) -> None:
        uid = str(user_id or "").strip()
        if not uid:
            return
        self.db.execute("DELETE FROM users WHERE id = ?", (uid,))

    def verify_user_password(self, *, username: str, password: str) -> dict[str, Any] | None:
        user = self.get_user_by_username(normalize_username(username))
        if user is None:
            return None
        if int(user.get("is_active", 0)) != 1:
            return None
        encoded = str(user.get("password_hash", ""))
        if not verify_password(password, encoded):
            return None
        return user

    def create_auth_token(self, *, user_id: str, ttl_hours: int) -> str:
        token = new_auth_token()
        self.db.execute(
            """
            INSERT INTO auth_tokens(token, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, user_id, expires_at_iso(ttl_hours), _now()),
        )
        return token

    def delete_auth_token(self, token: str) -> None:
        self.db.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))

    def get_user_by_token(self, token: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT u.* , t.expires_at as token_expires_at
            FROM auth_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token = ?
            """,
            (token,),
        )
        if row is None:
            return None
        payload = _row_to_dict(row)
        expires_at_raw = str(payload.get("token_expires_at", ""))
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            self.delete_auth_token(token)
            return None
        if expires_at <= datetime.now(UTC):
            self.delete_auth_token(token)
            return None
        if int(payload.get("is_active", 0)) != 1:
            return None
        return payload

    def list_users(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT id, username, email, email_verified, role, is_active, can_use_shared_key, shared_key_providers_json, permissions_json, created_at
            FROM users
            ORDER BY created_at ASC
            """
        )
        return [_row_to_dict(row) for row in rows]

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        canonical = normalize_email(email)
        row = self.db.fetchone("SELECT * FROM users WHERE lower(email) = ? LIMIT 1", (canonical,))
        if row is None:
            return None
        return _row_to_dict(row)

    def user_can_use_shared_key(self, *, user_id: str, provider: str = "openai") -> bool:
        row = self.db.fetchone(
            "SELECT can_use_shared_key, shared_key_providers_json, role FROM users WHERE id = ? LIMIT 1",
            (user_id,),
        )
        if row is None:
            return False
        try:
            role = str(row["role"] or "user")
            global_enabled = int(row["can_use_shared_key"]) == 1
            access = _normalize_shared_provider_access(
                row["shared_key_providers_json"],
                role=role,
                can_use_shared_key=global_enabled,
            )
            normalized_provider = _normalize_provider(provider)
            if normalized_provider == "mock":
                return True
            return bool(access.get(normalized_provider, False))
        except Exception:
            return False

    def set_user_shared_key_access(self, *, user_id: str, enabled: bool, provider: str | None = None) -> None:
        current = self.get_user_by_id(user_id)
        if current is None:
            return
        role = str(current.get("role", "user"))
        current_global = bool(int(current.get("can_use_shared_key", 0)))
        access = _normalize_shared_provider_access(
            current.get("shared_key_providers_json"),
            role=role,
            can_use_shared_key=current_global,
        )
        if provider is None:
            for key in access:
                access[key] = bool(enabled)
        else:
            normalized_provider = _normalize_provider(provider)
            if normalized_provider in access:
                access[normalized_provider] = bool(enabled)
        next_global = bool(any(access.values()))
        self.db.execute(
            "UPDATE users SET can_use_shared_key = ?, shared_key_providers_json = ? WHERE id = ?",
            (1 if next_global else 0, _json_dumps(access), user_id),
        )

    def get_user_permissions(self, user: dict[str, Any]) -> dict[str, bool]:
        role = str(user.get("role", "user"))
        return _normalize_permissions(user.get("permissions_json"), role=role)

    def update_user(
        self,
        *,
        user_id: str,
        role: str | None = None,
        is_active: bool | None = None,
        can_use_shared_key: bool | None = None,
        permissions: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_user_by_id(user_id)
        if current is None:
            return None

        next_role = str(role or current.get("role", "user")).strip().lower()
        next_is_active = int(current.get("is_active", 1)) == 1 if is_active is None else bool(is_active)
        current_shared_key = int(current.get("can_use_shared_key", 0)) == 1
        next_shared_key = current_shared_key if can_use_shared_key is None else bool(can_use_shared_key)
        next_shared_access = _normalize_shared_provider_access(
            current.get("shared_key_providers_json"),
            role=next_role,
            can_use_shared_key=next_shared_key,
            fallback=_normalize_shared_provider_access(
                current.get("shared_key_providers_json"),
                role=str(current.get("role", "user")),
                can_use_shared_key=current_shared_key,
            ),
        )
        next_permissions = _normalize_permissions(
            permissions if permissions is not None else current.get("permissions_json"),
            role=next_role,
            fallback=self.get_user_permissions(current),
        )
        if next_role == "admin":
            next_shared_key = True
            next_shared_access = {"openai": True, "gemini": True, "deepseek": True}
        else:
            next_shared_key = bool(any(next_shared_access.values()))

        self.db.execute(
            """
            UPDATE users
            SET role = ?, is_active = ?, can_use_shared_key = ?, shared_key_providers_json = ?, permissions_json = ?
            WHERE id = ?
            """,
            (
                next_role,
                1 if next_is_active else 0,
                1 if next_shared_key else 0,
                _json_dumps(next_shared_access),
                _json_dumps(next_permissions),
                user_id,
            ),
        )
        return self.get_user_by_id(user_id)

    def create_shared_key_invite(
        self,
        *,
        created_by_user_id: str,
        ttl_hours: int,
        max_uses: int,
        note: str | None = None,
    ) -> dict[str, Any]:
        hours = max(1, min(24 * 30, int(ttl_hours)))
        uses = max(1, min(1000, int(max_uses)))
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
        self.db.execute(
            """
            INSERT INTO shared_key_invites(token, created_by_user_id, expires_at, max_uses, used_count, note, created_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (token, created_by_user_id, expires_at, uses, (note or "").strip() or None, _now()),
        )
        return {
            "token": token,
            "expires_at": expires_at,
            "max_uses": uses,
            "note": (note or "").strip() or None,
        }

    def redeem_shared_key_invite(self, *, token: str, user_id: str) -> dict[str, Any]:
        invite_token = str(token or "").strip()
        if not invite_token:
            raise ValueError("invite token required")
        row = self.db.fetchone("SELECT * FROM shared_key_invites WHERE token = ? LIMIT 1", (invite_token,))
        if row is None:
            raise ValueError("invalid invite token")
        invite = _row_to_dict(row)
        expires_at_raw = str(invite.get("expires_at", ""))
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError as exc:
            raise ValueError("invalid invite token") from exc
        if expires_at <= datetime.now(UTC):
            raise ValueError("invite token expired")

        used_count = int(invite.get("used_count", 0))
        max_uses = int(invite.get("max_uses", 1))
        if used_count >= max_uses:
            raise ValueError("invite token already used")

        self.db.execute(
            "UPDATE shared_key_invites SET used_count = used_count + 1 WHERE token = ?",
            (invite_token,),
        )
        self.set_user_shared_key_access(user_id=user_id, enabled=True)

        return {
            "token": invite_token,
            "used_count": used_count + 1,
            "max_uses": max_uses,
            "expires_at": expires_at_raw,
        }

    def create_registration_invite(
        self,
        *,
        created_by_user_id: str,
        ttl_hours: int = 24 * 7,
        max_uses: int = 20,
        note: str | None = None,
    ) -> dict[str, Any]:
        creator_id = str(created_by_user_id or "").strip()
        if not creator_id:
            raise ValueError("created_by_user_id required")
        hours = max(1, min(24 * 30, int(ttl_hours)))
        uses = max(1, min(1000, int(max_uses)))
        expires_at = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
        note_text = (note or "").strip() or None
        now = _now()

        for _ in range(8):
            code = _new_registration_invite_code()
            try:
                self.db.execute(
                    """
                    INSERT INTO registration_invites(
                        code, created_by_user_id, expires_at, max_uses, used_count, revoked_at, note, created_at
                    )
                    VALUES (?, ?, ?, ?, 0, NULL, ?, ?)
                    """,
                    (code, creator_id, expires_at, uses, note_text, now),
                )
                row = self.db.fetchone("SELECT * FROM registration_invites WHERE code = ? LIMIT 1", (code,))
                if row is None:
                    break
                payload = _row_to_dict(row)
                return {
                    "code": code,
                    "expires_at": str(payload.get("expires_at", "")),
                    "max_uses": int(payload.get("max_uses", uses)),
                    "used_count": int(payload.get("used_count", 0)),
                    "note": payload.get("note"),
                    "revoked_at": payload.get("revoked_at"),
                    "created_at": str(payload.get("created_at", now)),
                }
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("failed to create registration invite")

    def create_registration_invites_batch(
        self,
        *,
        created_by_user_id: str,
        count: int,
        ttl_hours: int = 24 * 7,
        max_uses: int = 20,
        note: str | None = None,
    ) -> list[dict[str, Any]]:
        batch_count = max(1, min(200, int(count)))
        out: list[dict[str, Any]] = []
        for _ in range(batch_count):
            out.append(
                self.create_registration_invite(
                    created_by_user_id=created_by_user_id,
                    ttl_hours=ttl_hours,
                    max_uses=max_uses,
                    note=note,
                )
            )
        return out

    def list_registration_invites(self, *, limit: int = 500) -> list[dict[str, Any]]:
        safe_limit = max(1, min(2000, int(limit)))
        rows = self.db.fetchall(
            """
            SELECT code, created_by_user_id, expires_at, max_uses, used_count, revoked_at, note, created_at
            FROM registration_invites
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        return [_row_to_dict(row) for row in rows]

    def revoke_registration_invite(self, *, code: str) -> dict[str, Any] | None:
        invite_code = str(code or "").strip().upper()
        if not invite_code:
            return None
        self.db.execute(
            """
            UPDATE registration_invites
            SET revoked_at = COALESCE(revoked_at, ?)
            WHERE code = ?
            """,
            (_now(), invite_code),
        )
        row = self.db.fetchone(
            """
            SELECT code, created_by_user_id, expires_at, max_uses, used_count, revoked_at, note, created_at
            FROM registration_invites
            WHERE code = ?
            LIMIT 1
            """,
            (invite_code,),
        )
        return _row_to_dict(row) if row is not None else None

    def consume_registration_invite(
        self,
        *,
        code: str,
        username: str,
        email: str | None = None,
        used_by_user_id: str | None = None,
        used_ip: str | None = None,
    ) -> dict[str, Any]:
        invite_code = str(code or "").strip().upper()
        if not invite_code:
            raise ValueError("invite code required")
        canonical_username = normalize_username(username)
        canonical_email = normalize_email(email or "") or None
        now_iso = _now()
        now_dt = datetime.now(UTC)

        with self.db._lock, self.db.connection() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                """
                SELECT code, expires_at, max_uses, used_count, revoked_at
                FROM registration_invites
                WHERE code = ?
                LIMIT 1
                """,
                (invite_code,),
            ).fetchone()
            if row is None:
                raise ValueError("invalid invite code")
            payload = _row_to_dict(row)
            revoked_at = str(payload.get("revoked_at", "") or "").strip()
            if revoked_at:
                raise ValueError("invite code revoked")
            expires_at_raw = str(payload.get("expires_at", ""))
            try:
                expires_at = datetime.fromisoformat(expires_at_raw)
            except ValueError as exc:
                raise ValueError("invalid invite code") from exc
            if expires_at <= now_dt:
                raise ValueError("invite code expired")
            used_count = int(payload.get("used_count", 0))
            max_uses = int(payload.get("max_uses", 1))
            if used_count >= max_uses:
                raise ValueError("invite code exhausted")

            cursor = conn.execute(
                """
                UPDATE registration_invites
                SET used_count = used_count + 1
                WHERE code = ? AND revoked_at IS NULL AND used_count < max_uses
                """,
                (invite_code,),
            )
            if int(cursor.rowcount or 0) != 1:
                raise ValueError("invite code unavailable")

            conn.execute(
                """
                INSERT INTO registration_invite_uses(
                    id, invite_code, used_by_user_id, used_username, used_email, used_ip, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    invite_code,
                    str(used_by_user_id or "").strip() or None,
                    canonical_username,
                    canonical_email,
                    str(used_ip or "").strip() or None,
                    now_iso,
                ),
            )

            latest = conn.execute(
                """
                SELECT code, expires_at, max_uses, used_count, revoked_at, note, created_at
                FROM registration_invites
                WHERE code = ?
                LIMIT 1
                """,
                (invite_code,),
            ).fetchone()

        if latest is None:
            raise ValueError("invalid invite code")
        latest_payload = _row_to_dict(latest)
        return {
            "code": invite_code,
            "used_count": int(latest_payload.get("used_count", 0)),
            "max_uses": int(latest_payload.get("max_uses", 1)),
            "expires_at": str(latest_payload.get("expires_at", "")),
            "revoked_at": latest_payload.get("revoked_at"),
        }

    def get_global_llm_settings(
        self,
        *,
        default_provider: str,
        default_models: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        fallback_models = dict(default_models or {})
        for provider in ("openai", "gemini", "deepseek", "mock"):
            fallback_models.setdefault(provider, "mock" if provider == "mock" else "")
        row = self.db.fetchone("SELECT value_json FROM llm_settings WHERE id = 'global' LIMIT 1")
        if row is None:
            return {
                "default_provider": _normalize_provider(default_provider, fallback="openai"),
                "default_models": fallback_models,
            }
        payload = _json_loads(str(row["value_json"]), {})
        if not isinstance(payload, dict):
            payload = {}
        stored_models = payload.get("default_models", {})
        merged_models = dict(fallback_models)
        if isinstance(stored_models, dict):
            for key, value in stored_models.items():
                provider = _normalize_provider(key)
                merged_models[provider] = str(value or "").strip() or merged_models.get(provider, "")
        return {
            "default_provider": _normalize_provider(payload.get("default_provider"), fallback=default_provider),
            "default_models": merged_models,
        }

    def save_global_llm_settings(
        self,
        *,
        default_provider: str | None = None,
        default_models: dict[str, str] | None = None,
        fallback_provider: str = "openai",
        fallback_models: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        current = self.get_global_llm_settings(
            default_provider=fallback_provider,
            default_models=fallback_models,
        )
        next_provider = _normalize_provider(default_provider or current.get("default_provider"), fallback=fallback_provider)
        next_models = dict(current.get("default_models", {}))
        if isinstance(default_models, dict):
            for key, value in default_models.items():
                provider = _normalize_provider(key)
                text = str(value or "").strip()
                if text:
                    next_models[provider] = text
        payload = {
            "default_provider": next_provider,
            "default_models": next_models,
        }
        self.db.execute(
            """
            INSERT INTO llm_settings(id, value_json, updated_at)
            VALUES ('global', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (_json_dumps(payload), _now()),
        )
        return payload

    def get_user_llm_settings(self, *, user_id: str) -> dict[str, str]:
        uid = str(user_id or "").strip()
        if not uid:
            return {}
        row = self.db.fetchone("SELECT value_json FROM llm_settings WHERE id = ? LIMIT 1", (f"user:{uid}",))
        if row is None:
            return {}
        payload = _json_loads(str(row["value_json"]), {})
        if not isinstance(payload, dict):
            return {}
        provider = _normalize_provider(payload.get("provider"))
        model = str(payload.get("model", "")).strip()
        if not model:
            return {"provider": provider}
        return {"provider": provider, "model": model}

    def save_user_llm_settings(
        self,
        *,
        user_id: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, str]:
        uid = str(user_id or "").strip()
        if not uid:
            return {}
        current = self.get_user_llm_settings(user_id=uid)
        next_provider = _normalize_provider(provider or current.get("provider"))
        next_model = str(model or current.get("model", "")).strip()
        payload = {"provider": next_provider, "model": next_model}
        self.db.execute(
            """
            INSERT INTO llm_settings(id, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (f"user:{uid}", _json_dumps(payload), _now()),
        )
        return payload

    def get_user_learning_preferences(self, *, user_id: str) -> dict[str, str]:
        uid = str(user_id or "").strip()
        defaults = default_learning_preferences()
        if not uid:
            return defaults
        row = self.db.fetchone("SELECT value_json FROM llm_settings WHERE id = ? LIMIT 1", (f"learning:user:{uid}",))
        if row is None:
            return defaults
        payload = _json_loads(str(row["value_json"]), {})
        if not isinstance(payload, dict):
            return defaults
        return normalize_learning_preferences(payload)

    def save_user_learning_preferences(
        self,
        *,
        user_id: str,
        learner_level: str | None = None,
        learning_goal: str | None = None,
        depth_mode: str | None = None,
        attention_support: str | None = None,
    ) -> dict[str, str]:
        uid = str(user_id or "").strip()
        if not uid:
            return default_learning_preferences()
        current = self.get_user_learning_preferences(user_id=uid)
        next_value = normalize_learning_preferences(
            {
                "learner_level": learner_level if learner_level is not None else current.get("learner_level"),
                "learning_goal": learning_goal if learning_goal is not None else current.get("learning_goal"),
                "depth_mode": depth_mode if depth_mode is not None else current.get("depth_mode"),
                "attention_support": attention_support if attention_support is not None else current.get("attention_support"),
            }
        )
        self.db.execute(
            """
            INSERT INTO llm_settings(id, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (f"learning:user:{uid}", _json_dumps(next_value), _now()),
        )
        return next_value

    def _fallback_chain_row_id(self, *, scope: str, identity: str | None = None) -> str:
        normalized_scope = str(scope or "").strip().lower()
        if normalized_scope == "global":
            return "fallback:global"
        if normalized_scope == "user":
            uid = str(identity or "").strip()
            return f"fallback:user:{uid}" if uid else "fallback:user:"
        if normalized_scope == "document":
            doc_id = str(identity or "").strip()
            return f"fallback:document:{doc_id}" if doc_id else "fallback:document:"
        raise ValueError(f"unsupported fallback chain scope: {scope}")

    def get_global_fallback_chain(self, *, default_chain: list[str]) -> list[str]:
        row = self.db.fetchone("SELECT value_json FROM llm_settings WHERE id = ? LIMIT 1", ("fallback:global",))
        if row is None:
            return _normalize_fallback_chain([], default_chain=default_chain)
        payload = _json_loads(str(row["value_json"]), {})
        chain = payload.get("chain", []) if isinstance(payload, dict) else []
        if not isinstance(chain, list):
            chain = []
        return _normalize_fallback_chain(chain, default_chain=default_chain)

    def save_global_fallback_chain(self, *, chain: list[str], default_chain: list[str]) -> list[str]:
        normalized = _normalize_fallback_chain(chain, default_chain=default_chain)
        payload = {"chain": normalized}
        self.db.execute(
            """
            INSERT INTO llm_settings(id, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            ("fallback:global", _json_dumps(payload), _now()),
        )
        return normalized

    def get_user_fallback_chain(self, *, user_id: str) -> list[str]:
        uid = str(user_id or "").strip()
        if not uid:
            return []
        row = self.db.fetchone(
            "SELECT value_json FROM llm_settings WHERE id = ? LIMIT 1",
            (self._fallback_chain_row_id(scope="user", identity=uid),),
        )
        if row is None:
            return []
        payload = _json_loads(str(row["value_json"]), {})
        if not isinstance(payload, dict):
            return []
        chain = payload.get("chain", [])
        return [item for item in _normalize_fallback_chain(chain if isinstance(chain, list) else [], default_chain=[]) if item]

    def save_user_fallback_chain(
        self,
        *,
        user_id: str,
        chain: list[str],
        default_chain: list[str],
    ) -> list[str]:
        uid = str(user_id or "").strip()
        if not uid:
            return []
        normalized = _normalize_fallback_chain(chain, default_chain=default_chain)
        payload = {"chain": normalized}
        self.db.execute(
            """
            INSERT INTO llm_settings(id, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (self._fallback_chain_row_id(scope="user", identity=uid), _json_dumps(payload), _now()),
        )
        return normalized

    def get_document_fallback_chain(self, *, document_id: str) -> list[str]:
        doc_id = str(document_id or "").strip()
        if not doc_id:
            return []
        row = self.db.fetchone(
            "SELECT value_json FROM llm_settings WHERE id = ? LIMIT 1",
            (self._fallback_chain_row_id(scope="document", identity=doc_id),),
        )
        if row is None:
            return []
        payload = _json_loads(str(row["value_json"]), {})
        if not isinstance(payload, dict):
            return []
        chain = payload.get("chain", [])
        return [item for item in _normalize_fallback_chain(chain if isinstance(chain, list) else [], default_chain=[]) if item]

    def save_document_fallback_chain(
        self,
        *,
        document_id: str,
        chain: list[str],
        default_chain: list[str],
    ) -> list[str]:
        doc_id = str(document_id or "").strip()
        if not doc_id:
            return []
        normalized = _normalize_fallback_chain(chain, default_chain=default_chain)
        payload = {"chain": normalized}
        self.db.execute(
            """
            INSERT INTO llm_settings(id, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (self._fallback_chain_row_id(scope="document", identity=doc_id), _json_dumps(payload), _now()),
        )
        return normalized

    def delete_document_fallback_chain(self, *, document_id: str) -> None:
        doc_id = str(document_id or "").strip()
        if not doc_id:
            return
        self.db.execute(
            "DELETE FROM llm_settings WHERE id = ?",
            (self._fallback_chain_row_id(scope="document", identity=doc_id),),
        )

    def upsert_user_personal_llm_key(
        self,
        *,
        user_id: str,
        provider: str,
        api_key: str,
        encryption_secret: str,
    ) -> dict[str, Any]:
        uid = str(user_id or "").strip()
        normalized_provider = _normalize_provider(provider)
        key = str(api_key or "").strip()
        if not uid:
            raise ValueError("user_id required")
        if not key:
            raise ValueError("api key required")
        encrypted_key = encrypt_secret(plaintext=key, secret=encryption_secret)
        now = _now()
        self.db.execute(
            """
            INSERT INTO user_llm_keys(user_id, provider, encrypted_key, last4, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, provider) DO UPDATE SET
                encrypted_key = excluded.encrypted_key,
                last4 = excluded.last4,
                updated_at = excluded.updated_at
            """,
            (uid, normalized_provider, encrypted_key, key[-4:], now, now),
        )
        return {"user_id": uid, "provider": normalized_provider, "last4": key[-4:]}

    def delete_user_personal_llm_key(self, *, user_id: str, provider: str) -> None:
        uid = str(user_id or "").strip()
        normalized_provider = _normalize_provider(provider)
        if not uid:
            return
        self.db.execute(
            "DELETE FROM user_llm_keys WHERE user_id = ? AND provider = ?",
            (uid, normalized_provider),
        )

    def has_user_personal_llm_key(self, *, user_id: str, provider: str) -> bool:
        uid = str(user_id or "").strip()
        normalized_provider = _normalize_provider(provider)
        if not uid:
            return False
        row = self.db.fetchone(
            "SELECT 1 FROM user_llm_keys WHERE user_id = ? AND provider = ? LIMIT 1",
            (uid, normalized_provider),
        )
        return row is not None

    def get_user_personal_llm_key(
        self,
        *,
        user_id: str,
        provider: str,
        encryption_secret: str,
    ) -> str:
        uid = str(user_id or "").strip()
        normalized_provider = _normalize_provider(provider)
        if not uid:
            return ""
        row = self.db.fetchone(
            "SELECT encrypted_key FROM user_llm_keys WHERE user_id = ? AND provider = ? LIMIT 1",
            (uid, normalized_provider),
        )
        if row is None:
            return ""
        encrypted = str(row["encrypted_key"] or "")
        if not encrypted:
            return ""
        try:
            return decrypt_secret(ciphertext=encrypted, secret=encryption_secret)
        except Exception:
            return ""

    def save_email_verification_code(self, *, email: str, code: str, ttl_minutes: int) -> None:
        canonical_email = normalize_email(email)
        minutes = max(1, min(120, int(ttl_minutes)))
        expires_at = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()
        self.db.execute(
            """
            INSERT INTO email_verification_codes(id, email, code_hash, expires_at, consumed_at, created_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            (str(uuid4()), canonical_email, hash_password(code), expires_at, _now()),
        )

    def get_latest_email_verification_code_created_at(self, *, email: str) -> datetime | None:
        canonical_email = normalize_email(email)
        row = self.db.fetchone(
            """
            SELECT created_at FROM email_verification_codes
            WHERE lower(email) = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (canonical_email,),
        )
        if row is None:
            return None
        raw = str(row["created_at"] or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    def verify_and_consume_email_code(self, *, email: str, code: str) -> bool:
        canonical_email = normalize_email(email)
        rows = self.db.fetchall(
            """
            SELECT * FROM email_verification_codes
            WHERE lower(email) = ? AND consumed_at IS NULL
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (canonical_email,),
        )
        now = datetime.now(UTC)
        for row in rows:
            payload = _row_to_dict(row)
            expires_at_raw = str(payload.get("expires_at", ""))
            try:
                expires_at = datetime.fromisoformat(expires_at_raw)
            except ValueError:
                continue
            if expires_at <= now:
                continue
            code_hash = str(payload.get("code_hash", ""))
            if not code_hash:
                continue
            if verify_password(str(code), code_hash):
                self.db.execute(
                    "UPDATE email_verification_codes SET consumed_at = ? WHERE id = ?",
                    (_now(), str(payload.get("id", ""))),
                )
                return True
        return False

    def remove_document_files(self, document_id: str, data_dir: Path) -> None:
        doc_dir = data_dir / "documents" / document_id
        if doc_dir.exists():
            shutil.rmtree(doc_dir, ignore_errors=True)
