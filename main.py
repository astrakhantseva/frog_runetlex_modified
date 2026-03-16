import asyncio
import csv
import io
import json
import os
import zipfile

import httpx
import pdfplumber
from docx import Document
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_ENDPOINT = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_MODEL = "qwen3-omni-flash"
MAX_FILES = 20

SUMMARIZE_PROMPT = (
    "Сделай суммаризацию текста на русском языке.\n"
    "Объём — не более 1000 символов.\n"
    "Структурируй по логическим разделам документа, используя короткие подзаголовки.\n"
    "Отвечай только суммаризацией, без вводных фраз.\n\n"
)

app = FastAPI(title="DOCX Summarizer")

# Serve static files (index.html etc.)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


ALLOWED_EXTENSIONS = (".docx", ".pdf")


def _docx_to_text(content: bytes) -> str:
    """Extract plain text from DOCX bytes."""
    doc = Document(io.BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs]
    return "\n".join(paragraphs)


def _pdf_to_text(content: bytes) -> str:
    """Extract plain text from PDF bytes using pdfplumber."""
    pages_text = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text)
    return "\n".join(pages_text)


def _file_to_text(filename: str, content: bytes) -> str:
    """Route to the correct extractor based on file extension."""
    lower = filename.lower()
    if lower.endswith(".docx"):
        return _docx_to_text(content)
    elif lower.endswith(".pdf"):
        return _pdf_to_text(content)
    else:
        raise ValueError(f"Unsupported format: {filename}")


@app.post("/convert")
async def convert(files: list[UploadFile] = File(...)):
    """Convert up to 20 DOCX files to TXT and return as a ZIP archive."""
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Максимум {MAX_FILES} файлов за один раз.")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for upload in files:
            if not upload.filename.lower().endswith(ALLOWED_EXTENSIONS):
                continue
            content = await upload.read()
            try:
                text = _file_to_text(upload.filename, content)
                txt_name = os.path.splitext(upload.filename)[0] + ".txt"
                # UTF-8 BOM so Windows Notepad renders Cyrillic correctly
                zf.writestr(txt_name, "\ufeff" + text)
            except Exception as e:
                # Include error stub in archive so caller knows which file failed
                err_name = os.path.splitext(upload.filename)[0] + "_ERROR.txt"
                zf.writestr(err_name, f"Ошибка обработки: {e}")

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=converted_txts.zip"},
    )


async def _summarize_one(filename: str, text: str) -> tuple[str, str]:
    """Send a single text to Qwen streaming API and collect the full response."""
    if not QWEN_API_KEY:
        return filename, "⚠ QWEN_API_KEY не задан."

    payload = {
        "model": QWEN_MODEL,
        "stream": True,
        "messages": [
            {"role": "user", "content": SUMMARIZE_PROMPT + text[:50000]}  # cap at ~50k chars
        ],
    }
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json; charset=utf-8",
    }

    result_parts: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                QWEN_ENDPOINT,
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    return filename, f"Ошибка API ({response.status_code}): {body.decode('utf-8', errors='replace')}"

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    try:
                        chunk = json.loads(line)
                        delta = chunk["choices"][0]["delta"].get("content", "")
                        if delta:
                            result_parts.append(delta)
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
    except Exception as e:
        return filename, f"Ошибка соединения: {e}"

    return filename, "".join(result_parts).strip()


@app.post("/summarize")
async def summarize(files: list[UploadFile] = File(...)):
    """Summarize up to 20 DOCX files via Qwen API and return a CSV."""
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Максимум {MAX_FILES} файлов за один раз.")

    # Read all files first (sequential I/O), then summarize in parallel
    tasks = []
    for upload in files:
        if not upload.filename.lower().endswith(ALLOWED_EXTENSIONS):
            continue
        content = await upload.read()
        try:
            text = _file_to_text(upload.filename, content)
        except Exception as e:
            text = f"[Ошибка чтения файла: {e}]"
        tasks.append(_summarize_one(upload.filename, text))

    if not tasks:
        raise HTTPException(status_code=400, detail="Нет корректных DOCX/PDF-файлов.")

    # Parallel summarization — all files simultaneously
    results: list[tuple[str, str]] = await asyncio.gather(*tasks)

    # Build CSV in memory
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer, quoting=csv.QUOTE_ALL)
    writer.writerow(["Имя файла", "Суммаризация"])
    for filename, summary in results:
        writer.writerow([filename, summary])

    csv_bytes = ("\ufeff" + csv_buffer.getvalue()).encode("utf-8")  # BOM for Excel
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=summaries.csv"},
    )
