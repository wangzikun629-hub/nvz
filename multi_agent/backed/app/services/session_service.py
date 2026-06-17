from __future__ import annotations

from json import JSONDecodeError
from typing import Any

from multi_agent.backed.app.infrastructure.async_lock_manager import key_lock
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.repositories.session_repository import session_repository

_REGISTRY = "session"


class SessionService:
    DEFAULT_SESSION_ID = "default_session"

    def __init__(self):
        self._repo = session_repository

    # ------------------------------------------------------------------ sync (兼容保留)

    def prepare_history(self, user_id: str, session_id: str, user_input: str) -> list[dict[str, Any]]:
        chat_history = self.load_history(user_id, session_id)
        chat_history.append({"role": "user", "content": user_input})
        return chat_history

    def load_history(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        target_session_id = session_id if session_id else self.DEFAULT_SESSION_ID
        try:
            session_history = self._repo.load_session(user_id, target_session_id)
            if session_history is None:
                return self._init_system_message(target_session_id)
            return session_history
        except JSONDecodeError as exc:
            logger.error("load_history failed user=%s session=%s error=%s", user_id, session_id, str(exc))
            return [{"role": "system", "content": "会话文件读取失败"}]

    def save_history(self, user_id: str, session_id: str, chat_history: list[dict[str, Any]]) -> None:
        if chat_history is None:
            return
        target_session_id = session_id if session_id else self.DEFAULT_SESSION_ID
        try:
            self._repo.save_session(user_id, target_session_id, chat_history)
        except Exception as exc:
            logger.error("save_history failed user=%s session=%s error=%s", user_id, session_id, str(exc))

    def append_message(self, user_id: str, session_id: str, role: str, content: Any) -> None:
        history = self.load_history(user_id, session_id)
        history.append({"role": role, "content": content})
        self.save_history(user_id, session_id, history)

    # ------------------------------------------------------------------ async

    async def aprepare_history(
        self, user_id: str, session_id: str, user_input: str
    ) -> list[dict[str, Any]]:
        """非阻塞读取历史，追加当前用户消息（只读，不持锁）。"""
        chat_history = await self.aload_history(user_id, session_id)
        chat_history.append({"role": "user", "content": user_input})
        return chat_history

    async def aload_history(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        """非阻塞读取会话历史（只读，不持锁）。"""
        target_session_id = session_id if session_id else self.DEFAULT_SESSION_ID
        try:
            session_history = await self._repo.aload_session(user_id, target_session_id)
            if session_history is None:
                return self._init_system_message(target_session_id)
            return session_history
        except JSONDecodeError as exc:
            logger.error("aload_history failed user=%s session=%s error=%s", user_id, session_id, str(exc))
            return [{"role": "system", "content": "会话文件读取失败"}]

    async def asave_history(
        self, user_id: str, session_id: str, chat_history: list[dict[str, Any]]
    ) -> None:
        """非阻塞写入（调用方负责持锁或保证不并发写同一 session）。"""
        if chat_history is None:
            return
        target_session_id = session_id if session_id else self.DEFAULT_SESSION_ID
        try:
            await self._repo.asave_session(user_id, target_session_id, chat_history)
        except Exception as exc:
            logger.error("asave_history failed user=%s session=%s error=%s", user_id, session_id, str(exc))

    async def aappend_message(
        self, user_id: str, session_id: str, role: str, content: Any
    ) -> None:
        """非阻塞 append_message，持 per-session 锁保护 RMW。"""
        async with key_lock(_REGISTRY, user_id, session_id or self.DEFAULT_SESSION_ID):
            history = await self.aload_history(user_id, session_id)
            history.append({"role": role, "content": content})
            await self.asave_history(user_id, session_id, history)

    def get_session_messages(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        history = self.load_history(user_id, session_id)
        return [
            message
            for message in history
            if message.get("role") in {"user", "assistant"}
        ]

    def get_latest_project_analysis(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        history = self.load_history(user_id, session_id)
        for message in reversed(history):
            if message.get("role") != "analysis":
                continue
            content = message.get("content")
            if isinstance(content, dict):
                return content
        return None

    def get_all_sessions_memory(self, user_id: str) -> list[dict[str, Any]]:
        raw_sessions = self._repo.get_all_sessions_metadata(user_id)
        formatted_sessions = []

        for session_id, create_time, data_or_error in raw_sessions:
            session_item = {
                "session_id": session_id,
                "create_time": create_time,
            }
            if isinstance(data_or_error, Exception):
                logger.error("read session failed session=%s error=%s", session_id, str(data_or_error))
                session_item.update(
                    {
                        "memory": [],
                        "total_messages": 0,
                        "error": "无法读取会话数据",
                    }
                )
            else:
                memory = data_or_error
                user_visible_memory = [msg for msg in memory if msg.get("role") != "system"]
                session_item.update(
                    {
                        "memory": user_visible_memory,
                        "total_messages": len(user_visible_memory),
                    }
                )
            formatted_sessions.append(session_item)

        formatted_sessions.sort(key=lambda item: item.get("create_time") or "", reverse=True)
        return formatted_sessions

    def delete_session(self, user_id: str, session_id: str) -> bool:
        if not user_id or not session_id:
            return False
        target_session_id = session_id if session_id else self.DEFAULT_SESSION_ID
        try:
            return self._repo.delete_session(user_id, target_session_id)
        except Exception as exc:
            logger.error("delete_session failed user=%s session=%s error=%s", user_id, session_id, str(exc))
            return False

    def _init_system_message(self, session_id: str) -> list[dict[str, Any]]:
        return [
            {
                "role": "system",
                "content": f"你是一个有记忆的智能体助手，请基于上下文历史会话回答用户问题（会话ID {session_id}）。",
            }
        ]


session_service = SessionService()
