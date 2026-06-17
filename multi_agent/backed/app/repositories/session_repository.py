import asyncio
import json
from datetime import datetime
from enum import Enum
from json import JSONDecodeError
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from multi_agent.backed.app.infrastructure.logging.logger import logger


class SessionRepository:
    """会话数据仓储类。

    负责处理底层的会话文件存储、读取和文件系统操作。
    使用 pathlib 进行现代化的路径管理。
    """

    # 存储目录名称常量
    STORAGE_DIR_NAME = "user_memories"

    def __init__(self):
        """初始化 SessionRepository。

        自动定位并创建存储根目录。
        """

        current_file = Path(__file__).resolve()

        self._base_dir = current_file.parent.parent

        # 拼接存储路径: backend/app/user_memories
        self._storage_root = self._base_dir / self.STORAGE_DIR_NAME

        # 确保存储根目录存在
        self._storage_root.mkdir(parents=True, exist_ok=True)
        self._sessions_metadata_cache: dict[
            str, tuple[tuple[tuple[str, float, int], ...], List[Tuple[str, str, Union[List, Exception]]]]
        ] = {}


    def load_session(
            self, user_id: str, session_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """从文件加载会话数据。

        Args:
            user_id: 用户ID。
            session_id: 会话ID。

        Returns:
            List[Dict]: 解析后的会话数据。
            None: 如果文件不存在。

        Raises:
            json.JSONDecodeError: 如果文件内容损坏。
        """
        file_path = self._get_file_path(user_id, session_id)

        if not file_path.exists():
            return None

        return self._load_json_file(file_path)

    def save_session(
            self, user_id: str, session_id: str, data: List[Dict[str, Any]]
    ) -> None:
        """保存会话数据到文件。

        Args:
            user_id: 用户ID。
            session_id: 会话ID。
            data: 要保存的数据列表。
        """
        file_path = self._get_file_path(user_id, session_id)

        # 确保用户的个人目录存在 (懒加载模式)
        if not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = file_path.with_suffix(f"{file_path.suffix}.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=self._json_default)
            tmp_path.replace(file_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
        self._sessions_metadata_cache.pop(user_id, None)

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """删除指定会话文件。"""
        file_path = self._get_file_path(user_id, session_id)
        if not file_path.exists():
            return False

        file_path.unlink()
        self._sessions_metadata_cache.pop(user_id, None)
        return True

    # ------------------------------------------------------------------ async

    async def aload_session(
        self, user_id: str, session_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """事件循环非阻塞版 load_session（asyncio.to_thread 包装）。"""
        return await asyncio.to_thread(self.load_session, user_id, session_id)

    async def asave_session(
        self, user_id: str, session_id: str, data: List[Dict[str, Any]]
    ) -> None:
        """事件循环非阻塞版 save_session（asyncio.to_thread 包装）。"""
        await asyncio.to_thread(self.save_session, user_id, session_id, data)

    def get_all_sessions_metadata(
            self, user_id: str
    ) -> List[Tuple[str, str, Union[List, Exception]]]:
        """获取用户所有会话的元数据和内容。

        Args:
            user_id: 用户ID。

        Returns:
            List[Tuple]: 包含 (session_id, create_time, data_or_error) 的列表。
        """
        user_dir = self._get_user_directory(user_id)

        if not user_dir.exists():
            logger.warning(f"用户目录不存在: {user_id}")
            return []

        signature = tuple(
            sorted(
                (
                    file_path.name,
                    file_path.stat().st_mtime,
                    file_path.stat().st_size,
                )
                for file_path in user_dir.glob("*.json")
            )
        )
        cached = self._sessions_metadata_cache.get(user_id)
        if cached and cached[0] == signature:
            return cached[1]

        results = []

        try:
            # 遍历目录下所有 .json 文件
            for file_path in user_dir.glob("*.json"):
                session_id = file_path.stem  # 获取文件名不带后缀部分

                # 获取文件创建时间
                stat = file_path.stat()
                create_time = datetime.fromtimestamp(stat.st_ctime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                try:
                    data = self._load_json_file(file_path)
                    results.append((session_id, create_time, data))
                except Exception as e:
                    # 读取或解析失败，返回异常对象
                    logger.error(f"读取会话文件 {file_path.name} 失败: {e}")
                    results.append((session_id, create_time, e))

        except Exception as e:
            logger.error(f"遍历用户 {user_id} 会话目录失败: {e}")
            return []

        self._sessions_metadata_cache[user_id] = (signature, results)
        return results

    def _get_user_directory(self, user_id: str) -> Path:
        """获取用户的记忆文件夹路径对象。"""
        return self._storage_root / user_id

    def _get_file_path(self, user_id: str, session_id: str) -> Path:
        """获取具体会话文件的路径对象。"""
        return self._get_user_directory(user_id) / f"{session_id}.json"

    def _load_json_file(self, file_path: Path) -> List[Dict[str, Any]]:
        text = file_path.read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except JSONDecodeError:
            repaired = self._repair_legacy_system_header(text)
            if repaired == text:
                raise
            return json.loads(repaired)

    @staticmethod
    def _json_default(value: Any):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (datetime, Path)):
            return str(value)
        if isinstance(value, set):
            return list(value)
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "dict"):
            return value.dict()
        return str(value)

    @staticmethod
    def _repair_legacy_system_header(text: str) -> str:
        """Recover sessions written with a malformed mojibake system message."""
        replacement = (
            '[\n'
            '  {\n'
            '    "role": "system",\n'
            '    "content": "你是一个有记忆的智能体助手，请基于上下文历史会话回答用户问题。"\n'
            '  },'
        )
        return re.sub(
            r'^\s*\[\s*\{\s*"role"\s*:\s*"system"\s*,\s*"content"\s*:\s*".*?\n\s*\}\s*,',
            replacement,
            text,
            count=1,
            flags=re.DOTALL,
        )


# 全局单例
session_repository = SessionRepository()
