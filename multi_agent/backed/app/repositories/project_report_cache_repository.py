"""项目级 AI 报告总结缓存。

按 (project_id, project_root) 存储，跨 session 共享，
避免同一项目每换一个 session 就重新生成报告总结。

存储路径：app/project_report_caches/{safe_key}.json
其中 safe_key = "{project_id}__{project_root_hash8}"
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class ProjectReportCacheRepository:
    STORAGE_DIR_NAME = "project_report_caches"

    def __init__(self) -> None:
        current_file = Path(__file__).resolve()
        self._base_dir = current_file.parent.parent
        self._storage_root = self._base_dir / self.STORAGE_DIR_NAME
        self._storage_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def load(self, project_id: str, project_root: str) -> dict[str, Any] | None:
        path = self._path(project_id, project_root)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def save(self, project_id: str, project_root: str, entry: dict[str, Any]) -> None:
        path = self._path(project_id, project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(entry, fh, ensure_ascii=False, indent=2)
            tmp.replace(path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def mark_running(self, project_id: str, project_root: str, generation_version: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        existing = self.load(project_id, project_root) or {}
        existing.update(
            {
                "status": "running",
                "project_id": project_id,
                "project_root": project_root,
                "generation_version": generation_version,
                "started_at": now,
                "updated_at": now,
                "error": "",
            }
        )
        self.save(project_id, project_root, existing)

    def mark_ready(
        self,
        project_id: str,
        project_root: str,
        analysis: dict[str, Any],
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        entry = {
            "status": "ready",
            "project_id": project_id,
            "project_root": project_root,
            "generation_version": analysis.get("generation_version"),
            "analysis": analysis,
            "updated_at": now,
            "error": "",
        }
        self.save(project_id, project_root, entry)

    def mark_failed(self, project_id: str, project_root: str, error: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        existing = self.load(project_id, project_root) or {}
        existing.update(
            {
                "status": "failed",
                "updated_at": now,
                "error": error,
                "analysis": None,
            }
        )
        self.save(project_id, project_root, existing)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _path(self, project_id: str, project_root: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_\-]", "_", project_id)[:64]
        root_hash = hashlib.sha1(project_root.encode("utf-8", errors="replace")).hexdigest()[:8]
        return self._storage_root / f"{safe_id}__{root_hash}.json"


project_report_cache_repository = ProjectReportCacheRepository()
