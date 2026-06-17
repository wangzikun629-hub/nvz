from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from multi_agent.backed.app.multi_agent.project_progress import publish_project_progress
from multi_agent.backed.app.services.business_agent.workspace import ProjectWorkspace


class BusinessProgressService:
    def emit(
        self,
        message: str,
        *,
        stage: str,
        status: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        publish_project_progress(message, stage=stage, status=status, detail=detail)

    def update_progress_file(
        self,
        workspace: ProjectWorkspace,
        *,
        stage: str,
        message: str,
        status: str = "in_progress",
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "stage": stage,
            "message": message,
            "status": status,
            "detail": detail or {},
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        workspace.progress_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload

    def report(
        self,
        workspace: ProjectWorkspace,
        *,
        stage: str,
        message: str,
        status: str = "in_progress",
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self.update_progress_file(
            workspace,
            stage=stage,
            message=message,
            status=status,
            detail=detail,
        )
        self.emit(message, stage=stage, status=status, detail=detail)
        return payload


business_progress_service = BusinessProgressService()
