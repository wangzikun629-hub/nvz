from __future__ import annotations

from typing import Any

from multi_agent.backed.app.infrastructure.tools.local.knowledge_base import retrieve_knowledge


class KnowledgeAugmentationService:
    async def retrieve(self, question: str) -> dict[str, Any]:
        return await retrieve_knowledge(question)


knowledge_augmentation_service = KnowledgeAugmentationService()
