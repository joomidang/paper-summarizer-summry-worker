import pika, json
from app.config import settings

EXCHANGE_NAME = "paper.direct"
ROUTING_KEY = "SUMMARY_COMPLETED"

def publish_summary_completed(paper_id: int, s3_key: str):
    payload = {
        "paperId": paper_id,
        "s3Key": s3_key
    }
    envelop = {
        "type": ROUTING_KEY,
        "payload": payload
    }

    conn = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    channel = conn.channel()

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="direct", durable=True)
    channel.basic_publish(
        exchange=EXCHANGE_NAME,
        routing_key=ROUTING_KEY,
        body=json.dumps(envelop)
    )
    conn.close()

    print(json.dumps(envelop, indent=2))
    print(f"📤 SUMMARY_COMPLETED 이벤트 발행 → paperId={paper_id}, s3Key={s3_key}")
