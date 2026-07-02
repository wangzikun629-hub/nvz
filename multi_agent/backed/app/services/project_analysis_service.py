from __future__ import annotations

import copy
import hashlib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from multi_agent.backed.app.config.settings import settings
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


from multi_agent.backed.app.services.project_analysis_constants import (
    TABLE_PRIORITY,
    STRUCTURED_TABLE_FILES,
    PROFESSIONAL_RULES,
    RNASEQ_SEMANTIC_OVERRIDES,
    QUESTION_FILE_HINTS,
    TARGET_METRIC_FILE_HINTS,
    SECONDARY_TEXT_HINTS,
    PIPELINE_FAILURE_TERMS,
    DIAGNOSTIC_TERMS,
    INTERNAL_WORKFLOW_TERMS,
)
from multi_agent.backed.app.services.project_parse_cache import project_parse_cache
from multi_agent.backed.app.services.project_file_parser_service import (
    project_file_parser_service,
    resolve_table_kind,
    progress_stage_for_evidence,
    safe_float,
    read_correlation_rows,
    looks_like_text_file,
    extract_motif_sample_name,
)
from multi_agent.backed.app.services.project_context_builder_service import project_context_builder_service
from multi_agent.backed.app.services.project_cause_analysis_service import project_cause_analysis_service
from multi_agent.backed.app.services.project_file_discovery_service import (
    discover_file_role_assignments,
    to_candidate_paths,
)


