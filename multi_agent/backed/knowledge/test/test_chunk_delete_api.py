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
sys.modules.setdefault("multi_agent.backed.knowledge.services.query_service", query_module)

retrieval_module = types.ModuleType("multi_agent.backed.knowledge.services.retrieval_service")


class DummyRetrievalService:
    def retrieval(self, user_question):
        return []


retrieval_module.RetrievalService = DummyRetrievalService
sys.modules.setdefault("multi_agent.backed.knowledge.services.retrieval_service", retrieval_module)

from multi_agent.backed.knowledge.api.routers import router, upload_tasks, ingestion_processor


class ChunkDeleteApiTests(unittest.TestCase):
    def setUp(self):
        upload_tasks.clear()
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)

    def test_delete_chunk_marks_it_deleted_and_updates_count(self):
        deleted_ids = []
        ingestion_processor.vector_store.delete_documents_by_ids = lambda ids: deleted_ids.extend(ids) or len(ids)
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
