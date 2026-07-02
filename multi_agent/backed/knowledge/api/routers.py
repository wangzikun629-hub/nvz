import hmac
import logging
import os
import shutil
import uuid
from time import perf_counter
from typing import Annotated

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, UploadFile

from multi_agent.backed.app.infrastructure.auth.token_utils import verify_auth_token
from multi_agent.backed.knowledge.config.settings import settings
from multi_agent.backed.knowledge.repositories.catalog_repository import (
    CatalogRepository,
    VALID_PARTITION_IDS,
    _PARTITION_MAP,
)
from multi_agent.backed.knowledge.schemas.schema import (
    ApproveRequest,
    CaseSummaryResponse,
    CaseSummaryUpdateRequest,
    CategoryCreateRequest,
    CategoryResponse,
    CategoryUpdateRequest,
    FileMoveRequest,
    FileResponse,
    ParserDocumentResponse,
    ParserDocumentUploadResponse,
    PartitionResponse,
    QueryRequest,
    QueryResponse,
    RetrievalItem,
    RetrievalResponse,
    ReviewActionRequest,
    UploadResponse,
)
from multi_agent.backed.knowledge.services.ingestion.ingestion_processor import (
    IngestionProcessor,
)
from multi_agent.backed.knowledge.services.query_service import QueryService
from multi_agent.backed.knowledge.services.retrieval_service import RetrievalService


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# ── 懒加载：避免 Milvus / 嵌入服务不可用时整个 knowledge 服务崩溃 ──
_ingestion_processor: IngestionProcessor | None = None
_retrieval_service: RetrievalService | None = None
_query_service: QueryService | None = None

catalog_repository = CatalogRepository()
upload_tasks: dict[str, dict] = {}


def _get_ingestion_processor() -> IngestionProcessor:
    global _ingestion_processor
    if _ingestion_processor is None:
        try:
            _ingestion_processor = IngestionProcessor()
            logger.info("IngestionProcessor initialized successfully")
        except Exception as exc:
            logger.error("IngestionProcessor init failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=f"上传服务暂不可用（向量库连接失败：{exc}）。请检查 Milvus 配置后重试。",
            ) from exc
    return _ingestion_processor


def _get_retrieval_service() -> RetrievalService:
    global _retrieval_service
    if _retrieval_service is None:
        try:
            _retrieval_service = RetrievalService()
        except Exception as exc:
            logger.error("RetrievalService init failed: %s", exc)
            raise HTTPException(status_code=503, detail=f"检索服务不可用：{exc}") from exc
    return _retrieval_service


def _get_query_service() -> QueryService:
    global _query_service
    if _query_service is None:
        try:
            _query_service = QueryService()
        except Exception as exc:
            logger.error("QueryService init failed: %s", exc)
            raise HTTPException(status_code=503, detail=f"问答服务不可用：{exc}") from exc
    return _query_service


def get_current_user_id_from_token(
    authorization: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header()] = None,
    x_service_key: Annotated[str | None, Header()] = None,
) -> str:
    # Internal service calls use a dedicated secret so APP_API_KEY can remain
    # empty in development without disabling app-to-KB authentication.
    svc_key = (settings.KB_SERVICE_SECRET or "").strip()
    if svc_key and x_service_key:
        incoming = x_service_key.strip()
        if incoming and hmac.compare_digest(incoming, svc_key):
            return "kanban_service"

    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    payload = verify_auth_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired auth token")

    user_id = str(payload.get("user_id", "")).strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Auth token missing user identity")

    if x_user_id and str(x_user_id).strip() and str(x_user_id).strip() != user_id:
        raise HTTPException(status_code=403, detail="User header does not match auth token")
    return user_id


