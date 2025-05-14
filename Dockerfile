FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем ffmpeg и другие необходимые системные зависимости
RUN apt-get update && apt-get install -y \
    ffmpeg \
    python3-dev \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements.txt и устанавливаем зависимости
COPY test_code/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY test_code/ /app/

# Создаем директорию для временных файлов
RUN mkdir -p /app/temp_videos && \
    chmod 777 /app/temp_videos

# Устанавливаем переменные окружения
ENV PYTHONUNBUFFERED=1

# Если .env.template существует, копируем его как .env
RUN if [ -f /app/.env.template ]; then \
    cp /app/.env.template /app/.env; \
    fi

# Запускаем телеграм-бота
CMD ["python", "telethon_bot.py"] 