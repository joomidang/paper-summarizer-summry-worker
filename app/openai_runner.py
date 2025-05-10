import os, time, uuid, json, datetime
from openai import OpenAI
from app.config import settings

client = OpenAI(api_key=settings.openai_api_key)

def run_gpt_summarization(instruction_path: str, markdown_text: str, content_list: object, prompt: str = "이 논문을 요약해줘", temperature=0.2) -> str:
    start_time = time.time()
    

    # 파일 ID를 저장할 리스트
    all_file_ids = []
    # 벡터 스토어 파일 ID 저장 변수
    vector_file_ids = []
    vector_main_file_id = None
    vector_content_list_id = None
    
    try:
        # 지시문 파일 읽기
        with open(instruction_path, 'r', encoding='utf-8') as f:
            instructions = f.read()
        print(f"📝 지시문 로드 완료: {instruction_path}")
        
        # 임시 디렉토리 설정
        temp_dir = "/tmp" if os.path.exists("/tmp") else "."
        
        # 마크다운 파일 생성
        temp_filename = f"{temp_dir}/{uuid.uuid4()}.md"
        with open(temp_filename, 'w', encoding='utf-8') as f:
            f.write(markdown_text)
        
        # content_list 파일 생성
        content_list_filename = f"{temp_dir}/{uuid.uuid4()}_content_list.json"
        with open(content_list_filename, 'w', encoding='utf-8') as f:
            json.dump(content_list, f, ensure_ascii=False, indent=4)
        
        print(f"💾 임시 파일 생성 완료")

        # 벡터 스토어 생성
        vector_store = client.vector_stores.create(name="Summarizer")
        print(f"🗄️ 벡터 스토어 생성: {vector_store.id}")
        
        # 파일 업로드 - 벡터 스토어용
        try:
            file_paths = [temp_filename, content_list_filename]
            file_streams = [open(path, "rb") for path in file_paths]
            
            upload_start = time.time()
            print(f"⬆️ 벡터 스토어에 파일 업로드 중...")
            
            file_batch = client.vector_stores.file_batches.upload_and_poll(
                vector_store_id=vector_store.id, 
                files=file_streams
            )
            
            # 스트림 닫기
            for stream in file_streams:
                stream.close()
                
            upload_time = time.time() - upload_start
            print(f"✅ 벡터 스토어 파일 업로드 완료: {file_batch.file_counts.total}개 파일, {upload_time:.2f}초 소요")
            
            # 벡터 스토어 파일 ID 명확하게 추출
            try:
                print(f"🔍 벡터 스토어 파일 ID 추출 중...")
                vs_files = client.vector_stores.files.list(vector_store_id=vector_store.id).data
                print(f"✅ 벡터 스토어 파일 목록 조회 성공 (파일 수: {len(vs_files)})")
                
                for i, vs_file in enumerate(vs_files):
                    vector_file_ids.append(vs_file.id)
                    print(f"📄 벡터 스토어 파일 #{i+1}: {vs_file.id}")
                    
                    # 첫 번째 파일이 main_file, 두 번째가 content_list_file에 해당
                    if i == 0:
                        vector_main_file_id = vs_file.id
                        print(f"📑 벡터 스토어 메인 파일 ID: {vector_main_file_id}")
                    elif i == 1:
                        vector_content_list_id = vs_file.id
                        print(f"📋 벡터 스토어 콘텐츠 리스트 ID: {vector_content_list_id}")
            except Exception as e:
                print(f"⚠️ 벡터 스토어 파일 ID 추출 오류 (계속 진행): {e}")
                # 추출 실패해도 계속 진행
            
        except Exception as e:
            print(f"❌ 벡터 스토어 파일 업로드 오류: {e}")
            raise e
            
        # 파일 업로드 - Assistant 파일 첨부용
        try:
            print(f"⬆️ Assistant용 파일 업로드 중...")
            
            # 메인 논문 파일 업로드
            main_file = client.files.create(
                file=open(temp_filename, "rb"),
                purpose="assistants")
            all_file_ids.append(main_file.id)
            
            # content_list 파일 업로드
            content_list_file = client.files.create(
                file=open(content_list_filename, "rb"),
                purpose="assistants")
            all_file_ids.append(content_list_file.id)
            
            print(f"✅ Assistant 파일 업로드 완료: {len(all_file_ids)}개 파일")
            
            # 벡터 스토어 ID 추출 실패 시 기본값 사용
            if vector_main_file_id is None:
                vector_main_file_id = main_file.id
                print(f"📌 벡터 스토어 메인 파일 ID 기본값 사용: {vector_main_file_id}")
            if vector_content_list_id is None:
                vector_content_list_id = content_list_file.id
                print(f"📌 벡터 스토어 콘텐츠 리스트 ID 기본값 사용: {vector_content_list_id}")
            
        except Exception as e:
            print(f"❌ Assistant 파일 업로드 오류: {e}")
            raise e

        # Assistant 생성
        print(f"🤖 Assistant 생성 중...")
        assistant = client.beta.assistants.create(
            name="PaperSummarizer",
            model="gpt-4.1",
            instructions=instructions,
            tools=[{"type": "file_search"}, {"type": "code_interpreter"}],
            temperature=temperature,
            tool_resources={
                "file_search": {"vector_store_ids": [vector_store.id]},
                "code_interpreter": {"file_ids": all_file_ids}
            },
            response_format={"type": "text"},
        )
        print(f"✅ Assistant 생성 완료: {assistant.id}")

        # Thread 생성
        print(f"🧵 Thread 생성 중...")
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
                
                4. {prompt}
                
                5. **즉시 작업 진행**: 모든 지침을 읽고 즉시 요약 작업을 시작하세요. 진행 상황이나 계획에 대한 설명 없이 바로 요약 결과물을 제공해야 합니다.
                """,
                "attachments": [
                    {"file_id": main_file.id, "tools": [{"type": "file_search"}, {"type": "code_interpreter"}]}
                ],
            }]
        )
        print(f"✅ Thread 생성 완료: {thread.id}")

        # 요약 실행
        run_start = time.time()
        print(f"▶️ [{datetime.datetime.now().strftime('%H:%M:%S')}] 요약 실행 시작")
        
        run = client.beta.threads.runs.create(
            thread_id=thread.id, 
            assistant_id=assistant.id
        )
        
        # 진행 상태 모니터링
        dots = 0
        status_log_time = time.time()
        while run.status not in ("completed", "failed", "cancelled"):
            dots = (dots + 1) % 4
            current_time = time.time()
            
            # 1초마다 진행 중 표시 업데이트
            print(f"\r⏳ [{datetime.datetime.now().strftime('%H:%M:%S')}] 요약 진행 중: {run.status} {'.'*dots}", end="")
            
            # 10초마다 상세 상태 로깅
            if current_time - status_log_time >= 10:
                elapsed = current_time - run_start
                print(f"\n⌛ 요약 계속 진행 중... (경과: {elapsed:.1f}초)")
                status_log_time = current_time
                
            time.sleep(1)
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        
        run_time = time.time() - run_start
        print(f"\n{'✅' if run.status == 'completed' else '❌'} [{datetime.datetime.now().strftime('%H:%M:%S')}] 요약 {run.status} ({run_time:.1f}초)")

        if run.status != "completed":
            raise RuntimeError(f"GPT 요약 실패: {run.status}")

        # 요약 결과 추출
        print(f"📄 요약 결과 추출 중...")
        messages = client.beta.threads.messages.list(thread_id=thread.id)
        summary = ""
        for msg in messages.data:
            if msg.role == "assistant":
                for part in msg.content:
                    if part.type == "text":
                        summary += part.text.value
        
        summary_length = len(summary)
        print(f"✅ 요약 결과 추출 완료: {summary_length}자")

        return summary
        
    finally:
        # 리소스 정리
        print(f"🧹 리소스 정리 중...")
        try:
            # 임시 파일 삭제
            if 'temp_filename' in locals() and os.path.exists(temp_filename):
                os.remove(temp_filename)
            if 'content_list_filename' in locals() and os.path.exists(content_list_filename):
                os.remove(content_list_filename)
                
            # 벡터 스토어 삭제
            if 'vector_store' in locals():
                client.vector_stores.delete(vector_store.id)
                print(f"✓ Vector Store 삭제 완료: {vector_store.id}")
                
                # 남은 벡터 스토어 확인 및 정리
                try:
                    time.sleep(1)
                    remaining = client.vector_stores.list().data
                    if remaining:
                        print(f"⚠️ 경고: {len(remaining)}개의 벡터 스토어가 남아있습니다")
                        for rem_vs in remaining:
                            try:
                                client.vector_stores.delete(vector_store_id=rem_vs.id)
                                print(f"✓ 남은 벡터 스토어 삭제 완료: {rem_vs.id}")
                            except Exception as e:
                                print(f"⚠️ 남은 벡터 스토어 삭제 중 오류: {e}")
                except Exception as e:
                    print(f"⚠️ 남은 벡터 스토어 확인 중 오류: {e}")
                
            # 모든 파일 ID 삭제
            for file_id in all_file_ids:
                try:
                    client.files.delete(file_id)
                    print(f"✓ 파일 삭제 완료: {file_id}")
                except Exception as e:
                    print(f"⚠️ 파일 삭제 중 오류 (무시됨): {e}")
                    
            # Assistant 삭제
            if 'assistant' in locals():
                client.beta.assistants.delete(assistant.id)
                print(f"✓ Assistant 삭제 완료: {assistant.id}")
                
            # Thread 삭제
            if 'thread' in locals():
                client.beta.threads.delete(thread.id)
                print(f"✓ Thread 삭제 완료: {thread.id}")
            
            # 마지막으로 남은 파일들 삭제
            try:
                remaining_files = client.files.list().data
                for remaining_file in remaining_files:
                    if remaining_file.id not in all_file_ids:  # 중복 삭제 방지
                        client.files.delete(remaining_file.id)
                        print(f"✓ 남은 파일 삭제 완료: {remaining_file.id}")
            except Exception as e:
                print(f"⚠️ 남은 파일 삭제 중 오류: {e}")
            
            print(f"✅ 모든 리소스 정리 완료")
            
        except Exception as e:
            print(f"⚠️ 리소스 정리 중 오류 발생 (요약 결과에는 영향 없음): {e}")
        
        # 총 소요 시간 출력
        total_time = time.time() - start_time
        print(f"🏁 [{datetime.datetime.now().strftime('%H:%M:%S')}] 논문 요약 완료 총 소요시간: {total_time:.1f}초")