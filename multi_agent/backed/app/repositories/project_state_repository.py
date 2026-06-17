from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class ProjectStateRepository:
    STORAGE_DIR_NAME = "project_session_states"

    def __init__(self):
        current_file = Path(__file__).resolve()
        self._base_dir = current_file.parent.parent
        self._storage_root = self._base_dir / self.STORAGE_DIR_NAME
        self._storage_root.mkdir(parents=True, exist_ok=True)

    def load_state(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        path = self._get_file_path(user_id, session_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def save_state(self, user_id: str, session_id: str, state: dict[str, Any]) -> None:
        path = self._get_file_path(user_id, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写 .tmp 再 rename，避免写入中断导致文件损坏
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2)
            tmp_path.replace(path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------ async

    async def aload_state(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        """事件循环非阻塞版 load_state（asyncio.to_thread 包装）。"""
        return await asyncio.to_thread(self.load_state, user_id, session_id)

    async def asave_state(self, user_id: str, session_id: str, state: dict[str, Any]) -> None:
        """事件循环非阻塞版 save_state（asyncio.to_thread 包装）。"""
        await asyncio.to_thread(self.save_state, user_id, session_id, state)

    def _get_file_path(self, user_id: str, session_id: str) -> Path:
        return self._storage_root / user_id / f"{session_id}.json"


project_state_repository = ProjectStateRepository()
