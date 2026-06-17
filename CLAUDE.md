# CLAUDE.md

## 项目概述

生物信息学多智能体分析平台。用多智能体架构对 CUT&Tag / ChIP-seq / CUT&RUN / ATAC-seq 测序项目进行 QC 分析、问题诊断和专业回答。

核心能力：
- 读取 SFTP 服务器或本地目录的项目结果文件（multiqc、比对统计、peak calling 结果等）
- 自动提取 FRiP、mapping rate、duplicate rate、NRF、PBC、spike-in 等关键指标
- 以结构化 evidence_card 为唯一真值源，输出可溯源的事实回答
- 通过 fact_packet / reasoning_packet 分离事实层与解释层
- 支持多轮对话、会话记忆、项目绑定与跨项目对比
- 提供 AI 辅助图表生成、项目报告总结与诊断建议

---

## 技术栈

| 层次 | 技术 / 框架 | 用途 |
|---|---|---|
| 后端运行时 | Python 3.11+ | 主语言 |
| Web 框架 | FastAPI + Uvicorn | HTTP / SSE 流式 API |
| 多智能体 | OpenAI Agents SDK | Agent / Runner / function_tool |
| 大语言模型 | Qwen3-32B（硅基流动 / 阿里百炼） | 推理主模型 |
| 配置管理 | pydantic-settings | .env 与环境变量统一管理 |
| 联网搜索 | DashScope MCP（通义千问） | 技术智能体联网搜索 |
| 数据库 | MySQL（aiomysql 连接池） | 会话、记忆持久化 |
| 前端 | Vue.js（knowlege_platform_ui） | 可视化问答界面 |

---

## 目录结构

```
multi_agent/
  backed/
    app/
      api/              FastAPI 入口（main.py）、路由（routers.py）
      config/           settings.py（pydantic-settings）
      infrastructure/   AI 客户端、数据库、MCP 工具
        ai/             openai_client.py、prompt_loader.py
        database/       database_pool.py（aiomysql）
        tools/
          local/        project_reader.py（本地/SFTP 文件读取）
                        knowledge_base.py（RAG 知识库查询）
          mcp/          mcp_manager.py、mcp_servers.py（联网搜索 MCP）
      multi_agent/      智能体定义
        orchestrator_agent.py   主调度智能体
        technical_agent.py      技术专家智能体（联网 + 知识库）
        business_agent.py       项目分析业务智能体
        agent_factory.py        工具注册 & ContextVar 上下文管理
      services/         核心业务逻辑
        project_analysis_service.py           主分析引擎（5000+ 行）
        project_analysis_workflow_service.py  分析工作流编排
        question_router_service.py            问题路由与意图识别
        project_chart_service.py              图表生成
        session_service.py                    会话管理
        project_memory_service.py             项目记忆管理
        business_agent/                       业务智能体子服务
          metric_schema_service.py            指标 schema 与 verifier_contract
          evidence_card_service.py            evidence_card 构建与 fact_packet 组装
          fact_verification_service.py        结构化事实校验（packet-first）
          answer_quality_service.py           回答质量评分（packet-first）
          response_service.py                 渲染层
          claim_service.py                    validated_claims 构建
          assay_analysis_service.py           实验类型分析
          experiment_design_service.py        实验设计解析
      tests/            runtime 评测脚本
      harness/          离线回归评测套件
        run_harness.py                        harness 入口
        cases/                                JSON 用例文件
        runners/                              各 suite runner
        evaluators/                           断言评估器
      schemas/          Pydantic 请求/响应模型（request.py、response.py）
      repositories/     数据库 Repository 层
      prompts/          Agent 提示词（.md 格式）
    knowledge/          RAG 知识库服务
  front/
    knowlege_platform_ui/  Vue.js 前端
    agent_web_ui/           Agent 调试 Web UI
```

---

## 常用命令

所有命令在 `D:\nvz\kefu` 下运行，使用 `.\venv\Scripts\python.exe`。

**启动后端服务**
```powershell
.\venv\Scripts\python.exe -m multi_agent.backed.app.api.main
```
默认监听 `0.0.0.0:8000`，启动时自动建立 MCP 连接（通义千问联网搜索）。

**runtime 质量评测（项目3，round2）**
```powershell
.\venv\Scripts\python.exe multi_agent/backed/app/tests/evaluate_runtime_project3_quality_round2.py
```

**离线 harness 回归（全套件）**
```powershell
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite project_analysis --offline
```

**运行单个 harness 用例**
```powershell
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite business_agent --case business_adapter_e2e --offline
```

**指定本地项目目录**
```powershell
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite project_analysis --project-root "VZ20260427001=D:\data\VZ20260427001" --offline
```

---

## 环境配置

`.env` 位于 `multi_agent/backed/app/.env`，关键字段：

