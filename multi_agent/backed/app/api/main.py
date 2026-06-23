import uvicorn
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from multi_agent.backed.app.api.routers import router
from multi_agent.backed.app.api.auth_router import auth_router
from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.logging.logger import logger
from multi_agent.backed.app.infrastructure.tools.mcp.mcp_manager import mcp_connect, mcp_cleanup
from multi_agent.backed.app.repositories import auth_session_repository, user_repository


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

    # 3. 注册各种路由（auth_router 不受 API_KEY 保护，须在受保护 router 之前注册）
    app.include_router(auth_router)
    app.include_router(router=router)

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
