FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
WORKDIR /app
COPY requirements-retrieval.txt ./
RUN pip install --no-cache-dir -r requirements-retrieval.txt && useradd --uid 10001 --create-home matcher
COPY --chown=10001:10001 app ./app
COPY serve.py ./
USER 10001:10001
ENV TRIAL_INDEX_PATH=/data/bm25.sqlite HOST=0.0.0.0 PORT=8000 MAX_SEARCHES_PER_MINUTE=10
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).read()"]
CMD ["python", "serve.py"]
