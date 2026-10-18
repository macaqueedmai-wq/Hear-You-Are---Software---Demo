"""Synthetic API checks for one-off timing and requested display of all support types."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from hya import engine as engine_module
from hya.engine import HK
from hya.memory_agents import STAGES
from test_api import FakeTranscriber
from test_memory_pipeline import FakeAgents


KINDS = ("reminder", "suggestion", "comfort", "warning")
TEXTS = {"reminder": "I need to send my presentation.", "suggestion": "I need help preparing my presentation.",
         "comfort": "I would like some reassurance.", "warning": "This is a fictional warning demonstration."}


class DisplayAnalyzer:
    def __init__(self):
        self.configured, self.fail, self.block = True, False, False
        self.calls = []
        self.started, self.release = threading.Event(), threading.Event()

    def status(self):
        return {"configured": self.configured, "mode": "llm", "external": False, "model": "synthetic"}

    def summarize(self, memories):
        return [{"category": category, "title": "Synthetic daily summary", "summary": "A fictional source-backed summary.",
                 "memory_ids": [memory["id"] for memory in memories if memory["category"] == category]}
                for category in sorted({memory["category"] for memory in memories})]

    def care_text(self, card, memories):
        self.calls.append({"id": card["id"], "type": card["type"]})
        self.started.set()
        if self.block:
            assert self.release.wait(5), "The synthetic care worker was not released"
        if self.fail:
            raise RuntimeError("Synthetic model failure")
        return {"title": "Fictional warning" if card["type"] == "warning" else "A little support",
                "summary": "Fictional demonstration only." if card["type"] == "warning" else "Take one gentle pause if that feels useful."}


@pytest.fixture
def support_api(tmp_path, monkeypatch, isolated_provider_mode):
    monkeypatch.setenv("HYA_MEMORY_MODE", "deepseek")
    monkeypatch.setenv("HYA_DATA_DIR", str(tmp_path / "unused-default"))
    monkeypatch.setenv("HYA_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
    monkeypatch.setenv("HYA_DEPLOYMENT_MODE", "local")
    for variable in ("HYA_LLM_BASE_URL", "HYA_LLM_MODEL", "HYA_LLM_API_KEY"):
        monkeypatch.setenv(variable, "")
    for stage in STAGES:
        monkeypatch.setenv(f"HYA_DEEPSEEK_{stage.upper()}_API_KEY", "")
    monkeypatch.setenv("HYA_ALLOW_EXTERNAL_LLM", "false")
    clock = [datetime.now(HK).replace(hour=10, minute=0, second=0, microsecond=0)]
    monkeypatch.setattr(engine_module, "_now", lambda: clock[0])
    analyzer = DisplayAnalyzer()
    app = importlib.import_module("hya.app").create_app(
        tmp_path / "support-data", transcriber=FakeTranscriber(), analyzer=analyzer, memory_agents=FakeAgents())
    with TestClient(app) as client:
        try:
            yield SimpleNamespace(client=client, app=app, engine=app.state.engine, analyzer=analyzer, clock=clock)
        finally:
            analyzer.release.set()


def prepare(ctx, kind, *, confirmed=True, demo=None):
    recording = ctx.engine.create_recording(f"synthetic-{kind}.txt", None, f"synthetic-{kind}", ctx.clock[0],
                                            is_demo=kind == "warning" if demo is None else demo)
    ctx.engine.ingest_segments(recording["id"], [{"start": .1, "end": 3, "text": TEXTS[kind], "speaker": "speaker_0", "words": []}],
                               subject_confirmed=confirmed, extract=True)
    memory = next(memory for memory in ctx.engine.state()["memories"] if memory["recording_id"] == recording["id"])
    if kind == "reminder":
        memory = ctx.engine.revise_memory(memory["id"], memory["content"], memory["version"],
                                          follow_up={"allowed": True, "completed": False,
                                                     "due_at": (ctx.clock[0] - timedelta(minutes=1)).isoformat()})
    if kind == "warning":
        ctx.engine.settings_patch({"preferences": {"warning": True}, "warning_demo_enabled": True})
    return ctx.engine.get_recording(recording["id"]), memory


def set_time(ctx, kind, display_at, expected=None):
    return ctx.client.patch("/api/proactive/display-time", json={
        "type": kind, "display_at": display_at, "expected_display_at": expected,
    })


def timing(state, kind):
    value = state["assessment"]["types"][kind]["display_time"]
    assert set(value) == {"scheduled_at", "effective_at", "displayed_at", "reason"}
    assert isinstance(value["reason"], str) and value["reason"]
    for key in ("scheduled_at", "effective_at", "displayed_at"):
        if value[key] is not None:
            assert datetime.fromisoformat(value[key].replace("Z", "+00:00")).utcoffset() is not None
    return value


def wait_card(ctx, kind):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = ctx.client.get("/api/state")
        assert response.status_code == 200, response.text
        state = response.json()
        cards = [card for card in state["proactive"] if card["type"] == kind]
        if cards:
            return state, cards[0]
        time.sleep(.01)
    pytest.fail(f"Synthetic {kind} was not released")


def schedule_row(ctx, kind):
    with ctx.engine._connect() as db:
        row = db.execute("SELECT revision,fulfilled_revision FROM support_display_times WHERE type=?", (kind,)).fetchone()
        return tuple(row) if row else None


def test_schedule_cas_normalizes_timezone_persists_and_resets_without_fact_or_setting_mutation(support_api):
    ctx = support_api
    prepare(ctx, "comfort")
    ctx.engine.settings_patch({"quiet_mode": True})
    original = deepcopy(ctx.engine.state())
    first = ctx.clock[0] + timedelta(hours=1)
    first_utc = first.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    response = set_time(ctx, "comfort", first_utc)
    assert response.status_code == 200, response.text
    assert response.json()["date"] == ctx.clock[0].date().isoformat()
    assert timing(response.json(), "comfort")["scheduled_at"] == first.isoformat()
    assert timing(response.json(), "comfort")["displayed_at"] is None
    second = ctx.clock[0] + timedelta(hours=2)
    assert set_time(ctx, "comfort", second.isoformat(), first_utc).status_code == 200
    stale = set_time(ctx, "comfort", None, first.isoformat())
    assert stale.status_code == 409
    state = ctx.client.get("/api/state").json()
    assert timing(state, "comfort")["scheduled_at"] == second.isoformat()
    for kind in KINDS:
        assert isinstance(state["assessment"]["types"][kind]["request_allowed"], bool)
        timing(state, kind)
    reset = set_time(ctx, "comfort", None, second.isoformat())
    assert reset.status_code == 200 and timing(reset.json(), "comfort")["scheduled_at"] is None
    assert reset.json()["settings"] == original["settings"]
    assert reset.json()["memories"] == original["memories"]
    assert reset.json()["recordings"] == original["recordings"]
    assert not ctx.analyzer.calls


@pytest.mark.parametrize("body", [
    {}, {"type": "comfort", "display_at": None}, {"type": "other", "display_at": None, "expected_display_at": None},
    {"type": "comfort", "display_at": "2026-10-18T12:00:00", "expected_display_at": None},
    {"type": "comfort", "display_at": "12:00", "expected_display_at": None},
    {"type": "comfort", "display_at": "bad date", "expected_display_at": None},
    {"type": "comfort", "display_at": True, "expected_display_at": None},
    {"type": "comfort", "display_at": "9999-12-31T23:59:59Z", "expected_display_at": None},
    {"type": "comfort", "display_at": None, "expected_display_at": "bad date"},
])
def test_invalid_or_incomplete_schedule_payload_is_422_and_atomic(support_api, body):
    ctx = support_api
    ctx.engine.settings_patch({"quiet_mode": True})
    before = deepcopy(ctx.engine.state())
    response = ctx.client.patch("/api/proactive/display-time", json=body)
    assert response.status_code == 422, response.text
    assert ctx.engine.state() == before and not ctx.analyzer.calls


@pytest.mark.parametrize("kind", KINDS)
def test_generic_manual_display_bypasses_delivery_only_and_retains_all_settings(support_api, kind):
    ctx = support_api
    recording, memory = prepare(ctx, kind)
    ctx.engine.settings_patch({"quiet_mode": True, "snoozed_until": (ctx.clock[0] + timedelta(hours=3)).isoformat(),
                               "frequency_minutes": 1440})
    future = (ctx.clock[0] + timedelta(hours=2)).isoformat()
    assert set_time(ctx, kind, future).status_code == 200
    before = deepcopy(ctx.engine.state())
    response = ctx.client.post("/api/proactive/display-now", json={"type": kind})
    assert response.status_code == 200, response.text
    assert [card["type"] for card in response.json()["cards"]] == [kind]
    card = response.json()["cards"][0]
    assert card["delivery_mode"] == "requested" and card["generation_mode"] == "llm"
    assert card["memory_ids"] == [memory["id"]]
    for _ in range(3):
        state = ctx.client.get("/api/state").json()
        assert [value["id"] for value in state["proactive"]] == [card["id"]]
        assert timing(state, kind)["scheduled_at"] == future
        assert state["settings"] == before["settings"] and state["memories"] == before["memories"]
    assert ctx.engine.get_recording(recording["id"])["segments"] == recording["segments"]
    assert len(ctx.analyzer.calls) == 1


@pytest.mark.parametrize("gate", ["disabled", "unknown", "warning_preference", "warning_demo", "warning_real"])
def test_generic_manual_display_cannot_override_source_or_warning_permission_gates(support_api, gate):
    ctx = support_api
    kind = "warning" if gate.startswith("warning") else "comfort"
    prepare(ctx, kind, confirmed=gate != "unknown", demo=False if gate == "warning_real" else None)
    if gate in {"disabled", "warning_preference"}:
        ctx.engine.settings_patch({"preferences": {kind: False}})
    if gate == "warning_demo":
        ctx.engine.settings_patch({"warning_demo_enabled": False})
    response = ctx.client.post("/api/proactive/display-now", json={"type": kind})
    assert response.status_code == 409, response.text
    assert not ctx.analyzer.calls


def test_future_type_is_removed_before_automatic_ambiguity_check(support_api):
    ctx = support_api
    prepare(ctx, "comfort")
    prepare(ctx, "suggestion")
    assert set(ctx.engine.evaluate()["assessment"]["matched_types"]) == {"comfort", "suggestion"}
    future = (ctx.clock[0] + timedelta(hours=1)).isoformat()
    assert set_time(ctx, "comfort", future).status_code == 200
    state, card = wait_card(ctx, "suggestion")
    assert [value["type"] for value in state["proactive"]] == ["suggestion"]
    assert state["assessment"]["types"]["comfort"]["condition_met"] is True
    assert timing(state, "comfort")["scheduled_at"] == future
    assert len(ctx.analyzer.calls) == 1


@pytest.mark.parametrize("dismissed,snoozed", [(False, False), (True, False), (True, True)])
def test_unfulfilled_future_schedule_projects_its_authorized_time_before_old_feedback_or_cooldown(support_api, dismissed, snoozed):
    ctx = support_api
    prepare(ctx, "comfort")
    ctx.engine.settings_patch({"frequency_minutes": 60})
    first = ctx.client.post("/api/proactive/evaluate", json={})
    assert first.status_code == 200, first.text
    card = first.json()["cards"][0]
    if dismissed:
        assert ctx.client.post(f"/api/proactive/{card['id']}/feedback", json={"action": "dismiss"}).status_code == 200
    target = ctx.clock[0] + timedelta(minutes=10)
    expected = target + timedelta(minutes=5) if snoozed else target
    if snoozed:
        ctx.engine.settings_patch({"snoozed_until": expected.isoformat()})
    response = set_time(ctx, "comfort", target.isoformat())
    assert response.status_code == 200 and response.json()["proactive"] == []
    assert timing(response.json(), "comfort")["effective_at"] == expected.isoformat()
    revision, fulfilled = schedule_row(ctx, "comfort")
    assert revision != fulfilled and len(ctx.analyzer.calls) == 1


def test_automatic_snooze_projects_known_snooze_and_frequency_bounds(support_api):
    ctx = support_api
    prepare(ctx, "comfort")
    ctx.engine.settings_patch({"frequency_minutes": 60})
    first = ctx.client.post("/api/proactive/evaluate", json={})
    assert first.status_code == 200, first.text
    card = first.json()["cards"][0]
    ctx.clock[0] += timedelta(minutes=5)
    assert ctx.client.post(f"/api/proactive/{card['id']}/feedback", json={"action": "snooze"}).status_code == 200
    state = ctx.client.get("/api/state").json()
    assert state["proactive"] == []
    snooze_until = datetime.fromisoformat(state["settings"]["snoozed_until"])
    expected = max(snooze_until, ctx.clock[0] + timedelta(minutes=60))
    assert timing(state, "comfort")["effective_at"] == expected.isoformat()
    assert len(ctx.analyzer.calls) == 1


def test_future_edit_hides_requested_wording_then_due_release_honors_quiet_and_snooze(support_api):
    ctx = support_api
    prepare(ctx, "comfort")
    first = ctx.client.post("/api/proactive/display-now", json={"type": "comfort"})
    assert first.status_code == 200, first.text
    card = first.json()["cards"][0]
    actual = timing(ctx.client.get("/api/state").json(), "comfort")["displayed_at"]
    future = ctx.clock[0] + timedelta(minutes=10)
    hidden = set_time(ctx, "comfort", future.isoformat())
    assert hidden.status_code == 200 and hidden.json()["proactive"] == []
    assert timing(hidden.json(), "comfort")["displayed_at"] == actual
    ctx.engine.settings_patch({"quiet_mode": True, "snoozed_until": (future + timedelta(hours=1)).isoformat()})
    ctx.clock[0] = future + timedelta(seconds=1)
    assert ctx.client.get("/api/state").json()["proactive"] == []
    revision, fulfilled = schedule_row(ctx, "comfort")
    assert revision != fulfilled
    ctx.engine.settings_patch({"quiet_mode": False})
    assert ctx.client.get("/api/state").json()["proactive"] == []
    ctx.engine.settings_patch({"snoozed_until": None})
    state, released = wait_card(ctx, "comfort")
    assert released["id"] == card["id"]
    assert (released["title"], released["summary"]) == (card["title"], card["summary"])
    assert timing(state, "comfort")["displayed_at"] == ctx.clock[0].isoformat()
    assert schedule_row(ctx, "comfort")[0] == schedule_row(ctx, "comfort")[1]
    assert len(ctx.analyzer.calls) == 1


def test_due_oneoff_can_redeliver_same_evidence_after_feedback_without_recurring(support_api):
    ctx = support_api
    prepare(ctx, "comfort")
    first = ctx.client.post("/api/proactive/display-now", json={"type": "comfort"})
    card = first.json()["cards"][0]
    assert ctx.client.post(f"/api/proactive/{card['id']}/feedback", json={"action": "dismiss"}).status_code == 200
    future = ctx.clock[0] + timedelta(minutes=10)
    assert set_time(ctx, "comfort", future.isoformat()).status_code == 200
    ctx.clock[0] = future + timedelta(seconds=1)
    _, released = wait_card(ctx, "comfort")
    calls = len(ctx.analyzer.calls)
    assert ctx.client.post(f"/api/proactive/{released['id']}/feedback", json={"action": "dismiss"}).status_code == 200
    for _ in range(3):
        assert ctx.client.get("/api/state").json()["proactive"] == []
    assert len(ctx.analyzer.calls) == calls
    assert schedule_row(ctx, "comfort")[0] == schedule_row(ctx, "comfort")[1]


@pytest.mark.parametrize("feedback_after_due", [True, False])
def test_feedback_at_due_boundary_consumes_only_an_already_due_visible_oneoff(support_api, feedback_after_due):
    ctx = support_api
    prepare(ctx, "comfort")
    target = ctx.clock[0] + timedelta(minutes=10)
    assert set_time(ctx, "comfort", target.isoformat()).status_code == 200
    response = ctx.client.post("/api/proactive/display-now", json={"type": "comfort"})
    assert response.status_code == 200, response.text
    card = response.json()["cards"][0]
    ctx.clock[0] = target + timedelta(seconds=1) if feedback_after_due else target - timedelta(seconds=1)
    # No intervening state poll may consume the newly due authorization.
    feedback = ctx.client.post(f"/api/proactive/{card['id']}/feedback", json={"action": "dismiss"})
    assert feedback.status_code == 200, feedback.text
    if feedback_after_due:
        for _ in range(3):
            assert ctx.client.get("/api/state").json()["proactive"] == []
        assert schedule_row(ctx, "comfort")[0] == schedule_row(ctx, "comfort")[1]
    else:
        assert ctx.client.get("/api/state").json()["proactive"] == []
        revision, fulfilled = schedule_row(ctx, "comfort")
        assert revision != fulfilled
        ctx.clock[0] = target + timedelta(seconds=1)
        wait_card(ctx, "comfort")
        assert schedule_row(ctx, "comfort")[0] == schedule_row(ctx, "comfort")[1]
    assert len(ctx.analyzer.calls) == 1


def test_actual_display_time_waits_for_model_ready_instead_of_candidate_creation(support_api):
    ctx = support_api
    prepare(ctx, "comfort")
    ctx.engine.settings_patch({"quiet_mode": True})
    ctx.analyzer.block = True
    with ThreadPoolExecutor(max_workers=1) as requests:
        request = requests.submit(ctx.client.post, "/api/proactive/display-now", json={"type": "comfort"})
        try:
            assert ctx.analyzer.started.wait(3)
            pending = ctx.client.get("/api/state").json()
            assert pending["proactive"] == [] and timing(pending, "comfort")["displayed_at"] is None
            ctx.clock[0] += timedelta(minutes=2)
        finally:
            ctx.analyzer.release.set()
        assert request.result(timeout=5).status_code == 200
    state, _ = wait_card(ctx, "comfort")
    assert timing(state, "comfort")["displayed_at"] == ctx.clock[0].isoformat()


def test_future_schedule_during_http_rejects_old_commit_then_due_can_generate_fresh(support_api, monkeypatch):
    ctx = support_api
    prepare(ctx, "comfort")
    ctx.analyzer.block = True
    attempted, successful = threading.Event(), []
    original_apply = ctx.engine.apply_proactive_text

    def watched_apply(*args, **kwargs):
        try:
            result = original_apply(*args, **kwargs)
            successful.append(result["id"])
            return result
        finally:
            attempted.set()

    monkeypatch.setattr(ctx.engine, "apply_proactive_text", watched_apply)
    try:
        ctx.client.get("/api/state")
        assert ctx.analyzer.started.wait(3)
        future = ctx.clock[0] + timedelta(minutes=10)
        changed = set_time(ctx, "comfort", future.isoformat())
        assert changed.status_code == 200 and changed.json()["proactive"] == []
        assert timing(changed.json(), "comfort")["displayed_at"] is None
        ctx.analyzer.block = False
    finally:
        ctx.analyzer.release.set()
    assert attempted.wait(3)
    assert not successful
    ctx.clock[0] = future + timedelta(seconds=1)
    state, _ = wait_card(ctx, "comfort")
    assert timing(state, "comfort")["displayed_at"] == ctx.clock[0].isoformat()
    assert len(ctx.analyzer.calls) == 2
    assert schedule_row(ctx, "comfort")[0] == schedule_row(ctx, "comfort")[1]


@pytest.mark.parametrize("failure", ["unconfigured", "provider_failure"])
def test_due_schedule_is_not_consumed_before_a_successful_release(support_api, failure):
    ctx = support_api
    prepare(ctx, "comfort")
    automatic = ctx.engine.evaluate()["cards"][0]
    ctx.engine.feedback(automatic["id"], "dismiss")
    ctx.analyzer.configured = failure != "unconfigured"
    ctx.analyzer.fail = failure == "provider_failure"
    due = (ctx.clock[0] - timedelta(seconds=1)).isoformat()
    response = set_time(ctx, "comfort", due)
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = ctx.client.get("/api/state").json()
        if state["care_generation"]["status"] in {"unconfigured", "failed"}:
            break
        time.sleep(.01)
    assert state["care_generation"]["status"] == ("unconfigured" if failure == "unconfigured" else "failed")
    assert timing(state, "comfort")["displayed_at"] is None
    revision, fulfilled = schedule_row(ctx, "comfort")
    assert revision != fulfilled
    ctx.analyzer.configured, ctx.analyzer.fail = True, False
    if failure == "provider_failure":
        retried = ctx.client.post("/api/proactive/evaluate", json={})
        assert retried.status_code == 200, retried.text
    state, _ = wait_card(ctx, "comfort")
    assert timing(state, "comfort")["displayed_at"] == ctx.clock[0].isoformat()
    assert schedule_row(ctx, "comfort")[0] == schedule_row(ctx, "comfort")[1]


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_generic_requested_display_rejects_stale_sources_during_model_work(support_api, mutation):
    ctx = support_api
    recording, memory = prepare(ctx, "comfort")
    ctx.engine.settings_patch({"quiet_mode": True})
    ctx.analyzer.block = True
    with ThreadPoolExecutor(max_workers=1) as requests:
        request = requests.submit(ctx.client.post, "/api/proactive/display-now", json={"type": "comfort"})
        try:
            assert ctx.analyzer.started.wait(3)
            if mutation == "edit":
                changed = ctx.client.patch(f"/api/memories/{memory['id']}", json={
                    "content": "I do not want reassurance.", "version": memory["version"], "confirmed": True,
                })
            else:
                changed = ctx.client.delete(f"/api/memories/{memory['id']}")
            assert changed.status_code == 200, changed.text
        finally:
            ctx.analyzer.release.set()
        assert request.result(timeout=5).status_code == 409
    assert ctx.client.get("/api/state").json()["proactive"] == []
    assert ctx.engine.get_recording(recording["id"])["segments"] == recording["segments"]
