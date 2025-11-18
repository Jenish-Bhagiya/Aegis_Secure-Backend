# fcm_service.py
import os
import json
import base64
import firebase_admin
from firebase_admin import credentials, messaging

def _init_firebase():
    print(f"🔥 Firebase Admin SDK version: {firebase_admin.__version__}")

    if firebase_admin._apps:
        print("⚠️ Firebase already initialized.")
        return

    try:
        b64_data = os.getenv("FIREBASE_SERVICE_ACCOUNT_B64")
        creds_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

        if b64_data:
            decoded = base64.b64decode(b64_data)
            creds_dict = json.loads(decoded)

            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

            cred = credentials.Certificate(creds_dict)
            print("✅ Firebase initialized using Base64 ENV variable.")
        elif creds_path and os.path.exists(creds_path):
            cred = credentials.Certificate(creds_path)
            print("✅ Firebase initialized using service-account.json file.")
        else:
            raise RuntimeError("❌ FIREBASE_SERVICE_ACCOUNT_B64 or FIREBASE_SERVICE_ACCOUNT_PATH missing.")

        firebase_admin.initialize_app(cred)
        print("✅ Firebase Admin SDK ready.")

    except Exception as e:
        print("❌ Firebase initialization failed:", e)
        raise


# initialize automatically on import
_init_firebase()


def send_fcm_notification(
    token: str = None,
    title: str = "AegisSecure",
    body: str = "",
    data: dict = None,
):
    if not token:
        print("❌ Cannot send FCM — token missing.")
        return {"success": False, "error": "missing token"}

    # FCM requires string-only data
    safe_data = {}
    if data:
        for k, v in data.items():
            safe_data[str(k)] = json.dumps(v) if not isinstance(v, str) else v

    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data=safe_data,
        token=token,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                sound="default",
                default_sound=True
            )
        )
    )

    try:
        message_id = messaging.send(message)
        print(f"✅ FCM sent → {message_id}")
        return {"success": True, "id": message_id}
    except Exception as e:
        print("❌ FCM send failed:", e)
        return {"success": False, "error": str(e)}
