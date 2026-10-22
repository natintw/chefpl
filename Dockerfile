# 從 repo 根目錄建置後端（給 Root Directory 留空的平台用，例如目前的 Render 設定）。
# 內容與 apps/api/Dockerfile 相同，只是路徑多了 apps/api/；改其中一個時兩個都要改。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app
RUN pip install ".[real]"

COPY apps/api/alembic.ini ./
COPY apps/api/migrations ./migrations
COPY apps/api/fixtures ./fixtures

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080
CMD ["sh", "-c", "if [ \"$RUN_MIGRATIONS\" = \"true\" ]; then alembic upgrade head || exit 1; fi; exec uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
