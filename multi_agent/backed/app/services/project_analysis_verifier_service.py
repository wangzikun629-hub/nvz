from __future__ import annotations

import re
from typing import Any

from multi_agent.backed.app.services.business_agent.evidence_card_service import (
    evidence_card_service,
)


class ProjectAnalysisVerifierService:
    """Independent deterministic verifier for evidence cards and claims."""

    VALID_SUPPORT_LEVELS = {"confirmed", "inferred", "insufficient"}
    VALID_CAUSAL_LEVELS = {
        "direct_observation",
        "associated_phenomenon",
        "possible_explanation",
        "verified_cause",
        "not_applicable",
    }

    def verify(
        self,
        *,
        evidence_cards: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        project_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cards = {
            str(card.get("evidence_id") or ""): card
            for card in evidence_cards
            if isinstance(card, dict) and card.get("evidence_id")
        }
        project_species = self._project_species(project_context or {})
        conflicts = evidence_card_service.detect_conflicts(list(cards.values()))
        unresolved_evidence_ids = {
            str(evidence_id)
            for conflict in conflicts
            if conflict.get("status") == "unresolved"
            for evidence_id in conflict.get("evidence_ids", [])
        }
        valid_claims: list[dict[str, Any]] = []
        invalid_claims: list[dict[str, Any]] = []
        violations: list[dict[str, Any]] = []

        for original in claims:
            claim = dict(original)
            claim_violations = self._verify_claim(
                claim,
                cards,
                project_species,
                unresolved_evidence_ids,
            )
            if claim_violations:
                violations.extend(claim_violations)
                claim["validation_status"] = "invalid"
                claim["validation_violations"] = [item["rule"] for item in claim_violations]
                invalid_claims.append(claim)
            else:
                claim["validation_status"] = "valid"
                claim["validation_violations"] = []
                valid_claims.append(claim)

        violation_rules = {str(item.get("rule") or "") for item in violations}
        return {
            "verifier_version": "independent-deterministic-v1",
            "passed": not invalid_claims,
            "claim_count": len(claims),
            "valid_claim_count": len(valid_claims),
            "invalid_claim_count": len(invalid_claims),
            "valid_claims": valid_claims,
            "invalid_claims": invalid_claims,
            "violations": violations,
            "evidence_conflicts": conflicts,
            "contracts": {
                "numbers_trace_to_evidence": not bool(
                    violation_rules
                    & {
                        "numeric_value_mismatch",
                        "display_value_mismatch",
                        "claim_text_value_mismatch",
                        "claim_text_contains_unsupported_number",
                        "formula_recalculation_mismatch",
                    }
                ),
                "denominator_matches_evidence": "denominator_mismatch" not in violation_rules,
                "species_matches_project": not bool(
                    violation_rules
                    & {
                        "species_evidence_mismatch",
                        "species_project_mismatch",
                        "claim_text_species_mismatch",
                    }
                ),
                "threshold_requires_project_source": not bool(
                    violation_rules
                    & {
                        "unverified_threshold_assertion",
                        "claim_text_unverified_threshold_assertion",
                    }
                ),
                "cause_support_label_required": "invalid_support_level" not in violation_rules,
                "causal_level_calibrated": not bool(
                    violation_rules
                    & {
                        "invalid_causal_level",
                        "verified_cause_without_independent_support",
                        "verified_cause_has_conflict_or_missing_evidence",
                    }
                ),
                "cross_source_conflicts_resolved": "unresolved_evidence_conflict" not in violation_rules,
            },
        }

    def _verify_claim(
        self,
        claim: dict[str, Any],
        cards: dict[str, dict[str, Any]],
        project_species: str,
        unresolved_evidence_ids: set[str],
    ) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        claim_id = str(claim.get("claim_id") or "")
        claim_type = str(claim.get("claim_type") or "")
        evidence_ids = [str(item) for item in claim.get("evidence_ids", []) or [] if str(item)]
        missing_ids = [item for item in evidence_ids if item not in cards]
        if missing_ids:
            violations.append(self._violation(claim_id, "missing_evidence_id", missing_ids))
        if claim_type == "observation" and not evidence_ids:
            violations.append(self._violation(claim_id, "observation_without_evidence"))

        linked_cards = [cards[item] for item in evidence_ids if item in cards]
        if any(item in unresolved_evidence_ids for item in evidence_ids):
            violations.append(self._violation(claim_id, "unresolved_evidence_conflict"))
        if claim_type == "observation" and linked_cards:
            card = linked_cards[0]
            if not self._same_number(claim.get("value"), card.get("value")):
                violations.append(self._violation(claim_id, "numeric_value_mismatch"))
            if str(claim.get("display_value") or "") != str(card.get("display_value") or ""):
                violations.append(self._violation(claim_id, "display_value_mismatch"))
            if str(claim.get("denominator") or "") != str(card.get("denominator") or ""):
                violations.append(self._violation(claim_id, "denominator_mismatch"))
            if not self._same_number(
                claim.get("denominator_value"),
                card.get("denominator_value"),
            ):
                violations.append(self._violation(claim_id, "denominator_value_mismatch"))
            if claim.get("numerator") != card.get("numerator"):
                violations.append(self._violation(claim_id, "numerator_mismatch"))
            if not self._same_number(
                claim.get("numerator_value"),
                card.get("numerator_value"),
            ):
                violations.append(self._violation(claim_id, "numerator_value_mismatch"))
            claim_species = str(claim.get("species") or "")
            card_species = str(card.get("species") or "")
            if claim_species and card_species and claim_species.lower() != card_species.lower():
                violations.append(self._violation(claim_id, "species_evidence_mismatch"))
            if project_species and claim_species and claim_species.lower() != project_species.lower():
                violations.append(self._violation(claim_id, "species_project_mismatch"))
            if claim.get("threshold_assertion") and not card.get("threshold_verified"):
                violations.append(self._violation(claim_id, "unverified_threshold_assertion"))
            if str(claim.get("threshold_source") or "") != str(card.get("threshold_source") or ""):
                violations.append(self._violation(claim_id, "threshold_source_mismatch"))
            violations.extend(self._verify_observation_text(claim, card, project_species))
            if not self._formula_matches(card):
                violations.append(self._violation(claim_id, "formula_recalculation_mismatch"))

        support_level = str(claim.get("support_level") or "")
        if support_level not in self.VALID_SUPPORT_LEVELS:
            violations.append(self._violation(claim_id, "invalid_support_level"))
        causal_level = str(claim.get("causal_level") or "")
        if not causal_level:
            causal_level = {
                "observation": "direct_observation",
                "causal_hypothesis": "possible_explanation",
                "limitation": "not_applicable",
                "action": "not_applicable",
            }.get(claim_type, "")
            claim["causal_level"] = causal_level
        if causal_level not in self.VALID_CAUSAL_LEVELS:
            violations.append(self._violation(claim_id, "invalid_causal_level"))
        if claim_type == "causal_hypothesis":
            supporting = [
                str(item)
                for item in claim.get("supporting_evidence_ids", []) or []
                if str(item) in cards
            ]
            if support_level == "confirmed" and not supporting:
                violations.append(self._violation(claim_id, "confirmed_cause_without_evidence"))
            if support_level == "insufficient" and claim.get("status") != "unknown":
                violations.append(self._violation(claim_id, "insufficient_cause_not_marked_unknown"))
            if causal_level == "verified_cause" and (
                support_level != "confirmed" or len(set(supporting)) < 2
            ):
                violations.append(
                    self._violation(claim_id, "verified_cause_without_independent_support")
                )
            if causal_level == "verified_cause" and (
                claim.get("contradicting_evidence_ids")
                or claim.get("missing_evidence")
            ):
                violations.append(
                    self._violation(
                        claim_id,
                        "verified_cause_has_conflict_or_missing_evidence",
                    )
                )
        return violations

    @classmethod
    def _verify_observation_text(
        cls,
        claim: dict[str, Any],
        card: dict[str, Any],
        project_species: str,
    ) -> list[dict[str, Any]]:
        claim_id = str(claim.get("claim_id") or "")
        text = str(claim.get("text") or "")
        violations: list[dict[str, Any]] = []
        expected_values = [
            cls._number(card.get("value")),
            cls._number(card.get("display_value")),
        ]
        expected_values = [item for item in expected_values if item is not None]
        text_numbers = [cls._number(item) for item in re.findall(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text)]
        text_numbers = [item for item in text_numbers if item is not None]
        if expected_values and not any(
            abs(number - expected) <= max(1e-6, abs(expected) * 1e-4)
            for number in text_numbers
            for expected in expected_values
        ):
            violations.append(cls._violation(claim_id, "claim_text_value_mismatch"))

        allowed_numbers = [
            cls._number(card.get("value")),
            cls._number(card.get("display_value")),
            cls._number(card.get("numerator_value", card.get("numerator"))),
            cls._number(card.get("denominator_value", card.get("denominator"))),
        ]
        allowed_numbers.extend(
            cls._number(item)
            for item in re.findall(
                r"\d+(?:\.\d+)?",
                " ".join(
                    str(card.get(key) or "")
                    for key in (
                        "sample",
                        "metric",
                        "metric_id",
                        "measurement_id",
                        "source_field",
                    )
                ),
            )
        )
        allowed_numbers = [item for item in allowed_numbers if item is not None]
        unsupported = [
            number
            for number in text_numbers
            if not any(
                abs(number - allowed) <= max(1e-6, abs(allowed) * 1e-4)
                for allowed in allowed_numbers
            )
        ]
        if unsupported:
            violations.append(
                cls._violation(
                    claim_id,
                    "claim_text_contains_unsupported_number",
                    unsupported[:5],
                )
            )

        lowered = text.lower()
        animal_project = any(
            token in project_species.lower()
            for token in ("hg", "grch", "human", "mm", "grcm", "mouse", "rn", "rat")
        )
        if animal_project and any(
            token in lowered
            for token in ("叶绿体", "质体", "chloroplast", "plastid")
        ):
            violations.append(cls._violation(claim_id, "claim_text_species_mismatch"))

        if not card.get("threshold_verified") and cls._contains_unqualified_threshold_judgement(text):
            violations.append(
                cls._violation(claim_id, "claim_text_unverified_threshold_assertion")
            )
        return violations

    @staticmethod
    def _contains_unqualified_threshold_judgement(text: str) -> bool:
        judgement_terms = (
            "偏高",
            "偏低",
            "异常",
            "超标",
            "不达标",
            "显著升高",
            "显著降低",
            "abnormally high",
            "abnormally low",
        )
        negations = (
            "不能",
            "无法",
            "不得",
            "不支持",
            "未验证",
            "尚未",
            "不能确定",
            "cannot",
            "not verified",
        )
        for segment in re.split(r"[\n。！？;；]", text):
            lowered = segment.lower()
            if any(term in lowered for term in judgement_terms) and not any(
                negation in lowered for negation in negations
            ):
                return True
        return False

    @classmethod
    def _formula_matches(cls, card: dict[str, Any]) -> bool:
        numerator = cls._number(card.get("numerator_value", card.get("numerator")))
        denominator = cls._number(card.get("denominator_value", card.get("denominator")))
        value = cls._number(card.get("value"))
        formula = str(card.get("formula") or card.get("measurement_definition") or "")
        if numerator is None or denominator in (None, 0) or value is None or "/" not in formula:
            return True
        ratio = numerator / denominator
        value_scale = str(card.get("value_scale") or "").strip().lower()
        if value_scale == "fraction":
            expected = ratio
        elif value_scale == "percent":
            expected = ratio * 100
        else:
            expected = ratio * 100 if "* 100" in formula else ratio
        if abs(expected - value) > max(0.02 if value_scale == "percent" else 0.0002, abs(value) * 1e-3):
            return False

        display = str(card.get("display_value") or "").strip()
        if display.endswith("%"):
            display_number = cls._number(display)
            if display_number is None:
                return False
            expected_display = ratio * 100
            if abs(expected_display - display_number) > max(0.02, abs(expected_display) * 1e-3):
                return False
        return True

    @staticmethod
    def _number(value: Any) -> float | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            return float(str(value).replace(",", "").rstrip("%"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _same_number(left: Any, right: Any) -> bool:
        if left is None or right is None:
            return left is right
        try:
            return abs(float(left) - float(right)) < 1e-9
        except (TypeError, ValueError):
            return str(left) == str(right)

    @staticmethod
    def _project_species(context: dict[str, Any]) -> str:
        config = context.get("config") if isinstance(context.get("config"), dict) else {}
        return str((config or {}).get("species") or context.get("species") or "").strip()

    @staticmethod
    def _violation(claim_id: str, rule: str, detail: Any = None) -> dict[str, Any]:
        payload = {"claim_id": claim_id, "rule": rule}
        if detail is not None:
            payload["detail"] = detail
        return payload


project_analysis_verifier_service = ProjectAnalysisVerifierService()
