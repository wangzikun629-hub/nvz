"""脚本公式转正祝福表持久化（project_analysis_phase1.5_auto_promotion_revision.md 第一部分 §5）。

`blessed_formula_map`：`(script_hash, metric_id, formula_variant) -> {contract, numerator_field,
denominator_field, blessed_by, blessed_at}`，是脚本公式转正机制的权威真值源，必须跨 worker 一致：

- 转正键含 `script_hash`：脚本一旦被修改，hash 变化，与已祝福的 `promotion_key` 不再匹配，
  该指标在新版本脚本下自动掉回候选队列（见 `script_formula_promotion_service.py`），不需要
  任何人记得去检查——这是"自动化为什么安全"的关键机制，见方案 §4。
- 写入只能来自两处：情形 A（静态确定性提取）自动祝福，或情形 B/C/D 人工审核通过；任何模型
  输出都不得直接落这张表（模型只能把候选推进到"待人工祝福"状态）。

存储后端与 `candidate_metric_repository.py` 对齐，同样受
`settings.CANDIDATE_METRIC_STORAGE_BACKEND` 控制，默认 MySQL、原子 upsert；显式配置为 json
时退化为单进程 JSON 文件（仅单 worker 部署安全）。

2026-07-02 code review 修复（详见 `candidate_metric_repository.py` 同批修复的说明，这里做法
完全对齐）：补 `ensure_table()` 并接入启动流程；去掉不必要的显式 `conn.begin()`；加短时熔断
避免 MySQL 故障时把连接超时拖进请求同步路径。

`promotion_key`（`sha256(64) + "::" + metric_id + "::" + formula_variant`）理论上可能超过
`VARCHAR(191)`（utf8mb4 唯一索引在部分 InnoDB row_format 下的安全上限）。没有把列宽直接放大到
255，因为那需要假定生产 MySQL 的 `innodb_large_prefix`/row_format 配置支持（不是所有部署都
开），风险不可控；改为在 `script_formula_promotion_service.build_promotion_key()` 里对
`metric_id`/`formula_variant` 做防御性截断，从源头保证生成的 key 不会超过 191，列宽维持
191 不变，和迁移脚本 `migrations/002_*.sql` 保持一致。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.logging.logger import logger

_CREATE_BLESSED_FORMULA_MAP_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `blessed_formula_map` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `promotion_key` VARCHAR(191) NOT NULL,
    `script_hash` CHAR(64) NOT NULL,
    `script_path_hint` VARCHAR(512) NOT NULL DEFAULT '',
    `metric_id` VARCHAR(128) NOT NULL,
    `formula_variant` VARCHAR(64) NOT NULL DEFAULT 'unknown_variant',
    `numerator_field` VARCHAR(160) NOT NULL DEFAULT '',
    `denominator_field` VARCHAR(160) NOT NULL DEFAULT '',
    `verifier_contract` VARCHAR(64) NOT NULL DEFAULT 'display_value_only',
    `case_class` CHAR(1) NOT NULL,
    `discovered_by` VARCHAR(32) NOT NULL DEFAULT 'code_semantics_static',
    `status` VARCHAR(32) NOT NULL DEFAULT 'blessed',
    `blessed_by` VARCHAR(64) NOT NULL DEFAULT '',
    `blessed_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_promotion_key` (`promotion_key`),
    KEY `idx_metric_status` (`metric_id`, `status`),
    KEY `idx_script_hash` (`script_hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='脚本公式转正祝福表（多 worker 权威真值源）';
"""


def ensure_table() -> None:
    """启动时调用，确保 `blessed_formula_map` 表存在（幂等）。见
    `candidate_metric_repository.ensure_table()` 的同一份设计说明。"""
    backend = str(getattr(settings, "CANDIDATE_METRIC_STORAGE_BACKEND", "mysql") or "mysql").strip().lower()
    if backend == "json":
        return
    try:
        from multi_agent.backed.app.infrastructure.database.database_pool import pool

        conn = pool.connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(_CREATE_BLESSED_FORMULA_MAP_TABLE_SQL)
            conn.commit()
            logger.info("[BlessedFormulaRepository] blessed_formula_map 表就绪")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("[BlessedFormulaRepository] blessed_formula_map 建表失败: %s", exc)


