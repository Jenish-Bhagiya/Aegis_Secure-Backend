from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI") 
client = AsyncIOMotorClient(MONGO_URI)

auth_db = client.auth_db   
mail_db = client.Mails_db  
sms_db= client.Sms_db
users_col = auth_db.users
accounts_col = mail_db.accounts
messages_col = mail_db.messages
otps_col=auth_db.otps
avatars_col = mail_db.avatars 
sms_messages_col = sms_db.sms_messages
