"""
真实项目数据 + LLM 回答质量评估脚本
使用 VZ20260531009（4样本 HUH6 CUT&Tag）项目测试 DeepSeekV4Pro 回答深度
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# 确保项目路径可导入
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from multi_agent.backed.app.services.project_analysis_service import ProjectAnalysisService
from multi_agent.backed.app.services.business_agent.response_service import (
    BusinessResponseService,
)
from multi_agent.backed.app.services.business_agent.answer_quality_service import (
    BusinessAnswerQualityService,
)
from multi_agent.backed.app.services.business_agent.analysis_planner_service import (
    AnalysisPlannerService,
)

# ── 真实项目路径 ──────────────────────────────────────────────
PROJECT_ROOT = (
    r"D:\nvz\kefu\multi_agent\backed\app\.project_sftp_cache"
    r"\1f75d44bb047\VZ20260531009"
)
PROJECT_ID = "VZ20260531009"

# ── 测试问题列表 ──────────────────────────────────────────────
TEST_QUESTIONS = [
    # ═══ 第1组：基础诊断与查询（原有5题） ═══
    # 问题1：单一指标异常诊断
    "为什么 HUH6_GNE_140_YD 样本的 duplication rate 达到 73%，远高于其他样本？",
    # 问题2：多指标关联分析
    "HUH6_GNE_140_YD 样本的 mapping rate、unique mapping、duplication、FRiP 和相关性都与其他三个样本差异很大，请帮我系统分析原因",
    # 问题3：相关性解读
    "HUH6_GNE_140_YD 与其他三个样本的 spearman 相关性只有 0.37-0.39，而其他三个样本之间相关性都很高（>0.93），这说明什么？",
    # 问题4：整体质控评估
    "这个项目的整体数据质量如何？有哪些样本或指标需要重点关注？",
    # 问题5：简单指标查询
    "各样本的 mapping rate 和 unique mapping rate 分别是多少？",

    # ═══ 第2组：文库复杂度与分子层面深度分析 ═══
    # 问题6：NRF/PBC 文库复杂度深度解读
    "HUH6_GNE_140_YD 的 NRF=0.27、PBC1=0.29、PBC2=1.85，而其他三个样本的 NRF 在 0.75-0.79、PBC1 在 0.77-0.81、PBC2 在 4.5-5.5。请从分子生物学层面解释 NRF/PBC1/PBC2 这三个指标分别衡量什么，并结合 fragment size distribution（YD 样本的 di-nucleosome 比例 14.5% 远高于其他样本的 5.8-6.0%）和 estimated library size（YD=563,729 vs 其他=57-70M），系统分析 HUH6_GNE_140_YD 在文库构建层面可能出了什么问题。",
    # 问题7：片段长度分布与 CUT&Tag 酶切生物学
    "各样本的 fragment size distribution 数据显示：三个 IP 样本的 sub-nucleosome（<150bp）比例约 48-49%、mono-nucleosome（150-300bp）约 32-33%、di-nucleosome（300-500bp）约 5.8-6.0%，median insert size 为 177-178bp；而 HUH6_GNE_140_YD 的 sub-nucleosome 仅 40.8%、di-nucleosome 高达 14.5%、median insert size 为 204bp。请从 CUT&Tag 技术原理（Tn5 转座酶靶向切割机制、核小体保护模式）出发，解释这些 fragment size 分布差异反映了什么样的生物学或技术问题，以及这对下游 peak calling 和信号富集会产生什么影响。",

    # ═══ 第3组：整合生物学解读与实验设计批判 ═══
    # 问题8：Peak/FRiP/Motif 跨样本整合分析
    "三个 IP 样本的 peak 数量差异很大：HUH6_GNE_140 有 26,263 个 peaks、HUH6_NC 有 21,371 个、HUH6_Rescue 只有 13,002 个。同时 FRiP 分别为 16.4%、12.2%、7.9%。从 motif 富集结果看，HUH6_GNE_140 和 HUH6_NC 都以 TEAD 家族转录因子为主（TEAD3/TEAD1/TEAD4），而 HUH6_Rescue 中 AP-1 家族（Fosl2/Fra2/Fos）的排名明显上升。请系统分析：这三个样本的 peak 数量、FRiP 和 motif 富集差异是否指向真实的生物学差异而非技术偏差？需要从哪些角度区分生物学信号和技术噪音？",
    # 问题9：实验设计批判——IgG 对照选择
    "该项目选择 HUH6_GNE_140_YD 作为所有三个 IP 样本（HUH6_GNE_140、HUH6_NC、HUH6_Rescue）的 IgG 对照。但 YD 样本本身 Clean Reads 仅 406 万（其他样本的 1/10）、NRF 被评为 Severe、且在 fragment size、MT rate、duplication 等方面均严重异常。请从实验设计角度批判性分析：使用这样一个严重异常的样本作为 IgG 对照是否合理？会对 peak calling（macs3 在 BAMPE 模式下使用 control 估计局部背景 λ）和 FRiP/q-value 计算产生什么具体影响？有没有替代方案？",
    # 问题10：跨样本 FRiP 矩阵解读
    "FRiP_raw.txt 提供了 3×3 的跨样本 FRiP 矩阵——以每个样本的 peaks 分别去计算所有样本的 FRiP。例如以 HUH6_GNE_140 的 peaks 为参考时：HUH6_GNE_140 自身 FRiP=16.40%、HUH6_NC=12.54%、HUH6_Rescue=9.57%；以 HUH6_Rescue 的 peaks 为参考时：HUH6_GNE_140=12.19%、HUH6_NC=10.08%、HUH6_Rescue=7.90%。结合 Spearman 相关性矩阵（IP 间 >0.93），请分析这个跨样本 FRiP 模式揭示了什么关于三个 IP 样本之间生物学相似性和差异性的信息？这种'交叉 FRiP'分析与简单的相关性分析相比有什么额外的生物学洞察力？",
]


def print_separator(title: str) -> None:
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")


async def evaluate_single_question(
    question: str,
    index: int,
    response_service: BusinessResponseService,
) -> dict:
    """对单个问题进行完整评估"""
    print_separator(f"问题 {index+1}: {question}")

    # ── Step 1: 数据结构化分析 ──
    t0 = time.perf_counter()
    analysis_plan = await AnalysisPlannerService.build_plan_with_llm(
        question=question,
        project_id=PROJECT_ID,
    )
    analysis_result = ProjectAnalysisService.analyze(
        project_id=PROJECT_ID,
        question=question,
        project_root=PROJECT_ROOT,
        max_evidence_files=20,
        planning_hints={
            "force_include_html_body": True,
            "analysis_plan": analysis_plan,
            "prioritized_evidence_hints": analysis_plan.get(
                "prioritized_evidence_hints", []
            ),
        },
    )
    analysis_duration = time.perf_counter() - t0
    print(f"  [1/4] 数据结构化分析完成 ({analysis_duration:.1f}s)")
    print(f"        - 识别指标: {analysis_result.get('metric_priority', [])[:8]}")
    print(f"        - 证据条目: {len(analysis_result.get('evidence_chain', []))}")
    print(f"        - 异常发现: {len(analysis_result.get('anomaly_summary', []))}")
    print(f"        - 警告数量: {len(analysis_result.get('warnings', []))}")

    # 打印诊断摘要
    diag = analysis_result.get("diagnosis_summary", {})
    if diag:
        print(f"        - 诊断摘要键: {list(diag.keys())[:10]}")

    # 打印证据链概要
    for i, ev in enumerate(analysis_result.get("evidence_chain", [])[:5]):
        print(
            f"        - 证据[{i}]: {ev.get('metric_key','?')} "
            f"sample={ev.get('sample','?')} "
            f"value={ev.get('display_value','?')} "
            f"severity={ev.get('severity','?')}"
        )

    # ── Step 2: LLM 生成回答 ──
    t0 = time.perf_counter()
    try:
        answer = await response_service.generate_fused_answer(
            analysis_result=analysis_result,
            question=question,
            retrieval_payload={},
            experience_summary={},
        )
    except Exception as exc:
        print(f"  [2/4] LLM 生成失败: {exc}")
        answer = f"[LLM 调用失败] {exc}"
    llm_duration = time.perf_counter() - t0
    print(f"  [2/4] LLM 回答生成完成 ({llm_duration:.1f}s)")
    print(f"        - 回答长度: {len(answer)} 字符")
    print(f"        - 回答预览 (前200字符):")
    print(f"          {answer[:200].replace(chr(10), chr(10)+'          ')}")

    # ── Step 3: 回答质量评估 ──
    t0 = time.perf_counter()
    quality = BusinessAnswerQualityService.evaluate(
        answer=answer,
        analysis_result=analysis_result,
    )
    quality_duration = time.perf_counter() - t0
    print(f"  [3/4] 回答质量评估完成 ({quality_duration:.1f}s)")
    print(f"        - 总分: {quality.get('score', 0)}/100")
    print(f"        - 通过: {'[PASS]' if quality.get('passed') else '[FAIL]'}")
    print(f"        - 各维度得分:")
    for dim_name, dim_val in quality.get("dimensions", {}).items():
        if isinstance(dim_val, dict):
            dim_score = dim_val.get("score", dim_val.get("value", 0))
            dim_max = dim_val.get("max", "?")
            print(f"          {dim_name:30s} {dim_score}/{dim_max}")
        else:
            print(f"          {dim_name:30s} = {dim_val}")
    issues = quality.get("issues", [])
    if issues:
        print(f"        - 质量问题 ({len(issues)} 项):")
        for issue in issues:
            print(
                f"          [{issue.get('severity','?')}] {issue.get('rule','?')}: "
                f"{issue.get('message','?')[:120]}"
            )

    # ── Step 4: 全面评估总结 ──
    print(f"\n  [4/4] ── 综合评估 ──")
    score = quality.get("score", 0)
    if score >= 85:
        print(f"  [PASS] 优秀 ({score}分) - 回答专业、有深度、结构良好")
    elif score >= 70:
        print(f"  [WARN] 合格 ({score}分) - 基本可用但深度不足")
    elif score >= 50:
        print(f"  [FAIL] 浅显 ({score}分) - 回答表面化，缺乏深度分析")
    else:
        print(f"  [FAIL] 差 ({score}分) - 回答质量严重不足")

    return {
        "question": question,
        "answer": answer,
        "answer_length": len(answer),
        "quality_score": score,
        "quality_passed": quality.get("passed"),
        "dimensions": quality.get("dimensions", {}),
        "issues": [i.get("rule", "") for i in issues],
        "analysis_duration_s": round(analysis_duration, 1),
        "llm_duration_s": round(llm_duration, 1),
        "evidence_count": len(analysis_result.get("evidence_chain", [])),
        "anomaly_count": len(analysis_result.get("anomaly_summary", [])),
    }


async def main():
    print_separator("项目分析回答质量深度评估")
    print(f"项目: {PROJECT_ID}")
    print(f"路径: {PROJECT_ROOT}")
    print(f"问题数量: {len(TEST_QUESTIONS)}")

    response_service = BusinessResponseService()

    results = []
    for idx, question in enumerate(TEST_QUESTIONS):
        result = await evaluate_single_question(question, idx, response_service)
        results.append(result)

    # ── 汇总报告 ──
    print_separator("汇总报告")
    total_score = sum(r["quality_score"] for r in results)
    avg_score = total_score / len(results) if results else 0
    passed_count = sum(1 for r in results if r["quality_passed"])

    print(f"\n  平均质量分: {avg_score:.1f}/100")
    print(f"  通过率: {passed_count}/{len(results)}")
    print(f"  总LLM耗时: {sum(r['llm_duration_s'] for r in results):.1f}s")

    print(f"\n  各问题得分:")
    for i, r in enumerate(results):
        status = "[PASS]" if r["quality_passed"] else "[FAIL]"
        print(
            f"  {status} Q{i+1}: {r['quality_score']:.0f}分 "
            f"({r['answer_length']}字符, {r['llm_duration_s']:.1f}s) - "
            f"{r['question'][:60]}..."
        )

    print(f"\n  各维度平均分:")
    dim_totals = {}
    dim_counts = {}
    for r in results:
        for dim, val in r["dimensions"].items():
            if isinstance(val, dict):
                score = val.get("score", val.get("value", 0))
            else:
                score = val
            dim_totals[dim] = dim_totals.get(dim, 0) + score
            dim_counts[dim] = dim_counts.get(dim, 0) + 1
    for dim in sorted(dim_totals.keys()):
        avg = dim_totals[dim] / dim_counts[dim]
        print(f"  {dim:30s} avg={avg:.1f}")

    print(f"\n  高频问题:")
    issue_freq = {}
    for r in results:
        for issue in r["issues"]:
            issue_freq[issue] = issue_freq.get(issue, 0) + 1
    for issue, count in sorted(issue_freq.items(), key=lambda x: -x[1]):
        print(f"  - {issue}: {count}/{len(results)} 次")

    # 保存完整结果
    output_path = Path(__file__).parent / "evaluation_results.json"
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  完整结果已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
