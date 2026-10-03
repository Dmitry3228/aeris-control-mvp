FROM python:3.12.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY aeris_simulator ./aeris_simulator
RUN python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 aeris
USER aeris

HEALTHCHECK --interval=10s --timeout=2s --retries=5 CMD ["python", "-c", "import pathlib,time; path=pathlib.Path('/tmp/aeris-simulator-ready'); assert path.exists() and time.time()-path.stat().st_mtime < 15"]
ENTRYPOINT ["python", "-m", "aeris_simulator"]
CMD ["--devices", "12", "--interval", "1", "--seed", "17"]
