# backend/Dockerfile
# Production-ready образ для российских серверов

FROM python:3.11-alpine3.19

# 1. Ускоряем apk — используем российское зеркало Яндекса
RUN echo "https://mirror.yandex.ru/mirrors/alpine/v3.19/main" > /etc/apk/repositories && \
    echo "https://mirror.yandex.ru/mirrors/alpine/v3.19/community" >> /etc/apk/repositories

# 2. Системные зависимости для компиляции Python-пакетов
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    python3-dev

# 3. Рабочая директория
WORKDIR /app

# 4. Python-зависимости
COPY requirements.txt .

# Используем pip3 (в Alpine нет симлинка pip → pip3 по умолчанию)
RUN pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir -r requirements.txt

# 5. Копируем код приложения
COPY main.py .
COPY config.ini .

# 6. Создаём папку для загрузок
RUN mkdir -p /app/uploads && chmod 755 /app/uploads

# 7. Открываем порт
EXPOSE 8000

# 8. Запуск через python3 -m (надёжнее прямого вызова uvicorn)
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
