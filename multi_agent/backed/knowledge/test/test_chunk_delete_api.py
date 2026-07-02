import sys
import types
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient


ingestion_module = types.ModuleType("multi_agent.backed.knowledge.services.ingestion.ingestion_processor")


class DummyVectorStore:
    def _build_chunk_id(self, document, index: int) -> str:
        return f"chunk-{index}"

    def delete_documents_by_ids(self, ids):
        return len(ids)


class DummyIngestionProcessor:
    def __init__(self):
        self.vector_store = DummyVectorStore()


ingestion_module.IngestionProcessor = DummyIngestionProcessor
sys.modules.setdefault(
    "multi_agent.backed.knowledge.services.ingestion.ingestion_processor",
    ingestion_module,
)

query_module = types.ModuleType("multi_agent.backed.knowledge.services.query_service")


class DummyQueryService:
    def generate_answer(self, user_question, retrival_context):
        return "ok"


query_module.QueryService = DummyQueryService
query_module_name = "multi_agent.backed.knowledge.services.query_service"
sys.modules.setdefault(query_module_name, query_module)

retrieval_module_name = "multi_agent.backed.knowledge.services.retrieval_service"
retrieval_module = types.ModuleType(retrieval_module_name)


class DummyRetrievalService:
    def retrieval(self, user_question):
        return []


retrieval_module.RetrievalService = DummyRetrievalService
sys.modules.setdefault(retrieval_module_name, retrieval_module)

from multi_agent.backed.knowledge.api import routers as routers_module
from multi_agent.backed.knowledge.api.routers import router, upload_tasks

for module_name, module in (
    ("multi_agent.backed.knowledge.services.ingestion.ingestion_processor", ingestion_module),
    (query_module_name, query_module),
    (retrieval_module_name, retrieval_module),
):
    if sys.modules.get(module_name) is module:
        sys.modules.pop(module_name)


class ChunkDeleteApiTests(unittest.TestCase):
    def setUp(self):
        upload_tasks.clear()
        routers_module._ingestion_processor = DummyIngestionProcessor()
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)

    def test_delete_chunk_marks_it_deleted_and_updates_count(self):
        deleted_ids = []
        routers_module._ingestion_processor.vector_store.delete_documents_by_ids = (
            lambda ids: deleted_ids.extend(ids) or len(ids)
        )
        upload_tasks["task-1"] = {
            "status": "success",
            "message": "ok",
            "file_name": "demo.pdf",
            "chunks_added": 2,
            "chunk_previews": [
                {
                    "chunk_index": 1,
                    "chunk_id": "chunk-a",
                    "content": "a",
                    "metadata": {},
                    "deleted": False,
                },
                {
                    "chunk_index": 2,
                    "chunk_id": "chunk-b",
                    "content": "b",
                    "metadata": {},
                    "deleted": False,
                },
            ],
        }

        response = self.client.delete("/upload/task-1/chunks/2")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted"], True)
        self.assertEqual(response.json()["chunks_added"], 1)
        self.assertEqual(deleted_ids, ["chunk-b"])
        self.assertTrue(upload_tasks["task-1"]["chunk_previews"][1]["deleted"])

    def test_delete_chunk_rejects_non_success_task(self):
        upload_tasks["task-2"] = {
            "status": "processing",
            "message": "processing",
            "file_name": "demo.pdf",
            "chunks_added": 0,
            "chunk_previews": [],
        }

        response = self.client.delete("/upload/task-2/chunks/1")

        self.assertEqual(response.status_code, 409)
        self.assertIn("after upload succeeds", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
