import os, time, uuid
from openai import OpenAI
from app.config import settings

client = OpenAI(api_key=settings.openai_api_key)

def run_gpt_summarization(instruction_path: str, markdown_text: str, prompt: str = "이 논문을 요약해줘") -> str:
    with open(instruction_path, 'r', encoding='utf-8') as f:
        instructions = f.read()

    vector_store = client.vector_stores.create(name="Summarizer")

    temp_filename = f"/tmp/{uuid.uuid4()}.md"
    with open(temp_filename, 'w', encoding='utf-8') as f:
        f.write(markdown_text)

    with open(temp_filename, "rb") as file:
        client.vector_stores.file_batches.upload_and_poll(
            vector_store_id=vector_store.id,
            files=[file]
        )

    assistant = client.beta.assistants.create(
        name="PaperSummarizer",
        model="gpt-4.1",
        instructions=instructions,
        tools=[{"type": "file_search"}],
        temperature=0.2,
        tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}},
        response_format={"type": "text"},
    )

    thread = client.beta.threads.create(tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}})
    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content= f"Please follow the instruction AND {vector_store.id}",
    )

    run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant.id)
    while run.status not in ("completed", "failed", "cancelled"):
        time.sleep(1)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

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

    return summary