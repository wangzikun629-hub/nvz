from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from multi_agent.backed.app.services.project_analysis_service import ProjectAnalysisService
from multi_agent.backed.app.services.project_analysis_workflow_service import (
    ProjectAnalysisWorkflowService,
)
from multi_agent.backed.app.services.project_locator_service import ProjectCandidate, ProjectLocatorService


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_checks() -> None:
    locator = ProjectLocatorService()
    locator.list_projects = lambda limit=200: [
        ProjectCandidate("proj_alpha", "D:/data/proj_alpha", ("sampleA",)),
        ProjectCandidate("proj_alpha_rep", "D:/data/proj_alpha_rep", ("sampleB",)),
    ]
    ambiguous = locator.identify_project("alpha project", None, "u1", "s1")
    _check(ambiguous["needs_confirmation"] is True, "ambiguous project should require confirmation")

    exact = locator.identify_project("any", "proj_alpha", "u1", "s1")
    _check(exact["matched_by"] == "project_id", "exact project id should be accepted")

    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "proj"
        root.mkdir()
        (root / "ReadsQC.xls").write_text(
            "Sample\tAdapter\tQ20\tQ30\tRaw Reads\tClean Reads\n"
            "S1\t55\t98\t94\t1000\t900\n",
            encoding="utf-8",
        )
        (root / "spearman_Corr_readCounts.tab").write_text(
            "Sample\tS1\tS2\n"
            "S1\t1\t0.82\n"
            "S2\t0.82\t1\n",
            encoding="utf-8",
        )
        (root / "sample_meme.txt").write_text(
            "MOTIF TGAGTCAY MEME-1\twidth =   8  sites = 2443  llr = 18767  E-value = 3.9e-241\n",
            encoding="utf-8",
        )
        (root / "sample_final_anno.xls").write_text(
            "SYMBOL\tchange\tannotation\tdistanceToTSS\n"
            "GENE1\tup\tPromoter-TSS\t-120\n"
            "GENE2\tdown\tIntron\t2300\n",
            encoding="utf-8",
        )
        (root / "GO_up.xls").write_text(
            "ONTOLOGY\tDescription\tGeneRatio\tp.adjust\n"
            "BP\tcell differentiation\t3/40\t0.001\n",
            encoding="utf-8",
        )
        result = ProjectAnalysisService.analyze("proj", "diff motif qc", str(root), 8)
        _check(result["trace"]["status"] == "ok", "analysis trace should be ok")
        _check(result["parsed_metrics"]["qc"][0]["q30_ratio"] == 0.94, "q30 should normalize to ratio")
        _check(result["parsed_metrics"]["correlation"]["max_pair"] == ("S1", "S2", 0.82), "correlation should skip diagonal")
        _check(result["parsed_metrics"]["motif_summary"]["samples"][0]["sample"] == "sample", "motif summary should aggregate by sample")
        diff_items = result["parsed_metrics"]["diff"]
        _check(any(item["kind"] == "diff_annotation" for item in diff_items), "diff annotation parser should run")
        _check(any(item["kind"] == "diff_go" for item in diff_items), "diff GO parser should run")

        workflow = ProjectAnalysisWorkflowService.run_analysis(
            question="proj diff motif qc",
            project_id="proj",
            user_id="u1",
            session_id="s1",
            project_root=str(root),
            max_evidence_files=8,
        )
        _check(workflow["success"] is True, "workflow should succeed")
        _check(workflow["workflow_trace"]["analysis_run_id"] == workflow["data"]["run_id"], "workflow trace should point to analysis run")
        _check("result_payload" in workflow, "workflow should include result payload")
        _check("answer" in workflow["result_payload"], "result payload should include answer")

    print("project analysis checks passed")


if __name__ == "__main__":
    run_checks()
