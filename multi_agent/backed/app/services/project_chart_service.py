from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from multi_agent.backed.app.infrastructure.tools.local.project_reader import (
    find_files,
    list_project_files,
    read_table_rows,
    resolve_project_root,
)
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.services.r_chart_service import r_chart_service
from multi_agent.backed.app.services.r_codegen_service import r_codegen_service


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
    SPEC_ROOT = Path(__file__).resolve().parents[1] / "generated" / "chart_specs"
    SUPPORTED_CHART_TYPES = {"bar", "line", "heatmap", "scatter"}
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
        return re.sub(r"[^a-z0-9一-鿿]+", "", (value or "").strip().lower())

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
            skipped = 0
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
                    skipped += 1
                    continue
                labels.append(sample)
                matrix.append(values)

            if skipped:
                logger.warning("alignment_summary: skipped %d rows with missing values", skipped)

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
            # Validate square matrix
            n = len(labels)
            matrix = [row for row in matrix if len(row) == n]
            if labels and matrix:
                return labels, matrix, path
        raise ValueError("未在项目中找到可用于绘制相关性热图的矩阵文件")

    @staticmethod
    def _display_source_path(source_file: Path, root: Path) -> str:
        try:
            return str(source_file.resolve().relative_to(root.resolve()))
        except ValueError:
            return str(source_file)

    # ── Spec 持久化 ────────────────────────────────────────────────────────────

    @classmethod
    def _save_spec(cls, chart_id: str, spec: dict[str, Any]) -> None:
        """将 Plotly spec 以 JSON 文件形式持久化，供页面重载后重新拉取。"""
        cls.SPEC_ROOT.mkdir(parents=True, exist_ok=True)
        path = cls.SPEC_ROOT / f"{chart_id}.json"
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def get_chart_spec(cls, chart_id: str) -> dict[str, Any] | None:
        """根据 chart_id 读取持久化的 Plotly spec；不存在时返回 None。"""
        # 限制 chart_id 字符，防止路径穿越
        if not re.fullmatch(r"[a-zA-Z0-9_\-]+", chart_id):
            return None
        path = cls.SPEC_ROOT / f"{chart_id}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ── 主入口：Plotly spec 生成 ───────────────────────────────────────────────

    @classmethod
    async def generate_chart_spec(
        cls,
        *,
        project_id: str,
        metric: str,
        metric2: str | None = None,
        chart_type: str | None = None,
        project_root: str | None = None,
        samples: list[str] | None = None,
        user_request: str = "",
        use_codegen: bool = True,   # 始终 True，保留参数仅供兼容
    ) -> dict[str, Any]:
        """
        提取项目 QC 数据 → LLM 生成 ggplot2 R 脚本 → 执行 → 返回 PNG 图片 URL。

        Returns
        -------
        dict  —  {
            "success": bool,
            "chart_id": str,
            "project_id": str,
            "metric": str,
            "chart_type": "codegen_png",
            "image_url": str,          # /generated/charts/{chart_id}.png
            "source_file": str,
            "data_points": int,
            "summary": str,
        }
        """
        spec = cls._resolve_metric(metric)
        root = resolve_project_root(project_id, project_root)

        # ── 双指标对比路径 ─────────────────────────────────────────────────────
        if metric2:
            spec2 = cls._resolve_metric(metric2)
            _ct = cls._normalize(chart_type or "scatter")
            dual_chart_type = "grouped_bar" if _ct in ("bar", "grouped_bar") else "scatter"

            def _extract_dual() -> tuple[list, list | None, list | None, list | None, list[list] | None, int, Path]:
                lbs1, vals1, src1, _, _ = cls._load_metric_rows(root, spec,  samples)
                lbs2, vals2, _,    _, _ = cls._load_metric_rows(root, spec2, samples)
                common = {cls._normalize(s): s for s in lbs1}
                labels_out, x_vals, y_vals = [], [], []
                for i, s in enumerate(lbs2):
                    key = cls._normalize(s)
                    if key in common:
                        idx = next(j for j, ls in enumerate(lbs1) if cls._normalize(ls) == key)
                        labels_out.append(lbs1[idx])
                        x_vals.append(vals1[idx])
                        y_vals.append(vals2[i])
                if not labels_out:
                    raise ValueError(
                        f"指标 {spec.key} 与 {spec2.key} 无公共样本，无法生成对比图"
                    )
                if dual_chart_type == "scatter":
                    return labels_out, None, x_vals, y_vals, None, len(labels_out), src1
                else:
                    matrix = [[x, y] for x, y in zip(x_vals, y_vals)]
                    return labels_out, None, None, None, matrix, len(labels_out), src1

            labels, vals, x_vals, y_vals, matrix, data_points, source_file = \
                await asyncio.to_thread(_extract_dual)

            metric_key = f"{spec.key}_vs_{spec2.key}"
            codegen_kwargs: dict[str, Any] = dict(
                project_id=project_id,
                metric=metric_key,
                ylabel=spec2.value_label,
                user_request=user_request or f"双指标对比：{spec.value_label} vs {spec2.value_label}",
                labels=labels,
                values=vals,
                x_values=x_vals,
                y_values=y_vals,
                xlabel=spec.value_label,
                metric_labels=[spec.value_label, spec2.value_label] if dual_chart_type == "grouped_bar" else None,
                matrix=matrix,
            )
            codegen_result = await r_codegen_service.generate(**codegen_kwargs)
            image_url = codegen_result["image_url"]
            chart_id  = codegen_result["chart_id"]
            chart_block = f"```image\n{image_url}\n```"
            summary = (
                f"已生成 {project_id} 的 {spec.key} vs {spec2.key} 双指标对比图"
                f"（共 {data_points} 个公共样本）。\n\n{chart_block}"
            )
            return {
                "success":     True,
                "chart_id":    chart_id,
                "project_id":  project_id,
                "metric":      metric_key,
                "chart_type":  "codegen_png",
                "image_url":   image_url,
                "r_script":    codegen_result.get("r_script", ""),
                "source_file": cls._display_source_path(source_file, root),
                "data_points": data_points,
                "summary":     summary,
            }

        # ── 单指标路径 ────────────────────────────────────────────────────────
        resolved_chart_type = cls._resolve_chart_type(chart_type, spec)

        def _extract_data() -> tuple[list, list | None, list | None, list[list] | None, int, Path]:
            if spec.key == "alignment_summary":
                lbs, mlbs, mat, src, _ = cls._load_alignment_summary(root, spec, samples)
                return lbs, None, mlbs, mat, len(lbs) * len(mlbs), src

            if resolved_chart_type == "heatmap":
                lbs, mat, src = cls._load_correlation_matrix(root, spec)
                return lbs, None, None, mat, len(lbs), src

            lbs, vls, src, _, _ = cls._load_metric_rows(root, spec, samples)
            return lbs, vls, None, None, len(vls), src

        labels, values, metric_labels, matrix, data_points, source_file = \
            await asyncio.to_thread(_extract_data)

        logger.info(
            "r_codegen path project=%s metric=%s chart_type=%s points=%d",
            project_id, spec.key, resolved_chart_type, data_points,
        )

        codegen_result = await r_codegen_service.generate(
            project_id=project_id,
            metric=spec.key,
            ylabel=spec.value_label,
            user_request=user_request or f"为 {spec.key} 指标画一个专业美观的图表",
            labels=labels,
            values=values,
            groups=None,
            metric_labels=metric_labels,
            matrix=matrix,
        )

        chart_id  = codegen_result["chart_id"]
        image_url = codegen_result["image_url"]
        chart_block = f"```image\n{image_url}\n```"
        summary = (
            f"已生成 {project_id} 的 {spec.key} 图表（数据点：{data_points}）。\n\n"
            f"{chart_block}"
        )
        logger.info("r_codegen done project=%s metric=%s chart_id=%s", project_id, spec.key, chart_id)

        return {
            "success":     True,
            "chart_id":    chart_id,
            "project_id":  project_id,
            "metric":      spec.key,
            "chart_type":  "codegen_png",
            "image_url":   image_url,
            "r_script":    codegen_result.get("r_script", ""),
            "source_file": cls._display_source_path(source_file, root),
            "data_points": data_points,
            "summary":     summary,
        }


project_chart_service = ProjectChartService()
