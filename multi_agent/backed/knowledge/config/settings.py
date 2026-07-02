from pydantic import model_validator
from pydantic_settings import BaseSettings,SettingsConfigDict
import os

class Settings(BaseSettings):
    API_KEY: str = os.environ.get("API_KEY", "")
    # 服务间调用共享密钥（与主服务 APP_API_KEY 使用相同的环境变量名，便于统一配置）
    APP_API_KEY: str = os.environ.get("APP_API_KEY", "")
    # 服务间调用专用密钥（主服务 → KB 服务），独立于前端认证
    KB_SERVICE_SECRET: str = os.environ.get("KB_SERVICE_SECRET", "")
    BASE_URL: str = os.environ.get("BASE_URL", "")
    LLM_BASE_URL: str = os.environ.get("LLM_BASE_URL", "")
    EMBEDDING_BASE_URL: str = os.environ.get("EMBEDDING_BASE_URL", "")
    MODEL: str = os.environ.get("MODEL", "")
    EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "")
    EMBEDDING_DIM: int = int(os.environ.get("EMBEDDING_DIM", "0"))
    DASHSCOPE_MULTIMODAL_EMBEDDING_URL: str = os.environ.get(
        "DASHSCOPE_MULTIMODAL_EMBEDDING_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding",
    )
    RERANK_MODEL: str = os.environ.get("RERANK_MODEL", "")

    
    # knowledge/config
    KNOWLEDGE_BASE_URL:str=os.environ.get("KNOWLEDGE_BASE_URL", "")

    _current_dir = os.path.dirname(os.path.abspath(__file__))
    # knowledge
    _project_root = os.path.dirname(_current_dir)
    
    MILVUS_URI: str = os.environ.get("MILVUS_URI", "http://localhost:19530")
    MILVUS_TOKEN: str = os.environ.get("MILVUS_TOKEN", "")
    MILVUS_DB_NAME: str = os.environ.get("MILVUS_DB_NAME", "default")
    MILVUS_COLLECTION: str = os.environ.get("MILVUS_COLLECTION", "nvz_knowledge_v2")
    MILVUS_PARTITION_KEY: str = os.environ.get("MILVUS_PARTITION_KEY", "kb_scope")
    MILVUS_NUM_PARTITIONS: int = int(os.environ.get("MILVUS_NUM_PARTITIONS", "16"))
    DEFAULT_KB_SCOPE: str = os.environ.get("DEFAULT_KB_SCOPE", "general")
    
    # Default directories
    CRAWL_OUTPUT_DIR: str = os.path.join(_project_root, "data", "crawl")
    # Using 'data/crawl' as the default location for markdown files
    MD_FOLDER_PATH: str = CRAWL_OUTPUT_DIR
    TMP_MD_FOLDER_PATH:str=os.path.join(_project_root, "data", "tmp")
    CATALOG_DATA_DIR: str = os.path.join(_project_root, "data", "catalog")
    CATALOG_DATA_FILE: str = os.path.join(CATALOG_DATA_DIR, "knowledge_catalog.json")
    # Text splitting configuration
    CHUNK_SIZE: int = 1500
    CHUNK_OVERLAP: int = 200
    ENABLE_AI_PREPROCESS_FOR_COMPLEX_DOCS: bool = os.environ.get("ENABLE_AI_PREPROCESS_FOR_COMPLEX_DOCS", "true").lower() == "true"
    AI_PREPROCESS_MAX_CHARS: int = int(os.environ.get("AI_PREPROCESS_MAX_CHARS", "20000"))
    ENABLE_MINERU_PDF: bool = os.environ.get("ENABLE_MINERU_PDF", "false").lower() == "true"
    MINERU_BASE_URL: str = os.environ.get("MINERU_BASE_URL", "https://mineru.net/api/v4")
    MINERU_API_TOKEN: str = os.environ.get("MINERU_API_TOKEN", "")
    MINERU_MODEL_VERSION: str = os.environ.get("MINERU_MODEL_VERSION", "vlm")
    MINERU_TIMEOUT_SECONDS: int = int(os.environ.get("MINERU_TIMEOUT_SECONDS", "600"))
    MINERU_POLL_INTERVAL_SECONDS: int = int(os.environ.get("MINERU_POLL_INTERVAL_SECONDS", "3"))
    ENABLE_ONLINE_RERANK: bool = os.environ.get("ENABLE_ONLINE_RERANK", "false").lower() == "true"
    ONLINE_RERANK_CANDIDATES: int = int(os.environ.get("ONLINE_RERANK_CANDIDATES", "5"))
    ONLINE_RERANK_SHORT_QUERY_TOKENS: int = int(os.environ.get("ONLINE_RERANK_SHORT_QUERY_TOKENS", "3"))
    ONLINE_RERANK_SHORT_QUERY_CHARS: int = int(os.environ.get("ONLINE_RERANK_SHORT_QUERY_CHARS", "12"))
    ONLINE_RERANK_SCORE_GAP: float = float(os.environ.get("ONLINE_RERANK_SCORE_GAP", "0.08"))
    ONLINE_RERANK_LOW_CONFIDENCE: float = float(os.environ.get("ONLINE_RERANK_LOW_CONFIDENCE", "0.45"))
    ONLINE_RERANK_HIGH_VALUE_KEYWORDS: str = os.environ.get(
        "ONLINE_RERANK_HIGH_VALUE_KEYWORDS",
        "报错,错误,失败,异常,登录,支付,权限,安全,退款,工单,告警"
    )

    # Retrieval configuration
    TOP_ROUGH: int = 50
    TOP_FINAL: int = 5

    # 智能解析通道配置
    PARSER_MODEL: str = os.environ.get("PARSER_MODEL", "qwen-vl-max-latest")
    # 留空则回退到 API_KEY / BASE_URL
    PARSER_API_KEY: str = os.environ.get("PARSER_API_KEY", "")
    PARSER_BASE_URL: str = os.environ.get("PARSER_BASE_URL", "")
    PARSER_UPLOADS_DIR: str = os.path.join(_project_root, "data", "uploads")
    PARSER_PAGE_IMAGES_DIR: str = os.path.join(_project_root, "data", "page_images")
    PARSER_IMAGE_DPI: int = int(os.environ.get("PARSER_IMAGE_DPI", "150"))
    PARSER_BATCH_PAGE_LIMIT: int = int(os.environ.get("PARSER_BATCH_PAGE_LIMIT", "50"))

    @model_validator(mode="after")
    def _fallback_split_model_base_urls(self):
        if not self.LLM_BASE_URL:
            self.LLM_BASE_URL = self.BASE_URL
        if not self.EMBEDDING_BASE_URL:
            self.EMBEDDING_BASE_URL = self.BASE_URL
        return self

    model_config = SettingsConfigDict(
        env_file=os.path.join(_project_root, ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

# 必须要实例化
settings = Settings()
