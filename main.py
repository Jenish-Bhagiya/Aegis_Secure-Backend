# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes import auth, gmail, Oauth, notifications, sms

app = FastAPI(title="Aegis Secure Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ROUTES
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(gmail.router, prefix="/gmail", tags=["gmail"])
app.include_router(Oauth.router, tags=["oauth"])
app.include_router(notifications.router, tags=["notifications"])
app.include_router(sms.router, prefix="/sms", tags=["sms"])

@app.get("/")
async def root():
    return {"status": "running", "app": "Aegis Secure Backend"}
