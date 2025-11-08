import os, json, asyncio, base64
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, messaging
from database import users_col

load_dotenv()


def _init_firebase():
    """Initialize Firebase Admin once (Base64 or local JSON support)."""
    if firebase_admin._apps:
        return

    try:
        # Prefer Base64 env var for Render (safe for long JSON)
        b64_data = os.getenv("FIREBASE_SERVICE_ACCOUNT_B64")
        creds_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

        if b64_data:
            creds_dict = json.loads(base64.b64decode(b64_data))
            cred = credentials.Certificate(creds_dict)
            print("✅ Firebase initialized using Base64 environment variable.")
        elif creds_path and os.path.exists(creds_path):
            cred = credentials.Certificate(creds_path)
            print("✅ Firebase initialized using service-account.json file.")
        else:
            raise RuntimeError("❌ Firebase credentials not found in environment.")

        firebase_admin.initialize_app(cred)
        print("✅ Firebase Admin SDK successfully initialized!")

    except Exception as e:
        print("❌ Firebase initialization failed:", e)
        raise


async def send_fcm_multicast(
    tokens: List[str],
    title: str,
    body: str,
    data: Optional[Dict[str, str]] = None
):
    """Send notification to multiple device tokens."""
    if not tokens:
        print("⚠️ No FCM tokens found — skipping push.")
        return {"success": 0, "failure": 0}

    await asyncio.to_thread(_init_firebase)

    msg = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        tokens=tokens,
        data={k: str(v) for k, v in (data or {}).items()},
    )

    try:
        resp = await asyncio.to_thread(messaging.send_multicast, msg)
        print(f"📤 FCM Push Sent: {resp.success_count} success, {resp.failure_count} fail.")
        return {"success": resp.success_count, "failure": resp.failure_count}
    except Exception as e:
        print("❌ Error sending FCM push:", e)
        return {"success": 0, "failure": len(tokens)}


async def send_fcm_notification_for_user(
    user_id: str,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None
):
    """Fetch user tokens and send a push notification."""
    user = await users_col.find_one({"user_id": user_id})
    if not user:
        print(f"⚠️ No user found with user_id={user_id}")
        return

    tokens = user.get("fcm_tokens", [])
    if not tokens:
        print(f"⚠️ User {user_id} has no FCM tokens.")
        return

    print(f"🚀 Sending FCM to {len(tokens)} tokens for user {user_id}")
    await send_fcm_multicast(tokens, title, body, data)
