import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from multi_agent.backed.app.harness.expert_eval.evaluator import (
    evaluate_response,
    load_cases,
)
from multi_agent.backed.app.infrastructure.tools.local import project_reader
from multi_agent.backed.app.harness.model_comparison import run_comparison
from multi_agent.backed.app.api.routers import _require_api_key
from multi_agent.backed.app.config.settings import Settings, settings
from multi_agent.backed.app.repositories.project_memory_repository import project_memory_repository
from multi_agent.backed.app.services.business_agent.bio_skill_reference_service import (
    BioSkillReferenceService,
)
from multi_agent.backed.app.services.business_agent.claim_service import ClaimService
from multi_agent.backed.app.services.business_agent.evidence_card_service import EvidenceCardService
from multi_agent.backed.app.services.project_analysis_verifier_service import (
    ProjectAnalysisVerifierService,
)
from multi_agent.backed.app.services.project_expert_tool_service import (
    ProjectExpertToolService,
)
from multi_agent.backed.app.services.project_memory_service import ProjectMemoryService


def test_bioskills_indexes_all_files_and_loads_selected_full_skill(tmp_path: Path):
    skill_root = tmp_path / "bioSkills"
    for index in range(85):
        path = skill_root / f"group-{index}" / "SKILL.md"
        path.parent.mkdir(parents=True)
        content = (
            "---\n"
            f"name: skill-{index}\n"
            f"description: generic workflow skill {index}\n"
            "---\n"
            f"# Skill {index}\n"
            "General workflow guidance.\n"
        )
        if index == 84:
            content += "Mitochondrial alignment mapping duplicate NRF PBC review.\n"
        path.write_text(content, encoding="utf-8")

    service = BioSkillReferenceService(skill_root=skill_root)
    references = service.select_references(
        question="review mitochondrial alignment NRF PBC",
        target_metrics=["mt_rate_percent", "duplicate_rate_percent"],
        intent="anomaly_investigation",
        limit=2,
    )
    loaded = service.load_full_skills(references, max_skills=1)

    assert service.index_stats()["indexed_local_skills"] == 85
    assert references[0]["id"] == "local:group-84/SKILL.md"
    assert "Mitochondrial alignment" in loaded[0]["content"]


def test_alignment_expert_loop_collects_numerator_denominator_and_stops(tmp_path: Path):
    alignment = tmp_path / "Alignment" / "S1"
    alignment.mkdir(parents=True)
    (alignment / "S1.summary.txt").write_text(
        "Sample_ID\tS1\n"
        "Total_Reads\t200000\n"
        "Total_Mapped_Reads\t120000\n"
        "Mapping_Rate\t60.00%\n"
        "Unique_Mapped_Reads\t30000\n"
        "Unique_Mapped_Rate\t15.00%\n"
        "Picard_Duplicate_Reads\t24000\n"
        "Picard_Duplication_Rate\t20.00%\n"
        "MT_Mapped_Reads\t60000\n"
        "MT_Ratio\t50.00%\n",
        encoding="utf-8",
    )

    result = ProjectExpertToolService().run_loop(
        project_root=tmp_path,
        project_id="P1",
        question="why is mitochondrial rate high",
        analysis_plan={"target_metrics": ["mt_rate_percent"]},
        project_context={"config": {"species": "hg38"}},
    )
    mt_card = next(card for card in result["evidence_cards"] if card["metric_id"] == "mt_rate_percent")

    assert result["round_count"] == 1
    assert result["round_count"] <= result["max_rounds"] == 3
    assert mt_card["numerator"] == 60000
    assert mt_card["denominator"] == 120000
    assert mt_card["processing_phase"] == "alignment_organelle_partition"
    assert mt_card["species"] == "hg38"


