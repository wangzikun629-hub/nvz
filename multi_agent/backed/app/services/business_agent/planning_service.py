from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from multi_agent.backed.app.services.project_analysis_service import ProjectAnalysisService
from multi_agent.backed.app.services.business_agent.background_task_service import (
    background_task_service,
)
from multi_agent.backed.app.services.business_agent.workspace import ProjectWorkspace


@dataclass
class PlanningService:
    DEFAULT_MAX_EVIDENCE_FILES: int = 40

    def build_plan(
        self,
        *,
        workspace: ProjectWorkspace,
        question: str,
        max_evidence_files: int,
        experience_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        question_tags = ProjectAnalysisService._infer_question_types(question)
        output_mode = "report" if self._is_report_request(question) else "qa"
        experience_summary = experience_summary or {}
        experience_hints = self._build_experience_hints(question_tags, experience_summary)
        question_route = experience_summary.get("question_route") or experience_hints.get("question_route") or {}

        plan = {
            "question": question,
            "question_tags": question_tags,
            "primary_question_type": question_tags[0],
            "question_route": question_route,
            "output_mode": output_mode,
            "max_evidence_files": max_evidence_files,
            "experience_inputs": experience_hints,
            "planning_summary": {
                "focus": question_tags[0],
                "intent": question_route.get("intent", ""),
                "route": question_route.get("route", ""),
                "use_knowledge": True,
                "use_experience": experience_hints.get("has_experience", False),
                "use_global_experience": experience_hints.get("has_global_experience", False),
                "report_requested": output_mode == "report",
            },
            "steps": [
                {
                    "step_id": "identify_scope",
                    "name": "identify_scope",
                    "status": "completed",
                    "description": "确认项目、问题类型和输出模式",
                },
                {
                    "step_id": "analyze_project_data",
                    "name": "analyze_project_data",
                    "status": "pending",
                    "description": "基于项目文件执行数据分析并收集证据",
                },
                {
                    "step_id": "execute_agent_loop",
                    "name": "execute_agent_loop",
                    "status": "pending",
                    "description": "执行最多 3 轮 Plan→Tool→Observe，只读补充缺失证据。",
                },
                {
                    "step_id": "verify_claims",
                    "name": "verify_claims",
                    "status": "pending",
                    "description": "独立校验 Claim 的数字、分母、物种和阈值来源。",
                },
                {
                    "step_id": "consult_experience",
                    "name": "consult_experience",
                    "status": "completed" if experience_hints.get("has_experience", False) else "skipped",
                    "description": "读取项目经验记忆，用于指导规划和补充历史结论",
                    "detail": experience_hints,
                },
                {
                    "step_id": "retrieve_knowledge",
                    "name": "retrieve_knowledge",
                    "status": "pending",
                    "description": "按问题类型检索知识库补充说明",
                },
                {
                    "step_id": "compose_response",
                    "name": "compose_response",
                    "status": "pending",
                    "description": "生成最终问答或项目报告",
                },
            ],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.save_plan(workspace, plan)
        return plan

    @staticmethod
    def _write_plan(workspace: ProjectWorkspace, plan: dict[str, Any]) -> None:
        workspace.task_plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save_plan(self, workspace: ProjectWorkspace, plan: dict[str, Any], *, background: bool = True) -> None:
        if background:
            background_task_service.submit("save_plan", self._write_plan, workspace, copy.deepcopy(plan))
            return
        self._write_plan(workspace, plan)

    def update_step_status(
        self,
        workspace: ProjectWorkspace,
        plan: dict[str, Any],
        step_id: str,
        status: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for step in plan.get("steps", []):
            if step.get("step_id") == step_id:
                step["status"] = status
                if detail:
                    step["detail"] = detail
                break
        plan["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.save_plan(workspace, plan)
        return plan

    @staticmethod
    def _is_report_request(question: str) -> bool:
        normalized = (question or "").lower()
        return any(token in normalized for token in ("report", "报告", "总结", "汇总"))

    @staticmethod
    def _build_experience_hints(question_tags: list[str], experience_summary: dict[str, Any]) -> dict[str, Any]:
        return {
            "has_experience": experience_summary.get("has_experience", False),
            "has_global_experience": experience_summary.get("has_global_experience", False),
            "matched_findings": experience_summary.get("matched_findings", []),
            "matched_question_types": experience_summary.get("matched_question_types", []),
            "global_similar_cases": experience_summary.get("global_similar_cases", []),
            "global_experience_rules": experience_summary.get("global_experience_rules", []),
            "global_structured_experience_rules": experience_summary.get("global_structured_experience_rules", []),
            "recent_question_count": experience_summary.get("recent_question_count", 0),
            "has_report_excerpt": experience_summary.get("has_report_excerpt", False),
            "prioritized_evidence_hints": experience_summary.get("prioritized_evidence_hints", []),
            "prioritized_metrics": experience_summary.get("prioritized_metrics", []),
            "experience_rules": experience_summary.get("experience_rules", []),
            "structured_experience_rules": experience_summary.get("structured_experience_rules", []),
            "rule_library_size": experience_summary.get("rule_library_size", 0),
            "active_rule_count": experience_summary.get("active_rule_count", 0),
            "evidence_scope_adjustment": experience_summary.get("evidence_scope_adjustment", {}),
            "planning_note": experience_summary.get("planning_note", "no_historical_evidence_priority"),
            "question_route": experience_summary.get("question_route", {}),
        }


planning_service = PlanningService()
