"""Stage B（project_analysis_exploration_and_evolution_plan.md 第一部分）：
把文件发现 + 字段发现合并成一个真正的多轮工具调用探索 agent。

背景：`project_file_discovery_service._exploration_model_augment`（Stage B 之前的
"模型增强"分支）本质仍是一次单轮分类调用——把候选文件的文件名+表头片段一次性喂给
模型，模型一次性判断哪个文件对应哪个指标，不能"打开一个文件发现不对，换下一个候选
再看"。这个模块提供真正的多轮探索能力：agent 自己决定 `list_directory` 列哪个目录、
`read_file_excerpt` 开哪个文件、`grep_content` 搜什么关键词，最终用 `propose_evidence`
逐个提交候选——工具调用轮数和墙钟时间都有硬预算，超预算/异常一律静默降级为空列表，
绝不能让这里的失败拖累或中断 `project_analysis_service.analyze()` 主流程。

关键约束（与文件发现层其余部分一致）：
1. 产出永远是候选，不是事实——是否采信完全由下游既有的 parser +
   `metric_schema_service.normalize()` 重算校验决定，这里不做也不能做真值判断。
2. `propose_evidence` 只接受已在 `metric_schema_service` 注册的 metric_id，
   agent 不能凭空发明新指标（与 Stage A0 的纪律一致）。
3. 模型未配置（`EXPLORATION_CLIENT_CONFIGURED` 为假）、离线模式、任何异常或超时，
   一律返回空列表，调用方据此退回纯启发式结果。
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import Agent, ModelSettings, Runner, function_tool
from agents.run import RunConfig

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)

# 探索预算：轮数和墙钟时间双重限制，任一超出就放弃剩余探索、返回已收集到的候选。
# 2026-07-03 真实模型回放排查：manual_stage_b_diagnose.py 证实 deepseek-v4-flash
# 本身响应快（裸调用 ~1s，最简单的单工具 Agents SDK 调用 ~2.6s），但
# manual_stage_b_replay.py 用真实的 4 工具 + 4 候选文件任务跑，12s 内 0 个候选都
# 没提交出来——说明不是模型/代理慢，是真实探索任务（更长的 instructions + 候选文件
# 清单 + 可能的多次 read_file_excerpt/grep_content + 多次 propose_evidence 往返）
# 本身轮数更多，12s 对多轮探索确实偏紧，调大到 25s 再验证。
_DEFAULT_MAX_TURNS = 8
# F-0（docs/project_planner_orchestrator_agent_design.md 第 1.5/4 节）：这里曾经硬编码
# 25.0s，与文件发现子阶段的硬预算（`settings.effective_file_discovery_budget_seconds`）
# 互不知情——两者一度分别是 25.0 与 30.0（旧默认），后者看似给前者留了余量，但外层
# `PROJECT_ANALYSIS_TIMEOUT_SECONDS` 总预算只有 25.0，`effective_file_discovery_
# budget_seconds` 被 clamp 到 20.0 后，25.0s 的 agent 超时反而比它所在的子阶段预算
# 还长——agent 还没来得及自行收尾就会被外层 ThreadPoolExecutor 直接抛弃，连部分候选
# 都拿不到。现在改为读 `settings.effective_exploration_agent_timeout_seconds`
# （= 文件发现子预算 - 安全边际），保证这里的超时永远严格小于调用方给的预算。
_DEFAULT_AGENT_TIMEOUT_SECONDS = settings.effective_exploration_agent_timeout_seconds
_MAX_LIST_ENTRIES = 200
_MAX_EXCERPT_CHARS = 4000
_MAX_GREP_MATCHES = 20
_MAX_GREP_FILES = 60
_MAX_CANDIDATE_FILES_IN_BRIEF = 60
# Stage G-2（2026-07-07-stage-g-explorer-codesemantics-tiered-plan.md 第 1.4/3 节）：
# formula_hints 拼进 task brief 时的字段白名单和每指标数量上限，避免代码语义解析的
# 原始线索（可能很长/含无关字段）无限制塞进 prompt。
_MAX_FORMULA_HINTS_PER_METRIC = 3
_FORMULA_HINT_ALLOWED_FIELDS = (
    "metric_guess",
    "numerator_var",
    "denominator_var",
    "script_path",
    "context_line",
    "confidence",
)


@dataclass
class _ExplorationContext:
    """一轮探索的运行时状态，通过 ContextVar 传给各个 function_tool。

    不通过 function_tool 的显式参数传项目路径，是为了不让模型在工具调用里自己拼
    project_root（容易拼错/越权），只允许模型传相对路径，真正的路径拼接和越权检查
    都在这里做（见 `_safe_resolve`），和 `agent_factory.py` 里 ContextVar 传请求上下文
    是同一个约定。
    """

    project_root: Path
    candidate_files: list[Path]
    target_metrics: list[str]
    proposals: list[dict[str, Any]] = field(default_factory=list)
    # Stage G-2 新增：代码语义解析线索（原样透传，metric_guess 未必已归一化，
    # 拼接时在 `_format_formula_hints` 里做 canonical_id 归一化再过滤）。
    formula_hints: list[dict[str, Any]] = field(default_factory=list)
    # Stage G-2 新增：按 canonical metric_id 索引的启发式最高置信度候选，取自
    # `discover_file_role_assignments()` 里已经算好的 `heuristic_assignments`，
    # 不在这里重新计算。只用于 task brief 的"确认优先"提示，不影响是否调用探索
    # agent（Stage G-2 之后是否调用只看 `EXPLORATION_ALWAYS_ON_ENABLED`）。
    heuristic_hints: dict[str, dict[str, Any]] = field(default_factory=dict)


_exploration_context_var: ContextVar[_ExplorationContext | None] = ContextVar(
    "project_exploration_context_var", default=None
)


def _require_context() -> _ExplorationContext:
    ctx = _exploration_context_var.get()
    if ctx is None:
        raise RuntimeError("exploration agent tool called outside of an active exploration round")
    return ctx


def _safe_resolve(relative_path: str, ctx: _ExplorationContext) -> Path | None:
    """把模型给出的相对路径解析成项目根目录内的绝对路径；越权（../ 逃出根目录）
    或路径不存在一律返回 None，工具函数据此拒绝并把原因回传给模型。"""
    raw = str(relative_path or "").strip().strip("/\\")
    candidate = (ctx.project_root / raw) if raw else ctx.project_root
    try:
        resolved = candidate.resolve()
        resolved.relative_to(ctx.project_root)
    except (OSError, ValueError):
        return None
    return resolved


# 每个工具的真正逻辑都写成一个接受显式 `ctx` 参数的纯函数（`_*_impl`），
# `@function_tool` 包装的版本只负责从 ContextVar 取 ctx 再委托过去。这样拆分
# 纯粹是为了可测试性：`agents` SDK 的 `@function_tool` 会把函数包装成一个
# `FunctionTool` 对象，不再能被当作普通函数直接调用/单测，`_*_impl` 保留了
# 可以脱离 Agent/Runner、脱离真实模型调用直接单测的入口。


def _list_directory_impl(path: str, ctx: _ExplorationContext) -> str:
    resolved = _safe_resolve(path, ctx)
    if resolved is None or not resolved.exists() or not resolved.is_dir():
        return f"error: path not found or not a directory: {path!r}"
    try:
        entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return f"error: cannot list directory: {exc}"
    dirs = [f"{p.name}/" for p in entries if p.is_dir()][:_MAX_LIST_ENTRIES]
    files = [p.name for p in entries if p.is_file()][:_MAX_LIST_ENTRIES]
    return (
        "directories:\n" + ("\n".join(dirs) or "(none)")
        + "\nfiles:\n" + ("\n".join(files) or "(none)")
    )


def _read_file_excerpt_impl(path: str, offset: int, max_chars: int, ctx: _ExplorationContext) -> str:
    resolved = _safe_resolve(path, ctx)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        return f"error: file not found: {path!r}"
    bounded_chars = max(200, min(int(max_chars or 2000), _MAX_EXCERPT_CHARS))
    try:
        text = resolved.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return f"error: cannot read file: {exc}"
    lines = text.splitlines()
    bounded_offset = max(0, int(offset or 0))
    excerpt = "\n".join(lines[bounded_offset:])[:bounded_chars]
    return excerpt or "(empty excerpt)"


def _grep_content_impl(pattern: str, ctx: _ExplorationContext) -> str:
    needle = str(pattern or "").strip().lower()
    if not needle:
        return "error: empty pattern"
    matches: list[str] = []
    for file_path in ctx.candidate_files[:_MAX_GREP_FILES]:
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            if needle in line.lower():
                try:
                    rel = str(file_path.relative_to(ctx.project_root)).replace("\\", "/")
                except ValueError:
                    rel = str(file_path)
                matches.append(f"{rel}: {line.strip()[:200]}")
                break
        if len(matches) >= _MAX_GREP_MATCHES:
            break
    return "\n".join(matches) if matches else "(no matches)"


def _propose_evidence_impl(
    file_path: str,
    metric_id: str,
    confidence: float,
    sample: str,
    source_field: str,
    note: str,
    ctx: _ExplorationContext,
    value: str = "",
) -> str:
    resolved = _safe_resolve(file_path, ctx)
    if resolved is None or not resolved.is_file():
        return f"rejected: file not found or outside project root: {file_path!r}"
    canonical_metric = metric_schema_service.canonical_id(metric_id)
    if canonical_metric not in ctx.target_metrics or not metric_schema_service.get(canonical_metric):
        return (
            f"rejected: {metric_id!r} is not one of this round's registered target "
            "metrics, cannot propose evidence for it"
        )
    try:
        bounded_confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        bounded_confidence = 0.5
    # Stage B-补（project_analysis_exploration_and_evolution_plan.md）：`value` 是
    # 可选的——只有 agent 真的从 read_file_excerpt/grep_content 看到了具体数值、
    # 有把握直接报出来时才填；不确定就留空，调用方（discover_and_extract）只在
    # 两条规则化字段发现都失败时才会尝试用这个值当候选，而且仍然要过
    # metric_schema_service.normalize() 重算/范围校验，绝不直接采信。
    ctx.proposals.append(
        {
            "file_path": str(resolved),
            "candidate_metric_type": canonical_metric,
            "confidence": bounded_confidence,
            "sample": str(sample or "").strip(),
            "source_field": str(source_field or "").strip(),
            "note": str(note or "").strip()[:200],
            "value": str(value or "").strip()[:50],
        }
    )
    return f"recorded: {resolved.name} -> {canonical_metric} (confidence={bounded_confidence:.2f})"


@function_tool
async def list_directory(path: str) -> str:
    """List immediate subdirectories and files under a project-relative directory
    path (use "" or "." for the project root). Only paths inside the current
    project root are allowed; anything else is rejected."""
    return _list_directory_impl(path, _require_context())


@function_tool
async def read_file_excerpt(path: str, offset: int = 0, max_chars: int = 2000) -> str:
    """Read a text excerpt of a project file (project-relative path), starting at
    line `offset` (0-based), up to `max_chars` characters (capped at 4000). Use this
    to inspect a candidate file's header/content before deciding whether it actually
    contains a target metric, instead of guessing from the file name alone."""
    return _read_file_excerpt_impl(path, offset, max_chars, _require_context())


@function_tool
async def grep_content(pattern: str) -> str:
    """Search for a literal substring (case-insensitive) across this round's
    candidate evidence files, returning matching file paths with one short context
    line each. Use this to quickly find which candidate file(s) actually contain a
    target metric's column header/name, instead of opening every candidate one by
    one — especially useful when several files share a similar/colliding file name
    but only one of them actually has the metric's data."""
    return _grep_content_impl(pattern, _require_context())


