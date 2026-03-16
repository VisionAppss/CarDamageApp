# backend/Dockerfile — добавь ПЕРВЫМИ строками
FROM python:3.11-alpine

RUN echo "https://mirror.yandex.ru/mirrors/alpine/v3.19/main" > /etc/apk/repositories && \
     echo "https://mirror.yandex.ru/mirrors/alpine/v3.19/community" >> /etc/apk/repositories

# Теперь установка будет в 5-10 раз быстрее
RUN apk add --no-cache gcc musl-dev

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY main.py .
COPY config.ini .

# Папка для загрузок
RUN mkdir -p /app/uploads

# Порт API
EXPOSE 8000

# Запуск
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
