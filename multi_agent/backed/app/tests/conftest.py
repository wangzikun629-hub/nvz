from pathlib import Path

import pytest

from multi_agent.backed.app.repositories.project_memory_repository import (
    project_memory_repository,
)
from multi_agent.backed.app.repositories.project_state_repository import (
    project_state_repository,
)
from multi_agent.backed.app.services.business_agent.experience_service import (
    experience_service,
)
from multi_agent.backed.app.services.project_parse_cache import ProjectParseCache


@pytest.fixture(autouse=True)
def isolate_project_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace_root = tmp_path / "workspaces"
    memory_root = tmp_path / "project_memories"
    state_root = tmp_path / "project_session_states"
    workspace_root.mkdir()
    memory_root.mkdir()
    state_root.mkdir()

    monkeypatch.setenv("PROJECT_WORKSPACE_DIR", str(workspace_root))
    monkeypatch.setattr(project_memory_repository, "_storage_root", memory_root)
    monkeypatch.setattr(project_state_repository, "_storage_root", state_root)
    monkeypatch.setattr(
        experience_service,
        "_global_experience_path",
        memory_root / "_global_experience.json",
    )


@pytest.fixture(autouse=True)
def _reset_file_discovery_cache():
    """Stage C/D/E（project_analysis_exploration_and_evolution_plan.md）：

    `project_parse_cache` 的文件发现缓存（`_FILE_DISCOVERY_SUCCESS_CACHE` /
    `_FILE_DISCOVERY_FAILURE_CACHE`）是进程内单例的类级别共享字典。多个测试文件
    （`test_stage_c_reexploration.py` / `test_stage_d_discovery_cache.py` /
    `test_stage_e_exploration_monitor.py`）都用同一个字面量假路径
    `Path("/tmp/stage_c_fixture_root")` + 同一个指标 `silva_total_ratio_percent`
    作为编排级测试输入。2026-07-03 review 修复"缓存命中不应该刷新失败 TTL"这个
    bug 之后，`_reexplore_unresolved_metrics` 在调用 `record_file_discovery_
    outcome`/`record_attempt` 之前会先探测一次真实缓存里是否已有结果——如果前一个
    测试往这个 (root, metrics) 组合的真实缓存里写过东西（多数测试确实会触发
    `record_file_discovery_outcome` 的真实实现，只 mock 了 `discover_file_role_
    assignments` 本身），后一个测试会"看到"这份残留缓存而断言失败，属于测试之间
    没有隔离，不是被测代码的问题。这里在每个用例前后清空文件发现缓存两张表，
    确保用例互不影响。
    """
    ProjectParseCache._FILE_DISCOVERY_SUCCESS_CACHE.clear()
    ProjectParseCache._FILE_DISCOVERY_FAILURE_CACHE.clear()
    yield
    ProjectParseCache._FILE_DISCOVERY_SUCCESS_CACHE.clear()
    ProjectParseCache._FILE_DISCOVERY_FAILURE_CACHE.clear()
