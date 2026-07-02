import logging

from agents import OpenAIChatCompletionsModel
from openai import AsyncOpenAI, OpenAI
from multi_agent.backed.app.config.settings import settings

_logger = logging.getLogger(__name__)

# 硅基流动配置(主模型)
SF_API_KEY = settings.SF_API_KEY
SF_BASE_URL = settings.SF_BASE_URL
MAIN_MODEL_NAME = settings.MAIN_MODEL_NAME

# 阿里百炼配置(子模型)
AL_BAILIAN_API_KEY = settings.AL_BAILIAN_API_KEY
AL_BAILIAN_BASE_URL = settings.AL_BAILIAN_BASE_URL
SUB_MODEL_NAME = settings.SUB_MODEL_NAME
REPORT_SUMMARY_MODEL_NAME = settings.REPORT_SUMMARY_MODEL_NAME or SUB_MODEL_NAME
REPORT_SUMMARY_API_KEY = settings.REPORT_SUMMARY_API_KEY or AL_BAILIAN_API_KEY
REPORT_SUMMARY_BASE_URL = settings.REPORT_SUMMARY_BASE_URL or AL_BAILIAN_BASE_URL

# 是否具备调用 AI 报告总结的完整配置
REPORT_SUMMARY_CLIENT_CONFIGURED: bool = bool(REPORT_SUMMARY_API_KEY and REPORT_SUMMARY_BASE_URL)

if not REPORT_SUMMARY_CLIENT_CONFIGURED:
    _logger.warning(
        "AI报告总结 LLM 客户端未配置（REPORT_SUMMARY_API_KEY / REPORT_SUMMARY_BASE_URL 均为空）。"
        "generate_existing_html_report_answer 将直接抛出 RuntimeError，"
        "调用方会 fallback 到规则提取版本。请在 .env 中配置相关字段。"
    )

# Phase 1：文件发现探索 agent 专用配置（project_analysis_agent_upgrade_plan.md）。
# 该 agent 从同步调用链（project_analysis_service._select_evidence_files）触发，
# 因此使用同步 OpenAI 客户端，不复用上面几个 AsyncOpenAI 客户端。
EXPLORATION_MODEL_NAME = settings.EXPLORATION_MODEL_NAME
EXPLORATION_API_KEY = settings.EXPLORATION_API_KEY
EXPLORATION_BASE_URL = settings.EXPLORATION_BASE_URL
EXPLORATION_CLIENT_CONFIGURED: bool = bool(EXPLORATION_API_KEY and EXPLORATION_BASE_URL and EXPLORATION_MODEL_NAME)

if not EXPLORATION_CLIENT_CONFIGURED:
    _logger.info(
        "文件发现探索 agent 未配置模型（EXPLORATION_MODEL_NAME/API_KEY/BASE_URL 任一为空），"
        "project_file_discovery_service 将只使用启发式 detection_signature 匹配，不调用模型。"
    )

exploration_model_client = OpenAI(
    base_url=EXPLORATION_BASE_URL or "http://placeholder",
    api_key=EXPLORATION_API_KEY or "unconfigured",
)

# Phase 1.1：代码语义解析 agent 专用配置（project_analysis_agent_upgrade_plan.md 2.1/3 节）。
# 同样从同步调用链触发，使用同步 OpenAI 客户端。
CODE_SEMANTICS_MODEL_NAME = settings.CODE_SEMANTICS_MODEL_NAME
CODE_SEMANTICS_API_KEY = settings.CODE_SEMANTICS_API_KEY
CODE_SEMANTICS_BASE_URL = settings.CODE_SEMANTICS_BASE_URL
CODE_SEMANTICS_CLIENT_CONFIGURED: bool = bool(
    CODE_SEMANTICS_API_KEY and CODE_SEMANTICS_BASE_URL and CODE_SEMANTICS_MODEL_NAME
)

if not CODE_SEMANTICS_CLIENT_CONFIGURED:
    _logger.info(
        "代码语义解析 agent 未配置模型（CODE_SEMANTICS_MODEL_NAME/API_KEY/BASE_URL 任一为空），"
        "project_code_semantics_service 将只使用静态规则提取，不调用模型。"
    )

code_semantics_model_client = OpenAI(
    base_url=CODE_SEMANTICS_BASE_URL or "http://placeholder",
    api_key=CODE_SEMANTICS_API_KEY or "unconfigured",
)

# 创建模型客户端
# 主模型客户端(协调Agent使用)
main_model_client = AsyncOpenAI(
    base_url=SF_BASE_URL,  # 硅基流动base url
    api_key=SF_API_KEY     # 硅基流动api key
)
# 子模型客户端(干活的子Agent使用)
sub_model_client = AsyncOpenAI(
    base_url=AL_BAILIAN_BASE_URL,  # 阿里百炼base url
    api_key=AL_BAILIAN_API_KEY     # 阿里百炼api key
)
report_summary_model_client = AsyncOpenAI(
    base_url=REPORT_SUMMARY_BASE_URL or "http://placeholder",
    api_key=REPORT_SUMMARY_API_KEY or "unconfigured",
)


# 创建主调度模型
main_model = OpenAIChatCompletionsModel(
    model=MAIN_MODEL_NAME,
    openai_client=main_model_client)

# 创建子调度模型
sub_model = OpenAIChatCompletionsModel(
    model=SUB_MODEL_NAME,
    openai_client=sub_model_client)
