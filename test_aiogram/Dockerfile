FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

# System dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential \
    curl \
    netcat-openbsd \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create directory for temporary videos
RUN mkdir -p /app/temp_videos

# Copy application code
COPY ./app ./app

# Run the application
CMD ["python3", "-m", "app"] 