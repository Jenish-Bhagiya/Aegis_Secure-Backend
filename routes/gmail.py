# routes/gmail.py
from fastapi import APIRouter, Depends, HTTPException
from database import messages_col, accounts_col, avatars_col
from routes.auth import get_current_user
from routes.notifications import process_message_and_notify
from pydantic import BaseModel
import httpx
from datetime import datetime
import base64, random, re, os

router = APIRouter()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")


# ----------------------------
# HELPER — Decode nested Gmail body
# ----------------------------
def extract_body(payload):
    if not payload:
        return ""

    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")

    if body_data and ("text/plain" in mime_type or "text/html" in mime_type):
        try:
            decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")
            return decoded.strip()
        except:
            return ""

    for part in payload.get("parts", []):
        text = extract_body(part)
        if text:
            return text

    return ""


# ----------------------------
# OPTIONAL — Consistent avatar colors
# ----------------------------
COLOR_PALETTE = [
    "#4285F4", "#EA4335", "#FBBC05", "#34A853", "#9C27B0", "#00ACC1", "#7E57C2",
    "#FF7043", "#F06292", "#4DB6AC", "#1A237E", "#B71C1C", "#1B5E20", "#0D47A1",
    "#F57F17", "#880E4F", "#004D40", "#311B92", "#BF360C", "#33691E", "#C62828",
    "#283593", "#00695C", "#4527A0", "#E64A19", "#1976D2", "#AD1457", "#00838F",
    "#5D4037", "#455A64"
]


async def resolve_sender_color(sender: str):
    existing = await avatars_col.find_one({"email": sender})
    if existing and "char_color" in existing:
        return existing["char_color"]

    color = random.choice(COLOR_PALETTE)
    await avatars_col.update_one(
        {"email": sender},
        {"$set": {"char_color": color}},
        upsert=True
    )
    return color


# ----------------------------
# ROUTE: Fetch inbox manually from device
# ----------------------------
class FetchRequest(BaseModel):
    gmail_email: str


@router.post("/gmail/fetch-latest")
async def fetch_latest(req: FetchRequest, current_user: dict = Depends(get_current_user)):
    gmail_email = req.gmail_email
    user_id = current_user.get("user_id")

    account = await accounts_col.find_one({"gmail_email": gmail_email, "user_id": user_id})
    if not account or "refresh_token" not in account:
        raise HTTPException(status_code=400, detail="Gmail account not linked")

    # --------------------------------------------
    # Refresh access token
    # --------------------------------------------
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": account["refresh_token"],
                "grant_type": "refresh_token"
            }
        )

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Failed to refresh access token")

    # --------------------------------------------
    # Pull last 10 Gmail messages
    # --------------------------------------------
    async with httpx.AsyncClient() as client:
        inbox_resp = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=10",
            headers={"Authorization": f"Bearer {access_token}"}
        )

    messages_list = inbox_resp.json().get("messages", [])
    stored_count = 0

    for msg in messages_list:
        msg_id = msg.get("id")
        if not msg_id:
            continue

        # avoid duplicates
        if await messages_col.find_one({"gmail_id": msg_id, "user_id": user_id}):
            continue

        # fetch full message data
        async with httpx.AsyncClient() as client:
            full_resp = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full",
                headers={"Authorization": f"Bearer {access_token}"}
            )

        data = full_resp.json()

        # extract headers
        subject = ""
        from_header = ""
        for h in data["payload"]["headers"]:
            if h["name"] == "Subject":
                subject = h["value"]
            if h["name"] == "From":
                from_header = h["value"]

        match = re.search(r"<(.+?)>", from_header)
        sender = match.group(1) if match else from_header

        body = extract_body(data.get("payload", {}))
        snippet = data.get("snippet", "")
        timestamp = int(data.get("internalDate", datetime.utcnow().timestamp() * 1000))

        # run ML + optionally notify
        result = await process_message_and_notify(
            user_id=user_id,
            message_text=body,
            sender=sender,
            channel="email"
        )

        # assign sender color
        char_color = await resolve_sender_color(sender)

        # final db object
        email_doc = {
            "gmail_id": msg_id,
            "gmail_email": gmail_email,
            "user_id": user_id,
            "subject": subject,
            "from": from_header,
            "from_email": sender,
            "char_color": char_color,
            "snippet": snippet,
            "body": body,
            "timestamp": timestamp,

            # ML score fields
            "spam_score": result.get("score"),
            "confidence": result.get("confidence"),
            "reasoning": result.get("reasoning", ""),
            "highlighted_text": result.get("highlighted_text", ""),
            "final_decision": result.get("final_decision", ""),
            "suggestion": result.get("suggestion", "")
        }

        await messages_col.insert_one(email_doc)
        stored_count += 1

    return {"status": "ok", "new_inserted": stored_count}
