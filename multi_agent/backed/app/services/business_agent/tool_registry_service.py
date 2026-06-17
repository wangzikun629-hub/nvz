from __future__ import annotations

import re
from typing import Any


class BusinessToolRegistryService:
    """Registry for delegated and loop-executable business analysis tools."""

    TOOLS: tuple[dict[str, Any], ...] = (
        {
            "name": "read_metric_table",
            "description": "读取与当前问题相关的结构化项目指标表。",
            "request_type": "metric_table",
            "handles_metrics": [
                "adapter_percent",
                "mapping_rate_percent",
                "unique_mapping_rate_percent",
                "duplicate_rate_percent",
                "chrmt_pt_rate_percent",
                "mt_rate_percent",
                "frip",
                "frip_ratio",
                "peak_count",
                "correlation",
                "q30_ratio",
            ],
            "keywords": ["metric", "qc", "指标", "数值", "mapping", "adapter", "frip", "peak"],
            "execution_status": "delegated",
            "executor": "project_analysis_service",
            "output_contract": "样本、指标值、来源文件、来源字段、公式来源和阈值来源。",
        },
        {
            "name": "read_script_rule_source",
            "description": "读取项目脚本、README、SOP 或报告说明，确认公式和阈值来源。",
            "request_type": "script_or_rule_source",
            "handles_metrics": ["all", "overview"],
            "keywords": ["script", "code", "readme", "sop", "公式", "脚本", "阈值", "标准", "来源"],
            "execution_status": "delegated",
            "executor": "project_context_service",
            "output_contract": "formula_source、formula、threshold_source、matched_files 和证据限制。",
        },
        {
            "name": "generate_chart_data",
            "description": "生成前端图表所需的数据结构。",
            "request_type": "chart_data",
            "handles_metrics": ["all", "overview", "frip_ratio", "peak_count", "correlation"],
            "keywords": ["chart", "plot", "heatmap", "scatter", "barplot", "画图", "图表"],
            "execution_status": "delegated",
            "executor": "project_chart_service",
            "output_contract": "chart_type、samples、series 和图表解释要点。",
        },
        {
            "name": "compare_projects",
            "description": "按同流程、同物种和同指标口径比较历史项目。",
            "request_type": "historical_project",
            "handles_metrics": ["all", "overview"],
            "keywords": ["compare", "history", "previous", "对比", "历史项目"],
            "execution_status": "delegated",
            "executor": "project_comparison_service",
            "output_contract": "对照项目、可比性限制、共有指标差异和不可比较原因。",
        },
        {
            "name": "diagnose_cuttag_adapter_readthrough",
            "description": "诊断 CUT&Tag/CUT&RUN 短片段造成的 adapter read-through。",
            "request_type": "diagnostic",
            "handles_metrics": ["adapter_percent", "q30_ratio", "clean_reads"],
            "keywords": ["cut&tag", "cuttag", "cut&run", "adapter", "接头", "read-through"],
            "execution_status": "delegated",
            "executor": "project_cuttag_diagnostic_service",
            "output_contract": "adapter 观测值、证据缺口、处理阶段和下一步复核项。",
        },
        {
            "name": "diagnose_cuttag_alignment_loss",
            "description": "诊断比对率、唯一比对率和细胞器 reads 相关原因链。",
            "request_type": "diagnostic",
            "handles_metrics": [
                "mapping_rate_percent",
                "unique_mapping_rate_percent",
                "chrmt_pt_rate_percent",
                "mt_rate_percent",
            ],
            "keywords": ["cut&tag", "cuttag", "mapping", "unique", "chrmt", "organelle", "线粒体"],
            "execution_status": "delegated",
            "executor": "project_cuttag_diagnostic_service",
            "output_contract": "比对与细胞器证据、参考基因组口径和验证项。",
        },
        {
            "name": "diagnose_cuttag_duplicate_policy",
            "description": "结合文库复杂度和去重配置诊断 duplicate 指标。",
            "request_type": "diagnostic",
            "handles_metrics": ["duplicate_rate_percent", "frip_ratio", "peak_count"],
            "keywords": ["cut&tag", "cuttag", "duplicate", "duplication", "keep-dup", "重复"],
            "execution_status": "delegated",
            "executor": "project_cuttag_diagnostic_service",
            "output_contract": "duplicate 观测值、去重配置、文库复杂度和影响。",
        },
        {
            "name": "diagnose_cuttag_frip_peak_quality",
            "description": "结合 FRiP、peak 数量、peak calling 参数和样本角色诊断富集质量。",
            "request_type": "diagnostic",
            "handles_metrics": ["frip", "frip_ratio", "peak_count", "correlation"],
            "keywords": ["cut&tag", "cuttag", "frip", "peak", "macs", "correlation", "富集"],
            "execution_status": "delegated",
            "executor": "project_cuttag_diagnostic_service",
            "output_contract": "FRiP/peak/相关性证据、样本角色和验证项。",
        },
        {
            "name": "diagnose_cuttag_sample_correlation",
            "description": "结合相关性矩阵、样本角色和上游 QC 复核样本一致性。",
            "request_type": "diagnostic",
            "handles_metrics": ["correlation"],
            "keywords": ["cut&tag", "cuttag", "correlation", "spearman", "pearson", "相关性", "重复"],
            "execution_status": "delegated",
            "executor": "project_cuttag_diagnostic_service",
            "output_contract": "相关样本对、样本角色限制、上游 QC 和验证项。",
        },
        {
            "name": "run_qc_expert",
            "description": "读取 cutadapt/fastp/FastQC 证据并区分 raw、trim 和 clean 阶段。",
            "request_type": "diagnostic",
            "handles_metrics": ["adapter_percent", "q20_ratio", "q30_ratio", "clean_reads"],
            "keywords": ["adapter", "cutadapt", "fastp", "fastqc", "trim", "qc", "接头"],
            "execution_status": "executable",
            "executor": "project_expert_tool_service.run_qc_expert",
            "output_contract": "Evidence Card 列表、命中文件、缺失证据和处理阶段。",
        },
        {
            "name": "run_alignment_expert",
            "description": "读取 alignment summary、mt_stat、NRF/PBC 和 library complexity 证据。",
            "request_type": "diagnostic",
            "handles_metrics": [
                "mapping_rate_percent",
                "unique_mapping_rate_percent",
                "duplicate_rate_percent",
                "chrmt_pt_rate_percent",
                "mt_rate_percent",
            ],
            "keywords": ["alignment", "mapping", "mitochondrial", "mt_stat", "nrf", "pbc", "picard"],
            "execution_status": "executable",
            "executor": "project_expert_tool_service.run_alignment_expert",
            "output_contract": "Evidence Card 列表、读数分子分母、命中文件和证据缺口。",
        },
        {
            "name": "run_enrichment_expert",
            "description": "读取 FRiP、peak、TSS、fragment size、spike-in、control 和相关性结果，并保留样本角色边界。",
            "request_type": "diagnostic",
            "handles_metrics": [
                "frip",
                "frip_ratio",
                "peak_count",
                "peak_width",
                "tss_enrichment",
                "fragment_size",
                "spikein_mapped_reads",
                "spikein_unique_mapping_rate_percent",
                "spikein_scaling_factor",
                "control_binding_status",
                "correlation",
            ],
            "keywords": [
                "frip",
                "peak",
                "macs",
                "tss",
                "fragment",
                "spike-in",
                "spikein",
                "control",
                "igg",
                "input",
                "correlation",
                "enrichment",
                "富集",
            ],
            "execution_status": "executable",
            "executor": "project_expert_tool_service.run_enrichment_expert",
            "output_contract": "Evidence Card 列表、命中文件、样本角色限制和验证建议。",
        },
    )

    @classmethod
    def list_tools(cls) -> list[dict[str, Any]]:
        return [dict(item) for item in cls.TOOLS]

    @classmethod
    def select_tools(
        cls,
        *,
        question: str,
        target_metrics: list[str] | tuple[str, ...] | None,
        intent: str,
        evidence_requests: list[dict[str, Any]] | None = None,
        skill_references: list[dict[str, Any]] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        metrics = {str(item).strip().lower() for item in (target_metrics or []) if str(item).strip()}
        if not metrics:
            metrics.add("overview")
        normalized_question = str(question or "").lower()
        request_types = {
            str(item.get("type") or "").strip()
            for item in (evidence_requests or [])
            if isinstance(item, dict)
        }
        skill_text = " ".join(
            " ".join(str(ref.get(key) or "") for key in ("id", "title", "source", "guidance"))
            for ref in (skill_references or [])
            if isinstance(ref, dict)
        ).lower()

        scored: list[tuple[int, dict[str, Any]]] = []
        for tool in cls.TOOLS:
            score = cls._score_tool(
                tool,
                question=normalized_question,
                skill_text=skill_text,
                metrics=metrics,
                request_types=request_types,
                intent=intent,
            )
            if score > 0:
                scored.append((score, tool))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("name") or "")))
        return [cls._public_tool(tool) for _, tool in scored[:limit]]

    @staticmethod
    def _score_tool(
        tool: dict[str, Any],
        *,
        question: str,
        skill_text: str,
        metrics: set[str],
        request_types: set[str],
        intent: str,
    ) -> int:
        score = 0
        handles = {str(item).lower() for item in tool.get("handles_metrics", []) or []}
        if metrics.intersection(handles) or "all" in handles:
            score += 7
        elif "overview" in handles and "overview" in metrics:
            score += 5
        request_type = str(tool.get("request_type") or "")
        if request_type in request_types:
            score += 4
        if intent == "chart_request" and tool.get("name") == "generate_chart_data":
            score += 5
        if intent == "project_comparison" and tool.get("name") == "compare_projects":
            score += 5
        for keyword in tool.get("keywords", []) or []:
            token = str(keyword or "").lower()
            if token and (token in question or token in skill_text):
                score += 2
        if tool.get("execution_status") == "executable":
            score += 1
        return score

    @staticmethod
    def _public_tool(tool: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": str(tool.get("name") or ""),
            "description": re.sub(r"\s+", " ", str(tool.get("description") or "")).strip(),
            "request_type": str(tool.get("request_type") or ""),
            "handles_metrics": list(tool.get("handles_metrics", []) or [])[:10],
            "execution_status": str(tool.get("execution_status") or "planned"),
            "executor": str(tool.get("executor") or ""),
            "output_contract": str(tool.get("output_contract") or ""),
        }


business_tool_registry_service = BusinessToolRegistryService()
