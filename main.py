from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes import auth, gmail, Oauth, notifications, sms, fcm

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ROUTES
app.include_router(auth.router, prefix="/auth")
app.include_router(gmail.router, prefix="/gmail")
app.include_router(Oauth.router, prefix="/auth")
app.include_router(notifications.router, prefix="/notifications")
app.include_router(sms.router, prefix="/")
app.include_router(fcm.router, prefix="/fcm")

@app.get("/")
async def root():
    return {"status": "ok", "message": "Aegis Secure Backend running"}
