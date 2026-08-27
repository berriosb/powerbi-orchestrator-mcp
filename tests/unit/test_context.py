"""Tests for orchestrator.context - SessionContext with SQLite WAL."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from powerbi_orchestrator_mcp.orchestrator.context import (
    EngineStatus,
    SessionContext,
    SessionStore,
    Target,
    UndoEntry,
)


@pytest.fixture()
def tmp_sessions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect sessions dir to a temp directory."""
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(
        "powerbi_orchestrator_mcp.orchestrator.context.SESSIONS_DIR", sessions_dir
    )
    return sessions_dir


@pytest.fixture()
def store(tmp_sessions_dir: Path) -> SessionStore:
    """Create a SessionStore with temp directory."""
    return SessionStore()


class TestModels:
    """Tests for Pydantic models."""

    def test_engine_status_model(self) -> None:
        status = EngineStatus(name="te", available=True, version="2.0")
        assert status.name == "te"
        assert status.available is True

    def test_undo_entry_model(self) -> None:
        entry = UndoEntry(step_id="s1", description="snapshot")
        assert entry.step_id == "s1"
        assert entry.snapshot_path is None

    def test_target_model(self) -> None:
        target = Target(target_type="pbip_folder", target_ref="./out")
        assert target.target_type == "pbip_folder"
        assert target.auth_mode == "interactive"

    def test_session_context_defaults(self) -> None:
        ctx = SessionContext()
        assert len(ctx.session_id) == 32  # uuid hex
        assert ctx.target is None
        assert ctx.engines_available == {}
        assert ctx.metadata_cache == {}
        assert ctx.undo_stack == []


class TestSessionStore:
    """Tests for SQLite WAL-backed session store."""

    def test_create_session(self, store: SessionStore) -> None:
        ctx = store.create()
        assert ctx.session_id
        assert len(ctx.session_id) == 32

    def test_get_session(self, store: SessionStore) -> None:
        ctx = store.create()
        retrieved = store.get(ctx.session_id)
        assert retrieved is not None
        assert retrieved.session_id == ctx.session_id

    def test_get_nonexistent_returns_none(self, store: SessionStore) -> None:
        assert store.get("nonexistent-id") is None

    def test_update_session(self, store: SessionStore) -> None:
        ctx = store.create()
        ctx.target = Target(target_type="pbip_folder", target_ref="./out")
        ctx.engines_available["te"] = EngineStatus(name="te", available=True)
        ctx.metadata_cache["model"] = {"tables": ["Customer"]}
        ctx.undo_stack.append(UndoEntry(step_id="s1", description="snap"))
        store.update(ctx)

        retrieved = store.get(ctx.session_id)
        assert retrieved is not None
        assert retrieved.target is not None
        assert retrieved.target.target_type == "pbip_folder"
        assert "te" in retrieved.engines_available
        assert retrieved.engines_available["te"].available is True
        assert retrieved.metadata_cache["model"]["tables"] == ["Customer"]
        assert len(retrieved.undo_stack) == 1

    def test_delete_session(self, store: SessionStore) -> None:
        ctx = store.create()
        assert store.get(ctx.session_id) is not None
        result = store.delete(ctx.session_id)
        assert result is True
        assert store.get(ctx.session_id) is None

    def test_delete_nonexistent_returns_false(self, store: SessionStore) -> None:
        assert store.delete("nonexistent-id") is False

    def test_list_sessions_empty(self, store: SessionStore) -> None:
        assert store.list_sessions() == []

    def test_list_sessions(self, store: SessionStore) -> None:
        store.create()
        store.create()
        sessions = store.list_sessions()
        assert len(sessions) == 2

    def test_persistence_across_instances(
        self, tmp_sessions_dir: Path
    ) -> None:
        store1 = SessionStore()
        ctx = store1.create()

        store2 = SessionStore()
        retrieved = store2.get(ctx.session_id)
        assert retrieved is not None
        assert retrieved.session_id == ctx.session_id
