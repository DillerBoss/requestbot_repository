import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота
TOKEN = os.getenv("TOKEN")

# ID администратора 
ADMIN_ID = int(os.getenv("ADMIN_ID"))
