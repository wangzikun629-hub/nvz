# CLAUDE.md

## 项目概述

生物信息学多智能体问答平台，支持 CUT&Tag / ChIP-seq / CUT&RUN / ATAC-seq 测序项目的 QC 分析、问题诊断与专业回答。

核心能力：从 SFTP / 本地目录读取项目结果文件，自动提取 FRiP、mapping rate、duplicate rate、NRF/PBC、spike-in 等关键指标，以 `evidence_card` 为唯一真值源，通过 `fact_packet / reasoning_packet` 分离事实层与解释层，输出可溯源的中文回答。支持多轮对话、会话记忆、项目绑定、跨项目对比与图表生成。

---

## 技术栈

| 层次 | 技术 | 用途 |
|---|---|---|
| 后端 | Python 3.11 + FastAPI + Uvicorn | HTTP / SSE 流式 API |
| 多智能体 | OpenAI Agents SDK | Agent / Runner / function_tool |
| 大模型 | Qwen3-32B（硅基流动 / 阿里百炼） | 推理主模型 |
| 联网搜索 | DashScope MCP（通义千问） | 技术智能体联网搜索 |
| 数据库 | MySQL + aiomysql | 会话与记忆持久化 |
| 配置 | pydantic-settings | .env 统一管理 |
| 前端 | Vue.js | 可视化问答界面 |

---

## 常用命令

工作目录：`D:\nvz\kefu`，解释器：`.\venv\Scripts\python.exe`

```powershell
# 启动后端（监听 0.0.0.0:8000，自动建立 MCP 连接）
.\venv\Scripts\python.exe -m multi_agent.backed.app.api.main

# runtime 质量评测（主要基准）
.\venv\Scripts\python.exe multi_agent/backed/app/tests/evaluate_runtime_project3_quality_round2.py

# 离线 harness 全套件
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite project_analysis --offline

# 单用例
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite business_agent --case business_adapter_e2e --offline

# 指定本地项目目录
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite project_analysis --project-root "VZ20260427001=D:\data\VZ20260427001" --offline
```

---

## 环境配置

`.env` 路径：`multi_agent/backed/app/.env`

```ini
APP_ENV=development
APP_API_KEY=               # 生产必填，空则跳过认证
CORS_ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

SF_API_KEY=                # 硅基流动
SF_BASE_URL=
AL_BAILIAN_API_KEY=        # 阿里百炼
AL_BAILIAN_BASE_URL=
DASHSCOPE_API_KEY=         # 通义千问（MCP 联网搜索）
MAIN_MODEL_NAME=Qwen/Qwen3-32B

PROJECT_BASE_DIRS=         # 本地路径（分号分隔）或 sftp://user@host:22/path/
PROJECT_SFTP_CACHE_DIR=
PROJECT_SFTP_OFFLINE=0     # 1=只用缓存

MYSQL_HOST=localhost
MYSQL_DATABASE=its_db
KNOWLEDGE_BASE_URL=        # RAG 知识库服务地址
```

生产要求：`APP_API_KEY` 非空；`CORS_ALLOW_ORIGINS` 使用明确域名；至少配置一个 AI 服务。

---

## API 接口

认证：`X-Api-Key` 请求头（HMAC `compare_digest`）

| 端点 | 方法 | 描述 |
|---|---|---|
| `/chat/message` | POST | 主对话，SSE 流式，支持 `mode` 参数 |
| `/chat/compat` | POST | 兼容接口，`question`/`query` 均可 |
| `/project/analyze` | POST | 直接触发分析，指定 `project_id`/`project_root` |
| `/project/chart` | POST | 生成可视化图表（bar/line/heatmap） |
| `/project/context` | GET | 获取会话绑定的项目上下文 |
| `/session/history` | POST | 获取会话历史 |
| `/generated/charts/{file}` | GET | 静态图表文件 |

`mode` 参数：`auto`（默认，路由器自动选择）/ `agent`（强制多智能体）/ `fast_rag`（跳过推理直接检索）

---

## 多智能体架构

