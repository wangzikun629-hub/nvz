# AI Workflow Harness

该目录用于回归评估项目分析、业务智能体、报告总结和问题路由。

## 项目数据解析顺序

需要项目数据的用例按以下顺序解析项目根目录：

1. 用例中的 `project_root` 或命令行 `--project-root`
2. `PROJECT_SFTP_CACHE_DIR`，默认是 `app/.project_sftp_cache`
3. `--project-base-dir` 和 `PROJECT_BASE_DIRS` 中的本地目录
4. `PROJECT_BASE_DIRS` 中的 SFTP 地址，并将可读文件缓存到本地

显式 `project_id` 会覆盖旧 session 中保存的项目路径，因此 RaiDrive 断开后不会继续使用失效的 UNC 路径。

SFTP 中的项目结果和 `Snakemake_Sop` 源码会分别缓存。SOP 缓存只下载分析需要的规则和脚本，单个无权限文件不会中止整个镜像过程。

## 常用命令

在 `D:\nvz\kefu` 下运行。

已有缓存时完全离线运行：

```powershell
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite project_analysis --offline
```

运行一个用例：

```powershell
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite business_agent --case business_adapter_e2e --offline
```

指定项目本地目录：

```powershell
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite project_analysis --project-root "VZ20260427001=D:\data\VZ20260427001" --offline
```

为多个项目增加本地搜索根目录：

```powershell
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite business_agent --project-base-dir "D:\bio-projects"
```

首次运行且本地无缓存时，不加 `--offline`。程序会直连 `.env` 中配置的 SFTP 并建立缓存：

```powershell
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.run_harness --suite project_analysis
```

## 隔离运行数据

Harness 默认将 session、项目记忆、全局经验和 workflow workspace 写入：

```text
app/harness/.runtime
```

它不会修改生产环境的 `project_memories`、`project_session_states` 和 `user_memories`。可用 `--runtime-dir PATH` 修改该位置。

JSON 评测报告仍写入 `app/harness/reports`。使用 `--no-report` 可关闭报告写入。

## 可选环境变量

- `PROJECT_BASE_DIRS`：本地目录或 SFTP URL，多个值用分号分隔
- `PROJECT_SOP_BASE_DIRS`：单独指定 SOP 本地目录或 SFTP URL
- `PROJECT_SFTP_CACHE_DIR`：SFTP 本地缓存根目录
- `PROJECT_SFTP_OFFLINE=1`：禁用所有 SFTP 连接
- `HARNESS_RUNTIME_DIR`：预留的 harness 运行目录变量；命令行优先使用 `--runtime-dir`
