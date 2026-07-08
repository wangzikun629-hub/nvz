"""Phase 1.2：字段发现层——替代硬编码列名穷举的通用抽取逻辑。

背景与边界见 docs/project_analysis_agent_upgrade_plan.md 2.3 节 / 3 节 Phase 1.2，核心约束：

1. **只在关键词/硬编码列名穷举失败的文件上生效**：`project_file_parser_service.py` 里现有
   11 个 `build_*_summary` 函数（`build_frip_summary` 等）完全不受影响，继续用原来的硬编码
   列名候选表；本模块只处理 `resolve_table_kind()` 认不出文件类型的表格文件——这类文件此前
   会直接落入纯文本摘要分支，一个指标都提取不出来。
2. **字段映射永远是候选猜测，取到值必须再跑重算校验**：猜出 `numerator_field`/
   `denominator_field`/`display_value_field` 后，通过 `metric_schema_service.normalize()`
   把猜出来的分子除以分母跟猜出来的展示值比较；差太多（或数值本身不合法）直接判定这次
   字段猜测失败，丢弃，不产出证据——不管猜测过程多"自信"，都不能绕开这一步。
3. **通过重算校验的值，与关键词命中的值同等可信**（2.3 节）：只要通过验证，产出的
   evidence_chain 条目就是正常证据，不带"探索性"标记；不能通过验证的猜测完全不返回。
4. **Phase 1.1 的 `formula_hint` 是加分线索，不是必需输入**：没有 formula_hint 时，本模块
   仍然可以只靠 Phase 0 的 `detection_signature`/`label` 做字段名启发式匹配。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.infrastructure.tools.local.project_reader import read_table_rows
from multi_agent.backed.app.services.business_agent.metric_schema_service import (
    metric_schema_service,
)

_TABLE_SUFFIXES = {".xls", ".csv", ".tab", ".tsv"}
_SAMPLE_HEADER_CANDIDATES = (
    "sample", "samples", "sample_id", "sampleid", "sample name", "样本", "样本名", "样本编号",
)
_MIN_TOKEN_SCORE = 1
_MIN_CONFIDENCE = 0.35
# 需要分子/分母才能重算校验的指标之外，还允许没有分子分母、只需展示值的 contract
_DISPLAY_ONLY_CONTRACTS = {"citation_only", "display_value_only"}
_NOT_APPLICABLE_CONTRACTS = {"non_numeric_design_status"}


@dataclass
class FieldMapping:
    """字段发现层的候选产出 + 已通过自洽校验的抽取值（一起返回，未通过校验的不会出现在结果里）。"""

    file_path: str
    metric_id: str
    sample: str
    display_field: str
    numerator_field: str
    denominator_field: str
    value: float
    display_value: str
    confidence: float
    discovered_by: str = "field_discovery"

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "metric_id": self.metric_id,
            "sample": self.sample,
            "display_field": self.display_field,
            "numerator_field": self.numerator_field,
            "denominator_field": self.denominator_field,
            "value": self.value,
            "display_value": self.display_value,
            "confidence": round(self.confidence, 4),
            "discovered_by": self.discovered_by,
        }


def _normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _tokenize_description(text: str) -> list[str]:
    return [tok for tok in re.split(r"[^a-zA-Z0-9]+", str(text or "").lower()) if len(tok) > 2]


def _pick_sample_field(headers: list[str]) -> str | None:
    lowered = {h.lower(): h for h in headers}
    for candidate in _SAMPLE_HEADER_CANDIDATES:
        if candidate in lowered:
            return lowered[candidate]
    return headers[0] if headers else None


_GENERIC_SHORT_LABEL_TOKENS = frozenset(
    {
        "total", "count", "counts", "rate", "ratio", "percent", "percentage",
        "value", "score", "status", "sample", "samples", "result", "results",
        "summary", "data", "number", "all", "raw", "clean", "name", "reads",
        "read",
    }
)


def _score_header(header: str, tokens: list[str]) -> int:
    """Token/header 双向子串打分。

    2026-07-03 真实项目排查修复：`normalized_header in normalized_token`（短的一边
    被长的一边"包含"）这个方向单独拿出来看风险很高——像行标签"Total"这种通用词，
    几乎必然是某个 metric_id/label 拼接后字符串的偶然子串（例如
    "silva_total_ratio_percent" 去掉下划线就是 "silvatotalratiopercent"，里面天然
    带着 "total"），这纯属字符串巧合碰撞，不代表真的识别出了这一行/这一列对应
    这个指标。真实案例：`AllSamples.silva.xls` 里物种表的汇总行就叫 "Total"，
    被 `_discover_transposed_summary_table` 误判命中 `silva_total_ratio_percent`，
    产出了看似合法、实际张冠李戴的证据。这里给这个方向加一条限制：被包含的
    一边如果本身就是这类过于宽泛的通用词，不计分——只有 `normalized_token in
    normalized_header`（长的、更具体的 token 出现在待匹配文本里）这个安全方向，
    或者完全相等，才无条件计分。"""
    normalized_header = _normalize_token(header)
    if not normalized_header:
        return 0
    score = 0
    for token in tokens:
        normalized_token = _normalize_token(token)
        if not normalized_token:
            continue
        if normalized_token == normalized_header:
            score += 1
        elif normalized_token in normalized_header:
            score += 1
        elif normalized_header in normalized_token and normalized_header not in _GENERIC_SHORT_LABEL_TOKENS:
            score += 1
    return score


def _best_header(headers: list[str], tokens: list[str], *, exclude: set[str]) -> tuple[str | None, int]:
    best_header = None
    best_score = 0
    for header in headers:
        if header in exclude:
            continue
        score = _score_header(header, tokens)
        if score > best_score:
            best_score = score
            best_header = header
    return best_header, best_score


def _metric_field_tokens(
    metric_id: str, formula_hints: list[dict[str, Any]] | None
) -> tuple[list[str], list[str], list[str]]:
    """返回 (display_tokens, numerator_tokens, denominator_tokens)。"""
    schema = metric_schema_service.get(metric_id)
    display_tokens = [
        str(t) for t in (schema.get("detection_signature") or []) if str(t).strip()
    ]
    label = str(schema.get("label") or "").strip()
    if label:
        display_tokens.append(label)
    numerator_tokens = _tokenize_description(schema.get("numerator") or "")
    denominator_tokens = _tokenize_description(schema.get("denominator") or "")

    for hint in formula_hints or []:
        if not isinstance(hint, dict):
            continue
        if metric_schema_service.canonical_id(hint.get("metric_guess")) != metric_id:
            continue
        numerator_var = str(hint.get("numerator_var") or "").strip()
        denominator_var = str(hint.get("denominator_var") or "").strip()
        if numerator_var:
            numerator_tokens.append(numerator_var)
        if denominator_var:
            denominator_tokens.append(denominator_var)
    return display_tokens, numerator_tokens, denominator_tokens


def _extract_metric_from_rows(
    file_path: Path,
    metric_id: str,
    rows: list[dict[str, str]],
    headers: list[str],
    sample_field: str | None,
    formula_hints: list[dict[str, Any]] | None,
) -> list[FieldMapping]:
    contract = metric_schema_service.verifier_contract(metric_id)
    if contract in _NOT_APPLICABLE_CONTRACTS:
        return []

    display_tokens, numerator_tokens, denominator_tokens = _metric_field_tokens(metric_id, formula_hints)
    exclude = {sample_field} if sample_field else set()

    display_field, display_score = _best_header(headers, display_tokens, exclude=exclude)
    if not display_field or display_score < _MIN_TOKEN_SCORE:
        return []

    numerator_field: str | None = None
    denominator_field: str | None = None
    needs_recalculation = contract == "strict_formula_recalculation"
    if needs_recalculation:
        numerator_field, num_score = _best_header(headers, numerator_tokens, exclude=exclude | {display_field})
        denominator_field, den_score = _best_header(
            headers, denominator_tokens, exclude=exclude | {display_field, numerator_field or ""}
        )
        if not numerator_field or not denominator_field or num_score < _MIN_TOKEN_SCORE or den_score < _MIN_TOKEN_SCORE:
            return []

    results: list[FieldMapping] = []
    for row in rows:
        sample = row.get(sample_field, "") if sample_field else "-"
        display_raw = row.get(display_field, "")
        numerator_raw = row.get(numerator_field, "") if numerator_field else None
        denominator_raw = row.get(denominator_field, "") if denominator_field else None
        if not str(display_raw or "").strip():
            continue
        normalized = metric_schema_service.normalize(
            metric_id,
            display_raw,
            source_field=display_field,
            numerator=numerator_raw,
            denominator=denominator_raw,
        )
        if not normalized.get("valid"):
            # 猜测的字段组合没能通过重算/范围自洽校验，如实丢弃，不产出证据（方案 Phase 1.2 步骤 3）。
            continue
        confidence = min(0.9, 0.4 + 0.15 * display_score)
        if needs_recalculation:
            confidence = min(0.9, confidence + 0.1)
        if confidence < _MIN_CONFIDENCE:
            continue
        results.append(
            FieldMapping(
                file_path=str(file_path),
                metric_id=metric_id,
                sample=str(sample or "-").strip() or "-",
                display_field=display_field,
                numerator_field=numerator_field or "",
                denominator_field=denominator_field or "",
                value=float(normalized["value"]),
                display_value=str(normalized.get("display_value") or "-"),
                confidence=confidence,
            )
        )
    return results


_NOVEL_MAX_NUMERIC_COLUMNS = 25
_NOVEL_MIN_ROWS = 2
_NOVEL_TOLERANCE = 1e-3


def _parse_number(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.rstrip("%").replace(",", "").strip()
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value == value else None  # 过滤 NaN


def _looks_numeric_column(rows: list[dict[str, str]], header: str, *, min_ratio: float = 0.8) -> bool:
    total = 0
    numeric = 0
    for row in rows:
        raw = row.get(header, "")
        if str(raw or "").strip() == "":
            continue
        total += 1
        if _parse_number(raw) is not None:
            numeric += 1
    return total > 0 and numeric / total >= min_ratio


def _matches_known_metric(header: str) -> bool:
    """判断某表头是否已经很像现有注册表里的某个指标——命中就不算"全新指标"。

    这是 Phase 1.5 和 Phase 1.2 的边界：已注册指标的字段发现是 Phase 1.2 的职责，
    这里的候选指标队列只处理注册表里完全没有的全新字段模式。
    """
    for metric_id in metric_schema_service.all_metric_ids():
        schema = metric_schema_service.get(metric_id)
        tokens = [str(t) for t in (schema.get("detection_signature") or []) if str(t).strip()]
        label = str(schema.get("label") or "").strip()
        if label:
            tokens.append(label)
        if _score_header(header, tokens) >= _MIN_TOKEN_SCORE:
            return True
    return False


def discover_novel_metric_candidates(file_path: Path) -> list[dict[str, Any]]:
    """在完全没有匹配到任何已知指标的表格文件里，探测"新的比例关系"候选指标。

    这是 Phase 1.5 影子层的输入来源：对全部数值列做两两组合，检查
    `A / B`（或 `A / B * 100`）是否在所有数据行里都与另一列 C 的值自洽——样本量太小
    时两个毫不相关的字段也可能凑巧满足除法关系（方案 3 节明确警告过这一点），所以要求
    至少 2 行数据、且每一行都满足关系才算候选，仅算术自洽，不代表这就是真实的生物学含义，
    最终只作为"探索性观察"呈现，不是正式结论。
    """
    if file_path.suffix.lower() not in _TABLE_SUFFIXES:
        return []
    try:
        rows = read_table_rows(file_path)
    except Exception:
        return []
    if len(rows) < _NOVEL_MIN_ROWS:
        return []

    headers = list(rows[0].keys())
    sample_field = _pick_sample_field(headers)
    numeric_headers = [
        h for h in headers
        if h != sample_field and _looks_numeric_column(rows, h)
    ][:_NOVEL_MAX_NUMERIC_COLUMNS]
    if len(numeric_headers) < 3:
        return []

    candidates: list[dict[str, Any]] = []
    seen_display_headers: set[str] = set()
    for display_header in numeric_headers:
        if display_header in seen_display_headers or _matches_known_metric(display_header):
            continue
        for num_header in numeric_headers:
            if num_header == display_header:
                continue
            for den_header in numeric_headers:
                if den_header in {display_header, num_header}:
                    continue
                percent_ok = True
                fraction_ok = True
                checked_rows = 0
                for row in rows:
                    num_val = _parse_number(row.get(num_header))
                    den_val = _parse_number(row.get(den_header))
                    display_val = _parse_number(row.get(display_header))
                    if num_val is None or not den_val or display_val is None:
                        continue
                    checked_rows += 1
                    ratio = num_val / den_val
                    if abs(ratio * 100 - display_val) > max(_NOVEL_TOLERANCE, abs(display_val) * 1e-2):
                        percent_ok = False
                    if abs(ratio - display_val) > max(_NOVEL_TOLERANCE, abs(display_val) * 1e-2):
                        fraction_ok = False
                    if not percent_ok and not fraction_ok:
                        break
                if checked_rows < _NOVEL_MIN_ROWS or not (percent_ok or fraction_ok):
                    continue
                unit_guess = "%" if percent_ok else "ratio"
                for row in rows[:5]:
                    display_val = _parse_number(row.get(display_header))
                    if display_val is None:
                        continue
                    candidates.append(
                        {
                            "metric_guess_label": display_header,
                            "unit_guess": unit_guess,
                            "numerator_field": num_header,
                            "denominator_field": den_header,
                            "display_field": display_header,
                            "sample": str(row.get(sample_field, "") or "-").strip() or "-",
                            "value": display_val,
                            "source_file": str(file_path),
                        }
                    )
                seen_display_headers.add(display_header)
                break
            if display_header in seen_display_headers:
                break
    return candidates


_TRANSPOSED_MIN_METRIC_HITS = 3


def _match_metric_for_row_label(label: str) -> tuple[str | None, int]:
    """在全部已注册指标里，找出与某一行标签（转置表的第一列）最匹配的 metric_id。

    复用 `_score_header` 的 token 匹配逻辑——行标签（如 `Silva_total_ratio(%)`）和列表头
    本质上是同一种"字段命名"，只是转置表里它出现在第一列而不是表头行，所以可以直接
    对每个已注册指标的 `detection_signature`/`label`/`metric_id` 做同样的打分匹配。
    """
    best_metric: str | None = None
    best_score = 0
    for metric_id in metric_schema_service.all_metric_ids():
        schema = metric_schema_service.get(metric_id)
        tokens = [str(t) for t in (schema.get("detection_signature") or []) if str(t).strip()]
        metric_label = str(schema.get("label") or "").strip()
        if metric_label:
            tokens.append(metric_label)
        tokens.append(metric_id)
        score = _score_header(label, tokens)
        if score > best_score:
            best_score = score
            best_metric = metric_id
    return best_metric, best_score


def _is_transposed_summary_table(rows: list[dict[str, str]], label_key: str) -> bool:
    """判断一份表格是不是"指标做行、样本做列"的转置主汇总表（如 All_Sample_Stat.xls）。

    判定依据：第一列（label_key）里能匹配上已注册指标 label/detection_signature 的行数
    达到 `_TRANSPOSED_MIN_METRIC_HITS` 条。要求多条命中而不是 1 条，是为了避免偶然出现
    一行像某个指标名字的普通正向表格被误判——转置主汇总表的特征是"整列全是指标名"，
    不是"某一格恰好长得像指标名"。
    """
    hits = 0
    for row in rows:
        label = str(row.get(label_key, "") or "").strip()
        if not label:
            continue
        _metric_id, score = _match_metric_for_row_label(label)
        if score >= _MIN_TOKEN_SCORE:
            hits += 1
            if hits >= _TRANSPOSED_MIN_METRIC_HITS:
                return True
    return False


def _discover_transposed_summary_table(
    file_path: Path,
    rows: list[dict[str, str]],
    headers: list[str],
    target_metrics: list[str],
) -> list[dict[str, Any]]:
    """Phase 1.6（project_analysis_agent_upgrade_plan.md）：转置主汇总表通用解析器。

    背景：`All_Sample_Stat.xls` 这类文件把指标名放在行、样本放在列，一份文件里覆盖几十个
    已注册指标（mapping rate / dup rate / rRNA_ratio / Silva_total_ratio 等），但现有 11 个
    `build_*_summary` 函数和上面的按列匹配的字段发现逻辑都假设"每行一个样本"，对这种文件
    完全无从下手——`_pick_sample_field` 会把第一列（其实是指标名列）误当成样本列，之后每个
    指标专属的列名匹配自然一个都对不上。

    这里换一个方向：把第一列当成"行标签＝指标名"，其余每一列当成一个样本，逐行匹配
    `metric_schema_service` 的 label/detection_signature，命中就对该行的每个样本列取值。
    这类主汇总表通常只有展示值、没有独立的分子/分母原始计数列，因此对
    `strict_formula_recalculation` 类指标无法在这里重算校验；产出的证据和现有按列匹配的
    字段发现结果一样，交给 `evidence_card_service._build_rule_entry()` 处理——没有
    `rule_source` 时会自动降级为"未核实公式/阈值"，不会冒充已核实的重算结论
    （见 project_analysis_service.py 里 field_discovery 分支的注释）。
    """
    if len(headers) < 2:
        return []
    label_key = headers[0]
    sample_columns = [h for h in headers[1:] if h]
    if not sample_columns:
        return []
    if not _is_transposed_summary_table(rows, label_key):
        return []

    canonical_targets = {metric_schema_service.canonical_id(m) for m in target_metrics}
    results: list[dict[str, Any]] = []
    for row in rows:
        label = str(row.get(label_key, "") or "").strip()
        if not label:
            continue
        metric_id, score = _match_metric_for_row_label(label)
        if not metric_id or metric_id not in canonical_targets or score < _MIN_TOKEN_SCORE:
            continue
        contract = metric_schema_service.verifier_contract(metric_id)
        if contract in _NOT_APPLICABLE_CONTRACTS:
            continue
        for sample in sample_columns:
            raw_value = row.get(sample, "")
            if str(raw_value or "").strip() == "":
                continue
            normalized = metric_schema_service.normalize(
                metric_id,
                raw_value,
                source_field=label,
            )
            if not normalized.get("valid"):
                # 猜的这一行不是这个指标，或者数值本身不合法／超出范围——如实丢弃，
                # 不产出证据（和按列匹配的字段发现逻辑同一条纪律）。
                continue
            confidence = min(0.85, 0.4 + 0.15 * score)
            results.append(
                {
                    "file_path": str(file_path),
                    "metric_id": metric_id,
                    "sample": str(sample).strip() or "-",
                    "display_field": label,
                    "numerator_field": "",
                    "denominator_field": "",
                    "value": float(normalized["value"]),
                    "display_value": str(normalized.get("display_value") or "-"),
                    "confidence": round(confidence, 4),
                    "discovered_by": "transposed_summary_table",
                }
            )
    if results:
        logger.info(
            "project_field_discovery stage=transposed_summary_table file=%s target_metrics=%s extracted=%d",
            str(file_path),
            target_metrics,
            len(results),
        )
    return results


def _discover_from_exploration_hint(
    file_path: Path,
    target_metrics: list[str],
    exploration_hint: dict[str, Any],
    known_samples: list[str] | None,
) -> list[dict[str, Any]]:
    """Stage B-补 Step 3：把探索 agent 提交的 `value` 当作第三候选来源。

    只在调用方已经确认两条规则化字段发现（按列匹配 + 按行匹配转置表）都没有
    产出任何结果时才会被调用（见 `discover_and_extract`）。这里不重新猜字段
    位置——agent 已经读过文件、报了它认为的值，这里只做两件事：
    1. 如果提供了 `known_samples`（项目已知样本列表），hint 里的 `sample` 必须
       在这个列表里，否则直接拒绝——这是给没有公式重算、`normalize()` 校验力度
       偏弱的 `display_value_only`/`citation_only` 类指标补的安全网，不能只靠
       数值范围检查兜底（`known_samples` 为空/未提供时跳过这条，不因为没有
       样本清单信息就一律拒绝）。
    2. 把 `value` 交给 `metric_schema_service.normalize()` 做该指标本身的
       自洽/范围校验，不通过就丢弃——和其它两条规则化路径的红线完全一致。
    """
    metric_id = metric_schema_service.canonical_id(exploration_hint.get("candidate_metric_type"))
    canonical_targets = {metric_schema_service.canonical_id(m) for m in target_metrics}
    if not metric_id or metric_id not in canonical_targets:
        return []
    contract = metric_schema_service.verifier_contract(metric_id)
    if contract in _NOT_APPLICABLE_CONTRACTS:
        return []
    value = str(exploration_hint.get("value") or "").strip()
    if not value:
        return []
    sample = str(exploration_hint.get("sample") or "").strip()
    source_field = str(exploration_hint.get("source_field") or "").strip()
    if known_samples:
        known_normalized = {str(s or "").strip().lower() for s in known_samples if str(s or "").strip()}
        if known_normalized and (not sample or sample.strip().lower() not in known_normalized):
            logger.info(
                "project_field_discovery stage=exploration_hint status=rejected_sample_mismatch "
                "file=%s metric=%s sample=%r known_sample_count=%d",
                str(file_path),
                metric_id,
                sample,
                len(known_normalized),
            )
            return []
    normalized = metric_schema_service.normalize(
        metric_id,
        value,
        source_field=source_field or "exploration_agent_hint",
    )
    if not normalized.get("valid"):
        logger.info(
            "project_field_discovery stage=exploration_hint status=rejected_normalize_failed "
            "file=%s metric=%s value=%r",
            str(file_path),
            metric_id,
            value,
        )
        return []
    try:
        confidence = min(0.6, float(exploration_hint.get("confidence") or 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    mapping = FieldMapping(
        file_path=str(file_path),
        metric_id=metric_id,
        sample=sample or "-",
        display_field=source_field or "-",
        numerator_field="",
        denominator_field="",
        value=float(normalized["value"]),
        display_value=str(normalized.get("display_value") or "-"),
        confidence=confidence,
        discovered_by="exploration_agent_hint",
    )
    logger.info(
        "project_field_discovery stage=exploration_hint status=accepted file=%s metric=%s sample=%s",
        str(file_path),
        metric_id,
        sample,
    )
    return [mapping.to_dict()]


# 2026-07-03 修复（真实项目排查：Silva_total_ratio(%) 撞车 bug）：字段发现层此前对
# "同一个 (metric_id, sample) 被多个文件各自独立命中"完全没有裁决——`discover_and_
# extract()` 是逐文件调用的，只要某个文件的列名字面匹配上就会产出一条通过校验的
# 证据，不知道、也不关心别的文件是否也命中了同一个指标。真实项目里 `SilvaBlast/
# Blast_result/AllSamples.silva.xls`（单样本 blast 明细）里恰好也有一列字面叫
# `Silva_total_ratio(%)`，和真正的项目级汇总表 `All_Sample_Stat.xls` 撞了同一个
# metric_id+sample，规则化按列匹配对两个文件都会"成功"，谁先被处理、谁的证据就留
# 在 evidence_chain 里，纯属偶然。这里加一层路径启发式的择优规则，在
# `project_analysis_service._build_evidence_chain` 消费 `field_discovery` 列表前
# 调用：汇总目录（如 `Statistic`）/ 项目级汇总表命名（如 `All_Sample_Stat`）优先，
# blast/明细目录降权；同一 (metric_id, sample) 只保留最高优先级的一条。
# 没有命中任何路径规则的情况下保持先到先得（stable），不引入新的不确定性。
_DEMOTED_SOURCE_PATH_TOKENS = ("blast_result", "silvablast", "_blast", ".blast.", "blast.xls")
_PROMOTED_SOURCE_PATH_TOKENS = ("statistic", "all_sample_stat")


def _field_discovery_source_priority(source_file: str, trust_level: str = "") -> tuple[int, int]:
    """返回 (path_score, trust_score)，数值越大越优先；用于跨文件择优排序。"""
    lower = str(source_file or "").lower().replace("/", "\\")
    demoted = any(token in lower for token in _DEMOTED_SOURCE_PATH_TOKENS)
    promoted = any(token in lower for token in _PROMOTED_SOURCE_PATH_TOKENS)
    path_score = (1 if promoted else 0) - (1 if demoted else 0)
    trust_score = 1 if str(trust_level or "").strip() == "recalculated" else 0
    return (path_score, trust_score)


def dedupe_by_source_priority(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一个 (metric_id, sample) 出现多条字段发现证据时，按来源路径优先级只保留一条。

    `metric_id`/`sample` 缺失的条目不参与去重（原样保留，避免误伤），因为分组键
    本身没有意义。有意义的分组里，优先级并列时保留先出现的一条（stable），不新增
    随机性。这不改变"通过 normalize() 校验才会出现在这里"这条既有红线——去重只在
    多条都已经是"合法证据"的前提下选一条展示，不影响校验逻辑本身。
    """
    best: dict[tuple[str, str], dict[str, Any]] = {}
    best_score: dict[tuple[str, str], tuple[int, int]] = {}
    order: list[tuple[str, str]] = []
    passthrough: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        metric_id = str(hit.get("metric_id") or "").strip()
        sample = str(hit.get("sample") or "").strip()
        if not metric_id or not sample:
            passthrough.append(hit)
            continue
        key = (metric_id, sample)
        score = _field_discovery_source_priority(
            str(hit.get("source_file") or ""), str(hit.get("trust_level") or "")
        )
        if key not in best:
            best[key] = hit
            best_score[key] = score
            order.append(key)
        elif score > best_score[key]:
            best[key] = hit
            best_score[key] = score
    return [best[key] for key in order] + passthrough


