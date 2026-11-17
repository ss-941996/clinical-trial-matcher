FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=4
WORKDIR /app
COPY requirements-demo.txt ./
RUN pip install --no-cache-dir -r requirements-demo.txt
COPY download_model.py ./
COPY app/rerank.py app/__init__.py ./app/
RUN python download_model.py --output /app/models \
    && useradd --uid 10001 --create-home matcher \
    && chmod -R a+rX /app
# Keep downloaded model layers cached when application code changes.
COPY --chown=10001:10001 app ./app
COPY serve.py ./
USER 10001:10001
ENV TRIAL_INDEX_PATH=/data/bm25.sqlite RERANK_MODEL_DIR=/app/models HOST=0.0.0.0 PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).read()"]
CMD ["python", "serve.py"]
