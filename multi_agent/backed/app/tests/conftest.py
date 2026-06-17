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
