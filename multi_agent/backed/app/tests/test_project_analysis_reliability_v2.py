import asyncio
import json
from pathlib import Path

from multi_agent.backed.app.services.business_agent.answer_quality_service import (
    BusinessAnswerQualityService,
)
from multi_agent.backed.app.services.business_agent.bio_skill_reference_service import (
    BioSkillReferenceService,
)
from multi_agent.backed.app.services.business_agent.evidence_card_service import (
    EvidenceCardService,
)
from multi_agent.backed.app.services.business_agent.evidence_catalog_service import (
    EvidenceCatalogService,
)
from multi_agent.backed.app.services.business_agent.read_lineage_service import (
    ReadLineageService,
)
from multi_agent.backed.app.services.business_agent.evidence_reasoning_service import (
    EvidenceReasoningService,
)
from multi_agent.backed.app.services.business_agent.analysis_planner_service import (
    AnalysisPlannerService,
)
from multi_agent.backed.app.services.business_agent.user_assertion_service import (
    UserAssertionService,
)
from multi_agent.backed.app.services.business_agent.response_service import (
    BusinessResponseService,
)
from multi_agent.backed.app.services.business_agent.runtime_service import (
    BusinessAgentRuntimeService,
)
from multi_agent.backed.app.services.project_analysis_service import (
    ProjectAnalysisService,
)


def test_frip_score_contract_keeps_22_24_as_22_24_percent():
    summary = ProjectAnalysisService._build_frip_summary(
        [
            {
                "Sample": "S1",
                "Reads_in_Peaks": "4937248",
                "Mapped_Reads": "22196468",
                "FRiP": "22.24",
            }
        ],
        source_name="FRiP_score.xls",
    )

    assert summary["metrics"][0]["frip_ratio"] == 0.2224


def test_evidence_card_validation_quarantines_inflated_frip():
    valid = EvidenceCardService.from_evidence(
        {
            "metric_key": "frip_ratio",
            "sample": "S1",
            "value": 0.2224,
            "display_value": "22.24%",
            "value_scale": "fraction",
            "display_scale": "percent",
            "numerator_value": 4937248,
            "denominator_value": 22196468,
            "processing_phase": "post_peak_calling_enrichment",
        }
    )
    invalid = {**valid, "evidence_id": "invalid", "value": 22.24}
    result = EvidenceCardService.validate_cards([valid, invalid])

    assert result["valid_count"] == 1
    assert result["quarantined_count"] == 1
    assert result["quarantined_cards"][0]["validation_status"] == "invalid"


def test_evidence_catalog_finds_metric_by_header_not_filename(tmp_path: Path):
    table = tmp_path / "arbitrary_result_table.tsv"
    table.write_text(
        "Sample\tReads_in_Peaks\tMapped_Reads\tFRiP\n"
        "S1\t100\t1000\t10.00\n",
        encoding="utf-8",
    )

    catalog = EvidenceCatalogService.build(tmp_path)
    matched = EvidenceCatalogService.query(catalog, metric_ids=["frip_ratio"])

    assert matched[0]["path"] == "arbitrary_result_table.tsv"
    assert "frip_ratio" in matched[0]["metric_ids"]


def test_evidence_catalog_indexes_stage_metrics_without_claiming_parsed_values(
    tmp_path: Path,
):
    frip = tmp_path / "05.PeakCalling" / "FRiP" / "FRiP_plot.png"
    motif = tmp_path / "06.Motif" / "knownResults.html"
    fragment = tmp_path / "03.FragmentSize" / "insert_size.png"
    for path in (frip, motif, fragment):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"binary")

    catalog = EvidenceCatalogService.build(tmp_path)

    assert EvidenceCatalogService.query(catalog, metric_ids=["frip_ratio"])
    assert EvidenceCatalogService.query(catalog, metric_ids=["motif"])
    assert EvidenceCatalogService.query(catalog, metric_ids=["fragment_size"])


def test_read_lineage_marks_observed_stage_break_and_independent_spike_status():
    lineage = ReadLineageService.build(
        parsed_metrics={
            "qc": [
                {
                    "sample": "PH_Igg",
                    "raw_read_count": 24424528,
                    "clean_read_count": 24338532,
                }
            ],
            "spikein": [
                {
                    "sample": "PH_Igg",
                    "mapped_reads": 6122554,
                }
            ],
            "alignment": [
                {
                    "sample": "PH_Igg",
                    "host_alignment_input_reads": 608988,
                    "total_mapped_reads": 310706,
                    "mt_mapped_reads": 10040,
                }
            ],
            "frip": [
                {
                    "sample": "PH_Igg",
                    "comparison_type": "self_frip",
                    "mapped_reads": 300666,
                }
            ],
        },
        assay_profile={"assay": "cuttag"},
        evidence_cards=[
            {
                "metric_id": "spikein_unique_mapping_rate_percent",
                "value": 24.92,
            }
        ],
    )
    sample = lineage["samples"][0]
    stages = {item["stage_id"]: item for item in sample["stages"]}
    status = lineage["data_status"]["by_metric"]

    assert stages["clean_reads"]["value"] == 24338532
    assert stages["host_alignment_input"]["value"] == 608988
    assert sample["unexplained_breaks"][0]["status"] == "unexplained_stage_break"
    assert status["spikein_unique_mapping_rate_percent"] == "observed"
    assert status["spikein_scaling_factor"] == "not_indexed"


