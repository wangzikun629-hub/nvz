import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from multi_agent.backed.knowledge.api.routers import catalog_repository, router
from multi_agent.backed.knowledge.config.settings import settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        catalog_repository.ensure_tables()
        migrated = catalog_repository.migrate_from_legacy_json()
        logger.info("knowledge catalog ready migrated=%s", migrated)
    except Exception as exc:
        logger.error("knowledge catalog init failed: %s", str(exc))
    yield


def create_fast_api() -> FastAPI:
    app = FastAPI(title="Knowledge API", lifespan=lifespan)
    app.include_router(router=router)

    # 挂载 page_images 静态文件，供 ParserReview.vue 预览页面图片
    # 访问路径：/static/data/page_images/{document_id}/page_XXXX.png
    page_images_dir = settings.PARSER_PAGE_IMAGES_DIR
    if not os.path.exists(page_images_dir):
        os.makedirs(page_images_dir, exist_ok=True)
    # 挂载整个 data 目录，使路径 /static/data/page_images/... 成立
    data_dir = os.path.dirname(page_images_dir)
    app.mount("/static/data", StaticFiles(directory=data_dir), name="static_data")

    return app


if __name__ == "__main__":
    try:
        uvicorn.run(app=create_fast_api(), host="0.0.0.0", port=8001)
    except KeyboardInterrupt as exc:
        logger.error("knowledge api interrupted: %s", str(exc))
