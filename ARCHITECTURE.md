# 项目架构说明

## 1. 项目定位

这个项目是一个以 **多代理问答 + 项目分析 + 知识库检索** 为核心的内部智能助手系统。

它不是单一的前后端应用，而是由以下几类组件共同组成：

- 2 个前端应用
  - `multi_agent/front/agent_web_ui`
  - `multi_agent/front/knowlege_platform_ui`
- 2 个后端服务
  - `multi_agent/backed/app`
  - `multi_agent/backed/knowledge`
- 1 个顶层启动器
  - `main.py`
- 1 组项目分析与评测脚本
  - `multi_agent/repeat_runs`
  - `multi_agent/rag_*`
- 1 个附加技能库
  - `bioSkills`

从职责上看，这个项目可以理解成：

- `app` 后端负责业务编排
- `knowledge` 后端负责知识能力
- `agent_web_ui` 是智能助手工作台
- `knowlege_platform_ui` 是知识平台前台

---

## 2. 总体架构

### 2.1 逻辑分层

系统整体可以拆成 4 层：

1. 展示层
   - `agent_web_ui`
   - `knowlege_platform_ui`
2. 业务编排层
   - `app` 后端
3. 知识能力层
   - `knowledge` 后端
4. 外部依赖层
   - OpenAI / Agents SDK
   - MCP 工具
   - 向量库 / Embedding / Rerank
   - 本地项目目录、知识文档目录

### 2.2 核心关系

最重要的调用关系是：

- `agent_web_ui -> app`
- `knowlege_platform_ui -> knowledge`
- `knowlege_platform_ui -> app`
- `app -> knowledge`

也就是说：

- 智能助手工作台主要对接 `app`
- 知识平台前台同时会对接 `knowledge` 和 `app`
- `app` 在需要知识增强时，会继续调用 `knowledge`

---

## 3. 顶层目录结构

项目根目录中的重要内容如下：

```text
kefu/
├─ main.py                         # 顶层启动器，拉起两个后端
├─ start_all.ps1                   # 辅助启动脚本
├─ requirements.txt                # Python 依赖
├─ multi_agent/
│  ├─ backed/
│  │  ├─ app/                      # 业务后端
│  │  └─ knowledge/                # 知识后端
│  ├─ front/
│  │  ├─ agent_web_ui/             # 智能助手工作台
│  │  └─ knowlege_platform_ui/     # 知识平台前台
│  ├─ repeat_runs/                 # 重复实验/评测数据
│  ├─ PROJECT_ANALYSIS_SCHEMA.md   # 项目分析输出结构说明
│  └─ rag_*                        # RAG 评测相关脚本与结果
├─ bioSkills/                      # 附加技能库，供项目分析能力引用
├─ scripts/                        # 辅助脚本
├─ project_workspaces/             # 项目工作区
├─ tmp/                            # 运行时临时目录
└─ venv/                           # 本地虚拟环境
```

---

## 4. 启动方式

### 4.1 顶层启动器

根目录的 [main.py](/abs/path/D:/nvz/kefu/main.py:1) 是系统统一入口。

它主要做 3 件事：

1. 检查端口是否被占用
2. 注入 `PYTHONPATH`
3. 拉起两个子进程服务

当前固定拉起的服务是：

- `knowledge`，端口 `8001`
- `app`，端口 `8000`

因此，项目当前的核心是“**一个启动器 + 两个 Python 后端**”。

### 4.2 前端开发代理

两个前端都通过 Vite 本地代理访问后端：

- `agent_web_ui`
  - `/api -> http://127.0.0.1:8000`
  - `/generated -> http://127.0.0.1:8000`
- `knowlege_platform_ui`
  - `/api -> http://127.0.0.1:8000`
  - `/knowledge-api -> http://127.0.0.1:8001`

这意味着：

- 前端本地开发时并不直接写死远端地址
- `knowlege_platform_ui` 是双后端接入

