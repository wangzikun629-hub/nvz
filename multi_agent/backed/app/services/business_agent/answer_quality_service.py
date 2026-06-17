from __future__ import annotations

import re
from typing import Any

from multi_agent.backed.app.services.business_agent.claim_service import claim_service
from multi_agent.backed.app.services.business_agent.fact_verification_service import (
    fact_verification_service,
)


class BusinessAnswerQualityService:
    PASS_SCORE = 70
    METRIC_LABELS = {
        "adapter_percent": "Adapter（原始 reads 接头检出率）",
        "q30_ratio": "Q30",
        "mapping_rate_percent": "比对率",
        "unique_mapping_rate_percent": "唯一比对率",
        "duplicate_rate_percent": "重复率",
        "mt_rate_percent": "线粒体 reads 比例",
        "frip_ratio": "FRiP",
        "correlation": "样本相关性",
        "peak_count": "Peak 数量",
        "peak_width": "Peak 宽度",
        "tss_enrichment": "TSS enrichment",
        "fragment_size": "Fragment size",
        "spikein_mapped_reads": "Spike-in mapped reads",
        "spikein_unique_mapping_rate_percent": "Spike-in 唯一比对率",
        "spikein_scaling_factor": "Spike-in scaling factor",
        "control_binding_status": "对照绑定状态",
    }
    METRIC_ALIASES = {
        "adapter_percent": ("adapter", "接头检出率", "接头比例"),
        "q30_ratio": ("q30",),
        "mapping_rate_percent": ("mapping", "总比对率", "比对率"),
        "unique_mapping_rate_percent": ("unique", "唯一比对率"),
        "duplicate_rate_percent": ("duplicate", "duplication", "重复率"),
        "mt_rate_percent": ("chrmt", "mt_ratio", "线粒体", "细胞器"),
        "frip_ratio": ("frip",),
        "correlation": ("correlation", "spearman", "相关性"),
        "peak_count": ("peak", "peak count", "峰数量"),
        "peak_width": ("peak width", "峰宽"),
        "tss_enrichment": ("tss enrichment", "tss富集"),
        "fragment_size": ("fragment size", "片段长度"),
        "spikein_mapped_reads": ("spike-in", "spikein"),
        "spikein_unique_mapping_rate_percent": ("spike-in unique", "spikein unique"),
        "spikein_scaling_factor": ("spike-in scaling", "spikein scaling"),
        "control_binding_status": ("control", "igg", "input", "对照"),
    }
    TARGET_ALIASES = {
        "chrmt_pt_rate_percent": "mt_rate_percent",
        "chrmt_rate_percent": "mt_rate_percent",
        "mt_ratio": "mt_rate_percent",
        "frip": "frip_ratio",
    }
    GENERIC_FILLER = (
        "根据您提供的信息",
        "综合来看",
        "总体而言",
        "需要注意的是",
        "希望以上信息",
        "如有其他问题",
        "仅供参考",
    )
    LIMIT_MARKERS = (
        "项目文件中未确认",
        "项目阈值未确认",
        "未确认该指标阈值",
        "仅记录观测值",
        "只能作为观测",
        "不能单独判定",
        "不支持阈值判定",
        "证据不足",
    )
    CAUSE_MARKERS = ("原因", "可能与", "可能由", "提示", "解释为", "上游")
    IMPACT_MARKERS = ("影响", "下游", "导致", "减少", "增加", "干扰")
    SUPPORT_MARKERS = ("证据支持", "当前证据", "不支持", "尚不能", "未确认", "限制")
    ACTION_MARKERS = ("核对", "检查", "复核", "确认", "比较", "查看", "补充", "排查", "验证", "结合")

    @classmethod
    def evaluate_packet(
        cls,
        *,
        fact_packet: dict[str, Any],
        reasoning_packet: dict[str, Any],
        analysis_result: dict[str, Any],
        question_route: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fact_verification = fact_verification_service.verify(answer="", analysis_result=analysis_result)
        # direct_conclusions and project_evidence may be dicts (Phase 2+) or strings (legacy).
        direct_conclusions = [item for item in (fact_packet.get("direct_conclusions") or []) if item]
        project_evidence = [item for item in (fact_packet.get("project_evidence") or []) if item]
        ts = fact_packet.get("threshold_status") or {}
        has_unverified_thresholds = bool(ts.get("has_unverified_thresholds"))
        threshold_statement = str(ts.get("statement") or "").strip()
        hypothesis_comparison = [str(item).strip() for item in (reasoning_packet.get("hypothesis_comparison") or []) if str(item).strip()]
        verification_plan = [str(item).strip() for item in (reasoning_packet.get("verification_plan") or []) if str(item).strip()]
        possible_causes = [str(item).strip() for item in (reasoning_packet.get("possible_causes") or []) if str(item).strip()]
        response_plan = (analysis_result.get("analysis_plan") or {}).get("response_plan") or {}

        dimensions = {
            "fact_correctness": {
                "score": max(0, 30 - 6 * len([i for i in (fact_verification.get("issues") or []) if i.get("severity") == "severe"])),
                "max_score": 30,
                "details": [f"fact_issues={len(fact_verification.get('issues') or [])}"],
            },
            "evidence_coverage": {
                "score": min(20, 4 + len(project_evidence) * 2),
                "max_score": 20,
                "details": [f"project_evidence={len(project_evidence)}"],
            },
            "unsupported_conclusion_control": {
                "score": 15 if direct_conclusions else 6,
                "max_score": 15,
                "details": [f"direct_conclusions={len(direct_conclusions)}"],
            },
            "unit_accuracy": {
                "score": max(0, 15 - 8 * int(fact_verification.get("unit_error_count", 0) or 0)),
                "max_score": 15,
                "details": [f"unit_error_count={fact_verification.get('unit_error_count', 0)}"],
            },
            "experimental_design_discipline": {
                "score": 10 - 5 * int(fact_verification.get("sample_role_conflict_count", 0) or 0),
                "max_score": 10,
                "details": [f"design_error_count={fact_verification.get('sample_role_conflict_count', 0)}"],
            },
            "causal_discipline": {
                "score": 5 - 5 * int(fact_verification.get("causal_overstatement_count", 0) or 0),
                "max_score": 5,
                "details": [f"causal_overstatement_count={fact_verification.get('causal_overstatement_count', 0)}"],
            },
            "complexity_fit": {
                "score": 5 if response_plan else 3,
                "max_score": 5,
                "details": [f"reasoning_mode={response_plan.get('reasoning_mode', '')}"],
            },
            "integration_depth": {
                "score": min(10, len(possible_causes) * 2 + len(verification_plan)),
                "max_score": 10,
                "details": [f"possible_causes={len(possible_causes)}", f"verification_plan={len(verification_plan)}"],
            },
            "hypothesis_discrimination": {
                "score": min(10, len(hypothesis_comparison) * 3),
                "max_score": 10,
                "details": [f"hypothesis_comparison={len(hypothesis_comparison)}"],
            },
            "assay_specificity": {
                "score": 2 + int(bool((analysis_result.get("assay_profile") or {}).get("assay"))) * 3,
                "max_score": 5,
                "details": [f"assay={(analysis_result.get('assay_profile') or {}).get('assay', '')}"],
            },
            "question_core_coverage": {
                "score": 8 if direct_conclusions else 4,
                "max_score": 10,
                "details": [f"direct_conclusions={len(direct_conclusions)}"],
            },
            "matrix_reasoning_coverage": {
                "score": 10 if hypothesis_comparison or possible_causes else 4,
                "max_score": 10,
                "details": [f"has_reasoning={bool(hypothesis_comparison or possible_causes)}"],
            },
        }
        if threshold_statement:
            dimensions["unsupported_conclusion_control"]["score"] = min(
                dimensions["unsupported_conclusion_control"]["max_score"],
                dimensions["unsupported_conclusion_control"]["score"] + 2,
            )
        raw_score = sum(max(0, item["score"]) for item in dimensions.values())
        total_max_score = sum(item.get("max_score", 0) for item in dimensions.values())
        score = round(100 * raw_score / max(1, total_max_score))
        issues: list[dict[str, str]] = []
        # Only flag missing threshold limitation when the project actually has unverified thresholds.
        if has_unverified_thresholds and not threshold_statement:
            issues.append(cls._issue("missing_threshold_limitation", "项目阈值未确认，但 packet 中没有正式限制语句。", "severe"))
        if response_plan.get("reasoning_mode") == "integrative_reasoning" and not hypothesis_comparison:
            issues.append(cls._issue("missing_hypothesis_comparison", "复杂问题的 reasoning packet 缺少竞争性假设比较。", "severe"))
        if not project_evidence:
            issues.append(cls._issue("insufficient_evidence_grounding", "fact packet 缺少项目证据条目。", "severe"))
        # Filter out fact_verification rules whose meaning is "the rendered answer lacks X".
        # Under packet-first evaluation we call the verifier with answer="" so it can derive
        # analysis-level metrics (unit_error_count, sample_role_conflict_count, etc.) without
        # seeing the answer text. The text-derived severe rules below would fire spuriously
        # because the answer is empty at evaluation time — the actual answer is rendered
        # downstream by response_service. Packet-level checks above already enforce the
        # threshold/hypothesis equivalents.
        TEXT_DERIVED_OR_PACKET_DUP_RULES = {
            "missing_threshold_limitation",
            "missing_hypothesis_comparison",
            "critical_modality_omission",
            "matrix_reasoning_missing_cross_frip_context",
            "cross_frip_directionality_mismatch",
            "evidence_presence_mismatch",
            "numeric_claim_not_found_in_project_evidence",
            "adapter_processing_stage_mismatch",
            "species_organelle_mismatch",
            "no_unverified_threshold_judgement",
            "target_metric_value_omission",
            # render_alignment rules — emitted by verify_render_alignment when called
            # with answer="" inside evaluate_packet(). These check whether the rendered
            # answer text mentions thresholds / metric terms. Under packet-first
            # evaluation, the answer is rendered downstream by response_service, so
            # firing these here is spurious — packet-level checks above already cover
            # the structural equivalents.
            "render_missing_threshold_limitation",
            "render_missing_key_evidence",
        }
        for item in fact_verification.get("issues", []) or []:
            rule = str(item.get("rule") or "")
            if rule in TEXT_DERIVED_OR_PACKET_DUP_RULES:
                continue
            issues.append(
                cls._issue(
                    rule or "fact_verification_failure",
                    str(item.get("text") or item.get("metric_id") or rule or ""),
                    "severe" if item.get("severity") == "severe" else "moderate",
                )
            )
        needs_repair = score < cls.PASS_SCORE or any(item["severity"] == "severe" for item in issues)
        return {
            "passed": not needs_repair,
            "status": "pass" if not needs_repair else "repair_required",
            "score": score,
            "pass_score": cls.PASS_SCORE,
            "dimensions": dimensions,
            "fact_verification": fact_verification,
            "issues": issues,
            "response_plan": response_plan,
            "answer_chars": 0,
            "repair_applied": False,
            "evaluation_mode": "packet_first",
        }

    @classmethod
    def evaluate(
        cls,
        *,
        answer: str,
        analysis_result: dict[str, Any],
        question_route: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Phase 3: packet-first routing.
        # If analysis_result already carries a structured fact_packet (assembled in
        # Phase 2 by project_analysis_service), delegate directly to evaluate_packet().
        # This avoids all text-based heuristics for the score dimensions that have
        # packet-level equivalents, while preserving the legacy path as fallback.
        fp = analysis_result.get("fact_packet")
        rp = analysis_result.get("reasoning_packet")
        if isinstance(fp, dict) and fp.get("project_evidence") is not None:
            return cls.evaluate_packet(
                fact_packet=fp,
                reasoning_packet=rp if isinstance(rp, dict) else {},
                analysis_result=analysis_result,
                question_route=question_route,
            )

        # Legacy text-based path (fallback when no fact_packet is present).
        text = cls._normalize_text(answer)
        evidence = cls._evidence_entries(analysis_result)
        target_metrics = cls._target_metrics(analysis_result, evidence)
        target_evidence = [
            item
            for item in evidence
            if not target_metrics or str(item.get("metric_key") or "") in target_metrics
        ]
        question_mode = cls._question_mode(analysis_result, question_route)
        scoring_evidence = target_evidence
        if question_mode == "overview" and not target_metrics:
            scoring_evidence = cls._overview_evidence_entries(target_evidence)
        overview_coverage = cls._overview_metric_coverage(text, scoring_evidence)
        unverified_threshold = any(
            bool(item.get("threshold_needs_project_validation"))
            or item.get("severity") == "unverified_threshold"
            for item in scoring_evidence
        )
        diagnosis = analysis_result.get("diagnosis_summary") or {}
        next_actions = cls._list_values(analysis_result.get("next_actions") or diagnosis.get("next_actions"))
        response_plan = (analysis_result.get("analysis_plan") or {}).get("response_plan") or {}

        legacy_dimensions = {
            "directness": cls._score_directness(text, target_metrics, scoring_evidence),
            "evidence_grounding": cls._score_evidence(text, scoring_evidence),
            "reasoning_depth": cls._score_depth(text, question_mode),
            "actionability": cls._score_actionability(text, next_actions),
            "uncertainty_discipline": cls._score_uncertainty(text, unverified_threshold),
            "concision": cls._score_concision(text),
        }
        fact_verification = analysis_result.get("fact_verification")
        if not isinstance(fact_verification, dict):
            fact_verification = fact_verification_service.verify(
                answer=answer,
                analysis_result=analysis_result,
            )
        dimensions = cls._professional_dimensions(
            fact_verification=fact_verification,
            legacy_dimensions=legacy_dimensions,
            analysis_result=analysis_result,
        )
        integration_depth = cls._score_integration_depth(text, analysis_result)
        hypothesis_discrimination = cls._score_hypothesis_discrimination(text, analysis_result)
        assay_specificity = cls._score_assay_specificity(text, analysis_result)
        question_core_coverage = cls._score_question_core_coverage(text, analysis_result)
        matrix_reasoning = cls._score_matrix_reasoning(text, analysis_result)
        dimensions.update(
            {
                "integration_depth": integration_depth,
                "hypothesis_discrimination": hypothesis_discrimination,
                "assay_specificity": assay_specificity,
                "question_core_coverage": question_core_coverage,
                "matrix_reasoning_coverage": matrix_reasoning,
            }
        )
        raw_score = sum(item["score"] for item in dimensions.values())
        total_max_score = sum(item.get("max_score", 0) for item in dimensions.values())
        score = round(100 * raw_score / max(1, total_max_score))
        issues: list[dict[str, str]] = []
        if legacy_dimensions["directness"]["score"] < 12:
            issues.append(cls._issue("weak_direct_answer", "回答没有直接回应目标指标或当前问题。", "moderate"))
        if target_evidence and legacy_dimensions["evidence_grounding"]["score"] < 15:
            issues.append(cls._issue("insufficient_evidence_grounding", "回答未充分引用项目样本值或来源字段。", "severe"))
        if (
            question_mode == "overview"
            and overview_coverage["available_metric_count"] >= 2
            and overview_coverage["covered_metric_count"] < overview_coverage["required_metric_count"]
        ):
            issues.append(
                cls._issue(
                    "incomplete_overview_coverage",
                    "项目概览只覆盖了少数指标，未横跨质控、比对、富集或样本一致性等代表指标族。",
                    "severe",
                )
            )
        if question_mode == "diagnostic" and legacy_dimensions["reasoning_depth"]["score"] < 14:
            issues.append(cls._issue("shallow_reasoning", "原因分析缺少上游原因、下游影响、证据边界或验证路径。", "severe"))
        if next_actions and legacy_dimensions["actionability"]["score"] < 10:
            issues.append(cls._issue("non_actionable_answer", "回答缺少可执行的复核动作。", "moderate"))
        if unverified_threshold and legacy_dimensions["uncertainty_discipline"]["score"] < 10:
            issues.append(cls._issue("missing_threshold_limitation", "项目阈值未确认，但回答没有明确证据限制。", "severe"))
        if legacy_dimensions["concision"]["score"] < 6:
            issues.append(cls._issue("verbosity_or_filler", "回答过短、过长、重复或包含无效套话。", "moderate"))
        if response_plan.get("reasoning_mode") == "integrative_reasoning" and integration_depth["score"] < 8:
            issues.append(cls._issue("weak_integrative_reasoning", "复杂问题回答没有充分整合多模态证据。", "severe"))
        if response_plan.get("reasoning_mode") == "integrative_reasoning" and hypothesis_discrimination["score"] < 6:
            issues.append(cls._issue("missing_hypothesis_comparison", "复杂问题回答缺少竞争性假设比较或取舍。", "severe"))
        if question_core_coverage["score"] < 6:
            issues.append(cls._issue("question_core_not_covered", "回答没有充分覆盖用户问题的核心分析目标。", "moderate"))

        for item in fact_verification.get("issues", []) or []:
            issues.append(
                cls._issue(
                    str(item.get("rule") or "fact_verification_failure"),
                    str(item.get("text") or item.get("metric_id") or item.get("rule") or ""),
                    "severe" if item.get("severity") == "severe" else "moderate",
                )
            )
        needs_repair = score < cls.PASS_SCORE or any(item["severity"] == "severe" for item in issues)
        return {
            "passed": not needs_repair,
            "status": "pass" if not needs_repair else "repair_required",
            "score": score,
            "pass_score": cls.PASS_SCORE,
            "dimensions": dimensions,
            "legacy_dimensions": legacy_dimensions,
            "fact_verification": fact_verification,
            "issues": issues,
            "question_mode": question_mode,
            "target_metrics": sorted(target_metrics),
            "target_evidence_count": len(target_evidence),
            "scoring_evidence_count": len(scoring_evidence),
            "response_plan": response_plan,
            "overview_coverage": overview_coverage,
            "answer_chars": len(text),
            "repair_applied": False,
        }

    @classmethod
    def build_repair_answer(
        cls,
        *,
        analysis_result: dict[str, Any],
        quality: dict[str, Any] | None = None,
    ) -> str:
        validated_claims = [
            item
            for item in analysis_result.get("validated_claims", []) or []
            if isinstance(item, dict)
        ]
        if validated_claims:
            evidence_entries = cls._evidence_entries(analysis_result)
            rendered = claim_service.render_markdown(
                validated_claims=validated_claims,
                evidence_cards=[
                    item
                    for item in analysis_result.get("evidence_cards", []) or []
                    if isinstance(item, dict)
                ],
                target_metrics=cls._target_metrics(analysis_result, evidence_entries),
            )
            if rendered:
                return rendered
        diagnosis = analysis_result.get("diagnosis_summary") or {}
        evidence = cls._evidence_entries(analysis_result)
        target_metrics = set((quality or {}).get("target_metrics") or [])
        if not target_metrics:
            target_metrics = cls._target_metrics(analysis_result, evidence)
        target_evidence = [
            item
            for item in evidence
            if not target_metrics or str(item.get("metric_key") or "") in target_metrics
        ]
        if not target_metrics:
            target_evidence = evidence
        target_evidence = cls._localized_evidence(target_evidence, analysis_result)
        conclusions = cls._list_values(diagnosis.get("conclusions"))
        possible_causes = cls._list_values(diagnosis.get("possible_causes"))
        ranked_causes = [
            item for item in (diagnosis.get("ranked_causes") or []) if isinstance(item, dict)
        ]
        analysis_limits = cls._list_values(analysis_result.get("analysis_limits"))
        next_actions = cls._list_values(analysis_result.get("next_actions") or diagnosis.get("next_actions"))

        lines = [cls._direct_answer(target_evidence, conclusions, target_metrics)]
        evidence_lines = cls._repair_evidence_lines(target_evidence)
        if evidence_lines:
            lines.extend(["", "## 项目证据"])
            lines.extend(f"- {item}" for item in evidence_lines)

        reasoning_lines = cls._ranked_cause_lines(ranked_causes)
        if not reasoning_lines:
            reasoning_lines = cls._repair_reasoning_lines(target_evidence, possible_causes)
        if reasoning_lines:
            lines.extend(["", "## 根因排序" if ranked_causes else "## 原因与影响"])
            lines.extend(f"- {item}" for item in reasoning_lines)

        limit_lines = cls._dedupe(analysis_limits + cls._threshold_limit_lines(target_evidence))[:3]
        if limit_lines:
            lines.extend(["", "## 证据边界"])
            lines.extend(f"- {item}" for item in limit_lines)

        if next_actions:
            lines.extend(["", "## 优先复核"])
            lines.extend(f"- {item}" for item in next_actions[:4])
        return "\n".join(lines).strip()

    @classmethod
    def build_controlled_answer(
        cls,
        *,
        analysis_result: dict[str, Any],
        quality: dict[str, Any] | None = None,
    ) -> str:
        evidence = cls._evidence_entries(analysis_result)
        targets = set((quality or {}).get("target_metrics") or [])
        if not targets:
            targets = cls._target_metrics(analysis_result, evidence)
        target_evidence = [
            item
            for item in evidence
            if not targets or str(item.get("metric_key") or "") in targets
        ]
        labels = "、".join(cls.METRIC_LABELS.get(item, item) for item in sorted(targets))
        overview_mode = not targets
        subject = labels or "当前项目概览"
        lines = [
            "## 直接结论",
            (
                "- 当前只报告项目中可追溯的关键观测和证据缺口；"
                "没有项目专属阈值支持时，不做整体高低或合格性判断。"
                if overview_mode
                else (
                    f"- 当前只能对 {subject} 给出受证据约束的结果，不能用无关指标替代，"
                    "也不能在证据未通过校验时下确定性结论。"
                )
            ),
            "",
            "## 项目证据",
        ]
        if target_evidence:
            lines.extend(f"- {item}" for item in cls._repair_evidence_lines(target_evidence))
        else:
            lines.append(f"- 项目中未读取到 {subject} 的可验证结构化数值，需要补充对应结果文件或字段。")
        lines.extend(
            [
                "",
                "## 原因与影响",
                (
                    "- 可能原因/解释：当前项目观测需要按数据阶段、样本角色和来源口径分别解释。"
                    if overview_mode
                    else "- 可能原因/解释：目标证据缺失、来源口径不一致，或数值与分子分母复算未通过。"
                ),
                "- 可能下游影响：若直接采用未校验结果，会把测量口径差异误判为生物学异常，并影响后续 QC、富集或样本比较。",
                "- 当前证据只支持列出已读取事实和缺口，不支持确定性异常判定。",
                "",
                "## 证据边界",
                "- 项目文件中未确认适用阈值或目标 Claim 未通过完整校验时，只报告观测和缺失证据。",
                "",
                "## 优先复核",
                (
                    "- 按 QC、比对、富集和样本一致性顺序复核来源文件、处理阶段及样本角色。"
                    if overview_mode
                    else f"- 检查 {subject} 的来源文件、来源字段、处理阶段、分母口径及分子分母复算结果。"
                ),
            ]
        )
        return "\n".join(lines).strip()

    @staticmethod
    def _professional_dimensions(
        *,
        fact_verification: dict[str, Any],
        legacy_dimensions: dict[str, dict[str, Any]],
        analysis_result: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        issues = fact_verification.get("issues", []) or []
        severe = sum(1 for item in issues if item.get("severity") == "severe")
        moderate = len(issues) - severe
        fact_score = max(0, 30 - severe * 12 - moderate * 4)
        has_project_evidence = bool(
            analysis_result.get("evidence_cards")
            or analysis_result.get("evidence_chain")
        )
        if (
            has_project_evidence
            and int(fact_verification.get("checked_numeric_claim_count", 0) or 0) == 0
        ):
            fact_score = min(fact_score, 10)

        legacy_evidence = legacy_dimensions.get("evidence_grounding", {})
        evidence_score = round(
            20
            * float(legacy_evidence.get("score", 0))
            / max(1, float(legacy_evidence.get("max_score", 25)))
        )
        if not has_project_evidence:
            evidence_score = min(evidence_score, 8)
        unsupported_rate = float(
            fact_verification.get("unsupported_numeric_claim_rate", 0.0) or 0.0
        )
        unsupported_score = max(
            0,
            round(
                15 * (1.0 - unsupported_rate)
                - 5 * int(fact_verification.get("causal_overstatement_count", 0) or 0)
            ),
        )
        unit_errors = int(fact_verification.get("unit_error_count", 0) or 0)
        unit_score = max(0, 15 - unit_errors * 8)
        design_errors = int(
            fact_verification.get("sample_role_conflict_count", 0) or 0
        ) + int(
            fact_verification.get("differential_precondition_error_count", 0) or 0
        )
        design_score = max(0, 10 - design_errors * 5)
        causal_score = max(
            0,
            5
            - 5
            * int(fact_verification.get("causal_overstatement_count", 0) or 0),
        )

        response_plan = (
            (analysis_result.get("analysis_plan") or {}).get("response_plan") or {}
        )
        expected_complexity = response_plan.get("complexity", "focused")
        depth = legacy_dimensions.get("reasoning_depth", {})
        directness = legacy_dimensions.get("directness", {})
        concision = legacy_dimensions.get("concision", {})
        complexity_ratio = (
            float(depth.get("score", 0)) / max(1, float(depth.get("max_score", 20)))
            + float(directness.get("score", 0))
            / max(1, float(directness.get("max_score", 20)))
            + float(concision.get("score", 0))
            / max(1, float(concision.get("max_score", 10)))
        ) / 3
        complexity_score = min(5, round(5 * complexity_ratio))

        return {
            "fact_correctness": {
                "score": fact_score,
                "max_score": 30,
                "details": [f"severe={severe}", f"moderate={moderate}"],
            },
            "evidence_coverage": {
                "score": evidence_score,
                "max_score": 20,
                "details": legacy_evidence.get("details", []),
            },
            "unsupported_conclusion_control": {
                "score": unsupported_score,
                "max_score": 15,
                "details": [f"unsupported_numeric_claim_rate={unsupported_rate:.4f}"],
            },
            "unit_accuracy": {
                "score": unit_score,
                "max_score": 15,
                "details": [f"unit_error_count={unit_errors}"],
            },
            "experimental_design_discipline": {
                "score": design_score,
                "max_score": 10,
                "details": [f"design_error_count={design_errors}"],
            },
            "causal_discipline": {
                "score": causal_score,
                "max_score": 5,
                "details": [
                    "causal_overstatement_count="
                    f"{fact_verification.get('causal_overstatement_count', 0)}"
                ],
            },
            "complexity_fit": {
                "score": complexity_score,
                "max_score": 5,
                "details": [f"expected_complexity={expected_complexity}"],
            },
        }

    @classmethod
    def _score_integration_depth(
        cls,
        text: str,
        analysis_result: dict[str, Any],
    ) -> dict[str, Any]:
        response_plan = (analysis_result.get("analysis_plan") or {}).get("response_plan") or {}
        if response_plan.get("reasoning_mode") != "integrative_reasoning":
            return {"score": 10, "max_score": 10, "details": ["not_integrative_mode"]}
        expected = list(response_plan.get("expected_evidence_modalities") or [])
        modality_terms = {
            "frip": ("frip",),
            "peak_count": ("peak", "峰数量"),
            "correlation": ("correlation", "spearman", "相关性"),
            "motif": ("motif", "基序"),
            "spikein": ("spike-in", "spikein"),
            "alignment": ("mapping", "unique", "比对率", "唯一比对"),
            "experiment_design": ("对照", "重复", "condition", "batch"),
        }
        lowered = text.lower()
        covered = 0
        for modality in expected:
            if any(term in lowered for term in modality_terms.get(modality, ())):
                covered += 1
        score = min(10, 2 + covered * 2)
        return {
            "score": score,
            "max_score": 10,
            "details": [f"covered_modalities={covered}/{len(expected)}"],
        }

    @classmethod
    def _score_hypothesis_discrimination(
        cls,
        text: str,
        analysis_result: dict[str, Any],
    ) -> dict[str, Any]:
        response_plan = (analysis_result.get("analysis_plan") or {}).get("response_plan") or {}
        if response_plan.get("reasoning_mode") != "integrative_reasoning":
            return {"score": 10, "max_score": 10, "details": ["not_integrative_mode"]}
        checks = (
            any(term in text for term in ("更支持", "优先支持", "不优先支持", "相比之下")),
            any(term in text for term in ("反证", "削弱", "不支持")),
            any(term in text for term in ("仍缺", "缺失证据", "还需验证")),
        )
        return {
            "score": sum(3 for matched in checks if matched) + (1 if checks[0] else 0),
            "max_score": 10,
            "details": [f"component_{index + 1}={matched}" for index, matched in enumerate(checks)],
        }

    @classmethod
    def _score_assay_specificity(
        cls,
        text: str,
        analysis_result: dict[str, Any],
    ) -> dict[str, Any]:
        assay_profile = analysis_result.get("assay_profile") or {}
        assay = str(assay_profile.get("assay") or "").lower()
        target_class = str(assay_profile.get("target_class") or "").lower()
        if not assay and not target_class:
            return {"score": 3, "max_score": 5, "details": ["assay_unknown"]}
        lowered = text.lower()
        matched = int(bool(assay and assay in lowered)) + int(bool(target_class and target_class in lowered))
        return {
            "score": min(5, 2 + matched * 2),
            "max_score": 5,
            "details": [f"assay={assay or '-'}", f"target_class={target_class or '-'}"],
        }

    @classmethod
    def _score_question_core_coverage(
        cls,
        text: str,
        analysis_result: dict[str, Any],
    ) -> dict[str, Any]:
        question = str(analysis_result.get("question") or "").lower()
        checks = (
            ("技术偏差" in question or "生物学差异" in question),
            ("矩阵" in question or "cross-frip" in question or "cross frip" in question),
            ("对照" in question or "igg" in question or "input" in question),
        )
        applicable = [
            any_term
            for any_term in checks
        ]
        match_results = (
            applicable[0] and any(term in text for term in ("技术偏差", "生物学差异")),
            applicable[1] and any(term in text.lower() for term in ("cross-frip", "cross frip", "交叉frip", "交叉 frip", "矩阵")),
            applicable[2] and any(term in text.lower() for term in ("对照", "igg", "input")),
        )
        if not any(applicable):
            return {"score": 8, "max_score": 10, "details": ["no_special_core_constraint"]}
        score = 4 + sum(2 for matched in match_results if matched)
        return {
            "score": min(10, score),
            "max_score": 10,
            "details": [f"component_{index + 1}={matched}" for index, matched in enumerate(match_results)],
        }

    @classmethod
    def _score_matrix_reasoning(
        cls,
        text: str,
        analysis_result: dict[str, Any],
    ) -> dict[str, Any]:
        relationships = [
            item
            for item in ((analysis_result.get("evidence_reasoning") or {}).get("derived_relationships") or [])
            if isinstance(item, dict)
        ]
        matrix_related = any(
            item.get("relationship")
            in {
                "directional_frip_asymmetry",
                "cross_frip_vs_correlation_consistency",
                "frip_correlation_evidence_alignment",
            }
            for item in relationships
        )
        if not matrix_related:
            return {"score": 5, "max_score": 10, "details": ["no_matrix_relationships"]}
        checks = (
            any(term in text for term in ("方向", "不对称", "asymmetry", "peak set")),
            any(term in text.lower() for term in ("cross-frip", "cross frip", "交叉frip", "交叉 frip")),
            any(term in text for term in ("相关性", "Spearman", "correlation")),
        )
        return {
            "score": 4 + sum(2 for matched in checks if matched),
            "max_score": 10,
            "details": [f"component_{index + 1}={matched}" for index, matched in enumerate(checks)],
        }

    @classmethod
    def _score_directness(
        cls,
        text: str,
        target_metrics: set[str],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        score = 4 if text else 0
        details: list[str] = []
        terms = cls._target_terms(target_metrics, evidence)
        if not terms or any(term in text.lower() for term in terms):
            score += 8
            details.append("target_metric_present")
        values = cls._value_tokens(evidence)
        if not values or any(value in text[:260] for value in values):
            score += 4
            details.append("direct_value_or_finding")
        if not any(phrase in text[:260] for phrase in cls.GENERIC_FILLER):
            score += 4
            details.append("no_generic_opening")
        return {"score": score, "max_score": 20, "details": details}

    @classmethod
    def _score_evidence(cls, text: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        if not evidence:
            transparent = any(marker in text for marker in ("证据不足", "未读取到", "需要补充"))
            return {"score": 12 if transparent else 6, "max_score": 25, "details": ["no_structured_evidence"]}
        values = cls._value_tokens(evidence)
        matched_values = sum(1 for value in values if value in text)
        score = round(14 * min(1.0, matched_values / max(1, len(values))))
        sources = cls._source_tokens(evidence)
        matched_sources = [token for token in sources if token.lower() in text.lower()]
        if matched_sources:
            score += 7
        if any(marker in text for marker in ("项目证据", "来源", "字段", "观测值", "样本")):
            score += 4
        return {
            "score": min(25, score),
            "max_score": 25,
            "details": [f"value_coverage={matched_values}/{len(values)}", f"source_mentions={len(matched_sources)}"],
        }

    @classmethod
    def _score_depth(cls, text: str, question_mode: str) -> dict[str, Any]:
        if question_mode == "definition":
            checks = (
                any(term in text for term in ("表示", "定义", "衡量", "比例")),
                any(term in text for term in ("公式", "计算", "分母", "口径", "未确认")),
                any(term in text for term in ("项目", "样本", "来源", "字段")),
                any(term in text for term in cls.ACTION_MARKERS),
            )
        elif question_mode == "overview":
            checks = (
                any(term in text for term in ("关键观测", "项目证据", "证据依据", "样本")),
                any(term in text for term in ("来源", "字段", "报告")),
                any(term in text for term in ("证据限制", "未确认", "不能确定", "阈值")),
                any(term in text for term in cls.ACTION_MARKERS),
            )
        else:
            checks = (
                any(term in text for term in cls.CAUSE_MARKERS),
                any(term in text for term in cls.IMPACT_MARKERS),
                any(term in text for term in cls.SUPPORT_MARKERS),
                any(term in text for term in cls.ACTION_MARKERS),
            )
        return {
            "score": sum(5 for matched in checks if matched),
            "max_score": 20,
            "details": [f"component_{index + 1}={matched}" for index, matched in enumerate(checks)],
        }

    @classmethod
    def _score_actionability(cls, text: str, next_actions: list[str]) -> dict[str, Any]:
        if not next_actions:
            return {"score": 15, "max_score": 15, "details": ["no_required_actions"]}
        marker_count = sum(1 for marker in cls.ACTION_MARKERS if marker in text)
        score = 8 if marker_count else 0
        action_terms = cls._action_terms(next_actions)
        if action_terms and any(term in text.lower() for term in action_terms):
            score += 7
        return {
            "score": score,
            "max_score": 15,
            "details": [f"action_markers={marker_count}", f"action_terms={len(action_terms)}"],
        }

    @classmethod
    def _score_uncertainty(cls, text: str, unverified: bool) -> dict[str, Any]:
        if not unverified:
            return {"score": 10, "max_score": 10, "details": ["threshold_verified_or_not_required"]}
        matched = [marker for marker in cls.LIMIT_MARKERS if marker in text]
        return {"score": 10 if matched else 0, "max_score": 10, "details": matched}

    @classmethod
    def _score_concision(cls, text: str) -> dict[str, Any]:
        chars = len(text)
        score = 6 if 80 <= chars <= 4500 else (3 if 40 <= chars <= 6500 else 0)
        if not any(phrase in text for phrase in cls.GENERIC_FILLER):
            score += 2
        headings = len(re.findall(r"(?m)^#{1,6}\s+", text))
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
        if headings <= 5 and len(paragraphs) == len(set(paragraphs)):
            score += 2
        return {"score": score, "max_score": 10, "details": [f"chars={chars}", f"headings={headings}"]}

    @classmethod
    def _direct_answer(
        cls,
        evidence: list[dict[str, Any]],
        conclusions: list[str],
        target_metrics: set[str] | None = None,
    ) -> str:
        if evidence:
            verified = [
                item
                for item in evidence
                if item.get("severity") in {"warning", "critical"}
                and not item.get("threshold_needs_project_validation")
            ]
            observations = "；".join(cls._compact_observation(item) for item in evidence[:3])
            if verified:
                return f"项目数据支持优先复核以下指标：{observations}。"
            return (
                f"项目已读取到相关观测值：{observations}。"
                "项目文件中未确认这些指标的专属阈值，因此当前只能定位证据和排查方向，不能单独判定高低或异常。"
            )
        if conclusions and not target_metrics:
            return conclusions[0]
        if target_metrics:
            labels = "、".join(
                cls.METRIC_LABELS.get(metric, metric)
                for metric in sorted(target_metrics)
            )
            return (
                f"当前项目未形成 {labels} 的可验证观测证据，不能使用其他指标替代回答。"
                "需要先补充对应样本、结果字段和计算口径。"
            )
        return "当前项目证据不足以直接回答该问题，需要先补充对应指标、样本或流程结果。"

    @classmethod
    def _repair_evidence_lines(cls, evidence: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for item in evidence[:4]:
            source_file = str(item.get("source_file") or "-").strip()
            source_field = str(item.get("source_field") or item.get("metric_key") or "-").strip()
            line = f"{cls._compact_observation(item)}；来源 {source_file}::{source_field}"
            rule = str(item.get("rule") or "").strip()
            if rule and not item.get("threshold_needs_project_validation"):
                line += f"；项目规则 {rule}"
            else:
                line += "；项目文件中未确认该指标阈值/标准"
            lines.append(line)
        return cls._dedupe(lines)

    @classmethod
    def _repair_reasoning_lines(
        cls,
        evidence: list[dict[str, Any]],
        possible_causes: list[str],
    ) -> list[str]:
        lines = [f"可能原因/解释：{item}" for item in possible_causes]
        for item in evidence:
            interpretation = str(item.get("interpretation") or "").strip()
            impact = str(item.get("downstream_impact") or "").strip()
            if interpretation:
                lines.append(f"可能原因/解释：{interpretation}")
            if impact:
                lines.append(f"可能下游影响：{impact}")
        if evidence:
            lines.append("当前证据支持上述观测值及其数据口径；缺少项目专属阈值或补充质控证据时，不支持进一步做确定性异常判定。")
        return cls._dedupe(lines)[:4]

    @classmethod
    def _ranked_cause_lines(cls, ranked_causes: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for cause in ranked_causes[:3]:
            label = str(cause.get("label") or cause.get("cause_id") or "候选原因").strip()
            lines.append(
                f"候选原因 #{cause.get('rank', '-')} {label}：{cause.get('reasoning_summary', '')}"
            )
            supporting = cause.get("supporting_evidence") or []
            if supporting and isinstance(supporting[0], dict):
                item = supporting[0]
                lines.append(
                    f"支持证据：{item.get('sample', '-')} {item.get('metric_key', '-')}="
                    f"{item.get('value', '-')}；{item.get('reason', '')}"
                )
            contradicting = cause.get("contradicting_evidence") or []
            if contradicting and isinstance(contradicting[0], dict):
                item = contradicting[0]
                lines.append(
                    f"反证：{item.get('sample', '-')} {item.get('metric_key', '-')}="
                    f"{item.get('value', '-')}；{item.get('reason', '')}"
                )
            else:
                lines.append("反证：当前未发现可独立排除该假设的项目证据。")
            impacts = cls._list_values(cause.get("downstream_impacts"))
            if impacts:
                lines.append(f"可能下游影响：{impacts[0]}")
            actions = cls._list_values(cause.get("verification_actions"))
            if actions:
                lines.append(f"验证动作：{actions[0]}")
            missing = cls._list_values(cause.get("missing_evidence"))
            if missing:
                lines.append(f"缺失证据：{missing[0]}")
            outcomes = cls._list_values(cause.get("expected_validation_outcomes"))
            if outcomes:
                lines.append(f"预期验证结果：{outcomes[0]}")
        return cls._dedupe(lines)[:12]

    @staticmethod
    def _localized_evidence(
        evidence: list[dict[str, Any]],
        analysis_result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        config = (analysis_result.get("project_context") or {}).get("config") or {}
        species = str(
            config.get("species") or config.get("genome") or config.get("reference") or ""
        ).lower()
        plant_tokens = ("tair", "arabidopsis", "oryza", "rice", "zea", "maize", "plant")
        animal_tokens = ("hg", "grch", "human", "mm", "grcm", "mouse", "rn", "rat")
        localized: list[dict[str, Any]] = []
        for raw_item in evidence:
            item = dict(raw_item)
            if str(item.get("metric_key") or "") == "mt_rate_percent":
                if any(token in species for token in plant_tokens):
                    item["metric"] = "线粒体/叶绿体 reads 比例"
                elif any(token in species for token in animal_tokens):
                    item["metric"] = "线粒体 reads 比例"
                else:
                    item["metric"] = "细胞器 reads 比例"
            localized.append(item)
        return localized

    @classmethod
    def _threshold_limit_lines(cls, evidence: list[dict[str, Any]]) -> list[str]:
        metrics = [
            str(item.get("metric") or item.get("metric_key") or "").strip()
            for item in evidence
            if (
                item.get("threshold_needs_project_validation")
                or item.get("severity") == "unverified_threshold"
            )
        ]
        metrics = cls._dedupe([item for item in metrics if item])
        if not metrics:
            return []
        return [
            f"项目文件中未确认 {', '.join(metrics)} 的专属阈值；本轮只报告观测值，不据此判定偏高、偏低或异常。"
        ]

    @classmethod
    def _target_metrics(
        cls,
        analysis_result: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> set[str]:
        question = str(analysis_result.get("question") or "").lower()
        inferred = {
            metric_key
            for metric_key, aliases in cls.METRIC_ALIASES.items()
            if any(alias.lower() in question for alias in (metric_key, *aliases))
        }
        if len(inferred) == 1:
            return inferred

        analysis_plan = analysis_result.get("analysis_plan") or {}
        raw_targets = list(analysis_plan.get("target_metrics") or [])
        targets = {
            cls.TARGET_ALIASES.get(str(item).strip().lower(), str(item).strip().lower())
            for item in raw_targets
            if str(item).strip()
        }
        targets = {item for item in targets if item in cls.METRIC_LABELS}
        if targets:
            return targets
        return {item for item in inferred if item in cls.METRIC_LABELS}

    @staticmethod
    def _question_mode(
        analysis_result: dict[str, Any],
        question_route: Any,
    ) -> str:
        question = str(analysis_result.get("question") or "").lower()
        route = (
            question_route.get("route")
            if isinstance(question_route, dict)
            else getattr(question_route, "route", "")
        )
        question_type = str(analysis_result.get("question_type") or "").strip().lower()
        report_mode = str(analysis_result.get("report_mode") or "").strip().lower()
        if (
            str(route or "") in {"ai_report_summary", "project_compare"}
            or question_type == "overview"
            or report_mode == "existing_html_report_summary"
        ):
            return "overview"
        if any(term in question for term in ("是什么", "什么意思", "公式", "怎么计算", "如何计算", "定义")):
            return "definition"
        if any(term in question for term in ("为什么", "原因", "异常", "问题", "排查", "影响", "偏高", "偏低")):
            return "diagnostic"
        return "analysis"

    @classmethod
    def _target_terms(cls, targets: set[str], evidence: list[dict[str, Any]]) -> set[str]:
        terms: set[str] = set()
        for metric_key in targets:
            terms.add(metric_key.lower())
            terms.update(alias.lower() for alias in cls.METRIC_ALIASES.get(metric_key, ()))
        for item in evidence:
            metric = str(item.get("metric") or "").strip().lower()
            if metric:
                terms.add(metric)
        return terms

    @classmethod
    def _overview_evidence_entries(
        cls,
        evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        metric_order = (
            "mapping_rate_percent",
            "unique_mapping_rate_percent",
            "mt_rate_percent",
            "frip_ratio",
            "correlation",
            "duplicate_rate_percent",
            "adapter_percent",
            "q30_ratio",
            "peak_count",
            "tss_enrichment",
            "fragment_size",
            "spikein_mapped_reads",
            "control_binding_status",
        )
        first_by_metric: dict[str, dict[str, Any]] = {}
        for item in evidence:
            metric_key = str(item.get("metric_key") or "").strip()
            if metric_key and metric_key not in first_by_metric:
                first_by_metric[metric_key] = item
        ordered_metrics = [
            *[metric for metric in metric_order if metric in first_by_metric],
            *[metric for metric in first_by_metric if metric not in metric_order],
        ]
        return [first_by_metric[metric] for metric in ordered_metrics[:8]]

    @classmethod
    def _overview_metric_coverage(
        cls,
        text: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        lowered = text.lower()
        covered: list[str] = []
        available: list[str] = []
        for item in evidence:
            metric_key = str(item.get("metric_key") or "").strip()
            if not metric_key or metric_key in available:
                continue
            available.append(metric_key)
            terms = {
                metric_key.lower(),
                str(item.get("metric") or "").strip().lower(),
                str(cls.METRIC_LABELS.get(metric_key) or "").strip().lower(),
                *(alias.lower() for alias in cls.METRIC_ALIASES.get(metric_key, ())),
            }
            terms.discard("")
            values = cls._value_tokens([item])
            if any(term in lowered for term in terms) and any(value in text for value in values):
                covered.append(metric_key)
        required_count = min(4, len(available))
        return {
            "available_metric_count": len(available),
            "required_metric_count": required_count,
            "covered_metric_count": len(covered),
            "covered_metrics": covered,
        }

    @classmethod
    def _value_tokens(cls, evidence: list[dict[str, Any]]) -> list[str]:
        tokens: list[str] = []
        for item in evidence[:6]:
            display = str(item.get("display_value") or "").strip()
            if display and display != "-":
                tokens.append(display)
                if display.endswith("%"):
                    tokens.append(display.rstrip("%").rstrip("0").rstrip(".") + "%")
        return cls._dedupe(tokens)

    @staticmethod
    def _source_tokens(evidence: list[dict[str, Any]]) -> list[str]:
        tokens: list[str] = []
        for item in evidence[:6]:
            for key in ("source_file", "source_field"):
                value = str(item.get(key) or "").strip()
                if value and value != "-":
                    tokens.append(value)
        return list(dict.fromkeys(tokens))

    @classmethod
    def _action_terms(cls, actions: list[str]) -> set[str]:
        terms: set[str] = set()
        for action in actions[:4]:
            lowered = action.lower()
            for aliases in cls.METRIC_ALIASES.values():
                for alias in aliases:
                    if alias.lower() in lowered:
                        terms.add(alias.lower())
            terms.update(re.findall(r"[a-z][a-z0-9_.-]{2,}", lowered))
        return terms

    @staticmethod
    def _compact_observation(item: dict[str, Any]) -> str:
        sample = str(item.get("sample") or "-").strip()
        metric = str(item.get("metric") or item.get("metric_key") or "指标").strip()
        value = str(item.get("display_value") or item.get("value") or "-").strip()
        return f"{sample} {metric}={value}"

    @staticmethod
    def _evidence_entries(analysis_result: dict[str, Any]) -> list[dict[str, Any]]:
        placeholders = {"", "-", "--", "na", "n/a", "nan", "none", "null"}
        return [
            item
            for item in (analysis_result.get("evidence_chain") or [])
            if isinstance(item, dict)
            and not (
                item.get("value") is None
                and str(item.get("display_value") or "").strip().lower() in placeholders
            )
        ]

    @staticmethod
    def _list_values(value: Any) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return re.sub(r"[ \t]+", " ", str(value or "")).strip()

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        return list(dict.fromkeys(item for item in values if item))

    @staticmethod
    def _issue(rule: str, message: str, severity: str) -> dict[str, str]:
        return {"rule": rule, "message": message, "severity": severity}


business_answer_quality_service = BusinessAnswerQualityService()
