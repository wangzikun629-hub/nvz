"""
研发看板数据仓储

负责 kanban_rd_records 表的 CRUD 操作。
"""
import json
from datetime import date
from typing import Optional, List, Dict, Any

from multi_agent.backed.app.infrastructure.database.database_pool import pool
from multi_agent.backed.app.infrastructure.logging.logger import logger

# ── DDL ─────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kanban_rd_records (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    product_line  VARCHAR(64)  NOT NULL COMMENT '产品线',
    project_name  VARCHAR(128) NOT NULL COMMENT '开发项目名称',
    project_bg    TEXT         COMMENT '项目背景',
    progress_date DATE         COMMENT '进展日期',
    team_group    VARCHAR(64)  COMMENT '归属项目组',
    reagent_owner VARCHAR(64)  COMMENT '试剂负责人',
    owner         VARCHAR(64)  COMMENT '生信负责人',
    problem       TEXT         COMMENT '问题描述',
    solution      TEXT         COMMENT '解决方案/生信进展',
    conclusion    TEXT         COMMENT '进展/结论',
    exp_plan      TEXT         COMMENT '实验计划',
    attachments   JSON         COMMENT '结果文件列表',
    source_sheet  VARCHAR(64)  COMMENT '来源 Sheet',
    embedding_text TEXT        COMMENT '向量化拼接文本',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_product_line (product_line),
    INDEX idx_owner (owner),
    INDEX idx_progress_date (progress_date),
    FULLTEXT INDEX ft_rd (problem, solution, conclusion)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='研发看板记录'
"""

_ALTER_EXTRA_DATA = "ALTER TABLE kanban_rd_records ADD COLUMN extra_data JSON NULL COMMENT '自定义扩展字段'"


def ensure_table() -> None:
    """启动时调用，确保 kanban_rd_records 表存在，并迁移添加 extra_data 列。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
            # 添加 extra_data 列（已存在则静默跳过）
            try:
                cur.execute(_ALTER_EXTRA_DATA)
            except Exception:
                pass
        conn.commit()
        logger.info("[KanbanRdRepository] kanban_rd_records 表就绪")
    finally:
        conn.close()


# ── 工具 ─────────────────────────────────────────────────────────────────────

def _row_to_dict(row, cursor) -> Dict[str, Any]:
    cols = [d[0] for d in cursor.description]
    d = dict(zip(cols, row))
    if isinstance(d.get("attachments"), str):
        try:
            d["attachments"] = json.loads(d["attachments"])
        except Exception:
            d["attachments"] = []
    if isinstance(d.get("extra_data"), str):
        try:
            d["extra_data"] = json.loads(d["extra_data"])
        except Exception:
            d["extra_data"] = {}
    if d.get("extra_data") is None:
        d["extra_data"] = {}
    if isinstance(d.get("progress_date"), date):
        d["progress_date"] = str(d["progress_date"])
    if isinstance(d.get("created_at"), object) and hasattr(d.get("created_at"), "isoformat"):
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
    if isinstance(d.get("updated_at"), object) and hasattr(d.get("updated_at"), "isoformat"):
        d["updated_at"] = d["updated_at"].isoformat() if d.get("updated_at") else None
    return d


def _build_embedding_text(data: Dict[str, Any]) -> str:
    parts = [
        data.get("product_line", ""),
        data.get("project_name", ""),
        data.get("project_bg", ""),
        data.get("problem", ""),
        data.get("solution", ""),
        data.get("conclusion", ""),
    ]
    # 把自定义列的值也拼入，使 AI 问答能检索到
    extra = data.get("extra_data") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except Exception:
            extra = {}
    for v in extra.values():
        if v and isinstance(v, str):
            parts.append(v)
    return " ".join(p for p in parts if p)


# ── CRUD ─────────────────────────────────────────────────────────────────────

