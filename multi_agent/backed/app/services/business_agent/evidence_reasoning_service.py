from __future__ import annotations

from statistics import mean
from typing import Any

from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)


class EvidenceReasoningService:
    """Build a compact, relation-aware evidence packet for professional writing."""

    VERSION = "evidence-reasoning-v2"
    MAX_SELECTED_EVIDENCE = 12
    RELATED = {
        "frip_ratio": {
            "frip_ratio",
            "sequencing_depth",
            "mapping_rate_percent",
            "unique_mapping_rate_percent",
            "mt_rate_percent",
            "nrf",
            "pbc1",
            "pbc2",
            "peak_count",
            "correlation",
        },
        "spikein_scaling_factor": {
            "spikein_scaling_factor",
            "spikein_unique_mapping_rate_percent",
            "spikein_mapped_reads",
        },
    }

    @classmethod
    def build(
        cls,
        *,
        question: str,
        analysis_plan: dict[str, Any],
        evidence_cards: list[dict[str, Any]],
        validated_claims: list[dict[str, Any]],
        analysis_limits: list[str],
        next_actions: list[str],
        experiment_design: dict[str, Any],
        assay_profile: dict[str, Any],
        read_lineage: dict[str, Any],
        parsed_metrics: dict[str, Any] | None = None,
        user_assertions: list[dict[str, Any]] | None = None,
        evidence_conflicts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        targets = cls._target_metrics(question, analysis_plan)
        related = set(targets)
        for target in targets:
            related.update(cls.RELATED.get(target, {target}))
        valid_cards = [
            card
            for card in evidence_cards
            if isinstance(card, dict)
            and card.get("validation_status", "valid") == "valid"
        ]
        selected_cards, coverage = cls._select_with_coverage(
            cards=valid_cards,
            question=question,
            targets=targets,
            related=related,
            experiment_design=experiment_design,
        )
        selected_ids = {
            str(card.get("evidence_id") or "") for card in selected_cards
        }
        conclusions = [
            cls._claim_view(claim)
            for claim in validated_claims
            if isinstance(claim, dict)
            and (
                not claim.get("evidence_ids")
                or selected_ids.intersection(
                    str(item) for item in claim.get("evidence_ids", []) or []
                )
            )
        ][:6]
        relational_tables = cls._relational_tables(
            valid_cards,
            parsed_metrics or {},
            experiment_design,
        )
        derived_relationships = cls._derived_relationships(
            relational_tables,
            read_lineage,
        )
        hypothesis_panel = cls._hypothesis_panel(
            analysis_plan=analysis_plan,
            target_metrics=targets,
            evidence_cards=valid_cards,
            relational_tables=relational_tables,
            derived_relationships=derived_relationships,
            experiment_design=experiment_design,
            assay_profile=assay_profile,
        )
        lineage_breaks = [
            {"sample": sample.get("sample"), **transition}
            for sample in read_lineage.get("samples", []) or []
            for transition in sample.get("unexplained_breaks", []) or []
        ][:8]
        skill_cards = [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "decision_card": item.get("decision_card")
                or item.get("content")
                or "",
                "metadata": item.get("metadata") or {},
            }
            for item in analysis_plan.get("loaded_bio_skills", []) or []
            if isinstance(item, dict)
        ][:3]
        return {
            "version": cls.VERSION,
            "question": question,
            "target_metrics": sorted(targets),
            "project_observations": [cls._card_view(card) for card in selected_cards],
            "evidence": [cls._card_view(card) for card in selected_cards],
            "evidence_coverage": coverage,
            "structured_conclusions": conclusions,
            "conclusions": conclusions,
            "user_assertions": list(user_assertions or []),
            "relational_tables": relational_tables,
            "derived_relationships": derived_relationships,
            "hypothesis_panel": hypothesis_panel,
            "evidence_conflicts": cls._conflict_views(evidence_conflicts or []),
            "lineage_breaks": lineage_breaks,
            "data_availability": read_lineage.get("data_status", {}),
            "data_status": read_lineage.get("data_status", {}),
            "experiment_design": {
                "samples": experiment_design.get("samples", []),
                "differential_analysis": experiment_design.get(
                    "differential_analysis", {}
                ),
                "warnings": experiment_design.get("warnings", []),
            },
            "assay": {
                "assay": assay_profile.get("assay"),
                "target_class": assay_profile.get("target_class"),
                "missing_evidence": assay_profile.get("missing_evidence", []),
                "specialized_rules": assay_profile.get("specialized_rules", []),
            },
            "domain_interpretation_rules": {
                "assay_rules": assay_profile.get("specialized_rules", []),
                "skill_decision_cards": skill_cards,
            },
            "limitations": cls._compact_limits(analysis_limits),
            "verification_actions": list(
                dict.fromkeys(
                    str(item) for item in next_actions if str(item).strip()
                )
            )[:6],
            "next_actions": list(
                dict.fromkeys(
                    str(item) for item in next_actions if str(item).strip()
                )
            )[:6],
            "skill_decision_cards": skill_cards,
            "allowed_inferences": [
                "Project observations may be stated as direct observations with their logical source.",
                "User-provided assertions are unverified and may only support conditional reasoning.",
                "Domain rules may explain mechanisms but must not be presented as project facts.",
                "Causal conclusions require validated evidence beyond co-occurring abnormal metrics.",
                "Technical suitability may be assessed conditionally when the user explicitly asks.",
            ],
        }

    @classmethod
    def _target_metrics(
        cls, question: str, analysis_plan: dict[str, Any]
    ) -> set[str]:
        targets = {
            metric_schema_service.canonical_id(item)
            for item in analysis_plan.get("target_metrics", []) or []
            if str(item or "").strip()
        }
        lowered = question.lower()
        inferred = {
            "frip_ratio": ("frip",),
            "mapping_rate_percent": ("mapping", "比对率"),
            "spikein_scaling_factor": (
                "scaling factor",
                "scale factor",
                "spike-in",
                "spikein",
            ),
            "correlation": ("correlation", "spearman", "相关性"),
            "peak_count": ("peak", "peaks", "峰数量"),
            "fragment_size": ("fragment size", "insert size", "片段长度"),
            "nrf": ("nrf",),
            "pbc1": ("pbc1",),
            "pbc2": ("pbc2",),
            "motif": ("motif", "基序"),
        }
        targets.update(
            metric_id
            for metric_id, terms in inferred.items()
            if any(term in lowered for term in terms)
        )
        return targets

    @classmethod
    def _select_with_coverage(
        cls,
        *,
        cards: list[dict[str, Any]],
        question: str,
        targets: set[str],
        related: set[str],
        experiment_design: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        ranked = sorted(
            cards,
            key=lambda card: (
                -cls._score_card(card, question, targets, related),
                str(card.get("evidence_id") or ""),
            ),
        )
        selected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        def add_first(predicate: Any) -> None:
            for card in ranked:
                evidence_id = str(card.get("evidence_id") or id(card))
                if evidence_id in seen_ids or not predicate(card):
                    continue
                selected.append(card)
                seen_ids.add(evidence_id)
                return

        for metric_id in sorted(targets):
            add_first(
                lambda card, metric_id=metric_id: metric_schema_service.canonical_id(
                    card.get("metric_id")
                )
                == metric_id
            )

        relation_requirements = (
            ("frip_ratio", "self_frip", ""),
            ("frip_ratio", "cross_frip", "experiment_vs_experiment"),
            ("frip_ratio", "cross_frip", "experiment_vs_control"),
            ("frip_ratio", "cross_frip", "control_vs_experiment"),
            ("correlation", "", "experiment_vs_experiment"),
            ("correlation", "", "experiment_vs_control"),
        )
        if targets.intersection({"frip_ratio", "correlation", "peak_count"}):
            for metric_id, comparison_type, pair_type in relation_requirements:
                add_first(
                    lambda card,
                    metric_id=metric_id,
                    comparison_type=comparison_type,
                    pair_type=pair_type: (
                        metric_schema_service.canonical_id(card.get("metric_id"))
                        == metric_id
                        and (
                            not comparison_type
                            or card.get("comparison_type") == comparison_type
                        )
                        and (not pair_type or card.get("pair_type") == pair_type)
                    )
                )

        role_map = cls._role_map(experiment_design)
        for role in ("experimental", "control"):
            add_first(
                lambda card, role=role: cls._card_roles(card, role_map).intersection(
                    {role}
                )
                and metric_schema_service.canonical_id(card.get("metric_id"))
                in related
            )

        bucket_counts: dict[tuple[str, str, str], int] = {}
        for card in selected:
            bucket = cls._coverage_bucket(card)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        for card in ranked:
            if len(selected) >= cls.MAX_SELECTED_EVIDENCE:
                break
            evidence_id = str(card.get("evidence_id") or id(card))
            if evidence_id in seen_ids:
                continue
            bucket = cls._coverage_bucket(card)
            if bucket_counts.get(bucket, 0) >= 2:
                continue
            selected.append(card)
            seen_ids.add(evidence_id)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        covered_metrics = {
            metric_schema_service.canonical_id(card.get("metric_id"))
            for card in selected
        }
        relation_types = sorted(
            {
                value
                for card in selected
                for value in (
                    str(card.get("comparison_type") or ""),
                    str(card.get("pair_type") or ""),
                )
                if value
            }
        )
        roles = sorted(
            {
                role
                for card in selected
                for role in cls._card_roles(card, role_map)
                if role
            }
        )
        return selected, {
            "target_metrics": sorted(targets),
            "covered_target_metrics": sorted(targets.intersection(covered_metrics)),
            "missing_target_metrics": sorted(targets - covered_metrics),
            "covered_relation_types": relation_types,
            "covered_sample_roles": roles,
            "selection_strategy": "metric_relation_and_role_coverage",
        }

    @staticmethod
    def _score_card(
        card: dict[str, Any],
        question: str,
        targets: set[str],
        related: set[str],
    ) -> int:
        metric_id = metric_schema_service.canonical_id(card.get("metric_id"))
        score = 1
        if metric_id in targets:
            score += 20
        elif metric_id in related:
            score += 8
        lowered = question.lower()
        sample_tokens = {
            str(card.get(key) or "").lower()
            for key in ("sample", "sample_name", "left_sample", "right_sample", "peak_set")
        }
        if any(token and token != "-" and token in lowered for token in sample_tokens):
            score += 6
        if card.get("numerator_value") is not None and card.get(
            "denominator_value"
        ) is not None:
            score += 3
        if card.get("conflict_status") == "unresolved":
            score -= 20
        return score

    @classmethod
    def _relational_tables(
        cls,
        cards: list[dict[str, Any]],
        parsed_metrics: dict[str, Any],
        experiment_design: dict[str, Any],
    ) -> list[dict[str, Any]]:
        role_map = cls._role_map(experiment_design)
        frip_cells: list[dict[str, Any]] = []
        correlation_pairs: list[dict[str, Any]] = []
        peak_rows: list[dict[str, Any]] = []
        for card in cards:
            metric_id = metric_schema_service.canonical_id(card.get("metric_id"))
            if metric_id == "frip_ratio":
                read_sample = str(
                    card.get("sample_name")
                    or card.get("read_sample")
                    or card.get("sample")
                    or ""
                )
                peak_set = str(card.get("peak_set") or read_sample)
                frip_cells.append(
                    {
                        "read_sample": read_sample,
                        "peak_set": peak_set,
                        "value": card.get("value"),
                        "display_value": card.get("display_value"),
                        "comparison_type": card.get("comparison_type")
                        or ("self_frip" if read_sample == peak_set else "cross_frip"),
                        "pair_type": card.get("pair_type")
                        or cls._pair_type(read_sample, peak_set, role_map),
                        "read_role": role_map.get(read_sample, "unknown"),
                        "peak_role": role_map.get(peak_set, "unknown"),
                        "evidence_id": card.get("evidence_id"),
                    }
                )
            elif metric_id == "correlation":
                left, right = cls._pair_samples(card)
                correlation_pairs.append(
                    {
                        "left_sample": left,
                        "right_sample": right,
                        "value": card.get("value"),
                        "display_value": card.get("display_value"),
                        "pair_type": card.get("pair_type")
                        or cls._pair_type(left, right, role_map),
                        "evidence_id": card.get("evidence_id"),
                    }
                )
            elif metric_id == "peak_count":
                peak_rows.append(
                    {
                        "sample": card.get("sample"),
                        "value": card.get("value"),
                        "display_value": card.get("display_value"),
                        "role": role_map.get(str(card.get("sample") or ""), "unknown"),
                        "evidence_id": card.get("evidence_id"),
                    }
                )
        tables: list[dict[str, Any]] = []
        if frip_cells:
            tables.append(
                {
                    "table_id": "directional_frip_matrix",
                    "row_dimension": "read_sample",
                    "column_dimension": "peak_set",
                    "directional": True,
                    "rows": sorted({item["read_sample"] for item in frip_cells}),
                    "columns": sorted({item["peak_set"] for item in frip_cells}),
                    "cells": cls._dedupe_records(
                        frip_cells, ("read_sample", "peak_set")
                    ),
                }
            )
        if correlation_pairs:
            tables.append(
                {
                    "table_id": "sample_correlation_matrix",
                    "dimensions": ["left_sample", "right_sample"],
                    "directional": False,
                    "pairs": cls._dedupe_records(
                        correlation_pairs, ("left_sample", "right_sample")
                    ),
                }
            )
        if peak_rows:
            tables.append(
                {
                    "table_id": "peak_counts_by_sample",
                    "rows": cls._dedupe_records(peak_rows, ("sample",)),
                }
            )
        return tables

    @classmethod
    def _derived_relationships(
        cls,
        tables: list[dict[str, Any]],
        read_lineage: dict[str, Any],
    ) -> list[dict[str, Any]]:
        by_id = {str(table.get("table_id")): table for table in tables}
        frip_cells = by_id.get("directional_frip_matrix", {}).get("cells", []) or []
        correlation_pairs = by_id.get("sample_correlation_matrix", {}).get(
            "pairs", []
        ) or []
        result: list[dict[str, Any]] = []
        self_values = {
            str(item.get("read_sample")): cls._number(item.get("value"))
            for item in frip_cells
            if item.get("comparison_type") == "self_frip"
            or item.get("read_sample") == item.get("peak_set")
        }
        diagonal = [value for value in self_values.values() if value is not None]
        if diagonal:
            result.append(
                {
                    "relationship": "frip_diagonal_mean",
                    "value": round(mean(diagonal), 6),
                    "sample_count": len(diagonal),
                }
            )
        groups = {
            "ip_ip_cross": "experiment_vs_experiment",
            "ip_to_control": "experiment_vs_control",
            "control_to_ip": "control_vs_experiment",
        }
        for relationship, pair_type in groups.items():
            values = [
                cls._number(item.get("value"))
                for item in frip_cells
                if item.get("pair_type") == pair_type
                and item.get("comparison_type") != "self_frip"
            ]
            values = [value for value in values if value is not None]
            if values:
                result.append(
                    {
                        "relationship": relationship,
                        "mean": round(mean(values), 6),
                        "minimum": min(values),
                        "maximum": max(values),
                        "count": len(values),
                    }
                )
        ip_ip_cross_mean = cls._group_mean(result, "ip_ip_cross")
        ip_to_control_mean = cls._group_mean(result, "ip_to_control")
        control_gap = (
            ip_ip_cross_mean - ip_to_control_mean
            if ip_ip_cross_mean is not None and ip_to_control_mean is not None
            else None
        )
        if control_gap is not None:
            result.append(
                {
                    "relationship": "cross_frip_control_gap",
                    "difference": round(control_gap, 6),
                    "interpretation": (
                        "strong_control_separation"
                        if control_gap >= 0.1
                        else "weak_control_separation"
                    ),
                }
            )
        for item in frip_cells:
            read_sample = str(item.get("read_sample") or "")
            peak_set = str(item.get("peak_set") or "")
            value = cls._number(item.get("value"))
            own = self_values.get(read_sample)
            if (
                not read_sample
                or not peak_set
                or read_sample == peak_set
                or value is None
                or own in (None, 0)
            ):
                continue
            result.append(
                {
                    "relationship": "cross_to_self_frip_retention",
                    "read_sample": read_sample,
                    "peak_set": peak_set,
                    "retention_ratio": round(value / own, 6),
                }
            )
        directional = {
            (str(item.get("read_sample")), str(item.get("peak_set"))): cls._number(
                item.get("value")
            )
            for item in frip_cells
        }
        seen_pairs: set[tuple[str, str]] = set()
        for (left, right), forward in directional.items():
            if left == right or not left or not right:
                continue
            pair = tuple(sorted((left, right)))
            if pair in seen_pairs:
                continue
            reverse = directional.get((right, left))
            if forward is None or reverse is None:
                continue
            seen_pairs.add(pair)
            result.append(
                {
                    "relationship": "directional_frip_asymmetry",
                    "sample_a": left,
                    "sample_b": right,
                    "a_reads_in_b_peaks": forward,
                    "b_reads_in_a_peaks": reverse,
                    "absolute_difference": round(abs(forward - reverse), 6),
                }
            )
        correlation_map = {
            tuple(
                sorted(
                    (
                        str(item.get("left_sample") or ""),
                        str(item.get("right_sample") or ""),
                    )
                )
            ): cls._number(item.get("value"))
            for item in correlation_pairs
        }
        for pair, correlation in correlation_map.items():
            if not all(pair) or correlation is None:
                continue
            a, b = pair
            result.append(
                {
                    "relationship": "frip_correlation_evidence_alignment",
                    "sample_a": a,
                    "sample_b": b,
                    "correlation": correlation,
                    "a_reads_in_b_peaks": directional.get((a, b)),
                    "b_reads_in_a_peaks": directional.get((b, a)),
                    "interpretation": "requires_joint_interpretation",
                }
            )
            if directional.get((a, b)) is not None and directional.get((b, a)) is not None:
                mean_cross = mean(
                    [
                        value
                        for value in (
                            directional.get((a, b)),
                            directional.get((b, a)),
                        )
                        if value is not None
                    ]
                )
                result.append(
                    {
                        "relationship": "cross_frip_vs_correlation_consistency",
                        "sample_a": a,
                        "sample_b": b,
                        "correlation": correlation,
                        "mean_cross_frip": round(mean_cross, 6),
                        "consistency": (
                            "high_both"
                            if mean_cross >= 0.2 and correlation >= 0.8
                            else "frip_shared_but_global_signal_diverges"
                            if mean_cross >= 0.2 and correlation < 0.8
                            else "low_shared_enrichment"
                        ),
                    }
                )
        for sample in read_lineage.get("samples", []) or []:
            for transition in sample.get("unexplained_breaks", []) or []:
                result.append(
                    {
                        "relationship": "reads_stage_break",
                        "sample": sample.get("sample"),
                        **transition,
                    }
                )
        return result[:80]

    @staticmethod
    def _group_mean(items: list[dict[str, Any]], relationship: str) -> float | None:
        values = [
            float(item.get("mean"))
            for item in items
            if item.get("relationship") == relationship
            and item.get("mean") is not None
        ]
        return mean(values) if values else None

    @classmethod
    def _hypothesis_panel(
        cls,
        *,
        analysis_plan: dict[str, Any],
        target_metrics: set[str],
        evidence_cards: list[dict[str, Any]],
        relational_tables: list[dict[str, Any]],
        derived_relationships: list[dict[str, Any]],
        experiment_design: dict[str, Any],
        assay_profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        reasoning_mode = str(
            ((analysis_plan or {}).get("response_plan") or {}).get("reasoning_mode")
            or ""
        )
        if reasoning_mode != "integrative_reasoning":
            return []
        metric_ids = {
            metric_schema_service.canonical_id(card.get("metric_id"))
            for card in evidence_cards
            if isinstance(card, dict) and card.get("metric_id")
        }
        relationships = {
            str(item.get("relationship") or "")
            for item in derived_relationships
            if isinstance(item, dict)
        }
        hypothesis_rows: list[dict[str, Any]] = []
        if "frip_ratio" in target_metrics or "cross_frip_control_gap" in relationships:
            strong_control_gap = next(
                (
                    item
                    for item in derived_relationships
                    if item.get("relationship") == "cross_frip_control_gap"
                ),
                {},
            )
            control_gap_value = cls._number(strong_control_gap.get("difference"))
            hypothesis_rows.append(
                {
                    "hypothesis_type": "technical_artifact",
                    "label": "技术偏差主导",
                    "supported_by": [
                        "mapping/unique/duplicate/mt 等上游指标异常",
                        "cross-FRiP 与 correlation 同时下降或方向不稳定",
                    ],
                    "challenged_by": [
                        "IP-IP cross-FRiP 仍高且对照分离清晰",
                    ],
                    "applicable_when": [
                        "有效 reads 不足",
                        "背景升高",
                        "control/流程参数不稳定",
                    ],
                    "signal": (
                        "weakened" if control_gap_value is not None and control_gap_value >= 0.1 else "candidate"
                    ),
                }
            )
            hypothesis_rows.append(
                {
                    "hypothesis_type": "biological_difference",
                    "label": "真实生物学差异主导",
                    "supported_by": [
                        "IP-IP cross-FRiP 保持较高且 replicate/correlation 一致",
                        "peak/motif/target-class 变化具有方向性",
                    ],
                    "challenged_by": [
                        "对照分离不足或 technical QC 同时崩溃",
                    ],
                    "applicable_when": [
                        "target class 与实验设计支持条件差异解释",
                    ],
                    "signal": (
                        "supported" if control_gap_value is not None and control_gap_value >= 0.1 else "candidate"
                    ),
                }
            )
        if any(role.get("role") == "control" for role in (experiment_design.get("samples") or []) if isinstance(role, dict)):
            hypothesis_rows.append(
                {
                    "hypothesis_type": "control_design_issue",
                    "label": "对照/样本角色设计问题",
                    "supported_by": [
                        "experiment_vs_control 与 control_vs_experiment 关系异常",
                        "control binding / paired comparison 逻辑不稳定",
                    ],
                    "challenged_by": [
                        "样本角色与 control_for 关系清晰且 signal 分离稳定",
                    ],
                    "applicable_when": [
                        "IgG/Input/control 参与解释时",
                    ],
                    "signal": "candidate",
                }
            )
        if assay_profile.get("assay"):
            hypothesis_rows.append(
                {
                    "hypothesis_type": "workflow_parameter_issue",
                    "label": "流程参数或 assay 口径问题",
                    "supported_by": [
                        "需要 script/rule source 才能解释的口径差异",
                        "workflow/threshold/normalization 参数未确认",
                    ],
                    "challenged_by": [
                        "项目脚本已确认公式和参数，且结构化证据一致",
                    ],
                    "applicable_when": [
                        str(assay_profile.get("assay") or "").strip(),
                    ],
                    "signal": "candidate",
                }
            )
        return hypothesis_rows[:4]

    @staticmethod
    def _card_view(card: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "evidence_id",
            "metric_id",
            "sample",
            "sample_name",
            "read_sample",
            "peak_set",
            "left_sample",
            "right_sample",
            "comparison_type",
            "pair_type",
            "value",
            "display_value",
            "unit",
            "numerator_value",
            "denominator_value",
            "processing_phase",
            "source_field",
        )
        view = {
            key: card.get(key)
            for key in keys
            if card.get(key) not in (None, "", [], {})
        }
        source_file = str(card.get("source_file") or "").replace("\\", "/")
        if source_file:
            view["source"] = source_file.rsplit("/", 1)[-1]
        return view

    @staticmethod
    def _claim_view(claim: dict[str, Any]) -> dict[str, Any]:
        return {
            "claim_type": claim.get("claim_type"),
            "causal_level": claim.get("causal_level"),
            "support_level": claim.get("support_level"),
            "text": claim.get("text"),
            "evidence_ids": claim.get("evidence_ids", []),
        }

    @staticmethod
    def _conflict_views(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for conflict in conflicts[:12]:
            if not isinstance(conflict, dict):
                continue
            result.append(
                {
                    key: conflict.get(key)
                    for key in (
                        "conflict_id",
                        "metric_id",
                        "measurement_id",
                        "sample",
                        "processing_phase",
                        "status",
                        "reason",
                        "evidence_ids",
                        "values",
                    )
                    if conflict.get(key) not in (None, "", [], {})
                }
            )
        return result

    @staticmethod
    def _compact_limits(items: list[str]) -> list[str]:
        result: list[str] = []
        threshold_added = False
        for item in items:
            text = " ".join(str(item or "").split())
            if not text:
                continue
            if "threshold" in text.lower():
                if threshold_added:
                    continue
                threshold_added = True
                text = "No project-specific decision threshold was confirmed for the relevant unverified metrics."
            if text not in result:
                result.append(text)
            if len(result) >= 6:
                break
        return result

    @staticmethod
    def _coverage_bucket(card: dict[str, Any]) -> tuple[str, str, str]:
        return (
            metric_schema_service.canonical_id(card.get("metric_id")),
            str(card.get("comparison_type") or ""),
            str(card.get("pair_type") or ""),
        )

    @staticmethod
    def _role_map(experiment_design: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for sample in experiment_design.get("samples", []) or []:
            if not isinstance(sample, dict):
                continue
            name = str(
                sample.get("sample")
                or sample.get("sample_id")
                or sample.get("name")
                or ""
            )
            role = str(
                sample.get("role")
                or sample.get("sample_role")
                or sample.get("condition_role")
                or ""
            ).lower()
            if not name:
                continue
            if any(token in role for token in ("igg", "input", "control")):
                result[name] = "control"
            elif role:
                result[name] = "experimental"
        return result

    @classmethod
    def _card_roles(
        cls, card: dict[str, Any], role_map: dict[str, str]
    ) -> set[str]:
        return {
            role_map.get(str(card.get(key) or ""), "")
            for key in (
                "sample",
                "sample_name",
                "read_sample",
                "peak_set",
                "left_sample",
                "right_sample",
            )
        }

    @staticmethod
    def _pair_type(left: str, right: str, role_map: dict[str, str]) -> str:
        left_role = role_map.get(left, "unknown")
        right_role = role_map.get(right, "unknown")
        if left_role == "experimental" and right_role == "experimental":
            return "experiment_vs_experiment"
        if left_role == "experimental" and right_role == "control":
            return "experiment_vs_control"
        if left_role == "control" and right_role == "experimental":
            return "control_vs_experiment"
        if left_role == "control" and right_role == "control":
            return "control_vs_control"
        return "role_unresolved"

    @staticmethod
    def _pair_samples(card: dict[str, Any]) -> tuple[str, str]:
        left = str(card.get("left_sample") or "")
        right = str(card.get("right_sample") or "")
        if left or right:
            return left, right
        sample = str(card.get("sample") or "")
        for separator in (" vs ", " against ", "|", "__"):
            if separator in sample:
                parts = sample.split(separator, 1)
                return parts[0].strip(), parts[1].strip()
        return sample, ""

    @staticmethod
    def _dedupe_records(
        records: list[dict[str, Any]], keys: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for record in records:
            identity = tuple(str(record.get(key) or "") for key in keys)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(record)
        return result

    @staticmethod
    def _number(value: Any) -> float | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            return float(str(value).replace(",", "").rstrip("%"))
        except (TypeError, ValueError):
            return None


evidence_reasoning_service = EvidenceReasoningService()
