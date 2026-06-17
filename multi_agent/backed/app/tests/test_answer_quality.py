import asyncio
from types import SimpleNamespace

from multi_agent.backed.app.services.business_agent import response_service
from multi_agent.backed.app.services.business_agent.answer_quality_service import (
    BusinessAnswerQualityService,
)
from multi_agent.backed.app.services.business_agent.claim_service import ClaimService
from multi_agent.backed.app.harness.evaluators.assertions import evaluate_business_agent
from multi_agent.backed.app.multi_agent.project_progress import (
    get_round_project_progress_queue,
    init_round_project_progress_queue,
    reset_round_project_progress_queue,
)
from multi_agent.backed.app.services.business_agent.runtime_service import (
    BusinessAgentRuntimeService,
)


def _analysis_result() -> dict:
    return {
        "question": "为什么 S1 的 FRiP 有问题，应该怎么排查",
        "analysis_plan": {"target_metrics": ["frip_ratio"]},
        "metric_priority": ["frip_ratio"],
        "evidence_chain": [
            {
                "metric_key": "frip_ratio",
                "metric": "FRiP",
                "sample": "S1",
                "value": 0.18,
                "display_value": "0.1800",
                "source_file": "FRiP.xls",
                "source_field": "FRiP",
                "severity": "unverified_threshold",
                "threshold_needs_project_validation": True,
                "interpretation": "FRiP 需要结合 peak 数量、背景噪音和对照样本共同解释。",
                "downstream_impact": "富集信号不足时可能降低 peak 结果的可解释性。",
            }
        ],
        "analysis_limits": [
            "项目文件中未确认 FRiP 的专属判断阈值。",
        ],
        "next_actions": [
            "查看 peak count、IgG/Input 对照和样本相关性，确认信号是否一致。",
        ],
        "diagnosis_summary": {
            "conclusions": ["已读取 S1 的 FRiP 观测值。"],
            "possible_causes": [
                "可能与 peak 富集不足、背景噪音或对照设置有关。",
            ],
            "next_actions": [
                "查看 peak count、IgG/Input 对照和样本相关性，确认信号是否一致。",
            ],
        },
    }


def test_shallow_answer_requires_quality_repair():
    quality = BusinessAnswerQualityService.evaluate(
        answer="FRiP 有问题，需要关注。",
        analysis_result=_analysis_result(),
        question_route={"route": "project_qa"},
    )

    assert quality["passed"] is False
    assert quality["score"] < quality["pass_score"]
    assert {item["rule"] for item in quality["issues"]} >= {
        "insufficient_evidence_grounding",
        "shallow_reasoning",
        "missing_threshold_limitation",
    }


def test_structured_quality_repair_is_grounded_and_actionable():
    analysis_result = _analysis_result()
    original_quality = BusinessAnswerQualityService.evaluate(
        answer="FRiP 有问题，需要关注。",
        analysis_result=analysis_result,
        question_route={"route": "project_qa"},
    )
    repaired = BusinessAnswerQualityService.build_repair_answer(
        analysis_result=analysis_result,
        quality=original_quality,
    )
    repaired_quality = BusinessAnswerQualityService.evaluate(
        answer=repaired,
        analysis_result=analysis_result,
        question_route={"route": "project_qa"},
    )

    assert "S1 FRiP=0.1800" in repaired
    assert "FRiP.xls::FRiP" in repaired
    assert "可能原因/解释" in repaired
    assert "可能下游影响" in repaired
    assert "项目文件中未确认" in repaired
    assert "查看 peak count" in repaired
    assert repaired_quality["passed"] is True
    assert repaired_quality["score"] > original_quality["score"]


def test_harness_can_assert_answer_quality_score():
    checks = evaluate_business_agent(
        {
            "success": True,
            "result_payload": {
                "answer": "grounded answer",
                "answer_quality": {
                    "passed": True,
                    "score": 88,
                    "repair_applied": True,
                },
            },
        },
        {
            "answer_quality_passed": True,
            "answer_quality_min_score": 80,
            "answer_quality_repair_applied": True,
        },
    )

    assert checks
    assert all(check.passed for check in checks)


