from fastapi import APIRouter, HTTPException, Request
import httpx
from database import messages_col, accounts_col,avatars_col
import os
from jose import jwt
from dotenv import load_dotenv
from .notifications import get_spam_prediction
from datetime import datetime
import base64, json
import random
import re
from fastapi.responses import HTMLResponse
load_dotenv()
router = APIRouter()

# WebSocket broadcast function
from websocket_manager import broadcast_new_email

# Env variables
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
JWT_SECRET = os.getenv("JWT_SECRET")

if not JWT_SECRET:
    raise Exception("JWT_SECRET is not set in .env")


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


@router.get("/gmail/refresh")
async def refresh_access_token(user_id: str, gmail_email: str):
    user_data = await accounts_col.find_one({"user_id": user_id, "gmail_email": gmail_email})
    if not user_data or "refresh_token" not in user_data:
        raise HTTPException(status_code=400, detail="No refresh token found")

    refresh_token = user_data["refresh_token"]
    access_token = await get_access_token_from_refresh(refresh_token)
    if not access_token:
        raise HTTPException(status_code=400, detail="Failed to refresh access token")

    return {"access_token": access_token}


@router.get("/google/callback")
async def google_callback(code: str, state: str = None):
    if not state:
        raise HTTPException(status_code=400, detail="Missing user state token")
    
    # Decode JWT to get user_id
    try:
        payload = jwt.decode(state, JWT_SECRET, algorithms=["HS256"])
        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(status_code=400, detail="Invalid JWT token")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JWT token: {e}")

    async with httpx.AsyncClient() as client:
        # Get access & refresh tokens
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
            raise HTTPException(status_code=400, detail="Failed to get access token")

        # Get Gmail email
        profile_resp = await client.get(
            "https://www.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"})
        gmail_email = profile_resp.json().get("emailAddress")

        # photo_url = await get_user_profile_photo(access_token, gmail_email)

        # Save refresh token
        if refresh_token:
            await accounts_col.update_one(
                {"user_id": user_id, "gmail_email": gmail_email},
                {"$set": {"refresh_token": refresh_token}},
                upsert=True
            )

        else:
            await accounts_col.update_one(
                {"user_id": user_id, "gmail_email": gmail_email},
                {
                    "$setOnInsert": {"connected_at": datetime.utcnow()},
                    # "$set": {
                    #     "photo_url": photo_url,
                    #     "full_name": full_name,
                    # },
                },
                upsert=True,
            )
        messages_resp = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=10",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        messages_list = messages_resp.json().get("messages", [])
        emails = []

        for msg in messages_list:
            msg_id = msg["id"]
            msg_resp = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            msg_data = msg_resp.json()

            subject = next((h["value"] for h in msg_data["payload"]["headers"] if h["name"] == "Subject"), "")
            from_header = next((h["value"] for h in msg_data["payload"]["headers"] if h["name"] == "From"), "")
            match = re.search(r"<(.+?)>", from_header)
            sender_email = match.group(1) if match else from_header
            snippet = msg_data.get("snippet", "")
            combined_text = f"{subject} {snippet}"

            spam_prediction = await get_spam_prediction(combined_text)
            char_color = await get_sender_avatar_color(sender_email)

            emails.append({
                "gmail_id": msg_id,
                "gmail_email": gmail_email,
                "user_id": user_id,
                "subject": subject,
                "from": from_header,
                "from_email": sender_email,
                "char_color": char_color,
                "snippet": snippet,
                "timestamp": int(msg_data["internalDate"]),
                "spam_prediction": spam_prediction,
            })

        for email in emails:
            await messages_col.update_one(
                {"user_id": user_id, "gmail_email": gmail_email, "gmail_id": email["gmail_id"]},
                {"$set": email},
                upsert=True
            )
        topic_name = "projects/emailfetchingcheckandlearn/topics/AegisSecureMails"
        watch_resp = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/watch",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"topicName": topic_name, "labelIds": ["INBOX"]}
        )
        watch_data = watch_resp.json()
        if "historyId" in watch_data:
            await messages_col.update_one(
                {"user_id": user_id, "gmail_email": gmail_email},
                {"$set": {"last_history_id": watch_data["historyId"]}}
            )

    # return {
    #     "status": "success",
    #     "fetched": len(emails),
    #     "gmail_email": gmail_email,
    #     "watch_status": "started"
    # }
    return HTMLResponse(f"""
<html>
  <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AegisSecure — Account Linked</title>
    <style>
      :root {{
        --brand-color: #1F2A6E;
        --accent-color: #22C55E;
        --text-dark: #1E1E2F;
        --text-muted: #6B7280;
        --bg-light: #F4F6FB;
      }}
      body {{
        font-family: 'Inter', 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        background: radial-gradient(circle at top left, #E8ECFF, #F8FAFF 60%);
        color: var(--text-dark);
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        min-height: 100vh;
      }}
      .card {{
        background: white;
        border-radius: 20px;
        box-shadow: 0 12px 30px rgba(31, 42, 110, 0.1);
        padding: 50px 35px;
        text-align: center;
        width: 90%;
        max-width: 420px;
        animation: fadeIn 0.7s ease-in-out;
      }}
      @keyframes fadeIn {{
        from {{ opacity: 0; transform: translateY(20px); }}
        to {{ opacity: 1; transform: translateY(0); }}
      }}
      .checkmark {{
        width: 70px;
        height: 70px;
        border-radius: 50%;
        display: inline-block;
        position: relative;
        box-shadow: 0 0 0 4px rgba(34,197,94,0.1);
        background-color: var(--accent-color);
        margin-bottom: 25px;
        animation: pop 0.4s ease-in-out;
      }}
      .checkmark:after {{
        content: '';
        position: absolute;
        left: 20px;
        top: 15px;
        width: 16px;
        height: 32px;
        border-right: 4px solid white;
        border-bottom: 4px solid white;
        transform: rotate(45deg);
      }}
      @keyframes pop {{
        0% {{ transform: scale(0.5); opacity: 0; }}
        100% {{ transform: scale(1); opacity: 1; }}
      }}
      h2 {{
        margin: 0;
        color: var(--brand-color);
        font-size: 24px;
        font-weight: 700;
      }}
      p {{
        color: var(--text-muted);
        font-size: 15px;
        margin: 16px 0 28px;
        line-height: 1.6;
      }}
      .details {{
        background: var(--bg-light);
        border-radius: 10px;
        padding: 12px 15px;
        margin-bottom: 25px;
        font-size: 14px;
        color: var(--text-dark);
      }}
      .details b {{
        color: var(--brand-color);
      }}
      a.button {{
        display: inline-block;
        background: var(--brand-color);
        color: white;
        text-decoration: none;
        padding: 14px 26px;
        border-radius: 10px;
        font-weight: 600;
        font-size: 15px;
        transition: background 0.25s, transform 0.15s;
      }}
      a.button:hover {{
        background: #2E3BA8;
        transform: translateY(-1px);
      }}
      .note {{
        font-size: 13px;
        color: var(--text-muted);
        margin-top: 15px;
      }}
      footer {{
        margin-top: 35px;
        font-size: 12px;
        color: #A0A0B0;
      }}
    </style>
  </head>
  <body>
    <div class="card">
      <div class="checkmark"></div>
      <h2>You're Securely Connected</h2>
      <p>Gmail account <b>{gmail_email}</b> has been successfully linked to <b>AegisSecure</b>.<br>
      Your connection is encrypted and verified.</p>

      <a class="button" href="aegissecure://oauth-success?email={gmail_email}">
        Return to AegisSecure App
      </a>

      <div class="note">If your app doesn’t open automatically, tap the button above.</div>
    </div>
    <footer>© 2025 AegisSecure — Protecting You from Online Mishaps</footer>
  </body>
</html>
""")


COLOR_PALETTE = [
    "#4285F4", "#EA4335", "#FBBC05", "#34A853", "#9C27B0", "#00ACC1", "#7E57C2",
    "#FF7043", "#F06292", "#4DB6AC", "#1A237E", "#B71C1C", "#1B5E20", "#0D47A1",
    "#F57F17", "#880E4F", "#004D40", "#311B92", "#BF360C", "#33691E", "#C62828",
    "#283593", "#00695C", "#4527A0", "#E64A19", "#1976D2", "#AD1457", "#00838F",
    "#5D4037", "#455A64"
]


async def get_sender_avatar_color(sender_email: str):
    """Assign or fetch a fixed color for each sender email."""
    existing = await avatars_col.find_one({"email": sender_email})
    if existing and "char_color" in existing:
        return existing["char_color"]

    random_color = random.choice(COLOR_PALETTE)
    await avatars_col.update_one(
        {"email": sender_email},
        {"$set": {"char_color": random_color}},
        upsert=True
    )
    return random_color