```
POST /chat/message
  └─► QuestionRouterService（意图识别）
        ├─► 项目 QC 问答  → Orchestrator Agent
        │     └─► project_analysis_workflow_service
        │           ├─► project_reader（文件读取）
        │           ├─► evidence_card_service（build_cards → build_fact_packet）
        │           ├─► evidence_reasoning_service（build_reasoning_packet）
        │           └─► response_service.render_from_packet() → SSE
        ├─► 技术问答      → Technical Agent（query_knowledge + MCP 联网）
        ├─► 图表请求      → project_chart_service
        └─► 产品使用      → fast_rag / 直接回答
```

| 智能体 | 工具 | 职责 |
|---|---|---|
| Orchestrator | 所有 function_tool | 意图理解、任务分发、结果聚合 |
| Technical | `query_knowledge` + MCP | 生物信息学原理、方法、文献问题 |
| Business | 无（由 orchestrator 调用） | 解读 fact_packet，输出中文结论 |

---

## 核心架构：事实契约（Fact Contract）

**数据流（唯一真值源）：**

```
原始文件
  → evidence_chain
  → evidence_card_service.build_cards()  →  evidence_cards（canonical）
  → evidence_card_service.build_fact_packet()  →  fact_packet  ← 唯一真值源
  → response_service.render_from_packet()  →  Markdown 输出（渲染层不决定事实）
```

**fact_packet 关键字段：**
```python
{
  "question": str, "project_id": str,
  "direct_conclusions": [{"claim", "evidence_ids", "causal_level", "confidence"}],
  "project_evidence": [{"evidence_id", "metric_id", "sample", "value",
                        "source_file", "source_field", "numerator_value",
                        "denominator_value", "threshold_verified"}],
  "validated_observations": [...],
  "threshold_status": {"has_unverified_thresholds": bool, "statement": str}
}
```

**reasoning_packet 字段**（解释层，非已证实事实）：`possible_causes` / `ranked_causes` / `hypothesis_comparison` / `verification_plan` / `evidence_against`

### verifier_contract

| contract | 指标 | 校验方式 |
|---|---|---|
| `strict_formula_recalculation` | frip_ratio, mapping_rate_percent, unique_mapping_rate_percent, correlation | 分子/分母重算 + 范围检查 |
| `citation_only` | adapter_percent, duplicate_rate_percent | 只引用，不重算 |
| `display_value_only` | clean_read_retention_percent, peak_count, sequencing_depth 等 | 只展示 |
| `non_numeric_design_status` | control_binding_status | 定性，不做数值校验 |

### 服务职责边界

| 服务 | 做什么 | 不做什么 |
|---|---|---|
| `project_analysis_service` | `analyze()` 主流程、evidence_chain 构建、诊断汇总、报告渲染 | 不做最终事实判定 |
| `project_file_parser_service` | 各类表格 parser（QC/alignment/FRiP/correlation 等）、`parse_evidence_file` | 不决定 evidence_chain 结构 |
| `project_context_builder_service` | samplelist/config/workflow/HTML 上下文解析 | 不做指标计算 |
| `project_cause_analysis_service` | 因果图构建、假说排名、竞争假说对比 | 不做事实校验 |
| `project_parse_cache` | 文件解析结果缓存、项目上下文 TTL 缓存 | 不做业务逻辑 |
| `project_analysis_constants` | 规则字典（PROFESSIONAL_RULES 等）、文件名提示映射 | 不含可执行逻辑 |
| `metric_schema_service` | 维护指标规范（contract/单位/值域/公式） | 不解析文件 |
| `evidence_card_service` | evidence_chain → evidence_card；`build_fact_packet()` | 不评估回答质量 |
| `fact_verification_service` | `verify_fact_packet()` + `verify_render_alignment()` | 不从文本反向猜事实 |
| `answer_quality_service` | `evaluate_packet()` 打分 | 不用文本启发式替代结构分数 |
| `response_service` | `render_from_packet()` → Markdown | 不重新决定事实 |
| `question_router_service` | 意图识别与路由 | 不执行分析 |

---

## 指标体系

