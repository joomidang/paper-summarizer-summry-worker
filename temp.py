import os, time, uuid, json, datetime, re
from google import genai
from google.genai import types
from app.config import settings

client = genai.Client(api_key=settings.gemini_api_key)

def call_gemini_model(system_instruction, user_content, temperature=0.0, model_name="gemini-2.5-flash-preview-05-20"):
    """Gemini API 호출 유틸리티"""
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature
            ),
        )
        return response.text
    except Exception as e:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Gemini API 오류: {e}")
        raise e

def prompt1_classify_keywords_summary(paper_md_content):
    """1단계: 논문 분류 및 키워드 추출"""
    system_instruction = """
You are an AI assistant. Your task is to analyze the provided research paper (in Markdown format).
1.  **Classify the paper** into ONE of the following 7 categories based on its title, abstract, and introduction:
    1.  모델 구조 제안 (Model Architecture)
    2.  도메인 응용 연구 (Domain Application)
    3.  학습 전략 개선 (Training Strategy)
    4.  이론 분석 (Theoretical Analysis)
    5.  데이터·벤치마크 구축 (Dataset / Benchmark)
    6.  시스템·인프라 최적화 (System / Infrastructure)
    7.  안전성·해석 가능성·윤리 (Safety / Interpretability / Ethics)
2.  **Extract key keywords** (up to 10) from the paper.
3.  **Write a 3-line topic summary** of the paper.

Return the output STRICTLY in the following format:
CLASSIFICATION: [Chosen Category Name]
KEYWORDS: [keyword1, keyword2, keyword3]
TOPIC_SUMMARY:
[Line 1 of summary]
[Line 2 of summary]
[Line 3 of summary]
"""
    
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 1단계: 논문 분류 및 키워드 추출 중...")
    response_text = call_gemini_model(system_instruction, paper_md_content, temperature=0.0)
    
    # 응답 파싱
    parsed_data = {
        "classification": "모델 구조 제안",  # 기본값
        "keywords": [],
        "topic_summary": "Could not generate summary."
    }
    
    try:
        classification_match = re.search(r"CLASSIFICATION:\s*(.+)", response_text)
        if classification_match:
            parsed_data["classification"] = classification_match.group(1).strip()

        keywords_match = re.search(r"KEYWORDS:\s*\[(.+)\]", response_text)
        if keywords_match:
            parsed_data["keywords"] = [k.strip() for k in keywords_match.group(1).split(',')]
        elif re.search(r"KEYWORDS:\s*(.+)", response_text): # Fallback if no brackets
            keywords_match_alt = re.search(r"KEYWORDS:\s*(.+)", response_text)
            parsed_data["keywords"] = [k.strip() for k in keywords_match_alt.group(1).split(',')]

        summary_match = re.search(r"TOPIC_SUMMARY:\s*\n([\s\S]+)", response_text)
        if summary_match:
            parsed_data["topic_summary"] = summary_match.group(1).strip()
            
    except Exception as e:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚠️ 1단계 파싱 오류: {e}")
    
    return parsed_data

def prompt1_5_validate_classification(paper_md_content, initial_classification):
    """1.5단계: 분류 검증"""
    system_instruction = f"""
You are tasked with validating a research paper classification.

Initial classification: {initial_classification}

Please:
1. Re-examine the paper and confirm if this classification is accurate
2. If incorrect, provide the correct classification from the 7 categories
3. Provide a confidence score (0-100) and brief justification

Format:
VALIDATION: [CORRECT/INCORRECT]
FINAL_CLASSIFICATION: [Category name]
CONFIDENCE: [0-100]
JUSTIFICATION: [Brief explanation]
"""
    
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 1.5단계: 분류 검증 중...")
    response = call_gemini_model(system_instruction, paper_md_content, temperature=0.0)
    
    # 검증 결과 파싱
    validation_match = re.search(r"VALIDATION:\s*(\w+)", response)
    classification_match = re.search(r"FINAL_CLASSIFICATION:\s*(.+)", response)
    confidence_match = re.search(r"CONFIDENCE:\s*(\d+)", response)
    
    return {
        "is_correct": validation_match.group(1).strip() == "CORRECT" if validation_match else True,
        "final_classification": classification_match.group(1).strip() if classification_match else initial_classification,
        "confidence": int(confidence_match.group(1)) if confidence_match else 80,
        "raw_response": response
    }

