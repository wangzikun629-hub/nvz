from __future__ import annotations

import re
from statistics import mean
from typing import Any

from multi_agent.backed.app.services.project_analysis_service import ProjectAnalysisService
from multi_agent.backed.app.services.project_locator_service import project_locator_service


class ProjectComparisonService:
    PROJECT_ID_PATTERN = re.compile(r"\b(VZ[0-9A-Za-z._-]{4,})\b", flags=re.IGNORECASE)
    METRIC_LABELS = {
        "adapter_percent": "Adapter(%)",
        "q30_ratio": "Q30",
        "mapping_rate_percent": "Mapping(%)",
        "unique_mapping_rate_percent": "Unique(%)",
        "duplicate_rate_percent": "Duplicate(%)",
        "mt_rate_percent": "chrMT/Pt(%)",
        "frip_ratio": "FRiP",
    }
    LOWER_IS_BETTER = {"adapter_percent", "duplicate_rate_percent", "mt_rate_percent"}
    HIGHER_IS_BETTER = {"q30_ratio", "mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio"}

    @classmethod
    def _extract_project_ids(cls, question: str) -> list[str]:
        values: list[str] = []
        for match in cls.PROJECT_ID_PATTERN.findall(question or ""):
            normalized = match.strip()
            if normalized and normalized not in values:
                values.append(normalized)
        return values

    @classmethod
    def resolve_compare_project(
        cls,
        *,
        question: str,
        current_project_id: str,
        recent_projects: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        current_lower = (current_project_id or "").lower()
        explicit_ids = cls._extract_project_ids(question)
        for project_id in explicit_ids:
            if project_id.lower() == current_lower:
                continue
            resolved = project_locator_service.resolve_project_by_id(project_id)
            if resolved:
                return resolved

        normalized = question.lower()
        if any(term in normalized for term in ("上一个", "之前", "历史")):
            for item in recent_projects or []:
                project_id = str(item.get("project_id") or "").strip()
                if not project_id or project_id.lower() == current_lower:
                    continue
                project_root = str(item.get("project_root") or "").strip()
                if project_root:
                    return {
                        "project_id": project_id,
                        "project_root": project_root,
                        "sample_names": [],
                        "matched_by": "recent_projects",
                        "confidence": 0.8,
                    }

        candidates = [
            {
                "project_id": str(item.get("project_id") or ""),
                "project_root": str(item.get("project_root") or ""),
            }
            for item in recent_projects or []
            if str(item.get("project_id") or "").strip()
            and str(item.get("project_id") or "").strip().lower() != current_lower
        ]
        return {
            "needs_confirmation": True,
            "message": "No explicit comparison project was resolved.",
            "explicit_project_ids": explicit_ids,
            "current_project_id": current_project_id,
            "only_referenced_current_project": bool(explicit_ids)
            and all(project_id.lower() == current_lower for project_id in explicit_ids),
            "candidates": candidates[:5],
        }

    @staticmethod
    def _float_values(rows: list[dict[str, Any]], key: str) -> list[float]:
        values: list[float] = []
        for row in rows:
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                continue
            values.append(value)
        return values

    @classmethod
    def _extract_metric_summary(cls, analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
        parsed = analysis.get("parsed_metrics") or {}
        metric_rows = {
            "adapter_percent": parsed.get("qc", []) or [],
            "q30_ratio": parsed.get("qc", []) or [],
            "mapping_rate_percent": parsed.get("alignment", []) or [],
            "unique_mapping_rate_percent": parsed.get("alignment", []) or [],
            "duplicate_rate_percent": parsed.get("alignment", []) or [],
            "mt_rate_percent": parsed.get("alignment", []) or [],
            "frip_ratio": parsed.get("frip", []) or [],
        }
        summary: dict[str, dict[str, Any]] = {}
        for key, rows in metric_rows.items():
            if not isinstance(rows, list):
                continue
            values = cls._float_values(rows, key)
            if not values:
                continue
            summary[key] = {
                "label": cls.METRIC_LABELS.get(key, key),
                "mean": mean(values),
                "min": min(values),
                "max": max(values),
                "sample_count": len(values),
            }
        return summary

    @staticmethod
    def _format_value(value: float | None, metric_key: str) -> str:
        if value is None:
            return "-"
        if metric_key == "frip_ratio" or metric_key == "q30_ratio":
            return f"{value:.4f}"
        return f"{value:.2f}"

    @classmethod
    def _assessment(cls, metric_key: str, delta: float | None) -> str:
        if delta is None:
            return "数据不足"
        if abs(delta) < 1e-9:
            return "基本一致"
        if metric_key in cls.LOWER_IS_BETTER:
            return "当前项目风险更高" if delta > 0 else "当前项目风险更低"
        if metric_key in cls.HIGHER_IS_BETTER:
            return "当前项目更好" if delta > 0 else "当前项目更差"
        return "存在差异"

    @classmethod
    def _build_rows(
        cls,
        current_summary: dict[str, dict[str, Any]],
        compare_summary: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for metric_key, label in cls.METRIC_LABELS.items():
            current = current_summary.get(metric_key)
            compare = compare_summary.get(metric_key)
            current_mean = current.get("mean") if current else None
            compare_mean = compare.get("mean") if compare else None
            delta = None
            if current_mean is not None and compare_mean is not None:
                delta = float(current_mean) - float(compare_mean)
            rows.append(
                {
                    "metric_key": metric_key,
                    "metric": label,
                    "current_mean": current_mean,
                    "compare_mean": compare_mean,
                    "delta": delta,
                    "assessment": cls._assessment(metric_key, delta),
                    "current_sample_count": current.get("sample_count", 0) if current else 0,
                    "compare_sample_count": compare.get("sample_count", 0) if compare else 0,
                }
            )
        return rows

    @classmethod
    def build_answer(cls, result: dict[str, Any]) -> str:
        current = result.get("current_project") or {}
        compare = result.get("compare_project") or {}
        rows = result.get("comparison_rows") or []
        lines = [
            "## 跨项目对比结论",
            f"当前项目 `{current.get('project_id', '')}` 已与对照项目 `{compare.get('project_id', '')}` 完成核心指标对比。",
            "",
            "## 指标对比",
            "| 指标 | 当前项目均值 | 对照项目均值 | 差值 | 判断 |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for row in rows:
            metric_key = str(row.get("metric_key") or "")
            delta = row.get("delta")
            lines.append(
                "| "
                + f"{row.get('metric', '')} | "
                + f"{cls._format_value(row.get('current_mean'), metric_key)} | "
                + f"{cls._format_value(row.get('compare_mean'), metric_key)} | "
                + f"{cls._format_value(delta, metric_key) if delta is not None else '-'} | "
                + f"{row.get('assessment', '')} |"
            )
        highlights = result.get("highlights") or []
        if highlights:
            lines.extend(["", "## 关键差异"])
            lines.extend(f"- {item}" for item in highlights[:6])
        lines.extend(
            [
                "",
                "## 数据限制",
                "- 当前版本对比的是两个项目的项目级均值，不替代同批次、同样本设计下的统计检验。",
                "- 若两个项目的物种、流程版本、样本类型或测序批次不同，需要先确认这些元信息再解释生物学差异。",
            ]
        )
        return "\n".join(lines)

    @classmethod
    def _build_highlights(cls, rows: list[dict[str, Any]]) -> list[str]:
        highlights: list[str] = []
        ranked = sorted(
            [row for row in rows if row.get("delta") is not None],
            key=lambda row: abs(float(row.get("delta") or 0)),
            reverse=True,
        )
        for row in ranked[:4]:
            metric_key = str(row.get("metric_key") or "")
            highlights.append(
                f"{row.get('metric', '')}: 当前={cls._format_value(row.get('current_mean'), metric_key)}, "
                f"对照={cls._format_value(row.get('compare_mean'), metric_key)}, "
                f"差值={cls._format_value(row.get('delta'), metric_key)}（{row.get('assessment', '')}）"
            )
        return highlights

    @classmethod
    def compare(
        cls,
        *,
        question: str,
        current_project_id: str,
        current_project_root: str,
        compare_project_id: str,
        compare_project_root: str,
        max_evidence_files: int = 8,
    ) -> dict[str, Any]:
        current_analysis = ProjectAnalysisService.analyze(
            current_project_id,
            question,
            current_project_root,
            max_evidence_files=max_evidence_files,
            planning_hints={"force_include_html_body": True},
        )
        compare_analysis = ProjectAnalysisService.analyze(
            compare_project_id,
            question,
            compare_project_root,
            max_evidence_files=max_evidence_files,
            planning_hints={"force_include_html_body": True},
        )
        rows = cls._build_rows(
            cls._extract_metric_summary(current_analysis),
            cls._extract_metric_summary(compare_analysis),
        )
        payload = {
            "current_project": {
                "project_id": current_project_id,
                "project_root": current_project_root,
            },
            "compare_project": {
                "project_id": compare_project_id,
                "project_root": compare_project_root,
            },
            "comparison_rows": rows,
            "highlights": cls._build_highlights(rows),
            "current_analysis_run_id": current_analysis.get("run_id"),
            "compare_analysis_run_id": compare_analysis.get("run_id"),
            "current_analysis_status": (current_analysis.get("trace") or {}).get("status"),
            "compare_analysis_status": (compare_analysis.get("trace") or {}).get("status"),
            "current_warning_count": len(current_analysis.get("warnings", []) or []),
            "compare_warning_count": len(compare_analysis.get("warnings", []) or []),
        }
        payload["answer"] = cls.build_answer(payload)
        return payload


project_comparison_service = ProjectComparisonService()