| metric_id | 中文名 | 单位 | 适用实验 | contract |
|---|---|---|---|---|
| `adapter_percent` | 接头检出率 | % | all | `citation_only` |
| `clean_read_retention_percent` | Trimming 保留率 | % | all | `display_value_only` |
| `mapping_rate_percent` | 总比对率 | % | all | `strict_formula_recalculation` |
| `unique_mapping_rate_percent` | 唯一比对率 | % | all | `strict_formula_recalculation` |
| `duplicate_rate_percent` | 重复率 | % | all | `citation_only` |
| `frip_ratio` | FRiP | fraction | cuttag/chipseq/cutrun/atacseq | `strict_formula_recalculation` |
| `peak_count` | Peak 数量 | count | cuttag/chipseq/cutrun/atacseq | `display_value_only` |
| `nrf` / `pbc1` / `pbc2` | NRF / PBC1 / PBC2 | ratio | cuttag/chipseq/cutrun/atacseq | `display_value_only` |
| `spikein_scaling_factor` | Spike-in 归一化因子 | ratio | 含 spike-in 实验 | `display_value_only` |
| `correlation` | 样本相关性 | fraction | all | `strict_formula_recalculation` |
| `control_binding_status` | 对照结合状态 | 定性 | cuttag/chipseq | `non_numeric_design_status` |
| `tss_enrichment` | TSS Enrichment | score | atacseq | `display_value_only` |
| `mt_rate_percent` | 线粒体 reads 比例 | % | all | `display_value_only` |

---

## 文件读取机制

