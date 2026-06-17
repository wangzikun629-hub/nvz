import hashlib
import logging
from typing import List

import requests
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from pymilvus import DataType, MilvusClient

from multi_agent.backed.knowledge.config.settings import settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VectorStoreRepository:
    EMBEDDING_BATCH_LIMIT = 10
    PRIMARY_FIELD = "pk"
    TEXT_FIELD = "text"
    VECTOR_FIELD = "vector"

    def __init__(self):
        self.use_dashscope_multimodal_embedding = self._should_use_dashscope_multimodal_embedding()
        self.embedding = None
        if not self.use_dashscope_multimodal_embedding:
            self.embedding = OpenAIEmbeddings(
                model=settings.EMBEDDING_MODEL,
                api_key=settings.API_KEY,
                base_url=settings.BASE_URL,
                check_embedding_ctx_length=False,
                model_kwargs={"encoding_format": "float"},
                chunk_size=self.EMBEDDING_BATCH_LIMIT,
            )

        client_args = {
            "uri": settings.MILVUS_URI,
            "db_name": settings.MILVUS_DB_NAME,
        }
        if settings.MILVUS_TOKEN:
            client_args["token"] = settings.MILVUS_TOKEN
        self.client = MilvusClient(**client_args)
        self.embedding_dimension = self._resolve_embedding_dimension()
        logger.info(
            "vector_store init embedding_route=%s embedding_model=%s embedding_dim=%s milvus_uri=%s collection=%s",
            "dashscope_multimodal" if self.use_dashscope_multimodal_embedding else "openai_compatible",
            settings.EMBEDDING_MODEL,
            self.embedding_dimension or "auto",
            settings.MILVUS_URI,
            settings.MILVUS_COLLECTION,
        )
        if self.client.has_collection(settings.MILVUS_COLLECTION):
            self._ensure_index_and_load()

    @staticmethod
    def _should_use_dashscope_multimodal_embedding() -> bool:
        model = (settings.EMBEDDING_MODEL or "").lower()
        base_url = (settings.BASE_URL or "").lower()
        return "qwen3-vl-embedding" in model and "dashscope.aliyuncs.com" in base_url

    def _embed_documents_with_dashscope_multimodal(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        headers = {
            "Authorization": f"Bearer {settings.API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.EMBEDDING_MODEL,
            "input": {
                "contents": [{"text": text} for text in texts],
            },
        }
        if self.embedding_dimension > 0:
            payload["parameters"] = {"dimension": self.embedding_dimension}
        response = requests.post(
            settings.DASHSCOPE_MULTIMODAL_EMBEDDING_URL,
            headers=headers,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        embeddings = ((data.get("output") or {}).get("embeddings")) or []
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"DashScope multimodal embedding returned {len(embeddings)} vectors for {len(texts)} inputs"
            )
        logger.info(
            "embedding batch route=dashscope_multimodal model=%s inputs=%d dimension=%d",
            settings.EMBEDDING_MODEL,
            len(texts),
            len(embeddings[0]["embedding"]) if embeddings else 0,
        )
        return [item["embedding"] for item in embeddings]

    def _resolve_embedding_dimension(self) -> int:
        if settings.EMBEDDING_DIM > 0:
            return settings.EMBEDDING_DIM
        if self.client.has_collection(settings.MILVUS_COLLECTION):
            collection = self.client.describe_collection(settings.MILVUS_COLLECTION)
            fields = collection.get("fields") or []
            for field in fields:
                if field.get("name") == self.VECTOR_FIELD:
                    params = field.get("params") or {}
                    dim = params.get("dim")
                    if dim:
                        return int(dim)
        return 0

    @staticmethod
    def _build_chunk_id(document, index: int) -> str:
        source = document.metadata.get("source", "")
        owner_user_id = document.metadata.get("owner_user_id", "")
        file_id = document.metadata.get("file_id", "")
        category_id = document.metadata.get("category_id", "")
        content = document.page_content or ""
        return hashlib.md5(
            f"{source}\n{owner_user_id}\n{file_id}\n{category_id}\n{index}\n{content}".encode("utf-8")
        ).hexdigest()

    def _build_index_params(self):
        index_params = MilvusClient.prepare_index_params()
        index_params.add_index(
            field_name=self.VECTOR_FIELD,
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        return index_params

    def _ensure_collection(self, dimension: int) -> None:
        if self.client.has_collection(settings.MILVUS_COLLECTION):
            self._ensure_index_and_load()
            return

        schema = MilvusClient.create_schema(
            auto_id=False,
            enable_dynamic_field=True,
        )
        schema.add_field(
            field_name=self.TEXT_FIELD,
            datatype=DataType.VARCHAR,
            max_length=65_535,
        )
        schema.add_field(
            field_name=self.PRIMARY_FIELD,
            datatype=DataType.VARCHAR,
            max_length=65_535,
            is_primary=True,
        )
        schema.add_field(
            field_name=self.VECTOR_FIELD,
            datatype=DataType.FLOAT_VECTOR,
            dim=dimension,
        )
        schema.add_field(
            field_name=settings.MILVUS_PARTITION_KEY,
            datatype=DataType.VARCHAR,
            max_length=65_535,
            is_partition_key=True,
        )

        self.client.create_collection(
            collection_name=settings.MILVUS_COLLECTION,
            schema=schema,
            index_params=self._build_index_params(),
            consistency_level="Strong",
            num_partitions=settings.MILVUS_NUM_PARTITIONS,
        )
        self.client.load_collection(settings.MILVUS_COLLECTION)

    def _ensure_index_and_load(self) -> None:
        if not self.client.list_indexes(settings.MILVUS_COLLECTION):
            self.client.create_index(
                collection_name=settings.MILVUS_COLLECTION,
                index_params=self._build_index_params(),
            )
        self.client.load_collection(settings.MILVUS_COLLECTION)

    def _documents_to_entities(
        self,
        documents: list[Document],
        ids: list[str],
        embeddings: List[List[float]],
    ) -> list[dict]:
        entities = []
        for document, document_id, embedding in zip(documents, ids, embeddings):
            entity = dict(document.metadata)
            entity[self.PRIMARY_FIELD] = document_id
            entity[self.TEXT_FIELD] = document.page_content or ""
            entity[self.VECTOR_FIELD] = embedding
            entities.append(entity)
        return entities

    def add_documents(self, documents: list, batch_size: int = EMBEDDING_BATCH_LIMIT) -> int:
        """Upsert document chunks into Milvus in embedding-sized batches."""
        if not documents:
            return 0

        total_documents_chunks = len(documents)
        documents_chunks_added = 0
        try:
            for i in range(0, total_documents_chunks, batch_size):
                batch = documents[i : i + batch_size]
                ids = [
                    self._build_chunk_id(document, i + offset)
                    for offset, document in enumerate(batch)
                ]
                embeddings = self.embedd_documents(
                    [document.page_content or "" for document in batch]
                )
                self._ensure_collection(len(embeddings[0]))
                self.client.upsert(
                    collection_name=settings.MILVUS_COLLECTION,
                    data=self._documents_to_entities(batch, ids, embeddings),
                )
                documents_chunks_added += len(batch)
                logger.info(
                    "Stored document chunks in Milvus: %s/%s",
                    documents_chunks_added,
                    total_documents_chunks,
                )

            return documents_chunks_added
        except Exception:
            logger.exception("Failed to store document chunks in Milvus")
            raise

    def _get_existing_ids(self, ids: list[str]) -> list[str]:
        if not self.client.has_collection(settings.MILVUS_COLLECTION):
            return []
        quoted_ids = ", ".join(f'"{document_id}"' for document_id in ids)
        rows = self.client.query(
            collection_name=settings.MILVUS_COLLECTION,
            filter=f"{self.PRIMARY_FIELD} in [{quoted_ids}]",
            output_fields=[self.PRIMARY_FIELD],
        )
        return [row[self.PRIMARY_FIELD] for row in rows]

    def delete_documents_by_ids(self, ids: list[str]) -> int:
        if not ids:
            return 0

        existing_ids = self._get_existing_ids(ids)
        if not existing_ids:
            return 0

        try:
            self.client.delete(
                collection_name=settings.MILVUS_COLLECTION,
                ids=existing_ids,
            )
            logger.info("Deleted document chunks from Milvus: %s", len(existing_ids))
            return len(existing_ids)
        except Exception:
            logger.exception("Failed to delete document chunks from Milvus")
            raise

    def embedd_document(self, text: str) -> List[float]:
        if self.use_dashscope_multimodal_embedding:
            return self._embed_documents_with_dashscope_multimodal([text])[0]
        return self.embedding.embed_query(text)

    def embedd_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        if self.use_dashscope_multimodal_embedding:
            embeddings: List[List[float]] = []
            for i in range(0, len(texts), self.EMBEDDING_BATCH_LIMIT):
                batch = texts[i : i + self.EMBEDDING_BATCH_LIMIT]
                embeddings.extend(self._embed_documents_with_dashscope_multimodal(batch))
            return embeddings

        embeddings: List[List[float]] = []
        for i in range(0, len(texts), self.EMBEDDING_BATCH_LIMIT):
            batch = texts[i : i + self.EMBEDDING_BATCH_LIMIT]
            embeddings.extend(self.embedding.embed_documents(batch))
        return embeddings

    @staticmethod
    def _build_scope_expr(kb_scope: str | None) -> str:
        if not kb_scope or not kb_scope.strip():
            return ""
        escaped_scope = kb_scope.strip().replace("\\", "\\\\").replace('"', '\\"')
        return f'{settings.MILVUS_PARTITION_KEY} == "{escaped_scope}"'

    def _search_by_vector(
        self,
        embedding: List[float],
        top_k: int,
        kb_scope: str | None,
    ) -> List[tuple[Document, float]]:
        if not self.client.has_collection(settings.MILVUS_COLLECTION):
            return []

        results = self.client.search(
            collection_name=settings.MILVUS_COLLECTION,
            data=[embedding],
            anns_field=self.VECTOR_FIELD,
            filter=self._build_scope_expr(kb_scope),
            limit=top_k,
            output_fields=[
                self.TEXT_FIELD,
                self.PRIMARY_FIELD,
                "source",
                "title",
                "source_name",
                "path",
                settings.MILVUS_PARTITION_KEY,
                "original_extension",
                "content_format",
            ],
            search_params={
                "metric_type": "COSINE",
                "params": {"ef": 64},
            },
        )

        documents_with_score = []
        for result in results[0]:
            entity = dict(result.get("entity") or {})
            page_content = entity.pop(self.TEXT_FIELD, "")
            document_id = str(entity.pop(self.PRIMARY_FIELD, result.get("id", "")))
            entity.pop(self.VECTOR_FIELD, None)
            documents_with_score.append(
                (
                    Document(
                        page_content=page_content,
                        metadata=entity,
                        id=document_id,
                    ),
                    float(result.get("distance", 0.0)),
                )
            )
        return documents_with_score

    def search_similarity_with_score(
        self,
        user_question: str,
        top_k: int = 5,
        kb_scope: str | None = None,
    ) -> List[tuple[Document, float]]:
        return self._search_by_vector(
            self.embedd_document(user_question),
            top_k=top_k,
            kb_scope=kb_scope,
        )

    def search_similarity_by_vector_with_score(
        self,
        embedding: List[float],
        top_k: int = 5,
        kb_scope: str | None = None,
    ) -> List[tuple[Document, float]]:
        return self._search_by_vector(
            embedding,
            top_k=top_k,
            kb_scope=kb_scope,
        )
