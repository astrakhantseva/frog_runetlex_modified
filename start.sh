#!/bin/bash
set -e
pip install fastapi uvicorn python-docx httpx python-multipart python-dotenv
exec uvicorn main:app --host 0.0.0.0 --port 8000
