from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._lock, self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT,
                    original_filename TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    pdf_path TEXT,
                    status TEXT NOT NULL,
                    prompt_profile TEXT NOT NULL DEFAULT 'personal',
                    task_prompt TEXT,
                    prompt_config_json TEXT,
                    learner_level TEXT NOT NULL DEFAULT 'beginner',
                    learning_goal TEXT NOT NULL DEFAULT 'understand',
                    depth_mode TEXT NOT NULL DEFAULT 'standard',
                    attention_support TEXT NOT NULL DEFAULT 'adhd_friendly',
                    last_page_no INTEGER NOT NULL DEFAULT 1,
                    latest_run_id TEXT,
                    total_pages INTEGER NOT NULL DEFAULT 0,
                    processed_pages INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    language_default TEXT NOT NULL DEFAULT 'zh',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    job_type TEXT NOT NULL DEFAULT 'translate_document',
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS document_runs (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    job_id TEXT,
                    trigger_type TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    target_page_no INTEGER,
                    status TEXT NOT NULL,
                    error TEXT,
                    prompt_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    learning_profile_json TEXT NOT NULL DEFAULT '{}',
                    model_chain_json TEXT NOT NULL DEFAULT '[]',
                    quality_stats_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
                    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS pages (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    page_no INTEGER NOT NULL,
                    text_content TEXT NOT NULL,
                    formulas_json TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    page_width REAL NOT NULL DEFAULT 0,
                    page_height REAL NOT NULL DEFAULT 0,
                    layout_blocks_json TEXT NOT NULL DEFAULT '[]',
                    translation_blocks_json TEXT NOT NULL DEFAULT '[]',
                    untranslated_blocks_json TEXT NOT NULL DEFAULT '[]',
                    literal_translation TEXT NOT NULL DEFAULT '',
                    translation_overlay_status TEXT NOT NULL DEFAULT 'pending',
                    translation_updated_at TEXT,
                    group_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(document_id, page_no),
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS groups_table (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    page_start INTEGER NOT NULL,
                    page_end INTEGER NOT NULL,
                    summary TEXT,
                    key_concepts_json TEXT NOT NULL,
                    prerequisites_json TEXT NOT NULL,
                    misconceptions_json TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS global_memory (
                    document_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    glossary_json TEXT NOT NULL,
                    knowledge_map_json TEXT NOT NULL,
                    learning_arc_json TEXT NOT NULL DEFAULT '[]',
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS explanations (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    page_no INTEGER NOT NULL,
                    language TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    quality_score REAL NOT NULL,
                    quality_json TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(document_id, page_no, language, version),
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    page_no INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    answer_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS prompt_configs (
                    id TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    can_use_shared_key INTEGER NOT NULL DEFAULT 0,
                    shared_key_providers_json TEXT NOT NULL DEFAULT '{}',
                    permissions_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_tokens (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_key_invites (
                    token TEXT PRIMARY KEY,
                    created_by_user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    max_uses INTEGER NOT NULL,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS registration_invites (
                    code TEXT PRIMARY KEY,
                    created_by_user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    max_uses INTEGER NOT NULL,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    revoked_at TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS registration_invite_uses (
                    id TEXT PRIMARY KEY,
                    invite_code TEXT NOT NULL,
                    used_by_user_id TEXT,
                    used_username TEXT NOT NULL,
                    used_email TEXT,
                    used_ip TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(invite_code) REFERENCES registration_invites(code) ON DELETE CASCADE,
                    FOREIGN KEY(used_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS email_verification_codes (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_llm_keys (
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    encrypted_key TEXT NOT NULL,
                    last4 TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, provider),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS llm_settings (
                    id TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pages_document_page ON pages(document_id, page_no);
                CREATE INDEX IF NOT EXISTS idx_groups_document ON groups_table(document_id);
                CREATE INDEX IF NOT EXISTS idx_explanations_lookup ON explanations(document_id, page_no, language, version DESC);
                CREATE INDEX IF NOT EXISTS idx_chats_document_page ON chats(document_id, page_no, created_at);
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id, expires_at);
                CREATE INDEX IF NOT EXISTS idx_shared_key_invites_exp ON shared_key_invites(expires_at);
                CREATE INDEX IF NOT EXISTS idx_registration_invites_exp ON registration_invites(expires_at, revoked_at);
                CREATE INDEX IF NOT EXISTS idx_registration_invite_uses_code ON registration_invite_uses(invite_code, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_email_codes_email_created ON email_verification_codes(email, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_user_llm_keys_provider ON user_llm_keys(provider, updated_at DESC);
                """
            )

            # Lightweight schema migrations for existing local databases.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
            if "owner_user_id" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN owner_user_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_owner_created ON documents(owner_user_id, created_at)")
            if "prompt_profile" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN prompt_profile TEXT NOT NULL DEFAULT 'personal'")
            if "task_prompt" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN task_prompt TEXT")
            if "prompt_config_json" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN prompt_config_json TEXT")
            if "learner_level" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN learner_level TEXT NOT NULL DEFAULT 'beginner'")
            if "learning_goal" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN learning_goal TEXT NOT NULL DEFAULT 'understand'")
            if "depth_mode" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN depth_mode TEXT NOT NULL DEFAULT 'standard'")
            if "attention_support" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN attention_support TEXT NOT NULL DEFAULT 'adhd_friendly'")
            if "last_page_no" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN last_page_no INTEGER NOT NULL DEFAULT 1")
            if "latest_run_id" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN latest_run_id TEXT")

            job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "job_type" not in job_columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'translate_document'")

            page_columns = {row[1] for row in conn.execute("PRAGMA table_info(pages)").fetchall()}
            if "page_width" not in page_columns:
                conn.execute("ALTER TABLE pages ADD COLUMN page_width REAL NOT NULL DEFAULT 0")
            if "page_height" not in page_columns:
                conn.execute("ALTER TABLE pages ADD COLUMN page_height REAL NOT NULL DEFAULT 0")
            if "layout_blocks_json" not in page_columns:
                conn.execute("ALTER TABLE pages ADD COLUMN layout_blocks_json TEXT NOT NULL DEFAULT '[]'")
            if "translation_blocks_json" not in page_columns:
                conn.execute("ALTER TABLE pages ADD COLUMN translation_blocks_json TEXT NOT NULL DEFAULT '[]'")
            if "untranslated_blocks_json" not in page_columns:
                conn.execute("ALTER TABLE pages ADD COLUMN untranslated_blocks_json TEXT NOT NULL DEFAULT '[]'")
            if "literal_translation" not in page_columns:
                conn.execute("ALTER TABLE pages ADD COLUMN literal_translation TEXT NOT NULL DEFAULT ''")
            if "translation_overlay_status" not in page_columns:
                conn.execute("ALTER TABLE pages ADD COLUMN translation_overlay_status TEXT NOT NULL DEFAULT 'pending'")
            if "translation_updated_at" not in page_columns:
                conn.execute("ALTER TABLE pages ADD COLUMN translation_updated_at TEXT")

            global_columns = {row[1] for row in conn.execute("PRAGMA table_info(global_memory)").fetchall()}
            if "learning_arc_json" not in global_columns:
                conn.execute("ALTER TABLE global_memory ADD COLUMN learning_arc_json TEXT NOT NULL DEFAULT '[]'")

            user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "email" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
            if "email_verified" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
            if "can_use_shared_key" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN can_use_shared_key INTEGER NOT NULL DEFAULT 0")
            if "shared_key_providers_json" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN shared_key_providers_json TEXT NOT NULL DEFAULT '{}'")
            if "permissions_json" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '{}'")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users(lower(email)) WHERE email IS NOT NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_document_runs_document_created ON document_runs(document_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_document_runs_status ON document_runs(status, updated_at DESC)")

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock, self.connection() as conn:
            conn.execute(sql, params)

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock, self.connection() as conn:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            return row

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock, self.connection() as conn:
            cursor = conn.execute(sql, params)
            return cursor.fetchall()

    def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        if not seq:
            return
        with self._lock, self.connection() as conn:
            conn.executemany(sql, seq)
