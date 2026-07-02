"""Phase 1.1：代码语义解析 agent。

背景与边界见 docs/project_analysis_agent_upgrade_plan.md 2.1 / 2.2 / 3 节，核心约束复述如下：

1. **只是文件发现层/字段发现层的信息增强来源，不是独立的第四道流程**：这里读取 SOP/workflow
   脚本（`Filter/cutadapt_stat.py`、`Align/align_stat.py`、`CP_rule/*.smk` 等），提取
   "这段代码在计算什么指标、用了哪些变量做分子分母"，产出 `formula_hint`。它只是候选线索，
   不能绕开 `metric_schema_service.normalize()` 的重算比对直接采信，本模块也不做真值判断。
2. **只能在人工预审的 `formula_variants`（Phase 0）里做分类匹配，不能自造新公式**：如果代码
   内容跟所有已知变体都对不上，如实报告 `unknown_variant=True`，交给人工复核队列
   （Phase 1.5，本次未实施），而不是把没人审过的公式当真值使用。
3. **确定性优先**：先跑静态正则提取（不需要模型即可工作，可离线复现）；只有配置了
   `CODE_SEMANTICS_MODEL_NAME` 且非离线模式，才对静态提取完全没结果的脚本追加一次模型调用。
   模型调用失败/超时/返回非 JSON 一律静默降级为纯静态结果。
4. **按脚本文件路径 + mtime 缓存**（复用 `project_parse_cache.py`），同一份 SOP 脚本被多个
   项目复用时不用重复解析。
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

# 2026-07-02 修订：`analyze_project_workflow_scripts` 原来对最多 6 个脚本顺序调用
# `_analyze_script`，静态提取没结果的脚本每个都会追加一次模型调用（`_model_augment`
# 单次 timeout=25s），顺序执行最坏情况 6 * 25s = 150s，远超调用方
# （project_analysis_service._select_evidence_files）能给这一步的预算，是实测
# `analyze_project_data` 25s 超时的主因之一（见 docs/project_analysis_agent_upgrade_plan.md
# 待办第 3 点后续修复记录）。这里改成并发跑 + 统一硬预算：预算耗尽后不再等待还没
# 返回的模型调用，只取已经完成的结果，静默降级（不影响主流程，符合方案"模型是可选
# 增强"的既有约束）。
_MODEL_AUGMENT_BUDGET_SECONDS = float(
    os.environ.get("CODE_SEMANTICS_MODEL_BUDGET_SECONDS", "10")
)
_MODEL_AUGMENT_MAX_WORKERS = 4

# project_analysis_phase1.5_auto_promotion_revision.md §13：模型增强分支移出同步请求路径。
# `_MODEL_AUGMENT_BUDGET_SECONDS`（默认 10s）曾经是"愿意为这批脚本等待多久"，但那仍然是
# 同步阻塞——一次慢的模型调用照样能吃掉调用方（analyze_project_data）的整体延迟预算。
# 现在拆成两段：`_MODEL_AUGMENT_SYNC_WAIT_SECONDS`（默认很短，本次请求最多顺带等这么久，
# 等到算这次响应的"意外惊喜"）+ 后台线程池继续跑到 `_MODEL_AUGMENT_BUDGET_SECONDS`，通过
# `future.add_done_callback` 在真正完成时把结果写进缓存——不管主线程有没有等到，缓存都会
# 被写入，下一次同类请求（同一脚本 hash）直接命中缓存，不用再调模型。这就是方案原文
# "模型结果留给下一次同类提问用"的具体实现。
_MODEL_AUGMENT_SYNC_WAIT_SECONDS = float(
    os.environ.get("CODE_SEMANTICS_MODEL_SYNC_WAIT_SECONDS", "1.5")
)

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


def _model_augment(script_path: Path, content: str) -> list[FormulaHint]:
    """静态提取完全没结果时，可选调用代码语义解析模型做一次辅助提取。

    模型的产出同样只能"分类到已知变体"，不能自造新公式；任何失败都静默降级为空列表。
    """
    try:
        from multi_agent.backed.app.infrastructure.ai.openai_client import (
            CODE_SEMANTICS_CLIENT_CONFIGURED,
            CODE_SEMANTICS_MODEL_NAME,
            code_semantics_model_client,
        )
    except Exception:
        return []
    if not CODE_SEMANTICS_CLIENT_CONFIGURED:
        return []

    known_metrics = [
        {
            "metric_id": metric_id,
            "label": metric_schema_service.get(metric_id).get("label", ""),
            "formula_variants": metric_schema_service.get(metric_id).get("formula_variants", []),
        }
        for metric_id in metric_schema_service.all_metric_ids()
        if metric_schema_service.get(metric_id).get("formula_variants")
    ]
    prompt = (
        "你是生物信息学流程脚本分析器。给定一段脚本内容和一批已知指标的公式变体定义，"
        "找出脚本里计算这些指标用到的变量名（分子/分母）。"
        "只能从给定的 formula_variants 里选择最匹配的 variant_id；如果都对不上，"
        "variant_id 填 null，不要发明新公式。只输出严格 JSON："
        '{"hints": [{"metric_id": "...", "numerator_var": "...", "denominator_var": "...", '
        '"variant_id": "..." or null, "confidence": 0.0-1.0}]}，不要输出解释文字。\n'
        f"已知指标定义：{json.dumps(known_metrics, ensure_ascii=False)[:3000]}\n"
        f"脚本内容（{script_path.name}）：\n{content[:4000]}"
    )
    try:
        response = code_semantics_model_client.chat.completions.create(
            model=CODE_SEMANTICS_MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            # 2026-07-02 从 25s 调低到与 _MODEL_AUGMENT_BUDGET_SECONDS 同数量级：
            # 调用方现在并发发起、共享一个批次总预算（默认 10s），单次请求超时设得
            # 比批次预算还长没有意义，只会让个别慢请求在后台空跑更久。
            timeout=10,
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
        payload = json.loads(raw)
        raw_hints = payload.get("hints") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "project_code_semantics stage=model_augment script=%s status=failed error=%s",
            str(script_path),
            exc,
        )
        return []

    known_ids = {m["metric_id"] for m in known_metrics}
    results: list[FormulaHint] = []
    for item in raw_hints:
        if not isinstance(item, dict):
            continue
        metric_id = metric_schema_service.canonical_id(item.get("metric_id"))
        if metric_id not in known_ids:
            continue
        numerator_var = str(item.get("numerator_var") or "").strip()
        denominator_var = str(item.get("denominator_var") or "").strip()
        if not numerator_var or not denominator_var:
            continue
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.5
        # 模型声称的 variant_id 仍必须在人工预审名单里真正对得上，不能只听模型自称——
        # 这是 2.2 节"代码语义理解本身也可能出错"的直接体现，分类结果以本地重新校验为准。
        variant_id, unknown_variant = _classify_variant(metric_id, numerator_var, denominator_var)
        confidence = min(0.8, max(0.0, confidence)) if not unknown_variant else min(0.4, confidence)
        if confidence < _MIN_CONFIDENCE:
            continue
        results.append(
            FormulaHint(
                script_path=str(script_path),
                metric_guess=metric_id,
                numerator_var=numerator_var,
                denominator_var=denominator_var,
                confidence=confidence,
                variant_id=variant_id,
                unknown_variant=unknown_variant,
                discovered_by="code_semantics_model",
            )
        )
    return results


def _analyze_script_cheap(script_path: Path) -> tuple[list[dict[str, Any]] | None, str, list[FormulaHint]]:
    """确定性、不发网络请求的部分：查缓存 + 静态正则提取。

    返回 `(cached_result_or_none, content, static_hints)`。当第一个元素非 None 时，
    表示缓存命中，调用方直接使用，不需要再看后两个字段；否则调用方按需决定是否
    对 `static_hints` 为空的脚本追加一次模型调用。
    """
    cached = project_parse_cache.get_cached_formula_hint(script_path)
    if cached is not None:
        return list(cached.get("hints") or []), "", []
    content = _read_script(script_path)
    static_hints = _static_extract(script_path, content) if content else []
    return None, content, static_hints


def _analyze_script(script_path: Path) -> list[dict[str, Any]]:
    """单脚本全量解析（含可能的模型调用），供未走并发批处理的调用方使用。"""
    cached, content, static_hints = _analyze_script_cheap(script_path)
    if cached is not None:
        return cached

    hints: list[FormulaHint] = list(static_hints)
    if content and not hints and not _is_offline():
        try:
            hints.extend(_model_augment(script_path, content))
        except Exception:  # noqa: BLE001
            logger.warning(
                "project_code_semantics stage=model_augment status=unhandled_error script=%s",
                str(script_path),
                exc_info=True,
            )

    result = [h.to_dict() for h in hints]
    project_parse_cache.set_cached_formula_hint(script_path, {"hints": result})
    return result


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


def analyze_project_workflow_scripts(
    project_root: Path,
    *,
    project_config: dict[str, Any] | None = None,
    assay: str | None = None,
    limit: int = _MAX_SCRIPTS_PER_PROJECT,
) -> list[dict[str, Any]]:
    """定位项目使用的 SOP/workflow 脚本并提取 formula_hint 列表。

    产出只作为线索，供字段发现层（Phase 1.2，本次未实施）或文件发现层参考，
    不直接进入 fact_packet；调用方仍须经既有 parser + strict_formula_recalculation
    校验才能把任何数值当作正式证据。

    `assay` 建议传 assay_analysis_service 用的同一套词汇
    （cuttag/chipseq/cutrun/atacseq/rnaseq）；不传或传未知值时按"不确定"处理，
    不做 assay 过滤，行为等同于改造前。
    """
    try:
        scripts = find_internal_workflow_files(project_root, limit=limit, project_config=project_config)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "project_code_semantics stage=find_scripts status=failed root=%s error=%s",
            str(project_root),
            exc,
        )
        return []

    all_hints: list[dict[str, Any]] = []
    # 第一遍：只做确定性、不发网络请求的缓存查询 + 静态正则提取，成本可忽略。
    # 收集出静态提取完全没结果、需要追加模型调用的脚本，留到第二遍并发处理。
    pending_model: list[tuple[Path, str]] = []
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
            result = [h.to_dict() for h in static_hints]
            project_parse_cache.set_cached_formula_hint(script_path, {"hints": result})
            all_hints.extend(result)
            continue
        if content and not _is_offline():
            pending_model.append((script_path, content))
        else:
            project_parse_cache.set_cached_formula_hint(script_path, {"hints": []})

    # 第二遍：需要模型增强的脚本并发发起，共享一个总预算（而不是每个脚本各自
    # 顺序占用 25s）。预算耗尽后不再等待剩余请求的结果——httpx 层面的请求仍可能
    # 在后台跑到自己的超时才结束，但不会阻塞本函数返回，也不会拖累调用方
    # （project_analysis_service._select_evidence_files）的整体预算。
    if pending_model:
        # 注意：不能用 `with ThreadPoolExecutor(...) as executor:`——退出时会调用
        # `shutdown(wait=True)`，会重新阻塞到所有 future 跑完。这里的执行器故意不
        # 在本函数生命周期内关闭等待：`shutdown(wait=False)` 之后，后台线程继续独立跑到
        # 各自的 `_MODEL_AUGMENT_BUDGET_SECONDS` 超时或完成，通过 `add_done_callback`
        # 把结果写入缓存，不需要本函数还活着。
        executor = ThreadPoolExecutor(
            max_workers=min(_MODEL_AUGMENT_MAX_WORKERS, len(pending_model)),
            thread_name_prefix="code-semantics-model",
        )

        def _on_done(script_path: Path, future) -> None:
            try:
                hints = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "project_code_semantics stage=model_augment status=failed script=%s error=%s",
                    str(script_path),
                    exc,
                )
                hints = []
            result = [h.to_dict() for h in hints]
            project_parse_cache.set_cached_formula_hint(script_path, {"hints": result})

        future_to_script = {}
        for script_path, content in pending_model:
            future = executor.submit(_model_augment, script_path, content)
            future.add_done_callback(lambda f, sp=script_path: _on_done(sp, f))
            future_to_script[future] = script_path

        # §13 解决方法 1：只顺带等一个很短的同步预算（默认 1.5s），不阻塞主流程；
        # 等到的算这次响应的"意外惊喜"，没等到的继续在后台跑，写入缓存供下一次同类
        # 请求直接命中——本函数返回时完全不需要这些 future 已经完成。
        try:
            for future in as_completed(future_to_script, timeout=_MODEL_AUGMENT_SYNC_WAIT_SECONDS):
                script_path = future_to_script[future]
                try:
                    hints = future.result()
                except Exception:
                    hints = []
                all_hints.extend(h.to_dict() for h in hints)
        except FuturesTimeoutError:
            logger.info(
                "project_code_semantics stage=model_augment status=deferred_to_background root=%s "
                "sync_wait_seconds=%.1f background_budget_seconds=%.1f pending=%d",
                str(project_root),
                _MODEL_AUGMENT_SYNC_WAIT_SECONDS,
                _MODEL_AUGMENT_BUDGET_SECONDS,
                len(pending_model),
            )
        finally:
            executor.shutdown(wait=False)

    all_hints = _filter_hints_by_assay(all_hints, assay)
    logger.info(
        "project_code_semantics stage=analyze root=%s scripts=%d hints=%d assay=%s",
        str(project_root),
        len(scripts),
        len(all_hints),
        assay or "unknown",
    )
    return all_hints
