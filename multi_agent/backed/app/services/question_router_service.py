from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from multi_agent.backed.app.services.project_analysis_service import ProjectAnalysisService


@dataclass
class QuestionRoute:
    intent: str
    route: str
    requires_project: bool
    needs_chart: bool
    target_metrics: list[str]
    question_tags: list[str]
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuestionRouterService:
    METRIC_ALIASES = {
        "adapter_percent": ("adapter", "接头", "接头比例", "接头污染"),
        "mapping_rate_percent": ("mapping", "比对率", "比对"),
        "unique_mapping_rate_percent": ("unique", "唯一比对", "唯一比对率"),
        "duplicate_rate_percent": ("duplicate", "重复率", "去重"),
        "mt_rate_percent": ("chrmt", "chrmt/pt", "mt", "线粒体", "叶绿体"),
        "frip_ratio": ("frip", "reads in peaks", "富集比例"),
        "q30_ratio": ("q30", "q20", "测序质量"),
        "correlation": ("corr", "correlation", "相关性", "相关系数", "spearman"),
        "peak_count": ("peak", "峰", "峰数量"),
    }
    FORMULA_TERMS = ("怎么计算", "如何计算", "计算方式", "怎么算", "公式", "怎么来的", "口径")
    PIPELINE_PARAM_TERMS = (
        "阈值", "qvalue", "q-value", "q值", "callpeak", "call peak",
        "去重设置", "spikein设置", "spike-in设置", "参考基因组", "接头类型",
        "trimming", "macs", "bowtie", "比对参数", "峰值参数",
    )
    EXPLAIN_TERMS = ("是什么", "代表什么", "什么意思", "解释", "定义")
    # 强画图词：明确动作动词，直接触发（不受负面语境过滤影响）
    CHART_STRONG_TERMS = frozenset((
        "画", "画出", "画图", "画个图", "画一下", "画一个",
        "绘图", "绘制", "作图", "做个图", "出图", "出个图",
        "生成图", "可视化", "visualize",
    ))
    # 弱画图词：图表名词，需同时不含描述性语境才触发
    # 删除了 "热图"/"图表" ——在描述场景极易误触发，改为只作指标别名
    CHART_WEAK_TERMS = frozenset((
        "对比图", "比较图", "散点图", "柱状图", "折线图",
        "heatmap", "chart", "plot",
    ))
    CHART_TERMS = tuple(CHART_STRONG_TERMS | CHART_WEAK_TERMS)  # 向后兼容
    # 描述性语境词：出现时说明用户在描述/解读已有图，而非请求生图
    # "里" 单字：弱词与"里"同现时，通常是 "heatmap里..."/"图里..." 描述性表达
    CHART_NEGATIVE_CONTEXT = frozenset((
        "看到", "看见", "里面", "里", "图里", "图中", "图上",
        "观察到", "显示", "解读", "什么意思", "怎么看", "如何看",
        "说明什么", "表示什么", "代表什么",
    ))
    REPORT_TERMS = ("ai报告总结", "报告总结", "总结报告", "生成报告", "汇总报告")
    DIAGNOSTIC_TERMS = ("为什么", "原因", "异常", "偏高", "偏低", "不对", "排查", "诊断", "继续排查", "报错", "错误", "日志", "错误日志", "log文件")
    PRODUCT_TERMS = ("产品怎么用", "产品使用", "怎么使用", "功能介绍", "使用说明")
    PROJECT_TERMS = ("当前项目", "这个项目", "该项目", "本项目", "样本", "t1", "t2", "vz")
    CONTEXT_CLEAR_TERMS = ("清空项目", "取消绑定", "不看项目", "退出项目")
    PROJECT_COMPARE_TERMS = (
        "跨项目对比",
        "项目对比",
        "和之前项目比",
        "和上一个项目比",
        "和历史项目比",
        "与之前项目对比",
        "与上一个项目对比",
        "compare project",
    )

    def classify(self, question: str, *, active_project_id: str | None = None) -> QuestionRoute:
        normalized = " ".join((question or "").split()).strip().lower()
        question_tags = ProjectAnalysisService._infer_question_types(question)
        target_metrics = self._detect_metrics(normalized)
        has_active_project = bool(str(active_project_id or "").strip())
        mentions_project = has_active_project or any(token in normalized for token in self.PROJECT_TERMS)

        if not normalized:
            return QuestionRoute("empty", "clarification", False, False, [], ["overview"], 0.99, "empty_question")

        if any(token in normalized for token in self.CONTEXT_CLEAR_TERMS):
            return QuestionRoute(
                "project_context_control",
                "project_context",
                False,
                False,
                target_metrics,
                question_tags,
                0.95,
                "project_context_control_term",
            )

        explicit_project_ids = re.findall(r"\b(?:vz|VZ)[0-9A-Za-z._-]{4,}\b", question or "", flags=re.IGNORECASE)
        asks_project_compare = (
            any(token in normalized for token in self.PROJECT_COMPARE_TERMS)
            or (
                has_active_project
                and len({item.lower() for item in explicit_project_ids}) >= 1
                and any(token in normalized for token in ("对比", "比较", "比一下", "差异", "compare"))
            )
        )
        if asks_project_compare:
            return QuestionRoute(
                "project_comparison",
                "project_compare",
                True,
                False,
                target_metrics,
                question_tags,
                0.91,
                "project_compare_term",
            )

        if any(token in normalized for token in self.REPORT_TERMS):
            return QuestionRoute(
                "report_summary",
                "ai_report_summary",
                True,
                False,
                target_metrics,
                question_tags,
                0.92,
                "report_summary_term",
            )

        _has_strong_chart = any(token in normalized for token in self.CHART_STRONG_TERMS)
        _has_weak_chart   = any(token in normalized for token in self.CHART_WEAK_TERMS)
        _is_chart_request = _has_strong_chart or (
            _has_weak_chart
            and not any(neg in normalized for neg in self.CHART_NEGATIVE_CONTEXT)
        )
        if _is_chart_request:
            return QuestionRoute(
                "chart_request",
                "chart",
                True,
                True,
                target_metrics,
                question_tags,
                0.9,
                "chart_term",
            )

        asks_formula = any(token in normalized for token in self.FORMULA_TERMS)
        asks_explanation = any(token in normalized for token in self.EXPLAIN_TERMS)
        if target_metrics and asks_formula:
            return QuestionRoute(
                "metric_formula",
                "project_analysis" if mentions_project else "knowledge",
                mentions_project,
                False,
                target_metrics,
                question_tags,
                0.93,
                "metric_formula_term",
            )

        if target_metrics and asks_explanation and not mentions_project:
            return QuestionRoute(
                "metric_explanation",
                "knowledge",
                False,
                False,
                target_metrics,
                question_tags,
                0.86,
                "metric_explanation_without_project",
            )

        if any(token in normalized for token in self.PRODUCT_TERMS):
            return QuestionRoute(
                "product_usage",
                "product_help",
                False,
                False,
                target_metrics,
                question_tags,
                0.84,
                "product_usage_term",
            )

        if any(token in normalized for token in self.DIAGNOSTIC_TERMS):
            return QuestionRoute(
                "project_diagnostic" if mentions_project or target_metrics else "general_diagnostic",
                "project_analysis" if mentions_project or target_metrics else "knowledge",
                bool(mentions_project or target_metrics),
                False,
                target_metrics,
                question_tags,
                0.82,
                "diagnostic_term",
            )

        if has_active_project and normalized in {"继续", "继续分析", "继续排查", "接着看", "下一步"}:
            return QuestionRoute(
                "project_followup",
                "project_analysis",
                True,
                False,
                target_metrics,
                question_tags,
                0.88,
                "active_project_followup",
            )

        if mentions_project:
            return QuestionRoute(
                "project_analysis",
                "project_analysis",
                True,
                False,
                target_metrics,
                question_tags,
                0.76,
                "project_reference",
            )

        # Pipeline parameter questions (e.g. "callpeak q-value 阈值是多少") are
        # project-specific even when no project keyword appears in the text.
        # Route to project_analysis so config/workflow context is available.
        if any(token in normalized for token in self.PIPELINE_PARAM_TERMS):
            return QuestionRoute(
                "project_pipeline_config",
                "project_analysis",
                True,
                False,
                target_metrics,
                question_tags,
                0.78,
                "pipeline_param_term",
            )

        return QuestionRoute(
            "general_question",
            "knowledge",
            False,
            False,
            target_metrics,
            question_tags,
            0.68,
            "fallback_general",
        )

    @classmethod
    def _detect_metrics(cls, normalized_question: str) -> list[str]:
        metrics: list[str] = []
        for metric_key, aliases in cls.METRIC_ALIASES.items():
            if any(alias in normalized_question for alias in aliases):
                metrics.append(metric_key)
        return metrics


question_router_service = QuestionRouterService()
