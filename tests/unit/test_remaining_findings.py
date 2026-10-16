from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from powerbi_orchestrator_mcp.orchestrator.authorization import LOCAL_IDENTITY
from powerbi_orchestrator_mcp.plugins import (
    PLUGINS_ENV_VAR,
    directory_is_private,
    load_plugins,
    plugins_enabled,
)
from powerbi_orchestrator_mcp.tools.sync_git_to_workspace import _is_inside


def _plan(store: Any, plan_id: str = "p1") -> Any:
    from powerbi_orchestrator_mcp.orchestrator.plan_models import (
        EstimatedChanges,
        Plan,
    )

    return Plan(
        id=plan_id,
        yaml=f"plan {plan_id}",
        steps=[],
        rollback_steps=[],
        risk_score=0.0,
        estimated_changes=EstimatedChanges(),
    )


class TestPlanOwnership:
    @pytest.fixture(autouse=True)
    def _store(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        from powerbi_orchestrator_mcp.orchestrator import plan_store as ps
        from powerbi_orchestrator_mcp.orchestrator import server as server_mod
        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        monkeypatch.setattr(ps, "PLAN_DIR", tmp_path)
        monkeypatch.setattr(ps, "PLAN_DB", tmp_path / "plans.db")
        monkeypatch.setattr(server_mod, "_plan_store", PlanStore(db_path=tmp_path / "plans.db"))
        server_mod._reset_server_state()
        set_current_user(None)
        yield
        server_mod._reset_server_state()
        set_current_user(None)

    def test_plan_is_visible_to_its_owner(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        set_current_user({"oid": "alice", "scp": "Tools.Read Tools.Write"})
        store = PlanStore()
        store.put(_plan(store), owner="alice")

        assert store.get("p1", owner="alice") is not None

    def test_other_principal_cannot_read_the_plan(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore

        store = PlanStore()
        store.put(_plan(store), owner="alice")

        assert store.get("p1", owner="mallory") is None

    def test_unauthenticated_caller_cannot_read_a_remote_plan(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore

        store = PlanStore()
        store.put(_plan(store), owner="alice")

        assert store.get("p1") is None

    def test_local_plans_stay_visible_over_stdio(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore

        store = PlanStore()
        store.put(_plan(store))

        assert store.get("p1") is not None
        assert store.owner_of("p1") == LOCAL_IDENTITY

    def test_list_all_is_scoped_to_owner(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore

        store = PlanStore()
        store.put(_plan(store, "alice-1"), owner="alice")
        store.put(_plan(store, "alice-2"), owner="alice")
        store.put(_plan(store, "mallory-1"), owner="mallory")

        assert {p.id for p in store.list_all(owner="alice")} == {"alice-1", "alice-2"}

    def test_count_is_scoped_to_owner(self) -> None:
        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        store = PlanStore()
        store.put(_plan(store, "a1"), owner="alice")
        store.put(_plan(store, "m1"), owner="mallory")

        set_current_user({"oid": "alice", "scp": "Tools.Read Tools.Write"})
        assert store.count() == 1

    def test_schema_migrates_databases_without_owner(self, tmp_path: Path) -> None:
        import sqlite3

        legacy = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(legacy))
        conn.execute(
            """
            CREATE TABLE plans (
                plan_id TEXT PRIMARY KEY,
                plan_yaml TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                rollback_steps_json TEXT NOT NULL DEFAULT '[]',
                risk_score REAL NOT NULL DEFAULT 0.0,
                estimated_changes_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                target_id TEXT
            )
            """
        )
        conn.execute("INSERT INTO plans VALUES ('old', 'y', '[]', '[]', 0.0, '{}', 'c', NULL)")
        conn.commit()
        conn.close()

        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore

        store = PlanStore(db_path=legacy)
        store.count()
        cols = {r[1] for r in sqlite3.connect(str(legacy)).execute("PRAGMA table_info(plans)")}

        assert "owner" in cols
        assert store.get("old") is not None

    def test_apply_plan_refuses_a_plan_owned_by_someone_else(self) -> None:
        import asyncio

        from powerbi_orchestrator_mcp.orchestrator import server as server_mod
        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        store = PlanStore()
        store.put(_plan(store), owner="alice")
        server_mod._reset_server_state()

        set_current_user({"oid": "mallory", "scp": "Tools.Read Tools.Write"})
        result = asyncio.run(server_mod.apply_plan("p1", dry_run=False))

        assert result.result == "failed"
        assert "not owned" in (result.failed_step or {}).get("error_message", "")

    def test_apply_plan_allows_the_owner(self) -> None:
        import asyncio

        from powerbi_orchestrator_mcp.orchestrator import server as server_mod
        from powerbi_orchestrator_mcp.orchestrator.plan_store import PlanStore
        from powerbi_orchestrator_mcp.orchestrator.transport import set_current_user

        store = PlanStore()
        store.put(_plan(store), owner="alice")
        server_mod._reset_server_state()

        set_current_user({"oid": "alice", "scp": "Tools.Read Tools.Write"})
        result = asyncio.run(server_mod.apply_plan("p1", dry_run=True))

        assert result.result != "failed"


class TestGitSymlinkRefused:
    def test_symlink_out_of_repo_is_not_inside(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "a").mkdir(parents=True)
        outside = tmp_path / "secret"
        outside.write_text("classified", encoding="utf-8")
        link = repo / "a" / "leak"
        link.symlink_to(outside)

        assert _is_inside(link, repo) is False

    def test_regular_file_inside_repo_is_allowed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        target = repo / "model.pbip"
        target.write_text("{}", encoding="utf-8")

        assert _is_inside(target, repo) is True

    @pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
    def test_sync_skips_a_symlinked_pbip(self, tmp_path: Path) -> None:
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        secret = tmp_path / "secret"
        secret.write_text("Server=prod;Password=hunter2", encoding="utf-8")
        (repo / "x.pbip").mkdir()
        (repo / "x.pbip" / "leak.tmdl").symlink_to(secret)
        (repo / "x.pbip" / "real.tmdl").write_text("table Sales", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "x"], check=True)

        from powerbi_orchestrator_mcp.tools.sync_git_to_workspace import (
            _list_pbip_files,
        )

        files = _list_pbip_files(repo, "HEAD")
        assert not any(f.is_symlink() for f in files)


class TestPluginOptIn:
    @pytest.fixture(autouse=True)
    def _clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PLUGINS_ENV_VAR, raising=False)

    def test_disabled_by_default(self) -> None:
        assert plugins_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
    def test_truthy_values_enable(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PLUGINS_ENV_VAR, value)

        assert plugins_enabled() is True

    def test_existing_plugins_are_not_loaded_when_disabled(self, tmp_path: Path) -> None:
        (tmp_path / "evil.py").write_text("raise SystemExit(1)", encoding="utf-8")

        registry = load_plugins(tmp_path)

        assert registry.loaded == []
        assert registry.has_errors()
        assert PLUGINS_ENV_VAR in registry.errors[0].error

    def test_enabled_plugins_load(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PLUGINS_ENV_VAR, "1")
        (tmp_path / "rules.py").write_text("BPA_RULES = []\n", encoding="utf-8")

        registry = load_plugins(tmp_path)

        assert [p.name for p in registry.loaded] == ["rules"]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes are not meaningful")
    def test_world_writable_directory_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PLUGINS_ENV_VAR, "1")
        (tmp_path / "rules.py").write_text("BPA_RULES = []\n", encoding="utf-8")
        os.chmod(tmp_path, 0o777)

        registry = load_plugins(tmp_path)

        assert registry.loaded == []
        assert "world-writable" in registry.errors[0].error

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes are not meaningful")
    def test_group_writable_directory_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PLUGINS_ENV_VAR, "1")
        (tmp_path / "rules.py").write_text("BPA_RULES = []\n", encoding="utf-8")
        os.chmod(tmp_path, 0o770)

        assert load_plugins(tmp_path).loaded == []

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes are not meaningful")
    def test_private_directory_is_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PLUGINS_ENV_VAR, "1")
        (tmp_path / "rules.py").write_text("BPA_RULES = []\n", encoding="utf-8")
        os.chmod(tmp_path, 0o700)

        assert len(load_plugins(tmp_path).loaded) == 1

    @pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
    def test_symlinked_plugin_file_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PLUGINS_ENV_VAR, "1")
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        os.chmod(plugin_dir, 0o700)
        outside = tmp_path / "evil.py"
        outside.write_text("raise SystemExit(1)", encoding="utf-8")
        (plugin_dir / "linked.py").symlink_to(outside)

        registry = load_plugins(plugin_dir)

        assert registry.loaded == []
        assert "symlinked" in registry.errors[0].error

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes are not meaningful")
    def test_directory_is_private_helper(self, tmp_path: Path) -> None:
        os.chmod(tmp_path, 0o700)
        assert directory_is_private(tmp_path) is True

        os.chmod(tmp_path, 0o707)
        assert directory_is_private(tmp_path) is False

    def test_health_surfaces_disabled_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(PLUGINS_ENV_VAR, raising=False)
        monkeypatch.setenv("PBI_ORCHESTRATOR_HOME", str(tmp_path))
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "rules.py").write_text("BPA_RULES = []\n", encoding="utf-8")

        from powerbi_orchestrator_mcp.tools.powerbi_health import powerbi_health

        snapshot = asyncio.run(powerbi_health())
        plugins_check = next(c for c in snapshot["checks"] if c["name"] == "plugins")

        assert plugins_check["ok"] is False
        assert PLUGINS_ENV_VAR in plugins_check["detail"]


class TestAuditEnvVarStrictness:
    def test_misspelled_env_var_is_no_longer_honoured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from powerbi_orchestrator_mcp.orchestrator import audit as audit_mod

        monkeypatch.delenv("PBI_ORCHESTRATOR_AUDIT_SECRET", raising=False)
        monkeypatch.setenv("PBI_ORCHestrATOR_AUDIT_SECRET", "legacy")

        assert not hasattr(audit_mod, "_LEGACY_HMAC_KEY_ENV")

    def test_correct_env_var_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from powerbi_orchestrator_mcp.orchestrator.audit import _get_hmac_key

        monkeypatch.setenv("PBI_ORCHESTRATOR_AUDIT_SECRET", "right")

        assert _get_hmac_key() == b"right"
