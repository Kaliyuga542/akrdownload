FROM python:3.11-slim

# =========================================================
# SYSTEM DEPENDENCIES
# =========================================================

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# =========================================================
# OPTIONAL: N_m3u8DL-RE (future DRM/AES support)
# Uncomment to install. Binary name may change per release,
# check: https://github.com/nilaoda/N_m3u8DL-RE/releases
# =========================================================

# RUN curl -L -o /tmp/re.tar.gz \
#     "https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v20241203/N_m3u8DL-RE_Beta_linux-x64_20241203.tar.gz" \
#     && tar -xzf /tmp/re.tar.gz -C /usr/local/bin N_m3u8DL-RE \
#     && chmod +x /usr/local/bin/N_m3u8DL-RE \
#     && rm /tmp/re.tar.gz

# =========================================================
# PYTHON DEPENDENCIES
# =========================================================

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# =========================================================
# APP
# =========================================================

COPY . .

# Koyeb injects PORT automatically - main.py reads it
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
