"""Boot smoke test: the app must import and register every router. No database is needed (lifespan is not run)."""
import base64
import os

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("CRM_DATABASE_URL", "postgresql://x:y@localhost/z")
    monkeypatch.setenv("CRM_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("CRM_JWT_SECRET", "j" * 40)
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app
    yield create_app()
    get_settings.cache_clear()


def test_all_routers_registered(app):
    paths = {route.path for route in app.routes}
    expected = ("/healthz", "/readyz", "/api/auth/login", "/api/database", "/api/channels", "/api/inbox/{platform}/conversations",
                "/api/comments/{platform}", "/api/kb", "/api/media/{media_id}", "/api/analytics", "/api/notifications",
                "/api/notifications/unread-count", "/api/notifications/read", "/api/activity", "/api/events",
                "/webhooks/{tenant_id}/{channel_id}")
    missing = [path for path in expected if path not in paths]
    assert not missing, f"routes not registered: {missing}"
