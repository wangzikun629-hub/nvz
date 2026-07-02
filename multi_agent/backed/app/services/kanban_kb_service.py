"""
看板知识库上传代理服务

职责：
  1. 将文件代理转发到 KB 服务的 /upload 端点
  2. 代理轮询 /upload/{task_id} 状态
  3. 更新对应看板 record 的 attachments 字段

KB 服务认证：使用服务端 APP_API_KEY（通过 X-Api-Key 头），
避免前端直连 KB 服务的 CORS / 认证问题。
"""
import logging
import re
from typing import Any, Dict, Optional

import httpx

from multi_agent.backed.app.config.settings import settings

logger = logging.getLogger(__name__)

# document_id 为 uuid4().hex（32 位小写十六进制），页面图片固定为 page_0001.png 格式
_DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_IMG_FILENAME_RE = re.compile(r"^page_\d{4}\.png$")


def is_valid_doc_id(doc_id: str) -> bool:
    return bool(_DOC_ID_RE.match(doc_id or ""))


def is_valid_image_filename(filename: str) -> bool:
    return bool(_IMG_FILENAME_RE.match(filename or ""))

# KB 服务地址（来自 .env KNOWLEDGE_BASE_URL）
def _kb_base() -> str:
    url = (settings.KNOWLEDGE_BASE_URL or "").rstrip("/")
    if not url:
        raise RuntimeError("KNOWLEDGE_BASE_URL 未配置，无法调用知识库服务")
    return url


def _kb_headers() -> Dict[str, str]:
    """Use the dedicated internal service secret for app-to-KB calls."""
    key = settings.KB_SERVICE_SECRET or ""
    return {"X-Service-Key": key}


# ── 上传 ──────────────────────────────────────────────────────────────────────

async def upload_file_to_kb(
    file_content: bytes,
    filename: str,
    partition_id: str,          # "kanban_rd" 或 "kanban_cs"
) -> Dict[str, Any]:
    """
    将文件上传到 KB 服务。
    返回 KB 服务的响应体：{status, task_id, file_id, file_name, chunks_added, message}
    """
    base = _kb_base()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{base}/upload",
            headers=_kb_headers(),
            files={"file": (filename, file_content, _guess_content_type(filename))},
            data={"partition_id": partition_id},
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"KB 上传失败 [{resp.status_code}]: {resp.text[:300]}")
    return resp.json()


async def poll_kb_status(task_id: str) -> Dict[str, Any]:
    """
    查询 KB 服务的上传任务状态。
    返回 {status, chunks_added, message, file_name, task_id, file_id}
    status: processing | success | error
    """
    base = _kb_base()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{base}/upload/{task_id}",
            headers=_kb_headers(),
        )
    if resp.status_code == 404:
        return {"status": "error", "message": "task not found", "chunks_added": 0}
    if resp.status_code != 200:
        raise RuntimeError(f"KB 状态查询失败 [{resp.status_code}]: {resp.text[:200]}")
    return resp.json()


# ── attachments 操作（普通解析 / plain 通道）───────────────────────────────────
# 与智能解析通道共用同一份 attachments 结构（name / parse_status / chunks_added），
# 用 "channel" 字段区分来源，便于前端复用同一套状态展示逻辑。

def upsert_attachment(
    existing: list,
    filename: str,
    task_id: Optional[str] = None,
    kb_file_id: Optional[int] = None,
    partition_id: str = "kanban_cs",
    parse_status: str = "uploading",
    chunks_added: int = 0,
) -> list:
    """
    在 attachments 列表中插入或更新普通解析通道的条目。
    """
    attachments = [a for a in (existing or []) if isinstance(a, dict)]
    for att in attachments:
        if att.get("name") == filename and att.get("kind") != "file":
            att["channel"]      = "plain"
            att["task_id"]      = task_id or att.get("task_id")
            att["kb_file_id"]   = kb_file_id if kb_file_id is not None else att.get("kb_file_id")
            att["partition_id"] = partition_id
            att["parse_status"] = parse_status
            att["chunks_added"] = chunks_added
            att.pop("doc_id", None)
            return attachments
    # 新条目
    attachments.append({
        "name":         filename,
        "channel":      "plain",
        "task_id":      task_id,
        "kb_file_id":   kb_file_id,
        "doc_id":       None,
        "summary_id":   None,
        "partition_id": partition_id,
        "parse_status": parse_status,
        "chunks_added": chunks_added,
    })
    return attachments


