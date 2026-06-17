from __future__ import annotations

import asyncio
from typing import Any

from multi_agent.backed.app.services.business_agent.runtime_service import (
    business_agent_runtime_service,
)


class ProjectAnalysisWorkflowService:
    @staticmethod
    def identify_project(
        question: str,
        project_id: str | None,
        user_id: str,
        session_id: str,
        project_root: str | None = None,
    ) -> dict[str, Any]:
        return business_agent_runtime_service.identify_project(
            question=question,
            project_id=project_id,
            user_id=user_id,
            session_id=session_id,
            project_root=project_root,
        )

    @classmethod
    async def arun_analysis(
        cls,
        question: str,
        project_id: str | None,
        user_id: str,
        session_id: str,
        project_root: str | None = None,
        max_evidence_files: int = 40,
    ) -> dict[str, Any]:
        return await business_agent_runtime_service.run(
            question=question,
            project_id=project_id,
            user_id=user_id,
            session_id=session_id,
            project_root=project_root,
            max_evidence_files=max_evidence_files,
        )

    @classmethod
    def run_analysis(
        cls,
        question: str,
        project_id: str | None,
        user_id: str,
        session_id: str,
        project_root: str | None = None,
        max_evidence_files: int = 40,
    ) -> dict[str, Any]:
        return asyncio.run(
            cls.arun_analysis(
                question=question,
                project_id=project_id,
                user_id=user_id,
                session_id=session_id,
                project_root=project_root,
                max_evidence_files=max_evidence_files,
            )
        )


project_analysis_workflow_service = ProjectAnalysisWorkflowService()
