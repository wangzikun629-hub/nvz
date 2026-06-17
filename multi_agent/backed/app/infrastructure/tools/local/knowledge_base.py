import asyncio
from contextvars import ContextVar
from typing import Dict, List

import httpx
from agents import function_tool

from multi_agent.backed.app.config.settings import settings
from multi_agent.backed.app.infrastructure.logging.logger import logger


KNOWLEDGE_RETRIEVAL_TIMEOUT_SECONDS = 8.0

knowledge_http_client = httpx.AsyncClient(
    trust_env=False,
    timeout=httpx.Timeout(
        connect=2.0,
        read=KNOWLEDGE_RETRIEVAL_TIMEOUT_SECONDS,
        write=2.0,
        pool=2.0,
    ),
)
knowledge_query_cache_var: ContextVar[dict[str, Dict] | None] = ContextVar(
    "knowledge_query_cache_var",
    default=None,
)
knowledge_retrieval_trace_var: ContextVar[list[Dict] | None] = ContextVar(
    "knowledge_retrieval_trace_var",
    default=None,
)


def init_round_knowledge_cache():
    cache_token = knowledge_query_cache_var.set({})
    trace_token = knowledge_retrieval_trace_var.set([])
    return cache_token, trace_token


def reset_round_knowledge_cache(token) -> None:
    cache_token, trace_token = token
    knowledge_query_cache_var.reset(cache_token)
    knowledge_retrieval_trace_var.reset(trace_token)


def get_round_retrieval_trace() -> List[Dict]:
    trace = knowledge_retrieval_trace_var.get()
    return list(trace or [])


def _normalize_question(question: str) -> str:
    return " ".join((question or "").split()).strip().lower()


def _format_retrieval_payload(payload: Dict) -> Dict:
    documents = payload.get("documents", [])
    formatted_documents = []
    for item in documents:
        formatted_documents.append(
            {
                "index": item.get("index"),
                "title": item.get("title", ""),
                "source": item.get("source", ""),
                "content": item.get("content", ""),
                "chunk_id": item.get("chunk_id", ""),
            }
        )

    return {
        "question": payload.get("question", ""),
        "documents": formatted_documents,
    }


def _record_retrieval_trace(normalized_question: str, payload: Dict) -> None:
    trace = knowledge_retrieval_trace_var.get()
    if trace is None:
        return
    for item in trace:
        if item.get("_normalized_question") == normalized_question:
            return
    trace.append(
        {
            "_normalized_question": normalized_question,
            "question": payload.get("question", ""),
            "documents": payload.get("documents", []),
        }
    )


async def retrieve_knowledge(question: str) -> Dict:
    normalized_question = _normalize_question(question)
    round_cache = knowledge_query_cache_var.get()
    if round_cache is not None and normalized_question in round_cache:
        logger.info("query_knowledge cache hit question=%s", normalized_question[:80])
        cached_payload = round_cache[normalized_question]
        _record_retrieval_trace(normalized_question, cached_payload)
        return cached_payload

    response = await knowledge_http_client.post(
        url=f"{settings.KNOWLEDGE_BASE_URL}/retrieve",
        json={"question": question},
    )
    response.raise_for_status()
    formatted_payload = _format_retrieval_payload(response.json())
    if round_cache is not None:
        round_cache[normalized_question] = formatted_payload
    _record_retrieval_trace(normalized_question, formatted_payload)
    return formatted_payload


@function_tool
async def query_knowledge(question: str) -> Dict:
    """查询知识库检索结果，用于获取与用户问题相关的技术文档片段。"""
    try:
        return await retrieve_knowledge(question)
    except httpx.HTTPError as e:
        logger.error("发送请求获取知识库数据失败: %s", str(e))
        return {"status": "error", "error_msg": f"发送请求获取知识库数据失败: {e}"}
    except Exception as e:
        logger.error("未知错误: %s", str(e))
        return {"status": "error", "error_msg": f"未知错误: {e}"}


async def main():
    result = await query_knowledge(question="比对率低是什么原因")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
