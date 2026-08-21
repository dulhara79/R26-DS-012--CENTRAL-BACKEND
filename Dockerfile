FROM python:3.11-slim

# Hugging Face Spaces run containers as UID 1000. Running as root works on
# Render but causes permission errors on HF, so build for the stricter host.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR $HOME/app

COPY --chown=user requirements.txt .
RUN pip install --user -r requirements.txt

COPY --chown=user . .

# HF routes to app_port from README frontmatter (8000).
# Render injects PORT. One line satisfies both.
EXPOSE 8000
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]