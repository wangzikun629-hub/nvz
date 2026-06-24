from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any


class MetricSchemaService:
    """Canonical metric definitions and scale-aware value normalization."""

    SCHEMA_VERSION = "metric-schema-v1"

    METRICS: dict[str, dict[str, Any]] = {
        "adapter_percent": {
            "label": "Adapter read-through rate",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "adapter_affected_reads / raw_reads * 100",
            "numerator": "reads affected by adapter sequence",
            "denominator": "raw reads examined",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "verifier_contract": "citation_only",
        },
        "clean_read_retention_percent": {
            "label": "Read-pair retention after trimming",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "written_pairs / processed_pairs * 100",
            "numerator": "read pairs written after trimming",
            "denominator": "processed read pairs before trimming output",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "verifier_contract": "display_value_only",
        },
        "frip_ratio": {
            "label": "FRiP",
            "unit": "fraction",
            "display_unit": "%",
            "value_scale": "fraction",
            "valid_range": [0.0, 1.0],
            "formula": "reads_in_peaks / usable_mapped_reads_or_fragments",
            "numerator": "reads in called peaks",
            "denominator": "usable mapped reads/fragments evaluated against the peak set",
            "source_scale": "fraction",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
            "verifier_contract": "strict_formula_recalculation",
        },
        "mapping_rate_percent": {
            "label": "Mapping rate",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "mapped_reads / alignment_input_reads * 100",
            "numerator": "mapped reads",
            "denominator": "alignment input reads",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "verifier_contract": "strict_formula_recalculation",
        },
        "unique_mapping_rate_percent": {
            "label": "Unique mapping rate",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "uniquely_mapped_reads / alignment_input_reads * 100",
            "numerator": "uniquely mapped reads",
            "denominator": "alignment input reads",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "verifier_contract": "strict_formula_recalculation",
        },
        "duplicate_rate_percent": {
            "label": "Duplicate rate",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "duplicate_reads_or_fragments / examined_mapped_reads_or_fragments * 100",
            "numerator": "duplicate reads/fragments",
            "denominator": "examined mapped reads/fragments",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "verifier_contract": "citation_only",
        },
        "picard_duplicate_pair_rate_percent": {
            "label": "Picard duplicate pair rate",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "PERCENT_DUPLICATION * 100",
            "numerator": "duplicate read pairs",
            "denominator": "Picard examined read pairs",
            "source_scale": "fraction",
            "assay_scope": ["all"],
            "verifier_contract": "citation_only",
        },
        "mt_rate_percent": {
            "label": "Organelle alignment rate",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "organelle_mapped_reads / mapped_reads * 100",
            "numerator": "organelle mapped reads",
            "denominator": "mapped reads",
            "source_scale": "percent",
            "assay_scope": ["all"],
        },
        "q20_ratio": {
            "label": "Q20",
            "unit": "fraction",
            "display_unit": "%",
            "value_scale": "fraction",
            "valid_range": [0.0, 1.0],
            "formula": "bases_with_quality_ge_20 / total_bases",
            "numerator": "bases with Q >= 20",
            "denominator": "bases in the reported read population",
            "source_scale": "percent",
            "assay_scope": ["all"],
        },
        "q30_ratio": {
            "label": "Q30",
            "unit": "fraction",
            "display_unit": "%",
            "value_scale": "fraction",
            "valid_range": [0.0, 1.0],
            "formula": "bases_with_quality_ge_30 / total_bases",
            "numerator": "bases with Q >= 30",
            "denominator": "bases in the reported read population",
            "source_scale": "percent",
            "assay_scope": ["all"],
        },
        "nrf": {
            "label": "NRF",
            "unit": "fraction",
            "display_unit": "",
            "value_scale": "fraction",
            "valid_range": [0.0, 1.0],
            "formula": "distinct_genomic_locations / total_mapped_fragments",
            "numerator": "distinct genomic locations",
            "denominator": "total mapped fragments",
            "source_scale": "fraction",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
        },
        "pbc1": {
            "label": "PBC1",
            "unit": "fraction",
            "display_unit": "",
            "value_scale": "fraction",
            "valid_range": [0.0, 1.0],
            "formula": "locations_with_exactly_one_fragment / distinct_genomic_locations",
            "numerator": "locations with exactly one fragment",
            "denominator": "distinct genomic locations",
            "source_scale": "fraction",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
        },
        "pbc2": {
            "label": "PBC2",
            "unit": "ratio",
            "display_unit": "",
            "value_scale": "ratio",
            "valid_range": [0.0, None],
            "formula": "locations_with_exactly_one_fragment / locations_with_exactly_two_fragments",
            "numerator": "locations with exactly one fragment",
            "denominator": "locations with exactly two fragments",
            "source_scale": "ratio",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
        },
        "spikein_mapped_reads": {
            "label": "Spike-in mapped reads",
            "unit": "reads",
            "display_unit": "reads",
            "value_scale": "count",
            "valid_range": [0.0, None],
            "formula": "count(reads_mapped_to_spikein_reference)",
            "numerator": "reads mapped to spike-in reference",
            "denominator": "",
            "source_scale": "count",
            "assay_scope": ["cuttag", "chipseq", "cutrun"],
        },
        "spikein_unique_mapping_rate_percent": {
            "label": "Spike-in unique mapping rate",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "unique_spikein_mapped_reads / spikein_alignment_input_reads * 100",
            "numerator": "uniquely mapped spike-in reads",
            "denominator": "spike-in alignment input reads",
            "source_scale": "percent",
            "assay_scope": ["cuttag", "chipseq", "cutrun"],
        },
        "spikein_scaling_factor": {
            "label": "Spike-in scaling factor",
            "unit": "factor",
            "display_unit": "",
            "value_scale": "number",
            "valid_range": [0.0, None],
            "formula": "project_defined_normalization_constant",
            "numerator": "",
            "denominator": "",
            "source_scale": "number",
            "assay_scope": ["cuttag", "chipseq", "cutrun"],
        },
        "correlation": {
            "label": "Sample correlation",
            "unit": "coefficient",
            "display_unit": "",
            "value_scale": "coefficient",
            "valid_range": [-1.0, 1.0],
            "formula": "correlation(signal_vector_a, signal_vector_b)",
            "numerator": "",
            "denominator": "paired signal features/bins",
            "source_scale": "coefficient",
            "assay_scope": ["all"],
            "verifier_contract": "strict_formula_recalculation",
        },
        "peak_count": {
            "label": "Peak count",
            "unit": "peaks",
            "display_unit": "peaks",
            "value_scale": "count",
            "valid_range": [0.0, None],
            "formula": "count(called_peak_records)",
            "numerator": "called peak records",
            "denominator": "",
            "source_scale": "count",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
            "verifier_contract": "display_value_only",
        },
        "sequencing_depth": {
            "label": "Sequencing depth",
            "unit": "reads",
            "display_unit": "reads",
            "value_scale": "count",
            "valid_range": [0.0, None],
            "formula": "count(input_or_clean_reads)",
            "numerator": "input or clean reads",
            "denominator": "",
            "source_scale": "count",
            "assay_scope": ["all"],
            "verifier_contract": "display_value_only",
        },
        "control_binding_status": {
            "label": "Control binding status",
            "unit": "categorical",
            "display_unit": "",
            "value_scale": "categorical",
            "valid_range": None,
            "formula": "qualitative_assessment_of_control_background_binding",
            "numerator": "",
            "denominator": "",
            "source_scale": "categorical",
            "assay_scope": ["cuttag", "chipseq", "cutrun"],
            "verifier_contract": "non_numeric_design_status",
        },
        # ── RNA-seq 专属指标 ───────────────────────────────────────────────
        "mrna_ratio_percent": {
            "label": "mRNA reads ratio",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "mRNA_reads / total_mapped_reads * 100",
            "numerator": "mRNA aligned reads",
            "denominator": "total mapped reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "verifier_contract": "citation_only",
        },
        "rrna_ratio_percent": {
            "label": "rRNA reads ratio",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "rRNA_reads / total_mapped_reads * 100",
            "numerator": "rRNA aligned reads",
            "denominator": "total mapped reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "verifier_contract": "citation_only",
        },
        "exon_ratio_percent": {
            "label": "Exon reads ratio",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "exon_reads / total_mapped_reads * 100",
            "numerator": "exon aligned reads",
            "denominator": "total mapped reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "verifier_contract": "citation_only",
        },
        "intronic_ratio_percent": {
            "label": "Intronic reads ratio",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "intronic_reads / total_mapped_reads * 100",
            "numerator": "intronic reads",
            "denominator": "total mapped reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "verifier_contract": "citation_only",
        },
        "intergenic_ratio_percent": {
            "label": "Intergenic reads ratio",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "intergenic_reads / total_mapped_reads * 100",
            "numerator": "intergenic reads",
            "denominator": "total mapped reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "verifier_contract": "citation_only",
        },
        "detected_gene_count": {
            "label": "Detected genes",
            "unit": "genes",
            "display_unit": "genes",
            "value_scale": "count",
            "valid_range": [0.0, None],
            "formula": "count(genes_with_expression_above_zero)",
            "numerator": "genes with non-zero expression",
            "denominator": "",
            "source_scale": "count",
            "assay_scope": ["rnaseq"],
            "verifier_contract": "display_value_only",
        },
    }

    ALIASES = {
        "frip": "frip_ratio",
        "mapping": "mapping_rate_percent",
        "mapping_rate": "mapping_rate_percent",
        "unique": "unique_mapping_rate_percent",
        "unique_mapping_rate": "unique_mapping_rate_percent",
        "duplicate": "duplicate_rate_percent",
        "duplication": "duplicate_rate_percent",
        "picard_duplicate_pair_rate_percent": "duplicate_rate_percent",
        "mt_ratio": "mt_rate_percent",
        "chrmt_pt_rate_percent": "mt_rate_percent",
        "spikein_unique_rate": "spikein_unique_mapping_rate_percent",
        # RNA-seq aliases
        "mrna_ratio": "mrna_ratio_percent",
        "rrna_ratio": "rrna_ratio_percent",
        "exon_ratio": "exon_ratio_percent",
        "intronic_ratio": "intronic_ratio_percent",
        "intergenic_ratio": "intergenic_ratio_percent",
        "gene_count": "detected_gene_count",
        "detected_genes": "detected_gene_count",
    }

    @classmethod
    def canonical_id(cls, metric_id: Any) -> str:
        normalized = str(metric_id or "").strip().lower()
        return cls.ALIASES.get(normalized, normalized)

    @classmethod
    def get(cls, metric_id: Any) -> dict[str, Any]:
        canonical = cls.canonical_id(metric_id)
        schema = deepcopy(cls.METRICS.get(canonical, {}))
        if schema:
            schema["metric_id"] = canonical
            schema["schema_version"] = cls.SCHEMA_VERSION
        return schema

    @classmethod
    def export_schema(cls) -> dict[str, Any]:
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "metrics": {
                metric_id: {**deepcopy(schema), "metric_id": metric_id}
                for metric_id, schema in cls.METRICS.items()
            },
        }

    @classmethod
    def verifier_contract(cls, metric_id: Any) -> str:
        schema = cls.get(metric_id)
        return str(schema.get("verifier_contract") or "strict_formula_recalculation")

    @classmethod
    def normalize(
        cls,
        metric_id: Any,
        raw_value: Any,
        *,
        source_field: str = "",
        source_scale: str = "",
        numerator: Any = None,
        denominator: Any = None,
    ) -> dict[str, Any]:
        canonical = cls.canonical_id(metric_id)
        schema = cls.get(canonical)
        parsed = cls._number(raw_value)
        issues: list[dict[str, Any]] = []
        if parsed is None:
            return {
                "metric_id": canonical,
                "value": None,
                "display_value": "-",
                "input_scale": "unknown",
                "conversion": "none",
                "valid": False,
                "issues": [{"rule": "non_numeric_metric_value", "raw_value": raw_value}],
                "schema": schema,
            }

        input_scale = (
            str(source_scale).strip().lower()
            or cls._input_scale(raw_value, source_field, schema)
        )
        expected_scale = str(schema.get("value_scale") or "number")
        value = parsed
        conversion = "identity"
        if expected_scale == "fraction" and input_scale == "percent":
            value = parsed / 100.0
            conversion = "percent_to_fraction"
        elif expected_scale == "percent" and input_scale == "fraction":
            value = parsed * 100.0
            conversion = "fraction_to_percent"
        elif expected_scale == "percent" and parsed <= 1.0 and input_scale == "number":
            value = parsed * 100.0
            conversion = "implicit_fraction_to_percent"

        numerator_value = cls._number(numerator)
        denominator_value = cls._number(denominator)
        if numerator_value is not None and denominator_value not in (None, 0):
            recalculated = numerator_value / denominator_value
            if expected_scale == "percent":
                recalculated *= 100.0
            rounding_tolerance = (
                5e-5
                if expected_scale == "fraction"
                else 5e-3
                if expected_scale == "percent"
                else 1e-6
            )
            tolerance = max(rounding_tolerance, abs(value) * 1e-3)
            if abs(recalculated - value) > tolerance:
                issues.append(
                    {
                        "rule": "formula_recalculation_mismatch",
                        "observed": value,
                        "recalculated": recalculated,
                    }
                )

        valid_range = schema.get("valid_range") or [None, None]
        lower = valid_range[0] if len(valid_range) > 0 else None
        upper = valid_range[1] if len(valid_range) > 1 else None
        if (lower is not None and value < lower) or (upper is not None and value > upper):
            issues.append(
                {
                    "rule": "metric_value_out_of_physical_range",
                    "value": value,
                    "valid_range": valid_range,
                }
            )

        return {
            "metric_id": canonical,
            "value": value,
            "display_value": cls.format_value(canonical, value),
            "input_scale": input_scale,
            "value_scale": expected_scale,
            "conversion": conversion,
            "valid": not issues,
            "issues": issues,
            "schema": schema,
        }

    @classmethod
    def format_value(cls, metric_id: Any, value: Any) -> str:
        schema = cls.get(metric_id)
        number = cls._number(value)
        if number is None:
            return "-"
        scale = str(schema.get("value_scale") or "number")
        display_unit = str(schema.get("display_unit") or "")
        if scale == "fraction" and display_unit == "%":
            return f"{number * 100:.2f}%"
        if scale == "percent":
            return f"{number:.2f}%"
        if scale == "count":
            return str(int(round(number)))
        return f"{number:.4f}".rstrip("0").rstrip(".")

    @classmethod
    def _input_scale(
        cls,
        raw_value: Any,
        source_field: str,
        schema: dict[str, Any],
    ) -> str:
        raw_text = str(raw_value or "").strip().lower()
        field = str(source_field or "").strip().lower()
        if "percent_duplication" in field:
            return "fraction"
        if "%" in raw_text or "%" in field or "percent" in field:
            return "percent"
        if any(token in field for token in ("rate",)) and str(schema.get("value_scale") or "") == "percent":
            return "percent"
        if any(token in field for token in ("fraction", "proportion")):
            return "fraction"
        return str(schema.get("source_scale") or schema.get("value_scale") or "number")

    @staticmethod
    def _number(value: Any) -> float | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        text = str(value).strip()
        if "(" in text and "%)" in text:
            text = text.rsplit("(", 1)[1].split("%", 1)[0]
        text = text.replace(",", "").rstrip("%").strip()
        try:
            parsed = float(text)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None


metric_schema_service = MetricSchemaService()
