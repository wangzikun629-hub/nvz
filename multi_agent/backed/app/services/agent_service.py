import asyncio
import json
import re
import traceback
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

from agents.run import RunConfig, Runner

from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.infrastructure.tools.local.knowledge_base import (
    get_round_retrieval_trace,
    init_round_knowledge_cache,
    reset_round_knowledge_cache,
)
from multi_agent.backed.app.infrastructure.tools.mcp.mcp_pool import technical_agent_pool
from multi_agent.backed.app.multi_agent.agent_factory import (
    _build_technical_agent_input,
    consult_technical_expert,
    execute_project_business_analysis,
    get_round_project_analysis_result,
    get_round_project_progress_queue,
    init_round_project_analysis_result,
    init_round_project_request_context,
    init_round_project_progress_queue,
    init_round_technical_cache,
    init_round_technical_context,
    reset_round_project_analysis_result,
    reset_round_project_progress_queue,
    reset_round_project_request_context,
    reset_round_technical_cache,
    reset_round_technical_context,
    set_round_project_analysis_result,
    set_round_project_request_context,
    set_round_technical_context,
)
from multi_agent.backed.app.multi_agent.technical_agent import technical_agent_kb_only
from multi_agent.backed.app.multi_agent.orchestrator_agent import orchestrator_agent
from multi_agent.backed.app.multi_agent.project_progress import close_project_progress
from multi_agent.backed.app.schemas.request import ChatMessageRequest
from multi_agent.backed.app.schemas.response import ContentKind
from multi_agent.backed.app.services.followup_intent_service import followup_intent_service
from multi_agent.backed.app.services.project_locator_service import (
    project_locator_service,
)
from multi_agent.backed.app.services.project_chart_service import project_chart_service
from multi_agent.backed.app.services.project_session_state_service import (
    project_session_state_service,
)
from multi_agent.backed.app.services.rag_fast_service import RagFastService
from multi_agent.backed.app.services.session_service import session_service
from multi_agent.backed.app.services.stream_response_service import process_stream_response
from multi_agent.backed.app.utils.response_util import ResponseFactory
from multi_agent.backed.app.utils.retry_util import with_llm_retry


# ── 全局并发限流 ──────────────────────────────────────────────────────────────
# MAX_CONCURRENT_REQUESTS 通过 settings 读取（默认 30），.env 可覆盖。
# 超出时请求等待最多 10 s；仍未拿到 slot 则返回"服务器繁忙"提示，不报错。
from multi_agent.backed.app.config.settings import settings as _settings
_MAX_CONCURRENT: int = _settings.MAX_CONCURRENT_REQUESTS
_request_semaphore: asyncio.Semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
# ─────────────────────────────────────────────────────────────────────────────


def _build_stage_payload(stage: str, status: str, text: str, detail: dict | None = None) -> str:
    return json.dumps(
        {
            "type": "project_stage",
            "stage": stage,
            "status": status,
            "text": text,
            "detail": detail or {},
        },
        ensure_ascii=False,
    )


