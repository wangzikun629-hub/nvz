"""
看板"文件"列 — 纯附件存储服务

职责：
  与知识库 / 智能解析完全无关，只是把文件存到本地磁盘、记一条元数据，
  能再下载回来、能删除。不做文本抽取、不做向量化、不经过 KB 服务。

存储布局：app/data/kanban_files/{scope}/{record_id}/{file_id}__{原始文件名}
"""
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "kanban_files"

MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

_FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def is_valid_file_id(file_id: str) -> bool:
    return bool(_FILE_ID_RE.match(file_id or ""))


def is_valid_scope(scope: str) -> bool:
    return scope in ("rd", "cs")


def _record_dir(scope: str, record_id: int) -> Path:
    d = _DATA_DIR / scope / str(int(record_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _guess_content_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc": "application/msword",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls": "application/vnd.ms-excel",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "ppt": "application/vnd.ms-powerpoint",
        "txt": "text/plain",
        "md": "text/markdown",
        "csv": "text/csv",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "zip": "application/zip",
    }.get(ext, "application/octet-stream")


def save_file(scope: str, record_id: int, filename: str, content: bytes) -> Dict[str, Any]:
    """保存文件到磁盘，返回可直接塞进 attachments 列表的条目。"""
    file_id = uuid.uuid4().hex
    safe_name = os.path.basename((filename or "file").strip()) or "file"
    target = _record_dir(scope, record_id) / f"{file_id}__{safe_name}"
    with open(target, "wb") as f:
        f.write(content)
    return {
        "name": safe_name,
        "kind": "file",
        "file_id": file_id,
        "size": len(content),
        "content_type": _guess_content_type(safe_name),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }


def load_file(scope: str, record_id: int, file_id: str) -> Optional[Tuple[bytes, str, str]]:
    """按 file_id 读回文件内容，返回 (content, filename, content_type)，找不到返回 None。"""
    if not is_valid_file_id(file_id):
        return None
    d = _record_dir(scope, record_id)
    for p in d.glob(f"{file_id}__*"):
        name = p.name.split("__", 1)[1] if "__" in p.name else p.name
        return p.read_bytes(), name, _guess_content_type(name)
    return None


def delete_file(scope: str, record_id: int, file_id: str) -> bool:
    if not is_valid_file_id(file_id):
        return False
    d = _record_dir(scope, record_id)
    found = False
    for p in d.glob(f"{file_id}__*"):
        try:
            p.unlink()
            found = True
        except OSError:
            pass
    return found


def upsert_file_attachment(existing: list, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    attachments = [a for a in (existing or []) if isinstance(a, dict)]
    attachments.append(entry)
    return attachments


def remove_file_attachment(existing: list, file_id: str) -> List[Dict[str, Any]]:
    return [
        a for a in (existing or [])
        if isinstance(a, dict) and not (a.get("kind") == "file" and a.get("file_id") == file_id)
    ]
