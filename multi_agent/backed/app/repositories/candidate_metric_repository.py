"""候选指标（Phase 1.5）持久化。

2026-07-02 修订（见 project_analysis_phase1.5_auto_promotion_revision.md §1/§9）：生产确认
要跑多 worker/多实例，原来"单个 JSON 文件 + threading.Lock"的存储只在单进程内安全——
worker A 写入的候选，worker B 的内存/文件视图不保证立刻可见，并发写还可能互相覆盖。

默认存储后端改为 MySQL（`settings.CANDIDATE_METRIC_STORAGE_BACKEND = "mysql"`），写入用
`SELECT ... FOR UPDATE` 事务做行级锁定的读-改-写，天然序列化同一 candidate_key 上的并发更新，
不依赖任何进程内锁。仅当显式配置为 `json` 时才退化为原来的单进程 JSON 文件实现——这只在
明确单 worker 部署时安全，见 `_JsonCandidateMetricRepository` 的类文档。

对外接口（`is_blacklisted` / `get` / `list_all` / `upsert` / `add_to_blacklist`）在两种后端下
完全一致，调用方（`candidate_metric_service.py`）不需要关心具体存储介质。

2026-07-02 code review 修复：
1. 补上 `ensure_table()` 并接入 `api/main.py` 的启动流程（对齐 `user_repository.py` /
   `kanban_rd_repository.py` 等既有 repository 的自建表约定）。此前只能靠手动执行
   `migrations/002_*.sql`，忘记跑迁移脚本会让代码静默退化成单进程 JSON 存储，恰好违背
   本表要解决的"多 worker 一致性"目标，而且不会有任何显式报错——是这一轮里最容易被忽略、
   后果又最隐蔽的问题。
2. 去掉了显式 `conn.begin()`：这个仓库里所有既有 repository（`user_repository.py` 等）都是
   直接执行 SQL 后 `conn.commit()`，不显式开事务（pymysql 连接默认 autocommit=False，第一条
   语句本身就会隐式开启事务）。之前引入的 `conn.begin()` 是不必要的新写法，为了和现有约定保持
   一致、降低连接池行为不确定的风险，这里改回和其它 repository 完全一样的写法。
3. 新增短时熔断：MySQL 不可用时，`MYSQL_CONNECT_TIMEOUT`（默认 10s）级别的连接尝试如果每次
   调用都重新来一遍，会在请求同步路径里重新引入 Phase 1.6 之后专门修复过的那类延迟问题。
   现在记录最近一次失败时间，`_CIRCUIT_BREAKER_SECONDS` 内的后续调用直接跳过 MySQL 连接尝试，
   直接走 JSON 兜底。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.logging.logger import logger

_CREATE_CANDIDATE_METRICS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `candidate_metrics` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `candidate_key` VARCHAR(128) NOT NULL,
    `metric_guess` VARCHAR(128) NOT NULL,
    `label` VARCHAR(160) NOT NULL DEFAULT '',
    `unit_guess` VARCHAR(32) NOT NULL DEFAULT '',
    `status` VARCHAR(32) NOT NULL DEFAULT 'shadow',
    `occurrence_count` INT UNSIGNED NOT NULL DEFAULT 0,
    `distinct_project_count` INT UNSIGNED NOT NULL DEFAULT 0,
    `occurrences_json` MEDIUMTEXT NULL,
    `promoted_metric_id` VARCHAR(128) NULL,
    `reviewed_by` VARCHAR(64) NULL,
    `review_note` VARCHAR(512) NULL,
    `blacklisted` TINYINT(1) NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_candidate_key` (`candidate_key`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Phase 1.5 候选指标队列（多 worker 权威存储）';
"""


def ensure_table() -> None:
    """启动时调用，确保 `candidate_metrics` 表存在（`CREATE TABLE IF NOT EXISTS`，幂等）。

    只在 MySQL 后端下有意义；`CANDIDATE_METRIC_STORAGE_BACKEND=json` 时直接跳过。任何异常都
    只记录日志，不阻塞应用启动（和 `user_repository.ensure_table()` 的容错方式一致）——万一
    这里失败，运行时仍会按预期降级到 JSON 兜底，只是失去多 worker 一致性。
    """
    backend = str(getattr(settings, "CANDIDATE_METRIC_STORAGE_BACKEND", "mysql") or "mysql").strip().lower()
    if backend == "json":
        return
    try:
        from multi_agent.backed.app.infrastructure.database.database_pool import pool

        conn = pool.connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(_CREATE_CANDIDATE_METRICS_TABLE_SQL)
            conn.commit()
            logger.info("[CandidateMetricRepository] candidate_metrics 表就绪")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("[CandidateMetricRepository] candidate_metrics 建表失败: %s", exc)


