"""
Runtime-integrated quality evaluation for project 2 (VZ20260513002, extreme QC failure).
Uses MultiAgentService.process_task_sync (same path as /api/chat) with clean per-run
session IDs to avoid follow-up contamination.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from multi_agent.backed.app.schemas.request import ChatMessageRequest, UserContext
from multi_agent.backed.app.services.agent_service import MultiAgentService

PROJECT_ROOT = (
    r"D:\nvz\kefu\multi_agent\backed\app\.project_sftp_cache"
    r"\ba7f0610952b\VZ20260513002"
)
PROJECT_ID = "VZ20260513002"
RUN_ID = uuid4().hex[:8]

QUESTIONS = [
    "这个项目的两个样本 T1 和 T2 的 Mitochondrial reads 比例分别高达 72.61% 和 66.87%，而 Mapping Rate 分别只有 57.54% 和 57.71%。请从 CUT&Tag 实验技术原理、细胞状态和建库流程三个层面，系统分析为什么会出现如此极端的线粒体 reads 比例，以及为什么高 MT 比例会导致总 Mapping Rate 被压低。如果这两个样本还想挽救用于下游分析，应该采取什么措施？",
    "T1 和 T2 两个样本之间的 Spearman 相关系数竟然是 -0.6303。正常情况下 CUT&Tag 重复样本之间应该是高度正相关（>0.9），即使不同处理之间也不应该出现负相关。请从技术噪声、线粒体基因组干扰、文库复杂度差异和潜在生物学差异四个角度，系统分析负相关可能的原因。如果必须判断这两个样本是'生物学差异'还是'技术失败'，你的判断是什么？依据是什么？",
    "请对这个项目的整体数据质量做一个全面评估。重点关注：Mapping Rate 仅 57%、Unique Mapping 仅 12-15%、MT rate 67-73%、NRF 0.67-0.70（Concerning）、Spearman 相关性 -0.63。这个项目的数据是否还有任何下游分析价值？如果没有，请说明从哪些指标可以做出这个判断；如果有，请说明哪些分析还能做、需要什么前提条件。",
    "尽管数据质量极差，macs3 仍然在 T1 中检出了 384 个 peaks（FRiP=14.82%），T2 中检出了 380 个 peaks（FRiP=12.05%）。跨样本 FRiP 显示 T1 peaks 在 T2 上仅 5.43%，T2 peaks 在 T1 上仅 6.48%。请批判性分析：在这种极端低质量数据下（mapping 57%、unique 12-15%、MT 67-73%），macs3 检出的这些 peaks 是否可信？FRiP=14.82% 在这种背景下意味着什么？是否需要这些 peak 结果？",
]


def print_separator(title: str) -> None:
    print(f"\n{'='*80}\n  {title}\n{'='*80}")


async def evaluate_single_question(question: str, index: int) -> dict:
    request = ChatMessageRequest(
        query=question,
        context=UserContext(user_id="runtime_eval_user", session_id=f"runtime_eval_p2_{RUN_ID}_{index}"),
        mode="agent",
        project_id=PROJECT_ID,
        project_root=PROJECT_ROOT,
        max_evidence_files=20,
    )
    result = await MultiAgentService.process_task_sync(request)
    payload = (result.get("project_analysis") or {}).get("result_payload") or {}
    return {
        "question": question,
        "answer": str(payload.get("answer") or result.get("answer") or ""),
        "quality_score": (payload.get("answer_quality") or {}).get("score", 0),
        "quality_passed": (payload.get("answer_quality") or {}).get("passed"),
        "fact_passed": (payload.get("fact_verification") or {}).get("passed"),
        "answer_quality": payload.get("answer_quality") or {},
        "fact_verification": payload.get("fact_verification") or {},
    }


async def main():
    print_separator("项目2 runtime-integrated 质量评估（packet-first，极端 QC 失败场景）")
    print(f"项目: {PROJECT_ID}\n路径: {PROJECT_ROOT}\n问题数量: {len(QUESTIONS)}")
    results = []
    for idx, question in enumerate(QUESTIONS):
        results.append(await evaluate_single_question(question, idx))

    avg = sum(item["quality_score"] for item in results) / len(results)
    passed = sum(1 for item in results if item["quality_passed"])
    fact_passed = sum(1 for item in results if item["fact_passed"])

    print_separator("汇总报告")
    print(f"\n  平均 runtime 质量分: {avg:.1f}/100")
    print(f"  answer_quality 通过率: {passed}/{len(results)}")
    print(f"  fact_verification 通过率: {fact_passed}/{len(results)}")
    print(f"\n  各问题得分:")
    for i, item in enumerate(results):
        status = "[PASS]" if item["quality_passed"] else "[FAIL]"
        print(f"  {status} Q{i+1}: {item['quality_score']:.0f}分 - {item['question'][:60]}...")

    output_path = Path(__file__).resolve().with_name("evaluation_results_runtime_project2.json")
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  完整结果已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
