"""
研发看板 API 路由

挂载在 /api/kanban/rd/
"""
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile

from multi_agent.backed.app.api.routers import _require_api_key
from multi_agent.backed.app.repositories import kanban_rd_repository, kanban_custom_column_repository
from multi_agent.backed.app.schemas.kanban_rd_schema import (
    RdRecordCreate,
    RdRecordUpdate,
    RdRecordResponse,
    RdRecordListResponse,
    KanbanAiQueryRequest,
    KanbanAiQueryResponse,
)
from multi_agent.backed.app.services import kanban_ai_service
from multi_agent.backed.app.services import kanban_file_service
from multi_agent.backed.app.services import kanban_kb_service

router = APIRouter(prefix="/api/kanban/rd", tags=["研发看板"], dependencies=[Depends(_require_api_key)])


async def _cleanup_stale_attachment(rec: Dict[str, Any], filename: str) -> None:
    """重新上传同名文件、且新文件可能改走另一条通道时，先清理旧通道在 KB 服务端遗留的资源，
    避免智能解析文档（页面图片/摘要）或普通解析文件成为孤儿数据。"""
    existing = next(
        (a for a in (rec.get("attachments") or []) if a.get("name") == filename and a.get("kind") != "file"),
        None,
    )
    if not existing:
        return
    if existing.get("doc_id"):
        await kanban_kb_service.delete_parser_document(existing["doc_id"])
    elif existing.get("kb_file_id") is not None:
        await kanban_kb_service.delete_kb_file(existing["kb_file_id"])


