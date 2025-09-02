# Dockerfile (backend)
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# +++ добавить git + ca-certificates + tini (если нужно)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates tini && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN python -m pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir "uvicorn[standard]"

COPY hhcli /app/hhcli

EXPOSE 5179
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["python","-m","uvicorn","hhcli.server:app","--host","0.0.0.0","--port","5179"]
