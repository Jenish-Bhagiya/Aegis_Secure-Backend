# routes/auth.py
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from dotenv import load_dotenv
import datetime, jwt, os, base64

from database import users_col, otps_col, accounts_col
from routes import otp

load_dotenv()
router = APIRouter()
security = HTTPBearer()

JWT_SECRET = os.getenv("JWT_SECRET", "supersecret")
JWT_ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ----------------------------
# REQUEST MODELS
# ----------------------------
class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class LoginResponse(BaseModel):
    token: str
    verified: bool

class SendOTPRequest(BaseModel):
    email: EmailStr

class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str

class SetNotificationPref(BaseModel):
    notification_pref: str  # "all" or "high_only"

class FCMTokenRequest(BaseModel):
    fcm_token: str


# ----------------------------
# PASSWORD UTILS
# ----------------------------
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def check_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def decode_jwt(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    decoded = decode_jwt(credentials.credentials)
    email = decoded.get("email")
    user = await users_col.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user["user_id"] = str(user["_id"])
    return user


# ----------------------------
# REGISTER
# ----------------------------
@router.post("/register")
async def register(req: RegisterRequest):
    if await users_col.find_one({"email": req.email}):
        raise HTTPException(status_code=400, detail="Email already exists")

    hashed_pw = hash_password(req.password)
    user_doc = {
        "name": req.name,
        "email": req.email,
        "password": hashed_pw,
        "verified": False,
        "avatar_base64": "",
        "fcm_tokens": [],
        "notification_pref": "all"
    }
    await users_col.insert_one(user_doc)

    otp_code = otp.generate_otp()
    await otp.store_otp(req.email, otp_code)
    await otp.send_otp_email_async(req.email, otp_code)

    return {"message": "User created. OTP sent."}


# ----------------------------
# LOGIN
# ----------------------------
@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    user = await users_col.find_one({"email": req.email})
    if not user or not check_password(req.password, user["password"]):
        raise HTTPException(status_code=400, detail="Invalid credentials")

    token = jwt.encode(
        {"email": req.email, "user_id": str(user["_id"]), "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=12)},
        JWT_SECRET, algorithm=JWT_ALGORITHM
    )

    return {"token": token, "verified": user.get("verified", False)}


# ----------------------------
# SEND OTP
# ----------------------------
@router.post("/send-otp")
async def send_otp_now(req: SendOTPRequest):
    if not await users_col.find_one({"email": req.email}):
        raise HTTPException(status_code=400, detail="User not found")

    otp_code = otp.generate_otp()
    await otp.store_otp(req.email, otp_code)
    await otp.send_otp_email_async(req.email, otp_code)
    return {"message": "OTP sent"}


# ----------------------------
# VERIFY OTP
# ----------------------------
@router.post("/verify-otp")
async def otp_verify(req: VerifyOTPRequest):
    if not await otp.verify_otp_in_db(req.email, req.otp):
        raise HTTPException(status_code=400, detail="Invalid OTP")

    await users_col.update_one({"email": req.email}, {"$set": {"verified": True}})
    await otps_col.delete_many({"email": req.email})
    return {"message": "Verified"}


# ----------------------------
# PROFILE
# ----------------------------
@router.get("/me")
async def get_profile(current_user: dict = Depends(get_current_user)):
    return {
        "name": current_user.get("name"),
        "email": current_user.get("email"),
        "avatar_base64": current_user.get("avatar_base64", ""),
        "notification_pref": current_user.get("notification_pref", "all")
    }


@router.post("/me/avatar")
async def upload_avatar(current_user: dict = Depends(get_current_user), file: UploadFile = File(...)):
    contents = await file.read()
    encoded = base64.b64encode(contents).decode("utf-8")
    await users_col.update_one({"_id": current_user["_id"]}, {"$set": {"avatar_base64": encoded}})
    return {"avatar_base64": encoded}


# ----------------------------
# STORE FCM TOKEN
# ----------------------------
@router.post("/register-fcm")
async def save_fcm_token(req: FCMTokenRequest, current_user: dict = Depends(get_current_user)):
    await users_col.update_one({"_id": current_user["_id"]}, {"$addToSet": {"fcm_tokens": req.fcm_token}})
    await accounts_col.update_many({"user_id": current_user["user_id"]}, {"$addToSet": {"fcm_tokens": req.fcm_token}})
    return {"status": "ok"}


# ----------------------------
# SET NOTIFICATION PREFERENCE
# ----------------------------
@router.post("/set-notification-pref")
async def update_pref(pref: SetNotificationPref, current_user: dict = Depends(get_current_user)):
    if pref.notification_pref not in ["all", "high_only"]:
        raise HTTPException(status_code=400, detail="Invalid preference")

    await users_col.update_one({"_id": current_user["_id"]}, {"$set": {"notification_pref": pref.notification_pref}})
    await accounts_col.update_many({"user_id": current_user["user_id"]}, {"$set": {"notification_pref": pref.notification_pref}})
    return {"status": "ok", "pref": pref.notification_pref}