---

## 5. 四个核心应用

## 5.1 前端一：`agent_web_ui`

位置：

- [multi_agent/front/agent_web_ui](/abs/path/D:/nvz/kefu/multi_agent/front/agent_web_ui)

技术栈：

- Vue 3
- Vite
- Element Plus
- Marked

主要职责：

- 提供智能助手工作台
- 提供登录态和历史会话列表
- 展示聊天时间线
- 展示项目上下文绑定状态
- 展示 AI 报告总结
- 发起统一问题输入

这个前端的特点是：

- 更像“业务工作台”
- 用户在同一个输入框里既可以提普通问题，也可以发起项目分析
- 界面强调“当前会话绑定了哪个项目”“系统正在执行哪一步”

对应核心文件：

- [multi_agent/front/agent_web_ui/src/App.vue](/abs/path/D:/nvz/kefu/multi_agent/front/agent_web_ui/src/App.vue:1)
- [multi_agent/front/agent_web_ui/vite.config.js](/abs/path/D:/nvz/kefu/multi_agent/front/agent_web_ui/vite.config.js:1)

---

## 5.2 前端二：`knowlege_platform_ui`

位置：

- [multi_agent/front/knowlege_platform_ui](/abs/path/D:/nvz/kefu/multi_agent/front/knowlege_platform_ui)

技术栈：

- Vue 3
- Vite
- Vue Router
- Element Plus
- Axios

主要职责：

- 提供知识平台前台
- 上传知识文档
- 查看上传状态和切分结果
- 调用知识问答接口
- 调用项目问答接口

它的路由结构很简单：

- `/knowledge`：知识文档上传与管理
- `/chat`：项目/通用问答

这个前端的特点是：

- 更像“知识运营界面”
- 同时访问 `app` 和 `knowledge`
- 把知识上传、切分预览、问答放在一个独立平台里

对应核心文件：

- [multi_agent/front/knowlege_platform_ui/src/router/index.js](/abs/path/D:/nvz/kefu/multi_agent/front/knowlege_platform_ui/src/router/index.js:1)
- [multi_agent/front/knowlege_platform_ui/src/views/Knowledge.vue](/abs/path/D:/nvz/kefu/multi_agent/front/knowlege_platform_ui/src/views/Knowledge.vue:1)
- [multi_agent/front/knowlege_platform_ui/src/views/Chat.vue](/abs/path/D:/nvz/kefu/multi_agent/front/knowlege_platform_ui/src/views/Chat.vue:1)
- [multi_agent/front/knowlege_platform_ui/src/api/knowledge.js](/abs/path/D:/nvz/kefu/multi_agent/front/knowlege_platform_ui/src/api/knowledge.js:1)
- [multi_agent/front/knowlege_platform_ui/src/api/request.js](/abs/path/D:/nvz/kefu/multi_agent/front/knowlege_platform_ui/src/api/request.js:1)

---

## 5.3 后端一：`app`

位置：

- [multi_agent/backed/app](/abs/path/D:/nvz/kefu/multi_agent/backed/app)

定位：

- **业务编排后端**

`app` 后端不是一个单纯的聊天接口，而是整个系统的 orchestration layer。

它负责：

- 统一接收用户请求
- 路由到技术咨询或项目分析
- 维护会话状态
- 维护项目上下文
- 调用多代理能力
- 在需要时调用知识后端
- 输出流式回答或结构化结果

### 5.3.1 API 层

核心入口在：

- [multi_agent/backed/app/api/main.py](/abs/path/D:/nvz/kefu/multi_agent/backed/app/api/main.py:1)
- [multi_agent/backed/app/api/routers.py](/abs/path/D:/nvz/kefu/multi_agent/backed/app/api/routers.py:1)

主要接口包括：

- `/api/query`
  - 流式聊天接口
- `/api/chat`
  - JSON 聊天接口
- `/api/project_analyze`
  - 项目分析接口