def test_claim_verifier_rejects_number_and_denominator_mismatch():
    card = EvidenceCardService.from_evidence(
        {
            "metric_key": "mt_rate_percent",
            "metric": "Mitochondrial alignment rate",
            "sample": "S1",
            "value": 50.0,
            "display_value": "50.00%",
            "numerator": 60000,
            "denominator": 120000,
            "source_file": "S1.mt_stat.txt",
            "source_field": "MT_rate(%)",
        },
        project_id="P1",
        species="hg38",
    )
    claim = ClaimService.build_claims(evidence_cards=[card])[0]
    claim["value"] = 55.0
    claim["denominator"] = 200000

    result = ProjectAnalysisVerifierService().verify(
        evidence_cards=[card],
        claims=[claim],
        project_context={"config": {"species": "hg38"}},
    )

    assert result["passed"] is False
    assert {item["rule"] for item in result["violations"]} >= {
        "numeric_value_mismatch",
        "denominator_mismatch",
    }


def test_project_memory_invalidates_fact_when_evidence_changes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(project_memory_repository, "_storage_root", tmp_path)
    service = ProjectMemoryService()

    def analysis(value: float, evidence_id: str, run_id: str) -> dict:
        card = {
            "evidence_id": evidence_id,
            "metric_id": "mapping_rate_percent",
            "sample": "S1",
            "value": value,
            "display_value": f"{value:.2f}%",
            "numerator": int(value * 1000),
            "denominator": 100000,
            "source_file": "S1.summary.txt",
            "source_field": "Mapping_Rate",
        }
        return {
            "run_id": run_id,
            "question": "mapping",
            "question_type": "alignment",
            "evidence_cards": [card],
            "validated_claims": [
                {
                    "claim_id": f"claim-{run_id}",
                    "claim_type": "observation",
                    "text": f"S1 mapping={value:.2f}%",
                    "support_level": "confirmed",
                    "evidence_ids": [evidence_id],
                    "metric_id": "mapping_rate_percent",
                    "sample": "S1",
                    "value": value,
                    "denominator": 100000,
                    "species": "hg38",
                }
            ],
            "evidence_files": ["S1.summary.txt"],
            "warnings": ["must not become a fact"],
        }

    service.update_memory("P1", analysis(50.0, "ev-old", "run-1"))
    memory = service.update_memory("P1", analysis(60.0, "ev-new", "run-2"))

    assert len(memory["active_facts"]) == 1
    assert memory["active_facts"][0]["value"] == 60.0
    assert len(memory["invalidated_facts"]) == 1
    assert memory["invalidated_facts"][0]["invalidation_reason"] == "superseded_by_new_evidence"
    assert "must not become a fact" not in "\n".join(memory["latest_findings"])


def test_expert_eval_dataset_and_offline_model_comparison():
    cases_path = (
        Path(__file__).resolve().parents[1]
        / "harness"
        / "expert_eval"
        / "cases.jsonl"
    )
    cases = load_cases(cases_path)
    assert len(cases) == 6

    answer = (
        "该值来自原始 reads 的接头检出，不等于 clean reads 接头残留。"
        "需要查看 clean FastQC 或 trim 后指标验证。"
    )
    first = evaluate_response(cases[0], answer)
    assert first["passed"] is True

    fixtures = {
        "model-a": {case["id"]: answer for case in cases},
        "model-b": {case["id"]: "" for case in cases},
    }
    report = run_comparison(
        cases=cases,
        models=[{"name": "model-a", "model": "a"}, {"name": "model-b", "model": "b"}],
        response_fixture=fixtures,
    )
    assert report["ranking"][0]["name"] == "model-a"


def test_evidence_consolidation_preserves_sources_and_conflicts_are_rejected():
    first = EvidenceCardService.from_evidence(
        {
            "metric_key": "mapping_rate_percent",
            "sample": "S1",
            "value": 60.0,
            "display_value": "60.00%",
            "source_file": "summary.txt",
            "source_field": "Mapping_Rate",
        },
        project_id="P1",
    )
    second = EvidenceCardService.from_evidence(
        {
            "metric_key": "mapping_rate_percent",
            "sample": "S1",
            "value": 60.0,
            "display_value": "60.00%",
            "source_file": "report.xls",
            "source_field": "Mapping",
        },
        project_id="P1",
    )
    consolidated = EvidenceCardService.consolidate_cards([first, second])

    assert len(consolidated) == 1
    assert len(consolidated[0]["source_records"]) == 2

    conflicting = EvidenceCardService.from_evidence(
        {
            "metric_key": "mapping_rate_percent",
            "sample": "S1",
            "value": 72.0,
            "display_value": "72.00%",
            "source_file": "other.txt",
            "source_field": "Mapping_Rate",
        },
        project_id="P1",
    )
    cards = EvidenceCardService.consolidate_cards([first, conflicting])
    claims = ClaimService.build_claims(evidence_cards=cards)
    verified = ProjectAnalysisVerifierService().verify(
        evidence_cards=cards,
        claims=claims,
    )

    assert verified["passed"] is False
    assert verified["contracts"]["cross_source_conflicts_resolved"] is False
    assert any(
        item["rule"] == "unresolved_evidence_conflict"
        for item in verified["violations"]
    )


