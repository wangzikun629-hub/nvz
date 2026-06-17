"""
真实项目数据 + LLM 回答质量评估脚本（项目3：VZ20260508009 — H3K27ac CUT&Tag + Spike-in）
高质量mm10小鼠项目，含PH/PN两组IP + IgG对照 + spike-in标准化
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

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

# ── 项目3路径 ──────────────────────────────────────────────
PROJECT_ROOT = (
    r"D:\nvz\kefu\multi_agent\backed\app\.project_sftp_cache"
    r"\f9361f715137\VZ20260508009"
)
PROJECT_ID = "VZ20260508009"

# ── 测试问题列表（高质量对照实验 — H3K27ac + Spike-in） ─────
TEST_QUESTIONS = [
    # ═══ 第1组：IgG对照与信号特异性 ═══
    # 问题1：IgG对照的有效性评估
    "这个项目设置了 PH_Igg 作为 IgG 对照。PH_Igg 样本仅 60万 reads（IP样本的约1/40），mapping rate 只有 51%（IP样本 93-95%），但 NRF 高达 0.976、PBC2 高达 76.25。从实验设计角度分析：这个 IgG 对照的测序深度是否足够？NRF 和 PBC2 的极高值说明了什么？mapping rate 仅 51% 是否影响其作为对照的有效性？",

    # 问题2：跨样本FRiP的生物学解读
    "FRiP 矩阵显示了清晰的对比：IP样本自身FRiP为22.24%（PH_H3K27ac）和25.57%（PN_H3K27ac），交叉FRiP（一个IP对另一个IP的peak计算）为20.99%和21.49%，而IP样本在IgG对照上的FRiP仅1.07-1.11%，IgG自身FRiP仅3.13%。请系统解读这个FRiP矩阵：为什么两个IP样本的交叉FRiP接近自身FRiP（~21% vs 22-26%），而IP在IgG上的FRiP仅~1%？这种FRiP模式是否足以证明实验的特异性和重复性？",

    # 问题3：Spearman相关性的多层次解读
    "Spearman相关性矩阵显示：PH_H3K27ac与PN_H3K27ac的相关性为0.9022（高度正相关），而两个IP样本与IgG对照的相关性仅0.3165和0.3200。结合FRiP、peak数量等指标，分析这种相关性差异反映了什么生物学意义？IP间0.90的相关性对H3K27ac这个组蛋白修饰来说是高还是低？IP-IgG间0.32的相关性说明了什么？",

    # ═══ 第2组：Spike-in标准化与定量比较 ═══
    # 问题4：Spike-in的价值与解读
    "项目配置了spike-in分析（spikein_analysis=yes, spikein_align=2）。PH_H3K27ac和PN_H3K27ac的FRiP分别为22.24%和25.57%，peak数量PN更高。如果有spike-in scaling factor，两组之间的H3K27ac信号强度差异可以通过spike-in进行归一化。请解释：(1) CUT&Tag中spike-in标准化的原理（与ChIP-seq中常用的spike-in有何异同？）(2) 如果spike-in归一化后PN_H3K27ac的信号强度仍显著高于PH_H3K27ac，可能反映什么生物学差异？",

    # 问题5：Fragment Size分布的生物学解释
    "三个样本的fragment size分布呈现出相同的模式：sub-nucleosome约60-63%、mono-nucleosome约17-19%、di-nucleosome仅0.6-0.8%，median insert size仅104-113bp。这与典型CUT&Tag数据（sub-nucleosome 48-49%, mono 32-33%）差异很大。请从H3K27ac的生物学特征（活跃增强子/启动子标记，主要分布在开放染色质区域）出发，解释为什么H3K27ac CUT&Tag的fragment分布会以sub-nucleosome为主，以及这种分布模式对抗体特异性的验证意义。",

    # ═══ 第3组：跨组比较与生物学推断 ═══
    # 问题6：PH vs PN 差异的生物学推断
    "两个IP样本明显分为两组：PH_H3K27ac和PN_H3K27ac。两者mapping rate（92.94% vs 94.85%）、unique rate（69.44% vs 71.13%）、MT rate（0.59% vs 0.43%）均非常接近，Spearman相关性0.90，FRiP交叉验证良好。但PN_H3K27ac的自身FRiP（25.57%）高于PH_H3K27ac（22.24%），且trim log显示PH的raw read pairs（1639万）明显多于PN（1282万）。请问：(1) PH和PN可能是怎样的实验设计关系（treatment/control? 不同时间点? 不同组织?）(2) 仅从QC数据和FRiP结果，能否判断两组之间存在真实的H3K27ac信号差异？需要哪些额外信息来做这个判断？",

    # 问题7：Motif分析在高质量数据中的价值
    "HOMER motif分析在PH_H3K27ac和PN_H3K27ac上都给出了结果。在数据质量如此好的情况下（mapping>93%, unique>69%, FRiP>22%, IgG背景<1.1%），motif富集分析的可信度与之前测试的极端低质量项目（MT 67-73%, Unique 12-15%）相比有何不同？从哪些角度验证H3K27ac peaks中富集的motif是真实的转录因子结合位点而非技术偏差？",

    # 问题8：综合项目质量评估
    "请对这个H3K27ac CUT&Tag项目做一个全面的数据质量评估。重点关注：实验设计（PH/PN/IgG三组设计）、QC指标（mapping/unique/MT/NRF/PBC）、信号特异性（FRiP矩阵）、样本重复性（Spearman相关性）、Spike-in标准化和fragment size分布。这个项目的数据质量在CUT&Tag项目中属于什么水平？是否可以直接用于下游差异分析和生物学结论？有哪些需要补充或注意的地方？",
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
    print_separator(f"问题 {index+1}: {question}")

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

    diag = analysis_result.get("diagnosis_summary", {})
    if diag:
        print(f"        - 诊断摘要键: {list(diag.keys())[:10]}")

    for i, ev in enumerate(analysis_result.get("evidence_chain", [])[:5]):
        print(
            f"        - 证据[{i}]: {ev.get('metric_key','?')} "
            f"sample={ev.get('sample','?')} "
            f"value={ev.get('display_value','?')} "
            f"severity={ev.get('severity','?')}"
        )

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
    print_separator("项目3分析回答质量深度评估 (高质量H3K27ac + Spike-in)")
    print(f"项目: {PROJECT_ID}")
    print(f"路径: {PROJECT_ROOT}")
    print(f"问题数量: {len(TEST_QUESTIONS)}")

    response_service = BusinessResponseService()

    results = []
    for idx, question in enumerate(TEST_QUESTIONS):
        result = await evaluate_single_question(question, idx, response_service)
        results.append(result)

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

    output_path = Path(__file__).parent / "evaluation_results_project3.json"
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  完整结果已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
