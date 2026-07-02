"""knowledge_parser_chunks 表 CRUD。"""
from __future__ import annotations

import json
import uuid
from typing import Any

from multi_agent.backed.app.infrastructure.database.database_pool import pool


class ParserChunkRepository:
    def bulk_create(self, chunks: list[dict]) -> list[dict[str, Any]]:
        """批量写入 chunks，返回写入的记录。"""
        if not chunks:
            return []
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                for chunk in chunks:
                    chunk_id = uuid.uuid4().hex
                    cur.execute(
                        """
                        INSERT INTO knowledge_parser_chunks
                        (id, document_id, summary_id, partition_id, chunk_type,
                         chunk_text, metadata_json, vector_status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (
                            chunk_id,
                            chunk["document_id"],
                            chunk["summary_id"],
                            chunk["partition_id"],
                            chunk["chunk_type"],
                            chunk["chunk_text"],
                            json.dumps(chunk.get("metadata_json") or {}, ensure_ascii=False),
                        ),
                    )
                    chunk["id"] = chunk_id
            conn.commit()
        finally:
            conn.close()
        return chunks

    def update_vector_status(self, chunk_ids: list[str], status: str) -> None:
        if not chunk_ids:
            return
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(chunk_ids))
                cur.execute(
                    f"UPDATE knowledge_parser_chunks SET vector_status = %s WHERE id IN ({placeholders})",
                    (status, *chunk_ids),
                )
            conn.commit()
        finally:
            conn.close()

    def list_by_document(self, document_id: str) -> list[dict[str, Any]]:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, document_id, summary_id, partition_id, chunk_type,
                           chunk_text, metadata_json, vector_status, created_at
                    FROM knowledge_parser_chunks WHERE document_id = %s ORDER BY created_at
                    """,
                    (document_id,),
                )
                rows = cur.fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        meta = row[6]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        return {
            "id": row[0],
            "document_id": row[1],
            "summary_id": row[2],
            "partition_id": row[3],
            "chunk_type": row[4],
            "chunk_text": row[5],
            "metadata_json": meta or {},
            "vector_status": row[7],
            "created_at": str(row[8]),
        }
