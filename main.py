from fastapi import FastAPI, HTTPException
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from base64 import urlsafe_b64decode
import os
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

@app.get("/auth/gmail/login")
async def gmail_login():
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

@app.get("/auth/gmail/callback")
async def gmail_callback(code: str):
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
    access_token = flow.credentials.token

    # ✅ Fix: Use Credentials object (no ADC error)
    creds = Credentials(access_token)
    service = build('gmail', 'v1', credentials=creds)

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

        # Scam detection
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
