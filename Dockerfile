FROM denoland/deno:bin AS deno

FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libmediainfo0v5 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV KEO_MUSIC_DIR=/music \
    KEO_DATA_DIR=/data \
    KEO_CONFIG=/config/keo.toml \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "--factory", "keo.main:build_app", "--host", "0.0.0.0", "--port", "8000"]
