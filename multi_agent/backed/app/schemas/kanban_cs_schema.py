"""客户服务看板 Pydantic 请求/响应模型。"""
from datetime import date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AttachmentItem(BaseModel):
    name: str
    kb_file_id: Optional[str] = None
    kb_status: str = "pending"


class CsRecordCreate(BaseModel):
    record_type: str = Field(..., description="售前 / 售后")
    is_closed: bool = False
    customer_name: Optional[str] = None
    project_name: Optional[str] = None
    product_no: Optional[str] = None
    case_type: Optional[str] = None
    problem_category: Optional[str] = None
    cause_category: Optional[str] = None
    start_date: Optional[date] = None
    customer_need: Optional[str] = None
    problem: Optional[str] = None
    analysis_note: Optional[str] = None
    solution: Optional[str] = None
    conclusion: Optional[str] = None
    product_line: Optional[str] = None
    owner: Optional[str] = None
    attachments: Optional[List[AttachmentItem]] = []
    attention_point: Optional[str] = None
    source_sheet: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None


class CsRecordUpdate(BaseModel):
    record_type: Optional[str] = None
    is_closed: Optional[bool] = None
    customer_name: Optional[str] = None
    project_name: Optional[str] = None
    product_no: Optional[str] = None
    case_type: Optional[str] = None
    problem_category: Optional[str] = None
    cause_category: Optional[str] = None
    start_date: Optional[date] = None
    customer_need: Optional[str] = None
    problem: Optional[str] = None
    analysis_note: Optional[str] = None
    solution: Optional[str] = None
    conclusion: Optional[str] = None
    product_line: Optional[str] = None
    owner: Optional[str] = None
    attachments: Optional[List[AttachmentItem]] = None
    attention_point: Optional[str] = None
    source_sheet: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None


class CloseRequest(BaseModel):
    is_closed: bool


class CsRecordResponse(BaseModel):
    id: int
    is_closed: int = 0
    record_type: str
    customer_name: Optional[str] = None
    project_name: Optional[str] = None
    product_no: Optional[str] = None
    case_type: Optional[str] = None
    problem_category: Optional[str] = None
    cause_category: Optional[str] = None
    start_date: Optional[str] = None
    customer_need: Optional[str] = None
    problem: Optional[str] = None
    analysis_note: Optional[str] = None
    solution: Optional[str] = None
    conclusion: Optional[str] = None
    product_line: Optional[str] = None
    owner: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = []
    attention_point: Optional[str] = None
    source_sheet: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = {}
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class CsRecordListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    records: List[CsRecordResponse]


class KanbanAiQueryRequest(BaseModel):
    question: str = Field(..., description="用户自然语言问题")
    session_id: Optional[str] = None


class KanbanAiQueryResponse(BaseModel):
    answer: str
    route: str  # "sql" | "rag"
    sql: Optional[str] = None
