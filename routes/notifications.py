# routes/notifications.py
from fastapi import APIRouter, HTTPException
import os
import httpx
from dotenv import load_dotenv
from fcm_service import send_fcm_notification
from database import users_col

load_dotenv()
router = APIRouter()

CYBER_SECURE_API_URI = os.getenv("CYBER_SECURE_API_URI")
THRESHOLD = 75  # final fixed value

if not CYBER_SECURE_API_URI:
    raise Exception("CYBER_SECURE_API_URI is missing in .env file")


async def call_ml_api(text: str) -> dict:
    """Send message text to ML model and return normalized response dict."""
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(CYBER_SECURE_API_URI, json={"text": text})
            resp.raise_for_status()
            data = resp.json()

        # Normalize expected output
        return {
            "score": data.get("score", 0),                        # 0–100
            "confidence": data.get("confidence", None),           # 0 (ham) | 1 (spam)
            "reasoning": data.get("reasoning", ""),
            "highlighted_text": data.get("highlighted_text", ""),
            "final_decision": data.get("final_decision", ""),
            "suggestion": data.get("suggestion", ""),
        }
    except Exception as e:
        print(f"❌ ML API error: {e}")
        return {
            "score": 0,
            "confidence": None,
            "reasoning": "",
            "highlighted_text": "",
            "final_decision": "",
            "suggestion": "",
        }


async def should_send_notification(user_id: str, score: float) -> bool:
    """Check notification preference logic before sending push alert."""
    user = await users_col.find_one({"_id": user_id}) or await users_col.find_one({"user_id": str(user_id)})

    pref = user.get("notification_pref", "all") if user else "all"

    if pref == "high_only":
        return score >= THRESHOLD
    return True


async def trigger_notification(user_id: str, channel: str, sender: str, score: float):
    """Trigger FCM only if preference + threshold rules are satisfied."""

    if not await should_send_notification(user_id, score):
        print("⚠ Notification skipped due to user preference")
        return

    body = f"{sender} • Risk Score: {score}"

    await send_fcm_notification(
        user_id=str(user_id),
        title="New Risk Alert",
        body=body,
        channel=channel,        # "sms" OR "gmail"
        score=score
    )


@router.post("/analyze-text")
async def analyze_text(payload: dict):
    """Public route (optional) for frontend to test ML scoring."""
    text = payload.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")

    return await call_ml_api(text)


async def process_message_and_notify(
    user_id: str,
    message_text: str,
    sender: str,
    channel: str
) -> dict:
    """
    Shared function used by SMS + Gmail route after saving into DB.
    Returns enriched ML data back to the caller.
    """

    result = await call_ml_api(message_text)

    score = result["score"]

    # Send push if qualified
    await trigger_notification(
        user_id=user_id,
        channel=channel,
        sender=sender,
        score=score
    )

    return result