- `/api/project_chart`
  - 图表生成接口
- `/api/user_sessions`
  - 会话列表
- `/api/session_messages`
  - 会话消息
- `/api/project_context`
  - 当前项目上下文
- `/api/project_context/clear`
  - 清空项目绑定

### 5.3.2 服务层

`app/services` 是项目最复杂的部分。

其中可以分成 3 类：

1. 通用业务服务
   - `agent_service.py`
   - `session_service.py`
   - `project_session_state_service.py`
   - `project_memory_service.py`
   - `project_chart_service.py`
2. 项目分析相关服务
   - `project_analysis_service.py`
   - `project_analysis_verifier_service.py`
   - `project_analysis_workflow_service.py`
   - `project_comparison_service.py`
   - `project_cuttag_diagnostic_service.py`
3. `business_agent` 子域
   - 更细颗粒度的项目分析、证据、规划、校验、知识整合服务

`business_agent` 子目录实际上体现了项目分析能力的真实复杂度，里面有：

- 规划与分析
- 证据目录与证据推理
- 答案质量与事实校验
- 领域知识整合
- 进度跟踪与后台任务

也就是说，**项目分析能力并不是一个函数，而是一整套领域服务集合**。

### 5.3.3 多代理层

对应目录：

- [multi_agent/backed/app/multi_agent](/abs/path/D:/nvz/kefu/multi_agent/backed/app/multi_agent)

核心文件：

- [agent_factory.py](/abs/path/D:/nvz/kefu/multi_agent/backed/app/multi_agent/agent_factory.py:1)
- [orchestrator_agent.py](/abs/path/D:/nvz/kefu/multi_agent/backed/app/multi_agent/orchestrator_agent.py:1)

这个层负责：

- 暴露可供 Agent 调用的工具
- 区分技术咨询与项目分析
- 维护本轮调用上下文
- 把项目分析结果挂回当前请求上下文

这里的核心判断是：

- `app` 是 orchestration layer
- 不是所有问题都直接给一个模型回答
- 有一层显式路由和工作流组织

### 5.3.4 数据与状态

对应目录：

- `repositories`
- `project_memories`
- `project_session_states`
- `user_memories`
- `generated/charts`

这里保存的内容包括：

- 用户会话历史
- 当前窗口绑定的项目
- 项目记忆
- 用户记忆
- 生成的图表文件
- AI 报告总结状态

---

## 5.4 后端二：`knowledge`

位置：

- [multi_agent/backed/knowledge](/abs/path/D:/nvz/kefu/multi_agent/backed/knowledge)

定位：

- **知识能力后端**

`knowledge` 后端是 capability layer。

它负责：

- 知识文档上传
- 异步入库
- 文档切分
- 向量检索
- 标题检索
- 重排
- 知识问答

### 5.4.1 API 层

核心入口：

- [multi_agent/backed/knowledge/api/main.py](/abs/path/D:/nvz/kefu/multi_agent/backed/knowledge/api/main.py:1)
- [multi_agent/backed/knowledge/api/routers.py](/abs/path/D:/nvz/kefu/multi_agent/backed/knowledge/api/routers.py:1)

主要接口：

- `/upload`
  - 上传知识文件
- `/upload/{task_id}`
  - 查看上传处理状态
- `/upload/{task_id}/chunks`
  - 查看切分后的 chunk
- `/upload/{task_id}/chunks/{chunk_index}`
  - 删除指定 chunk
- `/query`
  - 知识问答
- `/retrieve`
  - 仅返回检索结果

### 5.4.2 服务层

对应目录：

- [multi_agent/backed/knowledge/services](/abs/path/D:/nvz/kefu/multi_agent/backed/knowledge/services)

主要模块：

- `ingestion/ingestion_processor.py`
  - 文档切分、chunk 构建、入库
- `retrieval_service.py`
  - 检索主入口
- `query_service.py`
  - 基于检索结果生成答案
