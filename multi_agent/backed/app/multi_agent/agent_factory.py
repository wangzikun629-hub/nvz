from contextvars import ContextVar

from agents import Runner, function_tool
from agents.run import RunConfig

from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.multi_agent.project_progress import (
    get_round_project_progress_queue,
    init_round_project_progress_queue,
    reset_round_project_progress_queue,
)
from multi_agent.backed.app.infrastructure.tools.mcp.mcp_pool import technical_agent_pool
from multi_agent.backed.app.multi_agent.technical_agent import technical_agent_kb_only
from multi_agent.backed.app.utils.retry_util import with_llm_retry
from multi_agent.backed.app.services.project_analysis_workflow_service import (
    project_analysis_workflow_service,
)
from multi_agent.backed.app.services.project_session_state_service import (
    project_session_state_service,
)


technical_agent_cache_var: ContextVar[dict[str, str] | None] = ContextVar(
    "technical_agent_cache_var",
    default=None,
)
technical_agent_context_var: ContextVar[dict[str, object] | None] = ContextVar(
    "technical_agent_context_var",
    default=None,
)
project_agent_request_context_var: ContextVar[dict[str, object] | None] = ContextVar(
    "project_agent_request_context_var",
    default=None,
)
project_analysis_result_var: ContextVar[dict[str, object] | None] = ContextVar(
    "project_analysis_result_var",
    default=None,
)


def init_round_technical_cache():
    return technical_agent_cache_var.set({})


def reset_round_technical_cache(token) -> None:
    technical_agent_cache_var.reset(token)


def init_round_technical_context():
    return technical_agent_context_var.set(None)


def reset_round_technical_context(token) -> None:
    technical_agent_context_var.reset(token)


def set_round_technical_context(payload: dict[str, object] | None) -> None:
    technical_agent_context_var.set(payload)


def init_round_project_request_context():
    return project_agent_request_context_var.set(None)


def reset_round_project_request_context(token) -> None:
    project_agent_request_context_var.reset(token)


def set_round_project_request_context(
    user_id: str,
    session_id: str | None,
    project_id: str | None = None,
    project_root: str | None = None,
    max_evidence_files: int | None = None,
    *,
    _preloaded_state: dict | None = None,
) -> None:
    """将项目请求上下文写入 ContextVar。

    ``_preloaded_state`` 可传入已在 async 上下文中加载好的 project_state，
    避免在事件循环中再做一次同步文件读取。
    """
    resolved_user_id = user_id or "project_user"
    resolved_session_id = session_id or "default_project_session"
    state = _preloaded_state if _preloaded_state is not None else project_session_state_service.load_state(
        resolved_user_id, resolved_session_id
    )
    project_agent_request_context_var.set(
        {
            "user_id": resolved_user_id,
            "session_id": resolved_session_id,
            "project_id": (project_id or "").strip(),
            "project_root": (project_root or "").strip(),
            "active_project_id": str(state.get("active_project_id") or "").strip(),
            "active_project_root": str(state.get("active_project_root") or "").strip(),
            "project_context_locked": bool(state.get("project_context_locked")),
            "max_evidence_files": max_evidence_files or 40,
        }
    )


def init_round_project_analysis_result():
    return project_analysis_result_var.set(None)


def reset_round_project_analysis_result(token) -> None:
    project_analysis_result_var.reset(token)


def get_round_project_analysis_result() -> dict[str, object] | None:
    return project_analysis_result_var.get()


def set_round_project_analysis_result(payload: dict[str, object] | None) -> None:
    project_analysis_result_var.set(payload)


def _normalize_query(query: str) -> str:
    return " ".join((query or "").split()).strip().lower()


def _format_turns(turns: list[dict[str, str]]) -> str:
    blocks: list[str] = []
    for index, turn in enumerate(turns, start=1):
        user_text = (turn.get("user") or "").strip()
        assistant_text = (turn.get("assistant") or "").strip()
        blocks.append(
            f"Turn {index} user question:\n{user_text}\n\n"
            f"Turn {index} assistant answer:\n{assistant_text}"
        )
    return "\n\n".join(blocks)


def _build_technical_agent_input(query: str) -> str:
    context_payload = technical_agent_context_var.get()
    request_context = project_agent_request_context_var.get() or {}
    active_project_id = str(request_context.get("active_project_id") or "").strip()
    active_project_root = str(request_context.get("active_project_root") or "").strip()
    project_context_note = ""
    if active_project_id and request_context.get("project_context_locked"):
        project_context_note = (
            "Current session is bound to a project.\n"
            "Prefer to resolve references like 'this project', 'the current sample', or similar "
            "against the following context:\n"
            f"- project_id: {active_project_id}\n"
            f"- project_root: {active_project_root}\n\n"
        )

    if not context_payload:
        return project_context_note + query if project_context_note else query

    mode = str(context_payload.get("mode") or "standalone").strip()
    turns = context_payload.get("turns") or []
    current_user = str(context_payload.get("current_user") or query).strip()
    if not turns:
        return project_context_note + current_user if project_context_note else current_user

    turns_text = _format_turns(turns)
    if project_context_note:
        turns_text = project_context_note + turns_text

    if mode == "follow_up":
        return (
            "Below is the follow-up context for the current question. "
            "Answer only the current user question based on the relevant prior turns.\n\n"
            f"{turns_text}\n\n"
            f"Current user follow-up:\n{current_user}"
        )

    if mode == "ambiguous":
        return (
            "Below is possibly relevant prior dialogue.\n"
            "1. If the current question clearly depends on the prior turns, use them.\n"
            "2. If the current question is already self-contained, ignore the prior turns.\n"
            "3. Answer only the current question and do not explain your reasoning process.\n\n"
            f"{turns_text}\n\n"
            f"Current user question:\n{current_user}"
        )

    return project_context_note + current_user if project_context_note else current_user


