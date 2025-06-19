import pika, json, requests, time, datetime
from app.config import settings
# from app.openai_runner import run_gpt_summarization
from app.gemini_runner import run_gpt_summarization
from app.s3 import upload_markdown
from app.event import publish_summary_completed

def on_message(ch, method, properties, body):
    msg = json.loads(body)
    payload = msg["payload"]
    paper_id = payload["paperId"]
    markdown_url = payload["markdownUrl"]
    content_list_url = payload["contentListUrl"]
    prompt = payload.get("prompt") or "test"
    temperature = payload.get("temperature", 0.1)
    lang = payload.get("language", "ko")

    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 요약 요청 수신 → paperId={paper_id}")

    try:
        markdown_text = requests.get(markdown_url).text
        content_list_json = requests.get(content_list_url).json()
        
        summary = run_gpt_summarization(
            instruction_path="instructions/instructions.md",
            markdown_text=markdown_text,
            content_list=content_list_json,
            prompt=prompt,
            temperature=temperature,
            enable_hallucination_check=True
        )
        s3_key = upload_markdown(summary, paper_id)
        publish_summary_completed(paper_id, s3_key)

        ch.basic_ack(delivery_tag=method.delivery_tag)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}]✅ 요약 완료 및 업로드 완료 → paperId={paper_id}, s3Key={s3_key}")
    except Exception as e:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}]❌ 처리 실패: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

QUEUE_NAME = "summary.requested.queue"
EXCHANGE_NAME = "paper.direct"
ROUTING_KEY = "SUMMARY_REQUESTED"

def start_consumer():
    print("MQ 연결 대기중...")
    time.sleep(10)
    
    # URL 파라미터에서 연결 파라미터로 변경하여 heartbeat 설정
    from urllib.parse import urlparse
    parsed_url = urlparse(settings.rabbitmq_url)
    
    connection_params = pika.ConnectionParameters(
        host=parsed_url.hostname,
        port=parsed_url.port or 5672,
        virtual_host=parsed_url.path.lstrip('/') or '/',
        credentials=pika.PlainCredentials(
            parsed_url.username or 'guest',
            parsed_url.password or 'guest'
        ),
        heartbeat=240,  # 3분
        blocked_connection_timeout=200,  # 3분
        connection_attempts=3,
        retry_delay=5.0
    )
    
    # 이 부분이 핵심 - connection_params를 실제로 사용
    conn = pika.BlockingConnection(connection_params)
    channel = conn.channel()

    # 1. exchange 선언 (direct 타입)
    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="direct", durable=True)

    # 2. 큐 선언
    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    # 3. exchange와 queue 바인딩
    channel.queue_bind(exchange=EXCHANGE_NAME, queue=QUEUE_NAME, routing_key=ROUTING_KEY)

    # 4. consume 시작
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message, auto_ack=False)
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🟢 MQ Consumer 시작")
    channel.start_consuming()