def test_harness_rejects_failed_runtime_quality_without_case_opt_in():
    checks = evaluate_business_agent(
        {
            "success": True,
            "result_payload": {
                "answer": "shallow answer",
                "answer_quality": {"passed": False, "score": 35},
            },
        },
        {"success": True},
    )

    failed = {check.name for check in checks if not check.passed}
    assert "runtime_answer_quality_passed" in failed


def test_harness_rejects_failed_runtime_claim_validation():
    checks = evaluate_business_agent(
        {
            "success": True,
            "result_payload": {
                "answer": "grounded answer",
                "answer_quality": {"passed": True, "score": 88},
            },
            "data": {
                "claim_validation": {
                    "passed": False,
                    "invalid_claim_count": 1,
                }
            },
        },
        {"success": True},
    )

    failed = {check.name for check in checks if not check.passed}
    assert "runtime_claim_validation_passed" in failed


def test_harness_can_require_target_evidence_values_in_answer():
    checks = evaluate_business_agent(
        {
            "success": True,
            "result_payload": {
                "answer": "T1 Adapter=38.24%，T2 Adapter=39.19%。",
                "answer_quality": {"passed": True, "score": 85},
            },
        },
        {
            "answer_target_values_must_contain": ["38.24%", "39.19%"],
        },
    )

    assert all(check.passed for check in checks)


def test_quality_repair_uses_ranked_cause_evidence_and_verification():
    analysis_result = _analysis_result()
    analysis_result["diagnosis_summary"]["ranked_causes"] = [
        {
            "rank": 1,
            "cause_id": "insufficient_effective_reads",
            "label": "核基因组有效 reads 不足",
            "reasoning_summary": "该假设获得独立关联指标支持。",
            "supporting_evidence": [
                {
                    "sample": "S1",
                    "metric_key": "unique_mapping_rate_percent",
                    "value": "12.60%",
                    "reason": "项目阈值支持该关联指标需要复核。",
                }
            ],
            "contradicting_evidence": [],
            "verification_actions": [
                "逐级核算 raw、clean、mapped、unique 和 peak calling 输入 reads。",
            ],
        }
    ]

    repaired = BusinessAnswerQualityService.build_repair_answer(
        analysis_result=analysis_result,
        quality={"target_metrics": ["frip_ratio"]},
    )

    assert "## 根因排序" in repaired
    assert "核基因组有效 reads 不足" in repaired
    assert "支持证据" in repaired
    assert "反证" in repaired
    assert "验证动作" in repaired


def test_quality_repair_keeps_related_metric_out_of_direct_answer():
    analysis_result = {
        "question": "为什么线粒体 reads 比例高",
        "analysis_plan": {"target_metrics": ["mt_rate_percent"]},
        "metric_priority": ["mt_rate_percent", "frip_ratio"],
        "project_context": {"config": {"species": "hg38"}},
        "evidence_chain": [
            {
                "metric_key": "mt_rate_percent",
                "metric": "Mitochondrial alignment rate",
                "sample": "T1",
                "display_value": "72.61%",
                "source_file": "AlignmentQC.xls",
                "source_field": "MT_Ratio",
                "severity": "unverified_threshold",
                "threshold_needs_project_validation": True,
            },
            {
                "metric_key": "frip_ratio",
                "metric": "FRiP",
                "sample": "T1",
                "display_value": "0.1482",
                "source_file": "FRiP.xls",
                "source_field": "FRiP",
                "severity": "unverified_threshold",
                "threshold_needs_project_validation": True,
            },
        ],
        "diagnosis_summary": {"conclusions": [], "possible_causes": [], "next_actions": []},
    }
    quality = BusinessAnswerQualityService.evaluate(
        answer="需要排查。",
        analysis_result=analysis_result,
        question_route={"route": "project_qa"},
    )

    repaired = BusinessAnswerQualityService.build_repair_answer(
        analysis_result=analysis_result,
        quality=quality,
    )

    assert "线粒体 reads 比例=72.61%" in repaired
    assert "FRiP=0.1482" not in repaired


