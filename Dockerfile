FROM python:3.11-slim-bookworm as builder

# Install build deps
RUN apt-get update &amp;&amp; apt-get install -y --no-install-recommends \
    gcc g++ &amp;&amp; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip &amp;&amp; \
    pip install --no-cache-dir --only-binary=:all: -r requirements.txt

FROM python:3.11-slim-bookworm

WORKDIR /app

# Copy installed deps from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY app.py .
COPY .env.example .

# No build deps needed
RUN apt-get update &amp;&amp; apt-get install -y --no-install-recommends curl &amp;&amp; rm -rf /var/lib/apt/lists/*

# Non-root
RUN useradd --create-home appuser &amp;&amp; chown -R appuser:appuser /app
USER appuser

EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=3s CMD curl -f http://localhost:10000/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]

