from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from multi_agent.backed.app.repositories.project_memory_repository import project_memory_repository


class ProjectMemoryService:
    """Persist evidence-backed facts and explicitly invalidate superseded facts."""

    def load_memory(self, project_id: str) -> dict[str, Any]:
        memory = project_memory_repository.load_memory(project_id) or {}
        defaults = {
            "project_id": project_id,
            "last_report": None,
            "latest_findings": [],
            "recent_questions": [],
            "evidence_history": [],
            "active_facts": [],
            "invalidated_facts": [],
            "updated_at": None,
        }
        return {**defaults, **memory}

    def update_memory(self, project_id: str, analysis_result: dict[str, Any]) -> dict[str, Any]:
        with project_memory_repository.lock_for(project_id):
            return self._update_memory_locked(project_id, analysis_result)

    def _update_memory_locked(self, project_id: str, analysis_result: dict[str, Any]) -> dict[str, Any]:
        memory = self.load_memory(project_id)
        now = datetime.now().isoformat(timespec="seconds")
        recent_questions = list(memory.get("recent_questions", []))
        evidence_history = list(memory.get("evidence_history", []))
        question = str(analysis_result.get("question") or "")
        if question:
            recent_questions.append(question)

        new_facts = self._facts_from_analysis(project_id, analysis_result, now)
        active_facts, invalidated_facts = self._reconcile_facts(
            existing=list(memory.get("active_facts", []) or []),
            invalidated=list(memory.get("invalidated_facts", []) or []),
            incoming=new_facts,
            run_id=str(analysis_result.get("run_id") or ""),
            now=now,
            observed_scope=self._observed_scope(analysis_result),
        )
        fact_claims = [
            {
                "claim_id": fact.get("claim_id"),
                "claim_type": fact.get("claim_type"),
                "text": fact.get("text"),
                "evidence_ids": fact.get("evidence_ids", []),
                "fact_id": fact.get("fact_id"),
            }
            for fact in new_facts
        ]
        evidence_history.append(
            {
                "run_id": analysis_result.get("run_id"),
                "trace": analysis_result.get("trace"),
                "question": question,
                "question_type": analysis_result.get("question_type"),
                "evidence_files": analysis_result.get("evidence_files", []),
                "evidence_ids": list(
                    dict.fromkeys(
                        evidence_id
                        for fact in new_facts
                        for evidence_id in fact.get("evidence_ids", [])
                    )
                ),
                "validated_claims": fact_claims,
                # Compatibility field: it now contains only verified fact text.
                "automatic_findings": [fact.get("text") for fact in new_facts],
                "warnings": [],
                "confidence": analysis_result.get("confidence"),
                "created_at": now,
            }
        )
        memory.update(
            {
                "last_report": analysis_result.get("report"),
                "latest_findings": [fact.get("text") for fact in active_facts[:20]],
                "latest_warnings": [],
                "last_confidence": analysis_result.get("confidence"),
                "last_run_id": analysis_result.get("run_id"),
                "last_trace": analysis_result.get("trace"),
                "recent_questions": recent_questions[-20:],
                "evidence_history": evidence_history[-20:],
                "active_facts": active_facts[-200:],
                "invalidated_facts": invalidated_facts[-200:],
                "memory_contract": {
                    "facts_require_validated_claim": True,
                    "facts_require_evidence_ids": True,
                    "superseded_facts_are_invalidated": True,
                    "unverified_hypotheses_are_not_facts": True,
                },
                "updated_at": now,
            }
        )
        project_memory_repository.save_memory(project_id, memory)
        return memory

    def invalidate_fact(self, project_id: str, fact_id: str, reason: str) -> dict[str, Any]:
        with project_memory_repository.lock_for(project_id):
            return self._invalidate_fact_locked(project_id, fact_id, reason)

    def _invalidate_fact_locked(self, project_id: str, fact_id: str, reason: str) -> dict[str, Any]:
        memory = self.load_memory(project_id)
        now = datetime.now().isoformat(timespec="seconds")
        active: list[dict[str, Any]] = []
        invalidated = list(memory.get("invalidated_facts", []) or [])
        for fact in memory.get("active_facts", []) or []:
            if str(fact.get("fact_id") or "") == str(fact_id):
                invalidated.append(
                    {
                        **fact,
                        "status": "invalidated",
                        "invalidated_at": now,
                        "invalidation_reason": reason,
                    }
                )
            else:
                active.append(fact)
        memory["active_facts"] = active
        memory["invalidated_facts"] = invalidated[-200:]
        memory["latest_findings"] = [fact.get("text") for fact in active[:20]]
        memory["updated_at"] = now
        project_memory_repository.save_memory(project_id, memory)
        return memory

    def _facts_from_analysis(
        self,
        project_id: str,
        analysis_result: dict[str, Any],
        now: str,
    ) -> list[dict[str, Any]]:
        cards = {
            str(card.get("evidence_id") or ""): card
            for card in analysis_result.get("evidence_cards", []) or []
            if isinstance(card, dict) and card.get("evidence_id")
        }
        facts: list[dict[str, Any]] = []
        for claim in analysis_result.get("validated_claims", []) or []:
            if not isinstance(claim, dict):
                continue
            claim_type = str(claim.get("claim_type") or "")
            evidence_ids = [
                str(item)
                for item in claim.get("evidence_ids", []) or []
                if str(item) in cards
            ]
            is_observed_fact = claim_type == "observation" and evidence_ids
            is_confirmed_cause = (
                claim_type == "causal_hypothesis"
                and claim.get("support_level") == "confirmed"
                and bool(claim.get("supporting_evidence_ids"))
                and evidence_ids
            )
            if not (is_observed_fact or is_confirmed_cause):
                continue
            fact_key = self._fact_key(claim)
            fingerprint = self._evidence_fingerprint(evidence_ids, cards)
            facts.append(
                {
                    "fact_id": self._hash("fact", project_id, fact_key, fingerprint),
                    "fact_key": fact_key,
                    "claim_id": claim.get("claim_id"),
                    "claim_type": claim_type,
                    "text": claim.get("text"),
                    "metric_id": claim.get("metric_id", ""),
                    "measurement_id": claim.get("measurement_id") or claim.get("metric_id", ""),
                    "processing_phase": claim.get("processing_phase", ""),
                    "sample": claim.get("sample", ""),
                    "value": claim.get("value"),
                    "denominator": claim.get("denominator", ""),
                    "species": claim.get("species", ""),
                    "support_level": claim.get("support_level"),
                    "evidence_ids": evidence_ids,
                    "evidence_fingerprint": fingerprint,
                    "status": "active",
                    "source_run_id": analysis_result.get("run_id"),
                    "created_at": now,
                    "last_seen_at": now,
                }
            )
        return facts

    @staticmethod
    def _reconcile_facts(
        *,
        existing: list[dict[str, Any]],
        invalidated: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
        run_id: str,
        now: str,
        observed_scope: set[tuple[str, str]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        incoming_by_key = {str(fact.get("fact_key") or ""): fact for fact in incoming}
        active: list[dict[str, Any]] = []
        for old in existing:
            key = str(old.get("fact_key") or "")
            new = incoming_by_key.get(key)
            if new is None:
                scope_key = (
                    str(old.get("measurement_id") or old.get("metric_id") or ""),
                    str(old.get("sample") or ""),
                )
                if observed_scope and scope_key in observed_scope:
                    invalidated.append(
                        {
                            **old,
                            "status": "invalidated",
                            "invalidated_at": now,
                            "invalidation_reason": "absent_from_latest_observed_scope",
                        }
                    )
                    continue
                active.append(old)
                continue
            if old.get("evidence_fingerprint") == new.get("evidence_fingerprint"):
                active.append({**old, "last_seen_at": now, "source_run_id": run_id or old.get("source_run_id")})
                incoming_by_key.pop(key, None)
                continue
            invalidated.append(
                {
                    **old,
                    "status": "invalidated",
                    "invalidated_at": now,
                    "invalidation_reason": "superseded_by_new_evidence",
                    "superseded_by_fact_id": new.get("fact_id"),
                }
            )
        active.extend(incoming_by_key.values())
        return active, invalidated

    @staticmethod
    def _fact_key(claim: dict[str, Any]) -> str:
        if claim.get("claim_type") == "observation":
            return "|".join(
                str(claim.get(key) or "")
                for key in (
                    "claim_type",
                    "measurement_id",
                    "metric_id",
                    "sample",
                    "processing_phase",
                    "species",
                )
            )
        return "|".join(
            str(claim.get(key) or "")
            for key in ("claim_type", "text", "metric_id", "sample")
        )

    @staticmethod
    def _evidence_fingerprint(
        evidence_ids: list[str],
        cards: dict[str, dict[str, Any]],
    ) -> str:
        payload = [
            {
                "evidence_id": evidence_id,
                "metric_id": cards[evidence_id].get("metric_id"),
                "sample": cards[evidence_id].get("sample"),
                "value": cards[evidence_id].get("value"),
                "numerator": cards[evidence_id].get("numerator"),
                "denominator": cards[evidence_id].get("denominator"),
                "source_file": cards[evidence_id].get("source_file"),
                "source_field": cards[evidence_id].get("source_field"),
            }
            for evidence_id in sorted(evidence_ids)
        ]
        return hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _observed_scope(analysis_result: dict[str, Any]) -> set[tuple[str, str]]:
        return {
            (
                str(card.get("measurement_id") or card.get("metric_id") or ""),
                str(card.get("sample") or ""),
            )
            for card in analysis_result.get("evidence_cards", []) or []
            if isinstance(card, dict) and (card.get("measurement_id") or card.get("metric_id"))
        }

    @staticmethod
    def _hash(*parts: Any) -> str:
        raw = "|".join(str(part or "") for part in parts)
        return "fact_" + hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


project_memory_service = ProjectMemoryService()
