"""Phase 1.1：代码语义解析 agent。

背景与边界见 docs/project_analysis_agent_upgrade_plan.md 2.1 / 2.2 / 3 节，核心约束复述如下：

1. **只是文件发现层/字段发现层的信息增强来源，不是独立的第四道流程**：这里读取 SOP/workflow
   脚本（`Filter/cutadapt_stat.py`、`Align/align_stat.py`、`CP_rule/*.smk` 等），提取
   "这段代码在计算什么指标、用了哪些变量做分子分母"，产出 `formula_hint`。它只是候选线索，
   不能绕开 `metric_schema_service.normalize()` 的重算比对直接采信，本模块也不做真值判断。
2. **只能在人工预审的 `formula_variants`（Phase 0）里做分类匹配，不能自造新公式**：如果代码
   内容跟所有已知变体都对不上，如实报告 `unknown_variant=True`，交给人工复核队列
   （Phase 1.5，本次未实施），而不是把没人审过的公式当真值使用。
3. **确定性优先，但静态正则的猜测必须经子智能体确认**：先跑静态正则提取（不需要模型即可
   工作，可离线复现）；只要配置了 `CODE_SEMANTICS_MODEL_NAME` 且非离线模式，未命中缓存的
   脚本都会过一次子智能体会话（Stage G-3 二次修订，见下方 4a）——静态正则命中不再是"跳过
   子智能体"的门槛，而是喂给子智能体的"确认优先"先验提示，子智能体必须真正打开脚本看一眼
   才能提交，也可以否决静态正则的猜测。子智能体调用失败/超时一律静默降级（见 4a 的兜底
   语义）。
4. **按脚本文件路径 + mtime 缓存**（复用 `project_parse_cache.py`），同一份 SOP 脚本被多个
   项目复用时不用重复解析——这也是"每个未命中缓存的脚本都过一次子智能体"这个改动能被
   摊薄的原因：真正付这次代价的只有"第一次遇到某个脚本"。
4a. **Stage G-3（2026-07-07-stage-g-explorer-codesemantics-tiered-plan.md 第 305-330 行，
    二次修订）**：原来"静态提取没结果的脚本各自追加一次单轮模型调用"先升级成"一批静态提取
    没结果的脚本共享一次多轮 `Agent + Runner` 会话"，随后又修订为"不管静态正则有没有命中，
    未命中缓存的脚本都过一次会话，静态正则的候选作为确认优先的先验输入"——和 Stage G-2 里
    "启发式匹配从门槛降级为先验输入"（1.4 节）是同一个原则在代码语义解析这一层的对应体现。
    子智能体自己决定先看哪个脚本、看完发现不对要不要换下一个脚本再看。这个会话对等于
    `project_exploration_agent_service.py` 的文件探索 agent，只是工具集换成
    `list_directory`/`read_file_excerpt`/`propose_formula_hint`，且提交仍然只能分类到 Phase 0
    人工预审过的 `formula_variants`，不能自造新变体（约束 2 不变）。会话超时/异常时的兜底
    语义：只给已经拿到确认（不管是提交了候选还是明确看过没提交）的脚本写缓存；被跳过、
    没能真正确认的脚本退回静态正则的候选作为这次响应的信号，但不写缓存，留给下一次同类
    请求重新尝试。旧的单轮 `_model_augment()` 随这次升级一起下线，不保留双路径。
"""
from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import Agent, ModelSettings, Runner, function_tool
from agents.run import RunConfig

from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.infrastructure.tools.local.project_reader import (
    find_internal_workflow_files,
)
from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)
from multi_agent.backed.app.services.project_parse_cache import project_parse_cache

_MAX_SCRIPT_CHARS = 8000
_MAX_SCRIPTS_PER_PROJECT = 6
_MIN_KEYWORD_SCORE = 1
_MIN_CONFIDENCE = 0.3

# Stage G-3（2026-07-07-stage-g-explorer-codesemantics-tiered-plan.md 第 305-330 行）：
# 替换旧的"每脚本一次单轮模型调用 + ThreadPoolExecutor 并发 + 短同步等待/后台续跑两段式
# 预算"整套机制。新设计是"静态提取没结果的一批脚本共享一次多轮 Agent 会话"，并发单元从
# N 个单轮 HTTP 调用降到 1 个多轮会话，会话自身已经有 max_turns 硬上限，因此 v1 采用最简单
# 的"同步阻塞到会话自己的硬超时"（不做后台续跑）：会话如果没在这个时间内跑完，直接放弃
# 剩余轮次、按"这一批脚本本轮未覆盖"处理，未覆盖的脚本不缓存（不能把"没来得及看"固化成
# "确认没有公式"），下一次同类请求可以重新尝试。是否需要后台续跑留给真实调用数据出来后
# 再评估，不在这次改动里照抄旧机制的复杂度。
_CODE_SEMANTICS_AGENT_MAX_TURNS = int(
    os.environ.get("CODE_SEMANTICS_AGENT_MAX_TURNS", "8")
)
# 七次修订（真实日志排查）：原默认 12s 在真实模型延迟下几乎必然超时——同一进程里观测到的
# 单轮 `llm_call`（fused_answer_stream，虽然不是同一个模型/端点，但同一套内网基础设施）
# first_token 就要 18~19s，对一个需要多轮工具调用（每轮都要等模型一次推理）的会话来说，
# 12s 连一个脚本的"读完 + 决定提交"都做不完，更不用说 6 个脚本共享一次会话。日志显示
# 连续两轮请求都是 `status=timeout`，第二轮甚至 `confirmed=6`（6 个脚本全部被判定为
# "已确认"但 `hints=0`）——即所有脚本都被写成"确认没有公式"的空缓存，这正是模块顶部
# 文档写的"未覆盖的脚本不缓存"这条设计承诺被违反的实例（见下面 `_compute_confirmed_
# script_keys` 的 `timed_out` 参数修复）。这里把默认值调大到相对不那么容易触发的量级，
# 仍然可以用环境变量按实际模型延迟调整，不代表这个默认值本身经过了严格测算。
_CODE_SEMANTICS_AGENT_TIMEOUT_SECONDS = float(
    os.environ.get("CODE_SEMANTICS_AGENT_TIMEOUT_SECONDS", "45")
)

# 2026-07-03（真实日志排查，见 docs/project_planner_orchestrator_agent_design.md 讨论）：
# 同一个 project_analysis 请求内，`parse_evidence_parallel` 会并发起多个 worker 线程，
# 如果它们在几乎同一时刻都需要解析同一批未命中缓存的脚本，各自都会独立调用
# `analyze_project_workflow_scripts()`，都看到"缓存未命中"（缓存要等会话真正跑完才回填），
# 于是各自独立起一次代码语义 agent 会话打同一批脚本——对同一个内网模型端点制造了自己打
# 自己的并发风暴。这里加一个非阻塞的 in-flight 标记：某个脚本已经在被别的线程认领处理时，
# 后来者直接跳过、不重复把它纳入本轮会话，立刻按"这一轮没拿到 hint"处理——不能做成阻塞
# 等待，因为 formula_hint 本来就是可选的锦上添花（模块自身设计原则："会话失败/超时一律
# 静默降级为纯静态结果"）。标记按脚本路径的字符串键控，处理完（无论成功失败）都会清除；
# 额外加一个安全上限时间，防止极端情况下（比如进程被杀死导致会话没有走到 finally）标记
# 永久卡死、后续请求永远拿不到这个脚本的 hint。
_IN_FLIGHT_LOCK = threading.Lock()
_IN_FLIGHT_SCRIPTS: dict[str, float] = {}
_IN_FLIGHT_STALE_SECONDS = _CODE_SEMANTICS_AGENT_TIMEOUT_SECONDS + 30.0


def _try_claim_in_flight(script_key: str) -> bool:
    """非阻塞地尝试认领某个脚本的模型解析权。返回 True 表示认领成功，调用方应该
    发起模型请求并在完成后调用 `_release_in_flight`；返回 False 表示已经有其他
    线程在处理，调用方应该跳过、不重复发起请求。"""
    now = time.monotonic()
    with _IN_FLIGHT_LOCK:
        claimed_at = _IN_FLIGHT_SCRIPTS.get(script_key)
        if claimed_at is not None and (now - claimed_at) < _IN_FLIGHT_STALE_SECONDS:
            return False
        _IN_FLIGHT_SCRIPTS[script_key] = now
        return True


def _release_in_flight(script_key: str) -> None:
    with _IN_FLIGHT_LOCK:
        _IN_FLIGHT_SCRIPTS.pop(script_key, None)

