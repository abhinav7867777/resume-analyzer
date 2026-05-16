from dotenv import load_dotenv
import os

load_dotenv()
key = os.getenv("OPENROUTER_API_KEY")

if key:
    print(f"✅ Key mili: {key[:8]}...{key[-4:]}")  # Partial print for safety
else:
    print("❌ Key nahi mili — None aa raha hai")