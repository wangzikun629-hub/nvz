from __future__ import annotations

import asyncio
import copy

from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.multi_agent.project_progress import (
    publish_project_answer_delta,
    publish_project_answer_final,
)
from multi_agent.backed.app.services.project_analysis_service import ProjectAnalysisService
from multi_agent.backed.app.services.project_chart_service import project_chart_service
from multi_agent.backed.app.services.project_comparison_service import project_comparison_service
from multi_agent.backed.app.services.project_context_intent_service import project_context_intent_service
from multi_agent.backed.app.services.project_memory_service import project_memory_service
from multi_agent.backed.app.services.project_locator_service import project_locator_service
from multi_agent.backed.app.services.project_session_state_service import project_session_state_service
from multi_agent.backed.app.services.question_router_service import question_router_service
from multi_agent.backed.app.services.business_agent.data_analysis_service import (
    data_analysis_service,
)
from multi_agent.backed.app.services.business_agent.analysis_planner_service import (
    analysis_planner_service,
)
from multi_agent.backed.app.services.business_agent.answer_cache_service import (
    answer_cache_service,
)
from multi_agent.backed.app.services.business_agent.background_task_service import (
    background_task_service,
)
from multi_agent.backed.app.services.business_agent.concurrency_service import (
    business_agent_concurrency_service,
)
from multi_agent.backed.app.services.business_agent.experience_service import (
    experience_service,
)
from multi_agent.backed.app.services.business_agent.knowledge_service import (
    knowledge_augmentation_service,
)
from multi_agent.backed.app.services.business_agent.harness_guard_service import (
    business_harness_guard_service,
)
from multi_agent.backed.app.services.business_agent.semantic_guard_service import (
    business_semantic_guard_service,
)
from multi_agent.backed.app.services.business_agent.answer_quality_service import (
    business_answer_quality_service,
)
from multi_agent.backed.app.services.business_agent.fact_verification_service import (
    fact_verification_service,
)
from multi_agent.backed.app.services.business_agent.planning_service import (
    planning_service,
)
from multi_agent.backed.app.services.business_agent.progress_service import (
    business_progress_service,
)
from multi_agent.backed.app.services.business_agent.response_service import (
    business_response_service,
)
from multi_agent.backed.app.services.business_agent.workspace import ProjectWorkspace