@function_tool
async def propose_evidence(
    file_path: str,
    metric_id: str,
    confidence: float,
    sample: str = "",
    source_field: str = "",
    note: str = "",
    value: str = "",
) -> str:
    """Submit ONE candidate evidence file for a target metric. `metric_id` must be
    one of the already-registered target metric ids given in the task brief — you
    cannot invent a new metric_id here (unregistered metrics have no verification
    contract downstream, so a proposal for one is always rejected). `confidence` is
    your own estimate in [0, 1] of how likely this file truly contains that metric's
    data (not just a name collision). `value` is OPTIONAL: only fill it in if you
    actually saw the specific number for this sample/field while reading the file
    (via read_file_excerpt/grep_content) and are confident about it — leave it empty
    if you are only guessing the file/field is relevant without having read the
    actual number. A filled-in `value` is never trusted directly: the caller always
    re-validates it against the metric's own recomputation/range rules before using
    it, and only falls back to it when the caller's own deterministic field-guessing
    already failed on this file — so there is no harm in reporting an uncertain
    number as long as you are honest that you saw it. Call this once per candidate
    file; you may call it multiple times for different files or metrics. This tool
    call is the ONLY way your findings reach the caller — any closing text summary
    you write is not read, so do not skip calling this for files you believe are
    correct."""
    return _propose_evidence_impl(
        file_path, metric_id, confidence, sample, source_field, note, _require_context(), value=value
    )


