# routes/fcm.py

from fastapi import APIRouter, Depends
from database import db
from routes.auth import get_current_user

router = APIRouter(prefix="/fcm")

fcm_collection = db["fcm_tokens"]


@router.post("/register")
async def register_fcm_token(data: dict, user=Depends(get_current_user)):
    token = data.get("fcm_token")
    if not token:
        return {"status": "error", "message": "missing token"}

    await fcm_collection.update_one(
        {"user_id": user["_id"]},
        {"$set": {"fcm_token": token}},
        upsert=True
    )

    return {"status": "success"}


@router.get("/info")
async def get_fcm_info(user=Depends(get_current_user)):
    entry = await fcm_collection.find_one({"user_id": user["_id"]})

    if not entry:
        return {
            "fcm_token": None,
            "notification_pref": "all"
        }

    return {
        "fcm_token": entry.get("fcm_token"),
        "notification_pref": entry.get("notification_pref", "all")
    }


@router.post("/set_pref")
async def set_pref(data: dict, user=Depends(get_current_user)):
    pref = data.get("notification_pref", "all")

    await fcm_collection.update_one(
        {"user_id": user["_id"]},
        {"$set": {"notification_pref": pref}},
        upsert=True
    )

    return {"status": "success", "notification_pref": pref}
