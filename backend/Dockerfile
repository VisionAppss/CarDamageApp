# backend/Dockerfile
# Лёгкий образ Python 3.11 на Alpine Linux

FROM python:3.11-alpine

# Рабочая директория
WORKDIR /app

# Системные зависимости
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