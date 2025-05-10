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
    content_list_filename = args.file.replace('.md', '_content_list.json')
    instruction_filename = args.instruction
    temperature = args.temperature

    # OpenAI 클라이언트 초기화
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    # 파일 ID를 저장할 리스트
    all_file_ids = []

    try:
    # instruction 파일 읽기
        with open(instruction_filename, "r", encoding="utf-8") as f:
            instructions = f.read()

        print("파일 업로드 중...")
        # 메인 논문 파일 업로드 (어시스턴트용)
        main_file = client.files.create(
            file=open(target_filename, "rb"),
            purpose="assistants")
        all_file_ids.append(main_file.id)
        print(f"메인 파일 업로드 완료 (어시스턴트용): {main_file.id}")
        
        # content_list 파일 업로드 (어시스턴트용)
        content_list_file = client.files.create(
            file=open(content_list_filename, "rb"),
            purpose="assistants")
        all_file_ids.append(content_list_file.id)
        print(f"콘텐츠 리스트 파일 업로드 완료 (어시스턴트용): {content_list_file.id}")
        
        print("벡터 스토어 생성 중...")
        file_paths = [target_filename, content_list_filename]
        file_streams = [open(path, "rb") for path in file_paths]

        vector_store = client.vector_stores.create(name="Summarizer")

        file_batch = client.vector_stores.file_batches.upload_and_poll(
            vector_store_id=vector_store.id, files=file_streams
        )
        print(f"벡터 스토어 생성 완료: {vector_store.id}")
        print(f"파일 배치 상태: {file_batch.file_counts}")
        
        # 파일 ID 매핑 정보 생성
        vector_file_ids = []
        vector_main_file_id = None
        vector_content_list_id = None
        
        # 벡터 스토어 파일 ID 명확하게 추출
        try:
            # 방법 1: 벡터 스토어의 파일 목록 직접 조회
            vs_files = client.vector_stores.files.list(vector_store_id=vector_store.id).data
            print(f"벡터 스토어 파일 목록 조회 성공 (파일 수: {len(vs_files)})")
            
            for i, vs_file in enumerate(vs_files):
                vector_file_ids.append(vs_file.id)
                all_file_ids.append(vs_file.id)
                print(f"벡터 스토어 파일 #{i+1}: {vs_file.id}")
                
                # 첫 번째 파일이 main_file, 두 번째가 content_list_file에 해당
                if i == 0:
                    vector_main_file_id = vs_file.id
                    print(f"벡터 스토어 메인 파일 ID: {vector_main_file_id}")
                elif i == 1:
                    vector_content_list_id = vs_file.id
                    print(f"벡터 스토어 콘텐츠 리스트 ID: {vector_content_list_id}")
        except Exception as e:
            print(f"벡터 스토어 파일 ID 추출 중 오류: {e}")
            # 오류 발생 시 기본 파일 ID 사용
            vector_main_file_id = main_file.id
            vector_content_list_id = content_list_file.id

        # Assistant 생성 - 모든 파일 ID 전달
        print("Assistant 생성 중...")
        assistant = client.beta.assistants.create(
            name="PaperSummarizer",
            model="gpt-4.1",
            tools=[{"type": "code_interpreter"}, {"type": "file_search"}],
            instructions=instructions,
            temperature=temperature,
            tool_resources={
                "code_interpreter": {"file_ids": all_file_ids},
                "file_search": {"vector_store_ids": [vector_store.id]}
            },
            response_format={"type": "text"},
        )
        print(f"Assistant 생성 완료: {assistant.id}")

        print("Thread 생성 중...")
        thread = client.beta.threads.create(
            messages=[{
                "role": "user",
                "content": f"""
                1. Please read and comprehend all instructions carefully, paying close attention to the specified guidelines for academic paper summarization.
                
                2. You have access to the following resources:
                - Direct files for code interpreter: {main_file.id} and {content_list_file.id}
                - Vector store ({vector_store.id}) containing the following files:
                    * Main paper in vector store: {vector_main_file_id or "ID unavailable"}
                    * Content list in vector store: {vector_content_list_id or "ID unavailable"}
                
                3. IMPORTANT: When using file_search tool, search in the VECTOR STORE files ({vector_main_file_id or "Main paper"} and {vector_content_list_id or "Content list"}).
                When using code_interpreter tool, use the direct files ({main_file.id} and {content_list_file.id}).
                
                4. **Immediate Action Required**: After reading all instructions, begin the summarization process immediately. Provide the summary without any explanations or discussions about the progress or plans.
                """,
                "attachments": [
                    {"file_id": main_file.id, "tools": [{"type": "file_search"}, {"type": "code_interpreter"}]}
                ],
            }]
        )
        print(f"Thread 생성 완료: {thread.id}")

        print("요약 실행 중...")
        # run = client.beta.threads.runs.create_and_poll(
        #     thread_id=thread.id, assistant_id=assistant.id
        # )

        run = client.beta.threads.runs.create(
            thread_id=thread.id, 
            assistant_id=assistant.id,
            # additional_instructions=instructions,
            temperature=temperature,
        )
        while run.status not in ("completed", "failed", "cancelled"):
            time.sleep(1)
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            print(f"Run status: {run.status}")

        if run.status == "completed":
            print("요약 작업 완료!")
        else:
            print(f"요약 작업 실패: {run.status}")
            exit(1)

        print("응답 저장 중...")
        messages = client.beta.threads.messages.list(thread_id=thread.id)
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

                output_file = f"output/{instruction_filename}_논문_요약.md"
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                output_file = get_unique_filename(output_file)
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(summary)
                print(f"요약이 완료되었습니다. '{output_file}' 파일을 확인하세요.")
                break
    finally:
        # 리소스 정리 부분도 수정
        print("리소스 정리 중...")
        try:
            # 벡터 스토어가 있으면 먼저 삭제
            if 'vector_store' in locals():
                client.vector_stores.delete(vector_store.id)
                print(f"Vector Store 삭제 완료: {vector_store.id}")
                # 삭제 후 벡터 스토어 확인
                time.sleep(2)
                remaining = client.vector_stores.list().data
                if remaining:
                    print(f"경고: {len(remaining)}개의 벡터 스토어가 남아있습니다")
                    for rem_vs in remaining:
                        try:
                            client.vector_stores.delete(vector_store_id=rem_vs.id)
                            print(f"남은 벡터 스토어 삭제 완료: {rem_vs.id}")
                        except Exception as e:
                            print(f"남은 벡터 스토어 삭제 중 오류: {e}")
            
            # 모든 파일 ID 삭제
            for file_id in all_file_ids:
                try:
                    client.files.delete(file_id)
                    print(f"파일 삭제 완료: {file_id}")
                except Exception as e:
                    print(f"파일 삭제 중 오류: {e}")
            
            # 나머지 리소스 삭제
            if 'thread' in locals():
                client.beta.threads.delete(thread.id)
                print(f"Thread 삭제 완료: {thread.id}")
                
            if 'assistant' in locals():
                client.beta.assistants.delete(assistant.id)
                print(f"Assistant 삭제 완료: {assistant.id}")
                
            # 마지막으로 남은 파일들 삭제
            try:
                remaining_files = client.files.list().data
                for remaining_file in remaining_files:
                    if remaining_file.id not in all_file_ids:  # 중복 삭제 방지
                        client.files.delete(remaining_file.id)
                        print(f"남은 파일 삭제 완료: {remaining_file.id}")
            except Exception as e:
                print(f"남은 파일 삭제 중 오류: {e}")

            print("모든 리소스가 정리되었습니다.")
        except Exception as e:
            print(f"리소스 정리 중 오류 발생: {str(e)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="논문 요약기")
    parser.add_argument("--file","-f", help="요약할 파일명")
    parser.add_argument("--instruction", "-i", help="instruction 파일 경로")
    parser.add_argument("--temperature", "-t", type=float, default=0.4, help="온도 설정")
    args = parser.parse_args()
    main(args)