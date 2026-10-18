"""Real SQLite transaction/handle regressions; no GC or platform timing assumptions."""
from __future__ import annotations

import sqlite3

import pytest

from hya import engine as engine_module
from hya.engine import Engine, NotFoundError, _now


@pytest.fixture
def tracked_connections(monkeypatch):
    original_connect = sqlite3.connect
    opened = []

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            super().close()
            self.closed = True

    def connect(*args, **kwargs):
        kwargs["factory"] = TrackedConnection
        connection = original_connect(*args, **kwargs)
        # Retain strong references: relying on object destruction cannot make
        # these checks pass, even on an implementation with immediate GC.
        opened.append(connection)
        return connection

    monkeypatch.setattr(engine_module.sqlite3, "connect", connect)
    yield opened
    # Also release handles if a regression makes a test fail on Windows.
    for connection in opened:
        if not connection.closed:
            connection.close()


def assert_closed(connections):
    assert connections
    assert all(connection.closed for connection in connections)
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_constructor_and_public_read_write_operations_close_connections_immediately(tmp_path, tracked_connections):
    engine = Engine(tmp_path / "data")
    assert_closed(tracked_connections)

    recording = engine.create_recording("fixture.wav", None, "fixture-hash", _now().isoformat())
    assert_closed(tracked_connections)

    engine.ingest_segments(recording["id"], [{"start": 0.25, "end": 2, "text": "I felt calm today."}], subject_confirmed=True)
    assert_closed(tracked_connections)

    state = engine.state()
    memory = state["memories"][0]
    assert_closed(tracked_connections)

    source = engine.get_source(memory["id"])
    assert source["segments"][0]["text"] == "I felt calm today."
    assert_closed(tracked_connections)

    engine.revise_memory(memory["id"], "I felt calm after the meeting.", memory["version"])
    assert_closed(tracked_connections)

    engine.settings_patch({"quiet_mode": True})
    assert_closed(tracked_connections)

    engine.agent_snapshot(recording["id"])
    assert_closed(tracked_connections)

    engine.delete_recording(recording["id"])
    assert engine.list_recordings() == []
    assert_closed(tracked_connections)


def test_failed_ingestion_rolls_back_all_partial_rows_and_closes_handle(tmp_path, tracked_connections):
    engine = Engine(tmp_path / "data")
    recording = engine.create_recording("fixture.wav", None, "fixture-hash", _now().isoformat())
    before_count = len(tracked_connections)

    with pytest.raises(ValueError):
        engine.ingest_segments(recording["id"], [
            {"start": 0, "end": 2, "text": "I felt calm today."},
            {"start": -1, "end": 3, "text": "This second segment has invalid timing."},
        ], subject_confirmed=True)

    assert len(tracked_connections) > before_count
    assert_closed(tracked_connections)
    current = engine.get_recording(recording["id"])
    assert current["segments"] == []
    assert current["status"] == "queued"
    with engine._connect() as database:
        assert database.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 1
    assert_closed(tracked_connections)


def test_domain_lookup_failure_also_releases_database_handle(tmp_path, tracked_connections):
    engine = Engine(tmp_path / "data")
    with pytest.raises(NotFoundError):
        engine.get_recording("missing-recording")
    assert_closed(tracked_connections)


def test_connection_context_commits_success_before_closing(tmp_path, tracked_connections):
    engine = Engine(tmp_path / "data")
    with engine._connect() as database:
        database.execute("CREATE TABLE lifecycle_probe (value TEXT NOT NULL)")
        database.execute("INSERT INTO lifecycle_probe VALUES (?)", ("committed",))
    assert_closed(tracked_connections)
    with engine._connect() as database:
        assert database.execute("SELECT value FROM lifecycle_probe").fetchone()[0] == "committed"
    assert_closed(tracked_connections)
