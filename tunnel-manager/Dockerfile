FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* && \
    mkdir -p /app && \
    touch /app/dns-manager.log && \
    chmod 666 /app/dns-manager.log

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv

COPY requirements.txt requirements.lock ./
RUN uv pip install --system --no-cache -r requirements.lock

COPY . .

CMD ["python", "main.py"]
