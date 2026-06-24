import hmac
from time import perf_counter, time

from fastapi import Depends, Header, HTTPException
from fastapi.routing import APIRouter
from starlette.responses import StreamingResponse

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.auth.token_utils import verify_auth_token
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.multi_agent.agent_factory import execute_project_business_analysis
from multi_agent.backed.app.schemas.request import (
    ChatCompatRequest,
    ChatMessageRequest,
    ProjectChartRequest,
    ProjectAnalyzeRequest,
    ProjectContextRequest,
    SessionHistoryRequest,
    UserContext,
    UserSessionsRequest,
)
from multi_agent.backed.app.services.agent_service import MultiAgentService
from multi_agent.backed.app.services.project_chart_service import project_chart_service
from multi_agent.backed.app.services.project_session_state_service import (
    project_session_state_service,
)
from multi_agent.backed.app.services.session_service import session_service


def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    configured_key = str(settings.APP_API_KEY or "").strip()
    if not configured_key:
        return
    supplied_key = str(x_api_key or "")
    if not hmac.compare_digest(supplied_key, configured_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


router = APIRouter(dependencies=[Depends(_require_api_key)])


def _require_auth_user(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> dict:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    payload = verify_auth_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired auth token")

    user_id = str(payload.get("user_id", "")).strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Auth token missing user identity")
    if x_user_id and str(x_user_id).strip() and str(x_user_id).strip() != user_id:
        raise HTTPException(status_code=403, detail="User header does not match auth token")
    return payload


def _resolve_user_id(auth_user: dict) -> str:
    return str(auth_user.get("user_id", "")).strip()


def _build_project_context_payload(user_id: str, session_id: str) -> dict:
    state = project_session_state_service.load_state(user_id, session_id)
    ai_report_summary = state.get("ai_report_summary") if isinstance(state.get("ai_report_summary"), dict) else {}
    return {
        "active_project_id": state.get("active_project_id"),
        "active_project_root": state.get("active_project_root"),
        "project_context_locked": bool(state.get("project_context_locked")),
        "project_context_source": state.get("project_context_source"),
        "recent_project_questions": state.get("recent_project_questions", []),
        "recent_projects": state.get("recent_projects", []),
        "pending_project_confirmation": state.get("pending_project_confirmation"),
        "pending_followup_action": state.get("pending_followup_action"),
        "last_identified_at": state.get("last_identified_at"),
        "ai_report_summary_status": ai_report_summary.get("status"),
        "ai_report_summary_updated_at": ai_report_summary.get("updated_at"),
        "ai_report_summary_error": ai_report_summary.get("error"),
    }


def _build_project_context_event(previous_context: dict, current_context: dict) -> dict | None:
    previous_project_id = previous_context.get("active_project_id")
    current_project_id = current_context.get("active_project_id")
    previous_locked = bool(previous_context.get("project_context_locked"))
    current_locked = bool(current_context.get("project_context_locked"))

    if not previous_locked and current_locked and current_project_id:
        return {
            "type": "project_bound",
            "project_id": current_project_id,
            "message": f"Current window is now bound to project {current_project_id}.",
        }

    if (
        previous_locked
        and current_locked
        and previous_project_id
        and current_project_id
        and previous_project_id != current_project_id
    ):
        return {
            "type": "project_switched",
            "project_id": current_project_id,
            "previous_project_id": previous_project_id,
            "message": f"Project context switched from {previous_project_id} to {current_project_id}.",
        }

    if previous_locked and not current_locked:
        return {
            "type": "project_cleared",
            "previous_project_id": previous_project_id,
            "message": f"Project context cleared from {previous_project_id or 'current window'}.",
        }

    return None


@router.post("/api/query", summary="Streaming chat endpoint")
async def query(request_context: ChatMessageRequest, auth_user: dict = Depends(_require_auth_user)) -> StreamingResponse:
    user_id = _resolve_user_id(auth_user)
    request_context.context.user_id = user_id
    user_query = request_context.query
    logger.info("user=%s query=%s", user_id, user_query)
    async_generator_result = MultiAgentService.process_task(request_context, flag=True)
    return StreamingResponse(
        content=async_generator_result,
        status_code=200,
        media_type="text/event-stream",
    )


@router.post("/api/chat", summary="JSON chat endpoint")
async def chat(request_context: ChatCompatRequest, auth_user: dict = Depends(_require_auth_user)):
    request_context.user_id = _resolve_user_id(auth_user)
    question = (request_context.question or request_context.query or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question or query is required")

    session_id = request_context.session_id or f"api_chat_{int(time() * 1000)}"
    previous_project_context = _build_project_context_payload(request_context.user_id, session_id)
    chat_request = ChatMessageRequest(
        query=question,
        context=UserContext(
            user_id=request_context.user_id,
            session_id=session_id,
        ),
        flag=request_context.flag,
        mode=request_context.mode,
        project_id=request_context.project_id,
        project_root=request_context.project_root,
        max_evidence_files=request_context.max_evidence_files,
    )
    result = await MultiAgentService.process_task_sync(chat_request)
    current_project_context = _build_project_context_payload(request_context.user_id, session_id)

    response = {
        "question": question,
        "answer": result["answer"],
        "sources": result["sources"],
        "retrieved_docs": result["retrieved_docs"],
        "execution_trace": result.get("execution_trace", {}),
        "user_id": request_context.user_id,
        "session_id": session_id,
        "project_context": current_project_context,
        "project_context_event": _build_project_context_event(
            previous_project_context,
            current_project_context,
        ),
    }

    project_analysis = result.get("project_analysis") or {}
    if project_analysis:
        response.update(
            {
                "project_analysis": project_analysis,
                "identified_project": project_analysis.get("identified_project", {}),
                "workflow_trace": project_analysis.get("workflow_trace", {}),
                "data": project_analysis.get("data", {}),
                "project_memory": project_analysis.get("project_memory", {}),
                "result_payload": project_analysis.get("result_payload", {}),
                "project_answer": project_analysis.get("answer", ""),
                "report": project_analysis.get("report", ""),
                "knowledge_retrieval": project_analysis.get("knowledge_retrieval", {}),
                "used_knowledge": project_analysis.get("used_knowledge", False),
                "answer_quality": project_analysis.get("answer_quality", {}),
            }
        )
    return response


@router.post("/api/user_sessions")
def get_user_sessions(request: UserSessionsRequest, auth_user: dict = Depends(_require_auth_user)):
    user_id = _resolve_user_id(auth_user)
    request.user_id = user_id
    try:
        started_at = perf_counter()
        all_sessions = session_service.get_all_sessions_summary(user_id)
        logger.info(
            "get_user_sessions done user=%s sessions=%d cost=%.3fs",
            user_id,
            len(all_sessions),
            perf_counter() - started_at,
        )
        return {
            "success": True,
            "user_id": user_id,
            "total_sessions": len(all_sessions),
            "sessions": all_sessions,
        }
    except Exception as exc:
        logger.error("get_user_sessions failed user=%s error=%s", user_id, str(exc))
        return {
            "success": False,
            "user_id": user_id,
            "error": str(exc),
        }


@router.delete("/api/user_sessions/{session_id}")
def delete_user_session(session_id: str, auth_user: dict = Depends(_require_auth_user)):
    user_id = _resolve_user_id(auth_user)
    deleted = session_service.delete_session(user_id, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "success": True,
        "user_id": user_id,
        "session_id": session_id,
    }


@router.post("/api/project_analyze", summary="Project analysis endpoint")
async def project_analyze(request: ProjectAnalyzeRequest, auth_user: dict = Depends(_require_auth_user)):
    request.user_id = _resolve_user_id(auth_user)
    previous_project_context = _build_project_context_payload(request.user_id, request.session_id)
    await session_service.aappend_message(
        user_id=request.user_id,
        session_id=request.session_id,
        role="user",
        content=request.question,
    )
    result = await execute_project_business_analysis(
        request.question,
        user_id=request.user_id,
        session_id=request.session_id,
        project_id=request.project_id,
        project_root=request.project_root,
        max_evidence_files=request.max_evidence_files,
    )
    current_project_context = _build_project_context_payload(request.user_id, request.session_id)
    if result.get("analysis_result"):
        await session_service.aappend_message(
            user_id=request.user_id,
            session_id=request.session_id,
            role="analysis",
            content=result["analysis_result"],
        )
        result_payload = result["analysis_result"].get("result_payload", {})
        return {
            "success": True,
            "message": result.get("answer", ""),
            "project_context": current_project_context,
            "project_context_event": _build_project_context_event(
                previous_project_context,
                current_project_context,
            ),
            "identified_project": result["analysis_result"].get("identified_project", {}),
            "workflow_trace": result["analysis_result"].get("workflow_trace", {}),
            "data": result["analysis_result"].get("data", {}),
            "project_memory": result["analysis_result"].get("project_memory", {}),
            "result_payload": result_payload,
            "answer": result_payload.get("answer", ""),
            "report": result_payload.get("report", ""),
            "knowledge_retrieval": result_payload.get("knowledge_retrieval", {}),
            "used_knowledge": result_payload.get("used_knowledge", False),
        }
    return {
        "success": False,
        "message": result.get("answer", "Project analysis did not return a structured result."),
        "project_context": current_project_context,
        "project_context_event": _build_project_context_event(
            previous_project_context,
            current_project_context,
        ),
    }


@router.post("/api/project_chart", summary="Generate project chart (static PNG, legacy)")
def project_chart(request: ProjectChartRequest, auth_user: dict = Depends(_require_auth_user)):
    request.user_id = _resolve_user_id(auth_user)
    try:
        return project_chart_service.generate_chart(
            project_id=request.project_id,
            project_root=request.project_root,
            metric=request.metric,
            chart_type=request.chart_type,
            samples=request.samples,
            title=request.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("project_chart failed project=%s metric=%s", request.project_id, request.metric)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/project_chart_spec", summary="Generate interactive Plotly chart spec (LLM-driven)")
async def project_chart_spec(request: ProjectChartRequest, auth_user: dict = Depends(_require_auth_user)):
    """
    返回 Plotly JSON spec，前端用 Plotly.js 直接渲染交互图。
    user_request 字段传入个性化需求，如"加一条 0.1 阈值线，柱子用绿色"。
    """
    request.user_id = _resolve_user_id(auth_user)
    try:
        return await project_chart_service.generate_chart_spec(
            project_id=request.project_id,
            project_root=request.project_root,
            metric=request.metric,
            chart_type=request.chart_type,
            samples=request.samples,
            user_request=request.user_request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("project_chart_spec failed project=%s metric=%s", request.project_id, request.metric)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/project_context")
def get_project_context(request: ProjectContextRequest, auth_user: dict = Depends(_require_auth_user)):
    request.user_id = _resolve_user_id(auth_user)
    return {
        "success": True,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "project_context": _build_project_context_payload(request.user_id, request.session_id),
    }


@router.post("/api/session_messages")
def get_session_messages(request: SessionHistoryRequest, auth_user: dict = Depends(_require_auth_user)):
    request.user_id = _resolve_user_id(auth_user)
    return {
        "success": True,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "messages": session_service.get_session_messages(
            request.user_id,
            request.session_id,
        ),
    }


@router.post("/api/latest_project_analysis")
def get_latest_project_analysis(request: SessionHistoryRequest, auth_user: dict = Depends(_require_auth_user)):
    request.user_id = _resolve_user_id(auth_user)
    ai_report_summary = project_session_state_service.get_ai_report_summary(request.user_id, request.session_id)
    latest_analysis = None
    if ai_report_summary and ai_report_summary.get("status") == "ready":
        latest_analysis = ai_report_summary.get("analysis")
    return {
        "success": True,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "analysis": latest_analysis,
    }


@router.post("/api/project_context/clear")
async def clear_project_context(request: ProjectContextRequest, auth_user: dict = Depends(_require_auth_user)):
    request.user_id = _resolve_user_id(auth_user)
    previous_project_context = _build_project_context_payload(request.user_id, request.session_id)
    state = await project_session_state_service.aclear_active_project(request.user_id, request.session_id)
    current_project_context = {
        "active_project_id": state.get("active_project_id"),
        "active_project_root": state.get("active_project_root"),
        "project_context_locked": bool(state.get("project_context_locked")),
        "project_context_source": state.get("project_context_source"),
        "recent_project_questions": state.get("recent_project_questions", []),
        "recent_projects": state.get("recent_projects", []),
        "pending_project_confirmation": state.get("pending_project_confirmation"),
        "pending_followup_action": state.get("pending_followup_action"),
        "last_identified_at": state.get("last_identified_at"),
    }
    return {
        "success": True,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "project_context": current_project_context,
        "project_context_event": _build_project_context_event(
            previous_project_context,
            current_project_context,
            ),
    }
