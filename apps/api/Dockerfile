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
# 不以 root 執行；匯入原檔存在 /data/files（可在 Railway 掛 Volume 到 /data，重新部署後原檔才會保留）
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data/files && chown -R appuser /data
ENV LOCAL_FILE_DIR=/data/files
USER appuser

EXPOSE 8080
# HOST 預設 0.0.0.0（Render 等平台）；Railway 私有網路要收 IPv6，設 HOST=::
# RUN_MIGRATIONS=true 時啟動前先跑 alembic（給沒有 pre-deploy 指令的方案用）
CMD ["sh", "-c", "if [ \"$RUN_MIGRATIONS\" = \"true\" ]; then alembic upgrade head || exit 1; fi; exec uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
