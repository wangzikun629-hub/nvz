from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class HarnessCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _contains_all(text: str, needles: list[str]) -> tuple[bool, str]:
    missing = [needle for needle in needles if needle not in text]
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, "matched all"


def _contains_all_tolerant(text: str, needles: list[Any]) -> tuple[bool, str]:
    """与 `_contains_all` 相同，但每个 needle 允许是"可接受取值列表"而不只是单个字符串。

    project_analysis_phase1.5_auto_promotion_revision.md §11 解决方法 2：
    `business_adapter_e2e` 之前把精确浮点百分比写死在断言里，指标计算口径/取整方式的正常
    版本漂移就会让这条断言持续失败，属于"既有失败"被当作噪音掩盖真实回归。这里改成容差
    断言：case json 里的每一项可以写成 `["38.24%", "38.2%", "38.23%"]` 这样的候选列表，
    命中任意一个即算通过，不再要求逐字节精确匹配同一个历史观测值。字符串 needle 仍按原样
    单值匹配，完全向后兼容。
    """
    missing: list[str] = []
    for needle in needles:
        if isinstance(needle, (list, tuple)):
            candidates = [str(item) for item in needle]
            if not any(candidate in text for candidate in candidates):
                missing.append(" | ".join(candidates))
        else:
            candidate = str(needle)
            if candidate not in text:
                missing.append(candidate)
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, "matched all (tolerant)"


def _contains_none(text: str, needles: list[str]) -> tuple[bool, str]:
    found = [needle for needle in needles if needle in text]
    if found:
        return False, "unexpected: " + ", ".join(found)
    return True, "matched none"


def _answer_text(result: dict[str, Any]) -> str:
    payload = result.get("result_payload") or {}
    parts = [
        result.get("answer"),
        payload.get("answer") if isinstance(payload, dict) else "",
        result.get("report"),
    ]
    return "\n".join(_as_text(part) for part in parts if part)


def _runtime_validation_checks(
    *,
    answer_quality: dict[str, Any],
    analysis_result: dict[str, Any],
) -> list[HarnessCheck]:
    checks: list[HarnessCheck] = []
    if answer_quality:
        passed = bool(answer_quality.get("passed"))
        checks.append(
            HarnessCheck(
                "runtime_answer_quality_passed",
                passed,
                f"passed={passed} score={answer_quality.get('score')!r}",
            )
        )

    claim_validation = analysis_result.get("claim_validation")
    if isinstance(claim_validation, dict) and claim_validation:
        passed = bool(claim_validation.get("passed"))
        checks.append(
            HarnessCheck(
                "runtime_claim_validation_passed",
                passed,
                "passed="
                f"{passed} invalid_claim_count={claim_validation.get('invalid_claim_count')!r}",
            )
        )
    return checks


