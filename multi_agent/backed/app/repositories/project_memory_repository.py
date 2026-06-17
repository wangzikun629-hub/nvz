from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from json import JSONDecodeError
from pathlib import Path
from typing import Any


class ProjectMemoryRepository:
    STORAGE_DIR_NAME = "project_memories"

    def __init__(self):
        current_file = Path(__file__).resolve()
        self._base_dir = current_file.parent.parent
        self._storage_root = self._base_dir / self.STORAGE_DIR_NAME
        self._storage_root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _path(self, project_id: str) -> Path:
        normalized = str(project_id or "").strip()
        if not normalized or not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", normalized):
            raise ValueError("project_id contains unsafe filename characters")
        if normalized in {".", ".."}:
            raise ValueError("project_id is not a valid memory identifier")
        path = (self._storage_root / f"{normalized}.json").resolve()
        root = self._storage_root.resolve()
        if root not in path.parents:
            raise ValueError("project memory path escapes storage root")
        return path

    def lock_for(self, project_id: str) -> threading.RLock:
        normalized = str(project_id or "").strip()
        with self._locks_guard:
            return self._locks.setdefault(normalized, threading.RLock())

    def load_memory(self, project_id: str) -> dict[str, Any] | None:
        path = self._path(project_id)
        with self.lock_for(project_id):
            if not path.exists():
                return None
            try:
                with path.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
            except JSONDecodeError:
                return None

    def save_memory(self, project_id: str, payload: dict[str, Any]) -> None:
        path = self._path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_for(project_id):
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.stem}.",
                suffix=".tmp",
                delete=False,
            )
            temporary_path = Path(handle.name)
            try:
                with handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, path)
            finally:
                if temporary_path.exists():
                    temporary_path.unlink(missing_ok=True)


project_memory_repository = ProjectMemoryRepository()
