import copy
import json
import os
from datetime import datetime
from typing import Any

from multi_agent.backed.app.infrastructure.database.database_pool import pool
from multi_agent.backed.knowledge.config.settings import settings

# ── 固定 13 个业务分区 ──────────────────────────────────────────────────────────
FIXED_PARTITIONS = [
    {"id": "cuttag",    "name": "CUT&Tag",    "schema_type": "case_report",    "sort_order": 1},
    {"id": "atac",      "name": "ATAC-seq",   "schema_type": "case_report",    "sort_order": 2},
    {"id": "hic",       "name": "Hi-C",       "schema_type": "case_report",    "sort_order": 3},
    {"id": "foodie",    "name": "FOODIE",     "schema_type": "case_report",    "sort_order": 4},
    {"id": "rnaseq",    "name": "RNA-seq",    "schema_type": "case_report",    "sort_order": 5},
    {"id": "smallrna",  "name": "smallRNA",   "schema_type": "case_report",    "sort_order": 6},
    {"id": "dnaseq",    "name": "DNA-seq",    "schema_type": "case_report",    "sort_order": 7},
    {"id": "capture",   "name": "Capture-QC", "schema_type": "case_report",    "sort_order": 8},
    {"id": "mngs",      "name": "mNGS-NT",    "schema_type": "case_report",    "sort_order": 9},
    {"id": "primer",    "name": "引物探针",    "schema_type": "case_report",    "sort_order": 10},
    {"id": "reagent",    "name": "试剂说明书",  "schema_type": "reagent_manual", "sort_order": 11},
    {"id": "general",    "name": "通用知识",   "schema_type": "general_doc",    "sort_order": 12},
    {"id": "reference",  "name": "参考文件",   "schema_type": "general_doc",    "sort_order": 13},
    {"id": "kanban_rd",  "name": "研发看板",   "schema_type": "general_doc",    "sort_order": 20},
    {"id": "kanban_cs",  "name": "客服看板",   "schema_type": "case_report",    "sort_order": 21},
]
VALID_PARTITION_IDS: set[str] = {p["id"] for p in FIXED_PARTITIONS}
_PARTITION_MAP: dict[str, dict] = {p["id"]: p for p in FIXED_PARTITIONS}


def _utcnow() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