def list_records(
    product_line: Optional[str] = None,
    owner: Optional[str] = None,
    team_group: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    where_clauses = []
    params = []

    if product_line:
        where_clauses.append("product_line LIKE %s")
        params.append(f"%{product_line}%")
    if owner:
        where_clauses.append("owner LIKE %s")
        params.append(f"%{owner}%")
    if team_group:
        where_clauses.append("team_group LIKE %s")
        params.append(f"%{team_group}%")
    if keyword:
        where_clauses.append("(project_name LIKE %s OR problem LIKE %s OR solution LIKE %s OR conclusion LIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    if date_from:
        where_clauses.append("progress_date >= %s")
        params.append(date_from)
    if date_to:
        where_clauses.append("progress_date <= %s")
        params.append(date_to)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    offset = (page - 1) * page_size

    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM kanban_rd_records {where_sql}", params)
            total = cur.fetchone()[0]

            cur.execute(
                f"SELECT * FROM kanban_rd_records {where_sql} ORDER BY progress_date DESC, id DESC LIMIT %s OFFSET %s",
                params + [page_size, offset],
            )
            rows = cur.fetchall()
            records = [_row_to_dict(r, cur) for r in rows]
    finally:
        conn.close()

    return {"total": total, "page": page, "page_size": page_size, "records": records}


def get_record(record_id: int) -> Optional[Dict[str, Any]]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM kanban_rd_records WHERE id = %s", (record_id,))
            row = cur.fetchone()
            return _row_to_dict(row, cur) if row else None
    finally:
        conn.close()


def create_record(data: Dict[str, Any]) -> Dict[str, Any]:
    data["embedding_text"] = _build_embedding_text(data)
    attachments = data.get("attachments") or []
    if not isinstance(attachments, str):
        attachments = json.dumps(attachments, ensure_ascii=False)

    extra_data = data.get("extra_data") or {}
    if not isinstance(extra_data, str):
        extra_data = json.dumps(extra_data, ensure_ascii=False)

    sql = """
        INSERT INTO kanban_rd_records
            (product_line, project_name, project_bg, progress_date, team_group,
             reagent_owner, owner, problem, solution, conclusion, exp_plan,
             attachments, source_sheet, embedding_text, extra_data)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    params = (
        data.get("product_line", ""),
        data.get("project_name", ""),
        data.get("project_bg"),
        data.get("progress_date"),
        data.get("team_group"),
        data.get("reagent_owner"),
        data.get("owner"),
        data.get("problem"),
        data.get("solution"),
        data.get("conclusion"),
        data.get("exp_plan"),
        attachments,
        data.get("source_sheet"),
        data.get("embedding_text"),
        extra_data,
    )

    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            new_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return get_record(new_id)


def update_record(record_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text_fields = {"problem", "solution", "conclusion", "project_bg", "project_name", "product_line", "extra_data"}
    existing = get_record(record_id) if any(k in data for k in text_fields) else None

    # extra_data 用 MySQL JSON_MERGE_PATCH 在 SQL 层原子合并，而不是 Python 侧读-改-写，
    # 避免同一行两次并发 update（例如快速连续编辑两个自定义列）互相覆盖导致丢更新。
    extra_data_patch = None
    if "extra_data" in data:
        incoming = data.pop("extra_data")
        if not isinstance(incoming, dict):
            incoming = {}
        extra_data_patch = json.dumps(incoming, ensure_ascii=False)

    if existing is not None:
        merged = {**existing, **data}
        if extra_data_patch is not None:
            # embedding_text 仅用于检索，这里用已知的 existing+incoming 拼出一个近似合并视图即可，
            # 不需要跟 JSON_MERGE_PATCH 的原子写严格一致
            merged["extra_data"] = {**(existing.get("extra_data") or {}), **incoming}
        data["embedding_text"] = _build_embedding_text(merged)

    if "attachments" in data and not isinstance(data["attachments"], str):
        data["attachments"] = json.dumps(data["attachments"], ensure_ascii=False)

    allowed = {
        "product_line", "project_name", "project_bg", "progress_date", "team_group",
        "reagent_owner", "owner", "problem", "solution", "conclusion", "exp_plan",
        "attachments", "source_sheet", "embedding_text",
    }
    set_clauses = []
    params = []
    for k, v in data.items():
        if k in allowed:
            set_clauses.append(f"{k} = %s")
            params.append(v)

    if extra_data_patch is not None:
        set_clauses.append("extra_data = JSON_MERGE_PATCH(COALESCE(extra_data, '{}'), %s)")
        params.append(extra_data_patch)

    if not set_clauses:
        return get_record(record_id)

    params.append(record_id)
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE kanban_rd_records SET {', '.join(set_clauses)} WHERE id = %s",
                params,
            )
        conn.commit()
    finally:
        conn.close()

    return get_record(record_id)


def delete_record(record_id: int) -> bool:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kanban_rd_records WHERE id = %s", (record_id,))
            affected = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return affected > 0


def get_product_lines() -> List[str]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT product_line FROM kanban_rd_records WHERE product_line != '' ORDER BY product_line")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def get_owners() -> List[str]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT owner FROM kanban_rd_records WHERE owner IS NOT NULL AND owner != '' ORDER BY owner")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def get_stats() -> Dict[str, Any]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM kanban_rd_records")
            total = cur.fetchone()[0]

            cur.execute("SELECT product_line, COUNT(*) cnt FROM kanban_rd_records GROUP BY product_line ORDER BY cnt DESC")
            by_product_line = [{"product_line": r[0], "count": r[1]} for r in cur.fetchall()]

            cur.execute("SELECT owner, COUNT(*) cnt FROM kanban_rd_records WHERE owner IS NOT NULL GROUP BY owner ORDER BY cnt DESC LIMIT 10")
            by_owner = [{"owner": r[0], "count": r[1]} for r in cur.fetchall()]

    finally:
        conn.close()

    return {"total": total, "by_product_line": by_product_line, "by_owner": by_owner}


def bulk_insert(records: List[Dict[str, Any]]) -> int:
    """批量插入，跳过 (project_name, progress_date) 已存在的记录，返回实际插入数。"""
    if not records:
        return 0
    inserted = 0
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            for rec in records:
                rec["embedding_text"] = _build_embedding_text(rec)
                attachments = rec.get("attachments") or []
                if not isinstance(attachments, str):
                    attachments = json.dumps(attachments, ensure_ascii=False)

                # 去重检查
                cur.execute(
                    "SELECT id FROM kanban_rd_records WHERE project_name=%s AND progress_date=%s",
                    (rec.get("project_name", ""), rec.get("progress_date")),
                )
                if cur.fetchone():
                    continue

                cur.execute(
                    """INSERT INTO kanban_rd_records
                       (product_line,project_name,project_bg,progress_date,team_group,
                        reagent_owner,owner,problem,solution,conclusion,exp_plan,
                        attachments,source_sheet,embedding_text)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        rec.get("product_line", ""),
                        rec.get("project_name", ""),
                        rec.get("project_bg"),
                        rec.get("progress_date"),
                        rec.get("team_group"),
                        rec.get("reagent_owner"),
                        rec.get("owner"),
                        rec.get("problem"),
                        rec.get("solution"),
                        rec.get("conclusion"),
                        rec.get("exp_plan"),
                        attachments,
                        rec.get("source_sheet"),
                        rec.get("embedding_text"),
                    ),
                )
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def fulltext_search(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """全文检索，不可用时降级为 LIKE。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT * FROM kanban_rd_records WHERE MATCH(problem,solution,conclusion) AGAINST(%s IN BOOLEAN MODE) LIMIT %s",
                    (query, limit),
                )
            except Exception:
                like = f"%{query}%"
                cur.execute(
                    "SELECT * FROM kanban_rd_records WHERE problem LIKE %s OR solution LIKE %s OR conclusion LIKE %s LIMIT %s",
                    (like, like, like, limit),
                )
            rows = cur.fetchall()
            return [_row_to_dict(r, cur) for r in rows]
    finally:
        conn.close()
