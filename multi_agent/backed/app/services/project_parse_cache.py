from __future__ import annotations

import copy
import threading
from pathlib import Path
from time import perf_counter
from typing import Any

from multi_agent.backed.app.infrastructure.logging.logger import logger


class ProjectParseCache:
    # ── File-parse cache ──────────────────────────────────────────────────
    _EVIDENCE_PARSE_WORKERS = 4
    _FILE_PARSE_CACHE: dict[tuple[str, int, str], dict[str, Any]] = {}
    _FILE_PARSE_CACHE_MAX_ENTRIES = 512
    _FILE_PARSE_CACHE_LOCK = threading.Lock()

    # ── Project-context cache ─────────────────────────────────────────────
    _PROJECT_CONTEXT_CACHE_TTL_SECONDS = 120.0
    _PROJECT_CONTEXT_CACHE_MAX_ENTRIES = 128
    _PROJECT_CONTEXT_CACHE: dict[tuple[str, bool], tuple[float, dict[str, Any]]] = {}
    _PROJECT_CONTEXT_IN_FLIGHT: dict[tuple[str, bool], threading.Event] = {}
    _PROJECT_CONTEXT_LOCK = threading.Lock()

    # ── File-parse helpers ────────────────────────────────────────────────

    def _cache_key(self, file_path: Path, parser_kind: str) -> tuple[str, int, str]:
        stat = file_path.stat()
        return (str(file_path.resolve()), stat.st_mtime_ns, parser_kind)

    @staticmethod
    def _clone_payload(payload: dict[str, Any]) -> dict[str, Any]:
        if not payload:
            return {}
        cloned: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                cloned[key] = dict(value)
            elif isinstance(value, list):
                cloned[key] = [dict(item) if isinstance(item, dict) else item for item in value]
            else:
                cloned[key] = value
        return cloned

    def _get_cached_parse(self, file_path: Path, parser_kind: str) -> dict[str, Any] | None:
        with self._FILE_PARSE_CACHE_LOCK:
            cached = self._FILE_PARSE_CACHE.get(self._cache_key(file_path, parser_kind))
        if cached is None:
            return None
        return self._clone_payload(cached)

    def _set_cached_parse(self, file_path: Path, parser_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        cloned = self._clone_payload(payload)
        cache_key = self._cache_key(file_path, parser_kind)
        resolved_path = cache_key[0]
        with self._FILE_PARSE_CACHE_LOCK:
            stale_keys = [
                key
                for key in self._FILE_PARSE_CACHE
                if key[0] == resolved_path and key[2] == parser_kind and key != cache_key
            ]
            for key in stale_keys:
                self._FILE_PARSE_CACHE.pop(key, None)
            self._FILE_PARSE_CACHE[cache_key] = cloned
            while len(self._FILE_PARSE_CACHE) > self._FILE_PARSE_CACHE_MAX_ENTRIES:
                self._FILE_PARSE_CACHE.pop(next(iter(self._FILE_PARSE_CACHE)))
        return self._clone_payload(cloned)

    # ── Project-context cache helpers ─────────────────────────────────────

    def _project_context_key(self, root: Path, include_html_body: bool) -> tuple[str, bool]:
        return (str(root.resolve()), include_html_body)

    @staticmethod
    def _clone_project_context(payload: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(payload)

    def _get_cached_project_context(self, root: Path, include_html_body: bool) -> dict[str, Any] | None:
        key = self._project_context_key(root, include_html_body)
        now = perf_counter()
        with self._PROJECT_CONTEXT_LOCK:
            cached = self._PROJECT_CONTEXT_CACHE.get(key)
            if cached is None:
                return None
            cached_at, payload = cached
            if now - cached_at > self._PROJECT_CONTEXT_CACHE_TTL_SECONDS:
                self._PROJECT_CONTEXT_CACHE.pop(key, None)
                return None
            return self._clone_project_context(payload)

    def _set_cached_project_context(
        self,
        root: Path,
        include_html_body: bool,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        cloned = self._clone_project_context(payload)
        with self._PROJECT_CONTEXT_LOCK:
            now = perf_counter()
            expired = [
                key
                for key, (cached_at, _) in self._PROJECT_CONTEXT_CACHE.items()
                if now - cached_at > self._PROJECT_CONTEXT_CACHE_TTL_SECONDS
            ]
            for key in expired:
                self._PROJECT_CONTEXT_CACHE.pop(key, None)
            self._PROJECT_CONTEXT_CACHE[self._project_context_key(root, include_html_body)] = (now, cloned)
            while len(self._PROJECT_CONTEXT_CACHE) > self._PROJECT_CONTEXT_CACHE_MAX_ENTRIES:
                self._PROJECT_CONTEXT_CACHE.pop(next(iter(self._PROJECT_CONTEXT_CACHE)))
        return self._clone_project_context(cloned)

    def build_cached_project_context(self, root: Path, include_html_body: bool) -> dict[str, Any]:
        """Build or return cached project context, with in-flight deduplication."""
        # Deferred import to avoid circular dependency: context builder imports this module.
        from multi_agent.backed.app.services.project_context_builder_service import (
            project_context_builder_service,
        )
        from multi_agent.backed.app.services.business_agent.project_snapshot_service import (
            project_snapshot_service,
        )

        key = self._project_context_key(root, include_html_body)
        context_snapshot_key = project_snapshot_service.build_context_snapshot_key(
            root=str(root.resolve()),
            include_html_body=include_html_body,
        )
        while True:
            cached = self._get_cached_project_context(root, include_html_body)
            if cached is not None:
                logger.info(
                    "project_analysis stage=build_context_cache root=%s include_html_body=%s status=hit",
                    str(root),
                    include_html_body,
                )
                return cached

            with self._PROJECT_CONTEXT_LOCK:
                event = self._PROJECT_CONTEXT_IN_FLIGHT.get(key)
                if event is None:
                    event = threading.Event()
                    self._PROJECT_CONTEXT_IN_FLIGHT[key] = event
                    owner = True
                else:
                    owner = False

            if owner:
                try:
                    snapshot_payload = project_snapshot_service.get(context_snapshot_key)
                    if snapshot_payload is not None:
                        logger.info(
                            "project_analysis stage=context_snapshot root=%s include_html_body=%s snapshot=hit",
                            str(root),
                            include_html_body,
                        )
                        payload = snapshot_payload.get("project_context", {}) or {}
                    else:
                        logger.info(
                            "project_analysis stage=context_snapshot root=%s include_html_body=%s snapshot=miss",
                            str(root),
                            include_html_body,
                        )
                        payload = project_context_builder_service.build_project_context(
                            root, include_html_body=include_html_body
                        )
                        project_snapshot_service.set(
                            context_snapshot_key,
                            {
                                "project_context": payload,
                                "experiment_design": payload.get("experiment_design", {}),
                                "evidence_catalog_summary": payload.get("evidence_catalog_summary", {}),
                            },
                        )
                    return self._set_cached_project_context(root, include_html_body, payload)
                finally:
                    with self._PROJECT_CONTEXT_LOCK:
                        self._PROJECT_CONTEXT_IN_FLIGHT.pop(key, None)
                        event.set()

            logger.info(
                "project_analysis stage=build_context_cache root=%s include_html_body=%s status=wait_in_flight",
                str(root),
                include_html_body,
            )
            event.wait(timeout=30.0)


project_parse_cache = ProjectParseCache()
