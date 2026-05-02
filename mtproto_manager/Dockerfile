FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PYTHONUNBUFFERED=1
ENV MTPROTO_CONFIG=/opt/mtproto-proxy/config.toml
ENV MTPROTO_STATE=/data/secrets_state.json
ENV MTPROTO_PID_FILE=/run/mtproto-proxy.pid

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
