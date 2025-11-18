# routes/Oauth.py
from fastapi import APIRouter, HTTPException, Request
from database import messages_col, accounts_col, avatars_col
from pydantic import BaseModel
from dotenv import load_dotenv
from routes.notifications import process_message_and_notify
from datetime import datetime
import httpx, os, base64, re, random

router = APIRouter()
load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")


# ----------------------------
# Helper - extract nested Gmail body
# ----------------------------
def extract_body(payload):
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if data and ("text/plain" in mime or "text/html" in mime):
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        except:
            return ""
    for part in payload.get("parts", []):
        text = extract_body(part)
        if text:
            return text
    return ""


COLOR_PALETTE = [
    "#4285F4", "#EA4335", "#FBBC05", "#34A853", "#9C27B0", "#00ACC1", "#7E57C2",
    "#FF7043", "#F06292", "#4DB6AC", "#1A237E", "#B71C1C", "#1B5E20", "#0D47A1"
]


async def resolve_sender_color(sender: str):
    existing = await avatars_col.find_one({"email": sender})
    if existing:
        return existing.get("char_color")
    color = random.choice(COLOR_PALETTE)
    await avatars_col.update_one({"email": sender}, {"$set": {"char_color": color}}, upsert=True)
    return color


# ----------------------------
# Gmail OAuth Callback
# ----------------------------
@router.get("/google/callback")
async def gmail_callback(code: str, state: str = None):
    if not state:
        raise HTTPException(status_code=400, detail="Missing state")

    # exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code"
            }
        )
    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    if not access_token:
        raise HTTPException(status_code=400, detail="Failed token exchange")

    # fetch Gmail profile
    async with httpx.AsyncClient() as client:
        prof_resp = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"}
        )
    gmail_email = prof_resp.json().get("emailAddress")

    # derive user_id from state
    try:
        import jwt
        decoded = jwt.decode(state, os.getenv("JWT_SECRET"), algorithms=["HS256"])
        user_id = decoded.get("user_id")
    except:
        raise HTTPException(status_code=400, detail="State decode failed")

    # update DB
    if refresh_token:
        await accounts_col.update_one(
            {"user_id": user_id, "gmail_email": gmail_email},
            {"$set": {"refresh_token": refresh_token}},
            upsert=True
        )
    else:
        await accounts_col.update_one(
            {"user_id": user_id, "gmail_email": gmail_email},
            {"$setOnInsert": {"connected_at": datetime.utcnow()}},
            upsert=True
        )

    # Pull only 1-2 initial emails
    async with httpx.AsyncClient() as client:
        inbox_resp = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=2",
            headers={"Authorization": f"Bearer {access_token}"}
        )

    for msg in inbox_resp.json().get("messages", []):
        msg_id = msg["id"]

        async with httpx.AsyncClient() as client:
            msg_full = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full",
                headers={"Authorization": f"Bearer {access_token}"}
            )

        data = msg_full.json()

        subject = ""
        from_header = ""
        for h in data["payload"]["headers"]:
            if h["name"] == "Subject": subject = h["value"]
            if h["name"] == "From": from_header = h["value"]

        sender_match = re.search(r"<(.+?)>", from_header)
        sender = sender_match.group(1) if sender_match else from_header
        body = extract_body(data.get("payload", {}))
        snippet = data.get("snippet", "")
        timestamp = int(data.get("internalDate"))

        result = await process_message_and_notify(
            user_id, body, sender, channel="email"
        )

        char_color = await resolve_sender_color(sender)

        email_doc = {
            "gmail_id": msg_id,
            "gmail_email": gmail_email,
            "user_id": user_id,
            "subject": subject,
            "from": from_header,
            "from_email": sender,
            "body": body,
            "snippet": snippet,
            "timestamp": timestamp,
            "char_color": char_color,
            "spam_score": result.get("score"),
            "confidence": result.get("confidence"),
            "reasoning": result.get("reasoning"),
            "highlighted_text": result.get("highlighted_text"),
            "final_decision": result.get("final_decision"),
            "suggestion": result.get("suggestion")
        }

        await messages_col.update_one(
            {"gmail_id": msg_id, "user_id": user_id},
            {"$set": email_doc},
            upsert=True
        )

    return "<h2>✔ Gmail Linked — Return to App</h2>"
