from __future__ import annotations

import re
from pathlib import Path
from time import perf_counter
from typing import Any

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.services.business_agent.metric_schema_service import metric_schema_service
from multi_agent.backed.app.services.business_agent.experiment_design_service import experiment_design_service
from multi_agent.backed.app.services.project_analysis_constants import LOG_NAME_TOKENS


# ── Module-level pure utility functions ───────────────────────────────────────

def clean_matrix_token(value: str) -> str:
    return (value or "").strip().strip("'").strip('"')


def parse_numeric(value: str | None) -> float | None:
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


def first_nonempty(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def first_nonempty_with_key(row: dict[str, str], *keys: str) -> tuple[str, str]:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value), key
    return "", ""


def parse_embedded_percent(value: str | None) -> float | None:
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


def normalize_ratio(
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
    embedded_percent = parse_embedded_percent(value)
    if embedded_percent is not None:
        return embedded_percent / 100.0
    parsed = parse_numeric(value)
    if parsed is None:
        return None
    if parsed > 1:
        return parsed / 100.0
    return parsed


def normalize_percent(
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
    embedded_percent = parse_embedded_percent(value)
    if embedded_percent is not None:
        return embedded_percent
    parsed = parse_numeric(value)
    if parsed is None:
        return None
    if parsed <= 1:
        return parsed * 100.0
    return parsed


def raw_table_value(row: dict[str, str], *keys: str) -> str:
    return first_nonempty(row, *keys)


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value))
    except ValueError:
        return None


def extract_motif_sample_name(file_path: Path) -> str:
    lower_name = file_path.name.lower()
    if lower_name.endswith("_meme.txt"):
        return file_path.name[:-9]
    if lower_name.endswith("_meme.html"):
        return file_path.name[:-10]
    if "_logo" in lower_name:
        return file_path.name.split("_logo", 1)[0]
    return file_path.stem


def read_correlation_rows(file_path: Path) -> list[dict[str, str]]:
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.rstrip("\n\r") for line in text.splitlines() if line.strip()]
    data_lines = [line for line in lines if not line.startswith("#")]
    if len(data_lines) < 2:
        raise ValueError(f"Correlation matrix has insufficient data: {file_path}")

    header_parts = [clean_matrix_token(part) for part in data_lines[0].split("\t")]
    sample_names = [part for part in header_parts[1:] if part]
    rows: list[dict[str, str]] = []

    for line in data_lines[1:]:
        parts = [clean_matrix_token(part) for part in line.split("\t")]
        if len(parts) < 2:
            continue
        sample = parts[0]
        values = parts[1:]
        row = {"Sample": sample}
        for index, other in enumerate(sample_names):
            row[other] = values[index] if index < len(values) else ""
        rows.append(row)
    return rows


def read_tsv_rows_strip_trailing(file_path: Path) -> list[dict[str, str]]:
    """读取 TSV 文件，容错处理行末尾多余制表符（如 Samples.rRNA_Globin_stat.xls）。"""
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.rstrip("\t\r\n") for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    headers = [h.strip() for h in lines[0].split("\t")]
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        parts = [p.strip() for p in line.split("\t")]
        row = {headers[i]: parts[i] if i < len(parts) else "" for i in range(len(headers))}
        rows.append(row)
    return rows


# ── Utility to resolve table kind (also used by main service) ─────────────────

def resolve_table_kind(file_path: Path) -> str | None:
    lower_path = str(file_path).lower().replace("/", "\\")
    lower_name = file_path.name.lower()
    if lower_name == "readsqc.xls":
        return "qc"
    if lower_name == "statistic_reads.xls":
        return "rnaseq_qc"
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
    if lower_name.startswith("correlation_summary") and file_path.suffix.lower() in {".xls", ".csv", ".tab"}:
        return "rnaseq_correlation"
    if lower_name == "samples.rrna_globin_stat.xls":
        return "rnaseq_reads_class"
    if lower_name == "samples.exprange.xls":
        return "rnaseq_gene_exp"
    # SilvaBlast 每样本 rRNA blast 统计：<sample>.stat.xls（排除 seq/species_rank/reads_num/tmp 中间文件）
    if (
        "silva" in lower_path
        and lower_name.endswith(".stat.xls")
        and not any(
            token in lower_name
            for token in (".stat.seq.", "species_rank", "reads_num", ".tmp", "tmp1", "tmp2", "tmp3")
        )
    ):
        return "rnaseq_silva"
    if "final_anno" in lower_name:
        return "diff_annotation"
    if "go_up" in lower_name or "go_down" in lower_name:
        return "diff_go"
    if "pathway" in lower_name and file_path.suffix.lower() in {".xls", ".csv", ".tab", ".tsv"}:
        return "diff_pathway"
    if "diff" in lower_path and file_path.suffix.lower() in {".xls", ".csv", ".tab", ".tsv"}:
        return "diff_table"
    return None


def looks_like_text_file(path: Path) -> bool:
    return path.suffix.lower() in {".txt", ".md", ".log", ".html", ".htm", ".bed", ".tsv", ".tab", ".csv", ".xls"}


