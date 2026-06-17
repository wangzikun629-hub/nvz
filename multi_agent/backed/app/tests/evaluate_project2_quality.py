"""
真实项目数据 + LLM 回答质量评估脚本（项目2：VZ20260513002 极端QC异常）
使用 VZ20260513002（2样本 CUT&Tag, MT rate 67-73%, 负相关, mapping仅57%）测试 DeepSeekV4Pro 回答深度
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

# ── 项目2路径 ──────────────────────────────────────────────
PROJECT_ROOT = (
    r"D:\nvz\kefu\multi_agent\backed\app\.project_sftp_cache"
    r"\ba7f0610952b\VZ20260513002"
)
PROJECT_ID = "VZ20260513002"

# ── 测试问题列表（针对极端QC失败场景） ─────────────────────
TEST_QUESTIONS = [
    # ═══ 第1组：极端异常诊断 ═══
    # 问题1：MT rate 极端异常——线粒体污染根源分析
    "这个项目的两个样本 T1 和 T2 的 Mitochondrial reads 比例分别高达 72.61% 和 66.87%，而 Mapping Rate 分别只有 57.54% 和 57.71%。请从 CUT&Tag 实验技术原理、细胞状态和建库流程三个层面，系统分析为什么会出现如此极端的线粒体 reads 比例，以及为什么高 MT 比例会导致总 Mapping Rate 被压低。如果这两个样本还想挽救用于下游分析，应该采取什么措施？",

    # 问题2：负相关诊断——Spearman -0.63的生物学/技术解释
    "T1 和 T2 两个样本之间的 Spearman 相关系数竟然是 -0.6303。正常情况下 CUT&Tag 重复样本之间应该是高度正相关（>0.9），即使不同处理之间也不应该出现负相关。请从技术噪声、线粒体基因组干扰、文库复杂度差异和潜在生物学差异四个角度，系统分析负相关可能的原因。如果必须判断这两个样本是'生物学差异'还是'技术失败'，你的判断是什么？依据是什么？",

    # 问题3：极端低质量项目的整体诊断
    "请对这个项目的整体数据质量做一个全面评估。重点关注：Mapping Rate 仅 57%、Unique Mapping 仅 12-15%、MT rate 67-73%、NRF 0.67-0.70（Concerning）、Spearman 相关性 -0.63。这个项目的数据是否还有任何下游分析价值？如果没有，请说明从哪些指标可以做出这个判断；如果有，请说明哪些分析还能做、需要什么前提条件。",

    # 问题4：Peak Calling 可行性质疑
    "尽管数据质量极差，macs3 仍然在 T1 中检出了 384 个 peaks（FRiP=14.82%），T2 中检出了 380 个 peaks（FRiP=12.05%）。跨样本 FRiP 显示 T1 peaks 在 T2 上仅 5.43%，T2 peaks 在 T1 上仅 6.48%。请批判性分析：在这种极端低质量数据下（mapping 57%、unique 12-15%、MT 67-73%），macs3 检出的这些 peaks 是否可信？FRiP=14.82% 在这种背景下意味着什么？是否需要这些 peak 结果？",

    # ═══ 第2组：跨项目对比与实验失败模式 ═══
    # 问题5：CUT&Tag 实验失败模式分类
    "将本项目的失败模式与一个'好的但有个别异常样本'的CUT&Tag项目进行对比。假设你有另一个项目（4样本，mapping>97%，unique 67-69%，MT 3-5%，样本间相关性>0.93，仅一个样本因文库起始量不足导致 duplicate 73% 和低 FRiP）。请从实验失败的根本原因层级（实验设计→细胞制备→建库→测序→数据分析）出发，分类和对比这两种失败模式，并说明在项目交付报告中应该如何分别描述这两种情况。",

    # 问题6：Fragment Size 分布解读——sub-nucleosome 主导的含义
    "T1 的 fragment size 分布为 sub-nucleosome 56.8%、mono-nucleosome 34.8%、di-nucleosome 7.7%；T2 为 sub-nucleosome 60.8%、mono-nucleosome 34.1%、di-nucleosome 4.7%。与典型高质量 CUT&Tag 数据（sub-nucleosome 约 48-49%）相比，这两个样本的 sub-nucleosome 比例明显偏高。请从 Tn5 tagmentation 机制和细胞/染色质状态角度，解释 sub-nucleosome 比例异常升高可能反映的技术或生物学问题。这与高 MT 比例和低 Mapping Rate 是否有一致性？",

    # 问题7：Motif 分析在低质量数据中的可靠性
    "尽管数据质量极差，HOMER motif 分析仍然给出了结果——T1 和 T2 的 top motifs 都包括 KLF/Sp1 家族转录因子。考虑到底层数据的严重问题（MT 67-73%，Unique 仅 12-15%，peak 仅 384/380 个），这些 motif 富集结果是否具有任何生物学意义？HOMER 在使用背景序列做统计检验时，极端的数据偏差（如线粒体 reads 主导的背景）会如何影响 motif 富集的假阳性率？",

    # 问题8：如果这是你的项目，你会怎么处理和报告？
    "假设你是这个项目的生物信息学负责人，你需要给实验团队和项目负责人写一份数据质量评估报告。报告中需要包含：(1) 用哪些关键指标判断项目数据不可用 (2) 从哪些实验环节排查失败原因 (3) 是否需要重新建库测序，如果重新做需要哪些实验改进 (4) 当前数据是否还有任何可以保留利用的部分。请以报告的形式组织你的回答。",
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
    print_separator("项目2分析回答质量深度评估 (极端QC失败场景)")
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
    output_path = Path(__file__).parent / "evaluation_results_project2.json"
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  完整结果已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
