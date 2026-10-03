FROM python:3.12.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY logging.json ./logging.json
RUN python -m pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 aeris \
    && mkdir -p /data \
    && chown aeris:aeris /data
USER aeris

EXPOSE 4100
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD ["python", "-c", "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:4100/api/health',timeout=2)); assert data['status']=='ok'"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4100", "--log-config", "logging.json", "--no-access-log"]
