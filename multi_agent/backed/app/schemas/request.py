from typing import Optional

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    user_id: str
    session_id: Optional[str] = Field(default=None, description="Session ID")


class ChatMessageRequest(BaseModel):
    query: str
    context: UserContext
    flag: bool = True
    mode: Optional[str] = Field(default="auto", description="Execution mode: auto / fast_rag / agent")
    project_id: Optional[str] = Field(default=None, description="Optional project ID or project name")
    project_root: Optional[str] = Field(default=None, description="Optional project root path")
    max_evidence_files: int = Field(
        default=40,
        ge=1,
        le=40,
        description="Maximum number of evidence files to read during project analysis",
    )


class ChatCompatRequest(BaseModel):
    question: Optional[str] = Field(default=None, description="Question field for the compatible chat API")
    query: Optional[str] = Field(default=None, description="Query field for the multi-agent API")
    user_id: str = Field(default="api_chat_user", description="Unique user ID")
    session_id: Optional[str] = Field(default=None, description="Optional session ID")
    flag: bool = Field(default=True, description="Whether internal retry is allowed")
    mode: str = Field(default="auto", description="Execution mode: auto / fast_rag / agent")
    project_id: Optional[str] = Field(default=None, description="Optional project ID or project name")
    project_root: Optional[str] = Field(default=None, description="Optional project root path")
    max_evidence_files: int = Field(
        default=40,
        ge=1,
        le=40,
        description="Maximum number of evidence files to read during project analysis",
    )


class UserSessionsRequest(BaseModel):
    user_id: str = Field(description="Unique user ID")


class ProjectContextRequest(BaseModel):
    user_id: str = Field(default="api_chat_user", description="Unique user ID")
    session_id: str = Field(default="default_project_session", description="Session ID")


class SessionHistoryRequest(BaseModel):
    user_id: str = Field(default="api_chat_user", description="Unique user ID")
    session_id: str = Field(default="default_session", description="Session ID")


class ProjectAnalyzeRequest(BaseModel):
    question: str = Field(description="User question")
    project_id: Optional[str] = Field(default=None, description="Project name or directory name")
    project_root: Optional[str] = Field(default=None, description="Project root path")
    user_id: str = Field(default="project_user", description="Unique user ID")
    session_id: str = Field(default="default_project_session", description="Project analysis session ID")
    max_evidence_files: int = Field(
        default=40,
        ge=1,
        le=40,
        description="Maximum number of evidence files to read for this request",
    )


class ProjectChartRequest(BaseModel):
    project_id: str = Field(description="Project name or directory name")
    metric: str = Field(description="Metric name, for example q30, frip, peak, correlation")
    metric2: Optional[str] = Field(
        default=None,
        description="第二个指标名称（填写后自动切换为双指标对比图）",
    )
    chart_type: Optional[str] = Field(default=None, description="Chart type: bar / line / heatmap / scatter")
    project_root: Optional[str] = Field(default=None, description="Optional project root path")
    user_id: str = Field(default="project_user", description="Unique user ID")
    session_id: str = Field(default="default_project_session", description="Project analysis session ID")
    samples: list[str] = Field(default_factory=list, description="Optional sample names to include")
    title: Optional[str] = Field(default=None, description="Optional chart title")
    user_request: str = Field(
        default="",
        description="用户个性化需求描述，如'加一条 0.1 阈值线，柱子用绿色'",
    )
    use_codegen: bool = Field(
        default=False,
        description="为 True 时由 LLM 生成 R 脚本执行出图（ggplot2 PNG），适合自定义图类型",
    )


class ProjectIdentifyRequest(BaseModel):
    question: str = Field(description="Question, project name, sample name, or batch description")
    project_id: Optional[str] = Field(default=None, description="Optional project name")
    user_id: str = Field(default="project_user", description="Unique user ID")
    session_id: str = Field(default="default_project_session", description="Project analysis session ID")


class ProjectMemoryRequest(BaseModel):
    project_id: str = Field(description="Project name or directory name")