def prompt2_section_summary_from_template(paper_md_content, classification, paper_json_content_str=None):
    """2단계: 분류별 템플릿 기반 섹션 요약"""
    
    # 분류별 템플릿 정의
    common_sections_desc = """
**연구 동기 및 주요 기여 요약**
- 문제 배경·연구 동기 (≥ 1 단락)
- 핵심 기여·혁신점 (≥ 1 단락)

**한계점 및 향후 연구 방향**
- 연구·실험 한계·가정 제약 (≥ 1 단락)
- 후속 연구·응용 제안 (≥ 1 단락)
"""

    templates = {
        "모델 구조 제안": f"""
{common_sections_desc}

**전용 템플릿: 모델 구조 제안**
  1.  기존 모델 구조의 한계
  2.  제안 아키텍처 핵심 구성 요소
  3.  구조적 차별점
  4.  **Ablation & 모듈별 기여 분석**
  5.  실험 결과 및 성능 비교
  6.  **세부 구현 & 재현성**(코드·하드웨어)
  7.  구조 확장성 및 적용 범위
""",
        "도메인 응용 연구": f"""
{common_sections_desc}

**전용 템플릿: 도메인 응용 연구**
  1. 도메인 문제와 배경
  2. 기존 방식의 한계
  3. **도메인 특화 설계 & 지식 통합**
  4. 제안 모델·시스템 개요
  5. 대표 예시 및 실험 결과
  6. **윤리·규제·실사용 제약**
  7. 실무 적용성 및 확장성
""",
        "학습 전략 개선": f"""
{common_sections_desc}

**전용 템플릿: 학습 전략 개선**
  1. 기존 학습 전략의 한계
  2. 새로운 학습 전략·손실 함수
  3. **이론적 근거 & 분석**
  4. 적용 방식 및 예시 구조
  5. **Ablation & ablation 결과**
  6. 성능·학습 효율 및 **오버헤드 분석**
  7. 범용성 및 모듈화 가능성
""",
        "이론 분석": f"""
{common_sections_desc}

**전용 템플릿: 이론 분석**
  1. 이론적 질문 또는 목적
  2. 가정(Assumptions) 및 정의
  3. 핵심 이론 결과
  4. 이론 검증 실험 또는 시뮬레이션
  5. **가정 타당성 & 확장 범위**
  6. **실무적 영향 & 활용 시나리오**
""",
        "데이터·벤치마크 구축": f"""
{common_sections_desc}

**전용 템플릿: 데이터·벤치마크 구축**
  1. 기존 데이터·평가 한계
  2. 수집·전처리 방법
  3. 품질 보증 및 윤리 고려
  4. 비교 실험 및 베이스라인
  5. 사용 가이드 및 확장성
""",
        "시스템·인프라 최적화": f"""
{common_sections_desc}

**전용 템플릿: 시스템·인프라 최적화**
  1. 기존 인프라 병목
  2. 제안된 시스템 아키텍처
  3. 최적화 기법·알고리즘
  4. 성능·비용 비교 (Throughput, Latency, $/token 등)
  5. 확장성·재현성 및 배포 가이드
""",
        "안전성·해석 가능성·윤리": f"""
{common_sections_desc}

**전용 템플릿: 안전성·해석 가능성·윤리**
  1. 문제 정의 및 사회적 영향
  2. 한계·위험 분석
  3. 제안 기법(안전 정책·해석 모듈 등)
  4. 정량·정성 평가 (독성↓, 공정성↑, 투명성↑)
  5. 규제 적합성 및 실무 가이드
"""
    }

    chosen_template = templates.get(classification, templates["모델 구조 제안"])
    
    system_instruction = f"""
You are an AI assistant tasked with generating a detailed, section-by-section summary of a research paper.
The paper's classification is: **{classification}**.
You must follow the precise structure and subheadings for this classification as outlined below.
For EACH subheading, provide at least two detailed paragraphs. If the paper lacks explicit information for a subheading, infer or state that information is not detailed. Do not skip any subheadings.

**Structure for '{classification}':**
{chosen_template}

**Content Guidelines:**
-   Use Markdown for all formatting (e.g., `### Subheading Name`).
-   The input paper is in Markdown and may contain S3 URLs for figures/tables like `![Caption](https://s3...)`.
-   When you encounter or decide to include a figure or table (using its S3 URL from the input Markdown or the provided JSON data):
    1.  First, write detailed descriptive text (at least one paragraph) explaining the figure/table, its context, key findings, and significance to the paper's arguments.
    2.  Then, on a new line, place the Markdown for the image/table: `![Caption from paper](S3_URL_from_paper)`.
    3.  Ensure there's a blank line between the header and your descriptive text.
-   Do NOT include any self-meta commentary like "I will now summarize..." or "This section covers...". Directly provide the summary content under each heading.
-   If `content_list_json` is provided, it contains a list of figures/tables with S3 URLs and captions. Prioritize using these if the Markdown itself is missing them, or to confirm details.
"""
    
    # 콘텐츠 결합
    user_content = paper_md_content
    if paper_json_content_str:
        user_content += f"\n\n--- Supplemental Figure/Table List ---\n{paper_json_content_str}"
    
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 📝 2단계: '{classification}' 템플릿 기반 섹션 요약 중...")
    return call_gemini_model(system_instruction, user_content, temperature=0.2)