class _JsonBlessedFormulaRepository:
    STORAGE_DIR_NAME = "candidate_metrics"
    STORAGE_FILE_NAME = "blessed_formula_map.json"

    def __init__(self) -> None:
        current_file = Path(__file__).resolve()
        base_dir = current_file.parent.parent
        self._storage_root = base_dir / self.STORAGE_DIR_NAME
        self._storage_root.mkdir(parents=True, exist_ok=True)
        self._path = self._storage_root / self.STORAGE_FILE_NAME
        self._lock = threading.Lock()

    def _load_all(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_all(self, payload: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        tmp.replace(self._path)

    def get(self, promotion_key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._load_all().get(promotion_key)
            return dict(entry) if entry else None

    def upsert(self, promotion_key: str, entry: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = self._load_all()
            payload[promotion_key] = dict(entry)
            self._save_all(payload)
            return dict(entry)

    def list_all(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(v) for v in self._load_all().values()]
        if status:
            items = [item for item in items if item.get("status") == status]
        return items

    def list_blessed_for_metric(self, metric_id: str) -> list[dict[str, Any]]:
        return [item for item in self.list_all(status="blessed") if item.get("metric_id") == metric_id]


class _MysqlBlessedFormulaRepository:
    _CIRCUIT_BREAKER_SECONDS = 30.0

    def __init__(self) -> None:
        self._fallback = _JsonBlessedFormulaRepository()
        self._mysql_warned = False
        self._circuit_open_until = 0.0
        self._circuit_lock = threading.Lock()

    def _mysql_available(self) -> bool:
        return time.monotonic() >= self._circuit_open_until

    def _trip_circuit(self) -> None:
        with self._circuit_lock:
            self._circuit_open_until = time.monotonic() + self._CIRCUIT_BREAKER_SECONDS

    def _connection(self):
        from multi_agent.backed.app.infrastructure.database.database_pool import (
            DatabasePool,
        )

        return DatabasePool.get_connection()

    def _warn_fallback(self, exc: Exception) -> None:
        self._trip_circuit()
        if not self._mysql_warned:
            logger.warning(
                "blessed_formula_repository stage=mysql status=unavailable "
                "fallback=json_single_process circuit_breaker_seconds=%.0f error=%s",
                self._CIRCUIT_BREAKER_SECONDS,
                exc,
            )
            self._mysql_warned = True

    def _mark_success(self) -> None:
        if self._mysql_warned:
            logger.info("blessed_formula_repository stage=mysql status=recovered")
            self._mysql_warned = False

    @staticmethod
    def _row_to_entry(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "promotion_key": row.get("promotion_key"),
            "script_hash": row.get("script_hash"),
            "script_path_hint": row.get("script_path_hint", ""),
            "metric_id": row.get("metric_id"),
            "formula_variant": row.get("formula_variant"),
            "numerator_field": row.get("numerator_field", ""),
            "denominator_field": row.get("denominator_field", ""),
            "verifier_contract": row.get("verifier_contract", "display_value_only"),
            "case_class": row.get("case_class"),
            "discovered_by": row.get("discovered_by", ""),
            "status": row.get("status", "pending_review"),
            "blessed_by": row.get("blessed_by", ""),
            "blessed_at": str(row.get("blessed_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }

    def get(self, promotion_key: str) -> dict[str, Any] | None:
        if not self._mysql_available():
            return self._fallback.get(promotion_key)
        try:
            import pymysql.cursors

            conn = self._connection()
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM blessed_formula_map WHERE promotion_key=%s",
                        (promotion_key,),
                    )
                    row = cursor.fetchone()
                    self._mark_success()
                    return self._row_to_entry(row) if row else None
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            self._warn_fallback(exc)
            return self._fallback.get(promotion_key)

    def upsert(self, promotion_key: str, entry: dict[str, Any]) -> dict[str, Any]:
        if not self._mysql_available():
            return self._fallback.upsert(promotion_key, entry)
        try:
            import pymysql.cursors

            conn = self._connection()
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    # 不显式调用 conn.begin()——和仓库里其它 repository 一致（见
                    # candidate_metric_repository.py 同批修复说明）。
                    cursor.execute(
                        "SELECT id FROM blessed_formula_map WHERE promotion_key=%s FOR UPDATE",
                        (promotion_key,),
                    )
                    existing = cursor.fetchone()
                    params = (
                        entry.get("script_hash", ""),
                        str(entry.get("script_path_hint") or "")[:512],
                        entry.get("metric_id"),
                        entry.get("formula_variant") or "unknown_variant",
                        str(entry.get("numerator_field") or "")[:160],
                        str(entry.get("denominator_field") or "")[:160],
                        entry.get("verifier_contract") or "display_value_only",
                        entry.get("case_class"),
                        entry.get("discovered_by", ""),
                        entry.get("status", "pending_review"),
                        entry.get("blessed_by", ""),
                        entry.get("blessed_at"),
                    )
                    if existing:
                        cursor.execute(
                            """
                            UPDATE blessed_formula_map SET
                                script_hash=%s, script_path_hint=%s, metric_id=%s,
                                formula_variant=%s, numerator_field=%s, denominator_field=%s,
                                verifier_contract=%s, case_class=%s, discovered_by=%s,
                                status=%s, blessed_by=%s, blessed_at=%s
                            WHERE promotion_key=%s
                            """,
                            (*params, promotion_key),
                        )
                    else:
                        cursor.execute(
                            """
                            INSERT INTO blessed_formula_map (
                                promotion_key, script_hash, script_path_hint, metric_id,
                                formula_variant, numerator_field, denominator_field,
                                verifier_contract, case_class, discovered_by, status,
                                blessed_by, blessed_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (promotion_key, *params),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            self._mark_success()
            return dict(entry)
        except Exception as exc:  # noqa: BLE001
            self._warn_fallback(exc)
            return self._fallback.upsert(promotion_key, entry)

    def list_all(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if not self._mysql_available():
            return self._fallback.list_all(status=status)
        try:
            import pymysql.cursors

            conn = self._connection()
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    if status:
                        cursor.execute(
                            "SELECT * FROM blessed_formula_map WHERE status=%s", (status,)
                        )
                    else:
                        cursor.execute("SELECT * FROM blessed_formula_map")
                    rows = cursor.fetchall() or []
                    self._mark_success()
                    return [self._row_to_entry(row) for row in rows]
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            self._warn_fallback(exc)
            return self._fallback.list_all(status=status)

    def list_blessed_for_metric(self, metric_id: str) -> list[dict[str, Any]]:
        if not self._mysql_available():
            return self._fallback.list_blessed_for_metric(metric_id)
        try:
            import pymysql.cursors

            conn = self._connection()
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM blessed_formula_map WHERE metric_id=%s AND status='blessed'",
                        (metric_id,),
                    )
                    rows = cursor.fetchall() or []
                    self._mark_success()
                    return [self._row_to_entry(row) for row in rows]
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            self._warn_fallback(exc)
            return self._fallback.list_blessed_for_metric(metric_id)


def _build_repository():
    backend = str(getattr(settings, "CANDIDATE_METRIC_STORAGE_BACKEND", "mysql") or "mysql").strip().lower()
    if backend == "json":
        logger.info("blessed_formula_repository stage=init backend=json note=single_worker_only")
        return _JsonBlessedFormulaRepository()
    logger.info("blessed_formula_repository stage=init backend=mysql")
    return _MysqlBlessedFormulaRepository()


blessed_formula_repository = _build_repository()
