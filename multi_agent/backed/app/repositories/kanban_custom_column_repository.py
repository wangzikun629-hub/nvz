"""
看板自定义列定义仓储

负责 kanban_custom_columns 表的建表与 CRUD。
每个看板（scope='rd'/'cs'）可以自由新增展示列，实际值存在对应记录表的
extra_data JSON 字段里，以 field_key 为 key。这张表只记录"有哪些自定义列、
显示名叫什么、排在第几个"，是列的元数据，不存业务数据本身。
"""
import uuid
from typing import Any, Dict, List, Optional

from multi_agent.backed.app.infrastructure.database.database_pool import pool
from multi_agent.backed.app.infrastructure.logging.logger import logger

# ── DDL ─────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kanban_custom_columns (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    scope      VARCHAR(8)  NOT NULL COMMENT '适用看板：rd / cs',
    field_key  VARCHAR(64) NOT NULL COMMENT '存入 extra_data 的字段 key',
    label      VARCHAR(64) NOT NULL COMMENT '列显示名称',
    sort_order INT         NOT NULL DEFAULT 0,
    created_at DATETIME    DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_scope_key (scope, field_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='看板自定义列定义'
"""


def ensure_table() -> None:
    """启动时调用，确保 kanban_custom_columns 表存在。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        conn.commit()
        logger.info("[KanbanCustomColumnRepository] kanban_custom_columns 表就绪")
    finally:
        conn.close()


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "scope": row[1],
        "field_key": row[2],
        "label": row[3],
        "sort_order": row[4],
    }


def list_columns(scope: str) -> List[Dict[str, Any]]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, scope, field_key, label, sort_order FROM kanban_custom_columns "
                "WHERE scope = %s ORDER BY sort_order ASC, id ASC",
                (scope,),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def create_column(scope: str, label: str) -> Dict[str, Any]:
    label = (label or "").strip()[:64]
    if not label:
        raise ValueError("列名称不能为空")

    field_key = f"custom_{uuid.uuid4().hex[:10]}"
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM kanban_custom_columns WHERE scope = %s",
                (scope,),
            )
            next_order = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO kanban_custom_columns (scope, field_key, label, sort_order) VALUES (%s,%s,%s,%s)",
                (scope, field_key, label, next_order),
            )
            new_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {"id": new_id, "scope": scope, "field_key": field_key, "label": label, "sort_order": next_order}


def rename_column(scope: str, field_key: str, label: str) -> Optional[Dict[str, Any]]:
    label = (label or "").strip()[:64]
    if not label:
        raise ValueError("列名称不能为空")
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE kanban_custom_columns SET label = %s WHERE scope = %s AND field_key = %s",
                (label, scope, field_key),
            )
            affected = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if not affected:
        return None
    cols = list_columns(scope)
    return next((c for c in cols if c["field_key"] == field_key), None)


def delete_column(scope: str, field_key: str) -> bool:
    """删除列定义。注意：不会清理各记录 extra_data 里已存的该字段值，只是列不再展示。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kanban_custom_columns WHERE scope = %s AND field_key = %s",
                (scope, field_key),
            )
            affected = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return affected > 0