def test_adapter_read_mates_are_distinct_measurements_and_rounding_is_compatible():
    r1 = EvidenceCardService.from_evidence(
        {
            "metric_key": "adapter_percent",
            "measurement_id": "cutadapt_r1_adapter_detected_percent",
            "measurement_definition": "cutadapt R1 adapter reads / raw R1 reads * 100",
            "sample": "S1",
            "value": 38.30,
            "display_value": "38.30%",
            "population_scope": "raw R1 reads",
            "counting_unit": "reads",
            "source_file": "S1.trim.log",
            "source_field": "Read 1 with adapter",
        },
        project_id="P1",
    )
    r2 = EvidenceCardService.from_evidence(
        {
            "metric_key": "adapter_percent",
            "measurement_id": "cutadapt_r2_adapter_detected_percent",
            "measurement_definition": "cutadapt R2 adapter reads / raw R2 reads * 100",
            "sample": "S1",
            "value": 38.20,
            "display_value": "38.20%",
            "population_scope": "raw R2 reads",
            "counting_unit": "reads",
            "source_file": "S1.trim.log",
            "source_field": "Read 2 with adapter",
        },
        project_id="P1",
    )
    rounded = EvidenceCardService.from_evidence(
        {
            "metric_key": "adapter_percent",
            "measurement_id": "readsqc_raw_adapter_detected_percent",
            "measurement_definition": "adapter reads / raw reads * 100",
            "sample": "S1",
            "value": 39.2,
            "display_value": "39.2%",
            "population_scope": "raw reads aggregated by the project ReadsQC table",
            "counting_unit": "reads",
            "source_file": "report.xls",
            "source_field": "Adapter",
        },
        project_id="P1",
    )
    precise = EvidenceCardService.from_evidence(
        {
            "metric_key": "adapter_percent",
            "measurement_id": "readsqc_raw_adapter_detected_percent",
            "measurement_definition": "adapter reads / raw reads * 100",
            "sample": "S1",
            "value": 39.19,
            "display_value": "39.19%",
            "population_scope": "raw reads aggregated by the project ReadsQC table",
            "counting_unit": "reads",
            "source_file": "summary.txt",
            "source_field": "Adapter",
        },
        project_id="P1",
    )

    cards = EvidenceCardService.consolidate_cards([r1, r2, rounded, precise])

    assert len(cards) == 3
    assert EvidenceCardService.detect_conflicts(cards) == []


def test_frip_fraction_recalculation_matches_percent_display():
    card = EvidenceCardService.from_evidence(
        {
            "metric_key": "frip_ratio",
            "measurement_id": "frip_reads_in_peaks_ratio",
            "sample": "S1",
            "value": 0.1482,
            "display_value": "14.82%",
            "value_scale": "fraction",
            "display_scale": "percent",
            "numerator": 1482,
            "numerator_value": 1482,
            "denominator": 10000,
            "denominator_value": 10000,
            "formula": "reads_in_peaks / mapped_reads",
            "source_file": "FRiP_score.xls",
            "source_field": "FRiP",
        },
        project_id="P1",
    )
    claim = ClaimService.build_claims(evidence_cards=[card])[0]
    result = ProjectAnalysisVerifierService().verify(
        evidence_cards=[card],
        claims=[claim],
    )

    assert result["passed"] is True


