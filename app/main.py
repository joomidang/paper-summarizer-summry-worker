from fastapi import FastAPI
from app.consumer import start_consumer
import threading

app = FastAPI()


@app.get("/")
async def health_check():
    return {"message": "summary-worker is running"}

@app.on_event("startup")
def startup_event():
    threading.Thread(target=start_consumer, daemon=True).start()