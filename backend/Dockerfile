FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/models

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Run as an unprivileged user. /models holds the embedding model so it survives restarts (mount a volume there).
RUN useradd --system --uid 10001 crm && mkdir -p /models && chown crm /models
USER crm

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# Behind a reverse proxy set CRM_TRUSTED_PROXY_HOPS and keep --proxy-headers off: the app reads X-Forwarded-For itself.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
