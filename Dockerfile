# Node.js is only used for `node --check` on generated test scripts (syntax check). Copied from the official image.
FROM node:22-slim AS node

FROM python:3.14-slim
COPY --from=node /usr/local/bin/node /usr/local/bin/node
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
# The runner's source is only here so the server can offer it as a download (the runner itself never runs in this container).
COPY runner ./runner
COPY system-prompts ./system-prompts
RUN useradd --create-home --uid 10001 testmax && chown -R testmax /srv
USER testmax
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
