from datetime import datetime

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from multi_agent.backed.app.infrastructure.auth.token_utils import issue_auth_token, verify_auth_token
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.repositories import auth_session_repository, user_repository


auth_router = APIRouter(prefix="/auth", tags=["auth"])


def _extract_bearer_token(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _require_auth_payload(authorization: str | None) -> dict | None:
    token = _extract_bearer_token(authorization)
    return verify_auth_token(token)


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() + "Z"


class AuthRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_not_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("用户名不能为空")
        if len(normalized) > 64:
            raise ValueError("用户名长度不能超过 64 个字符")
        return normalized

    @field_validator("password")
    @classmethod
    def password_min_length(cls, value: str) -> str:
        if len(value) < 6:
            raise ValueError("密码长度不能少于 6 位")
        return value


@auth_router.post("/register")
def register(body: AuthRequest):
    try:
        user = user_repository.create_user(body.username, body.password)
        return {"ok": True, "message": "注册成功", "username": user["username"]}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "message": str(exc)})
    except Exception as exc:
        logger.error("[AuthRouter] register failed: %s", str(exc))
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


@auth_router.post("/login")
def login(body: AuthRequest):
    try:
        user = user_repository.authenticate_user(body.username, body.password)
        if user is None:
            return JSONResponse(status_code=401, content={"ok": False, "message": "用户名或密码错误"})

        token_bundle = issue_auth_token(str(user["id"]), user["username"])
        auth_session_repository.create_session(
            token_bundle["session_id"],
            int(user["id"]),
            user["username"],
            token_bundle["token"],
            int(token_bundle["expires_at"]),
        )
        return {
            "ok": True,
            "message": "登录成功",
            "username": user["username"],
            "isAdmin": bool(user.get("is_admin", False)),
            "userId": str(user["id"]),
            "authToken": token_bundle["token"],
            "sessionId": token_bundle["session_id"],
            "expiresAt": token_bundle["expires_at"],
        }
    except Exception as exc:
        logger.error("[AuthRouter] login failed: %s", str(exc))
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


@auth_router.post("/logout")
def logout(authorization: str | None = Header(default=None)):
    payload = _require_auth_payload(authorization)
    if not payload:
        return JSONResponse(status_code=401, content={"ok": False, "message": "未登录或 token 已失效"})

    session_id = str(payload.get("sid", "")).strip()
    revoked = auth_session_repository.revoke_session(session_id)
    return {
        "ok": True,
        "revoked": revoked,
        "sessionId": session_id,
    }


@auth_router.get("/sessions")
def list_sessions(authorization: str | None = Header(default=None)):
    payload = _require_auth_payload(authorization)
    if not payload:
        return JSONResponse(status_code=401, content={"ok": False, "message": "未登录或 token 已失效"})

    user_id = int(payload["user_id"])
    current_session_id = str(payload.get("sid", "")).strip()
    sessions = auth_session_repository.list_user_sessions(user_id)
    return {
        "ok": True,
        "items": [
            {
                "sessionId": item["session_id"],
                "username": item["username"],
                "createdAt": _format_datetime(item["created_at"]),
                "lastUsedAt": _format_datetime(item["last_used_at"]),
                "expiresAt": _format_datetime(item["expires_at"]),
                "current": item["session_id"] == current_session_id,
            }
            for item in sessions
        ],
    }


@auth_router.post("/sessions/{session_id}/revoke")
def revoke_user_session(session_id: str, authorization: str | None = Header(default=None)):
    payload = _require_auth_payload(authorization)
    if not payload:
        return JSONResponse(status_code=401, content={"ok": False, "message": "未登录或 token 已失效"})

    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        return JSONResponse(status_code=400, content={"ok": False, "message": "session_id 不能为空"})

    revoked = auth_session_repository.revoke_user_session(int(payload["user_id"]), normalized_session_id)
    if not revoked:
        return JSONResponse(status_code=404, content={"ok": False, "message": "会话不存在或已失效"})

    return {"ok": True, "sessionId": normalized_session_id, "revoked": True}
