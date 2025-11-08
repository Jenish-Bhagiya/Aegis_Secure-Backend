# # routes/fcm_service.py
# import os, json, asyncio
# from typing import List, Dict, Any, Optional
# from dotenv import load_dotenv
# import firebase_admin
# from firebase_admin import credentials, messaging
# from database import users_col

# load_dotenv()

# def _init_firebase():
#     """Initialize Firebase Admin once."""
#     if firebase_admin._apps:
#         return
#     creds_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
#     creds_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
#     if creds_path and os.path.exists(creds_path):
#         cred = credentials.Certificate(creds_path)
#     elif creds_json:
#         cred = credentials.Certificate(json.loads(creds_json))
#     else:
#         raise RuntimeError("Firebase credentials not configured")
#     firebase_admin.initialize_app(cred)

# async def send_fcm_multicast(tokens: List[str], title: str, body: str, data: Optional[Dict[str, str]] = None):
#     if not tokens:
#         return {"success": 0, "failure": 0}
#     await asyncio.to_thread(_init_firebase)
#     msg = messaging.MulticastMessage(
#         notification=messaging.Notification(title=title, body=body),
#         tokens=tokens,
#         data={k: str(v) for k, v in (data or {}).items()},
#     )
#     try:
#         resp = await asyncio.to_thread(messaging.send_multicast, msg)
#         print(f"✅ FCM Sent: {resp.success_count} ok, {resp.failure_count} fail")
#         return {"success": resp.success_count, "failure": resp.failure_count}
#     except Exception as e:
#         print("❌ FCM error:", e)
#         return {"success": 0, "failure": len(tokens)}

# async def send_fcm_notification_for_user(user_id: str, title: str, body: str, data: Optional[Dict[str, Any]] = None):
#     user = await users_col.find_one({"user_id": user_id})
#     if not user:
#         print(f"⚠️ No user found for {user_id}")
#         return
#     tokens = user.get("fcm_tokens", [])
#     if not tokens:
#         print(f"⚠️ No FCM tokens for {user_id}")
#         return
#     await send_fcm_multicast(tokens, title, body, data)



# routes/fcm_service.py
import os, json, asyncio
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, messaging
from database import users_col

load_dotenv()

def _init_firebase():
    """Initialize Firebase Admin once."""
    if firebase_admin._apps:
        return

    creds_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    creds_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

    try:
        if creds_path and os.path.exists(creds_path):
            cred = credentials.Certificate(creds_path)
            print("✅ Firebase initialized using service-account.json file.")
        elif creds_json:
            cred = credentials.Certificate(json.loads(creds_json))
            print("✅ Firebase initialized using FIREBASE_SERVICE_ACCOUNT_JSON.")
        else:
            raise RuntimeError("❌ Firebase credentials not found in .env or Render.")

        firebase_admin.initialize_app(cred)
        print("✅ Firebase Admin SDK successfully initialized!")

    except Exception as e:
        print("❌ Firebase initialization failed:", e)
        raise

async def send_fcm_multicast(tokens: List[str], title: str, body: str, data: Optional[Dict[str, str]] = None):
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

async def send_fcm_notification_for_user(user_id: str, title: str, body: str, data: Optional[Dict[str, Any]] = None):
    user = await users_col.find_one({"user_id": user_id})
    if not user:
        print(f"⚠️ No user found with user_id={user_id}")
        return

    tokens = user.get("fcm_tokens", [])
    if not tokens:
        print(f"⚠️ User {user_id} has no FCM tokens.")
        return

    await send_fcm_multicast(tokens, title, body, data)

