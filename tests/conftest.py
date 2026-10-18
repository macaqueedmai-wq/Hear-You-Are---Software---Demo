"""Tests explicitly opt into external-provider mode, never developer secrets."""
import pytest


@pytest.fixture(autouse=True)
def isolated_provider_mode(monkeypatch):
    monkeypatch.setenv("HYA_MEMORY_MODE", "local_rules")
