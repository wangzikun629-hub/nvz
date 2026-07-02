from pathlib import Path

from multi_agent.backed.app.services.business_agent.analysis_planner_service import (
    AnalysisPlannerService,
)
from multi_agent.backed.app.services.business_agent.answer_quality_service import (
    BusinessAnswerQualityService,
)
from multi_agent.backed.app.services.business_agent.claim_service import ClaimService
from multi_agent.backed.app.services.business_agent.experiment_design_service import (
    ExperimentDesignService,
)
from multi_agent.backed.app.services.business_agent.fact_verification_service import (
    FactVerificationService,
)
from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    MetricSchemaService,
)
from multi_agent.backed.app.services.project_context_builder_service import (
    ProjectContextBuilderService,
)
from multi_agent.backed.app.services.project_file_parser_service import (
    ProjectFileParserService,
)


def test_metric_schema_preserves_percent_scale_and_frip_fraction():
    mapping = MetricSchemaService.normalize(
        "mapping_rate_percent",
        "0.63",
        source_field="Mapping(%)",
    )
    frip = MetricSchemaService.normalize(
        "frip_ratio",
        "12.18",
        source_field="percent",
        numerator=4547806,
        denominator=37350878,
    )

    assert mapping["value"] == 0.63
    assert mapping["display_value"] == "0.63%"
    assert round(frip["value"], 4) == 0.1218
    assert frip["display_value"] == "12.18%"
    assert frip["issues"] == []


def test_alignment_parser_uses_canonical_nrf_pbc_definitions():
    # 2026-07-02 修复历史测试债：ProjectAnalysisService._build_alignment_summary 早已
    # 拆分迁移到 ProjectFileParserService.build_alignment_summary（公开方法），这个测试
    # 文件当时没跟着更新，一直调用不存在的旧私有方法名。这里改指向现有实现，断言内容
    # 不变——仍然验证 NRF/PBC1/PBC2 走 metric_schema_service 的规范定义解析。
    summary = ProjectFileParserService.build_alignment_summary(
        [
            {
                "Sample_ID": "S1",
                "Mapping_Rate": "0.63%",
                "NRF": "0.7894",
                "PBC1": "0.8123",
                "PBC2": "5.52",
            }
        ]
    )
    metric = summary["metrics"][0]

    assert metric["mapping_rate_percent"] == 0.63
    assert metric["nrf"] == 0.7894
    assert metric["pbc1"] == 0.8123
    assert metric["pbc2"] == 5.52
    assert MetricSchemaService.get("pbc2")["denominator"] == (
        "locations with exactly two fragments"
    )


def test_samplelist_header_builds_structured_experiment_design(tmp_path: Path):
    samplelist = tmp_path / "samplelist"
    samplelist.write_text(
        "sample\tfastq_1\tfastq_2\tcondition\treplicate\ttarget\trole\tcontrol_for\tbatch\n"
        "PH_H3K27ac_R1\ta_1.fq\ta_2.fq\tPH\t1\tH3K27ac\texperimental\t\tB1\n"
        "PH_H3K27ac_R2\tb_1.fq\tb_2.fq\tPH\t2\tH3K27ac\texperimental\t\tB1\n"
        "PH_IgG\tc_1.fq\tc_2.fq\tPH\t\tIgG\tcontrol\tPH_H3K27ac_R1,PH_H3K27ac_R2\tB1\n",
        encoding="utf-8",
    )

    samples = ProjectContextBuilderService.parse_samplelist(samplelist)
    design = ExperimentDesignService.build(samples)

    assert design["replicate_groups"][0]["samples"] == [
        "PH_H3K27ac_R1",
        "PH_H3K27ac_R2",
    ]
    control = next(item for item in design["samples"] if item["sample"] == "PH_IgG")
    assert control["role"] == "control"
    assert control["control_for"] == ["PH_H3K27ac_R1", "PH_H3K27ac_R2"]


def test_correlation_is_stratified_by_experiment_design():
    samples = [
        {"sample": "PH_H3K27ac_R1", "design_fields": {}},
        {"sample": "PH_H3K27ac_R2", "design_fields": {}},
        {"sample": "PH_IgG", "design_fields": {}},
    ]
    design = ExperimentDesignService.build(samples)
    summary = ProjectFileParserService.build_correlation_summary(
        [
            {
                "Sample": "PH_H3K27ac_R1",
                "PH_H3K27ac_R1": "1",
                "PH_H3K27ac_R2": "0.92",
                "PH_IgG": "0.15",
            },
            {
                "Sample": "PH_H3K27ac_R2",
                "PH_H3K27ac_R1": "0.92",
                "PH_H3K27ac_R2": "1",
                "PH_IgG": "0.18",
            },
            {
                "Sample": "PH_IgG",
                "PH_H3K27ac_R1": "0.15",
                "PH_H3K27ac_R2": "0.18",
                "PH_IgG": "1",
            },
        ]
    )
    stratified = ProjectFileParserService.stratify_correlation_summary(summary, design)

    assert stratified["strata"]["biological_replicates"]["pair_count"] == 1
    assert stratified["strata"]["experiment_vs_control"]["pair_count"] == 2
    assert stratified["replicate_min_pair"]["value"] == 0.92