def _require_category(user_id: str, category_id: int):
    category = catalog_repository.get_category(user_id, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


def _build_retrieval_items(retrieval_context) -> list[RetrievalItem]:
    items: list[RetrievalItem] = []
    for index, document in enumerate(retrieval_context, start=1):
        title = (
            document.metadata.get("source_name")
            or document.metadata.get("title")
            or document.metadata.get("source")
            or f"资料{index}"
        )
        source = document.metadata.get("source") or title
        category_name = document.metadata.get("category_name")
        if category_name:
            source = f"{source} | {category_name}"
        content = " ".join((document.page_content or "").split())
        chunk_id = getattr(document, "id", "") or document.metadata.get("chunk_id", "")
        items.append(
            RetrievalItem(
                index=index,
                title=title,
                source=source,
                content=content[:1200],
                chunk_id=chunk_id,
            )
        )
    return items


def _run_upload_task(
    task_id: str,
    tmp_file_path: str,
    original_file_name: str,
    kb_scope: str,
    owner_user_id: str,
    category: dict,
    file_record: dict,
) -> None:
    try:
        upload_tasks[task_id].update(
            {"status": "processing", "message": "File uploaded. Processing in background."}
        )
        processor = _get_ingestion_processor()
        document_chunks = processor._build_document_chunks(
            tmp_file_path,
            kb_scope=kb_scope,
            source_name=original_file_name,
            extra_metadata={
                "owner_user_id": owner_user_id,
                "category_id": str(category.get("id", 0)),
                "category_name": category.get("name", ""),
                "partition_id": file_record.get("partition_id", "general"),
                "file_id": str(file_record["id"]),
                "file_name": original_file_name,
            },
        )
        chunk_previews = []
        for index, document in enumerate(document_chunks, start=1):
            content = document.page_content or ""
            chunk_id = processor.vector_store._build_chunk_id(document, index - 1)
            chunk_previews.append(
                {
                    "chunk_index": index,
                    "chunk_id": chunk_id,
                    "length": len(content),
                    "preview": content[:200],
                    "content": content,
                    "metadata": dict(document.metadata),
                    "deleted": False,
                }
            )
        upload_tasks[task_id]["chunk_previews"] = chunk_previews
        chunks_added = processor.add_document_chunks(document_chunks)
        upload_tasks[task_id].update(
            {"status": "success", "message": "File uploaded to knowledge base successfully.", "chunks_added": chunks_added}
        )
        catalog_repository.update_file_record(
            int(file_record["id"]),
            status="success",
            message="File uploaded to knowledge base successfully.",
            chunks_added=chunks_added,
            chunk_previews=chunk_previews,
        )
        logger.info("background upload succeeded: %s", original_file_name)
    except (ValueError, RuntimeError) as exc:
        logger.warning("background upload failed: %s", str(exc))
        upload_tasks[task_id].update(
            {"status": "error", "message": f"File upload failed: {str(exc)}", "chunk_previews": upload_tasks[task_id].get("chunk_previews", [])}
        )
        catalog_repository.update_file_record(
            int(file_record["id"]), status="error", message=f"File upload failed: {str(exc)}",
            chunks_added=0, chunk_previews=upload_tasks[task_id].get("chunk_previews", []),
        )
    except Exception as exc:
        logger.exception("background upload crashed")
        upload_tasks[task_id].update(
            {"status": "error", "message": f"File upload failed: {str(exc)}", "chunk_previews": upload_tasks[task_id].get("chunk_previews", [])}
        )
        catalog_repository.update_file_record(
            int(file_record["id"]), status="error", message=f"File upload failed: {str(exc)}",
            chunks_added=0, chunk_previews=upload_tasks[task_id].get("chunk_previews", []),
        )
    finally:
        if os.path.exists(tmp_file_path):
            os.remove(tmp_file_path)
            logger.info("temporary file deleted: %s", tmp_file_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 固定分区端点
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/partitions", response_model=list[PartitionResponse], summary="list fixed partitions")
async def list_partitions(user_id: str = Depends(get_current_user_id_from_token)):
    partitions = catalog_repository.list_partitions(user_id)
    return [PartitionResponse(**p) for p in partitions]


# ═══════════════════════════════════════════════════════════════════════════════
# 旧版分类端点（保留兼容）
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/categories", response_model=CategoryResponse, summary="create category")
async def create_category(request: CategoryCreateRequest, user_id: str = Depends(get_current_user_id_from_token)):
    try:
        category = catalog_repository.create_category(user_id, request.name, request.description)
        category["file_count"] = 0
        return CategoryResponse(**category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/categories", response_model=list[CategoryResponse], summary="list categories")
async def list_categories(user_id: str = Depends(get_current_user_id_from_token)):
    return [CategoryResponse(**item) for item in catalog_repository.list_categories(user_id)]


@router.put("/categories/{category_id}", response_model=CategoryResponse, summary="update category")
async def update_category(category_id: int, request: CategoryUpdateRequest, user_id: str = Depends(get_current_user_id_from_token)):
    try:
        category = catalog_repository.update_category(user_id, category_id, request.name, request.description)
        file_count = next(
            (item["file_count"] for item in catalog_repository.list_categories(user_id) if int(item["id"]) == category_id),
            0,
        )
        category["file_count"] = file_count
        return CategoryResponse(**category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/categories/{category_id}", summary="delete category")
async def delete_category(category_id: int, user_id: str = Depends(get_current_user_id_from_token)):
    try:
        catalog_repository.delete_category(user_id, category_id)
        return {"success": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# 文件端点
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/files", response_model=list[FileResponse], summary="list files")
async def list_files(
    category_id: int | None = Query(default=None),
    partition_id: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id_from_token),
):
    files = catalog_repository.list_files(user_id, category_id=category_id, partition_id=partition_id)
    return [FileResponse(**item) for item in files]


@router.get("/files/{file_id}/chunks", summary="list file chunks")
async def list_file_chunks(file_id: int, user_id: str = Depends(get_current_user_id_from_token)):
    try:
        chunks = catalog_repository.list_chunks_for_file(user_id, file_id)
        return {"file_id": file_id, "chunks": chunks}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/files/{file_id}/move-category", response_model=FileResponse, summary="move file category")
async def move_file_category(file_id: int, request: FileMoveRequest, user_id: str = Depends(get_current_user_id_from_token)):
    category = _require_category(user_id, request.target_category_id)
    try:
        file_record = catalog_repository.move_file_to_category(user_id, file_id, int(category["id"]), category["name"])
        return FileResponse(**file_record)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/files/{file_id}", summary="delete file")
async def delete_file(file_id: int, user_id: str = Depends(get_current_user_id_from_token)):
    try:
        deleted = catalog_repository.delete_file(user_id, file_id)
        deleted_count = _get_ingestion_processor().vector_store.delete_documents_by_ids(deleted["chunk_ids"])
        return {"success": True, "deleted_chunks": deleted_count, "file_id": file_id}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# 上传端点（支持 partition_id 和旧版 category_id 两种方式）
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/upload", response_model=UploadResponse, summary="upload file")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    partition_id: str | None = Form(default=None),
    category_id: int | None = Form(default=None),
    kb_scope: str = Form(default=settings.DEFAULT_KB_SCOPE),
    user_id: str = Depends(get_current_user_id_from_token),
):
    temp_file_path = None

    # 优先使用 partition_id；兼容旧版 category_id
    if partition_id:
        if partition_id not in VALID_PARTITION_IDS:
            raise HTTPException(status_code=400, detail=f"无效的分区 ID: {partition_id}")
        partition_info = _PARTITION_MAP[partition_id]
        category = {"id": 0, "name": partition_info["name"]}
        kb_scope = partition_id  # partition_id 直接作为 kb_scope
    elif category_id is not None:
        category = _require_category(user_id, category_id)
        partition_id = "general"
    else:
        raise HTTPException(status_code=400, detail="partition_id 或 category_id 必须提供一个")

    try:
        temp_md_dir = settings.TMP_MD_FOLDER_PATH
        file_suffix = os.path.splitext(file.filename or "")[1]
        safe_filename = os.path.basename(file.filename or "upload")
        tmp_file_path = os.path.join(temp_md_dir, f"{uuid.uuid4()}_{safe_filename}")
        os.makedirs(temp_md_dir, exist_ok=True)

        async with aiofiles.tempfile.NamedTemporaryFile(delete=False, suffix=file_suffix) as temp_file:
            while content := await file.read(1024 * 1024):
                await temp_file.write(content)
            temp_file_path = temp_file.name

        shutil.move(temp_file_path, tmp_file_path)
        temp_file_path = None

        task_id = uuid.uuid4().hex
        file_record = catalog_repository.create_file_record(
            user_id,
            int(category["id"]),
            category["name"],
            file.filename or safe_filename,
            kb_scope,
            task_id,
            file_suffix.lower(),
            partition_id=partition_id,
        )
        upload_tasks[task_id] = {
            "status": "processing",
            "message": "File uploaded. Processing in background.",
            "file_name": file.filename or safe_filename,
            "file_id": int(file_record["id"]),
            "chunks_added": 0,
            "chunk_previews": [],
        }
        background_tasks.add_task(
            _run_upload_task,
            task_id,
            tmp_file_path,
            file.filename or safe_filename,
            kb_scope,
            user_id,
            category,
            file_record,
        )
        return UploadResponse(
            status="processing",
            message="File uploaded. Processing in background.",
            file_name=file.filename or safe_filename,
            chunks_added=0,
            task_id=task_id,
            file_id=int(file_record["id"]),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("failed to accept upload")
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(exc)}") from exc
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)


@router.get("/upload/{task_id}", response_model=UploadResponse, summary="get upload status")
async def get_upload_status(task_id: str):
    task = upload_tasks.get(task_id)
    if task:
        return UploadResponse(
            status=task["status"],
            message=task["message"],
            file_name=task["file_name"],
            chunks_added=task.get("chunks_added", 0),
            task_id=task_id,
            file_id=task.get("file_id"),
        )
    try:
        file_record = catalog_repository.get_file_by_task_id(task_id)
    except Exception as exc:
        logger.warning("DB fallback for upload status failed: %s", exc)
        file_record = None

    if not file_record:
        raise HTTPException(status_code=404, detail="Upload task not found")
    return UploadResponse(
        status=file_record["status"],
        message=file_record["message"] or "Recovered from database after service restart.",
        file_name=file_record["file_name"],
        chunks_added=file_record.get("chunk_count", 0),
        task_id=task_id,
        file_id=file_record["id"],
    )


@router.get("/upload/{task_id}/chunks", summary="get upload chunks")
async def get_upload_chunks(task_id: str):
    task = upload_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Upload task not found")
    return {
        "task_id": task_id,
        "file_id": task.get("file_id"),
        "file_name": task["file_name"],
        "status": task["status"],
        "chunks": task.get("chunk_previews", []),
    }


@router.delete("/upload/{task_id}/chunks/{chunk_index}", summary="delete uploaded chunk")
async def delete_upload_chunk(task_id: str, chunk_index: int):
    task = upload_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Upload task not found")
    if task["status"] != "success":
        raise HTTPException(status_code=409, detail="Chunks can only be deleted after upload succeeds")
    chunk = next(
        (item for item in task.get("chunk_previews", []) if item.get("chunk_index") == chunk_index),
        None,
    )
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    if chunk.get("deleted"):
        raise HTTPException(status_code=409, detail="Chunk already deleted")

    deleted_count = _get_ingestion_processor().vector_store.delete_documents_by_ids([chunk["chunk_id"]])
    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="Chunk not found in vector store")
    chunk["deleted"] = True
    task["chunks_added"] = max(0, task.get("chunks_added", 0) - deleted_count)
    catalog_repository.mark_chunk_deleted(chunk["chunk_id"])
    return {
        "task_id": task_id,
        "file_name": task["file_name"],
        "chunk_index": chunk_index,
        "deleted": True,
        "message": "Chunk deleted successfully.",
        "chunks_added": task["chunks_added"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 问答 / 检索端点
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/query", response_model=QueryResponse, summary="query knowledge base")
async def query(request: QueryRequest):
    try:
        started_at = perf_counter()
        if not request.question:
            raise HTTPException(status_code=400, detail="Question is required")
        retrieval_started_at = perf_counter()
        retrieval_context = _get_retrieval_service().retrieval(request.question, kb_scope=request.kb_scope)
        retrieval_cost = perf_counter() - retrieval_started_at
        answer_started_at = perf_counter()
        answer = _get_query_service().generate_answer(request.question, retrieval_context)
        answer_cost = perf_counter() - answer_started_at
        logger.info(
            "knowledge_query done question=%s retrieval_docs=%d total=%.3fs retrieval=%.3fs answer=%.3fs",
            request.question[:80], len(retrieval_context), perf_counter() - started_at, retrieval_cost, answer_cost,
        )
        return QueryResponse(question=request.question, answer=answer)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("query failed: %s", str(exc))
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/retrieve", response_model=RetrievalResponse, summary="retrieve knowledge snippets")
async def retrieve(request: QueryRequest):
    try:
        started_at = perf_counter()
        if not request.question:
            raise HTTPException(status_code=400, detail="Question is required")
        retrieval_context = _get_retrieval_service().retrieval(request.question, kb_scope=request.kb_scope)
        items = _build_retrieval_items(retrieval_context)
        logger.info("knowledge_retrieve done question=%s docs=%d total=%.3fs", request.question[:80], len(items), perf_counter() - started_at)
        return RetrievalResponse(question=request.question, documents=items)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("retrieve failed: %s", str(exc))
        raise HTTPException(status_code=500, detail="Internal server error") from exc


# ═══════════════════════════════════════════════════════════════════════════════
# 智能解析通道端点
# ═══════════════════════════════════════════════════════════════════════════════

# 智能解析通道支持的格式：具备可视化"页面/工作表"版式的文档类型
# （纯文本/结构化数据类文件如 md/txt/csv/json 等走普通解析通道，见 /upload）
SUPPORTED_PARSER_EXTS = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx"}


def _get_parser_services():
    """懒加载智能解析服务（避免 import 失败阻断整个服务）"""
    import traceback as _tb
    steps = []
    try:
        steps.append("import DocumentIngestService")
        from multi_agent.backed.knowledge.services.parser.document_ingest_service import DocumentIngestService

        steps.append("import CaseSummaryService")
        from multi_agent.backed.knowledge.services.parser.case_summary_service import CaseSummaryService

        steps.append("import ChunkGenerationService")
        from multi_agent.backed.knowledge.services.parser.chunk_generation_service import ChunkGenerationService

        steps.append("import SourceDocumentRepository")
        from multi_agent.backed.knowledge.repositories.source_document_repository import SourceDocumentRepository

        steps.append("import CaseSummaryRepository")
        from multi_agent.backed.knowledge.repositories.case_summary_repository import CaseSummaryRepository

        steps.append("import ParserChunkRepository")
        from multi_agent.backed.knowledge.repositories.parser_chunk_repository import ParserChunkRepository

        steps.append("instantiate DocumentIngestService")
        ingest = DocumentIngestService()
        steps.append("instantiate CaseSummaryService")
        summary = CaseSummaryService()
        steps.append("instantiate ChunkGenerationService")
        chunk = ChunkGenerationService()
        steps.append("instantiate SourceDocumentRepository")
        doc_repo = SourceDocumentRepository()
        steps.append("instantiate CaseSummaryRepository")
        sum_repo = CaseSummaryRepository()
        steps.append("instantiate ParserChunkRepository")
        chunk_repo = ParserChunkRepository()

        return (ingest, summary, chunk, doc_repo, sum_repo, chunk_repo)
    except Exception as exc:
        last = steps[-1] if steps else "unknown"
        detail = f"智能解析服务不可用（步骤：{last}）：{exc}\n{_tb.format_exc()}"
        logger.error(detail)
        raise HTTPException(status_code=503, detail=f"智能解析服务不可用（步骤：{last}）：{exc}") from exc


@router.post("/parser/documents", response_model=ParserDocumentUploadResponse, summary="智能解析：上传文件")
async def parser_upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    partition_id: str = Form(...),
    user_id: str = Depends(get_current_user_id_from_token),
):
    if partition_id not in VALID_PARTITION_IDS:
        raise HTTPException(status_code=400, detail=f"无效的分区 ID: {partition_id}")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in SUPPORTED_PARSER_EXTS:
        raise HTTPException(
            status_code=400,
            detail="智能解析通道只支持 PDF / PPT/PPTX / Word(.doc/.docx) / Excel(.xls/.xlsx)",
        )

    ingest_svc, _, _, doc_repo, _, _ = _get_parser_services()

    # 保存上传文件
    document_id = uuid.uuid4().hex
    upload_dir = os.path.join(settings.PARSER_UPLOADS_DIR, document_id)
    os.makedirs(upload_dir, exist_ok=True)
    save_path = os.path.join(upload_dir, file.filename or f"document{ext}")

    async with aiofiles.open(save_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)

    # 写入 DB 记录
    partition_info = _PARTITION_MAP[partition_id]
    doc = doc_repo.create(
        document_id=document_id,
        partition_id=partition_id,
        schema_type=partition_info["schema_type"],
        file_name=file.filename or f"document{ext}",
        file_path=save_path,
        file_type=ext.lstrip("."),
        uploaded_by=user_id,
    )

    # 后台：转页面图片
    background_tasks.add_task(ingest_svc.convert_to_images_bg, document_id, save_path, ext, doc_repo)

    return ParserDocumentUploadResponse(
        document_id=document_id,
        file_name=doc["file_name"],
        partition_id=partition_id,
        parse_status=doc["parse_status"],
        message="文件已接收，正在后台转换页面图片",
    )


@router.get("/parser/documents", response_model=list[ParserDocumentResponse], summary="智能解析：文档列表")
async def parser_list_documents(
    partition_id: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id_from_token),
):
    _, _, _, doc_repo, _, _ = _get_parser_services()
    docs = doc_repo.list(uploaded_by=user_id, partition_id=partition_id)
    return [ParserDocumentResponse(**d) for d in docs]


@router.get("/parser/documents/{document_id}", response_model=ParserDocumentResponse, summary="智能解析：文档详情")
async def parser_get_document(document_id: str, user_id: str = Depends(get_current_user_id_from_token)):
    _, _, _, doc_repo, _, _ = _get_parser_services()
    doc = doc_repo.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return ParserDocumentResponse(**doc)


@router.post("/parser/documents/{document_id}/parse", summary="智能解析：触发 LLM 摘要生成")
async def parser_trigger_parse(
    document_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id_from_token),
):
    _, summary_svc, _, doc_repo, summary_repo, _ = _get_parser_services()
    doc = doc_repo.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc["parse_status"] not in ("converted", "summary_failed"):
        raise HTTPException(status_code=409, detail=f"当前状态 {doc['parse_status']} 不可触发解析，需 converted 状态")

    background_tasks.add_task(summary_svc.generate_summary_bg, document_id, doc, doc_repo, summary_repo)
    doc_repo.update_status(document_id, "summarizing")
    return {"document_id": document_id, "message": "LLM 摘要生成已触发"}


