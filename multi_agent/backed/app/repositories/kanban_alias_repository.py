"""
看板字段别名仓储

负责 kanban_field_aliases 表的建表、初始化数据插入与查询。
"""
from typing import Dict, List

from multi_agent.backed.app.infrastructure.database.database_pool import pool
from multi_agent.backed.app.infrastructure.logging.logger import logger

# ── DDL ─────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kanban_field_aliases (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    scope     VARCHAR(16) NOT NULL COMMENT '适用范围：rd / cs / both',
    canonical VARCHAR(64) NOT NULL COMMENT '标准字段名',
    alias     VARCHAR(128) NOT NULL COMMENT '别名/口语表达',
    UNIQUE KEY uk_scope_alias (scope, alias)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_SEED_DATA = [
    # scope='both'
    ("both", "product_line", "产品线"),
    ("both", "product_line", "产品"),
    ("both", "owner",        "负责人"),
    ("both", "owner",        "生信负责人"),
    ("both", "problem",      "问题"),
    ("both", "conclusion",   "结论"),
    ("both", "conclusion",   "进展"),
    # scope='rd'
    ("rd", "project_name",  "项目"),
    ("rd", "project_name",  "开发项目"),
    ("rd", "reagent_owner", "试剂负责人"),
    ("rd", "reagent_owner", "试剂端"),
    ("rd", "team_group",    "项目组"),
    ("rd", "team_group",    "归属组"),
    ("rd", "progress_date", "进展日期"),
    ("rd", "progress_date", "日期"),
    ("rd", "solution",      "解决方案"),
    # scope='cs'
    ("cs", "customer_name",   "客户"),
    ("cs", "customer_name",   "客户名"),
    ("cs", "product_no",      "产品货号"),
    ("cs", "product_no",      "货号"),
    ("cs", "record_type",     "售前售后"),
    ("cs", "case_type",       "业务类型"),
    ("cs", "case_type",       "科服"),
    ("cs", "case_type",       "科研"),
    ("cs", "is_closed",       "是否结题"),
    ("cs", "is_closed",       "结题"),
    ("cs", "problem_category","问题分类"),
    ("cs", "cause_category",  "原因分类"),
    ("cs", "start_date",      "开始日期"),
    ("cs", "start_date",      "时间"),
    ("cs", "solution",        "解决方案"),
    ("cs", "conclusion",      "原因"),
    ("cs", "attention_point", "关注点"),
]


def ensure_table() -> None:
    """启动时调用，确保 kanban_field_aliases 表存在并已填充初始数据。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
            for scope, canonical, alias in _SEED_DATA:
                cur.execute(
                    "INSERT IGNORE INTO kanban_field_aliases (scope, canonical, alias) VALUES (%s,%s,%s)",
                    (scope, canonical, alias),
                )
        conn.commit()
        logger.info("[KanbanAliasRepository] kanban_field_aliases 表就绪")
    finally:
        conn.close()


def get_aliases(scope: str) -> Dict[str, str]:
    """
    返回 {alias: canonical} 映射，包含 scope 对应别名 + both 通用别名。
    """
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT alias, canonical FROM kanban_field_aliases WHERE scope IN (%s, 'both')",
                (scope,),
            )
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def get_aliases_text(scope: str) -> str:
    """返回供 Prompt 使用的别名文本列表，格式：alias -> canonical。"""
    mapping = get_aliases(scope)
    lines = [f"  {alias} → {canonical}" for alias, canonical in sorted(mapping.items())]
    return "\n".join(lines)


def list_all() -> List[dict]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, scope, canonical, alias FROM kanban_field_aliases ORDER BY scope, canonical")
            cols = ["id", "scope", "canonical", "alias"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()
