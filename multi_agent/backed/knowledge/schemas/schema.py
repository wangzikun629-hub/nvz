from typing import Any

from pydantic import BaseModel


# ── 通用上传 / 查询 Schema ──────────────────────────────────────────────────────
class UploadResponse(BaseModel):
    status: str
    message: str
    file_name: str
    chunks_added: int = 0
    task_id: str | None = None
    file_id: int | None = None


class QueryResponse(BaseModel):
    question: str
    answer: str


class QueryRequest(BaseModel):
    question: str
    kb_scope: str | None = None


class RetrievalItem(BaseModel):
    index: int
    title: str
    source: str
    content: str
    chunk_id: str = ""


class RetrievalResponse(BaseModel):
    question: str
    documents: list[RetrievalItem]


# ── 旧版分类 Schema（保留，兼容历史接口） ────────────────────────────────────────
class CategoryCreateRequest(BaseModel):
    name: str
    description: str = ""


class CategoryUpdateRequest(BaseModel):
    name: str
    description: str = ""


class CategoryResponse(BaseModel):
    id: int
    owner_user_id: str
    name: str
    description: str = ""
    file_count: int = 0
    created_at: str
    updated_at: str


class FileMoveRequest(BaseModel):
    target_category_id: int


class FileResponse(BaseModel):
    id: int
    owner_user_id: str
    category_id: int
    category_name: str
    file_name: str
    kb_scope: str
    original_extension: str = ""
    chunk_count: int = 0
    status: str
    message: str = ""
    upload_task_id: str = ""
    created_at: str
    updated_at: str
    partition_id: str = "general"


# ── 固定分区 Schema ─────────────────────────────────────────────────────────────
class PartitionResponse(BaseModel):
    id: str
    name: str
    schema_type: str
    sort_order: int
    file_count: int = 0


# ── 智能解析通道 Schema ─────────────────────────────────────────────────────────
class ParserDocumentUploadResponse(BaseModel):
    document_id: str
    file_name: str
    partition_id: str
    parse_status: str
    message: str = ""


class ParserDocumentResponse(BaseModel):
    id: str
    partition_id: str
    schema_type: str
    file_name: str
    file_type: str
    page_count: int | None = None
    page_image_paths: list[str] = []
    parse_status: str
    parse_error: str | None = None
    uploaded_by: str | None = None
    uploaded_at: str
    created_at: str
    updated_at: str


class ParserParseRequest(BaseModel):
    """触发 LLM 摘要生成"""
    pass


class CaseSummaryResponse(BaseModel):
    id: str
    document_id: str
    review_status: str
    draft_json: dict[str, Any]
    reviewed_json: dict[str, Any] | None = None
    reviewer_id: str | None = None
    reviewed_at: str | None = None
    review_comment: str | None = None
    created_at: str
    updated_at: str


class CaseSummaryUpdateRequest(BaseModel):
    """保存审核草稿"""
    reviewed_json: dict[str, Any]


class ReviewActionRequest(BaseModel):
    """驳回 / 需修改"""
    action: str  # "needs_revision" | "rejected"
    comment: str = ""


class ApproveRequest(BaseModel):
    """审核通过"""
    pass