def prompt2_5_quality_check(section_summaries, classification):
    """2.5단계: 품질 검사"""
    system_instruction = f"""
You are tasked with quality-checking a research paper summary for classification: {classification}

Please evaluate the provided summary and:
1. Check if all required sections are covered
2. Identify any missing or weak sections
3. Suggest specific improvements
4. Rate overall quality (1-10)

Format your response as:
QUALITY_SCORE: [1-10]
MISSING_SECTIONS: [List any missing sections]
WEAK_SECTIONS: [List sections that need improvement]
IMPROVEMENTS: [Specific suggestions]
OVERALL_ASSESSMENT: [Brief assessment]
"""
    
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 2.5단계: 품질 검사 중...")
    return call_gemini_model(system_instruction, section_summaries, temperature=0.0)

def prompt2_6_refine_sections(section_summaries, quality_feedback, paper_md_content):
    """2.6단계: 피드백 기반 개선"""
    system_instruction = f"""
Based on the quality feedback below, please refine and improve the research paper summary.

Quality Feedback:
{quality_feedback}

Instructions:
1. Address all identified weaknesses
2. Add missing sections if any
3. Enhance weak sections with more detail
4. Maintain the original structure and formatting
5. Ensure technical accuracy and completeness

Provide the refined summary maintaining all Markdown formatting.
"""
    
    combined_content = f"Original Summary:\n{section_summaries}\n\nOriginal Paper:\n{paper_md_content}"
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔧 2.6단계: 피드백 기반 개선 중...")
    return call_gemini_model(system_instruction, combined_content, temperature=0.2)

