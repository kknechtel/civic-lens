# Build from the repo root so jurisdiction profiles are baked in:
#   docker build -f api/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

# tesseract/antiword: free OCR for scanned PDFs and legacy .doc text
# (services/ocr_pipeline.py, services/text_extract.py).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev tesseract-ocr tesseract-ocr-eng antiword && \
    rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ .
COPY jurisdictions/ /jurisdictions/
ENV JURISDICTIONS_DIR=/jurisdictions \
    OMP_THREAD_LIMIT=1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