@router.get("/parser/case-summaries/{summary_id}", response_model=CaseSummaryResponse, summary="智能解析：获取摘要")
async def parser_get_summary(summary_id: str, user_id: str = Depends(get_current_user_id_from_token)):
    _, _, _, _, summary_repo, _ = _get_parser_services()
    s = summary_repo.get(summary_id)
    if not s:
        raise HTTPException(status_code=404, detail="摘要不存在")
    return CaseSummaryResponse(**s)


@router.get("/parser/documents/{document_id}/summary", response_model=CaseSummaryResponse, summary="智能解析：通过文档 ID 获取摘要")
async def parser_get_summary_by_doc(document_id: str, user_id: str = Depends(get_current_user_id_from_token)):
    _, _, _, _, summary_repo, _ = _get_parser_services()
    s = summary_repo.get_by_document(document_id)
    if not s:
        raise HTTPException(status_code=404, detail="摘要不存在")
    return CaseSummaryResponse(**s)


@router.put("/parser/case-summaries/{summary_id}", response_model=CaseSummaryResponse, summary="智能解析：保存审核草稿")
async def parser_update_summary(
    summary_id: str,
    request: CaseSummaryUpdateRequest,
    user_id: str = Depends(get_current_user_id_from_token),
):
    _, _, _, _, summary_repo, _ = _get_parser_services()
    s = summary_repo.get(summary_id)
    if not s:
        raise HTTPException(status_code=404, detail="摘要不存在")
    updated = summary_repo.save_reviewed_json(summary_id, request.reviewed_json, reviewer_id=user_id)
    return CaseSummaryResponse(**updated)


