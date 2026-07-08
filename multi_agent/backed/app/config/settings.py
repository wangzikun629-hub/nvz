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

    # F-0（docs/project_planner_orchestrator_agent_design.md 第 1.5/4 节）：修复"外层
    # 25s 总预算装不下文件发现子阶段自己配置的 30s 硬预算"这个现状矛盾。做法是把
    # 外层总预算显式搬到这里做唯一真值源，`FILE_DISCOVERY_STAGE_TIMEOUT_SECONDS` 的
    # 有效值不再是一个与总预算无关的独立常量，而是 clamp 到
    # `PROJECT_ANALYSIS_TIMEOUT_SECONDS - OTHER_STAGES_RESERVED_SECONDS` 之内
    # （见下面 `effective_file_discovery_budget_seconds` 属性）。
    #
    # 这里的具体秒数（尤其 OTHER_STAGES_RESERVED_SECONDS=5.0）是保守估算，不是真实
    # 项目压测结论——本次会话明确决定："先用原型里的保守值上线，后续用真实项目的
    # 耗时分布数据再调"，所有数字都可以用对应环境变量覆盖，不需要改代码。
    PROJECT_ANALYSIS_TIMEOUT_SECONDS: float = Field(
        default=60.0,
        description="analyze() 整个同步分析主流程的外层总预算（秒），与 "
        "runtime_service.py 现状值保持一致，是本次 F-0 唯一真值源"
    )
    OTHER_STAGES_RESERVED_SECONDS: float = Field(
        default=5.0,
        description="项目上下文构建/解析/建卡/因果图等除'文件发现'与'重探索'之外"
        "其余阶段的预留耗时（秒），保守估算，未经真实项目压测校准"
    )
    AGENT_TIMEOUT_SAFETY_MARGIN_SECONDS: float = Field(
        default=2.0,
        description="探索 agent 单次调用超时必须严格小于文件发现子预算，这里是"
        "预留给 agent 自行收尾（把已收集候选序列化返回）的安全边际（秒）"
    )

    # Stage B（project_analysis_exploration_and_evolution_plan.md）：文件发现的模型
    # 增强分支从"单轮分类调用"换成多轮工具调用探索 agent 后，单次往返耗时明显高于
    # 之前的单轮调用，6.0s 的旧预算大概率不够用（agent 内部自己的
    # `_DEFAULT_AGENT_TIMEOUT_SECONDS`（2026-07-03 真实回放排查后调到 25.0s）会先一步
    # 自行收尾并返回已收集到的候选，但这里的外层硬预算必须留出比它更长的余量，否则
    # agent 还没来得及自行收尾就被外层 ThreadPoolExecutor 直接抛弃、连部分候选都拿不到）。
    # 默认值同样是待压测校准的经验值，可通过 PROJECT_FILE_DISCOVERY_BUDGET_SECONDS
    # 环境变量覆盖。
    #
    # F-0 修订：这个字段现在只是"文件发现子阶段的名义上限"，真正生效的值见下面
    # `effective_file_discovery_budget_seconds`——会被 clamp 到不超过
    # `PROJECT_ANALYSIS_TIMEOUT_SECONDS - OTHER_STAGES_RESERVED_SECONDS`，不再允许
    # 子阶段预算独立于总预算配置成一个总预算根本装不下的数字。
    FILE_DISCOVERY_STAGE_TIMEOUT_SECONDS: float = Field(
        default=30.0,
        description="文件发现启发式探测 + 探索 agent 单阶段硬预算上限（秒）——实际"
        "生效值见 effective_file_discovery_budget_seconds，会被 clamp 到总预算内"
    )

    @property
    def effective_file_discovery_budget_seconds(self) -> float:
        """F-0：文件发现子阶段的实际生效预算 = min(名义上限, 总预算 - 其余阶段预留)。

        这是本次修复的核心——现状 bug 是 `FILE_DISCOVERY_STAGE_TIMEOUT_SECONDS` 与
        `PROJECT_ANALYSIS_TIMEOUT_SECONDS` 互不知情，子阶段配置的硬预算（30s）可以
        大于外层总预算本身（25s）。这里强制子阶段预算不能超过总预算刨去其余阶段
        预留后剩下的部分。
        """
        return max(
            0.0,
            min(
                self.FILE_DISCOVERY_STAGE_TIMEOUT_SECONDS,
                self.PROJECT_ANALYSIS_TIMEOUT_SECONDS - self.OTHER_STAGES_RESERVED_SECONDS,
            ),
        )

    @property
    def effective_exploration_agent_timeout_seconds(self) -> float:
        """F-0：探索 agent 单次调用超时，必须严格小于文件发现子预算减去安全边际，
        否则 agent 还没来得及自行收尾就被外层 ThreadPoolExecutor 直接抛弃（见
        docs/project_planner_orchestrator_agent_design.md 第 1.5 节）。"""
        return max(
            0.5,
            self.effective_file_discovery_budget_seconds - self.AGENT_TIMEOUT_SAFETY_MARGIN_SECONDS,
        )

    # 离线 harness 的确定性门禁（project_analysis_phase1.5_auto_promotion_revision.md §11）：
    # 开启后，file discovery / field discovery / code semantics 的模型增强分支在离线模式下
    # 一律跳过（等价于模型未配置），只保留确定性的启发式/静态正则路径，保证同一 commit
    # 反复跑分数完全一致。生产环境不应开启（会关闭模型增强能力）。
    HARNESS_DETERMINISTIC_MODE: bool = Field(
        default=True,
        description="离线 harness 是否强制关闭所有模型增强分支以保证结果确定性"
    )

    # Phase 5（2026-07-06-fact-packet-first-refactor-plan.md / docs/
    # project_planner_orchestrator_agent_design.md F-5）：planner-orchestrator 实际
    # 派发子任务的总开关，默认关闭。关闭时 `_reexplore_unresolved_metrics` 完全走
    # Phase 4 之前的既有单轮判断，`planner_orchestrator_trace.mode` 恒为 "dry_run"，
    # `fact_packet`/`evidence_cards` 行为字节级不变。只有确认 dry-run trace 在真实
    # 项目上可信之后才应该打开。
    PLANNER_DISPATCH_ENABLED: bool = Field(
        default=False,
        description="是否允许 planner-orchestrator 真实调用 explore_files/"
        "check_code_semantics 补证据缺口；默认关闭，行为等价于只产出 dry-run trace"
    )
    PLANNER_MAX_DISPATCH_ROUNDS: int = Field(
        default=2,
        description="PLANNER_DISPATCH_ENABLED 开启时，重探索循环允许的最大轮数；"
        "轮数和 _REEXPLORE_SOFT_DEADLINE_SECONDS 墙钟预算是双重上限，谁先触发谁生效，"
        "不是互相替代关系"
    )
    CODE_SEMANTICS_TOOL_CONFIDENCE_THRESHOLD: float = Field(
        default=0.6,
        description="planner 在 explore_files / check_code_semantics 两个工具之间"
        "选择的置信度阈值，与 project_file_discovery_service."
        "_CODE_SEMANTICS_TRIGGER_MAX_CONFIDENCE 语义一致（迁移为可配置项，避免两处"
        "各自维护一份 0.6）：某指标当前最高规则命中置信度低于此值时派 "
        "check_code_semantics，否则派 explore_files"
    )
    EXPLORATION_ALWAYS_ON_ENABLED: bool = Field(
        default=False,
        description="Stage G-2（2026-07-07-stage-g-explorer-codesemantics-tiered-plan.md）："
        "默认关闭时，_exploration_agent_augment() 维持现状，只对启发式未命中的"
        "剩余指标（target_metrics - heuristic_hits）触发探索 agent。开启后对完整"
        "target_metrics 无条件触发探索 agent，启发式/代码语义命中的候选改为以"
        "\"确认优先\"提示词喂给探索 agent 的 task brief，不再作为是否调用的门槛。"
        "这是一次实打实的调用量增加，必须先在关闭状态下合入并验证行为逐位不变，"
        "收集真实调用量/耗时数据后再评估是否把默认值改为 True，不允许直接默认开启"
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

    # ==================== F-0 预算自洽性校验 ====================
    @model_validator(mode='after')
    def check_stage_f_budget_consistency(self) -> Self:
        """F-0（docs/project_planner_orchestrator_agent_design.md 第 1.5 节）：现状的
        预算矛盾是"静默存在，只在真实超时时才被发现"——这里改成配置加载时就主动
        检出并记录告警，不阻断启动（预算配置不自洽不代表服务不可用，只是文件发现/
        重探索阶段会更容易提前降级），但必须让运维/开发者能第一时间在日志里看到。
        """
        problems: list[str] = []
        if self.effective_file_discovery_budget_seconds <= 0:
            problems.append(
                "effective_file_discovery_budget_seconds<=0："
                f"PROJECT_ANALYSIS_TIMEOUT_SECONDS={self.PROJECT_ANALYSIS_TIMEOUT_SECONDS} - "
                f"OTHER_STAGES_RESERVED_SECONDS={self.OTHER_STAGES_RESERVED_SECONDS} 已经不剩"
                "任何时间给文件发现阶段"
            )
        if self.effective_exploration_agent_timeout_seconds < 1.0:
            problems.append(
                "effective_exploration_agent_timeout_seconds="
                f"{self.effective_exploration_agent_timeout_seconds:.2f}s 过小，探索 agent "
                "几乎不可能在这个时间内完成任何有意义的多轮工具调用"
            )
        if problems:
            try:
                from multi_agent.backed.app.infrastructure.logging.logger import logger

                logger.warning(
                    "settings stage=stage_f_budget_check status=inconsistent problems=%s",
                    problems,
                )
            except Exception:
                # 日志基础设施本身不可用时不能阻断配置加载，退化为标准错误输出。
                import sys as _sys

                print(f"[settings] stage_f_budget_check inconsistent: {problems}", file=_sys.stderr)
        return self


# 创建全局配置实例
settings = Settings()

