# backend/Dockerfile — используем готовый Python с fastapi

FROM tiangolo/uvicorn-gunicorn-fastapi:python3.11-slim

WORKDIR /app

# Доп. зависимости из requirements.txt (только специфичные)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || true

COPY main.py .
COPY config.ini .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
