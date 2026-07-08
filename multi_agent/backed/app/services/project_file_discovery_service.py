"""Phase 1：文件发现探索 agent 的确定性外壳。

背景与边界见 docs/project_analysis_agent_upgrade_plan.md 第 2 / 3 节，核心约束复述如下：

1. **只在关键词表未命中时触发**：调用方（`project_analysis_service._select_evidence_files`）
   只有在 `QUESTION_FILE_HINTS` / `TARGET_METRIC_FILE_HINTS` / `evidence_catalog` 均未
   命中任何候选文件时才会调用本模块。已调好的项目类型/关键词路径完全不经过这里。
2. **产出永远是候选，不是事实**：`discover_file_role_assignments()` 返回的
   `file_role_assignment` 只是"这个文件可能对应哪个指标"的猜测；是否采信完全由
   下游既有的 parser + `metric_schema_service.normalize()` 重算校验决定，本模块不做
   也不能做真值判断。
3. **确定性优先，模型是可选增强**：先跑纯规则的 `detection_signature` 关键词匹配
   （不需要任何模型即可工作，可离线复现）；只有配置了 `EXPLORATION_MODEL_NAME` 且
   不在离线模式下，才会调用 Stage B 的多轮探索 agent
   （`project_exploration_agent_service.explore_with_agent`）去补充/提高置信度。
   agent 调用失败、超时、任何异常一律静默降级为纯启发式结果，绝不让上层分析流程
   因为这里出错而中断。
   2026-07-03（Stage B）：原来这里是一次单轮分类调用（`_exploration_model_augment`，
   给模型一批候选文件名+表头片段，一次性判断归属），已退役删除，替换为真正的多轮
   工具调用探索（`_exploration_agent_augment`），agent 能自己列目录、开文件、
   决定看不看、要不要换一个候选，而不再是"猜一次就定案"。
4. **按项目根目录 mtime + 目标指标集合缓存**（复用 `project_parse_cache.py`），
   离线 harness 场景下天然只依赖文件 mtime，不受模型输出随机性影响。
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.infrastructure.tools.local.project_reader import (
    ProjectFile,
    list_project_files,
    read_text_snippet,
)
from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)
from multi_agent.backed.app.services.project_parse_cache import project_parse_cache

# 候选文件的启发式探测预算：控制成本，不做全量读取（对应方案 3.1 "便宜探测"）。
_MAX_PROBE_FILES = 60
_MAX_SNIPPET_LINES = 20
_MAX_SNIPPET_CHARS = 2000
# Stage A（project_analysis_exploration_and_evolution_plan.md）：`_heuristic_match`
# 此前把每个指标的候选截断到胜者通吃的前 3 名，是本次真实 bug（VZ20260529017 的
# Silva_total_ratio(%) 排查）的直接成因之一——同目录下 3 个文件名带 "silva" 的原始
# BLAST 明细表，靠文件名命中就能拿到 +0.2 的置信度加成，把唯一真正有效、只能靠
# 内容片段命中（置信度更低）的转置汇总表挤出仅有的 3 个候选位，从未进入解析阶段。
# 这里把"候选生成"和"最终决策"彻底分开：`_heuristic_match` 只负责按分数排序、
# 尽量把所有像样的候选都留下来，交给下游（当前是 parser + strict_formula_
# recalculation 校验，Stage B 上线后是探索 agent）做真正的取舍，不再由这一层的
# 排名截断替下游做决定。30-50 是方案给出的经验区间，具体值需要结合真实项目单个
# 目录下的文件数量做压测校准，目前先取区间中段。
_MAX_CANDIDATES_PER_METRIC = 40
# 同步放宽最终返回给调用方（project_analysis_service._select_evidence_files）的
# 全局候选上限——如果只放大每个指标的候选池、却仍然把跨指标合并去重后的结果砍到
# 原来的 8 个，当同时有多个指标待探索时（比如宽泛的 overview/diagnostic 问题，
# Stage A0 放权后这类问题会带着更多 target_metrics 进来），扩大的候选池一样会在
# 这一步被重新挤掉，等于没有解决问题。20 同样是待压测校准的经验值，不是最终定论；
# 每个候选最终都会被当作真实证据文件读取解析，过大会推高 analyze_project_data
# 的时间预算，需要和 Stage E 的探索耗时/unresolved 比例监控一起校准。
_MAX_TOTAL_CANDIDATES = 20
_MIN_CONFIDENCE = 0.35
# F-1（docs/project_planner_orchestrator_agent_design.md 第 3.4/4 节）：纯启发式匹配
# 达到这个置信度阈值时，认为代码语义解析大概率不会改变结果，跳过按需触发。
_CODE_SEMANTICS_TRIGGER_MAX_CONFIDENCE = 0.6

# 2026-07-01 修订：小文件放宽探测窗口再匹配，避免关键词只出现在文件靠后位置时
# （例如转置格式的主汇总表，指标名排在几十行开外）被前 _MAX_SNIPPET_LINES 行的
# 截断窗口漏掉；只对体积较大的文件保留原截断窗口以控制探测成本，见
# docs/project_analysis_agent_upgrade_plan.md 待办第 2 点。
# 注意：这个窗口值必须偏保守——`_heuristic_match` 现在已按文件路径缓存 snippet（同一批
# 文件不再随 target_metrics 数量重复读盘），但单次 discover 调用仍可能探测 _MAX_PROBE_FILES
# 份文件，窗口开太大会直接推高 `analyze_project_data` 25s 超时预算里这一步的耗时。
_SMALL_FILE_FULL_READ_BYTES = 500_000
_SMALL_FILE_MAX_LINES = 500
_SMALL_FILE_MAX_CHARS = 50_000

# 探索层不读取的文件类型：二进制、图片、以及内部工作流脚本
# （工作流脚本是 Phase 1.1 代码语义解析 agent 的输入，见 project_code_semantics_service.py，
# 这里的文件发现层只处理证据/报告类文件，明确跳过工作流脚本本身避免误用）。
_SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".svg", ".gif", ".bam", ".bai", ".bw", ".bigwig",
    ".gz", ".zip", ".tar", ".py", ".smk", ".sh", ".r", ".pyc",
}


@dataclass
class FileRoleAssignment:
    """文件发现 agent 的候选产出（假设，非事实）。"""

    file_path: str  # 相对项目根目录的路径，使用正斜杠
    candidate_metric_type: str
    confidence: float
    discovered_by: str = "file_discovery"
    matched_signature: list[str] = field(default_factory=list)
    # Stage B-补（project_analysis_exploration_and_evolution_plan.md，2026-07-03）：
    # 探索 agent 的 `propose_evidence` 本来就会提交 sample/source_field/note 这些
    # 字段级线索，这里给它们专门的字段承接，不再像改造前那样只能靠塞进
    # `matched_signature`（`"sample:C1"`/`"field:xxx"` 这种字符串编码）委屈保存。
    # 只有 `discovered_by="file_discovery_exploration_agent"` 的候选才会填这几个
    # 字段；纯启发式候选（`_heuristic_match` 产出）永远是空字符串。
    sample: str = ""
    source_field: str = ""
    note: str = ""
    # Stage B-补 Step 3：agent 如果在读文件时已经看到具体数值并有把握，可以顺手
    # 报出来（`propose_evidence(value=...)`）；只有两条规则化字段发现都失败时，
    # `discover_and_extract` 才会把这个值当第三候选来源去校验，绝不直接采信。
    proposed_value: str = ""
    # Stage G-2（2026-07-07-stage-g-explorer-codesemantics-tiered-plan.md 第 3 节）：
    # `discovered_by` 不能改成复合值——`to_candidate_hints()` 靠精确匹配
    # `"file_discovery_exploration_agent"` 才会保留 sample/source_field/note/
    # proposed_value 这几个字段级线索，改了这个值会导致这些线索被过滤丢失。
    # 这个独立字段表达"这条候选借助了哪些先验信息"，如 ["heuristic_hint"]、
    # ["code_semantics"]，或两者都有；不影响 discovered_by 的既有匹配逻辑。
    context_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "candidate_metric_type": self.candidate_metric_type,
            "confidence": round(self.confidence, 4),
            "discovered_by": self.discovered_by,
            "matched_signature": list(self.matched_signature),
            "sample": self.sample,
            "source_field": self.source_field,
            "note": self.note,
            "proposed_value": self.proposed_value,
            "context_sources": list(self.context_sources),
        }


def _is_offline() -> bool:
    return str(os.environ.get("PROJECT_SFTP_OFFLINE", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _is_probeable(project_file: ProjectFile) -> bool:
    suffix = project_file.path.suffix.lower()
    if suffix in _SKIP_SUFFIXES:
        return False
    try:
        if project_file.path.stat().st_size > 2_000_000:
            return False
    except OSError:
        return False
    return True


def _cheap_snippet(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    max_lines, max_chars = _MAX_SNIPPET_LINES, _MAX_SNIPPET_CHARS
    if size is not None and size <= _SMALL_FILE_FULL_READ_BYTES:
        max_lines, max_chars = _SMALL_FILE_MAX_LINES, _SMALL_FILE_MAX_CHARS
    try:
        return read_text_snippet(path, max_lines=max_lines, max_chars=max_chars)
    except Exception:
        return ""


def _formula_hint_tokens_by_metric(formula_hints: list[dict[str, Any]]) -> dict[str, list[str]]:
    """把 Phase 1.1 代码语义解析 agent 的 formula_hint 转成按指标分组的额外关键词。

    这是 formula_hint 在 Phase 1.2 字段发现层落地前的过渡桥接：Phase 1.2 本次未实施，
    这里先把"代码里明确写了这一列对应哪个变量"当作文件发现层的额外线索使用，命中只是
    加分项，不改变"必须过 strict_formula_recalculation 校验"这条红线。
    """
    tokens_by_metric: dict[str, list[str]] = {}
    for hint in formula_hints or []:
        if not isinstance(hint, dict):
            continue
        metric_id = metric_schema_service.canonical_id(hint.get("metric_guess"))
        if not metric_id:
            continue
        for var in (hint.get("numerator_var"), hint.get("denominator_var")):
            token = str(var or "").strip().lower()
            if token:
                tokens_by_metric.setdefault(metric_id, []).append(token)
    return tokens_by_metric


def _heuristic_match(
    project_files: list[ProjectFile],
    target_metrics: list[str],
    *,
    formula_hints: list[dict[str, Any]] | None = None,
) -> list[FileRoleAssignment]:
    """纯规则匹配：文件名 + 表头/前几行 与指标 detection_signature 的关键词重合度。

    2026-07-01 修复：`target_metrics` 外层循环 × `project_files` 内层循环会对同一个文件
    重复调用 `_cheap_snippet`（对每个指标都重新读一次盘），在小文件探测窗口放宽到整篇读取
    （见 `_SMALL_FILE_*` 常量）之后，这个重复读盘的成本被放大到足以在真实项目里造成
    `analyze_project_data` 25s 超时（见 docs/project_analysis_agent_upgrade_plan.md 待办
    "Silva 修复引入的性能回归"一节）。这里改成按文件路径缓存一次 snippet，同一批
    `project_files` 不管有多少个 target_metrics 都只读一次盘。
    """
    assignments: list[FileRoleAssignment] = []
    hint_tokens_by_metric = _formula_hint_tokens_by_metric(formula_hints or [])
    snippet_cache: dict[Path, str] = {}

    def _snippet_for(project_file: ProjectFile) -> str:
        if project_file.kind not in {"table", "text"}:
            return ""
        cached = snippet_cache.get(project_file.path)
        if cached is None:
            cached = _cheap_snippet(project_file.path).lower()
            snippet_cache[project_file.path] = cached
        return cached

    for metric_id in target_metrics:
        schema = metric_schema_service.get(metric_id)
        signature = [str(token).strip().lower() for token in schema.get("detection_signature") or [] if str(token).strip()]
        label_tokens = [str(schema.get("label") or "").strip().lower()]
        canonical_metric_id = metric_schema_service.canonical_id(metric_id)
        hint_tokens = hint_tokens_by_metric.get(canonical_metric_id, [])
        tokens = [tok for tok in signature + label_tokens if tok]
        if not tokens and not hint_tokens:
            continue
        per_metric_candidates: list[FileRoleAssignment] = []
        for project_file in project_files:
            filename = project_file.path.name.lower()
            snippet = _snippet_for(project_file)
            matched = [tok for tok in tokens if tok in filename or (snippet and tok in snippet)]
            hint_matched = [tok for tok in hint_tokens if tok in filename or (snippet and tok in snippet)]
            if not matched and not hint_matched:
                continue
            filename_hits = sum(1 for tok in matched if tok in filename)
            confidence = min(0.95, 0.35 + 0.2 * filename_hits + 0.1 * (len(matched) - filename_hits))
            if hint_matched:
                # 有 Phase 1.1 代码语义线索佐证的猜测置信度应明显高于纯靠列名瞎猜（方案 3 节）。
                confidence = min(0.95, confidence + 0.15 * len(set(hint_matched))) if matched else max(confidence, 0.6)
            if confidence < _MIN_CONFIDENCE:
                continue
            per_metric_candidates.append(
                FileRoleAssignment(
                    file_path=str(project_file.path),
                    candidate_metric_type=canonical_metric_id,
                    confidence=confidence,
                    discovered_by="file_discovery_heuristic" if not hint_matched else "file_discovery_heuristic+code_semantics",
                    matched_signature=matched + [f"formula_hint:{tok}" for tok in hint_matched],
                )
            )
        per_metric_candidates.sort(key=lambda item: item.confidence, reverse=True)
        assignments.extend(per_metric_candidates[:_MAX_CANDIDATES_PER_METRIC])
    return assignments


def _exploration_agent_augment(
    project_files: list[ProjectFile],
    project_root: Path,
    target_metrics: list[str],
    heuristic_hits: set[str],
    *,
    formula_hints: list[dict[str, Any]] | None = None,
    heuristic_hints: dict[str, dict[str, Any]] | None = None,
) -> list[FileRoleAssignment]:
    """Stage B（project_analysis_exploration_and_evolution_plan.md）：真正的多轮工具
    调用探索。

    Stage G-2（2026-07-07-stage-g-explorer-codesemantics-tiered-plan.md 第 1.4/3 节）
    更新：`settings.EXPLORATION_ALWAYS_ON_ENABLED` 默认 `False` 时维持原逻辑——只对
    `heuristic_hits`（启发式命中的 canonical metric_id 集合）里没有的剩余指标触发；
    开启后不再用启发式命中与否当门槛，对完整 `target_metrics` 触发，改为把
    `heuristic_hits`/`formula_hints` 对应的候选/线索作为"确认优先"的软提示喂给
    `explore_with_agent()` 的 task brief（见该函数与 `_build_task_brief()` 的实现），
    降低采纳成本，但不保证减少调用轮次——`max_turns` 硬上限不变。

    退役说明：这个函数取代了原来的 `_exploration_model_augment`（单轮分类调用，
    给一批候选文件的文件名+表头片段，模型一次性判断归属，不能"打开一个文件发现不对，
    换下一个再看"）。新实现委托给 `project_exploration_agent_service.explore_with_agent`
    ——一个配了 `list_directory`/`read_file_excerpt`/`grep_content`/`propose_evidence`
    四个工具的多轮 Agent，真正具备"看了不对就换一个候选"的反思能力。

    这里仍然只是同步入口（调用方 `discover_file_role_assignments` 是同步函数，且被
    `project_analysis_service._select_evidence_files` 放进独立线程里跑），用
    `asyncio.run()` 桥接到新 agent 的异步实现；任何配置缺失/异常/超时都会被
    `explore_with_agent` 自己吞掉并返回空列表，这里不需要重复兜底。

    2026-07-07：曾经的"统一候选协议 packet"第二返回值（`subagent_candidate_contract`）
    已下线——那套协议从未被 `evidence_card_service`/`fact_packet` 消费，只是重复
    包装同一份 `proposals`，纯粹的日志/trace 用途，收益不足以覆盖维护成本，见
    2026-07-07 架构决策记录。现在恢复成只返回 `assignments`。
    """
    # P1 修复（2026-07-07 code review）：flag 关闭时不仅 `remaining_metrics` 筛选逻辑
    # 要保持不变，喂给 `explore_with_agent()` 的 task brief 内容也必须逐位不变——
    # 之前的写法不管 flag 开关都把 formula_hints/heuristic_hints 传下去，只要
    # code semantics（独立于这个 flag 的既有触发条件）跑出了结果，flag 关闭时的
    # prompt 也会比改动前多一段线索，不满足"默认关闭=行为不变"这条验收要求。
    # 现在两个软提示入参和 remaining_metrics 一样，只在 flag 打开时才生效。
    if settings.EXPLORATION_ALWAYS_ON_ENABLED:
        remaining_metrics = list(target_metrics)
        effective_formula_hints = formula_hints
        effective_heuristic_hints = heuristic_hints
    else:
        remaining_metrics = [
            m for m in target_metrics if metric_schema_service.canonical_id(m) not in heuristic_hits
        ]
        effective_formula_hints = None
        effective_heuristic_hints = None
    if not remaining_metrics:
        return []

    candidates = [pf for pf in project_files if pf.kind in {"table", "text"}][:_MAX_PROBE_FILES]
    if not candidates:
        return []

    formula_hint_metric_ids = {
        metric_schema_service.canonical_id(hint.get("metric_guess"))
        for hint in (effective_formula_hints or [])
        if isinstance(hint, dict) and metric_schema_service.canonical_id(hint.get("metric_guess"))
    }

    try:
        from multi_agent.backed.app.services.project_exploration_agent_service import (
            explore_with_agent,
        )

        proposals = asyncio.run(
            explore_with_agent(
                project_root,
                remaining_metrics,
                [pf.path for pf in candidates],
                formula_hints=effective_formula_hints,
                heuristic_hints=effective_heuristic_hints,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 任何异常都只记录日志，不向上抛出
        logger.warning("project_file_discovery stage=exploration_agent status=failed error=%s", exc, exc_info=True)
        return []

    return _proposals_to_assignments(
        remaining_metrics,
        proposals,
        heuristic_hints=effective_heuristic_hints,
        formula_hint_metric_ids=formula_hint_metric_ids,
    )


def _proposals_to_assignments(
    target_metrics: list[str],
    proposals: list[dict[str, Any]],
    *,
    heuristic_hints: dict[str, dict[str, Any]] | None = None,
    formula_hint_metric_ids: set[str] | None = None,
) -> list[FileRoleAssignment]:
    """把 `explore_with_agent()` 的原始 `proposals` 转成 `FileRoleAssignment` 列表。

    F-3（docs/project_planner_orchestrator_agent_design.md 3.2/4.2）：从
    `_exploration_agent_augment()` 里抽出来的共享转换逻辑，纯函数、无副作用，
    供 `_exploration_agent_augment()`（既有编排路径，行为不变）和 `explore_files()`
    （F-3 显式工具接口）复用，避免两处各自维护一份同样的置信度折扣/字段映射规则。

    Stage G-2 新增 `heuristic_hints`/`formula_hint_metric_ids`：只用来给每条候选打
    `context_sources` 标记（该指标这一轮探索时是否有启发式候选/代码语义线索可用），
    不影响置信度、不影响 `discovered_by`（后者仍必须保持
    `"file_discovery_exploration_agent"` 不变，见 `to_candidate_hints()` 的精确匹配
    约束）。这只是"这一轮有没有先验信息可参考"的记录，不代表 agent 确实采纳了它。
    """
    heuristic_hints = heuristic_hints or {}
    formula_hint_metric_ids = formula_hint_metric_ids or set()
    results: list[FileRoleAssignment] = []
    for item in proposals:
        if not isinstance(item, dict):
            continue
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)):
            continue
        confidence = max(0.0, min(0.9, float(confidence)))  # agent 产出的候选永远打折于人工确认过的启发式命中
        if confidence < _MIN_CONFIDENCE:
            continue
        file_path = str(item.get("file_path") or "").strip()
        metric_id = str(item.get("candidate_metric_type") or "").strip()
        if not file_path or not metric_id:
            continue
        canonical_metric_id = metric_schema_service.canonical_id(metric_id)
        context_sources: list[str] = []
        if canonical_metric_id in heuristic_hints:
            context_sources.append("heuristic_hint")
        if canonical_metric_id in formula_hint_metric_ids:
            context_sources.append("code_semantics")
        results.append(
            FileRoleAssignment(
                file_path=file_path,
                candidate_metric_type=metric_id,
                confidence=confidence,
                discovered_by="file_discovery_exploration_agent",
                sample=str(item.get("sample") or "").strip(),
                source_field=str(item.get("source_field") or "").strip(),
                note=str(item.get("note") or "").strip(),
                proposed_value=str(item.get("value") or "").strip(),
                context_sources=context_sources,
            )
        )
    return results


def explore_files(
    project_root: Path,
    target_metrics: list[str],
    *,
    hint: str | None = None,
    exclude_paths: set[Path] | None = None,
) -> list[dict[str, Any]]:
    """F-3（docs/project_planner_orchestrator_agent_design.md 3.2/4.2）：把探索能力
    显式包装成一个独立、可被 planner 直接调用的工具接口。

    现状（F-4/F-5 之前）：`explore_with_agent()` 只能通过 `discover_file_role_
    assignments()` 内部的 `_exploration_agent_augment()` 间接触发，调用方没法单独
    只要"探索"这一步，必须先理解并触发整套"启发式匹配 → 按需代码语义 → 探索 agent
    → 去重合并"编排。这个函数是同一个底层能力（`explore_with_agent()`）的独立入口，
    不需要先跑一遍启发式匹配、也不参与 `discover_file_role_assignments()` 的候选
    去重/合并/缓存——`discover_file_role_assignments()`/`_exploration_agent_augment()`
    仍然是现有编排路径使用的实现，行为不变；这里只是新增一条"planner 想直接派探索
    agent"的显式路径，两者共享同一份 `_proposals_to_assignments()` 转换逻辑，不
    重复实现置信度折扣/字段映射规则。

    返回 `assignments`：`file_role_assignment` 字典列表（`to_dict()` 后的格式，和
    `discover_file_role_assignments()` 的候选格式一致，方便调用方复用
    `to_candidate_paths()`/`to_candidate_hints()`）。没有触发探索（离线模式/无目标
    指标/无候选文件）或探索本身失败时为空列表。

    2026-07-07：曾经的第二返回值"统一候选协议 packet"已下线，见
    `_exploration_agent_augment()` 同批次改动说明。

    `hint`：可选的自然语言提示，当前实现暂不使用，为后续规划 agent（F-6，未在本次
    范围内）传递额外上下文预留占位，不影响现有调用行为。
    """
    del hint  # 占位参数，见函数文档字符串；当前 explore_with_agent() 不接受它。
    if _is_offline():
        return []

    target_metrics = [str(m or "").strip() for m in target_metrics if str(m or "").strip()]
    if not target_metrics:
        return []

    try:
        all_files = list_project_files(project_root, limit=400)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "project_file_discovery stage=explore_files status=list_files_failed root=%s error=%s",
            str(project_root),
            exc,
        )
        return []

    exclude = {p.resolve() for p in (exclude_paths or set())}
    candidates = [
        pf for pf in all_files
        if _is_probeable(pf) and pf.path.resolve() not in exclude and pf.kind in {"table", "text"}
    ][:_MAX_PROBE_FILES]
    if not candidates:
        return []

    try:
        from multi_agent.backed.app.services.project_exploration_agent_service import (
            explore_with_agent,
        )

        proposals = asyncio.run(
            explore_with_agent(project_root, target_metrics, [pf.path for pf in candidates])
        )
    except Exception as exc:  # noqa: BLE001 - 任何异常都只记录日志，不向上抛出
        logger.warning(
            "project_file_discovery stage=explore_files status=failed root=%s error=%s",
            str(project_root),
            exc,
            exc_info=True,
        )
        return []

    assignments = _proposals_to_assignments(target_metrics, proposals)
    return [a.to_dict() for a in assignments]


def discover_file_role_assignments(
    project_root: Path,
    target_metrics: list[str],
    *,
    exclude_paths: set[Path] | None = None,
    max_candidates: int = _MAX_TOTAL_CANDIDATES,
    project_config: dict[str, Any] | None = None,
    force_code_semantics: bool = False,
) -> list[dict[str, Any]]:
    """在关键词表未命中时，探索候选 file_role_assignment。

    返回值是候选证据文件的字典列表（含 `file_path` 绝对路径字符串），调用方把
    它们当作普通证据文件路径喂给现有 parser；是否真正生成 `evidence_card` 完全
    取决于下游既有的 `strict_formula_recalculation` 等校验，本函数不做真值判断。

    Stage D 修订（project_analysis_exploration_and_evolution_plan.md）：本函数不再
    在探索完成后自动写入缓存——它没有下游解析/校验结果的可见性，无法判断这批候选
    是否真的产出了 evidence_card。缓存的读取仍在这里做（见 `get_cached_file_
    discovery` 的"已确认成功/短 TTL 失败"两段式语义），但写入职责交给明确知道结果
    的调用方，调用完成后应该调用 `project_parse_cache.record_file_discovery_
    outcome(project_root, target_metrics, result, success=...)` 回填真实结果。

    `project_config`（2026-07-03 真实项目排查补充）：项目 config.yaml 解析后的摘要
    字典，透传给 `analyze_project_workflow_scripts()` 用于读取 `scripts`/
    `pipeline_dir` 等 key（不同 assay 的流程脚本目录，例如 RNA-seq 项目会声明
    `scripts: /mnt/data/Pipeline/...`）。调用方不传时（None）该功能静默降级为
    "只在 SOP 仓库里找"，不影响原有行为——发现问题前，两处调用方一直都没传这个
    参数，`_sop_workflow_roots` 里读 config 的那段代码实际上从未真正生效过。

    2026-07-07：曾经的 `return_candidate_packets` 开关（Phase 3 统一候选协议）
    已下线——那套协议只用于日志/trace，从未被 `evidence_card_service`/
    `fact_packet` 消费，见 2026-07-07 架构决策记录。本函数恢复成只返回
    `list[dict[str, Any]]`。

    `force_code_semantics`（Phase 5，2026-07-06-fact-packet-first-refactor-plan.md，
    docs/project_planner_orchestrator_agent_design.md F-4）：默认 `False` 时行为
    完全不变——是否触发 `analyze_project_workflow_scripts()` 仍然只看
    `_CODE_SEMANTICS_TRIGGER_MAX_CONFIDENCE` 这条既有启发式规则。只有
    planner-orchestrator 在重探索的后续轮次里，认为"纯文件层规则命中已经给过
    机会、这个指标仍未解决，值得直接花代价看代码语义"时，才会显式传 `True`，
    跳过置信度判断、无条件触发代码语义解析——调用决策收归 planner，而不是让
    这个函数自己的内部启发式在每一轮都重新算一遍同样的结论。这个参数只影响
    "要不要跑代码语义"，不影响探索 agent（`_exploration_agent_augment`）是否
    运行——二者不是互斥的两个工具，探索 agent 该不该跑仍由它自己现有的
    `_is_offline()` 判断决定，不受这个参数影响。
    """
    target_metrics = [str(m or "").strip() for m in target_metrics if str(m or "").strip()]
    if not target_metrics:
        return []

    cached = project_parse_cache.get_cached_file_discovery(project_root, target_metrics)
    if cached is not None:
        return cached

    try:
        all_files = list_project_files(project_root, limit=400)
    except Exception as exc:  # noqa: BLE001
        logger.warning("project_file_discovery stage=list_files status=failed error=%s", exc)
        return []

    exclude = {p.resolve() for p in (exclude_paths or set())}
    probeable = [
        pf for pf in all_files
        if _is_probeable(pf) and pf.path.resolve() not in exclude
    ][:_MAX_PROBE_FILES]

    # F-1（docs/project_planner_orchestrator_agent_design.md 第 3.4/4 节）：
    # `analyze_project_workflow_scripts()` 曾经无条件调用——不管纯文件名/表头关键词
    # 匹配是否已经给出了足够可信的候选，都先跑一遍代码语义解析（可能触发模型调用），
    # 白白消耗预算。这里改成先跑一次不带 formula_hint 的启发式匹配，只有目标指标里
    # 有任何一个"最高置信度低于阈值（含完全没候选）"时，才认为纯启发式大概率不够，
    # 才去调代码语义解析、把 formula_hint 喂回去重新匹配一次；heuristic_match 本身是
    # 纯内存正则匹配（唯一的磁盘开销是 `_cheap_snippet` 读文件片段，且按路径缓存），
    # 多跑一次的成本远低于无条件触发代码语义解析（可能是模型调用）的成本。
    #
    # 阈值取 0.6，和 `_heuristic_match` 内部"有 formula_hint 佐证时置信度至少提到
    # 0.6"的既有逻辑对齐（见下方 `_heuristic_match` 里 `max(confidence, 0.6)`）——
    # 也就是说，如果纯启发式已经能达到"即使有代码语义佐证也不会更高"的置信度，
    # 代码语义解析大概率不会改变最终排序结果，值得跳过。
    heuristic_assignments = _heuristic_match(probeable, target_metrics)
    canonical_targets = [metric_schema_service.canonical_id(m) for m in target_metrics]
    best_confidence_by_metric: dict[str, float] = {}
    for assignment in heuristic_assignments:
        current = best_confidence_by_metric.get(assignment.candidate_metric_type, 0.0)
        if assignment.confidence > current:
            best_confidence_by_metric[assignment.candidate_metric_type] = assignment.confidence
    needs_code_semantics = force_code_semantics or any(
        best_confidence_by_metric.get(metric_id, 0.0) < _CODE_SEMANTICS_TRIGGER_MAX_CONFIDENCE
        for metric_id in canonical_targets
    )

    # Stage G-2（2026-07-07-stage-g-explorer-codesemantics-tiered-plan.md 第 3 节
    # 评审修正）：这里必须在 `if needs_code_semantics:` 分支之外初始化，否则
    # `needs_code_semantics=False` 时下面把 `formula_hints` 传给
    # `_exploration_agent_augment()` 会引用不到这个名字/两个分支写出不一致的行为。
    formula_hints: list[dict[str, Any]] = []
    if needs_code_semantics:
        # Phase 1.1（代码语义解析 agent）的 formula_hint 作为过渡桥接线索，见
        # _formula_hint_tokens_by_metric() 说明；解析失败不能影响文件发现主流程。
        try:
            from multi_agent.backed.app.services.project_code_semantics_service import (
                analyze_project_workflow_scripts,
            )

            formula_hints = analyze_project_workflow_scripts(
                project_root, project_config=project_config, target_metrics=target_metrics
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "project_file_discovery stage=code_semantics_hint status=failed root=%s",
                str(project_root),
                exc_info=True,
            )

        if formula_hints:
            heuristic_assignments = _heuristic_match(probeable, target_metrics, formula_hints=formula_hints)

    heuristic_hits = {a.candidate_metric_type for a in heuristic_assignments}
    # Stage G-2：按 canonical metric_id 索引、每个 metric 只留置信度最高一条，喂给
    # 探索 agent 的 task brief 做"确认优先"提示。`assignment.candidate_metric_type`
    # 在 `_heuristic_match()` 里创建时就已经是 canonical_id（见该函数实现），这里
    # 不需要再转一次。
    heuristic_hints_by_metric: dict[str, dict[str, Any]] = {}
    for assignment in heuristic_assignments:
        existing = heuristic_hints_by_metric.get(assignment.candidate_metric_type)
        if existing is None or assignment.confidence > existing.get("confidence", 0.0):
            heuristic_hints_by_metric[assignment.candidate_metric_type] = {
                "file_path": assignment.file_path,
                "confidence": assignment.confidence,
            }

    agent_assignments: list[FileRoleAssignment] = []
    if not _is_offline():
        agent_assignments = _exploration_agent_augment(
            probeable,
            project_root,
            target_metrics,
            heuristic_hits,
            formula_hints=formula_hints,
            heuristic_hints=heuristic_hints_by_metric,
        )

    combined = heuristic_assignments + agent_assignments
    combined.sort(key=lambda item: item.confidence, reverse=True)

    seen_paths: set[str] = set()
    deduped: list[FileRoleAssignment] = []
    for assignment in combined:
        if assignment.file_path in seen_paths:
            continue
        seen_paths.add(assignment.file_path)
        deduped.append(assignment)
        if len(deduped) >= max_candidates:
            break

    result = [a.to_dict() for a in deduped]
    logger.info(
        "project_file_discovery stage=discover root=%s target_metrics=%s heuristic=%d agent=%d final=%d offline=%s",
        str(project_root),
        target_metrics,
        len(heuristic_assignments),
        len(agent_assignments),
        len(result),
        _is_offline(),
    )
    return result


def to_candidate_paths(assignments: list[dict[str, Any]]) -> list[Path]:
    """把 file_role_assignment 转成 Path 列表，供调用方并入既有证据文件候选。"""
    paths: list[Path] = []
    for item in assignments:
        file_path = str(item.get("file_path") or "").strip()
        if not file_path:
            continue
        try:
            paths.append(Path(file_path))
        except (TypeError, ValueError):
            continue
    return paths


def to_candidate_hints(assignments: list[dict[str, Any]]) -> dict[Path, dict[str, Any]]:
    """把 file_role_assignment 里的字段级线索保留下来，按解析后的绝对路径建索引。

    Stage B-补（project_analysis_exploration_and_evolution_plan.md）：`to_candidate_
    paths()` 只取 `file_path`，`sample`/`source_field`/`note`/`confidence`/
    `candidate_metric_type` 全部被丢弃——这个函数不改动 `to_candidate_paths()`
    本身（避免影响已依赖它返回类型的调用点），并列提供一份"文件路径 -> 线索"的
    映射，供调用方（目前是 `_reexplore_unresolved_metrics`）在解析对应文件时，把
    线索一并传给 `parse_evidence_file`/`discover_and_extract`。

    只有 `discovered_by="file_discovery_exploration_agent"` 且至少有
    `sample`/`source_field` 之一非空的候选才会出现在返回值里——纯启发式候选没有
    这些字段，不需要占一条空线索。同一个文件路径出现多条候选时，保留置信度最高
    的一条（调用方只需要"这个文件最可能的字段线索"，不需要处理多条冲突建议）。
    """
    hints: dict[Path, dict[str, Any]] = {}
    for item in assignments:
        if item.get("discovered_by") != "file_discovery_exploration_agent":
            continue
        sample = str(item.get("sample") or "").strip()
        source_field = str(item.get("source_field") or "").strip()
        if not sample and not source_field:
            continue
        file_path = str(item.get("file_path") or "").strip()
        if not file_path:
            continue
        try:
            resolved = Path(file_path).resolve()
        except (TypeError, ValueError):
            continue
        candidate_hint = {
            "sample": sample,
            "source_field": source_field,
            "note": str(item.get("note") or "").strip(),
            "confidence": item.get("confidence"),
            "candidate_metric_type": str(item.get("candidate_metric_type") or "").strip(),
            "value": str(item.get("proposed_value") or "").strip(),
        }
        existing = hints.get(resolved)
        if existing is None:
            hints[resolved] = candidate_hint
            continue
        try:
            existing_confidence = float(existing.get("confidence") or 0.0)
            new_confidence = float(candidate_hint.get("confidence") or 0.0)
        except (TypeError, ValueError):
            continue
        if new_confidence > existing_confidence:
            hints[resolved] = candidate_hint
    return hints
