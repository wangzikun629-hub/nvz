from __future__ import annotations

import copy
import json
import os
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multi_agent.backed.app.infrastructure.tools.local.project_reader import list_project_files
from multi_agent.backed.app.services.business_agent.background_task_service import (
    background_task_service,
)


@dataclass
class ProjectWorkspace:
    project_id: str
    project_root: Path
    user_id: str
    session_id: str
    workspace_root: Path
    metadata_path: Path
    task_plan_path: Path
    progress_path: Path
    experience_rules_path: Path

    @staticmethod
    def _safe_path_part(value: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value or "").strip("._")
        return cleaned or "default"

    @classmethod
    def _default_workspace_base(cls) -> Path:
        configured = os.getenv("PROJECT_WORKSPACE_DIR", "").strip()
        if configured:
            return Path(configured)
        return Path(__file__).resolve().parents[5] / "project_workspaces"

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        project_root: Path,
        user_id: str,
        session_id: str,
    ) -> "ProjectWorkspace":
        try:
            project_root_key = str(project_root.resolve())
        except (OSError, RuntimeError):
            project_root_key = str(project_root)
        project_hash = hashlib.sha1(project_root_key.encode("utf-8")).hexdigest()[:12]
        workspace_root = (
            cls._default_workspace_base()
            / f"{cls._safe_path_part(project_id)}_{project_hash}"
            / cls._safe_path_part(user_id)
            / cls._safe_path_part(session_id)
        )
        workspace_root.mkdir(parents=True, exist_ok=True)
        return cls(
            project_id=project_id,
            project_root=project_root,
            user_id=user_id,
            session_id=session_id,
            workspace_root=workspace_root,
            metadata_path=workspace_root / "workspace.json",
            task_plan_path=workspace_root / "task_plan.json",
            progress_path=workspace_root / "progress.json",
            experience_rules_path=workspace_root / "experience_rules.json",
        )

    def snapshot(self) -> dict[str, Any]:
        project_files = list_project_files(self.project_root, limit=200)
        return {
            "project_id": self.project_id,
            "project_root": str(self.project_root),
            "user_id": self.user_id,
            "session_id": self.session_id,
            "workspace_root": str(self.workspace_root),
            "project_file_count": len(project_files),
            "files": [
                {
                    "path": str(item.path.relative_to(self.project_root)),
                    "kind": item.kind,
                }
                for item in project_files
            ],
        }

    def save_snapshot(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        self.save_snapshot_payload(snapshot)
        return snapshot

    def save_snapshot_payload(self, snapshot: dict[str, Any], *, background: bool = True) -> None:
        if background:
            background_task_service.submit(
                "save_workspace_snapshot",
                self._write_snapshot_payload,
                self.metadata_path,
                copy.deepcopy(snapshot),
            )
            return
        self._write_snapshot_payload(self.metadata_path, snapshot)

    @staticmethod
    def _write_snapshot_payload(metadata_path: Path, snapshot: dict[str, Any]) -> None:
        metadata_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
