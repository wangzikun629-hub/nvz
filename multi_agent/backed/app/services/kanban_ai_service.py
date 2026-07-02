"""
看板双路 AI 查询服务

scope='rd'  → kanban_rd_records
scope='cs'  → kanban_cs_records

路由逻辑：
  意图分类 → "sql"（统计/聚合）或 "rag"（语义/内容）
  sql路径 → LLM 生成 SQL → 安全校验 → 执行 → 自然语言转述
  rag路径 → FULLTEXT 全文检索 → Top-K → LLM 归纳
"""
import re
import json
import logging
from typing import Any, Dict, Tuple

from openai import AsyncOpenAI

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.database.database_pool import pool
from multi_agent.backed.app.repositories import kanban_alias_repository

logger = logging.getLogger(__name__)

# ── SQL 安全校验 ──────────────────────────────────────────────────────────────

ALLOWED_TABLES = {"kanban_rd_records", "kanban_cs_records"}
_BLOCKED = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|EXEC|EXECUTE|UNION)\b",
    re.IGNORECASE,
)


def validate_sql(sql: str) -> None:
    stripped = sql.strip().upper()
    if not stripped.startswith("SELECT"):
        raise ValueError("只允许 SELECT 语句")
    if _BLOCKED.search(sql):
        raise ValueError("SQL 包含禁止关键字")
    tables = re.findall(r"FROM\s+(\w+)", sql, re.IGNORECASE)
    for t in tables:
        if t.lower() not in ALLOWED_TABLES:
            raise ValueError(f"不允许查询表：{t}")


# ── SQL Context ──────────────────────────────────────────────────────────────

_RD_SQL_CONTEXT = """
表名：kanban_rd_records
关键字段：
- product_line  (产品线，单值，精确匹配或 LIKE)
- project_name  (开发项目名称)
- owner         (生信负责人)
- reagent_owner (试剂负责人)
- team_group    (归属项目组，如 51组/52组)
- progress_date (DATE，格式 YYYY-MM-DD)
- 文本字段（不参与聚合）：problem, solution, conclusion

字段别名：
{aliases}

注意：只输出 SELECT 语句，禁止其他任何 SQL 操作
"""

_CS_SQL_CONTEXT = """
表名：kanban_cs_records
关键字段：
- is_closed     (0=进行中, 1=已结题)
- record_type   (售前 / 售后)
- customer_name (客户名称，用 LIKE '%名%' 模糊匹配)
- product_no    (产品货号，如 TD904 / NR616，用 LIKE)
- case_type     (科服/科研/临检/肿瘤/生殖)
- product_line  (产品线，多值逗号分隔，用 FIND_IN_SET(?, product_line))
- owner         (生信负责人)
- start_date    (DATE，格式 YYYY-MM-DD)
- problem_category / cause_category  (问题/原因分类)
- 文本字段（不参与聚合）：problem, solution, conclusion, customer_need, analysis_note

字段别名：
{aliases}

注意：
1. 只输出 SELECT 语句，禁止其他任何 SQL 操作
2. 仅当用户明确说"进行中"或"未结题"时才加 WHERE is_closed=0；默认查全部
3. 产品线多值匹配用 FIND_IN_SET(?, product_line) 而非 LIKE
4. 人名用 LIKE '%姓名%'，日期范围用 BETWEEN
"""

# ── 意图分类 ──────────────────────────────────────────────────────────────────

_INTENT_PROMPT = """你是一个数据查询意图分类器。
用户的问题是关于看板数据库的。
如果问题是统计类/聚合类（如"多少条"、"分布"、"谁负责最多"、"按产品线统计"），输出 sql。
如果问题是内容类/语义类（如"关于XX的问题有哪些"、"找和YY相关的案例"、"有什么解决方案"），输出 rag。
只输出 sql 或 rag 两个词之一，不要任何解释。

用户问题：{question}"""

# ── LLM 客户端 ────────────────────────────────────────────────────────────────

def _get_client() -> AsyncOpenAI:
    if settings.SF_API_KEY and settings.SF_BASE_URL:
        return AsyncOpenAI(api_key=settings.SF_API_KEY, base_url=settings.SF_BASE_URL)
    if settings.AL_BAILIAN_API_KEY and settings.AL_BAILIAN_BASE_URL:
        return AsyncOpenAI(api_key=settings.AL_BAILIAN_API_KEY, base_url=settings.AL_BAILIAN_BASE_URL)
    raise RuntimeError("未配置任何 AI 服务（SF 或 阿里百炼）")


def _get_model() -> str:
    return settings.MAIN_MODEL_NAME or "Qwen/Qwen3-32B"


# ── 核心查询 ──────────────────────────────────────────────────────────────────

async def _classify_intent(question: str) -> str:
    client = _get_client()
    resp = await client.chat.completions.create(
        model=_get_model(),
        messages=[{"role": "user", "content": _INTENT_PROMPT.format(question=question)}],
        max_tokens=10,
        temperature=0,
        extra_body={"enable_thinking": False},
    )
    raw = (resp.choices[0].message.content or "rag").strip().lower()
    return "sql" if "sql" in raw else "rag"


