"""
客户服务看板数据仓储

负责 kanban_cs_records 表的 CRUD 操作。
"""
import json
from datetime import date
from typing import Optional, List, Dict, Any

from multi_agent.backed.app.infrastructure.database.database_pool import pool
from multi_agent.backed.app.infrastructure.logging.logger import logger

# ── DDL ─────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kanban_cs_records (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    is_closed        TINYINT(1)   DEFAULT 0   COMMENT '是否结题（0=进行中，1=已结题）',
    record_type      VARCHAR(16)  NOT NULL    COMMENT '记录类型：售前/售后',
    customer_name    VARCHAR(128) COMMENT '客户名称',
    project_name     VARCHAR(256) COMMENT '项目名称（售前）',
    product_no       VARCHAR(128) COMMENT '产品货号',
    case_type        VARCHAR(64)  COMMENT '类型（科服/科研/临检/肿瘤/生殖等）',
    problem_category VARCHAR(128) COMMENT '问题分类（组级 Sheet 特有）',
    cause_category   VARCHAR(128) COMMENT '原因分类（组级 Sheet 特有）',
    start_date       DATE         COMMENT '开始日期',
    customer_need    TEXT         COMMENT '客户需求（售前）',
    problem          TEXT         COMMENT '客户问题/问题描述',
    analysis_note    TEXT         COMMENT '分析说明',
    solution         TEXT         COMMENT '解决方案',
    conclusion       TEXT         COMMENT '进展/结论/原因',
    product_line     VARCHAR(128) COMMENT '产品线（多值逗号分隔）',
    owner            VARCHAR(64)  COMMENT '生信负责人',
    attachments      JSON         COMMENT '结果文件列表',
    attention_point  TEXT         COMMENT '关注点',
    source_sheet     VARCHAR(64)  COMMENT '来源 Sheet',
    embedding_text   TEXT         COMMENT '向量化拼接文本',
    created_at       DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_record_type (record_type),
    INDEX idx_is_closed (is_closed),
    INDEX idx_customer (customer_name),
    INDEX idx_product_line (product_line(64)),
    INDEX idx_product_no (product_no),
    INDEX idx_owner (owner),
    INDEX idx_start_date (start_date),
    FULLTEXT INDEX ft_cs (problem, solution, conclusion, customer_need, analysis_note)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客户服务看板记录'
"""


def ensure_table() -> None:
    """启动时调用，确保 kanban_cs_records 表存在，并迁移添加 extra_data 列。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
            try:
                cur.execute(
                    "ALTER TABLE kanban_cs_records ADD COLUMN extra_data JSON NULL COMMENT '自定义扩展字段'"
                )
            except Exception:
                pass
        conn.commit()
        logger.info("[KanbanCsRepository] kanban_cs_records 表就绪")
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
    for date_field in ("start_date",):
        if isinstance(d.get(date_field), date):
            d[date_field] = str(d[date_field])
    for dt_field in ("created_at", "updated_at"):
        if d.get(dt_field) and hasattr(d[dt_field], "isoformat"):
            d[dt_field] = d[dt_field].isoformat()
    if "is_closed" in d:
        d["is_closed"] = int(d["is_closed"]) if d["is_closed"] is not None else 0
    return d


