import os
import json
import time
import sys
import argparse
from openai import OpenAI
import tiktoken
from dotenv import load_dotenv

def main(args):
    # 환경 변수 로드
    load_dotenv()

    target_filename = args.file
    instruction_filename = args.instruction

    # OpenAI 클라이언트 초기화
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    try:
        # instruction 파일 읽기
        with open(instruction_filename, "r", encoding="utf-8") as f:
            instructions = f.read()

        file = open(target_filename, "rb")
        
        # content_list 파일 경로 생성 (경로에서 확장자를 바꾸거나 _content_list 추가)
        file_name, file_ext = os.path.splitext(target_filename)
        content_list_filename = f"{file_name}_content_list.json"
    
        
        file_streams = [file]  # 파일 리스트로 만들기

        
        # content_list 파일이 존재하면 추가
        if os.path.exists(content_list_filename):
            content_list_file = open(content_list_filename, "rb")
            file_streams.append(content_list_file)
            print(f"Content list 파일도 함께 업로드합니다: {content_list_filename}")
        else:
            print(f"경고: Content list 파일을 찾을 수 없습니다: {content_list_filename}")

        # 벡터 스토어 생성
        vector_store = client.vector_stores.create(
            name="Summarizer"
        )   
        # 파일 업로드및 벡터화 처리
        file_batch = client.vector_stores.file_batches.upload_and_poll(
            vector_store_id=vector_store.id, 
            files=file_streams,
        )
        print(f"Vector Store ID: {vector_store.id}")
        print(f"File batch status: {file_batch.status}")
        print(f"File counts: {file_batch.file_counts}")
        
        if hasattr(file_batch, 'files') and file_batch.files:
            for idx, uploaded_file in enumerate(file_batch.files):
                print(f"Uploaded file {idx+1} ID: {uploaded_file.id}")
        
        # vector_store_info = client.vector_stores.retrieve(vector_store_id=vector_store.id)
        # print(f"Vector store info: {vector_store_info}")

        # Assistant 및 Thread 생성
        print("Assistant 생성 중...")
        assistant_id = client.beta.assistants.create(
            name="PaperSummarizer",
            model="gpt-4.1",
            tools=[{"type": "file_search"}],
            instructions=instructions,
            temperature=0.1,
            tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}},
            response_format={"type": "text"},
        ).id
        print(f"Assistant 생성 완료: {assistant_id}")
        print("Thread 생성 중...")
        thread_id = client.beta.threads.create(
            tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}},
        ).id
        print(f"Thread 생성 완료: {thread_id}")
        
        # 메세지 생성
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=f"1. Please read and comprehend all instructions carefully, paying close attention to the specified guidelines for academic paper summarization.\n2. Apply these instructions methodically to the files in vector_store {vector_store.id}.\n3. Also refer to the content_list.json file for the structured information about the paper's organization, tables, figures, and equations to ensure comprehensive summarization of all elements.",
        )

        print("요약 실행 중...")
        run = client.beta.threads.runs.create(
            thread_id=thread_id, assistant_id=assistant_id
        )
        while run.status not in ("completed", "failed", "cancelled"):
            time.sleep(1)
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            print(f"Run status: {run.status}")

        if run.status == "completed":
            print("요약 작업 완료!")
        else:
            print(f"요약 작업 실패: {run.status}")
            exit(1)

        print("응답 저장 중...")
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        for msg in messages.data:
            if msg.role == "assistant":
                summary = ""
                for content_part in msg.content:
                    if content_part.type == "text":
                        summary += content_part.text.value

                def get_unique_filename(base_path):
                    if not os.path.exists(base_path):
                        return base_path
                    base_name, ext = os.path.splitext(base_path)
                    count = 1
                    while True:
                        new_path = f"{base_name}_{count}{ext}"
                        if not os.path.exists(new_path):
                            return new_path
                        count += 1

                output_file = "output/논문_요약.md"
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                output_file = get_unique_filename(output_file)
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(summary)
                print(f"요약이 완료되었습니다. '{output_file}' 파일을 확인하세요.")
                break
    finally:
        print("리소스 정리 중...")
        try:
            if 'vector_store' in locals():
                client.vector_stores.delete(vector_store.id)
                print(f"Vector Store 삭제 완료: {vector_store.id}")
            if 'assistant_id' in locals():
                client.beta.assistants.delete(assistant_id)
                print(f"Assistant 삭제 완료: {assistant_id}")
            if 'thread_id' in locals():
                client.beta.threads.delete(thread_id)
                print(f"Thread 삭제 완료: {thread_id}")
            print("모든 리소스가 정리되었습니다.")
        except Exception as e:
            print(f"리소스 정리 중 오류 발생: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="논문 요약기")
    parser.add_argument("--file","-f", help="요약할 파일명")
    parser.add_argument("--instruction", "-i", help="instruction 파일 경로")
    args = parser.parse_args()
    main(args)