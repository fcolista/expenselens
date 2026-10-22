FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apk add --no-cache \
    tesseract-ocr \
    tesseract-ocr-data-ita \
    tesseract-ocr-data-eng \
    poppler-utils

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .

RUN adduser -D -u 10001 appuser && mkdir -p /data/documents \
    && chown -R appuser:appuser /app /data
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