@router.post("/parser/case-summaries/{summary_id}/approve", summary="智能解析：审核通过，触发入库")
async def parser_approve_summary(
    summary_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id_from_token),
):
    _, _, chunk_svc, doc_repo, summary_repo, chunk_repo = _get_parser_services()
    s = summary_repo.get(summary_id)
    if not s:
        raise HTTPException(status_code=404, detail="摘要不存在")

    # 校验必填字段
    reviewed = s.get("reviewed_json") or s.get("draft_json") or {}
    doc = doc_repo.get(s["document_id"])
    schema_type = doc["schema_type"] if doc else "case_report"
    _validate_approve(reviewed, schema_type)

    summary_repo.set_status(summary_id, "approved", reviewer_id=user_id)
    background_tasks.add_task(chunk_svc.generate_and_index_bg, summary_id, s, doc, doc_repo, summary_repo, chunk_repo)
    return {"summary_id": summary_id, "message": "审核通过，正在后台生成 chunks 并写入向量库"}


@router.post("/parser/case-summaries/{summary_id}/review-action", summary="智能解析：驳回或需修改")
async def parser_review_action(
    summary_id: str,
    request: ReviewActionRequest,
    user_id: str = Depends(get_current_user_id_from_token),
):
    _, _, _, _, summary_repo, _ = _get_parser_services()
    s = summary_repo.get(summary_id)
    if not s:
        raise HTTPException(status_code=404, detail="摘要不存在")
    if request.action not in ("needs_revision", "rejected"):
        raise HTTPException(status_code=400, detail="action 必须是 needs_revision 或 rejected")
    summary_repo.set_status(summary_id, request.action, reviewer_id=user_id, comment=request.comment)
    return {"summary_id": summary_id, "status": request.action, "message": "操作已记录"}