def test_target_metric_without_valid_claim_never_falls_back_to_unrelated_observation():
    unrelated_claim = {
        "claim_type": "observation",
        "metric_id": "mapping_rate_percent",
        "text": "S1 的比对率为 90.00%。",
        "evidence_ids": ["ev-map"],
    }
    cards = [
        {
            "evidence_id": "ev-map",
            "metric_id": "mapping_rate_percent",
            "metric": "Mapping",
            "sample": "S1",
            "display_value": "90.00%",
            "source_file": "AlignmentQC.xls",
            "source_field": "Mapping",
        },
        {
            "evidence_id": "ev-adapter",
            "metric_id": "adapter_percent",
            "metric": "Adapter",
            "sample": "S1",
            "display_value": "39.19%",
            "source_file": "ReadsQC.xls",
            "source_field": "Adapter",
            "conflict_status": "unresolved",
        },
    ]

    rendered = ClaimService.render_markdown(
        validated_claims=[unrelated_claim],
        evidence_cards=cards,
        target_metrics={"adapter_percent"},
    )

    assert "Adapter" in rendered
    assert "39.19%" in rendered
    assert "90.00%" not in rendered
    assert "不能用其他指标替代回答" in rendered


def test_controlled_quality_answer_passes_without_substituting_related_metrics():
    analysis_result = {
        "question": "Adapter 指标是什么，怎么计算",
        "analysis_plan": {"target_metrics": ["adapter_percent"]},
        "evidence_chain": [
            {
                "metric_key": "mapping_rate_percent",
                "metric": "Mapping",
                "sample": "S1",
                "display_value": "90.00%",
                "source_file": "AlignmentQC.xls",
                "source_field": "Mapping",
            }
        ],
        "next_actions": ["检查 Adapter 来源字段和分母口径。"],
    }

    quality = BusinessAnswerQualityService.evaluate(
        answer="需要关注。",
        analysis_result=analysis_result,
        question_route={"route": "project_qa"},
    )
    controlled = BusinessAnswerQualityService.build_controlled_answer(
        analysis_result=analysis_result,
        quality=quality,
    )
    controlled_quality = BusinessAnswerQualityService.evaluate(
        answer=controlled,
        analysis_result=analysis_result,
        question_route={"route": "project_qa"},
    )

    assert "Adapter" in controlled
    assert "90.00%" not in controlled
    assert controlled_quality["passed"] is True


def test_overview_does_not_promote_metric_priority_to_user_targets():
    analysis_result = {
        "question": "还有什么问题",
        "question_type": "overview",
        "analysis_plan": {"target_metrics": []},
        "metric_priority": [
            "overview",
            "alignment",
            "qc",
            "frip",
            "mapping",
            "mt",
            "chrmt",
            "peak",
            "duplicate",
            "adapter",
        ],
        "evidence_chain": [
            {
                "metric_key": "frip_ratio",
                "metric": "FRiP",
                "sample": "T1",
                "display_value": "14.82%",
                "source_file": "FRiP_score.xls",
                "source_field": "FRiP",
                "threshold_needs_project_validation": True,
            }
        ],
    }

    quality = BusinessAnswerQualityService.evaluate(
        answer="## 项目证据\n- T1 FRiP=14.82%；来源 FRiP_score.xls::FRiP。\n\n## 证据限制\n- 项目文件中未确认适用阈值。",
        analysis_result=analysis_result,
        question_route={"route": "project_analysis"},
    )
    controlled = BusinessAnswerQualityService.build_controlled_answer(
        analysis_result=analysis_result,
        quality=quality,
    )

    assert quality["question_mode"] == "overview"
    assert quality["target_metrics"] == []
    assert "adapter、alignment" not in controlled
    assert "当前项目概览" not in controlled or "全部指标" not in controlled


