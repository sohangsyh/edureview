FROM python:3.12-slim

# Hugging Face Spaces 以 uid 1000 執行容器，/app 及模型快取都要屬於這個使用者
RUN useradd -m -u 1000 user && mkdir -p /app && chown user:user /app
ENV HOME=/home/user HF_HOME=/home/user/.cache/huggingface PYTHONUNBUFFERED=1
WORKDIR /app

# CPU 版 PyTorch：預設版本會連同數 GB 的 CUDA 套件一併下載
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

USER user
# 把預設 embedding 模型放進映像（bge-m3 約 2.3 GB），啟動時不用再下載
ARG EMBEDDING_MODEL=BAAI/bge-m3
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')"

COPY --chown=user:user . .
# data/ 存放資料庫、課程資料及向量索引；需要保留審查記錄時，應掛載為持久磁碟
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
