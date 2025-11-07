from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI") 
client = AsyncIOMotorClient(MONGO_URI)

auth_db = client.auth_db    
#Name of Databse for Authentication of Ussers in AegisSecure Application
mail_db = client.Mails_db  
sms_db= client.Sms_db
#Name of Databse for mail connection, this is the databse which stores the connected mails from each mail. ex: I have mail abc, and I have connected 5 other mails to this so the DB stores this information.

users_col = auth_db.users
accounts_col = mail_db.accounts
messages_col = mail_db.messages
otps_col=auth_db.otps
avatars_col = mail_db.avatars 
sms_messages_col = sms_db.sms_messages
