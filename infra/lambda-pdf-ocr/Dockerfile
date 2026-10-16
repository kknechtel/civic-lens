# Debian-slim base; AL2023's tesseract package is awkward.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

ARG LAMBDA_TASK_ROOT=/var/task
WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt awslambdaric

COPY lambda_function.py ${LAMBDA_TASK_ROOT}/

ENTRYPOINT [ "/usr/local/bin/python", "-m", "awslambdaric" ]
CMD [ "lambda_function.handler" ]
