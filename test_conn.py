import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("DATABASE_URL")
print(f"Testing connection to: {url.split('@')[1]}") # Print host only for safety

try:
    conn = psycopg2.connect(url)
    print("Connection SUCCESS!")
    conn.close()
except Exception as e:
    print(f"Connection FAILED: {e}")