def discover_and_extract(
    file_path: Path,
    target_metrics: list[str],
    *,
    formula_hints: list[dict[str, Any]] | None = None,
    exploration_hint: dict[str, Any] | None = None,
    known_samples: list[str] | None = None,
) -> list[dict[str, Any]]:
    """对一个未被 resolve_table_kind() 认出的表格文件，猜字段映射并抽取已验证的指标值。

    只返回通过 `metric_schema_service.normalize()` 自洽/重算校验的结果；猜测失败的字段
    组合直接丢弃，调用方拿到的每一条都可以像正常证据一样使用。

    Stage B-补（project_analysis_exploration_and_evolution_plan.md）：`exploration_
    hint` 是 Stage B 探索 agent 通过 `propose_evidence(value=...)` 提交的字段级线索
    （见 `project_file_discovery_service.to_candidate_hints()`）。只有前两条规则化
    发现（按列匹配 + 按行匹配转置表）都完全没找到任何证据时，才会尝试把这个 hint
    当作第三候选来源，且同样要过 `metric_schema_service.normalize()` 校验——这是
    2026-07-03 review 的结论：让 agent 只给已有规则候选"排序打分"，在两条规则都
    失效的场景里等于没有真正的决定权；只有让它的读值作为独立候选、和规则候选一样
    过同一道真值校验，它才有独立于规则之外的话语权。`known_samples` 提供时会额外
    核对 hint 里的 `sample` 是否在项目已知样本列表里——这条是给
    `display_value_only`/`citation_only` 这类没有公式重算、`normalize()` 校验力度
    偏弱的指标补的安全网，样本对不上直接拒绝，不能只靠数值范围检查兜底。
    """
    if file_path.suffix.lower() not in _TABLE_SUFFIXES:
        return []
    target_metrics = [str(m or "").strip() for m in target_metrics if str(m or "").strip()]
    if not target_metrics:
        return []

    try:
        rows = read_table_rows(file_path)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "project_field_discovery stage=read_table status=skip file=%s error=%s",
            str(file_path),
            exc,
        )
        return []
    if not rows:
        return []

    headers = list(rows[0].keys())
    sample_field = _pick_sample_field(headers)

    all_results: list[dict[str, Any]] = []
    for metric_id in target_metrics:
        canonical = metric_schema_service.canonical_id(metric_id)
        try:
            matches = _extract_metric_from_rows(
                file_path, canonical, rows, headers, sample_field, formula_hints
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "project_field_discovery stage=extract status=failed file=%s metric=%s",
                str(file_path),
                canonical,
                exc_info=True,
            )
            continue
        all_results.extend(item.to_dict() for item in matches)

    # Phase 1.6：按列匹配的字段发现完全没找到任何证据时，再尝试一次"按行匹配"的转置
    # 主汇总表解析——两者互斥触发，不会互相覆盖或重复产出同一条证据。
    if not all_results:
        try:
            all_results = _discover_transposed_summary_table(file_path, rows, headers, target_metrics)
        except Exception:  # noqa: BLE001
            logger.warning(
                "project_field_discovery stage=transposed_summary_table status=failed file=%s",
                str(file_path),
                exc_info=True,
            )

    # Stage B-补 Step 3：两条规则化发现都交白卷，且有探索 agent 的 hint 时，把它
    # 当第三候选来源，仍然强制过 normalize() 校验。
    if not all_results and exploration_hint:
        try:
            all_results = _discover_from_exploration_hint(
                file_path, target_metrics, exploration_hint, known_samples
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "project_field_discovery stage=exploration_hint status=failed file=%s",
                str(file_path),
                exc_info=True,
            )

    if all_results:
        logger.info(
            "project_field_discovery stage=discover file=%s target_metrics=%s extracted=%d",
            str(file_path),
            target_metrics,
            len(all_results),
        )
    return all_results
