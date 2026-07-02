"""knowledge_source_documents 表 CRUD。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from multi_agent.backed.app.infrastructure.database.database_pool import pool


class SourceDocumentRepository:
    def create(
        self,
        document_id: str,
        partition_id: str,
        schema_type: str,
        file_name: str,
        file_path: str,
        file_type: str,
        uploaded_by: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.utcnow()
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO knowledge_source_documents
                    (id, partition_id, schema_type, file_name, file_path, file_type,
                     parse_status, uploaded_by, uploaded_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'uploaded', %s, %s)
                    """,
                    (document_id, partition_id, schema_type, file_name, file_path,
                     file_type, uploaded_by, now),
                )
            conn.commit()
        finally:
            conn.close()
        return self.get(document_id)

    def get(self, document_id: str) -> dict[str, Any] | None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, partition_id, schema_type, file_name, file_path, file_type,
                           page_count, page_image_paths, parse_status, parse_error,
                           uploaded_by, uploaded_at, created_at, updated_at
                    FROM knowledge_source_documents WHERE id = %s
                    """,
                    (document_id,),
                )
                row = cur.fetchone()
                return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def list(self, uploaded_by: str | None = None, partition_id: str | None = None) -> list[dict[str, Any]]:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                sql = """
                    SELECT id, partition_id, schema_type, file_name, file_path, file_type,
                           page_count, page_image_paths, parse_status, parse_error,
                           uploaded_by, uploaded_at, created_at, updated_at
                    FROM knowledge_source_documents WHERE 1=1
                """
                params: list[Any] = []
                if uploaded_by:
                    sql += " AND uploaded_by = %s"
                    params.append(uploaded_by)
                if partition_id:
                    sql += " AND partition_id = %s"
                    params.append(partition_id)
                sql += " ORDER BY created_at DESC"
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def update_status(
        self,
        document_id: str,
        status: str,
        parse_error: str | None = None,
    ) -> None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE knowledge_source_documents SET parse_status = %s, parse_error = %s WHERE id = %s",
                    (status, parse_error, document_id),
                )
            conn.commit()
        finally:
            conn.close()

    def set_page_images(self, document_id: str, page_count: int, image_paths: list[str]) -> None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE knowledge_source_documents
                    SET page_count = %s, page_image_paths = %s, parse_status = 'converted'
                    WHERE id = %s
                    """,
                    (page_count, json.dumps(image_paths, ensure_ascii=False), document_id),
                )
            conn.commit()
        finally:
            conn.close()

    def delete(self, document_id: str) -> None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM knowledge_source_documents WHERE id = %s",
                    (document_id,),
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        image_paths = row[7]
        if isinstance(image_paths, str):
            try:
                image_paths = json.loads(image_paths)
            except Exception:
                image_paths = []
        return {
            "id": row[0],
            "partition_id": row[1],
            "schema_type": row[2],
            "file_name": row[3],
            "file_path": row[4],
            "file_type": row[5],
            "page_count": row[6],
            "page_image_paths": image_paths or [],
            "parse_status": row[8],
            "parse_error": row[9],
            "uploaded_by": row[10],
            "uploaded_at": str(row[11]),
            "created_at": str(row[12]),
            "updated_at": str(row[13]),
        }
