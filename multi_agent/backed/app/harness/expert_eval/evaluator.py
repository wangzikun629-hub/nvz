from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _numeric_values(value: Any) -> list[float]:
    if isinstance(value, dict):
        return [number for item in value.values() for number in _numeric_values(item)]
    if isinstance(value, list):
        return [number for item in value for number in _numeric_values(item)]
    if value is None or isinstance(value, bool):
        return []
    numbers: list[float] = []
    for match in re.findall(r"-?\d+(?:,\d{3})*(?:\.\d+)?", str(value)):
        try:
            numbers.append(float(match.replace(",", "")))
        except ValueError:
            continue
    return numbers


def _has_unqualified_threshold_judgement(answer: str) -> bool:
    judgements = ("偏高", "偏低", "异常", "超标", "不达标", "abnormally high", "abnormally low")
    negations = ("不能", "无法", "不得", "不支持", "未验证", "尚未", "cannot", "not verified")
    return any(
        any(term in segment.lower() for term in judgements)
        and not any(term in segment.lower() for term in negations)
        for segment in re.split(r"[\n。！？;；]", answer)
    )


def _answer_numeric_values(answer: str) -> list[float]:
    without_ranks = re.sub(r"#\d+\b", "", answer)
    without_ordinals = re.sub(
        r"(?m)^\s*\d+[.)、]\s*",
        "",
        without_ranks,
    )
    return _numeric_values(without_ordinals)


def evaluate_response(case: dict[str, Any], answer: str) -> dict[str, Any]:
    rubric = case.get("rubric") or {}
    checks: list[dict[str, Any]] = []
    for index, alternatives in enumerate(rubric.get("must_contain_any", []) or [], start=1):
        alternatives = [str(item) for item in alternatives]
        matched = [item for item in alternatives if item.lower() in answer.lower()]
        checks.append(
            {
                "name": f"required_point_{index}",
                "passed": bool(matched),
                "detail": matched or alternatives,
            }
        )
    for blocked in rubric.get("must_not_contain", []) or []:
        found = str(blocked).lower() in answer.lower()
        checks.append(
            {
                "name": f"forbidden:{blocked}",
                "passed": not found,
                "detail": "not found" if not found else "found",
            }
        )
    max_chars = int(rubric.get("max_chars") or 0)
    if max_chars:
        checks.append(
            {
                "name": "conciseness",
                "passed": len(answer) <= max_chars,
                "detail": f"chars={len(answer)} max={max_chars}",
            }
        )
    if rubric.get("require_cause_labels"):
        label_hits = [
            label
            for label in ("已证实", "推测", "证据不足")
            if label in answer
        ]
        checks.append(
            {
                "name": "cause_support_labels",
                "passed": "推测" in label_hits and "证据不足" in label_hits,
                "detail": label_hits,
            }
        )
    context = case.get("analysis_context") or {}
    if rubric.get("numbers_must_trace", True) and context:
        allowed_numbers = _numeric_values(context)
        answer_numbers = _answer_numeric_values(answer)
        unsupported = [
            number
            for number in answer_numbers
            if not any(
                abs(number - allowed) <= max(1e-6, abs(allowed) * 1e-4)
                for allowed in allowed_numbers
            )
        ]
        checks.append(
            {
                "name": "numeric_traceability",
                "passed": not unsupported,
                "detail": {"unsupported": unsupported[:10], "allowed": allowed_numbers[:30]},
            }
        )
    if context.get("threshold_verified") is False:
        unqualified = _has_unqualified_threshold_judgement(answer)
        checks.append(
            {
                "name": "unverified_threshold_boundary",
                "passed": not unqualified,
                "detail": "unqualified judgement found" if unqualified else "boundary preserved",
            }
        )
    passed_count = sum(1 for check in checks if check["passed"])
    return {
        "case_id": case.get("id"),
        "category": case.get("category"),
        "passed": all(check["passed"] for check in checks),
        "score": round(passed_count / max(1, len(checks)) * 100, 2),
        "checks": checks,
    }


def build_prompt(case: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "你是生物信息项目分析智能体。只依据给定项目证据回答，所有数字必须说明来源和分母；"
        "未验证项目阈值时不得给出确定的偏高/偏低结论；每个原因必须标注已证实、推测或证据不足。"
        "回答要直接、精炼、可执行。"
    )
    user = (
        f"问题：{case.get('question', '')}\n"
        f"项目证据：{json.dumps(case.get('analysis_context') or {}, ensure_ascii=False)}\n"
        "请按“直接结论、关键证据、原因排序、验证动作、证据边界”组织回答。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