@router.get("/records", response_model=RdRecordListResponse)
def list_records(
    product_line: Optional[str] = Query(None),
    owner: Optional[str] = Query(None),
    team_group: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=10000),
):
    result = kanban_rd_repository.list_records(
        product_line=product_line,
        owner=owner,
        team_group=team_group,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return result


@router.post("/records", response_model=RdRecordResponse, status_code=201)
def create_record(body: RdRecordCreate):
    data = body.model_dump(exclude_none=False)
    if data.get("progress_date"):
        data["progress_date"] = str(data["progress_date"])
    if data.get("attachments"):
        data["attachments"] = [a.model_dump() if hasattr(a, "model_dump") else a for a in data["attachments"]]
    return kanban_rd_repository.create_record(data)


@router.get("/records/{record_id}", response_model=RdRecordResponse)
def get_record(record_id: int):
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    return rec


@router.put("/records/{record_id}", response_model=RdRecordResponse)
def update_record(record_id: int, body: RdRecordUpdate):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if "progress_date" in data and data["progress_date"]:
        data["progress_date"] = str(data["progress_date"])
    if "attachments" in data and data["attachments"]:
        data["attachments"] = [a.model_dump() if hasattr(a, "model_dump") else a for a in data["attachments"]]
    rec = kanban_rd_repository.update_record(record_id, data)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    return rec


@router.delete("/records/{record_id}")
def delete_record(record_id: int):
    ok = kanban_rd_repository.delete_record(record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


@router.get("/product_lines")
def get_product_lines():
    return {"product_lines": kanban_rd_repository.get_product_lines()}


@router.get("/owners")
def get_owners():
    return {"owners": kanban_rd_repository.get_owners()}


@router.get("/stats")
def get_stats():
    return kanban_rd_repository.get_stats()


# ── 自定义列 ──────────────────────────────────────────────────────────────────

@router.get("/custom_columns")
def list_custom_columns():
    return {"columns": kanban_custom_column_repository.list_columns("rd")}


@router.post("/custom_columns", status_code=201)
def create_custom_column(body: Dict[str, Any]):
    try:
        col = kanban_custom_column_repository.create_column("rd", body.get("label", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return col


@router.put("/custom_columns/{field_key}")
def rename_custom_column(field_key: str, body: Dict[str, Any]):
    try:
        col = kanban_custom_column_repository.rename_column("rd", field_key, body.get("label", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not col:
        raise HTTPException(status_code=404, detail="列不存在")
    return col


@router.delete("/custom_columns/{field_key}")
def delete_custom_column(field_key: str):
    ok = kanban_custom_column_repository.delete_column("rd", field_key)
    if not ok:
        raise HTTPException(status_code=404, detail="列不存在")
    return {"success": True}


# ── 文件列（纯附件存储，不入知识库）──────────────────────────────────────────────

@router.post("/records/{record_id}/file")
async def upload_plain_file(record_id: int, file: UploadFile = File(...)):
    """上传文件，仅本地存储 + 记元数据，不做任何解析/向量化。"""
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    content = await file.read()
    if len(content) > kanban_file_service.MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"文件超过大小限制（{kanban_file_service.MAX_FILE_SIZE_MB}MB）")
    filename = file.filename or "file"
    entry = kanban_file_service.save_file("rd", record_id, filename, content)
    attachments = kanban_file_service.upsert_file_attachment(rec.get("attachments") or [], entry)
    kanban_rd_repository.update_record(record_id, {"attachments": attachments})
    return entry


@router.get("/records/{record_id}/file/{file_id}")
async def download_plain_file(record_id: int, file_id: str):
    if not kanban_file_service.is_valid_file_id(file_id):
        raise HTTPException(status_code=400, detail="非法的文件 ID")
    result = kanban_file_service.load_file("rd", record_id, file_id)
    if not result:
        raise HTTPException(status_code=404, detail="文件不存在")
    content, filename, content_type = result
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.delete("/records/{record_id}/file/{file_id}")
def delete_plain_file(record_id: int, file_id: str):
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    kanban_file_service.delete_file("rd", record_id, file_id)
    attachments = kanban_file_service.remove_file_attachment(rec.get("attachments") or [], file_id)
    kanban_rd_repository.update_record(record_id, {"attachments": attachments})
    return {"success": True, "file_id": file_id}


@router.post("/records/{record_id}/kb-upload")
async def kb_upload(
    record_id: int,
    file: UploadFile = File(...),
    partition_id: str = Query(default="kanban_rd"),
):
    """普通解析路线：文本类文件（如 Markdown）直接切分入库，不经过 LLM 摘要/人工审核。"""
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    content  = await file.read()
    filename = file.filename or "upload"
    await _cleanup_stale_attachment(rec, filename)
    try:
        result = await kanban_kb_service.upload_file_to_kb(
            file_content=content, filename=filename, partition_id=partition_id,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"知识库上传失败：{e}")
    task_id = result.get("task_id")
    attachments = kanban_kb_service.upsert_attachment(
        existing=rec.get("attachments") or [],
        filename=filename, task_id=task_id, kb_file_id=result.get("file_id"),
        partition_id=partition_id, parse_status="uploading",
        chunks_added=result.get("chunks_added", 0),
    )
    kanban_rd_repository.update_record(record_id, {"attachments": attachments})
    return {"success": True, "filename": filename, "task_id": task_id, "parse_status": "uploading"}


@router.get("/records/{record_id}/kb-status/{task_id}")
async def kb_status(record_id: int, task_id: str):
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        poll = await kanban_kb_service.poll_kb_status(task_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"状态查询失败：{e}")
    parse_status = kanban_kb_service.kb_status_from_poll(poll.get("status", "processing"))
    # 与 parser_status 保持一致：只在终态时落库，避免每 2s 一次的中间轮询产生无意义写操作
    if parse_status in {"indexed", "error"}:
        attachments = kanban_kb_service.update_attachment_by_task_id(
            existing=rec.get("attachments") or [], task_id=task_id,
            parse_status=parse_status, chunks_added=poll.get("chunks_added", 0),
            kb_file_id=poll.get("file_id"),
        )
        kanban_rd_repository.update_record(record_id, {"attachments": attachments})
    return {"task_id": task_id, "parse_status": parse_status, "chunks_added": poll.get("chunks_added", 0)}


@router.post("/records/{record_id}/parser-upload")
async def parser_upload(
    record_id: int,
    file: UploadFile = File(...),
    partition_id: str = Query(default="kanban_rd"),
):
    """智能解析路线：上传 PDF/PPT/Word/Excel 到 KB parser 通道，写入 attachments。"""
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")

    content  = await file.read()
    filename = file.filename or "upload"

    await _cleanup_stale_attachment(rec, filename)

    try:
        doc = await kanban_kb_service.upload_file_to_parser(
            file_content=content,
            filename=filename,
            partition_id=partition_id,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"智能解析上传失败：{e}")

    doc_id = doc.get("document_id")
    attachments = kanban_kb_service.upsert_parser_attachment(
        existing=rec.get("attachments") or [],
        filename=filename,
        doc_id=doc_id,
        partition_id=partition_id,
        parse_status=doc.get("parse_status", "converting"),
    )
    kanban_rd_repository.update_record(record_id, {"attachments": attachments})
    return {"success": True, "filename": filename, "doc_id": doc_id,
            "parse_status": doc.get("parse_status", "converting")}


@router.post("/records/{record_id}/parser-trigger/{doc_id}")
async def parser_trigger(record_id: int, doc_id: str):
    """触发 LLM 摘要解析（文档已完成图片转换后调用）。"""
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        result = await kanban_kb_service.trigger_parser_parse(doc_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"触发解析失败：{e}")
    attachments = kanban_kb_service.update_parser_attachment_by_doc_id(
        existing=rec.get("attachments") or [],
        doc_id=doc_id,
        parse_status="summarizing",
    )
    kanban_rd_repository.update_record(record_id, {"attachments": attachments})
    return {"success": True, "doc_id": doc_id, **result}


@router.get("/records/{record_id}/parser-status/{doc_id}")
async def parser_status(record_id: int, doc_id: str):
    """轮询智能解析文档状态，终态时写库。"""
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        doc = await kanban_kb_service.get_parser_document(doc_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"状态查询失败：{e}")

    parse_status = doc.get("parse_status", "")
    terminal = {"pending_review", "summary_failed", "indexed", "needs_revision", "rejected"}
    if parse_status in terminal:
        attachments = kanban_kb_service.update_parser_attachment_by_doc_id(
            existing=rec.get("attachments") or [],
            doc_id=doc_id,
            parse_status=parse_status,
        )
        kanban_rd_repository.update_record(record_id, {"attachments": attachments})
    return {"doc_id": doc_id, "parse_status": parse_status,
            "page_count": doc.get("page_count", 0), "file_name": doc.get("file_name", "")}


@router.get("/records/{record_id}/parser-summary/{doc_id}")
async def parser_summary(record_id: int, doc_id: str):
    """获取文档的 LLM 摘要（供审核抽屉使用）。"""
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        summary = await kanban_kb_service.get_parser_summary_by_doc(doc_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"摘要获取失败：{e}")
    return summary


@router.put("/records/{record_id}/parser-summary/{summary_id}")
async def parser_save_draft(record_id: int, summary_id: str, body: Dict[str, Any]):
    """保存审核草稿。"""
    try:
        result = await kanban_kb_service.save_parser_draft(summary_id, body.get("reviewed_json", {}))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"保存草稿失败：{e}")
    return result


@router.post("/records/{record_id}/parser-approve/{summary_id}")
async def parser_approve(record_id: int, summary_id: str):
    """审核通过，触发 chunk 入库；更新附件状态为 approved。"""
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        result = await kanban_kb_service.approve_parser(summary_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"审核通过失败：{e}")
    # 按 summary_id 找对应附件并更新状态
    attachments = rec.get("attachments") or []
    for att in attachments:
        if att.get("summary_id") == summary_id:
            att["parse_status"] = "approved"
            break
    kanban_rd_repository.update_record(record_id, {"attachments": attachments})
    return {"success": True, "summary_id": summary_id, **result}


@router.post("/records/{record_id}/parser-review-action/{summary_id}")
async def parser_review_action(record_id: int, summary_id: str, body: Dict[str, Any]):
    """驳回或标记需修改（body: {action, comment}）。"""
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    action  = body.get("action", "needs_revision")
    comment = body.get("comment", "")
    try:
        result = await kanban_kb_service.parser_review_action(summary_id, action, comment)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"审核操作失败：{e}")
    attachments = rec.get("attachments") or []
    for att in attachments:
        if att.get("summary_id") == summary_id:
            att["parse_status"] = action
            break
    kanban_rd_repository.update_record(record_id, {"attachments": attachments})
    return {"success": True, "summary_id": summary_id, "action": action, **result}


@router.delete("/records/{record_id}/parser-attachment")
async def parser_delete_attachment(record_id: int, filename: str = Query(...)):
    """删除附件：同步删除 KB 文档 + 移除 attachments 条目。"""
    rec = kanban_rd_repository.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    target = next((a for a in (rec.get("attachments") or []) if a.get("name") == filename), None)
    if target:
        if target.get("doc_id"):
            await kanban_kb_service.delete_parser_document(target["doc_id"])
        elif target.get("kb_file_id") is not None:
            await kanban_kb_service.delete_kb_file(target["kb_file_id"])
    attachments = [a for a in (rec.get("attachments") or []) if a.get("name") != filename]
    kanban_rd_repository.update_record(record_id, {"attachments": attachments})
    return {"success": True, "filename": filename}


@router.get("/parser-image/{doc_id}/{img_filename}")
async def parser_page_image(doc_id: str, img_filename: str):
    """代理 KB 服务页面图片，前端无需直接访问 KB 端口。"""
    if not kanban_kb_service.is_valid_doc_id(doc_id) or not kanban_kb_service.is_valid_image_filename(img_filename):
        raise HTTPException(status_code=400, detail="非法的文档 ID 或图片文件名")
    data = await kanban_kb_service.get_parser_page_image(doc_id, img_filename)
    if data is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    return Response(content=data, media_type="image/png")


@router.post("/ai_query", response_model=KanbanAiQueryResponse)
async def ai_query(body: KanbanAiQueryRequest):
    result = await kanban_ai_service.query(body.question, scope="rd")
    return result
