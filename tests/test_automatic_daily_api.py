"""Daily summaries run automatically against immutable synthetic source snapshots."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
import threading
import time

import pytest

from test_pipeline_api import pipeline_api, upload, wait_for_pipeline


class SummaryProbe:
    def __init__(self, *, block_first=False, fail=False):
        self.calls = []
        self.block_first, self.fail = block_first, fail
        self.started, self.release = threading.Event(), threading.Event()

    def summarize(self, memories):
        self.calls.append(deepcopy(memories))
        number = len(self.calls)
        self.started.set()
        if self.block_first and number == 1:
            assert self.release.wait(5), "The synthetic summary worker was not released"
        if self.fail:
            raise RuntimeError("Synthetic summary provider failure")
        return [{"category": category, "title": f"Synthetic snapshot {number}",
                 "summary": f"Snapshot {number}: " + " | ".join(memory["content"] for memory in memories if memory["category"] == category),
                 "memory_ids": [memory["id"] for memory in memories if memory["category"] == category]}
                for category in sorted({memory["category"] for memory in memories})]


def add_evidence(app, now, *, suffix="daily", text="I felt calm today.", confirmed=True):
    engine = app.state.engine
    recording = engine.create_recording(f"fictional-{suffix}.txt", None, f"fictional-{suffix}", now)
    engine.ingest_segments(recording["id"], [{"start": .1, "end": 2.8, "text": text, "speaker": "speaker_0", "words": []}],
                           subject_confirmed=confirmed, extract=True)
    return engine.get_recording(recording["id"])


def add_topic(app, recording):
    engine = app.state.engine
    snapshot = engine.agent_snapshot(recording["id"])
    current = engine.commit_topic_groups(recording["id"], [{
        "id": "synthetic-daily-topic", "title": "A calm moment", "summary": "The speaker described feeling calm.",
        "source_segment_ids": [source["id"] for source in recording["segments"]],
    }], snapshot["revision"])
    return current["topics"][0]


def wait_summary(client, status):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get("/api/state")
        assert response.status_code == 200, response.text
        state = response.json()
        if state["summary_generation"]["status"] == status:
            return state
        time.sleep(.01)
    pytest.fail(f"Synthetic daily summary did not reach {status}")


def assert_current_cards(state):
    lookup = {memory["id"]: memory for memory in state["memories"]}
    assert state["cards"]
    for card in state["cards"]:
        assert card["analysis_mode"] == "llm" and card["summary_mode"] == "llm_summary"
        assert card["versions"] == {ident: lookup[ident]["version"] for ident in card["memory_ids"]}
        assert set(card["memory_ids"]) == {memory["id"] for memory in state["memories"]
                                           if memory["category"] == card["category"] and memory["confirmed"]
                                           and memory["event_time"][:10] == state["date"]}


def test_get_state_is_nonblocking_and_generates_daily_cards_once_without_review(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe(block_first=True)
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    recording = add_evidence(app, now)
    before = app.state.engine.state()["memories"]
    try:
        response = client.get("/api/state")
        assert response.status_code == 200
        pending = response.json()
        assert pending["summary_generation"]["status"] in {"queued", "running"}
        assert probe.started.wait(3)
        for _ in range(3):
            current = client.get("/api/state").json()
            assert current["summary_generation"]["status"] in {"queued", "running"}
        assert len(probe.calls) == 1
    finally:
        probe.release.set()
    ready = wait_summary(client, "ready")
    assert_current_cards(ready)
    assert ready["memories"] == before
    assert app.state.engine.get_recording(recording["id"])["segments"] == recording["segments"]
    assert all(memory["reviewed"] is False for memory in ready["memories"])
    for _ in range(3):
        assert client.get("/api/state").json()["summary_generation"]["status"] == "ready"
    assert len(probe.calls) == 1 and not analyzer.care_calls


@pytest.mark.parametrize("unknown_source", [False, True])
def test_no_eligible_sources_reports_no_sources_without_model_calls(pipeline_api, monkeypatch, unknown_source):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe()
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    if unknown_source:
        add_evidence(app, now, confirmed=False)
    state = client.get("/api/state").json()
    assert state["summary_generation"]["status"] == "no-sources"
    assert state["summary_generation"]["message"] and state["cards"] == []
    assert not probe.calls


def test_missing_configuration_keeps_dashboard_available_with_explicit_status(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe()
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    analyzer.configured = False
    add_evidence(app, now)
    response = client.get("/api/state")
    assert response.status_code == 200
    assert response.json()["summary_generation"]["status"] == "unconfigured"
    assert response.json()["cards"][0]["analysis_mode"] == "pending"
    assert client.post("/api/analysis/generate", json={}).status_code == 422
    assert not probe.calls


def test_failed_snapshot_is_cached_for_polling_and_explicit_post_retries(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe(fail=True)
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    add_evidence(app, now)
    failed = wait_summary(client, "failed")
    assert failed["cards"][0]["analysis_mode"] == "pending"
    for _ in range(3):
        assert client.get("/api/state").json()["summary_generation"]["status"] == "failed"
    assert len(probe.calls) == 1
    response = client.post("/api/analysis/generate", json={})
    assert response.status_code == 502 and "Synthetic summary provider failure" not in response.text
    assert len(probe.calls) == 2
    probe.fail = False
    retried = client.post("/api/analysis/generate", json={})
    assert retried.status_code == 200, retried.text
    assert retried.json()["summary_generation"]["status"] == "ready"
    assert_current_cards(retried.json())
    assert len(probe.calls) == 3


@pytest.mark.parametrize("failure", ["source_conflict", "provider_failure"])
def test_only_source_conflicts_retry_automatically_after_source_status_recovers(pipeline_api, monkeypatch, failure):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe(fail=failure == "provider_failure")
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    recording = add_evidence(app, now)
    engine = app.state.engine
    versions = {memory["id"]: memory["version"] for memory in engine.state()["memories"]}
    if failure == "source_conflict":
        engine.set_recording_status(recording["id"], "transcribing")
    failed = client.post("/api/analysis/generate", json={})
    assert failed.status_code == (409 if failure == "source_conflict" else 502), failed.text
    previous_calls = 0 if failure == "source_conflict" else 1
    for _ in range(3):
        assert client.get("/api/state").json()["summary_generation"]["status"] == "failed"
    assert len(probe.calls) == previous_calls
    # The provider would now succeed, so this tests failure memoization rather
    # than another naturally failing model call after the source recovers.
    probe.fail = False
    if failure == "provider_failure":
        engine.set_recording_status(recording["id"], "transcribing")
        for _ in range(3):
            assert client.get("/api/state").json()["summary_generation"]["status"] == "failed"
        assert len(probe.calls) == 1
    engine.set_recording_status(recording["id"], "completed")
    if failure == "source_conflict":
        recovered = wait_summary(client, "ready")
        assert_current_cards(recovered)
        assert len(probe.calls) == 1
    else:
        for _ in range(3):
            cached = client.get("/api/state").json()
            assert cached["summary_generation"]["status"] == "failed"
            assert all(card["analysis_mode"] == "pending" for card in cached["cards"])
        assert len(probe.calls) == 1
    assert {memory["id"]: memory["version"] for memory in engine.state()["memories"]} == versions


def test_explicit_post_joins_same_running_snapshot_instead_of_calling_model_twice(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe(block_first=True)
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    add_evidence(app, now)
    client.get("/api/state")
    assert probe.started.wait(3)
    with ThreadPoolExecutor(max_workers=1) as callers:
        request = callers.submit(client.post, "/api/analysis/generate", json={})
        try:
            with pytest.raises(TimeoutError):
                request.result(timeout=.05)
            assert len(probe.calls) == 1
        finally:
            probe.release.set()
        response = request.result(timeout=5)
    assert response.status_code == 200, response.text
    assert response.json()["summary_generation"]["status"] == "ready"
    assert_current_cards(response.json())
    assert len(probe.calls) == 1


@pytest.mark.parametrize("invalid", ["omitted_category", "empty_cards", "unknown_category"])
def test_invalid_or_incomplete_category_batch_returns_422_without_partial_cache(pipeline_api, monkeypatch, invalid):
    client, app, agents, asr, analyzer, now = pipeline_api
    add_evidence(app, now)
    add_evidence(app, now, suffix="social", text="I talked with Alex after class.")
    original = analyzer.summarize
    calls = []

    def invalid_batch(memories):
        calls.append(deepcopy(memories))
        summaries = original(memories)
        if invalid == "empty_cards":
            return []
        if invalid == "omitted_category":
            return summaries[:1]
        summaries[0]["category"] = "unsupported"
        return summaries

    monkeypatch.setattr(analyzer, "summarize", invalid_batch)
    response = client.post("/api/analysis/generate", json={})
    assert response.status_code == 422, response.text
    state = client.get("/api/state").json()
    assert state["summary_generation"]["status"] == "failed"
    assert len(state["cards"]) == 2 and all(card["analysis_mode"] == "pending" for card in state["cards"])
    assert len(calls) == 1
    with app.state.engine._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM card_summaries").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["memory_edit", "topic_memory_edit", "new_category_member"])
def test_inflight_source_change_never_commits_old_snapshot_and_generates_current_membership(pipeline_api, monkeypatch, mutation):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe(block_first=True)
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    recording = add_evidence(app, now)
    topic = add_topic(app, recording)
    successful = []
    original_apply = app.state.engine.apply_card_summaries

    def record_success(date, summaries, versions, **kwargs):
        result = original_apply(date, summaries, versions, **kwargs)
        successful.extend(summary["title"] for summary in summaries)
        return result

    monkeypatch.setattr(app.state.engine, "apply_card_summaries", record_success)
    try:
        client.get("/api/state")
        assert probe.started.wait(3)
        memory = app.state.engine.state()["memories"][0]
        if mutation == "memory_edit":
            response = client.patch(f"/api/memories/{memory['id']}", json={
                "content": "I felt happy today.", "version": memory["version"], "confirmed": True,
            })
            assert response.status_code == 200
        elif mutation == "topic_memory_edit":
            response = client.patch(f"/api/recordings/{recording['id']}/topics/{topic['id']}", json={
                "version": topic["version"], "title": topic["title"], "summary": "The speaker felt happy.",
                "memories": [{"id": memory["id"], "version": memory["version"], "content": "I felt happy today."}],
            })
            assert response.status_code == 200
        else:
            add_evidence(app, now, suffix="added", text="I felt excited after class.")
        # Fresh polling must select the new signature even while the old job is blocked.
        pending = client.get("/api/state").json()
        assert pending["summary_generation"]["status"] in {"queued", "running"}
        assert all(card["analysis_mode"] == "pending" for card in pending["cards"])
    finally:
        probe.release.set()
    ready = wait_summary(client, "ready")
    assert_current_cards(ready)
    assert "Synthetic snapshot 1" not in successful
    assert all(card["title"] == "Synthetic snapshot 2" for card in ready["cards"])
    assert len(probe.calls) == 2
    if mutation == "new_category_member":
        assert len(ready["cards"][0]["memory_ids"]) == 2
    else:
        assert all("happy" in card["summary"] for card in ready["cards"])


def test_deletion_while_summary_runs_cannot_restore_deleted_memory_or_cache(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe(block_first=True)
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    add_evidence(app, now)
    attempted, successful = threading.Event(), []
    original_apply = app.state.engine.apply_card_summaries

    def checked_apply(date, summaries, versions, **kwargs):
        try:
            result = original_apply(date, summaries, versions, **kwargs)
            successful.append(True)
            return result
        finally:
            attempted.set()

    monkeypatch.setattr(app.state.engine, "apply_card_summaries", checked_apply)
    try:
        client.get("/api/state")
        assert probe.started.wait(3)
        memory = app.state.engine.state()["memories"][0]
        assert client.delete(f"/api/memories/{memory['id']}").status_code == 200
        assert client.get("/api/state").json()["summary_generation"]["status"] == "no-sources"
    finally:
        probe.release.set()
    assert attempted.wait(3)
    state = client.get("/api/state").json()
    assert state["cards"] == state["memories"] == []
    assert state["summary_generation"]["status"] == "no-sources"
    assert not successful and len(probe.calls) == 1
    with app.state.engine._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM card_summaries").fetchone()[0] == 0


def test_topic_title_only_invalidation_reruns_completed_signature_without_fact_version_change(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe()
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    recording = add_evidence(app, now)
    topic = add_topic(app, recording)
    first = wait_summary(client, "ready")
    versions = {memory["id"]: memory["version"] for memory in first["memories"]}
    response = client.patch(f"/api/recordings/{recording['id']}/topics/{topic['id']}", json={
        "version": topic["version"], "title": "Corrected topic heading", "summary": topic["summary"],
    })
    assert response.status_code == 200, response.text
    latest = wait_summary(client, "ready")
    assert {memory["id"]: memory["version"] for memory in latest["memories"]} == versions
    assert len(probe.calls) == 2
    assert all(card["title"] == "Synthetic snapshot 2" for card in latest["cards"])


def test_title_only_edit_does_not_automatically_retry_failed_identical_signature(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe(fail=True)
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    recording = add_evidence(app, now)
    topic = add_topic(app, recording)
    wait_summary(client, "failed")
    response = client.patch(f"/api/recordings/{recording['id']}/topics/{topic['id']}", json={
        "version": topic["version"], "title": "A harmless title correction", "summary": topic["summary"],
    })
    assert response.status_code == 200
    assert wait_summary(client, "failed")["cards"][0]["analysis_mode"] == "pending"
    assert len(probe.calls) == 1


def test_identity_before_atomic_pipeline_snapshot_generates_summary_when_upload_work_finishes(pipeline_api, monkeypatch):
    client, app, agents, asr, analyzer, now = pipeline_api
    probe = SummaryProbe()
    monkeypatch.setattr(analyzer, "summarize", probe.summarize)
    transcript_ready, release_pipeline = threading.Event(), threading.Event()
    original_status = app.state.engine.set_recording_status

    def pause_before_understanding(ident, status, error=None, metrics=None):
        result = original_status(ident, status, error, metrics)
        if status == "completed":
            transcript_ready.set()
            assert release_pipeline.wait(5)
        return result

    monkeypatch.setattr(app.state.engine, "set_recording_status", pause_before_understanding)
    try:
        recording = upload(client, now)
        assert transcript_ready.wait(3)
        raw = client.get(f"/api/recordings/{recording['id']}").json()
        assert raw["segments"]
        response = client.post(f"/api/recordings/{recording['id']}/confirm", json={
            "segment_ids": [source["id"] for source in raw["segments"]],
        })
        assert response.status_code == 200
        early = client.get("/api/state").json()
        assert early["summary_generation"]["status"] == "no-sources" and not early["memories"]
    finally:
        release_pipeline.set()
    assert probe.started.wait(3), "Successful upload processing must schedule summaries without another GET or manual click"
    completed = wait_for_pipeline(client, recording["id"])
    state = wait_summary(client, "ready")
    assert_current_cards(state)
    assert completed["segments"] == response.json()["segments"]
    assert all(memory["confirmed"] is True and memory["reviewed"] is False for memory in state["memories"])
    assert len(probe.calls) == 1 and len(asr.calls) == 1
