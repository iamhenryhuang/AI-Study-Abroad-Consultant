FROM python:3.11-slim

WORKDIR /app

# 系統依賴
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 先只複製 requirements，利用 layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製後端程式碼
COPY backend/ ./backend/

# model 會在 runtime 透過 volume 提供或首次啟動時下載
# HF_HOME 統一指向 /models，讓 volume mount 生效
ENV HF_HOME=/models
ENV TRANSFORMERS_CACHE=/models
ENV SENTENCE_TRANSFORMERS_HOME=/models

# model 路徑由 docker-compose environment 注入
ENV BGE_EMBED_MODEL_PATH=/models/hub/models--BAAI--bge-m3
ENV BGE_RERANKER_MODEL_PATH=/models/hub/models--BAAI--bge-reranker-v2-m3

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8000

CMD ["/docker-entrypoint.sh"]