def prompt3_final_assembly_insights_qa(full_summary_from_prompt2, classification, paper_metadata):
    """3단계: 최종 조립 및 Q&A 생성"""
    system_instruction = f"""
You are an AI assistant. Your task is to finalize a research paper summary by formatting it and adding Q&A and Key Implications.
The paper's classification is: **{classification}**.
The main body of the summary (section-by-section) is provided to you.

Your tasks are:
1.  **Format the entire summary** into the final Markdown structure.
    -   Use the provided paper metadata (title, author, etc.).
    -   Integrate the `full_summary_from_prompt2` which contains "연구 동기 및 주요 기여 요약", the specialized template sections for "{classification}", and "한계점 및 향후 연구 방향".
2.  **Generate 3 Q&A pairs** based on the core content of the summary (as per p5.md Step D). Each question and answer should be 1-2 sentences.
3.  **Generate "결론 중심 시사점" (Key Implications)** by answering the following questions based on the full summary:
    -   이 논문의 핵심 기여는 무엇이며, 어떤 실제 상황이나 연구 분야에서 가장 유용하게 활용될 수 있습니까? (What is the core contribution of this paper, and in what real-world situations or research areas can it be most usefully applied?)
    -   이 논문의 결과 중 어떤 점이 현재 진행 중인 연구나 개발 중인 서비스에 직접적으로 적용될 수 있으며, 적용 시 특별히 주의해야 할 점은 무엇입니까? (Which findings from this paper can be directly applied to ongoing research or services under development, and what specific precautions should be taken during application?)
    -   이 논문의 연구를 바탕으로 새로운 후속 연구를 기획한다면, 어떤 구체적인 연구 주제나 방향을 고려해볼 수 있습니까? (If planning new follow-up research based on this paper, what specific research topics or directions could be considered?)

Strictly adhere to the following final Markdown output format. Do not add any extra explanations before or after the formatted output.


# {paper_metadata['paper_title']}
**저자:** {paper_metadata['author']}
**출판일:** {paper_metadata['pub_date']}
**저널/출판사:** {paper_metadata['journal']}
**분야:** {paper_metadata['field']}
**DOI/링크:** {paper_metadata['doi_link']}

## 주제 유형: **{classification}**

## 최종 요약
{full_summary_from_prompt2}

### Q&A로 이해 돕기
**Q1. [Generated Question 1]?**
- A1. [Generated Answer 1].

**Q2. [Generated Question 2]?**
- A2. [Generated Answer 2].

**Q3. [Generated Question 3]?**
- A3. [Generated Answer 3].

### 결론 중심 시사점
**Q: 이 논문의 핵심 기여는 무엇이며, 어떤 실제 상황이나 연구 분야에서 가장 유용하게 활용될 수 있습니까?**
- A: [Generated Answer for Implication 1]

**Q: 이 논문의 결과 중 어떤 점이 현재 진행 중인 연구나 개발 중인 서비스에 직접적으로 적용될 수 있으며, 적용 시 특별히 주의해야 할 점은 무엇입니까?**
- A: [Generated Answer for Implication 2]

**Q: 이 논문의 연구를 바탕으로 새로운 후속 연구를 기획한다면, 어떤 구체적인 연구 주제나 방향을 고려해볼 수 있습니까?**
- A: [Generated Answer for Implication 3]

Ensure all placeholders in the template above (like `[Generated Question 1]`) are filled with relevant content.
The `{full_summary_from_prompt2}` part should be inserted as is, as it's the pre-generated content.
"""
    
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🎯 3단계: 최종 조립 및 Q&A 생성 중...")
    return call_gemini_model(system_instruction, full_summary_from_prompt2, temperature=0.15)

def prompt4_final_review(final_summary):
    """4단계: 최종 검토 및 다듬기"""
    system_instruction = """
이 연구 논문 요약에 대한 최종 검토를 수행하세요. 다음 사항들을 확인하세요:
1. 용어 및 형식의 일관성
2. 섹션 간의 논리적 흐름
3. 정보의 완전성
4. 문법 및 명확성
5. 적절한 마크다운 형식

모든 내용을 유지하면서 명확성과 흐름을 개선한 최종 완성 버전을 제공하세요.
"""
    
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✨ 4단계: 최종 검토 및 다듬기 중...")
    return call_gemini_model(system_instruction, final_summary, temperature=0.1)

