from pathlib import Path

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from multi_agent.backed.app.api.routers import chat
from multi_agent.backed.app.schemas.request import ChatCompatRequest, ChatMessageRequest, UserContext
from multi_agent.backed.app.services.agent_service import MultiAgentService
from multi_agent.backed.app.services.project_analysis_service import ProjectAnalysisService
from multi_agent.backed.app.services.project_context_builder_service import (
    ProjectContextBuilderService,
    project_context_builder_service,
)
from multi_agent.backed.app.services.project_cause_analysis_service import (
    ProjectCauseAnalysisService,
)
from multi_agent.backed.app.services.project_parse_cache import project_parse_cache
from multi_agent.backed.app.services.project_cuttag_diagnostic_service import (
    ProjectCuttagDiagnosticService,
)
from multi_agent.backed.app.services.project_analysis_workflow_service import (
    ProjectAnalysisWorkflowService,
)
from multi_agent.backed.app.services.project_locator_service import ProjectCandidate, ProjectLocatorService
from multi_agent.backed.app.services.business_agent.runtime_service import BusinessAgentRuntimeService
from multi_agent.backed.app.services.business_agent.answer_quality_service import (
    BusinessAnswerQualityService,
)
from multi_agent.backed.app.services.business_agent.response_service import business_response_service
from multi_agent.backed.app.services.business_agent.semantic_guard_service import BusinessSemanticGuardService
from multi_agent.backed.app.infrastructure.tools.local.project_reader import find_log_files


def test_locator_returns_confirmation_for_ambiguous_match(monkeypatch):
    service = ProjectLocatorService()
    candidates = [
        ProjectCandidate("proj_alpha", "D:/data/proj_alpha", ("sampleA",)),
        ProjectCandidate("proj_alpha_rep", "D:/data/proj_alpha_rep", ("sampleB",)),
    ]
    monkeypatch.setattr(service, "list_projects", lambda limit=200: candidates)

    result = service.identify_project(
        question="帮我看 alpha 项目",
        project_id=None,
        user_id="u1",
        session_id="s1",
    )

    assert result["needs_confirmation"] is True
    assert len(result["candidates"]) >= 2
    assert result["confidence"] < 0.99


def test_locator_accepts_exact_project_id(monkeypatch):
    service = ProjectLocatorService()
    candidates = [
        ProjectCandidate("proj_alpha", "D:/data/proj_alpha", ("sampleA",)),
    ]
    monkeypatch.setattr(service, "list_projects", lambda limit=200: candidates)

    result = service.identify_project(
        question="随便问",
        project_id="proj_alpha",
        user_id="u1",
        session_id="s1",
    )

    assert result["needs_confirmation"] is False
    assert result["matched_by"] == "project_id"


