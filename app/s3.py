import os, boto3, uuid
from app.config import settings

s3_client = boto3.client(
    's3',
    aws_access_key_id = os.getenv("AWS_ACCESS_KEY"),
    aws_secret_access_key = os.getenv("AWS_SECRET_KEY"),
    region_name = 'ap-northeast-2'
)

def upload_markdown(text: str, paper_id: int):
    key = f"summaries/{paper_id}/{uuid.uuid4()}.md"
    s3_client.put_object(Bucket=settings.s3_bucket, Key=key, Body=text.encode("utf-8"))
    return key