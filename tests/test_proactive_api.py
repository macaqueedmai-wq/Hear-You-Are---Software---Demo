"""User-facing v0.2 review, longitudinal support, reminders, and dataset boundaries."""
from datetime import datetime, timedelta, timezone
import json

from test_api import api, completed, review_cards, upload

HK = timezone(timedelta(hours=8))


def test_confirmed_identity_can_trigger_support_without_mandatory_card_review(api):
    client, app, fake, directory = api
    rec = completed(client, upload(client)["recording"]["id"])
    assert client.post(f"/api/recordings/{rec['id']}/confirm", json={}).status_code == 200
    state = client.get("/api/state").json()
    assert state["cards"] and state["proactive"][0]["type"] == "comfort"
    assert all(m["confirmed"] and not m["reviewed"] for m in state["memories"])
    assert all(c["review_status"] == "pending" and c["review_prompt"] for c in state["cards"])
    reviewed = review_cards(client, state)
    assert all(m["reviewed"] for m in reviewed["memories"])
    assert all(c["review_status"] == "confirmed" for c in reviewed["cards"])
    assert reviewed["proactive"][0]["type"] == "comfort"


def test_card_review_rejects_stale_summary_and_incomplete_sources(api):
    client, app, fake, directory = api
    state = client.post("/api/demo/seed").json()
    card = state["cards"][0]
    path = f"/api/cards/{card['id']}/confirm"
    assert client.post(path, json={"expected_versions": {}, "review_token": card["review_token"]}).status_code == 409
    assert client.post(path, json={"expected_versions": card["versions"], "review_token": "stale-summary"}).status_code == 409
    assert client.post(path, json={"expected_versions": card["versions"], "review_token": card["review_token"]}).status_code == 200
    memory_id = card["memory_ids"][0]
    assert client.patch(f"/api/memories/{memory_id}", json={"content": "I feel calm today.", "version": card["versions"][memory_id]}).status_code == 200
    assert client.post(path, json={"expected_versions": card["versions"], "review_token": card["review_token"]}).status_code == 409
    updated = next(c for c in client.get("/api/state").json()["cards"] if c["category"] == card["category"])
    assert updated["review_status"] == "pending"


def test_review_preserves_selected_historical_day(api):
    client, app, fake, directory = api
    older = datetime.now(HK) - timedelta(days=1)
    rec = completed(client, upload(client, recorded_at=older.isoformat())["recording"]["id"])
    client.post(f"/api/recordings/{rec['id']}/confirm", json={})
    state = client.get("/api/state", params={"date": older.date().isoformat()}).json()
    card = state["cards"][0]
    result = client.post(f"/api/cards/{card['id']}/confirm", json={"expected_versions": card["versions"], "review_token": card["review_token"]})
    assert result.status_code == 200, result.text
    assert result.json()["date"] == older.date().isoformat()
    assert result.json()["proactive"] == []  # Old short-lived need is not current need.


def test_sleep_support_links_history_then_requests_explicit_bedtime_permission(api):
    client, app, fake, directory = api
    result = client.post("/api/demo/scenario", json={"type": "sleep"})
    assert result.status_code == 200, result.text
    state = result.json()
    support = next(c for c in state["proactive"] if c.get("support_basis") == "sleep_pattern")
    assert support["type"] == "suggestion" and support["can_schedule_bedtime"]
    assert support["historical_memory_ids"] and support["current_memory_ids"]
    memories = {m["id"]: m for m in state["memories"]}
    assert len({memories[mid]["event_time"][:10] for mid in support["memory_ids"]}) >= 3
    assert all(memories[mid]["reviewed"] for mid in support["memory_ids"])
    assert all(not memories[mid].get("follow_up") for mid in support["memory_ids"])
    endpoint = f"/api/proactive/{support['id']}/bedtime"
    now = datetime.now(HK)
    assert client.post(endpoint, json={"due_at": (now - timedelta(minutes=1)).isoformat(), "expected_versions": support["versions"]}).status_code == 422
    assert client.post(endpoint, json={"due_at": (now + timedelta(hours=1)).isoformat(), "expected_versions": {}}).status_code == 409
    due_at = now + timedelta(hours=1)
    scheduled = client.post(endpoint, json={"due_at": due_at.isoformat(), "expected_versions": support["versions"]})
    assert scheduled.status_code == 200, scheduled.text
    tasks = [m for m in scheduled.json()["memories"] if m.get("follow_up")]
    assert len(tasks) == 1
    assert tasks[0]["follow_up"] == {"due_at": due_at.isoformat(), "allowed": True, "completed": False}
    assert client.post(endpoint, json={"due_at": due_at.isoformat(), "expected_versions": support["versions"]}).status_code == 409
    evaluation = app.state.engine.evaluate(now=due_at + timedelta(seconds=1))
    assert any(c["type"] == "reminder" for c in evaluation["cards"])


