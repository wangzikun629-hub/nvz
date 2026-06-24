from __future__ import annotations

import copy
from typing import Any


class ProjectCauseAnalysisService:

    @staticmethod
    def canonical_metric_key(metric: Any) -> str:
        normalized = str(metric or "").strip().lower()
        aliases = {
            "frip": "frip_ratio",
            "chrmt_pt_rate_percent": "mt_rate_percent",
            "chrmt/pt": "mt_rate_percent",
            "mt": "mt_rate_percent",
        }
        return aliases.get(normalized, normalized)

    @classmethod
    def evidence_for_metrics(
        cls,
        evidence_chain: list[dict[str, Any]],
        metric_keys: list[str],
    ) -> list[dict[str, Any]]:
        wanted = {cls.canonical_metric_key(item) for item in metric_keys}
        rows: list[dict[str, Any]] = []
        for item in evidence_chain:
            metric_key = cls.canonical_metric_key(item.get("metric_key"))
            if metric_key not in wanted:
                continue
            rows.append(
                {
                    "evidence_id": item.get("evidence_id", ""),
                    "metric_key": metric_key,
                    "metric": item.get("metric", metric_key),
                    "sample": item.get("sample", "-"),
                    "value": item.get("display_value", "-"),
                    "severity": item.get("severity", "-"),
                    "source": f"{item.get('source_file', '-')}::{item.get('source_field', '-')}",
                    "formula_source": item.get("formula_source", "-"),
                    "threshold_source": item.get("threshold_source", "-"),
                    "needs_verification": item.get("needs_verification", True),
                    "measurement_id": item.get("measurement_id", metric_key),
                    "population_scope": item.get("population_scope", ""),
                }
            )
        return rows

    @classmethod
    def diagnostics_for_metric(
        cls,
        metric: str,
        tool_diagnostics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        metric = cls.canonical_metric_key(metric)
        tool_terms = {
            "adapter_percent": ("adapter",),
            "mapping_rate_percent": ("alignment",),
            "unique_mapping_rate_percent": ("alignment",),
            "mt_rate_percent": ("alignment",),
            "duplicate_rate_percent": ("duplicate",),
            "frip_ratio": ("frip", "peak"),
            "correlation": ("correlation",),
        }.get(metric, (metric,))
        diagnostics: list[dict[str, Any]] = []
        for item in tool_diagnostics:
            tool_name = str(item.get("tool") or "").lower()
            if any(term in tool_name for term in tool_terms):
                diagnostics.append(item)
        return diagnostics


    @classmethod
    def build_cause_graph(
        cls,
        *,
        question: str,
        analysis_plan: dict[str, Any],
        evidence_chain: list[dict[str, Any]],
        tool_diagnostics: list[dict[str, Any]],
        project_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metric_evidence_plan = analysis_plan.get("metric_evidence_plan") if isinstance(analysis_plan, dict) else {}
        if not isinstance(metric_evidence_plan, dict) or not metric_evidence_plan:
            target_metrics = analysis_plan.get("target_metrics", []) if isinstance(analysis_plan, dict) else []
            metric_evidence_plan = cls.fallback_metric_evidence_plan(target_metrics, question)

        nodes: list[dict[str, Any]] = []
        for metric, graph in metric_evidence_plan.items():
            if not isinstance(graph, dict):
                continue
            node = cls.build_cause_node(
                metric=str(metric),
                graph=graph,
                evidence_chain=evidence_chain,
                tool_diagnostics=tool_diagnostics,
                project_context=project_context or {},
            )
            if node:
                nodes.append(node)

        ranked_causes = cls.aggregate_ranked_causes(nodes)
        leading_hypothesis = ranked_causes[0] if ranked_causes else None
        confirmed_hypothesis = (
            leading_hypothesis
            if leading_hypothesis and leading_hypothesis.get("support_level") == "supported"
            else None
        )
        confidence_score = cls.cause_graph_confidence(ranked_causes)
        competing_hypotheses = cls.build_competing_hypotheses(ranked_causes)
        return {
            "version": "metric-evidence-graph-v2",
            "mode": "question_driven_metric_diagnosis" if nodes else "evidence_only",
            "nodes": nodes,
            "ranked_causes": ranked_causes,
            "competing_hypotheses": competing_hypotheses,
            "leading_hypothesis": leading_hypothesis,
            "confirmed_hypothesis": confirmed_hypothesis,
            "diagnostic_confidence": {
                "score": confidence_score,
                "level": "high" if confidence_score >= 0.75 else "moderate" if confidence_score >= 0.45 else "low",
                "boundary": (
                    "根因已获得独立项目证据支持。"
                    if confirmed_hypothesis
                    else "当前仅形成待验证的差异诊断，目标指标本身不能证明根因。"
                ),
            },
            "answer_guidance": [
                "Answer the user's current question first.",
                "Use evidence_chain for observed values and source fields.",
                "Use ranked_causes for differential diagnosis, supporting or contradicting evidence, and verification actions.",
                "Do not treat the focus metric itself as proof of its root cause.",
                "Do not judge overall project pass/fail.",
            ],
        }

    @classmethod
    def build_cause_node(
        cls,
        *,
        metric: str,
        graph: dict[str, Any],
        evidence_chain: list[dict[str, Any]],
        tool_diagnostics: list[dict[str, Any]],
        project_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        _canonical = cls.canonical_metric_key
        _evidence_for = cls.evidence_for_metrics
        _diag_for = cls.diagnostics_for_metric

        primary_metrics = [_canonical(item) for item in graph.get("primary", []) or []]
        if not primary_metrics:
            primary_metrics = [_canonical(metric)]
        upstream_metrics = [_canonical(item) for item in graph.get("upstream", []) or []]
        parallel_metrics = [_canonical(item) for item in graph.get("parallel", []) or []]
        downstream_metrics = [_canonical(item) for item in graph.get("downstream", []) or []]

        primary_evidence = _evidence_for(evidence_chain, primary_metrics)
        upstream_evidence = _evidence_for(evidence_chain, upstream_metrics)
        parallel_evidence = _evidence_for(evidence_chain, parallel_metrics)
        downstream_evidence = _evidence_for(evidence_chain, downstream_metrics)
        diagnostics = _diag_for(metric, tool_diagnostics)

        if not any((primary_evidence, upstream_evidence, parallel_evidence, downstream_evidence, diagnostics)):
            return None

        evidence_gaps: list[str] = []
        next_checks: list[str] = []
        reasoning: list[str] = []
        for diagnostic in diagnostics:
            for item in diagnostic.get("evidence_gaps", []) or []:
                text = str(item).strip()
                if text and text not in evidence_gaps:
                    evidence_gaps.append(text)
            for item in diagnostic.get("next_checks", []) or []:
                text = str(item).strip()
                if text and text not in next_checks:
                    next_checks.append(text)
            for item in diagnostic.get("reasoning_chain", []) or []:
                text = str(item).strip()
                if text and text not in reasoning:
                    reasoning.append(text)

        hypotheses = cls.rank_candidate_causes(
            focus_metric=_canonical(metric),
            candidate_causes=graph.get("candidate_causes", []) or [],
            primary_evidence=primary_evidence,
            upstream_evidence=upstream_evidence,
            parallel_evidence=parallel_evidence,
            downstream_evidence=downstream_evidence,
            diagnostics=diagnostics,
            project_context=project_context,
            diagnostic_gaps=evidence_gaps,
            diagnostic_checks=next_checks,
        )

        return {
            "focus_metric": _canonical(metric),
            "primary_evidence": primary_evidence[:8],
            "upstream_evidence": upstream_evidence[:8],
            "parallel_evidence": parallel_evidence[:8],
            "downstream_evidence": downstream_evidence[:8],
            "candidate_causes": hypotheses[:8],
            "ranked_causes": hypotheses[:8],
            "diagnostic_summaries": [
                {
                    "tool": item.get("tool", ""),
                    "status": item.get("status", ""),
                    "summary": item.get("summary", ""),
                }
                for item in diagnostics[:4]
                if isinstance(item, dict)
            ],
            "reasoning_chain": reasoning[:8],
            "evidence_gaps": evidence_gaps[:8],
            "next_checks": next_checks[:8],
        }

    @classmethod
    def rank_candidate_causes(
        cls,
        *,
        focus_metric: str,
        candidate_causes: list[Any],
        primary_evidence: list[dict[str, Any]],
        upstream_evidence: list[dict[str, Any]],
        parallel_evidence: list[dict[str, Any]],
        downstream_evidence: list[dict[str, Any]],
        diagnostics: list[dict[str, Any]],
        project_context: dict[str, Any],
        diagnostic_gaps: list[str],
        diagnostic_checks: list[str],
    ) -> list[dict[str, Any]]:
        _canonical = cls.canonical_metric_key

        related_evidence: list[tuple[str, dict[str, Any]]] = []
        seen_related: set[tuple[str, str, str, str, str, str]] = set()
        for relation, items in (
            ("upstream", upstream_evidence),
            ("parallel", parallel_evidence),
            ("downstream", downstream_evidence),
        ):
            for item in items:
                if not isinstance(item, dict):
                    continue
                semantic_key = (
                    relation,
                    _canonical(item.get("metric_key")),
                    str(item.get("sample") or "-"),
                    str(item.get("measurement_id") or item.get("metric_key") or ""),
                    str(item.get("population_scope") or ""),
                    str(item.get("value") or ""),
                )
                if semantic_key in seen_related:
                    continue
                seen_related.add(semantic_key)
                related_evidence.append((relation, item))

        ranked: list[dict[str, Any]] = []
        diagnostic_review = any(item.get("status") == "needs_review" for item in diagnostics)
        diagnostic_available = any(item.get("status") != "missing_evidence" for item in diagnostics)
        for index, raw_cause in enumerate(candidate_causes):
            cause_id = str(raw_cause or "").strip()
            if not cause_id:
                continue
            profile = cls.cause_profile(cause_id)
            support_metrics = {_canonical(item) for item in profile["support_metrics"]}
            contradict_on_normal = {
                _canonical(item) for item in profile.get("contradict_on_normal", [])
            }
            supporting: list[dict[str, Any]] = []
            contradicting: list[dict[str, Any]] = []
            verified_support_families: set[tuple[str, str, str, str]] = set()
            for relation, item in related_evidence:
                metric_key = _canonical(item.get("metric_key"))
                if metric_key not in support_metrics:
                    continue
                severity = str(item.get("severity") or "")
                evidence_item = {
                    "evidence_id": item.get("evidence_id", ""),
                    "relation": relation,
                    "metric_key": metric_key,
                    "sample": item.get("sample", "-"),
                    "value": item.get("value", "-"),
                    "severity": severity or "-",
                    "source": item.get("source", "-"),
                    "measurement_id": item.get("measurement_id", metric_key),
                    "population_scope": item.get("population_scope", ""),
                    "strength": "verified" if severity in {"critical", "warning"} else "observational",
                }
                if severity == "normal" and metric_key in contradict_on_normal:
                    evidence_item["reason"] = "该关联指标在项目阈值下正常，未出现该假设预期的联动。"
                    contradicting.append(evidence_item)
                else:
                    if severity in {"critical", "warning"}:
                        verified_support_families.add(
                            (
                                metric_key,
                                str(item.get("sample") or "-"),
                                str(item.get("measurement_id") or metric_key),
                                str(item.get("population_scope") or ""),
                            )
                        )
                        evidence_item["reason"] = "项目阈值支持该关联指标需要复核，为根因假设提供独立支持。"
                    else:
                        evidence_item["reason"] = "存在同项目关联观测，但阈值或方向尚未验证，只能作为弱支持。"
                    supporting.append(evidence_item)

            verified_support_count = len(verified_support_families)
            context_evidence = cls.cause_context_evidence(profile, project_context)
            score = 12 + max(0, 4 - index)
            score += min(verified_support_count * 18, 36)
            score += min(sum(1 for item in supporting if item["strength"] == "observational") * 4, 12)
            score += min(len(context_evidence) * 2, 4)
            score += 5 if diagnostic_review else 2 if diagnostic_available else 0
            score -= min(len(contradicting) * 15, 30)
            score = max(0, min(score, 100))

            if verified_support_count >= 2 and not contradicting:
                support_level = "supported"
            elif verified_support_count >= 1:
                support_level = "partially_supported"
            elif supporting or context_evidence or diagnostic_available:
                support_level = "plausible"
            else:
                support_level = "insufficient_evidence"

            verification_actions = cls.dedupe_text(
                list(profile["verification_actions"]) + list(diagnostic_checks)
            )[:5]
            missing_evidence = cls.dedupe_text(
                list(profile["missing_evidence"]) + list(diagnostic_gaps)
            )[:5]
            expected = cls.expected_validation_outcomes(cause_id, profile)
            reasoning_summary = cls.cause_reasoning_summary(
                label=profile["label"],
                support_level=support_level,
                supporting=supporting,
                contradicting=contradicting,
                context_evidence=context_evidence,
            )
            ranked.append(
                {
                    "cause_id": cause_id,
                    "cause": cause_id,
                    "label": profile["label"],
                    "score": score,
                    "support_level": support_level,
                    "supporting_evidence": supporting[:6],
                    "supporting_evidence_count": len(supporting),
                    "verified_support_count": verified_support_count,
                    "contradicting_evidence": contradicting[:4],
                    "context_evidence": context_evidence[:4],
                    "missing_evidence": missing_evidence,
                    "verification_actions": verification_actions,
                    "expected_validation_outcomes": expected,
                    "downstream_impacts": list(profile["downstream_impacts"])[:4],
                    "reasoning_summary": reasoning_summary,
                    "focus_metric": focus_metric,
                }
            )

        ranked.sort(
            key=lambda item: (
                -int(item.get("score") or 0),
                -int(item.get("verified_support_count") or 0),
                str(item.get("cause_id") or ""),
            )
        )
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
        return ranked

    @staticmethod
    def build_competing_hypotheses(ranked_causes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        panel: list[dict[str, Any]] = []
        leader = ranked_causes[0] if ranked_causes else None
        leader_score = float(leader.get("score") or 0.0) if isinstance(leader, dict) else 0.0
        for cause in ranked_causes[:4]:
            if not isinstance(cause, dict):
                continue
            score = float(cause.get("score") or 0.0)
            panel.append(
                {
                    "hypothesis_id": cause.get("cause_id") or cause.get("label") or "candidate",
                    "label": cause.get("label") or cause.get("cause_id") or "candidate",
                    "focus_metric": cause.get("focus_metric") or "",
                    "support_level": cause.get("support_level") or "",
                    "confidence": round(min(score / 100.0, 0.99), 3),
                    "supporting_evidence": list(cause.get("supporting_evidence") or [])[:3],
                    "contradicting_evidence": list(cause.get("contradicting_evidence") or [])[:2],
                    "missing_critical_evidence": list(cause.get("missing_evidence") or [])[:3],
                    "verification_actions": list(cause.get("verification_actions") or [])[:3],
                    "preferred_over_alternatives": bool(
                        leader is cause or str(cause.get("cause_id") or "") == str((leader or {}).get("cause_id") or "")
                    ),
                    "preference_reason": (
                        "当前综合得分最高，且独立支持证据更多。"
                        if leader is cause or str(cause.get("cause_id") or "") == str((leader or {}).get("cause_id") or "")
                        else "当前排序低于首位假设，需要更多独立证据或更少反证才能提升优先级。"
                        if leader_score > score
                        else "当前与其他假设接近，仍需补充验证。"
                    ),
                }
            )
        return panel

    @classmethod
    def aggregate_ranked_causes(cls, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_cause: dict[str, dict[str, Any]] = {}
        for node in nodes:
            for cause in node.get("ranked_causes", []) or []:
                if not isinstance(cause, dict):
                    continue
                cause_id = str(cause.get("cause_id") or cause.get("cause") or "")
                current = by_cause.get(cause_id)
                if current is None or int(cause.get("score") or 0) > int(current.get("score") or 0):
                    by_cause[cause_id] = copy.deepcopy(cause)
        ranked = sorted(
            by_cause.values(),
            key=lambda item: (
                -int(item.get("score") or 0),
                -int(item.get("verified_support_count") or 0),
                str(item.get("cause_id") or ""),
            ),
        )
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
        return ranked[:12]

    @staticmethod
    def cause_graph_confidence(ranked_causes: list[dict[str, Any]]) -> float:
        if not ranked_causes:
            return 0.0
        top = ranked_causes[0]
        score = 0.15
        score += min(int(top.get("verified_support_count") or 0) * 0.22, 0.44)
        score += min(int(top.get("supporting_evidence_count") or 0) * 0.04, 0.16)
        score += 0.1 if top.get("context_evidence") else 0.0
        score -= min(len(top.get("contradicting_evidence") or []) * 0.12, 0.24)
        return round(max(0.0, min(score, 0.95)), 2)

    @classmethod
    def cause_context_evidence(
        cls,
        profile: dict[str, Any],
        project_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        sources = project_context.get("workflow_rule_sources") or {}
        evidence: list[dict[str, Any]] = []
        for rule_key in profile.get("context_rules", []) or []:
            source = sources.get(rule_key) if isinstance(sources, dict) else None
            if not isinstance(source, dict) or not source:
                continue
            evidence.append(
                {
                    "rule": rule_key,
                    "value": source.get("value", "-"),
                    "source": source.get("source_file") or source.get("evidence") or "-",
                    "source_level": source.get("source_level", "-"),
                    "reason": "项目配置可用于直接核验该假设，但配置存在本身不等于根因成立。",
                }
            )
        return evidence

    @staticmethod
    def cause_reasoning_summary(
        *,
        label: str,
        support_level: str,
        supporting: list[dict[str, Any]],
        contradicting: list[dict[str, Any]],
        context_evidence: list[dict[str, Any]],
    ) -> str:
        if support_level in {"supported", "partially_supported"}:
            summary = f"{label}获得独立关联指标支持"
        elif supporting:
            summary = f"{label}与当前关联观测一致，但相关阈值或方向尚未验证"
        elif context_evidence:
            summary = f"{label}可由项目配置直接核验，但当前没有独立指标证明"
        else:
            summary = f"{label}目前仅是机制上可解释的候选原因"
        if contradicting:
            summary += f"；同时有 {len(contradicting)} 条关联证据削弱该假设"
        return summary + "。"

    @staticmethod
    def expected_validation_outcomes(
        cause_id: str,
        profile: dict[str, Any],
    ) -> list[str]:
        specific = {
            "organelle_dna_background": [
                "若假设成立，raw/trimmed/mapped 各阶段会持续看到细胞器 reads 富集，且问题样本高于同批对照。",
                "若假设不成立，细胞器比例应主要由某一统计阶段或口径变化造成，而不是从原始数据开始持续升高。",
            ],
            "organelle_filtering_not_applied_before_statistics": [
                "若假设成立，过滤前 BAM 的细胞器比例较高，而过滤后核基因组 usable reads 明显恢复。",
                "若假设不成立，过滤前后比例变化不足以解释当前观测。",
            ],
            "short_fragment_readthrough": [
                "若假设成立，短片段区间与 Adapter Content 同步富集，clean FASTQ 或重剪切后该信号应下降。",
                "若假设不成立，Adapter 信号与片段长度无明显关联，且 clean FASTQ 中不持续存在。",
            ],
            "reference_genome_mismatch": [
                "若假设成立，更换正确参考或索引后 mapping/unique 应同步改善，未比对 reads 的物种归属会发生系统变化。",
                "若假设不成立，使用备选正确参考复算后 mapping/unique 不会得到有意义恢复。",
            ],
            "insufficient_effective_reads": [
                "若假设成立，reads 流失会集中在可定位的处理阶段，并与 FRiP、peak count 或相关性下降同向。",
                "若假设不成立，进入 peak calling 的 usable reads 仍充足，需要转向富集、背景或参数原因。",
            ],
            "high_background": [
                "若假设成立，IgG/Input 或 peak 外信号会升高，信噪比下降，且背景校正后 FRiP/peak 特异性改善。",
                "若假设不成立，对照与 peak 外背景不高，应优先检查目标富集强度或 peak calling 参数。",
            ],
            "missing_or_mismatched_control": [
                "若假设成立，修正 control 配对后背景估计和 peak 集合会发生方向一致的变化。",
                "若假设不成立，正确绑定 control 后主要结论应保持稳定。",
            ],
        }
        if cause_id in specific:
            return specific[cause_id]
        label = str(profile.get("label") or cause_id)
        actions = [str(item) for item in profile.get("verification_actions", []) if str(item).strip()]
        action = actions[0] if actions else "补充该假设对应的独立项目证据"
        return [
            f'若「{label}」成立，执行「{action}」后应观察到与该机制一致、可重复的方向性变化。',
            f'若「{label}」不成立，复核结果不会出现预期联动，应降低该原因排序并转向其他候选原因。',
        ]

    @staticmethod
    def dedupe_text(items: list[Any]) -> list[str]:
        values: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if text and text not in values:
                values.append(text)
        return values

    @staticmethod
    def cause_profile(cause_id: str) -> dict[str, Any]:
        profiles: dict[str, dict[str, Any]] = {
            "organelle_dna_background": {
                "label": "样本中细胞器 DNA 背景较高",
                "support_metrics": ["duplicate_rate_percent", "mapping_rate_percent", "unique_mapping_rate_percent"],
                "contradict_on_normal": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "context_rules": ["organelle_handling"],
                "missing_evidence": ["缺少 raw/clean reads 中细胞器 reads 的分阶段计数。", "缺少样本制备或细胞核提取质量记录。"],
                "verification_actions": ["分别统计 raw、trimmed、mapped BAM 中细胞器 reads 占比，定位升高发生在哪一步。", "复核样本裂解、细胞核纯化和起始材料状态，确认是否引入细胞器 DNA 背景。"],
                "downstream_impacts": ["可能压缩核基因组有效 reads，并传导到 unique mapping、FRiP、peak 和相关性。"],
            },
            "organelle_filtering_not_applied_before_statistics": {
                "label": "统计口径位于细胞器 reads 过滤之前",
                "support_metrics": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "contradict_on_normal": [],
                "context_rules": ["organelle_handling"],
                "missing_evidence": ["缺少该比例对应 BAM 阶段及分母定义。", "缺少细胞器过滤命令与统计命令的执行顺序。"],
                "verification_actions": ["核对 AlignmentQC 指标使用的 BAM、分母以及 organelle filtering 的先后顺序。", "对过滤前后 BAM 重算该比例，判断高值是否主要来自统计口径。"],
                "downstream_impacts": ["若只是过滤前统计口径，高比例不必然等同于下游有效 reads 同比例损失。"],
            },
            "sample_preparation_background": {
                "label": "样本制备引入的细胞器背景",
                "support_metrics": ["duplicate_rate_percent", "adapter_percent", "q30_ratio"],
                "contradict_on_normal": [],
                "context_rules": [],
                "missing_evidence": ["缺少裂解、细胞核分离、起始量和样本状态记录。"],
                "verification_actions": ["按样本批次对照裂解和细胞核纯化记录，并比较同批次样本的细胞器 reads。", "结合片段长度和文库复杂度判断是否存在制备阶段的系统性背景。"],
                "downstream_impacts": ["可能减少核基因组有效片段并增加样本间技术差异。"],
            },
            "reference_genome_mismatch": {
                "label": "参考基因组或版本不匹配",
                "support_metrics": ["mapping_rate_percent", "unique_mapping_rate_percent", "q30_ratio"],
                "contradict_on_normal": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "context_rules": ["reference_config"],
                "missing_evidence": ["缺少样本物种、参考版本与比对索引的一致性核对。"],
                "verification_actions": ["核对 species、reference、index 构建来源和染色体命名是否一致。", "抽取未比对 reads 重新比对到预期参考或污染库，比较归属变化。"],
                "downstream_impacts": ["会同时降低 mapping/unique，并减少可用于 peak calling 的有效 reads。"],
            },
            "adapter_or_low_quality_reads": {
                "label": "接头或低质量 reads 消耗",
                "support_metrics": ["adapter_percent", "q30_ratio", "mapping_rate_percent"],
                "contradict_on_normal": ["adapter_percent", "q30_ratio"],
                "context_rules": ["trimming_policy"],
                "missing_evidence": ["缺少 clean FASTQ 的 Adapter Content 和过滤前后保留率。"],
                "verification_actions": ["比较 raw/clean FastQC 的 Adapter Content、Q30 和 reads 保留率。", "核对 trimming 参数后重算 mapping，确认是否可恢复。"],
                "downstream_impacts": ["可能降低 mapping/unique，并进一步减少富集分析可用 reads。"],
            },
            "organelle_reads_dominant": {
                "label": "细胞器 reads 占用主要比对 reads",
                "support_metrics": ["mt_rate_percent", "mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent"],
                "contradict_on_normal": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "context_rules": ["organelle_handling"],
                "missing_evidence": ["缺少过滤前后核基因组有效 reads 计数。"],
                "verification_actions": ["按细胞器和核基因组分别统计 mapped/unique reads，并比较过滤前后变化。"],
                "downstream_impacts": ["可能压缩核基因组有效 reads，影响 FRiP、peak 和相关性。"],
            },
            "multi_mapping_or_repetitive_regions": {
                "label": "多重比对或重复区域占比较高",
                "support_metrics": ["mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent"],
                "contradict_on_normal": ["unique_mapping_rate_percent"],
                "context_rules": ["reference_config"],
                "missing_evidence": ["缺少 uniquely mapped、multi-mapped 和 unmapped reads 的拆分统计。"],
                "verification_actions": ["从比对日志拆分 unique、multi-mapped 和 unmapped reads，并核对 MAPQ 过滤口径。"],
                "downstream_impacts": ["会降低 unique reads，并影响后续定量和 peak 稳定性。"],
            },
            "short_fragment_readthrough": {
                "label": "短片段导致 adapter read-through",
                "support_metrics": ["fragment_size", "adapter_percent", "mapping_rate_percent", "unique_mapping_rate_percent"],
                "contradict_on_normal": [],
                "context_rules": ["trimming_policy"],
                "missing_evidence": ["缺少 fragment size 分布和 clean FASTQ adapter 证据。"],
                "verification_actions": ["联合查看 fragment size 与 raw/clean Adapter Content，确认 adapter 是否集中在短片段。"],
                "downstream_impacts": ["可能造成 reads 丢失并降低 mapping、unique 和有效富集 reads。"],
            },
            "adapter_trimming_parameter_mismatch": {
                "label": "trimming 参数或接头序列不匹配",
                "support_metrics": ["adapter_percent", "mapping_rate_percent", "q30_ratio"],
                "contradict_on_normal": ["adapter_percent"],
                "context_rules": ["trimming_policy"],
                "missing_evidence": ["缺少接头序列、overlap、错误率和最小保留长度参数。"],
                "verification_actions": ["核对接头序列与试剂盒，并用参数敏感性重跑小批 reads 比较保留率和 mapping。"],
                "downstream_impacts": ["可能留下接头或过度剪切，降低 clean reads 和比对效率。"],
            },
            "high_organelle_or_low_complexity_reads": {
                "label": "细胞器 reads 或低复杂度序列偏多",
                "support_metrics": ["mt_rate_percent", "duplicate_rate_percent", "unique_mapping_rate_percent"],
                "contradict_on_normal": ["mt_rate_percent", "duplicate_rate_percent"],
                "context_rules": ["organelle_handling"],
                "missing_evidence": ["缺少低复杂度、细胞器 reads 和重复序列的分类统计。"],
                "verification_actions": ["对未通过 trimming 或比对的 reads 做序列分类，并联合查看复杂度与细胞器占比。"],
                "downstream_impacts": ["可能减少可用于核基因组富集分析的有效 reads。"],
            },
            "library_construction_issue": {
                "label": "文库构建或起始材料问题",
                "support_metrics": ["duplicate_rate_percent", "q30_ratio", "mapping_rate_percent", "frip_ratio"],
                "contradict_on_normal": [],
                "context_rules": [],
                "missing_evidence": ["缺少起始量、PCR cycle、文库浓度和片段分布记录。"],
                "verification_actions": ["复核起始量、PCR cycle、文库浓度和片段分布，并与同批正常样本比较。"],
                "downstream_impacts": ["可能同时影响复杂度、比对和富集稳定性。"],
            },
            "low_library_complexity": {
                "label": "文库复杂度不足",
                "support_metrics": ["duplicate_rate_percent", "frip_ratio", "correlation"],
                "contradict_on_normal": ["duplicate_rate_percent"],
                "context_rules": ["dedup_policy"],
                "missing_evidence": ["缺少 NRF/PBC 或 estimated library size。"],
                "verification_actions": ["查看 NRF、PBC1/PBC2、estimated library size 和去重前后有效 reads。"],
                "downstream_impacts": ["可能降低 peak 定量稳定性和样本重复一致性。"],
            },
            "true_enrichment_duplication": {
                "label": "真实富集区域产生的生物学重复",
                "support_metrics": ["frip_ratio", "peak_count"],
                "contradict_on_normal": [],
                "context_rules": ["dedup_policy"],
                "missing_evidence": ["缺少 duplicates 在 peak 内外的分布和去重前后 FRiP 对比。"],
                "verification_actions": ["比较 duplicates 在 peak 内外的富集，并评估去重前后 FRiP/peak 稳定性。"],
                "downstream_impacts": ["若重复集中在真实 peak，简单删除可能损失真实信号。"],
            },
            "organelle_or_repetitive_reads": {
                "label": "细胞器或重复序列推高重复率",
                "support_metrics": ["mt_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent"],
                "contradict_on_normal": ["mt_rate_percent", "unique_mapping_rate_percent"],
                "context_rules": ["organelle_handling", "dedup_policy"],
                "missing_evidence": ["缺少 duplicates 的染色体和 MAPQ 分布。"],
                "verification_actions": ["按染色体、MAPQ 和细胞器/核基因组拆分 duplicates。"],
                "downstream_impacts": ["可能降低核基因组有效复杂度和定量稳定性。"],
            },
            "pcr_amplification_bias": {
                "label": "PCR 扩增偏倚",
                "support_metrics": ["duplicate_rate_percent", "frip_ratio", "correlation"],
                "contradict_on_normal": [],
                "context_rules": ["dedup_policy"],
                "missing_evidence": ["缺少 PCR cycle、起始量和文库复杂度统计。"],
                "verification_actions": ["核对 PCR cycle 与起始量，并比较同批次文库复杂度和 duplicates 分布。"],
                "downstream_impacts": ["可能放大少数片段并降低样本间定量一致性。"],
            },
            "insufficient_effective_reads": {
                "label": "核基因组有效 reads 不足",
                "support_metrics": ["mapping_rate_percent", "unique_mapping_rate_percent", "mt_rate_percent", "duplicate_rate_percent"],
                "contradict_on_normal": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "context_rules": [],
                "missing_evidence": ["缺少进入 peak calling 的 usable reads 绝对数量。"],
                "verification_actions": ["按 raw、clean、mapped、unique、去重后和 peak calling 输入逐级核算 reads。"],
                "downstream_impacts": ["可直接限制 FRiP、peak 数量和相关性稳定性。"],
            },
            "high_background": {
                "label": "背景信号较高",
                "support_metrics": ["frip_ratio", "peak_count", "peak_width", "tss_enrichment", "correlation"],
                "contradict_on_normal": ["frip_ratio", "correlation"],
                "context_rules": ["peak_calling_params"],
                "missing_evidence": ["缺少 IgG/Input、peak 外信号和信噪比分布。"],
                "verification_actions": ["比较实验样本与 IgG/Input 的 peak 内外信号、FRiP 和覆盖轨迹。"],
                "downstream_impacts": ["会降低 peak 特异性并削弱重复一致性。"],
            },
            "weak_target_enrichment": {
                "label": "目标蛋白富集较弱",
                "support_metrics": ["frip_ratio", "peak_count", "peak_width", "tss_enrichment", "correlation"],
                "contradict_on_normal": ["frip_ratio", "peak_count"],
                "context_rules": [],
                "missing_evidence": ["缺少阳性位点、抗体批次和目标蛋白预期富集模式。"],
                "verification_actions": ["检查阳性位点覆盖、抗体信息和目标蛋白预期 broad/narrow 富集模式。"],
                "downstream_impacts": ["可能导致 peak 数量少、FRiP 低和生物学解释受限。"],
            },
            "peak_calling_parameter_issue": {
                "label": "peak calling 参数或模式不适配",
                "support_metrics": ["frip_ratio", "peak_count"],
                "contradict_on_normal": [],
                "context_rules": ["peak_calling_params"],
                "missing_evidence": ["缺少 peak caller、阈值、broad/narrow 和 control 参数。"],
                "verification_actions": ["用符合目标蛋白模式的 peak caller/参数做小范围敏感性比较。"],
                "downstream_impacts": ["会改变 peak 集合，从而影响 FRiP、motif 和下游富集分析。"],
            },
            "missing_or_mismatched_control": {
                "label": "对照缺失或角色不匹配",
                "support_metrics": ["frip_ratio", "peak_count", "correlation"],
                "contradict_on_normal": [],
                "context_rules": ["peak_calling_params"],
                "missing_evidence": ["缺少 IgG/Input/control 角色和 peak calling 对照绑定关系。"],
                "verification_actions": ["核对样本角色、control 配对和 peak caller 实际使用的对照文件。"],
                "downstream_impacts": ["可能造成背景估计偏差并改变 peak 集合。"],
            },
            "weak_signal_noise_dominated_bins": {
                "label": "弱信号导致相关性被噪音主导",
                "support_metrics": ["frip_ratio", "peak_count", "mapping_rate_percent"],
                "contradict_on_normal": ["frip_ratio", "peak_count"],
                "context_rules": [],
                "missing_evidence": ["缺少相关性使用的 bin/peak 特征空间和信号分布。"],
                "verification_actions": ["分别在全基因组 bins 和共识 peaks 上重算相关性，并过滤低信号区域。"],
                "downstream_impacts": ["会降低重复一致性并影响差异分析可靠性。"],
            },
            "sample_role_or_group_mismatch": {
                "label": "样本角色或分组不匹配",
                "support_metrics": ["correlation"],
                "contradict_on_normal": [],
                "context_rules": [],
                "missing_evidence": ["缺少样本分组、处理条件和 biological replicate 定义。"],
                "verification_actions": ["核对 samplelist 中的组别、处理条件和重复关系后分层比较相关性。"],
                "downstream_impacts": ["错误分组会造成不恰当的重复一致性判断。"],
            },
            "upstream_qc_or_enrichment_issue": {
                "label": "上游 QC 或富集问题传导",
                "support_metrics": ["mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio", "peak_count"],
                "contradict_on_normal": ["mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio"],
                "context_rules": [],
                "missing_evidence": ["缺少最低相关样本的完整 QC 与富集链路。"],
                "verification_actions": ["对最低相关样本逐级比较 mapping、unique、FRiP、peak count 和覆盖轨迹。"],
                "downstream_impacts": ["可使相关性下降并削弱重复合并与差异分析。"],
            },
            "incorrect_correlation_feature_space": {
                "label": "相关性特征空间或参数不适配",
                "support_metrics": ["correlation", "peak_count"],
                "contradict_on_normal": [],
                "context_rules": [],
                "missing_evidence": ["缺少相关性输入矩阵、bin size、归一化和过滤参数。"],
                "verification_actions": ["核对相关性输入矩阵、bin size、归一化，并在共识 peak 上复算。"],
                "downstream_impacts": ["可能产生与真实生物学重复关系不一致的相关性结果。"],
            },
        }
        generic = {
            "label": cause_id.replace("_", " "),
            "support_metrics": [],
            "contradict_on_normal": [],
            "context_rules": [],
            "missing_evidence": ["缺少能够区分该候选原因与其他原因的独立项目证据。"],
            "verification_actions": ["补充与该假设直接对应的项目配置、原始质控或分阶段统计后再判断。"],
            "downstream_impacts": [],
        }
        return profiles.get(cause_id, generic)

    @classmethod
    def fallback_metric_evidence_plan(cls, target_metrics: Any, question: str) -> dict[str, Any]:
        _canonical = cls.canonical_metric_key
        metrics = [_canonical(item) for item in (target_metrics or []) if str(item).strip()]
        normalized_question = str(question or "").lower()
        if not metrics:
            if any(term in normalized_question for term in ("线粒体", "叶绿体", "质体", "细胞器", "organelle", "mitochond")):
                metrics.append("mt_rate_percent")
            elif "adapter" in normalized_question:
                metrics.append("adapter_percent")
            elif "frip" in normalized_question:
                metrics.append("frip_ratio")
            elif "unique" in normalized_question:
                metrics.append("unique_mapping_rate_percent")
            elif "mapping" in normalized_question:
                metrics.append("mapping_rate_percent")
            elif "duplicate" in normalized_question:
                metrics.append("duplicate_rate_percent")
            elif "corr" in normalized_question or "spearman" in normalized_question:
                metrics.append("correlation")

        templates = {
            "adapter_percent": {
                "primary": ["adapter_percent", "q30_ratio"],
                "upstream": ["fragment_size", "read_length", "cutadapt_params"],
                "parallel": ["mt_rate_percent", "duplicate_rate_percent"],
                "downstream": ["mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio", "correlation"],
                "candidate_causes": [
                    "short_fragment_readthrough",
                    "adapter_trimming_parameter_mismatch",
                    "high_organelle_or_low_complexity_reads",
                    "library_construction_issue",
                ],
            },
            "mapping_rate_percent": {
                "primary": ["mapping_rate_percent", "unique_mapping_rate_percent"],
                "upstream": ["adapter_percent", "q30_ratio", "reference_genome", "mt_rate_percent"],
                "parallel": ["duplicate_rate_percent"],
                "downstream": ["frip_ratio", "peak_count", "correlation"],
                "candidate_causes": [
                    "reference_genome_mismatch",
                    "adapter_or_low_quality_reads",
                    "organelle_reads_dominant",
                    "multi_mapping_or_repetitive_regions",
                ],
            },
            "unique_mapping_rate_percent": {
                "primary": ["unique_mapping_rate_percent", "mapping_rate_percent"],
                "upstream": ["adapter_percent", "q30_ratio", "reference_genome", "mt_rate_percent"],
                "parallel": ["duplicate_rate_percent"],
                "downstream": ["frip_ratio", "peak_count", "correlation"],
                "candidate_causes": [
                    "multi_mapping_or_repetitive_regions",
                    "organelle_reads_dominant",
                    "reference_genome_mismatch",
                    "low_library_complexity",
                ],
            },
            "mt_rate_percent": {
                "primary": ["mt_rate_percent"],
                "upstream": ["organelle_chroms", "sample_preparation", "filtering_policy"],
                "parallel": ["mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent"],
                "downstream": ["frip_ratio", "peak_count", "correlation"],
                "candidate_causes": [
                    "organelle_dna_background",
                    "organelle_filtering_not_applied_before_statistics",
                    "sample_preparation_background",
                ],
            },
            "duplicate_rate_percent": {
                "primary": ["duplicate_rate_percent"],
                "upstream": ["library_complexity", "pcr_cycles", "mt_rate_percent"],
                "parallel": ["mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio"],
                "downstream": ["peak_count", "correlation"],
                "candidate_causes": [
                    "low_library_complexity",
                    "true_enrichment_duplication",
                    "organelle_or_repetitive_reads",
                    "pcr_amplification_bias",
                ],
            },
            "frip_ratio": {
                "primary": ["frip_ratio", "peak_count"],
                "upstream": ["mapping_rate_percent", "unique_mapping_rate_percent", "duplicate_rate_percent", "mt_rate_percent"],
                "parallel": ["peak_width", "reads_in_peaks", "background_signal"],
                "downstream": ["correlation"],
                "candidate_causes": [
                    "insufficient_effective_reads",
                    "high_background",
                    "weak_target_enrichment",
                    "peak_calling_parameter_issue",
                    "missing_or_mismatched_control",
                ],
            },
            "correlation": {
                "primary": ["correlation"],
                "upstream": ["sample_group", "mapping_rate_percent", "unique_mapping_rate_percent", "frip_ratio", "peak_count"],
                "parallel": ["pca", "peak_overlap"],
                "downstream": ["replicate_consistency"],
                "candidate_causes": [
                    "weak_signal_noise_dominated_bins",
                    "sample_role_or_group_mismatch",
                    "upstream_qc_or_enrichment_issue",
                    "incorrect_correlation_feature_space",
                ],
            },
        }
        return {metric: templates[metric] for metric in metrics if metric in templates}


project_cause_analysis_service = ProjectCauseAnalysisService()
