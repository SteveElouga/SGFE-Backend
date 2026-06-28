FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 5 -r requirements.txt

COPY manage.py .
COPY gateway/ gateway/
COPY schema/ schema/
COPY proto/ proto/

RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "gateway.asgi:application"]
