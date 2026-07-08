from __future__ import annotations

import re
from typing import Any

from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)


class FactVerificationService:
    """Verify generated answers against canonical project evidence and design."""

    VERSION = "answer-fact-verifier-v2"
    METRIC_TERMS = {
        "frip_ratio": ("frip",),
        "mapping_rate_percent": ("mapping rate", "mapping", "比对率"),
        "unique_mapping_rate_percent": ("unique mapping", "unique", "唯一比对"),
        "duplicate_rate_percent": ("duplicate", "duplication", "重复率"),
        "mt_rate_percent": ("mtdna", "chrmt", "mitochond", "线粒体"),
        "nrf": ("nrf",),
        "pbc1": ("pbc1",),
        "pbc2": ("pbc2",),
        "spikein_unique_mapping_rate_percent": (
            "spike-in unique",
            "spikein unique",
        ),
        "spikein_scaling_factor": (
            "scaling factor",
            "scale factor",
            "归一化因子",
        ),
        "correlation": ("correlation", "spearman", "pearson", "相关性"),
    }
    NON_NUMERIC_FACT_METRICS = {"control_binding_status"}
    EXCLUDED_NUMERIC_VERIFICATION_METRICS = {
        "picard_duplicate_pair_rate_percent",
        "clean_read_retention_percent",
        "control_binding_status",
    }
    CAUSAL_CERTAINTY = (
        "证明了",
        "证实了",
        "根本原因是",
        "原因就是",
        "导致了",
        "由此可确定",
        "confirmed cause",
        "proves that",
        "caused by",
    )
    REFERENCE_NUMERIC_HINTS = (
        "通常",
        "一般",
        "可接受",
        "参考",
        "文献",
        "如果",
        "若",
        "应",
        "建议",
        "比如",
        "例如",
        "可能",
        "经验",
        "至少",
        "至少应",
        "不等于",
        "不能只靠",
        "不代表",
        "金标准",
    )
    DIFFERENTIAL_TERMS = (
        "差异分析",
        "差异 peak",
        "差异表达",
        "differential analysis",
        "deseq",
        "edger",
    )

    # ------------------------------------------------------------------
    # Packet-first entry points (Phase 1)
    # ------------------------------------------------------------------

    @classmethod
    def verify_fact_packet(
        cls,
        fact_packet: dict[str, Any],
        analysis_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify the structural integrity of a fact_packet.

        This check operates entirely on the structured packet — no text parsing.
        Checks:
          1. project_evidence traceability (source_file or source_field present)
          2. threshold_status field is populated
          3. Unit / scale consistency via metric_schema_service (strict metrics only)
          4. Sample role consistency from experiment_design
          5. Differential preconditions from experiment_design
        """
        issues: list[dict[str, Any]] = []

        # 1. traceability
        for ev in fact_packet.get("project_evidence") or []:
            if not isinstance(ev, dict):
                continue
            if not ev.get("source_file") and not ev.get("source_field"):
                issues.append(
                    {
                        "rule": "evidence_not_traceable",
                        "severity": "severe",
                        "metric_id": ev.get("metric_id"),
                        "sample": ev.get("sample"),
                    }
                )

        # 2. threshold_status
        ts = fact_packet.get("threshold_status")
        if not isinstance(ts, dict) or "has_unverified_thresholds" not in ts:
            issues.append(
                {
                    "rule": "missing_threshold_status_field",
                    "severity": "severe",
                    "text": "fact_packet 缺少 threshold_status 字段。",
                }
            )

        # 3. unit / scale consistency (strict metrics only)
        unit_errors = 0
        for ev in fact_packet.get("project_evidence") or []:
            if not isinstance(ev, dict):
                continue
            metric_id = metric_schema_service.canonical_id(ev.get("metric_id"))
            if metric_schema_service.verifier_contract(metric_id) != "strict_formula_recalculation":
                continue
            value = ev.get("value")
            if value is None:
                continue
            schema_check = metric_schema_service.normalize(
                metric_id,
                value,
                source_field="",
                numerator=ev.get("numerator_value"),
                denominator=ev.get("denominator_value"),
            )
            for issue in schema_check.get("issues") or []:
                unit_errors += 1
                issues.append(
                    {
                        **issue,
                        "severity": "severe",
                        "rule": issue.get("rule", "unit_error"),
                        "metric_id": metric_id,
                        "sample": ev.get("sample"),
                    }
                )
            # 2026-07-02 止血（truth_layer_recompute_generalization_plan.md §6/§9）：
            # 诚实失败信号放在这里判定，而不是 normalize() 内部——这里才是真正的
            # "验证"网关，其它调用 normalize() 的地方（字段发现/文件解析）另有用途，
            # 不应被这个判定连带影响。声称 strict_formula_recalculation 却没有可重算
            # 的分子/分母时，追加一条 warning：不计入 unit_errors / severe，不影响
            # verify_fact_packet 的 passed 判定，但让"引用了展示值、没真正重算过"这件事
            # 对下游可见，不再像改造前那样彻底沉默。
            # 2026-07-06 fact_packet 增强（任务②）：recompute_status 现在优先读
            # evidence_card_service.validate_cards() 已经算好并写在 card/project_evidence
            # 上的值，只有旧数据没有这个字段时才回退到这里现算，避免两处各算一遍、
            # 可能因为传参差异而互相不一致。
            recompute_status = ev.get("recompute_status") or schema_check.get("recompute_status")
            if recompute_status in ("inputs_missing", "not_applicable"):
                issues.append(
                    {
                        "rule": "recalculation_inputs_missing",
                        "severity": "warning",
                        "metric_id": metric_id,
                        "sample": ev.get("sample"),
                    }
                )

        # 4. sample role consistency
        design = analysis_result.get("experiment_design") or (
            analysis_result.get("project_context") or {}
        ).get("experiment_design") or {}
        role_issues = cls._packet_role_issues(fact_packet, design)
        issues.extend(role_issues)

        # 5. differential preconditions
        differential_issues = cls._packet_differential_issues(fact_packet, design)
        issues.extend(differential_issues)

        severe_count = sum(1 for item in issues if item.get("severity") == "severe")
        return {
            "version": cls.VERSION,
            "mode": "packet",
            "passed": severe_count == 0,
            "unit_error_count": unit_errors,
            "sample_role_conflict_count": len(role_issues),
            "differential_precondition_error_count": len(differential_issues),
            "issues": issues,
            "contracts": {
                "evidence_traceable": not any(
                    i.get("rule") == "evidence_not_traceable" for i in issues
                ),
                "threshold_status_present": not any(
                    i.get("rule") == "missing_threshold_status_field" for i in issues
                ),
                "units_and_denominators_correct": unit_errors == 0,
                "sample_roles_respected": not role_issues,
                "differential_analysis_preconditions_respected": not differential_issues,
            },
        }

    @classmethod
    def verify_render_alignment(
        cls,
        answer: str,
        fact_packet: dict[str, Any],
    ) -> dict[str, Any]:
        """Weak check: does the rendered answer cover key facts from the packet?

        This is intentionally lightweight — text is a view, not the source of
        truth.  We only flag clear omissions, not numeric mismatches.
        """
        text = str(answer or "")
        issues: list[dict[str, Any]] = []

        # Check threshold limitation disclosure
        ts = fact_packet.get("threshold_status") or {}
        if ts.get("has_unverified_thresholds") and not any(
            marker in text
            for marker in (
                "项目文件中未确认该指标阈值/标准",
                "项目文件中未确认",
                "项目阈值未确认",
                "只能报告观测值",
            )
        ):
            issues.append(
                {
                    "rule": "render_missing_threshold_limitation",
                    "severity": "severe",
                    "text": "fact_packet 有未验证阈值，但最终回答未声明 '只能报告观测值'。",
                }
            )

        # Check key evidence metrics are at least mentioned
        lowered = text.lower()
        for ev in fact_packet.get("project_evidence") or []:
            if not isinstance(ev, dict):
                continue
            metric_id = str(ev.get("metric_id") or "")
            terms = cls.METRIC_TERMS.get(metric_schema_service.canonical_id(metric_id), ())
            if not terms:
                continue
            if not any(term in lowered for term in terms):
                issues.append(
                    {
                        "rule": "render_missing_key_evidence",
                        "severity": "moderate",
                        "metric_id": metric_id,
                        "sample": ev.get("sample"),
                    }
                )

        severe_count = sum(1 for item in issues if item.get("severity") == "severe")
        return {
            "version": cls.VERSION,
            "mode": "render_alignment",
            "passed": severe_count == 0,
            "issues": issues,
            "contracts": {
                "threshold_limitations_present": not any(
                    i.get("rule") == "render_missing_threshold_limitation" for i in issues
                ),
                "critical_evidence_mentioned": not any(
                    i.get("severity") == "severe"
                    and i.get("rule") == "render_missing_key_evidence"
                    for i in issues
                ),
            },
        }

    @classmethod
    def _packet_role_issues(
        cls,
        fact_packet: dict[str, Any],
        design: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Check sample roles within fact_packet conclusions (no text parsing)."""
        issues: list[dict[str, Any]] = []
        role_map = {
            str(s.get("sample") or "").lower(): str(s.get("role") or "")
            for s in (design.get("samples") or [])
            if isinstance(s, dict) and s.get("sample")
        }
        for ev in fact_packet.get("project_evidence") or []:
            if not isinstance(ev, dict):
                continue
            sample = str(ev.get("sample") or "").strip()
            if not sample or sample == "-":
                continue
            expected_role = role_map.get(sample.lower())
            if expected_role and ev.get("assigned_role") and ev["assigned_role"] != expected_role:
                issues.append(
                    {
                        "rule": "sample_role_conflict",
                        "severity": "severe",
                        "sample": sample,
                        "expected_role": expected_role,
                        "actual_role": ev["assigned_role"],
                    }
                )
        return issues

    @classmethod
    def _packet_differential_issues(
        cls,
        fact_packet: dict[str, Any],
        design: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Check differential preconditions via fact_packet conclusions."""
        conclusions_text = " ".join(
            str(c.get("claim") or "") for c in (fact_packet.get("direct_conclusions") or [])
        ).lower()
        if not any(term in conclusions_text for term in cls.DIFFERENTIAL_TERMS):
            return []
        readiness = design.get("differential_analysis") or {}
        if readiness.get("ready"):
            return []
        uncertainty = any(
            term in conclusions_text
            for term in ("不能", "不建议", "缺少", "需要补充", "not ready", "cannot")
        )
        if uncertainty:
            return []
        return [
            {
                "rule": "differential_analysis_without_replicates_or_groups",
                "severity": "severe",
                "missing": readiness.get("reasons", []),
            }
        ]

    # ------------------------------------------------------------------
    # Legacy / compat entry point (packet-first from Phase 1 onwards)
    # ------------------------------------------------------------------

    @classmethod
    def verify(cls, *, answer: str, analysis_result: dict[str, Any]) -> dict[str, Any]:
        # Packet-first: if a fact_packet is available, use the structured path.
        fact_packet = analysis_result.get("fact_packet")
        if isinstance(fact_packet, dict) and fact_packet.get("project_evidence") is not None:
            packet_result = cls.verify_fact_packet(fact_packet, analysis_result)
            render_result = cls.verify_render_alignment(answer, fact_packet)
            # Merge results into a unified response
            all_issues = packet_result.get("issues", []) + render_result.get("issues", [])
            severe_count = sum(1 for i in all_issues if i.get("severity") == "severe")
            contracts = {**packet_result.get("contracts", {}), **render_result.get("contracts", {})}
            # Add legacy text-based checks for causal language and matrix reasoning
            text = str(answer or "")
            causal_issues = cls._causal_issues(text, analysis_result)
            matrix_issues = cls._matrix_reasoning_issues(text, analysis_result)
            modality_issues = cls._missing_modality_issues(text, analysis_result)
            all_issues.extend(causal_issues)
            all_issues.extend(matrix_issues)
            all_issues.extend(modality_issues)
            severe_count = sum(1 for i in all_issues if i.get("severity") == "severe")
            contracts["causal_language_calibrated"] = not causal_issues
            contracts["matrix_reasoning_consistent"] = not matrix_issues
            contracts["critical_modalities_covered"] = not modality_issues
            return {
                "version": cls.VERSION,
                "mode": "packet_first",
                "passed": severe_count == 0,
                "checked_numeric_claim_count": 0,
                "supported_numeric_claim_count": 0,
                "unsupported_numeric_claim_rate": 0.0,
                "unit_error_count": packet_result.get("unit_error_count", 0),
                "definition_error_count": 0,
                "sample_role_conflict_count": packet_result.get("sample_role_conflict_count", 0),
                "differential_precondition_error_count": packet_result.get(
                    "differential_precondition_error_count", 0
                ),
                "causal_overstatement_count": len(causal_issues),
                "threshold_limit_error_count": sum(
                    1 for i in all_issues if i.get("rule") == "render_missing_threshold_limitation"
                ),
                "matrix_reasoning_error_count": len(matrix_issues),
                "critical_modality_omission_count": len(modality_issues),
                "issues": all_issues,
                "contracts": contracts,
            }

        # Fallback: legacy text-based verification
        text = str(answer or "")
        fact_text = cls._fact_layer_text(text)
        evidence = [
            item
            for item in analysis_result.get("evidence_cards", []) or []
            if isinstance(item, dict)
        ]
        if not evidence:
            evidence = [
                {
                    **item,
                    "metric_id": item.get("metric_id") or item.get("metric_key"),
                    "value_scale": item.get("value_scale")
                    or (
                        "fraction"
                        if item.get("metric_key") == "frip_ratio"
                        else "percent"
                        if str(item.get("display_value") or "").endswith("%")
                        else ""
                    ),
                }
                for item in analysis_result.get("evidence_chain", []) or []
                if isinstance(item, dict)
            ]
        design = analysis_result.get("experiment_design") or (
            analysis_result.get("project_context") or {}
        ).get("experiment_design") or {}
        issues: list[dict[str, Any]] = []
        checked_claims = 0
        supported_claims = 0
        unit_errors = 0

        evidence_by_metric: dict[str, list[dict[str, Any]]] = {}
        for card in evidence:
            metric_id = metric_schema_service.canonical_id(card.get("metric_id"))
            evidence_by_metric.setdefault(metric_id, []).append(card)

        answer_mentions_threshold_limit = any(
            marker in text
            for marker in (
                "项目文件中未确认该指标阈值/标准",
                "项目文件中未确认",
                "项目阈值未确认",
                "只能报告观测值",
            )
        )

        question_mode = str(analysis_result.get("question_type") or "").strip().lower()
        for sentence in cls._sentences(fact_text):
            lowered = sentence.lower()
            metric_ids = [
                metric_id
                for metric_id, terms in cls.METRIC_TERMS.items()
                if any(term in lowered for term in terms)
            ]
            metric_ids = [
                metric_id
                for metric_id in metric_ids
                if metric_id not in cls.EXCLUDED_NUMERIC_VERIFICATION_METRICS
                and metric_schema_service.verifier_contract(metric_id)
                not in {"non_numeric_design_status", "citation_only", "display_value_only"}
            ]
            numbers = cls._numbers(sentence)
            if not metric_ids or not numbers:
                continue
            if question_mode == "definition":
                continue
            if cls._is_reference_or_conditional_numeric_sentence(sentence):
                continue
            if any(
                marker in lowered
                for marker in ("计算口径", "公式", "formula", "definition")
            ) or "÷" in sentence:
                continue
            checked_claims += 1
            if any(
                cls._matches_any_value(sentence, numbers, evidence_by_metric.get(metric_id, []))
                for metric_id in metric_ids
            ):
                supported_claims += 1
            else:
                issues.append(
                    {
                        "rule": "numeric_claim_not_found_in_project_evidence",
                        "severity": "severe",
                        "text": sentence[:300],
                        "metrics": metric_ids,
                        "numbers": numbers,
                    }
                )

        for metric_id, cards in evidence_by_metric.items():
            if metric_id in cls.EXCLUDED_NUMERIC_VERIFICATION_METRICS:
                continue
            if metric_schema_service.verifier_contract(metric_id) != "strict_formula_recalculation":
                continue
            for card in cards:
                if card.get("value") is None:
                    continue
                if not card.get("value_scale") and not card.get("schema_version"):
                    # Legacy evidence does not declare its canonical scale.
                    # It can support answer matching but must not be reinterpreted.
                    continue
                # Evidence cards already contain canonical values. Do not reapply
                # source-field scale conversion to a normalized value.
                schema_check = metric_schema_service.normalize(
                    metric_id,
                    card.get("value"),
                    source_field="",
                    numerator=card.get("numerator_value"),
                    denominator=card.get("denominator_value"),
                )
                for issue in schema_check.get("issues", []):
                    unit_errors += 1
                    issues.append(
                        {
                            **issue,
                            "severity": "severe",
                            "metric_id": metric_id,
                            "sample": card.get("sample"),
                            "source": (
                                f"{card.get('source_file', '')}::"
                                f"{card.get('source_field', '')}"
                            ),
                        }
                    )
                # 2026-07-02 止血：同 verify_fact_packet，诚实失败信号只在这里（真正的
                # 验证网关）判定，warning 级别，不计入 unit_errors。
                # 2026-07-06 fact_packet 增强（任务②）：同上，优先读 card 上已经算好的
                # recompute_status，没有才回退现算。
                recompute_status = card.get("recompute_status") or schema_check.get("recompute_status")
                if recompute_status in ("inputs_missing", "not_applicable"):
                    issues.append(
                        {
                            "rule": "recalculation_inputs_missing",
                            "severity": "warning",
                            "metric_id": metric_id,
                            "sample": card.get("sample"),
                            "source": (
                                f"{card.get('source_file', '')}::"
                                f"{card.get('source_field', '')}"
                            ),
                        }
                    )

        definition_issues = cls._definition_issues(fact_text)
        role_issues = cls._role_issues(fact_text, design)
        differential_issues = cls._differential_issues(fact_text, design)
        causal_issues = cls._causal_issues(text, analysis_result)
        threshold_issues = [] if answer_mentions_threshold_limit else cls._threshold_limit_issues(analysis_result)
        matrix_issues = cls._matrix_reasoning_issues(text, analysis_result)
        modality_issues = cls._missing_modality_issues(text, analysis_result)
        issues.extend(definition_issues)
        issues.extend(role_issues)
        issues.extend(differential_issues)
        issues.extend(causal_issues)
        issues.extend(threshold_issues)
        issues.extend(matrix_issues)
        issues.extend(modality_issues)

        unsupported_rate = (
            (checked_claims - supported_claims) / checked_claims
            if checked_claims
            else 0.0
        )
        severe_count = sum(1 for item in issues if item.get("severity") == "severe")
        return {
            "version": cls.VERSION,
            "passed": severe_count == 0,
            "checked_numeric_claim_count": checked_claims,
            "supported_numeric_claim_count": supported_claims,
            "unsupported_numeric_claim_rate": round(unsupported_rate, 4),
            "unit_error_count": unit_errors,
            "definition_error_count": len(definition_issues),
            "sample_role_conflict_count": len(role_issues),
            "differential_precondition_error_count": len(differential_issues),
            "causal_overstatement_count": len(causal_issues),
            "threshold_limit_error_count": len(threshold_issues),
            "matrix_reasoning_error_count": len(matrix_issues),
            "critical_modality_omission_count": len(modality_issues),
            "issues": issues,
            "contracts": {
                "numbers_from_project_files": not any(
                    item.get("rule") == "numeric_claim_not_found_in_project_evidence"
                    for item in issues
                ),
                "units_and_denominators_correct": unit_errors == 0,
                "metric_definitions_correct": not definition_issues,
                "sample_roles_respected": not role_issues,
                "differential_analysis_preconditions_respected": not differential_issues,
                "causal_language_calibrated": not causal_issues,
                "threshold_limitations_present": not threshold_issues,
                "matrix_reasoning_consistent": not matrix_issues,
                "critical_modalities_covered": not modality_issues,
            },
        }

    @classmethod
    def _threshold_limit_issues(
        cls,
        analysis_result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        evidence = [
            item
            for item in (analysis_result.get("evidence_chain") or [])
            if isinstance(item, dict)
        ]
        if not any(
            item.get("threshold_needs_project_validation")
            or item.get("severity") == "unverified_threshold"
            for item in evidence
        ):
            return []
        return [
            {
                "rule": "missing_threshold_limitation",
                "severity": "severe",
                "text": "项目存在未验证阈值，但回答没有明确声明只能报告观测值。",
            }
        ]

    @classmethod
    def _matrix_reasoning_issues(
        cls,
        text: str,
        analysis_result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        lowered = text.lower()
        relationships = [
            item
            for item in ((analysis_result.get("evidence_reasoning") or {}).get("derived_relationships") or [])
            if isinstance(item, dict)
        ]
        issues: list[dict[str, Any]] = []
        asymmetry = [
            item for item in relationships if item.get("relationship") == "directional_frip_asymmetry"
        ]
        if asymmetry and "对称" in lowered and not any(term in lowered for term in ("不对称", "asymmetry", "方向性")):
            issues.append(
                {
                    "rule": "cross_frip_directionality_mismatch",
                    "severity": "severe",
                    "text": "回答把具有方向性的 cross-FRiP 关系解释成完全对称。",
                }
            )
        if any(item.get("relationship") == "cross_frip_vs_correlation_consistency" for item in relationships):
            if "相关性" in text and "cross-frip" not in lowered and "cross frip" not in lowered and "交叉frip" not in lowered and "交叉 frip" not in lowered:
                issues.append(
                    {
                        "rule": "matrix_reasoning_missing_cross_frip_context",
                        "severity": "moderate",
                        "text": "当前问题需要联合解释 correlation 与 cross-FRiP，但回答缺少 cross-FRiP 语境。",
                    }
                )
        return issues

    @classmethod
    def _missing_modality_issues(
        cls,
        text: str,
        analysis_result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        response_plan = ((analysis_result.get("analysis_plan") or {}).get("response_plan") or {})
        if response_plan.get("reasoning_mode") != "integrative_reasoning":
            return []
        expected = [str(item) for item in (response_plan.get("expected_evidence_modalities") or []) if str(item).strip()]
        lowered = text.lower()
        issues: list[dict[str, Any]] = []
        modality_terms = {
            "frip": ("frip",),
            "peak_count": ("peak", "峰数量"),
            "correlation": ("correlation", "spearman", "相关性"),
            "motif": ("motif", "基序"),
            "spikein": ("spike-in", "spikein"),
            "alignment": ("mapping", "unique", "比对率", "唯一比对"),
            "experiment_design": ("对照", "重复", "condition", "batch"),
        }
        for modality, terms in modality_terms.items():
            if modality not in expected:
                continue
            if any(term in lowered for term in terms):
                continue
            issues.append(
                {
                    "rule": "critical_modality_omission",
                    "severity": "moderate" if modality in {"motif", "spikein"} else "severe",
                    "text": f"回答遗漏了复杂问题所需的关键模态：{modality}。",
                    "metric_id": modality,
                }
            )
        return issues

    @classmethod
    def _matches_any_value(
        cls,
        sentence: str,
        numbers: list[float],
        cards: list[dict[str, Any]],
    ) -> bool:
        has_percent = "%" in sentence or "百分" in sentence
        for card in cards:
            value = cls._number(card.get("value"))
            display = cls._number(card.get("display_value"))
            scale = str(card.get("value_scale") or "")
            allowed = [number for number in (value, display) if number is not None]
            if value is not None and scale == "fraction":
                allowed.append(value * 100.0 if has_percent else value)
            for number in numbers:
                if any(
                    abs(number - expected) <= max(0.01, abs(expected) * 1e-3)
                    for expected in allowed
                ):
                    return True
        return False

    @classmethod
    def _is_reference_or_conditional_numeric_sentence(cls, sentence: str) -> bool:
        stripped = str(sentence or "").strip()
        lowered = stripped.lower()
        if re.match(r"^\d+[.)、]\s*", stripped):
            return True
        if stripped.startswith(("- ", "* ", "• ")) and re.search(r"\d", stripped):
            return True
        return any(token in lowered for token in cls.REFERENCE_NUMERIC_HINTS)

    @classmethod
    def _definition_issues(cls, text: str) -> list[dict[str, Any]]:
        lowered = text.lower()
        issues: list[dict[str, Any]] = []
        if "frip" in lowered and any(
            phrase in lowered
            for phrase in (
                "reads in peaks / total reads",
                "peak reads / total reads",
                "所有 reads 作为分母",
                "总 reads 为分母",
            )
        ):
            issues.append(
                {
                    "rule": "metric_definition_mismatch",
                    "severity": "severe",
                    "metric_id": "frip_ratio",
                    "expected_denominator": metric_schema_service.get("frip_ratio").get(
                        "denominator"
                    ),
                }
            )
        if (
            "pbc2" in lowered
            and "two" not in lowered
            and "2" not in lowered
            and "两个" not in lowered
        ):
            for sentence in cls._sentences(text):
                if "pbc2" in sentence.lower() and any(
                    token in sentence for token in ("公式", "=", "定义")
                ):
                    issues.append(
                        {
                            "rule": "metric_definition_mismatch",
                            "severity": "moderate",
                            "metric_id": "pbc2",
                        }
                    )
        return issues

    @classmethod
    def _role_issues(
        cls,
        text: str,
        design: dict[str, Any],
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        lowered = text.lower()
        for item in design.get("samples", []) or []:
            if not isinstance(item, dict):
                continue
            sample = str(item.get("sample") or "")
            sample_lower = sample.lower()
            if not sample or sample_lower not in lowered:
                continue
            if item.get("role") == "control" and any(
                phrase in lowered
                for phrase in (
                    f"{sample_lower} is an experimental replicate",
                    f"{sample_lower} 是实验重复",
                    f"{sample_lower} 作为处理组",
                )
            ):
                issues.append(
                    {
                        "rule": "sample_role_conflict",
                        "severity": "severe",
                        "sample": sample,
                        "expected_role": "control",
                    }
                )
        return issues

    @classmethod
    def _differential_issues(
        cls,
        text: str,
        design: dict[str, Any],
    ) -> list[dict[str, Any]]:
        lowered = text.lower()
        if not any(term in lowered for term in cls.DIFFERENTIAL_TERMS):
            return []
        readiness = design.get("differential_analysis") or {}
        if readiness.get("ready"):
            return []
        uncertainty = any(
            term in lowered
            for term in (
                "不能",
                "不建议",
                "缺少",
                "需要补充",
                "not ready",
                "cannot",
            )
        )
        if uncertainty:
            return []
        return [
            {
                "rule": "differential_analysis_without_replicates_or_groups",
                "severity": "severe",
                "missing": readiness.get("reasons", []),
            }
        ]

    @classmethod
    def _causal_issues(
        cls,
        text: str,
        analysis_result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        lowered = text.lower()
        if not any(term in lowered for term in cls.CAUSAL_CERTAINTY):
            return []
        verified_causes = [
            item
            for item in analysis_result.get("validated_claims", []) or []
            if isinstance(item, dict) and item.get("causal_level") == "verified_cause"
        ]
        if verified_causes:
            return []
        return [
            {
                "rule": "causal_overstatement_without_verified_cause",
                "severity": "severe",
            }
        ]

    @staticmethod
    def _fact_layer_text(text: str) -> str:
        if not text:
            return ""
        capture = False
        collected: list[str] = []
        for line in str(text).splitlines():
            stripped = line.strip()
            if stripped.startswith("##"):
                capture = stripped.startswith("## 直接结论") or stripped.startswith("## 项目证据")
            if capture:
                collected.append(line)
        return "\n".join(collected).strip() or str(text)

    @staticmethod
    def _sentences(text: str) -> list[str]:
        return [
            item.strip()
            for item in re.split(r"[\n。！？；;]+", text)
            if item.strip()
        ]

    @staticmethod
    def _numbers(text: str) -> list[float]:
        result: list[float] = []
        for raw in re.findall(
            r"(?<![A-Za-z_])-?\d+(?:,\d{3})*(?:\.\d+)?(?![A-Za-z_])",
            text,
        ):
            try:
                result.append(float(raw.replace(",", "")))
            except ValueError:
                continue
        return result

    @staticmethod
    def _number(value: Any) -> float | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            return float(str(value).replace(",", "").rstrip("%"))
        except (TypeError, ValueError):
            return None


fact_verification_service = FactVerificationService()
