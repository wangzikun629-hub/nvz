"""
管理员 API 路由

提供用户管理、会话统计等管理员专用接口。
鉴权方式：验证请求方的 JWT（Authorization: Bearer <token>）且 is_admin = 1。
"""
from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from multi_agent.backed.app.infrastructure.auth.token_utils import verify_auth_token
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.repositories import auth_session_repository, user_repository
from multi_agent.backed.app.repositories.session_repository import session_repository

admin_router = APIRouter(prefix="/admin", tags=["admin"])


# ── 鉴权辅助 ─────────────────────────────────────────────────────────────────

def _require_admin(authorization: str | None) -> tuple[dict | None, JSONResponse | None]:
    """
    从 Authorization 头解析 JWT，并验证该用户是管理员。
    返回 (user_dict, None) 或 (None, error_response)。
    """
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    payload = verify_auth_token(token)
    if not payload:
        return None, JSONResponse(status_code=401, content={"ok": False, "message": "未登录或 token 已失效"})

    user_id = int(str(payload.get("user_id", 0)))
    user = user_repository.get_user_by_id(user_id)
    if not user or not user.get("is_admin"):
        return None, JSONResponse(status_code=403, content={"ok": False, "message": "无管理员权限"})

    return user, None


def _parse_user_id(user_id: str) -> int | None:
    """将路径参数解析为整数，防止路径穿越。"""
    try:
        return int(user_id.strip())
    except (ValueError, AttributeError):
        return None


# ── 统计概览 ─────────────────────────────────────────────────────────────────

@admin_router.get("/stats")
def get_stats(authorization: str | None = Header(default=None)):
    """返回平台统计概览：注册用户总数 + 当前在线会话数。"""
    _, err = _require_admin(authorization)
    if err:
        return err
    try:
        return {
            "ok": True,
            "total_users": user_repository.get_user_count(),
            "active_sessions": auth_session_repository.count_active_sessions(),
        }
    except Exception as exc:
        logger.error("[AdminRouter] get_stats failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


# ── 用户列表 ─────────────────────────────────────────────────────────────────

@admin_router.get("/users")
def list_users(authorization: str | None = Header(default=None)):
    """返回所有注册用户列表（含 is_admin、活跃会话数、对话总数）。"""
    _, err = _require_admin(authorization)
    if err:
        return err
    try:
        users = user_repository.list_all_users()
        session_counts: dict[int, int] = auth_session_repository.count_active_sessions_grouped()
        result = []
        for u in users:
            uid_int = int(u["id"])
            uid_str = str(uid_int)
            sessions_meta = session_repository.get_all_sessions_summary_metadata(uid_str)
            result.append({
                "id": uid_str,
                "username": u["username"],
                "is_admin": u["is_admin"],
                "created_at": u["created_at"],
                "active_sessions": session_counts.get(uid_int, 0),
                "conversation_count": len(sessions_meta),
            })
        return {"ok": True, "items": result}
    except Exception as exc:
        logger.error("[AdminRouter] list_users failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


# ── 用户对话列表 ──────────────────────────────────────────────────────────────

@admin_router.get("/users/{user_id}/conversations")
def get_user_conversations(user_id: str, authorization: str | None = Header(default=None)):
    """返回指定用户的所有对话（含标题、消息数、最后更新时间）。"""
    _, err = _require_admin(authorization)
    if err:
        return err

    uid_int = _parse_user_id(user_id)
    if uid_int is None:
        return JSONResponse(status_code=400, content={"ok": False, "message": "user_id 格式错误"})

    try:
        sessions_meta = session_repository.get_all_sessions_summary_metadata(str(uid_int))
        items = [
            {
                "session_id": s["session_id"],
                "title": (s.get("preview") or "")[:60] or "（无内容）",
                "total_messages": s.get("total_messages", 0),
                "created_at": s.get("create_time", ""),
                "updated_at": s.get("updated_at", ""),
            }
            for s in sorted(sessions_meta, key=lambda x: x.get("updated_at", ""), reverse=True)
        ]
        return {"ok": True, "user_id": str(uid_int), "items": items}
    except Exception as exc:
        logger.error("[AdminRouter] get_user_conversations failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


# ── 重置密码 ─────────────────────────────────────────────────────────────────

class ResetPasswordBody(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("密码长度不能少于 6 位")
        return v


@admin_router.put("/users/{user_id}/password")
def reset_password(
    user_id: str,
    body: ResetPasswordBody,
    authorization: str | None = Header(default=None),
):
    """重置指定用户密码（管理员专用）。"""
    _, err = _require_admin(authorization)
    if err:
        return err

    uid_int = _parse_user_id(user_id)
    if uid_int is None:
        return JSONResponse(status_code=400, content={"ok": False, "message": "user_id 格式错误"})

    try:
        changed = user_repository.update_password(uid_int, body.new_password)
        if not changed:
            return JSONResponse(status_code=404, content={"ok": False, "message": "用户不存在"})
        return {"ok": True, "message": "密码已重置"}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "message": str(exc)})
    except Exception as exc:
        logger.error("[AdminRouter] reset_password failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


# ── 切换管理员权限 ────────────────────────────────────────────────────────────

class AdminStatusBody(BaseModel):
    is_admin: bool


@admin_router.put("/users/{user_id}/admin-status")
def set_admin_status(
    user_id: str,
    body: AdminStatusBody,
    authorization: str | None = Header(default=None),
):
    """设置或取消指定用户的管理员权限。"""
    current_admin, err = _require_admin(authorization)
    if err:
        return err

    uid_int = _parse_user_id(user_id)
    if uid_int is None:
        return JSONResponse(status_code=400, content={"ok": False, "message": "user_id 格式错误"})

    # 不允许管理员撤销自己的权限
    if uid_int == int(current_admin["id"]):
        return JSONResponse(status_code=400, content={"ok": False, "message": "不能修改自己的管理员权限"})

    try:
        changed = user_repository.set_admin_status(uid_int, body.is_admin)
        if not changed:
            return JSONResponse(status_code=404, content={"ok": False, "message": "用户不存在"})
        return {"ok": True, "is_admin": body.is_admin, "message": "设为管理员" if body.is_admin else "已取消管理员"}
    except Exception as exc:
        logger.error("[AdminRouter] set_admin_status failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


# ── 删除用户 ─────────────────────────────────────────────────────────────────

@admin_router.delete("/users/{user_id}")
def delete_user(user_id: str, authorization: str | None = Header(default=None)):
    """删除指定用户，同时撤销其所有有效 session。"""
    current_admin, err = _require_admin(authorization)
    if err:
        return err

    uid_int = _parse_user_id(user_id)
    if uid_int is None:
        return JSONResponse(status_code=400, content={"ok": False, "message": "user_id 格式错误"})

    if uid_int == int(current_admin["id"]):
        return JSONResponse(status_code=400, content={"ok": False, "message": "不能删除自己"})

    try:
        revoked = auth_session_repository.revoke_all_user_sessions(uid_int)
        changed = user_repository.delete_user(uid_int)
        if not changed:
            return JSONResponse(status_code=404, content={"ok": False, "message": "用户不存在"})
        logger.info("[AdminRouter] 删除用户 id=%s，撤销会话 %s 个", uid_int, revoked)
        return {"ok": True, "message": "用户已删除"}
    except Exception as exc:
        logger.error("[AdminRouter] delete_user failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})
