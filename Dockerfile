FROM python:3.12-slim

WORKDIR /app

# Install build dependencies (gcc for C-extension wheels; no libpq needed — using SQLite)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure the persistent volume mount point exists in the image.
# On Railway this directory is replaced by the mounted volume at runtime.
RUN mkdir -p /data

# Railway injects $PORT at runtime; the default and EXPOSE value is 8080.
# EXPOSE tells Railway's edge router (hikari) which port to forward external traffic to.
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
