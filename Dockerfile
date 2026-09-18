FROM python:3.11-slim

RUN useradd -m -u 1000 user
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY central_backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY --chown=user:user central_backend /app/central_backend

USER user
WORKDIR /app/central_backend
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
