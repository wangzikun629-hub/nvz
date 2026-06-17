from __future__ import annotations

import hashlib
from typing import Any


class ClaimService:
    """Build structured claims and deterministic rendering layers."""

    METRIC_LABELS = {
        "adapter_percent": "原始 reads 接头检出率",
        "clean_read_retention_percent": "过滤后 reads 保留率",
        "q20_ratio": "Q20 碱基比例",
        "q30_ratio": "Q30 碱基比例",
        "mapping_rate_percent": "比对率",
        "unique_mapping_rate_percent": "唯一比对率",
        "duplicate_rate_percent": "重复率",
        "picard_duplicate_pair_rate_percent": "Picard read-pair 重复率",
        "mt_rate_percent": "线粒体比对 reads 比例",
        "frip_ratio": "FRiP",
        "peak_count": "Peak 数量",
        "peak_width": "Peak 宽度",
        "tss_enrichment": "TSS enrichment",
        "fragment_size": "Fragment size",
        "spikein_mapped_reads": "Spike-in mapped reads",
        "spikein_unique_mapping_rate_percent": "Spike-in 唯一比对率",
        "spikein_scaling_factor": "Spike-in scaling factor",
        "control_binding_status": "对照可用性与绑定状态",
        "correlation": "样本信号相关性",
        "nrf": "NRF 文库复杂度",
        "pbc1": "PBC1 文库复杂度",
        "pbc2": "PBC2 文库复杂度",
    }

    @classmethod
    def build_claims(
        cls,
        *,
        evidence_cards: list[dict[str, Any]],
        cause_graph: dict[str, Any] | None = None,
        analysis_limits: list[str] | None = None,
        next_actions: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        claims: list[dict[str, Any]] = []
        for card in evidence_cards[:40]:
            evidence_id = str(card.get("evidence_id") or "")
            if not evidence_id:
                continue
            if card.get("value") is None and str(card.get("display_value") or "").strip() in {"", "-"}:
                continue
            metric_id = str(card.get("metric_id") or "")
            metric = cls.METRIC_LABELS.get(
                metric_id,
                str(card.get("metric") or metric_id or "指标"),
            )
            sample = str(card.get("sample") or "-")
            display = str(card.get("display_value") or card.get("value") or "-")
            denominator = card.get("denominator")
            denominator_name = str(card.get("denominator_name") or "").strip()
            denominator_value = card.get("denominator_value")
            if denominator_value not in (None, ""):
                denominator_text = (
                    f"，分母为 {denominator_value}"
                    + (f" {denominator_name}" if denominator_name else "")
                )
            else:
                denominator_text = f"，分母口径为 {denominator_name or denominator}" if denominator not in (None, "") else ""
            threshold_text = ""
            if card.get("threshold_verified") and card.get("severity") in {"warning", "critical"}:
                threshold_text = "，按项目已验证阈值需要复核"
            claims.append(
                {
                    "claim_id": cls._claim_id("observation", evidence_id),
                    "claim_type": "observation",
                    "causal_level": (
                        "associated_phenomenon"
                        if metric_id == "correlation"
                        or str(card.get("comparison_type") or "") == "cross_frip"
                        else "direct_observation"
                    ),
                    "text": f"{sample} 的 {metric} 观测值为 {display}{denominator_text}{threshold_text}。",
                    "status": "observed",
                    "support_level": "confirmed",
                    "evidence_ids": [evidence_id],
                    "supporting_evidence_ids": [evidence_id],
                    "contradicting_evidence_ids": [],
                    "metric_id": metric_id,
                    "measurement_id": card.get("measurement_id", card.get("metric_id")),
                    "measurement_definition": card.get("measurement_definition", ""),
                    "processing_phase": card.get("processing_phase", ""),
                    "sample": sample,
                    "species": card.get("species", ""),
                    "value": card.get("value"),
                    "display_value": card.get("display_value"),
                    "numerator": card.get("numerator"),
                    "numerator_value": card.get("numerator_value"),
                    "denominator": denominator,
                    "denominator_name": denominator_name,
                    "denominator_value": denominator_value,
                    "threshold_source": card.get("threshold_source", ""),
                    "threshold_assertion": bool(threshold_text),
                    "confidence": 1.0,
                    "missing_evidence": [],
                }
            )

        for cause in ((cause_graph or {}).get("ranked_causes") or [])[:8]:
            if not isinstance(cause, dict):
                continue
            support = cls._cause_support(cause.get("support_level"))
            supporting_ids = cls._cause_evidence_ids(cause.get("supporting_evidence"))
            contradicting_ids = cls._cause_evidence_ids(cause.get("contradicting_evidence"))
            missing_evidence = list(cause.get("missing_evidence") or [])[:6]
            causal_level = (
                "verified_cause"
                if support == "confirmed"
                and len(set(supporting_ids)) >= 2
                and not contradicting_ids
                and not missing_evidence
                else "possible_explanation"
            )
            label = str(cause.get("label") or cause.get("cause_id") or "待验证原因")
            reasoning = str(cause.get("reasoning_summary") or "").strip()
            text = f"{label}：{reasoning}" if reasoning else label
            claims.append(
                {
                    "claim_id": cls._claim_id("cause", cause.get("cause_id"), text),
                    "claim_type": "causal_hypothesis",
                    "causal_level": causal_level,
                    "text": text,
                    "status": "inferred" if support != "insufficient" else "unknown",
                    "support_level": support,
                    "evidence_ids": list(dict.fromkeys(supporting_ids + contradicting_ids)),
                    "supporting_evidence_ids": supporting_ids,
                    "contradicting_evidence_ids": contradicting_ids,
                    "metric_id": cause.get("focus_metric", ""),
                    "sample": "",
                    "species": "",
                    "value": None,
                    "display_value": "",
                    "numerator": None,
                    "denominator": "",
                    "threshold_source": "",
                    "threshold_assertion": False,
                    "confidence": min(1.0, float(cause.get("score") or 0.0) / 100.0),
                    "missing_evidence": missing_evidence,
                    "verification_actions": list(cause.get("verification_actions") or [])[:6],
                    "expected_validation_outcomes": list(
                        cause.get("expected_validation_outcomes") or []
                    )[:4],
                }
            )

        for text in list(dict.fromkeys(str(item).strip() for item in (analysis_limits or []) if str(item).strip()))[:8]:
            claims.append(
                {
                    "claim_id": cls._claim_id("limitation", text),
                    "claim_type": "limitation",
                    "causal_level": "not_applicable",
                    "text": text,
                    "status": "unknown",
                    "support_level": "insufficient",
                    "evidence_ids": [],
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": [],
                    "threshold_assertion": False,
                    "confidence": 1.0,
                    "missing_evidence": [text],
                }
            )
        for text in list(dict.fromkeys(str(item).strip() for item in (next_actions or []) if str(item).strip()))[:8]:
            claims.append(
                {
                    "claim_id": cls._claim_id("action", text),
                    "claim_type": "action",
                    "causal_level": "not_applicable",
                    "text": text,
                    "status": "recommended",
                    "support_level": "inferred",
                    "evidence_ids": [],
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": [],
                    "threshold_assertion": False,
                    "confidence": 1.0,
                    "missing_evidence": [],
                }
            )
        return claims

    @staticmethod
    def build_render_layers(validated_claims: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        return {
            "direct_observations": [
                item
                for item in validated_claims
                if item.get("causal_level") == "direct_observation"
            ][:8],
            "associated_phenomena": [
                item
                for item in validated_claims
                if item.get("causal_level") == "associated_phenomenon"
            ][:8],
            "possible_explanations": [
                item
                for item in validated_claims
                if item.get("causal_level") == "possible_explanation"
            ][:5],
            "verified_causes": [
                item
                for item in validated_claims
                if item.get("causal_level") == "verified_cause"
            ][:5],
            "ranked_causes": [
                item for item in validated_claims if item.get("claim_type") == "causal_hypothesis"
            ][:5],
            "limitations": [
                item for item in validated_claims if item.get("claim_type") == "limitation"
            ][:5],
            "actions": [
                item for item in validated_claims if item.get("claim_type") == "action"
            ][:5],
        }

    @staticmethod
    def render_markdown(
        *,
        validated_claims: list[dict[str, Any]],
        evidence_cards: list[dict[str, Any]],
        target_metrics: set[str] | None = None,
    ) -> str:
        """Render the verified contract without asking a model to restate it."""

        targets = set(target_metrics or [])
        card_by_id = {
            str(card.get("evidence_id") or ""): card
            for card in evidence_cards
            if isinstance(card, dict) and card.get("evidence_id")
        }
        observations = [
            claim
            for claim in validated_claims
            if claim.get("claim_type") == "observation"
            and (not targets or str(claim.get("metric_id") or "") in targets)
        ]
        if observations and not targets:
            observations = ClaimService._overview_observations(observations)
        if not observations and not targets:
            observations = [
                claim
                for claim in validated_claims
                if claim.get("claim_type") == "observation"
            ][:6]
        target_cards = [
            card
            for card in evidence_cards
            if isinstance(card, dict)
            and str(card.get("metric_id") or "") in targets
            and (card.get("value") is not None or str(card.get("display_value") or "").strip())
        ]
        causes = [
            claim
            for claim in validated_claims
            if claim.get("claim_type") == "causal_hypothesis"
            and (
                not targets
                or not str(claim.get("metric_id") or "")
                or str(claim.get("metric_id") or "") in targets
            )
        ][:3]
        limits = [
            claim
            for claim in validated_claims
            if claim.get("claim_type") == "limitation"
            and not str(claim.get("text") or "").startswith(
                "Threshold not verified in project scripts/README/SOP/report notes:"
            )
        ][:3]
        actions = [
            claim
            for claim in validated_claims
            if claim.get("claim_type") == "action"
        ][:4]
        referenced_cards = [
            card_by_id.get(str(evidence_id), {})
            for claim in observations
            for evidence_id in (claim.get("evidence_ids") or [])
        ]
        if targets and not observations:
            referenced_cards = target_cards
        has_unverified_threshold = any(
            card and not card.get("threshold_verified")
            for card in referenced_cards
        )

        lines: list[str] = []
        if observations:
            lines.append("## 直接结论")
            lines.extend(f"- {claim.get('text', '')}" for claim in observations[:8])
            lines.extend(["", "## 项目证据"])
            for claim in observations[:8]:
                evidence_id = next(iter(claim.get("evidence_ids", []) or []), "")
                card = card_by_id.get(str(evidence_id), {})
                sources = list(card.get("source_records") or [])
                source_text = "；".join(
                    f"{item.get('source_file', '-')}::{item.get('source_field', '-')}"
                    for item in sources[:3]
                ) or f"{card.get('source_file', '-')}::{card.get('source_field', '-')}"
                denominator_value = card.get("denominator_value")
                denominator_name = card.get("denominator_name") or card.get("denominator")
                denominator_text = (
                    f"{denominator_value} {denominator_name}".strip()
                    if denominator_value not in (None, "")
                    else str(denominator_name or "-")
                )
                lines.append(
                    f"- {card.get('sample', '-')}/"
                    f"{ClaimService.METRIC_LABELS.get(str(card.get('metric_id') or ''), card.get('metric', card.get('metric_id', '-')))}: "
                    f"{card.get('display_value', card.get('value', '-'))}；"
                    f"分母口径={denominator_text}；来源={source_text}"
                )
                measurement_text = ClaimService._measurement_text(card)
                if measurement_text:
                    lines.append(f"  - 计算口径：{measurement_text}")
        elif targets:
            target_labels = "、".join(
                ClaimService.METRIC_LABELS.get(metric_id, metric_id)
                for metric_id in sorted(targets)
            )
            lines.extend(
                [
                    "## 直接结论",
                    f"- 当前没有形成可发布的 {target_labels} 观测 Claim，不能用其他指标替代回答。",
                    "",
                    "## 目标证据状态",
                ]
            )
            if target_cards:
                for card in target_cards[:8]:
                    status = (
                        "存在未解决的跨来源冲突"
                        if card.get("conflict_status") == "unresolved"
                        else "未通过 Claim 数值、公式或口径校验"
                    )
                    lines.append(
                        f"- {card.get('sample', '-')}/"
                        f"{ClaimService.METRIC_LABELS.get(str(card.get('metric_id') or ''), card.get('metric', '-'))}: "
                        f"{card.get('display_value') or card.get('value')}；{status}；"
                        f"来源={card.get('source_file', '-')}::{card.get('source_field', '-')}"
                    )
            else:
                lines.append(f"- 项目中未读取到 {target_labels} 的结构化数值证据。")
        if causes:
            lines.extend(["", "## 原因排序"])
            for index, claim in enumerate(causes, start=1):
                label = {
                    "confirmed": "已证实",
                    "inferred": "推测",
                    "insufficient": "证据不足",
                }.get(str(claim.get("support_level") or ""), "证据不足")
                lines.append(f"- #{index} [{label}] {claim.get('text', '')}")
                supporting_cards = [
                    card_by_id.get(str(evidence_id), {})
                    for evidence_id in (claim.get("supporting_evidence_ids") or [])
                ]
                supporting_cards = [card for card in supporting_cards if card]
                if supporting_cards:
                    card = supporting_cards[0]
                    lines.append(
                        f"  - 支持证据：{card.get('sample', '-')} "
                        f"{ClaimService.METRIC_LABELS.get(str(card.get('metric_id') or ''), card.get('metric', '-'))}="
                        f"{card.get('display_value') or card.get('value')}。"
                    )
                else:
                    lines.append("  - 支持证据：当前没有已验证的独立关联指标，仅保留为待验证假设。")
                contradicting_cards = [
                    card_by_id.get(str(evidence_id), {})
                    for evidence_id in (claim.get("contradicting_evidence_ids") or [])
                ]
                contradicting_cards = [card for card in contradicting_cards if card]
                if contradicting_cards:
                    card = contradicting_cards[0]
                    lines.append(
                        f"  - 反证：{card.get('sample', '-')} "
                        f"{ClaimService.METRIC_LABELS.get(str(card.get('metric_id') or ''), card.get('metric', '-'))}="
                        f"{card.get('display_value') or card.get('value')}。"
                    )
                else:
                    lines.append("  - 反证：当前未发现可独立排除该假设的项目证据。")
                missing = list(claim.get("missing_evidence") or [])
                if missing:
                    lines.append(f"  - 缺失证据：{missing[0]}")
                actions_for_cause = list(claim.get("verification_actions") or [])
                if actions_for_cause:
                    lines.append(f"  - 验证动作：{actions_for_cause[0]}")
                outcomes = list(claim.get("expected_validation_outcomes") or [])
                if outcomes:
                    lines.append(f"  - 预期结果：{outcomes[0]}")
        if limits or has_unverified_threshold:
            lines.extend(["", "## 证据边界"])
            if has_unverified_threshold:
                lines.append(
                    "- 项目文件中未确认该指标的适用阈值，因此只能报告观测值，不能确定判断偏高、偏低或不合格。"
                )
            lines.extend(f"- {claim.get('text', '')}" for claim in limits)
        if actions:
            lines.extend(["", "## 优先复核"])
            lines.extend(f"- {claim.get('text', '')}" for claim in actions)
        return "\n".join(lines).strip()

    @staticmethod
    def _overview_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        by_metric: dict[str, list[dict[str, Any]]] = {}
        for claim in observations:
            metric_id = str(claim.get("metric_id") or "")
            by_metric.setdefault(metric_id, []).append(claim)

        selected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        ordered_metrics = [
            *[metric_id for metric_id in metric_order if metric_id in by_metric],
            *[metric_id for metric_id in by_metric if metric_id not in metric_order],
        ]
        for metric_id in ordered_metrics:
            claim = by_metric[metric_id][0]
            claim_id = str(claim.get("claim_id") or id(claim))
            if claim_id not in seen_ids:
                selected.append(claim)
                seen_ids.add(claim_id)
            if len(selected) >= 8:
                return selected

        for claim in observations:
            claim_id = str(claim.get("claim_id") or id(claim))
            if claim_id in seen_ids:
                continue
            selected.append(claim)
            seen_ids.add(claim_id)
            if len(selected) >= 8:
                break
        return selected

    @staticmethod
    def _measurement_text(card: dict[str, Any]) -> str:
        metric_id = str(card.get("metric_id") or "")
        known = {
            "adapter_percent": "接头检出 reads 数 ÷ 对应原始 reads 数 × 100%；R1、R2 和汇总值属于不同测量口径。",
            "frip_ratio": "落在 peak 内的 reads/fragments 数 ÷ 用于统计的 mapped reads/fragments 数。",
            "mapping_rate_percent": "成功比对的 reads 数 ÷ 比对输入 reads 数 × 100%。",
            "unique_mapping_rate_percent": "唯一比对 reads 数 ÷ 比对输入 reads 数 × 100%。",
            "mt_rate_percent": "线粒体比对 reads 数 ÷ 项目定义的 mapped reads 数 × 100%。",
        }
        return known.get(metric_id, str(card.get("measurement_definition") or "").strip())

    @staticmethod
    def _cause_support(value: Any) -> str:
        normalized = str(value or "").lower()
        if normalized in {"supported", "confirmed"}:
            return "confirmed"
        if normalized in {"partially_supported", "plausible", "inferred"}:
            return "inferred"
        return "insufficient"

    @staticmethod
    def _cause_evidence_ids(value: Any) -> list[str]:
        result: list[str] = []
        for item in value or []:
            if isinstance(item, dict) and item.get("evidence_id"):
                result.append(str(item["evidence_id"]))
        return list(dict.fromkeys(result))

    @staticmethod
    def _claim_id(*parts: Any) -> str:
        raw = "|".join(str(part or "") for part in parts)
        return "cl_" + hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


claim_service = ClaimService()