# `<name> = <numerator> / <denominator> [* 100]` 这类赋值语句，是 Python/R/Snakemake 里
# 计算比例类指标最常见的写法；这是启发式提取，不追求覆盖所有语言/写法。
_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<var>[A-Za-z_][A-Za-z0-9_]{2,60})\s*=\s*"
    r"(?P<num>[A-Za-z_][A-Za-z0-9_.\[\]'\"]{1,60})\s*/\s*"
    r"(?P<den>[A-Za-z_][A-Za-z0-9_.\[\]'\"]{1,60})"
    r"(?P<pct>\s*\*\s*100)?"
)


@dataclass
class FormulaHint:
    """代码语义解析 agent 的候选产出（线索，非事实）。"""

    script_path: str
    metric_guess: str
    numerator_var: str
    denominator_var: str
    confidence: float
    variant_id: str | None = None
    unknown_variant: bool = True
    discovered_by: str = "code_semantics_static"
    context_line: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "script_path": self.script_path,
            "metric_guess": self.metric_guess,
            "numerator_var": self.numerator_var,
            "denominator_var": self.denominator_var,
            "confidence": round(self.confidence, 4),
            "variant_id": self.variant_id,
            "unknown_variant": self.unknown_variant,
            "discovered_by": self.discovered_by,
            "context_line": self.context_line[:200],
        }


