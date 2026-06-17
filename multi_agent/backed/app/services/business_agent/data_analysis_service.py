from __future__ import annotations

from typing import Any

from multi_agent.backed.app.services.project_analysis_service import ProjectAnalysisService
from multi_agent.backed.app.services.business_agent.workspace import ProjectWorkspace


class DataAnalysisService:
    def run(
        self,
        *,
        workspace: ProjectWorkspace,
        question: str,
        max_evidence_files: int,
        planning_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return ProjectAnalysisService.analyze(
            project_id=workspace.project_id,
            question=question,
            project_root=str(workspace.project_root),
            max_evidence_files=max_evidence_files,
            planning_hints=planning_hints,
        )


data_analysis_service = DataAnalysisService()