def evaluate_project_analysis(result: dict[str, Any], expect: dict[str, Any]) -> list[HarnessCheck]:
    checks: list[HarnessCheck] = []

    if "question_type" in expect:
        actual = _as_text(result.get("question_type"))
        expected = _as_text(expect["question_type"])
        checks.append(
            HarnessCheck(
                "question_type",
                actual == expected,
                f"expected={expected!r} actual={actual!r}",
            )
        )

    if "trace_status" in expect:
        actual = _as_text((result.get("trace") or {}).get("status"))
        expected = _as_text(expect["trace_status"])
        checks.append(
            HarnessCheck(
                "trace_status",
                actual == expected,
                f"expected={expected!r} actual={actual!r}",
            )
        )

    if "min_evidence_files" in expect:
        actual_count = len(result.get("evidence_files") or [])
        expected_count = int(expect["min_evidence_files"])
        checks.append(
            HarnessCheck(
                "min_evidence_files",
                actual_count >= expected_count,
                f"expected>={expected_count} actual={actual_count}",
            )
        )

    workflow_files = [
        _as_text(item.get("file"))
        for item in ((result.get("project_context") or {}).get("workflow_summary") or {}).get("files", [])
        if isinstance(item, dict)
    ]
    for needle in expect.get("required_workflow_file_contains", []) or []:
        matched = any(needle in file for file in workflow_files)
        checks.append(
            HarnessCheck(
                f"workflow_file_contains:{needle}",
                matched,
                "matched" if matched else "not found in workflow files",
            )
        )

    workflow_rule_key = expect.get("required_workflow_rule")
    workflow_rule_source: dict[str, Any] = {}
    if workflow_rule_key:
        workflow_rule_sources = (result.get("project_context") or {}).get("workflow_rule_sources") or {}
        workflow_rule_source = workflow_rule_sources.get(workflow_rule_key) or {}
        checks.append(
            HarnessCheck(
                "required_workflow_rule",
                bool(workflow_rule_source),
                f"rule={workflow_rule_key}",
            )
        )

    if workflow_rule_source:
        if "workflow_rule_source_level" in expect:
            actual = _as_text(workflow_rule_source.get("source_level"))
            expected = _as_text(expect["workflow_rule_source_level"])
            checks.append(
                HarnessCheck(
                    "workflow_rule_source_level",
                    actual == expected,
                    f"expected={expected!r} actual={actual!r}",
                )
            )

        if "workflow_rule_source_type_contains" in expect:
            actual = _as_text(workflow_rule_source.get("source_type"))
            expected = _as_text(expect["workflow_rule_source_type_contains"])
            checks.append(
                HarnessCheck(
                    "workflow_rule_source_type_contains",
                    expected in actual,
                    f"expected contains={expected!r} actual={actual!r}",
                )
            )

        if "workflow_rule_value_contains" in expect:
            value = _as_text(workflow_rule_source.get("value"))
            passed, detail = _contains_all(value, list(expect["workflow_rule_value_contains"]))
            checks.append(HarnessCheck("workflow_rule_value_contains", passed, detail))

        if "workflow_rule_evidence_contains" in expect:
            evidence = _as_text(workflow_rule_source.get("evidence"))
            passed, detail = _contains_all(evidence, list(expect["workflow_rule_evidence_contains"]))
            checks.append(HarnessCheck("workflow_rule_evidence_contains", passed, detail))

    metric_key = expect.get("required_metric")
    metric_source: dict[str, Any] = {}
    if metric_key:
        metric_sources = (result.get("project_context") or {}).get("metric_rule_sources") or {}
        metric_source = metric_sources.get(metric_key) or {}
        checks.append(
            HarnessCheck(
                "required_metric",
                bool(metric_source),
                f"metric={metric_key}",
            )
        )

    if metric_source:
        if "metric_source_level" in expect:
            actual = _as_text(metric_source.get("source_level"))
            expected = _as_text(expect["metric_source_level"])
            checks.append(
                HarnessCheck(
                    "metric_source_level",
                    actual == expected,
                    f"expected={expected!r} actual={actual!r}",
                )
            )

        if "formula_source_contains" in expect:
            actual = _as_text(metric_source.get("formula_source"))
            expected = _as_text(expect["formula_source_contains"])
            checks.append(
                HarnessCheck(
                    "formula_source_contains",
                    expected in actual,
                    f"expected contains={expected!r} actual={actual!r}",
                )
            )

        if "formula_file_contains" in expect:
            actual = _as_text(metric_source.get("formula_source_file"))
            expected = _as_text(expect["formula_file_contains"])
            checks.append(
                HarnessCheck(
                    "formula_file_contains",
                    expected in actual,
                    f"expected contains={expected!r} actual={actual!r}",
                )
            )

        if "formula_contains" in expect:
            formula = _as_text(metric_source.get("formula"))
            passed, detail = _contains_all(formula, list(expect["formula_contains"]))
            checks.append(HarnessCheck("formula_contains", passed, detail))

        if "needs_verification" in expect:
            actual = bool(metric_source.get("needs_verification"))
            expected = bool(expect["needs_verification"])
            checks.append(
                HarnessCheck(
                    "needs_verification",
                    actual == expected,
                    f"expected={expected} actual={actual}",
                )
            )

    evidence_chain = result.get("evidence_chain") or []
    if "evidence_metric" in expect:
        expected_metric = _as_text(expect["evidence_metric"])
        matched = [
            item for item in evidence_chain
            if isinstance(item, dict) and _as_text(item.get("metric_key")) == expected_metric
        ]
        checks.append(
            HarnessCheck(
                "evidence_metric",
                bool(matched),
                f"metric={expected_metric} matches={len(matched)}",
            )
        )
        if matched and "evidence_threshold_source_contains" in expect:
            expected = _as_text(expect["evidence_threshold_source_contains"])
            actual_values = [_as_text(item.get("threshold_source")) for item in matched]
            checks.append(
                HarnessCheck(
                    "evidence_threshold_source_contains",
                    any(expected in value for value in actual_values),
                    f"expected contains={expected!r} actual={actual_values!r}",
                )
            )
        if matched and "evidence_grade_contains" in expect:
            expected = _as_text(expect["evidence_grade_contains"])
            actual_values = [_as_text(item.get("evidence_grade")) for item in matched]
            checks.append(
                HarnessCheck(
                    "evidence_grade_contains",
                    any(expected in value for value in actual_values),
                    f"expected contains={expected!r} actual={actual_values!r}",
                )
            )
        if matched and "evidence_rule_must_be_empty" in expect:
            actual_values = [_as_text(item.get("rule")) for item in matched]
            expected_empty = bool(expect["evidence_rule_must_be_empty"])
            passed = all(not value for value in actual_values) if expected_empty else any(actual_values)
            checks.append(
                HarnessCheck(
                    "evidence_rule_must_be_empty",
                    passed,
                    f"expected_empty={expected_empty} actual={actual_values!r}",
                )
            )
        if matched and "evidence_severity_must_not_be" in expect:
            blocked = [str(item) for item in expect["evidence_severity_must_not_be"]]
            actual_values = [_as_text(item.get("severity")) for item in matched]
            unexpected = [value for value in actual_values if value in blocked]
            checks.append(
                HarnessCheck(
                    "evidence_severity_must_not_be",
                    not unexpected,
                    f"blocked={blocked!r} actual={actual_values!r}",
                )
            )

    if "warnings_must_contain" in expect:
        warning_text = "\n".join(_as_text(item) for item in result.get("warnings", []) or [])
        passed, detail = _contains_all(warning_text, list(expect["warnings_must_contain"]))
        checks.append(HarnessCheck("warnings_must_contain", passed, detail))

    if "analysis_limits_must_contain" in expect:
        limit_text = "\n".join(_as_text(item) for item in result.get("analysis_limits", []) or [])
        passed, detail = _contains_all(limit_text, list(expect["analysis_limits_must_contain"]))
        checks.append(HarnessCheck("analysis_limits_must_contain", passed, detail))

    diagnosis_summary = result.get("diagnosis_summary") or {}
    diagnosis_text = ""
    if isinstance(diagnosis_summary, dict):
        diagnosis_parts: list[str] = []
        for key in ("conclusions", "evidence", "possible_causes", "next_actions"):
            value = diagnosis_summary.get(key)
            if isinstance(value, list):
                diagnosis_parts.extend(_as_text(item) for item in value)
            else:
                diagnosis_parts.append(_as_text(value))
        diagnosis_text = "\n".join(part for part in diagnosis_parts if part)
    if "diagnosis_must_not_contain" in expect:
        passed, detail = _contains_none(diagnosis_text, list(expect["diagnosis_must_not_contain"]))
        checks.append(HarnessCheck("diagnosis_must_not_contain", passed, detail))

    cause_graph = result.get("cause_graph") or {}
    ranked_causes = cause_graph.get("ranked_causes", []) if isinstance(cause_graph, dict) else []
    ranked_causes = [item for item in ranked_causes if isinstance(item, dict)]
    if "cause_graph_version" in expect:
        actual = _as_text(cause_graph.get("version") if isinstance(cause_graph, dict) else "")
        expected = _as_text(expect["cause_graph_version"])
        checks.append(
            HarnessCheck(
                "cause_graph_version",
                actual == expected,
                f"expected={expected!r} actual={actual!r}",
            )
        )
    if "min_ranked_causes" in expect:
        expected_count = int(expect["min_ranked_causes"])
        checks.append(
            HarnessCheck(
                "min_ranked_causes",
                len(ranked_causes) >= expected_count,
                f"expected>={expected_count} actual={len(ranked_causes)}",
            )
        )
    required_cause = _as_text(expect.get("required_ranked_cause"))
    matched_cause = next(
        (
            item for item in ranked_causes
            if _as_text(item.get("cause_id") or item.get("cause")) == required_cause
        ),
        None,
    ) if required_cause else (ranked_causes[0] if ranked_causes else None)
    if required_cause:
        checks.append(
            HarnessCheck(
                "required_ranked_cause",
                matched_cause is not None,
                f"cause={required_cause!r}",
            )
        )
    if "ranked_cause_min_supporting_evidence" in expect:
        expected_count = int(expect["ranked_cause_min_supporting_evidence"])
        actual_count = len((matched_cause or {}).get("supporting_evidence") or [])
        checks.append(
            HarnessCheck(
                "ranked_cause_min_supporting_evidence",
                actual_count >= expected_count,
                f"expected>={expected_count} actual={actual_count}",
            )
        )
    if "ranked_cause_requires_verification_action" in expect:
        expected = bool(expect["ranked_cause_requires_verification_action"])
        actual = bool((matched_cause or {}).get("verification_actions"))
        checks.append(
            HarnessCheck(
                "ranked_cause_requires_verification_action",
                actual == expected,
                f"expected={expected} actual={actual}",
            )
        )

    evidence_cards = [
        item for item in (result.get("evidence_cards") or []) if isinstance(item, dict)
    ]
    if "min_evidence_cards" in expect:
        expected_count = int(expect["min_evidence_cards"])
        checks.append(
            HarnessCheck(
                "min_evidence_cards",
                len(evidence_cards) >= expected_count,
                f"expected>={expected_count} actual={len(evidence_cards)}",
            )
        )
    if "required_evidence_card_metric" in expect:
        metric = _as_text(expect["required_evidence_card_metric"])
        matched_cards = [item for item in evidence_cards if _as_text(item.get("metric_id")) == metric]
        checks.append(
            HarnessCheck(
                "required_evidence_card_metric",
                bool(matched_cards),
                f"metric={metric!r} matches={len(matched_cards)}",
            )
        )
        if matched_cards and expect.get("evidence_card_requires_denominator"):
            checks.append(
                HarnessCheck(
                    "evidence_card_requires_denominator",
                    all(item.get("denominator") not in (None, "") for item in matched_cards),
                    f"denominators={[item.get('denominator') for item in matched_cards]!r}",
                )
            )
    if "required_processing_phase" in expect:
        phase = _as_text(expect["required_processing_phase"])
        actual_phases = [_as_text(item.get("processing_phase")) for item in evidence_cards]
        checks.append(
            HarnessCheck(
                "required_processing_phase",
                phase in actual_phases,
                f"expected={phase!r} actual={actual_phases!r}",
            )
        )

    agent_loop = result.get("agent_loop") or {}
    if "agent_loop_min_rounds" in expect:
        expected_rounds = int(expect["agent_loop_min_rounds"])
        actual_rounds = int(agent_loop.get("round_count") or 0)
        checks.append(
            HarnessCheck(
                "agent_loop_min_rounds",
                actual_rounds >= expected_rounds,
                f"expected>={expected_rounds} actual={actual_rounds}",
            )
        )
    if "agent_loop_max_rounds" in expect:
        expected_rounds = int(expect["agent_loop_max_rounds"])
        actual_rounds = int(agent_loop.get("round_count") or 0)
        checks.append(
            HarnessCheck(
                "agent_loop_max_rounds",
                actual_rounds <= expected_rounds,
                f"expected<={expected_rounds} actual={actual_rounds}",
            )
        )

    claim_validation = result.get("claim_validation") or {}
    if "claim_validation_passed" in expect:
        expected = bool(expect["claim_validation_passed"])
        actual = bool(claim_validation.get("passed"))
        checks.append(
            HarnessCheck(
                "claim_validation_passed",
                actual == expected,
                f"expected={expected} actual={actual}",
            )
        )
    if "min_validated_claims" in expect:
        expected_count = int(expect["min_validated_claims"])
        actual_count = len(result.get("validated_claims") or [])
        checks.append(
            HarnessCheck(
                "min_validated_claims",
                actual_count >= expected_count,
                f"expected>={expected_count} actual={actual_count}",
            )
        )

    answer = _answer_text(result)
    if "answer_must_contain" in expect:
        passed, detail = _contains_all(answer, list(expect["answer_must_contain"]))
        checks.append(HarnessCheck("answer_must_contain", passed, detail))
    if "answer_must_not_contain" in expect:
        passed, detail = _contains_none(answer, list(expect["answer_must_not_contain"]))
        checks.append(HarnessCheck("answer_must_not_contain", passed, detail))

    return checks