def test_q30_metric_label_is_not_treated_as_hallucinated_number():
    card = EvidenceCardService.from_evidence(
        {
            "metric_key": "q30_ratio",
            "metric": "Q30 base ratio",
            "sample": "S1",
            "value": 94.0,
            "display_value": "94.00%",
            "source_file": "fastp.json",
            "source_field": "summary.after_filtering.q30_rate",
        },
        project_id="P1",
    )
    claim = ClaimService.build_claims(evidence_cards=[card])[0]
    result = ProjectAnalysisVerifierService().verify(
        evidence_cards=[card],
        claims=[claim],
    )

    assert result["passed"] is True


def test_expert_parsers_read_fastp_fastqc_and_correlation(tmp_path: Path):
    (tmp_path / "S1.fastp.json").write_text(
        json.dumps(
            {
                "summary": {
                    "before_filtering": {
                        "total_reads": 1000,
                        "q20_rate": 0.98,
                        "q30_rate": 0.94,
                    },
                    "after_filtering": {
                        "total_reads": 900,
                        "q20_rate": 0.99,
                        "q30_rate": 0.96,
                    },
                },
                "adapter_cutting": {"adapter_trimmed_reads": 200},
            }
        ),
        encoding="utf-8",
    )
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    (clean_dir / "S1_fastqc_data.txt").write_text(
        ">>Adapter Content\tpass\n"
        "#Position\tIllumina Universal Adapter\tNextera Transposase Sequence\n"
        "1\t0.10\t0.20\n"
        "2\t0.30\t0.25\n"
        ">>END_MODULE\n",
        encoding="utf-8",
    )
    (tmp_path / "spearman_Corr_readCounts.tab").write_text(
        "Sample\tS1\tS2\n"
        "S1\t1\t0.82\n"
        "S2\t0.82\t1\n",
        encoding="utf-8",
    )

    service = ProjectExpertToolService()
    qc = service.run_qc_expert(project_root=tmp_path, project_id="P1")
    enrichment = service.run_enrichment_expert(project_root=tmp_path, project_id="P1")

    assert any(
        card["measurement_id"] == "fastp_adapter_trimmed_reads_percent"
        for card in qc["evidence_cards"]
    )
    assert any(
        card["measurement_id"] == "fastqc_adapter_content_max_percent"
        and card["processing_phase"] == "clean_reads_post_trim"
        for card in qc["evidence_cards"]
    )
    correlation = next(
        card
        for card in enrichment["evidence_cards"]
        if card["metric_id"] == "correlation"
    )
    assert correlation["sample"] == "S1 vs S2"
    assert correlation["value"] == 0.82


def test_enrichment_expert_parses_tss_fragment_spikein_peak_width_and_controls(tmp_path: Path):
    (tmp_path / "TSS_enrichment.tsv").write_text(
        "Sample\tTSS_enrichment\nS1\t8.5\n",
        encoding="utf-8",
    )
    (tmp_path / "fragment_size.tsv").write_text(
        "Sample\tMedian_fragment_size\tMean_fragment_size\nS1\t145\t172.5\n",
        encoding="utf-8",
    )
    (tmp_path / "spikein_align.xls").write_text(
        "Sample\tMapped reads\tUnique mapping rate(%)\tScaleFactor\n"
        "S1\t12345\t88.2\t0.734\n",
        encoding="utf-8",
    )
    (tmp_path / "peak_stat.xls").write_text(
        "Sample\tPeakCount\tMedian_peak_width\tMean_peak_width\n"
        "S1\t4567\t310\t425.5\n",
        encoding="utf-8",
    )

    result = ProjectExpertToolService().run_enrichment_expert(
        project_root=tmp_path,
        project_id="P1",
        project_context={
            "samplelist_file": "samplelist",
            "sample_roles": [
                {"sample": "S1", "role": "Experimental"},
                {"sample": "IgG", "role": "IgG/control"},
            ],
        },
    )
    cards = result["evidence_cards"]

    assert any(card["metric_id"] == "tss_enrichment" and card["value"] == 8.5 for card in cards)
    assert any(
        card["metric_id"] == "fragment_size"
        and card["measurement_id"] == "median_fragment_size_bp"
        and card["value"] == 145
        for card in cards
    )
    assert any(card["metric_id"] == "spikein_mapped_reads" and card["value"] == 12345 for card in cards)
    assert any(
        card["metric_id"] == "spikein_unique_mapping_rate_percent"
        and card["value"] == 88.2
        for card in cards
    )
    assert any(card["metric_id"] == "spikein_scaling_factor" and card["value"] == 0.734 for card in cards)
    assert any(
        card["metric_id"] == "peak_width"
        and card["measurement_id"] == "median_peak_width_bp"
        and card["value"] == 310
        for card in cards
    )
    control = next(card for card in cards if card["metric_id"] == "control_binding_status")
    assert "IgG" in control["display_value"]
    assert "binding unverified" in control["display_value"]


