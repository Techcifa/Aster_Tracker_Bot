"""
tests/test_webhooks.py — Tests for Alchemy webhook signature verification and routing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_webhook_missing_signature():
    """Verify that requests missing the signature header are rejected with 401."""
    client = TestClient(app)
    response = client.post(
        "/webhook/alchemy",
        json={"test": "data"},
    )
    assert response.status_code == 401


def test_webhook_invalid_signature():
    """Verify that requests with invalid signatures are rejected with 403."""
    client = TestClient(app)
    response = client.post(
        "/webhook/alchemy",
        json={"test": "data"},
        headers={"x-alchemy-signature": "invalid_sig_here"},
    )
    assert response.status_code == 403


def test_webhook_valid_signature():
    """Verify that requests with valid signatures are accepted and process background tasks."""
    settings = get_settings()
    signing_key = settings.alchemy_webhook_signing_key

    payload = {
        "webhookId": "wh_dummy",
        "id": "evt_123",
        "event": {
            "network": "ETH_MAINNET",
            "activity": [
                {
                    "hash": "0xhash123",
                    "fromAddress": "0xfrom",
                    "toAddress": "0xto",
                }
            ],
        },
    }
    body_bytes = json.dumps(payload).encode("utf-8")

    # Compute correct HMAC signature
    computed_sig = hmac.new(
        signing_key.encode("utf-8"),
        body_bytes,
        hashlib.sha256
    ).hexdigest()

    with patch("app.webhooks.alchemy.process_alchemy_payload") as mock_process:
        client = TestClient(app)
        response = client.post(
            "/webhook/alchemy",
            content=body_bytes,
            headers={
                "x-alchemy-signature": computed_sig,
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        
        # TestClient runs FastAPI background tasks synchronously before returning
        mock_process.assert_called_once_with(payload)
