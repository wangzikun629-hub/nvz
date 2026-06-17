from __future__ import annotations

from time import perf_counter
from typing import Any

from multi_agent.backed.app.infrastructure.tools.local.project_reader import resolve_project_root
from multi_agent.backed.app.services.project_analysis_service import ProjectAnalysisService


class ProjectAnalysisHarnessRunner:
    def run(self, case: dict[str, Any]) -> dict[str, Any]:
        started_at = perf_counter()
        project_id = str(case["project_id"])
        project_root = str(resolve_project_root(project_id, case.get("project_root")))
        result = ProjectAnalysisService.analyze(
            project_id=project_id,
            question=str(case["question"]),
            project_root=project_root,
            max_evidence_files=int(case.get("max_evidence_files", 8)),
            planning_hints=case.get("planning_hints") or {"force_include_html_body": True},
        )
        result["_harness"] = {
            "case_id": case.get("id"),
            "target": case.get("target", "project_analysis"),
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            "project_root": project_root,
        }
        return result
