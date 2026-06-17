import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from multi_agent.backed.app.services.business_agent.fact_verification_service import (
    FactVerificationService,
)


def test_reference_numeric_sentence_is_not_project_fact_claim():
    analysis_result = {
        "question_type": "alignment",
        "evidence_cards": [
            {
                "metric_id": "frip_ratio",
                "sample": "S1",
                "value": 0.2224,
                "display_value": "22.24%",
                "value_scale": "fraction",
                "source_file": "FRiP_score.xls",
                "source_field": "FRiP",
            }
        ],
        "evidence_chain": [
            {
                "metric_key": "frip_ratio",
                "sample": "S1",
                "value": 0.2224,
                "display_value": "22.24%",
            }
        ],
    }

    answer = "如果 FRiP 低于 20%，通常说明富集不足，但本项目当前观测值为 22.24%。"
    result = FactVerificationService.verify(answer=answer, analysis_result=analysis_result)

    assert not any(
        item.get("rule") == "numeric_claim_not_found_in_project_evidence"
        and 20.0 in (item.get("numbers") or [])
        for item in result.get("issues", [])
    )


def test_bullet_numeric_reference_sentence_is_not_project_fact_claim():
    analysis_result = {
        "question_type": "frip",
        "evidence_cards": [
            {
                "metric_id": "frip_ratio",
                "sample": "S1",
                "value": 0.2224,
                "display_value": "22.24%",
                "value_scale": "fraction",
                "source_file": "FRiP_score.xls",
                "source_field": "FRiP",
            }
        ],
    }

    answer = "- 如果 NRF < 0.7-0.8，应进一步评估是否需要调整去重策略。"
    result = FactVerificationService.verify(answer=answer, analysis_result=analysis_result)

    assert not any(
        item.get("rule") == "numeric_claim_not_found_in_project_evidence"
        for item in result.get("issues", [])
    )


def test_list_numbering_is_not_treated_as_project_numeric_claim():
    analysis_result = {
        "question_type": "frip",
        "evidence_cards": [
            {
                "metric_id": "frip_ratio",
                "sample": "S1",
                "value": 0.2224,
                "display_value": "22.24%",
                "value_scale": "fraction",
                "source_file": "FRiP_score.xls",
                "source_field": "FRiP",
            }
        ],
    }

    answer = "1. 重复性不能只靠 FRiP 交叉值。\n2. 还要结合相关性与 peak 质量。"
    result = FactVerificationService.verify(answer=answer, analysis_result=analysis_result)

    assert not any(
        item.get("rule") == "numeric_claim_not_found_in_project_evidence"
        and 1.0 in (item.get("numbers") or [])
        for item in result.get("issues", [])
    )
