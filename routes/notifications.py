from fastapi import APIRouter, Request
import httpx
from database import messages_col, accounts_col
import os, json, base64
from dotenv import load_dotenv
from websocket_manager import broadcast_new_email
from datetime import datetime, timezone

from fcm_service import send_fcm_notification_for_user
from database import users_col

load_dotenv()

router = APIRouter()

# Env variables
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
CYBER_SECURE_URI=os.getenv("CYBER_SECURE_API_URI")
if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    raise Exception("Google OAuth credentials are missing in .env")

async def get_spam_prediction(text: str):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                CYBER_SECURE_URI,
                json={"text": text},
                timeout=60.0
            )
            resp.raise_for_status()
            data = resp.json()

            return data.get("prediction", "unknown")
    except Exception as e:
        print("### Spam prediction failed:", repr(e))  
        return "unknown"


async def get_access_token_from_refresh(refresh_token: str):
    """Get a new Google access token using the refresh token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            }
        )
        data = resp.json()
        return data.get("access_token")


#-----------------------

@router.post("/analyze_text")
async def analyze_text_endpoint(data: dict):
    text = data.get("text", "")
    if not text:
        return {"prediction": "UNKNOWN"} 

    try:
        
        prediction = await get_spam_prediction(text)
        prediction_str = str(prediction).strip().upper() 
        if prediction_str not in ["SPAM", "HAM"]:
            prediction_str = "UNKNOWN"
        return {"prediction": prediction_str}
    except Exception as e:
        print("Error in analyze_text_endpoint:", e)
        return {"prediction": "UNKNOWN"}


@router.post("/gmail/notifications")
async def gmail_notifications(request: Request):
    try:
        raw_data = await request.json()
        if "message" not in raw_data:
            return {"status": "ignored"}

        
        msg_str = base64.b64decode(raw_data["message"]["data"]).decode("utf-8")
        msg = json.loads(msg_str)

        email_address = msg.get("emailAddress")
        history_id = msg.get("historyId")
        print(f"Gmail Push Notification for {email_address} | historyId={history_id}")

        if not email_address:
            return {"status": "error", "message": "Missing emailAddress in notification"}

        
        user = await accounts_col.find_one({"gmail_email": email_address})
        if not user or "refresh_token" not in user:
            print(f"⚠️ Missing refresh_token for {email_address}")
            return {"status": "ignored"}
        access_token = await get_access_token_from_refresh(user["refresh_token"])
        if not access_token:
            print(f"⚠️ Failed to get access_token for {email_address}")
            return {"status": "error"}

        async with httpx.AsyncClient() as client:
            start_history_id = user.get("last_history_id", history_id)
            history_resp = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/history",
                params={"startHistoryId": start_history_id},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            history_data = history_resp.json()

            for record in history_data.get("history", []):
                for msg_event in record.get("messages", []):
                    msg_id = msg_event["id"]
                    msg_resp = await client.get(
                        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full",
                        headers={"Authorization": f"Bearer {access_token}"}
                    )
                    msg_data = msg_resp.json()
                    headers = msg_data.get("payload", {}).get("headers", [])
                    subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
                    sender = next((h["value"] for h in headers if h["name"] == "From"), "")
                    snippet = msg_data.get("snippet", "")
                    combined_text = f"{subject} {snippet}"
                    spam_prediction = await get_spam_prediction(combined_text)
                    await messages_col.update_one(
                        {"user_id": user["user_id"], "gmail_email": email_address, "gmail_id": msg_id},
                        {"$set": {
                            "subject": subject,
                            "from": sender,
                            "snippet": snippet,
                            "timestamp": int(msg_data.get("internalDate", datetime.now(timezone.utc).timestamp()*1000)),
                            "spam_prediction": spam_prediction,
                        }},
                        upsert=True
                    )
        await accounts_col.update_one(
            {"gmail_email": email_address},
            {"$set": {"last_history_id": history_id}}
        )
        await broadcast_new_email(email_address)

        try:
            user_doc = await users_col.find_one({"email": user.get("email")})
            if user_doc:
                user_id = user_doc.get("user_id")
                pref = user_doc.get("notification_pref", "all")
                label = str(spam_prediction)
                try:
                    score = float(label)
                except:
                    score = 100.0 if label.upper() == "SPAM" else 0.0

                if pref == "all" or (pref == "high_only" and score >= 70):
                    await send_fcm_notification_for_user(
                        user_id,
                        title="New Email Alert",
                        body=f"{label} email detected — Score: {int(score)}",
                        data={
                            "type": "mail",
                            "score": str(int(score)),
                            "label": label,
                            "email": email_address,
                        },
                    )
        except Exception as fcm_err:
            print("⚠️ FCM Gmail error:", fcm_err)


        return {"status": "processed"}

    except Exception as e:
        import traceback
        print("Error in gmail_notifications:", str(e))
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
