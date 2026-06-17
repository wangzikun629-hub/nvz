import logging
from time import perf_counter
from typing import List

from langchain_core.documents import Document
from langchain_openai import ChatOpenAI

from multi_agent.backed.knowledge.config.settings import settings


logger = logging.getLogger(__name__)


class QueryService:
    """Knowledge-base answer generation service."""

    MAX_CONTEXT_DOCS = 3
    MAX_CHARS_PER_DOC = 1200
    MAX_TOTAL_CONTEXT_CHARS = 2400
    MAX_OUTPUT_TOKENS = 600

    def __init__(self):
        self.llm = ChatOpenAI(
            model=settings.MODEL,
            api_key=settings.API_KEY,
            base_url=settings.BASE_URL,
            temperature=0,
            timeout=120,
            max_tokens=self.MAX_OUTPUT_TOKENS,
        )

    def _clip_text(self, text: str, limit: int) -> str:
        normalized = " ".join((text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip() + "..."

    def _build_context(self, retrieval_context: List[Document]) -> str:
        context_parts: List[str] = []
        used_chars = 0

        for index, document in enumerate(retrieval_context[: self.MAX_CONTEXT_DOCS], start=1):
            title = document.metadata.get("title") or document.metadata.get("source") or f"资料{index}"
            content = self._clip_text(document.page_content or "", self.MAX_CHARS_PER_DOC)
            if not content:
                continue

            remaining_chars = self.MAX_TOTAL_CONTEXT_CHARS - used_chars
            if remaining_chars <= 0:
                break
            if len(content) > remaining_chars:
                content = self._clip_text(content, remaining_chars)

            context_parts.append(f"资料{index} 标题: {title}\n内容: {content}")
            used_chars += len(content)

        return "\n\n".join(context_parts)

    def generate_answer(self, user_question: str, retrival_context: List[Document]) -> str:
        if not retrival_context:
            return "未检索到任何相关文档，无法提供回复。"

        retrieval_prompt_context = self._build_context(retrival_context)
        if not retrieval_prompt_context:
            return "未检索到任何相关文档，无法提供回复。"

        prompt = f"""
你是诺唯赞生物科技有限公司的高级技术支持专家。请严格基于参考资料回答用户问题。

【参考资料】
{retrieval_prompt_context}

【用户问题】
{user_question}

【回答要求】
1. 只能依据参考资料作答，不要补充资料中没有的信息。
2. 如果资料不足以支持回答，直接回复：当前知识库中暂时没有找到该问题的解决方案。
3. 优先给出简洁、可执行的结论；如需分点，控制在 3 到 6 点。
4. 只在用户明确提及或资料必须依赖时保留具体产品名、货号、型号。
5. 结尾单独一行列出“参考资料：资料1、资料2”这样的引用编号。
"""

        llm_started_at = perf_counter()
        llm_response = self.llm.invoke(prompt)
        logger.info(
            "generate_answer done query=%s docs=%d context_chars=%d cost=%.3fs",
            user_question[:80],
            len(retrival_context),
            len(retrieval_prompt_context),
            perf_counter() - llm_started_at,
        )
        return llm_response.content
