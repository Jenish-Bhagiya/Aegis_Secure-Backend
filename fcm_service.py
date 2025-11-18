# fcm_service.py
import os
import json
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, messaging

load_dotenv()

# 🔐 This must be in your .env as ONE line JSON:
# FIREBASE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":...}
FIREBASE_SERVICE_ACCOUNT_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")

if not FIREBASE_SERVICE_ACCOUNT_JSON:
    raise Exception("❌ FIREBASE_SERVICE_ACCOUNT_JSON is not set in .env")

try:
    # 1️⃣ Parse JSON from env
    service_account_info = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)

    # 2️⃣ Fix the private key (\\n → real newline)
    if "private_key" in service_account_info and isinstance(service_account_info["private_key"], str):
        service_account_info["private_key"] = service_account_info["private_key"].replace("\\n", "\n")

    # 3️⃣ Initialize Firebase with dict (same pattern as your older code)
    cred = credentials.Certificate(service_account_info)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)

    print("✅ Firebase initialized from FIREBASE_SERVICE_ACCOUNT_JSON env")

except Exception as e:
    raise Exception(f"❌ Firebase initialization failed: {e}")


async def send_fcm_notification(
    tokens: list,
    title: str,
    body: str,
    spam_score: float | None = None,
    is_sms: bool = False,
):
    """
    Send push notification via FCM.
    - tokens: list of FCM device tokens
    - title, body: notification UI
    - spam_score: 0–100 score (optional)
    - is_sms: True => SMS notification, False => Gmail
    """
    if not tokens:
        print("⚠️ No FCM tokens, skipping notification.")
        return

    data_payload = {
        "type": "sms" if is_sms else "mail",
        "spam_score": str(spam_score) if spam_score is not None else "",
    }

    message = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        data=data_payload,
        tokens=tokens,
    )

    try:
        response = messaging.send_multicast(message)
        print(
            f"📨 FCM sent → success={response.success_count}, "
            f"failed={response.failure_count}"
        )
        return {
            "success": response.success_count,
            "failure": response.failure_count,
        }
    except Exception as e:
        print(f"❌ Error sending FCM: {e}")
        return None