def progress_stage_for_evidence(file_path: Path, table_kind: str | None) -> tuple[str, str]:
    lower_name = file_path.name.lower()
    if table_kind in {"qc", "rnaseq_qc"} or lower_name in {"readsqc.xls", "statistic_reads.xls"}:
        return "read_reads_qc", "ReadsQC"
    if table_kind == "alignment" or lower_name in {"alignmentqc.xls", "aligentqc.xls"}:
        return "read_alignment_qc", "AlignmentQC"
    if table_kind == "spikein":
        return "read_spikein", "Spike-in"
    if table_kind == "frip" or "frip" in lower_name:
        return "read_frip", "FRiP"
    if table_kind == "peak":
        return "read_peak", "Peak 统计"
    if table_kind in {"correlation", "rnaseq_correlation"}:
        return "read_correlation", "相关性矩阵"
    if table_kind == "rnaseq_reads_class":
        return "read_rnaseq_reads_class", "Reads 组成分析"
    if table_kind == "rnaseq_gene_exp":
        return "read_rnaseq_gene_exp", "基因表达分布"
    if table_kind == "rnaseq_silva":
        return "read_rnaseq_silva", "Silva rRNA 污染"
    if table_kind and table_kind.startswith("diff"):
        return "read_diff", "差异分析结果"
    if "readme" in lower_name:
        return "read_metric_guide", "指标说明"
    return "read_evidence_file", file_path.name


# ── Table-summary builders ────────────────────────────────────────────────────