_EXPLORATION_TOOLS = [list_directory, read_file_excerpt, grep_content, propose_evidence]

_EXPLORATION_AGENT_INSTRUCTIONS = (
    "你是生物信息学项目的证据文件探索智能体。你的任务是在一个项目目录里找出真正包含"
    "目标指标数值的证据文件，而不是仅凭文件名猜测。\n"
    "工作方式：可以用 list_directory 浏览目录、read_file_excerpt 打开文件看内容、"
    "grep_content 在候选文件里搜索关键词；打开一个文件发现内容不对，应该换下一个候选"
    "再看，而不是只看第一个文件名像的就下结论。\n"
    "文件名撞词是常见陷阱：同一批目录里可能有多个文件名相似甚至完全一致的文件，"
    "但只有其中一个真正包含目标指标那一行/那一列的数据，其余可能是同名但内容完全"
    "无关的原始明细表——必须打开看内容再判断，不能只凭文件名或目录名判断。\n"
    "找到候选后，必须调用 propose_evidence 提交，未提交的候选不会被采纳（你写的"
    "任何结束语总结都不会被读取）。如果排查后发现都不匹配，什么都不提交即可，不要"
    "为了'有产出'而胡乱提交低置信度的错误候选。\n"
    "你只负责提名候选，不做最终事实判断——你提交的候选后续会被公式重算/取值范围等"
    "规则重新校验，校验不通过会被丢弃，这是正常流程，不代表你需要自己保证 100% 正确。\n"
    "如果你在读文件内容时已经看到了某个样本对应的具体数值，并且有把握，可以顺手用"
    "propose_evidence 的 value 参数报出来；没把握或者没细看具体数字就留空，不要编造。"
    "这个值只在调用方自己的规则化字段识别都失败时才会被考虑，而且同样要过校验，所以"
    "报一个你自己不确定的数字并不会造成风险，只是可能没被采用。"
)

