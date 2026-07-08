from __future__ import annotations

import json
import re
from typing import Any

from multi_agent.backed.app.infrastructure.ai.openai_client import SUB_MODEL_NAME, sub_model_client
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.services.business_agent.bio_skill_reference_service import (
    bio_skill_reference_service,
)
from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)
from multi_agent.backed.app.services.business_agent.tool_registry_service import (
    business_tool_registry_service,
)


class AnalysisPlannerService:
    """Question-driven project analysis planner.

    The planner decides what evidence should be prioritized. It does not read
    files or calculate metrics; deterministic backend services execute the plan.
    """

    METRIC_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
        ("adapter_percent", ("adapter", "adapters", "接头", "接头残留"), ("adapter", "cutadapt", "ReadsQC", "fastp")),
        ("mapping_rate_percent", ("mapping", "比对率", "比对", "map rate"), ("AlignmentQC", "mapping")),
        ("unique_mapping_rate_percent", ("unique", "唯一比对", "唯一比对率"), ("AlignmentQC", "unique")),
        ("duplicate_rate_percent", ("duplicate", "duplication", "重复率", "重复"), ("Picard", "duplicate", "duplication")),
        ("chrmt_pt_rate_percent", ("chrmt", "chrmt/pt", "pt", "线粒体", "叶绿体", "细胞器"), ("AlignmentQC", "organelle", "chrMT", "chrPt")),
        ("frip", ("frip", "峰内", "reads 比例", "富集"), ("FRiP", "plotEnrichment", "peak")),
        ("correlation", ("spearman", "pearson", "相关性", "相关系数", "相关"), ("Correlation", "deeptools", "readCounts")),
        ("peak_count", ("peak", "peaks", "峰数量", "富集峰"), ("peak", "narrowPeak", "macs")),
    )

    METRIC_RULES += (
        ("sequencing_depth", ("sequencing depth", "read depth", "clean reads", "raw reads", "测序深度"), ("ReadsQC", "samplelist")),
        ("spikein_mapped_reads", ("spike-in mapped", "spikein mapped", "spike-in reads"), ("SpikeIn", "AlignmentQC", "normalization")),
        ("spikein_unique_mapping_rate_percent", ("spike-in unique", "spikein unique", "spike-in比对率"), ("SpikeIn", "AlignmentQC", "unique")),
        ("spikein_scaling_factor", ("scaling factor", "scale factor", "normalization factor", "spike-in归一化"), ("SpikeIn", "normalization", "scaling")),
        ("fragment_size", ("fragment size", "insert size", "片段长度"), ("FragmentSize", "Picard", "insert_size")),
        ("nrf", ("nrf", "non-redundant fraction"), ("LibraryComplexity", "NRF")),
        ("pbc1", ("pbc1", "bottleneck coefficient 1"), ("LibraryComplexity", "PBC1")),
        ("pbc2", ("pbc2", "bottleneck coefficient 2"), ("LibraryComplexity", "PBC2")),
        ("motif", ("motif", "homer", "基序"), ("Motif", "HOMER")),
        ("peak_overlap", ("peak overlap", "overlap", "idr", "峰重叠"), ("PeakOverlap", "IDR")),
    )

    METRIC_EVIDENCE_GRAPH: dict[str, dict[str, Any]] = {
        "adapter_percent": {
            "primary": ["adapter_percent", "q30_ratio"],
            "upstream": ["fragment_size", "read_length", "cutadapt_params", "library_type"],
            "parallel": ["mt_rate_percent", "duplicate_rate_percent"],
            "downstream": ["mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio", "correlation"],
            "modules": ["ReadsQC", "cutadapt", "fastp", "fragment_size", "AlignmentQC", "FRiP", "Correlation"],
            "candidate_causes": [
                "short_fragment_readthrough",
                "adapter_trimming_parameter_mismatch",
                "high_organelle_or_low_complexity_reads",
                "library_construction_issue",
            ],
        },
        "mapping_rate_percent": {
            "primary": ["mapping_rate_percent", "unique_mapping_rate_percent"],
            "upstream": ["adapter_percent", "q30_ratio", "reference_genome", "bowtie2_params", "mt_rate_percent"],
            "parallel": ["duplicate_rate_percent", "mt_rate_percent"],
            "downstream": ["frip_ratio", "peak_count", "correlation"],
            "modules": ["AlignmentQC", "bowtie2", "samtools", "ReadsQC", "config", "FRiP", "Correlation"],
            "candidate_causes": [
                "reference_genome_mismatch",
                "adapter_or_low_quality_reads",
                "organelle_reads_dominant",
                "multi_mapping_or_repetitive_regions",
            ],
        },
        "unique_mapping_rate_percent": {
            "primary": ["unique_mapping_rate_percent", "mapping_rate_percent"],
            "upstream": ["adapter_percent", "q30_ratio", "reference_genome", "mt_rate_percent"],
            "parallel": ["duplicate_rate_percent", "complexity"],
            "downstream": ["frip_ratio", "peak_count", "correlation"],
            "modules": ["AlignmentQC", "bowtie2", "samtools", "ReadsQC", "FRiP", "Correlation"],
            "candidate_causes": [
                "multi_mapping_or_repetitive_regions",
                "organelle_reads_dominant",
                "reference_genome_mismatch",
                "low_library_complexity",
            ],
        },
        "chrmt_pt_rate_percent": {
            "primary": ["mt_rate_percent"],
            "upstream": ["organelle_chroms", "sample_preparation", "filtering_policy"],
            "parallel": ["mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent"],
            "downstream": ["frip_ratio", "peak_count", "correlation"],
            "modules": ["AlignmentQC", "organelle", "config", "samtools", "FRiP", "Correlation"],
            "candidate_causes": [
                "organelle_dna_background",
                "organelle_filtering_not_applied_before_statistics",
                "sample_preparation_background",
            ],
        },
        "mt_rate_percent": {
            "primary": ["mt_rate_percent"],
            "upstream": ["organelle_chroms", "sample_preparation", "filtering_policy"],
            "parallel": ["mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent"],
            "downstream": ["frip_ratio", "peak_count", "correlation"],
            "modules": ["AlignmentQC", "organelle", "config", "samtools", "FRiP", "Correlation"],
            "candidate_causes": [
                "organelle_dna_background",
                "organelle_filtering_not_applied_before_statistics",
                "sample_preparation_background",
            ],
        },
        "duplicate_rate_percent": {
            "primary": ["duplicate_rate_percent", "complexity"],
            "upstream": ["library_complexity", "pcr_cycles", "mt_rate_percent"],
            "parallel": ["mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio"],
            "downstream": ["peak_count", "correlation"],
            "modules": ["AlignmentQC", "Picard", "duplicate", "FRiP", "PeakStat", "Correlation"],
            "candidate_causes": [
                "low_library_complexity",
                "true_enrichment_duplication",
                "organelle_or_repetitive_reads",
                "pcr_amplification_bias",
            ],
        },
        "frip": {
            "primary": ["frip_ratio", "peak_count"],
            "upstream": ["mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent", "mt_rate_percent"],
            "parallel": ["peak_width", "reads_in_peaks", "background_signal"],
            "downstream": ["correlation", "biological_interpretation"],
            "modules": ["FRiP", "PeakStat", "MACS", "SEACR", "AlignmentQC", "Correlation"],
            "candidate_causes": [
                "insufficient_effective_reads",
                "high_background",
                "weak_target_enrichment",
                "peak_calling_parameter_issue",
                "missing_or_mismatched_control",
            ],
        },
        "frip_ratio": {
            "primary": ["frip_ratio", "peak_count"],
            "upstream": ["mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent", "mt_rate_percent"],
            "parallel": ["peak_width", "reads_in_peaks", "background_signal"],
            "downstream": ["correlation", "biological_interpretation"],
            "modules": ["FRiP", "PeakStat", "MACS", "SEACR", "AlignmentQC", "Correlation"],
            "candidate_causes": [
                "insufficient_effective_reads",
                "high_background",
                "weak_target_enrichment",
                "peak_calling_parameter_issue",
                "missing_or_mismatched_control",
            ],
        },
        "correlation": {
            "primary": ["correlation"],
            "upstream": ["sample_group", "mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio", "peak_count"],
            "parallel": ["pca", "peak_overlap", "read_count_matrix"],
            "downstream": ["replicate_consistency", "differential_analysis_reliability"],
            "modules": ["Correlation", "deeptools", "readCounts", "AlignmentQC", "FRiP", "PeakStat", "samplelist"],
            "candidate_causes": [
                "weak_signal_noise_dominated_bins",
                "sample_role_or_group_mismatch",
                "upstream_qc_or_enrichment_issue",
                "incorrect_correlation_feature_space",
            ],
        },
    }

    METRIC_EVIDENCE_GRAPH.update(
        {
            "spikein_scaling_factor": {
                "primary": ["spikein_scaling_factor"],
                "upstream": ["spikein_mapped_reads", "spikein_unique_mapping_rate_percent", "sequencing_depth"],
                "parallel": ["mapping_rate_percent", "frip_ratio"],
                "downstream": ["normalized_signal_comparison"],
                "modules": ["SpikeIn", "normalization", "AlignmentQC", "config"],
                "candidate_causes": ["scaling_factor_not_generated", "spikein_alignment_evidence_incomplete", "normalization_parameters_missing"],
            },
            "spikein_unique_mapping_rate_percent": {
                "primary": ["spikein_unique_mapping_rate_percent", "spikein_mapped_reads"],
                "upstream": ["sequencing_depth", "spikein_reference"],
                "parallel": ["spikein_scaling_factor"],
                "downstream": ["normalized_signal_comparison"],
                "modules": ["SpikeIn", "AlignmentQC", "normalization"],
                "candidate_causes": ["spikein_reference_mismatch", "insufficient_spikein_reads"],
            },
            "fragment_size": {
                "primary": ["fragment_size"],
                "upstream": ["library_type", "tagmentation"],
                "parallel": ["nrf", "pbc1", "pbc2", "duplicate_rate_percent"],
                "downstream": ["peak_count", "frip_ratio"],
                "modules": ["FragmentSize", "Picard", "AlignmentQC", "FRiP"],
                "candidate_causes": ["tagmentation_distribution_shift", "library_complexity_issue"],
            },
            "nrf": {
                "primary": ["nrf", "pbc1", "pbc2"],
                "upstream": ["sequencing_depth", "fragment_size"],
                "parallel": ["duplicate_rate_percent"],
                "downstream": ["peak_count", "frip_ratio", "correlation"],
                "modules": ["LibraryComplexity", "NRF", "PBC"],
                "candidate_causes": ["library_bottleneck", "low_input_complexity"],
            },
            "pbc1": {
                "primary": ["nrf", "pbc1", "pbc2"],
                "upstream": ["sequencing_depth", "fragment_size"],
                "parallel": ["duplicate_rate_percent"],
                "downstream": ["peak_count", "frip_ratio", "correlation"],
                "modules": ["LibraryComplexity", "NRF", "PBC"],
                "candidate_causes": ["library_bottleneck", "low_input_complexity"],
            },
            "pbc2": {
                "primary": ["nrf", "pbc1", "pbc2"],
                "upstream": ["sequencing_depth", "fragment_size"],
                "parallel": ["duplicate_rate_percent"],
                "downstream": ["peak_count", "frip_ratio", "correlation"],
                "modules": ["LibraryComplexity", "NRF", "PBC"],
                "candidate_causes": ["library_bottleneck", "low_input_complexity"],
            },
            "motif": {
                "primary": ["motif"],
                "upstream": ["peak_count", "frip_ratio", "peak_overlap"],
                "parallel": ["correlation"],
                "downstream": ["biological_interpretation"],
                "modules": ["Motif", "HOMER", "PeakStat", "FRiP"],
                "candidate_causes": ["biological_regulatory_difference", "peak_background_or_selection_bias"],
            },
        }
    )

    SAMPLE_PATTERN = re.compile(r"\b(?:T\d+|C\d+|IP\d+|IgG|Input|Ctrl|Control)\b", re.IGNORECASE)
    INTEGRATIVE_REASONING_TERMS = (
        "综合分析",
        "整合分析",
        "联合分析",
        "多指标",
        "多维",
        "交叉",
        "cross-frip",
        "cross frip",
        "矩阵",
        "技术偏差",
        "生物学差异",
        "是否合理",
        "vs",
        "versus",
        "对比",
        "比较",
        "motif",
        "peak",
        "spike-in",
        "spikein",
    )
    MATRIX_REASONING_TERMS = (
        "矩阵",
        "cross-frip",
        "cross frip",
        "交叉frip",
        "交叉 frip",
        "spearman",
        "pearson",
        "相关矩阵",
        "peak set",
    )
    FACT_LAYER_TERMS = ("技术偏差", "生物学差异", "实验设计", "对照", "hypothesis")
    ALLOWED_INTENTS = {
        "metric_explanation",
        "anomaly_investigation",
        "project_comparison",
        "chart_request",
        "report_summary",
        "project_overview",
    }
    ALLOWED_REQUEST_TYPES = {
        "metric_table",
        "script_or_rule_source",
        "historical_project",
        "chart_data",
        "project_summary",
    }
    ALLOWED_METRICS = {item[0] for item in METRIC_RULES}
    ALLOWED_METRICS.update(
        {
            "frip_ratio",
            "mt_rate_percent",
            "q30_ratio",
            "all",
            "overview",
            "spikein_mapped_reads",
            "spikein_unique_mapping_rate_percent",
            "spikein_scaling_factor",
            "fragment_size",
            "nrf",
            "pbc1",
            "pbc2",
            "motif",
            "peak_overlap",
            "sequencing_depth",
        }
    )

    @classmethod
    def _registered_metric_choices(cls) -> set[str]:
        """A0-3（project_analysis_exploration_and_evolution_plan.md Stage A0）：
        planner 可选的指标集合，从"手写的 ALLOWED_METRICS 子集"扩为
        "ALLOWED_METRICS 里的特殊 token（all/overview 等）∪ metric_schema_service
        的完整注册表"。改造前 LLM planner 即使被放行调用，产出的
        silva_total_ratio_percent 这类未在 ALLOWED_METRICS 手写清单里的已注册指标，
        也会在 `_normalize_metrics`/`_normalize_evidence_requests` 里被直接过滤掉——
        这是指标层放权本该修的核心缺口，而不只是门控问题。
        """
        return set(cls.ALLOWED_METRICS) | set(metric_schema_service.all_metric_ids())

    @classmethod
    def _metric_catalog_prompt_lines(cls) -> list[str]:
        """把 metric_schema_service 的注册表渲染成给 LLM planner 看的紧凑目录：
        metric_id + 中文/英文 label + 适用实验类型 + detection_signature 同义词。
        让 planner 能凭同义词/口语描述匹配已注册指标（比如用户说"核糖体RNA比例"
        而不是逐字打出 silva_total_ratio_percent），而不再依赖字面命中。
        """
        lines: list[str] = []
        for metric_id in sorted(metric_schema_service.all_metric_ids()):
            schema = metric_schema_service.get(metric_id)
            if not schema:
                continue
            label = str(schema.get("label") or "")
            assays = ",".join(schema.get("assay_scope") or ["all"])
            signature_tokens = [str(token) for token in (schema.get("detection_signature") or [])][:6]
            aka = ",".join(signature_tokens)
            lines.append(f"- {metric_id} | {label} | assay={assays}" + (f" | aka: {aka}" if aka else ""))
        return lines

    @classmethod
    def build_plan(
        cls,
        *,
        question: str,
        project_id: str,
        question_route: dict[str, Any] | None = None,
        experience_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = str(question or "").strip()
        lowered = normalized.lower()
        route = question_route or {}
        intent = cls._infer_intent(lowered, route)
        target_metrics = cls._infer_metrics(lowered, route)
        target_samples = cls._infer_samples(normalized)
        metric_evidence_plan = cls._build_metric_evidence_plan(target_metrics)
        related_metrics = cls._related_metrics(metric_evidence_plan)
        planner_metrics = cls._merge_metrics(target_metrics, related_metrics)
        evidence_requests = cls._build_evidence_requests(intent, target_metrics, lowered, metric_evidence_plan)
        bio_skill_references = bio_skill_reference_service.select_references(
            question=normalized,
            target_metrics=planner_metrics,
            intent=intent,
        )
        loaded_bio_skills = bio_skill_reference_service.load_full_skills(bio_skill_references)
        bio_skill_index = bio_skill_reference_service.index_stats()
        prioritized_evidence_hints = cls._build_evidence_hints(evidence_requests)
        prioritized_evidence_hints = list(
            dict.fromkeys(
                prioritized_evidence_hints
                + bio_skill_reference_service.evidence_hints(bio_skill_references)
            )
        )[:20]
        selected_tools = business_tool_registry_service.select_tools(
            question=normalized,
            target_metrics=planner_metrics,
            intent=intent,
            evidence_requests=evidence_requests,
            skill_references=bio_skill_references,
        )
        response_plan = cls._build_response_plan(
            question=normalized,
            intent=intent,
            target_metrics=target_metrics,
            evidence_requests=evidence_requests,
            question_route=route,
        )

        return {
            "planner_version": "question-driven-v3",
            "project_id": project_id,
            "intent": intent,
            "target_metrics": target_metrics,
            "related_metrics": related_metrics,
            "target_samples": target_samples,
            "metric_evidence_plan": metric_evidence_plan,
            "evidence_requests": evidence_requests,
            "prioritized_metrics": target_metrics,
            "prioritized_evidence_hints": prioritized_evidence_hints,
            "bio_skill_references": bio_skill_references,
            "loaded_bio_skills": loaded_bio_skills,
            "bio_skill_index": bio_skill_index,
            "selected_tools": selected_tools,
            "response_plan": response_plan,
            "requires_script_review": cls._requires_script_review(lowered, intent),
            "answer_boundary": {
                "allow": [
                    "指出单项指标偏高、偏低或需关注",
                    "解释指标可能原因和后续复核方向",
                    "说明通用参考阈值或行业经验阈值，并标注其不是项目专属标准",
                    "在复杂问题中比较技术偏差、生物学差异与实验设计问题的相对支持度",
                ],
                "forbid": [
                    "判断整体项目合格或不合格",
                    "判断项目是否可交付或可放心进入下游分析",
                    "把未在项目文件中确认的通用阈值写成项目专属验收标准",
                ],
            },
            "planning_notes": cls._build_notes(intent, target_metrics, target_samples, experience_summary or {}),
        }

    @classmethod
    async def build_plan_with_llm(
        cls,
        *,
        question: str,
        project_id: str,
        question_route: dict[str, Any] | None = None,
        experience_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fallback = cls.build_plan(
            question=question,
            project_id=project_id,
            question_route=question_route,
            experience_summary=experience_summary,
        )
        use_llm, reason = cls._should_use_llm_planner(
            question=question,
            question_route=question_route or {},
            fallback=fallback,
        )
        if not use_llm:
            fallback["planner_version"] = "rule-question-driven-v1"
            fallback["planner_fallback_used"] = False
            fallback["planner_llm_skipped"] = True
            fallback["planner_skip_reason"] = reason
            return fallback

        messages = cls._build_llm_messages(
            question=question,
            project_id=project_id,
            question_route=question_route or {},
            fallback=fallback,
        )
        try:
            response = await sub_model_client.chat.completions.create(
                model=SUB_MODEL_NAME,
                messages=messages,
                temperature=0,
                max_tokens=900,
                stream=False,
            )
            raw = response.choices[0].message.content or ""
            parsed = cls._parse_json(raw)
            plan = cls._normalize_llm_plan(
                parsed,
                fallback=fallback,
                question=question,
                project_id=project_id,
                question_route=question_route,
                experience_summary=experience_summary,
            )
            plan["planner_version"] = "llm-question-driven-v1"
            plan["planner_fallback_used"] = False
            plan["planner_llm_skipped"] = False
            return plan
        except Exception as exc:
            logger.warning("analysis_planner llm fallback_used error=%s", str(exc))
            fallback["planner_fallback_used"] = True
            fallback["planner_error"] = str(exc)
            return fallback

    @classmethod
    def _should_use_llm_planner(
        cls,
        *,
        question: str,
        question_route: dict[str, Any],
        fallback: dict[str, Any],
    ) -> tuple[bool, str]:
        """Use the planner model only when deterministic routing is likely insufficient."""
        normalized = " ".join(str(question or "").split()).strip().lower()
        route = str((question_route or {}).get("route") or "").strip()
        intent = str(fallback.get("intent") or "").strip()
        if cls.should_force_rule_planner(
            question_route=question_route,
            question=question,
            fallback=fallback,
        ):
            return False, "force_rule_planner"
        if route in {"project_compare", "ai_report_summary"}:
            return True, f"route={route}"
        # A0-1（project_analysis_exploration_and_evolution_plan.md Stage A0）：
        # 宽泛/诊断类问题一律放行给 LLM planner，不再靠 should_force_rule_planner
        # 的关键词门控拦截——这正是"换个问法→走不同 planner→选出不同指标"这次真实
        # bug 的直接成因。project_overview/anomaly_investigation 对应
        # project_analysis_service 里的 overview/diagnostic 问题分类，本来就应该让
        # planner 有机会从已注册指标全集里挑，而不是被 force_rule_planner 拦下来。
        if intent in {"project_comparison", "report_summary", "anomaly_investigation", "project_overview"}:
            return True, f"intent={intent}"
        if not fallback.get("target_metrics"):
            return True, "no_target_metrics"
        broad_or_planning_terms = (
            "整体",
            "总结",
            "报告",
            "对比",
            "比较",
            "画图",
            "图表",
            "分析路线",
            "怎么分析",
            "规划",
        )
        if any(term in normalized for term in broad_or_planning_terms):
            return True, "complex_or_broad_question"
        return False, "fast_metric_question"

    @classmethod
    def should_force_rule_planner(
        cls,
        *,
        question_route: dict[str, Any],
        question: str,
        fallback: dict[str, Any],
    ) -> bool:
        """A0-1（Stage A0）收窄后的确定性快路径。

        改造前这里用一长串宽松条件（多指标子集、<=2 指标、<=2 工具、任意关键词命中
        即可）几乎把所有问题都拦回规则 planner，导致"换个问法→走不同 planner→
        选出不同指标"。收窄后只保留一条真正零成本、行为不应变化的快路径：
        **明确单指标 + 该指标是已调好的通用指标 + 问题里有强关键词命中**。
        其余情况一律交给上层 `_should_use_llm_planner` 按 intent 决定是否放权给
        LLM planner，不再用指标数量/工具数量这类粗粒度信号代替真正的意图判断。
        """
        normalized = " ".join(str(question or "").split()).strip().lower()
        route = str((question_route or {}).get("route") or "").strip()
        target_metrics = list(fallback.get("target_metrics", []) or [])
        common_metric_targets = {
            "adapter_percent",
            "mapping_rate_percent",
            "unique_mapping_rate_percent",
            "duplicate_rate_percent",
            "mt_rate_percent",
            "frip_ratio",
            "correlation",
        }
        if route == "chart":
            return True
        metric_hint_terms = (
            "frip",
            "mapping",
            "duplicate",
            "correlation",
            "mt",
            "qc",
            "图",
            "鍥捐〃",
        )
        if (
            len(target_metrics) == 1
            and target_metrics[0] in common_metric_targets
            and any(term in normalized for term in metric_hint_terms)
        ):
            return True
        return False

    @classmethod
    def _build_llm_messages(
        cls,
        *,
        question: str,
        project_id: str,
        question_route: dict[str, Any],
        fallback: dict[str, Any],
    ) -> list[dict[str, str]]:
        request_types = ", ".join(sorted(cls.ALLOWED_REQUEST_TYPES))
        intents = ", ".join(sorted(cls.ALLOWED_INTENTS))
        metric_catalog_lines = cls._metric_catalog_prompt_lines()
        metric_catalog_text = "\n".join(metric_catalog_lines) if metric_catalog_lines else "-"
        system_prompt = (
            "你是生物信息项目分析的路线规划智能体，只输出 JSON。"
            "你只负责规划，不读取文件、不计算数值、不下整体项目合格/不合格结论。"
            f"intent 只能取: {intents}。"
            f"evidence_requests.type 只能取: {request_types}。"
            "target_metrics 和 evidence_requests.metric 只能从下面『已注册指标目录』里按 metric_id "
            "选择（可参考 label 与 aka 同义词判断用户问题里口语化描述对应哪个 metric_id），"
            "或者用 overview/all 表示不针对单一指标。"
            "不允许发明目录里没有的 metric_id；如果怀疑项目里存在目录外的新指标，"
            "不要把它塞进 target_metrics，只在 planning_notes 里如实提一句『疑似存在未注册指标』。"
            "允许规划读取指标表、脚本/规则来源、历史项目、绘图数据或项目摘要。"
            "必须保留回答边界：允许指出单项指标高低和通用参考阈值；禁止整体项目合格/交付/下游判断。"
            "只返回 JSON，不要 Markdown，不要解释。\n\n"
            "## 已注册指标目录（metric_id | label | assay | aka 同义词）\n"
            f"{metric_catalog_text}"
        )
        schema_hint = {
            "intent": "metric_explanation | anomaly_investigation | project_comparison | chart_request | report_summary | project_overview",
            "target_metrics": ["adapter_percent"],
            "target_samples": ["T1"],
            "evidence_requests": [
                {
                    "type": "metric_table",
                    "module": "AlignmentQC",
                    "metric": "mapping_rate_percent",
                    "reason": "核对 mapping 观测值",
                }
            ],
            "requires_script_review": True,
            "answer_boundary": {
                "allow": ["指出单项指标高低"],
                "forbid": ["判断整体项目合格或不合格"],
            },
        }
        bio_skill_reference_lines = []
        for item in (fallback.get("bio_skill_references", []) or [])[:4]:
            if isinstance(item, dict):
                bio_skill_reference_lines.append(
                    f"- {item.get('title', '-')}: {item.get('guidance', '-')} "
                    f"(boundary={item.get('boundary', '-')})"
                )
        loaded_skill_lines = [
            f"### {item.get('title', '-')}\n{str(item.get('decision_card') or item.get('content') or '')[:1800]}"
            for item in (fallback.get("loaded_bio_skills", []) or [])[:2]
            if isinstance(item, dict)
        ]
        loaded_skill_context = "\n\n".join(loaded_skill_lines) or "-"
        user_prompt = (
            "## 用户问题\n"
            f"{question}\n\n"
            "## 项目ID\n"
            f"{project_id}\n\n"
            "## 问题路由\n"
            f"{json.dumps(question_route, ensure_ascii=False)[:1600]}\n\n"
            "## 规则 planner 参考结果\n"
            f"{json.dumps(fallback, ensure_ascii=False)[:3000]}\n\n"
            "## Selected bioSkills diagnostic decision cards\n"
            f"{loaded_skill_context}\n\n"
            "## bioSkills 通用参考\n"
            + ("\n".join(bio_skill_reference_lines) if bio_skill_reference_lines else "无")
            + "\n注意：bioSkills 只能用于规划排查路线，不能作为项目专属阈值、SOP、交付标准或合格性判断依据。\n\n"
            "## JSON schema 示例\n"
            f"{json.dumps(schema_hint, ensure_ascii=False)}\n\n"
            "请输出最适合当前问题的分析路线 JSON。"
        )
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            text = match.group(0)
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _normalize_llm_plan(
        cls,
        payload: dict[str, Any],
        *,
        fallback: dict[str, Any],
        question: str,
        project_id: str,
        question_route: dict[str, Any] | None,
        experience_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return fallback
        intent = str(payload.get("intent") or fallback.get("intent") or "project_overview").strip()
        if intent not in cls.ALLOWED_INTENTS:
            intent = str(fallback.get("intent") or "project_overview")

        target_metrics = cls._normalize_metrics(payload.get("target_metrics"), fallback.get("target_metrics", []))
        target_samples = cls._normalize_samples(payload.get("target_samples"), fallback.get("target_samples", []))
        metric_evidence_plan = cls._build_metric_evidence_plan(target_metrics)
        related_metrics = cls._related_metrics(metric_evidence_plan)
        planner_metrics = cls._merge_metrics(target_metrics, related_metrics)
        evidence_requests = cls._normalize_evidence_requests(
            payload.get("evidence_requests"),
            fallback=fallback.get("evidence_requests", []),
            target_metrics=target_metrics,
        )
        evidence_requests = cls._build_evidence_requests(
            intent,
            target_metrics,
            str(question or "").lower(),
            metric_evidence_plan,
        ) if not evidence_requests else evidence_requests
        requires_script_review = bool(
            payload.get("requires_script_review")
            if "requires_script_review" in payload
            else fallback.get("requires_script_review")
        )
        if any(item.get("type") == "script_or_rule_source" for item in evidence_requests):
            requires_script_review = True

        boundary = payload.get("answer_boundary") if isinstance(payload.get("answer_boundary"), dict) else {}
        allow = boundary.get("allow") if isinstance(boundary, dict) else []
        forbid = boundary.get("forbid") if isinstance(boundary, dict) else []
        answer_boundary = {
            "allow": cls._clean_string_list(allow)[:6] or fallback.get("answer_boundary", {}).get("allow", []),
            "forbid": cls._clean_string_list(forbid)[:6] or fallback.get("answer_boundary", {}).get("forbid", []),
        }
        if not any("整体项目" in item or "项目合格" in item for item in answer_boundary["forbid"]):
            answer_boundary["forbid"].append("判断整体项目合格或不合格")

        bio_skill_references = bio_skill_reference_service.select_references(
            question=question,
            target_metrics=planner_metrics,
            intent=intent,
        )
        loaded_bio_skills = bio_skill_reference_service.load_full_skills(bio_skill_references)
        bio_skill_index = bio_skill_reference_service.index_stats()
        prioritized_evidence_hints = cls._build_evidence_hints(evidence_requests)
        prioritized_evidence_hints = list(
            dict.fromkeys(
                prioritized_evidence_hints
                + bio_skill_reference_service.evidence_hints(bio_skill_references)
            )
        )[:20]
        selected_tools = business_tool_registry_service.select_tools(
            question=question,
            target_metrics=planner_metrics,
            intent=intent,
            evidence_requests=evidence_requests,
            skill_references=bio_skill_references,
        )
        response_plan = cls._build_response_plan(
            question=question,
            intent=intent,
            target_metrics=target_metrics,
            evidence_requests=evidence_requests,
            question_route=question_route or {},
        )
        return {
            "planner_version": "llm-question-driven-v3",
            "project_id": project_id,
            "intent": intent,
            "target_metrics": target_metrics,
            "related_metrics": related_metrics,
            "target_samples": target_samples,
            "metric_evidence_plan": metric_evidence_plan,
            "evidence_requests": evidence_requests,
            "prioritized_metrics": target_metrics,
            "prioritized_evidence_hints": prioritized_evidence_hints,
            "bio_skill_references": bio_skill_references,
            "loaded_bio_skills": loaded_bio_skills,
            "bio_skill_index": bio_skill_index,
            "selected_tools": selected_tools,
            "response_plan": response_plan,
            "requires_script_review": requires_script_review,
            "answer_boundary": answer_boundary,
            "planning_notes": cls._build_notes(intent, target_metrics, target_samples, experience_summary or {}),
        }

    @staticmethod
    def _build_response_plan(
        *,
        question: str,
        intent: str,
        target_metrics: list[str],
        evidence_requests: list[dict[str, str]],
        question_route: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reasoning_mode = AnalysisPlannerService._reasoning_mode(
            question=question,
            intent=intent,
            target_metrics=target_metrics,
            evidence_requests=evidence_requests,
            question_route=question_route or {},
        )
        if reasoning_mode == "simple_metric":
            complexity = "simple"
            sections = ["direct_answer", "definition_or_value", "source", "limitation"]
            max_sections = 4
            answer_focus = "single_metric_or_definition"
            token_budget_hint = 1400
        elif reasoning_mode == "integrative_reasoning":
            complexity = "comprehensive"
            sections = [
                "direct_answer",
                "fact_layer",
                "integrated_evidence",
                "hypothesis_comparison",
                "limitations",
                "verification_actions",
            ]
            max_sections = 6
            answer_focus = "integrated_competing_hypotheses"
            token_budget_hint = 5200
        elif intent in {
            "anomaly_investigation",
            "project_comparison",
            "report_summary",
            "project_overview",
        } or len(target_metrics) >= 3 or len(question) > 80:
            complexity = "comprehensive"
            sections = [
                "analysis_plan",
                "direct_observations",
                "evidence_chain",
                "associated_phenomena",
                "possible_explanations",
                "limitations",
                "actions",
            ]
            max_sections = 7
            answer_focus = "multi_metric_structured_analysis"
            token_budget_hint = 3200
        else:
            complexity = "focused"
            sections = [
                "direct_answer",
                "evidence",
                "possible_explanations",
                "limitations",
                "actions",
            ]
            max_sections = 5
            answer_focus = "target_metric_diagnostic"
            token_budget_hint = 2200
        expected_modalities = AnalysisPlannerService._expected_evidence_modalities(
            target_metrics=target_metrics,
            evidence_requests=evidence_requests,
            reasoning_mode=reasoning_mode,
        )
        return {
            "complexity": complexity,
            "reasoning_mode": reasoning_mode,
            "required_sections": sections,
            "max_sections": max_sections,
            "answer_focus": answer_focus,
            "expected_evidence_modalities": expected_modalities,
            "token_budget_hint": token_budget_hint,
            "claim_contract": [
                "conclusion",
                "metric_value",
                "data_source",
                "decision_basis",
                "limitation",
                "recommended_action",
            ],
            "hypothesis_contract": (
                [
                    "hypothesis_label",
                    "supporting_evidence",
                    "contradicting_evidence",
                    "missing_critical_evidence",
                    "preference_reason",
                    "verification_action",
                ]
                if reasoning_mode == "integrative_reasoning"
                else []
            ),
        }

    @classmethod
    def _normalize_metrics(cls, value: Any, fallback: Any) -> list[str]:
        # A0-3: 允许集合从 ALLOWED_METRICS 扩为"特殊 token ∪ 完整注册表"，并对
        # LLM 输出做一次 canonical_id() 规范化（同义词/别名收敛到唯一 metric_id），
        # 这样已注册但未在 ALLOWED_METRICS 手写清单里的指标（如 silva_total_ratio_percent）
        # 才能真正被 planner 选中，而不是在这里被静默过滤掉。
        allowed = cls._registered_metric_choices()
        metrics: list[str] = []
        for item in (value if isinstance(value, list) else []):
            raw = str(item or "").strip().lower()
            metric = raw if raw in {"all", "overview"} else metric_schema_service.canonical_id(raw)
            if metric in allowed and metric not in metrics:
                metrics.append(metric)
        if metrics:
            return metrics[:8]
        return [str(item) for item in fallback if str(item).strip()][:8]

    @classmethod
    def _normalize_samples(cls, value: Any, fallback: Any) -> list[str]:
        samples = cls._clean_string_list(value)
        if samples:
            return samples[:10]
        return [str(item) for item in fallback if str(item).strip()][:10]

    @classmethod
    def _normalize_evidence_requests(
        cls,
        value: Any,
        *,
        fallback: Any,
        target_metrics: list[str],
    ) -> list[dict[str, str]]:
        requests: list[dict[str, str]] = []
        source = value if isinstance(value, list) else fallback
        for item in (source if isinstance(source, list) else []):
            if not isinstance(item, dict):
                continue
            request_type = str(item.get("type") or "").strip()
            if request_type not in cls.ALLOWED_REQUEST_TYPES:
                continue
            raw_metric = str(item.get("metric") or "").strip().lower()
            metric = raw_metric if raw_metric in {"all", "overview", ""} else metric_schema_service.canonical_id(raw_metric)
            if metric and metric not in cls._registered_metric_choices():
                metric = target_metrics[0] if target_metrics else "overview"
            module = re.sub(r"\s+", " ", str(item.get("module") or "")).strip()[:80]
            reason = re.sub(r"\s+", " ", str(item.get("reason") or "")).strip()[:160]
            requests.append(
                {
                    "type": request_type,
                    "module": module or request_type,
                    "metric": metric or "overview",
                    "reason": reason or "按用户问题补充证据",
                }
            )
        if requests:
            return requests[:12]
        return fallback if isinstance(fallback, list) else []

    @staticmethod
    def _clean_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    @classmethod
    def _reasoning_mode(
        cls,
        *,
        question: str,
        intent: str,
        target_metrics: list[str],
        evidence_requests: list[dict[str, str]],
        question_route: dict[str, Any],
    ) -> str:
        lowered = str(question or "").lower()
        route_name = str((question_route or {}).get("route") or "").strip().lower()
        request_count = len(evidence_requests)
        metric_count = len({cls._normalize_metric_alias(item) for item in target_metrics})
        numeric_groups = re.findall(r"\d+(?:\.\d+)?%?", lowered)
        has_matrix_terms = any(term in lowered for term in cls.MATRIX_REASONING_TERMS)
        has_integrative_terms = any(term in lowered for term in cls.INTEGRATIVE_REASONING_TERMS)
        has_fact_layer_terms = any(term in lowered for term in cls.FACT_LAYER_TERMS)
        if intent == "metric_explanation" and metric_count <= 1 and not has_integrative_terms:
            return "simple_metric"
        if route_name in {"project_compare", "chart", "ai_report_summary"}:
            return "integrative_reasoning"
        if (
            has_matrix_terms
            or (has_integrative_terms and metric_count >= 2)
            or has_fact_layer_terms
            or metric_count >= 3
            or (request_count >= 12 and metric_count >= 3)
            or len(numeric_groups) >= 6
            or len(question) > 140
        ):
            return "integrative_reasoning"
        if intent in {"anomaly_investigation", "project_overview", "project_comparison"}:
            return "focused_diagnostic"
        return "focused_diagnostic"

    @classmethod
    def _expected_evidence_modalities(
        cls,
        *,
        target_metrics: list[str],
        evidence_requests: list[dict[str, str]],
        reasoning_mode: str,
    ) -> list[str]:
        modalities: list[str] = []
        if any(cls._normalize_metric_alias(metric) == "frip_ratio" for metric in target_metrics):
            modalities.extend(["frip", "peak_count"])
        if any(cls._normalize_metric_alias(metric) == "correlation" for metric in target_metrics):
            modalities.append("correlation")
        if any(cls._normalize_metric_alias(metric) == "motif" for metric in target_metrics):
            modalities.append("motif")
        if any(cls._normalize_metric_alias(metric) == "spikein_scaling_factor" for metric in target_metrics):
            modalities.extend(["spikein", "alignment"])
        if any(
            cls._normalize_metric_alias(metric)
            in {"mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent", "mt_rate_percent"}
            for metric in target_metrics
        ):
            modalities.append("alignment")
        if any(request.get("type") == "script_or_rule_source" for request in evidence_requests):
            modalities.append("workflow_rule_source")
        if reasoning_mode == "integrative_reasoning":
            modalities.extend(["experiment_design", "competing_hypotheses"])
        return list(dict.fromkeys(modalities))

    @classmethod
    def _infer_intent(cls, lowered_question: str, route: dict[str, Any]) -> str:
        route_name = str(route.get("route") or "").strip()
        route_intent = str(route.get("intent") or "").strip()
        if route_name == "project_compare":
            return "project_comparison"
        if route_name == "chart":
            return "chart_request"
        if route_name == "ai_report_summary" or "总结" in lowered_question or "报告" in lowered_question:
            return "report_summary"
        if any(token in lowered_question for token in ("为什么", "原因", "排查", "异常", "偏高", "偏低", "不对", "问题")):
            return "anomaly_investigation"
        if any(token in lowered_question for token in ("是什么", "什么意思", "怎么计算", "如何计算", "公式")):
            return "metric_explanation"
        if route_intent:
            return route_intent
        return "project_overview"

    @classmethod
    def _infer_metrics(cls, lowered_question: str, route: dict[str, Any]) -> list[str]:
        metrics: list[str] = []
        for item in route.get("target_metrics", []) or []:
            metric = str(item).strip()
            if metric and metric not in metrics:
                metrics.append(metric)
        for metric, tokens, _ in cls.METRIC_RULES:
            if any(token.lower() in lowered_question for token in tokens) and metric not in metrics:
                metrics.append(metric)
        # A0-2（project_analysis_exploration_and_evolution_plan.md Stage A0）：
        # 原来"问题里字面提及已注册指标"的检测（detect_metrics_in_text）是
        # project_analysis_service._select_evidence_files / analyze() 里两处独立的
        # 下游补丁，和这里的规则推断并行、互不知情，形成第 3/4 层之外的又一套指标
        # 来源。收敛成规则 planner（这里）自己的一部分：任何时候规则 fallback 生效
        # （包括 LLM planner 抛异常时的兜底），都应该已经把问题里字面点名的已注册
        # 指标（含别名/detection_signature 同义词）纳入 target_metrics，不需要再靠
        # project_analysis_service 里的独立补丁去补救。
        try:
            for metric_id in metric_schema_service.detect_metrics_in_text(lowered_question):
                canonical = metric_schema_service.canonical_id(metric_id)
                if canonical and canonical not in metrics:
                    metrics.append(canonical)
        except Exception:
            pass
        if not metrics and any(token in lowered_question for token in ("质控", "qc", "质量")):
            metrics.extend(["adapter_percent", "mapping_rate_percent", "unique_mapping_rate_percent"])
        if not metrics and any(token in lowered_question for token in ("项目", "整体", "分析")):
            metrics.extend(["adapter_percent", "mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent", "chrmt_pt_rate_percent", "frip"])
        return metrics[:8]

    @classmethod
    def _infer_samples(cls, question: str) -> list[str]:
        samples: list[str] = []
        for match in cls.SAMPLE_PATTERN.findall(question or ""):
            sample = match.strip()
            if sample and sample not in samples:
                samples.append(sample)
        return samples[:10]

    @classmethod
    def _build_evidence_requests(cls, intent: str, target_metrics: list[str], lowered_question: str) -> list[dict[str, str]]:
        requests: list[dict[str, str]] = []
        for metric in target_metrics:
            hints = cls._metric_hints(metric)
            requests.append(
                {
                    "type": "metric_table",
                    "module": hints[0] if hints else metric,
                    "metric": metric,
                    "reason": f"核对 {metric} 的观测值、样本差异和来源字段",
                }
            )
            if intent in {"metric_explanation", "anomaly_investigation"} or any(token in lowered_question for token in ("公式", "怎么计算", "如何计算", "阈值", "标准")):
                requests.append(
                    {
                        "type": "script_or_rule_source",
                        "module": ", ".join(hints) if hints else metric,
                        "metric": metric,
                        "reason": f"确认 {metric} 的计算口径、脚本来源和是否存在项目专属阈值",
                    }
                )
        if intent == "project_comparison":
            requests.append({"type": "historical_project", "module": "project_memory", "metric": "all", "reason": "读取历史项目用于横向比较"})
        if intent == "chart_request":
            requests.append({"type": "chart_data", "module": "project_chart", "metric": ",".join(target_metrics), "reason": "准备绘图所需的样本与指标数据"})
        if not requests:
            requests.append({"type": "project_summary", "module": "report", "metric": "overview", "reason": "先读取项目摘要和核心 QC 指标"})
        return requests[:12]

    @classmethod
    def _build_evidence_requests(
        cls,
        intent: str,
        target_metrics: list[str],
        lowered_question: str,
        metric_evidence_plan: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        requests: list[dict[str, str]] = []
        metric_evidence_plan = metric_evidence_plan or {}
        needs_script_context = intent in {"metric_explanation", "anomaly_investigation"} or any(
            token in lowered_question
            for token in ("formula", "threshold", "script", "code", "source")
        )
        for metric in target_metrics:
            normalized_metric = cls._normalize_metric_alias(metric)
            hints = cls._metric_hints(normalized_metric)
            requests.append(
                {
                    "type": "metric_table",
                    "module": hints[0] if hints else normalized_metric,
                    "metric": normalized_metric,
                    "reason": f"Read primary observed value and source field for {normalized_metric}",
                }
            )
            if needs_script_context:
                requests.append(
                    {
                        "type": "script_or_rule_source",
                        "module": ", ".join(hints) if hints else normalized_metric,
                        "metric": normalized_metric,
                        "reason": f"Verify calculation, source script, parameters and project-specific rules for {normalized_metric}",
                    }
                )

            graph = metric_evidence_plan.get(normalized_metric)
            if isinstance(graph, dict) and intent in {"anomaly_investigation", "project_overview"}:
                for relation in ("upstream", "parallel", "downstream"):
                    for related_metric in graph.get(relation, []) or []:
                        related = cls._normalize_metric_alias(str(related_metric))
                        if related not in cls.ALLOWED_METRICS:
                            continue
                        related_hints = cls._metric_hints(related)
                        requests.append(
                            {
                                "type": "metric_table",
                                "module": related_hints[0] if related_hints else related,
                                "metric": related,
                                "reason": f"Use {relation} evidence for {normalized_metric}; avoid isolated single-metric diagnosis",
                            }
                        )
                for module in graph.get("modules", []) or []:
                    module_text = str(module or "").strip()
                    if not module_text:
                        continue
                    if module_text.lower() in {"readsqc", "alignmentqc", "frip", "correlation"}:
                        continue
                    requests.append(
                        {
                            "type": "script_or_rule_source",
                            "module": module_text,
                            "metric": normalized_metric,
                            "reason": f"Check workflow/script context related to {normalized_metric}",
                        }
                    )

        if intent == "project_comparison":
            requests.append(
                {
                    "type": "historical_project",
                    "module": "project_memory",
                    "metric": "all",
                    "reason": "Read historical projects for cross-project comparison",
                }
            )
        if intent == "chart_request":
            requests.append(
                {
                    "type": "chart_data",
                    "module": "project_chart",
                    "metric": ",".join(target_metrics),
                    "reason": "Prepare chart-ready sample and metric data",
                }
            )
        if not requests:
            requests.append(
                {
                    "type": "project_summary",
                    "module": "report",
                    "metric": "overview",
                    "reason": "Read project overview and core QC metrics",
                }
            )

        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in requests:
            key = (item.get("type", ""), item.get("module", ""), item.get("metric", ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:18]

    @classmethod
    def _build_metric_evidence_plan(cls, target_metrics: list[str]) -> dict[str, Any]:
        plan: dict[str, Any] = {}
        for metric in target_metrics:
            normalized = cls._normalize_metric_alias(metric)
            graph = cls.METRIC_EVIDENCE_GRAPH.get(normalized)
            if not graph:
                continue
            plan[normalized] = {
                "primary": list(graph.get("primary", []) or []),
                "upstream": list(graph.get("upstream", []) or []),
                "parallel": list(graph.get("parallel", []) or []),
                "downstream": list(graph.get("downstream", []) or []),
                "modules": list(graph.get("modules", []) or []),
                "candidate_causes": list(graph.get("candidate_causes", []) or []),
            }
        return plan

    @classmethod
    def _related_metrics(cls, metric_evidence_plan: dict[str, Any]) -> list[str]:
        metrics: list[str] = []
        for graph in metric_evidence_plan.values():
            if not isinstance(graph, dict):
                continue
            for relation in ("primary", "upstream", "parallel", "downstream"):
                for item in graph.get(relation, []) or []:
                    metric = cls._normalize_metric_alias(str(item))
                    if metric in cls.ALLOWED_METRICS and metric not in metrics:
                        metrics.append(metric)
        return metrics[:12]

    @staticmethod
    def _merge_metrics(primary: list[str], related: list[str]) -> list[str]:
        merged: list[str] = []
        for metric in list(primary or []) + list(related or []):
            if metric and metric not in merged:
                merged.append(metric)
        return merged[:16]

    @staticmethod
    def _normalize_metric_alias(metric: str) -> str:
        normalized = str(metric or "").strip().lower()
        aliases = {
            "chrmt_pt_rate_percent": "mt_rate_percent",
            "chrmt/pt": "mt_rate_percent",
            "mt": "mt_rate_percent",
            "frip": "frip_ratio",
        }
        return aliases.get(normalized, normalized)

    @classmethod
    def _metric_hints(cls, metric: str) -> tuple[str, ...]:
        for name, _, hints in cls.METRIC_RULES:
            if name == metric:
                return hints
        return (metric,)

    @classmethod
    def _build_evidence_hints(cls, evidence_requests: list[dict[str, str]]) -> list[str]:
        hints: list[str] = []
        for request in evidence_requests:
            for part in re.split(r"[,，]\s*", request.get("module", "")):
                part = part.strip()
                if part and part not in hints:
                    hints.append(part)
        return hints[:20]

    @staticmethod
    def _requires_script_review(lowered_question: str, intent: str) -> bool:
        return intent in {"metric_explanation", "anomaly_investigation"} or any(
            token in lowered_question for token in ("公式", "怎么计算", "如何计算", "阈值", "标准", "脚本", "代码", "来源")
        )

    @staticmethod
    def _build_notes(
        intent: str,
        target_metrics: list[str],
        target_samples: list[str],
        experience_summary: dict[str, Any],
    ) -> list[str]:
        notes = [f"本轮按 {intent} 规划分析路线"]
        if target_metrics:
            notes.append("优先指标: " + ", ".join(target_metrics))
        if target_samples:
            notes.append("优先样本: " + ", ".join(target_samples))
        if experience_summary.get("has_experience"):
            notes.append("结合项目历史记忆和全局经验提示，但不把历史经验当项目专属标准")
        return notes


analysis_planner_service = AnalysisPlannerService()
