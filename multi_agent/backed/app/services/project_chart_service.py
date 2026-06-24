from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any
from uuid import uuid4

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from multi_agent.backed.app.infrastructure.tools.local.project_reader import (
    find_files,
    list_project_files,
    read_table_rows,
    resolve_project_root,
)
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.services.chart_spec_service import chart_spec_service


@dataclass(frozen=True)
class MetricSpec:
    key: str
    aliases: tuple[str, ...]
    preferred_files: tuple[str, ...]
    sample_columns: tuple[str, ...]
    value_columns: tuple[str, ...]
    default_chart: str
    value_label: str
    percent: bool = False


class ProjectChartService:
    CHART_ROOT = Path(__file__).resolve().parents[1] / "generated" / "charts"
    CHART_URL_PREFIX = "/generated/charts"
    SUPPORTED_CHART_TYPES = {"bar", "line", "heatmap"}
    METRICS: tuple[MetricSpec, ...] = (
        MetricSpec(
            key="alignment_summary",
            aliases=("alignment summary", "alignmentqc", "样本对比", "指标对比", "对比图", "比较图"),
            preferred_files=("AlignmentQC.xls", "AlignmentQC.tsv", "AlignmentQC.csv", "AligentQC.xls", "AligentQC.tsv", "AligentQC.csv"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=(),
            default_chart="bar",
            value_label="Percent (%)",
            percent=True,
        ),
        MetricSpec(
            key="q30",
            aliases=("q30", "clean q30", "测序质量", "碱基质量"),
            preferred_files=("ReadsQC.xls", "ReadsQC.tsv", "ReadsQC.csv"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=("Q30", "Clean Q30", "Q30(%)", "Q30%", "clean_q30"),
            default_chart="bar",
            value_label="Q30 (%)",
            percent=True,
        ),
        MetricSpec(
            key="q20",
            aliases=("q20", "clean q20"),
            preferred_files=("ReadsQC.xls", "ReadsQC.tsv", "ReadsQC.csv"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=("Q20", "Clean Q20", "Q20(%)", "Q20%", "clean_q20"),
            default_chart="bar",
            value_label="Q20 (%)",
            percent=True,
        ),
        MetricSpec(
            key="mapping",
            aliases=("mapping", "mapping rate", "mapped reads", "比对率", "比对", "有效比对率"),
            preferred_files=("AlignmentQC.xls", "AlignmentQC.tsv", "AlignmentQC.csv", "AligentQC.xls", "AligentQC.tsv", "AligentQC.csv"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=("Mapping(%)", "Mapping", "Mapping%", "Mapped(%)", "Mapped Reads(%)", "mapping_rate"),
            default_chart="bar",
            value_label="Mapping (%)",
            percent=True,
        ),
        MetricSpec(
            key="unique",
            aliases=("unique", "unique rate", "unique mapping", "唯一比对", "唯一比对率"),
            preferred_files=("AlignmentQC.xls", "AlignmentQC.tsv", "AlignmentQC.csv", "AligentQC.xls", "AligentQC.tsv", "AligentQC.csv"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=("Unique(%)", "Unique", "Unique%", "Unique Mapping(%)", "unique_rate"),
            default_chart="bar",
            value_label="Unique (%)",
            percent=True,
        ),
        MetricSpec(
            key="duplicate",
            aliases=("duplicate", "duplicates", "dup rate", "重复率", "重复"),
            preferred_files=("AlignmentQC.xls", "AlignmentQC.tsv", "AlignmentQC.csv", "AligentQC.xls", "AligentQC.tsv", "AligentQC.csv"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=("Duplicate(%)", "Duplicate", "Duplicate%", "Dup(%)", "duplicate_rate"),
            default_chart="bar",
            value_label="Duplicate (%)",
            percent=True,
        ),
        MetricSpec(
            key="chrmt_pt",
            aliases=("chrmt", "chrmt/pt", "chrmtpt", "线粒体", "叶绿体", "质体", "mt污染", "pt污染"),
            preferred_files=("AlignmentQC.xls", "AlignmentQC.tsv", "AlignmentQC.csv", "AligentQC.xls", "AligentQC.tsv", "AligentQC.csv"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=("chrMT/Pt(%)", "chrMT/Pt", "chrMT/Pt%", "chrMT(%)", "chrM(%)", "chrPt(%)"),
            default_chart="bar",
            value_label="chrMT/Pt (%)",
            percent=True,
        ),
        MetricSpec(
            key="adapter",
            aliases=("adapter", "adapter rate", "接头", "接头污染"),
            preferred_files=("ReadsQC.xls", "ReadsQC.tsv", "ReadsQC.csv"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=("Adapter", "Adapter(%)", "Adapter%", "Adapter Rate", "adapter_rate"),
            default_chart="bar",
            value_label="Adapter (%)",
            percent=True,
        ),
        MetricSpec(
            key="frip",
            aliases=("frip", "frip score", "frip_score", "富集比例"),
            preferred_files=("FRiP.xls", "FRiP_score.xls", "FRiP.tsv", "FRiP.csv"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=("FRiP", "FRiP_score", "FRiP Score", "FRiP(%)", "frip_score"),
            default_chart="bar",
            value_label="FRiP (%)",
            percent=True,
        ),
        MetricSpec(
            key="peak",
            aliases=("peak", "peak number", "peak count", "peaks", "peak数量", "峰数量"),
            preferred_files=("Samples_peak_number_stat.xls", "Samples_peak_number_stat.tsv", "Samples_peak_number_stat.csv"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=("PeakNumber", "Peak Number", "Peak Count", "Peaks", "peak_num", "Total Peaks"),
            default_chart="bar",
            value_label="Peak Count",
            percent=False,
        ),
        MetricSpec(
            key="correlation",
            aliases=("correlation", "spearman", "相关性", "样本相关性"),
            preferred_files=("spearman_Corr_readCounts.tab", "spearman_Corr_readCounts.tsv", "correlation.tab"),
            sample_columns=("Sample", "SampleID", "Sample Name", "样本"),
            value_columns=(),
            default_chart="heatmap",
            value_label="Correlation",
            percent=False,
        ),
    )

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").strip().lower())

    @classmethod
    def _resolve_metric(cls, metric: str) -> MetricSpec:
        normalized = cls._normalize(metric)
        for spec in cls.METRICS:
            if normalized == cls._normalize(spec.key):
                return spec
            if any(normalized == cls._normalize(alias) for alias in spec.aliases):
                return spec
        for spec in cls.METRICS:
            if any(cls._normalize(alias) in normalized or normalized in cls._normalize(alias) for alias in spec.aliases):
                return spec
        supported = ", ".join(spec.key for spec in cls.METRICS)
        raise ValueError(f"暂不支持指标 {metric!r}，当前支持: {supported}")

    @classmethod
    def _resolve_chart_type(cls, chart_type: str | None, spec: MetricSpec) -> str:
        normalized = cls._normalize(chart_type or spec.default_chart)
        aliases = {
            "柱状图": "bar",
            "柱形图": "bar",
            "barplot": "bar",
            "折线图": "line",
            "linechart": "line",
            "热图": "heatmap",
            "heatmap": "heatmap",
        }
        resolved = aliases.get(normalized, normalized)
        if resolved not in cls.SUPPORTED_CHART_TYPES:
            raise ValueError(
                f"暂不支持图类型 {chart_type!r}，当前支持: {', '.join(sorted(cls.SUPPORTED_CHART_TYPES))}"
            )
        if spec.key == "correlation" and resolved != "heatmap":
            return "heatmap"
        return resolved

    @classmethod
    def _find_candidate_files(cls, root: Path, spec: MetricSpec) -> list[Path]:
        candidates: list[Path] = []
        seen: set[Path] = set()
        for filename in spec.preferred_files:
            for path in find_files(root, [filename], limit=5):
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    candidates.append(resolved)
        if candidates:
            return candidates

        aliases = spec.aliases + (spec.key,)
        for item in list_project_files(root, limit=800):
            if item.path.suffix.lower() not in {".xls", ".csv", ".tsv", ".tab"}:
                continue
            haystack = cls._normalize(str(item.path))
            if any(cls._normalize(alias) in haystack for alias in aliases):
                resolved = item.path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    candidates.append(resolved)
        return candidates

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        text = str(value).strip().replace("%", "").replace(",", "")
        if not text or text in {"-", "NA", "N/A", "nan"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @classmethod
    def _match_column(cls, columns: list[str], aliases: tuple[str, ...]) -> str | None:
        normalized_columns = {cls._normalize(column): column for column in columns}
        for alias in aliases:
            normalized_alias = cls._normalize(alias)
            if normalized_alias in normalized_columns:
                return normalized_columns[normalized_alias]
        for alias in aliases:
            normalized_alias = cls._normalize(alias)
            for normalized_column, column in normalized_columns.items():
                if normalized_alias and (normalized_alias in normalized_column or normalized_column in normalized_alias):
                    return column
        return None

    @classmethod
    def _load_metric_rows(
        cls,
        root: Path,
        spec: MetricSpec,
        samples: list[str] | None,
    ) -> tuple[list[str], list[float], Path, str, str]:
        sample_filter = {cls._normalize(sample) for sample in (samples or []) if sample}
        for path in cls._find_candidate_files(root, spec):
            rows = read_table_rows(path)
            if not rows:
                continue
            columns = list(rows[0].keys())
            sample_col = cls._match_column(columns, spec.sample_columns) or columns[0]
            value_col = cls._match_column(columns, spec.value_columns)
            if value_col is None and spec.key == "peak":
                numeric_counts: dict[str, int] = {}
                for column in columns:
                    if column == sample_col:
                        continue
                    numeric_counts[column] = sum(1 for row in rows if cls._to_float(row.get(column)) is not None)
                value_col = max(numeric_counts, key=numeric_counts.get) if numeric_counts else None
            if value_col is None:
                continue

            labels: list[str] = []
            values: list[float] = []
            for row in rows:
                sample = str(row.get(sample_col, "")).strip()
                if not sample:
                    continue
                if sample_filter and cls._normalize(sample) not in sample_filter:
                    continue
                value = cls._to_float(row.get(value_col))
                if value is None:
                    continue
                labels.append(sample)
                values.append(value)
            if values:
                if spec.percent and max(values) <= 1.5:
                    values = [value * 100 for value in values]
                return labels, values, path, sample_col, value_col
        raise ValueError(f"未在项目中找到可用于绘制 {spec.key} 的表格列")

    @classmethod
    def _load_alignment_summary(
        cls,
        root: Path,
        spec: MetricSpec,
        samples: list[str] | None,
    ) -> tuple[list[str], list[str], list[list[float]], Path, list[str]]:
        metric_columns = (
            ("Mapping", ("Mapping(%)", "Mapping", "Mapping%", "Mapped(%)", "mapping_rate")),
            ("Unique", ("Unique(%)", "Unique", "Unique%", "Unique Mapping(%)", "unique_rate")),
            ("Duplicate", ("Duplicate(%)", "Duplicate", "Duplicate%", "Dup(%)", "duplicate_rate")),
            ("chrMT/Pt", ("chrMT/Pt(%)", "chrMT/Pt", "chrMT/Pt%", "chrMT(%)", "chrM(%)", "chrPt(%)")),
        )
        sample_filter = {cls._normalize(sample) for sample in (samples or []) if sample}
        for path in cls._find_candidate_files(root, spec):
            rows = read_table_rows(path)
            if not rows:
                continue
            columns = list(rows[0].keys())
            sample_col = cls._match_column(columns, spec.sample_columns) or columns[0]
            selected_columns: list[tuple[str, str]] = []
            for metric_label, aliases in metric_columns:
                value_col = cls._match_column(columns, aliases)
                if value_col:
                    selected_columns.append((metric_label, value_col))
            if not selected_columns:
                continue

            labels: list[str] = []
            matrix: list[list[float]] = []
            for row in rows:
                sample = str(row.get(sample_col, "")).strip()
                if not sample:
                    continue
                if sample_filter and cls._normalize(sample) not in sample_filter:
                    continue
                values: list[float] = []
                for _, value_col in selected_columns:
                    value = cls._to_float(row.get(value_col))
                    if value is None:
                        break
                    values.append(value)
                if len(values) != len(selected_columns):
                    continue
                labels.append(sample)
                matrix.append(values)

            if matrix:
                flattened = [value for row_values in matrix for value in row_values]
                if flattened and max(flattened) <= 1.5:
                    matrix = [[value * 100 for value in row_values] for row_values in matrix]
                source_columns = [sample_col] + [value_col for _, value_col in selected_columns]
                return labels, [metric_label for metric_label, _ in selected_columns], matrix, path, source_columns
        raise ValueError("未在项目中找到可用于绘制 AlignmentQC 样本对比图的表格列")

    @classmethod
    def _load_correlation_matrix(cls, root: Path, spec: MetricSpec) -> tuple[list[str], list[list[float]], Path]:
        for path in cls._find_candidate_files(root, spec):
            text = path.read_text(encoding="utf-8", errors="ignore")
            lines = [line.rstrip("\n\r") for line in text.splitlines() if line.strip() and not line.startswith("#")]
            if len(lines) < 2:
                continue
            header = [part.strip().strip("'\"") for part in re.split(r"\t|,", lines[0])]
            sample_names = [part for part in header[1:] if part]
            matrix: list[list[float]] = []
            row_names: list[str] = []
            for line in lines[1:]:
                parts = [part.strip().strip("'\"") for part in re.split(r"\t|,", line)]
                if len(parts) < 2:
                    continue
                row_names.append(parts[0])
                values = [cls._to_float(part) for part in parts[1:1 + len(sample_names)]]
                if any(value is None for value in values):
                    continue
                matrix.append([float(value) for value in values if value is not None])
            labels = sample_names or row_names
            if labels and matrix:
                return labels, matrix, path
        raise ValueError("未在项目中找到可用于绘制相关性热图的矩阵文件")

    @staticmethod
    def _safe_filename(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_")[:80] or "chart"

    @classmethod
    def _output_path(cls, project_id: str, metric: str, chart_type: str) -> tuple[Path, str]:
        cls.CHART_ROOT.mkdir(parents=True, exist_ok=True)
        filename = f"{cls._safe_filename(project_id)}_{cls._safe_filename(metric)}_{chart_type}_{int(time())}_{uuid4().hex[:6]}.png"
        path = cls.CHART_ROOT / filename
        return path, f"{cls.CHART_URL_PREFIX}/{filename}"

    @staticmethod
    def _display_source_path(source_file: Path, root: Path) -> str:
        try:
            return str(source_file.resolve().relative_to(root.resolve()))
        except ValueError:
            return str(source_file)

    @staticmethod
    def _configure_plot() -> None:
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        plt.rcParams["figure.facecolor"] = "#FFFFFF"
        plt.rcParams["axes.facecolor"] = "#FFFFFF"
        plt.rcParams["axes.edgecolor"] = "#D6DEE8"
        plt.rcParams["axes.labelcolor"] = "#334155"
        plt.rcParams["xtick.color"] = "#475569"
        plt.rcParams["ytick.color"] = "#475569"
        plt.rcParams["text.color"] = "#111827"
        plt.rcParams["savefig.facecolor"] = "#FFFFFF"
        plt.rcParams["savefig.bbox"] = "tight"
        plt.rcParams["font.size"] = 9.5

    @staticmethod
    def _format_value(value: float) -> str:
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        if abs(value) >= 100:
            return f"{value:.0f}"
        if abs(value) >= 10:
            return f"{value:.1f}"
        return f"{value:.2f}"

    @staticmethod
    def _professional_colormap() -> LinearSegmentedColormap:
        return LinearSegmentedColormap.from_list(
            "nvz_correlation",
            ["#2F5597", "#8FB9DA", "#F7F9FC", "#F6B26B", "#B03A2E"],
            N=256,
        )

    @classmethod
    def _draw_series_chart(
        cls,
        labels: list[str],
        values: list[float],
        *,
        title: str,
        ylabel: str,
        chart_type: str,
        output_path: Path,
    ) -> None:
        cls._configure_plot()
        width = max(6.6, min(11.5, len(labels) * 0.42))
        fig, ax = plt.subplots(figsize=(width, 4.2))
        if chart_type == "line":
            x_values = list(range(len(labels)))
            ax.plot(
                x_values,
                values,
                marker="o",
                markersize=5.2,
                linewidth=2.1,
                color="#1F5E9C",
                markerfacecolor="white",
                markeredgewidth=1.8,
                markeredgecolor="#1F5E9C",
            )
            if values and min(values) >= 0:
                ax.fill_between(x_values, values, [0] * len(values), color="#1F5E9C", alpha=0.08)
            for index, value in enumerate(values):
                ax.annotate(
                    cls._format_value(value),
                    (index, value),
                    textcoords="offset points",
                    xytext=(0, 8),
                    ha="center",
                    fontsize=8,
                    color="#334155",
                )
            ax.set_xticks(x_values)
            ax.set_xticklabels(labels)
        else:
            colors = ["#1F5E9C" if index % 2 == 0 else "#4F8CC9" for index in range(len(values))]
            bars = ax.bar(labels, values, color=colors, width=0.58, edgecolor="white", linewidth=1.0)
            max_value = max(values) if values else 0
            offset = max(max_value * 0.014, 0.01)
            for bar, value in zip(bars, values):
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + offset,
                    cls._format_value(value),
                    ha="center",
                    va="bottom",
                    fontsize=8.5,
                    color="#334155",
                )
        ax.set_title(title, fontsize=12.5, fontweight="bold", color="#111827", pad=12)
        ax.set_xlabel("Sample", fontsize=9.5, labelpad=7)
        ax.set_ylabel(ylabel, fontsize=9.5, labelpad=7)
        ax.tick_params(axis="x", labelrotation=42)
        for tick in ax.get_xticklabels():
            tick.set_horizontalalignment("right")
        ax.grid(axis="y", linestyle="-", linewidth=0.6, color="#E6ECF2")
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#D6DEE8")
        ax.spines["bottom"].set_color("#D6DEE8")
        if values:
            ymin, ymax = min(values), max(values)
            if ymin >= 0:
                ax.set_ylim(0, ymax * 1.14 if ymax else 1)
            else:
                ax.set_ylim(ymin * 1.12, ymax * 1.12)
        fig.tight_layout(pad=1.0)
        fig.savefig(output_path, dpi=150)
        plt.close(fig)

    @classmethod
    def _draw_grouped_bar_chart(
        cls,
        labels: list[str],
        metric_labels: list[str],
        matrix: list[list[float]],
        *,
        title: str,
        ylabel: str,
        output_path: Path,
    ) -> None:
        cls._configure_plot()
        sample_count = len(labels)
        metric_count = len(metric_labels)
        width = max(7.2, min(12.0, sample_count * max(metric_count, 1) * 0.34))
        fig, ax = plt.subplots(figsize=(width, 4.8))
        x_values = list(range(sample_count))
        group_width = 0.72
        bar_width = min(0.18, group_width / max(metric_count, 1))
        colors = ["#1F5E9C", "#4F8CC9", "#D86F45", "#7C6AEB", "#2E8B57", "#B7791F"]

        max_value = max((value for row in matrix for value in row), default=0)
        label_offset = max(max_value * 0.012, 0.2)
        for metric_index, metric_label in enumerate(metric_labels):
            offset = (metric_index - (metric_count - 1) / 2) * bar_width
            positions = [x + offset for x in x_values]
            values = [row[metric_index] for row in matrix]
            bars = ax.bar(
                positions,
                values,
                width=bar_width * 0.92,
                label=metric_label,
                color=colors[metric_index % len(colors)],
                edgecolor="white",
                linewidth=0.8,
            )
            if sample_count <= 6 and metric_count <= 4:
                for bar, value in zip(bars, values):
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + label_offset,
                        cls._format_value(value),
                        ha="center",
                        va="bottom",
                        fontsize=7.5,
                        color="#334155",
                    )

        ax.set_title(title, fontsize=12.5, fontweight="bold", color="#111827", pad=12)
        ax.set_xlabel("Sample", fontsize=9.5, labelpad=7)
        ax.set_ylabel(ylabel, fontsize=9.5, labelpad=7)
        ax.set_xticks(x_values)
        ax.set_xticklabels(labels, rotation=20, ha="right")
        ax.grid(axis="y", linestyle="-", linewidth=0.6, color="#E6ECF2")
        ax.set_axisbelow(True)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=min(metric_count, 4), frameon=False)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#D6DEE8")
        ax.spines["bottom"].set_color("#D6DEE8")
        ax.set_ylim(0, max_value * 1.18 if max_value else 1)
        fig.tight_layout(pad=1.0)
        fig.savefig(output_path, dpi=150)
        plt.close(fig)

    @classmethod
    def _draw_heatmap(
        cls,
        labels: list[str],
        matrix: list[list[float]],
        *,
        title: str,
        output_path: Path,
    ) -> None:
        cls._configure_plot()
        label_count = max(len(labels), len(matrix))
        if label_count <= 3:
            size = 3.75
        elif label_count <= 8:
            size = max(4.7, label_count * 0.48)
        else:
            size = min(10.0, label_count * 0.40)
        fig, ax = plt.subplots(figsize=(size + 0.65, size))
        image = ax.imshow(matrix, cmap=cls._professional_colormap(), vmin=-1, vmax=1, aspect="equal")
        ax.set_xticks(range(len(labels)))
        ax.set_yticks(range(len(matrix)))
        ax.set_xticklabels(labels, rotation=35, ha="right")
        ax.set_yticklabels(labels[: len(matrix)])
        ax.set_title(title, fontsize=12.5, fontweight="bold", color="#111827", pad=12)
        ax.tick_params(axis="both", length=0)
        ax.set_xticks([x - 0.5 for x in range(1, len(labels))], minor=True)
        ax.set_yticks([y - 0.5 for y in range(1, len(matrix))], minor=True)
        ax.grid(which="minor", color="white", linestyle="-", linewidth=1.2)
        ax.tick_params(which="minor", bottom=False, left=False)
        for row_index, row in enumerate(matrix):
            for col_index, value in enumerate(row):
                if len(labels) > 18:
                    continue
                text_color = "white" if abs(value) > 0.72 else "#111827"
                ax.text(
                    col_index,
                    row_index,
                    f"{value:.2f}",
                    ha="center",
                    va="center",
                    fontsize=8.5 if label_count <= 4 else 7.5,
                    fontweight="bold" if abs(value) >= 0.9 else "normal",
                    color=text_color,
                )
        for spine in ax.spines.values():
            spine.set_visible(False)
        colorbar = fig.colorbar(image, ax=ax, fraction=0.042, pad=0.035, shrink=0.72)
        colorbar.outline.set_visible(False)
        colorbar.ax.tick_params(labelsize=8.5, colors="#475569")
        colorbar.set_label("Spearman correlation", fontsize=9.5, color="#334155")
        fig.tight_layout(pad=1.0)
        fig.savefig(output_path, dpi=150)
        plt.close(fig)

    @classmethod
    def generate_chart(
        cls,
        *,
        project_id: str,
        metric: str,
        chart_type: str | None = None,
        project_root: str | None = None,
        samples: list[str] | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        spec = cls._resolve_metric(metric)
        resolved_chart_type = cls._resolve_chart_type(chart_type, spec)
        root = resolve_project_root(project_id, project_root)
        output_path, image_url = cls._output_path(project_id, spec.key, resolved_chart_type)

        if spec.key == "alignment_summary":
            labels, metric_labels, matrix, source_file, source_columns = cls._load_alignment_summary(root, spec, samples)
            chart_title = title or f"{project_id} AlignmentQC Comparison"
            cls._draw_grouped_bar_chart(
                labels,
                metric_labels,
                matrix,
                title=chart_title,
                ylabel=spec.value_label,
                output_path=output_path,
            )
            data_points = len(labels) * len(metric_labels)
        elif resolved_chart_type == "heatmap":
            labels, matrix, source_file = cls._load_correlation_matrix(root, spec)
            chart_title = title or f"{project_id} {spec.value_label} Heatmap"
            cls._draw_heatmap(labels, matrix, title=chart_title, output_path=output_path)
            data_points = len(labels)
            source_columns = labels
        else:
            labels, values, source_file, sample_col, value_col = cls._load_metric_rows(root, spec, samples)
            chart_title = title or f"{project_id} {spec.key.upper()} by Sample"
            cls._draw_series_chart(
                labels,
                values,
                title=chart_title,
                ylabel=spec.value_label,
                chart_type=resolved_chart_type,
                output_path=output_path,
            )
            data_points = len(values)
            source_columns = [sample_col, value_col]

        logger.info(
            "project_chart generated project=%s metric=%s chart_type=%s source=%s output=%s",
            project_id,
            spec.key,
            resolved_chart_type,
            str(source_file),
            str(output_path),
        )
        return {
            "success": True,
            "project_id": project_id,
            "project_root": str(root),
            "metric": spec.key,
            "chart_type": resolved_chart_type,
            "title": title or "",
            "image_url": image_url,
            "image_path": str(output_path),
            "source_file": cls._display_source_path(source_file, root),
            "source_columns": source_columns,
            "data_points": data_points,
            "artifacts": [
                {
                    "type": "image",
                    "title": title or f"{spec.key.upper()} {resolved_chart_type}",
                    "url": image_url,
                }
            ],
            "summary": f"已生成 {project_id} 的 {spec.key} {resolved_chart_type} 图。",
        }


    # ── 新增：Plotly spec 生成（LLM 驱动，支持个性化） ──────────────────────────

    @classmethod
    async def generate_chart_spec(
        cls,
        *,
        project_id: str,
        metric: str,
        chart_type: str | None = None,
        project_root: str | None = None,
        samples: list[str] | None = None,
        user_request: str = "",
    ) -> dict[str, Any]:
        """
        提取项目数据后调用 LLM 生成 Plotly JSON spec，返回给前端直接渲染。

        Parameters
        ----------
        project_id    : 项目编号
        metric        : 指标名（同 generate_chart，支持中英文别名）
        chart_type    : 图类型提示（bar/line/heatmap），可为 None 由 LLM 决定
        project_root  : 项目根目录（可选，优先级高于自动定位）
        samples       : 过滤样本列表（可选）
        user_request  : 用户自然语言需求，如"加一条 0.1 阈值线，柱子用绿色"

        Returns
        -------
        dict  —  {
            "success": bool,
            "project_id": str,
            "metric": str,
            "plotly_spec": {"data": [...], "layout": {...}},
            "source_file": str,
            "data_points": int,
            "summary": str,
        }
        """
        spec = cls._resolve_metric(metric)
        resolved_chart_type = cls._resolve_chart_type(chart_type, spec)
        root = resolve_project_root(project_id, project_root)

        # ── 提取数据（同步文件 I/O，用 asyncio.to_thread 避免阻塞事件循环） ──
        def _extract_data() -> tuple[dict[str, Any], int, Path]:
            if spec.key == "alignment_summary":
                lbs, mlbs, mat, src, _ = cls._load_alignment_summary(root, spec, samples)
                idata: dict[str, Any] = {
                    "metric":          spec.key,
                    "chart_type_hint": "grouped_bar",
                    "project_id":      project_id,
                    "data": {
                        "labels":        lbs,
                        "values":        None,
                        "ylabel":        spec.value_label,
                        "metric_labels": mlbs,
                        "matrix":        mat,
                    },
                }
                return idata, len(lbs) * len(mlbs), src

            if resolved_chart_type == "heatmap":
                lbs, mat, src = cls._load_correlation_matrix(root, spec)
                idata = {
                    "metric":          spec.key,
                    "chart_type_hint": "heatmap",
                    "project_id":      project_id,
                    "data": {
                        "labels":        lbs,
                        "values":        None,
                        "ylabel":        spec.value_label,
                        "metric_labels": None,
                        "matrix":        mat,
                    },
                }
                return idata, len(lbs), src

            lbs, vals, src, _, _ = cls._load_metric_rows(root, spec, samples)
            idata = {
                "metric":          spec.key,
                "chart_type_hint": resolved_chart_type,
                "project_id":      project_id,
                "data": {
                    "labels":        lbs,
                    "values":        vals,
                    "ylabel":        spec.value_label,
                    "metric_labels": None,
                    "matrix":        None,
                },
            }
            return idata, len(vals), src

        input_data, data_points, source_file = await asyncio.to_thread(_extract_data)

        # ── 调用 LLM 生成 Plotly spec ────────────────────────────────────────
        plotly_spec = await chart_spec_service.generate(input_data, user_request)

        logger.info(
            "project_chart_spec generated project=%s metric=%s chart_type=%s source=%s",
            project_id, spec.key, resolved_chart_type, str(source_file),
        )

        return {
            "success":     True,
            "project_id":  project_id,
            "metric":      spec.key,
            "chart_type":  resolved_chart_type,
            "plotly_spec": plotly_spec,
            "source_file": cls._display_source_path(source_file, root),
            "data_points": data_points,
            "summary":     f"已生成 {project_id} 的 {spec.key} 交互图表。",
        }


project_chart_service = ProjectChartService()