_exploration_agent: Agent | None = None


def _get_exploration_agent() -> Agent:
    global _exploration_agent
    if _exploration_agent is None:
        from multi_agent.backed.app.infrastructure.ai.openai_client import (
            exploration_agent_model,
        )

        _exploration_agent = Agent(
            name="文件发现探索智能体",
            instructions=_EXPLORATION_AGENT_INSTRUCTIONS,
            model=exploration_agent_model,
            model_settings=ModelSettings(temperature=0),
            tools=_EXPLORATION_TOOLS,
        )
    return _exploration_agent


def _format_formula_hints(target_metrics: list[str], formula_hints: list[dict[str, Any]]) -> str:
    """把代码语义解析线索按目标指标分组、白名单字段过滤、每指标截断到

    `_MAX_FORMULA_HINTS_PER_METRIC` 条后拼成一段 prompt 文本；没有可用线索时返回
    空字符串（调用方据此决定是否拼接这一段）。

    `hint["metric_guess"]` 是代码语义解析猜出来的原始字符串，不保证已经是
    canonical id（见 `project_code_semantics_service.py::_guess_metric()`），
    这里和已经 canonical 的 `target_metrics` 比较前必须先做 `canonical_id()`
    归一化，否则会因为大小写/别名不一致漏检（Stage G 计划三次评审修订(3)）。
    """
    if not formula_hints:
        return ""
    target_set = set(target_metrics)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for hint in formula_hints:
        if not isinstance(hint, dict):
            continue
        metric_id = metric_schema_service.canonical_id(hint.get("metric_guess"))
        if not metric_id or metric_id not in target_set:
            continue
        grouped.setdefault(metric_id, []).append(hint)
    if not grouped:
        return ""

    lines: list[str] = []
    for metric_id in target_metrics:
        hints_for_metric = grouped.get(metric_id)
        if not hints_for_metric:
            continue
        hints_sorted = sorted(
            hints_for_metric, key=lambda h: h.get("confidence", 0.0) or 0.0, reverse=True
        )[:_MAX_FORMULA_HINTS_PER_METRIC]
        for hint in hints_sorted:
            parts = [
                f"{field_name}={hint[field_name]}"
                for field_name in _FORMULA_HINT_ALLOWED_FIELDS
                if hint.get(field_name) not in (None, "")
            ]
            if parts:
                lines.append(f"- {metric_id}: " + ", ".join(parts))
    if not lines:
        return ""
    return (
        "\n\n## 代码语义解析线索（仅供参考，不代表最终事实，最终以你在文件里实际"
        "看到的内容为准）\n" + "\n".join(lines)
    )


