FROM python:3.11

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install --index-url https://download.pytorch.org/whl/cpu torch==2.3.1

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefer-binary -r requirements.txt

COPY common ./common
COPY templates ./templates
COPY services ./services

CMD ["uvicorn", "services.google_data.main:app", "--host", "0.0.0.0", "--port", "8000"]
