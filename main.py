from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from base64 import urlsafe_b64decode
import os
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
from datetime import datetime, timedelta
import jwt
from pydantic import BaseModel

# --- Config ---
app = FastAPI()
SECRET_KEY = os.getenv("JWT_SECRET", "your_strong_secret_here")
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- MongoDB Setup ---
@app.on_event("startup")
async def startup():
    app.mongodb = AsyncIOMotorClient(os.getenv("MONGODB_URL"))[os.getenv("DB_NAME")]

# --- Models ---
class UserInDB(BaseModel):
    email: str
    hashed_password: str
    gmail_accounts: list = []

class TokenData(BaseModel):
    email: str

# --- Auth Helpers ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=24))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(401, "Invalid token")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token")
    
    user = await app.mongodb.users.find_one({"email": email})
    if user is None:
        raise HTTPException(401, "User not found")
    return user

# --- Auth Endpoints ---
@app.post("/auth/signup")
async def signup(email: str = Form(...), password: str = Form(...)):
    if await app.mongodb.users.find_one({"email": email}):
        raise HTTPException(400, "Email already registered")
    hashed_pw = get_password_hash(password)
    await app.mongodb.users.insert_one({
        "email": email,
        "hashed_password": hashed_pw,
        "gmail_accounts": []
    })
    return {"message": "User created"}

@app.post("/auth/login")
async def login(response: HTMLResponse, email: str = Form(...), password: str = Form(...)):
    user = await app.mongodb.users.find_one({"email": email})
    if not user or not verify_password(password, user["hashed_password"]):
        raise HTTPException(401, "Invalid credentials")
    access_token = create_access_token(data={"sub": email})
    response = HTMLResponse(content="<script>localStorage.setItem('token', '{}'); window.location.href='/mail';</script>".format(access_token))
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=True, samesite="lax")
    return response

# --- Gmail Integration ---
@app.get("/auth/gmail/initiate")
async def gmail_initiate(user=Depends(get_current_user)):
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
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return {"auth_url": auth_url}

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

    creds = Credentials(flow.credentials.token)
    service = build('gmail', 'v1', credentials=creds)
    profile = service.users().getProfile(userId='me').execute()
    gmail_email = profile['emailAddress']

    await app.mongodb.users.update_one(
        {"email": user["email"]},
        {"$addToSet": {
            "gmail_accounts": {
                "email": gmail_email,
                "access_token": flow.credentials.token,
                "refresh_token": flow.credentials.refresh_token,
                "connected_at": datetime.utcnow().isoformat()
            }
        }}
    )
    return HTMLResponse(content="""
    <html><body>
        <h2>✅ Gmail connected!</h2>
        <a href="/mail">Go to Mail</a>
    </body></html>
    """)

# --- Fetch & Scan Emails ---
@app.get("/gmail/emails")
async def get_emails(account_email: str, user=Depends(get_current_user)):
    account = next((acc for acc in user.get("gmail_accounts", []) if acc["email"] == account_email), None)
    if not account:
        raise HTTPException(400, "Account not connected")

    creds = Credentials(account["access_token"])
    service = build('gmail', 'v1', credentials=creds)
    messages = service.users().messages().list(userId='me', maxResults=20).execute()
    result = []

    async with httpx.AsyncClient() as client:
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

            try:
                ml_resp = await client.post(
                    "https://cybersecure-backend-api.onrender.com/predict",
                    json={"text": f"{subject}\n{body}"}
                )
                prediction = ml_resp.json().get("prediction", "ham")
            except:
                prediction = "ham"

            label = "Scam" if prediction == "spam" else "Safe"
            confidence = 95 if label == "Scam" else 10
            explanation = "Suspicious content detected." if label == "Scam" else "No threats found."

            result.append({
                "message_id": msg['id'],
                "subject": subject,
                "sender": sender,
                "timestamp": date,
                "label": label,
                "confidence_score": confidence,
                "explanation": explanation,
                "body": body
            })

    return {"emails": result}

# --- List Accounts ---
@app.get("/gmail/accounts")
async def get_accounts(user=Depends(get_current_user)):
    accounts = [acc["email"] for acc in user.get("gmail_accounts", [])]
    return {"accounts": accounts}
