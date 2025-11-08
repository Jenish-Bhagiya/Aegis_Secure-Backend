from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from datetime import datetime
from dotenv import load_dotenv
from .auth import get_current_user
from database import sms_messages_col
from websocket_manager import broadcast_new_email
import hashlib
import httpx
import os
from fastapi.encoders import jsonable_encoder
from bson import ObjectId

from .fcm_service import send_fcm_notification_for_user
from database import users_col

router = APIRouter()
load_dotenv()

CYBER_MODEL_URL = "https://cybersecure-backend-api.onrender.com/predict"

# --- Pydantic Models ---
class SmsMessage(BaseModel):
    address: str
    body: str
    date_ms: int
    type: str

class SmsSyncRequest(BaseModel):
    messages: List[SmsMessage]

# --- Utils ---
def generate_message_hash(address: str, body: str, date_ms: int):
    text = f"{address}-{body}-{date_ms}"
    return hashlib.sha256(text.encode()).hexdigest()


def serialize_doc(doc):
    """Convert MongoDB ObjectId and datetime to JSON serializable forms."""
    doc["_id"] = str(doc["_id"])
    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    return doc

async def analyze_sms_text(text: str) -> float:
    """Send message text to CyberSecure model for spam probability."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.post(
                CYBER_MODEL_URL,
                json={"text": text},
                headers={"Content-Type": "application/json"},
            )
        if res.status_code == 200:
            data = res.json()
            return float(data.get("prediction", 0.0))
        else:
            print(f"⚠️ Model API error: {res.status_code} {res.text}")
            return 0.0
    except Exception as e:
        print(f"❌ Error calling model: {e}")
        return 0.0

# --- POST /sms/sync ---
@router.post("/sync")
async def sync_sms(request: SmsSyncRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")

    inserted_count = 0
    for msg in request.messages:
        msg_hash = generate_message_hash(msg.address, msg.body, msg.date_ms)

        existing = await sms_messages_col.find_one({"hash": msg_hash, "user_id": user_id})
        if existing:
            continue

        
        spam_score = await analyze_sms_text(msg.body)
        if isinstance(spam_score, str) or spam_score is None:
            spam_score = 0.0


        message_doc = {
            "user_id": user_id,
            "address": msg.address,
            "body": msg.body,
            "timestamp": msg.date_ms,
            "type": msg.type,
            "hash": msg_hash,
            "spam_score": spam_score,
            "created_at": datetime.utcnow()
        }

        await sms_messages_col.insert_one(message_doc)
        inserted_count += 1
        await broadcast_new_email(message_doc)

        user_doc = await users_col.find_one({"user_id": user_id})
        pref = user_doc.get("notification_pref", "all")
        score = float(message_doc.get("spam_score", 0))
        label = message_doc.get("scan_result", message_doc.get("spam_prediction", "Unknown"))

        if pref == "all" or (pref == "high_only" and score >= 70): 
            await send_fcm_notification_for_user(
                user_id,
                title="New SMS Alert",
                body=f"{label} message detected — Score: {int(score)}",
                data={
                    "type": "sms",
                    "score": str(int(score)),
                    "label": label,
                    "timestamp": str(message_doc["timestamp"]),
                },
            )


    return {
        "status": "success",
        "inserted": inserted_count,
        "user_id": user_id,
    }


# --- GET /sms/all ---
# @router.get("/all")
# async def get_all_sms(current_user: dict = Depends(get_current_user)):
#     user_id = current_user.get("user_id")
#     messages = await sms_messages_col.find({"user_id": user_id}).sort("timestamp", -1).to_list(100)
#     return {"sms_messages": messages}


@router.get("/all")
async def get_all_sms(current_user: dict = Depends(get_current_user)):
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")

    # Fetch latest 100 SMS for this user
    messages = await sms_messages_col.find({"user_id": user_id}).sort("timestamp", -1).to_list(100)

    # Safely serialize ObjectId and datetime fields
    serialized_messages = [serialize_doc(m) for m in messages]

    return {"sms_messages": jsonable_encoder(serialized_messages)}