def update_attachment_by_task_id(
    existing: list,
    task_id: str,
    parse_status: str,
    chunks_added: int = 0,
    kb_file_id: Optional[int] = None,
) -> list:
    """
    按 task_id 精确定位附件条目并更新状态字段。
    不依赖 KB 服务返回的 file_name，避免与上传时存储的 name 不匹配导致重复条目。
    若找不到匹配项则不做任何修改（上传时 upsert 已保证条目存在）。
    """
    attachments = [a for a in (existing or []) if isinstance(a, dict)]
    for att in attachments:
        if att.get("task_id") == task_id:
            att["parse_status"] = parse_status
            att["chunks_added"] = chunks_added
            if kb_file_id is not None:
                att["kb_file_id"] = kb_file_id
            break
    return attachments


def kb_status_from_poll(poll_status: str) -> str:
    """将 KB 服务 /upload/{task_id} 返回的 status 映射到统一的 parse_status。"""
    return {
        "processing": "uploading",
        "success":    "indexed",
        "error":      "error",
    }.get(poll_status, "uploading")


async def delete_kb_file(file_id: int) -> bool:
    """删除普通解析通道已入库的文件（代理 KB 服务 DELETE /files/{file_id}）。"""
    base = _kb_base()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(f"{base}/files/{file_id}", headers=_kb_headers())
    return resp.status_code in (200, 204)


# ══════════════════════════════════════════════════════════════════════════════
# 智能解析通道（parser route）
# ══════════════════════════════════════════════════════════════════════════════

async def upload_file_to_parser(
    file_content: bytes,
    filename: str,
    partition_id: str,
) -> Dict[str, Any]:
    """上传文件到 KB 服务的智能解析通道（/parser/documents）。
    返回 {document_id, file_name, partition_id, parse_status, message}
    """
    base = _kb_base()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{base}/parser/documents",
            headers=_kb_headers(),
            files={"file": (filename, file_content, _guess_content_type(filename))},
            data={"partition_id": partition_id},
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Parser 上传失败 [{resp.status_code}]: {resp.text[:300]}")
    return resp.json()


async def trigger_parser_parse(doc_id: str) -> Dict[str, Any]:
    """触发指定文档的 LLM 摘要解析。"""
    base = _kb_base()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{base}/parser/documents/{doc_id}/parse",
            headers=_kb_headers(),
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"触发解析失败 [{resp.status_code}]: {resp.text[:200]}")
    return resp.json()


async def get_parser_document(doc_id: str) -> Dict[str, Any]:
    """查询智能解析文档状态。
    返回 {document_id, parse_status, page_count, file_name, ...}
    """
    base = _kb_base()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{base}/parser/documents/{doc_id}",
            headers=_kb_headers(),
        )
    if resp.status_code == 404:
        return {"document_id": doc_id, "parse_status": "error", "message": "document not found"}
    if resp.status_code != 200:
        raise RuntimeError(f"状态查询失败 [{resp.status_code}]: {resp.text[:200]}")
    return resp.json()


async def get_parser_summary_by_doc(doc_id: str) -> Dict[str, Any]:
    """通过文档 ID 获取 LLM 摘要（含 draft_json / reviewed_json）。"""
    base = _kb_base()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{base}/parser/documents/{doc_id}/summary",
            headers=_kb_headers(),
        )
    if resp.status_code == 404:
        return {}
    if resp.status_code != 200:
        raise RuntimeError(f"摘要查询失败 [{resp.status_code}]: {resp.text[:200]}")
    return resp.json()


