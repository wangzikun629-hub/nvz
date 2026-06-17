from pathlib import Path

from multi_agent.backed.app.harness import run_harness
from multi_agent.backed.app.harness.runners import business_agent_runner
from multi_agent.backed.app.infrastructure.tools.local import project_reader
from multi_agent.backed.app.services import project_locator_service as locator_module
from multi_agent.backed.app.services.project_locator_service import ProjectCandidate, ProjectLocatorService


def test_resolve_project_root_uses_cache_when_mount_is_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    cache_root = tmp_path / "cache"
    cached_project = cache_root / "source-a" / "PROJ001"
    cached_project.mkdir(parents=True)
    (cached_project / "samplelist").write_text("S1\n", encoding="utf-8")

    monkeypatch.setenv("PROJECT_SFTP_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("PROJECT_SFTP_OFFLINE", "1")
    monkeypatch.setenv("PROJECT_BASE_DIRS", "sftp://user@example.invalid/data/Result")

    resolved = project_reader.resolve_project_root(
        "PROJ001",
        r"\\RaiDrive-ASUS\SFTP\Result\PROJ001",
    )

    assert resolved == cached_project.resolve()


def test_cached_sop_root_preserves_source_identity(tmp_path: Path, monkeypatch):
    cache_root = tmp_path / "cache"
    sop_url = "sftp://user@example.invalid/data/Snakemake_Sop"
    monkeypatch.setenv("PROJECT_SFTP_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("PROJECT_SFTP_OFFLINE", "1")
    monkeypatch.setattr(project_reader, "_get_sop_base_dirs", lambda: [sop_url])

    location = project_reader._remote_sop_workflow_locations(sop_url, "CUTTag")[0]
    cached_sop = project_reader._sftp_sop_cache_root(location, "CUTTag")
    workflow_file = cached_sop / "Filter" / "cutadapt_stat.py"
    workflow_file.parent.mkdir(parents=True)
    workflow_file.write_text("adapter_rate = adapter_count / total_reads_raw\n", encoding="utf-8")

    project_root = tmp_path / "PROJ001"
    project_root.mkdir()
    roots = project_reader._sop_workflow_roots(
        project_root,
        {"Sequencing": "CUTTag"},
    )

    assert cached_sop in roots
    assert "Snakemake_Sop" in str(cached_sop)


def test_sftp_mirror_skips_unreadable_files(tmp_path: Path, monkeypatch):
    class RemoteEntry:
        def __init__(self, filename: str):
            self.filename = filename
            self.st_mode = 0o100644

    class FakeSftp:
        def listdir_attr(self, remote_path: str):
            return [RemoteEntry("blocked.py"), RemoteEntry("readable.py")]

    class FakeClient:
        def close(self):
            return None

    location = project_reader.SftpLocation(
        host="example.invalid",
        port=22,
        username="user",
        password=None,
        remote_path="/workflow",
        url="sftp://user@example.invalid/workflow",
    )
    monkeypatch.setattr(project_reader, "_open_sftp", lambda value: (FakeClient(), FakeSftp()))
    monkeypatch.setattr(project_reader, "_remote_is_dir", lambda sftp, path: True)
    monkeypatch.setattr(project_reader, "_remote_report_roots", lambda sftp, root, project_id: [])

    def fake_download(sftp, remote_file, remote_root, local_root):
        if remote_file.endswith("blocked.py"):
            raise PermissionError("denied")
        target = local_root / "readable.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok\n", encoding="utf-8")
        return target

    monkeypatch.setattr(project_reader, "_download_remote_file", fake_download)
    target = tmp_path / "mirror"

    mirrored = project_reader._mirror_sftp_project(
        location,
        "CUTTag",
        local_project_root=target,
    )

    assert mirrored == target
    assert (target / "readable.py").exists()


def test_business_harness_resolves_root_before_runtime(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "PROJ001"
    project_root.mkdir()
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        business_agent_runner,
        "resolve_project_root",
        lambda project_id, project_root_override=None: project_root,
    )
    monkeypatch.setattr(business_agent_runner.business_agent_runtime_service, "run", fake_run)

    result = business_agent_runner.BusinessAgentHarnessRunner().run(
        {
            "id": "cached-project",
            "target": "business_agent",
            "project_id": "PROJ001",
            "question": "Analyze the project",
        }
    )

    assert captured["project_root"] == str(project_root)
    assert result["_harness"]["project_root"] == str(project_root)


def test_explicit_project_id_overrides_locked_session(monkeypatch):
    service = ProjectLocatorService()
    explicit = ProjectCandidate("PROJ001", "D:/cache/PROJ001", ("S1",))
    monkeypatch.setattr(service, "_find_exact_project_candidate", lambda project_id: explicit)
    monkeypatch.setattr(
        locator_module.project_session_state_service,
        "load_state",
        lambda user_id, session_id: {
            "project_context_locked": True,
            "active_project_id": "OLD_PROJECT",
            "active_project_root": r"\\RaiDrive-ASUS\SFTP\Result\OLD_PROJECT",
        },
    )
    monkeypatch.setattr(
        locator_module.project_context_intent_service,
        "classify",
        lambda question, state: "continue_project",
    )

    result = service.identify_project(
        question="Analyze the requested project",
        project_id="PROJ001",
        user_id="u1",
        session_id="s1",
    )

    assert result["matched_by"] == "project_id"
    assert result["project_id"] == "PROJ001"


def test_harness_project_root_override_supports_single_project_path():
    cases = [{"project_id": "PROJ001"}, {"project_id": "PROJ001"}]

    run_harness._apply_project_root_overrides(cases, [r"D:\data\PROJ001"])

    assert all(case["project_root"] == r"D:\data\PROJ001" for case in cases)
