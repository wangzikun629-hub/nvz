import asyncio
import json
import re
from datetime import datetime
from enum import Enum
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from multi_agent.backed.app.infrastructure.logging.logger import logger


class SessionRepository:
    STORAGE_DIR_NAME = "user_memories"
    SUMMARY_DIR_NAME = ".session_summaries"

    def __init__(self):
        current_file = Path(__file__).resolve()
        self._base_dir = current_file.parent.parent
        self._storage_root = self._base_dir / self.STORAGE_DIR_NAME
        self._storage_root.mkdir(parents=True, exist_ok=True)
        self._sessions_metadata_cache: dict[
            str, tuple[tuple[tuple[str, float, int], ...], List[Tuple[str, str, Union[List, Exception]]]]
        ] = {}
        self._sessions_summary_cache: dict[
            str, tuple[tuple[tuple[str, float, int], ...], List[Dict[str, Any]]]
        ] = {}

    def load_session(
        self, user_id: str, session_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        file_path = self._get_file_path(user_id, session_id)
        if not file_path.exists():
            return None
        return self._load_json_file(file_path)

    def save_session(
        self, user_id: str, session_id: str, data: List[Dict[str, Any]]
    ) -> None:
        file_path = self._get_file_path(user_id, session_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = file_path.with_suffix(f"{file_path.suffix}.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=self._json_default)
            tmp_path.replace(file_path)
            self._write_session_summary(user_id, session_id, data, file_path.stat())
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
        self._sessions_metadata_cache.pop(user_id, None)
        self._sessions_summary_cache.pop(user_id, None)

    def delete_session(self, user_id: str, session_id: str) -> bool:
        file_path = self._get_file_path(user_id, session_id)
        if not file_path.exists():
            return False

        file_path.unlink()
        summary_path = self._get_summary_path(user_id, session_id)
        if summary_path.exists():
            summary_path.unlink()
        self._sessions_metadata_cache.pop(user_id, None)
        self._sessions_summary_cache.pop(user_id, None)
        return True

    async def aload_session(
        self, user_id: str, session_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        return await asyncio.to_thread(self.load_session, user_id, session_id)

    async def asave_session(
        self, user_id: str, session_id: str, data: List[Dict[str, Any]]
    ) -> None:
        await asyncio.to_thread(self.save_session, user_id, session_id, data)

    def get_all_sessions_metadata(
        self, user_id: str
    ) -> List[Tuple[str, str, Union[List, Exception]]]:
        user_dir = self._get_user_directory(user_id)
        if not user_dir.exists():
            logger.warning("user directory does not exist: %s", user_id)
            return []

        signature = self._session_files_signature(user_dir)
        cached = self._sessions_metadata_cache.get(user_id)
        if cached and cached[0] == signature:
            return cached[1]

        results = []
        try:
            for file_path in user_dir.glob("*.json"):
                stat = file_path.stat()
                create_time = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
                try:
                    data = self._load_json_file(file_path)
                    results.append((file_path.stem, create_time, data))
                except Exception as exc:
                    logger.error("read session file failed file=%s error=%s", file_path.name, exc)
                    results.append((file_path.stem, create_time, exc))
        except Exception as exc:
            logger.error("list user sessions failed user=%s error=%s", user_id, exc)
            return []

        self._sessions_metadata_cache[user_id] = (signature, results)
        return results

    def get_all_sessions_summary_metadata(self, user_id: str) -> List[Dict[str, Any]]:
        user_dir = self._get_user_directory(user_id)
        if not user_dir.exists():
            logger.warning("user directory does not exist: %s", user_id)
            return []

        files = list(user_dir.glob("*.json"))
        signature = tuple(
            sorted(
                (
                    file_path.name,
                    file_path.stat().st_mtime,
                    file_path.stat().st_size,
                )
                for file_path in files
            )
        )
        cached = self._sessions_summary_cache.get(user_id)
        if cached and cached[0] == signature:
            return cached[1]

        results = []
        for file_path in files:
            try:
                summary = self._load_or_create_session_summary(user_id, file_path)
            except Exception as exc:
                stat = file_path.stat()
                logger.error("read session summary failed file=%s error=%s", file_path.name, exc)
                summary = {
                    "session_id": file_path.stem,
                    "create_time": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "total_messages": 0,
                    "preview": "",
                    "error": "无法读取会话摘要",
                }
            results.append(summary)

        self._sessions_summary_cache[user_id] = (signature, results)
        return results

    def _session_files_signature(self, user_dir: Path) -> tuple[tuple[str, float, int], ...]:
        return tuple(
            sorted(
                (
                    file_path.name,
                    file_path.stat().st_mtime,
                    file_path.stat().st_size,
                )
                for file_path in user_dir.glob("*.json")
            )
        )

    def _get_user_directory(self, user_id: str) -> Path:
        return self._storage_root / user_id

    def _get_file_path(self, user_id: str, session_id: str) -> Path:
        return self._get_user_directory(user_id) / f"{session_id}.json"

    def _get_summary_path(self, user_id: str, session_id: str) -> Path:
        return self._get_user_directory(user_id) / self.SUMMARY_DIR_NAME / f"{session_id}.json"

    def _load_or_create_session_summary(self, user_id: str, file_path: Path) -> Dict[str, Any]:
        stat = file_path.stat()
        session_id = file_path.stem
        summary_path = self._get_summary_path(user_id, session_id)
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if (
                summary.get("_source_mtime") == stat.st_mtime
                and summary.get("_source_size") == stat.st_size
            ):
                return self._public_summary(summary)

        data = self._load_json_file(file_path)
        return self._write_session_summary(user_id, session_id, data, stat)

    def _write_session_summary(
        self,
        user_id: str,
        session_id: str,
        data: List[Dict[str, Any]],
        stat,
    ) -> Dict[str, Any]:
        summary_path = self._get_summary_path(user_id, session_id)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary = self._build_session_summary(session_id, data, stat)
        tmp_path = summary_path.with_suffix(f"{summary_path.suffix}.tmp")
        try:
            tmp_path.write_text(
                json.dumps(summary, ensure_ascii=False, default=self._json_default),
                encoding="utf-8",
            )
            tmp_path.replace(summary_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
        return self._public_summary(summary)

    def _build_session_summary(
        self,
        session_id: str,
        data: List[Dict[str, Any]],
        stat,
    ) -> Dict[str, Any]:
        visible_messages = [msg for msg in data if msg.get("role") != "system"]
        first_user = next((msg for msg in data if msg.get("role") == "user"), None)
        return {
            "session_id": session_id,
            "create_time": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "total_messages": len(visible_messages),
            "preview": self._preview_text(first_user.get("content") if first_user else ""),
            "_source_mtime": stat.st_mtime,
            "_source_size": stat.st_size,
        }

    @staticmethod
    def _preview_text(content: Any) -> str:
        if isinstance(content, str):
            return content[:200]
        if content is None:
            return ""
        return json.dumps(content, ensure_ascii=False)[:200]

    @staticmethod
    def _public_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in summary.items() if not key.startswith("_")}

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
        replacement = (
            '[\n'
            '  {\n'
            '    "role": "system",\n'
            '    "content": "\u4f60\u662f\u4e00\u4e2a\u6709\u8bb0\u5fc6\u7684\u667a\u80fd\u4f53\u52a9\u624b\uff0c\u8bf7\u57fa\u4e8e\u4e0a\u4e0b\u6587\u5386\u53f2\u4f1a\u8bdd\u56de\u7b54\u7528\u6237\u95ee\u9898\u3002"\n'
            '  },'
        )
        return re.sub(
            r'^\s*\[\s*\{\s*"role"\s*:\s*"system"\s*,\s*"content"\s*:\s*".*?\n\s*\}\s*,',
            replacement,
            text,
            count=1,
            flags=re.DOTALL,
        )


session_repository = SessionRepository()