def test_analysis_normalizes_percentages_and_skips_correlation_diagonal(tmp_path: Path):
    project_root = tmp_path / "proj1"
    project_root.mkdir()
    (project_root / "ReadsQC.xls").write_text(
        "Sample\tAdapter\tQ20\tQ30\tRaw Reads\tClean Reads\n"
        "S1\t55\t98\t94\t1000\t900\n",
        encoding="utf-8",
    )
    (project_root / "spearman_Corr_readCounts.tab").write_text(
        "Sample\tS1\tS2\n"
        "S1\t1\t0.82\n"
        "S2\t0.82\t1\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisService.analyze(
        project_id="proj1",
        question="帮我看质控和相关性",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    qc_metrics = result["parsed_metrics"]["qc"][0]
    assert qc_metrics["adapter_percent"] == 55.0
    assert qc_metrics["q30_ratio"] == 0.94
    corr = result["parsed_metrics"]["correlation"]
    assert corr["max_pair"] == ("S1", "S2", 0.82)
    assert corr["min_pair"] == ("S1", "S2", 0.82)


def test_target_metric_table_precedes_external_evidence_hints(tmp_path: Path):
    project_root = tmp_path / "proj_target_first"
    project_root.mkdir()
    (project_root / "AlignmentQC.xls").write_text(
        "Sample_ID\tMapping_Rate\tUnique_Mapped_Rate\tPicard_Duplication_Rate\tMT_Ratio\n"
        "T1\t57.54%\t12.6%\t33.66%\t72.61%\n",
        encoding="utf-8",
    )
    (project_root / "historical_note.txt").write_text("history", encoding="utf-8")
    (project_root / "generic_reference.txt").write_text("reference", encoding="utf-8")

    selected = ProjectAnalysisService._select_evidence_files(
        project_root,
        ["alignment", "diagnostic"],
        1,
        planning_hints={
            "prioritized_evidence_hints": ["historical_note", "generic_reference"],
            "analysis_plan": {"target_metrics": ["mt_rate_percent"]},
        },
    )

    assert selected[0].name == "AlignmentQC.xls"


def test_unverified_thresholds_do_not_create_anomaly_findings(tmp_path: Path):
    project_root = tmp_path / "proj_unverified_thresholds"
    project_root.mkdir()
    (project_root / "ReadsQC.xls").write_text(
        "Sample\tAdapter\tQ20\tQ30\tTotal Raw Reads\tTotal Clean Reads\n"
        "S1\t800(80%)\t98\t90\t1000\t990(99%)\n",
        encoding="utf-8",
    )
    (project_root / "AlignmentQC.xls").write_text(
        "Sample_ID\tMapping_Rate\tUnique_Mapped_Rate\tPicard_Duplication_Rate\tMT_Ratio\n"
        "S1\t40%\t10%\t70%\t60%\n",
        encoding="utf-8",
    )
    (project_root / "FRiP.xls").write_text(
        "Sample\tFRiP\nS1\t0.01\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisService.analyze(
        project_id="proj_unverified_thresholds",
        question="全面诊断这些指标为什么异常",
        project_root=str(project_root),
        max_evidence_files=8,
    )

    assert result["evidence_chain"]
    assert all(item["severity"] == "unverified_threshold" for item in result["evidence_chain"])
    combined_findings = "\n".join(result["automatic_findings"])
    assert "接头污染偏高" not in combined_findings
    assert "比对率偏低" not in combined_findings
    assert "FRiP 偏低" not in combined_findings


def test_threshold_requires_structured_project_rule():
    mentioned_only = ProjectAnalysisService._build_rule_entry(
        metric_key="mapping_rate_percent",
        category="AlignmentQC",
        sample="S1",
        value=50.0,
        source_file="AlignmentQC.xls",
        rule_source={
            "threshold_source": "project_readme",
            "formula_source": "not_found_in_project_code",
        },
    )
    assert mentioned_only["severity"] == "unverified_threshold"
    assert mentioned_only["threshold_needs_project_validation"] is True

    structured = ProjectAnalysisService._build_rule_entry(
        metric_key="mapping_rate_percent",
        category="AlignmentQC",
        sample="S1",
        value=50.0,
        source_file="AlignmentQC.xls",
        rule_source={
            "threshold_source": "project_sop",
            "formula_source": "not_found_in_project_code",
            "threshold_rule": {"warning": {"op": "<", "value": 75.0}},
        },
    )
    assert structured["severity"] == "warning"
    assert structured["rule"] == "Mapping rate < 75%"


def test_partial_mt_count_script_is_not_treated_as_rate_formula():
    # 2026-07-03 修复：`_build_metric_rule_sources` 早已从 ProjectAnalysisService
    # 搬到 project_context_builder_service.build_metric_rule_sources（职责边界见
    # CLAUDE.md 服务表："project_context_builder_service | samplelist/config/
    # workflow/HTML 上下文解析"），这条用例是搬迁前遗留的孤儿测试，一直没更新调用
    # 路径——本次修复只是把调用对象改到当前实现，断言逻辑本身不变。
    sources = project_context_builder_service.build_metric_rule_sources(
        metric_guides=[],
        workflow_summary={
            "detected_parameters": {},
            "code_formula_sources": {
                "mt_rate_percent": {
                    "formula": "mt=$(samtools view -c sample.bam chrM)",
                    "source_file": "align.smk",
                    "source_line": "10",
                }
            },
        },
        config={"species": "hg38"},
    )

    mt_source = sources["mt_rate_percent"]
    assert mt_source["formula"] == ""
    assert mt_source["formula_source"] == "project_code_partial"
    assert mt_source["formula_candidate"]
    assert mt_source["needs_verification"] is True


def test_hg38_organelle_metric_is_mitochondrial_only(tmp_path: Path):
    project_root = tmp_path / "proj_hg38"
    project_root.mkdir()
    (project_root / "config.yaml").write_text("species: hg38\n", encoding="utf-8")
    (project_root / "AlignmentQC.xls").write_text(
        "Sample_ID\tMapping_Rate\tUnique_Mapped_Rate\tPicard_Duplication_Rate\tMT_Ratio\n"
        "S1\t90%\t80%\t10%\t12%\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisService.analyze(
        project_id="proj_hg38",
        question="解释线粒体比例",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    mt_evidence = next(item for item in result["evidence_chain"] if item["metric_key"] == "mt_rate_percent")
    assert mt_evidence["metric"] == "Mitochondrial alignment rate"
    assert "线粒体染色体" in mt_evidence["definition"]
    assert "叶绿体" not in mt_evidence["definition"]
    alignment = result["parsed_metrics"]["alignment"][0]
    assert alignment["organelle_metric_label"] == "Mitochondrial(%)"
    assert "叶绿体" not in result["project_context"]["metric_glossary"]["AlignmentQC.MT_Ratio"]


def test_semantic_guard_rejects_adapter_stage_mismatch_without_llm():
    analysis_result = {
        "question": "为什么说是接头残留",
        "question_type": "qc",
        "project_context": {"config": {"species": "hg38"}},
        "evidence_chain": [
            {
                "metric_key": "adapter_percent",
                "denominator": "raw reads",
                "display_value": "39.19%",
            }
        ],
    }

    result = asyncio.run(
        BusinessSemanticGuardService.validate(
            answer="这说明 clean reads 中存在明显接头残留。",
            analysis_result=analysis_result,
            question_route={"route": "project_qa"},
        )
    )

    assert result["passed"] is False
    assert result["check_mode"] == "deterministic_scientific_rules"
    assert result["violations"][0]["rule"] == "adapter_processing_stage_mismatch"


def test_semantic_guard_rejects_plant_interpretation_for_hg38_without_llm():
    analysis_result = {
        "question": "为什么细胞器比例高",
        "question_type": "alignment",
        "project_context": {"config": {"species": "hg38"}},
        "evidence_chain": [],
    }

    result = asyncio.run(
        BusinessSemanticGuardService.validate(
            answer="该值说明叶绿体 reads 占比较高。",
            analysis_result=analysis_result,
            question_route={"route": "project_qa"},
        )
    )

    assert result["passed"] is False
    assert result["violations"][0]["rule"] == "species_organelle_mismatch"


def test_semantic_guard_rejects_unverified_high_low_judgement_without_llm():
    analysis_result = {
        "question": "Mapping 为什么低",
        "question_type": "alignment",
        "project_context": {"config": {"species": "hg38"}},
        "evidence_chain": [
            {
                "metric_key": "mapping_rate_percent",
                "display_value": "57.54%",
                "severity": "unverified_threshold",
                "threshold_needs_project_validation": True,
            }
        ],
    }

    result = asyncio.run(
        BusinessSemanticGuardService.validate(
            answer="S1 的 Mapping 比对率偏低，需要排查。",
            analysis_result=analysis_result,
            question_route={"route": "project_qa"},
        )
    )

    assert result["passed"] is False
    assert result["violations"][0]["rule"] == "no_unverified_threshold_judgement"


def test_semantic_guard_rejects_false_missing_metric_claim_without_llm():
    analysis_result = {
        "question": "为什么线粒体比例高",
        "question_type": "alignment",
        "project_context": {"config": {"species": "hg38"}},
        "evidence_chain": [
            {
                "metric_key": "mt_rate_percent",
                "value": 72.61,
                "display_value": "72.61%",
                "severity": "unverified_threshold",
                "threshold_needs_project_validation": True,
            }
        ],
    }

    result = asyncio.run(
        BusinessSemanticGuardService.validate(
            answer="目前未能成功读取到具体的线粒体 reads 比例指标表，因此无法给出具体数值。",
            analysis_result=analysis_result,
            question_route={"route": "project_qa"},
        )
    )

    assert result["passed"] is False
    assert any(item["rule"] == "evidence_presence_mismatch" for item in result["violations"])


def test_scientific_repair_uses_present_metric_values_and_passes_guard():
    analysis_result = {
        "question": "为什么线粒体 reads 比例高",
        "question_type": "alignment",
        "analysis_plan": {"target_metrics": ["mt_rate_percent"]},
        "project_context": {"config": {"species": "hg38"}},
        "evidence_chain": [
            {
                "metric_key": "mt_rate_percent",
                "metric": "Mitochondrial alignment rate",
                "sample": "T1",
                "value": 72.61,
                "display_value": "72.61%",
                "denominator": "mapped reads",
                "source_file": "result/AlignmentQC.xls",
                "source_field": "MT_Ratio",
                "severity": "unverified_threshold",
                "threshold_needs_project_validation": True,
            },
            {
                "metric_key": "mt_rate_percent",
                "metric": "Mitochondrial alignment rate",
                "sample": "T2",
                "value": 66.87,
                "display_value": "66.87%",
                "denominator": "mapped reads",
                "source_file": "result/AlignmentQC.xls",
                "source_field": "MT_Ratio",
                "severity": "unverified_threshold",
                "threshold_needs_project_validation": True,
            },
        ],
    }
    bad_answer = "未能成功读取线粒体 reads 比例，因此无法给出具体数值；该指标偏高。"
    violations = BusinessSemanticGuardService._deterministic_violations(
        answer=bad_answer,
        analysis_result=analysis_result,
    )

    repaired = business_response_service.build_scientific_repair_answer(
        analysis_result=analysis_result,
        violations=violations,
    )
    repaired_violations = BusinessSemanticGuardService._deterministic_violations(
        answer=repaired,
        analysis_result=analysis_result,
    )

    assert "72.61%" in repaired
    assert "66.87%" in repaired
    assert "叶绿体" not in repaired
    assert repaired_violations == []


def test_semantic_guard_requires_all_narrow_target_metric_values():
    analysis_result = {
        "question": "为什么线粒体 reads 比例高",
        "analysis_plan": {"target_metrics": ["mt_rate_percent"]},
        "evidence_chain": [
            {
                "metric_key": "mt_rate_percent",
                "sample": "T1",
                "value": 72.61,
                "display_value": "72.61%",
            },
            {
                "metric_key": "mt_rate_percent",
                "sample": "T2",
                "value": 66.87,
                "display_value": "66.87%",
            },
        ],
    }

    violations = BusinessSemanticGuardService._deterministic_violations(
        answer="线粒体 reads 比例需要结合 mapping 和过滤口径解释。",
        analysis_result=analysis_result,
    )

    assert any(item["rule"] == "target_metric_value_omission" for item in violations)


def test_answer_cleaner_repairs_omitted_target_values_before_guard():
    analysis_result = {
        "question": "为什么线粒体 reads 比例高",
        "analysis_plan": {"target_metrics": ["chrmt_pt_rate_percent"]},
        "project_context": {"config": {"species": "hg38"}},
        "evidence_chain": [
            {
                "metric_key": "mt_rate_percent",
                "sample": "T1",
                "value": 72.61,
                "display_value": "72.61%",
                "source_file": "AlignmentQC.xls",
                "source_field": "chrMT/Pt(%)",
            },
            {
                "metric_key": "mt_rate_percent",
                "sample": "T2",
                "value": 66.87,
                "display_value": "66.87%",
                "source_file": "AlignmentQC.xls",
                "source_field": "chrMT/Pt(%)",
            },
        ],
    }

    cleaned = business_response_service.clean_final_answer(
        "当前没有读到结构化表。hg38 项目不是叶绿体/质体 reads。",
        analysis_result=analysis_result,
    )

    assert "72.61%" in cleaned
    assert "66.87%" in cleaned
    assert "叶绿体" not in cleaned
    assert "质体" not in cleaned


def test_known_species_violation_can_be_repaired_without_model():
    repaired = BusinessSemanticGuardService.repair_known_violations(
        answer="hg38 样本的线粒体/叶绿体 reads 比例为 60%。",
        analysis_result={"project_context": {"config": {"species": "hg38"}}},
        violations=[{"rule": "species_organelle_mismatch"}],
    )

    assert "叶绿体" not in repaired
    assert "线粒体 reads" in repaired


def test_semantic_guard_allows_explicit_adapter_boundary_statement():
    analysis_result = {
        "question": "Adapter 是什么",
        "question_type": "qc",
        "project_context": {"config": {"species": "hg38"}},
        "evidence_chain": [{"metric_key": "adapter_percent", "denominator": "raw reads"}],
    }

    result = asyncio.run(
        BusinessSemanticGuardService.validate(
            answer="该值来自 raw reads，不等于 clean reads 中的接头残留。",
            analysis_result=analysis_result,
            question_route={"route": "project_qa"},
        )
    )

    assert result["passed"] is True


def test_answer_cleaner_keeps_generated_next_actions():
    cleaned = business_response_service.clean_final_answer(
        "当前证据只支持描述观测值。",
        analysis_result={
            "question_type": "qc",
            "warnings": ["项目阈值未确认"],
            "next_actions": ["查看 clean FastQC 的 Adapter Content。"],
        },
    )

    assert "## 下一步建议" in cleaned
    assert "查看 clean FastQC" in cleaned


def test_narrow_metric_context_excludes_unrelated_history_and_evidence():
    context = business_response_service.build_analysis_context(
        analysis_result={
            "project_id": "p1",
            "question": "为什么线粒体比例高",
            "question_type": "alignment",
            "confidence": 0.8,
            "analysis_plan": {"target_metrics": ["mt_rate_percent"]},
            "project_context": {
                "config": {"species": "hg38", "organelle_chroms": "chrM"},
                "sample_roles": [{"sample": "S1", "role": "Experimental"}],
                "metric_glossary": {
                    "AlignmentQC.MT_Ratio": "比对到线粒体染色体的 reads 比例。",
                    "ReadsQC.Adapter": "raw reads adapter metric",
                },
            },
            "evidence_chain": [
                {
                    "category": "AlignmentQC",
                    "metric_key": "mt_rate_percent",
                    "metric": "Mitochondrial alignment rate",
                    "sample": "S1",
                    "display_value": "72.61%",
                },
                {
                    "category": "ReadsQC",
                    "metric_key": "adapter_percent",
                    "metric": "Adapter",
                    "sample": "S1",
                    "display_value": "39.19%",
                },
            ],
            "parsed_metrics": {},
        },
        experience_summary={
            "has_experience": True,
            "latest_findings": ["旧项目叶绿体结论"],
            "global_similar_cases": [{"project_id": "old", "automatic_findings": ["旧案例"]}],
        },
    )

    assert "窄指标证据模式" in context
    assert "Mitochondrial alignment rate" in context
    assert "Adapter: sample=" not in context
    assert "旧项目叶绿体结论" not in context
    assert "旧案例" not in context


def test_narrow_alignment_question_skips_raw_workflow_context():
    assert ProjectAnalysisService._should_read_internal_workflow_context(
        "为什么线粒体比例高",
        ["alignment", "diagnostic"],
    ) is False
    assert ProjectAnalysisService._should_read_internal_workflow_context(
        "检查 workflow 参数和脚本公式",
        ["alignment"],
    ) is True


def test_auto_report_summary_is_lazy_by_default(monkeypatch):
    # 2026-07-03 修复：`AUTO_REPORT_SUMMARY_ENABLED` 现在默认是 True（见 runtime_service.py
    # 里 2026-07-02 的修复记录，AI_REPORT_SUMMARY_VERSION 升到了 project-review-v4），
    # 这是一个独立于本轮 Stage A0-E / Stage B-补工作的既有产品行为变化——不是这条测试
    # 名字所说的"默认关闭"，而是"已经生成过匹配当前版本的摘要后不重复生成"意义上的懒惰。
    # 这里改为模拟"已存在匹配当前版本的 ready 缓存"这个真实的懒惰触发条件，而不是继续
    # 断言一个已经不成立的全局默认关闭前提。
    monkeypatch.setattr(
        "multi_agent.backed.app.repositories.project_report_cache_repository.project_report_cache_repository.load",
        lambda project_id, project_root: {
            "status": "ready",
            "generation_version": BusinessAgentRuntimeService.AI_REPORT_SUMMARY_VERSION,
        },
    )
    service = BusinessAgentRuntimeService()
    assert service._should_start_auto_report_summary("u1", "s1", "p1", "D:/p1") is False


def test_analysis_collects_warning_when_table_parse_fails(tmp_path: Path):
    project_root = tmp_path / "proj2"
    project_root.mkdir()
    (project_root / "FRiP.xls").write_bytes(b"\x00\x01\x02\x03")

    result = ProjectAnalysisService.analyze(
        project_id="proj2",
        question="帮我看 frip",
        project_root=str(project_root),
        max_evidence_files=2,
    )

    assert result["warnings"]
    assert any(item["status"] == "error" for item in result["evidence_status"])


def test_analysis_emits_trace_and_text_summaries(tmp_path: Path):
    project_root = tmp_path / "proj3"
    project_root.mkdir()
    (project_root / "diff_summary.txt").write_text(
        "up regulated genes detected\n"
        "down regulated genes detected\n",
        encoding="utf-8",
    )
    (project_root / "motif_hits.txt").write_text(
        "motif enrichment result\n"
        "P-value=1e-6\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisService.analyze(
        project_id="proj3",
        question="帮我看差异和 motif",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    assert result["run_id"].startswith("projrun_")
    assert result["trace"]["duration_ms"] >= 0
    assert "diff" in result["parsed_metrics"]
    assert "motif" in result["parsed_metrics"]


def test_workflow_returns_workflow_trace_and_memory(tmp_path: Path):
    project_root = tmp_path / "proj4"
    project_root.mkdir()
    (project_root / "FRiP.xls").write_text(
        "Sample\tFRiP\n"
        "S1\t0.18\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisWorkflowService.run_analysis(
        question="proj4 frip",
        project_id="proj4",
        user_id="u1",
        session_id="s1",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    assert result["success"] is True
    assert result["workflow_trace"]["workflow_run_id"].startswith("projwf_")
    assert result["data"]["run_id"].startswith("projrun_")
    assert "project_memory" in result
    assert "task_plan" in result
    assert "workspace" in result
    assert Path(result["workflow_trace"]["task_plan_path"]).exists()
    assert Path(result["workflow_trace"]["workspace_root"]).exists()
    assert Path(result["workflow_trace"]["progress_path"]).exists()
    assert "experience_inputs" in result["task_plan"]


def test_analysis_parses_meme_text_and_diff_readme(tmp_path: Path):
    project_root = tmp_path / "proj5"
    project_root.mkdir()
    (project_root / "sample_meme.txt").write_text(
        "MOTIF TGAGTCAY MEME-1\twidth =   8  sites = 2443  llr = 18767  E-value = 3.9e-241\n",
        encoding="utf-8",
    )
    (project_root / "7.4.1DiffPeak.readme.txt").write_text(
        "change: use |Mval| > 0.57 and p < 0.05 to select up and down peaks.\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisService.analyze(
        project_id="proj5",
        question="diff motif",
        project_root=str(project_root),
        max_evidence_files=6,
    )

    motif_items = result["parsed_metrics"]["motif"]
    diff_items = result["parsed_metrics"]["diff"]
    assert motif_items[0]["motif_source"] == "meme_txt"
    assert motif_items[0]["top_motifs"][0]["motif_name"] == "TGAGTCAY"
    assert result["parsed_metrics"]["motif_summary"]["samples"][0]["sample"] == "sample"
    assert diff_items[0]["diff_source"] == "readme"
    assert diff_items[0]["mentions_up_down_threshold"] is True


def test_analysis_prefers_diff_result_tables_when_present(tmp_path: Path):
    project_root = tmp_path / "proj6"
    project_root.mkdir()
    (project_root / "sample_final_anno.xls").write_text(
        "SYMBOL\tchange\tannotation\tdistanceToTSS\n"
        "GENE1\tup\tPromoter-TSS\t-120\n"
        "GENE2\tdown\tIntron\t2300\n"
        "GENE3\tnot\tIntergenic\t10000\n",
        encoding="utf-8",
    )
    (project_root / "GO_up.xls").write_text(
        "ONTOLOGY\tDescription\tGeneRatio\tp.adjust\n"
        "BP\tcell differentiation\t3/40\t0.001\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisService.analyze(
        project_id="proj6",
        question="diff analysis",
        project_root=str(project_root),
        max_evidence_files=6,
    )

    diff_items = result["parsed_metrics"]["diff"]
    annotation_item = next(item for item in diff_items if item["kind"] == "diff_annotation")
    go_item = next(item for item in diff_items if item["kind"] == "diff_go")
    assert annotation_item["change_counts"]["up"] == 1
    assert annotation_item["change_counts"]["down"] == 1
    assert go_item["top_terms"][0]["description"] == "cell differentiation"


def test_workflow_emits_file_progress_detail(tmp_path: Path):
    project_root = tmp_path / "proj7"
    project_root.mkdir()
    (project_root / "FRiP.xls").write_text(
        "Sample\tFRiP\n"
        "S1\t0.18\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisWorkflowService.run_analysis(
        question="proj7 frip",
        project_id="proj7",
        user_id="u1",
        session_id="s1",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    assert result["success"] is True
    assert result["data"]["evidence_status"]


def test_experience_hints_affect_planning_and_analysis(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "proj_exp"
    project_root.mkdir()
    (project_root / "FRiP.xls").write_text(
        "Sample\tFRiP\n"
        "S1\t0.18\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "multi_agent.backed.app.services.project_memory_service.project_memory_service.load_memory",
        lambda project_id: {
            "project_id": project_id,
            "last_report": "# old report",
            "latest_findings": ["FRiP 偏低"],
            "latest_warnings": ["duplicate warning"],
            "recent_questions": ["frip question"],
            "evidence_history": [
                {
                    "question_type": "frip",
                    "evidence_files": ["FRiP.xls", "ReadsQC.xls"],
                    "automatic_findings": ["motif signal found"],
                    "warnings": ["mapping warning"],
                }
            ],
            "updated_at": "2026-01-01T00:00:00",
        },
    )

    async def fake_retrieve(question: str):
        return {"question": question, "documents": []}

    async def fake_stream(question: str, analysis_result: dict, retrieval_payload: dict, experience_summary: dict):
        yield "experience answer"

    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.knowledge_service.retrieve_knowledge",
        fake_retrieve,
    )
    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.business_response_service.stream_fused_answer",
        fake_stream,
    )

    result = ProjectAnalysisWorkflowService.run_analysis(
        question="proj_exp frip issue",
        project_id="proj_exp",
        user_id="u1",
        session_id="s1",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    hints = result["task_plan"]["experience_inputs"]
    assert hints["has_experience"] is True
    assert "FRiP.xls" in hints["prioritized_evidence_hints"]
    assert "frip" in hints["prioritized_metrics"]
    assert "mapping" in hints["experience_rules"] or "duplicate" in hints["experience_rules"]
    assert hints["structured_experience_rules"]
    assert {"rule_key", "source", "frequency", "priority_type", "matched_text", "confidence", "activation_conditions"} <= set(hints["structured_experience_rules"][0].keys())
    assert hints["active_rule_count"] >= 1
    assert hints["evidence_scope_adjustment"]["expand_by"] >= 1
    assert hints["evidence_scope_adjustment"]["effective_max_evidence_files"] >= 4
    assert hints["rule_library_size"] >= 1
    assert (Path(result["workflow_trace"]["workspace_root"]) / "experience_rules.json").exists()
    assert result["data"]["planning_hints"]["prioritized_evidence_hints"]
    assert result["data"]["metric_priority"][0] == "frip_ratio"
    assert "优先关注指标顺序" in result["data"]["read_plan"][1]


def test_workflow_returns_fused_answer_payload(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "proj8"
    project_root.mkdir()
    (project_root / "FRiP.xls").write_text(
        "Sample\tFRiP\n"
        "S1\t0.18\n",
        encoding="utf-8",
    )

    async def fake_retrieve(question: str):
        return {
            "question": question,
            "documents": [
                {
                    "title": "FRiP知识",
                    "source": "kb://frip",
                    "content": "FRiP偏低通常提示峰内reads占比较低，需要结合peak质量和背景噪音判断。",
                    "chunk_id": "doc-1",
                }
            ],
        }

    model_answer = "## 直接结论\n- S1 的 FRiP 观测值为 18.00%，分母口径为 usable mapped reads/fragments evaluated against the peak set。\n\n## 项目证据\n- FRiP.xls::FRiP\n\n## 证据边界\n- 项目文件中未确认该指标阈值/标准，本轮只报告观测值、证据来源和排查方向，不据此直接判定高低或异常。\n\n## 下一步操作\n- 需要结合 peak 质量继续排查。"

    async def fake_stream(question: str, analysis_result: dict, retrieval_payload: dict, experience_summary: dict):
        assert retrieval_payload["documents"][0]["title"] == "FRiP知识"
        yield "## 直接结论\n- S1 的 FRiP 观测值为 18.00%，分母口径为 usable mapped reads/fragments evaluated against the peak set。\n"
        yield "\n## 项目证据\n- FRiP.xls::FRiP\n\n## 证据边界\n- 项目文件中未确认该指标阈值/标准，本轮只报告观测值、证据来源和排查方向，不据此直接判定高低或异常。\n\n## 下一步操作\n- 需要结合 peak 质量继续排查。"

    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.business_response_service.stream_fused_answer",
        fake_stream,
    )
    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.knowledge_augmentation_service.retrieve",
        fake_retrieve,
    )

    result = ProjectAnalysisWorkflowService.run_analysis(
        question="根据知识库分析 proj8 frip 有什么问题",
        project_id="proj8",
        user_id="u1",
        session_id="s1",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    assert result["success"] is True
    assert result["result_payload"]["output_mode"] == "qa"
    assert "FRiP" in result["result_payload"]["answer"]
    assert "未确认" in result["result_payload"]["answer"]
    assert result["result_payload"]["answer"] == model_answer
    assert result["result_payload"]["used_knowledge"] is True
    assert result["result_payload"]["answer_quality"]["enforcement_mode"] == "observe_only"
    assert result["result_payload"]["answer_quality"]["repair_applied"] is False
    assert "answer_quality" in result["workflow_trace"]


def test_workflow_prefers_explicit_project_request_over_session_memory(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "proj_explicit"
    project_root.mkdir()
    (project_root / "FRiP.xls").write_text(
        "Sample\tFRiP\n"
        "S1\t0.18\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.project_session_state_service.load_state",
        lambda user_id, session_id: {
            "current_project_id": "WRONG_PROJECT",
            "current_project_root": "D:/wrong/project",
            "recent_questions": [],
            "last_identified_at": None,
        },
    )

    async def fake_retrieve(question: str):
        return {"question": question, "documents": []}

    async def fake_stream(question: str, analysis_result: dict, retrieval_payload: dict, experience_summary: dict):
        yield "explicit project answer"

    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.business_response_service.stream_fused_answer",
        fake_stream,
    )
    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.knowledge_augmentation_service.retrieve",
        fake_retrieve,
    )

    result = ProjectAnalysisWorkflowService.run_analysis(
        question="proj explicit frip issue",
        project_id="proj_explicit",
        user_id="u1",
        session_id="s1",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    assert result["identified_project"]["matched_by"] == "request"
    assert result["identified_project"]["project_id"] == "proj_explicit"
    assert result["data"]["project_id"] == "proj_explicit"


def test_workflow_returns_report_mode_for_report_request(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "proj9"
    project_root.mkdir()
    (project_root / "ReadsQC.xls").write_text(
        "Sample\tAdapter\tQ20\tQ30\tRaw Reads\tClean Reads\n"
        "S1\t5\t98\t97\t1000\t950\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "multi_agent.backed.app.services.project_memory_service.project_memory_service.load_memory",
        lambda project_id: {
            "project_id": project_id,
            "last_report": "# old report",
            "latest_findings": ["FRiP low"],
            "latest_warnings": [],
            "recent_questions": ["frip question"],
            "evidence_history": [{"question_type": "frip", "evidence_files": ["FRiP.xls"]}],
            "updated_at": "2026-01-01T00:00:00",
        },
    )

    async def fake_retrieve(question: str):
        return {"question": question, "documents": []}

    async def fake_stream(question: str, analysis_result: dict, retrieval_payload: dict, experience_summary: dict):
        yield "这是问答模式下的融合回答"

    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.business_response_service.stream_fused_answer",
        fake_stream,
    )
    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.knowledge_augmentation_service.retrieve",
        fake_retrieve,
    )

    result = ProjectAnalysisWorkflowService.run_analysis(
        question="generate proj9 analysis report",
        project_id="proj9",
        user_id="u1",
        session_id="s1",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    assert result["success"] is True
    assert result["result_payload"]["output_mode"] == "report"
    assert result["result_payload"]["report"].startswith("# proj9")
    assert "优先排查指标顺序" in result["result_payload"]["report"]
    assert "source=" in result["result_payload"]["report"]


def test_workflow_falls_back_when_knowledge_or_llm_unavailable(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "proj10"
    project_root.mkdir()
    (project_root / "FRiP.xls").write_text(
        "Sample\tFRiP\n"
        "S1\t0.18\n",
        encoding="utf-8",
    )

    async def failing_retrieve(question: str):
        raise RuntimeError("knowledge offline")

    async def failing_stream(question: str, analysis_result: dict, retrieval_payload: dict, experience_summary: dict):
        raise RuntimeError("llm offline")
        yield

    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.business_response_service.stream_fused_answer",
        failing_stream,
    )
    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.knowledge_augmentation_service.retrieve",
        failing_retrieve,
    )

    result = ProjectAnalysisWorkflowService.run_analysis(
        question="proj10 frip 是否异常",
        project_id="proj10",
        user_id="u1",
        session_id="s1",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    assert result["success"] is True
    assert result["result_payload"]["used_knowledge"] is False
    assert "FRiP" in result["result_payload"]["answer"]
    assert "项目文件中未确认" in result["result_payload"]["answer"]


def test_process_task_sync_includes_project_analysis_payload(monkeypatch):
    request = ChatMessageRequest(
        query="proj1 frip issue",
        context=UserContext(user_id="u1", session_id="s1"),
        mode="agent",
        project_id="proj1",
        project_root="D:/proj1",
        max_evidence_files=5,
    )

    async def fake_business_analysis(*args, **kwargs):
        return {
            "answer": "project final answer",
            "analysis_result": {
                "identified_project": {"project_id": "proj1"},
                "workflow_trace": {"workflow_run_id": "wf1"},
                "data": {"project_id": "proj1"},
                "project_memory": {"last_run_id": "r1"},
                "result_payload": {
                    "answer": "fused project answer",
                    "report": "# proj1 report",
                    "knowledge_retrieval": {"documents": []},
                    "used_knowledge": False,
                    "answer_quality": {"passed": True, "score": 88},
                },
            },
        }

    monkeypatch.setattr(
        "multi_agent.backed.app.services.agent_service.session_service.prepare_history",
        lambda user_id, session_id, user_query: [{"role": "user", "content": user_query}],
    )
    monkeypatch.setattr(
        "multi_agent.backed.app.services.agent_service.session_service.save_history",
        lambda user_id, session_id, history: None,
    )
    # 2026-07-03 修复：真实调用现在会多传一个 `_preloaded_state=project_state`
    # 关键字参数（见 agent_service.py 里 set_round_project_request_context 的调用
    # 处，注释写明是为了避免内部重复做同步文件读取）——这条 lambda 是搬迁前的旧签名，
    # 补上这个参数（带默认值）即可，不影响测试本身要验证的行为。
    monkeypatch.setattr(
        "multi_agent.backed.app.services.agent_service.set_round_project_request_context",
        lambda user_id, session_id, project_id, project_root=None, max_evidence_files=None, _preloaded_state=None: None,
    )
    monkeypatch.setattr(
        "multi_agent.backed.app.services.agent_service.execute_project_business_analysis",
        fake_business_analysis,
    )

    result = asyncio.run(MultiAgentService.process_task_sync(request))

    assert result["answer"] == "project final answer"
    assert result["project_analysis"]["identified_project"]["project_id"] == "proj1"
    assert result["project_analysis"]["answer"] == "fused project answer"
    assert result["project_analysis"]["report"] == "# proj1 report"
    assert result["project_analysis"]["answer_quality"]["score"] == 88


def test_chat_returns_project_analysis_fields(monkeypatch):
    async def fake_process_task_sync(request):
        assert request.project_id == "proj1"
        assert request.project_root == "D:/proj1"
        assert request.max_evidence_files == 5
        return {
            "answer": "chat answer",
            "sources": ["src1"],
            "retrieved_docs": [{"title": "doc1"}],
            "project_analysis": {
                "identified_project": {"project_id": "proj1"},
                "workflow_trace": {"workflow_run_id": "wf1"},
                "data": {"project_id": "proj1"},
                "project_memory": {"last_run_id": "r1"},
                "result_payload": {
                    "answer": "fused project answer",
                    "report": "# proj1 report",
                    "knowledge_retrieval": {"documents": []},
                    "used_knowledge": False,
                    "answer_quality": {"passed": True, "score": 91},
                },
                "answer": "fused project answer",
                "report": "# proj1 report",
                "knowledge_retrieval": {"documents": []},
                "used_knowledge": False,
                "answer_quality": {"passed": True, "score": 91},
            },
        }

    monkeypatch.setattr(
        "multi_agent.backed.app.api.routers.MultiAgentService.process_task_sync",
        fake_process_task_sync,
    )

    # 2026-07-03 修复：`chat()` 之后新增了 `auth_user: dict = Depends(_require_auth_user)`
    # 参数——直接函数调用（绕开 FastAPI 依赖注入）必须显式传入这个参数，否则拿到的是
    # `Depends(...)` 哨兵对象本身，`_resolve_user_id` 对它调用 `.get()` 会报错。
    response = asyncio.run(
        chat(
            ChatCompatRequest(
                question="proj1 frip issue",
                user_id="u1",
                session_id="s1",
                mode="agent",
                project_id="proj1",
                project_root="D:/proj1",
                max_evidence_files=5,
            ),
            auth_user={"user_id": "u1"},
        )
    )

    assert response["answer"] == "chat answer"
    assert response["identified_project"]["project_id"] == "proj1"
    assert response["project_answer"] == "fused project answer"
    assert response["report"] == "# proj1 report"
    assert response["answer_quality"]["score"] == 91


def test_session_bound_project_forces_agent_route(monkeypatch):
    request = ChatMessageRequest(
        query="这个项目的FRiP还有问题吗",
        context=UserContext(user_id="u1", session_id="s1"),
        mode="auto",
        max_evidence_files=5,
    )

    monkeypatch.setattr(
        "multi_agent.backed.app.services.agent_service.session_service.prepare_history",
        lambda user_id, session_id, user_query: [{"role": "user", "content": user_query}],
    )
    monkeypatch.setattr(
        "multi_agent.backed.app.services.agent_service.session_service.save_history",
        lambda user_id, session_id, history: None,
    )
    monkeypatch.setattr(
        "multi_agent.backed.app.services.agent_service.project_session_state_service.load_state",
        lambda user_id, session_id: {
            "current_project_id": "proj1",
            "current_project_root": "D:/proj1",
            "recent_questions": ["proj1 frip issue"],
            "last_identified_at": "2026-01-01T00:00:00",
        },
    )
    captured = {}

    async def fake_business_analysis(query, **kwargs):
        captured["project_id"] = kwargs["project_id"]
        captured["project_root"] = kwargs["project_root"]
        return {"answer": "project session answer", "analysis_result": None}

    monkeypatch.setattr(
        "multi_agent.backed.app.services.agent_service.execute_project_business_analysis",
        fake_business_analysis,
    )

    result = asyncio.run(MultiAgentService.process_task_sync(request))

    assert result["answer"] == "project session answer"
    assert captured["project_id"] == "proj1"
    assert captured["project_root"] == "D:/proj1"


def test_diagnostic_analysis_reads_scripts_only_as_private_context(tmp_path: Path):
    project_root = tmp_path / "proj_private_workflow"
    project_root.mkdir()
    (project_root / "ReadsQC.xls").write_text(
        "Sample\tAdapter\tQ20\tQ30\nS1\t5\t98\t90\n",
        encoding="utf-8",
    )
    (project_root / "main.sh").write_text(
        "SECRET_CMD='private_command --token hidden'\nrun_pipeline --qvalue 0.01\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisService.analyze(
        project_id="proj_private_workflow",
        question="why is this QC result abnormal; check workflow parameters",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    assert "private_command" in result["_internal_workflow_context"]
    assert "main.sh" not in result["evidence_files"]
    assert "private_command" not in result["report"]

    sanitized = BusinessAgentRuntimeService._remove_internal_fields(result)
    assert "_internal_workflow_context" not in sanitized
    assert "private_command" not in str(sanitized)


def test_project_context_cache_coalesces_parallel_html_context_builds(tmp_path: Path, monkeypatch):
    # 2026-07-03 修复：项目上下文缓存/去重（`_PROJECT_CONTEXT_CACHE`/
    # `_PROJECT_CONTEXT_IN_FLIGHT`/`build_cached_project_context`）早已从
    # ProjectAnalysisService 搬到 project_parse_cache.py（见 CLAUDE.md 服务表
    # "project_parse_cache | 文件解析结果缓存、项目上下文 TTL 缓存"），真正构建
    # 上下文的 `build_project_context` 在 project_context_builder_service 上。
    # 这条用例是搬迁前遗留的孤儿测试，调用路径没跟着更新——本次只改调用对象，
    # 断言的"并发去重"逻辑本身不变。
    project_root = tmp_path / "proj_context_cache"
    project_root.mkdir()
    project_parse_cache._PROJECT_CONTEXT_CACHE.clear()
    project_parse_cache._PROJECT_CONTEXT_IN_FLIGHT.clear()

    entered = threading.Event()
    release = threading.Event()
    calls: list[bool] = []

    def fake_build_context(cls, root: Path, include_html_body: bool = True):
        calls.append(include_html_body)
        entered.set()
        release.wait(timeout=2)
        return {
            "samplelist_file": "",
            "samples": [],
            "config_file": "",
            "config": {},
            "report_roots": [],
            "html_report": {"file": "report.html", "section_text": "cached report"},
            "metric_guides": [],
            "metric_glossary": {},
        }

    monkeypatch.setattr(ProjectContextBuilderService, "build_project_context", classmethod(fake_build_context))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(project_parse_cache.build_cached_project_context, project_root, True)
        assert entered.wait(timeout=2)
        second = pool.submit(project_parse_cache.build_cached_project_context, project_root, True)
        release.set()
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert len(calls) == 1
    assert first_result == second_result


def test_followup_execution_prompt_uses_bound_project_context():
    state = {
        "active_project_id": "VZ20260423002",
        "active_project_root": "D:/projects/VZ20260423002",
        "project_context_locked": True,
        "pending_followup_action": {
            "project_id": "VZ20260423002",
            "summary": "复核 ReadsQC 结果",
            "actions": ["检查 Q20 和 Q30"],
        },
    }

    prompt, confirmed = MultiAgentService._resolve_followup_execution("继续排查", state)

    assert confirmed is True
    assert "VZ20260423002" in prompt
    assert "D:/projects/VZ20260423002" in prompt
    assert "检查 Q20 和 Q30" in prompt


def test_cause_graph_ranks_independently_supported_cause():
    # 2026-07-03 修复：因果图构建已迁移到 project_cause_analysis_service.py
    # （见 CLAUDE.md 服务职责表："因果图构建、假说排名、竞争假说对比"）——
    # 这条是搬迁前遗留的孤儿测试。
    graph = ProjectCauseAnalysisService.build_cause_graph(
        question="为什么线粒体 reads 比例高",
        analysis_plan={
            "target_metrics": ["mt_rate_percent"],
            "metric_evidence_plan": {
                "mt_rate_percent": {
                    "primary": ["mt_rate_percent"],
                    "parallel": [
                        "mapping_rate_percent",
                        "unique_mapping_rate_percent",
                        "duplicate_rate_percent",
                    ],
                    "candidate_causes": [
                        "organelle_dna_background",
                        "organelle_filtering_not_applied_before_statistics",
                        "sample_preparation_background",
                    ],
                }
            },
        },
        evidence_chain=[
            {
                "metric_key": "mt_rate_percent",
                "metric": "Mitochondrial alignment rate",
                "sample": "S1",
                "display_value": "72.61%",
                "severity": "unverified_threshold",
                "source_file": "AlignmentQC.xls",
                "source_field": "MT_Ratio",
            },
            {
                "metric_key": "mapping_rate_percent",
                "metric": "Mapping rate",
                "sample": "S1",
                "display_value": "57.54%",
                "severity": "warning",
                "source_file": "AlignmentQC.xls",
                "source_field": "Mapping_Rate",
            },
            {
                "metric_key": "unique_mapping_rate_percent",
                "metric": "Unique mapping rate",
                "sample": "S1",
                "display_value": "12.60%",
                "severity": "warning",
                "source_file": "AlignmentQC.xls",
                "source_field": "Unique_Mapped_Rate",
            },
        ],
        tool_diagnostics=[
            {
                "tool": "diagnose_cuttag_alignment_loss",
                "status": "needs_review",
                "evidence_gaps": ["缺少过滤前后 BAM 统计。"],
                "next_checks": ["重算过滤前后细胞器 reads 比例。"],
                "reasoning_chain": ["线粒体 reads 与 mapping/unique 需要联动解释。"],
            }
        ],
        project_context={
            "workflow_rule_sources": {
                "organelle_handling": {
                    "value": "chrM",
                    "source_file": "config.yaml",
                    "source_level": "project_verified",
                }
            }
        },
    )

    assert graph["version"] == "metric-evidence-graph-v2"
    assert graph["ranked_causes"][0]["cause_id"] == "organelle_dna_background"
    assert graph["ranked_causes"][0]["support_level"] == "supported"
    assert graph["confirmed_hypothesis"]["cause_id"] == "organelle_dna_background"
    assert graph["ranked_causes"][0]["verified_support_count"] == 2
    assert all(
        item["metric_key"] != "mt_rate_percent"
        for item in graph["ranked_causes"][0]["supporting_evidence"]
    )
    assert graph["ranked_causes"][0]["verification_actions"]
    assert graph["ranked_causes"][0]["expected_validation_outcomes"]


def test_target_metric_alone_does_not_confirm_root_cause():
    graph = ProjectCauseAnalysisService.build_cause_graph(
        question="为什么线粒体 reads 比例高",
        analysis_plan={"target_metrics": ["mt_rate_percent"]},
        evidence_chain=[
            {
                "metric_key": "mt_rate_percent",
                "metric": "Mitochondrial alignment rate",
                "sample": "S1",
                "display_value": "72.61%",
                "severity": "unverified_threshold",
                "source_file": "AlignmentQC.xls",
                "source_field": "MT_Ratio",
            }
        ],
        tool_diagnostics=[],
        project_context={},
    )

    assert graph["ranked_causes"]
    assert graph["confirmed_hypothesis"] is None
    assert graph["diagnostic_confidence"]["level"] == "low"
    assert all(item["support_level"] == "insufficient_evidence" for item in graph["ranked_causes"])
    assert all(not item["supporting_evidence"] for item in graph["ranked_causes"])


def test_duplicate_sources_do_not_inflate_independent_cause_support():
    graph = ProjectCauseAnalysisService.build_cause_graph(
        question="为什么线粒体 reads 比例高",
        analysis_plan={
            "metric_evidence_plan": {
                "mt_rate_percent": {
                    "primary": ["mt_rate_percent"],
                    "parallel": ["mapping_rate_percent"],
                    "candidate_causes": ["organelle_dna_background"],
                }
            }
        },
        evidence_chain=[
            {
                "metric_key": "mt_rate_percent",
                "sample": "S1",
                "display_value": "72.61%",
                "severity": "unverified_threshold",
            },
            {
                "metric_key": "mapping_rate_percent",
                "measurement_id": "mapping_rate_percent",
                "population_scope": "alignment input reads",
                "sample": "S1",
                "display_value": "57.54%",
                "severity": "warning",
                "source_file": "AlignmentQC.xls",
                "source_field": "Mapping_Rate",
            },
            {
                "metric_key": "mapping_rate_percent",
                "measurement_id": "mapping_rate_percent",
                "population_scope": "alignment input reads",
                "sample": "S1",
                "display_value": "57.54%",
                "severity": "warning",
                "source_file": "report.html",
                "source_field": "Mapping",
            },
        ],
        tool_diagnostics=[],
        project_context={},
    )

    cause = graph["ranked_causes"][0]
    assert cause["verified_support_count"] == 1
    assert cause["supporting_evidence_count"] == 1
    assert cause["support_level"] == "partially_supported"


def test_cuttag_workflow_reads_species_from_project_config():
    workflow = ProjectCuttagDiagnosticService._workflow_context(
        {"config": {"species": "hg38", "assay": "CUT&Tag"}}
    )

    assert workflow["species"] == "hg38"
    assert workflow["organelle_label"] == "线粒体 reads 比例"


def test_existing_report_summary_suppresses_nulls_raw_formulas_and_contradiction():
    analysis_result = {
        "project_id": "P1",
        "question": "总结这个项目报告",
        "report_mode": "existing_html_report_summary",
        "project_context": {"html_report": {"sections": [{"title": "QC", "text": "QC summary"}]}},
        "anomaly_summary": {"critical": [], "warning": []},
        "diagnosis_summary": {"possible_causes": [], "next_actions": ["复核样本分组和相关性矩阵。"]},
        "next_actions": ["复核样本分组和相关性矩阵。"],
        "evidence_chain": [
            {
                "metric_key": "q30_ratio",
                "metric": "Q30",
                "sample": "T1",
                "value": None,
                "display_value": "-",
                "source_file": "ReadsQC.xls",
                "source_field": "Q30",
            },
            {
                "metric_key": "correlation",
                "metric": "Spearman correlation",
                "sample": "T1 vs T2",
                "value": -0.6303,
                "display_value": "-0.6303",
                "severity": "unverified_threshold",
                "threshold_needs_project_validation": True,
                "source_file": "spearman_Corr_readCounts.tab",
                "source_field": "T1::T2",
                "formula": "python_internal_corr(signal_a, signal_b)",
                "formula_source": "not_found_in_project_code",
            },
        ],
    }

    answer = business_response_service.build_existing_html_report_answer(analysis_result)
    quality = BusinessAnswerQualityService.evaluate(
        answer=answer,
        analysis_result=analysis_result,
        question_route={"route": "ai_report_summary"},
    )

    assert "T1 vs T2" in answer
    assert "-0.6303" in answer
    assert "Q30" not in answer
    assert "python_internal_corr" not in answer
    assert "未识别到项目文件阈值支持的需复核指标" not in answer
    assert "没有项目已验证阈值支持的异常结论" in answer
    assert quality["passed"] is True


def test_threshold_limits_ignore_missing_metric_placeholders():
    limits = ProjectAnalysisService._build_threshold_validation_warnings(
        [
            {
                "metric_key": "q30_ratio",
                "metric": "Q30",
                "value": None,
                "display_value": "-",
                "threshold_needs_project_validation": True,
            },
            {
                "metric_key": "adapter_percent",
                "metric": "Adapter",
                "value": 38.24,
                "display_value": "38.24%",
                "threshold_needs_project_validation": True,
            },
        ]
    )

    assert len(limits) == 1
    assert "Adapter" in limits[0]
    assert "Q30" not in limits[0]


def test_strict_log_file_lookup_excludes_non_log_text_files(tmp_path: Path):
    project_root = tmp_path / "proj_strict_logs"
    project_root.mkdir()
    (project_root / "run.log").write_text("ERROR real log failure\n", encoding="utf-8")
    (project_root / "stderr.txt").write_text("ERROR text stderr should not be used\n", encoding="utf-8")
    (project_root / "pipeline").write_text("ERROR extensionless file should not be used\n", encoding="utf-8")

    logs = find_log_files(project_root, limit=10, strict_log_suffix=True)

    assert [path.name for path in logs] == ["run.log"]


def test_default_log_file_lookup_still_includes_run_text_files(tmp_path: Path):
    project_root = tmp_path / "proj_default_logs"
    project_root.mkdir()
    (project_root / "run.txt").write_text("ERROR legacy run log\n", encoding="utf-8")

    logs = find_log_files(project_root, limit=10)

    assert [path.name for path in logs] == ["run.txt"]


def test_pipeline_failure_analysis_reads_only_log_files_and_short_circuits(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "proj_pipeline_failure"
    project_root.mkdir()
    (project_root / "run.log").write_text(
        "INFO start\n"
        "Traceback (most recent call last):\n"
        "ERROR missing reference index\n",
        encoding="utf-8",
    )
    (project_root / "stderr.txt").write_text("ERROR non-log text should not be read\n", encoding="utf-8")
    (project_root / "ReadsQC.xls").write_text(
        "Sample\tAdapter\tQ30\nS1\t99\t10\n",
        encoding="utf-8",
    )
    (project_root / "config.yaml").write_text("species: hg38\n", encoding="utf-8")
    (project_root / "main.sh").write_text("private_command --token hidden\n", encoding="utf-8")

    def fail_build_context(*args, **kwargs):
        raise AssertionError("pipeline_failure must not build project context")

    def fail_expert_loop(*args, **kwargs):
        raise AssertionError("pipeline_failure must not run expert loop")

    # 2026-07-03 修复：`analyze()` 现在直接调用 project_parse_cache.
    # build_cached_project_context（见该文件同名方法），不再经过
    # ProjectAnalysisService 自己的方法——这条用例是搬迁前遗留的孤儿测试。
    monkeypatch.setattr(project_parse_cache, "build_cached_project_context", fail_build_context)
    monkeypatch.setattr(
        "multi_agent.backed.app.services.project_analysis_service.project_expert_tool_service.run_loop",
        fail_expert_loop,
    )

    result = ProjectAnalysisService.analyze(
        project_id="proj_pipeline_failure",
        question="项目报错的原因",
        project_root=str(project_root),
        max_evidence_files=10,
    )

    assert result["question_type"] == "pipeline_failure"
    assert result["evidence_files"] == ["run.log"]
    assert result["parsed_metrics"] == {}
    assert result["agent_loop"]["round_count"] == 0
    assert "ReadsQC.xls" not in str(result)
    assert "config.yaml" not in str(result)
    assert "private_command" not in str(result)
    assert result["fact_packet"]["pipeline_errors_found"] is True
    claims = [item["claim"] for item in result["fact_packet"]["direct_conclusions"]]
    assert any("missing reference index" in claim for claim in claims)
    assert result["analysis_limits"] == []
    assert result["read_plan"] == ["只读 .log，提取错误行"]
    assert result["report"] == (
        "日志文件：run.log\n"
        "错误信息：\n"
        "- [run.log] Traceback (most recent call last):\n"
        "- [run.log] ERROR missing reference index\n"
        "解释：日志中直接出现上述错误行，项目失败原因优先以这些日志错误为准。"
    )
    assert "下一步" not in result["report"]
    assert "QC" not in result["report"]
    assert "指标" not in result["report"]
    assert business_response_service.build_analysis_context(
        analysis_result=result,
        experience_summary={},
    ) == result["report"]
    messages = business_response_service._build_fused_answer_messages(
        question="项目报错的原因",
        analysis_result=result,
        retrieval_payload={"documents": []},
        experience_summary={},
    )
    combined_prompt = "\n".join(message["content"] for message in messages)
    assert "保持简洁" in combined_prompt
    assert "专业原因链路" not in combined_prompt
    assert "可执行复核动作" not in combined_prompt
    assert "QC" not in combined_prompt


def test_pipeline_failure_analysis_filters_cutadapt_error_table_labels(tmp_path: Path):
    project_root = tmp_path / "proj_cutadapt_log_labels"
    project_root.mkdir()
    (project_root / "trim.log").write_text(
        "[FFPE_H3012326.trim.log] No. of allowed errors:\n"
        "[FFPE_H3012326.trim.log] length\tcount\texpect\tmax.err\terror counts\n"
        "[FFPE_Z1391354.trim.log] No. of allowed errors:\n"
        "[FFPE_Z1391354.trim.log] length count expect max.err error counts\n"
        "ERROR missing adapter index\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisService.analyze(
        project_id="proj_cutadapt_log_labels",
        question="why did the project error",
        project_root=str(project_root),
        max_evidence_files=10,
    )

    claims = [item["claim"] for item in result["fact_packet"]["direct_conclusions"]]

    assert claims == ["[trim.log] ERROR missing adapter index"]
    assert "No. of allowed errors" not in result["report"]
    assert "length count expect max.err error counts" not in result["report"]
    assert "解释：" in result["report"]
    assert "缺少" in result["report"]
    assert "索引" in result["report"]


def test_pipeline_failure_answer_context_filters_stale_cutadapt_table_claims():
    context = business_response_service.build_analysis_context(
        analysis_result={
            "report_mode": "pipeline_failure_log_only",
            "evidence_files": ["filter.log"],
            "report": (
                "日志文件：filter.log\n"
                "错误信息：\n"
                "- [filter.log] length count expect max.err error counts\n"
                "解释：\n"
                "- [filter.log] length count expect max.err error counts：日志明确标记 ERROR，表示该步骤执行失败。"
            ),
            "fact_packet": {
                "pipeline_errors_found": True,
                "direct_conclusions": [
                    {
                        "claim": "[filter.log] length count expect max.err error counts",
                        "confidence": "direct_log_evidence",
                    }
                ],
            },
        },
        experience_summary={},
    )

    assert "length count expect max.err error counts" not in context
    assert "日志明确标记 ERROR" not in context
    assert "未检测到真实错误行" in context


def test_workflow_pipeline_failure_uses_deterministic_filtered_answer(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "proj_pipeline_filter_only"
    project_root.mkdir()
    (project_root / "filter.log").write_text(
        "length count expect max.err error counts\n",
        encoding="utf-8",
    )

    async def fail_stream(*args, **kwargs):
        raise AssertionError("pipeline failure log-only answers must not call the LLM")
        yield ""

    monkeypatch.setattr(
        "multi_agent.backed.app.services.business_agent.runtime_service.business_response_service.stream_fused_answer",
        fail_stream,
    )

    result = ProjectAnalysisWorkflowService.run_analysis(
        question="项目为什么报错",
        project_id="proj_pipeline_filter_only",
        user_id="u1",
        session_id="s_pipeline_filter_only",
        project_root=str(project_root),
        max_evidence_files=4,
    )

    answer = result["result_payload"]["answer"]

    assert result["success"] is True
    assert "length count expect max.err error counts" not in answer
    assert "日志明确标记 ERROR" not in answer
    assert "未检测到真实错误行" in answer


def test_pipeline_failure_explains_snakemake_rule_failure_as_pointer_to_detail_log(tmp_path: Path):
    project_root = tmp_path / "proj_snakemake_diffbind"
    log_dir = project_root / ".snakemake" / "log"
    log_dir.mkdir(parents=True)
    (log_dir / "2026-06-20T115618.538751.snakemake.log").write_text(
        "RuleException:\n"
        "CalledProcessError in file "
        "\"/beegfs/Pipline_cloud/data_cloud/Snakemake_Sop/CUTTag/Pipline_smk/cuttag_pipline/rules/8.diff_DiffBind.smk\", line 151:\n"
        "Error in rule diffbind_analyze:\n"
        "    log: /data/Pipline_cloud/data_cloud/Result/Peak差异分析/VZ20260620003/DiffAnalysis/DiffBind/results/Oxamate-vs-Control/run_diffbind.log (check log file(s) for error details)\n"
        "Exiting because a job execution failed. Look below for error messages\n",
        encoding="utf-8",
    )

    result = ProjectAnalysisService.analyze(
        project_id="proj_snakemake_diffbind",
        question="项目为什么报错",
        project_root=str(project_root),
        max_evidence_files=10,
    )
    answer = business_response_service.build_analysis_context(
        analysis_result=result,
        experience_summary={},
    )

    assert "diffbind_analyze" in answer
    assert "8.diff_DiffBind.smk" in answer
    assert "第 151 行" in answer
    assert "外部命令" in answer
    assert "run_diffbind.log" in answer
    assert "真正的详细错误" in answer
    assert "日志明确标记 ERROR" not in answer
