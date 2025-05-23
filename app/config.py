import os
from dotenv import load_dotenv
load_dotenv()

class Settings:
    rabbitmq_url = os.getenv("RABBITMQ_URL")
    s3_bucket = os.getenv("S3_BUCKET")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    gemini_api_key = os.getenv("GEMINI_API_KEY") # Add this line

settings = Settings()