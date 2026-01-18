FROM python:3.12-slim

# Install base tools (cached separately)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install FFmpeg (expensive - cached in separate layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install mkvtoolnix (for mkvextract) in its own layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    mkvtoolnix \
    && rm -rf /var/lib/apt/lists/*

# Install WireGuard for VPN support (separate layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wireguard-tools \
    iproute2 \
    iptables \
    && rm -rf /var/lib/apt/lists/*

# Install Tesseract OCR with language packs (separate layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-deu \
    tesseract-ocr-fra \
    tesseract-ocr-spa \
    tesseract-ocr-ita \
    && rm -rf /var/lib/apt/lists/*

 ARG TESSDATA_DOWNLOAD=0
 ARG TESSDATA_LANGS=
 ARG TESSDATA_REPO_RAW_BASE=https://raw.githubusercontent.com/tesseract-ocr/tessdata/main

 RUN set -eux; \
     if [ "${TESSDATA_DOWNLOAD}" = "1" ] || [ "${TESSDATA_DOWNLOAD}" = "true" ]; then \
         if [ -d "/usr/share/tesseract-ocr/5/tessdata" ]; then \
             TESSDATA_DIR="/usr/share/tesseract-ocr/5/tessdata"; \
         elif [ -d "/usr/share/tesseract-ocr/4.00/tessdata" ]; then \
             TESSDATA_DIR="/usr/share/tesseract-ocr/4.00/tessdata"; \
         elif [ -d "/usr/share/tesseract-ocr/tessdata" ]; then \
             TESSDATA_DIR="/usr/share/tesseract-ocr/tessdata"; \
         else \
             TESSDATA_DIR="/usr/share/tesseract-ocr/tessdata"; \
         fi; \
         mkdir -p "${TESSDATA_DIR}"; \
         if [ "${TESSDATA_LANGS}" = "all" ]; then \
             curl -fsSL "https://github.com/tesseract-ocr/tessdata/archive/refs/heads/main.tar.gz" \
               | tar -xz --strip-components=2 -C "${TESSDATA_DIR}" "tessdata-main"; \
         else \
             for lang in ${TESSDATA_LANGS}; do \
                 curl -fsSL -o "${TESSDATA_DIR}/${lang}.traineddata" "${TESSDATA_REPO_RAW_BASE}/${lang}.traineddata"; \
             done; \
         fi; \
     fi

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY src/ ./src/
COPY templates/ ./templates/
COPY static/ ./static/

# Create directory for settings persistence
RUN mkdir -p /app/data

# Expose Flask port
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=src/app.py
ENV PYTHONUNBUFFERED=1
ENV DEFAULT_MEDIA_DIR=/media

# Run Flask app
CMD ["python", "-m", "flask", "run", "--host=0.0.0.0", "--port=5000"]