```
APP_ENV=development
APP_API_KEY=               # 生产环境 API 认证密钥（空则跳过认证）
CORS_ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

SF_API_KEY=                # 硅基流动
SF_BASE_URL=
AL_BAILIAN_API_KEY=        # 阿里百炼
AL_BAILIAN_BASE_URL=
DASHSCOPE_API_KEY=         # 通义千问 DashScope（MCP 联网搜索）
MAIN_MODEL_NAME=Qwen/Qwen3-32B

PROJECT_BASE_DIRS=         # 本地项目根目录，分号分隔；或 SFTP URL
PROJECT_SFTP_CACHE_DIR=
PROJECT_SFTP_OFFLINE=0     # 1=只用缓存，0=实时

MYSQL_HOST=localhost
MYSQL_DATABASE=its_db
KNOWLEDGE_BASE_URL=        # RAG 知识库服务 URL
```

**生产环境额外要求：**
- `APP_API_KEY` 必须非空
- `CORS_ALLOW_ORIGINS` 必须列出明确域名，不可使用 `*`
- 须配置至少一个 AI 服务（硅基流动或阿里百炼）

---

## API 接口

### 主要端点

| 端点 | 方法 | 描述 |
|---|---|---|
| `/chat/message` | POST | 主对话接口，SSE 流式返回，支持项目问答与通用问答 |
| `/chat/compat` | POST | 兼容接口，`question`/`query` 字段均可接受 |
| `/project/analyze` | POST | 直接触发项目分析，指定 `project_id`/`project_root` |
| `/project/chart` | POST | 生成指定指标的可视化图表（bar/line/heatmap） |
| `/project/context` | GET | 获取当前会话绑定的项目上下文状态 |
| `/session/history` | POST | 获取指定会话的历史对话记录 |
| `/generated/charts/{file}` | GET | 静态文件服务，返回已生成的图表文件 |

### 执行模式（`mode` 参数）

| mode | 描述 |
|---|---|
| `auto` | 自动路由：QuestionRouterService 识别意图后选择最优路径（默认） |
| `agent` | 强制走多智能体分析流水线，适合项目 QC 深度问答 |
| `fast_rag` | 快速 RAG 模式，直接从知识库检索，跳过 Agent 推理 |

### 认证

生产环境通过 `X-Api-Key` 请求头认证（HMAC `compare_digest` 常数时间比较）。

---

## 多智能体架构

### 请求生命周期

```
POST /chat/message
  └─► QuestionRouterService（意图识别）
        ├─► 项目 QC 问答  → execute_project_business_analysis()
        │     └─► Orchestrator Agent
        │           └─► project_analysis_workflow_service
        │                 ├─► project_reader（文件读取）
        │                 ├─► evidence_card_service（build_cards → build_fact_packet）
        │                 ├─► evidence_reasoning_service（build reasoning_packet）
        │                 └─► response_service.render_from_packet() → SSE 流式输出
        ├─► 技术问答      → Technical Agent（query_knowledge + MCP 联网搜索）
        ├─► 图表请求      → project_chart_service
        └─► 产品使用      → fast_rag / 直接回答
```

### 三类智能体

| 智能体 | 名称 | 工具 | 职责 |
|---|---|---|---|
| Orchestrator | 主调度智能体 | 所有 function_tool | 意图理解、任务分发、结果聚合 |
| Technical | 技术专家智能体 | `query_knowledge` + MCP 联网搜索 | 回答生物信息学原理、方法、文献问题 |
| Business | 项目分析业务智能体 | 无（由 orchestrator 调用） | 解读 fact_packet，输出中文可读结论 |

---

## 核心架构：事实契约（Fact Contract）

### 唯一真值源

```
evidence_chain（原始文件解析结果）
  └─► evidence_card_service.build_cards()
        └─► evidence_cards（canonical，带 schema_version）
              └─► evidence_card_service.build_fact_packet()
                    └─► fact_packet（唯一真值源）
                          └─► response_service.render_from_packet()
                                └─► Markdown 回答（渲染层，不重新决定事实）
```

`fact_packet` 结构：
```python
{
  "question": str,
  "project_id": str,
  "direct_conclusions": [{"claim", "evidence_ids", "causal_level", "confidence"}],
  "project_evidence": [{"evidence_id", "metric_id", "sample", "value",
                        "source_file", "source_field", "numerator_value",
                        "denominator_value", "threshold_verified", ...}],
  "validated_observations": [...],
  "threshold_status": {
    "has_unverified_thresholds": bool,
    "statement": str,
  }
}
```

`reasoning_packet` 结构（解释层，不等于项目已证实事实）：
```python
{
  "possible_causes": [...],
  "ranked_causes": [...],
  "hypothesis_comparison": [...],
  "verification_plan": [...],
  "evidence_against": [...],
}
```

### verifier_contract 分类

