from __future__ import annotations

from typing import Any


class ProjectCuttagDiagnosticService:
    """Specialized read-only CUT&Tag/CUT&RUN diagnostic tools.

    These tools do not execute external commands. They summarize already parsed
    project evidence into domain-specific diagnostic chains for the LLM.
    """

    @classmethod
    def diagnose_adapter_readthrough(
        cls,
        *,
        parsed_metrics: dict[str, Any],
        evidence_chain: list[dict[str, Any]],
        project_context: dict[str, Any],
        analysis_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not cls._should_run_adapter_tool(parsed_metrics, analysis_plan or {}):
            return None

        qc_rows = [row for row in (parsed_metrics.get("qc") or []) if isinstance(row, dict)]
        if not qc_rows:
            return {
                "tool": "diagnose_cuttag_adapter_readthrough",
                "status": "missing_evidence",
                "summary": "未读取到 ReadsQC 指标，无法判断 adapter read-through 是否参与当前问题。",
                "evidence": [],
                "reasoning_chain": [],
                "evidence_gaps": ["缺少 ReadsQC 表中的 Adapter、Clean Reads、Q30 等字段。"],
                "next_checks": ["先确认项目报告或统计表中是否存在 ReadsQC/adapter trimming 结果。"],
                "boundary": "该工具只做 CUT&Tag/CUT&RUN adapter read-through 诊断，不判断整体项目合格性。",
            }

        adapter_entries = cls._adapter_entries(qc_rows, evidence_chain)
        if not adapter_entries:
            return None

        workflow = cls._workflow_context(project_context)
        high_entries = [
            item
            for item in adapter_entries
            if cls._has_verified_anomaly(evidence_chain, str(item.get("sample") or "-"), {"adapter_percent"})
        ]
        highest = max(
            (item for item in adapter_entries if item.get("adapter_percent") is not None),
            key=lambda item: item["adapter_percent"],
            default=None,
        )
        status = "needs_review" if high_entries else "observed"

        reasoning_chain = []
        if highest:
            reasoning_chain.append(
                f"观测指标：{highest['sample']} Adapter={highest['adapter_percent']:.2f}%，来源 {highest.get('source', '-')}"
            )
        if high_entries:
            reasoning_chain.append(
                "可能上游原因：CUT&Tag/CUT&RUN 常见短片段会导致 read-through adapter；当前项目需要结合 fragment size 和 trimming 参数确认。"
            )
        else:
            reasoning_chain.append(
                "当前只确认了原始 reads 中的 Adapter 检出值；项目阈值未验证时不能据此判定偏高，也不能直接称为 clean reads 接头残留。"
            )
        reasoning_chain.append(
            "下游影响：adapter read-through 会降低可用 clean reads，并可能传导到 mapping、unique mapping、FRiP 或 peak 稳定性。"
        )
        reasoning_chain.append(
            "当前证据边界：项目文件若未确认 adapter 专属阈值，只能描述观测值和复核方向，不能把通用 CUT&Tag 经验写成项目验收标准。"
        )

        evidence_gaps = cls._adapter_evidence_gaps(parsed_metrics, workflow)
        next_checks = cls._adapter_next_checks(workflow)

        return {
            "tool": "diagnose_cuttag_adapter_readthrough",
            "status": status,
            "summary": cls._adapter_summary(high_entries, highest),
            "evidence": adapter_entries[:12],
            "workflow_context": workflow,
            "reasoning_chain": reasoning_chain,
            "evidence_gaps": evidence_gaps,
            "next_checks": next_checks,
            "boundary": "该诊断使用 CUT&Tag/CUT&RUN 通用排查逻辑组织原因链路；不作为项目专属阈值、SOP 或整体合格性判断。",
        }

    @classmethod
    def _should_run_adapter_tool(cls, parsed_metrics: dict[str, Any], analysis_plan: dict[str, Any]) -> bool:
        selected_tool_names = {
            str(item.get("name") or "")
            for item in (analysis_plan.get("selected_tools") or [])
            if isinstance(item, dict)
        }
        target_metrics = {str(item) for item in (analysis_plan.get("target_metrics") or [])}
        if "diagnose_cuttag_adapter_readthrough" in selected_tool_names:
            return True
        if "adapter_percent" in target_metrics:
            return True
        return bool(parsed_metrics.get("qc"))

    @classmethod
    def diagnose_alignment_loss(
        cls,
        *,
        parsed_metrics: dict[str, Any],
        evidence_chain: list[dict[str, Any]],
        project_context: dict[str, Any],
        analysis_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not cls._should_run_tool(
            analysis_plan or {},
            {"diagnose_cuttag_alignment_loss"},
            {"mapping_rate_percent", "unique_mapping_rate_percent", "chrmt_pt_rate_percent", "mt_rate_percent"},
        ):
            return None
        rows = [row for row in (parsed_metrics.get("alignment") or []) if isinstance(row, dict)]
        if not rows:
            return cls._missing_tool_result(
                "diagnose_cuttag_alignment_loss",
                "未读取到 AlignmentQC 指标，无法复核 mapping/unique/chrMT-Pt 原因链。",
                ["缺少 AlignmentQC.xls 或可解析的比对统计表。"],
            )

        entries = []
        for row in rows:
            sample = str(row.get("sample") or "-")
            item = {
                "sample": sample,
                "mapping_rate_percent": cls._safe_float(row.get("mapping_rate_percent")),
                "unique_mapping_rate_percent": cls._safe_float(row.get("unique_mapping_rate_percent")),
                "duplicate_rate_percent": cls._safe_float(row.get("duplicate_rate_percent")),
                "mt_rate_percent": cls._safe_float(row.get("mt_rate_percent")),
                "complexity": row.get("complexity", "-"),
                "sources": cls._metric_sources(
                    evidence_chain,
                    sample,
                    ("mapping_rate_percent", "unique_mapping_rate_percent", "mt_rate_percent", "duplicate_rate_percent"),
                ),
            }
            entries.append(item)

        flagged = [
            item
            for item in entries
            if cls._has_verified_anomaly(
                evidence_chain,
                str(item.get("sample") or "-"),
                {"mapping_rate_percent", "unique_mapping_rate_percent", "mt_rate_percent"},
            )
        ]
        status = "needs_review" if flagged else "observed"
        worst = cls._lowest_entry(entries, "unique_mapping_rate_percent") or cls._lowest_entry(entries, "mapping_rate_percent")
        workflow = cls._workflow_context(project_context)
        organelle_label = workflow["organelle_label"]
        reasoning_chain = [
            f"先看 mapping 与 unique 的联动，再结合 {organelle_label} 和 duplicate 判断 reads 是否被细胞器、多重比对或重复 reads 消耗。",
            "如果 mapping 接近但 unique 很低，优先排查多重比对、重复序列、参考基因组版本和细胞器 reads 占比。",
            f"只有项目阈值确认 {organelle_label} 需要复核时，才能进一步讨论其对核基因组有效 reads、FRiP、peak 和相关性的影响。",
            "当前诊断只指出需要复核的指标链，不判断整体项目是否合格。",
        ]
        return {
            "tool": "diagnose_cuttag_alignment_loss",
            "status": status,
            "summary": cls._alignment_summary(flagged, worst),
            "evidence": entries[:12],
            "workflow_context": workflow,
            "reasoning_chain": reasoning_chain,
            "evidence_gaps": cls._alignment_evidence_gaps(parsed_metrics, workflow),
            "next_checks": [
                f"核对 species/reference genome 与项目配置是否一致；当前解析物种为 {workflow.get('species') or '未确认'}。",
                "查看 bowtie2/samtools 口径：mapping、unique、multi-mapped reads 是否来自同一批 BAM。",
                f"结合 organelle_chroms 配置复核 {organelle_label} 对应染色体是否被正确识别和过滤。",
                "把 mapping/unique 与 FRiP、peak、correlation 串联判断，避免只看单个百分比。",
            ],
            "boundary": "只做 alignment 原因链复核，不给整体项目合格/交付判断。",
        }

    @classmethod
    def diagnose_duplicate_policy(
        cls,
        *,
        parsed_metrics: dict[str, Any],
        evidence_chain: list[dict[str, Any]],
        project_context: dict[str, Any],
        analysis_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not cls._should_run_tool(
            analysis_plan or {},
            {"diagnose_cuttag_duplicate_policy"},
            {"duplicate_rate_percent"},
        ):
            return None
        rows = [row for row in (parsed_metrics.get("alignment") or []) if isinstance(row, dict)]
        if not rows:
            return cls._missing_tool_result(
                "diagnose_cuttag_duplicate_policy",
                "未读取到 AlignmentQC/Picard duplicate 指标，无法复核重复率原因链。",
                ["缺少 AlignmentQC.xls、Picard duplicate 统计或对应脚本来源。"],
            )

        frip_by_sample = cls._rows_by_sample(parsed_metrics.get("frip") or [])
        entries = []
        for row in rows:
            sample = str(row.get("sample") or "-")
            duplicate = cls._safe_float(row.get("duplicate_rate_percent"))
            frip_row = frip_by_sample.get(sample, {})
            entries.append(
                {
                    "sample": sample,
                    "duplicate_rate_percent": duplicate,
                    "complexity": row.get("complexity", "-"),
                    "frip_ratio": frip_row.get("frip_ratio", "-"),
                    "peak_count": frip_row.get("peak_count", "-"),
                    "source": cls._metric_source(evidence_chain, sample, "duplicate_rate_percent"),
                }
            )

        flagged = [
            item
            for item in entries
            if cls._has_verified_anomaly(
                evidence_chain,
                str(item.get("sample") or "-"),
                {"duplicate_rate_percent"},
            )
        ]
        status = "needs_review" if flagged else "observed"
        workflow = cls._workflow_context(project_context)
        return {
            "tool": "diagnose_cuttag_duplicate_policy",
            "status": status,
            "summary": cls._duplicate_summary(flagged),
            "evidence": entries[:12],
            "workflow_context": workflow,
            "reasoning_chain": [
                "CUT&Tag/CUT&RUN 的真实富集区域可能天然产生较高重复 reads，duplicate 不能简单按 ChIP 经验直接判定为失败。",
                "需要同时看 library complexity、FRiP、peak count、去重策略和目标蛋白富集模式。",
                "如果 duplicate 高且 FRiP/peak 同时低，应优先排查建库复杂度、PCR cycle、起始量和细胞器/多重比对干扰。",
                "当前只提示重复率相关复核方向，不自动建议删除或保留 duplicates。",
            ],
            "evidence_gaps": cls._duplicate_evidence_gaps(parsed_metrics, workflow),
            "next_checks": [
                "确认流程配置中的 remove_duplicates/keep-dup 策略，以及 peak calling 使用的是去重前还是去重后 BAM。",
                "查看 Picard metrics 中 PERCENT_DUPLICATION、ESTIMATED_LIBRARY_SIZE、NRF/PBC1/PBC2 是否完整。",
                "结合 FRiP 和 peak count 判断重复 reads 是否伴随有效富集，避免误删真实信号。",
            ],
            "boundary": "只复核 duplicate 解释和处理策略，不判断整体项目合格。",
        }

    @classmethod
    def diagnose_frip_peak_quality(
        cls,
        *,
        parsed_metrics: dict[str, Any],
        evidence_chain: list[dict[str, Any]],
        project_context: dict[str, Any],
        analysis_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not cls._should_run_tool(
            analysis_plan or {},
            {"diagnose_cuttag_frip_peak_quality"},
            {"frip", "frip_ratio", "peak_count"},
        ):
            return None
        frip_rows = [row for row in (parsed_metrics.get("frip") or []) if isinstance(row, dict)]
        peak_metrics = parsed_metrics.get("peak") or {}
        if not frip_rows and not peak_metrics:
            return cls._missing_tool_result(
                "diagnose_cuttag_frip_peak_quality",
                "未读取到 FRiP 或 peak count 结果，无法复核富集质量链路。",
                ["缺少 FRiP_score.xls、FRiP.xls 或 Samples_peak_number_stat.xls。"],
            )

        peak_counts = peak_metrics.get("metrics") if isinstance(peak_metrics, dict) else {}
        if not isinstance(peak_counts, dict):
            peak_counts = {}
        entries = []
        for row in frip_rows:
            sample = str(row.get("sample") or "-")
            entries.append(
                {
                    "sample": sample,
                    "frip_ratio": row.get("frip_ratio"),
                    "peak_count": row.get("peak_count") or peak_counts.get(sample, "-"),
                    "reads_in_peaks": row.get("reads_in_peaks", "-"),
                    "mapped_reads": row.get("mapped_reads", "-"),
                    "source": cls._metric_source(evidence_chain, sample, "frip_ratio"),
                }
            )
        for sample, count in peak_counts.items():
            if not any(item.get("sample") == sample for item in entries):
                entries.append({"sample": sample, "frip_ratio": "-", "peak_count": count, "source": "PeakStat"})

        flagged = [
            item
            for item in entries
            if cls._has_verified_anomaly(
                evidence_chain,
                str(item.get("sample") or "-"),
                {"frip_ratio"},
            )
            or cls._safe_float(item.get("peak_count")) == 0
        ]
        status = "needs_review" if flagged else "observed"
        return {
            "tool": "diagnose_cuttag_frip_peak_quality",
            "status": status,
            "summary": cls._frip_peak_summary(flagged, entries),
            "evidence": entries[:12],
            "workflow_context": cls._workflow_context(project_context),
            "reasoning_chain": [
                "FRiP 应和 peak count、mapped reads、样本角色一起解释，不能只看一个阈值。",
                "低 FRiP 可能来自有效核基因组 reads 少、peak calling 参数不适配、IgG/Input 角色差异或目标蛋白本身富集弱。",
                "如果 mapping/unique/chrMT 已有问题，FRiP 偏低更可能是上游有效 reads 被压缩后的传导结果。",
                "当前只指出富集相关需复核指标，不判断项目整体成败。",
            ],
            "evidence_gaps": cls._frip_evidence_gaps(parsed_metrics),
            "next_checks": [
                "确认 FRiP 计算使用的 peak set、BAM 和 usable reads 口径。",
                "复核 peak caller 与参数：MACS/SEACR、qvalue/pvalue、IgG/Input control、是否 broad/narrow peak。",
                "把 FRiP 与 peak count、样本相关性、目标蛋白预期富集区域一起看。",
            ],
            "boundary": "只复核 FRiP/peak 富集链路，不给整体项目合格结论。",
        }

    @classmethod
    def diagnose_sample_correlation(
        cls,
        *,
        parsed_metrics: dict[str, Any],
        evidence_chain: list[dict[str, Any]],
        project_context: dict[str, Any],
        analysis_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not cls._should_run_tool(
            analysis_plan or {},
            {"diagnose_cuttag_sample_correlation", "diagnose_cuttag_frip_peak_quality"},
            {"correlation"},
        ):
            return None
        corr = parsed_metrics.get("correlation") or {}
        if not isinstance(corr, dict) or not corr.get("min_pair"):
            return cls._missing_tool_result(
                "diagnose_cuttag_sample_correlation",
                "未读取到样本相关性矩阵，无法复核重复一致性。",
                ["缺少 spearman_Corr_readCounts.tab 或 deeptools correlation 输出。"],
            )
        min_pair = corr.get("min_pair")
        max_pair = corr.get("max_pair")
        return {
            "tool": "diagnose_cuttag_sample_correlation",
            "status": (
                "needs_review"
                if min_pair
                and cls._has_verified_anomaly(
                    evidence_chain,
                    f"{min_pair[0]} vs {min_pair[1]}",
                    {"correlation"},
                )
                else "observed"
            ),
            "summary": cls._correlation_summary(min_pair),
            "evidence": [
                {"pair": f"{min_pair[0]} vs {min_pair[1]}", "spearman": min_pair[2], "source": cls._metric_source(evidence_chain, "-", "correlation")}
                if min_pair else {},
                {"pair": f"{max_pair[0]} vs {max_pair[1]}", "spearman": max_pair[2], "source": cls._metric_source(evidence_chain, "-", "correlation")}
                if max_pair else {},
            ],
            "workflow_context": cls._workflow_context(project_context),
            "reasoning_chain": [
                "相关性应优先比较同组生物学重复，不同处理组、IgG/Input 或明显不同角色样本不应强行要求高度一致。",
                "负相关或低相关需要结合 read count 特征、bin/peak 集合、样本角色和上游 QC 共同解释。",
                "若同时存在 chrMT/Pt 高、unique 低或 FRiP 低，相关性异常可能是上游有效信号不足的结果。",
            ],
            "evidence_gaps": ["缺少样本分组/处理信息时，相关性只能提示需要复核，不能直接判断重复失败。"],
            "next_checks": [
                "确认相关性矩阵使用的是全基因组 bins、peak 区域还是 readCounts 文件。",
                "先按 biological replicate、IgG/Input、处理组分层比较，避免把不同生物背景样本直接对标。",
                "查看最低相关性样本在 mapping、FRiP、peak count 中是否也有联动问题。",
            ],
            "boundary": "只复核样本相关性和可能原因，不判断项目整体质量是否合格。",
        }

    @staticmethod
    def _should_run_tool(analysis_plan: dict[str, Any], tool_names: set[str], metrics: set[str]) -> bool:
        selected_tool_names = {
            str(item.get("name") or "")
            for item in (analysis_plan.get("selected_tools") or [])
            if isinstance(item, dict)
        }
        target_metrics = {str(item) for item in (analysis_plan.get("target_metrics") or [])}
        return bool(selected_tool_names & tool_names or target_metrics & metrics)

    @staticmethod
    def _missing_tool_result(tool: str, summary: str, gaps: list[str]) -> dict[str, Any]:
        return {
            "tool": tool,
            "status": "missing_evidence",
            "summary": summary,
            "evidence": [],
            "reasoning_chain": [],
            "evidence_gaps": gaps,
            "next_checks": ["先确认本轮证据文件是否包含该模块结果，再进行原因链分析。"],
            "boundary": "证据不足时只说明缺口，不做结论判断。",
        }

    @classmethod
    def _metric_sources(
        cls,
        evidence_chain: list[dict[str, Any]],
        sample: str,
        metric_keys: tuple[str, ...],
    ) -> dict[str, str]:
        return {metric: cls._metric_source(evidence_chain, sample, metric) for metric in metric_keys}

    @staticmethod
    def _metric_source(evidence_chain: list[dict[str, Any]], sample: str, metric_key: str) -> str:
        for item in evidence_chain:
            if str(item.get("metric_key") or "") != metric_key:
                continue
            if sample not in {"", "-"} and str(item.get("sample") or "") not in {sample, "-"}:
                continue
            return f"{item.get('source_file', '-') or '-'}::{item.get('source_field', '-') or '-'}"
        for item in evidence_chain:
            if str(item.get("metric_key") or "") == metric_key:
                return f"{item.get('source_file', '-') or '-'}::{item.get('source_field', '-') or '-'}"
        return "-"

    @staticmethod
    def _has_verified_anomaly(
        evidence_chain: list[dict[str, Any]],
        sample: str,
        metric_keys: set[str],
    ) -> bool:
        for item in evidence_chain:
            if str(item.get("metric_key") or "") not in metric_keys:
                continue
            item_sample = str(item.get("sample") or "-")
            if sample not in {"", "-"} and item_sample not in {sample, "-"}:
                continue
            if item.get("severity") in {"warning", "critical"} and not item.get(
                "threshold_needs_project_validation", True
            ):
                return True
        return False

    @staticmethod
    def _rows_by_sample(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(row.get("sample") or "-"): row for row in rows if isinstance(row, dict)}

    @staticmethod
    def _lowest_entry(entries: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
        valid = [item for item in entries if item.get(key) is not None]
        if not valid:
            return None
        return min(valid, key=lambda item: item.get(key))

    @staticmethod
    def _alignment_summary(flagged: list[dict[str, Any]], worst: dict[str, Any] | None) -> str:
        if flagged:
            samples = ", ".join(str(item.get("sample") or "-") for item in flagged[:4])
            return f"Alignment 相关指标需要复核：{samples} 存在 mapping/unique/chrMT-Pt 联动异常信号。"
        if worst:
            return f"Alignment 已读取，最低 unique/mapping 相关样本为 {worst.get('sample', '-')}，适合继续结合 FRiP/peak 复核。"
        return "Alignment 已读取，但未形成明确的优先复核样本。"

    @staticmethod
    def _duplicate_summary(flagged: list[dict[str, Any]]) -> str:
        if not flagged:
            return "Duplicate 指标已读取，当前未形成优先复核信号。"
        samples = ", ".join(f"{item.get('sample', '-')}={item.get('duplicate_rate_percent'):.2f}%" for item in flagged[:4])
        return f"Duplicate 指标需要复核：{samples}；需结合去重策略、library complexity、FRiP 和 peak count 判断。"

    @staticmethod
    def _frip_peak_summary(flagged: list[dict[str, Any]], entries: list[dict[str, Any]]) -> str:
        if flagged:
            samples = ", ".join(str(item.get("sample") or "-") for item in flagged[:4])
            return f"FRiP/peak 富集链路需要复核：{samples} 存在 FRiP 偏低或 peak 支撑不足的信号。"
        return f"FRiP/peak 指标已读取，共 {len(entries)} 个样本/条目，可用于回答富集相关问题。"

    @staticmethod
    def _correlation_summary(min_pair: Any) -> str:
        if not min_pair:
            return "未形成最低相关性样本对。"
        return f"最低样本相关性为 {min_pair[0]} vs {min_pair[1]} ({min_pair[2]:.4f})，需结合样本分组和上游 QC 复核。"

    @staticmethod
    def _alignment_evidence_gaps(parsed_metrics: dict[str, Any], workflow: dict[str, Any]) -> list[str]:
        gaps = []
        if workflow.get("assay") in {"", "-"}:
            gaps.append("未从项目配置中解析到明确实验类型，需确认是否为 CUT&Tag/CUT&RUN。")
        if workflow.get("sequencing_mode") in {"", "-"}:
            gaps.append("未解析到测序模式，paired-end/single-end 会影响短片段和比对解释。")
        if not parsed_metrics.get("frip"):
            gaps.append("本轮未读到 FRiP，暂不能判断 alignment 问题是否传导到富集信号。")
        return gaps or ["Alignment 原因链所需核心表格已读取，但仍需结合样本分组和参考基因组配置复核。"]

    @staticmethod
    def _duplicate_evidence_gaps(parsed_metrics: dict[str, Any], workflow: dict[str, Any]) -> list[str]:
        gaps = []
        if not parsed_metrics.get("frip"):
            gaps.append("缺少 FRiP，无法判断高重复 reads 是否仍对应有效富集信号。")
        if workflow.get("remove_duplicates") in {None, "", "-"}:
            gaps.append("未解析到 remove_duplicates/keep-dup 配置，无法确认下游是否实际去重。")
        return gaps or ["duplicate 解释所需的 alignment 指标已读取，但仍建议补充 Picard 完整指标和去重配置。"]

    @staticmethod
    def _frip_evidence_gaps(parsed_metrics: dict[str, Any]) -> list[str]:
        gaps = []
        if not parsed_metrics.get("peak"):
            gaps.append("缺少 peak count 表，FRiP 需要与 peak 数量一起解释。")
        if not parsed_metrics.get("correlation"):
            gaps.append("缺少 correlation 结果，暂不能判断低 FRiP 是否伴随样本一致性问题。")
        if not parsed_metrics.get("alignment"):
            gaps.append("缺少 AlignmentQC，暂不能追踪 FRiP 低是否由 mapping/unique/chrMT-Pt 传导。")
        return gaps or ["FRiP、peak、alignment/correlation 证据较完整，可形成富集原因链。"]

    @classmethod
    def _adapter_entries(
        cls,
        qc_rows: list[dict[str, Any]],
        evidence_chain: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source_by_sample = {}
        for item in evidence_chain:
            if item.get("metric_key") == "adapter_percent":
                source_by_sample[str(item.get("sample") or "-")] = {
                    "source": f"{item.get('source_file', '-') or '-'}::{item.get('source_field', '-') or '-'}",
                    "formula_source": item.get("formula_source", "-"),
                    "threshold_source": item.get("threshold_source", "-"),
                    "threshold_needs_project_validation": item.get("threshold_needs_project_validation", True),
                }

        entries = []
        for row in qc_rows:
            adapter = cls._safe_float(row.get("adapter_percent"))
            if adapter is None:
                continue
            sample = str(row.get("sample") or "-")
            source = source_by_sample.get(sample, {})
            entries.append(
                {
                    "sample": sample,
                    "adapter_percent": adapter,
                    "adapter_reads": row.get("adapter_reads", "-"),
                    "clean_reads": row.get("clean_reads", "-"),
                    "clean_read_retention_percent": row.get("clean_read_retention_percent", "-"),
                    "q30_ratio": row.get("q30_ratio", "-"),
                    "source": source.get("source", "ReadsQC::Adapter"),
                    "formula_source": source.get("formula_source", "-"),
                    "threshold_source": source.get("threshold_source", "-"),
                    "threshold_needs_project_validation": source.get("threshold_needs_project_validation", True),
                }
            )
        return sorted(entries, key=lambda item: item.get("adapter_percent") or 0, reverse=True)

    @staticmethod
    def _workflow_context(project_context: dict[str, Any]) -> dict[str, Any]:
        params = {}
        config = project_context.get("config")
        if isinstance(config, dict):
            params.update(config)
        for source_key in ("workflow_detected_parameters", "pipeline_config", "project_config"):
            value = project_context.get(source_key)
            if isinstance(value, dict):
                params.update(value)
        species = str(params.get("species") or params.get("genome") or params.get("reference") or "").strip()
        normalized_species = species.lower()
        plant_tokens = ("tair", "arabidopsis", "oryza", "rice", "zea", "maize", "plant")
        animal_tokens = ("hg", "grch", "human", "mm", "grcm", "mouse", "rn", "rat")
        if any(token in normalized_species for token in plant_tokens):
            organelle_label = "线粒体/叶绿体 reads 比例"
        elif any(token in normalized_species for token in animal_tokens):
            organelle_label = "线粒体 reads 比例"
        else:
            organelle_label = "细胞器 reads 比例"
        return {
            "assay": params.get("assay") or params.get("project_type") or params.get("Sequencing") or "-",
            "species": species or "-",
            "organelle_label": organelle_label,
            "sequencing_mode": params.get("sequencing_mode") or "-",
            "adapter_type": params.get("adapter_type") or "-",
            "trimming_tool": params.get("trimming_tool") or "-",
            "raw_fastq_dir": params.get("raw_fastq_dir") or "-",
        }

    @staticmethod
    def _adapter_evidence_gaps(parsed_metrics: dict[str, Any], workflow: dict[str, Any]) -> list[str]:
        gaps = []
        if not parsed_metrics.get("fragment_size") and not parsed_metrics.get("fragment_sizes"):
            gaps.append("缺少 fragment size 分布，暂不能确认是否存在 CUT&Tag 短片段 read-through adapter。")
        if workflow.get("trimming_tool") in {"", "-"}:
            gaps.append("项目配置中未解析到 trimming_tool，需确认 adapter trimming 使用的工具和参数。")
        if workflow.get("adapter_type") in {"", "-"}:
            gaps.append("项目配置中未解析到 adapter_type，需确认接头序列或试剂盒类型。")
        return gaps or ["当前 adapter 诊断所需的核心上下文已部分具备，但仍应结合原始 fastq QC 图复核。"]

    @staticmethod
    def _adapter_next_checks(workflow: dict[str, Any]) -> list[str]:
        checks = [
            "优先查看 raw/clean FASTQ 的 adapter content 和 overrepresented sequences，确认是否集中在短片段读穿。",
            "补充 fragment size 分布；CUT&Tag 若主峰集中在短片段区间，adapter read-through 的解释更有支撑。",
            "核对 trimming 工具和参数，重点看最小保留长度、overlap、错误率和双端接头序列是否匹配。",
            "把 Adapter 与 Clean Reads 保留率、Mapping、Unique、FRiP 串联看，确认 adapter 是否只是上游现象还是已传导到有效信号损失。",
        ]
        if workflow.get("trimming_tool") not in {"", "-"}:
            checks[2] = f"核对 {workflow.get('trimming_tool')} 的 trimming 参数，重点看最小保留长度、overlap、错误率和双端接头序列是否匹配。"
        return checks

    @staticmethod
    def _adapter_summary(high_entries: list[dict[str, Any]], highest: dict[str, Any] | None) -> str:
        if not highest:
            return "未解析到 Adapter 观测值。"
        if high_entries:
            samples = ", ".join(f"{item['sample']}={item['adapter_percent']:.2f}%" for item in high_entries[:4])
            return f"项目内已验证阈值提示部分样本的 Adapter 检出率需要复核：{samples}。仍需用 clean FASTQ 证据判断是否存在处理后残留。"
        return (
            f"最高 Adapter 检出率为 {highest['sample']}={highest['adapter_percent']:.2f}%（分母为 raw reads）；"
            "当前未形成项目阈值支持的高低判断，也不能据此称为 clean reads 接头残留。"
        )

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(str(value).strip().rstrip("%"))
        except (TypeError, ValueError):
            return None


project_cuttag_diagnostic_service = ProjectCuttagDiagnosticService()
