import pika, json, requests, time
from app.config import settings
from app.openai_runner import run_gpt_summarization
from app.s3 import upload_markdown
from app.event import publish_summary_completed

def on_message(ch, method, properties, body):
    msg = json.loads(body)
    payload = msg["payload"]
    paper_id = payload["paperId"]
    markdown_url = payload["markdownUrl"]
    content_list_url = payload["contentListUrl"]
    prompt = payload.get("prompt") or "test"
    temperature = payload.get("temperature", 0.2)
    lang = payload.get("language", "ko")

    print(f"📥 요약 요청 수신 → paperId={paper_id}")

    try:
        markdown_text = requests.get(markdown_url).text
        content_list_json = requests.get(content_list_url).json()
        summary = run_gpt_summarization(
            instruction_path="instructions/p11.md",
            markdown_text=markdown_text,
            content_list=content_list_json,
            prompt=prompt,
            temperature=temperature
        )
        s3_key = upload_markdown(summary, paper_id)
        publish_summary_completed(paper_id, s3_key)

        ch.basic_ack(delivery_tag=method.delivery_tag)
        print(f"✅ 요약 완료 및 업로드 완료 → paperId={paper_id}, s3Key={s3_key}")
    except Exception as e:
        print(f"❌ 처리 실패: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

QUEUE_NAME = "summary.requested.queue"
EXCHANGE_NAME = "paper.direct"
ROUTING_KEY = "SUMMARY_REQUESTED"

def start_consumer():
    print("MQ 연결 대기중...")
    time.sleep(10)
    conn = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    channel = conn.channel()

    # 1. exchange 선언 (direct 타입)
    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="direct", durable=True)

    # 2. 큐 선언
    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    # 3. exchange와 queue 바인딩
    channel.queue_bind(exchange=EXCHANGE_NAME, queue=QUEUE_NAME, routing_key=ROUTING_KEY)

    # 4. consume 시작
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message, auto_ack=False)
    print("🟢 MQ Consumer 시작")
    channel.start_consuming()