class BusinessAgentRuntimeService:
    KNOWLEDGE_RETRIEVAL_TIMEOUT_SECONDS = 8.0
    PROJECT_ANALYSIS_TIMEOUT_SECONDS = 25.0
    AI_REPORT_SUMMARY_VERSION = "project-review-v3"
    AUTO_HTML_REPORT_QUESTION = "\u603b\u7ed3\u4e00\u4e0b\u8fd9\u4e2a\u9879\u76ee"
    AUTO_REPORT_SUMMARY_ENABLED = True
    _auto_report_tasks: set[str] = set()
    CHART_METRIC_MAP = {
        "adapter_percent": "adapter",
        "mapping_rate_percent": "mapping",
        "unique_mapping_rate_percent": "unique",
        "duplicate_rate_percent": "duplicate",
        "mt_rate_percent": "chrmt_pt",
        "frip_ratio": "frip",
        "q30_ratio": "q30",
        "correlation": "correlation",
        "peak_count": "peak",
    }
    METRIC_EXPLANATIONS = {
        "frip_ratio": "FRiP（Fraction of Reads in Peaks）表示落入 peak 区域的 reads 占比，常用于评估 CUT&Tag/ChIP-seq 信号富集质量。项目内解释时还需要结合 peak 数量、mapping、duplicate 和样本角色判断。",
        "adapter_percent": "Adapter（原始 reads 接头检出率）表示 raw reads 中检测到接头相关序列的比例。它不能单独证明 clean reads 中仍有接头残留；需要结合 clean FastQC、保留率和 fragment size 复核。",
        "mapping_rate_percent": "Mapping（比对率）表示 reads 成功比对到参考基因组的比例。偏低可能与参考基因组、物种、污染、reads 质量或高细胞器 reads 有关。",
        "unique_mapping_rate_percent": "Unique（唯一比对率）表示唯一比对到一个基因组位置的 reads 比例。偏低通常提示重复序列、污染、比对质量或文库复杂度问题。",
        "duplicate_rate_percent": "Duplicate（重复率）表示重复 reads 的比例。CUT&Tag 中需要结合真实富集片段和去重策略判断，不能只按通用测序重复率解释。",
        "mt_rate_percent": "该字段表示比对到项目所配置细胞器染色体的 reads 比例。必须先确认物种和 organelle_chroms：动物项目通常解释为线粒体，植物项目才可能同时涉及叶绿体/质体。",
        "q30_ratio": "Q30 表示测序碱基质量达到 Q30 的比例，用于评估测序质量。Q30 越高，单碱基错误率通常越低。",
        "correlation": "样本相关性通常用于评估重复样本或处理组之间信号一致性。CUT&Tag 项目中需要结合样本角色、富集强度和质控指标共同判断。",
        "peak_count": "Peak 数量表示 peak calling 识别到的富集区域数量。它需要结合 FRiP、mapping、样本角色和目标蛋白类型解释。",
    }

    @staticmethod
    def _remove_internal_fields(analysis_result: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(analysis_result)
        sanitized.pop("_internal_workflow_context", None)
        return sanitized

    # 写入缓存前，剔除占用空间大但不影响复用的字段
    _CACHE_HEAVY_FIELDS = frozenset({
        "evidence_chain",
        "evidence_cards",
        "validated_claims",
        "fact_packet",
        "reasoning_packet",
        "evidence_reasoning",
        "analysis_plan",
        "tool_diagnostics",
        "cause_graph",
        "claim_layers",
        "claim_validation",
        "agent_loop",
    })

    @classmethod
    def _slim_analysis_for_cache(cls, analysis_result: dict[str, Any]) -> dict[str, Any]:
        """剔除大字段后返回轻量副本，用于写入缓存。"""
        slimmed = {k: v for k, v in analysis_result.items() if k not in cls._CACHE_HEAVY_FIELDS}
        # project_context 里的 html_report.body/sections 也很大，单独处理
        project_context = slimmed.get("project_context")
        if isinstance(project_context, dict):
            html_report = project_context.get("html_report")
            if isinstance(html_report, dict) and ("body" in html_report or "sections" in html_report):
                stripped_html = {k: v for k, v in html_report.items() if k not in {"body", "sections"}}
                slimmed["project_context"] = {**project_context, "html_report": stripped_html}
        return slimmed

    @staticmethod
    def _publish_answer_text(answer: str) -> None:
        publish_project_answer_final(str(answer or ""))

    @staticmethod
    async def _enforce_project_answer_guard(
        *,
        answer: str,
        analysis_result: dict[str, Any],
        question_route: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]]:
        # Guard 已禁用：直接透传模型输出，离线 harness 仍可独立评测。
        return str(answer or ""), {
            "passed": True,
            "action": "disabled",
            "severity": "none",
            "violations": [],
            "review_only": True,
        }

    @classmethod
    async def _apply_answer_quality_gate(
        cls,
        *,
        answer: str,
        analysis_result: dict[str, Any],
        question_route: dict[str, Any] | None,
        harness_guard: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        fact_verification = fact_verification_service.verify(
            answer=answer,
            analysis_result=analysis_result,
        )
        analysis_result["fact_verification"] = fact_verification
        initial_quality = business_answer_quality_service.evaluate(
            answer=answer,
            analysis_result=analysis_result,
            question_route=question_route,
        )
        initial_quality.update(
            {
                "repair_attempted": False,
                "repair_applied": False,
                "enforcement_mode": "observe_only",
            }
        )
        if fact_verification.get("passed"):
            return answer, initial_quality, harness_guard

        initial_quality.update(
            {
                "repair_attempted": False,
                "repair_applied": False,
                "repair_skip_reason": "interactive_project_answers_are_observe_only",
                "original_fact_issues": fact_verification.get("issues", []),
                "enforcement_mode": "observe_only",
            }
        )
        return answer, initial_quality, harness_guard

    @staticmethod
    def _fact_blocked_answer() -> str:
        return (
            "本次生成结果未通过项目事实校验，因此系统没有发布可能包含错误数值、"
            "单位、样本角色或因果结论的原答案。请重新发起分析，系统将仅使用已验证证据重建回答。"
        )

    @staticmethod
    def _repair_covers_fact_failures(
        answer: str,
        original_verification: dict[str, Any],
        repaired_verification: dict[str, Any],
    ) -> bool:
        failed_metrics: set[str] = set()
        for issue in original_verification.get("issues", []) or []:
            if not isinstance(issue, dict):
                continue
            failed_metrics.update(
                str(item)
                for item in issue.get("metrics", []) or []
                if str(item).strip()
            )
            if issue.get("metric_id"):
                failed_metrics.add(str(issue["metric_id"]))
        if not failed_metrics:
            return True
        if int(repaired_verification.get("supported_numeric_claim_count", 0)) <= 0:
            return False
        lowered = str(answer or "").lower()
        for metric_id in failed_metrics:
            terms = fact_verification_service.METRIC_TERMS.get(metric_id, (metric_id,))
            if not any(str(term).lower() in lowered for term in terms):
                return False
        return True

    @staticmethod
    def _harness_guard_progress_message(guard: dict[str, Any]) -> str:
        action = guard.get("action")
        if action == "disabled":
            return "回答已保留模型原始输出"
        if action == "pass":
            return "回答已通过项目分析规则校验"
        if action in {"sanitized", "repaired"}:
            return "回答触发规则，已按约束修正"
        if action == "model_rewritten":
            return "回答触发规则，已由模型按约束重写并通过校验"
        if action == "semantic_review_recorded":
            return "回答已通过硬规则校验，语义复核提示已记录"
        if action == "guard_review_recorded":
            return "回答存在低风险表述，已记录规则提示并保留原回答"
        if action == "backend_repaired":
            return "模型重写未通过校验，已使用结构化证据生成受控回答"
        if action == "semantic_guard_failed":
            return "回答触发语义校验，正在尝试按约束重写"
        if action == "semantic_repaired":
            return "回答触发语义校验，已按项目规则修正"
        if action in {"blocked", "semantic_blocked", "backend_blocked"}:
            return "原回答触发规则拦截，已返回受控说明"
        return "回答规则校验已完成"

    @staticmethod
    def _infer_chart_type(question: str, metric: str) -> str | None:
        normalized = " ".join((question or "").split()).strip().lower()
        if metric == "correlation" or any(term in normalized for term in ("heatmap", "热图")):
            return "heatmap"
        if any(term in normalized for term in ("line", "折线图", "趋势")):
            return "line"
        if any(term in normalized for term in ("bar", "柱状图", "柱形图")):
            return "bar"
        return None

    @staticmethod
    def _build_chart_answer(chart_result: dict[str, Any]) -> str:
        chart_id = str(chart_result.get("chart_id") or "")
        metric = str(chart_result.get("metric") or "")
        project_id = str(chart_result.get("project_id") or "")
        data_points = chart_result.get("data_points", 0)
        source_file = str(chart_result.get("source_file") or "")
        # ```chart 代码块：页面重载后前端凭 chart_id 重新拉取 spec 渲染
        chart_block = f"```chart\n{chart_id}\n```" if chart_id else ""
        source_columns = chart_result.get("source_columns") or []
        source_columns_text = "、".join(str(item) for item in source_columns[:8]) if isinstance(source_columns, list) else str(source_columns)
        lines = [
            "## 交互图表已生成",
            "",
            f"项目：`{project_id}`　指标：`{metric}`　数据点：{data_points}",
            f"来源文件：{source_file}",
            "",
            "> 图表已在下方渲染，支持悬停查看数值、缩放、平移。",
            "",
            chart_block,
            "",
            "## 数据来源",
            f"- 图类型：{chart_result.get('chart_type', '')}",
            f"- 来源文件：{chart_result.get('source_file', '')}",
        ]
        if source_columns_text:
            lines.append(f"- 使用字段：{source_columns_text}")
        return "\n".join(lines)

    @classmethod
    def _build_non_project_answer(cls, question: str, question_route: dict[str, Any]) -> str:
        route = str(question_route.get("route") or "")
        intent = str(question_route.get("intent") or "")
        target_metrics = [str(item) for item in (question_route.get("target_metrics") or [])]

        if route == "product_help":
            return (
                "## 产品使用\n"
                "你可以先绑定或指定项目，然后使用项目分析、问题排查、图表生成和 AI报告总结。\n\n"
                "常用问法：\n"
                "- `分析一下 VZ20260427001 项目`\n"
                "- `为什么这个项目 Mapping 偏低`\n"
                "- `帮我画 T1 和 T2 的 Mapping 对比图`\n"
                "- `生成 AI报告总结`\n\n"
                "如果只是问生信概念，不需要绑定项目；如果要判断某个项目是否异常，需要先提供或绑定项目。"
            )

        if route == "project_context":
            return "已清空当前项目上下文。后续问题不会继续默认结合上一个项目，除非你重新指定或绑定项目。"

        if intent == "metric_explanation" and target_metrics:
            explanation = cls.METRIC_EXPLANATIONS.get(target_metrics[0])
            if explanation:
                return (
                    "## 指标解释\n"
                    f"{explanation}\n\n"
                    "## 说明\n"
                    "这是通用概念解释，未读取具体项目文件。若你想知道当前项目里的计算公式或样本数值，需要先绑定项目或指定项目编号。"
                )

        return (
            "## 通用回答\n"
            "这个问题当前不需要读取项目文件。我可以先做概念解释；如果你希望结合具体样本、指标数值或报告结论，请提供项目编号或先绑定项目。"
        )

    def _run_non_project_route(
        self,
        *,
        question: str,
        question_route: dict[str, Any],
        workflow_run_id: str,
        workflow_started_at: float,
        user_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        route = str(question_route.get("route") or "knowledge")
        if route == "project_context":
            project_session_state_service.clear_active_project(user_id, session_id)

        answer = self._build_non_project_answer(question, question_route)
        publish_project_answer_delta(answer)
        publish_project_answer_final(answer)
        result_payload = {
            "output_mode": route,
            "answer": answer,
            "report": "",
            "knowledge_retrieval": {"question": question, "documents": [], "status": "skipped"},
            "used_knowledge": False,
            "question_route": question_route,
        }
        data = {
            "project_id": None,
            "question": question,
            "question_type": route,
            "question_tags": question_route.get("question_tags") or [],
            "question_route": question_route,
            "analysis_status": "non_project_routed",
            "warnings": [],
            "answer": answer,
            "result_payload": result_payload,
        }
        task_plan = {
            "question": question,
            "question_route": question_route,
            "output_mode": route,
            "steps": [
                {
                    "step_id": "route_question",
                    "name": "route_question",
                    "status": "completed",
                    "description": "问题不需要项目数据，已直接分流处理。",
                }
            ],
        }
        workflow_trace = {
            "workflow_run_id": workflow_run_id,
            "status": "ok",
            "duration_ms": round((perf_counter() - workflow_started_at) * 1000, 2),
            "analysis_run_id": None,
            "warning_count": 0,
            "route": route,
        }
        return {
            "success": True,
            "needs_confirmation": False,
            "identified_project": {},
            "project_memory": {},
            "data": data,
            "result_payload": result_payload,
            "task_plan": task_plan,
            "workspace": {},
            "workflow_trace": workflow_trace,
        }

    @staticmethod
    def _build_chart_clarification_answer() -> str:
        return (
            "## 需要明确图表指标\n"
            "我可以生成项目图表，但当前问题没有明确要画哪个指标。\n\n"
            "可选指标包括：\n"
            "- `Mapping`：比对率对比图\n"
            "- `Duplicate`：重复率对比图\n"
            "- `FRiP`：富集比例对比图\n"
            "- `Adapter`：接头比例对比图\n"
            "- `chrMT/Pt`：细胞器 reads 比例对比图（按项目物种解释）\n"
            "- `Correlation`：样本相关性热图\n\n"
            "你可以这样问：`帮我画 T1 和 T2 的 Mapping 对比图` 或 `画一下样本相关性热图`。"
        )

    async def _run_chart_route(
        self,
        *,
        workspace: ProjectWorkspace,
        question: str,
        question_route: dict[str, Any],
        plan: dict[str, Any],
        workflow_run_id: str,
        workflow_started_at: float,
        workspace_snapshot: dict[str, Any],
        identified: dict[str, Any],
    ) -> dict[str, Any]:
        target_metrics = question_route.get("target_metrics") or []
        metric = self.CHART_METRIC_MAP.get(str(target_metrics[0])) if target_metrics else None
        if not metric:
            planning_service.update_step_status(
                workspace,
                plan,
                "analyze_project_data",
                "skipped",
                detail={"route": "chart", "reason": "missing_chart_metric"},
            )
            planning_service.update_step_status(
                workspace,
                plan,
                "retrieve_knowledge",
                "skipped",
                detail={"reason": "chart_metric_missing"},
            )
            answer = self._build_chart_clarification_answer()
            publish_project_answer_delta(answer)
            publish_project_answer_final(answer)
            planning_service.update_step_status(
                workspace,
                plan,
                "compose_response",
                "completed",
                detail={"output_mode": "clarification", "route": "chart", "reason": "missing_chart_metric"},
            )
            result_payload = {
                "output_mode": "clarification",
                "answer": answer,
                "report": "",
                "knowledge_retrieval": {"question": question, "documents": [], "status": "skipped"},
                "used_knowledge": False,
                "question_route": question_route,
            }
            analysis_result = {
                "project_id": workspace.project_id,
                "question": question,
                "question_type": "chart_clarification",
                "question_tags": question_route.get("question_tags") or [],
                "question_route": question_route,
                "analysis_status": "chart_metric_missing",
                "warnings": [],
                "result_payload": result_payload,
                "answer": answer,
                "used_knowledge": False,
            }
            workflow_trace = {
                "workflow_run_id": workflow_run_id,
                "status": "clarification",
                "duration_ms": round((perf_counter() - workflow_started_at) * 1000, 2),
                "analysis_run_id": None,
                "warning_count": 0,
                "task_plan_path": str(workspace.task_plan_path),
                "workspace_root": str(workspace.workspace_root),
                "progress_path": str(workspace.progress_path),
                "route": "chart",
                "reason": "missing_chart_metric",
            }
            return {
                "success": True,
                "needs_confirmation": False,
                "identified_project": identified,
                "project_memory": {},
                "data": analysis_result,
                "result_payload": result_payload,
                "task_plan": plan,
                "workspace": workspace_snapshot,
                "workflow_trace": workflow_trace,
            }
        chart_type = self._infer_chart_type(question, metric)

        planning_service.update_step_status(
            workspace,
            plan,
            "analyze_project_data",
            "completed",
            detail={"route": "chart", "metric": metric},
        )
        business_progress_service.report(
            workspace,
            stage="read_chart_data",
            message="正在读取图表所需项目数据",
            status="in_progress",
            detail={"metric": metric},
        )
        chart_result = await project_chart_service.generate_chart_spec(
            project_id=workspace.project_id,
            project_root=str(workspace.project_root),
            metric=metric,
            chart_type=chart_type,
            samples=[],
        )
        business_progress_service.report(
            workspace,
            stage="generate_chart",
            message="交互图表已生成",
            status="completed",
            detail={"metric": chart_result.get("metric"), "chart_type": chart_result.get("chart_type")},
        )
        planning_service.update_step_status(
            workspace,
            plan,
            "retrieve_knowledge",
            "skipped",
            detail={"reason": "chart_route"},
        )
        answer = self._build_chart_answer(chart_result)
        publish_project_answer_delta(answer)
        publish_project_answer_final(answer)
        planning_service.update_step_status(
            workspace,
            plan,
            "compose_response",
            "completed",
            detail={"output_mode": "chart", "route": "chart"},
        )
        result_payload = {
            "output_mode": "chart",
            "answer": answer,
            "report": "",
            "chart": chart_result,
            "knowledge_retrieval": {"question": question, "documents": [], "status": "skipped"},
            "used_knowledge": False,
            "experience_summary": {},
            "question_route": question_route,
        }
        analysis_result = {
            "project_id": workspace.project_id,
            "question": question,
            "question_type": "chart",
            "question_tags": question_route.get("question_tags") or [],
            "question_route": question_route,
            "analysis_status": "chart_generated",
            "chart_result": chart_result,
            "warnings": [],
            "result_payload": result_payload,
            "answer": answer,
            "used_knowledge": False,
        }
        workflow_trace = {
            "workflow_run_id": workflow_run_id,
            "status": "ok",
            "duration_ms": round((perf_counter() - workflow_started_at) * 1000, 2),
            "analysis_run_id": None,
            "warning_count": 0,
            "task_plan_path": str(workspace.task_plan_path),
            "workspace_root": str(workspace.workspace_root),
            "progress_path": str(workspace.progress_path),
            "route": "chart",
        }
        return {
            "success": True,
            "needs_confirmation": False,
            "identified_project": identified,
            "project_memory": {},
            "data": analysis_result,
            "result_payload": result_payload,
            "task_plan": plan,
            "workspace": workspace_snapshot,
            "workflow_trace": workflow_trace,
        }

    async def _run_project_compare_route(
        self,
        *,
        workspace: ProjectWorkspace,
        question: str,
        question_route: dict[str, Any],
        plan: dict[str, Any],
        workflow_run_id: str,
        workflow_started_at: float,
        workspace_snapshot: dict[str, Any],
        identified: dict[str, Any],
        recent_projects: list[dict[str, Any]],
        max_evidence_files: int,
    ) -> dict[str, Any]:
        business_progress_service.report(
            workspace,
            stage="resolve_compare_project",
            message="正在解析对照项目",
            status="in_progress",
            detail={"current_project_id": workspace.project_id},
        )
        compare_project = project_comparison_service.resolve_compare_project(
            question=question,
            current_project_id=workspace.project_id,
            recent_projects=recent_projects,
        )
        if compare_project.get("needs_confirmation"):
            planning_service.update_step_status(
                workspace,
                plan,
                "analyze_project_data",
                "skipped",
                detail={"route": "project_compare", "reason": "compare_project_missing"},
            )
            planning_service.update_step_status(
                workspace,
                plan,
                "retrieve_knowledge",
                "skipped",
                detail={"route": "project_compare", "reason": "compare_project_missing"},
            )
            candidates = compare_project.get("candidates") or []
            candidate_lines = [
                f"- `{item.get('project_id')}`: `{item.get('project_root')}`"
                for item in candidates
                if isinstance(item, dict) and item.get("project_id")
            ]
            if compare_project.get("only_referenced_current_project"):
                reason_text = (
                    f"你只提供了当前项目 `{workspace.project_id}`，系统还缺少另一个用于对比的项目。"
                )
            else:
                explicit_ids = ", ".join(f"`{item}`" for item in compare_project.get("explicit_project_ids", []) or [])
                reason_text = (
                    f"已识别到项目编号 {explicit_ids}，但它不能作为当前项目之外的对照项目。"
                    if explicit_ids
                    else "当前问题是跨项目对比，但没有解析到明确的对照项目。"
                )
            answer = "\n".join(
                [
                    "## 需要指定对照项目",
                    reason_text,
                    "",
                    "正确问法示例：",
                    "- `将当前项目和 VZ20260531009 做对比`",
                    "- `把 VZ20260427001 和 VZ20260531009 做对比`",
                    "- 如果你想用历史项目：`将当前项目和上一个项目做对比`",
                    "",
                    "## 可选历史项目",
                    *(candidate_lines or ["- 暂无可用历史项目，请先分析或绑定另一个项目。"]),
                ]
            )
            publish_project_answer_delta(answer)
            planning_service.update_step_status(
                workspace,
                plan,
                "compose_response",
                "completed",
                detail={"output_mode": "clarification", "route": "project_compare"},
            )
            result_payload = {
                "output_mode": "clarification",
                "answer": answer,
                "report": "",
                "comparison": compare_project,
                "knowledge_retrieval": {"question": question, "documents": [], "status": "skipped"},
                "used_knowledge": False,
                "question_route": question_route,
            }
            workflow_trace = {
                "workflow_run_id": workflow_run_id,
                "status": "clarification",
                "duration_ms": round((perf_counter() - workflow_started_at) * 1000, 2),
                "analysis_run_id": None,
                "warning_count": 0,
                "task_plan_path": str(workspace.task_plan_path),
                "workspace_root": str(workspace.workspace_root),
                "progress_path": str(workspace.progress_path),
                "route": "project_compare",
                "reason": "compare_project_missing",
            }
            analysis_result = {
                "project_id": workspace.project_id,
                "question": question,
                "question_type": "project_compare",
                "question_tags": question_route.get("question_tags") or [],
                "question_route": question_route,
                "analysis_status": "compare_project_missing",
                "warnings": [],
                "result_payload": result_payload,
                "answer": answer,
                "used_knowledge": False,
            }
            return {
                "success": True,
                "needs_confirmation": True,
                "identified_project": identified,
                "project_memory": {},
                "data": analysis_result,
                "result_payload": result_payload,
                "task_plan": plan,
                "workspace": workspace_snapshot,
                "workflow_trace": workflow_trace,
            }

        business_progress_service.report(
            workspace,
            stage="compare_project_data",
            message="正在读取两个项目的核心指标",
            status="in_progress",
            detail={
                "current_project_id": workspace.project_id,
                "compare_project_id": compare_project.get("project_id"),
            },
        )
        planning_service.update_step_status(
            workspace,
            plan,
            "analyze_project_data",
            "in_progress",
            detail={"route": "project_compare", "compare_project_id": compare_project.get("project_id")},
        )
        try:
            compare_result = await asyncio.wait_for(
                asyncio.to_thread(
                    project_comparison_service.compare,
                    question=question,
                    current_project_id=workspace.project_id,
                    current_project_root=str(workspace.project_root),
                    compare_project_id=str(compare_project["project_id"]),
                    compare_project_root=str(compare_project["project_root"]),
                    max_evidence_files=max_evidence_files,
                ),
                timeout=self.PROJECT_ANALYSIS_TIMEOUT_SECONDS * 2,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "business_runtime route=project_compare workflow_run_id=%s status=timeout",
                workflow_run_id,
            )
            answer = (
                "## 跨项目对比超时\n"
                "系统已解析到对照项目，但读取两个项目的核心指标耗时过长，本轮已停止等待。\n\n"
                "建议先分别确认两个项目的结果目录可访问，再重试对比。"
            )
            publish_project_answer_delta(answer)
            planning_service.update_step_status(
                workspace,
                plan,
                "analyze_project_data",
                "failed",
                detail={"route": "project_compare", "reason": "timeout"},
            )
            planning_service.update_step_status(
                workspace,
                plan,
                "retrieve_knowledge",
                "skipped",
                detail={"reason": "project_compare_timeout"},
            )
            planning_service.update_step_status(
                workspace,
                plan,
                "compose_response",
                "completed",
                detail={"output_mode": "project_compare_timeout", "route": "project_compare"},
            )
            result_payload = {
                "output_mode": "project_compare_timeout",
                "answer": answer,
                "report": "",
                "comparison": {
                    "current_project": {"project_id": workspace.project_id, "project_root": str(workspace.project_root)},
                    "compare_project": compare_project,
                },
                "knowledge_retrieval": {"question": question, "documents": [], "status": "skipped"},
                "used_knowledge": False,
                "question_route": question_route,
            }
            workflow_trace = {
                "workflow_run_id": workflow_run_id,
                "status": "timeout",
                "duration_ms": round((perf_counter() - workflow_started_at) * 1000, 2),
                "analysis_run_id": None,
                "warning_count": 1,
                "task_plan_path": str(workspace.task_plan_path),
                "workspace_root": str(workspace.workspace_root),
                "progress_path": str(workspace.progress_path),
                "route": "project_compare",
                "reason": "timeout",
            }
            analysis_result = {
                "project_id": workspace.project_id,
                "question": question,
                "question_type": "project_compare",
                "question_tags": question_route.get("question_tags") or [],
                "question_route": question_route,
                "analysis_status": "project_compare_timeout",
                "warnings": ["project_compare_timeout"],
                "result_payload": result_payload,
                "answer": answer,
                "used_knowledge": False,
            }
            return {
                "success": True,
                "needs_confirmation": False,
                "identified_project": identified,
                "project_memory": {},
                "data": analysis_result,
                "result_payload": result_payload,
                "task_plan": plan,
                "workspace": workspace_snapshot,
                "workflow_trace": workflow_trace,
            }
        answer = str(compare_result.get("answer") or "")
        publish_project_answer_delta(answer)
        planning_service.update_step_status(
            workspace,
            plan,
            "analyze_project_data",
            "completed",
            detail={
                "route": "project_compare",
                "compare_project_id": compare_project.get("project_id"),
                "comparison_rows": len(compare_result.get("comparison_rows") or []),
            },
        )
        planning_service.update_step_status(
            workspace,
            plan,
            "retrieve_knowledge",
            "skipped",
            detail={"reason": "project_compare_route"},
        )
        planning_service.update_step_status(
            workspace,
            plan,
            "compose_response",
            "completed",
            detail={"output_mode": "project_compare", "route": "project_compare"},
        )
        business_progress_service.report(
            workspace,
            stage="project_compare",
            message="跨项目对比完成",
            status="completed",
            detail={
                "current_project_id": workspace.project_id,
                "compare_project_id": compare_project.get("project_id"),
                "comparison_rows": len(compare_result.get("comparison_rows") or []),
            },
        )
        result_payload = {
            "output_mode": "project_compare",
            "answer": answer,
            "report": "",
            "comparison": compare_result,
            "knowledge_retrieval": {"question": question, "documents": [], "status": "skipped"},
            "used_knowledge": False,
            "question_route": question_route,
        }
        analysis_result = {
            "project_id": workspace.project_id,
            "question": question,
            "question_type": "project_compare",
            "question_tags": question_route.get("question_tags") or [],
            "question_route": question_route,
            "analysis_status": "project_compare_completed",
            "comparison": compare_result,
            "warnings": [],
            "result_payload": result_payload,
            "answer": answer,
            "used_knowledge": False,
        }
        workflow_trace = {
            "workflow_run_id": workflow_run_id,
            "status": "ok",
            "duration_ms": round((perf_counter() - workflow_started_at) * 1000, 2),
            "analysis_run_id": None,
            "warning_count": 0,
            "task_plan_path": str(workspace.task_plan_path),
            "workspace_root": str(workspace.workspace_root),
            "progress_path": str(workspace.progress_path),
            "route": "project_compare",
        }
        return {
            "success": True,
            "needs_confirmation": False,
            "identified_project": identified,
            "project_memory": {},
            "data": analysis_result,
            "result_payload": result_payload,
            "task_plan": plan,
            "workspace": workspace_snapshot,
            "workflow_trace": workflow_trace,
        }

    async def _run_report_summary_route(
        self,
        *,
        workspace: ProjectWorkspace,
        question: str,
        question_route: dict[str, Any],
        plan: dict[str, Any],
        workflow_run_id: str,
        workflow_started_at: float,
        workspace_snapshot: dict[str, Any],
        identified: dict[str, Any],
        user_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        # 优先查项目级缓存（跨 session），再查 session 级
        from multi_agent.backed.app.repositories.project_report_cache_repository import project_report_cache_repository
        cached = project_report_cache_repository.load(workspace.project_id, str(workspace.project_root))
        if not isinstance(cached, dict):
            cached = project_session_state_service.get_ai_report_summary(user_id, session_id)
        cached_analysis = None
        if (
            isinstance(cached, dict)
            and cached.get("status") == "ready"
            and cached.get("generation_version") == self.AI_REPORT_SUMMARY_VERSION
            and isinstance(cached.get("analysis"), dict)
        ):
            cached_analysis = cached["analysis"]

        planning_service.update_step_status(
            workspace,
            plan,
            "analyze_project_data",
            "in_progress",
            detail={"route": "ai_report_summary"},
        )
        business_progress_service.report(
            workspace,
            stage="analyze_project_data",
            message="正在读取项目报告和结构化证据",
            status="in_progress",
            detail={"route": "ai_report_summary"},
        )

        if cached_analysis:
            analysis_result = cached_analysis
            # 命中缓存只复用分析数据，答案始终用用户真实问题重新生成（缓存 answer 是用泛化问题生成的）
            try:
                answer = await business_response_service.generate_existing_html_report_answer(
                    analysis_result, question=question
                )
                generation_mode = "cached_analysis_llm_answer"
            except Exception as exc:
                logger.warning("cached html report llm summary failed project=%s error=%s", workspace.project_id, str(exc))
                answer = business_response_service.build_existing_html_report_answer(analysis_result, question=question)
                generation_mode = "cached_analysis_extractive_fallback"
        else:
            analysis_result = await asyncio.to_thread(
                ProjectAnalysisService.analyze,
                workspace.project_id,
                self.AUTO_HTML_REPORT_QUESTION,
                str(workspace.project_root),
                16,
                {"force_include_html_body": True},
            )
            try:
                answer = await business_response_service.generate_existing_html_report_answer(
                    analysis_result, question=question
                )
                generation_mode = "llm_html_report_summary"
            except Exception as exc:
                logger.warning("explicit html report llm summary failed project=%s error=%s", workspace.project_id, str(exc))
                answer = business_response_service.build_existing_html_report_answer(analysis_result, question=question)
                generation_mode = "extractive_html_report_summary_fallback"

        planning_service.update_step_status(
            workspace,
            plan,
            "analyze_project_data",
            "completed",
            detail={"route": "ai_report_summary", "generation_mode": generation_mode},
        )
        planning_service.update_step_status(
            workspace,
            plan,
            "retrieve_knowledge",
            "skipped",
            detail={"reason": "ai_report_summary_route"},
        )

        answer, harness_guard = await self._enforce_project_answer_guard(
            answer=answer,
            analysis_result=analysis_result,
            question_route=question_route,
        )
        business_progress_service.report(
            workspace,
            stage="harness_guard",
            message=self._harness_guard_progress_message(harness_guard),
            status="completed",
            detail={
                "route": "ai_report_summary",
                "harness_guard_action": harness_guard.get("action"),
                "harness_guard_passed": harness_guard.get("passed"),
                "harness_guard_severity": harness_guard.get("severity"),
                "violation_count": len(harness_guard.get("violations") or []),
            },
        )
        self._publish_answer_text(answer)
        result_payload = {
            "output_mode": "report",
            "answer": answer,
            "report": analysis_result.get("report", ""),
            "knowledge_retrieval": {"question": question, "documents": [], "status": "skipped"},
            "used_knowledge": False,
            "generation_mode": generation_mode,
            "question_route": question_route,
            "harness_guard": harness_guard,
        }
        analysis_result["result_payload"] = result_payload
        analysis_result["answer"] = answer
        analysis_result["used_knowledge"] = False
        analysis_result["generation_mode"] = generation_mode
        analysis_result["generation_version"] = self.AI_REPORT_SUMMARY_VERSION
        analysis_result["agent_role"] = "html_report_summary"
        analysis_result["question_route"] = question_route
        analysis_result["harness_guard"] = harness_guard
        analysis_result = self._remove_internal_fields(analysis_result)

        project_session_state_service.save_ai_report_summary(
            user_id,
            session_id,
            workspace.project_id,
            str(workspace.project_root),
            self._slim_analysis_for_cache(analysis_result),
        )
        planning_service.update_step_status(
            workspace,
            plan,
            "compose_response",
            "completed",
            detail={
                "output_mode": "report",
                "route": "ai_report_summary",
                "generation_mode": generation_mode,
                "harness_guard_action": harness_guard.get("action"),
                "harness_guard_passed": harness_guard.get("passed"),
            },
        )
        business_progress_service.report(
            workspace,
            stage="compose_response",
            message="AI报告总结已生成",
            status="completed",
            detail={"output_mode": "report", "generation_mode": generation_mode},
        )
        workflow_trace = {
            "workflow_run_id": workflow_run_id,
            "status": "ok",
            "duration_ms": round((perf_counter() - workflow_started_at) * 1000, 2),
            "analysis_run_id": analysis_result.get("run_id"),
            "warning_count": len(analysis_result.get("warnings", [])),
            "task_plan_path": str(workspace.task_plan_path),
            "workspace_root": str(workspace.workspace_root),
            "progress_path": str(workspace.progress_path),
            "route": "ai_report_summary",
            "harness_guard": harness_guard,
        }
        return {
            "success": True,
            "needs_confirmation": False,
            "identified_project": identified,
            "project_memory": {},
            "data": analysis_result,
            "result_payload": result_payload,
            "task_plan": plan,
            "workspace": workspace_snapshot,
            "workflow_trace": workflow_trace,
        }

    @staticmethod
    def _build_workspace(
        *,
        project_id: str,
        project_root: str,
        user_id: str,
        session_id: str,
    ) -> ProjectWorkspace:
        return ProjectWorkspace.create(
            project_id=project_id,
            project_root=Path(project_root).resolve(),
            user_id=user_id,
            session_id=session_id,
        )

    @staticmethod
    def _should_force_include_html_body(question: str, question_route: dict[str, Any] | None = None) -> bool:
        route = str((question_route or {}).get("route") or "").strip()
        if route == "ai_report_summary":
            return True
        normalized_question = " ".join((question or "").split()).strip().lower()
        html_report_terms = (
            "报告总结",
            "总结报告",
            "完整报告",
            "html报告",
            "html 报告",
            "项目报告",
            "报告内容",
            "ai报告总结",
        )
        return any(term in normalized_question for term in html_report_terms)

    @staticmethod
    def _should_retrieve_knowledge(question: str, analysis_result: dict[str, Any]) -> tuple[bool, str]:
        # 硬排除：分析超时或已有 HTML 报告摘要模式，跳过检索
        if analysis_result.get("analysis_status") == "timeout":
            return False, "project_analysis_timeout"
        if analysis_result.get("report_mode") == "existing_html_report_summary":
            return False, "existing_html_report_summary"
        # 知识库未配置时跳过，避免无意义的网络请求
        from multi_agent.backed.app.config.settings import settings as _settings
        if not _settings.KNOWLEDGE_BASE_URL:
            return False, "knowledge_base_not_configured"
        # 其余所有项目问题均检索知识库（检索已在并行阶段提前发起，此处无额外延迟）
        return True, "always_retrieve"

    @staticmethod
    def _build_project_analysis_timeout_result(
        *,
        workspace: ProjectWorkspace,
        question: str,
        question_tags: list[str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        question_type = question_tags[0] if question_tags else "overview"
        run_id = f"projrun_timeout_{uuid4().hex[:8]}"
        warning = f"项目数据分析超过 {timeout_seconds:.0f}s 未返回，本轮已停止等待文件读取结果。"
        diagnosis_summary = {
            "conclusions": [
                "本轮项目文件读取耗时异常，未能在限定时间内完成数据解析，因此不能给出可靠的项目数据结论。"
            ],
            "evidence": [
                "后端已完成项目识别和分析规划，但卡在项目数据读取/解析阶段。"
            ],
            "possible_causes": [
                "项目目录文件遍历或个别结果文件读取阻塞。",
                "挂载盘、网络盘或大文件读取响应过慢。",
                "后台仍有未结束的文件分析线程占用资源。",
            ],
            "next_actions": [
                "先停止当前请求或重启后端，避免旧的文件读取线程继续占用资源。",
                "检查该项目目录是否存在超大 HTML/日志/表格文件或挂载盘访问卡顿。",
                "后续应在文件遍历和单文件读取层面继续加更细的耗时日志。",
            ],
        }
        return {
            "run_id": run_id,
            "trace": {
                "run_id": run_id,
                "question_type": question_type,
                "question_tags": question_tags,
                "duration_ms": round(timeout_seconds * 1000, 2),
                "evidence_attempted": 0,
                "evidence_succeeded": 0,
                "warning_count": 1,
                "status": "timeout",
            },
            "analysis_status": "timeout",
            "project_id": workspace.project_id,
            "project_root": str(workspace.project_root),
            "project_match": {
                "project_id": workspace.project_id,
                "project_root": str(workspace.project_root),
            },
            "question": question,
            "question_type": question_type,
            "question_tags": question_tags,
            "confidence": 0.0,
            "planning_hints": {},
            "metric_priority": question_tags or [question_type],
            "read_plan": [],
            "project_context": {},
            "pre_analysis_steps": [],
            "report_mode": "structured_evidence_analysis",
            "report_source": "",
            "stage_names": [],
            "project_file_count": 0,
            "evidence_files": [],
            "evidence_status": [],
            "file_summaries": [],
            "parsed_metrics": {},
            "comparison_tables": {},
            "diagnosis_summary": diagnosis_summary,
            "automatic_findings": diagnosis_summary["conclusions"],
            "warnings": [warning],
            "next_actions": diagnosis_summary["next_actions"],
            "report": warning,
        }

    # 超过此时限仍处于 running 状态视为 stale（服务器重启后孤儿任务）
    AUTO_REPORT_SUMMARY_RUNNING_TIMEOUT_SECONDS = 15 * 60  # 15 分钟

    def _is_running_stale(self, cached: dict[str, Any]) -> bool:
        """判断 running 状态是否因服务重启而永久卡住。"""
        started_at_str = cached.get("started_at") or cached.get("updated_at")
        if not started_at_str:
            return True  # 没有时间戳，视为 stale
        try:
            from datetime import datetime, timezone
            started_at = datetime.fromisoformat(started_at_str)
            # 若时间戳没有时区信息，视为本地时间
            now = datetime.now()
            elapsed = (now - started_at).total_seconds()
            return elapsed > self.AUTO_REPORT_SUMMARY_RUNNING_TIMEOUT_SECONDS
        except Exception:
            return True

    def _should_start_auto_report_summary(self, user_id: str, session_id: str, project_id: str, project_root: str) -> bool:
        if not self.AUTO_REPORT_SUMMARY_ENABLED:
            return False
        task_key = f"{user_id}:{session_id}:{project_id}:{project_root}"
        if task_key in self._auto_report_tasks:
            return False
        # 优先查项目级缓存（跨 session），再查 session 级
        from multi_agent.backed.app.repositories.project_report_cache_repository import project_report_cache_repository
        cached = project_report_cache_repository.load(project_id, project_root)
        if not isinstance(cached, dict):
            cached = project_session_state_service.get_ai_report_summary(user_id, session_id)
        if (
            isinstance(cached, dict)
            and cached.get("status") == "running"
            and not self._is_running_stale(cached)
        ):
            return False
        if (
            isinstance(cached, dict)
            and cached.get("status") == "ready"
            and cached.get("generation_version") == self.AI_REPORT_SUMMARY_VERSION
        ):
            return False
        return True

    def _schedule_auto_report_summary(self, *, user_id: str, session_id: str, project_id: str, project_root: str) -> None:
        task_key = f"{user_id}:{session_id}:{project_id}:{project_root}"
        if not self._should_start_auto_report_summary(user_id, session_id, project_id, project_root):
            return
        self._auto_report_tasks.add(task_key)
        project_session_state_service.mark_ai_report_summary_running(user_id, session_id, project_id, project_root)

        async def runner() -> None:
            try:
                report_started_at = perf_counter()
                report_analysis_started_at = perf_counter()
                analysis_result = await asyncio.to_thread(
                    ProjectAnalysisService.analyze,
                    project_id,
                    self.AUTO_HTML_REPORT_QUESTION,
                    project_root,
                    16,
                    {"force_include_html_body": True},
                )
                logger.info(
                    "html_report_summary stage=analyze_html project=%s duration_ms=%.2f",
                    project_id,
                    (perf_counter() - report_analysis_started_at) * 1000,
                )
                try:
                    report_llm_started_at = perf_counter()
                    answer = await business_response_service.generate_existing_html_report_answer(
                        analysis_result, question=self.AUTO_HTML_REPORT_QUESTION
                    )
                    generation_mode = "llm_html_report_summary"
                    logger.info(
                        "html_report_summary stage=generate_summary project=%s mode=%s output_chars=%d duration_ms=%.2f",
                        project_id,
                        generation_mode,
                        len(answer or ""),
                        (perf_counter() - report_llm_started_at) * 1000,
                    )
                except Exception as exc:
                    logger.warning("auto html report llm summary failed project=%s error=%s", project_id, str(exc))
                    answer = business_response_service.build_existing_html_report_answer(
                        analysis_result, question=self.AUTO_HTML_REPORT_QUESTION
                    )
                    generation_mode = "extractive_html_report_summary_fallback"
                answer, harness_guard = await self._enforce_project_answer_guard(
                    answer=answer,
                    analysis_result=analysis_result,
                    question_route={"route": "ai_report_summary", "intent": "report_summary"},
                )
                analysis_result["result_payload"] = {
                    "output_mode": "report",
                    "answer": answer,
                    "report": analysis_result.get("report", ""),
                    "knowledge_retrieval": {"question": self.AUTO_HTML_REPORT_QUESTION, "documents": [], "status": "skipped"},
                    "used_knowledge": False,
                    "auto_generated": True,
                    "generation_mode": generation_mode,
                    "harness_guard": harness_guard,
                }
                analysis_result["answer"] = answer
                analysis_result["used_knowledge"] = False
                analysis_result["generation_mode"] = generation_mode
                analysis_result["generation_version"] = self.AI_REPORT_SUMMARY_VERSION
                analysis_result["agent_role"] = "html_report_summary"
                analysis_result["harness_guard"] = harness_guard
                analysis_result = self._remove_internal_fields(analysis_result)
                project_session_state_service.save_ai_report_summary(
                    user_id,
                    session_id,
                    project_id,
                    project_root,
                    self._slim_analysis_for_cache(analysis_result),
                )
                logger.info(
                    "html_report_summary done project=%s mode=%s duration_ms=%.2f",
                    project_id,
                    generation_mode,
                    (perf_counter() - report_started_at) * 1000,
                )
            except Exception as exc:
                logger.warning("auto html report summary failed project=%s error=%s", project_id, str(exc))
                project_session_state_service.mark_ai_report_summary_failed(
                    user_id,
                    session_id,
                    project_id,
                    project_root,
                    str(exc),
                )
            finally:
                self._auto_report_tasks.discard(task_key)

        asyncio.create_task(runner())

    def identify_project(
        self,
        *,
        question: str,
        project_id: str | None,
        user_id: str,
        session_id: str,
        project_root: str | None = None,
        suppress_auto_report_summary: bool = False,
    ) -> dict[str, Any]:
        state = project_session_state_service.load_state(user_id, session_id)
        intent = project_context_intent_service.classify(question, state)
        if intent == "clear_project_context":
            project_session_state_service.clear_active_project(user_id, session_id)

        if project_id and project_root:
            identified = {
                "matched_by": "request",
                "project_id": project_id,
                "project_root": project_root,
                "sample_names": [],
                "candidates": [],
                "matched_terms": [project_id],
                "confidence": 0.99,
                "needs_confirmation": False,
                "context_intent": "bind_project",
            }
            project_session_state_service.bind_active_project(
                user_id=user_id,
                session_id=session_id,
                project_id=project_id,
                project_root=project_root,
                question=question,
                source="user_explicit",
            )
            if not suppress_auto_report_summary:
                self._schedule_auto_report_summary(
                    user_id=user_id,
                    session_id=session_id,
                    project_id=project_id,
                    project_root=project_root,
                )
            return identified
        identified = project_locator_service.identify_project(
            question=question,
            project_id=project_id,
            user_id=user_id,
            session_id=session_id,
        )
        if project_root:
            identified["project_root"] = project_root
        identified["context_intent"] = intent
        if not identified.get("needs_confirmation"):
            source = "user_explicit" if intent in {"bind_project", "switch_project"} else str(
                identified.get("matched_by") or "inferred"
            )
            project_session_state_service.bind_active_project(
                user_id=user_id,
                session_id=session_id,
                project_id=str(identified["project_id"]),
                project_root=str(identified["project_root"]),
                question=question,
                source=source,
            )
            if not suppress_auto_report_summary:
                self._schedule_auto_report_summary(
                    user_id=user_id,
                    session_id=session_id,
                    project_id=str(identified["project_id"]),
                    project_root=str(identified["project_root"]),
                )
        else:
            project_session_state_service.mark_pending_project_confirmation(user_id, session_id, identified)
        return identified

    async def run(
        self,
        *,
        question: str,
        project_id: str | None,
        user_id: str,
        session_id: str,
        project_root: str | None = None,
        max_evidence_files: int = 40,
    ) -> dict[str, Any]:
        workflow_started_at = perf_counter()
        workflow_run_id = f"projwf_{uuid4().hex[:12]}"
        current_state = project_session_state_service.load_state(user_id, session_id)
        pre_route = question_router_service.classify(
            question,
            active_project_id=project_id or current_state.get("active_project_id"),
        )
        if not pre_route.requires_project:
            logger.info(
                "business_runtime route=non_project workflow_run_id=%s intent=%s route=%s",
                workflow_run_id,
                pre_route.intent,
                pre_route.route,
            )
            return self._run_non_project_route(
                question=question,
                question_route=pre_route.to_dict(),
                workflow_run_id=workflow_run_id,
                workflow_started_at=workflow_started_at,
                user_id=user_id,
                session_id=session_id,
            )

        business_progress_service.emit("正在识别当前问题对应的项目", stage="identify_project")
        identify_started_at = perf_counter()
        identify_project_id = project_id
        identify_project_root = project_root
        if pre_route.route == "project_compare" and current_state.get("active_project_id") and current_state.get("active_project_root"):
            identify_project_id = str(current_state["active_project_id"])
            identify_project_root = str(current_state["active_project_root"])

        # 若调用方未提供 project_root（常见情况），从 session 状态中恢复已绑定的 root，
        # 避免每次请求都触发慢路径（扫描网络磁盘/SFTP base_dirs），导致 identify_project 卡 90+ 秒
        if not identify_project_root and identify_project_id and current_state.get("active_project_root"):
            if current_state.get("active_project_id") == identify_project_id:
                identify_project_root = str(current_state["active_project_root"])

        # 始终抑制 auto report summary，等主分析全部 LLM 调用完成后再 defer 启动，
        # 避免两个请求并发打到同一 API endpoint 导致 run_planner_llm 永久等待（转圈）
        _suppress_auto_report = pre_route.route in {"ai_report_summary", "chart", "project_compare"}
        identified = self.identify_project(
            question=question,
            project_id=identify_project_id,
            user_id=user_id,
            session_id=session_id,
            project_root=identify_project_root,
            suppress_auto_report_summary=True,
        )
        logger.info(
            "business_runtime stage=identify_project workflow_run_id=%s duration_ms=%.2f",
            workflow_run_id,
            (perf_counter() - identify_started_at) * 1000,
        )
        if identified.get("needs_confirmation"):
            business_progress_service.emit(
                "项目匹配存在歧义，等待确认",
                stage="identify_project",
                status="needs_confirmation",
            )
            return {
                "success": False,
                "needs_confirmation": True,
                "message": "Project match is ambiguous. Please confirm the project before analysis.",
                "identified_project": identified,
                "workflow_trace": {
                    "workflow_run_id": workflow_run_id,
                    "status": "needs_confirmation",
                    "duration_ms": round((perf_counter() - workflow_started_at) * 1000, 2),
                },
            }

        workspace = self._build_workspace(
            project_id=str(identified["project_id"]),
            project_root=str(identified["project_root"]),
            user_id=user_id,
            session_id=session_id,
        )
        workspace_snapshot = workspace.snapshot()
        workspace.save_snapshot_payload(workspace_snapshot)

        business_progress_service.report(
            workspace,
            stage="workflow_start",
            message="正在启动项目分析工作流",
            status="completed",
            detail={"project_id": workspace.project_id},
        )

        planning_started_at = perf_counter()
        question_route = question_router_service.classify(question, active_project_id=workspace.project_id)
        question_tags = question_route.question_tags or ProjectAnalysisService._infer_question_types(question)
        base_experience_summary = experience_service.summarize_experience(workspace.project_id)
        experience_service.refresh_rule_library(
            workspace=workspace,
            experience_summary=base_experience_summary,
        )
        rule_library = experience_service.load_rule_library(workspace)
        planning_hints = experience_service.build_planning_hints(
            question_tags=question_tags,
            experience_summary=base_experience_summary,
            rule_library=rule_library,
        )
        analysis_plan = await business_agent_concurrency_service.run_planner_llm(
            lambda: analysis_planner_service.build_plan_with_llm(
                question=question,
                project_id=workspace.project_id,
                question_route=question_route.to_dict(),
                experience_summary=base_experience_summary,
            ),
            workflow_run_id=workflow_run_id,
        )
        planning_hints["analysis_plan"] = analysis_plan
        if analysis_plan.get("prioritized_metrics"):
            existing_metrics = list(planning_hints.get("prioritized_metrics", []) or [])
            planning_hints["prioritized_metrics"] = list(
                dict.fromkeys(list(analysis_plan.get("prioritized_metrics") or []) + existing_metrics)
            )
        if analysis_plan.get("prioritized_evidence_hints"):
            existing_hints = list(planning_hints.get("prioritized_evidence_hints", []) or [])
            planning_hints["prioritized_evidence_hints"] = list(
                dict.fromkeys(list(analysis_plan.get("prioritized_evidence_hints") or []) + existing_hints)
            )
        planning_hints["question_route"] = question_route.to_dict()
        experience_summary = {**base_experience_summary, **planning_hints}
        plan = planning_service.build_plan(
            workspace=workspace,
            question=question,
            max_evidence_files=max_evidence_files,
            experience_summary=experience_summary,
        )
        evidence_scope_adjustment = plan.get("experience_inputs", {}).get("evidence_scope_adjustment", {}) or {}
        effective_max_evidence_files = min(
            40,
            max_evidence_files + int(evidence_scope_adjustment.get("expand_by", 0) or 0),
        )
        evidence_scope_adjustment["effective_max_evidence_files"] = effective_max_evidence_files
        plan["experience_inputs"]["evidence_scope_adjustment"] = evidence_scope_adjustment
        plan["planning_summary"]["effective_max_evidence_files"] = effective_max_evidence_files
        planning_service.save_plan(workspace, plan)
        logger.info(
            "business_runtime stage=planning workflow_run_id=%s duration_ms=%.2f",
            workflow_run_id,
            (perf_counter() - planning_started_at) * 1000,
        )
        business_progress_service.report(
            workspace,
            stage="planning",
            message="已生成问题驱动的分析路线",
            status="completed",
            detail={
                **(plan.get("planning_summary", {}) or {}),
                "analysis_intent": analysis_plan.get("intent"),
                "planner_mode": "rule" if analysis_plan.get("planner_llm_skipped") else "llm",
                "planner_skip_reason": analysis_plan.get("planner_skip_reason", ""),
                "target_metrics": analysis_plan.get("target_metrics", []),
                "target_samples": analysis_plan.get("target_samples", []),
                "evidence_request_count": len(analysis_plan.get("evidence_requests", []) or []),
                "requires_script_review": analysis_plan.get("requires_script_review"),
                "planner_version": analysis_plan.get("planner_version"),
                "planner_fallback_used": analysis_plan.get("planner_fallback_used", False),
                "bio_skill_reference_count": len(analysis_plan.get("bio_skill_references", []) or []),
                "selected_tool_count": len(analysis_plan.get("selected_tools", []) or []),
            },
        )

        if question_route.route == "project_compare":
            logger.info(
                "business_runtime route=project_compare workflow_run_id=%s intent=%s",
                workflow_run_id,
                question_route.intent,
            )
            return await self._run_project_compare_route(
                workspace=workspace,
                question=question,
                question_route=question_route.to_dict(),
                plan=plan,
                workflow_run_id=workflow_run_id,
                workflow_started_at=workflow_started_at,
                workspace_snapshot=workspace_snapshot,
                identified=identified,
                recent_projects=list(current_state.get("recent_projects") or []),
                max_evidence_files=effective_max_evidence_files,
            )

        if question_route.route == "ai_report_summary":
            logger.info(
                "business_runtime route=ai_report_summary workflow_run_id=%s intent=%s",
                workflow_run_id,
                question_route.intent,
            )
            return await self._run_report_summary_route(
                workspace=workspace,
                question=question,
                question_route=question_route.to_dict(),
                plan=plan,
                workflow_run_id=workflow_run_id,
                workflow_started_at=workflow_started_at,
                workspace_snapshot=workspace_snapshot,
                identified=identified,
                user_id=user_id,
                session_id=session_id,
            )

        if question_route.route == "chart":
            logger.info(
                "business_runtime route=chart workflow_run_id=%s intent=%s metrics=%s",
                workflow_run_id,
                question_route.intent,
                ",".join(question_route.target_metrics),
            )
            return await self._run_chart_route(
                workspace=workspace,
                question=question,
                question_route=question_route.to_dict(),
                plan=plan,
                workflow_run_id=workflow_run_id,
                workflow_started_at=workflow_started_at,
                workspace_snapshot=workspace_snapshot,
                identified=identified,
            )

        planning_service.update_step_status(workspace, plan, "analyze_project_data", "in_progress")
        business_progress_service.report(
            workspace,
            stage="analyze_project_data",
            message="正在读取项目证据文件",
            status="in_progress",
            detail={
                "max_evidence_files": max_evidence_files,
                "effective_max_evidence_files": effective_max_evidence_files,
            },
        )
        planning_service.update_step_status(workspace, plan, "retrieve_knowledge", "in_progress")
        business_progress_service.report(
            workspace,
            stage="retrieve_knowledge",
            message="正在检索知识库补充说明",
            status="in_progress",
        )
        analysis_started_at = perf_counter()
        retrieval_started_at = perf_counter()

        async def _run_analysis() -> dict[str, Any]:
            try:
                return await asyncio.wait_for(
                    business_agent_concurrency_service.run_analysis_blocking(
                        data_analysis_service.run,
                        workspace=workspace,
                        question=question,
                        max_evidence_files=effective_max_evidence_files,
                        planning_hints={
                            **(plan.get("experience_inputs", {}) or {}),
                            "analysis_plan": analysis_plan,
                            "force_include_html_body": self._should_force_include_html_body(
                                question,
                                question_route.to_dict(),
                            ),
                        },
                        workflow_run_id=workflow_run_id,
                    ),
                    timeout=self.PROJECT_ANALYSIS_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "business_runtime stage=analyze_project_data workflow_run_id=%s status=timeout timeout_seconds=%.1f",
                    workflow_run_id,
                    self.PROJECT_ANALYSIS_TIMEOUT_SECONDS,
                )
                return self._build_project_analysis_timeout_result(
                    workspace=workspace,
                    question=question,
                    question_tags=question_tags,
                    timeout_seconds=self.PROJECT_ANALYSIS_TIMEOUT_SECONDS,
                )

        async def _run_knowledge() -> dict[str, Any]:
            try:
                return await asyncio.wait_for(
                    business_agent_concurrency_service.run_knowledge(
                        lambda: knowledge_augmentation_service.retrieve(question),
                        workflow_run_id=workflow_run_id,
                    ),
                    timeout=self.KNOWLEDGE_RETRIEVAL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "business runtime knowledge retrieval timeout after %.1fs",
                    self.KNOWLEDGE_RETRIEVAL_TIMEOUT_SECONDS,
                )
                return {
                    "question": question,
                    "documents": [],
                    "status": "timeout",
                    "error_msg": f"knowledge retrieval exceeded {self.KNOWLEDGE_RETRIEVAL_TIMEOUT_SECONDS:.1f}s",
                }
            except Exception as exc:
                logger.warning("business runtime knowledge retrieval failed: %s", str(exc))
                return {
                    "question": question,
                    "documents": [],
                    "status": "error",
                    "error_msg": str(exc),
                }

        analysis_result, speculative_retrieval = await asyncio.gather(
            _run_analysis(),
            _run_knowledge(),
        )

        logger.info(
            "business_runtime stage=analyze_project_data workflow_run_id=%s analysis_cache=%s snapshot=%s duration_ms=%.2f",
            workflow_run_id,
            analysis_result.get("analysis_cache", "miss"),
            (analysis_result.get("snapshot") or {}),
            (perf_counter() - analysis_started_at) * 1000,
        )
        planning_service.update_step_status(
            workspace,
            plan,
            "analyze_project_data",
            "completed",
            detail={
                "evidence_files": analysis_result.get("evidence_files", []),
                "warning_count": len(analysis_result.get("warnings", [])),
                "analysis_cache": analysis_result.get("analysis_cache", "miss"),
                "snapshot": analysis_result.get("snapshot", {}),
                "evidence_request_count": len(analysis_result.get("evidence_request_status", []) or []),
                "evidence_requests_found": sum(
                    1 for item in (analysis_result.get("evidence_request_status", []) or [])
                    if item.get("status") == "found"
                ),
                "evidence_requests_partial": sum(
                    1 for item in (analysis_result.get("evidence_request_status", []) or [])
                    if item.get("status") == "partial"
                ),
                "evidence_requests_missing": sum(
                    1 for item in (analysis_result.get("evidence_request_status", []) or [])
                    if item.get("status") == "missing"
                ),
            },
        )
        planning_service.update_step_status(
            workspace,
            plan,
            "execute_agent_loop",
            "completed",
            detail={
                "round_count": (analysis_result.get("agent_loop") or {}).get("round_count", 0),
                "max_rounds": (analysis_result.get("agent_loop") or {}).get("max_rounds", 3),
                "stop_reason": (analysis_result.get("agent_loop") or {}).get("stop_reason", ""),
                "evidence_card_count": len(analysis_result.get("evidence_cards", []) or []),
            },
        )
        planning_service.update_step_status(
            workspace,
            plan,
            "verify_claims",
            "completed" if (analysis_result.get("claim_validation") or {}).get("passed") else "failed",
            detail={
                "valid_claim_count": len(analysis_result.get("validated_claims", []) or []),
                "invalid_claim_count": (analysis_result.get("claim_validation") or {}).get(
                    "invalid_claim_count",
                    0,
                ),
            },
        )
        business_progress_service.report(
            workspace,
            stage="analyze_project_data",
            message="项目证据读取完成",
            status="completed",
            detail={
                "evidence_files": analysis_result.get("evidence_files", []),
                "warning_count": len(analysis_result.get("warnings", [])),
                "analysis_cache": analysis_result.get("analysis_cache", "miss"),
                "snapshot": analysis_result.get("snapshot", {}),
                "evidence_request_count": len(analysis_result.get("evidence_request_status", []) or []),
                "evidence_requests_found": sum(
                    1 for item in (analysis_result.get("evidence_request_status", []) or [])
                    if item.get("status") == "found"
                ),
                "evidence_requests_partial": sum(
                    1 for item in (analysis_result.get("evidence_request_status", []) or [])
                    if item.get("status") == "partial"
                ),
                "evidence_requests_missing": sum(
                    1 for item in (analysis_result.get("evidence_request_status", []) or [])
                    if item.get("status") == "missing"
                ),
            },
        )

        evidence_files = analysis_result.get("evidence_files", []) or []
        evidence_status = analysis_result.get("evidence_status", []) or []
        files_done = sum(1 for item in evidence_status if item.get("status") == "ok")
        files_failed = sum(1 for item in evidence_status if item.get("status") == "error")
        business_progress_service.report(
            workspace,
            stage="synthesis",
            message="结论汇总完成",
            status="completed",
            detail={
                "files_total": len(evidence_files),
                "files_done": files_done,
                "files_failed": files_failed,
                "warning_count": len(analysis_result.get("warnings", [])),
                "last_file": evidence_status[-1].get("file") if evidence_status else "",
            },
        )

        planning_service.update_step_status(
            workspace,
            plan,
            "consult_experience",
            "completed" if experience_summary.get("has_experience", False) else "skipped",
            detail=plan.get("experience_inputs", {}),
        )

        # 根据 analysis_result 决定是否采用乐观并行取回的知识检索结果
        retrieval_payload: dict[str, Any] = {"question": question, "documents": []}
        should_retrieve_knowledge, knowledge_skip_reason = self._should_retrieve_knowledge(question, analysis_result)
        if not should_retrieve_knowledge:
            retrieval_payload["status"] = "skipped"
            retrieval_payload["error_msg"] = knowledge_skip_reason
            planning_service.update_step_status(
                workspace,
                plan,
                "retrieve_knowledge",
                "skipped",
                detail={"reason": knowledge_skip_reason},
            )
        else:
            retrieval_payload = speculative_retrieval
        planning_service.update_step_status(
            workspace,
            plan,
            "retrieve_knowledge",
            "completed",
            detail={
                "document_count": len(retrieval_payload.get("documents", []) or []),
                "status": retrieval_payload.get("status", "ok"),
                "error_msg": retrieval_payload.get("error_msg", ""),
            },
        )
        business_progress_service.report(
            workspace,
            stage="retrieve_knowledge",
            message="知识库检索完成",
            status="completed",
            detail={
                "document_count": len(retrieval_payload.get("documents", []) or []),
                "status": retrieval_payload.get("status", "ok"),
                "error_msg": retrieval_payload.get("error_msg", ""),
            },
        )
        logger.info(
            "business_runtime stage=retrieve_knowledge workflow_run_id=%s status=%s documents=%d duration_ms=%.2f",
            workflow_run_id,
            retrieval_payload.get("status", "ok"),
            len(retrieval_payload.get("documents", []) or []),
            (perf_counter() - retrieval_started_at) * 1000,
        )

        project_version = str(analysis_result.get("project_version") or "")
        answer_cache_key = answer_cache_service.build_cache_key(
            project_id=workspace.project_id,
            project_version=project_version,
            question_route=question_route.to_dict(),
            question=question,
            knowledge_status=answer_cache_service.build_knowledge_status(retrieval_payload),
        )
        cached_answer_payload = answer_cache_service.get(answer_cache_key)
        answer_cache_status = "hit" if cached_answer_payload else "miss"
        logger.info(
            "business_runtime stage=compose_response workflow_run_id=%s answer_cache=%s project_version=%s",
            workflow_run_id,
            answer_cache_status,
            project_version,
        )
        planning_service.update_step_status(workspace, plan, "compose_response", "in_progress")
        business_progress_service.report(
            workspace,
            stage="compose_response",
            message="正在汇总结论并生成回复",
            status="in_progress",
        )
        compose_started_at = perf_counter()
        harness_guard: dict[str, Any] = {
            "passed": True,
            "action": "disabled",
            "severity": "none",
            "violations": [],
            "review_only": True,
        }
        answer_quality: dict[str, Any] = {}
        try:
            if cached_answer_payload is not None:
                fused_answer = str(cached_answer_payload.get("answer") or "")
                harness_guard = dict(cached_answer_payload.get("harness_guard") or harness_guard)
                answer_quality = dict(cached_answer_payload.get("answer_quality") or {})
                self._publish_answer_text(fused_answer)
            else:
                answer_text = ""

                if analysis_result.get("report_mode") == "existing_html_report_summary":
                    answer_text, _ = await business_response_service._stream_with_project_deltas(
                        business_response_service.stream_existing_html_report_answer(
                            analysis_result=analysis_result,
                        )
                    )
                elif analysis_result.get("analysis_status") == "timeout":
                    fallback_text = business_response_service.build_fallback_answer(
                        analysis_result=analysis_result,
                        retrieval_payload=retrieval_payload,
                        experience_summary=experience_summary,
                    )

                    async def timeout_answer_stream():
                        yield fallback_text

                    answer_text, _ = await business_response_service._stream_with_project_deltas(
                        timeout_answer_stream()
                    )
                else:
                    answer_text, _ = await business_agent_concurrency_service.run_answer_llm(
                        lambda: business_response_service._stream_with_project_deltas(
                            business_response_service.stream_fused_answer(
                                question=question,
                                analysis_result=analysis_result,
                                retrieval_payload=retrieval_payload,
                                experience_summary=experience_summary,
                            )
                        ),
                        workflow_run_id=workflow_run_id,
                    )
                fused_answer = answer_text
                final_answer, final_guard = await self._enforce_project_answer_guard(
                    answer=fused_answer,
                    analysis_result=analysis_result,
                    question_route=question_route,
                )
                final_answer, answer_quality, final_guard = await self._apply_answer_quality_gate(
                    answer=final_answer,
                    analysis_result=analysis_result,
                    question_route=question_route,
                    harness_guard=final_guard,
                )
                if final_answer != fused_answer:
                    publish_project_answer_final(final_answer)
                fused_answer = final_answer
                harness_guard = final_guard if final_guard else harness_guard
                answer_cache_service.set(
                    answer_cache_key,
                    {
                        "answer": fused_answer,
                        "harness_guard": harness_guard,
                        "answer_quality": answer_quality,
                    },
                )
        except Exception as exc:
            logger.warning("business runtime fused answer generation failed: %s", str(exc))
            fused_answer = business_response_service.build_fallback_answer(
                analysis_result=analysis_result,
                retrieval_payload=retrieval_payload,
                experience_summary=experience_summary,
            )
            publish_project_answer_delta(fused_answer)
            final_answer, final_guard = await self._enforce_project_answer_guard(
                answer=fused_answer,
                analysis_result=analysis_result,
                question_route=question_route,
            )
            final_answer, answer_quality, final_guard = await self._apply_answer_quality_gate(
                answer=final_answer,
                analysis_result=analysis_result,
                question_route=question_route,
                harness_guard=final_guard,
            )
            if final_answer != fused_answer:
                publish_project_answer_final(final_answer)
            fused_answer = final_answer
            harness_guard = final_guard if final_guard else harness_guard
        business_progress_service.report(
            workspace,
            stage="harness_guard",
            message=self._harness_guard_progress_message(harness_guard),
            status="completed",
            detail={
                "output_mode": plan.get("output_mode", "qa"),
                "harness_guard_action": harness_guard.get("action"),
                "harness_guard_passed": harness_guard.get("passed"),
                "harness_guard_severity": harness_guard.get("severity"),
                "violation_count": len(harness_guard.get("violations") or []),
                "answer_quality_score": answer_quality.get("score"),
                "answer_quality_status": answer_quality.get("status"),
                "answer_quality_repair_applied": answer_quality.get("repair_applied", False),
            },
        )
        planning_service.update_step_status(
            workspace,
            plan,
            "compose_response",
            "completed",
            detail={
                "output_mode": plan.get("output_mode", "qa"),
                "answer_cache": answer_cache_status,
                "harness_guard_action": harness_guard.get("action"),
                "harness_guard_passed": harness_guard.get("passed"),
                "answer_quality_score": answer_quality.get("score"),
            },
        )
        business_progress_service.report(
            workspace,
            stage="compose_response",
            message="最终结果生成完成",
            status="completed",
            detail={
                "output_mode": plan.get("output_mode", "qa"),
                "answer_cache": answer_cache_status,
                "harness_guard_action": harness_guard.get("action"),
                "harness_guard_passed": harness_guard.get("passed"),
                "answer_quality_score": answer_quality.get("score"),
                "answer_quality_repair_applied": answer_quality.get("repair_applied", False),
            },
        )
        logger.info(
            "business_runtime stage=compose_response workflow_run_id=%s output_chars=%d answer_cache=%s duration_ms=%.2f",
            workflow_run_id,
            len(fused_answer or ""),
            answer_cache_status,
            (perf_counter() - compose_started_at) * 1000,
        )

        analysis_result = self._remove_internal_fields(analysis_result)
        analysis_result["answer"] = fused_answer
        analysis_result["harness_guard"] = harness_guard
        analysis_result["answer_quality"] = answer_quality
        result_payload = {
            "output_mode": plan.get("output_mode", "qa"),
            "answer": fused_answer,
            "report": analysis_result.get("report", ""),
            "knowledge_retrieval": retrieval_payload,
            "used_knowledge": bool(retrieval_payload.get("documents")),
            "experience_summary": experience_summary,
            "harness_guard": harness_guard,
            "answer_quality": answer_quality,
            "fact_verification": analysis_result.get("fact_verification", {}),
        }

        memory_started_at = perf_counter()
        memory_analysis_result = copy.deepcopy(analysis_result)
        global_experience_analysis_result = copy.deepcopy(analysis_result)
        background_task_service.submit(
            "update_project_memory",
            project_memory_service.update_memory,
            workspace.project_id,
            memory_analysis_result,
        )
        background_task_service.submit(
            "record_global_experience",
            experience_service.record_global_experience,
            project_id=workspace.project_id,
            analysis_result=global_experience_analysis_result,
        )
        background_task_queue_size = background_task_service.queue_size()
        project_memory = {
            "status": "queued",
            "project_id": workspace.project_id,
        }
        business_progress_service.report(
            workspace,
            stage="memory_update",
            message="分析完成，正在保存项目记忆",
            status="completed",
            detail={
                "files_total": len(evidence_files),
                "files_done": files_done,
                "files_failed": files_failed,
                "warning_count": len(analysis_result.get("warnings", [])),
                "project_id": workspace.project_id,
                "background_task_queue_size": background_task_queue_size,
            },
        )
        logger.info(
            "business_runtime stage=memory_update workflow_run_id=%s background_task_queue_size=%d duration_ms=%.2f",
            workflow_run_id,
            background_task_queue_size,
            (perf_counter() - memory_started_at) * 1000,
        )

        workflow_trace = {
            "workflow_run_id": workflow_run_id,
            "status": "warning" if analysis_result.get("warnings") else "ok",
            "duration_ms": round((perf_counter() - workflow_started_at) * 1000, 2),
            "analysis_run_id": analysis_result.get("run_id"),
            "warning_count": len(analysis_result.get("warnings", [])),
            "analysis_cache": analysis_result.get("analysis_cache", "miss"),
            "snapshot": analysis_result.get("snapshot", {}),
            "task_plan_path": str(workspace.task_plan_path),
            "workspace_root": str(workspace.workspace_root),
            "progress_path": str(workspace.progress_path),
            "harness_guard": harness_guard,
            "answer_quality": answer_quality,
            "fact_verification": analysis_result.get("fact_verification", {}),
            "background_task_queue_size": background_task_queue_size,
        }
        logger.info(
            "business_runtime workflow_run_id=%s analysis_run_id=%s status=%s duration_ms=%.2f",
            workflow_run_id,
            analysis_result.get("run_id"),
            workflow_trace["status"],
            workflow_trace["duration_ms"],
        )

        # 主分析所有 LLM 调用已完成，现在可以安全启动 auto report summary（不会再争用 API 并发）
        if not _suppress_auto_report:
            self._schedule_auto_report_summary(
                user_id=user_id,
                session_id=session_id,
                project_id=str(identified["project_id"]),
                project_root=str(identified["project_root"]),
            )

        return {
            "success": True,
            "needs_confirmation": False,
            "identified_project": identified,
            "project_memory": project_memory,
            "data": analysis_result,
            "result_payload": result_payload,
            "task_plan": plan,
            "workspace": workspace_snapshot,
            "workflow_trace": workflow_trace,
        }


# 模块级单例，供其他模块直接 import 使用
business_agent_runtime_service = BusinessAgentRuntimeService()