class CatalogRepository:
    # ── 分区表 ──────────────────────────────────────────────────────────────────
    _CREATE_PARTITIONS_SQL = """
    CREATE TABLE IF NOT EXISTS knowledge_partitions (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        schema_type VARCHAR(50) NOT NULL,
        sort_order INT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    # ── 原始文件表（含 partition_id） ────────────────────────────────────────────
    _CREATE_CATEGORIES_SQL = """
    CREATE TABLE IF NOT EXISTS knowledge_categories (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        owner_user_id VARCHAR(64) NOT NULL,
        name VARCHAR(128) NOT NULL,
        description VARCHAR(255) NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_owner_name (owner_user_id, name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    _CREATE_FILES_SQL = """
    CREATE TABLE IF NOT EXISTS knowledge_files (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        owner_user_id VARCHAR(64) NOT NULL,
        partition_id VARCHAR(64) NOT NULL DEFAULT 'general',
        category_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
        category_name VARCHAR(128) NOT NULL DEFAULT '',
        file_name VARCHAR(255) NOT NULL,
        kb_scope VARCHAR(64) NOT NULL DEFAULT 'general',
        original_extension VARCHAR(32) NOT NULL DEFAULT '',
        chunk_count INT NOT NULL DEFAULT 0,
        status VARCHAR(32) NOT NULL DEFAULT 'processing',
        message VARCHAR(255) NOT NULL DEFAULT '',
        upload_task_id VARCHAR(64) NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_owner_partition (owner_user_id, partition_id),
        KEY idx_owner_category (owner_user_id, category_id),
        KEY idx_owner_status (owner_user_id, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    _CREATE_CHUNK_MAPS_SQL = """
    CREATE TABLE IF NOT EXISTS knowledge_chunk_maps (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        file_id BIGINT UNSIGNED NOT NULL,
        owner_user_id VARCHAR(64) NOT NULL,
        partition_id VARCHAR(64) NOT NULL DEFAULT 'general',
        category_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
        category_name VARCHAR(128) NOT NULL DEFAULT '',
        chunk_index INT NOT NULL,
        chunk_id VARCHAR(64) NOT NULL,
        content MEDIUMTEXT NOT NULL,
        preview TEXT NOT NULL,
        length INT NOT NULL DEFAULT 0,
        metadata_json MEDIUMTEXT NOT NULL,
        is_deleted TINYINT(1) NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_chunk_id (chunk_id),
        UNIQUE KEY uk_file_chunk (file_id, chunk_index),
        KEY idx_file_deleted (file_id, is_deleted),
        KEY idx_owner_partition (owner_user_id, partition_id),
        KEY idx_owner_category (owner_user_id, category_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    # ── 智能解析三张表 ──────────────────────────────────────────────────────────
    _CREATE_SOURCE_DOCUMENTS_SQL = """
    CREATE TABLE IF NOT EXISTS knowledge_source_documents (
        id VARCHAR(64) PRIMARY KEY,
        partition_id VARCHAR(64) NOT NULL,
        schema_type VARCHAR(50) NOT NULL,
        file_name VARCHAR(255) NOT NULL,
        file_path VARCHAR(500) NOT NULL,
        file_type VARCHAR(20) NOT NULL,
        page_count INT NULL,
        page_image_paths JSON NULL,
        parse_status VARCHAR(50) NOT NULL DEFAULT 'uploaded',
        parse_error TEXT NULL,
        uploaded_by VARCHAR(64) NULL,
        uploaded_at DATETIME NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_partition_status (partition_id, parse_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    _CREATE_CASE_SUMMARIES_SQL = """
    CREATE TABLE IF NOT EXISTS knowledge_case_summaries (
        id VARCHAR(64) PRIMARY KEY,
        document_id VARCHAR(64) NOT NULL,
        review_status VARCHAR(50) NOT NULL DEFAULT 'pending_review',
        draft_json JSON NOT NULL,
        reviewed_json JSON NULL,
        reviewer_id VARCHAR(64) NULL,
        reviewed_at DATETIME NULL,
        review_comment TEXT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_document (document_id),
        INDEX idx_review_status (review_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    _CREATE_PARSER_CHUNKS_SQL = """
    CREATE TABLE IF NOT EXISTS knowledge_parser_chunks (
        id VARCHAR(64) PRIMARY KEY,
        document_id VARCHAR(64) NOT NULL,
        summary_id VARCHAR(64) NOT NULL,
        partition_id VARCHAR(64) NOT NULL,
        chunk_type VARCHAR(80) NOT NULL,
        chunk_text TEXT NOT NULL,
        metadata_json JSON NOT NULL,
        vector_status VARCHAR(50) NOT NULL DEFAULT 'pending',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_document (document_id),
        INDEX idx_partition (partition_id),
        INDEX idx_vector_status (vector_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    def __init__(self):
        self._legacy_json_path = settings.CATALOG_DATA_FILE

    # ── 表初始化 ────────────────────────────────────────────────────────────────
    def ensure_tables(self) -> None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                # 创建分区表
                cur.execute(self._CREATE_PARTITIONS_SQL)
                # 原有表（含新字段的 DDL，适用于全新安装）
                cur.execute(self._CREATE_CATEGORIES_SQL)
                cur.execute(self._CREATE_FILES_SQL)
                cur.execute(self._CREATE_CHUNK_MAPS_SQL)
                # 智能解析表
                cur.execute(self._CREATE_SOURCE_DOCUMENTS_SQL)
                cur.execute(self._CREATE_CASE_SUMMARIES_SQL)
                cur.execute(self._CREATE_PARSER_CHUNKS_SQL)
            conn.commit()

            # 存量表迁移：按需加列（幂等）
            conn2 = pool.connection()
            try:
                self._add_column_if_not_exists(
                    conn2, "knowledge_files", "partition_id",
                    "partition_id VARCHAR(64) NOT NULL DEFAULT 'general' AFTER owner_user_id"
                )
                self._add_column_if_not_exists(
                    conn2, "knowledge_chunk_maps", "partition_id",
                    "partition_id VARCHAR(64) NOT NULL DEFAULT 'general' AFTER owner_user_id"
                )
                conn2.commit()
            finally:
                conn2.close()

            # 初始化 13 个固定分区（幂等）
            self._seed_partitions()
        finally:
            conn.close()

    def _add_column_if_not_exists(self, conn, table: str, column: str, column_def: str) -> None:
        """为已存在的表添加列（幂等，兼容各 MySQL 版本）。"""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(1) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                (table, column),
            )
            if cur.fetchone()[0] == 0:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")

    def _seed_partitions(self) -> None:
        """幂等写入 13 个固定分区。"""
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                for p in FIXED_PARTITIONS:
                    cur.execute(
                        """
                        INSERT IGNORE INTO knowledge_partitions (id, name, schema_type, sort_order)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (p["id"], p["name"], p["schema_type"], p["sort_order"]),
                    )
            conn.commit()
        finally:
            conn.close()

    # ── 分区查询 ────────────────────────────────────────────────────────────────
    def list_partitions(self, owner_user_id: str) -> list[dict[str, Any]]:
        """返回固定 13 个分区，附加每个分区下的文件数。"""
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT partition_id, COUNT(1)
                    FROM knowledge_files
                    WHERE owner_user_id = %s AND status <> 'deleted'
                    GROUP BY partition_id
                    """,
                    (owner_user_id,),
                )
                counts = {row[0]: int(row[1]) for row in cur.fetchall()}
        finally:
            conn.close()

        result = []
        for p in FIXED_PARTITIONS:
            result.append({
                "id": p["id"],
                "name": p["name"],
                "schema_type": p["schema_type"],
                "sort_order": p["sort_order"],
                "file_count": counts.get(p["id"], 0),
            })
        return result

    def get_partition(self, partition_id: str) -> dict[str, Any] | None:
        return dict(_PARTITION_MAP[partition_id]) if partition_id in _PARTITION_MAP else None

    # ── 历史分类兼容（保留，只读） ────────────────────────────────────────────────
    def migrate_from_legacy_json(self) -> bool:
        if not os.path.exists(self._legacy_json_path):
            return False
        if self._count_rows("knowledge_categories") > 0:
            return False

        with open(self._legacy_json_path, "r", encoding="utf-8") as file:
            legacy = json.load(file)

        categories = legacy.get("categories") or []
        files = legacy.get("files") or []
        chunk_maps = legacy.get("chunk_maps") or []
        if not categories and not files and not chunk_maps:
            return False

        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                for item in categories:
                    cur.execute(
                        """
                        INSERT INTO knowledge_categories
                        (id, owner_user_id, name, description, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            int(item["id"]),
                            item["owner_user_id"],
                            item["name"],
                            item.get("description", ""),
                            self._parse_legacy_timestamp(item.get("created_at")),
                            self._parse_legacy_timestamp(item.get("updated_at")),
                        ),
                    )
                for item in files:
                    cur.execute(
                        """
                        INSERT INTO knowledge_files
                        (id, owner_user_id, partition_id, category_id, category_name, file_name, kb_scope,
                         original_extension, chunk_count, status, message, upload_task_id,
                         created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            int(item["id"]),
                            item["owner_user_id"],
                            item.get("partition_id", "general"),
                            int(item.get("category_id", 0)),
                            item.get("category_name", ""),
                            item["file_name"],
                            item.get("kb_scope", settings.DEFAULT_KB_SCOPE),
                            item.get("original_extension", ""),
                            int(item.get("chunk_count", 0)),
                            item.get("status", "processing"),
                            item.get("message", ""),
                            item.get("upload_task_id", ""),
                            self._parse_legacy_timestamp(item.get("created_at")),
                            self._parse_legacy_timestamp(item.get("updated_at")),
                        ),
                    )
                for item in chunk_maps:
                    cur.execute(
                        """
                        INSERT INTO knowledge_chunk_maps
                        (id, file_id, owner_user_id, partition_id, category_id, category_name, chunk_index,
                         chunk_id, content, preview, length, metadata_json, is_deleted, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            int(item["id"]),
                            int(item["file_id"]),
                            item["owner_user_id"],
                            item.get("partition_id", "general"),
                            int(item.get("category_id", 0)),
                            item.get("category_name", ""),
                            int(item["chunk_index"]),
                            item["chunk_id"],
                            item.get("content", ""),
                            item.get("preview", ""),
                            int(item.get("length", 0)),
                            json.dumps(item.get("metadata") or {}, ensure_ascii=False),
                            1 if item.get("is_deleted", False) else 0,
                            self._parse_legacy_timestamp(item.get("created_at")),
                        ),
                    )
            conn.commit()
            return True
        finally:
            conn.close()

    def _count_rows(self, table_name: str) -> int:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(1) FROM {table_name}")
                row = cur.fetchone()
                return int(row[0] if row else 0)
        finally:
            conn.close()

    @staticmethod
    def _parse_legacy_timestamp(value: str | None):
        if not value:
            return datetime.utcnow()
        normalized = value.rstrip("Z")
        return datetime.fromisoformat(normalized)

    # ── 行转 dict ───────────────────────────────────────────────────────────────
    @staticmethod
    def _category_row_to_dict(row) -> dict[str, Any]:
        return {
            "id": int(row[0]),
            "owner_user_id": row[1],
            "name": row[2],
            "description": row[3] or "",
            "created_at": str(row[4]),
            "updated_at": str(row[5]),
        }

    @staticmethod
    def _file_row_to_dict(row) -> dict[str, Any]:
        # 列顺序：id(0) owner_user_id(1) category_id(2) category_name(3) file_name(4) kb_scope(5)
        #          original_extension(6) chunk_count(7) status(8) message(9) upload_task_id(10)
        #          created_at(11) updated_at(12) partition_id(13)
        return {
            "id": int(row[0]),
            "owner_user_id": row[1],
            "category_id": int(row[2]),
            "category_name": row[3] or "",
            "file_name": row[4],
            "kb_scope": row[5],
            "original_extension": row[6] or "",
            "chunk_count": int(row[7] or 0),
            "status": row[8],
            "message": row[9] or "",
            "upload_task_id": row[10] or "",
            "created_at": str(row[11]),
            "updated_at": str(row[12]),
            "partition_id": row[13] or "general",
        }

    _FILE_SELECT = """
        SELECT id, owner_user_id, category_id, category_name, file_name, kb_scope,
               original_extension, chunk_count, status, message, upload_task_id,
               created_at, updated_at, COALESCE(partition_id, 'general')
        FROM knowledge_files
    """

    @staticmethod
    def _chunk_row_to_dict(row) -> dict[str, Any]:
        return {
            "id": int(row[0]),
            "file_id": int(row[1]),
            "owner_user_id": row[2],
            "category_id": int(row[3]),
            "category_name": row[4],
            "chunk_index": int(row[5]),
            "chunk_id": row[6],
            "content": row[7] or "",
            "preview": row[8] or "",
            "length": int(row[9] or 0),
            "metadata": json.loads(row[10] or "{}"),
            "is_deleted": bool(row[11]),
            "created_at": str(row[12]),
        }

    # ── 分类 CRUD（保留，兼容历史数据） ─────────────────────────────────────────
    def create_category(self, owner_user_id: str, name: str, description: str = "") -> dict[str, Any]:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("Category name is required")

        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM knowledge_categories WHERE owner_user_id = %s AND name = %s",
                    (owner_user_id, normalized_name),
                )
                if cur.fetchone():
                    raise ValueError("Category name already exists")

                cur.execute(
                    "INSERT INTO knowledge_categories (owner_user_id, name, description) VALUES (%s, %s, %s)",
                    (owner_user_id, normalized_name, (description or "").strip()),
                )
                category_id = cur.lastrowid
                cur.execute(
                    "SELECT id, owner_user_id, name, description, created_at, updated_at "
                    "FROM knowledge_categories WHERE id = %s",
                    (category_id,),
                )
                row = cur.fetchone()
            conn.commit()
            category = self._category_row_to_dict(row)
            category["file_count"] = 0
            return category
        finally:
            conn.close()

    def list_categories(self, owner_user_id: str) -> list[dict[str, Any]]:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.id, c.owner_user_id, c.name, c.description, c.created_at, c.updated_at,
                           COUNT(f.id) AS file_count
                    FROM knowledge_categories c
                    LEFT JOIN knowledge_files f
                      ON f.category_id = c.id AND f.owner_user_id = c.owner_user_id AND f.status <> 'deleted'
                    WHERE c.owner_user_id = %s
                    GROUP BY c.id, c.owner_user_id, c.name, c.description, c.created_at, c.updated_at
                    ORDER BY c.name ASC, c.id ASC
                    """,
                    (owner_user_id,),
                )
                rows = cur.fetchall()
            result = []
            for row in rows:
                item = self._category_row_to_dict(row[:6])
                item["file_count"] = int(row[6] or 0)
                result.append(item)
            return result
        finally:
            conn.close()

    def get_category(self, owner_user_id: str, category_id: int) -> dict[str, Any] | None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, owner_user_id, name, description, created_at, updated_at "
                    "FROM knowledge_categories WHERE owner_user_id = %s AND id = %s",
                    (owner_user_id, int(category_id)),
                )
                row = cur.fetchone()
                return self._category_row_to_dict(row) if row else None
        finally:
            conn.close()

    def update_category(self, owner_user_id: str, category_id: int, name: str, description: str = "") -> dict[str, Any]:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("Category name is required")

        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM knowledge_categories WHERE owner_user_id = %s AND id = %s",
                    (owner_user_id, int(category_id)),
                )
                if not cur.fetchone():
                    raise ValueError("Category not found")

                cur.execute(
                    "SELECT id FROM knowledge_categories WHERE owner_user_id = %s AND id <> %s AND name = %s",
                    (owner_user_id, int(category_id), normalized_name),
                )
                if cur.fetchone():
                    raise ValueError("Category name already exists")

                cur.execute(
                    "UPDATE knowledge_categories SET name = %s, description = %s WHERE owner_user_id = %s AND id = %s",
                    (normalized_name, (description or "").strip(), owner_user_id, int(category_id)),
                )
                cur.execute(
                    "UPDATE knowledge_files SET category_name = %s WHERE owner_user_id = %s AND category_id = %s AND status <> 'deleted'",
                    (normalized_name, owner_user_id, int(category_id)),
                )
                cur.execute(
                    "SELECT id, metadata_json FROM knowledge_chunk_maps WHERE owner_user_id = %s AND category_id = %s",
                    (owner_user_id, int(category_id)),
                )
                for chunk_row in cur.fetchall():
                    metadata = json.loads(chunk_row[1] or "{}")
                    metadata["category_name"] = normalized_name
                    metadata["category_id"] = str(category_id)
                    cur.execute(
                        "UPDATE knowledge_chunk_maps SET category_name = %s, metadata_json = %s WHERE id = %s",
                        (normalized_name, json.dumps(metadata, ensure_ascii=False), int(chunk_row[0])),
                    )
                cur.execute(
                    "SELECT id, owner_user_id, name, description, created_at, updated_at FROM knowledge_categories WHERE id = %s",
                    (int(category_id),),
                )
                row = cur.fetchone()
            conn.commit()
            return self._category_row_to_dict(row)
        finally:
            conn.close()

    def delete_category(self, owner_user_id: str, category_id: int) -> None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(1) FROM knowledge_files WHERE owner_user_id = %s AND category_id = %s AND status <> 'deleted'",
                    (owner_user_id, int(category_id)),
                )
                if cur.fetchone()[0]:
                    raise ValueError("Category still contains files")
                cur.execute(
                    "DELETE FROM knowledge_categories WHERE owner_user_id = %s AND id = %s",
                    (owner_user_id, int(category_id)),
                )
                if cur.rowcount == 0:
                    raise ValueError("Category not found")
            conn.commit()
        finally:
            conn.close()

    # ── 文件 CRUD ────────────────────────────────────────────────────────────────
    def create_file_record(
        self,
        owner_user_id: str,
        category_id: int,
        category_name: str,
        file_name: str,
        kb_scope: str,
        upload_task_id: str,
        original_extension: str,
        partition_id: str = "general",
    ) -> dict[str, Any]:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO knowledge_files
                    (owner_user_id, partition_id, category_id, category_name, file_name, kb_scope,
                     original_extension, status, message, upload_task_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'processing', 'File uploaded. Processing in background.', %s)
                    """,
                    (
                        owner_user_id,
                        partition_id,
                        int(category_id),
                        category_name,
                        file_name,
                        kb_scope,
                        original_extension,
                        upload_task_id,
                    ),
                )
                file_id = cur.lastrowid
                cur.execute(self._FILE_SELECT + " WHERE id = %s", (file_id,))
                row = cur.fetchone()
            conn.commit()
            return self._file_row_to_dict(row)
        finally:
            conn.close()

    def update_file_record(
        self,
        file_id: int,
        *,
        status: str,
        message: str,
        chunks_added: int,
        chunk_previews: list[dict[str, Any]],
    ) -> dict[str, Any]:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE knowledge_files SET status = %s, message = %s, chunk_count = %s WHERE id = %s",
                    (status, message, int(chunks_added), int(file_id)),
                )
                if cur.rowcount == 0:
                    raise ValueError("File record not found")

                cur.execute("DELETE FROM knowledge_chunk_maps WHERE file_id = %s", (int(file_id),))
                if chunk_previews:
                    cur.execute(
                        "SELECT owner_user_id, category_id, category_name, COALESCE(partition_id, 'general') "
                        "FROM knowledge_files WHERE id = %s",
                        (int(file_id),),
                    )
                    file_meta = cur.fetchone()
                    for chunk in chunk_previews:
                        cur.execute(
                            """
                            INSERT INTO knowledge_chunk_maps
                            (file_id, owner_user_id, partition_id, category_id, category_name,
                             chunk_index, chunk_id, content, preview, length, metadata_json, is_deleted)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                int(file_id),
                                file_meta[0],
                                file_meta[3],           # partition_id
                                int(file_meta[1]),
                                file_meta[2],
                                int(chunk["chunk_index"]),
                                chunk["chunk_id"],
                                chunk.get("content", ""),
                                chunk.get("preview", ""),
                                int(chunk.get("length", 0)),
                                json.dumps(chunk.get("metadata") or {}, ensure_ascii=False),
                                1 if chunk.get("deleted", False) else 0,
                            ),
                        )
                cur.execute(self._FILE_SELECT + " WHERE id = %s", (int(file_id),))
                row = cur.fetchone()
            conn.commit()
            return self._file_row_to_dict(row)
        finally:
            conn.close()

    def list_files(
        self,
        owner_user_id: str,
        category_id: int | None = None,
        partition_id: str | None = None,
    ) -> list[dict[str, Any]]:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                sql = self._FILE_SELECT + " WHERE owner_user_id = %s AND status <> 'deleted'"
                params: list[Any] = [owner_user_id]
                if partition_id is not None:
                    sql += " AND COALESCE(partition_id, 'general') = %s"
                    params.append(partition_id)
                elif category_id is not None:
                    sql += " AND category_id = %s"
                    params.append(int(category_id))
                sql += " ORDER BY created_at DESC, id DESC"
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
            return [self._file_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def get_file_by_task_id(self, upload_task_id: str) -> dict[str, Any] | None:
        if not upload_task_id:
            return None
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(self._FILE_SELECT + " WHERE upload_task_id = %s LIMIT 1", (upload_task_id,))
                row = cur.fetchone()
                return self._file_row_to_dict(row) if row else None
        finally:
            conn.close()

    def get_file(self, owner_user_id: str, file_id: int) -> dict[str, Any] | None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    self._FILE_SELECT + " WHERE owner_user_id = %s AND id = %s AND status <> 'deleted'",
                    (owner_user_id, int(file_id)),
                )
                row = cur.fetchone()
                return self._file_row_to_dict(row) if row else None
        finally:
            conn.close()

    def list_chunks_for_file(self, owner_user_id: str, file_id: int) -> list[dict[str, Any]]:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM knowledge_files WHERE owner_user_id = %s AND id = %s AND status <> 'deleted'",
                    (owner_user_id, int(file_id)),
                )
                if not cur.fetchone():
                    raise ValueError("File not found")

                cur.execute(
                    """
                    SELECT id, file_id, owner_user_id, category_id, category_name, chunk_index, chunk_id,
                           content, preview, length, metadata_json, is_deleted, created_at
                    FROM knowledge_chunk_maps
                    WHERE file_id = %s
                    ORDER BY chunk_index ASC
                    """,
                    (int(file_id),),
                )
                rows = cur.fetchall()
            return [self._chunk_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def move_file_to_category(
        self,
        owner_user_id: str,
        file_id: int,
        target_category_id: int,
        target_category_name: str,
    ) -> dict[str, Any]:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE knowledge_files SET category_id = %s, category_name = %s "
                    "WHERE owner_user_id = %s AND id = %s AND status <> 'deleted'",
                    (int(target_category_id), target_category_name, owner_user_id, int(file_id)),
                )
                if cur.rowcount == 0:
                    raise ValueError("File not found")
                cur.execute(
                    "SELECT id, file_name FROM knowledge_files WHERE owner_user_id = %s AND id = %s",
                    (owner_user_id, int(file_id)),
                )
                file_row = cur.fetchone()
                file_id_value = int(file_row[0])
                file_name = file_row[1]
                cur.execute(
                    "SELECT id, metadata_json FROM knowledge_chunk_maps WHERE file_id = %s",
                    (file_id_value,),
                )
                for chunk_row in cur.fetchall():
                    metadata = json.loads(chunk_row[1] or "{}")
                    metadata["category_id"] = str(target_category_id)
                    metadata["category_name"] = target_category_name
                    metadata["file_name"] = file_name
                    cur.execute(
                        "UPDATE knowledge_chunk_maps SET category_id = %s, category_name = %s, metadata_json = %s WHERE id = %s",
                        (int(target_category_id), target_category_name, json.dumps(metadata, ensure_ascii=False), int(chunk_row[0])),
                    )
                cur.execute(self._FILE_SELECT + " WHERE id = %s", (file_id_value,))
                row = cur.fetchone()
            conn.commit()
            return self._file_row_to_dict(row)
        finally:
            conn.close()

    def mark_chunk_deleted(self, chunk_id: str) -> None:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, file_id FROM knowledge_chunk_maps WHERE chunk_id = %s", (chunk_id,))
                chunk_row = cur.fetchone()
                if not chunk_row:
                    return
                cur.execute("UPDATE knowledge_chunk_maps SET is_deleted = 1 WHERE id = %s", (int(chunk_row[0]),))
                cur.execute(
                    "SELECT COUNT(1) FROM knowledge_chunk_maps WHERE file_id = %s AND is_deleted = 0",
                    (int(chunk_row[1]),),
                )
                active_count = cur.fetchone()[0]
                cur.execute(
                    "UPDATE knowledge_files SET chunk_count = %s WHERE id = %s",
                    (int(active_count), int(chunk_row[1])),
                )
            conn.commit()
        finally:
            conn.close()

    def delete_file(self, owner_user_id: str, file_id: int) -> dict[str, Any]:
        conn = pool.connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    self._FILE_SELECT + " WHERE owner_user_id = %s AND id = %s AND status <> 'deleted'",
                    (owner_user_id, int(file_id)),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("File not found")
                cur.execute(
                    "SELECT chunk_id FROM knowledge_chunk_maps WHERE file_id = %s AND is_deleted = 0",
                    (int(file_id),),
                )
                chunk_ids = [item[0] for item in cur.fetchall()]
                cur.execute("UPDATE knowledge_files SET status = 'deleted' WHERE id = %s", (int(file_id),))
            conn.commit()
            return {"file": self._file_row_to_dict(row), "chunk_ids": chunk_ids}
        finally:
            conn.close()
