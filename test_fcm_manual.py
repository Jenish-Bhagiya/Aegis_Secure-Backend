import asyncio
from routes.fcm_service import send_fcm_notification_for_user

async def main():
    user_id = ""  # Replace this with your actual user_id from MongoDB
    await send_fcm_notification_for_user(
        user_id,
        title="🔔 Manual FCM Test",
        body="If you receive this, Firebase notifications are working 🎉",
        data={"type": "test"},
    )

asyncio.run(main())