async def save_parser_draft(summary_id: str, reviewed_json: Dict[str, Any]) -> Dict[str, Any]:
    """保存审核草稿（PUT /parser/case-summaries/{summary_id}）。"""
    base = _kb_base()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.put(
            f"{base}/parser/case-summaries/{summary_id}",
            headers={**_kb_headers(), "Content-Type": "application/json"},
            json={"reviewed_json": reviewed_json},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"保存草稿失败 [{resp.status_code}]: {resp.text[:200]}")
    return resp.json()


async def approve_parser(summary_id: str) -> Dict[str, Any]:
    """审核通过，触发入库。"""
    base = _kb_base()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{base}/parser/case-summaries/{summary_id}/approve",
            headers=_kb_headers(),
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"审核通过失败 [{resp.status_code}]: {resp.text[:200]}")
    return resp.json()


async def parser_review_action(summary_id: str, action: str, comment: str = "") -> Dict[str, Any]:
    """驳回或标记需修改（action: needs_revision | rejected）。"""
    base = _kb_base()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{base}/parser/case-summaries/{summary_id}/review-action",
            headers={**_kb_headers(), "Content-Type": "application/json"},
            json={"action": action, "comment": comment},
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"审核操作失败 [{resp.status_code}]: {resp.text[:200]}")
    return resp.json()


async def delete_parser_document(doc_id: str) -> bool:
    """删除智能解析文档及相关文件。"""
    base = _kb_base()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(
            f"{base}/parser/documents/{doc_id}",
            headers=_kb_headers(),
        )
    return resp.status_code in (200, 204)


async def get_parser_page_image(doc_id: str, filename: str) -> Optional[bytes]:
    """代理获取页面图片字节流。"""
    base = _kb_base()
    url = f"{base}/static/data/page_images/{doc_id}/{filename}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_kb_headers())
    if resp.status_code != 200:
        return None
    return resp.content


# ── parser attachments 操作 ────────────────────────────────────────────────────

def upsert_parser_attachment(
    existing: list,
    filename: str,
    doc_id: str,
    partition_id: str,
    parse_status: str = "converting",
) -> list:
    """在 attachments 列表中插入或更新 parser 路由附件条目。
    按 name 匹配时排除 kind == 'file' 的纯文件列条目，避免同名文件跨列互相覆盖。"""
    attachments = [a for a in (existing or []) if isinstance(a, dict)]
    for att in attachments:
        if att.get("name") == filename and att.get("kind") != "file":
            att["doc_id"]       = doc_id
            att["partition_id"] = partition_id
            att["parse_status"] = parse_status
            att.pop("task_id", None)
            att.pop("kb_file_id", None)
            return attachments
    attachments.append({
        "name":         filename,
        "doc_id":       doc_id,
        "summary_id":   None,
        "partition_id": partition_id,
        "parse_status": parse_status,
        "chunks_added": 0,
    })
    return attachments


def update_parser_attachment_by_doc_id(
    existing: list,
    doc_id: str,
    parse_status: str,
    summary_id: Optional[str] = None,
    chunks_added: int = 0,
) -> list:
    """按 doc_id 更新 parser 附件状态。"""
    attachments = [a for a in (existing or []) if isinstance(a, dict)]
    for att in attachments:
        if att.get("doc_id") == doc_id:
            att["parse_status"] = parse_status
            if summary_id is not None:
                att["summary_id"] = summary_id
            if chunks_added:
                att["chunks_added"] = chunks_added
            break
    return attachments


def parser_status_label(parse_status: str) -> str:
    """将 KB 文档 parse_status 映射到前端展示标签。"""
    return {
        "pending":        "待转换",
        "converting":     "转图中",
        "converted":      "待解析",
        "summarizing":    "解析中",
        "pending_review": "待审核",
        "summary_failed": "解析失败",
        "approved":       "入库中",
        "indexed":        "已入库",
        "needs_revision": "需修改",
        "rejected":       "已驳回",
        "uploading":      "上传中",
        "error":          "上传失败",
    }.get(parse_status, parse_status)


# ── 工具 ──────────────────────────────────────────────────────────────────────

def _guess_content_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf":  "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc":  "application/msword",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "txt":  "text/plain",
        "md":   "text/markdown",
    }.get(ext, "application/octet-stream")