def evaluate_business_agent(result: dict[str, Any], expect: dict[str, Any]) -> list[HarnessCheck]:
    checks: list[HarnessCheck] = []

    if "success" in expect:
        actual = bool(result.get("success"))
        expected = bool(expect["success"])
        checks.append(
            HarnessCheck(
                "success",
                actual == expected,
                f"expected={expected} actual={actual}",
            )
        )

    workflow_trace = result.get("workflow_trace") or {}
    if "workflow_status_in" in expect:
        actual = _as_text(workflow_trace.get("status"))
        allowed = [str(item) for item in expect["workflow_status_in"]]
        checks.append(
            HarnessCheck(
                "workflow_status_in",
                actual in allowed,
                f"allowed={allowed!r} actual={actual!r}",
            )
        )
    if "workflow_route" in expect:
        actual = _as_text(workflow_trace.get("route"))
        expected = _as_text(expect["workflow_route"])
        checks.append(
            HarnessCheck(
                "workflow_route",
                actual == expected,
                f"expected={expected!r} actual={actual!r}",
            )
        )
    if expect.get("analysis_run_absent"):
        actual = workflow_trace.get("analysis_run_id")
        checks.append(
            HarnessCheck(
                "analysis_run_absent",
                actual in (None, ""),
                f"analysis_run_id={actual!r}",
            )
        )

    result_payload = result.get("result_payload") or {}
    if "output_mode" in expect:
        actual = _as_text(result_payload.get("output_mode") if isinstance(result_payload, dict) else "")
        expected = _as_text(expect["output_mode"])
        checks.append(
            HarnessCheck(
                "output_mode",
                actual == expected,
                f"expected={expected!r} actual={actual!r}",
            )
        )
    if "generation_mode_in" in expect:
        actual = _as_text(result_payload.get("generation_mode") if isinstance(result_payload, dict) else "")
        allowed = [str(item) for item in expect["generation_mode_in"]]
        checks.append(
            HarnessCheck(
                "generation_mode_in",
                actual in allowed,
                f"allowed={allowed!r} actual={actual!r}",
            )
        )
    harness_guard = result_payload.get("harness_guard") if isinstance(result_payload, dict) else {}
    if not isinstance(harness_guard, dict):
        harness_guard = {}
    if "harness_guard_passed" in expect:
        actual = bool(harness_guard.get("passed"))
        expected = bool(expect["harness_guard_passed"])
        checks.append(
            HarnessCheck(
                "harness_guard_passed",
                actual == expected,
                f"expected={expected} actual={actual}",
            )
        )
    if "harness_guard_action" in expect:
        actual = _as_text(harness_guard.get("action"))
        expected = _as_text(expect["harness_guard_action"])
        checks.append(
            HarnessCheck(
                "harness_guard_action",
                actual == expected,
                f"expected={expected!r} actual={actual!r}",
            )
        )
    if "harness_guard_action_in" in expect:
        actual = _as_text(harness_guard.get("action"))
        allowed = [str(item) for item in expect["harness_guard_action_in"]]
        checks.append(
            HarnessCheck(
                "harness_guard_action_in",
                actual in allowed,
                f"allowed={allowed!r} actual={actual!r}",
            )
        )
    answer_quality = result_payload.get("answer_quality") if isinstance(result_payload, dict) else {}
    if not isinstance(answer_quality, dict):
        answer_quality = {}
    if "answer_quality_passed" in expect:
        actual = bool(answer_quality.get("passed"))
        expected = bool(expect["answer_quality_passed"])
        checks.append(
            HarnessCheck(
                "answer_quality_passed",
                actual == expected,
                f"expected={expected} actual={actual}",
            )
        )
    if "answer_quality_min_score" in expect:
        actual = int(answer_quality.get("score") or 0)
        expected = int(expect["answer_quality_min_score"])
        checks.append(
            HarnessCheck(
                "answer_quality_min_score",
                actual >= expected,
                f"expected>={expected} actual={actual}",
            )
        )
    if "answer_quality_repair_applied" in expect:
        actual = bool(answer_quality.get("repair_applied"))
        expected = bool(expect["answer_quality_repair_applied"])
        checks.append(
            HarnessCheck(
                "answer_quality_repair_applied",
                actual == expected,
                f"expected={expected} actual={actual}",
            )
        )
    if "chart_metric" in expect:
        chart = result_payload.get("chart") if isinstance(result_payload, dict) else {}
        actual = _as_text((chart or {}).get("metric") if isinstance(chart, dict) else "")
        expected = _as_text(expect["chart_metric"])
        checks.append(
            HarnessCheck(
                "chart_metric",
                actual == expected,
                f"expected={expected!r} actual={actual!r}",
            )
        )
    if "chart_type" in expect:
        chart = result_payload.get("chart") if isinstance(result_payload, dict) else {}
        actual = _as_text((chart or {}).get("chart_type") if isinstance(chart, dict) else "")
        expected = _as_text(expect["chart_type"])
        checks.append(
            HarnessCheck(
                "chart_type",
                actual == expected,
                f"expected={expected!r} actual={actual!r}",
            )
        )
    if expect.get("chart_image_required"):
        chart = result_payload.get("chart") if isinstance(result_payload, dict) else {}
        image_url = _as_text((chart or {}).get("image_url") if isinstance(chart, dict) else "")
        checks.append(
            HarnessCheck(
                "chart_image_required",
                bool(image_url),
                f"image_url={image_url!r}",
            )
        )

    comparison = result_payload.get("comparison") if isinstance(result_payload, dict) else {}
    if "comparison_project_ids" in expect:
        expected_ids = [str(item) for item in expect["comparison_project_ids"]]
        actual_ids: list[str] = []
        if isinstance(comparison, dict):
            current_project = comparison.get("current_project") or {}
            compare_project = comparison.get("compare_project") or {}
            if isinstance(current_project, dict):
                actual_ids.append(_as_text(current_project.get("project_id")))
            if isinstance(compare_project, dict):
                actual_ids.append(_as_text(compare_project.get("project_id")))
        missing = [item for item in expected_ids if item not in actual_ids]
        checks.append(
            HarnessCheck(
                "comparison_project_ids",
                not missing,
                f"expected={expected_ids!r} actual={actual_ids!r}",
            )
        )
    if "min_comparison_rows" in expect:
        rows = comparison.get("comparison_rows") if isinstance(comparison, dict) else []
        actual_count = len(rows) if isinstance(rows, list) else 0
        expected_count = int(expect["min_comparison_rows"])
        checks.append(
            HarnessCheck(
                "min_comparison_rows",
                actual_count >= expected_count,
                f"expected>={expected_count} actual={actual_count}",
            )
        )

    analysis_result = result.get("data") or {}
    if not isinstance(analysis_result, dict):
        analysis_result = {}
    checks.extend(
        _runtime_validation_checks(
            answer_quality=answer_quality,
            analysis_result=analysis_result,
        )
    )
    if "agent_role" in expect:
        actual = _as_text(analysis_result.get("agent_role"))
        expected = _as_text(expect["agent_role"])
        checks.append(
            HarnessCheck(
                "agent_role",
                actual == expected,
                f"expected={expected!r} actual={actual!r}",
            )
        )
    if "question_type" in expect:
        actual = _as_text(analysis_result.get("question_type"))
        expected = _as_text(expect["question_type"])
        checks.append(
            HarnessCheck(
                "question_type",
                actual == expected,
                f"expected={expected!r} actual={actual!r}",
            )
        )

    task_steps = {
        _as_text(step.get("step_id")): _as_text(step.get("status"))
        for step in (result.get("task_plan") or {}).get("steps", [])
        if isinstance(step, dict)
    }
    for step_id in expect.get("required_task_steps", []) or []:
        actual = task_steps.get(step_id)
        checks.append(
            HarnessCheck(
                f"task_step_completed:{step_id}",
                actual == "completed",
                f"actual={actual!r}",
            )
        )

    answer = _answer_text(result)
    if "min_answer_chars" in expect:
        actual_len = len(answer)
        expected_len = int(expect["min_answer_chars"])
        checks.append(
            HarnessCheck(
                "min_answer_chars",
                actual_len >= expected_len,
                f"expected>={expected_len} actual={actual_len}",
            )
        )
    if "answer_must_contain" in expect:
        passed, detail = _contains_all(answer, list(expect["answer_must_contain"]))
        checks.append(HarnessCheck("answer_must_contain", passed, detail))
    if "answer_must_not_contain" in expect:
        passed, detail = _contains_none(answer, list(expect["answer_must_not_contain"]))
        checks.append(HarnessCheck("answer_must_not_contain", passed, detail))
    if "answer_target_values_must_contain" in expect:
        values = list(expect["answer_target_values_must_contain"])
        passed, detail = _contains_all_tolerant(answer, values)
        checks.append(HarnessCheck("answer_target_values_must_contain", passed, detail))

    analysis_expect = expect.get("analysis_expect")
    if isinstance(analysis_expect, dict):
        checks.extend(
            HarnessCheck(f"analysis.{check.name}", check.passed, check.detail)
            for check in evaluate_project_analysis(analysis_result, analysis_expect)
        )

    return checks