def _build_task_brief(ctx: _ExplorationContext) -> str:
    metric_lines = []
    for metric_id in ctx.target_metrics:
        schema = metric_schema_service.get(metric_id)
        label = schema.get("label", "") if schema else ""
        signature = ", ".join((schema.get("detection_signature") or [])[:6]) if schema else ""
        line = f"- {metric_id} | {label} | aka: {signature}"
        hint = ctx.heuristic_hints.get(metric_id)
        if hint:
            hint_path = hint.get("file_path", "")
            hint_confidence = hint.get("confidence", "")
            line += (
                f"\n  启发式已找到候选：{hint_path}（置信度 {hint_confidence}），建议先用"
                " read_file_excerpt 打开确认，内容对得上就直接 propose_evidence 提交，不需要"
                "再列目录搜索；如果确认后发现不对，再展开正常搜索。这是线索，不是事实，最终"
                "以你在文件里实际看到的内容为准。"
            )
        metric_lines.append(line)

    candidate_lines = []
    for path in ctx.candidate_files[:_MAX_CANDIDATE_FILES_IN_BRIEF]:
        try:
            rel = str(path.relative_to(ctx.project_root)).replace("\\", "/")
        except ValueError:
            rel = str(path)
        candidate_lines.append(f"- {rel}")

    formula_hint_section = _format_formula_hints(ctx.target_metrics, ctx.formula_hints)

    return (
        "## 待查找的目标指标（metric_id | label | aka 同义词）\n"
        + ("\n".join(metric_lines) or "(none)")
        + "\n\n## 已知候选证据文件（项目根目录相对路径，仅供参考，不代表这就是正确答案；"
        "你可以用 list_directory 探索候选清单之外的目录）\n"
        + ("\n".join(candidate_lines) or "(none)")
        + formula_hint_section
        + "\n\n请对每个目标指标找出真正包含其数值的文件，逐个调用 propose_evidence 提交。"
    )


async def explore_with_agent(
    project_root: Path,
    target_metrics: list[str],
    candidate_files: list[Path],
    *,
    max_turns: int = _DEFAULT_MAX_TURNS,
    timeout_seconds: float = _DEFAULT_AGENT_TIMEOUT_SECONDS,
    formula_hints: list[dict[str, Any]] | None = None,
    heuristic_hints: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """跑一轮多轮工具调用探索，返回候选列表（每项是 propose_evidence 提交的字典）。

    任何配置缺失/异常/超时都返回空列表，调用方（project_file_discovery_service）
    据此退回纯启发式结果，绝不向上抛出异常影响 project_analysis 主流程。

    `formula_hints`/`heuristic_hints`（Stage G-2 新增，均为可选参数，不传时行为与
    改动前完全一致）：分别是代码语义解析线索、按 metric_id 索引的启发式最高置信度
    候选，只用于拼进 task brief 作为"确认优先"的软提示，不影响是否调用探索 agent
    本身、也不改变终止条件——是否采信仍然只由下游 `metric_schema_service.normalize()`
    等既有校验层决定。
    """
    try:
        from multi_agent.backed.app.infrastructure.ai.openai_client import (
            EXPLORATION_CLIENT_CONFIGURED,
        )
    except Exception:
        return []

    canonical_targets = [
        metric_schema_service.canonical_id(m) for m in target_metrics if str(m or "").strip()
    ]
    canonical_targets = [m for m in canonical_targets if metric_schema_service.get(m)]
    if not EXPLORATION_CLIENT_CONFIGURED or not canonical_targets or not candidate_files:
        return []

    # 防御性归一化：调用方（project_file_discovery_service）已经保证
    # heuristic_hints 的 key 是 canonical（`candidate_metric_type` 在生成时就是
    # canonical_id），这里再做一次是为了不让这个函数的正确性依赖调用方的隐式约定。
    normalized_heuristic_hints: dict[str, dict[str, Any]] = {}
    for metric_id, hint in (heuristic_hints or {}).items():
        canonical_metric_id = metric_schema_service.canonical_id(metric_id)
        if canonical_metric_id:
            normalized_heuristic_hints[canonical_metric_id] = hint

    resolved_root = project_root.resolve()
    ctx = _ExplorationContext(
        project_root=resolved_root,
        candidate_files=[p.resolve() for p in candidate_files],
        target_metrics=canonical_targets,
        formula_hints=list(formula_hints or []),
        heuristic_hints=normalized_heuristic_hints,
    )
    token = _exploration_context_var.set(ctx)
    try:
        agent = _get_exploration_agent()
        task_brief = _build_task_brief(ctx)
        try:
            await asyncio.wait_for(
                Runner.run(
                    agent,
                    input=task_brief,
                    max_turns=max_turns,
                    run_config=RunConfig(tracing_disabled=True),
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "project_exploration_agent stage=explore status=timeout root=%s "
                "target_metrics=%s partial_proposal_count=%d",
                str(project_root),
                canonical_targets,
                len(ctx.proposals),
            )
        return list(ctx.proposals)
    except Exception as exc:  # noqa: BLE001 - 任何异常都只记录日志，不向上抛出
        logger.warning(
            "project_exploration_agent stage=explore status=failed root=%s error=%s",
            str(project_root),
            exc,
            exc_info=True,
        )
        return []
    finally:
        _exploration_context_var.reset(token)
