# Backend Methadata: FastAPI + silnik (kontekst budowania = katalog główny repo).
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY backend ./backend
COPY configs ./configs
COPY contracts ./contracts
# Tryb lekki (domyślny): bez modeli ML i bez ffmpeg. Pełny: METHADATA_EXTRAS=... METHADATA_WITH_FFMPEG=1 docker compose build
ARG EXTRAS=web,media,pdf,perceptual
ARG WITH_FFMPEG=0
RUN if [ "$WITH_FFMPEG" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*; fi
RUN pip install -e ".[${EXTRAS}]"
ENV METHADATA_DATA_DIR=/data METHADATA_HOST=0.0.0.0 METHADATA_PORT=8000
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "methadata_api"]
