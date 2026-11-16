FROM python:3.12-slim
WORKDIR /seed
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY demo/index-image/seed_index.py demo/index-image/manifest.json ./
COPY data/indexes/bm25.sqlite /payload/bm25.sqlite
# Validate the baked-in bytes during build; refuse an accidental different index.
RUN python seed_index.py --verify-source
ENTRYPOINT ["python", "/seed/seed_index.py"]
CMD ["--destination", "/data"]
