from __future__ import annotations

import json
import re
from typing import Any

from multi_agent.backed.app.infrastructure.ai.openai_client import SUB_MODEL_NAME, sub_model_client
from multi_agent.backed.app.infrastructure.logging.logger import logger


class BusinessSemanticGuardService:
    MAX_ANSWER_CHARS = 5000
    MAX_CONTEXT_CHARS = 3500
    RULE_ALIASES = {
        "1": "no_project_delivery_judgement",
        "2": "must_only_report_review_indicators",
        "3": "no_unverified_threshold_judgement",
        "4": "must_stay_on_user_question",
        "5": "adapter_processing_stage_mismatch",
        "6": "species_organelle_mismatch",
        "7": "metric_denominator_mismatch",
        "8": "sample_role_mismatch",
        "9": "evidence_presence_mismatch",
        "10": "target_metric_value_omission",
    }
    METRIC_ALIASES = {
        "adapter_percent": ("adapter", "接头检出率", "接头比例"),
        "q30_ratio": ("q30",),
        "mapping_rate_percent": ("mapping", "总比对率", "比对率"),
        "unique_mapping_rate_percent": ("unique", "唯一比对率"),
        "duplicate_rate_percent": ("duplicate", "duplication", "重复率"),
        "mt_rate_percent": (
            "chrmt",
            "mt_ratio",
            "mt ratio",
            "alignmentqc",
            "线粒体 reads",
            "线粒体比例",
            "细胞器 reads",
            "细胞器比例",
        ),
        "frip_ratio": ("frip",),
        "correlation": ("correlation", "spearman", "相关性"),
    }
    UNVERIFIED_JUDGEMENT_TERMS = (
        "异常高",
        "异常低",
        "偏高",
        "偏低",
        "极高",
        "极低",
        "过高",
        "过低",
        "远高于",
        "远低于",
        "超标",
        "不达标",
        "严重不足",
    )

    @classmethod
    def should_check(cls, *, answer: str, analysis_result: dict[str, Any], question_route: dict[str, Any] | None = None) -> bool:
        if not str(answer or "").strip():
            return False
        question = str(analysis_result.get("question") or "").strip().lower()
        low_risk_question_terms = (
            "是什么",
            "什么意思",
            "怎么计算",
            "如何计算",
            "计算公式",
            "公式",
            "指标具体",
        )
        if any(term in question for term in low_risk_question_terms):
            return False
        has_project_evidence = bool(
            analysis_result.get("tool_diagnostics")
            or analysis_result.get("evidence_chain")
            or analysis_result.get("evidence_request_status")
        )
        narrow_metric_terms = (
            "adapter",
            "mapping",
            "unique",
            "duplicate",
            "dup",
            "frip",
            "peak",
            "q30",
            "spearman",
            "pearson",
            "线粒体",
            "叶绿体",
            "接头",
            "比对率",
            "重复率",
            "相关性",
        )
        broad_or_delivery_terms = (
            "整体",
            "总结",
            "报告",
            "合格",
            "不合格",
            "交付",
            "下游",
            "质量怎么样",
            "项目怎么样",
        )
        if (
            has_project_evidence
            and any(term in question for term in narrow_metric_terms)
            and not any(term in question for term in broad_or_delivery_terms)
        ):
            return False
        route = str(cls._route_get(question_route, "route") or "").strip()
        intent = str(cls._route_get(question_route, "intent") or "").strip()
        question_type = str(analysis_result.get("question_type") or "").strip()
        report_mode = str(analysis_result.get("report_mode") or "").strip()
        high_risk = {
            "overview",
            "diagnostic",
            "qc",
            "alignment",
            "frip",
            "peak",
            "correlation",
        }
        return (
            route in {"project_qa", "ai_report_summary", ""}
            or intent in {"report_summary", "project_analysis"}
            or question_type in high_risk
            or report_mode == "existing_html_report_summary"
        )

    @classmethod
    async def validate(
        cls,
        *,
        answer: str,
        analysis_result: dict[str, Any],
        question_route: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deterministic_violations = cls._deterministic_violations(
            answer=answer,
            analysis_result=analysis_result,
        )
        if deterministic_violations:
            return {
                "passed": False,
                "action": "repair",
                "severity": cls._max_severity(deterministic_violations),
                "violations": deterministic_violations,
                "semantic_checked": True,
                "check_mode": "deterministic_scientific_rules",
            }
        if not cls.should_check(answer=answer, analysis_result=analysis_result, question_route=question_route):
            return {"passed": True, "action": "pass", "severity": "none", "violations": [], "semantic_checked": False}

        messages = cls._build_messages(answer=answer, analysis_result=analysis_result, question_route=question_route)
        try:
            response = await sub_model_client.chat.completions.create(
                model=SUB_MODEL_NAME,
                messages=messages,
                temperature=0,
                max_tokens=500,
                stream=False,
            )
            raw = response.choices[0].message.content or ""
            payload = cls._parse_json(raw)
        except Exception as exc:
            logger.warning("semantic_guard failed: %s", str(exc))
            return {
                "passed": True,
                "action": "pass",
                "severity": "none",
                "violations": [],
                "semantic_checked": False,
                "error": str(exc),
            }

        violations = payload.get("violations") if isinstance(payload, dict) else []
        if not isinstance(violations, list):
            violations = []
        normalized = []
        for item in violations:
            if not isinstance(item, dict):
                continue
            rule = str(item.get("rule") or "semantic_guard_violation").strip()
            rule = cls._normalize_rule(rule)
            text = str(item.get("text") or "").strip()
            reason = str(item.get("reason") or "").strip()
            severity = str(item.get("severity") or "moderate").strip()
            normalized.append(
                {
                    "rule": rule,
                    "message": reason or f"语义校验发现违规：{rule}",
                    "matched": text,
                    "severity": severity if severity in {"minor", "moderate", "severe"} else "moderate",
                }
            )

        severity = cls._max_severity(normalized)
        return {
            "passed": not normalized,
            "action": "pass" if not normalized else "repair",
            "severity": severity,
            "violations": normalized,
            "semantic_checked": True,
        }

    @classmethod
    def _build_messages(
        cls,
        *,
        answer: str,
        analysis_result: dict[str, Any],
        question_route: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        context = cls._build_context(analysis_result)
        system_prompt = (
            "你是项目分析回答的语义校验者，只输出 JSON，不输出解释文本。"
            "任务：判断回答是否违反项目分析规则。"
            "允许内容："
            "A. 只有 evidence_chain 中存在结构化项目阈值且 severity=warning/critical 时，才允许指出单个指标偏高、偏低或异常。"
            "B. 项目阈值未确认时，只允许报告观测值；可以另行说明通用参考信息，但不得据此给当前项目贴高/低、异常或通过标签。"
            "C. 允许结合指标解释可能风险、可能原因和后续复核方向。"
            "违规规则："
            "1. 不允许判断整体项目合格/不合格、好/坏、整体质量没问题、可以放心进入后续分析、适合/不适合下游分析。"
            "2. 不允许把单项指标高低直接推导成整体项目交付结论。"
            "3. 未从项目文件确认阈值时，不允许把通用阈值写成项目专属标准、验收标准或交付判定依据。"
            "4. 回答必须紧贴用户问题，不要扩展到无关模块或泛泛背景。"
            "5. ReadsQC Adapter/raw reads 只能说明原始 reads 检测到接头相关序列；没有 clean FASTQ/FastQC 或 trimming 后证据时，不允许称为 clean reads 接头残留。"
            "6. 细胞器指标必须与项目 species/reference 一致；hg38/hg19/GRCh/mm10 等动物项目不允许解释为叶绿体或质体 reads。"
            "7. 数值解释必须与证据中的处理阶段、分子/分母和单位一致。"
            "8. 未确认样本分组和 biological replicate 关系时，不允许把任意两个样本直接判定为重复不一致。"
            "9. evidence_chain 已提供目标指标样本值时，不允许声称该指标未读取、缺失或无法给出具体数值。"
            "10. 单一目标指标的原因分析必须引用 evidence_chain 中已有的样本观测值，不能只给通用背景。"
            "返回 JSON 格式："
            "{\"passed\": boolean, \"violations\": [{\"rule\": string, \"text\": string, \"reason\": string, \"severity\": \"minor|moderate|severe\"}]}。"
        )
        user_prompt = (
            "## 用户问题\n"
            f"{analysis_result.get('question', '')}\n\n"
            "## 问题路由\n"
            f"{json.dumps(cls._route_payload(question_route), ensure_ascii=False)[:1000]}\n\n"
            "## 项目结构化上下文\n"
            f"{context}\n\n"
            "## 待校验回答\n"
            f"{str(answer or '')[:cls.MAX_ANSWER_CHARS]}\n\n"
            "只返回 JSON。"
        )
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    @classmethod
    def _build_context(cls, analysis_result: dict[str, Any]) -> str:
        diagnosis = analysis_result.get("diagnosis_summary") or {}
        parts: list[str] = []
        if isinstance(diagnosis, dict):
            for key in ("conclusions", "evidence", "possible_causes"):
                value = diagnosis.get(key)
                if isinstance(value, list) and value:
                    parts.append(f"{key}: " + "; ".join(str(item) for item in value[:6]))
        limits = analysis_result.get("analysis_limits") or []
        if limits:
            parts.append("analysis_limits: " + "; ".join(str(item) for item in limits[:6]))
        warnings = analysis_result.get("warnings") or []
        if warnings:
            parts.append("warnings: " + "; ".join(str(item) for item in warnings[:6]))
        project_context = analysis_result.get("project_context") or {}
        config = project_context.get("config") if isinstance(project_context, dict) else {}
        if isinstance(config, dict):
            species = config.get("species") or config.get("genome") or config.get("reference")
            if species:
                parts.append(f"species/reference: {species}")
        evidence_chain = cls._scientific_evidence_entries(analysis_result)
        if isinstance(evidence_chain, list):
            compact_evidence = []
            for item in evidence_chain[:10]:
                if not isinstance(item, dict):
                    continue
                compact_evidence.append(
                    {
                        "metric_key": item.get("metric_key"),
                        "sample": item.get("sample"),
                        "value": item.get("display_value"),
                        "denominator": item.get("denominator"),
                        "severity": item.get("severity"),
                        "threshold_source": item.get("threshold_source"),
                        "definition": item.get("definition"),
                    }
                )
            if compact_evidence:
                parts.append("evidence_chain: " + json.dumps(compact_evidence, ensure_ascii=False))
        return "\n".join(parts)[:cls.MAX_CONTEXT_CHARS]

    @classmethod
    def _deterministic_violations(
        cls,
        *,
        answer: str,
        analysis_result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        text = str(answer or "")
        violations: list[dict[str, Any]] = []
        evidence_chain = cls._scientific_evidence_entries(analysis_result)
        has_raw_adapter_metric = any(
            isinstance(item, dict)
            and item.get("metric_key") == "adapter_percent"
            and "raw" in str(item.get("denominator") or "").lower()
            for item in evidence_chain
        )
        if has_raw_adapter_metric and cls._contains_unqualified_term(
            text,
            ("接头残留", "adapter remains", "residual adapter"),
            (
                "不等于",
                "不能",
                "无法",
                "不代表",
                "并非",
                "尚不能",
                "未证明",
                "未确认",
                "不能确认",
                "没有证据",
                "缺乏证据",
                "禁止",
            ),
        ):
            violations.append(
                {
                    "rule": "adapter_processing_stage_mismatch",
                    "message": "Adapter 指标分母为 raw reads，当前证据不能证明 trimming 后 clean reads 仍有接头残留。",
                    "matched": "接头残留",
                    "severity": "severe",
                }
            )

        species = cls._project_species(analysis_result).lower()
        animal_tokens = ("hg", "grch", "human", "mm", "grcm", "mouse", "rn", "rat")
        if any(token in species for token in animal_tokens) and any(
            term in text.lower()
            for term in ("叶绿体", "质体", "chloroplast", "plastid")
        ):
            violations.append(
                {
                    "rule": "species_organelle_mismatch",
                    "message": f"项目 species/reference={species or '-'}，细胞器指标应解释为线粒体而不是叶绿体/质体。",
                    "matched": "叶绿体/质体",
                    "severity": "severe",
                }
            )
        seen_metrics: set[str] = set()
        for item in evidence_chain:
            if not isinstance(item, dict):
                continue
            metric_key = str(item.get("metric_key") or "")
            if metric_key in seen_metrics or metric_key not in cls.METRIC_ALIASES:
                continue
            if not item.get("threshold_needs_project_validation") and item.get("severity") != "unverified_threshold":
                continue
            matched_segment = cls._unverified_judgement_segment(text, cls.METRIC_ALIASES[metric_key])
            if not matched_segment:
                continue
            seen_metrics.add(metric_key)
            violations.append(
                {
                    "rule": "no_unverified_threshold_judgement",
                    "message": (
                        f"{metric_key} 的项目专属阈值未确认，只能报告观测值和证据限制，"
                        "不能直接写成偏高、偏低、异常或超标。"
                    ),
                    "matched": matched_segment,
                    "severity": "severe",
                }
            )
        seen_present_metrics: set[str] = set()
        for item in evidence_chain:
            if not isinstance(item, dict):
                continue
            metric_key = str(item.get("metric_key") or "")
            if metric_key in seen_present_metrics or metric_key not in cls.METRIC_ALIASES:
                continue
            if item.get("value") is None and not item.get("display_value"):
                continue
            aliases = (metric_key, *cls.METRIC_ALIASES[metric_key])
            matched_segment = cls._false_missing_evidence_segment(text, aliases)
            if not matched_segment:
                continue
            seen_present_metrics.add(metric_key)
            violations.append(
                {
                    "rule": "evidence_presence_mismatch",
                    "message": f"证据链已经提供 {metric_key} 的样本观测值，回答不得声称该指标缺失或无法获得数值。",
                    "matched": matched_segment,
                    "severity": "severe",
                }
            )
        question = str(analysis_result.get("question") or "").lower()
        formula_or_definition_terms = (
            "是什么",
            "什么意思",
            "怎么计算",
            "如何计算",
            "计算公式",
            "公式",
        )
        analysis_plan = analysis_result.get("analysis_plan") or {}
        planned_target_metrics = cls._normalized_target_metrics(analysis_plan)
        question_target_metrics = {
            metric_key
            for metric_key, aliases in cls.METRIC_ALIASES.items()
            if any(alias.lower() in question for alias in (metric_key, *aliases))
        }
        target_metrics = (
            question_target_metrics
            if len(question_target_metrics) == 1
            else planned_target_metrics
        )
        if len(target_metrics) == 1 and not any(term in question for term in formula_or_definition_terms):
            target_metric = next(iter(target_metrics))
            target_entries = [
                item
                for item in evidence_chain
                if str(item.get("metric_key") or "") == target_metric
                and (item.get("value") is not None or item.get("display_value"))
            ][:6]
            missing_values = [
                str(item.get("display_value") or item.get("value"))
                for item in target_entries
                if str(item.get("display_value") or item.get("value")) not in text
            ]
            if target_entries and missing_values:
                violations.append(
                    {
                        "rule": "target_metric_value_omission",
                        "message": (
                            f"回答未完整引用目标指标 {target_metric} 的结构化样本值："
                            f"{', '.join(missing_values)}。"
                        ),
                        "matched": ", ".join(missing_values),
                        "severity": "severe",
                    }
                )
        return violations

    @classmethod
    def _scientific_evidence_entries(cls, analysis_result: dict[str, Any]) -> list[dict[str, Any]]:
        evidence_chain = [
            item
            for item in (analysis_result.get("evidence_chain") or [])
            if isinstance(item, dict)
        ]
        if evidence_chain:
            return evidence_chain
        parsed_metrics = analysis_result.get("parsed_metrics") or {}
        fallback: list[dict[str, Any]] = []
        section_metrics = {
            "qc": ("adapter_percent", "q30_ratio"),
            "alignment": (
                "mapping_rate_percent",
                "unique_mapping_rate_percent",
                "duplicate_rate_percent",
                "mt_rate_percent",
            ),
            "frip": ("frip_ratio",),
        }
        for section, metric_keys in section_metrics.items():
            rows = parsed_metrics.get(section) or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for metric_key in metric_keys:
                    value = row.get(metric_key)
                    if value is None:
                        continue
                    fallback.append(
                        {
                            "metric_key": metric_key,
                            "sample": row.get("sample") or "-",
                            "value": value,
                            "display_value": str(value),
                            "severity": "unverified_threshold",
                            "threshold_needs_project_validation": True,
                            "denominator": "raw reads" if metric_key == "adapter_percent" else "",
                        }
                    )
        correlation = parsed_metrics.get("correlation") or {}
        min_pair = correlation.get("min_pair") if isinstance(correlation, dict) else None
        if min_pair and len(min_pair) >= 3:
            fallback.append(
                {
                    "metric_key": "correlation",
                    "sample": f"{min_pair[0]} vs {min_pair[1]}",
                    "value": min_pair[2],
                    "display_value": str(min_pair[2]),
                    "severity": "unverified_threshold",
                    "threshold_needs_project_validation": True,
                }
            )
        return fallback

    @staticmethod
    def _normalized_target_metrics(analysis_plan: dict[str, Any]) -> set[str]:
        aliases = {
            "chrmt_pt_rate_percent": "mt_rate_percent",
            "chrmt_rate_percent": "mt_rate_percent",
            "mt_ratio": "mt_rate_percent",
            "chrmt/pt": "mt_rate_percent",
            "frip": "frip_ratio",
        }
        return {
            aliases.get(str(item or "").strip().lower(), str(item or "").strip().lower())
            for item in (analysis_plan.get("target_metrics") or [])
            if str(item or "").strip()
        }

    @classmethod
    def _unverified_judgement_segment(cls, text: str, aliases: tuple[str, ...]) -> str:
        segments = re.split(r"[\n。！？!?；;]+", str(text or ""))
        negations = (
            "不能判定",
            "无法判断",
            "不应称为",
            "不能称为",
            "不代表",
            "未确认",
            "没有项目阈值",
            "缺少项目阈值",
            "仅为观测",
            "只是观测",
        )
        for segment in segments:
            lowered = segment.lower()
            if not any(alias.lower() in lowered for alias in aliases):
                continue
            if cls._contains_unqualified_term(
                segment,
                cls.UNVERIFIED_JUDGEMENT_TERMS,
                negations,
            ):
                return segment.strip()[:240]
        return ""

    @staticmethod
    def _false_missing_evidence_segment(text: str, aliases: tuple[str, ...]) -> str:
        missing_terms = (
            "缺少可直接读取",
            "未读取到",
            "尚未读取到",
            "尚未读到",
            "未能读取到",
            "未能成功读取",
            "未能提取",
            "没有读取到",
            "没有读到",
            "未读到具体",
            "无法读取",
            "无法给出具体",
            "无法引用",
            "没有具体数值",
            "缺少指标结果",
            "指标结果缺失",
        )
        for segment in re.split(r"[\n。！？!?；;]+", str(text or "")):
            lowered = segment.lower()
            if not any(alias.lower() in lowered for alias in aliases):
                continue
            if any(term in segment for term in missing_terms):
                return segment.strip()[:240]
        return ""

    @classmethod
    def repair_known_violations(
        cls,
        *,
        answer: str,
        analysis_result: dict[str, Any],
        violations: list[dict[str, Any]],
    ) -> str:
        repaired = str(answer or "")
        rules = {str(item.get("rule") or "") for item in violations if isinstance(item, dict)}
        species = cls._project_species(analysis_result).lower()
        animal_tokens = ("hg", "grch", "human", "mm", "grcm", "mouse", "rn", "rat")
        if "species_organelle_mismatch" in rules and any(token in species for token in animal_tokens):
            replacements = (
                ("线粒体/叶绿体（质体）", "线粒体"),
                ("线粒体/叶绿体(质体)", "线粒体"),
                ("线粒体或叶绿体/质体", "线粒体"),
                ("线粒体和叶绿体/质体", "线粒体"),
                ("线粒体/叶绿体", "线粒体"),
                ("叶绿体/质体", "线粒体"),
                ("叶绿体或质体", "线粒体"),
                ("叶绿体", "线粒体"),
                ("质体", "线粒体"),
                ("chloroplast", "mitochondrial"),
                ("plastid", "mitochondrial"),
            )
            for source, target in replacements:
                repaired = repaired.replace(source, target)
                repaired = repaired.replace(source.capitalize(), target)
            repaired = repaired.replace("线粒体或线粒体", "线粒体")
            repaired = repaired.replace("线粒体和线粒体", "线粒体")
            repaired = repaired.replace("植物样本", f"{species or '当前'} 样本")
        return repaired

    @staticmethod
    def _contains_unqualified_term(
        text: str,
        terms: tuple[str, ...],
        negations: tuple[str, ...],
    ) -> bool:
        lowered = str(text or "").lower()
        for term in terms:
            normalized_term = term.lower()
            start = 0
            while True:
                index = lowered.find(normalized_term, start)
                if index < 0:
                    break
                context = lowered[max(0, index - 36): index + len(normalized_term) + 40]
                if not any(negation.lower() in context for negation in negations):
                    return True
                start = index + len(normalized_term)
        return False

    @staticmethod
    def _project_species(analysis_result: dict[str, Any]) -> str:
        project_context = analysis_result.get("project_context") or {}
        if not isinstance(project_context, dict):
            return ""
        config = project_context.get("config") or {}
        if not isinstance(config, dict):
            return ""
        return str(config.get("species") or config.get("genome") or config.get("reference") or "").strip()

    @staticmethod
    def _route_get(question_route: Any, key: str) -> Any:
        if isinstance(question_route, dict):
            return question_route.get(key)
        return getattr(question_route, key, None)

    @classmethod
    def _route_payload(cls, question_route: Any) -> dict[str, Any]:
        if isinstance(question_route, dict):
            return question_route
        if question_route is None:
            return {}
        payload: dict[str, Any] = {}
        for key in ("intent", "route", "requires_project", "target_metrics", "question_tags"):
            value = cls._route_get(question_route, key)
            if value is not None:
                payload[key] = value
        return payload

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            text = match.group(0)
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _normalize_rule(cls, rule: str) -> str:
        normalized = str(rule or "").strip()
        if normalized in cls.RULE_ALIASES:
            return cls.RULE_ALIASES[normalized]
        prefix = normalized.split(".", 1)[0].strip()
        if prefix in cls.RULE_ALIASES:
            return cls.RULE_ALIASES[prefix]
        return normalized or "semantic_guard_violation"

    @staticmethod
    def _max_severity(violations: list[dict[str, Any]]) -> str:
        if not violations:
            return "none"
        rank = {"minor": 1, "moderate": 2, "severe": 3}
        return max((str(item.get("severity") or "moderate") for item in violations), key=lambda value: rank.get(value, 0))


business_semantic_guard_service = BusinessSemanticGuardService()
