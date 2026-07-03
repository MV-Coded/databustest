FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей (curl для healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Установка Python-зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения
COPY app/ ./app/

EXPOSE 8000

# Запуск без reload — в продакшене так и должно быть
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]