def _build_embedding_text(data: Dict[str, Any]) -> str:
    parts = [
        data.get("customer_name", ""),
        data.get("product_no", ""),
        data.get("product_line", ""),
        data.get("problem", ""),
        data.get("customer_need", ""),
        data.get("analysis_note", ""),
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
    record_type: Optional[str] = None,
    is_closed: Optional[int] = None,
    customer_name: Optional[str] = None,
    product_no: Optional[str] = None,
    case_type: Optional[str] = None,
    product_line: Optional[str] = None,
    owner: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    where_clauses = []
    params = []

    if record_type:
        where_clauses.append("record_type = %s")
        params.append(record_type)
    if is_closed is not None:
        where_clauses.append("is_closed = %s")
        params.append(is_closed)
    if customer_name:
        where_clauses.append("customer_name LIKE %s")
        params.append(f"%{customer_name}%")
    if product_no:
        where_clauses.append("product_no LIKE %s")
        params.append(f"%{product_no}%")
    if case_type:
        where_clauses.append("case_type = %s")
        params.append(case_type)
    if product_line:
        where_clauses.append("FIND_IN_SET(%s, product_line) > 0")
        params.append(product_line)
    if owner:
        where_clauses.append("owner LIKE %s")
        params.append(f"%{owner}%")
    if date_from:
        where_clauses.append("start_date >= %s")
        params.append(date_from)
    if date_to:
        where_clauses.append("start_date <= %s")
        params.append(date_to)
    if keyword:
        where_clauses.append(
            "(customer_name LIKE %s OR problem LIKE %s OR solution LIKE %s OR conclusion LIKE %s OR customer_need LIKE %s)"
        )
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw, kw, kw])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    offset = (page - 1) * page_size

    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM kanban_cs_records {where_sql}", params)
            total = cur.fetchone()[0]

            cur.execute(
                f"SELECT * FROM kanban_cs_records {where_sql} ORDER BY start_date DESC, id DESC LIMIT %s OFFSET %s",
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
            cur.execute("SELECT * FROM kanban_cs_records WHERE id = %s", (record_id,))
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
        INSERT INTO kanban_cs_records
            (is_closed, record_type, customer_name, project_name, product_no,
             case_type, problem_category, cause_category, start_date,
             customer_need, problem, analysis_note, solution, conclusion,
             product_line, owner, attachments, attention_point, source_sheet,
             embedding_text, extra_data)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    params = (
        int(data.get("is_closed", 0)),
        data.get("record_type", "售后"),
        data.get("customer_name"),
        data.get("project_name"),
        data.get("product_no"),
        data.get("case_type"),
        data.get("problem_category"),
        data.get("cause_category"),
        data.get("start_date"),
        data.get("customer_need"),
        data.get("problem"),
        data.get("analysis_note"),
        data.get("solution"),
        data.get("conclusion"),
        data.get("product_line"),
        data.get("owner"),
        attachments,
        data.get("attention_point"),
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
    text_fields = {"problem", "solution", "conclusion", "customer_need", "analysis_note",
                   "customer_name", "product_no", "product_line", "extra_data"}
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
            merged["extra_data"] = {**(existing.get("extra_data") or {}), **incoming}
        data["embedding_text"] = _build_embedding_text(merged)

    if "attachments" in data and not isinstance(data["attachments"], str):
        data["attachments"] = json.dumps(data["attachments"], ensure_ascii=False)

    allowed = {
        "is_closed", "record_type", "customer_name", "project_name", "product_no",
        "case_type", "problem_category", "cause_category", "start_date", "customer_need",
        "problem", "analysis_note", "solution", "conclusion", "product_line", "owner",
        "attachments", "attention_point", "source_sheet", "embedding_text",
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
                f"UPDATE kanban_cs_records SET {', '.join(set_clauses)} WHERE id = %s",
                params,
            )
        conn.commit()
    finally:
        conn.close()

    return get_record(record_id)


def set_closed(record_id: int, is_closed: bool) -> Optional[Dict[str, Any]]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE kanban_cs_records SET is_closed=%s WHERE id=%s",
                (int(is_closed), record_id),
            )
        conn.commit()
    finally:
        conn.close()
    return get_record(record_id)


def delete_record(record_id: int) -> bool:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kanban_cs_records WHERE id = %s", (record_id,))
            affected = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return affected > 0


def get_customers() -> List[str]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT customer_name FROM kanban_cs_records WHERE customer_name IS NOT NULL AND customer_name != '' ORDER BY customer_name")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def get_owners() -> List[str]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT owner FROM kanban_cs_records WHERE owner IS NOT NULL AND owner != '' ORDER BY owner")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def get_case_types() -> List[str]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT case_type FROM kanban_cs_records WHERE case_type IS NOT NULL AND case_type != '' ORDER BY case_type")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def get_product_nos() -> List[str]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT product_no FROM kanban_cs_records WHERE product_no IS NOT NULL AND product_no != '' ORDER BY product_no")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def get_stats() -> Dict[str, Any]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM kanban_cs_records")
            total = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM kanban_cs_records WHERE is_closed=1")
            closed = cur.fetchone()[0]

            cur.execute("SELECT record_type, COUNT(*) cnt FROM kanban_cs_records GROUP BY record_type")
            by_type = [{"record_type": r[0], "count": r[1]} for r in cur.fetchall()]

            cur.execute("SELECT owner, COUNT(*) cnt FROM kanban_cs_records WHERE owner IS NOT NULL GROUP BY owner ORDER BY cnt DESC LIMIT 10")
            by_owner = [{"owner": r[0], "count": r[1]} for r in cur.fetchall()]

    finally:
        conn.close()

    return {
        "total": total,
        "closed": closed,
        "active": total - closed,
        "close_rate": round(closed / total * 100, 1) if total else 0,
        "by_type": by_type,
        "by_owner": by_owner,
    }


def bulk_insert(records: List[Dict[str, Any]]) -> int:
    """批量插入，以 (customer_name, product_no, start_date) 为去重键，返回实际插入数。"""
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
                    "SELECT id FROM kanban_cs_records WHERE customer_name=%s AND product_no=%s AND start_date=%s",
                    (rec.get("customer_name"), rec.get("product_no"), rec.get("start_date")),
                )
                if cur.fetchone():
                    continue

                cur.execute(
                    """INSERT INTO kanban_cs_records
                       (is_closed,record_type,customer_name,project_name,product_no,
                        case_type,problem_category,cause_category,start_date,
                        customer_need,problem,analysis_note,solution,conclusion,
                        product_line,owner,attachments,attention_point,source_sheet,embedding_text)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        int(rec.get("is_closed", 0)),
                        rec.get("record_type", "售后"),
                        rec.get("customer_name"),
                        rec.get("project_name"),
                        rec.get("product_no"),
                        rec.get("case_type"),
                        rec.get("problem_category"),
                        rec.get("cause_category"),
                        rec.get("start_date"),
                        rec.get("customer_need"),
                        rec.get("problem"),
                        rec.get("analysis_note"),
                        rec.get("solution"),
                        rec.get("conclusion"),
                        rec.get("product_line"),
                        rec.get("owner"),
                        attachments,
                        rec.get("attention_point"),
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
                    "SELECT * FROM kanban_cs_records WHERE MATCH(problem,solution,conclusion,customer_need,analysis_note) AGAINST(%s IN BOOLEAN MODE) LIMIT %s",
                    (query, limit),
                )
            except Exception:
                like = f"%{query}%"
                cur.execute(
                    "SELECT * FROM kanban_cs_records WHERE problem LIKE %s OR solution LIKE %s OR conclusion LIKE %s LIMIT %s",
                    (like, like, like, limit),
                )
            rows = cur.fetchall()
            return [_row_to_dict(r, cur) for r in rows]
    finally:
        conn.close()
