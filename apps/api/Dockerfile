# 帳務損益系統 API／worker 共用映像。Railway 各服務只是啟動指令不同（見 README「部署到 Railway」）。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install ".[real]"

COPY alembic.ini ./
COPY migrations ./migrations
COPY fixtures ./fixtures

# 不以 root 執行
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080
# 監聽 ::（IPv6）才收得到 Railway 私有網路的流量
CMD ["sh", "-c", "uvicorn app.main:app --host :: --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
