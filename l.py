import os
import tempfile
import json
import time
import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from uuid import uuid4

import boto3
import uvicorn
import httpx
from pydantic import BaseModel, Field
from fastapi import FastAPI, Body, Header, HTTPException, Depends, BackgroundTasks, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pyngrok import ngrok
from functools import wraps

# MagicPDF imports
from magic_pdf.data.data_reader_writer import FileBasedDataWriter, FileBasedDataReader
from magic_pdf.data.dataset import PymuDocDataset
from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze
from magic_pdf.tools import counter_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("app.log")
    ]
)
logger = logging.getLogger("magic-pdf-service")

# Configuration
class Config:
    # S3 configuration
    INPUT_BUCKET = os.getenv("INPUT_BUCKET", "paper-dev-test-magic-pdf-input")
    OUTPUT_BUCKET = os.getenv("OUTPUT_BUCKET", "paper-dev-test-magic-pdf-output")
    INPUT_PREFIX = os.getenv("INPUT_PREFIX", "")
    OUTPUT_PREFIX = os.getenv("OUTPUT_PREFIX", "")
    
    # AWS credentials
    AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
    AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")
    AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
    
    # API endpoints
    API_SERVER_URL = os.getenv("API_SERVER_URL", "http://43.202.9.100:8080/api/papers/{paper_id}/callback")
    
    # App settings
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
    USE_NGROK = os.getenv("USE_NGROK", "False").lower() == "true"
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8000"))

config = Config()

# Models
class User(BaseModel):
    userId: int
    username: str

class ParseRequest(BaseModel):
    paperId: int
    userId: int
    s3Url: str

class Response(BaseModel):
    code: str
    message: str
    data: Optional[Dict[str, Any]] = None

class CancelRequest(BaseModel):
    paperId: int

