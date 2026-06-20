"""
app/services/alchemy_api.py — Alchemy Notify API wrapper.

Used to add/remove Ethereum addresses from the Address Activity webhook's
watch list when users run /track or /untrack.

API reference:
  PATCH https://dashboard.alchemy.com/api/update-webhook-addresses
  Authorization: X-Alchemy-Token <auth_token>
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_ALCHEMY_WEBHOOK_API = "https://dashboard.alchemy.com/api/update-webhook-addresses"


async def update_webhook_addresses(
    *,
    auth_token: str,
    webhook_id: str,
    addresses_to_add: list[str] | None = None,
    addresses_to_remove: list[str] | None = None,
) -> bool:
    """
    Add or remove addresses from an existing Alchemy Address Activity webhook.

    Returns True on success, False on failure (logged — caller should proceed
    regardless to avoid blocking the user).

    API note: field names are camelCase (addressesToAdd / addressesToRemove)
    and both arrays must always be present in the payload.
    """
    if not auth_token or not webhook_id:
        logger.warning(
            "Alchemy credentials not configured (ALCHEMY_AUTH_TOKEN / ALCHEMY_WEBHOOK_ID). "
            "Skipping webhook update — set these in Railway Variables."
        )
        return False

    # Alchemy requires camelCase keys and both arrays present (even if empty)
    payload: dict = {
        "webhookId": webhook_id,
        "addressesToAdd": [a.lower() for a in (addresses_to_add or [])],
        "addressesToRemove": [a.lower() for a in (addresses_to_remove or [])],
    }

    headers = {
        "X-Alchemy-Token": auth_token,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                _ALCHEMY_WEBHOOK_API,
                json=payload,
                headers=headers,
            )
        if resp.status_code == 200:
            logger.info(
                "Alchemy webhook updated: +%s -%s",
                addresses_to_add or [],
                addresses_to_remove or [],
            )
            return True
        else:
            logger.error(
                "Alchemy webhook update failed (%d): %s",
                resp.status_code,
                resp.text[:200],
            )
            return False
    except Exception as e:
        logger.exception("Alchemy webhook API error: %s", e)
        return False


async def add_address(auth_token: str, webhook_id: str, address: str) -> bool:
    """Add a single address to the webhook watch list."""
    return await update_webhook_addresses(
        auth_token=auth_token,
        webhook_id=webhook_id,
        addresses_to_add=[address],
    )


async def remove_address(auth_token: str, webhook_id: str, address: str) -> bool:
    """Remove a single address from the webhook watch list."""
    return await update_webhook_addresses(
        auth_token=auth_token,
        webhook_id=webhook_id,
        addresses_to_remove=[address],
    )