**数据源**（`PROJECT_BASE_DIRS` 分号分隔）：本地路径（如 `Y:\Result\`）或 SFTP URL（Paramiko 连接，缓存至 `app/.project_sftp_cache/`）

**扫描限制**：最大 1,200 文件 / 220 目录 / 4 秒超时，以根目录 mtime 为缓存失效键

**支持的证据文件**：MultiQC HTML/JSON、bowtie2/flagstat/picard/ENCODE NRF·PBC 比对统计、`*.narrowPeak` / `*.broadPeak`、FRiP 汇总、Spike-in scaling factor、Spearman/Pearson 相关矩阵

---

## 会话与记忆

每个 `(user_id, session_id)` 独立维护：会话历史（`session_repository`）、项目上下文绑定（`project_session_state_service`）、项目报告摘要缓存（`project_memory_repository`）、跨会话用户记忆（`user_memories/`）。

**项目绑定**：问题中出现项目号（如 `VZ20260427001`）时自动锁定，后续无需重复指定；切换时通过 SSE 事件（`project_bound` / `project_changed`）通知前端。

---

## 评测体系

**两套分数严格分开报告：**

| 类型 | 脚本 | 用途 |
|---|---|---|
| `runtime_integrated_score` | `evaluate_runtime_project3_quality_round2.py` | 真实产品输出，**主要基准** |
| `offline_generation_score` | `evaluate_project3_quality.py` | 生成器行为分析，辅助调试 |

**evaluate_packet 评分维度：**

| 维度 | 满分 | 来源 |
|---|---|---|
| `fact_correctness` | 30 | `verify_fact_packet` 严重问题数 |
| `evidence_coverage` | 20 | `project_evidence` 条目数 |
| `unsupported_conclusion_control` | 15 | `direct_conclusions` 无依据结论数 |
| `unit_accuracy` | 15 | `unit_error_count` |
| `experimental_design_discipline` | 10 | `sample_role_conflict_count` |
| `integration_depth` | 10 | `possible_causes` + `verification_plan` 条目数 |
| `hypothesis_discrimination` | 10 | `hypothesis_comparison` 数量 |
| 其余（causal/complexity/assay/matrix） | 30 | 混合 |

**Harness 测试套件**（数据写入 `app/harness/.runtime/`，不污染生产库）：

| suite | 验证内容 |
|---|---|
| `project_analysis` | fact_packet 生成与指标提取 |
| `business_agent` | reasoning_packet 与回答渲染 |
| `question_router` | 路由精度与意图标签准确性 |

---

## 完整代码结构

```
multi_agent/
├── backed/
│   ├── app/
│   │   ├── api/
│   │   │   ├── main.py                          # FastAPI 应用入口，启动 MCP 连接
│   │   │   ├── routers.py                       # 主路由注册（chat/project/session）
│   │   │   ├── admin_router.py                  # 管理后台路由
│   │   │   └── auth_router.py                   # 认证路由
│   │   ├── config/
│   │   │   ├── settings.py                      # pydantic-settings 配置
│   │   │   └── test_settings.py
│   │   ├── infrastructure/
│   │   │   ├── ai/
│   │   │   │   ├── openai_client.py             # OpenAI 兼容客户端（Qwen）
│   │   │   │   └── prompt_loader.py             # 提示词加载器
│   │   │   ├── auth/
│   │   │   │   └── token_utils.py
│   │   │   ├── database/
│   │   │   │   └── database_pool.py             # aiomysql 连接池
│   │   │   ├── logging/
│   │   │   │   └── logger.py
│   │   │   ├── tools/
│   │   │   │   ├── local/
│   │   │   │   │   ├── project_reader.py        # 本地/SFTP 文件读取 + 多层缓存
│   │   │   │   │   └── knowledge_base.py        # RAG 知识库查询
│   │   │   │   └── mcp/
│   │   │   │       ├── mcp_manager.py           # MCP 连接管理
│   │   │   │       ├── mcp_pool.py              # MCP 连接池
│   │   │   │       └── mcp_servers.py           # 服务器定义（DashScope 联网）
│   │   │   └── async_lock_manager.py
│   │   ├── multi_agent/
│   │   │   ├── orchestrator_agent.py            # 主调度智能体
│   │   │   ├── technical_agent.py               # 技术专家智能体
│   │   │   ├── business_agent.py                # 项目分析业务智能体
│   │   │   ├── agent_factory.py                 # 工具注册 + ContextVar 上下文
│   │   │   └── project_progress.py              # 分析进度追踪
│   │   ├── services/
│   │   │   ├── project_analysis_service.py          # 主分析引擎：analyze() 主流程 + evidence chain（~2900 行）
│   │   │   ├── project_analysis_constants.py         # 规则字典与常量（PROFESSIONAL_RULES / QUESTION_FILE_HINTS 等）
│   │   │   ├── project_parse_cache.py                # 文件解析缓存 + 项目上下文缓存（TTL + in-flight 去重）
│   │   │   ├── project_file_parser_service.py        # 各类表格 parser（build_*_summary / parse_evidence_file）
│   │   │   ├── project_context_builder_service.py    # 项目上下文构建（samplelist/config/workflow/HTML 报告）
│   │   │   ├── project_cause_analysis_service.py     # 因果图 + 假说排名（build_cause_graph / rank_candidate_causes）
│   │   │   ├── project_analysis_workflow_service.py  # 分析工作流编排
│   │   │   ├── project_analysis_verifier_service.py
│   │   │   ├── question_router_service.py       # 意图识别与路由
│   │   │   ├── project_chart_service.py         # 图表生成
│   │   │   ├── project_comparison_service.py    # 跨项目对比
│   │   │   ├── project_context_intent_service.py
│   │   │   ├── project_cuttag_diagnostic_service.py
│   │   │   ├── project_expert_tool_service.py
│   │   │   ├── project_locator_service.py       # 项目目录定位
│   │   │   ├── project_memory_service.py        # 项目记忆管理
│   │   │   ├── project_session_state_service.py # 会话项目绑定状态
│   │   │   ├── session_service.py               # 会话管理
│   │   │   ├── stream_response_service.py       # SSE 流式输出
│   │   │   ├── agent_service.py
│   │   │   ├── followup_intent_service.py
│   │   │   ├── rag_fast_service.py              # fast_rag 直接检索
│   │   │   └── business_agent/                  # 业务智能体子服务
│   │   │       ├── metric_schema_service.py     # 指标 schema + verifier_contract
│   │   │       ├── evidence_card_service.py     # evidence_card + fact_packet 组装
│   │   │       ├── evidence_reasoning_service.py  # reasoning_packet 构建
│   │   │       ├── evidence_catalog_service.py
│   │   │       ├── fact_verification_service.py # 结构化事实校验
│   │   │       ├── answer_quality_service.py    # packet 质量评分
│   │   │       ├── response_service.py          # render_from_packet() → Markdown
│   │   │       ├── claim_service.py             # validated_claims 构建
│   │   │       ├── assay_analysis_service.py    # 实验类型分析
│   │   │       ├── experiment_design_service.py # 实验设计解析
│   │   │       ├── analysis_planner_service.py
│   │   │       ├── answer_cache_service.py
│   │   │       ├── background_task_service.py
│   │   │       ├── bio_skill_reference_service.py
│   │   │       ├── concurrency_service.py
│   │   │       ├── data_analysis_service.py
│   │   │       ├── experience_service.py
│   │   │       ├── harness_guard_service.py
│   │   │       ├── knowledge_service.py
│   │   │       ├── planning_service.py
│   │   │       ├── progress_service.py
│   │   │       ├── project_snapshot_service.py
│   │   │       ├── read_lineage_service.py
│   │   │       ├── runtime_service.py
│   │   │       ├── semantic_guard_service.py
│   │   │       ├── tool_registry_service.py
│   │   │       ├── user_assertion_service.py
│   │   │       └── workspace.py
│   │   ├── schemas/
│   │   │   ├── request.py                       # Pydantic 请求模型
│   │   │   └── response.py                      # Pydantic 响应模型
│   │   ├── repositories/
│   │   │   ├── session_repository.py
│   │   │   ├── project_memory_repository.py
│   │   │   ├── project_report_cache_repository.py
│   │   │   ├── project_state_repository.py
│   │   │   ├── user_repository.py
│   │   │   └── auth_session_repository.py
│   │   ├── prompts/                             # Agent 提示词（.md）
│   │   │   ├── orchestrator_v1.md
│   │   │   ├── project_business_agent.md
│   │   │   ├── technical_agent.md
│   │   │   └── comprehensive_service_agent.md
│   │   ├── tests/                               # runtime 评测脚本
│   │   │   ├── evaluate_runtime_project3_quality_round2.py  ← 主要基准
│   │   │   ├── evaluate_project3_quality.py
│   │   │   ├── evaluate_runtime_project1/2/3_quality.py
│   │   │   ├── deep_evaluation_report*.py
│   │   │   └── test_*.py
│   │   ├── harness/                             # 离线回归评测套件
│   │   │   ├── run_harness.py                   # 入口
│   │   │   ├── cases/
│   │   │   │   ├── project_analysis/            # 11 个 JSON 用例
│   │   │   │   ├── business_agent/              # 11 个 JSON 用例
│   │   │   │   └── question_router/             # core_intents.json
│   │   │   ├── runners/
│   │   │   │   ├── project_analysis_runner.py
│   │   │   │   ├── business_agent_runner.py
│   │   │   │   └── question_router_runner.py
│   │   │   ├── evaluators/
│   │   │   │   └── assertions.py
│   │   │   ├── expert_eval/                     # 专家评估数据集
│   │   │   │   ├── evaluator.py
│   │   │   │   ├── cases.jsonl
│   │   │   │   └── dataset_manifest.json
│   │   │   ├── reports/                         # 历史运行报告（JSON）
│   │   │   ├── .runtime/                        # harness 运行时数据（不污染生产库）
│   │   │   └── model_comparison.py
│   │   └── utils/
│   │       ├── response_util.py
│   │       ├── retry_util.py
│   │       └── text_util.py
│   └── knowledge/                               # RAG 知识库服务（独立部署）
│       ├── api/
│       │   ├── main.py
│       │   └── routers.py
│       ├── config/
│       │   └── settings.py
│       ├── repositories/
│       │   ├── catalog_repository.py
│       │   ├── file_repository.py
│       │   └── vector_store_repository.py
│       ├── schemas/
│       │   └── schema.py
│       ├── cli/
│       │   └── upload_cli.py
│       └── data/
│           ├── catalog/
│           │   └── knowledge_catalog.json
│           └── crawl/                           # 生信知识文档（.md）
│               ├── CUTTag_ATAC_FAQ_知识库.md
│               ├── FastQC_结果解读.md
│               ├── Spike-in校准.md
│               └── ...
└── front/
    ├── knowlege_platform_ui/                    # 主前端（Vue.js）
    │   └── src/
    │       ├── api/
    │       │   ├── request.js                   # Axios 封装
    │       │   ├── auth.js
    │       │   ├── admin.js
    │       │   └── knowledge.js
    │       ├── views/
    │       │   ├── Chat.vue                     # 主问答界面
    │       │   ├── KbChat.vue                   # 知识库问答
    │       │   ├── Knowledge.vue                # 知识库管理
    │       │   ├── Login.vue
    │       │   └── admin/
    │       │       └── AdminDashboard.vue
    │       ├── layout/
    │       │   └── index.vue
    │       ├── router/
    │       │   └── index.js
    │       └── App.vue
    └── agent_web_ui/                            # Agent 调试 Web UI（轻量单页）
        └── src/
            └── App.vue
```

