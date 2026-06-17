from __future__ import annotations

from typing import Any

from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)


class ReadLineageService:
    """Reconcile read counts across pipeline stages without treating gaps as missing data."""

    VERSION = "reads-lineage-v1"
    ORDERED_STAGES = (
        "raw_reads",
        "clean_reads",
        "spikein_alignment",
        "host_alignment_input",
        "mapped_reads",
        "nuclear_usable_reads",
        "peak_denominator",
    )

    @classmethod
    def build(
        cls,
        *,
        parsed_metrics: dict[str, Any],
        evidence_catalog: dict[str, Any] | None = None,
        assay_profile: dict[str, Any] | None = None,
        quarantined_cards: list[dict[str, Any]] | None = None,
        evidence_cards: list[dict[str, Any]] | None = None,
        evidence_status: list[dict[str, Any]] | None = None,
        selected_files: list[str] | None = None,
        evidence_conflicts: list[dict[str, Any]] | None = None,
        user_assertions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        qc = cls._by_sample(parsed_metrics.get("qc", []) or [])
        spike = cls._by_sample(parsed_metrics.get("spikein", []) or [])
        alignment = cls._by_sample(parsed_metrics.get("alignment", []) or [])
        frip = cls._self_frip_by_sample(parsed_metrics.get("frip", []) or [])
        samples = sorted(set(qc) | set(spike) | set(alignment) | set(frip))
        source_map = cls._source_map(evidence_catalog or {})
        sample_lineages: list[dict[str, Any]] = []

        for sample in samples:
            qc_item = qc.get(sample, {})
            spike_item = spike.get(sample, {})
            align_item = alignment.get(sample, {})
            frip_item = frip.get(sample, {})
            mapped = cls._number(align_item.get("total_mapped_reads"))
            organelle = cls._number(align_item.get("mt_mapped_reads"))
            nuclear = mapped - organelle if mapped is not None and organelle is not None else None
            stages = [
                cls._stage("raw_reads", cls._number(qc_item.get("raw_read_count")), source_map.get("sequencing_depth")),
                cls._stage("clean_reads", cls._number(qc_item.get("clean_read_count")), source_map.get("sequencing_depth")),
                cls._stage(
                    "spikein_alignment",
                    cls._number(spike_item.get("mapped_reads")),
                    source_map.get("spikein_mapped_reads"),
                    branch="spikein_reference",
                ),
                cls._stage(
                    "host_alignment_input",
                    cls._number(align_item.get("host_alignment_input_reads")),
                    source_map.get("mapping_rate_percent"),
                ),
                cls._stage("mapped_reads", mapped, source_map.get("mapping_rate_percent")),
                cls._stage("nuclear_usable_reads", nuclear, source_map.get("mt_rate_percent")),
                cls._stage(
                    "peak_denominator",
                    cls._number(frip_item.get("mapped_reads")),
                    source_map.get("frip_ratio"),
                ),
            ]
            transitions = cls._transitions(stages)
            sample_lineages.append(
                {
                    "sample": sample,
                    "ordered_stage_ids": list(cls.ORDERED_STAGES),
                    "stages": stages,
                    "transitions": transitions,
                    "unexplained_breaks": [
                        item for item in transitions if item.get("status") == "unexplained_stage_break"
                    ],
                }
            )

        data_status = cls._data_status(
            evidence_cards=evidence_cards or [],
            quarantined_cards=quarantined_cards or [],
            assay_profile=assay_profile or {},
            evidence_catalog=evidence_catalog or {},
            evidence_status=evidence_status or [],
            selected_files=selected_files or [],
            evidence_conflicts=evidence_conflicts or [],
            user_assertions=user_assertions or [],
        )
        return {
            "version": cls.VERSION,
            "samples": sample_lineages,
            "data_status": data_status,
            "contracts": {
                "observed_stage_values_are_not_reported_as_missing": True,
                "stage_drops_require_explicit_explanation_evidence": True,
                "spikein_unique_rate_and_scaling_factor_have_independent_status": True,
            },
        }

    @classmethod
    def _transitions(cls, stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {item["stage_id"]: item for item in stages}
        pairs = (
            ("raw_reads", "clean_reads"),
            ("clean_reads", "host_alignment_input"),
            ("host_alignment_input", "mapped_reads"),
            ("mapped_reads", "nuclear_usable_reads"),
            ("nuclear_usable_reads", "peak_denominator"),
        )
        result: list[dict[str, Any]] = []
        for upstream_id, downstream_id in pairs:
            upstream = by_id[upstream_id].get("value")
            downstream = by_id[downstream_id].get("value")
            if upstream is None or downstream is None:
                status = "not_observed"
                ratio = None
            else:
                ratio = downstream / upstream if upstream else None
                status = "observed"
                if (
                    upstream_id == "clean_reads"
                    and downstream_id == "host_alignment_input"
                    and ratio is not None
                    and ratio < 0.8
                ):
                    status = "unexplained_stage_break"
            result.append(
                {
                    "from_stage": upstream_id,
                    "to_stage": downstream_id,
                    "from_value": upstream,
                    "to_value": downstream,
                    "retention_ratio": round(ratio, 6) if ratio is not None else None,
                    "status": status,
                    "explanation": (
                        "Both stage counts are present, but current project evidence does not explain the intervening read loss."
                        if status == "unexplained_stage_break"
                        else ""
                    ),
                }
            )
        return result

    @classmethod
    def _data_status(
        cls,
        *,
        evidence_cards: list[dict[str, Any]],
        quarantined_cards: list[dict[str, Any]],
        assay_profile: dict[str, Any],
        evidence_catalog: dict[str, Any],
        evidence_status: list[dict[str, Any]],
        selected_files: list[str],
        evidence_conflicts: list[dict[str, Any]],
        user_assertions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        observed = {
            metric_schema_service.canonical_id(item.get("metric_id"))
            for item in evidence_cards
            if isinstance(item, dict) and item.get("metric_id") and item.get("value") not in (None, "")
        }
        invalid = {
            metric_schema_service.canonical_id(item.get("metric_id"))
            for item in quarantined_cards
            if isinstance(item, dict) and item.get("metric_id")
        }
        indexed = {
            metric_schema_service.canonical_id(metric_id): [
                cls._normalize_path(path) for path in paths or []
            ]
            for metric_id, paths in (evidence_catalog.get("metric_index") or {}).items()
        }
        selected = {cls._normalize_path(path) for path in selected_files if str(path).strip()}
        status_by_file = {
            cls._normalize_path(item.get("file")): item
            for item in evidence_status
            if isinstance(item, dict) and item.get("file")
        }
        conflict_metrics = cls._conflict_metrics(evidence_conflicts)
        asserted = {
            metric_schema_service.canonical_id(item.get("metric_id"))
            for item in user_assertions
            if isinstance(item, dict) and item.get("metric_id")
        }
        assay = str(assay_profile.get("assay") or "").lower()
        expected = {
            "frip_ratio",
            "mapping_rate_percent",
            "unique_mapping_rate_percent",
            "duplicate_rate_percent",
            "mt_rate_percent",
            "nrf",
            "pbc1",
            "pbc2",
        }
        if assay in {"cuttag", "cutrun", "chipseq"} or any(
            metric.startswith("spikein_") for metric in observed | invalid
        ):
            expected.update(
                {
                    "spikein_unique_mapping_rate_percent",
                    "spikein_scaling_factor",
                }
            )
        items = []
        all_metrics = expected | observed | invalid | set(indexed) | asserted | conflict_metrics
        for metric_id in sorted(all_metrics):
            indexed_files = indexed.get(metric_id, [])
            selected_indexed = [path for path in indexed_files if path in selected]
            parse_errors = [
                {
                    "file": path,
                    "error": status_by_file[path].get("error", ""),
                }
                for path in selected_indexed
                if status_by_file.get(path, {}).get("status") == "error"
            ]
            schema = metric_schema_service.get(metric_id)
            assay_scope = {
                str(item).lower() for item in schema.get("assay_scope", []) or []
            }
            not_applicable = bool(
                assay
                and assay_scope
                and "all" not in assay_scope
                and assay not in assay_scope
            )
            if metric_id in conflict_metrics:
                state = "conflicting"
                reason = "Validated evidence contains unresolved competing values."
            elif metric_id in observed:
                state = "observed"
                reason = "A validated project evidence card was selected and parsed."
            elif not_applicable:
                state = "not_applicable"
                reason = f"The metric schema does not apply to assay={assay}."
            elif selected_indexed:
                state = "selected_but_parse_failed"
                reason = "Indexed evidence was selected, but parsing or validation did not yield a valid card."
            elif indexed_files:
                state = "indexed_not_selected"
                reason = "The project catalog contains this metric, but its evidence was not selected in this analysis turn."
            elif metric_id in asserted:
                state = "user_provided_unverified"
                reason = "The value appears only in the user question and may be used conditionally."
            else:
                state = "not_indexed"
                reason = "The project evidence catalog did not index this metric."
            items.append(
                {
                    "metric_id": metric_id,
                    "state": state,
                    "reason": reason,
                    "indexed_files": indexed_files[:6],
                    "selected_indexed_files": selected_indexed[:6],
                    "parse_errors": parse_errors[:4],
                }
            )
        return {
            "items": items,
            "by_metric": {item["metric_id"]: item["state"] for item in items},
        }

    @staticmethod
    def _normalize_path(value: Any) -> str:
        return str(value or "").replace("\\", "/").strip().lower()

    @classmethod
    def _conflict_metrics(cls, conflicts: list[dict[str, Any]]) -> set[str]:
        metrics: set[str] = set()
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                continue
            metric = conflict.get("metric_id") or conflict.get("measurement_id")
            if metric:
                metrics.add(metric_schema_service.canonical_id(metric))
            for card in conflict.get("cards", []) or []:
                if isinstance(card, dict) and card.get("metric_id"):
                    metrics.add(metric_schema_service.canonical_id(card.get("metric_id")))
        return metrics

    @staticmethod
    def _stage(stage_id: str, value: float | None, source_file: str | None, **extra: Any) -> dict[str, Any]:
        normalized_value: int | float | None = value
        if value is not None and float(value).is_integer():
            normalized_value = int(value)
        return {
            "stage_id": stage_id,
            "value": normalized_value,
            "status": "observed" if value is not None else "not_observed",
            "source_file": source_file or "",
            **extra,
        }

    @staticmethod
    def _by_sample(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            str(item.get("sample") or ""): item
            for item in items
            if isinstance(item, dict) and str(item.get("sample") or "")
        }

    @staticmethod
    def _self_frip_by_sample(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            sample = str(item.get("sample") or "")
            if not sample:
                continue
            if item.get("comparison_type") == "self_frip" or not item.get("peak_set"):
                result[sample] = item
        return result

    @staticmethod
    def _source_map(catalog: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for metric_id, paths in (catalog.get("metric_index") or {}).items():
            if paths:
                result[str(metric_id)] = str(paths[0])
        return result

    @staticmethod
    def _number(value: Any) -> float | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        text = str(value).replace(",", "").strip()
        if "(" in text:
            text = text.split("(", 1)[0].strip()
        text = text.rstrip("%")
        try:
            return float(text)
        except (TypeError, ValueError):
            return None


read_lineage_service = ReadLineageService()
