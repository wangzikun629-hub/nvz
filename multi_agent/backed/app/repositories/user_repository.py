"""
用户账号数据仓储

负责 users 表的 CRUD 操作，密码使用 bcrypt hash 存储。
"""
import bcrypt
from typing import Optional

from multi_agent.backed.app.infrastructure.database.database_pool import pool
from multi_agent.backed.app.infrastructure.logging.logger import logger

# DDL（首次启动自动建表）
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(128) NOT NULL,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def ensure_table() -> None:
    """启动时调用，确保 users 表存在。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


# ── 密码工具 ────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── 数据操作 ─────────────────────────────────────────────────────────────────

def get_user_by_username(username: str) -> Optional[dict]:
    """查找用户，不存在返回 None。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password_hash, created_at FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "password_hash": row[2],
            "created_at": str(row[3]),
        }
    finally:
        conn.close()


def create_user(username: str, plain_password: str) -> dict:
    """
    创建用户，返回新用户信息。
    若用户名已存在则抛出 ValueError。
    """
    if get_user_by_username(username):
        raise ValueError(f"用户名 '{username}' 已存在")

    password_hash = hash_password(plain_password)
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, password_hash),
            )
            new_id = cur.lastrowid
        conn.commit()
        logger.info(f"[UserRepository] 注册新用户: {username} (id={new_id})")
        return {"id": new_id, "username": username}
    finally:
        conn.close()


def authenticate_user(username: str, plain_password: str) -> Optional[dict]:
    """
    验证用户名 + 密码。
    成功返回用户 dict（不含 password_hash），失败返回 None。
    """
    user = get_user_by_username(username)
    if user is None:
        return None
    if not verify_password(plain_password, user["password_hash"]):
        return None
    return {"id": user["id"], "username": user["username"]}


# ── 管理员接口 ────────────────────────────────────────────────────────────────

def list_all_users() -> list[dict]:
    """返回所有用户列表（管理员专用）。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, created_at FROM users ORDER BY created_at DESC"
            )
            rows = cur.fetchall() or []
        return [
            {"id": row[0], "username": row[1], "created_at": str(row[2])}
            for row in rows
        ]
    finally:
        conn.close()


def get_user_count() -> int:
    """返回用户总数（管理员专用）。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def update_password(user_id: int, new_plain_password: str) -> bool:
    """重置指定用户密码（管理员专用）。"""
    new_hash = hash_password(new_plain_password)
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (new_hash, int(user_id)),
            )
            changed = cur.rowcount > 0
        conn.commit()
        if changed:
            logger.info(f"[UserRepository][Admin] 重置用户 id={user_id} 的密码")
        return changed
    finally:
        conn.close()


def delete_user(user_id: int) -> bool:
    """删除指定用户（管理员专用）。"""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (int(user_id),))
            changed = cur.rowcount > 0
        conn.commit()
        if changed:
            logger.info(f"[UserRepository][Admin] 删除用户 id={user_id}")
        return changed
    finally:
        conn.close()
