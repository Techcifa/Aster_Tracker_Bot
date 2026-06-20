"""
tests/conftest.py — Test configuration and shared mock fixtures.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-wide event loop for async tests."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock required environment variables to prevent pydantic settings errors."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx987wuv321")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "dummy_webhook_secret")
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://mockbot.railway.app")
    monkeypatch.setenv("ALCHEMY_API_KEY", "dummy_alchemy_key")
    monkeypatch.setenv("ALCHEMY_WEBHOOK_SIGNING_KEY", "dummy_signing_key")
    monkeypatch.setenv("ALCHEMY_WEBHOOK_ID", "wh_dummy")
    monkeypatch.setenv("ALCHEMY_AUTH_TOKEN", "dummy_auth_token")
    monkeypatch.setenv("OPENSEA_API_KEY", "dummy_opensea_key")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest.fixture(autouse=True)
def mock_bot_api():
    """Mock the Bot instance from aiogram to prevent network requests."""
    with patch("app.main.Bot") as mock_bot_class:
        mock_instance = MagicMock()
        mock_instance.set_webhook = AsyncMock()
        mock_instance.session = AsyncMock()
        mock_bot_class.return_value = mock_instance
        yield mock_instance