async def _run_sql_path(question: str, scope: str) -> Tuple[str, str, str]:
    """返回 (answer, route, sql)"""
    aliases_text = kanban_alias_repository.get_aliases_text(scope)
    ctx = _RD_SQL_CONTEXT if scope == "rd" else _CS_SQL_CONTEXT
    sql_context = ctx.format(aliases=aliases_text)

    client = _get_client()

    # 生成 SQL
    sql_resp = await client.chat.completions.create(
        model=_get_model(),
        messages=[
            {"role": "system", "content": f"你是 SQL 生成专家。根据以下数据库结构生成 SQL 查询语句：\n{sql_context}"},
            {"role": "user", "content": question},
        ],
        max_tokens=500,
        temperature=0,
        extra_body={"enable_thinking": False},
    )
    raw_sql = (sql_resp.choices[0].message.content or "").strip()

    # 提取 SQL（去掉 markdown 代码块）
    sql_match = re.search(r"```(?:sql)?\s*(SELECT[\s\S]+?)```", raw_sql, re.IGNORECASE)
    sql = sql_match.group(1).strip() if sql_match else raw_sql.strip()
    if not sql.upper().startswith("SELECT"):
        # 找第一个 SELECT
        idx = sql.upper().find("SELECT")
        sql = sql[idx:] if idx >= 0 else sql

    # 安全校验
    try:
        validate_sql(sql)
    except ValueError as e:
        logger.warning(f"[KanbanAI] SQL 安全校验失败：{e}，降级为 RAG")
        return await _run_rag_path(question, scope) + ("rag_fallback",)

    # 执行 SQL
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            result_data = [dict(zip(cols, row)) for row in rows[:50]]
    except Exception as e:
        logger.warning(f"[KanbanAI] SQL 执行失败：{e}，降级为 RAG")
        answer, route, _ = await _run_rag_path(question, scope)
        return answer, "rag_fallback", sql
    finally:
        conn.close()

    if not result_data:
        return "查询结果为空，未找到相关数据。", "sql", sql

    # 自然语言转述
    result_json = json.dumps(result_data, ensure_ascii=False, indent=2, default=str)
    nl_resp = await client.chat.completions.create(
        model=_get_model(),
        messages=[
            {"role": "system", "content": "你是数据解读专家，请将查询结果用简洁、清晰的中文回答用户问题。不要输出原始 JSON，用自然语言归纳。"},
            {"role": "user", "content": f"用户问题：{question}\n\nSQL 查询结果：\n{result_json}"},
        ],
        max_tokens=1000,
        temperature=0.3,
        extra_body={"enable_thinking": False},
    )
    answer = (nl_resp.choices[0].message.content or "").strip()
    return answer, "sql", sql


async def _run_rag_path(question: str, scope: str) -> Tuple[str, str, None]:
    """全文检索 + LLM 归纳，返回 (answer, route, None)"""
    from multi_agent.backed.app.repositories import kanban_rd_repository, kanban_cs_repository

    if scope == "rd":
        records = kanban_rd_repository.fulltext_search(question, limit=15)
    else:
        records = kanban_cs_repository.fulltext_search(question, limit=15)

    if not records:
        return "未找到与问题相关的记录，请尝试换个关键词。", "rag", None

    # 格式化记录为简洁文本
    context_lines = []
    for i, rec in enumerate(records[:10], 1):
        if scope == "rd":
            context_lines.append(
                f"[{i}] 产品线:{rec.get('product_line','')} 项目:{rec.get('project_name','')} "
                f"负责人:{rec.get('owner','')} 问题:{rec.get('problem','')[:100]} "
                f"结论:{rec.get('conclusion','')[:100]}"
            )
        else:
            context_lines.append(
                f"[{i}] 客户:{rec.get('customer_name','')} 货号:{rec.get('product_no','')} "
                f"问题:{rec.get('problem','')[:100]} 结论:{rec.get('conclusion','')[:100]} "
                f"状态:{'已结题' if rec.get('is_closed') else '进行中'}"
            )

    context = "\n".join(context_lines)
    client = _get_client()

    resp = await client.chat.completions.create(
        model=_get_model(),
        messages=[
            {"role": "system", "content": "你是生物信息学项目知识库助手，根据以下相关记录回答用户问题，引用具体案例，用中文回答。"},
            {"role": "user", "content": f"问题：{question}\n\n相关记录：\n{context}"},
        ],
        max_tokens=1000,
        temperature=0.3,
        extra_body={"enable_thinking": False},
    )
    answer = (resp.choices[0].message.content or "").strip()
    return answer, "rag", None


async def query(question: str, scope: str) -> Dict[str, Any]:
    """
    主入口。scope='rd' 或 'cs'。
    返回 {"answer": str, "route": str, "sql": str|None}
    """
    if not question.strip():
        return {"answer": "请输入问题。", "route": "none", "sql": None}

    try:
        intent = await _classify_intent(question)
    except Exception as e:
        logger.warning(f"[KanbanAI] 意图分类失败 ({e})，降级为 RAG")
        intent = "rag"

    if intent == "sql":
        try:
            answer, route, sql = await _run_sql_path(question, scope)
            return {"answer": answer, "route": route, "sql": sql}
        except Exception as e:
            logger.error(f"[KanbanAI] SQL 路径异常 ({e})，降级为 RAG")

    try:
        answer, route, sql = await _run_rag_path(question, scope)
        return {"answer": answer, "route": route, "sql": sql}
    except Exception as e:
        logger.error(f"[KanbanAI] RAG 路径异常：{e}")
        return {"answer": f"查询失败：{str(e)}", "route": "error", "sql": None}
