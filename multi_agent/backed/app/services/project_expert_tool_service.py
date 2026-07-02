from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Callable

from multi_agent.backed.app.services.business_agent.evidence_card_service import (
    evidence_card_service,
)
from multi_agent.backed.app.services.business_agent.experiment_design_service import (
    experiment_design_service,
)


class ProjectExpertToolService:
    """Read-only QC, alignment and enrichment executors for the agent loop."""

    MAX_ROUNDS = 3
    DOMAIN_METRICS = {
        "qc": {"adapter_percent", "q20_ratio", "q30_ratio", "clean_reads"},
        "alignment": {
            "mapping_rate_percent",
            "unique_mapping_rate_percent",
            "duplicate_rate_percent",
            "chrmt_pt_rate_percent",
            "mt_rate_percent",
        },
        "enrichment": {
            "frip",
            "frip_ratio",
            "peak_count",
            "peak_width",
            "correlation",
            "tss_enrichment",
            "fragment_size",
            "spikein_mapped_reads",
            "spikein_unique_mapping_rate_percent",
            "spikein_scaling_factor",
            "control_binding_status",
        },
    }
    TOOL_DOMAINS = {
        "run_qc_expert": "qc",
        "diagnose_cuttag_adapter_readthrough": "qc",
        "run_alignment_expert": "alignment",
        "diagnose_cuttag_alignment_loss": "alignment",
        "diagnose_cuttag_duplicate_policy": "alignment",
        "run_enrichment_expert": "enrichment",
        "diagnose_cuttag_frip_peak_quality": "enrichment",
        "diagnose_cuttag_sample_correlation": "enrichment",
    }

    def run_loop(
        self,
        *,
        project_root: str | Path,
        project_id: str,
        question: str,
        analysis_plan: dict[str, Any] | None,
        project_context: dict[str, Any] | None = None,
        existing_cards: list[dict[str, Any]] | None = None,
        max_rounds: int = MAX_ROUNDS,
    ) -> dict[str, Any]:
        root = Path(project_root)
        plan = analysis_plan or {}
        context = project_context or {}
        domains = self._plan_domains(plan)
        rounds: list[dict[str, Any]] = []
        cards = evidence_card_service.consolidate_cards(list(existing_cards or []))
        all_observations: list[dict[str, Any]] = []
        stop_reason = "max_rounds_reached"

        executors: dict[str, Callable[..., dict[str, Any]]] = {
            "qc": self.run_qc_expert,
            "alignment": self.run_alignment_expert,
            "enrichment": self.run_enrichment_expert,
        }
        bounded_rounds = min(self.MAX_ROUNDS, max(1, int(max_rounds)))
        pending_domains = list(domains)
        completed_domains: list[str] = []
        no_progress_rounds = 0
        index = 0
        while pending_domains and index < bounded_rounds:
            domain = pending_domains.pop(0)
            if domain in completed_domains:
                continue
            index += 1
            result = executors[domain](
                project_root=root,
                project_id=project_id,
                project_context=context,
            )
            previous_ids = {str(card.get("evidence_id") or "") for card in cards}
            cards = evidence_card_service.consolidate_cards(
                cards + list(result.get("evidence_cards", []) or [])
            )
            new_cards = [
                card
                for card in cards
                if str(card.get("evidence_id") or "") not in previous_ids
            ]
            completed_domains.append(domain)
            no_progress_rounds = no_progress_rounds + 1 if not new_cards else 0
            observation = {
                "domain": domain,
                "status": result.get("status", "no_evidence"),
                "matched_files": result.get("matched_files", []),
                "new_evidence_ids": [card.get("evidence_id") for card in new_cards],
                "evidence_gaps": result.get("evidence_gaps", []),
                "summary": result.get("summary", ""),
            }
            all_observations.append(observation)
            replanned = self._replan_after_observation(
                domain=domain,
                observation=observation,
                plan=plan,
                completed_domains=completed_domains,
                pending_domains=pending_domains,
            )
            for candidate in replanned:
                if candidate not in completed_domains and candidate not in pending_domains:
                    pending_domains.append(candidate)
            rounds.append(
                {
                    "round": index,
                    "plan": {
                        "goal": f"collect_{domain}_evidence",
                        "reason": self._domain_reason(domain, plan, question),
                    },
                    "tool_calls": [
                        {
                            "tool": f"run_{domain}_expert",
                            "executor": f"project_expert_tool_service.run_{domain}_expert",
                            "read_only": True,
                        }
                    ],
                    "observations": [observation],
                    "new_evidence_count": len(new_cards),
                    "replan": {
                        "added_domains": replanned,
                        "pending_domains": list(pending_domains),
                        "reason": "observation_evidence_gap_review",
                    },
                }
            )
            if no_progress_rounds >= 2:
                stop_reason = "no_progress"
                break

        if not domains:
            stop_reason = "no_relevant_expert_tool"
        elif not pending_domains and no_progress_rounds == 0:
            stop_reason = "evidence_sufficient"
        elif not pending_domains:
            stop_reason = "planned_tools_exhausted"
        return {
            "version": "plan-tool-observe-v1",
            "max_rounds": bounded_rounds,
            "round_count": len(rounds),
            "rounds": rounds,
            "observations": all_observations,
            "evidence_cards": cards,
            "new_evidence_cards": [
                card
                for card in cards
                if str(card.get("evidence_id") or "")
                not in {
                    str(item.get("evidence_id") or "")
                    for item in (existing_cards or [])
                }
            ],
            "stop_reason": stop_reason,
        }

    def run_qc_expert(
        self,
        *,
        project_root: str | Path,
        project_id: str,
        project_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        root = Path(project_root)
        files = self._find_files(root, ("*.trim.log", "*cutadapt*.log", "*fastp*.json", "*fastqc_data.txt"))
        cards: list[dict[str, Any]] = []
        matched: list[str] = []
        for path in files[:24]:
            relative = self._relative(path, root)
            before_count = len(cards)
            lower_name = path.name.lower()
            if lower_name.endswith(".json") and "fastp" in lower_name:
                cards.extend(
                    self._fastp_cards(
                        path,
                        project_id=project_id,
                        relative=relative,
                        project_context=project_context,
                    )
                )
                if len(cards) > before_count:
                    matched.append(relative)
                continue
            text = self._read_text(path)
            if not text:
                continue
            sample = self._sample_from_path(path)
            if lower_name.endswith("fastqc_data.txt"):
                cards.extend(
                    self._fastqc_cards(
                        text,
                        project_id=project_id,
                        sample=sample,
                        relative=relative,
                        project_context=project_context,
                    )
                )
                if len(cards) > before_count:
                    matched.append(relative)
                continue
            processed_pairs = self._number(text, r"Total read pairs processed:\s*([\d,]+)")
            written_pairs = self._number(text, r"Pairs written \(passing filters\):\s*([\d,]+)")
            for read_name in ("R1", "R2"):
                adapter_count = self._number(
                    text,
                    rf"Read {read_name[-1]} with adapter:\s*([\d,]+)",
                )
                percent = self._number(
                    text,
                    rf"Read {read_name[-1]} with adapter:\s*[\d,]+\s*\(([\d.]+)%\)",
                )
                if percent is not None:
                    cards.append(
                        self._card(
                            project_id=project_id,
                            metric_id="adapter_percent",
                            measurement_id=f"cutadapt_{read_name.lower()}_adapter_detected_percent",
                            measurement_definition=(
                                f"cutadapt Read {read_name[-1]} with adapter / raw {read_name} reads * 100"
                            ),
                            metric=f"Raw reads with adapter ({read_name})",
                            category="QCExpert",
                            sample=sample,
                            value=percent,
                            display_value=f"{percent:.2f}%",
                            numerator=self._as_int(adapter_count),
                            numerator_name=f"raw {read_name} reads with adapter",
                            numerator_value=self._as_int(adapter_count),
                            denominator=self._as_int(processed_pairs) if processed_pairs else f"raw {read_name} reads",
                            denominator_name=f"raw {read_name} reads",
                            denominator_value=self._as_int(processed_pairs),
                            counting_unit="reads",
                            population_scope=f"raw {read_name} reads",
                            value_scale="percent",
                            display_scale="percent",
                            source_file=relative,
                            source_field=f"Read {read_name[-1]} with adapter",
                            formula="adapter_detected_reads / raw_reads * 100",
                            processing_phase="raw_reads_pre_trim",
                            expert_tool="run_qc_expert",
                            project_context=project_context,
                        )
                    )
            if processed_pairs and written_pairs is not None:
                retention = written_pairs / processed_pairs * 100
                cards.append(
                    self._card(
                        project_id=project_id,
                        metric_id="clean_read_retention_percent",
                        metric="Read-pair retention after trimming",
                        category="QCExpert",
                        sample=sample,
                        value=round(retention, 4),
                        display_value=f"{retention:.2f}%",
                        numerator=int(written_pairs),
                        denominator=int(processed_pairs),
                        source_file=relative,
                        source_field="Pairs written (passing filters)",
                        formula="written_pairs / processed_pairs * 100",
                        processing_phase="trim_filter_output",
                        expert_tool="run_qc_expert",
                        project_context=project_context,
                    )
                )
            if len(cards) > before_count:
                matched.append(relative)
        gaps = []
        if not any(card.get("processing_phase") == "raw_reads_pre_trim" for card in cards):
            gaps.append("未找到 raw reads 接头检出日志。")
        if not any(card.get("processing_phase") == "trim_filter_output" for card in cards):
            gaps.append("未找到 trim 后 reads 保留数量。")
        return self._result("qc", cards, matched, gaps)

    def run_alignment_expert(
        self,
        *,
        project_root: str | Path,
        project_id: str,
        project_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        root = Path(project_root)
        files = self._find_files(
            root,
            ("*.summary.txt", "*.mt_stat.txt", "*.nrf_pbc.txt", "*.library_complexity.txt"),
        )
        cards: list[dict[str, Any]] = []
        matched: list[str] = []
        for path in files[:40]:
            relative = self._relative(path, root)
            text = self._read_text(path)
            if not text:
                continue
            before_count = len(cards)
            lower_name = path.name.lower()
            sample = self._sample_from_path(path)
            if lower_name.endswith(".summary.txt"):
                values = self._key_value_text(text)
                sample = values.get("Sample_ID") or values.get("Sample") or sample
                cards.extend(
                    self._alignment_summary_cards(
                        values,
                        project_id=project_id,
                        sample=sample,
                        relative=relative,
                        project_context=project_context,
                    )
                )
            elif lower_name.endswith(".mt_stat.txt"):
                values = self._key_value_text(text)
                sample = values.get("Sample") or sample
                total = self._float(values.get("Total_reads"))
                mt_reads = self._float(values.get("MT_reads"))
                rate = self._float(values.get("MT_rate(%)"))
                if rate is not None:
                    cards.append(
                        self._card(
                            project_id=project_id,
                            metric_id="mt_rate_percent",
                            metric="Mitochondrial alignment rate",
                            category="AlignmentExpert",
                            sample=sample,
                            value=rate,
                            display_value=f"{rate:.2f}%",
                            numerator=self._as_int(mt_reads),
                            denominator=self._as_int(total) if total is not None else "mapped reads",
                            source_file=relative,
                            source_field="MT_rate(%)",
                            formula="MT_reads / Total_reads * 100",
                            processing_phase="alignment_organelle_partition",
                            expert_tool="run_alignment_expert",
                            project_context=project_context,
                        )
                    )
            elif lower_name.endswith(".nrf_pbc.txt"):
                values = self._key_value_text(text)
                sample = values.get("Sample") or sample
                # 2026-07-02 引擎泛化排查后接通：calc_nrf_pbc.sh 产出的 *.nrf_pbc.txt
                # 里本来就带 Total_Fragments/Distinct_Locations/Locations_1read/
                # Locations_2reads 四个原始计数列（NRF=Distinct/Total，
                # PBC1=Locations_1read/Distinct，PBC2=Locations_1read/Locations_2reads，
                # 见同一文件里的公式）。此前这里只取了算好的 NRF/PBC1/PBC2 三个最终值，
                # numerator 传 None、denominator 传字符串占位符，导致
                # evidence_card_service.from_evidence() 拿不到真实 numerator_value/
                # denominator_value，metric_schema_service 只能把这三个指标当
                # citation_only 处理。这里改为把四个原始计数一并读出，按各自公式传入
                # 真实的 numerator/denominator，让 nrf/pbc1/pbc2 能在这条证据链上真正
                # 走 strict_formula_recalculation 重算校验。
                total_fragments = self._float(values.get("Total_Fragments"))
                distinct_locations = self._float(values.get("Distinct_Locations"))
                locations_1read = self._float(values.get("Locations_1read"))
                locations_2reads = self._float(values.get("Locations_2reads"))
                complexity_inputs = {
                    "NRF": (distinct_locations, total_fragments),
                    "PBC1": (locations_1read, distinct_locations),
                    "PBC2": (locations_1read, locations_2reads),
                }
                for source_key, metric_id in (("NRF", "nrf"), ("PBC1", "pbc1"), ("PBC2", "pbc2")):
                    value = self._float(values.get(source_key))
                    if value is not None:
                        numerator_value, denominator_value = complexity_inputs[source_key]
                        cards.append(
                            self._card(
                                project_id=project_id,
                                metric_id=metric_id,
                                metric=source_key,
                                category="AlignmentExpert",
                                sample=sample,
                                value=value,
                                display_value=f"{value:.4f}".rstrip("0").rstrip("."),
                                numerator=numerator_value,
                                denominator=(
                                    denominator_value
                                    if denominator_value is not None
                                    else "library complexity fragments"
                                ),
                                source_file=relative,
                                source_field=source_key,
                                formula=self._complexity_formula(source_key),
                                processing_phase="post_alignment_library_complexity",
                                expert_tool="run_alignment_expert",
                                project_context=project_context,
                            )
                        )
            elif lower_name.endswith(".library_complexity.txt"):
                row = self._picard_metrics(text)
                if row:
                    sample = row.get("LIBRARY") or sample
                    fraction = self._float(row.get("PERCENT_DUPLICATION"))
                    pairs = self._float(row.get("READ_PAIRS_EXAMINED"))
                    duplicates = self._float(row.get("READ_PAIR_DUPLICATES"))
                    if fraction is not None:
                        cards.append(
                            self._card(
                                project_id=project_id,
                                metric_id="picard_duplicate_pair_rate_percent",
                                metric_family="duplicate_rate_percent",
                                measurement_id="picard_duplicate_pair_rate_percent",
                                measurement_definition="Picard PERCENT_DUPLICATION over examined read pairs",
                                metric="Picard duplication rate",
                                category="AlignmentExpert",
                                sample=sample,
                                value=round(fraction * 100, 4),
                                display_value=f"{fraction * 100:.2f}%",
                                numerator=self._as_int(duplicates),
                                denominator=self._as_int(pairs) if pairs is not None else "examined read pairs",
                                denominator_name="examined read pairs",
                                denominator_value=self._as_int(pairs),
                                numerator_name="duplicate read pairs",
                                numerator_value=self._as_int(duplicates),
                                counting_unit="read_pairs",
                                population_scope="Picard examined read pairs",
                                source_file=relative,
                                source_field="PERCENT_DUPLICATION",
                                formula="PERCENT_DUPLICATION * 100",
                                processing_phase="post_alignment_library_complexity",
                                expert_tool="run_alignment_expert",
                                project_context=project_context,
                            )
                        )
            if len(cards) > before_count:
                matched.append(relative)
        gaps = []
        for metric, label in (
            ("mapping_rate_percent", "mapping"),
            ("mt_rate_percent", "线粒体"),
            ("duplicate_rate_percent", "duplicate"),
            ("nrf", "NRF/PBC"),
        ):
            if not any(card.get("metric_id") == metric for card in cards):
                gaps.append(f"未找到 {label} 的独立诊断证据。")
        return self._result("alignment", cards, list(dict.fromkeys(matched)), gaps)

    def run_enrichment_expert(
        self,
        *,
        project_root: str | Path,
        project_id: str,
        project_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        root = Path(project_root)
        score_files = self._find_files(
            root,
            (
                "FRiP_score.xls",
                "FRiP_raw.txt",
                "*peak_number*.xls",
                "*spearman*Corr*.tab",
                "*pearson*Corr*.tab",
                "*correlation*.tsv",
                "*tss*.xls",
                "*tss*.txt",
                "*tss*.tsv",
                "*fragment*size*.xls",
                "*fragment*size*.txt",
                "*fragment*size*.tsv",
                "*spikein*.xls",
                "*spikein*.txt",
                "*spikein*.tsv",
                "*peak*stat*.xls",
                "*peak*stat*.tsv",
            ),
        )
        cards: list[dict[str, Any]] = []
        matched: list[str] = []
        used_samples: set[str] = set()
        for path in score_files[:40]:
            relative = self._relative(path, root)
            rows = self._tabular_rows(path)
            if not rows:
                continue
            lower_name = path.name.lower()
            if "corr" in lower_name or "correlation" in lower_name:
                correlation_cards = self._correlation_cards(
                    path,
                    project_id=project_id,
                    relative=relative,
                    project_context=project_context,
                )
                if correlation_cards:
                    cards.extend(correlation_cards)
                    matched.append(relative)
                continue
            specialized_cards = self._specialized_enrichment_cards(
                path,
                rows,
                project_id=project_id,
                relative=relative,
                project_context=project_context,
            )
            if specialized_cards:
                cards.extend(specialized_cards)
                matched.append(relative)
            added = False
            for row in rows:
                sample = str(row.get("Sample") or row.get("sample") or row.get("file") or "").strip()
                feature = str(row.get("featureType") or "").strip()
                observation_key = f"{sample}::{feature}" if feature else sample
                if "frip" in lower_name and sample and observation_key not in used_samples:
                    raw_value = row.get("FRiP") or row.get("percent")
                    value = self._float(raw_value)
                    if value is not None:
                        numerator = self._float(row.get("Reads_in_Peaks") or row.get("featureReadCount"))
                        denominator = self._float(row.get("Mapped_Reads") or row.get("totalReadCount"))
                        ratio = value / 100 if value > 1 else value
                        cards.append(
                            self._card(
                                project_id=project_id,
                                metric_id="frip_ratio",
                                measurement_id=(
                                    "cross_frip_reads_in_peak_set_ratio"
                                    if feature and feature != sample
                                    else "frip_reads_in_peaks_ratio"
                                ),
                                measurement_definition="reads_in_peaks / mapped_reads",
                                metric="FRiP",
                                category="EnrichmentExpert",
                                sample=(
                                    f"{sample} against {feature}"
                                    if feature and feature != sample
                                    else sample
                                ),
                                sample_name=sample,
                                peak_set=feature or sample,
                                comparison_type=(
                                    "cross_frip"
                                    if feature and feature != sample
                                    else "self_frip"
                                ),
                                pair_type=(
                                    experiment_design_service.classify_pair(
                                        sample,
                                        feature,
                                        (project_context or {}).get("experiment_design") or {},
                                    )
                                    if feature and feature != sample
                                    else "self"
                                ),
                                value=ratio,
                                display_value=f"{ratio * 100:.2f}%",
                                numerator=self._as_int(numerator),
                                numerator_name="reads in peaks",
                                numerator_value=self._as_int(numerator),
                                denominator=self._as_int(denominator) if denominator is not None else "mapped reads",
                                denominator_name="mapped reads",
                                denominator_value=self._as_int(denominator),
                                counting_unit="reads",
                                population_scope="mapped reads evaluated against the called peak set",
                                value_scale="fraction",
                                display_scale="percent",
                                source_file=relative,
                                source_field="FRiP" if row.get("FRiP") is not None else "percent",
                                formula="reads_in_peaks / mapped_reads",
                                processing_phase="post_peak_calling_enrichment",
                                expert_tool="run_enrichment_expert",
                                project_context=project_context,
                            )
                        )
                        used_samples.add(observation_key)
                        added = True
                peak_count = self._float(
                    row.get("PeakCount")
                    or row.get("Peak_Number")
                    or row.get("peak_count")
                    or row.get("PeakNum")
                )
                if peak_count is not None and sample:
                    cards.append(
                        self._card(
                            project_id=project_id,
                            metric_id="peak_count",
                            metric="Peak count",
                            category="EnrichmentExpert",
                            sample=sample,
                            value=self._as_int(peak_count),
                            display_value=str(self._as_int(peak_count)),
                            numerator=self._as_int(peak_count),
                            denominator="called peaks",
                            source_file=relative,
                            source_field="PeakCount",
                            formula="count(called_peak_records)",
                            processing_phase="peak_calling",
                            expert_tool="run_enrichment_expert",
                            project_context=project_context,
                        )
                    )
                    added = True
            if added:
                matched.append(relative)
        control_cards, control_gaps = self._control_role_cards(
            project_id=project_id,
            project_context=project_context,
        )
        cards.extend(control_cards)
        gaps = []
        if not any(card.get("metric_id") == "frip_ratio" for card in cards):
            gaps.append("未找到可追溯到 reads_in_peaks/mapped_reads 的 FRiP 证据。")
        if not any(card.get("metric_id") == "peak_count" for card in cards):
            gaps.append("未找到 peak 数量证据。")
        if not any(card.get("metric_id") == "tss_enrichment" for card in cards):
            gaps.append("未找到结构化 TSS enrichment 数值。")
        if not any(card.get("metric_id") == "fragment_size" for card in cards):
            gaps.append("未找到结构化 fragment size 汇总值。")
        if not any(str(card.get("metric_id") or "").startswith("spikein_") for card in cards):
            gaps.append("未找到结构化 spike-in 比对或归一化证据。")
        gaps.extend(control_gaps)
        gaps.append("相关性只能在确认样本角色后解释，不得把不同处理组自动视为生物学重复。")
        return self._result("enrichment", cards, list(dict.fromkeys(matched)), list(dict.fromkeys(gaps)))

    def _specialized_enrichment_cards(
        self,
        path: Path,
        rows: list[dict[str, str]],
        *,
        project_id: str,
        relative: str,
        project_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        lower_name = path.name.lower()
        for row in rows:
            sample = str(
                self._row_value(row, ("Sample", "sample", "SampleID", "file"))[0]
                or self._sample_from_path(path)
                or "-"
            ).strip()

            tss_raw, tss_field = self._row_value(
                row,
                (
                    "TSS_enrichment",
                    "TSS enrichment",
                    "TSS_Enrichment_Score",
                    "TSSEnrichment",
                    "TSS score",
                    "tss_score",
                ),
            )
            tss = self._float(tss_raw)
            if tss is not None and ("tss" in lower_name or tss_field):
                cards.append(
                    self._card(
                        project_id=project_id,
                        metric_id="tss_enrichment",
                        measurement_id="tss_enrichment_score",
                        measurement_definition="normalized signal around TSS relative to flanking background",
                        metric="TSS enrichment score",
                        category="EnrichmentExpert",
                        sample=sample,
                        value=tss,
                        display_value=f"{tss:.3f}".rstrip("0").rstrip("."),
                        denominator="TSS flanking background signal",
                        denominator_name="TSS flanking background signal",
                        counting_unit="normalized_signal",
                        population_scope="signal centered on annotated transcription start sites",
                        source_file=relative,
                        source_field=tss_field,
                        processing_phase="post_alignment_tss_enrichment",
                        expert_tool="run_enrichment_expert",
                        project_context=project_context,
                    )
                )

            for aliases, measurement_id, label in (
                (
                    ("Median_fragment_size", "Median fragment size", "fragment_median", "median_size"),
                    "median_fragment_size_bp",
                    "Median fragment size",
                ),
                (
                    ("Mean_fragment_size", "Mean fragment size", "fragment_mean", "mean_size"),
                    "mean_fragment_size_bp",
                    "Mean fragment size",
                ),
                (
                    ("Fragment_size", "Fragment size", "fragment_size"),
                    "reported_fragment_size_bp",
                    "Reported fragment size",
                ),
            ):
                raw, field = self._row_value(row, aliases)
                value = self._float(raw)
                if value is None or ("fragment" not in lower_name and not field):
                    continue
                cards.append(
                    self._card(
                        project_id=project_id,
                        metric_id="fragment_size",
                        measurement_id=measurement_id,
                        measurement_definition=f"{label} reported by the project fragment-size summary",
                        metric=label,
                        category="EnrichmentExpert",
                        sample=sample,
                        value=value,
                        display_value=f"{value:.1f} bp",
                        denominator="fragments included in the size summary",
                        denominator_name="fragments included in the size summary",
                        counting_unit="base_pairs",
                        population_scope="aligned fragments used for fragment-size QC",
                        source_file=relative,
                        source_field=field,
                        processing_phase="post_alignment_fragment_size_qc",
                        expert_tool="run_enrichment_expert",
                        project_context=project_context,
                    )
                )

            for aliases, measurement_id, label in (
                (
                    ("Median_peak_width", "Median peak width", "median_width"),
                    "median_peak_width_bp",
                    "Median peak width",
                ),
                (
                    ("Mean_peak_width", "Mean peak width", "mean_width", "Average_peak_width"),
                    "mean_peak_width_bp",
                    "Mean peak width",
                ),
            ):
                raw, field = self._row_value(row, aliases)
                value = self._float(raw)
                if value is None:
                    continue
                cards.append(
                    self._card(
                        project_id=project_id,
                        metric_id="peak_width",
                        measurement_id=measurement_id,
                        measurement_definition=f"{label} across called peak intervals",
                        metric=label,
                        category="EnrichmentExpert",
                        sample=sample,
                        value=value,
                        display_value=f"{value:.1f} bp",
                        denominator="called peak intervals",
                        denominator_name="called peak intervals",
                        counting_unit="base_pairs",
                        population_scope="called peak set",
                        source_file=relative,
                        source_field=field,
                        processing_phase="peak_calling",
                        expert_tool="run_enrichment_expert",
                        project_context=project_context,
                    )
                )

            spike_specs = (
                (
                    ("Mapped reads", "Mapped_reads", "spikein_mapped_reads"),
                    "spikein_mapped_reads",
                    "spikein_mapped_reads",
                    "Spike-in mapped reads",
                    "count",
                ),
                (
                    ("Unique mapping rate(%)", "Unique_mapping_rate", "spikein_unique_rate"),
                    "spikein_unique_mapping_rate_percent",
                    "spikein_unique_mapping_rate_percent",
                    "Spike-in unique mapping rate",
                    "percent",
                ),
                (
                    ("ScaleFactor", "Scaling factor", "scale_factor", "SpikeinScaleFactor"),
                    "spikein_scaling_factor",
                    "spikein_scaling_factor",
                    "Spike-in scaling factor",
                    "number",
                ),
            )
            for aliases, metric_id, measurement_id, label, value_scale in spike_specs:
                raw, field = self._row_value(row, aliases)
                value = self._float(raw)
                if value is None or "spike" not in lower_name:
                    continue
                display = (
                    f"{value:.2f}%"
                    if value_scale == "percent"
                    else str(self._as_int(value))
                    if value_scale == "count"
                    else f"{value:.6g}"
                )
                cards.append(
                    self._card(
                        project_id=project_id,
                        metric_id=metric_id,
                        measurement_id=measurement_id,
                        measurement_definition=f"{label} reported by the project spike-in alignment/normalization table",
                        metric=label,
                        category="EnrichmentExpert",
                        sample=sample,
                        value=self._as_int(value) if value_scale == "count" else value,
                        display_value=display,
                        denominator="spike-in alignment input" if "rate" in metric_id else "",
                        denominator_name="spike-in alignment input" if "rate" in metric_id else "",
                        counting_unit="reads" if value_scale in {"count", "percent"} else "scaling_factor",
                        population_scope="spike-in reference alignment",
                        value_scale=value_scale,
                        display_scale=value_scale,
                        source_file=relative,
                        source_field=field,
                        processing_phase="spikein_alignment_normalization",
                        expert_tool="run_enrichment_expert",
                        project_context=project_context,
                    )
                )
        return cards

    def _control_role_cards(
        self,
        *,
        project_id: str,
        project_context: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        context = project_context or {}
        design = context.get("experiment_design") or {}
        roles = [
            item
            for item in (design.get("samples") or [])
            if isinstance(item, dict)
        ]
        source_field = "experiment_design.samples"
        if not roles:
            roles = [
                item
                for item in (context.get("sample_roles") or [])
                if isinstance(item, dict)
            ]
            source_field = "sample_roles_compatibility_fallback"
        controls = [
            str(item.get("sample") or "")
            for item in roles
            if str(item.get("role") or "").lower() == "control"
            or any(
                token in str(item.get("role") or "").lower()
                for token in ("igg", "input", "control")
            )
        ]
        experiments = [
            str(item.get("sample") or "")
            for item in roles
            if str(item.get("sample") or "") and str(item.get("sample") or "") not in controls
        ]
        if not roles:
            return [], ["未读取到 sample_roles，无法核对 IgG/Input/Control 与实验样本的配对关系。"]
        if not controls:
            return [], ["sample_roles 中未识别到 IgG/Input/Control；需确认对照是否缺失或命名未被识别。"]
        source_file = str(context.get("samplelist_file") or "samplelist")
        bindings = []
        for item in roles:
            if str(item.get("sample") or "") not in controls:
                continue
            targets = [
                str(value)
                for value in item.get("control_for", []) or []
                if str(value).strip()
            ]
            bindings.append(
                f"{item.get('sample')} -> {', '.join(targets) if targets else 'unresolved'}"
            )
        display = (
            f"authoritative control relation: {'; '.join(bindings)}; "
            "peak-caller file binding unverified"
        )
        cards = [
            self._card(
                project_id=project_id,
                metric_id="control_binding_status",
                measurement_id="experiment_design_control_binding",
                measurement_definition="authoritative control_for relation from the structured experiment design; actual peak-caller file binding requires configuration evidence",
                metric="Control availability and binding status",
                category="EnrichmentExpert",
                sample=", ".join(experiments) or "project",
                value="available_binding_unverified",
                display_value=display,
                denominator="structured experiment design",
                denominator_name="structured experiment design",
                counting_unit="control_bindings",
                population_scope="project experimental design",
                source_file=source_file,
                source_field=source_field,
                processing_phase="experimental_design",
                expert_tool="run_enrichment_expert",
                project_context=project_context,
            )
        ]
        return cards, ["已识别对照样本，但仍需核对 peak caller 实际使用的 control 文件绑定关系。"]

    def _alignment_summary_cards(
        self,
        values: dict[str, str],
        *,
        project_id: str,
        sample: str,
        relative: str,
        project_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        total_reads = self._float(values.get("Total_Reads"))
        mapped_reads = self._float(values.get("Total_Mapped_Reads"))
        unique_reads = self._float(values.get("Unique_Mapped_Reads"))
        duplicate_reads = self._float(values.get("Picard_Duplicate_Reads"))
        mt_reads = self._float(values.get("MT_Mapped_Reads"))
        specs = (
            ("Mapping_Rate", "mapping_rate_percent", "Mapping rate", mapped_reads, total_reads, "mapped_reads / total_reads * 100"),
            (
                "Unique_Mapped_Rate",
                "unique_mapping_rate_percent",
                "Unique mapping rate",
                unique_reads,
                total_reads,
                "unique_mapped_reads / total_reads * 100",
            ),
            (
                "Picard_Duplication_Rate",
                "duplicate_rate_percent",
                "Picard duplication rate",
                duplicate_reads,
                mapped_reads,
                "duplicate_reads / mapped_reads * 100",
            ),
            ("MT_Ratio", "mt_rate_percent", "Mitochondrial alignment rate", mt_reads, mapped_reads, "mt_mapped_reads / mapped_reads * 100"),
        )
        for source_key, metric_id, metric, numerator, denominator, formula in specs:
            value = self._float(values.get(source_key))
            if value is None:
                continue
            cards.append(
                self._card(
                    project_id=project_id,
                    metric_id=metric_id,
                    metric=metric,
                    category="AlignmentExpert",
                    sample=sample,
                    value=value,
                    display_value=f"{value:.2f}%",
                    numerator=self._as_int(numerator),
                    denominator=self._as_int(denominator) if denominator is not None else "",
                    source_file=relative,
                    source_field=source_key,
                    formula=formula,
                    processing_phase=(
                        "alignment_organelle_partition"
                        if metric_id == "mt_rate_percent"
                        else "post_alignment_library_complexity"
                        if metric_id == "duplicate_rate_percent"
                        else "alignment"
                    ),
                    expert_tool="run_alignment_expert",
                    project_context=project_context,
                )
            )
        for source_key, formula in (
            ("NRF", "distinct_locations / total_fragments"),
            ("PBC1", "locations_with_one_read / distinct_locations"),
            ("PBC2", "locations_with_one_read / locations_with_two_reads"),
        ):
            value = self._float(values.get(source_key))
            if value is not None:
                cards.append(
                    self._card(
                        project_id=project_id,
                        metric_id=source_key.lower(),
                        metric=source_key,
                        category="AlignmentExpert",
                        sample=sample,
                        value=value,
                        display_value=f"{value:.4f}".rstrip("0").rstrip("."),
                        numerator=None,
                        denominator="library complexity fragments",
                        source_file=relative,
                        source_field=source_key,
                        formula=formula,
                        processing_phase="post_alignment_library_complexity",
                        expert_tool="run_alignment_expert",
                        project_context=project_context,
                    )
                )
        return cards

    @staticmethod
    def _plan_domains(plan: dict[str, Any]) -> list[str]:
        target_metrics = {
            str(item).lower()
            for item in (plan.get("target_metrics") or []) + (plan.get("related_metrics") or [])
        }
        selected_names = {
            str(item.get("name") or "")
            for item in (plan.get("selected_tools") or [])
            if isinstance(item, dict)
        }
        domains: list[str] = []
        for domain in ("qc", "alignment", "enrichment"):
            if target_metrics.intersection(ProjectExpertToolService.DOMAIN_METRICS[domain]):
                domains.append(domain)
        for name in selected_names:
            domain = ProjectExpertToolService.TOOL_DOMAINS.get(name)
            if domain and domain not in domains:
                domains.append(domain)
        return domains

    @staticmethod
    def _replan_after_observation(
        *,
        domain: str,
        observation: dict[str, Any],
        plan: dict[str, Any],
        completed_domains: list[str],
        pending_domains: list[str],
    ) -> list[str]:
        if observation.get("status") != "no_evidence":
            return []
        planned_metrics = {
            str(item).lower()
            for item in (plan.get("target_metrics") or []) + (plan.get("related_metrics") or [])
        }
        candidates: list[str] = []
        if domain == "enrichment" and planned_metrics.intersection(
            ProjectExpertToolService.DOMAIN_METRICS["alignment"]
        ):
            candidates.append("alignment")
        if domain == "alignment" and planned_metrics.intersection(
            ProjectExpertToolService.DOMAIN_METRICS["qc"]
        ):
            candidates.append("qc")
        if domain == "qc" and planned_metrics.intersection(
            ProjectExpertToolService.DOMAIN_METRICS["alignment"]
        ):
            candidates.append("alignment")
        return [
            candidate
            for candidate in candidates
            if candidate not in completed_domains and candidate not in pending_domains
        ]

    @staticmethod
    def _domain_reason(domain: str, plan: dict[str, Any], question: str) -> str:
        metrics = [
            str(item)
            for item in (plan.get("target_metrics") or []) + (plan.get("related_metrics") or [])
            if str(item) in ProjectExpertToolService.DOMAIN_METRICS[domain]
        ]
        return f"question={question[:120]}; relevant_metrics={','.join(metrics) or domain}"

    def _fastp_cards(
        self,
        path: Path,
        *,
        project_id: str,
        relative: str,
        project_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return []
        sample = self._sample_from_path(path)
        summary = payload.get("summary") if isinstance(payload, dict) else {}
        before = summary.get("before_filtering") if isinstance(summary, dict) else {}
        after = summary.get("after_filtering") if isinstance(summary, dict) else {}
        cards: list[dict[str, Any]] = []

        before_reads = self._float((before or {}).get("total_reads"))
        after_reads = self._float((after or {}).get("total_reads"))
        if before_reads and after_reads is not None:
            retention = after_reads / before_reads * 100
            cards.append(
                self._card(
                    project_id=project_id,
                    metric_id="clean_read_retention_percent",
                    metric="fastp read retention after filtering",
                    category="QCExpert",
                    sample=sample,
                    value=round(retention, 4),
                    display_value=f"{retention:.2f}%",
                    numerator=self._as_int(after_reads),
                    numerator_name="reads after filtering",
                    numerator_value=self._as_int(after_reads),
                    denominator=self._as_int(before_reads),
                    denominator_name="reads before filtering",
                    denominator_value=self._as_int(before_reads),
                    counting_unit="reads",
                    population_scope="fastp input reads",
                    source_file=relative,
                    source_field="summary.after_filtering.total_reads",
                    formula="after_filtering.total_reads / before_filtering.total_reads * 100",
                    processing_phase="trim_filter_output",
                    expert_tool="run_qc_expert",
                    project_context=project_context,
                )
            )

        adapter = payload.get("adapter_cutting") if isinstance(payload, dict) else {}
        adapter_reads = self._float((adapter or {}).get("adapter_trimmed_reads"))
        if before_reads and adapter_reads is not None:
            adapter_percent = adapter_reads / before_reads * 100
            cards.append(
                self._card(
                    project_id=project_id,
                    metric_id="adapter_percent",
                    measurement_id="fastp_adapter_trimmed_reads_percent",
                    measurement_definition="fastp adapter_trimmed_reads / before_filtering.total_reads * 100",
                    metric="fastp reads with adapter trimmed",
                    category="QCExpert",
                    sample=sample,
                    value=round(adapter_percent, 4),
                    display_value=f"{adapter_percent:.2f}%",
                    numerator=self._as_int(adapter_reads),
                    numerator_name="adapter-trimmed reads",
                    numerator_value=self._as_int(adapter_reads),
                    denominator=self._as_int(before_reads),
                    denominator_name="reads before filtering",
                    denominator_value=self._as_int(before_reads),
                    counting_unit="reads",
                    population_scope="fastp input reads",
                    source_file=relative,
                    source_field="adapter_cutting.adapter_trimmed_reads",
                    formula="adapter_trimmed_reads / before_filtering.total_reads * 100",
                    processing_phase="raw_reads_pre_trim",
                    expert_tool="run_qc_expert",
                    project_context=project_context,
                )
            )

        for phase_name, phase_payload, processing_phase in (
            ("before_filtering", before, "raw_reads_pre_trim"),
            ("after_filtering", after, "clean_reads_post_trim"),
        ):
            for field, metric_id, label in (
                ("q20_rate", "q20_ratio", "Q20 base ratio"),
                ("q30_rate", "q30_ratio", "Q30 base ratio"),
            ):
                value = self._float((phase_payload or {}).get(field))
                if value is None:
                    continue
                ratio = value if value <= 1 else value / 100
                percent = ratio * 100
                cards.append(
                    self._card(
                        project_id=project_id,
                        metric_id=metric_id,
                        measurement_id=f"fastp_{phase_name}_{field}",
                        metric=f"fastp {label} ({phase_name})",
                        category="QCExpert",
                        sample=sample,
                        value=round(ratio, 6),
                        display_value=f"{percent:.2f}%",
                        value_scale="fraction",
                        display_scale="percent",
                        numerator=None,
                        denominator_name=f"bases in {phase_name} reads",
                        denominator=f"bases in {phase_name} reads",
                        counting_unit="bases",
                        population_scope=f"fastp {phase_name} reads",
                        source_file=relative,
                        source_field=f"summary.{phase_name}.{field}",
                        formula=f"summary.{phase_name}.{field}",
                        processing_phase=processing_phase,
                        expert_tool="run_qc_expert",
                        project_context=project_context,
                    )
                )
        return cards

    def _fastqc_cards(
        self,
        text: str,
        *,
        project_id: str,
        sample: str,
        relative: str,
        project_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        lines = text.splitlines()
        in_module = False
        headers: list[str] = []
        maximum: float | None = None
        for line in lines:
            if line.startswith(">>Adapter Content"):
                in_module = True
                continue
            if in_module and line.startswith(">>END_MODULE"):
                break
            if not in_module:
                continue
            if line.startswith("#"):
                headers = line.lstrip("#").split("\t")
                continue
            parts = line.split("\t")
            for raw in parts[1:]:
                value = self._float(raw)
                if value is not None:
                    maximum = value if maximum is None else max(maximum, value)
        if maximum is None:
            return []
        lowered = relative.lower()
        if any(token in lowered for token in ("clean", "trim", "filtered", "after")):
            phase = "clean_reads_post_trim"
        elif any(token in lowered for token in ("raw", "before")):
            phase = "raw_reads_pre_trim"
        else:
            phase = "fastqc_input_unspecified"
        return [
            self._card(
                project_id=project_id,
                metric_id="adapter_percent",
                measurement_id="fastqc_adapter_content_max_percent",
                measurement_definition="maximum FastQC Adapter Content percentage across positions/adapters",
                metric="FastQC maximum Adapter Content",
                category="QCExpert",
                sample=sample,
                value=round(maximum, 4),
                display_value=f"{maximum:.2f}%",
                numerator=None,
                denominator="reads covering the evaluated sequence position",
                denominator_name="reads covering the evaluated sequence position",
                counting_unit="reads",
                population_scope=phase,
                source_file=relative,
                source_field="Adapter Content" + (f" ({', '.join(headers[1:])})" if len(headers) > 1 else ""),
                formula="max(Adapter Content percentage across positions and adapter categories)",
                processing_phase=phase,
                expert_tool="run_qc_expert",
                project_context=project_context,
            )
        ]

    def _correlation_cards(
        self,
        path: Path,
        *,
        project_id: str,
        relative: str,
        project_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        text = self._read_text(path)
        rows = [line.split("\t") for line in text.splitlines() if line.strip()]
        if len(rows) < 2 or len(rows[0]) < 2:
            return []
        headers = [item.strip() for item in rows[0][1:]]
        method = "spearman" if "spearman" in path.name.lower() else "pearson" if "pearson" in path.name.lower() else "correlation"
        cards: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows[1:]):
            if len(row) < 2:
                continue
            left = row[0].strip()
            for column_index, raw in enumerate(row[1:]):
                if column_index >= len(headers):
                    continue
                right = headers[column_index]
                if not left or not right or left == right or row_index > column_index:
                    continue
                value = self._float(raw)
                if value is None:
                    continue
                pair_type = experiment_design_service.classify_pair(
                    left,
                    right,
                    (project_context or {}).get("experiment_design") or {},
                )
                cards.append(
                    self._card(
                        project_id=project_id,
                        metric_id="correlation",
                        measurement_id=f"{method}_correlation",
                        measurement_definition=f"{method} correlation between paired sample signal vectors",
                        metric=f"{method.title()} correlation",
                        category="EnrichmentExpert",
                        sample=f"{left} vs {right}",
                        left_sample=left,
                        right_sample=right,
                        pair_type=pair_type,
                        value=round(value, 6),
                        display_value=f"{value:.4f}",
                        numerator=None,
                        denominator="paired signal features/bins",
                        denominator_name="paired signal features/bins",
                        counting_unit="paired_features",
                        population_scope=f"{left} and {right} signal vectors",
                        source_file=relative,
                        source_field=f"{left}::{right}",
                        formula=f"{method}(signal_vector_{left}, signal_vector_{right})",
                        processing_phase="cross_sample_signal_comparison",
                        expert_tool="run_enrichment_expert",
                        project_context=project_context,
                    )
                )
        return cards

    def _card(self, **payload: Any) -> dict[str, Any]:
        context = payload.pop("project_context", None) or {}
        config = context.get("config") if isinstance(context.get("config"), dict) else {}
        species = str((config or {}).get("species") or "")
        assay = str((config or {}).get("assay") or (config or {}).get("experiment_type") or "")
        evidence = {
            **payload,
            "species": species,
            "assay": assay,
            "severity": "unverified_threshold",
            "threshold_source": "",
            "threshold_rule": {},
            "threshold_needs_project_validation": True,
            "needs_verification": False,
            "evidence_grade": "direct_project_diagnostic_file",
            "conclusion_strength": "direct_observation",
        }
        return evidence_card_service.from_evidence(
            evidence,
            project_id=str(payload.get("project_id") or ""),
            species=species,
            assay=assay,
        )

    @staticmethod
    def _result(domain: str, cards: list[dict[str, Any]], matched: list[str], gaps: list[str]) -> dict[str, Any]:
        return {
            "tool": f"run_{domain}_expert",
            "status": "observed" if cards else "no_evidence",
            "summary": f"{domain} expert collected {len(cards)} evidence cards from {len(matched)} files.",
            "matched_files": matched,
            "evidence_cards": cards,
            "evidence_gaps": gaps,
        }

    @staticmethod
    def _find_files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
        files: list[Path] = []
        seen: set[str] = set()
        for pattern in patterns:
            try:
                matches = sorted(root.rglob(pattern))
            except OSError:
                matches = []
            for path in matches:
                key = str(path.resolve())
                if path.is_file() and key not in seen:
                    seen.add(key)
                    files.append(path)
        return files

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    @staticmethod
    def _relative(path: Path, root: Path) -> str:
        try:
            return str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            return str(path)

    @staticmethod
    def _sample_from_path(path: Path) -> str:
        name = path.name
        for suffix in (
            ".library_complexity.txt",
            ".summary.txt",
            ".mt_stat.txt",
            ".nrf_pbc.txt",
            ".trim.log",
            ".fastp.json",
            "_fastqc_data.txt",
        ):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return path.parent.name or "-"

    @staticmethod
    def _number(text: str, pattern: str) -> float | None:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return ProjectExpertToolService._float(match.group(1)) if match else None

    @staticmethod
    def _float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None

    @staticmethod
    def _as_int(value: float | None) -> int | None:
        return int(value) if value is not None else None

    @staticmethod
    def _key_value_text(text: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in text.splitlines():
            parts = re.split(r"\t+|\s{2,}", line.strip(), maxsplit=1)
            if len(parts) == 2:
                values[parts[0].strip()] = parts[1].strip().split()[0]
        return values

    @staticmethod
    def _picard_metrics(text: str) -> dict[str, str]:
        lines = [line for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if line.startswith("LIBRARY\t") and index + 1 < len(lines):
                headers = line.split("\t")
                values = lines[index + 1].split("\t")
                return dict(zip(headers, values))
        return {}

    @staticmethod
    def _tabular_rows(path: Path) -> list[dict[str, str]]:
        try:
            with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                return list(csv.DictReader(handle, delimiter="\t"))
        except OSError:
            return []

    @staticmethod
    def _row_value(row: dict[str, Any], aliases: tuple[str, ...]) -> tuple[Any, str]:
        normalized = {
            re.sub(r"[^a-z0-9]+", "", str(key).lower()): key
            for key in row
        }
        for alias in aliases:
            original = normalized.get(re.sub(r"[^a-z0-9]+", "", alias.lower()))
            if original is not None and row.get(original) not in (None, ""):
                return row.get(original), str(original)
        return None, ""

    @staticmethod
    def _complexity_formula(metric: str) -> str:
        return {
            "NRF": "distinct_locations / total_fragments",
            "PBC1": "locations_with_one_read / distinct_locations",
            "PBC2": "locations_with_one_read / locations_with_two_reads",
        }.get(metric, "")


project_expert_tool_service = ProjectExpertToolService()
