from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any, AsyncIterator

from multi_agent.backed.app.infrastructure.ai.openai_client import (
    REPORT_SUMMARY_CLIENT_CONFIGURED,
    REPORT_SUMMARY_MODEL_NAME,
    SUB_MODEL_NAME,
    report_summary_model_client,
    sub_model_client,
)
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.multi_agent.project_progress import publish_project_answer_delta
from multi_agent.backed.app.services.business_agent.claim_service import claim_service


class BusinessResponseService:
    STANDARD_ANSWER_MAX_TOKENS = 2200
    REPORT_ANSWER_MAX_TOKENS = 3600
    METRIC_FORMULA_TARGETS = {
        "adapter_percent": {
            "display": "Adapter（原始 reads 接头检出率）",
            "definition": "表示原始 reads 中被识别为接头相关 reads 的比例；它不等于 clean reads 中的接头残留率。",
            "tokens": ("adapter", "接头"),
            "section": "qc",
            "value_key": "adapter_percent",
            "raw_key": "adapter",
            "source_field": "Adapter",
            "unit": "%",
        },
        "mapping_rate_percent": {
            "display": "Mapping（比对率）",
            "definition": "表示 reads 成功比对到参考基因组的比例。",
            "tokens": ("mapping", "比对率"),
            "section": "alignment",
            "value_key": "mapping_rate_percent",
            "source_field": "Mapping(%)",
            "unit": "%",
        },
        "unique_mapping_rate_percent": {
            "display": "Unique（唯一比对率）",
            "definition": "表示唯一比对到参考基因组位置的 reads 比例。",
            "tokens": ("unique", "唯一比对"),
            "section": "alignment",
            "value_key": "unique_mapping_rate_percent",
            "source_field": "Unique(%)",
            "unit": "%",
        },
        "duplicate_rate_percent": {
            "display": "Duplicate（重复率）",
            "definition": "表示被判定为重复 reads 的比例。",
            "tokens": ("duplicate", "重复率"),
            "section": "alignment",
            "value_key": "duplicate_rate_percent",
            "source_field": "Duplicate(%)",
            "unit": "%",
        },
        "frip_ratio": {
            "display": "FRiP（reads in peaks 比例）",
            "definition": "表示落入 peak 区域的比对 reads 占比。",
            "tokens": ("frip", "reads in peaks"),
            "section": "frip",
            "value_key": "frip_ratio",
            "source_field": "FRiP",
            "unit": "",
        },
    }

    REPORT_LINE_SKIP_MARKERS = (
        "Code",
        "Show All Code",
        "Hide All Code",
        "点击展开",
        "数据说明",
        "Produced by",
        "FastQC",
    )

    NEXT_ACTION_HEADING = "下一步建议"
    RELATED_METRIC_SCOPE = {
        "adapter_percent": {"adapter_percent", "q30_ratio"},
        "q30_ratio": {"q30_ratio", "adapter_percent"},
        "mapping_rate_percent": {"mapping_rate_percent", "unique_mapping_rate_percent", "mt_rate_percent"},
        "unique_mapping_rate_percent": {"unique_mapping_rate_percent", "mapping_rate_percent", "mt_rate_percent"},
        "duplicate_rate_percent": {"duplicate_rate_percent", "frip_ratio"},
        "mt_rate_percent": {"mt_rate_percent", "mapping_rate_percent", "unique_mapping_rate_percent"},
        "frip_ratio": {"frip_ratio", "mapping_rate_percent", "unique_mapping_rate_percent"},
        "correlation": {"correlation", "frip_ratio"},
    }
    SCIENTIFIC_REPAIR_LABELS = {
        "adapter_percent": "原始 reads 接头检出率",
        "q30_ratio": "Q30 比例",
        "mapping_rate_percent": "比对率",
        "unique_mapping_rate_percent": "唯一比对率",
        "duplicate_rate_percent": "重复率",
        "mt_rate_percent": "线粒体 reads 比例",
        "frip_ratio": "FRiP",
        "correlation": "样本相关性",
    }
    SCIENTIFIC_REPAIR_ALIASES = {
        "adapter_percent": ("adapter", "接头"),
        "q30_ratio": ("q30",),
        "mapping_rate_percent": ("mapping", "总比对率"),
        "unique_mapping_rate_percent": ("unique", "唯一比对率"),
        "duplicate_rate_percent": ("duplicate", "duplication", "重复率"),
        "mt_rate_percent": ("chrmt", "mt_ratio", "线粒体 reads", "线粒体比例", "细胞器比例"),
        "frip_ratio": ("frip",),
        "correlation": ("correlation", "spearman", "相关性"),
    }

    @staticmethod
    def _markdown_table(headers: list[str], rows: list[list[Any]], limit: int = 200) -> str:
        if not rows:
            return ""
        safe_headers = [str(item) for item in headers]
        safe_rows = [
            [str(value if value is not None and value != "" else "-") for value in row]
            for row in rows[:limit]
        ]
        lines = [
            "| " + " | ".join(safe_headers) + " |",
            "| " + " | ".join("---" for _ in safe_headers) + " |",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in safe_rows)
        return "\n".join(lines)

    @staticmethod
    def _format_rule_reference(experience_summary: dict[str, Any], limit: int = 5) -> list[str]:
        formatted = []
        for item in (experience_summary.get("structured_experience_rules", []) or [])[:limit]:
            rule_key = item.get("rule_key", "")
            source = item.get("source", "")
            frequency = item.get("frequency", 0)
            if rule_key:
                formatted.append(f"{rule_key} (source={source}, frequency={frequency})")
        return formatted

    @staticmethod
    def _format_metric_cell(value: Any, unit: str = "") -> str:
        if value in (None, ""):
            return "-"
        if isinstance(value, float):
            text = f"{value:.4f}".rstrip("0").rstrip(".")
        else:
            text = str(value)
        if unit == "%" and text != "-" and not text.endswith("%"):
            return f"{text}%"
        return text

    @classmethod
    def _humanize_project_formula(cls, metric_key: str, formula: str) -> str:
        formula_lower = (formula or "").lower()
        if metric_key == "adapter_percent" and "adapter_count" in formula_lower and "total_reads_raw" in formula_lower:
            return "Adapter(%) = adapter_count / total_reads_raw × 100，其中 adapter_count 是接头相关 reads 数，total_reads_raw 是原始 reads 总数。"
        if metric_key == "mapping_rate_percent":
            return "Mapping(%) 取自比对日志中的 overall alignment rate。"
        if metric_key == "unique_mapping_rate_percent":
            return "Unique(%) 取自比对日志中唯一比对一次的 reads 比例。"
        if metric_key == "duplicate_rate_percent":
            return "Duplicate(%) = PERCENT_DUPLICATION × 100。"
        if metric_key == "frip_ratio":
            return "FRiP 取自 peak 区域富集统计中的 Reads_in_Peaks / Mapped_Reads。"
        return "项目脚本中已定位计算定义，但当前未提供可安全展示的标准化文字口径。"

    @classmethod
    def _build_metric_formula_answer(cls, question: str, analysis_result: dict[str, Any]) -> str:
        normalized = (question or "").lower()
        asks_formula = any(
            token in normalized
            for token in ("怎么计算", "如何计算", "计算方式", "公式", "怎么算", "是什么")
        )
        if not asks_formula:
            return ""

        metric_key = ""
        target: dict[str, Any] = {}
        for candidate_key, candidate in cls.METRIC_FORMULA_TARGETS.items():
            if any(token.lower() in normalized for token in candidate["tokens"]):
                metric_key = candidate_key
                target = candidate
                break
        if not metric_key:
            return ""

        metric_sources = (analysis_result.get("project_context") or {}).get("metric_rule_sources") or {}
        metric_source = metric_sources.get(metric_key) or {}
        formula = ""
        formula_status = "项目脚本中未确认计算公式"
        if metric_source.get("formula_source") == "project_code" and metric_source.get("formula"):
            formula = cls._humanize_project_formula(metric_key, str(metric_source.get("formula") or ""))
            formula_status = "已从项目脚本验证计算口径"

        parsed_metrics = analysis_result.get("parsed_metrics") or {}
        metric_rows = parsed_metrics.get(str(target.get("section"))) or []
        table_rows: list[list[Any]] = []
        for item in metric_rows[:10]:
            if not isinstance(item, dict):
                continue
            row = [
                item.get("sample", "-"),
                cls._format_metric_cell(item.get(str(target.get("value_key"))), str(target.get("unit", ""))),
            ]
            raw_key = target.get("raw_key")
            if raw_key:
                row.append(item.get(str(raw_key), "-"))
            table_rows.append(row)

        headers = ["样本", str(target["display"])]
        if target.get("raw_key"):
            headers.append("原始字段值")
        table = cls._markdown_table(headers, table_rows, limit=10) if table_rows else "当前证据表中未解析到该指标的样本值。"

        lines = [
            "## 指标口径",
            f"{target['display']}是当前项目中的质控指标，{target['definition']}",
            "",
            "## 关键数据",
            table,
            "",
            "## 依据",
            f"- 来源字段：{target.get('source_field', '-')}",
            f"- 计算口径：{formula if formula else formula_status}",
            f"- 口径状态：{formula_status}",
        ]
        return "\n".join(lines).strip()

    @staticmethod
    def _metric_scope(analysis_result: dict[str, Any]) -> set[str]:
        tags = set(str(item) for item in (analysis_result.get("question_tags") or []) if item)
        question_type = analysis_result.get("question_type")
        if question_type:
            tags.add(str(question_type))
        if not tags or "overview" in tags:
            return {"qc", "alignment", "spikein", "frip", "peak", "correlation"}
        scope_map = {
            "qc": {"qc"},
            "alignment": {"alignment"},
            "spikein": {"spikein"},
            "diagnostic": {"qc", "alignment", "spikein", "frip", "peak", "correlation"},
            "frip": {"frip", "peak"},
            "peak": {"peak", "frip"},
            "correlation": {"correlation"},
            "diff": {"diff"},
            "motif": {"motif"},
            "igv": {"igv"},
        }
        scope: set[str] = set()
        for tag in tags:
            scope.update(scope_map.get(tag, set()))
        return scope or {"qc", "alignment", "spikein", "frip", "peak", "correlation"}

    def build_metric_tables(self, analysis_result: dict[str, Any]) -> str:
        parsed_metrics = analysis_result.get("parsed_metrics", {}) or {}
        scope = self._metric_scope(analysis_result)
        blocks: list[str] = []

        if "qc" in scope:
            rows = [
                [
                    item.get("sample", "-"),
                    item.get("clean_reads", "-"),
                    item.get("clean_read_retention_percent", "-"),
                    item.get("adapter", "-"),
                    item.get("adapter_percent", "-"),
                    item.get("q20_ratio", "-"),
                    item.get("q30_ratio", "-"),
                ]
                for item in (parsed_metrics.get("qc") or [])
            ]
            if rows:
                blocks.append(
                    "ReadsQC 指标:\n"
                    + self._markdown_table(
                        ["样本", "Clean Reads(raw)", "Clean(%)", "Adapter reads(raw)", "Adapter/raw reads(%)", "Q20", "Q30"],
                        rows,
                    )
                )

        if "alignment" in scope:
            alignment_items = parsed_metrics.get("alignment") or []
            rows = [
                [
                    item.get("sample", "-"),
                    item.get("mapping_rate_percent", "-"),
                    item.get("unique_mapping_rate_percent", "-"),
                    item.get("duplicate_rate_percent", "-"),
                    item.get("mt_rate_percent", "-"),
                    item.get("complexity", "-"),
                ]
                for item in alignment_items
            ]
            if rows:
                organelle_header = next(
                    (
                        str(item.get("organelle_metric_label"))
                        for item in alignment_items
                        if item.get("organelle_metric_label")
                    ),
                    "Organelle(%)",
                )
                blocks.append(
                    "AlignmentQC 指标:\n"
                    + self._markdown_table(
                        ["样本", "Mapping(%)", "Unique(%)", "Duplicate(%)", organelle_header, "Complexity"],
                        rows,
                    )
                )

        if "spikein" in scope:
            rows = [
                [item.get("sample", "-"), item.get("unique_mapping_rate_percent", "-")]
                for item in (parsed_metrics.get("spikein") or [])
            ]
            if rows:
                blocks.append("Spike-in 指标:\n" + self._markdown_table(["样本", "Unique Mapping(%)"], rows))

        if "frip" in scope:
            rows = [
                [item.get("sample", "-"), item.get("frip_ratio", "-")]
                for item in (parsed_metrics.get("frip") or [])
            ]
            if rows:
                blocks.append("FRiP 指标:\n" + self._markdown_table(["样本", "FRiP"], rows))

        if "peak" in scope:
            peak_metrics = parsed_metrics.get("peak") or {}
            rows = [[sample, count] for sample, count in (peak_metrics.get("ranked") or [])]
            if rows:
                blocks.append("Peak 数量:\n" + self._markdown_table(["样本", "Peak 数"], rows))

        if "correlation" in scope:
            corr_metrics = parsed_metrics.get("correlation") or {}
            rows = []
            if corr_metrics.get("max_pair"):
                pair = corr_metrics["max_pair"]
                rows.append(["最高相关", pair[0], pair[1], f"{pair[2]:.4f}"])
            if corr_metrics.get("min_pair"):
                pair = corr_metrics["min_pair"]
                rows.append(["最低相关", pair[0], pair[1], f"{pair[2]:.4f}"])
            if rows:
                blocks.append("样本相关性:\n" + self._markdown_table(["类型", "样本1", "样本2", "相关系数"], rows))

        return "\n\n".join(blocks)

    def build_project_context_block(self, analysis_result: dict[str, Any]) -> str:
        context = analysis_result.get("project_context", {}) or {}
        if not context:
            return ""

        lines: list[str] = []
        config = context.get("config", {}) or {}
        samples = context.get("samples", []) or []
        experiment_design = context.get("experiment_design") or {}
        guides = context.get("metric_guides", []) or []
        glossary = context.get("metric_glossary", {}) or {}
        html_report = context.get("html_report", {}) or {}
        workflow_summary = context.get("workflow_summary", {}) or {}
        workflow_rule_sources = context.get("workflow_rule_sources", {}) or {}

        if context.get("samplelist_file"):
            lines.append(f"samplelist: {context.get('samplelist_file')}")
        if samples:
            sample_names = [str(item.get("sample", "")).strip() for item in samples if item.get("sample")]
            lines.append(f"samples({len(samples)}): {', '.join(sample_names[:30])}")
        if experiment_design.get("samples"):
            role_lines = [
                (
                    f"- {item.get('sample', '')}: role={item.get('role', '')}; "
                    f"condition={item.get('condition', '')}; target={item.get('target', '')}; "
                    f"replicate={item.get('replicate', '')}; "
                    f"control_for={item.get('control_for', [])}; batch={item.get('batch', '')}"
                )
                for item in (experiment_design.get("samples") or [])[:30]
            ]
            lines.append("experiment_design:\n" + "\n".join(role_lines))
        if context.get("config_file"):
            lines.append(f"config: {context.get('config_file')}")
        if config:
            # Put QC-critical params first, noisy thread/path entries last.
            _LOW_PRIORITY_PREFIXES = ("threads.", "deeptools_params.", "adapter_sets.", "scripts", "output", "samplist", "samplelist", "db_root")
            qc_items = [
                f"{key}={value}"
                for key, value in config.items()
                if value not in (None, "") and not any(key.startswith(p) for p in _LOW_PRIORITY_PREFIXES)
            ]
            low_items = [
                f"{key}={value}"
                for key, value in config.items()
                if value not in (None, "") and any(key.startswith(p) for p in _LOW_PRIORITY_PREFIXES)
            ]
            config_items = qc_items + low_items
            lines.append("pipeline_config: " + "; ".join(config_items))
        if workflow_summary:
            workflow_files = [
                f"{item.get('file', '')}({item.get('matched_topics', '')})"
                for item in workflow_summary.get("files", [])[:10]
            ]
            if workflow_files:
                lines.append("workflow_files: " + "; ".join(workflow_files))
            detected_parameters = workflow_summary.get("detected_parameters", {}) or {}
            if detected_parameters:
                lines.append(
                    "workflow_detected_parameters:\n"
                    + "\n".join(f"- {key}: {value}" for key, value in list(detected_parameters.items())[:10])
                )
        if workflow_rule_sources:
            rule_lines = []
            for key, source in list(workflow_rule_sources.items())[:12]:
                rule_lines.append(
                    "- "
                    + f"{key}: {source.get('value', '')} "
                    + f"[{source.get('source_type', '')}; {source.get('source_file', '')}]"
                )
            lines.append("workflow_rule_sources:\n" + "\n".join(rule_lines))
        if context.get("report_roots"):
            lines.append("report_roots: " + ", ".join(str(item) for item in context.get("report_roots", [])[:5]))
        if html_report:
            lines.append(f"existing_html_report: {html_report.get('file', '')}")
            if html_report.get("sections"):
                section_titles = [str(item.get("title", "")) for item in html_report.get("sections", []) if item.get("title")]
                lines.append("existing_html_report_sections: " + " -> ".join(section_titles[:24]))
            report_text = html_report.get("section_text") or html_report.get("text_excerpt")
            if report_text and analysis_result.get("report_mode") == "existing_html_report_summary":
                lines.append("existing_html_report_section_text:\n" + str(report_text)[:50000])
        if guides:
            guide_files = [str(item.get("file", "")) for item in guides if item.get("file")]
            lines.append("metric_guide_files: " + ", ".join(guide_files[:12]))
        if glossary:
            glossary_lines = [f"- {key}: {value}" for key, value in list(glossary.items())[:16]]
            lines.append("metric_glossary:\n" + "\n".join(glossary_lines))

        return "\n".join(lines)

    def build_narrow_project_context_block(
        self,
        analysis_result: dict[str, Any],
        target_metrics: set[str],
    ) -> str:
        context = analysis_result.get("project_context", {}) or {}
        config = context.get("config", {}) or {}
        experiment_design = context.get("experiment_design") or {}
        glossary = context.get("metric_glossary", {}) or {}
        lines: list[str] = []
        if config:
            preferred_keys = (
                "species",
                "genome",
                "reference",
                "assay",
                "project_type",
                "Sequencing",
                "sequencing_mode",
                "organelle_chroms",
                "adapter_type",
                "trimming_tool",
                "remove_duplicates",
            )
            config_items = [
                f"{key}={config[key]}"
                for key in preferred_keys
                if config.get(key) not in (None, "")
            ]
            if config_items:
                lines.append("pipeline_config: " + "; ".join(config_items))
        if experiment_design.get("samples"):
            lines.append(
                "experiment_design:\n"
                + "\n".join(
                    (
                        f"- {item.get('sample', '')}: role={item.get('role', '')}; "
                        f"condition={item.get('condition', '')}; target={item.get('target', '')}; "
                        f"replicate={item.get('replicate', '')}; "
                        f"control_for={item.get('control_for', [])}; batch={item.get('batch', '')}"
                    )
                    for item in (experiment_design.get("samples") or [])[:20]
                )
            )
        glossary_tokens = {
            "adapter_percent": ("ReadsQC.Adapter",),
            "q30_ratio": ("ReadsQC.Q20/Q30",),
            "mapping_rate_percent": ("AlignmentQC.Mapping_Rate",),
            "unique_mapping_rate_percent": ("AlignmentQC.Unique_Mapped_Rate",),
            "duplicate_rate_percent": ("AlignmentQC.Picard_Duplication_Rate",),
            "mt_rate_percent": ("AlignmentQC.MT_Ratio",),
            "frip_ratio": ("Peak.FRIP",),
            "correlation": ("Correlation.Spearman",),
        }
        glossary_keys = {
            key
            for metric in target_metrics
            for key in glossary_tokens.get(metric, ())
        }
        glossary_lines = [
            f"- {key}: {value}"
            for key, value in glossary.items()
            if key in glossary_keys
        ]
        if glossary_lines:
            lines.append("metric_glossary:\n" + "\n".join(glossary_lines))
        return "\n".join(lines)

    @staticmethod
    def _normalized_target_metrics(analysis_plan: dict[str, Any]) -> set[str]:
        aliases = {
            "chrmt_pt_rate_percent": "mt_rate_percent",
            "chrmt_rate_percent": "mt_rate_percent",
            "mt_ratio": "mt_rate_percent",
            "chrmt/pt": "mt_rate_percent",
            "frip": "frip_ratio",
        }
        return {
            aliases.get(str(item or "").strip().lower(), str(item or "").strip().lower())
            for item in (analysis_plan.get("target_metrics") or [])
            if str(item or "").strip()
        }

    @classmethod
    def _answer_target_metrics(cls, analysis_result: dict[str, Any]) -> set[str]:
        analysis_plan = analysis_result.get("analysis_plan") or {}
        target_metrics = cls._normalized_target_metrics(analysis_plan)
        question = str(analysis_result.get("question") or "").lower()
        question_target_metrics = {
            metric_key
            for metric_key, aliases in cls.SCIENTIFIC_REPAIR_ALIASES.items()
            if any(alias in question for alias in aliases)
        }
        return question_target_metrics if len(question_target_metrics) == 1 else target_metrics

    @classmethod
    def build_scientific_repair_answer(
        cls,
        *,
        analysis_result: dict[str, Any],
        violations: list[dict[str, Any]],
    ) -> str:
        repair_rules = {
            str(item.get("rule") or "")
            for item in violations
            if isinstance(item, dict)
        }
        supported_rules = {
            "adapter_processing_stage_mismatch",
            "species_organelle_mismatch",
            "no_unverified_threshold_judgement",
            "evidence_presence_mismatch",
            "target_metric_value_omission",
        }
        if not repair_rules.intersection(supported_rules):
            return ""

        target_metrics = cls._answer_target_metrics(analysis_result)
        evidence_chain = [
            item
            for item in (analysis_result.get("evidence_chain") or [])
            if isinstance(item, dict)
            and (item.get("value") is not None or item.get("display_value"))
        ]
        if not evidence_chain:
            return ""

        if target_metrics:
            target_evidence = [
                item
                for item in evidence_chain
                if str(item.get("metric_key") or "") in target_metrics
            ]
        else:
            target_evidence = []
        if not target_evidence:
            violation_text = " ".join(
                f"{item.get('message', '')} {item.get('matched', '')}"
                for item in violations
                if isinstance(item, dict)
            ).lower()
            matched_metrics = {
                metric_key
                for metric_key, label in cls.SCIENTIFIC_REPAIR_LABELS.items()
                if metric_key.lower() in violation_text or label.lower() in violation_text
            }
            target_evidence = [
                item
                for item in evidence_chain
                if str(item.get("metric_key") or "") in matched_metrics
            ]
        if not target_evidence:
            return ""

        target_metric = str(target_evidence[0].get("metric_key") or "")
        target_evidence = [
            item
            for item in target_evidence
            if str(item.get("metric_key") or "") == target_metric
        ][:12]
        label = cls.SCIENTIFIC_REPAIR_LABELS.get(
            target_metric,
            str(target_evidence[0].get("metric") or target_metric),
        )
        observed = "、".join(
            f"{item.get('sample', '-')}={item.get('display_value') or item.get('value')}"
            for item in target_evidence
        )
        lines = [
            f"当前项目已经读取到{label}的结构化结果：{observed}。",
            "项目文件中未确认该指标的专属判断阈值，因此这些数值只能作为观测结果，不能单独判定偏高、偏低或异常。",
            "",
            "## 关键证据",
        ]
        for item in target_evidence:
            detail = [
                f"{item.get('sample', '-')}={item.get('display_value') or item.get('value')}",
            ]
            denominator = str(item.get("denominator") or "").strip()
            if denominator:
                detail.append(f"分母口径为 {denominator}")
            source_file = str(item.get("source_file") or "-")
            source_field = str(item.get("source_field") or "-")
            detail.append(f"来源 {source_file}::{source_field}")
            lines.append("- " + "；".join(detail) + "。")

        lines.extend(["", "## 解释边界"])
        project_context = analysis_result.get("project_context") or {}
        config = project_context.get("config") if isinstance(project_context, dict) else {}
        species = ""
        if isinstance(config, dict):
            species = str(
                config.get("species")
                or config.get("genome")
                or config.get("reference")
                or ""
            ).strip()
        if target_metric == "adapter_percent":
            lines.append("- 该指标来自 raw reads，只表示原始 reads 中检测到接头相关序列，不能替代 trimming 后 clean FASTQ 的接头评估。")
        elif target_metric == "mt_rate_percent":
            species_text = f"在 species/reference={species} 的项目中，" if species else ""
            lines.append(f"- {species_text}该指标按线粒体 reads 解释；需结合 mapping、unique mapping 和细胞器染色体过滤口径共同判断。")
        else:
            definition = str(target_evidence[0].get("definition") or "").strip()
            if definition:
                lines.append(f"- {definition}")

        related_metrics = cls.RELATED_METRIC_SCOPE.get(target_metric, set()) - {target_metric}
        related_evidence = [
            item
            for item in evidence_chain
            if str(item.get("metric_key") or "") in related_metrics
        ]
        if related_evidence:
            related_text = "、".join(
                f"{cls.SCIENTIFIC_REPAIR_LABELS.get(str(item.get('metric_key') or ''), str(item.get('metric') or '指标'))}"
                f" {item.get('sample', '-')}={item.get('display_value') or item.get('value')}"
                for item in related_evidence[:6]
            )
            lines.append(f"- 可联动核对：{related_text}。")

        next_checks: list[str] = []
        for diagnostic in analysis_result.get("tool_diagnostics") or []:
            if not isinstance(diagnostic, dict):
                continue
            tool_name = str(diagnostic.get("tool") or "").lower()
            relevant = (
                target_metric == "adapter_percent" and "adapter" in tool_name
            ) or (
                target_metric in {"mapping_rate_percent", "unique_mapping_rate_percent", "mt_rate_percent"}
                and "alignment" in tool_name
            ) or (
                target_metric == "duplicate_rate_percent" and "duplicate" in tool_name
            ) or (
                target_metric == "frip_ratio" and "frip" in tool_name
            ) or (
                target_metric == "correlation" and "correlation" in tool_name
            )
            if relevant:
                next_checks.extend(
                    str(item).strip()
                    for item in (diagnostic.get("next_checks") or [])
                    if str(item).strip()
                )
        if next_checks:
            lines.extend(["", f"## {cls.NEXT_ACTION_HEADING}"])
            lines.extend(f"- {item}" for item in list(dict.fromkeys(next_checks))[:3])

        return "\n".join(lines).strip()

    @classmethod
    def _repair_omitted_target_values(
        cls,
        answer: str,
        analysis_result: dict[str, Any],
    ) -> str:
        question = str(analysis_result.get("question") or "").lower()
        formula_or_definition_terms = (
            "是什么",
            "什么意思",
            "怎么计算",
            "如何计算",
            "计算公式",
            "公式",
        )
        if any(term in question for term in formula_or_definition_terms):
            return ""
        target_metrics = cls._answer_target_metrics(analysis_result)
        if len(target_metrics) != 1:
            return ""
        target_metric = next(iter(target_metrics))
        target_evidence = [
            item
            for item in (analysis_result.get("evidence_chain") or [])
            if isinstance(item, dict)
            and str(item.get("metric_key") or "") == target_metric
            and (item.get("value") is not None or item.get("display_value"))
        ][:6]
        if not target_evidence:
            return ""
        missing_values = [
            str(item.get("display_value") or item.get("value"))
            for item in target_evidence
            if str(item.get("display_value") or item.get("value")) not in answer
        ]
        if not missing_values:
            return ""
        return cls.build_scientific_repair_answer(
            analysis_result=analysis_result,
            violations=[
                {
                    "rule": "target_metric_value_omission",
                    "matched": ", ".join(missing_values),
                }
            ],
        )

    def build_existing_html_report_context(self, analysis_result: dict[str, Any]) -> str:
        context = analysis_result.get("project_context", {}) or {}
        html_report = (context.get("html_report", {}) or {}) if context else {}
        sections = html_report.get("sections", []) or []
        section_titles = [str(item.get("title", "")).strip() for item in sections if item.get("title")]
        config = context.get("config", {}) or {}
        samples = context.get("samples", []) or []
        diagnosis_summary = analysis_result.get("diagnosis_summary", {}) or {}
        anomaly_summary = analysis_result.get("anomaly_summary", {}) or {}
        evidence_chain = analysis_result.get("evidence_chain", []) or []
        evidence_cards = analysis_result.get("evidence_cards", []) or []
        validated_claims = analysis_result.get("validated_claims", []) or []
        claim_layers = analysis_result.get("claim_layers", {}) or {}
        claim_validation = analysis_result.get("claim_validation", {}) or {}
        agent_loop = analysis_result.get("agent_loop", {}) or {}
        analysis_limits = analysis_result.get("analysis_limits", []) or []

        blocks: list[str] = [
            "输出要求:",
            "- 只回答用户当前问题，不要扩展到无关模块、无关指标或通用背景。",
            "- 不判断项目合格/不合格、通过/失败、是否适合继续下游分析。",
            "- 不要固定使用五个一级标题；根据用户问题自然组织回答，避免机械套用模板。",
            "- 只列与当前问题相关、需要结合生物背景复核的指标；没有项目文件阈值时不要下异常结论。",
            "- 每条需复核指标尽量绑定样本、数值、项目文件阈值或阈值缺失说明、来源文件和来源字段。",
            "- 可选复核动作必须可执行，不要写泛泛的「进一步分析」。",
            "",
            f"项目ID: {analysis_result.get('project_id', '')}",
            f"用户问题: {analysis_result.get('question', '')}",
            f"报告文件: {analysis_result.get('report_source', '') or html_report.get('file', '')}",
        ]

        if samples:
            sample_names = [str(item.get("sample", "")).strip() for item in samples if item.get("sample")]
            blocks.append(f"样本: {', '.join(sample_names[:50])}")
        if config:
            config_items = [f"{key}={value}" for key, value in config.items() if value not in (None, "")]
            blocks.append("流程配置: " + "; ".join(config_items[:24]))

        if section_titles:
            blocks.append("existing_html_report_sections:\n" + "\n".join(f"{index}. {title}" for index, title in enumerate(section_titles, start=1)))

        conclusions = diagnosis_summary.get("conclusions", []) or []
        if conclusions:
            blocks.append("结构化需复核指标:\n- " + "\n- ".join(str(item) for item in conclusions[:6]))

        abnormal_items = list(anomaly_summary.get("critical", []) or []) + list(anomaly_summary.get("warning", []) or [])
        if abnormal_items:
            lines = []
            for item in abnormal_items[:16]:
                lines.append(
                    f"{item.get('severity', '')}: {item.get('sample', '-')} "
                    f"{item.get('metric', '')}={item.get('display_value', '-')}; "
                    f"rule={item.get('rule', '-')}; "
                    f"source={item.get('source_file', '-')}::{item.get('source_field', '-')}; "
                    f"interpretation={item.get('interpretation', '')}"
                )
            blocks.append("结构化需复核指标:\n- " + "\n- ".join(lines))

        if evidence_chain:
            lines = []
            for item in evidence_chain[:24]:
                lines.append(
                    f"{item.get('category', '')}/{item.get('metric', '')}: "
                    f"sample={item.get('sample', '-')}, value={item.get('display_value', '-')}, "
                    f"severity={item.get('severity', '-')}, "
                    f"source={item.get('source_file', '-')}::{item.get('source_field', '-')}, "
                    f"formula={item.get('formula', '-')}, "
                    f"formula_source={item.get('formula_source', '-')}, "
                    f"threshold_source={item.get('threshold_source', '-')}, "
                    f"evidence_grade={item.get('evidence_grade', '-')}, "
                    f"conclusion_strength={item.get('conclusion_strength', '-')}, "
                    f"needs_verification={item.get('needs_verification', True)}, "
                    f"assumption={item.get('assumption', '-')}, "
                    f"impact={item.get('downstream_impact', '-')}"
                )
            blocks.append("证据链:\n- " + "\n- ".join(lines))

        report_text = html_report.get("section_text") or html_report.get("text_excerpt") or ""
        if report_text:
            blocks.append("HTML 报告正文参考:\n" + str(report_text)[:50000])

        metric_tables = self.build_metric_tables(analysis_result)
        if metric_tables:
            blocks.append("关键指标表:\n" + metric_tables)

        warnings = analysis_result.get("warnings", []) or []
        if warnings:
            blocks.append("读取告警:\n- " + "\n- ".join(str(item) for item in warnings[:6]))
        if analysis_limits:
            blocks.append("分析限制:\n- " + "\n- ".join(str(item) for item in analysis_limits[:8]))

        return "\n\n".join(block for block in blocks if block)

    @staticmethod
    def _clean_report_line(line: str) -> str:
        return re.sub(r"\s+", " ", str(line or "")).strip()

    @classmethod
    def _select_report_summary_lines(cls, text: str, limit: int = 5) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()
        pending = ""

        def push_pending() -> bool:
            nonlocal pending
            line = cls._clean_report_line(pending)
            pending = ""
            if len(line) < 12:
                return False
            if line in seen:
                return False
            seen.add(line)
            lines.append(line)
            return len(lines) >= limit

        for raw_line in str(text or "").splitlines():
            line = cls._clean_report_line(raw_line)
            if len(line) < 8:
                continue
            if re.fullmatch(r"[\d,]+(\.\d+)?%?", line):
                continue
            if re.fullmatch(r"[\w./\\-]+", line) and len(line) < 24:
                continue
            if re.fullmatch(r"\d+(\.\d+)*\s+.{2,30}", line):
                continue
            if any(marker in line for marker in cls.REPORT_LINE_SKIP_MARKERS):
                continue
            if re.fullmatch(r"[-–—=_]{3,}", line):
                continue
            pending = f"{pending} {line}".strip() if pending else line
            if len(pending) >= 120 or re.search(r"[。；.!?]$", line):
                if push_pending():
                    break
        if len(lines) < limit and pending:
            push_pending()
        return lines

    def build_existing_html_report_answer(self, analysis_result: dict[str, Any], question: str | None = None) -> str:
        context = analysis_result.get("project_context", {}) or {}
        html_report = context.get("html_report", {}) or {}
        sections = html_report.get("sections", []) or []
        project_id = analysis_result.get("project_id", "")
        report_file = analysis_result.get("report_source", "") or html_report.get("file", "")
        diagnosis_summary = analysis_result.get("diagnosis_summary", {}) or {}
        anomaly_summary = analysis_result.get("anomaly_summary", {}) or {}
        evidence_chain = [
            item
            for item in (analysis_result.get("evidence_chain", []) or [])
            if isinstance(item, dict)
            and item.get("value") is not None
            and str(item.get("display_value") or item.get("value") or "").strip() not in {"", "-"}
        ]
        evidence_chain = self._dedupe_report_evidence(evidence_chain)
        next_actions = analysis_result.get("next_actions", []) or diagnosis_summary.get("next_actions", []) or []

        abnormal_items = [
            item
            for item in (
                list(anomaly_summary.get("critical", []) or [])
                + list(anomaly_summary.get("warning", []) or [])
            )
            if isinstance(item, dict)
            and item.get("value") is not None
            and str(item.get("display_value") or item.get("value") or "").strip() not in {"", "-"}
        ]
        possible_causes = diagnosis_summary.get("possible_causes", []) or []
        notable_items = abnormal_items or self._notable_report_evidence(evidence_chain)

        if abnormal_items:
            first = abnormal_items[0]
            scope_summary = (
                f"本轮识别到需要优先复核的指标，首要复核项为 "
                f"{first.get('sample', '-')} {first.get('metric', '')}={first.get('display_value', '-')}。"
            )
        elif notable_items:
            first = notable_items[0]
            scope_summary = (
                "当前没有项目已验证阈值支持的异常结论；"
                f"但报告中存在需要结合实验设计复核的观测：{first.get('sample', '-')} "
                f"{first.get('metric', first.get('metric_key', '指标'))}="
                f"{first.get('display_value') or first.get('value')}。"
            )
        else:
            scope_summary = (
                f"项目 `{project_id}` 已完成报告读取，但未解析到可用于当前问题的非空结构化指标。"
            )

        question_header = []
        if question and question.strip() and question.strip() != "总结一下这个项目":
            question_header = [f"> 针对问题：{question.strip()}", ""]
        blocks = question_header + [
            "## 结论范围",
            scope_summary,
            "",
            "## 关键观测",
        ]
        if notable_items:
            for item in notable_items[:8]:
                rule = str(item.get("rule") or "").strip()
                rule_text = (
                    f"；项目阈值 {rule}"
                    if rule and not item.get("threshold_needs_project_validation")
                    else "；项目文件中未确认该指标适用阈值"
                )
                blocks.append(
                    f"- {item.get('sample', '-')} {item.get('metric', item.get('metric_key', '指标'))}="
                    f"{item.get('display_value') or item.get('value')}{rule_text}"
                    f"；来源 {item.get('source_file', '-')}::{item.get('source_field', '-')}"
                )
        else:
            blocks.append("- 当前没有可列出的非空结构化观测；不使用空值或占位符补齐结论。")

        blocks.extend(["", "## 证据依据"])
        if evidence_chain:
            for item in evidence_chain[:10]:
                evidence_line = (
                    f"- {item.get('category', '')}/{item.get('metric', '')}: "
                    f"{item.get('sample', '-')}={item.get('display_value') or item.get('value')}"
                    f"；证据状态 {item.get('severity', '-')}"
                    f"；来源 {item.get('source_file', '-')}::{item.get('source_field', '-')}"
                )
                if item.get("formula_source") == "project_code" and item.get("formula"):
                    evidence_line += (
                        "；计算口径 "
                        + self._humanize_project_formula(
                            str(item.get("metric_key") or ""),
                            str(item.get("formula") or ""),
                        )
                    )
                blocks.append(evidence_line)
        elif sections:
            blocks.append(f"- 已读取 HTML 报告 `{report_file}`，但未解析到可用于阈值判断的结构化证据表。")
        else:
            blocks.append("- 未读取到 HTML 报告正文或结构化证据表，需补齐证据后再判断指标影响范围。")

        blocks.extend(["", "## 证据限制"])
        if possible_causes:
            for item in possible_causes[:5]:
                blocks.append(f"- {item}")
        elif abnormal_items:
            interpretations = [str(item.get("interpretation", "")).strip() for item in abnormal_items if item.get("interpretation")]
            for item in list(dict.fromkeys(interpretations))[:5]:
                blocks.append(f"- {item}")
        else:
            blocks.append("- 项目文件中未确认适用阈值时，只能报告观测关系，不能确定判定偏高、偏低或异常。")

        blocks.extend(["", "## 可选复核动作"])
        if next_actions:
            for item in next_actions[:5]:
                blocks.append(f"- {item}")
        else:
            blocks.extend(
                [
                    "- 先复核与当前问题直接相关的 ReadsQC、AlignmentQC、FRiP 或相关性证据。",
                    "- 将异常指标回溯到样本制备、参考基因组、去重策略和细胞器 reads 处理配置。",
                    "- 若证据不足，先补充项目脚本、README、SOP 或报告说明中的指标口径再解释生物学含义。",
                ]
            )
        return "\n".join(blocks)

    @staticmethod
    def _dedupe_report_evidence(evidence_chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in evidence_chain:
            key = (
                str(item.get("metric_key") or ""),
                str(item.get("sample") or "-"),
                str(item.get("measurement_id") or item.get("metric_key") or ""),
                str(item.get("display_value") or item.get("value") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
        return rows

    @staticmethod
    def _notable_report_evidence(evidence_chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
        correlation = [
            item
            for item in evidence_chain
            if str(item.get("metric_key") or "") == "correlation"
        ]
        correlation.sort(key=lambda item: float(item.get("value") or 0.0))
        other = [
            item
            for item in evidence_chain
            if str(item.get("metric_key") or "") != "correlation"
        ]
        return (correlation[:2] + other[:6])[:8]

    async def stream_existing_html_report_answer(
        self,
        analysis_result: dict[str, Any],
    ) -> AsyncIterator[str]:
        answer = self.build_existing_html_report_answer(analysis_result)
        chunk_size = 240
        for index in range(0, len(answer), chunk_size):
            yield answer[index:index + chunk_size]

    # 各实验类型的行业参考阈值，用于在 system prompt 中给 LLM 提供上下文
    _ASSAY_THRESHOLDS: dict[str, str] = {
        "cuttag": (
            "CUT&Tag 行业参考阈值：比对率 >60%（推荐 >80%），去重后唯一比对率 >50%，"
            "重复率 <80%，FRiP >0.1（窄峰目标 >0.3），NRF >0.7，PBC1 >0.7，"
            "线粒体 reads 比例 <10%，样本间 Pearson/Spearman 相关系数 >0.9（同组重复）。"
        ),
        "chipseq": (
            "ChIP-seq 行业参考阈值：比对率 >70%，去重后唯一比对率 >50%，"
            "重复率 <80%，FRiP >0.05（转录因子 >0.1），NRF >0.7，PBC1 >0.7，"
            "样本间 Pearson/Spearman 相关系数 >0.9（同组重复）。"
        ),
        "cutrun": (
            "CUT&RUN 行业参考阈值：比对率 >60%，去重后唯一比对率 >40%，"
            "重复率 <70%，FRiP >0.1，线粒体 reads 比例 <10%，"
            "样本间 Pearson/Spearman 相关系数 >0.9（同组重复）。"
        ),
        "atacseq": (
            "ATAC-seq 行业参考阈值：比对率 >70%，去重后唯一比对率 >50%，"
            "重复率 <30%（高质量 <20%），FRiP >0.2，TSS Enrichment >4，"
            "线粒体 reads 比例 <20%，NRF >0.7，PBC1 >0.7，"
            "样本间 Pearson/Spearman 相关系数 >0.9（同组重复）。"
        ),
    }

    def _build_existing_html_report_summary_messages(self, analysis_result: dict[str, Any], question: str | None = None) -> list[dict[str, str]]:
        context = analysis_result.get("project_context", {}) or {}
        html_report = context.get("html_report", {}) or {}
        sections = html_report.get("sections", []) or []
        project_id = analysis_result.get("project_id", "")
        report_file = analysis_result.get("report_source", "") or html_report.get("file", "")
        assay_profile = analysis_result.get("assay_profile", {}) or {}
        experiment_design = analysis_result.get("experiment_design", {}) or context.get("experiment_design", {}) or {}
        anomaly_summary = analysis_result.get("anomaly_summary", {}) or {}

        # 1. 实验类型与样本概况
        assay = str(assay_profile.get("assay") or "").lower()
        assay_display = assay_profile.get("assay") or "未知实验类型"
        species = assay_profile.get("species") or experiment_design.get("species") or ""
        genome = assay_profile.get("genome") or experiment_design.get("genome") or ""
        samples_raw = experiment_design.get("samples") or assay_profile.get("samples") or []
        sample_names = [str(s.get("name") or s) for s in samples_raw if s][:20]
        has_spikein = bool(assay_profile.get("has_spikein") or experiment_design.get("has_spikein"))
        assay_hint = self._ASSAY_THRESHOLDS.get(assay, "")

        project_meta_lines = [f"实验类型: {assay_display}"]
        if species:
            project_meta_lines.append(f"物种/参考基因组: {species} {genome}".strip())
        if sample_names:
            project_meta_lines.append(f"样本列表({len(sample_names)}个): {', '.join(sample_names)}")
        if has_spikein:
            project_meta_lines.append("包含 Spike-in 归一化")

        # 2. 按类别组织结构化指标
        evidence_chain = [
            item
            for item in (analysis_result.get("evidence_chain", []) or [])
            if isinstance(item, dict)
            and item.get("value") is not None
            and str(item.get("display_value") or item.get("value") or "").strip() not in {"", "-"}
        ]
        evidence_chain = self._dedupe_report_evidence(evidence_chain)

        category_order = ["ReadsQC", "AlignmentQC", "FRiP", "CrossFRiP", "SpikeIn", "Correlation", "PeakQC"]
        by_category: dict[str, list[str]] = {}
        for item in evidence_chain[:60]:
            cat = str(item.get("category") or "Other")
            metric = str(item.get("metric") or item.get("metric_key") or "")
            sample = str(item.get("sample") or "-")
            val = str(item.get("display_value") or item.get("value") or "-")
            severity = str(item.get("severity") or "")
            rule = str(item.get("rule") or "")
            interp = str(item.get("interpretation") or "")
            src_file = str(item.get("source_file") or "-")
            line = f"  {sample} | {metric}={val}"
            if severity and severity not in {"-", "normal", "ok"}:
                line += f" [{severity}]"
            if rule and rule not in {"-", ""}:
                line += f" (阈值: {rule})"
            if interp and interp not in {"-", ""}:
                line += f" → {interp}"
            line += f" 来源:{src_file}"
            by_category.setdefault(cat, []).append(line)

        evidence_blocks: list[str] = []
        # 先按预定顺序，再补其他类别
        seen_cats: set[str] = set()
        for cat in category_order:
            if cat in by_category:
                evidence_blocks.append(f"### {cat}\n" + "\n".join(by_category[cat]))
                seen_cats.add(cat)
        for cat, lines in by_category.items():
            if cat not in seen_cats:
                evidence_blocks.append(f"### {cat}\n" + "\n".join(lines))

        # 3. 异常汇总
        critical = list(anomaly_summary.get("critical", []) or [])
        warning = list(anomaly_summary.get("warning", []) or [])
        anomaly_lines: list[str] = []
        for item in (critical + warning)[:20]:
            sev = str(item.get("severity") or "")
            s = str(item.get("sample") or "-")
            m = str(item.get("metric") or "")
            v = str(item.get("display_value") or item.get("value") or "-")
            r = str(item.get("rule") or "")
            i = str(item.get("interpretation") or "")
            anomaly_lines.append(f"  [{sev}] {s} {m}={v}" + (f" (阈值:{r})" if r else "") + (f" → {i}" if i else ""))

        # 4. HTML 报告分节（主要内容来源）
        section_blocks: list[str] = []
        used_chars = 0
        html_budget = 55000  # 留出空间给结构化数据
        for index, section in enumerate(sections[:20], start=1):
            title = self._clean_report_line(section.get("title") or f"模块{index}")
            text = str(section.get("text") or "").strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text[:8000]
            block = f"#### {title}\n{text}"
            remaining = html_budget - used_chars
            if remaining <= 0:
                break
            if len(block) > remaining:
                block = block[:remaining]
            section_blocks.append(block)
            used_chars += len(block)

        # ── System prompt：综合 QC 报告模式 ──────────────────────────────
        system_prompt = (
            "你是专业生物信息学数据质控分析师，负责根据项目 QC 报告和结构化证据生成完整的项目质控总结报告。\n\n"
            "【输出要求】\n"
            "1. 按以下顺序组织内容（有数据的章节必须写，无数据则注明「报告中未提供」）：\n"
            "   - 项目概况：实验类型、样本数量、测序策略\n"
            "   - 测序数据质量：各样本原始/clean reads 量、Q30 比例、接头检出率、trimming 保留率\n"
            "   - 比对质量：总比对率、唯一比对率、重复率、NRF/PBC1/PBC2（若有）、线粒体 reads 比例（若有）\n"
            "   - Peak 分析：各样本 Peak 数量、FRiP 分数\n"
            "   - 样本重复性：重复组间 Pearson/Spearman 相关系数\n"
            "   - Spike-in 归一化（有则写）：各样本 scaling factor 及差异分析\n"
            "   - 综合质控评估：对比参考阈值，明确标注各指标【达标】或【需关注】，列出需要重点跟进的样本和指标\n"
            "2. 每个指标必须给出具体数值和样本名，禁止只写定性描述（如【较高】）而不给数字。\n"
            "3. 可以指出【指标偏低/偏高/需关注】，但不得给出项目通过/失败/建议终止分析的最终结论。\n"
            "4. 若某类指标有异常（severity=critical 或 warning），在综合评估中单独列出并给出可能原因。\n"
            "5. 禁止编造数据——所有数值必须来自提供的结构化证据或 HTML 报告原文。\n"
            "6. 使用清晰的 Markdown 格式（允许表格、二级标题）。不要输出代码块。\n"
            "7. 细胞器指标（线粒体/叶绿体）必须结合物种和参考基因组解释。\n"
            "8. 禁止把 not_found_in_project_code 的公式描述为项目验证规则。"
        )

        # ── User prompt ────────────────────────────────────────────────────
        meta_block = "\n".join(project_meta_lines)
        threshold_block = f"\n【{assay_display} 行业参考阈值】\n{assay_hint}" if assay_hint else ""
        evidence_block = "\n\n".join(evidence_blocks) if evidence_blocks else "（暂未解析到结构化指标，请基于 HTML 报告正文提取数值）"
        anomaly_block = "\n".join(anomaly_lines) if anomaly_lines else "无"
        html_block = "\n\n".join(section_blocks) if section_blocks else "（未读取到 HTML 报告正文）"

        user_prompt = (
            f"项目ID: {project_id}\n"
            f"报告文件: {report_file}\n"
            f"{meta_block}"
            f"{threshold_block}\n\n"
            "请生成完整的项目质控总结报告。\n\n"
            "## 一、结构化 QC 指标（优先引用）\n"
            f"{evidence_block}\n\n"
            "## 二、异常指标汇总\n"
            f"{anomaly_block}\n\n"
            "## 三、HTML 报告原文（补充提取数值）\n"
            f"{html_block}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    async def generate_existing_html_report_answer(self, analysis_result: dict[str, Any], question: str | None = None) -> str:
        if not REPORT_SUMMARY_CLIENT_CONFIGURED:
            raise RuntimeError(
                "AI报告总结 LLM 客户端未配置：REPORT_SUMMARY_API_KEY / REPORT_SUMMARY_BASE_URL 均为空。"
                "请在 .env 中配置后重启服务。"
            )
        messages = self._build_existing_html_report_summary_messages(analysis_result, question=question)
        prompt_chars = sum(len(item.get("content", "")) for item in messages)
        started_at = perf_counter()
        response = await report_summary_model_client.chat.completions.create(
            model=REPORT_SUMMARY_MODEL_NAME,
            messages=messages,
            temperature=0,
            max_tokens=3200,
            stream=False,
        )
        answer = (response.choices[0].message.content or "").strip()
        logger.info(
            "llm_call done purpose=html_report_summary model=%s prompt_chars=%d output_chars=%d duration_ms=%.2f",
            REPORT_SUMMARY_MODEL_NAME,
            prompt_chars,
            len(answer),
            (perf_counter() - started_at) * 1000,
        )
        return answer

    def build_analysis_context(
        self,
        *,
        analysis_result: dict[str, Any],
        experience_summary: dict[str, Any],
    ) -> str:
        if analysis_result.get("report_mode") == "existing_html_report_summary":
            return self.build_existing_html_report_context(analysis_result)
        fact_packet = analysis_result.get("fact_packet") or {}
        reasoning_packet = analysis_result.get("reasoning_packet") or {}
        if fact_packet or reasoning_packet:
            return self.build_fact_and_reasoning_context(analysis_result)
        reasoning_packet = analysis_result.get("evidence_reasoning") or {}
        if reasoning_packet:
            return self.build_evidence_reasoning_context(analysis_result)

        diagnosis_summary = analysis_result.get("diagnosis_summary", {}) or {}
        automatic_findings = analysis_result.get("automatic_findings", []) or []
        warnings = analysis_result.get("warnings", []) or []
        analysis_limits = analysis_result.get("analysis_limits", []) or []
        next_actions = analysis_result.get("next_actions", []) or []
        evidence_files = analysis_result.get("evidence_files", []) or []
        evidence_chain = analysis_result.get("evidence_chain", []) or []
        evidence_cards = analysis_result.get("evidence_cards", []) or []
        validated_claims = analysis_result.get("validated_claims", []) or []
        claim_layers = analysis_result.get("claim_layers", {}) or {}
        claim_validation = analysis_result.get("claim_validation", {}) or {}
        agent_loop = analysis_result.get("agent_loop", {}) or {}
        anomaly_summary = analysis_result.get("anomaly_summary", {}) or {}
        analysis_plan = analysis_result.get("analysis_plan", {}) or {}
        tool_diagnostics = analysis_result.get("tool_diagnostics", []) or []
        cause_graph = analysis_result.get("cause_graph", {}) or {}
        experiment_design = analysis_result.get("experiment_design") or {}
        assay_profile = analysis_result.get("assay_profile") or {}
        response_plan = analysis_plan.get("response_plan") or {}
        target_metrics = self._normalized_target_metrics(analysis_plan)
        narrow_metric_mode = (
            len(target_metrics) == 1
            and analysis_result.get("question_type") not in {"overview", "diagnostic"}
        )
        if narrow_metric_mode:
            target_metric = next(iter(target_metrics))
            related_metrics = self.RELATED_METRIC_SCOPE.get(target_metric, {target_metric})
            evidence_chain = [
                item
                for item in evidence_chain
                if isinstance(item, dict) and item.get("metric_key") in related_metrics
            ]
            evidence_cards = [
                item
                for item in evidence_cards
                if isinstance(item, dict) and item.get("metric_id") in related_metrics
            ]
            validated_claims = [
                item
                for item in validated_claims
                if item.get("claim_type") != "observation"
                or item.get("metric_id") in related_metrics
            ]

        blocks = [
            f"项目ID: {analysis_result.get('project_id', '')}",
            f"问题: {analysis_result.get('question', '')}",
            f"问题类型: {analysis_result.get('question_type', '')}",
            f"置信度: {analysis_result.get('confidence', 0.0):.3f}",
            f"证据文件: {', '.join(evidence_files[:30]) if evidence_files else '无'}",
        ]
        if narrow_metric_mode:
            blocks.append(
                "窄指标证据模式: "
                + ", ".join(sorted(target_metrics))
                + "；回答必须优先使用下方结构化观测值，不得声称已读取到的指标缺失。"
            )
        if analysis_result.get("report_mode") or analysis_result.get("report_source"):
            blocks.append(
                "Report source:\n"
                f"- mode: {analysis_result.get('report_mode', '')}\n"
                f"- file: {analysis_result.get('report_source', '')}"
            )
        if analysis_plan:
            evidence_requests = analysis_plan.get("evidence_requests", []) or []
            evidence_request_status = analysis_result.get("evidence_request_status", []) or []
            bio_skill_references = analysis_plan.get("bio_skill_references", []) or []
            loaded_bio_skills = analysis_plan.get("loaded_bio_skills", []) or []
            bio_skill_index = analysis_plan.get("bio_skill_index", {}) or {}
            selected_tools = analysis_plan.get("selected_tools", []) or []
            request_lines = []
            for item in evidence_requests[:8]:
                if isinstance(item, dict):
                    request_lines.append(
                        f"{item.get('type', '-')}/{item.get('module', '-')}: "
                        f"metric={item.get('metric', '-')}; reason={item.get('reason', '-')}"
                    )
            status_lines = []
            for item in evidence_request_status[:10]:
                if isinstance(item, dict):
                    matched_files = item.get("matched_files", []) or []
                    status_lines.append(
                        f"{item.get('status', '-')}: {item.get('type', '-')}/{item.get('module', '-')} "
                        f"metric={item.get('metric', '-')}; message={item.get('message', '-')}; "
                        f"files={', '.join(str(file) for file in matched_files[:3]) or '-'}"
                    )
            boundary = analysis_plan.get("answer_boundary", {}) or {}
            boundary_lines = []
            allow = boundary.get("allow", []) or []
            forbid = boundary.get("forbid", []) or []
            if allow:
                boundary_lines.append("允许: " + "；".join(str(item) for item in allow[:5]))
            if forbid:
                boundary_lines.append("禁止: " + "；".join(str(item) for item in forbid[:5]))
            bio_skill_lines = []
            for item in ([] if narrow_metric_mode else bio_skill_references[:4]):
                if isinstance(item, dict):
                    bio_skill_lines.append(
                        f"{item.get('title', '-')}: {item.get('guidance', '-')} "
                        f"boundary={item.get('boundary', '-')}"
                    )
            tool_lines = []
            for item in selected_tools[:8]:
                if isinstance(item, dict):
                    tool_lines.append(
                        f"{item.get('name', '-')}: status={item.get('execution_status', '-')}; "
                        f"executor={item.get('executor', '-')}; contract={item.get('output_contract', '-')}"
                    )
            loaded_skill_lines = []
            blocks.append(
                "分析路线规划:\n"
                f"- intent: {analysis_plan.get('intent', '')}\n"
                f"- target_metrics: {', '.join(analysis_plan.get('target_metrics', []) or []) or '-'}\n"
                f"- target_samples: {', '.join(analysis_plan.get('target_samples', []) or []) or '-'}\n"
                f"- requires_script_review: {analysis_plan.get('requires_script_review', False)}\n"
                + ("- evidence_requests:\n  - " + "\n  - ".join(request_lines) + "\n" if request_lines else "")
                + ("- evidence_request_status:\n  - " + "\n  - ".join(status_lines) + "\n" if status_lines else "")
                + ("- bioSkills_general_reference:\n  - " + "\n  - ".join(bio_skill_lines) + "\n" if bio_skill_lines else "")
                + (
                    f"- bioSkills_index: indexed={bio_skill_index.get('total_indexed_skills', 0)}; "
                    f"loading={bio_skill_index.get('full_skill_loading', '-')}\n"
                    if bio_skill_index
                    else ""
                )
                + ("- loaded_bioSkills:\n  - " + "\n  - ".join(loaded_skill_lines) + "\n" if loaded_skill_lines else "")
                + ("- selected_tools:\n  - " + "\n  - ".join(tool_lines) + "\n" if tool_lines else "")
                + ("- answer_boundary:\n  - " + "\n  - ".join(boundary_lines) if boundary_lines else "")
            )
        if response_plan:
            blocks.append(
                "Response plan:\n"
                f"- complexity: {response_plan.get('complexity', 'focused')}\n"
                f"- required_sections: {', '.join(response_plan.get('required_sections', []) or [])}\n"
                f"- claim_contract: {', '.join(response_plan.get('claim_contract', []) or [])}"
            )
        if experiment_design:
            design_lines = [
                (
                    f"{item.get('sample', '-')}: condition={item.get('condition', '-')}; "
                    f"replicate={item.get('replicate', '-')}; target={item.get('target', '-')}; "
                    f"role={item.get('role', '-')}; control_for={item.get('control_for', [])}; "
                    f"batch={item.get('batch', '-')}"
                )
                for item in (experiment_design.get("samples", []) or [])[:20]
                if isinstance(item, dict)
            ]
            differential = experiment_design.get("differential_analysis") or {}
            blocks.append(
                "Structured experiment design:\n- "
                + ("\n- ".join(design_lines) if design_lines else "unresolved")
                + "\n"
                + f"- differential_ready={differential.get('ready', False)}; "
                + f"reasons={', '.join(differential.get('reasons', []) or []) or '-'}"
            )
        if assay_profile:
            blocks.append(
                "Assay-specific evidence contract:\n"
                f"- assay={assay_profile.get('assay', 'generic')}; "
                f"target_class={assay_profile.get('target_class', 'unknown')}\n"
                f"- required_chain={', '.join(assay_profile.get('required_evidence_chain', []) or [])}\n"
                f"- missing_evidence={', '.join(assay_profile.get('missing_evidence', []) or []) or '-'}\n"
                f"- specialized_rules={', '.join(assay_profile.get('specialized_rules', []) or []) or '-'}"
            )
        project_context_block = (
            self.build_narrow_project_context_block(analysis_result, target_metrics)
            if narrow_metric_mode
            else self.build_project_context_block(analysis_result)
        )
        if project_context_block:
            blocks.append("Pre-analysis project context:\n" + project_context_block)
        conclusions = diagnosis_summary.get("conclusions", []) or []
        if conclusions:
            blocks.append("需复核指标:\n- " + "\n- ".join(str(item) for item in conclusions[:5]))
        evidence = diagnosis_summary.get("evidence", []) or []
        if evidence:
            blocks.append("关键证据:\n- " + "\n- ".join(str(item) for item in evidence[:8]))
        abnormal_items = list(anomaly_summary.get("critical", []) or []) + list(anomaly_summary.get("warning", []) or [])
        if abnormal_items:
            lines = []
            for item in abnormal_items[:12]:
                lines.append(
                    f"{item.get('severity', '')}: {item.get('sample', '-')} "
                    f"{item.get('metric', '')}={item.get('display_value', '-')}; "
                    f"rule={item.get('rule', '-')}; "
                    f"source={item.get('source_file', '-')}::{item.get('source_field', '-')}"
                )
            blocks.append("结构化需复核指标:\n- " + "\n- ".join(lines))
        if validated_claims:
            card_by_id = {
                str(card.get("evidence_id") or ""): card
                for card in evidence_cards
                if isinstance(card, dict) and card.get("evidence_id")
            }
            claim_lines = []
            for item in validated_claims[:16]:
                if not isinstance(item, dict):
                    continue
                evidence_ids = [str(value) for value in item.get("evidence_ids", []) or []]
                source_parts = []
                for evidence_id in evidence_ids[:3]:
                    card = card_by_id.get(evidence_id)
                    if card:
                        source_parts.append(
                            f"{card.get('source_file', '-')}::{card.get('source_field', '-')}"
                        )
                claim_lines.append(
                    f"{item.get('claim_type', '-')}/{item.get('causal_level', '-')}/"
                    f"{item.get('support_level', '-')}: "
                    f"{item.get('text', '-')}; evidence_ids={','.join(evidence_ids) or '-'}; "
                    f"sources={'; '.join(source_parts) or '-'}"
                )
            blocks.append("已校验结构化 Claim:\n- " + "\n- ".join(claim_lines))
            blocks.append(
                "Claim 渲染合同:\n"
                f"- direct_observations={len(claim_layers.get('direct_observations', []) or [])}\n"
                f"- associated_phenomena={len(claim_layers.get('associated_phenomena', []) or [])}\n"
                f"- possible_explanations={len(claim_layers.get('possible_explanations', []) or [])}\n"
                f"- verified_causes={len(claim_layers.get('verified_causes', []) or [])}\n"
                f"- ranked_causes={len(claim_layers.get('ranked_causes', []) or [])}\n"
                f"- limitations={len(claim_layers.get('limitations', []) or [])}\n"
                f"- actions={len(claim_layers.get('actions', []) or [])}\n"
                f"- verifier_passed={claim_validation.get('passed', False)}"
            )
        if evidence_cards:
            referenced_ids = {
                str(evidence_id)
                for claim in validated_claims
                if isinstance(claim, dict)
                for evidence_id in claim.get("evidence_ids", []) or []
            }
            scoped_cards = [
                card
                for card in evidence_cards
                if not referenced_ids or str(card.get("evidence_id") or "") in referenced_ids
            ]
            card_lines = []
            for card in scoped_cards[:10]:
                card_lines.append(
                    f"{card.get('evidence_id', '-')}: metric={card.get('metric_id', '-')}; "
                    f"sample={card.get('sample', '-')}; value={card.get('display_value', '-')}; "
                    f"numerator={card.get('numerator_value', card.get('numerator', '-'))} "
                    f"({card.get('numerator_name', '-')}); "
                    f"denominator={card.get('denominator_value', card.get('denominator', '-'))} "
                    f"({card.get('denominator_name', '-')}); "
                    f"phase={card.get('processing_phase', '-')}; species={card.get('species', '-')}; "
                    f"source={card.get('source_file', '-')}::{card.get('source_field', '-')}; "
                    f"threshold_verified={card.get('threshold_verified', False)}"
                )
            blocks.append("Canonical Evidence Cards:\n- " + "\n- ".join(card_lines))
        if agent_loop:
            blocks.append(
                "Plan→Tool→Observe trace:\n"
                f"- rounds={agent_loop.get('round_count', 0)}/{agent_loop.get('max_rounds', 3)}\n"
                f"- stop_reason={agent_loop.get('stop_reason', '-')}"
            )
        if evidence_chain and not validated_claims:
            lines = []
            for item in evidence_chain[:16]:
                lines.append(
                    f"{item.get('category', '')}/{item.get('metric', '')}: "
                    f"sample={item.get('sample', '-')}, value={item.get('display_value', '-')}, "
                    f"severity={item.get('severity', '-')}, "
                    f"source={item.get('source_file', '-')}::{item.get('source_field', '-')}, "
                    f"formula={item.get('formula', '-')}, "
                    f"formula_source={item.get('formula_source', '-')}, "
                    f"threshold_source={item.get('threshold_source', '-')}, "
                    f"needs_verification={item.get('needs_verification', True)}, "
                    f"assumption={item.get('assumption', '-')}, "
                    f"impact={item.get('downstream_impact', '-')}"
                )
            blocks.append("证据链:\n- " + "\n- ".join(lines))
        if tool_diagnostics:
            diagnostic_lines = []
            for item in tool_diagnostics[:6]:
                if not isinstance(item, dict):
                    continue
                diagnostic_lines.append(
                    f"{item.get('tool', '-')}: status={item.get('status', '-')}; "
                    f"summary={item.get('summary', '-')}; boundary={item.get('boundary', '-')}"
                )
                reasoning = item.get("reasoning_chain", []) or []
                if reasoning:
                    diagnostic_lines.append("  reasoning: " + " -> ".join(str(part) for part in reasoning[:5]))
                gaps = item.get("evidence_gaps", []) or []
                if gaps:
                    diagnostic_lines.append("  evidence_gaps: " + "；".join(str(part) for part in gaps[:4]))
                next_checks = item.get("next_checks", []) or []
                if next_checks:
                    diagnostic_lines.append("  next_checks: " + "；".join(str(part) for part in next_checks[:4]))
            if diagnostic_lines:
                blocks.append("专业诊断工具结果:\n- " + "\n- ".join(diagnostic_lines))
        ranked_causes = cause_graph.get("ranked_causes", []) if isinstance(cause_graph, dict) else []
        if ranked_causes and not validated_claims:
            ranked_lines = []
            for cause in ranked_causes[:5]:
                if not isinstance(cause, dict):
                    continue
                ranked_lines.append(
                    f"#{cause.get('rank', '-')} {cause.get('label', cause.get('cause_id', '-'))}: "
                    f"score={cause.get('score', 0)}; support={cause.get('support_level', '-')}; "
                    f"reason={cause.get('reasoning_summary', '-')}"
                )
                support = cause.get("supporting_evidence", []) or []
                if support:
                    ranked_lines.append(
                        "  supporting_evidence: "
                        + "；".join(
                            f"{item.get('relation', '-')}/{item.get('sample', '-')}/"
                            f"{item.get('metric_key', '-')}={item.get('value', '-')} "
                            f"[{item.get('strength', '-')}]"
                            for item in support[:4]
                            if isinstance(item, dict)
                        )
                    )
                against = cause.get("contradicting_evidence", []) or []
                if against:
                    ranked_lines.append(
                        "  contradicting_evidence: "
                        + "；".join(
                            f"{item.get('sample', '-')}/{item.get('metric_key', '-')}={item.get('value', '-')}"
                            for item in against[:3]
                            if isinstance(item, dict)
                        )
                    )
                checks = cause.get("verification_actions", []) or []
                if checks:
                    ranked_lines.append("  verification: " + "；".join(str(item) for item in checks[:3]))
            confidence = cause_graph.get("diagnostic_confidence") or {}
            ranked_lines.append(
                f"diagnostic_confidence={confidence.get('level', 'low')}/"
                f"{confidence.get('score', 0)}; boundary={confidence.get('boundary', '-')}"
            )
            blocks.append("排序后的差异诊断:\n- " + "\n- ".join(ranked_lines))
        cause_nodes = cause_graph.get("nodes", []) if isinstance(cause_graph, dict) else []
        if cause_nodes and not validated_claims:
            cause_lines = []
            for node in cause_nodes[:4]:
                if not isinstance(node, dict):
                    continue
                focus_metric = node.get("focus_metric", "-")
                cause_lines.append(f"focus_metric={focus_metric}")
                for section in ("primary_evidence", "upstream_evidence", "parallel_evidence", "downstream_evidence"):
                    evidence_items = node.get(section, []) or []
                    if not evidence_items:
                        continue
                    cause_lines.append(
                        f"  {section}: "
                        + "; ".join(
                            f"{item.get('sample', '-')}/{item.get('metric_key', '-')}={item.get('value', '-')}"
                            for item in evidence_items[:6]
                            if isinstance(item, dict)
                        )
                    )
                hypotheses = node.get("candidate_causes", []) or []
                if hypotheses:
                    cause_lines.append(
                        "  candidate_causes: "
                        + "; ".join(
                            f"{item.get('cause', '-')}({item.get('support_level', '-')})"
                            for item in hypotheses[:6]
                            if isinstance(item, dict)
                        )
                    )
                gaps = node.get("evidence_gaps", []) or []
                if gaps:
                    cause_lines.append("  evidence_gaps: " + "; ".join(str(item) for item in gaps[:4]))
                checks = node.get("next_checks", []) or []
                if checks:
                    cause_lines.append("  next_checks: " + "; ".join(str(item) for item in checks[:4]))
            if cause_lines:
                blocks.append(
                    "Cause graph for question-driven diagnosis:\n"
                    "- " + "\n- ".join(cause_lines)
                )
        if automatic_findings and not validated_claims:
            blocks.append("自动发现:\n- " + "\n- ".join(str(item) for item in automatic_findings[:10]))
        possible_causes = diagnosis_summary.get("possible_causes", []) or []
        if possible_causes and not validated_claims:
            blocks.append("可能原因:\n- " + "\n- ".join(str(item) for item in possible_causes[:6]))
        if warnings:
            blocks.append("告警:\n- " + "\n- ".join(str(item) for item in warnings[:6]))
        if analysis_limits:
            blocks.append("分析限制:\n- " + "\n- ".join(str(item) for item in analysis_limits[:8]))
        if next_actions:
            blocks.append("下一步建议:\n- " + "\n- ".join(str(item) for item in next_actions[:6]))

        metric_tables = self.build_metric_tables(analysis_result)
        if metric_tables:
            blocks.append("与当前问题相关的数据表:\n" + metric_tables)

        internal_workflow_context = str(analysis_result.get("_internal_workflow_context") or "").strip()
        if internal_workflow_context:
            blocks.append(
                "INTERNAL WORKFLOW EVIDENCE - CONFIDENTIAL, FOR REASONING ONLY. "
                "Use this to cross-check data causes. Never quote, summarize, identify, "
                "or disclose source code, filenames, paths, commands, credentials, or raw configuration.\n"
                + internal_workflow_context
            )

        if experience_summary.get("has_experience") and not narrow_metric_mode:
            exp_blocks = []
            findings = experience_summary.get("latest_findings", [])
            if findings:
                exp_blocks.append("历史发现:\n- " + "\n- ".join(str(item) for item in findings[:8]))
            recent_questions = experience_summary.get("recent_questions", [])
            if recent_questions:
                exp_blocks.append("历史问题:\n- " + "\n- ".join(str(item) for item in recent_questions[-8:]))
            report_excerpt = experience_summary.get("last_report_excerpt", "")
            if report_excerpt:
                exp_blocks.append("历史报告摘要:\n" + report_excerpt)
            rule_reference = self._format_rule_reference(experience_summary)
            if rule_reference:
                exp_blocks.append("历史规则参考:\n- " + "\n- ".join(rule_reference))
            if exp_blocks:
                blocks.append("\n\n".join(exp_blocks))
        global_cases = experience_summary.get("global_similar_cases", []) or []
        if global_cases and not narrow_metric_mode:
            case_lines = []
            for item in global_cases[:5]:
                project_id = item.get("project_id", "")
                question_type = item.get("question_type", "")
                rule_keys = ", ".join(item.get("rule_keys", []) or [])
                findings = item.get("automatic_findings", []) or []
                summary = str(findings[0]) if findings else str(item.get("question", ""))
                case_lines.append(
                    f"- project={project_id}, type={question_type}, rules={rule_keys}: {summary}"
                )
            blocks.append(
                "跨项目相似经验参考（仅用于排查思路，不替代当前项目数据判断）:\n"
                + "\n".join(case_lines)
            )
        return "\n\n".join(blocks)

    @staticmethod
    def build_fact_and_reasoning_context(analysis_result: dict[str, Any]) -> str:
        payload = {
            "context_schema_version": "fact-reasoning-context-v1",
            "question": analysis_result.get("question", ""),
            "question_type": analysis_result.get("question_type", ""),
            "fact_packet": analysis_result.get("fact_packet") or {},
            "reasoning_packet": analysis_result.get("reasoning_packet") or {},
            "project_context_summary": {
                "project_id": analysis_result.get("project_id", ""),
                "analysis_plan": ((analysis_result.get("analysis_plan") or {}).get("response_plan") or {}),
            },
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def build_evidence_reasoning_context(analysis_result: dict[str, Any]) -> str:
        """Serialize one valid, compact reasoning object without cutting JSON text."""

        packet = analysis_result.get("evidence_reasoning") or {}
        catalog_summary = (
            (analysis_result.get("project_context") or {}).get(
                "evidence_catalog_summary"
            )
            or {}
        )
        payload = {
            "context_schema_version": "professional-analysis-context-v2",
            "question_scope": {
                "project_id": analysis_result.get("project_id"),
                "question": analysis_result.get("question"),
                "question_type": analysis_result.get("question_type"),
                "target_metrics": packet.get("target_metrics", []),
                "evidence_coverage": packet.get("evidence_coverage", {}),
                "catalog_summary": catalog_summary,
                "response_plan": ((analysis_result.get("analysis_plan") or {}).get("response_plan") or {}),
            },
            "project_observations": packet.get("project_observations")
            or packet.get("evidence", []),
            "user_assertions": packet.get("user_assertions")
            or analysis_result.get("user_assertions", []),
            "relational_tables": packet.get("relational_tables", []),
            "derived_relationships": packet.get("derived_relationships", []),
            "hypothesis_panel": packet.get("hypothesis_panel", []),
            "evidence_conflicts": packet.get("evidence_conflicts")
            or analysis_result.get("evidence_conflicts", []),
            "data_availability": packet.get("data_availability")
            or packet.get("data_status", {}),
            "domain_interpretation_rules": packet.get(
                "domain_interpretation_rules",
                {
                    "assay": packet.get("assay", {}),
                    "skill_decision_cards": packet.get(
                        "skill_decision_cards", []
                    ),
                },
            ),
            "allowed_inferences": packet.get("allowed_inferences", []),
            "structured_conclusions": packet.get("structured_conclusions")
            or packet.get("conclusions", []),
            "limitations": packet.get("limitations", []),
            "verification_actions": packet.get("verification_actions")
            or packet.get("next_actions", []),
        }
        return BusinessResponseService._serialize_reasoning_payload(
            payload,
            max_chars=12000,
        )

    @staticmethod
    def _serialize_reasoning_payload(
        payload: dict[str, Any],
        *,
        max_chars: int,
    ) -> str:
        compact = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
        availability = compact.get("data_availability")
        if isinstance(availability, dict) and isinstance(
            availability.get("items"), list
        ):
            targets = set(
                (compact.get("question_scope") or {}).get("target_metrics", [])
                or []
            )
            items = sorted(
                availability["items"],
                key=lambda item: (
                    str(item.get("metric_id") or "") not in targets,
                    str(item.get("state") or "") == "not_indexed",
                    str(item.get("metric_id") or ""),
                ),
            )
            availability["items"] = [
                {
                    **{
                        key: value
                        for key, value in item.items()
                        if key
                        not in {
                            "indexed_files",
                            "selected_indexed_files",
                            "parse_errors",
                        }
                    },
                    "indexed_files": (item.get("indexed_files") or [])[:2],
                    "selected_indexed_files": (
                        item.get("selected_indexed_files") or []
                    )[:2],
                    "parse_errors": (item.get("parse_errors") or [])[:1],
                }
                for item in items[:14]
                if isinstance(item, dict)
            ]

        def render() -> str:
            return json.dumps(
                compact,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )

        text = render()
        if len(text) <= max_chars:
            return text

        limits = {
            "project_observations": 8,
            "user_assertions": 6,
            "derived_relationships": 28,
            "structured_conclusions": 4,
            "limitations": 5,
            "verification_actions": 5,
            "evidence_conflicts": 6,
        }
        omitted: dict[str, int] = {}
        for key, limit in limits.items():
            value = compact.get(key)
            if isinstance(value, list) and len(value) > limit:
                omitted[key] = len(value) - limit
                compact[key] = value[:limit]

        for table in compact.get("relational_tables", []) or []:
            if not isinstance(table, dict):
                continue
            for key, limit in (("cells", 24), ("pairs", 18), ("rows", 18)):
                value = table.get(key)
                if isinstance(value, list) and len(value) > limit:
                    table[f"{key}_omitted_count"] = len(value) - limit
                    table[key] = value[:limit]

        rules = compact.get("domain_interpretation_rules") or {}
        for skill in rules.get("skill_decision_cards", []) or []:
            if not isinstance(skill, dict):
                continue
            card = str(skill.get("decision_card") or "")
            if len(card) > 500:
                skill["decision_card"] = card[:497] + "..."
                skill["decision_card_compacted"] = True
        compact["budget_compaction"] = {
            "applied": True,
            "omitted_counts": omitted,
        }
        text = render()
        if len(text) <= max_chars:
            return text

        compact["derived_relationships"] = compact.get(
            "derived_relationships", []
        )[:12]
        compact["project_observations"] = compact.get(
            "project_observations", []
        )[:6]
        compact["relational_tables"] = [
            {
                key: value
                for key, value in table.items()
                if key
                in {
                    "table_id",
                    "row_dimension",
                    "column_dimension",
                    "dimensions",
                    "directional",
                    "cells",
                    "pairs",
                    "rows",
                    "cells_omitted_count",
                    "pairs_omitted_count",
                    "rows_omitted_count",
                }
            }
            for table in compact.get("relational_tables", [])[:3]
            if isinstance(table, dict)
        ]
        for table in compact["relational_tables"]:
            for key in ("cells", "pairs", "rows"):
                if isinstance(table.get(key), list):
                    table[key] = table[key][:10]
        text = render()
        if len(text) <= max_chars:
            return text

        fallback = {
            key: compact.get(key)
            for key in (
                "context_schema_version",
                "question_scope",
                "project_observations",
                "user_assertions",
                "relational_tables",
                "evidence_conflicts",
                "data_availability",
                "allowed_inferences",
                "limitations",
                "verification_actions",
                "budget_compaction",
            )
        }
        fallback["project_observations"] = fallback.get(
            "project_observations", []
        )[:4]
        fallback["relational_tables"] = fallback.get("relational_tables", [])[:2]
        fallback_text = json.dumps(
            fallback,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        if len(fallback_text) <= max_chars:
            return fallback_text
        scope = fallback.get("question_scope") or {}
        question = str(scope.get("question") or "")
        if len(question) > 800:
            scope["question"] = question[:797] + "..."
            scope["question_compacted"] = True
        for assertion in fallback.get("user_assertions", []) or []:
            if isinstance(assertion, dict):
                text = str(assertion.get("text") or "")
                if len(text) > 400:
                    assertion["text"] = text[:397] + "..."
        availability = fallback.get("data_availability") or {}
        minimal = {
            "context_schema_version": fallback.get("context_schema_version"),
            "question_scope": scope,
            "project_observations": [
                {
                    key: item.get(key)
                    for key in (
                        "evidence_id",
                        "metric_id",
                        "sample",
                        "sample_name",
                        "read_sample",
                        "peak_set",
                        "left_sample",
                        "right_sample",
                        "comparison_type",
                        "pair_type",
                        "value",
                        "display_value",
                        "unit",
                        "source",
                        "source_field",
                    )
                    if item.get(key) not in (None, "", [], {})
                }
                for item in fallback.get("project_observations", [])[:4]
                if isinstance(item, dict)
            ],
            "user_assertions": fallback.get("user_assertions", [])[:3],
            "relational_tables": [],
            "evidence_conflicts": fallback.get("evidence_conflicts", [])[:3],
            "data_availability": {
                "by_metric": availability.get("by_metric", availability)
                if isinstance(availability, dict)
                else {}
            },
            "allowed_inferences": fallback.get("allowed_inferences", [])[:5],
            "limitations": fallback.get("limitations", [])[:4],
            "verification_actions": fallback.get("verification_actions", [])[:4],
            "budget_compaction": {
                **(fallback.get("budget_compaction") or {}),
                "final_minimal_form": True,
            },
        }
        for table in fallback.get("relational_tables", [])[:2]:
            if not isinstance(table, dict):
                continue
            table_view = {
                key: table.get(key)
                for key in (
                    "table_id",
                    "row_dimension",
                    "column_dimension",
                    "dimensions",
                    "directional",
                )
                if table.get(key) not in (None, "", [], {})
            }
            for records_key in ("cells", "pairs", "rows"):
                records = table.get(records_key)
                if isinstance(records, list):
                    table_view[records_key] = records[:6]
                    table_view[f"{records_key}_omitted_count"] = max(
                        0, len(records) - len(table_view[records_key])
                    )
            minimal["relational_tables"].append(table_view)
        final_text = json.dumps(
            minimal,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        while len(final_text) > max_chars:
            reduced = False
            for table in reversed(minimal["relational_tables"]):
                for key in ("rows", "pairs", "cells"):
                    records = table.get(key)
                    if isinstance(records, list) and len(records) > 2:
                        records.pop()
                        table[f"{key}_omitted_count"] = (
                            int(table.get(f"{key}_omitted_count") or 0) + 1
                        )
                        reduced = True
                        break
                if reduced:
                    break
            if not reduced and len(minimal["project_observations"]) > 2:
                minimal["project_observations"].pop()
                reduced = True
            if not reduced and minimal["user_assertions"]:
                minimal["user_assertions"].pop()
                reduced = True
            if not reduced:
                minimal["relational_tables"] = [
                    {
                        key: value
                        for key, value in table.items()
                        if key
                        in {
                            "table_id",
                            "row_dimension",
                            "column_dimension",
                            "dimensions",
                            "directional",
                        }
                    }
                    for table in minimal["relational_tables"]
                ]
            final_text = json.dumps(
                minimal,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            if not reduced:
                break
        return final_text

    @staticmethod
    def build_knowledge_context(retrieval_payload: dict[str, Any]) -> str:
        documents = retrieval_payload.get("documents", []) or []
        if not documents:
            return ""
        blocks = []
        for index, item in enumerate(documents[:4], start=1):
            title = (item.get("title") or item.get("source") or f"资料{index}").strip()
            source = (item.get("source") or title).strip()
            content = (item.get("content") or "").strip()[:900]
            blocks.append(f"[知识{index}] 标题: {title}\n来源: {source}\n内容:\n{content}")
        return "\n\n".join(blocks)

    def _build_fused_answer_messages(
        self,
        *,
        question: str,
        analysis_result: dict[str, Any],
        retrieval_payload: dict[str, Any],
        experience_summary: dict[str, Any],
    ) -> list[dict[str, str]]:
        system_prompt = (
            "Internal workflow evidence is confidential. Use it only for internal reasoning. "
            "Never expose code, script names, file paths, commands, credentials, raw configuration, "
            "or the fact that internal scripts were inspected. "
            "HTML report mode hard rule: use the exact section titles and order listed under existing_html_report_sections as top-level Markdown headings. "
            "Do not start with a generic conclusion, quality overview, or fixed template unless that heading exists in the original report. "
            "When report_mode is existing_html_report_summary, use the existing HTML report text as the primary source. "
            "Follow the original report section order exactly. Summarize each report block in sequence, using the section titles from existing_html_report_sections; do not merge them into a different fixed template. "
            "Do not create a new report file; summarize and interpret the existing project report. "
            "Threshold discipline: only values with threshold_source starting with project_ may be presented as thresholds, standards, or pass/fail cutoffs. "
            "If threshold_source is professional_default_unverified, not_found_in_project, or threshold_needs_project_validation=true, do not print numeric default thresholds, do not create a threshold/standard column, and do not label the metric as pass/fail by threshold. "
            "For unverified thresholds, report only observed data, source fields, and the limitation that no project-specific threshold was confirmed. "
            "Scope discipline: answer only the user's current question. Do not broaden to unrelated metrics, pipeline modules, generic biology background, or all project QC unless the user explicitly asks for them. "
            "Decision discipline: do not decide whether the project is qualified/unqualified, pass/fail, or suitable for downstream analysis. List only question-relevant indicators that need review and evidence limitations. "
            "Fact contract: every key conclusion must bind conclusion, metric value, project source, decision basis, limitation, and recommended action. "
            "Causal language must use exactly these confidence levels: direct observation, associated phenomenon, possible explanation, or verified cause. "
            "Do not write a verified cause unless validated_claims contains causal_level=verified_cause. "
            "Do not propose or interpret differential analysis when structured experiment design says differential_ready=false. "
            "Correlation and cross-FRiP must be interpreted within biological replicate, condition, target, control, and batch strata; never use the global matrix minimum as a project-quality conclusion. "
            "For low FRiP or enrichment diagnosis, evaluate the evidence chain from sequencing depth through mapping, organelle rate, library complexity, peaks, FRiP, and replicate consistency. "
            "Follow response_plan complexity: simple questions get a direct compact answer; comprehensive project diagnosis starts with the analysis plan and then presents modular evidence. "
            "你是项目分析业务智能体。请用自然、专业的 Markdown 方式回答，不要写得像模板报告。"
            "回答目标是帮用户解决当前问题，不是复述完整分析流程。先用一两句话直接回应问题，再按问题需要自然补充证据、原因链或复核动作；不要固定写成「处理方向/关键证据/原因链路/复核步骤」。"
            "如果用户只问一个指标或一个现象，只回答该指标/现象；不要主动扩展到其它 QC 模块、历史项目、知识库背景或 bioSkills 说明。"
            "除 AI 报告总结外，回答最多 5 个小节，每个小节最多 6 条要点；避免长段背景、泛泛解释和重复结论。"
            "专业性要求：不能只说高/低或异常。必须把当前指标放到实验类型、样本角色、流程参数、公式来源和证据限制中解释，给出从上游到下游的因果链路。"
            "Adapter 处理阶段纪律：ReadsQC 的 Adapter/raw reads 只表示原始 reads 中检测到接头相关序列；除非 clean FASTQ/FastQC 或 trimming 后结果直接证明，否则禁止称为「接头残留」或声称 clean reads 仍有接头。"
            "物种纪律：必须依据 pipeline_config/config 中的 species/reference 解释细胞器指标。hg38、hg19、GRCh、mm10 等动物参考不得解释为叶绿体/质体；物种不明时只写「细胞器 reads」，不得自行猜测植物。"
            "证据存在性纪律：只要证据链或相关数据表已列出目标指标的样本值，就必须引用这些值，禁止声称该指标缺失、未读取或无法获得。"
            "原因链路要自然融入回答：说明观测指标、可能上游原因、下游影响、当前证据支持/不支持的部分，以及如何验证；不要机械输出箭头模板。"
            "如果证据不足，要明确指出缺的是哪类证据，例如原始 fastq QC、fragment size、IgG/Input 角色、peak calling 参数、细胞器过滤结果、脚本公式或项目专属阈值。"
            "如果 bioSkills_general_reference 提供了 CUT&Tag/CUT&RUN/ChIP/ATAC 的通用排查逻辑，可以用于组织原因链路和复核动作，但不要在回答中暴露 bioSkills 名称，也不要把其中阈值当项目标准。"
            "优先依据当前项目数据；知识库和历史经验只能作为补充解释，不能覆盖当前数据结论。"
            "解释项目异常时必须结合 sample_roles、pipeline_config 和 workflow_detected_parameters。"
            "说明发现前必须列出支撑该发现的具体指标和来源字段；只有 threshold_source 为项目文件验证来源时才允许说明阈值和适用前提，否则必须写「项目文件中未确认该指标阈值/标准」。计算方式只能引用 formula_source=project_code 的项目脚本公式。"
            "用户问题中出现的英文指标名、缩写或字段名必须在回答中原样保留，并优先写成「英文名（中文解释）」，便于用户核对指标口径。"
            "如果 formula 为空，必须写「项目脚本中未确认计算公式」，严禁用通用知识或 README 补公式。"
            "严禁把 not_found_in_project_code 的公式或阈值描述为项目专属规则；只有 formula_source=project_code 才能说已由项目脚本验证计算口径。"
            "IgG/Input/Control/Treatment 等样本角色不能套用完全相同阈值；若结论依赖角色推断，需说明依据。"
            "若 workflow_detected_parameters 显示参考基因组、去重、细胞器过滤、peak calling 或 FRiP 计算参数，应纳入原因链路。"
            "如果需要展示数据，优先使用输入中提供的相关数据表，不要转置、重排或凭空补字段。"
            "只展示能够支撑当前结论的数据；与问题无关或没有用于分析的数据不要展示。"
            "非 existing_html_report_summary 模式不要套固定标题模板。根据用户问题自然组织标题和顺序。"
            "无论标题如何变化，回答必须尽量覆盖：直接回应用户问题、关键证据、证据限制、可执行下一步；如果当前问题不需要某一部分，不要为了凑结构强行输出。"
            "如果用户问公式/定义，重点覆盖指标口径、项目证据和核对方式；如果用户问异常/原因，重点覆盖主要怀疑链路、支持证据和优先排查动作；如果用户问画图，重点覆盖推荐图表、使用数据和图表解读。"
            "不要为了凑标题输出空泛小节；只有存在可执行复核动作时才给下一步，不要输出无意义的泛泛建议。"
            "严禁输出代码块、Python、matplotlib、notebook、shell 命令或让用户本地运行代码。"
            "如果用户要画图，说明图表应由系统图表接口生成，不要提供绘图代码。"
        )
        user_prompt = (
            f"用户问题:\n{question}\n\n"
            f"项目数据分析:\n{self.build_analysis_context(analysis_result=analysis_result, experience_summary=experience_summary)}\n\n"
            f"知识库检索结果:\n{self.build_knowledge_context(retrieval_payload) or '未检索到相关知识'}\n\n"
            "请直接输出最终回答；优先回答用户要解决的问题，删掉与当前问题无关的信息。回答要有专业原因链路和可执行复核动作，不要停留在表面描述。不要输出代码，不要输出绘图脚本。用户问题里的英文指标名、缩写或字段名必须原样保留。"
        )
        system_prompt = self._professional_analysis_system_prompt()
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _professional_analysis_system_prompt() -> str:
        return (
            "You are a senior bioinformatics project-analysis assistant. "
            "The project-analysis context is a structured JSON object. "
            "Treat project_observations and relational_tables as project facts only when they are validated. "
            "Treat user_assertions as unverified user-provided values and use them only conditionally, for example: "
            "'if these values come from the project output, then...'. "
            "Use domain_interpretation_rules and retrieved knowledge for mechanism explanation, clearly identifying them as general or reference knowledge rather than project facts. "
            "Bind each important conclusion to the metric value, logical data source or table, interpretation basis, limitation, and verification action. "
            "Do not expose internal file paths, scripts, commands, credentials, or configuration details; cite logical sources such as FRiP matrix, AlignmentQC, ReadsQC, or Spike-in normalization table. "
            "Preserve directional matrix semantics: read_sample evaluated against peak_set is not interchangeable with the reverse direction. "
            "Separate direct observation, associated phenomenon, possible explanation, and verified cause. "
            "Never promote co-occurring abnormal metrics to a verified cause without validating evidence. "
            "Do not report indexed_not_selected as absent project data, and do not merge spike-in unique mapping rate with spike-in scaling factor. "
            "When the user explicitly asks whether data are suitable for downstream analysis, provide a conditional technical assessment based on design readiness and evidence; do not issue a business acceptance decision. "
            "Do not propose differential analysis when biological replicates, conditions, controls, or normalization parameters are unresolved. "
            "Answer simple questions directly. For project diagnosis, synthesize an evidence chain rather than listing isolated metrics. "
            "Use natural Chinese Markdown, avoid fixed headings, avoid repeated boilerplate, and do not output code."
        )

    async def generate_fused_answer(
        self,
        *,
        question: str,
        analysis_result: dict[str, Any],
        retrieval_payload: dict[str, Any],
        experience_summary: dict[str, Any],
    ) -> str:
        direct_answer = self._build_metric_formula_answer(question, analysis_result)
        if direct_answer:
            logger.info(
                "business_response direct_metric_formula question_type=%s output_chars=%d",
                analysis_result.get("question_type"),
                len(direct_answer),
            )
            return direct_answer

        response_plan = (analysis_result.get("analysis_plan") or {}).get("response_plan") or {}
        max_tokens = self._response_max_tokens(analysis_result)
        messages = self._build_fused_answer_messages(
            question=question,
            analysis_result=analysis_result,
            retrieval_payload=retrieval_payload,
            experience_summary=experience_summary,
        )
        prompt_chars = sum(len(item.get("content", "")) for item in messages)
        started_at = perf_counter()
        response = await sub_model_client.chat.completions.create(
            model=SUB_MODEL_NAME,
            messages=messages,
            temperature=0,
            max_tokens=max_tokens,
            stream=False,
        )
        answer = (response.choices[0].message.content or "").strip()
        logger.info(
            "llm_call done purpose=fused_answer_nonstream model=%s prompt_chars=%d output_chars=%d max_tokens=%d reasoning_mode=%s duration_ms=%.2f",
            SUB_MODEL_NAME,
            prompt_chars,
            len(answer),
            max_tokens,
            response_plan.get("reasoning_mode", ""),
            (perf_counter() - started_at) * 1000,
        )
        return answer

    def _response_max_tokens(self, analysis_result: dict[str, Any]) -> int:
        if analysis_result.get("report_mode") == "existing_html_report_summary":
            return self.REPORT_ANSWER_MAX_TOKENS
        response_plan = (analysis_result.get("analysis_plan") or {}).get("response_plan") or {}
        hinted = int(response_plan.get("token_budget_hint") or 0)
        if hinted > 0:
            return hinted
        return self.STANDARD_ANSWER_MAX_TOKENS

    @classmethod
    async def _stream_with_project_deltas(cls, stream) -> tuple[str, list[str]]:
        chunks: list[str] = []
        deltas: list[str] = []
        async for chunk in stream:
            text = str(chunk or "")
            if not text:
                continue
            chunks.append(text)
            deltas.append(text)
            publish_project_answer_delta(text)
        return "".join(chunks).strip(), deltas

    def _build_guard_rewrite_messages(
        self,
        *,
        answer: str,
        guard: dict[str, Any],
        analysis_result: dict[str, Any],
    ) -> list[dict[str, str]]:
        violations = guard.get("violations") or []
        violation_lines = []
        for item in violations[:8]:
            if not isinstance(item, dict):
                continue
            rule = item.get("rule") or "unknown_rule"
            message = item.get("message") or item.get("reason") or ""
            matched = item.get("matched") or item.get("text") or ""
            violation_lines.append(f"- rule={rule}; matched={matched}; reason={message}")
        system_prompt = (
            "你是项目分析回答的重写器。你的任务不是重新分析项目，而是在保留事实、样本、数值、来源字段的前提下，"
            "删除或改写违反规则的表达。"
            "硬性规则："
            "1. 可以指出单项指标偏高、偏低、异常、需关注或需复核，但不判断整体项目合格/不合格、好/坏、通过/失败、是否可继续下游分析。"
            "2. 只回答用户当前问题，不扩展到无关模块或泛泛背景。"
            "3. 未从项目脚本、README、SOP 或报告说明解析到结构化项目阈值时，只报告观测值和证据限制；必须删除「异常高/低、偏高/低、极高/低、超标、不达标」等确定性判断。"
            "4. 不输出代码、绘图脚本、命令、内部路径或工作流内部标记。"
            "5. 保持中文 Markdown，但不要套固定标题模板；根据用户问题自然组织标题和顺序。"
            "6. 保持回答短而聚焦，但必须覆盖直接回应、关键证据、证据限制和可执行下一步，并保留专业原因链路：观测指标 -> 可能上游原因 -> 下游影响 -> 当前证据支持/不支持 -> 如何验证。"
            "7. 不要加入与当前问题无关的历史经验、通用背景或完整 QC 报告。"
            "8. Adapter/raw reads 不等于 clean reads 接头残留；没有处理后证据时必须删除「接头残留」的确定性表述。"
            "9. 细胞器指标必须按项目 species/reference 改写；动物项目不得解释为叶绿体/质体。"
            "10. 证据链已经提供目标指标样本值时，必须保留并引用这些值，删除「指标缺失、未读取、无法给出数值」的错误表述。"
            "只输出重写后的最终回答，不要解释重写过程。"
        )
        user_prompt = (
            "## 用户问题\n"
            f"{analysis_result.get('question', '')}\n\n"
            "## 项目结构化上下文\n"
            f"{self.build_analysis_context(analysis_result=analysis_result, experience_summary={})[:6000]}\n\n"
            "## 违规原因\n"
            f"{chr(10).join(violation_lines) if violation_lines else '原回答未通过项目分析规则校验。'}\n\n"
            "## 原回答\n"
            f"{str(answer or '')[:7000]}\n\n"
            "请输出修正后的最终回答。"
        )
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    async def rewrite_guarded_answer(
        self,
        *,
        answer: str,
        guard: dict[str, Any],
        analysis_result: dict[str, Any],
    ) -> str:
        messages = self._build_guard_rewrite_messages(
            answer=answer,
            guard=guard,
            analysis_result=analysis_result,
        )
        prompt_chars = sum(len(item.get("content", "")) for item in messages)
        started_at = perf_counter()
        response = await sub_model_client.chat.completions.create(
            model=SUB_MODEL_NAME,
            messages=messages,
            temperature=0,
            max_tokens=min(self.STANDARD_ANSWER_MAX_TOKENS, 2200),
            stream=False,
        )
        cleaned = self.clean_final_answer(
            response.choices[0].message.content or "",
            analysis_result=analysis_result,
        )
        logger.info(
            "llm_call done purpose=guard_rewrite model=%s prompt_chars=%d output_chars=%d duration_ms=%.2f",
            SUB_MODEL_NAME,
            prompt_chars,
            len(cleaned),
            (perf_counter() - started_at) * 1000,
        )
        return cleaned

    async def stream_fused_answer(
        self,
        *,
        question: str,
        analysis_result: dict[str, Any],
        retrieval_payload: dict[str, Any],
        experience_summary: dict[str, Any],
    ) -> AsyncIterator[str]:
        direct_answer = self._build_metric_formula_answer(question, analysis_result)
        if direct_answer:
            logger.info(
                "business_response direct_metric_formula_stream question_type=%s output_chars=%d",
                analysis_result.get("question_type"),
                len(direct_answer),
            )
            for chunk in re.split(r"(\n\n)", direct_answer):
                if chunk:
                    yield chunk
            return

        max_tokens = self._response_max_tokens(analysis_result)
        messages = self._build_fused_answer_messages(
            question=question,
            analysis_result=analysis_result,
            retrieval_payload=retrieval_payload,
            experience_summary=experience_summary,
        )
        prompt_chars = sum(len(item.get("content", "")) for item in messages)
        started_at = perf_counter()
        first_token_ms: float | None = None
        output_chars = 0
        stream = await sub_model_client.chat.completions.create(
            model=SUB_MODEL_NAME,
            messages=messages,
            temperature=0,
            max_tokens=max_tokens,
            stream=True,
        )
        try:
            async for event in stream:
                delta = event.choices[0].delta.content if event.choices else None
                if delta:
                    if first_token_ms is None:
                        first_token_ms = (perf_counter() - started_at) * 1000
                        logger.info(
                            "llm_call first_token purpose=fused_answer_stream model=%s prompt_chars=%d max_tokens=%d first_token_ms=%.2f",
                            SUB_MODEL_NAME,
                            prompt_chars,
                            max_tokens,
                            first_token_ms,
                        )
                    output_chars += len(delta)
                    yield delta
        finally:
            logger.info(
                "llm_call done purpose=fused_answer_stream model=%s prompt_chars=%d output_chars=%d max_tokens=%d first_token_ms=%s duration_ms=%.2f",
                SUB_MODEL_NAME,
                prompt_chars,
                output_chars,
                max_tokens,
                f"{first_token_ms:.2f}" if first_token_ms is not None else "",
                (perf_counter() - started_at) * 1000,
            )

    @classmethod
    def _needs_next_action_section(cls, analysis_result: dict[str, Any]) -> bool:
        if analysis_result.get("warnings"):
            return True
        if analysis_result.get("next_actions"):
            return True
        for item in analysis_result.get("evidence_status", []) or []:
            if item.get("status") not in (None, "", "ok"):
                return True
        return False

    @classmethod
    def _strip_next_action_section(cls, text: str) -> str:
        heading = re.escape(cls.NEXT_ACTION_HEADING)
        patterns = [
            rf"\n+\s*#{1,6}\s*{heading}[^\n]*[\s\S]*$",
            rf"\n+\s*{heading}\s*[:：]?[^\n]*[\s\S]*$",
        ]
        cleaned = text.rstrip()
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).rstrip()
        return cleaned

    @staticmethod
    def _looks_incomplete_markdown_tail(text: str) -> bool:
        stripped = (text or "").rstrip()
        if not stripped:
            return False
        if stripped.count("**") % 2 == 1:
            return True

        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if not lines:
            return False
        final_line = lines[-1]
        final_line_is_bullet = bool(re.match(r"^(?:[-*]|\d+[.、])\s+", final_line))
        if not final_line_is_bullet:
            return False
        if len(final_line) < 28:
            return True
        return not bool(re.search(r"[。！？.!?；;）)\]】%]$", final_line))

    @classmethod
    def clean_final_answer(cls, answer: str, *, analysis_result: dict[str, Any]) -> str:
        cleaned = (answer or "").strip()
        if not cleaned:
            return ""
        if cls._looks_like_code_answer(cleaned):
            return cls._build_structured_answer_from_analysis(analysis_result)
        cleaned = re.sub(r"```[\s\S]*?```", "", cleaned).strip()
        evidence_repair = cls._repair_omitted_target_values(cleaned, analysis_result)
        if evidence_repair:
            return evidence_repair
        cleaned = cls._ensure_threshold_limitation(cleaned, analysis_result)
        cleaned = cls._ensure_hypothesis_comparison(cleaned, analysis_result)

        needs_next_actions = cls._needs_next_action_section(analysis_result)
        has_next_action_heading = cls.NEXT_ACTION_HEADING in cleaned

        if not needs_next_actions and has_next_action_heading:
            return cls._strip_next_action_section(cleaned)
        if not needs_next_actions:
            return cleaned

        if has_next_action_heading:
            next_action_tail = cleaned.rsplit(cls.NEXT_ACTION_HEADING, 1)[-1]
            has_list_item = bool(re.search(r"(^|\n)\s*(?:[-*]|\d+[.、])\s+", next_action_tail))
            has_dangling_markdown = cleaned.endswith(("**", "*", "：", ":"))
            too_short_tail = len(next_action_tail.strip()) < 16
            has_incomplete_tail = cls._looks_incomplete_markdown_tail(next_action_tail)
            if not has_list_item or has_dangling_markdown or too_short_tail or has_incomplete_tail:
                cleaned = cls._strip_next_action_section(cleaned)

        next_actions = analysis_result.get("next_actions", []) or []
        if cls.NEXT_ACTION_HEADING not in cleaned and next_actions:
            bullets = "\n".join(f"- {item}" for item in next_actions[:3])
            cleaned = f"{cleaned.rstrip()}\n\n## {cls.NEXT_ACTION_HEADING}\n{bullets}"

        return cleaned.strip()

    @classmethod
    def _ensure_threshold_limitation(cls, text: str, analysis_result: dict[str, Any]) -> str:
        evidence = [
            item
            for item in (analysis_result.get("evidence_chain") or [])
            if isinstance(item, dict)
        ]
        if not any(
            item.get("threshold_needs_project_validation")
            or item.get("severity") == "unverified_threshold"
            for item in evidence
        ):
            return text
        if any(marker in text for marker in ("项目文件中未确认该指标阈值/标准", "项目阈值未确认", "只能报告观测值")):
            return text
        limitation = "项目文件中未确认该指标阈值/标准，本轮只报告观测值、证据来源和排查方向，不据此直接判定高低或异常。"
        if "## 证据边界" in text:
            return text.replace("## 证据边界", f"## 证据边界\n- {limitation}\n", 1)
        return f"{text.rstrip()}\n\n## 证据边界\n- {limitation}".strip()

    @classmethod
    def _ensure_hypothesis_comparison(cls, text: str, analysis_result: dict[str, Any]) -> str:
        response_plan = (analysis_result.get("analysis_plan") or {}).get("response_plan") or {}
        if response_plan.get("reasoning_mode") != "integrative_reasoning":
            return text
        if any(marker in text for marker in ("假设比较", "更支持", "不优先支持")):
            return text
        diagnosis = analysis_result.get("diagnosis_summary") or {}
        comparisons = [str(item).strip() for item in (diagnosis.get("hypothesis_comparison") or []) if str(item).strip()]
        if not comparisons:
            competing = analysis_result.get("competing_hypotheses") or []
            for item in competing[:2]:
                if not isinstance(item, dict):
                    continue
                comparisons.append(
                    f"{item.get('label', '-')}: {item.get('preference_reason', '')}"
                )
        if not comparisons:
            return text
        block = "\n".join(f"- {item}" for item in comparisons[:3])
        return f"{text.rstrip()}\n\n## 假设比较\n{block}".strip()

    @staticmethod
    def _looks_like_code_answer(text: str) -> bool:
        lowered = (text or "").lower()
        code_markers = (
            "```",
            "import matplotlib",
            "matplotlib.pyplot",
            "import numpy",
            "plt.",
            "np.arange",
            "notebook",
            "python 画图代码",
            "绘图代码",
        )
        return any(marker in lowered for marker in code_markers)

    @classmethod
    def _build_structured_answer_from_analysis(cls, analysis_result: dict[str, Any]) -> str:
        diagnosis_summary = analysis_result.get("diagnosis_summary", {}) or {}
        conclusions = diagnosis_summary.get("conclusions", []) or []
        evidence = diagnosis_summary.get("evidence", []) or []
        next_actions = analysis_result.get("next_actions", []) or diagnosis_summary.get("next_actions", []) or []
        warnings = analysis_result.get("warnings", []) or []

        lines = [
            "## 问题相关发现",
            str(conclusions[0]) if conclusions else "当前项目数据已完成读取，本轮未识别到与当前问题直接相关的需复核指标。",
        ]
        if evidence:
            lines.extend(["", "## 依据"])
            lines.extend(f"- {item}" for item in evidence[:5])
        if warnings:
            lines.extend(["", "## 数据限制"])
            lines.extend(f"- {item}" for item in warnings[:3])
        if next_actions or warnings:
            lines.extend(["", f"## {cls.NEXT_ACTION_HEADING}"])
            lines.extend(f"- {item}" for item in next_actions[:3] or ["请补充更明确的指标或样本范围后重试。"])
        return "\n".join(lines).strip()

    def build_fallback_answer(
        self,
        *,
        analysis_result: dict[str, Any],
        retrieval_payload: dict[str, Any],
        experience_summary: dict[str, Any],
    ) -> str:
        validated_claims = [
            item
            for item in analysis_result.get("validated_claims", []) or []
            if isinstance(item, dict)
        ]
        fact_packet = analysis_result.get("fact_packet") or {}
        reasoning_packet = analysis_result.get("reasoning_packet") or {}
        if fact_packet or reasoning_packet:
            rendered_packet_answer = self._render_fact_and_reasoning_packets(analysis_result)
            if rendered_packet_answer:
                return self.clean_final_answer(rendered_packet_answer, analysis_result=analysis_result)
        response_plan = (analysis_result.get("analysis_plan") or {}).get("response_plan") or {}
        if response_plan.get("reasoning_mode") == "integrative_reasoning":
            structured = self._build_grounded_integrative_answer(analysis_result)
            if structured:
                return self.clean_final_answer(structured, analysis_result=analysis_result)
        if validated_claims:
            rendered = claim_service.render_markdown(
                validated_claims=validated_claims,
                evidence_cards=[
                    item
                    for item in analysis_result.get("evidence_cards", []) or []
                    if isinstance(item, dict)
                ],
                target_metrics=self._normalized_target_metrics(
                    analysis_result.get("analysis_plan", {}) or {}
                ),
            )
            if rendered:
                return rendered
        diagnosis_summary = analysis_result.get("diagnosis_summary", {}) or {}
        conclusions = diagnosis_summary.get("conclusions", []) or []
        evidence = diagnosis_summary.get("evidence", []) or []
        possible_causes = diagnosis_summary.get("possible_causes", []) or []
        next_actions = diagnosis_summary.get("next_actions", []) or []
        ranked_causes = diagnosis_summary.get("ranked_causes", []) or []
        diagnostic_confidence = diagnosis_summary.get("diagnostic_confidence", {}) or {}
        evidence_chain = analysis_result.get("evidence_chain", []) or []
        anomaly_summary = analysis_result.get("anomaly_summary", {}) or {}

        lines = [
            "## 处理方向",
            str(conclusions[0] if conclusions else "当前问题未识别到直接相关的需复核指标。"),
        ]
        abnormal_items = list(anomaly_summary.get("critical", []) or []) + list(anomaly_summary.get("warning", []) or [])
        if abnormal_items:
            lines.append("")
            lines.append("## 需复核指标")
            for item in abnormal_items[:4]:
                rule = str(item.get("rule") or "").strip()
                rule_text = f"；项目阈值 {rule}" if rule else "；项目文件中未确认该指标阈值/标准"
                lines.append(
                    f"- {item.get('sample', '-')} {item.get('metric', '')}="
                    f"{item.get('display_value', '-')}{rule_text}"
                    f"；来源 {item.get('source_file', '-')}::{item.get('source_field', '-')}"
                )
        metric_tables = self.build_metric_tables(analysis_result)
        if metric_tables and len(metric_tables) <= 2500:
            lines.append("")
            lines.append("## 关键数据")
            lines.append(metric_tables)
        if evidence:
            lines.append("")
            lines.append("## 关键证据")
            lines.extend(f"- {item}" for item in evidence[:3])
        elif evidence_chain:
            lines.append("")
            lines.append("## 关键证据")
            for item in evidence_chain[:3]:
                lines.append(
                    f"- {item.get('category', '')}/{item.get('metric', '')}: "
                    f"{item.get('sample', '-')}={item.get('display_value', '-')}; "
                    f"来源 {item.get('source_file', '-')}::{item.get('source_field', '-')}; "
                    f"计算 {item.get('formula', '-')}"
                )
        if abnormal_items or possible_causes or ranked_causes:
            lines.append("")
            lines.append("## 根因排序")
            if ranked_causes:
                for cause in ranked_causes[:3]:
                    if not isinstance(cause, dict):
                        continue
                    lines.append(
                        f"- #{cause.get('rank', '-')} {cause.get('label', cause.get('cause_id', '-'))}"
                        f"（{cause.get('support_level', '-')}，评分 {cause.get('score', 0)}）："
                        f"{cause.get('reasoning_summary', '')}"
                    )
                    support = cause.get("supporting_evidence", []) or []
                    if support:
                        item = support[0]
                        lines.append(
                            f"  支持证据：{item.get('sample', '-')} {item.get('metric_key', '-')}="
                            f"{item.get('value', '-')}；{item.get('reason', '')}"
                        )
                    against = cause.get("contradicting_evidence", []) or []
                    if against:
                        item = against[0]
                        lines.append(
                            f"  反证：{item.get('sample', '-')} {item.get('metric_key', '-')}="
                            f"{item.get('value', '-')}；{item.get('reason', '')}"
                        )
                    else:
                        lines.append("  反证：当前未发现可独立排除该假设的项目证据。")
                    actions = cause.get("verification_actions", []) or []
                    if actions:
                        lines.append(f"  验证动作：{actions[0]}")
                if diagnostic_confidence:
                    lines.append(
                        f"- 诊断置信度：{diagnostic_confidence.get('level', 'low')} "
                        f"({diagnostic_confidence.get('score', 0)})；"
                        f"{diagnostic_confidence.get('boundary', '')}"
                    )
            elif possible_causes:
                lines.extend(f"- {item}" for item in possible_causes[:3])
            else:
                lines.append("- 当前证据只支持定位到需复核指标；仍需结合样本角色、流程参数和原始质控结果确认上游原因。")
            if evidence_chain:
                impacted = []
                for item in evidence_chain[:4]:
                    impact = str(item.get("downstream_impact") or "").strip()
                    if impact and impact != "-" and impact not in impacted:
                        impacted.append(impact)
                if impacted:
                    lines.extend(f"- 下游影响：{item}" for item in impacted[:2])
        if next_actions:
            lines.append("")
            lines.append("## 下一步操作")
            lines.extend(f"- {item}" for item in next_actions[:3])
        return self.clean_final_answer("\n".join(lines), analysis_result=analysis_result)

    def _build_grounded_integrative_answer(self, analysis_result: dict[str, Any]) -> str:
        diagnosis_summary = analysis_result.get("diagnosis_summary", {}) or {}
        evidence = diagnosis_summary.get("evidence", []) or []
        ranked_causes = diagnosis_summary.get("ranked_causes", []) or []
        hypothesis_comparison = diagnosis_summary.get("hypothesis_comparison", []) or []
        next_actions = analysis_result.get("next_actions", []) or diagnosis_summary.get("next_actions", []) or []
        next_actions = analysis_result.get("next_actions", []) or diagnosis_summary.get("next_actions", []) or []
        conclusions = diagnosis_summary.get("conclusions", []) or []
        if not evidence and not ranked_causes and not conclusions:
            return ""
        lines = [
            "## 直接结论",
            f"- {conclusions[0] if conclusions else '当前项目只支持基于已验证证据给出有限结论。'}",
        ]
        if evidence:
            lines.extend(["", "## 项目证据"])
            lines.extend(f"- {item}" for item in evidence[:6])
        if hypothesis_comparison:
            lines.extend(["", "## 假设比较"])
            lines.extend(f"- {item}" for item in hypothesis_comparison[:4])
        elif possible_causes:
            lines.extend(["", "## 解释层"])
            lines.append("- 以下内容属于基于项目证据的机制解释，不等同于项目已证实事实。")
            lines.extend(f"- {item}" for item in possible_causes[:4])
        if threshold_statement:
            lines.append(f"- {threshold_statement}")
        if verification_plan:
            lines.extend(["", "## 验证方案"])
            lines.extend(f"- {item}" for item in verification_plan[:3])
        return "\n".join(lines).strip()


business_response_service = BusinessResponseService()
