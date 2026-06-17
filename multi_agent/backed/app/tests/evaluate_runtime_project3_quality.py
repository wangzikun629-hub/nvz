"""
Runtime-integrated project quality evaluation (Project 3)
Uses the same business runtime path as /api/chat instead of directly calling
analyze + generate_fused_answer + evaluate.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from multi_agent.backed.app.schemas.request import ChatMessageRequest, UserContext
from multi_agent.backed.app.services.agent_service import MultiAgentService

PROJECT_ROOT = (
    r"D:\nvz\kefu\multi_agent\backed\app\.project_sftp_cache"
    r"\f9361f715137\VZ20260508009"
)
PROJECT_ID = "VZ20260508009"

TEST_QUESTIONS = [
    "这个项目设置了 PH_Igg 作为 IgG 对照。PH_Igg 样本仅 60万 reads（IP样本的约1/40），mapping rate 只有 51%（IP样本 93-95%），但 NRF 高达 0.976、PBC2 高达 76.25。从实验设计角度分析：这个 IgG 对照的测序深度是否足够？NRF 和 PBC2 的极高值说明了什么？mapping rate 仅 51% 是否影响其作为对照的有效性？",
    "FRiP 矩阵显示了清晰的对比：IP样本自身FRiP为22.24%（PH_H3K27ac）和25.57%（PN_H3K27ac），交叉FRiP（一个IP对另一个IP的peak计算）为20.99%和21.49%，而IP样本在IgG对照上的FRiP仅1.07-1.11%，IgG自身FRiP仅3.13%。请系统解读这个FRiP矩阵：为什么两个IP样本的交叉FRiP接近自身FRiP（~21% vs 22-26%），而IP在IgG上的FRiP仅~1%？这种FRiP模式是否足以证明实验的特异性和重复性？",
    "Spearman相关性矩阵显示：PH_H3K27ac与PN_H3K27ac的相关性为0.9022（高度正相关），而两个IP样本与IgG对照的相关性仅0.3165和0.3200。结合FRiP、peak数量等指标，分析这种相关性差异反映了什么生物学意义？IP间0.90的相关性对H3K27ac这个组蛋白修饰来说是高还是低？IP-IgG间0.32的相关性说明了什么？",
    "项目配置了spike-in分析（spikein_analysis=yes, spikein_align=2）。PH_H3K27ac和PN_H3K27ac的FRiP分别为22.24%和25.57%，peak数量PN更高。如果有spike-in scaling factor，两组之间的H3K27ac信号强度差异可以通过spike-in进行归一化。请解释：(1) CUT&Tag中spike-in标准化的原理（与ChIP-seq中常用的spike-in有何异同？）(2) 如果spike-in归一化后PN_H3K27ac的信号强度仍显著高于PH_H3K27ac，可能反映什么生物学差异？",
]


def print_separator(title: str) -> None:
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")


async def evaluate_single_question(question: str, index: int) -> dict:
    print_separator(f"问题 {index+1}: {question}")
    request = ChatMessageRequest(
        query=question,
        context=UserContext(user_id="runtime_eval_user", session_id=f"runtime_eval_{index}"),
        mode="agent",
        project_id=PROJECT_ID,
        project_root=PROJECT_ROOT,
        max_evidence_files=20,
    )
    started = time.perf_counter()
    result = await MultiAgentService.process_task_sync(request)
    duration = time.perf_counter() - started

    project_analysis = result.get("project_analysis") or {}
    payload = project_analysis.get("result_payload") or {}
    answer = str(payload.get("answer") or result.get("answer") or "")
    answer_quality = payload.get("answer_quality") or project_analysis.get("answer_quality") or {}
    fact_verification = payload.get("fact_verification") or project_analysis.get("fact_verification") or {}
    workflow_trace = project_analysis.get("workflow_trace") or {}

    print(f"  [runtime] 调用完成 ({duration:.1f}s)")
    print(f"        - 回答长度: {len(answer)} 字符")
    print(f"        - 回答预览: {answer[:200].replace(chr(10), chr(10)+'          ')}")
    print(f"        - answer_quality: {answer_quality.get('score', 0)}/100")
    print(f"        - fact_verification passed: {fact_verification.get('passed')}")
    print(f"        - workflow status: {workflow_trace.get('status')}")

    return {
        "question": question,
        "answer": answer,
        "answer_length": len(answer),
        "quality_score": answer_quality.get("score", 0),
        "quality_passed": answer_quality.get("passed"),
        "fact_passed": fact_verification.get("passed"),
        "answer_quality": answer_quality,
        "fact_verification": fact_verification,
        "workflow_trace": workflow_trace,
        "duration_s": round(duration, 1),
    }


async def main():
    print_separator("项目3 runtime-integrated 质量评估")
    print(f"项目: {PROJECT_ID}")
    print(f"路径: {PROJECT_ROOT}")
    print(f"问题数量: {len(TEST_QUESTIONS)}")

    results = []
    for idx, question in enumerate(TEST_QUESTIONS):
        result = await evaluate_single_question(question, idx)
        results.append(result)

    print_separator("汇总报告")
    total_score = sum(item["quality_score"] for item in results)
    avg_score = total_score / len(results) if results else 0
    passed_count = sum(1 for item in results if item["quality_passed"])
    fact_passed_count = sum(1 for item in results if item["fact_passed"])

    print(f"\n  平均 runtime 质量分: {avg_score:.1f}/100")
    print(f"  answer_quality 通过率: {passed_count}/{len(results)}")
    print(f"  fact_verification 通过率: {fact_passed_count}/{len(results)}")
    print(f"  总耗时: {sum(item['duration_s'] for item in results):.1f}s")

    print(f"\n  各问题得分:")
    for i, item in enumerate(results):
        status = "[PASS]" if item["quality_passed"] else "[FAIL]"
        print(
            f"  {status} Q{i+1}: {item['quality_score']:.0f}分 "
            f"({item['answer_length']}字符, {item['duration_s']:.1f}s) - "
            f"{item['question'][:60]}..."
        )

    output_path = Path(__file__).resolve().with_name("evaluation_results_runtime_project3.json")
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  完整结果已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
