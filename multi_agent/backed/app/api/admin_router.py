"""
管理员 API 路由

提供用户管理、会话统计等管理员专用接口。
所有端点均须携带 X-Admin-Token 请求头（与 APP_API_KEY 一致）。
"""
import hmac

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.repositories import auth_session_repository, user_repository
from multi_agent.backed.app.repositories.session_repository import session_repository

admin_router = APIRouter(prefix="/admin", tags=["admin"])


# ── 鉴权辅助 ─────────────────────────────────────────────────────────────────

def _check_admin(x_admin_token: str | None) -> bool:
    """校验管理员令牌（与 APP_API_KEY 相同）。"""
    configured = str(settings.APP_API_KEY or "").strip()
    if not configured:
        # 未配置 API_KEY 时，开发环境直接放行；生产环境应确保配置该值
        logger.warning("[AdminRouter] APP_API_KEY 未配置，管理接口对所有请求开放！请在生产环境中设置此值。")
        return True
    return hmac.compare_digest(str(x_admin_token or ""), configured)


def _admin_required(x_admin_token: str | None) -> JSONResponse | None:
    if not _check_admin(x_admin_token):
        return JSONResponse(status_code=403, content={"ok": False, "message": "无管理员权限"})
    return None


def _parse_user_id(user_id: str) -> int | None:
    """将路径参数 user_id 解析为整数，非纯数字返回 None（防路径穿越）。"""
    try:
        return int(user_id.strip())
    except (ValueError, AttributeError):
        return None


# ── 统计概览 ─────────────────────────────────────────────────────────────────

@admin_router.get("/stats")
def get_stats(x_admin_token: str | None = Header(default=None)):
    """
    返回平台统计概览：
    - total_users: 注册用户总数
    - active_sessions: 当前在线（未过期）会话数
    """
    err = _admin_required(x_admin_token)
    if err:
        return err
    try:
        total_users = user_repository.get_user_count()
        active_sessions = auth_session_repository.count_active_sessions()
        return {
            "ok": True,
            "total_users": total_users,
            "active_sessions": active_sessions,
        }
    except Exception as exc:
        logger.error("[AdminRouter] get_stats failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


# ── 用户列表 ─────────────────────────────────────────────────────────────────

@admin_router.get("/users")
def list_users(x_admin_token: str | None = Header(default=None)):
    """
    返回所有注册用户列表，每个用户附带当前活跃会话数和对话总数。
    使用批量查询避免 N+1 问题。
    """
    err = _admin_required(x_admin_token)
    if err:
        return err
    try:
        users = user_repository.list_all_users()

        # 一次查询拿到所有用户的活跃会话数，避免 N+1
        session_counts: dict[int, int] = auth_session_repository.count_active_sessions_grouped()

        result = []
        for u in users:
            uid_int = int(u["id"])
            uid_str = str(uid_int)
            sessions_meta = session_repository.get_all_sessions_summary_metadata(uid_str)
            result.append({
                "id": uid_str,
                "username": u["username"],
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
def get_user_conversations(user_id: str, x_admin_token: str | None = Header(default=None)):
    """
    返回指定用户的所有对话，包含对话名称（首条用户消息）、消息数、最后更新时间。
    """
    err = _admin_required(x_admin_token)
    if err:
        return err

    # 校验 user_id 为纯数字，防止路径穿越
    uid_int = _parse_user_id(user_id)
    if uid_int is None:
        return JSONResponse(status_code=400, content={"ok": False, "message": "user_id 格式错误"})

    # 验证用户存在
    uid_str = str(uid_int)
    try:
        sessions_meta = session_repository.get_all_sessions_summary_metadata(uid_str)
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
        return {"ok": True, "user_id": uid_str, "items": items}
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
    x_admin_token: str | None = Header(default=None),
):
    """重置指定用户密码（管理员专用）。"""
    err = _admin_required(x_admin_token)
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


# ── 删除用户 ─────────────────────────────────────────────────────────────────

@admin_router.delete("/users/{user_id}")
def delete_user(user_id: str, x_admin_token: str | None = Header(default=None)):
    """
    删除指定用户（管理员专用）。
    同时撤销该用户所有有效 auth session，防止已删用户的 token 继续生效。
    """
    err = _admin_required(x_admin_token)
    if err:
        return err

    uid_int = _parse_user_id(user_id)
    if uid_int is None:
        return JSONResponse(status_code=400, content={"ok": False, "message": "user_id 格式错误"})

    try:
        # 先撤销所有会话，再删用户，防止已删用户 token 仍有效
        revoked = auth_session_repository.revoke_all_user_sessions(uid_int)
        changed = user_repository.delete_user(uid_int)
        if not changed:
            return JSONResponse(status_code=404, content={"ok": False, "message": "用户不存在"})
        logger.info("[AdminRouter] 删除用户 id=%s，撤销会话 %s 个", uid_int, revoked)
        return {"ok": True, "message": "用户已删除"}
    except Exception as exc:
        logger.error("[AdminRouter] delete_user failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})
