"""Phase 1.5：候选指标队列——影子层全自动 + 转正分级。

背景与边界见 docs/project_analysis_agent_upgrade_plan.md 3 节 Phase 1.5 / 2.3 节，以及
2026-07-02 评审修订 project_analysis_phase1.5_auto_promotion_revision.md 第一部分（替换了
下面第 2 点原来的"≥5 项目"自动转正条件），核心约束：

1. **影子层全自动，无需人工卡点**：探索出的候选字段映射只要通过一次算术自洽检查就自动
   写入候选表，呈现为 `reasoning_packet.exploratory_observations`（"探索性观察，未正式
   确认"），不进入 `fact_packet.direct_conclusions`。
2. **转正锚点已改为脚本公式，不再是"跨项目次数"**：主转正路径见
   `script_formula_promotion_service.py`——`record_script_backed_observation()` 在能定位到
   产出该候选的 SOP/workflow 脚本时调用它，按 (script_hash, metric_id, formula_variant)
   分级（情形 A 自动祝福、B/C/D 人工祝福一次、E 禁止自动转正）。本文件里原来"≥5 个不同
   项目 + 跨项目公式一致"的 `_classify_status`/`_maybe_auto_promote` 逻辑现在**只作为情形
   E（脚本里定位不到公式，只有算术自洽）的残留兜底**保留：不再自动调用
   `register_metric()`，只把候选标记为 `pending_review` 并在 `review_note` 里注明"多项目
   复现"作为人工审核的排序参考，见 `CANDIDATE_METRIC_AUTO_PROMOTE_MIN_PROJECTS` 的降级说明。
3. **能自动化的永远是"在已知选项里匹配/分类"，"定义一个新规则"必须先经人工审核**：
   `script_formula_promotion_service` 的情形 A 之所以能免人工，是因为公式来自静态正则
   确定性提取 + 重算通过，属于"分类到已知变体"，不是"发明新规则"，与本条纪律一致。
4. 驳回的候选写入黑名单，避免同一猜测反复出现在审核队列里。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.repositories.candidate_metric_repository import (
    candidate_metric_repository,
)
from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)

_MAX_LABEL_LEN = 80


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify_metric_guess(label: str) -> str:
    """把候选指标的原始表头/描述文本转成 metric_id 风格的 slug。"""
    normalized = re.sub(r"[^a-zA-Z0-9一-鿿]+", "_", str(label or "").strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized[:64] or "unnamed_candidate_metric"


def record_observation(
    *,
    project_id: str,
    metric_guess_label: str,
    unit_guess: str,
    source_file: str,
    numerator_field: str,
    denominator_field: str,
    display_field: str,
    sample: str,
    value: float,
    script_path: str | None = None,
    formula_hint: dict[str, Any] | None = None,
) -> None:
    """记录一条候选指标观测；影子层全自动写入，不需要人工卡点。

    `script_path`/`formula_hint`（可选，来自 Phase 1.1 代码语义解析层，见
    `project_code_semantics_service.py` 的 `FormulaHint`）：如果调用方能定位到产出这条候选的
    SOP/workflow 脚本、且脚本里确实提取到了这个指标的公式线索，就走
    `script_formula_promotion_service` 的脚本公式转正主路径（替换原来的 ≥5 项目路径，见本
    模块顶部文档）；拿不到脚本线索时，仍然只进影子层，等待人工审核或极少数残留场景兜底。

    任何异常都只记录日志，不能影响调用方（文件/字段发现流程）。
    """
    try:
        candidate_key = slugify_metric_guess(metric_guess_label)
        if not candidate_key:
            return
        # 已经是注册表里的正式指标（含别名），不算候选——这属于 Phase 1.2 字段发现层
        # 的职责范围，不应该混进候选指标队列。
        if metric_schema_service.canonical_id(candidate_key) in set(metric_schema_service.all_metric_ids()):
            return
        if candidate_metric_repository.is_blacklisted(candidate_key):
            return

        existing = candidate_metric_repository.get(candidate_key) or {
            "candidate_key": candidate_key,
            "metric_guess": candidate_key,
            "label": str(metric_guess_label or candidate_key)[:_MAX_LABEL_LEN],
            "unit_guess": unit_guess,
            "occurrences": [],
            "status": "shadow",
            "created_at": _now_iso(),
            "reviewed_by": None,
            "review_note": None,
            "promoted_metric_id": None,
        }
        occurrence = {
            "project_id": project_id,
            "source_file": source_file,
            "numerator_field": numerator_field,
            "denominator_field": denominator_field,
            "display_field": display_field,
            "sample": sample,
            "value": value,
            "observed_at": _now_iso(),
        }
        occurrences = list(existing.get("occurrences") or [])
        occurrences.append(occurrence)
        existing["occurrences"] = occurrences
        existing["updated_at"] = _now_iso()
        existing["distinct_project_count"] = len({o.get("project_id") for o in occurrences if o.get("project_id")})
        existing["occurrence_count"] = len(occurrences)

        if existing.get("status") not in {"approved", "approved_auto", "rejected"}:
            promoted = False
            if script_path and formula_hint and bool(settings.FORMULA_PROMOTION_ENABLED):
                promoted = _maybe_script_backed_promote(candidate_key, existing, script_path, formula_hint)
            if not promoted:
                # 残留场景（情形 E：没有可用的脚本公式线索）：只用旧的次数统计做人工审核
                # 排序参考，不再自动转正。
                existing["status"] = _classify_status_legacy_hint(existing)

        candidate_metric_repository.upsert(candidate_key, existing)
    except Exception:  # noqa: BLE001
        logger.warning(
            "candidate_metric stage=record_observation status=failed metric_guess=%s",
            metric_guess_label,
            exc_info=True,
        )


def _maybe_script_backed_promote(
    candidate_key: str,
    candidate: dict[str, Any],
    script_path: str,
    formula_hint: dict[str, Any],
) -> bool:
    """脚本公式转正主路径。返回 True 表示已经处理了 status（祝福或转人工），调用方不应
    再叠加旧的次数统计路径。"""
    try:
        from multi_agent.backed.app.services.business_agent.script_formula_promotion_service import (
            evaluate_and_maybe_bless,
        )

        latest = (candidate.get("occurrences") or [])[-1] if candidate.get("occurrences") else {}
        result = evaluate_and_maybe_bless(
            script_path=script_path,
            metric_id=candidate_key,
            formula_variant=formula_hint.get("variant_id"),
            unknown_variant=bool(formula_hint.get("unknown_variant", True)),
            discovered_by=str(formula_hint.get("discovered_by") or "code_semantics_static"),
            numerator_field=str(latest.get("numerator_field") or ""),
            denominator_field=str(latest.get("denominator_field") or ""),
            recalculation_passed=True,  # 调用方（字段发现层）已经过 normalize() 自洽校验
            is_new_candidate=True,
            target_contract="strict_formula_recalculation",
        )
        case_class = result.get("case_class")
        if case_class == "E":
            return False
        if result.get("blessed"):
            candidate["status"] = "approved_auto"
            candidate["promoted_metric_id"] = candidate_key
            candidate["reviewed_by"] = "script_formula_auto_promotion"
            candidate["review_note"] = f"脚本公式转正 case={case_class} promotion_key={result.get('promotion_key')}"
        else:
            candidate["status"] = "pending_review"
            candidate["review_note"] = (
                f"脚本公式转正候选（case={case_class}，需人工祝福一次）"
                f" promotion_key={result.get('promotion_key')}"
            )
        candidate["updated_at"] = _now_iso()
        return True
    except Exception:  # noqa: BLE001
        logger.warning(
            "candidate_metric stage=script_backed_promote status=failed candidate_key=%s",
            candidate_key,
            exc_info=True,
        )
        return False


def _formula_consistent(occurrences: list[dict[str, Any]]) -> bool:
    """跨项目公式是否一致：分子/分母字段命名模式在所有出现里保持一致。"""
    field_pairs = {
        (str(o.get("numerator_field") or "").strip().lower(), str(o.get("denominator_field") or "").strip().lower())
        for o in occurrences
    }
    return len(field_pairs) == 1


def _classify_status_legacy_hint(candidate: dict[str, Any]) -> str:
    """情形 E 残留兜底（脚本里定位不到公式）：`CANDIDATE_METRIC_AUTO_PROMOTE_MIN_PROJECTS`
    从"自动转正阈值"降级为"人工审核排序提示"（见
    project_analysis_phase1.5_auto_promotion_revision.md 第一部分 §6）。

    **不再调用 `register_metric()`**：跨项目次数只能证明"这个字段映射反复出现"，证明不了
    "这一列真的是这个意思"（弱统计代理的硬伤，见修订方案 §1）；命中阈值时只是把候选标记
    为 `pending_review` 并在 review_note 里留一句多项目复现的提示，最终转正与否交给
    `admin_router.py` 的人工审核接口决定。跨项目公式不一致的情形同样转人工判断
    （可能是 SOP 版本差异，也可能是猜错了）。
    """
    distinct_projects = int(candidate.get("distinct_project_count") or 0)
    occurrences = candidate.get("occurrences") or []
    min_projects = int(settings.CANDIDATE_METRIC_AUTO_PROMOTE_MIN_PROJECTS)
    if distinct_projects >= min_projects and _formula_consistent(occurrences):
        candidate["review_note"] = (
            f"多项目复现（{distinct_projects} 个项目，跨项目字段映射一致），"
            "但缺少脚本公式依据，不满足自动转正条件——建议优先人工审核。"
        )
        return "pending_review"
    if distinct_projects >= 2 and not _formula_consistent(occurrences):
        # 跨项目公式不一致：可能是 SOP 版本差异，也可能是猜错了，需要人工判断（方案 3 节）。
        candidate["review_note"] = f"跨 {distinct_projects} 个项目的分子/分母字段命名不一致，需人工判断。"
        return "pending_review"
    return "shadow"


def list_exploratory_observations(project_id: str) -> list[dict[str, Any]]:
    """给 reasoning_packet.exploratory_observations 用：只暴露 shadow/pending_review 状态、
    且与当前项目相关的候选，格式严格对齐方案 2.3 节新增字段定义。
    """
    if not project_id:
        return []
    results: list[dict[str, Any]] = []
    try:
        for candidate in candidate_metric_repository.list_all():
            if candidate.get("status") not in {"shadow", "pending_review", "eligible_for_auto_promotion"}:
                continue
            occurrences = [
                o for o in (candidate.get("occurrences") or []) if o.get("project_id") == project_id
            ]
            if not occurrences:
                continue
            latest = occurrences[-1]
            results.append(
                {
                    "metric_guess": candidate.get("metric_guess") or candidate.get("candidate_key"),
                    "value": latest.get("value"),
                    "unit_guess": candidate.get("unit_guess") or "",
                    "source_file": latest.get("source_file") or "",
                    "source_field": latest.get("display_field") or "",
                    "confidence": 0.5 if candidate.get("status") == "shadow" else 0.65,
                    "discovered_by": "field_discovery",
                    "status": "unverified",
                }
            )
    except Exception:  # noqa: BLE001
        logger.warning("candidate_metric stage=list_exploratory_observations status=failed", exc_info=True)
        return []
    return results[:8]


def list_for_admin_review(status: str | None = None) -> list[dict[str, Any]]:
    candidates = candidate_metric_repository.list_all()
    if status:
        candidates = [c for c in candidates if c.get("status") == status]
    return sorted(candidates, key=lambda c: c.get("updated_at") or c.get("created_at") or "", reverse=True)


def approve_candidate(
    candidate_key: str,
    *,
    metric_id: str,
    unit: str,
    verifier_contract: str,
    applicable_assays: list[str] | None = None,
    label: str | None = None,
    reviewer: str = "",
) -> tuple[bool, str]:
    candidate = candidate_metric_repository.get(candidate_key)
    if not candidate:
        return False, "候选指标不存在"
    canonical = metric_schema_service.canonical_id(metric_id)
    if canonical in set(metric_schema_service.all_metric_ids()):
        return False, f"指标 ID 已存在：{canonical}"
    schema = {
        "label": label or candidate.get("label") or metric_id,
        "unit": unit,
        "display_unit": unit,
        "value_scale": "percent" if unit == "%" else "number",
        "valid_range": [0.0, None],
        "formula": "human_reviewed_candidate_metric_pending_full_formula_documentation",
        "numerator": "",
        "denominator": "",
        "source_scale": "number",
        "assay_scope": applicable_assays or ["all"],
        "verifier_contract": verifier_contract,
    }
    registered = metric_schema_service.register_metric(metric_id, schema, overwrite=False)
    if not registered:
        return False, "写入指标注册表失败（可能已存在）"
    candidate["status"] = "approved"
    candidate["promoted_metric_id"] = canonical
    candidate["reviewed_by"] = reviewer or "admin"
    candidate["updated_at"] = _now_iso()
    candidate_metric_repository.upsert(candidate_key, candidate)
    return True, "已转正"


def reject_candidate(candidate_key: str, *, note: str = "", reviewer: str = "") -> tuple[bool, str]:
    candidate = candidate_metric_repository.get(candidate_key)
    if not candidate:
        return False, "候选指标不存在"
    candidate["status"] = "rejected"
    candidate["review_note"] = note
    candidate["reviewed_by"] = reviewer or "admin"
    candidate["updated_at"] = _now_iso()
    candidate_metric_repository.upsert(candidate_key, candidate)
    candidate_metric_repository.add_to_blacklist(candidate_key)
    return True, "已驳回并加入黑名单"
