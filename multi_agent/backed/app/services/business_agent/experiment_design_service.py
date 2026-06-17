from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


class ExperimentDesignService:
    """Resolve sample roles and comparison structure without inventing replicates."""

    VERSION = "experiment-design-v1"
    CONTROL_TOKENS = {"igg", "input", "control", "ctrl", "mock", "vehicle", "isotype"}
    TARGET_PATTERN = re.compile(
        r"^(?:h\d+k\d+(?:me\d|ac)?|ctcf|pol2|rad21|smc\d|tf|atac|rna)$",
        re.IGNORECASE,
    )
    REPLICATE_PATTERN = re.compile(r"^(?:rep|r)(\d+)$", re.IGNORECASE)
    BATCH_PATTERN = re.compile(r"^(?:batch|b)(\d+)$", re.IGNORECASE)

    @classmethod
    def build(
        cls,
        samples: list[dict[str, Any]],
        *,
        config: dict[str, Any] | None = None,
        sample_roles: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        explicit_roles = {
            str(item.get("sample") or ""): str(item.get("role") or "")
            for item in (sample_roles or [])
            if isinstance(item, dict)
        }
        records = [
            cls._sample_record(item, explicit_roles.get(str(item.get("sample") or ""), ""))
            for item in samples
            if isinstance(item, dict) and str(item.get("sample") or "").strip()
        ]
        cls._bind_controls(records)
        replicate_groups = cls._replicate_groups(records)
        conditions = sorted({item["condition"] for item in records if item.get("condition")})
        batches = sorted({item["batch"] for item in records if item.get("batch")})
        targets = sorted({item["target"] for item in records if item.get("target")})
        differential = cls._differential_readiness(records, replicate_groups)
        warnings: list[str] = []
        if not records:
            warnings.append("samplelist_missing_or_empty")
        if records and not any(item.get("condition") for item in records):
            warnings.append("condition_not_resolved")
        if records and not replicate_groups:
            warnings.append("biological_replicates_not_confirmed")
        if any(item["role"] == "control" and not item.get("control_for") for item in records):
            warnings.append("control_pairing_not_resolved")
        return {
            "version": cls.VERSION,
            "samples": records,
            "conditions": conditions,
            "targets": targets,
            "batches": batches,
            "replicate_groups": replicate_groups,
            "controls": [
                item for item in records if item.get("role") == "control"
            ],
            "differential_analysis": differential,
            "warnings": warnings,
            "config_assay": str((config or {}).get("assay") or (config or {}).get("project_type") or ""),
        }

    @classmethod
    def classify_pair(
        cls,
        left: str,
        right: str,
        design: dict[str, Any] | None,
    ) -> str:
        records = {
            str(item.get("sample") or ""): item
            for item in ((design or {}).get("samples") or [])
            if isinstance(item, dict)
        }
        a = records.get(left)
        b = records.get(right)
        if not a or not b:
            return "unresolved"
        if a.get("role") == "control" or b.get("role") == "control":
            if a.get("role") != b.get("role"):
                return "experiment_vs_control"
            return "control_vs_control"
        same_group = (
            a.get("condition")
            and a.get("condition") == b.get("condition")
            and a.get("target") == b.get("target")
            and a.get("batch") == b.get("batch")
        )
        if same_group and a.get("replicate") and b.get("replicate"):
            return "biological_replicates"
        if a.get("target") and b.get("target") and a.get("target") != b.get("target"):
            return "different_targets"
        if a.get("condition") and b.get("condition") and a.get("condition") != b.get("condition"):
            return "different_conditions"
        if a.get("batch") and b.get("batch") and a.get("batch") != b.get("batch"):
            return "different_batches"
        return "unresolved"

    @classmethod
    def _sample_record(cls, item: dict[str, Any], inferred_role: str) -> dict[str, Any]:
        sample = str(item.get("sample") or "").strip()
        explicit = cls._explicit_fields(item)
        tokens = [token for token in re.split(r"[_\-.]+", sample) if token]
        lowered = [token.lower() for token in tokens]
        role = cls._role(explicit.get("role"), inferred_role, lowered)
        replicate = explicit.get("replicate") or cls._match_token(tokens, cls.REPLICATE_PATTERN)
        batch = explicit.get("batch") or cls._match_token(tokens, cls.BATCH_PATTERN)
        target = explicit.get("target") or cls._target(tokens, role)
        condition = explicit.get("condition") or cls._condition(
            tokens,
            role=role,
            target=target,
            replicate=replicate,
            batch=batch,
        )
        return {
            "sample": sample,
            "condition": condition,
            "replicate": replicate,
            "target": target,
            "role": role,
            "control_for": explicit.get("control_for") or [],
            "matched_control": str(item.get("control_sample") or "").strip(),
            "batch": batch,
            "source": "samplelist_explicit" if explicit else "sample_name_inference",
            "confidence": 1.0 if explicit else 0.65,
        }

    @staticmethod
    def _explicit_fields(item: dict[str, Any]) -> dict[str, Any]:
        fields = item.get("design_fields") if isinstance(item.get("design_fields"), dict) else {}
        result: dict[str, Any] = {}
        for key in ("condition", "replicate", "target", "role", "control_for", "batch"):
            value = fields.get(key) if fields else item.get(key)
            if value not in (None, ""):
                result[key] = value
        if isinstance(result.get("control_for"), str):
            result["control_for"] = [
                token.strip()
                for token in re.split(r"[,;|]+", result["control_for"])
                if token.strip()
            ]
        return result

    @classmethod
    def _role(cls, explicit: Any, inferred_role: str, tokens: list[str]) -> str:
        text = str(explicit or inferred_role or "").lower()
        if any(token in text for token in cls.CONTROL_TOKENS) or any(
            token in cls.CONTROL_TOKENS for token in tokens
        ):
            return "control"
        if any(token in text for token in ("treat", "case", "ko", "oe", "stim")):
            return "treatment"
        return "experimental"

    @classmethod
    def _target(cls, tokens: list[str], role: str) -> str:
        if role == "control":
            for token in tokens:
                if token.lower() in {"igg", "input", "control", "ctrl", "mock", "isotype"}:
                    return token
        for token in tokens:
            if cls.TARGET_PATTERN.match(token):
                return token
        return ""

    @classmethod
    def _condition(
        cls,
        tokens: list[str],
        *,
        role: str,
        target: str,
        replicate: str,
        batch: str,
    ) -> str:
        excluded = {str(target).lower(), str(replicate).lower(), str(batch).lower()}
        excluded.update(cls.CONTROL_TOKENS)
        candidates = [
            token
            for token in tokens
            if token.lower() not in excluded
            and not cls.REPLICATE_PATTERN.match(token)
            and not cls.BATCH_PATTERN.match(token)
        ]
        if role == "control" and not candidates:
            return ""
        return "_".join(candidates)

    @staticmethod
    def _match_token(tokens: list[str], pattern: re.Pattern[str]) -> str:
        for token in tokens:
            match = pattern.match(token)
            if match:
                return match.group(1)
        return ""

    @classmethod
    def _bind_controls(cls, records: list[dict[str, Any]]) -> None:
        experiments = [item for item in records if item["role"] != "control"]
        by_sample = {item["sample"]: item for item in records}
        for experiment in experiments:
            matched_control = str(experiment.get("matched_control") or "")
            control = by_sample.get(matched_control)
            if not control:
                continue
            control["role"] = "control"
            if experiment["sample"] not in control.get("control_for", []):
                control.setdefault("control_for", []).append(experiment["sample"])
        for control in [item for item in records if item["role"] == "control"]:
            if control.get("control_for"):
                continue
            candidates = [
                item["sample"]
                for item in experiments
                if (
                    not control.get("condition")
                    or control.get("condition") == item.get("condition")
                )
                and (
                    not control.get("batch")
                    or control.get("batch") == item.get("batch")
                )
            ]
            control["control_for"] = candidates

    @staticmethod
    def _replicate_groups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in records:
            if item.get("role") == "control" or not item.get("replicate"):
                continue
            key = (
                str(item.get("condition") or ""),
                str(item.get("target") or ""),
                str(item.get("role") or ""),
                str(item.get("batch") or ""),
            )
            grouped[key].append(item)
        return [
            {
                "condition": key[0],
                "target": key[1],
                "role": key[2],
                "batch": key[3],
                "samples": [item["sample"] for item in values],
                "replicates": [item["replicate"] for item in values],
            }
            for key, values in grouped.items()
            if len(values) >= 2
        ]

    @staticmethod
    def _differential_readiness(
        records: list[dict[str, Any]],
        replicate_groups: list[dict[str, Any]],
    ) -> dict[str, Any]:
        comparable_groups = {
            (item["condition"], item["target"])
            for item in replicate_groups
            if item.get("condition")
        }
        reasons: list[str] = []
        if len(replicate_groups) < 2:
            reasons.append("at_least_two_replicated_groups_required")
        if len({condition for condition, _ in comparable_groups}) < 2:
            reasons.append("at_least_two_conditions_required")
        if any(
            item.get("role") != "control" and not item.get("condition")
            for item in records
        ):
            reasons.append("condition_missing_for_one_or_more_samples")
        return {
            "ready": not reasons,
            "reasons": reasons,
            "replicated_group_count": len(replicate_groups),
            "comparison_group_count": len(comparable_groups),
        }


experiment_design_service = ExperimentDesignService()
