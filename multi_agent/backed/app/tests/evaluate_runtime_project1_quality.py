"""
Runtime-integrated quality evaluation for project 1 (VZ20260531009, HUH6 CUT&Tag).
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
    r"\1f75d44bb047\VZ20260531009"
)
PROJECT_ID = "VZ20260531009"
RUN_ID = uuid4().hex[:8]

QUESTIONS = [
    "为什么 HUH6_GNE_140_YD 样本的 duplication rate 达到 73%，远高于其他样本？",
    "HUH6_GNE_140_YD 样本的 mapping rate、unique mapping、duplication、FRiP 和相关性都与其他三个样本差异很大，请帮我系统分析原因",
    "HUH6_GNE_140_YD 与其他三个样本的 spearman 相关性只有 0.37-0.39，而其他三个样本之间相关性都很高（>0.93），这说明什么？",
    "这个项目的整体数据质量如何？有哪些样本或指标需要重点关注？",
    "FRiP_raw.txt 提供了 3×3 的跨样本 FRiP 矩阵——以每个样本的 peaks 分别去计算所有样本的 FRiP。例如以 HUH6_GNE_140 的 peaks 为参考时：HUH6_GNE_140 自身 FRiP=16.40%、HUH6_NC=12.54%、HUH6_Rescue=9.57%；以 HUH6_Rescue 的 peaks 为参考时：HUH6_GNE_140=12.19%、HUH6_NC=10.08%、HUH6_Rescue=7.90%。结合 Spearman 相关性矩阵（IP 间 >0.93），请分析这个跨样本 FRiP 模式揭示了什么关于三个 IP 样本之间生物学相似性和差异性的信息？这种'交叉 FRiP'分析与简单的相关性分析相比有什么额外的生物学洞察力？",
]


def print_separator(title: str) -> None:
    print(f"\n{'='*80}\n  {title}\n{'='*80}")


async def evaluate_single_question(question: str, index: int) -> dict:
    request = ChatMessageRequest(
        query=question,
        context=UserContext(user_id="runtime_eval_user", session_id=f"runtime_eval_p1_{RUN_ID}_{index}"),
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
    print_separator("项目1 runtime-integrated 质量评估（packet-first）")
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

    output_path = Path(__file__).resolve().with_name("evaluation_results_runtime_project1.json")
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  完整结果已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
