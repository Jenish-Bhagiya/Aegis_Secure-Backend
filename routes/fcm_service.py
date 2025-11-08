import os, json, asyncio
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, messaging
from database import users_col

load_dotenv()


def _init_firebase():
    """Initialize Firebase Admin once (Render + local support)."""
    if firebase_admin._apps:
        return

    creds_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    creds_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

    try:
        if creds_path and os.path.exists(creds_path):
            cred = credentials.Certificate(creds_path)
            print("✅ Firebase initialized using service-account.json file.")
        elif creds_json:
            # Convert escaped \n to actual newlines (important for Render)
            clean_json = creds_json.replace("\\n", "\n")
            cred = credentials.Certificate(json.loads(clean_json))
            print("✅ Firebase initialized using FIREBASE_SERVICE_ACCOUNT_JSON.")
        else:
            raise RuntimeError("❌ Firebase credentials missing in environment.")

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