def test_cuttag_skill_filter_excludes_atac_scatac_and_amplicon(tmp_path: Path):
    definitions = {
        "cuttag": (
            "assay: [cuttag]\ncontraindications: [atacseq, scatacseq, amplicon]\n",
            "CUT&Tag FRiP diagnosis",
        ),
        "atac": ("assay: [atacseq]\n", "ATAC-seq FRiP diagnosis"),
        "scatac": ("assay: [scatacseq]\n", "scATAC diagnosis"),
        "amplicon": ("assay: [amplicon]\n", "amplicon alignment diagnosis"),
    }
    for name, (metadata, body) in definitions.items():
        path = tmp_path / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "---\n"
            f"name: {name}\n"
            "target_class: [all]\n"
            "species_scope: [all]\n"
            "required_evidence: [FRiP]\n"
            f"{metadata}"
            "---\n"
            f"# {body}\n"
            "## Decision procedure\nUse project evidence.\n",
            encoding="utf-8",
        )

    service = BioSkillReferenceService(skill_root=tmp_path)
    references = service.select_references(
        question="CUT&Tag FRiP diagnosis",
        target_metrics=["frip_ratio"],
        intent="anomaly_investigation",
        limit=10,
    )
    ids = {item["id"] for item in references}

    assert "local:cuttag/SKILL.md" in ids
    assert "local:atac/SKILL.md" not in ids
    assert "local:scatac/SKILL.md" not in ids
    assert "local:amplicon/SKILL.md" not in ids
    assert len(service.load_full_skills(references, max_skills=1)[0]["content"]) <= 600


def test_compact_reasoning_context_is_valid_json_and_capped_at_12000_chars():
    result = {
        "project_id": "P1",
        "question": "focused FRiP question",
        "question_type": "frip",
        "project_context": {
            "evidence_catalog_summary": {
                "file_count": 100,
                "indexed_metrics": ["frip_ratio"],
            }
        },
        "evidence_reasoning": {
            "target_metrics": ["frip_ratio"],
            "writing_contract": ["direct conclusion"],
            "evidence": [{"metric_id": "frip_ratio", "text": "x" * 2000}] * 12,
            "conclusions": [{"text": "y" * 2000}] * 8,
        },
    }

    context = BusinessResponseService().build_analysis_context(
        analysis_result=result,
        experience_summary={},
    )

    parsed = json.loads(context)

    assert len(context) <= 12000
    assert parsed["context_schema_version"] == "professional-analysis-context-v2"
    assert "project_observations" in parsed


def test_data_status_distinguishes_indexed_not_selected_and_parse_failed():
    lineage = ReadLineageService.build(
        parsed_metrics={},
        evidence_catalog={
            "metric_index": {
                "peak_count": ["PeakStat/peak_count.xls"],
                "correlation": ["Correlation/correlation.xls"],
            }
        },
        selected_files=["Correlation/correlation.xls"],
        evidence_status=[
            {
                "file": "Correlation/correlation.xls",
                "status": "error",
                "error": "bad matrix",
            }
        ],
        user_assertions=[
            {
                "metric_id": "fragment_size",
                "provenance": "user_provided",
                "verification": "unverified",
            }
        ],
        evidence_conflicts=[
            {
                "measurement_id": "mapping_rate_percent",
                "status": "unresolved",
                "values": [51.0, 93.0],
            }
        ],
    )
    status = lineage["data_status"]["by_metric"]

    assert status["peak_count"] == "indexed_not_selected"
    assert status["correlation"] == "selected_but_parse_failed"
    assert status["fragment_size"] == "user_provided_unverified"
    assert status["mapping_rate_percent"] == "conflicting"
    assert "not_observed_in_project_files" not in status.values()


