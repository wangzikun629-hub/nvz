import uvicorn
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from multi_agent.backed.app.api.routers import router
from multi_agent.backed.app.api.auth_router import auth_router
from multi_agent.backed.app.api.admin_router import admin_router
from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.infrastructure.tools.mcp.mcp_manager import mcp_connect, mcp_cleanup
from multi_agent.backed.app.repositories import auth_session_repository, user_repository
from multi_agent.backed.app.repositories import (
    kanban_rd_repository,
    kanban_cs_repository,
    kanban_alias_repository,
    kanban_custom_column_repository,
)
from multi_agent.backed.app.repositories import (
    blessed_formula_repository,
    candidate_metric_repository,
)
from multi_agent.backed.app.api.kanban_rd_router import router as kanban_rd_router
from multi_agent.backed.app.api.kanban_cs_router import router as kanban_cs_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI应用生命周期管理

    在应用启动时建立MCP连接，在应用关闭时清理连接。
    确保资源正确初始化和释放。
    """
    # 应用启动时执行
    logger.info("初始化 users 表...")
    try:
        user_repository.ensure_table()
        auth_session_repository.ensure_table()
        cleared = auth_session_repository.purge_expired_sessions()
        logger.info("auth_sessions cleaned=%s", cleared)
        logger.info("users 表就绪")
    except Exception as e:
        logger.error(f"users 表初始化失败: {str(e)}")

    logger.info("初始化看板表...")
    try:
        kanban_rd_repository.ensure_table()
        kanban_cs_repository.ensure_table()
        kanban_alias_repository.ensure_table()
        kanban_custom_column_repository.ensure_table()
        logger.info("看板表就绪")
    except Exception as e:
        logger.error(f"看板表初始化失败: {str(e)}")

    # code review 修复（Critical#2）：这两张表此前只能靠手动执行
    # migrations/002_blessed_formula_and_candidate_metrics.sql 创建；如果忘记跑，代码会
    # 静默降级成单进程 JSON 存储（只打一行 warning log，容易被忽略），恰好悄悄违反了
    # #1（多 worker 一致性）想解决的问题。现在和 users/看板表一样，在启动时自动
    # CREATE TABLE IF NOT EXISTS，幂等、不影响已存在的表。
    logger.info("初始化候选指标 / 脚本公式祝福表...")
    try:
        candidate_metric_repository.ensure_table()
        blessed_formula_repository.ensure_table()
        logger.info("候选指标 / 脚本公式祝福表就绪")
    except Exception as e:
        logger.error(f"候选指标 / 脚本公式祝福表初始化失败: {str(e)}")

    # project_analysis_phase1.5_auto_promotion_revision.md §5/§9：多 worker 部署下，
    # register_metric() 维护的运行时指标注册表只是内存投影，权威真值源是 MySQL 里的
    # blessed_formula_map。每个 worker 进程启动时都要从这张表重建，而不是依赖某个 worker
    # 自己积累的内存历史——否则 worker A 转正的指标，worker B 永远看不到。
    logger.info("从 blessed_formula_map 重建候选指标注册表...")
    try:
        from multi_agent.backed.app.services.business_agent.script_formula_promotion_service import (
            rebuild_registry_from_blessed_map,
        )

        added = rebuild_registry_from_blessed_map()
        logger.info("候选指标注册表重建完成，新增 %d 条", added)
    except Exception as e:
        logger.error(f"候选指标注册表重建失败: {str(e)}")

    logger.info("应用启动，建立MCP连接...")
    try:
        await mcp_connect()
        logger.info("MCP连接建立完成")
    except Exception as e:
        logger.error(f"MCP连接建立失败: {str(e)}")

    yield  # 应用运行期间（先别释放mcp链接 去处理请求...）

    # 应用关闭时执行
    logger.info("应用关闭，清理MCP连接...")
    try:
        await mcp_cleanup()
        logger.info("MCP连接清理完成")
    except Exception as e:
        logger.error(f"MCP连接清理失败: {str(e)}")


def create_fast_api() -> FastAPI:
    # 1. 创建FastApi实例,绑定了生命周期事件
    app = FastAPI(title="ITS API", lifespan=lifespan)

    # 2. 处理跨域
    allowed_origins = [
        item.strip()
        for item in settings.CORS_ALLOW_ORIGINS.split(",")
        if item.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        # CORSMiddleware 会自动拦截后端的响应 并贴上这些标签 Access-Control-Allow-Origin Access-Control-Allow-Methods Access-Control-Allow-Headers
        allow_origins=allowed_origins,
        allow_credentials=True,  # cookie(自定义的key value)(user_id)
        allow_methods=["*"],  # 任意的请求都可以（POST）
        allow_headers=["*"],  # 请求头中带上自己的信息（token）
    )

    # 3. 注册各种路由（auth_router / admin_router 不受 API_KEY 保护，须在受保护 router 之前注册）
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(admin_router, prefix="/api")
    app.include_router(router=router)
    # 看板路由（与业务路由同级，同样受 X-Api-Key 保护，见 routers._require_api_key）
    app.include_router(kanban_rd_router)
    app.include_router(kanban_cs_router)

    chart_dir = Path(__file__).resolve().parents[1] / "generated" / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/generated/charts", StaticFiles(directory=str(chart_dir)), name="generated_charts")

    # 4.返回创建的FastAPI
    return app


if __name__ == '__main__':
    print("1.准备启动Web服务器")
    try:
        uvicorn.run(app=create_fast_api(), host="0.0.0.0", port=8000)

        logger.info("2.启动Web服务器成功...")

    except KeyboardInterrupt as e:
        logger.error(f"2.启动Web服务器失败: {str(e)}")
