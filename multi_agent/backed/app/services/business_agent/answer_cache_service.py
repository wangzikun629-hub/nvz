from __future__ import annotations

import copy
import threading
from time import monotonic
from typing import Any


class AnswerCacheService:
    def __init__(self, *, ttl_seconds: float = 300.0, max_entries: int = 512) -> None:
        self._ttl_seconds = max(1.0, ttl_seconds)
        self._max_entries = max(1, max_entries)
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def normalize_question(question: str) -> str:
        return " ".join(str(question or "").strip().lower().split())

    @staticmethod
    def build_knowledge_status(retrieval_payload: dict[str, Any] | None) -> str:
        payload = retrieval_payload or {}
        status = str(payload.get("status") or "ok").strip() or "ok"
        document_count = len(payload.get("documents", []) or [])
        return f"{status}:{document_count}"

    @classmethod
    def build_cache_key(
        cls,
        *,
        project_id: str,
        project_version: str,
        question_route: dict[str, Any] | str | None,
        question: str,
        knowledge_status: str,
    ) -> str:
        if isinstance(question_route, dict):
            route_value = str(question_route.get("route") or question_route.get("intent") or "")
        else:
            route_value = str(question_route or "")
        parts = (
            str(project_id or ""),
            str(project_version or ""),
            route_value,
            cls.normalize_question(question),
            str(knowledge_status or ""),
        )
        return "||".join(parts)

    def get(self, cache_key: str) -> dict[str, Any] | None:
        now = monotonic()
        with self._lock:
            cached = self._entries.get(cache_key)
            if cached is None:
                return None
            expires_at, payload = cached
            if expires_at <= now:
                self._entries.pop(cache_key, None)
                return None
            return copy.deepcopy(payload)

    def set(self, cache_key: str, payload: dict[str, Any]) -> None:
        now = monotonic()
        with self._lock:
            expired_keys = [
                key
                for key, (expires_at, _) in self._entries.items()
                if expires_at <= now
            ]
            for key in expired_keys:
                self._entries.pop(key, None)
            self._entries[cache_key] = (now + self._ttl_seconds, copy.deepcopy(payload))
            while len(self._entries) > self._max_entries:
                self._entries.pop(next(iter(self._entries)))


answer_cache_service = AnswerCacheService()
