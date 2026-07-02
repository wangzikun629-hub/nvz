"""knowledge_case_summaries 表 CRUD。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from multi_agent.backed.app.infrastructure.database.database_pool import pool


class CaseSummaryRepository:
    def create(self, document_id: str, draft_json: dict) -> dict[str, Any]:
        summary_id = uuid.uuid4().hex
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO knowledge_case_summaries
                    (id, document_id, review_status, draft_json)
                    VALUES (%s, %s, 'pending_review', %s)
                    """,
                    (summary_id, document_id, json.dumps(draft_json, ensure_ascii=False)),
                )
            conn.commit()
        finally:
            conn.close()
        return self.get(summary_id)

    def get(self, summary_id: str) -> dict[str, Any] | None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, document_id, review_status, draft_json, reviewed_json,
                           reviewer_id, reviewed_at, review_comment, created_at, updated_at
                    FROM knowledge_case_summaries WHERE id = %s
                    """,
                    (summary_id,),
                )
                row = cur.fetchone()
                return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def get_by_document(self, document_id: str) -> dict[str, Any] | None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, document_id, review_status, draft_json, reviewed_json,
                           reviewer_id, reviewed_at, review_comment, created_at, updated_at
                    FROM knowledge_case_summaries WHERE document_id = %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (document_id,),
                )
                row = cur.fetchone()
                return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def save_reviewed_json(
        self, summary_id: str, reviewed_json: dict, reviewer_id: str | None = None
    ) -> dict[str, Any]:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE knowledge_case_summaries
                    SET reviewed_json = %s, reviewer_id = %s, review_status = 'needs_revision'
                    WHERE id = %s
                    """,
                    (json.dumps(reviewed_json, ensure_ascii=False), reviewer_id, summary_id),
                )
            conn.commit()
        finally:
            conn.close()
        return self.get(summary_id)

    def set_status(
        self,
        summary_id: str,
        status: str,
        reviewer_id: str | None = None,
        comment: str | None = None,
    ) -> None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE knowledge_case_summaries
                    SET review_status = %s, reviewer_id = COALESCE(%s, reviewer_id),
                        reviewed_at = %s, review_comment = COALESCE(%s, review_comment)
                    WHERE id = %s
                    """,
                    (status, reviewer_id, datetime.utcnow(), comment, summary_id),
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        def _parse_json(val):
            if val is None:
                return None
            if isinstance(val, (dict, list)):
                return val
            try:
                return json.loads(val)
            except Exception:
                return {}

        return {
            "id": row[0],
            "document_id": row[1],
            "review_status": row[2],
            "draft_json": _parse_json(row[3]) or {},
            "reviewed_json": _parse_json(row[4]),
            "reviewer_id": row[5],
            "reviewed_at": str(row[6]) if row[6] else None,
            "review_comment": row[7],
            "created_at": str(row[8]),
            "updated_at": str(row[9]),
        }