def test_reasoning_preserves_directional_frip_and_correlation_coverage():
    cards = [
        {
            "evidence_id": "frip-self-a",
            "metric_id": "frip_ratio",
            "sample": "IP_A",
            "sample_name": "IP_A",
            "peak_set": "IP_A",
            "comparison_type": "self_frip",
            "pair_type": "experiment_vs_experiment",
            "value": 0.22,
            "display_value": "22%",
            "validation_status": "valid",
        },
        {
            "evidence_id": "frip-a-b",
            "metric_id": "frip_ratio",
            "sample": "IP_A against IP_B",
            "sample_name": "IP_A",
            "peak_set": "IP_B",
            "comparison_type": "cross_frip",
            "pair_type": "experiment_vs_experiment",
            "value": 0.19,
            "display_value": "19%",
            "validation_status": "valid",
        },
        {
            "evidence_id": "frip-b-a",
            "metric_id": "frip_ratio",
            "sample": "IP_B against IP_A",
            "sample_name": "IP_B",
            "peak_set": "IP_A",
            "comparison_type": "cross_frip",
            "pair_type": "experiment_vs_experiment",
            "value": 0.17,
            "display_value": "17%",
            "validation_status": "valid",
        },
        {
            "evidence_id": "corr-a-b",
            "metric_id": "correlation",
            "sample": "IP_A vs IP_B",
            "left_sample": "IP_A",
            "right_sample": "IP_B",
            "pair_type": "experiment_vs_experiment",
            "value": 0.91,
            "validation_status": "valid",
        },
        {
            "evidence_id": "peak-a",
            "metric_id": "peak_count",
            "sample": "IP_A",
            "value": 12000,
            "validation_status": "valid",
        },
    ]
    packet = EvidenceReasoningService.build(
        question="Compare cross-FRiP, peak count and Spearman correlation",
        analysis_plan={
            "target_metrics": ["frip_ratio", "peak_count", "correlation"]
        },
        evidence_cards=cards,
        validated_claims=[],
        analysis_limits=[],
        next_actions=[],
        experiment_design={"samples": []},
        assay_profile={"assay": "cuttag"},
        read_lineage={"data_status": {}, "samples": []},
    )
    frip_table = next(
        table
        for table in packet["relational_tables"]
        if table["table_id"] == "directional_frip_matrix"
    )
    directions = {
        (cell["read_sample"], cell["peak_set"]) for cell in frip_table["cells"]
    }

    assert ("IP_A", "IP_B") in directions
    assert ("IP_B", "IP_A") in directions
    assert "correlation" in packet["evidence_coverage"]["covered_target_metrics"]
    assert any(
        item["relationship"] == "directional_frip_asymmetry"
        for item in packet["derived_relationships"]
    )


def test_planner_recognizes_spikein_and_complexity_metrics():
    spike_plan = AnalysisPlannerService.build_plan(
        question="Review spike-in unique rate and scaling factor",
        project_id="P1",
    )
    complexity_plan = AnalysisPlannerService.build_plan(
        question="Explain fragment size, NRF, PBC1, PBC2 and motif",
        project_id="P1",
    )

    assert "spikein_scaling_factor" in spike_plan["target_metrics"]
    assert "spikein_unique_mapping_rate_percent" in spike_plan["target_metrics"]
    assert {"fragment_size", "nrf", "pbc1", "pbc2", "motif"}.issubset(
        set(complexity_plan["target_metrics"])
    )


def test_user_assertions_are_unverified_and_conditional():
    assertions = UserAssertionService.extract(
        "S1 FRiP=22.24%，peak 数量为 13002，fragment size 为 177bp。",
        known_samples=["S1"],
    )

    assert assertions
    assert all(item["provenance"] == "user_provided" for item in assertions)
    assert all(item["verification"] == "unverified" for item in assertions)
    assert all(item["allowed_usage"] == "conditional_reasoning" for item in assertions)


def test_fact_failure_is_observe_only_for_interactive_project_answers(monkeypatch):
    analysis_result = {
        "evidence_cards": [
            {
                "schema_version": "evidence-card-v2",
                "metric_id": "mapping_rate_percent",
                "sample": "S1",
                "value": 0.63,
                "display_value": "0.63%",
                "value_scale": "percent",
                "source_file": "AlignmentQC.xls",
                "source_field": "Mapping(%)",
            }
        ]
    }
    monkeypatch.setattr(
        BusinessAnswerQualityService,
        "evaluate",
        classmethod(lambda cls, **kwargs: {"passed": False, "score": 20, "issues": []}),
    )

    answer, quality, guard = asyncio.run(
        BusinessAgentRuntimeService._apply_answer_quality_gate(
            answer="S1 mapping rate 为 63%。",
            analysis_result=analysis_result,
            question_route={"route": "project_analysis"},
            harness_guard={"action": "disabled", "passed": True},
        )
    )

    assert answer == "S1 mapping rate 为 63%。"
    assert quality["enforcement_mode"] == "observe_only"
    assert quality["repair_applied"] is False
    assert quality["repair_skip_reason"] == "interactive_project_answers_are_observe_only"
    assert guard["action"] == "disabled"