def test_history_support_is_invalidated_when_past_evidence_is_deleted(api):
    client, app, fake, directory = api
    state = client.post("/api/demo/scenario", json={"type": "social_history"}).json()
    support = next(c for c in state["proactive"] if c.get("support_basis") == "social_follow_up")
    assert support["historical_memory_ids"] and support["current_memory_ids"]
    history = support["historical_memory_ids"][0]
    assert client.delete(f"/api/memories/{history}").status_code == 200
    after = client.get("/api/state").json()
    assert not any(c["id"] == support["id"] for c in after["proactive"])
    assert client.post(f"/api/proactive/{support['id']}/feedback", json={"action": "helpful"}).status_code == 409


def dataset_bytes(**overrides):
    item = {"kind": "knowledge", "title": "虛構格式示例", "content": "只作資料格式測試，不是專業心理建議。",
            "source": "合成測試", "license": "MIT", "deidentified": True, "consent_to_use": True,
            **overrides}
    return (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")


def test_dataset_entry_is_stored_locally_but_not_used_by_live_support(api):
    client, app, fake, directory = api
    before = client.post("/api/demo/seed").json()
    response = client.post("/api/datasets", files={"file": ("sample.jsonl", dataset_bytes(training_enabled=True, status="approved"), "application/json")})
    assert response.status_code == 200, response.text
    dataset = response.json()
    assert dataset["status"] == "pending_review" and dataset["training_enabled"] is False and dataset["runtime_enabled"] is False
    assert client.get("/api/datasets").json()["datasets"][0]["id"] == dataset["id"]
    detail = client.get(f"/api/datasets/{dataset['id']}").json()
    assert detail["items"][0]["title"] == "虛構格式示例"
    duplicate = client.post("/api/datasets", files={"file": ("renamed.jsonl", dataset_bytes(training_enabled=True, status="approved"), "application/json")}).json()
    assert duplicate["duplicate"] is True and duplicate["id"] == dataset["id"]
    after = client.get("/api/state").json()
    assert after["memories"] == before["memories"]
    assert after["cards"] == before["cards"]
    assert client.delete(f"/api/datasets/{dataset['id']}").status_code == 200
    assert client.get(f"/api/datasets/{dataset['id']}").status_code == 404


def test_dataset_http_boundaries_return_readable_errors(api):
    client, app, fake, directory = api
    for filename, raw, status in [("sample.txt", dataset_bytes(), 415),
                                  ("sample.jsonl", b"not json", 422),
                                  ("sample.jsonl", dataset_bytes(deidentified=False), 422),
                                  ("sample.jsonl", b"x" * (5 * 1024 * 1024 + 1), 413)]:
        response = client.post("/api/datasets", files={"file": (filename, raw, "application/json")})
        assert response.status_code == status, response.text
        assert isinstance(response.json()["detail"], str)
    assert client.get("/api/datasets").json()["datasets"] == []
    assert client.get("/api/datasets/missing").status_code == 404
