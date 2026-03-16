# backend/Dockerfile — debug-версия с проверками

FROM python:3.11-alpine3.19

# 1. Проверяем что Alpine-зеркало работает
RUN echo "https://mirror.yandex.ru/mirrors/alpine/v3.19/main" > /etc/apk/repositories && \
    echo "https://mirror.yandex.ru/mirrors/alpine/v3.19/community" >> /etc/apk/repositories && \
    echo "=== Проверка Alpine mirror ===" && \
    apk update && \
    echo "=== Alpine OK ==="

# 2. Ставим системные зависимости
RUN apk add --no-cache gcc musl-dev libffi-dev openssl-dev python3-dev && \
    echo "=== Системные пакеты OK ==="

WORKDIR /app

# 3. Проверяем что файлы на месте
COPY requirements.txt .
RUN echo "=== requirements.txt скопирован ===" && \
    ls -la requirements.txt && \
    cat requirements.txt

# 4. PyPI зеркало с запасным вариантом
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ENV PIP_TIMEOUT=60
ENV PIP_RETRIES=5

# 5. Проверяем pip и ставим зависимости с подробным выводом
RUN echo "=== Версия pip ===" && \
    pip3 --version && \
    echo "=== Проверка зеркала ===" && \
    pip3 config list && \
    echo "=== Установка зависимостей ===" && \
    pip3 install -v --no-cache-dir -r requirements.txt || \
    (echo "=== ОШИБКА: пробуем резервное зеркало ===" && \
     pip3 install -v --no-cache-dir --index-url https://mirror.yandex.ru/pypi/simple -r requirements.txt)

# 6. Код
COPY main.py .
COPY config.ini .
RUN mkdir -p /app/uploads && chmod 755 /app/uploads

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
