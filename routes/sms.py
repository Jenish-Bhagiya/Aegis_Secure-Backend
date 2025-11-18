# routes/sms.py
from fastapi import APIRouter, Depends, HTTPException
from database import sms_messages_col
from routes.notifications import process_message_and_notify
from routes.auth import get_current_user
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

class DeviceSmsPayload(BaseModel):
    address: str
    body: str
    date_ms: int  # epoch ms timestamp
    type: str     # inbox / sent


@router.get("/sms/all")
async def get_all_sms(current_user: dict = Depends(get_current_user)):
    """Return all SMS messages for the logged-in user."""
    user_id = current_user.get("user_id")
    msgs = await sms_messages_col.find({"user_id": user_id}).sort("date_ms", -1).to_list(None)

    return {"count": len(msgs), "sms_messages": msgs}


@router.post("/sms/save")
async def save_sms(payload: DeviceSmsPayload, current_user: dict = Depends(get_current_user)):
    """
    Called by the device after reading SMS messages locally.
    Runs ML inference + stores in DB + triggers push if required.
    """

    user_id = current_user.get("user_id")

    # Prevent duplicates using (user_id + timestamp + address)
    existing = await sms_messages_col.find_one({
        "user_id": user_id,
        "address": payload.address,
        "date_ms": payload.date_ms
    })

    if existing:
        return {"status": "duplicate_skipped"}

    # ML Processing + conditional notification
    result = await process_message_and_notify(
        user_id=user_id,
        message_text=payload.body,
        sender=payload.address,
        channel="sms"
    )

    # Store final record
    sms_doc = {
        "user_id": user_id,
        "address": payload.address,
        "body": payload.body,
        "date_ms": payload.date_ms,
        "type": payload.type,
        
        # ML scored details
        "spam_score": result.get("score"),
        "confidence": result.get("confidence"),
        "reasoning": result.get("reasoning", ""),
        "highlighted_text": result.get("highlighted_text", ""),
        "final_decision": result.get("final_decision", ""),
        "suggestion": result.get("suggestion", ""),

        "saved_at": datetime.utcnow()
    }

    await sms_messages_col.insert_one(sms_doc)

    return {
        "status": "saved",
        "spam_score": sms_doc["spam_score"],
        "final_decision": sms_doc["final_decision"]
    }


@router.delete("/sms/clear")
async def clear_all_sms(current_user: dict = Depends(get_current_user)):
    """Developer utility: delete all SMS for this user."""
    user_id = current_user.get("user_id")
    await sms_messages_col.delete_many({"user_id": user_id})
    return {"status": "cleared"}
