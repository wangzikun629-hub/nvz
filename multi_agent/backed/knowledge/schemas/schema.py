from pydantic import BaseModel


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
