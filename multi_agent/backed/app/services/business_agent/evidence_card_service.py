from __future__ import annotations

import hashlib
import math
from typing import Any

from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)


class EvidenceCardService:
    """Build the compact, canonical evidence contract used by every layer."""

    SCHEMA_VERSION = "evidence-card-v2"

    PROCESSING_PHASES = {
        "adapter_percent": "raw_reads_pre_trim",
        "q20_ratio": "clean_reads_post_trim",
        "q30_ratio": "clean_reads_post_trim",
        "mapping_rate_percent": "alignment",
        "unique_mapping_rate_percent": "alignment",
        "duplicate_rate_percent": "post_alignment_library_complexity",
        "picard_duplicate_pair_rate_percent": "post_alignment_library_complexity",
        "mt_rate_percent": "alignment_organelle_partition",
        "frip_ratio": "post_peak_calling_enrichment",
        "peak_count": "peak_calling",
        "peak_width": "peak_calling",
        "tss_enrichment": "post_alignment_tss_enrichment",
        "fragment_size": "post_alignment_fragment_size_qc",
        "spikein_mapped_reads": "spikein_alignment_normalization",
        "spikein_unique_mapping_rate_percent": "spikein_alignment_normalization",
        "spikein_scaling_factor": "spikein_alignment_normalization",
        "sequencing_depth": "reads_qc",
        "nrf": "post_alignment_library_complexity",
        "pbc1": "post_alignment_library_complexity",
        "pbc2": "post_alignment_library_complexity",
        "control_binding_status": "experimental_design",
        "correlation": "cross_sample_signal_comparison",
    }
    ALLOWED_PHASES = {
        "adapter_percent": {"raw_reads_pre_trim", "clean_reads_post_trim", "reads_qc"},
        "q20_ratio": {"raw_reads_pre_trim", "clean_reads_post_trim", "reads_qc"},
        "q30_ratio": {"raw_reads_pre_trim", "clean_reads_post_trim", "reads_qc"},
    }

    @classmethod
    def build_fact_packet(
        cls,
        cards: list[dict[str, Any]],
        *,
        analysis_result: dict[str, Any] | None = None,
        question: str = "",
        project_id: str = "",
        quarantined_cards: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Assemble the canonical fact_packet that is the single source of truth.

        Only project-evidenced facts belong here.  Interpretation, mechanism
        explanations, and experience-based thresholds belong in reasoning_packet.

        `quarantined_cards`（任务⑦，2026-07-06）：validate_cards() 剔除的、没通过证据层
        校验的 card。这些不进入 project_evidence（其中的数值没通过校验，不该被当成可用
        证据），但摘要进 quarantined_evidence_summary，让主模型知道"项目里检测到这条证据，
        但因为具体原因被剔除了"，而不是让它们完全消失、看起来像项目根本没有这个指标。
        """
        result = analysis_result or {}
        validated_claims = [
            c for c in (result.get("validated_claims") or []) if isinstance(c, dict)
        ]
        evidence_chain = [
            e for e in (result.get("evidence_chain") or []) if isinstance(e, dict)
        ]

        # Build project_evidence from canonical cards (already normalised)
        project_evidence: list[dict[str, Any]] = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            project_evidence.append(
                {
                    "evidence_id": card.get("evidence_id"),
                    "metric_id": card.get("metric_id"),
                    # 2026-07-06 fact_packet 增强（任务④）：主模型回答时应直接使用
                    # metric_display_name，不要自己翻译 metric_id/source_field；
                    # label_verified=false 时这个名字来自候选指标/字段发现猜测，不是
                    # 受控词表，回答里要连同原始 metric_id 一起呈现、措辞保持谨慎。
                    "metric_display_name": card.get("metric_display_name", card.get("metric_id")),
                    "label_verified": card.get("label_verified", False),
                    "sample": card.get("sample"),
                    # 2026-07-06：sample 在 experiment_design 里对应的角色（treatment/control/
                    # input 等），修复 fact_verification_service._packet_role_issues 此前一直
                    # 取不到这个字段、sample_role_conflict 规则永远不触发的问题。
                    "assigned_role": card.get("assigned_role", ""),
                    "value": card.get("value"),
                    "display_value": card.get("display_value"),
                    "value_scale": card.get("value_scale"),
                    "source_file": card.get("source_file"),
                    "source_field": card.get("source_field"),
                    # 多来源合并后的完整溯源列表（consolidate_cards 合并时会保留多条），
                    # 单独的 source_file/source_field 在合并场景下会丢失次要来源。
                    "source_records": list(card.get("source_records") or []),
                    "formula": card.get("formula", ""),
                    "measurement_definition": card.get("measurement_definition", ""),
                    "numerator_name": card.get("numerator_name", ""),
                    "numerator_value": card.get("numerator_value"),
                    "denominator_name": card.get("denominator_name", ""),
                    "denominator_value": card.get("denominator_value"),
                    "population_scope": card.get("population_scope", ""),
                    "counting_unit": card.get("counting_unit", ""),
                    "assay": card.get("assay", ""),
                    "species": card.get("species", ""),
                    "processing_phase": card.get("processing_phase"),
                    "severity": card.get("severity", "unknown"),
                    "threshold_rule": card.get("threshold_rule") or {},
                    "threshold_verified": card.get("threshold_verified", False),
                    "conflict_status": card.get("conflict_status", "none"),
                    "conflict_ids": list(card.get("conflict_ids") or []),
                    # 校验层字段：build_fact_packet 收到的 cards 已经是 validate_cards() 过滤
                    # 后的 valid_cards（project_analysis_service._analyze 里 evidence_cards =
                    # evidence_card_validation["valid_cards"]），所以这两个字段在这里恒为
                    # "valid"/[]——没通过校验的证据被剔除进 quarantined_cards，另见
                    # quarantined_evidence_summary（顶层字段）。
                    "validation_status": card.get("validation_status", "valid"),
                    "validation_issues": list(card.get("validation_issues") or []),
                    # 公式/数值是否被重算验证过：recomputed（重算通过）/
                    # inputs_missing（声称严格重算但缺分子分母，只能引用展示值）/
                    # not_applicable（该指标本来就不是靠重算校验的，如纯展示/定性指标）。
                    "recompute_status": card.get("recompute_status", "not_applicable"),
                    # project_analysis_phase1.5_auto_promotion_revision.md §10：未核实值
                    # （转置表/字段发现猜测等）此前和已重算通过的值在 evidence_coverage
                    # 打分里权重完全相同，是一处完整性漏洞。trust_level 是这里的信任等级
                    # 标记：recalculated（过了公式重算或脚本公式转正祝福）/ display_only
                    # （仅展示，公式未核实）/ screening_signal（字段发现降级猜测）。
                    "trust_level": card.get("trust_level", "display_only"),
                }
            )

        # direct_conclusions: only validated_claims with direct evidence support
        # 2026-07-06 code review 修复：claim_service.py 实际产出的 claim 对象用的是
        # "text" 字段（不是 "claim"/"summary"），此前这里没有兜底 "text"，导致
        # claim_service 产出的 validated_claims 在这里全部变成空 claim。
        direct_conclusions: list[dict[str, Any]] = [
            {
                "claim": c.get("claim") or c.get("summary") or c.get("text") or "",
                "evidence_ids": list(c.get("evidence_ids") or []),
                "causal_level": c.get("causal_level", "association"),
                "confidence": c.get("confidence", ""),
            }
            for c in validated_claims
        ]

        # validated_observations: evidence chain items with high confidence
        validated_observations: list[dict[str, Any]] = [
            {
                "metric_id": e.get("metric_key") or e.get("metric_id"),
                "sample": e.get("sample"),
                "value": e.get("value"),
                "display_value": e.get("display_value"),
                "source_file": e.get("source_file"),
                "source_field": e.get("source_field"),
                "evidence_grade": e.get("evidence_grade", "direct_project_data"),
            }
            for e in evidence_chain
            if e.get("evidence_grade") in {"direct_project_data", "high_confidence"}
            or not e.get("threshold_needs_project_validation")
        ]

        # threshold_status: whether any evidence has unverified thresholds
        has_unverified = any(
            e.get("threshold_needs_project_validation")
            or e.get("severity") == "unverified_threshold"
            for e in evidence_chain
        )
        threshold_status = {
            "has_unverified_thresholds": has_unverified,
            "statement": (
                "项目文件中未确认该指标阈值/标准，只能报告观测值。"
                if has_unverified
                else ""
            ),
        }

        # quarantined_evidence_summary（任务⑦，2026-07-06 code review 修复）：只放摘要
        # （metric_id/sample/来源/evidence_id/validation_status + 完整 validation_issues），
        # 不把未通过校验的 card["value"]/card["display_value"] 本身带进 packet，避免未验证
        # 数值被当成可用证据误用。validation_issues 本身是"为什么被拒绝"的诊断上下文
        # （observed/recalculated/valid_range 等），不是可引用的证据值，予以完整保留——
        # 此前这里只抽了 rule 字符串，丢了 observed/recalculated/valid_range 等具体信息，
        # planner/前端看不到失败的具体上下文。
        quarantined_items: list[dict[str, Any]] = []
        issue_counts: dict[str, int] = {}
        for card in quarantined_cards or []:
            if not isinstance(card, dict):
                continue
            issues = [
                dict(issue)
                for issue in (card.get("validation_issues") or [])
                if isinstance(issue, dict)
            ]
            for issue in issues:
                reason = str(issue.get("rule") or "unknown")
                issue_counts[reason] = issue_counts.get(reason, 0) + 1
            quarantined_items.append(
                {
                    "evidence_id": card.get("evidence_id"),
                    "metric_id": card.get("metric_id"),
                    "sample": card.get("sample"),
                    "source_file": card.get("source_file"),
                    "source_field": card.get("source_field"),
                    "validation_status": card.get("validation_status", "invalid"),
                    "validation_issues": issues,
                    "reason": [str(issue.get("rule") or "unknown") for issue in issues],
                }
            )
        quarantined_evidence_summary = {
            "quarantined_count": len(quarantined_items),
            "issue_counts": issue_counts,
            "items": quarantined_items,
        }

        return {
            "schema_version": cls.SCHEMA_VERSION,
            "question": question,
            "project_id": project_id,
            "direct_conclusions": direct_conclusions,
            "project_evidence": project_evidence,
            "validated_observations": validated_observations,
            "threshold_status": threshold_status,
            "quarantined_evidence_summary": quarantined_evidence_summary,
        }

    @classmethod
    def build_cards(
        cls,
        evidence_chain: list[dict[str, Any]],
        *,
        project_id: str = "",
        project_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        context = project_context or {}
        config = context.get("config") if isinstance(context.get("config"), dict) else {}
        species = str((config or {}).get("species") or context.get("species") or "").strip()
        assay = str(
            (config or {}).get("assay")
            or (config or {}).get("experiment_type")
            or (config or {}).get("library_type")
            or ""
        ).strip()
        # 2026-07-06 fact_packet 增强（任务③）：assigned_role 需要反查 experiment_design
        # 里的样本角色。project_context 本来就带 experiment_design（project_context_builder_
        # service 构建时写入），这里直接读，不需要调用方额外传参。role_map 按小写样本名索引，
        # 和 fact_verification_service._packet_role_issues 里匹配 sample 的方式保持一致。
        experiment_design = context.get("experiment_design") if isinstance(context.get("experiment_design"), dict) else {}
        role_map = {
            str(s.get("sample") or "").strip().lower(): str(s.get("role") or "").strip()
            for s in (experiment_design.get("samples") or [])
            if isinstance(s, dict) and s.get("sample")
        }
        # 2026-07-06 性能回归修复（任务⑧）：metric_display_name 的候选指标名查询此前放在
        # from_evidence() 循环内，对每张未注册指标的 card 都单独调用一次
        # candidate_metric_repository.get()——该仓库默认走 MySQL 后端，每次都是新建同步
        # 连接，一个项目未注册指标可能有几十个，等于每次分析多出几十次同步 DB 往返
        # （harness 实测：一个 case 从 5s 拖到 79s，最终答案渲染因此被下游超时吃空）。
        # 改成这里只调用一次 list_all()，建好 candidate_key -> label 的字典后传给
        # from_evidence()，把 N 次查询降到每次 build_cards() 调用最多 1 次。
        candidate_labels: dict[str, str] = {}
        try:
            from multi_agent.backed.app.repositories.candidate_metric_repository import (
                candidate_metric_repository,
            )

            candidate_labels = {
                str(c.get("candidate_key")): str(c.get("label") or "").strip()
                for c in candidate_metric_repository.list_all()
                if isinstance(c, dict) and c.get("candidate_key") and c.get("label")
            }
        except Exception:  # noqa: BLE001
            candidate_labels = {}
        cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in evidence_chain:
            if not isinstance(item, dict):
                continue
            card = cls.from_evidence(
                item,
                project_id=project_id,
                species=species,
                assay=assay,
                role_map=role_map,
                candidate_labels=candidate_labels,
            )
            dedup_key = cls._dedup_key(card)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            cards.append(card)
        return cls.consolidate_cards(cards)

    @classmethod
    def from_evidence(
        cls,
        item: dict[str, Any],
        *,
        project_id: str = "",
        species: str = "",
        assay: str = "",
        role_map: dict[str, str] | None = None,
        candidate_labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        metric_id = str(item.get("metric_key") or item.get("metric_id") or "").strip()
        sample = str(item.get("sample") or "-").strip()
        assigned_role = str((role_map or {}).get(sample.lower(), "")).strip()
        # 2026-07-06 fact_packet 增强（任务③，任务⑧性能修复）：label_verified 标记这个
        # metric_id 是否命中 metric_schema_service 已注册的 schema——false 代表是候选指标/
        # 字段发现猜测出来的名字，回答时要连同原始 metric_id/source_field 一起呈现，不能当成
        # 正式指标名单独使用。候选指标名统一从调用方（build_cards）批量查好的 candidate_labels
        # 字典里取——这里不再单独查库，避免每张 card 一次同步 DB 往返（曾把一次分析从 5s 拖到
        # 79s，见任务⑧）。
        _schema_for_label = metric_schema_service.get(metric_id)
        label_verified = bool(_schema_for_label)
        if label_verified:
            metric_display_name = str(
                _schema_for_label.get("label_zh") or _schema_for_label.get("label") or metric_id
            )
        else:
            _candidate_label = str((candidate_labels or {}).get(metric_id, "")).strip()
            metric_display_name = _candidate_label or str(item.get("metric") or metric_id)
        source_file = str(item.get("source_file") or "").strip()
        source_field = str(item.get("source_field") or metric_id).strip()
        evidence_id = str(item.get("evidence_id") or "").strip() or cls._evidence_id(
            project_id,
            metric_id,
            sample,
            source_file,
            source_field,
            item.get("value"),
        )
        threshold_verified = bool(
            item.get("threshold_source")
            and not item.get("threshold_needs_project_validation", False)
            and item.get("threshold_rule")
        )
        denominator = item.get("denominator")
        denominator_name = str(
            item.get("denominator_name")
            or (denominator if isinstance(denominator, str) else "")
            or cls._default_denominator(metric_id)
        ).strip()
        denominator_value = item.get("denominator_value")
        if denominator_value is None and isinstance(denominator, (int, float)):
            denominator_value = denominator
        numerator = item.get("numerator")
        numerator_name = str(item.get("numerator_name") or cls._default_numerator(metric_id)).strip()
        numerator_value = item.get("numerator_value")
        if numerator_value is None and isinstance(numerator, (int, float)):
            numerator_value = numerator
        processing_phase = str(
            item.get("processing_phase")
            or cls.PROCESSING_PHASES.get(metric_id)
            or "unspecified"
        )
        measurement_id = str(item.get("measurement_id") or metric_id).strip()
        measurement_definition = str(
            item.get("measurement_definition")
            or item.get("formula")
            or cls._default_measurement_definition(metric_id)
        ).strip()
        counting_unit = str(
            item.get("counting_unit")
            or cls._default_counting_unit(metric_id)
        ).strip()
        population_scope = str(
            item.get("population_scope")
            or denominator_name
            or cls._default_denominator(metric_id)
        ).strip()
        value_scale = str(
            item.get("value_scale")
            or cls._default_value_scale(metric_id, item.get("unit"), item.get("display_value"))
        ).strip()
        display_scale = str(
            item.get("display_scale")
            or ("percent" if str(item.get("display_value") or "").strip().endswith("%") else value_scale)
        ).strip()
        source_record = {
            "source_file": source_file,
            "source_field": source_field,
            "formula_source": item.get("formula_source", ""),
            "expert_tool": item.get("expert_tool", ""),
        }
        conclusion_strength = str(item.get("conclusion_strength") or "direct_observation")
        # project_analysis_phase1.5_auto_promotion_revision.md §10：trust_level 独立于
        # conclusion_strength/evidence_grade，专供 evidence_coverage 打分按信任加权。
        # 大多数证据来自 project_analysis_service._build_rule_entry()，已经显式带
        # trust_level；这里的推导只是给未经过该函数的旧路径（如手工构造的 evidence_chain
        # 条目）一个合理默认，不覆盖已有值。
        trust_level = str(item.get("trust_level") or "").strip()
        if not trust_level:
            # 2026-07-02 code review 修复：这里之前写的是
            # `threshold_verified or evidence_grade in {...}`，把"阈值是否项目验证过"
            # 和"公式是否重算过"两件不相关的事混到了一起——一条阈值已验证、但公式完全没
            # 验证的证据（evidence_grade="project_threshold_formula_unverified"）会被
            # 误判成 recalculated，直接违反了这个字段本来要解决的"未核实值不能和已核实值
            # 同权重"的目标。trust_level 只能由 evidence_grade（是否 formula_verified）
            # 决定，和 threshold_verified 无关。
            if conclusion_strength == "screening_signal_only":
                trust_level = "screening_signal"
            elif item.get("evidence_grade") in {
                "project_formula_and_project_threshold",
                "project_formula_with_unverified_threshold",
            }:
                trust_level = "recalculated"
            else:
                trust_level = "display_only"
        allowed = ["report_observed_value", "trace_to_source"]
        if threshold_verified:
            allowed.append("apply_project_verified_threshold")
        else:
            allowed.append("describe_without_high_low_judgement")
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "evidence_id": evidence_id,
            "project_id": project_id,
            "metric_id": metric_id,
            "metric_family": str(item.get("metric_family") or metric_id),
            "measurement_id": measurement_id,
            "measurement_definition": measurement_definition,
            "metric": str(item.get("metric") or metric_id),
            "metric_display_name": metric_display_name,
            "label_verified": label_verified,
            "category": str(item.get("category") or ""),
            "sample": sample,
            "assigned_role": assigned_role,
            "sample_name": item.get("sample_name", ""),
            "left_sample": item.get("left_sample", ""),
            "right_sample": item.get("right_sample", ""),
            "peak_set": item.get("peak_set", ""),
            "pair_type": item.get("pair_type", ""),
            "comparison_type": item.get("comparison_type", ""),
            "assay": str(item.get("assay") or assay),
            "species": str(item.get("species") or species),
            "processing_phase": processing_phase,
            "numerator": numerator,
            "numerator_name": numerator_name,
            "numerator_value": numerator_value,
            "denominator": denominator if denominator not in (None, "") else denominator_name,
            "denominator_name": denominator_name,
            "denominator_value": denominator_value,
            "counting_unit": counting_unit,
            "population_scope": population_scope,
            "value": item.get("value"),
            "display_value": item.get("display_value"),
            "unit": item.get("unit", ""),
            "value_scale": value_scale,
            "display_scale": display_scale,
            "measurement_tolerance": item.get("measurement_tolerance"),
            "source_file": source_file,
            "source_field": source_field,
            "formula": item.get("formula", ""),
            "formula_source": item.get("formula_source", ""),
            "threshold_source": item.get("threshold_source", ""),
            "threshold_rule": item.get("threshold_rule") or {},
            "threshold_verified": threshold_verified,
            "severity": item.get("severity", "unknown"),
            "evidence_grade": item.get("evidence_grade", "direct_project_data"),
            "conclusion_strength": conclusion_strength,
            "trust_level": trust_level,
            "needs_verification": bool(item.get("needs_verification", False)),
            "allowed_interpretations": allowed,
            "interpretation": item.get("interpretation", ""),
            "downstream_impact": item.get("downstream_impact", ""),
            "expert_tool": item.get("expert_tool", ""),
            "source_records": [source_record] if source_file or source_field else [],
            "conflict_status": str(item.get("conflict_status") or "none"),
            "conflict_ids": list(item.get("conflict_ids") or []),
        }

    @classmethod
    def attach_ids(
        cls,
        evidence_chain: list[dict[str, Any]],
        cards: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        card_by_key = {cls._dedup_key(card): card for card in cards}
        card_by_semantic_key = {cls._semantic_key(card): card for card in cards}
        result: list[dict[str, Any]] = []
        for item in evidence_chain:
            copied = dict(item)
            probe = {
                "metric_id": item.get("metric_key") or item.get("metric_id"),
                "sample": item.get("sample"),
                "source_file": item.get("source_file"),
                "source_field": item.get("source_field"),
                "value": item.get("value"),
            }
            card = card_by_key.get(cls._dedup_key(probe))
            if card is None:
                probe.update(
                    {
                        "processing_phase": item.get("processing_phase")
                        or cls.PROCESSING_PHASES.get(str(probe.get("metric_id") or ""))
                        or "unspecified",
                        "measurement_id": item.get("measurement_id")
                        or probe.get("metric_id"),
                    }
                )
                card = card_by_semantic_key.get(cls._semantic_key(probe))
            if card:
                copied["evidence_id"] = card["evidence_id"]
                copied["processing_phase"] = card["processing_phase"]
            result.append(copied)
        return result

    @classmethod
    def filter_chain_to_valid_cards(
        cls,
        evidence_chain: list[dict[str, Any]],
        cards: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Drop evidence_chain items whose card did not survive validate_cards().

        `attach_ids()` deliberately keeps every chain item regardless of match
        (it only *attaches* ids when found), so it must not be reused to filter
        quarantined evidence out of evidence_chain — a quarantined retry item
        would sail straight through. This is the filtering counterpart: only
        used where the caller explicitly wants to exclude anything that isn't
        backed by a card in `cards` (e.g. reexplore merging retry_chain, where
        `cards` is validate_cards()'s valid_cards, not the unvalidated retry set).
        """
        card_by_key = {cls._dedup_key(card): card for card in cards}
        card_by_semantic_key = {cls._semantic_key(card): card for card in cards}
        result: list[dict[str, Any]] = []
        for item in evidence_chain:
            if not isinstance(item, dict):
                continue
            probe = {
                "metric_id": item.get("metric_key") or item.get("metric_id"),
                "sample": item.get("sample"),
                "source_file": item.get("source_file"),
                "source_field": item.get("source_field"),
                "value": item.get("value"),
            }
            card = card_by_key.get(cls._dedup_key(probe))
            if card is None:
                probe.update(
                    {
                        "processing_phase": item.get("processing_phase")
                        or cls.PROCESSING_PHASES.get(str(probe.get("metric_id") or ""))
                        or "unspecified",
                        "measurement_id": item.get("measurement_id")
                        or probe.get("metric_id"),
                    }
                )
                card = card_by_semantic_key.get(cls._semantic_key(probe))
            if card is not None:
                result.append(item)
        return result

    @staticmethod
    def to_evidence_chain(card: dict[str, Any]) -> dict[str, Any]:
        return {
            "evidence_id": card.get("evidence_id"),
            "category": card.get("category") or "ExpertTool",
            "metric_key": card.get("metric_id"),
            "metric": card.get("metric") or card.get("metric_id"),
            "sample": card.get("sample") or "-",
            "sample_name": card.get("sample_name", ""),
            "left_sample": card.get("left_sample", ""),
            "right_sample": card.get("right_sample", ""),
            "peak_set": card.get("peak_set", ""),
            "pair_type": card.get("pair_type", ""),
            "comparison_type": card.get("comparison_type", ""),
            "value": card.get("value"),
            "display_value": card.get("display_value"),
            "unit": card.get("unit", ""),
            "severity": card.get("severity", "unverified_threshold"),
            "source_file": card.get("source_file", ""),
            "source_field": card.get("source_field", ""),
            "formula": card.get("formula", ""),
            "denominator": card.get("denominator", ""),
            "numerator": card.get("numerator"),
            "denominator_name": card.get("denominator_name", ""),
            "denominator_value": card.get("denominator_value"),
            "numerator_name": card.get("numerator_name", ""),
            "numerator_value": card.get("numerator_value"),
            "counting_unit": card.get("counting_unit", ""),
            "population_scope": card.get("population_scope", ""),
            "measurement_id": card.get("measurement_id", card.get("metric_id")),
            "measurement_definition": card.get("measurement_definition", ""),
            "value_scale": card.get("value_scale", ""),
            "display_scale": card.get("display_scale", ""),
            "measurement_tolerance": card.get("measurement_tolerance"),
            "species": card.get("species", ""),
            "processing_phase": card.get("processing_phase", ""),
            "formula_source": card.get("formula_source", ""),
            "threshold_source": card.get("threshold_source", ""),
            "threshold_rule": card.get("threshold_rule") or {},
            "threshold_needs_project_validation": not bool(card.get("threshold_verified")),
            "needs_verification": card.get("needs_verification", False),
            "evidence_grade": card.get("evidence_grade", "direct_project_data"),
            "conclusion_strength": card.get("conclusion_strength", "direct_observation"),
            "interpretation": card.get("interpretation", ""),
            "downstream_impact": card.get("downstream_impact", ""),
            "expert_tool": card.get("expert_tool", ""),
            "source_records": list(card.get("source_records") or []),
            "conflict_status": card.get("conflict_status", "none"),
            "conflict_ids": list(card.get("conflict_ids") or []),
        }

    @classmethod
    def consolidate_cards(cls, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge duplicate observations while preserving every supporting source."""

        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for raw in cards:
            if not isinstance(raw, dict):
                continue
            card = dict(raw)
            key = cls._semantic_key(card)
            if key not in merged:
                compatible_key = cls._compatible_semantic_key(card, merged)
                if compatible_key:
                    key = compatible_key
            if key not in merged:
                merged[key] = card
                order.append(key)
                continue
            current = merged[key]
            preferred, secondary = cls._preferred_card(current, card)
            sources = cls._merge_sources(current, card)
            preferred["source_records"] = sources
            preferred["source_count"] = len(sources)
            preferred["supporting_evidence_ids"] = list(
                dict.fromkeys(
                    str(item)
                    for item in (
                        list(current.get("supporting_evidence_ids") or [])
                        + list(card.get("supporting_evidence_ids") or [])
                        + [current.get("evidence_id"), card.get("evidence_id")]
                    )
                    if item
                )
            )
            preferred["merged_source_fields"] = list(
                dict.fromkeys(
                    str(item.get("source_field") or "")
                    for item in sources
                    if item.get("source_field")
                )
            )
            if not preferred.get("formula") and secondary.get("formula"):
                preferred["formula"] = secondary["formula"]
            merged[key] = preferred

        result = [merged[key] for key in order]
        conflicts = cls.detect_conflicts(result)
        conflicts_by_evidence: dict[str, list[str]] = {}
        for conflict in conflicts:
            conflict_id = str(conflict["conflict_id"])
            for evidence_id in conflict["evidence_ids"]:
                conflicts_by_evidence.setdefault(str(evidence_id), []).append(conflict_id)
        for card in result:
            conflict_ids = conflicts_by_evidence.get(str(card.get("evidence_id") or ""), [])
            if conflict_ids:
                card["conflict_status"] = "unresolved"
                card["conflict_ids"] = conflict_ids
        return result

    @classmethod
    def validate_cards(cls, cards: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate canonical values before cards can support claims or answers."""

        valid_cards: list[dict[str, Any]] = []
        quarantined_cards: list[dict[str, Any]] = []
        issue_counts: dict[str, int] = {}
        for raw in cards:
            if not isinstance(raw, dict):
                continue
            card = dict(raw)
            metric_id = metric_schema_service.canonical_id(card.get("metric_id"))
            schema = metric_schema_service.get(metric_id)
            issues: list[dict[str, Any]] = []
            value = cls._as_float(card.get("value"))
            card.setdefault("recompute_status", "not_applicable")
            if schema and value is not None:
                valid_range = schema.get("valid_range") or [None, None]
                lower = valid_range[0] if len(valid_range) > 0 else None
                upper = valid_range[1] if len(valid_range) > 1 else None
                if (lower is not None and value < lower) or (
                    upper is not None and value > upper
                ):
                    issues.append(
                        {
                            "rule": "metric_value_out_of_physical_range",
                            "value": value,
                            "valid_range": valid_range,
                        }
                    )
                expected_scale = str(schema.get("value_scale") or "")
                actual_scale = str(card.get("value_scale") or "")
                if actual_scale and expected_scale and actual_scale != expected_scale:
                    issues.append(
                        {
                            "rule": "metric_value_scale_mismatch",
                            "expected": expected_scale,
                            "actual": actual_scale,
                        }
                    )
                numerator = cls._as_float(card.get("numerator_value"))
                denominator = cls._as_float(card.get("denominator_value"))
                if numerator is None:
                    numerator = cls._as_float(card.get("numerator"))
                if denominator is None:
                    denominator = cls._as_float(card.get("denominator"))
                if numerator is not None and denominator not in (None, 0):
                    recalculated = numerator / denominator
                    if expected_scale == "percent":
                        recalculated *= 100.0
                    tolerance = max(1e-6, abs(recalculated) * 0.005)
                    if abs(value - recalculated) > tolerance:
                        issues.append(
                            {
                                "rule": "formula_recalculation_mismatch",
                                "observed": value,
                                "recalculated": recalculated,
                            }
                        )
                # 2026-07-06 fact_packet 增强（任务②）：recompute_status 此前只在
                # fact_verification_service 验证时刻临时用 metric_schema_service.normalize()
                # 现算，card 上不落这个字段。这里对 strict_formula_recalculation 类指标提前
                # 调用一次 normalize()，把结果存回 card，供 build_fact_packet 直接抄进
                # project_evidence，也让 fact_verification_service 有机会直接读 card 上的值，
                # 不必每次验证都重新推导一遍。只额外新增字段，不改变上面这段既有的
                # formula_recalculation_mismatch 判定逻辑/容差，避免连带改变现有 quarantine 行为。
                if metric_schema_service.verifier_contract(metric_id) == "strict_formula_recalculation":
                    schema_check = metric_schema_service.normalize(
                        metric_id,
                        card.get("value"),
                        source_field="",
                        numerator=numerator,
                        denominator=denominator,
                    )
                    card["recompute_status"] = schema_check.get("recompute_status", "not_applicable")
                else:
                    card.setdefault("recompute_status", "not_applicable")
                expected_phase = cls.PROCESSING_PHASES.get(metric_id)
                actual_phase = str(card.get("processing_phase") or "unspecified")
                allowed_phases = cls.ALLOWED_PHASES.get(
                    metric_id,
                    {expected_phase} if expected_phase else set(),
                )
                if expected_phase and actual_phase not in allowed_phases:
                    issues.append(
                        {
                            "rule": "processing_phase_mismatch",
                            "expected": sorted(allowed_phases),
                            "actual": actual_phase,
                        }
                    )
            if card.get("conflict_status") == "unresolved":
                issues.append({"rule": "unresolved_evidence_conflict"})
            for issue in issues:
                rule = str(issue.get("rule") or "unknown")
                issue_counts[rule] = issue_counts.get(rule, 0) + 1
            card["validation_status"] = "invalid" if issues else "valid"
            card["validation_issues"] = issues
            if issues:
                card["quarantine_reason"] = "evidence_contract_failed"
                quarantined_cards.append(card)
            else:
                valid_cards.append(card)
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "passed": not quarantined_cards,
            "valid_cards": valid_cards,
            "quarantined_cards": quarantined_cards,
            "valid_count": len(valid_cards),
            "quarantined_count": len(quarantined_cards),
            "issue_counts": issue_counts,
        }

    @classmethod
    def detect_conflicts(cls, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str, str, str, str, str], list[dict[str, Any]]] = {}
        for card in cards:
            measurement_id = str(card.get("measurement_id") or card.get("metric_id") or "")
            sample = str(card.get("sample") or "-")
            phase = str(card.get("processing_phase") or "unspecified")
            definition = cls._normalize_identity_text(card.get("measurement_definition"))
            population_scope = cls._normalize_identity_text(card.get("population_scope"))
            counting_unit = cls._normalize_identity_text(card.get("counting_unit"))
            value_scale = str(card.get("value_scale") or "")
            groups.setdefault(
                (
                    measurement_id,
                    sample,
                    phase,
                    definition,
                    population_scope,
                    counting_unit,
                    value_scale,
                ),
                [],
            ).append(card)

        conflicts: list[dict[str, Any]] = []
        for (
            measurement_id,
            sample,
            phase,
            definition,
            population_scope,
            counting_unit,
            value_scale,
        ), group in groups.items():
            numeric = [card for card in group if cls._as_float(card.get("value")) is not None]
            values = [float(cls._as_float(card.get("value"))) for card in numeric]
            if len(values) < 2 or max(values) - min(values) <= cls._value_tolerance(values, numeric):
                continue
            evidence_ids = [str(card.get("evidence_id") or "") for card in numeric if card.get("evidence_id")]
            conflict_id = "conf_" + hashlib.sha1(
                (
                    f"{measurement_id}|{sample}|{phase}|{definition}|{population_scope}|"
                    f"{counting_unit}|{value_scale}|{'|'.join(sorted(evidence_ids))}"
                ).encode("utf-8")
            ).hexdigest()[:16]
            conflicts.append(
                {
                    "conflict_id": conflict_id,
                    "measurement_id": measurement_id,
                    "sample": sample,
                    "processing_phase": phase,
                    "measurement_definition": definition,
                    "population_scope": population_scope,
                    "counting_unit": counting_unit,
                    "value_scale": value_scale,
                    "values": values,
                    "evidence_ids": evidence_ids,
                    "status": "unresolved",
                    "reason": "same_measurement_has_incompatible_values",
                }
            )
        return conflicts

    @staticmethod
    def _default_denominator(metric_id: str) -> str:
        return {
            "adapter_percent": "raw reads",
            "q20_ratio": "clean read bases",
            "q30_ratio": "clean read bases",
            "mapping_rate_percent": "raw reads",
            "unique_mapping_rate_percent": "raw reads",
            "duplicate_rate_percent": "examined mapped fragments",
            "picard_duplicate_pair_rate_percent": "examined read pairs",
            "mt_rate_percent": "mapped reads",
            "frip_ratio": "mapped reads",
            "correlation": "paired sample signal bins",
        }.get(metric_id, "")

    @staticmethod
    def _default_numerator(metric_id: str) -> str:
        return {
            "adapter_percent": "adapter-detected reads",
            "mapping_rate_percent": "mapped reads",
            "unique_mapping_rate_percent": "uniquely mapped reads",
            "duplicate_rate_percent": "duplicate reads/fragments",
            "picard_duplicate_pair_rate_percent": "duplicate read pairs",
            "mt_rate_percent": "mitochondrial mapped reads",
            "frip_ratio": "reads in peaks",
            "peak_count": "called peaks",
        }.get(metric_id, "")

    @staticmethod
    def _default_counting_unit(metric_id: str) -> str:
        if metric_id == "picard_duplicate_pair_rate_percent":
            return "read_pairs"
        if metric_id in {"peak_count"}:
            return "peaks"
        if metric_id in {"fragment_size", "peak_width"}:
            return "base_pairs"
        if metric_id == "tss_enrichment":
            return "normalized_signal"
        if metric_id in {"nrf", "pbc1", "pbc2"}:
            return "fragments"
        return "reads"

    @staticmethod
    def _default_value_scale(metric_id: str, unit: Any, display_value: Any) -> str:
        if metric_id in {"frip_ratio", "nrf", "pbc1"}:
            return "fraction"
        if metric_id == "pbc2":
            return "ratio"
        if metric_id == "correlation":
            return "coefficient"
        if metric_id == "peak_count":
            return "count"
        if metric_id.endswith("_percent") or str(unit or "").strip() == "%":
            return "percent"
        if str(display_value or "").strip().endswith("%"):
            return "percent"
        if metric_id.endswith("_ratio"):
            return "fraction"
        return "number"

    @staticmethod
    def _default_measurement_definition(metric_id: str) -> str:
        return {
            "mapping_rate_percent": "mapped_reads / alignment_input_reads * 100",
            "unique_mapping_rate_percent": "unique_mapped_reads / alignment_input_reads * 100",
            "duplicate_rate_percent": "duplicate_reads_or_fragments / mapped_reads_or_fragments * 100",
            "picard_duplicate_pair_rate_percent": "Picard PERCENT_DUPLICATION * 100",
            "mt_rate_percent": "mitochondrial_mapped_reads / mapped_reads * 100",
            "frip_ratio": "reads_in_peaks / mapped_reads",
            "tss_enrichment": "normalized TSS-centered signal relative to flanking background",
            "correlation": "correlation(signal_vector_a, signal_vector_b)",
        }.get(metric_id, "")

    @staticmethod
    def _evidence_id(*parts: Any) -> str:
        raw = "|".join(str(part or "") for part in parts)
        return "ev_" + hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]

    @staticmethod
    def _dedup_key(card: dict[str, Any]) -> str:
        return "|".join(
            str(card.get(key) or "")
            for key in ("metric_id", "sample", "source_file", "source_field", "value")
        )

    @classmethod
    def _semantic_key(cls, card: dict[str, Any]) -> str:
        value = cls._as_float(card.get("value"))
        normalized_value = f"{value:.8g}" if value is not None else str(card.get("value") or "")
        return "|".join(
            (
                *cls._semantic_identity(card),
                normalized_value,
            )
        )

    @classmethod
    def _compatible_semantic_key(
        cls,
        card: dict[str, Any],
        merged: dict[str, dict[str, Any]],
    ) -> str | None:
        value = cls._as_float(card.get("value"))
        if value is None:
            return None
        identity = cls._semantic_identity(card)
        for key, current in merged.items():
            if cls._semantic_identity(current) != identity:
                continue
            current_value = cls._as_float(current.get("value"))
            if current_value is None:
                continue
            if abs(value - current_value) <= cls._value_tolerance(
                [value, current_value],
                [card, current],
            ):
                return key
        return None

    @classmethod
    def _semantic_identity(cls, card: dict[str, Any]) -> tuple[str, ...]:
        metric_id = str(card.get("measurement_id") or card.get("metric_id") or "")
        canonical_metric = metric_schema_service.canonical_id(metric_id)
        sample = str(card.get("sample") or "-")
        definition = cls._normalize_identity_text(card.get("measurement_definition"))
        population = cls._normalize_identity_text(card.get("population_scope"))
        counting_unit = cls._normalize_identity_text(card.get("counting_unit"))
        if canonical_metric == "frip_ratio":
            read_sample = str(
                card.get("sample_name")
                or card.get("read_sample")
                or card.get("sample")
                or "-"
            )
            peak_set = str(card.get("peak_set") or read_sample)
            sample = f"{read_sample}|{peak_set}"
            definition = "reads_in_peaks/usable_mapped_reads_or_fragments"
            population = "usable_mapped_reads_or_fragments_evaluated_against_peak_set"
            if counting_unit in {
                "read",
                "reads",
                "fragment",
                "fragments",
                "fragments_or_reads",
                "reads_or_fragments",
            }:
                counting_unit = "reads_or_fragments"
        elif canonical_metric == "correlation":
            left = str(card.get("left_sample") or "")
            right = str(card.get("right_sample") or "")
            if left and right:
                sample = "|".join(sorted((left, right)))
        return (
            canonical_metric,
            sample,
            str(card.get("processing_phase") or "unspecified"),
            definition,
            population,
            counting_unit,
            str(card.get("value_scale") or ""),
        )

    @staticmethod
    def _normalize_identity_text(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().split())

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if isinstance(value, bool) or value in (None, ""):
            return None
        try:
            parsed = float(str(value).replace(",", "").rstrip("%"))
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @classmethod
    def _value_tolerance(
        cls,
        values: list[float],
        cards: list[dict[str, Any]] | None = None,
    ) -> float:
        scale = max([abs(value) for value in values] or [1.0])
        tolerances = [
            cls._as_float(card.get("measurement_tolerance"))
            for card in (cards or [])
        ]
        explicit = max([value for value in tolerances if value is not None] or [0.0])
        display_tolerance = max(
            [cls._display_rounding_tolerance(card.get("display_value")) for card in (cards or [])]
            or [0.0]
        )
        return max(1e-6, scale * 1e-4, explicit, display_tolerance)

    @staticmethod
    def _display_rounding_tolerance(display_value: Any) -> float:
        text = str(display_value or "").strip().rstrip("%")
        if not text:
            return 0.0
        if "." not in text:
            return 0.5
        decimals = len(text.rsplit(".", 1)[-1])
        return 0.5 * (10 ** (-decimals))

    @staticmethod
    def _merge_sources(*cards: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for card in cards:
            source_records = list(card.get("source_records") or [])
            if not source_records and (card.get("source_file") or card.get("source_field")):
                source_records = [
                    {
                        "source_file": card.get("source_file", ""),
                        "source_field": card.get("source_field", ""),
                        "formula_source": card.get("formula_source", ""),
                        "expert_tool": card.get("expert_tool", ""),
                    }
                ]
            for record in source_records:
                key = tuple(
                    str(record.get(name) or "")
                    for name in ("source_file", "source_field", "formula_source", "expert_tool")
                )
                if key not in seen:
                    seen.add(key)
                    records.append(dict(record))
        return records

    @staticmethod
    def _preferred_card(left: dict[str, Any], right: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        def score(card: dict[str, Any]) -> int:
            return sum(
                (
                    3 if card.get("denominator_value") is not None else 0,
                    2 if card.get("numerator_value") is not None else 0,
                    2 if card.get("formula") else 0,
                    1 if card.get("expert_tool") else 0,
                    1 if card.get("measurement_definition") else 0,
                )
            )

        return (dict(left), right) if score(left) >= score(right) else (dict(right), left)


evidence_card_service = EvidenceCardService()