def test_explicit_plan_target_still_survives_without_matching_evidence():
    analysis_result = {
        "question": "Adapter 指标是什么",
        "analysis_plan": {"target_metrics": ["adapter_percent"]},
        "metric_priority": ["overview", "mapping", "frip"],
        "evidence_chain": [],
    }

    quality = BusinessAnswerQualityService.evaluate(
        answer="项目中未读取到 Adapter 的结构化证据，需要补充对应结果文件。",
        analysis_result=analysis_result,
        question_route={"route": "project_analysis"},
    )

    assert quality["target_metrics"] == ["adapter_percent"]


def test_project_runtime_publishes_stream_deltas_and_final_answer_event():
    token = init_round_project_progress_queue()
    try:
        asyncio.run(
            response_service.business_response_service._stream_with_project_deltas(
                _async_iter(["A", "B", "C"])
            )
        )
        queue = get_round_project_progress_queue()
        events = [queue.get_nowait(), queue.get_nowait(), queue.get_nowait()]

        assert events == [
            {"type": "project_answer_delta", "text": "A"},
            {"type": "project_answer_delta", "text": "B"},
            {"type": "project_answer_delta", "text": "C"},
        ]
        assert queue.empty()
    finally:
        reset_round_project_progress_queue(token)


async def _async_iter(items):
    for item in items:
        yield item


def test_project_runtime_guard_preserves_model_answer_verbatim():
    model_answer = "  模型原始回答\n\n```text\n不得被后端替换\n```\n"

    answer, guard = asyncio.run(
        BusinessAgentRuntimeService._enforce_project_answer_guard(
            answer=model_answer,
            analysis_result={},
            question_route={"route": "project_analysis"},
        )
    )

    assert answer == model_answer
    assert guard["action"] == "disabled"


def test_project_runtime_quality_is_observe_only(monkeypatch):
    model_answer = "模型给出的最终回答"

    monkeypatch.setattr(
        BusinessAnswerQualityService,
        "evaluate",
        classmethod(lambda cls, **kwargs: {"passed": False, "score": 20, "issues": []}),
    )

    def fail_if_repair_runs(cls, **kwargs):
        raise AssertionError("quality repair must not replace the model answer")

    monkeypatch.setattr(
        BusinessAnswerQualityService,
        "build_repair_answer",
        classmethod(fail_if_repair_runs),
    )

    answer, quality, guard = asyncio.run(
        BusinessAgentRuntimeService._apply_answer_quality_gate(
            answer=model_answer,
            analysis_result={},
            question_route={"route": "project_analysis"},
            harness_guard={"action": "disabled"},
        )
    )

    assert answer == model_answer
    assert quality["enforcement_mode"] == "observe_only"
    assert quality["repair_applied"] is False
    assert guard["action"] == "disabled"


def test_nonstream_model_answer_bypasses_backend_cleaning(monkeypatch):
    model_answer = "```text\n模型原始代码块内容\n```"

    async def fake_create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=model_answer))]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create),
        )
    )
    monkeypatch.setattr(response_service, "sub_model_client", fake_client)

    answer = asyncio.run(
        response_service.business_response_service.generate_fused_answer(
            question="分析项目",
            analysis_result={},
            retrieval_payload={},
            experience_summary={},
        )
    )

    assert answer == model_answer


def test_overview_claim_rendering_spans_distinct_metric_families():
    claims = []
    cards = []
    for metric_id, display in (
        ("adapter_percent", "38.24%"),
        ("adapter_percent", "39.19%"),
        ("mapping_rate_percent", "57.54%"),
        ("unique_mapping_rate_percent", "12.60%"),
        ("mt_rate_percent", "72.61%"),
        ("frip_ratio", "14.82%"),
        ("correlation", "-0.6303"),
    ):
        evidence_id = f"ev_{len(cards)}"
        claim_id = f"cl_{len(claims)}"
        cards.append(
            {
                "evidence_id": evidence_id,
                "metric_id": metric_id,
                "sample": "T1",
                "display_value": display,
                "denominator": "reads",
                "source_file": "result.xls",
                "source_field": metric_id,
            }
        )
        claims.append(
            {
                "claim_id": claim_id,
                "claim_type": "observation",
                "metric_id": metric_id,
                "text": f"{metric_id}={display}",
                "evidence_ids": [evidence_id],
            }
        )

    rendered = ClaimService.render_markdown(
        validated_claims=claims,
        evidence_cards=cards,
        target_metrics=set(),
    )

    assert "mapping_rate_percent=57.54%" in rendered
    assert "frip_ratio=14.82%" in rendered
    assert "correlation=-0.6303" in rendered
    assert rendered.index("mapping_rate_percent=57.54%") < rendered.index("adapter_percent=38.24%")


