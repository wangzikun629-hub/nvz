from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from multi_agent.backed.app.infrastructure.tools.local.project_reader import resolve_project_root
from multi_agent.backed.app.services.business_agent.runtime_service import business_agent_runtime_service


class BusinessAgentHarnessRunner:
    def run(self, case: dict[str, Any]) -> dict[str, Any]:
        started_at = perf_counter()
        project_id = case.get("project_id")
        project_root = case.get("project_root")
        if project_id:
            project_root = str(resolve_project_root(str(project_id), project_root))
        result = asyncio.run(
            business_agent_runtime_service.run(
                question=str(case["question"]),
                project_id=project_id,
                project_root=project_root,
                user_id=str(case.get("user_id", "harness_user")),
                session_id=str(case.get("session_id", f"harness_{case.get('id', 'case')}")),
                max_evidence_files=int(case.get("max_evidence_files", 8)),
            )
        )
        result["_harness"] = {
            "case_id": case.get("id"),
            "target": case.get("target", "business_agent"),
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            "project_root": project_root,
        }
        return result