def run_gpt_summarization(instruction_path: str, markdown_text: str, content_list: object, prompt: str = "이 논문을 요약해줘", temperature=0.2) -> str:
    """멀티턴 방식으로 논문 요약 실행"""
    start_time = time.time()
    
 
    paper_id = None
    if isinstance(content_list, dict) and "paperId" in content_list:
        paper_id = content_list["paperId"]
    elif isinstance(content_list, list) and len(content_list) > 0 and isinstance(content_list[0], dict) and "paperId" in content_list[0]:
        paper_id = content_list[0]["paperId"]
    
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 논문 요약 시작 | ID: {paper_id or 'unknown'}")
    
    try:
        # 지시문 파일 읽기 (멀티턴에서는 참고용으로만 사용)
        with open(instruction_path, 'r', encoding='utf-8') as f:
            instructions = f.read()        
        # JSON 콘텐츠 문자열 변환
        paper_json_content_str = None
        if content_list:
            paper_json_content_str = json.dumps(content_list, ensure_ascii=False, indent=2).encode('utf-8')
        
        # 1단계: 논문 분류 및 키워드 추출
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 1단계: 논문 분류 및 키워드 추출 중...")
        prompt1_output = prompt1_classify_keywords_summary(markdown_text)
        initial_classification = prompt1_output.get("classification", "모델 구조 제안")
        
        # 1.5단계: 분류 검증
        validation_result = prompt1_5_validate_classification(markdown_text, initial_classification)
        final_classification = validation_result["final_classification"]
        confidence = validation_result["confidence"]
        
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ 분류 확정: {final_classification} (신뢰도: {confidence}%)")
        
        # 2단계: 섹션별 요약
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 📝 2단계: '{final_classification}' 템플릿 기반 섹션 요약 중...")
        section_summaries = prompt2_section_summary_from_template(
            markdown_text, final_classification, paper_json_content_str
        )
        
        # 2.5단계: 품질 검사
        quality_feedback = prompt2_5_quality_check(section_summaries, final_classification)
        
        # 품질 점수 추출
        quality_score_match = re.search(r"QUALITY_SCORE:\s*(\d+)", quality_feedback)
        quality_score = int(quality_score_match.group(1)) if quality_score_match else 7
        
        
        # 2.6단계: 필요시 개선
        if quality_score < 8:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔧 품질 점수 {quality_score}/10 - 개선 진행")
            section_summaries = prompt2_6_refine_sections(
                section_summaries, quality_feedback, markdown_text
            )
        else:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ 품질 점수 {quality_score}/10 - 개선 생략")
        
        # 메타데이터 추출
        title_match = re.search(r"^#\s*(.+)", markdown_text, re.MULTILINE)
        paper_title = title_match.group(1).strip() if title_match else "논문 제목 추출 실패"
        
        paper_metadata = {
            "paper_title": paper_title,
            "author": "[저자 정보 필요]",
            "pub_date": "[출판 연도 필요]",
            "journal": "[저널/출판사 필요]",
            "field": "[학문 분야 필요]",
            "doi_link": "[DOI/링크 필요]"
        }
        
        # 3단계: 최종 조립 및 Q&A
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🎯 3단계: 최종 조립 및 Q&A 생성 중...")
        final_markdown_summary = prompt3_final_assembly_insights_qa(
            section_summaries, final_classification, paper_metadata
        )
        
        # 4단계: 최종 검토
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✨ 4단계: 최종 검토 및 다듬기 중...")
        summary = prompt4_final_review(final_markdown_summary)
        
        return summary
        
    except Exception as e:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ❌ 멀티턴 요약 오류: {e}")
        raise e
        
    finally:
        total_time = time.time() - start_time
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🏁 멀티턴 논문 요약 완료 | ID: {paper_id or 'unknown'} | 총 소요시간: {total_time:.1f}초")