from __future__ import annotations

import copy
import hashlib
import json
import threading
from time import monotonic
from typing import Any


class ProjectSnapshotService:
    def __init__(self, *, ttl_seconds: float = 3600.0, max_entries: int = 256) -> None:
        self._ttl_seconds = max(60.0, ttl_seconds)
        self._max_entries = max(8, max_entries)
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _stable_hash(payload: Any) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha1(encoded.encode("utf-8", errors="ignore")).hexdigest()[:16]

    @classmethod
    def build_evidence_snapshot_key(
        cls,
        *,
        project_version: str,
        selected_files: list[str],
    ) -> str:
        return f"evidence:{project_version}:{cls._stable_hash(selected_files)}"

    @classmethod
    def build_context_snapshot_key(
        cls,
        *,
        root: str,
        include_html_body: bool,
    ) -> str:
        return f"context:{cls._stable_hash({'root': root, 'include_html_body': include_html_body})}"

    @classmethod
    def build_post_evidence_snapshot_key(
        cls,
        *,
        project_version: str,
        selected_files: list[str],
        evidence_cards: list[dict[str, Any]],
        quarantined_cards: list[dict[str, Any]],
        evidence_status: list[dict[str, Any]],
        evidence_conflicts: list[dict[str, Any]],
        user_assertions: list[dict[str, Any]],
    ) -> str:
        payload = {
            "selected_files": selected_files,
            "evidence_cards": evidence_cards,
            "quarantined_cards": quarantined_cards,
            "evidence_status": evidence_status,
            "evidence_conflicts": evidence_conflicts,
            "user_assertions": user_assertions,
        }
        return f"post-evidence:{project_version}:{cls._stable_hash(payload)}"

    @classmethod
    def build_assay_snapshot_key(
        cls,
        *,
        project_version: str,
        selected_files: list[str],
        evidence_cards: list[dict[str, Any]],
        experiment_design: dict[str, Any],
    ) -> str:
        payload = {
            "selected_files": selected_files,
            "evidence_cards": evidence_cards,
            "experiment_design": experiment_design,
        }
        return f"assay:{project_version}:{cls._stable_hash(payload)}"

    def get(self, snapshot_key: str) -> dict[str, Any] | None:
        now = monotonic()
        with self._lock:
            cached = self._entries.get(snapshot_key)
            if cached is None:
                return None
            expires_at, payload = cached
            if expires_at <= now:
                self._entries.pop(snapshot_key, None)
                return None
            return copy.deepcopy(payload)

    def set(self, snapshot_key: str, payload: dict[str, Any]) -> None:
        now = monotonic()
        with self._lock:
            expired_keys = [
                key
                for key, (expires_at, _) in self._entries.items()
                if expires_at <= now
            ]
            for key in expired_keys:
                self._entries.pop(key, None)
            self._entries[snapshot_key] = (now + self._ttl_seconds, copy.deepcopy(payload))
            while len(self._entries) > self._max_entries:
                self._entries.pop(next(iter(self._entries)))


project_snapshot_service = ProjectSnapshotService()
