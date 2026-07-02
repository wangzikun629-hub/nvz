"""
管理员 API 路由

提供用户管理、会话统计等管理员专用接口。
鉴权方式：验证请求方的 JWT（Authorization: Bearer <token>）且 is_admin = 1。
"""
import hmac

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.auth.token_utils import verify_auth_token
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.repositories import auth_session_repository, user_repository
from multi_agent.backed.app.repositories.session_repository import session_repository
from multi_agent.backed.app.services.business_agent import candidate_metric_service
from multi_agent.backed.app.services.business_agent import script_formula_promotion_service

admin_router = APIRouter(prefix="/admin", tags=["admin"])


# ── 鉴权辅助 ─────────────────────────────────────────────────────────────────

def _valid_legacy_admin_token(x_admin_token: str | None) -> bool:
    configured_key = str(settings.APP_API_KEY or "").strip()
    supplied_key = str(x_admin_token or "").strip()
    return bool(configured_key and supplied_key and hmac.compare_digest(supplied_key, configured_key))


def _legacy_admin_user() -> dict:
    return {
        "id": 0,
        "username": "app-api-key-admin",
        "is_admin": True,
    }


def _require_admin(
    authorization: str | None,
    x_admin_token: str | None = None,
) -> tuple[dict | None, JSONResponse | None]:
    """
    从 Authorization 头解析 JWT，并验证该用户是管理员。
    返回 (user_dict, None) 或 (None, error_response)。
    """
    if _valid_legacy_admin_token(x_admin_token):
        return _legacy_admin_user(), None

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
def get_stats(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """返回平台统计概览：注册用户总数 + 当前在线会话数。"""
    _, err = _require_admin(authorization, x_admin_token)
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
def list_users(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """返回所有注册用户列表（含 is_admin、活跃会话数、对话总数）。"""
    _, err = _require_admin(authorization, x_admin_token)
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
def get_user_conversations(
    user_id: str,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """返回指定用户的所有对话（含标题、消息数、最后更新时间）。"""
    _, err = _require_admin(authorization, x_admin_token)
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
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """重置指定用户密码（管理员专用）。"""
    _, err = _require_admin(authorization, x_admin_token)
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
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """设置或取消指定用户的管理员权限。"""
    current_admin, err = _require_admin(authorization, x_admin_token)
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
def delete_user(
    user_id: str,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """删除指定用户，同时撤销其所有有效 session。"""
    current_admin, err = _require_admin(authorization, x_admin_token)
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


# ── 候选指标审核（Phase 1.5）───────────────────────────────────────────────────

@admin_router.get("/candidate-metrics")
def list_candidate_metrics(
    status: str | None = None,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """返回候选指标队列（默认全部状态，可用 status 过滤：pending/auto_promoted/rejected）。"""
    _, err = _require_admin(authorization, x_admin_token)
    if err:
        return err
    try:
        items = candidate_metric_service.list_for_admin_review(status=status)
        return {"ok": True, "items": items}
    except Exception as exc:
        logger.error("[AdminRouter] list_candidate_metrics failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


class ApproveCandidateMetricBody(BaseModel):
    metric_id: str
    unit: str
    verifier_contract: str
    applicable_assays: list[str] | None = None
    label: str | None = None


@admin_router.post("/candidate-metrics/{candidate_key}/approve")
def approve_candidate_metric(
    candidate_key: str,
    body: ApproveCandidateMetricBody,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """审核通过候选指标：补全正式指标名/单位/校验合约/适用实验类型后正式注册。"""
    admin_user, err = _require_admin(authorization, x_admin_token)
    if err:
        return err
    try:
        ok, message = candidate_metric_service.approve_candidate(
            candidate_key,
            metric_id=body.metric_id,
            unit=body.unit,
            verifier_contract=body.verifier_contract,
            applicable_assays=body.applicable_assays,
            label=body.label,
            reviewer=str((admin_user or {}).get("username") or ""),
        )
        if not ok:
            return JSONResponse(status_code=400, content={"ok": False, "message": message})
        return {"ok": True, "message": message, "metric_id": body.metric_id}
    except Exception as exc:
        logger.error("[AdminRouter] approve_candidate_metric failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


class RejectCandidateMetricBody(BaseModel):
    note: str = ""


@admin_router.post("/candidate-metrics/{candidate_key}/reject")
def reject_candidate_metric(
    candidate_key: str,
    body: RejectCandidateMetricBody,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """驳回候选指标，加入黑名单，避免再次被探测上报。"""
    admin_user, err = _require_admin(authorization, x_admin_token)
    if err:
        return err
    try:
        ok, message = candidate_metric_service.reject_candidate(
            candidate_key,
            note=body.note,
            reviewer=str((admin_user or {}).get("username") or ""),
        )
        if not ok:
            return JSONResponse(status_code=400, content={"ok": False, "message": message})
        return {"ok": True, "message": message}
    except Exception as exc:
        logger.error("[AdminRouter] reject_candidate_metric failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


# ── 脚本公式转正审核（project_analysis_phase1.5_auto_promotion_revision.md 第一部分）───────
# 复用候选指标审核同一套 admin 基础设施；审核对象从"候选指标"扩展为"待祝福公式变体"
# （情形 B/C/D：模型提取、命名可能冲突、或未知变体），通过 = 写入祝福表并立即生效，
# 驳回 = 标记 rejected（不再自动匹配）。情形 A 已经在字段发现流程里自动祝福，不出现在这里。

@admin_router.get("/formula-blessings")
def list_formula_blessings(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """返回待人工祝福的公式变体队列（情形 B/C/D）。"""
    _, err = _require_admin(authorization, x_admin_token)
    if err:
        return err
    try:
        items = script_formula_promotion_service.list_pending_review()
        return {"ok": True, "items": items}
    except Exception as exc:
        logger.error("[AdminRouter] list_formula_blessings failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


class BlessFormulaBody(BaseModel):
    verifier_contract: str | None = None
    # code review 建议#4 修复：情形 B/C/D 里如果是全新候选指标（没有正式 label/unit），
    # 审核员应该在这里补全，否则会退化用 metric_id 本身当 label。对已注册指标做信任升级时
    # 这两个字段不生效（不影响其既有 schema）。
    label: str | None = None
    unit: str | None = None


@admin_router.post("/formula-blessings/{promotion_key:path}/bless")
def bless_formula_variant(
    promotion_key: str,
    body: BlessFormulaBody,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """人工祝福一次；此后任何匹配同一 (script_hash, metric_id, variant) 的项目自动套用。"""
    admin_user, err = _require_admin(authorization, x_admin_token)
    if err:
        return err
    try:
        ok, message = script_formula_promotion_service.bless_pending(
            promotion_key,
            reviewer=str((admin_user or {}).get("username") or ""),
            verifier_contract=body.verifier_contract,
            label=body.label,
            unit=body.unit,
        )
        if not ok:
            return JSONResponse(status_code=400, content={"ok": False, "message": message})
        return {"ok": True, "message": message}
    except Exception as exc:
        logger.error("[AdminRouter] bless_formula_variant failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})


class RejectFormulaBody(BaseModel):
    note: str = ""


@admin_router.post("/formula-blessings/{promotion_key:path}/reject")
def reject_formula_variant(
    promotion_key: str,
    body: RejectFormulaBody,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    admin_user, err = _require_admin(authorization, x_admin_token)
    if err:
        return err
    try:
        ok, message = script_formula_promotion_service.reject_pending(
            promotion_key,
            reviewer=str((admin_user or {}).get("username") or ""),
            note=body.note,
        )
        if not ok:
            return JSONResponse(status_code=400, content={"ok": False, "message": message})
        return {"ok": True, "message": message}
    except Exception as exc:
        logger.error("[AdminRouter] reject_formula_variant failed: %s", exc)
        return JSONResponse(status_code=500, content={"ok": False, "message": "服务器内部错误"})