| contract | 适用指标 | 校验方式 |
|---|---|---|
| `strict_formula_recalculation` | frip_ratio, mapping_rate_percent, unique_mapping_rate_percent, correlation | 分子/分母重算，范围检查 |
| `citation_only` | adapter_percent, duplicate_rate_percent | 只引用，不重算 |
| `display_value_only` | clean_read_retention_percent, peak_count, sequencing_depth | 只展示 |
| `non_numeric_design_status` | control_binding_status | 定性，不做数值校验 |

### 服务职责分工

| 服务 | 职责 | 禁止 |
|---|---|---|
| `project_analysis_service` | 文件读取、evidence_chain 解析、evidence_cards 组装、fact_packet 与 reasoning_packet 生成 | 不做最终事实判定 |
| `metric_schema_service` | 维护指标规范定义、contract、单位、值域、公式 | 不解析文件 |
| `evidence_card_service` | evidence_chain 归一化 → evidence_card；`build_fact_packet()` 组装唯一真值源 | 不评估回答质量 |
| `fact_verification_service` | `verify_fact_packet()`（结构校验）+ `verify_render_alignment()`（弱文本校验） | 不从文本反向猜事实（仅 fallback） |
| `answer_quality_service` | `evaluate_packet()`（packet 打分） | 不用文本启发式替代结构分数（仅 fallback） |
| `response_service` | `render_from_packet()` → Markdown；固定输出结构 | 不重新决定什么是事实 |
| `question_router_service` | 意图识别与路由分发 | 不执行分析 |

---

## 指标体系

`MetricSchemaService` 维护所有受支持指标的规范定义。

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

## 项目文件读取机制

### 数据源

通过 `PROJECT_BASE_DIRS` 配置（分号分隔），支持两类数据源：
- **本地路径**：如 `Y:\Result\`，适合开发环境和已挂载网络磁盘
- **SFTP URL**：如 `sftp://user@host:22/path/`，通过 Paramiko 连接，结果缓存于 `app/.project_sftp_cache/`

### 文件索引与缓存

`project_reader.py` 实现多层缓存，以根目录 mtime 为失效键。扫描硬限制：
- 最大 1,200 个文件、220 个目录
- 扫描超时 4 秒

### 支持的证据文件类型

- MultiQC HTML/JSON 报告
- 比对统计：bowtie2 / samtools flagstat / picard MarkDuplicates / ENCODE NRF·PBC
- Peak 文件：`*.narrowPeak`、`*.broadPeak`
- FRiP 统计文件（featureCounts 输出或自定义汇总）
- Spike-in 归一化统计（scaling factor、spike mapped reads）
- 样本相关性矩阵（Spearman / Pearson）

---

## 会话与记忆机制

每个 `(user_id, session_id)` 组合维护独立的：
- **会话历史**（`session_repository`）：对话轮次、摘要
- **项目上下文状态**（`project_session_state_service`）：当前绑定项目、是否锁定、最近问题列表
- **项目记忆**（`project_memory_repository`）：AI 生成的项目报告总结缓存
- **用户记忆**（`user_memories/`）：跨会话偏好与经验积累

**项目绑定机制**：当问题中明确提及某个项目（如 `VZ20260427001`），系统自动将当前会话"锁定"到该项目，后续问题无需重复指定。切换项目时，API 通过 SSE 事件（`project_bound` / `project_changed`）通知前端。用户可主动"清空项目"解除绑定。

---

## 评测体系

### 两套分数必须分开报告

| 类型 | 脚本 | 用途 |
|---|---|---|
| `runtime_integrated_score` | `evaluate_runtime_project3_quality_round2.py` | 评估真实产品最终输出，是主要基准 |
| `offline_generation_score` | `evaluate_project3_quality.py` | 分析生成器行为，辅助调试 |

### 评分维度（evaluate_packet）

| 维度 | 满分 | 来源 |
|---|---|---|
| `fact_correctness` | 30 | `verify_fact_packet` 严重问题数 |
| `evidence_coverage` | 20 | `project_evidence` 条目数 |
| `unsupported_conclusion_control` | 15 | `direct_conclusions` 是否存在无依据结论 |
| `unit_accuracy` | 15 | `unit_error_count` |
| `experimental_design_discipline` | 10 | `sample_role_conflict_count` |
| `integration_depth` | 10 | `possible_causes` + `verification_plan` 条目数 |
| `hypothesis_discrimination` | 10 | `hypothesis_comparison` 数量 |
| 其余（causal/complexity/assay/matrix） | 30 | 混合 |

### Harness 测试套件

| suite | 描述 |
|---|---|
| `project_analysis` | 端到端项目分析，验证 fact_packet 生成与指标提取 |
| `business_agent` | 业务智能体，验证 reasoning_packet 与回答渲染 |
| `question_router` | 意图路由，验证路由精度与意图标签准确性 |

Harness 运行数据写入 `app/harness/.runtime/`，不污染生产数据库。