def test_answer_quality_ignores_null_placeholder_evidence():
    evidence = BusinessAnswerQualityService._evidence_entries(
        {
            "evidence_chain": [
                {"metric_key": "q30_ratio", "value": None, "display_value": "-"},
                {"metric_key": "frip_ratio", "value": 0.1482, "display_value": "14.82%"},
            ]
        }
    )

    assert [item["metric_key"] for item in evidence] == ["frip_ratio"]


def test_overview_focused_on_one_metric_requires_diverse_repair():
    metric_rows = (
        ("mapping_rate_percent", "比对率", "57.54%", "AlignmentQC.xls", "Mapping"),
        ("unique_mapping_rate_percent", "唯一比对率", "12.60%", "AlignmentQC.xls", "Unique"),
        ("mt_rate_percent", "线粒体 reads 比例", "72.61%", "AlignmentQC.xls", "chrMT"),
        ("frip_ratio", "FRiP", "14.82%", "FRiP_score.xls", "FRiP"),
        ("correlation", "样本相关性", "-0.6303", "spearman.tab", "T1:T2"),
        ("duplicate_rate_percent", "重复率", "33.66%", "AlignmentQC.xls", "Duplicate"),
    )
    evidence_chain = []
    evidence_cards = []
    validated_claims = []
    for index, (metric_id, metric, display, source_file, source_field) in enumerate(metric_rows):
        evidence_id = f"ev_{index}"
        evidence_chain.append(
            {
                "metric_key": metric_id,
                "metric": metric,
                "sample": "T1",
                "value": float(display.rstrip("%")),
                "display_value": display,
                "source_file": source_file,
                "source_field": source_field,
                "threshold_needs_project_validation": True,
            }
        )
        evidence_cards.append(
            {
                "evidence_id": evidence_id,
                "metric_id": metric_id,
                "metric": metric,
                "sample": "T1",
                "value": float(display.rstrip("%")),
                "display_value": display,
                "source_file": source_file,
                "source_field": source_field,
            }
        )
        validated_claims.append(
            {
                "claim_id": f"cl_{index}",
                "claim_type": "observation",
                "metric_id": metric_id,
                "text": f"T1 的 {metric} 观测值为 {display}。",
                "evidence_ids": [evidence_id],
            }
        )
    analysis_result = {
        "question": "还有什么问题",
        "question_type": "overview",
        "analysis_plan": {"target_metrics": []},
        "evidence_chain": evidence_chain,
        "evidence_cards": evidence_cards,
        "validated_claims": validated_claims,
    }
    mapping_only_answer = (
        "当前读取到比对率 T1=57.54%。来源 AlignmentQC.xls::Mapping。"
        "项目文件中未确认阈值，只能作为观测值，不能单独判定异常。"
    )

    original_quality = BusinessAnswerQualityService.evaluate(
        answer=mapping_only_answer,
        analysis_result=analysis_result,
        question_route={"route": "project_analysis"},
    )
    repaired = BusinessAnswerQualityService.build_repair_answer(
        analysis_result=analysis_result,
        quality=original_quality,
    )
    repaired_quality = BusinessAnswerQualityService.evaluate(
        answer=repaired,
        analysis_result=analysis_result,
        question_route={"route": "project_analysis"},
    )

    assert original_quality["passed"] is False
    assert "incomplete_overview_coverage" in {
        item["rule"] for item in original_quality["issues"]
    }
    assert repaired_quality["passed"] is True
    assert repaired_quality["overview_coverage"]["covered_metric_count"] >= 4
    assert "FRiP" in repaired
    assert "样本相关性" in repaired
