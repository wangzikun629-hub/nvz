"""approved → 生成语义化 RAG chunks → 写入 Milvus。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from multi_agent.backed.knowledge.repositories.catalog_repository import _PARTITION_MAP

logger = logging.getLogger(__name__)

# chunk_type → 中文标签
_CHUNK_TYPE_LABELS: dict[str, str] = {
    "case_background": "案例背景",
    "key_observations": "关键现象",
    "analysis_process": "分析过程",
    "conclusion_and_recs": "结论与建议",
    "limitations": "适用限制",
    "product_overview": "产品概述",
    "usage_protocol": "使用步骤",
    "precautions": "注意事项",
    "summary_and_points": "摘要与要点",
    "applicable_scenarios": "适用场景",
}


class ChunkGenerationService:
    def generate_and_index_bg(
        self,
        summary_id: str,
        summary: dict,
        doc: dict,
        doc_repo,
        summary_repo,
        chunk_repo,
    ) -> None:
        """后台任务：根据 reviewed_json 生成 chunks，写入 Milvus，更新状态。"""
        try:
            reviewed = summary.get("reviewed_json") or summary.get("draft_json") or {}
            schema_type = doc.get("schema_type", "case_report") if doc else "case_report"
            partition_id = doc.get("partition_id", "general") if doc else "general"
            file_name = doc.get("file_name", "") if doc else ""
            document_id = summary.get("document_id", "")
            approved_by = summary.get("reviewer_id")
            approved_at = datetime.now(timezone.utc).isoformat()
            partition_name = (_PARTITION_MAP.get(partition_id) or {}).get("name", partition_id)

            chunks = self._build_chunks(
                reviewed=reviewed,
                schema_type=schema_type,
                partition_id=partition_id,
                partition_name=partition_name,
                file_name=file_name,
                document_id=document_id,
                summary_id=summary_id,
                approved_by=approved_by,
                approved_at=approved_at,
            )

            if not chunks:
                logger.warning("no chunks generated summary_id=%s", summary_id)
                doc_repo.update_status(document_id, "indexed")
                return

            # 写 DB
            saved_chunks = chunk_repo.bulk_create(chunks)

            # 写 Milvus
            self._index_to_milvus(saved_chunks, partition_id)

            # 更新向量状态
            chunk_ids = [c["id"] for c in saved_chunks if "id" in c]
            chunk_repo.update_vector_status(chunk_ids, "indexed")
            doc_repo.update_status(document_id, "indexed")
            logger.info("chunks indexed summary_id=%s count=%d", summary_id, len(saved_chunks))
        except Exception as exc:
            logger.exception("chunk generation failed summary_id=%s", summary_id)
            if doc:
                doc_repo.update_status(doc.get("id", ""), "summary_failed", parse_error=str(exc))

    def _build_chunks(
        self,
        reviewed: dict,
        schema_type: str,
        partition_id: str,
        partition_name: str,
        file_name: str,
        document_id: str,
        summary_id: str,
        approved_by: str | None,
        approved_at: str,
    ) -> list[dict]:
        common_meta = {
            "partition_id": partition_id,
            "schema_type": schema_type,
            "source_file": file_name,
            "review_status": "approved",
            "confidence": (reviewed.get("metadata") or {}).get("confidence", "case_specific"),
            "approved_by": approved_by,
            "approved_at": approved_at,
        }

        if schema_type == "case_report":
            return self._chunks_case_report(reviewed, partition_id, partition_name, file_name, document_id, summary_id, common_meta)
        elif schema_type == "reagent_manual":
            return self._chunks_reagent_manual(reviewed, partition_id, partition_name, file_name, document_id, summary_id, common_meta)
        else:
            return self._chunks_general_doc(reviewed, partition_id, partition_name, file_name, document_id, summary_id, common_meta)

    def _make_chunk(
        self,
        chunk_type: str,
        body: str,
        source_pages: list[int],
        partition_name: str,
        file_name: str,
        document_id: str,
        summary_id: str,
        partition_id: str,
        meta: dict,
    ) -> dict:
        label = _CHUNK_TYPE_LABELS.get(chunk_type, chunk_type)
        pages_str = ", ".join(str(p) for p in source_pages) if source_pages else "?"
        chunk_text = (
            f"【分区】{partition_name}\n"
            f"【内容类型】{label}\n"
            f"【来源文件】{file_name} 第 {pages_str} 页\n"
            f"【审核状态】approved\n\n"
            f"{body}"
        )
        return {
            "document_id": document_id,
            "summary_id": summary_id,
            "partition_id": partition_id,
            "chunk_type": chunk_type,
            "chunk_text": chunk_text,
            "metadata_json": {**meta, "source_pages": source_pages, "chunk_type": chunk_type},
        }

    def _text(self, obj) -> str:
        if isinstance(obj, dict):
            return obj.get("text") or ""
        return str(obj) if obj else ""

    def _pages(self, obj) -> list[int]:
        if isinstance(obj, dict):
            return obj.get("source_pages") or []
        return []

    def _chunks_case_report(self, d: dict, pid: str, pname: str, fname: str, did: str, sid: str, meta: dict) -> list[dict]:
        chunks = []
        kw = {**meta}

        # 1. case_background
        title = d.get("title", "")
        cq = self._text(d.get("customer_question"))
        sp = self._text(d.get("sample_or_species"))
        rm = self._text(d.get("reagent_or_method"))
        pages = self._pages(d.get("customer_question"))
        body = f"标题：{title}\n客户问题：{cq}\n样本/物种：{sp}\n试剂/方法：{rm}"
        chunks.append(self._make_chunk("case_background", body, pages, pname, fname, did, sid, pid, kw))

        # 2. key_observations
        obs_list = d.get("key_observations") or []
        if obs_list:
            obs_pages: list[int] = []
            obs_body_lines = []
            for o in obs_list:
                obs_body_lines.append(f"- {self._text(o)}")
                obs_pages.extend(self._pages(o))
            chunks.append(self._make_chunk("key_observations", "\n".join(obs_body_lines), sorted(set(obs_pages)), pname, fname, did, sid, pid, kw))

        # 3. analysis_process（有才生成）
        ap_list = d.get("analysis_process") or []
        if ap_list:
            ap_pages: list[int] = []
            ap_lines = []
            for step in ap_list:
                ap_lines.append(f"步骤{step.get('step', '?')} [{step.get('analysis_type', '')}]：{self._text(step)}")
                ap_pages.extend(self._pages(step))
            chunks.append(self._make_chunk("analysis_process", "\n".join(ap_lines), sorted(set(ap_pages)), pname, fname, did, sid, pid, kw))

        # 4. conclusion_and_recs
        concl = self._text(d.get("conclusion"))
        concl_pages = self._pages(d.get("conclusion"))
        recs = d.get("recommendations") or []
        rec_lines = [f"- {self._text(r)}" for r in recs]
        body = f"结论：{concl}"
        if rec_lines:
            body += "\n\n建议：\n" + "\n".join(rec_lines)
        all_pages = list(concl_pages)
        for r in recs:
            all_pages.extend(self._pages(r))
        chunks.append(self._make_chunk("conclusion_and_recs", body, sorted(set(all_pages)), pname, fname, did, sid, pid, kw))

        # 5. limitations
        lim_list = d.get("limitations") or []
        if lim_list:
            lim_pages: list[int] = []
            lim_lines = [f"- {self._text(l)}" for l in lim_list]
            for l in lim_list:
                lim_pages.extend(self._pages(l))
            chunks.append(self._make_chunk("limitations", "\n".join(lim_lines), sorted(set(lim_pages)), pname, fname, did, sid, pid, kw))

        return chunks

    def _chunks_reagent_manual(self, d: dict, pid: str, pname: str, fname: str, did: str, sid: str, meta: dict) -> list[dict]:
        chunks = []
        kw = {**meta}

        # product_overview
        name = d.get("product_name", "")
        mfr = d.get("manufacturer", "")
        assays = ", ".join(d.get("applicable_assays") or [])
        components = d.get("key_components") or []
        comp_lines = [f"- {c.get('name', '')}：{c.get('function', '')}" for c in components]
        storage = self._text(d.get("storage_conditions"))
        body = f"产品名称：{name}\n厂商：{mfr}\n适用实验：{assays}\n\n成分：\n" + "\n".join(comp_lines) + f"\n\n储存条件：{storage}"
        comp_pages: list[int] = []
        for c in components:
            comp_pages.extend(c.get("source_pages") or [])
        chunks.append(self._make_chunk("product_overview", body, sorted(set(comp_pages)), pname, fname, did, sid, pid, kw))

        # usage_protocol
        proto = d.get("usage_protocol") or []
        if proto:
            proto_pages: list[int] = []
            proto_lines = [f"步骤{p.get('step', '?')}：{self._text(p)}" for p in proto]
            for p in proto:
                proto_pages.extend(p.get("source_pages") or [])
            chunks.append(self._make_chunk("usage_protocol", "\n".join(proto_lines), sorted(set(proto_pages)), pname, fname, did, sid, pid, kw))

        # precautions
        prec = d.get("precautions") or []
        if prec:
            prec_pages: list[int] = []
            prec_lines = [f"- {self._text(p)}" for p in prec]
            for p in prec:
                prec_pages.extend(p.get("source_pages") or [])
            chunks.append(self._make_chunk("precautions", "\n".join(prec_lines), sorted(set(prec_pages)), pname, fname, did, sid, pid, kw))

        return chunks

    def _chunks_general_doc(self, d: dict, pid: str, pname: str, fname: str, did: str, sid: str, meta: dict) -> list[dict]:
        chunks = []
        kw = {**meta}

        # summary_and_points
        title = d.get("title", "")
        summary = self._text(d.get("summary"))
        summary_pages = self._pages(d.get("summary"))
        points = d.get("key_points") or []
        point_lines = [f"- {self._text(p)}" for p in points]
        body = f"标题：{title}\n摘要：{summary}"
        if point_lines:
            body += "\n\n要点：\n" + "\n".join(point_lines)
        all_pages = list(summary_pages)
        for p in points:
            all_pages.extend(self._pages(p))
        chunks.append(self._make_chunk("summary_and_points", body, sorted(set(all_pages)), pname, fname, did, sid, pid, kw))

        # applicable_scenarios
        scenarios = d.get("applicable_scenarios") or []
        lims = d.get("limitations") or []
        if scenarios or lims:
            sc_pages: list[int] = []
            lines = [f"- {self._text(s)}" for s in scenarios]
            for s in scenarios:
                sc_pages.extend(self._pages(s))
            lim_lines = [f"- 限制：{self._text(l)}" for l in lims]
            for l in lims:
                sc_pages.extend(self._pages(l))
            body = "适用场景：\n" + "\n".join(lines)
            if lim_lines:
                body += "\n\n" + "\n".join(lim_lines)
            chunks.append(self._make_chunk("applicable_scenarios", body, sorted(set(sc_pages)), pname, fname, did, sid, pid, kw))

        return chunks

    def _index_to_milvus(self, chunks: list[dict], partition_id: str) -> None:
        """将 chunks 写入 Milvus。"""
        try:
            from langchain_core.documents import Document
            from langchain_community.vectorstores.utils import filter_complex_metadata
            from multi_agent.backed.knowledge.repositories.vector_store_repository import VectorStoreRepository

            vector_store = VectorStoreRepository()
            documents = []
            for chunk in chunks:
                meta = dict(chunk.get("metadata_json") or {})
                meta[settings.MILVUS_PARTITION_KEY] = partition_id
                meta["chunk_type"] = chunk["chunk_type"]
                meta["source"] = chunk.get("metadata_json", {}).get("source_file", "")
                meta["source_name"] = meta["source"]
                documents.append(Document(page_content=chunk["chunk_text"], metadata=meta))
            documents = filter_complex_metadata(documents)
            vector_store.add_documents(documents)
        except Exception as exc:
            logger.error("Milvus indexing failed: %s", exc)
            raise


# 避免循环引用，在方法内部 import
from multi_agent.backed.knowledge.config.settings import settings  # noqa: E402
