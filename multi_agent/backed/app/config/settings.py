"""
应用配置管理模块

使用 pydantic-settings 进行配置管理，支持：
1. 自动从环境变量读取配置
2. 类型验证和转换
3. 默认值设置
4. 配置文档化
"""
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from typing_extensions import Self


class Settings(BaseSettings):
    """
    应用配置类

    配置项会自动从以下来源读取（优先级从高到低）：
    1. 环境变量
    2. .env 文件
    3. 默认值
    """

    # ==================== AI 服务配置 ====================

    # 硅基流动 API
    APP_ENV: str = Field(default="development", description="Runtime environment")
    APP_API_KEY: Optional[str] = Field(default=None, description="API authentication key")
    KB_SERVICE_SECRET: Optional[str] = Field(
        default=None,
        description="Shared secret for internal app-to-knowledge-service calls",
    )
    AUTH_TOKEN_SECRET: str = Field(
        default="dev-knowledge-token-secret",
        description="Secret used to sign lightweight auth tokens",
    )
    AUTH_TOKEN_EXPIRE_SECONDS: int = Field(
        default=60 * 60 * 24 * 7,
        description="Knowledge platform auth token lifetime in seconds",
    )
    CORS_ALLOW_ORIGINS: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="Comma-separated allowed browser origins",
    )
    REQUIRE_AI_SERVICE: bool = Field(
        default=False,
        description="Require a configured AI service at startup",
    )

    SF_API_KEY: Optional[str] = Field(default=None, description="硅基流动 API Key")
    SF_BASE_URL: Optional[str] = Field(default=None, description="硅基流动 Base URL")

    # 阿里百炼 API
    AL_BAILIAN_API_KEY: Optional[str] = Field(default=None, description="阿里百炼 API Key")
    AL_BAILIAN_BASE_URL: Optional[str] = Field(default=None, description="阿里百炼 Base URL")

    # ==================== 模型配置 ====================

    MAIN_MODEL_NAME: Optional[str] = Field(
        default="Qwen/Qwen3-32B",
        description="主模型名称"
    )
    SUB_MODEL_NAME: Optional[str] = Field(
        default="",
        description="qwen3-max"
    )
    REPORT_SUMMARY_MODEL_NAME: Optional[str] = Field(
        default=None,
        description="AI报告总结专用模型名称"
    )
    REPORT_SUMMARY_API_KEY: Optional[str] = Field(
        default=None,
        description="AI报告总结专用 API Key"
    )
    REPORT_SUMMARY_BASE_URL: Optional[str] = Field(
        default=None,
        description="AI报告总结专用 Base URL"
    )

    # Phase 1（project_analysis_agent_upgrade_plan.md）：文件发现探索 agent 专用模型配置。
    # 与主对话模型分开管理/分开计费，仿照 REPORT_SUMMARY_* 先例。留空则该 agent
    # 直接退化为纯启发式（detection_signature 关键词匹配），不调用任何模型。
    #
    # 【待确认，次要项，见 project_analysis_phase1.5_auto_promotion_revision.md §14】
    # 现有 SF_API_KEY（硅基流动）/ AL_BAILIAN_API_KEY（阿里百炼）渠道目前跑的是 Qwen 系列
    # 模型，`deepseek-v4-flash`/`deepseek-v4-pro` 能否直接通过这两个渠道调用，还是需要新增
    # 一个供应商适配（类似这里单独的 EXPLORATION_BASE_URL），需要在正式启用这两个 agent 前
    # 跟基础设施确认，不要默认这两个模型已经开箱可用；确认前留空即可安全退化为纯启发式路径。
    EXPLORATION_MODEL_NAME: Optional[str] = Field(
        default=None,
        description="文件发现探索 agent 专用模型名称（如 deepseek-v4-flash）"
    )
    EXPLORATION_API_KEY: Optional[str] = Field(
        default=None,
        description="文件发现探索 agent 专用 API Key"
    )
    EXPLORATION_BASE_URL: Optional[str] = Field(
        default=None,
        description="文件发现探索 agent 专用 Base URL"
    )

    # 项目回答的规则守卫（business_harness_guard_service）当前在 runtime_service.py 里
    # 被硬编码禁用（直接透传模型输出）。这里改为可配置开关，默认保持关闭以维持现有生产行为；
    # 开启后会对回答做确定性短语级拦截/替换（不合格/pass-fail 等越界措辞），仅供本地/预发验证。
    # 注意：这不等于恢复历史上更完整的语义级 semantic_guard + 模型重写链路，那是更大的改动。
    HARNESS_GUARD_ENFORCEMENT_ENABLED: bool = Field(
        default=False,
        description="是否对项目分析回答启用确定性规则守卫（harness_guard）的真实拦截/改写，默认关闭"
    )

    # Phase 1.1（project_analysis_agent_upgrade_plan.md）：代码语义解析 agent 专用模型配置。
    # 留空则该 agent 只跑静态规则提取（正则匹配已知公式变体的变量名模式），不调用任何模型。
    CODE_SEMANTICS_MODEL_NAME: Optional[str] = Field(
        default=None,
        description="代码语义解析 agent 专用模型名称（如 deepseek-v4-pro）"
    )
    CODE_SEMANTICS_API_KEY: Optional[str] = Field(
        default=None,
        description="代码语义解析 agent 专用 API Key"
    )
    CODE_SEMANTICS_BASE_URL: Optional[str] = Field(
        default=None,
        description="代码语义解析 agent 专用 Base URL"
    )

    # Phase 1.5（project_analysis_agent_upgrade_plan.md）：候选指标自动转正阈值。
    # 方案原文明确写道"这个数字目前只是经验判断，没有实证支撑……应该做成可配置项"，
    # 不要写死在代码里；上线后应结合人工抽查结果动态校准。
    CANDIDATE_METRIC_AUTO_PROMOTE_MIN_PROJECTS: int = Field(
        default=5,
        description=(
            "【2026-07-02 评审修订后已降级为残留场景弱信号，不再是主转正路径】"
            "候选指标自动转正所需的最少不同项目出现次数；仅在情形 E（脚本里定位不到公式，"
            "只有算术自洽）下用于给人工审核队列一个「多项目复现」的排序提示，不再触发自动转正。"
            "见 project_analysis_phase1.5_auto_promotion_revision.md 第一部分 §6。"
        )
    )

    # project_analysis_phase1.5_auto_promotion_revision.md 第一部分：脚本公式转正。
    # promotion_key = (script_hash, metric_id, formula_variant)；情形 A（静态正则提取 + 重算
    # 通过 + 无命名冲突）自动祝福；情形 B/C/D 需要人工祝福一次，此后自动。
    FORMULA_PROMOTION_ENABLED: bool = Field(
        default=True,
        description="是否启用脚本公式驱动的候选指标/公式变体自动转正机制（第一部分方案）"
    )
    FORMULA_PROMOTION_AUTO_BLESS_STATIC_ONLY: bool = Field(
        default=True,
        description="情形 A 自动祝福是否严格要求公式来自静态正则提取（不接受模型提取），默认是"
    )

    # ==================== 数据库配置 ====================

    MYSQL_HOST: Optional[str] = Field(default="localhost", description="MySQL主机地址")
    MYSQL_PORT: int = Field(default=3306, description="MySQL端口")
    MYSQL_USER: Optional[str] = Field(default="root", description="MySQL用户名")
    MYSQL_PASSWORD: Optional[str] = Field(default="", description="MySQL密码")
    MYSQL_DATABASE: Optional[str] = Field(default="its_db", description="MySQL数据库名")
    MYSQL_CHARSET: str = Field(default="utf8mb4", description="MySQL字符集")
    MYSQL_CONNECT_TIMEOUT: int = Field(default=10, description="MySQL连接超时（秒）")
    MYSQL_MAX_CONNECTIONS: int = Field(default=5, description="MySQL最大连接数")

    # project_analysis_phase1.5_auto_promotion_revision.md §1/§9：生产多 worker 场景下，
    # candidate_metrics / blessed_formula_map 必须是跨进程一致的权威真值源，不能只在单进程
    # 内存/JSON 文件里维护。默认 mysql；仅当显式设为 json 时才退化为单进程 JSON 文件
    # （只在明确单 worker 部署时安全）。运行时若 MySQL 不可达，repository 会记录一次告警
    # 并自动降级到只读兜底，避免影响主分析流程，但这属于故障态而非推荐配置。
    CANDIDATE_METRIC_STORAGE_BACKEND: str = Field(
        default="mysql",
        description="候选指标 / 公式祝福表的存储后端：mysql（推荐，多 worker 一致）或 json（仅单 worker）"
    )

    # project_analysis_phase1.5_auto_promotion_revision.md §13：25s 同步路径延迟预算拆分。
    # 每个子阶段独立超时，超时即降级返回已有证据，不再只靠一个全局 25s 兜底。
    FIELD_DISCOVERY_STAGE_TIMEOUT_SECONDS: float = Field(
        default=6.0,
        description="字段发现/转置表解析单阶段硬子预算（秒），超时降级返回已取得的证据"
    )
    FILE_DISCOVERY_STAGE_TIMEOUT_SECONDS: float = Field(
        default=6.0,
        description="文件发现启发式探测单阶段硬子预算（秒）"
    )

    # 离线 harness 的确定性门禁（project_analysis_phase1.5_auto_promotion_revision.md §11）：
    # 开启后，file discovery / field discovery / code semantics 的模型增强分支在离线模式下
    # 一律跳过（等价于模型未配置），只保留确定性的启发式/静态正则路径，保证同一 commit
    # 反复跑分数完全一致。生产环境不应开启（会关闭模型增强能力）。
    HARNESS_DETERMINISTIC_MODE: bool = Field(
        default=True,
        description="离线 harness 是否强制关闭所有模型增强分支以保证结果确定性"
    )

    # ==================== 并发控制 ====================

    MCP_POOL_SIZE: int = Field(
        default=15,
        description="Technical Agent MCP 连接池大小；生产环境可适当调大",
    )
    MAX_CONCURRENT_REQUESTS: int = Field(
        default=30,
        description="同时处理的最大请求数（超出则返回 '服务器繁忙'）",
    )

    # ==================== 外部服务配置 ====================

    # 知识库服务
    KNOWLEDGE_BASE_URL: Optional[str] = Field(
        default=None,
        description="知识库服务URL"
    )

    # 通义千问搜索服务
    DASHSCOPE_BASE_URL: Optional[str] = Field(
        default=None,
        description="通义千问 DashScope Base URL"
    )
    DASHSCOPE_API_KEY: Optional[str] = Field(
        default=None,
        description="通义千问 DashScope API Key"
    )

    # 百度地图服务
    BAIDUMAP_AK: Optional[str] = Field(
        default=None,
        description="百度地图 AK (Access Key)"
    )

    # R Plumber 图表微服务
    R_PLUMBER_URL: str = Field(
        default="http://127.0.0.1:8889",
        description="R Plumber 图表微服务地址（端口 8889），替代 LLM 生成 Plotly spec",
    )
    R_PLUMBER_TIMEOUT: float = Field(
        default=20.0,
        description="R Plumber 单次请求超时（秒）",
    )

    # ==================== Pydantic Settings 配置 ====================

    model_config = SettingsConfigDict(
        # 计算.env文件的绝对路径：config目录的父目录(app目录)下的.env
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",          # .env文件编码
        case_sensitive=True,                 # 环境变量名大小写敏感
        extra="ignore",                      # 忽略额外的环境变量
        validate_default=True,               # 验证默认值
    )

    # ====================  ====================
    @model_validator(mode='after')
    def check_ai_service_configuration(self) -> Self:
        """
        验证器：在配置加载完成后自动执行。
        如果需要强制至少配置一个 AI 服务，可以在这里抛出 ValueError
        """
        # 注意：这里 self 已经是实例化后的模型对象
        has_service = any([
            self.SF_API_KEY and self.SF_BASE_URL,
            self.AL_BAILIAN_API_KEY and self.AL_BAILIAN_BASE_URL
        ])

        if self.REQUIRE_AI_SERVICE and not has_service:
            raise ValueError("必须配置至少一个 AI 服务 (硅基流动 或 阿里百炼)")

        if self.APP_ENV.strip().lower() == "production":
            if not self.APP_API_KEY:
                raise ValueError("APP_API_KEY is required in production")
            if self.AUTH_TOKEN_SECRET == "dev-knowledge-token-secret":
                raise ValueError("AUTH_TOKEN_SECRET must be changed in production")
            origins = {
                item.strip()
                for item in self.CORS_ALLOW_ORIGINS.split(",")
                if item.strip()
            }
            if not origins or "*" in origins:
                raise ValueError("Production CORS_ALLOW_ORIGINS must list explicit origins")

        return self



# 创建全局配置实例
settings = Settings()