def _is_offline() -> bool:
    return str(os.environ.get("PROJECT_SFTP_OFFLINE", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _code_semantics_client_configured() -> bool:
    """代码语义解析 agent 是否配置了可用模型（懒加载读取，避免这个模块在没有配置
    模型的环境里对 `openai_client` 产生硬依赖）。任何导入失败都视为未配置。

    Stage G-3 三次修订（code review）：主流程判断"这个脚本要不要送进 agent 会话"时
    必须先看这个开关，不能只看 `content and not _is_offline()`——否则模型压根没配置时，
    脚本仍会被送进 `pending_model`、每次调用都白白起一次 `asyncio.run()` 事件循环
    （`_analyze_scripts_with_agent` 内部才会发现未配置、返回"全部未确认"），既浪费一次
    事件循环开销，又会导致这个脚本永远拿不到"确认"，静态候选每次都只能走 fallback
    分支、不能写缓存——退化成每次请求都要重新走一遍这套逻辑。
    """
    try:
        from multi_agent.backed.app.infrastructure.ai.openai_client import (
            CODE_SEMANTICS_CLIENT_CONFIGURED,
        )
    except Exception:  # noqa: BLE001
        return False
    return bool(CODE_SEMANTICS_CLIENT_CONFIGURED)


def _canonical_script_key(path: Path) -> str:
    """统一的脚本 canonical key（`str(path.resolve())`），只用于在内存里匹配"agent 对
    哪个脚本提交/确认了什么"，不用于 `project_parse_cache` 的缓存键（缓存键沿用调用方
    传入的原始 `script_path`，和改造前保持一致，避免这次改动意外改变缓存命中行为）。

    Stage G-3 三次修订（code review）：改动前 `propose_formula_hint_impl` 提交时用的是
    `str(ref.path)`（`ref.path = script_path.resolve()`），但主流程回填时查的是
    `str(script_path)`（原始、未必已经 resolve）。只要 `find_internal_workflow_files()`
    返回的路径不是 100% 规范化形式（相对路径、大小写差异、符号链接等），这两个 key 就会
    对不上，导致 agent 明明提交了候选，回填时却查不到、被误判成"没有 hint"直接写空缓存。
    统一在所有需要匹配的地方都用这个函数计算 key 就不会有这个问题。
    """
    return str(path.resolve())


def _read_script(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return text[:_MAX_SCRIPT_CHARS]


def _normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _keyword_score(candidate: str, tokens: list[str]) -> int:
    normalized_candidate = _normalize_token(candidate)
    score = 0
    for token in tokens:
        normalized_token = _normalize_token(token)
        if normalized_token and normalized_token in normalized_candidate:
            score += 1
    return score


def _guess_metric(var_name: str, context: str) -> str | None:
    """用赋值变量名 + 同段上下文，对照 Phase 0 的 detection_signature/label 猜测目标指标。"""
    best_metric: str | None = None
    best_score = 0
    for metric_id in metric_schema_service.all_metric_ids():
        schema = metric_schema_service.get(metric_id)
        tokens = [str(t) for t in (schema.get("detection_signature") or []) if str(t).strip()]
        label = str(schema.get("label") or "").strip()
        if label:
            tokens.append(label)
        if not tokens:
            continue
        score = _keyword_score(var_name, tokens) * 2 + _keyword_score(context, tokens)
        if score > best_score:
            best_score = score
            best_metric = metric_id
    if best_score < _MIN_KEYWORD_SCORE:
        return None
    return best_metric


def _classify_variant(
    metric_id: str, numerator_var: str, denominator_var: str
) -> tuple[str | None, bool]:
    """在 Phase 0 人工预审的 formula_variants 名单里做分类匹配，不允许自造新变体。

    返回 (variant_id, unknown_variant)。命中即视为"分类任务"，未命中如实报告未知变体。
    """
    schema = metric_schema_service.get(metric_id)
    variants = schema.get("formula_variants") or []
    if not variants:
        return None, True
    normalized_num = _normalize_token(numerator_var)
    normalized_den = _normalize_token(denominator_var)
    for variant in variants:
        num_candidates = [_normalize_token(v) for v in variant.get("numerator_vars") or []]
        den_candidates = [_normalize_token(v) for v in variant.get("denominator_vars") or []]
        num_matched = any(c and (c in normalized_num or normalized_num in c) for c in num_candidates)
        den_matched = any(c and (c in normalized_den or normalized_den in c) for c in den_candidates)
        if num_matched and den_matched:
            return str(variant.get("variant_id") or ""), False
    return None, True


def _static_extract(script_path: Path, content: str) -> list[FormulaHint]:
    hints: list[FormulaHint] = []
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        match = _ASSIGNMENT_PATTERN.search(line)
        if not match:
            continue
        var_name = match.group("var")
        numerator_var = match.group("num")
        denominator_var = match.group("den")
        context_window = "\n".join(lines[max(0, idx - 2): idx + 1])
        metric_guess = _guess_metric(var_name, context_window)
        if not metric_guess:
            continue
        variant_id, unknown_variant = _classify_variant(metric_guess, numerator_var, denominator_var)
        confidence = 0.85 if not unknown_variant else 0.4
        if confidence < _MIN_CONFIDENCE:
            continue
        hints.append(
            FormulaHint(
                script_path=str(script_path),
                metric_guess=metric_guess,
                numerator_var=numerator_var,
                denominator_var=denominator_var,
                confidence=confidence,
                variant_id=variant_id,
                unknown_variant=unknown_variant,
                discovered_by="code_semantics_static",
                context_line=line.strip(),
            )
        )
    return hints


def _analyze_script_cheap(script_path: Path) -> tuple[list[dict[str, Any]] | None, str, list[FormulaHint]]:
    """确定性、不发网络请求的部分：查缓存 + 静态正则提取。

    返回 `(cached_result_or_none, content, static_hints)`。当第一个元素非 None 时，
    表示缓存命中，调用方直接使用，不需要再看后两个字段；否则调用方按需决定是否把这个
    脚本纳入下一批代码语义 agent 会话（见 `_analyze_scripts_with_agent`）。
    """
    cached = project_parse_cache.get_cached_formula_hint(script_path)
    if cached is not None:
        return list(cached.get("hints") or []), "", []
    content = _read_script(script_path)
    static_hints = _static_extract(script_path, content) if content else []
    return None, content, static_hints


def _resolve_target_metrics_for_agent(target_metrics: list[str] | None) -> list[str]:
    """把调用方传入的目标指标归一化成代码语义 agent 本轮的关注范围。

    `target_metrics` 非空且能归一化出至少一个已注册指标时，按这批指标限定 agent 的
    `propose_formula_hint` 白名单；否则（未传 / 传空 / 全部无法归一化）回退到"所有已注册
    且有 `formula_variants` 的指标"——这是旧版 `_model_augment` 的 `known_metrics` 范围，
    保留这个回退是为了不打破 `analyze_project_workflow_scripts` 文档里"`target_metrics`
    不传时行为等同于改造前"的既有兼容承诺。
    """
    if target_metrics:
        canonical = []
        seen: set[str] = set()
        for metric_id in target_metrics:
            canonical_id = metric_schema_service.canonical_id(metric_id)
            if canonical_id and metric_schema_service.get(canonical_id) and canonical_id not in seen:
                seen.add(canonical_id)
                canonical.append(canonical_id)
        if canonical:
            return canonical
    return [
        metric_id
        for metric_id in metric_schema_service.all_metric_ids()
        if metric_schema_service.get(metric_id).get("formula_variants")
    ]


# ---------------------------------------------------------------------------
# Stage G-3：代码语义解析多轮子智能体
#
# 结构上对等于 `project_exploration_agent_service.py` 的文件探索 agent：一批脚本共享
# 一次多轮 `Agent + Runner` 会话，agent 自己决定先看哪个脚本、看完发现不对要不要换下一个
# 脚本再看，最终用 `propose_formula_hint` 逐条提交候选——工具调用轮数和墙钟时间都有硬
# 预算，超预算/异常一律静默降级为空结果，绝不能让这里的失败拖累或中断
# `project_analysis_service.analyze()` 主流程。
# ---------------------------------------------------------------------------


@dataclass
class _ScriptRef:
    """一个待分析脚本在本轮会话里的浏览边界。

    `root` 取脚本自身所在目录（而不是 project_root 或某个全局 commonpath）——同一次
    `find_internal_workflow_files()` 调用返回的脚本可能来自完全不相关的目录树（项目根、
    config 声明的流程脚本目录、每个 workflow 各自的 SFTP 镜像缓存根等，见
    `project_reader._sop_workflow_roots()`），对它们取 `os.path.commonpath` 在最坏情况会
    退化成盘符根甚至跨盘符报错。用"脚本自己的父目录"当边界，天然支持 agent 查看同目录下
    的辅助脚本/被 import 的模块，又不会让浏览范围意外扩大到不相关的目录树。
    """

    alias: str
    root: Path
    entry: str
    path: Path
    # Stage G-3 二次修订：静态正则对这个脚本已经给出的候选（可能为空）。不再是
    # "有候选就跳过 agent"的门槛，而是喂给 agent 的"确认优先"先验输入——和 Stage G-2
    # 里 Explorer 对待 `heuristic_hints` 的处理方式对称：置信度再高也要真正打开脚本看一眼
    # 再提交，静态正则的猜测不能替代 agent 自己的确认。
    static_hints: list["FormulaHint"] = field(default_factory=list)


@dataclass
class _CodeSemanticsContext:
    """一轮代码语义解析会话的运行时状态，通过 ContextVar 传给各个 function_tool。"""

    scripts: dict[str, _ScriptRef]
    target_metrics: list[str]
    proposals: list[dict[str, Any]] = field(default_factory=list)
    # Stage G-3 三次修订（code review）：记录哪些 alias 真正被 `read_file_excerpt` 打开过
    # 自己的脚本本身（不管读到多少）——这是 `propose_formula_hint` 的最低准入门槛：没
    # 打开过就不允许提交，防止照抄静态候选文字直接提交。
    read_aliases: set[str] = field(default_factory=set)
    # Stage G-3 四次修订（code review）：`read_aliases` 只保证"打开过"，不保证"看全了"——
    # 如果 agent 只读了一小段 excerpt（比如默认 offset=0/max_chars=2000）就没再继续，
    # 静态正则命中的公式如果在文件后半段，agent 完全可能没看到就转向下一个脚本，这时
    # "没提交"不能当作"确认没有公式"。只有覆盖了足够内容（见 `_read_file_excerpt_impl`
    # 里的判定：读到了 `_MAX_SCRIPT_CHARS` 全量窗口，或者读到的内容里包含了该脚本静态
    # 候选的 `context_line`）才真正可信，允许把"没有提交"当作"确认为空"写进缓存。
    fully_read_aliases: set[str] = field(default_factory=set)
    # Stage G-3 五次修订（code review）：按 alias 记录这一轮里通过 `read_file_excerpt`
    # 真正读取过的所有文件（不止入口脚本本身，也包括同目录的 helper 文件）。
    # `propose_formula_hint` 允许提交时显式指定 `source_path`（相对于该 alias 的 root），
    # 用来把候选正确归因到"真正包含这段代码的文件"，而不是无条件写死成入口脚本路径——
    # 没有这层记录，agent 打开 helper 文件发现真实公式后提交，会被错误地记成入口脚本
    # 自己在算这个指标。
    read_files: dict[str, set[Path]] = field(default_factory=dict)


_code_semantics_context_var: ContextVar[_CodeSemanticsContext | None] = ContextVar(
    "project_code_semantics_context_var", default=None
)


def _require_code_semantics_context() -> _CodeSemanticsContext:
    ctx = _code_semantics_context_var.get()
    if ctx is None:
        raise RuntimeError("code semantics agent tool called outside of an active analysis round")
    return ctx


def _safe_resolve_in_root(relative_path: str, root: Path) -> Path | None:
    """把模型给出的相对路径解析成某个脚本 `root` 内的绝对路径；越权（../ 逃出该脚本
    所在目录）或路径不存在一律返回 None——每个 alias 各自有独立的 `root`，不允许跨 alias
    访问，这也顺带解决了"多个 root 下可能有同名相对路径"的歧义（工具调用必须先指定
    alias，再在该 alias 自己的 root 下解析相对路径，不存在全局共享的相对路径命名空间）。
    """
    raw = str(relative_path or "").strip().strip("/\\")
    candidate = (root / raw) if raw else root
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def _list_directory_impl(alias: str, path: str, ctx: _CodeSemanticsContext) -> str:
    ref = ctx.scripts.get(alias)
    if ref is None:
        return f"error: unknown alias {alias!r}, use one of: {', '.join(sorted(ctx.scripts))}"
    resolved = _safe_resolve_in_root(path, ref.root)
    if resolved is None or not resolved.exists() or not resolved.is_dir():
        return f"error: path not found or not a directory under alias {alias!r}: {path!r}"
    try:
        entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return f"error: cannot list directory: {exc}"
    dirs = [f"{p.name}/" for p in entries if p.is_dir()]
    files = [p.name for p in entries if p.is_file()]
    return (
        "directories:\n" + ("\n".join(dirs) or "(none)")
        + "\nfiles:\n" + ("\n".join(files) or "(none)")
    )


def _read_file_excerpt_impl(
    alias: str, path: str, offset: int, max_chars: int, ctx: _CodeSemanticsContext
) -> str:
    ref = ctx.scripts.get(alias)
    if ref is None:
        return f"error: unknown alias {alias!r}, use one of: {', '.join(sorted(ctx.scripts))}"
    # 不传 path（或传空串）时默认读取该 alias 对应的脚本本身，这是最常见的用法——
    # 大多数情况下 agent 只需要看这一个脚本，不需要先 list_directory 再拼路径。
    target = str(path or "").strip() or ref.entry
    resolved = _safe_resolve_in_root(target, ref.root)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        return f"error: file not found under alias {alias!r}: {path!r}"
    bounded_chars = max(200, min(int(max_chars or 2000), _MAX_SCRIPT_CHARS))
    try:
        text = resolved.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return f"error: cannot read file: {exc}"
    lines = text.splitlines()
    bounded_offset = max(0, int(offset or 0))
    excerpt = "\n".join(lines[bounded_offset:])[:bounded_chars]

    # Stage G-3 五次修订：不管这次读的是入口脚本还是同目录的 helper 文件，都记一笔——
    # `propose_formula_hint` 的 `source_path` 参数据此校验"你要归因的这个文件，是不是
    # 真的被你读过"，防止 agent 凭空指认一个自己根本没打开过的文件。
    ctx.read_files.setdefault(alias, set()).add(resolved)

    # 只有真正读取了这个 alias 自己的脚本本身（而不是同目录下的其它辅助文件）才算
    # "打开过"——`propose_formula_hint` 会据此拒绝没打开过入口脚本就直接提交的调用
    # （不管这次提交最终归因到入口脚本还是 helper，都至少要先看过入口脚本一眼，见
    # 该函数里 `read_aliases` 的检查）。
    entry_path = _safe_resolve_in_root(ref.entry, ref.root)
    if entry_path is not None and resolved == entry_path:
        ctx.read_aliases.add(alias)
        # Stage G-3 四次修订（code review）：光"打开过"不足以信任"没提交=确认没有公式"——
        # 一次 offset=0/max_chars=2000 的默认读取可能只看到文件前一小段，静态正则命中的
        # 公式如果在后半段，agent 可能压根没看到就转向下一个脚本。只有下面两种情况之一
        # 才算真正"看全了"：(a) 这次读取覆盖了从头到 `_MAX_SCRIPT_CHARS` 的完整窗口
        # （即请求的起点是 0、请求的长度达到了我们允许的最大值，等价于"和静态提取当初
        # 看到的内容一样多"）；(b) 这个脚本有静态正则候选、且这次读到的内容里确实包含了
        # 候选的 `context_line`（说明 agent 已经扫过公式所在的那一行）。满足任一条件，
        # 才允许下游把"这个脚本没有提交"当作"确认为空"写进缓存。
        full_window_covered = bounded_offset == 0 and bounded_chars >= _MAX_SCRIPT_CHARS
        saw_hinted_line = any(
            hint.context_line and hint.context_line in excerpt for hint in ref.static_hints
        )
        if full_window_covered or saw_hinted_line:
            ctx.fully_read_aliases.add(alias)
    return excerpt or "(empty excerpt)"


def _propose_formula_hint_impl(
    alias: str,
    metric_id: str,
    numerator_var: str,
    denominator_var: str,
    confidence: float,
    context_line: str,
    ctx: _CodeSemanticsContext,
    source_path: str = "",
) -> str:
    ref = ctx.scripts.get(alias)
    if ref is None:
        return f"rejected: unknown alias {alias!r}, use one of: {', '.join(sorted(ctx.scripts))}"
    # Stage G-3 三次修订（code review）：不管这个脚本有没有静态正则候选，都必须先真正
    # 用 read_file_excerpt 打开这个 alias 自己的脚本看过一眼才能提交——否则"确认优先"
    # 就只是 prompt 里的软提示，模型完全可以照抄静态候选直接提交，静态正则的猜测就
    # 变相重新变成了事实来源，而不是需要打开确认的先验。
    if alias not in ctx.read_aliases:
        return (
            f"rejected: you must call read_file_excerpt on alias {alias!r} "
            "(its own script) at least once before proposing a formula hint for it — "
            "even if there is already a static-regex candidate for it, you still need "
            "to open and confirm the actual code"
        )
    # Stage G-3 五次修订（code review）：默认把候选归因到这个 alias 自己的入口脚本
    # （`ref.path`），保持向后兼容；但如果真正包含这段代码的是同目录的 helper 文件
    # （agent 通过 read_file_excerpt 的 `path` 参数打开过），必须显式传 `source_path`
    # 指明，不能让候选被默默记成"入口脚本自己在算这个指标"——否则会污染下游对这个
    # 入口脚本的缓存/溯源。`source_path` 必须是这一轮里通过 read_file_excerpt 真正
    # 读取过的文件之一，防止 agent 凭空指认一个自己没打开过的路径。
    attributed_path = ref.path
    if str(source_path or "").strip():
        resolved_source = _safe_resolve_in_root(source_path, ref.root)
        if resolved_source is None:
            return (
                f"rejected: source_path {source_path!r} is outside alias {alias!r}'s "
                "own directory or does not exist"
            )
        if resolved_source not in ctx.read_files.get(alias, set()):
            return (
                f"rejected: you must call read_file_excerpt(alias={alias!r}, "
                f"path={source_path!r}) at least once before attributing a formula "
                "hint to that file"
            )
        attributed_path = resolved_source
    canonical_metric = metric_schema_service.canonical_id(metric_id)
    if canonical_metric not in ctx.target_metrics or not metric_schema_service.get(canonical_metric):
        return (
            f"rejected: {metric_id!r} is not one of this round's registered target "
            "metrics, cannot propose a formula hint for it"
        )
    numerator_var = str(numerator_var or "").strip()
    denominator_var = str(denominator_var or "").strip()
    if not numerator_var or not denominator_var:
        return "rejected: numerator_var/denominator_var cannot be empty"
    # 和旧版 `_model_augment` 一致：agent 声称的分类仍必须在 Phase 0 人工预审名单里真正
    # 对得上，不能只听 agent 自称——分类结果以本地重新校验（`_classify_variant`）为准，
    # 这是约束 2"只能分类到已知变体，不能自造新公式"的具体落点。
    variant_id, unknown_variant = _classify_variant(canonical_metric, numerator_var, denominator_var)
    try:
        supplied_confidence = float(confidence)
    except (TypeError, ValueError):
        supplied_confidence = 0.5
    bounded_confidence = (
        min(0.8, max(0.0, supplied_confidence)) if not unknown_variant else min(0.4, supplied_confidence)
    )
    if bounded_confidence < _MIN_CONFIDENCE:
        return (
            f"rejected: confidence too low after variant classification "
            f"({bounded_confidence:.2f} < {_MIN_CONFIDENCE})"
        )
    ctx.proposals.append(
        {
            # Stage G-3 六次修订（code review）：保留 `alias`——回填缓存时需要按 alias
            # （而不是字面 `script_path`）聚合，否则一个脚本明明通过 `source_path` 归因
            # 到了 helper 文件、入口脚本自己又被判定为"已确认"，会在入口脚本的缓存槽里
            # 写成空列表，把这条本来找到的 helper 线索永久屏蔽到入口脚本 mtime 变化
            # 为止（见 `_analyze_scripts_with_agent` 里 `hints_by_script` 的分组方式）。
            "alias": alias,
            "script_path": str(attributed_path),
            "metric_guess": canonical_metric,
            "numerator_var": numerator_var,
            "denominator_var": denominator_var,
            "confidence": bounded_confidence,
            "variant_id": variant_id,
            "unknown_variant": unknown_variant,
            "context_line": str(context_line or "").strip()[:200],
        }
    )
    return (
        f"recorded: {attributed_path.name} -> {canonical_metric} "
        f"(confidence={bounded_confidence:.2f}, variant={variant_id or 'unknown'})"
    )


@function_tool
async def list_directory(alias: str, path: str = "") -> str:
    """List immediate subdirectories and files under a directory relative to the
    given script `alias`'s own root directory (use "" or "." for that root itself).
    Each alias has its own independent browsing root (its script's parent directory)
    — you cannot use one alias's relative path to reach another alias's files."""
    return _list_directory_impl(alias, path, _require_code_semantics_context())


@function_tool
async def read_file_excerpt(
    alias: str, path: str = "", offset: int = 0, max_chars: int = 4000
) -> str:
    """Read a text excerpt of a file under the given script `alias`. Leave `path`
    empty to read the alias's own script file. Starts at line `offset` (0-based), up
    to `max_chars` characters (capped at 8000). Use this to inspect the actual code
    before deciding whether it computes one of the target metrics, instead of
    guessing from the file name alone; if this script doesn't look relevant, move on
    to another alias instead of giving up entirely. If you decide NOT to submit
    anything for this alias (no formula found), prefer reading with offset=0 and a
    large max_chars (up to 8000) at least once before concluding — a small default
    excerpt may miss a formula further down the file, and the caller only trusts a
    "nothing here" conclusion when it can tell you actually saw the whole file (or
    at minimum, saw the line the static-regex candidate pointed at)."""
    return _read_file_excerpt_impl(alias, path, offset, max_chars, _require_code_semantics_context())


@function_tool
async def propose_formula_hint(
    alias: str,
    metric_id: str,
    numerator_var: str,
    denominator_var: str,
    confidence: float,
    context_line: str = "",
    source_path: str = "",
) -> str:
    """Submit ONE formula hint: this script (identified by `alias`) computes
    `metric_id` using `numerator_var` / `denominator_var`. REQUIRES that you already
    called `read_file_excerpt` on this alias's own script at least once in this
    session — this is enforced and will be rejected otherwise, even if a task-brief
    "static candidate" hint already told you the likely answer. Never submit based on
    the static candidate text alone; you must have actually looked at the code.
    `metric_id` must be one of the already-registered target metric ids given in the
    task brief. The numerator/denominator variable names you report are
    re-classified locally against a pre-reviewed list of known formula variants — you
    cannot invent a new formula variant, you can only report the variable names you
    actually see in the code; if they don't match any known variant, that is reported
    honestly as an unknown variant (still useful as a low-confidence hint), you don't
    need to force a match. `confidence` is your own estimate in [0, 1] of how sure you
    are that this is the right variable pair for this metric. `source_path` is
    OPTIONAL: leave it empty if the formula is actually computed in this alias's own
    entry script (the common case). If the entry script only calls into a helper
    file in the same directory and the REAL calculation lives there instead, set
    `source_path` to that helper file's path (relative to this alias, as you would
    pass it to `read_file_excerpt`) — you must have already called
    `read_file_excerpt(alias, path=source_path)` on it first, otherwise this is
    rejected. Do not silently attribute a helper file's formula to the entry script;
    use `source_path` so the finding is recorded against the file that actually
    contains it. Call this once per (script, metric) finding; you may call it
    multiple times for different metrics or after opening a different script. This
    tool call is the ONLY way your findings reach the caller — any closing text
    summary you write is not read, so do not skip calling this for scripts you
    believe are relevant. If a script turns out irrelevant after reading it, just
    move on without calling this — do not submit a low-confidence guess just to have
    "some output"."""
    return _propose_formula_hint_impl(
        alias,
        metric_id,
        numerator_var,
        denominator_var,
        confidence,
        context_line,
        _require_code_semantics_context(),
        source_path=source_path,
    )


_CODE_SEMANTICS_TOOLS = [list_directory, read_file_excerpt, propose_formula_hint]

_CODE_SEMANTICS_AGENT_INSTRUCTIONS = (
    "你是生物信息学流程脚本的代码语义解析智能体。你的任务是判断给定的一批脚本里，"
    "有没有哪一段代码在计算任务简报里列出的目标指标，并找出它用哪些变量做分子/分母。\n"
    "这批脚本里有些已经有静态正则给出的候选（任务简报里每个 alias 后面会附上，标注了"
    "猜测的指标/变量名/置信度），有些没有——不管哪种情况，你都必须用 read_file_excerpt"
    "真正打开这个 alias 自己的脚本看一遍内容，才能对它调用 propose_formula_hint；这是"
    "强制要求，工具会拒绝没打开就直接提交的调用。静态正则的候选只是先验提示，用来告诉你"
    "'这里可能有答案、值得优先确认'，本身可能是错的或者不完整，不能替代你打开脚本确认这"
    "一步，更不能只凭任务简报里的文字描述就直接照抄提交。\n"
    "工作方式：先看有没有静态候选，有的话优先打开对应脚本确认；没有静态候选的脚本，同样"
    "要打开看内容判断，不能只凭文件名猜测。如果这个脚本看起来和任何目标指标都无关，或者"
    "只是调用了其它模块的函数、真正的计算逻辑不在这个文件里，可以用 list_directory 看看"
    "同目录下有没有更相关的辅助脚本，或者直接换下一个 alias 继续看，不要在一个不相关的"
    "脚本上纠结。如果真正的计算逻辑其实在这样打开的辅助脚本里，提交时要通过"
    "propose_formula_hint 的 source_path 参数指明是那个辅助脚本，不要把它算到入口"
    "脚本头上——入口脚本本身没有算这个指标的话，不能因为它同目录下的另一个文件算了，"
    "就说成是它算的。\n"
    "确认后如果代码里确实在算某个目标指标，必须调用 propose_formula_hint 提交，未提交的"
    "候选不会被采纳（你写的任何结束语总结都不会被读取）。你提交的变量名会被本地重新校验"
    "是否匹配已知公式变体，对不上也可以如实提交（会被标记为未知变体，仍然是有价值的"
    "线索），但不要为了'看起来匹配'而编造变量名——报你在代码里真实看到的名字，静态候选"
    "给出的变量名如果和你实际看到的不一致，以你实际看到的为准。\n"
    "如果打开脚本确认后发现和静态候选描述的不一样、或者这个脚本其实和任何目标指标都无关，"
    "不要提交，直接跳过或换下一个脚本即可，不要为了'有产出'而胡乱提交低置信度的错误候选。"
    "你只负责提名候选，不做最终事实判断——你提交的候选后续还会被公式重算/取值范围等规则"
    "重新校验，这是正常流程，不代表你需要自己保证 100% 正确。"
)

_code_semantics_agent: Agent | None = None


def _get_code_semantics_agent() -> Agent:
    global _code_semantics_agent
    if _code_semantics_agent is None:
        from multi_agent.backed.app.infrastructure.ai.openai_client import (
            code_semantics_agent_model,
        )

        _code_semantics_agent = Agent(
            name="代码语义解析智能体",
            instructions=_CODE_SEMANTICS_AGENT_INSTRUCTIONS,
            model=code_semantics_agent_model,
            model_settings=ModelSettings(temperature=0),
            tools=_CODE_SEMANTICS_TOOLS,
        )
    return _code_semantics_agent


def _build_code_semantics_task_brief(ctx: _CodeSemanticsContext) -> str:
    metric_lines = []
    for metric_id in ctx.target_metrics:
        schema = metric_schema_service.get(metric_id)
        label = schema.get("label", "") if schema else ""
        signature = ", ".join((schema.get("detection_signature") or [])[:6]) if schema else ""
        metric_lines.append(f"- {metric_id} | {label} | aka: {signature}")

    script_lines = []
    for alias in sorted(ctx.scripts):
        ref = ctx.scripts[alias]
        line = f"- alias={alias}: {ref.entry}"
        if ref.static_hints:
            hint_parts = [
                f"{h.metric_guess}（num={h.numerator_var}, den={h.denominator_var}, "
                f"confidence={h.confidence:.2f}, "
                f"{'已知变体' if not h.unknown_variant else '未知变体'}）"
                for h in ref.static_hints[:3]
            ]
            line += (
                "\n  静态正则已经找到候选：" + "；".join(hint_parts)
                + "。建议先用 read_file_excerpt 打开这个脚本确认，内容对得上就直接"
                " propose_formula_hint 提交（可以沿用这里的变量名，也可以修正）；如果打开"
                "确认后发现不对，就不要提交，跳过或换下一个脚本。这只是线索，不是事实，"
                "最终以你在文件里实际看到的内容为准，静态正则的猜测本身可能是错的。"
            )
        script_lines.append(line)

    return (
        "## 目标指标（metric_id | label | aka 同义词）\n"
        + ("\n".join(metric_lines) or "(none)")
        + "\n\n## 待分析脚本（每个脚本一个独立 alias，工具调用必须带上 alias；"
        "同一 alias 下可以用 list_directory/read_file_excerpt 查看同目录的其它辅助脚本，"
        "但不能跨 alias 访问其它脚本所在目录）\n"
        + ("\n".join(script_lines) or "(none)")
        + "\n\n请对每个脚本判断是否计算了上面某个目标指标，逐个调用 propose_formula_hint 提交。"
    )


async def _analyze_scripts_with_agent(
    scripts: list[Path],
    target_metrics: list[str],
    static_hints_by_script: dict[str, list[FormulaHint]] | None = None,
) -> tuple[dict[str, list[FormulaHint]], set[str], bool, dict[str, list[Path]]]:
    """跑一次多轮工具调用，让 agent 在给定脚本集合里自己判断要不要换脚本、能不能提取出
    公式。返回 `(按"入口脚本"canonical key 分组的 FormulaHint 列表, 已确认脚本的
    canonical key 集合, 是否超时/失败提前中断, 按"入口脚本"canonical key 分组的
    依赖文件列表)`。

    七次修订（code review P1）：第四个返回值 `dependencies_by_script`——这一轮里，
    每个入口脚本对应的 alias 真正 `read_file_excerpt` 读过的、除了它自己以外的其它
    文件（典型是同目录 helper）。之前的版本只在 agent 提交了 helper 归因的候选时
    （`hints_by_script` 里有它）才会知道"这个入口脚本依赖某个 helper"——如果 agent
    打开了 helper、判断里面没有目标公式、什么都没提交，这次"确认为空"的结果（通过
    `fully_read_aliases` 判定）会被当作安全地写进入口脚本的缓存，但缓存本身没有
    记录"这个空结果其实依赖 helper 当前的内容"。如果之后 helper 加了新公式、入口
    脚本自己没变，缓存的 mtime key 不会变，空缓存会继续命中，agent 不会重新确认。
    这里直接从 `ctx.read_files`（不只看有没有提交候选）推导依赖，覆盖"读过但没有
    提交"和"读过且提交了"两种场景，调用方写缓存时统一用这份依赖列表。

    六次修订（code review）：返回值第一个元素的分组 key 是"这个候选所属 alias 对应的
    入口脚本"canonical key，不是候选自己 `FormulaHint.script_path` 字面值——如果 agent
    通过 `propose_formula_hint` 的 `source_path` 把候选归因到同目录的 helper 文件，
    `FormulaHint.script_path` 会如实是 helper 的路径，但它仍然会被分到它所属入口脚本
    的那个组里。这是为了让调用方（`analyze_project_workflow_scripts`）能直接把这个
    分组结果原样写进入口脚本的缓存槽——如果按字面 `script_path` 分组，入口脚本自己
    的槽位会查不到任何候选，调用方会把这个"查不到"误判成"确认没有公式"、写空缓存，
    把 helper 里已经找到的候选永久屏蔽掉（见 `_propose_formula_hint_impl` 里
    `alias` 字段的注释）。

    `static_hints_by_script`（按 `_canonical_script_key(原始 script_path)` 索引，可选）：
    静态正则对这批脚本里部分/全部脚本已经给出的候选。静态正则命中不再是"跳过 agent"的
    门槛——agent 对每个脚本都要真正打开看一眼（`read_file_excerpt`）才允许提交，这里只是
    把静态正则的猜测作为"确认优先"的先验提示塞进 task brief（见
    `_build_code_semantics_task_brief`），不代表事实，agent 完全可以在打开脚本后否决它
    （不提交）。

    返回值里的"已确认脚本"集合（第二个元素）是调用方能否信任"这个脚本没有 hint"的唯一
    依据（五次修订：`read_aliases`——只要打开过就算——已经不够，改成看
    `_compute_confirmed_script_keys()` 的判定）：一个脚本要么(a) agent 已经对它提交过
    候选（`hints_by_script` 里有它的 key，提交本身已经过 `read_aliases` 门槛 +
    `_classify_variant` 校验，可信，不管有没有"看全"）、要么(b) `ctx.fully_read_aliases`
    里有记录（agent 真正看全了这个脚本、没有提交，可信地当作"确认没有公式"）——满足
    任一条件才算"已确认"，调用方才可以放心把它的空结果当作"确认没有公式"写缓存。
    只是"打开过、没看全、也没提交"（`read_aliases` 有但 `fully_read_aliases`/
    `hints_by_script` 都没有）的脚本，不管是超时被打断、还是 agent 自己提前结束都没
    轮到，一律不能当作"确认为空"处理（否则会把静态正则本来的命中错误地清空）。

    任何配置缺失/异常都视为"全部未确认"处理（不缓存，静默降级），不向上抛出异常影响
    `project_analysis_service.analyze()` 主流程——和 `explore_with_agent()` 的既有约束
    一致。
    """
    try:
        from multi_agent.backed.app.infrastructure.ai.openai_client import (
            CODE_SEMANTICS_CLIENT_CONFIGURED,
        )
    except Exception:
        return {}, set(), True, {}
    if not CODE_SEMANTICS_CLIENT_CONFIGURED or not scripts:
        return {}, set(), True, {}

    static_hints_by_script = static_hints_by_script or {}
    script_refs: dict[str, _ScriptRef] = {}
    for idx, script_path in enumerate(scripts):
        resolved = script_path.resolve()
        alias = f"s{idx}"
        script_refs[alias] = _ScriptRef(
            alias=alias,
            root=resolved.parent,
            entry=resolved.name,
            path=resolved,
            static_hints=list(static_hints_by_script.get(_canonical_script_key(script_path), [])),
        )

    ctx = _CodeSemanticsContext(scripts=script_refs, target_metrics=target_metrics)
    token = _code_semantics_context_var.set(ctx)
    timed_out = False
    try:
        agent = _get_code_semantics_agent()
        task_brief = _build_code_semantics_task_brief(ctx)
        try:
            await asyncio.wait_for(
                Runner.run(
                    agent,
                    input=task_brief,
                    max_turns=_CODE_SEMANTICS_AGENT_MAX_TURNS,
                    run_config=RunConfig(tracing_disabled=True),
                ),
                timeout=_CODE_SEMANTICS_AGENT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            timed_out = True
            logger.warning(
                "project_code_semantics_agent stage=analyze status=timeout scripts=%d "
                "partial_proposal_count=%d confirmed_count=%d",
                len(scripts),
                len(ctx.proposals),
                len(ctx.read_aliases),
            )
    except Exception as exc:  # noqa: BLE001 - 任何异常都只记录日志，不向上抛出
        logger.warning(
            "project_code_semantics_agent stage=analyze status=failed scripts=%d error=%s",
            len(scripts),
            exc,
            exc_info=True,
        )
        return {}, set(), True, {}
    finally:
        _code_semantics_context_var.reset(token)

    hints_by_script = _group_formula_hints_by_entry_script(ctx, script_refs)
    confirmed_script_keys = _compute_confirmed_script_keys(
        ctx, script_refs, hints_by_script, timed_out=timed_out
    )
    dependencies_by_script = _group_read_dependencies_by_entry_script(ctx, script_refs)
    return hints_by_script, confirmed_script_keys, timed_out, dependencies_by_script


def _group_read_dependencies_by_entry_script(
    ctx: _CodeSemanticsContext, script_refs: dict[str, _ScriptRef]
) -> dict[str, list[Path]]:
    """按入口脚本 canonical key，汇总这一轮里该 alias 真正读过的、除自己以外的文件。

    七次修订（code review P1）：依据 `ctx.read_files`（每次 `read_file_excerpt` 调用
    都会记录，不管最终有没有提交候选），而不是只看 `ctx.proposals`——"读过 helper 但
    判断没有公式、什么都没提交"的场景同样要让调用方知道"这个入口脚本的确认结果依赖
    helper 当前内容"，否则 helper 之后加了公式、入口脚本没变，空缓存不会失效。
    """
    dependencies_by_script: dict[str, list[Path]] = {}
    for alias, ref in script_refs.items():
        read_paths = ctx.read_files.get(alias, set())
        deps = [path for path in read_paths if path != ref.path]
        if deps:
            dependencies_by_script[str(ref.path)] = deps
    return dependencies_by_script


def _group_formula_hints_by_entry_script(
    ctx: _CodeSemanticsContext, script_refs: dict[str, _ScriptRef]
) -> dict[str, list[FormulaHint]]:
    """按 alias 聚合 `ctx.proposals`，返回"入口脚本 canonical key -> FormulaHint 列表"。

    Stage G-3 六次修订（code review）：按 alias 聚合到"这个 alias 对应的入口脚本
    canonical key"下，而不是按 `proposal["script_path"]`（可能是 helper 文件的
    真实路径）分组——`FormulaHint.script_path` 仍然如实保留 helper 的真实路径（不
    影响溯源/展示），只是分组的"槽位"统一用入口脚本的 key。原因：调用方
    （`analyze_project_workflow_scripts`）的缓存是按"入口脚本"（`claimed_scripts`
    里的每一个元素）分槽位写的，如果这里按字面 `script_path` 分组，一个脚本通过
    `source_path` 把候选归因到 helper 文件后，入口脚本自己那个槽位会查不到任何
    候选——如果入口脚本恰好因为被完整读过而"已确认"，调用方会把这个空结果当真、
    写进入口脚本的缓存，helper 里明明找到的这条线索就会被永久屏蔽，直到入口脚本
    的 mtime 变化、缓存失效为止（这不是"不缓存 helper 结果"的性能损失，是实打实
    把已发现的结果误判成"确认没有"）。按 alias 聚合后，入口脚本的缓存槽位会同时
    包含它自己名下 + 它读过的 helper 名下的全部候选，调用方原样整体写入缓存即可。

    七次修订（code review P3）：这段分组逻辑单独抽成函数，让生产代码
    （`_analyze_scripts_with_agent`）和单测（`test_stage_g3_code_semantics_agent.py`）
    共用同一份实现——之前测试手动复刻了这段 for 循环，生产逻辑改动/回退时测试不一定
    会同步失败，保护力偏弱。
    """
    hints_by_script: dict[str, list[FormulaHint]] = {}
    for proposal in ctx.proposals:
        hint = FormulaHint(
            script_path=proposal["script_path"],
            metric_guess=proposal["metric_guess"],
            numerator_var=proposal["numerator_var"],
            denominator_var=proposal["denominator_var"],
            confidence=proposal["confidence"],
            variant_id=proposal.get("variant_id"),
            unknown_variant=proposal.get("unknown_variant", True),
            discovered_by="code_semantics_agent",
            context_line=proposal.get("context_line", ""),
        )
        entry_ref = script_refs.get(proposal.get("alias"))
        # 理论上 `proposal["alias"]` 一定能在 `script_refs` 里找到（alias 是本函数
        # 自己生成、`_propose_formula_hint_impl` 只能引用已存在的 alias）；这里的
        # `or hint.script_path` 只是防御性兜底，不应该真的被触发。
        group_key = str(entry_ref.path) if entry_ref is not None else hint.script_path
        hints_by_script.setdefault(group_key, []).append(hint)
    return hints_by_script


def _compute_confirmed_script_keys(
    ctx: _CodeSemanticsContext,
    script_refs: dict[str, _ScriptRef],
    hints_by_script: dict[str, list[FormulaHint]],
    *,
    timed_out: bool = False,
) -> set[str]:
    """计算这一轮会话里哪些脚本的"没有 hint"是可以信任、可以写进缓存的。

    已确认脚本分两种情况都算数（拆成独立函数是为了不依赖真实 Agent/Runner 调用就能
    单独单测这条判定逻辑，见 `test_stage_g3_code_semantics_agent.py`）：

    1. 已经在 `hints_by_script` 里有提交——提交本身已经过 `read_aliases` 门槛
       （`_propose_formula_hint_impl` 强制要求）+ `_classify_variant` 校验，是否
       "看全了"不重要，这条候选本身就是可信的，必须缓存，不能因为不在
       `fully_read_aliases` 里就被误判成"未确认"退回静态兜底、把 agent 的真实
       确认结果丢掉。
    2. `fully_read_aliases`（不是 `read_aliases`）里记录过、但没有提交——四次修订：
       只有"打开过"（`read_aliases`）不足以信任"没提交=确认为空"，必须是"看全了"
       （见 `_read_file_excerpt_impl` 的判定）才能允许调用方把空结果当真、写进缓存。

    七次修订（真实日志排查 P0）：`timed_out=True` 时，第 2 种情况不再算数——只信任
    `hints_by_script` 里真正提交过的候选。原因：`fully_read_aliases` 是在
    `read_file_excerpt` 这次工具调用本身返回时就同步记下的（见 `_read_file_excerpt_impl`），
    只代表"这次工具调用读到了足够内容"，不代表模型已经看完这份内容、想清楚要不要提交
    ——如果 `asyncio.wait_for` 恰好在模型读完文件、还没来得及决定提交/跳过之前就取消了
    整个会话（这在超时会话里非常常见，真实日志里观测到过一次 timeout 会话里
    `confirmed_count`==脚本总数、但 `hints=0` 的情况——所有脚本都被判定"已确认"但没有
    任何候选），继续把这些"读完但没能表态"的脚本当作"确认没有公式"写进缓存，就正是
    模块顶部文档承诺的"未覆盖的脚本不缓存（不能把'没来得及看'固化成'确认没有公式'）"
    被违反的实例。完整跑完（没有超时）的会话里，"看全了但没提交"仍然可信——这种情况下
    模型确实有机会处理完这份内容再决定不提交，只有"会话被超时打断"这一种场景需要收紧。
    """
    if timed_out:
        return set(hints_by_script.keys())
    fully_read_keys = {
        str(script_refs[alias].path) for alias in ctx.fully_read_aliases if alias in script_refs
    }
    return set(hints_by_script.keys()) | fully_read_keys


def _invoke_code_semantics_agent(
    scripts: list[Path],
    target_metrics: list[str],
    static_hints_by_script: dict[str, list[FormulaHint]] | None = None,
) -> tuple[dict[str, list[FormulaHint]], set[str], bool, dict[str, list[Path]]]:
    """同步桥接入口。调用方契约（必须遵守，否则会抛 `RuntimeError`）：只能从没有正在
    运行的 asyncio 事件循环的线程调用——即普通同步调用链/`ThreadPoolExecutor` 工作线程，
    不能从协程内部直接调用。当前两处调用方（`project_file_discovery_service.
    discover_file_role_assignments()` 经由 `project_analysis_service.py` 的
    `ThreadPoolExecutor(thread_name_prefix="file-discovery")`；
    `project_file_parser_service.parse_evidence_file()` 经由同文件
    `ThreadPoolExecutor(thread_name_prefix="project-evidence")`）都满足这个前提，和
    `explore_with_agent()` 现有的 `asyncio.run()` 用法一致。这里仍兜底捕获
    `RuntimeError`，防止未来误用导致异常向上抛出影响主流程。

    返回值第四个元素 `dependencies_by_script` 见 `_analyze_scripts_with_agent`。
    """
    try:
        return asyncio.run(
            _analyze_scripts_with_agent(scripts, target_metrics, static_hints_by_script)
        )
    except RuntimeError as exc:
        logger.warning(
            "project_code_semantics_agent stage=analyze status=event_loop_conflict error=%s",
            exc,
        )
        return {}, set(), True, {}


def _filter_hints_by_assay(hints: list[dict[str, Any]], assay: str | None) -> list[dict[str, Any]]:
    """按项目 assay 过滤/降级 formula_hint。

    同一个 metric_id 下的变体可能只在人工预审时标注给部分 assay（见
    metric_schema_service._FORMULA_VARIANTS 的 applicable_assays 字段），例如
    CUT&Tag/CUT&RUN 和 ChIP-seq/ATAC-seq 对 frip_ratio 用不同分母口径。这里的
    分类（_classify_variant）本身是 assay 无关的，会拿脚本内容对照该 metric 下
    全部变体做匹配（脚本按路径+mtime 缓存，多个 assay 复用同一份 SOP 脚本时不用
    重复解析）；真正"这条变体是否适用于当前项目"的判断放在这一步做，不写进缓存，
    避免不同 assay 复用同一脚本缓存时互相污染。

    不知道 assay（None/空/未识别）时不做任何过滤，保持原有行为。命中了"其他
    assay 专属"的变体时不是直接丢弃线索，而是降级为 unknown_variant——脚本里确实
    有一段形如公式的赋值语句，只是不能再声称它对应人工预审过的、适用于当前
    assay 的那个变体。
    """
    if not assay:
        return hints
    filtered: list[dict[str, Any]] = []
    for hint in hints:
        variant_id = hint.get("variant_id")
        if hint.get("unknown_variant") or not variant_id:
            filtered.append(hint)
            continue
        schema = metric_schema_service.get(hint.get("metric_guess"))
        variants = schema.get("formula_variants") or []
        variant_def = next((v for v in variants if v.get("variant_id") == variant_id), None)
        applicable = list((variant_def or {}).get("applicable_assays") or ["all"])
        if assay in applicable or "all" in applicable:
            filtered.append(hint)
            continue
        downgraded = dict(hint)
        downgraded["variant_id"] = None
        downgraded["unknown_variant"] = True
        downgraded["confidence"] = min(float(hint.get("confidence") or 0.4), 0.4)
        downgraded["assay_mismatch"] = True
        filtered.append(downgraded)
    return filtered


_METRIC_KEYWORD_STOPWORDS = frozenset(
    {"percent", "ratio", "rate", "total", "count", "status", "score", "value", "fraction"}
)


def _derive_metric_keywords(target_metrics: list[str] | None) -> list[str]:
    """从目标指标 id 推导用于扫描优先级排序的关键词（如
    silva_total_ratio_percent -> ["silva"]）。优先复用
    `metric_schema_service` 已维护的 `detection_signature`（Phase 0 就是为这类
    启发式匹配准备的），metric_id 本身按下划线切分兜底。过滤掉过于宽泛、
    命中面太大的通用词（percent/ratio 等），避免"关键词优先"退化成"到处优先"。
    找不到任何有效关键词时返回空列表——调用方应据此保持改造前的纯字母序行为。
    """
    if not target_metrics:
        return []
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        normalized = token.strip().lower()
        if len(normalized) < 3 or normalized in _METRIC_KEYWORD_STOPWORDS:
            return
        if normalized not in seen:
            seen.add(normalized)
            tokens.append(normalized)

    for metric_id in target_metrics:
        try:
            canonical = metric_schema_service.canonical_id(metric_id)
            schema = metric_schema_service.get(canonical)
        except Exception:  # noqa: BLE001
            canonical = str(metric_id or "").strip().lower()
            schema = {}
        for signature_token in schema.get("detection_signature") or []:
            _add(str(signature_token or ""))
        for part in canonical.split("_"):
            _add(part)
    return tokens


def analyze_project_workflow_scripts(
    project_root: Path,
    *,
    project_config: dict[str, Any] | None = None,
    assay: str | None = None,
    limit: int = _MAX_SCRIPTS_PER_PROJECT,
    target_metrics: list[str] | None = None,
) -> list[dict[str, Any]]:
    """定位项目使用的 SOP/workflow 脚本并提取 formula_hint 列表。

    产出只作为线索，供字段发现层（Phase 1.2，本次未实施）或文件发现层参考，
    不直接进入 fact_packet；调用方仍须经既有 parser + strict_formula_recalculation
    校验才能把任何数值当作正式证据。

    `assay` 建议传 assay_analysis_service 用的同一套词汇
    （cuttag/chipseq/cutrun/atacseq/rnaseq）；不传或传未知值时按"不确定"处理，
    不做 assay 过滤，行为等同于改造前。

    `target_metrics` 传入本次要解答的目标指标 id 列表，用于推导扫描关键词
    （见 `_derive_metric_keywords`），让 config 脚本目录的 SFTP 兜底镜像
    （`find_internal_workflow_files` -> `_sop_workflow_roots` ->
    `_maybe_mirror_config_script_dir` -> `_mirror_remote_script_dir`）按关键词
    优先扫描/下载，而不是纯字母序。不传时行为等同于改造前。
    """
    metric_keywords = _derive_metric_keywords(target_metrics)
    try:
        scripts = find_internal_workflow_files(
            project_root,
            limit=limit,
            project_config=project_config,
            metric_keywords=metric_keywords,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "project_code_semantics stage=find_scripts status=failed root=%s error=%s",
            str(project_root),
            exc,
        )
        return []

    all_hints: list[dict[str, Any]] = []
    # 第一遍：只做确定性、不发网络请求的缓存查询 + 静态正则提取，成本可忽略。
    # Stage G-3 二次修订：静态正则命中不再是"跳过 agent 会话"的门槛——和 Stage G-2 里
    # Explorer 对待启发式命中的处理方式对称（1.4 节"启发式匹配从门槛降级为先验输入"），
    # 静态正则的猜测再像也只是猜测，必须让 agent 真正打开脚本确认一遍才算数。这里收集
    # 两件东西：`pending_model`（本轮需要过一次 agent 会话的脚本，不管静态正则有没有
    # 命中）、`static_hints_by_script`（按 `_canonical_script_key()` 索引，每个脚本静态
    # 正则已经给出的候选，作为"确认优先"的先验提示喂给 agent，也作为 agent 会话超时/
    # 未确认时的兜底）。
    #
    # 三次修订（code review）：`pending_model` 的门槛除了"有内容、非离线"，还要加上
    # "模型确实配置了"——否则模型未配置时脚本仍会被送进 agent 会话，`_analyze_scripts_
    # with_agent` 才发现未配置、白起一次事件循环，还会导致这个脚本永远拿不到"确认"、
    # 每次请求都只能走 fallback、写不进缓存。
    pending_model: list[Path] = []
    static_hints_by_script: dict[str, list[FormulaHint]] = {}
    agent_available = not _is_offline() and _code_semantics_client_configured()
    for script_path in scripts:
        try:
            cached, content, static_hints = _analyze_script_cheap(script_path)
        except Exception:  # noqa: BLE001
            logger.warning(
                "project_code_semantics stage=analyze_script status=failed script=%s",
                str(script_path),
                exc_info=True,
            )
            continue
        if cached is not None:
            all_hints.extend(cached)
            continue
        if static_hints:
            static_hints_by_script[_canonical_script_key(script_path)] = static_hints
        if content and agent_available:
            pending_model.append(script_path)
        else:
            # 离线模式/模型未配置/读不到脚本内容时没有 agent 可用，只能直接采信静态
            # 正则的结果（可能是空列表）——这就是"改造前的行为"，不受这次修订影响。
            result = [h.to_dict() for h in static_hints]
            project_parse_cache.set_cached_formula_hint(script_path, {"hints": result})
            all_hints.extend(result)

    # Stage G-3：第二遍不再是"每脚本一次单轮模型调用、并发发起"，而是"这一批脚本
    # （不管静态正则有没有命中）共享一次多轮 Agent 会话"（见 `_analyze_scripts_with_
    # agent`），agent 自己决定先看哪个脚本、要不要换下一个脚本再看；静态正则的候选只是
    # 拼进 task brief 的"确认优先"提示，agent 打开脚本后可以直接确认提交，也可以否决它。
    #
    # 成本提醒：这个改动意味着每一个未命中缓存的脚本都会触发一次 agent 会话（即使静态
    # 正则已经高置信度猜出公式），相比"静态正则命中就完全跳过 agent"，这是一次实打实的
    # 调用量增加——和 Stage G-2 的 `EXPLORATION_ALWAYS_ON_ENABLED` 面临的是同一类
    # 取舍。这里选择直接生效、不加 feature flag，因为代码语义解析按脚本路径 + mtime 缓存，
    # 同一份 SOP 脚本被多个项目复用时只有"第一次遇到"才会真正付这次代价，之后都命中缓存。
    if pending_model:
        # 先按脚本路径认领 in-flight 标记——已经被同一进程内其他并发调用方（典型场景是
        # parse_evidence_parallel 的多个 worker 线程几乎同时都需要同一批脚本）占用的脚本
        # 直接跳出本轮会话，不重复发起 agent 请求，避免对同一个内网模型端点制造自己打
        # 自己的并发风暴（见上面 `_IN_FLIGHT_*` 的说明）。跳过不等待，按"这一轮没拿到
        # hint"处理，不影响主流程——formula_hint 本来就是可选的，被跳过的脚本如果确实
        # 需要 hint，会在认领方的会话完成、缓存回填后被下一次同类请求命中。
        claimed_scripts: list[Path] = []
        # Stage G-3 四次修订（code review）：`skipped_scripts`（不只是计数）——另一个
        # 线程正在确认这个脚本时，本线程不能直接把它的静态候选丢掉。这批脚本本来就是
        # "静态正则命中也要走 agent 确认"这条新语义下才会进 `pending_model` 的，被
        # in-flight 跳过不代表这个脚本没有信号，只是这次没能拿到 agent 的确认——和
        # claimed 但未被 agent 确认的脚本一样，应该退回静态候选当这次响应的兜底信号，
        # 但不写缓存（不是"确认过"的结果）。
        skipped_scripts: list[Path] = []
        for script_path in pending_model:
            # 四次修订：in-flight key 也统一用 canonical key，避免同一个脚本因为
            # 相对路径/大小写/符号链接等不同表示形式而被当成两个不同脚本，重复触发
            # 并发的 agent 会话。
            if _try_claim_in_flight(_canonical_script_key(script_path)):
                claimed_scripts.append(script_path)
            else:
                skipped_scripts.append(script_path)
        if skipped_scripts:
            logger.info(
                "project_code_semantics stage=code_semantics_agent "
                "status=skipped_in_flight_duplicate root=%s skipped=%d",
                str(project_root),
                len(skipped_scripts),
            )
            for script_path in skipped_scripts:
                fallback_hints = static_hints_by_script.get(_canonical_script_key(script_path), [])
                all_hints.extend(h.to_dict() for h in fallback_hints)

        if claimed_scripts:
            try:
                effective_target_metrics = _resolve_target_metrics_for_agent(target_metrics)
                (
                    hints_by_script,
                    confirmed_script_keys,
                    timed_out,
                    dependencies_by_script,
                ) = _invoke_code_semantics_agent(
                    claimed_scripts, effective_target_metrics, static_hints_by_script
                )
            finally:
                for script_path in claimed_scripts:
                    _release_in_flight(_canonical_script_key(script_path))

            if timed_out:
                logger.info(
                    "project_code_semantics stage=code_semantics_agent "
                    "status=incomplete_round root=%s claimed=%d confirmed=%d",
                    str(project_root),
                    len(claimed_scripts),
                    len(confirmed_script_keys),
                )

            # 六次修订（code review）：`hints_by_script` 现在按"候选所属 alias 对应的
            # 入口脚本"canonical key 分组（见 `_analyze_scripts_with_agent` 内部的
            # 聚合逻辑）——即使某条候选通过 `source_path` 被归因到同目录的 helper 文件
            # （`FormulaHint.script_path` 会如实是 helper 的真实路径，用于溯源/展示），
            # 它在这个字典里仍然挂在它所属入口脚本的 key 下，和 `claimed_scripts` 逐个
            # 匹配不会漏。之前（五次修订前）是按候选自己的字面 `script_path` 分组，会
            # 导致入口脚本自己的槽位查不到任何候选，被误判成"确认没有公式"写空缓存，
            # 把 helper 里已经找到的候选永久屏蔽掉——这是这次要修的回归。
            #
            # 三次修订（code review）：能否信任"这个脚本没有 hint"只看这个脚本的
            # canonical key 是不是在 `confirmed_script_keys` 里——即 agent 是否真的
            # `read_file_excerpt` 过这个脚本本身，而不是看全局的 `timed_out` 是否为
            # False。agent 即便正常跑完整个会话，也完全可能没轮到某几个脚本就提前
            # 结束；这种情况不能当作"确认为空"，否则会把静态正则本来的命中错误地
            # 清空成空缓存。已确认的脚本：agent 明确看过（或者对它/它的 helper 提交过
            # 候选），不管有没有提交都写缓存（没提交就是空列表，表示"确认看过、否决
            # 了"）；缓存内容是这个入口脚本自己 + 它读过的 helper 名下的全部候选并集。
            # 未确认的脚本：退回静态正则的候选作为这次响应的兜底信号，但不写缓存，
            # 留给下一次同类请求让 agent 真正确认一遍。
            for script_path in claimed_scripts:
                script_key = _canonical_script_key(script_path)
                if script_key not in confirmed_script_keys:
                    fallback_hints = static_hints_by_script.get(script_key, [])
                    all_hints.extend(h.to_dict() for h in fallback_hints)
                    continue
                agent_hints = hints_by_script.get(script_key) or []
                result = [h.to_dict() for h in agent_hints]
                # 七次修订（code review P1，取代上一轮只从 agent_hints 推导依赖的做法）：
                # 这条缓存的槽位是入口脚本，但"确认结果"可能依赖同目录的 helper 文件——
                # 不只是"helper 里有 hint"这一种情况，"agent 打开了 helper、判断里面
                # 没有目标公式、什么都没提交"同样依赖 helper 的当前内容（这次的"空"结果
                # 只在 helper 保持现在这样时才成立）。`dependencies_by_script` 来自
                # `ctx.read_files`，覆盖了这两种场景，不会像只看 `agent_hints` 那样
                # 漏掉"读过但没提交"的依赖。
                dependency_paths = dependencies_by_script.get(script_key, [])
                project_parse_cache.set_cached_formula_hint(
                    script_path, {"hints": result}, dependency_paths=dependency_paths
                )
                all_hints.extend(result)

    all_hints = _filter_hints_by_assay(all_hints, assay)
    logger.info(
        "project_code_semantics stage=analyze root=%s scripts=%d hints=%d assay=%s",
        str(project_root),
        len(scripts),
        len(all_hints),
        assay or "unknown",
    )
    return all_hints
