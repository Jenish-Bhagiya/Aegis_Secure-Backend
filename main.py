from fastapi import FastAPI, HTTPException, Depends
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from base64 import urlsafe_b64decode
import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()
app = FastAPI()

# Simulate authenticated user (replace with real auth later)
async def get_current_user():
    return {"email": "user@example.com"}

@app.on_event("startup")
async def startup():
    app.mongodb = AsyncIOMotorClient(os.getenv("MONGODB_URL"))[os.getenv("DB_NAME")]

# Step 1: Get Google OAuth URL
@app.get("/auth/gmail/initiate")
async def gmail_initiate():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")]
            }
        },
        scopes=["https://www.googleapis.com/auth/gmail.readonly"]
    )
    flow.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    auth_url, _ = flow.authorization_url(prompt="consent")
    return {"auth_url": auth_url}

# Step 2: Handle OAuth callback and save tokens
@app.get("/auth/gmail/callback")
async def gmail_callback(code: str, user=Depends(get_current_user)):
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")]
            }
        },
        scopes=["https://www.googleapis.com/auth/gmail.readonly"]
    )
    flow.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    flow.fetch_token(code=code)
    
    # Save tokens to user account
    await app.mongodb.users.update_one(
        {"email": user["email"]},
        {
            "$set": {
                "gmail_access_token": flow.credentials.token,
                "gmail_refresh_token": flow.credentials.refresh_token
            }
        },
        upsert=True
    )
    return {"message": "Gmail connected"}

# Step 3: Fetch and scan emails
@app.get("/gmail/emails")
async def get_gmail_emails(user=Depends(get_current_user)):
    user_doc = await app.mongodb.users.find_one({"email": user["email"]})
    if not user_doc or not user_doc.get("gmail_access_token"):
        raise HTTPException(400, "Gmail not connected")
    
    service = build('gmail', 'v1', credentials=None)
    service._http.credentials = type('', (), {'token': user_doc["gmail_access_token"], 'refresh': lambda: None})()

    messages = service.users().messages().list(userId='me', maxResults=5).execute()
    result = []

    for msg in messages.get('messages', []):
        raw = service.users().messages().get(userId='me', id=msg['id']).execute()
        headers = {h['name'].lower(): h['value'] for h in raw['payload']['headers']}
        subject = headers.get('subject', '(No subject)')
        sender = headers.get('from', 'Unknown')
        date = headers.get('date', '')

        body = ""
        if 'parts' in raw['payload']:
            for part in raw['payload']['parts']:
                if part['mimeType'] == 'text/plain':
                    body = part['body'].get('data', '')
                    break
        elif 'body' in raw['payload']:
            body = raw['payload']['body'].get('data', '')
        if body:
            try:
                body = urlsafe_b64decode(body).decode('utf-8', errors='ignore')
            except:
                body = ""

        # Scam detection (replace with real model later)
        is_scam = "win" in subject.lower() or "free" in subject.lower() or "urgent" in body.lower()
        label = "Scam" if is_scam else "Safe"
        confidence = 95 if is_scam else 10
        explanation = "Suspicious keywords detected." if is_scam else "No threats found."

        result.append({
            "message_id": msg['id'],
            "subject": subject,
            "sender": sender,
            "timestamp": date,
            "label": label,
            "confidence_score": confidence,
            "explanation": explanation
        })

    return {"emails": result}