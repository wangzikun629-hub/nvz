import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from multi_agent.backed.knowledge.api.routers import catalog_repository, router


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
    return app


if __name__ == "__main__":
    try:
        uvicorn.run(app=create_fast_api(), host="0.0.0.0", port=8001)
    except KeyboardInterrupt as exc:
        logger.error("knowledge api interrupted: %s", str(exc))