def test_frip_matrix_keeps_self_and_cross_frip():
    summary = ProjectFileParserService.build_frip_summary(
        [
            {
                "file": "S1",
                "featureType": "S1",
                "percent": "12.18",
                "featureReadCount": "1218",
                "totalReadCount": "10000",
            },
            {
                "file": "S1",
                "featureType": "S2",
                "percent": "8.32",
                "featureReadCount": "832",
                "totalReadCount": "10000",
            },
        ]
    )

    assert len(summary["metrics"]) == 2
    assert summary["metrics"][0]["comparison_type"] == "self_frip"
    assert summary["metrics"][1]["comparison_type"] == "cross_frip"
    assert summary["metrics"][1]["peak_set"] == "S2"
    merged = ProjectFileParserService.merge_frip_metrics(
        [{"sample": "S1", "frip_ratio": 0.1218}],
        summary["metrics"],
    )
    assert len(merged) == 2
    assert merged[0]["reads_in_peaks"] == "1218"


def test_fact_verifier_catches_percent_inflation_and_causal_overstatement():
    card = {
        "schema_version": "evidence-card-v2",
        "metric_id": "mapping_rate_percent",
        "sample": "S1",
        "value": 0.63,
        "display_value": "0.63%",
        "value_scale": "percent",
        "source_file": "AlignmentQC.xls",
        "source_field": "Mapping(%)",
    }
    result = FactVerificationService.verify(
        answer="S1 的 mapping rate 是 63%，这导致了实验失败。",
        analysis_result={"evidence_cards": [card], "validated_claims": []},
    )

    rules = {item["rule"] for item in result["issues"]}
    assert "numeric_claim_not_found_in_project_evidence" in rules
    assert "causal_overstatement_without_verified_cause" in rules
    assert result["passed"] is False


def test_fact_verifier_rejects_differential_analysis_without_replicates():
    design = ExperimentDesignService.build(
        [
            {"sample": "Control", "design_fields": {"condition": "control"}},
            {"sample": "Treat", "design_fields": {"condition": "treat"}},
        ]
    )
    result = FactVerificationService.verify(
        answer="可以直接进行差异分析并解释差异 peak。",
        analysis_result={"experiment_design": design, "evidence_cards": []},
    )

    assert result["differential_precondition_error_count"] == 1
    assert result["contracts"]["differential_analysis_preconditions_respected"] is False


def test_claims_expose_four_level_causal_contract():
    cards = [
        {
            "evidence_id": "ev-corr",
            "metric_id": "correlation",
            "measurement_id": "spearman_correlation",
            "metric": "Correlation",
            "sample": "S1 vs S2",
            "value": 0.82,
            "display_value": "0.8200",
            "denominator": "paired bins",
            "threshold_source": "",
        }
    ]
    claims = ClaimService.build_claims(
        evidence_cards=cards,
        cause_graph={
            "ranked_causes": [
                {
                    "cause_id": "low_complexity",
                    "label": "Low complexity",
                    "support_level": "supported",
                    "supporting_evidence": [
                        {"evidence_id": "ev-a"},
                        {"evidence_id": "ev-b"},
                    ],
                    "contradicting_evidence": [],
                    "missing_evidence": [],
                }
            ]
        },
    )

    assert claims[0]["causal_level"] == "associated_phenomenon"
    assert claims[1]["causal_level"] == "verified_cause"


def test_planner_and_quality_expose_professional_contracts():
    plan = AnalysisPlannerService.build_plan(
        question="请完整诊断这个项目的低 FRiP、相关性和重复设计问题",
        project_id="P1",
    )
    quality = BusinessAnswerQualityService.evaluate(
        answer="S1 FRiP 为 12.18%，来源 FRiP_raw.txt::percent。当前只能作为直接观察。",
        analysis_result={
            "analysis_plan": plan,
            "evidence_cards": [
                {
                    "schema_version": "evidence-card-v2",
                    "metric_id": "frip_ratio",
                    "sample": "S1",
                    "value": 0.1218,
                    "display_value": "12.18%",
                    "value_scale": "fraction",
                    "source_file": "FRiP_raw.txt",
                    "source_field": "percent",
                }
            ],
        },
    )

    assert plan["response_plan"]["complexity"] == "comprehensive"
    assert set(quality["dimensions"]) >= {
        "fact_correctness",
        "evidence_coverage",
        "unsupported_conclusion_control",
        "unit_accuracy",
        "experimental_design_discipline",
        "causal_discipline",
    }
