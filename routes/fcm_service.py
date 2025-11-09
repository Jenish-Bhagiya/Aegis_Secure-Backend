import os, json, asyncio, base64
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, messaging
from database import users_col

load_dotenv()


def _init_firebase():
    """Initialize Firebase Admin once (auto-fix for \\n issues)."""
    if firebase_admin._apps:
        return

    try:
        # Try Base64-encoded JSON first (Render safe)
        b64_data = os.getenv("FIREBASE_SERVICE_ACCOUNT_B64")
        creds_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

        if b64_data:
            decoded = base64.b64decode(b64_data)
            creds_dict = json.loads(decoded)

            # 🩹 Fix: Replace escaped newlines with real newlines in private key
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

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
    """Send notification to multiple device tokens (compatible for all firebase-admin versions)."""
    if not tokens:
        print("⚠️ No FCM tokens found — skipping push.")
        return {"success": 0, "failure": 0}

    await asyncio.to_thread(_init_firebase)

    try:
        # Prepare notification message
        notification = messaging.Notification(title=title, body=body)
        android_config = messaging.AndroidConfig(priority="high")
        payload = {k: str(v) for k, v in (data or {}).items()}

        # Check Firebase SDK version
        if hasattr(messaging, "send_multicast"):
            # ✅ New SDKs (v5+)
            msg = messaging.MulticastMessage(
                notification=notification,
                tokens=tokens,
                data=payload,
                android=android_config,
            )
            resp = await asyncio.to_thread(messaging.send_multicast, msg)
            print(f"📤 FCM Push Sent: {resp.success_count} success, {resp.failure_count} fail.")
            return {"success": resp.success_count, "failure": resp.failure_count}
        else:
            # 🧩 Fallback for old SDKs — send individually
            success, fail = 0, 0
            for token in tokens:
                msg = messaging.Message(
                    notification=notification,
                    token=token,
                    data=payload,
                    android=android_config,
                )
                try:
                    await asyncio.to_thread(messaging.send, msg)
                    success += 1
                except Exception as e:
                    print(f"⚠️ FCM send failed for token {token[:15]}...: {e}")
                    fail += 1
            print(f"📤 FCM Push Sent: {success} success, {fail} fail.")
            return {"success": success, "failure": fail}

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
