from __future__ import annotations

import copy
import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.multi_agent.project_progress import publish_project_progress
from multi_agent.backed.app.infrastructure.tools.local.project_reader import (
    find_internal_workflow_files,
    find_files,
    find_log_files,
    list_project_files,
    list_report_roots,
    read_log_snippet,
    read_table_rows,
    read_text_snippet,
    refresh_project_sftp_logs,
    resolve_project_root,
)
from multi_agent.backed.app.services.project_cuttag_diagnostic_service import (
    project_cuttag_diagnostic_service,
)
from multi_agent.backed.app.services.project_expert_tool_service import (
    project_expert_tool_service,
)
from multi_agent.backed.app.services.project_analysis_verifier_service import (
    project_analysis_verifier_service,
)
from multi_agent.backed.app.services.business_agent.claim_service import claim_service
from multi_agent.backed.app.services.business_agent.evidence_card_service import (
    evidence_card_service,
)
from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)
from multi_agent.backed.app.services.business_agent.experiment_design_service import (
    experiment_design_service,
)
from multi_agent.backed.app.services.business_agent.assay_analysis_service import (
    assay_analysis_service,
)
from multi_agent.backed.app.services.business_agent.evidence_catalog_service import (
    evidence_catalog_service,
)
from multi_agent.backed.app.services.business_agent.read_lineage_service import (
    read_lineage_service,
)
from multi_agent.backed.app.services.business_agent.evidence_reasoning_service import (
    evidence_reasoning_service,
)
from multi_agent.backed.app.services.business_agent.project_snapshot_service import (
    project_snapshot_service,
)
from multi_agent.backed.app.services.business_agent.user_assertion_service import (
    user_assertion_service,
)
from multi_agent.backed.app.services.business_agent.bio_skill_reference_service import (
    bio_skill_reference_service,
)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag.lower() in {"br", "p", "div", "section", "article", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag.lower() in {"p", "div", "section", "article", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def text(self) -> str:
        lines = []
        for line in "\n".join(self.parts).splitlines():
            cleaned = " ".join(line.split())
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines)


class ProjectAnalysisService:
    _EVIDENCE_PARSE_WORKERS = 4
    _FILE_PARSE_CACHE: dict[tuple[str, int, str], dict[str, Any]] = {}
    _FILE_PARSE_CACHE_MAX_ENTRIES = 512
    _FILE_PARSE_CACHE_LOCK = threading.Lock()
    _PROJECT_CONTEXT_CACHE_TTL_SECONDS = 120.0
    _PROJECT_CONTEXT_CACHE_MAX_ENTRIES = 128
    _PROJECT_CONTEXT_CACHE: dict[tuple[str, bool], tuple[float, dict[str, Any]]] = {}
    _PROJECT_CONTEXT_IN_FLIGHT: dict[tuple[str, bool], threading.Event] = {}
    _PROJECT_CONTEXT_LOCK = threading.Lock()
    INTERNAL_WORKFLOW_TERMS = (
        "流程",
        "脚本",
        "参数",
        "命令",
        "配置",
        "阈值",       # e.g. "q-value 阈值是多少"
        "qvalue",
        "q-value",
        "q值",
        "callpeak",
        "call peak",
        "snakefile",
        "snakemake",
        "workflow",
        "pipeline",
        "shell",
        "config",
        "script",
        "trimming",
        "macs",
        "bowtie",
    )
    TABLE_PRIORITY = [
        "samplelist",
        "ReadsQC.xls",
        "spikein_align.xls",
        "AlignmentQC.xls",
        "spearman_Corr_readCounts.tab",
        "FRiP_score.xls",
        "Samples_peak_number_stat.xls",
        "FRiP.xls",
        "CUTTag_report.html",
        "report.html",
    ]
    STRUCTURED_TABLE_FILES = {
        "readsqc.xls",
        "spikein_align.xls",
        "alignmentqc.xls",
        "samples_peak_number_stat.xls",
        "spearman_corr_readcounts.tab",
        "frip_score.xls",
        "frip.xls",
    }
    PREFLIGHT_CONFIG_KEYS = {
        "project",
        "project_name",
        "Sequencing",
        "scripts",
        "pipeline",
        "workflow",
        "assay",
        "project_type",
        "species",
        "sample",
        "samplelist",
        "samplist",               # typo variant used in some pipeline configs
        "output",
        "output_dir",
        "raw_fastq_dir",
        "sequencing_mode",
        "seq_length",             # read length, e.g. "150"
        "adapter_type",
        "trimming_tool",
        "remove_duplicates",
        "spikein_analysis",
        "macs3_qvalue",
        "macs2_qvalue",           # macs2 variant
        "TOP_PEAKS_NUM",
        "KNOWN_MOTIF_TOP_N",      # motif analysis top-N parameter
        "organelle_chroms",
        "genome",
        "reference",
        "db_root",                # database root path
        "effective_genome_size",
        "peak_caller",
        "peak_calling",
        "tss_region",             # TSS window, e.g. "-3000,3000"
        "blacklist_bed",          # blacklist filter setting ("", "none", or path)
        "report_lang",            # report language, e.g. "zh" / "en"
    }
    PROFESSIONAL_RULES = {
        "adapter_percent": {
            "label": "Adapter-detected raw-read rate",
            "unit": "%",
            "warning": {"op": ">", "value": 20.0},
            "critical": {"op": ">", "value": 50.0},
            "source_field": "Adapter",
            "definition": "原始 reads 中检测到接头相关序列的 reads 比例。",
            "denominator": "raw reads",
            "assumption": "该指标描述 trimming 前的原始 reads；不能单独证明 clean reads 中仍有接头残留。",
            "interpretation": "需结合 clean FASTQ 的 Adapter Content、clean reads 保留率和 fragment size 判断是否存在处理后残留或短片段 read-through。",
            "downstream_impact": "只有在接头相关序列造成 reads 丢失或 clean reads 仍有残留时，才可能进一步影响比对和富集分析。",
        },
        "q30_ratio": {
            "label": "Q30",
            "unit": "",
            "warning": {"op": "<", "value": 0.93},
            "critical": {"op": "<", "value": 0.90},
            "source_field": "Q30",
            "definition": "碱基质量值不低于 Q30 的比例。",
            "denominator": "total bases",
            "assumption": "通常使用测序质控工具统计的 clean reads 或 raw reads 碱基质量比例。",
            "interpretation": "Q30 偏低提示测序质量下降，需要结合 raw QC 和过滤比例判断。",
            "downstream_impact": "增加错配风险，可能降低比对率和唯一比对率。",
        },
        "mapping_rate_percent": {
            "label": "Mapping rate",
            "unit": "%",
            "warning": {"op": "<", "value": 70.0},
            "critical": {"op": "<", "value": 60.0},
            "source_field": "Mapping(%)",
            "definition": "reads 成功比对到参考基因组的比例。",
            "denominator": "alignment input reads",
            "assumption": "需确认参考基因组版本、比对工具参数和是否包含细胞器染色体。",
            "interpretation": "比对率偏低通常与参考基因组、污染、样本质量或 reads 组成异常相关。",
            "downstream_impact": "降低可用于后续 peak calling、FRiP 和相关性分析的有效 reads。",
        },
        "unique_mapping_rate_percent": {
            "label": "Unique mapping rate",
            "unit": "%",
            "warning": {"op": "<", "value": 30.0},
            "critical": {"op": "<", "value": 20.0},
            "source_field": "Unique(%)",
            "definition": "唯一比对到参考基因组位置的 reads 比例。",
            "denominator": "alignment input reads",
            "assumption": "低唯一比对可能来自重复序列、细胞器 reads、参考基因组不匹配或多重比对策略。",
            "interpretation": "唯一比对率偏低会削弱后续富集、peak 和相关性判断的可靠性。",
            "downstream_impact": "直接影响 peak calling、FRiP、样本相关性和差异分析可信度。",
        },
        "duplicate_rate_percent": {
            "label": "Duplicate rate",
            "unit": "%",
            "warning": {"op": ">", "value": 30.0},
            "critical": {"op": ">", "value": 50.0},
            "source_field": "Duplicate(%)",
            "definition": "被判定为重复的 reads 或 fragments 比例。",
            "denominator": "mapped reads/fragments",
            "assumption": "需结合 library complexity；CUT&Tag 中来自真实富集区域的重复不应被简单等同于失败。",
            "interpretation": "重复率偏高可能来自低复杂度文库，也可能由高比例细胞器 reads 推高。",
            "downstream_impact": "可能降低有效文库复杂度，并影响 peak 强度和定量稳定性。",
        },
        "mt_rate_percent": {
            "label": "Organelle alignment rate",
            "unit": "%",
            "warning": {"op": ">", "value": 10.0},
            "critical": {"op": ">", "value": 30.0},
            "source_field": "chrMT/Pt(%)",
            "definition": "比对到项目所配置细胞器染色体的 reads 比例。",
            "denominator": "mapped reads",
            "assumption": "必须根据 species/reference 和 organelle_chroms 判断该字段代表线粒体、叶绿体/质体或其他细胞器。",
            "interpretation": "细胞器 reads 占比需要结合物种和流程过滤策略解释。",
            "downstream_impact": "显著压缩核基因组有效 reads，造成 Unique、FRiP、peak 和相关性异常。",
        },
        "frip_ratio": {
            "label": "FRiP",
            "unit": "",
            "warning": {"op": "<", "value": 0.20},
            "critical": {"op": "<", "value": 0.10},
            "source_field": "FRiP",
            "definition": "落在 peak 区域内的 reads 占比，用于衡量富集质量。",
            "denominator": "usable mapped reads/fragments",
            "assumption": "IgG/Input/control 的 FRiP 不能和实验样本套用相同解释；口径依赖 peak set 和 usable reads 定义。",
            "interpretation": "FRiP 偏低提示富集效率不足或 peak calling 质量受限。",
            "downstream_impact": "影响 peak 可信度、差异 peak 和 motif/enrichment 分析。",
        },
        "correlation": {
            "label": "Sample correlation",
            "unit": "",
            "warning": {"op": "<", "value": 0.80},
            "critical": {"op": "<", "value": 0.60},
            "source_field": "spearman correlation",
            "definition": "样本间 read count 信号的一致性，通常使用 Spearman 相关系数。",
            "denominator": "genomic bins or peak/read count features",
            "assumption": "应优先比较同组生物学重复；不同处理组或 IgG/control 与实验样本不应强行要求高度相关。",
            "interpretation": "样本相关性偏低提示重复一致性不足或样本间存在系统差异。",
            "downstream_impact": "影响重复合并、差异分析和最终生物学解释可靠性。",
        },
    }

    QUESTION_FILE_HINTS = {
        "qc": ["ReadsQC.xls", "1.ReadsQC.readme.txt"],
        "alignment": ["AlignmentQC.xls", "2.1AlignmentStat.readme.txt"],
        "correlation": ["spearman_Corr_readCounts.tab", "3.Correlation.readme.txt"],
        "peak": ["FRiP_score.xls", "FRiP_raw.txt", "Samples_peak_number_stat.xls", "5.1PeakStat.readme.txt", "5.1PeakStat"],
        "frip": ["FRiP_score.xls", "FRiP.xls", "FRiP_raw.txt", "peakFrip"],
        "spikein": ["spikein_align.xls", "Spikein"],
        "diagnostic": [
            "ReadsQC.xls",
            "AlignmentQC.xls",
            "spikein_align.xls",
            "FRiP.xls",
            "FRiP_score.xls",
            "FRiP_raw.txt",
            "peakFrip",
            "Samples_peak_number_stat.xls",
            "spearman_Corr_readCounts.tab",
        ],
        "diff": [
            "final_anno",
            "GO_up",
            "GO_down",
            "Pathway",
            "DiffPeak.readme.txt",
            "GOList.readme.txt",
            "PathwayList.readme.txt",
            "DiffAnalysis",
            "diff",
            "DEG",
        ],
        "motif": ["knownResults.txt", "homerMotifs.all.motifs", "_meme.txt", "Motify.readme.txt", "Motifyhtml", "MotifyAnalysis", "Motif", "meme"],
        "igv": ["ForIGV", "bigwig", "bw", "bedgraph"],
        "overview": ["CUTTag_report.html", "README.txt", "report.html", "ReadsQC.xls", "AlignmentQC.xls"],
        "log": [".log", "error", "stderr", "stdout", "pipeline", "snakemake", "workflow"],
    }
    TARGET_METRIC_FILE_HINTS = {
        "adapter_percent": ("ReadsQC.xls",),
        "q30_ratio": ("ReadsQC.xls",),
        "mapping_rate_percent": ("AlignmentQC.xls",),
        "unique_mapping_rate_percent": ("AlignmentQC.xls",),
        "duplicate_rate_percent": ("AlignmentQC.xls",),
        "mt_rate_percent": ("AlignmentQC.xls",),
        "chrmt_pt_rate_percent": ("AlignmentQC.xls",),
        "frip": ("FRiP_score.xls", "FRiP.xls", "FRiP_raw.txt"),
        "frip_ratio": ("FRiP_score.xls", "FRiP.xls", "FRiP_raw.txt"),
        "correlation": ("spearman_Corr_readCounts.tab",),
        "peak_count": ("Samples_peak_number_stat.xls",),
    }
    # Terms that indicate the pipeline itself failed — only log files should be read.
    PIPELINE_FAILURE_TERMS = (
        "为什么失败",
        "为什么报错",
        "失败原因",
        "报错原因",
        "分析失败",
        "跑失败",
        "没跑完",
        "没有跑完",
        "没分析完",
        "没有分析完",
        "任务失败",
        "流程报错",
        "流程失败",
        "pipeline失败",
        "pipeline报错",
        "pipeline error",
        "pipeline failed",
        "why failed",
        "why error",
        "job failed",
        "task failed",
        "run failed",
        "什么错误",
        "什么报错",
        "查看报错",
        "看报错",
        "看错误",
        "查看错误",
    )
    DIAGNOSTIC_TERMS = (
        "低",
        "偏低",
        "高",
        "偏高",
        "差",
        "不好",
        "不对",
        "异常",
        "太少",
        "太多",
        "怎么回事",
        "为什么",
        "失败",
        "不理想",
        "有问题",
        "low",
        "lower",
        "high",
        "higher",
        "bad",
        "poor",
        "abnormal",
        "wrong",
        "issue",
        "problem",
        "fail",
        "failed",
        "why",
        "报错",
        "错误",
        "日志",
        "错误日志",
        "log",
        "error",
    )
    SECONDARY_TEXT_HINTS = {
        "diff": ["diff", "deg", "differential"],
        "motif": ["motif", "homer", "meme"],
        "igv": ["igv", "bigwig", "bedgraph", ".bw"],
        "log": [".log", "error", "stderr", "stdout"],
    }

    @classmethod
    def classify_question(cls, question: str) -> str:
        return cls._infer_question_types(question)[0]

    @classmethod
    def _infer_question_types(cls, question: str) -> list[str]:
        normalized = (question or "").lower()
        # Pipeline failure questions are a special early-exit path:
        # ONLY read log files, skip all QC metric analysis entirely.
        # Also match combinations like "失败的原因" (token contains 的 between terms).
        _is_pipeline_failure = any(token in normalized for token in cls.PIPELINE_FAILURE_TERMS)
        if not _is_pipeline_failure:
            # Flexible combination: 失败/报错 + 原因/why → pipeline failure
            _has_failure = any(t in normalized for t in ("失败", "报错", "错误", "fail", "error"))
            _has_reason = any(t in normalized for t in ("原因", "为什么", "why", "reason"))
            if _has_failure and _has_reason:
                _is_pipeline_failure = True
        if _is_pipeline_failure:
            return ["pipeline_failure"]
        tags: list[str] = []

        def add(tag: str) -> None:
            if tag not in tags:
                tags.append(tag)

        if "igg" in normalized and any(
            token in normalized
            for token in ("mt", "mito", "mitochond", "mapping", "duplicate", "线粒体", "叶绿体", "比对", "重复")
        ):
            add("alignment")
        if any(token in normalized for token in ("frip", "reads in peaks", "富集")):
            add("frip")
        if any(token in normalized for token in ("spike", "spikein", "spike-in", "外源")):
            add("spikein")
        if any(
            token in normalized
            for token in (
                "mapping",
                "duplicate",
                "complexity",
                "mt",
                "mito",
                "mitochond",
                "chrmt",
                "比对",
                "重复率",
                "线粒体",
                "叶绿体",
                "复杂度",
            )
        ):
            add("alignment")
        if any(token in normalized for token in ("pca", "corr", "correlation", "相关", "聚类", "一致性")):
            add("correlation")
        if any(token in normalized for token in ("peak", "peaks", "峰", "富集峰")):
            add("peak")
        if any(token in normalized for token in ("diff", "differential", "差异", "上调", "下调", "up-regulated", "down-regulated")):
            add("diff")
        if any(token in normalized for token in ("motif", "homer", "meme", "基序")):
            add("motif")
        if any(token in normalized for token in ("igv", "bigwig", "bedgraph", "轨道")):
            add("igv")
        if any(token in normalized for token in ("q30", "q20", "adapter", "质控", "接头", "测序质量", "clean reads")):
            add("qc")
        if any(
            token in normalized
            for token in (
                "报错",
                "错误日志",
                "日志文件",
                "log文件",
                "error log",
                "stderr",
                "stdout",
                "查看日志",
                "看日志",
                "查看log",
                "看log",
            )
        ):
            add("log")
        if cls._has_diagnostic_signal(normalized):
            add("diagnostic")
        return tags or ["overview"]

    @classmethod
    def _has_diagnostic_signal(cls, normalized_question: str) -> bool:
        return any(token in normalized_question for token in cls.DIAGNOSTIC_TERMS)

    @classmethod
    def _select_evidence_files(
        cls,
        project_root: Path,
        question_types: list[str],
        max_evidence_files: int,
        planning_hints: dict[str, Any] | None = None,
        evidence_catalog: dict[str, Any] | None = None,
    ) -> list[Path]:
        # Pipeline failure questions: ONLY read log files, skip all QC evidence.
        if "pipeline_failure" in question_types:
            return find_log_files(project_root, limit=max_evidence_files)
        ordered: list[Path] = []
        analysis_plan = (planning_hints or {}).get("analysis_plan") or {}
        target_metrics = [
            metric_schema_service.canonical_id(metric)
            for metric in analysis_plan.get("target_metrics", []) or []
            if str(metric or "").strip()
        ]
        expanded_metrics = list(target_metrics)
        if "frip_ratio" in target_metrics:
            expanded_metrics.extend(
                [
                    "sequencing_depth",
                    "mapping_rate_percent",
                    "unique_mapping_rate_percent",
                    "mt_rate_percent",
                    "nrf",
                    "pbc1",
                    "pbc2",
                    "peak_count",
                    "correlation",
                ]
            )
        if any(metric.startswith("spikein_") for metric in target_metrics):
            expanded_metrics.extend(
                [
                    "sequencing_depth",
                    "mapping_rate_percent",
                    "spikein_mapped_reads",
                    "spikein_unique_mapping_rate_percent",
                ]
            )
        target_metrics = list(dict.fromkeys(expanded_metrics))
        if not target_metrics:
            metric_by_question_type = {
                "frip": ["frip_ratio"],
                "alignment": [
                    "mapping_rate_percent",
                    "unique_mapping_rate_percent",
                    "mt_rate_percent",
                    "nrf",
                    "pbc1",
                    "pbc2",
                ],
                "qc": ["sequencing_depth"],
                "correlation": ["correlation"],
                "peak": ["peak_count", "frip_ratio"],
            }
            for question_type in question_types:
                target_metrics.extend(metric_by_question_type.get(question_type, []))
        if evidence_catalog and target_metrics:
            ordered.extend(
                evidence_catalog_service.paths_for_metrics(
                    project_root,
                    evidence_catalog,
                    target_metrics,
                    limit=max(4, min(max_evidence_files, 20)),
                )
            )
        for metric in analysis_plan.get("target_metrics", []) or []:
            normalized_metric = str(metric or "").strip().lower()
            for hint in cls.TARGET_METRIC_FILE_HINTS.get(normalized_metric, ()):
                ordered.extend(find_files(project_root, [hint], limit=2))
        prioritized_hints = [
            str(item).strip()
            for item in (planning_hints or {}).get("prioritized_evidence_hints", [])
            if str(item).strip()
        ]
        for hint in prioritized_hints:
            ordered.extend(find_files(project_root, [hint], limit=2))
        for question_type in question_types:
            for hint in cls.QUESTION_FILE_HINTS.get(question_type, []):
                ordered.extend(find_files(project_root, [hint], limit=3))
        for question_type, hints in cls.SECONDARY_TEXT_HINTS.items():
            if question_type in question_types:
                ordered.extend(find_files(project_root, hints, limit=2))
        if "log" in question_types or "diagnostic" in question_types:
            ordered.extend(find_log_files(project_root, limit=5))
        for name in cls.TABLE_PRIORITY:
            ordered.extend(find_files(project_root, [name], limit=1))

        seen_paths: set[Path] = set()
        unique: list[Path] = []
        for item in ordered:
            resolved = item.resolve()
            if "motif" in question_types and resolved.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg"}:
                continue
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            unique.append(resolved)
            if len(unique) >= max_evidence_files:
                break
        return unique

    @staticmethod
    def _looks_like_text_file(path: Path) -> bool:
        return path.suffix.lower() in {".txt", ".md", ".log", ".html", ".htm", ".bed", ".tsv", ".tab", ".csv", ".xls"}

    @staticmethod
    def _resolve_table_kind(file_path: Path) -> str | None:
        lower_path = str(file_path).lower().replace("/", "\\")
        lower_name = file_path.name.lower()
        if lower_name == "readsqc.xls":
            return "qc"
        if lower_name == "spikein_align.xls":
            return "spikein"
        if lower_name == "alignmentqc.xls":
            return "alignment"
        if lower_name == "samples_peak_number_stat.xls":
            return "peak"
        if lower_name == "frip_raw.txt":
            return "frip"
        if "frip" in lower_name and file_path.suffix.lower() in {".xls", ".csv", ".tab", ".tsv"}:
            return "frip"
        if lower_name == "spearman_corr_readcounts.tab":
            return "correlation"
        if "final_anno" in lower_name:
            return "diff_annotation"
        if "go_up" in lower_name or "go_down" in lower_name:
            return "diff_go"
        if "pathway" in lower_name and file_path.suffix.lower() in {".xls", ".csv", ".tab", ".tsv"}:
            return "diff_pathway"
        if "diff" in lower_path and file_path.suffix.lower() in {".xls", ".csv", ".tab", ".tsv"}:
            return "diff_table"
        return None

    @staticmethod
    def _clean_matrix_token(value: str) -> str:
        return (value or "").strip().strip("'").strip('"')

    @classmethod
    def _read_correlation_rows(cls, file_path: Path) -> list[dict[str, str]]:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        lines = [line.rstrip("\n\r") for line in text.splitlines() if line.strip()]
        data_lines = [line for line in lines if not line.startswith("#")]
        if len(data_lines) < 2:
            raise ValueError(f"Correlation matrix has insufficient data: {file_path}")

        header_parts = [cls._clean_matrix_token(part) for part in data_lines[0].split("\t")]
        sample_names = [part for part in header_parts[1:] if part]
        rows: list[dict[str, str]] = []

        for line in data_lines[1:]:
            parts = [cls._clean_matrix_token(part) for part in line.split("\t")]
            if len(parts) < 2:
                continue
            sample = parts[0]
            values = parts[1:]
            row = {"Sample": sample}
            for index, other in enumerate(sample_names):
                row[other] = values[index] if index < len(values) else ""
            rows.append(row)
        return rows

    @staticmethod
    def _parse_numeric(value: str | None) -> float | None:
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None
        for token in ("(", "%"):
            if token in raw:
                raw = raw.split(token, 1)[0].strip()
        raw = raw.replace(",", "")
        try:
            return float(raw)
        except ValueError:
            return None

    @staticmethod
    def _first_nonempty(row: dict[str, str], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return value
        return ""

    @staticmethod
    def _first_nonempty_with_key(row: dict[str, str], *keys: str) -> tuple[str, str]:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return str(value), key
        return "", ""

    @staticmethod
    def _parse_embedded_percent(value: str | None) -> float | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        if "(" in raw and "%)" in raw:
            raw = raw.rsplit("(", 1)[1].split("%", 1)[0].strip()
        elif "%" in raw:
            raw = raw.split("%", 1)[0].strip()
        else:
            return None
        raw = raw.replace(",", "")
        try:
            return float(raw)
        except ValueError:
            return None

    @classmethod
    def _normalize_ratio(
        cls,
        value: str | None,
        metric_id: str = "",
        source_field: str = "",
        *,
        numerator: Any = None,
        denominator: Any = None,
    ) -> float | None:
        if metric_id:
            return metric_schema_service.normalize(
                metric_id,
                value,
                source_field=source_field,
                numerator=numerator,
                denominator=denominator,
            ).get("value")
        embedded_percent = cls._parse_embedded_percent(value)
        if embedded_percent is not None:
            return embedded_percent / 100.0
        parsed = cls._parse_numeric(value)
        if parsed is None:
            return None
        if parsed > 1:
            return parsed / 100.0
        return parsed

    @classmethod
    def _normalize_percent(
        cls,
        value: str | None,
        metric_id: str = "",
        source_field: str = "",
        *,
        numerator: Any = None,
        denominator: Any = None,
    ) -> float | None:
        if metric_id:
            return metric_schema_service.normalize(
                metric_id,
                value,
                source_field=source_field,
                numerator=numerator,
                denominator=denominator,
            ).get("value")
        embedded_percent = cls._parse_embedded_percent(value)
        if embedded_percent is not None:
            return embedded_percent
        parsed = cls._parse_numeric(value)
        if parsed is None:
            return None
        if parsed <= 1:
            return parsed * 100.0
        return parsed

    @staticmethod
    def _raw_table_value(row: dict[str, str], *keys: str) -> str:
        return ProjectAnalysisService._first_nonempty(row, *keys)

    @classmethod
    def _build_qc_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
        metrics = []
        for row in rows:
            sample = row.get("Sample", "")
            clean_reads_raw = cls._first_nonempty(row, "Clean Reads", "Total Clean Reads")
            clean_bases_raw = cls._first_nonempty(row, "Clean Bases", "Total Clean Bases")
            adapter_raw = row.get("Adapter", "")
            adapter = cls._normalize_percent(adapter_raw, "adapter_percent", "Adapter")
            q20 = cls._normalize_ratio(row.get("Q20", ""), "q20_ratio", "Q20")
            q30 = cls._normalize_ratio(row.get("Q30", ""), "q30_ratio", "Q30")
            clean_retention = cls._parse_embedded_percent(clean_reads_raw)
            clean_base_retention = cls._parse_embedded_percent(clean_bases_raw)
            metrics.append(
                {
                    "sample": sample,
                    "raw_reads": cls._first_nonempty(row, "Raw Reads", "Total Raw Reads"),
                    "raw_read_count": cls._parse_numeric(
                        cls._first_nonempty(row, "Raw Reads", "Total Raw Reads")
                    ),
                    "clean_reads": clean_reads_raw,
                    "clean_read_count": cls._parse_numeric(clean_reads_raw),
                    "clean_read_retention_percent": clean_retention,
                    "clean_bases": clean_bases_raw,
                    "clean_base_retention_percent": clean_base_retention,
                    "adapter": adapter_raw,
                    "adapter_reads": cls._parse_numeric(adapter_raw),
                    "adapter_percent": adapter,
                    "q20_ratio": q20,
                    "q30_ratio": q30,
                }
            )
        return {"metrics": metrics, "findings": []}

    @classmethod
    def _build_alignment_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
        metrics = []
        for row in rows:
            sample = cls._first_nonempty(row, "Sample", "Sample_ID", "SampleID", "Sample Name", "样本")
            mapping_raw, mapping_field = cls._first_nonempty_with_key(
                row, "Mapping rate", "Mapping_Rate", "Mapping(%)", "Mapping", "Mapping%"
            )
            unique_raw, unique_field = cls._first_nonempty_with_key(
                row,
                "Unique mapping rate",
                "Unique_Mapped_Rate",
                "Unique(%)",
                "Unique",
                "Unique%",
            )
            duplicate_raw, duplicate_field = cls._first_nonempty_with_key(
                row,
                "Duplicate rate",
                "Picard_Duplication_Rate",
                "PERCENT_DUPLICATION",
                "Duplicate(%)",
                "Duplicate",
                "Duplicate%",
            )
            mt_raw, mt_field = cls._first_nonempty_with_key(
                row, "chrMT/Pt rate", "MT_Ratio", "chrMT/Pt(%)", "chrMT/Pt", "chrMT/Pt%"
            )
            duplicate_metric = (
                "picard_duplicate_pair_rate_percent"
                if duplicate_field == "PERCENT_DUPLICATION"
                else "duplicate_rate_percent"
            )
            mapping = cls._normalize_percent(
                mapping_raw, "mapping_rate_percent", mapping_field
            )
            unique = cls._normalize_percent(
                unique_raw, "unique_mapping_rate_percent", unique_field
            )
            duplicate = cls._normalize_percent(
                duplicate_raw, duplicate_metric, duplicate_field
            )
            mt_rate = cls._normalize_percent(mt_raw, "mt_rate_percent", mt_field)
            nrf = cls._normalize_ratio(row.get("NRF", ""), "nrf", "NRF")
            pbc1 = cls._normalize_ratio(row.get("PBC1", ""), "pbc1", "PBC1")
            pbc2 = metric_schema_service.normalize(
                "pbc2", row.get("PBC2", ""), source_field="PBC2"
            ).get("value")
            metrics.append(
                {
                    "sample": sample,
                    "host_alignment_input_reads": cls._parse_numeric(
                        cls._first_nonempty(
                            row,
                            "Total_Reads",
                            "Total Reads",
                            "Alignment input reads",
                        )
                    ),
                    "total_mapped_reads": cls._parse_numeric(
                        cls._first_nonempty(
                            row,
                            "Total_Mapped_Reads",
                            "Total Mapped Reads",
                            "Mapped reads",
                        )
                    ),
                    "unique_mapped_reads": cls._parse_numeric(
                        cls._first_nonempty(
                            row,
                            "Unique_Mapped_Reads",
                            "Unique Mapped Reads",
                            "Unique mapping reads",
                        )
                    ),
                    "mt_mapped_reads": cls._parse_numeric(
                        cls._first_nonempty(
                            row,
                            "MT_Mapped_Reads",
                            "MT Mapped Reads",
                            "chrMT/Pt mapped reads",
                        )
                    ),
                    "mapping_rate_percent": mapping,
                    "unique_mapping_rate_percent": unique,
                    "duplicate_rate_percent": duplicate,
                    "mt_rate_percent": mt_rate,
                    "complexity": cls._first_nonempty(
                        row, "Est.Lib.Complexity", "Estimated_Library_Size", "Complexity"
                    ),
                    "nrf": nrf,
                    "pbc1": pbc1,
                    "pbc2": pbc2,
                    "source_fields": {
                        "mapping_rate_percent": mapping_field,
                        "unique_mapping_rate_percent": unique_field,
                        "duplicate_rate_percent": duplicate_field,
                        "mt_rate_percent": mt_field,
                        "nrf": "NRF",
                        "pbc1": "PBC1",
                        "pbc2": "PBC2",
                    },
                }
            )
        return {"metrics": metrics, "findings": []}

    @classmethod
    def _build_spikein_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
        metrics = []
        for row in rows:
            sample = row.get("Sample", "")
            unique_raw, unique_field = cls._first_nonempty_with_key(
                row,
                "Unique mapping rate(%)",
                "Unique mapping rate",
                "Unique(%)",
            )
            unique_rate = cls._normalize_percent(
                unique_raw,
                "spikein_unique_mapping_rate_percent",
                unique_field,
            )
            metrics.append(
                {
                    "sample": sample,
                    "spikein_alignment_input_reads": cls._parse_numeric(
                        cls._first_nonempty(row, "Clean reads", "Clean Reads")
                    ),
                    "mapped_reads": cls._parse_numeric(row.get("Mapped reads", "")),
                    "unique_mapped_reads": cls._parse_numeric(
                        cls._first_nonempty(
                            row,
                            "Unique mapping reads",
                            "Unique mapped reads",
                        )
                    ),
                    "unique_mapping_rate_percent": unique_rate,
                    "scaling_factor": metric_schema_service.normalize(
                        "spikein_scaling_factor",
                        cls._first_nonempty(
                            row,
                            "Scaling factor",
                            "Scale factor",
                            "Normalization factor",
                        ),
                        source_field="Scaling factor",
                    ).get("value"),
                }
            )
        return {"metrics": metrics, "findings": []}

    @classmethod
    def _build_frip_summary(
        cls,
        rows: list[dict[str, str]],
        source_name: str = "",
    ) -> dict[str, Any]:
        metrics = []
        for row in rows:
            sample = row.get("Sample", "") or row.get("sample", "") or row.get("file", "")
            frip_value = None
            frip_field = ""
            reads_in_peaks = (
                row.get("Reads_in_Peaks", "")
                or row.get("Reads in peaks", "")
                or row.get("featureReadCount", "")
            )
            mapped_reads = (
                row.get("Mapped_Reads", "")
                or row.get("Mapped reads", "")
                or row.get("totalReadCount", "")
            )
            for key in (
                "FRiP",
                "Frip",
                "frip",
                "Reads in peaks ratio",
                "ReadsInPeaksRatio",
                "percent",
            ):
                raw_value = row.get(key)
                source_scale = ""
                if (
                    key.lower() == "percent"
                    or source_name.lower() in {"frip_raw.txt", "frip_score.xls"}
                ):
                    source_scale = "percent"
                normalized = metric_schema_service.normalize(
                    "frip_ratio",
                    raw_value,
                    source_field=key,
                    source_scale=source_scale,
                    numerator=reads_in_peaks,
                    denominator=mapped_reads,
                )
                frip_value = normalized.get("value") if normalized.get("valid") else None
                if frip_value is not None:
                    frip_field = key
                    break
            peak_set = cls._first_nonempty(
                row,
                "PeakSet",
                "Peak Set",
                "featureType",
                "Feature",
                "Reference sample",
            )
            metrics.append(
                {
                    "sample": sample,
                    "frip_ratio": frip_value,
                    "peak_count": row.get("PeakCount", "") or row.get("Peaks_number", ""),
                    "reads_in_peaks": reads_in_peaks,
                    "mapped_reads": mapped_reads,
                    "peak_set": peak_set,
                    "comparison_type": (
                        "cross_frip" if peak_set and peak_set != sample else "self_frip"
                    ),
                    "source_field": frip_field,
                }
            )
        return {"metrics": metrics, "findings": []}

    @staticmethod
    def _merge_frip_metrics(
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        order: list[tuple[str, str]] = []
        for item in [*existing, *incoming]:
            if not isinstance(item, dict):
                continue
            sample = str(item.get("sample") or "")
            peak_set = str(item.get("peak_set") or sample)
            key = (sample, peak_set)
            if key not in merged:
                merged[key] = item
                order.append(key)
                continue
            current = merged[key]
            current_has_counts = bool(
                current.get("reads_in_peaks") and current.get("mapped_reads")
            )
            incoming_has_counts = bool(
                item.get("reads_in_peaks") and item.get("mapped_reads")
            )
            if incoming_has_counts and not current_has_counts:
                merged[key] = item
        return [merged[key] for key in order]

    @staticmethod
    def _build_peak_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
        counts = {
            row.get("Sample", ""): int(float(row.get("Peaks_number", "0") or 0))
            for row in rows
            if row.get("Sample")
        }
        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        findings = [f"{ranked[-1][0]} peak 数量最低({ranked[-1][1]})"] if ranked else []
        return {"metrics": counts, "ranked": ranked, "findings": findings}

    @classmethod
    def _build_correlation_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
        max_pair = None
        min_pair = None
        pairs: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for row in rows:
            sample = row.get("Sample", "")
            for other, value in row.items():
                if other == "Sample" or not other or sample == other:
                    continue
                pair_key = tuple(sorted((sample, other)))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                num = cls._normalize_ratio(value, "correlation", other)
                if num is None:
                    continue
                pair = (sample, other, num)
                pairs.append({"left": sample, "right": other, "value": num})
                if max_pair is None or num > max_pair[2]:
                    max_pair = pair
                if min_pair is None or num < min_pair[2]:
                    min_pair = pair
        findings = [f"最低相关性为 {min_pair[0]} vs {min_pair[1]} ({min_pair[2]:.4f})"] if min_pair else []
        return {
            "max_pair": max_pair,
            "min_pair": min_pair,
            "pairs": pairs,
            "strata": {},
            "findings": [],
        }

    @classmethod
    def _stratify_correlation_summary(
        cls,
        summary: dict[str, Any],
        experiment_design: dict[str, Any] | None,
    ) -> dict[str, Any]:
        enriched = copy.deepcopy(summary)
        strata: dict[str, list[dict[str, Any]]] = {}
        for pair in enriched.get("pairs", []) or []:
            relation = experiment_design_service.classify_pair(
                str(pair.get("left") or ""),
                str(pair.get("right") or ""),
                experiment_design,
            )
            pair["pair_type"] = relation
            strata.setdefault(relation, []).append(pair)
        enriched["strata"] = {
            relation: {
                "pair_count": len(items),
                "min_pair": min(items, key=lambda item: item["value"]) if items else None,
                "max_pair": max(items, key=lambda item: item["value"]) if items else None,
                "pairs": items,
            }
            for relation, items in strata.items()
        }
        replicate_pairs = strata.get("biological_replicates", [])
        enriched["replicate_min_pair"] = (
            min(replicate_pairs, key=lambda item: item["value"])
            if replicate_pairs
            else None
        )
        enriched["findings"] = []
        return enriched

    @classmethod
    def _build_diff_annotation_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
        change_counts = {"up": 0, "down": 0, "not": 0, "other": 0}
        top_genes: list[dict[str, str]] = []
        for row in rows:
            change = (row.get("change", "") or "").strip().lower()
            if change in change_counts:
                change_counts[change] += 1
            else:
                change_counts["other"] += 1
            symbol = row.get("SYMBOL", "") or row.get("GENENAME", "") or row.get("geneId", "")
            if symbol and len(top_genes) < 100 and change in {"up", "down"}:
                top_genes.append(
                    {
                        "symbol": symbol,
                        "change": change,
                        "annotation": row.get("annotation", ""),
                        "distance_to_tss": row.get("distanceToTSS", ""),
                    }
                )
        findings: list[str] = []
        if change_counts["up"] or change_counts["down"]:
            findings.append(
                f"差异峰统计: up={change_counts['up']}, down={change_counts['down']}, not={change_counts['not']}"
            )
        return {
            "change_counts": change_counts,
            "top_genes": top_genes,
            "findings": findings,
        }

    @classmethod
    def _build_enrichment_summary(cls, rows: list[dict[str, str]], enrichment_type: str) -> dict[str, Any]:
        top_terms: list[dict[str, Any]] = []
        for row in rows[:100]:
            description = row.get("Description", "") or row.get("Term", "") or row.get("ID", "")
            if not description:
                continue
            top_terms.append(
                {
                    "description": description,
                    "ontology": row.get("ONTOLOGY", "") or row.get("Category", ""),
                    "gene_ratio": row.get("GeneRatio", ""),
                    "p_adjust": row.get("p.adjust", "") or row.get("padj", "") or row.get("qvalue", ""),
                }
            )
        findings: list[str] = []
        if top_terms:
            findings.append(f"{enrichment_type} 富集 top term: {top_terms[0]['description']}")
        return {
            "enrichment_type": enrichment_type,
            "top_terms": top_terms,
            "findings": findings,
        }

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(str(value))
        except ValueError:
            return None

    @staticmethod
    def _extract_motif_sample_name(file_path: Path) -> str:
        lower_name = file_path.name.lower()
        if lower_name.endswith("_meme.txt"):
            return file_path.name[:-9]
        if lower_name.endswith("_meme.html"):
            return file_path.name[:-10]
        if "_logo" in lower_name:
            return file_path.name.split("_logo", 1)[0]
        return file_path.stem

    @classmethod
    def _cache_key(cls, file_path: Path, parser_kind: str) -> tuple[str, int, str]:
        stat = file_path.stat()
        return (str(file_path.resolve()), stat.st_mtime_ns, parser_kind)

    @staticmethod
    def _clone_payload(payload: dict[str, Any]) -> dict[str, Any]:
        if not payload:
            return {}
        cloned: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                cloned[key] = dict(value)
            elif isinstance(value, list):
                cloned[key] = [dict(item) if isinstance(item, dict) else item for item in value]
            else:
                cloned[key] = value
        return cloned

    @classmethod
    def _get_cached_parse(cls, file_path: Path, parser_kind: str) -> dict[str, Any] | None:
        with cls._FILE_PARSE_CACHE_LOCK:
            cached = cls._FILE_PARSE_CACHE.get(cls._cache_key(file_path, parser_kind))
        if cached is None:
            return None
        return cls._clone_payload(cached)

    @classmethod
    def _set_cached_parse(cls, file_path: Path, parser_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        cloned = cls._clone_payload(payload)
        cache_key = cls._cache_key(file_path, parser_kind)
        resolved_path = cache_key[0]
        with cls._FILE_PARSE_CACHE_LOCK:
            stale_keys = [
                key
                for key in cls._FILE_PARSE_CACHE
                if key[0] == resolved_path and key[2] == parser_kind and key != cache_key
            ]
            for key in stale_keys:
                cls._FILE_PARSE_CACHE.pop(key, None)
            cls._FILE_PARSE_CACHE[cache_key] = cloned
            while len(cls._FILE_PARSE_CACHE) > cls._FILE_PARSE_CACHE_MAX_ENTRIES:
                cls._FILE_PARSE_CACHE.pop(next(iter(cls._FILE_PARSE_CACHE)))
        return cls._clone_payload(cloned)

    @classmethod
    def _project_context_key(cls, root: Path, include_html_body: bool) -> tuple[str, bool]:
        return (str(root.resolve()), include_html_body)

    @staticmethod
    def _clone_project_context(payload: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(payload)

    @classmethod
    def _get_cached_project_context(cls, root: Path, include_html_body: bool) -> dict[str, Any] | None:
        key = cls._project_context_key(root, include_html_body)
        now = perf_counter()
        with cls._PROJECT_CONTEXT_LOCK:
            cached = cls._PROJECT_CONTEXT_CACHE.get(key)
            if cached is None:
                return None
            cached_at, payload = cached
            if now - cached_at > cls._PROJECT_CONTEXT_CACHE_TTL_SECONDS:
                cls._PROJECT_CONTEXT_CACHE.pop(key, None)
                return None
            return cls._clone_project_context(payload)

    @classmethod
    def _set_cached_project_context(
        cls,
        root: Path,
        include_html_body: bool,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        cloned = cls._clone_project_context(payload)
        with cls._PROJECT_CONTEXT_LOCK:
            now = perf_counter()
            expired = [
                key
                for key, (cached_at, _) in cls._PROJECT_CONTEXT_CACHE.items()
                if now - cached_at > cls._PROJECT_CONTEXT_CACHE_TTL_SECONDS
            ]
            for key in expired:
                cls._PROJECT_CONTEXT_CACHE.pop(key, None)
            cls._PROJECT_CONTEXT_CACHE[cls._project_context_key(root, include_html_body)] = (now, cloned)
            while len(cls._PROJECT_CONTEXT_CACHE) > cls._PROJECT_CONTEXT_CACHE_MAX_ENTRIES:
                cls._PROJECT_CONTEXT_CACHE.pop(next(iter(cls._PROJECT_CONTEXT_CACHE)))
        return cls._clone_project_context(cloned)

    @classmethod
    def _build_cached_project_context(cls, root: Path, include_html_body: bool) -> dict[str, Any]:
        key = cls._project_context_key(root, include_html_body)
        context_snapshot_key = project_snapshot_service.build_context_snapshot_key(
            root=str(root.resolve()),
            include_html_body=include_html_body,
        )
        while True:
            cached = cls._get_cached_project_context(root, include_html_body)
            if cached is not None:
                logger.info(
                    "project_analysis stage=build_context_cache root=%s include_html_body=%s status=hit",
                    str(root),
                    include_html_body,
                )
                return cached

            with cls._PROJECT_CONTEXT_LOCK:
                event = cls._PROJECT_CONTEXT_IN_FLIGHT.get(key)
                if event is None:
                    event = threading.Event()
                    cls._PROJECT_CONTEXT_IN_FLIGHT[key] = event
                    owner = True
                else:
                    owner = False

            if owner:
                try:
                    snapshot_payload = project_snapshot_service.get(context_snapshot_key)
                    if snapshot_payload is not None:
                        logger.info(
                            "project_analysis stage=context_snapshot root=%s include_html_body=%s snapshot=hit",
                            str(root),
                            include_html_body,
                        )
                        payload = snapshot_payload.get("project_context", {}) or {}
                    else:
                        logger.info(
                            "project_analysis stage=context_snapshot root=%s include_html_body=%s snapshot=miss",
                            str(root),
                            include_html_body,
                        )
                        payload = cls._build_project_context(root, include_html_body=include_html_body)
                        project_snapshot_service.set(
                            context_snapshot_key,
                            {
                                "project_context": payload,
                                "experiment_design": payload.get("experiment_design", {}),
                                "evidence_catalog_summary": payload.get("evidence_catalog_summary", {}),
                            },
                        )
                    return cls._set_cached_project_context(root, include_html_body, payload)
                finally:
                    with cls._PROJECT_CONTEXT_LOCK:
                        cls._PROJECT_CONTEXT_IN_FLIGHT.pop(key, None)
                        event.set()

            logger.info(
                "project_analysis stage=build_context_cache root=%s include_html_body=%s status=wait_in_flight",
                str(root),
                include_html_body,
            )
            event.wait(timeout=30.0)

    @staticmethod
    def _relative_path(root: Path, path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    @classmethod
    def _parse_samplelist(cls, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        text = path.read_text(encoding="utf-8", errors="ignore")
        content_lines = [
            raw_line.strip()
            for raw_line in text.splitlines()
            if raw_line.strip() and not raw_line.strip().startswith("#")
        ]
        if not content_lines:
            return rows
        first_parts = [part.strip() for part in content_lines[0].split("\t")]
        if len(first_parts) < 2:
            first_parts = content_lines[0].split()
        normalized_headers = [re.sub(r"[^a-z0-9]+", "_", item.lower()).strip("_") for item in first_parts]
        known_headers = {
            "sample",
            "sample_id",
            "fastq_1",
            "fastq1",
            "condition",
            "replicate",
            "target",
            "role",
            "control_for",
            "batch",
        }
        has_header = bool(set(normalized_headers) & known_headers)
        data_lines = content_lines[1:] if has_header else content_lines
        for raw_line in data_lines:
            line = raw_line.strip()
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) < 2:
                parts = line.split()
            if not parts:
                continue
            mapped = {
                normalized_headers[index]: value
                for index, value in enumerate(parts)
                if has_header and index < len(normalized_headers)
            }
            sample = mapped.get("sample") or mapped.get("sample_id") or parts[0]
            design_fields = {
                key: mapped.get(key, "")
                for key in ("condition", "replicate", "target", "role", "control_for", "batch")
                if mapped.get(key, "") not in (None, "")
            }
            row = {
                "sample": sample,
                "fastq_1": mapped.get("fastq_1") or mapped.get("fastq1") or (parts[1] if len(parts) > 1 else ""),
                "fastq_2": mapped.get("fastq_2") or mapped.get("fastq2") or (parts[2] if len(parts) > 2 else ""),
                "peak_type": mapped.get("peak_type") or (parts[3] if len(parts) > 3 else ""),
                "control_sample": mapped.get("control") or (
                    parts[4] if not has_header and len(parts) > 4 else ""
                ),
                "design_fields": design_fields,
                "raw_fields": parts,
            }
            rows.append(row)
        return rows

    # Nested YAML sections to flatten one level deep as "section.subkey"
    _CONFIG_NESTED_SECTIONS = {"deeptools_params", "threads"}
    # Top-level sections / keys to skip entirely (binary paths or annotation DB
    # paths that are irrelevant to QC analysis and only add noise).
    _CONFIG_SKIP_SECTIONS = {"software"}
    _CONFIG_SKIP_KEYS = {
        "genome_size_file",           # internal genome sizes file path
        "peak_go_term2gene_relpath",  # GO annotation DB relative path
        "go_name",                    # GO term name DB path
        "kegg_name",                  # KEGG pathway name DB path
    }

    @classmethod
    def _extract_yaml_config_fields(cls, raw: dict) -> dict[str, str]:
        """Walk a parsed YAML dict and extract ALL pipeline parameters.

        Strategy (blocklist instead of whitelist):
        - Skip _CONFIG_SKIP_SECTIONS (e.g. "software" — just tool binary paths).
        - Flatten _CONFIG_NESTED_SECTIONS one level deep: deeptools_params.body_len, etc.
        - Flatten adapter_sets two levels deep: adapter_sets.tn5.PE, adapter_sets.illumina.SE, etc.
        - For any other nested dict, emit a compact "k=v; k=v" summary string.
        - All scalars and lists are included unconditionally.
        """
        result: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                continue
            if key in cls._CONFIG_SKIP_SECTIONS or key in cls._CONFIG_SKIP_KEYS:
                continue
            if isinstance(value, dict):
                if key in cls._CONFIG_NESTED_SECTIONS:
                    # Flatten one level: deeptools_params.body_len = "3000"
                    for sub_key, sub_val in value.items():
                        if isinstance(sub_val, (str, int, float, bool)):
                            result[f"{key}.{sub_key}"] = str(sub_val)
                elif key == "adapter_sets":
                    # Flatten two levels: adapter_sets.tn5.PE = "-a CTGTCTCT..."
                    for adapter_type, tool_dict in value.items():
                        if not isinstance(tool_dict, dict):
                            continue
                        for tool, mode_dict in tool_dict.items():
                            if not isinstance(mode_dict, dict):
                                continue
                            for mode, cmd in mode_dict.items():
                                result[f"adapter_sets.{adapter_type}.{mode}"] = str(cmd)[:240]
                else:
                    # Generic nested dict → compact summary
                    pairs = "; ".join(
                        f"{k}={v}"
                        for k, v in value.items()
                        if isinstance(v, (str, int, float, bool))
                    )
                    if pairs:
                        result[key] = pairs[:300]
            elif isinstance(value, list):
                result[key] = ", ".join(str(item) for item in value)[:200]
            else:
                str_val = "" if value is None else str(value)
                result[key] = str_val
                # Normalise "samplist" typo → "samplelist"
                if key == "samplist" and "samplelist" not in result:
                    result["samplelist"] = str_val
        return result

    @classmethod
    def _parse_config_summary(cls, path: Path) -> dict[str, str]:
        text = path.read_text(encoding="utf-8", errors="ignore")

        # Primary: proper YAML parsing handles nested structures and quoted values
        try:
            import yaml  # PyYAML — available as a transitive dependency
            raw = yaml.safe_load(text)
            if isinstance(raw, dict):
                result = cls._extract_yaml_config_fields(raw)
                logger.info("config_parse path=%s parser=yaml keys=%d", path, len(result))
                return result
            logger.warning("config_parse path=%s parser=yaml result_not_dict type=%s", path, type(raw))
        except ImportError:
            logger.warning("config_parse path=%s parser=yaml_unavailable fallback=line_by_line", path)
        except Exception as exc:
            logger.warning("config_parse path=%s parser=yaml_failed error=%s fallback=line_by_line", path, exc)

        # Fallback: line-by-line flat parser for non-standard YAML files
        summary: dict[str, str] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().strip("'\"")
            if key not in cls.PREFLIGHT_CONFIG_KEYS and key != "samplist":
                continue
            cleaned_value = value.strip()
            if cleaned_value.startswith(("'", '"')):
                quote_char = cleaned_value[0]
                end_index = cleaned_value.find(quote_char, 1)
                if end_index > 0:
                    cleaned_value = cleaned_value[: end_index + 1]
            elif "#" in cleaned_value:
                cleaned_value = cleaned_value.split("#", 1)[0].strip()
            cleaned_value = cleaned_value.strip().strip("'\"")
            norm_key = "samplelist" if key == "samplist" else key
            summary[norm_key] = cleaned_value
        return summary

    @staticmethod
    def _infer_sample_role(sample_name: str, raw_fields: list[str] | None = None) -> str:
        tokens = " ".join([sample_name or "", *[str(item) for item in (raw_fields or [])]]).lower()
        if any(token in tokens for token in ("igg", "isotype", "negative")):
            return "IgG/control"
        if any(token in tokens for token in ("input", "inputdna", "input_dna")):
            return "Input"
        if any(token in tokens for token in ("control", "ctrl", "mock", "vehicle")):
            return "Control"
        if any(token in tokens for token in ("treat", "case", "ko", "oe", "stim")):
            return "Treatment"
        return "Experimental"

    @classmethod
    def _build_sample_roles(cls, samples: list[dict[str, Any]]) -> list[dict[str, str]]:
        roles: list[dict[str, str]] = []
        for item in samples:
            sample = str(item.get("sample") or "").strip()
            if not sample:
                continue
            roles.append(
                {
                    "sample": sample,
                    "role": cls._infer_sample_role(sample, item.get("raw_fields") or []),
                    "basis": "samplelist/name heuristic",
                }
            )
        return roles

    @classmethod
    def _build_workflow_summary(
        cls,
        root: Path,
        limit: int = 10,
        project_config: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        files: list[dict[str, str]] = []
        detected_parameters: dict[str, str] = {}
        code_formula_sources: dict[str, dict[str, str]] = {}
        keyword_patterns = {
            "aligner": r"\b(bowtie2|bwa|hisat2|star)\b",
            "peak_caller": r"\b(macs2|macs3|seacr)\b",
            "duplicate_handling": r"\b(remove_duplicates|markdup|rmdup|dedup|picard)\b",
            "organelle_filter": r"\b(organelle_chroms|chrM|chrMT|Pt|chloroplast|mitochond)\b",
            "reference_genome": r"\b(species|genome|reference|fasta|hg38|hg19|mm10|tair|grch)\b",
            "frip": r"\b(FRiP|frip|reads.*peak|peak.*reads)\b",
        }
        metric_formula_patterns = {
            "adapter_percent": r"(adapter[^=\n]{0,40}=\s*[^\n]+|adapter[^/\n]{0,60}/[^\n]+)",
            "q30_ratio": r"(q30[^=\n]{0,40}=\s*[^\n]+|Q\s*>=\s*30|qual[^/\n]{0,60}/[^\n]+)",
            "mapping_rate_percent": r"(mapping[^=\n]{0,40}=\s*[^\n]+|map[^/\n]{0,80}/[^\n]+)",
            "unique_mapping_rate_percent": r"(unique[^=\n]{0,40}=\s*[^\n]+|uniq[^/\n]{0,80}/[^\n]+)",
            "duplicate_rate_percent": r"(duplicate[^=\n]{0,40}=\s*[^\n]+|dup[^/\n]{0,80}/[^\n]+)",
            "mt_rate_percent": r"((?:chrMT|chrM|organelle|mitochond|chloroplast|chrPt)[^=\n]{0,60}=\s*[^\n]+|(?:chrMT|chrM|organelle|mitochond|chloroplast|chrPt)[^/\n]{0,80}/[^\n]+)",
            "frip_ratio": r"(frip[^=\n]{0,40}=\s*[^\n]+|reads[^/\n]{0,40}peaks[^/\n]{0,80}/[^\n]+|peak[^/\n]{0,40}reads[^/\n]{0,80}/[^\n]+)",
            "correlation": r"(spearman[^=\n]{0,40}=\s*[^\n]+|correlation[^=\n]{0,40}=\s*[^\n]+)",
        }

        def read_workflow_text(path: Path) -> str:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return ""
            return text[:50000]

        def logical_lines(text: str) -> list[tuple[int, str]]:
            rows: list[tuple[int, str]] = []
            pending = ""
            start_line = 1
            for line_no, raw in enumerate(text.splitlines(), start=1):
                stripped = raw.strip()
                if not stripped or stripped.startswith(("#", "//")):
                    continue
                if not pending:
                    start_line = line_no
                pending = f"{pending} {stripped}".strip()
                if stripped.endswith(("\\", "+", "%>%", "|>", ",")):
                    continue
                rows.append((start_line, pending))
                pending = ""
            if pending:
                rows.append((start_line, pending))
            return rows

        def is_formula_like(line: str) -> bool:
            code_only = re.sub(r"(['\"]).*?\1", "", line)
            lowered = code_only.lower()
            if any(token in lowered for token in ("read.table", "read_csv", "read.delim", "fread", "open(", "file.path")):
                return False
            has_assignment = bool(re.search(r"(<-|=|:=)", code_only))
            has_ratio = "/" in code_only or "percent" in lowered or "rate" in lowered or "ratio" in lowered
            has_compute_call = any(token in lowered for token in ("mutate(", "summarise(", "awk", "bc", "scale=", "corr(", "cor("))
            return has_assignment and (has_ratio or has_compute_call)

        def extract_formula(metric_key: str, text: str) -> tuple[str, int] | None:
            code_patterns = {
                "mapping_rate_percent": (
                    r"metrics\[['\"]Mapping_Rate['\"]\]\s*=\s*[^\n]+",
                    r"overall alignment rate",
                ),
                "unique_mapping_rate_percent": (
                    r"metrics\[['\"]Unique_Mapped_Rate['\"]\]\s*=\s*[^\n]+",
                    r"aligned concordantly exactly 1 time",
                ),
                "duplicate_rate_percent": (
                    r"metrics\[f?['\"][^'\"]*Duplication_Rate['\"]\]\s*=\s*[^\n]+",
                    r"PERCENT_DUPLICATION",
                ),
                "frip_ratio": (
                    r"target_df\.columns\s*=\s*\[[^\n]*['\"]FRiP['\"][^\n]*\]",
                    r"plotEnrichment[^\n]+--outRawCounts",
                ),
            }.get(metric_key, ())
            for pattern in code_patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if not match:
                    continue
                line_no = text[: match.start()].count("\n") + 1
                line = " ".join(match.group(0).strip().split())
                if metric_key == "frip_ratio" and "target_df.columns" in line:
                    line = f"{line}; FRiP is read from plotEnrichment --outRawCounts after retaining matching BAM/peak rows"
                if metric_key == "mapping_rate_percent" and "overall alignment rate" in line.lower():
                    line = "Mapping_Rate is parsed from bowtie2 'overall alignment rate' line"
                if metric_key == "unique_mapping_rate_percent" and "aligned concordantly exactly 1 time" in line.lower():
                    line = "Unique_Mapped_Rate is parsed from bowtie2 'aligned concordantly exactly 1 time' line"
                if metric_key == "duplicate_rate_percent" and "percent_duplication" in line.upper():
                    line = "Picard_Duplication_Rate is derived from Picard PERCENT_DUPLICATION * 100"
                return line[:320], line_no

            terms = {
                "adapter_percent": ("adapter",),
                "q30_ratio": ("q30", "qual", "quality"),
                "mapping_rate_percent": ("mapping", "map_rate", "mapped", "alignment_rate"),
                "unique_mapping_rate_percent": ("unique", "uniq"),
                "duplicate_rate_percent": ("duplicate", "duplication", "dup_rate", "dup"),
                "mt_rate_percent": ("chrmt", "chrm", "organelle", "mitochond", "chloroplast", "chrpt"),
                "frip_ratio": ("frip", "reads_in_peak", "reads_in_peaks", "in_peaks"),
                "correlation": ("spearman", "correlation", "corr"),
            }.get(metric_key, ())
            for line_no, line in logical_lines(text):
                lowered = line.lower()
                if not any(term in lowered for term in terms):
                    continue
                if not is_formula_like(line):
                    continue
                return " ".join(line.split())[:320], line_no
            return None

        for path in find_internal_workflow_files(root, limit=limit, project_config=project_config):
            text = read_workflow_text(path)
            if not text:
                continue
            lower_text = text.lower()
            matched = []
            is_code_file = path.suffix.lower() in {
                ".py",
                ".r",
                ".rmd",
                ".sh",
                ".bash",
                ".awk",
                ".sed",
                ".pl",
                ".smk",
                ".rule",
                ".rules",
            } or path.name.lower() == "snakefile"
            if is_code_file:
                for metric_key, pattern in metric_formula_patterns.items():
                    if metric_key in code_formula_sources:
                        continue
                    extracted = extract_formula(metric_key, text)
                    if extracted:
                        formula, source_line = extracted
                    else:
                        match = re.search(pattern, text, flags=re.IGNORECASE)
                        if not match:
                            continue
                        if not is_formula_like(match.group(0)):
                            continue
                        formula = " ".join(match.group(0).strip().split())[:320]
                        source_line = text[: match.start()].count("\n") + 1
                    code_formula_sources[metric_key] = {
                        "formula": formula,
                        "source_file": cls._relative_path(root, path),
                        "source_line": str(source_line),
                    }
            for key, pattern in keyword_patterns.items():
                if re.search(pattern, text, flags=re.IGNORECASE):
                    matched.append(key)
                    if key not in detected_parameters:
                        line = next(
                            (
                                raw.strip()
                                for raw in text.splitlines()
                                if re.search(pattern, raw, flags=re.IGNORECASE)
                            ),
                            "",
                        )
                        detected_parameters[key] = line[:240]
            files.append(
                {
                    "file": cls._relative_path(root, path),
                    "type": path.suffix.lower() or path.name,
                    "matched_topics": ", ".join(matched),
                    "has_shell_command": str(any(cmd in lower_text for cmd in ("bowtie2", "macs", "samtools", "bedtools"))),
                }
            )
        return {
            "files": files,
            "detected_parameters": detected_parameters,
            "code_formula_sources": code_formula_sources,
            "file_count": len(files),
        }

    @classmethod
    def _organelle_semantics(cls, payload: dict[str, Any] | None) -> dict[str, str]:
        payload = payload or {}
        config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
        species = str(
            config.get("species")
            or config.get("genome")
            or config.get("reference")
            or ""
        ).strip()
        normalized = species.lower()
        plant_tokens = (
            "tair",
            "arabidopsis",
            "oryza",
            "rice",
            "zea",
            "maize",
            "tomato",
            "solanum",
            "wheat",
            "triticum",
            "brassica",
            "plant",
        )
        animal_tokens = (
            "hg",
            "grch",
            "human",
            "mm",
            "grcm",
            "mouse",
            "rn",
            "rat",
            "danrer",
            "zebrafish",
            "bos",
            "sus",
            "canfam",
        )
        if any(token in normalized for token in plant_tokens):
            return {
                "species": species,
                "label": "Mitochondrial/plastid alignment rate",
                "table_label": "chrMT/Pt(%)",
                "definition": "比对到线粒体或叶绿体/质体染色体的 reads 比例。",
                "assumption": "植物样本需结合 organelle_chroms 确认 chrMT 与 Pt/chrPt 的实际染色体命名和过滤策略。",
                "interpretation": "该值描述植物细胞器 reads 占比，需要结合核基因组有效 reads 和流程过滤策略解释。",
                "downstream_impact": "若细胞器 reads 占用较多已比对 reads，可能减少可用于核基因组信号分析的 reads。",
            }
        if any(token in normalized for token in animal_tokens):
            return {
                "species": species,
                "label": "Mitochondrial alignment rate",
                "table_label": "Mitochondrial(%)",
                "definition": "比对到线粒体染色体的 reads 比例。",
                "assumption": "当前参考基因组属于动物/人类体系，应按 chrM/MT 及项目配置核对该指标。",
                "interpretation": "该值描述线粒体 reads 占比，需要结合参考基因组、chrM/MT 命名和过滤策略解释。",
                "downstream_impact": "若线粒体 reads 占用较多已比对 reads，可能减少可用于核基因组信号分析的 reads。",
            }
        return {
            "species": species,
            "label": "Organelle alignment rate",
            "table_label": "Organelle(%)",
            "definition": "比对到项目所配置细胞器染色体的 reads 比例。",
            "assumption": "物种信息不足时不能自行判断该字段代表线粒体还是叶绿体/质体。",
            "interpretation": "需先确认 species/reference 和 organelle_chroms，再解释该指标。",
            "downstream_impact": "需先确认物种和细胞器染色体口径，才能判断其对核基因组有效 reads 的影响。",
        }

    @classmethod
    def _metric_glossary(cls, config: dict[str, Any] | None = None) -> dict[str, str]:
        organelle = cls._organelle_semantics(config)
        return {
            "samplelist": "Defines sample IDs and input FASTQ paths. This should be read before interpreting all downstream metrics.",
            "config.yaml": "Pipeline parameters, species/reference choice, output path, duplicate handling, spike-in setting and peak calling thresholds.",
            "ReadsQC.Total Clean Reads": "Reads retained after filtering. Low retention can indicate raw sequencing or trimming problems.",
            "ReadsQC.Adapter": "Fraction of raw reads in which adapter-related sequence was detected. It does not by itself prove adapter remains in clean reads after trimming.",
            "ReadsQC.Q20/Q30": "Base quality ratios. Lower Q30 usually points to sequencing quality issues.",
            "AlignmentQC.Mapping_Rate": "Fraction of reads mapped to the reference genome. Low values can suggest species/reference mismatch or poor read quality.",
            "AlignmentQC.Unique_Mapped_Rate": "Uniquely mapped read ratio. Low values can indicate repetitive reads or reference issues.",
            "AlignmentQC.Picard_Duplication_Rate": "PCR/optical duplication estimate. High duplication can reduce usable library complexity.",
            "AlignmentQC.MT_Ratio": f"{organelle['definition']} {organelle['assumption']}",
            "Correlation.Spearman": "Sample similarity based on genome-wide signal. Replicates should generally correlate better than unrelated controls.",
            "Peak.FRIP": "Reads in peaks fraction. It reflects enrichment strength and should be interpreted with peak count and control samples.",
            "Peak.PeakCount": "Number of called peaks. Very high control peak counts or very low target peaks need review.",
            "Motif.knownResults": "Known motif enrichment from peak sequences. Top motifs should match target biology or expected cofactors where possible.",
        }

    @classmethod
    def _read_metric_guides(cls, root: Path, report_roots: list[Path]) -> list[dict[str, str]]:
        relative_candidates = [
            "README.txt",
            "1.ReadsQC/README.txt",
            "2.AlignmentQC/README.txt",
            "2.AlignmentQC/2.1AlignmentStat/README.txt",
            "3.Correlation/README.txt",
            "5.Peakcalling/README.txt",
            "5.Peakcalling/5.1PeakStat/README.txt",
            "5.Peakcalling/5.3PeakAnno/README.txt",
            "7.Motif/README.txt",
            "8.MotifyAnalysis/README.txt",
        ]
        candidates: list[Path] = [root / "README.txt"]
        for report_root in report_roots:
            candidates.extend(report_root / relative for relative in relative_candidates)

        guides: list[dict[str, str]] = []
        seen: set[Path] = set()
        for path in candidates:
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                resolved = path
            if resolved in seen or not path.exists() or not path.is_file():
                continue
            seen.add(resolved)
            try:
                preview = read_text_snippet(path, max_lines=120, max_chars=12000)
            except OSError:
                continue
            guides.append(
                {
                    "file": cls._relative_path(root, path),
                    "section": path.parent.name,
                    "preview": preview[:3000],
                }
            )
            if len(guides) >= 12:
                break
        return guides

    @classmethod
    def _build_metric_rule_sources(
        cls,
        *,
        metric_guides: list[dict[str, str]],
        workflow_summary: dict[str, Any],
        config: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        searchable_blocks: list[tuple[str, str, str]] = []
        for guide in metric_guides:
            searchable_blocks.append(
                (
                    "report_readme",
                    str(guide.get("file", "")),
                    str(guide.get("preview", "")),
                )
            )
        for key, value in (workflow_summary.get("detected_parameters", {}) or {}).items():
            searchable_blocks.append(("project_workflow", key, str(value)))
        if config:
            searchable_blocks.append(("project_config", "config.yaml", "\n".join(f"{k}: {v}" for k, v in config.items())))

        metric_terms = {
            "adapter_percent": ("adapter", "接头"),
            "q30_ratio": ("q30",),
            "mapping_rate_percent": ("mapping", "mapping rate", "比对率"),
            "unique_mapping_rate_percent": ("unique", "unique mapping", "唯一比对"),
            "duplicate_rate_percent": ("duplicate", "duplication", "重复率", "picard"),
            "mt_rate_percent": ("chrmt", "chrmt/pt", "mtratio", "organelle", "线粒体", "叶绿体", "质体"),
            "frip_ratio": ("frip", "reads in peaks"),
            "correlation": ("spearman", "correlation", "相关性"),
        }
        sources: dict[str, dict[str, Any]] = {}
        for metric_key, terms in metric_terms.items():
            matched: list[dict[str, str]] = []
            for source_type, source_file, text in searchable_blocks:
                lower_text = text.lower()
                if not any(term.lower() in lower_text for term in terms):
                    continue
                line = next(
                    (
                        raw.strip()
                        for raw in text.splitlines()
                        if any(term.lower() in raw.lower() for term in terms)
                    ),
                    text.strip()[:240],
                )
                matched.append(
                    {
                        "source_type": source_type,
                        "source_file": source_file,
                        "evidence": line[:240],
                    }
                )
            code_formula = (workflow_summary.get("code_formula_sources", {}) or {}).get(metric_key)
            formula_complete = cls._is_complete_metric_formula(metric_key, code_formula)
            if matched or code_formula:
                source_level = "project_verified" if formula_complete else "project_metric_mentioned"
                sources[metric_key] = {
                    "source_level": source_level,
                    "formula": code_formula.get("formula", "") if formula_complete else "",
                    "formula_candidate": code_formula.get("formula", "") if code_formula and not formula_complete else "",
                    "formula_source": (
                        "project_code"
                        if formula_complete
                        else "project_code_partial"
                        if code_formula
                        else "not_found_in_project_code"
                    ),
                    "formula_source_file": code_formula.get("source_file", "") if code_formula else "",
                    "formula_source_line": code_formula.get("source_line", "") if code_formula else "",
                    "threshold_source": "professional_default_unverified",
                    "matched_sources": matched[:4],
                    "needs_verification": not formula_complete,
                    "confidence": 0.8 if formula_complete else 0.5 if code_formula else 0.45,
                }
            else:
                sources[metric_key] = {
                    "source_level": "not_found_in_project",
                    "formula": "",
                    "formula_source": "not_found_in_project_code",
                    "formula_source_file": "",
                    "formula_source_line": "",
                    "threshold_source": "not_found_in_project",
                    "matched_sources": [],
                    "needs_verification": True,
                    "confidence": 0.25,
                }
        return sources

    @staticmethod
    def _is_complete_metric_formula(metric_key: str, code_formula: dict[str, Any] | None) -> bool:
        if not isinstance(code_formula, dict):
            return False
        formula = str(code_formula.get("formula") or "").strip().lower()
        if not formula:
            return False
        if metric_key == "mt_rate_percent":
            return "/" in formula and any(token in formula for token in ("mapped", "total", "alignment"))
        return True

    @staticmethod
    def _first_present(config: dict[str, str], keys: tuple[str, ...]) -> dict[str, str]:
        return {
            key: str(config.get(key, "")).strip()
            for key in keys
            if str(config.get(key, "")).strip()
        }

    @classmethod
    def _build_workflow_rule_sources(
        cls,
        *,
        workflow_summary: dict[str, Any],
        config: dict[str, str],
        config_file: str,
    ) -> dict[str, dict[str, Any]]:
        detected = workflow_summary.get("detected_parameters", {}) or {}
        sources: dict[str, dict[str, Any]] = {}

        def add_config_rule(rule_key: str, label: str, keys: tuple[str, ...]) -> None:
            values = cls._first_present(config, keys)
            if not values:
                return
            evidence = "; ".join(f"{key}={value}" for key, value in values.items())
            sources[rule_key] = {
                "label": label,
                "source_level": "project_verified",
                "source_type": "project_config",
                "source_file": config_file or "config.yaml",
                "value": evidence,
                "evidence": evidence,
                "needs_verification": False,
                "confidence": 0.82,
            }

        def add_workflow_rule(rule_key: str, label: str, parameter_key: str) -> None:
            evidence = str(detected.get(parameter_key, "")).strip()
            if not evidence:
                return
            existing = sources.get(rule_key)
            if existing:
                existing["source_type"] = "project_config+workflow"
                existing["evidence"] = f"{existing.get('evidence', '')}; workflow: {evidence}"[:500]
                existing["value"] = f"{existing.get('value', '')}; workflow: {evidence}"[:500]
                existing["confidence"] = 0.9
                return
            sources[rule_key] = {
                "label": label,
                "source_level": "project_verified",
                "source_type": "project_workflow",
                "source_file": parameter_key,
                "value": evidence,
                "evidence": evidence,
                "needs_verification": False,
                "confidence": 0.78,
            }

        add_config_rule(
            "reference_config",
            "reference genome and species configuration",
            ("species", "genome", "reference", "effective_genome_size"),
        )
        add_config_rule(
            "dedup_policy",
            "duplicate removal policy",
            ("remove_duplicates",),
        )
        add_config_rule(
            "peak_calling_params",
            "peak calling parameters",
            ("peak_caller", "peak_calling", "macs3_qvalue", "TOP_PEAKS_NUM"),
        )
        add_config_rule(
            "organelle_handling",
            "mitochondrial/plastid chromosome handling",
            ("organelle_chroms",),
        )
        add_config_rule(
            "trimming_policy",
            "adapter trimming policy",
            ("adapter_type", "trimming_tool"),
        )
        add_config_rule(
            "sequencing_mode",
            "sequencing mode",
            ("Sequencing", "sequencing_mode", "assay", "project_type"),
        )

        add_workflow_rule("aligner_config", "alignment tool and command rule", "aligner")
        add_workflow_rule("peak_calling_params", "peak calling parameters", "peak_caller")
        add_workflow_rule("dedup_policy", "duplicate handling rule", "duplicate_handling")
        add_workflow_rule("organelle_handling", "organelle filtering rule", "organelle_filter")
        add_workflow_rule("reference_config", "reference genome and species configuration", "reference_genome")
        add_workflow_rule("frip_data_source", "FRiP data source rule", "frip")

        return sources

    @staticmethod
    def _html_to_text(html: str) -> str:
        parser = _HTMLTextExtractor()
        parser.feed(html)
        return parser.text()

    @classmethod
    def _extract_html_report_sections(cls, html: str) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        section_matches = list(re.finditer(r"<section\b[^>]*>(.*?)</section>", html, flags=re.IGNORECASE | re.DOTALL))
        if not section_matches:
            text = cls._html_to_text(html)
            return [{"title": "完整报告", "text": text[:80000]}] if text else []

        for match in section_matches:
            section_html = match.group(1)
            text = cls._html_to_text(section_html)
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if not lines:
                continue
            title = lines[0]
            body = "\n".join(lines[1:]).strip()
            if not body:
                body = title
            sections.append(
                {
                    "title": title[:120],
                    "text": body[:10000],
                }
            )
            if len(sections) >= 24:
                break
        return sections

    @staticmethod
    def _format_report_sections(sections: list[dict[str, str]], max_chars: int = 30000) -> str:
        blocks: list[str] = []
        used = 0
        for index, section in enumerate(sections, start=1):
            title = section.get("title", f"Section {index}")
            text = section.get("text", "")
            block = f"## {index}. {title}\n{text}".strip()
            if not block:
                continue
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(block) > remaining:
                block = block[:remaining]
            blocks.append(block)
            used += len(block)
        return "\n\n".join(blocks)

    @classmethod
    def _find_project_html_report(cls, root: Path, report_roots: list[Path], include_body: bool = True) -> dict[str, Any]:
        candidates: list[Path] = []
        preferred_names = [
            "CUTTag_report.html",
            "CUTTag_report.htm",
            "report.html",
            "report.htm",
            f"{root.name}.html",
            f"{root.name}.htm",
        ]
        for report_root in report_roots:
            candidates.extend(report_root / name for name in preferred_names)
            candidates.extend((report_root / "report" / name) for name in preferred_names)
        candidates.extend(root / name for name in preferred_names)
        for path in find_files(root, ["cuttag_report.html", "report.html", ".html"], limit=10):
            if path not in candidates:
                candidates.append(path)

        seen: set[Path] = set()
        for path in candidates:
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                resolved = path
            if resolved in seen or not path.exists() or not path.is_file():
                continue
            seen.add(resolved)
            if not include_body:
                return {
                    "file": cls._relative_path(root, path),
                    "text_excerpt": "",
                    "sections": [],
                    "section_text": "",
                    "source": "project_html_report",
                }
            try:
                html = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            body_index = html.lower().find("<body")
            if body_index >= 0:
                html = html[body_index:]
            text = cls._html_to_text(html)
            sections = cls._extract_html_report_sections(html)
            section_text = cls._format_report_sections(sections)
            if not text:
                continue
            return {
                "file": cls._relative_path(root, path),
                "text_excerpt": text[:80000],
                "sections": sections,
                "section_text": section_text,
                "source": "project_html_report",
            }
        return {}

    @classmethod
    def _build_project_context(cls, root: Path, include_html_body: bool = True) -> dict[str, Any]:
        report_roots = list_report_roots(root)
        samplelist_files = [path for path in (root / "samplelist", root / "samplelist.txt") if path.exists()]
        for path in find_files(root, ["samplelist"], limit=3):
            if path not in samplelist_files:
                samplelist_files.append(path)
        config_files = [path for path in (root / "config.yaml", root / "config.yml") if path.exists()]
        for path in find_files(root, ["config.yaml", "config.yml"], limit=3):
            if path not in config_files:
                config_files.append(path)
        logger.info(
            "build_project_context root=%s config_files=%s",
            root,
            [str(p) for p in config_files],
        )

        samples: list[dict[str, Any]] = []
        samplelist_path = ""
        if samplelist_files:
            samplelist_path = cls._relative_path(root, samplelist_files[0])
            try:
                samples = cls._parse_samplelist(samplelist_files[0])
            except OSError:
                samples = []

        config: dict[str, str] = {}
        config_path = ""
        if config_files:
            config_path = cls._relative_path(root, config_files[0])
            try:
                config = cls._parse_config_summary(config_files[0])
            except OSError:
                config = {}
        workflow_summary = cls._build_workflow_summary(root, project_config=config)
        metric_guides = cls._read_metric_guides(root, report_roots)
        workflow_rule_sources = cls._build_workflow_rule_sources(
            workflow_summary=workflow_summary,
            config=config,
            config_file=config_path,
        )
        sample_roles = cls._build_sample_roles(samples)
        experiment_design = experiment_design_service.build(
            samples,
            config=config,
            sample_roles=sample_roles,
        )
        evidence_catalog = evidence_catalog_service.build(root)

        return {
            "samplelist_file": samplelist_path,
            "samples": samples,
            "sample_roles": sample_roles,
            "sample_roles_deprecated": True,
            "experiment_design": experiment_design,
            "evidence_catalog": evidence_catalog,
            "evidence_catalog_summary": evidence_catalog_service.summary_for_context(
                evidence_catalog
            ),
            "metric_schema": metric_schema_service.export_schema(),
            "knowledge_base_contract": {
                "version": "project-analysis-kb-v1",
                "stores": [
                    "metric definitions, units, formulas, numerators, denominators, and physical ranges",
                    "assay and target-class distinctions",
                    "versioned literature or guideline references",
                    "project SOP and workflow rules",
                ],
                "reference_range_policy": "source-attributed and versioned; never promoted to a project threshold without project evidence",
                "diagnostic_procedure_policy": "stored in metadata-filtered Skill decision cards, not long tutorial prompts",
            },
            "sample_count": len(samples),
            "config_file": config_path,
            "config": config,
            "workflow_summary": workflow_summary,
            "workflow_rule_sources": workflow_rule_sources,
            "metric_rule_sources": cls._build_metric_rule_sources(
                metric_guides=metric_guides,
                workflow_summary=workflow_summary,
                config=config,
            ),
            "report_roots": [cls._relative_path(root, path) for path in report_roots],
            "html_report": cls._find_project_html_report(root, report_roots, include_body=include_html_body),
            "metric_guides": metric_guides,
            "metric_glossary": cls._metric_glossary(config),
        }

    @classmethod
    def _build_project_version(cls, root: Path, project_context: dict[str, Any]) -> str:
        digest = hashlib.sha1()
        digest.update(str(root).encode("utf-8", errors="ignore"))
        for record in ((project_context.get("evidence_catalog") or {}).get("files", []) or []):
            if not isinstance(record, dict):
                continue
            digest.update(str(record.get("path") or "").encode("utf-8", errors="ignore"))
            digest.update(str(record.get("size_bytes") or 0).encode("utf-8", errors="ignore"))
            digest.update(str(record.get("mtime_ns") or 0).encode("utf-8", errors="ignore"))
        return f"project-v1:{digest.hexdigest()[:16]}"

    @classmethod
    def _aggregate_motif_metrics(cls, motif_items: list[dict[str, Any]]) -> dict[str, Any]:
        samples: list[dict[str, Any]] = []
        for item in motif_items:
            sample_name = str(item.get("sample") or "").strip()
            top_motifs = item.get("top_motifs") or []
            top_motif = top_motifs[0] if top_motifs else None
            samples.append(
                {
                    "sample": sample_name,
                    "motif_source": item.get("motif_source"),
                    "motif_count": item.get("motif_count", len(top_motifs)),
                    "top_motif_name": (top_motif or {}).get("motif_name"),
                    "top_motif_sites": (top_motif or {}).get("sites"),
                    "top_motif_evalue": (top_motif or {}).get("evalue"),
                }
            )

        ranked_samples = sorted(
            samples,
            key=lambda item: cls._safe_float(item.get("top_motif_evalue")) or float("inf"),
        )
        findings: list[str] = []
        if ranked_samples:
            best = ranked_samples[0]
            findings.append(
                f"motif 聚合结果显示 {best.get('sample')} 的 top motif 为 {best.get('top_motif_name')}, E-value={best.get('top_motif_evalue')}"
            )
        return {
            "sample_count": len(samples),
            "samples": ranked_samples,
            "findings": findings,
        }

    @staticmethod
    def _collect_evidence_notes_from_file_summaries(file_summaries: list[dict[str, Any]]) -> list[str]:
        findings: list[str] = []
        for item in file_summaries:
            if not isinstance(item, dict):
                continue
            summary = item.get("summary") or {}
            if isinstance(summary, dict):
                findings.extend(str(value) for value in (summary.get("findings") or []) if str(value).strip())
        return list(dict.fromkeys(findings))

    @classmethod
    def _parse_evidence_file(
        cls,
        *,
        root: Path,
        file_path: Path,
        experiment_design: dict[str, Any],
    ) -> dict[str, Any]:
        relative = str(file_path.relative_to(root))
        lower_name = file_path.name.lower()
        file_started_at = perf_counter()
        table_kind = cls._resolve_table_kind(file_path)
        progress_stage, progress_label = cls._progress_stage_for_evidence(file_path, table_kind)
        try:
            if table_kind is not None or lower_name in cls.STRUCTURED_TABLE_FILES or table_kind in {
                "diff_annotation",
                "diff_go",
                "diff_pathway",
                "diff_table",
            }:
                cache_kind = f"table:v2:{table_kind or lower_name}"
                cached = cls._get_cached_parse(file_path, cache_kind)
                if cached is None:
                    rows = cls._read_correlation_rows(file_path) if table_kind == "correlation" else read_table_rows(file_path)
                    if table_kind == "qc":
                        summary = cls._build_qc_summary(rows)
                        metric_payload = {"target": "qc", "value": summary.get("metrics", []), "mode": "replace"}
                    elif table_kind == "spikein":
                        summary = cls._build_spikein_summary(rows)
                        metric_payload = {"target": "spikein", "value": summary.get("metrics", []), "mode": "replace"}
                    elif table_kind == "alignment":
                        summary = cls._build_alignment_summary(rows)
                        metric_payload = {"target": "alignment", "value": summary.get("metrics", []), "mode": "replace"}
                    elif table_kind == "peak":
                        summary = cls._build_peak_summary(rows)
                        metric_payload = {
                            "target": "peak",
                            "value": {
                                "metrics": summary.get("metrics", {}),
                                "ranked": summary.get("ranked", []),
                            },
                            "mode": "replace",
                        }
                    elif table_kind == "frip":
                        summary = cls._build_frip_summary(rows, source_name=file_path.name)
                        metric_payload = {"target": "frip", "value": summary.get("metrics", []), "mode": "merge_frip"}
                    elif table_kind == "correlation":
                        summary = cls._build_correlation_summary(rows)
                        metric_payload = {"target": "correlation", "value": summary, "mode": "replace"}
                    elif table_kind == "diff_annotation":
                        summary = cls._build_diff_annotation_summary(rows)
                        metric_payload = {
                            "target": "diff",
                            "value": {
                                "kind": "diff_annotation",
                                "change_counts": summary.get("change_counts", {}),
                                "top_genes": summary.get("top_genes", []),
                            },
                            "mode": "append",
                        }
                    elif table_kind == "diff_go":
                        summary = cls._build_enrichment_summary(rows, "GO")
                        metric_payload = {
                            "target": "diff",
                            "value": {
                                "kind": "diff_go",
                                "top_terms": summary.get("top_terms", []),
                            },
                            "mode": "append",
                        }
                    elif table_kind == "diff_pathway":
                        summary = cls._build_enrichment_summary(rows, "Pathway")
                        metric_payload = {
                            "target": "diff",
                            "value": {
                                "kind": "diff_pathway",
                                "top_terms": summary.get("top_terms", []),
                            },
                            "mode": "append",
                        }
                    else:
                        summary = cls._build_enrichment_summary(rows, "DiffTable")
                        metric_payload = {
                            "target": "diff",
                            "value": {
                                "kind": "diff_table",
                                "top_terms": summary.get("top_terms", []),
                            },
                            "mode": "append",
                        }
                    cached = cls._set_cached_parse(
                        file_path,
                        cache_kind,
                        {"summary": summary, "metric_payload": metric_payload},
                    )
                summary = cached["summary"]
                metric_payload = cached["metric_payload"]
                if table_kind == "correlation":
                    summary = cls._stratify_correlation_summary(summary, experiment_design)
                    metric_payload = {
                        "target": "correlation",
                        "value": summary,
                        "mode": "replace",
                    }
                return {
                    "relative": relative,
                    "progress_stage": progress_stage,
                    "progress_label": progress_label,
                    "file_summary": {"file": relative, "type": "table", "summary": summary},
                    "evidence_status": {
                        "file": relative,
                        "status": "ok",
                        "type": "table",
                        "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                    },
                    "parsed_metric_update": metric_payload,
                    "findings": summary.get("findings", []),
                }

            cache_kind = "text_summary"
            cached = cls._get_cached_parse(file_path, cache_kind)
            if cached is None:
                if file_path.suffix.lower() == ".log":
                    preview = read_log_snippet(file_path)
                elif cls._looks_like_text_file(file_path):
                    preview = read_text_snippet(file_path)
                else:
                    preview = ""
                summary = cls._summarize_text_evidence(file_path, preview)
                cached = cls._set_cached_parse(
                    file_path,
                    cache_kind,
                    {"preview": preview, "summary": summary},
                )
            preview = cached["preview"]
            summary = cached["summary"]
            parsed_metric_update = None
            findings = []
            if summary.get("kind") in {"diff", "motif", "igv"}:
                payload = {key: value for key, value in summary.items() if key not in {"preview", "findings"}}
                if summary.get("kind") == "motif":
                    payload["sample"] = cls._extract_motif_sample_name(file_path)
                payload["file"] = relative
                parsed_metric_update = {
                    "target": summary["kind"],
                    "value": payload,
                    "mode": "append",
                }
                findings = summary.get("findings", [])
            return {
                "relative": relative,
                "progress_stage": progress_stage,
                "progress_label": progress_label,
                "file_summary": {"file": relative, "type": "text", "preview": preview, "summary": summary},
                "evidence_status": {
                    "file": relative,
                    "status": "ok",
                    "type": "text",
                    "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                },
                "parsed_metric_update": parsed_metric_update,
                "findings": findings,
            }
        except Exception as exc:
            return {
                "relative": relative,
                "progress_stage": progress_stage,
                "progress_label": progress_label,
                "error": str(exc),
                "file_summary": {"file": relative, "type": "error", "error": str(exc)},
                "evidence_status": {
                    "file": relative,
                    "status": "error",
                    "type": "unknown",
                    "error": str(exc),
                    "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                },
                "parsed_metric_update": None,
                "findings": [],
            }

    @classmethod
    def _apply_parsed_metric_update(
        cls,
        parsed_metrics: dict[str, Any],
        update: dict[str, Any] | None,
    ) -> None:
        if not update:
            return
        target = str(update.get("target") or "")
        mode = str(update.get("mode") or "replace")
        value = update.get("value")
        if not target:
            return
        if mode == "append":
            parsed_metrics.setdefault(target, []).append(value)
            return
        if mode == "merge_frip":
            parsed_metrics["frip"] = cls._merge_frip_metrics(
                parsed_metrics.get("frip", []) or [],
                value or [],
            )
            return
        parsed_metrics[target] = value

    @staticmethod
    def _format_percent(value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value:.2f}%"

    @staticmethod
    def _format_int_like(value: Any) -> str:
        if value is None or value == "":
            return "-"
        try:
            return f"{int(float(str(value))):,}"
        except ValueError:
            return str(value)

    @staticmethod
    def _peer_range(values: list[float]) -> tuple[float | None, float | None]:
        if not values:
            return None, None
        return min(values), max(values)

    @classmethod
    def _assess_relative_value(
        cls,
        sample_value: float | None,
        peer_min: float | None,
        peer_max: float | None,
        *,
        higher_is_worse: bool = False,
        lower_is_worse: bool = False,
    ) -> str:
        if sample_value is None or peer_min is None or peer_max is None:
            return "无法判断"
        if peer_min <= sample_value <= peer_max:
            return "正常"
        if higher_is_worse and sample_value > peer_max:
            return "明显偏高"
        if lower_is_worse and sample_value < peer_min:
            return "明显偏低"
        if sample_value > peer_max:
            return "偏高"
        if sample_value < peer_min:
            return "偏低"
        return "正常"

    @classmethod
    def _build_alignment_comparison_table(cls, rows: list[dict[str, Any]], focus_sample: str = "IgG") -> dict[str, Any] | None:
        if not rows:
            return None
        focus_row = next((item for item in rows if str(item.get("sample", "")).lower() == focus_sample.lower()), None)
        if focus_row is None:
            return None

        peer_rows = [item for item in rows if item is not focus_row]
        metric_specs = [
            ("mt_rate_percent", "mt_rate (线粒体比例)", cls._format_percent, True, False),
            ("mapping_rate_percent", "Mapping Rate", cls._format_percent, False, False),
            ("unique_mapping_rate_percent", "Unique Mapping Rate", cls._format_percent, False, False),
            ("duplicate_rate_percent", "Duplicate Rate", cls._format_percent, False, False),
            ("complexity", "Complexity (有效文库复杂度)", cls._format_int_like, False, True),
        ]
        rows_out: list[dict[str, str]] = []

        for field, label, formatter, higher_is_worse, lower_is_worse in metric_specs:
            sample_value_raw = focus_row.get(field)
            sample_value = cls._safe_float(sample_value_raw)
            peer_values = [cls._safe_float(item.get(field)) for item in peer_rows]
            peer_values = [value for value in peer_values if value is not None]
            peer_min, peer_max = cls._peer_range(peer_values)
            if formatter is cls._format_percent:
                peer_range_text = (
                    f"{cls._format_percent(peer_min)} - {cls._format_percent(peer_max)}"
                    if peer_min is not None and peer_max is not None
                    else "-"
                )
            else:
                peer_range_text = (
                    f"{cls._format_int_like(peer_min)} - {cls._format_int_like(peer_max)}"
                    if peer_min is not None and peer_max is not None
                    else "-"
                )
            rows_out.append(
                {
                    "metric": label,
                    "sample": focus_sample,
                    "sample_value": formatter(sample_value_raw),
                    "peer_range": peer_range_text,
                    "assessment": cls._assess_relative_value(
                        sample_value,
                        peer_min,
                        peer_max,
                        higher_is_worse=higher_is_worse,
                        lower_is_worse=lower_is_worse,
                    ),
                }
            )

        return {
            "title": "Alignment 指标对比",
            "sample": focus_sample,
            "columns": ["指标", f"{focus_sample} 样本", "其他样本范围", "对比情况"],
            "rows": rows_out,
        }

    @staticmethod
    def _median(values: list[float]) -> float | None:
        cleaned = sorted(value for value in values if value is not None)
        if not cleaned:
            return None
        middle = len(cleaned) // 2
        if len(cleaned) % 2:
            return cleaned[middle]
        return (cleaned[middle - 1] + cleaned[middle]) / 2

    @classmethod
    def _source_file_for_metric(cls, file_summaries: list[dict[str, Any]], *name_fragments: str) -> str:
        lowered_fragments = [item.lower() for item in name_fragments if item]
        for item in file_summaries:
            file_name = str(item.get("file", ""))
            lower_file = file_name.lower().replace("/", "\\")
            if any(fragment in lower_file for fragment in lowered_fragments):
                return file_name
        return ""

    @staticmethod
    def _progress_stage_for_evidence(file_path: Path, table_kind: str | None) -> tuple[str, str]:
        lower_name = file_path.name.lower()
        if table_kind == "qc" or lower_name == "readsqc.xls":
            return "read_reads_qc", "ReadsQC"
        if table_kind == "alignment" or lower_name in {"alignmentqc.xls", "aligentqc.xls"}:
            return "read_alignment_qc", "AlignmentQC"
        if table_kind == "spikein":
            return "read_spikein", "Spike-in"
        if table_kind == "frip" or "frip" in lower_name:
            return "read_frip", "FRiP"
        if table_kind == "peak":
            return "read_peak", "Peak 统计"
        if table_kind == "correlation":
            return "read_correlation", "相关性矩阵"
        if table_kind and table_kind.startswith("diff"):
            return "read_diff", "差异分析结果"
        if "readme" in lower_name:
            return "read_metric_guide", "指标说明"
        return "read_evidence_file", file_path.name

    @classmethod
    def _rule_severity(cls, rule: dict[str, Any], value: float | None) -> str:
        if value is None:
            return "unknown"
        for severity in ("critical", "warning"):
            threshold = rule.get(severity) or {}
            op = threshold.get("op")
            threshold_value = cls._safe_float(threshold.get("value"))
            if threshold_value is None:
                continue
            if op == ">" and value > threshold_value:
                return severity
            if op == "<" and value < threshold_value:
                return severity
        return "normal"

    @staticmethod
    def _rule_text(rule: dict[str, Any], severity: str) -> str:
        threshold = rule.get(severity) or rule.get("warning") or {}
        op = threshold.get("op", "")
        value = threshold.get("value", "")
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        unit = rule.get("unit", "")
        return f"{rule.get('label', '')} {op} {value}{unit}".strip()

    @staticmethod
    def _format_metric_value(value: float | None, unit: str) -> str:
        if value is None:
            return "-"
        if unit == "%":
            return f"{value:.2f}%"
        if abs(value) < 1:
            return f"{value:.4f}"
        return f"{value:.2f}"

    @classmethod
    def _build_rule_entry(
        cls,
        *,
        metric_key: str,
        category: str,
        sample: str,
        value: float | None,
        source_file: str,
        source_field: str | None = None,
        rule_source: dict[str, Any] | None = None,
        semantic_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rule = dict(cls.PROFESSIONAL_RULES.get(metric_key, {}))
        schema = metric_schema_service.get(metric_key)
        if schema:
            rule.setdefault("label", schema.get("label", metric_key))
            rule.setdefault("unit", schema.get("unit", ""))
            rule.setdefault("definition", schema.get("formula", ""))
            rule.setdefault("denominator", schema.get("denominator", ""))
        if semantic_overrides:
            rule.update(semantic_overrides)
        source_payload = rule_source or {
            "source_level": "not_found_in_project",
            "formula": "",
            "formula_source": "not_found_in_project_code",
            "formula_source_file": "",
            "formula_source_line": "",
            "threshold_source": "not_found_in_project",
            "matched_sources": [],
            "needs_verification": True,
            "confidence": 0.25,
        }
        threshold_source = str(source_payload.get("threshold_source", "not_found_in_project") or "not_found_in_project")
        if threshold_source in {"not_found_in_project", "project_rule_unverified"}:
            threshold_source = "professional_default_unverified"
        formula_source = str(source_payload.get("formula_source", "not_found_in_project_code") or "not_found_in_project_code")
        formula_verified = formula_source == "project_code" and bool(source_payload.get("formula"))
        threshold_rule = source_payload.get("threshold_rule")
        threshold_rule = threshold_rule if isinstance(threshold_rule, dict) else {}
        has_structured_threshold = any(
            isinstance(threshold_rule.get(level), dict)
            and threshold_rule[level].get("op") in {"<", ">"}
            and cls._safe_float(threshold_rule[level].get("value")) is not None
            for level in ("warning", "critical")
        )
        threshold_verified = (
            threshold_source.startswith("project_")
            and threshold_source != "project_rule_unverified"
            and has_structured_threshold
        )
        if threshold_verified:
            rule.pop("warning", None)
            rule.pop("critical", None)
            for level in ("warning", "critical"):
                if isinstance(threshold_rule.get(level), dict):
                    rule[level] = threshold_rule[level]
        severity = cls._rule_severity(rule, value) if threshold_verified else "unverified_threshold"
        measurement_id = {
            "adapter_percent": "readsqc_raw_adapter_detected_percent",
            "frip_ratio": "frip_reads_in_peaks_ratio",
        }.get(metric_key, metric_key)
        value_scale = str(
            schema.get("value_scale")
            or {
                "frip_ratio": "fraction",
                "correlation": "coefficient",
                "peak_count": "count",
            }.get(metric_key, "percent" if str(rule.get("unit", "")) == "%" else "number")
        )
        display_scale = (
            "percent"
            if schema.get("display_unit") == "%" or str(rule.get("unit", "")) == "%"
            else value_scale
        )
        population_scope = {
            "adapter_percent": "raw reads aggregated by the project ReadsQC table",
            "frip_ratio": "usable mapped reads/fragments evaluated against the called peak set",
        }.get(metric_key, str(rule.get("denominator", "")))

        if formula_verified and threshold_verified:
            evidence_grade = "project_formula_and_project_threshold"
            conclusion_strength = "project_rule_supported"
        elif formula_verified:
            evidence_grade = "project_formula_with_unverified_threshold"
            conclusion_strength = "data_supported_threshold_needs_project_validation"
        elif threshold_verified:
            evidence_grade = "project_threshold_formula_unverified"
            conclusion_strength = "threshold_supported_formula_needs_project_validation"
        else:
            evidence_grade = "data_observed_with_unverified_formula_and_threshold"
            conclusion_strength = "screening_signal_only"
        return {
            "category": category,
            "metric_key": metric_key,
            "measurement_id": measurement_id,
            "measurement_definition": (
                source_payload.get("formula")
                or schema.get("formula")
                or rule.get("definition", "")
            ),
            "metric": rule.get("label", metric_key),
            "sample": sample or "-",
            "value": value,
            "display_value": (
                metric_schema_service.format_value(metric_key, value)
                if schema
                else cls._format_metric_value(value, str(rule.get("unit", "")))
            ),
            "unit": rule.get("unit", ""),
            "value_scale": value_scale,
            "display_scale": display_scale,
            "population_scope": population_scope,
            "counting_unit": "fragments_or_reads" if metric_key == "frip_ratio" else "reads",
            "severity": severity,
            "rule": cls._rule_text(rule, severity) if threshold_verified and severity in {"critical", "warning"} else "",
            "source_file": source_file,
            "source_field": source_field or rule.get("source_field", ""),
            "definition": rule.get("definition", ""),
            "formula": source_payload.get("formula") or schema.get("formula", ""),
            "denominator": rule.get("denominator", ""),
            "denominator_name": schema.get("denominator", rule.get("denominator", "")),
            "numerator_name": schema.get("numerator", ""),
            "valid_range": schema.get("valid_range", []),
            "metric_schema_version": schema.get("schema_version", ""),
            "assumption": rule.get("assumption", ""),
            "source_level": source_payload.get("source_level", "not_found_in_project"),
            "formula_source": formula_source,
            "formula_source_file": source_payload.get("formula_source_file", ""),
            "formula_source_line": source_payload.get("formula_source_line", ""),
            "threshold_source": threshold_source,
            "threshold_rule": threshold_rule if threshold_verified else {},
            "threshold_needs_project_validation": not threshold_verified,
            "threshold_basis": "project_rule" if threshold_verified else "not_applied_unverified_default",
            "matched_sources": source_payload.get("matched_sources", []),
            "needs_verification": bool(source_payload.get("needs_verification", True)) or not threshold_verified,
            "metric_confidence": source_payload.get("confidence", 0.35),
            "evidence_grade": evidence_grade,
            "conclusion_strength": conclusion_strength,
            "interpretation": rule.get("interpretation", ""),
            "downstream_impact": rule.get("downstream_impact", ""),
        }

    @classmethod
    def _build_evidence_chain(
        cls,
        parsed_metrics: dict[str, Any],
        file_summaries: list[dict[str, Any]],
        metric_rule_sources: dict[str, dict[str, Any]] | None = None,
        project_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        metric_rule_sources = metric_rule_sources or {}
        qc_source = cls._source_file_for_metric(file_summaries, "readsqc.xls")
        alignment_source = cls._source_file_for_metric(file_summaries, "alignmentqc.xls", "aligentqc.xls")
        spikein_source = cls._source_file_for_metric(file_summaries, "spikein")
        frip_source = cls._source_file_for_metric(file_summaries, "frip")
        correlation_source = cls._source_file_for_metric(file_summaries, "spearman_corr_readcounts.tab")
        organelle_semantics = cls._organelle_semantics(project_context)

        for item in parsed_metrics.get("qc", []) or []:
            sample = item.get("sample") or "-"
            chain.append(
                cls._build_rule_entry(
                    metric_key="sequencing_depth",
                    category="ReadsQC",
                    sample=sample,
                    value=cls._safe_float(item.get("clean_read_count")),
                    source_file=qc_source,
                    source_field="Total Clean Reads",
                    rule_source=metric_rule_sources.get("sequencing_depth"),
                )
            )
            for key in ("adapter_percent", "q30_ratio"):
                chain.append(
                    cls._build_rule_entry(
                        metric_key=key,
                        category="ReadsQC",
                        sample=sample,
                        value=cls._safe_float(item.get(key)),
                        source_file=qc_source,
                        rule_source=metric_rule_sources.get(key),
                    )
                )
            if item.get("clean_read_retention_percent") is not None:
                chain.append(
                    cls._build_rule_entry(
                        metric_key="clean_read_retention_percent",
                        category="ReadsQC",
                        sample=sample,
                        value=cls._safe_float(item.get("clean_read_retention_percent")),
                        source_file=qc_source,
                        source_field="Clean Reads",
                        rule_source=metric_rule_sources.get("clean_read_retention_percent"),
                    )
                )

        for item in parsed_metrics.get("alignment", []) or []:
            sample = item.get("sample") or "-"
            source_fields = item.get("source_fields") or {}
            for key in (
                "mapping_rate_percent",
                "unique_mapping_rate_percent",
                "duplicate_rate_percent",
                "mt_rate_percent",
                "nrf",
                "pbc1",
                "pbc2",
            ):
                chain.append(
                    cls._build_rule_entry(
                        metric_key=key,
                        category="AlignmentQC",
                        sample=sample,
                        value=cls._safe_float(item.get(key)),
                        source_file=alignment_source,
                        source_field=source_fields.get(key),
                        rule_source=metric_rule_sources.get(key),
                        semantic_overrides=organelle_semantics if key == "mt_rate_percent" else None,
                    )
                )

        for item in parsed_metrics.get("spikein", []) or []:
            sample = item.get("sample") or "-"
            for key, field in (
                ("spikein_mapped_reads", "Mapped reads"),
                ("spikein_unique_mapping_rate_percent", "Unique mapping rate(%)"),
                ("spikein_scaling_factor", "Scaling factor"),
            ):
                source_key = {
                    "spikein_mapped_reads": "mapped_reads",
                    "spikein_unique_mapping_rate_percent": "unique_mapping_rate_percent",
                    "spikein_scaling_factor": "scaling_factor",
                }[key]
                chain.append(
                    cls._build_rule_entry(
                        metric_key=key,
                        category="SpikeIn",
                        sample=sample,
                        value=cls._safe_float(item.get(source_key)),
                        source_file=spikein_source,
                        source_field=field,
                        rule_source=metric_rule_sources.get(key),
                    )
                )

        for item in parsed_metrics.get("frip", []) or []:
            sample = item.get("sample") or "-"
            peak_set = item.get("peak_set") or sample
            entry = cls._build_rule_entry(
                metric_key="frip_ratio",
                category="CrossFRiP" if item.get("comparison_type") == "cross_frip" else "FRiP",
                sample=f"{sample} against {peak_set}" if peak_set != sample else sample,
                value=cls._safe_float(item.get("frip_ratio")),
                source_file=frip_source,
                source_field=item.get("source_field") or "percent",
                rule_source=metric_rule_sources.get("frip_ratio"),
            )
            entry.update(
                {
                    "measurement_id": (
                        "cross_frip_reads_in_peak_set_ratio"
                        if peak_set != sample
                        else "frip_reads_in_peaks_ratio"
                    ),
                    "sample_name": sample,
                    "peak_set": peak_set,
                    "comparison_type": item.get("comparison_type"),
                    "pair_type": experiment_design_service.classify_pair(
                        sample,
                        peak_set,
                        (project_context or {}).get("experiment_design") or {},
                    )
                    if peak_set != sample
                    else "self",
                    "numerator_value": cls._safe_float(item.get("reads_in_peaks")),
                    "denominator_value": cls._safe_float(item.get("mapped_reads")),
                }
            )
            chain.append(entry)

        correlation = parsed_metrics.get("correlation") or {}
        for pair in (correlation.get("pairs", []) if isinstance(correlation, dict) else [])[:200]:
            pair_type = str(pair.get("pair_type") or "unresolved")
            entry = cls._build_rule_entry(
                metric_key="correlation",
                category="Correlation",
                sample=f"{pair.get('left')} vs {pair.get('right')}",
                value=cls._safe_float(pair.get("value")),
                source_file=correlation_source,
                source_field="spearman correlation",
                rule_source=metric_rule_sources.get("correlation"),
            )
            entry["pair_type"] = pair_type
            entry["left_sample"] = pair.get("left")
            entry["right_sample"] = pair.get("right")
            if pair_type != "biological_replicates":
                entry.update(
                    {
                        "severity": "observed",
                        "rule": "",
                        "threshold_rule": {},
                        "threshold_needs_project_validation": True,
                        "threshold_basis": "not_applicable_outside_replicate_stratum",
                        "conclusion_strength": "direct_observation_only",
                    }
                )
            chain.append(entry)

        severity_rank = {"critical": 0, "warning": 1, "unknown": 2, "normal": 3}
        return sorted(chain, key=lambda item: (severity_rank.get(item.get("severity", "normal"), 9), item.get("sample", "")))

    @staticmethod
    def _build_anomaly_summary(evidence_chain: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        summary = {"critical": [], "warning": [], "unknown": [], "normal": []}
        for item in evidence_chain:
            severity = item.get("severity") if item.get("severity") in summary else "unknown"
            summary[severity].append(item)
        return summary

    @staticmethod
    def _build_threshold_validation_warnings(evidence_chain: list[dict[str, Any]]) -> list[str]:
        warnings: list[str] = []
        seen: set[str] = set()
        for item in evidence_chain:
            if not item.get("threshold_needs_project_validation"):
                continue
            display_value = str(item.get("display_value") or "").strip()
            if item.get("value") is None and display_value.lower() in {
                "",
                "-",
                "--",
                "na",
                "n/a",
                "nan",
                "none",
                "null",
            }:
                continue
            key = str(item.get("metric_key") or item.get("metric") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            warnings.append(
                "Threshold not verified in project scripts/README/SOP/report notes: "
                f"{item.get('metric', key)} has observed value {display_value}. "
                "No project-specific threshold was applied; do not present professional/default thresholds "
                "as project standards until a project-specific threshold is confirmed."
            )
        return warnings

    @classmethod
    def _build_rule_based_findings(cls, evidence_chain: list[dict[str, Any]]) -> list[str]:
        findings: list[str] = []
        severity_label = {"critical": "需优先复核", "warning": "需复核"}
        for item in evidence_chain:
            severity = item.get("severity")
            if severity not in severity_label:
                continue
            source = item.get("source_file") or "-"
            field = item.get("source_field") or item.get("metric_key") or "-"
            findings.append(
                f"{severity_label[severity]}：{item.get('sample', '-')} {item.get('metric', '')}="
                f"{item.get('display_value', '-')}；阈值 {item.get('rule', '-')}"
                f"；来源 {source}::{field}"
            )
        return findings

    @classmethod
    def _build_diagnostic_findings(cls, evidence_chain: list[dict[str, Any]]) -> list[str]:
        """Only emit anomaly language backed by a project-verified threshold."""
        return cls._build_rule_based_findings(evidence_chain)

    @classmethod
    def _build_tool_diagnostics(
        cls,
        *,
        parsed_metrics: dict[str, Any],
        evidence_chain: list[dict[str, Any]],
        project_context: dict[str, Any],
        analysis_plan: dict[str, Any],
    ) -> list[dict[str, Any]]:
        diagnostics: list[dict[str, Any]] = []
        selected_tool_names = {
            str(item.get("name") or "")
            for item in (analysis_plan.get("selected_tools") or [])
            if isinstance(item, dict)
        }
        target_metrics = {str(item) for item in (analysis_plan.get("target_metrics") or [])}
        should_run_adapter = (
            "diagnose_cuttag_adapter_readthrough" in selected_tool_names
            or "adapter_percent" in target_metrics
        )
        if should_run_adapter:
            adapter_diagnostic = project_cuttag_diagnostic_service.diagnose_adapter_readthrough(
                parsed_metrics=parsed_metrics,
                evidence_chain=evidence_chain,
                project_context=project_context,
                analysis_plan=analysis_plan,
            )
            if adapter_diagnostic:
                diagnostics.append(adapter_diagnostic)
        diagnostic_calls = (
            project_cuttag_diagnostic_service.diagnose_alignment_loss,
            project_cuttag_diagnostic_service.diagnose_duplicate_policy,
            project_cuttag_diagnostic_service.diagnose_frip_peak_quality,
            project_cuttag_diagnostic_service.diagnose_sample_correlation,
        )
        for diagnostic_call in diagnostic_calls:
            diagnostic = diagnostic_call(
                parsed_metrics=parsed_metrics,
                evidence_chain=evidence_chain,
                project_context=project_context,
                analysis_plan=analysis_plan,
            )
            if diagnostic:
                diagnostics.append(diagnostic)
        return diagnostics

    @classmethod
    def _build_cause_graph(
        cls,
        *,
        question: str,
        analysis_plan: dict[str, Any],
        evidence_chain: list[dict[str, Any]],
        tool_diagnostics: list[dict[str, Any]],
        project_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metric_evidence_plan = analysis_plan.get("metric_evidence_plan") if isinstance(analysis_plan, dict) else {}
        if not isinstance(metric_evidence_plan, dict) or not metric_evidence_plan:
            target_metrics = analysis_plan.get("target_metrics", []) if isinstance(analysis_plan, dict) else []
            metric_evidence_plan = cls._fallback_metric_evidence_plan(target_metrics, question)

        nodes: list[dict[str, Any]] = []
        for metric, graph in metric_evidence_plan.items():
            if not isinstance(graph, dict):
                continue
            node = cls._build_cause_node(
                metric=str(metric),
                graph=graph,
                evidence_chain=evidence_chain,
                tool_diagnostics=tool_diagnostics,
                project_context=project_context or {},
            )
            if node:
                nodes.append(node)

        ranked_causes = cls._aggregate_ranked_causes(nodes)
        leading_hypothesis = ranked_causes[0] if ranked_causes else None
        confirmed_hypothesis = (
            leading_hypothesis
            if leading_hypothesis and leading_hypothesis.get("support_level") == "supported"
            else None
        )
        confidence_score = cls._cause_graph_confidence(ranked_causes)
        competing_hypotheses = cls._build_competing_hypotheses(ranked_causes)
        return {
            "version": "metric-evidence-graph-v2",
            "mode": "question_driven_metric_diagnosis" if nodes else "evidence_only",
            "nodes": nodes,
            "ranked_causes": ranked_causes,
            "competing_hypotheses": competing_hypotheses,
            "leading_hypothesis": leading_hypothesis,
            "confirmed_hypothesis": confirmed_hypothesis,
            "diagnostic_confidence": {
                "score": confidence_score,
                "level": "high" if confidence_score >= 0.75 else "moderate" if confidence_score >= 0.45 else "low",
                "boundary": (
                    "根因已获得独立项目证据支持。"
                    if confirmed_hypothesis
                    else "当前仅形成待验证的差异诊断，目标指标本身不能证明根因。"
                ),
            },
            "answer_guidance": [
                "Answer the user's current question first.",
                "Use evidence_chain for observed values and source fields.",
                "Use ranked_causes for differential diagnosis, supporting or contradicting evidence, and verification actions.",
                "Do not treat the focus metric itself as proof of its root cause.",
                "Do not judge overall project pass/fail.",
            ],
        }

    @classmethod
    def _build_cause_node(
        cls,
        *,
        metric: str,
        graph: dict[str, Any],
        evidence_chain: list[dict[str, Any]],
        tool_diagnostics: list[dict[str, Any]],
        project_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        primary_metrics = [cls._canonical_metric_key(item) for item in graph.get("primary", []) or []]
        if not primary_metrics:
            primary_metrics = [cls._canonical_metric_key(metric)]
        upstream_metrics = [cls._canonical_metric_key(item) for item in graph.get("upstream", []) or []]
        parallel_metrics = [cls._canonical_metric_key(item) for item in graph.get("parallel", []) or []]
        downstream_metrics = [cls._canonical_metric_key(item) for item in graph.get("downstream", []) or []]

        primary_evidence = cls._evidence_for_metrics(evidence_chain, primary_metrics)
        upstream_evidence = cls._evidence_for_metrics(evidence_chain, upstream_metrics)
        parallel_evidence = cls._evidence_for_metrics(evidence_chain, parallel_metrics)
        downstream_evidence = cls._evidence_for_metrics(evidence_chain, downstream_metrics)
        diagnostics = cls._diagnostics_for_metric(metric, tool_diagnostics)

        if not any((primary_evidence, upstream_evidence, parallel_evidence, downstream_evidence, diagnostics)):
            return None

        evidence_gaps: list[str] = []
        next_checks: list[str] = []
        reasoning: list[str] = []
        for diagnostic in diagnostics:
            for item in diagnostic.get("evidence_gaps", []) or []:
                text = str(item).strip()
                if text and text not in evidence_gaps:
                    evidence_gaps.append(text)
            for item in diagnostic.get("next_checks", []) or []:
                text = str(item).strip()
                if text and text not in next_checks:
                    next_checks.append(text)
            for item in diagnostic.get("reasoning_chain", []) or []:
                text = str(item).strip()
                if text and text not in reasoning:
                    reasoning.append(text)

        hypotheses = cls._rank_candidate_causes(
            focus_metric=cls._canonical_metric_key(metric),
            candidate_causes=graph.get("candidate_causes", []) or [],
            primary_evidence=primary_evidence,
            upstream_evidence=upstream_evidence,
            parallel_evidence=parallel_evidence,
            downstream_evidence=downstream_evidence,
            diagnostics=diagnostics,
            project_context=project_context,
            diagnostic_gaps=evidence_gaps,
            diagnostic_checks=next_checks,
        )

        return {
            "focus_metric": cls._canonical_metric_key(metric),
            "primary_evidence": primary_evidence[:8],
            "upstream_evidence": upstream_evidence[:8],
            "parallel_evidence": parallel_evidence[:8],
            "downstream_evidence": downstream_evidence[:8],
            "candidate_causes": hypotheses[:8],
            "ranked_causes": hypotheses[:8],
            "diagnostic_summaries": [
                {
                    "tool": item.get("tool", ""),
                    "status": item.get("status", ""),
                    "summary": item.get("summary", ""),
                }
                for item in diagnostics[:4]
                if isinstance(item, dict)
            ],
            "reasoning_chain": reasoning[:8],
            "evidence_gaps": evidence_gaps[:8],
            "next_checks": next_checks[:8],
        }

    @classmethod
    def _rank_candidate_causes(
        cls,
        *,
        focus_metric: str,
        candidate_causes: list[Any],
        primary_evidence: list[dict[str, Any]],
        upstream_evidence: list[dict[str, Any]],
        parallel_evidence: list[dict[str, Any]],
        downstream_evidence: list[dict[str, Any]],
        diagnostics: list[dict[str, Any]],
        project_context: dict[str, Any],
        diagnostic_gaps: list[str],
        diagnostic_checks: list[str],
    ) -> list[dict[str, Any]]:
        related_evidence: list[tuple[str, dict[str, Any]]] = []
        seen_related: set[tuple[str, str, str, str, str, str]] = set()
        for relation, items in (
            ("upstream", upstream_evidence),
            ("parallel", parallel_evidence),
            ("downstream", downstream_evidence),
        ):
            for item in items:
                if not isinstance(item, dict):
                    continue
                semantic_key = (
                    relation,
                    cls._canonical_metric_key(item.get("metric_key")),
                    str(item.get("sample") or "-"),
                    str(item.get("measurement_id") or item.get("metric_key") or ""),
                    str(item.get("population_scope") or ""),
                    str(item.get("value") or ""),
                )
                if semantic_key in seen_related:
                    continue
                seen_related.add(semantic_key)
                related_evidence.append((relation, item))

        ranked: list[dict[str, Any]] = []
        diagnostic_review = any(item.get("status") == "needs_review" for item in diagnostics)
        diagnostic_available = any(item.get("status") != "missing_evidence" for item in diagnostics)
        for index, raw_cause in enumerate(candidate_causes):
            cause_id = str(raw_cause or "").strip()
            if not cause_id:
                continue
            profile = cls._cause_profile(cause_id)
            support_metrics = {cls._canonical_metric_key(item) for item in profile["support_metrics"]}
            contradict_on_normal = {
                cls._canonical_metric_key(item) for item in profile.get("contradict_on_normal", [])
            }
            supporting: list[dict[str, Any]] = []
            contradicting: list[dict[str, Any]] = []
            verified_support_families: set[tuple[str, str, str, str]] = set()
            for relation, item in related_evidence:
                metric_key = cls._canonical_metric_key(item.get("metric_key"))
                if metric_key not in support_metrics:
                    continue
                severity = str(item.get("severity") or "")
                evidence_item = {
                    "evidence_id": item.get("evidence_id", ""),
                    "relation": relation,
                    "metric_key": metric_key,
                    "sample": item.get("sample", "-"),
                    "value": item.get("value", "-"),
                    "severity": severity or "-",
                    "source": item.get("source", "-"),
                    "measurement_id": item.get("measurement_id", metric_key),
                    "population_scope": item.get("population_scope", ""),
                    "strength": "verified" if severity in {"critical", "warning"} else "observational",
                }
                if severity == "normal" and metric_key in contradict_on_normal:
                    evidence_item["reason"] = "该关联指标在项目阈值下正常，未出现该假设预期的联动。"
                    contradicting.append(evidence_item)
                else:
                    if severity in {"critical", "warning"}:
                        verified_support_families.add(
                            (
                                metric_key,
                                str(item.get("sample") or "-"),
                                str(item.get("measurement_id") or metric_key),
                                str(item.get("population_scope") or ""),
                            )
                        )
                        evidence_item["reason"] = "项目阈值支持该关联指标需要复核，为根因假设提供独立支持。"
                    else:
                        evidence_item["reason"] = "存在同项目关联观测，但阈值或方向尚未验证，只能作为弱支持。"
                    supporting.append(evidence_item)

            verified_support_count = len(verified_support_families)
            context_evidence = cls._cause_context_evidence(profile, project_context)
            score = 12 + max(0, 4 - index)
            score += min(verified_support_count * 18, 36)
            score += min(sum(1 for item in supporting if item["strength"] == "observational") * 4, 12)
            score += min(len(context_evidence) * 2, 4)
            score += 5 if diagnostic_review else 2 if diagnostic_available else 0
            score -= min(len(contradicting) * 15, 30)
            score = max(0, min(score, 100))

            if verified_support_count >= 2 and not contradicting:
                support_level = "supported"
            elif verified_support_count >= 1:
                support_level = "partially_supported"
            elif supporting or context_evidence or diagnostic_available:
                support_level = "plausible"
            else:
                support_level = "insufficient_evidence"

            verification_actions = cls._dedupe_text(
                list(profile["verification_actions"]) + list(diagnostic_checks)
            )[:5]
            missing_evidence = cls._dedupe_text(
                list(profile["missing_evidence"]) + list(diagnostic_gaps)
            )[:5]
            expected_validation_outcomes = cls._expected_validation_outcomes(
                cause_id,
                profile,
            )
            reasoning_summary = cls._cause_reasoning_summary(
                label=profile["label"],
                support_level=support_level,
                supporting=supporting,
                contradicting=contradicting,
                context_evidence=context_evidence,
            )
            ranked.append(
                {
                    "cause_id": cause_id,
                    "cause": cause_id,
                    "label": profile["label"],
                    "score": score,
                    "support_level": support_level,
                    "supporting_evidence": supporting[:6],
                    "supporting_evidence_count": len(supporting),
                    "verified_support_count": verified_support_count,
                    "contradicting_evidence": contradicting[:4],
                    "context_evidence": context_evidence[:4],
                    "missing_evidence": missing_evidence,
                    "verification_actions": verification_actions,
                    "expected_validation_outcomes": expected_validation_outcomes,
                    "downstream_impacts": list(profile["downstream_impacts"])[:4],
                    "reasoning_summary": reasoning_summary,
                    "focus_metric": focus_metric,
                }
            )

        ranked.sort(
            key=lambda item: (
                -int(item.get("score") or 0),
                -int(item.get("verified_support_count") or 0),
                str(item.get("cause_id") or ""),
            )
        )
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
        return ranked

    @staticmethod
    def _build_competing_hypotheses(ranked_causes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        panel: list[dict[str, Any]] = []
        leader = ranked_causes[0] if ranked_causes else None
        leader_score = float(leader.get("score") or 0.0) if isinstance(leader, dict) else 0.0
        for cause in ranked_causes[:4]:
            if not isinstance(cause, dict):
                continue
            score = float(cause.get("score") or 0.0)
            panel.append(
                {
                    "hypothesis_id": cause.get("cause_id") or cause.get("label") or "candidate",
                    "label": cause.get("label") or cause.get("cause_id") or "candidate",
                    "focus_metric": cause.get("focus_metric") or "",
                    "support_level": cause.get("support_level") or "",
                    "confidence": round(min(score / 100.0, 0.99), 3),
                    "supporting_evidence": list(cause.get("supporting_evidence") or [])[:3],
                    "contradicting_evidence": list(cause.get("contradicting_evidence") or [])[:2],
                    "missing_critical_evidence": list(cause.get("missing_evidence") or [])[:3],
                    "verification_actions": list(cause.get("verification_actions") or [])[:3],
                    "preferred_over_alternatives": bool(
                        leader is cause or str(cause.get("cause_id") or "") == str((leader or {}).get("cause_id") or "")
                    ),
                    "preference_reason": (
                        "当前综合得分最高，且独立支持证据更多。"
                        if leader is cause or str(cause.get("cause_id") or "") == str((leader or {}).get("cause_id") or "")
                        else "当前排序低于首位假设，需要更多独立证据或更少反证才能提升优先级。"
                        if leader_score > score
                        else "当前与其他假设接近，仍需补充验证。"
                    ),
                }
            )
        return panel

    @classmethod
    def _aggregate_ranked_causes(cls, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_cause: dict[str, dict[str, Any]] = {}
        for node in nodes:
            for cause in node.get("ranked_causes", []) or []:
                if not isinstance(cause, dict):
                    continue
                cause_id = str(cause.get("cause_id") or cause.get("cause") or "")
                current = by_cause.get(cause_id)
                if current is None or int(cause.get("score") or 0) > int(current.get("score") or 0):
                    by_cause[cause_id] = copy.deepcopy(cause)
        ranked = sorted(
            by_cause.values(),
            key=lambda item: (
                -int(item.get("score") or 0),
                -int(item.get("verified_support_count") or 0),
                str(item.get("cause_id") or ""),
            ),
        )
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
        return ranked[:12]

    @staticmethod
    def _cause_graph_confidence(ranked_causes: list[dict[str, Any]]) -> float:
        if not ranked_causes:
            return 0.0
        top = ranked_causes[0]
        score = 0.15
        score += min(int(top.get("verified_support_count") or 0) * 0.22, 0.44)
        score += min(int(top.get("supporting_evidence_count") or 0) * 0.04, 0.16)
        score += 0.1 if top.get("context_evidence") else 0.0
        score -= min(len(top.get("contradicting_evidence") or []) * 0.12, 0.24)
        return round(max(0.0, min(score, 0.95)), 2)

    @classmethod
    def _cause_context_evidence(
        cls,
        profile: dict[str, Any],
        project_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        sources = project_context.get("workflow_rule_sources") or {}
        evidence: list[dict[str, Any]] = []
        for rule_key in profile.get("context_rules", []) or []:
            source = sources.get(rule_key) if isinstance(sources, dict) else None
            if not isinstance(source, dict) or not source:
                continue
            evidence.append(
                {
                    "rule": rule_key,
                    "value": source.get("value", "-"),
                    "source": source.get("source_file") or source.get("evidence") or "-",
                    "source_level": source.get("source_level", "-"),
                    "reason": "项目配置可用于直接核验该假设，但配置存在本身不等于根因成立。",
                }
            )
        return evidence

    @staticmethod
    def _cause_reasoning_summary(
        *,
        label: str,
        support_level: str,
        supporting: list[dict[str, Any]],
        contradicting: list[dict[str, Any]],
        context_evidence: list[dict[str, Any]],
    ) -> str:
        if support_level in {"supported", "partially_supported"}:
            summary = f"{label}获得独立关联指标支持"
        elif supporting:
            summary = f"{label}与当前关联观测一致，但相关阈值或方向尚未验证"
        elif context_evidence:
            summary = f"{label}可由项目配置直接核验，但当前没有独立指标证明"
        else:
            summary = f"{label}目前仅是机制上可解释的候选原因"
        if contradicting:
            summary += f"；同时有 {len(contradicting)} 条关联证据削弱该假设"
        return summary + "。"

    @staticmethod
    def _expected_validation_outcomes(
        cause_id: str,
        profile: dict[str, Any],
    ) -> list[str]:
        specific = {
            "organelle_dna_background": [
                "若假设成立，raw/trimmed/mapped 各阶段会持续看到细胞器 reads 富集，且问题样本高于同批对照。",
                "若假设不成立，细胞器比例应主要由某一统计阶段或口径变化造成，而不是从原始数据开始持续升高。",
            ],
            "organelle_filtering_not_applied_before_statistics": [
                "若假设成立，过滤前 BAM 的细胞器比例较高，而过滤后核基因组 usable reads 明显恢复。",
                "若假设不成立，过滤前后比例变化不足以解释当前观测。",
            ],
            "short_fragment_readthrough": [
                "若假设成立，短片段区间与 Adapter Content 同步富集，clean FASTQ 或重剪切后该信号应下降。",
                "若假设不成立，Adapter 信号与片段长度无明显关联，且 clean FASTQ 中不持续存在。",
            ],
            "reference_genome_mismatch": [
                "若假设成立，更换正确参考或索引后 mapping/unique 应同步改善，未比对 reads 的物种归属会发生系统变化。",
                "若假设不成立，使用备选正确参考复算后 mapping/unique 不会得到有意义恢复。",
            ],
            "insufficient_effective_reads": [
                "若假设成立，reads 流失会集中在可定位的处理阶段，并与 FRiP、peak count 或相关性下降同向。",
                "若假设不成立，进入 peak calling 的 usable reads 仍充足，需要转向富集、背景或参数原因。",
            ],
            "high_background": [
                "若假设成立，IgG/Input 或 peak 外信号会升高，信噪比下降，且背景校正后 FRiP/peak 特异性改善。",
                "若假设不成立，对照与 peak 外背景不高，应优先检查目标富集强度或 peak calling 参数。",
            ],
            "missing_or_mismatched_control": [
                "若假设成立，修正 control 配对后背景估计和 peak 集合会发生方向一致的变化。",
                "若假设不成立，正确绑定 control 后主要结论应保持稳定。",
            ],
        }
        if cause_id in specific:
            return specific[cause_id]
        label = str(profile.get("label") or cause_id)
        actions = [str(item) for item in profile.get("verification_actions", []) if str(item).strip()]
        action = actions[0] if actions else "补充该假设对应的独立项目证据"
        return [
            f"若“{label}”成立，执行“{action}”后应观察到与该机制一致、可重复的方向性变化。",
            f"若“{label}”不成立，复核结果不会出现预期联动，应降低该原因排序并转向其他候选原因。",
        ]

    @staticmethod
    def _dedupe_text(items: list[Any]) -> list[str]:
        values: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if text and text not in values:
                values.append(text)
        return values

    @staticmethod
    def _cause_profile(cause_id: str) -> dict[str, Any]:
        profiles: dict[str, dict[str, Any]] = {
            "organelle_dna_background": {
                "label": "样本中细胞器 DNA 背景较高",
                "support_metrics": ["duplicate_rate_percent", "mapping_rate_percent", "unique_mapping_rate_percent"],
                "contradict_on_normal": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "context_rules": ["organelle_handling"],
                "missing_evidence": ["缺少 raw/clean reads 中细胞器 reads 的分阶段计数。", "缺少样本制备或细胞核提取质量记录。"],
                "verification_actions": ["分别统计 raw、trimmed、mapped BAM 中细胞器 reads 占比，定位升高发生在哪一步。", "复核样本裂解、细胞核纯化和起始材料状态，确认是否引入细胞器 DNA 背景。"],
                "downstream_impacts": ["可能压缩核基因组有效 reads，并传导到 unique mapping、FRiP、peak 和相关性。"],
            },
            "organelle_filtering_not_applied_before_statistics": {
                "label": "统计口径位于细胞器 reads 过滤之前",
                "support_metrics": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "contradict_on_normal": [],
                "context_rules": ["organelle_handling"],
                "missing_evidence": ["缺少该比例对应 BAM 阶段及分母定义。", "缺少细胞器过滤命令与统计命令的执行顺序。"],
                "verification_actions": ["核对 AlignmentQC 指标使用的 BAM、分母以及 organelle filtering 的先后顺序。", "对过滤前后 BAM 重算该比例，判断高值是否主要来自统计口径。"],
                "downstream_impacts": ["若只是过滤前统计口径，高比例不必然等同于下游有效 reads 同比例损失。"],
            },
            "sample_preparation_background": {
                "label": "样本制备引入的细胞器背景",
                "support_metrics": ["duplicate_rate_percent", "adapter_percent", "q30_ratio"],
                "contradict_on_normal": [],
                "context_rules": [],
                "missing_evidence": ["缺少裂解、细胞核分离、起始量和样本状态记录。"],
                "verification_actions": ["按样本批次对照裂解和细胞核纯化记录，并比较同批次样本的细胞器 reads。", "结合片段长度和文库复杂度判断是否存在制备阶段的系统性背景。"],
                "downstream_impacts": ["可能减少核基因组有效片段并增加样本间技术差异。"],
            },
            "reference_genome_mismatch": {
                "label": "参考基因组或版本不匹配",
                "support_metrics": ["mapping_rate_percent", "unique_mapping_rate_percent", "q30_ratio"],
                "contradict_on_normal": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "context_rules": ["reference_config"],
                "missing_evidence": ["缺少样本物种、参考版本与比对索引的一致性核对。"],
                "verification_actions": ["核对 species、reference、index 构建来源和染色体命名是否一致。", "抽取未比对 reads 重新比对到预期参考或污染库，比较归属变化。"],
                "downstream_impacts": ["会同时降低 mapping/unique，并减少可用于 peak calling 的有效 reads。"],
            },
            "adapter_or_low_quality_reads": {
                "label": "接头或低质量 reads 消耗",
                "support_metrics": ["adapter_percent", "q30_ratio", "mapping_rate_percent"],
                "contradict_on_normal": ["adapter_percent", "q30_ratio"],
                "context_rules": ["trimming_policy"],
                "missing_evidence": ["缺少 clean FASTQ 的 Adapter Content 和过滤前后保留率。"],
                "verification_actions": ["比较 raw/clean FastQC 的 Adapter Content、Q30 和 reads 保留率。", "核对 trimming 参数后重算 mapping，确认是否可恢复。"],
                "downstream_impacts": ["可能降低 mapping/unique，并进一步减少富集分析可用 reads。"],
            },
            "organelle_reads_dominant": {
                "label": "细胞器 reads 占用主要比对 reads",
                "support_metrics": ["mt_rate_percent", "mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent"],
                "contradict_on_normal": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "context_rules": ["organelle_handling"],
                "missing_evidence": ["缺少过滤前后核基因组有效 reads 计数。"],
                "verification_actions": ["按细胞器和核基因组分别统计 mapped/unique reads，并比较过滤前后变化。"],
                "downstream_impacts": ["可能压缩核基因组有效 reads，影响 FRiP、peak 和相关性。"],
            },
            "multi_mapping_or_repetitive_regions": {
                "label": "多重比对或重复区域占比较高",
                "support_metrics": ["mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent"],
                "contradict_on_normal": ["unique_mapping_rate_percent"],
                "context_rules": ["reference_config"],
                "missing_evidence": ["缺少 uniquely mapped、multi-mapped 和 unmapped reads 的拆分统计。"],
                "verification_actions": ["从比对日志拆分 unique、multi-mapped 和 unmapped reads，并核对 MAPQ 过滤口径。"],
                "downstream_impacts": ["会降低 unique reads，并影响后续定量和 peak 稳定性。"],
            },
            "short_fragment_readthrough": {
                "label": "短片段导致 adapter read-through",
                "support_metrics": ["fragment_size", "adapter_percent", "mapping_rate_percent", "unique_mapping_rate_percent"],
                "contradict_on_normal": [],
                "context_rules": ["trimming_policy"],
                "missing_evidence": ["缺少 fragment size 分布和 clean FASTQ adapter 证据。"],
                "verification_actions": ["联合查看 fragment size 与 raw/clean Adapter Content，确认 adapter 是否集中在短片段。"],
                "downstream_impacts": ["可能造成 reads 丢失并降低 mapping、unique 和有效富集 reads。"],
            },
            "adapter_trimming_parameter_mismatch": {
                "label": "trimming 参数或接头序列不匹配",
                "support_metrics": ["adapter_percent", "mapping_rate_percent", "q30_ratio"],
                "contradict_on_normal": ["adapter_percent"],
                "context_rules": ["trimming_policy"],
                "missing_evidence": ["缺少接头序列、overlap、错误率和最小保留长度参数。"],
                "verification_actions": ["核对接头序列与试剂盒，并用参数敏感性重跑小批 reads 比较保留率和 mapping。"],
                "downstream_impacts": ["可能留下接头或过度剪切，降低 clean reads 和比对效率。"],
            },
            "high_organelle_or_low_complexity_reads": {
                "label": "细胞器 reads 或低复杂度序列偏多",
                "support_metrics": ["mt_rate_percent", "duplicate_rate_percent", "unique_mapping_rate_percent"],
                "contradict_on_normal": ["mt_rate_percent", "duplicate_rate_percent"],
                "context_rules": ["organelle_handling"],
                "missing_evidence": ["缺少低复杂度、细胞器 reads 和重复序列的分类统计。"],
                "verification_actions": ["对未通过 trimming 或比对的 reads 做序列分类，并联合查看复杂度与细胞器占比。"],
                "downstream_impacts": ["可能减少可用于核基因组富集分析的有效 reads。"],
            },
            "library_construction_issue": {
                "label": "文库构建或起始材料问题",
                "support_metrics": ["duplicate_rate_percent", "q30_ratio", "mapping_rate_percent", "frip_ratio"],
                "contradict_on_normal": [],
                "context_rules": [],
                "missing_evidence": ["缺少起始量、PCR cycle、文库浓度和片段分布记录。"],
                "verification_actions": ["复核起始量、PCR cycle、文库浓度和片段分布，并与同批正常样本比较。"],
                "downstream_impacts": ["可能同时影响复杂度、比对和富集稳定性。"],
            },
            "low_library_complexity": {
                "label": "文库复杂度不足",
                "support_metrics": ["duplicate_rate_percent", "frip_ratio", "correlation"],
                "contradict_on_normal": ["duplicate_rate_percent"],
                "context_rules": ["dedup_policy"],
                "missing_evidence": ["缺少 NRF/PBC 或 estimated library size。"],
                "verification_actions": ["查看 NRF、PBC1/PBC2、estimated library size 和去重前后有效 reads。"],
                "downstream_impacts": ["可能降低 peak 定量稳定性和样本重复一致性。"],
            },
            "true_enrichment_duplication": {
                "label": "真实富集区域产生的生物学重复",
                "support_metrics": ["frip_ratio", "peak_count"],
                "contradict_on_normal": [],
                "context_rules": ["dedup_policy"],
                "missing_evidence": ["缺少 duplicates 在 peak 内外的分布和去重前后 FRiP 对比。"],
                "verification_actions": ["比较 duplicates 在 peak 内外的富集，并评估去重前后 FRiP/peak 稳定性。"],
                "downstream_impacts": ["若重复集中在真实 peak，简单删除可能损失真实信号。"],
            },
            "organelle_or_repetitive_reads": {
                "label": "细胞器或重复序列推高重复率",
                "support_metrics": ["mt_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent"],
                "contradict_on_normal": ["mt_rate_percent", "unique_mapping_rate_percent"],
                "context_rules": ["organelle_handling", "dedup_policy"],
                "missing_evidence": ["缺少 duplicates 的染色体和 MAPQ 分布。"],
                "verification_actions": ["按染色体、MAPQ 和细胞器/核基因组拆分 duplicates。"],
                "downstream_impacts": ["可能降低核基因组有效复杂度和定量稳定性。"],
            },
            "pcr_amplification_bias": {
                "label": "PCR 扩增偏倚",
                "support_metrics": ["duplicate_rate_percent", "frip_ratio", "correlation"],
                "contradict_on_normal": [],
                "context_rules": ["dedup_policy"],
                "missing_evidence": ["缺少 PCR cycle、起始量和文库复杂度统计。"],
                "verification_actions": ["核对 PCR cycle 与起始量，并比较同批次文库复杂度和 duplicates 分布。"],
                "downstream_impacts": ["可能放大少数片段并降低样本间定量一致性。"],
            },
            "insufficient_effective_reads": {
                "label": "核基因组有效 reads 不足",
                "support_metrics": ["mapping_rate_percent", "unique_mapping_rate_percent", "mt_rate_percent", "duplicate_rate_percent"],
                "contradict_on_normal": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "context_rules": [],
                "missing_evidence": ["缺少进入 peak calling 的 usable reads 绝对数量。"],
                "verification_actions": ["按 raw、clean、mapped、unique、去重后和 peak calling 输入逐级核算 reads。"],
                "downstream_impacts": ["可直接限制 FRiP、peak 数量和相关性稳定性。"],
            },
            "high_background": {
                "label": "背景信号较高",
                "support_metrics": ["frip_ratio", "peak_count", "peak_width", "tss_enrichment", "correlation"],
                "contradict_on_normal": ["frip_ratio", "correlation"],
                "context_rules": ["peak_calling_params"],
                "missing_evidence": ["缺少 IgG/Input、peak 外信号和信噪比分布。"],
                "verification_actions": ["比较实验样本与 IgG/Input 的 peak 内外信号、FRiP 和覆盖轨迹。"],
                "downstream_impacts": ["会降低 peak 特异性并削弱重复一致性。"],
            },
            "weak_target_enrichment": {
                "label": "目标蛋白富集较弱",
                "support_metrics": ["frip_ratio", "peak_count", "peak_width", "tss_enrichment", "correlation"],
                "contradict_on_normal": ["frip_ratio", "peak_count"],
                "context_rules": [],
                "missing_evidence": ["缺少阳性位点、抗体批次和目标蛋白预期富集模式。"],
                "verification_actions": ["检查阳性位点覆盖、抗体信息和目标蛋白预期 broad/narrow 富集模式。"],
                "downstream_impacts": ["可能导致 peak 数量少、FRiP 低和生物学解释受限。"],
            },
            "peak_calling_parameter_issue": {
                "label": "peak calling 参数或模式不适配",
                "support_metrics": ["frip_ratio", "peak_count"],
                "contradict_on_normal": [],
                "context_rules": ["peak_calling_params"],
                "missing_evidence": ["缺少 peak caller、阈值、broad/narrow 和 control 参数。"],
                "verification_actions": ["用符合目标蛋白模式的 peak caller/参数做小范围敏感性比较。"],
                "downstream_impacts": ["会改变 peak 集合，从而影响 FRiP、motif 和下游富集分析。"],
            },
            "missing_or_mismatched_control": {
                "label": "对照缺失或角色不匹配",
                "support_metrics": ["frip_ratio", "peak_count", "correlation"],
                "contradict_on_normal": [],
                "context_rules": ["peak_calling_params"],
                "missing_evidence": ["缺少 IgG/Input/control 角色和 peak calling 对照绑定关系。"],
                "verification_actions": ["核对样本角色、control 配对和 peak caller 实际使用的对照文件。"],
                "downstream_impacts": ["可能造成背景估计偏差并改变 peak 集合。"],
            },
            "weak_signal_noise_dominated_bins": {
                "label": "弱信号导致相关性被噪音主导",
                "support_metrics": ["frip_ratio", "peak_count", "mapping_rate_percent"],
                "contradict_on_normal": ["frip_ratio", "peak_count"],
                "context_rules": [],
                "missing_evidence": ["缺少相关性使用的 bin/peak 特征空间和信号分布。"],
                "verification_actions": ["分别在全基因组 bins 和共识 peaks 上重算相关性，并过滤低信号区域。"],
                "downstream_impacts": ["会降低重复一致性并影响差异分析可靠性。"],
            },
            "sample_role_or_group_mismatch": {
                "label": "样本角色或分组不匹配",
                "support_metrics": ["correlation"],
                "contradict_on_normal": [],
                "context_rules": [],
                "missing_evidence": ["缺少样本分组、处理条件和 biological replicate 定义。"],
                "verification_actions": ["核对 samplelist 中的组别、处理条件和重复关系后分层比较相关性。"],
                "downstream_impacts": ["错误分组会造成不恰当的重复一致性判断。"],
            },
            "upstream_qc_or_enrichment_issue": {
                "label": "上游 QC 或富集问题传导",
                "support_metrics": ["mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio", "peak_count"],
                "contradict_on_normal": ["mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio"],
                "context_rules": [],
                "missing_evidence": ["缺少最低相关样本的完整 QC 与富集链路。"],
                "verification_actions": ["对最低相关样本逐级比较 mapping、unique、FRiP、peak count 和覆盖轨迹。"],
                "downstream_impacts": ["可使相关性下降并削弱重复合并与差异分析。"],
            },
            "incorrect_correlation_feature_space": {
                "label": "相关性特征空间或参数不适配",
                "support_metrics": ["correlation", "peak_count"],
                "contradict_on_normal": [],
                "context_rules": [],
                "missing_evidence": ["缺少相关性输入矩阵、bin size、归一化和过滤参数。"],
                "verification_actions": ["核对相关性输入矩阵、bin size、归一化，并在共识 peak 上复算。"],
                "downstream_impacts": ["可能产生与真实生物学重复关系不一致的相关性结果。"],
            },
        }
        generic = {
            "label": cause_id.replace("_", " "),
            "support_metrics": [],
            "contradict_on_normal": [],
            "context_rules": [],
            "missing_evidence": ["缺少能够区分该候选原因与其他原因的独立项目证据。"],
            "verification_actions": ["补充与该假设直接对应的项目配置、原始质控或分阶段统计后再判断。"],
            "downstream_impacts": [],
        }
        return profiles.get(cause_id, generic)

    @classmethod
    def _fallback_metric_evidence_plan(cls, target_metrics: Any, question: str) -> dict[str, Any]:
        metrics = [cls._canonical_metric_key(item) for item in (target_metrics or []) if str(item).strip()]
        normalized_question = str(question or "").lower()
        if not metrics:
            if any(term in normalized_question for term in ("线粒体", "叶绿体", "质体", "细胞器", "organelle", "mitochond")):
                metrics.append("mt_rate_percent")
            elif "adapter" in normalized_question:
                metrics.append("adapter_percent")
            elif "frip" in normalized_question:
                metrics.append("frip_ratio")
            elif "unique" in normalized_question:
                metrics.append("unique_mapping_rate_percent")
            elif "mapping" in normalized_question:
                metrics.append("mapping_rate_percent")
            elif "duplicate" in normalized_question:
                metrics.append("duplicate_rate_percent")
            elif "corr" in normalized_question or "spearman" in normalized_question:
                metrics.append("correlation")

        templates = {
            "adapter_percent": {
                "primary": ["adapter_percent", "q30_ratio"],
                "upstream": ["fragment_size", "read_length", "cutadapt_params"],
                "parallel": ["mt_rate_percent", "duplicate_rate_percent"],
                "downstream": ["mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio", "correlation"],
                "candidate_causes": [
                    "short_fragment_readthrough",
                    "adapter_trimming_parameter_mismatch",
                    "high_organelle_or_low_complexity_reads",
                    "library_construction_issue",
                ],
            },
            "mapping_rate_percent": {
                "primary": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "upstream": ["adapter_percent", "q30_ratio", "reference_genome", "mt_rate_percent"],
                "parallel": ["duplicate_rate_percent"],
                "downstream": ["frip_ratio", "peak_count", "correlation"],
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
                "parallel": ["duplicate_rate_percent"],
                "downstream": ["frip_ratio", "peak_count", "correlation"],
                "candidate_causes": [
                    "multi_mapping_or_repetitive_regions",
                    "organelle_reads_dominant",
                    "reference_genome_mismatch",
                    "low_library_complexity",
                ],
            },
            "mt_rate_percent": {
                "primary": ["mt_rate_percent"],
                "upstream": ["organelle_chroms", "sample_preparation", "filtering_policy"],
                "parallel": ["mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent"],
                "downstream": ["frip_ratio", "peak_count", "correlation"],
                "candidate_causes": [
                    "organelle_dna_background",
                    "organelle_filtering_not_applied_before_statistics",
                    "sample_preparation_background",
                ],
            },
            "duplicate_rate_percent": {
                "primary": ["duplicate_rate_percent"],
                "upstream": ["library_complexity", "pcr_cycles", "mt_rate_percent"],
                "parallel": ["mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio"],
                "downstream": ["peak_count", "correlation"],
                "candidate_causes": [
                    "low_library_complexity",
                    "true_enrichment_duplication",
                    "organelle_or_repetitive_reads",
                    "pcr_amplification_bias",
                ],
            },
            "frip_ratio": {
                "primary": ["frip_ratio", "peak_count"],
                "upstream": ["mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent", "mt_rate_percent"],
                "parallel": ["peak_width", "reads_in_peaks", "background_signal"],
                "downstream": ["correlation"],
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
                "parallel": ["pca", "peak_overlap"],
                "downstream": ["replicate_consistency"],
                "candidate_causes": [
                    "weak_signal_noise_dominated_bins",
                    "sample_role_or_group_mismatch",
                    "upstream_qc_or_enrichment_issue",
                    "incorrect_correlation_feature_space",
                ],
            },
        }
        return {metric: templates[metric] for metric in metrics if metric in templates}

    @staticmethod
    def _canonical_metric_key(metric: Any) -> str:
        normalized = str(metric or "").strip().lower()
        aliases = {
            "frip": "frip_ratio",
            "chrmt_pt_rate_percent": "mt_rate_percent",
            "chrmt/pt": "mt_rate_percent",
            "mt": "mt_rate_percent",
        }
        return aliases.get(normalized, normalized)

    @classmethod
    def _evidence_for_metrics(
        cls,
        evidence_chain: list[dict[str, Any]],
        metric_keys: list[str],
    ) -> list[dict[str, Any]]:
        wanted = {cls._canonical_metric_key(item) for item in metric_keys}
        rows: list[dict[str, Any]] = []
        for item in evidence_chain:
            metric_key = cls._canonical_metric_key(item.get("metric_key"))
            if metric_key not in wanted:
                continue
            rows.append(
                {
                    "evidence_id": item.get("evidence_id", ""),
                    "metric_key": metric_key,
                    "metric": item.get("metric", metric_key),
                    "sample": item.get("sample", "-"),
                    "value": item.get("display_value", "-"),
                    "severity": item.get("severity", "-"),
                    "source": f"{item.get('source_file', '-') }::{item.get('source_field', '-')}",
                    "formula_source": item.get("formula_source", "-"),
                    "threshold_source": item.get("threshold_source", "-"),
                    "needs_verification": item.get("needs_verification", True),
                    "measurement_id": item.get("measurement_id", metric_key),
                    "population_scope": item.get("population_scope", ""),
                }
            )
        return rows

    @classmethod
    def _diagnostics_for_metric(
        cls,
        metric: str,
        tool_diagnostics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        metric = cls._canonical_metric_key(metric)
        tool_terms = {
            "adapter_percent": ("adapter",),
            "mapping_rate_percent": ("alignment",),
            "unique_mapping_rate_percent": ("alignment",),
            "mt_rate_percent": ("alignment",),
            "duplicate_rate_percent": ("duplicate",),
            "frip_ratio": ("frip", "peak"),
            "correlation": ("correlation",),
        }.get(metric, (metric,))
        diagnostics: list[dict[str, Any]] = []
        for item in tool_diagnostics:
            tool_name = str(item.get("tool") or "").lower()
            if any(term in tool_name for term in tool_terms):
                diagnostics.append(item)
        return diagnostics

    @classmethod
    def _build_comparison_tables(cls, parsed_metrics: dict[str, Any]) -> list[dict[str, Any]]:
        tables: list[dict[str, Any]] = []
        alignment_rows = parsed_metrics.get("alignment")
        if alignment_rows:
            table = cls._build_alignment_comparison_table(alignment_rows, focus_sample="IgG")
            if table:
                tables.append(table)
        return tables

    @classmethod
    def _build_diagnosis_summary(
        cls,
        question_type: str,
        comparison_tables: list[dict[str, Any]],
        automatic_findings: list[str],
        warnings: list[str],
        next_actions: list[str],
        evidence_chain: list[dict[str, Any]] | None = None,
        cause_graph: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conclusions: list[str] = []
        evidence: list[str] = []
        possible_causes: list[str] = []

        for item in evidence_chain or []:
            if item.get("severity") not in {"critical", "warning"}:
                continue
            source = item.get("source_file") or "-"
            field = item.get("source_field") or item.get("metric_key") or "-"
            rule = str(item.get("rule") or "").strip()
            rule_text = f"；项目阈值 {rule}" if rule else "；项目文件中未确认该指标阈值/标准"
            conclusions.append(
                f"需复核指标：{item.get('sample', '-')} {item.get('metric', '')}="
                f"{item.get('display_value', '-')}"
            )
            evidence.append(
                f"{item.get('category', '')}/{item.get('metric', '')}: {item.get('sample', '-')}="
                f"{item.get('display_value', '-')}{rule_text}"
                f"；来源 {source}::{field}"
            )
            interpretation = item.get("interpretation")
            if interpretation:
                possible_causes.append(str(interpretation))
            downstream_impact = item.get("downstream_impact")
            if downstream_impact:
                possible_causes.append(f"下游影响：{downstream_impact}")

        for table in comparison_tables:
            for row in table.get("rows", []):
                metric = row.get("metric", "")
                assessment = row.get("assessment", "")
                sample_value = row.get("sample_value", "")
                peer_range = row.get("peer_range", "")
                if assessment in {"明显偏高", "明显偏低", "偏高", "偏低"}:
                    conclusions.append(f"需复核指标：{metric} {assessment}")
                    evidence.append(f"{metric}: 当前样本 {sample_value}；其他样本范围 {peer_range}；仅作为同项目相对差异")

                    metric_lower = metric.lower()
                    if "mt_rate" in metric_lower or "线粒体" in metric:
                        possible_causes.append("线粒体 reads 偏高，可能与样本质量、线粒体污染或对照样本背景特征有关。")
                    if "complexity" in metric_lower:
                        possible_causes.append("文库复杂度偏低，可能提示有效文库量不足、起始输入偏低或扩增偏倚。")
                    if "mapping" in metric_lower and assessment == "偏低":
                        possible_causes.append("比对率偏低，建议结合参考基因组、污染和原始质控进一步排查。")

        if not conclusions and automatic_findings:
            for finding in automatic_findings[:3]:
                conclusions.append(f"需复核指标：{finding}")
                evidence.append(str(finding))
        if not conclusions and question_type == "diagnostic":
            conclusions.append("本轮未识别到与当前问题直接相关的需复核指标；不做项目交付判定。")

        if not conclusions:
            conclusions.append(f"{question_type} 方向未识别到与当前问题直接相关的需复核指标；不做项目交付判定。")

        if warnings:
            possible_causes.append("当前存在解析或证据告警，需先补齐证据后再判断指标影响范围。")

        ranked_causes = list((cause_graph or {}).get("ranked_causes", []) or [])
        for cause in ranked_causes[:5]:
            if not isinstance(cause, dict):
                continue
            summary = str(cause.get("reasoning_summary") or cause.get("label") or "").strip()
            if summary:
                possible_causes.append(summary)

        possible_causes = list(dict.fromkeys(possible_causes))[:5]
        evidence = list(dict.fromkeys(evidence))[:8]
        leading_hypothesis = (cause_graph or {}).get("leading_hypothesis")
        confirmed_hypothesis = (cause_graph or {}).get("confirmed_hypothesis")
        competing_hypotheses = list((cause_graph or {}).get("competing_hypotheses", []) or [])
        diagnostic_confidence = (cause_graph or {}).get("diagnostic_confidence") or {}
        evidence_against = []
        verification_plan: list[str] = []
        hypothesis_comparison: list[str] = []
        for cause in ranked_causes[:3]:
            if not isinstance(cause, dict):
                continue
            for item in cause.get("contradicting_evidence", []) or []:
                if not isinstance(item, dict):
                    continue
                evidence_against.append(
                    f"{cause.get('label', cause.get('cause_id', '-'))}: "
                    f"{item.get('sample', '-')} {item.get('metric_key', '-')}={item.get('value', '-')}; "
                    f"{item.get('reason', '')}"
                )
            verification_plan.extend(str(item) for item in cause.get("verification_actions", []) or [])
        for item in competing_hypotheses[:3]:
            if not isinstance(item, dict):
                continue
            hypothesis_comparison.append(
                f"{item.get('label', '-')}: {item.get('preference_reason', '')}"
            )
        verification_plan = cls._dedupe_text(verification_plan + list(next_actions))[:6]

        return {
            "conclusions": conclusions[:5],
            "evidence": evidence,
            "possible_causes": possible_causes,
            "ranked_causes": ranked_causes[:8],
            "leading_hypothesis": leading_hypothesis,
            "confirmed_hypothesis": confirmed_hypothesis,
            "diagnostic_confidence": diagnostic_confidence,
            "evidence_against": evidence_against[:6],
            "hypothesis_comparison": hypothesis_comparison[:4],
            "verification_plan": verification_plan,
            "next_actions": verification_plan[:5] or next_actions[:5],
        }

    @classmethod
    def _summarize_text_evidence(cls, file_path: Path, preview: str) -> dict[str, Any]:
        lower_name = file_path.name.lower()
        lines = [line.strip() for line in preview.splitlines() if line.strip()]
        summary: dict[str, Any] = {"preview": preview[:1500], "findings": []}

        if file_path.suffix.lower() == ".log":
            error_lines: list[str] = []
            warning_lines: list[str] = []
            for line in lines:
                ll = line.lower()
                if any(tok in ll for tok in ("error", "exception", "traceback", "critical", "fatal", "abort", "报错", "错误")):
                    error_lines.append(line)
                elif any(tok in ll for tok in ("warning", "warn", "警告")):
                    warning_lines.append(line)
            summary.update(
                {
                    "kind": "log",
                    "total_lines_in_preview": len(lines),
                    "error_line_count": len(error_lines),
                    "warning_line_count": len(warning_lines),
                    "error_lines": error_lines[:20],
                    "warning_lines": warning_lines[:10],
                }
            )
            if error_lines:
                summary["findings"].append(
                    f"日志中发现 {len(error_lines)} 行错误信息，首条：{error_lines[0][:200]}"
                )
            elif warning_lines:
                summary["findings"].append(
                    f"日志中发现 {len(warning_lines)} 行警告信息，首条：{warning_lines[0][:200]}"
                )
            else:
                summary["findings"].append("日志末尾未检测到明显错误或警告行")
            return summary

        if lower_name.endswith("_meme.txt"):
            motifs: list[dict[str, Any]] = []
            for line in lines:
                if not line.startswith("MOTIF "):
                    continue
                pieces = line.split()
                motif_name = pieces[1] if len(pieces) > 1 else "unknown"
                sites = None
                evalue = None
                if "sites" in line:
                    try:
                        sites = int(line.split("sites =", 1)[1].split()[0])
                    except (IndexError, ValueError):
                        sites = None
                if "E-value =" in line:
                    try:
                        evalue = line.split("E-value =", 1)[1].split()[0]
                    except IndexError:
                        evalue = None
                motifs.append({"motif_name": motif_name, "sites": sites, "evalue": evalue})
                if len(motifs) >= 100:
                    break
            summary.update(
                {
                    "kind": "motif",
                    "motif_source": "meme_txt",
                    "motif_count": len(motifs),
                    "top_motifs": motifs,
                }
            )
            if motifs:
                top = motifs[0]
                summary["findings"].append(
                    f"motif 结果检测到 {top['motif_name']}，sites={top.get('sites')}, E-value={top.get('evalue')}"
                )
            return summary

        if lower_name.endswith("diffpeak.readme.txt"):
            summary.update(
                {
                    "kind": "diff",
                    "diff_source": "readme",
                    "mentions_mval": "mval" in preview.lower(),
                    "mentions_padj": "padj" in preview.lower(),
                    "mentions_up_down_threshold": "change" in preview.lower() and "up" in preview.lower() and "down" in preview.lower(),
                }
            )
            if summary["mentions_up_down_threshold"]:
                summary["findings"].append("差异峰 readme 明确给出了 up/down 判定规则")
            return summary

        if "golist.readme" in lower_name or "pathwaylist.readme" in lower_name:
            enrichment_kind = "go" if "golist" in lower_name else "pathway"
            summary.update(
                {
                    "kind": "diff",
                    "diff_source": f"{enrichment_kind}_readme",
                    "mentions_enrichment": True,
                    "mentions_adjusted_pvalue": "p.adjust" in preview.lower() or "qvalue" in preview.lower(),
                }
            )
            summary["findings"].append(f"差异分析包含 {enrichment_kind.upper()} 富集结果说明")
            return summary

        if "diff" in lower_name or "deg" in lower_name:
            up_count = sum(1 for line in lines if "up" in line.lower())
            down_count = sum(1 for line in lines if "down" in line.lower())
            summary.update(
                {
                    "kind": "diff",
                    "line_count": len(lines),
                    "up_mentions": up_count,
                    "down_mentions": down_count,
                }
            )
            if up_count or down_count:
                summary["findings"].append(f"差异分析证据中包含 up={up_count}, down={down_count} 的文本线索")
            return summary

        if "motif" in lower_name or "homer" in lower_name or "meme" in lower_name:
            motif_lines = [line for line in lines if any(token in line.lower() for token in ("motif", "p-value", "target"))]
            summary.update(
                {
                    "kind": "motif",
                    "line_count": len(lines),
                    "motif_hits": motif_lines[:5],
                }
            )
            if motif_lines:
                summary["findings"].append("motif 结果文件中检测到候选 motif 线索")
            return summary

        if "igv" in lower_name or lower_name.endswith((".bw", ".bigwig", ".bedgraph")):
            summary.update({"kind": "igv", "line_count": len(lines)})
            summary["findings"].append("检测到可视化轨道相关文件，可用于后续 IGV 复核")
            return summary

        summary.update({"kind": "text", "line_count": len(lines)})
        return summary

    @staticmethod
    def _format_value(value: Any, digits: int = 2) -> str:
        if value is None:
            return "-"
        if isinstance(value, (int, float)):
            return f"{value:.{digits}f}"
        return str(value)

    @classmethod
    def _build_confidence(
        cls,
        evidence_status: list[dict[str, Any]],
        automatic_findings: list[str],
        *,
        analysis_plan: dict[str, Any] | None = None,
        evidence_request_status: list[dict[str, Any]] | None = None,
        claim_validation: dict[str, Any] | None = None,
        cause_graph: dict[str, Any] | None = None,
    ) -> float:
        if not evidence_status:
            return 0.0
        success_count = sum(1 for item in evidence_status if item.get("status") == "ok")
        score = success_count / max(len(evidence_status), 1)
        if automatic_findings:
            score += 0.05
        found_ratio = 0.0
        if evidence_request_status:
            found_ratio = sum(
                1
                for item in evidence_request_status
                if item.get("status") in {"found", "partial"}
            ) / max(len(evidence_request_status), 1)
            score += 0.15 * found_ratio
        if isinstance(claim_validation, dict) and claim_validation.get("passed"):
            score += 0.1
        if isinstance(cause_graph, dict):
            score += min(
                float((cause_graph.get("diagnostic_confidence") or {}).get("score") or 0.0)
                * 0.15,
                0.12,
            )
        reasoning_mode = str(
            ((analysis_plan or {}).get("response_plan") or {}).get("reasoning_mode")
            or ""
        )
        if reasoning_mode == "integrative_reasoning" and found_ratio < 0.6:
            score -= 0.08
        return round(max(0.0, min(score, 0.99)), 3)

    @classmethod
    def _render_report(cls, analysis: dict[str, Any]) -> str:
        file_summaries = analysis.get("file_summaries", [])
        findings = analysis.get("automatic_findings", [])
        warnings = analysis.get("warnings", [])
        metric_priority = analysis.get("metric_priority", []) or []
        structured_rules = analysis.get("structured_experience_rules", []) or []

        def block(filename: str):
            return next(
                (
                    item.get("summary")
                    for item in file_summaries
                    if item.get("summary") and item.get("file", "").lower().endswith(filename)
                ),
                None,
            )

        qc_block = block("readsqc.xls")
        aln_block = block("alignmentqc.xls")
        spike_block = block("spikein_align.xls")
        peak_block = block("samples_peak_number_stat.xls")
        frip_block = block("frip.xls")
        corr_block = block("spearman_corr_readcounts.tab")

        def markdown_table(headers: list[str], rows: list[list[Any]], limit: int = 200) -> list[str]:
            if not rows:
                return []
            table_lines = [
                "| " + " | ".join(headers) + " |",
                "| " + " | ".join("---" for _ in headers) + " |",
            ]
            for row in rows[:limit]:
                table_lines.append("| " + " | ".join(str(value if value is not None else "-") for value in row) + " |")
            return table_lines

        table_blocks: list[str] = []
        if qc_block:
            table_blocks.extend(["### ReadsQC", ""])
            table_blocks.extend(markdown_table(
                ["样本", "Clean Reads", "Adapter(%)", "Q20", "Q30"],
                [
                    [
                        item.get("sample", "-"),
                        item.get("clean_reads", "-"),
                        cls._format_value(item.get("adapter_percent")),
                        cls._format_value(item.get("q20_ratio"), 4),
                        cls._format_value(item.get("q30_ratio"), 4),
                    ]
                    for item in qc_block.get("metrics", [])
                ],
            ))
            table_blocks.append("")
        if aln_block:
            table_blocks.extend(["### AlignmentQC", ""])
            table_blocks.extend(markdown_table(
                ["样本", "Mapping(%)", "Unique(%)", "Duplicate(%)", "chrMT/Pt(%)", "Complexity"],
                [
                    [
                        item.get("sample", "-"),
                        cls._format_value(item.get("mapping_rate_percent")),
                        cls._format_value(item.get("unique_mapping_rate_percent")),
                        cls._format_value(item.get("duplicate_rate_percent")),
                        cls._format_value(item.get("mt_rate_percent")),
                        item.get("complexity", "-"),
                    ]
                    for item in aln_block.get("metrics", [])
                ],
            ))
            table_blocks.append("")
        if spike_block:
            table_blocks.extend(["### Spike-in", ""])
            table_blocks.extend(markdown_table(
                ["样本", "Unique Mapping(%)"],
                [
                    [item.get("sample", "-"), cls._format_value(item.get("unique_mapping_rate_percent"))]
                    for item in spike_block.get("metrics", [])
                ],
            ))
            table_blocks.append("")
        if frip_block:
            table_blocks.extend(["### FRiP", ""])
            table_blocks.extend(markdown_table(
                ["样本", "FRiP"],
                [[item.get("sample", "-"), cls._format_value(item.get("frip_ratio"), 4)] for item in frip_block.get("metrics", [])],
            ))
            table_blocks.append("")
        if peak_block:
            table_blocks.extend(["### Peak 数量", ""])
            table_blocks.extend(markdown_table(
                ["样本", "Peak 数"],
                [[sample, count] for sample, count in peak_block.get("ranked", [])],
            ))
            table_blocks.append("")
        if corr_block:
            corr_rows = []
            if corr_block.get("max_pair"):
                pair = corr_block["max_pair"]
                corr_rows.append(["最高相关", pair[0], pair[1], f"{pair[2]:.4f}"])
            if corr_block.get("min_pair"):
                pair = corr_block["min_pair"]
                corr_rows.append(["最低相关", pair[0], pair[1], f"{pair[2]:.4f}"])
            if corr_rows:
                table_blocks.extend(["### 样本相关性", ""])
                table_blocks.extend(markdown_table(["类型", "样本1", "样本2", "相关系数"], corr_rows))
                table_blocks.append("")

        lines = [
            f"# {analysis.get('project_id', '')} 二代建库分析报告",
            "",
            "## 项目概况",
            f"- 问题：{analysis.get('question', '')}",
            f"- 问题类型：{analysis.get('question_type', '')}",
            f"- 读取文件：{len(analysis.get('evidence_files', []))} 个",
            f"- 结果置信度：{analysis.get('confidence', 0.0):.3f}",
            "",
            "## 核心质控指标",
        ]

        if metric_priority:
            lines.append(f"- 优先排查指标顺序：{' -> '.join(metric_priority)}")
            lines.append("")
        if table_blocks:
            lines.extend(["", "## 关键数据表", ""])
            lines.extend(table_blocks)
        if qc_block:
            for item in qc_block.get("metrics", [])[:200]:
                lines.append(
                    f"- {item.get('sample', '-')}: Clean Reads {item.get('clean_reads', '-')}, Adapter {cls._format_value(item.get('adapter_percent'))}%, Q20 {cls._format_value(item.get('q20_ratio'), 4)}, Q30 {cls._format_value(item.get('q30_ratio'), 4)}"
                )
        else:
            lines.append("- 未读取到质控表")

        lines.extend(["", "## 比对与富集指标"])
        if aln_block:
            for item in aln_block.get("metrics", [])[:200]:
                lines.append(
                    f"- {item.get('sample', '-')}: Mapping {cls._format_value(item.get('mapping_rate_percent'))}%, Unique {cls._format_value(item.get('unique_mapping_rate_percent'))}%, Duplicate {cls._format_value(item.get('duplicate_rate_percent'))}%, chrMT/Pt {cls._format_value(item.get('mt_rate_percent'))}%, Complexity {item.get('complexity', '-')}"
                )
        else:
            lines.append("- 未读取到比对表")

        lines.extend(["", "## Spike-in 指标"])
        if spike_block:
            for item in spike_block.get("metrics", [])[:200]:
                lines.append(f"- {item.get('sample', '-')}: unique mapping rate {cls._format_value(item.get('unique_mapping_rate_percent'))}%")
        else:
            lines.append("- 未读取到 spike-in 表")

        lines.extend(["", "## FRiP 指标"])
        if frip_block:
            for item in frip_block.get("metrics", [])[:200]:
                lines.append(f"- {item.get('sample', '-')}: FRiP {cls._format_value(item.get('frip_ratio'), 4)}")
        else:
            lines.append("- 未读取到 FRiP 表")

        lines.extend(["", "## Peak 指标"])
        if peak_block:
            for sample, count in peak_block.get("ranked", [])[:200]:
                lines.append(f"- {sample}: {count} peaks")
        else:
            lines.append("- 未读取到 peak 统计表")

        lines.extend(["", "## 样本一致性"])
        if corr_block and corr_block.get("max_pair"):
            max_pair = corr_block["max_pair"]
            min_pair = corr_block.get("min_pair")
            lines.append(f"- 最高相关性：{max_pair[0]} vs {max_pair[1]} = {max_pair[2]:.4f}")
            if min_pair:
                lines.append(f"- 最低相关性：{min_pair[0]} vs {min_pair[1]} = {min_pair[2]:.4f}")
        else:
            lines.append("- 未读取到相关性矩阵")

        lines.extend(["", "## 需复核指标"])
        if findings:
            for item in findings[:50]:
                lines.append(f"- {item}")
        else:
            lines.append("- 暂未识别到需复核指标")

        if warnings:
            lines.extend(["", "## 风险与告警"])
            for item in warnings[:50]:
                lines.append(f"- {item}")

        if structured_rules:
            lines.extend(["", "## 历史规则参考"])
            for item in structured_rules[:5]:
                rule_key = item.get("rule_key", "")
                source = item.get("source", "")
                frequency = item.get("frequency", 0)
                lines.append(f"- {rule_key} (source={source}, frequency={frequency})")

        lines.extend(
            [
                "",
                "## 复核建议",
                "- 先按质控 -> 比对 -> spike-in -> FRiP -> peak -> 一致性的顺序逐步排查。",
            "- 若多个样本同时出现 Adapter 检出、Q30 或比对指标联动变化，先核对项目阈值和处理阶段，再排查建库与测序质量。",
                "- 若仅单样本指标需复核，优先检查样本输入、富集步骤和样本混淆。",
            ]
        )
        return "\n".join(lines)

    @classmethod
    def _build_default_next_actions(
        cls,
        question_type: str,
        warnings: list[str],
        automatic_findings: list[str],
    ) -> list[str]:
        if not warnings and not automatic_findings:
            return []
        actions = {
            "qc": ["优先复核 ReadsQC 与原始测序质控结果。", "区分原始 reads 的 Adapter 检出与 clean reads 的处理后残留，并核对项目阈值来源。"],
            "alignment": ["复核 AlignmentQC 中 mapping、duplicate 和 chrMT/Pt 指标。", "必要时回查比对参数与参考基因组版本。"],
            "spikein": ["检查 spike-in 样本投加量与比对配置。"],
            "frip": ["结合 peak 数和 FRiP 同步判断富集质量。"],
            "correlation": ["结合相关性最低样本，回查样本标签与重复一致性。"],
            "diff": ["优先确认差异分析分组定义与阈值设置。", "抽查 top 差异基因/peak 是否符合预期方向。"],
            "motif": ["优先复核 motif 富集来源文件和候选 motif。", "确认 motif 富集是否与 peak 集合和物种背景一致。"],
            "igv": ["结合轨道文件在 IGV 中复核关键位点信号。"],
        }.get(question_type, [])
        if warnings:
            actions.append("先处理解析失败或证据不足的问题，再解释指标影响范围。")
        if question_type == "diagnostic":
            diagnostic_actions = [
                "先按 ReadsQC -> AlignmentQC -> Spike-in -> FRiP -> Peak -> Correlation 顺序复核最异常样本。",
                "如果异常集中在单个样本，优先核对输入量、建库质量、比对质量和富集效率。",
            ]
            actions = diagnostic_actions + [item for item in actions if item not in diagnostic_actions]
        return actions

    @staticmethod
    def _wants_existing_report_summary(question: str, question_types: list[str]) -> bool:
        normalized = (question or "").strip().lower()
        if not normalized:
            return False
        specific_tags = {
            "qc",
            "alignment",
            "spikein",
            "diagnostic",
            "frip",
            "peak",
            "correlation",
            "diff",
            "motif",
            "igv",
        }
        if any(tag in specific_tags for tag in question_types):
            return False
        report_terms = (
            "报告",
            "ai报告",
            "html",
            "总结报告",
            "项目总结",
            "整体总结",
            "overview",
            "summary",
            "summarize",
        )
        extra_report_terms = (
            "\u603b\u7ed3",
            "\u9879\u76ee\u603b\u7ed3",
            "\u62a5\u544a",
            "\u62a5\u544a\u6d41\u7a0b",
            "ai\u62a5\u544a",
            "html\u62a5\u544a",
        )
        return "overview" in question_types and any(term in normalized for term in report_terms + extra_report_terms)

    @staticmethod
    def _resolve_report_mode(
        question: str,
        question_types: list[str],
        project_context: dict[str, Any],
    ) -> str:
        if project_context.get("html_report") and ProjectAnalysisService._wants_existing_report_summary(question, question_types):
            return "existing_html_report_summary"
        return "structured_evidence_analysis"

    @staticmethod
    def _build_metric_priority(planning_hints: dict[str, Any] | None, question_type: str) -> list[str]:
        prioritized = []
        for item in (planning_hints or {}).get("prioritized_metrics", []):
            normalized = str(item).strip().lower()
            if normalized and normalized not in prioritized:
                prioritized.append(normalized)
        if question_type not in prioritized:
            prioritized.append(question_type)
        return prioritized

    @classmethod
    def _should_read_internal_workflow_context(cls, question: str, question_types: list[str]) -> bool:
        normalized = (question or "").strip().lower()
        primary_type = question_types[0] if question_types else ""
        return primary_type in {"diagnostic", "overview"} or any(
            term in normalized for term in cls.INTERNAL_WORKFLOW_TERMS
        )

    @classmethod
    def _build_internal_workflow_context(
        cls,
        root: Path,
        question: str,
        question_types: list[str],
        project_config: dict[str, str] | None = None,
    ) -> str:
        if not cls._should_read_internal_workflow_context(question, question_types):
            return ""

        blocks: list[str] = []
        used_chars = 0
        for path in find_internal_workflow_files(root, limit=10, project_config=project_config):
            remaining = 18000 - used_chars
            if remaining <= 0:
                break
            snippet = read_text_snippet(path, max_lines=160, max_chars=min(5000, remaining)).strip()
            if not snippet:
                continue
            relative = cls._relative_path(root, path)
            block = f"[private workflow source: {relative}]\n{snippet}"
            blocks.append(block)
            used_chars += len(block)
        return "\n\n".join(blocks)

    @classmethod
    def _apply_metric_priority_to_actions(
        cls,
        actions: list[str],
        metric_priority: list[str],
    ) -> list[str]:
        if not metric_priority:
            return actions
        keyword_map = {
            "qc": ("readsqc", "q30", "adapter", "质控"),
            "alignment": ("alignment", "mapping", "duplicate", "chrmt", "比对"),
            "spikein": ("spike", "spike-in"),
            "diagnostic": ("readsqc", "alignment", "spike", "frip", "peak", "correlation", "adapter", "q30", "mapping"),
            "frip": ("frip",),
            "peak": ("peak",),
            "correlation": ("correlation", "相关"),
            "diff": ("diff", "差异"),
            "motif": ("motif",),
            "igv": ("igv",),
        }

        def score(action: str) -> int:
            lowered = action.lower()
            for index, metric in enumerate(metric_priority):
                for token in keyword_map.get(metric, (metric,)):
                    if token in lowered:
                        return index
            return len(metric_priority) + 1

        return sorted(actions, key=score)

    PLANNER_METRIC_ALIASES = {
        "chrmt_pt_rate_percent": "mt_rate_percent",
        "frip": "frip_ratio",
    }

    @classmethod
    def _planner_metric_key(cls, metric: str) -> str:
        normalized = str(metric or "").strip().lower()
        return cls.PLANNER_METRIC_ALIASES.get(normalized, normalized)

    @staticmethod
    def _request_module_tokens(request: dict[str, Any]) -> list[str]:
        raw_module = str(request.get("module") or "")
        raw_metric = str(request.get("metric") or "")
        tokens: list[str] = []
        for value in re.split(r"[,，/]\s*", f"{raw_module},{raw_metric}"):
            value = value.strip().lower()
            if value and value not in {"-", "all", "overview"} and value not in tokens:
                tokens.append(value)
        return tokens

    @classmethod
    def _build_evidence_request_status(
        cls,
        *,
        analysis_plan: dict[str, Any],
        evidence_files: list[str],
        file_summaries: list[dict[str, Any]],
        evidence_chain: list[dict[str, Any]],
        evidence_status: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        requests = analysis_plan.get("evidence_requests", []) if isinstance(analysis_plan, dict) else []
        if not isinstance(requests, list):
            return []

        known_files = [str(item or "") for item in evidence_files]
        known_files.extend(str(item.get("file") or "") for item in file_summaries if isinstance(item, dict))
        known_files.extend(str(item.get("file") or "") for item in evidence_status if isinstance(item, dict))
        unique_files = list(dict.fromkeys(file for file in known_files if file))
        lower_files = [(file, file.lower()) for file in unique_files]

        statuses: list[dict[str, Any]] = []
        for index, request in enumerate(requests[:16], start=1):
            if not isinstance(request, dict):
                continue
            request_type = str(request.get("type") or "unknown")
            metric = cls._planner_metric_key(str(request.get("metric") or ""))
            tokens = cls._request_module_tokens(request)
            matched_files = [
                file for file, lowered in lower_files
                if any(token in lowered for token in tokens)
            ][:8]
            matched_chain = [
                item for item in evidence_chain
                if isinstance(item, dict)
                and (
                    cls._planner_metric_key(str(item.get("metric_key") or "")) == metric
                    or str(item.get("category") or "").lower() in tokens
                )
            ][:8]

            formula_sources = list(dict.fromkeys(
                str(item.get("formula_source") or "")
                for item in matched_chain
                if str(item.get("formula_source") or "")
            ))
            threshold_sources = list(dict.fromkeys(
                str(item.get("threshold_source") or "")
                for item in matched_chain
                if str(item.get("threshold_source") or "")
            ))

            if request_type == "script_or_rule_source":
                has_project_formula = any(source == "project_code" for source in formula_sources)
                if has_project_formula:
                    status = "found"
                    message = "已确认项目脚本中的计算口径"
                elif matched_chain or matched_files:
                    status = "partial"
                    message = "已读到相关指标或文件，但未确认项目脚本公式或专属阈值"
                else:
                    status = "missing"
                    message = "未在本轮证据中读到相关脚本或规则来源"
            elif request_type in {"metric_table", "chart_data"}:
                if matched_chain:
                    status = "found"
                    message = "已读到相关指标观测值"
                elif matched_files:
                    status = "partial"
                    message = "已读到相关文件，但未形成结构化指标链"
                else:
                    status = "missing"
                    message = "未在本轮证据中读到相关指标表"
            elif request_type == "historical_project":
                status = "partial"
                message = "历史项目对比由独立对比链路处理，本轮仅记录请求"
            else:
                if matched_chain or matched_files:
                    status = "found"
                    message = "已读到相关证据"
                else:
                    status = "missing"
                    message = "未读到相关证据"

            statuses.append(
                {
                    "request_index": index,
                    "type": request_type,
                    "module": request.get("module", ""),
                    "metric": request.get("metric", ""),
                    "reason": request.get("reason", ""),
                    "status": status,
                    "message": message,
                    "matched_files": matched_files,
                    "matched_metrics": [
                        {
                            "metric_key": item.get("metric_key"),
                            "sample": item.get("sample"),
                            "value": item.get("display_value"),
                            "source_file": item.get("source_file"),
                        }
                        for item in matched_chain[:5]
                    ],
                    "formula_sources": formula_sources,
                    "threshold_sources": threshold_sources,
                }
            )
        return statuses

    @classmethod
    def analyze(
        cls,
        project_id: str,
        question: str,
        project_root: str | None = None,
        max_evidence_files: int = 40,
        planning_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        run_id = f"projrun_{uuid4().hex[:12]}"
        root = resolve_project_root(project_id, project_root)
        analysis_plan = copy.deepcopy(
            (planning_hints or {}).get("analysis_plan") or {}
        )
        question_types = cls._infer_question_types(question)
        question_type = question_types[0]
        # For pipeline_failure questions, ensure log files are in the local cache.
        # resolve_project_root returns cached paths immediately without re-mirroring,
        # so an old cache may lack log files.  Re-mirror only downloads new/changed files.
        if question_type == "pipeline_failure" and not find_log_files(root, limit=1):
            try:
                refresh_project_sftp_logs(project_id, root)
            except Exception:
                pass
        wants_report_summary = cls._wants_existing_report_summary(question, question_types)
        include_html_body = wants_report_summary or bool((planning_hints or {}).get("force_include_html_body"))
        logger.info(
            "project_analysis stage=start run_id=%s project=%s question_type=%s root=%s",
            run_id,
            project_id,
            question_type,
            str(root),
        )
        context_started_at = perf_counter()
        project_context = cls._build_cached_project_context(root, include_html_body=include_html_body)
        internal_workflow_context = cls._build_internal_workflow_context(
            root,
            question,
            question_types,
            project_context.get("config") or {},
        )
        logger.info(
            "project_analysis stage=build_context run_id=%s project=%s include_html_body=%s duration_ms=%.2f",
            run_id,
            project_id,
            include_html_body,
            (perf_counter() - context_started_at) * 1000,
        )
        project_version = cls._build_project_version(root, project_context)
        report_mode = cls._resolve_report_mode(question, question_types, project_context)
        if report_mode == "existing_html_report_summary" and max_evidence_files <= 0:
            evidence_files = []
        else:
            evidence_started_at = perf_counter()
            evidence_files = cls._select_evidence_files(
                root,
                question_types,
                max_evidence_files,
                planning_hints=planning_hints,
                evidence_catalog=project_context.get("evidence_catalog") or {},
            )
            logger.info(
                "project_analysis stage=select_evidence run_id=%s project=%s evidence=%d duration_ms=%.2f",
                run_id,
                project_id,
                len(evidence_files),
                (perf_counter() - evidence_started_at) * 1000,
            )
        file_summaries: list[dict[str, Any]] = []
        evidence_notes: list[str] = []
        evidence_status: list[dict[str, Any]] = []
        warnings: list[str] = []
        parsed_metrics: dict[str, Any] = {}
        selected_evidence_files = [
            str(path.relative_to(root)).replace("\\", "/")
            for path in evidence_files
        ]
        evidence_snapshot_key = project_snapshot_service.build_evidence_snapshot_key(
            project_version=project_version,
            selected_files=selected_evidence_files,
        )
        evidence_snapshot = project_snapshot_service.get(evidence_snapshot_key)
        evidence_snapshot_status = "hit" if evidence_snapshot else "miss"
        logger.info(
            "project_analysis stage=evidence_snapshot run_id=%s project=%s snapshot=%s",
            run_id,
            project_id,
            evidence_snapshot_status,
        )
        if evidence_snapshot is not None:
            parsed_metrics = evidence_snapshot.get("parsed_metrics", {}) or {}
            file_summaries = evidence_snapshot.get("file_summaries", []) or []
            evidence_status = evidence_snapshot.get("evidence_status", []) or []
            warnings = evidence_snapshot.get("warnings", []) or []
            evidence_notes = cls._collect_evidence_notes_from_file_summaries(file_summaries)
        evidence_files_to_parse = [] if evidence_snapshot is not None else evidence_files
        if evidence_files_to_parse:
            for file_path in evidence_files_to_parse:
                table_kind = cls._resolve_table_kind(file_path)
                progress_stage, progress_label = cls._progress_stage_for_evidence(file_path, table_kind)
                publish_project_progress(
                    f"姝ｅ湪璇诲彇 {progress_label}",
                    stage=progress_stage,
                    status="in_progress",
                    detail={"file": str(file_path.relative_to(root))},
                )
            max_workers = max(1, min(cls._EVIDENCE_PARSE_WORKERS, len(evidence_files_to_parse)))
            logger.info(
                "project_analysis stage=parse_evidence_parallel run_id=%s project=%s workers=%d files=%d",
                run_id,
                project_id,
                max_workers,
                len(evidence_files_to_parse),
            )
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="project-evidence") as executor:
                futures = [
                    executor.submit(
                        cls._parse_evidence_file,
                        root=root,
                        file_path=file_path,
                        experiment_design=project_context.get("experiment_design") or {},
                    )
                    for file_path in evidence_files_to_parse
                ]
                for future in futures:
                    result = future.result()
                    if result.get("error"):
                        error_message = f"{result['relative']} 璇诲彇澶辫触: {result['error']}"
                        warnings.append(error_message)
                        file_summaries.append(result["file_summary"])
                        evidence_status.append(result["evidence_status"])
                        publish_project_progress(
                            f"{result['progress_label']} 璇诲彇澶辫触",
                            stage=result["progress_stage"],
                            status="error",
                            detail={"file": result["relative"], "error": result["error"]},
                        )
                    else:
                        cls._apply_parsed_metric_update(parsed_metrics, result.get("parsed_metric_update"))
                        file_summaries.append(result["file_summary"])
                        evidence_notes.extend(result.get("findings", []))
                        evidence_status.append(result["evidence_status"])
                        publish_project_progress(
                            f"宸茶鍙?{result['progress_label']}",
                            stage=result["progress_stage"],
                            status="completed",
                            detail={"file": result["relative"], "type": result["evidence_status"].get("type")},
                        )
            evidence_files_to_parse = []

        for file_path in evidence_files_to_parse:
            relative = str(file_path.relative_to(root))
            lower_name = file_path.name.lower()
            file_started_at = perf_counter()
            table_kind = cls._resolve_table_kind(file_path)
            progress_stage, progress_label = cls._progress_stage_for_evidence(file_path, table_kind)
            publish_project_progress(
                f"正在读取 {progress_label}",
                stage=progress_stage,
                status="in_progress",
                detail={"file": relative},
            )
            try:
                if table_kind is not None or lower_name in cls.STRUCTURED_TABLE_FILES or table_kind in {
                    "diff_annotation",
                    "diff_go",
                    "diff_pathway",
                    "diff_table",
                }:
                    cache_kind = f"table:v2:{table_kind or lower_name}"
                    cached = cls._get_cached_parse(file_path, cache_kind)
                    if cached is None:
                        rows = cls._read_correlation_rows(file_path) if table_kind == "correlation" else read_table_rows(file_path)
                        if table_kind == "qc":
                            summary = cls._build_qc_summary(rows)
                            metric_payload = {"target": "qc", "value": summary.get("metrics", [])}
                        elif table_kind == "spikein":
                            summary = cls._build_spikein_summary(rows)
                            metric_payload = {"target": "spikein", "value": summary.get("metrics", [])}
                        elif table_kind == "alignment":
                            summary = cls._build_alignment_summary(rows)
                            metric_payload = {"target": "alignment", "value": summary.get("metrics", [])}
                        elif table_kind == "peak":
                            summary = cls._build_peak_summary(rows)
                            metric_payload = {
                                "target": "peak",
                                "value": {
                                    "metrics": summary.get("metrics", {}),
                                    "ranked": summary.get("ranked", []),
                                },
                            }
                        elif table_kind == "frip":
                            summary = cls._build_frip_summary(
                                rows,
                                source_name=file_path.name,
                            )
                            metric_payload = {"target": "frip", "value": summary.get("metrics", [])}
                        elif table_kind == "correlation":
                            summary = cls._build_correlation_summary(rows)
                            metric_payload = {
                                "target": "correlation",
                                "value": summary,
                            }
                        elif table_kind == "diff_annotation":
                            summary = cls._build_diff_annotation_summary(rows)
                            metric_payload = {
                                "target": "diff",
                                "value": {
                                    "kind": "diff_annotation",
                                    "change_counts": summary.get("change_counts", {}),
                                    "top_genes": summary.get("top_genes", []),
                                },
                            }
                        elif table_kind == "diff_go":
                            summary = cls._build_enrichment_summary(rows, "GO")
                            metric_payload = {
                                "target": "diff",
                                "value": {
                                    "kind": "diff_go",
                                    "top_terms": summary.get("top_terms", []),
                                },
                            }
                        elif table_kind == "diff_pathway":
                            summary = cls._build_enrichment_summary(rows, "Pathway")
                            metric_payload = {
                                "target": "diff",
                                "value": {
                                    "kind": "diff_pathway",
                                    "top_terms": summary.get("top_terms", []),
                                },
                            }
                        else:
                            summary = cls._build_enrichment_summary(rows, "DiffTable")
                            metric_payload = {
                                "target": "diff",
                                "value": {
                                    "kind": "diff_table",
                                    "top_terms": summary.get("top_terms", []),
                                },
                            }
                        cached = cls._set_cached_parse(
                            file_path,
                            cache_kind,
                            {"summary": summary, "metric_payload": metric_payload},
                        )
                    summary = cached["summary"]
                    metric_payload = cached["metric_payload"]
                    if table_kind == "correlation":
                        summary = cls._stratify_correlation_summary(
                            summary,
                            project_context.get("experiment_design") or {},
                        )
                        metric_payload = {
                            "target": "correlation",
                            "value": summary,
                        }
                    if metric_payload["target"] == "diff":
                        parsed_metrics.setdefault("diff", []).append(metric_payload["value"])
                    elif metric_payload["target"] == "frip":
                        parsed_metrics["frip"] = cls._merge_frip_metrics(
                            parsed_metrics.get("frip", []) or [],
                            metric_payload["value"],
                        )
                    else:
                        parsed_metrics[metric_payload["target"]] = metric_payload["value"]
                    file_summaries.append({"file": relative, "type": "table", "summary": summary})
                    evidence_notes.extend(summary.get("findings", []))
                    evidence_status.append(
                        {
                            "file": relative,
                            "status": "ok",
                            "type": "table",
                            "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                        }
                    )
                    publish_project_progress(
                        f"已读取 {progress_label}",
                        stage=progress_stage,
                        status="completed",
                        detail={"file": relative, "type": "table"},
                    )
                else:
                    cache_kind = "text_summary"
                    cached = cls._get_cached_parse(file_path, cache_kind)
                    if cached is None:
                        preview = read_text_snippet(file_path) if cls._looks_like_text_file(file_path) else ""
                        summary = cls._summarize_text_evidence(file_path, preview)
                        cached = cls._set_cached_parse(
                            file_path,
                            cache_kind,
                            {"preview": preview, "summary": summary},
                        )
                    preview = cached["preview"]
                    summary = cached["summary"]
                    if summary.get("kind") in {"diff", "motif", "igv"}:
                        payload = {key: value for key, value in summary.items() if key not in {"preview", "findings"}}
                        if summary.get("kind") == "motif":
                            payload["sample"] = cls._extract_motif_sample_name(file_path)
                        payload["file"] = relative
                        parsed_metrics.setdefault(summary["kind"], []).append(
                            payload
                        )
                        evidence_notes.extend(summary.get("findings", []))
                    file_summaries.append({"file": relative, "type": "text", "preview": preview, "summary": summary})
                    evidence_status.append(
                        {
                            "file": relative,
                            "status": "ok",
                            "type": "text",
                            "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                        }
                    )
                    publish_project_progress(
                        f"已读取 {progress_label}",
                        stage=progress_stage,
                        status="completed",
                        detail={"file": relative, "type": "text"},
                    )
            except Exception as exc:
                error_message = f"{relative} 读取失败: {exc}"
                warnings.append(error_message)
                file_summaries.append({"file": relative, "type": "error", "error": str(exc)})
                evidence_status.append(
                    {
                        "file": relative,
                        "status": "error",
                        "type": "unknown",
                        "error": str(exc),
                        "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                    }
                )
                publish_project_progress(
                    f"{progress_label} 读取失败",
                    stage=progress_stage,
                    status="error",
                    detail={"file": relative, "error": str(exc)},
                )

        publish_project_progress(
            "正在汇总结论",
            stage="synthesis",
            status="in_progress",
            detail={"evidence_count": len(evidence_status)},
        )
        if evidence_snapshot is None:
            project_snapshot_service.set(
                evidence_snapshot_key,
                {
                    "parsed_metrics": parsed_metrics,
                    "file_summaries": file_summaries,
                    "evidence_status": evidence_status,
                    "warnings": warnings,
                    "metric_tables_ready": sorted(parsed_metrics.keys()),
                },
            )
        automatic_findings = list(dict.fromkeys(evidence_notes))
        if parsed_metrics.get("motif"):
            motif_summary = cls._aggregate_motif_metrics(parsed_metrics["motif"])
            parsed_metrics["motif_summary"] = motif_summary
            automatic_findings.extend(motif_summary.get("findings", []))
            automatic_findings = list(dict.fromkeys(automatic_findings))
        metric_rule_sources = project_context.get("metric_rule_sources", {}) or {}
        organelle_semantics = cls._organelle_semantics(project_context)
        for item in parsed_metrics.get("alignment", []) or []:
            if isinstance(item, dict):
                item["organelle_metric_label"] = organelle_semantics["table_label"]
                item["organelle_interpretation"] = organelle_semantics["interpretation"]
                item["species"] = organelle_semantics["species"]
        evidence_chain = cls._build_evidence_chain(
            parsed_metrics,
            file_summaries,
            metric_rule_sources,
            project_context=project_context,
        )
        evidence_cards = evidence_card_service.build_cards(
            evidence_chain,
            project_id=project_id,
            project_context=project_context,
        )
        evidence_chain = evidence_card_service.attach_ids(evidence_chain, evidence_cards)
        agent_loop = project_expert_tool_service.run_loop(
            project_root=root,
            project_id=project_id,
            question=question,
            analysis_plan=analysis_plan,
            project_context=project_context,
            existing_cards=evidence_cards,
            max_rounds=3,
        )
        evidence_cards = evidence_card_service.consolidate_cards(
            agent_loop.get("evidence_cards", evidence_cards)
        )
        evidence_card_validation = evidence_card_service.validate_cards(evidence_cards)
        quarantined_evidence_cards = evidence_card_validation.get(
            "quarantined_cards",
            [],
        )
        evidence_cards = evidence_card_validation.get("valid_cards", [])
        experiment_design = project_context.get("experiment_design") or {}
        assay_snapshot_key = project_snapshot_service.build_assay_snapshot_key(
            project_version=project_version,
            selected_files=selected_evidence_files,
            evidence_cards=evidence_cards,
            experiment_design=experiment_design,
        )
        assay_snapshot = project_snapshot_service.get(assay_snapshot_key)
        assay_snapshot_status = "hit" if assay_snapshot else "miss"
        logger.info(
            "project_analysis stage=assay_snapshot run_id=%s project=%s snapshot=%s",
            run_id,
            project_id,
            assay_snapshot_status,
        )
        if assay_snapshot is not None:
            assay_profile = assay_snapshot.get("assay_profile", {}) or {}
        else:
            assay_profile = assay_analysis_service.build(
                project_context=project_context,
                evidence_cards=evidence_cards,
                experiment_design=experiment_design,
            )
            project_snapshot_service.set(
                assay_snapshot_key,
                {
                    "assay_profile": assay_profile,
                    "experiment_design": experiment_design,
                },
            )
        known_samples = [
            str(
                item.get("sample")
                or item.get("sample_id")
                or item.get("name")
                or ""
            )
            for item in experiment_design.get("samples", []) or []
            if isinstance(item, dict)
        ]
        user_assertions = user_assertion_service.extract(
            question,
            known_samples=known_samples,
        )
        valid_evidence_ids = {
            str(card.get("evidence_id") or "")
            for card in evidence_cards
            if card.get("evidence_id")
        }
        for card in agent_loop.get("new_evidence_cards", []) or []:
            if str(card.get("evidence_id") or "") in valid_evidence_ids:
                evidence_chain.append(evidence_card_service.to_evidence_chain(card))
        evidence_chain = evidence_card_service.attach_ids(evidence_chain, evidence_cards)
        evidence_conflicts = evidence_card_service.detect_conflicts(evidence_cards)
        available_evidence = {
            metric_schema_service.canonical_id(card.get("metric_id"))
            for card in evidence_cards
            if isinstance(card, dict) and card.get("metric_id")
        }
        available_evidence.update(
            str(item)
            for item in (
                (project_context.get("evidence_catalog") or {})
                .get("metric_index", {})
                .keys()
            )
        )
        if analysis_plan:
            selected_skill_references = bio_skill_reference_service.select_for_project(
                question=question,
                target_metrics=analysis_plan.get("target_metrics", []),
                intent=str(analysis_plan.get("intent") or "anomaly_investigation"),
                assay=str(assay_profile.get("assay") or ""),
                target_class=str(assay_profile.get("target_class") or ""),
                species_scope=str(
                    assay_profile.get("species")
                    or (project_context.get("config") or {}).get("species")
                    or ""
                ),
                available_evidence=available_evidence,
                limit=3,
            )
            analysis_plan["bio_skill_references"] = selected_skill_references
            analysis_plan["loaded_bio_skills"] = (
                bio_skill_reference_service.load_full_skills(
                    selected_skill_references,
                    max_skills=3,
                    max_chars=600,
                )
            )
            analysis_plan["skill_selection_stage"] = "post_assay_hard_filter"
        read_lineage_snapshot_key = project_snapshot_service.build_post_evidence_snapshot_key(
            project_version=project_version,
            selected_files=selected_evidence_files,
            evidence_cards=evidence_cards,
            quarantined_cards=quarantined_evidence_cards,
            evidence_status=evidence_status,
            evidence_conflicts=evidence_conflicts,
            user_assertions=user_assertions,
        )
        read_lineage_snapshot = project_snapshot_service.get(read_lineage_snapshot_key)
        read_lineage_snapshot_status = "hit" if read_lineage_snapshot else "miss"
        logger.info(
            "project_analysis stage=read_lineage_snapshot run_id=%s project=%s snapshot=%s",
            run_id,
            project_id,
            read_lineage_snapshot_status,
        )
        if read_lineage_snapshot is not None:
            read_lineage = read_lineage_snapshot.get("read_lineage", {}) or {}
        else:
            read_lineage = read_lineage_service.build(
                parsed_metrics=parsed_metrics,
                evidence_catalog=project_context.get("evidence_catalog") or {},
                assay_profile=assay_profile,
                quarantined_cards=quarantined_evidence_cards,
                evidence_cards=evidence_cards,
                evidence_status=evidence_status,
                selected_files=selected_evidence_files,
                evidence_conflicts=evidence_conflicts,
                user_assertions=user_assertions,
            )
            project_snapshot_service.set(
                read_lineage_snapshot_key,
                {
                    "read_lineage": read_lineage,
                    "metric_tables_ready": sorted(parsed_metrics.keys()),
                },
            )
        evidence_file_list = [str(path.relative_to(root)) for path in evidence_files]
        for observation in agent_loop.get("observations", []) or []:
            for matched_file in observation.get("matched_files", []) or []:
                if matched_file not in evidence_file_list:
                    evidence_file_list.append(matched_file)
        evidence_request_status = cls._build_evidence_request_status(
            analysis_plan=analysis_plan,
            evidence_files=evidence_file_list,
            file_summaries=file_summaries,
            evidence_chain=evidence_chain,
            evidence_status=evidence_status,
        )
        tool_diagnostics = cls._build_tool_diagnostics(
            parsed_metrics=parsed_metrics,
            evidence_chain=evidence_chain,
            project_context=project_context,
            analysis_plan=analysis_plan,
        )
        cause_graph = cls._build_cause_graph(
            question=question,
            analysis_plan=analysis_plan,
            evidence_chain=evidence_chain,
            tool_diagnostics=tool_diagnostics,
            project_context=project_context,
        )
        anomaly_summary = cls._build_anomaly_summary(evidence_chain)
        analysis_limits = cls._build_threshold_validation_warnings(evidence_chain)
        analysis_limits.extend(
            f"Experiment design unresolved: {item}"
            for item in experiment_design.get("warnings", []) or []
        )
        analysis_limits.extend(
            f"Required {assay_profile.get('assay', 'assay')} evidence missing: {item}"
            for item in assay_profile.get("missing_evidence", []) or []
        )
        if quarantined_evidence_cards:
            analysis_limits.append(
                f"{len(quarantined_evidence_cards)} evidence card(s) were quarantined because unit, range, formula, phase, or conflict validation failed."
            )
        for sample_lineage in read_lineage.get("samples", []) or []:
            for transition in sample_lineage.get("unexplained_breaks", []) or []:
                analysis_limits.append(
                    "Reads lineage break for "
                    f"{sample_lineage.get('sample', '-')}: "
                    f"{transition.get('from_stage')}={transition.get('from_value')} -> "
                    f"{transition.get('to_stage')}={transition.get('to_value')}; "
                    "both values are observed, but the intervening loss is not explained by current evidence."
                )
        differential_readiness = experiment_design.get("differential_analysis") or {}
        if not differential_readiness.get("ready"):
            analysis_limits.append(
                "Differential analysis is not supported by the current design: "
                + ", ".join(differential_readiness.get("reasons", []) or ["design unresolved"])
            )
        analysis_limits = list(dict.fromkeys(analysis_limits))
        automatic_findings.extend(cls._build_rule_based_findings(evidence_chain))
        automatic_findings = list(dict.fromkeys(automatic_findings))
        if "diagnostic" in question_types:
            automatic_findings.extend(cls._build_diagnostic_findings(evidence_chain))
            automatic_findings = list(dict.fromkeys(automatic_findings))
        for diagnostic in tool_diagnostics:
            summary = str(diagnostic.get("summary") or "").strip()
            if summary:
                automatic_findings.append(summary)
        automatic_findings = list(dict.fromkeys(automatic_findings))
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        comparison_tables = cls._build_comparison_tables(parsed_metrics)
        metric_priority = cls._build_metric_priority(planning_hints, question_type)
        next_actions = cls._apply_metric_priority_to_actions(
            cls._build_default_next_actions(question_type, warnings, automatic_findings),
            metric_priority,
        )
        if assay_profile.get("missing_evidence"):
            next_actions.append(
                "补充实验类型证据链缺失项: "
                + ", ".join(assay_profile.get("missing_evidence", [])[:8])
            )
        if not differential_readiness.get("ready"):
            next_actions.append(
                "在进行差异分析前补充并确认 condition、biological replicate、target、control_for 和 batch。"
            )
        next_actions = list(dict.fromkeys(next_actions))
        diagnosis_summary = cls._build_diagnosis_summary(
            question_type=question_type,
            comparison_tables=comparison_tables,
            automatic_findings=automatic_findings,
            warnings=warnings,
            next_actions=next_actions,
            evidence_chain=evidence_chain,
            cause_graph=cause_graph,
        )
        claims = claim_service.build_claims(
            evidence_cards=evidence_cards,
            cause_graph=cause_graph,
            analysis_limits=analysis_limits,
            next_actions=next_actions,
        )
        claim_validation = project_analysis_verifier_service.verify(
            evidence_cards=evidence_cards,
            claims=claims,
            project_context=project_context,
        )
        validated_claims = claim_validation.get("valid_claims", [])
        claim_layers = claim_service.build_render_layers(validated_claims)
        evidence_reasoning = evidence_reasoning_service.build(
            question=question,
            analysis_plan=analysis_plan,
            evidence_cards=evidence_cards,
            validated_claims=validated_claims,
            analysis_limits=analysis_limits,
            next_actions=next_actions,
            experiment_design=experiment_design,
            assay_profile=assay_profile,
            read_lineage=read_lineage,
            parsed_metrics=parsed_metrics,
            user_assertions=user_assertions,
            evidence_conflicts=evidence_conflicts,
        )
        # Phase 2: fact_packet is now assembled from canonical evidence_cards,
        # not from free-text diagnosis_summary strings.  This makes fact_packet
        # the single source of truth that verify_fact_packet() can structurally
        # check without any text parsing.
        fact_packet = evidence_card_service.build_fact_packet(
            evidence_cards,
            analysis_result={
                "validated_claims": validated_claims,
                "evidence_chain": evidence_chain,
            },
            question=question,
            project_id=project_id,
        )
        # Overlay priority 1: pipeline log errors take precedence over all speculation.
        #
        # Two triggers:
        #   A) Explicit: question_type is pipeline_failure / diagnostic / log
        #   B) Implicit: project produced NO QC evidence at all — the pipeline likely
        #      failed before generating output, so the log IS the only evidence.
        #
        # For trigger B we also do a fallback log scan here, because the question may
        # have been classified as "overview" (e.g. "项目诊断") and log files were
        # therefore never included in evidence_files / file_summaries.
        _no_qc_evidence = not fact_packet.get("project_evidence")
        _has_log_summaries = any(
            (fs.get("summary") or {}).get("kind") == "log"
            for fs in file_summaries
        )

        if _no_qc_evidence and not _has_log_summaries:
            # Emergency fallback: scan log files directly and append to file_summaries.
            _fallback_logs = find_log_files(root, limit=10)
            for _lf in _fallback_logs:
                try:
                    _snippet = read_log_snippet(_lf)
                    _log_sum = cls._summarize_text_evidence(_lf, _snippet)
                    _rel = str(_lf.relative_to(root)).replace("\\", "/")
                    file_summaries.append({
                        "file": _rel,
                        "type": "text",
                        "preview": _snippet,
                        "summary": _log_sum,
                    })
                except Exception:
                    pass

        _log_error_conclusions: list[dict[str, Any]] = []
        _should_use_log_evidence = (
            "pipeline_failure" in question_types
            or "diagnostic" in question_types
            or "log" in question_types
            or _no_qc_evidence  # No QC output is itself evidence of pipeline failure
        )
        if _should_use_log_evidence:
            for _fs in file_summaries:
                _summary = _fs.get("summary") or {}
                if _summary.get("kind") != "log":
                    continue
                _error_lines = _summary.get("error_lines") or []
                if not _error_lines:
                    continue
                _fname = str(_fs.get("file", "")).replace("\\", "/").split("/")[-1]
                for _err in _error_lines[:5]:
                    _log_error_conclusions.append({
                        "claim": f"[{_fname}] {_err[:400]}",
                        "evidence_ids": [],
                        "causal_level": "pipeline_error",
                        "confidence": "direct_log_evidence",
                    })
        if _log_error_conclusions:
            fact_packet["direct_conclusions"] = _log_error_conclusions
            fact_packet["pipeline_errors_found"] = True
            # Suppress hypothesis generation — log errors ARE the answer.
            reasoning_packet: dict[str, Any] = {
                "possible_causes": [],
                "ranked_causes": [],
                "hypothesis_comparison": [],
                "verification_plan": [],
                "evidence_against": [],
            }
        else:
            # Overlay priority 2: if validated_claims produced no direct_conclusions,
            # fall back to diagnosis_summary string conclusions so rendering is not empty.
            if not fact_packet.get("direct_conclusions") and diagnosis_summary.get("conclusions"):
                fact_packet["direct_conclusions"] = [
                    {"claim": c, "evidence_ids": [], "causal_level": "observation", "confidence": ""}
                    for c in diagnosis_summary.get("conclusions", [])[:4]
                ]
            reasoning_packet = {
                "possible_causes": diagnosis_summary.get("possible_causes", [])[:5],
                "ranked_causes": diagnosis_summary.get("ranked_causes", [])[:5],
                "hypothesis_comparison": diagnosis_summary.get("hypothesis_comparison", [])[:4],
                "verification_plan": diagnosis_summary.get("verification_plan", [])[:6],
                "evidence_against": diagnosis_summary.get("evidence_against", [])[:6],
            }
        analysis_result_layers = {
            "fact_packet": fact_packet,
            "reasoning_packet": reasoning_packet,
        }
        confidence = cls._build_confidence(
            evidence_status,
            automatic_findings,
            analysis_plan=analysis_plan,
            evidence_request_status=evidence_request_status,
            claim_validation=claim_validation,
            cause_graph=cause_graph,
        )
        trace = {
            "run_id": run_id,
            "question_type": question_type,
            "question_tags": question_types,
            "duration_ms": duration_ms,
            "evidence_attempted": len(evidence_files),
            "evidence_succeeded": sum(1 for item in evidence_status if item.get("status") == "ok"),
            "warning_count": len(warnings),
            "evidence_request_count": len(evidence_request_status),
            "evidence_requests_found": sum(1 for item in evidence_request_status if item.get("status") == "found"),
            "evidence_requests_partial": sum(1 for item in evidence_request_status if item.get("status") == "partial"),
            "evidence_requests_missing": sum(1 for item in evidence_request_status if item.get("status") == "missing"),
            "tool_diagnostic_count": len(tool_diagnostics),
            "cause_graph_node_count": len(cause_graph.get("nodes", []) or []),
            "ranked_cause_count": len(cause_graph.get("ranked_causes", []) or []),
            "agent_loop_round_count": agent_loop.get("round_count", 0),
            "evidence_card_count": len(evidence_cards),
            "quarantined_evidence_card_count": len(quarantined_evidence_cards),
            "validated_claim_count": len(validated_claims),
            "invalid_claim_count": claim_validation.get("invalid_claim_count", 0),
            "evidence_conflict_count": len(evidence_conflicts),
            "experiment_design_warning_count": len(experiment_design.get("warnings", []) or []),
            "assay_missing_evidence_count": len(assay_profile.get("missing_evidence", []) or []),
            "analysis_cache": evidence_snapshot_status,
            "snapshot": {
                "evidence": evidence_snapshot_status,
                "assay_profile": assay_snapshot_status,
                "read_lineage": read_lineage_snapshot_status,
            },
            "status": "warning" if warnings else "ok",
        }
        project_match = {
            "project_id": project_id,
            "project_root": str(root),
        }
        analysis_payload = {
            "project_id": project_id,
            "question": question,
            "question_type": question_type,
            "analysis_plan": analysis_plan,
            "evidence_request_status": evidence_request_status,
            "evidence_files": evidence_file_list,
            "file_summaries": file_summaries,
            "project_context": project_context,
            "experiment_design": experiment_design,
            "assay_profile": assay_profile,
            "automatic_findings": automatic_findings,
            "tool_diagnostics": tool_diagnostics,
            "cause_graph": cause_graph,
            "competing_hypotheses": cause_graph.get("competing_hypotheses", []),
            "evidence_chain": evidence_chain,
            "evidence_cards": evidence_cards,
            "evidence_card_validation": evidence_card_validation,
            "quarantined_evidence_cards": quarantined_evidence_cards,
            "evidence_conflicts": evidence_conflicts,
            "user_assertions": user_assertions,
            "read_lineage": read_lineage,
            "evidence_reasoning": evidence_reasoning,
            "fact_packet": fact_packet,
            "reasoning_packet": reasoning_packet,
            "agent_loop": agent_loop,
            "claims": claims,
            "validated_claims": validated_claims,
            "claim_validation": claim_validation,
            "claim_layers": claim_layers,
            "anomaly_summary": anomaly_summary,
            "warnings": warnings,
            "analysis_limits": analysis_limits,
            "confidence": confidence,
            "metric_priority": metric_priority,
            "metric_tables_ready": sorted(parsed_metrics.keys()),
            "report_mode": report_mode,
            "structured_experience_rules": (planning_hints or {}).get("structured_experience_rules", []),
        }
        existing_report = project_context.get("html_report") or {}

        logger.info(
            "project_analysis run_id=%s project=%s question_type=%s evidence=%s warnings=%s analysis_cache=%s snapshot=%s/%s/%s duration_ms=%.2f",
            run_id,
            project_id,
            question_type,
            len(evidence_files),
            len(warnings),
            evidence_snapshot_status,
            evidence_snapshot_status,
            assay_snapshot_status,
            read_lineage_snapshot_status,
            duration_ms,
        )

        return {
            "run_id": run_id,
            "project_version": project_version,
            "trace": trace,
            "project_id": project_id,
            "project_root": str(root),
            "project_match": project_match,
            "question": question,
            "question_type": question_type,
            "question_tags": question_types,
            "analysis_plan": analysis_plan,
            "evidence_request_status": evidence_request_status,
            "confidence": confidence,
            "planning_hints": planning_hints or {},
            "analysis_cache": evidence_snapshot_status,
            "metric_priority": metric_priority,
            "read_plan": [
                "先识别问题类型",
                f"优先关注指标顺序: {' -> '.join(metric_priority)}",
                "优先读取项目摘要与统计表",
                "再按问题类型补读对应证据文件",
                "最后基于证据输出结论与下一步排查建议",
            ],
            "project_context": project_context,
            "experiment_design": experiment_design,
            "assay_profile": assay_profile,
            "pre_analysis_steps": [
                "Read samplelist to identify samples and input FASTQ files.",
                "Read config.yaml to identify pipeline parameters and output conventions.",
                "Read report README files to load metric interpretation guidance.",
                "Summarize the existing project HTML report only for project-level report summary questions.",
                "Parse QC, alignment, FRiP, peak, correlation and motif evidence as supporting checks.",
            ],
            "report_mode": report_mode,
            "report_source": existing_report.get("file", ""),
            "stage_names": sorted({path.parts[-2] if len(path.parts) >= 2 else path.name for path in evidence_files}),
            "project_file_count": (project_context.get("evidence_catalog") or {}).get(
                "file_count",
                len(list_project_files(root)),
            ),
            "evidence_files": evidence_file_list,
            "evidence_status": evidence_status,
            "file_summaries": file_summaries,
            "parsed_metrics": parsed_metrics,
            "metric_tables_ready": sorted(parsed_metrics.keys()),
            "comparison_tables": comparison_tables,
            "diagnosis_summary": diagnosis_summary,
            "evidence_chain": evidence_chain,
            "evidence_cards": evidence_cards,
            "evidence_card_validation": evidence_card_validation,
            "quarantined_evidence_cards": quarantined_evidence_cards,
            "evidence_conflicts": evidence_conflicts,
            "user_assertions": user_assertions,
            "read_lineage": read_lineage,
            "evidence_reasoning": evidence_reasoning,
            "fact_packet": fact_packet,
            "reasoning_packet": reasoning_packet,
            "agent_loop": agent_loop,
            "claims": claims,
            "validated_claims": validated_claims,
            "claim_validation": claim_validation,
            "claim_layers": claim_layers,
            "anomaly_summary": anomaly_summary,
            "tool_diagnostics": tool_diagnostics,
            "cause_graph": cause_graph,
            "automatic_findings": automatic_findings,
            "findings": automatic_findings,
            "warnings": warnings,
            "analysis_limits": analysis_limits,
            "next_actions": next_actions,
            "report": existing_report.get("text_excerpt", "") if report_mode == "existing_html_report_summary" and existing_report else cls._render_report(analysis_payload),
            "snapshot": {
                "evidence": evidence_snapshot_status,
                "assay_profile": assay_snapshot_status,
                "read_lineage": read_lineage_snapshot_status,
            },
            "_internal_workflow_context": internal_workflow_context,
        }