def test_production_project_root_must_be_inside_allowlist(tmp_path: Path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside" / "P1"
    allowed.mkdir()
    outside.mkdir(parents=True)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PROJECT_BASE_DIRS", str(allowed))

    with pytest.raises(PermissionError):
        project_reader.resolve_project_root("P1", str(outside))
    with pytest.raises(ValueError):
        project_reader.resolve_project_root("../P1", str(outside))


def test_expert_evaluator_rejects_untraceable_numbers():
    case = {
        "id": "numeric",
        "category": "traceability",
        "analysis_context": {
            "evidence": [{"metric": "mapping_rate_percent", "value": "50.0%"}],
            "threshold_verified": False,
        },
        "rubric": {},
    }

    result = evaluate_response(case, "观测值为 50.0%，外部经验阈值为 80%。")

    assert result["passed"] is False
    trace_check = next(
        check for check in result["checks"] if check["name"] == "numeric_traceability"
    )
    assert trace_check["detail"]["unsupported"] == [80.0]


def test_answer_replacement_protocol_is_removed():
    app_root = Path(__file__).resolve().parents[1]
    workspace_root = Path(__file__).resolve().parents[4]
    sources = [
        app_root / "multi_agent" / "project_progress.py",
        app_root / "services" / "agent_service.py",
        app_root / "services" / "business_agent" / "runtime_service.py",
        workspace_root / "multi_agent" / "front" / "agent_web_ui" / "src" / "App.vue",
    ]

    for source in sources:
        assert "project_answer_replace" not in source.read_text(encoding="utf-8")


def test_project_memory_updates_are_serialized(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(project_memory_repository, "_storage_root", tmp_path)
    service = ProjectMemoryService()

    def update(index: int) -> None:
        evidence_id = f"ev-{index}"
        card = {
            "evidence_id": evidence_id,
            "metric_id": "mapping_rate_percent",
            "measurement_id": "mapping_rate_percent",
            "sample": f"S{index}",
            "value": float(index),
            "source_file": f"S{index}.txt",
            "source_field": "Mapping_Rate",
        }
        service.update_memory(
            "P1",
            {
                "run_id": f"run-{index}",
                "question": "mapping",
                "evidence_cards": [card],
                "validated_claims": [
                    {
                        "claim_id": f"claim-{index}",
                        "claim_type": "observation",
                        "text": f"S{index} mapping={index}",
                        "support_level": "confirmed",
                        "evidence_ids": [evidence_id],
                        "metric_id": "mapping_rate_percent",
                        "measurement_id": "mapping_rate_percent",
                        "sample": f"S{index}",
                        "value": float(index),
                    }
                ],
            },
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(update, range(8)))

    memory = service.load_memory("P1")
    assert len(memory["active_facts"]) == 8


def test_runtime_configuration_supports_offline_dev_and_requires_production_auth(monkeypatch):
    offline = Settings(_env_file=None)
    assert offline.REQUIRE_AI_SERVICE is False

    with pytest.raises(ValueError, match="APP_API_KEY"):
        Settings(
            _env_file=None,
            APP_ENV="production",
            CORS_ALLOW_ORIGINS="https://analysis.example.com",
        )

    monkeypatch.setattr(settings, "APP_API_KEY", "test-secret")
    with pytest.raises(Exception) as exc_info:
        _require_api_key(None)
    assert getattr(exc_info.value, "status_code", None) == 401
    _require_api_key("test-secret")