@router.delete("/parser/documents/{document_id}", summary="智能解析：删除文档及相关文件")
async def parser_delete_document(
    document_id: str,
    user_id: str = Depends(get_current_user_id_from_token),
):
    _, _, _, doc_repo, _, _ = _get_parser_services()
    doc = doc_repo.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 删除上传文件目录
    upload_dir = os.path.join(settings.PARSER_UPLOADS_DIR, document_id)
    if os.path.isdir(upload_dir):
        shutil.rmtree(upload_dir, ignore_errors=True)

    # 删除页面图片目录
    images_dir = os.path.join(settings.PARSER_PAGE_IMAGES_DIR, document_id)
    if os.path.isdir(images_dir):
        shutil.rmtree(images_dir, ignore_errors=True)

    # 删除 DB 记录（summaries / chunks 靠外键级联或留孤儿无妨）
    doc_repo.delete(document_id)
    return {"document_id": document_id, "message": "文档已删除"}


def _validate_approve(reviewed: dict, schema_type: str) -> None:
    """审核通过前校验必填字段。"""
    errors = []
    if schema_type == "case_report":
        if not (reviewed.get("customer_question") or {}).get("text"):
            errors.append("customer_question.text 不能为空")
        if not (reviewed.get("conclusion") or {}).get("text"):
            errors.append("conclusion.text 不能为空")
        if not reviewed.get("limitations"):
            errors.append("limitations 至少需要 1 条")
    elif schema_type == "reagent_manual":
        if not reviewed.get("product_name"):
            errors.append("product_name 不能为空")
    elif schema_type == "general_doc":
        if not (reviewed.get("summary") or {}).get("text"):
            errors.append("summary.text 不能为空")
    if errors:
        raise HTTPException(status_code=422, detail="审核校验失败：" + "；".join(errors))