@function_tool
async def consult_technical_expert(query: str) -> str:
    """Handle technical consultation, troubleshooting, and real-time technical questions."""
    agent_input = _build_technical_agent_input(query)
    normalized_cache_key = _normalize_query(agent_input)
    round_cache = technical_agent_cache_var.get()
    if round_cache is not None and normalized_cache_key in round_cache:
        logger.info("technical expert cache hit key=%s", normalized_cache_key[:80])
        return round_cache[normalized_cache_key]

    try:
        logger.info("[Route] technical expert: %s", query[:60])
        async with technical_agent_pool.acquire() as agent:
            result = await with_llm_retry(
                lambda: Runner.run(
                    agent,
                    input=agent_input,
                    run_config=RunConfig(tracing_disabled=True),
                )
            )
        final_output = result.final_output
        if round_cache is not None:
            round_cache[normalized_cache_key] = final_output
        return final_output
    except Exception as e:
        error_text = str(e)
        logger.error("technical agent failed: %s", error_text)

        mcp_unavailable_errors = (
            "Server not initialized",
            "connect() first",
            "Failed to connect to MCP server",
            "HTTP error 500",
            "Tool bailian_web_search not found",
            "TechnicalAgentPool not initialized",
        )
        if any(item in error_text for item in mcp_unavailable_errors):
            try:
                logger.warning("technical agent falling back to knowledge-base-only mode")
                fallback_result = await with_llm_retry(
                    lambda: Runner.run(
                        technical_agent_kb_only,
                        input=agent_input,
                        run_config=RunConfig(tracing_disabled=True),
                    )
                )
                final_output = fallback_result.final_output
                if round_cache is not None:
                    round_cache[normalized_cache_key] = final_output
                return final_output
            except Exception as fallback_error:
                logger.error("technical agent fallback failed: %s", str(fallback_error))
                return f"Technical expert is temporarily unavailable: {fallback_error}"

        return f"Technical expert is temporarily unavailable: {error_text}"


async def _run_project_analysis_workflow_impl(query: str) -> str:
    request_context = project_agent_request_context_var.get() or {}
    user_id = str(request_context.get("user_id") or "project_user")
    session_id = str(request_context.get("session_id") or "default_project_session")
    explicit_project_id = str(request_context.get("project_id") or "").strip() or None
    explicit_project_root = str(request_context.get("project_root") or "").strip() or None
    max_evidence_files = int(request_context.get("max_evidence_files") or 40)
    try:
        logger.info("[Route] project workflow: %s", query[:60])
        result = await project_analysis_workflow_service.arun_analysis(
            question=query,
            project_id=explicit_project_id,
            user_id=user_id,
            session_id=session_id,
            project_root=explicit_project_root,
            max_evidence_files=max_evidence_files,
        )
        set_round_project_analysis_result(result)
        if result.get("needs_confirmation"):
            candidates = result.get("identified_project", {}).get("candidates", [])
            if candidates:
                candidate_text = ", ".join(
                    f"{item.get('project_id', '-')}(score={item.get('score', '-')})"
                    for item in candidates[:5]
                )
                return (
                    f"{result.get('message', 'Project match is ambiguous. Please confirm the project first.')}"
                    f" Candidates: {candidate_text}"
                )
            return result.get("message", "Project match is ambiguous. Please confirm the project first.")

        result_payload = result.get("result_payload", {}) or {}
        if result_payload.get("output_mode") == "report":
            return str(result_payload.get("report") or result_payload.get("answer") or "")
        return str(result_payload.get("answer") or result_payload.get("report") or "")
    except Exception as e:
        logger.error("project analysis workflow failed: %s", str(e))
        return f"Project analysis failed: {e}"


@function_tool
async def run_project_analysis_workflow(query: str) -> str:
    """Handle project analysis, reporting, and step-by-step investigation tasks."""
    return await _run_project_analysis_workflow_impl(query)


async def execute_project_business_analysis(
    query: str,
    *,
    user_id: str,
    session_id: str | None,
    project_id: str | None = None,
    project_root: str | None = None,
    max_evidence_files: int | None = None,
) -> dict[str, object]:
    previous_request_context = project_agent_request_context_var.get()
    previous_analysis_result = project_analysis_result_var.get()
    set_round_project_request_context(
        user_id,
        session_id,
        project_id,
        project_root=project_root,
        max_evidence_files=max_evidence_files,
    )
    set_round_project_analysis_result(None)
    try:
        logger.info("[Route] project business agent: %s", query[:60])
        return {
            "answer": await _run_project_analysis_workflow_impl(query),
            "analysis_result": get_round_project_analysis_result(),
        }
    finally:
        project_agent_request_context_var.set(previous_request_context)
        project_analysis_result_var.set(previous_analysis_result)


@function_tool
async def consult_project_business_expert(query: str) -> str:
    """Route project-analysis tasks to the business project agent."""
    try:
        request_context = project_agent_request_context_var.get() or {}
        result = await execute_project_business_analysis(
            query,
            user_id=str(request_context.get("user_id") or "project_user"),
            session_id=str(request_context.get("session_id") or "default_project_session"),
            project_id=str(request_context.get("project_id") or "").strip() or None,
            project_root=str(request_context.get("project_root") or "").strip() or None,
            max_evidence_files=int(request_context.get("max_evidence_files") or 40),
        )
        return str(result.get("answer", ""))
    except Exception as e:
        logger.error("project business agent failed: %s", str(e))
        return f"Project business agent is temporarily unavailable: {e}"


AGENT_TOOLS = [
    consult_project_business_expert,
    consult_technical_expert,
]
