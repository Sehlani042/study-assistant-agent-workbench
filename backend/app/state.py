from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from app.config import Settings
from app.pipeline.worker import PipelineWorker
from app.services.store import Store


@dataclass
class AppState:
    settings: Settings
    store: Store
    worker: PipelineWorker
    _runtime_user_keys: dict[str, str] | None = None
    _runtime_user_keys_lock: RLock | None = None

    def __post_init__(self) -> None:
        if self._runtime_user_keys is None:
            self._runtime_user_keys = {}
        if self._runtime_user_keys_lock is None:
            self._runtime_user_keys_lock = RLock()

    def set_runtime_user_key(self, *, user_id: str, api_key: str) -> None:
        if not user_id:
            return
        key = str(api_key or "").strip()
        if not key:
            return
        assert self._runtime_user_keys_lock is not None
        assert self._runtime_user_keys is not None
        with self._runtime_user_keys_lock:
            self._runtime_user_keys[user_id] = key

    def clear_runtime_user_key(self, *, user_id: str) -> None:
        if not user_id:
            return
        assert self._runtime_user_keys_lock is not None
        assert self._runtime_user_keys is not None
        with self._runtime_user_keys_lock:
            self._runtime_user_keys.pop(user_id, None)

    def get_runtime_user_key(self, *, user_id: str) -> str:
        if not user_id:
            return ""
        assert self._runtime_user_keys_lock is not None
        assert self._runtime_user_keys is not None
        with self._runtime_user_keys_lock:
            return str(self._runtime_user_keys.get(user_id, ""))

    def has_runtime_user_key(self, *, user_id: str) -> bool:
        return bool(self.get_runtime_user_key(user_id=user_id))
