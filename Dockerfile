# 1. Python 기반 slim 이미지 사용
FROM python:3.11-slim

# 2. 작업 디렉토리 설정
WORKDIR /app

# 3. 의존성 복사 및 설치
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 4. 애플리케이션 코드 복사
COPY . .
COPY instructions/ ./instructions/

# 5. 환경변수 설정 예시 (실제 배포 시 docker run 시점 또는 .env로 대체)
ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

# 6. 포트 열기 (FastAPI healthcheck용)
EXPOSE 8000

# 7. FastAPI 서버 실행
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
