"""Stage E 监控指标（project_analysis_exploration_and_evolution_plan.md）。

方案原文："新增一个监控指标：探索 agent 最终 unresolved（尝试完所有候选仍未产出证据）
的比例。比例异常升高，说明候选池覆盖不够或 agent 的探索策略需要调整，而不是放宽真值层
校验去凑数字。"

这里只做一个足够朴素、足够可靠的实现：进程内计数器，记录"探索真正跑完一次"
（不含超时/异常——那属于基础设施不确定性，不能算进"这个指标到底能不能被发现"这件事
本身，见 Stage D 里同样的区分）以及其中有多少次最终一张 evidence_card 都没有产出。
不做跨进程持久化/时间窗口衰减——如果观测后发现需要按时间窗口/按项目维度拆分，再在此
基础上扩展，不必现在就做。

调用方约定：`_reexplore_unresolved_metrics` 每次 `discovery_confirmed=True`（探索没
超时、没异常，真正拿到了确定性结果）时调用一次 `record_attempt(resolved=...)`，和
`project_parse_cache.record_file_discovery_outcome(success=...)` 用的是同一个判断
结果，语义完全对齐：`resolved` 就是 `success`。
"""

from __future__ import annotations

import threading

from multi_agent.backed.app.infrastructure.logging.logger import logger


class ProjectExplorationMonitorService:
    _LOCK = threading.Lock()
    _TOTAL_ATTEMPTS = 0
    _UNRESOLVED_ATTEMPTS = 0

    def record_attempt(self, *, resolved: bool) -> None:
        with self._LOCK:
            ProjectExplorationMonitorService._TOTAL_ATTEMPTS += 1
            if not resolved:
                ProjectExplorationMonitorService._UNRESOLVED_ATTEMPTS += 1
            total = ProjectExplorationMonitorService._TOTAL_ATTEMPTS
            unresolved = ProjectExplorationMonitorService._UNRESOLVED_ATTEMPTS
        ratio = unresolved / total if total else 0.0
        logger.info(
            "project_analysis stage=exploration_monitor status=recorded resolved=%s "
            "total_attempts=%d unresolved_attempts=%d unresolved_ratio=%.3f",
            resolved,
            total,
            unresolved,
            ratio,
        )

    def get_unresolved_ratio(self) -> float | None:
        """返回当前累计的 unresolved 比例；从未记录过任何一次探索时返回 `None`

        （不是 0.0——"没有样本"和"样本全部成功"是两回事，调用方不应该把 None 误读成
        "从未失败过"）。
        """
        with self._LOCK:
            total = ProjectExplorationMonitorService._TOTAL_ATTEMPTS
            unresolved = ProjectExplorationMonitorService._UNRESOLVED_ATTEMPTS
        if total == 0:
            return None
        return unresolved / total

    def snapshot(self) -> dict[str, int | float | None]:
        with self._LOCK:
            total = ProjectExplorationMonitorService._TOTAL_ATTEMPTS
            unresolved = ProjectExplorationMonitorService._UNRESOLVED_ATTEMPTS
        return {
            "total_attempts": total,
            "unresolved_attempts": unresolved,
            "unresolved_ratio": (unresolved / total) if total else None,
        }

    def reset(self) -> None:
        """仅供测试使用，重置累计计数。"""
        with self._LOCK:
            ProjectExplorationMonitorService._TOTAL_ATTEMPTS = 0
            ProjectExplorationMonitorService._UNRESOLVED_ATTEMPTS = 0


project_exploration_monitor_service = ProjectExplorationMonitorService()
