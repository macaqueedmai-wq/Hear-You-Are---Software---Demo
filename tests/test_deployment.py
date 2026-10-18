"""Hosted mode accepts only configured hosts and identifies server processing."""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


class IdleTranscriber:
    def health(self):
        return {"state": "idle", "loaded": False, "local_only": True}


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("HYA_DATA_DIR", str(tmp_path / "default"))
    monkeypatch.setenv("HYA_LLM_BASE_URL", "")
    monkeypatch.setenv("HYA_LLM_MODEL", "")
    module = importlib.import_module("hya.app")
    return module.create_app(tmp_path / "demo", transcriber=IdleTranscriber())


def test_hosted_demo_accepts_configured_domain_and_reports_server_processing(tmp_path, monkeypatch):
    monkeypatch.setenv("HYA_DEPLOYMENT_MODE", "server")
    monkeypatch.setenv("HYA_ALLOWED_HOSTS", "localhost,127.0.0.1,demo.example.test")
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://demo.example.test") as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        health = response.json()
        assert health["deployment"] == {
            "mode": "server", "audio_processing": "server", "shared_workspace": True,
        }
        assert health["asr"]["local_only"] is False
        assert health["asr"]["loaded"] is False  # A health probe never loads model weights.
        assert client.get("/api/health", headers={"host": "untrusted.example.test"}).status_code == 400
        assert client.get("/api/health", headers={"host": "127.0.0.1:8765"}).status_code == 200


def test_local_mode_keeps_loopback_access_and_rejects_public_host(tmp_path, monkeypatch):
    monkeypatch.delenv("HYA_DEPLOYMENT_MODE", raising=False)
    monkeypatch.delenv("HYA_ALLOWED_HOSTS", raising=False)
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["deployment"]["mode"] == "local"
        assert health["deployment"]["audio_processing"] == "this_device"
        assert health["asr"]["local_only"] is True
        assert client.get("/api/state", headers={"host": "public.example.test"}).status_code == 400
