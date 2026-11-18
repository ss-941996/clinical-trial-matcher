FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /seed
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG INDEX_PATH=data/indexes/bm25.sqlite
ARG INDEX_MANIFEST_PATH=demo/index-image/manifest.json
COPY demo/index-image/seed_index.py ./
COPY ${INDEX_MANIFEST_PATH} /seed/manifest.json
COPY ${INDEX_PATH} /payload/bm25.sqlite
# Validate the baked-in bytes during build; refuse an accidental different index.
RUN python seed_index.py --verify-source
ENTRYPOINT ["python", "/seed/seed_index.py"]
CMD ["--destination", "/data"]