- `crawler/`
  - 知识原始内容采集支持

### 5.4.3 检索策略

`retrieval_service.py` 的实现说明这个知识后端不是单一路径检索。

它当前组合了：

1. 向量检索
   - 基于 embedding 的相似度召回
2. 标题检索
   - 基于 Markdown 标题和分词匹配
3. 去重
4. 重排
   - 通过外部 rerank 服务筛选最终结果

它的优点是：

- 兼顾语义相似度和标题/关键词命中
- 对中文知识库更稳
- 在向量检索失败时还有一定回退路径

---

## 6. 关键数据流

## 6.1 智能助手问答链路

链路如下：

1. 用户在 `agent_web_ui` 输入问题
2. 前端请求 `app:/api/query` 或 `/api/chat`
3. `app` 根据问题内容决定路由
4. 若是普通咨询，走技术专家链路
5. 若是项目分析，走项目工作流
6. 若分析过程需要知识增强，`app` 再调用 `knowledge`
7. 返回流式回答、项目上下文变化和结构化结果

## 6.2 知识文档上传链路

链路如下：

1. 用户在 `knowlege_platform_ui` 上传文件
2. 前端调用 `knowledge:/upload`
3. 后端创建后台任务
4. `ingestion_processor` 构建文档 chunks
5. chunks 写入向量存储
6. 前端轮询任务状态并可查看 chunk 预览

## 6.3 知识问答链路

链路如下：

1. 用户在知识平台问答页发起请求
2. 前端可走 `app:/api/query` 的流式代理模式
3. 或直接调用 `knowledge` 的知识接口
4. `knowledge` 执行检索、去重、重排、答案生成
5. 前端展示答案或检索片段

## 6.4 项目分析链路

链路如下：

1. 用户在 `agent_web_ui` 或知识平台的 chat 页面提问
2. `app` 识别当前问题是否涉及具体项目
3. 若识别成功，绑定当前会话的项目上下文
4. 调用项目分析工作流
5. 读取项目文件、提取指标、构建证据
6. 必要时调用知识能力补充说明
7. 输出结构化分析结果与最终回答

相关结构说明在：

- [multi_agent/PROJECT_ANALYSIS_SCHEMA.md](/abs/path/D:/nvz/kefu/multi_agent/PROJECT_ANALYSIS_SCHEMA.md:1)

---

## 7. 状态与持久化

这个项目的一个重要特点是：**它不仅返回答案，还维护状态**。

主要状态有 3 类：

### 7.1 会话状态

用于保存：

- 历史消息
- 当前会话 ID
- 用户会话列表

对应服务：

- `session_service.py`
- `session_repository.py`

### 7.2 项目上下文状态

用于保存：

- 当前 active project
- 项目是否已锁定
- 项目来源
- 最近项目问题
- 待确认项目候选
- 后续 follow-up action

对应服务：

- `project_session_state_service.py`

### 7.3 记忆与生成资产

用于保存：

- 项目记忆
- 用户记忆
- AI 报告总结
- 生成图表

对应目录：

- `project_memories`
- `user_memories`
- `generated/charts`

---

## 8. 评测与实验支持

`multi_agent` 根目录下除了正式应用代码，还有一组评测和实验文件：

- `rag_singleturn_autoeval.py`
- `analyze_repeat_consistency.py`
- `check_repeat_consistency_gate.py`
- `repeat_runs/`
- 多份 `csv/xlsx/json` 结果文件

这说明项目不只是“做功能”，还包含：

- RAG 效果评测
- 重复性/一致性验证
- 报告导出

换句话说，这个仓库同时承担了：

- 应用代码仓
- 模型/问答评测仓

---

## 9. `bioSkills` 的角色

根目录下的 `bioSkills` 不是前后端应用本体，而是一套扩展技能库。

从目录结构看，它是一个领域技能集合，覆盖：

- 生信分析
- 对齐、剪切、差异分析
- 因果基因组学
- 化学信息学等