class _JsonCandidateMetricRepository:
    """原有的单进程 JSON 文件实现，仅在 `CANDIDATE_METRIC_STORAGE_BACKEND=json` 时使用，
    或作为 MySQL 不可用时的运行时兜底。

    只在确定单 worker 部署时安全（见 project_analysis_phase1.5_auto_promotion_revision.md
    §9 解决方法 4："若短期内不引入新表，至少保证 JSON 写入走单进程 + 原子替换"）。
    """

    STORAGE_DIR_NAME = "candidate_metrics"
    STORAGE_FILE_NAME = "candidates.json"
    MAX_OCCURRENCES_PER_CANDIDATE = 50

    def __init__(self) -> None:
        current_file = Path(__file__).resolve()
        base_dir = current_file.parent.parent
        self._storage_root = base_dir / self.STORAGE_DIR_NAME
        self._storage_root.mkdir(parents=True, exist_ok=True)
        self._path = self._storage_root / self.STORAGE_FILE_NAME
        self._lock = threading.Lock()

    def _load_all(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"candidates": {}, "blacklist": []}
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            return {"candidates": {}, "blacklist": []}
        if not isinstance(payload, dict):
            return {"candidates": {}, "blacklist": []}
        payload.setdefault("candidates", {})
        payload.setdefault("blacklist", [])
        return payload

    def _save_all(self, payload: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        tmp.replace(self._path)

    def is_blacklisted(self, candidate_key: str) -> bool:
        with self._lock:
            payload = self._load_all()
            return candidate_key in set(payload.get("blacklist") or [])

    def get(self, candidate_key: str) -> dict[str, Any] | None:
        with self._lock:
            payload = self._load_all()
            candidate = payload.get("candidates", {}).get(candidate_key)
            return dict(candidate) if candidate else None

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._load_all()
            return [dict(item) for item in payload.get("candidates", {}).values()]

    def upsert(self, candidate_key: str, candidate: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = self._load_all()
            candidate = dict(candidate)
            occurrences = candidate.get("occurrences") or []
            if len(occurrences) > self.MAX_OCCURRENCES_PER_CANDIDATE:
                candidate["occurrences"] = occurrences[-self.MAX_OCCURRENCES_PER_CANDIDATE:]
            payload["candidates"][candidate_key] = candidate
            self._save_all(payload)
            return dict(candidate)

    def add_to_blacklist(self, candidate_key: str) -> None:
        with self._lock:
            payload = self._load_all()
            blacklist = set(payload.get("blacklist") or [])
            blacklist.add(candidate_key)
            payload["blacklist"] = sorted(blacklist)
            self._save_all(payload)


class _MysqlCandidateMetricRepository:
    """MySQL 权威存储：`candidate_metrics` 表，见
    migrations/002_blessed_formula_and_candidate_metrics.sql（或 `ensure_table()` 自动建表）。

    `upsert` 用 `SELECT ... FOR UPDATE` 事务做行级锁定的读-改-写：同一 `candidate_key` 的并发
    写入会被 MySQL 行锁天然序列化，不依赖任何 Python 进程内锁，跨 worker 一致。
    """

    MAX_OCCURRENCES_PER_CANDIDATE = 50
    # MySQL 不可用时的短时熔断窗口：这段时间内的后续调用直接跳过连接尝试，避免每次调用都
    # 重新等一次 MYSQL_CONNECT_TIMEOUT（默认 10s），把故障态的延迟拖进请求同步路径。
    _CIRCUIT_BREAKER_SECONDS = 30.0

    def __init__(self) -> None:
        self._fallback = _JsonCandidateMetricRepository()
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
                "candidate_metric_repository stage=mysql status=unavailable "
                "fallback=json_single_process circuit_breaker_seconds=%.0f error=%s",
                self._CIRCUIT_BREAKER_SECONDS,
                exc,
            )
            self._mysql_warned = True

    def _mark_success(self) -> None:
        if self._mysql_warned:
            logger.info("candidate_metric_repository stage=mysql status=recovered")
            self._mysql_warned = False

    @staticmethod
    def _row_to_candidate(row: dict[str, Any]) -> dict[str, Any]:
        occurrences_raw = row.get("occurrences_json") or "[]"
        try:
            occurrences = json.loads(occurrences_raw)
        except Exception:
            occurrences = []
        return {
            "candidate_key": row.get("candidate_key"),
            "metric_guess": row.get("metric_guess"),
            "label": row.get("label", ""),
            "unit_guess": row.get("unit_guess", ""),
            "status": row.get("status", "shadow"),
            "occurrence_count": int(row.get("occurrence_count") or 0),
            "distinct_project_count": int(row.get("distinct_project_count") or 0),
            "occurrences": occurrences,
            "promoted_metric_id": row.get("promoted_metric_id"),
            "reviewed_by": row.get("reviewed_by"),
            "review_note": row.get("review_note"),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }

    def is_blacklisted(self, candidate_key: str) -> bool:
        if not self._mysql_available():
            return self._fallback.is_blacklisted(candidate_key)
        try:
            conn = self._connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT blacklisted FROM candidate_metrics WHERE candidate_key=%s",
                        (candidate_key,),
                    )
                    row = cursor.fetchone()
                    self._mark_success()
                    return bool(row and row[0])
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            self._warn_fallback(exc)
            return self._fallback.is_blacklisted(candidate_key)

    def get(self, candidate_key: str) -> dict[str, Any] | None:
        if not self._mysql_available():
            return self._fallback.get(candidate_key)
        try:
            import pymysql.cursors

            conn = self._connection()
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM candidate_metrics WHERE candidate_key=%s",
                        (candidate_key,),
                    )
                    row = cursor.fetchone()
                    self._mark_success()
                    return self._row_to_candidate(row) if row else None
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            self._warn_fallback(exc)
            return self._fallback.get(candidate_key)

    def list_all(self) -> list[dict[str, Any]]:
        if not self._mysql_available():
            return self._fallback.list_all()
        try:
            import pymysql.cursors

            conn = self._connection()
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("SELECT * FROM candidate_metrics")
                    rows = cursor.fetchall() or []
                    self._mark_success()
                    return [self._row_to_candidate(row) for row in rows]
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            self._warn_fallback(exc)
            return self._fallback.list_all()

    def upsert(self, candidate_key: str, candidate: dict[str, Any]) -> dict[str, Any]:
        if not self._mysql_available():
            return self._fallback.upsert(candidate_key, candidate)
        try:
            import pymysql.cursors

            candidate = dict(candidate)
            occurrences = candidate.get("occurrences") or []
            if len(occurrences) > self.MAX_OCCURRENCES_PER_CANDIDATE:
                occurrences = occurrences[-self.MAX_OCCURRENCES_PER_CANDIDATE:]
                candidate["occurrences"] = occurrences
            occurrences_json = json.dumps(occurrences, ensure_ascii=False)

            conn = self._connection()
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    # 行级锁定：同一 candidate_key 的并发写入在此序列化，跨 worker 安全。
                    # 不显式调用 conn.begin()——和仓库里其它 repository 一致，pymysql
                    # 连接默认非 autocommit，第一条语句本身就会隐式开启事务。
                    cursor.execute(
                        "SELECT id FROM candidate_metrics WHERE candidate_key=%s FOR UPDATE",
                        (candidate_key,),
                    )
                    existing = cursor.fetchone()
                    params = (
                        candidate.get("metric_guess") or candidate_key,
                        str(candidate.get("label") or "")[:160],
                        str(candidate.get("unit_guess") or "")[:32],
                        str(candidate.get("status") or "shadow")[:32],
                        int(candidate.get("occurrence_count") or len(occurrences)),
                        int(candidate.get("distinct_project_count") or 0),
                        occurrences_json,
                        candidate.get("promoted_metric_id"),
                        candidate.get("reviewed_by"),
                        candidate.get("review_note"),
                    )
                    if existing:
                        cursor.execute(
                            """
                            UPDATE candidate_metrics SET
                                metric_guess=%s, label=%s, unit_guess=%s, status=%s,
                                occurrence_count=%s, distinct_project_count=%s,
                                occurrences_json=%s, promoted_metric_id=%s,
                                reviewed_by=%s, review_note=%s
                            WHERE candidate_key=%s
                            """,
                            (*params, candidate_key),
                        )
                    else:
                        cursor.execute(
                            """
                            INSERT INTO candidate_metrics (
                                candidate_key, metric_guess, label, unit_guess, status,
                                occurrence_count, distinct_project_count, occurrences_json,
                                promoted_metric_id, reviewed_by, review_note
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (candidate_key, *params),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            self._mark_success()
            return dict(candidate)
        except Exception as exc:  # noqa: BLE001
            self._warn_fallback(exc)
            return self._fallback.upsert(candidate_key, candidate)

    def add_to_blacklist(self, candidate_key: str) -> None:
        if not self._mysql_available():
            self._fallback.add_to_blacklist(candidate_key)
            return
        try:
            conn = self._connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE candidate_metrics SET blacklisted=1 WHERE candidate_key=%s",
                        (candidate_key,),
                    )
                conn.commit()
            finally:
                conn.close()
            self._mark_success()
        except Exception as exc:  # noqa: BLE001
            self._warn_fallback(exc)
            self._fallback.add_to_blacklist(candidate_key)


def _build_repository():
    backend = str(getattr(settings, "CANDIDATE_METRIC_STORAGE_BACKEND", "mysql") or "mysql").strip().lower()
    if backend == "json":
        logger.info(
            "candidate_metric_repository stage=init backend=json note=single_worker_only"
        )
        return _JsonCandidateMetricRepository()
    logger.info("candidate_metric_repository stage=init backend=mysql")
    return _MysqlCandidateMetricRepository()


candidate_metric_repository = _build_repository()
