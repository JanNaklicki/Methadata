# Backend Methadata: FastAPI + silnik (kontekst budowania = katalog główny repo).
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY backend ./backend
COPY configs ./configs
COPY contracts ./contracts
RUN pip install -e '.[web,media,pdf]'
ENV METHADATA_DATA_DIR=/data METHADATA_HOST=0.0.0.0 METHADATA_PORT=8000
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "methadata_api"]
