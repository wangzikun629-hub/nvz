"""研发看板 Pydantic 请求/响应模型。"""
from datetime import date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AttachmentItem(BaseModel):
    name: str
    kb_file_id: Optional[str] = None
    kb_status: str = "pending"  # pending / loading / done


class RdRecordCreate(BaseModel):
    product_line: str = Field(..., description="产品线")
    project_name: str = Field(..., description="开发项目名称")
    project_bg: Optional[str] = None
    progress_date: Optional[date] = None
    team_group: Optional[str] = None
    reagent_owner: Optional[str] = None
    owner: Optional[str] = None
    problem: Optional[str] = None
    solution: Optional[str] = None
    conclusion: Optional[str] = None
    exp_plan: Optional[str] = None
    attachments: Optional[List[AttachmentItem]] = []
    source_sheet: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None


class RdRecordUpdate(BaseModel):
    product_line: Optional[str] = None
    project_name: Optional[str] = None
    project_bg: Optional[str] = None
    progress_date: Optional[date] = None
    team_group: Optional[str] = None
    reagent_owner: Optional[str] = None
    owner: Optional[str] = None
    problem: Optional[str] = None
    solution: Optional[str] = None
    conclusion: Optional[str] = None
    exp_plan: Optional[str] = None
    attachments: Optional[List[AttachmentItem]] = None
    source_sheet: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None


class RdRecordResponse(BaseModel):
    id: int
    product_line: str
    project_name: str
    project_bg: Optional[str] = None
    progress_date: Optional[str] = None
    team_group: Optional[str] = None
    reagent_owner: Optional[str] = None
    owner: Optional[str] = None
    problem: Optional[str] = None
    solution: Optional[str] = None
    conclusion: Optional[str] = None
    exp_plan: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = []
    source_sheet: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = {}
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class RdRecordListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    records: List[RdRecordResponse]


class KanbanAiQueryRequest(BaseModel):
    question: str = Field(..., description="用户自然语言问题")
    session_id: Optional[str] = None


class KanbanAiQueryResponse(BaseModel):
    answer: str
    route: str  # "sql" | "rag"
    sql: Optional[str] = None