class MultiAgentService:
    MAX_CONTEXT_TURNS = 3
    NON_RETRYABLE_ERRORS = (
        "Tool not found in agent",
        "Server not initialized",
        "Failed to connect to MCP server",
        "HTTP error 500",
    )
    AGENT_ONLY_KEYWORDS = (
        "\u5bfc\u822a",
        "\u5730\u56fe",
        "\u9644\u8fd1",
        "\u8def\u7ebf",
        "\u5929\u6c14",
        "\u5b9e\u65f6",
        "\u6700\u65b0",
        "\u65b0\u95fb",
        "\u80a1\u4ef7",
        "\u4f4d\u7f6e",
        "\u5750\u6807",
        "\u95e8\u7968",
        "\u8425\u4e1a",
        "\u987a\u4fbf",
        "\u7136\u540e",
        "\u5982\u679c",
        "\u5e2e\u6211\u67e5",
        "\u5e2e\u6211\u627e",
        "cut_tag",
        "cut&tag",
        "frip",
        "peak",
        "motif",
        "spike",
        "readsqc",
        "alignmentqc",
        "\u6837\u672c",
        "\u9879\u76ee",
        "\u62a5\u544a",
        "\u54ea\u4e00\u6b65",
        "\u5f02\u5e38",
        "\u6392\u67e5",
    )
    PRODUCT_CONSULT_KEYWORDS = (
        "试剂",
        "试剂盒",
        "产品",
        "推荐",
        "用什么",
        "怎么选",
        "选择什么",
        "购买",
        "订购",
        "货号",
        "kit",
        "reagent",
        "antibody",
        "抗体",
    )
    PROJECT_ANALYSIS_KEYWORDS = (
        "数据",
        "分析",
        "报告",
        "结果",
        "质控",
        "比对",
        "异常",
        "排查",
        "frip",
        "peak",
        "readsqc",
        "alignmentqc",
    )
    CHART_INTENT_KEYWORDS = (
        "画",
        "画出",
        "画图",
        "画一下",
        "绘图",
        "绘制",
        "作图",
        "出图",
        "生成图",
        "可视化",
        "对比图",
        "比较图",
        "柱状图",
        "柱形图",
        "折线图",
        "热图",
        "图表",
        "chart",
        "plot",
        "visualize",
        "heatmap",
        "bar",
        "line",
    )

    @staticmethod
    def _iter_answer_chunks(text: str, max_chars: int = 80):
        """Split a completed answer into UI-friendly chunks for SSE rendering."""
        normalized = str(text or "")
        if not normalized:
            return

        buffer = ""
        for line in normalized.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                if buffer:
                    yield buffer
                    buffer = ""
                yield line
                continue

            pieces = re.split(r"([。！？；.!?;]\s*)", line)
            for index in range(0, len(pieces), 2):
                piece = pieces[index]
                if index + 1 < len(pieces):
                    piece += pieces[index + 1]
                if not piece:
                    continue
                if len(buffer) + len(piece) > max_chars and buffer:
                    yield buffer
                    buffer = ""
                buffer += piece
                if len(buffer) >= max_chars:
                    yield buffer
                    buffer = ""

        if buffer:
            yield buffer
    EXPLICIT_FOLLOW_UP_PATTERNS = (
        re.compile(r"^(\u90a3|\u90a3\u4e48|\u90a3\u5982\u679c|\u90a3\u8fd9|\u90a3\u8fd9\u4e2a|\u90a3\u8fd9\u7c7b|\u90a3\u4e0b\u4e00\u6b65)"),
        re.compile(r"(\u8fd9\u4e2a|\u8fd9\u79cd\u60c5\u51b5|\u4e0a\u8ff0|\u524d\u9762|\u521a\u624d|\u4e0a\u4e00\u4e2a)"),
        re.compile(r"(\u4e3a\u4ec0\u4e48\u4f1a\u8fd9\u6837|\u600e\u4e48\u505a|\u600e\u4e48\u5904\u7406|\u6b63\u5e38\u5417|\u8fd8\u6709\u5417|\u7136\u540e\u5462)$"),
    )
    EXPLICIT_FOLLOW_UP_PREFIXES = (
        "\u90a3",
        "\u90a3\u4e48",
        "\u90a3\u8fd9",
        "\u90a3\u8fd9\u4e2a",
        "\u8fd9\u4e2a",
        "\u8fd9\u4e2a\u5462",
        "\u8fd9\u4e2a\u7684\u8bdd",
        "\u8fd9\u79cd\u60c5\u51b5",
        "\u8fd9\u6837",
        "\u8fd9\u6837\u7684\u8bdd",
        "\u90a3\u4e3a\u4ec0\u4e48",
        "\u4e3a\u4ec0\u4e48\u4f1a\u8fd9\u6837",
        "\u600e\u4e48\u4f1a\u8fd9\u6837",
        "\u90a3\u600e\u4e48\u529e",
        "\u600e\u4e48\u5904\u7406",
        "\u7136\u540e\u5462",
        "\u8fd8\u6709\u5417",
        "\u63a5\u4e0b\u6765\u5462",
        "\u7ee7\u7eed",
    )
    STANDALONE_QUESTION_PATTERNS = (
        re.compile(r"^\u4ec0\u4e48\u662f[\w\u4e00-\u9fffA-Za-z0-9&/_+().\- ]+$", re.IGNORECASE),
        re.compile(r"^[\w\u4e00-\u9fffA-Za-z0-9&/_+().\- ]+\u662f\u4ec0\u4e48[\uff1f?]?$", re.IGNORECASE),
        re.compile(r"^[\w\u4e00-\u9fffA-Za-z0-9&/_+().\- ]+\u4ec0\u4e48\u610f\u601d[\uff1f?]?$", re.IGNORECASE),
        re.compile(r"^[\w\u4e00-\u9fffA-Za-z0-9&/_+().\- ]+\u662f\u5565$", re.IGNORECASE),
        re.compile(r"^[\w\u4e00-\u9fffA-Za-z0-9&/_+().\- ]+\u5417[\uff1f?]?$", re.IGNORECASE),
        re.compile(r"^[\w\u4e00-\u9fffA-Za-z0-9&/_+().\- ]+\u662f\u591a\u5c11[\uff1f?]?$", re.IGNORECASE),
        re.compile(r"^[\w\u4e00-\u9fffA-Za-z0-9&/_+().\- ]+\u6709\u54ea\u4e9b[\uff1f?]?$", re.IGNORECASE),
    )



    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join((text or "").split()).strip()

    @classmethod
    def _is_explicit_standalone(cls, text: str) -> bool:
        normalized = cls._normalize_text(text)
        if not normalized:
            return False
        trimmed = normalized.rstrip("\uff1f?\u3002.,\uff0c\uff1b;\uff01!")
        return any(pattern.fullmatch(trimmed) for pattern in cls.STANDALONE_QUESTION_PATTERNS)

    @classmethod
    def _is_explicit_follow_up(cls, text: str) -> bool:
        normalized = cls._normalize_text(text)
        if not normalized:
            return False
        trimmed = normalized.rstrip("\uff1f?\u3002.,\uff0c\uff1b;\uff01!")
        lowered = trimmed.lower()


        if cls._is_explicit_standalone(trimmed):
            return False
        if any(lowered.startswith(prefix) for prefix in cls.EXPLICIT_FOLLOW_UP_PREFIXES):
            return True
        return any(pattern.search(normalized) for pattern in cls.EXPLICIT_FOLLOW_UP_PATTERNS)

    @classmethod
    def _classify_context_mode(cls, text: str) -> str:
        normalized = cls._normalize_text(text)
        if not normalized:
            return "standalone"
        if cls._is_explicit_standalone(normalized):
            return "standalone"
        if cls._is_explicit_follow_up(normalized):
            return "follow_up"
        return "ambiguous"

    @staticmethod
    def _get_dialogue_messages(chat_history: list[dict]) -> list[dict]:
        return [message for message in chat_history if message.get("role") in {"user", "assistant"}]

    @classmethod
    def _extract_context_payload(cls, chat_history: list[dict]) -> dict[str, object] | None:
        dialogue_messages = cls._get_dialogue_messages(chat_history)
        user_indexes = [
            index for index, message in enumerate(dialogue_messages) if message.get("role") == "user"
        ]
        if not user_indexes:
            return None

        latest_user_index = user_indexes[-1]
        latest_user_message = dialogue_messages[latest_user_index]
        context_mode = cls._classify_context_mode(latest_user_message.get("content", ""))
        if context_mode == "standalone":
            return None

        turns: list[dict[str, str]] = []
        current_user_index = latest_user_index
        chain_mode = context_mode

        while len(turns) < cls.MAX_CONTEXT_TURNS:
            if current_user_index < 2:
                break

            previous_assistant_message = dialogue_messages[current_user_index - 1]
            previous_user_message = dialogue_messages[current_user_index - 2]
            if previous_assistant_message.get("role") != "assistant":
                break
            if previous_user_message.get("role") != "user":
                break

            turns.append(
                {
                    "user": previous_user_message.get("content", ""),
                    "assistant": previous_assistant_message.get("content", ""),
                }
            )

            previous_user_index = current_user_index - 2
            previous_user_mode = cls._classify_context_mode(previous_user_message.get("content", ""))
            if previous_user_mode != "follow_up":
                break
            current_user_index = previous_user_index

        if not turns:
            return None

        turns.reverse()
        return {
            "mode": chain_mode,
            "turns": turns,
            "current_user": latest_user_message.get("content", ""),
        }

    @classmethod
    def _build_orchestrator_history(cls, chat_history: list[dict]) -> list[dict]:
        system_messages = [message for message in chat_history if message.get("role") == "system"]
        context_payload = cls._extract_context_payload(chat_history)
        if not context_payload:
            user_messages = [message for message in chat_history if message.get("role") == "user"]
            if not user_messages:
                return system_messages
            return system_messages + [user_messages[-1]]

        history: list[dict] = []
        for turn in context_payload["turns"]:
            history.append({"role": "user", "content": turn["user"]})
            history.append({"role": "assistant", "content": turn["assistant"]})
        history.append({"role": "user", "content": context_payload["current_user"]})
        return system_messages + history

    @staticmethod
    def _build_retrieval_summary(retrieval_trace: list[dict]) -> tuple[list[str], list[dict]]:
        sources: list[str] = []
        source_seen: set[str] = set()
        documents: list[dict] = []
        doc_seen: set[tuple[str, str, str, str]] = set()

        for payload in retrieval_trace:
            for item in payload.get("documents", []):
                title = item.get("title", "") or ""
                source = item.get("source", "") or ""
                content = item.get("content", "") or ""
                chunk_id = item.get("chunk_id", "") or ""

                for source_value in (title, source):
                    normalized_source = source_value.strip()
                    if normalized_source and normalized_source not in source_seen:
                        source_seen.add(normalized_source)
                        sources.append(normalized_source)

                doc_key = (chunk_id, title, source, content)
                if doc_key in doc_seen:
                    continue
                doc_seen.add(doc_key)
                documents.append(
                    {
                        "title": title,
                        "source": source,
                        "content": content,
                        "chunk_id": chunk_id,
                    }
                )

        return sources, documents

    @staticmethod
    def _build_project_result_payload(project_analysis_result: dict[str, object] | None) -> dict:
        if not project_analysis_result:
            return {}
        result_payload = project_analysis_result.get("result_payload", {}) or {}
        return {
            "identified_project": project_analysis_result.get("identified_project", {}),
            "workflow_trace": project_analysis_result.get("workflow_trace", {}),
            "data": project_analysis_result.get("data", {}),
            "project_memory": project_analysis_result.get("project_memory", {}),
            "result_payload": result_payload,
            "answer": result_payload.get("answer", ""),
            "report": result_payload.get("report", ""),
            "knowledge_retrieval": result_payload.get("knowledge_retrieval", {}),
            "used_knowledge": result_payload.get("used_knowledge", False),
            "answer_quality": result_payload.get("answer_quality", {}),
        }

    @classmethod
    def _extract_chart_request(cls, user_query: str) -> dict[str, object] | None:
        normalized = cls._normalize_text(user_query).lower()
        if not normalized or not any(keyword.lower() in normalized for keyword in cls.CHART_INTENT_KEYWORDS):
            return None

        metric = None
        metric_hits = (
            ("correlation", ("correlation", "spearman", "相关性", "相关系数", "热图")),
            ("mapping", ("mapping", "比对率", "比对", "mapped reads", "reads对比", "reads 比对")),
            ("unique", ("unique", "唯一比对", "唯一比对率", "unique rate")),
            ("duplicate", ("duplicate", "duplicates", "重复率", "重复", "dup rate")),
            ("chrmt_pt", ("chrmt", "chrmt/pt", "chrm", "线粒体", "叶绿体", "质体", "mt污染", "pt污染")),
            ("adapter", ("adapter", "接头", "接头污染")),
            ("frip", ("frip", "富集比例")),
            ("peak", ("peak", "peak数量", "峰数量")),
            ("q30", ("q30", "测序质量")),
            ("q20", ("q20",)),
        )
        for candidate, aliases in metric_hits:
            if any(alias.lower() in normalized for alias in aliases):
                metric = candidate
                break
        if metric is None:
            if any(term in normalized for term in ("对比图", "比较图", "他们", "样本对比", "指标对比", "画图", "图表", "柱状图", "柱形图", "折线图", "可视化")):
                metric = "alignment_summary"
            else:
                return None

        chart_type = None
        if any(term in normalized for term in ("热图", "heatmap")):
            chart_type = "heatmap"
        elif any(term in normalized for term in ("折线图", "line")):
            chart_type = "line"
        elif any(term in normalized for term in ("柱状图", "柱形图", "bar")):
            chart_type = "bar"

        return {"metric": metric, "chart_type": chart_type, "samples": []}

    @staticmethod
    def _build_chart_answer(chart_result: dict[str, object]) -> str:
        title = str(chart_result.get("title") or "").strip()
        if not title:
            title = f"{str(chart_result.get('metric') or '').upper()} {chart_result.get('chart_type') or ''}".strip()
        image_url = str(chart_result.get("image_url") or "")
        source_file = str(chart_result.get("source_file") or "")
        source_columns = chart_result.get("source_columns") or []
        source_columns_text = "、".join(str(item) for item in source_columns[:8]) if isinstance(source_columns, list) else str(source_columns)
        lines = [
            "## 图表已生成",
            "",
            f"项目：`{chart_result.get('project_id', '')}`",
            f"指标：`{chart_result.get('metric', '')}`",
            "",
            f"![{title}]({image_url})",
            "",
            "## 数据来源",
            f"- 图类型：{chart_result.get('chart_type', '')}",
            f"- 来源文件：{source_file}",
        ]
        if source_columns_text:
            lines.append(f"- 使用字段：{source_columns_text}")
        return "\n".join(lines)

    @classmethod
    async def _run_project_chart_route(
        cls,
        *,
        user_query: str,
        project_id: str | None,
        project_root: str | None,
    ) -> tuple[str, dict]:
        """
        返回 (answer_text, plotly_spec)。
        - answer_text：文字说明，流式推送给前端作为 ANSWER 事件
        - plotly_spec：Plotly JSON spec，作为 CHART_SPEC 事件推送，前端用 Plotly.js 渲染
        """
        chart_request = cls._extract_chart_request(user_query)
        if not chart_request:
            raise ValueError("未能识别要绘制的指标，请明确指定 q30、frip、peak、adapter、mapping、duplicate、chrMT/Pt 或 correlation。")
        if not project_id:
            raise ValueError("当前没有识别到项目，请先指定项目或绑定项目后再画图。")

        chart_result = await project_chart_service.generate_chart_spec(
            project_id=project_id,
            project_root=project_root,
            metric=str(chart_request["metric"]),
            chart_type=chart_request.get("chart_type"),
            samples=chart_request.get("samples") or [],
            user_request=user_query,   # 把完整用户需求传给 LLM，支持个性化
        )

        plotly_spec = chart_result.get("plotly_spec", {})
        source_file = str(chart_result.get("source_file") or "")
        metric = str(chart_result.get("metric") or "")
        data_points = chart_result.get("data_points", 0)

        answer_text = "\n".join([
            "## 交互图表已生成",
            "",
            f"项目：`{project_id}`　指标：`{metric}`　数据点：{data_points}",
            f"来源文件：{source_file}",
            "",
            "> 图表已在下方渲染，支持悬停查看数值、缩放、平移。",
        ])

        return answer_text, plotly_spec

    @classmethod
    async def _prepare_task_context(cls, request: ChatMessageRequest):
        user_id = request.context.user_id
        session_id = request.context.session_id
        user_query = request.query

        history_started_at = perf_counter()
        # 非阻塞并发加载：session 历史 + project state 同时发起
        chat_history, project_state = await asyncio.gather(
            session_service.aprepare_history(user_id, session_id, user_query),
            project_session_state_service.aload_state(
                user_id, session_id or "default_project_session"
            ),
        )
        context_payload = cls._extract_context_payload(chat_history)
        orchestrator_history = cls._build_orchestrator_history(chat_history)
        logger.info(
            "prepare_history done user=%s session=%s cost=%.3fs context_mode=%s context_turns=%s",
            user_id,
            session_id,
            perf_counter() - history_started_at,
            (context_payload or {}).get("mode", "standalone"),
            len((context_payload or {}).get("turns", [])),
        )
        return user_id, session_id, user_query, chat_history, orchestrator_history, context_payload, project_state

    @staticmethod
    def _resolve_project_context(
        request: ChatMessageRequest,
        project_state: dict[str, object] | None,
        user_query: str,
        user_id: str,
        session_id: str | None,
    ) -> tuple[str | None, str | None, dict | None]:
        """返回 (project_id, project_root, identified_project)。

        ``identified_project`` 是 project_locator_service.identify_project() 的原始结果，
        仅在本方法内调用过时非 None，供下游路由判断复用，避免二次调用。
        """
        explicit_project_id = (getattr(request, "project_id", None) or "").strip() or None
        explicit_project_root = (getattr(request, "project_root", None) or "").strip() or None
        if explicit_project_id or explicit_project_root:
            return explicit_project_id, explicit_project_root, None

        if MultiAgentService._is_product_consult_query(user_query):
            return None, None, None

        state = project_state or {}
        active_project_id = str(state.get("active_project_id") or state.get("current_project_id") or "").strip() or None
        active_project_root = str(state.get("active_project_root") or state.get("current_project_root") or "").strip() or None
        if state.get("project_context_locked") and active_project_id and active_project_root:
            return active_project_id, active_project_root, None

        resolved_session_id = session_id or "default_project_session"
        identified: dict | None = None
        try:
            identified = project_locator_service.identify_project(
                question=user_query,
                project_id=None,
                user_id=user_id,
                session_id=resolved_session_id,
            )
            identified_project_id = str(identified.get("project_id") or "").strip() or None
            identified_project_root = str(identified.get("project_root") or "").strip() or None
            if identified_project_id and identified_project_root:
                return identified_project_id, identified_project_root, identified
        except FileNotFoundError:
            pass

        return active_project_id, active_project_root, identified

    @staticmethod
    def _get_request_mode(request: ChatMessageRequest) -> str:
        return (getattr(request, "mode", "auto") or "auto").strip().lower()

    @classmethod
    def _is_product_consult_query(cls, user_query: str) -> bool:
        normalized = cls._normalize_text(user_query).lower()
        if not normalized:
            return False
        has_product_intent = any(keyword.lower() in normalized for keyword in cls.PRODUCT_CONSULT_KEYWORDS)
        if not has_product_intent:
            return False
        has_project_analysis_intent = any(keyword.lower() in normalized for keyword in cls.PROJECT_ANALYSIS_KEYWORDS)
        return not has_project_analysis_intent

    @classmethod
    def _build_followup_execution_prompt(
        cls,
        user_text: str,
        pending_action: dict[str, object],
        project_state: dict[str, object],
    ) -> str:
        project_id = str(
            project_state.get("active_project_id")
            or pending_action.get("project_id")
            or project_state.get("current_project_id")
            or ""
        ).strip() or "N/A"
        project_root = str(
            project_state.get("active_project_root")
            or project_state.get("current_project_root")
            or ""
        ).strip()
        summary = str(pending_action.get("summary") or "").strip()
        actions = pending_action.get("actions", []) or []
        action_lines = "\n".join(f"- {item}" for item in actions[:6]) if actions else "- " + (summary or "\u7ee7\u7eed\u6267\u884c\u4e0a\u4e00\u8f6e\u5efa\u8bae")
        default_summary = summary or "\u7ee7\u7eed\u6267\u884c\u4e0a\u4e00\u8f6e\u5efa\u8bae"
        return (
            "\u5f53\u524d\u4f1a\u8bdd\u5df2\u7ed1\u5b9a\u9879\u76ee " + project_id + "\u3002\n"
            + "\u7528\u6237\u786e\u8ba4\u7ee7\u7eed\u6267\u884c\u4e0a\u4e00\u8f6e\u5efa\u8bae\uff0c\u8bf7\u76f4\u63a5\u5ef6\u7eed\u5f53\u524d\u9879\u76ee\u7684\u6392\u67e5\u6d41\u7a0b\uff0c\u4e0d\u8981\u91cd\u65b0\u89e3\u91ca\u80cc\u666f\u3002\n"
            + "\u9879\u76ee\u8def\u5f84: " + (project_root or "N/A") + "\n"
            + "\u4e0a\u4e00\u8f6e\u5efa\u8bae\u6458\u8981: " + default_summary + "\n"
            + "\u5efa\u8bae\u660e\u7ec6:\n" + action_lines + "\n\n" + "\u7528\u6237\u672c\u8f6e\u56de\u590d: " + user_text
        )











    @classmethod
    def _resolve_followup_execution(
        cls,
        user_query: str,
        project_state: dict[str, object] | None,
    ) -> tuple[str, bool]:
        state = project_state or {}
        intent = followup_intent_service.classify(user_query, state)
        if intent != "confirm_followup_action":
            return user_query, False

        pending_action = state.get("pending_followup_action") or {}
        effective_query = cls._build_followup_execution_prompt(user_query, pending_action, state)
        return effective_query, True

    @staticmethod
    def _apply_effective_query(
        orchestrator_history: list[dict],
        context_payload: dict[str, object] | None,
        effective_query: str,
    ) -> tuple[list[dict], dict[str, object] | None]:
        effective_history = [dict(item) for item in orchestrator_history]
        for item in reversed(effective_history):
            if item.get("role") == "user":
                item["content"] = effective_query
                break

        if not context_payload:
            return effective_history, None

        effective_context_payload = dict(context_payload)
        effective_context_payload["current_user"] = effective_query
        return effective_history, effective_context_payload

    @classmethod
    def _should_use_fast_rag(
        cls,
        request: ChatMessageRequest,
        user_query: str,
        context_payload: dict[str, object] | None,
        resolved_project_id: str | None,
    ) -> bool:
        requested_mode = cls._get_request_mode(request)
        if requested_mode in {"agent", "full_agent"}:
            return False
        if requested_mode in {"fast", "fast_rag", "rag_fast"}:
            return True
        if resolved_project_id:
            return False

        normalized_query = cls._normalize_text(user_query).lower()
        if any(keyword in normalized_query for keyword in cls.AGENT_ONLY_KEYWORDS):
            return False

        if len(normalized_query) > 120 and any(
            token in normalized_query for token in ("\uff0c", ",", ";", "\u5e76\u4e14", "\u540c\u65f6", "\u53e6\u5916", "\u7136\u540e")
        ):
            return False

        return True

    @classmethod
    def _should_force_consult_route(
        cls,
        request: ChatMessageRequest,
        user_query: str,
        resolved_project_id: str | None,
        project_state: dict[str, object] | None,
        *,
        _cached_identified: dict | None = None,
    ) -> bool:
        """判断是否走强制咨询路由。

        ``_cached_identified`` 为 _resolve_project_context 已调用过
        identify_project 的原始结果，有则直接复用，不再重复调用。
        """
        requested_mode = cls._get_request_mode(request)
        if requested_mode in {"fast", "fast_rag", "rag_fast"}:
            return False
        if cls._is_product_consult_query(user_query):
            return True
        if resolved_project_id:
            return False

        state = project_state or {}
        if state.get("project_context_locked"):
            return False

        explicit_project_id = (getattr(request, "project_id", None) or "").strip()
        explicit_project_root = (getattr(request, "project_root", None) or "").strip()
        if explicit_project_id or explicit_project_root:
            return False

        normalized_query = cls._normalize_text(user_query).lower()
        if not normalized_query:
            return False

        # 复用已有识别结果，避免二次 identify_project 调用
        if _cached_identified is not None:
            return not bool(_cached_identified.get("project_id"))

        user_id = getattr(getattr(request, "context", None), "user_id", None) or "project_user"
        session_id = getattr(getattr(request, "context", None), "session_id", None) or "default_project_session"
        try:
            identified = project_locator_service.identify_project(
                question=user_query,
                project_id=None,
                user_id=user_id,
                session_id=session_id,
            )
            if identified.get("project_id"):
                return False
        except FileNotFoundError:
            pass

        return True

    @classmethod
    def _should_direct_business_route(
        cls,
        request: ChatMessageRequest,
        user_query: str,
        resolved_project_id: str | None,
        project_state: dict[str, object] | None,
    ) -> bool:
        requested_mode = cls._get_request_mode(request)
        if requested_mode in {"fast", "fast_rag", "rag_fast"}:
            return False
        if cls._is_product_consult_query(user_query):
            return False
        if resolved_project_id:
            return True

        explicit_project_id = (getattr(request, "project_id", None) or "").strip()
        explicit_project_root = (getattr(request, "project_root", None) or "").strip()
        if explicit_project_id or explicit_project_root:
            return True

        state = project_state or {}
        return bool(state.get("project_context_locked") and state.get("active_project_id"))

    @staticmethod
    def _extract_pending_followup_action(
        project_analysis_result: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if not project_analysis_result:
            return None

        data = project_analysis_result.get("data", {}) or {}
        diagnosis_summary = data.get("diagnosis_summary", {}) or {}
        next_actions = data.get("next_actions", []) or diagnosis_summary.get("next_actions", []) or []
        if not next_actions:
            return None

        result_payload = project_analysis_result.get("result_payload", {}) or {}
        answer = str(result_payload.get("answer") or "").strip()
        summary = "\uff1b".join(str(item).strip() for item in next_actions[:3] if str(item).strip())
        return {
            "kind": "continue_investigation",
            "source": "assistant_next_step",
            "summary": summary,
            "actions": [str(item).strip() for item in next_actions[:6] if str(item).strip()],
            "analysis_run_id": data.get("run_id"),
            "project_id": data.get("project_id") or project_analysis_result.get("identified_project", {}).get("project_id"),
            "answer_excerpt": answer[:400],
        }

    @classmethod
    async def _sync_pending_followup_action(
        cls,
        user_id: str,
        session_id: str | None,
        project_analysis_result: dict[str, object] | None,
    ) -> None:
        if not session_id:
            return
        payload = cls._extract_pending_followup_action(project_analysis_result)
        if payload:
            await project_session_state_service.aset_pending_followup_action(user_id, session_id, payload)
        elif project_analysis_result:
            await project_session_state_service.aclear_pending_followup_action(user_id, session_id)

    _MCP_FALLBACK_ERRORS = (
        "Server not initialized",
        "connect() first",
        "Failed to connect to MCP server",
        "HTTP error 500",
        "Tool bailian_web_search not found",
        "TechnicalAgentPool not initialized",
    )

    @classmethod
    async def _run_force_consult_route(cls, query: str) -> str:
        agent_input = _build_technical_agent_input(query)
        try:
            async with technical_agent_pool.acquire() as agent:
                result = await with_llm_retry(
                    lambda: Runner.run(
                        agent,
                        input=agent_input,
                        run_config=RunConfig(tracing_disabled=True),
                    )
                )
            return result.final_output
        except Exception as exc:
            error_text = str(exc)
            if any(item in error_text for item in cls._MCP_FALLBACK_ERRORS):
                fallback_result = await with_llm_retry(
                    lambda: Runner.run(
                        technical_agent_kb_only,
                        input=agent_input,
                        run_config=RunConfig(tracing_disabled=True),
                    )
                )
                return fallback_result.final_output
            raise

    @classmethod
    @asynccontextmanager
    async def _force_consult_stream(cls, query: str) -> AsyncIterator:
        """从连接池借出 agent，持有 slot 直到 streaming 全部完成。

        用法（在 async generator 中）：
            async with cls._force_consult_stream(query) as streaming_result:
                async for chunk in process_stream_response(streaming_result):
                    yield chunk
                answer_text = streaming_result.final_output

        pool slot 在整个 streaming 期间都被持有，不会被其他请求抢占。
        连接池未就绪时自动回退到 technical_agent_kb_only（单路径 yield，无双 yield 风险）。
        """
        agent_input = _build_technical_agent_input(query)
        if not technical_agent_pool.is_ready:
            logger.warning("technical_agent_pool not ready, falling back to kb_only for streaming")
            yield Runner.run_streamed(
                starting_agent=technical_agent_kb_only,
                input=agent_input,
                run_config=RunConfig(tracing_disabled=True),
            )
            return
        async with technical_agent_pool.acquire() as agent:
            yield Runner.run_streamed(
                starting_agent=agent,
                input=agent_input,
                run_config=RunConfig(tracing_disabled=True),
            )

    @staticmethod
    def _build_execution_trace(
        *,
        route_name: str,
        project_analysis_result: dict[str, object] | None,
    ) -> dict[str, object]:
        project_payload = project_analysis_result or {}
        workflow_trace = project_payload.get("workflow_trace", {}) or {}
        result_payload = project_payload.get("result_payload", {}) or {}
        answer_quality = result_payload.get("answer_quality", {}) or {}
        answer_cache = workflow_trace.get("answer_cache")
        if answer_cache is None:
            answer_cache = (result_payload.get("answer_quality") or {}).get("answer_cache")
        planner_mode = ""
        analysis_cache = ""
        if isinstance(project_payload.get("data"), dict):
            planner_mode = str((project_payload.get("data") or {}).get("analysis_plan", {}).get("planner_llm_skipped"))
            analysis_cache = str((project_payload.get("data") or {}).get("analysis_cache") or "")
        planner_llm_used = None
        if planner_mode != "":
            planner_llm_used = planner_mode == "False"
        route_uses_llm = route_name in {"fast_rag", "agent", "force_consult"}
        answer_llm_used = route_uses_llm
        if route_name == "business" and answer_cache == "hit":
            answer_llm_used = False
        return {
            "route": route_name,
            "planner_llm_used": planner_llm_used,
            "answer_llm_used": answer_llm_used,
            "orchestrator_llm_used": route_name == "agent",
            "knowledge_retrieve_used": route_name in {"fast_rag", "business", "agent", "force_consult"},
            "answer_cache": answer_cache or "",
            "analysis_cache": analysis_cache,
            "answer_quality_status": answer_quality.get("status", ""),
        }

    @classmethod
    async def _finalize_task_context(
        cls,
        user_id: str,
        session_id: str | None,
        chat_history: list[dict],
        agent_result: str,
        started_at: float,
    ) -> str:
        formatted_agent_result = re.sub(r"\n+", "\n", agent_result or "")
        chat_history.append({"role": "assistant", "content": formatted_agent_result})
        await session_service.asave_history(user_id, session_id, chat_history)
        logger.info(
            "process_task done user=%s session=%s total_cost=%.3fs",
            user_id,
            session_id,
            perf_counter() - started_at,
        )
        return formatted_agent_result

    @classmethod
    async def process_task_sync(cls, request: ChatMessageRequest) -> dict:
        # ── 并发限流 guard ──
        try:
            await asyncio.wait_for(_request_semaphore.acquire(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("process_task_sync: concurrency limit reached, rejecting request")
            return {
                "answer": "服务器繁忙，请稍后重试。",
                "sources": [],
                "retrieved_docs": [],
                "project_analysis": {},
                "execution_trace": {"route": "rejected", "answer_llm_used": False},
            }
        # ── 以下代码持有 semaphore，finally 中释放 ──
        cache_token = init_round_knowledge_cache()
        technical_cache_token = init_round_technical_cache()
        technical_context_token = init_round_technical_context()
        project_request_token = init_round_project_request_context()
        project_analysis_result_token = init_round_project_analysis_result()
        try:
            started_at = perf_counter()
            (
                user_id,
                session_id,
                user_query,
                chat_history,
                orchestrator_history,
                context_payload,
                project_state,
            ) = await cls._prepare_task_context(request)
            effective_query, followup_confirmed = cls._resolve_followup_execution(user_query, project_state)
            effective_history, effective_context_payload = cls._apply_effective_query(
                orchestrator_history,
                context_payload,
                effective_query,
            )
            resolved_project_id, resolved_project_root, _identified_project = cls._resolve_project_context(
                request,
                project_state,
                effective_query,
                user_id,
                session_id,
            )
            set_round_technical_context(effective_context_payload)
            # 传入已加载的 project_state，避免 set_round_project_request_context 内再做同步文件读取
            set_round_project_request_context(
                user_id,
                session_id,
                resolved_project_id,
                project_root=resolved_project_root,
                max_evidence_files=request.max_evidence_files,
                _preloaded_state=project_state,
            )
            use_fast_rag = cls._should_use_fast_rag(
                request,
                effective_query,
                effective_context_payload,
                resolved_project_id,
            )
            force_consult_route = cls._should_force_consult_route(
                request,
                effective_query,
                resolved_project_id,
                project_state,
                _cached_identified=_identified_project,
            )
            chart_request = cls._extract_chart_request(effective_query)
            direct_business_route = cls._should_direct_business_route(
                request,
                effective_query,
                resolved_project_id,
                project_state,
            )
            logger.info(
                "request route user=%s session=%s mode=%s route=%s resolved_project_id=%s force_consult=%s direct_business=%s chart=%s followup_confirmed=%s",
                user_id,
                session_id,
                cls._get_request_mode(request),
                "chart" if chart_request else ("fast_rag" if use_fast_rag else ("business" if direct_business_route else "agent")),
                resolved_project_id,
                force_consult_route,
                direct_business_route,
                bool(chart_request),
                followup_confirmed,
            )
            route_name = "chart" if chart_request else ("fast_rag" if use_fast_rag else ("business" if direct_business_route else ("force_consult" if force_consult_route else "agent")))

            chart_plotly_spec: dict | None = None
            if chart_request:
                answer_text, chart_plotly_spec = await cls._run_project_chart_route(
                    user_query=effective_query,
                    project_id=resolved_project_id,
                    project_root=resolved_project_root,
                )
            elif use_fast_rag:
                answer_text = await RagFastService.answer_sync(
                    question=effective_query,
                    context_payload=effective_context_payload,
                )
            elif direct_business_route:
                business_result = await execute_project_business_analysis(
                    effective_query,
                    user_id=user_id,
                    session_id=session_id,
                    project_id=resolved_project_id,
                    project_root=resolved_project_root,
                    max_evidence_files=request.max_evidence_files,
                )
                set_round_project_analysis_result(business_result.get("analysis_result"))
                answer_text = str(business_result.get("answer", ""))
            elif force_consult_route:
                answer_text = await cls._run_force_consult_route(effective_query)
            else:
                run_result = await with_llm_retry(
                    lambda: Runner.run(
                        starting_agent=orchestrator_agent,
                        input=effective_history,
                        context=effective_query,
                        max_turns=3,
                        run_config=RunConfig(tracing_disabled=True),
                    )
                )
                answer_text = run_result.final_output

            answer = await cls._finalize_task_context(
                user_id=user_id,
                session_id=session_id,
                chat_history=chat_history,
                agent_result=answer_text,
                started_at=started_at,
            )
            sources, retrieved_docs = cls._build_retrieval_summary(get_round_retrieval_trace())
            project_analysis_result = get_round_project_analysis_result()
            await cls._sync_pending_followup_action(user_id, session_id, project_analysis_result)
            result = {
                "answer": answer,
                "sources": sources,
                "retrieved_docs": retrieved_docs,
                "project_analysis": cls._build_project_result_payload(project_analysis_result),
                "execution_trace": cls._build_execution_trace(
                    route_name=route_name,
                    project_analysis_result=project_analysis_result,
                ),
            }
            if chart_plotly_spec:
                result["plotly_spec"] = chart_plotly_spec
            return result
        finally:
            reset_round_knowledge_cache(cache_token)
            reset_round_technical_cache(technical_cache_token)
            reset_round_technical_context(technical_context_token)
            reset_round_project_analysis_result(project_analysis_result_token)
            reset_round_project_request_context(project_request_token)
            _request_semaphore.release()

    @classmethod
    async def process_task(cls, request: ChatMessageRequest, flag: bool) -> AsyncGenerator:
        # ── 并发限流 guard ──
        # 超时未获取到 slot → 立即向客户端推送"繁忙"事件并终止生成器；
        # semaphore 未被 acquire，不需要 release。
        try:
            await asyncio.wait_for(_request_semaphore.acquire(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("process_task: concurrency limit reached, rejecting request")
            yield "data: " + ResponseFactory.build_text(
                "服务器繁忙，请稍后重试。",
                ContentKind.PROCESS,
            ).model_dump_json() + "\n\n"
            yield "data: " + ResponseFactory.build_finish().model_dump_json() + "\n\n"
            return
        # ── 以下代码持有 semaphore，finally 中释放 ──
        cache_token = init_round_knowledge_cache()
        technical_cache_token = init_round_technical_cache()
        technical_context_token = init_round_technical_context()
        project_request_token = init_round_project_request_context()
        project_analysis_result_token = init_round_project_analysis_result()
        project_progress_token = init_round_project_progress_queue()
        try:
            started_at = perf_counter()
            (
                user_id,
                session_id,
                user_query,
                chat_history,
                orchestrator_history,
                context_payload,
                project_state,
            ) = await cls._prepare_task_context(request)
            effective_query, followup_confirmed = cls._resolve_followup_execution(user_query, project_state)
            effective_history, effective_context_payload = cls._apply_effective_query(
                orchestrator_history,
                context_payload,
                effective_query,
            )
            resolved_project_id, resolved_project_root, _identified_project = cls._resolve_project_context(
                request,
                project_state,
                effective_query,
                user_id,
                session_id,
            )
            set_round_technical_context(effective_context_payload)
            set_round_project_request_context(
                user_id,
                session_id,
                resolved_project_id,
                project_root=resolved_project_root,
                max_evidence_files=request.max_evidence_files,
                _preloaded_state=project_state,
            )
            use_fast_rag = cls._should_use_fast_rag(
                request,
                effective_query,
                effective_context_payload,
                resolved_project_id,
            )
            force_consult_route = cls._should_force_consult_route(
                request,
                effective_query,
                resolved_project_id,
                project_state,
                _cached_identified=_identified_project,
            )
            chart_request = cls._extract_chart_request(effective_query)
            direct_business_route = cls._should_direct_business_route(
                request,
                effective_query,
                resolved_project_id,
                project_state,
            )
            logger.info(
                "request route user=%s session=%s mode=%s route=%s resolved_project_id=%s force_consult=%s direct_business=%s chart=%s followup_confirmed=%s",
                user_id,
                session_id,
                cls._get_request_mode(request),
                "chart" if chart_request else ("fast_rag" if use_fast_rag else ("business" if direct_business_route else "agent")),
                resolved_project_id,
                force_consult_route,
                direct_business_route,
                bool(chart_request),
                followup_confirmed,
            )

            if chart_request:
                yield "data: " + ResponseFactory.build_text(
                    json.dumps(
                        {
                            "type": "project_stage",
                            "stage": "read_chart_data",
                            "status": "in_progress",
                            "text": "正在读取图表所需项目数据",
                            "detail": {"project_id": resolved_project_id or ""},
                        },
                        ensure_ascii=False,
                    ),
                    ContentKind.PROCESS,
                ).model_dump_json() + "\n\n"
                yield "data: " + ResponseFactory.build_text(
                    json.dumps(
                        {
                            "type": "project_stage",
                            "stage": "generate_chart",
                            "status": "in_progress",
                            "text": "正在生成交互图表",
                            "detail": {"metric": str(chart_request.get("metric") or "")},
                        },
                        ensure_ascii=False,
                    ),
                    ContentKind.PROCESS,
                ).model_dump_json() + "\n\n"
                # ── 图表生成（用 try/finally 保证 stage 事件一定发出） ────────
                _chart_error: str | None = None
                answer_text = ""
                plotly_spec = None
                try:
                    answer_text, plotly_spec = await cls._run_project_chart_route(
                        user_query=effective_query,
                        project_id=resolved_project_id,
                        project_root=resolved_project_root,
                    )
                except Exception as _exc:
                    _chart_error = str(_exc)
                    logger.error("chart_route error: %s", _chart_error, exc_info=True)
                    answer_text = f"图表生成失败：{_chart_error}"
                finally:
                    # 无论成功/失败，都发 completed 让前端阶段结束
                    yield "data: " + ResponseFactory.build_text(
                        json.dumps(
                            {
                                "type": "project_stage",
                                "stage": "generate_chart",
                                "status": "completed" if not _chart_error else "error",
                                "text": "交互图表已生成" if not _chart_error else f"生成失败：{_chart_error}",
                                "detail": {"metric": str(chart_request.get("metric") or "")},
                            },
                            ensure_ascii=False,
                        ),
                        ContentKind.PROCESS,
                    ).model_dump_json() + "\n\n"
                yield "data: " + ResponseFactory.build_text(
                    json.dumps(
                        {
                            "type": "project_stage",
                            "stage": "synthesis",
                            "status": "completed",
                            "text": "正在汇总结论",
                            "detail": {"project_id": resolved_project_id or ""},
                        },
                        ensure_ascii=False,
                    ),
                    ContentKind.PROCESS,
                ).model_dump_json() + "\n\n"
                # 先推文字说明
                for answer_chunk in cls._iter_answer_chunks(answer_text):
                    yield "data: " + ResponseFactory.build_text(
                        answer_chunk,
                        ContentKind.ANSWER,
                    ).model_dump_json() + "\n\n"
                    await asyncio.sleep(0)
                # 再推 Plotly spec（前端识别 kind=chart_spec 后渲染交互图）
                if plotly_spec:
                    yield "data: " + ResponseFactory.build_text(
                        json.dumps(plotly_spec, ensure_ascii=False),
                        ContentKind.CHART_SPEC,
                    ).model_dump_json() + "\n\n"
                yield "data: " + ResponseFactory.build_finish().model_dump_json() + "\n\n"
            elif use_fast_rag:
                yield "data: " + ResponseFactory.build_text(
                    "\u6b63\u5728\u67e5\u8be2\u77e5\u8bc6\u5e93\uff0c\u8bf7\u7a0d\u5019\u3002",
                    ContentKind.PROCESS,
                ).model_dump_json() + "\n\n"
                stream_generator, chunks = await RagFastService.answer_stream(
                    question=effective_query,
                    context_payload=effective_context_payload,
                )
                async for chunk in stream_generator:
                    yield chunk
                yield "data: " + ResponseFactory.build_finish().model_dump_json() + "\n\n"
                answer_text = "".join(chunks).strip()
            elif direct_business_route:
                yield "data: " + ResponseFactory.build_text(
                    "\u5df2\u8bc6\u522b\u5230\u5f53\u524d\u95ee\u9898\u5305\u542b\u9879\u76ee\u4e0a\u4e0b\u6587\uff0c\u6b63\u5728\u57fa\u4e8e\u9879\u76ee\u6570\u636e\u8fdb\u884c\u5206\u6790\u3002",
                    ContentKind.PROCESS,
                ).model_dump_json() + "\n\n"
                yield "data: " + ResponseFactory.build_text(
                    json.dumps(
                        {
                            "type": "project_stage",
                            "stage": "workflow_start",
                            "status": "in_progress",
                            "text": "\u5df2\u8fdb\u5165\u9879\u76ee\u5206\u6790\u5de5\u4f5c\u6d41\uff0c\u6b63\u5728\u521d\u59cb\u5316\u6267\u884c\u4e0a\u4e0b\u6587\u3002",
                            "detail": {
                                "project_id": resolved_project_id or "",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    ContentKind.PROCESS,
                ).model_dump_json() + "\n\n"
                progress_queue = get_round_project_progress_queue()
                business_task = asyncio.create_task(
                    execute_project_business_analysis(
                        effective_query,
                        user_id=user_id,
                        session_id=session_id,
                        project_id=resolved_project_id,
                        project_root=resolved_project_root,
                        max_evidence_files=request.max_evidence_files,
                    )
                )
                pending_progress = None
                progress_done = False
                business_done = False
                business_result = None
                answer_streamed = False

                while True:
                    if pending_progress is None and progress_queue is not None and not progress_done:
                        pending_progress = asyncio.create_task(progress_queue.get())

                    active = [task for task in (business_task, pending_progress) if task is not None]
                    if not active:
                        break

                    done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)

                    if pending_progress in done:
                        try:
                            progress_payload = pending_progress.result()
                            if isinstance(progress_payload, dict) and progress_payload.get("type") == "project_stage_end":
                                progress_done = True
                                pending_progress = None
                                continue
                            if isinstance(progress_payload, dict) and progress_payload.get("type") in {
                                "project_answer_delta",
                                "project_answer_final",
                            }:
                                answer_text_delta = str(progress_payload.get("text") or "")
                                if answer_text_delta:
                                    answer_streamed = True
                                    answer_parts = (
                                        cls._iter_answer_chunks(answer_text_delta)
                                        if progress_payload.get("type") == "project_answer_final"
                                        else (answer_text_delta,)
                                    )
                                    for answer_part in answer_parts:
                                        yield "data: " + ResponseFactory.build_text(
                                            answer_part,
                                            ContentKind.ANSWER,
                                        ).model_dump_json() + "\n\n"
                                        await asyncio.sleep(0)
                                pending_progress = None
                                continue
                            progress_text = (
                                json.dumps(progress_payload, ensure_ascii=False)
                                if isinstance(progress_payload, dict)
                                else str(progress_payload)
                            )
                            yield "data: " + ResponseFactory.build_text(
                                progress_text,
                                ContentKind.PROCESS,
                            ).model_dump_json() + "\n\n"
                        finally:
                            pending_progress = None

                    if business_task in done:
                        business_result = business_task.result()
                        business_done = True
                        close_project_progress()

                    if business_done and (progress_done or progress_queue is None):
                        break

                set_round_project_analysis_result((business_result or {}).get("analysis_result"))
                answer_text = str(business_result.get("answer", ""))
                if not answer_streamed:
                    for answer_chunk in cls._iter_answer_chunks(answer_text):
                        yield "data: " + ResponseFactory.build_text(
                            answer_chunk,
                            ContentKind.ANSWER,
                        ).model_dump_json() + "\n\n"
                        await asyncio.sleep(0)
                yield "data: " + ResponseFactory.build_finish().model_dump_json() + "\n\n"
            elif force_consult_route:
                yield "data: " + ResponseFactory.build_text(
                    _build_stage_payload(
                        "consult_start",
                        "completed",
                        "已进入咨询智能体链路，当前问题不会触发项目数据分析。",
                    ),
                    ContentKind.PROCESS,
                ).model_dump_json() + "\n\n"
                yield "data: " + ResponseFactory.build_text(
                    _build_stage_payload(
                        "consult_generate",
                        "in_progress",
                        "正在检索资料并生成咨询回答。",
                    ),
                    ContentKind.PROCESS,
                ).model_dump_json() + "\n\n"
                # _force_consult_stream 返回一个 asynccontextmanager，
                # 持有 pool slot 直到 streaming 完成
                async with cls._force_consult_stream(effective_query) as streaming_result:
                    stream_iter = process_stream_response(streaming_result).__aiter__()
                    while True:
                        try:
                            chunk = await stream_iter.__anext__()
                        except StopAsyncIteration:
                            break
                        yield chunk
                    answer_text = streaming_result.final_output
                yield "data: " + ResponseFactory.build_text(
                    _build_stage_payload(
                        "consult_generate",
                        "completed",
                        "咨询回答已生成。",
                    ),
                    ContentKind.PROCESS,
                ).model_dump_json() + "\n\n"
            else:
                streaming_result = Runner.run_streamed(
                    starting_agent=orchestrator_agent,
                    input=effective_history,
                    context=effective_query,
                    max_turns=3,
                    run_config=RunConfig(tracing_disabled=True),
                )
                progress_queue = get_round_project_progress_queue()
                stream_iter = process_stream_response(streaming_result).__aiter__()
                pending_stream = None
                pending_progress = None
                stream_done = False
                progress_done = False

                while True:
                    if pending_stream is None and not stream_done:
                        pending_stream = asyncio.create_task(stream_iter.__anext__())
                    if pending_progress is None and progress_queue is not None and not progress_done:
                        pending_progress = asyncio.create_task(progress_queue.get())

                    active = [task for task in (pending_stream, pending_progress) if task is not None]
                    if not active:
                        break

                    done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)

                    if pending_progress in done:
                        try:
                            progress_payload = pending_progress.result()
                            if isinstance(progress_payload, dict) and progress_payload.get("type") == "project_stage_end":
                                progress_done = True
                                pending_progress = None
                                continue
                            progress_text = (
                                json.dumps(progress_payload, ensure_ascii=False)
                                if isinstance(progress_payload, dict)
                                else str(progress_payload)
                            )
                            yield "data: " + ResponseFactory.build_text(
                                progress_text,
                                ContentKind.PROCESS,
                            ).model_dump_json() + "\n\n"
                        finally:
                            pending_progress = None

                    if pending_stream in done:
                        try:
                            chunk = pending_stream.result()
                            yield chunk
                        except StopAsyncIteration:
                            stream_done = True
                            close_project_progress()
                        finally:
                            pending_stream = None

                    if stream_done and progress_done:
                        break
                answer_text = streaming_result.final_output

            project_analysis_result = get_round_project_analysis_result()
            await cls._finalize_task_context(
                user_id=user_id,
                session_id=session_id,
                chat_history=chat_history,
                agent_result=answer_text,
                started_at=started_at,
            )
            await cls._sync_pending_followup_action(user_id, session_id, project_analysis_result)
            if project_analysis_result:
                await session_service.aappend_message(
                    user_id=user_id,
                    session_id=session_id,
                    role="analysis",
                    content=project_analysis_result,
                )
        except Exception as e:
            error_text = str(e)
            logger.error("AgentService.process_query error: %s", error_text)
            logger.debug("异常详情: %s", traceback.format_exc())

            yield "data: " + ResponseFactory.build_text(
                f"系统错误: {error_text}",
                ContentKind.PROCESS,
            ).model_dump_json() + "\n\n"

            should_retry = flag and not any(item in error_text for item in cls.NON_RETRYABLE_ERRORS)
            if should_retry:
                yield "data: " + ResponseFactory.build_text(
                    "正在尝试自动重试...",
                    ContentKind.PROCESS,
                ).model_dump_json() + "\n\n"

                async for item in MultiAgentService.process_task(request, flag=False):
                    yield item
            else:
                # 不重试时必须发送 finish 事件，否则前端 SSE 流永远挂起
                yield "data: " + ResponseFactory.build_finish().model_dump_json() + "\n\n"
        finally:
            close_project_progress()
            reset_round_knowledge_cache(cache_token)
            reset_round_technical_cache(technical_cache_token)
            reset_round_technical_context(technical_context_token)
            reset_round_project_analysis_result(project_analysis_result_token)
            reset_round_project_request_context(project_request_token)
            reset_round_project_progress_queue(project_progress_token)
            _request_semaphore.release()