class JobStatus:
    """Job status tracker with persistent storage option"""
    
    def __init__(self):
        self.statuses = {}
        self.tokens = {}
        # Cleanup old jobs periodically
        self.cleanup_interval = timedelta(hours=24)
        self.max_age = timedelta(days=7)
    
    def create_job(self, paper_id: int, user_id: int) -> str:
        """Create a new job and return the job ID"""
        job_id = f"pdf-job-{paper_id}-{int(time.time())}"
        self.statuses[job_id] = {
            "status": "queued",
            "message": "Job queued, waiting to start",
            "paperId": paper_id,
            "userId": user_id,
            "created_at": datetime.now().isoformat()
        }
        return job_id
    
    def update_job(self, job_id: str, **kwargs) -> None:
        """Update job status with the provided fields"""
        if job_id in self.statuses:
            self.statuses[job_id].update(kwargs)
            self.statuses[job_id]["updated_at"] = datetime.now().isoformat()
    
    def get_job_by_paper_id(self, paper_id: int) -> Optional[Dict[str, Any]]:
        """Find a job by paper ID"""
        for job_id, status in self.statuses.items():
            if status.get("paperId") == paper_id:
                return {"job_id": job_id, **status}
        return None
    
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job status by job ID"""
        return self.statuses.get(job_id)
    
    def get_job_status(self, job_id: str) -> Optional[str]:
        """Safely get job status"""
        job = self.get_job(job_id)
        return job.get("status") if job else None
    
    def store_token(self, job_id: str, token: str) -> None:
        """Store authentication token for a job"""
        self.tokens[job_id] = token
    
    def get_token(self, job_id: str) -> Optional[str]:
        """Get authentication token for a job"""
        return self.tokens.get(job_id)
    
    async def cleanup_old_jobs(self):
        """Remove old jobs from memory to prevent memory leaks"""
        now = datetime.now()
        to_remove = []
        
        for job_id, status in self.statuses.items():
            created_at = datetime.fromisoformat(status.get("created_at", now.isoformat()))
            if now - created_at > self.max_age:
                to_remove.append(job_id)
        
        for job_id in to_remove:
            self.statuses.pop(job_id, None)
            self.tokens.pop(job_id, None)
            
        logger.info(f"Cleaned up {len(to_remove)} old jobs from memory")
    
    def save_to_file(self, filename="job_statuses.json"):
        """Save job statuses to a file for persistence"""
        with open(filename, "w") as f:
            json.dump({
                "statuses": self.statuses,
                # Don't save tokens to file for security
            }, f)
    
    def load_from_file(self, filename="job_statuses.json"):
        """Load job statuses from a file"""
        try:
            if os.path.exists(filename):
                with open(filename, "r") as f:
                    data = json.load(f)
                    self.statuses = data.get("statuses", {})
                    logger.info(f"Loaded {len(self.statuses)} jobs from file")
        except Exception as e:
            logger.error(f"Error loading job statuses from file: {str(e)}")


# Initialize job status tracker
job_tracker = JobStatus()

# S3 client
def get_s3_client():
    """Get a configured S3 client"""
    if not config.AWS_ACCESS_KEY or not config.AWS_SECRET_KEY:
        logger.warning("AWS credentials not found in environment variables")
        
    return boto3.client(
        's3',
        aws_access_key_id=config.AWS_ACCESS_KEY,
        aws_secret_access_key=config.AWS_SECRET_KEY,
        region_name=config.AWS_REGION
    )

# S3 operations
class S3Operations:
    @staticmethod
    def parse_s3_url(s3_url: str) -> tuple:
        """Parse S3 URL to extract bucket and key"""
        try:
            if "amazonaws.com" in s3_url:
                url_without_protocol = s3_url.replace("https://", "").replace("http://", "")
                parts = url_without_protocol.split("/")
                
                # Extract bucket from domain
                bucket_and_region = parts[0]  # e.g., "bucket-name.s3.region.amazonaws.com"
                bucket = bucket_and_region.split(".")[0]  # e.g., "bucket-name"
                
                # Key is everything after the domain
                key = "/".join(parts[1:])
            else:
                bucket = config.INPUT_BUCKET
                key = s3_url if not s3_url.startswith("/") else s3_url[1:]
            
            return bucket, key
        except Exception as e:
            logger.error(f"Error parsing S3 URL '{s3_url}': {str(e)}")
            raise ValueError(f"Invalid S3 URL format: {s3_url}")
    
    @staticmethod
    async def download_file(s3_url: str, local_path: str) -> tuple:
        """Download a file from S3 to local path"""
        s3_client = get_s3_client()
        
        try:
            # Parse S3 URL
            bucket, key = S3Operations.parse_s3_url(s3_url)
            
            # Run blocking S3 operations in a thread pool
            loop = asyncio.get_running_loop()
            
            # Check if file exists
            await loop.run_in_executor(
                None, 
                lambda: s3_client.head_object(Bucket=bucket, Key=key)
            )
            
            # Download file
            logger.info(f"Downloading: bucket={bucket}, key={key}, local_path={local_path}")
            await loop.run_in_executor(
                None,
                lambda: s3_client.download_file(bucket, key, local_path)
            )
            
            return True, None
        except Exception as e:
            error_msg = f"S3 download error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    @staticmethod
    async def upload_files(local_dir: str, paper_id: int) -> dict:
        """Upload all files in a directory to S3 bucket"""
        s3_client = get_s3_client()
        
        s3_urls = {
            "markdown": "",
            "contentList": "",
            "figures": [],
            "tables": []
        }
        
        try:
            loop = asyncio.get_running_loop()
            upload_tasks = []
            
            for root, _, files in os.walk(local_dir):
                for file in files:
                    if file.endswith(".pdf"):
                        continue
                    
                    local_file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(local_file_path, local_dir).replace('\\', '/')
                    
                    s3_key = f"{config.OUTPUT_PREFIX}papers/{paper_id}/{relative_path}"
                    
                    # Queue upload task
                    upload_tasks.append(loop.run_in_executor(
                        None,
                        lambda local_path=local_file_path, bucket=config.OUTPUT_BUCKET, key=s3_key: 
                            s3_client.upload_file(local_path, bucket, key)
                    ))
                    
                    # Categorize the file for URL generation
                    s3_url = f"https://{config.OUTPUT_BUCKET}.s3.amazonaws.com/papers/{paper_id}/{relative_path}"
                    
                    if file.endswith(".md"):
                        s3_urls["markdown"] = s3_url
                    elif file.endswith("_content_list.json"):
                        s3_urls["contentList"] = s3_url
                    elif "figure" in file and file.endswith((".jpg", ".png")):
                        s3_urls["figures"].append(s3_url)
                    elif "table" in file and file.endswith((".jpg", ".png")):
                        s3_urls["tables"].append(s3_url)
            
            # Execute all uploads in parallel
            if upload_tasks:
                await asyncio.gather(*upload_tasks)
                logger.info(f"Successfully uploaded {len(upload_tasks)} files to S3 for paper {paper_id}")
            
            return s3_urls
        except Exception as e:
            logger.error(f"Error uploading files to S3: {str(e)}")
            raise

# PDF Processing
class PDFProcessor:
    @staticmethod
    async def process_pdf(pdf_path: str, output_dir: str, name: str, job_id: str = None) -> dict:
        """Process a PDF file using MagicPDF"""
        
        def check_cancellation():
            """Check if the job has been cancelled"""
            if job_id:
                job = job_tracker.get_job(job_id)
                if job and job.get("status") == "canceled":
                    logger.info(f"Job cancellation detected: {job_id}")
                    return True
            return False
        
        try:
            # Initialize directories
            local_image_dir = os.path.join(output_dir, "images")
            local_md_dir = output_dir
            image_dir = "images"
            
            counter_manager.reset_counters()
            os.makedirs(local_image_dir, exist_ok=True)
            
            # Initialize readers/writers
            image_writer = FileBasedDataWriter(local_image_dir)
            md_writer = FileBasedDataWriter(local_md_dir)
            reader = FileBasedDataReader("")
            pdf_bytes = reader.read(pdf_path)
            
            # Process PDF
            if check_cancellation():
                return {"status": "canceled"}
                
            ds = PymuDocDataset(pdf_bytes)
            
            # Use OCR mode for all documents for consistency
            if check_cancellation():
                return {"status": "canceled"}
                
            infer_result = ds.apply(doc_analyze, ocr=True)
            pipe_result = infer_result.pipe_ocr_mode(image_writer)
            
            # Get model inference result
            if check_cancellation():
                return {"status": "canceled"}
                
            # Update with model inference result
            results = {"model_inference": "Generated"}
            
            # Generate and write markdown
            if check_cancellation():
                return {"status": "canceled"}
                
            md_content = pipe_result.get_markdown(image_dir)
            md_path = f"{name}.md"
            
            if check_cancellation():
                return {"status": "canceled"}
                
            pipe_result.dump_md(md_writer, md_path, image_dir)
            results["markdown"] = md_path
            
            # Generate and write content list
            if check_cancellation():
                return {"status": "canceled"}
                
            content_list_content = pipe_result.get_content_list(image_dir)
            content_list_path = f"{name}_content_list.json"
            
            if check_cancellation():
                return {"status": "canceled"}
                
            pipe_result.dump_content_list(md_writer, content_list_path, image_dir)
            results["content_list"] = content_list_path
            
            return results
            
        except Exception as e:
            logger.error(f"Error processing PDF: {str(e)}")
            return {
                "status": "error",
                "error": str(e),
                "message": "Error occurred during PDF processing"
            }
    
    @staticmethod
    async def extract_title_from_markdown(markdown_path: str, paper_id: int = None) -> str:
        """Extract title from markdown and update image paths"""
        try:
            with open(markdown_path, 'r', encoding='utf-8') as f:
                content = f.read()
                lines = content.split('\n')
                
                # Extract title from first line
                extracted_title = lines[0].strip() if lines else ""
                
                # Update image paths if paper_id is provided
                if paper_id is not None:
                    s3_base_url = f"https://{config.OUTPUT_BUCKET}.s3.ap-northeast-2.amazonaws.com/papers/{paper_id}"
                    import re
                    
                    # Convert markdown image links to HTML img tags with S3 URLs
                    updated_content = re.sub(
                        r'!\[(.*?)\]\(images/(.*?)\)',
                        rf'<img src="{s3_base_url}/images/\2"/>\n',
                        content
                    )
                    
                    # Write updated content back to markdown file
                    with open(markdown_path, 'w', encoding='utf-8') as fw:
                        fw.write(updated_content)
                    
                    logger.info(f"Markdown image paths updated to S3 URLs: {paper_id}")
            
            return extracted_title
            
        except Exception as e:
            logger.error(f"Error processing markdown: {str(e)}")
            return ""
    
    @staticmethod
    async def filter_content_list(json_file_path: str, output_file_path: str = None, paper_id: int = None) -> list:
        """Filter out text and equation items from content list"""
        try:
            # Load JSON file
            with open(json_file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
            
            # Filter items
            filtered_items = [item for item in data if item.get("type") not in ["text", "equation"]]
            
            # Update image paths if paper_id is provided
            if paper_id is not None:
                s3_base_url = f"https://{config.OUTPUT_BUCKET}.s3.ap-northeast-2.amazonaws.com/papers/{paper_id}"
                
                for item in filtered_items:
                    if 'img_path' in item and item['img_path'].startswith('images/'):
                        item['img_path'] = f"{s3_base_url}/{item['img_path']}"
            
            # Write filtered items to output file if specified
            if output_file_path:
                with open(output_file_path, 'w', encoding='utf-8') as out_file:
                    json.dump(filtered_items, out_file, indent=4, ensure_ascii=False)
            
            return filtered_items
            
        except Exception as e:
            logger.error(f"Error processing content list: {str(e)}")
            return None

# API callbacks
class APIClient:
    @staticmethod
    async def send_result(user_id: int, paper_id: int, s3_urls: dict, paper_title: str, job_id: str = None) -> dict:
        """Send parsing results to API server"""
        
        # Prepare API endpoint URL
        api_url = config.API_SERVER_URL.format(paper_id=paper_id)
        
        # Prepare request payload
        payload = {
            "title": paper_title,
            "markdownUrl": s3_urls.get("markdown", ""),
            "contentListUrl": s3_urls.get("contentList", ""),
            "figures": s3_urls.get("figures", []),
            "tables": s3_urls.get("tables", [])
        }
        
        # Prepare headers
        headers = {"Content-Type": "application/json"}
        
        # Add authorization token if available
        if job_id:
            token = job_tracker.get_token(job_id)
            if token:
                headers["Authorization"] = token
                logger.info(f"Including token for job: {job_id}")
            else:
                logger.info(f"No token available for job: {job_id}")
        
        # Log request details
        logger.info(f"API request URL: {api_url}")
        logger.info(f"API request headers: {json.dumps({k: '***' if k == 'Authorization' else v for k, v in headers.items()})}")
        logger.info(f"API request payload: {json.dumps(payload, ensure_ascii=False)}")
        
        try:
            # Use httpx for async HTTP requests (instead of requests)
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                response = await client.post(
                    api_url,
                    json=payload,
                    headers=headers
                )
            
            # Process response
            logger.info(f"API response status: {response.status_code}")
            
            try:
                response_json = response.json()
                logger.info(f"API response content: {json.dumps(response_json, ensure_ascii=False)}")
            except:
                response_json = {"raw": response.text}
                logger.warning(f"Failed to parse API response as JSON: {response.text}")
            
            result = {
                "status": "success" if response.status_code < 400 else "error",
                "status_code": response.status_code,
                "response": response_json
            }
            
            return result
            
        except httpx.ConnectError:
            logger.error("Connection error: Unable to connect to API server")
            return {
                "status": "error",
                "message": "Unable to connect to API server"
            }
        except httpx.TimeoutException:
            logger.error("Timeout: API server response timeout")
            return {
                "status": "error",
                "message": "API server response timeout"
            }
        except Exception as e:
            logger.error(f"API request error: {str(e)}")
            return {
                "status": "error",
                "message": str(e)
            }

# Main processing workflow
async def process_pdf_workflow(job_id: str, request: ParseRequest) -> None:
    """Main workflow for PDF processing"""
    paper_id = request.paperId
    user_id = request.userId
    s3_url = request.s3Url
    
    # Check for cancellation
    job = job_tracker.get_job(job_id)
    if job and job.get("status") == "canceled":
        logger.info(f"Job {job_id} was canceled before processing")
        return
    
    # Update job status
    job_tracker.update_job(job_id, status="running", message="Processing started")
    
    try:
        # Create temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                name_without_suffix = f"paper_{paper_id}"
                filename = s3_url.split("/")[-1]
                local_pdf_path = os.path.join(temp_dir, filename)
                
                # Step 1: Download PDF from S3
                job_status = job_tracker.get_job_status(job_id)
                if job_status == "canceled":
                    logger.info(f"Job {job_id} was canceled before download")
                    return
                    
                success, error_msg = await S3Operations.download_file(s3_url, local_pdf_path)
                if not success:
                    job_tracker.update_job(
                        job_id, 
                        status="failed", 
                        message=error_msg
                    )
                    logger.error(f"S3 download failed: {error_msg}")
                    return
                
                # Step 2: Process PDF
                job_status = job_tracker.get_job_status(job_id)
                if job_status == "canceled":
                    logger.info(f"Job {job_id} was canceled before processing")
                    return
                    
                results = await PDFProcessor.process_pdf(local_pdf_path, temp_dir, name_without_suffix, job_id)
                
                # Handle processing results
                if isinstance(results, dict):
                    if results.get("status") == "canceled":
                        job_tracker.update_job(
                            job_id,
                            status="canceled",
                            message="User requested cancellation during processing"
                        )
                        return
                    elif results.get("status") == "error":
                        job_tracker.update_job(
                            job_id,
                            status="failed",
                            message=results.get("message", "Error during PDF processing"),
                            error=results.get("error", "Unknown error")
                        )
                        return
                
                # Step 3: Process content list
                content_list_path = os.path.join(temp_dir, f"{name_without_suffix}_content_list.json")
                if os.path.exists(content_list_path):
                    logger.info("Filtering text and equation items from content list")
                    await PDFProcessor.filter_content_list(content_list_path, content_list_path, paper_id)
                
                # Step 4: Extract title and update image paths
                markdown_file_path = os.path.join(temp_dir, f"{name_without_suffix}.md")
                extracted_title = ""
                if os.path.exists(markdown_file_path):
                    extracted_title = await PDFProcessor.extract_title_from_markdown(markdown_file_path, paper_id)
                
                # Step 5: Upload results to S3
                job_status = job_tracker.get_job_status(job_id)
                if job_status == "canceled":
                    logger.info(f"Job {job_id} was canceled before upload")
                    return
                    
                s3_urls = await S3Operations.upload_files(temp_dir, paper_id)
                
                # Step 6: Send results to API server
                job_status = job_tracker.get_job_status(job_id)
                if job_status == "canceled":
                    logger.info(f"Job {job_id} was canceled after upload")
                    return
                    
                api_response = await APIClient.send_result(user_id, paper_id, s3_urls, extracted_title, job_id)
                
                # Update job status with results
                job_tracker.update_job(
                    job_id,
                    status="completed",
                    message="Processing completed successfully",
                    results=s3_urls,
                    title=extracted_title,
                    api_callback_status=api_response
                )
            except Exception as e:
                logger.error(f"Error processing in temp directory: {str(e)}")
                raise
    except Exception as e:
        logger.error(f"Error in PDF processing workflow for job {job_id}, paper {paper_id}: {str(e)}")
        job_tracker.update_job(
            job_id,
            status="failed",
            message=f"Error: {str(e)}"
        )

# Initialize FastAPI app
app = FastAPI(title="MagicPDF Processing Service")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Background task for periodic job cleanup
@app.on_event("startup")
async def setup_periodic_tasks():
    """Set up periodic tasks to run on application startup"""
    # Load saved job statuses if available
    job_tracker.load_from_file()
    
    # Start periodic cleanup task
    asyncio.create_task(periodic_cleanup())

async def periodic_cleanup():
    """Periodically clean up old jobs to prevent memory leaks"""
    while True:
        try:
            await job_tracker.cleanup_old_jobs()
            job_tracker.save_to_file()
            await asyncio.sleep(3600)  # Run cleanup every hour
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {str(e)}")
            await asyncio.sleep(300)  # If error, retry after 5 minutes

@app.on_event("shutdown")
async def save_state():
    """Save state before shutting down"""
    job_tracker.save_to_file()
    logger.info("Saved job statuses to file before shutdown")

# API endpoints
@app.post("/api/parse", response_model=Response, status_code=status.HTTP_202_ACCEPTED)
async def parse_paper(
    request: ParseRequest = Body(...),
    authorization: str = Header(None),
    background_tasks: BackgroundTasks = Depends()
):
    """
    Parse a PDF paper and extract content
    
    - **paperId**: ID of the paper
    - **userId**: ID of the user requesting the parsing
    - **s3Url**: S3 URL of the PDF file
    """
    # Generate job ID
    job_id = job_tracker.create_job(request.paperId, request.userId)
    paper_id = request.paperId
    user_id = request.userId
    
    # Log request details
    logger.info(f"Parsing request received: Paper ID={paper_id}, User ID={user_id}, S3 URL={request.s3Url}")
    
    # Store authorization token
    if authorization:
        auth_prefix = authorization[:10] if len(authorization) > 10 else authorization
        masked_auth = f"{auth_prefix}...{len(authorization)-15}chars" if len(authorization) > 15 else "[token]"
        job_tracker.store_token(job_id, authorization)
        logger.info(f"Authorization header received: {masked_auth}")
    else:
        logger.info("No Authorization header received")
    
    try:
        # Add processing task to background
        background_tasks.add_task(process_pdf_workflow, job_id, request)
        
        return Response(
            code="SUCCESS_OK",
            message="Parsing request successfully processed",
            data={
                "paperId": paper_id,
                "userId": user_id,
                "jobId": job_id,
                "status": "QUEUED"
            }
        )
    except Exception as e:
        logger.error(f"Error processing parsing request: {str(e)}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=Response(
                code="SERVER_ERROR",
                message="Server error occurred",
                data={"error": str(e)}
            ).dict()
        )

@app.post("/api/parse/cancel", response_model=Response)
async def cancel_parsing(request: CancelRequest = Body(...)):
    """
    Cancel a parsing job
    
    - **paperId**: ID of the paper whose parsing job should be canceled
    """
    paper_id = request.paperId
    
    # Find job by paper ID
    job = job_tracker.get_job_by_paper_id(paper_id)
    
    if not job:
        logger.warning(f"No parsing job found for paper ID: {paper_id}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=Response(
                code="NOT_FOUND",
                message="No parsing job found for the specified paper",
                data=None
            ).dict()
        )
    
    job_id = job.get("job_id")
    current_status = job.get("status")
    
    # Check if job can be canceled
    if current_status == "completed":
        logger.warning(f"Cannot cancel completed job for paper ID: {paper_id}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=Response(
                code="ALREADY_COMPLETED",
                message="Cannot cancel a job that has already completed",
                data=None
            ).dict()
        )
    
    if current_status == "failed":
        logger.warning(f"Cannot cancel failed job for paper ID: {paper_id}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=Response(
                code="ALREADY_FAILED",
                message="Cannot cancel a job that has already failed",
                data=None
            ).dict()
        )
    
    # Cancel the job
    job_tracker.update_job(
        job_id,
        status="canceled",
        message="User requested cancellation"
    )
    
    logger.info(f"Cancellation request received for job: {job_id}")
    return Response(
        code="SUCCESS_OK",
        message="Cancellation request received",
        data={
            "paperId": paper_id,
            "jobId": job_id
        }
    )

@app.get("/api/status/{paper_id}", response_model=Response)
async def get_paper_status(paper_id: int):
    """
    Get the status of a paper parsing job
    
    - **paper_id**: ID of the paper
    """
    # Find job by paper ID
    job = job_tracker.get_job_by_paper_id(paper_id)
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Paper job not found"
        )
    
    return Response(
        code="SUCCESS_OK",
        message="Paper processing status retrieved successfully",
        data={
            "paperId": paper_id,
            "status": job.get("status"),
            "message": job.get("message"),
            "results": job.get("results") if job.get("status") == "completed" else None
        }
    )

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy", 
        "version": "1.0.0",
        "active_jobs": len(job_tracker.statuses),
        "uptime": "OK"
    }

# Run server with graceful shutdown
def run_app():
    """Start the uvicorn server with the FastAPI app"""
    uvicorn.run(
        app, 
        host=config.HOST, 
        port=config.PORT,
        log_level="info",
        access_log=True
    )

# Server startup
def start_server(use_ngrok=False):
    """Start the FastAPI server"""
    if use_ngrok:
        # Start ngrok tunnel
        try:
            public_url = ngrok.connect(config.PORT)
            logger.info(f"🚀 FastAPI server running at: {public_url}")
        except Exception as e:
            logger.error(f"Failed to start ngrok tunnel: {str(e)}")
            logger.info(f"🚀 FastAPI server running at: http://{config.HOST}:{config.PORT}")
    else:
        logger.info(f"🚀 FastAPI server running at: http://{config.HOST}:{config.PORT}")
    
    # Start server in a safe way that can be interrupted
    run_app()

if __name__ == "__main__":
    start_server(use_ngrok=config.USE_NGROK)