class ProjectAnalysisService:
    # Phase 1 文件发现探索（含代码语义解析 agent 的模型增强分支）内部串联了多次
    # 同步模型调用，理论最坏耗时可能远超 analyze_project_data 整体 25s 预算
    # （PROJECT_ANALYSIS_TIMEOUT_SECONDS，见 business_agent/runtime_service.py）。
    # 这里单独给它一个更短的硬预算，超时即放弃候选、回退到关键词命中的证据，
    # 而不是拖累整个分析阶段一起超时。可通过环境变量覆盖，便于线上按需调整。
    _FILE_DISCOVERY_BUDGET_SECONDS = float(
        os.environ.get("PROJECT_FILE_DISCOVERY_BUDGET_SECONDS")
        or getattr(settings, "FILE_DISCOVERY_STAGE_TIMEOUT_SECONDS", 8.0)
    )

    @classmethod
    def classify_question(cls, question: str) -> str:
        return cls._infer_question_types(question)[0]

    @classmethod
    def _infer_question_types(cls, question: str) -> list[str]:
        normalized = (question or "").lower()
        # Pipeline failure questions are a special early-exit path:
        # ONLY read log files, skip all QC metric analysis entirely.
        # Also match combinations like "失败的原因" (token contains 的 between terms).
        _is_pipeline_failure = any(token in normalized for token in PIPELINE_FAILURE_TERMS)
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
        return any(token in normalized_question for token in DIAGNOSTIC_TERMS)

    # 各实验类型对应的标准指标集（与 AssayAnalysisService.PROFILES 保持一致）
    _ASSAY_DEFAULT_METRICS: dict[str, list[str]] = {
        "cuttag": [
            "sequencing_depth", "mapping_rate_percent", "mt_rate_percent",
            "nrf", "pbc1", "pbc2", "peak_count", "frip_ratio", "correlation",
        ],
        "chipseq": [
            "sequencing_depth", "mapping_rate_percent", "duplicate_rate_percent",
            "peak_count", "frip_ratio", "correlation",
        ],
        "cutrun": [
            "sequencing_depth", "mapping_rate_percent", "mt_rate_percent",
            "nrf", "pbc1", "pbc2", "peak_count", "frip_ratio", "correlation",
        ],
        "atacseq": [
            "sequencing_depth", "mapping_rate_percent", "mt_rate_percent",
            "duplicate_rate_percent", "tss_enrichment", "fragment_size",
            "peak_count", "frip_ratio", "correlation",
        ],
        "rnaseq": [
            "sequencing_depth", "mapping_rate_percent", "unique_mapping_rate_percent",
            "duplicate_rate_percent", "mrna_ratio_percent", "rrna_ratio_percent",
            "silva_total_ratio_percent", "detected_gene_count", "correlation",
        ],
        "generic": [
            "sequencing_depth", "mapping_rate_percent",
        ],
    }

    @classmethod
    def _detect_assay_early(cls, project_context: dict[str, Any]) -> str:
        """在读取证据文件之前，从 config 字段快速推断实验类型。

        使用与 AssayAnalysisService._assay() 完全相同的字段和规则，
        确保早期检测结果与后续完整 assay_profile 一致。
        """
        config = project_context.get("config") or {}
        assay_raw = " ".join(
            str(config.get(key) or "")
            for key in ("assay", "project_type", "Sequencing", "library_type")
        )
        return assay_analysis_service._assay(assay_raw)

    @classmethod
    def _select_evidence_files(
        cls,
        project_root: Path,
        question_types: list[str],
        max_evidence_files: int,
        planning_hints: dict[str, Any] | None = None,
        evidence_catalog: dict[str, Any] | None = None,
        assay_type: str = "generic",
        question: str = "",
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
            # 若 question_type 也没命中具体指标（如 overview / diagnostic），
            # 使用早期检测到的实验类型标准指标集作为兜底，确保读到正确的文件。
            if not target_metrics:
                target_metrics = list(
                    cls._ASSAY_DEFAULT_METRICS.get(assay_type, cls._ASSAY_DEFAULT_METRICS["generic"])
                )
        # 2026-07-02 修复：question_type 分类表 / assay 默认指标集都是有限枚举，问题里
        # 直接点名一个不在这两张表里的已注册指标（比如 RNA-seq 的 Silva_total_ratio）时，
        # 上面两条路径永远凑不出这个指标，导致该指标既不会被当作目标、也不会触发下面
        # Phase 1 的文件发现探索兜底（探索只在 unresolved_metrics 非空时才跑），
        # 表现为该指标对应的证据文件完全不会被读取。这里把问题文本里字面提及的、
        # 已在 metric_schema_service 注册的指标补进 target_metrics（去重），
        # 不影响已有 question_type/assay 命中的指标，纯增量。
        literal_metrics = [
            metric_schema_service.canonical_id(metric_id)
            for metric_id in metric_schema_service.detect_metrics_in_text(question)
        ]
        for metric_id in literal_metrics:
            if metric_id not in target_metrics:
                target_metrics.append(metric_id)
        # 逐指标记录"关键词命中的候选文件里，有没有至少一个能被 resolve_table_kind
        # 识别的真实证据文件"，而不是只看 `ordered` 整体是否为空——避免像 SilvaBlast
        # 这类目录级关键词子串命中了不相关文件（如原始 blast 明细）时，把这个"假命中"
        # 当成"该指标已有证据"，从而挡住下面 Phase 1 探索 agent 对这个指标的兜底
        # （详见 docs/project_analysis_agent_upgrade_plan.md 待办第 1 点）。
        metrics_with_parseable_evidence: set[str] = set()

        def _mark_if_parseable(metric_id: str, path: Path) -> None:
            if not metric_id:
                return
            try:
                if resolve_table_kind(path) is not None:
                    metrics_with_parseable_evidence.add(metric_id)
            except Exception:
                pass

        if evidence_catalog and target_metrics:
            for metric in target_metrics:
                canonical_metric = metric_schema_service.canonical_id(metric)
                catalog_paths = evidence_catalog_service.paths_for_metrics(
                    project_root,
                    evidence_catalog,
                    [metric],
                    limit=max(4, min(max_evidence_files, 20)),
                )
                for path in catalog_paths:
                    _mark_if_parseable(canonical_metric, path)
                ordered.extend(catalog_paths)
        for metric in analysis_plan.get("target_metrics", []) or []:
            normalized_metric = str(metric or "").strip().lower()
            canonical_metric = metric_schema_service.canonical_id(metric)
            for hint in TARGET_METRIC_FILE_HINTS.get(normalized_metric, ()):
                hint_paths = find_files(project_root, [hint], limit=2)
                for path in hint_paths:
                    _mark_if_parseable(canonical_metric, path)
                ordered.extend(hint_paths)
        prioritized_hints = [
            str(item).strip()
            for item in (planning_hints or {}).get("prioritized_evidence_hints", [])
            if str(item).strip()
        ]
        for hint in prioritized_hints:
            ordered.extend(find_files(project_root, [hint], limit=2))
        for question_type in question_types:
            for hint in QUESTION_FILE_HINTS.get(question_type, []):
                ordered.extend(find_files(project_root, [hint], limit=3))
        for question_type, hints in SECONDARY_TEXT_HINTS.items():
            if question_type in question_types:
                ordered.extend(find_files(project_root, hints, limit=2))

        # Phase 1（project_analysis_agent_upgrade_plan.md 3 节）：文件发现探索兜底。
        # 关键约束（2026-07-01 修订）：按目标指标逐个判断——只有当 evidence_catalog /
        # TARGET_METRIC_FILE_HINTS 都没有为这个指标命中任何"能被 resolve_table_kind
        # 识别"的候选文件时，才对这个指标触发探索；不再要求全局 `ordered` 整体为空，
        # 因为 QUESTION_FILE_HINTS/SECONDARY_TEXT_HINTS/prioritized_hints 是按问题类型
        # 而非按指标匹配的宽泛关键词，它们命中的无关文件不应该阻止其他指标去探索。
        # 已调好的项目类型/指标只要关键词命中了真正可解析的文件，行为和之前完全一致。
        # 探索产出仅是候选路径，是否真正生成 evidence_card 仍由下游既有的 parser +
        # strict_formula_recalculation 校验决定，这里不做也不能做真值判断。
        # 2026-07-02 修订：只对"注册表里确实有 schema"的指标触发探索。像
        # `Silva_total_ratio(%)` 这种完全没登记的新指标，metric_schema_service.get()
        # 返回空 schema，_heuristic_match 里 tokens 必然为空、直接 continue，纯属
        # 陪跑；但 discover_file_role_assignments 内部的代码语义解析 agent
        # （analyze_project_workflow_scripts）不看 target_metrics，只要 unresolved_metrics
        # 非空就会对项目里最多 6 个 SOP 脚本逐个跑一遍，静态提取无结果时还会顺带触发一次
        # 模型调用——对完全未注册的指标这一整套探索没有任何收益，纯粹浪费
        # `analyze_project_data` 的 25s 预算（详见 docs/project_analysis_agent_upgrade_plan.md
        # "Silva_total_ratio 排查暴露的两个真实 bug" 待办第 3 点的后续修复记录）。
        unresolved_metrics = [
            metric for metric in target_metrics
            if metric_schema_service.canonical_id(metric) not in metrics_with_parseable_evidence
            and metric_schema_service.get(metric)
        ]
        if unresolved_metrics and "pipeline_failure" not in question_types:
            try:
                # 硬预算兜底：discover_file_role_assignments 内部串联了代码语义解析
                # agent（最多 6 个脚本 × 25s 模型超时）+ 文件发现 agent（20s 模型超时），
                # 理论最坏耗时远超 analyze_project_data 整体 25s 的预算。这里单独包一层
                # 更短的硬超时，让它在预算耗尽前主动放弃并回退到"无候选"，而不是拖着
                # 外层 asyncio.wait_for 一起超时（那样线程还会在后台空跑到自己的超时
                # 才结束，白白占用资源）。
                # 注意：不能用 `with ThreadPoolExecutor(...) as executor:`——上下文管理器
                # 退出时会调用 `shutdown(wait=True)`，即使 future.result() 已经因超时
                # 提前放弃等待，`with` 块本身还是会在这里重新阻塞到线程跑完，等于白做。
                # 必须手动创建 executor，并在拿到（或放弃等待）结果后用
                # `shutdown(wait=False)` 不阻塞地丢开这个线程，让它自己在后台跑完/超时。
                _fd_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="file-discovery")
                _fd_future = _fd_executor.submit(
                    discover_file_role_assignments,
                    project_root,
                    unresolved_metrics,
                    exclude_paths=set(),
                )
                try:
                    assignments = _fd_future.result(timeout=cls._FILE_DISCOVERY_BUDGET_SECONDS)
                except FuturesTimeoutError:
                    assignments = []
                    logger.warning(
                        "project_analysis stage=file_discovery status=budget_exceeded root=%s "
                        "budget_seconds=%.1f unresolved_metrics=%s",
                        str(project_root),
                        cls._FILE_DISCOVERY_BUDGET_SECONDS,
                        unresolved_metrics,
                    )
                finally:
                    _fd_executor.shutdown(wait=False)
                ordered.extend(to_candidate_paths(assignments))
            except Exception:
                logger.warning(
                    "project_analysis stage=file_discovery status=failed root=%s",
                    str(project_root),
                    exc_info=True,
                )

        if "log" in question_types or "diagnostic" in question_types:
            ordered.extend(find_log_files(project_root, limit=5))
        for name in TABLE_PRIORITY:
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
    def _explain_pipeline_error_line(error_line: str) -> str:
        raw = error_line or ""
        normalized = (error_line or "").lower()
        if project_file_parser_service._is_non_error_log_stat_line(normalized):
            return ""
        called_process = re.search(r'calledprocesserror\s+in\s+file\s+"([^"]+)",\s+line\s+(\d+)', raw, re.IGNORECASE)
        if called_process:
            rule_file = called_process.group(1).replace("\\", "/").split("/")[-1]
            line_no = called_process.group(2)
            return f"Snakemake 调用的外部命令返回非 0 状态，失败位置在规则文件 {rule_file} 第 {line_no} 行；这能定位失败规则，但真正根因通常还要看该规则自己的运行日志。"
        rule_match = re.search(r"error\s+in\s+rule\s+([^:\s]+)", raw, re.IGNORECASE)
        if rule_match:
            return f"Snakemake 显示失败规则是 {rule_match.group(1)}，说明流程在这个分析步骤中止；需要继续查看该规则对应的详细日志确认根因。"
        detail_log = re.search(r"\blog:\s*(.+?)(?:\s*\(check log file\(s\).*)?$", raw, re.IGNORECASE)
        if detail_log:
            return f"这行给出了真正的详细错误日志位置：{detail_log.group(1).strip()}；应继续查看该文件里的 R/命令原始报错。"
        if "ruleexception" in normalized:
            return "这是 Snakemake 汇总出的规则异常，表示某个 rule 执行失败；它本身不是最底层根因，需要结合失败 rule 和详细 log 文件判断。"
        if "exiting because a job execution failed" in normalized:
            return "Snakemake 因某个作业失败而退出；这行是流程终止说明，不是最底层错误原因。"
        if "traceback" in normalized:
            return "这是 Python 程序异常调用栈的开头，说明某个脚本执行时抛出了异常，需要结合后续 ERROR 或 Exception 行定位具体原因。"
        if "missing" in normalized and any(token in normalized for token in ("index", "idx", "reference", "adapter")):
            return "日志提示缺少索引、参考文件或接头索引等必要输入，流程无法继续完成对应步骤。"
        if any(token in normalized for token in ("no such file", "not found", "cannot find", "can't find")):
            return "日志提示找不到所需文件或路径，通常是输入文件缺失、路径配置不正确或上游步骤没有生成结果。"
        if any(token in normalized for token in ("permission denied", "access denied")):
            return "日志提示权限不足，当前运行用户可能没有读取、写入或执行相关文件的权限。"
        if any(token in normalized for token in ("out of memory", "memoryerror", "killed")):
            return "日志提示内存不足或进程被系统终止，常见于数据量较大但任务资源配置不够。"
        if any(token in normalized for token in ("exception", "fatal", "critical", "abort")):
            return "日志显示程序发生严重异常并中止，原始错误内容就是判断失败原因的主要依据。"
        if "error" in normalized:
            return "日志明确标记 ERROR，表示该步骤执行失败；具体原因需要优先按这条原始错误内容判断。"
        return "这是一条被日志解析器识别出的异常相关信息，可作为排查流程失败原因的直接线索。"

    @classmethod
    def _analyze_pipeline_failure_logs(
        cls,
        *,
        project_id: str,
        question: str,
        root: Path,
        run_id: str,
        question_types: list[str],
        analysis_plan: dict[str, Any],
        planning_hints: dict[str, Any] | None,
        started_at: float,
        max_evidence_files: int,
    ) -> dict[str, Any]:
        log_files = find_log_files(root, limit=max_evidence_files, strict_log_suffix=True)
        file_summaries: list[dict[str, Any]] = []
        evidence_status: list[dict[str, Any]] = []
        warnings: list[str] = []
        direct_conclusions: list[dict[str, Any]] = []

        for log_file in log_files:
            relative = str(log_file.relative_to(root)).replace("\\", "/")
            file_started_at = perf_counter()
            try:
                preview = read_log_snippet(log_file)
                summary = project_file_parser_service.summarize_text_evidence(log_file, preview)
                file_summaries.append(
                    {
                        "file": relative,
                        "type": "text",
                        "preview": preview,
                        "summary": summary,
                    }
                )
                evidence_status.append(
                    {
                        "file": relative,
                        "status": "ok",
                        "type": "log",
                        "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                    }
                )
                for error_line in (summary.get("error_lines") or [])[:5]:
                    error_text = str(error_line)
                    direct_conclusions.append(
                        {
                            "claim": f"[{log_file.name}] {error_text[:400]}",
                            "explanation": cls._explain_pipeline_error_line(error_text),
                            "evidence_ids": [],
                            "causal_level": "pipeline_error",
                            "confidence": "direct_log_evidence",
                        }
                    )
            except Exception as exc:
                warnings.append(f"{relative} 读取失败: {exc}")
                file_summaries.append({"file": relative, "type": "error", "error": str(exc)})
                evidence_status.append(
                    {
                        "file": relative,
                        "status": "error",
                        "type": "log",
                        "error": str(exc),
                        "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                    }
                )

        if not direct_conclusions:
            if log_files:
                direct_conclusions = [
                    {
                        "claim": "已读取项目 .log 日志文件，未在其中检测到明显的 error / exception / traceback 行；请检查日志末尾的退出状态或 warning 行。",
                        "evidence_ids": [],
                        "causal_level": "observation",
                        "confidence": "log_no_error_found",
                    }
                ]
            else:
                direct_conclusions = [
                    {
                        "claim": "未在项目目录中找到 .log 日志文件，无法仅通过日志判断项目失败或报错原因。",
                        "evidence_ids": [],
                        "causal_level": "observation",
                        "confidence": "no_log_files",
                    }
                ]

        fact_packet = {
            "project_id": project_id,
            "question": question,
            "direct_conclusions": direct_conclusions,
            "pipeline_errors_found": bool(
                direct_conclusions
                and direct_conclusions[0].get("confidence") == "direct_log_evidence"
            ),
            "project_evidence": [],
        }
        reasoning_packet: dict[str, Any] = {
            "possible_causes": [],
            "ranked_causes": [],
            "hypothesis_comparison": [],
            "verification_plan": [],
            "evidence_against": [],
        }
        evidence_file_list = [str(path.relative_to(root)).replace("\\", "/") for path in log_files]
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        report_lines = [
            "日志文件：" + ("、".join(evidence_file_list) if evidence_file_list else "未找到 .log"),
            "错误信息：",
        ]
        report_lines.extend(f"- {item['claim']}" for item in direct_conclusions)
        if fact_packet["pipeline_errors_found"]:
            report_lines.append("解释：")
            for item in direct_conclusions:
                report_lines.append(f"- {item['claim']}：{item.get('explanation') or cls._explain_pipeline_error_line(str(item.get('claim') or ''))}")
        elif log_files:
            report_lines.append("解释：已读取 .log，但未发现明显错误行。")
        else:
            report_lines.append("解释：未找到 .log，无法仅通过日志判断失败原因。")

        trace = {
            "run_id": run_id,
            "question_type": "pipeline_failure",
            "question_tags": question_types,
            "duration_ms": duration_ms,
            "evidence_attempted": len(log_files),
            "evidence_succeeded": sum(1 for item in evidence_status if item.get("status") == "ok"),
            "warning_count": len(warnings),
            "agent_loop_round_count": 0,
            "status": "warning" if warnings else "ok",
            "log_only_short_circuit": True,
        }

        return {
            "run_id": run_id,
            "project_version": f"pipeline-log-v2:{hashlib.sha1(str(root).encode('utf-8', errors='ignore')).hexdigest()[:16]}",
            "trace": trace,
            "project_id": project_id,
            "project_root": str(root),
            "project_match": {"project_id": project_id, "project_root": str(root)},
            "question": question,
            "question_type": "pipeline_failure",
            "question_tags": question_types,
            "analysis_plan": analysis_plan,
            "evidence_request_status": [],
            "confidence": 1.0 if fact_packet["pipeline_errors_found"] else 0.4,
            "planning_hints": planning_hints or {},
            "analysis_cache": "skipped_pipeline_failure_log_only",
            "metric_priority": [],
            "read_plan": ["只读 .log，提取错误行"],
            "project_context": {},
            "experiment_design": {},
            "assay_profile": {},
            "pre_analysis_steps": ["Read .log files only for pipeline failure diagnosis."],
            "report_mode": "pipeline_failure_log_only",
            "report_source": "",
            "stage_names": sorted({path.parts[-2] if len(path.parts) >= 2 else path.name for path in log_files}),
            "project_file_count": len(log_files),
            "evidence_files": evidence_file_list,
            "evidence_status": evidence_status,
            "file_summaries": file_summaries,
            "parsed_metrics": {},
            "metric_tables_ready": [],
            "comparison_tables": [],
            "diagnosis_summary": {"conclusions": [item["claim"] for item in direct_conclusions]},
            "evidence_chain": [],
            "evidence_cards": [],
            "evidence_card_validation": {"valid_cards": [], "quarantined_evidence": []},
            "quarantined_evidence_cards": [],
            "evidence_conflicts": [],
            "user_assertions": [],
            "read_lineage": {},
            "evidence_reasoning": {},
            "fact_packet": fact_packet,
            "reasoning_packet": reasoning_packet,
            "agent_loop": {"round_count": 0, "observations": [], "evidence_cards": [], "new_evidence_cards": []},
            "claims": [],
            "validated_claims": [],
            "claim_validation": {"valid_claims": [], "invalid_claim_count": 0},
            "claim_layers": {},
            "anomaly_summary": {"critical": [], "warning": []},
            "tool_diagnostics": [],
            "cause_graph": {},
            "automatic_findings": [item["claim"] for item in direct_conclusions],
            "findings": [item["claim"] for item in direct_conclusions],
            "warnings": warnings,
            "analysis_limits": [],
            "next_actions": [],
            "report": "\n".join(report_lines),
            "snapshot": {
                "evidence": "skipped_pipeline_failure_log_only",
                "assay_profile": "skipped_pipeline_failure_log_only",
                "read_lineage": "skipped_pipeline_failure_log_only",
            },
            "_internal_workflow_context": "",
        }

    @classmethod
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
            sample_value = safe_float(sample_value_raw)
            peer_values = [safe_float(item.get(field)) for item in peer_rows]
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

    @classmethod
    def _rule_severity(cls, rule: dict[str, Any], value: float | None) -> str:
        if value is None:
            return "unknown"
        for severity in ("critical", "warning"):
            threshold = rule.get(severity) or {}
            op = threshold.get("op")
            threshold_value = safe_float(threshold.get("value"))
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
        trust_level_override: str | None = None,
    ) -> dict[str, Any]:
        rule = dict(PROFESSIONAL_RULES.get(metric_key, {}))
        schema = metric_schema_service.get(metric_key)
        if schema:
            rule.setdefault("label", schema.get("label", metric_key))
            rule.setdefault("unit", schema.get("unit", ""))
            rule.setdefault("definition", schema.get("formula", ""))
            rule.setdefault("denominator", schema.get("denominator", ""))
        if semantic_overrides:
            rule.update(semantic_overrides)
            # None 值表示"移除该阈值"（如 RNA-seq unique_mapping 无数值阈值）
            for _k in ("warning", "critical"):
                if rule.get(_k) is None:
                    rule.pop(_k, None)
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
            and safe_float(threshold_rule[level].get("value")) is not None
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

        # project_analysis_phase1.5_auto_promotion_revision.md §10：trust_level 是独立于
        # evidence_grade/conclusion_strength 的信任分级，专供 answer_quality_service 的
        # evidence_coverage 按权重计分（而不是像原来那样按条目数一视同仁）。默认从既有的
        # formula_verified/category 派生；`trust_level_override` 用于脚本公式转正命中时
        # （见 project_file_parser_service._apply_script_formula_trust_upgrade）显式升级为
        # "recalculated"，即使这条证据本身来自字段发现层猜测的列名。
        if trust_level_override:
            trust_level = trust_level_override
        elif formula_verified:
            trust_level = "recalculated"
        elif category == "FieldDiscovery":
            trust_level = "screening_signal"
        else:
            trust_level = "display_only"
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
            "trust_level": trust_level,
            "interpretation": rule.get("interpretation", ""),
            "downstream_impact": rule.get("downstream_impact", ""),
        }

    @classmethod
    def _safe_list_exploratory_observations(cls, project_id: str) -> list[dict[str, Any]]:
        """Phase 1.5：候选指标队列的影子层观测，供 reasoning_packet.exploratory_observations 用。

        任何异常都不能影响主分析流程，静默降级为空列表。
        """
        try:
            from multi_agent.backed.app.services.business_agent.candidate_metric_service import (
                list_exploratory_observations,
            )

            return list_exploratory_observations(project_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "project_analysis stage=exploratory_observations status=failed project=%s",
                project_id,
                exc_info=True,
            )
            return []

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
        qc_source = cls._source_file_for_metric(file_summaries, "readsqc.xls", "statistic_reads.xls")
        alignment_source = cls._source_file_for_metric(file_summaries, "alignmentqc.xls", "aligentqc.xls")
        spikein_source = cls._source_file_for_metric(file_summaries, "spikein")
        # 根据 project_context 提前检测 assay，用于后续 RNA-seq 语义覆盖
        _assay_early = cls._detect_assay_early(project_context or {})
        frip_source = cls._source_file_for_metric(file_summaries, "frip")
        correlation_source = cls._source_file_for_metric(
            file_summaries, "spearman_corr_readcounts.tab", "correlation_summary"
        )
        rnaseq_reads_class_source = cls._source_file_for_metric(file_summaries, "rrna_globin_stat")
        rnaseq_gene_exp_source = cls._source_file_for_metric(file_summaries, "exprange")
        organelle_semantics = project_context_builder_service.organelle_semantics(project_context)

        for item in parsed_metrics.get("qc", []) or []:
            sample = item.get("sample") or "-"
            chain.append(
                cls._build_rule_entry(
                    metric_key="sequencing_depth",
                    category="ReadsQC",
                    sample=sample,
                    value=safe_float(item.get("clean_read_count")),
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
                        value=safe_float(item.get(key)),
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
                        value=safe_float(item.get("clean_read_retention_percent")),
                        source_file=qc_source,
                        source_field="Clean Reads",
                        rule_source=metric_rule_sources.get("clean_read_retention_percent"),
                    )
                )
            # RNA-seq Statistic_Reads.xls 中的 Dup(%) 字段
            if item.get("duplicate_rate_percent") is not None:
                chain.append(
                    cls._build_rule_entry(
                        metric_key="duplicate_rate_percent",
                        category="ReadsQC",
                        sample=sample,
                        value=safe_float(item.get("duplicate_rate_percent")),
                        source_file=qc_source,
                        source_field="Dup(%)",
                        rule_source=metric_rule_sources.get("duplicate_rate_percent"),
                    )
                )

        # ── RNA-seq Reads 组成分析 (Samples.rRNA_Globin_stat.xls) ─────────
        for item in parsed_metrics.get("rnaseq_reads_class", []) or []:
            sample = item.get("sample") or "-"
            for key, source_field in (
                ("mrna_ratio_percent", "mRNA_ratio(%)"),
                ("rrna_ratio_percent", "rRNA_ratio(%)"),
                ("exon_ratio_percent", "mRNA_Exon_ratio(%)"),
                ("intronic_ratio_percent", "mRNA_Intronic_ratio(%)"),
                ("intergenic_ratio_percent", "Intergenic_ratio(%)"),
            ):
                if item.get(key) is not None:
                    chain.append(
                        cls._build_rule_entry(
                            metric_key=key,
                            category="RNAseqReadsClass",
                            sample=sample,
                            value=safe_float(item.get(key)),
                            source_file=rnaseq_reads_class_source,
                            source_field=source_field,
                            rule_source=metric_rule_sources.get(key),
                        )
                    )

        # ── RNA-seq Silva rRNA 污染 (SilvaBlast/<sample>.stat.xls) ─────────
        silva_source = cls._source_file_for_metric(file_summaries, "silva", ".stat.xls")
        for item in parsed_metrics.get("rnaseq_silva", []) or []:
            sample = item.get("sample") or "-"
            if item.get("silva_total_ratio_percent") is not None:
                chain.append(
                    cls._build_rule_entry(
                        metric_key="silva_total_ratio_percent",
                        category="RNAseqSilva",
                        sample=sample,
                        value=safe_float(item.get("silva_total_ratio_percent")),
                        source_file=silva_source,
                        source_field="Silva_total_ratio(%)",
                        rule_source=metric_rule_sources.get("silva_total_ratio_percent"),
                    )
                )

        # ── RNA-seq 基因表达分布 (Samples.ExpRange.xls) ───────────────────
        for item in parsed_metrics.get("rnaseq_gene_exp", []) or []:
            sample = item.get("sample") or "-"
            if item.get("detected_gene_count") is not None:
                chain.append(
                    cls._build_rule_entry(
                        metric_key="detected_gene_count",
                        category="RNAseqGeneExp",
                        sample=sample,
                        value=safe_float(item.get("detected_gene_count")),
                        source_file=rnaseq_gene_exp_source,
                        source_field="Sum.",
                        rule_source=metric_rule_sources.get("detected_gene_count"),
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
                if key == "mt_rate_percent":
                    _sem_overrides = organelle_semantics
                elif _assay_early == "rnaseq" and key in RNASEQ_SEMANTIC_OVERRIDES:
                    _sem_overrides = RNASEQ_SEMANTIC_OVERRIDES[key]
                else:
                    _sem_overrides = None
                chain.append(
                    cls._build_rule_entry(
                        metric_key=key,
                        category="AlignmentQC",
                        sample=sample,
                        value=safe_float(item.get(key)),
                        source_file=alignment_source,
                        source_field=source_fields.get(key),
                        rule_source=metric_rule_sources.get(key),
                        semantic_overrides=_sem_overrides,
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
                        value=safe_float(item.get(source_key)),
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
                value=safe_float(item.get("frip_ratio")),
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
                    "numerator_value": safe_float(item.get("reads_in_peaks")),
                    "denominator_value": safe_float(item.get("mapped_reads")),
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
                value=safe_float(pair.get("value")),
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

        # Phase 1.2（project_analysis_agent_upgrade_plan.md）：字段发现层产出，见
        # project_field_discovery_service.discover_and_extract()。这里的每一条都已经过
        # metric_schema_service.normalize() 的自洽/重算校验；没有 rule_source 时
        # _build_rule_entry() 会按现有约定把它标注为 formula/threshold 均未经项目脚本确认
        # （professional_default_unverified + screening_signal_only），与其他缺少
        # workflow-code 佐证的指标一致，不冒充"项目已验证"结论，也不带额外的"探索性"标记。
        for item in parsed_metrics.get("field_discovery", []) or []:
            metric_key = str(item.get("metric_id") or "").strip()
            if not metric_key:
                continue
            chain.append(
                cls._build_rule_entry(
                    metric_key=metric_key,
                    category="FieldDiscovery",
                    sample=str(item.get("sample") or "-"),
                    value=safe_float(item.get("value")),
                    source_file=str(item.get("source_file") or ""),
                    source_field=str(item.get("display_field") or metric_key),
                    rule_source=metric_rule_sources.get(metric_key),
                    trust_level_override=item.get("trust_level"),
                )
            )

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
        verification_plan = project_cause_analysis_service.dedupe_text(verification_plan + list(next_actions))[:6]

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
            term in normalized for term in INTERNAL_WORKFLOW_TERMS
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
            relative = project_context_builder_service.relative_path(root, path)
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
        # 2026-07-02 修复：`_select_evidence_files` 里加的问题文本字面提及指标检测
        # （metric_schema_service.detect_metrics_in_text）只影响它自己局部的
        # target_metrics 变量，用来决定"读哪些候选文件"；但真正把文件内容解析成
        # evidence_card 的 parse_evidence_file 调用（下面 _parse_evidence_files 里）
        # 读的是 analysis_plan["target_metrics"]，是完全独立的另一份数据——只做文件
        # 选择侧的检测，转置表这类文件虽然会被选中读取，但因为 target_metrics 传给
        # parser 时还是空的，解析器不知道要找哪个指标，产出 0 张 evidence_card。
        # 这里把检测结果直接写回 analysis_plan，让文件选择和实际解析用同一份
        # target_metrics，避免同一个检测结果要维护两份。只在指标不存在时追加，
        # 不覆盖 planning_hints 显式传入的 analysis_plan。
        literal_metrics = [
            metric_schema_service.canonical_id(metric_id)
            for metric_id in metric_schema_service.detect_metrics_in_text(question)
        ]
        if literal_metrics:
            existing_target_metrics = list(analysis_plan.get("target_metrics") or [])
            for metric_id in literal_metrics:
                if metric_id not in existing_target_metrics:
                    existing_target_metrics.append(metric_id)
            analysis_plan["target_metrics"] = existing_target_metrics
        question_types = cls._infer_question_types(question)
        question_type = question_types[0]
        # For pipeline_failure questions, ensure log files are in the local cache.
        # resolve_project_root returns cached paths immediately without re-mirroring,
        # so an old cache may lack log files.  Re-mirror only downloads new/changed files.
        if question_type == "pipeline_failure" and not find_log_files(root, limit=1, strict_log_suffix=True):
            try:
                refresh_project_sftp_logs(project_id, root)
            except Exception:
                pass
        if question_type == "pipeline_failure":
            return cls._analyze_pipeline_failure_logs(
                project_id=project_id,
                question=question,
                root=root,
                run_id=run_id,
                question_types=question_types,
                analysis_plan=analysis_plan,
                planning_hints=planning_hints,
                started_at=started_at,
                max_evidence_files=max_evidence_files,
            )
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
        project_context = project_parse_cache.build_cached_project_context(root, include_html_body=include_html_body)
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
        # 在读取证据文件之前，从 config 快速确定实验类型，用于指导文件选择和指标优先级。
        early_assay = cls._detect_assay_early(project_context)
        logger.info(
            "project_analysis stage=early_assay_detect run_id=%s project=%s assay=%s",
            run_id,
            project_id,
            early_assay,
        )
        project_version = project_context_builder_service.build_project_version(root, project_context)
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
                assay_type=early_assay,
                question=question,
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
            evidence_notes = project_file_parser_service.collect_evidence_notes_from_file_summaries(file_summaries)
        evidence_files_to_parse = [] if evidence_snapshot is not None else evidence_files
        if evidence_files_to_parse:
            for file_path in evidence_files_to_parse:
                table_kind = resolve_table_kind(file_path)
                progress_stage, progress_label = progress_stage_for_evidence(file_path, table_kind)
                publish_project_progress(
                    f"姝ｅ湪璇诲彇 {progress_label}",
                    stage=progress_stage,
                    status="in_progress",
                    detail={"file": str(file_path.relative_to(root))},
                )
            max_workers = max(1, min(project_parse_cache._EVIDENCE_PARSE_WORKERS, len(evidence_files_to_parse)))
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
                        project_file_parser_service.parse_evidence_file,
                        root=root,
                        file_path=file_path,
                        experiment_design=project_context.get("experiment_design") or {},
                        cache=project_parse_cache,
                        summarize_text_fn=project_file_parser_service.summarize_text_evidence,
                        target_metrics=list(analysis_plan.get("target_metrics", []) or []),
                        project_id=project_id,
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
                        project_file_parser_service.apply_parsed_metric_update(parsed_metrics, result.get("parsed_metric_update"))
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
            table_kind = resolve_table_kind(file_path)
            progress_stage, progress_label = progress_stage_for_evidence(file_path, table_kind)
            publish_project_progress(
                f"正在读取 {progress_label}",
                stage=progress_stage,
                status="in_progress",
                detail={"file": relative},
            )
            try:
                if table_kind is not None or lower_name in STRUCTURED_TABLE_FILES or table_kind in {
                    "diff_annotation",
                    "diff_go",
                    "diff_pathway",
                    "diff_table",
                }:
                    cache_kind = f"table:v2:{table_kind or lower_name}"
                    cached = project_parse_cache._get_cached_parse(file_path, cache_kind)
                    if cached is None:
                        rows = read_correlation_rows(file_path) if table_kind == "correlation" else read_table_rows(file_path)
                        if table_kind == "qc":
                            summary = project_file_parser_service.build_qc_summary(rows)
                            metric_payload = {"target": "qc", "value": summary.get("metrics", [])}
                        elif table_kind == "spikein":
                            summary = project_file_parser_service.build_spikein_summary(rows)
                            metric_payload = {"target": "spikein", "value": summary.get("metrics", [])}
                        elif table_kind == "alignment":
                            summary = project_file_parser_service.build_alignment_summary(rows)
                            metric_payload = {"target": "alignment", "value": summary.get("metrics", [])}
                        elif table_kind == "peak":
                            summary = project_file_parser_service.build_peak_summary(rows)
                            metric_payload = {
                                "target": "peak",
                                "value": {
                                    "metrics": summary.get("metrics", {}),
                                    "ranked": summary.get("ranked", []),
                                },
                            }
                        elif table_kind == "frip":
                            summary = project_file_parser_service.build_frip_summary(
                                rows,
                                source_name=file_path.name,
                            )
                            metric_payload = {"target": "frip", "value": summary.get("metrics", [])}
                        elif table_kind == "correlation":
                            summary = project_file_parser_service.build_correlation_summary(rows)
                            metric_payload = {
                                "target": "correlation",
                                "value": summary,
                            }
                        elif table_kind == "rnaseq_reads_class":
                            summary = project_file_parser_service.build_rnaseq_reads_class_summary(rows)
                            metric_payload = {"target": "rnaseq_reads_class", "value": summary.get("metrics", [])}
                        elif table_kind == "rnaseq_gene_exp":
                            summary = project_file_parser_service.build_rnaseq_gene_exp_summary(rows)
                            metric_payload = {"target": "rnaseq_gene_exp", "value": summary.get("metrics", [])}
                        elif table_kind == "rnaseq_silva":
                            silva_sample = re.sub(r"\.stat\.xls$", "", file_path.name, flags=re.IGNORECASE)
                            summary = project_file_parser_service.build_rnaseq_silva_summary(rows, sample=silva_sample)
                            metric_payload = {"target": "rnaseq_silva", "value": summary.get("metrics", []), "mode": "merge_silva"}
                        elif table_kind == "diff_annotation":
                            summary = project_file_parser_service.build_diff_annotation_summary(rows)
                            metric_payload = {
                                "target": "diff",
                                "value": {
                                    "kind": "diff_annotation",
                                    "change_counts": summary.get("change_counts", {}),
                                    "top_genes": summary.get("top_genes", []),
                                },
                            }
                        elif table_kind == "diff_go":
                            summary = project_file_parser_service.build_enrichment_summary(rows, "GO")
                            metric_payload = {
                                "target": "diff",
                                "value": {
                                    "kind": "diff_go",
                                    "top_terms": summary.get("top_terms", []),
                                },
                            }
                        elif table_kind == "diff_pathway":
                            summary = project_file_parser_service.build_enrichment_summary(rows, "Pathway")
                            metric_payload = {
                                "target": "diff",
                                "value": {
                                    "kind": "diff_pathway",
                                    "top_terms": summary.get("top_terms", []),
                                },
                            }
                        else:
                            summary = project_file_parser_service.build_enrichment_summary(rows, "DiffTable")
                            metric_payload = {
                                "target": "diff",
                                "value": {
                                    "kind": "diff_table",
                                    "top_terms": summary.get("top_terms", []),
                                },
                            }
                        cached = project_parse_cache._set_cached_parse(
                            file_path,
                            cache_kind,
                            {"summary": summary, "metric_payload": metric_payload},
                        )
                    summary = cached["summary"]
                    metric_payload = cached["metric_payload"]
                    if table_kind == "correlation":
                        summary = project_file_parser_service.stratify_correlation_summary(
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
                        parsed_metrics["frip"] = project_file_parser_service.merge_frip_metrics(
                            parsed_metrics.get("frip", []) or [],
                            metric_payload["value"],
                        )
                    elif metric_payload.get("mode") == "merge_silva":
                        parsed_metrics["rnaseq_silva"] = project_file_parser_service.merge_silva_metrics(
                            parsed_metrics.get("rnaseq_silva", []) or [],
                            metric_payload["value"] or [],
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
                    cached = project_parse_cache._get_cached_parse(file_path, cache_kind)
                    if cached is None:
                        if file_path.suffix.lower() == ".log":
                            preview = read_log_snippet(file_path)
                        elif looks_like_text_file(file_path):
                            preview = read_text_snippet(file_path)
                        else:
                            preview = ""
                        summary = project_file_parser_service.summarize_text_evidence(file_path, preview)
                        cached = project_parse_cache._set_cached_parse(
                            file_path,
                            cache_kind,
                            {"preview": preview, "summary": summary},
                        )
                    preview = cached["preview"]
                    summary = cached["summary"]
                    if summary.get("kind") in {"diff", "motif", "igv"}:
                        payload = {key: value for key, value in summary.items() if key not in {"preview", "findings"}}
                        if summary.get("kind") == "motif":
                            payload["sample"] = extract_motif_sample_name(file_path)
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
            motif_summary = project_file_parser_service.aggregate_motif_metrics(parsed_metrics["motif"])
            parsed_metrics["motif_summary"] = motif_summary
            automatic_findings.extend(motif_summary.get("findings", []))
            automatic_findings = list(dict.fromkeys(automatic_findings))
        metric_rule_sources = project_context.get("metric_rule_sources", {}) or {}
        organelle_semantics = project_context_builder_service.organelle_semantics(project_context)
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
        cause_graph = project_cause_analysis_service.build_cause_graph(
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
                    _log_sum = project_file_parser_service.summarize_text_evidence(_lf, _snippet)
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
                    _err_text = str(_err)
                    if project_file_parser_service._is_non_error_log_stat_line(_err_text.lower()):
                        continue
                    _log_error_conclusions.append({
                        "claim": f"[{_fname}] {_err_text[:400]}",
                        "explanation": cls._explain_pipeline_error_line(_err_text),
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
                "exploratory_observations": cls._safe_list_exploratory_observations(project_id),
            }
        else:
            # pipeline_failure 但未找到任何 log 错误行：插入兜底 conclusion 避免 LLM 返回空答案导致 UI 无反应。
            if "pipeline_failure" in question_types and not fact_packet.get("direct_conclusions"):
                _log_files_found = bool(
                    any((fs.get("summary") or {}).get("kind") == "log" for fs in file_summaries)
                )
                if _log_files_found:
                    fact_packet["direct_conclusions"] = [{
                        "claim": "已读取项目日志文件，未在其中检测到明显的 error / exception / traceback 行，"
                                 "流程可能因其他原因中止（如资源不足、配置错误），请检查日志末尾的退出状态或告警行。",
                        "evidence_ids": [],
                        "causal_level": "observation",
                        "confidence": "log_no_error_found",
                    }]
                else:
                    fact_packet["direct_conclusions"] = [{
                        "claim": "未在项目目录中找到任何日志文件（.log / stderr / snakemake 等），"
                                 "无法通过日志分析错误原因。请确认流程是否已运行，或手动检查项目目录结构。",
                        "evidence_ids": [],
                        "causal_level": "observation",
                        "confidence": "no_log_files",
                    }]
                fact_packet["pipeline_errors_found"] = False
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
                # Phase 1.5（project_analysis_agent_upgrade_plan.md 2.3 节新增字段）：
                # 候选指标队列的影子层观测，语义上不是因果解释，独立于上面几个字段，
                # 不参与 answer_quality_service 的 integration_depth 等打分维度。
                "exploratory_observations": cls._safe_list_exploratory_observations(project_id),
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
            "early_assay": early_assay,
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


# ── Singleton ──────────────────────────────────────────────────────────────────
project_analysis_service = ProjectAnalysisService()
