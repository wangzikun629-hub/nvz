import logging
import jieba
import re
import requests
from pathlib import Path
from time import perf_counter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from typing import List, Dict, Any
from langchain_core.documents import Document
from multi_agent.backed.knowledge.repositories.vector_store_repository import VectorStoreRepository
from multi_agent.backed.knowledge.services.ingestion.ingestion_processor import IngestionProcessor
from multi_agent.backed.knowledge.utils.markdown_utils import MarkDownUtils
from multi_agent.backed.knowledge.config.settings import settings
from sklearn.metrics.pairwise import cosine_similarity


class RetrievalService:
    """
    负责检索的类（检索器）
    RAG:（小块：越小越好【小（无线小）】）文本嵌入模型  （完整信息：越大越大【不能无限大】）文本语言模型====准 原文档（大）--->1.小块（子） 2.稍微大一点的块（整个文档）【父】---留一个保留关系：穿针引线思想（父文档召回）
    """

    def __init__(self):
        self.vector_store = VectorStoreRepository()
        self.spliter = IngestionProcessor()
        self._metadata_cache: List[Dict[str, Any]] | None = None
        self._metadata_cache_key: tuple[tuple[str, float], ...] | None = None
        self._chunk_cache: List[Dict[str, Any]] | None = None
        self._chunk_cache_key: tuple[tuple[str, float], ...] | None = None
        self._file_content_cache: dict[tuple[str, float], str] = {}
        self._query_embedding_cache: dict[str, List[float]] = {}
        self._title_embedding_cache: dict[tuple[str, ...], List[List[float]]] = {}
        self._candidate_embedding_cache: dict[str, List[float]] = {}
        self._rerank_session = requests.Session()

    def _build_metadata_cache_key(self) -> tuple[tuple[str, float], ...]:
        root = Path(settings.CRAWL_OUTPUT_DIR)
        if not root.exists():
            return ()
        return tuple(
            sorted(
                (
                    str(path),
                    path.stat().st_mtime,
                )
                for path in root.rglob("*.md")
            )
        )

    def _get_md_metadata(self) -> List[Dict[str, Any]]:
        cache_key = self._build_metadata_cache_key()
        if self._metadata_cache is None or self._metadata_cache_key != cache_key:
            self._metadata_cache = MarkDownUtils.collect_md_metadata(settings.CRAWL_OUTPUT_DIR)
            self._metadata_cache_key = cache_key
            self._chunk_cache = None
            self._chunk_cache_key = None
            self._title_embedding_cache.clear()
        return [dict(item) for item in self._metadata_cache]

    @staticmethod
    def _tokenize_text(text: str) -> List[str]:
        if not text:
            return []
        return [
            token.strip().lower()
            for token in jieba.lcut(text)
            if token and token.strip() and not token.isspace()
        ]

    def _build_chunk_cache(self) -> List[Dict[str, Any]]:
        cache_key = self._build_metadata_cache_key()
        if self._chunk_cache is not None and self._chunk_cache_key == cache_key:
            return self._chunk_cache

        chunk_cache: List[Dict[str, Any]] = []
        for md_metadata in self._get_md_metadata():
            path = md_metadata["path"]
            title = md_metadata["title"]
            try:
                content = self._read_file_cached(path)
            except Exception as exc:
                logger.warning("skip chunk cache build for %s: %s", path, str(exc))
                continue

            chunks = self.spliter.document_spliter.split_text(content) if len(content) >= 1200 else [content]
            for chunk_index, chunk in enumerate(chunks):
                chunk_cache.append(
                    {
                        "path": path,
                        "title": title,
                        "chunk_index": chunk_index,
                        "content": chunk,
                        "tokens": set(self._tokenize_text(f"{title}\n{chunk}")),
                    }
                )

        self._chunk_cache = chunk_cache
        self._chunk_cache_key = cache_key
        return chunk_cache

    def _compute_keyword_score(self, user_query: str, title: str, content: str) -> float:
        query_tokens = set(self._tokenize_text(user_query))
        if not query_tokens:
            return 0.0

        content_tokens = set(self._tokenize_text(content))
        title_tokens = set(self._tokenize_text(title))
        content_overlap = len(query_tokens & content_tokens) / len(query_tokens)
        title_overlap = len(query_tokens & title_tokens) / len(query_tokens)
        phrase_hit = 1.0 if user_query.strip() and user_query.strip().lower() in content.lower() else 0.0
        return content_overlap * 0.6 + phrase_hit * 0.25 + title_overlap * 0.15

    def _read_file_cached(self, file_path: str) -> str:
        path = Path(file_path)
        cache_key = (str(path), path.stat().st_mtime)
        cached = self._file_content_cache.get(cache_key)
        if cached is not None:
            return cached

        content = path.read_text(encoding="utf-8").strip()
        self._file_content_cache = {
            key: value
            for key, value in self._file_content_cache.items()
            if key[0] != str(path)
        }
        self._file_content_cache[cache_key] = content
        return content

    def _get_query_embedding(self, query: str) -> List[float]:
        cached = self._query_embedding_cache.get(query)
        if cached is not None:
            logger.info(
                "query embedding done query=%s cache_hit=true cost=0.000s",
                query[:80],
            )
            return cached
        started_at = perf_counter()
        embedding = self.vector_store.embedd_document(query)
        cost = perf_counter() - started_at
        if len(self._query_embedding_cache) >= 128:
            self._query_embedding_cache.pop(next(iter(self._query_embedding_cache)))
        self._query_embedding_cache[query] = embedding
        logger.info(
            "query embedding done query=%s cache_hit=false cost=%.3fs",
            query[:80],
            cost,
        )
        return embedding

    def _get_title_embeddings(self, titles: List[str]) -> List[List[float]]:
        cache_key = tuple(titles)
        cached = self._title_embedding_cache.get(cache_key)
        if cached is not None:
            logger.info(
                "title embeddings done titles=%d cache_hit=true cost=0.000s",
                len(titles),
            )
            return cached

        started_at = perf_counter()
        embeddings = self.vector_store.embedd_documents(titles)
        self._title_embedding_cache[cache_key] = embeddings
        logger.info(
            "title embeddings done titles=%d cache_hit=false cost=%.3fs",
            len(titles),
            perf_counter() - started_at,
        )
        return embeddings

    def _get_candidate_embeddings(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        embeddings_by_index: list[List[float] | None] = [None] * len(texts)
        missing_texts: list[str] = []
        missing_indices: list[int] = []
        for index, text in enumerate(texts):
            cached = self._candidate_embedding_cache.get(text)
            if cached is None:
                missing_texts.append(text)
                missing_indices.append(index)
            else:
                embeddings_by_index[index] = cached

        if missing_texts:
            started_at = perf_counter()
            missing_embeddings = self.vector_store.embedd_documents(missing_texts)
            for text, embedding, index in zip(missing_texts, missing_embeddings, missing_indices):
                if len(self._candidate_embedding_cache) >= 512:
                    self._candidate_embedding_cache.pop(next(iter(self._candidate_embedding_cache)))
                self._candidate_embedding_cache[text] = embedding
                embeddings_by_index[index] = embedding
            logger.info(
                "candidate embeddings done texts=%d cache_hits=%d cache_misses=%d cost=%.3fs",
                len(texts),
                len(texts) - len(missing_texts),
                len(missing_texts),
                perf_counter() - started_at,
            )
        else:
            logger.info(
                "candidate embeddings done texts=%d cache_hits=%d cache_misses=0 cost=0.000s",
                len(texts),
                len(texts),
            )

        return [embedding for embedding in embeddings_by_index if embedding is not None]

    def rough_ranking(self, user_query, mds_metadata: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
         对标题进行粗排
         基于jieba进行标题的分词匹配
        Args:
            user_query: 用户的问题
            mds_metadata: 所有md的元数据（标题【title】，路径【path】）

        Returns:
            List[Dict[str,Any]]:所有md的元数据 （标题【title】，路径【path】，标题粗排得分【rough_score】）
        """

        # 1. 用户输入问题是否存在
        if not user_query:
            return []
        ROUGHIN_WORD_WEIGHT = 0.7

        # 2.遍历mds_metadata(所有md的元数据)
        for md_metadata in mds_metadata:
            # 2.1 获取md标题
            md_metadata_title = md_metadata['title']

            # 2.2 判断标题是否存在
            if not md_metadata_title  or not md_metadata_title.strip():
                continue
            # 2.3 进行分词&&算得分
            # 2.3.1 优先用字符切:set:交、并、差:jarcard算法=A N B/A U B
            user_query_char = set(user_query)
            md_metadata_title_char = set(md_metadata_title)
            unique_char = user_query_char | md_metadata_title_char
            char_score = len(user_query_char & md_metadata_title_char) / len(unique_char) if len(unique_char) > 0 else 0

            # 2.3.2 在用jieba词项切(影响因素大一些)
            user_query_word = set(jieba.lcut(user_query))
            md_metadata_title_word = set(jieba.lcut(md_metadata_title))
            unique_word = user_query_word | md_metadata_title_word
            word_score = len(user_query_word & md_metadata_title_word) / len(unique_word) if len(unique_word) > 0 else 0

            # 2.3.3 计算粗排分数：字符级+词性项级(侧重)
            roughing_score = word_score * ROUGHIN_WORD_WEIGHT + char_score * (1 - ROUGHIN_WORD_WEIGHT)

            md_metadata['roughing_score'] = float(roughing_score)

        # 3.根据标题的元数据（roughing_score）排序并且留下前50个
        return sorted(mds_metadata, key=lambda x: x['roughing_score'], reverse=True)[:50]

    def fine_ranking(self, user_query: str, rough_mds_metadata: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
         对标题进行精排
         基于嵌入模型相似性以及cosine_similarity()
        Args:
            user_query: 用户当前问题
            rough_mds_metadata: 粗排后的md元数据

        Returns:
            List[Dict[str, Any]]: 带精排分数的元数据
        """

        # 1. 判粗排元数据
        if not rough_mds_metadata:
            return []

        # 两个二维矩阵（X[样本数] Y[样本质量]） K(X, Y) = <X, Y> / (||X||*||Y||)

        # 2. 对问题向量化
        query_embedding = self._get_query_embedding(user_query)

        # 3. 获取粗排后的标题
        roughing_title = [md_metadata['title'] for md_metadata in rough_mds_metadata]

        # 4. 标题的向量值
        roughing_title_embeddings = self._get_title_embeddings(roughing_title)

        # 5. 计算问题和粗排标题的相似度（余弦相似分数）分数值越大 代表问题和标题越相似
        # flatten()--->一维数组[0.1,0.4,0.01,0.6...]
        # X:1  Y:[1,2,3,4,5]   similarity=[0.1,0.4,0.01,0.6,0.3]【-1,0】
        similarity = cosine_similarity([query_embedding], roughing_title_embeddings).flatten()

        # 6. 遍历粗排元数据
        ROUGH_HEIGHT = 0.3
        SIM_HEIGHT = 0.7
        for index, md_metadata in enumerate(rough_mds_metadata):

            # a. 获取精排分数(归一)
            sim = similarity[index]
            if sim < 0:
                sim = 0
            # b. 获取粗排
            roughing_score = md_metadata['roughing_score']

            # c. 加权求最终精排分数
            final_score = roughing_score * ROUGH_HEIGHT + sim * SIM_HEIGHT

            # d. 存放到md_metadata 元数据中
            md_metadata['sim_score'] = sim
            md_metadata['final_score'] = final_score

        # 7. 排序
        sim_mds_metadata = sorted(rough_mds_metadata, key=lambda x: x['final_score'], reverse=True)[:5]

        # 8. 返回
        return sim_mds_metadata

    def retrieval(self, user_question: str, kb_scope: str | None = None) -> List[Document]:
        """
         核心检索方法（检索器的入口）
        Args:
            user_question: 用户输入的问题

        Returns:
           List[Document]: 返回指定Top-N个相似性文档列表
        """

        # 1. 第一路检索(基于嵌入模型的向量检索)---主要两方面（bge模型对中文比较好，langchain整合不太好[真的!]）第二方面（向量时候语义被稀释）小块嵌入【保证嵌入能力不被稀释】--->大块做管理（保证模型看到完整的信息）精度和准确性
        retrieval_started_at = perf_counter()
        vector_started_at = perf_counter()
        vector_status = "ok"
        try:
            based_vector_candidates = self._search_based_vector(
                user_question,
                kb_scope=kb_scope,
            )
        except Exception as exc:
            vector_status = "fallback"
            based_vector_candidates = []
            logger.exception("vector search failed, fallback to title search only: %s", str(exc))
        vector_cost = perf_counter() - vector_started_at

        # 2. 第二路检索(基于jieba的分词匹配的检索)
        keyword_started_at = perf_counter()
        try:
            based_keyword_candidates = [] if kb_scope else self._search_based_chunk_keyword(user_question)
        except Exception as exc:
            based_keyword_candidates = []
            logger.exception("chunk keyword search failed: %s", str(exc))
        keyword_cost = perf_counter() - keyword_started_at

        title_started_at = perf_counter()
        try:
            based_title_candidates = [] if kb_scope else self._search_based_title(user_question)
        except Exception as exc:
            based_title_candidates = []
            logger.exception("title search failed: %s", str(exc))
        title_cost = perf_counter() - title_started_at

        # 3. 合并两路检索的文档列表
        total_candidates = based_vector_candidates + based_keyword_candidates + based_title_candidates

        # 4. 对合并后的文档列表去重
        unique_candidates = self._deduplicate(total_candidates)

        # 5. 重新打分排序
        rerank_started_at = perf_counter()
        top_documents = self._reranking(unique_candidates, user_question)
        rerank_cost = perf_counter() - rerank_started_at

        logger.info(
            "retrieval done query=%s vector=%d keyword=%d title=%d unique=%d final=%d vector_status=%s total=%.3fs vector=%.3fs keyword=%.3fs title=%.3fs rerank=%.3fs",
            user_question[:80],
            len(based_vector_candidates),
            len(based_keyword_candidates),
            len(based_title_candidates),
            len(unique_candidates),
            len(top_documents),
            vector_status,
            perf_counter() - retrieval_started_at,
            vector_cost,
            keyword_cost,
            title_cost,
            rerank_cost,
        )

        # 6.返回指定Top-N个文档列表
        return top_documents

    def _search_based_vector(self, user_question: str, kb_scope: str | None = None) -> List[Document]:
        """
        第一路检索
        基于语义相似度检索

        Args:
            user_question: 用户输入的问题

        Returns:
            List[Document]： Top-N个相似的文档列表

        """
        # 1.返回带分数的文档列表
        started_at = perf_counter()
        query_embedding = self._get_query_embedding(user_question)
        documents_with_score = self.vector_store.search_similarity_by_vector_with_score(
            query_embedding,
            kb_scope=kb_scope,
        )
        logger.info(
            "vector search done query=%s candidates=%d cost=%.3fs",
            user_question[:80],
            len(documents_with_score),
            perf_counter() - started_at,
        )

        # 2.TODO(不用距离得分)
        based_vector_candidates = []
        for document, score in documents_with_score:
            document.metadata["vector_score"] = float(score)
            based_vector_candidates.append(document)
        return based_vector_candidates

    def _search_based_chunk_keyword(self, user_query: str) -> List[Document]:
        if not user_query or not user_query.strip():
            return []

        scored_chunks = []
        for chunk in self._build_chunk_cache():
            keyword_score = self._compute_keyword_score(
                user_query,
                chunk["title"],
                chunk["content"],
            )
            if keyword_score <= 0:
                continue
            scored_chunks.append((chunk, keyword_score))

        scored_chunks.sort(key=lambda item: item[1], reverse=True)

        documents = []
        for chunk, keyword_score in scored_chunks[:8]:
            documents.append(
                Document(
                    page_content=f"source: {chunk['title']}\n{chunk['content']}",
                    metadata={
                        "path": chunk["path"],
                        "title": chunk["title"],
                        "chunk_index": int(chunk["chunk_index"]),
                        "keyword_score": float(keyword_score),
                    },
                )
            )
        return documents

    def _search_based_title(self, user_query: str) -> List[Document]:
        """
         第二路检索
         基于标题的关键词匹配检索
        Args:
            user_query: 用户输入的问题

        Returns:
            List[Document]: Top-N个相似的文档列表

        """

        # 1. 获取指定目录下的文件的标题
        mds_metadata = self._get_md_metadata()

        # 2. 进行标题匹配
        # 2.1 关键词匹配（jieba）--->（比较对象：用户输入的问题 vs crawl目录下的文件标题）
        # 2.2 标题的语义匹配（比较对象：用户的输入问题  vs md目录下的 ）
        rough_mds_metadata = self.rough_ranking(user_query, mds_metadata)
        fine_mds_metadata = self.fine_ranking(user_query, rough_mds_metadata[:20])

        # 3. 处理文档（根据标题读取标题对于的文档内容---Document(page_content,metadata={})）

        based_title_candidates = []
        for fine_md_metadata in fine_mds_metadata:
            try:
                # 3.1 打开文件
                content = self._read_file_cached(fine_md_metadata['path'])
                # 3.2 判断content内容长度
                # a.短md知识
                if len(content) < 1200:
                    # 不切分
                    doc = Document(page_content=content, metadata={
                        "path": fine_md_metadata['path'],
                        "title": fine_md_metadata['title'],
                        "title_score": float(fine_md_metadata.get("final_score", 0.0)),
                    })
                    based_title_candidates.append(doc)
                # b. 长md知识 切分
                else:
                    doc_chunks = self._deal_long_title_content(content, fine_md_metadata, user_query)
                    based_title_candidates.extend(doc_chunks)  # doc_chunks 列表中元素打散了
            except Exception as e:

                logger.error(f"打开文件失败:{e}")
                return []
        # 4. 返回指定文档列表

        return based_title_candidates

    def _deduplicate(self, total_candidates: List[Document]) -> List[Document]:
        """
         对合并后的文档列表去重
         用set()集合去重（(title,内容的前【100】个字符)）-->key
        Args:
            total_candidates: 合并的文档列表

        Returns:
            List[Document]：唯一的文档列表
        """

        if not total_candidates:
            return []

        # 2. 定义set集合
        seen = {}
        unique_candidates = []

        # 3. 遍历合并后的每一个文档列表
        for document in total_candidates:
            # 去重（）
            clean_content = re.sub(r'^文档来源:.*?(?=(\n|#))', '', document.page_content, flags=re.DOTALL).strip()#【加上】
            title = (
                document.metadata.get("title")
                or document.metadata.get("source_name")
                or document.metadata.get("source")
                or document.metadata.get("path")
                or getattr(document, "id", "")
                or ""
            )
            key = (title, clean_content[:100])
            if key not in seen:
                seen[key] = len(unique_candidates)
                unique_candidates.append(document)
                continue

            existed_document = unique_candidates[seen[key]]
            for score_key in ("vector_score", "keyword_score", "title_score", "similarity"):
                existed_score = float(existed_document.metadata.get(score_key, 0.0))
                current_score = float(document.metadata.get(score_key, 0.0))
                if current_score > existed_score:
                    existed_document.metadata[score_key] = current_score

        # 4. 返回唯一的
        return unique_candidates

    def _reranking(self, unique_candidates: List[Document], user_question: str) -> List[Document]:
        """
         重新计算打分&&排序
         第二路长文档已经进行了cosine_similarity()的计算（无需在次打分）
         对第一路的文档和第二路的短文档进行重新计算

        Args:

            user_question: 用户输入的问题

        Returns:
            List[Document]: 最终指定Top-N的文档列表

        """

        # 1. 判断去重合并之后文档列表是否有文档对象
        if not unique_candidates:
            return []

        if (
            not settings.ENABLE_ONLINE_RERANK
            or not settings.RERANK_MODEL
            or not settings.API_KEY
        ):
            return self._local_rerank(unique_candidates, user_question)

        local_reranked_candidates = self._local_rerank(unique_candidates, user_question)
        if not self._should_trigger_online_rerank(user_question, local_reranked_candidates):
            return local_reranked_candidates
        rerank_candidates = local_reranked_candidates[:max(1, settings.ONLINE_RERANK_CANDIDATES)]

        documents = []
        for document in rerank_candidates:
            title = document.metadata.get("title") or document.metadata.get("source") or ""
            content = document.page_content or ""
            documents.append(f"title: {title}\ncontent: {content}")

        url = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
        payload = {
            "model": settings.RERANK_MODEL,
            "input": {
                "query": user_question,
                "documents": documents,
            },
            "parameters": {
                "top_n": min(3, len(documents)),
                "return_documents": False,
            },
        }
        headers = {
            "Authorization": f"Bearer {settings.API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            rerank_started_at = perf_counter()
            response = self._rerank_session.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
            results = response.json()["output"]["results"]
            logger.info(
                "rerank request done query=%s docs=%d payload_chars=%d results=%d cost=%.3fs",
                user_question[:80],
                len(documents),
                sum(len(item) for item in documents),
                len(results),
                perf_counter() - rerank_started_at,
            )

            reranked_docs = []
            for item in results:
                document = rerank_candidates[item["index"]]
                document.metadata["rerank_score"] = item.get("relevance_score")
                reranked_docs.append(document)

            return reranked_docs
        except Exception as e:
            logger.exception(f"rerank failed, fallback to local rerank: {str(e)}")
            return local_reranked_candidates

        need_embedding_docs = []
        need_embedding_candidates_indices = []
        score_doc = []

        # 2. 遍历去重并合并之后的文档列表(Document,score)
        for candidate_index, unique_candidate in enumerate(unique_candidates):
            # 如何去判断 第二路长文档 or  第一路和第二路短文档?
            # 2.1 第二路的长文档
            if "chunk_index" in unique_candidate.metadata and "similarity" in unique_candidate.metadata:
                score_doc.append((unique_candidate, unique_candidate.metadata['similarity']))
            # 2.2 第一路和第二路的短文档
            else:
                need_embedding_docs.append(unique_candidate)
                need_embedding_candidates_indices.append(candidate_index)

        # 3.处理需要重新计算分数的文档
        if need_embedding_docs:
            # 3.1 计算用户问题的向量
            query_embedding = self.vector_store.embedd_document(user_question)

            # 3.2 获取到需要向量的文档内容
            embedding_docs_content = ["文档来源:" + doc.metadata['title'] + doc.page_content for doc in
                                      need_embedding_docs]
            # 3.3 计算需要向量的文档内容
            doc_embeddings = self.vector_store.embedd_documents(embedding_docs_content)

            # 3.4 计算相似得分
            similarity = cosine_similarity([query_embedding], doc_embeddings).flatten()

            # 3.5 封装到带得分的文档列表
            for idx, candidate_index in enumerate(need_embedding_candidates_indices):
                score_doc.append((unique_candidates[candidate_index], similarity[idx]))

        # 4. 排序
        sorted_docs = sorted(score_doc, key=lambda x: x[1], reverse=True)

        # 5. 返回Top-N
        return [doc for doc, _ in sorted_docs[:2]]

    def _local_rerank(self, unique_candidates: List[Document], user_question: str) -> List[Document]:
        query_embedding = self._get_query_embedding(user_question)

        candidate_texts = []
        for document in unique_candidates:
            title = document.metadata.get("title") or document.metadata.get("source") or ""
            content = document.page_content or ""
            candidate_texts.append(f"source: {title}\n{content[:4000]}")

        doc_embeddings = self._get_candidate_embeddings(candidate_texts)
        similarity = cosine_similarity([query_embedding], doc_embeddings).flatten()

        scored_documents = []
        for index, document in enumerate(unique_candidates):
            content_score = max(float(similarity[index]), 0.0)
            title_score = float(document.metadata.get("title_score", 0.0))
            keyword_score = float(document.metadata.get("keyword_score", 0.0))
            vector_score = float(document.metadata.get("vector_score", 0.0))
            final_score = content_score * 0.55 + keyword_score * 0.20 + title_score * 0.10 + vector_score * 0.15
            document.metadata["local_rerank_score"] = final_score
            scored_documents.append((document, final_score))

        sorted_docs = sorted(scored_documents, key=lambda x: x[1], reverse=True)
        top_n = max(settings.TOP_FINAL, settings.ONLINE_RERANK_CANDIDATES)
        return [doc for doc, _ in sorted_docs[:top_n]]

    def _should_trigger_online_rerank(self, user_question: str, local_reranked_candidates: List[Document]) -> bool:
        if not local_reranked_candidates:
            return False

        normalized_query = (user_question or "").strip().lower()
        query_tokens = self._tokenize_text(normalized_query)
        query_token_count = len(query_tokens)
        query_char_count = len(normalized_query)

        high_value_keywords = [
            keyword.strip().lower()
            for keyword in settings.ONLINE_RERANK_HIGH_VALUE_KEYWORDS.split(",")
            if keyword.strip()
        ]
        is_high_value_query = any(keyword in normalized_query for keyword in high_value_keywords)
        is_short_query = (
            query_token_count <= settings.ONLINE_RERANK_SHORT_QUERY_TOKENS
            or query_char_count <= settings.ONLINE_RERANK_SHORT_QUERY_CHARS
        )

        top1_score = float(local_reranked_candidates[0].metadata.get("local_rerank_score", 0.0))
        top2_score = float(local_reranked_candidates[1].metadata.get("local_rerank_score", 0.0)) \
            if len(local_reranked_candidates) > 1 else 0.0
        top_score_gap = top1_score - top2_score if len(local_reranked_candidates) > 1 else top1_score
        is_close_match = len(local_reranked_candidates) > 1 and top_score_gap <= settings.ONLINE_RERANK_SCORE_GAP
        is_low_confidence = top1_score <= settings.ONLINE_RERANK_LOW_CONFIDENCE

        should_rerank = is_high_value_query or is_short_query or is_close_match or is_low_confidence
        logger.info(
            "online rerank gate query=%s enabled=%s high_value=%s short=%s close=%s low_confidence=%s top1=%.3f top2=%.3f gap=%.3f",
            normalized_query[:80],
            should_rerank,
            is_high_value_query,
            is_short_query,
            is_close_match,
            is_low_confidence,
            top1_score,
            top2_score,
            top_score_gap,
        )
        return should_rerank

    def _deal_long_title_content(self, content: str, fine_md_metadata: Dict[str, Any], user_query: str) -> List[
        Document]:
        """
         处理标题对应的长文本
         切分-->文档块--->算文档块和问题的相似度
        Args:
            content: 长文本
            fine_md_metadata: 长文本对应的元数据
            user_query: 用户的问题

        Returns:
            List[Document]: 和问题相似的文档块（chunk）
        """

        # 1. 对长文本切分(换成适合)
        chunks = self.spliter.document_spliter.split_text(content)

        # 2. 获取对应的标题
        doc_chunks_title = fine_md_metadata['title']

        docs = []
        for chunk_idx, doc_chunk in enumerate([]):
            doc = Document(
                page_content=f"source: {doc_chunks_title}\n{doc_chunk}",
                metadata={
                    "path": fine_md_metadata['path'],
                    "title": fine_md_metadata['title'],
                    "chunk_index": int(chunk_idx),
                }
            )
            docs.append(doc)

        if False:
            return docs

        # 3. 标题注入到文档块中（第二次结构和第一次的拼接一定要一样）TODO
        doc_chunks_inject_title = [f"文档来源:{doc_chunks_title}\n{doc_chunk}" for doc_chunk in chunks]

        # 4. 对问题向量
        query_embedding = self._get_query_embedding(user_query)

        # 5. 对切分后的文档块向量化
        doc_chunk_embeddings = self.vector_store.embedd_documents(doc_chunks_inject_title)

        # 6. 计算相似性:doc_chunks_similarity[0.8,0.6,0.7,0.1,0.9]
        doc_chunks_similarity = cosine_similarity([query_embedding], doc_chunk_embeddings).flatten()

        # 7. 获取3个相似性分数值高的三个索引 argsort->[3,1,2,0,4]->[2,0,4]--->[4,0,2]
        top_doc_chunks_indices = doc_chunks_similarity.argsort()[-3:][::-1]

        # 8. 构建最终文档对象列表(为每一个切分后的块)
        docs = []
        for chunk_idx in top_doc_chunks_indices:
            doc = Document(
                page_content=doc_chunks_inject_title[chunk_idx],  # 带上
                metadata={
                    "path": fine_md_metadata['path'],
                    "title": fine_md_metadata['title'],
                    "chunk_index": int(chunk_idx),
                    "similarity": float(doc_chunks_similarity[chunk_idx]),
                    "title_score": float(fine_md_metadata.get("final_score", 0.0))
                }
            )
            docs.append(doc)

        return docs


if __name__ == '__main__':
    retrival_service = RetrievalService()

    # rough_ranking_result = retrival_service.rough_ranking("peak数量是不是越多越好？"）
    # for roughing_result in rough_ranking_result[:10]:
    #     print(f"粗排---{roughing_result}")
    #
    # sim_ranking_result = retrival_service.fine_ranking("peak数量是不是越多越好？", rough_ranking_result[:10])
    #
    # for sim_result in sim_ranking_result:
    #     print(f"精排---{sim_result}")


    result = retrival_service.retrieval("peak数量是不是越多越好？")
    # result = retrival_service.retrieval("ChromHMM结果该如何解读")
    # result = retrival_service.retrieval("比对率低是什么原因") # 80-90%

    for r in result:
        print(r)
