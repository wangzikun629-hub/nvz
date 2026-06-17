from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class GuardViolation:
    rule: str
    message: str
    matched: str = ""
    severity: str = "minor"


class BusinessHarnessGuardService:
    """Runtime guardrail for project-analysis answers.

    The offline harness proves regressions in tests. This service applies the
    same high-risk rules before an answer is published to the UI.
    """

    BLOCKED_ANSWER_PHRASES = (
        ("no_project_delivery_judgement", "项目合格", "minor"),
        ("no_project_delivery_judgement", "项目不合格", "minor"),
        ("no_project_delivery_judgement", "适合继续下游分析", "minor"),
        ("no_project_delivery_judgement", "不适合继续下游分析", "minor"),
        ("no_project_delivery_judgement", "通过/失败", "minor"),
        ("no_project_delivery_judgement", "pass/fail", "minor"),
        ("no_project_delivery_judgement", "PASS", "minor"),
        ("no_project_delivery_judgement", "FAIL", "minor"),
        ("no_legacy_no_abnormal_judgement", "未发现明显异常", "minor"),
        ("no_legacy_no_abnormal_judgement", "未发现明确异常", "minor"),
        ("no_implicit_delivery_judgement", "整体质量没有问题", "minor"),
        ("no_implicit_delivery_judgement", "可以放心进入后续分析", "minor"),
        ("no_implicit_delivery_judgement", "可以放心", "minor"),
        ("no_implicit_delivery_judgement", "放心进入后续分析", "minor"),
        ("no_implicit_delivery_judgement", "可进入后续分析", "minor"),
        ("no_implicit_delivery_judgement", "项目状态良好", "minor"),
        ("no_implicit_delivery_judgement", "整体质量良好", "minor"),
    )
    LOCAL_REPLACEMENTS = {
        "## 结论": "## 问题相关发现",
        "# 结论": "# 问题相关发现",
        "项目合格": "本轮不做项目交付判定",
        "项目不合格": "本轮不做项目交付判定",
        "适合继续下游分析": "不做是否进入下游分析的交付判定",
        "不适合继续下游分析": "不做是否进入下游分析的交付判定",
        "通过/失败": "需结合项目背景复核",
        "pass/fail": "review-only",
        "PASS": "REVIEW",
        "FAIL": "REVIEW",
        "未发现明显异常": "本轮未识别到直接相关的需复核指标",
        "未发现明确异常": "本轮未识别到直接相关的需复核指标",
        "可以放心进入后续分析": "不做是否进入后续分析的交付判定",
        "放心进入后续分析": "不做是否进入后续分析的交付判定",
        "可进入后续分析": "不做是否进入后续分析的交付判定",
        "整体质量没有问题": "本轮只列出需复核指标和证据限制",
        "可以放心": "需要结合项目背景复核后再判断",
        "项目状态良好": "本轮只列出需复核指标和证据限制",
        "整体质量良好": "本轮只列出需复核指标和证据限制",
        "经验阈值": "通用参考阈值（非项目交付标准）",
    }
    CODE_MARKERS = (
        "```python",
        "import matplotlib",
        "matplotlib.pyplot",
        "import numpy",
        "plt.",
        "np.arange",
        "notebook",
        "绘图代码",
    )
    INTERNAL_MARKERS = (
        "\\Snakemake_Sop",
        "Y:\\",
        "D:\\nvz\\kefu",
        ".project_sftp_cache",
        "workflow_detected_parameters",
        "INTERNAL WORKFLOW EVIDENCE",
    )
    SEGMENT_BOUNDARY_PATTERN = re.compile(r"([\s\S]*?(?:\n\n|[。！？!?]))")

    @classmethod
    def validate(
        cls,
        *,
        answer: str,
        analysis_result: dict[str, Any],
        question_route: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        violations: list[GuardViolation] = []
        answer_text = str(answer or "")
        lowered = answer_text.lower()

        for rule, phrase, severity in cls.BLOCKED_ANSWER_PHRASES:
            if phrase in answer_text:
                violations.append(GuardViolation(rule, f"回答包含禁用表达：{phrase}", phrase, severity))

        for marker in cls.CODE_MARKERS:
            if marker.lower() in lowered:
                violations.append(GuardViolation("no_code_answer", f"回答包含代码/绘图脚本标记：{marker}", marker, "severe"))

        for marker in cls.INTERNAL_MARKERS:
            if marker in answer_text:
                violations.append(GuardViolation("no_internal_source_leak", f"回答泄露内部路径或工作流标记：{marker}", marker, "severe"))

        diagnosis_text = cls._diagnosis_text(analysis_result)
        for rule, phrase, severity in cls.BLOCKED_ANSWER_PHRASES:
            if phrase in diagnosis_text:
                violation_severity = "severe" if severity == "severe" else "moderate"
                violations.append(GuardViolation(rule, f"诊断摘要包含禁用表达：{phrase}", phrase, violation_severity))

        max_severity = cls._max_severity(violations)
        result = {
            "passed": not violations,
            "action": "pass" if not violations else "repair",
            "severity": max_severity,
            "violations": [asdict(item) for item in violations],
            "question_route": question_route or {},
        }
        return result

    @classmethod
    def enforce(
        cls,
        *,
        answer: str,
        analysis_result: dict[str, Any],
        question_route: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        guard = cls.validate(answer=answer, analysis_result=analysis_result, question_route=question_route)
        if guard["passed"]:
            return str(answer or "").strip(), guard
        if guard.get("severity") == "minor":
            sanitized = cls.sanitize_answer(str(answer or ""))
            sanitized_guard = cls.validate(answer=sanitized, analysis_result={}, question_route=question_route)
            guard["action"] = "sanitized" if sanitized_guard["passed"] else "repair"
            guard["repair_passed"] = sanitized_guard["passed"]
            if sanitized_guard["passed"]:
                return sanitized, guard
        repaired = cls.build_repair_answer(analysis_result)
        repaired_guard = cls.validate(answer=repaired, analysis_result={}, question_route=question_route)
        guard["action"] = "repaired" if repaired_guard["passed"] else "blocked"
        guard["repair_passed"] = repaired_guard["passed"]
        if repaired_guard["passed"]:
            return repaired, guard
        return cls._minimal_blocked_answer(), guard

    @classmethod
    def sanitize_answer(cls, answer: str) -> str:
        sanitized = str(answer or "")
        for blocked, replacement in cls.LOCAL_REPLACEMENTS.items():
            sanitized = sanitized.replace(blocked, replacement)
        return sanitized.strip()

    @classmethod
    def split_ready_segments(cls, buffer: str, *, force: bool = False) -> tuple[list[str], str]:
        text = str(buffer or "")
        if not text:
            return [], ""
        segments: list[str] = []
        cursor = 0
        for match in cls.SEGMENT_BOUNDARY_PATTERN.finditer(text):
            segment = match.group(1)
            if segment:
                segments.append(segment)
            cursor = match.end()
        remaining = text[cursor:]
        if force and remaining:
            segments.append(remaining)
            remaining = ""
        return segments, remaining

    @classmethod
    def enforce_segment(
        cls,
        *,
        segment: str,
        question_route: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        guard = cls.validate(answer=segment, analysis_result={}, question_route=question_route)
        if guard["passed"]:
            return str(segment or ""), guard
        if guard.get("severity") == "minor":
            sanitized = cls.sanitize_answer(str(segment or ""))
            sanitized_guard = cls.validate(answer=sanitized, analysis_result={}, question_route=question_route)
            guard["action"] = "sanitized" if sanitized_guard["passed"] else "blocked"
            guard["repair_passed"] = sanitized_guard["passed"]
            if sanitized_guard["passed"]:
                return sanitized, guard
        guard["action"] = "blocked"
        guard["repair_passed"] = False
        return "", guard

    @classmethod
    def build_repair_answer(cls, analysis_result: dict[str, Any]) -> str:
        diagnosis_summary = analysis_result.get("diagnosis_summary") or {}
        evidence_chain = analysis_result.get("evidence_chain") or []
        warnings = analysis_result.get("warnings") or []
        analysis_limits = analysis_result.get("analysis_limits") or []

        findings = cls._safe_lines(diagnosis_summary.get("conclusions"), limit=4)
        evidence = cls._safe_lines(diagnosis_summary.get("evidence"), limit=5)
        if not evidence:
            evidence = cls._evidence_lines(evidence_chain, limit=5)
        limits = cls._safe_lines(analysis_limits or warnings, limit=4, localize=True)

        lines = [
            "## 当前问题的处理方向",
            "本轮回答触发了项目分析运行时规则校验，已收敛为只列出与当前问题相关、需要结合生物背景复核的指标。",
            "",
            "## 需复核指标",
        ]
        lines.extend(f"- {item}" for item in findings) if findings else lines.append("- 本轮未识别到直接相关的需复核指标。")
        lines.extend(["", "## 支撑证据"])
        lines.extend(f"- {item}" for item in evidence) if evidence else lines.append("- 当前结构化证据不足，需要补充项目文件或报告字段后复核。")
        lines.extend(["", "## 证据限制"])
        if limits:
            lines.extend(f"- {item}" for item in limits)
        else:
            lines.append("- 生物背景、样本角色和项目文件阈值会影响指标解释，本轮不做交付判定。")
        return "\n".join(lines).strip()

    @staticmethod
    def _minimal_blocked_answer() -> str:
        return (
            "## 问题相关发现\n"
            "本轮回答触发了项目分析运行时规则校验，系统已阻止原回答输出。\n\n"
            "## 证据限制\n"
            "- 当前证据不足以生成合规回答，请缩小问题到具体指标、样本或报告模块后重试。"
        )

    @classmethod
    def _diagnosis_text(cls, analysis_result: dict[str, Any]) -> str:
        diagnosis_summary = analysis_result.get("diagnosis_summary") or {}
        if not isinstance(diagnosis_summary, dict):
            return ""
        parts: list[str] = []
        for key in ("conclusions", "evidence", "possible_causes", "next_actions"):
            value = diagnosis_summary.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value if item)
            elif value:
                parts.append(str(value))
        return "\n".join(parts)

    @classmethod
    def _safe_lines(cls, value: Any, *, limit: int, localize: bool = False) -> list[str]:
        if not isinstance(value, list):
            return []
        lines = []
        blocked = [phrase for _, phrase, _ in cls.BLOCKED_ANSWER_PHRASES]
        for item in value:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if not text:
                continue
            if any(phrase in text for phrase in blocked):
                continue
            if localize:
                text = cls._localized_limit_line(text)
            lines.append(text)
            if len(lines) >= limit:
                break
        return lines

    @staticmethod
    def _localized_limit_line(text: str) -> str:
        if not text:
            return ""
        threshold_match = re.match(
            r"Threshold not verified in project scripts/README/SOP/report notes:\s*"
            r"(?P<metric>.+?)\s+has observed value\s+(?P<value>.+?)\.\s*"
            r"No project-specific threshold was applied;.*",
            text,
            flags=re.IGNORECASE,
        )
        if threshold_match:
            metric = threshold_match.group("metric").strip()
            value = threshold_match.group("value").strip()
            return (
                f"项目脚本、README、SOP 或报告说明中未确认 {metric} 的项目专属阈值；"
                f"本轮仅记录观测值 {value}，不把未在项目文件中确认的参考值作为项目标准。"
            )
        replacements = {
            "Threshold not verified in project scripts/README/SOP/report notes": "项目脚本、README、SOP 或报告说明中未确认阈值",
            "No project-specific threshold was applied": "未应用项目专属阈值",
            "do not present professional/default thresholds as project standards until a project-specific threshold is confirmed": "在确认项目专属阈值前，不应把未在项目文件中确认的参考值作为项目标准",
            "has observed value": "观测值为",
        }
        localized = text
        for source, target in replacements.items():
            localized = localized.replace(source, target)
        return localized

    @staticmethod
    def _max_severity(violations: list[GuardViolation]) -> str:
        if not violations:
            return "none"
        rank = {"minor": 1, "moderate": 2, "severe": 3}
        return max((item.severity for item in violations), key=lambda value: rank.get(value, 0))

    @classmethod
    def _evidence_lines(cls, evidence_chain: Any, *, limit: int) -> list[str]:
        if not isinstance(evidence_chain, list):
            return []
        lines: list[str] = []
        for item in evidence_chain:
            if not isinstance(item, dict):
                continue
            metric = str(item.get("metric") or item.get("metric_key") or "").strip()
            sample = str(item.get("sample") or "-").strip()
            value = str(item.get("display_value") or "-").strip()
            source_field = str(item.get("source_field") or item.get("metric_key") or "-").strip()
            if not metric:
                continue
            lines.append(f"{sample} {metric}={value}；来源字段 {source_field}")
            if len(lines) >= limit:
                break
        return lines


business_harness_guard_service = BusinessHarnessGuardService()