class ProjectFileParserService:

    @classmethod
    def build_qc_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
        metrics = []
        for row in rows:
            sample = first_nonempty(row, "Sample", "Samples")
            clean_reads_raw = first_nonempty(row, "Clean Reads", "Total Clean Reads")
            clean_bases_raw = first_nonempty(row, "Clean Bases", "Total Clean Bases")
            adapter_raw = row.get("Adapter", "")
            adapter = normalize_percent(adapter_raw, "adapter_percent", "Adapter")
            q20_raw = first_nonempty(row, "Q20", "Clean_Q20")
            q30_raw = first_nonempty(row, "Q30", "Clean_Q30")
            q20 = normalize_ratio(q20_raw, "q20_ratio", "Q20")
            q30 = normalize_ratio(q30_raw, "q30_ratio", "Q30")
            clean_retention = parse_embedded_percent(clean_reads_raw)
            clean_base_retention = parse_embedded_percent(clean_bases_raw)
            dup_raw = first_nonempty(row, "Dup(%)", "Dup", "Duplication(%)")
            dup_percent = normalize_percent(dup_raw, "duplicate_rate_percent", "Dup(%)") if dup_raw else None
            metrics.append(
                {
                    "sample": sample,
                    "raw_reads": first_nonempty(row, "Raw Reads", "Total Raw Reads"),
                    "raw_read_count": parse_numeric(first_nonempty(row, "Raw Reads", "Total Raw Reads")),
                    "clean_reads": clean_reads_raw,
                    "clean_read_count": parse_numeric(clean_reads_raw),
                    "clean_read_retention_percent": clean_retention,
                    "clean_bases": clean_bases_raw,
                    "clean_base_retention_percent": clean_base_retention,
                    "adapter": adapter_raw,
                    "adapter_reads": parse_numeric(adapter_raw),
                    "adapter_percent": adapter,
                    "q20_ratio": q20,
                    "q30_ratio": q30,
                    "duplicate_rate_percent": dup_percent,
                }
            )
        return {"metrics": metrics, "findings": []}

    @classmethod
    def build_alignment_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
        metrics = []
        for row in rows:
            sample = first_nonempty(row, "Sample", "Samples", "Sample_ID", "SampleID", "Sample Name", "样本")
            mapping_raw, mapping_field = first_nonempty_with_key(
                row,
                "Mapping rate", "Mapping_Rate", "Mapping(%)", "Mapping", "Mapping%",
                "Total mapped ratio",
            )
            unique_raw, unique_field = first_nonempty_with_key(
                row,
                "Unique mapping rate",
                "Unique_Mapped_Rate",
                "Unique(%)",
                "Unique",
                "Unique%",
                "Uniq mapped ratio",
            )
            duplicate_raw, duplicate_field = first_nonempty_with_key(
                row,
                "Duplicate rate",
                "Picard_Duplication_Rate",
                "PERCENT_DUPLICATION",
                "Duplicate(%)",
                "Duplicate",
                "Duplicate%",
            )
            mt_raw, mt_field = first_nonempty_with_key(
                row, "chrMT/Pt rate", "MT_Ratio", "chrMT/Pt(%)", "chrMT/Pt", "chrMT/Pt%"
            )
            duplicate_metric = (
                "picard_duplicate_pair_rate_percent"
                if duplicate_field == "PERCENT_DUPLICATION"
                else "duplicate_rate_percent"
            )
            mapping = normalize_percent(mapping_raw, "mapping_rate_percent", mapping_field)
            unique = normalize_percent(unique_raw, "unique_mapping_rate_percent", unique_field)
            duplicate = normalize_percent(duplicate_raw, duplicate_metric, duplicate_field)
            mt_rate = normalize_percent(mt_raw, "mt_rate_percent", mt_field)
            nrf = normalize_ratio(row.get("NRF", ""), "nrf", "NRF")
            pbc1 = normalize_ratio(row.get("PBC1", ""), "pbc1", "PBC1")
            pbc2 = metric_schema_service.normalize(
                "pbc2", row.get("PBC2", ""), source_field="PBC2"
            ).get("value")
            metrics.append(
                {
                    "sample": sample,
                    "host_alignment_input_reads": parse_numeric(
                        first_nonempty(row, "Total_Reads", "Total Reads", "Alignment input reads")
                    ),
                    "total_mapped_reads": parse_numeric(
                        first_nonempty(row, "Total_Mapped_Reads", "Total Mapped Reads", "Mapped reads")
                    ),
                    "unique_mapped_reads": parse_numeric(
                        first_nonempty(row, "Unique_Mapped_Reads", "Unique Mapped Reads", "Unique mapping reads")
                    ),
                    "mt_mapped_reads": parse_numeric(
                        first_nonempty(row, "MT_Mapped_Reads", "MT Mapped Reads", "chrMT/Pt mapped reads")
                    ),
                    "mapping_rate_percent": mapping,
                    "unique_mapping_rate_percent": unique,
                    "duplicate_rate_percent": duplicate,
                    "mt_rate_percent": mt_rate,
                    "complexity": first_nonempty(row, "Est.Lib.Complexity", "Estimated_Library_Size", "Complexity"),
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
    def build_spikein_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
        metrics = []
        for row in rows:
            sample = row.get("Sample", "")
            unique_raw, unique_field = first_nonempty_with_key(
                row,
                "Unique mapping rate(%)",
                "Unique mapping rate",
                "Unique(%)",
            )
            unique_rate = normalize_percent(unique_raw, "spikein_unique_mapping_rate_percent", unique_field)
            metrics.append(
                {
                    "sample": sample,
                    "spikein_alignment_input_reads": parse_numeric(
                        first_nonempty(row, "Clean reads", "Clean Reads")
                    ),
                    "mapped_reads": parse_numeric(row.get("Mapped reads", "")),
                    "unique_mapped_reads": parse_numeric(
                        first_nonempty(row, "Unique mapping reads", "Unique mapped reads")
                    ),
                    "unique_mapping_rate_percent": unique_rate,
                    "scaling_factor": metric_schema_service.normalize(
                        "spikein_scaling_factor",
                        first_nonempty(row, "Scaling factor", "Scale factor", "Normalization factor"),
                        source_field="Scaling factor",
                    ).get("value"),
                }
            )
        return {"metrics": metrics, "findings": []}

    @classmethod
    def build_frip_summary(cls, rows: list[dict[str, str]], source_name: str = "") -> dict[str, Any]:
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
            for key in ("FRiP", "Frip", "frip", "Reads in peaks ratio", "ReadsInPeaksRatio", "percent"):
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
            peak_set = first_nonempty(row, "PeakSet", "Peak Set", "featureType", "Feature", "Reference sample")
            metrics.append(
                {
                    "sample": sample,
                    "frip_ratio": frip_value,
                    "peak_count": row.get("PeakCount", "") or row.get("Peaks_number", ""),
                    "reads_in_peaks": reads_in_peaks,
                    "mapped_reads": mapped_reads,
                    "peak_set": peak_set,
                    "comparison_type": "cross_frip" if peak_set and peak_set != sample else "self_frip",
                    "source_field": frip_field,
                }
            )
        return {"metrics": metrics, "findings": []}

    @staticmethod
    def merge_frip_metrics(
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
            current_has_counts = bool(current.get("reads_in_peaks") and current.get("mapped_reads"))
            incoming_has_counts = bool(item.get("reads_in_peaks") and item.get("mapped_reads"))
            if incoming_has_counts and not current_has_counts:
                merged[key] = item
        return [merged[key] for key in order]

    @staticmethod
    def merge_silva_metrics(
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for item in [*existing, *incoming]:
            if not isinstance(item, dict):
                continue
            sample = str(item.get("sample") or "")
            if not sample:
                continue
            if sample not in merged:
                merged[sample] = item
                order.append(sample)
            elif merged[sample].get("silva_total_ratio_percent") is None:
                merged[sample] = item
        return [merged[sample] for sample in order]

    @classmethod
    def build_rnaseq_silva_summary(
        cls, rows: list[dict[str, str]], sample: str = ""
    ) -> dict[str, Any]:
        """解析 SilvaBlast <sample>.stat.xls，取 Total 行的 Percentage 作为 Silva_total_ratio(%)。

        文件结构：Species / Count / Percentage 三列，首行 Species=='Total' 汇总本样本
        比对到 SILVA rRNA 库的 reads 占抽样 reads 的比例（如 6.02%）。样本名由文件名给出。
        """
        total_ratio: float | None = None
        total_count: float | None = None
        top1_species = ""
        top1_ratio: float | None = None
        for row in rows:
            species = first_nonempty(row, "Species", "species").strip()
            percentage = first_nonempty(row, "Percentage", "percentage", "Percent")
            count = first_nonempty(row, "Count", "count", "Reads")
            if species.lower() == "total":
                total_ratio = normalize_percent(
                    percentage, "silva_total_ratio_percent", "Silva_total_ratio(%)"
                )
                total_count = parse_numeric(count)
                continue
            if species and top1_ratio is None:
                top1_species = species
                top1_ratio = parse_numeric(percentage)
        metric = {
            "sample": sample,
            "silva_total_ratio_percent": total_ratio,
            "silva_total_count": total_count,
            "silva_top1_species": top1_species,
            "silva_top1_ratio_percent": top1_ratio,
        }
        findings: list[str] = []
        if total_ratio is not None and total_ratio > 10:
            findings.append(f"{sample or '样本'} Silva rRNA 占比偏高（{total_ratio:.2f}%）")
        return {"metrics": [metric], "findings": findings}

    @staticmethod
    def build_peak_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
        counts = {
            row.get("Sample", ""): int(float(row.get("Peaks_number", "0") or 0))
            for row in rows
            if row.get("Sample")
        }
        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        findings = [f"{ranked[-1][0]} peak 数量最低({ranked[-1][1]})"] if ranked else []
        return {"metrics": counts, "ranked": ranked, "findings": findings}

    @classmethod
    def build_rnaseq_reads_class_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
        """解析 RNA-seq Samples.rRNA_Globin_stat.xls，提取 mRNA/rRNA/exon/intronic/intergenic 比例。"""
        metrics = []
        for row in rows:
            sample = first_nonempty(row, "Samples", "Sample")
            if not sample:
                continue
            mrna_ratio = normalize_percent(row.get("mRNA_ratio(%)", ""), "mrna_ratio_percent", "mRNA_ratio(%)")
            rrna_ratio = normalize_percent(row.get("rRNA_ratio(%)", ""), "rrna_ratio_percent", "rRNA_ratio(%)")
            exon_ratio = normalize_percent(row.get("mRNA_Exon_ratio(%)", ""), "exon_ratio_percent", "mRNA_Exon_ratio(%)")
            intronic_ratio = normalize_percent(row.get("mRNA_Intronic_ratio(%)", ""), "intronic_ratio_percent", "mRNA_Intronic_ratio(%)")
            intergenic_ratio = normalize_percent(row.get("Intergenic_ratio(%)", ""), "intergenic_ratio_percent", "Intergenic_ratio(%)")
            metrics.append(
                {
                    "sample": sample,
                    "mrna_ratio_percent": mrna_ratio,
                    "rrna_ratio_percent": rrna_ratio,
                    "exon_ratio_percent": exon_ratio,
                    "intronic_ratio_percent": intronic_ratio,
                    "intergenic_ratio_percent": intergenic_ratio,
                }
            )
        findings: list[str] = []
        low_mrna = [item["sample"] for item in metrics if (item.get("mrna_ratio_percent") or 100) < 30]
        if low_mrna:
            findings.append(f"mRNA 比例偏低（< 30%）样本: {', '.join(low_mrna[:5])}")
        high_rrna = [item["sample"] for item in metrics if (item.get("rrna_ratio_percent") or 0) > 30]
        if high_rrna:
            findings.append(f"rRNA 比例偏高（> 30%）样本: {', '.join(high_rrna[:5])}")
        return {"metrics": metrics, "findings": findings}

    @classmethod
    def build_rnaseq_gene_exp_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
        """解析 RNA-seq Samples.ExpRange.xls，提取每个样本的检测基因总数。"""
        metrics = []
        for row in rows:
            sample = first_nonempty(row, "Sample", "Samples")
            if not sample:
                continue
            sum_raw = first_nonempty(row, "Sum.", "Sum")
            gene_count = parse_numeric(sum_raw)
            metrics.append(
                {
                    "sample": sample,
                    "detected_gene_count": int(gene_count) if gene_count is not None else None,
                }
            )
        findings: list[str] = []
        low_gene = [item["sample"] for item in metrics if (item.get("detected_gene_count") or 99999) < 10000]
        if low_gene:
            findings.append(f"检测基因数偏少（< 10,000）样本: {', '.join(low_gene[:5])}")
        return {"metrics": metrics, "findings": findings}

    @classmethod
    def build_correlation_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
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
                num = normalize_ratio(value, "correlation", other)
                if num is None:
                    continue
                pair = (sample, other, num)
                pairs.append({"left": sample, "right": other, "value": num})
                if max_pair is None or num > max_pair[2]:
                    max_pair = pair
                if min_pair is None or num < min_pair[2]:
                    min_pair = pair
        return {
            "max_pair": max_pair,
            "min_pair": min_pair,
            "pairs": pairs,
            "strata": {},
            "findings": [],
        }

    @classmethod
    def stratify_correlation_summary(
        cls,
        summary: dict[str, Any],
        experiment_design: dict[str, Any] | None,
    ) -> dict[str, Any]:
        import copy as _copy
        enriched = _copy.deepcopy(summary)
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
            min(replicate_pairs, key=lambda item: item["value"]) if replicate_pairs else None
        )
        enriched["findings"] = []
        return enriched

    @classmethod
    def build_diff_annotation_summary(cls, rows: list[dict[str, str]]) -> dict[str, Any]:
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
        return {"change_counts": change_counts, "top_genes": top_genes, "findings": findings}

    @classmethod
    def build_enrichment_summary(cls, rows: list[dict[str, str]], enrichment_type: str) -> dict[str, Any]:
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
        return {"enrichment_type": enrichment_type, "top_terms": top_terms, "findings": findings}

    @classmethod
    def aggregate_motif_metrics(cls, motif_items: list[dict[str, Any]]) -> dict[str, Any]:
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
            key=lambda item: safe_float(item.get("top_motif_evalue")) or float("inf"),
        )
        findings: list[str] = []
        if ranked_samples:
            best = ranked_samples[0]
            findings.append(
                f"motif 聚合结果显示 {best.get('sample')} 的 top motif 为 {best.get('top_motif_name')}, E-value={best.get('top_motif_evalue')}"
            )
        return {"sample_count": len(samples), "samples": ranked_samples, "findings": findings}

    @staticmethod
    def collect_evidence_notes_from_file_summaries(file_summaries: list[dict[str, Any]]) -> list[str]:
        findings: list[str] = []
        for item in file_summaries:
            if not isinstance(item, dict):
                continue
            summary = item.get("summary") or {}
            if isinstance(summary, dict):
                findings.extend(str(value) for value in (summary.get("findings") or []) if str(value).strip())
        return list(dict.fromkeys(findings))

    @classmethod
    def parse_evidence_file(
        cls,
        *,
        root: Path,
        file_path: Path,
        experiment_design: dict[str, Any],
        cache: Any,  # ProjectParseCache instance
        summarize_text_fn: Any,  # callable(file_path, preview) -> dict
        target_metrics: list[str] | None = None,
        project_id: str = "",
        exploration_hint: dict[str, Any] | None = None,
        project_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from multi_agent.backed.app.infrastructure.tools.local.project_reader import (
            read_log_snippet,
            read_table_rows,
            read_text_snippet,
        )
        relative = str(file_path.relative_to(root))
        lower_name = file_path.name.lower()
        file_started_at = perf_counter()
        table_kind = resolve_table_kind(file_path)
        p_stage, p_label = progress_stage_for_evidence(file_path, table_kind)
        try:
            from multi_agent.backed.app.services.project_analysis_constants import STRUCTURED_TABLE_FILES
            if table_kind is not None or lower_name in STRUCTURED_TABLE_FILES or table_kind in {
                "diff_annotation", "diff_go", "diff_pathway", "diff_table",
            }:
                cache_kind = f"table:v2:{table_kind or lower_name}"
                cached = cache._get_cached_parse(file_path, cache_kind)
                if cached is None:
                    if table_kind in {"correlation", "rnaseq_correlation"}:
                        rows = read_correlation_rows(file_path)
                    elif table_kind == "rnaseq_reads_class":
                        rows = read_tsv_rows_strip_trailing(file_path)
                    else:
                        rows = read_table_rows(file_path)
                    if table_kind in {"qc", "rnaseq_qc"}:
                        summary = cls.build_qc_summary(rows)
                        metric_payload = {"target": "qc", "value": summary.get("metrics", []), "mode": "replace"}
                    elif table_kind == "rnaseq_reads_class":
                        summary = cls.build_rnaseq_reads_class_summary(rows)
                        metric_payload = {"target": "rnaseq_reads_class", "value": summary.get("metrics", []), "mode": "replace"}
                    elif table_kind == "rnaseq_gene_exp":
                        summary = cls.build_rnaseq_gene_exp_summary(rows)
                        metric_payload = {"target": "rnaseq_gene_exp", "value": summary.get("metrics", []), "mode": "replace"}
                    elif table_kind == "rnaseq_silva":
                        silva_sample = re.sub(r"\.stat\.xls$", "", file_path.name, flags=re.IGNORECASE)
                        summary = cls.build_rnaseq_silva_summary(rows, sample=silva_sample)
                        metric_payload = {"target": "rnaseq_silva", "value": summary.get("metrics", []), "mode": "merge_silva"}
                    elif table_kind == "rnaseq_correlation":
                        summary = cls.build_correlation_summary(rows)
                        metric_payload = {"target": "correlation", "value": summary, "mode": "replace"}
                    elif table_kind == "spikein":
                        summary = cls.build_spikein_summary(rows)
                        metric_payload = {"target": "spikein", "value": summary.get("metrics", []), "mode": "replace"}
                    elif table_kind == "alignment":
                        summary = cls.build_alignment_summary(rows)
                        metric_payload = {"target": "alignment", "value": summary.get("metrics", []), "mode": "replace"}
                    elif table_kind == "peak":
                        summary = cls.build_peak_summary(rows)
                        metric_payload = {
                            "target": "peak",
                            "value": {"metrics": summary.get("metrics", {}), "ranked": summary.get("ranked", [])},
                            "mode": "replace",
                        }
                    elif table_kind == "frip":
                        summary = cls.build_frip_summary(rows, source_name=file_path.name)
                        metric_payload = {"target": "frip", "value": summary.get("metrics", []), "mode": "merge_frip"}
                    elif table_kind == "correlation":
                        summary = cls.build_correlation_summary(rows)
                        metric_payload = {"target": "correlation", "value": summary, "mode": "replace"}
                    elif table_kind == "diff_annotation":
                        summary = cls.build_diff_annotation_summary(rows)
                        metric_payload = {
                            "target": "diff",
                            "value": {"kind": "diff_annotation", "change_counts": summary.get("change_counts", {}), "top_genes": summary.get("top_genes", [])},
                            "mode": "append",
                        }
                    elif table_kind == "diff_go":
                        summary = cls.build_enrichment_summary(rows, "GO")
                        metric_payload = {
                            "target": "diff",
                            "value": {"kind": "diff_go", "top_terms": summary.get("top_terms", [])},
                            "mode": "append",
                        }
                    elif table_kind == "diff_pathway":
                        summary = cls.build_enrichment_summary(rows, "Pathway")
                        metric_payload = {
                            "target": "diff",
                            "value": {"kind": "diff_pathway", "top_terms": summary.get("top_terms", [])},
                            "mode": "append",
                        }
                    else:
                        summary = cls.build_enrichment_summary(rows, "DiffTable")
                        metric_payload = {
                            "target": "diff",
                            "value": {"kind": "diff_table", "top_terms": summary.get("top_terms", [])},
                            "mode": "append",
                        }
                    cached = cache._set_cached_parse(
                        file_path, cache_kind,
                        {"summary": summary, "metric_payload": metric_payload},
                    )
                summary = cached["summary"]
                metric_payload = cached["metric_payload"]
                if table_kind in {"correlation", "rnaseq_correlation"}:
                    summary = cls.stratify_correlation_summary(summary, experiment_design)
                    metric_payload = {"target": "correlation", "value": summary, "mode": "replace"}
                return {
                    "relative": relative,
                    "progress_stage": p_stage,
                    "progress_label": p_label,
                    "file_summary": {"file": relative, "type": "table", "summary": summary},
                    "evidence_status": {
                        "file": relative, "status": "ok", "type": "table",
                        "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                    },
                    "parsed_metric_update": metric_payload,
                    "findings": summary.get("findings", []),
                }

            # Phase 1.2（project_analysis_agent_upgrade_plan.md）：字段发现层兜底。
            # resolve_table_kind() 认不出的表格文件此前直接落入纯文本摘要分支，一个指标都
            # 提取不出来；这里补一次"猜字段映射 + 重算校验"，只有通过 metric_schema_service
            # 自洽/重算校验的值才会被采信，不影响上面已识别文件类型的既有 11 个 build_*_summary
            # 路径。target_metrics 为空（默认值，兼容旧调用方）时行为与改造前完全一致。
            if target_metrics and file_path.suffix.lower() in {".xls", ".csv", ".tab", ".tsv"}:
                formula_hints: list[dict[str, Any]] = []
                try:
                    from multi_agent.backed.app.services.project_field_discovery_service import (
                        discover_and_extract,
                    )
                    from multi_agent.backed.app.services.project_code_semantics_service import (
                        analyze_project_workflow_scripts,
                    )
                    from multi_agent.backed.app.services.business_agent.assay_analysis_service import (
                        assay_analysis_service,
                    )

                    # 尽力而为地把 assay 传下去，让 formula_hint 按 assay 过滤/降级
                    # （见 project_code_semantics_service._filter_hints_by_assay）。
                    # detect_assay() 识别不出具体 assay 时返回 "generic"——这里要转成
                    # None，表示"不确定，不做过滤"，而不是当成一个真实存在、
                    # 谁都不适用的 assay 名字去过滤掉所有变体。
                    detected_assay = assay_analysis_service.detect_assay(
                        str(experiment_design.get("config_assay") or "")
                    )
                    assay = detected_assay if detected_assay != "generic" else None
                    # project_analysis_phase1.5_auto_promotion_revision.md §13 解决方法 2：
                    # 每个同步阶段设独立硬子预算。analyze_project_workflow_scripts 内部的模型
                    # 分支已经改成短同步等待+后台完成回调（见 project_code_semantics_service.py
                    # 顶部注释），本身不再是无界阻塞；这里再加一层软性预算兜底——万一静态提取
                    # 本身在异常大的脚本/文件上变慢，也不放任单个文件的字段发现无限占用
                    # analyze_project_data 的整体预算，超时直接跳过这一步，降级为"没有字段
                    # 发现证据"，不影响其它已识别文件类型的证据继续正常产出。
                    _field_discovery_started_at = perf_counter()
                    formula_hints = analyze_project_workflow_scripts(
                        root, assay=assay, project_config=project_config, target_metrics=target_metrics
                    )
                    _stage_budget = float(getattr(settings, "FIELD_DISCOVERY_STAGE_TIMEOUT_SECONDS", 6.0))
                    if perf_counter() - _field_discovery_started_at > _stage_budget:
                        logger.warning(
                            "project_file_parser stage=field_discovery status=budget_exceeded "
                            "file=%s budget_seconds=%.1f elapsed_seconds=%.2f",
                            relative,
                            _stage_budget,
                            perf_counter() - _field_discovery_started_at,
                        )
                        field_discovery_hits = []
                    else:
                        # Stage B-补（project_analysis_exploration_and_evolution_plan.md）：
                        # known_samples 直接从 experiment_design 派生，和
                        # project_analysis_service.py 里 user_assertion_service 用的
                        # 同一套取值逻辑，不新增一个独立参数。
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
                        known_samples = [s for s in known_samples if s]
                        field_discovery_hits = discover_and_extract(
                            file_path,
                            target_metrics,
                            formula_hints=formula_hints,
                            exploration_hint=exploration_hint,
                            known_samples=known_samples,
                        )
                    for hit in field_discovery_hits:
                        hit["source_file"] = relative

                    # project_analysis_phase1.5_auto_promotion_revision.md 第一部分 + §10：
                    # 字段发现层猜出来的值默认只能拿到 "screening_signal" trust_level（见
                    # evidence_card_service / answer_quality_service 的信任分级）。如果这条
                    # 命中的指标在项目实际使用的 SOP 脚本里能找到对应的 formula_hint（即
                    # Phase 1.1 代码语义解析层确实读到了这段公式，而不是纯靠列名猜的），就
                    # 尝试把它推进脚本公式转正流程——命中即可把这条证据的 trust_level 升级为
                    # "recalculated"，不再是"未核实公式"。任何异常都不能影响字段发现主流程。
                    cls._apply_script_formula_trust_upgrade(field_discovery_hits, formula_hints)
                except Exception:  # noqa: BLE001
                    field_discovery_hits = []

                # Phase 1.5：候选指标队列——完全独立的副作用，只记录观测，不影响本次
                # parsed_metrics/evidence_chain。任何异常都不能影响文件解析主流程。
                try:
                    from multi_agent.backed.app.services.project_field_discovery_service import (
                        discover_novel_metric_candidates,
                    )
                    from multi_agent.backed.app.services.business_agent.candidate_metric_service import (
                        record_observation,
                    )

                    for novel in discover_novel_metric_candidates(file_path):
                        record_observation(
                            project_id=project_id,
                            metric_guess_label=novel["metric_guess_label"],
                            unit_guess=novel["unit_guess"],
                            source_file=relative,
                            numerator_field=novel["numerator_field"],
                            denominator_field=novel["denominator_field"],
                            display_field=novel["display_field"],
                            sample=novel["sample"],
                            value=novel["value"],
                        )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "project_file_parser stage=candidate_metric_observe status=failed file=%s",
                        relative,
                        exc_info=True,
                    )
                if field_discovery_hits:
                    return {
                        "relative": relative,
                        "progress_stage": p_stage,
                        "progress_label": p_label,
                        "file_summary": {
                            "file": relative,
                            "type": "field_discovery",
                            "summary": {"metrics": field_discovery_hits, "findings": []},
                        },
                        "evidence_status": {
                            "file": relative, "status": "ok", "type": "field_discovery",
                            "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                        },
                        "parsed_metric_update": {
                            "target": "field_discovery",
                            "value": field_discovery_hits,
                            "mode": "extend",
                        },
                        "findings": [],
                    }

            cache_kind = "text_summary"
            cached = cache._get_cached_parse(file_path, cache_kind)
            if cached is None:
                if file_path.suffix.lower() == ".log":
                    preview = read_log_snippet(file_path)
                elif looks_like_text_file(file_path):
                    preview = read_text_snippet(file_path)
                else:
                    preview = ""
                summary = summarize_text_fn(file_path, preview)
                cached = cache._set_cached_parse(
                    file_path, cache_kind, {"preview": preview, "summary": summary},
                )
            preview = cached["preview"]
            summary = cached["summary"]
            parsed_metric_update = None
            findings = []
            if summary.get("kind") in {"diff", "motif", "igv"}:
                payload = {key: value for key, value in summary.items() if key not in {"preview", "findings"}}
                if summary.get("kind") == "motif":
                    payload["sample"] = extract_motif_sample_name(file_path)
                payload["file"] = relative
                parsed_metric_update = {"target": summary["kind"], "value": payload, "mode": "append"}
                findings = summary.get("findings", [])
            return {
                "relative": relative,
                "progress_stage": p_stage,
                "progress_label": p_label,
                "file_summary": {"file": relative, "type": "text", "preview": preview, "summary": summary},
                "evidence_status": {
                    "file": relative, "status": "ok", "type": "text",
                    "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                },
                "parsed_metric_update": parsed_metric_update,
                "findings": findings,
            }
        except Exception as exc:
            return {
                "relative": relative,
                "progress_stage": p_stage,
                "progress_label": p_label,
                "error": str(exc),
                "file_summary": {"file": relative, "type": "error", "error": str(exc)},
                "evidence_status": {
                    "file": relative, "status": "error", "type": "unknown", "error": str(exc),
                    "duration_ms": round((perf_counter() - file_started_at) * 1000, 2),
                },
                "parsed_metric_update": None,
                "findings": [],
            }

    @classmethod
    def _apply_script_formula_trust_upgrade(
        cls,
        field_discovery_hits: list[dict],
        formula_hints: list[dict],
    ) -> None:
        """给字段发现命中的证据尝试脚本公式转正升级（见调用方注释）。就地修改
        `field_discovery_hits`：命中就追加 `trust_level="recalculated"` 和
        `promotion_key`，未命中不做任何改动（沿用默认的 screening_signal 分级）。
        """
        if not field_discovery_hits or not formula_hints:
            return
        try:
            from multi_agent.backed.app.services.business_agent.metric_schema_service import (
                metric_schema_service,
            )
            from multi_agent.backed.app.services.business_agent.script_formula_promotion_service import (
                evaluate_and_maybe_bless,
            )
            from multi_agent.backed.app.config.settings import settings

            if not bool(settings.FORMULA_PROMOTION_ENABLED):
                return

            hints_by_metric: dict[str, list[dict]] = {}
            for hint in formula_hints:
                metric_id = metric_schema_service.canonical_id(hint.get("metric_guess"))
                if metric_id and hint.get("script_path"):
                    hints_by_metric.setdefault(metric_id, []).append(hint)

            # code review 修复（建议#2）：一个文件里的 field_discovery_hits 通常是"每个
            # 样本一条"，同一个 metric_id 在同一个文件里会重复出现很多次。之前对每一条 hit
            # 都单独调一次 evaluate_and_maybe_bless（脚本哈希+一次 DB 往返），10 个样本就是
            # 10 倍的冗余开销。现在按 metric_id 去重，每个文件里每个指标只评估一次，评估
            # 结果套用到该指标下的所有 hit。（compute_script_hash 本身也按 mtime 做了缓存，
            # 这里的去重是在此基础上进一步减少重复的 evaluate_and_maybe_bless/DB 调用次数。）
            bless_result_by_metric: dict[str, dict] = {}
            for hit in field_discovery_hits:
                metric_id = metric_schema_service.canonical_id(hit.get("metric_id"))
                if metric_id not in bless_result_by_metric:
                    candidates = hints_by_metric.get(metric_id) or []
                    if not candidates:
                        bless_result_by_metric[metric_id] = {}
                    else:
                        # 同一指标可能命中多个脚本/多条线索，取置信度最高的一条。
                        best_hint = max(candidates, key=lambda h: float(h.get("confidence") or 0))
                        first_hit_with_fields = next(
                            (
                                h
                                for h in field_discovery_hits
                                if metric_schema_service.canonical_id(h.get("metric_id")) == metric_id
                            ),
                            hit,
                        )
                        bless_result_by_metric[metric_id] = evaluate_and_maybe_bless(
                            script_path=best_hint.get("script_path"),
                            metric_id=metric_id,
                            formula_variant=best_hint.get("variant_id"),
                            unknown_variant=bool(best_hint.get("unknown_variant", True)),
                            discovered_by=str(best_hint.get("discovered_by") or "code_semantics_static"),
                            numerator_field=str(first_hit_with_fields.get("numerator_field") or ""),
                            denominator_field=str(first_hit_with_fields.get("denominator_field") or ""),
                            recalculation_passed=True,  # discover_and_extract 已经过 normalize() 校验
                            is_new_candidate=False,
                            target_contract=metric_schema_service.verifier_contract(metric_id),
                        )
                result = bless_result_by_metric[metric_id]
                if result.get("blessed"):
                    hit["trust_level"] = "recalculated"
                    hit["promotion_key"] = result.get("promotion_key")
        except Exception:  # noqa: BLE001
            logger.warning(
                "project_file_parser stage=script_formula_trust_upgrade status=failed",
                exc_info=True,
            )

    @classmethod
    def apply_parsed_metric_update(
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
        if mode == "extend":
            parsed_metrics.setdefault(target, []).extend(value or [])
            return
        if mode == "merge_frip":
            parsed_metrics["frip"] = cls.merge_frip_metrics(
                parsed_metrics.get("frip", []) or [],
                value or [],
            )
            return
        if mode == "merge_silva":
            parsed_metrics["rnaseq_silva"] = cls.merge_silva_metrics(
                parsed_metrics.get("rnaseq_silva", []) or [],
                value or [],
            )
            return
        parsed_metrics[target] = value

    @staticmethod
    def _is_non_error_log_stat_line(lower_line: str) -> bool:
        normalized = " ".join((lower_line or "").split())
        return (
            "no. of allowed errors" in normalized
            or "number of allowed errors" in normalized
            or "length count expect max.err error counts" in normalized
        )

    @classmethod
    def summarize_text_evidence(cls, file_path: Path, preview: str) -> dict[str, Any]:
        lower_name = file_path.name.lower()
        lines = [line.strip() for line in preview.splitlines() if line.strip()]
        summary: dict[str, Any] = {"preview": preview[:1500], "findings": []}

        _is_log = (
            file_path.suffix.lower() == ".log"
            or any(tok in lower_name for tok in LOG_NAME_TOKENS)
        )
        if _is_log:
            error_lines: list[str] = []
            warning_lines: list[str] = []
            for line in lines:
                ll = line.lower()
                if cls._is_non_error_log_stat_line(ll):
                    continue
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


project_file_parser_service = ProjectFileParserService()
