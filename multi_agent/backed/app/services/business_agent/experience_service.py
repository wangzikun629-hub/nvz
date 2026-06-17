from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from multi_agent.backed.app.services.project_memory_service import project_memory_service
from multi_agent.backed.app.services.business_agent.workspace import ProjectWorkspace


@dataclass(frozen=True)
class ExperienceRule:
    rule_key: str
    source: str
    matched_text: str
    frequency: int
    priority_type: str
    confidence: float
    activation_conditions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_key": self.rule_key,
            "source": self.source,
            "matched_text": self.matched_text,
            "frequency": self.frequency,
            "priority_type": self.priority_type,
            "confidence": self.confidence,
            "activation_conditions": self.activation_conditions,
        }


class ExperienceService:
    GLOBAL_EXPERIENCE_LIMIT = 300
    GLOBAL_SIMILAR_CASE_LIMIT = 5
    RULE_KEYWORDS = {
        "frip": "metric",
        "q30": "metric",
        "adapter": "metric",
        "alignment": "metric",
        "mapping": "metric",
        "duplicate": "metric",
        "spike": "metric",
        "peak": "metric",
        "motif": "metric",
        "correlation": "metric",
        "diff": "metric",
        "mt": "metric",
        "chrmt": "metric",
        "warning": "warning",
    }
    RULE_TO_EVIDENCE_KEYWORDS = {
        "frip": ["FRiP.xls", "peakFrip"],
        "q30": ["ReadsQC.xls", "ReadsQC"],
        "adapter": ["ReadsQC.xls", "ReadsQC"],
        "alignment": ["AlignmentQC.xls", "AlignmentQC"],
        "mapping": ["AlignmentQC.xls", "AlignmentQC"],
        "duplicate": ["AlignmentQC.xls", "AlignmentQC"],
        "spike": ["spikein_align.xls", "Spikein"],
        "peak": ["Samples_peak_number_stat.xls", "PeakStat"],
        "motif": ["_meme.txt", "Motify", "Motif"],
        "correlation": ["spearman_Corr_readCounts.tab", "Correlation"],
        "diff": ["final_anno", "GO_up", "GO_down", "Pathway", "DiffAnalysis"],
        "mt": ["AlignmentQC.xls", "chrMT"],
        "chrmt": ["AlignmentQC.xls", "chrMT"],
    }

    def __init__(self) -> None:
        app_root = Path(__file__).resolve().parents[2]
        self._global_experience_path = app_root / "project_memories" / "_global_experience.json"
        self._global_experience_path.parent.mkdir(parents=True, exist_ok=True)

    def load_experience(self, project_id: str) -> dict[str, Any]:
        memory = project_memory_service.load_memory(project_id)
        return {
            "project_id": project_id,
            # Generated prose is not reusable evidence. Only validated facts and
            # evidence history may influence later planning.
            "last_report": None,
            "latest_findings": memory.get("latest_findings", []),
            "latest_warnings": memory.get("latest_warnings", []),
            "recent_questions": memory.get("recent_questions", []),
            "evidence_history": memory.get("evidence_history", []),
            "updated_at": memory.get("updated_at"),
        }

    def refresh_rule_library(
        self,
        *,
        workspace: ProjectWorkspace,
        experience_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rules = experience_summary.get("structured_experience_rules", []) or []
        payload = {
            "project_id": workspace.project_id,
            "updated_at": experience_summary.get("updated_at"),
            "rules": rules,
        }
        workspace.experience_rules_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return rules

    def load_rule_library(self, workspace: ProjectWorkspace) -> list[dict[str, Any]]:
        path = workspace.experience_rules_path
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return payload.get("rules", []) or []

    def summarize_experience(self, project_id: str) -> dict[str, Any]:
        experience = self.load_experience(project_id)
        question_tags: list[str] = []
        evidence_history = experience.get("evidence_history", []) or []
        evidence_file_counter: Counter[str] = Counter()
        question_type_counter: Counter[str] = Counter()
        rule_counter: Counter[str] = Counter()
        rule_examples: dict[str, tuple[str, str]] = {}

        for item in evidence_history:
            question_type = str(item.get("question_type") or "").strip()
            if question_type:
                question_type_counter[question_type] += 1
                if question_type not in question_tags:
                    question_tags.append(question_type)
            for evidence_file in item.get("evidence_files", []) or []:
                normalized = str(evidence_file).strip()
                if normalized:
                    evidence_file_counter[normalized] += 1
            for finding in item.get("automatic_findings", []) or []:
                self._collect_rules(rule_counter, rule_examples, str(finding), source="finding")
            for warning in item.get("warnings", []) or []:
                self._collect_rules(rule_counter, rule_examples, str(warning), source="warning")

        for finding in experience.get("latest_findings", []) or []:
            self._collect_rules(rule_counter, rule_examples, str(finding), source="latest_finding")
        for warning in experience.get("latest_warnings", []) or []:
            self._collect_rules(rule_counter, rule_examples, str(warning), source="latest_warning")

        structured_rules = self._build_structured_rules(rule_counter, rule_examples)
        global_summary = self.summarize_global_experience(
            project_id=project_id,
            question_tags=question_tags,
        )

        return {
            "project_id": project_id,
            "has_experience": bool(
                experience.get("latest_findings")
                or experience.get("recent_questions")
                or experience.get("evidence_history")
            ),
            "latest_findings": experience.get("latest_findings", [])[:10],
            "latest_warnings": experience.get("latest_warnings", [])[:10],
            "recent_questions": experience.get("recent_questions", [])[-10:],
            "last_report_excerpt": "",
            "common_evidence_files": [item for item, _ in evidence_file_counter.most_common(8)],
            "common_question_types": [item for item, _ in question_type_counter.most_common(5)],
            "experience_rules": [rule.rule_key for rule in structured_rules],
            "structured_experience_rules": [rule.to_dict() for rule in structured_rules],
            **global_summary,
            "updated_at": experience.get("updated_at"),
        }

    def summarize_global_experience(
        self,
        *,
        project_id: str,
        question_tags: list[str],
    ) -> dict[str, Any]:
        entries = self.load_global_experience().get("entries", []) or []
        scored_entries = self._rank_global_experience_entries(
            entries=entries,
            project_id=project_id,
            question_tags=question_tags,
        )
        similar_cases = scored_entries[: self.GLOBAL_SIMILAR_CASE_LIMIT]
        evidence_file_counter: Counter[str] = Counter()
        rule_counter: Counter[str] = Counter()
        rule_examples: dict[str, tuple[str, str]] = {}

        for item in similar_cases:
            for evidence_file in item.get("evidence_files", []) or []:
                normalized = str(evidence_file).strip()
                if normalized:
                    evidence_file_counter[normalized] += 1
            for finding in item.get("automatic_findings", []) or []:
                self._collect_rules(rule_counter, rule_examples, str(finding), source=f"global:{item.get('project_id', '')}")
            for warning in item.get("warnings", []) or []:
                self._collect_rules(rule_counter, rule_examples, str(warning), source=f"global:{item.get('project_id', '')}")

        structured_rules = self._build_structured_rules(rule_counter, rule_examples)
        return {
            "global_has_experience": bool(similar_cases),
            "global_similar_cases": similar_cases,
            "global_common_evidence_files": [item for item, _ in evidence_file_counter.most_common(8)],
            "global_experience_rules": [rule.rule_key for rule in structured_rules],
            "global_structured_experience_rules": [rule.to_dict() for rule in structured_rules],
        }

    def build_planning_hints(
        self,
        *,
        question_tags: list[str],
        experience_summary: dict[str, Any],
        rule_library: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        global_summary = self.summarize_global_experience(
            project_id=str(experience_summary.get("project_id") or ""),
            question_tags=question_tags,
        )
        latest_findings = experience_summary.get("latest_findings", []) or []
        recent_questions = experience_summary.get("recent_questions", []) or []
        report_excerpt = experience_summary.get("last_report_excerpt", "") or ""
        common_evidence_files = experience_summary.get("common_evidence_files", []) or []
        global_common_evidence_files = global_summary.get("global_common_evidence_files", []) or []
        common_question_types = experience_summary.get("common_question_types", []) or []
        experience_rules = experience_summary.get("experience_rules", []) or []
        structured_rules = experience_summary.get("structured_experience_rules", []) or []
        global_experience_rules = global_summary.get("global_experience_rules", []) or []
        global_structured_rules = global_summary.get("global_structured_experience_rules", []) or []
        library_rules = rule_library or []

        merged_structured_rules = self._merge_structured_rules(
            structured_rules + global_structured_rules,
            library_rules,
        )
        active_rules = self._filter_active_rules(merged_structured_rules, question_tags)
        merged_rule_keys = [item.get("rule_key", "") for item in active_rules if item.get("rule_key")]

        matched_findings = [
            item
            for item in latest_findings
            if any(tag.lower() in str(item).lower() for tag in question_tags)
        ][:5]
        matched_question_types = [item for item in common_question_types if item in question_tags]
        prioritized_evidence_hints = [
            item.split("/")[-1].split("\\")[-1]
            for item in (common_evidence_files + global_common_evidence_files)[:10]
        ]

        prioritized_metrics = []
        seed_metrics = (
            matched_question_types
            + common_question_types
            + merged_rule_keys
            + experience_rules
            + global_experience_rules
            + question_tags
        )
        for item in seed_metrics:
            normalized = str(item).strip().lower()
            if normalized and normalized not in prioritized_metrics:
                prioritized_metrics.append(normalized)
        if not prioritized_evidence_hints:
            prioritized_evidence_hints = []

        return {
            "has_experience": experience_summary.get("has_experience", False),
            "has_global_experience": global_summary.get("global_has_experience", False),
            "matched_findings": matched_findings,
            "matched_question_types": matched_question_types,
            "global_similar_cases": global_summary.get("global_similar_cases", []),
            "global_experience_rules": global_experience_rules,
            "global_structured_experience_rules": global_structured_rules,
            "recent_question_count": len(recent_questions),
            "has_report_excerpt": bool(report_excerpt),
            "prioritized_evidence_hints": prioritized_evidence_hints,
            "prioritized_metrics": prioritized_metrics,
            "experience_rules": merged_rule_keys,
            "structured_experience_rules": active_rules,
            "rule_library_size": len(library_rules),
            "active_rule_count": len(active_rules),
            "evidence_scope_adjustment": self._build_evidence_scope_adjustment(
                structured_rules=active_rules,
                prioritized_evidence_hints=prioritized_evidence_hints,
            ),
            "planning_note": (
                "reuse_historical_evidence_priority"
                if prioritized_evidence_hints
                else "no_historical_evidence_priority"
            ),
        }

    def load_global_experience(self) -> dict[str, Any]:
        if not self._global_experience_path.exists():
            payload = self._bootstrap_global_experience()
            if payload.get("entries"):
                self._global_experience_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            return payload
        try:
            payload = json.loads(self._global_experience_path.read_text(encoding="utf-8"))
        except Exception:
            return {"entries": []}
        entries = payload.get("entries", [])
        return {"entries": entries if isinstance(entries, list) else []}

    def _bootstrap_global_experience(self) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for path in sorted(self._global_experience_path.parent.glob("*.json")):
            if path.name == self._global_experience_path.name:
                continue
            try:
                memory = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            project_id = str(memory.get("project_id") or path.stem)
            for item in memory.get("evidence_history", []) or []:
                findings = item.get("automatic_findings", []) or []
                warnings = item.get("warnings", []) or []
                evidence_files = item.get("evidence_files", []) or []
                if not findings and not warnings and not evidence_files:
                    continue
                question_type = str(item.get("question_type") or "")
                question_tags = (item.get("trace", {}) or {}).get("question_tags") or [question_type]
                entries.append(
                    {
                        "run_id": item.get("run_id"),
                        "project_id": project_id,
                        "question": item.get("question", ""),
                        "question_type": question_type,
                        "question_tags": question_tags,
                        "rule_keys": self._extract_rule_keys(
                            [str(value) for value in findings]
                            + [str(value) for value in warnings]
                            + [question_type]
                            + [str(value) for value in question_tags]
                        ),
                        "automatic_findings": findings[:10],
                        "warnings": warnings[:5],
                        "evidence_files": evidence_files[:20],
                        "confidence": item.get("confidence"),
                        "created_at": item.get("created_at") or memory.get("updated_at"),
                    }
                )
        return {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "entries": entries[-self.GLOBAL_EXPERIENCE_LIMIT :],
        }

    def record_global_experience(self, *, project_id: str, analysis_result: dict[str, Any]) -> dict[str, Any]:
        fact_claims = [
            item
            for item in (analysis_result.get("validated_claims", []) or [])
            if isinstance(item, dict)
            and item.get("evidence_ids")
            and (
                item.get("claim_type") == "observation"
                or (
                    item.get("claim_type") == "causal_hypothesis"
                    and item.get("support_level") == "confirmed"
                )
            )
        ]
        findings = [str(item.get("text") or "") for item in fact_claims if item.get("text")]
        warnings: list[str] = []
        evidence_files = list(
            dict.fromkeys(
                str(card.get("source_file") or "")
                for card in (analysis_result.get("evidence_cards", []) or [])
                if isinstance(card, dict) and card.get("source_file")
            )
        )
        if not findings:
            return self.load_global_experience()

        rule_keys = self._extract_rule_keys(
            [str(item) for item in findings]
            + [str(item) for item in warnings]
            + [str(analysis_result.get("question_type") or "")]
            + [str(item) for item in analysis_result.get("question_tags", []) or []]
        )
        entry = {
            "run_id": analysis_result.get("run_id"),
            "project_id": project_id,
            "question": analysis_result.get("question", ""),
            "question_type": analysis_result.get("question_type", ""),
            "question_tags": analysis_result.get("question_tags", []),
            "rule_keys": rule_keys,
            "automatic_findings": findings[:10],
            "warnings": warnings[:5],
            "evidence_files": evidence_files[:20],
            "evidence_ids": list(
                dict.fromkeys(
                    str(evidence_id)
                    for claim in fact_claims
                    for evidence_id in claim.get("evidence_ids", []) or []
                )
            )[:40],
            "confidence": analysis_result.get("confidence"),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

        payload = self.load_global_experience()
        entries = payload.get("entries", []) or []
        run_id = str(entry.get("run_id") or "")
        if run_id:
            entries = [item for item in entries if str(item.get("run_id") or "") != run_id]
        entries.append(entry)
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "entries": entries[-self.GLOBAL_EXPERIENCE_LIMIT :],
        }
        self._global_experience_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload

    def _rank_global_experience_entries(
        self,
        *,
        entries: list[dict[str, Any]],
        project_id: str,
        question_tags: list[str],
    ) -> list[dict[str, Any]]:
        normalized_tags = {str(tag).strip().lower() for tag in question_tags if str(tag).strip()}
        broad_query = not normalized_tags or bool(normalized_tags & {"overview", "diagnostic"})
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in entries:
            if str(item.get("project_id") or "") == project_id:
                continue
            item_tags = {str(tag).strip().lower() for tag in item.get("question_tags", []) or [] if str(tag).strip()}
            question_type = str(item.get("question_type") or "").strip().lower()
            rule_keys = {str(key).strip().lower() for key in item.get("rule_keys", []) or [] if str(key).strip()}
            score = 0
            if question_type and question_type in normalized_tags:
                score += 4
            score += len(item_tags & normalized_tags) * 3
            score += len(rule_keys & normalized_tags) * 2
            if broad_query and (item.get("automatic_findings") or item.get("warnings")):
                score += 1
            if score <= 0:
                continue
            scored.append((score, item))
        scored.sort(
            key=lambda pair: (
                pair[0],
                str(pair[1].get("created_at") or ""),
            ),
            reverse=True,
        )
        return [item for _, item in scored]

    def _extract_rule_keys(self, texts: list[str]) -> list[str]:
        counter: Counter[str] = Counter()
        examples: dict[str, tuple[str, str]] = {}
        for text in texts:
            self._collect_rules(counter, examples, text, source="global_record")
        return [rule_key for rule_key, _ in counter.most_common(8)]

    def _collect_rules(
        self,
        counter: Counter[str],
        examples: dict[str, tuple[str, str]],
        text: str,
        *,
        source: str,
    ) -> None:
        lowered = (text or "").lower()
        for keyword in self.RULE_KEYWORDS:
            if keyword in lowered:
                counter[keyword] += 1
                examples.setdefault(keyword, (source, text))

    def _build_structured_rules(
        self,
        counter: Counter[str],
        examples: dict[str, tuple[str, str]],
    ) -> list[ExperienceRule]:
        rules: list[ExperienceRule] = []
        total = sum(counter.values()) or 1
        for rule_key, frequency in counter.most_common(8):
            source, matched_text = examples.get(rule_key, ("unknown", ""))
            confidence = round(min(0.99, max(0.2, frequency / total + 0.2)), 3)
            activation_conditions = self._build_activation_conditions(rule_key)
            rules.append(
                ExperienceRule(
                    rule_key=rule_key,
                    source=source,
                    matched_text=matched_text[:200],
                    frequency=frequency,
                    priority_type=self.RULE_KEYWORDS.get(rule_key, "metric"),
                    confidence=confidence,
                    activation_conditions=activation_conditions,
                )
            )
        return rules

    def _merge_structured_rules(
        self,
        memory_rules: list[dict[str, Any]],
        library_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in library_rules + memory_rules:
            rule_key = str(item.get("rule_key") or "").strip()
            if not rule_key:
                continue
            current = merged.get(rule_key)
            score = (
                float(item.get("confidence", 0.0) or 0.0),
                int(item.get("frequency", 0) or 0),
            )
            current_score = (
                float(current.get("confidence", 0.0) or 0.0),
                int(current.get("frequency", 0) or 0),
            ) if current else None
            if current is None or score > current_score:
                merged[rule_key] = dict(item)
        return sorted(
            merged.values(),
            key=lambda item: (
                float(item.get("confidence", 0.0) or 0.0),
                int(item.get("frequency", 0) or 0),
            ),
            reverse=True,
        )[:8]

    def _filter_active_rules(
        self,
        rules: list[dict[str, Any]],
        question_tags: list[str],
    ) -> list[dict[str, Any]]:
        active: list[dict[str, Any]] = []
        normalized_tags = {str(tag).strip().lower() for tag in question_tags if str(tag).strip()}
        broad_query = bool(normalized_tags & {"overview", "diagnostic"})
        for item in rules:
            confidence = float(item.get("confidence", 0.0) or 0.0)
            conditions = [str(cond).strip().lower() for cond in item.get("activation_conditions", []) or []]
            if confidence < 0.35:
                continue
            if broad_query or not conditions or any(cond in normalized_tags for cond in conditions):
                active.append(item)
        return active

    def _build_activation_conditions(self, rule_key: str) -> list[str]:
        mapping = {
            "frip": ["frip", "peak"],
            "q30": ["qc"],
            "adapter": ["qc"],
            "alignment": ["alignment"],
            "mapping": ["alignment"],
            "duplicate": ["alignment"],
            "spike": ["spikein"],
            "peak": ["peak", "frip"],
            "motif": ["motif"],
            "correlation": ["correlation"],
            "diff": ["diff"],
            "mt": ["alignment"],
            "chrmt": ["alignment"],
            "warning": [],
        }
        return mapping.get(rule_key, [])

    def _build_evidence_scope_adjustment(
        self,
        *,
        structured_rules: list[dict[str, Any]],
        prioritized_evidence_hints: list[str],
    ) -> dict[str, Any]:
        extra_keywords: list[str] = []
        metric_rule_count = 0
        for item in structured_rules:
            rule_key = str(item.get("rule_key") or "").strip().lower()
            priority_type = str(item.get("priority_type") or "").strip().lower()
            if priority_type == "metric":
                metric_rule_count += 1
            for keyword in self.RULE_TO_EVIDENCE_KEYWORDS.get(rule_key, []):
                normalized = str(keyword).strip()
                if normalized and normalized not in extra_keywords:
                    extra_keywords.append(normalized)

        for hint in prioritized_evidence_hints:
            normalized = str(hint).strip()
            if normalized and normalized not in extra_keywords:
                extra_keywords.append(normalized)

        expand_by = min(4, max(0, metric_rule_count))
        return {
            "expand_by": expand_by,
            "extra_keywords": extra_keywords[:12],
            "effective_max_evidence_files": None,
        }


experience_service = ExperienceService()
