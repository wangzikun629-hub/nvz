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

    # ==================== 数据库配置 ====================

    MYSQL_HOST: Optional[str] = Field(default="localhost", description="MySQL主机地址")
    MYSQL_PORT: int = Field(default=3306, description="MySQL端口")
    MYSQL_USER: Optional[str] = Field(default="root", description="MySQL用户名")
    MYSQL_PASSWORD: Optional[str] = Field(default="", description="MySQL密码")
    MYSQL_DATABASE: Optional[str] = Field(default="its_db", description="MySQL数据库名")
    MYSQL_CHARSET: str = Field(default="utf8mb4", description="MySQL字符集")
    MYSQL_CONNECT_TIMEOUT: int = Field(default=10, description="MySQL连接超时（秒）")
    MYSQL_MAX_CONNECTIONS: int = Field(default=5, description="MySQL最大连接数")

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

