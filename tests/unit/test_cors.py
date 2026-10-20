"""RELAY_CORS_ORIGINS parsing (relay/infrastructure/cors.py)."""
from relay.infrastructure.cors import DEFAULT_CORS_ORIGINS, cors_origins


def _defaults() -> list[str]:
    return [origin for origin in DEFAULT_CORS_ORIGINS.split(",") if origin]


def test_unset_uses_the_ui_origins(monkeypatch):
    monkeypatch.delenv("RELAY_CORS_ORIGINS", raising=False)
    assert cors_origins() == _defaults()


def test_empty_counts_as_unset(monkeypatch):
    # compose passes ${RELAY_CORS_ORIGINS:-} through as an empty string.
    monkeypatch.setenv("RELAY_CORS_ORIGINS", "")
    assert cors_origins() == _defaults()


def test_configured_origins_are_split_and_trimmed(monkeypatch):
    monkeypatch.setenv("RELAY_CORS_ORIGINS", "http://13.200.1.2:8090, http://localhost:8090")
    assert cors_origins() == ["http://13.200.1.2:8090", "http://localhost:8090"]
