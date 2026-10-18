"""Public clients cannot rewrite automatically assigned upload chronology."""
from copy import deepcopy

import pytest

from test_api import api, completed, upload


@pytest.mark.parametrize("body", [
    {}, {"recorded_at": None},
    {"recorded_at": "1900-01-01T00:00:00Z", "expected_recorded_at": None},
    {"recorded_at": "not a date", "expected_recorded_at": True},
])
def test_retired_public_date_edit_returns_409_without_changing_source_or_memories(api, body):
    client, app, asr, directory = api
    recording = completed(client, upload(client)["recording"]["id"])
    before = deepcopy(app.state.engine.get_recording(recording["id"]))
    memories = deepcopy(app.state.engine.state()["memories"])
    audio = client.get(f"/api/recordings/{recording['id']}/audio").content
    response = client.patch(f"/api/recordings/{recording['id']}", json=body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Uploading date is assigned automatically."
    assert app.state.engine.get_recording(recording["id"]) == before
    assert app.state.engine.state()["memories"] == memories
    assert client.get(f"/api/recordings/{recording['id']}/audio").content == audio
    assert len(asr.calls) == 1


def test_retired_date_edit_keeps_unknown_and_deleted_recordings_404(api):
    client, app, asr, directory = api
    assert client.patch("/api/recordings/missing", json={}).status_code == 404
    recording = completed(client, upload(client)["recording"]["id"])
    assert client.delete(f"/api/recordings/{recording['id']}").status_code == 200
    assert client.patch(f"/api/recordings/{recording['id']}", json={}).status_code == 404
    assert app.state.engine.list_recordings() == []
