FROM python:3.11-alpine

WORKDIR /app

# Правильное зеркало Яндекса (проверено!)
RUN sed -i 's|dl-cdn.alpinelinux.org|mirror.yandex.ru/mirrors/alpine|g' /etc/apk/repositories && \
    apk update && \
    apk add --no-cache gcc musl-dev python3-dev libffi-dev openssl-dev

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY config.ini .
RUN mkdir -p /app/uploads

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