在当前项目里，它更像：

- 项目分析能力的参考知识库
- 领域技能来源
- 可被业务分析服务引用的外部能力资产

因此它属于“**领域知识扩展层**”，而不是“四个核心应用”的一部分。

---

## 10. 当前架构的优点

### 10.1 前后端职责清晰

- 前端负责交互与状态展示
- 后端负责编排与能力执行

### 10.2 `app` 与 `knowledge` 解耦

- 业务编排和知识能力分开
- 知识后端可以被多个前端或服务复用

### 10.3 项目分析能力可扩展

- `business_agent` 子目录说明分析流程已拆成多个领域服务
- 后续可以继续按领域加能力，而不是把逻辑堆到一个文件里

### 10.4 项目上下文连续

- 会话内项目绑定机制让多轮追问更加自然
- 适合项目制分析场景

### 10.5 仓库自带评测资产

- 方便持续验证 RAG 或问答效果

---

## 11. 当前架构的复杂点与风险

### 11.1 `app` 后端复杂度很高

`app/services` 尤其是 `business_agent` 子目录已经比较重。

风险：

- 维护成本高
- 单点修改影响面大
- 新人接手成本高

### 11.2 仓库混合了运行代码与实验数据

项目里同时存在：

- 正式服务代码
- 前端构建产物
- node_modules
- 评测结果
- 临时状态文件

风险：

- 噪声大
- 定位核心代码成本高

### 11.3 状态文件较多

项目上下文、记忆、生成物都以本地目录方式沉淀。

风险：

- 部署迁移需要注意目录约定
- 多实例扩展会遇到状态共享问题

### 11.4 命名与编码一致性一般

例如：

- `knowlege_platform_ui` 拼写不标准
- 部分中文注释存在编码问题

这会增加阅读门槛。

---

## 12. 建议的阅读顺序

如果要快速理解整个项目，建议按这个顺序阅读：

1. [main.py](/abs/path/D:/nvz/kefu/main.py:1)
2. [multi_agent/backed/app/api/routers.py](/abs/path/D:/nvz/kefu/multi_agent/backed/app/api/routers.py:1)
3. [multi_agent/backed/app/multi_agent/agent_factory.py](/abs/path/D:/nvz/kefu/multi_agent/backed/app/multi_agent/agent_factory.py:1)
4. [multi_agent/backed/app/services/project_analysis_workflow_service.py](/abs/path/D:/nvz/kefu/multi_agent/backed/app/services/project_analysis_workflow_service.py:1)
5. [multi_agent/backed/knowledge/api/routers.py](/abs/path/D:/nvz/kefu/multi_agent/backed/knowledge/api/routers.py:1)
6. [multi_agent/backed/knowledge/services/retrieval_service.py](/abs/path/D:/nvz/kefu/multi_agent/backed/knowledge/services/retrieval_service.py:1)
7. [multi_agent/front/agent_web_ui/src/App.vue](/abs/path/D:/nvz/kefu/multi_agent/front/agent_web_ui/src/App.vue:1)
8. [multi_agent/front/knowlege_platform_ui/src/views/Knowledge.vue](/abs/path/D:/nvz/kefu/multi_agent/front/knowlege_platform_ui/src/views/Knowledge.vue:1)
9. [multi_agent/front/knowlege_platform_ui/src/views/Chat.vue](/abs/path/D:/nvz/kefu/multi_agent/front/knowlege_platform_ui/src/views/Chat.vue:1)
10. [multi_agent/PROJECT_ANALYSIS_SCHEMA.md](/abs/path/D:/nvz/kefu/multi_agent/PROJECT_ANALYSIS_SCHEMA.md:1)

---

## 13. 一句话总结

这个项目的本质是：

> 一个以 `app` 为业务编排中心、以 `knowledge` 为知识能力底座、同时配有两个不同前台入口的多代理项目分析系统。

