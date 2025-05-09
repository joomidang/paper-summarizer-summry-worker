import os, time, uuid, json, datetime
from openai import OpenAI
from app.config import settings

client = OpenAI(api_key=settings.openai_api_key)

def run_gpt_summarization(instruction_path: str, markdown_text: str, content_list: object,prompt: str = "이 논문을 요약해줘", temperature=0.2) -> str:
    start_time = time.time()
    print(f"📋 [{datetime.datetime.now().strftime('%H:%M:%S')}] 논문 요약 시작")

    with open(instruction_path, 'r', encoding='utf-8') as f:
        instructions = f.read()
    
    vector_store = client.vector_stores.create(name="Summarizer")

    temp_filename = f"/tmp/{uuid.uuid4()}.md"
    with open(temp_filename, 'w', encoding='utf-8') as f:
        f.write(markdown_text)

    content_list_filename = f"/tmp/{uuid.uuid4()}_content_list.json"
    with open(content_list_filename, 'w', encoding='utf-8') as f:
        json.dump(content_list, f, ensure_ascii=False, indent=4)

    file_list = []
    try:
        with open(temp_filename, "rb") as md_file:
            file_list.append(md_file)
            with open(content_list_filename, "rb") as cl_file:
                file_list.append(cl_file)
                
                print(f"마크다운 및 content_list.json 파일 업로드 중...")
                client.vector_stores.file_batches.upload_and_poll(
                    vector_store_id=vector_store.id,
                    files=file_list
                )
                print(f"파일 업로드 완료")
    except Exception as e:
        print(f"파일 업로드 오류: {e}")
        raise e

    assistant = client.beta.assistants.create(
        name="PaperSummarizer",
        model="gpt-4.1",
        instructions=instructions,
        tools=[{"type": "file_search"}],
        temperature=temperature,
        tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}},
        response_format={"type": "text"},
    )

    thread = client.beta.threads.create(tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}})
    client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=f"""
            1. Apply this instruction methodically to the markdown files in vector_store {vector_store.id}.\n
            2. Also refer to the content_list.json file for the structured information about the paper's organization, tables, figures to ensure comprehensive summarization of all elements.
            3. **즉시 작업 진행**: 모든 지침을 읽고 즉시 요약 작업을 시작하세요. 진행 상황이나 계획에 대한 설명 없이 바로 요약 결과물을 제공해야 합니다.
            """,
        )
    run_start = time.time()
    print(f"▶️ [{datetime.datetime.now().strftime('%H:%M:%S')}] 요약 실행 시작")

    run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant.id)
    dots = 0
    while run.status not in ("completed", "failed", "cancelled"):
        dots = (dots + 1) % 4
        print(f"\r⏳ [{datetime.datetime.now().strftime('%H:%M:%S')}] 요약 진행 중: {run.status} {'.'*dots}", end="")
        time.sleep(1)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
    
    run_time = time.time() - run_start
    print(f"\n{'✅' if run.status == 'completed' else '❌'} [{datetime.datetime.now().strftime('%H:%M:%S')}] 요약 {run.status} ({run_time:.1f}초)")

    if run.status != "completed":
        raise RuntimeError(f"GPT 요약 실패: {run.status}")


    messages = client.beta.threads.messages.list(thread_id=thread.id)
    summary = ""
    for msg in messages.data:
        if msg.role == "assistant":
            for part in msg.content:
                if part.type == "text":
                    summary += part.text.value

    client.vector_stores.delete(vector_store.id)
    client.beta.assistants.delete(assistant.id)
    client.beta.threads.delete(thread.id)
    total_time = time.time() - start_time
    print(f"🏁 [{datetime.datetime.now().strftime('%H:%M:%S')}] 논문 요약 완료 | 총 소요시간: {total_time:.1f}초")

    return summary