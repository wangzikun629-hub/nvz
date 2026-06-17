import base64
import hashlib
import hmac
import json
import time
import uuid

from multi_agent.backed.app.config.settings import settings


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload_part: str) -> str:
    signature = hmac.new(
        settings.AUTH_TOKEN_SECRET.encode("utf-8"),
        payload_part.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(signature)


def issue_auth_token(user_id: str, username: str) -> dict:
    now = int(time.time())
    expires_at = now + int(settings.AUTH_TOKEN_EXPIRE_SECONDS)
    session_id = uuid.uuid4().hex
    payload = {
        "user_id": user_id,
        "username": username,
        "sid": session_id,
        "iat": now,
        "exp": expires_at,
    }
    payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    token = f"{payload_part}.{_sign(payload_part)}"
    return {
        "token": token,
        "session_id": session_id,
        "expires_at": expires_at,
    }


def verify_auth_token(token: str) -> dict | None:
    if not token or "." not in token:
        return None

    payload_part, signature_part = token.split(".", 1)
    expected_signature = _sign(payload_part)
    if not hmac.compare_digest(signature_part, expected_signature):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None

    if int(payload.get("exp", 0)) <= int(time.time()):
        return None

    if not payload.get("user_id"):
        return None
    session_id = str(payload.get("sid", "")).strip()
    if not session_id:
        return None

    from multi_agent.backed.app.repositories import auth_session_repository

    if not auth_session_repository.is_token_valid(session_id, token):
        return None

    return payload
