import os, time, uuid, json, datetime
from google import genai
from google.genai import types
from app.config import settings

client = genai.Client(api_key=settings.openai_api_key)

def run_gpt_summarization(instruction_path: str, markdown_text: str, content_list: object, prompt: str = "이 논문을 요약해줘", temperature=0.2) -> str:
    start_time = time.time()
        
    try:
        with open(instruction_path, 'r', encoding='utf-8') as f:
            instructions = f.read()
        
        if isinstance(markdown_text, str):
            markdown_bytes = markdown_text.encode('utf-8')
        else:
            markdown_bytes = markdown_text 

        if isinstance(content_list, dict) or isinstance(content_list, list):
            content_list_bytes = json.dumps(content_list, ensure_ascii=False).encode('utf-8')
            content_mime_type = 'text/plain'
        elif isinstance(content_list, str):
            content_list_bytes = content_list.encode('utf-8')
            content_mime_type = 'text/plain'
        else:
            content_list_bytes = content_list #
            content_mime_type = 'text/plain' 

        # 모델에 전달할 콘텐츠 파트 리스트 생성
        content_parts = [
            types.Part.from_bytes(
                data=markdown_bytes,
                mime_type='text/markdown'
            ),
            types.Part.from_bytes(
                data=content_list_bytes,
                mime_type=content_mime_type
            )
        ]
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] contents 로딩 완료")
        
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 요약 실행 시작")
        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-04-17",
            contents=content_parts,
            config=types.GenerateContentConfig(
                system_instruction=instructions,
                temperature=0.2
            ),
        )

        # 요약 결과 추출
        summary = response.text
        summary_length = len(summary)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 요약 결과 추출 완료: {summary_length}자")

        return summary
        
    finally:
        # 총 소요 시간 출력
        total_time = time.time() - start_time
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 논문 요약 완료 총 소요시간: {total_time:.1f}초")