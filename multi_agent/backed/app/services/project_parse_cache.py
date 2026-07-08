from __future__ import annotations

import copy
import threading
from pathlib import Path
from time import perf_counter
from typing import Any

from multi_agent.backed.app.infrastructure.logging.logger import logger


class ProjectParseCache:
    # ── File-parse cache ──────────────────────────────────────────────────
    _EVIDENCE_PARSE_WORKERS = 4
    _FILE_PARSE_CACHE: dict[tuple[str, int, str], dict[str, Any]] = {}
    _FILE_PARSE_CACHE_MAX_ENTRIES = 512
    _FILE_PARSE_CACHE_LOCK = threading.Lock()

    # ── Project-context cache ─────────────────────────────────────────────
    _PROJECT_CONTEXT_CACHE_TTL_SECONDS = 120.0
    _PROJECT_CONTEXT_CACHE_MAX_ENTRIES = 128
    _PROJECT_CONTEXT_CACHE: dict[tuple[str, bool], tuple[float, dict[str, Any]]] = {}
    _PROJECT_CONTEXT_IN_FLIGHT: dict[tuple[str, bool], threading.Event] = {}
    _PROJECT_CONTEXT_LOCK = threading.Lock()

    # ── File-discovery cache（Phase 1，见 project_analysis_agent_upgrade_plan.md；
    # 缓存语义 Stage D 改造，见 project_analysis_exploration_and_evolution_plan.md）──
    # 按项目根目录 mtime + 目标指标集合缓存 file_role_assignment 候选列表，
    # 避免每次提问都重新触发探索（关键词表命中的路径不经过这里，见调用方）。
    #
    # 已知次要项（project_analysis_phase1.5_auto_promotion_revision.md §14）：缓存粒度是
    # "整个项目根目录"，任何单个文件被 touch（mtime 变化）就会让整个项目的
    # file_role_assignment 缓存失效，即使实际改动和候选文件毫无关系。当前项目规模下足够用，
    # 先记一笔；如果观测到失效过于频繁（比如项目目录里有频繁更新的日志/进度文件），再考虑
    # 细化为按子目录或按目标指标分片缓存，不必现在就做。
    #
    # Stage D 修订（2026-07-03）：改造前这里"探索返回了候选"就无条件永久缓存，不管这批
    # 候选后来解析/校验有没有真正产出 evidence_card——一次探索失误（比如 agent 选错文件、
    # 目标指标其实不在项目里）会被当成"确定结果"缓存到项目根目录 mtime 不变为止，后续同类
    # 请求永远拿到同一个错误/空结果，不会重试。现在拆成两张表：
    #   - `_FILE_DISCOVERY_SUCCESS_CACHE`：只在调用方确认这批候选最终至少产出了 1 张
    #     evidence_card 后才写入（`record_file_discovery_outcome(..., success=True)`），
    #     和之前一样按 mtime 隐式失效，长期有效。
    #   - `_FILE_DISCOVERY_FAILURE_CACHE`：调用方确认这批候选没有产出任何 evidence_card
    #     时写入一个短 TTL 的"最近失败"时间戳（`success=False`），在 TTL 内直接短路返回
    #     空列表，避免同一个必然失败的场景被反复触发探索；TTL 过期后自动允许重试，
    #     不会无限期卡死后续请求。
    # `discover_file_role_assignments()` 本身不再在探索完成后自动写入这两张表——它没有
    # 下游解析/校验结果的可见性，无法判断"探索出来的候选"是否真的有用；写入职责交给
    # 明确知道结果的调用方（目前是 `ProjectAnalysisService._reexplore_unresolved_metrics`）。
    _FILE_DISCOVERY_CACHE_MAX_ENTRIES = 256
    _FILE_DISCOVERY_FAILURE_TTL_SECONDS = 300.0
    _FILE_DISCOVERY_SUCCESS_CACHE: dict[tuple[str, int, tuple[str, ...], str], list[dict[str, Any]]] = {}
    _FILE_DISCOVERY_FAILURE_CACHE: dict[tuple[str, int, tuple[str, ...], str], float] = {}
    _FILE_DISCOVERY_LOCK = threading.Lock()

    # ── Code-semantics cache（Phase 1.1，见 project_analysis_agent_upgrade_plan.md 2.1 节）──
    # 按脚本文件路径 + mtime 缓存 formula_hint；同一份 SOP 脚本被多个项目复用时不用重复解析。
    _CODE_SEMANTICS_CACHE_MAX_ENTRIES = 512
    _CODE_SEMANTICS_CACHE: dict[tuple[str, int], dict[str, Any]] = {}
    _CODE_SEMANTICS_LOCK = threading.Lock()

    # ── File-parse helpers ────────────────────────────────────────────────

    def _cache_key(self, file_path: Path, parser_kind: str) -> tuple[str, int, str]:
        stat = file_path.stat()
        return (str(file_path.resolve()), stat.st_mtime_ns, parser_kind)

    @staticmethod
    def _clone_payload(payload: dict[str, Any]) -> dict[str, Any]:
        if not payload:
            return {}
        cloned: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                cloned[key] = dict(value)
            elif isinstance(value, list):
                cloned[key] = [dict(item) if isinstance(item, dict) else item for item in value]
            else:
                cloned[key] = value
        return cloned

    def _get_cached_parse(self, file_path: Path, parser_kind: str) -> dict[str, Any] | None:
        with self._FILE_PARSE_CACHE_LOCK:
            cached = self._FILE_PARSE_CACHE.get(self._cache_key(file_path, parser_kind))
        if cached is None:
            return None
        return self._clone_payload(cached)

    def _set_cached_parse(self, file_path: Path, parser_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        cloned = self._clone_payload(payload)
        cache_key = self._cache_key(file_path, parser_kind)
        resolved_path = cache_key[0]
        with self._FILE_PARSE_CACHE_LOCK:
            stale_keys = [
                key
                for key in self._FILE_PARSE_CACHE
                if key[0] == resolved_path and key[2] == parser_kind and key != cache_key
            ]
            for key in stale_keys:
                self._FILE_PARSE_CACHE.pop(key, None)
            self._FILE_PARSE_CACHE[cache_key] = cloned
            while len(self._FILE_PARSE_CACHE) > self._FILE_PARSE_CACHE_MAX_ENTRIES:
                self._FILE_PARSE_CACHE.pop(next(iter(self._FILE_PARSE_CACHE)))
        return self._clone_payload(cloned)

    # ── Project-context cache helpers ─────────────────────────────────────

    def _project_context_key(self, root: Path, include_html_body: bool) -> tuple[str, bool]:
        return (str(root.resolve()), include_html_body)

    @staticmethod
    def _clone_project_context(payload: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(payload)

    def _get_cached_project_context(self, root: Path, include_html_body: bool) -> dict[str, Any] | None:
        key = self._project_context_key(root, include_html_body)
        now = perf_counter()
        with self._PROJECT_CONTEXT_LOCK:
            cached = self._PROJECT_CONTEXT_CACHE.get(key)
            if cached is None:
                return None
            cached_at, payload = cached
            if now - cached_at > self._PROJECT_CONTEXT_CACHE_TTL_SECONDS:
                self._PROJECT_CONTEXT_CACHE.pop(key, None)
                return None
            return self._clone_project_context(payload)

    def _set_cached_project_context(
        self,
        root: Path,
        include_html_body: bool,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        cloned = self._clone_project_context(payload)
        with self._PROJECT_CONTEXT_LOCK:
            now = perf_counter()
            expired = [
                key
                for key, (cached_at, _) in self._PROJECT_CONTEXT_CACHE.items()
                if now - cached_at > self._PROJECT_CONTEXT_CACHE_TTL_SECONDS
            ]
            for key in expired:
                self._PROJECT_CONTEXT_CACHE.pop(key, None)
            self._PROJECT_CONTEXT_CACHE[self._project_context_key(root, include_html_body)] = (now, cloned)
            while len(self._PROJECT_CONTEXT_CACHE) > self._PROJECT_CONTEXT_CACHE_MAX_ENTRIES:
                self._PROJECT_CONTEXT_CACHE.pop(next(iter(self._PROJECT_CONTEXT_CACHE)))
        return self._clone_project_context(cloned)

    # ── File-discovery cache helpers ───────────────────────────────────────

    @staticmethod
    def _file_discovery_key(
        root: Path, target_metrics: list[str]
    ) -> tuple[str, int, tuple[str, ...], str]:
        resolved = str(root.resolve())
        try:
            mtime_ns = root.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        metrics_key = tuple(sorted({str(m or "").strip().lower() for m in target_metrics if str(m or "").strip()}))
        # Stage G-2 P2 修复（2026-07-07 code review）：`EXPLORATION_ALWAYS_ON_ENABLED`
        # 改变了 `_exploration_agent_augment()` 对 explore_with_agent 的调用方式
        # （对哪些指标触发、prompt 里带不带确认优先线索），如果不纳入缓存 key，
        # 先在 flag 关闭时缓存过的成功/失败结果，会在之后打开 flag 做真实回放时
        # 被直接命中，Explorer 全量 target_metrics 分支根本不会跑，回放数据不可信。
        # 这里直接读当前的 settings 值算进 key，不改 get_cached_file_discovery()/
        # record_file_discovery_outcome() 的调用签名——两者在同一个进程里读同一个
        # settings 单例，读写双方天然用同一个 flag 状态算 key，不需要每个调用点
        # （project_analysis_service.py 里有 5 处）都显式透传这个参数，避免读写
        # 用不同 key 导致缓存错位这个更隐蔽的问题。
        from multi_agent.backed.app.config.settings import settings

        exploration_mode = "always_on" if settings.EXPLORATION_ALWAYS_ON_ENABLED else "default"
        return (resolved, mtime_ns, metrics_key, exploration_mode)

    def get_cached_file_discovery(
        self, root: Path, target_metrics: list[str]
    ) -> list[dict[str, Any]] | None:
        """返回缓存的探索候选，仅在"已确认结果"时命中：

        - 曾被 `record_file_discovery_outcome(success=True)` 确认过至少产出 1 张
          evidence_card 的候选列表——长期有效（随 mtime 隐式失效）。
        - 曾被确认 `success=False`（探索完成但没有产出任何 evidence_card）且仍在
          短 TTL 窗口内——直接返回空列表，短路掉这一轮必然复现的失败探索。
        - 其余情况（从未探索过，或失败 TTL 已过期）返回 `None`，调用方需要真正
          触发一次探索。
        """
        key = self._file_discovery_key(root, target_metrics)
        with self._FILE_DISCOVERY_LOCK:
            success_cached = self._FILE_DISCOVERY_SUCCESS_CACHE.get(key)
            failure_ts = self._FILE_DISCOVERY_FAILURE_CACHE.get(key)
        if success_cached is not None:
            return [dict(item) for item in success_cached]
        if failure_ts is not None:
            if (perf_counter() - failure_ts) < self._FILE_DISCOVERY_FAILURE_TTL_SECONDS:
                return []
            with self._FILE_DISCOVERY_LOCK:
                self._FILE_DISCOVERY_FAILURE_CACHE.pop(key, None)
        return None

    def record_file_discovery_outcome(
        self,
        root: Path,
        target_metrics: list[str],
        assignments: list[dict[str, Any]],
        *,
        success: bool,
    ) -> None:
        """由调用方在解析/校验结束后回填这一轮探索的真实结果。

        `success=True` 表示这批 `assignments` 最终至少产出了 1 张 evidence_card，
        长期缓存复用；`success=False` 表示探索完成但没有产出任何 evidence_card，
        只写一个短 TTL 的失败标记（不缓存 `assignments` 本身，TTL 内直接短路为
        空列表），既不会让同一个必然失败的场景被反复触发探索，也不会无限期卡住
        后续同类请求——TTL 过期后自动允许重试。
        """
        key = self._file_discovery_key(root, target_metrics)
        if success:
            cloned = [dict(item) for item in assignments]
            with self._FILE_DISCOVERY_LOCK:
                self._FILE_DISCOVERY_FAILURE_CACHE.pop(key, None)
                self._FILE_DISCOVERY_SUCCESS_CACHE[key] = cloned
                while len(self._FILE_DISCOVERY_SUCCESS_CACHE) > self._FILE_DISCOVERY_CACHE_MAX_ENTRIES:
                    self._FILE_DISCOVERY_SUCCESS_CACHE.pop(next(iter(self._FILE_DISCOVERY_SUCCESS_CACHE)))
        else:
            with self._FILE_DISCOVERY_LOCK:
                self._FILE_DISCOVERY_FAILURE_CACHE[key] = perf_counter()
                while len(self._FILE_DISCOVERY_FAILURE_CACHE) > self._FILE_DISCOVERY_CACHE_MAX_ENTRIES:
                    self._FILE_DISCOVERY_FAILURE_CACHE.pop(next(iter(self._FILE_DISCOVERY_FAILURE_CACHE)))

    # ── Code-semantics cache helpers ───────────────────────────────────────

    @staticmethod
    def _code_semantics_key(script_path: Path) -> tuple[str, int]:
        resolved = str(script_path.resolve())
        try:
            mtime_ns = script_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        return (resolved, mtime_ns)

    def get_cached_formula_hint(self, script_path: Path) -> dict[str, Any] | None:
        """按入口脚本自身 `(resolved path, mtime)` 查缓存。

        Stage G-3 七次修订（code review P2）：入口脚本自己的 mtime 没变不代表缓存
        一定还有效——Stage G-3 六次修订允许一条候选通过 `source_path` 归因到同目录
        的 helper 文件后，仍然缓存在"入口脚本"这个槽位下（`FormulaHint.script_path`
        指向 helper 的真实路径，但缓存 key 只看入口脚本）。如果之后 helper 文件改了、
        入口脚本本身没改，入口脚本的 `(resolved, mtime)` key 不变，缓存会继续命中，
        返回 helper 改动前的旧公式——这是"陈旧"问题，和六次修订解决的"丢失"问题不是
        一回事。这里在命中缓存后，额外校验 `set_cached_formula_hint` 写入时记录的
        依赖文件（`_dep_mtimes`，见该函数）的当前 mtime 是否和写入时一致；任何一个
        依赖文件的 mtime 变了（或者已经不存在），就当缓存失效，直接淘汰这条缓存并
        返回 None，让调用方重新触发一次 agent 会话。
        """
        key = self._code_semantics_key(script_path)
        with self._CODE_SEMANTICS_LOCK:
            cached = self._CODE_SEMANTICS_CACHE.get(key)
        if cached is None:
            return None
        dep_mtimes = cached.get("_dep_mtimes") or []
        for dep_path_str, dep_mtime_ns in dep_mtimes:
            try:
                current_mtime_ns = Path(dep_path_str).stat().st_mtime_ns
            except OSError:
                current_mtime_ns = None
            if current_mtime_ns != dep_mtime_ns:
                with self._CODE_SEMANTICS_LOCK:
                    # 依赖文件已经变了，这条缓存整体不可信，直接淘汰，不留半失效状态。
                    self._CODE_SEMANTICS_CACHE.pop(key, None)
                return None
        result = dict(cached)
        result.pop("_dep_mtimes", None)
        return result

    def set_cached_formula_hint(
        self,
        script_path: Path,
        formula_hint: dict[str, Any],
        *,
        dependency_paths: list[Path] | None = None,
    ) -> dict[str, Any]:
        """写入按入口脚本 `(resolved path, mtime)` 分槽位的 formula_hint 缓存。

        `dependency_paths`：这次结果里除了入口脚本自身之外，实际依赖的其它文件
        （典型场景是 Stage G-3 的 helper 文件归因——某条候选通过 `source_path`
        指向同目录的 helper，缓存槽位仍然是入口脚本，但内容依赖 helper 的当前状态）。
        这里记录下每个依赖文件写入时的 mtime，`get_cached_formula_hint` 命中时会
        重新核对这些 mtime，任何一个变了就整体判定缓存失效。和入口脚本自身重复的
        路径会被跳过（入口脚本的 mtime 已经是缓存 key 的一部分，不需要再存一份）。
        """
        key = self._code_semantics_key(script_path)
        cloned = dict(formula_hint)
        entry_resolved = key[0]
        dep_mtimes: list[tuple[str, int]] = []
        for dep_path in dependency_paths or []:
            try:
                resolved_dep = str(dep_path.resolve())
            except OSError:
                continue
            if resolved_dep == entry_resolved:
                continue
            try:
                dep_mtime_ns = dep_path.stat().st_mtime_ns
            except OSError:
                continue
            dep_mtimes.append((resolved_dep, dep_mtime_ns))
        if dep_mtimes:
            cloned["_dep_mtimes"] = dep_mtimes
        with self._CODE_SEMANTICS_LOCK:
            self._CODE_SEMANTICS_CACHE[key] = cloned
            while len(self._CODE_SEMANTICS_CACHE) > self._CODE_SEMANTICS_CACHE_MAX_ENTRIES:
                self._CODE_SEMANTICS_CACHE.pop(next(iter(self._CODE_SEMANTICS_CACHE)))
        result = dict(cloned)
        result.pop("_dep_mtimes", None)
        return result

    def build_cached_project_context(self, root: Path, include_html_body: bool) -> dict[str, Any]:
        """Build or return cached project context, with in-flight deduplication."""
        # Deferred import to avoid circular dependency: context builder imports this module.
        from multi_agent.backed.app.services.project_context_builder_service import (
            project_context_builder_service,
        )
        from multi_agent.backed.app.services.business_agent.project_snapshot_service import (
            project_snapshot_service,
        )

        key = self._project_context_key(root, include_html_body)
        context_snapshot_key = project_snapshot_service.build_context_snapshot_key(
            root=str(root.resolve()),
            include_html_body=include_html_body,
        )
        while True:
            cached = self._get_cached_project_context(root, include_html_body)
            if cached is not None:
                logger.info(
                    "project_analysis stage=build_context_cache root=%s include_html_body=%s status=hit",
                    str(root),
                    include_html_body,
                )
                return cached

            with self._PROJECT_CONTEXT_LOCK:
                event = self._PROJECT_CONTEXT_IN_FLIGHT.get(key)
                if event is None:
                    event = threading.Event()
                    self._PROJECT_CONTEXT_IN_FLIGHT[key] = event
                    owner = True
                else:
                    owner = False

            if owner:
                try:
                    snapshot_payload = project_snapshot_service.get(context_snapshot_key)
                    if snapshot_payload is not None:
                        logger.info(
                            "project_analysis stage=context_snapshot root=%s include_html_body=%s snapshot=hit",
                            str(root),
                            include_html_body,
                        )
                        payload = snapshot_payload.get("project_context", {}) or {}
                    else:
                        logger.info(
                            "project_analysis stage=context_snapshot root=%s include_html_body=%s snapshot=miss",
                            str(root),
                            include_html_body,
                        )
                        payload = project_context_builder_service.build_project_context(
                            root, include_html_body=include_html_body
                        )
                        project_snapshot_service.set(
                            context_snapshot_key,
                            {
                                "project_context": payload,
                                "experiment_design": payload.get("experiment_design", {}),
                                "evidence_catalog_summary": payload.get("evidence_catalog_summary", {}),
                            },
                        )
                    return self._set_cached_project_context(root, include_html_body, payload)
                finally:
                    with self._PROJECT_CONTEXT_LOCK:
                        self._PROJECT_CONTEXT_IN_FLIGHT.pop(key, None)
                        event.set()

            logger.info(
                "project_analysis stage=build_context_cache root=%s include_html_body=%s status=wait_in_flight",
                str(root),
                include_html_body,
            )
            event.wait(timeout=30.0)


project_parse_cache = ProjectParseCache()
