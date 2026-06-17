from collections.abc import AsyncGenerator

from multi_agent.backed.app.utils.retry_util import with_llm_retry
from multi_agent.backed.app.infrastructure.ai.openai_client import (
    SUB_MODEL_NAME,
    sub_model_client,
)
from multi_agent.backed.app.infrastructure.tools.local.knowledge_base import retrieve_knowledge
from multi_agent.backed.app.schemas.response import ContentKind
from multi_agent.backed.app.utils.response_util import ResponseFactory


class RagFastService:
    """Low-latency path for ordinary knowledge-base QA."""

    MAX_DOCS = 4
    MAX_DOC_CHARS = 1200
    MAX_OUTPUT_TOKENS = 420
    SYSTEM_PROMPT = (
        "你是企业知识库问答助手。"
        "请严格依据提供的资料回答，优先给出直接结论。"
        "如果资料不足，请明确说明资料不足，不要编造。"
        "回答尽量简洁，优先使用短段落或短列表。"
    )

    @classmethod
    def _build_context_block(cls, context_payload: dict[str, object] | None) -> str:
        if not context_payload:
            return ""

        turns = context_payload.get("turns") or []
        current_user = str(context_payload.get("current_user") or "").strip()
        if not turns:
            return ""

        blocks: list[str] = ["## 对话上下文"]
        for index, turn in enumerate(turns, start=1):
            user_text = str(turn.get("user") or "").strip()
            assistant_text = str(turn.get("assistant") or "").strip()
            blocks.append(
                f"[第{index}轮用户]\n{user_text}\n\n[第{index}轮助手]\n{assistant_text}"
            )
        if current_user:
            blocks.append(f"## 当前问题\n{current_user}")
        return "\n\n".join(blocks)

    @classmethod
    def _build_documents_block(cls, retrieval_payload: dict) -> str:
        documents = retrieval_payload.get("documents", [])[: cls.MAX_DOCS]
        if not documents:
            return "## 检索资料\n未检索到可用资料。"

        blocks = ["## 检索资料"]
        for index, item in enumerate(documents, start=1):
            title = (item.get("title") or item.get("source") or f"文档{index}").strip()
            source = (item.get("source") or "").strip() or "未标注"
            content = (item.get("content") or "").strip()[: cls.MAX_DOC_CHARS]
            blocks.append(
                f"[资料{index}] 标题: {title}\n来源: {source}\n内容:\n{content}"
            )
        return "\n\n".join(blocks)

    @classmethod
    def _build_messages(
        cls,
        question: str,
        retrieval_payload: dict,
        context_payload: dict[str, object] | None,
    ) -> list[dict[str, str]]:
        context_block = cls._build_context_block(context_payload)
        documents_block = cls._build_documents_block(retrieval_payload)
        prompt_parts = []
        if context_block:
            prompt_parts.append(context_block)
        prompt_parts.append(documents_block)
        prompt_parts.append(f"## 用户问题\n{question}")
        prompt_parts.append("请直接输出最终答案。")
        return [
            {"role": "system", "content": cls.SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(prompt_parts)},
        ]

    @classmethod
    async def answer_sync(
        cls,
        question: str,
        context_payload: dict[str, object] | None = None,
    ) -> str:
        retrieval_payload = await retrieve_knowledge(question)
        response = await with_llm_retry(
            lambda: sub_model_client.chat.completions.create(
                model=SUB_MODEL_NAME,
                messages=cls._build_messages(question, retrieval_payload, context_payload),
                temperature=0,
                max_tokens=cls.MAX_OUTPUT_TOKENS,
                stream=False,
            )
        )
        return (response.choices[0].message.content or "").strip()

    @classmethod
    async def answer_stream(
        cls,
        question: str,
        context_payload: dict[str, object] | None = None,
    ) -> tuple[AsyncGenerator[str, None], list[str]]:
        retrieval_payload = await retrieve_knowledge(question)
        stream = await with_llm_retry(
            lambda: sub_model_client.chat.completions.create(
                model=SUB_MODEL_NAME,
                messages=cls._build_messages(question, retrieval_payload, context_payload),
                temperature=0,
                max_tokens=cls.MAX_OUTPUT_TOKENS,
                stream=True,
            )
        )
        chunks: list[str] = []

        async def generate() -> AsyncGenerator[str, None]:
            yield "data: " + ResponseFactory.build_text(
                "已完成知识库检索，正在生成答案。",
                ContentKind.PROCESS,
            ).model_dump_json() + "\n\n"

            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                delta = ""
                if choice and getattr(choice, "delta", None):
                    delta = choice.delta.content or ""
                if not delta:
                    continue
                chunks.append(delta)
                yield "data: " + ResponseFactory.build_text(
                    delta,
                    ContentKind.ANSWER,
                ).model_dump_json() + "\n\n"

        return generate(), chunks
