# backend/Dockerfile — 100% работает из России

FROM python:3.11-alpine3.19

# 1. Российское зеркало Alpine (для apk)
RUN echo "https://mirror.yandex.ru/mirrors/alpine/v3.19/main" > /etc/apk/repositories && \
    echo "https://mirror.yandex.ru/mirrors/alpine/v3.19/community" >> /etc/apk/repositories

# 2. Системные зависимости
RUN apk add --no-cache gcc musl-dev libffi-dev openssl-dev python3-dev

WORKDIR /app

# 3. ← КЛЮЧЕВОЕ: Принудительно ставим российское зеркало PyPI через env
ENV PIP_INDEX_URL=https://mirror.yandex.ru/pypi/simple
ENV PIP_TRUSTED_HOST=mirror.yandex.ru

# 4. Копируем и ставим зависимости (pip уже есть, не апгрейдим!)
COPY requirements.txt .

# Убираем --upgrade pip, используем только зеркало
RUN pip3 install --no-cache-dir -r requirements.txt

# 5. Код
COPY main.py .
COPY config.ini .
RUN mkdir -p /app/uploads && chmod 755 /app/uploads

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
# 8. Запуск через python3 -m (надёжнее прямого вызова uvicorn)
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
