from __future__ import annotations

from datetime import datetime
from typing import Any

from multi_agent.backed.app.infrastructure.async_lock_manager import key_lock
from multi_agent.backed.app.repositories.project_report_cache_repository import project_report_cache_repository
from multi_agent.backed.app.repositories.project_state_repository import project_state_repository

_REGISTRY = "project_state"


class ProjectSessionStateService:
    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "active_project_id": None,
            "active_project_root": None,
            "project_context_locked": False,
            "project_context_source": None,
            "recent_project_questions": [],
            "recent_projects": [],
            "last_identified_at": None,
            "pending_project_confirmation": None,
            "pending_followup_action": None,
            "current_project_id": None,
            "current_project_root": None,
            "recent_questions": [],
            "ai_report_summary": None,
        }

    @classmethod
    def _merge(cls, raw: dict[str, Any] | None) -> dict[str, Any]:
        merged = cls._default_state()
        merged.update(raw or {})
        if not merged.get("active_project_id") and merged.get("current_project_id"):
            merged["active_project_id"] = merged.get("current_project_id")
        if not merged.get("active_project_root") and merged.get("current_project_root"):
            merged["active_project_root"] = merged.get("current_project_root")
        if not merged.get("recent_project_questions") and merged.get("recent_questions"):
            merged["recent_project_questions"] = list(merged.get("recent_questions", []))[-10:]
        if not isinstance(merged.get("recent_projects"), list):
            merged["recent_projects"] = []
        return merged

    # ------------------------------------------------------------------ sync (保留兼容旧调用)

    def load_state(self, user_id: str, session_id: str) -> dict[str, Any]:
        raw = project_state_repository.load_state(user_id, session_id) or {}
        return self._merge(raw)

    # ------------------------------------------------------------------ async read

    async def aload_state(self, user_id: str, session_id: str) -> dict[str, Any]:
        """非阻塞读取状态（只读，不持锁）。"""
        raw = await project_state_repository.aload_state(user_id, session_id)
        return self._merge(raw)

    # ------------------------------------------------------------------ async mutate（持锁保护 RMW）

    async def abind_active_project(
        self,
        user_id: str,
        session_id: str,
        project_id: str,
        project_root: str,
        question: str,
        source: str = "inferred",
    ) -> dict[str, Any]:
        async with key_lock(_REGISTRY, user_id, session_id):
            state = self._merge(await project_state_repository.aload_state(user_id, session_id))
            recent_questions = list(state.get("recent_questions", []))
            recent_project_questions = list(state.get("recent_project_questions", []))
            if question:
                recent_questions.append(question)
                recent_project_questions.append(question)
            recent_projects = [
                item
                for item in list(state.get("recent_projects", []))
                if isinstance(item, dict) and str(item.get("project_id") or "") != project_id
            ]
            recent_projects.insert(
                0,
                {
                    "project_id": project_id,
                    "project_root": project_root,
                    "last_used_at": datetime.now().isoformat(timespec="seconds"),
                    "source": source,
                },
            )
            state.update(
                {
                    "active_project_id": project_id,
                    "active_project_root": project_root,
                    "project_context_locked": True,
                    "project_context_source": source,
                    "recent_project_questions": recent_project_questions[-10:],
                    "recent_projects": recent_projects[:10],
                    "pending_project_confirmation": None,
                    "pending_followup_action": None,
                    "current_project_id": project_id,
                    "current_project_root": project_root,
                    "recent_questions": recent_questions[-10:],
                    "last_identified_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            await project_state_repository.asave_state(user_id, session_id, state)
            return state

    # sync 兼容版
    def bind_active_project(
        self,
        user_id: str,
        session_id: str,
        project_id: str,
        project_root: str,
        question: str,
        source: str = "inferred",
    ) -> dict[str, Any]:
        state = self.load_state(user_id, session_id)
        recent_questions = list(state.get("recent_questions", []))
        recent_project_questions = list(state.get("recent_project_questions", []))
        if question:
            recent_questions.append(question)
            recent_project_questions.append(question)
        recent_projects = [
            item
            for item in list(state.get("recent_projects", []))
            if isinstance(item, dict) and str(item.get("project_id") or "") != project_id
        ]
        recent_projects.insert(
            0,
            {
                "project_id": project_id,
                "project_root": project_root,
                "last_used_at": datetime.now().isoformat(timespec="seconds"),
                "source": source,
            },
        )
        state.update(
            {
                "active_project_id": project_id,
                "active_project_root": project_root,
                "project_context_locked": True,
                "project_context_source": source,
                "recent_project_questions": recent_project_questions[-10:],
                "recent_projects": recent_projects[:10],
                "pending_project_confirmation": None,
                "pending_followup_action": None,
                "current_project_id": project_id,
                "current_project_root": project_root,
                "recent_questions": recent_questions[-10:],
                "last_identified_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        project_state_repository.save_state(user_id, session_id, state)
        return state

    async def aclear_active_project(self, user_id: str, session_id: str) -> dict[str, Any]:
        async with key_lock(_REGISTRY, user_id, session_id):
            state = self._merge(await project_state_repository.aload_state(user_id, session_id))
            state.update(
                {
                    "active_project_id": None,
                    "active_project_root": None,
                    "project_context_locked": False,
                    "project_context_source": None,
                    "pending_project_confirmation": None,
                    "pending_followup_action": None,
                    "current_project_id": None,
                    "current_project_root": None,
                    "ai_report_summary": None,
                }
            )
            await project_state_repository.asave_state(user_id, session_id, state)
            return state

    def clear_active_project(self, user_id: str, session_id: str) -> dict[str, Any]:
        state = self.load_state(user_id, session_id)
        state.update(
            {
                "active_project_id": None,
                "active_project_root": None,
                "project_context_locked": False,
                "project_context_source": None,
                "pending_project_confirmation": None,
                "pending_followup_action": None,
                "current_project_id": None,
                "current_project_root": None,
                "ai_report_summary": None,
            }
        )
        project_state_repository.save_state(user_id, session_id, state)
        return state

    async def amark_pending_project_confirmation(
        self,
        user_id: str,
        session_id: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        async with key_lock(_REGISTRY, user_id, session_id):
            state = self._merge(await project_state_repository.aload_state(user_id, session_id))
            state["pending_project_confirmation"] = payload
            await project_state_repository.asave_state(user_id, session_id, state)
            return state

    def mark_pending_project_confirmation(
        self,
        user_id: str,
        session_id: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        state = self.load_state(user_id, session_id)
        state["pending_project_confirmation"] = payload
        project_state_repository.save_state(user_id, session_id, state)
        return state

    async def aset_pending_followup_action(
        self,
        user_id: str,
        session_id: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        async with key_lock(_REGISTRY, user_id, session_id):
            state = self._merge(await project_state_repository.aload_state(user_id, session_id))
            state["pending_followup_action"] = payload
            await project_state_repository.asave_state(user_id, session_id, state)
            return state

    def set_pending_followup_action(
        self,
        user_id: str,
        session_id: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        state = self.load_state(user_id, session_id)
        state["pending_followup_action"] = payload
        project_state_repository.save_state(user_id, session_id, state)
        return state

    async def aclear_pending_followup_action(self, user_id: str, session_id: str) -> dict[str, Any]:
        return await self.aset_pending_followup_action(user_id, session_id, None)

    def clear_pending_followup_action(self, user_id: str, session_id: str) -> dict[str, Any]:
        return self.set_pending_followup_action(user_id, session_id, None)

    def get_ai_report_summary(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        """优先从项目级缓存（跨 session）读取；若无则回退到 session 级缓存（向下兼容）。"""
        session_summary = self.load_state(user_id, session_id).get("ai_report_summary")
        if not isinstance(session_summary, dict):
            return None
        project_id = session_summary.get("project_id")
        project_root = session_summary.get("project_root")
        if project_id and project_root:
            project_entry = project_report_cache_repository.load(project_id, project_root)
            if isinstance(project_entry, dict):
                return project_entry
        # 向下兼容：返回 session 级旧缓存
        return session_summary

    async def asave_ai_report_summary_running(
        self,
        user_id: str,
        session_id: str,
        project_id: str,
        project_root: str,
    ) -> dict[str, Any]:
        # 写项目级缓存
        from multi_agent.backed.app.services.business_agent.runtime_service import BusinessAgentRuntimeService
        project_report_cache_repository.mark_running(
            project_id, project_root, BusinessAgentRuntimeService.AI_REPORT_SUMMARY_VERSION
        )
        async with key_lock(_REGISTRY, user_id, session_id):
            state = self._merge(await project_state_repository.aload_state(user_id, session_id))
            now = datetime.now().isoformat(timespec="seconds")
            state["ai_report_summary"] = {
                "status": "running",
                "project_id": project_id,
                "project_root": project_root,
                "started_at": now,
                "updated_at": now,
                "error": "",
            }
            await project_state_repository.asave_state(user_id, session_id, state)
            return state

    # 保留 sync 别名供非 async 调用处使用
    def mark_ai_report_summary_running(
        self,
        user_id: str,
        session_id: str,
        project_id: str,
        project_root: str,
    ) -> dict[str, Any]:
        # 写项目级缓存
        from multi_agent.backed.app.services.business_agent.runtime_service import BusinessAgentRuntimeService
        project_report_cache_repository.mark_running(
            project_id, project_root, BusinessAgentRuntimeService.AI_REPORT_SUMMARY_VERSION
        )
        state = self.load_state(user_id, session_id)
        now = datetime.now().isoformat(timespec="seconds")
        state["ai_report_summary"] = {
            "status": "running",
            "project_id": project_id,
            "project_root": project_root,
            "started_at": now,
            "updated_at": now,
            "error": "",
        }
        project_state_repository.save_state(user_id, session_id, state)
        return state

    async def asave_ai_report_summary(
        self,
        user_id: str,
        session_id: str,
        project_id: str,
        project_root: str,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        # 1. 写入项目级缓存（跨 session 共享）
        project_report_cache_repository.mark_ready(project_id, project_root, analysis)
        # 2. session 状态中只保留轻量绑定引用，不再存全量 analysis
        async with key_lock(_REGISTRY, user_id, session_id):
            state = self._merge(await project_state_repository.aload_state(user_id, session_id))
            state["ai_report_summary"] = {
                "status": "ready",
                "project_id": project_id,
                "project_root": project_root,
                "generation_version": analysis.get("generation_version") if isinstance(analysis, dict) else None,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "error": "",
            }
            await project_state_repository.asave_state(user_id, session_id, state)
            return state

    def save_ai_report_summary(
        self,
        user_id: str,
        session_id: str,
        project_id: str,
        project_root: str,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        # 1. 写入项目级缓存（跨 session 共享）
        project_report_cache_repository.mark_ready(project_id, project_root, analysis)
        # 2. session 状态中只保留轻量绑定引用，不再存全量 analysis
        state = self.load_state(user_id, session_id)
        state["ai_report_summary"] = {
            "status": "ready",
            "project_id": project_id,
            "project_root": project_root,
            "generation_version": analysis.get("generation_version") if isinstance(analysis, dict) else None,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "error": "",
        }
        project_state_repository.save_state(user_id, session_id, state)
        return state

    async def asave_ai_report_summary_failed(
        self,
        user_id: str,
        session_id: str,
        project_id: str,
        project_root: str,
        error: str,
    ) -> dict[str, Any]:
        # 写项目级缓存
        project_report_cache_repository.mark_failed(project_id, project_root, error)
        async with key_lock(_REGISTRY, user_id, session_id):
            state = self._merge(await project_state_repository.aload_state(user_id, session_id))
            state["ai_report_summary"] = {
                "status": "failed",
                "project_id": project_id,
                "project_root": project_root,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "error": error,
            }
            await project_state_repository.asave_state(user_id, session_id, state)
            return state


    def mark_ai_report_summary_failed(
        self,
        user_id: str,
        session_id: str,
        project_id: str,
        project_root: str,
        error: str,
    ) -> dict[str, Any]:
        # 写项目级缓存
        project_report_cache_repository.mark_failed(project_id, project_root, error)
        state = self.load_state(user_id, session_id)
        state["ai_report_summary"] = {
            "status": "failed",
            "project_id": project_id,
            "project_root": project_root,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "error": error,
        }
        project_state_repository.save_state(user_id, session_id, state)
        return state

    async def aupdate_current_project(
        self,
        user_id: str,
        session_id: str,
        project_id: str,
        project_root: str,
        question: str,
    ) -> dict[str, Any]:
        return await self.abind_active_project(
            user_id=user_id,
            session_id=session_id,
            project_id=project_id,
            project_root=project_root,
            question=question,
            source="inferred",
        )

    def update_current_project(
        self,
        user_id: str,
        session_id: str,
        project_id: str,
        project_root: str,
        question: str,
    ) -> dict[str, Any]:
        return self.bind_active_project(
            user_id=user_id,
            session_id=session_id,
            project_id=project_id,
            project_root=project_root,
            question=question,
            source="inferred",
        )


project_session_state_service = ProjectSessionStateService()
