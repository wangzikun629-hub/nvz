from __future__ import annotations

from typing import Any


class AssayAnalysisService:
    """Assay- and target-aware evidence requirements and conclusion readiness."""

    VERSION = "assay-analysis-v1"
    PROFILES = {
        "cuttag": {
            "required_chain": [
                "sequencing_depth",
                "mapping_rate_percent",
                "mt_rate_percent",
                "nrf",
                "pbc1",
                "peak_count",
                "frip_ratio",
                "correlation",
            ],
            "controls": ["igg", "input"],
        },
        "chipseq": {
            "required_chain": [
                "sequencing_depth",
                "mapping_rate_percent",
                "duplicate_rate_percent",
                "peak_count",
                "frip_ratio",
                "correlation",
            ],
            "controls": ["input", "igg"],
        },
        "cutrun": {
            "required_chain": [
                "sequencing_depth",
                "mapping_rate_percent",
                "mt_rate_percent",
                "nrf",
                "pbc1",
                "peak_count",
                "frip_ratio",
                "correlation",
            ],
            "controls": ["igg", "input"],
        },
        "atacseq": {
            "required_chain": [
                "sequencing_depth",
                "mapping_rate_percent",
                "mt_rate_percent",
                "duplicate_rate_percent",
                "tss_enrichment",
                "fragment_size",
                "peak_count",
                "frip_ratio",
                "correlation",
            ],
            "controls": [],
        },
        "rnaseq": {
            "required_chain": [
                "sequencing_depth",
                "mapping_rate_percent",
                "unique_mapping_rate_percent",
                "duplicate_rate_percent",
                "mrna_ratio_percent",
                "rrna_ratio_percent",
                "exon_ratio_percent",
                "intronic_ratio_percent",
                "intergenic_ratio_percent",
                "detected_gene_count",
                "correlation",
            ],
            "controls": [],
        },
        "generic": {
            "required_chain": [
                "sequencing_depth",
                "mapping_rate_percent",
                "biological_replicates",
            ],
            "controls": [],
        },
    }

    @classmethod
    def build(
        cls,
        *,
        project_context: dict[str, Any],
        evidence_cards: list[dict[str, Any]],
        experiment_design: dict[str, Any],
    ) -> dict[str, Any]:
        config = project_context.get("config") if isinstance(project_context.get("config"), dict) else {}
        assay_raw = " ".join(
            str((config or {}).get(key) or "")
            for key in ("assay", "project_type", "Sequencing", "library_type")
        )
        assay = cls._assay(assay_raw)
        profile = cls.PROFILES.get(assay, cls.PROFILES["generic"])
        targets = [
            str(item.get("target") or "")
            for item in experiment_design.get("samples", []) or []
            if isinstance(item, dict) and item.get("target")
        ]
        target_class = cls._target_class(targets)
        available = {
            str(card.get("metric_id") or "")
            for card in evidence_cards
            if isinstance(card, dict)
            and card.get("value") is not None
        }
        if any(
            item.get("raw_reads") or item.get("clean_reads")
            for item in project_context.get("samples", []) or []
            if isinstance(item, dict)
        ):
            available.add("sequencing_depth")
        if experiment_design.get("replicate_groups"):
            available.add("biological_replicates")
        missing = [
            metric for metric in profile["required_chain"] if metric not in available
        ]
        controls_present = any(
            item.get("role") == "control"
            for item in experiment_design.get("samples", []) or []
            if isinstance(item, dict)
        )
        spikein_enabled = str((config or {}).get("spikein_analysis") or "").lower() in {
            "yes",
            "true",
            "1",
            "on",
        }
        specialized_rules: list[str] = []
        if target_class == "histone_broad_repressive":
            specialized_rules.append("broad_repressive_histone_marks_require_broad_peak_and_domain_level_interpretation")
        elif target_class == "histone_active_mark":
            specialized_rules.append("active_histone_marks_require_promoter_enhancer_aware_peak_and_signal_interpretation")
        elif target_class == "transcription_factor":
            specialized_rules.append("transcription_factors_require_narrow_peak_and_motif-aware interpretation")
        if assay == "atacseq":
            specialized_rules.append("atacseq_requires_tss_enrichment_and_nucleosomal_fragment_periodicity")
        if assay == "rnaseq":
            specialized_rules.append("rnaseq_mrna_ratio_below_30pct_indicates_ribosomal_or_genomic_contamination")
            specialized_rules.append("rnaseq_rrna_ratio_above_30pct_indicates_insufficient_rrna_depletion_or_kit_failure")
            specialized_rules.append("rnaseq_low_mapping_rate_may_reflect_reference_genome_mismatch_or_contamination")
            specialized_rules.append("rnaseq_unique_mapping_rate_interpretation_depends_on_aligner_multimapping_strategy")
            specialized_rules.append("rnaseq_high_duplicate_rate_may_be_normal_for_highly_expressed_genes_not_equivalent_to_library_failure")
            specialized_rules.append("rnaseq_detected_gene_count_must_be_interpreted_relative_to_species_and_library_depth")
            specialized_rules.append("rnaseq_low_exon_ratio_indicates_genomic_dna_contamination_or_rna_degradation")
            specialized_rules.append("rnaseq_high_intronic_ratio_suggests_premrna_contamination_or_incomplete_splicing")
            specialized_rules.append("rnaseq_correlation_is_based_on_gene_expression_counts_not_genomic_bins")
        if assay in {"cuttag", "cutrun"}:
            specialized_rules.append("cuttag_cutrun_controls_and_target_class_must_not_share_one_threshold_contract")
        if any("igg" in target.lower() for target in targets):
            specialized_rules.append("igg_is_a_background_control_not_an_experimental_replicate")
        if spikein_enabled:
            specialized_rules.append("spikein_comparisons_require_verified_scaling_or_normalization_parameters")
            if not any(metric.startswith("spikein_") for metric in available):
                missing.append("spikein_normalization_evidence")
        if profile["controls"] and not controls_present:
            missing.append("matched_control")
        return {
            "version": cls.VERSION,
            "assay": assay,
            "assay_raw": assay_raw.strip(),
            "target_class": target_class,
            "targets": sorted(set(targets)),
            "required_evidence_chain": list(profile["required_chain"]),
            "available_evidence": sorted(available),
            "missing_evidence": list(dict.fromkeys(missing)),
            "specialized_rules": specialized_rules,
            "spikein_enabled": spikein_enabled,
            "conclusion_readiness": {
                "project_diagnosis_ready": not missing,
                "differential_analysis_ready": bool(
                    experiment_design.get("differential_analysis", {}).get("ready")
                )
                and (not spikein_enabled or "spikein_normalization_evidence" not in missing),
            },
        }

    @classmethod
    def detect_assay(cls, value: str) -> str:
        """公开版 _assay()，供其他服务（如代码语义解析 agent）复用同一套 assay
        归一化逻辑，避免各处各写一套关键词判断。识别不出时返回 'generic'，
        调用方应把它当作"不确定"处理，而不是当成一个真实存在的 assay 类型。
        """
        return cls._assay(value)

    @staticmethod
    def _assay(value: str) -> str:
        normalized = value.lower().replace("&", "").replace("-", "")
        if "cuttag" in normalized or "cutandtag" in normalized:
            return "cuttag"
        if "chipseq" in normalized or "chip" in normalized:
            return "chipseq"
        if "cutrun" in normalized or "cutandrun" in normalized:
            return "cutrun"
        if "atacseq" in normalized or "atac" in normalized:
            return "atacseq"
        if "rnaseq" in normalized or "transcriptome" in normalized or "mrna" in normalized:
            return "rnaseq"
        return "generic"

    @staticmethod
    def _target_class(targets: list[str]) -> str:
        lowered = " ".join(targets).lower()
        if any(token in lowered for token in ("h3k9me3", "h3k27me3")):
            return "histone_broad_repressive"
        if any(token in lowered for token in ("h3k27ac", "h3k4me1", "h3k4me3")):
            return "histone_active_mark"
        if any(token in lowered for token in ("igg", "input", "control")) and len(targets) == 1:
            return "control_only"
        if targets:
            return "transcription_factor"
        return "unknown"


assay_analysis_service = AssayAnalysisService()
