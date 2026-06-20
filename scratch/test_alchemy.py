import os
import sys
import httpx
import asyncio
from dotenv import load_dotenv

# Load local .env if it exists
load_dotenv()

async def test_alchemy():
    # If the user passed credentials as arguments, use them, otherwise read from environment/dotenv
    auth_token = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ALCHEMY_AUTH_TOKEN", "")
    webhook_id = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ALCHEMY_WEBHOOK_ID", "")
    address = sys.argv[3] if len(sys.argv) > 3 else "0xF8c6e733d4e26b2b20d474705Ab5B213f9268991"

    if not auth_token or not webhook_id:
        print("❌ Error: ALCHEMY_AUTH_TOKEN or ALCHEMY_WEBHOOK_ID is empty!")
        print("Please run this script passing them as arguments:")
        print("  .venv\\Scripts\\python scratch/test_alchemy.py <AUTH_TOKEN> <WEBHOOK_ID>")
        return

    print(f"Testing with:")
    print(f"  Auth Token: {auth_token[:6]}...{auth_token[-4:] if len(auth_token) > 10 else ''}")
    print(f"  Webhook ID: {webhook_id}")
    print(f"  Address to add: {address}")

    payload = {
        "webhookId": webhook_id,
        "addressesToAdd": [address.lower()],
        "addressesToRemove": [],
    }

    headers = {
        "X-Alchemy-Token": auth_token,
        "Content-Type": "application/json",
    }

    print("\nSending PATCH request to Alchemy...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                "https://dashboard.alchemy.com/api/update-webhook-addresses",
                json=payload,
                headers=headers,
            )
        print(f"Response status: {resp.status_code}")
        print(f"Response body: {resp.text}")
    except Exception as e:
        print(f"Error making request: {e}")

if __name__ == "__main__":
    asyncio.run(test_alchemy())