def evaluate_question_router(result: dict[str, Any], expect: dict[str, Any]) -> list[HarnessCheck]:
    checks: list[HarnessCheck] = []
    examples = result.get("examples") or []
    if "min_examples" in expect:
        actual_count = len(examples)
        expected_count = int(expect["min_examples"])
        checks.append(
            HarnessCheck(
                "min_examples",
                actual_count >= expected_count,
                f"expected>={expected_count} actual={actual_count}",
            )
        )

    for example in examples:
        example_id = _as_text(example.get("id") or example.get("question"))
        route = example.get("route") or {}
        expected = example.get("expect") or {}

        for field in ("intent", "route", "requires_project", "needs_chart"):
            if field not in expected:
                continue
            actual = route.get(field)
            expected_value = expected[field]
            checks.append(
                HarnessCheck(
                    f"{example_id}.{field}",
                    actual == expected_value,
                    f"expected={expected_value!r} actual={actual!r}",
                )
            )

        if "target_metrics_contains" in expected:
            actual_metrics = [str(item) for item in route.get("target_metrics", []) or []]
            expected_metrics = [str(item) for item in expected["target_metrics_contains"]]
            missing = [item for item in expected_metrics if item not in actual_metrics]
            checks.append(
                HarnessCheck(
                    f"{example_id}.target_metrics_contains",
                    not missing,
                    "missing: " + ", ".join(missing) if missing else "matched",
                )
            )

        if "question_tags_contains" in expected:
            actual_tags = [str(item) for item in route.get("question_tags", []) or []]
            expected_tags = [str(item) for item in expected["question_tags_contains"]]
            missing = [item for item in expected_tags if item not in actual_tags]
            checks.append(
                HarnessCheck(
                    f"{example_id}.question_tags_contains",
                    not missing,
                    "missing: " + ", ".join(missing) if missing else "matched",
                )
            )

        if "min_confidence" in expected:
            actual_confidence = float(route.get("confidence") or 0)
            expected_confidence = float(expected["min_confidence"])
            checks.append(
                HarnessCheck(
                    f"{example_id}.min_confidence",
                    actual_confidence >= expected_confidence,
                    f"expected>={expected_confidence} actual={actual_confidence}",
                )
            )

    return checks
