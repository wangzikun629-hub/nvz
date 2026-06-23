import hashlib
from datetime import datetime
from typing import Optional

from multi_agent.backed.app.infrastructure.database.database_pool import pool


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS auth_sessions (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL UNIQUE,
    user_id INT UNSIGNED NOT NULL,
    username VARCHAR(64) NOT NULL,
    token_hash CHAR(64) NOT NULL,
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME NULL DEFAULT NULL,
    last_used_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_user_id (user_id),
    KEY idx_expires_at (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_table() -> None:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


def purge_expired_sessions() -> int:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM auth_sessions
                WHERE expires_at <= UTC_TIMESTAMP() OR revoked_at IS NOT NULL
                """
            )
            deleted = int(cur.rowcount or 0)
        conn.commit()
        return deleted
    finally:
        conn.close()


def create_session(session_id: str, user_id: int, username: str, token: str, expires_at_unix: int) -> dict:
    purge_expired_sessions()
    expires_at = datetime.utcfromtimestamp(int(expires_at_unix))
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO auth_sessions (session_id, user_id, username, token_hash, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (session_id, int(user_id), username, _hash_token(token), expires_at),
            )
        conn.commit()
        return {
            "session_id": session_id,
            "user_id": int(user_id),
            "username": username,
            "expires_at": expires_at_unix,
        }
    finally:
        conn.close()


def get_active_session(session_id: str) -> Optional[dict]:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, user_id, username, token_hash, expires_at, revoked_at
                FROM auth_sessions
                WHERE session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "session_id": row[0],
            "user_id": int(row[1]),
            "username": row[2],
            "token_hash": row[3],
            "expires_at": row[4],
            "revoked_at": row[5],
        }
    finally:
        conn.close()


def touch_session(session_id: str) -> None:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth_sessions SET last_used_at = CURRENT_TIMESTAMP WHERE session_id = %s",
                (session_id,),
            )
        conn.commit()
    finally:
        conn.close()


def list_user_sessions(user_id: int) -> list[dict]:
    purge_expired_sessions()
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, user_id, username, expires_at, last_used_at, created_at
                FROM auth_sessions
                WHERE user_id = %s AND revoked_at IS NULL AND expires_at > UTC_TIMESTAMP()
                ORDER BY last_used_at DESC, created_at DESC
                """,
                (int(user_id),),
            )
            rows = cur.fetchall() or []
        return [
            {
                "session_id": row[0],
                "user_id": int(row[1]),
                "username": row[2],
                "expires_at": row[3],
                "last_used_at": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]
    finally:
        conn.close()


def revoke_session(session_id: str) -> bool:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE session_id = %s AND revoked_at IS NULL
                """,
                (session_id,),
            )
            changed = cur.rowcount > 0
        conn.commit()
        return changed
    finally:
        conn.close()


def revoke_user_session(user_id: int, session_id: str) -> bool:
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE session_id = %s AND user_id = %s AND revoked_at IS NULL
                """,
                (session_id, int(user_id)),
            )
            changed = cur.rowcount > 0
        conn.commit()
        return changed
    finally:
        conn.close()


def is_token_valid(session_id: str, token: str) -> bool:
    purge_expired_sessions()
    session = get_active_session(session_id)
    if not session:
        return False
    if session["revoked_at"] is not None:
        return False
    if session["expires_at"] <= datetime.utcnow():
        return False
    if session["token_hash"] != _hash_token(token):
        return False
    touch_session(session_id)
    return True
