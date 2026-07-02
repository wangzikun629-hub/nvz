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
   不在离线模式下，才尝试一次轻量模型调用去补充/提高置信度。模型调用失败、超时、
   返回非 JSON 一律静默降级为纯启发式结果，绝不让上层分析流程因为这里出错而中断。
4. **按项目根目录 mtime + 目标指标集合缓存**（复用 `project_parse_cache.py`），
   离线 harness 场景下天然只依赖文件 mtime，不受模型输出随机性影响。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
_MAX_CANDIDATES_PER_METRIC = 3
_MAX_TOTAL_CANDIDATES = 8
_MIN_CONFIDENCE = 0.35

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "candidate_metric_type": self.candidate_metric_type,
            "confidence": round(self.confidence, 4),
            "discovered_by": self.discovered_by,
            "matched_signature": list(self.matched_signature),
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


def _exploration_model_augment(
    project_files: list[ProjectFile],
    target_metrics: list[str],
    heuristic_hits: set[str],
) -> list[FileRoleAssignment]:
    """可选的模型增强：只对启发式完全没命中的指标尝试一次轻量分类调用。

    任何失败都静默降级为空列表，调用方只会得到纯启发式结果——绝不能因为模型
    调用异常影响 project_analysis 主流程（见方案 5 节验收门槛）。
    """
    remaining_metrics = [m for m in target_metrics if metric_schema_service.canonical_id(m) not in heuristic_hits]
    if not remaining_metrics:
        return []
    try:
        from multi_agent.backed.app.infrastructure.ai.openai_client import (
            EXPLORATION_CLIENT_CONFIGURED,
            EXPLORATION_MODEL_NAME,
            exploration_model_client,
        )
    except Exception:
        return []
    if not EXPLORATION_CLIENT_CONFIGURED:
        return []

    candidates = [pf for pf in project_files if pf.kind in {"table", "text"}][:_MAX_PROBE_FILES]
    if not candidates:
        return []

    file_briefs = []
    for pf in candidates:
        file_briefs.append(
            {
                "file_path": str(pf.path.name),
                "header_snippet": _cheap_snippet(pf.path)[:400],
            }
        )
    metric_briefs = [
        {
            "metric_id": metric_schema_service.canonical_id(m),
            "label": metric_schema_service.get(m).get("label", ""),
            "detection_signature": metric_schema_service.get(m).get("detection_signature", []),
        }
        for m in remaining_metrics
    ]
    prompt = (
        "你是生物信息学项目文件分类器。给定候选文件的文件名和表头片段，以及一批目标指标定义，"
        "判断每个候选文件最可能对应哪个目标指标（如果都不像，就不要输出这个文件）。"
        "只输出严格 JSON，格式为："
        '{"assignments": [{"file_path": "...", "metric_id": "...", "confidence": 0.0-1.0}]}'
        "，不要输出任何解释文字。\n"
        f"目标指标定义：{json.dumps(metric_briefs, ensure_ascii=False)}\n"
        f"候选文件：{json.dumps(file_briefs, ensure_ascii=False)}"
    )
    try:
        response = exploration_model_client.chat.completions.create(
            model=EXPLORATION_MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            # 2026-07-02 从 20s 调低：这一步是可选增强，且调用方
            # （project_analysis_service._select_evidence_files）现在给整个
            # discover_file_role_assignments 只留了 _FILE_DISCOVERY_BUDGET_SECONDS
            # （默认 8s）的硬预算，20s 的单次调用超时本身就大概率把预算吃满。
            timeout=8,
        )
        content = (response.choices[0].message.content or "").strip()
        content = re.sub(r"^```(json)?|```$", "", content, flags=re.IGNORECASE | re.MULTILINE).strip()
        payload = json.loads(content)
        raw_assignments = payload.get("assignments") or []
    except Exception as exc:  # noqa: BLE001 - 任何异常都只记录日志，不向上抛出
        logger.warning("project_file_discovery stage=exploration_model status=failed error=%s", exc)
        return []

    file_by_name = {pf.path.name: pf for pf in candidates}
    remaining_ids = {metric_schema_service.canonical_id(m) for m in remaining_metrics}
    results: list[FileRoleAssignment] = []
    for item in raw_assignments:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file_path") or "").strip()
        metric_id = metric_schema_service.canonical_id(item.get("metric_id"))
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            continue
        project_file = file_by_name.get(file_name)
        if project_file is None or metric_id not in remaining_ids:
            continue
        confidence = max(0.0, min(0.9, confidence))  # 模型产出的候选永远打折于人工确认过的启发式命中
        if confidence < _MIN_CONFIDENCE:
            continue
        results.append(
            FileRoleAssignment(
                file_path=str(project_file.path),
                candidate_metric_type=metric_id,
                confidence=confidence,
                discovered_by="file_discovery_exploration_model",
                matched_signature=[],
            )
        )
    return results


def discover_file_role_assignments(
    project_root: Path,
    target_metrics: list[str],
    *,
    exclude_paths: set[Path] | None = None,
    max_candidates: int = _MAX_TOTAL_CANDIDATES,
) -> list[dict[str, Any]]:
    """在关键词表未命中时，探索候选 file_role_assignment。

    返回值是候选证据文件的字典列表（含 `file_path` 绝对路径字符串），调用方把
    它们当作普通证据文件路径喂给现有 parser；是否真正生成 `evidence_card` 完全
    取决于下游既有的 `strict_formula_recalculation` 等校验，本函数不做真值判断。
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

    # Phase 1.1（代码语义解析 agent）的 formula_hint 作为过渡桥接线索，见
    # _formula_hint_tokens_by_metric() 说明；解析失败不能影响文件发现主流程。
    formula_hints: list[dict[str, Any]] = []
    try:
        from multi_agent.backed.app.services.project_code_semantics_service import (
            analyze_project_workflow_scripts,
        )

        formula_hints = analyze_project_workflow_scripts(project_root)
    except Exception:  # noqa: BLE001
        logger.warning(
            "project_file_discovery stage=code_semantics_hint status=failed root=%s",
            str(project_root),
            exc_info=True,
        )

    heuristic_assignments = _heuristic_match(probeable, target_metrics, formula_hints=formula_hints)
    heuristic_hits = {a.candidate_metric_type for a in heuristic_assignments}

    model_assignments: list[FileRoleAssignment] = []
    if not _is_offline():
        model_assignments = _exploration_model_augment(probeable, target_metrics, heuristic_hits)

    combined = heuristic_assignments + model_assignments
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
        "project_file_discovery stage=discover root=%s target_metrics=%s heuristic=%d model=%d final=%d offline=%s",
        str(project_root),
        target_metrics,
        len(heuristic_assignments),
        len(model_assignments),
        len(result),
        _is_offline(),
    )
    return project_parse_cache.set_cached_file_discovery(project_root, target_metrics, result)


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
