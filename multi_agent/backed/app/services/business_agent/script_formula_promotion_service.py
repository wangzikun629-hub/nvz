"""脚本公式驱动的安全自动转正（project_analysis_phase1.5_auto_promotion_revision.md 第一部分）。

替换原 Phase 1.5"≥5 个项目 + 跨项目公式一致 + display_value_only"的自动转正条件。核心思路：
把自动转正的锚点从"跨项目算术自洽的弱统计代理"换成"公式明确写在项目脚本里"——脚本就是定义，
不需要用项目数量来排除巧合，单个项目即可，且可以授予 `strict_formula_recalculation` 全信任。

两条独立的确定性确认叠加，才允许自动信任（§2）：
    确认 A（公式来源）：公式由静态正则从脚本里确定性提取，或由模型提取后经人工祝福一次。
    确认 B（数据吻合）：用该公式对结果文件重算，数值对得上（调用方已经用
        `metric_schema_service.normalize()` 做过这一步，这里只接收结果）。

转正键 `promotion_key = (script_hash, metric_id, formula_variant)`：脚本一旦被修改，
hash 变化，旧祝福自动失效，该 metric 在新脚本版本下掉回候选队列并触发复审——这是"自动化
为什么安全"的关键，不依赖任何人记得去巡检（见方案 §4）。

分级表（§3.3）：
    A．静态正则确定性提取 + 重算通过 + 无命名冲突   -> 自动祝福
    B．仅模型能提取（静态失败）+ 重算通过 + 无冲突   -> 人工祝福一次，此后自动
    C．任意来源 + 重算通过 + 命名可能与现有指标冲突  -> 人工判断
    D．匹配不上任何已知 formula_variants             -> 人工审核新变体
    E．脚本里定位不到公式（仅展示值）                 -> 禁止自动转正，留在探索性观察层

本模块只做分类 + 持久化判定结果，不发明公式、不绕过 `metric_schema_service.normalize()` 的
重算比对——这条红线与 2.2 节完全一致，代码语义解析 agent 的产出永远只是"分类到已知变体"。
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.repositories.blessed_formula_repository import (
    blessed_formula_repository,
)
from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)

CASE_AUTO_BLESS = "A"
CASE_MODEL_EXTRACTED = "B"
CASE_NAMING_CONFLICT = "C"
CASE_UNKNOWN_VARIANT = "D"
CASE_NO_SCRIPT_FORMULA = "E"

# code review 修复（建议#2）：`_SCRIPT_HASH_CACHE` 按 (脚本路径, mtime) 记忆 sha256 结果。
# 同一份 SOP 脚本会被同一个项目里多个样本/多个文件的字段发现命中反复调用
# `evaluate_and_maybe_bless` → `compute_script_hash`，之前每次都重新读盘+哈希；现在只在
# 脚本内容真的变化（mtime 变化）时才重新计算，和 `project_parse_cache` 里其它按 mtime
# 缓存的机制是同一套思路。
_SCRIPT_HASH_CACHE: dict[tuple[str, int], str] = {}
_SCRIPT_HASH_CACHE_LOCK = threading.Lock()
_SCRIPT_HASH_CACHE_MAX_ENTRIES = 512


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_script_hash(script_path: str | Path) -> str:
    """整份脚本内容 sha256（第一版粒度，见修订方案 §8 待确认 #2：先用整份脚本 hash，
    监控复审次数后再决定是否要按函数/代码段细化），按 (路径, mtime) 记忆结果。"""
    path = Path(script_path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return ""
    cache_key = (str(path.resolve()), mtime_ns)
    with _SCRIPT_HASH_CACHE_LOCK:
        cached = _SCRIPT_HASH_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        content = path.read_bytes()
    except Exception:
        return ""
    digest = hashlib.sha256(content).hexdigest()
    with _SCRIPT_HASH_CACHE_LOCK:
        _SCRIPT_HASH_CACHE[cache_key] = digest
        while len(_SCRIPT_HASH_CACHE) > _SCRIPT_HASH_CACHE_MAX_ENTRIES:
            _SCRIPT_HASH_CACHE.pop(next(iter(_SCRIPT_HASH_CACHE)))
    return digest


# code review 修复：`blessed_formula_map.promotion_key` 列宽是 VARCHAR(191)（utf8mb4 唯一
# 索引在部分 InnoDB row_format 下的安全上限）。sha256 固定 64 位，分隔符固定 4 位，剩下
# 123 位分给 metric_id + formula_variant；这里各截断到 48 位（合计最坏情况 64+4+48+48=164
# < 191，留了安全余量），防止极端情况下超长 key 插入失败后被广义 except 悄悄吞掉、
# 退化到单进程 JSON 存储（那样就悄悄违反了这张表要保证的多 worker 一致性）。
_PROMOTION_KEY_SEGMENT_MAX_LEN = 48


def build_promotion_key(script_hash: str, metric_id: str, formula_variant: str | None) -> str:
    variant = (formula_variant or "unknown_variant")[:_PROMOTION_KEY_SEGMENT_MAX_LEN]
    metric = str(metric_id or "")[:_PROMOTION_KEY_SEGMENT_MAX_LEN]
    return f"{script_hash}::{metric}::{variant}"


def _metric_id_conflict(metric_id: str, *, is_new_candidate: bool) -> bool:
    """情形 C 的命名冲突判定：第一版从严——任何 ID 与现有注册表重叠都转人工
    （修订方案 §8 待确认 #3 建议的第一版策略）。

    对已经在注册表里的正式指标（`is_new_candidate=False`）走脚本公式转正，本身就是给
    这个指标"升级信任等级"，不算命名冲突。只有全新候选指标 ID 撞上已有指标才算冲突。
    """
    if not is_new_candidate:
        return False
    canonical = metric_schema_service.canonical_id(metric_id)
    return canonical in set(metric_schema_service.all_metric_ids())


def classify(
    *,
    metric_id: str,
    formula_variant: str | None,
    unknown_variant: bool,
    discovered_by: str,
    recalculation_passed: bool,
    is_new_candidate: bool,
) -> str:
    """对照 §3.3 分级表返回 A/B/C/D/E。"""
    if not recalculation_passed:
        return CASE_NO_SCRIPT_FORMULA
    if unknown_variant or not formula_variant:
        return CASE_UNKNOWN_VARIANT
    if _metric_id_conflict(metric_id, is_new_candidate=is_new_candidate):
        return CASE_NAMING_CONFLICT
    if discovered_by == "code_semantics_static":
        return CASE_AUTO_BLESS
    return CASE_MODEL_EXTRACTED


def evaluate_and_maybe_bless(
    *,
    script_path: str | Path,
    metric_id: str,
    formula_variant: str | None,
    unknown_variant: bool,
    discovered_by: str,
    numerator_field: str,
    denominator_field: str,
    recalculation_passed: bool,
    is_new_candidate: bool,
    target_contract: str = "strict_formula_recalculation",
) -> dict[str, Any]:
    """核心入口：分类 + （情形 A 时）自动祝福 + 持久化。

    返回结构:
        {"case_class": "A"/"B"/"C"/"D"/"E", "blessed": bool, "promotion_key": str|None,
         "entry": dict|None}

    任何异常都只记录日志、返回未祝福结果，不能影响调用方（字段发现/候选指标记录）主流程。
    """
    try:
        canonical_metric = metric_schema_service.canonical_id(metric_id)

        # code review 修复（建议#6）；2026-07-02 补充对齐
        # truth_layer_recompute_generalization_plan.md §9："自动转正判据...recompute ∈
        # {ratio, expression, aggregate} 且其全部输入变量都能在结果文件里定位到列 →
        # 授予 strict_formula_recalculation；statistical 缺原始输入、constant、display →
        # 只授予 citation_only/display_value_only"。
        #
        # 对已经注册过的正式指标做"信任升级"时，只有它本身的 verifier_contract 就是
        # strict_formula_recalculation 才有意义——citation_only / display_value_only 这类
        # 按设计就不需要重算的指标（包括止血阶段改判过的 nrf/pbc1/pbc2/correlation/
        # spikein_scaling_factor/spikein_mapped_reads，它们的 recompute 是
        # statistical/constant/display，最高只配得上 citation_only/display_value_only），
        # 就算 formula_hint 碰巧命中，也不该被拖进脚本公式转正的分级流程（走到情形 B/C/D
        # 只会制造和"公式重算信任"无关的人工审核噪音，更不能借这条路径把它们偷偷升级成
        # strict——那正是 §1.3 那个 bug）。全新候选指标（is_new_candidate=True）还没有
        # 既有 contract，target_contract 本身就是这次要争取授予的信任等级，不受这条限制。
        if not is_new_candidate:
            existing_contract = metric_schema_service.verifier_contract(canonical_metric)
            if existing_contract != "strict_formula_recalculation":
                return {
                    "case_class": CASE_NO_SCRIPT_FORMULA,
                    "blessed": False,
                    "promotion_key": None,
                    "entry": None,
                }

        # 本模块目前只支持"脚本里有 numerator_field/denominator_field"这一种发现形态，
        # 对应 metric_schema_service 的 recompute="ratio"。§9 的判据要求"全部输入变量
        # 都能定位到列"才配拿 strict——numerator_field/denominator_field 任一为空，
        # 说明这次发现本质上凑不出一个完整的 a/b 公式，不该被授予 strict，防止调用方
        # 传错 target_contract 或以后有新调用方在字段不全时也传默认值。
        if target_contract == "strict_formula_recalculation" and not (
            str(numerator_field or "").strip() and str(denominator_field or "").strip()
        ):
            target_contract = "citation_only"

        script_hash = compute_script_hash(script_path)
        if not script_hash:
            return {"case_class": CASE_NO_SCRIPT_FORMULA, "blessed": False, "promotion_key": None, "entry": None}

        case_class = classify(
            metric_id=canonical_metric,
            formula_variant=formula_variant,
            unknown_variant=unknown_variant,
            discovered_by=discovered_by,
            recalculation_passed=recalculation_passed,
            is_new_candidate=is_new_candidate,
        )
        if case_class == CASE_NO_SCRIPT_FORMULA:
            # 情形 E：脚本里定位不到公式，禁止自动转正，交回调用方走原来的探索性观察层。
            return {"case_class": case_class, "blessed": False, "promotion_key": None, "entry": None}

        promotion_key = build_promotion_key(script_hash, canonical_metric, formula_variant)
        existing = blessed_formula_repository.get(promotion_key)
        if existing and existing.get("status") == "blessed":
            # 同一 (script_hash, metric_id, variant) 之前已经祝福过（可能是另一个项目复用
            # 同一份 SOP 脚本），直接复用，零人工。
            return {"case_class": case_class, "blessed": True, "promotion_key": promotion_key, "entry": existing}

        entry = {
            "promotion_key": promotion_key,
            "script_hash": script_hash,
            "script_path_hint": str(script_path),
            "metric_id": canonical_metric,
            "formula_variant": formula_variant or "unknown_variant",
            "numerator_field": numerator_field,
            "denominator_field": denominator_field,
            "verifier_contract": target_contract,
            "case_class": case_class,
            "discovered_by": discovered_by,
            "updated_at": _now_iso(),
        }

        if case_class == CASE_AUTO_BLESS:
            entry["status"] = "blessed"
            entry["blessed_by"] = "auto_static_extraction"
            entry["blessed_at"] = _now_iso()
            blessed_formula_repository.upsert(promotion_key, entry)
            _apply_blessing_to_registry(entry, is_new_candidate=is_new_candidate)
            logger.info(
                "script_formula_promotion stage=auto_bless metric_id=%s promotion_key=%s "
                "case=%s contract=%s",
                canonical_metric,
                promotion_key,
                case_class,
                target_contract,
            )
            return {"case_class": case_class, "blessed": True, "promotion_key": promotion_key, "entry": entry}

        # 情形 B/C/D：写入待人工祝福队列，不自动生效；admin 审核接口消费这张表
        # （见 api/admin_router.py 的候选公式审核扩展）。
        entry["status"] = "pending_review"
        entry["blessed_by"] = ""
        entry["blessed_at"] = None
        blessed_formula_repository.upsert(promotion_key, entry)
        logger.info(
            "script_formula_promotion stage=pending_review metric_id=%s promotion_key=%s case=%s",
            canonical_metric,
            promotion_key,
            case_class,
        )
        return {"case_class": case_class, "blessed": False, "promotion_key": promotion_key, "entry": entry}
    except Exception:  # noqa: BLE001
        logger.warning(
            "script_formula_promotion stage=evaluate status=failed metric_id=%s script=%s",
            metric_id,
            str(script_path),
            exc_info=True,
        )
        return {"case_class": CASE_NO_SCRIPT_FORMULA, "blessed": False, "promotion_key": None, "entry": None}


def _apply_blessing_to_registry(
    entry: dict[str, Any],
    *,
    is_new_candidate: bool,
    label: str | None = None,
    unit: str | None = None,
) -> None:
    """把已祝福的公式写入运行时指标注册表。

    - 全新候选指标：`register_metric()` 首次登记，直接授予 `verifier_contract`（不再像旧的
      ≥5 项目路径那样被迫降级为 display_value_only）。`label`/`unit` 由人工审核
      （`bless_pending`）时补充；情形 A 的自动祝福路径拿不到人工输入的 label/unit，只能退化
      为用 metric_id 本身占位——这类自动祝福场景绝大多数发生在已注册指标的信任升级
      （`is_new_candidate=False`）上，真正命中"全新指标 + 自动祝福"的情况很少见，
      详见方案第一部分 §2 已知局限说明。
    - 已有正式指标：说明这份脚本证实了该指标在当前 SOP 下确有据可查的分子/分母口径，这里
      只做只读式确认（不覆盖已有人工维护的 schema），交由字段发现层在命中该 promotion_key
      时把对应证据的 trust_level 标记为 recalculated（见 project_field_discovery_service.py
      的 script_formula_promotion 接入点）。
    """
    metric_id = str(entry.get("metric_id") or "")
    if not metric_id:
        return
    if not is_new_candidate:
        return
    schema = {
        "label": label or metric_id,
        "unit": unit or "",
        "display_unit": unit or "",
        "value_scale": "number",
        "valid_range": [0.0, None],
        "formula": f"blessed_from_script:{entry.get('promotion_key')}",
        "numerator": entry.get("numerator_field", ""),
        "denominator": entry.get("denominator_field", ""),
        "source_scale": "number",
        "assay_scope": ["all"],
        # 2026-07-02（truth_layer_recompute_generalization_plan.md §8/§9）：本模块只
        # 发现 numerator_field/denominator_field 形态的公式，对应 recompute="ratio"。
        # 显式声明，配合 metric_schema_service.validate_registry() 的一致性检查——
        # 缺这个字段不会让程序崩，但会让新指标的 verifier_contract() 兜底逻辑失去
        # 该有的依据，未来做运行时注册表自检时也需要它。
        "recompute": "ratio",
        "verifier_contract": entry.get("verifier_contract") or "strict_formula_recalculation",
    }
    metric_schema_service.register_metric(metric_id, schema, overwrite=False)


def lookup_blessed_contract(script_path: str | Path, metric_id: str, formula_variant: str | None) -> dict[str, Any] | None:
    """字段发现层的读路径：给定脚本 + metric_id + variant，查是否已经被祝福过。

    命中即可把对应证据的 trust_level 标记为 recalculated，即使这次重算发生在字段发现层
    （而不是脚本本身）——因为 promotion_key 里已经含有脚本 hash，祝福表已经确认了这套
    (脚本版本, 指标, 变体) 组合的公式来源可信。
    """
    script_hash = compute_script_hash(script_path)
    if not script_hash:
        return None
    canonical_metric = metric_schema_service.canonical_id(metric_id)
    promotion_key = build_promotion_key(script_hash, canonical_metric, formula_variant)
    entry = blessed_formula_repository.get(promotion_key)
    if entry and entry.get("status") == "blessed":
        return entry
    return None


def list_pending_review() -> list[dict[str, Any]]:
    """给 admin 审核接口用：情形 B/C/D 待人工祝福的公式变体队列。"""
    try:
        return blessed_formula_repository.list_all(status="pending_review")
    except Exception:  # noqa: BLE001
        logger.warning("script_formula_promotion stage=list_pending_review status=failed", exc_info=True)
        return []


def bless_pending(
    promotion_key: str,
    *,
    reviewer: str,
    verifier_contract: str | None = None,
    is_new_candidate: bool = True,
    label: str | None = None,
    unit: str | None = None,
) -> tuple[bool, str]:
    """人工祝福情形 B/C/D 的候选公式变体。祝福后此后任何匹配同一 promotion_key（同一脚本
    版本、同一指标、同一变体）的项目自动套用，无需再审。

    `label`/`unit`（code review 建议#4 修复）：情形 B/C/D 里如果这是一个全新候选指标（还没有
    正式 label/unit），审核员应该通过这两个参数补全，否则 `_apply_blessing_to_registry` 会
    退化用 `metric_id` 本身当 label——这和 `candidate_metric_service.approve_candidate()`
    要求审核员必须填 label/unit 是同一条纪律。已注册指标走信任升级（`is_new_candidate=False`）
    时这两个参数不生效（该分支本身就不写 schema）。
    """
    entry = blessed_formula_repository.get(promotion_key)
    if not entry:
        return False, "未找到该候选公式变体"
    if entry.get("status") == "blessed":
        return True, "已祝福（重复操作）"
    entry["status"] = "blessed"
    entry["blessed_by"] = reviewer or "admin"
    entry["blessed_at"] = _now_iso()
    if verifier_contract:
        entry["verifier_contract"] = verifier_contract
    blessed_formula_repository.upsert(promotion_key, entry)
    _apply_blessing_to_registry(entry, is_new_candidate=is_new_candidate, label=label, unit=unit)
    logger.info(
        "script_formula_promotion stage=bless_pending status=blessed promotion_key=%s reviewer=%s",
        promotion_key,
        reviewer,
    )
    return True, "已祝福并生效"


def reject_pending(promotion_key: str, *, reviewer: str, note: str = "") -> tuple[bool, str]:
    entry = blessed_formula_repository.get(promotion_key)
    if not entry:
        return False, "未找到该候选公式变体"
    entry["status"] = "rejected"
    entry["blessed_by"] = reviewer or "admin"
    entry["blessed_at"] = _now_iso()
    entry["review_note"] = note
    blessed_formula_repository.upsert(promotion_key, entry)
    return True, "已驳回"


def rebuild_registry_from_blessed_map() -> int:
    """开机加载：从祝福表重建运行时注册表里的候选指标条目（跨 worker 一致性的关键一环，
    见修订方案 §5："运行时 register_metric() 的注册表状态，启动时应从该祝福表重建，而不是
    依赖某个 worker 的内存历史"）。

    只重建"全新候选指标"这一类（`register_metric` 是幂等的 no-op 如果已存在），不会覆盖
    Phase 0 人工维护的正式指标基线。返回本次重建写入的条目数。
    """
    count = 0
    try:
        blessed_entries = blessed_formula_repository.list_all(status="blessed")
    except Exception:  # noqa: BLE001
        logger.warning("script_formula_promotion stage=rebuild_registry status=failed", exc_info=True)
        return 0
    known_ids = set(metric_schema_service.all_metric_ids())
    for entry in blessed_entries:
        metric_id = str(entry.get("metric_id") or "")
        if not metric_id or metric_id in known_ids:
            continue
        _apply_blessing_to_registry(entry, is_new_candidate=True)
        known_ids.add(metric_id)
        count += 1
    if count:
        logger.info("script_formula_promotion stage=rebuild_registry status=ok metrics_added=%d", count)
    return count
